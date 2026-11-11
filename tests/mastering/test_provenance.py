"""Evidence provenance (P2-14): a file name is never content, so a plan made from file names alone is never READY.

rc5 Windows check: with every AI call failing (401) every part of a raw Tổng hợp still said ``mode: content``,
because the file name counted as content. The seven cases of the directive: vision OK, vision failing, no key,
the user's note, the file name only, a misleading file name, a transcript.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import review
from engine.dubflow.mastering import vision as seeing
from engine.dubflow.mastering.planners import compilation as compiler
from engine.dubflow.mastering.planners import product as planner

SPOKEN = [("Nồi chiên không dầu màu đen trên bàn bếp.", 2400), ("Lòng nồi chống dính rất dễ lau.", 2400)]
TOTAL = 300 + 4800 + 250 + 700


def candidate(cid, source, start, end):
    return shots.Candidate(cid, source, start, end, 0, 50, shots.Quality(120, 50, 300), random.getrandbits(64))


def world(names=("noi chien khong dau mau den.mp4", "long noi chong dinh.mp4")):
    visuals = [planner.Visual(Path(name), 6000, 1920, 1080) for name in names]
    pool = {0: [candidate("v1c1", "v1", 0, 6000)], 1: [candidate("v2c1", "v2", 0, 6000)]}
    return visuals, pool


def plan(visuals, pool, found):
    made, log = planner.plan_editorial(visuals, SPOKEN, TOTAL, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                       candidates=pool, evidence=found, facts="", inputs={})
    review.annotate(log, made.to_dict())  # as the jobs do: a unit is not ready while one of its pieces needs review
    json.dumps(log, ensure_ascii=False)
    return log


def states(log):
    return [u["review"]["state"] for u in log["units"]]


def vision_item(text):
    return ev.Item("vision", 900, text, (), "gemini")


class Failing:
    """An ask that always fails the way a bad or missing key does."""

    code, message = "KEY", "401 unauthorized"

    def __call__(self, *_):
        error = RuntimeError("401")
        error.code, error.message = self.code, self.message
        raise error


class ProvenanceTests(unittest.TestCase):
    def test_vision_ok_is_content_and_can_be_ready(self):
        visuals, pool = world()
        found = planner.gather_evidence([v.path for v in visuals], pool)
        found["v1c1"].add(vision_item("nồi chiên không dầu màu đen đặt trên bàn bếp"))
        found["v2c1"].add(vision_item("cận cảnh lòng nồi chống dính, tay lau lòng nồi"))
        log = plan(visuals, pool, found)
        self.assertEqual(log["mode"], "content")
        self.assertEqual(log["provenance"]["vision"], 2)
        self.assertEqual(log["provenance"]["filename_metadata"], 2)
        self.assertIn(review.READY, states(log))

    def _no_vision(self, ask):
        visuals, pool = world()
        found = planner.gather_evidence([v.path for v in visuals], pool)
        with TemporaryDirectory() as tmp:
            frame = Path(tmp) / "k.jpg"
            frame.write_bytes(b"\xff\xd8 frame")
            describer = seeing.Describer(ask, provider="gemini", model="m", cache=Path(tmp) / "cache")
            seen = describer.describe([("v1c1", frame), ("v2c1", frame)])
            seeing.apply(seen, found, "gemini")
        self.assertEqual(seen, {})
        self.assertTrue(describer.warnings)
        return plan(visuals, pool, found)

    def test_vision_failing_falls_back_to_signals_only(self):
        log = self._no_vision(Failing())
        self.assertEqual(log["mode"], "signals_only")
        self.assertEqual(log["provenance"]["vision"], 0)
        self.assertNotIn(review.READY, states(log))
        self.assertNotIn(review.SOURCE_INSUFFICIENT, [review.evidence_state(u, log) for u in log["units"]])

    def test_no_key_is_the_same_as_vision_failing(self):
        visuals, pool = world()
        found = planner.gather_evidence([v.path for v in visuals], pool)  # no describer at all
        log = plan(visuals, pool, found)
        self.assertEqual(log["mode"], "signals_only")
        self.assertTrue(all(s == review.NEEDS_REVIEW or s == review.MATCH_NOT_FOUND for s in states(log)))
        self.assertTrue(any("tên file không tính" in w for w in log["warnings"]))

    def test_a_user_note_is_content(self):
        visuals, pool = world(("a.mp4", "b.mp4"))
        found = planner.gather_evidence([v.path for v in visuals], pool,
                                        ["Nồi chiên không dầu màu đen trên bàn bếp", "Lòng nồi chống dính, lau rất dễ"])
        log = plan(visuals, pool, found)
        self.assertEqual(log["mode"], "content")
        self.assertEqual(log["provenance"]["user_note"], 2)
        self.assertEqual(log["provenance"]["filename_metadata"], 0)

    def test_a_file_name_alone_never_reaches_ready(self):
        visuals, pool = world()
        found = planner.gather_evidence([v.path for v in visuals], pool)
        log = plan(visuals, pool, found)
        self.assertEqual(log["provenance"], {"vision": 0, "transcript": 0, "user_note": 0, "ocr": 0,
                                             "filename_metadata": 2, "signal": 0})
        self.assertEqual(review.content_mode(log), "signals_only")
        self.assertNotIn(review.READY, states(log))
        view = review.view(log, None, review.Review(log["plan_id"]))
        self.assertEqual(view["mode"], "signals_only")
        self.assertEqual(view["provenance"]["filename_metadata"], 2)

    def test_a_misleading_file_name_cannot_make_a_shot_ready(self):
        # the file says "lòng nồi chống dính" but the AI saw a cat: the name may rank, it never proves the shot
        visuals, pool = world(("noi chien khong dau mau den.mp4", "long noi chong dinh rat de lau.mp4"))
        found = planner.gather_evidence([v.path for v in visuals], pool)
        found["v1c1"].add(vision_item("nồi chiên không dầu màu đen đặt trên bàn bếp"))
        found["v2c1"].add(vision_item("một con mèo nằm ngủ trên ghế sofa"))
        log = plan(visuals, pool, found)
        self.assertEqual(log["mode"], "content")
        self.assertEqual(states(log)[0], review.READY)  # the shot the AI saw the pot in
        self.assertNotEqual(states(log)[1], review.READY)

    def test_a_transcript_is_content_in_a_compilation(self):
        found_scene = scene.Analysis(6000, (), (scene.Shot(0, 6000, 50),), (), True, "x")
        source = compiler.Footage("s1", Path("meo nhay.mp4"), found_scene, 640, 360, cues=((0, 6000, "con mèo nhảy lên bàn"),))
        pool = [candidate("s1c1", "s1", 0, 6000)]
        found = compiler.evidence_for([source], {"s1": pool}, {})
        kinds = {ev.provenance(i.origin, i.detail) for i in found["s1c1"].items}
        self.assertEqual(kinds, {"transcript", "filename_metadata"})
        self.assertTrue(found["s1c1"].content)
        only_name = ev.gather(["x"])
        only_name["x"].add(ev.Item("metadata", 700, "con meo nhay len ban", (), "tên file"))
        self.assertFalse(only_name["x"].content)

    def test_an_old_log_that_said_content_from_file_names_is_read_as_signals_only(self):
        visuals, pool = world()
        found = planner.gather_evidence([v.path for v in visuals], pool)
        log = plan(visuals, pool, found)
        old = dict(log, mode="content")  # what a log written before P2-14 said
        old.pop("provenance")
        for unit in old["units"]:
            unit["review"] = {"evidence": review.MATCH_AVAILABLE, "state": review.READY}
        view = review.view(old, None, review.Review(old["plan_id"]))
        self.assertEqual(view["mode"], "signals_only")
        self.assertNotIn(review.READY, [u["state"] for u in view["units"]])
        self.assertEqual(view["provenance"]["filename_metadata"], 2)
        self.assertEqual(set(view["unreviewed"]), {u["unit_id"] for u in old["units"]})


if __name__ == "__main__":
    unittest.main()
