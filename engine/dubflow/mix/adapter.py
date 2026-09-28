"""Deterministic, non-destructive source ducking and Vietnamese dub mixing.

AUD-0 deliberately stays at a small PCM16 WAV boundary.  It does not remove
dialogue from the source, depend on a separation model, or require a live
site/GPU.  Source audio remains an independently published artifact; the
dialogue stem contains only accepted TTS segments; and the final mix combines
the ducked source with that stem after integer timeline/sample mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Any, Mapping, Sequence
import wave

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint
from engine.dubflow.tts import map_timepoint_to_sample


MIX_CONTRACT_VERSION = 1
TARGET_LANGUAGE = "vi"
PCM_FORMAT = "pcm_s16le"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class MixError(ValueError):
    """Stable error at the source-mix boundary."""

    def __init__(self, code: str, condition: str, *, segment_id: str | None = None, recoverable: bool = False) -> None:
        if not code or not condition:
            raise ValueError("mix errors require a code and condition")
        self.code = code
        self.condition = _safe_condition(condition)
        self.segment_id = segment_id
        self.recoverable = recoverable
        super().__init__(f"{code}: {self.condition}")


class MixStageError(MixError):
    """Raised when the source cannot produce a usable mix document."""

    def __init__(self, code: str, condition: str, *, document: "MixDocument | None" = None) -> None:
        self.document = document
        super().__init__(code, condition)


def _safe_condition(value: Any) -> str:
    text = str(value) or "audio mix failure"
    text = _CONTROL.sub(" ", text).strip()
    return text[:4096] or "audio mix failure"


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise MixError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _integer(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise MixError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise MixError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise MixError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise MixError("INVALID_BOOLEAN", f"{name} must be boolean")
    return value


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(encoded)


def _ensure_hash(value: Any, name: str) -> str:
    value = _text(value, name, limit=80)
    if _SHA256.fullmatch(value) is None:
        raise MixError("INVALID_PROVENANCE", f"{name} must be a sha256 digest")
    return value


def _canonical_interval(start: TimePoint, end: TimePoint, name: str) -> TimeInterval:
    if not isinstance(start, TimePoint) or not isinstance(end, TimePoint):
        raise MixError("INVALID_TIMELINE", f"{name} boundaries must be canonical time points")
    try:
        return TimeInterval(start, end)
    except Exception as error:
        raise MixError("INVALID_TIMELINE", f"{name} must be a positive same-base interval: {error}") from error


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise MixError("INVALID_SAMPLE_RATE", "sample mapping denominator must be positive")
    return -((-numerator) // denominator)


def _duration_ticks_to_samples(ticks: int, time_base: TimeBase, sample_rate: int) -> int:
    if ticks <= 0:
        return 0
    return _ceil_div(ticks * time_base.numerator * sample_rate, time_base.denominator)


@dataclass(frozen=True)
class ResourceProfile:
    max_threads: int = 1
    max_memory_mb: int = 256
    max_batch_items: int = 8

    def __post_init__(self) -> None:
        _integer(self.max_threads, "resource.max_threads", minimum=1, maximum=256)
        _integer(self.max_memory_mb, "resource.max_memory_mb", minimum=32, maximum=1_048_576)
        _integer(self.max_batch_items, "resource.max_batch_items", minimum=1, maximum=256)

    def to_dict(self) -> dict[str, int]:
        return {
            "max_threads": self.max_threads,
            "max_memory_mb": self.max_memory_mb,
            "max_batch_items": self.max_batch_items,
        }


@dataclass(frozen=True)
class MixConfig:
    duck_gain_milli: int = 350
    dialogue_gain_milli: int = 900
    attack_ticks: int = 40
    release_ticks: int = 120
    target_rms_milli: int = 220
    max_rms_milli: int = 850
    max_peak: int = 30000
    max_clip_fraction_ppm: int = 0
    max_duration_error_ticks: int = 80
    max_segments_per_chunk: int = 8
    max_source_bytes: int = 128 * 1024 * 1024
    max_tts_bytes: int = 32 * 1024 * 1024
    requested_profile: str = "cpu"
    resource: ResourceProfile = ResourceProfile()

    def __post_init__(self) -> None:
        _integer(self.duck_gain_milli, "config.duck_gain_milli", minimum=0, maximum=1000)
        _integer(self.dialogue_gain_milli, "config.dialogue_gain_milli", minimum=0, maximum=2000)
        _integer(self.attack_ticks, "config.attack_ticks", minimum=0, maximum=I64_MAX)
        _integer(self.release_ticks, "config.release_ticks", minimum=0, maximum=I64_MAX)
        _integer(self.target_rms_milli, "config.target_rms_milli", minimum=0, maximum=1000)
        _integer(self.max_rms_milli, "config.max_rms_milli", minimum=1, maximum=1000)
        if self.target_rms_milli > self.max_rms_milli:
            raise MixError("INVALID_LOUDNESS_POLICY", "target RMS exceeds maximum RMS")
        _integer(self.max_peak, "config.max_peak", minimum=1, maximum=32767)
        _integer(self.max_clip_fraction_ppm, "config.max_clip_fraction_ppm", minimum=0, maximum=1_000_000)
        _integer(self.max_duration_error_ticks, "config.max_duration_error_ticks", minimum=0, maximum=I64_MAX)
        _integer(self.max_segments_per_chunk, "config.max_segments_per_chunk", minimum=1, maximum=256)
        _integer(self.max_source_bytes, "config.max_source_bytes", minimum=1024, maximum=1 << 31)
        _integer(self.max_tts_bytes, "config.max_tts_bytes", minimum=1024, maximum=1 << 31)
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise MixError("INVALID_PROFILE", "config.requested_profile is unsupported")
        if not isinstance(self.resource, ResourceProfile):
            raise MixError("INVALID_RESOURCE_PROFILE", "config.resource must be ResourceProfile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "duck_gain_milli": self.duck_gain_milli,
            "dialogue_gain_milli": self.dialogue_gain_milli,
            "attack_ticks": self.attack_ticks,
            "release_ticks": self.release_ticks,
            "target_rms_milli": self.target_rms_milli,
            "max_rms_milli": self.max_rms_milli,
            "max_peak": self.max_peak,
            "max_clip_fraction_ppm": self.max_clip_fraction_ppm,
            "max_duration_error_ticks": self.max_duration_error_ticks,
            "max_segments_per_chunk": self.max_segments_per_chunk,
            "max_source_bytes": self.max_source_bytes,
            "max_tts_bytes": self.max_tts_bytes,
            "requested_profile": self.requested_profile,
            "resource": self.resource.to_dict(),
        }

    def content_hash(self) -> str:
        return _hash_json(self.to_dict())


@dataclass(frozen=True)
class SourceAudio:
    """Canonical source WAV input. `audio_bytes` is never mutated."""

    source_id: str
    audio_bytes: bytes
    start: TimePoint
    end: TimePoint
    layout: str = "source"

    def __post_init__(self) -> None:
        _text(self.source_id, "source.source_id", limit=256)
        if not isinstance(self.audio_bytes, bytes) or not self.audio_bytes:
            raise MixError("INVALID_SOURCE", "source.audio_bytes must be non-empty bytes")
        _canonical_interval(self.start, self.end, "source")
        _text(self.layout, "source.layout", limit=128)

    @classmethod
    def from_path(cls, path: str | Path, *, start: TimePoint, end: TimePoint, source_id: str = "source-audio", layout: str = "source") -> "SourceAudio":
        try:
            payload = Path(path).read_bytes()
        except OSError as error:
            raise MixError("SOURCE_READ_FAILED", str(error)) from error
        return cls(source_id, payload, start, end, layout)


@dataclass(frozen=True)
class MixSegment:
    """One TTS segment, including explicit missing/failed states."""

    segment_id: str
    source_utterance_id: str
    start: TimePoint
    end: TimePoint
    audio_bytes: bytes | None = None
    status: str = "available"
    condition: str | None = None
    confidence: float = 1.0
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.segment_id, "segment.segment_id", limit=256)
        _text(self.source_utterance_id, "segment.source_utterance_id", limit=256)
        _canonical_interval(self.start, self.end, "segment")
        if self.status not in {"available", "missing", "failed"}:
            raise MixError("INVALID_SEGMENT", "segment.status is unsupported")
        if self.status == "available" and (not isinstance(self.audio_bytes, bytes) or not self.audio_bytes):
            raise MixError("INVALID_SEGMENT", "available TTS segment requires audio bytes")
        if self.status != "available" and self.audio_bytes is not None and not isinstance(self.audio_bytes, bytes):
            raise MixError("INVALID_SEGMENT", "non-available segment audio must be bytes or None")
        if self.condition is not None:
            _text(self.condition, "segment.condition", limit=4096)
        if type(self.confidence) not in (int, float) or isinstance(self.confidence, bool) or not math.isfinite(float(self.confidence)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise MixError("INVALID_CONFIDENCE", "segment.confidence must be finite and between 0 and 1")
        _boolean(self.fallback_used, "segment.fallback_used")

    @classmethod
    def from_tts_artifact(cls, artifact: Any) -> "MixSegment":
        try:
            segment_id = getattr(artifact, "segment_id")
            source_utterance_id = getattr(artifact, "source_utterance_id")
            start = getattr(artifact, "slot_start")
            end = getattr(artifact, "slot_end")
            path = getattr(artifact, "path")
            confidence = getattr(artifact, "confidence", 1.0)
            fallback_used = getattr(artifact, "fallback_used", False)
        except AttributeError as error:
            raise MixError("INVALID_TTS_ARTIFACT", "TTS artifact lacks identity/timing/path fields") from error
        try:
            payload = Path(path).read_bytes()
        except OSError as error:
            return cls(segment_id, source_utterance_id, start, end, None, "missing", str(error), confidence, fallback_used)
        return cls(segment_id, source_utterance_id, start, end, payload, "available", None, confidence, fallback_used)


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
        if self.clipped_samples > self.sample_count:
            raise MixError("INVALID_AUDIO", "metrics.clipped_samples exceeds sample_count")
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
class MixArtifact:
    artifact_id: str
    kind: str
    path: str
    content_hash: str
    format: str
    sample_rate: int
    channels: int
    bits_per_sample: int
    frame_count: int
    metrics: AudioMetrics

    def __post_init__(self) -> None:
        _text(self.artifact_id, "artifact.artifact_id", limit=256)
        if self.kind not in {"original_audio", "dialogue_stem", "final_mix"}:
            raise MixError("INVALID_ARTIFACT", "artifact kind is unsupported")
        _text(self.path, "artifact.path", limit=4096)
        _ensure_hash(self.content_hash, "artifact.content_hash")
        if self.format != "wav":
            raise MixError("UNSUPPORTED_AUDIO_FORMAT", "mix artifacts must be WAV")
        _integer(self.sample_rate, "artifact.sample_rate", minimum=1, maximum=U64_MAX)
        _integer(self.channels, "artifact.channels", minimum=1, maximum=2)
        if self.bits_per_sample != 16:
            raise MixError("UNSUPPORTED_AUDIO_FORMAT", "mix artifacts must be signed-16 PCM")
        _integer(self.frame_count, "artifact.frame_count", minimum=1, maximum=U64_MAX)
        if not isinstance(self.metrics, AudioMetrics) or self.metrics.frame_count != self.frame_count or self.metrics.sample_count != self.frame_count * self.channels or self.metrics.content_hash != self.content_hash:
            raise MixError("INVALID_ARTIFACT", "artifact metrics do not match WAV metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "path": self.path,
            "content_hash": self.content_hash,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bits_per_sample": self.bits_per_sample,
            "frame_count": self.frame_count,
            "metrics": self.metrics.to_dict(),
        }


@dataclass(frozen=True)
class MixFailure:
    code: str
    segment_id: str
    condition: str
    recoverable: bool = True
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.code, "failure.code", limit=128)
        _text(self.segment_id, "failure.segment_id", limit=256)
        _text(self.condition, "failure.condition", limit=4096)
        _boolean(self.recoverable, "failure.recoverable")
        _boolean(self.fallback_used, "failure.fallback_used")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "segment_id": self.segment_id,
            "condition": self.condition,
            "recoverable": self.recoverable,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class DuckWindow:
    segment_id: str
    start_sample: int
    end_sample: int
    attack_samples: int
    release_samples: int
    duck_gain_milli: int

    def __post_init__(self) -> None:
        _text(self.segment_id, "duck.segment_id", limit=256)
        _integer(self.start_sample, "duck.start_sample", minimum=I64_MIN, maximum=I64_MAX)
        _integer(self.end_sample, "duck.end_sample", minimum=I64_MIN, maximum=I64_MAX)
        if self.end_sample <= self.start_sample:
            raise MixError("INVALID_DUCK_WINDOW", "duck window must be positive")
        _integer(self.attack_samples, "duck.attack_samples", minimum=0, maximum=U64_MAX)
        _integer(self.release_samples, "duck.release_samples", minimum=0, maximum=U64_MAX)
        _integer(self.duck_gain_milli, "duck.duck_gain_milli", minimum=0, maximum=1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "attack_samples": self.attack_samples,
            "release_samples": self.release_samples,
            "duck_gain_milli": self.duck_gain_milli,
        }


@dataclass(frozen=True)
class MixProvenance:
    producer: str
    producer_version: str
    backend_id: str
    runtime: str
    timeline_contract: str
    config_hash: str
    source_hash: str
    input_hash: str
    requested_profile: str
    hardware_profile: str
    resource: ResourceProfile
    non_destructive: bool = True

    def __post_init__(self) -> None:
        for name in ("producer", "producer_version", "backend_id", "runtime", "timeline_contract"):
            _text(getattr(self, name), f"provenance.{name}", limit=256)
        if self.timeline_contract != "timeline-v1":
            raise MixError("UNSUPPORTED_TIMELINE_CONTRACT", "AUD-0 requires timeline-v1")
        for name in ("config_hash", "source_hash", "input_hash"):
            _ensure_hash(getattr(self, name), f"provenance.{name}")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"} or self.hardware_profile not in {"cpu", "gpu", "fixture", "mixed"}:
            raise MixError("INVALID_PROFILE", "unsupported mix provenance profile")
        if not isinstance(self.resource, ResourceProfile):
            raise MixError("INVALID_RESOURCE_PROFILE", "provenance.resource must be ResourceProfile")
        _boolean(self.non_destructive, "provenance.non_destructive")
        if not self.non_destructive:
            raise MixError("DESTRUCTIVE_MIX_FORBIDDEN", "AUD-0 must preserve the original source")

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "backend_id": self.backend_id,
            "runtime": self.runtime,
            "timeline_contract": self.timeline_contract,
            "config_hash": self.config_hash,
            "source_hash": self.source_hash,
            "input_hash": self.input_hash,
            "requested_profile": self.requested_profile,
            "hardware_profile": self.hardware_profile,
            "resource": self.resource.to_dict(),
            "non_destructive": self.non_destructive,
        }


@dataclass(frozen=True)
class MixCheckpoint:
    source_hash: str
    config_hash: str
    segment_hash: str
    document: "MixDocument"

    def __post_init__(self) -> None:
        _ensure_hash(self.source_hash, "checkpoint.source_hash")
        _ensure_hash(self.config_hash, "checkpoint.config_hash")
        _ensure_hash(self.segment_hash, "checkpoint.segment_hash")
        if not isinstance(self.document, MixDocument):
            raise MixError("INVALID_CHECKPOINT", "checkpoint document is invalid")


@dataclass(frozen=True)
class MixDocument:
    source_id: str
    segment_ids: tuple[str, ...]
    segments: tuple[dict[str, Any], ...]
    source_layout: str
    source_start: TimePoint
    source_end: TimePoint
    sample_rate: int
    channels: int
    original_audio: MixArtifact
    dialogue_stem: MixArtifact
    final_mix: MixArtifact
    duck_windows: tuple[DuckWindow, ...]
    chunks: tuple[dict[str, Any], ...]
    provenance: MixProvenance
    failures: tuple[MixFailure, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.source_id, "document.source_id", limit=256)
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise MixError("INVALID_DOCUMENT", "document segment IDs must be unique")
        for segment_id in self.segment_ids:
            _text(segment_id, "document.segment_id", limit=256)
        if len(self.segments) != len(self.segment_ids):
            raise MixError("INVALID_DOCUMENT", "document segment metadata must cover every input segment")
        _text(self.source_layout, "document.source_layout", limit=128)
        _canonical_interval(self.source_start, self.source_end, "document.source")
        _integer(self.sample_rate, "document.sample_rate", minimum=1, maximum=U64_MAX)
        _integer(self.channels, "document.channels", minimum=1, maximum=2)
        if self.original_audio.kind != "original_audio" or self.dialogue_stem.kind != "dialogue_stem" or self.final_mix.kind != "final_mix":
            raise MixError("INVALID_DOCUMENT", "document artifact kinds are inconsistent")
        if self.original_audio.sample_rate != self.sample_rate or self.dialogue_stem.sample_rate != self.sample_rate or self.final_mix.sample_rate != self.sample_rate:
            raise MixError("INVALID_DOCUMENT", "document artifact sample rates are inconsistent")
        if self.original_audio.channels != self.channels or self.dialogue_stem.channels != self.channels or self.final_mix.channels != self.channels:
            raise MixError("INVALID_DOCUMENT", "document artifact channels are inconsistent")
        for warning in self.warnings:
            _text(warning, "document.warning", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MIX_CONTRACT_VERSION,
            "kind": "audio_mix_document",
            "source_kind": "source_audio",
            "target_language": TARGET_LANGUAGE,
            "source_id": self.source_id,
            "segment_ids": list(self.segment_ids),
            "segments": list(self.segments),
            "source_layout": self.source_layout,
            "source_start": self.source_start.to_dict(),
            "source_end": self.source_end.to_dict(),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "original_audio": self.original_audio.to_dict(),
            "dialogue_stem": self.dialogue_stem.to_dict(),
            "final_mix": self.final_mix.to_dict(),
            "duck_windows": [item.to_dict() for item in self.duck_windows],
            "chunks": list(self.chunks),
            "provenance": self.provenance.to_dict(),
            "failures": [item.to_dict() for item in self.failures],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class _Pcm:
    sample_rate: int
    channels: int
    frames: tuple[tuple[int, ...], ...]

    @property
    def frame_count(self) -> int:
        return len(self.frames)


def _read_wav(payload: bytes, *, name: str, max_bytes: int) -> _Pcm:
    if not isinstance(payload, bytes) or not payload or len(payload) > max_bytes:
        raise MixError("AUDIO_INVALID", f"{name} is empty or exceeds the configured byte limit")
    try:
        with wave.open(io.BytesIO(payload), "rb") as reader:
            channels = reader.getnchannels()
            sample_rate = reader.getframerate()
            sample_width = reader.getsampwidth()
            frame_count = reader.getnframes()
            compression = reader.getcomptype()
            frames = reader.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as error:
        raise MixError("AUDIO_CORRUPT", f"{name} WAV could not be decoded: {error}") from error
    if compression != "NONE" or sample_width != 2:
        raise MixError("UNSUPPORTED_AUDIO_FORMAT", f"{name} must be uncompressed signed-16 PCM")
    if channels < 1 or channels > 2 or sample_rate < 1 or frame_count < 1:
        raise MixError("AUDIO_CORRUPT", f"{name} WAV metadata is empty or unsupported")
    expected = frame_count * channels * sample_width
    if len(frames) != expected:
        raise MixError("AUDIO_CORRUPT", f"{name} WAV frame data is truncated")
    try:
        unpacked = list(struct.iter_unpack("<" + "h" * channels, frames))
    except struct.error as error:
        raise MixError("AUDIO_CORRUPT", f"{name} WAV PCM alignment is invalid: {error}") from error
    if len(unpacked) != frame_count:
        raise MixError("AUDIO_CORRUPT", f"{name} WAV has no complete frames")
    return _Pcm(sample_rate, channels, tuple(tuple(frame) for frame in unpacked))


def _write_wav(pcm: _Pcm) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(pcm.channels)
        writer.setsampwidth(2)
        writer.setframerate(pcm.sample_rate)
        writer.writeframes(b"".join(struct.pack("<" + "h" * pcm.channels, *frame) for frame in pcm.frames))
    return stream.getvalue()


def _metrics(payload: bytes, pcm: _Pcm) -> AudioMetrics:
    samples = [sample for frame in pcm.frames for sample in frame]
    squared = sum(sample * sample for sample in samples)
    rms = math.sqrt(squared / len(samples)) if samples else 0.0
    if not math.isfinite(rms):
        raise MixError("AUDIO_NONFINITE", "mix RMS is not finite")
    rms_milli = min(1000, int(round(rms * 1000 / 32768)))
    peak = max((abs(sample) for sample in samples), default=0)
    clipped = sum(1 for sample in samples if abs(sample) >= 32767)
    return AudioMetrics(len(samples), pcm.frame_count, rms_milli, peak, clipped, _hash_bytes(payload))


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


def _scale(value: int, milli: int) -> int:
    if milli == 1000:
        return value
    return (value * milli) // 1000 if value >= 0 else -((-value * milli) // 1000)


def _resample_layout(source: _Pcm, target_rate: int, target_channels: int) -> tuple[tuple[int, ...], ...]:
    if not source.frames:
        return ()
    output_count = max(1, (len(source.frames) * target_rate + source.sample_rate - 1) // source.sample_rate)
    output: list[tuple[int, ...]] = []
    for index in range(output_count):
        source_index = min(len(source.frames) - 1, (index * source.sample_rate) // target_rate)
        frame = source.frames[source_index]
        if source.channels == target_channels:
            values = frame
        elif source.channels == 1 and target_channels == 2:
            values = (frame[0], frame[0])
        elif source.channels == 2 and target_channels == 1:
            values = ((frame[0] + frame[1]) // 2,)
        else:
            raise MixError("UNSUPPORTED_LAYOUT", "source and TTS channel layouts are unsupported")
        output.append(tuple(values))
    return tuple(output)


def _normalize(frames: Sequence[tuple[int, ...]], config: MixConfig) -> tuple[tuple[tuple[int, ...], ...], tuple[str, ...]]:
    if not frames:
        raise MixError("AUDIO_EMPTY", "cannot normalize an empty audio buffer")
    peak = max((abs(sample) for frame in frames for sample in frame), default=0)
    samples = [sample for frame in frames for sample in frame]
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) if samples else 0.0
    factor_milli = 1000
    warnings: list[str] = []
    if peak > config.max_peak:
        factor_milli = min(factor_milli, (config.max_peak * 1000) // peak)
        warnings.append("mix peak was reduced to the configured headroom")
    rms_milli = min(1000, int(round(rms * 1000 / 32768))) if math.isfinite(rms) else 1000
    if rms_milli > config.max_rms_milli:
        factor_milli = min(factor_milli, (config.max_rms_milli * 1000) // max(1, rms_milli))
        warnings.append("mix RMS was reduced to the configured loudness ceiling")
    normalized = tuple(tuple(max(-32768, min(32767, _scale(sample, factor_milli))) for sample in frame) for frame in frames)
    return normalized, tuple(dict.fromkeys(warnings))


def _validate_loudness(payload: bytes, pcm: _Pcm, config: MixConfig, *, allow_silence: bool = False) -> AudioMetrics:
    metrics = _metrics(payload, pcm)
    if metrics.clipped_samples * 1_000_000 > metrics.sample_count * config.max_clip_fraction_ppm:
        raise MixError("AUDIO_CLIPPING", "mix clipping exceeds the configured fraction")
    if not allow_silence and metrics.rms_milli == 0:
        raise MixError("AUDIO_SILENT", "final mix is silent")
    return metrics


def _object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise MixError("INVALID_DOCUMENT", f"{name} must be an object")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        raise MixError("INVALID_DOCUMENT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise MixError("INVALID_DOCUMENT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise MixError("INVALID_DOCUMENT", f"{name} must be a canonical decimal string")
    pattern = r"^(0|-?[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise MixError("INVALID_DOCUMENT", f"{name} is not canonical decimal")
    return _integer(int(value), name, minimum=I64_MIN if signed else 1, maximum=I64_MAX if signed else U64_MAX)


def _parse_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or type(value["schema_version"]) is not int or value["schema_version"] != MIX_CONTRACT_VERSION:
        raise MixError("INVALID_DOCUMENT", f"{name} discriminator/version is unsupported")
    base = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    try:
        return TimePoint(
            _decimal(value["ticks"], f"{name}.ticks", signed=True),
            TimeBase(
                _decimal(base["numerator"], f"{name}.time_base.numerator", signed=False),
                _decimal(base["denominator"], f"{name}.time_base.denominator", signed=False),
            ),
        )
    except MixError:
        raise
    except Exception as error:
        raise MixError("INVALID_DOCUMENT", f"{name} is not a valid time point: {error}") from error


def _parse_metrics(value: Any, name: str) -> AudioMetrics:
    value = _object(value, name, {"sample_count", "frame_count", "rms_milli", "peak", "clipped_samples", "content_hash"})
    return AudioMetrics(
        _integer(value["sample_count"], f"{name}.sample_count", minimum=1, maximum=U64_MAX),
        _integer(value["frame_count"], f"{name}.frame_count", minimum=1, maximum=U64_MAX),
        _integer(value["rms_milli"], f"{name}.rms_milli", minimum=0, maximum=1000),
        _integer(value["peak"], f"{name}.peak", minimum=0, maximum=32768),
        _integer(value["clipped_samples"], f"{name}.clipped_samples", minimum=0, maximum=U64_MAX),
        _ensure_hash(value["content_hash"], f"{name}.content_hash"),
    )


def _parse_artifact(value: Any, name: str) -> MixArtifact:
    value = _object(value, name, {"artifact_id", "kind", "path", "content_hash", "format", "sample_rate", "channels", "bits_per_sample", "frame_count", "metrics"})
    return MixArtifact(
        _text(value["artifact_id"], f"{name}.artifact_id", limit=256),
        value["kind"],
        _text(value["path"], f"{name}.path", limit=4096),
        _ensure_hash(value["content_hash"], f"{name}.content_hash"),
        value["format"],
        _integer(value["sample_rate"], f"{name}.sample_rate", minimum=1, maximum=U64_MAX),
        _integer(value["channels"], f"{name}.channels", minimum=1, maximum=2),
        value["bits_per_sample"],
        _integer(value["frame_count"], f"{name}.frame_count", minimum=1, maximum=U64_MAX),
        _parse_metrics(value["metrics"], f"{name}.metrics"),
    )


def validate_mix_document(value: Mapping[str, Any]) -> None:
    value = _object(
        value,
        "document",
        {"schema_version", "kind", "source_kind", "target_language", "source_id", "segment_ids", "segments", "source_layout", "source_start", "source_end", "sample_rate", "channels", "original_audio", "dialogue_stem", "final_mix", "duck_windows", "chunks", "provenance", "failures", "warnings"},
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != MIX_CONTRACT_VERSION or value["kind"] != "audio_mix_document" or value["source_kind"] != "source_audio" or value["target_language"] != TARGET_LANGUAGE:
        raise MixError("INVALID_DOCUMENT", "audio mix document discriminator/version is unsupported")
    source_id = _text(value["source_id"], "document.source_id", limit=256)
    segment_values = value["segment_ids"]
    if type(segment_values) is not list:
        raise MixError("INVALID_DOCUMENT", "document.segment_ids must be an array")
    segment_ids = tuple(_text(item, "document.segment_id", limit=256) for item in segment_values)
    if len(segment_ids) != len(set(segment_ids)):
        raise MixError("INVALID_DOCUMENT", "document.segment_ids must be unique")
    raw_segments = value["segments"]
    if type(raw_segments) is not list or len(raw_segments) != len(segment_ids):
        raise MixError("INVALID_DOCUMENT", "document.segments must cover every input segment")
    segment_metadata: dict[str, dict[str, Any]] = {}
    for position, raw_segment in enumerate(raw_segments):
        item = _object(raw_segment, f"segments[{position}]", {"segment_id", "source_utterance_id", "status", "confidence", "fallback_used"})
        segment_id = _text(item["segment_id"], "segment.segment_id", limit=256)
        if segment_id in segment_metadata or segment_id not in set(segment_ids):
            raise MixError("INVALID_DOCUMENT", "document segment metadata IDs are invalid")
        _text(item["source_utterance_id"], "segment.source_utterance_id", limit=256)
        if item["status"] not in {"completed", "failed"}:
            raise MixError("INVALID_DOCUMENT", "document segment status is invalid")
        confidence = item["confidence"]
        if type(confidence) not in (int, float) or isinstance(confidence, bool) or not math.isfinite(float(confidence)) or not 0.0 <= float(confidence) <= 1.0:
            raise MixError("INVALID_DOCUMENT", "document segment confidence is invalid")
        _boolean(item["fallback_used"], "segment.fallback_used")
        segment_metadata[segment_id] = item
    if set(segment_metadata) != set(segment_ids):
        raise MixError("INVALID_DOCUMENT", "document segment metadata IDs do not match segment_ids")
    source_start = _parse_point(value["source_start"], "document.source_start")
    source_end = _parse_point(value["source_end"], "document.source_end")
    _canonical_interval(source_start, source_end, "document.source")
    sample_rate = _integer(value["sample_rate"], "document.sample_rate", minimum=1, maximum=U64_MAX)
    channels = _integer(value["channels"], "document.channels", minimum=1, maximum=2)
    original = _parse_artifact(value["original_audio"], "original_audio")
    stem = _parse_artifact(value["dialogue_stem"], "dialogue_stem")
    final = _parse_artifact(value["final_mix"], "final_mix")
    if original.kind != "original_audio" or stem.kind != "dialogue_stem" or final.kind != "final_mix":
        raise MixError("INVALID_DOCUMENT", "artifact kinds do not match their document fields")
    all_artifacts = (original, stem, final)
    if any(item.sample_rate != sample_rate or item.channels != channels for item in all_artifacts):
        raise MixError("INVALID_DOCUMENT", "artifact layout differs from document layout")
    if len({item.artifact_id for item in all_artifacts}) != 3:
        raise MixError("INVALID_DOCUMENT", "artifact IDs must be unique")

    provenance_value = _object(value["provenance"], "provenance", {"producer", "producer_version", "backend_id", "runtime", "timeline_contract", "config_hash", "source_hash", "input_hash", "requested_profile", "hardware_profile", "resource", "non_destructive"})
    resource_value = _object(provenance_value["resource"], "provenance.resource", {"max_threads", "max_memory_mb", "max_batch_items"})
    MixProvenance(**{**provenance_value, "resource": ResourceProfile(**resource_value)})

    windows = value["duck_windows"]
    if type(windows) is not list:
        raise MixError("INVALID_DOCUMENT", "duck_windows must be an array")
    seen_window_ids: set[str] = set()
    for position, raw_window in enumerate(windows):
        item = _object(raw_window, f"duck_windows[{position}]", {"segment_id", "start_sample", "end_sample", "attack_samples", "release_samples", "duck_gain_milli"})
        window = DuckWindow(
            _text(item["segment_id"], "duck.segment_id", limit=256),
            _integer(item["start_sample"], "duck.start_sample", minimum=I64_MIN, maximum=I64_MAX),
            _integer(item["end_sample"], "duck.end_sample", minimum=I64_MIN, maximum=I64_MAX),
            _integer(item["attack_samples"], "duck.attack_samples", minimum=0, maximum=U64_MAX),
            _integer(item["release_samples"], "duck.release_samples", minimum=0, maximum=U64_MAX),
            _integer(item["duck_gain_milli"], "duck.duck_gain_milli", minimum=0, maximum=1000),
        )
        if window.segment_id in seen_window_ids or window.segment_id not in set(segment_ids):
            raise MixError("INVALID_DOCUMENT", "duck windows must reference unique source segments")
        seen_window_ids.add(window.segment_id)

    chunks = value["chunks"]
    if type(chunks) is not list:
        raise MixError("INVALID_DOCUMENT", "chunks must be an array")
    claimed: set[str] = set()
    chunk_ids: set[str] = set()
    for position, raw_chunk in enumerate(chunks):
        item = _object(raw_chunk, f"chunks[{position}]", {"chunk_id", "index", "segment_ids", "status", "attempt"}, {"error_code"})
        chunk_id = _text(item["chunk_id"], "chunk.chunk_id", limit=128)
        if chunk_id in chunk_ids or item["index"] != position:
            raise MixError("INVALID_DOCUMENT", "chunks must use unique contiguous indexes")
        _integer(item["index"], "chunk.index", minimum=0)
        raw_ids = item["segment_ids"]
        if type(raw_ids) is not list:
            raise MixError("INVALID_DOCUMENT", "chunk.segment_ids must be an array")
        ids = tuple(_text(identifier, "chunk.segment_id", limit=256) for identifier in raw_ids)
        if len(ids) != len(set(ids)) or claimed.intersection(ids) or not set(ids) <= set(segment_ids):
            raise MixError("INVALID_DOCUMENT", "chunk segment ownership is invalid")
        claimed.update(ids)
        if item["status"] not in {"completed", "degraded", "failed", "skipped"}:
            raise MixError("INVALID_DOCUMENT", "chunk status is invalid")
        _integer(item["attempt"], "chunk.attempt", minimum=1, maximum=255)
        if item["status"] == "failed" and "error_code" not in item:
            raise MixError("INVALID_DOCUMENT", "failed chunk lacks error_code")
        if "error_code" in item:
            _text(item["error_code"], "chunk.error_code", limit=128)
        chunk_ids.add(chunk_id)
    if claimed != set(segment_ids):
        raise MixError("INVALID_DOCUMENT", "every TTS segment must belong to exactly one chunk")

    failures = value["failures"]
    if type(failures) is not list:
        raise MixError("INVALID_DOCUMENT", "failures must be an array")
    for position, raw_failure in enumerate(failures):
        item = _object(raw_failure, f"failures[{position}]", {"code", "segment_id", "condition", "recoverable", "fallback_used"})
        segment_id = _text(item["segment_id"], "failure.segment_id", limit=256)
        if segment_id not in set(segment_ids):
            raise MixError("INVALID_DOCUMENT", "failure references unknown segment")
        MixFailure(_text(item["code"], "failure.code", limit=128), segment_id, _text(item["condition"], "failure.condition", limit=4096), item["recoverable"], item["fallback_used"])
    if type(value["warnings"]) is not list:
        raise MixError("INVALID_DOCUMENT", "warnings must be an array")
    for warning in value["warnings"]:
        _text(warning, "document.warning", limit=4096)


def parse_mix_json(text: str | bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise MixError("DUPLICATE_FIELD", f"duplicate JSON field {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise MixError("INVALID_DOCUMENT", f"non-finite JSON number {value}")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except MixError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MixError("MALFORMED_JSON", str(error)) from error
    validate_mix_document(value)
    return value


class LocalAudioMixer:
    """CPU-only, non-destructive AUD-0 mixer for validated local artifacts."""

    def __init__(
        self,
        *,
        config: MixConfig,
        output_dir: str | Path,
        producer: str = "dubflow-audio-mix",
        producer_version: str = "1.0.0",
        backend_id: str = "pcm-duck-v1",
        runtime: str = "python-stdlib",
        hardware_profile: str = "cpu",
    ) -> None:
        if not isinstance(config, MixConfig):
            raise MixError("INVALID_CONFIG", "mixer config must be MixConfig")
        _text(producer, "producer", limit=256)
        _text(producer_version, "producer_version", limit=256)
        _text(backend_id, "backend_id", limit=256)
        _text(runtime, "runtime", limit=256)
        if hardware_profile not in {"cpu", "gpu", "fixture", "mixed"}:
            raise MixError("INVALID_PROFILE", "hardware_profile is unsupported")
        self.config = config
        self.output_dir = Path(output_dir)
        self.producer = producer
        self.producer_version = producer_version
        self.backend_id = backend_id
        self.runtime = runtime
        self.hardware_profile = hardware_profile

    @staticmethod
    def segment_input_hash(segments: Sequence[MixSegment] | Sequence[Any]) -> str:
        values: list[dict[str, Any]] = []
        for item in segments:
            segment = item if isinstance(item, MixSegment) else MixSegment.from_tts_artifact(item)
            values.append({
                "segment_id": segment.segment_id,
                "source_utterance_id": segment.source_utterance_id,
                "start": segment.start.to_dict(),
                "end": segment.end.to_dict(),
                "audio_hash": _hash_bytes(segment.audio_bytes) if segment.audio_bytes is not None else None,
                "status": segment.status,
                "condition": segment.condition,
                "confidence": float(segment.confidence),
                "fallback_used": segment.fallback_used,
            })
        values.sort(key=lambda item: (item["start"]["ticks"], item["end"]["ticks"], item["segment_id"]))
        return _hash_json(values)

    def checkpoint_for(self, document: MixDocument, segments: Sequence[MixSegment] | Sequence[Any]) -> MixCheckpoint:
        return MixCheckpoint(
            document.provenance.source_hash,
            document.provenance.config_hash,
            self.segment_input_hash(segments),
            document,
        )

    def mix(
        self,
        source: SourceAudio,
        segments: Sequence[MixSegment] | Sequence[Any],
        *,
        input_hash: str,
        checkpoint: MixCheckpoint | None = None,
    ) -> MixDocument:
        if not isinstance(source, SourceAudio):
            raise MixError("INVALID_SOURCE", "source must be SourceAudio")
        input_hash = _ensure_hash(input_hash, "mix.input_hash")
        source_payload = source.audio_bytes
        source_hash = _hash_bytes(source_payload)
        segment_values = tuple(item if isinstance(item, MixSegment) else MixSegment.from_tts_artifact(item) for item in segments)
        segment_ids = tuple(item.segment_id for item in segment_values)
        if len(set(segment_ids)) != len(segment_ids):
            raise MixError("DUPLICATE_SEGMENT_ID", "TTS segment IDs must be unique")
        segment_hash = self.segment_input_hash(segment_values)
        if checkpoint is not None and self._checkpoint_matches(checkpoint, source_hash, segment_hash, input_hash):
            return checkpoint.document

        try:
            source_pcm = _read_wav(source_payload, name="source", max_bytes=self.config.max_source_bytes)
            if source_pcm.channels not in {1, 2}:
                raise MixError("UNSUPPORTED_LAYOUT", "source channel layout is unsupported")
            source_start_sample = map_timepoint_to_sample(source.start, source_pcm.sample_rate)
            source_end_sample = map_timepoint_to_sample(source.end, source_pcm.sample_rate, end=True)
            expected_frames = source_end_sample - source_start_sample
            if expected_frames <= 0 or abs(expected_frames - source_pcm.frame_count) > self.config.max_duration_error_ticks:
                raise MixError("SOURCE_DURATION_MISMATCH", "source frame duration differs from canonical source interval")
        except MixError as error:
            raise MixStageError(error.code, error.condition) from error

        failures: list[MixFailure] = []
        warnings: list[str] = []
        valid_segments: list[tuple[MixSegment, _Pcm, tuple[tuple[int, ...], ...], DuckWindow, int, int]] = []
        outcomes: dict[str, str] = {}
        windows: list[DuckWindow] = []
        for segment in sorted(segment_values, key=lambda item: (item.start.ticks, item.end.ticks, item.segment_id)):
            if segment.start.time_base != source.start.time_base:
                failures.append(MixFailure("TIMELINE_TIME_BASE_MISMATCH", segment.segment_id, "TTS segment and source use different time bases", True, segment.fallback_used))
                outcomes[segment.segment_id] = "failed"
                continue
            if segment.status != "available" or segment.audio_bytes is None:
                code = "TTS_MISSING" if segment.status == "missing" else "TTS_FAILED"
                failures.append(MixFailure(code, segment.segment_id, segment.condition or "TTS segment has no usable audio", True, segment.fallback_used))
                outcomes[segment.segment_id] = "failed"
                continue
            try:
                tts_pcm = _read_wav(segment.audio_bytes, name=f"TTS {segment.segment_id}", max_bytes=self.config.max_tts_bytes)
                actual_ticks = _ceil_div(tts_pcm.frame_count * source.start.time_base.denominator, tts_pcm.sample_rate * source.start.time_base.numerator)
                target_ticks = segment.end.ticks - segment.start.ticks
                if abs(actual_ticks - target_ticks) > self.config.max_duration_error_ticks:
                    raise MixError("TTS_DURATION_MISMATCH", f"TTS duration {actual_ticks} differs from slot {target_ticks}", segment_id=segment.segment_id)
                resampled = _resample_layout(tts_pcm, source_pcm.sample_rate, source_pcm.channels)
                start_absolute = map_timepoint_to_sample(segment.start, source_pcm.sample_rate)
                end_absolute = map_timepoint_to_sample(segment.end, source_pcm.sample_rate, end=True)
                if end_absolute <= start_absolute:
                    raise MixError("INVALID_TIMELINE", "TTS slot maps to no positive source sample interval", segment_id=segment.segment_id)
                attack = _duration_ticks_to_samples(self.config.attack_ticks, source.start.time_base, source_pcm.sample_rate)
                release = _duration_ticks_to_samples(self.config.release_ticks, source.start.time_base, source_pcm.sample_rate)
                window = DuckWindow(segment.segment_id, start_absolute, end_absolute, attack, release, self.config.duck_gain_milli)
                offset = start_absolute - source_start_sample
                if offset >= source_pcm.frame_count or offset + len(resampled) <= 0:
                    raise MixError("TTS_OUTSIDE_SOURCE", "TTS slot does not overlap source audio", segment_id=segment.segment_id)
                valid_segments.append((segment, tts_pcm, resampled, window, offset, end_absolute - start_absolute))
                windows.append(window)
                outcomes[segment.segment_id] = "completed"
            except MixError as error:
                failures.append(MixFailure(error.code, segment.segment_id, error.condition, error.recoverable, segment.fallback_used))
                outcomes[segment.segment_id] = "failed"

        dialogue = [[0 for _ in range(source_pcm.channels)] for _ in range(source_pcm.frame_count)]
        for segment, _tts_pcm, resampled, _window, offset, _target_count in valid_segments:
            for index, frame in enumerate(resampled):
                destination = offset + index
                if 0 <= destination < len(dialogue):
                    for channel, value in enumerate(frame):
                        dialogue[destination][channel] += _scale(value, self.config.dialogue_gain_milli)

        envelope = [1000] * source_pcm.frame_count
        for window in windows:
            start = max(0, window.start_sample - source_start_sample)
            end = min(source_pcm.frame_count, window.end_sample - source_start_sample)
            if end <= start:
                continue
            for frame in range(max(0, start - window.attack_samples), min(source_pcm.frame_count, end + window.release_samples)):
                if frame < start:
                    distance = start - frame
                    if window.attack_samples == 0:
                        continue
                    level = 1000 - ((1000 - window.duck_gain_milli) * (window.attack_samples - distance + 1) // (window.attack_samples + 1))
                elif frame >= end:
                    distance = frame - end
                    if window.release_samples == 0:
                        continue
                    level = window.duck_gain_milli + ((1000 - window.duck_gain_milli) * (distance + 1) // (window.release_samples + 1))
                else:
                    level = window.duck_gain_milli
                envelope[frame] = min(envelope[frame], max(0, min(1000, level)))

        dialogue_frames = tuple(tuple(max(-32768, min(32767, value)) for value in frame) for frame in dialogue)
        dialogue_frames, dialogue_warnings = _normalize(dialogue_frames, self.config)
        warnings.extend(dialogue_warnings)
        dialogue_pcm = _Pcm(source_pcm.sample_rate, source_pcm.channels, dialogue_frames)
        dialogue_payload = _write_wav(dialogue_pcm)
        dialogue_metrics = _validate_loudness(dialogue_payload, dialogue_pcm, self.config, allow_silence=True)

        ducked_source = tuple(tuple(_scale(value, envelope[index]) for value in frame) for index, frame in enumerate(source_pcm.frames))
        combined = tuple(tuple(ducked_source[index][channel] + dialogue_frames[index][channel] for channel in range(source_pcm.channels)) for index in range(source_pcm.frame_count))
        final_frames, final_warnings = _normalize(combined, self.config)
        warnings.extend(final_warnings)
        final_pcm = _Pcm(source_pcm.sample_rate, source_pcm.channels, final_frames)
        final_payload = _write_wav(final_pcm)
        final_metrics = _validate_loudness(final_payload, final_pcm, self.config, allow_silence=True)
        source_metrics = _metrics(source_payload, source_pcm)
        if source_metrics.clipped_samples:
            warnings.append("original source contains clipped samples; it was preserved as supplied")
        if final_metrics.rms_milli == 0:
            warnings.append("final mix is silent because no usable source or TTS energy was available")
        elif final_metrics.rms_milli < self.config.target_rms_milli:
            warnings.append("final mix is below the configured target RMS; source and dialogue were preserved")
        if failures:
            warnings.append("one or more TTS segments were unavailable; the original source remains in the final mix")

        chunks: list[dict[str, Any]] = []
        ordered_ids = [item.segment_id for item in sorted(segment_values, key=lambda item: (item.start.ticks, item.end.ticks, item.segment_id))]
        for index in range(0, len(ordered_ids), self.config.max_segments_per_chunk):
            ids = ordered_ids[index : index + self.config.max_segments_per_chunk]
            statuses = [outcomes.get(identifier, "failed") for identifier in ids]
            if statuses and all(status == "completed" for status in statuses):
                status = "completed"
            elif statuses and all(status == "failed" for status in statuses):
                status = "failed"
            else:
                status = "degraded"
            chunk: dict[str, Any] = {
                "chunk_id": "mix-chunk-" + sha256(json.dumps({"source": source_hash, "config": self.config.content_hash(), "index": index, "segments": ids}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24],
                "index": index // self.config.max_segments_per_chunk,
                "segment_ids": ids,
                "status": status,
                "attempt": 1,
            }
            if status in {"failed", "degraded"}:
                chunk["error_code"] = next((failure.code for failure in failures if failure.segment_id in ids), "TTS_UNAVAILABLE")
            chunks.append(chunk)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        artifact_prefix = source_hash[7:23]
        original_path = self.output_dir / f"mix-original-{artifact_prefix}.wav"
        stem_path = self.output_dir / f"mix-dialogue-{artifact_prefix}-{self.config.content_hash()[7:19]}.wav"
        final_path = self.output_dir / f"mix-final-{artifact_prefix}-{self.config.content_hash()[7:19]}.wav"
        try:
            _atomic_write(original_path, source_payload)
            _atomic_write(stem_path, dialogue_payload)
            _atomic_write(final_path, final_payload)
        except OSError as error:
            raise MixStageError("ARTIFACT_WRITE_FAILED", _safe_condition(error)) from error

        provenance = MixProvenance(
            self.producer,
            self.producer_version,
            self.backend_id,
            self.runtime,
            "timeline-v1",
            self.config.content_hash(),
            source_hash,
            input_hash,
            self.config.requested_profile,
            self.hardware_profile,
            self.config.resource,
        )
        segment_metadata = tuple(
            {
                "segment_id": item.segment_id,
                "source_utterance_id": item.source_utterance_id,
                "status": outcomes.get(item.segment_id, "failed"),
                "confidence": float(item.confidence),
                "fallback_used": item.fallback_used,
            }
            for item in sorted(segment_values, key=lambda value: (value.start.ticks, value.end.ticks, value.segment_id))
        )
        document = MixDocument(
            source.source_id,
            tuple(ordered_ids),
            segment_metadata,
            source.layout,
            source.start,
            source.end,
            source_pcm.sample_rate,
            source_pcm.channels,
            MixArtifact("original-audio", "original_audio", str(original_path), source_hash, "wav", source_pcm.sample_rate, source_pcm.channels, 16, source_pcm.frame_count, source_metrics),
            MixArtifact("dialogue-stem", "dialogue_stem", str(stem_path), dialogue_metrics.content_hash, "wav", dialogue_pcm.sample_rate, dialogue_pcm.channels, 16, dialogue_pcm.frame_count, dialogue_metrics),
            MixArtifact("final-mix", "final_mix", str(final_path), final_metrics.content_hash, "wav", final_pcm.sample_rate, final_pcm.channels, 16, final_pcm.frame_count, final_metrics),
            tuple(windows),
            tuple(chunks),
            provenance,
            tuple(failures),
            tuple(dict.fromkeys(warnings)),
        )
        validate_mix_document(document.to_dict())
        return document

    def _checkpoint_matches(self, checkpoint: MixCheckpoint, source_hash: str, segment_hash: str, input_hash: str) -> bool:
        if checkpoint.source_hash != source_hash or checkpoint.config_hash != self.config.content_hash() or checkpoint.segment_hash != segment_hash:
            return False
        document = checkpoint.document
        if document.provenance.source_hash != source_hash or document.provenance.config_hash != self.config.content_hash() or document.provenance.input_hash != input_hash:
            return False
        try:
            for artifact in (document.original_audio, document.dialogue_stem, document.final_mix):
                payload = Path(artifact.path).read_bytes()
                if _hash_bytes(payload) != artifact.content_hash:
                    return False
                parsed = _read_wav(payload, name=artifact.kind, max_bytes=self.config.max_source_bytes)
                parsed_metrics = _metrics(payload, parsed)
                if parsed.sample_rate != artifact.sample_rate or parsed.channels != artifact.channels or parsed.frame_count != artifact.frame_count or parsed_metrics != artifact.metrics:
                    return False
        except (OSError, MixError):
            return False
        try:
            validate_mix_document(document.to_dict())
        except MixError:
            return False
        return True


__all__ = [
    "AudioMetrics",
    "DuckWindow",
    "LocalAudioMixer",
    "MIX_CONTRACT_VERSION",
    "MixArtifact",
    "MixCheckpoint",
    "MixConfig",
    "MixDocument",
    "MixError",
    "MixFailure",
    "MixProvenance",
    "MixSegment",
    "MixStageError",
    "PCM_FORMAT",
    "ResourceProfile",
    "SourceAudio",
    "parse_mix_json",
    "validate_mix_document",
]
