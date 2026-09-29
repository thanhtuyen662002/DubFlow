"""Deterministic visual character tracks with audio-cluster fallback."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
import re
from typing import Iterable, Mapping, Sequence


I64_MAX = (1 << 63) - 1
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: object, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or _CONTROL.search(value):
        raise ValueError(f"{name} must be bounded text")
    return value


def _ticks(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX:
        raise ValueError(f"{name} must be a non-negative signed 64-bit tick")
    return value


def _score(value: object, name: str) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or result > 1:
        raise ValueError(f"{name} must be between zero and one")
    return result


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y), ("width", self.width), ("height", self.height)):
            if type(value) is not int or value < (1 if name in {"width", "height"} else 0) or value > 1_000_000:
                raise ValueError(f"bbox.{name} is invalid")

    def iou(self, other: "BBox") -> float:
        intersection = max(0, min(self.x + self.width, other.x + other.width) - max(self.x, other.x)) * max(0, min(self.y + self.height, other.y + other.height) - max(self.y, other.y))
        area_a = self.width * self.height
        area_b = other.width * other.height
        union = area_a + area_b - intersection
        return intersection / union if union else 0.0

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class VisualDetection:
    detection_id: str
    scene_id: str
    frame_index: int
    start_ticks: int
    end_ticks: int
    bbox: BBox
    appearance: tuple[float, ...]
    lip_activity: Decimal | str | float
    confidence: Decimal | str | float = Decimal("0")
    character_key: str | None = None
    actor_group_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.detection_id, "detection_id")
        _text(self.scene_id, "scene_id", 128)
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ticks(self.start_ticks, "start_ticks")
        _ticks(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("visual detection interval must be positive")
        if not self.appearance or len(self.appearance) > 1024 or any(not math.isfinite(float(value)) for value in self.appearance):
            raise ValueError("appearance vector must be finite and bounded")
        object.__setattr__(self, "lip_activity", _score(self.lip_activity, "lip_activity"))
        object.__setattr__(self, "confidence", _score(self.confidence, "confidence"))
        if self.character_key is not None:
            _text(self.character_key, "character_key", 128)
        if self.actor_group_id is not None:
            _text(self.actor_group_id, "actor_group_id", 128)


@dataclass(frozen=True)
class Association:
    audio_cluster_id: str
    start_ticks: int
    end_ticks: int
    visual_track_id: str | None
    status: str
    confidence: Decimal
    fallback_audio_cluster_id: str | None
    reason: str

    def __post_init__(self) -> None:
        _text(self.audio_cluster_id, "audio_cluster_id", 128)
        _ticks(self.start_ticks, "start_ticks")
        _ticks(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("association interval must be positive")
        if self.visual_track_id is not None:
            _text(self.visual_track_id, "visual_track_id", 128)
        if self.status not in {"visible", "offscreen", "unresolved"}:
            raise ValueError("association status is invalid")
        _score(self.confidence, "confidence")
        if self.fallback_audio_cluster_id is not None:
            _text(self.fallback_audio_cluster_id, "fallback_audio_cluster_id", 128)
        _text(self.reason, "reason", 256)

    def to_dict(self) -> dict[str, object]:
        return {"audio_cluster_id": self.audio_cluster_id, "start_ticks": str(self.start_ticks), "end_ticks": str(self.end_ticks), "visual_track_id": self.visual_track_id, "status": self.status, "confidence": format(self.confidence, "f"), "fallback_audio_cluster_id": self.fallback_audio_cluster_id, "reason": self.reason}


@dataclass(frozen=True)
class CharacterBenchmark:
    identity_switches: int
    active_speaker_accuracy: Decimal
    offscreen_precision: Decimal
    association_accuracy: Decimal
    latency_ms: int
    peak_memory_bytes: int

    def __post_init__(self) -> None:
        if type(self.identity_switches) is not int or self.identity_switches < 0 or type(self.latency_ms) is not int or self.latency_ms < 0 or type(self.peak_memory_bytes) is not int or self.peak_memory_bytes < 0:
            raise ValueError("benchmark integer metrics are invalid")
        for name in ("active_speaker_accuracy", "offscreen_precision", "association_accuracy"):
            _score(getattr(self, name), name)

    def to_dict(self) -> dict[str, object]:
        return {"identity_switches": self.identity_switches, "active_speaker_accuracy": format(self.active_speaker_accuracy, "f"), "offscreen_precision": format(self.offscreen_precision, "f"), "association_accuracy": format(self.association_accuracy, "f"), "latency_ms": self.latency_ms, "peak_memory_bytes": self.peak_memory_bytes}


class VisualCharacterTracker:
    def __init__(self, *, appearance_threshold: float = 0.95, iou_threshold: float = 0.1) -> None:
        if not 0 < appearance_threshold <= 1 or not 0 <= iou_threshold <= 1:
            raise ValueError("tracking thresholds are invalid")
        self.appearance_threshold = appearance_threshold
        self.iou_threshold = iou_threshold

    def build(self, job_id: str, detections: Iterable[VisualDetection]) -> tuple[dict[str, object], ...]:
        _text(job_id, "job_id")
        records = sorted(tuple(detections), key=lambda item: (item.start_ticks, item.scene_id, item.frame_index, item.detection_id))
        tracks: dict[str, list[VisualDetection]] = {}
        last: dict[str, VisualDetection] = {}
        for detection in records:
            candidates: list[tuple[float, str]] = []
            for track_id, previous in last.items():
                if detection.character_key is not None and previous.character_key != detection.character_key:
                    continue
                if detection.scene_id == previous.scene_id or detection.character_key is not None:
                    iou = detection.bbox.iou(previous.bbox)
                    similarity = self._cosine(detection.appearance, previous.appearance)
                    if (iou >= self.iou_threshold and similarity >= self.appearance_threshold) or (detection.character_key is not None and detection.character_key == previous.character_key and similarity >= self.appearance_threshold):
                        candidates.append(((iou + similarity) / 2, track_id))
            if candidates:
                track_id = max(candidates, key=lambda item: (item[0], item[1]))[1]
            else:
                seed = f"{job_id}\0{detection.character_key or detection.detection_id}\0{detection.scene_id}".encode()
                track_id = "char-" + hashlib.sha256(seed).hexdigest()[:16]
                tracks[track_id] = []
            tracks[track_id].append(detection)
            last[track_id] = detection
        payload: list[dict[str, object]] = []
        for track_id in sorted(tracks):
            values = tracks[track_id]
            payload.append({"track_id": track_id, "scene_id": values[0].scene_id, "actor_group_id": values[0].actor_group_id, "character_key": values[0].character_key, "segments": [{"frame_index": value.frame_index, "start_ticks": str(value.start_ticks), "end_ticks": str(value.end_ticks), "bbox": value.bbox.to_dict(), "lip_activity": format(value.lip_activity, "f"), "confidence": format(value.confidence, "f")} for value in values]})
        return tuple(payload)

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            return -1.0
        left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
        right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
        if left_norm <= 1e-12 or right_norm <= 1e-12:
            return -1.0
        return max(-1.0, min(1.0, sum(float(a) * float(b) for a, b in zip(left, right)) / (left_norm * right_norm)))


class ActiveSpeakerAssociator:
    def __init__(self, *, minimum_score: Decimal | str = Decimal("0.7"), minimum_margin: Decimal | str = Decimal("0.1")) -> None:
        self.minimum_score = _score(minimum_score, "minimum_score")
        self.minimum_margin = _score(minimum_margin, "minimum_margin")

    def associate(self, audio_cluster_id: str, start_ticks: int, end_ticks: int, candidate_scores: Mapping[str, Decimal | str | float], *, offscreen: bool = False) -> Association:
        _text(audio_cluster_id, "audio_cluster_id", 128)
        _ticks(start_ticks, "start_ticks")
        _ticks(end_ticks, "end_ticks")
        if end_ticks <= start_ticks:
            raise ValueError("association interval must be positive")
        if offscreen or not candidate_scores:
            return Association(audio_cluster_id, start_ticks, end_ticks, None, "offscreen", Decimal("1"), audio_cluster_id, "no visible candidate; retain audio cluster")
        scores = sorted(((track_id, _score(score, "candidate_score")) for track_id, score in candidate_scores.items()), key=lambda item: (item[1], item[0]), reverse=True)
        best_id, best_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else Decimal("0")
        if best_score < self.minimum_score or best_score - second_score < self.minimum_margin:
            return Association(audio_cluster_id, start_ticks, end_ticks, None, "unresolved", best_score, audio_cluster_id, "visual evidence is weak or ambiguous")
        return Association(audio_cluster_id, start_ticks, end_ticks, best_id, "visible", best_score, audio_cluster_id, "visible candidate exceeds score and margin")


__all__ = ["ActiveSpeakerAssociator", "Association", "BBox", "CharacterBenchmark", "VisualCharacterTracker", "VisualDetection"]
