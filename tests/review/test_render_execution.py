import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.media import MediaProbeResult, MediaStream, Rational
from engine.dubflow.review.corrected_timeline import derive_corrected_timeline
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.render_execution import admit_context, render_candidate, MAX_PACKET, _path
from engine.dubflow.review.synthesis_input import _digest, _encode
import test_corrected_timeline as timelines
from schema_assertions import assert_schema

REPO = Path(__file__).resolve().parents[2]


def encode(value): return _encode(value, MAX_PACKET)


def pin(path):
    return {"path":str(path), "sha256":_digest(path.read_bytes()), "size_bytes":str(path.stat().st_size)}


def probe_result(path, *, duration=10000, audio=True):
    streams = [MediaStream(0, "video", "h264", Rational(1, 1000), 0, duration, duration,
                           width=320, height=180, frame_rate=Rational(25, 1))]
    if audio:
        streams.append(MediaStream(1, "audio", "aac", Rational(1, 48000), 0, duration*48,
                                   duration*48, sample_rate=48000, channels=2))
    return MediaProbeResult(path, "mov,mp4", duration, Rational(1, 1000), tuple(streams))


def render_fixture(directory, *, b1=True, voice_only=False, source_probe=None, source_bytes=None):
    root = Path(directory).resolve()
    original = root / "original"; original.mkdir()
    source = root / "source.mp4"; source.write_bytes(source_bytes or b"original-source-mp4")
    timeline, before, store, value = timelines.CorrectedTimelineTests().fixture(root, b1=b1)
    value["duration_ticks"] = 10000
    timeline = encode(value)
    after, _ = store.commit_correction(before, "risk-a", **({"voice":"voice-b"} if voice_only else {"translation":"Chào bạn"}))
    corrected = derive_corrected_timeline(timeline, before, after)
    artifacts = {}
    from engine.dubflow.worker.production_job import TextCue, _write_subtitles
    srt, ass = _write_subtitles(original, tuple(TextCue(**cue) for cue in value["cues"]))
    for path in (srt, ass):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    for role, name, raw in (("captions_srt", "captions_vi.srt", srt.read_bytes()),
                            ("captions_ass", "captions_vi.ass", ass.read_bytes()),
                            ("editable_timeline", "timeline.json", timeline)):
        path = original / name; path.write_bytes(raw)
        artifacts[role] = {**pin(path), "size_bytes":len(raw)}
    manifest = {"schema_version":1, "kind":"dubflow_localization_job", "job_id":before.job_id,
        "source":{"path":str(source), "sha256":pin(source)["sha256"],
                  "probe":(source_probe or probe_result(source)).to_dict()},
        "artifacts":artifacts, "warnings":["prior low-confidence warning"]}
    manifest_raw = encode(manifest)
    context = {"schema_version":1, "kind":"review_native_render_context", "job_id":before.job_id,
        "mode":"b1" if b1 else "b2", "before_snapshot_json":encode(before.to_dict()).decode(),
        "after_snapshot_json":encode(after.to_dict()).decode(), "original_manifest_json":manifest_raw.decode(),
        "original_manifest_sha256":_digest(manifest_raw), "original_timeline_json":timeline.decode(),
        "corrected_timeline_json":corrected.decode(), "corrected_timeline_sha256":_digest(corrected),
        "original_output_root":str(original), "source":{**pin(source), "artifact_root":str(root)},
        "retained_mix":None, "burn_in_subtitles":False}
    if not b1:
        retained = root / "retained"; retained.mkdir()
        # Small physical PCM + versioned metadata fixture, not a mixer/voice or
        # long-form qualification. No model inference is used by this suite.
        fixture = json.loads((REPO / "tests/review/native_mix_candidate_v1.json").read_text(encoding="utf-8"))
        mix = json.loads(fixture["packet"]["mix_document_json"])
        mix.update(segment_ids=[], segments=[], duck_windows=[], chunks=[], failures=[])
        mix["source_start"]["ticks"] = "0"; mix["source_end"]["ticks"] = "10000"
        for name in ("source_start", "source_end"):
            mix[name]["time_base"] = {"numerator":"1", "denominator":"1000"}
        for role in ("original_audio", "dialogue_stem", "final_mix"):
            path = retained / (role + ".wav")
            with wave.open(str(path), "wb") as audio:
                audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(48000*10*4))
            mix[role].update(path=str(path), frame_count=480000, content_hash=pin(path)["sha256"])
            mix[role]["metrics"].update(content_hash=pin(path)["sha256"], frame_count=480000,
                sample_count=960000, clipped_samples=0, peak=0, rms_milli=0)
        composition = {"schema_version":2, "kind":"review_audio_composition", "job_id":before.job_id,
            "manifest_sha256":context["original_manifest_sha256"], "review_snapshot_sha256":_digest(encode(after.to_dict()))}
        composition_raw = encode(composition)
        mix["provenance"]["input_hash"] = _digest(composition_raw)
        mix_raw = encode(mix)
        context["retained_mix"] = {"artifact_root":str(retained), "mix_document_json":mix_raw.decode(),
            "mix_document_sha256":_digest(mix_raw), "composition_json":composition_raw.decode(),
            "composition_sha256":_digest(composition_raw)}
    private = root / "private"; private.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (root / name).write_bytes(("owned-test-"+name).encode())
    return context, dict(output_root=private, ffmpeg_path=root/"ffmpeg", ffprobe_path=root/"ffprobe", media_root=root)


class RenderExecutionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native local-drive aliases require Windows")
    def test_native_extended_wav_paths_match_dos_root_identity_without_retargeting(self):
        with TemporaryDirectory(prefix="dubflow-render-os-root-") as directory:
            context, _ = render_fixture(directory, b1=False)
            retained = context["retained_mix"]
            root = Path(retained["artifact_root"])
            mix = json.loads(retained["mix_document_json"])
            prefix = chr(92) * 2 + "?" + chr(92)
            for role in ("original_audio", "dialogue_stem", "final_mix"):
                ordinary = Path(mix[role]["path"])
                extended = prefix + str(ordinary)
                self.assertTrue(os.path.samefile(ordinary, extended))
                self.assertEqual(str(_path(extended, root)), extended)
                self.assertEqual(_path(str(ordinary), Path(prefix + str(root))), ordinary)
                mix[role]["path"] = extended
            raw = encode(mix)
            retained.update(mix_document_json=raw.decode(), mix_document_sha256=_digest(raw))
            admitted = admit_context(encode(context))[0]
            self.assertEqual(admitted, context)
            foreign = root.parent / "foreign.wav"
            foreign.write_bytes(ordinary.read_bytes())
            self.assertEqual(pin(foreign)["sha256"], pin(ordinary)["sha256"])
            with self.assertRaises(ReviewError):
                _path(prefix + str(foreign), root)

    def test_physical_render_root_still_refuses_foreign_non_directory_and_linked_paths(self):
        from engine.dubflow.mix.adapter import MixError
        with TemporaryDirectory(prefix="dubflow-render-root-guards-") as directory:
            parent = Path(directory).resolve()
            root = parent / "retained"; root.mkdir()
            child = root / "wave.wav"; child.write_bytes(b"same PCM fixture bytes")
            foreign = parent / "wave.wav"; foreign.write_bytes(child.read_bytes())
            self.assertEqual(_path(str(child), root), child)
            with self.assertRaises(ReviewError):
                _path(str(foreign), root)
            with self.assertRaises(ReviewError):
                _path(str(child), child)
            linked = root / "linked.wav"
            try:
                linked.symlink_to(foreign)
            except OSError:
                return  # Foreign/root refusals above still run on Windows.
            with self.assertRaises(MixError):
                _path(str(linked), root)

    def adapters(self, context, args, *, decode_error=False):
        media_patch = patch("engine.dubflow.media.FfmpegMediaAdapter")
        probe_patch = patch("engine.dubflow.media.MediaProbe")
        media = self.enterContext(media_patch).return_value
        probe = self.enterContext(probe_patch).return_value
        media.ffmpeg_path = args["ffmpeg_path"]
        probe.ffprobe_path = args["ffprobe_path"]
        media.render.side_effect = lambda source, target, **kw: target.write_bytes(b"private corrected MP4")
        media._run.return_value = subprocess.CompletedProcess([], 1 if decode_error else 0, "", "bad frame" if decode_error else "")
        probe.probe.side_effect = lambda path: probe_result(path)
        return media, probe

    def test_b1_render_subtitles_qc_and_resume_without_asr_ocr_or_rerender(self):
        with TemporaryDirectory(prefix="dubflow-review-render-") as directory:
            context, args = render_fixture(directory)
            assert_schema(context, "native-render-context-v1.json")
            source = Path(context["source"]["path"]); original = source.read_bytes()
            media, probe = self.adapters(context, args)
            first = render_candidate(encode(context), **args)
            self.assertFalse(first["active_graph_published"])
            self.assertFalse(first["correction_ready"])
            assert_schema(first, "render-candidate-v1.json")
            srt = Path(first["artifacts"]["captions_srt"]["path"]).read_text(encoding="utf-8")
            self.assertIn("Chào bạn", srt); self.assertIn("Yên lặng", srt)
            self.assertIn("00:00:01,000 --> 00:00:02,000", srt)
            qc = json.loads(Path(first["artifacts"]["qc_report"]["path"]).read_bytes())
            self.assertEqual(qc["warnings"], ["prior low-confidence warning"])
            self.assertTrue(qc["full_av_decode"])
            self.assertEqual(media.render.call_args.kwargs["audio_path"], None)
            self.assertTrue(media.render.call_args.kwargs["preserve_original_audio"])
            stamp = {r:Path(p["path"]).stat().st_mtime_ns for r,p in first["artifacts"].items()}
            second = render_candidate(encode(context), **args)
            self.assertEqual(first, second); self.assertEqual(media.render.call_count, 1)
            self.assertEqual(media._run.call_count, 2); self.assertEqual(probe.probe.call_count, 4)
            self.assertEqual(stamp, {r:Path(p["path"]).stat().st_mtime_ns for r,p in second["artifacts"].items()})
            self.assertEqual(source.read_bytes(), original)

    def test_b2_voice_only_uses_retained_mix_and_keeps_exact_subtitle_and_timeline_bytes(self):
        with TemporaryDirectory(prefix="dubflow-review-voice-render-") as directory:
            context, args = render_fixture(directory, b1=False, voice_only=True)
            media, _ = self.adapters(context, args)
            result = render_candidate(encode(context), **args)
            manifest = json.loads(context["original_manifest_json"])
            for role in ("captions_srt", "captions_ass"):
                self.assertEqual(Path(result["artifacts"][role]["path"]).read_bytes(),
                                 Path(manifest["artifacts"][role]["path"]).read_bytes())
            self.assertEqual(Path(result["artifacts"]["corrected_timeline"]["path"]).read_bytes(), context["original_timeline_json"].encode())
            self.assertEqual(media.render.call_args.kwargs["audio_path"], Path(json.loads(context["retained_mix"]["mix_document_json"])["final_mix"]["path"]))
            self.assertFalse(media.render.call_args.kwargs["preserve_original_audio"])

    def test_source_or_mix_corruption_refuses_and_never_downgrades_b2(self):
        for corruption in ("source", "mix"):
            with self.subTest(corruption=corruption), TemporaryDirectory(prefix="dubflow-review-render-corrupt-") as directory:
                context, args = render_fixture(directory, b1=False)
                media, _ = self.adapters(context, args)
                path = Path(context["source"]["path"] if corruption == "source" else json.loads(context["retained_mix"]["mix_document_json"])["final_mix"]["path"])
                raw = path.read_bytes(); path.write_bytes(raw[:-1] + bytes([raw[-1]^1]))
                with self.assertRaises(ReviewError): render_candidate(encode(context), **args)
                self.assertFalse(media.render.called)
                self.assertEqual(list(args["output_root"].glob("*/result.json")), [])

    def test_bad_source_probe_or_full_decode_never_publishes_success(self):
        for bad_probe in (False, True):
            with self.subTest(probe=bad_probe), TemporaryDirectory(prefix="dubflow-review-render-qc-") as directory:
                context, args = render_fixture(directory)
                media, probe = self.adapters(context, args, decode_error=not bad_probe)
                if bad_probe: probe.probe.side_effect = lambda path: probe_result(path, duration=11000)
                with self.assertRaises(ReviewError): render_candidate(encode(context), **args)
                self.assertEqual(list(args["output_root"].glob("*/result.json")), [])
                self.assertEqual(media.render.call_count, 0 if bad_probe else 1)

    def test_checkpoint_missing_role_foreign_path_publication_and_same_size_corruption_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-render-checkpoint-") as directory:
            context, args = render_fixture(directory)
            media, _ = self.adapters(context, args)
            result = render_candidate(encode(context), **args)
            result_path = Path(result["artifacts"]["final_video"]["path"]).parent / "result.json"
            for case in ("missing", "foreign", "publish", "extra"):
                bad = copy.deepcopy(result)
                if case == "missing": bad["artifacts"].pop("final_video")
                elif case == "foreign": bad["artifacts"]["final_video"]["path"] = context["source"]["path"]
                elif case == "publish": bad["active_graph_published"] = True
                else: bad["extra"] = True
                result_path.write_bytes(encode(bad))
                with self.assertRaises(ReviewError): render_candidate(encode(context), **args)
            result_path.write_bytes(encode(result))
            video = Path(result["artifacts"]["final_video"]["path"]); raw = video.read_bytes()
            video.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            with self.assertRaises(ReviewError): render_candidate(encode(context), **args)
            self.assertEqual(media.render.call_count, 1)

    def test_unknown_context_and_wrong_revision_mode_or_timeline_refuse_before_media(self):
        with TemporaryDirectory(prefix="dubflow-review-render-context-") as directory:
            context, args = render_fixture(directory)
            for changes in ({"schema_version":True}, {"extra":1}, {"mode":"b2"}, {"job_id":"foreign"},
                            {"corrected_timeline_sha256":"sha256:"+"0"*64}):
                with self.assertRaises(ReviewError): admit_context(encode({**context, **changes}))
            with self.assertRaises(ReviewError):
                admit_context(encode(context).replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1))
            self.assertEqual(list(args["output_root"].iterdir()), [])

    def test_packaged_executable_hardlinks_are_readable_but_media_hardlinks_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-render-links-") as directory:
            context, args = render_fixture(directory)
            os.link(args["ffmpeg_path"], Path(directory)/"deduplicated-ffmpeg")
            self.adapters(context, args)
            render_candidate(encode(context), **args)
            os.link(context["source"]["path"], Path(directory)/"linked-source.mp4")
            with self.assertRaises(ValueError): render_candidate(encode(context), **args)

    def test_isolated_cli_refuses_missing_native_gate_and_wrong_context_hash(self):
        with TemporaryDirectory(prefix="dubflow-review-render-cli-") as directory:
            context, args = render_fixture(directory)
            path = Path(directory)/"context.json"; path.write_bytes(encode(context))
            command = [os.environ.get("DUBFLOW_TEST_PYTHON", sys.executable), "-I", "-S", "-B",
                str(REPO/"engine/dubflow/review/render_execution.py"), "--context", str(path),
                "--context-sha256", "sha256:"+"0"*64, "--output-root", str(args["output_root"]),
                "--ffmpeg", str(args["ffmpeg_path"]), "--ffprobe", str(args["ffprobe_path"]), "--media-root", str(args["media_root"])]
            for gate in (b"", b"\x01"):
                child = subprocess.run(command, input=gate, capture_output=True, timeout=20)
                self.assertEqual(child.returncode, 2, child.stderr)
                self.assertEqual(child.stdout, b"")
                self.assertEqual(json.loads(child.stderr)["code"], "REVIEW_RENDER_EXECUTION_FAILED")
            self.assertEqual(list(args["output_root"].iterdir()), [])


