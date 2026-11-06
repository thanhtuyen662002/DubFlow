"""VisualMatcher: B-roll slices for every narrated scene (ADR-0049, phase 2b; Tier-3).

Each scene's slot (its narration plus the pause after it) is filled with
slices cut from the footage its script cites. Rules, in order:

* one shot, one slice (Tier-3): every slice lies inside one continuous shot
  of ``shots.json``, kept 0.12 s away from each ``scdet`` cut so a dissolve
  frame of the source never shows, so a slice never jumps to another camera
  angle part-way (the old 3.0–4.5 s grid snapped to a cut only within 0.4 s
  and often ran across one);
* slices are 1.8–6.5 s long, following the clean shots and the act's rhythm
  instead of an even ~3.8 s: a 2.2 s shot gives a ~2 s slice, a long aerial
  shot opening the hook or the engineering act, or closing the verdict, is
  held for 5–6 s, and in between the lengths alternate long, short and medium;
* footage the scene cites first, then a few seconds around it, then uncited
  footage (the rest of the cited sources or any other source, ranked equally so
  interleaving decides, then the closest to the cited ranges); the scene says
  when it had to look further;
* consecutive slices alternate sources when the candidates allow it (A/B
  interleaving); two consecutive slices from one source are at least 8 s apart
  in that source;
* no source range is used twice in the project; when nothing unused is left,
  a slice that repeats footage (the reuse tier, or more than half of it shown
  before) is marked ``reused`` so the rough cut frames it differently;
* footage showing big burned-in text (a Chinese title card, a giant number) is
  never used while any other footage, even reused footage, can fill the slot;
* a source with a watermark too big to hide (``heavy``) ranks after every clean
  source; a source marked unusable (a heavy watermark and text most of the
  time) is used only when there is nothing else at all;
* a portrait (phone) source keeps only a strip in 16:9: its footage ranks as
  uncited and after any landscape footage of the same rank;
* uncited filler goes first to footage whose source lines talk about what the
  scene does (``keywords``), so a scene about biogas gets footage about manure
  and gas, not the control room;
* a scene can reserve part of its slot for a generated picture (the satellite
  map, an infographic: ``Insert``); footage fills the rest.

Everything is deterministic: the same inputs give the same plan.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cached_property
import math
from typing import Iterable, Sequence

from . import keywords

MIN_SLICE, MAX_SLICE = 1.8, 6.5
# Kept away from every scdet cut so a dissolve or a flash frame of the source never shows.
SHOT_MARGIN = 0.12
SAME_SOURCE_GAP = 8.0
WIDEN = 6.0
# The lines that count for a candidate slice: those spoken over it, give or take this much (speech and picture drift).
LINE_REACH = 1.0
EPSILON = 0.05
# The shortest slice when the sources cut faster than MIN_SLICE everywhere.
RELAXED = 1.0
# A held establishing shot: the first slice of the hook and of the engineering act, the last of the verdict.
LONG_TARGET = 5.6
LONG_MIN = 5.0
# Lengths in between, long and short alternating so the cut breathes instead of ticking.
RHYTHM = (4.4, 2.6, 3.4, 5.0, 2.2, 3.8, 2.9, 4.6, 3.1, 2.4)
# Tiers, best first.
CITED, AROUND, SAME_SOURCES, ANY_SOURCE, REUSED = range(5)
TIER_NOTES = {
    AROUND: "Một số lát cắt lấy ngay quanh đoạn nguồn kịch bản gợi ý",
    SAME_SOURCES: "Một số lát cắt lấy từ phần khác của nguồn được gợi ý",
    ANY_SOURCE: "Một số lát cắt lấy từ nguồn khác vì đoạn gợi ý không đủ",
    REUSED: "Đã dùng lại cảnh đã có vì hết cảnh chưa dùng",
}
CROSSED_NOTE = "Một lát cắt phải vắt qua điểm chuyển cảnh của nguồn vì không còn cú máy nào đủ dài"
FOOTAGE = "footage"


@dataclass(frozen=True)
class Source:
    id: str
    duration: float
    cuts: tuple[float, ...]
    # A phone video: 16:9 keeps only a strip of it, so it is the last resort.
    portrait: bool = False
    # Stretches showing big burned-in text (a title card, a giant number): left out unless nothing else fits.
    text: tuple[tuple[float, float], ...] = ()
    # (start, end, concepts) of each source line, in time order (``keywords.concepts`` of its text).
    lines: tuple[tuple[float, float, frozenset[str]], ...] = ()
    # A watermark framing cannot crop away and too big to blur unseen: after every clean source.
    heavy: bool = False
    # A heavy watermark and big text most of the time: only when nothing else exists.
    usable: bool = True

    @cached_property
    def _line_starts(self) -> tuple[list[float], float]:
        return [line[0] for line in self.lines], max((b - a for a, b, _ in self.lines), default=0.0)

    @cached_property
    def windows(self) -> tuple[tuple[float, float], ...]:
        """The clean part of every shot: its bounds moved ``SHOT_MARGIN`` in from each scdet cut."""
        bounds = [0.0, *(cut for cut in self.cuts if 0.0 < cut < self.duration), self.duration]
        found = []
        for a, b in zip(bounds, bounds[1:]):
            low = a + (SHOT_MARGIN if a > 0 else 0.0)
            high = b - (SHOT_MARGIN if b < self.duration else 0.0)
            if high - low > EPSILON:
                found.append((round(low, 3), round(high, 3)))
        return tuple(found)

    def concepts_between(self, start: float, end: float) -> frozenset[str]:
        """The concepts of the lines spoken over [start, end], give or take ``LINE_REACH``."""
        starts, longest = self._line_starts
        found: set[str] = set()
        for line_start, line_end, concepts in self.lines[bisect_left(starts, start - LINE_REACH - longest):]:
            if line_start > end + LINE_REACH:
                break
            if line_end >= start - LINE_REACH:
                found |= concepts
        return frozenset(found)

    def shows_text(self, start: float, end: float) -> bool:
        return any(start < b and end > a for a, b in self.text)


@dataclass(frozen=True)
class Ref:
    source_id: str
    start: float
    end: float


@dataclass(frozen=True)
class Insert:
    """A generated picture (``kind``: map, infographic) ``duration`` long, ``offset`` seconds into the scene's slot."""
    kind: str
    offset: float
    duration: float
    ref: str = ""
    # The footage source whose picture the graphic may use as its background.
    source_id: str = ""


