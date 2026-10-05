from __future__ import annotations

import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from dataclasses import replace
import wave

from engine.dubflow.models import HardwareResolver
from engine.dubflow.separation import CpuAttenuationBackend, SeparationConfig


def write_wav(path: Path) -> None:
    rate = 16_000
    samples = []
    for seconds, amplitude in ((0.8, 7000), (0.4, 0), (0.8, 7000)):
        for index in range(int(seconds * rate)):
            samples.append(0 if amplitude == 0 else int(amplitude * math.sin(2 * math.pi * 220 * index / rate)))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"".join(struct.pack("<h", value) for value in samples))


def write_activity_wav(path: Path, channels: int = 1) -> None:
    """PCM regression fixture, not a speech-quality benchmark."""
    frames = bytearray()
    for index in range(8_000):
        amplitude = 100 if index < 3_200 else 5_000
        sample = amplitude if index % 2 else -amplitude
        values = (sample,) if channels == 1 else (sample, -sample)
        frames.extend(struct.pack("<" + "h" * channels, *values))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(8_000)
        writer.writeframes(frames)


class ProductionAudioCleanupTests(unittest.TestCase):
    def test_real_pcm_gate_publishes_independent_stems_and_truthful_decision(self) -> None:
        with TemporaryDirectory(prefix="dubflow-aud2-") as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source)
            result = CpuAttenuationBackend().process(source, root / "stems")
            self.assertTrue(result.dialogue_path.is_file())
            self.assertTrue(result.background_path.is_file())
            self.assertTrue(result.metrics.dialogue_hash.startswith("sha256:"))
            self.assertTrue(result.metrics.background_hash.startswith("sha256:"))
            self.assertEqual(result.metrics.frame_count, 32_000)
            with wave.open(str(result.dialogue_path), "rb") as dialogue, wave.open(str(result.background_path), "rb") as background:
                self.assertEqual(dialogue.getnframes(), background.getnframes())
                self.assertEqual(dialogue.getframerate(), 16_000)
            self.assertEqual(result.metrics.decision, "AUD-0")

    def test_activity_is_not_a_quality_benchmark(self) -> None:
        with TemporaryDirectory(prefix="dubflow-audio-quality-") as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_activity_wav(source)
            result = CpuAttenuationBackend().process(source, root / "stems")
            self.assertGreater(result.metrics.active_fraction_milli, 500)
            self.assertLess(result.metrics.active_fraction_milli, 960)
            self.assertEqual(result.metrics.clipping_fraction_ppm, 0)
            self.assertEqual(result.metrics.decision, "AUD-0")
            report = result.to_dict()
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["metrics"]["backend_id"], "dubflow-cpu-gate-v2")
            self.assertIsNone(report["metrics"]["residual_speech_millidb"])
            self.assertIsNone(report["metrics"]["background_artifact_milli"])
            self.assertTrue(report["fallback"])
            self.assertTrue(any("not measured" in warning for warning in result.warnings))
            for residual, artifact in ((None, None), (None, 0), (0, None)):
                with self.subTest(residual=residual, artifact=artifact):
                    with self.assertRaisesRegex(ValueError, "requires measured"):
                        replace(result.metrics, decision="AUD-2", residual_speech_millidb=residual, background_artifact_milli=artifact)

    def test_opposite_phase_stereo_preserves_activity(self) -> None:
        with TemporaryDirectory(prefix="dubflow-stereo-energy-") as directory:
            root = Path(directory)
            mono, stereo = root / "mono.wav", root / "stereo.wav"
            write_activity_wav(mono)
            write_activity_wav(stereo, channels=2)
            backend = CpuAttenuationBackend()
            mono_result = backend.process(mono, root / "mono-stems")
            stereo_result = backend.process(stereo, root / "stereo-stems")
            self.assertGreater(stereo_result.metrics.active_fraction_milli, 500)
            self.assertEqual(stereo_result.metrics.active_fraction_milli, mono_result.metrics.active_fraction_milli)
            with wave.open(str(stereo_result.background_path), "rb") as background:
                self.assertEqual(background.getnchannels(), 2)
                self.assertEqual(background.getnframes(), 8_000)

    def test_empty_environment_does_not_inherit_host_gpu_hints(self) -> None:
        hints = {"DUBFLOW_GPU_NAME": "Host GPU", "DUBFLOW_GPU_VRAM_MB": "8192", "DUBFLOW_GPU_ENCODER": "h264_nvenc"}
        with patch.dict("os.environ", hints, clear=True):
            explicit = HardwareResolver({}).resolve("auto")
            inherited = HardwareResolver().resolve("auto", encoder="h264_nvenc")
        self.assertEqual(explicit.selected, "cpu")
        self.assertFalse(explicit.gpu)
        self.assertEqual(explicit.vram_mb, 0)
        self.assertEqual(inherited.selected, "cpu")
        self.assertTrue(inherited.fallback)

    def test_malformed_wav_fails_closed(self) -> None:
        with TemporaryDirectory(prefix="dubflow-aud2-") as directory:
            source = Path(directory) / "bad.wav"
            source.write_bytes(b"not wav")
            with self.assertRaises(ValueError):
                CpuAttenuationBackend().process(source, Path(directory) / "stems")

    def test_hardware_resolver_never_invents_gpu_and_falls_back(self) -> None:
        cpu = HardwareResolver({}).resolve("gpu")
        self.assertEqual(cpu.selected, "cpu")
        self.assertTrue(cpu.fallback)
        gpu = HardwareResolver({"DUBFLOW_GPU_NAME": "Test GPU", "DUBFLOW_GPU_VRAM_MB": "8192", "DUBFLOW_GPU_ENCODER": "h264_nvenc"}).resolve("auto", encoder="h264_nvenc")
        self.assertEqual(gpu.selected, "cpu")
        self.assertEqual(gpu.encoder, "software")
        self.assertTrue(gpu.fallback)
        malformed = HardwareResolver({"DUBFLOW_GPU_NAME": "Test GPU", "DUBFLOW_GPU_VRAM_MB": "bad"}).resolve("auto", encoder="h264_nvenc")
        self.assertEqual(malformed.selected, "cpu")
        self.assertTrue(malformed.fallback)


if __name__ == "__main__":
    unittest.main()
