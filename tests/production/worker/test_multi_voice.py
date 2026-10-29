from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from engine.dubflow.diarization import speakers
from engine.dubflow.media import parse_ffprobe_json
from engine.dubflow.tts import approved_default_voice
from engine.dubflow.worker import production_job as worker

ROOT = Path(__file__).resolve().parents[3]
PRIMARY = "vi-truc-ly-vieneu3-v1"
OTHER = "vi-thien-minh-vieneu3-v1"
CUES = (worker.TextCue("a", 0, 1000, "你好"), worker.TextCue("b", 1500, 2500, "谢谢"))
RESULT = speakers.SpeakerResult({"a": "S1", "b": "S2"}, [
    {"id": "S1", "cues": ["a"], "seconds": 1.0, "gender": "female", "f0_hz": 220.0},
    {"id": "S2", "cues": ["b"], "seconds": 1.0, "gender": "male", "f0_hz": 120.0}])


class MultiVoiceWorkerTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, multi_voice=True, enable_dubbing=True):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"controlled media seam")
            config = worker.WorkerConfig("multi", "local-file", source, root / "output", ROOT, root / "models",
                root, root / "ffmpeg.exe", root / "ffprobe.exe", source_language="zh", enable_dubbing=enable_dubbing,
                multi_voice=multi_voice)
            probe = parse_ffprobe_json(json.dumps({"streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "time_base": "1/1000", "start_pts": 0, "duration_ts": 3000},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "time_base": "1/16000", "start_pts": 0,
                 "duration_ts": 48000, "sample_rate": "16000", "channels": 1}]}), source_path=source)
            def extract(_source, output, **kwargs):
                with wave.open(str(output), "wb") as writer:
                    writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    writer.writeframes(bytes(3000 * 16 * 2))
                return output
            media = Mock(extract_audio=Mock(side_effect=extract))
            media.render.side_effect = lambda source, output, **kwargs: output.write_bytes(b"controlled rendered video")
            yield root, config, probe, media

    def stage(self, config, probe, media, work, checkpoint, emitter, warnings):
        return worker._multi_voice_cast(config, "source-hash", CUES, probe, media, work, checkpoint,
                                        work / "checkpoint.json", emitter, warnings)

    def test_stage_checkpoint_recasts_without_inference_or_download(self):
        with self.fixture() as (root, config, probe, media), patch.object(worker, "ensure_model_profile") as download, \
             patch.object(speakers, "assign_speakers", return_value=RESULT) as assign:
            work = root / "work"
            work.mkdir()
            checkpoint, emitter, warnings = {"stages": {}}, Mock(), []
            result, cast = self.stage(config, probe, media, work, checkpoint, emitter, warnings)
            self.assertEqual(result, RESULT)
            self.assertEqual(cast["S1"], PRIMARY)
            self.assertNotEqual(cast["S1"], cast["S2"])
            self.assertEqual(json.loads((work / "speakers.json").read_text())["labels"], RESULT.labels)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(assign.call_count, 1)
            self.assertEqual(assign.call_args.kwargs["threshold"], .3)
            self.assertIn("speaker-chunks", str(assign.call_args.kwargs["checkpoint_dir"]))
            self.assertEqual(media.extract_audio.call_args.kwargs["sample_rate"], 16000)
            self.assertEqual(media.extract_audio.call_args.kwargs["channels"], 1)
            emitter.progress.assert_called_with(.39, "Đang nhận diện người nói (thử nghiệm)")
            recast = replace(config, speaker_voices={"S2": OTHER})
            result, cast = self.stage(recast, probe, media, work, checkpoint, emitter, warnings)
            self.assertEqual(cast["S2"], OTHER)
            self.assertEqual(assign.call_count, 1)
            self.assertEqual(download.call_count, 1)
            # Changed analysis recipe invalidates the whole stage, with a new
            # per-cue checkpoint namespace. Decoded source audio remains reusable.
            identity = checkpoint["stages"]["speakers"]["input_hash"]
            with patch.object(worker, "SPEAKER_THRESHOLD", .4):
                self.stage(config, probe, media, work, checkpoint, emitter, warnings)
            self.assertEqual(assign.call_count, 2)
            self.assertEqual(media.extract_audio.call_count, 1)
            self.assertNotEqual(checkpoint["stages"]["speakers"]["input_hash"], identity)
            self.assertEqual(warnings, [])

    def test_optional_stage_errors_always_return_single_voice_plan(self):
        for failing in ("download", "decode", "infer", "write", "cast"):
            with self.subTest(failing=failing), self.fixture() as (root, config, probe, media), \
                 patch.object(worker, "ensure_model_profile") as download, \
                 patch.object(speakers, "assign_speakers", return_value=RESULT) as assign, \
                 patch.object(speakers, "cast_voices", wraps=speakers.cast_voices) as cast:
                work = root / "work"
                work.mkdir()
                if failing == "download":
                    download.side_effect = OSError("offline")
                elif failing == "decode":
                    media.extract_audio.side_effect = OSError("decoder unavailable")
                elif failing == "infer":
                    assign.side_effect = RuntimeError("native runtime unavailable")
                elif failing == "write":
                    (work / "speakers.json").mkdir()  # atomic promotion must fail
                else:
                    cast.side_effect = ValueError("invalid catalog")
                warnings = []
                self.assertEqual(self.stage(config, probe, media, work, {"stages": {}}, Mock(), warnings), (None, {}))
                self.assertEqual(len(warnings), 1)
                self.assertTrue(warnings[0].startswith("MULTI_VOICE_SKIPPED:"))

    def fake_b2(self, **kwargs):
        root = kwargs["work_dir"]
        root.mkdir(parents=True)
        def artifact(name):
            path = root / name
            path.write_bytes(name.encode())
            return SimpleNamespace(path=str(path), content_hash=worker._sha256(path))
        original, dialogue, final = [artifact(name) for name in ("original.wav", "dialogue.wav", "final.wav")]
        tts_path, mix_path = root / "tts_document.json", root / "mix_document.json"
        tts_path.write_text("{}")
        mix_path.write_text("{}")
        document = SimpleNamespace(provenance=SimpleNamespace(backend_id="hermetic-tts"), failures=(), warnings=())
        mix = SimpleNamespace(provenance=SimpleNamespace(to_dict=lambda: {"backend": "hermetic-mix"}), failures=(), warnings=(),
                              original_audio=original, dialogue_stem=dialogue, final_mix=final)
        return SimpleNamespace(final_mix_path=Path(final.path), source_audio_path=Path(original.path), original_audio_path=Path(original.path),
            dialogue_stem_path=Path(dialogue.path), tts_document_path=tts_path, mix_document_path=mix_path, tts_document=document,
            mix_document=mix, voice=replace(approved_default_voice(), voice_id=PRIMARY), placement_document_path=None,
            spoken_text_document_path=None, voices=tuple(dict.fromkeys(kwargs.get("voice_by_cue", {}).values())) or (PRIMARY,))

    @contextmanager
    def pipeline(self, config, probe, media):
        runtime = SimpleNamespace(provenance=lambda: {"backend": "hermetic-translation"})
        translated = tuple(replace(cue, translated_text="Xin chào") for cue in CUES)
        with patch.object(worker, "ensure_model_profile"), \
             patch.object(worker, "MediaProbe") as probing, \
             patch.object(worker, "FfmpegMediaAdapter", return_value=media), \
             patch.object(worker, "_load_sidecar", return_value=CUES), \
             patch.object(worker, "create_translation_runtime", return_value=runtime), \
             patch.object(worker, "_translate_with_argos", return_value=translated), \
             patch.object(worker, "run_b2_audio", side_effect=self.fake_b2) as dub:
            probing.return_value.probe.return_value = probe
            yield dub

    def test_pipeline_exports_speakers_and_cast_changes_generation_without_asr(self):
        with self.fixture() as (root, config, probe, media), self.pipeline(config, probe, media) as dub, \
             patch.object(speakers, "assign_speakers", return_value=RESULT) as assign:
            first = worker.run_local_file(config, Mock())
            manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
            timeline = json.loads((config.output_dir / "editable/timeline.json").read_text(encoding="utf-8"))
            qc = json.loads(Path(first["qc"]).read_text(encoding="utf-8"))
            self.assertEqual([cue["speaker"] for cue in timeline["cues"]], ["S1", "S2"])
            self.assertEqual([(cue["cue_id"], cue["start_ms"], cue["end_ms"]) for cue in timeline["cues"]], [("a", 0, 1000), ("b", 1500, 2500)])
            self.assertEqual(manifest["speakers"], qc["speakers"])
            self.assertEqual(manifest["speakers"][0], {"id": "S1", "gender": "female", "voice_id": PRIMARY, "cues": ["a"]})
            first_dir = dub.call_args.kwargs["work_dir"]
            first_identity = first_dir.parent.name
            recast = replace(config, speaker_voices={"S2": OTHER})
            worker.run_local_file(recast, Mock())
            self.assertEqual(dub.call_args.kwargs["voice_by_cue"], {"a": PRIMARY, "b": OTHER})
            self.assertEqual(dub.call_args.kwargs["speaker_by_cue"], RESULT.labels)
            self.assertNotEqual(dub.call_args.kwargs["work_dir"].parent.name, first_identity)
            self.assertEqual(assign.call_count, 1)
            # The recast publication retires the earlier, now unreachable generation.
            self.assertFalse(first_dir.exists())
            self.assertTrue(dub.call_args.kwargs["work_dir"].is_dir())

    def test_failed_phrase_counts_all_member_cues_in_review_metadata(self):
        with self.fixture(multi_voice=False) as (root, config, probe, media), self.pipeline(config, probe, media) as dub:
            def failed_phrase(**kwargs):
                audio = self.fake_b2(**kwargs)
                audio.member_cue_ids = {"phrase-a": ("a", "b")}
                audio.tts_document.failures = (SimpleNamespace(segment_id="phrase-a"),)
                audio.mix_document.failures = (SimpleNamespace(segment_id="phrase-a"),)
                audio.tts_document_paths = (audio.tts_document_path,)
                return audio
            dub.side_effect = failed_phrase
            result = worker.run_local_file(config, Mock())
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            qc = json.loads(Path(result["qc"]).read_text(encoding="utf-8"))
            checkpoint = json.loads((config.output_dir / ".dubflow-work/checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["tts_failures"], 2)
            self.assertEqual(manifest["audio"]["mix_failures"], 2)
            self.assertEqual(qc["audio"]["tts_failures"], 2)
            self.assertEqual(checkpoint["stages"]["b2-audio"]["tts_documents"], [manifest["audio"]["tts_document"]])

    def test_b1_fallback_retains_all_failed_phrase_review_paths_and_counts(self):
        with self.fixture(multi_voice=False) as (root, config, probe, media), self.pipeline(config, probe, media) as dub:
            review = {"tts_document": str(config.output_dir / ".dubflow-work/tts_document.json"),
                      "tts_documents": [str(config.output_dir / ".dubflow-work/tts_document.json")], "tts_failures": 2}
            dub.side_effect = worker.B2AudioError("TTS_FAILED", "refused phrase", review=review)
            result = worker.run_local_file(config, Mock())
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            checkpoint = json.loads((config.output_dir / ".dubflow-work/checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["mode"], "original")
            self.assertEqual(manifest["audio"]["tts_failures"], 2)
            self.assertEqual(checkpoint["stages"]["b2-audio"], {"status": "failed", **review})

    def test_pipeline_optional_failure_dubs_one_voice_and_default_is_opt_in(self):
        for multi, dubbing in ((True, True), (False, True), (True, False)):
            with self.subTest(multi=multi, dubbing=dubbing), self.fixture(multi_voice=multi, enable_dubbing=dubbing) as (root, config, probe, media), \
                 self.pipeline(config, probe, media) as dub, patch.object(speakers, "assign_speakers", side_effect=RuntimeError("no runtime")) as assign:
                result = worker.run_local_file(config, Mock())
                manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
                self.assertNotIn("speakers", manifest)
                self.assertEqual(assign.call_count, int(multi and dubbing))
                if dubbing:
                    self.assertNotIn("voice_by_cue", dub.call_args.kwargs)
                    self.assertEqual(manifest["audio"]["mode"], "dubbed")
                else:
                    dub.assert_not_called()
                self.assertEqual(any(warning.startswith("MULTI_VOICE_SKIPPED:") for warning in manifest["warnings"]), multi and dubbing)

    def test_group_failure_retries_once_with_single_voice_and_reports_actual_cast(self):
        with self.fixture() as (root, config, probe, media), self.pipeline(config, probe, media) as dub, \
             patch.object(speakers, "assign_speakers", return_value=RESULT):
            def fail_group(**kwargs):
                if kwargs.get("voice_by_cue") is not None:
                    raise worker.B2AudioError("VOICE_PACK_CHECKSUM_MISMATCH", "secondary voice unavailable")
                return self.fake_b2(**kwargs)
            dub.side_effect = fail_group
            result = worker.run_local_file(config, Mock())
            self.assertEqual(dub.call_count, 2)
            self.assertIn("voice_by_cue", dub.call_args_list[0].kwargs)
            self.assertNotIn("voice_by_cue", dub.call_args_list[1].kwargs)
            self.assertNotEqual(dub.call_args_list[0].kwargs["work_dir"], dub.call_args_list[1].kwargs["work_dir"])
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["mode"], "dubbed")
            self.assertEqual({speaker["voice_id"] for speaker in manifest["speakers"]}, {PRIMARY})
            self.assertTrue(any(warning.startswith("MULTI_VOICE_SKIPPED:") for warning in manifest["warnings"]))


if __name__ == "__main__":
    unittest.main()
