from __future__ import annotations

import io
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace

from engine.dubflow.worker.b2_audio import B2AudioError, run_b2_audio, tts_recipe_identity
from engine.dubflow.tts.adapter import TtsError
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice
from engine.dubflow.worker.production_job import TextCue


ROOT = Path(__file__).resolve().parents[3]


class HermeticDecodedAudioAdapter:
    """Hermetic decoded PCM seam; this is wiring evidence, not media quality."""
    ffmpeg_path = Path("unused-fixture-ffmpeg")

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
    def test_missing_or_invalid_recipe_remains_a_typed_b1_downgrade(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selector = root / "cpu.json"
            selector.write_text('{"tts_neural_profile":"missing.json"}')
            with self.assertRaises(B2AudioError):
                tts_recipe_identity(root, selector)
            selector.write_text('invalid JSON')
            with self.assertRaises(B2AudioError):
                tts_recipe_identity(root, selector)

    def test_selected_neural_backend_wires_aud0_editable_assets(self) -> None:
        pack = SimpleNamespace(sample_rate=16000, channels=1)
        with TemporaryDirectory(prefix="dubflow-production-b2-") as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", return_value=(pack, approved_default_voice())
        ) as bootstrap, patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=DeterministicFixtureEngine()
        ) as backend:
            root = Path(directory)
            result = run_b2_audio(
                media=HermeticDecodedAudioAdapter(),
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
            bootstrap.assert_called_once()
            backend.assert_called_once_with(pack, ffmpeg_path=HermeticDecodedAudioAdapter.ffmpeg_path)
            self.assertEqual(result.tts_document.provenance.backend_id, "vieneu-v3-turbo-onnx-v1")
            self.assertEqual(result.tts_document.provenance.producer_version, "3.0.0")
            self.assertEqual(result.tts_document.provenance.model_id, "dubflow-fixture-vi")
            self.assertEqual(result.mix_document.provenance.backend_id, "pcm-duck-v1")
            self.assertEqual(len(result.tts_document.artifacts), 2)
            self.assertTrue(result.final_mix_path.is_file())
            self.assertTrue(result.dialogue_stem_path.is_file())
            self.assertTrue(result.original_audio_path.is_file())
            self.assertEqual(result.mix_document.provenance.hardware_profile, "cpu")
            self.assertEqual(result.mix_document.source_start.ticks, 0)
            self.assertEqual(result.mix_document.source_end.ticks, 3000)
            self.assertEqual(result.mix_document.to_dict()["provenance"]["non_destructive"], True)

    def test_missing_or_corrupt_voice_preserves_existing_b1_assets(self) -> None:
        for code in ("MODEL_DOWNLOAD_FAILED", "VOICE_PACK_CHECKSUM_MISMATCH"):
            with self.subTest(code=code), TemporaryDirectory() as directory:
                root = Path(directory)
                b1 = root / "export.mp4"
                subtitles = root / "translation.vi.srt"
                b1.write_bytes(b"already-validated-B1")
                subtitles.write_bytes(b"already-validated-subtitles")
                with patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=TtsError(code, "voice unavailable")):
                    with self.assertRaises(B2AudioError) as raised:
                        run_b2_audio(
                            media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                            source_probe=SimpleNamespace(has_audio=True),
                            translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),),
                            source_language="en", app_root=ROOT,
                            profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                            model_root=root / "models", work_dir=root / "b2",
                        )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(b1.read_bytes(), b"already-validated-B1")
                self.assertEqual(subtitles.read_bytes(), b"already-validated-subtitles")
                self.assertFalse((root / "b2/source_audio.wav").exists())


if __name__ == "__main__":
    unittest.main()
