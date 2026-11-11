"""The recorded planner call of a real run plans again to the same result with the same code (the replay is exact)."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering.planners import compilation as compiler
from engine.dubflow.mastering.planners import product as seller
from tests.mastering import test_provenance as world
from tests.mastering.real import capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.saved = seller.plan_editorial, compiler.plan_part

    def tearDown(self):
        seller.plan_editorial, compiler.plan_part = self.saved

    def test_a_recorded_product_call_replays_to_the_same_plan_and_log(self):
        visuals, pool = world.world()
        found = seller.gather_evidence([v.path for v in visuals], pool)
        found["v1c1"].add(world.vision_item("nồi chiên không dầu màu đen đặt trên bàn bếp"))
        found["v2c1"].add(ev.Item("vision", 700, "cận cảnh lòng nồi", ("closeup",), "gemini"))
        found["v2c1"].product_box = m.Box(1000, 2000, 3000, 4000, "product")
        with TemporaryDirectory() as folder:
            capture.install(Path(folder))
            plan, log = seller.plan_editorial(visuals, world.SPOKEN, world.TOTAL, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                              candidates=pool, evidence=found, facts="", inputs={"x": "a" * 64}, plan_id="product-1")
            call = json.loads((Path(folder) / "product-1.call.json").read_text(encoding="utf-8"))
            result = json.loads((Path(folder) / "product-1.result.json").read_text(encoding="utf-8"))
        again_plan, again_log = capture.replay(call)
        self.assertEqual(again_plan.to_dict(), plan.to_dict())
        self.assertEqual(json.loads(json.dumps(again_log, ensure_ascii=False)), json.loads(json.dumps(log, ensure_ascii=False)))
        self.assertEqual(result["plan"], json.loads(json.dumps(plan.to_dict())))

    def test_a_recorded_compilation_part_replays_to_the_same_plan(self):
        from engine.dubflow.mastering import analysis as scene
        shots = (scene.Shot(0, 3000, 40), scene.Shot(3000, 7000, 60), scene.Shot(7000, 12_000, 30))
        found_scene = scene.Analysis(12_000, (3000, 7000), shots, ((9000, 9600),), True, "id-1")
        source = compiler.Footage("i1-s1", Path("meo.mp4"), found_scene, 640, 360, (25, 1),
                                  ((3200, 6800, "con mèo nhảy lên bàn"),))
        pool = [world.candidate(f"i1-s1-c{n}", "i1-s1", s.start_ms, s.end_ms) for n, s in enumerate(shots)]
        evidence = compiler.evidence_for([source], {"i1-s1": pool}, {})
        with TemporaryDirectory() as folder:
            capture.install(Path(folder))
            made = compiler.plan_part("i1", "item", [("Con mèo nhảy lên bàn.", 2500)], 3500, m.Options("balanced"), lead_ms=300,
                                      gap_ms=250, footage=[source], candidates={"i1-s1": pool}, evidence=evidence, inputs={},
                                      title="Mèo")
            call = json.loads((Path(folder) / "i1.call.json").read_text(encoding="utf-8"))
        plan, log = capture.replay(call)
        self.assertEqual(plan.to_dict(), made.plan.to_dict())
        self.assertEqual(log["units"], json.loads(json.dumps(made.log["units"], ensure_ascii=False)))

    def test_the_evaluator_scores_fresh_and_replayed_results_alike(self):
        from tests.mastering.real import evaluate
        visuals, pool = world.world()
        found = seller.gather_evidence([v.path for v in visuals], pool)
        found["v1c1"].add(world.vision_item("nồi chiên không dầu màu đen đặt trên bàn bếp"))
        found["v2c1"].add(world.vision_item("một con mèo nằm ngủ trên ghế sofa"))
        with TemporaryDirectory() as folder:
            calls = Path(folder) / "v1"
            capture.install(calls)
            seller.plan_editorial(visuals, world.SPOKEN, world.TOTAL, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                  candidates=pool, evidence=found, facts="", inputs={}, plan_id="product-1")
            labels = {"schema": evaluate.LABELS_SCHEMA, "split": "dev", "videos": [{"source_id": "v1", "group": "household", "sentences": [
                {"speech_ref": "v1-s1", "text": world.SPOKEN[0][0], "type": "footage_present", "state": "MATCH_AVAILABLE",
                 "acceptable": [{"start_ms": 0, "end_ms": 6000}], "contra": [], "serious_if_wrong": True},
                {"speech_ref": "v1-s2", "text": world.SPOKEN[1][0], "type": "footage_insufficient", "state": "SOURCE_INSUFFICIENT",
                 "acceptable": [], "contra": [], "serious_if_wrong": True}]}]}
            fresh = evaluate.evaluate(labels, Path(folder), replay=False)
            again = evaluate.evaluate(labels, Path(folder), replay=True)
        self.assertEqual(fresh["total"], again["total"])
        rows = {r["speech_ref"]: r for r in fresh["rows"]}
        self.assertTrue(rows["v1-s1"]["correct"] and rows["v1-s1"]["ready"])
        self.assertTrue(rows["v1-s2"]["serious"])
        self.assertFalse(rows["v1-s2"]["ready"])  # the cat shot behind a misleading file name is flagged
        total = fresh["total"]
        self.assertEqual(total["serious_silent_misses"], 0)
        self.assertEqual(total["serious_review_recall"], {"num": 1, "den": 1, "milli": 1000})
        self.assertEqual(total["ready_precision"], {"num": 1, "den": 1, "milli": 1000})
        self.assertIn("thresholds", total)

    def test_the_real_labels_are_the_frozen_ones(self):
        import hashlib
        from tests.mastering.real import evaluate
        folder = Path(capture.HERE)
        for line in (folder / "HASHES.txt").read_text(encoding="utf-8").splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), digest, f"{name} changed after it was frozen")
        for name in ("real-dev.json", "real-holdout.json"):
            labels = evaluate.load_labels(folder / name)
            self.assertTrue(labels["labeller"].startswith("agent"))


if __name__ == "__main__":
    unittest.main()
