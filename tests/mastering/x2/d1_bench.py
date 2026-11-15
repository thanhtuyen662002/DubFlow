"""D1 (#867) engineering benchmark: independent planner-level fixtures with a known picture and known cues ($0).

ENGINEERING_FIXTURE_ONLY = TRUE. The scenarios are written by hand to test the experimental cue in-point
(``align.assign(..., cue_inpoint_lead_ms=...)``). Nothing here is REAL-DEV, REAL-HOLDOUT or a human label.

Unlike ``solo_bench`` (a real video through the whole chain, where the speech always describes its own shot), every
scenario here separates three things, so that one is never read as another:

1. *candidate contains the cue*: the cue lies inside the shot the planner chose;
2. *played span overlaps the cue*: the piece that plays covers some of the cue;
3. *picture is right*: by the scenario's picture truth (``picture``: what is visible from when to when, which can
   change inside one shot, e.g. a pan the cut detector does not split), the share of the played time that shows
   what the sentence is about.

(2) is never evidence of (3). Each scenario runs with the experiment off (production default) and on, in both
reorder and forward-only modes. Measured per unit and in total: top-1 picture, played-over-cue, start offset,
picture-correct ms, no-footage assignment, review state (READY must not change), the timeline problems of
``quality.timeline_problems`` and the total duration.
"""
from __future__ import annotations

import json, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align, evidence as ev, quality, review as rv  # noqa: E402
from engine.dubflow.mastering import policy as rules  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate, Quality  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
LEAD_MS = 300  # the value under test, not a proven constant


@dataclass(frozen=True)
class Shot:
    sid: str
    start_ms: int
    end_ms: int
    seen: str = ""  # what a vision description of its keyframe would say ("" = never looked at)


@dataclass(frozen=True)
class Sentence:
    uid: str
    text: str
    shows: frozenset[str]  # picture truth tags that illustrate it (empty: no footage by construction)
    kind: str = "match"  # match | montage | entity | action | time | none


@dataclass
class Scenario:
    name: str
    about: str
    shots: list[Shot]
    cues: list[tuple[int, int, str]]
    picture: list[tuple[int, int, str]]  # (start ms, end ms, tag): what is visible
    sentences: list[Sentence]
    sentence_ms: int = 3000
    notes: dict[str, str] = field(default_factory=dict)


def _shots(*bounds: int, seen: Sequence[str] = ()) -> list[Shot]:
    return [Shot(f"s{i}", a, b, seen[i] if i < len(seen) else "") for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]


def _s(uid: str, text: str, *tags: str, kind: str = "match") -> Sentence:
    return Sentence(uid, text, frozenset(tags), kind)


def scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    # 1. speech and picture together: the words about each shot are said while it shows it, late in the shot
    out.append(Scenario(
        "sync", "lời nói và cảnh đúng đồng thời (cue nằm giữa/cuối cảnh)",
        _shots(0, 6000, 12000, 18000, 24000),
        [(3500, 5200, "The tractor crosses the river."), (9800, 11500, "The lighthouse flashes over the harbor."),
         (13500, 15200, "The glacier melts into the valley."), (22000, 23800, "The locomotive climbs the mountain.")],
        [(0, 6000, "tractor"), (6000, 12000, "lighthouse"), (12000, 18000, "glacier"), (18000, 24000, "locomotive")],
        [_s("u1", "We watch the tractor cross the river.", "tractor"),
         _s("u2", "The lighthouse flashes above the harbor.", "lighthouse"),
         _s("u3", "Then the glacier melts slowly.", "glacier"),
         _s("u4", "Finally the locomotive climbs the mountain.", "locomotive")]))
    # 2. the speech comes before its picture: the line about the volcano is said at the end of the shot before it
    out.append(Scenario(
        "speech_before_picture", "lời nói xuất hiện trước khi cảnh đúng bắt đầu",
        _shots(0, 6000, 12000, 18000),
        [(4300, 5900, "Next you will see the volcano erupt."), (10300, 11900, "Now the orchard blossoms.")],
        [(0, 6000, "harbor"), (6000, 12000, "volcano"), (12000, 18000, "orchard")],
        [_s("u1", "The volcano erupts on the island.", "volcano"),
         _s("u2", "The orchard blossoms on the hillside.", "orchard")]))
    # 3. the picture comes before the speech, inside one shot the cut detector does not split (a pan): the
    #    satellite is visible 0-5 s, the planet 5-10 s; the line about the satellite is said at 7 s
    out.append(Scenario(
        "picture_before_speech", "cảnh đúng xuất hiện trước lời nói, trong cùng một shot (pan không bị cắt)",
        _shots(0, 10000, 16000),
        [(7000, 8600, "That satellite orbits far above us."), (12500, 14000, "Down in the ocean the submarine dives.")],
        [(0, 5000, "satellite"), (5000, 10000, "planet"), (10000, 16000, "submarine")],
        [_s("u1", "A satellite orbits above the planet.", "satellite"),
         _s("u2", "The submarine dives into the ocean.", "submarine")]))
    # 3b. the same, where a vision description of the shot's keyframe already says what the sentence is about: the
    #     speech is then not what makes it a match, so the experiment leaves the in-point alone
    out.append(Scenario(
        "picture_before_speech_seen", "như 3, nhưng mô tả hình của shot đã khớp câu (lời nói không phải lý do khớp)",
        _shots(0, 10000, 16000, seen=["a satellite orbits above the planet", "a submarine in the ocean"]),
        [(7000, 8600, "That satellite orbits far above us."), (12500, 14000, "Down in the ocean the submarine dives.")],
        [(0, 5000, "satellite"), (5000, 10000, "planet"), (10000, 16000, "submarine")],
        [_s("u1", "A satellite orbits above the planet.", "satellite"),
         _s("u2", "The submarine dives into the ocean.", "submarine")]))
    # 4. cues at the very start and the very end of the source
    out.append(Scenario(
        "cue_at_edges", "cue sát đầu hoặc cuối source",
        _shots(0, 6000, 12000),
        [(0, 1500, "The windmill spins in the meadow."), (11300, 12000, "The bridge sways.")],
        [(0, 6000, "windmill"), (6000, 12000, "bridge")],
        [_s("u1", "The windmill spins over the meadow.", "windmill"),
         _s("u2", "The bridge sways over the canyon.", "bridge")]))
    # 5. a cue much shorter than the piece
    out.append(Scenario(
        "short_cue", "cue ngắn hơn đoạn phát",
        _shots(0, 8000, 16000),
        [(5200, 5700, "Kangaroo!"), (9000, 9400, "Cathedral.")],
        [(0, 8000, "kangaroo"), (8000, 16000, "cathedral")],
        [_s("u1", "The kangaroo jumps across the desert.", "kangaroo"),
         _s("u2", "The cathedral glows at night.", "cathedral")]))
    # 6. several close cues in one shot; the sentence matches the second one best
    out.append(Scenario(
        "close_cues", "nhiều cue gần nhau trong một cảnh",
        _shots(0, 8000, 16000),
        [(1000, 2400, "Look at the harbor."), (2600, 4000, "The ferry leaves the harbor at dawn."),
         (4200, 5600, "Gulls follow it."), (9000, 10500, "The market opens.")],
        [(0, 8000, "ferry"), (8000, 16000, "market")],
        [_s("u1", "At dawn the ferry leaves the harbor.", "ferry"),
         _s("u2", "The market opens in the morning.", "market")]))
    # 7. the same words said over two shots; only one of them shows it (vision saw the other one is a crowd)
    out.append(Scenario(
        "repeated_cue", "cue lặp ở nhiều cảnh",
        _shots(0, 6000, 12000, 18000, seen=["crowd of people in a square", "", "crowd of people in a square"]),
        [(4000, 5500, "The balloon rises."), (8000, 9500, "The balloon rises."), (13000, 14500, "The balloon rises.")],
        [(0, 6000, "crowd"), (6000, 12000, "balloon"), (12000, 18000, "crowd")],
        [_s("u1", "The balloon rises over the town.", "balloon")]))
    # 8. no cue at all
    out.append(Scenario(
        "no_cue", "không có cue",
        _shots(0, 6000, 12000, seen=["a tractor on a river bank", "a lighthouse on a harbor"]),
        [],
        [(0, 6000, "tractor"), (6000, 12000, "lighthouse")],
        [_s("u1", "The tractor crosses the river.", "tractor"),
         _s("u2", "The lighthouse flashes over the harbor.", "lighthouse")]))
    # 9. a commentator talks about things the picture never shows (a talking head over every shot)
    out.append(Scenario(
        "commentary_unrelated", "nguồn có lời bình nhưng hình không liên quan",
        _shots(0, 6000, 12000),
        [(2000, 3600, "The glacier melts every summer."), (8000, 9600, "The volcano erupts again.")],
        [(0, 12000, "presenter")],
        [_s("u1", "The glacier melts in summer.", "glacier"),
         _s("u2", "The volcano erupts again.", "volcano")]))
    # 10. a montage over two shots
    out.append(Scenario(
        "montage", "montage nhiều đoạn",
        _shots(0, 6000, 12000, 18000),
        [(3800, 5300, "The lighthouse flashes."), (15500, 17000, "The bridge sways.")],
        [(0, 6000, "lighthouse"), (6000, 12000, "meadow"), (12000, 18000, "bridge")],
        [_s("u1", "The lighthouse flashes and the bridge sways.", "lighthouse", "bridge", kind="montage")],
        sentence_ms=6000))
    # 11. hard negatives: same name, other action; same action, other entity; same words, other year
    out.append(Scenario(
        "hard_negatives", "hard negative: cùng tên khác hành động / cùng hành động khác thực thể hoặc năm",
        _shots(0, 6000, 12000, 18000),
        [(3000, 4600, "In 2021 the tractor crosses the river."), (9000, 10600, "The locomotive climbs the mountain."),
         (15000, 16600, "In 2023 the glacier melts into the valley.")],
        [(0, 6000, "tractor"), (6000, 12000, "locomotive"), (12000, 18000, "glacier")],
        [_s("u1", "The tractor sinks in the swamp.", kind="action"),
         _s("u2", "The helicopter climbs the mountain.", kind="entity"),
         _s("u3", "In 1959 the glacier melts into the valley.", kind="time"),
         _s("u4", "Grandmother bakes bread for the festival.", kind="none")]))
    return out


