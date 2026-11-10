"""Quality checks of an editorial result against labelled ground truth (regression harness, Phase 2 hardening).

Two independent checks, both on *pieces*: (unit, source, source span, timeline span, role).

* :func:`timeline_problems` needs no labels. It finds negative or empty pieces, gaps and overlaps on the output
  timeline, pieces under the pacing floor, held frames of a video source (a frame frozen to fill time), footage
  shown twice without being declared a repeat, and, for forward-only workflows, a source played backwards.
* :func:`alignment` needs a :class:`Truth`: for each unit, the source intervals that illustrate it (``accept``),
  the ones that contradict it (``contra``) and the ones it particularly needs (``key``, e.g. the only shot of a
  detail). It measures, per unit and overall, how much of the narration time is illustrated by accepted footage,
  how much by contradicting footage, and how much ``key`` footage of a later unit an earlier one (the hook) used.

The checks read plans (:func:`pieces_from_plan`) or the aligner's choices (:func:`pieces_from_choices`), so the
same harness runs in unit tests, on fixtures, and on a real run's ``mastering-plan`` files.

``python -m dubflow.mastering.quality --truth truth.json plan.json [plan.json ...]`` prints the JSON report.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

from . import plan as m

MIN_PIECE_MS = 500
REPEAT_OVERLAP_MS = 300
GOOD_UNIT = 900  # ‰ of a unit's time on accepted footage for the unit to count as illustrated


@dataclass(frozen=True)
class Piece:
    unit_id: str
    source_id: str
    src_start: int
    src_end: int
    tl_start: int
    tl_end: int
    role: str = "primary"
    held: bool = False        # a frame of a video held to fill time (not a photo)
    declared_repeat: bool = False
    part: str = ""

    @property
    def length(self) -> int:
        return self.tl_end - self.tl_start


def pieces_from_plan(plan: m.Plan, *, part: str = "") -> list[Piece]:
    """The plan's segments as pieces in milliseconds (a segment of several units counts for the first)."""
    out = []
    durations = {s.source_id: s.duration for s in plan.sources}
    for segment in plan.segments:
        ms = lambda t: m.to_milliseconds(t, plan.time_base)  # noqa: E731
        src_start, src_end = ms(segment.source.start), ms(segment.source.end)
        video = durations.get(segment.source_id, 0) > 0
        held = segment.role == "still" and video
        declared = segment.role == "hook" or any("nhắc lại" in r for r in segment.reasons)
        out.append(Piece(segment.speech_refs[0] if segment.speech_refs else "", segment.source_id, src_start, src_end,
                         ms(segment.timeline.start), ms(segment.timeline.end), segment.role, held, declared, part or plan.plan_id))
    return out


def pieces_from_choices(choices: Sequence[Any], *, stills: Iterable[str] = (), part: str = "") -> list[Piece]:
    """The aligner's choices laid end to end (as the planners do)."""
    still_ids = set(stills)
    out, at = [], 0
    for choice in choices:
        candidate = choice.candidate
        photo = candidate.candidate_id in still_ids
        start = 0 if photo else choice.offset_ms
        end = 0 if photo else choice.offset_ms + choice.length_ms
        declared = any("nhắc lại" in r for r in choice.reasons)
        out.append(Piece(choice.unit_id, candidate.source_id, start, end, at, at + choice.length_ms,
                         "still" if photo else "primary", False, declared, part))
        at += choice.length_ms
    return out


