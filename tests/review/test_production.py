from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review import ReviewError, ReviewFinding, ReviewSnapshot, ReviewStore, build_review_snapshot


class ProductionReviewTests(unittest.TestCase):
    def initialized(self, directory: str, reason: str):
        # The diagnostic deliberately names a different root than the edit.
        finding = ReviewFinding("cue-a", reason, "1000", "2000", "Hello", "Original",
                                "speaker-a", "voice-a", None, "0.3", ("render", "qc"), cue_id="cue-a")
        snapshot = ReviewSnapshot("job-review", 0, (finding,))
        store = ReviewStore(Path(directory) / "review.json")
        store.initialize(snapshot)
        return store, snapshot

    def test_translation_edit_on_tts_failure_invalidates_subtitles_qc_and_export(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store, snapshot = self.initialized(directory, "TTS_FAILURE")
            updated, plan = store.commit_correction(snapshot, "cue-a", translation="Corrected")
            self.assertEqual(plan.stages_to_rerun, ("translation", "subtitles", "tts", "mix", "render", "qc", "export"))
            self.assertEqual(plan.preserved_artifacts, ("asr", "ocr"))
            self.assertNotIn("review:cue-a", plan.preserved_artifacts)
            self.assertEqual(updated.findings[0].source_text, snapshot.findings[0].source_text)

    def test_voice_edit_on_translation_warning_preserves_translation_and_subtitles(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store, snapshot = self.initialized(directory, "TRANSLATION_ANOMALY")
            updated, plan = store.commit_correction(snapshot, "cue-a", voice="voice-b")
            self.assertEqual(plan.stages_to_rerun, ("tts", "mix", "render", "qc", "export"))
            self.assertEqual(updated.findings[0].translated_text, snapshot.findings[0].translated_text)
            self.assertTrue(plan.retain_previous_until_validated)

    def test_combined_edit_has_one_ordered_downstream_closure(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store, snapshot = self.initialized(directory, "SYNC_QC_WARNING")
            _, plan = store.commit_correction(snapshot, "cue-a", translation="Corrected", voice="voice-b")
            self.assertEqual(plan.stages_to_rerun, ("translation", "subtitles", "tts", "mix", "render", "qc", "export"))
            self.assertEqual(len(set(plan.replacement_artifacts)), len(plan.replacement_artifacts))
            self.assertFalse(plan.requires_full_manual_review)

    def test_noop_correction_is_refused_without_new_revision_or_history(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store, snapshot = self.initialized(directory, "TRANSLATION_ANOMALY")
            before = store.path.read_bytes()
            with self.assertRaisesRegex(ReviewError, "must change"):
                store.commit_correction(snapshot, "cue-a", translation="  Original  ", voice="voice-a")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertFalse(store.history_path.exists())

    def test_snapshot_maps_warnings_to_tick_bound_findings(self) -> None:
        snapshot = build_review_snapshot(
            "job-review",
            cues=({"cue_id": "cue-a", "start_ms": 120, "end_ms": 900, "source_text": "Hello", "translated_text": "Xin chào", "confidence": 0.3},),
            warnings=("B2_AUDIO_FALLBACK_TO_B1: voice unavailable", "OCR_TEXT_REVIEW: uncertain"),
        )
        self.assertEqual(snapshot.revision, 0)
        self.assertEqual(len(snapshot.findings), 3)
        self.assertIsNone(snapshot.findings[0].start_ticks)
        self.assertEqual(snapshot.findings[2].start_ticks, "120")
        self.assertIn(snapshot.findings[0].reason, {"TTS_FAILURE", "ASR_OCR_CONFLICT"})

    def test_correction_is_atomic_targeted_and_undoable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store = ReviewStore(Path(directory) / "review.json")
            store, snapshot = self.initialized(directory, "TTS_FAILURE")
            updated, plan = store.commit_correction(snapshot, snapshot.findings[0].finding_id, translation="Xin chào", voice="vi-builtin-v1")
            self.assertEqual(updated.revision, 1)
            self.assertTrue(plan.retain_previous_until_validated)
            self.assertIn("tts", plan.stages_to_rerun)
            self.assertEqual(store.load("job-review").findings[0].voice, "vi-builtin-v1")
            restored = store.undo(updated)
            self.assertEqual(restored.revision, 2)
            self.assertEqual(restored.findings, snapshot.findings)

    def test_revision_conflict_and_invalid_ticks_fail_closed(self) -> None:
        with TemporaryDirectory(prefix="dubflow-review-") as directory:
            store = ReviewStore(Path(directory) / "review.json")
            store, snapshot = self.initialized(directory, "SYNC_QC_WARNING")
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
