from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review.corrected_timeline import derive_corrected_timeline
from engine.dubflow.review.production import ReviewError, ReviewFinding, ReviewSnapshot
from engine.dubflow.review.store import ReviewStore


class CorrectedTimelineTests(unittest.TestCase):
    def fixture(self, root, *, b1=False, time_base=("1", "1000")):
        factor = 48 if time_base == ("1", "48000") else 1
        selected = ReviewFinding("risk-a", "TRANSLATION_ANOMALY", str(1000 * factor), str(2000 * factor),
            "Hello", "Xin chào", None, None if b1 else "voice-a", None, None, ("render", "qc"), cue_id="cue-a")
        related = replace(selected, finding_id="risk-b", reason="SYNC_QC_WARNING", warning="Timing risk")
        other = replace(selected, finding_id="risk-c", cue_id="cue-b", start_ticks=str(3000 * factor),
                        end_ticks=str(4000 * factor), source_text="Goodbye", translated_text="Tạm biệt")
        before = ReviewSnapshot("job-timeline", 0, (selected, related, other), time_base=time_base)
        store = ReviewStore(Path(root) / "review.json")
        store.initialize(before)
        timeline = {"schema_version": 1, "time_base": {"numerator": 1, "denominator": 1000},
            "duration_ticks": 14400000, "source_clock_mapping": {"recipe": "source-video-origin-v1", "start_pts": "9000"},
            "cues": [
                {"cue_id": "cue-a", "start_ms": 1000, "end_ms": 2000, "source_text": "Hello", "translated_text": "Xin chào", "confidence": 0.27},
                {"cue_id": "cue-b", "start_ms": 3000, "end_ms": 4000, "source_text": "Goodbye", "translated_text": "Tạm biệt", "confidence": 0.9},
                # A cue with no warning is still part of canonical output.
                {"cue_id": "cue-c", "start_ms": 8000, "end_ms": 9000, "source_text": "Quiet", "translated_text": "Yên lặng", "confidence": 0.8},
            ]}
        raw = (json.dumps(timeline, ensure_ascii=False, indent=3) + "\n").encode()
        return raw, before, store, timeline

    def test_b1_b2_one_translation_preserves_all_other_fields_and_unknown_confidence(self):
        with TemporaryDirectory(prefix="dubflow-corrected-timeline-") as root:
            for b1 in (False, True):
                child = Path(root) / str(b1)
                raw, before, store, timeline = self.fixture(child, b1=b1)
                after, _ = store.commit_correction(before, "risk-a", translation="Chào bạn")
                original_before = bytes(raw)
                actual = json.loads(derive_corrected_timeline(raw, before, after))
                timeline["cues"][0]["translated_text"] = "Chào bạn"
                self.assertEqual(actual, timeline)
                self.assertEqual(raw, original_before)
                self.assertIsNone(after.findings[0].confidence)
                self.assertEqual(actual["cues"][0]["confidence"], 0.27)

    def test_voice_only_preserves_exact_timeline_bytes_and_subtitle_identity(self):
        with TemporaryDirectory(prefix="dubflow-voice-timeline-") as root:
            raw, before, store, _ = self.fixture(root)
            after, _ = store.commit_correction(before, "risk-a", voice="voice-b")
            self.assertIs(derive_corrected_timeline(raw, before, after), raw)

    def test_exact_rational_mapping_without_rounding(self):
        with TemporaryDirectory(prefix="dubflow-rational-timeline-") as root:
            raw, before, store, _ = self.fixture(root, time_base=("1", "48000"))
            after, _ = store.commit_correction(before, "risk-a", translation="Chào bạn")
            self.assertEqual(json.loads(derive_corrected_timeline(raw, before, after))["cues"][0]["start_ms"], 1000)
            bad = replace(before, findings=tuple(replace(f, start_ticks=str(int(f.start_ticks) + 1)) if f.cue_id == "cue-a" else f for f in before.findings))
            bad_after = replace(after, findings=tuple(replace(f, start_ticks=str(int(f.start_ticks) + 1)) if f.cue_id == "cue-a" else f for f in after.findings))
            with self.assertRaisesRegex(ReviewError, "original canonical"):
                derive_corrected_timeline(raw, bad, bad_after)

    def test_mutated_source_diagnostics_other_cue_or_plan_refuse(self):
        with TemporaryDirectory(prefix="dubflow-timeline-proposal-") as root:
            raw, before, store, _ = self.fixture(root)
            after, _ = store.commit_correction(before, "risk-a", translation="Chào bạn")
            for field, value, index in (("source_text", "Changed", 0), ("confidence", "0.8", 0),
                    ("warning", "hidden warning", 1), ("translated_text", "Changed", 2)):
                findings = list(after.findings)
                findings[index] = replace(findings[index], **{field: value})
                with self.assertRaises(ReviewError):
                    derive_corrected_timeline(raw, before, replace(after, findings=tuple(findings)))
            with self.assertRaises(ReviewError):
                derive_corrected_timeline(raw, before, replace(after, last_plan=replace(after.last_plan, stages_to_rerun=("render",))))

    def test_missing_duplicate_or_changed_original_cue_and_version_refuse(self):
        with TemporaryDirectory(prefix="dubflow-timeline-binding-") as root:
            raw, before, store, timeline = self.fixture(root)
            after, _ = store.commit_correction(before, "risk-a", translation="Chào bạn")
            for case in range(7):
                bad = json.loads(raw)
                if case == 0: bad["cues"].pop(0)
                elif case == 1: bad["cues"].append(bad["cues"][0])
                elif case == 2: bad["cues"][0]["translated_text"] = "Foreign"
                elif case == 3: bad["schema_version"] = True
                elif case == 4: bad["duration_ticks"] = 1
                elif case == 5: bad["cues"][2]["start_ms"] = True
                else: bad["cues"][2]["cue_id"] = ["cue-c"]
                with self.assertRaises(ReviewError):
                    derive_corrected_timeline(json.dumps(bad).encode(), before, after)
            with self.assertRaises(ReviewError):
                derive_corrected_timeline(raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1'), before, after)
            with self.assertRaisesRegex(ReviewError, "UTF-8"):
                derive_corrected_timeline(b"\xff", before, after)

    def test_b1_cannot_silently_enable_dubbing(self):
        with TemporaryDirectory(prefix="dubflow-b1-timeline-") as root:
            raw, before, store, _ = self.fixture(root, b1=True)
            after, _ = store.commit_correction(before, "risk-a", voice="voice-a")
            with self.assertRaisesRegex(ReviewError, "dubbing mode"):
                derive_corrected_timeline(raw, before, after)


if __name__ == "__main__":
    unittest.main()
