"""Deterministic geometry-aware text tracking with safe fallback."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
import re
import unicodedata
from typing import Iterable, Sequence


I64_MAX = (1 << 63) - 1
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: object, name: str, limit: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise ValueError(f"{name} must be bounded text")
    return value.strip()


def _ticks(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX:
        raise ValueError(f"{name} must be a non-negative signed 64-bit tick")
    return value


@dataclass(frozen=True)
class Polygon:
    points: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if len(self.points) < 4 or len(self.points) > 64:
            raise ValueError("polygon must contain four to 64 points")
        for point in self.points:
            if not isinstance(point, tuple) or len(point) != 2 or any(type(value) is not int or value < 0 or value > 1_000_000 for value in point):
                raise ValueError("polygon points must be bounded integer pixel coordinates")
        if self.area <= 0:
            raise ValueError("polygon area must be positive")

    @property
    def area(self) -> int:
        return abs(sum(self.points[index][0] * self.points[(index + 1) % len(self.points)][1] - self.points[(index + 1) % len(self.points)][0] * self.points[index][1] for index in range(len(self.points))) // 2)

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def bbox_iou(self, other: "Polygon") -> float:
        left_a, top_a, right_a, bottom_a = self.bounds
        left_b, top_b, right_b, bottom_b = other.bounds
        intersection = max(0, min(right_a, right_b) - max(left_a, left_b)) * max(0, min(bottom_a, bottom_b) - max(top_a, top_b))
        area_a = max(1, right_a - left_a) * max(1, bottom_a - top_a)
        area_b = max(1, right_b - left_b) * max(1, bottom_b - top_b)
        return intersection / (area_a + area_b - intersection) if area_a + area_b - intersection else 0.0

    def to_dict(self) -> dict[str, object]:
        return {"points": [[x, y] for x, y in self.points]}


@dataclass(frozen=True)
class OrientedObservation:
    observation_id: str
    frame_index: int
    start_ticks: int
    end_ticks: int
    text: str
    polygon: Polygon
    angle_milli_degrees: int
    confidence: Decimal | str | float = Decimal("0")
    karaoke_progress_milli: int | None = None

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id", 256)
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ticks(self.start_ticks, "start_ticks")
        _ticks(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("oriented interval must be positive")
        _text(self.text, "text")
        if type(self.angle_milli_degrees) is not int or not -180_000 <= self.angle_milli_degrees <= 180_000:
            raise ValueError("angle is outside the supported range")
        confidence = Decimal(str(self.confidence))
        if not confidence.is_finite() or confidence < 0 or confidence > 1:
            raise ValueError("confidence must be between zero and one")
        object.__setattr__(self, "confidence", confidence)
        if self.karaoke_progress_milli is not None and (type(self.karaoke_progress_milli) is not int or not 0 <= self.karaoke_progress_milli <= 1000):
            raise ValueError("karaoke progress must be between 0 and 1000 milli")


@dataclass(frozen=True)
class OrientedTrack:
    track_id: str
    segments: tuple[dict[str, object], ...]
    rectification_quality: Decimal
    decision: str

    def __post_init__(self) -> None:
        if not isinstance(self.track_id, str) or not self.track_id.startswith("txt-orient-"):
            raise ValueError("oriented track ID is invalid")
        if self.decision not in {"keep", "review"}:
            raise ValueError("oriented decision is invalid")
        if not self.segments:
            raise ValueError("oriented track requires segments")
        if not self.rectification_quality.is_finite() or not 0 <= self.rectification_quality <= 1:
            raise ValueError("rectification quality is invalid")


@dataclass(frozen=True)
class OrientationBenchmark:
    dataset_revision: str
    oriented_recall: Decimal
    fragmentation_rate: Decimal
    temporal_consistency: Decimal
    cer: Decimal
    latency_ms: int
    peak_memory_bytes: int
    decision: str

    def __post_init__(self) -> None:
        _text(self.dataset_revision, "dataset_revision", 128)
        for name in ("oriented_recall", "fragmentation_rate", "temporal_consistency", "cer"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be between zero and one")
        if type(self.latency_ms) is not int or self.latency_ms < 0 or type(self.peak_memory_bytes) is not int or self.peak_memory_bytes < 0:
            raise ValueError("resource metrics must be non-negative integers")
        if self.decision not in {"PROMOTE", "EXPERIMENTAL", "REJECT", "SPLIT"}:
            raise ValueError("benchmark decision is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"dataset_revision": self.dataset_revision, "oriented_recall": format(self.oriented_recall, "f"), "fragmentation_rate": format(self.fragmentation_rate, "f"), "temporal_consistency": format(self.temporal_consistency, "f"), "cer": format(self.cer, "f"), "latency_ms": self.latency_ms, "peak_memory_bytes": self.peak_memory_bytes, "decision": self.decision}


class AnimatedTextTracker:
    def __init__(self, *, iou_threshold: float = 0.15, text_threshold: float = 0.15) -> None:
        if not math.isfinite(iou_threshold) or not 0 <= iou_threshold <= 1 or not math.isfinite(text_threshold) or not 0 <= text_threshold <= 1:
            raise ValueError("tracking thresholds must be in [0, 1]")
        self.iou_threshold = iou_threshold
        self.text_threshold = text_threshold

    def track(self, job_id: str, observations: Iterable[OrientedObservation]) -> tuple[OrientedTrack, ...]:
        _text(job_id, "job_id", 256)
        records = sorted(tuple(observations), key=lambda value: (value.start_ticks, value.frame_index, value.observation_id))
        tracks: dict[str, list[OrientedObservation]] = {}
        last: dict[str, OrientedObservation] = {}
        for observation in records:
            candidates: list[tuple[float, str]] = []
            for track_id, previous in last.items():
                if observation.start_ticks > previous.end_ticks + 1_000_000:
                    continue
                overlap = observation.polygon.bbox_iou(previous.polygon)
                text_score = self._text_similarity(observation.text, previous.text)
                if overlap >= self.iou_threshold and text_score >= self.text_threshold:
                    candidates.append(((overlap + text_score) / 2, track_id))
            if candidates:
                track_id = max(candidates, key=lambda value: (value[0], value[1]))[1]
            else:
                seed = f"{job_id}\0{observation.observation_id}\0{observation.text}\0{observation.polygon.to_dict()}".encode()
                track_id = "txt-orient-" + hashlib.sha256(seed).hexdigest()[:16]
                tracks[track_id] = []
            tracks[track_id].append(observation)
            last[track_id] = observation
        result: list[OrientedTrack] = []
        for track_id in sorted(tracks):
            values = tracks[track_id]
            quality = min(value.confidence for value in values)
            segments = [{"observation_id": value.observation_id, "start_ticks": str(value.start_ticks), "end_ticks": str(value.end_ticks), "text": value.text, "polygon": value.polygon.to_dict(), "angle_milli_degrees": value.angle_milli_degrees, "karaoke_progress_milli": value.karaoke_progress_milli, "confidence": format(value.confidence, "f")} for value in values]
            # Safe rendering is retained whenever rectification/OCR confidence
            # is weak; this prototype never auto-removes oriented text.
            result.append(OrientedTrack(track_id, tuple(segments), quality, "keep" if quality >= Decimal("0.5") else "review"))
        return tuple(result)

    @staticmethod
    def _text_similarity(left: str, right: str) -> float:
        left = set(unicodedata.normalize("NFC", left).casefold())
        right = set(unicodedata.normalize("NFC", right).casefold())
        return len(left & right) / len(left | right) if left | right else 1.0


__all__ = ["AnimatedTextTracker", "OrientationBenchmark", "OrientedObservation", "OrientedTrack", "Polygon"]
