"""Gates over the fixed regression scenarios (``tests/mastering/regression``) and the P0 allocation rules.

The gates are invariants, not scores fitted to a scenario: no timeline defect, no footage the truth marks as wrong
(contra), no unit left uncovered without a review flag (silent miss) and no key shot taken by another sentence
outside the intrinsically scarce long-forward scenario. Coverage numbers are reported by the runner, not asserted
here, except a floor on the dev espresso set the hardening work was built against.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import align, evidence as ev, policy as rules
from engine.dubflow.mastering import candidates as shots
from tests.mastering.regression import runner

PACING = rules.STYLE_PACING["natural"]
SCARCE = {"dev-long-forward"}  # one source, more narration about the map than the map lasts


def candidate(cid, source, start, end, motion=50):
    return shots.Candidate(cid, source, start, end, 0, motion, shots.Quality(120, 50, 300, ()), random.getrandbits(64))


class FrozenFixtures(unittest.TestCase):
    def test_ground_truth_matches_the_frozen_hashes(self):
        folder = Path(runner.__file__).parent
        frozen = json.loads((folder / "FROZEN.json").read_text(encoding="utf-8"))["fixtures"]
        on_disk = sorted(p.name for p in folder.glob("*.json") if p.name != "FROZEN.json")
        self.assertEqual(sorted(frozen), on_disk, "every fixture must be frozen")
        for name, entry in frozen.items():
            digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"], f"{name} changed after it was frozen")


class ScenarioGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = [runner.run(s) for s in runner.load("all")]

    def test_no_timeline_problems(self):
        for result in self.results:
            self.assertEqual([p["code"] for p in result["problems"]], [], result["name"])

    def test_no_contradicting_footage_and_no_silent_miss(self):
        for result in self.results:
            a = result["alignment"]
            self.assertEqual((a["contra_ms"], a["silent_misses"]), (0, 0), result["name"])

    def test_no_sentence_takes_another_sentences_key_shot(self):
        for result in self.results:
            if result["name"] not in SCARCE:
                self.assertEqual(result["alignment"]["stolen_key_ms"], 0, result["name"])

    def test_dev_espresso_floor(self):
        espresso = next(r for r in self.results if r["name"] == "dev-espresso")
        self.assertGreaterEqual(espresso["alignment"]["coverage_milli"], 900)


class AllocationRules(unittest.TestCase):
    def test_a_weak_hook_match_does_not_take_a_later_sentences_only_shot(self):
        pool = [candidate("a", "v1", 0, 6000), candidate("b", "v2", 0, 3000), candidate("c", "v3", 0, 6000)]
        found = ev.gather(c.candidate_id for c in pool)
        found["a"].add(ev.Item("vision", 800, "Hộp tai nghe trên bàn gỗ"))
        found["b"].add(ev.Item("vision", 800, "Cắm sạc USB-C vào hộp, đèn sạc sáng, có tiếng nhạc"))
        found["c"].add(ev.Item("vision", 800, "Người chạy bộ đeo tai nghe trong công viên"))
        found["b"].hook = 900
        hook = align.Unit("u1", "Nghe nhạc cả ngày không lo hết pin.", 0, 2500, "hook")
        charge = align.Unit("u3", "Cắm sạc USB-C, đèn sạc sáng báo đầy.", 2500, 5500, "feature")
        choices, _, _ = align.assign([hook, charge], pool, found, PACING, forward_only=False)
        self.assertNotIn("b", [c.candidate.candidate_id for c in choices if c.unit_id == "u1"])
        self.assertIn("b", [c.candidate.candidate_id for c in choices if c.unit_id == "u3"])

    def test_partial_coverage_is_flagged_not_hidden(self):
        pool = [candidate("a", "v1", 0, 1500), candidate("z", "v2", 0, 9000)]
        found = ev.gather(c.candidate_id for c in pool)
        found["a"].add(ev.Item("vision", 800, "Nút hẹn giờ trên nồi chiên không dầu"))
        found["z"].add(ev.Item("vision", 800, "Phong cảnh bãi biển"))
        sentence = align.Unit("u1", "Hẹn giờ bằng nút trên nồi chiên không dầu rồi chờ món chín vàng đều.", 0, 6000, "feature")
        choices, decisions, _ = align.assign([sentence], pool, found, PACING, forward_only=False)
        coverage = decisions[0].to_dict()["coverage"]
        self.assertEqual(coverage["status"], "partial")
        self.assertTrue(coverage["needs_review"])
        self.assertTrue(any(c.needs_review for c in choices))


if __name__ == "__main__":
    unittest.main()
