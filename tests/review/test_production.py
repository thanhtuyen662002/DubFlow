from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review import ReviewError, ReviewStore, build_review_snapshot


class ProductionReviewTests(unittest.TestCase):
    def test_snapshot_maps_warnings_to_tick_bound_findings(self) -> None:
        snapshot = build_review_snapshot(
            "job-review",
            cues=({"start_ms": 120, "end_ms": 900, "source_text": "Hello", "translated_text": "Xin chào", "confidence": 0.3},),
            warnings=("B2_AUDIO_FALLBACK_TO_B1: voice unavailable", "OCR_TEXT_REVIEW: uncertain"),
        )
        self.assertEqual(snapshot.revision, 0)
        self.assertEqual(len(snapshot.findings), 3)
        self.assertEqual(snapshot.findings[0].start_ticks, "120")
        self.assertIn(snapshot.findings[0].reason, {"TTS_FAILURE", "ASR_OCR_CONFLICT"})

    def test_correction_is_atomic_targeted_and_undoable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store = ReviewStore(Path(directory) / "review.json")
            snapshot = build_review_snapshot("job-review", warnings=("TTS_FAILURE: cue failed",))
            store._atomic_write(snapshot.to_dict())
            updated, plan = store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="Xin chào", voice="vi-builtin-v1")
            self.assertEqual(updated.revision, 1)
            self.assertTrue(plan.retain_previous_until_validated)
            self.assertIn("tts", plan.stages_to_rerun)
            self.assertEqual(store.load("job-review").findings[0].voice, "vi-builtin-v1")
            restored = store.undo("job-review")
            self.assertEqual(restored.revision, 0)
            self.assertIsNone(restored.findings[0].voice)

    def test_revision_conflict_and_invalid_ticks_fail_closed(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store = ReviewStore(Path(directory) / "review.json")
            snapshot = build_review_snapshot("job-review", warnings=("QC_WARNING: sync",))
            store._atomic_write(snapshot.to_dict())
            updated, _ = store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="ok")
            with self.assertRaisesRegex(ReviewError, "changed"):
                store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="stale")
            self.assertEqual(updated.revision, 1)
            with self.assertRaises(ReviewError):
                # Direct constructor validation covers non-canonical negative ticks.
                from engine.dubflow.review.production import ReviewFinding
                ReviewFinding("bad", "SYNC_QC_WARNING", "-1", "2", "a", "b", None, None, None, "0.5", ("qc",))


if __name__ == "__main__":
    unittest.main()
