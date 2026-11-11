"""The Gate S report of one regression split: every metric the owner's directive of 2026-11-11 asks for.

``python -m tests.mastering.regression.evaluate <split> [--json PATH]`` runs the split through ``runner.run`` (the
same code the frozen regression test uses) and prints:

* coverage by duration (accepted ms of the sentences whose footage exists ÷ their spoken ms), per video, per
  content group and in total; sentences ok;
* silent misses (footage exists, under the bar, not flagged), false alarms (flagged though fine), contra ms (footage
  the truth says contradicts the sentence), stolen ms (a later sentence's key shot used early), repeats and every
  other timeline problem (``quality.timeline_problems``);
* evidence states against the truth: for sentences with footage, how many the planner said MATCH_AVAILABLE,
  MATCH_NOT_FOUND or SOURCE_INSUFFICIENT; for sentences without footage (``footage_exists: false``), the same —
  the right answer there is to flag it (NOT_FOUND or INSUFFICIENT), never a silent READY;
* the frame budget: keyframes (one per shot) plus the extra frames the multi-frame step described, which are the
  vision calls of the run (the AI call budget is one call per described frame, batched in the app);
* calibration: of the sentences the planner did not flag, how many are right (must be all: that is "no silent
  miss"); of the flagged ones, how many were really wrong (the precision of a review flag).

Nothing here changes a threshold or a label: it only reads results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from engine.dubflow.mastering import review as reviewing
from engine.dubflow.mastering.quality import GOOD_UNIT
from . import runner


def _evidence(decision: Mapping[str, Any], mode: str) -> str:
    # In a scenario every shot has what its keyframe shows, so "every usable shot was looked at" holds; the review
    # module decides the state exactly as it does in the app.
    log = {"mode": mode, "candidates": [{"quality": {}, "evidence": {"items": [{"origin": "vision", "text": "x"}]}}]}
    return reviewing.evidence_state(decision, log)


def evaluate(scenarios: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    videos, groups = [], {}
    states = {"with_footage": {}, "without_footage": {}}
    totals = {"need_ms": 0, "accepted_ms": 0, "units": 0, "units_ok": 0, "silent_misses": 0, "false_alarms": 0, "contra_ms": 0,
              "contra_unflagged_ms": 0, "stolen_key_ms": 0, "keyframes": 0, "extra_frames": 0, "flagged": 0, "flagged_wrong": 0,
              "unflagged": 0, "unflagged_right": 0, "without_footage": 0, "without_footage_flagged": 0}
    problems: dict[str, int] = {}
    for scenario in scenarios:
        result = runner.run(scenario)
        a = result["alignment"]
        decisions = {d["unit_id"]: d for d in result["decisions"]}
        need = sum(u["need_ms"] for u in a["units"] if u["footage_exists"])
        good = sum(u["accepted_ms"] for u in a["units"] if u["footage_exists"])
        row = {"name": result["name"], "group": result["group"], "need_ms": need, "accepted_ms": good,
               "coverage": round(good * 100 / need, 1) if need else None, "units_ok": a["units_ok"], "units": a["units_total"],
               "silent_misses": a["silent_misses"], "false_alarms": a["false_alarms"], "contra_ms": a["contra_ms"],
               "stolen_key_ms": a["stolen_key_ms"], "problems": sorted({p["code"] for p in result["problems"]}),
               "keyframes": len(scenario["candidates"]), "extra_frames": result["extra_frames"], "mode": result["mode"]}
        videos.append(row)
        g = groups.setdefault(result["group"] or "-", {"need_ms": 0, "accepted_ms": 0, "units": 0, "units_ok": 0, "silent_misses": 0,
                                                     "videos": 0})
        g["need_ms"] += need; g["accepted_ms"] += good; g["units"] += a["units_total"]; g["units_ok"] += a["units_ok"]
        g["silent_misses"] += a["silent_misses"]; g["videos"] += 1
        for p in result["problems"]:
            problems[p["code"]] = problems.get(p["code"], 0) + 1
        totals["need_ms"] += need; totals["accepted_ms"] += good; totals["units"] += a["units_total"]; totals["units_ok"] += a["units_ok"]
        for key in ("silent_misses", "false_alarms", "contra_ms", "stolen_key_ms"):
            totals[key] += a[key]
        totals["keyframes"] += row["keyframes"]; totals["extra_frames"] += row["extra_frames"]
        for unit in a["units"]:
            decision = decisions.get(unit["unit"], {})
            state = _evidence(decision, result["mode"]) if decision else "NO_DECISION"
            bucket = states["with_footage" if unit["footage_exists"] else "without_footage"]
            bucket[state] = bucket.get(state, 0) + 1
            if unit["contra_ms"] and not unit["flagged"]:
                totals["contra_unflagged_ms"] += unit["contra_ms"]
            if not unit["footage_exists"]:
                totals["without_footage"] += 1
                totals["without_footage_flagged"] += unit["flagged"]
                continue
            right = unit["coverage_milli"] >= GOOD_UNIT and unit["contra_ms"] == 0
            if unit["flagged"]:
                totals["flagged"] += 1; totals["flagged_wrong"] += not right
            else:
                totals["unflagged"] += 1; totals["unflagged_right"] += right
    for g in groups.values():
        g["coverage"] = round(g["accepted_ms"] * 100 / g["need_ms"], 1) if g["need_ms"] else None
    totals["coverage"] = round(totals["accepted_ms"] * 100 / totals["need_ms"], 1) if totals["need_ms"] else None
    totals["flag_precision"] = round(totals["flagged_wrong"] * 100 / totals["flagged"], 1) if totals["flagged"] else None
    totals["unflagged_right_share"] = round(totals["unflagged_right"] * 100 / totals["unflagged"], 1) if totals["unflagged"] else None
    return {"videos": videos, "groups": groups, "totals": totals, "evidence_states": states, "problems": problems}


def markdown(report: Mapping[str, Any], title: str) -> str:
    t = report["totals"]
    out = [f"### {title}", "",
           f"Coverage by duration **{t['coverage']} %** ({t['accepted_ms']} of {t['need_ms']} ms), sentences ok {t['units_ok']}/{t['units']}, "
           f"silent misses **{t['silent_misses']}**, false alarms {t['false_alarms']}, contra {t['contra_ms']} ms "
           f"(unflagged {t['contra_unflagged_ms']} ms), stolen {t['stolen_key_ms']} ms.", "",
           f"Frames: {t['keyframes']} keyframes + {t['extra_frames']} extra frames described. Calibration: unflagged sentences right "
           f"{t['unflagged_right']}/{t['unflagged']}; flagged sentences really wrong {t['flagged_wrong']}/{t['flagged']}; sentences "
           f"without footage flagged {t['without_footage_flagged']}/{t['without_footage']}.", "",
           "| Video | Group | Coverage | Ok | Silent | False alarm | Contra ms | Stolen ms | Extra frames | Problems |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for v in report["videos"]:
        out.append(f"| {v['name']} | {v['group'] or '-'} | {v['coverage']} % | {v['units_ok']}/{v['units']} | {v['silent_misses']} | "
                   f"{v['false_alarms']} | {v['contra_ms']} | {v['stolen_key_ms']} | {v['extra_frames']} | {', '.join(v['problems']) or '-'} |")
    out += ["", "| Group | Videos | Coverage | Ok | Silent |", "|---|---:|---:|---:|---:|"]
    for name, g in sorted(report["groups"].items()):
        out.append(f"| {name} | {g['videos']} | {g['coverage']} % | {g['units_ok']}/{g['units']} | {g['silent_misses']} |")
    states = report["evidence_states"]
    out += ["", f"Evidence states — footage exists: {json.dumps(states['with_footage'], sort_keys=True)}; "
                f"footage does not exist: {json.dumps(states['without_footage'], sort_keys=True)}.",
            f"Timeline problems: {json.dumps(report['problems'], sort_keys=True) if report['problems'] else 'none'}.", ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.mastering.regression.evaluate")
    parser.add_argument("split")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(runner.load(args.split))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(markdown(report, args.split))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
