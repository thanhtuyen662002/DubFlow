"""The regression harness itself: timeline checks and labelled coverage (dubflow.mastering.quality)."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from engine.dubflow.mastering import plan as m, quality
from tests.mastering.regression import runner

P = quality.Piece


def truth(units, split="dev", forward=False):
    return quality.Truth.from_dict({"schema_version": 1, "dataset": "t", "split": split, "forward_only": forward, "units": units})


def iv(source, start, end):
    return {"source": source, "start_ms": start, "end_ms": end}


class TimelineTests(unittest.TestCase):
    def codes(self, pieces, **kw):
        return [p["code"] for p in quality.timeline_problems(pieces, **kw)]

    def test_a_clean_timeline_has_no_problems(self):
        pieces = [P("u1", "v1", 0, 2000, 0, 2000), P("u2", "v2", 0, 3000, 2000, 5000)]
        self.assertEqual(self.codes(pieces, duration_ms=5000, forward_only=True), [])

    def test_gaps_overlaps_slivers_held_frames_and_negative_pieces(self):
        pieces = [P("u1", "v1", 0, 2000, 0, 2000), P("u1", "v1", 2000, 2200, 2500, 2700),
                  P("u2", "v2", 500, 500, 2600, 4000, "still", held=True), P("u3", "v3", 900, 100, 4000, 3000)]
        codes = self.codes(pieces)
        for code in ("GAP", "SLIVER", "OVERLAP", "HELD_FRAME", "NEGATIVE"):
            self.assertIn(code, codes)

    def test_a_timeline_shorter_than_the_video_is_a_gap(self):
        self.assertEqual(self.codes([P("u1", "v1", 0, 2000, 0, 2000)], duration_ms=3000), ["GAP"])

    def test_forward_only_flags_a_source_played_backwards_but_not_the_teaser(self):
        pieces = [P("u1", "v1", 5000, 7000, 0, 2000, "hook"), P("u2", "v1", 0, 3000, 2000, 5000),
                  P("u3", "v1", 1000, 2000, 5000, 6000)]
        codes = self.codes(pieces, forward_only=True)
        self.assertEqual(codes.count("BACKWARDS"), 1)
        self.assertIn("REPEAT", codes)  # 1000–2000 shown twice, not declared

    def test_a_declared_repeat_is_not_flagged(self):
        pieces = [P("u1", "v1", 0, 3000, 0, 3000), P("u2", "v1", 0, 3000, 3000, 6000, declared_repeat=True)]
        self.assertNotIn("REPEAT", self.codes(pieces))


class AlignmentTests(unittest.TestCase):
    def test_coverage_counts_accepted_time_contra_and_stolen_keys(self):
        t = truth({"u1": {"accept": [iv("v1", 0, 3000)]},
                   "u2": {"accept": [iv("v2", 0, 2000)], "contra": [iv("v3", 0, 5000)], "key": [iv("v2", 0, 2000)]}})
        pieces = [P("u1", "v2", 0, 1000, 0, 1000), P("u1", "v1", 0, 2000, 1000, 3000),   # 1 s of u2's key used by u1
                  P("u2", "v2", 1000, 2000, 3000, 4000), P("u2", "v3", 0, 1000, 4000, 5000)]
        result = quality.alignment(pieces, t)
        u1, u2 = result["units"]
        self.assertEqual((u1["accepted_ms"], u1["stolen_key_ms"], u1["coverage_milli"]), (2000, 1000, 666))
        self.assertEqual((u2["accepted_ms"], u2["contra_ms"]), (1000, 1000))
        self.assertTrue(u1["silent_miss"])
        self.assertEqual(result["coverage_milli"], 3000 * 1000 // 5000)

    def test_a_unit_without_footage_passes_only_when_flagged(self):
        t = truth({"u1": {"accept": [], "footage_exists": False}})
        pieces = [P("u1", "v1", 0, 2000, 0, 2000)]
        self.assertFalse(quality.alignment(pieces, t)["units"][0]["ok"])
        self.assertTrue(quality.alignment(pieces, t, flagged={"u1": True})["units"][0]["ok"])

    def test_a_photo_counts_by_the_instant_it_shows(self):
        t = truth({"u1": {"accept": [iv("v1", 0, 1)]}})
        self.assertEqual(quality.alignment([P("u1", "v1", 0, 0, 0, 3000, "still")], t)["units"][0]["accepted_ms"], 3000)

    def test_truth_is_validated(self):
        with self.assertRaises(ValueError):
            quality.Truth.from_dict({"schema_version": 1, "split": "tuned", "units": {}})
        with self.assertRaises(ValueError):
            truth({"u1": {"accept": [iv("v1", 5, 5)]}})


class PlanTests(unittest.TestCase):
    def test_a_plan_file_is_checked_from_the_command_line(self):
        plan = m.Plan("p", "product_showcase", m.Options("balanced"), 4000,
                      (m.Source("v1", 9000, 1080, 1920),),
                      (m.Segment("s1", "v1", m.Span(0, 2000), m.Span(0, 2000), speech_refs=("u1",)),
                       m.Segment("s2", "v1", m.Span(2000, 2300), m.Span(2000, 2300), speech_refs=("u2",)),
                       m.Segment("s3", "v1", m.Span(4000, 4000), m.Span(2300, 4000), role="still", speech_refs=("u2",),
                                 needs_review=True)),
                      m.Provenance("t", "1", "r", (("x", "0" * 64),)))
        with tempfile.TemporaryDirectory() as folder:
            path, truth_path = Path(folder) / "plan.json", Path(folder) / "truth.json"
            path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
            truth_path.write_text(json.dumps({"schema_version": 1, "dataset": "x", "split": "dev",
                                              "units": {"u1": {"accept": [iv("v1", 0, 9000)]}, "u2": {"accept": [iv("v1", 0, 1000)]}}}),
                                  encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                quality.main([str(path), "--truth", str(truth_path)])
        result = json.loads(out.getvalue())
        self.assertEqual(result["problem_counts"], {"HELD_FRAME": 1, "SLIVER": 1})
        self.assertEqual(result["alignment"]["units"][0]["coverage_milli"], 1000)
        self.assertTrue(result["alignment"]["units"][1]["flagged"])


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_is_valid_and_runs(self):
        names = set()
        for scenario in runner.load("all"):
            self.assertNotIn(scenario["name"], names)
            names.add(scenario["name"])
            self.assertEqual(scenario["split"], scenario["truth"]["split"])
            ids = {c["id"] for c in scenario["candidates"]}
            self.assertEqual(len(ids), len(scenario["candidates"]))
            result = runner.run(scenario)
            self.assertEqual({u["unit"] for u in result["alignment"]["units"]}, {u["id"] for u in scenario["units"]})
        self.assertTrue(any(n.startswith("holdout-") for n in names) and any(n.startswith("dev-") for n in names))


if __name__ == "__main__":
    unittest.main()
