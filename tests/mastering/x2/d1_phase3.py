"""Phase 3 (D1) closing measurement: every D1 in-point rule on the picture-truth fixture of ``d1_bench`` ($0).

ENGINEERING_FIXTURE_ONLY = TRUE. Research only: nothing in ``engine/`` calls this, and no production default changes.

``d1_bench`` scores the engine experiment (``cue_inpoint_lead_ms``) against a hand-written picture truth: what is
visible from when to when, which can change inside one shot (a pan the cut detector does not split).
``d1_same_shot`` (#879/#881) is a later rule that never changes the shot, but its own fixture has no picture truth.
This script puts every rule on the same picture-truth scenarios, in reorder and forward-only mode:

- ``off``: production (no D1);
- ``engine@<lead>``: ``align.assign(..., cue_inpoint_lead_ms=lead)`` for a sweep of leads (Bước B: the lead is
  measured, not assumed);
- ``same_shot`` / ``same_shot+clamp``: ``d1_same_shot.reanchor`` applied to the ``off`` plan. The cue a piece is
  moved to is the cue inside its shot sharing the most words with the sentence (the engine's own rule without cue
  references, since ``d1_bench`` sentences cite none).

Per rule: played-over-cue, picture-right ms (share of played time that shows what the sentence is about), units whose
picture got worse/better than ``off``, shots changed, new timeline problems, READY. A cue says where words are
spoken, never what the picture shows: (played over cue) is never read as (picture right).
"""
from __future__ import annotations

import json, sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align, evidence as ev, quality, review as rv  # noqa: E402
from engine.dubflow.mastering import policy as rules  # noqa: E402

from tests.mastering.x2 import d1_bench as bench, d1_same_shot as same  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
LEADS = (0, 150, 300, 500, 800)


def mirror_scenarios() -> list[bench.Scenario]:
    """The mirror of ``picture_before_speech``: inside one unsplit shot the thing appears when it is named.

    ``d1_bench`` only has the pan where the picture comes first (D1 can only lose there) and whole-shot pictures (D1
    cannot change the picture there). Without this case the fixture could never show a picture gain."""
    return [bench.Scenario(
        "picture_with_speech", "cảnh đúng xuất hiện cùng lúc lời nói, trong cùng một shot (pan không bị cắt)",
        bench._shots(0, 10000, 16000),
        [(6500, 8100, "There, the satellite orbits far above us."), (12500, 14000, "Down in the ocean the submarine dives.")],
        [(0, 6000, "planet"), (6000, 10000, "satellite"), (10000, 16000, "submarine")],
        [bench._s("u1", "A satellite orbits above the planet.", "satellite"),
         bench._s("u2", "The submarine dives into the ocean.", "submarine")])]


def _plan(sc: bench.Scenario, forward_only: bool, lead_ms: int | None):
    candidates, found = bench._evidence(sc)
    spoken = [(s.text, sc.sentence_ms) for s in sc.sentences]
    total = sum(ms for _, ms in spoken)
    units = align.units(spoken, total, lead_ms=0, gap_ms=0, function=None if forward_only else align.product_function)
    choices, decisions, mode = align.assign(units, candidates, found, rules.STYLE_PACING["natural"],
                                            forward_only=forward_only, cue_inpoint_lead_ms=lead_ms)
    return units, choices, decisions, total, mode


def _cited(sc: bench.Scenario, units: Sequence[align.Unit], choices: Sequence[align.Choice]) -> dict[str, list[tuple[int, int]]]:
    """Per unit: in each shot it plays, the cue sharing the most words with it (earliest of equals)."""
    out: dict[str, list[tuple[int, int]]] = {}
    text = {u.unit_id: u.text for u in units}
    for c in choices:
        wanted = ev.terms(text[c.unit_id])
        best: tuple[int, int, int] | None = None
        for a, b, said in sc.cues:
            if c.candidate.start_ms <= a < c.candidate.end_ms:
                shared = len(wanted & ev.terms(said))
                if shared and (best is None or shared > best[0]):
                    best = (shared, a, b)
        if best is not None and (best[1], best[2]) not in out.setdefault(c.unit_id, []):
            out[c.unit_id].append((best[1], best[2]))
    return out