def timeline_problems(pieces: Sequence[Piece], *, duration_ms: int | None = None, forward_only: bool = False,
                      min_piece_ms: int = MIN_PIECE_MS) -> list[dict[str, Any]]:
    """Every timeline-integrity problem, as ``{code, part, unit, at_ms, detail}``."""
    problems: list[dict[str, Any]] = []

    def add(code: str, piece: Piece | None, detail: str) -> None:
        problems.append({"code": code, "part": piece.part if piece else "", "unit": piece.unit_id if piece else "",
                         "at_ms": piece.tl_start if piece else 0, "detail": detail})

    parts: dict[str, list[Piece]] = {}
    for piece in pieces:
        parts.setdefault(piece.part, []).append(piece)
    for part_pieces in parts.values():
        ordered = sorted(part_pieces, key=lambda p: (p.tl_start, p.tl_end))
        cursor = 0
        for piece in ordered:
            if piece.length < 0 or piece.src_end < piece.src_start:
                add("NEGATIVE", piece, f"{piece.length} ms")
                continue
            if piece.length == 0:
                add("EMPTY", piece, "0 ms")
            if piece.tl_start > cursor:
                add("GAP", piece, f"{piece.tl_start - cursor} ms trống trước đoạn này")
            elif piece.tl_start < cursor:
                add("OVERLAP", piece, f"chồng {cursor - piece.tl_start} ms lên đoạn trước")
            cursor = max(cursor, piece.tl_end)
            if 0 < piece.length < min_piece_ms:
                add("SLIVER", piece, f"{piece.length} ms < {min_piece_ms} ms")
            if piece.held:
                add("HELD_FRAME", piece, f"giữ một khung hình {piece.length} ms")
        if duration_ms is not None and len(parts) == 1 and cursor != duration_ms:
            add("GAP" if cursor < duration_ms else "OVERLAP", ordered[-1] if ordered else None,
                f"timeline kết thúc ở {cursor} ms, video dài {duration_ms} ms")
        if forward_only:
            last: dict[str, int] = {}
            for piece in ordered:
                if piece.role == "hook" or piece.src_end <= piece.src_start:
                    continue
                if piece.src_start < last.get(piece.source_id, 0):
                    add("BACKWARDS", piece, f"{piece.source_id} lùi về {piece.src_start} ms sau khi đã phát tới {last[piece.source_id]} ms")
                last[piece.source_id] = max(last.get(piece.source_id, 0), piece.src_end)
    # footage shown twice (across parts too) without being declared a repeat or a teaser
    shown: list[Piece] = []
    for piece in sorted(pieces, key=lambda p: (p.part, p.tl_start)):
        if piece.src_end <= piece.src_start:
            continue
        for earlier in shown:
            overlap = min(piece.src_end, earlier.src_end) - max(piece.src_start, earlier.src_start)
            if earlier.source_id == piece.source_id and overlap >= REPEAT_OVERLAP_MS \
                    and not (piece.declared_repeat or earlier.declared_repeat):
                add("REPEAT", piece, f"{piece.source_id} {max(piece.src_start, earlier.src_start)}–"
                                     f"{min(piece.src_end, earlier.src_end)} ms đã chiếu ở {earlier.part}/{earlier.unit_id}")
                break
        shown.append(piece)
    return problems


@dataclass(frozen=True)
class Interval:
    source_id: str
    start_ms: int
    end_ms: int

    @classmethod
    def from_dict(cls, value: Any) -> "Interval":
        if not isinstance(value, Mapping) or set(value) - {"source", "start_ms", "end_ms", "note"}:
            raise ValueError("an interval is {source, start_ms, end_ms, note?}")
        source, start, end = value.get("source"), value.get("start_ms"), value.get("end_ms")
        if type(source) is not str or type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError(f"bad interval {value!r}")
        return cls(source, start, end)


@dataclass
class UnitTruth:
    accept: list[Interval] = field(default_factory=list)
    contra: list[Interval] = field(default_factory=list)
    key: list[Interval] = field(default_factory=list)
    footage_exists: bool = True  # false: the sources have nothing for this unit (a warning is the right answer)


@dataclass
class Truth:
    dataset: str
    split: str  # "dev" (used to tune) or "holdout" (never used to tune)
    units: dict[str, UnitTruth]
    forward_only: bool = False

    @classmethod
    def from_dict(cls, value: Any) -> "Truth":
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise ValueError("truth must be {schema_version: 1, ...}")
        split = value.get("split")
        if split not in ("dev", "holdout"):
            raise ValueError("truth.split must be dev or holdout")
        units: dict[str, UnitTruth] = {}
        for unit_id, item in dict(value.get("units", {})).items():
            if not isinstance(item, Mapping) or set(item) - {"accept", "contra", "key", "footage_exists", "note"}:
                raise ValueError(f"truth.units.{unit_id}: unknown keys")
            units[unit_id] = UnitTruth([Interval.from_dict(i) for i in item.get("accept", [])],
                                       [Interval.from_dict(i) for i in item.get("contra", [])],
                                       [Interval.from_dict(i) for i in item.get("key", [])],
                                       bool(item.get("footage_exists", True)))
        return cls(str(value.get("dataset", "")), split, units, bool(value.get("forward_only", False)))


def _overlap(piece: Piece, intervals: Sequence[Interval]) -> int:
    """Timeline ms of ``piece`` on the intervals (a photo or held frame counts by the instant it shows)."""
    if piece.src_end <= piece.src_start:
        hit = any(i.source_id == piece.source_id and i.start_ms <= piece.src_start <= i.end_ms for i in intervals)
        return piece.length if hit else 0
    covered = []
    for interval in intervals:
        if interval.source_id == piece.source_id:
            start, end = max(piece.src_start, interval.start_ms), min(piece.src_end, interval.end_ms)
            if end > start:
                covered.append((start, end))
    covered.sort()
    total, reach = 0, piece.src_start
    for start, end in covered:
        start = max(start, reach)
        if end > start:
            total += end - start
            reach = end
    return min(piece.length, total)


