"""D1 variant that never changes the shot (#879): research only, explicit call only, nothing calls it by default.

ENGINEERING_FIXTURE_ONLY = TRUE. The planner chooses shots exactly as it does in lineage ``region`` mode (no
``cue_inpoint_lead_ms``). Afterwards :func:`reanchor` moves each piece's in-point, inside the shot it already plays, to
``lead_ms`` before the earliest cited cue that starts in that shot. Source, shot id, number of pieces, order and length
of every piece stay as they were. A piece keeps its original in-point (and records why) when:

- ``still``: the shot is a still;
- ``declared_repeat``: the piece is a declared repeat ("nhắc lại");
- ``no_cited_cue``: the sentence cites no valid cue;
- ``cue_outside_shot``: no cited cue starts inside the shot;
- ``outside_shot``: the moved span would start before or end after the shot;
- ``unchanged``: the new in-point is the old one;
- ``no_gain``: the overlap with the cue would not grow;
- ``timeline``: the move would add a ``quality.timeline_problems`` code (REPEAT, BACKWARDS in forward-only, ...).

Option ``clamp_to_shot_end`` (default off, #881): when the cue-led span would run past the end of the shot, try
``shot_end_ms - length_ms`` instead (``clamped``). With the option on, a move (clamped or not) is accepted only if the
total overlap with all cited cues grows (else ``no_gain``) and every cited cue it touched before is still touched (else
``drops_touched_cue``); the timeline check is the same.

A cue says where words are spoken, never what the picture shows: confidence, review flags and READY are untouched.
"""
from __future__ import annotations

import dataclasses, json, sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align, evidence as ev, lineage as lin, quality  # noqa: E402
from engine.dubflow.mastering import policy as rules  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate, Quality  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
LEAD_MS = 300
REASON = "thử nghiệm #879: điểm vào theo cue được trích, giữ nguyên cảnh"


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _codes(choices: Sequence[align.Choice], stills: Sequence[str], duration_ms: int | None, forward_only: bool) -> Counter:
    pieces = quality.pieces_from_choices(choices, stills=stills)
    return Counter(p["code"] for p in quality.timeline_problems(pieces, duration_ms=duration_ms, forward_only=forward_only))


def reanchor(choices: Sequence[align.Choice], cited: Mapping[str, Sequence[tuple[int, int]]], *, lead_ms: int = LEAD_MS,
             forward_only: bool = False, stills: Sequence[str] = (), duration_ms: int | None = None,
             clamp_to_shot_end: bool = False) -> tuple[list[align.Choice], list[dict[str, Any]]]:
    """``choices`` in timeline order; ``cited``: unit id -> its verified cited cue spans (source ms).
    Returns the new choices (same length, same shots, same lengths) and one log row per piece."""
    if type(lead_ms) is not int or not 0 <= lead_ms <= 5000:
        raise ValueError("lead_ms must be 0–5000")
    if type(clamp_to_shot_end) is not bool:
        raise ValueError("clamp_to_shot_end must be a bool")
    out = list(choices)
    log: list[dict[str, Any]] = []
    still_ids = set(stills)
    for i, choice in enumerate(choices):
        c = choice.candidate
        row = {"index": i, "unit": choice.unit_id, "candidate": c.candidate_id, "offset_ms": choice.offset_ms,
               "length_ms": choice.length_ms, "new_offset_ms": choice.offset_ms, "moved": False}
        spans = list(cited.get(choice.unit_id) or ())
        inside = sorted(s for s in spans if c.start_ms <= s[0] < c.end_ms)
        if c.candidate_id in still_ids:
            row["kept"] = "still"
        elif any("nhắc lại" in r for r in choice.reasons):
            row["kept"] = "declared_repeat"
        elif not spans:
            row["kept"] = "no_cited_cue"
        elif not inside:
            row["kept"] = "cue_outside_shot"
        else:
            cue = inside[0]
            new = cue[0] - lead_ms
            row["cue_ms"] = list(cue)
            if clamp_to_shot_end and new >= c.start_ms and new + choice.length_ms > c.end_ms:
                row["target_offset_ms"], row["clamped"] = new, True
                new = c.end_ms - choice.length_ms
            old_gain = _overlap(choice.offset_ms, choice.offset_ms + choice.length_ms, *cue)
            new_gain = _overlap(new, new + choice.length_ms, *cue)
            row["cue_overlap_ms"] = [old_gain, new_gain]
            if clamp_to_shot_end:
                before = [_overlap(choice.offset_ms, choice.offset_ms + choice.length_ms, *s) for s in spans]
                after = [_overlap(new, new + choice.length_ms, *s) for s in spans]
                row["all_cue_overlap_ms"] = [before, after]
            if new < c.start_ms or new + choice.length_ms > c.end_ms:
                row["kept"] = "outside_shot"
            elif new == choice.offset_ms:
                row["kept"] = "unchanged"
            elif (sum(after) <= sum(before)) if clamp_to_shot_end else new_gain <= old_gain:
                row["kept"] = "no_gain"
            elif clamp_to_shot_end and any(b > 0 and a == 0 for b, a in zip(before, after)):
                row["kept"] = "drops_touched_cue"
            else:
                trial = list(out)
                trial[i] = dataclasses.replace(choice, offset_ms=new, reasons=(*choice.reasons, REASON))
                before = _codes(out, stills, duration_ms, forward_only)
                after = _codes(trial, stills, duration_ms, forward_only)
                added = after - before
                if added:
                    row["kept"] = "timeline"
                    row["would_add"] = sorted(added.elements())
                else:
                    out = trial
                    row.update(new_offset_ms=new, moved=True, kept=None)
        log.append(row)
    return out, log


