import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from engine.dubflow.accelerator import nvidia
from engine.dubflow.models import ModelBootstrapError

REPO = Path(__file__).resolve().parents[2]
GIB = 1024 ** 3


class FakeDriver:
    """The CUDA driver API calls ``probe`` makes, writing through ctypes byref."""

    def __init__(self, version=12080, count=1, name=b"NVIDIA GeForce RTX 3060", memory=12 * GIB, cc=(8, 6), errors=None):
        self.values = {"version": version, "count": count, "name": name, "memory": memory, "cc": cc}
        self.errors = errors or {}

    def _result(self, name):
        return self.errors.get(name, 0)

    def cuInit(self, flags):
        return self._result("cuInit")

    def cuDriverGetVersion(self, ref):
        ref._obj.value = self.values["version"]
        return self._result("cuDriverGetVersion")

    def cuDeviceGetCount(self, ref):
        ref._obj.value = self.values["count"]
        return self._result("cuDeviceGetCount")

    def cuDeviceGet(self, ref, ordinal):
        ref._obj.value = ordinal
        return self._result("cuDeviceGet")

    def cuDeviceGetName(self, buffer, size, device):
        buffer.value = self.values["name"][:size - 1]  # the driver writes a terminated, bounded name
        return self._result("cuDeviceGetName")

    def cuDeviceTotalMem_v2(self, ref, device):
        ref._obj.value = self.values["memory"]
        return self._result("cuDeviceTotalMem_v2")

    def cuDeviceGetAttribute(self, ref, attribute, device):
        ref._obj.value = self.values["cc"][0 if attribute == 75 else 1]
        return self._result("cuDeviceGetAttribute")


def gpu(**overrides):
    values = {"name": "NVIDIA GeForce RTX 3060", "total_memory": 12 * GIB, "compute_capability": (8, 6), "driver_version": 12080}
    return nvidia.NvidiaDevice(**{**values, **overrides})


