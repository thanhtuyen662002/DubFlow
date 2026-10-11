from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review import (LegacyReviewPayload, ReviewError, ReviewFinding, ReviewSnapshot,
                                  ReviewStore, build_review_snapshot, read_review_snapshot)
from engine.dubflow.review.wire import decode_review_json
from schema_assertions import assert_schema

ROOT = Path(__file__).resolve().parents[2]
CUES = (
    {"cue_id": "cue-a", "start_ms": 110724, "end_ms": 113884, "source_text": "It has shed",
     "translated_text": "Nó đã lột xác", "confidence": "0.9", "voice_id": "voice-a"},
    {"cue_id": "asr-1883904-1922944-6efeed1b9151", "start_ms": 117744, "end_ms": 120184,
     "source_text": "You're a fool", "translated_text": "Đồ ngốc", "confidence": "0.8", "voice_id": "voice-a"},
)
WARNING = "TTS_INTERCUE_GAP_USED: asr-1883904-1922944-6efeed1b9151; used reserved gap"


class VersionedReviewTests(unittest.TestCase):
    def snapshot(self):
        return build_review_snapshot("job-review", cues=CUES, warnings=(WARNING,))

    def store(self, directory):
        snapshot = self.snapshot()
        store = ReviewStore(Path(directory) / "review-store.json")
        store.initialize(snapshot)
        return store, snapshot

    def test_actual_emitted_snapshot_and_store_match_declared_schema(self):
        with TemporaryDirectory(prefix="dubflow-review-v2-") as directory:
            store, snapshot = self.store(directory)
            emitted = json.loads(json.dumps(snapshot.to_dict(), ensure_ascii=False))
            assert_schema(emitted, "schema-v2.json")
            with self.assertRaisesRegex(AssertionError, "extra fields|const mismatch"):
                assert_schema(emitted, "schema-v1.json")
            self.assertEqual(read_review_snapshot(emitted), snapshot)
            first, _ = store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="Đừng ngốc nữa")
            self.assertEqual(store.load(snapshot.job_id), first)
            assert_schema(json.loads(store.path.read_text(encoding="utf-8")), "store-v1.json")

    def test_released_v1_reads_without_invented_job_timebase_or_cue(self):
        value = self.snapshot().findings[0].to_dict()
        for name in ("cue_id", "warning", "confidence_source"):
            value.pop(name)
        value["source_text"] = ""  # Legal released v1 display values stay readable.
        value["range"]["start_ticks"] = "-1"
        payload = {"schema_version": 1, "findings": [value]}
        assert_schema(payload, "schema-v1.json")
        legacy = read_review_snapshot(payload)
        self.assertIsInstance(legacy, LegacyReviewPayload)
        self.assertEqual(legacy.to_dict(), payload)
        self.assertFalse(hasattr(legacy, "job_id"))
        with TemporaryDirectory(prefix="dubflow-review-v1-") as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            history = path.with_suffix(".json.history")
            history.write_text("retained historical evidence", encoding="utf-8")
            original = path.read_bytes(), history.read_bytes()
            with self.assertRaisesRegex(ReviewError, "display-only"):
                ReviewStore(path).load("job-review")
            self.assertEqual((path.read_bytes(), history.read_bytes()), original)

    def test_unknown_versions_and_malformed_old_envelope_fail_closed(self):
        with self.assertRaisesRegex(ReviewError, "unsupported"):
            read_review_snapshot({"schema_version": 99, "findings": []})
        with self.assertRaisesRegex(ReviewError, "unsupported fields"):
            read_review_snapshot({"schema_version": 1, "findings": [], "job_id": "old", "revision": 0, "last_plan": None})
        with self.assertRaises(ReviewError):
            read_review_snapshot({"schema_version": True, "findings": []})

    def test_sparse_reordered_mixed_warnings_bind_by_explicit_cue_identity(self):
        global_warning = "QC_WARNING: source loudness advisory"
        first = build_review_snapshot("job-review", cues=CUES, warnings=(global_warning, WARNING))
        second = build_review_snapshot("job-review", cues=CUES, warnings=(WARNING, global_warning))
        self.assertEqual({item.finding_id: item for item in first.findings}, {item.finding_id: item for item in second.findings})
        bound = next(item for item in first.findings if item.cue_id is not None)
        self.assertEqual((bound.cue_id, bound.start_ticks, bound.end_ticks, bound.source_text),
                         (CUES[1]["cue_id"], "117744", "120184", "You're a fool"))
        global_item = next(item for item in first.findings if item.cue_id is None)
        self.assertIsNone(global_item.start_ticks)
        self.assertIsNone(global_item.to_dict()["range"])
        self.assertIsNone(global_item.confidence)

    def test_structured_warning_binding_and_unknown_identity(self):
        snapshot = build_review_snapshot("job-review", cues=CUES,
            warnings=({"code": "TRANSLATION_ANOMALY", "message": "Check phrase", "cue_id": CUES[1]["cue_id"]},))
        self.assertEqual(snapshot.findings[0].cue_id, CUES[1]["cue_id"])
        with self.assertRaisesRegex(ReviewError, "unknown cue_id"):
            build_review_snapshot("job-review", cues=CUES,
                warnings=({"code": "TTS_FAILURE", "message": "failed", "cue_id": "missing"},))

    def test_actual_confidence_and_unknown_confidence_remain_distinct(self):
        low = {**CUES[0], "confidence": "0.1"}
        snapshot = build_review_snapshot("job-review", cues=(low,), warnings=("QC_WARNING: no measurement",))
        measured = next(item for item in snapshot.findings if item.cue_id is not None)
        self.assertEqual((measured.confidence, measured.confidence_source), ("0.1", "cue.confidence"))
        self.assertEqual((snapshot.findings[0].confidence, snapshot.findings[0].confidence_source), (None, None))
        assert_schema(snapshot.to_dict(), "schema-v2.json")
        for invalid in (True, float("nan"), float("inf"), -0.1, "0.1x"):
            with self.subTest(invalid=invalid), self.assertRaises(ReviewError):
                build_review_snapshot("job-review", cues=({**low, "confidence": invalid},))

    def test_exact_integer_timebase_rescaling_and_inexact_refusal(self):
        snapshot = build_review_snapshot("job-review", cues=CUES, warnings=(WARNING,), time_base=("1", "48000"))
        self.assertEqual(snapshot.findings[0].start_ticks, str(117744 * 48))
        self.assertEqual(read_review_snapshot(snapshot.to_dict()).time_base, ("1", "48000"))
        with self.assertRaisesRegex(ReviewError, "exactly"):
            build_review_snapshot("job-review", cues=CUES, warnings=(WARNING,), time_base=("1", "24"))
        with self.assertRaisesRegex(ReviewError, "reduced"):
            replace(snapshot, time_base=("2", "96000"))

    def test_global_findings_cannot_authorize_a_random_cue_edit(self):
        snapshot = build_review_snapshot("job-review", warnings=("QC_WARNING: advisory",))
        with TemporaryDirectory(prefix="dubflow-review-global-") as directory:
            store = ReviewStore(Path(directory) / "review.json")
            store.initialize(snapshot)
            original = store.path.read_bytes()
            with self.assertRaisesRegex(ReviewError, "global/unmapped"):
                store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="Wrong cue")
            self.assertEqual(store.path.read_bytes(), original)

    def test_two_corrections_two_undos_restore_content_with_monotonic_revision(self):
        with TemporaryDirectory(prefix="dubflow-review-undo-") as directory:
            store, original = self.store(directory)
            finding_id = original.findings[0].finding_id
            first, _ = store.commit_correction(original, finding_id, translation="First")
            second, _ = store.commit_correction(first, finding_id, voice="voice-b")
            undo_one = store.undo(second)
            self.assertEqual(undo_one.findings, first.findings)
            self.assertEqual(undo_one.last_plan, first.last_plan)
            self.assertEqual(undo_one.revision, 3)
            undo_two = store.undo(undo_one)
            self.assertEqual(undo_two.findings, original.findings)
            self.assertIsNone(undo_two.last_plan)
            self.assertEqual(undo_two.revision, 4)
            with self.assertRaisesRegex(ReviewError, "changed"):
                store.commit_correction(original, finding_id, translation="Stale ABA edit")
            with self.assertRaisesRegex(ReviewError, "no review"):
                store.undo(undo_two)
            self.assertEqual(json.loads(store.path.read_text(encoding="utf-8"))["undo"], [])

    def test_replace_failure_preserves_current_and_undo_stack_then_retry_succeeds(self):
        with TemporaryDirectory(prefix="dubflow-review-fault-") as directory:
            store, original = self.store(directory)
            first, _ = store.commit_correction(original, original.findings[0].finding_id, translation="First")
            before = store.path.read_bytes()
            with patch("engine.dubflow.review.store.os.replace", side_effect=OSError("injected replace failure")):
                with self.assertRaisesRegex(OSError, "injected"):
                    store.undo(first)
                with self.assertRaisesRegex(OSError, "injected"):
                    store.commit_correction(first, first.findings[0].finding_id, voice="voice-b")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(ReviewStore(store.path).load(original.job_id), first)
            self.assertEqual(list(store.path.parent.glob("*.partial")), [])
            restored = store.undo(first)
            self.assertEqual(restored.findings, original.findings)

    def test_writer_lock_refuses_competing_handle_without_mutation(self):
        with TemporaryDirectory(prefix="dubflow-review-lock-") as directory:
            store, original = self.store(directory)
            before = store.path.read_bytes()
            with store._writer(), self.assertRaisesRegex(ReviewError, "busy"):
                ReviewStore(store.path).commit_correction(original, original.findings[0].finding_id, translation="Competing")
            self.assertEqual(store.path.read_bytes(), before)
            updated, _ = store.commit_correction(original, original.findings[0].finding_id, translation="After lock release")
            self.assertEqual(updated.revision, 1)

    def test_process_death_releases_os_writer_lock(self):
        with TemporaryDirectory(prefix="dubflow-review-lock-death-") as directory:
            path = Path(directory) / "review.json"
            code = ("import os,sys; sys.path.insert(0," + repr(str(ROOT)) + "); "
                    "from engine.dubflow.review import ReviewStore; "
                    "store=ReviewStore(" + repr(str(path)) + "); "
                    "guard=store._writer(); guard.__enter__(); os._exit(0)")
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], timeout=10, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            store = ReviewStore(path)
            store.initialize(self.snapshot())
            self.assertEqual(store.load("job-review"), self.snapshot())

    def test_undo_bound_and_store_byte_limit_do_not_discard_prior_history(self):
        with TemporaryDirectory(prefix="dubflow-review-bound-") as directory:
            store, snapshot = self.store(directory)
            with patch("engine.dubflow.review.store.MAX_UNDO", 1):
                first, _ = store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="First")
                before = store.path.read_bytes()
                with self.assertRaisesRegex(ReviewError, "depth limit"):
                    store.commit_correction(first, first.findings[0].finding_id, voice="voice-b")
            with patch("engine.dubflow.review.store.MAX_STORE_BYTES", 1), self.assertRaisesRegex(ReviewError, "byte limit"):
                store.commit_correction(first, first.findings[0].finding_id, voice="voice-b")
            self.assertEqual(store.path.read_bytes(), before)

    def test_duplicate_json_keys_and_corrupt_store_versions_refuse(self):
        with self.assertRaisesRegex(ReviewError, "duplicate"):
            decode_review_json('{"schema_version":1,"schema_version":2}')
        with TemporaryDirectory(prefix="dubflow-review-corrupt-") as directory:
            store, snapshot = self.store(directory)
            document = json.loads(store.path.read_text(encoding="utf-8"))
            document["store_version"] = 2
            store.path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ReviewError, "unsupported"):
                store.load(snapshot.job_id)

    def test_same_revision_tampered_client_and_cross_job_undo_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-stale-") as directory:
            store, snapshot = self.store(directory)
            changed = replace(snapshot, findings=(replace(snapshot.findings[0], translated_text="Client changed"),))
            with self.assertRaisesRegex(ReviewError, "changed"):
                store.commit_correction(changed, changed.findings[0].finding_id, translation="New")
            with self.assertRaisesRegex(ReviewError, "another job"):
                store.undo(replace(snapshot, job_id="another-job"))

    def test_schema_mutations_and_noncanonical_identity_refuse(self):
        snapshot = self.snapshot()
        mutations = (("revision", True), ("job_id", " job-review "), ("schema_version", 99),
                     ("time_base", {"numerator": "1", "denominator": 1000}))
        for name, value in mutations:
            payload = {**snapshot.to_dict(), name: value}
            with self.subTest(name=name), self.assertRaises(AssertionError):
                assert_schema(payload, "schema-v2.json")
            with self.subTest(reader=name), self.assertRaises(ReviewError):
                read_review_snapshot(payload)
        with self.assertRaises(ReviewError):
            replace(snapshot.findings[0], finding_id=" cue-a ")
        with self.assertRaises(ReviewError):
            replace(snapshot.findings[0], start_ticks="1" * 5000)

    def test_no_duplicate_diagnostic_overwrites_a_different_finding(self):
        item = self.snapshot().findings[0]
        with self.assertRaisesRegex(ReviewError, "conflicting duplicate"):
            build_review_snapshot("job-review", findings=(item, replace(item, translated_text="Different")))
        snapshot = build_review_snapshot("job-review", cues=CUES, warnings=(WARNING, WARNING))
        self.assertEqual(len(snapshot.findings), 1)


if __name__ == "__main__":
    unittest.main()
