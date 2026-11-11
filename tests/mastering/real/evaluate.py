"""Scores real-world planner results against frame-checked labels (Gate S final closure, PROTOCOL.md).

``python -m tests.mastering.real.evaluate <labels.json> <results-dir> [--replay] [--json out.json]``

``results-dir`` holds one folder per labelled video (named by its ``source_id``) with the recorded planner calls
of that video's run (``capture.py``: ``*.call.json`` and ``*.result.json``). Without ``--replay`` the results the
run itself produced are scored (fresh); with it every recorded call is planned again by the code checked out now
(deterministic replay on the same evidence). Each sentence's verdict is the one the desktop shows
(``review.annotate`` over the plan): READY, NEEDS_REVIEW, MATCH_NOT_FOUND or SOURCE_INSUFFICIENT.

Every definition below is fixed in PROTOCOL.md before any result was seen; change neither after.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import quality
from engine.dubflow.mastering import review as reviewing
from tests.mastering.real import capture

LABELS_SCHEMA = "dubflow-real-labels-v1"
STATES = ("MATCH_AVAILABLE", "SOURCE_INSUFFICIENT", "NOT_NEEDED")
CORRECT_COVERAGE = 900   # ‰ of a sentence's time on acceptable footage for it to be truly correct
SERIOUS_COVERAGE = 500   # ‰ under which a sentence that has footage is a serious error
ELIGIBLE_SUPPLY = 900    # ‰ of its narration the acceptable footage must last for a sentence to be READY-eligible
PART_ORDER = {"hook": 0, "intro": 1, "outro": 9}
BINS = ((0, 300), (300, 450), (450, 600), (600, 750), (750, 1001))

# Pre-registered REAL-HOLDOUT thresholds (directive "GATE S FINAL CLOSURE"; never changed after a result).
THRESHOLDS = {
    "coverage_milli": (">=", 900),
    "serious_silent_misses": ("==", 0),
    "serious_contra_ready": ("==", 0),
    "ready_precision_milli": (">=", 950),
    "serious_review_recall_milli": (">=", 1000),
    "false_alarm_milli": ("<=", 250),
    "ready_rate_milli": (">=", 600),
}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def load_labels(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != LABELS_SCHEMA:
        raise ValueError(f"{path}: not {LABELS_SCHEMA}")
    for video in value["videos"]:
        for sentence in video["sentences"]:
            if sentence["state"] not in STATES:
                raise ValueError(f"{sentence['speech_ref']}: unknown state {sentence['state']!r}")
            for key in ("acceptable", "partial", "contra"):
                for span in sentence.get(key) or ():
                    if not 0 <= int(span["start_ms"]) < int(span["end_ms"]):
                        raise ValueError(f"{sentence['speech_ref']}: bad span {span}")
    return value


def _part_key(plan_id: str) -> tuple[int, int, str]:
    if plan_id in PART_ORDER:
        return PART_ORDER[plan_id], 0, plan_id
    digits = "".join(ch for ch in plan_id if ch.isdigit())
    return 5, int(digits or 0), plan_id


def results_of(folder: Path, replay: bool) -> list[tuple[m.Plan, dict]]:
    """(plan, annotated decision log) of every plan of one video, in programme order."""
    out = []
    for call_path in sorted(folder.glob("*.call.json")):
        call = json.loads(call_path.read_text(encoding="utf-8"))
        if replay:
            plan, log = capture.replay(call)
            log = json.loads(json.dumps(log, ensure_ascii=False))
        else:
            saved = json.loads(call_path.with_name(call_path.name.replace(".call.json", ".result.json")).read_text(encoding="utf-8"))
            plan, log = m.Plan.from_dict(saved["plan"]), saved["log"]
        reviewing.annotate(log, plan.to_dict())
        out.append((plan, log, call["plan_id"]))
    out.sort(key=lambda item: _part_key(item[2]))
    return [(plan, log) for plan, log, _ in out]


def _overlap(start: int, end: int, spans: Iterable[Mapping[str, Any]]) -> int:
    covered = sorted((max(start, int(s["start_ms"])), min(end, int(s["end_ms"]))) for s in spans)
    total, reach = 0, start
    for a, b in covered:
        a = max(a, reach)
        if b > a:
            total += b - a
            reach = b
    return total


def _piece_overlap(piece: quality.Piece, spans: Sequence[Mapping[str, Any]]) -> int:
    """Timeline ms of a piece on the spans (a held frame counts by the instant it shows)."""
    if piece.src_end <= piece.src_start:
        return piece.length if any(int(s["start_ms"]) <= piece.src_start <= int(s["end_ms"]) for s in spans) else 0
    on = _overlap(piece.src_start, piece.src_end, spans)
    return on * piece.length // max(1, piece.src_end - piece.src_start)


def score_video(video: Mapping[str, Any], results: Sequence[tuple[m.Plan, dict]]) -> list[dict[str, Any]]:
    units, pieces, segments = [], [], []
    for plan, log in results:
        units += log.get("units") or []
        pieces += quality.pieces_from_plan(plan)
        ms = lambda t, p=plan: m.to_milliseconds(t, p.time_base)  # noqa: E731
        segments += [(s.speech_refs[0] if s.speech_refs else "", ms(s.source.start), ms(s.source.end),
                      ms(s.timeline.end) - ms(s.timeline.start), s.confidence, s.needs_review) for s in plan.segments]
    by_text = {}
    for unit in units:
        by_text.setdefault(fold(unit.get("text", "")), unit)
    sentences = video["sentences"]
    matched = []
    for index, sentence in enumerate(sentences):
        unit = by_text.get(fold(sentence["text"]))
        if unit is None:
            raise ValueError(f"{sentence['speech_ref']}: no planned sentence says {sentence['text']!r}")
        matched.append(unit)
    rows = []
    for sentence, unit in zip(sentences, matched):
        mine = [p for p in pieces if p.unit_id == unit["unit_id"]]
        need = sum(p.length for p in mine)
        accepted = sum(_piece_overlap(p, sentence.get("acceptable") or []) for p in mine)
        contra = sum(_piece_overlap(p, sentence.get("contra") or []) for p in mine)
        stolen = 0
        for other, other_unit in zip(sentences, matched):
            if other is sentence or other["state"] != "MATCH_AVAILABLE":
                continue
            other_need = sum(p.length for p in pieces if p.unit_id == other_unit["unit_id"]) or other.get("est_duration_ms", 0)
            supply = sum(int(s["end_ms"]) - int(s["start_ms"]) for s in other.get("acceptable") or [])
            if supply * 2 > other_need * 3:  # the other sentence has plenty: sharing its footage takes nothing
                continue
            for p in mine:
                stolen += max(0, _piece_overlap(p, other["acceptable"]) - _piece_overlap(p, sentence.get("acceptable") or []))
        verdict = (unit.get("review") or {}).get("state")
        supply = sum(int(s["end_ms"]) - int(s["start_ms"]) for s in sentence.get("acceptable") or [])
        coverage = accepted * 1000 // need if need else 0
        state = sentence["state"]
        serious_flag = bool(sentence.get("serious_if_wrong", True))
        if state == "MATCH_AVAILABLE":
            correct = coverage >= CORRECT_COVERAGE and contra == 0 and stolen == 0
            serious = coverage < SERIOUS_COVERAGE or (contra > 0 and serious_flag)
        elif state == "NOT_NEEDED":
            correct = contra == 0
            serious = contra > 0 and serious_flag
        else:  # SOURCE_INSUFFICIENT: whatever is shown does not show it; correct handling is a flag
            correct = False
            serious = serious_flag
        rows.append({
            "speech_ref": sentence["speech_ref"], "unit_id": unit["unit_id"], "type": sentence.get("type", ""),
            "state": state, "verdict": verdict, "ready": verdict == reviewing.READY,
            "need_ms": need, "accepted_ms": accepted, "contra_ms": contra, "stolen_ms": stolen, "coverage_milli": coverage,
            "supply_ms": supply, "correct": correct, "serious": serious, "serious_if_wrong": serious_flag,
            "eligible": state == "MATCH_AVAILABLE" and supply * 1000 >= ELIGIBLE_SUPPLY * max(1, need),
            "pieces": [{"src": [p.src_start, p.src_end], "ms": p.length} for p in mine],
        })
    # pieces with their confidence for the reliability bins
    accept_of = {u["unit_id"]: s.get("acceptable") or [] for s, u in zip(sentences, matched)}
    bins = []
    for unit_id, a, b, length, confidence, needs_review in segments:
        if unit_id in accept_of:
            on = _overlap(a, b, accept_of[unit_id]) * length // max(1, b - a) if b > a else 0
            bins.append({"confidence": confidence, "needs_review": needs_review, "ms": length, "accepted_ms": on})
    for row in rows:
        row["video"] = video["source_id"]
        row["group"] = video.get("group", "")
    return rows, bins


def _ratio(num: int, den: int) -> dict[str, int]:
    return {"num": num, "den": den, "milli": num * 1000 // den if den else 0}


def summary(rows: Sequence[Mapping[str, Any]], bins: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    footage = [r for r in rows if r["state"] == "MATCH_AVAILABLE"]
    ready = [r for r in rows if r["ready"]]
    serious = [r for r in rows if r["serious"]]
    correct = [r for r in rows if r["correct"]]
    eligible = [r for r in rows if r["eligible"]]
    out = {
        "sentences": len(rows),
        "coverage": _ratio(sum(r["accepted_ms"] for r in footage), sum(r["need_ms"] for r in footage)),
        "serious_silent_misses": sum(r["serious"] and r["ready"] for r in rows),
        "serious_contra_ready": sum(r["contra_ms"] > 0 and r["serious_if_wrong"] and r["ready"] for r in rows),
        "ready_precision": _ratio(sum(r["correct"] for r in ready), len(ready)),
        "serious_review_recall": _ratio(sum(not r["ready"] for r in serious), len(serious)),
        "false_alarm": _ratio(sum(not r["ready"] for r in correct), len(correct)),
        "ready_rate": _ratio(sum(r["ready"] for r in eligible), len(eligible)),
        "contra_ms": sum(r["contra_ms"] for r in rows), "stolen_ms": sum(r["stolen_ms"] for r in rows),
        "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})},
    }
    out["reliability"] = [{"bin": f"{lo}-{hi - 1}", "pieces": len(mine), "ms": sum(b["ms"] for b in mine),
                           "accepted_milli": sum(b["accepted_ms"] for b in mine) * 1000 // max(1, sum(b["ms"] for b in mine))}
                          for lo, hi in BINS for mine in [[b for b in bins if lo <= b["confidence"] < hi]]]
    values = {"coverage_milli": out["coverage"]["milli"], "serious_silent_misses": out["serious_silent_misses"],
              "serious_contra_ready": out["serious_contra_ready"], "ready_precision_milli": out["ready_precision"]["milli"],
              "serious_review_recall_milli": out["serious_review_recall"]["milli"] if serious else 1000,
              "false_alarm_milli": out["false_alarm"]["milli"], "ready_rate_milli": out["ready_rate"]["milli"]}
    if not ready:
        values["ready_precision_milli"] = 0  # no READY at all cannot pass a precision threshold
    checks = {}
    for name, (op, bound) in THRESHOLDS.items():
        value = values[name]
        checks[name] = {"value": value, "bound": f"{op} {bound}",
                        "pass": value >= bound if op == ">=" else value <= bound if op == "<=" else value == bound}
    out["thresholds"] = checks
    out["pass"] = all(c["pass"] for c in checks.values())
    return out


def evaluate(labels: Mapping[str, Any], results_dir: Path, *, replay: bool) -> dict[str, Any]:
    rows, bins, per_video = [], [], {}
    for video in labels["videos"]:
        folder = results_dir / video["source_id"]
        if not folder.is_dir():
            raise FileNotFoundError(f"no results for {video['source_id']} in {results_dir}")
        mine, mine_bins = score_video(video, results_of(folder, replay))
        rows += mine
        bins += mine_bins
        per_video[video["source_id"]] = summary(mine, mine_bins)
    groups = {g: summary([r for r in rows if r["group"] == g]) for g in sorted({r["group"] for r in rows})}
    types = {t: summary([r for r in rows if r["type"] == t]) for t in sorted({r["type"] for r in rows})}
    return {"split": labels.get("split"), "mode": "replay" if replay else "fresh", "total": summary(rows, bins),
            "videos": per_video, "groups": groups, "types": types, "rows": rows}


def text_report(result: Mapping[str, Any]) -> str:
    t = result["total"]
    lines = [f"{result['split']} ({result['mode']}): {t['sentences']} sentences, verdicts {t['verdicts']}"]
    for name, check in t["thresholds"].items():
        lines.append(f"  {name:<28} {check['value']:>5}  {check['bound']:<8} {'PASS' if check['pass'] else 'FAIL'}")
    for key in ("coverage", "ready_precision", "serious_review_recall", "false_alarm", "ready_rate"):
        lines.append(f"  {key:<28} {t[key]['num']}/{t[key]['den']}")
    lines.append(f"  contra {t['contra_ms']} ms, stolen {t['stolen_ms']} ms")
    for video, s in result["videos"].items():
        lines.append(f"  {video:<6} coverage {s['coverage']['milli'] / 10:5.1f} %  READY {s['verdicts'].get('READY', 0)}  "
                     f"silent {s['serious_silent_misses']}  false alarms {s['false_alarm']['num']}/{s['false_alarm']['den']}")
    lines.append("  reliability: " + ", ".join(f"{b['bin']}: {b['pieces']} pieces {b['accepted_milli'] / 10:.0f} % accepted"
                                               for b in t["reliability"]))
    lines.append(f"Gate S thresholds: {'PASS' if t['pass'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.mastering.real.evaluate")
    parser.add_argument("labels", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(load_labels(args.labels), args.results, replay=args.replay)
    print(text_report(result))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
