from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.models.hardware import HardwareResolver, NvidiaHardwareProbe


class HardwareProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory(prefix="dubflow-hardware-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.ffmpeg = root / "app runtime" / "ffmpeg.exe"
        self.driver = root / "driver" / "nvidia-smi.exe"

    def result(self, stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    def probe(self, runner: Mock) -> NvidiaHardwareProbe:
        return NvidiaHardwareProbe(self.ffmpeg, self.driver, timeout_seconds=2, runner=runner)

    def test_verified_encoder_uses_free_vram_and_explicit_device(self) -> None:
        runner = Mock(side_effect=[self.result('"Test, GPU", 6144\n'), self.result()])
        snapshot = self.probe(runner).detect()
        self.assertTrue(snapshot.gpu_available)
        self.assertEqual(snapshot.gpu_name, "Test, GPU")
        self.assertEqual(snapshot.vram_mb, 6144)
        self.assertEqual(snapshot.encoder, "h264_nvenc")
        self.assertEqual(snapshot.source, "nvidia-smi+ffmpeg-smoke")
        self.assertEqual(snapshot.warnings, ())
        inventory, encode = runner.call_args_list
        self.assertEqual(inventory.args[0][0], str(self.driver))
        self.assertIn("--id=0", inventory.args[0])
        self.assertIn("--query-gpu=name,memory.free", inventory.args[0])
        self.assertEqual(encode.args[0][0], str(self.ffmpeg))
        self.assertEqual(encode.args[0][encode.args[0].index("-gpu") + 1], "0")
        for call in runner.call_args_list:
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(call.kwargs["timeout"], 2)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertIn("creationflags", call.kwargs)

    def test_profile_requires_matching_verified_encoder(self) -> None:
        runner = Mock(side_effect=[self.result("GPU,8192"), self.result()])
        selected = HardwareResolver({}, probe=self.probe(runner)).resolve("gpu", encoder="h264_nvenc")
        self.assertEqual(selected.selected, "gpu")
        self.assertEqual(selected.encoder, "h264_nvenc")
        self.assertFalse(selected.fallback)
        runner.side_effect = [self.result("GPU,8192"), self.result()]
        incompatible = HardwareResolver({}, probe=self.probe(runner)).resolve("gpu", encoder="hevc_nvenc")
        self.assertEqual(incompatible.selected, "cpu")
        self.assertTrue(incompatible.fallback)

    def test_requested_cpu_and_software_never_initialize_gpu(self) -> None:
        runner = Mock(side_effect=AssertionError("GPU process must not run"))
        resolver = HardwareResolver({}, probe=self.probe(runner))
        self.assertEqual(resolver.resolve("cpu", encoder="h264_nvenc").selected, "cpu")
        self.assertEqual(resolver.resolve("auto", encoder="software").selected, "cpu")
        self.assertTrue(resolver.resolve("gpu", encoder="software").fallback)
        runner.assert_not_called()

    def test_low_free_vram_preserves_cpu_export(self) -> None:
        runner = Mock(side_effect=[self.result("GPU,4095"), self.result()])
        selected = HardwareResolver({}, probe=self.probe(runner)).resolve("auto", minimum_vram_mb=4096, encoder="h264_nvenc")
        self.assertEqual(selected.selected, "cpu")
        self.assertEqual(selected.encoder, "software")
        self.assertFalse(selected.gpu)
        self.assertEqual(selected.vram_mb, 0)
        self.assertTrue(selected.fallback)

    def test_failed_inventory_does_not_run_encoder(self) -> None:
        runner = Mock(return_value=self.result(returncode=1))
        self.assertFalse(self.probe(runner).detect().gpu_available)
        self.assertEqual(runner.call_count, 1)

    def test_malformed_inventory_never_qualifies_gpu(self) -> None:
        for text in ("", "GPU", "GPU,8192,extra", "GPU,8192\nOther,8192", "GPU,bad", "GPU,-1", "GPU,0", ",8192", "x" * 65537):
            with self.subTest(inventory=text[:30]):
                runner = Mock(return_value=self.result(text))
                snapshot = self.probe(runner).detect()
                self.assertFalse(snapshot.gpu_available)
                self.assertEqual(snapshot.vram_mb, 0)
                self.assertEqual(runner.call_count, 1)

    def test_failed_encoder_falls_back_after_device_detection(self) -> None:
        runner = Mock(side_effect=[self.result("GPU,8192"), self.result(returncode=1)])
        snapshot = self.probe(runner).detect()
        self.assertFalse(snapshot.gpu_available)
        self.assertIsNone(snapshot.encoder)
        self.assertEqual(runner.call_count, 2)

    def test_timeout_at_either_probe_returns_cpu(self) -> None:
        for phase in ("inventory", "encode"):
            with self.subTest(phase=phase):
                timeout = subprocess.TimeoutExpired("probe", 2)
                responses = [timeout] if phase == "inventory" else [self.result("GPU,8192"), timeout]
                snapshot = self.probe(Mock(side_effect=responses)).detect()
                self.assertFalse(snapshot.gpu_available)
                self.assertIn("timed out", snapshot.warnings[0])

    def test_missing_or_unusable_driver_is_optional(self) -> None:
        for error in (FileNotFoundError("driver missing"), PermissionError("driver denied")):
            with self.subTest(error=type(error).__name__):
                snapshot = self.probe(Mock(side_effect=error)).detect()
                self.assertFalse(snapshot.gpu_available)
                self.assertIsNone(snapshot.gpu_name)

    def test_hints_do_not_override_a_failed_probe(self) -> None:
        hints = {"DUBFLOW_GPU_NAME": "Claimed GPU", "DUBFLOW_GPU_VRAM_MB": "24000", "DUBFLOW_GPU_ENCODER": "h264_nvenc"}
        runner = Mock(return_value=self.result(returncode=1))
        selected = HardwareResolver(hints, probe=self.probe(runner)).resolve("gpu", encoder="h264_nvenc")
        self.assertEqual(selected.selected, "cpu")
        self.assertTrue(selected.fallback)
        with patch.dict("os.environ", hints, clear=True):
            inherited = HardwareResolver().detect()
            explicit = HardwareResolver({}).detect()
        self.assertFalse(inherited.gpu_available)
        self.assertTrue(inherited.warnings)
        self.assertFalse(explicit.gpu_available)
        self.assertEqual(explicit.warnings, ())

    def test_probe_rejects_relative_paths_and_unbounded_timeout(self) -> None:
        with self.assertRaises(ValueError):
            NvidiaHardwareProbe(Path("ffmpeg.exe"), self.driver)
        with self.assertRaises(ValueError):
            NvidiaHardwareProbe(self.ffmpeg, Path("nvidia-smi.exe"))
        for timeout in (0, -1, 31, float("inf"), float("nan"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                NvidiaHardwareProbe(self.ffmpeg, self.driver, timeout_seconds=timeout)

    def test_profile_rejects_invalid_policy_before_starting_process(self) -> None:
        runner = Mock()
        resolver = HardwareResolver({}, probe=self.probe(runner))
        for values in ({"requested": "unknown"}, {"minimum_vram_mb": -1}, {"minimum_vram_mb": True}, {"encoder": "custom"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                resolver.resolve(**values)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
