"""More than one frame per shot, only where one frame is not enough (Gate S, adaptive multi-frame evidence).

A candidate is described by its middle frame. That frame can be wrong for the whole shot: a fade or motion blur
makes it unusable while the rest of the shot is fine, and a long take can show several things one after another
(unboxing, then a water test) of which the middle frame shows one. Two steps, both bounded:

1. *Fallback keyframe* (no AI): when the middle frame is dark, blank or blurred, frames at a quarter and three
   quarters of the shot are measured and the first usable one becomes the keyframe (``candidates.extract``).
2. *Extra frames* (with the user's vision model, after a first plan): for at most ``EXTRA_SHOTS`` shots per plan,
   two more frames early and late in the shot are described. Which shots: long ones (≥ ``LONG_MS``) that a sentence
   uses or that lie where an under-covered sentence may take footage, and shots whose first answer was unsure.
   Short, steady shots and shots already sampled are never asked again; answers are cached by frame content.
   When the frames agree, they are added as more evidence of the same shot; when they show different things, the
   shot is split at the midpoints between the frames so each part carries what its own frame shows.

Nothing here runs without a vision model except step 1, and nothing here is needed for a video to be made.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from . import evidence as ev
from .candidates import CUT_MARGIN_MS, FALLBACK_POSITIONS, MIN_CANDIDATE_MS, Candidate, fallback_times

LONG_MS = 6000            # a shot this long is not represented by one frame
EXTRA_POSITIONS = (150, 850)  # ‰ of the shot: early and late
EXTRA_SHOTS = 8           # shots per plan that get extra frames
UNSURE_BELOW = 500        # a first answer under this confidence (or not clear) is looked at again
SAME_SHARE = 400          # ‰ of shared words above which two frames show the same thing
SPLIT_MARK = "f"          # sub-shot ids: <candidate id>f<n>


def _inside(candidate: Candidate, at_ms: int) -> int:
    low = candidate.start_ms + min(CUT_MARGIN_MS, candidate.length_ms // 4)
    high = candidate.end_ms - min(CUT_MARGIN_MS, candidate.length_ms // 4) - 1
    return max(low, min(high, at_ms))


def extra_times(candidate: Candidate) -> list[int]:
    """The extra frames of a shot (source ms), inside it and clear of its cuts."""
    return [_inside(candidate, candidate.start_ms + candidate.length_ms * p // 1000) for p in EXTRA_POSITIONS]


def _unsure(found: ev.Evidence | None) -> bool:
    if found is None:
        return False
    vision = [i for i in found.items if i.origin == "vision"]
    return bool(vision) and max(i.confidence for i in vision) < UNSURE_BELOW


def wanted(units: Sequence[Mapping[str, Any]], pool: Sequence[Candidate], evidence: Mapping[str, ev.Evidence],
           sampled: Iterable[str], *, limit: int = EXTRA_SHOTS, forward_only: bool = False,
           locked: Iterable[str] = (), again: Iterable[str] = ()) -> list[str]:
    """The shots of a first plan (its editorial log ``units``) worth more frames, most useful first.

    1. long shots in reach of a sentence that is not fully illustrated (in a forward-only plan, between what the
       sentences around it used), so a later part of the shot that shows it can be found;
    2. long shots a sentence uses: one frame stood for all of them;
    3. shots the model was unsure about.
    ``sampled`` shots and ``locked`` ones (the user pinned or excluded them) are left as they are; ``again`` are shots
    a user decision names a part of (``v1c3f3``): they are split again first, so the decision still finds its shot.
    """
    done = set(sampled) | set(locked)
    usable = [c for c in sorted(pool, key=lambda c: (c.source_id, c.start_ms)) if c.quality.usable and c.keyframe]
    by_id = {c.candidate_id: c for c in usable}
    position = {u["unit_id"]: i for i, u in enumerate(units)}
    out: list[str] = []

    def add(candidate_id: str) -> None:
        # a part of a shot already split by its frames is not split again (``c3f2f3`` is no shot id the
        # desktop or a decision accepts: rc4 Windows check, "Cảnh không hợp lệ")
        if parent(candidate_id) != candidate_id:
            return
        if candidate_id not in done and candidate_id not in out and candidate_id in by_id and len(out) < limit:
            out.append(candidate_id)

    for candidate_id in again:
        add(candidate_id)

    weak = [u for u in units if (u.get("coverage") or {}).get("needs_review")
            and (u.get("coverage") or {}).get("status") in ("partial", "none")]
    for unit in weak:
        here = position[unit["unit_id"]]
        low: dict[str, int] = {}
        high: dict[str, int] = {}
        if forward_only:
            for other in units:
                for chosen in other.get("chosen", ()):
                    c = by_id.get(chosen)
                    if c is None:
                        continue
                    if position[other["unit_id"]] < here:
                        low[c.source_id] = max(low.get(c.source_id, 0), c.start_ms)
                    elif position[other["unit_id"]] > here:
                        high[c.source_id] = min(high.get(c.source_id, c.end_ms), c.end_ms)
        for c in sorted(usable, key=lambda c: -c.length_ms):
            if c.length_ms >= LONG_MS and c.start_ms >= low.get(c.source_id, 0) and c.end_ms <= high.get(c.source_id, c.end_ms):
                add(c.candidate_id)
    for unit in units:
        for chosen in unit.get("chosen", ()):
            c = by_id.get(chosen)
            if c is not None and c.length_ms >= LONG_MS:
                add(chosen)
    for c in usable:
        if _unsure(evidence.get(c.candidate_id)):
            add(c.candidate_id)
    return out


def _words(text: str) -> set[str]:
    return {t for t in ev.terms(text) if " " not in t}


def same(a: str, b: str) -> bool:
    """Whether two descriptions show the same thing (enough of their words in common)."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return True
    return len(x & y) * 1000 >= SAME_SHARE * min(len(x), len(y))