# ---------------------------------------------------------------- fixture (synthetic, hand-written)

@dataclass(frozen=True)
class Shot:
    sid: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Sentence:
    uid: str
    text: str
    refs: tuple[str, ...]


@dataclass
class Scenario:
    name: str
    about: str
    shots: list[Shot]
    cues: list[tuple[str, int, int, str]]  # (cue id, start, end, text)
    sentences: list[Sentence]
    sentence_ms: int = 3000
    window_ms: int = 0


def _shots(*bounds: int) -> list[Shot]:
    return [Shot(f"s{i}", a, b) for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]


def scenarios() -> list[Scenario]:
    return [
        Scenario("moves", "cue nằm sâu trong cảnh: dời được điểm vào, tăng phần giao với cue",
                 _shots(0, 10000, 20000),
                 [("c0", 6000, 7500, "The tractor crosses the river."), ("c1", 16000, 17500, "The lighthouse flashes.")],
                 [Sentence("u1", "We watch the tractor cross the river.", ("src:c0",)),
                  Sentence("u2", "The lighthouse flashes over the harbor.", ("src:c1",))]),
        Scenario("near_edge", "cue sát cuối cảnh: đoạn dời sẽ vượt biên, giữ điểm vào gốc",
                 _shots(0, 8000, 16000),
                 [("c0", 7300, 7900, "The windmill spins."), ("c1", 8200, 9000, "The bridge sways.")],
                 [Sentence("u1", "The windmill spins in the meadow.", ("src:c0",)),
                  Sentence("u2", "The bridge sways over the canyon.", ("src:c1",))]),
        Scenario("cue_outside_shot", "cue được trích nằm ở cảnh khác cảnh đang phát (vùng region rộng)",
                 _shots(0, 4000, 12000),
                 [("c0", 2500, 3500, "The glacier melts.")],
                 [Sentence("u1", "The glacier melts into the valley over many long summers.", ("src:c0",))],
                 sentence_ms=6000, window_ms=8000),
        Scenario("montage", "montage hai cảnh, mỗi cảnh có cue riêng (một đoạn dời được, một đoạn sát biên)",
                 _shots(0, 4000, 8000, 12000),
                 [("c0", 1500, 2500, "The lighthouse flashes."), ("c1", 9500, 10500, "The bridge sways.")],
                 [Sentence("u1", "The lighthouse flashes and the bridge sways.", ("src:c0", "src:c1"))],
                 sentence_ms=6000),
        Scenario("reuse", "câu sau trích lại cue của câu trước (giống r1): dời sẽ chiếu lại, giữ điểm vào gốc",
                 _shots(0, 12000, 20000),
                 [("c0", 5000, 6500, "Now the screen of the X9.")],
                 [Sentence("u1", "Now we come to the screen of the X9.", ("src:c0",)),
                  Sentence("u2", "Here is the X9 screen.", ("src:c0",))]),
        Scenario("competing_shot", "hai cảnh có lời gần giống nhau (giống r1): biến thể giữ đúng cảnh planner chọn",
                 _shots(0, 10000, 20000, 30000),
                 [("c0", 6000, 7500, "This is the screen of the X9."), ("c1", 24000, 25500, "The X9 screen again.")],
                 [Sentence("u1", "Next is the screen of the X9.", ("src:c0",))],
                 window_ms=30000),
    ]


