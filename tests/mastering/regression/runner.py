"""Runs the fixed editorial regression scenarios (``dev.json``, ``holdout.json``) through the aligner and scores them.

A scenario is footage (candidates with what a vision model or a note said about them), a narration (sentences and
their spoken length) and ground truth (``dubflow.mastering.quality.Truth``). The ``holdout`` scenarios were written
before the Phase 2 hardening changes and are never used to tune thresholds: report them, do not fit them.

``python -m tests.mastering.regression.runner [dev|holdout|all]`` prints one line per scenario and the totals.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping

from engine.dubflow.mastering import align, evidence as ev, policy as rules, quality
from engine.dubflow.mastering.candidates import Candidate, Quality

HERE = Path(__file__).resolve().parent


def load(split: str) -> list[dict[str, Any]]:
    names = ["dev", "holdout"] if split == "all" else [split]
    return [s for name in names for s in json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))]


def build(scenario: Mapping[str, Any]):
    candidates, found, stills, groups = [], {}, [], {}
    by_text: dict[str, str] = {}
    for index, item in enumerate(scenario["candidates"]):
        problems = () if item.get("usable", True) else ("mờ, thiếu chi tiết",)
        candidates.append(Candidate(item["id"], item["source"], item["start_ms"], item["end_ms"], index,
                                    item.get("motion", 50), Quality(120, 50, 300, problems), index + 1))
        found[item["id"]] = ev.Evidence(item["id"])
        found[item["id"]].add(ev.Item("vision", item.get("confidence", 800), item["text"]))
        found[item["id"]].hook = item.get("hook", 0)
        if item.get("still"):
            stills.append(item["id"])
        groups[item["id"]] = by_text.setdefault(item["text"], item["id"])  # the same picture twice is one group
    lead, gap = scenario.get("lead_ms", 0), scenario.get("gap_ms", 0)
    spoken = [(u["text"], u["ms"]) for u in scenario["units"]]
    total = lead + sum(ms for _, ms in spoken) + gap * (len(spoken) - 1)
    function = align.product_function if not scenario.get("forward_only") else None
    found_units = align.units(spoken, total, lead_ms=lead, gap_ms=gap, function=function)
    return candidates, found, stills, groups, found_units, total


def run(scenario: Mapping[str, Any]) -> dict[str, Any]:
    candidates, found, stills, groups, found_units, total = build(scenario)
    pacing = rules.STYLE_PACING[scenario.get("pacing", "natural")]
    choices, decisions, mode = align.assign(found_units, candidates, found, pacing, forward_only=bool(scenario.get("forward_only")),
                                            groups=groups, stills=stills)
    pieces = quality.pieces_from_choices(choices, stills=stills)
    flagged: dict[str, bool] = {}
    for choice in choices:
        flagged[choice.unit_id] = flagged.get(choice.unit_id, False) or choice.needs_review
    for decision in decisions:  # a unit the aligner says is not fully covered is flagged too
        coverage = decision.to_dict().get("coverage")
        if coverage and coverage.get("needs_review"):
            flagged[decision.unit.unit_id] = True
    truth = quality.Truth.from_dict(scenario["truth"])
    return {"name": scenario["name"], "split": scenario["split"], "mode": mode,
            "problems": quality.timeline_problems(pieces, duration_ms=total, forward_only=bool(scenario.get("forward_only"))),
            "alignment": quality.alignment(pieces, truth, flagged=flagged),
            "pieces": [(p.unit_id, p.source_id, p.src_start, p.src_end, p.length) for p in pieces]}


def line(result: Mapping[str, Any]) -> str:
    a = result["alignment"]
    codes = sorted({p["code"] for p in result["problems"]})
    return (f"{result['name']:<28} coverage {a['coverage_milli'] / 10:5.1f} %  units ok {a['units_ok']}/{a['units_total']}  "
            f"illustrated {a['units_illustrated']}/{a['units_with_footage']}  contra {a['contra_ms']} ms  "
            f"stolen {a['stolen_key_ms']} ms  silent misses {a['silent_misses']}  false alarms {a['false_alarms']}  problems {','.join(codes) or '-'}")


def main(argv: list[str]) -> int:
    split = argv[0] if argv else "all"
    results = [run(s) for s in load(split)]
    for result in results:
        print(line(result))
    for name in ("dev", "holdout"):
        mine = [r["alignment"] for r in results if r["split"] == name]
        if mine:
            need = sum(u["need_ms"] for a in mine for u in a["units"] if u["footage_exists"])
            good = sum(u["accepted_ms"] for a in mine for u in a["units"] if u["footage_exists"])
            print(f"{name}: coverage {good * 100 / max(1, need):.1f} % of {need} ms, units ok "
                  f"{sum(a['units_ok'] for a in mine)}/{sum(a['units_total'] for a in mine)}, silent misses "
                  f"{sum(a['silent_misses'] for a in mine)}, false alarms {sum(a['false_alarms'] for a in mine)}, stolen {sum(a['stolen_key_ms'] for a in mine)} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
