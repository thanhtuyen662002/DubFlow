from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts import (TtsError, DeterministicFixtureEngine, LocalTtsAdapter,
    TtsConfig, TtsInput, TtsProvenance, approved_default_voice)
from engine.dubflow.worker.tts_checkpoints import TtsCheckpointStore
from engine.dubflow.worker.tts_rewrites import (BoundedDurationRewriteEngine,
    SpokenTextStore, plan_rewrite, rewrite_warning, _encoded)

INPUT = "sha256:" + "1" * 64
IDENTITY = "2" * 64
SEGMENTS = tuple(TtsInput("cue-" + str(index), "source-" + str(index), "Xin chào",
    TimePoint(index * 1200, TimeBase(1, 1000)), TimePoint(index * 1200 + 1000, TimeBase(1, 1000))) for index in range(2))


def adapter(root, engine):
    config = TtsConfig(max_attempts=1, max_segments_per_chunk=1)
    voice = approved_default_voice()
    provenance = TtsProvenance("rewrite-test", "1", "fixture", "stdlib", "timeline-v1",
        config.content_hash(), INPUT, voice.model_id, voice.model_version, voice.model_hash,
        voice.content_hash(), voice.voice_id, voice.voice_version, config.requested_profile, "fixture", config.resource)
    return LocalTtsAdapter(engine, config=config, voice=voice, provenance=provenance, output_dir=root)


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


def run(root, engine, cues=CUES):
    checkpoints = TtsCheckpointStore(root, identity=IDENTITY)
    spoken = SpokenTextStore(root, identity=IDENTITY, segments=cues)
    wrapped = BoundedDurationRewriteEngine(engine, spoken)
    def commit(value):
        spoken.commit(value)
        checkpoints.commit(value)
    document = adapter(root, wrapped).synthesize(cues, input_hash=INPUT,
        checkpoints=spoken.filter_checkpoints(checkpoints.load(cues)), on_checkpoint=commit)
    return document, spoken, checkpoints


class DurationRewriteTests(unittest.TestCase):
    def test_rule_keeps_pronouns_negation_numbers_predicates_and_punctuation(self):
        self.assertEqual(plan_rewrite(TEXT).spoken_text,
                         "Khi lần đầu chúng tôi gặp nhau, không phải ngày 8.")
        for value in ('Khi "lần đầu tiên" xuất hiện.', "Khi lần đầu tiên An đến.",
                      "Lần Đầu Tiên là tên phim.", "Khi lần đầu tiên rồi khi lần đầu tiên.",
                      "Chúng tôi không đến.", "lần đầu tiên", "Khi lần đầu tiên\nchúng tôi gặp.",
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
                self.assertEqual(len(engine.requests), 3 if options.get("corrupt") or code == "DURATION_FIT_REQUIRED" else 2)
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
