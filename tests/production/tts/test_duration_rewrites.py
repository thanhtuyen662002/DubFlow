from dataclasses import replace
from hashlib import sha256
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts import (TtsError, DeterministicFixtureEngine, LocalTtsAdapter,
    TtsConfig, TtsInput, TtsProvenance, approved_default_voice)
from engine.dubflow.worker.tts_checkpoints import TtsCheckpointStore
from engine.dubflow.worker.tts_rewrites import (BoundedDurationRewriteEngine,
    SpokenTextStore, plan_rewrite, rewrite_warning, validate_spoken_text_document, _encoded)

INPUT = "sha256:" + "1" * 64
IDENTITY = "2" * 64
SEGMENTS = tuple(TtsInput("cue-" + str(index), "source-" + str(index), "Xin chào",
    TimePoint(index * 1200, TimeBase(1, 1000)), TimePoint(index * 1200 + 1000, TimeBase(1, 1000))) for index in range(2))


def adapter(root, engine, **kwargs):
    config = TtsConfig(max_attempts=1, max_segments_per_chunk=1)
    voice = approved_default_voice()
    provenance = TtsProvenance("rewrite-test", "1", "fixture", "stdlib", "timeline-v1",
        config.content_hash(), INPUT, voice.model_id, voice.model_version, voice.model_hash,
        voice.content_hash(), voice.voice_id, voice.voice_version, config.requested_profile, "fixture", config.resource)
    return LocalTtsAdapter(engine, config=config, voice=voice, provenance=provenance, output_dir=root, **kwargs)


TEXT = "Khi lần đầu tiên chúng tôi gặp nhau, không phải ngày 8."
CUES = (replace(SEGMENTS[0], text=TEXT, normalized_text=None), SEGMENTS[1])


class DurationFixture(DeterministicFixtureEngine):
    """Fault seam only; not actual speech or semantic qualification."""
    def __init__(self, failure=None, corrupt=False, healthy=False):
        super().__init__()
        self.failure, self.corrupt, self.healthy = failure, corrupt, healthy
        self.requests = []

    def synthesize(self, request):
        self.requests.append(request)
        if request.segment.segment_id == CUES[0].segment_id and not self.healthy:
            if self.failure and self.failure != "DURATION_FIT_REQUIRED":
                raise TtsError(self.failure, "native complete-speech/model refusal")
            if "lần đầu tiên" in request.segment.text or self.failure:
                raise TtsError("DURATION_FIT_REQUIRED", "measured speech exceeds its window")
        result = super().synthesize(request)
        if self.corrupt and request.segment.segment_id == CUES[0].segment_id:
            result = replace(result, audio_bytes=b"not-a-waveform")
        return result


def run(root, engine, cues=CUES, *, limits=None):
    checkpoints = TtsCheckpointStore(root, identity=IDENTITY)
    spoken = SpokenTextStore(root, identity=IDENTITY, segments=cues)
    wrapped = BoundedDurationRewriteEngine(engine, spoken, render_window_limits=limits)
    def commit(value):
        spoken.commit(value)
        checkpoints.commit(value)
    document = adapter(root, wrapped, render_window_limits=limits, allow_fast_fit=True).synthesize(cues, input_hash=INPUT,
        checkpoints=spoken.filter_checkpoints(checkpoints.load(cues)), on_checkpoint=commit)
    return document, spoken, checkpoints


