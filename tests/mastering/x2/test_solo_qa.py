"""SOLO-QA selection and the contrast prototype (research tools; no planner, verification or Final QC change)."""
from __future__ import annotations

import unittest

from tests.mastering.x2 import hard_negatives as hn
from tests.mastering.x2 import solo_qa as qa


def _unit(uid: str, text: str, *, matched: int = 1, state: str = "NEEDS_REVIEW", status: str = "none", notes=(),
          piece=("s1", 0, 3000)) -> dict:
    return {"unit_id": uid, "text": text, "notes": list(notes), "coverage": {"matched_footage": matched, "status": status},
            "review": {"state": state}, "pieces": [{"id": piece[0], "offset_ms": piece[1], "length_ms": piece[2]}]}


CUES = [(1000, 2500, "The locomotive climbs the mountain."), (11000, 12500, "In 2023 the glacier melts into the valley."),
        (21000, 22500, "The tractor crosses the river.")]


class Contrast(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertEqual(hn.contrast("The helicopter climbs the mountain.", CUES[0][2])["verdict"], "substitution")
        self.assertEqual(hn.contrast("In 1959 the glacier melts.", CUES[1][2])["verdict"], "number_conflict")
        self.assertEqual(hn.contrast("We watch the tractor cross the river.", CUES[2][2])["verdict"], "consistent")

    def test_a_cue_saying_more_is_not_a_signal(self) -> None:
        self.assertEqual(hn.contrast("The tractor crosses.", "The red tractor crosses the wide river at dawn.")["verdict"],
                         "consistent")


class Stages(unittest.TestCase):
    def test_offered_assigned_reviewed_are_counted_apart(self) -> None:
        rows = [{"kind": "entity", "false_assignment": True, "matched_footage": True, "best_relevance": 700, "state": "NEEDS_REVIEW"},
                {"kind": "none", "false_assignment": False, "matched_footage": False, "best_relevance": 0, "state": "MATCH_NOT_FOUND"},
                {"kind": "match", "matched_footage": True, "best_relevance": 1000, "state": "NEEDS_REVIEW"}]
        out = hn.stages(rows, final_qc_blocks=True)
        self.assertEqual(out["by_kind"]["entity"], {"units": 1, "offered": 1, "assigned": 1, "reviewed": 1, "ready": 0})
        self.assertEqual(out["by_kind"]["none"], {"units": 1, "offered": 0, "assigned": 0, "reviewed": 1, "ready": 0})
        self.assertNotIn("match", out["by_kind"])
        self.assertTrue(out["final_qc_blocks_part"])


class Select(unittest.TestCase):
    def log(self) -> dict:
        return {"units": [
            _unit("u1", "The helicopter climbs the mountain.", piece=("s0", 500, 3000)),
            _unit("u2", "In 1959 the glacier melts.", piece=("s1", 10500, 3000)),
            _unit("u3", "We watch the tractor cross the river.", piece=("s2", 20700, 3000)),
            _unit("u4", "Something with no footage.", matched=0, state="MATCH_NOT_FOUND"),
            _unit("u5", "A ready one.", state="READY"),
            _unit("u6", "Spoken only.", notes=["3.0 giây cảnh chỉ khớp lời nói trong nguồn (phụ đề/transcript), ..."],
                  piece=("s9", 90000, 3000)),
            _unit("u7", "Partly shown.", status="partial", piece=("s8", 80000, 3000))]}

    def test_priority_and_what_is_left_out(self) -> None:
        plan = {"segments": [{"speech_refs": ["u3"], "reasons": ["thử nghiệm #867: điểm vào theo lời nguồn"]}]}
        cases = qa.select(self.log(), CUES, plan=plan, limit=5)
        self.assertEqual([(c["unit_id"], c["why"]) for c in cases],
                         [("u2", "number_conflict"), ("u1", "substitution"), ("u3", "cue_inpoint"), ("u6", "speech_only"),
                          ("u7", "unclear")])
        self.assertTrue(all(c["owner_spot_check"] is None for c in cases))

    def test_limit_is_five_to_ten(self) -> None:
        for bad in (0, 4, 11):
            with self.assertRaises(ValueError):
                qa.select(self.log(), CUES, limit=bad)
        self.assertLessEqual(len(qa.select(self.log(), CUES, limit=5)), 5)

    def test_manifest_is_never_a_label_or_a_gate(self) -> None:
        doc = qa.manifest(qa.select(self.log(), CUES), source="x")
        self.assertEqual(doc["evidence_level"], "OWNER_SPOT_CHECK_NOT_A_LABEL")
        self.assertIs(doc["is_gate"], False)
        qa.answer(doc, "u2", "wrong", at="2026-11-15T00:00:00Z")
        case = next(c for c in doc["cases"] if c["unit_id"] == "u2")
        self.assertEqual((case["owner_spot_check"], case["answered_at"]), ("wrong", "2026-11-15T00:00:00Z"))
        with self.assertRaises(ValueError):
            qa.answer(doc, "u2", "label:match")
        self.assertIn("| 1 |", qa.markdown(doc))


if __name__ == "__main__":
    unittest.main()
