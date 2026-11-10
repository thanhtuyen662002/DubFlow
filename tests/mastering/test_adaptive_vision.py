"""Adaptive frame sampling: a broad look spread over every source, then targeted looks for uncovered sentences."""
from __future__ import annotations

from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import align, evidence as ev, policy as rules, vision
from engine.dubflow.mastering import candidates as shots

PACING = rules.STYLE_PACING["natural"]


def shot(cid, source, start, end, usable=True):
    quality = shots.Quality(120, 50, 300, () if usable else ("mờ, thiếu chi tiết",))
    return shots.Candidate(cid, source, start, end, 0, 50, quality, random.getrandbits(64), keyframe=f"{cid}.jpg")


class BroadTests(unittest.TestCase):
    def test_the_budget_is_spread_over_every_source_and_its_length(self):
        pool = [shot(f"a{i}", "a", i * 4000, i * 4000 + 4000) for i in range(100)] + \
               [shot(f"b{i}", "b", i * 4000, i * 4000 + 4000) for i in range(20)]
        picked = [cid for cid, _ in vision.broad(pool, Path("f"), 12)]
        self.assertEqual(len(picked), 12)
        self.assertTrue(any(cid.startswith("b") for cid in picked))  # the second source is seen
        a = sorted(int(cid[1:]) for cid in picked if cid.startswith("a"))
        self.assertGreater(a[-1], 80)  # and the end of the first, not just its first shots

    def test_everything_is_seen_when_the_budget_allows_usable_first(self):
        pool = [shot("x", "a", 0, 1000, usable=False), shot("y", "a", 1000, 2000)]
        self.assertEqual([cid for cid, _ in vision.broad(pool, Path("f"), 1)], ["y"])
        self.assertEqual(sorted(cid for cid, _ in vision.broad(pool, Path("f"), 5)), ["x", "y"])


class TargetedTests(unittest.TestCase):
    def setUp(self):
        # One long source; the broad look saw every fourth shot. The map is in a shot nobody has seen yet.
        self.pool = [shot(f"s{i}", "s1", i * 4000, i * 4000 + 4000) for i in range(30)]
        self.found = ev.gather(c.candidate_id for c in self.pool)
        for i in (0, 4, 8, 12):
            self.found[f"s{i}"].add(ev.Item("vision", 800, "Đoàn quân hành quân trên đường"))
        for i in (16, 20, 24, 28):
            self.found[f"s{i}"].add(ev.Item("vision", 800, "Khói bốc lên trên thành phố"))
        self.looked = {f"s{i}" for i in range(0, 30, 4)}
        self.units = [align.Unit("u1", "Đoàn quân hành quân trên đường.", 0, 8000, "narration"),
                      align.Unit("u2", "Bản đồ vùng Kavkaz.", 8000, 11000, "narration"),
                      align.Unit("u3", "Khói bốc lên trên thành phố.", 11000, 19000, "narration")]

    def log(self):
        _, decisions, _ = align.assign(self.units, self.pool, self.found, PACING, forward_only=True)
        return [d.to_dict() for d in decisions]

    def test_forward_only_looks_between_what_the_neighbours_chose_and_never_twice(self):
        units = self.log()
        self.assertEqual(units[1]["coverage"]["status"], "none")
        picked = vision.targets(units, self.pool, self.found, self.looked, 6, forward_only=True)
        self.assertEqual(picked, ["s9", "s10", "s11", "s13", "s14", "s15"])  # after u1's s8, before u3's s16; s12 seen

    def test_the_targeted_look_lets_the_sentence_find_its_shot(self):
        for cid in vision.targets(self.log(), self.pool, self.found, self.looked, 6, forward_only=True):
            # what the model would say about the shots it is shown
            self.found[cid].add(ev.Item("vision", 800, "Bản đồ vùng Kavkaz với các mũi tên" if cid == "s14" else "Cánh đồng trống"))
        again = self.log()
        self.assertEqual(again[1]["chosen"], ["s14"])
        self.assertEqual(again[1]["coverage"]["status"], "full")

    def test_other_evidence_decides_which_unseen_shots_come_first(self):
        self.found["s27"].add(ev.Item("transcript", 900, "bản đồ"))  # subtitles mention a map, far ahead
        units = [{"unit_id": "u2", "text": "Bản đồ vùng Kavkaz.", "function": "narration", "chosen": [],
                  "coverage": {"status": "none", "needs_review": True}}]
        self.assertEqual(vision.targets(units, self.pool, self.found, self.looked, 1, forward_only=False), ["s27"])

    def test_sentences_take_turns_and_covered_plans_look_at_nothing_more(self):
        covered = [{"unit_id": "u1", "text": "x", "chosen": ["s0"], "coverage": {"status": "full", "needs_review": False}}]
        self.assertEqual(vision.targets(covered, self.pool, self.found, set(), 8, forward_only=True), [])
        weak = [{"unit_id": u, "text": "Bản đồ.", "chosen": [], "coverage": {"status": "none", "needs_review": True}} for u in ("a", "b")]
        self.assertEqual(len(vision.targets(weak, self.pool, self.found, self.looked, 5, forward_only=False)), 5)


if __name__ == "__main__":
    unittest.main()
