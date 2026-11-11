"""Runs the fixed editorial regression scenarios (``dev.json``, ``holdout.json``) through the aligner and scores them.

A scenario is footage (candidates with what a vision model or a note said about them), a narration (sentences and
their spoken length) and ground truth (``dubflow.mastering.quality.Truth``). The ``holdout`` scenarios were written
before the Phase 2 hardening changes and are never used to tune thresholds: report them, do not fit them.

A scenario may say more than the middle keyframe shows (``HOLDOUT5.md``): ``frames`` (what other frames of a shot
show, and whether they are usable) and ``transcript`` (the source's own speech in the shot). The runner plays the
planner's part honestly: a shot's text is what its keyframe shows (the middle one, or the fallback keyframe the
candidate step picks when the middle one is unusable), and other frames are learnt only for the shots
``frames.wanted`` asks about after a first plan, at the times ``frames.extra_times`` gives; what a frame at time t
shows is the scenario's nearest described frame.

``python -m tests.mastering.regression.runner [dev|holdout|all|<file>]`` prints one line per scenario and the totals.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping

from engine.dubflow.mastering import align, evidence as ev, frames as more, policy as rules, quality
from engine.dubflow.mastering.candidates import Candidate, Quality

HERE = Path(__file__).resolve().parent


def load(split: str) -> list[dict[str, Any]]:
    names = {"all": ["dev", "dev2", "dev3", "holdout", "holdout2", "holdout4"], "dev": ["dev", "dev2", "dev3"],
             "holdout": ["holdout", "holdout2", "holdout4"]}.get(split, [split])
    return [s for name in names for s in json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))]


def seen_at(item: Mapping[str, Any], at_ms: int) -> tuple[str, bool]:
    """What the frame of ``item`` at ``at_ms`` shows: the nearest described frame (the keyframe is the middle)."""
    middle = (item["start_ms"] + item["end_ms"]) // 2
    known = [(middle, item["text"], item.get("usable", True))]
    known += [(f["at_ms"], f["text"], f.get("usable", True)) for f in item.get("frames", ())]
    _, text, usable = min(known, key=lambda k: (abs(k[0] - at_ms), k[0]))
    return text, usable


def keyframe(item: Mapping[str, Any]) -> tuple[str, bool]:
    """The candidate step's keyframe: the middle frame, or the first usable fallback frame when it is unusable."""
    if item.get("usable", True) or not item.get("frames"):
        return item["text"], item.get("usable", True)
    for at in more.fallback_times(item["start_ms"], item["end_ms"]):
        text, usable = seen_at(item, at)
        if usable:
            return text, True
    return item["text"], False


def build(scenario: Mapping[str, Any]):
    candidates, found, stills, groups = [], {}, [], {}
    by_text: dict[str, str] = {}
    for index, item in enumerate(scenario["candidates"]):
        text, usable = keyframe(item)
        problems = () if usable else ("mờ, thiếu chi tiết",)
        candidates.append(Candidate(item["id"], item["source"], item["start_ms"], item["end_ms"], index,
                                    item.get("motion", 50), Quality(120, 50, 300, problems), index + 1, f"{item['id']}.jpg"))
        found[item["id"]] = ev.Evidence(item["id"])
        found[item["id"]].add(ev.Item("vision", item.get("confidence", 800), text))
        if item.get("transcript"):
            found[item["id"]].add(ev.Item("transcript", 850, item["transcript"], (), "phụ đề nguồn"))
        found[item["id"]].hook = item.get("hook", 0)
        if item.get("still"):
            stills.append(item["id"])
        groups[item["id"]] = by_text.setdefault(text, item["id"])  # the same picture twice is one group
    lead, gap = scenario.get("lead_ms", 0), scenario.get("gap_ms", 0)
    spoken = [(u["text"], u["ms"]) for u in scenario["units"]]
    total = lead + sum(ms for _, ms in spoken) + gap * (len(spoken) - 1)
    function = align.product_function if not scenario.get("forward_only") else None
    found_units = align.units(spoken, total, lead_ms=lead, gap_ms=gap, function=function)
    return candidates, found, stills, groups, found_units, total


def look_again(scenario: Mapping[str, Any], candidates, found, groups, decisions) -> tuple[list, int]:
    """The extra-frames step: the shots ``frames.wanted`` picks get two more frames each, merged or split."""
    items = {i["id"]: i for i in scenario["candidates"]}
    ids = more.wanted([d.to_dict() for d in decisions], candidates, found, (), forward_only=bool(scenario.get("forward_only")))
    if not ids:
        return candidates, 0
    out = []
    for candidate in candidates:
        if candidate.candidate_id not in ids:
            out.append(candidate)
            continue
        item = items[candidate.candidate_id]
        middle = found[candidate.candidate_id].summary() or item["text"]
        extra = []
        for at in more.extra_times(candidate):
            text, usable = seen_at(item, at)
            if usable:
                extra.append((at, ev.Item("vision", item.get("confidence", 800), text, (), f"khung {at} ms"), None, None))
        for part, evidence in more.merge(candidate, middle, extra, found.pop(candidate.candidate_id)):
            found[part.candidate_id] = evidence
            groups[part.candidate_id] = groups.get(candidate.candidate_id, candidate.candidate_id) if part is candidate \
                else part.candidate_id
            out.append(part)
    return out, 2 * len(ids)


def run(scenario: Mapping[str, Any]) -> dict[str, Any]:
    candidates, found, stills, groups, found_units, total = build(scenario)
    pacing = rules.STYLE_PACING[scenario.get("pacing", "natural")]
    forward = bool(scenario.get("forward_only"))
    choices, decisions, mode = align.assign(found_units, candidates, found, pacing, forward_only=forward, groups=groups, stills=stills)
    candidates, looked = look_again(scenario, candidates, found, groups, decisions)
    if looked:
        choices, decisions, mode = align.assign(found_units, candidates, found, pacing, forward_only=forward, groups=groups,
                                                stills=stills)
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
            "pieces": [(p.unit_id, p.source_id, p.src_start, p.src_end, p.length) for p in pieces], "extra_frames": looked,
            "group": scenario.get("group", ""), "decisions": [d.to_dict() for d in decisions]}


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
    for name in ("dev-", "dev2-", "dev3-", "holdout-", "holdout2-", "holdout4-", "holdout5-", "holdout6-"):
        mine = [r["alignment"] for r in results if r["name"].startswith(name)]
        if mine:
            need = sum(u["need_ms"] for a in mine for u in a["units"] if u["footage_exists"])
            good = sum(u["accepted_ms"] for a in mine for u in a["units"] if u["footage_exists"])
            print(f"{name}: coverage {good * 100 / max(1, need):.1f} % of {need} ms, units ok "
                  f"{sum(a['units_ok'] for a in mine)}/{sum(a['units_total'] for a in mine)}, silent misses "
                  f"{sum(a['silent_misses'] for a in mine)}, false alarms {sum(a['false_alarms'] for a in mine)}, stolen {sum(a['stolen_key_ms'] for a in mine)} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
