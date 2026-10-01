"""Production-safe OCR cleanup planning and verification.

The planner is intentionally conservative.  It emits a deterministic VIS-3
to VIS-0 decision chain and only authorizes a VIS-1 adaptive-cover operation
for high-confidence dialogue tracks without an ASR conflict.  Inpainting and
stronger cleanup backends can consume the same mask contract later; until
their benchmark and model health checks pass, they remain explicit fallback
steps and cannot remove uncertain text.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Iterable, Mapping, Sequence


I64_MAX = (1 << 63) - 1
_DEFAULT_SMOOTHING_TICKS = 100


def _tick(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX:
        raise ValueError(f"{name} must be a non-negative signed 64-bit tick")
    return value


def _bounded_text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must be bounded text")
    return value.strip()


def _confidence(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("confidence is malformed") from error
    if not parsed.is_finite() or not 0 <= parsed <= 1:
        raise ValueError("confidence must be between zero and one")
    return parsed


@dataclass(frozen=True)
class CleanupMask:
    track_id: str
    observation_id: str
    start_ticks: int
    end_ticks: int
    x: int
    y: int
    width: int
    height: int
    confidence: Decimal

    def __post_init__(self) -> None:
        _bounded_text(self.track_id, "track_id", 256)
        _bounded_text(self.observation_id, "observation_id", 256)
        _tick(self.start_ticks, "start_ticks")
        _tick(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("cleanup mask interval must be positive")
        for name, value in (("x", self.x), ("y", self.y)):
            if type(value) is not int or value < 0 or value > 1_000_000:
                raise ValueError(f"mask.{name} is invalid")
        for name, value in (("width", self.width), ("height", self.height)):
            if type(value) is not int or value < 1 or value > 1_000_000:
                raise ValueError(f"mask.{name} is invalid")
        if not isinstance(self.confidence, Decimal) or not self.confidence.is_finite() or not 0 <= self.confidence <= 1:
            raise ValueError("mask confidence is invalid")

    @property
    def area(self) -> int:
        return self.width * self.height

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "observation_id": self.observation_id,
            "start_ticks": str(self.start_ticks),
            "end_ticks": str(self.end_ticks),
            "bbox": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "confidence": format(self.confidence, "f"),
        }

    def filter_expression(self) -> str:
        """Return a shell-independent FFmpeg delogo expression."""

        start = _format_seconds(self.start_ticks)
        end = _format_seconds(self.end_ticks)
        return f"delogo=x={self.x}:y={self.y}:w={self.width}:h={self.height}:enable='between(t,{start},{end})'"


@dataclass(frozen=True)
class CleanupPlan:
    backend_id: str
    mode: str
    decision: str
    auto_remove: bool
    fallback_chain: tuple[str, ...]
    masks: tuple[CleanupMask, ...]
    warnings: tuple[str, ...]
    affected_ranges: tuple[dict[str, object], ...]
    smoothing_ticks: int

    def __post_init__(self) -> None:
        _bounded_text(self.backend_id, "backend_id", 128)
        if self.mode not in {"cover", "inpaint", "keep"}:
            raise ValueError("cleanup mode is invalid")
        if self.decision not in {"PROMOTE", "EXPERIMENTAL", "FALLBACK", "REVIEW"}:
            raise ValueError("cleanup decision is invalid")
        if type(self.auto_remove) is not bool:
            raise ValueError("auto_remove must be boolean")
        if not self.fallback_chain or self.fallback_chain[-1] != "vis-0-source":
            raise ValueError("fallback chain must terminate at vis-0-source")
        if type(self.smoothing_ticks) is not int or self.smoothing_ticks < 0:
            raise ValueError("smoothing_ticks must be non-negative")

    @property
    def selected(self) -> bool:
        return self.auto_remove and bool(self.masks)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "backend_id": self.backend_id,
            "mode": self.mode,
            "decision": self.decision,
            "auto_remove": self.auto_remove,
            "fallback_chain": list(self.fallback_chain),
            "masks": [mask.to_dict() for mask in self.masks],
            "warnings": list(self.warnings),
            "affected_ranges": list(self.affected_ranges),
            "smoothing_ticks": self.smoothing_ticks,
        }


def _format_seconds(ticks: int) -> str:
    value = Decimal(ticks) / Decimal(1000)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _bbox_from_segment(segment: Mapping[str, object]) -> tuple[int, int, int, int] | None:
    bbox = segment.get("bbox")
    if isinstance(bbox, Mapping):
        try:
            x, y, width, height = (int(bbox[key]) for key in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError):
            return None
        if x >= 0 and y >= 0 and width > 0 and height > 0:
            return x, y, width, height
    polygon = segment.get("polygon")
    if isinstance(polygon, Mapping) and isinstance(polygon.get("points"), list):
        points = polygon["points"]
        if points:
            try:
                xs = [int(point[0]) for point in points if isinstance(point, (list, tuple)) and len(point) == 2]
                ys = [int(point[1]) for point in points if isinstance(point, (list, tuple)) and len(point) == 2]
            except (TypeError, ValueError):
                return None
            if xs and ys and min(xs) >= 0 and min(ys) >= 0:
                return min(xs), min(ys), max(1, max(xs) - min(xs)), max(1, max(ys) - min(ys))
    return None


def _clamp_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int] | None:
    x, y, box_width, box_height = box
    if width <= 0 or height <= 0:
        return None
    right = min(width, x + box_width)
    bottom = min(height, y + box_height)
    x = min(max(0, x), width - 1)
    y = min(max(0, y), height - 1)
    if right <= x or bottom <= y:
        return None
    return x, y, right - x, bottom - y


def _iou(left: CleanupMask, right: CleanupMask) -> float:
    intersection = max(0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x)) * max(0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y))
    union = left.area + right.area - intersection
    return intersection / union if union else 0.0


def _smooth_masks(masks: Sequence[CleanupMask], smoothing_ticks: int) -> tuple[CleanupMask, ...]:
    result: list[CleanupMask] = []
    for mask in sorted(masks, key=lambda item: (item.track_id, item.start_ticks, item.end_ticks, item.observation_id)):
        if result and result[-1].track_id == mask.track_id and mask.start_ticks <= result[-1].end_ticks + smoothing_ticks and _iou(result[-1], mask) >= 0.5:
            previous = result[-1]
            result[-1] = CleanupMask(
                previous.track_id,
                previous.observation_id,
                previous.start_ticks,
                max(previous.end_ticks, mask.end_ticks),
                min(previous.x, mask.x),
                min(previous.y, mask.y),
                max(previous.x + previous.width, mask.x + mask.width) - min(previous.x, mask.x),
                max(previous.y + previous.height, mask.y + mask.height) - min(previous.y, mask.y),
                min(previous.confidence, mask.confidence),
            )
        else:
            result.append(mask)
    return tuple(result)


def plan_cleanup(
    document: Mapping[str, object],
    *,
    width: int,
    height: int,
    minimum_confidence: Decimal | str | float = Decimal("0.9"),
    smoothing_ticks: int = _DEFAULT_SMOOTHING_TICKS,
) -> CleanupPlan:
    """Build a conservative VIS-3→VIS-0 plan from one text-track document."""

    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("video dimensions must be positive integers")
    threshold = _confidence(minimum_confidence)
    if type(smoothing_ticks) is not int or smoothing_ticks < 0 or smoothing_ticks > I64_MAX:
        raise ValueError("smoothing_ticks is invalid")
    tracks = document.get("tracks") if isinstance(document, Mapping) else None
    conflicts = document.get("conflicts") if isinstance(document, Mapping) else None
    if not isinstance(tracks, list):
        tracks = []
    conflict_ids = {item.get("track_id") for item in conflicts or [] if isinstance(item, Mapping)}
    candidates: list[CleanupMask] = []
    warnings: list[str] = []
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        track_id = track.get("track_id")
        role = track.get("role")
        decision = track.get("decision")
        try:
            confidence = _confidence(track.get("confidence", "0"))
        except ValueError:
            continue
        # Automatic removal is intentionally restricted to explicit dialogue
        # tracks.  Watermarks, signage, UI, danmaku and unknown text remain in
        # the source or require review even when their OCR score is high.
        if role != "dialogue" or decision != "keep" or confidence < threshold:
            if role in {"watermark", "signage", "ui", "danmaku", "unknown"}:
                warnings.append(f"preserved uncertain/non-dialogue track {track_id}")
            continue
        if track_id in conflict_ids:
            warnings.append(f"preserved ASR/OCR-conflict track {track_id}")
            continue
        segments = track.get("segments")
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            try:
                start_ticks = _tick(int(segment.get("start_ticks")), "segment.start_ticks")
                end_ticks = _tick(int(segment.get("end_ticks")), "segment.end_ticks")
            except (TypeError, ValueError):
                continue
            if end_ticks <= start_ticks:
                continue
            box = _clamp_box(_bbox_from_segment(segment) or (0, 0, 0, 0), width, height)
            if box is None:
                warnings.append(f"skipped invalid cleanup geometry for {track_id}")
                continue
            x, y, box_width, box_height = box
            if box_width * box_height > width * height * 0.35:
                warnings.append(f"preserved oversized cleanup mask for {track_id}")
                continue
            try:
                segment_confidence = min(confidence, _confidence(segment.get("confidence", confidence)))
                observation_id = _bounded_text(segment.get("observation_id"), "observation_id", 256)
            except ValueError:
                continue
            candidates.append(CleanupMask(str(track_id), observation_id, start_ticks, end_ticks, x, y, box_width, box_height, segment_confidence))
    masks = _smooth_masks(candidates, smoothing_ticks)
    if not masks:
        warnings.append("no high-confidence dialogue masks; retained source text")
        return CleanupPlan("vis-0-source", "keep", "FALLBACK", False, ("vis-3-inpaint", "vis-2-inpaint", "vis-1-cover", "vis-0-source"), (), tuple(dict.fromkeys(warnings)), (), smoothing_ticks)
    affected_ranges = tuple({"start_ticks": str(mask.start_ticks), "end_ticks": str(mask.end_ticks), "track_id": mask.track_id} for mask in masks)
    warnings.append("VIS-3/VIS-2 inpaint backends are not health-checked; selected VIS-1 adaptive cover")
    return CleanupPlan("vis-1-cover", "cover", "EXPERIMENTAL", True, ("vis-3-inpaint", "vis-2-inpaint", "vis-1-cover", "vis-0-source"), masks, tuple(dict.fromkeys(warnings)), affected_ranges, smoothing_ticks)


def verify_cleaned_media(source_probe: object, cleaned_probe: object) -> dict[str, object]:
    """Validate container-level invariants after a cleanup render."""

    source_duration = getattr(source_probe, "duration_ticks", None)
    cleaned_duration = getattr(cleaned_probe, "duration_ticks", None)
    source_video = getattr(source_probe, "video", None)
    cleaned_video = getattr(cleaned_probe, "video", None)
    source_has_audio = bool(getattr(source_probe, "has_audio", False))
    cleaned_has_audio = bool(getattr(cleaned_probe, "has_audio", False))
    reasons: list[str] = []
    if source_duration is not None and cleaned_duration is not None and cleaned_duration + 2_000 < source_duration:
        reasons.append("duration shortened beyond tolerance")
    if source_has_audio and not cleaned_has_audio:
        reasons.append("source audio was dropped")
    if source_video is None or cleaned_video is None:
        reasons.append("video stream metadata is unavailable")
    elif getattr(cleaned_video, "codec_name", None) not in {"h264", "hevc", "vp9", "av1", getattr(source_video, "codec_name", None)}:
        reasons.append("cleaned video codec is unsupported")
    return {"status": "passed" if not reasons else "failed", "reasons": reasons, "source_duration_ticks": source_duration, "cleaned_duration_ticks": cleaned_duration, "source_has_audio": source_has_audio, "cleaned_has_audio": cleaned_has_audio}


__all__ = ["CleanupMask", "CleanupPlan", "plan_cleanup", "verify_cleaned_media"]
