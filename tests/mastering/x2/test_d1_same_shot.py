"""D1 same-shot variant (#879, ENGINEERING_FIXTURE_ONLY): shots, order and lengths never change; fall back when unsafe."""
from __future__ import annotations

import dataclasses
import json
import unittest
from collections import Counter
from pathlib import Path

from engine.dubflow.mastering import align
from engine.dubflow.mastering.candidates import Candidate, Quality
from tests.mastering.x2 import d1_same_shot as d


def _choice(unit: str, sid: str, start: int, end: int, offset: int, length: int, reasons: tuple[str, ...] = ()) -> align.Choice:
    cand = Candidate(sid, "src", start, end, 0, 50, Quality(120, 50, 300, ()), 1, f"{sid}.jpg")
    return align.Choice(unit, cand, offset, length, 50, 600, False, reasons)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = d.run()
        cls.by = {(r["scenario"], r["forward_only"]): r for r in cls.result["results"]}

    def test_every_scenario_runs_in_both_modes(self) -> None:
        names = {sc.name for sc in d.scenarios()}
        self.assertEqual(names, {"moves", "near_edge", "cue_outside_shot", "montage", "reuse", "competing_shot"})
        self.assertEqual(set(self.by), {(n, fo) for n in names for fo in (False, True)})

    def test_shots_order_lengths_never_change(self) -> None:
        for key, r in self.by.items():
            self.assertTrue(r["same_shots_order_lengths"], key)
            self.assertEqual(len(r["region"]["pieces"]), len(r["variant"]["pieces"]), key)

    def test_no_new_timeline_problem_and_no_repeat(self) -> None:
        for key, r in self.by.items():
            self.assertEqual(r["new_timeline_problems"], [], key)
            self.assertEqual(r["variant"]["repeat"], r["region"]["repeat"], key)
            self.assertEqual(r["variant"]["repeat"], 0, key)

    def test_moved_pieces_stay_inside_their_shot(self) -> None:
        for key, r in self.by.items():
            for row in r["log"]:
                if row["moved"]:
                    piece = r["variant"]["pieces"][row["index"]]
                    sc = next(s for s in d.scenarios() if s.name == key[0])
                    shot = next(s for s in sc.shots if s.sid == piece[1])
                    self.assertGreaterEqual(piece[2], shot.start_ms, key)
                    self.assertLessEqual(piece[2] + piece[3], shot.end_ms, key)

    def test_moves_gain_cue_overlap(self) -> None:
        for fo in (False, True):
            r = self.by[("moves", fo)]
            self.assertEqual(r["moved"], 2)
            self.assertGreater(r["variant"]["cue_overlap_ms"], r["region"]["cue_overlap_ms"])
            self.assertEqual([p[2] for p in r["variant"]["pieces"]], [5700, 15700])
        s = self.result["summary"]
        self.assertGreater(s["cue_overlap_ms_variant"], s["cue_overlap_ms_region"])

    def test_near_edge_keeps_original_inpoint(self) -> None:
        for fo in (False, True):
            r = self.by[("near_edge", fo)]
            self.assertEqual(r["moved"], 0)
            self.assertEqual(r["kept"], {"outside_shot": 2})
            self.assertEqual(r["variant"]["pieces"], r["region"]["pieces"])

    def test_cue_outside_shot_keeps_original_inpoint(self) -> None:
        for fo in (False, True):
            r = self.by[("cue_outside_shot", fo)]
            self.assertEqual(r["moved"], 0)
            self.assertIn("cue_outside_shot", r["kept"])
            self.assertEqual(r["variant"]["pieces"], r["region"]["pieces"])

    def test_montage_moves_only_the_piece_that_fits(self) -> None:
        for fo in (False, True):
            r = self.by[("montage", fo)]
            self.assertEqual(len(r["region"]["pieces"]), 2)
            self.assertEqual(r["moved"], 1)
            self.assertEqual(r["kept"], {"outside_shot": 1})

    def test_reuse_falls_back_instead_of_replaying(self) -> None:
        for fo in (False, True):
            r = self.by[("reuse", fo)]
            kept = [row for row in r["log"] if row["kept"] == "timeline"]
            self.assertEqual(len(kept), 1)
            self.assertIn("REPEAT", kept[0]["would_add"])
            self.assertEqual(r["variant"]["repeat"], 0)

    def test_competing_shot_stays_on_planner_choice(self) -> None:
        for fo in (False, True):
            r = self.by[("competing_shot", fo)]
            self.assertEqual([p[1] for p in r["region"]["pieces"]], ["s0"])
            self.assertEqual([p[1] for p in r["variant"]["pieces"]], ["s0"])
            self.assertEqual(r["moved"], 1)