class DurationRewriteTests(unittest.TestCase):
    def test_all_safe_rules_apply_in_one_plan_and_protected_text_is_retained(self):
        examples = (
            ("Thực ra thì bây giờ tôi rất là vui nhé!", "Thực ra giờ tôi rất vui!"),
            ("À thì ừm, tôi kiểu như gặp bạn lần đầu tiên mà.", "tôi gặp bạn lần đầu."),
            ("Nhưng mà tại vì trời mưa cho nên tôi chưa đi nha.", "Nhưng vì trời mưa nên tôi chưa đi."),
            ("Tôi không biết như thế nào nhỉ?", "Tôi không biết thế nào?"),
            ("Tôi có một cái cảm giác rất là lạ.", "Tôi có cái cảm giác rất lạ."),
            ("Anh ấy thì là bác sĩ.", "Anh ấy là bác sĩ."),
            ("Khi lần đầu tiên rồi khi lần đầu tiên.", "Khi lần đầu rồi khi lần đầu."),
        )
        for original, expected in examples:
            with self.subTest(original=original):
                plan = plan_rewrite(original)
                self.assertEqual(plan.spoken_text, expected)
                self.assertTrue(plan.rules)
                self.assertIsNone(plan_rewrite(expected))
        for original in ("Tôi không đi.", "Đừng đi.", "Tôi chẳng biết.", "Tôi chưa đến.",
                         "Tôi có một cái bánh.", "Tôi có 12 cái bánh.", "Tôi ở đó.",
                         "Tôi nghĩ vậy.", "Đừng làm thế!", "Tôi không muốn vậy.",
                         "Anh sửa động cơ.", "Tôi thấy nguy cơ.", "Tôi giữ tư thế.",
                         "Tôi muốn thay thế.", "Tôi đến hôm đó.", "Tôi ở trên đấy.",
                         'Tôi nói "bây giờ nhé".', "Khi bây giờ An đến nhé.",
                         "Bây Giờ là tên phim.", "Thì là", "ờ"):
            with self.subTest(original=original):
                self.assertIsNone(plan_rewrite(original))
        for negation in ("không", "chẳng", "chưa", "đừng"):
            plan = plan_rewrite(f"Bây giờ {negation} lấy 2 cái bánh nhé.")
            self.assertEqual(plan.spoken_text, f"Giờ {negation} lấy 2 cái bánh.")

    def test_rule_keeps_pronouns_negation_numbers_predicates_and_punctuation(self):
        self.assertEqual(plan_rewrite(TEXT).spoken_text,
                         "Khi lần đầu chúng tôi gặp nhau, không phải ngày 8.")
        for value in ('Khi "lần đầu tiên" xuất hiện.', "Khi lần đầu tiên An đến.",
                      "Lần Đầu Tiên là tên phim.",
                      "Chúng tôi không đến.", "Khi lần đầu tiên\nchúng tôi gặp.",
                      "Khi lần đầu tiênxyz xuất hiện.", "Khi lần đầu tiên" + "x" * 512):
            with self.subTest(value=value):
                self.assertIsNone(plan_rewrite(value))

    def test_healthy_original_is_kept_without_a_rewrite_attempt(self):
        with TemporaryDirectory() as directory:
            engine = DurationFixture(healthy=True)
            doc, store, _ = run(Path(directory), engine)
            self.assertEqual(len(engine.requests), 2)
            self.assertEqual(engine.requests[0].segment.text, TEXT)
            self.assertFalse(any(w.startswith("TTS_DURATION_REWRITE") for w in doc.warnings))
            self.assertTrue(all(entry["mode"] == "original" for entry in store.document(doc)["entries"]))

    def test_healthy_waveform_request_and_artifact_identities_match_previous_wrapper(self):
        class LegacyWrapper(BoundedDurationRewriteEngine):
            def synthesize(self, request):
                return self.engine.synthesize(request)
        # Match the prior wrapper's fingerprint: successful speech went straight
        # through this class, with no changed config, window, warning or text.
        LegacyWrapper.__module__ = BoundedDurationRewriteEngine.__module__
        LegacyWrapper.__qualname__ = BoundedDurationRewriteEngine.__qualname__
        for speed in (1000, 1200, 1300):
            with self.subTest(speed=speed), TemporaryDirectory() as directory:
                class Healthy(DurationFixture):
                    def synthesize(self, request):
                        result = super().synthesize(request)
                        return replace(result, fit_mode="speed_adjusted" if speed != 1000 else "native", speed_ratio_milli=speed)
                cues = tuple(replace(cue, render_window_end=cue.end) for cue in CUES)
                root = Path(directory)
                store = SpokenTextStore(root, identity=IDENTITY, segments=cues)
                before_engine = Healthy(healthy=True)
                before = adapter(root, LegacyWrapper(before_engine, store)).synthesize(cues, input_hash=INPUT)
                after_engine = Healthy(healthy=True)
                after, _, _ = run(root, after_engine, cues)
                self.assertEqual(before_engine.requests, after_engine.requests)
                self.assertEqual(before.artifacts, after.artifacts)

    def test_window_then_condensation_then_fast_tier_and_recovery(self):
        cue = replace(CUES[0], render_window_end=TimePoint(1000, TimeBase(1, 1000)))
        limit = TimePoint(2000, TimeBase(1, 1000))
        # Natural original/condensed duration in ms. Each row reaches exactly
        # one more rung; the final row exceeds even the 1.5x bound.
        for original, condensed, tiers, speed in ((2200, 2000, 2, 1100),
                (3000, 2400, 3, 1200), (3600, 2800, 4, 1400), (3600, 3000, 4, 1500),
                (3600, 3001, 4, None)):
            with self.subTest(original=original, condensed=condensed), TemporaryDirectory() as directory:
                class TimedEngine(DeterministicFixtureEngine):
                    def __init__(self):
                        super().__init__()
                        self.requests = []
                    def synthesize(self, request):
                        self.requests.append(request)
                        if request.segment.segment_id != cue.segment_id:
                            return super().synthesize(request)
                        duration = original if request.segment.text == TEXT else condensed
                        target = request.segment.render_window_end.ticks - request.segment.start.ticks
                        speed_milli = max(1000, math.ceil(duration * 1000 / target))
                        if speed_milli > request.config.max_speed_ratio_milli:
                            raise TtsError("DURATION_FIT_REQUIRED", "complete speech cannot fit")
                        frames = max(request.sample_end - request.sample_start,
                                     math.ceil(duration * request.config.sample_rate / speed_milli))
                        result = super().synthesize(replace(request, sample_end=request.sample_start + frames))
                        return replace(result, fit_mode="speed_adjusted", speed_ratio_milli=speed_milli)
                root, engine = Path(directory), TimedEngine()
                cues = (cue, replace(CUES[1], start=TimePoint(3000, TimeBase(1, 1000)),
                                    end=TimePoint(4000, TimeBase(1, 1000)),
                                    render_window_end=TimePoint(4000, TimeBase(1, 1000))))
                doc, store, _ = run(root, engine, cues, limits={cue.segment_id: limit})
                requests = [item for item in engine.requests if item.segment.segment_id == cue.segment_id]
                self.assertEqual(len(requests), tiers)
                self.assertEqual([r.config.max_speed_ratio_milli for r in requests],
                                 [1300] * min(tiers, 3) + ([1500] if tiers == 4 else []))
                self.assertEqual([r.segment.text for r in requests],
                                 [TEXT] * 2 + ([plan_rewrite(TEXT).spoken_text] * (tiers - 2)))
                if speed is None:
                    self.assertEqual([item.segment_id for item in doc.artifacts], [cues[1].segment_id])
                    self.assertEqual(doc.failures[0].code, "DURATION_FIT_REQUIRED")
                    continue
                artifact = doc.artifacts[0]
                self.assertEqual(artifact.speed_ratio_milli, speed)
                self.assertEqual(artifact.render_window_end, limit)
                self.assertTrue(any(w.startswith("TTS_RENDER_WINDOW_WIDENED:") for w in artifact.warnings))
                self.assertEqual(any(w.startswith("TTS_FAST_FIT:") for w in artifact.warnings), tiers == 4)
                receipt = store.document(doc)
                validate_spoken_text_document(receipt, doc.to_bytes(), cues, identity=IDENTITY)
                self.assertEqual(receipt["entries"][0]["fit"]["speed_ratio_milli"], speed)
                before = Path(artifact.path).read_bytes(), Path(artifact.path).stat().st_mtime_ns
                fresh = TimedEngine()
                resumed, _, _ = run(root, fresh, cues, limits={cue.segment_id: limit})
                self.assertEqual(fresh.requests, [])
                self.assertEqual(resumed.artifacts, doc.artifacts)
                self.assertEqual((Path(artifact.path).read_bytes(), Path(artifact.path).stat().st_mtime_ns), before)

    def test_fast_tier_also_runs_when_no_condensation_or_wider_window_is_available(self):
        class FastEngine(DurationFixture):
            def synthesize(self, request):
                if request.segment.segment_id == SEGMENTS[0].segment_id and request.config.max_speed_ratio_milli < 1500:
                    self.requests.append(request)
                    raise TtsError("DURATION_FIT_REQUIRED", "needs the faster tier")
                result = super().synthesize(request)
                return replace(result, fit_mode="speed_adjusted", speed_ratio_milli=1500) if request.segment.segment_id == SEGMENTS[0].segment_id else result
        with TemporaryDirectory() as directory:
            engine = FastEngine(healthy=True)
            doc, _, _ = run(Path(directory), engine, SEGMENTS)
            self.assertEqual(len(engine.requests), 3)
            self.assertIn("TTS_FAST_FIT: cue-0; speed 1500", doc.artifacts[0].warnings)

    def test_changed_text_retry_keeps_source_identity_and_validated_audio_binding(self):
        with TemporaryDirectory() as directory:
            engine = DurationFixture()
            doc, store, _ = run(Path(directory), engine)
            self.assertEqual(len(engine.requests), 3)
            original, rewritten = engine.requests[:2]
            self.assertEqual(original.segment, CUES[0])
            self.assertEqual(rewritten.segment.text, plan_rewrite(TEXT).spoken_text)
            self.assertEqual(rewritten.segment.normalized_text, rewritten.segment.text)
            self.assertEqual((original.segment.start, original.segment.end, original.segment.confidence),
                             (rewritten.segment.start, rewritten.segment.end, rewritten.segment.confidence))
            self.assertEqual((original.voice, original.config, original.input_hash),
                             (rewritten.voice, rewritten.config, rewritten.input_hash))
            self.assertFalse(doc.failures)
            artifact = doc.artifacts[0]
            self.assertIn(rewrite_warning(CUES[0].segment_id), artifact.warnings)
            evidence = store.document(doc)
            self.assertEqual(evidence["tts_document_hash"], "sha256:" + sha256(doc.to_bytes()).hexdigest())
            self.assertEqual(evidence["entries"][0]["original_input"], CUES[0].to_dict())
            self.assertEqual(evidence["entries"][0]["audio_hash"], artifact.content_hash)
            self.assertEqual(TEXT, CUES[0].text)

    def test_refused_or_corrupt_rewrite_preserves_neighboring_cue(self):
        for options, code in (({"failure": "DURATION_FIT_REQUIRED"}, "DURATION_FIT_REQUIRED"),
                              ({"corrupt": True}, "AUDIO_CORRUPT"),
                              ({"failure": "TTS_INCOMPLETE_SPEECH"}, "TTS_INCOMPLETE_SPEECH"),
                              ({"failure": "TTS_MODEL_LOAD_FAILED"}, "TTS_MODEL_LOAD_FAILED")):
            with self.subTest(options=options), TemporaryDirectory() as directory:
                engine = DurationFixture(**options)
                doc, store, _ = run(Path(directory), engine)
                self.assertEqual([a.segment_id for a in doc.artifacts], [CUES[1].segment_id])
                self.assertTrue(any(f.code == code for f in doc.failures))
                self.assertEqual(len(engine.requests), 4 if code == "DURATION_FIT_REQUIRED" else 3 if options.get("corrupt") else 2)
                self.assertEqual([e["original_input"]["segment_id"] for e in store.document(doc)["entries"]], [CUES[1].segment_id])

    def test_valid_receipts_resume_without_inference_and_keep_rewrite_advisory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original, _, _ = run(root, DurationFixture())
            before = [(Path(a.path).read_bytes(), Path(a.path).stat().st_mtime_ns) for a in original.artifacts]
            engine = DurationFixture()
            resumed, store, _ = run(root, engine)
            self.assertEqual(engine.requests, [])
            self.assertEqual(resumed.artifacts, original.artifacts)
            self.assertIn(rewrite_warning(CUES[0].segment_id), resumed.artifacts[0].warnings)
            self.assertEqual([(Path(a.path).read_bytes(), Path(a.path).stat().st_mtime_ns) for a in resumed.artifacts], before)
            self.assertEqual(store.document(resumed)["entries"][0]["mode"], "rewritten")

    def test_receipt_tampering_reprocesses_only_affected_cue_even_with_recomputed_hash(self):
        for corruption in ("text", "recipe", "ticks", "audio", "missing", "duplicate", "oversized"):
            with self.subTest(corruption=corruption), TemporaryDirectory() as directory:
                root = Path(directory)
                original, store, _ = run(root, DurationFixture())
                neighbor = Path(original.artifacts[1].path)
                before = neighbor.read_bytes(), neighbor.stat().st_mtime_ns
                path = store._path(CUES[0].segment_id)
                record = json.loads(path.read_bytes())
                if corruption == "missing":
                    path.unlink()
                elif corruption == "oversized":
                    path.write_bytes(b" " * 65537)
                elif corruption == "duplicate":
                    path.write_bytes(b'{"mode":"rewritten",' + path.read_bytes()[1:])
                else:
                    if corruption == "text":
                        record["spoken_text"] = record["spoken_text"].replace("chúng tôi", "chúng ta")
                        record["spoken_text_hash"] = "sha256:" + sha256(record["spoken_text"].encode()).hexdigest()
                    elif corruption == "recipe": record["recipe_hash"] = "sha256:" + "0" * 64
                    elif corruption == "ticks": record["original_input"]["start"]["ticks"] = "7"
                    else: record["audio_hash"] = "sha256:" + "0" * 64
                    record.pop("record_hash")
                    record["record_hash"] = sha256(_encoded(record)).hexdigest()
                    path.write_bytes(_encoded(record))
                engine = DurationFixture()
                resumed, new_store, _ = run(root, engine)
                self.assertEqual([r.segment.segment_id for r in engine.requests], [CUES[0].segment_id] * 2)
                self.assertEqual((neighbor.read_bytes(), neighbor.stat().st_mtime_ns), before)
                self.assertTrue(new_store.warnings)
                self.assertEqual(len(resumed.artifacts), 2)
                self.assertEqual(new_store.document(resumed)["entries"][0]["spoken_text"], plan_rewrite(TEXT).spoken_text)

    def test_orphan_receipt_never_reuses_speech_without_checkpoint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(TtsCheckpointStore, "commit", side_effect=OSError("interrupted checkpoint")):
                with self.assertRaises(TtsError) as caught:
                    run(root, DurationFixture())
                self.assertEqual(caught.exception.code, "TTS_CHECKPOINT_WRITE_FAILED")
            self.assertEqual(len(list((root / "spoken_text").glob("*.json"))), 1)
            self.assertEqual(list((root / "checkpoints").glob("*.json")), [])
            engine = DurationFixture()
            resumed, store, _ = run(root, engine)
            self.assertEqual(len(engine.requests), 3)
            self.assertEqual(len(resumed.artifacts), 2)
            self.assertEqual(len(store.document(resumed)["entries"]), 2)