# New pure authority/wiring regressions; no native/media/model child or fixture.
import contextlib
from types import SimpleNamespace, ModuleType
import engine.dubflow.review.render_execution as compact_render
from engine.dubflow.review.production import ReviewError
from engine.dubflow.mix.adapter import MixError


class CompactRenderScratchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("C:/owned") if os.name == "nt" else Path("/owned")
        self.scratch = self.root / ".dfr" / "temp"
        self.context = {"original_output_root": str(self.root)}

    def plain_filesystem(self):
        stack = contextlib.ExitStack()
        for name, value in (("is_dir", True), ("is_file", False), ("is_symlink", False), ("exists", True)):
            stack.enter_context(patch.object(Path, name, return_value=value))
        stack.enter_context(patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0)))
        return stack

    def binding(self, value=None):
        return {key: str(value if value is not None else self.scratch) for key in ("TEMP", "TMP", "TMPDIR")}

    def test_only_exact_existing_native_layouts_and_triple_binding(self):
        with self.plain_filesystem():
            for directory in (self.scratch, self.root / ".dubflow-review-candidates" / "temp"):
                with patch.dict(os.environ, self.binding(directory), clear=True):
                    self.assertEqual(compact_render._render_subtitle_scratch(self.context), directory)
            for env in ({}, {"TEMP": str(self.scratch)}, {**self.binding(), "TMPDIR": str(self.root)},
                        self.binding(self.root / "foreign" / "temp"), self.binding(Path("relative"))):
                with patch.dict(os.environ, env, clear=True), self.assertRaises((ReviewError, MixError)):
                    compact_render._render_subtitle_scratch(self.context)

    def test_missing_linked_and_reparse_scratch_are_refused_without_fallback(self):
        with self.plain_filesystem(), patch.dict(os.environ, self.binding(), clear=True):
            with patch.object(Path, "is_dir", return_value=False), self.assertRaises((ReviewError, MixError)):
                compact_render._render_subtitle_scratch(self.context)
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises((ReviewError, MixError)):
                compact_render._render_subtitle_scratch(self.context)
            with patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400)), self.assertRaises((ReviewError, MixError)):
                compact_render._render_subtitle_scratch(self.context)

    @unittest.skipUnless(os.name == "nt", "Windows UTF16 bound")
    def test_existing_windows_200_utf16_bound_counts_surrogate_pairs(self):
        for size, accepted in ((200, True), (201, False)):
            base = Path("C:/\U0001f3ac") / ".dfr" / "temp"
            padding = size - len(str(base).encode("utf-16-le")) // 2
            root = Path("C:/\U0001f3ac" + "o" * padding)
            directory = root / ".dfr" / "temp"
            self.assertEqual(len(str(directory).encode("utf-16-le")) // 2, size)
            with self.plain_filesystem(), patch.dict(os.environ, self.binding(directory), clear=True):
                if accepted:
                    self.assertEqual(compact_render._render_subtitle_scratch({"original_output_root": str(root)}), directory)
                else:
                    with self.assertRaises(ReviewError):
                        compact_render._render_subtitle_scratch({"original_output_root": str(root)})

    @unittest.skipUnless(os.name == "nt", "Windows path spelling")
    def test_verbatim_alias_is_not_a_membership_waiver(self):
        value = chr(92) * 2 + "?" + chr(92) + str(self.scratch)
        with self.plain_filesystem(), patch.dict(os.environ, self.binding(value), clear=True), self.assertRaises(ReviewError):
            compact_render._render_subtitle_scratch(self.context)

    def test_shared_native_kernel_passes_scratch_for_b1_b2_and_voice_only(self):
        import hashlib
        import engine.dubflow.media as media_package
        class ReachedRender(BaseException):
            pass
        for audio, text_changed in ((None, True), (self.root / "mix", True), (self.root / "mix", False)):
            with self.subTest(audio=audio, text_changed=text_changed), self.plain_filesystem(), contextlib.ExitStack() as stack:
                context = {**self.context, "burn_in_subtitles": True, "source": {"path": str(self.root / "source")},
                    "corrected_timeline_json": '{"cues":[{"confidence":0.8724939823150635}]}'}
                probe = SimpleNamespace(to_dict=lambda: {"source_path": str(self.root / "source")})
                manifest = {"source": {"probe": probe.to_dict()}}
                worker = ModuleType("engine.dubflow.worker.production_job")
                worker.TextCue = lambda **kw: kw
                worker._write_subtitles = unittest.mock.Mock(side_effect=lambda g, _: (g / "srt", g / "ass"))
                worker._source_render_duration = lambda _: None
                worker._validate_rendered_audio = lambda *_: None
                synthesis = ModuleType("engine.dubflow.review.synthesis_input")
                synthesis._digest = lambda raw: "sha256:" + hashlib.sha256(raw).hexdigest()
                synthesis._encode = lambda value, _: json.dumps(value, sort_keys=True).encode()
                writer = ModuleType("engine.dubflow.review.synthesis_worker")
                writer._writer = lambda _: contextlib.nullcontext()
                writer._atomic = unittest.mock.Mock()
                stack.enter_context(patch.dict(sys.modules, {worker.__name__: worker, synthesis.__name__: synthesis, writer.__name__: writer}))
                stack.enter_context(patch.dict(os.environ, self.binding(), clear=True))
                media = stack.enter_context(patch.object(media_package, "FfmpegMediaAdapter")).return_value
                prober = stack.enter_context(patch.object(media_package, "MediaProbe")).return_value
                media.ffmpeg_path = self.root / "ffmpeg"; prober.ffprobe_path = self.root / "ffprobe"
                media.render.side_effect = ReachedRender
                prober.probe.return_value = probe
                stack.enter_context(patch.object(Path, "exists", return_value=False))
                mkdir = stack.enter_context(patch.object(Path, "mkdir"))
                stack.enter_context(patch.object(compact_render, "_file_hash", return_value=("sha256:" + "a" * 64, 1)))
                stack.enter_context(patch.object(compact_render, "_inputs", return_value=audio))
                with self.assertRaises(ReachedRender):
                    compact_render._render_admitted(b"new mock context", output_root=self.root / "private",
                        ffmpeg_path=self.root / "ffmpeg", ffprobe_path=self.root / "ffprobe", media_root=self.root,
                        timeout_seconds=900, admission=lambda _: (context, manifest, None, None, text_changed),
                        recipe="new-mock-only", caption_reader=lambda *_: {"captions_srt": b"exact SRT\r\n", "captions_ass": b"exact ASS\r\n"})
                self.assertEqual(media.render.call_args.kwargs["subtitle_staging_dir"], self.scratch)
                self.assertEqual(media.render.call_args.kwargs["audio_path"], audio)
                self.assertEqual(media.render.call_args.args[0], self.root / "source")
                final = media.render.call_args.args[1]
                self.assertEqual(final.parent.parent, self.root / "private")
                writer._atomic.assert_any_call(final.parent / "timeline.json", context["corrected_timeline_json"].encode())
                self.assertTrue(mkdir.called)
                self.assertEqual(worker._write_subtitles.called, text_changed)


if __name__ == "__main__": unittest.main()
