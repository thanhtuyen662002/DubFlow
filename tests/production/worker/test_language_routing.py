from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import Mock, patch

from engine.dubflow.media.adapter import MediaStream, Rational
from engine.dubflow.translation import argos_runtime
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


class ProductionRouteFixture:
    """Real route selection/publication, with only native inference/media mocked."""

    cue_identity = [("cue-1", 1250, 2500, "你好"), ("cue-2", 2601, 3103, "世界")]

    def __init__(self, root, source_language):
        app = root / "app"
        profiles = app / "models/manifests"
        profiles.mkdir(parents=True)
        (profiles / "production-cpu-v1.json").write_text("{}", encoding="utf-8")
        self.profile_path = profiles / "production-translation-v1.json"
        self.profile = {
            "schema_version": 1, "profile_id": "test-pinned-routes",
            "runtime_version": "1.11.0",
            "runtime_components": {"ctranslate2": "4.8.2", "sentencepiece": "0.2.2"},
            "routes": {"zh": ["zh_en", "en_vi"], "zh-TW": ["zh_en", "en_vi"]},
            # Synthetic pins: no archives or model weights are needed by these tests.
            "artifacts": [
                {"id": package_id, "from_code": source, "to_code": target,
                 "package_version": "1.0", "sha256": archive * 64,
                 "tree_sha256": tree * 64, "size_bytes": 1,
                 "path": package_id + ".argosmodel", "zip_root": package_id}
                for package_id, source, target, archive, tree in (
                    ("zh_en", "zh", "en", "a", "b"), ("en_vi", "en", "vi", "c", "d"))
            ],
        }
        self.write_profile()
        source = root / "video.mp4"
        source.write_bytes(b"source-media")
        source.with_suffix(".srt").write_text(
            "1\n00:00:01,250 --> 00:00:02,500\n你好\n\n"
            "2\n00:00:02,601 --> 00:00:03,103\n世界\n", encoding="utf-8")
        self.config = SimpleNamespace(
            job_id="job-211", stage_id="local-file", source_path=source,
            output_dir=root / "output", app_root=app, model_root=root / "models",
            ffmpeg_path=root / "ffmpeg", ffprobe_path=root / "ffprobe",
            media_runtime_root=root, checkpoint_path=None, source_language=source_language,
            target_language="vi", enable_dubbing=False, burn_in_subtitles=False)
        self.config.model_root.mkdir()
        self.work = self.config.output_dir / ".dubflow-work"
        self.calls = []
        self.preparations = []
        self.renders = []
        self.reject_qc = False
        self.vietnamese = {"Hello": "Xin chào", "World": "Thế giới"}

    def write_profile(self):
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")

    def prepare(self, runtime, model_root):
        self.preparations.append(runtime.provenance())
        runtime._translators = [
            SimpleNamespace(translate=lambda text, package=package: self.translate(package, text))
            for package in runtime.packages]

    def translate(self, package, text):
        self.calls.append((package["id"], text))
        if package["to_code"] == "en":
            return {"你好": "Hello", "世界": "World"}[text]
        return self.vietnamese[text]

    def render(self, source, output, **kwargs):
        # Make the stand-in video bytes depend on the actual generated subtitles.
        content = kwargs["subtitle_path"].read_bytes()
        self.renders.append(content)
        output.write_bytes(content)

    def probe(self, path):
        codec = "vp9" if self.reject_qc and path != self.config.source_path else "h264"
        return SimpleNamespace(
            has_audio=False, duration_ticks=4000,
            video=MediaStream(0, "video", codec, Rational(1, 1000), 0, 4000, 4000),
            to_dict=lambda: {"video_codec": codec})

    def run(self):
        return worker.run_local_file(self.config, Mock())

    def read(self, name):
        return json.loads((self.work / name).read_text(encoding="utf-8"))

    def public_bytes(self):
        return {path.relative_to(self.config.output_dir): path.read_bytes()
                for path in self.config.output_dir.rglob("*")
                if path.is_file() and not path.is_relative_to(self.work)}


class LanguageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.cues = (worker.TextCue("cue-1", 1250, 2500, "你好", confidence=0.7),)

    @contextmanager
    def production_route(self, source_language="zh"):
        with TemporaryDirectory() as directory:
            fixture = ProductionRouteFixture(Path(directory), source_language)
            with patch.object(worker, "ensure_model_profile"), \
                    patch.object(worker, "MediaProbe") as probing, \
                    patch.object(worker, "FfmpegMediaAdapter", return_value=fixture), \
                    patch.object(worker.ArgosRuntime, "prepare", autospec=True, side_effect=fixture.prepare):
                probing.return_value.probe.side_effect = fixture.probe
                yield fixture

    def assert_route_output(self, fixture, result, *, reviewed_overrides=None):
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        transcript = fixture.read("transcript.json")
        translation = fixture.read("translation.json")
        checkpoint = fixture.read("checkpoint.json")["stages"]
        language = fixture.config.source_language
        packages = {item["id"]: item for item in fixture.profile["artifacts"]}
        expected = {
            "backend": "argos-pinned-routes-v1",
            "runtime": "argostranslate-" + fixture.profile["runtime_version"],
            "components": fixture.profile["runtime_components"],
            "requested_source_language": language, "backend_route": ["zh", "en", "vi"],
            "profile_sha256": sha256(fixture.profile_path.read_bytes()).hexdigest(),
            "settings": {"device": "cpu", "compute_type": "int8",
                         "sentence_boundary": "cue-punctuation-v1", "beam_size": 4},
            "packages": [{key: packages[package_id][key]
                          for key in ("id", "from_code", "to_code", "package_version", "sha256", "tree_sha256")}
                         for package_id in fixture.profile["routes"][language]],
        }
        self.assertEqual(manifest["target_language"], "vi")
        self.assertEqual(manifest["language"]["source_language"], language)
        self.assertEqual(manifest["language"]["language_authority"], "requested")
        self.assertEqual(transcript["requested_source_language"], language)
        self.assertEqual(transcript["source_language"], language)
        self.assertEqual(translation["source_language"], language)
        self.assertEqual(translation["target_language"], "vi")
        self.assertEqual(translation["provenance"], expected)
        # Reviewed text supplements the selected machine route; it must not erase it.
        self.assertEqual(manifest["translation"], {**expected, "reviewed_overrides": reviewed_overrides})
        if reviewed_overrides is None:
            self.assertEqual(manifest["cues"], translation["cues"])
        for stage, filename in (("transcript", "transcript.json"), ("translation", "translation.json")):
            self.assertEqual(checkpoint[stage]["sha256"], "sha256:" + sha256((fixture.work / filename).read_bytes()).hexdigest())
            self.assertEqual(Path(checkpoint[stage]["path"]), fixture.work / filename)
        timeline = json.loads(Path(manifest["artifacts"]["editable_timeline"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(timeline["time_base"], {"numerator": 1, "denominator": 1000})
        for document in (transcript, translation, manifest, timeline):
            self.assertEqual([(cue["cue_id"], cue["start_ms"], cue["end_ms"], cue["source_text"])
                              for cue in document["cues"]], fixture.cue_identity)
            for cue in document["cues"]:
                self.assertIs(type(cue["start_ms"]), int)
                self.assertIs(type(cue["end_ms"]), int)
        self.assertEqual(timeline["cues"], manifest["cues"])
        self.assertEqual(checkpoint["render"]["sha256"], "sha256:" + sha256(Path(result["final_video"]).read_bytes()).hexdigest())
        self.assertEqual(set(manifest["artifacts"]),
                         {"final_video", "captions_srt", "captions_ass", "qc_report", "editable_timeline"})
        for artifact in manifest["artifacts"].values():
            path = Path(artifact["path"])
            self.assertTrue(path.is_relative_to(fixture.config.output_dir))
            self.assertFalse(path.is_relative_to(fixture.work))
            self.assertEqual(artifact["sha256"], "sha256:" + sha256(path.read_bytes()).hexdigest())
            self.assertEqual(artifact["size_bytes"], path.stat().st_size)
        return manifest

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
            with wave.open(str(root / "a.wav"), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(3 * 16000 * 2))
            with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
                auto = worker._transcribe_with_faster_whisper(root / "a.wav", root, "auto")
                explicit = worker._transcribe_with_faster_whisper(root / "a.wav", root, "zh-TW")
        self.assertEqual((auto.source_language, auto.authority, auto.probability), ("zh", "whisper-detected", 0.98))
        self.assertEqual((auto.cues[0].start_ms, auto.cues[0].end_ms), (1250, 2500))
        self.assertTrue(auto.cues[0].cue_id.startswith("asr-20000-40000-"))
        self.assertEqual(auto.cues[0].confidence, 0)
        self.assertEqual(auto.asr_evidence["recipe"], "faster-whisper-1.2.1-bounded-word-gap-v4")
        self.assertEqual(auto.asr_evidence["cue_quality"][auto.cues[0].cue_id]["timing_basis"], "segment-fallback")
        self.assertEqual(explicit.source_language, "zh-TW")
        self.assertIsNone(explicit.probability)
        self.assertIsNone(observed_calls[0]["language"])
        self.assertEqual(observed_calls[1]["language"], "zh")
        self.assertTrue(all(call["word_timestamps"] for call in observed_calls))

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
        document = {"schema_version": 4, "requested_source_language": "auto", "source_language": "zh", "language_authority": "whisper-detected", "language_probability": 0.9}
        self.assertEqual(worker._language_checkpoint(self.cues, document, "auto").source_language, "zh")
        for change in ({"schema_version": 1}, {"schema_version": 2}, {"schema_version": 3}, {"language_authority": "requested"}, {"language_probability": True}, {"source_language": "und"}, {"schema_version": 4.0}):
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


    def test_worker_persists_route_provenance_and_reuses_validated_output(self):
        for language in ("zh", "zh-TW"):
            with self.subTest(language=language), self.production_route(language) as fixture:
                first = fixture.run()
                manifest = self.assert_route_output(fixture, first)
                self.assertEqual([cue["translated_text"] for cue in manifest["cues"]], ["Xin chào", "Thế giới"])
                self.assertEqual(fixture.calls,
                                 [("zh_en", "你好"), ("en_vi", "Hello"), ("zh_en", "世界"), ("en_vi", "World")])
                before = {fixture.work / name: (fixture.work / name).read_bytes()
                          for name in ("transcript.json", "translation.json")}
                before.update({Path(first[key]): Path(first[key]).read_bytes() for key in ("srt", "ass")})
                chunks = {path: path.read_bytes() for path in (fixture.work / "translation-chunks").glob("*.json")}
                self.assertEqual(len(chunks), 2)
                render = fixture.read("checkpoint.json")["stages"]["render"]
                video = Path(first["final_video"]).read_bytes()
                second = fixture.run()
                self.assert_route_output(fixture, second)
                self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (1, 4, 1))
                self.assertEqual(fixture.read("checkpoint.json")["stages"]["render"], render)
                self.assertEqual(Path(second["final_video"]).read_bytes(), video)
                for path, content in {**before, **chunks}.items():
                    self.assertEqual(path.read_bytes(), content, f"unchanged route rewrote {path.name}")

    def test_worker_invalidates_derived_outputs_on_route_change(self):
        # A legacy producer can pass media QC while leaving English/Chinese text.
        # Valid file hashes must not let that text survive a new route identity.
        for index, changed in enumerate(("source_language", "selected_packages", "package_version", "sha256", "tree_sha256", "runtime")):
            with self.subTest(changed=changed), self.production_route() as fixture:
                stale = "Hello (stale English)" if index % 2 == 0 else "你好（旧中文）"
                fixture.vietnamese = {"Hello": stale, "World": stale}
                first = fixture.run()
                old_manifest = self.assert_route_output(fixture, first)
                old_translation = fixture.read("translation.json")
                old_render = fixture.read("checkpoint.json")["stages"]["render"]
                old_chunks = set((fixture.work / "translation-chunks").glob("*.json"))
                self.assertIn(stale, Path(first["srt"]).read_text(encoding="utf-8"))
                self.assertIn(stale.encode("utf-8"), Path(first["final_video"]).read_bytes())
                package = fixture.profile["artifacts"][1]
                if changed == "source_language":
                    fixture.config.source_language = "zh-TW"
                elif changed == "selected_packages":
                    replacement = {**package, "id": "en_vi_next", "zip_root": "en_vi_next"}
                    fixture.profile["artifacts"].append(replacement)
                    fixture.profile["routes"]["zh"] = ["zh_en", "en_vi_next"]
                elif changed == "runtime":
                    fixture.profile["runtime_version"] = "1.11.1"
                else:
                    package[changed] = "1.1" if changed == "package_version" else "e" * 64
                fixture.write_profile()
                fixture.vietnamese = {"Hello": "Xin chào", "World": "Thế giới"}
                # Simulate a newly pinned runtime without importing/installing it.
                with patch.object(argos_runtime, "RUNTIME_VERSION", fixture.profile["runtime_version"]):
                    second = fixture.run()
                    manifest = self.assert_route_output(fixture, second)
                    self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (2, 8, 2))
                    self.assertNotEqual(fixture.read("translation.json")["input_hash"], old_translation["input_hash"])
                    self.assertNotEqual(fixture.read("checkpoint.json")["stages"]["render"]["input_hash"], old_render["input_hash"])
                    self.assertNotEqual(manifest["translation"], old_manifest["translation"])
                    self.assertEqual(len(set((fixture.work / "translation-chunks").glob("*.json")) - old_chunks), 2)
                    self.assertEqual([cue["translated_text"] for cue in manifest["cues"]], ["Xin chào", "Thế giới"])
                    for key in ("srt", "ass", "final_video"):
                        content = Path(second[key]).read_bytes()
                        self.assertNotIn(stale.encode("utf-8"), content)
                        self.assertIn("Xin chào".encode("utf-8"), content)
                    fixture.run()
                    self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (2, 8, 2))

    def test_worker_route_change_rerenders_when_translated_text_is_unchanged(self):
        with self.production_route() as fixture:
            first = fixture.run()
            original = self.assert_route_output(fixture, first)
            old_translation = fixture.read("translation.json")
            old_render = fixture.read("checkpoint.json")["stages"]["render"]
            subtitles = Path(first["ass"]).read_bytes()
            fixture.profile["artifacts"][1]["package_version"] = "2.0"
            fixture.write_profile()
            result = fixture.run()
            manifest = self.assert_route_output(fixture, result)
            self.assertEqual(manifest["cues"], original["cues"])
            self.assertEqual(Path(result["ass"]).read_bytes(), subtitles)
            self.assertNotEqual(fixture.read("translation.json")["input_hash"], old_translation["input_hash"])
            self.assertNotEqual(fixture.read("checkpoint.json")["stages"]["render"]["input_hash"], old_render["input_hash"])
            self.assertNotEqual(manifest["translation"], original["translation"])
            self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (2, 8, 2),
                             "route provenance must invalidate rendering even when subtitle bytes are unchanged")

    def test_worker_revalidates_tampered_render_and_changed_subtitles(self):
        with self.production_route() as fixture:
            fixture.run()
            fixture.config.burn_in_subtitles = True
            fixture.run()
            self.assertEqual((len(fixture.calls), len(fixture.renders)), (4, 2))
            (fixture.config.output_dir / "final_vi.mp4").write_bytes(b"tampered public render")
            result = fixture.run()
            self.assert_route_output(fixture, result)
            self.assertEqual((len(fixture.calls), len(fixture.renders)), (4, 3))
            sidecar = fixture.config.source_path.with_suffix(".srt")
            sidecar.write_text(sidecar.read_text(encoding="utf-8").replace("你好", "世界"), encoding="utf-8")
            fixture.cue_identity = [("cue-1", 1250, 2500, "世界"), ("cue-2", 2601, 3103, "世界")]
            result = fixture.run()
            manifest = self.assert_route_output(fixture, result)
            self.assertEqual((len(fixture.calls), len(fixture.renders)), (6, 4))
            self.assertEqual([cue["translated_text"] for cue in manifest["cues"]], ["Thế giới", "Thế giới"])

    def test_changed_route_failed_qc_preserves_previous_export(self):
        with self.production_route() as fixture:
            first = fixture.run()
            old_manifest = self.assert_route_output(fixture, first)
            previous = fixture.public_bytes()
            source = fixture.config.source_path
            original_source = {path: path.read_bytes() for path in (source, source.with_suffix(".srt"))}
            fixture.profile["artifacts"][1]["package_version"] = "2.0"
            fixture.write_profile()
            fixture.vietnamese = {"Hello": "Chào bạn", "World": "Thế giới mới"}
            fixture.reject_qc = True
            with self.assertRaisesRegex(worker.ProductionJobError, "QC_VIDEO_CODEC"):
                fixture.run()
            # Includes every public file, even editable subtitle copies omitted
            # from manifest.artifacts, and detects added/missing public files.
            self.assertEqual(fixture.public_bytes(), previous, "failed QC replaced the validated public export")
            self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (2, 8, 2))
            self.assertIn("Chào bạn".encode("utf-8"), fixture.renders[-1])
            self.assertNotIn("render", fixture.read("checkpoint.json")["stages"])
            rejected_translation = (fixture.work / "translation.json").read_bytes()
            fixture.reject_qc = False  # Retry changes the failed QC condition.
            result = fixture.run()
            manifest = self.assert_route_output(fixture, result)
            self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (2, 8, 3),
                             "retry may reuse new translations but must regenerate the QC-rejected render")
            self.assertEqual((fixture.work / "translation.json").read_bytes(), rejected_translation)
            self.assertNotEqual(manifest["translation"], old_manifest["translation"])
            self.assertEqual(manifest["translation"]["packages"][1]["package_version"], "2.0")
            self.assertEqual([cue["translated_text"] for cue in manifest["cues"]], ["Chào bạn", "Thế giới mới"])
            self.assertIn("Chào bạn".encode("utf-8"), Path(result["final_video"]).read_bytes())
            self.assertNotEqual(fixture.public_bytes(), previous)
            for path, content in original_source.items():
                self.assertEqual(path.read_bytes(), content)

    def test_reviewed_override_keeps_machine_route_provenance_in_manifest(self):
        with self.production_route() as fixture:
            first = fixture.run()
            original = self.assert_route_output(fixture, first)
            machine_translation = (fixture.work / "translation.json").read_bytes()
            reviewed = {"cue-1": "Xin chào bạn đã duyệt"}
            (fixture.config.output_dir / "translation_overrides.json").write_text(
                json.dumps({"schema_version": 1, "cues": reviewed}, ensure_ascii=False), encoding="utf-8")
            identity = "sha256:" + sha256(json.dumps(reviewed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            result = fixture.run()
            manifest = self.assert_route_output(fixture, result, reviewed_overrides=identity)
            self.assertEqual((len(fixture.preparations), len(fixture.calls), len(fixture.renders)), (1, 4, 2))
            self.assertEqual((fixture.work / "translation.json").read_bytes(), machine_translation)
            self.assertEqual(manifest["translation"], {**original["translation"], "reviewed_overrides": identity})
            self.assertEqual([cue["translated_text"] for cue in manifest["cues"]], [reviewed["cue-1"], "Thế giới"])
            for key in ("srt", "ass", "final_video"):
                self.assertIn(reviewed["cue-1"].encode("utf-8"), Path(result[key]).read_bytes())

    def test_published_private_audio_is_immutable_and_failed_generation_resumes(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            work = output / ".dubflow-work"
            work.mkdir()
            checkpoint_path = work / "checkpoint.json"
            checkpoint = {"stages": {}}
            first = worker._b2_generation(checkpoint, checkpoint_path, work, output, "a" * 64)
            self.assertEqual(worker._b2_generation(checkpoint, checkpoint_path, work, output, "a" * 64), first)
            first.mkdir(parents=True)
            (first / "tts_document.json").write_bytes(b"validated audio provenance")
            (output / "job_manifest.json").write_text(json.dumps({"artifacts": {"tts_document": {"path": str(first / "tts_document.json")}}}))
            second = worker._b2_generation(checkpoint, checkpoint_path, work, output, "a" * 64)
            self.assertNotEqual(first, second)
            self.assertEqual(worker._b2_generation(checkpoint, checkpoint_path, work, output, "a" * 64), second)
            changed = worker._b2_generation(checkpoint, checkpoint_path, work, output, "b" * 64)
            self.assertNotEqual(second, changed)
            self.assertEqual((first / "tts_document.json").read_bytes(), b"validated audio provenance")

    def test_source_cannot_be_a_controlled_export_or_private_file(self):
        from unittest.mock import Mock
        with TemporaryDirectory() as directory:
            output = Path(directory)
            for source in (output / "final_vi.mp4", output / "editable/source.mp4", output / ".dubflow-work/source.mp4"):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"preserve input")
                config = SimpleNamespace(source_path=source, output_dir=output)
                with self.assertRaisesRegex(worker.ProductionJobError, "OUTPUT_SOURCE_OVERLAP"):
                    worker.run_local_file(config, Mock())
                self.assertEqual(source.read_bytes(), b"preserve input")


if __name__ == "__main__":
    unittest.main()