@dataclass(frozen=True)
class Scene:
    scene_id: str
    start: float
    end: float
    refs: tuple[Ref, ...]
    # Longest slice for this scene (its chapter's pacing, adjusted over MCP).
    max_slice: float = MAX_SLICE
    # (concept, weight) of what the scene is about: its visual tags and narration (``keywords.scene_terms``).
    terms: tuple[tuple[str, int], ...] = ()
    # "first" or "last": that slice is a held 5–6 s shot (the act's opening or closing picture).
    hold: str = ""
    inserts: tuple[Insert, ...] = field(default=())


@dataclass(frozen=True)
class Slice:
    source_id: str
    start: float
    end: float
    at: float
    tier: int
    # Footage shown earlier in the film (the reuse tier, or over half of it already used): rendered with another crop.
    reused: bool = False
    kind: str = FOOTAGE
    ref: str = ""
    # Ran across a shot cut: only when no shot anywhere was long enough.
    crossed: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start


def shortest(max_slice: float) -> float:
    """1.8 s, or two thirds of a very fast pace's longest slice."""
    return min(MIN_SLICE, max_slice * 2 / 3)


def fit_length(remaining: float, target: float, room: float, minimum: float, maximum: float) -> float | None:
    """The slice length for ``room`` seconds of clean shot when ``remaining`` seconds of the slot are left.

    Whatever is left after the slice must make a slice of its own (``minimum``), so the slice may grow to the
    end of the slot or shrink to leave exactly ``minimum``. None when the shot is too short.
    """
    cap = min(room, maximum)
    if remaining <= cap + 1e-9 and (remaining <= target + 1.2 or remaining - target < minimum):
        return remaining
    length = min(target, cap, remaining)
    left = remaining - length
    if 0 < left < minimum - 1e-9:
        if remaining <= cap + 1e-9:
            length = remaining
        else:
            length = remaining - minimum
    if length < minimum - 1e-9 and length < remaining - 1e-9:
        return None
    if length > cap + 1e-9:
        return None
    return length


