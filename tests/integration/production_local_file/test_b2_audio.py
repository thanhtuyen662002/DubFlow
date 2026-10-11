from __future__ import annotations

import io
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import wave
from types import SimpleNamespace

from engine.dubflow.worker.b2_audio import run_b2_audio
from engine.dubflow.worker.production_job import TextCue


ROOT = Path(__file__).resolve().parents[3]


class ActualMediaAudioAdapter:
    """Test seam that supplies decoded PCM bytes, never fixture TTS output."""

    def extract_audio(self, _source: Path, output: Path, **_options: object) -> Path:
        stream = io.BytesIO()
        with wave.open(stream, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16_000)
            writer.writeframes(b"".join(struct.pack("<h", 1200) for _ in range(48_000)))
        output.write_bytes(stream.getvalue())
        return output


class ProductionLocalFileB2Tests(unittest.TestCase):
    def test_real_builtin_tts_and_aud0_publish_editable_audio_provenance(self) -> None:
        with TemporaryDirectory(prefix="dubflow-production-b2-") as directory:
            root = Path(directory)
            result = run_b2_audio(
                media=ActualMediaAudioAdapter(),
                source_path=root / "portrait.mp4",
                source_probe=SimpleNamespace(has_audio=True),
                translated_cues=(
                    TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),
                    TextCue("cue-2", 1200, 2200, "World", "thế giới", 0.9),
                ),
                source_language="en",
                app_root=ROOT,
                profile_path=ROOT / "models" / "manifests" / "production-cpu-v1.json",
                work_dir=root / ".dubflow-work" / "b2-audio",
            )
            self.assertEqual(result.tts_document.provenance.backend_id, "dubflow-vi-builtin-v1")
            self.assertEqual(result.mix_document.provenance.backend_id, "pcm-duck-v1")
            self.assertEqual(len(result.tts_document.artifacts), 2)
            self.assertTrue(result.final_mix_path.is_file())
            self.assertTrue(result.dialogue_stem_path.is_file())
            self.assertTrue(result.original_audio_path.is_file())
            self.assertFalse(result.tts_document.provenance.backend_id.startswith("fixture"))
            self.assertEqual(result.mix_document.provenance.hardware_profile, "cpu")
            self.assertEqual(result.mix_document.source_start.ticks, 0)
            self.assertEqual(result.mix_document.source_end.ticks, 3000)
            self.assertEqual(result.mix_document.to_dict()["provenance"]["non_destructive"], True)


if __name__ == "__main__":
    unittest.main()