class Fixture:
    """A small pinned pack: one wheel holding two libraries and a notice."""

    MEMBERS = {"nvidia/x/bin/first64_1.dll": b"first library", "nvidia/x/bin/second64_1.dll": b"second library",
               "x.dist-info/License.txt": b"license text", "nvidia/x/bin/unpinned.dll": b"never extracted"}

    def __init__(self, root: Path, *, pinned_override=None):
        self.app_root, self.model_root = root / "app", root / "models"
        self.wheel_bytes = self._wheel()
        manifest = self.app_root / nvidia.PROFILE_PATH
        manifest.parent.mkdir(parents=True)
        def entry(name, member):
            data = (pinned_override or {}).get(member, self.MEMBERS[member])
            return {"name": name, "artifact": "x-wheel", "member": member, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        self.document = {
            "schema_version": 1, "profile_id": "fixture-pack", "pack_dir": "accelerators/fixture-v1",
            "requires": {"cuda_driver_version": 12000, "min_compute_capability": [6, 1], "min_total_memory_bytes": 2 * GIB},
            "artifacts": [{"id": "x-wheel", "path": "accelerators/fixture-v1/wheels/x.whl", "url": "https://files.pythonhosted.org/x.whl",
                           "sha256": hashlib.sha256(self.wheel_bytes).hexdigest(), "size_bytes": len(self.wheel_bytes)}],
            "libraries": [entry("first64_1.dll", "nvidia/x/bin/first64_1.dll"), entry("second64_1.dll", "nvidia/x/bin/second64_1.dll")],
            "notices": [entry("LICENSE-x.txt", "x.dist-info/License.txt")],
        }
        manifest.write_text(json.dumps(self.document), encoding="utf-8")
        self.pack = nvidia.load_pack(self.app_root)
        self.place_wheel()

    def _wheel(self) -> bytes:
        path = self.app_root.parent / "build.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for member, data in self.MEMBERS.items():
                archive.writestr(member, data)
        return path.read_bytes()

    @property
    def wheel(self) -> Path:
        return self.model_root / "accelerators/fixture-v1/wheels/x.whl"

    @property
    def bin_dir(self) -> Path:
        return self.model_root / "accelerators/fixture-v1/bin"

    def place_wheel(self, *_args, **_kwargs):
        self.wheel.parent.mkdir(parents=True, exist_ok=True)
        self.wheel.write_bytes(self.wheel_bytes)


class ProbeTests(unittest.TestCase):
    def test_reads_device_zero_through_the_driver_api(self):
        device = nvidia.probe(lambda: FakeDriver())
        self.assertEqual(device, gpu())
        self.assertEqual(device.label(), "NVIDIA GeForce RTX 3060 (12 GB)")

    def test_driver_errors_and_missing_devices_are_reported(self):
        with self.assertRaisesRegex(nvidia.AcceleratorUnavailable, "cuInit failed with CUDA error 100"):
            nvidia.probe(lambda: FakeDriver(errors={"cuInit": 100}))
        with self.assertRaisesRegex(nvidia.AcceleratorUnavailable, "no CUDA device"):
            nvidia.probe(lambda: FakeDriver(count=0))

    def test_device_name_is_bounded_and_normalized(self):
        device = nvidia.probe(lambda: FakeDriver(name=b"  NVIDIA\tRTX   " + b"A" * 300))
        self.assertTrue(device.name.startswith("NVIDIA RTX A"))
        self.assertLessEqual(len(device.name), 96)


class PackTests(unittest.TestCase):
    def test_shipped_manifest_pins_every_library_from_nvidia_wheels(self):
        pack = nvidia.load_pack(REPO)
        self.assertEqual([item.name for item in pack.libraries], [
            "cublasLt64_12.dll", "cublas64_12.dll", "cudnn_graph64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll",
            "cudnn_adv64_9.dll", "cudnn_heuristic64_9.dll", "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll"])
        self.assertEqual({artifact.artifact_id for artifact in pack.artifacts}, {"cublas-wheel", "cudnn-wheel"})
        for artifact in pack.artifacts:
            self.assertTrue(artifact.url.startswith("https://files.pythonhosted.org/packages/"))
            self.assertTrue(artifact.url.endswith("/" + artifact.relative_path.rsplit("/", 1)[1]))
        self.assertEqual((pack.driver_version, pack.min_compute_capability, pack.min_total_memory), (12000, (6, 1), 2 * GIB))
        self.assertEqual(len(pack.notices), 2)

    def test_extracts_only_pinned_members_then_drops_the_wheels(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            bin_dir = nvidia.ensure_pack(fixture.pack, fixture.model_root)
            self.assertEqual(bin_dir, fixture.bin_dir)
            self.assertEqual(sorted(path.name for path in bin_dir.iterdir()), ["first64_1.dll", "second64_1.dll"])
            self.assertEqual((bin_dir / "first64_1.dll").read_bytes(), b"first library")
            self.assertEqual((bin_dir.parent / "LICENSE-x.txt").read_bytes(), b"license text")
            self.assertFalse(fixture.wheel.exists())

    def test_verified_pack_is_reused_without_wheels_or_network(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            nvidia.ensure_pack(fixture.pack, fixture.model_root)
            with patch.object(nvidia, "ensure_model_profile", side_effect=AssertionError("no download expected")):
                self.assertEqual(nvidia.ensure_pack(fixture.pack, fixture.model_root), fixture.bin_dir)

    def test_tampered_library_is_restored_from_a_fresh_verified_wheel(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            nvidia.ensure_pack(fixture.pack, fixture.model_root)
            (fixture.bin_dir / "second64_1.dll").write_bytes(b"planted code")
            with patch.object(nvidia, "ensure_model_profile", side_effect=fixture.place_wheel) as download:
                nvidia.ensure_pack(fixture.pack, fixture.model_root)
            download.assert_called_once()
            self.assertEqual((fixture.bin_dir / "second64_1.dll").read_bytes(), b"second library")

    def test_member_that_differs_from_its_pin_is_never_published(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), pinned_override={"nvidia/x/bin/second64_1.dll": b"other bytes!!!"})
            with self.assertRaises(ModelBootstrapError) as raised:
                nvidia.ensure_pack(fixture.pack, fixture.model_root)
            self.assertEqual(raised.exception.code, "MODEL_HASH_MISMATCH")
            self.assertEqual(sorted(path.name for path in fixture.bin_dir.iterdir()), ["first64_1.dll"])

    def test_unsafe_or_ambiguous_manifests_are_rejected(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            manifest = fixture.app_root / nvidia.PROFILE_PATH
            library = fixture.document["libraries"][0]
            cases = {
                "escaping member": {"libraries": [{**library, "member": "../../evil.dll"}]},
                "not a library": {"libraries": [{**library, "name": "evil.exe"}]},
                "unknown artifact": {"libraries": [{**library, "artifact": "other"}]},
                "duplicate name": {"libraries": [library, {**library, "name": library["name"].upper()}]},
                "no libraries": {"libraries": []},
                "wheel outside pack": {"artifacts": [{**fixture.document["artifacts"][0], "path": "asr/x.whl"}]},
                "pack outside accelerators": {"pack_dir": "asr"},
                "missing requirements": {"requires": {}},
            }
            for label, change in cases.items():
                with self.subTest(label):
                    manifest.write_text(json.dumps({**fixture.document, **change}), encoding="utf-8")
                    with self.assertRaises(ModelBootstrapError):
                        nvidia.load_pack(fixture.app_root)

    def test_activation_refuses_a_library_that_changed_after_extraction(self):
        with TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            nvidia.ensure_pack(fixture.pack, fixture.model_root)
            (fixture.bin_dir / "first64_1.dll").write_bytes(b"replaced with something larger")
            with self.assertRaisesRegex(nvidia.AcceleratorUnavailable, "first64_1.dll no longer matches"):
                nvidia.activate(fixture.pack, fixture.bin_dir)
            (fixture.bin_dir / "first64_1.dll").unlink()
            with self.assertRaisesRegex(nvidia.AcceleratorUnavailable, "first64_1.dll no longer matches"):
                nvidia.activate(fixture.pack, fixture.bin_dir)


class SelectionTests(unittest.TestCase):
    def select(self, *, preference="auto", device=None, probe_error=None, ensure_error=None, activate_error=None, types=("float32", "int8_float16", "float16")):
        calls = []
        def probe():
            calls.append("probe")
            if probe_error:
                raise probe_error
            return device or gpu()
        def ensure(pack, model_root, progress=None):
            calls.append("ensure")
            if ensure_error:
                raise ensure_error
            return Path("bin")
        def activate(pack, bin_dir):
            calls.append("activate")
            if activate_error:
                raise activate_error
        return nvidia.select_asr_device(REPO, Path("models"), preference, probe=probe, ensure=ensure, activate=activate,
                                        compute_types=lambda: set(types)), calls

    def test_supported_gpu_uses_cuda_with_the_preferred_compute_type(self):
        selected, calls = self.select()
        self.assertEqual((selected.device, selected.compute_type, selected.reason), ("cuda", "int8_float16", None))
        self.assertEqual(selected.label, "GPU NVIDIA GeForce RTX 3060")
        self.assertEqual(nvidia.AsrDevice("cuda", "int8", gpu(name="Tesla T4")).label, "GPU NVIDIA Tesla T4")
        self.assertEqual(calls, ["probe", "ensure", "activate"])
        self.assertEqual(self.select(types=("float32", "float16"))[0].compute_type, "float16")

    def test_cpu_preference_never_touches_the_driver(self):
        selected, calls = self.select(preference="cpu")
        self.assertEqual((selected, calls), (nvidia.CPU, []))

    def test_machine_without_nvidia_driver_is_a_silent_cpu_choice(self):
        selected, calls = self.select(probe_error=nvidia.AcceleratorUnavailable("no NVIDIA driver is installed", silent=True))
        self.assertEqual((selected.device, selected.reason, calls), ("cpu", None, ["probe"]))

    def test_unsuitable_gpus_fall_back_with_a_reason_before_any_download(self):
        cases = {"CUDA 11.8; 12.0 or newer": gpu(driver_version=11080), "compute capability 5.2 is below 6.1": gpu(compute_capability=(5, 2)),
                 "1024 MB of GPU memory is below 2048 MB": gpu(total_memory=GIB)}
        for reason, device in cases.items():
            with self.subTest(reason):
                selected, calls = self.select(device=device)
                self.assertEqual((selected.device, calls), ("cpu", ["probe"]))
                self.assertIn(reason, selected.reason)

    def test_every_setup_failure_becomes_a_cpu_choice_with_its_reason(self):
        cases = {
            "probe": ({"probe_error": OSError("access violation")}, "NVIDIA probe failed: OSError: access violation"),
            "download": ({"ensure_error": ModelBootstrapError("MODEL_DOWNLOAD_FAILED", "offline", retryable=True)},
                         "GPU libraries unavailable: MODEL_DOWNLOAD_FAILED: offline"),
            "tampered": ({"activate_error": nvidia.AcceleratorUnavailable("x.dll no longer matches its pinned hash")}, "x.dll no longer matches"),
            "loader": ({"activate_error": OSError("[WinError 126]")}, "GPU setup failed: OSError: [WinError 126]"),
            "types": ({"types": ()}, "supports no compute type"),
        }
        for label, (arguments, reason) in cases.items():
            with self.subTest(label):
                selected, _ = self.select(**arguments)
                self.assertEqual(selected.device, "cpu")
                self.assertIn(reason, selected.reason)


if __name__ == "__main__":
    unittest.main()