class _Used:
    def __init__(self) -> None:
        self.ranges: dict[str, list[tuple[float, float]]] = {}

    def overlaps(self, source_id: str, start: float, end: float) -> bool:
        return any(start < b - EPSILON and end > a + EPSILON for a, b in self.ranges.get(source_id, ()))

    def shown(self, source_id: str, start: float, end: float) -> float:
        """The share of [start, end] that earlier slices already showed (overlaps between them counted once)."""
        spans = sorted((max(start, a), min(end, b)) for a, b in self.ranges.get(source_id, ()) if a < end and b > start)
        covered, reach = 0.0, start
        for a, b in spans:
            if b > reach:
                covered += b - max(a, reach)
                reach = b
        return covered / (end - start) if end > start else 0.0

    def add(self, source_id: str, start: float, end: float) -> None:
        ranges = self.ranges.setdefault(source_id, [])
        ranges.insert(bisect_right(ranges, (start, end)), (start, end))

    def free(self, source_id: str, low: float, high: float) -> list[tuple[float, float]]:
        """The parts of [low, high] no slice uses yet."""
        found, cursor = [], low
        for a, b in self.ranges.get(source_id, ()):
            if b <= cursor + EPSILON:
                continue
            if a >= high - EPSILON:
                break
            if a - cursor > EPSILON:
                found.append((cursor, min(a, high)))
            cursor = max(cursor, b)
        if high - cursor > EPSILON:
            found.append((cursor, high))
        return found


def _near(scene: Scene, source_id: str, start: float) -> float:
    """Seconds from ``start`` to the closest footage this scene cites in that source (its own start when it cites none).

    Footage next to what the narration talks about usually shows the same thing, so uncited filler is taken
    as close to the cited ranges as the other rules allow instead of from the top of the source.
    """
    gaps = [max(0.0, ref.start - start, start - ref.end) for ref in scene.refs if ref.source_id == source_id]
    return round(min(gaps), 3) if gaps else start


def _overlapping(windows: Sequence[tuple[float, float]], low: float, high: float) -> list[tuple[float, float]]:
    return [(a, b) for a, b in windows if b > low + EPSILON and a < high - EPSILON]


def _candidates(scene: Scene, sources: dict[str, Source], *, crossed: bool) -> list[tuple[int, int, str, float, float, float, float]]:
    """(tier, ref order, source, window low, window high, earliest start, latest start) of every shot a slice could use.

    A cited slice starts inside the cited range; around it, within ``WIDEN`` of it; uncited filler anywhere in the shot.
    With ``crossed`` the whole source is one window (the old behaviour, a last resort).
    """
    def windows(source: Source) -> Sequence[tuple[float, float]]:
        return ((0.0, source.duration),) if crossed else source.windows

    found: list[tuple[int, int, str, float, float, float, float]] = []
    cited = [ref for ref in scene.refs if ref.source_id in sources]
    for order, ref in enumerate(cited):
        source = sources[ref.source_id]
        for a, b in _overlapping(windows(source), ref.start, ref.end):
            found.append((CITED, order, ref.source_id, a, b, ref.start, ref.end))
        low, high = ref.start - WIDEN, ref.end + WIDEN
        for a, b in _overlapping(windows(source), low, high):
            found.append((AROUND, order, ref.source_id, a, b, low, high))
    cited_ids = list(dict.fromkeys(ref.source_id for ref in cited))
    rest = len(cited)
    for source_id in cited_ids:
        found += [(SAME_SOURCES, rest, source_id, a, b, a, b) for a, b in windows(sources[source_id])]
    for source_id in sorted(sources):
        if source_id not in cited_ids:
            found += [(ANY_SOURCE, rest + 1, source_id, a, b, a, b) for a, b in windows(sources[source_id])]
    return found


