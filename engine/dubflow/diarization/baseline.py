"""CPU-safe, deterministic speaker-cluster baseline.

The baseline consumes observations emitted by an audio backend.  Keeping the
backend outside this module makes the contract testable without model weights,
while the merge and scoring rules remain the same when a local model is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
from typing import Iterable, Sequence


DIARIZATION_CONTRACT_VERSION = 1
I64_MAX = (1 << 63) - 1
I64_MIN = -(1 << 63)
ROLES = {"speaker", "narrator", "offscreen", "unresolved"}


def _bounded_text(value: object, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be bounded text")
    return value


def _tick(value: object, name: str) -> int:
    if type(value) is not int or value < I64_MIN or value > I64_MAX:
        raise ValueError(f"{name} must be a signed 64-bit integer tick")
    return value


def _confidence(value: object) -> Decimal:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError("confidence must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("confidence is malformed") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise ValueError("confidence must be between zero and one")
    return parsed


def _vector(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or len(result) > 4096 or any(not math.isfinite(value) for value in result):
        raise ValueError("embedding must be finite and bounded")
    norm = math.sqrt(sum(value * value for value in result))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("embedding norm must be positive")
    return result


@dataclass(frozen=True)
class SpeakerObservation:
    observation_id: str
    start_ticks: int
    end_ticks: int
    local_cluster: str | None
    role: str = "speaker"
    confidence: Decimal | str | float = Decimal("0")
    embedding: tuple[float, ...] | None = None
    chunk_id: str = "chunk-0"

    def __post_init__(self) -> None:
        _bounded_text(self.observation_id, "observation_id")
        start = _tick(self.start_ticks, "start_ticks")
        end = _tick(self.end_ticks, "end_ticks")
        if start < 0 or end <= start:
            raise ValueError("speaker range must be non-negative and increasing")
        if self.role not in ROLES:
            raise ValueError("speaker role is invalid")
        if self.local_cluster is not None:
            _bounded_text(self.local_cluster, "local_cluster", 128)
        if self.role == "speaker" and not self.local_cluster:
            raise ValueError("speaker observations require a local cluster")
        if self.role != "speaker" and self.local_cluster is not None:
            raise ValueError("unresolved roles cannot carry a cluster")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if self.embedding is not None:
            object.__setattr__(self, "embedding", _vector(self.embedding))
        _bounded_text(self.chunk_id, "chunk_id", 128)


@dataclass(frozen=True)
class SpeakerSegment:
    segment_id: str
    start_ticks: int
    end_ticks: int
    speaker_cluster_ids: tuple[str, ...]
    role: str
    confidence: Decimal
    overlap_group_id: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.segment_id, "segment_id")
        _tick(self.start_ticks, "start_ticks")
        _tick(self.end_ticks, "end_ticks")
        if self.start_ticks < 0 or self.end_ticks <= self.start_ticks:
            raise ValueError("speaker segment range must be non-negative and increasing")
        if self.role not in ROLES:
            raise ValueError("speaker role is invalid")
        ids = tuple(self.speaker_cluster_ids)
        if len(ids) > 16 or len(set(ids)) != len(ids):
            raise ValueError("speaker cluster IDs must be unique and bounded")
        for cluster_id in ids:
            if not re_match_cluster(cluster_id):
                raise ValueError("speaker cluster ID is invalid")
        if self.role == "speaker" and not ids:
            raise ValueError("speaker segment requires a cluster")
        if self.role != "speaker" and ids:
            raise ValueError("unresolved segment cannot carry a cluster")
        _confidence(self.confidence)
        if self.overlap_group_id is not None and not re_match_overlap(self.overlap_group_id):
            raise ValueError("overlap group ID is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "start_ticks": str(self.start_ticks),
            "end_ticks": str(self.end_ticks),
            "speaker_cluster_ids": list(self.speaker_cluster_ids),
            "role": self.role,
            "confidence": format(self.confidence, "f"),
            "overlap_group_id": self.overlap_group_id,
        }


def re_match_cluster(value: object) -> bool:
    return isinstance(value, str) and len(value) == 20 and value.startswith("spk-") and all(char in "0123456789abcdef" for char in value[4:])


def re_match_overlap(value: object) -> bool:
    return isinstance(value, str) and len(value) == 24 and value.startswith("overlap-") and all(char in "0123456789abcdef" for char in value[8:])


@dataclass(frozen=True)
class ResourceProfile:
    input_duration_ticks: int
    observation_count: int
    elapsed_ms: int
    peak_memory_bytes: int
    backend: str = "cpu-baseline"

    def __post_init__(self) -> None:
        for name, value in (("input_duration_ticks", self.input_duration_ticks), ("observation_count", self.observation_count), ("elapsed_ms", self.elapsed_ms), ("peak_memory_bytes", self.peak_memory_bytes)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        _bounded_text(self.backend, "backend", 128)

    def to_dict(self) -> dict[str, object]:
        return {
            "input_duration_ticks": str(self.input_duration_ticks),
            "observation_count": self.observation_count,
            "elapsed_ms": self.elapsed_ms,
            "peak_memory_bytes": self.peak_memory_bytes,
            "backend": self.backend,
        }


@dataclass(frozen=True)
class DiarizationResult:
    job_id: str
    model_id: str
    model_version: str
    algorithm: str
    segments: tuple[SpeakerSegment, ...]
    resource_profile: ResourceProfile

    def __post_init__(self) -> None:
        _bounded_text(self.job_id, "job_id")
        _bounded_text(self.model_id, "model_id")
        _bounded_text(self.model_version, "model_version", 128)
        _bounded_text(self.algorithm, "algorithm", 128)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DIARIZATION_CONTRACT_VERSION,
            "job_id": self.job_id,
            "model": {"model_id": self.model_id, "model_version": self.model_version, "algorithm": self.algorithm},
            "segments": [segment.to_dict() for segment in self.segments],
            "resource_profile": self.resource_profile.to_dict(),
        }


@dataclass(frozen=True)
class DiarizationMetrics:
    reference_ticks: int
    miss_ticks: int
    false_alarm_ticks: int
    confusion_ticks: int

    @property
    def error_ticks(self) -> int:
        return self.miss_ticks + self.false_alarm_ticks

    @property
    def der(self) -> str:
        if self.reference_ticks == 0:
            return "0"
        return format((Decimal(self.error_ticks) / Decimal(self.reference_ticks)).normalize(), "f")

    def to_dict(self) -> dict[str, object]:
        return {"reference_ticks": str(self.reference_ticks), "miss_ticks": str(self.miss_ticks), "false_alarm_ticks": str(self.false_alarm_ticks), "confusion_ticks": str(self.confusion_ticks), "der": self.der}

    @staticmethod
    def compute(reference: Sequence[SpeakerSegment], hypothesis: Sequence[SpeakerSegment]) -> "DiarizationMetrics":
        return compute_der(reference, hypothesis)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return -1.0
    value = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, value))


@dataclass
class _Cluster:
    first: SpeakerObservation
    labels: set[str]

    @property
    def cluster_id(self) -> str:
        embedding = "" if self.first.embedding is None else ",".join(format(value, ".6g") for value in self.first.embedding)
        seed = f"{self.first.observation_id}\0{self.first.start_ticks}\0{embedding}".encode()
        return "spk-" + hashlib.sha256(seed).hexdigest()[:16]


class DiarizationBaseline:
    """Merge chunk observations and retain overlap/uncertainty explicitly."""

    def __init__(self, *, model_id: str = "local-diarization", model_version: str = "baseline-1", similarity_threshold: float = 0.999) -> None:
        _bounded_text(model_id, "model_id")
        _bounded_text(model_version, "model_version", 128)
        if not math.isfinite(similarity_threshold) or not 0 < similarity_threshold <= 1:
            raise ValueError("similarity threshold must be in (0, 1]")
        self.model_id = model_id
        self.model_version = model_version
        self.similarity_threshold = similarity_threshold

    def merge_chunks(self, job_id: str, chunks: Iterable[Sequence[SpeakerObservation]], *, resource_profile: ResourceProfile | None = None) -> DiarizationResult:
        _bounded_text(job_id, "job_id")
        observations = [observation for chunk in chunks for observation in chunk]
        if len(observations) > 100_000:
            raise ValueError("too many diarization observations")
        observations.sort(key=lambda item: (item.start_ticks, item.end_ticks, item.chunk_id, item.observation_id))
        clusters: list[_Cluster] = []
        assignments: dict[str, str] = {}
        for observation in observations:
            if observation.role != "speaker":
                continue
            matches: list[tuple[float, int]] = []
            for index, cluster in enumerate(clusters):
                if observation.local_cluster in cluster.labels:
                    matches.append((1.0, index))
                elif observation.embedding is not None and cluster.first.embedding is not None:
                    score = _cosine(observation.embedding, cluster.first.embedding)
                    if score >= self.similarity_threshold:
                        matches.append((score, index))
            if matches:
                _, index = max(matches, key=lambda value: (value[0], -value[1]))
                clusters[index].labels.add(observation.local_cluster or "")
            else:
                clusters.append(_Cluster(observation, {observation.local_cluster or ""}))
            assignments[observation.observation_id] = clusters[index if matches else len(clusters) - 1].cluster_id
        segments: list[SpeakerSegment] = []
        for observation in observations:
            cluster_ids = () if observation.role != "speaker" else (assignments[observation.observation_id],)
            segments.append(SpeakerSegment(observation.observation_id, observation.start_ticks, observation.end_ticks, cluster_ids, observation.role, observation.confidence))
        segments = self._mark_overlaps(segments)
        profile = resource_profile or ResourceProfile(
            input_duration_ticks=max((segment.end_ticks for segment in segments), default=0),
            observation_count=len(observations),
            elapsed_ms=0,
            peak_memory_bytes=0,
        )
        return DiarizationResult(job_id, self.model_id, self.model_version, "deterministic-cluster-merge", tuple(segments), profile)

    @staticmethod
    def _mark_overlaps(segments: Sequence[SpeakerSegment]) -> list[SpeakerSegment]:
        groups: list[list[int]] = []
        for index, segment in enumerate(segments):
            touching = [group for group in groups if any(segments[member].start_ticks < segment.end_ticks and segment.start_ticks < segments[member].end_ticks for member in group)]
            if touching:
                merged = touching[0]
                merged.append(index)
                for other in touching[1:]:
                    merged.extend(other)
                    groups.remove(other)
            else:
                groups.append([index])
        result = list(segments)
        for group in groups:
            if len(group) < 2:
                continue
            seed = "|".join(f"{segments[index].segment_id}:{segments[index].start_ticks}:{segments[index].end_ticks}" for index in sorted(group))
            group_id = "overlap-" + hashlib.sha256(seed.encode()).hexdigest()[:16]
            for index in group:
                segment = result[index]
                result[index] = SpeakerSegment(segment.segment_id, segment.start_ticks, segment.end_ticks, segment.speaker_cluster_ids, segment.role, segment.confidence, group_id)
        return result


def compute_der(reference: Sequence[SpeakerSegment], hypothesis: Sequence[SpeakerSegment]) -> DiarizationMetrics:
    """Compute overlap-aware DER on integer-tick interval boundaries."""

    boundaries = sorted({point for segment in (*reference, *hypothesis) for point in (segment.start_ticks, segment.end_ticks)})
    reference_ticks = miss_ticks = false_alarm_ticks = confusion_ticks = 0
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            continue
        ref = {cluster for segment in reference if segment.start_ticks < end and start < segment.end_ticks for cluster in segment.speaker_cluster_ids}
        hyp = {cluster for segment in hypothesis if segment.start_ticks < end and start < segment.end_ticks for cluster in segment.speaker_cluster_ids}
        duration = end - start
        correct = len(ref & hyp)
        miss = max(len(ref) - correct, 0)
        false_alarm = max(len(hyp) - correct, 0)
        reference_ticks += duration * len(ref)
        miss_ticks += duration * miss
        false_alarm_ticks += duration * false_alarm
        confusion_ticks += duration * min(miss, false_alarm)
    return DiarizationMetrics(reference_ticks, miss_ticks, false_alarm_ticks, confusion_ticks)


__all__ = ["DIARIZATION_CONTRACT_VERSION", "DiarizationBaseline", "DiarizationMetrics", "DiarizationResult", "ResourceProfile", "SpeakerObservation", "SpeakerSegment", "compute_der"]
