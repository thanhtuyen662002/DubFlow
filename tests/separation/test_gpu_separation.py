"""#323: Spleeter on an NVIDIA GPU in its own process, with the CPU as the fallback."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.separation import spleeter as sep
from engine.dubflow.separation.production import SeparationError

REPO = Path(__file__).resolve().parents[2]
FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")


class FakeChild:
    """A separation child that prints the given JSON lines and exits."""

    def __init__(self, lines, code=0, stderr=b""):
        self.stdin = BytesIO()
        self.stdin.close = lambda: None
        self.stdout = BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines))
        self.code, self.stderr = code, stderr
        self.requests = []

    def poll(self):
        return self.code

    def wait(self):
        return self.code

    def kill(self):
        pass


class GpuRunnerTests(unittest.TestCase):
    def run_child(self, child, **options):
        progress = []
        def popen(command, **kwargs):
            child.command = command
            kwargs["stderr"].write(child.stderr)
            return child
        class Job:  # Windows Job containment is the real child's; the fake needs none.
            def __init__(self, process): pass
            def close(self): pass
        with patch.object(sep.subprocess, "Popen", side_effect=popen), patch("engine.dubflow.tts.windows_job.WindowsJob", Job):
            metrics = sep.separate_background_on_gpu(Path("source.wav"), Path("background.wav"), model_dir=Path("models"),
                ffmpeg_path=Path("ffmpeg.exe"), accelerator={"root": "pack"}, app_root=Path("app"), progress=progress.append, **options)
        return metrics, progress, child

    def test_cuda_result_and_progress_come_back_from_the_isolated_child(self):
        metrics, progress, child = self.run_child(FakeChild([{"progress": 0.5}, {"progress": 1.0},
                                                              {"ok": True, "metrics": {"device": "cuda", "seconds": 1.0}}]))
        self.assertEqual((metrics["device"], progress), ("cuda", [0.5, 1.0]))
        self.assertEqual(child.command[1:3], ["-I", "-B"])
        self.assertTrue(child.command[3].endswith("spleeter_native.py"))
        request = json.loads(child.stdin.getvalue())
        self.assertEqual((request["accelerator"], request["app_root"]), ({"root": "pack"}, "app"))

    def test_every_failure_is_typed_for_the_cpu_fallback(self):
        cases = {
            "SEPARATION_GPU_UNAVAILABLE": FakeChild([{"ok": False, "code": "SEPARATION_GPU_UNAVAILABLE", "condition": "no CUDA"}], code=2),
            "SEPARATION_REJECTED": FakeChild([{"ok": False, "code": "SEPARATION_REJECTED", "condition": "clips"}], code=1),
            "SEPARATION_GPU_FAILED": FakeChild([], code=3221225477, stderr=b"access violation in cudnn"),
        }
        for code, child in cases.items():
            with self.subTest(code), self.assertRaises(SeparationError) as raised:
                self.run_child(child)
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(SeparationError) as raised:
            self.run_child(FakeChild([{"ok": True, "metrics": {"device": "cpu"}}]))
        self.assertIn("no CUDA result", raised.exception.condition)

    def test_child_reports_an_unusable_gpu_runtime_before_any_work(self):
        request = {"app_root": str(REPO), "accelerator": {"manifest": "missing.json", "manifest_sha256": "0" * 64, "root": "missing"},
                   "source": "s.wav", "background": "b.wav", "model_dir": "m", "ffmpeg": "f.exe"}
        result = subprocess.run([sys.executable, "-I", "-B", str(REPO / "engine/dubflow/separation/spleeter_native.py")],
                                input=json.dumps(request).encode() + b"\n", capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        reply = json.loads(result.stdout.decode().strip().splitlines()[-1])
        self.assertEqual((reply["ok"], reply["code"]), (False, "SEPARATION_GPU_UNAVAILABLE"))


@unittest.skipUnless(FFMPEG, "set DUBFLOW_TEST_FFMPEG for offline PCM conversion tests")
class DeviceIdentityTests(unittest.TestCase):
    def test_gpu_batches_never_share_cpu_checkpoints_and_metrics_name_the_device(self):
        import numpy as np
        with TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            models.mkdir()
            for stem in ("vocals", "accompaniment"):
                (models / f"spleeter-2stems-{stem}.onnx").write_bytes(b"fake model")
            source = root / "source.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes((np.full((16000, 2), 1000, "<i2")).tobytes())
            folders = {}
            def blocks(read, length, factory, *, checkpoint_dir=None, progress=None, **_):
                folders.setdefault(device, checkpoint_dir)
                yield np.zeros((2, length), np.float32) + 0.01
            for device in ("cpu", "cuda"):
                with patch.object(sep, "_separated_blocks", side_effect=blocks):
                    metrics = sep.separate_background(source, root / f"{device}.wav", model_dir=models,
                                                      ffmpeg_path=Path(FFMPEG), device=device)
                self.assertEqual(metrics["device"], device)
            self.assertNotEqual(folders["cpu"].name, folders["cuda"].name)


if __name__ == "__main__":
    unittest.main()
