from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace

from engine.dubflow.worker.b2_audio import B2AudioError, run_b2_audio, tts_recipe_identity, mix_recipe_identity, _dubbing_windows
from engine.dubflow.mix import streaming
from engine.dubflow.media import FfmpegMediaAdapter, Rational
from engine.dubflow.media.adapter import MediaStream
from engine.dubflow.tts.adapter import TtsError
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice
from engine.dubflow.worker.production_job import TextCue


ROOT = Path(__file__).resolve().parents[3]
VIDEO_STREAM = MediaStream(0, "video", "h264", Rational(1, 1000), 0, 3000, 3000)


class HermeticDecodedAudioAdapter:
    """Hermetic decoded PCM seam; this is wiring evidence, not media quality."""
    ffmpeg_path = Path("unused-fixture-ffmpeg")

    def extract_audio(self, _source: Path, output: Path, **options: object) -> Path:
        channels = options["channels"]
        # Opposite-phase channels disappear if the caller requests a downmix.
        frame = struct.pack("<hh", 1200, -1200) if channels == 2 else bytes(2)
        stream = io.BytesIO()
        with wave.open(stream, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(16_000)
            writer.writeframes(frame * 48_000)
        output.write_bytes(stream.getvalue())
        return output


class ProductionLocalFileB2Tests(unittest.TestCase):
    def test_multivoice_groups_keep_global_windows_union_artifacts_and_voice_caches(self):
        engines = []
        class Engine(DeterministicFixtureEngine):
            def __init__(self, pack, **kwargs):
                super().__init__()
                self.voice_id, self.cues, self.closed = pack.voice_id, [], False
                engines.append(self)
            def synthesize(self, request):
                self.cues.append(request.segment.segment_id)
                return super().synthesize(request)
            def close(self):
                self.closed = True
        def load(*args, voice_id=None):
            voice_id = voice_id or "primary"
            return SimpleNamespace(sample_rate=16000, channels=1, voice_id=voice_id), replace(approved_default_voice(), voice_id=voice_id)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=load), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=Engine):
            root = Path(directory)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", tts_voice_id="primary",
                synthesis_cache_dir=root / "cache", translated_cues=(
                    TextCue("a", 0, 800, "Hello", "Xin chào"),
                    TextCue("b", 1000, 1800, "World", "Thế giới"),
                    TextCue("c", 2000, 2800, "Thanks", "Cảm ơn")))
            result = run_b2_audio(**options, work_dir=root / "first", voice_by_cue={"b": "second"})
            self.assertEqual(result.voices, ("primary", "second"))
            self.assertEqual([engine.cues for engine in engines], [["a", "c"], ["b"]])
            self.assertTrue(all(engine.closed for engine in engines))
            self.assertEqual([doc.source_segment_ids for doc in result.tts_documents], [("a", "c"), ("b",)])
            self.assertEqual(len(result.mix_document.segments), 3)
            self.assertFalse(result.mix_document.failures)
            primary, second = result.tts_documents
            self.assertEqual(primary.provenance.voice_id, "primary")
            self.assertEqual(second.provenance.voice_id, "second")
            self.assertNotEqual(primary.provenance.input_hash, second.provenance.input_hash)
            for voice_id, document in zip(result.voices, result.tts_documents):
                self.assertTrue(all(Path(artifact.path).parent == root / "first/tts" / voice_id for artifact in document.artifacts))
                self.assertEqual(len(list((root / "first/tts" / voice_id / "checkpoints").glob("*.json"))), len(document.artifacts))
            self.assertEqual(primary.artifacts[0].render_window_end.ticks, 880)
            self.assertEqual(second.artifacts[0].render_window_end.ticks, 1880)
            self.assertTrue((root / "first/tts_document.json").is_file())
            self.assertTrue((root / "first/tts_document.second.json").is_file())
            self.assertTrue((root / "first/dubbing_spoken_text.second.json").is_file())
            first = json.loads(next((root / "first/tts/primary/checkpoints").iterdir()).read_text())
            other = json.loads(next((root / "first/tts/second/checkpoints").iterdir()).read_text())
            self.assertNotEqual(first["identity"], other["identity"])
            # A recast changes only b: a and c retain their cached primary voice.
            engines.clear()
            recast = run_b2_audio(**options, work_dir=root / "recast", voice_by_cue={"b": "third"})
            self.assertEqual([engine.cues for engine in engines], [[], ["b"]])
            self.assertEqual(recast.voices, ("primary", "third"))
            engines.clear()
            run_b2_audio(**options, work_dir=root / "again", voice_by_cue={"b": "second"})
            self.assertTrue(all(not engine.cues for engine in engines))

    def test_explicit_none_matches_default_bytes_and_flat_legacy_paths(self):
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: DeterministicFixtureEngine()), patch(
            "engine.dubflow.worker.b2_audio.TtsCheckpointStore.load", return_value={}):
            root = Path(directory)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào"),))
            result = run_b2_audio(**options)
            previous = {path: path.read_bytes() for path in (root / "b2").rglob("*") if path.is_file()}
            explicit = run_b2_audio(**options, voice_by_cue=None)
            self.assertEqual(previous, {path: path.read_bytes() for path in (root / "b2").rglob("*") if path.is_file()})
            self.assertEqual(explicit.tts_document.to_bytes(), result.tts_document.to_bytes())
            self.assertEqual(result.voices, (result.voice.voice_id,))
            self.assertEqual(Path(result.tts_document.artifacts[0].path).parent, root / "b2/tts")

    def test_reassigning_every_cue_uses_first_actual_voice_as_base_document(self):
        def load(*args, voice_id=None):
            voice_id = voice_id or "primary"
            return SimpleNamespace(sample_rate=16000, channels=1), replace(approved_default_voice(), voice_id=voice_id)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=load), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: DeterministicFixtureEngine()):
            root = Path(directory)
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2", tts_voice_id="primary",
                translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào"),), voice_by_cue={"cue-1": "second"})
            self.assertEqual(result.voices, ("second",))
            self.assertEqual(result.tts_document.provenance.voice_id, "second")
            self.assertEqual(result.voice.voice_id, "second")
            self.assertEqual(result.tts_document_path.name, "tts_document.json")

    def test_invalid_spoken_text_evidence_is_typed_and_preserves_existing_b1(self):
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=DeterministicFixtureEngine()), patch(
            "engine.dubflow.worker.b2_audio.SpokenTextStore.document", side_effect=ValueError("changed evidence")):
            root = Path(directory)
            b1 = root / "final_vi.mp4"
            b1.write_bytes(b"already-valid-B1-fixture")
            with self.assertRaises(B2AudioError) as caught:
                run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "stereo.mp4",
                    source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                    profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                    translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),))
            self.assertEqual(caught.exception.code, "TTS_SPOKEN_TEXT_EVIDENCE_INVALID")
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(b1.read_bytes(), b"already-valid-B1-fixture")

    def test_source_window_bounds_keep_nested_overlaps_next_cue_and_media_immutable(self):
        mappings = [{"cue_id": identifier, "start_ms": start, "end_ms": end, "confidence": .4}
            for identifier, start, end in (("first", 0, 500), ("second", 1700, 1800),
                ("outer", 4000, 6000), ("inner", 4500, 4600), ("last", 8000, 9000))]
        before = [dict(item) for item in mappings]
        windows = _dubbing_windows(mappings, 9500)
        self.assertEqual([int(item["render_window_end"]["ticks"]) for item in windows], [1580, 3800, 6000, 4600, 9500])
        self.assertEqual([item["overlaps_source"] for item in windows], [False, False, True, True, False])
        self.assertEqual(mappings, before)
        for invalid in (mappings + [mappings[0]], [{**mappings[0], "end_ms": 9501}]):
            with self.assertRaises(B2AudioError): _dubbing_windows(invalid, 9500)

    def test_stereo_source_survives_mix_with_centered_mono_dialogue(self):
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=DeterministicFixtureEngine()):
            root = Path(directory)
            # Actual PCM and streaming mixer; the decoder and TTS are fixtures.
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "stereo.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào", 0.95),))
            self.assertEqual(result.mix_document.source_layout, "stereo-source")
            self.assertEqual(result.source_audio_path.read_bytes(), result.original_audio_path.read_bytes())
            samples = {}
            for name, path in (("source", result.original_audio_path), ("speech", result.dialogue_stem_path),
                               ("final", result.final_mix_path)):
                with wave.open(str(path), "rb") as reader:
                    self.assertEqual((reader.getnchannels(), reader.getframerate(), reader.getnframes()), (2, 16000, 48000))
                    samples[name] = tuple(struct.iter_unpack("<hh", reader.readframes(reader.getnframes())))
            self.assertTrue(all(left == -right != 0 for left, right in samples["source"]))
            self.assertTrue(all(left == right for left, right in samples["speech"]))
            self.assertTrue(any(left for left, _ in samples["speech"]))
            self.assertTrue(all(left != right for left, right in samples["final"]))
            self.assertFalse(result.mix_document.failures)

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
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
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
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM),
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
            self.assertEqual(result.tts_document.provenance.producer_version, "3.5.0")
            self.assertEqual(result.tts_document.schema_version, 2)
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
                    source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM),
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
                            source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM),
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
