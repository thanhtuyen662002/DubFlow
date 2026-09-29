"""Deterministic benchmark policy for subtitle removal/inpaint backends."""

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
class VisualCleanupFixture:
    fixture_id: str
    frame_count: int
    width: int
    height: int
    mask_area_ppm: int
    face_overlap_ppm: int
    background_complexity_ppm: int

    def __post_init__(self) -> None:
        _text(self.fixture_id, "fixture_id")
        _nonnegative(self.frame_count, "frame_count", 10_000_000)
        if self.frame_count == 0:
            raise ValueError("frame_count must be positive")
        for name in ("width", "height"):
            _nonnegative(getattr(self, name), name, 100_000)
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be positive")
        for name in ("mask_area_ppm", "face_overlap_ppm", "background_complexity_ppm"):
            _nonnegative(getattr(self, name), name, 1_000_000)


@dataclass(frozen=True)
class InpaintResult:
    backend_id: str
    fixture_id: str
    mask_accuracy_ppm: int
    ghost_glyph_ppm: int
    temporal_flicker_ppm: int
    structural_damage_ppm: int
    latency_ms: int
    peak_memory_bytes: int
    long_form_stable: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        _text(self.backend_id, "backend_id")
        _text(self.fixture_id, "fixture_id")
        for name in ("mask_accuracy_ppm", "ghost_glyph_ppm", "temporal_flicker_ppm", "structural_damage_ppm"):
            _nonnegative(getattr(self, name), name, 1_000_000)
        _nonnegative(self.latency_ms, "latency_ms", 86_400_000)
        _nonnegative(self.peak_memory_bytes, "peak_memory_bytes")
        if type(self.long_form_stable) is not bool:
            raise ValueError("long_form_stable must be boolean")
        if self.error_code is not None:
            _text(self.error_code, "error_code", 128)


@dataclass(frozen=True)
class VisualCleanupPlan:
    fixture_id: str
    backend_id: str
    mode: str
    decision: str
    auto_remove: bool
    mask_accuracy_ppm: int
    temporal_flicker_ppm: int
    structural_damage_ppm: int
    fallback_used: bool
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.fixture_id, "fixture_id")
        _text(self.backend_id, "backend_id")
        if self.mode not in {"inpaint", "cover", "keep"} or self.decision not in {"PROMOTE", "EXPERIMENTAL", "FALLBACK", "REVIEW"}:
            raise ValueError("visual cleanup mode/decision is invalid")
        for name in ("mask_accuracy_ppm", "temporal_flicker_ppm", "structural_damage_ppm"):
            _nonnegative(getattr(self, name), name, 1_000_000)
        if type(self.auto_remove) is not bool or type(self.fallback_used) is not bool:
            raise ValueError("visual cleanup flags must be boolean")
        for warning in self.warnings:
            _text(warning, "warning", 4096)

    def to_dict(self) -> dict[str, object]:
        return {"fixture_id": self.fixture_id, "backend_id": self.backend_id, "mode": self.mode, "decision": self.decision, "auto_remove": self.auto_remove, "mask_accuracy_ppm": self.mask_accuracy_ppm, "temporal_flicker_ppm": self.temporal_flicker_ppm, "structural_damage_ppm": self.structural_damage_ppm, "fallback_used": self.fallback_used, "warnings": list(self.warnings)}


class VisualCleanupBenchmark:
    def __init__(self, *, minimum_mask_accuracy_ppm: int = 900_000, maximum_flicker_ppm: int = 40_000, maximum_ghost_ppm: int = 30_000, maximum_damage_ppm: int = 20_000, maximum_face_overlap_ppm: int = 100_000, maximum_latency_ms: int = 180_000) -> None:
        for name, value in (("minimum_mask_accuracy_ppm", minimum_mask_accuracy_ppm), ("maximum_flicker_ppm", maximum_flicker_ppm), ("maximum_ghost_ppm", maximum_ghost_ppm), ("maximum_damage_ppm", maximum_damage_ppm), ("maximum_face_overlap_ppm", maximum_face_overlap_ppm), ("maximum_latency_ms", maximum_latency_ms)):
            _nonnegative(value, name, 1_000_000)
        self.minimum_mask_accuracy_ppm = minimum_mask_accuracy_ppm
        self.maximum_flicker_ppm = maximum_flicker_ppm
        self.maximum_ghost_ppm = maximum_ghost_ppm
        self.maximum_damage_ppm = maximum_damage_ppm
        self.maximum_face_overlap_ppm = maximum_face_overlap_ppm
        self.maximum_latency_ms = maximum_latency_ms

    def evaluate(self, fixture: VisualCleanupFixture, candidates: Iterable[InpaintResult], *, fallback_backend_id: str = "vis-0-cover") -> VisualCleanupPlan:
        candidates = tuple(candidate for candidate in candidates if candidate.fixture_id == fixture.fixture_id)
        risk_warning: list[str] = []
        if fixture.face_overlap_ppm > self.maximum_face_overlap_ppm:
            risk_warning.append("mask overlaps a face region")
        usable = [candidate for candidate in candidates if candidate.error_code is None and candidate.long_form_stable and candidate.mask_accuracy_ppm >= self.minimum_mask_accuracy_ppm and candidate.ghost_glyph_ppm <= self.maximum_ghost_ppm and candidate.temporal_flicker_ppm <= self.maximum_flicker_ppm and candidate.structural_damage_ppm <= self.maximum_damage_ppm and candidate.latency_ms <= self.maximum_latency_ms and fixture.face_overlap_ppm <= self.maximum_face_overlap_ppm]
        if not usable:
            risk_warning.append("no candidate met conservative visual-risk thresholds")
            return VisualCleanupPlan(fixture.fixture_id, fallback_backend_id, "cover", "FALLBACK", False, 0, 0, 0, True, tuple(risk_warning))
        selected = min(usable, key=lambda item: (item.structural_damage_ppm, item.temporal_flicker_ppm, item.ghost_glyph_ppm, item.latency_ms, item.backend_id))
        decision = "PROMOTE" if selected.mask_accuracy_ppm >= self.minimum_mask_accuracy_ppm + 50_000 and selected.structural_damage_ppm <= self.maximum_damage_ppm // 2 else "EXPERIMENTAL"
        return VisualCleanupPlan(fixture.fixture_id, selected.backend_id, "inpaint", decision, True, selected.mask_accuracy_ppm, selected.temporal_flicker_ppm, selected.structural_damage_ppm, False, tuple(risk_warning))

    def report(self, fixtures: Sequence[VisualCleanupFixture], results: Sequence[InpaintResult], plans: Sequence[VisualCleanupPlan], *, dataset_revision: str = "visual-cleanup-v1") -> dict[str, object]:
        _text(dataset_revision, "dataset_revision", 128)
        payload = {"schema_version": 1, "dataset_revision": dataset_revision, "fixture_count": len(fixtures), "backend_count": len(results), "plans": [plan.to_dict() for plan in plans], "resource_profile": {"max_latency_ms": max((result.latency_ms for result in results), default=0), "max_peak_memory_bytes": max((result.peak_memory_bytes for result in results), default=0), "long_form_stable_count": sum(result.long_form_stable for result in results)}}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["content_hash"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
        return payload


__all__ = ["InpaintResult", "VisualCleanupBenchmark", "VisualCleanupFixture", "VisualCleanupPlan"]
