"""Backend-neutral speech attenuation/separation benchmark.

All quality values are integer milli-dB or milli-score units. This keeps policy
decisions deterministic and avoids making floating-point metrics durable.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable, Sequence


_TEXT = re.compile(r"[\x00-\x1f\x7f]")
U64_MAX = (1 << 64) - 1


def _text(value: object, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or _TEXT.search(value):
        raise ValueError(f"{name} must be bounded text")
    return value


def _nonnegative(value: object, name: str, maximum: int = U64_MAX) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f"{name} must be a bounded non-negative integer")
    return value


@dataclass(frozen=True)
class AudioFixture:
    fixture_id: str
    duration_ticks: int
    speech_rms_millidb: int
    music_rms_millidb: int
    sfx_rms_millidb: int
    overlap: bool = True

    def __post_init__(self) -> None:
        _text(self.fixture_id, "fixture_id")
        _nonnegative(self.duration_ticks, "duration_ticks")
        for name in ("speech_rms_millidb", "music_rms_millidb", "sfx_rms_millidb"):
            _nonnegative(getattr(self, name), name, 1_000_000)
        if type(self.overlap) is not bool:
            raise ValueError("overlap must be boolean")


@dataclass(frozen=True)
class BackendResult:
    backend_id: str
    fixture_id: str
    dialogue_attenuation_millidb: int
    residual_speech_millidb: int
    background_artifact_milli: int
    latency_ms: int
    peak_memory_bytes: int
    long_form_stable: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        _text(self.backend_id, "backend_id")
        _text(self.fixture_id, "fixture_id")
        for name in ("dialogue_attenuation_millidb", "residual_speech_millidb", "background_artifact_milli"):
            _nonnegative(getattr(self, name), name, 1_000_000)
        _nonnegative(self.latency_ms, "latency_ms", 86_400_000)
        _nonnegative(self.peak_memory_bytes, "peak_memory_bytes")
        if type(self.long_form_stable) is not bool:
            raise ValueError("long_form_stable must be boolean")
        if self.error_code is not None:
            _text(self.error_code, "error_code", 128)


@dataclass(frozen=True)
class AudioCleanupPlan:
    fixture_id: str
    backend_id: str
    decision: str
    dialogue_attenuation_millidb: int
    residual_speech_millidb: int
    background_artifact_milli: int
    preserve_music_sfx: bool
    fallback_used: bool
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.fixture_id, "fixture_id")
        _text(self.backend_id, "backend_id")
        if self.decision not in {"PROMOTE", "EXPERIMENTAL", "REJECT", "FALLBACK"}:
            raise ValueError("audio cleanup decision is invalid")
        for name in ("dialogue_attenuation_millidb", "residual_speech_millidb", "background_artifact_milli"):
            _nonnegative(getattr(self, name), name, 1_000_000)
        if type(self.preserve_music_sfx) is not bool or type(self.fallback_used) is not bool:
            raise ValueError("audio cleanup flags must be boolean")
        if len(self.warnings) > 64:
            raise ValueError("audio cleanup warnings are too many")
        for warning in self.warnings:
            _text(warning, "warning", 4096)

    def to_dict(self) -> dict[str, object]:
        return {"fixture_id": self.fixture_id, "backend_id": self.backend_id, "decision": self.decision, "dialogue_attenuation_millidb": self.dialogue_attenuation_millidb, "residual_speech_millidb": self.residual_speech_millidb, "background_artifact_milli": self.background_artifact_milli, "preserve_music_sfx": self.preserve_music_sfx, "fallback_used": self.fallback_used, "warnings": list(self.warnings)}


class AudioSeparationBenchmark:
    """Evaluate candidates and choose a safe backend per fixture."""

    def __init__(self, *, minimum_attenuation_millidb: int = 6000, maximum_residual_millidb: int = 4000, maximum_artifact_milli: int = 250, maximum_latency_ms: int = 120_000) -> None:
        for name, value in (("minimum_attenuation_millidb", minimum_attenuation_millidb), ("maximum_residual_millidb", maximum_residual_millidb), ("maximum_artifact_milli", maximum_artifact_milli), ("maximum_latency_ms", maximum_latency_ms)):
            _nonnegative(value, name)
        self.minimum_attenuation_millidb = minimum_attenuation_millidb
        self.maximum_residual_millidb = maximum_residual_millidb
        self.maximum_artifact_milli = maximum_artifact_milli
        self.maximum_latency_ms = maximum_latency_ms

    def evaluate(self, fixture: AudioFixture, candidates: Iterable[BackendResult], *, fallback_backend_id: str = "aud-0-conservative") -> AudioCleanupPlan:
        candidates = tuple(candidate for candidate in candidates if candidate.fixture_id == fixture.fixture_id)
        usable = [candidate for candidate in candidates if candidate.error_code is None and candidate.long_form_stable and candidate.dialogue_attenuation_millidb >= self.minimum_attenuation_millidb and candidate.residual_speech_millidb <= self.maximum_residual_millidb and candidate.background_artifact_milli <= self.maximum_artifact_milli and candidate.latency_ms <= self.maximum_latency_ms]
        if not usable:
            reasons = ["no candidate met conservative quality/resource thresholds"]
            if any(candidate.error_code for candidate in candidates):
                reasons.append("candidate backend failure is isolated")
            return AudioCleanupPlan(fixture.fixture_id, fallback_backend_id, "FALLBACK", 0, fixture.speech_rms_millidb, 0, True, True, tuple(reasons))
        selected = min(usable, key=lambda candidate: (candidate.background_artifact_milli, candidate.residual_speech_millidb, candidate.latency_ms, candidate.backend_id))
        decision = "PROMOTE" if selected.dialogue_attenuation_millidb >= self.minimum_attenuation_millidb + 2000 and selected.background_artifact_milli <= self.maximum_artifact_milli // 2 else "EXPERIMENTAL"
        return AudioCleanupPlan(fixture.fixture_id, selected.backend_id, decision, selected.dialogue_attenuation_millidb, selected.residual_speech_millidb, selected.background_artifact_milli, True, False, ())

    def report(self, fixtures: Sequence[AudioFixture], results: Sequence[BackendResult], plans: Sequence[AudioCleanupPlan], *, dataset_revision: str = "audio-cleanup-v1") -> dict[str, object]:
        _text(dataset_revision, "dataset_revision", 128)
        payload = {"schema_version": 1, "dataset_revision": dataset_revision, "fixture_count": len(fixtures), "backend_count": len(results), "plans": [plan.to_dict() for plan in plans], "resource_profile": {"max_latency_ms": max((result.latency_ms for result in results), default=0), "max_peak_memory_bytes": max((result.peak_memory_bytes for result in results), default=0), "long_form_stable_count": sum(result.long_form_stable for result in results)}}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["content_hash"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
        return payload


__all__ = ["AudioCleanupPlan", "AudioFixture", "AudioSeparationBenchmark", "BackendResult"]
