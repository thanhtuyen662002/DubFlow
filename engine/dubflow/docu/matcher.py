"""VisualMatcher: B-roll slices for every narrated scene (ADR-0049, phase 2b).

Each scene's slot (its narration plus the pause after it) is filled with
3.0–4.5 s slices cut from the footage its script cites. Rules, in order:

* footage the scene cites first, then a few seconds around it, then uncited
  footage (the rest of the cited sources or any other source, ranked equally so
  interleaving decides); the scene says when it had to look further;
* consecutive slices alternate sources when the candidates allow it (A/B
  interleaving); two consecutive slices from one source are at least 8 s apart
  in that source, so the cut never jumps within one shot;
* no source range is used twice in the project;
* a slice starts on a shot boundary when one is available and ends on a cut
  near its planned end, so a cut never lands inside a camera transition.

Everything is deterministic: the same inputs give the same plan.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import math
from typing import Iterable, Sequence

MIN_SLICE, MAX_SLICE = 3.0, 4.5
SAME_SOURCE_GAP = 8.0
SNAP = 0.4
WIDEN = 6.0
EPSILON = 0.05
# Tiers, best first.
CITED, AROUND, SAME_SOURCES, ANY_SOURCE, REUSED = range(5)
TIER_NOTES = {
    AROUND: "Một số lát cắt lấy ngay quanh đoạn nguồn kịch bản gợi ý",
    SAME_SOURCES: "Một số lát cắt lấy từ phần khác của nguồn được gợi ý",
    ANY_SOURCE: "Một số lát cắt lấy từ nguồn khác vì đoạn gợi ý không đủ",
    REUSED: "Đã dùng lại cảnh đã có vì hết cảnh chưa dùng",
}


@dataclass(frozen=True)
class Source:
    id: str
    duration: float
    cuts: tuple[float, ...]


@dataclass(frozen=True)
class Ref:
    source_id: str
    start: float
    end: float


@dataclass(frozen=True)
class Scene:
    scene_id: str
    start: float
    end: float
    refs: tuple[Ref, ...]
    # Longest slice for this scene (its chapter's pacing, adjusted over MCP).
    max_slice: float = MAX_SLICE


@dataclass(frozen=True)
class Slice:
    source_id: str
    start: float
    end: float
    at: float
    tier: int

    @property
    def duration(self) -> float:
        return self.end - self.start


def shortest(max_slice: float) -> float:
    """3 s, or two thirds of a faster pace's longest slice."""
    return min(MIN_SLICE, max_slice * 2 / 3)


def lengths(duration: float, max_slice: float = MAX_SLICE) -> list[float]:
    """Equal slices of at most ``max_slice`` (3.0–4.5 s by default); one slice when the slot is too short to split."""
    if duration <= max_slice:
        return [duration]
    count = math.ceil(duration / max_slice)
    while count > 1 and duration / count < shortest(max_slice):
        count -= 1
    return [duration / count] * count


class _Used:
    def __init__(self) -> None:
        self.ranges: dict[str, list[tuple[float, float]]] = {}

    def overlaps(self, source_id: str, start: float, end: float) -> bool:
        return any(start < b - EPSILON and end > a + EPSILON for a, b in self.ranges.get(source_id, ()))

    def add(self, source_id: str, start: float, end: float) -> None:
        self.ranges.setdefault(source_id, []).append((start, end))


def _starts(source: Source, low: float, high: float) -> list[float]:
    """Shot starts inside [low, high), plus ``low`` itself when it is not one."""
    low, high = max(0.0, low), min(source.duration, high)
    if high <= low:
        return []
    cuts = [cut for cut in source.cuts[bisect_left(source.cuts, low):] if cut < high]
    return sorted({round(low, 3), *(round(cut, 3) for cut in cuts)})


def _snap(source: Source, start: float, length: float) -> float:
    """End at a cut close to the planned end (within 0.4 s, so the length stays near the plan)."""
    planned = start + length
    near = [cut for cut in source.cuts if abs(cut - planned) <= SNAP and cut - start > SNAP]
    return min(near, key=lambda cut: (abs(cut - planned), cut)) if near else planned


