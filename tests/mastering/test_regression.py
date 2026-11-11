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
# known open defect, not an exemption by score: in a forward-only compilation the opening sentence, filled after the
# items, still starts one item's shot (1.4 s of the bánh xèo key) instead of the unkeyed broth shot; tracked in
# docs/mastering/SEMANTIC_REMEDIATION_REPORT.md and its issue. Its contra and silent-miss gates still apply.
KNOWN_OPEN = {"dev3-street-food": 1400}


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
                # an open defect may not get worse
                self.assertLessEqual(result["alignment"]["stolen_key_ms"], KNOWN_OPEN.get(result["name"], 0), result["name"])

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


class PieceInvariants(unittest.TestCase):
    """Real run 8, P2 (handheld air duster: 28 s of footage for 22 s of narration): the call to action was cut
    into six pieces, two under 0.6 s, and the last ran past its clip (OUTSIDE_SOURCE), so the plan was refused and
    the video fell back to a plain join. Whatever the footage, every piece stays inside its shot and every sentence
    is covered exactly."""

    WORDS = ("pin", "hút bụi", "thổi", "bàn phím", "sạc", "nhỏ gọn", "giá", "mua", "đèn", "mạnh", "ô tô", "khe")

    def scenario(self, seed: int):
        rnd = random.Random(seed)
        pacing = rnd.choice(list(rules.STYLE_PACING.values()))
        pool = []
        for v in range(rnd.randint(1, 5)):
            at = 0
            for k in range(rnd.randint(1, 4)):
                length = rnd.randint(300, 9000)
                pool.append(shots.Candidate(f"v{v}c{k}", f"v{v}", at, at + length, k, rnd.randint(0, 100),
                                            shots.Quality(120, 50, 300, () if rnd.random() > .1 else ("mờ",)), rnd.getrandbits(64)))
                at += length
        found = ev.gather(c.candidate_id for c in pool)
        for c in pool:
            if rnd.random() < .7:
                found[c.candidate_id].add(ev.Item("vision", rnd.randint(400, 1000), " ".join(rnd.sample(self.WORDS, 2))))
        units, at, count = [], 0, rnd.randint(2, 7)
        for i in range(count):
            length = rnd.randint(800, 6000)
            function = "hook" if i == 0 else "cta" if i == count - 1 else rnd.choice(("benefit", "feature", "demonstration"))
            units.append(align.Unit(f"u{i}", " ".join(rnd.sample(self.WORDS, 3)), at, at + length, function))
            at += length
        return pool, found, units, pacing

    def test_pieces_stay_inside_their_shot_and_cover_each_sentence_exactly(self):
        for seed in range(150):
            pool, found, units, pacing = self.scenario(seed)
            for forward in (False, True):
                with self.subTest(seed=seed, forward=forward):
                    choices, _, _ = align.assign(units, pool, found, pacing, forward_only=forward)
                    for u in units:
                        mine = [c for c in choices if c.unit_id == u.unit_id]
                        self.assertEqual(sum(c.length_ms for c in mine), u.length_ms)
                        for c in mine:
                            self.assertGreaterEqual(c.offset_ms, c.candidate.start_ms)
                            self.assertLessEqual(c.offset_ms + c.length_ms, c.candidate.end_ms)

    def test_scarce_footage_does_not_run_past_a_shot_or_cut_flashes(self):
        # P2's shape: four clips cut into short shots, one unusable, about as much footage as narration.
        # Before the fix the opening's last piece ran 541 ms past its shot (v3c2) and the plan was refused.
        shots_ = [("v1c1", 0, 7200, 11, False, "sạc tay mua"), ("v2c1", 0, 2360, 27, True, "thổi nhỏ gọn đèn"),
                  ("v2c2", 2360, 5263, 20, True, "tay sạc mua"), ("v2c3", 5263, 8400, 92, True, "sạc nhỏ gọn thổi"),
                  ("v3c1", 0, 3944, 64, True, "đèn nhỏ gọn tay"), ("v3c2", 3944, 4757, 3, True, "ô tô nhỏ gọn khe"),
                  ("v3c3", 4757, 6600, 48, True, "nhỏ gọn tay mua"), ("v4c1", 0, 4891, 22, True, "sạc nhỏ gọn mua"),
                  ("v4c2", 4891, 5805, 41, True, "sạc mua tay")]
        pool = [shots.Candidate(cid, cid[:2], start, end, 0, motion, shots.Quality(120, 50, 300, () if ok else ("mờ",)), n)
                for n, (cid, start, end, motion, ok, _) in enumerate(shots_)]
        found = ev.gather(c.candidate_id for c in pool)
        for cid, *_, note in shots_:
            found[cid].add(ev.Item("vision", 800, note))
        script = [("nhỏ gọn giá ô tô", 3600, "hook"), ("sạc thổi khe", 4200, "benefit"), ("nhỏ gọn khe mua", 3400, "feature"),
                  ("tay giá mua", 3300, "feature"), ("tay mua đèn", 3500, "demonstration"), ("khe ô tô nhỏ gọn", 4000, "cta")]
        units, at = [], 0
        for i, (text, length, function) in enumerate(script):
            units.append(align.Unit(f"u{i + 1}", text, at, at + length, function))
            at += length
        choices, _, _ = align.assign(units, pool, found, PACING, forward_only=False)
        for u in units:
            mine = [c for c in choices if c.unit_id == u.unit_id]
            self.assertEqual(sum(c.length_ms for c in mine), u.length_ms, u.unit_id)
            self.assertTrue(len(mine) == 1 or all(c.length_ms >= align.SHORT_FLOOR for c in mine), (u.unit_id, [c.length_ms for c in mine]))
            for c in mine:
                self.assertLessEqual(c.offset_ms + c.length_ms, c.candidate.end_ms, (u.unit_id, c.candidate.candidate_id))
                self.assertTrue(c.candidate.quality.usable)


if __name__ == "__main__":
    unittest.main()