def _score(sc: bench.Scenario, units, choices, decisions, total: int, forward_only: bool, mode: str) -> dict[str, Any]:
    candidates, found = bench._evidence(sc)
    log = {"mode": mode, "candidates": [{"id": c.candidate_id, "quality": {}, "evidence": found[c.candidate_id].to_dict()} for c in candidates]}
    rows = []
    ordered = sorted(decisions, key=lambda d: d.unit.start_ms)
    for sentence, unit, decision in zip(sc.sentences, units, ordered):
        mine = [c for c in choices if c.unit_id == unit.unit_id]
        played = [(c.candidate.candidate_id, c.offset_ms, c.offset_ms + c.length_ms) for c in mine]
        own = [q for q in sc.cues if ev.terms(sentence.text) & ev.terms(q[2])]
        state = rv.unit_state(decision.to_dict(), log, [{"needs_review": c.needs_review} for c in mine])
        rows.append({"unit": sentence.uid, "shows": bool(sentence.shows), "shots": [p[0] for p in played],
                     "state": state,
                     "picture_right_ms": sum(bench._ov(o, e, a, b) for _, o, e in played
                                             for a, b, tag in sc.picture if tag in sentence.shows),
                     "played_ms": sum(e - o for _, o, e in played),
                     "played_over_cue": any(bench._ov(o, e, q[0], q[1]) > 0 for _, o, e in played for q in own)})
    pieces = quality.pieces_from_choices(choices)
    codes = sorted(p["code"] for p in quality.timeline_problems(pieces, duration_ms=total, forward_only=forward_only))
    return {"units": rows, "timeline_problems": codes}


def run() -> dict[str, Any]:
    arms = ["off", *(f"engine@{lead}" for lead in LEADS), "same_shot", "same_shot+clamp"]
    per: dict[str, list[dict[str, Any]]] = {arm: [] for arm in arms}
    for sc in [*bench.scenarios(), *mirror_scenarios()]:
        for fo in (False, True):
            tag = f"{sc.name}{'/fwd' if fo else ''}"
            units, base, decisions, total, mode = _plan(sc, fo, None)
            per["off"].append({"run": tag, **_score(sc, units, base, decisions, total, fo, mode)})
            for lead in LEADS:
                u2, ch, d2, t2, m2 = _plan(sc, fo, lead)
                per[f"engine@{lead}"].append({"run": tag, **_score(sc, u2, ch, d2, t2, fo, m2)})
            cited = _cited(sc, units, base)
            for clamp, arm in ((False, "same_shot"), (True, "same_shot+clamp")):
                moved, _log = same.reanchor(base, cited, lead_ms=same.LEAD_MS, forward_only=fo, duration_ms=total,
                                            clamp_to_shot_end=clamp)
                per[arm].append({"run": tag, **_score(sc, units, moved, decisions, total, fo, mode)})
    summary = {}
    for arm in arms:
        rows = [(r["run"], u) for r in per[arm] for u in r["units"]]
        base_rows = [(r["run"], u) for r in per["off"] for u in r["units"]]
        pos = [u for _, u in rows if u["shows"]]
        worse = [f"{run}:{u['unit']}" for (run, u), (_, b) in zip(rows, base_rows) if u["picture_right_ms"] < b["picture_right_ms"]]
        better = [f"{run}:{u['unit']}" for (run, u), (_, b) in zip(rows, base_rows) if u["picture_right_ms"] > b["picture_right_ms"]]
        shots = [f"{run}:{u['unit']}" for (run, u), (_, b) in zip(rows, base_rows) if u["shots"] != b["shots"]]
        new_problems = Counter()
        for r, b in zip(per[arm], per["off"]):
            new_problems.update(Counter(r["timeline_problems"]) - Counter(b["timeline_problems"]))
        summary[arm] = {"units_with_footage": len(pos), "played_over_cue": sum(u["played_over_cue"] for u in pos),
                        "picture_right_ms": sum(u["picture_right_ms"] for u in pos),
                        "played_ms": sum(u["played_ms"] for u in pos),
                        "picture_worse": worse, "picture_better": better, "shots_changed": shots,
                        "new_timeline_problems": dict(new_problems),
                        "ready": sum(u["state"] == rv.READY for _, u in rows),
                        "ready_changed": sum(u["state"] != b["state"] for (_, u), (_, b) in zip(rows, base_rows))}
    return {"ENGINEERING_FIXTURE_ONLY": True, "summary": summary, "runs": per}


if __name__ == "__main__":
    result = run()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    for arm, s in result["summary"].items():
        print(f"{arm:16} cue {s['played_over_cue']:2}/{s['units_with_footage']}  picture {s['picture_right_ms']:6}/{s['played_ms']}"
              f"  worse {len(s['picture_worse'])} better {len(s['picture_better'])} shots {len(s['shots_changed'])}"
              f"  timeline+ {sum(s['new_timeline_problems'].values())}  READY {s['ready']} changed {s['ready_changed']}")
