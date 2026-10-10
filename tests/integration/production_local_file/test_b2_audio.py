from __future__ import annotations

import io
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace

from engine.dubflow.worker.b2_audio import B2AudioError, run_b2_audio, tts_recipe_identity, mix_recipe_identity
from engine.dubflow.mix import streaming
from engine.dubflow.media import FfmpegMediaAdapter, Rational
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
    def test_no_audio_with_supplied_dialogue_produces_dub_and_honest_silent_source(self):
        with TemporaryDirectory(prefix="dubflow-no-audio-b2-") as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=DeterministicFixtureEngine()):
            root = Path(directory)
            source = root / "no-audio.mp4"
            source.write_bytes(b"controlled-media-seam-not-a-real-video")
            executable = Path(sys.executable).resolve()
            media = FfmpegMediaAdapter(executable, trusted_root=executable.parent)
            # Actual bounded PCM writer/mixer, substituted TTS and media probe.
            # Native packaged real-media/backend qualification remains separate.
            probe = SimpleNamespace(has_audio=False, video=SimpleNamespace(time_base=Rational(1, 1000), duration_ticks=3000))
            with patch.object(media, "extract_audio", side_effect=AssertionError("source has no audio to decode")):
                result = run_b2_audio(media=media, source_path=source, source_probe=probe,
                    translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),),
                    source_language="en", app_root=ROOT, profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                    work_dir=root / "b2", tts_voice_id="vi-truc-ly-vieneu3-v1")
            self.assertEqual(result.mix_document.source_layout, "generated-silence-stereo")
            self.assertEqual(result.mix_document.source_end.ticks, 3000)
            self.assertEqual(len(result.tts_document.artifacts), 1)
            self.assertFalse(result.tts_document.failures)
            with wave.open(str(result.source_audio_path), "rb") as reader:
                self.assertEqual((reader.getnframes(), reader.getframerate(), reader.getnchannels()), (144000, 48000, 2))
                self.assertEqual(reader.readframes(reader.getnframes()), bytes(144000 * 4))
            with wave.open(str(result.final_mix_path), "rb") as reader:
                self.assertEqual((reader.getnframes(), reader.getnchannels()), (144000, 2))
                self.assertTrue(any(reader.readframes(reader.getnframes())))
            self.assertTrue(result.dialogue_stem_path.is_file())

    def test_no_audio_without_positive_source_duration_refuses_before_model_bootstrap(self):
        for duration in (None, 0):
            with self.subTest(duration=duration), patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice") as bootstrap:
                probe = SimpleNamespace(has_audio=False, video=SimpleNamespace(time_base=None, duration_ticks=None), duration_ticks=duration)
                with self.assertRaisesRegex(B2AudioError, "MEDIA_DURATION_INVALID"):
                    run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path="unused.mp4", source_probe=probe,
                        translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),),
                        source_language="en", app_root=ROOT, profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir="unused")
                bootstrap.assert_not_called()

    def test_no_audio_does_not_invent_dialogue(self):
        with patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice") as bootstrap:
            with self.assertRaisesRegex(B2AudioError, "TTS_INPUT_EMPTY"):
                run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path="unused.mp4", source_probe=SimpleNamespace(has_audio=False),
                    translated_cues=(), source_language="en", app_root=ROOT,
                    profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir="unused")
            bootstrap.assert_not_called()

    def test_interrupted_production_synthesis_loads_per_cue_checkpoints(self):
        class InterruptedEngine(DeterministicFixtureEngine):
            interrupted = True

            def synthesize(self, request):
                if self.interrupted and request.segment.segment_id == "cue-2":
                    raise KeyboardInterrupt("worker interrupted after first committed cue")
                return super().synthesize(request)

        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())):
            root = Path(directory)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),
                                 TextCue("cue-2", 1200, 2200, "World", "Thế giới", 0.9)))
            with patch("engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=InterruptedEngine()):
                with self.assertRaises(KeyboardInterrupt):
                    run_b2_audio(**options)
            wav = next((root / "b2/tts").glob("tts-*.wav"))
            original = (wav.read_bytes(), wav.stat().st_mtime_ns)
            self.assertEqual(len(list((root / "b2/tts/checkpoints").glob("*.json"))), 1)
            self.assertFalse((root / "b2/tts_document.json").exists())
            engine = InterruptedEngine()
            engine.interrupted = False
            with patch("engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=engine):
                result = run_b2_audio(**options)
            self.assertEqual(engine.calls, [Path(result.tts_document.artifacts[1].path).stem])
            self.assertEqual((wav.read_bytes(), wav.stat().st_mtime_ns), original)
            self.assertEqual(len(result.tts_document.artifacts), 2)
            self.assertTrue(result.final_mix_path.is_file())

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
                tts_voice_id="vi-truc-ly-vieneu3-v1",
            )
            bootstrap.assert_called_once()
            self.assertEqual(bootstrap.call_args.kwargs["voice_id"], "vi-truc-ly-vieneu3-v1")
            backend.assert_called_once_with(pack, ffmpeg_path=HermeticDecodedAudioAdapter.ffmpeg_path)
            self.assertEqual(result.tts_document.provenance.backend_id, "vieneu-v3-turbo-onnx-v1")
            self.assertEqual(result.tts_document.provenance.producer_version, "3.2.0")
            self.assertEqual(result.tts_document.provenance.model_id, "dubflow-fixture-vi")
            self.assertEqual(result.mix_document.provenance.backend_id, "pcm-stream-duck-v1")
            self.assertEqual(result.mix_document.provenance.producer_version, "2.0.1")
            self.assertEqual(result.mix_document.provenance.runtime, "owned-python/numpy-2.2.6")
            self.assertEqual(len(result.tts_document.artifacts), 2)
            self.assertTrue(result.final_mix_path.is_file())
            self.assertTrue(result.dialogue_stem_path.is_file())
            self.assertTrue(result.original_audio_path.is_file())
            self.assertEqual(result.mix_document.provenance.hardware_profile, "cpu")
            self.assertEqual(result.mix_document.source_start.ticks, 0)
            self.assertEqual(result.mix_document.source_end.ticks, 3000)
            self.assertEqual(result.mix_document.to_dict()["provenance"]["non_destructive"], True)

            old_files = {path: path.read_bytes() for path in (result.final_mix_path, result.dialogue_stem_path,
                         result.original_audio_path, Path(result.final_mix_path.parent / "mix_document.json"))}
            old_recipe = mix_recipe_identity()
            with patch.object(streaming, "PRODUCER_VERSION", "2.0.2"):
                self.assertNotEqual(mix_recipe_identity(), old_recipe)
                changed = run_b2_audio(
                    media=HermeticDecodedAudioAdapter(), source_path=root / "portrait.mp4",
                    source_probe=SimpleNamespace(has_audio=True),
                    translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),
                                     TextCue("cue-2", 1200, 2200, "World", "thế giới", 0.9)),
                    source_language="en", app_root=ROOT,
                    profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                    work_dir=root / ".dubflow-work" / "b2-audio", tts_voice_id="vi-truc-ly-vieneu3-v1")
            self.assertNotEqual(changed.final_mix_path, result.final_mix_path)
            self.assertEqual(changed.mix_document.provenance.producer_version, "2.0.2")
            for path, data in old_files.items():
                self.assertEqual(path.read_bytes(), data)

    def test_unavailable_mixer_recipe_is_a_typed_b1_downgrade(self):
        with patch("engine.dubflow.worker.b2_audio._digest", side_effect=OSError("cannot read mixer")):
            with self.assertRaises(B2AudioError) as raised:
                mix_recipe_identity()
        self.assertEqual(raised.exception.code, "MIX_RECIPE_UNAVAILABLE")
        self.assertFalse(raised.exception.retryable)

    def test_missing_or_corrupt_voice_preserves_existing_b1_assets(self) -> None:
        for code in ("MODEL_DOWNLOAD_FAILED", "VOICE_PACK_CHECKSUM_MISMATCH", "VOICE_ID_UNKNOWN"):
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