def _evidence(scenario: Scenario) -> tuple[list[Candidate], dict[str, ev.Evidence]]:
    candidates, found = [], {}
    for index, shot in enumerate(scenario.shots):
        candidates.append(Candidate(shot.sid, "src", shot.start_ms, shot.end_ms, index, 50, Quality(120, 50, 300, ()),
                                    index + 1, f"{shot.sid}.jpg"))
        item = ev.Evidence(shot.sid)
        if shot.seen:
            item.add(ev.Item("vision", 800, shot.seen))
        said = ev.transcript_text(scenario.cues, shot.start_ms, shot.end_ms)
        item.cues = tuple(c for c in scenario.cues if c[0] < shot.end_ms and c[1] > shot.start_ms)
        if said:  # as planners.compilation.evidence_for adds it
            item.add(ev.Item("transcript", 850, said[:400], (), "phụ đề nguồn"))
        found[shot.sid] = item
    return candidates, found


def _ov(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def run_one(scenario: Scenario, *, lead_ms: int | None, forward_only: bool) -> dict[str, Any]:
    candidates, found = _evidence(scenario)
    spoken = [(s.text, scenario.sentence_ms) for s in scenario.sentences]
    total = sum(ms for _, ms in spoken)
    units = align.units(spoken, total, lead_ms=0, gap_ms=0, function=None if forward_only else align.product_function)
    choices, decisions, mode = align.assign(units, candidates, found, rules.STYLE_PACING["natural"],
                                            forward_only=forward_only, cue_inpoint_lead_ms=lead_ms)
    pieces = quality.pieces_from_choices(choices)
    log = {"mode": mode, "candidates": [{"id": c.candidate_id, "quality": {}, "evidence": found[c.candidate_id].to_dict()}
                                        for c in candidates]}
    rows = []
    by_shot = {s.sid: s for s in scenario.shots}
    for sentence, unit, decision in zip(scenario.sentences, units, sorted(decisions, key=lambda d: d.unit.start_ms)):
        assert decision.unit.unit_id == unit.unit_id
        mine = [c for c in choices if c.unit_id == unit.unit_id]
        played = [(c.candidate.candidate_id, c.offset_ms, c.offset_ms + c.length_ms) for c in mine]
        d = decision.to_dict()
        state = rv.unit_state(d, log, [{"needs_review": c.needs_review} for c in mine])
        played_ms = sum(e - o for _, o, e in played)
        right_ms = sum(_ov(o, e, a, b) for _, o, e in played for a, b, tag in scenario.picture if tag in sentence.shows)
        own = [c for c in scenario.cues if ev.terms(sentence.text) & ev.terms(c[2])]
        row = {"unit": sentence.uid, "kind": sentence.kind, "state": state,
               "matched_footage": bool((d.get("coverage") or {}).get("matched_footage")), "played": played,
               "flags": [c.needs_review for c in mine], "confidence": [c.confidence for c in mine],
               "picture_right_ms": right_ms, "played_ms": played_ms}
        if sentence.shows:
            first = played[0] if played else None
            row["top1_picture_right"] = bool(first) and any(
                _ov(first[1], first[2], a, b) * 2 >= first[2] - first[1] for a, b, tag in scenario.picture if tag in sentence.shows)
            row["candidate_contains_cue"] = any(by_shot[cid].start_ms <= c[0] < by_shot[cid].end_ms for cid, _, _ in played for c in own)
            row["played_over_cue"] = any(_ov(o, e, c[0], c[1]) > 0 for _, o, e in played for c in own)
            near = [c for c in own if first and by_shot[first[0]].start_ms <= c[0] < by_shot[first[0]].end_ms]
            row["start_offset_ms"] = first[1] - near[0][0] if near else None
        rows.append(row)
    problems = quality.timeline_problems(pieces, duration_ms=total, forward_only=forward_only)
    return {"scenario": scenario.name, "lead_ms": lead_ms, "forward_only": forward_only, "units": rows,
            "timeline_problems": sorted(p["code"] for p in problems), "duration_ms": sum(p.length for p in pieces),
            "narration_ms": total}


def compare(scenario: Scenario, forward_only: bool, lead_ms: int = LEAD_MS) -> dict[str, Any]:
    off = run_one(scenario, lead_ms=None, forward_only=forward_only)
    on = run_one(scenario, lead_ms=lead_ms, forward_only=forward_only)
    worse, better = [], []
    for a, b in zip(off["units"], on["units"]):
        if b["picture_right_ms"] < a["picture_right_ms"]:
            worse.append(a["unit"])
        elif b["picture_right_ms"] > a["picture_right_ms"]:
            better.append(a["unit"])
    same_choice = all([p[0] for p in a["played"]] == [p[0] for p in b["played"]] for a, b in zip(off["units"], on["units"]))
    # the review contract: the same state, the same "footage found" verdict, every piece still flagged where every
    # piece was, and no piece more confident than the most confident piece before
    same_safety = all((a["state"], a["matched_footage"], all(a["flags"])) == (b["state"], b["matched_footage"], all(b["flags"]))
                      and max(b["confidence"], default=0) <= max(a["confidence"], default=0)
                      for a, b in zip(off["units"], on["units"]))
    return {"scenario": scenario.name, "about": scenario.about, "forward_only": forward_only, "off": off, "on": on,
            "picture_worse": worse, "picture_better": better, "same_shots": same_choice, "same_review_state": same_safety,
            "new_timeline_problems": sorted(set(on["timeline_problems"]) - set(off["timeline_problems"])),
            "duration_equal": off["duration_ms"] == on["duration_ms"]}


def _count(results: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    rows = [u for r in results for u in r[side]["units"]]
    pos = [u for u in rows if "top1_picture_right" in u]
    neg = [u for u in rows if "top1_picture_right" not in u]
    offsets = sorted(u["start_offset_ms"] for u in pos if u.get("start_offset_ms") is not None)
    return {"units_with_footage": len(pos), "top1_picture_right": sum(u["top1_picture_right"] for u in pos),
            "candidate_contains_cue": sum(u["candidate_contains_cue"] for u in pos),
            "played_over_cue": sum(u["played_over_cue"] for u in pos),
            "picture_right_ms": sum(u["picture_right_ms"] for u in pos), "played_ms": sum(u["played_ms"] for u in pos),
            "median_start_offset_ms": offsets[len(offsets) // 2] if offsets else None,
            "no_footage_units": len(neg), "no_footage_assigned": sum(u["matched_footage"] for u in neg),
            "ready": sum(u["state"] == rv.READY for u in rows),
            "timeline_problems": sum(len(r[side]["timeline_problems"]) for r in results)}


def run(lead_ms: int = LEAD_MS) -> dict[str, Any]:
    results = [compare(s, fo, lead_ms) for s in scenarios() for fo in (False, True)]
    return {"ENGINEERING_FIXTURE_ONLY": True, "lead_ms": lead_ms,
            "summary": {"off": _count(results, "off"), "on": _count(results, "on"),
                        "picture_worse": [f"{r['scenario']}{'/fwd' if r['forward_only'] else ''}:{u}" for r in results for u in r["picture_worse"]],
                        "picture_better": [f"{r['scenario']}{'/fwd' if r['forward_only'] else ''}:{u}" for r in results for u in r["picture_better"]],
                        "shots_changed": [r["scenario"] for r in results if not r["same_shots"]],
                        "review_state_changed": [r["scenario"] for r in results if not r["same_review_state"]],
                        "new_timeline_problems": {r["scenario"]: r["new_timeline_problems"] for r in results if r["new_timeline_problems"]},
                        "duration_changed": [r["scenario"] for r in results if not r["duration_equal"]]},
            "results": results}


if __name__ == "__main__":
    lead = int(sys.argv[2]) if len(sys.argv) > 2 else LEAD_MS
    result = run(lead)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1, ensure_ascii=False))