class Reanchor(unittest.TestCase):
    def test_still_declared_repeat_and_no_cue_are_kept(self) -> None:
        still = _choice("u1", "s0", 0, 10000, 0, 3000)
        rep = _choice("u2", "s1", 10000, 20000, 10000, 3000, ("nhắc lại có chủ đích",))
        none = _choice("u3", "s2", 20000, 30000, 20000, 3000)
        cited = {"u1": [(6000, 7000)], "u2": [(16000, 17000)]}
        out, log = d.reanchor([still, rep, none], cited, stills=("s0",))
        self.assertEqual([r["kept"] for r in log], ["still", "declared_repeat", "no_cited_cue"])
        self.assertEqual(out, [still, rep, none])

    def test_unchanged_and_no_gain(self) -> None:
        same = _choice("u1", "s0", 0, 10000, 5700, 3000)
        log = d.reanchor([same], {"u1": [(6000, 7000)]})[1]
        self.assertEqual(log[0]["kept"], "unchanged")
        covered = _choice("u1", "s0", 0, 10000, 5800, 3000)
        log = d.reanchor([covered], {"u1": [(6000, 7000)]})[1]
        self.assertEqual(log[0]["kept"], "no_gain")

    def test_moved_piece_keeps_shot_and_length_and_says_why(self) -> None:
        c = _choice("u1", "s0", 0, 10000, 0, 3000)
        out, log = d.reanchor([c], {"u1": [(6000, 7000)]})
        self.assertTrue(log[0]["moved"])
        self.assertEqual(out[0], dataclasses.replace(c, offset_ms=5700, reasons=(d.REASON,)))
        self.assertEqual(log[0]["cue_overlap_ms"], [0, 1000])

    def test_lead_ms_is_validated(self) -> None:
        for bad in (-1, 5001, 1.5, True):
            with self.assertRaises(ValueError):
                d.reanchor([], {}, lead_ms=bad)

    def test_input_is_not_mutated(self) -> None:
        cs = [_choice("u1", "s0", 0, 10000, 0, 3000)]
        before = list(cs)
        d.reanchor(cs, {"u1": [(6000, 7000)]})
        self.assertEqual(cs, before)


class ClampDs2Timeline(unittest.TestCase):
    """#881: the d-s2 region timeline (22 pieces, numbers only) with the clamp-to-shot-end option."""

    @classmethod
    def setUpClass(cls) -> None:
        doc = json.loads((Path(__file__).with_name("d1_ds2_timeline.json")).read_text(encoding="utf-8"))
        cls.doc = doc
        cls.choices = [_choice(u, sid, *doc["shots_ms"][sid], off, ln) for u, sid, off, ln in doc["pieces"]]
        cls.cited = {u: list(map(tuple, spans.values())) for u, spans in doc["cited_cues_ms"].items()}

    def run_(self, clamp: bool):
        return d.reanchor(self.choices, self.cited, lead_ms=300, duration_ms=self.doc["duration_ms"], clamp_to_shot_end=clamp)

    def piece(self, out, unit):
        return [c.offset_ms for c in out if c.unit_id == unit]

    def test_baseline_is_22_clean_pieces(self) -> None:
        self.assertEqual(len(self.choices), 22)
        self.assertEqual(d._codes(self.choices, (), self.doc["duration_ms"], False), Counter())

    def test_clamp_moves_p3_and_p4_to_the_shot_end(self) -> None:
        out, log = self.run_(True)
        self.assertEqual(self.piece(out, "p3"), [67020])
        self.assertEqual(self.piece(out, "p4"), [103020])
        rows = {r["unit"]: r for r in log}
        self.assertTrue(rows["p3"]["clamped"] and rows["p3"]["moved"])
        self.assertEqual(rows["p3"]["all_cue_overlap_ms"], [[0], [340]])
        self.assertEqual(rows["p4"]["all_cue_overlap_ms"], [[0], [960]])

    def test_p5_keeps_its_inpoint_because_it_would_replay_r1(self) -> None:
        out, log = self.run_(True)
        row = next(r for r in log if r["unit"] == "p5")
        self.assertEqual(self.piece(out, "p5"), [124820])
        self.assertEqual(row["kept"], "timeline")
        self.assertIn("REPEAT", row["would_add"])

    def test_m1_still_touches_c1_and_c2(self) -> None:
        out, log = self.run_(True)
        row = next(r for r in log if r["unit"] == "m1")
        self.assertEqual(row["kept"], "drops_touched_cue")
        (start,) = self.piece(out, "m1")
        for a, b in self.cited["m1"]:
            self.assertGreater(d._overlap(start, start + 3800, a, b), 0)

    def test_clamped_timeline_keeps_shots_order_lengths_and_is_clean(self) -> None:
        out, _ = self.run_(True)
        key = lambda cs: [(c.unit_id, c.candidate.candidate_id, c.length_ms) for c in cs]
        self.assertEqual(key(out), key(self.choices))
        self.assertEqual(d._codes(out, (), self.doc["duration_ms"], False), Counter())
        for c in out:
            self.assertTrue(c.candidate.start_ms <= c.offset_ms and c.offset_ms + c.length_ms <= c.candidate.end_ms)

    def test_option_off_is_the_8a55f42a_behaviour(self) -> None:
        out, log = d.reanchor(self.choices, self.cited, lead_ms=300, duration_ms=self.doc["duration_ms"])
        self.assertEqual(self.piece(out, "m1"), [7920])
        self.assertEqual({r["unit"]: r["kept"] for r in log if r["unit"] in ("p3", "p4", "p5", "r1")},
                         {"p3": "outside_shot", "p4": "outside_shot", "p5": "outside_shot", "r1": "outside_shot"})

    def test_option_must_be_bool(self) -> None:
        with self.assertRaises(ValueError):
            d.reanchor([], {}, clamp_to_shot_end=1)


if __name__ == "__main__":
    unittest.main()
