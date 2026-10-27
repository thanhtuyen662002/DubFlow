from __future__ import annotations

import io
import json
import shutil
from dataclasses import replace
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace

from engine.dubflow.worker.b2_audio import B2AudioError, run_b2_audio, tts_recipe_identity, mix_recipe_identity, _dubbing_windows, _clamped_mappings
from engine.dubflow.mix import streaming, FileSegment
from engine.dubflow.media import FfmpegMediaAdapter, Rational
from engine.dubflow.media.adapter import MediaStream
from engine.dubflow.tts.adapter import TtsError
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice, parse_tts_json, TtsInput
from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.worker.production_job import TextCue, _apply_translation_overrides
from engine.dubflow.worker.tts_rewrites import validate_spoken_text_document


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
    def test_same_voice_cast_keeps_speaker_boundary_and_singleton_tts_input(self):
        requests = []
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                requests.append(request)
                return super().synthesize(request)
        def load(*args, voice_id=None):
            return SimpleNamespace(sample_rate=16000, channels=1), replace(approved_default_voice(), voice_id="primary")
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=load), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="zh", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                voice_by_cue={"a": "primary", "b": "primary", "c": "primary"}, speaker_by_cue={"a": "S1", "b": "S2", "c": "S2"},
                translated_cues=(TextCue("a", 0, 800, "你好", "Xin chào", .95), TextCue("b", 800, 1600, "好", "Được"),
                                 TextCue("c", 1600, 2200, "谢谢", "cảm ơn")))
            self.assertEqual([r.segment.segment_id for r in requests], ["a", "phrase-b"])
            self.assertEqual(result.member_cue_ids, {"a": ("a",), "phrase-b": ("b", "c")})
            base = TimeBase(1, 1000)
            self.assertEqual(requests[0].segment, TtsInput("a", "a", "Xin chào", TimePoint(0, base), TimePoint(800, base),
                source_language="zh", confidence=.95, render_window_end=TimePoint(800, base)))
            self.assertEqual(requests[0].config.max_speed_ratio_milli, 1300)
            self.assertEqual(result.tts_document.provenance.producer_version, "3.5.0")

    def test_phrase_documents_revision_cache_and_recovery_preserve_cue_edits(self):
        requests = []
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                requests.append(request)
                return super().synthesize(request)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            cues = (TextCue("a", 0, 400, "你好", "Xin chào."), TextCue("b", 500, 1000, "谢谢", "cảm ơn"),
                    TextCue("c", 1300, 1700, "再见", "Tạm biệt"), TextCue("d", 1800, 2400, "好", "được rồi!"))
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="zh", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json")
            cache = root / "original/tts-cache"
            result = run_b2_audio(**options, translated_cues=cues, work_dir=root / "original/b2", synthesis_cache_dir=cache)
            self.assertEqual([r.segment.text for r in requests], ["Xin chào. cảm ơn", "Tạm biệt, được rồi!"])
            self.assertEqual([(r.segment.start.ticks, r.segment.end.ticks) for r in requests], [(0, 1000), (1300, 2400)])
            self.assertEqual(result.member_cue_ids, {"phrase-a": ("a", "b"), "phrase-c": ("c", "d")})
            tts = json.loads(result.tts_document_path.read_bytes())
            spoken = json.loads(result.spoken_text_document_path.read_bytes())
            placement = json.loads(result.placement_document_path.read_bytes())
            self.assertEqual(tts["member_cue_ids"], spoken["member_cue_ids"])
            self.assertEqual(tts["member_cue_ids"], {w["cue_id"]: w["member_cue_ids"] for w in placement["windows"]})
            self.assertEqual(parse_tts_json(json.dumps(tts["tts_document"]))["source_segment_ids"], ["phrase-a", "phrase-c"])
            segments = [r.segment for r in requests]
            validate_spoken_text_document(spoken, result.tts_document_path.read_bytes(), segments,
                identity=spoken["checkpoint_identity"], member_cue_ids=result.member_cue_ids)
            changed = json.loads(json.dumps(spoken))
            changed["member_cue_ids"]["phrase-a"] = ["a"]
            with self.assertRaises(ValueError):
                validate_spoken_text_document(changed, result.tts_document_path.read_bytes(), segments,
                    identity=spoken["checkpoint_identity"], member_cue_ids=result.member_cue_ids)
            requests.clear()
            resumed = run_b2_audio(**options, translated_cues=cues, work_dir=root / "original/b2", synthesis_cache_dir=cache)
            self.assertEqual(requests, [])
            self.assertEqual(resumed.tts_document.artifacts, result.tts_document.artifacts)
            revision = root / "revision"
            shutil.copytree(cache, revision / "tts-cache")  # The desktop revision copies the synthesis cache.
            (revision / "translation_overrides.json").write_text(json.dumps({"schema_version": 1, "cues": {"b": "chào bạn"}}), encoding="utf-8")
            edited, _ = _apply_translation_overrides(cues, revision)
            revised = run_b2_audio(**options, translated_cues=edited, work_dir=revision / "b2", synthesis_cache_dir=revision / "tts-cache")
            self.assertEqual([(r.segment.segment_id, r.segment.text) for r in requests], [("phrase-a", "Xin chào. chào bạn")])
            self.assertEqual(revised.member_cue_ids, result.member_cue_ids)
            self.assertEqual(revised.tts_document.artifacts[1].content_hash, result.tts_document.artifacts[1].content_hash)
            self.assertEqual([(c.cue_id, c.start_ms, c.end_ms, c.source_text) for c in edited],
                             [(c.cue_id, c.start_ms, c.end_ms, c.source_text) for c in cues])

    def test_entire_fit_ladder_applies_to_a_phrase_and_reuses_its_receipt(self):
        requests = []
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                requests.append(request)
                if request.config.max_speed_ratio_milli < 1500:
                    raise TtsError("DURATION_FIT_REQUIRED", "phrase needs every bounded fit tier")
                return replace(super().synthesize(request), fit_mode="speed_adjusted", speed_ratio_milli=1400)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="zh", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                translated_cues=(TextCue("a", 0, 200, "现在", "Bây giờ"), TextCue("b", 200, 500, "不去", "không đi nhé.")))
            result = run_b2_audio(**options)
            self.assertEqual([r.segment.segment_id for r in requests], ["phrase-a"] * 4)
            self.assertEqual([r.segment.render_window_end.ticks for r in requests], [2500, 3000, 3000, 3000])
            self.assertEqual([r.config.max_speed_ratio_milli for r in requests], [1300, 1300, 1300, 1500])
            spoken = json.loads(result.spoken_text_document_path.read_bytes())
            self.assertEqual(spoken["entries"][0]["spoken_text"], "Giờ, không đi.")
            self.assertEqual(result.tts_document.artifacts[0].slot_end.ticks, 500)
            requests.clear()
            self.assertEqual(run_b2_audio(**options).tts_document.artifacts, result.tts_document.artifacts)
            self.assertEqual(requests, [])

    def test_failed_phrase_restores_its_whole_span_including_internal_gap(self):
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                if request.segment.segment_id == "phrase-a":
                    raise TtsError("DURATION_FIT_REQUIRED", "refused phrase")
                return super().synthesize(request)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            background = root / "background.wav"
            with wave.open(str(background), "wb") as writer:
                writer.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(struct.pack("<hh", 400, 500) * 48000)
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="zh", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2", background_path=background,
                translated_cues=(TextCue("a", 400, 900, "现在", "Bây giờ"), TextCue("b", 1000, 1600, "走", "đi nhé."),
                                 TextCue("c", 2200, 2600, "好", "Được")))
            self.assertEqual(result.tts_document.failures[0].segment_id, "phrase-a")
            tts = json.loads(result.tts_document_path.read_bytes())
            self.assertEqual(tts["member_cue_ids"]["phrase-a"], ["a", "b"])
            self.assertEqual(json.loads(result.spoken_text_document_path.read_bytes())["member_cue_ids"]["phrase-a"], ["a", "b"])
            self.assertEqual(result.background_stem_path.read_bytes(), background.read_bytes())
            for cue in ("a", "b"):
                self.assertIn("SEPARATION_SOURCE_RESTORED: " + cue, result.mix_document.warnings)
            with wave.open(result.mix_document.original_audio.path, "rb") as reader:
                frames = tuple(struct.iter_unpack("<hh", reader.readframes(reader.getnframes())))
            self.assertTrue(all(frame == (400, 500) for frame in frames[:6400] + frames[25600:]))
            self.assertTrue(all(frame == (1200, -1200) for frame in frames[6720:25280]))
            self.assertEqual(frames[6400], (400, 500))
            self.assertEqual(frames[25599], (400, 500))

    def test_all_failed_phrase_retains_member_evidence_for_b1_review(self):
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                raise TtsError("DURATION_FIT_REQUIRED", "refused complete phrase")
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=Engine()):
            root = Path(directory)
            with self.assertRaises(B2AudioError) as raised:
                run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                    source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="zh", app_root=ROOT,
                    profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                    translated_cues=(TextCue("a", 0, 500, "现在", "Bây giờ"), TextCue("b", 500, 1000, "走", "đi nhé.")))
            self.assertEqual(raised.exception.code, "TTS_FAILED")
            self.assertEqual(raised.exception.review["tts_failures"], 2)
            tts = json.loads(Path(raised.exception.review["tts_document"]).read_bytes())
            self.assertEqual(tts["member_cue_ids"], {"phrase-a": ["a", "b"]})
            self.assertEqual(tts["tts_document"]["failures"][0]["segment_id"], "phrase-a")

    def test_cues_past_the_media_end_are_clamped_not_fatal(self) -> None:
        cue = lambda cue_id, start, end: SimpleNamespace(cue_id=cue_id, start_ms=start, end_ms=end, source_text="你好",
                                                       translated_text="Xin chào", confidence=0.9)
        mappings = _clamped_mappings([cue("a", 0, 900), cue("b", 39_500, 40_100), cue("c", 39_900, 40_500)], 39_800)
        self.assertEqual([(item["cue_id"], item["start_ms"], item["end_ms"]) for item in mappings], [("a", 0, 900), ("b", 39_500, 39_800)])
        self.assertEqual(len(_dubbing_windows(mappings, 39_800)), 2)

    def test_wider_window_is_attempted_only_after_duration_failure_and_resumes(self):
        requests = []
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                requests.append(request)
                if request.segment.render_window_end.ticks < 3000:
                    raise TtsError("DURATION_FIT_REQUIRED", "complete speech needs a wider window")
                return super().synthesize(replace(request, sample_end=44800))
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2",
                translated_cues=(TextCue("a", 0, 500, "Hello", "Xin chào"),))
            result = run_b2_audio(**options)
            self.assertEqual([r.segment.render_window_end.ticks for r in requests], [2500, 3000])
            self.assertEqual([r.config.max_speed_ratio_milli for r in requests], [1300, 1300])
            artifact = result.tts_document.artifacts[0]
            self.assertEqual((artifact.slot_end.ticks, artifact.actual_end.ticks, artifact.render_window_end.ticks), (500, 2800, 3000))
            self.assertEqual(result.mix_document.duck_windows[0].end_sample, 44800)
            self.assertTrue(any(w.startswith("TTS_RENDER_WINDOW_WIDENED: a;") for w in result.tts_document.warnings))
            requests.clear()
            resumed = run_b2_audio(**options)
            self.assertEqual(requests, [])
            self.assertEqual(resumed.tts_document.artifacts, result.tts_document.artifacts)

    def test_separated_bed_restores_failed_cue_with_fades_and_preserves_pure_stem(self):
        class Engine(DeterministicFixtureEngine):
            def synthesize(self, request):
                if request.segment.segment_id == "failed":
                    raise TtsError("DURATION_FIT_REQUIRED", "complete speech exceeds every bounded tier")
                return super().synthesize(request)
        def load(*args, voice_id=None):
            voice = approved_default_voice()
            return SimpleNamespace(sample_rate=16000, channels=1), replace(voice, voice_id=voice_id or voice.voice_id)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=load), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: Engine()):
            root = Path(directory)
            background = root / "background.wav"
            with wave.open(str(background), "wb") as writer:
                writer.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(struct.pack("<hh", 400, 500) * 48000)
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2", background_path=background,
                voice_by_cue={"good": "other"},
                translated_cues=(TextCue("failed", 500, 1500, "Hello", "Bây giờ không đi nhé."),
                                 TextCue("good", 2000, 2500, "Thanks", "Cảm ơn")))
            self.assertEqual(result.background_stem_path.read_bytes(), background.read_bytes())
            self.assertEqual([a.segment_id for doc in result.tts_documents for a in doc.artifacts], ["good"])
            self.assertEqual(result.tts_documents[0].failures[0].segment_id, "failed")
            self.assertTrue(any("max speed 1500" in warning for warning in result.tts_documents[0].warnings))
            self.assertIn("SEPARATION_SOURCE_RESTORED: failed", result.mix_document.warnings)
            self.assertEqual([w.segment_id for w in result.mix_document.duck_windows], ["good"])
            with wave.open(result.mix_document.original_audio.path, "rb") as reader:
                frames = tuple(struct.iter_unpack("<hh", reader.readframes(reader.getnframes())))
            self.assertTrue(all(frame == (400, 500) for frame in frames[:8000] + frames[24000:]))
            self.assertTrue(all(frame == (1200, -1200) for frame in frames[8320:23680]))
            self.assertEqual(frames[8000], (400, 500))
            self.assertEqual(frames[23999], (400, 500))
            self.assertTrue(400 < frames[8160][0] < 1200)
            self.assertTrue(-1200 < frames[8160][1] < 500)
            self.assertTrue(400 < frames[23840][0] < 1200)

    def test_separated_bed_also_restores_tts_rejected_by_the_mixer(self):
        bridge = FileSegment.from_tts_artifact
        def rejected(artifact):
            segment = bridge(artifact)
            return replace(segment, content_hash="sha256:" + "0" * 64) if artifact.segment_id == "bad-hash" else segment
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: DeterministicFixtureEngine()), patch.object(
            FileSegment, "from_tts_artifact", side_effect=rejected):
            root = Path(directory)
            background = root / "background.wav"
            with wave.open(str(background), "wb") as writer:
                writer.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(struct.pack("<hh", 400, 500) * 48000)
            result = run_b2_audio(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2", background_path=background,
                translated_cues=(TextCue("bad-hash", 500, 1500, "Hello", "Xin chào"),
                                 TextCue("good", 2000, 2500, "Thanks", "Cảm ơn")))
            self.assertFalse(result.tts_document.failures)
            self.assertEqual(result.mix_document.failures[0].code, "AUDIO_HASH_MISMATCH")
            self.assertIn("SEPARATION_SOURCE_RESTORED: bad-hash", result.mix_document.warnings)
            with wave.open(result.mix_document.original_audio.path, "rb") as reader:
                reader.setpos(16000)
                self.assertEqual(reader.readframes(1), struct.pack("<hh", 1200, -1200))
            self.assertEqual(result.background_stem_path.read_bytes(), background.read_bytes())

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

    def test_progress_counts_every_phrase_once_across_voices_failures_and_resume(self):
        class Engine(DeterministicFixtureEngine):
            def __init__(self, pack, **kwargs):
                super().__init__()
            def synthesize(self, request):
                if request.segment.segment_id == "b":
                    raise TtsError("DURATION_FIT_REQUIRED", "refused phrase")
                return super().synthesize(request)
        def load(*args, voice_id=None):
            voice_id = voice_id or "primary"
            return SimpleNamespace(sample_rate=16000, channels=1, voice_id=voice_id), replace(approved_default_voice(), voice_id=voice_id)
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice", side_effect=load), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=Engine):
            root = Path(directory)
            reports = []
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json", tts_voice_id="primary",
                work_dir=root / "b2", voice_by_cue={"b": "second"}, on_progress=lambda *report: reports.append(report),
                translated_cues=(TextCue("a", 0, 800, "Hello", "Xin chào"), TextCue("b", 1000, 1800, "World", "Thế giới"),
                                 TextCue("c", 2000, 2800, "Thanks", "Cảm ơn")))
            result = run_b2_audio(**options)
            self.assertEqual(result.tts_documents[1].failures[0].segment_id, "b")
            self.assertEqual(reports, [(1, 3), (2, 3), (3, 3)])
            reports.clear()
            run_b2_audio(**options)  # Reused phrases count as settled too.
            self.assertEqual(reports, [(1, 3), (2, 3), (3, 3)])

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
            explicit = run_b2_audio(**options, voice_by_cue=None, background_path=None)
            self.assertEqual(previous, {path: path.read_bytes() for path in (root / "b2").rglob("*") if path.is_file()})
            self.assertEqual(explicit.tts_document.to_bytes(), result.tts_document.to_bytes())
            self.assertEqual(result.voices, (result.voice.voice_id,))
            self.assertEqual(Path(result.tts_document.artifacts[0].path).parent, root / "b2/tts")
            self.assertIsNone(explicit.background_stem_path)
            self.assertEqual(explicit.bed, "source")

    def test_background_replaces_mixer_bed_and_retains_original_source(self):
        with TemporaryDirectory() as directory, patch(
            "engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
            return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), patch(
            "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: DeterministicFixtureEngine()):
            root = Path(directory)
            background = root / "background.wav"
            with wave.open(str(background), "wb") as writer:
                writer.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(struct.pack("<hh", 400, 500) * 48000)
            options = dict(media=HermeticDecodedAudioAdapter(), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), source_language="en", app_root=ROOT,
                profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                translated_cues=(TextCue("cue-1", 0, 1000, "Hello", "Xin chào"),))
            original = run_b2_audio(**options, work_dir=root / "original")
            separated = run_b2_audio(**options, work_dir=root / "separated", background_path=background)
            self.assertEqual(separated.original_audio_path.read_bytes(), original.original_audio_path.read_bytes())
            self.assertEqual(separated.background_stem_path.read_bytes(), background.read_bytes())
            self.assertEqual(Path(separated.mix_document.original_audio.path).read_bytes(), background.read_bytes())
            self.assertNotEqual(separated.final_mix_path.read_bytes(), original.final_mix_path.read_bytes())
            self.assertEqual(separated.dialogue_stem_path.read_bytes(), original.dialogue_stem_path.read_bytes())
            self.assertEqual(separated.bed, "spleeter-2stems")
            self.assertEqual(separated.mix_document.source_layout, "stereo-accompaniment")
            # A later analysis cannot mutate the bed of a published generation.
            background.write_bytes(b"later candidate")
            self.assertNotEqual(separated.background_stem_path.read_bytes(), background.read_bytes())
            with self.assertRaisesRegex(B2AudioError, "SEPARATION_BED_FAILED"):
                run_b2_audio(**options, work_dir=root / "bad-bed", background_path=root / "missing.wav")

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
        wider = _dubbing_windows(mappings, 9500, max_postroll_ms=4000)
        self.assertEqual([int(item["render_window_end"]["ticks"]) for item in wider], [1580, 3880, 6000, 4600, 9500])
        isolated = _dubbing_windows([mappings[0], {**mappings[-1], "start_ms": 8500}], 12000, max_postroll_ms=4000)
        self.assertEqual(int(isolated[0]["render_window_end"]["ticks"]), 4500)
        with self.assertRaises(B2AudioError):
            _dubbing_windows(mappings, 9500, max_postroll_ms=4001)
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

    def test_interrupted_production_synthesis_loads_per_phrase_checkpoints(self):
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
                translated_cues=(TextCue("cue-1", 0, 500, "Hello", "Xin chào", 0.95),
                                 TextCue("cue-1b", 500, 1000, "Thanks", "cảm ơn", 0.95),
                                 TextCue("cue-2", 1300, 2200, "World", "Thế giới", 0.9)))
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
            self.assertEqual(result.member_cue_ids["phrase-cue-1"], ("cue-1", "cue-1b"))
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
            self.assertEqual(len(result.tts_document.artifacts), 1)
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
