"""#323 leaf 2: VieNeu dubbing on an NVIDIA GPU through a verified ONNX Runtime CUDA pack."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

from engine.dubflow.accelerator import nvidia
from engine.dubflow.models.runtime import ModelBootstrapError
from engine.dubflow.tts.adapter import TtsBackendError
from engine.dubflow.tts.vieneu import GPU_RUNTIME_ID, RUNTIME_ID, _DeviceProcess

REPO = Path(__file__).resolve().parents[2]
GIB = 1024 ** 3


def gpu(**overrides):
    values = {"name": "NVIDIA GeForce RTX 4070", "total_memory": 12 * GIB, "compute_capability": (8, 9), "driver_version": 13000}
    return nvidia.NvidiaDevice(**{**values, **overrides})


class OnnxPackTests(unittest.TestCase):
    def test_shipped_manifest_pins_the_cuda13_runtime_and_onnxruntime_package(self):
        pack = nvidia.load_pack(REPO, nvidia.ONNX_PROFILE_PATH)
        self.assertEqual((pack.driver_version, pack.min_compute_capability, pack.min_total_memory), (13000, (7, 5), 4 * GIB))
        names = [item.name for item in pack.libraries]
        self.assertEqual(names[:3], ["cudart64_13.dll", "cublasLt64_13.dll", "cublas64_13.dll"])
        self.assertEqual(names[-1], "cudnn64_9.dll")  # The dispatcher after every sub-library it loads.
        self.assertLess(names.index("cudnn_graph64_9.dll"), names.index("cudnn_ops64_9.dll"))
        site = {item.name for item in pack.site}
        self.assertTrue({"onnxruntime/__init__.py", "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
                         "onnxruntime/capi/onnxruntime_providers_cuda.dll", "onnxruntime/capi/onnxruntime.dll"} <= site)
        self.assertNotIn("onnxruntime/capi/onnxruntime_providers_tensorrt.dll", site)
        for artifact in pack.artifacts:
            self.assertTrue(artifact.url.startswith("https://files.pythonhosted.org/packages/"))
            self.assertTrue(artifact.relative_path.startswith(pack.pack_dir + "/wheels/"))
        self.assertEqual(len(pack.notices), 5)
        self.assertNotEqual(pack.pack_dir, nvidia.load_pack(REPO).pack_dir)

    def test_runtime_package_files_extract_into_their_own_folders(self):
        members = {"onnxruntime/__init__.py": b"__version__ = '1.30.0'\n", "onnxruntime/capi/core.pyd": b"binary",
                   "nvidia/bin/lib64_1.dll": b"library", "onnxruntime/capi/skip.dll": b"not pinned"}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "build.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for member, data in members.items():
                    archive.writestr(member, data)
            def entry(name, member):
                return {"name": name, "artifact": "w", "member": member, "sha256": hashlib.sha256(members[member]).hexdigest(),
                        "size_bytes": len(members[member])}
            document = {"schema_version": 1, "profile_id": "fixture-onnx", "pack_dir": "accelerators/fixture-onnx",
                        "requires": {"cuda_driver_version": 13000, "min_compute_capability": [7, 5], "min_total_memory_bytes": 4 * GIB},
                        "artifacts": [{"id": "w", "path": "accelerators/fixture-onnx/wheels/w.whl", "url": "https://files.pythonhosted.org/w.whl",
                                       "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "size_bytes": wheel.stat().st_size}],
                        "libraries": [entry("lib64_1.dll", "nvidia/bin/lib64_1.dll")],
                        "site": [entry("onnxruntime/__init__.py", "onnxruntime/__init__.py"),
                                 entry("onnxruntime/capi/core.pyd", "onnxruntime/capi/core.pyd")]}
            manifest = root / "app" / nvidia.ONNX_PROFILE_PATH
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps(document), encoding="utf-8")
            pack = nvidia.load_pack(root / "app", nvidia.ONNX_PROFILE_PATH)
            target = root / "models/accelerators/fixture-onnx/wheels/w.whl"
            target.parent.mkdir(parents=True)
            target.write_bytes(wheel.read_bytes())
            bin_dir = nvidia.ensure_pack(pack, root / "models", verify=True)
            site = bin_dir.parent / "site"
            self.assertEqual((site / "onnxruntime/capi/core.pyd").read_bytes(), b"binary")
            self.assertEqual((bin_dir / "lib64_1.dll").read_bytes(), b"library")
            self.assertFalse((site / "onnxruntime/capi/skip.dll").exists())
            self.assertFalse(target.exists())
            for unsafe in ("onnxruntime/../escape.py", "other/x.py", "/onnxruntime/x.py", "onnxruntime\\capi\\x.py", "onnxruntime/a/b/c/d/e/f.py"):
                with self.subTest(unsafe=unsafe):
                    manifest.write_text(json.dumps({**document, "site": [{**document["site"][0], "name": unsafe}]}), encoding="utf-8")
                    with self.assertRaises(ModelBootstrapError):
                        nvidia.load_pack(root / "app", nvidia.ONNX_PROFILE_PATH)


class TtsSelectionTests(unittest.TestCase):
    def select(self, *, preference="auto", device=None, probe_error=None, ensure_error=None):
        calls = []
        def probe():
            calls.append("probe")
            if probe_error:
                raise probe_error
            return device or gpu()
        def ensure(pack, model_root, progress=None, verify=False):
            calls.append("ensure-verify" if verify else "ensure")
            if ensure_error:
                raise ensure_error
            return Path(model_root).absolute() / pack.pack_dir / "bin"
        return nvidia.select_tts_device(REPO, Path("models"), preference, probe=probe, ensure=ensure), calls

    def test_supported_gpu_gets_a_verified_runtime_for_the_native_process(self):
        selected, calls = self.select()
        self.assertEqual((selected.device, selected.reason, selected.label), ("cuda", None, "GPU NVIDIA GeForce RTX 4070"))
        self.assertEqual(calls, ["probe", "ensure-verify"])  # Hashed before use; nothing is loaded in the worker.
        manifest = REPO / nvidia.ONNX_PROFILE_PATH
        self.assertEqual(selected.runtime, {"manifest": str(manifest), "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                                            "root": str(Path("models").absolute() / "accelerators/nvidia-onnx-cuda13-v1")})

    def test_cpu_preference_and_no_driver_never_download(self):
        self.assertEqual(self.select(preference="cpu"), (nvidia.TtsDevice("cpu"), []))
        selected, calls = self.select(probe_error=nvidia.AcceleratorUnavailable("no NVIDIA driver is installed", silent=True))
        self.assertEqual((selected.device, selected.reason, calls), ("cpu", None, ["probe"]))

    def test_gpus_the_cuda13_runtime_cannot_use_keep_dubbing_on_the_cpu(self):
        cases = {"CUDA 12.8; 13.0 or newer": gpu(driver_version=12080), "compute capability 6.1 is below 7.5": gpu(compute_capability=(6, 1)),
                 "2048 MB of GPU memory is below 4096 MB": gpu(total_memory=2 * GIB)}
        for reason, device in cases.items():
            with self.subTest(reason):
                selected, calls = self.select(device=device)
                self.assertEqual((selected.device, calls), ("cpu", ["probe"]))
                self.assertIn(reason, selected.reason)
        selected, _ = self.select(ensure_error=ModelBootstrapError("MODEL_DOWNLOAD_FAILED", "offline", retryable=True))
        self.assertEqual((selected.device, selected.reason), ("cpu", "GPU libraries unavailable: MODEL_DOWNLOAD_FAILED: offline"))


class FakeChild:
    def __init__(self, device, failures=()):
        self.device, self.failures, self.closed, self.calls = device, list(failures), False, []

    def generate(self, text, sid, speed):
        self.calls.append(text)
        if self.failures:
            raise self.failures.pop(0)
        return (self.device, text)

    def close(self):
        self.closed = True


class DeviceProcessTests(unittest.TestCase):
    def process(self, children):
        started = []
        def start(accelerator):
            child = children.pop(0)
            started.append((accelerator, child))
            if isinstance(child, Exception):
                raise child
            return child
        return started, start

    def test_gpu_child_serves_every_request_while_healthy(self):
        gpu_child = FakeChild("cuda")
        started, start = self.process([gpu_child])
        device = _DeviceProcess(start, {"root": "pack"})
        self.assertEqual((device.device, device.generate("a", 0, 1.0), device.warnings), ("cuda", ("cuda", "a"), []))
        self.assertEqual([accelerator for accelerator, _ in started], [{"root": "pack"}])

    def test_gpu_that_cannot_start_or_did_not_load_falls_back_to_a_cpu_child(self):
        for first in (TtsBackendError("TTS_NATIVE_INFERENCE_FAILED", "cudaErrorInsufficientDriver"), FakeChild("cpu")):
            with self.subTest(first=type(first).__name__):
                started, start = self.process([first, FakeChild("cpu")])
                device = _DeviceProcess(start, {"root": "pack"})
                self.assertEqual(device.device, "cpu")
                self.assertEqual([accelerator for accelerator, _ in started], [{"root": "pack"}, None])
                self.assertTrue(device.warnings[0].startswith("ACCELERATOR_FALLBACK: dubbing continued on the CPU"))
                if isinstance(first, FakeChild):
                    self.assertTrue(first.closed)

    def test_gpu_failure_mid_job_retries_that_request_on_the_cpu_once(self):
        gpu_child = FakeChild("cuda", [TtsBackendError("TTS_NATIVE_INFERENCE_FAILED", "CUDA out of memory")])
        cpu_child = FakeChild("cpu")
        _started, start = self.process([gpu_child, cpu_child])
        device = _DeviceProcess(start, {"root": "pack"})
        self.assertEqual(device.generate("b", 0, 1.0), ("cpu", "b"))
        self.assertEqual(device.generate("c", 0, 1.0), ("cpu", "c"))
        self.assertTrue(gpu_child.closed)
        self.assertIn("CUDA out of memory", device.warnings[0])

    def test_content_refusals_and_cpu_failures_are_not_device_fallbacks(self):
        refusal = TtsBackendError("TTS_SPEECH_INCOMPLETE", "no end of speech")
        _started, start = self.process([FakeChild("cuda", [refusal])])
        device = _DeviceProcess(start, {"root": "pack"})
        with self.assertRaises(TtsBackendError) as raised:
            device.generate("d", 0, 1.0)
        self.assertEqual((raised.exception.code, device.device, device.warnings), ("TTS_SPEECH_INCOMPLETE", "cuda", []))
        _started, start = self.process([FakeChild("cpu", [TtsBackendError("TTS_NATIVE_EXITED", "crash")])])
        device = _DeviceProcess(start, None)
        with self.assertRaises(TtsBackendError):
            device.generate("e", 0, 1.0)

    def test_gpu_speech_has_its_own_runtime_identity(self):
        self.assertTrue(GPU_RUNTIME_ID.startswith(RUNTIME_ID + "+onnxruntime-gpu-1.30.0-cuda13"))


@unittest.skipUnless(os.name == "nt", "the native GPU loader is Windows-only")
class NativeActivationTests(unittest.TestCase):
    """The VieNeu child imports ONNX Runtime from the verified pack and puts sessions on CUDA."""

    FAKE_ORT = (b"__version__ = '1.30.0'\n"
                b"def get_available_providers():\n    return ['CUDAExecutionProvider', 'CPUExecutionProvider']\n"
                b"class InferenceSession:\n"
                b"    def __init__(self, path, options=None, providers=None, **kwargs):\n        self.providers = providers\n"
                b"    def get_providers(self):\n        return [item[0] if isinstance(item, tuple) else item for item in self.providers]\n")
    SCRIPT = ("import importlib.util, json, sys\n"
              "spec = importlib.util.spec_from_file_location('vieneu_native', sys.argv[1])\n"
              "native = importlib.util.module_from_spec(spec); spec.loader.exec_module(native)\n"
              "request = json.loads(sys.argv[2])\n"
              "if request.pop('preload', False): import onnxruntime\n"
              "native._activate_cuda(request)\n"
              "import onnxruntime\n"
              "session = onnxruntime.InferenceSession('model.onnx', None, providers=['CPUExecutionProvider'])\n"
              "print(json.dumps([onnxruntime.__file__, session.get_providers()]))\n")

    def run_child(self, root: Path, spec: dict, *, preload=False, path_entry=None):
        env = dict(os.environ)
        if path_entry is not None:
            env["PYTHONPATH"] = str(path_entry)
        return subprocess.run([sys.executable, "-B", "-c", self.SCRIPT, str(REPO / "engine/dubflow/tts/vieneu_native.py"),
                               json.dumps({**spec, "preload": preload})], capture_output=True, text=True, timeout=60, env=env, cwd=root)

    def pack(self, root: Path) -> dict:
        site = root / "pack/site/onnxruntime"
        (site / "capi").mkdir(parents=True)
        (site / "__init__.py").write_bytes(self.FAKE_ORT)
        (root / "pack/bin").mkdir()
        document = {"libraries": [], "site": [{"name": "onnxruntime/__init__.py", "sha256": hashlib.sha256(self.FAKE_ORT).hexdigest(),
                                               "size_bytes": len(self.FAKE_ORT)}]}
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        return {"manifest": str(manifest), "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "root": str(root / "pack")}

    def test_verified_runtime_is_imported_first_and_sessions_run_on_cuda(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.pack(root)
            result = self.run_child(root, spec)
            self.assertEqual(result.returncode, 0, result.stderr)
            path, providers = json.loads(result.stdout)
            self.assertEqual(Path(path).resolve(), (root / "pack/site/onnxruntime/__init__.py").resolve())
            self.assertEqual(providers, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    def test_changed_runtime_manifest_or_an_earlier_import_is_refused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.pack(root)
            other = root / "other"
            (other / "onnxruntime").mkdir(parents=True)
            (other / "onnxruntime/__init__.py").write_bytes(self.FAKE_ORT)
            init = root / "pack/site/onnxruntime/__init__.py"
            cases = {
                "no longer matches its pinned hash": (lambda: init.write_bytes(self.FAKE_ORT.replace(b"1.30.0", b"1.30.9")), {}),
                "differs from the copy the worker verified": (lambda: None, {"manifest_sha256": "0" * 64}),
                "must be the first ONNX Runtime": (lambda: None, {"preload": True}),
            }
            for reason, (change, override) in cases.items():
                with self.subTest(reason):
                    init.write_bytes(self.FAKE_ORT)
                    change()
                    preload = override.pop("preload", False)
                    result = self.run_child(root, {**spec, **override}, preload=preload, path_entry=other)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reason, result.stderr)


if __name__ == "__main__":
    unittest.main()