def _build(sc: Scenario):
    candidates, found = [], {}
    plain = [(a, b, t) for _, a, b, t in sc.cues]
    for index, shot in enumerate(sc.shots):
        candidates.append(Candidate(shot.sid, "src", shot.start_ms, shot.end_ms, index, 50, Quality(120, 50, 300, ()),
                                    index + 1, f"{shot.sid}.jpg"))
        item = ev.Evidence(shot.sid)
        said = ev.transcript_text(plain, shot.start_ms, shot.end_ms)
        item.cues = tuple(c for c in plain if c[0] < shot.end_ms and c[1] > shot.start_ms)
        if said:
            item.add(ev.Item("transcript", 850, said[:400], (), "phụ đề nguồn"))
        found[shot.sid] = item
    doc = {"cues": [{"id": cid, "start_ms": a, "end_ms": b, "text": t} for cid, a, b, t in sc.cues]}
    index = lin.CueIndex.from_document("src", doc, "fixture")
    plan = lin.resolve("region", [s.refs for s in sc.sentences], {"src": index}, window_ms=sc.window_ms)
    return candidates, found, plan


def run_one(sc: Scenario, *, forward_only: bool, lead_ms: int = LEAD_MS, clamp_to_shot_end: bool = False) -> dict[str, Any]:
    """Region baseline (planner, D1 off) and the same plan re-anchored."""
    candidates, found, plan = _build(sc)
    spoken = [(s.text, sc.sentence_ms) for s in sc.sentences]
    total = sc.sentence_ms * len(spoken)
    units = align.units(spoken, total, lead_ms=0, gap_ms=0, function=None)
    choices, _decisions, _mode = align.assign(units, candidates, found, rules.STYLE_PACING["natural"],
                                              forward_only=forward_only, lineage=plan)
    cited = {u.unit_id: [(q.start_ms, q.end_ms) for q in cues] for u, cues in zip(units, plan.cues)}
    variant, log = reanchor(choices, cited, lead_ms=lead_ms, forward_only=forward_only, duration_ms=total,
                            clamp_to_shot_end=clamp_to_shot_end)

    def summary(cs: Sequence[align.Choice]) -> dict[str, Any]:
        pieces = quality.pieces_from_choices(cs)
        codes = sorted(p["code"] for p in quality.timeline_problems(pieces, duration_ms=total, forward_only=forward_only))
        return {"pieces": [(c.unit_id, c.candidate.candidate_id, c.offset_ms, c.length_ms) for c in cs],
                "timeline_problems": codes, "repeat": codes.count("REPEAT"),
                "cue_overlap_ms": sum(_overlap(c.offset_ms, c.offset_ms + c.length_ms, a, b)
                                      for c in cs for a, b in cited.get(c.unit_id, ()))}

    base, var = summary(choices), summary(variant)
    same = [(u, cid, ln) for u, cid, _, ln in base["pieces"]] == [(u, cid, ln) for u, cid, _, ln in var["pieces"]]
    return {"scenario": sc.name, "about": sc.about, "forward_only": forward_only, "region": base, "variant": var,
            "same_shots_order_lengths": same, "moved": sum(r["moved"] for r in log),
            "kept": dict(Counter(r["kept"] for r in log if not r["moved"])), "log": log,
            "new_timeline_problems": sorted((Counter(var["timeline_problems"]) - Counter(base["timeline_problems"])).elements())}


def run(lead_ms: int = LEAD_MS, clamp_to_shot_end: bool = False) -> dict[str, Any]:
    results = [run_one(sc, forward_only=fo, lead_ms=lead_ms, clamp_to_shot_end=clamp_to_shot_end)
               for sc in scenarios() for fo in (False, True)]
    return {"ENGINEERING_FIXTURE_ONLY": True, "lead_ms": lead_ms, "clamp_to_shot_end": clamp_to_shot_end,
            "summary": {"runs": len(results), "pieces": sum(len(r["log"]) for r in results),
                        "moved": sum(r["moved"] for r in results),
                        "kept": dict(sum((Counter(r["kept"]) for r in results), Counter())),
                        "all_same_shots_order_lengths": all(r["same_shots_order_lengths"] for r in results),
                        "new_timeline_problems": {f"{r['scenario']}{'/fwd' if r['forward_only'] else ''}": r["new_timeline_problems"]
                                                  for r in results if r["new_timeline_problems"]},
                        "repeat_region": sum(r["region"]["repeat"] for r in results),
                        "repeat_variant": sum(r["variant"]["repeat"] for r in results),
                        "cue_overlap_ms_region": sum(r["region"]["cue_overlap_ms"] for r in results),
                        "cue_overlap_ms_variant": sum(r["variant"]["cue_overlap_ms"] for r in results)},
            "results": results}


if __name__ == "__main__":
    result = run()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1, ensure_ascii=False))
    for r in result["results"]:
        print(r["scenario"], "fwd" if r["forward_only"] else "reorder", "moved", r["moved"], "kept", r["kept"],
              "same", r["same_shots_order_lengths"], "region", r["region"]["pieces"], "variant", r["variant"]["pieces"],
              r["region"]["timeline_problems"], r["variant"]["timeline_problems"])