def merge(candidate: Candidate, middle: str, frames: Sequence[tuple[int, ev.Item, str | None, int | None]],
          found: ev.Evidence) -> list[tuple[Candidate, ev.Evidence]]:
    """``candidate`` with what its extra ``frames`` (source ms, a vision item, the frame's keyframe file, its
    difference hash) show.

    Frames that agree with the middle frame become more evidence of the same shot; frames that show something else
    split the shot at the midpoints between the frames (each part at least ``MIN_CANDIDATE_MS``), and each part
    keeps the shot's other evidence (notes, subtitles, file name) and the description of its own frame.
    """
    if not frames:
        return [(candidate, found)]
    if all(same(middle, item.text) for _, item, _, _ in frames):
        for _, item, _, _ in frames:
            found.add(item)
        return [(candidate, found)]
    points = sorted([*frames, (candidate.middle_ms, None, candidate.keyframe, candidate.dhash)], key=lambda p: p[0])
    bounds = [candidate.start_ms] + [(a[0] + b[0]) // 2 for a, b in zip(points, points[1:])] + [candidate.end_ms]
    if any(b - a < MIN_CANDIDATE_MS for a, b in zip(bounds, bounds[1:])):
        for _, item, _, _ in frames:
            found.add(item)
        return [(candidate, found)]
    shared = [i for i in found.items if i.origin != "vision"]
    own_vision = [i for i in found.items if i.origin == "vision"]
    out = []
    for number, ((at, item, key, hashed), start, end) in enumerate(zip(points, bounds, bounds[1:]), 1):
        part = replace(candidate, candidate_id=f"{candidate.candidate_id}{SPLIT_MARK}{number}", start_ms=start, end_ms=end,
                       keyframe=key or candidate.keyframe, dhash=candidate.dhash if hashed is None else hashed)
        evidence = ev.Evidence(part.candidate_id, list(shared) + ([item] if item is not None else list(own_vision)),
                               found.product_box if item is None else None, found.hook if item is None else 0,
                               found.product_visible, found.same_product)
        out.append((part, evidence))
    return out


K = TypeVar("K")


def look_again(ids: Sequence[str], candidates: Mapping[K, Sequence[Candidate]], evidence: dict[str, ev.Evidence],
               describer: Any, folder: Path, video_of: Mapping[str, Path], *, grab: Callable[[Path, int, str, str], None],
               measure: Callable[[bytes], tuple[Any, int]], detail: str,
               cancelled: Callable[[], bool] = lambda: False) -> tuple[dict[K, list[Candidate]], int]:
    """Production step 2: grab, measure and describe the extra frames of ``ids``, then merge or split each shot.

    ``grab(video, at_ms, jpeg, grey)`` writes a frame into ``folder`` (``candidates.grab_argv``); an unreadable or
    unusable frame is skipped. Returns the candidates in the same shape and the number of frames described.
    """
    from .vision import item_of  # vision imports nothing of this module

    by_id = {c.candidate_id: c for mine in candidates.values() for c in mine}
    asked: list[tuple[str, Path]] = []
    grabbed: dict[str, list[tuple[int, str, int]]] = {}
    for candidate_id in ids:
        candidate = by_id.get(candidate_id)
        if candidate is None or not candidate.keyframe or candidate.source_id not in video_of:
            continue
        stem = candidate.keyframe.rsplit(".", 1)[0]
        for number, at in zip((1, 3), extra_times(candidate)):
            if cancelled():
                return {k: list(v) for k, v in candidates.items()}, 0
            jpeg, grey = f"{stem}{SPLIT_MARK}{number}.jpg", f"{stem}{SPLIT_MARK}{number}.grey"
            try:
                grab(video_of[candidate.source_id], at, jpeg, grey)
                quality, hashed = measure((folder / grey).read_bytes())
            except Exception:  # noqa: BLE001 - a frame that cannot be read is simply not looked at
                continue
            finally:
                (folder / grey).unlink(missing_ok=True)
            if quality.usable and (folder / jpeg).is_file():
                key = f"{candidate_id}@{at}"
                asked.append((key, folder / jpeg))
                grabbed.setdefault(candidate_id, []).append((at, jpeg, hashed))
    seen = describer.describe(asked, cancelled) if asked else {}
    out: dict[K, list[Candidate]] = {}
    for source_key, mine in candidates.items():
        out[source_key] = []
        for candidate in mine:
            extra = [(at, item_of(seen[f"{candidate.candidate_id}@{at}"], detail), jpeg, hashed)
                     for at, jpeg, hashed in grabbed.get(candidate.candidate_id, ()) if f"{candidate.candidate_id}@{at}" in seen]
            if not extra:
                out[source_key].append(candidate)
                continue
            middle = evidence[candidate.candidate_id].summary()
            for part, found in merge(candidate, middle, extra, evidence.pop(candidate.candidate_id)):
                evidence[part.candidate_id] = found
                out[source_key].append(part)
    return out, len(seen)


def parent(candidate_id: str) -> str:
    """The shot a sub-shot was split from (itself when it is not one)."""
    head, mark, tail = candidate_id.rpartition(SPLIT_MARK)
    return head if mark and tail.isdigit() and "c" in head else candidate_id


__all__ = ["EXTRA_POSITIONS", "EXTRA_SHOTS", "FALLBACK_POSITIONS", "LONG_MS", "extra_times", "fallback_times", "look_again",
           "merge", "parent", "same", "wanted"]
