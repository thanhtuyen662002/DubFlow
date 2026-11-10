"""Bán hàng: the seller's clips cut at their own shot boundaries (Phase 1 hookup, ADR-0052).

Without mastering a product video splits its narration into equal parts of at
most 5 s and plays the visuals in turn (``product/render.plan``), so a part
often runs across a cut inside a clip and shows two camera angles in one
"scene". With mastering on, each part lies inside one shot of its clip (kept
``SHOT_MARGIN_MS`` away from the cut, so no frame of the next angle shows),
lasts about the style's target length, and the visuals still take turns in the
seller's order. A shot used again continues where it stopped. Photos stay
stills. The plan is checked against the workflow policy before it is used.

Hook, zoom, captions and colour are allowed by the policy at higher levels but
are planned in Phase 2; this planner only decides the cuts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .. import analysis as scene
from .. import plan as m
from .. import policy as rules

PLANNER = "dubflow.mastering.product"
VERSION = "1"
RECIPE = "product-shot-aware-v1"
WORKFLOW = "product_showcase"
SHOT_MARGIN_MS = 120
# A shot shorter than this is skipped while longer ones exist; it is used when nothing else is left.
MIN_PIECE_MS = 1000


@dataclass(frozen=True)
class Visual:
    path: Path
    # None for a photo.
    duration_ms: int | None
    width: int
    height: int
    # The clip's analysis, or None for a photo.
    analysis: scene.Analysis | None = None


@dataclass(frozen=True)
class Piece:
    """A segment of the output: ``duration_ms`` of ``visual`` from ``offset_ms`` (0 for a photo)."""

    visual: int
    offset_ms: int
    duration_ms: int


class _Clip:
    """The usable windows of one clip, each inside one shot, and where each was left off."""

    def __init__(self, analysis: scene.Analysis) -> None:
        windows = []
        for shot in analysis.shots:
            start = shot.start_ms + (SHOT_MARGIN_MS if shot.start_ms > 0 else 0)
            end = shot.end_ms - (SHOT_MARGIN_MS if shot.end_ms < analysis.duration_ms else 0)
            if end - start > 0:
                windows.append((start, end))
        long = [w for w in windows if w[1] - w[0] >= MIN_PIECE_MS]
        self.windows = long or windows or [(0, analysis.duration_ms)]
        self.crossed = not long
        self.turn = 0
        self.resume = {index: start for index, (start, _) in enumerate(self.windows)}

    def take(self, wanted: int) -> tuple[int, int, int]:
        """(window index, offset, length) of the next piece, at most ``wanted`` long."""
        index = self.turn % len(self.windows)
        self.turn += 1
        start, end = self.windows[index]
        offset = self.resume[index]
        if end - offset < min(wanted, MIN_PIECE_MS, end - start):
            offset = start  # what was left of this shot is too short: play it from its start again
        length = min(wanted, end - offset)
        self.resume[index] = offset + length if offset + length < end else start
        return index, offset, length


def plan(visuals: Sequence[Visual], total_ms: int, options: m.Options, *, inputs: Mapping[str, str],
         plan_id: str = "product") -> m.Plan:
    """The mastering plan for one product video of ``total_ms``; ``inputs`` are the digests it is made from."""
    policy = rules.resolve(WORKFLOW, options)
    if not policy.enabled:
        raise ValueError("mastering is off: render the legacy plan")
    if not visuals or total_ms <= 0:
        raise ValueError("a product video needs visuals and a positive length")
    pacing = policy.pacing
    clips = {i: _Clip(v.analysis) for i, v in enumerate(visuals) if v.duration_ms is not None and v.analysis is not None}
    warnings = [f"Clip {i + 1} không có cú máy nào dài từ {MIN_PIECE_MS / 1000:g} s; đoạn có thể ngắn"
                for i, clip in clips.items() if clip.crossed]
    segments: list[m.Segment] = []
    at, turn, last = 0, 0, None
    while at < total_ms:
        remaining = total_ms - at
        # The last part takes what is left when that is shorter than a part should be.
        wanted = remaining if remaining < pacing.target + pacing.minimum else pacing.target
        index = turn % len(visuals)
        turn += 1
        visual = visuals[index]
        reasons: list[str] = []
        confidence = 900
        if visual.duration_ms is None:
            length, source = wanted, m.Span(0, 0)
            role = "still"
        else:
            clip = clips[index]
            window, offset, length = clip.take(wanted)
            if (index, window) == last:
                reasons.append("same shot as the previous part (only one usable shot)")
                confidence = 600
            last = (index, window)
            source = m.Span(offset, offset + length)
            role = "demonstration"
            reasons.append("inside one shot of the clip")
            if length < wanted:
                reasons.append("shot ends before the target length")
        if visual.duration_ms is None:
            last = None
        segments.append(m.Segment(f"p{len(segments) + 1}", f"v{index + 1}", source, m.Span(at, at + length), role=role,
                                  audio_mode="narration", confidence=confidence, needs_review=confidence < 700,
                                  reasons=tuple(reasons)))
        at += length
    sources = tuple(m.Source(f"v{i + 1}", 0 if v.duration_ms is None else v.duration_ms, max(1, v.width), max(1, v.height),
                             has_audio=bool(v.analysis and v.analysis.has_audio),
                             rights=m.Rights("unknown", "Ảnh/clip do người dùng cung cấp")) for i, v in enumerate(visuals))
    result = m.Plan(plan_id, WORKFLOW, options, total_ms, sources, tuple(segments),
                    m.Provenance(PLANNER, VERSION, RECIPE, tuple(sorted(inputs.items()))), warnings=tuple(warnings))
    problems = rules.check(result, policy)
    if problems:
        raise ValueError("; ".join(problems))
    return result


def pieces(saved: m.Plan) -> list[Piece]:
    """The plan as the product renderer's parts, in milliseconds."""
    out = []
    for segment in saved.segments:
        index = int(segment.source_id[1:]) - 1
        start = m.to_milliseconds(segment.source.start, saved.time_base, "floor")
        begin = m.to_milliseconds(segment.timeline.start, saved.time_base, "floor")
        end = m.to_milliseconds(segment.timeline.end, saved.time_base, "floor")
        out.append(Piece(index, start, end - begin))
    return out


def inputs(visuals: Sequence[Path], total_ms: int, options: m.Options) -> dict[str, str]:
    """What a saved plan must have been made from to be reused: each file (size, mtime), the length and the options."""
    found = {f"v{i + 1}": m.file_identity(path, scene.RECIPE) for i, path in enumerate(visuals)}
    found["timeline"] = m.digest_json({"total_ms": total_ms, "visuals": len(visuals), "level": options.level, "style": options.style})
    return found
