"""Deterministic OCR temporal tracker and conservative role classifier."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

from .orientation.tracker import Polygon


I64_MAX = (1 << 63) - 1
ROLES = {"dialogue", "watermark", "signage", "danmaku", "ui", "unknown"}
DECISION = {"keep", "remove", "review"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKER = re.compile(r"(?:watermark|logo|official|\.com|https?://|@|版权|关注|扫码)", re.IGNORECASE)


def _text(value: object, name: str, limit: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise ValueError(f"{name} must be bounded text")
    return value.strip()


def _ticks(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX:
        raise ValueError(f"{name} must be a non-negative signed 64-bit tick")
    return value


def _confidence(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("confidence is malformed") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise ValueError("confidence must be between zero and one")
    return result


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _similarity(left: str, right: str) -> float:
    left_tokens = set(_norm(left))
    right_tokens = set(_norm(right))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


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
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        intersection = max(0, right - left) * max(0, bottom - top)
        union = self.width * self.height + other.width * other.height - intersection
        return intersection / union if union else 0.0

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class OcrObservation:
    observation_id: str
    frame_index: int
    start_ticks: int
    end_ticks: int
    text: str
    bbox: BBox
    confidence: Decimal | str | float = Decimal("0")
    role_hint: str | None = None
    asr_text: str | None = None
    asr_utterance_id: str | None = None
    # Polygon is optional for backwards compatibility with the original
    # horizontal-observation contract.  Production OCR observations retain the
    # detector polygon so cleanup and orientation stages never have to
    # reconstruct geometry from a lossy axis-aligned box.
    polygon: Polygon | None = None

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id", 256)
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ticks(self.start_ticks, "start_ticks")
        _ticks(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("OCR interval must be positive")
        object.__setattr__(self, "text", _text(self.text, "text"))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if self.role_hint is not None and self.role_hint not in ROLES:
            raise ValueError("role_hint is invalid")
        if self.asr_text is not None:
            object.__setattr__(self, "asr_text", _text(self.asr_text, "asr_text"))
            if self.asr_utterance_id is None:
                raise ValueError("asr_utterance_id is required with asr_text")
        if self.asr_utterance_id is not None:
            _text(self.asr_utterance_id, "asr_utterance_id", 256)
        if self.polygon is not None and not isinstance(self.polygon, Polygon):
            raise ValueError("polygon must be a Polygon")


@dataclass(frozen=True)
class TextTrackDocument:
    job_id: str
    dataset_revision: str
    tracks: tuple[dict[str, object], ...]
    conflicts: tuple[dict[str, object], ...]
    debug_overlay: dict[str, object]
    benchmark: "BenchmarkReport"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "text_track_document",
            "job_id": self.job_id,
            "dataset_revision": self.dataset_revision,
            "tracks": list(self.tracks),
            "conflicts": list(self.conflicts),
            "debug_overlay": self.debug_overlay,
            "benchmark": self.benchmark.to_dict(),
        }


@dataclass(frozen=True)
class BenchmarkReport:
    dataset_revision: str
    precision: Decimal
    recall: Decimal
    f1: Decimal
    cer: Decimal
    latency_ms: int
    peak_memory_bytes: int
    decision: str

    def __post_init__(self) -> None:
        _text(self.dataset_revision, "dataset_revision", 128)
        for name in ("precision", "recall", "f1", "cer"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"benchmark {name} is invalid")
        if type(self.latency_ms) is not int or self.latency_ms < 0 or type(self.peak_memory_bytes) is not int or self.peak_memory_bytes < 0:
            raise ValueError("benchmark resource values are invalid")
        if self.decision not in {"PROMOTE", "EXPERIMENTAL", "REJECT", "SPLIT"}:
            raise ValueError("benchmark decision is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"dataset_revision": self.dataset_revision, "precision": format(self.precision, "f"), "recall": format(self.recall, "f"), "f1": format(self.f1, "f"), "cer": format(self.cer, "f"), "latency_ms": self.latency_ms, "peak_memory_bytes": self.peak_memory_bytes, "decision": self.decision}

    @classmethod
    def from_labels(cls, dataset_revision: str, expected: Sequence[str], predicted: Sequence[str], expected_text: Sequence[str], predicted_text: Sequence[str], *, latency_ms: int = 0, peak_memory_bytes: int = 0) -> "BenchmarkReport":
        if len(expected) != len(predicted) or len(expected_text) != len(predicted_text) or len(expected) != len(expected_text) or not expected:
            raise ValueError("benchmark label/text lengths must match and be non-empty")
        positive = {role for role in expected if role != "unknown"}
        predicted_positive = {role for role in predicted if role != "unknown"}
        true_positive = sum(1 for actual, guess in zip(expected, predicted) if actual == guess and actual != "unknown")
        precision = Decimal(true_positive) / Decimal(max(1, sum(1 for role in predicted if role != "unknown")))
        recall = Decimal(true_positive) / Decimal(max(1, sum(1 for role in expected if role != "unknown")))
        f1 = Decimal(0) if precision + recall == 0 else (Decimal(2) * precision * recall / (precision + recall))
        distance = sum(_levenshtein(_norm(actual), _norm(guess)) for actual, guess in zip(expected_text, predicted_text))
        chars = sum(max(1, len(_norm(actual))) for actual in expected_text)
        cer = Decimal(distance) / Decimal(chars)
        decision = "PROMOTE" if f1 >= Decimal("0.9") and cer <= Decimal("0.1") else ("REJECT" if f1 < Decimal("0.5") else "EXPERIMENTAL")
        return cls(dataset_revision, precision, recall, f1, cer, latency_ms, peak_memory_bytes, decision)


def _levenshtein(left: str, right: str) -> int:
    row = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        previous = row[0]
        row[0] = i
        for j, right_char in enumerate(right, 1):
            old = row[j]
            row[j] = min(row[j] + 1, row[j - 1] + 1, previous + (left_char != right_char))
            previous = old
    return row[-1]


class TextIntelligence:
    def __init__(self, *, iou_threshold: float = 0.2, text_threshold: float = 0.2) -> None:
        if not math.isfinite(iou_threshold) or not 0 <= iou_threshold <= 1 or not math.isfinite(text_threshold) or not 0 <= text_threshold <= 1:
            raise ValueError("tracking thresholds must be in [0, 1]")
        self.iou_threshold = iou_threshold
        self.text_threshold = text_threshold

    def track(self, job_id: str, observations: Iterable[OcrObservation], *, dataset_revision: str = "fixture-v1", benchmark: BenchmarkReport | None = None) -> TextTrackDocument:
        _text(job_id, "job_id", 256)
        _text(dataset_revision, "dataset_revision", 128)
        records = sorted(tuple(observations), key=lambda item: (item.start_ticks, item.frame_index, item.observation_id))
        tracks: dict[str, list[OcrObservation]] = {}
        last: dict[str, OcrObservation] = {}
        for observation in records:
            candidates: list[tuple[float, str]] = []
            for track_id, previous in last.items():
                if observation.start_ticks > previous.end_ticks + 1_000_000:
                    continue
                iou = observation.bbox.iou(previous.bbox)
                similarity = _similarity(observation.text, previous.text)
                score = (iou + similarity) / 2
                if iou >= self.iou_threshold and similarity >= self.text_threshold:
                    candidates.append((score, track_id))
            if candidates:
                track_id = max(candidates, key=lambda value: (value[0], value[1]))[1]
            else:
                seed = f"{job_id}\0{observation.observation_id}\0{_norm(observation.text)}\0{observation.bbox.to_dict()}".encode()
                track_id = "txt-" + hashlib.sha256(seed).hexdigest()[:16]
                tracks[track_id] = []
            tracks[track_id].append(observation)
            last[track_id] = observation
        track_payload: list[dict[str, object]] = []
        conflicts: list[dict[str, object]] = []
        for track_id in sorted(tracks):
            values = tracks[track_id]
            role, decision, confidence = self._classify(values)
            segments = []
            for value in values:
                segment = {
                    "observation_id": value.observation_id,
                    "start_ticks": str(value.start_ticks),
                    "end_ticks": str(value.end_ticks),
                    "text": value.text,
                    "bbox": value.bbox.to_dict(),
                    "confidence": format(value.confidence, "f"),
                }
                if value.polygon is not None:
                    segment["polygon"] = value.polygon.to_dict()
                segments.append(segment)
            track_payload.append({"track_id": track_id, "role": role, "decision": decision, "confidence": format(confidence, "f"), "segments": segments})
            for value in values:
                if value.asr_text is not None and _similarity(value.text, value.asr_text) < 0.5:
                    conflicts.append({"track_id": track_id, "observation_id": value.observation_id, "asr_utterance_id": value.asr_utterance_id, "ocr_text": value.text, "asr_text": value.asr_text, "reason": "ASR_OCR_CONFLICT"})
        overlay_data = {"schema_version": 1, "job_id": job_id, "tracks": [{"track_id": track["track_id"], "segments": track["segments"]} for track in track_payload]}
        encoded = json.dumps(overlay_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        overlay = {"path": "debug_overlays/" + job_id + ".json", "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "track_count": len(track_payload)}
        report = benchmark or BenchmarkReport(dataset_revision, Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, 0, "EXPERIMENTAL")
        return TextTrackDocument(job_id, dataset_revision, tuple(track_payload), tuple(conflicts), overlay, report)

    def _classify(self, values: Sequence[OcrObservation]) -> tuple[str, str, Decimal]:
        hints = [value.role_hint for value in values if value.role_hint is not None]
        if hints:
            role = max(set(hints), key=lambda value: (hints.count(value), value))
            confidence = max((value.confidence for value in values if value.role_hint == role), default=Decimal(0))
        elif any(value.asr_text is not None and _similarity(value.text, value.asr_text) >= 0.5 for value in values):
            role, confidence = "dialogue", max(value.confidence for value in values)
        elif any(_MARKER.search(value.text) for value in values):
            role, confidence = "watermark", max(value.confidence for value in values)
        else:
            role, confidence = "unknown", max(value.confidence for value in values)
        has_conflict = any(value.asr_text is not None and _similarity(value.text, value.asr_text) < 0.5 for value in values)
        if role in {"watermark", "danmaku"} and confidence >= Decimal("0.9") and not has_conflict:
            return role, "remove", confidence
        if role == "dialogue":
            return role, "keep", confidence
        return role, "review", confidence


__all__ = ["BenchmarkReport", "BBox", "OcrObservation", "TextIntelligence", "TextTrackDocument"]
