"""The offline W3 diagnosis tool on a run folder made here by the planner itself (no media, no network)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import align
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import review as reviewing
from engine.dubflow.mastering.planners import compilation as planner
from engine.dubflow.product import script
from tests.mastering.longform import w3

SENTENCES = [("Bản đồ có mũi tên chỉ khoảng cách một nghìn năm trăm dặm.", 2600),
             ("Đoàn binh sĩ đội mũ sắt hành quân trên đường đất.", 2400),
             ("Một chiếc xe tăng lăn bánh qua cánh đồng.", 2200)]
TEXTS = {  # shot number -> what the vision said; shots 1 and 6 were never looked at
    2: "Bản đồ châu Âu có mũi tên, dòng chữ 1500 MILES",
    3: "Bản đồ vùng Balkan, mũi tên đánh dấu đường tiến quân",
    4: "Đoàn binh sĩ đội mũ sắt hành quân trên con đường đất",
    5: "Hàng xe tăng đỗ trên cánh đồng, khói mù",
    7: "Bầu trời có mây",
}


def make_run(root: Path) -> Path:
    """A one-part raw run as ``run_program`` saves it: editorial, plan and analysis of part i1."""
    length = 60_000
    found = scene.Analysis(length, tuple(range(6000, length, 6000)), tuple(scene.Shot(a, a + 6000, 40) for a in range(0, length, 6000)),
                           (), True, "a" * 64)
    source = planner.Footage("i1-s1", Path("film.webm"), found, 640, 480, (24000, 1001))
    quality = shots.Quality(120, 50, 300)
    rng = random.Random(7)
    pool = [shots.Candidate(f"i1-s1c{n + 1}", "i1-s1", n * 6000 + 240, n * 6000 + 5760, n, 30, quality, rng.getrandbits(64))
            for n in range(length // 6000)]
    evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
    for number, text in TEXTS.items():
        evidence[f"i1-s1c{number}"].add(ev.Item("vision", 850, text, ("explanation",), "AI test"))
    _, narration_ms = script.captions(SENTENCES)
    made = planner.plan_part("i1", "item", SENTENCES, narration_ms, m.Options("balanced", "entertainment"),
                             lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, footage=[source], candidates={"i1-s1": pool},
                             evidence=evidence, inputs={"i1-s1": "a" * 64, "part": "b" * 64}, title="")
    reviewing.annotate(made.log, made.plan.to_dict())
    folder = root / "run-test" / ".dubflow-work" / "mastering"
    folder.mkdir(parents=True)
    (folder / "editorial-i1.json").write_text(json.dumps(made.log, ensure_ascii=False), encoding="utf-8")
    (folder / "plan-i1.json").write_text(json.dumps(made.plan.to_dict(), ensure_ascii=False), encoding="utf-8")
    (folder / f"analysis-{'a' * 8}.json").write_text(json.dumps(found.to_dict()), encoding="utf-8")
    return root / "run-test"


def make_truth(root: Path) -> tuple[Path, Path]:
    truth = {"units": [{"text": SENTENCES[0][0], "ranges": [[6.5, 11.5]], "kind": "footage"},
                       {"text": SENTENCES[1][0], "ranges": [[18.5, 23.5]], "kind": "footage"},
                       {"text": SENTENCES[2][0], "ranges": [[30.5, 35.5]], "kind": "footage"}]}
    path = root / "gt.json"
    path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    lock = root / "gt.lock"
    lock.write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "  gt.json\n", encoding="utf-8")
    return path, lock


class W3ToolTests(unittest.TestCase):
    def test_spoken_lengths_are_read_back_from_the_unit_bounds(self):
        _, total = script.captions(SENTENCES)
        units = align.units(SENTENCES, total, lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS)
        spoken, narration = w3.spoken_of([{"text": u.text, "start_ms": u.start_ms, "end_ms": u.end_ms} for u in units])
        self.assertEqual(spoken, SENTENCES)
        self.assertEqual(narration, total)
        one = align.units(SENTENCES[:1], script.captions(SENTENCES[:1])[1], lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS)
        self.assertEqual(w3.spoken_of([{"text": u.text, "start_ms": u.start_ms, "end_ms": u.end_ms} for u in one])[0], SENTENCES[:1])

    def test_a_changed_ground_truth_is_refused(self):
        with TemporaryDirectory() as tmp:
            path, lock = make_truth(Path(tmp))
            w3.load_gt(path, lock)
            path.write_text(path.read_text(encoding="utf-8").replace("6.5", "6.0"), encoding="utf-8")
            with self.assertRaises(SystemExit):
                w3.load_gt(path, lock)

    def test_replay_gives_the_saved_plan(self):
        with TemporaryDirectory() as tmp:
            run = w3.load_run(make_run(Path(tmp)))
            again = w3.replay(run)
            self.assertTrue(w3.same_plan(run.plans["i1"], again.plans["i1"]))
            self.assertEqual([u["review"] for u in run.logs["i1"]["units"]], [u["review"] for u in again.logs["i1"]["units"]])

    def test_score_and_attribution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            scored = w3.score(gt, run)
            self.assertEqual(scored["units_scored"], 3)
            self.assertEqual(scored["recall_at_5"][1], 3)
            found = w3.attribute(gt, run)
            stages = {r["unit"]: r["stage"] for r in found["rows"]}
            self.assertEqual(stages["i1.u1"], "OK")  # the map with "1500 MILES"
            self.assertEqual(stages["i1.u2"], "OK")
            # the tank shot inside the truth was never looked at: the unit's miss is lost at acquisition
            self.assertEqual(stages["i1.u3"], "R3_NOT_LOOKED")
            self.assertEqual(sum(found["stages"].values()), 3)
            for row in found["rows"]:
                self.assertIn(row["r7"], ("SILENT_MISS", "FLAGGED_MISS", "FLAGGED_HIT", "READY_HIT"))

    def test_oracle_evidence_only_changes_a_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            before = json.dumps(run.logs, sort_keys=True)
            oracle = w3.oracle_evidence(gt, run, only_looked=False)
            self.assertEqual(json.dumps(run.logs, sort_keys=True), before)
            self.assertGreaterEqual(w3.score(gt, oracle)["chosen_hit"][0], w3.score(gt, w3.replay(run))["chosen_hit"][0])
            self.assertIn("i1-s1c6", oracle.logs["i1"]["units"][2]["chosen"])  # the shot nobody looked at, told by the oracle

    def test_pooled_looks_fill_only_unlooked_shots(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            donor = w3.load_run(make_run(root / "donor"))
            tank = next(c for c in donor.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
            tank.setdefault("evidence", {"candidate_id": "i1-s1c6", "items": []})["items"].append(
                {"origin": "vision", "confidence": 850, "text": "Một chiếc xe tăng lăn bánh qua cánh đồng", "labels": ["action"], "detail": "AI donor"})
            again = w3.pooled(run, [donor])
            self.assertEqual(again.spec["_pooled_shots_added"], 1)
            self.assertIn("i1-s1c6", again.logs["i1"]["units"][2]["chosen"])

    def test_variability_and_hint(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            same = w3.variability([run, run])
            self.assertEqual(same["identical_text"], same["looked_in_all"])
            near = w3.hint(gt, run, 6.0)
            self.assertEqual(near["units"], 3)


class ReviewFindingsTests(unittest.TestCase):  # an independent review of the tool (#810)
    def test_an_empty_range_hits_nothing(self):
        shot = {"start_ms": 0, "end_ms": 5000}
        self.assertFalse(w3.hits(shot, [[12.0, 12.0]]))
        self.assertFalse(w3.hits({"start_ms": 3000, "end_ms": 3000}, [[0.0, 6.0]]))
        self.assertTrue(w3.hits(shot, [[1.0, 2.0]]))

    def test_the_spec_names_the_kept_sound_keep_source_audio(self):
        spec = {"items": [{"item_id": "i1", "title": "T", "keep_source_audio": "off", "sources": []}]}
        self.assertEqual(w3.spec_part(spec, "i1")["keep"], "off")

    def test_a_replay_with_other_evidence_keeps_that_evidence_in_a_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            oracle = w3.oracle_evidence(gt, run, only_looked=False)
            told = next(c for c in oracle.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
            self.assertIn("oracle", [i["detail"] for i in told["evidence"]["items"]])  # what was planned with
            self.assertIsNot(oracle.logs["i1"]["candidates"], run.logs["i1"]["candidates"])
            # attribution on the oracle run reads the oracle's evidence: the never-looked tank shot is looked at now
            self.assertEqual({r["unit"]: r["stage"] for r in w3.attribute(gt, oracle)["rows"]}["i1.u3"], "OK")
            self.assertEqual(w3.attribute(gt, run)["multi_source_parts"], [])

    def test_out_and_json_are_written(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = make_run(root)
            gt, lock = make_truth(root)
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                w3.main(["replay", str(gt), str(lock), str(folder), "--out", str(root / "again")])
                w3.main(["variability", str(folder), str(folder), "--json", str(root / "v.json")])
            self.assertTrue((root / "again" / "plan-i1.json").is_file())
            self.assertEqual(json.loads((root / "v.json").read_text(encoding="utf-8"))["looked_in_all"], len(TEXTS))


if __name__ == "__main__":
    unittest.main()
