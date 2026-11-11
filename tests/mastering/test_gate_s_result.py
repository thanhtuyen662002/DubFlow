"""Gate S final closure: the recorded real-world result, locked to the committed recordings.

REAL-HOLDOUT was scored once (main 7acd262, after the one calibration round #763); the recordings of that run and
of the two REAL-DEV runs are under tests/mastering/real/runs/. Replay is deterministic, so the published numbers
(REAL_WORLD_SEMANTIC_CALIBRATION_REPORT.md) must come out of these files with the code checked out. A planner
change that moves them is a change to the reported Gate S evidence and must say so.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from tests.mastering.real import evaluate

REAL = Path(__file__).resolve().parent / "real"
RUNS = REAL / "runs" / "fresh-claude"


class GateSResultTests(unittest.TestCase):
    def _total(self, labels: str, replay: bool) -> dict:
        return evaluate.evaluate(evaluate.load_labels(REAL / labels), RUNS, replay=replay)["total"]

    def test_real_holdout_replay_is_the_published_fail(self):
        t = self._total("real-holdout.json", True)
        self.assertEqual((t["coverage"]["num"], t["coverage"]["den"]), (25168, 93810))
        self.assertEqual(t["serious_silent_misses"], 0)
        self.assertEqual(t["serious_contra_ready"], 0)
        self.assertEqual((t["ready_precision"]["num"], t["ready_precision"]["den"]), (0, 0))
        self.assertEqual((t["serious_review_recall"]["num"], t["serious_review_recall"]["den"]), (22, 22))
        self.assertEqual((t["false_alarm"]["num"], t["false_alarm"]["den"]), (6, 6))
        self.assertEqual((t["ready_rate"]["num"], t["ready_rate"]["den"]), (0, 20))
        self.assertFalse(t["pass"])

    def test_real_holdout_fresh_as_recorded(self):
        t = self._total("real-holdout.json", False)
        self.assertEqual((t["coverage"]["num"], t["coverage"]["den"]), (24327, 93810))
        self.assertEqual(t["serious_silent_misses"], 0)
        self.assertFalse(t["pass"])

    def test_real_dev_replay_after_the_calibration_round(self):
        t = self._total("real-dev.json", True)
        self.assertEqual((t["coverage"]["num"], t["coverage"]["den"]), (30039, 89945))
        self.assertEqual(t["serious_silent_misses"], 0)
        self.assertFalse(t["pass"])


if __name__ == "__main__":
    unittest.main()
