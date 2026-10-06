from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.dubflow.worker import production_job as worker


class RecordingRuntime:
    source_language = "zh"

    def __init__(self, *, fail_on=None, version=1):
        self.calls = []
        self.prepared = False
        self.fail_on = fail_on
        self.version = version

    def prepare(self, root):
        self.prepared = True

    def provenance(self):
        return {"route": ["zh", "en", "vi"], "version": self.version}

    def translate_text(self, text):
        self.calls.append(text)
        if text == self.fail_on:
            raise RuntimeError("native process interrupted")
        return "Bản dịch " + text


class LanguageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.cues = (worker.TextCue("cue-1", 1250, 2500, "你好", confidence=0.7),)

    def test_auto_and_und_never_mean_english(self):
        for observed in (None, "und"):
            with self.subTest(observed=observed), self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_UNRESOLVED"):
                worker._resolved_language(self.cues, "auto", observed)

    def test_explicit_variant_remains_declared_and_mismatch_is_typed(self):
        self.assertEqual(worker._resolved_language(self.cues, "zh-tw", "zh"), "zh-TW")
        with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_MISMATCH"):
            worker._resolved_language(self.cues, "zh", "en")

    def test_whisper_language_evidence_keeps_ticks_and_probability(self):
        observed_calls = []
        class Model:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, path, **kwargs):
                observed_calls.append(kwargs)
                return iter([SimpleNamespace(text="你好", start=1.25, end=2.5)]), SimpleNamespace(language="zh", language_probability=0.98)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "asr/faster-whisper-small").mkdir(parents=True)
            with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
                auto = worker._transcribe_with_faster_whisper(root / "a.wav", root, "auto")
                explicit = worker._transcribe_with_faster_whisper(root / "a.wav", root, "zh-TW")
        self.assertEqual((auto.source_language, auto.authority, auto.probability), ("zh", "whisper-detected", 0.98))
        self.assertEqual((auto.cues[0].cue_id, auto.cues[0].start_ms, auto.cues[0].end_ms), ("cue-1", 1250, 2500))
        self.assertEqual(explicit.source_language, "zh-TW")
        self.assertIsNone(explicit.probability)
        self.assertIsNone(observed_calls[0]["language"])
        self.assertEqual(observed_calls[1]["language"], "zh")

    def test_unresolved_language_is_rejected_before_runtime_initialization(self):
        runtime = RecordingRuntime()
        with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_UNRESOLVED"):
            worker._translate_with_argos(self.cues, Path("models"), "auto", runtime=runtime)
        self.assertFalse(runtime.prepared)

    def test_sidecar_without_audio_needs_explicit_language(self):
        with TemporaryDirectory() as directory:
            config = SimpleNamespace(source_language="auto")
            with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_UNRESOLVED"):
                worker._sidecar_language(self.cues, config, Path(directory) / "missing.wav")
            config.source_language = "zh-CN"
            resolved = worker._sidecar_language(self.cues, config, Path(directory) / "missing.wav")
            self.assertEqual((resolved.source_language, resolved.authority), ("zh-CN", "requested"))

    def test_legacy_and_forged_authority_cannot_reuse_auto_language(self):
        document = {"schema_version": 2, "requested_source_language": "auto", "source_language": "zh", "language_authority": "whisper-detected", "language_probability": 0.9}
        self.assertEqual(worker._language_checkpoint(self.cues, document, "auto").source_language, "zh")
        for change in ({"schema_version": 1}, {"language_authority": "requested"}, {"language_probability": True}, {"source_language": "und"}, {"schema_version": 2.0}):
            candidate = {**document, **change}
            if change.get("source_language") == "und":
                with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_UNRESOLVED"):
                    worker._language_checkpoint(self.cues, candidate, "auto")
            else:
                self.assertIsNone(worker._language_checkpoint(self.cues, candidate, "auto"))
        self.assertIsNone(worker._language_checkpoint(self.cues, document, "en"))

    def test_chunk_recovery_reuses_completed_work_and_route_changes_invalidate(self):
        cues = (*self.cues, worker.TextCue("cue-2", 2600, 3000, "世界"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = RecordingRuntime(fail_on="世界")
            with self.assertRaisesRegex(worker.ProductionJobError, "TRANSLATION_FAILED"):
                worker._translate_with_argos(cues, root, "zh", runtime=runtime, chunks_dir=root)
            self.assertEqual(len(list(root.glob("*.json"))), 1)
            resumed = RecordingRuntime()
            result = worker._translate_with_argos(cues, root, "zh", runtime=resumed, chunks_dir=root)
            self.assertEqual(resumed.calls, ["世界"])
            self.assertEqual((result[0].start_ms, result[0].end_ms), (1250, 2500))
            changed = RecordingRuntime(version=2)
            worker._translate_with_argos(cues, root, "zh", runtime=changed, chunks_dir=root)
            self.assertEqual(changed.calls, ["你好", "世界"])

    def test_tampered_chunk_is_recomputed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worker._translate_with_argos(self.cues, root, "zh", runtime=RecordingRuntime(), chunks_dir=root)
            path = next(root.glob("*.json"))
            value = json.loads(path.read_text(encoding="utf-8"))
            value["cues"][0]["translated_text"] = "incorrect cached output"
            path.write_text(json.dumps(value), encoding="utf-8")
            resumed = RecordingRuntime()
            worker._translate_with_argos(self.cues, root, "zh", runtime=resumed, chunks_dir=root)
            self.assertEqual(resumed.calls, ["你好"])

    def test_legacy_list_chunk_is_recomputed_instead_of_failing_the_item(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worker._translate_with_argos(self.cues, root, "zh", runtime=RecordingRuntime(), chunks_dir=root)
            path = next(root.glob("*.json"))
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(value["cues"]), encoding="utf-8")
            resumed = RecordingRuntime()
            result = worker._translate_with_argos(self.cues, root, "zh", runtime=resumed, chunks_dir=root)
            self.assertEqual(resumed.calls, ["你好"])
            self.assertEqual(result[0].start_ms, 1250)


    def test_worker_invalidates_derived_outputs_on_route_change(self):
        from unittest.mock import Mock
        class Media:
            renders = 0
            def render(self, source, output, **kwargs):
                self.renders += 1
                output.write_bytes(kwargs["subtitle_path"].read_bytes())
        class Runtime(RecordingRuntime):
            def translate_text(self, text):
                return f"Vietnamese output version {self.version}"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            profile = app / "models/manifests/production-cpu-v1.json"
            profile.parent.mkdir(parents=True)
            profile.write_text("{}", encoding="utf-8")
            source = root / "video.mp4"
            source.write_bytes(b"source-media")
            source.with_suffix(".srt").write_text("1\n00:00:01,250 --> 00:00:02,500\n你好\n", encoding="utf-8")
            config = SimpleNamespace(job_id="job-1", stage_id="local-file", source_path=source, output_dir=root / "output", app_root=app, model_root=root / "models", ffmpeg_path=root / "ffmpeg", ffprobe_path=root / "ffprobe", media_runtime_root=root, checkpoint_path=None, source_language="zh", target_language="vi", enable_dubbing=False, burn_in_subtitles=False)
            config.model_root.mkdir()
            probe = SimpleNamespace(has_audio=False, duration_ticks=4000, video=SimpleNamespace(codec_name="h264"), to_dict=lambda: {})
            media = Media()
            runtime = Runtime()
            with patch.object(worker, "ensure_model_profile"), patch.object(worker, "MediaProbe") as probing, patch.object(worker, "FfmpegMediaAdapter", return_value=media), patch.object(worker, "ArgosRuntime", side_effect=lambda *a, **k: runtime):
                probing.return_value.probe.return_value = probe
                worker.run_local_file(config, Mock())
                self.assertEqual(media.renders, 1)
                worker.run_local_file(config, Mock())
                self.assertEqual(media.renders, 1)
                runtime.version = 2
                worker.run_local_file(config, Mock())
                self.assertEqual(media.renders, 2)
                source.with_suffix(".srt").write_text("1\n00:00:01,250 --> 00:00:02,500\n世界\n", encoding="utf-8")
                worker.run_local_file(config, Mock())
                self.assertEqual(media.renders, 3)
            self.assertIn("version 2", (config.output_dir / "captions_vi.srt").read_text(encoding="utf-8"))
            self.assertIn(b"version 2", (config.output_dir / "final_vi.mp4").read_bytes())
            manifest = json.loads((config.output_dir / "job_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["language"]["source_language"], "zh")
            self.assertEqual(manifest["translation"]["version"], 2)
            self.assertEqual(manifest["cues"][0]["source_text"], "世界")


if __name__ == "__main__":
    unittest.main()
