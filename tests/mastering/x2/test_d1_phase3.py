"""Locks the Phase 3 (D1) closing measurement (``d1_phase3.py``): ENGINEERING_FIXTURE_ONLY, research only."""
from __future__ import annotations

import unittest

from tests.mastering.x2 import d1_phase3 as phase3


class D1Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = phase3.run()["summary"]

    def test_every_rule_moves_more_pieces_onto_their_cue(self):
        off = self.summary["off"]["played_over_cue"]
        for arm, row in self.summary.items():
            if arm != "off" and arm != "same_shot":
                self.assertGreater(row["played_over_cue"], off, arm)

    def test_picture_gain_and_loss_mirror_each_other_for_the_engine_rule(self):
        # the pan where the picture comes first loses exactly what the pan where it comes with the speech gains:
        # a cue alone cannot tell the two apart, so D1 is a timing change, not a picture improvement
        row = self.summary["engine@300"]
        self.assertEqual(sorted(u.split(":")[0].split("/")[0] for u in row["picture_worse"]), ["picture_before_speech"] * 2)
        self.assertEqual(sorted(u.split(":")[0].split("/")[0] for u in row["picture_better"]), ["picture_with_speech"] * 2)
        self.assertEqual(row["picture_right_ms"], self.summary["off"]["picture_right_ms"])

    def test_lead_between_0_and_500_ms_makes_no_difference_to_the_picture(self):
        values = {self.summary[f"engine@{lead}"]["picture_right_ms"] for lead in (0, 150, 300, 500)}
        self.assertEqual(len(values), 1)

    def test_same_shot_rule_ignores_vision_and_loses_more_picture_than_the_engine_rule(self):
        self.assertEqual(self.summary["same_shot+clamp"]["shots_changed"], [])
        self.assertLess(self.summary["same_shot+clamp"]["picture_right_ms"], self.summary["engine@300"]["picture_right_ms"])
        self.assertTrue(any(u.startswith("picture_before_speech_seen") for u in self.summary["same_shot+clamp"]["picture_worse"]))

    def test_no_rule_changes_review_state_or_adds_timeline_problems(self):
        for arm, row in self.summary.items():
            self.assertEqual(row["ready_changed"], 0, arm)
            self.assertEqual(row["new_timeline_problems"], {}, arm)
            self.assertEqual(row["ready"], self.summary["off"]["ready"], arm)


if __name__ == "__main__":
    unittest.main()
