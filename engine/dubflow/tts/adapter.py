"""Validated, backend-neutral local Vietnamese TTS adapter.

The adapter owns the boundary between translated timed text and per-segment
PCM artifacts.  It deliberately accepts only canonical integer timeline
points and signed-16 PCM WAV output in v1.  A real local model implements the
small :class:`TtsEngine` protocol; the standard-library fixture engine keeps
PR and Integration deterministic without model downloads, network access, or
GPU availability.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Any, Mapping, Protocol, Sequence
import unicodedata
import wave

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint


TTS_CONTRACT_VERSION = 1
TARGET_LANGUAGE = "vi"
PCM_FORMAT = "pcm_s16le"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_LANGUAGE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class TtsError(ValueError):
    """Stable, serializable failure at the TTS boundary."""

    def __init__(
        self,
        code: str,
        condition: str,
        *,
        retryable: bool = False,
        segment_id: str | None = None,
        attempt: int = 1,
        fallback_used: bool = False,
    ) -> None:
        if not code or not condition:
            raise ValueError("TTS failures require a code and condition")
        if type(retryable) is not bool or type(attempt) is not int or not 1 <= attempt <= 255:
            raise ValueError("invalid TTS failure metadata")
        if type(fallback_used) is not bool:
            raise ValueError("fallback_used must be boolean")
        self.code = code
        self.condition = _safe_condition(condition)
        self.retryable = retryable
        self.segment_id = segment_id
        self.attempt = attempt
        self.fallback_used = fallback_used
        super().__init__(f"{code}: {self.condition}")


class TtsBackendError(TtsError):
    """A backend-reported, potentially retryable failure."""


class TtsStageError(TtsError):
    """Raised when every requested segment fails synthesis."""

    def __init__(self, document: "TtsDocument") -> None:
        self.document = document
        super().__init__(
            "TTS_FAILED",
            "every requested TTS segment failed",
            retryable=any(item.retryable for item in document.failures),
        )


def _safe_condition(value: Any) -> str:
    text = str(value) or "TTS backend returned an empty condition"
    text = _CONTROL.sub(" ", text).strip()
    return text[:4096] or "TTS backend returned an empty condition"


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise TtsError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _blob(value: Any, name: str, *, limit: int) -> bytes:
    if not isinstance(value, bytes) or not value or len(value) > limit:
        raise TtsError("INVALID_AUDIO", f"{name} must be a bounded non-empty byte string")
    return value


def _integer(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise TtsError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise TtsError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise TtsError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _confidence(value: Any, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise TtsError("INVALID_CONFIDENCE", f"{name} must be finite")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise TtsError("INVALID_CONFIDENCE", f"{name} must be between 0 and 1")
    return result


def _language(value: Any, name: str = "language") -> str:
    value = _text(value, name, limit=32)
    if _LANGUAGE.fullmatch(value) is None:
        raise TtsError("INVALID_LANGUAGE", f"unsupported language tag {value!r}")
    return value


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(encoded)


def _ensure_hash(value: Any, name: str) -> str:
    value = _text(value, name, limit=80)
    if _SHA256.fullmatch(value) is None:
        raise TtsError("INVALID_PROVENANCE", f"{name} must be a sha256 digest")
    return value


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _canonical_interval(start: TimePoint, end: TimePoint, name: str) -> TimeInterval:
    if not isinstance(start, TimePoint) or not isinstance(end, TimePoint):
        raise TtsError("INVALID_TIMELINE", f"{name} boundaries must be canonical time points")
    if start.time_base != end.time_base:
        raise TtsError("TIMELINE_TIME_BASE_MISMATCH", f"{name} boundaries use different time bases")
    try:
        return TimeInterval(start, end)
    except Exception as error:
        raise TtsError("INVALID_TIMELINE", f"{name} must be a positive interval: {error}") from error


def _floor_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise TtsError("INVALID_SAMPLE_RATE", "sample mapping denominator must be positive")
    return numerator // denominator


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise TtsError("INVALID_SAMPLE_RATE", "sample mapping denominator must be positive")
    return -((-numerator) // denominator)


def map_timepoint_to_sample(point: TimePoint, sample_rate: int, *, end: bool = False) -> int:
    """Map a canonical point to a containing sample boundary with integer math."""

    if not isinstance(point, TimePoint):
        raise TtsError("INVALID_TIMELINE", "sample mapping requires a canonical time point")
    _integer(sample_rate, "sample_rate", minimum=1, maximum=U64_MAX)
    numerator = point.ticks * point.time_base.numerator * sample_rate
    denominator = point.time_base.denominator
    return _ceil_div(numerator, denominator) if end else _floor_div(numerator, denominator)


def map_interval_to_samples(interval: TimeInterval, sample_rate: int) -> tuple[int, int]:
    start = map_timepoint_to_sample(interval.start, sample_rate)
    end = map_timepoint_to_sample(interval.end, sample_rate, end=True)
    if end <= start:
        # A positive canonical interval must own at least one sample. This is
        # an integer containment rule, not a floating-point duration guess.
        end = start + 1
    return start, end


@dataclass(frozen=True)
class TtsInput:
    """One translated segment accepted by the synthesis adapter."""

    segment_id: str
    source_utterance_id: str
    text: str
    start: TimePoint
    end: TimePoint
    source_language: str = "und"
    target_language: str = TARGET_LANGUAGE
    confidence: float = 1.0
    normalized_text: str | None = None

    def __post_init__(self) -> None:
        _text(self.segment_id, "segment.segment_id", limit=256)
        _text(self.source_utterance_id, "segment.source_utterance_id", limit=256)
        _text(self.text, "segment.text", limit=16384)
        _language(self.source_language, "segment.source_language")
        if self.target_language != TARGET_LANGUAGE:
            raise TtsError("UNSUPPORTED_TARGET", "TTS v1 targets Vietnamese (vi)")
        _confidence(self.confidence, "segment.confidence")
        _canonical_interval(self.start, self.end, "segment")
        normalized = _normalize_text(self.text if self.normalized_text is None else self.normalized_text)
        if not normalized or _CONTROL.search(normalized):
            raise TtsError("INVALID_TEXT", "segment.normalized_text must not be empty")
        if len(normalized) > 16384:
            raise TtsError("INVALID_TEXT", "segment.normalized_text exceeds the limit")
        object.__setattr__(self, "normalized_text", normalized)

    @classmethod
    def from_translation(cls, segment: Any) -> "TtsInput":
        """Adapt a translated segment without importing translation internals."""

        try:
            source_language = getattr(segment, "source_language", "und")
            return cls(
                segment_id=getattr(segment, "source_utterance_id"),
                source_utterance_id=getattr(segment, "source_utterance_id"),
                text=getattr(segment, "translated_text"),
                normalized_text=getattr(segment, "normalized_text", None),
                start=getattr(segment, "start"),
                end=getattr(segment, "end"),
                source_language=source_language,
                target_language=getattr(segment, "target_language", TARGET_LANGUAGE),
                confidence=getattr(segment, "confidence", 1.0),
            )
        except AttributeError as error:
            raise TtsError("INVALID_SOURCE", "translated segment lacks required identity/timing fields") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "source_utterance_id": self.source_utterance_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "source_language": self.source_language,
            "target_language": self.target_language,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    voice_version: str
    language: str = TARGET_LANGUAGE
    display_name: str = "DubFlow Vietnamese Default"
    model_id: str = "dubflow-fixture-vi"
    model_version: str = "1"
    model_hash: str = "sha256:" + "0" * 64
    manifest_hash: str = "sha256:" + "1" * 64
    license_id: str = "dubflow-fixture-permissive"
    approved: bool = True
    credential_required: bool = False
    network_required: bool = False
    speaking_rate_milli: int = 1000
    pitch_style: str = "auto"
    emotion_mode: str = "neutral"
    default: bool = False

    def __post_init__(self) -> None:
        for name in ("voice_id", "voice_version", "display_name", "model_id", "model_version", "license_id", "pitch_style", "emotion_mode"):
            _text(getattr(self, name), f"voice.{name}", limit=256)
        _language(self.language, "voice.language")
        if self.language != TARGET_LANGUAGE:
            raise TtsError("UNSUPPORTED_VOICE", "baseline TTS voice must speak Vietnamese (vi)")
        _ensure_hash(self.model_hash, "voice.model_hash")
        _ensure_hash(self.manifest_hash, "voice.manifest_hash")
        _integer(self.speaking_rate_milli, "voice.speaking_rate_milli", minimum=500, maximum=2000)
        for name, value in (("approved", self.approved), ("credential_required", self.credential_required), ("network_required", self.network_required), ("default", self.default)):
            if type(value) is not bool:
                raise TtsError("INVALID_VOICE", f"voice.{name} must be boolean")
        if self.credential_required or self.network_required:
            raise TtsError("VOICE_REQUIRES_SETUP", "the baseline voice cannot require credentials or network access")
        if not self.approved:
            raise TtsError("VOICE_NOT_APPROVED", "voice is not approved for the baseline path")

    @property
    def speaking_rate(self) -> float:
        return self.speaking_rate_milli / 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "voice_version": self.voice_version,
            "language": self.language,
            "display_name": self.display_name,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "manifest_hash": self.manifest_hash,
            "license_id": self.license_id,
            "approved": self.approved,
            "credential_required": self.credential_required,
            "network_required": self.network_required,
            "speaking_rate_milli": self.speaking_rate_milli,
            "pitch_style": self.pitch_style,
            "emotion_mode": self.emotion_mode,
            "default": self.default,
        }

    def content_hash(self) -> str:
        return _hash_json(self.to_dict())


def approved_default_voice() -> VoiceProfile:
    """Return the no-setup stock voice used by the deterministic path."""

    return VoiceProfile(
        voice_id="vi-default-01",
        voice_version="1",
        default=True,
    )


@dataclass(frozen=True)
class ResourceProfile:
    max_threads: int = 1
    max_memory_mb: int = 512
    max_batch_items: int = 1

    def __post_init__(self) -> None:
        _integer(self.max_threads, "resource.max_threads", minimum=1, maximum=256)
        _integer(self.max_memory_mb, "resource.max_memory_mb", minimum=32, maximum=1_048_576)
        _integer(self.max_batch_items, "resource.max_batch_items", minimum=1, maximum=256)

    def to_dict(self) -> dict[str, int]:
        return {"max_threads": self.max_threads, "max_memory_mb": self.max_memory_mb, "max_batch_items": self.max_batch_items}


@dataclass(frozen=True)
class TtsConfig:
    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    requested_profile: str = "fixture"
    max_attempts: int = 2
    max_segments_per_chunk: int = 8
    max_text_chars: int = 16384
    max_wave_bytes: int = 16 * 1024 * 1024
    min_rms_milli: int = 1
    max_clip_fraction_ppm: int = 1000
    max_duration_error_ticks: int = 80
    min_speed_ratio_milli: int = 800
    max_speed_ratio_milli: int = 1300
    resource: ResourceProfile = ResourceProfile()

    def __post_init__(self) -> None:
        _integer(self.sample_rate, "config.sample_rate", minimum=8000, maximum=96000)
        _integer(self.channels, "config.channels", minimum=1, maximum=2)
        if self.bits_per_sample != 16:
            raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "TTS v1 requires signed-16 PCM")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise TtsError("INVALID_PROFILE", "requested profile is unsupported")
        _integer(self.max_attempts, "config.max_attempts", minimum=1, maximum=3)
        _integer(self.max_segments_per_chunk, "config.max_segments_per_chunk", minimum=1, maximum=256)
        _integer(self.max_text_chars, "config.max_text_chars", minimum=1, maximum=16384)
        _integer(self.max_wave_bytes, "config.max_wave_bytes", minimum=1024, maximum=512 * 1024 * 1024)
        _integer(self.min_rms_milli, "config.min_rms_milli", minimum=0, maximum=1000)
        _integer(self.max_clip_fraction_ppm, "config.max_clip_fraction_ppm", minimum=0, maximum=1_000_000)
        _integer(self.max_duration_error_ticks, "config.max_duration_error_ticks", minimum=0, maximum=I64_MAX)
        _integer(self.min_speed_ratio_milli, "config.min_speed_ratio_milli", minimum=100, maximum=1000)
        _integer(self.max_speed_ratio_milli, "config.max_speed_ratio_milli", minimum=1000, maximum=3000)
        if self.min_speed_ratio_milli > self.max_speed_ratio_milli:
            raise TtsError("INVALID_DURATION_POLICY", "minimum speed ratio exceeds maximum")
        if not isinstance(self.resource, ResourceProfile):
            raise TtsError("INVALID_RESOURCE_PROFILE", "config.resource must be ResourceProfile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bits_per_sample": self.bits_per_sample,
            "requested_profile": self.requested_profile,
            "max_attempts": self.max_attempts,
            "max_segments_per_chunk": self.max_segments_per_chunk,
            "max_text_chars": self.max_text_chars,
            "max_wave_bytes": self.max_wave_bytes,
            "min_rms_milli": self.min_rms_milli,
            "max_clip_fraction_ppm": self.max_clip_fraction_ppm,
            "max_duration_error_ticks": self.max_duration_error_ticks,
            "min_speed_ratio_milli": self.min_speed_ratio_milli,
            "max_speed_ratio_milli": self.max_speed_ratio_milli,
            "resource": self.resource.to_dict(),
        }

    def content_hash(self) -> str:
        return _hash_json(self.to_dict())


@dataclass(frozen=True)
class TtsProvenance:
    producer: str
    producer_version: str
    backend_id: str
    runtime: str
    timeline_contract: str
    config_hash: str
    input_hash: str
    model_id: str
    model_version: str
    model_hash: str
    voice_hash: str
    voice_id: str
    voice_version: str
    requested_profile: str
    hardware_profile: str
    resource: ResourceProfile
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("producer", "producer_version", "backend_id", "runtime", "timeline_contract", "model_id", "model_version", "voice_id", "voice_version"):
            _text(getattr(self, name), f"provenance.{name}", limit=256)
        if self.timeline_contract != "timeline-v1":
            raise TtsError("UNSUPPORTED_TIMELINE_CONTRACT", "TTS v1 requires timeline-v1")
        for name in ("config_hash", "input_hash", "model_hash", "voice_hash"):
            _ensure_hash(getattr(self, name), f"provenance.{name}")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise TtsError("INVALID_PROFILE", "requested profile is unsupported")
        if self.hardware_profile not in {"cpu", "gpu", "fixture", "mixed"}:
            raise TtsError("INVALID_PROFILE", "hardware profile is unsupported")
        if not isinstance(self.resource, ResourceProfile):
            raise TtsError("INVALID_RESOURCE_PROFILE", "provenance.resource must be ResourceProfile")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "provenance.fallback_reason", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "backend_id": self.backend_id,
            "runtime": self.runtime,
            "timeline_contract": self.timeline_contract,
            "config_hash": self.config_hash,
            "input_hash": self.input_hash,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "voice_hash": self.voice_hash,
            "voice_id": self.voice_id,
            "voice_version": self.voice_version,
            "requested_profile": self.requested_profile,
            "hardware_profile": self.hardware_profile,
            "resource": self.resource.to_dict(),
        }
        if self.fallback_reason is not None:
            result["fallback_reason"] = self.fallback_reason
        return result


@dataclass(frozen=True)
class EngineCapabilities:
    engine_id: str
    languages: tuple[str, ...] = (TARGET_LANGUAGE,)
    formats: tuple[str, ...] = ("wav",)
    sample_rates: tuple[int, ...] = (16000,)
    channels: tuple[int, ...] = (1,)
    deterministic: bool = True
    supports_duration_fit: bool = True
    network_required: bool = False
    credential_required: bool = False

    def __post_init__(self) -> None:
        _text(self.engine_id, "capabilities.engine_id", limit=256)
        if type(self.languages) is not tuple or not self.languages or any(type(item) is not str for item in self.languages):
            raise TtsError("INVALID_CAPABILITIES", "engine languages must be a non-empty tuple")
        if type(self.formats) is not tuple or not self.formats or any(type(item) is not str for item in self.formats):
            raise TtsError("INVALID_CAPABILITIES", "engine formats must be a non-empty tuple")
        if type(self.sample_rates) is not tuple or not self.sample_rates or type(self.channels) is not tuple or not self.channels:
            raise TtsError("INVALID_CAPABILITIES", "engine sample rates and channels must be non-empty tuples")
        for language in self.languages:
            _language(language, "capabilities.language")
        if TARGET_LANGUAGE not in self.languages or "wav" not in self.formats:
            raise TtsError("ENGINE_UNSUPPORTED", "engine does not support Vietnamese WAV output")
        for rate in self.sample_rates:
            _integer(rate, "capabilities.sample_rate", minimum=1, maximum=U64_MAX)
        for channel_count in self.channels:
            _integer(channel_count, "capabilities.channels", minimum=1, maximum=2)
        for name, value in (("deterministic", self.deterministic), ("supports_duration_fit", self.supports_duration_fit), ("network_required", self.network_required), ("credential_required", self.credential_required)):
            if type(value) is not bool:
                raise TtsError("INVALID_CAPABILITIES", f"capabilities.{name} must be boolean")


@dataclass(frozen=True)
class EngineHealth:
    ready: bool
    code: str = "READY"
    condition: str = "engine ready"

    def __post_init__(self) -> None:
        if type(self.ready) is not bool:
            raise TtsError("INVALID_HEALTH", "engine health.ready must be boolean")
        _text(self.code, "health.code", limit=128)
        _text(self.condition, "health.condition", limit=4096)


@dataclass(frozen=True)
class TtsRequest:
    request_id: str
    segment: TtsInput
    voice: VoiceProfile
    config: TtsConfig
    input_hash: str
    sample_start: int
    sample_end: int

    def __post_init__(self) -> None:
        _text(self.request_id, "request.request_id", limit=256)
        _ensure_hash(self.input_hash, "request.input_hash")
        if not isinstance(self.segment, TtsInput) or not isinstance(self.voice, VoiceProfile) or not isinstance(self.config, TtsConfig):
            raise TtsError("INVALID_REQUEST", "request contains an invalid typed field")
        _integer(self.sample_start, "request.sample_start", minimum=I64_MIN, maximum=I64_MAX)
        _integer(self.sample_end, "request.sample_end", minimum=I64_MIN, maximum=I64_MAX)
        if self.sample_end <= self.sample_start:
            raise TtsError("INVALID_REQUEST", "request sample interval must be positive")


@dataclass(frozen=True)
class EngineSynthesis:
    audio_bytes: bytes
    format: str = "wav"
    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    fit_mode: str = "native"
    speed_ratio_milli: int = 1000
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _blob(self.audio_bytes, "synthesis.audio_bytes", limit=512 * 1024 * 1024)
        if self.format != "wav":
            raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "TTS v1 requires WAV")
        _integer(self.sample_rate, "synthesis.sample_rate", minimum=1, maximum=U64_MAX)
        _integer(self.channels, "synthesis.channels", minimum=1, maximum=2)
        if self.bits_per_sample != 16:
            raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "TTS v1 requires signed-16 PCM")
        if self.fit_mode not in {"native", "trimmed_silence", "speed_adjusted", "padded"}:
            raise TtsError("INVALID_DURATION_FIT", "unsupported duration fit mode")
        _integer(self.speed_ratio_milli, "synthesis.speed_ratio_milli", minimum=100, maximum=3000)
        if type(self.warnings) is not tuple:
            raise TtsError("INVALID_AUDIO", "synthesis.warnings must be a tuple")
        for warning in self.warnings:
            _text(warning, "synthesis.warning", limit=4096)


class TtsEngine(Protocol):
    def capabilities(self) -> EngineCapabilities:
        ...

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        ...

    def synthesize(self, request: TtsRequest) -> EngineSynthesis:
        ...


@dataclass(frozen=True)
class AudioMetrics:
    sample_count: int
    frame_count: int
    rms_milli: int
    peak: int
    clipped_samples: int
    content_hash: str

    def __post_init__(self) -> None:
        _integer(self.sample_count, "metrics.sample_count", minimum=1, maximum=U64_MAX)
        _integer(self.frame_count, "metrics.frame_count", minimum=1, maximum=U64_MAX)
        _integer(self.rms_milli, "metrics.rms_milli", minimum=0, maximum=1000)
        _integer(self.peak, "metrics.peak", minimum=0, maximum=32768)
        _integer(self.clipped_samples, "metrics.clipped_samples", minimum=0, maximum=U64_MAX)
        _ensure_hash(self.content_hash, "metrics.content_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "frame_count": self.frame_count,
            "rms_milli": self.rms_milli,
            "peak": self.peak,
            "clipped_samples": self.clipped_samples,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class TtsArtifact:
    segment_id: str
    source_utterance_id: str
    path: str
    content_hash: str
    format: str
    sample_rate: int
    channels: int
    bits_per_sample: int
    frame_count: int
    slot_start: TimePoint
    slot_end: TimePoint
    actual_end: TimePoint
    fit_mode: str
    speed_ratio_milli: int
    metrics: AudioMetrics
    artifact_hash: str
    confidence: float
    warnings: tuple[str, ...] = ()
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.segment_id, "artifact.segment_id", limit=256)
        _text(self.source_utterance_id, "artifact.source_utterance_id", limit=256)
        _text(self.path, "artifact.path", limit=4096)
        _ensure_hash(self.content_hash, "artifact.content_hash")
        _ensure_hash(self.artifact_hash, "artifact.artifact_hash")
        if self.format != "wav":
            raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "artifact format must be wav")
        _integer(self.sample_rate, "artifact.sample_rate", minimum=1, maximum=U64_MAX)
        _integer(self.channels, "artifact.channels", minimum=1, maximum=2)
        if self.bits_per_sample != 16:
            raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "artifact must be signed-16 PCM")
        _integer(self.frame_count, "artifact.frame_count", minimum=1, maximum=U64_MAX)
        _canonical_interval(self.slot_start, self.slot_end, "artifact.slot")
        _canonical_interval(self.slot_start, self.actual_end, "artifact.actual")
        if self.fit_mode not in {"native", "trimmed_silence", "speed_adjusted", "padded"}:
            raise TtsError("INVALID_DURATION_FIT", "artifact fit mode is unsupported")
        _integer(self.speed_ratio_milli, "artifact.speed_ratio_milli", minimum=100, maximum=3000)
        _confidence(self.confidence, "artifact.confidence")
        if not isinstance(self.metrics, AudioMetrics):
            raise TtsError("INVALID_AUDIO", "artifact metrics must be AudioMetrics")
        if self.metrics.frame_count != self.frame_count or self.metrics.sample_count != self.frame_count * self.channels:
            raise TtsError("INVALID_AUDIO", "artifact metrics do not match frame metadata")
        if self.metrics.clipped_samples > self.metrics.sample_count or self.metrics.content_hash != self.content_hash:
            raise TtsError("INVALID_AUDIO", "artifact metrics do not match waveform metadata")
        if type(self.warnings) is not tuple:
            raise TtsError("INVALID_AUDIO", "artifact warnings must be a tuple")
        if type(self.fallback_used) is not bool:
            raise TtsError("INVALID_AUDIO", "artifact fallback_used must be boolean")
        for warning in self.warnings:
            _text(warning, "artifact.warning", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "source_utterance_id": self.source_utterance_id,
            "path": self.path,
            "content_hash": self.content_hash,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bits_per_sample": self.bits_per_sample,
            "frame_count": self.frame_count,
            "slot_start": self.slot_start.to_dict(),
            "slot_end": self.slot_end.to_dict(),
            "actual_end": self.actual_end.to_dict(),
            "fit_mode": self.fit_mode,
            "speed_ratio_milli": self.speed_ratio_milli,
            "metrics": self.metrics.to_dict(),
            "artifact_hash": self.artifact_hash,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class TtsFailure:
    code: str
    segment_id: str
    retryable: bool
    attempt: int
    condition: str
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.code, "failure.code", limit=128)
        _text(self.segment_id, "failure.segment_id", limit=256)
        _integer(self.attempt, "failure.attempt", minimum=1, maximum=255)
        if type(self.retryable) is not bool or type(self.fallback_used) is not bool:
            raise TtsError("INVALID_FAILURE", "failure flags must be boolean")
        object.__setattr__(self, "condition", _safe_condition(self.condition))

    @classmethod
    def from_error(cls, error: TtsError, *, segment_id: str, fallback_used: bool | None = None) -> "TtsFailure":
        return cls(
            error.code,
            segment_id,
            error.retryable,
            error.attempt,
            error.condition,
            error.fallback_used if fallback_used is None else fallback_used,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "segment_id": self.segment_id,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "condition": self.condition,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class TtsChunk:
    chunk_id: str
    index: int
    segment_ids: tuple[str, ...]

    def to_dict(self, *, status: str, attempt: int, error_code: str | None = None, artifact_hashes: Sequence[str] = ()) -> dict[str, Any]:
        if status not in {"completed", "failed", "skipped"}:
            raise TtsError("INVALID_CHUNK_STATUS", "unsupported TTS chunk status")
        result: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "segment_ids": list(self.segment_ids),
            "status": status,
            "attempt": attempt,
        }
        if error_code is not None:
            result["error_code"] = error_code
        if artifact_hashes:
            result["artifact_hashes"] = list(artifact_hashes)
        return result


@dataclass(frozen=True)
class TtsCheckpoint:
    segment_id: str
    artifact_hash: str
    artifact: TtsArtifact

    def __post_init__(self) -> None:
        _text(self.segment_id, "checkpoint.segment_id", limit=256)
        _ensure_hash(self.artifact_hash, "checkpoint.artifact_hash")
        if self.artifact.segment_id != self.segment_id:
            raise TtsError("INVALID_CHECKPOINT", "checkpoint segment does not match artifact")
        if self.artifact_hash != self.artifact.artifact_hash:
            raise TtsError("INVALID_CHECKPOINT", "checkpoint hash does not match artifact")


@dataclass(frozen=True)
class TtsDocument:
    target_language: str
    source_segment_ids: tuple[str, ...]
    artifacts: tuple[TtsArtifact, ...]
    chunks: tuple[dict[str, Any], ...]
    provenance: TtsProvenance
    failures: tuple[TtsFailure, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.target_language != TARGET_LANGUAGE:
            raise TtsError("UNSUPPORTED_TARGET", "TTS v1 targets Vietnamese (vi)")
        if not self.source_segment_ids or len(set(self.source_segment_ids)) != len(self.source_segment_ids):
            raise TtsError("INVALID_DOCUMENT", "document source segment IDs must be non-empty and unique")
        for value in self.source_segment_ids:
            _text(value, "document.source_segment_id", limit=256)
        for warning in self.warnings:
            _text(warning, "document.warning", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TTS_CONTRACT_VERSION,
            "kind": "tts_document",
            "source_kind": "translation",
            "target_language": self.target_language,
            "source_segment_ids": list(self.source_segment_ids),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "chunks": list(self.chunks),
            "provenance": self.provenance.to_dict(),
            "failures": [item.to_dict() for item in self.failures],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def to_bytes(self) -> bytes:
        return (self.to_json() + "\n").encode("utf-8")


def _object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise TtsError("INVALID_DOCUMENT", f"{name} must be an object")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        raise TtsError("INVALID_DOCUMENT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise TtsError("INVALID_DOCUMENT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise TtsError("INVALID_DOCUMENT", f"{name} must be a decimal string")
    pattern = r"^(0|-?[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise TtsError("INVALID_DOCUMENT", f"{name} is not canonical decimal")
    result = int(value)
    _integer(result, name, minimum=I64_MIN if signed else 1, maximum=I64_MAX if signed else U64_MAX)
    return result


def _parse_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or type(value["schema_version"]) is not int or value["schema_version"] != TTS_CONTRACT_VERSION:
        raise TtsError("INVALID_DOCUMENT", f"{name} discriminator/version is unsupported")
    base = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    try:
        return TimePoint(
            _decimal(value["ticks"], f"{name}.ticks", signed=True),
            TimeBase(
                _decimal(base["numerator"], f"{name}.time_base.numerator", signed=False),
                _decimal(base["denominator"], f"{name}.time_base.denominator", signed=False),
            ),
        )
    except TtsError:
        raise
    except Exception as error:
        raise TtsError("INVALID_DOCUMENT", f"{name} is not a valid canonical time point: {error}") from error


def validate_tts_document(value: Mapping[str, Any]) -> None:
    value = _object(
        value,
        "document",
        {"schema_version", "kind", "source_kind", "target_language", "source_segment_ids", "artifacts", "chunks", "provenance", "failures", "warnings"},
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != TTS_CONTRACT_VERSION or value["kind"] != "tts_document" or value["source_kind"] != "translation" or value["target_language"] != TARGET_LANGUAGE:
        raise TtsError("INVALID_DOCUMENT", "TTS document discriminator/version is unsupported")
    if type(value["source_segment_ids"]) is not list or not value["source_segment_ids"]:
        raise TtsError("INVALID_DOCUMENT", "source_segment_ids must be a non-empty array")
    source_ids = tuple(_text(item, "source_segment_id", limit=256) for item in value["source_segment_ids"])
    if len(source_ids) != len(set(source_ids)):
        raise TtsError("INVALID_DOCUMENT", "source_segment_ids must be unique")
    source_id_set = set(source_ids)
    if type(value["artifacts"]) is not list or type(value["chunks"]) is not list or not value["chunks"] or type(value["failures"]) is not list or type(value["warnings"]) is not list:
        raise TtsError("INVALID_DOCUMENT", "document arrays have invalid types")

    provenance_value = _object(
        value["provenance"],
        "provenance",
        {"producer", "producer_version", "backend_id", "runtime", "timeline_contract", "config_hash", "input_hash", "model_id", "model_version", "model_hash", "voice_hash", "voice_id", "voice_version", "requested_profile", "hardware_profile", "resource"},
        {"fallback_reason"},
    )
    resource_value = _object(provenance_value["resource"], "provenance.resource", {"max_threads", "max_memory_mb", "max_batch_items"})
    if "fallback_reason" in provenance_value:
        _text(provenance_value["fallback_reason"], "provenance.fallback_reason", limit=4096)
    try:
        TtsProvenance(**{**provenance_value, "resource": ResourceProfile(**resource_value)})
    except TtsError:
        raise
    except Exception as error:
        raise TtsError("INVALID_DOCUMENT", f"provenance is invalid: {error}") from error

    artifacts: dict[str, TtsArtifact] = {}
    artifact_hashes: set[str] = set()
    for position, raw_item in enumerate(value["artifacts"]):
        item = _object(
            raw_item,
            f"artifacts[{position}]",
            {"segment_id", "source_utterance_id", "path", "content_hash", "format", "sample_rate", "channels", "bits_per_sample", "frame_count", "slot_start", "slot_end", "actual_end", "fit_mode", "speed_ratio_milli", "metrics", "artifact_hash", "confidence", "warnings", "fallback_used"},
        )
        segment_id = _text(item["segment_id"], "artifact.segment_id", limit=256)
        if segment_id in artifacts or segment_id not in source_id_set:
            raise TtsError("INVALID_DOCUMENT", "artifact segment IDs must be unique source IDs")
        metrics_value = _object(item["metrics"], f"artifacts[{position}].metrics", {"sample_count", "frame_count", "rms_milli", "peak", "clipped_samples", "content_hash"})
        try:
            metrics = AudioMetrics(
                _integer(metrics_value["sample_count"], "metrics.sample_count", minimum=1, maximum=U64_MAX),
                _integer(metrics_value["frame_count"], "metrics.frame_count", minimum=1, maximum=U64_MAX),
                _integer(metrics_value["rms_milli"], "metrics.rms_milli", minimum=0, maximum=1000),
                _integer(metrics_value["peak"], "metrics.peak", minimum=0, maximum=32768),
                _integer(metrics_value["clipped_samples"], "metrics.clipped_samples", minimum=0, maximum=U64_MAX),
                _ensure_hash(metrics_value["content_hash"], "metrics.content_hash"),
            )
            artifact = TtsArtifact(
                segment_id,
                _text(item["source_utterance_id"], "artifact.source_utterance_id", limit=256),
                _text(item["path"], "artifact.path", limit=4096),
                _ensure_hash(item["content_hash"], "artifact.content_hash"),
                item["format"],
                _integer(item["sample_rate"], "artifact.sample_rate", minimum=1, maximum=U64_MAX),
                _integer(item["channels"], "artifact.channels", minimum=1, maximum=2),
                _integer(item["bits_per_sample"], "artifact.bits_per_sample", minimum=1, maximum=64),
                _integer(item["frame_count"], "artifact.frame_count", minimum=1, maximum=U64_MAX),
                _parse_point(item["slot_start"], f"artifacts[{position}].slot_start"),
                _parse_point(item["slot_end"], f"artifacts[{position}].slot_end"),
                _parse_point(item["actual_end"], f"artifacts[{position}].actual_end"),
                item["fit_mode"],
                _integer(item["speed_ratio_milli"], "artifact.speed_ratio_milli", minimum=100, maximum=3000),
                metrics,
                _ensure_hash(item["artifact_hash"], "artifact.artifact_hash"),
                _confidence(item["confidence"], "artifact.confidence"),
                tuple(item["warnings"]),
                item["fallback_used"],
            )
        except TtsError:
            raise
        except Exception as error:
            raise TtsError("INVALID_DOCUMENT", f"artifact {segment_id} is invalid: {error}") from error
        if type(item["warnings"]) is not list or any(type(warning) is not str or not warning for warning in item["warnings"]):
            raise TtsError("INVALID_DOCUMENT", f"artifact {segment_id} warnings must be a string array")
        if artifact.metrics.frame_count != artifact.frame_count or artifact.metrics.sample_count != artifact.frame_count * artifact.channels or artifact.metrics.clipped_samples > artifact.metrics.sample_count:
            raise TtsError("INVALID_DOCUMENT", f"artifact {segment_id} has inconsistent sample/frame metrics")
        if artifact.metrics.content_hash != artifact.content_hash:
            raise TtsError("INVALID_DOCUMENT", f"artifact {segment_id} content hash disagrees with metrics")
        if artifact.artifact_hash in artifact_hashes:
            raise TtsError("INVALID_DOCUMENT", "artifact hashes must be unique")
        artifact_hashes.add(artifact.artifact_hash)
        artifacts[segment_id] = artifact

    chunks_value = value["chunks"]
    claimed_chunk_ids: set[str] = set()
    chunks_by_id: dict[str, dict[str, Any]] = {}
    for position, raw_item in enumerate(chunks_value):
        item = _object(raw_item, f"chunks[{position}]", {"chunk_id", "index", "segment_ids", "status", "attempt"}, {"error_code", "artifact_hashes"})
        chunk_id = _text(item["chunk_id"], "chunk.chunk_id", limit=128)
        if chunk_id in chunks_by_id or item["index"] != position:
            raise TtsError("INVALID_DOCUMENT", "chunks must have unique contiguous indexes and IDs")
        _integer(item["index"], "chunk.index", minimum=0)
        segment_values = item["segment_ids"]
        if type(segment_values) is not list or not segment_values:
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} segment IDs are invalid")
        segment_names = tuple(_text(identifier, "chunk.segment_id", limit=256) for identifier in segment_values)
        if len(segment_names) != len(set(segment_names)):
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} segment IDs are duplicated")
        segment_ids = set(segment_names)
        if not segment_ids <= source_id_set or claimed_chunk_ids & segment_ids:
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} references duplicate or unknown source IDs")
        claimed_chunk_ids.update(segment_ids)
        status = item["status"]
        if status not in {"completed", "failed", "skipped"}:
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} status is invalid")
        _integer(item["attempt"], "chunk.attempt", minimum=1, maximum=255)
        hashes_value = item.get("artifact_hashes", [])
        if type(hashes_value) is not list:
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} artifact hashes are invalid")
        normalized_hashes = tuple(_ensure_hash(item_hash, "chunk.artifact_hash") for item_hash in hashes_value)
        if len(normalized_hashes) != len(set(normalized_hashes)) or any(item_hash not in artifact_hashes for item_hash in normalized_hashes):
            raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} artifact hashes are invalid")
        if status in {"completed", "skipped"}:
            if "error_code" in item or not segment_ids <= artifacts.keys() or set(normalized_hashes) != {artifacts[identifier].artifact_hash for identifier in segment_ids}:
                raise TtsError("INVALID_DOCUMENT", f"chunk {chunk_id} lacks complete artifact evidence")
        elif "error_code" not in item:
            raise TtsError("INVALID_DOCUMENT", f"failed chunk {chunk_id} lacks an error code")
        if "error_code" in item:
            _text(item["error_code"], "chunk.error_code", limit=128)
        chunks_by_id[chunk_id] = item
    if claimed_chunk_ids != source_id_set:
        raise TtsError("INVALID_DOCUMENT", "every source segment must belong to exactly one chunk")

    for position, raw_item in enumerate(value["failures"]):
        item = _object(raw_item, f"failures[{position}]", {"code", "segment_id", "retryable", "attempt", "condition", "fallback_used"})
        failure = TtsFailure(
            _text(item["code"], "failure.code", limit=128),
            _text(item["segment_id"], "failure.segment_id", limit=256),
            item["retryable"],
            _integer(item["attempt"], "failure.attempt", minimum=1, maximum=255),
            _text(item["condition"], "failure.condition", limit=4096),
            item["fallback_used"],
        )
        if failure.segment_id not in source_id_set:
            raise TtsError("INVALID_DOCUMENT", "failure references an unknown source segment")
    for item in value["warnings"]:
        _text(item, "document.warning", limit=4096)


def parse_tts_json(text: str | bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise TtsError("DUPLICATE_FIELD", f"duplicate JSON field {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise TtsError("INVALID_DOCUMENT", f"non-finite JSON number {value}")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except TtsError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise TtsError("MALFORMED_JSON", str(error)) from error
    validate_tts_document(value)
    return value


def _wav_metrics(audio_bytes: bytes, config: TtsConfig) -> tuple[int, int, AudioMetrics]:
    if len(audio_bytes) > config.max_wave_bytes:
        raise TtsError("AUDIO_TOO_LARGE", "WAV exceeds configured byte limit")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            channels = reader.getnchannels()
            sample_rate = reader.getframerate()
            sample_width = reader.getsampwidth()
            frame_count = reader.getnframes()
            compression = reader.getcomptype()
            frames = reader.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as error:
        raise TtsError("AUDIO_CORRUPT", f"WAV could not be decoded: {error}") from error
    if compression != "NONE" or sample_width != 2:
        raise TtsError("UNSUPPORTED_AUDIO_FORMAT", "only uncompressed signed-16 PCM WAV is accepted")
    if channels < 1 or channels > 2 or sample_rate < 1 or frame_count < 1:
        raise TtsError("AUDIO_CORRUPT", "WAV metadata is empty or outside supported bounds")
    expected_bytes = frame_count * channels * sample_width
    if len(frames) != expected_bytes:
        raise TtsError("AUDIO_CORRUPT", "WAV data chunk is truncated or frame count is inconsistent")
    try:
        samples = [item[0] for item in struct.iter_unpack("<h", frames)] if channels == 1 else [item for frame in struct.iter_unpack("<" + "h" * channels, frames) for item in frame]
    except struct.error as error:
        raise TtsError("AUDIO_CORRUPT", f"WAV PCM alignment is invalid: {error}") from error
    if not samples or len(samples) != frame_count * channels:
        raise TtsError("AUDIO_CORRUPT", "WAV contains no complete PCM samples")
    squared = sum(sample * sample for sample in samples)
    rms = math.sqrt(squared / len(samples))
    if not math.isfinite(rms):
        raise TtsError("AUDIO_NONFINITE", "WAV RMS is not finite")
    rms_milli = min(1000, int(round(rms * 1000 / 32768)))
    peak = max(abs(sample) for sample in samples)
    clipped = sum(1 for sample in samples if abs(sample) >= 32767)
    if rms_milli < config.min_rms_milli:
        raise TtsError("AUDIO_SILENT", "WAV is below the configured RMS threshold")
    if clipped * 1_000_000 > len(samples) * config.max_clip_fraction_ppm:
        raise TtsError("AUDIO_CLIPPING", "WAV clipping exceeds the configured fraction")
    metrics = AudioMetrics(len(samples), frame_count, rms_milli, peak, clipped, _hash_bytes(audio_bytes))
    return sample_rate, channels, metrics


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class DeterministicFixtureEngine:
    """CPU-only fixture engine producing deterministic non-silent PCM WAV."""

    def __init__(self, *, amplitude: int = 9000) -> None:
        self.amplitude = _integer(amplitude, "amplitude", minimum=1, maximum=32766)
        self.calls: list[str] = []

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities("fixture-tts", sample_rates=(16000, 24000, 48000), channels=(1, 2))

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        return EngineHealth(True)

    def synthesize(self, request: TtsRequest) -> EngineSynthesis:
        self.calls.append(request.request_id)
        frame_count = request.sample_end - request.sample_start
        if frame_count < 1:
            raise TtsBackendError("AUDIO_DURATION_MISMATCH", "target interval has no sample", retryable=False)
        if frame_count * request.config.channels * 2 + 128 > request.config.max_wave_bytes:
            raise TtsBackendError("AUDIO_TOO_LARGE", "fixture target exceeds configured waveform limit", retryable=False)
        seed = int(sha256(request.segment.normalized_text.encode("utf-8")).hexdigest()[:8], 16)
        frames = bytearray()
        for index in range(frame_count):
            phase = ((index + seed) % 64) - 32
            value = int(self.amplitude * phase / 32)
            if value == 0:
                value = self.amplitude if (seed & 1) else -self.amplitude
            value = max(-32766, min(32766, value))
            for _ in range(request.config.channels):
                frames.extend(struct.pack("<h", value))
        stream = io.BytesIO()
        with wave.open(stream, "wb") as writer:
            writer.setnchannels(request.config.channels)
            writer.setsampwidth(2)
            writer.setframerate(request.config.sample_rate)
            writer.writeframes(bytes(frames))
        return EngineSynthesis(
            stream.getvalue(),
            sample_rate=request.config.sample_rate,
            channels=request.config.channels,
            bits_per_sample=16,
            fit_mode="native",
        )


class LocalTtsAdapter:
    """Validate a pluggable engine and publish checkpointable WAV artifacts."""

    def __init__(
        self,
        engine: TtsEngine,
        *,
        config: TtsConfig,
        provenance: TtsProvenance,
        voice: VoiceProfile | None = None,
        output_dir: str | Path,
        fallback_engine: TtsEngine | None = None,
        fallback_profile: str = "cpu",
        fallback_provenance: TtsProvenance | None = None,
    ) -> None:
        self.engine = engine
        self.fallback_engine = fallback_engine
        self.config = config
        self.voice = voice or approved_default_voice()
        self.provenance = provenance
        self.output_dir = Path(output_dir)
        self.fallback_profile = fallback_profile
        if fallback_profile not in {"cpu", "gpu", "fixture"}:
            raise TtsError("INVALID_PROFILE", "fallback profile is unsupported")
        if provenance.config_hash != config.content_hash():
            raise TtsError("PROVENANCE_CONFIG_MISMATCH", "TTS provenance config hash differs from adapter config")
        if provenance.requested_profile != config.requested_profile or provenance.resource != config.resource:
            raise TtsError("PROVENANCE_RESOURCE_MISMATCH", "TTS provenance profile/resource differs from adapter config")
        if provenance.input_hash == "":
            raise TtsError("INVALID_PROVENANCE", "TTS provenance input hash is empty")
        if provenance.voice_hash != self.voice.content_hash() or provenance.voice_id != self.voice.voice_id or provenance.voice_version != self.voice.voice_version:
            raise TtsError("PROVENANCE_VOICE_MISMATCH", "TTS provenance voice differs from adapter voice")
        if (provenance.model_id, provenance.model_version, provenance.model_hash) != (self.voice.model_id, self.voice.model_version, self.voice.model_hash):
            raise TtsError("PROVENANCE_MODEL_MISMATCH", "TTS provenance model differs from adapter voice manifest")
        if fallback_provenance is not None and (
            fallback_provenance.config_hash != provenance.config_hash
            or fallback_provenance.input_hash != provenance.input_hash
            or fallback_provenance.voice_hash != provenance.voice_hash
            or fallback_provenance.requested_profile != provenance.requested_profile
            or fallback_provenance.resource != provenance.resource
        ):
            raise TtsError("PROVENANCE_FALLBACK_MISMATCH", "fallback provenance must preserve input/config/voice/profile/resource identity")
        self.fallback_provenance = fallback_provenance

    def synthesize(
        self,
        segments: Sequence[TtsInput] | Sequence[Any],
        *,
        input_hash: str,
        checkpoints: Mapping[str, TtsCheckpoint] | None = None,
    ) -> TtsDocument:
        if input_hash != self.provenance.input_hash:
            raise TtsError("PROVENANCE_INPUT_MISMATCH", "TTS input hash differs from provenance")
        values = tuple(item if isinstance(item, TtsInput) else TtsInput.from_translation(item) for item in segments)
        if not values:
            raise TtsError("EMPTY_SOURCE", "TTS input has no translated segments")
        source_ids = tuple(item.segment_id for item in values)
        if len(set(source_ids)) != len(source_ids):
            raise TtsError("DUPLICATE_SEGMENT_ID", "TTS input segment IDs must be unique")
        base = values[0].start.time_base
        for item in values:
            if item.start.time_base != base:
                raise TtsError("TIMELINE_TIME_BASE_MISMATCH", "TTS input segments must share one time base")
            if len(item.normalized_text or "") > self.config.max_text_chars:
                raise TtsError("TEXT_TOO_LONG", f"TTS segment {item.segment_id} exceeds the configured text limit")
        ordered = tuple(sorted(values, key=lambda item: (item.start.ticks, item.end.ticks, item.segment_id)))
        source_ids = tuple(item.segment_id for item in ordered)
        try:
            capabilities = self.engine.capabilities()
            if not isinstance(capabilities, EngineCapabilities):
                raise TtsError("MALFORMED_ENGINE_CAPABILITIES", "engine did not return EngineCapabilities", retryable=False)
        except Exception as error:
            capabilities = None
            capability_error = error if isinstance(error, TtsError) else TtsBackendError("ENGINE_UNAVAILABLE", _safe_condition(error), retryable=True)
        else:
            capability_error = None
        artifacts: list[TtsArtifact] = []
        failures: list[TtsFailure] = []
        warnings: list[str] = []
        chunks: list[dict[str, Any]] = []
        successful = 0
        fallback_successes = 0
        primary_successes = 0
        selected_provenance = self.provenance
        checkpoint_values = checkpoints or {}
        for index in range(0, len(ordered), self.config.max_segments_per_chunk):
            batch = ordered[index : index + self.config.max_segments_per_chunk]
            chunk_id = "tts-chunk-" + sha256(json.dumps({"input": input_hash, "config": self.config.content_hash(), "voice": self.voice.content_hash(), "index": index, "segments": [item.segment_id for item in batch]}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
            chunk_artifacts: list[TtsArtifact] = []
            chunk_failed = False
            chunk_reused = True
            chunk_attempt = 1
            for segment in batch:
                segment_artifact, segment_failures, used_fallback, reused, attempts, segment_warning = self._synthesize_one(
                    segment,
                    input_hash=input_hash,
                    checkpoint=checkpoint_values.get(segment.segment_id),
                    capability_error=capability_error,
                    capabilities=capabilities,
                )
                failures.extend(segment_failures)
                chunk_attempt = max(chunk_attempt, attempts)
                if segment_artifact is None:
                    chunk_failed = True
                    chunk_reused = False
                    continue
                chunk_artifacts.append(segment_artifact)
                artifacts.append(segment_artifact)
                successful += 1
                if not reused:
                    chunk_reused = False
                if used_fallback:
                    fallback_successes += 1
                    selected_provenance = self._fallback_selected_provenance()
                else:
                    primary_successes += 1
                warnings.extend(segment_warning)
            if chunk_failed:
                chunk_status = "failed"
            elif chunk_reused:
                chunk_status = "skipped"
            else:
                chunk_status = "completed"
            chunks.append(
                TtsChunk(chunk_id, index // self.config.max_segments_per_chunk, tuple(item.segment_id for item in batch)).to_dict(
                    status=chunk_status,
                    attempt=chunk_attempt,
                    error_code=failures[-1].code if chunk_status == "failed" and failures else None,
                    artifact_hashes=[item.artifact_hash for item in chunk_artifacts],
                )
            )
        if failures:
            warnings.append("TTS output is degraded: one or more segments failed or used fallback")
        if primary_successes and fallback_successes:
            selected_provenance = replace(
                self.provenance,
                backend_id="composite",
                model_id="composite",
                model_version="mixed",
                model_hash=_hash_json({"primary": self.provenance.model_hash, "fallback": self._fallback_model_hash()}),
                hardware_profile="mixed",
                fallback_reason="document contains primary and fallback segment results; see per-segment failures",
            )
        document = TtsDocument(TARGET_LANGUAGE, source_ids, tuple(artifacts), tuple(chunks), selected_provenance, tuple(failures), tuple(dict.fromkeys(warnings)))
        validate_tts_document(document.to_dict())
        if successful == 0:
            raise TtsStageError(document)
        return document

    def _synthesize_one(
        self,
        segment: TtsInput,
        *,
        input_hash: str,
        checkpoint: TtsCheckpoint | None,
        capability_error: TtsError | None,
        capabilities: EngineCapabilities | None,
    ) -> tuple[TtsArtifact | None, list[TtsFailure], bool, bool, int, tuple[str, ...]]:
        request_id = self._request_id(segment, input_hash)
        failures: list[TtsFailure] = []
        try:
            sample_start, sample_end = map_interval_to_samples(TimeInterval(segment.start, segment.end), self.config.sample_rate)
            request = TtsRequest(request_id, segment, self.voice, self.config, input_hash, sample_start, sample_end)
        except TtsError as error:
            failure = TtsError(error.code, error.condition, retryable=error.retryable, segment_id=segment.segment_id, attempt=1)
            return None, [TtsFailure.from_error(failure, segment_id=segment.segment_id)], False, False, 1, ()
        except Exception as error:
            failure = TtsError("TTS_REQUEST_FAILED", _safe_condition(error), retryable=False, segment_id=segment.segment_id, attempt=1)
            return None, [TtsFailure.from_error(failure, segment_id=segment.segment_id)], False, False, 1, ()
        if checkpoint is not None and checkpoint.segment_id == segment.segment_id and checkpoint.artifact_hash == self._artifact_hash(segment, checkpoint.artifact.content_hash, request_id=request_id):
            try:
                audio_bytes = Path(checkpoint.artifact.path).read_bytes()
                checked_synthesis, checked_metrics, checked_rate, checked_channels = self._validate_audio(
                    segment,
                    EngineSynthesis(audio_bytes, sample_rate=checkpoint.artifact.sample_rate, channels=checkpoint.artifact.channels),
                    request,
                )
                checked_end = self._actual_end(segment, checked_metrics.frame_count, checked_rate)
                descriptor_matches = (
                    _hash_bytes(audio_bytes) == checkpoint.artifact.content_hash
                    and checkpoint.artifact.metrics == checked_metrics
                    and checkpoint.artifact.frame_count == checked_metrics.frame_count
                    and checkpoint.artifact.sample_rate == checked_rate
                    and checkpoint.artifact.channels == checked_channels
                    and checkpoint.artifact.actual_end == checked_end
                    and checkpoint.artifact.slot_start == segment.start
                    and checkpoint.artifact.slot_end == segment.end
                    and checkpoint.artifact.fit_mode == checked_synthesis.fit_mode
                    and checkpoint.artifact.speed_ratio_milli == checked_synthesis.speed_ratio_milli
                    and checkpoint.artifact.content_hash == checked_metrics.content_hash
                )
                if descriptor_matches:
                    return checkpoint.artifact, failures, checkpoint.artifact.fallback_used, True, 1, ("reused TTS checkpoint for " + segment.segment_id,)
            except (OSError, TtsError):
                pass
        primary_result, primary_errors, attempts = self._invoke(self.engine, request, capability_error, capabilities)
        failures.extend(TtsFailure.from_error(error, segment_id=segment.segment_id) for error in primary_errors)
        validated: tuple[EngineSynthesis, AudioMetrics, int, int] | None = None
        if primary_result is not None:
            try:
                validated = self._validate_audio(segment, primary_result, request)
            except TtsError as error:
                failures.append(TtsFailure.from_error(error, segment_id=segment.segment_id))
        used_fallback = False
        if validated is None and self.fallback_engine is not None:
            used_fallback = True
            fallback_result, fallback_errors, fallback_attempts = self._invoke(self.fallback_engine, request, None, None)
            attempts = max(attempts, fallback_attempts)
            failures.extend(TtsFailure.from_error(error, segment_id=segment.segment_id, fallback_used=True) for error in fallback_errors)
            failures[:] = [replace(item, fallback_used=True) for item in failures]
            if fallback_result is not None:
                try:
                    validated = self._validate_audio(segment, fallback_result, request)
                except TtsError as error:
                    failures.append(TtsFailure.from_error(error, segment_id=segment.segment_id, fallback_used=True))
        if validated is None:
            return None, failures, used_fallback, False, max(1, attempts), ()
        synthesis, metrics, sample_rate, channels = validated
        try:
            actual_end = self._actual_end(segment, metrics.frame_count, sample_rate)
        except Exception as error:
            failure = TtsError("AUDIO_TIMELINE_OVERFLOW", f"audio end time cannot be represented: {error}", segment_id=segment.segment_id)
            failures.append(TtsFailure.from_error(failure, segment_id=segment.segment_id, fallback_used=used_fallback))
            return None, failures, used_fallback, False, max(1, attempts), ()
        artifact_path = self.output_dir / f"tts-{request.request_id[4:]}.wav"
        try:
            _atomic_write(artifact_path, synthesis.audio_bytes)
        except Exception as error:
            failure = TtsError("ARTIFACT_WRITE_FAILED", _safe_condition(error), retryable=True, segment_id=segment.segment_id)
            failures.append(TtsFailure.from_error(failure, segment_id=segment.segment_id, fallback_used=used_fallback))
            return None, failures, used_fallback, False, max(1, attempts), ()
        artifact_hash = self._artifact_hash(segment, metrics.content_hash, request_id=request.request_id)
        warnings = tuple(synthesis.warnings)
        try:
            artifact = TtsArtifact(segment.segment_id, segment.source_utterance_id, str(artifact_path), metrics.content_hash, "wav", sample_rate, channels, 16, metrics.frame_count, segment.start, segment.end, actual_end, synthesis.fit_mode, synthesis.speed_ratio_milli, metrics, artifact_hash, segment.confidence, warnings, used_fallback)
        except TtsError as error:
            failures.append(TtsFailure.from_error(error, segment_id=segment.segment_id, fallback_used=used_fallback))
            return None, failures, used_fallback, False, max(1, attempts), ()
        return artifact, failures, used_fallback, False, max(1, attempts), warnings

    def _invoke(self, engine: TtsEngine, request: TtsRequest, capability_error: TtsError | None, capabilities: EngineCapabilities | None) -> tuple[EngineSynthesis | None, list[TtsError], int]:
        errors: list[TtsError] = []
        previous_condition: str | None = None
        if capabilities is None and capability_error is None:
            try:
                capabilities = engine.capabilities()
                if not isinstance(capabilities, EngineCapabilities):
                    raise TtsError("MALFORMED_ENGINE_CAPABILITIES", "engine did not return EngineCapabilities", retryable=False)
            except Exception as error:
                if isinstance(error, TtsError):
                    capability_error = error
                else:
                    capability_error = TtsBackendError("ENGINE_UNAVAILABLE", _safe_condition(error), retryable=True)
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                if capability_error is not None:
                    raise capability_error
                if capabilities is None or self.config.sample_rate not in capabilities.sample_rates or self.config.channels not in capabilities.channels or "wav" not in capabilities.formats or TARGET_LANGUAGE not in capabilities.languages or capabilities.network_required or capabilities.credential_required:
                    raise TtsBackendError("ENGINE_UNSUPPORTED", "engine capabilities do not satisfy the offline Vietnamese PCM profile", retryable=False, attempt=attempt)
                health = engine.healthcheck(request.voice)
                if not health.ready:
                    raise TtsBackendError(health.code, health.condition, retryable=True, attempt=attempt)
                result = engine.synthesize(request)
                if not isinstance(result, EngineSynthesis):
                    raise TtsError("MALFORMED_ENGINE_RESULT", "engine did not return EngineSynthesis", attempt=attempt)
                return result, errors, attempt
            except TtsError as error:
                failure = TtsError(error.code, error.condition, retryable=error.retryable, segment_id=request.segment.segment_id, attempt=attempt, fallback_used=error.fallback_used)
                errors.append(failure)
                if previous_condition == failure.condition:
                    errors.append(TtsError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", segment_id=request.segment.segment_id, attempt=attempt))
                    return None, errors, attempt
                if not failure.retryable or attempt >= self.config.max_attempts:
                    return None, errors, attempt
                previous_condition = failure.condition
            except Exception as error:
                condition = _safe_condition(error)
                failure = TtsError("TTS_BACKEND_FAILED", condition, retryable=True, segment_id=request.segment.segment_id, attempt=attempt)
                errors.append(failure)
                if previous_condition == condition or attempt >= self.config.max_attempts:
                    if previous_condition == condition:
                        errors.append(TtsError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", segment_id=request.segment.segment_id, attempt=attempt))
                    return None, errors, attempt
                previous_condition = condition
        return None, errors, self.config.max_attempts

    def _validate_audio(self, segment: TtsInput, synthesis: EngineSynthesis, request: TtsRequest) -> tuple[EngineSynthesis, AudioMetrics, int, int]:
        if synthesis.sample_rate != self.config.sample_rate or synthesis.channels != self.config.channels or synthesis.bits_per_sample != self.config.bits_per_sample:
            raise TtsError("AUDIO_METADATA_MISMATCH", "engine WAV metadata differs from requested profile")
        sample_rate, channels, metrics = _wav_metrics(synthesis.audio_bytes, self.config)
        if sample_rate != self.config.sample_rate or channels != self.config.channels:
            raise TtsError("AUDIO_METADATA_MISMATCH", "WAV header metadata differs from requested profile")
        target_ticks = segment.end.ticks - segment.start.ticks
        actual_ticks = _ceil_div(metrics.frame_count * segment.start.time_base.denominator, sample_rate * segment.start.time_base.numerator)
        if abs(actual_ticks - target_ticks) > self.config.max_duration_error_ticks:
            raise TtsError("AUDIO_DURATION_MISMATCH", f"audio duration {actual_ticks} ticks differs from target {target_ticks}")
        if synthesis.fit_mode == "speed_adjusted" and not (self.config.min_speed_ratio_milli <= synthesis.speed_ratio_milli <= self.config.max_speed_ratio_milli):
            raise TtsError("DURATION_FIT_REQUIRED", "engine speed adjustment exceeds safe duration-fit bounds")
        return synthesis, metrics, sample_rate, channels

    def _request_id(self, segment: TtsInput, input_hash: str) -> str:
        return "tts-" + sha256(f"{input_hash}|{self.config.content_hash()}|{self.voice.content_hash()}|{segment.segment_id}|{segment.start.ticks}:{segment.end.ticks}".encode("utf-8")).hexdigest()[:32]

    def _actual_end(self, segment: TtsInput, frame_count: int, sample_rate: int) -> TimePoint:
        actual_ticks = _ceil_div(frame_count * segment.start.time_base.denominator, sample_rate * segment.start.time_base.numerator)
        return TimePoint(segment.start.ticks + actual_ticks, segment.start.time_base)

    def _artifact_hash(self, segment: TtsInput, content_hash: str, *, request_id: str) -> str:
        return _hash_json({
            "segment": segment.to_dict(),
            "input_hash": self.provenance.input_hash,
            "config_hash": self.config.content_hash(),
            "voice_hash": self.voice.content_hash(),
            "content_hash": content_hash,
            "request_id": request_id,
            "engine_fingerprint": self._engine_fingerprint(),
        })

    def _engine_fingerprint(self) -> dict[str, Any]:
        return {
            "primary_class": type(self.engine).__module__ + "." + type(self.engine).__qualname__,
            "fallback_class": type(self.fallback_engine).__module__ + "." + type(self.fallback_engine).__qualname__ if self.fallback_engine is not None else None,
            "fallback_profile": self.fallback_profile if self.fallback_engine is not None else None,
            "backend_id": self.provenance.backend_id,
            "producer": self.provenance.producer,
            "producer_version": self.provenance.producer_version,
            "runtime": self.provenance.runtime,
            "model_id": self.provenance.model_id,
            "model_version": self.provenance.model_version,
            "model_hash": self.provenance.model_hash,
        }

    def _fallback_selected_provenance(self) -> TtsProvenance:
        if self.fallback_provenance is not None:
            return self.fallback_provenance
        return replace(
            self.provenance,
            backend_id="fallback",
            model_id="fallback",
            model_version="1",
            model_hash=self._fallback_model_hash(),
            hardware_profile=self.fallback_profile,
            fallback_reason="primary TTS engine failed; all successful segments used the configured fallback",
        )

    def _fallback_model_hash(self) -> str:
        fallback_identity = {
            "engine": type(self.fallback_engine).__module__ + "." + type(self.fallback_engine).__qualname__ if self.fallback_engine is not None else "none",
            "profile": self.fallback_profile,
        }
        if self.fallback_provenance is not None:
            fallback_identity.update({
                "backend_id": self.fallback_provenance.backend_id,
                "model_id": self.fallback_provenance.model_id,
                "model_version": self.fallback_provenance.model_version,
                "model_hash": self.fallback_provenance.model_hash,
            })
        return _hash_json(fallback_identity)


__all__ = [
    "AudioMetrics",
    "DeterministicFixtureEngine",
    "EngineCapabilities",
    "EngineHealth",
    "EngineSynthesis",
    "LocalTtsAdapter",
    "PCM_FORMAT",
    "ResourceProfile",
    "TARGET_LANGUAGE",
    "TTS_CONTRACT_VERSION",
    "TtsArtifact",
    "TtsBackendError",
    "TtsCheckpoint",
    "TtsConfig",
    "TtsDocument",
    "TtsEngine",
    "TtsError",
    "TtsFailure",
    "TtsInput",
    "TtsProvenance",
    "TtsRequest",
    "TtsStageError",
    "VoiceProfile",
    "approved_default_voice",
    "map_interval_to_samples",
    "map_timepoint_to_sample",
    "parse_tts_json",
    "validate_tts_document",
]
