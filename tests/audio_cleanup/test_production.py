from __future__ import annotations

import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
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
            self.assertIn(result.metrics.decision, {"AUD-2", "AUD-0"})

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
        self.assertEqual(gpu.selected, "gpu")
        self.assertEqual(gpu.encoder, "h264_nvenc")
        malformed = HardwareResolver({"DUBFLOW_GPU_NAME": "Test GPU", "DUBFLOW_GPU_VRAM_MB": "bad"}).resolve("auto")
        self.assertEqual(malformed.selected, "cpu")
        self.assertTrue(malformed.fallback)


if __name__ == "__main__":
    unittest.main()