def _candidates(scene: Scene, sources: dict[str, Source]) -> list[tuple[int, int, str, float]]:
    """(tier, ref order, source, start) for every place a slice of this scene could start."""
    found: list[tuple[int, int, str, float]] = []
    cited = [ref for ref in scene.refs if ref.source_id in sources]
    for order, ref in enumerate(cited):
        source = sources[ref.source_id]
        found += [(CITED, order, ref.source_id, t) for t in _starts(source, ref.start, ref.end)]
        found += [(AROUND, order, ref.source_id, t) for t in _starts(source, ref.start - WIDEN, ref.end + WIDEN)]
    cited_ids = list(dict.fromkeys(ref.source_id for ref in cited))
    rest = len(cited)
    for source_id in cited_ids:
        found += [(SAME_SOURCES, rest, source_id, t) for t in _starts(sources[source_id], 0, sources[source_id].duration)]
    for source_id in sorted(sources):
        found += [(ANY_SOURCE, rest + 1, source_id, t) for t in _starts(sources[source_id], 0, sources[source_id].duration)]
    return found


def _fits(source: Source, start: float, length: float) -> float | None:
    """The slice start, moved back when the source ends first; None when the source is too short."""
    if length > source.duration + EPSILON:
        return None
    return min(start, source.duration - length)


def plan(scenes: Sequence[Scene], sources: Iterable[Source]) -> list[tuple[Scene, list[Slice], list[str]]]:
    by_id = {source.id: source for source in sources}
    if not by_id:
        raise ValueError("no sources")
    used = _Used()
    previous: Slice | None = None
    result: list[tuple[Scene, list[Slice], list[str]]] = []
    for scene in scenes:
        slot = scene.end - scene.start
        candidates = _candidates(scene, by_id)
        slices: list[Slice] = []
        at, remaining = scene.start, slot
        while remaining > EPSILON:
            length = lengths(remaining, scene.max_slice)[0]
            minimum = shortest(scene.max_slice)
            choice = _choose(candidates, by_id, used, previous, length, remaining, minimum, reuse=False) \
                or _choose(candidates, by_id, used, previous, length, remaining, minimum, reuse=True)
            if choice is None:
                raise ValueError(f"no footage long enough for {scene.scene_id}")
            tier, source_id, start, end = choice
            piece = Slice(source_id, round(start, 3), round(end, 3), round(at, 3), tier)
            slices.append(piece)
            used.add(source_id, piece.start, piece.end)
            previous = piece
            at += piece.duration
            remaining = scene.end - at
        notes = [TIER_NOTES[tier] for tier in sorted({piece.tier for piece in slices}) if tier in TIER_NOTES]
        result.append((scene, slices, notes))
    return result


def _choose(candidates, sources: dict[str, Source], used: _Used, previous: Slice | None, length: float, remaining: float,
            minimum: float, *, reuse: bool) -> tuple[int, str, float, float] | None:
    best: tuple | None = None
    for tier, order, source_id, wanted in candidates:
        source = sources[source_id]
        start = _fits(source, wanted, length)
        if start is None:
            continue
        # A slice may end on a nearby cut when what is left still makes a full slice;
        # the last one ends exactly at the slot end.
        end = _snap(source, start, length)
        if end - start > remaining or (remaining - (end - start) < minimum - EPSILON and end - start != remaining):
            end = start + length
        if not reuse and used.overlaps(source_id, start, end):
            continue
        same = previous is not None and previous.source_id == source_id
        if same and (start < previous.end + SAME_SOURCE_GAP and end > previous.start - SAME_SOURCE_GAP):
            continue
        on_cut = 0 if (start == 0 or any(abs(cut - start) < 0.01 for cut in source.cuts)) else 1
        # Uncited footage is uncited wherever it comes from: between the rest of a cited
        # source and another source, the A/B interleaving decides (#450).
        rank = REUSED if reuse else min(tier, SAME_SOURCES)
        key = (rank, int(same), on_cut, order, source_id, start)
        if best is None or key < best[0]:
            best = (key, (REUSED if reuse else tier, source_id, start, end))
    return best[1] if best else None