def _targets(scene: Scene, counter: int, remaining: float, minimum: float, first: bool) -> float:
    """How long the next slice would like to be."""
    cap = min(scene.max_slice, MAX_SLICE)
    if scene.hold == "first" and first:
        return min(cap, LONG_TARGET)
    if scene.hold == "last":
        if remaining <= cap + 1e-9 and remaining >= min(cap, LONG_MIN):
            return remaining
        if remaining > cap and remaining - minimum >= min(cap, LONG_MIN):
            # Leave the held shot for the end of the scene.
            normal = min(cap, RHYTHM[counter % len(RHYTHM)])
            return max(minimum, min(normal, remaining - LONG_TARGET))
    pace = RHYTHM[counter % len(RHYTHM)]
    # A slower chapter (a longer pacing) stretches the rhythm, a faster one compresses it.
    if cap < MAX_SLICE:
        pace = pace * cap / MAX_SLICE + minimum * (1 - cap / MAX_SLICE)
    return max(minimum, min(cap, pace))


def _segments(scene: Scene) -> list[tuple[float, float, Insert | None]]:
    """The scene's slot as footage stretches and generated inserts, in time order."""
    found: list[tuple[float, float, Insert | None]] = []
    cursor = scene.start
    for insert in sorted(scene.inserts, key=lambda item: item.offset):
        start = max(cursor, scene.start + insert.offset)
        end = min(scene.end, start + insert.duration)
        if end - start < 1.0:
            continue
        if start - cursor > EPSILON:
            found.append((cursor, start, None))
        found.append((start, end, insert))
        cursor = end
    if scene.end - cursor > EPSILON:
        found.append((cursor, scene.end, None))
    return found


def plan(scenes: Sequence[Scene], sources: Iterable[Source]) -> list[tuple[Scene, list[Slice], list[str]]]:
    every = {source.id: source for source in sources}
    if not every:
        raise ValueError("no sources")
    usable = {key: source for key, source in every.items() if source.usable} or every
    used = _Used()
    previous: Slice | None = None
    counter = 0
    result: list[tuple[Scene, list[Slice], list[str]]] = []
    for scene in scenes:
        cache: dict[tuple[bool, bool], list] = {}
        slices: list[Slice] = []
        minimum = shortest(scene.max_slice)
        for seg_start, seg_end, insert in _segments(scene):
            if insert is not None:
                piece = Slice(insert.source_id, 0.0, round(seg_end - seg_start, 3), round(seg_start, 3), 0, kind=insert.kind, ref=insert.ref)
                slices.append(piece)
                previous = None
                continue
            at, remaining = seg_start, seg_end - seg_start
            while remaining > EPSILON:
                choice = None
                first = not any(piece.kind == FOOTAGE for piece in slices)
                target = _targets(scene, counter, remaining, minimum, first)
                # Last resorts, in order: reused footage, short shots (only cuts this fast exist), footage with big
                # text, then a slice across a cut.
                for reuse, text_ok, crossed, pool, least in (
                        (False, False, False, usable, minimum), (True, False, False, usable, minimum),
                        (True, False, False, usable, RELAXED), (True, True, False, usable, RELAXED),
                        (True, True, True, every, minimum)):
                    key = (crossed, pool is every)
                    if key not in cache:
                        cache[key] = _candidates(scene, pool, crossed=crossed)
                    choice = _choose(cache[key], pool, used, previous, min(max(target, least), scene.max_slice), remaining,
                                     least, reuse=reuse, scene=scene, text_ok=text_ok)
                    if choice is not None:
                        tier, source_id, start, end = choice
                        break
                if choice is None:
                    raise ValueError(f"no footage long enough for {scene.scene_id}")
                shown = used.shown(source_id, start, end)
                reused = shown > 0.5 or (tier == REUSED and shown > EPSILON)
                piece = Slice(source_id, round(start, 3), round(end, 3), round(at, 3), tier, reused=reused, crossed=crossed)
                slices.append(piece)
                used.add(source_id, piece.start, piece.end)
                previous = piece
                counter += 1
                at += end - start
                remaining = seg_end - at
        tiers = sorted({piece.tier for piece in slices if piece.kind == FOOTAGE})
        notes = [TIER_NOTES[tier] for tier in tiers if tier in TIER_NOTES]
        if any(piece.crossed for piece in slices):
            notes.append(CROSSED_NOTE)
        result.append((scene, slices, notes))
    return result


