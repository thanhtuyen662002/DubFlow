"""The offline human-guided timestamp tool (#832) on a run folder made here by the planner itself.

Every number below comes from a synthetic one-part run (``test_w3.make_run``) and synthetic form rows. They test the
code only: they are not study results and not participant data.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import csv
import io
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.mastering.longform import hintexp, w3
from tests.mastering.longform.test_w3 import SENTENCES, make_run, make_truth

FORMS = Path(__file__).resolve().parents[3] / "docs" / "mastering" / "human-timestamp-test" / "forms"


def truth_with_missing(root: Path) -> tuple[Path, Path]:
    """Sentences 1 and 2 have footage; sentence 3 is marked as having none."""
    truth = {"units": [{"text": SENTENCES[0][0], "ranges": [[6.5, 11.5]], "kind": "footage"},
                       {"text": SENTENCES[1][0], "ranges": [[18.5, 23.5]], "kind": "footage"},
                       {"text": SENTENCES[2][0], "ranges": [], "kind": "missing"}]}
    path = root / "gt-missing.json"
    path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    lock = root / "gt-missing.lock"
    lock.write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "  gt-missing.json\n", encoding="utf-8")
    return path, lock


# the synthetic run: shot n covers [6(n-1)+0.24, 6n-0.24] s; the truth of u1 is c2, of u2 is c4
RIGHT = {"i1.u1": ("0:09", "i1-s1c2"), "i1.u2": ("0:21", "i1-s1c4")}


def form_rows(*, ts_time: float = 30, rp_time: float = 60, ts_on_missing: str = "", participants: int = 8) -> list[dict]:
    """Synthetic filled rows: each person does each sentence of each video once, in one condition only."""
    rows = []
    for p in range(participants):
        for video in ("v1", "v2"):
            for n, unit in enumerate(("i1.u1", "i1.u2", "i1.u3")):
                cond = "RP" if (p + n) % 2 == 0 else "TS"
                row = {"participant_code": f"P{p:02d}", "video": video, "unit_id": unit, "condition": cond,
                       "total_s": str(ts_time if cond == "TS" else rp_time), "actions": "4", "re_searches": "0", "assisted": ""}
                if unit in RIGHT:
                    hint, shot = RIGHT[unit]
                    row.update(chosen_id=shot, marked_missing="")
                else:
                    shot = ts_on_missing if cond == "TS" else ""
                    row.update(chosen_id=shot, marked_missing="" if shot else "có")
                    hint = "0:33"
                if cond == "TS":
                    row.update(ts_hint=hint, ts_precision_s="5")
                rows.append(row)
    return rows


class StudyTests(unittest.TestCase):
    def test_every_group_is_reported_apart_and_the_output_is_stable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            first = hintexp.dumps(hintexp.study(gt, {"rcX": run}))
            again = hintexp.dumps(hintexp.study(gt, {"rcX": w3.load_run(make_run(root / "again"))}))
            self.assertEqual(first, again)
            res = json.loads(first)["runs"]["rcX"]
            self.assertEqual(len(res), 2 + 4 * (1 + 3 * len(hintexp.ERRS)))  # BASE, POS error, then 4 rankings x groups
            for tag in ("rel", "dist", "rel+wf", "dist+wf"):
                self.assertIn(f"ORACLE5:{tag}", res)
                for e in hintexp.ERRS:
                    for group in ("SIM", "SIMworst", "POS"):
                        self.assertIn(f"{group}{e}:{tag}", res)
            scored = w3.score(gt, run)
            self.assertEqual(res["BASE"]["r5"], scored["recall_at_5"][0])
            self.assertEqual(res["BASE"]["footage"], 3)
            self.assertEqual(set(res["SIM5:rel"]["r5"]), {"mean", "min", "max"})  # a simulation is a spread over seeds
            # no sentence text, no file name, no local path in the output
            for sentence, _ in SENTENCES:
                self.assertNotIn(sentence, first)
            self.assertNotIn(str(root), first)
            self.assertNotIn("film.webm", first)

    def test_the_position_hint_never_reads_the_truth(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            ctx = hintexp.Ctx(run)
            part, u = ctx.found[w3.norm(SENTENCES[0][0])]
            narration = [x for x in run.logs[part]["units"] if x.get("function", "narration") == "narration"]
            total = max(x["end_ms"] for x in narration)
            # no trim in the spec: the shots' extent stands in for the part's source range
            start = min(c["start_ms"] for c in run.logs[part]["candidates"])
            end = max(c["end_ms"] for c in run.logs[part]["candidates"])
            frac = ((u["start_ms"] + u["end_ms"]) / 2) / total
            self.assertAlmostEqual(ctx.pos_hint(part, u), start + frac * (end - start))
            # two different truths give the same POS groups' marks; only their scores may differ
            near = hintexp.study(w3.load_gt(*make_truth(root)), {"x": run})["runs"]["x"]
            (root / "far").mkdir()
            far = hintexp.study(w3.load_gt(*make_truth(root / "far", third=[[50.5, 55.5]])), {"x": run})["runs"]["x"]
            for e in hintexp.ERRS:
                self.assertEqual(near[f"POS{e}:rel"]["marks"], far[f"POS{e}:rel"]["marks"])
                self.assertEqual(near[f"POS{e}:rel"]["mean_shots_in_window"], far[f"POS{e}:rel"]["mean_shots_in_window"])

    def test_a_sentence_without_footage_is_counted_when_a_window_would_give_it_a_shot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*truth_with_missing(root))
            res = hintexp.study(gt, {"x": run})["runs"]["x"]
            self.assertEqual(res["BASE"]["footage"], 2)
            self.assertEqual(res["ORACLE5:rel"]["missing_given_a_pick"], 1)
            self.assertEqual(res["ORACLE5:rel"]["marks"], 3)
            # in the workflow a READY unit keeps the production pick and gets no mark
            self.assertEqual(res["ORACLE5:rel+wf"]["marks"], 0)


class SuggestTests(unittest.TestCase):
    def test_suggestions_stay_inside_the_window_and_rank_by_relevance(self):
        with TemporaryDirectory() as tmp:
            run = w3.load_run(make_run(Path(tmp)))
            got = hintexp.suggest(run, "i1.u2", hintexp.parse_time("0:21"), 5)
            self.assertEqual(got[0]["id"], "i1-s1c4")  # the shot the vision text matches
            self.assertEqual([g["rank"] for g in got], list(range(1, len(got) + 1)))
            for g in got:
                self.assertLess(g["start_s"], 26)
                self.assertGreater(g["end_s"], 16)
                self.assertEqual(set(g), {"rank", "id", "source", "start_s", "end_s"})  # ids and times only
            self.assertLessEqual(len(hintexp.suggest(run, "i1.u2", 30_000, 60)), hintexp.SUGGEST_K)
            self.assertEqual(hintexp.suggest(run, "i1.u2", 21_000, 5, source="other"), [])
            with self.assertRaises(KeyError):
                hintexp.suggest(run, "i9.u9", 21_000, 5)

    def test_help_is_printed_for_every_command_without_needing_a_run(self):
        for argv in (["--help"], ["suggest", "--help"], ["study", "-h"], ["score-human", "--help"]):
            with self.subTest(argv=argv):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(hintexp.main(argv), 0)
                self.assertIn("suggest", out.getvalue())
                self.assertNotIn("missing", out.getvalue())

    def test_timestamps_and_errors(self):
        self.assertEqual(hintexp.parse_time("1:05"), 65_000)
        self.assertEqual(hintexp.parse_time("1:02:03"), 3_723_000)
        self.assertEqual(hintexp.parse_time("12.5"), 12_500)
        for bad in ("", "a:b", "1:2:3:4", "-5"):
            with self.assertRaises(ValueError):
                hintexp.parse_time(bad)
        self.assertEqual(hintexp.error_s(9_000, [[6.5, 11.5]]), 0)
        self.assertEqual(hintexp.error_s(20_000, [[6.5, 11.5], [30, 31]]), 8.5)


class ScoreHumanTests(unittest.TestCase):
    def score(self, rows: list[dict]) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = {v: w3.load_run(make_run(root / v)) for v in ("v1", "v2")}
            gt = w3.load_gt(*truth_with_missing(root))
            return hintexp.score_human(rows, {"v1": gt, "v2": gt}, runs)

    def test_the_form_has_every_column_the_scorer_reads(self):
        with open(FORMS / "units.csv", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        for column in ("participant_code", "video", "unit_id", "condition", "ts_hint", "ts_precision_s", "chosen_id",
                       "marked_missing", "total_s", "actions", "re_searches", "assisted"):
            self.assertIn(column, header)

    def test_too_few_sentences_or_one_video_is_inconclusive(self):
        result = self.score(form_rows()[:6])
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("< 20" in r for r in result["reasons"]))
        one_video = [r for r in form_rows(participants=12) if r["video"] == "v1"]
        result = self.score(one_video)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("videos" in r for r in result["reasons"]))

    def test_a_faster_equally_right_timestamp_flow_is_a_success(self):
        result = self.score(form_rows())
        self.assertEqual(result["label"], "HUMAN (real participants)")
        self.assertEqual(result["verdict"], "SUCCESS", result["reasons"])
        ts, rp = result["TS"], result["RP"]
        self.assertEqual(ts["timestamp_error_s"]["median"], 0)
        self.assertEqual(ts["r5"], ts["footage"])
        self.assertEqual(ts["missing_detected"], ts["missing"])
        self.assertEqual(rp["chosen_right_footage"], rp["footage"])
        self.assertNotIn("timestamp_error_s", rp)

    def test_giving_a_shot_to_a_sentence_without_footage_is_negative(self):
        result = self.score(form_rows(ts_on_missing="i1-s1c6"))
        self.assertEqual(result["verdict"], "NEGATIVE")
        self.assertGreater(result["TS"]["assigned_on_missing"], result["RP"]["assigned_on_missing"])

    def test_a_slower_flow_with_no_gain_is_negative(self):
        result = self.score(form_rows(ts_time=90, rp_time=60))
        self.assertEqual(result["verdict"], "NEGATIVE")

    def test_a_timestamp_row_marked_without_footage_needs_no_timestamp(self):
        rows = form_rows()
        for row in rows:
            if row["condition"] == "TS" and row["marked_missing"]:
                row["ts_hint"] = row["ts_precision_s"] = ""
        result = self.score(rows)
        self.assertEqual(result["deviations"], [])
        self.assertEqual(result["verdict"], "SUCCESS", result["reasons"])
        self.assertEqual(result["TS"]["missing_detected"], result["TS"]["missing"])
        for row in rows:
            if row["condition"] == "TS" and not row["marked_missing"]:
                row["ts_hint"] = ""
                break
        result = self.score(rows)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("without a timestamp" in d for d in result["deviations"]))

    def test_an_ai_playing_the_participant_never_gets_a_human_verdict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = {v: w3.load_run(make_run(root / v)) for v in ("v1", "v2")}
            gt = w3.load_gt(*truth_with_missing(root))
            result = hintexp.score_human(form_rows(), {"v1": gt, "v2": gt}, runs, ai_proxy=True)
        self.assertEqual(result["label"], "AI PROXY TEST \u2014 EXPLORATORY, NOT HUMAN VALIDATION")
        self.assertEqual(result["verdict"], "EXPLORATORY")
        self.assertEqual(result["protocol_reading"], "SUCCESS")
        self.assertNotIn("HUMAN (real", json.dumps(result))

    def test_the_same_person_on_the_same_sentence_twice_voids_the_verdict(self):
        rows = form_rows()
        rows.append(dict(rows[0], condition="TS" if rows[0]["condition"] == "RP" else "RP", ts_hint="0:09", ts_precision_s="5"))
        result = self.score(rows)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("twice" in d for d in result["deviations"]))

    def test_the_command_line_writes_no_text_or_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gt, lock = truth_with_missing(root)
            runs = [make_run(root / v) for v in ("v1", "v2")]
            units = root / "units.csv"
            rows = form_rows()
            with open(units, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=sorted({k for r in rows for k in r}))
                writer.writeheader()
                writer.writerows(rows)
            out = root / "human.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hintexp.main(["score-human", "--units", str(units), "--gt", f"v1={gt},{lock}", "--gt", f"v2={gt},{lock}",
                                               "--run", f"v1={runs[0]}", "--run", f"v2={runs[1]}", "--out", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(json.loads(text)["verdict"], "SUCCESS")
            study = root / "study.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hintexp.main(["study", "--gt", str(gt), "--lock", str(lock), "--run", f"rcX={runs[0]}",
                                               "--out", str(study)]), 0)
            for written in (text, study.read_text(encoding="utf-8")):
                self.assertNotIn(str(root), written)
                for sentence, _ in SENTENCES:
                    self.assertNotIn(sentence, written)


if __name__ == "__main__":
    unittest.main()