def alignment(pieces: Sequence[Piece], truth: Truth, *, flagged: Mapping[str, bool] | None = None,
              unit_part: str = "") -> dict[str, Any]:
    """Coverage of each labelled unit and the totals. ``flagged``: unit id → the result marked it for review."""
    flagged = flagged or {}
    by_unit: dict[str, list[Piece]] = {}
    for piece in pieces:
        key = f"{piece.part}/{piece.unit_id}" if unit_part == "qualified" else piece.unit_id
        by_unit.setdefault(key, []).append(piece)
    order = [u for u in truth.units]
    rows = []
    totals = {"need_ms": 0, "accepted_ms": 0, "contra_ms": 0, "stolen_key_ms": 0}
    for position, unit_id in enumerate(order):
        labels = truth.units[unit_id]
        mine = by_unit.get(unit_id, [])
        need = sum(p.length for p in mine)
        accepted = sum(_overlap(p, labels.accept) for p in mine)
        contra = sum(_overlap(p, labels.contra) for p in mine)
        later_keys = [i for later in order[position + 1:] for i in truth.units[later].key]
        stolen = sum(_overlap(p, later_keys) for p in mine)
        share = accepted * 1000 // need if need else 0
        row = {"unit": unit_id, "need_ms": need, "accepted_ms": accepted, "contra_ms": contra, "stolen_key_ms": stolen,
               "coverage_milli": share, "footage_exists": labels.footage_exists, "flagged": bool(flagged.get(unit_id))}
        # a unit the sources cannot illustrate passes when the result says so; otherwise it must be covered
        row["ok"] = (share >= GOOD_UNIT and contra == 0) if labels.footage_exists else row["flagged"] and contra == 0
        row["silent_miss"] = labels.footage_exists and share < GOOD_UNIT and not row["flagged"]
        rows.append(row)
        if labels.footage_exists:
            totals["need_ms"] += need
            totals["accepted_ms"] += accepted
        totals["contra_ms"] += contra
        totals["stolen_key_ms"] += stolen
    labelled = [r for r in rows if r["footage_exists"]]
    return {
        "dataset": truth.dataset, "split": truth.split, "units": rows,
        "coverage_milli": totals["accepted_ms"] * 1000 // totals["need_ms"] if totals["need_ms"] else 0,
        "units_ok": sum(r["ok"] for r in rows), "units_total": len(rows),
        "units_illustrated": sum(r["coverage_milli"] >= GOOD_UNIT for r in labelled), "units_with_footage": len(labelled),
        "contra_ms": totals["contra_ms"], "stolen_key_ms": totals["stolen_key_ms"],
        "silent_misses": sum(r["silent_miss"] for r in rows),
        # flags on units that were fine: a review flag on everything says nothing
        "false_alarms": sum(r["flagged"] and r["ok"] and r["footage_exists"] for r in rows),
    }


def report(plans: Sequence[m.Plan], truth: Truth | None = None, *, flagged: Mapping[str, bool] | None = None) -> dict[str, Any]:
    """The full check of one result (one plan, or the parts of a programme)."""
    pieces = [p for plan in plans for p in pieces_from_plan(plan)]
    # Plan.duration is in plan ticks; the pieces are already in ms.
    duration = m.to_milliseconds(plans[0].duration, plans[0].time_base) if len(plans) == 1 else None
    forward = truth.forward_only if truth else plans[0].workflow == "compilation_entertainment"
    problems = timeline_problems(pieces, duration_ms=duration, forward_only=forward)
    if flagged is None:
        marked: dict[str, bool] = {}
        for plan in plans:
            for segment in plan.segments:
                for ref in segment.speech_refs:
                    key = ref
                    marked[key] = marked.get(key, False) or segment.needs_review
        flagged = marked
    out: dict[str, Any] = {"pieces": len(pieces), "problems": problems,
                           "problem_counts": {code: sum(p["code"] == code for p in problems) for code in sorted({p["code"] for p in problems})}}
    if truth is not None:
        out["alignment"] = alignment(pieces, truth, flagged=flagged)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dubflow.mastering.quality", description=__doc__.split("\n\n")[0])
    parser.add_argument("plans", nargs="+", type=Path, help="mastering plan JSON files (one per part)")
    parser.add_argument("--truth", type=Path, help="ground truth JSON (schema_version 1)")
    args = parser.parse_args(argv)
    plans = [m.Plan.from_dict(json.loads(path.read_text(encoding="utf-8"))) for path in args.plans]
    truth = Truth.from_dict(json.loads(args.truth.read_text(encoding="utf-8"))) if args.truth else None
    json.dump(report(plans, truth), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Interval", "Piece", "Truth", "UnitTruth", "alignment", "pieces_from_choices", "pieces_from_plan", "report",
           "timeline_problems"]