def _choose(candidates, sources: dict[str, Source], used: _Used, previous: Slice | None, target: float, remaining: float,
            minimum: float, *, reuse: bool, scene: Scene, text_ok: bool = False) -> tuple[int, str, float, float] | None:
    best: tuple | None = None
    cap = min(scene.max_slice, MAX_SLICE)
    for tier, order, source_id, low, high, earliest, latest in candidates:
        source = sources[source_id]
        pieces = [(low, high)] if reuse else used.free(source_id, low, high)
        for free_low, free_high in pieces:
            room = free_high - free_low
            if room < min(minimum, remaining) - 1e-9:
                continue
            # Start as close to the wanted range as the shot allows, then as early as the length needs.
            wanted = max(free_low, earliest)
            if wanted >= min(free_high, latest + EPSILON):
                continue
            length = fit_length(remaining, target, free_high - wanted, minimum, cap)
            start = wanted
            if length is None:
                # The shot has more room before the wanted start: slide back inside it.
                length = fit_length(remaining, target, room, minimum, cap)
                if length is None:
                    continue
                start = max(free_low, free_high - length)
                if start >= latest + EPSILON:
                    continue
            end = start + length
            if not text_ok and source.shows_text(start, end):
                # Big text in part of the shot: try the clean part after it.
                clean = _after_text(source, start, free_high)
                if clean is None:
                    continue
                start = clean
                length = fit_length(remaining, target, free_high - start, minimum, cap)
                if length is None or start >= latest + EPSILON:
                    continue
                end = start + length
                if source.shows_text(start, end):
                    continue
            same = previous is not None and previous.source_id == source_id
            if same and (start < previous.end + SAME_SOURCE_GAP and end > previous.start - SAME_SOURCE_GAP):
                # Too close to the slice just before it: further on in the same shot, if it lasts.
                start = max(start, previous.end + SAME_SOURCE_GAP)
                length = fit_length(remaining, target, free_high - start, minimum, cap) if start < free_high else None
                if length is None or start >= latest + EPSILON:
                    continue
                end = start + length
                if not text_ok and source.shows_text(start, end):
                    continue
            # Uncited footage is uncited wherever it comes from: between the rest of a cited
            # source and another source, the A/B interleaving decides (#450).
            rank = REUSED if reuse else min(tier, SAME_SOURCES)
            if source.portrait and not reuse:
                rank = max(rank, SAME_SOURCES)
            uncited = tier >= SAME_SOURCES or reuse
            near = _near(scene, source_id, start) if uncited else 0.0
            # Filler that talks about the scene's subject first; cited footage is already on topic.
            relevance = keywords.score(scene.terms, source.concepts_between(start, end)) if uncited and scene.terms and source.lines else 0
            # A held shot wants a long clean shot; otherwise a length near the rhythm's.
            fit = int(abs(length - target) / 0.75)
            # The held shot of an act (target 5–6 s) wants a long clean shot more than it wants the cited range.
            held = int(length < LONG_MIN - EPSILON) if target >= LONG_MIN - EPSILON else 0
            key = (int(source.heavy), held, rank, int(source.portrait), int(same), -relevance, fit, order, near, source_id, start)
            if best is None or key < best[0]:
                best = (key, (REUSED if reuse else tier, source_id, start, end))
    return best[1] if best else None


def _after_text(source: Source, start: float, high: float) -> float | None:
    """The first time after ``start`` (before ``high``) that no big text covers, or None."""
    cursor = start
    for a, b in sorted(source.text):
        if b <= cursor:
            continue
        if a >= high:
            break
        if a <= cursor < b:
            cursor = b
    return cursor if cursor < high - EPSILON else None
