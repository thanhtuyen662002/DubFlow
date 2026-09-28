"""Local ASR contract and deterministic adapter.

The module intentionally uses only the Python standard library.  A model
runtime supplies an :class:`AsrBackend`; the adapter owns canonical timestamp
validation, bounded chunk planning, overlap merge, provenance and structured
failure handling.  It never writes durable state or model artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence
import unicodedata


ASR_CONTRACT_VERSION = 1
U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U128_MAX = (1 << 128) - 1
_LANGUAGE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class AsrError(ValueError):
    """A stable, serializable ASR failure rather than an opaque traceback."""

    def __init__(
        self,
        code: str,
        condition: str,
        *,
        retryable: bool = False,
        chunk_id: str | None = None,
        attempt: int = 1,
        fallback_used: bool = False,
    ) -> None:
        if not code or not condition:
            raise ValueError("ASR failures require a code and condition")
        if type(retryable) is not bool or type(attempt) is not int or not 1 <= attempt <= 255:
            raise ValueError("invalid ASR failure retry metadata")
        self.code = code
        self.condition = condition
        self.retryable = retryable
        self.chunk_id = chunk_id
        self.attempt = attempt
        self.fallback_used = fallback_used
        super().__init__(f"{code}: {condition}")


class AsrBackendError(AsrError):
    """An adapter/backend failure that may be classified per chunk."""


class AsrStageError(AsrError):
    """All chunks failed, so no usable transcript can be produced."""

    def __init__(self, transcript: "Transcript") -> None:
        self.transcript = transcript
        super().__init__(
            "ASR_FAILED",
            "every planned audio chunk failed",
            retryable=any(failure.retryable for failure in transcript.failures),
        )


def _int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise AsrError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise AsrError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise AsrError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise AsrError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _confidence(value: Any, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise AsrError("INVALID_CONFIDENCE", f"{name} must be finite")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise AsrError("INVALID_CONFIDENCE", f"{name} must be between 0 and 1")
    return number


def _language(value: Any) -> str:
    value = _text(value, "language", limit=32)
    if not _LANGUAGE.fullmatch(value):
        raise AsrError("INVALID_LANGUAGE", f"unsupported language tag {value!r}")
    return value


def _checked_product(left: int, right: int, name: str) -> int:
    value = left * right
    if value > U128_MAX:
        raise AsrError("TIMELINE_OVERFLOW", f"{name} exceeds the checked u128 range")
    return value


def _compare_positive_fractions(left_num: int, left_den: int, right_num: int, right_den: int) -> int:
    gcd = math.gcd(left_num, left_den)
    left_num //= gcd
    left_den //= gcd
    gcd = math.gcd(right_num, right_den)
    right_num //= gcd
    right_den //= gcd
    gcd = math.gcd(left_num, right_num)
    left_num //= gcd
    right_num //= gcd
    gcd = math.gcd(left_den, right_den)
    left_den //= gcd
    right_den //= gcd
    left = _checked_product(left_num, right_den, "timeline comparison")
    right = _checked_product(right_num, left_den, "timeline comparison")
    return (left > right) - (left < right)


def _safe_condition(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = _CONTROL.sub(" ", text)
    text = text[:4096]
    return text or "backend failure"


@dataclass(frozen=True, order=True)
class TimeBase:
    """Reduced positive rational seconds per tick."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _int(self.numerator, "time_base.numerator", minimum=1, maximum=U64_MAX)
        _int(self.denominator, "time_base.denominator", minimum=1, maximum=U64_MAX)
        import math as _math

        if _math.gcd(self.numerator, self.denominator) != 1:
            raise AsrError("NON_REDUCED_TIME_BASE", "time base factors must be coprime")

    def to_dict(self) -> dict[str, str]:
        return {"numerator": str(self.numerator), "denominator": str(self.denominator)}


@dataclass(frozen=True)
class TimePoint:
    ticks: int
    time_base: TimeBase

    def __post_init__(self) -> None:
        _int(self.ticks, "time_point.ticks", minimum=I64_MIN, maximum=I64_MAX)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "time_point",
            "schema_version": ASR_CONTRACT_VERSION,
            "ticks": str(self.ticks),
            "time_base": self.time_base.to_dict(),
        }

    def compare(self, other: "TimePoint") -> int:
        if not isinstance(other, TimePoint):
            raise AsrError("INVALID_TIME_POINT", "comparison requires a time point")
        if self.time_base == other.time_base:
            return (self.ticks > other.ticks) - (self.ticks < other.ticks)
        if (self.ticks < 0) != (other.ticks < 0):
            return -1 if self.ticks < 0 else 1
        left_num = _checked_product(abs(self.ticks), self.time_base.numerator, "timeline comparison")
        right_num = _checked_product(abs(other.ticks), other.time_base.numerator, "timeline comparison")
        ordering = _compare_positive_fractions(
            left_num,
            self.time_base.denominator,
            right_num,
            other.time_base.denominator,
        )
        return -ordering if self.ticks < 0 else ordering

    def rescale(self, target: TimeBase, *, rounding: str = "exact") -> "TimePoint":
        _text(rounding, "rounding", limit=32)
        ratio_numerator = _checked_product(self.time_base.numerator, target.denominator, "timestamp rescale")
        ratio_denominator = _checked_product(self.time_base.denominator, target.numerator, "timestamp rescale")
        divisor = math.gcd(ratio_numerator, ratio_denominator)
        ratio_numerator //= divisor
        ratio_denominator //= divisor
        magnitude = abs(self.ticks)
        divisor = math.gcd(magnitude, ratio_denominator)
        if divisor:
            magnitude //= divisor
            ratio_denominator //= divisor
        product = _checked_product(magnitude, ratio_numerator, "timestamp rescale")
        quotient, remainder = divmod(product, ratio_denominator)
        if remainder and rounding == "exact":
            raise AsrError("NON_INTEGRAL_RESCALE", "timestamp cannot be represented exactly")
        if rounding == "floor":
            result = -(quotient + (1 if self.ticks < 0 and remainder else 0)) if self.ticks < 0 else quotient
        elif rounding == "ceil":
            result = -quotient if self.ticks < 0 else quotient + (1 if remainder else 0)
        elif rounding == "toward_zero" or (rounding == "exact" and not remainder):
            result = -quotient if self.ticks < 0 else quotient
        elif rounding == "nearest_ties_to_even":
            twice = remainder * 2
            rounded = quotient + (1 if twice > ratio_denominator or (twice == ratio_denominator and quotient % 2) else 0)
            result = -rounded if self.ticks < 0 else rounded
        else:
            raise AsrError("INVALID_ROUNDING", f"unsupported rounding policy {rounding!r}")
        _int(result, "rescaled ticks", minimum=I64_MIN, maximum=I64_MAX)
        return TimePoint(result, target)


@dataclass(frozen=True)
class TimeInterval:
    start: TimePoint
    end: TimePoint

    def __post_init__(self) -> None:
        if self.start.time_base != self.end.time_base or self.start.compare(self.end) >= 0:
            raise AsrError("INVALID_INTERVAL", "interval must be positive and use one time base")

    @property
    def time_base(self) -> TimeBase:
        return self.start.time_base

    def contains(self, point: TimePoint) -> bool:
        return self.start.compare(point) <= 0 and point.compare(self.end) <= 0

    def intersects(self, other: "TimeInterval") -> bool:
        return self.start.compare(other.end) < 0 and other.start.compare(self.end) < 0

    def overlap_ticks(self, other: "TimeInterval") -> int:
        if self.time_base != other.time_base or not self.intersects(other):
            return 0
        left = max(self.start.ticks, other.start.ticks)
        right = min(self.end.ticks, other.end.ticks)
        return max(0, right - left)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}


@dataclass(frozen=True)
class RawWord:
    word_id: str
    text: str
    start: TimePoint
    end: TimePoint
    confidence: float


@dataclass(frozen=True)
class RawUtterance:
    utterance_id: str
    start: TimePoint
    end: TimePoint
    language: str
    text: str
    words: tuple[RawWord, ...]
    confidence: float

    @classmethod
    def from_words(
        cls,
        utterance_id: str,
        language: str,
        words: Sequence[RawWord],
        *,
        confidence: float,
    ) -> "RawUtterance":
        if not words:
            raise AsrError("EMPTY_UTTERANCE", "an utterance must contain at least one word")
        return cls(
            utterance_id,
            words[0].start,
            words[-1].end,
            language,
            " ".join(word.text for word in words),
            tuple(words),
            confidence,
        )


@dataclass(frozen=True)
class VadSegment:
    start: TimePoint
    end: TimePoint
    confidence: float

    def __post_init__(self) -> None:
        _confidence(self.confidence, "vad.confidence")
        if self.start.time_base != self.end.time_base or self.start.compare(self.end) >= 0:
            raise AsrError("INVALID_VAD_INTERVAL", "VAD segment must be positive")


@dataclass(frozen=True)
class BackendResult:
    utterances: tuple[RawUtterance, ...] = ()
    vad_segments: tuple[VadSegment, ...] = ()


class AsrBackend(Protocol):
    def transcribe(self, chunk: "AudioChunk") -> BackendResult:
        """Transcribe one bounded decode window without durable mutations."""


@dataclass(frozen=True)
class AdapterConfig:
    chunk_ticks: int
    overlap_ticks: int
    requested_profile: str = "cpu"
    max_attempts: int = 1

    def __post_init__(self) -> None:
        _int(self.chunk_ticks, "chunk_ticks", minimum=2, maximum=I64_MAX)
        _int(self.overlap_ticks, "overlap_ticks", minimum=1, maximum=I64_MAX)
        if self.overlap_ticks >= self.chunk_ticks:
            raise AsrError("INVALID_CHUNK_CONFIG", "overlap must be smaller than chunk duration")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise AsrError("INVALID_PROFILE", "requested profile is unsupported")
        _int(self.max_attempts, "max_attempts", minimum=1, maximum=3)

    def to_hash(self) -> str:
        payload = json.dumps(
            {
                "chunk_ticks": self.chunk_ticks,
                "overlap_ticks": self.overlap_ticks,
                "requested_profile": self.requested_profile,
                "max_attempts": self.max_attempts,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + sha256(payload).hexdigest()


@dataclass(frozen=True)
class Provenance:
    producer: str
    producer_version: str
    backend_id: str
    model_id: str
    model_version: str
    runtime: str
    timeline_contract: str
    config_hash: str
    input_hash: str
    requested_profile: str
    hardware_profile: str
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("producer", "producer_version", "backend_id", "model_id", "model_version", "runtime", "timeline_contract"):
            _text(getattr(self, name), f"provenance.{name}", limit=256)
        if self.timeline_contract != "timeline-v1":
            raise AsrError("UNSUPPORTED_TIMELINE_CONTRACT", "ASR v1 requires timeline-v1")
        for name in ("config_hash", "input_hash"):
            value = _text(getattr(self, name), f"provenance.{name}", limit=80)
            if not _SHA256.fullmatch(value):
                raise AsrError("INVALID_PROVENANCE", f"{name} must be a sha256 digest")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise AsrError("INVALID_PROFILE", "requested profile is unsupported")
        if self.hardware_profile not in {"cpu", "gpu", "fixture"}:
            raise AsrError("INVALID_PROFILE", "selected hardware profile is unsupported")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "provenance.fallback_reason", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "backend_id": self.backend_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "runtime": self.runtime,
            "timeline_contract": self.timeline_contract,
            "config_hash": self.config_hash,
            "input_hash": self.input_hash,
            "requested_profile": self.requested_profile,
            "hardware_profile": self.hardware_profile,
        }
        if self.fallback_reason is not None:
            result["fallback_reason"] = self.fallback_reason
        return result


@dataclass(frozen=True)
class AudioChunk:
    chunk_id: str
    index: int
    core: TimeInterval
    window: TimeInterval
    audio_ref: str | None = None
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        _text(self.chunk_id, "chunk_id", limit=128)
        _int(self.index, "chunk.index", minimum=0)
        if self.audio_ref is not None:
            _text(self.audio_ref, "chunk.audio_ref", limit=4096)
        if self.sample_rate is not None:
            _int(self.sample_rate, "chunk.sample_rate", minimum=1, maximum=U64_MAX)
        if self.core.time_base != self.window.time_base or not self.window.contains(self.core.start) or not self.window.contains(self.core.end):
            raise AsrError("INVALID_CHUNK", "decode window must contain the core interval")

    def to_dict(self, *, status: str = "completed", attempt: int = 1, error_code: str | None = None) -> dict[str, Any]:
        if status not in {"completed", "failed", "skipped"}:
            raise AsrError("INVALID_CHUNK_STATUS", "unknown chunk status")
        value: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "window_start": self.window.start.to_dict(),
            "window_end": self.window.end.to_dict(),
            "core_start": self.core.start.to_dict(),
            "core_end": self.core.end.to_dict(),
            "status": status,
            "attempt": attempt,
        }
        if error_code is not None:
            value["error_code"] = error_code
        return value


class ChunkPlanner:
    """Plan bounded overlapping decode windows with deterministic IDs."""

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config

    def plan(
        self,
        duration: TimeInterval,
        *,
        input_hash: str,
        audio_ref: str | None = None,
        sample_rate: int | None = None,
    ) -> tuple[AudioChunk, ...]:
        if not _SHA256.fullmatch(input_hash):
            raise AsrError("INVALID_PROVENANCE", "input_hash must be a sha256 digest")
        chunks: list[AudioChunk] = []
        cursor = duration.start.ticks
        final = duration.end.ticks
        base = duration.time_base
        index = 0
        while cursor < final:
            core_end = min(final, cursor + self.config.chunk_ticks)
            window_start = max(duration.start.ticks, cursor - self.config.overlap_ticks)
            window_end = min(final, core_end + self.config.overlap_ticks)
            core = TimeInterval(TimePoint(cursor, base), TimePoint(core_end, base))
            window = TimeInterval(TimePoint(window_start, base), TimePoint(window_end, base))
            raw_id = (
                f"{input_hash}|{self.config.to_hash()}|{audio_ref or ''}|{sample_rate or ''}|"
                f"{base.numerator}/{base.denominator}|{cursor}:{core_end}"
            )
            chunk_id = "chunk-" + sha256(raw_id.encode("ascii")).hexdigest()[:24]
            chunks.append(AudioChunk(chunk_id, index, core, window, audio_ref, sample_rate))
            index += 1
            if core_end == final:
                break
            # Core intervals own each source tick exactly once.  Only the
            # decode window is expanded backward/forward for context.
            cursor = core_end
        return tuple(chunks)


@dataclass(frozen=True)
class Failure:
    code: str
    chunk_id: str
    retryable: bool
    attempt: int
    condition: str
    fallback_used: bool

    def __post_init__(self) -> None:
        _text(self.code, "failure.code", limit=128)
        _text(self.chunk_id, "failure.chunk_id", limit=128)
        _int(self.attempt, "failure.attempt", minimum=1, maximum=255)
        if type(self.retryable) is not bool or type(self.fallback_used) is not bool:
            raise AsrError("INVALID_FAILURE", "failure flags must be boolean")
        object.__setattr__(self, "condition", _safe_condition(self.condition))

    @classmethod
    def from_error(cls, error: AsrError, *, chunk_id: str, fallback_used: bool | None = None) -> "Failure":
        return cls(
            error.code,
            chunk_id,
            error.retryable,
            error.attempt,
            error.condition,
            error.fallback_used if fallback_used is None else fallback_used,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "chunk_id": self.chunk_id,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "condition": self.condition,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class SuppressedCandidate:
    candidate_id: str
    chunk_id: str
    raw_text: str
    start: TimePoint
    end: TimePoint
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "chunk_id": self.chunk_id,
            "raw_text": self.raw_text,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HallucinationPolicy:
    max_words_per_second: int = 12
    max_repeated_token_run: int = 6
    max_word_length: int = 256

    def __post_init__(self) -> None:
        _int(self.max_words_per_second, "max_words_per_second", minimum=1, maximum=1000)
        _int(self.max_repeated_token_run, "max_repeated_token_run", minimum=2, maximum=100)
        _int(self.max_word_length, "max_word_length", minimum=1, maximum=4096)

    def reason(self, utterance: RawUtterance) -> str | None:
        duration = utterance.end.ticks - utterance.start.ticks
        base = utterance.start.time_base
        if len(utterance.words) * base.denominator > self.max_words_per_second * duration * base.numerator:
            return "word rate exceeds hallucination guard"
        previous: str | None = None
        repeated = 0
        for word in utterance.words:
            normalized = _normalize_word(word.text).casefold()
            if len(normalized) > self.max_word_length:
                return "word exceeds hallucination guard length"
            if normalized and normalized == previous:
                repeated += 1
            else:
                repeated = 1
                previous = normalized
            if repeated >= self.max_repeated_token_run:
                return "repeated token run exceeds hallucination guard"
        return None


@dataclass(frozen=True)
class Word:
    word_id: str
    raw_text: str
    text: str
    start: TimePoint
    end: TimePoint
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_id": self.word_id,
            "raw_text": self.raw_text,
            "text": self.text,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Utterance:
    utterance_id: str
    raw_text: str
    text: str
    start: TimePoint
    end: TimePoint
    language: str
    words: tuple[Word, ...]
    confidence: float
    chunk_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance_id": self.utterance_id,
            "source_kind": "asr",
            "raw_text": self.raw_text,
            "text": self.text,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "language": self.language,
            "words": [word.to_dict() for word in self.words],
            "confidence": self.confidence,
            "chunk_ids": list(self.chunk_ids),
        }


@dataclass(frozen=True)
class Transcript:
    language: str
    utterances: tuple[Utterance, ...]
    chunks: tuple[dict[str, Any], ...]
    provenance: Provenance
    vad_segments: tuple[VadSegment, ...] = ()
    failures: tuple[Failure, ...] = ()
    warnings: tuple[str, ...] = ()
    suppressed_candidates: tuple[SuppressedCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ASR_CONTRACT_VERSION,
            "kind": "asr_transcript",
            "source_kind": "asr",
            "language": self.language,
            "utterances": [utterance.to_dict() for utterance in self.utterances],
            "chunks": list(self.chunks),
            "provenance": self.provenance.to_dict(),
            "vad_segments": [
                {"start": segment.start.to_dict(), "end": segment.end.to_dict(), "confidence": segment.confidence}
                for segment in self.vad_segments
            ],
            "failures": [failure.to_dict() for failure in self.failures],
            "warnings": list(self.warnings),
            "suppressed_candidates": [item.to_dict() for item in self.suppressed_candidates],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def to_bytes(self) -> bytes:
        return (self.to_json() + "\n").encode("utf-8")


def _object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} must be an object")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} must be a decimal string")
    pattern = r"^-?(0|[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if value == "-0" or re.fullmatch(pattern, value) is None:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} is not canonical decimal")
    parsed = int(value)
    if signed:
        _int(parsed, name, minimum=I64_MIN, maximum=I64_MAX)
    else:
        _int(parsed, name, minimum=1, maximum=U64_MAX)
    return parsed


def _parse_time_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or value["schema_version"] != ASR_CONTRACT_VERSION:
        raise AsrError("INVALID_TRANSCRIPT", f"{name} discriminator/version is unsupported")
    base_value = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    base = TimeBase(
        _decimal(base_value["numerator"], f"{name}.time_base.numerator", signed=False),
        _decimal(base_value["denominator"], f"{name}.time_base.denominator", signed=False),
    )
    return TimePoint(_decimal(value["ticks"], f"{name}.ticks", signed=True), base)


def validate_transcript(value: Mapping[str, Any]) -> None:
    """Validate a v1 transcript, including cross-field timeline invariants."""

    value = _object(
        value,
        "transcript",
        {
            "schema_version", "kind", "source_kind", "language", "utterances", "chunks",
            "provenance", "vad_segments", "failures", "warnings", "suppressed_candidates",
        },
    )
    if value["schema_version"] != ASR_CONTRACT_VERSION or value["kind"] != "asr_transcript" or value["source_kind"] != "asr":
        raise AsrError("INVALID_TRANSCRIPT", "transcript discriminator/version is unsupported")
    language = _language(value["language"])
    provenance_value = _object(
        value["provenance"],
        "provenance",
        {
            "producer", "producer_version", "backend_id", "model_id", "model_version", "runtime",
            "timeline_contract", "config_hash", "input_hash", "requested_profile", "hardware_profile",
        },
        {"fallback_reason"},
    )
    Provenance(**provenance_value)
    if type(value["warnings"]) is not list or any(type(item) is not str or not item.strip() for item in value["warnings"]):
        raise AsrError("INVALID_TRANSCRIPT", "warnings must be non-empty strings")

    chunks_value = value["chunks"]
    if type(chunks_value) is not list:
        raise AsrError("INVALID_TRANSCRIPT", "chunks must be an array")
    chunks: dict[str, dict[str, Any]] = {}
    previous_core: TimeInterval | None = None
    for index, item in enumerate(chunks_value):
        item = _object(
            item,
            f"chunks[{index}]",
            {"chunk_id", "window_start", "window_end", "core_start", "core_end", "status", "attempt"},
            {"error_code", "artifact_hash", "checkpoint_id"},
        )
        chunk_id = _text(item["chunk_id"], "chunk_id", limit=128)
        if chunk_id in chunks:
            raise AsrError("INVALID_TRANSCRIPT", f"duplicate chunk id {chunk_id}")
        window = TimeInterval(_parse_time_point(item["window_start"], f"chunks[{index}].window_start"), _parse_time_point(item["window_end"], f"chunks[{index}].window_end"))
        core = TimeInterval(_parse_time_point(item["core_start"], f"chunks[{index}].core_start"), _parse_time_point(item["core_end"], f"chunks[{index}].core_end"))
        if window.time_base != core.time_base or not window.contains(core.start) or not window.contains(core.end):
            raise AsrError("INVALID_TRANSCRIPT", f"chunk {chunk_id} core is outside its decode window")
        if previous_core is not None and previous_core.end.compare(core.start) > 0:
            raise AsrError("INVALID_TRANSCRIPT", "chunk core intervals overlap or are unordered")
        _int(item["attempt"], f"chunks[{index}].attempt", minimum=1, maximum=255)
        if item["status"] not in {"completed", "failed", "skipped"}:
            raise AsrError("INVALID_TRANSCRIPT", f"chunk {chunk_id} has an invalid status")
        if item["status"] in {"completed", "skipped"}:
            if "artifact_hash" not in item or "checkpoint_id" not in item:
                raise AsrError("INVALID_TRANSCRIPT", f"chunk {chunk_id} lacks reusable artifact evidence")
            if not _SHA256.fullmatch(item["artifact_hash"]):
                raise AsrError("INVALID_TRANSCRIPT", f"chunk {chunk_id} artifact hash is invalid")
        chunks[chunk_id] = item
        previous_core = core

    for index, item in enumerate(value["vad_segments"]):
        item = _object(item, f"vad_segments[{index}]", {"start", "end", "confidence"})
        interval = TimeInterval(_parse_time_point(item["start"], f"vad_segments[{index}].start"), _parse_time_point(item["end"], f"vad_segments[{index}].end"))
        _confidence(item["confidence"], f"vad_segments[{index}].confidence")
        del interval

    for index, item in enumerate(value["failures"]):
        item = _object(item, f"failures[{index}]", {"code", "chunk_id", "retryable", "attempt", "condition", "fallback_used"})
        _text(item["code"], "failure.code", limit=128)
        _text(item["chunk_id"], "failure.chunk_id", limit=128)
        _text(item["condition"], "failure.condition", limit=4096)
        if type(item["retryable"]) is not bool or type(item["fallback_used"]) is not bool:
            raise AsrError("INVALID_TRANSCRIPT", "failure booleans are invalid")
        _int(item["attempt"], "failure.attempt", minimum=1, maximum=255)

    utterance_ids: set[str] = set()
    for index, item in enumerate(value["utterances"]):
        item = _object(
            item,
            f"utterances[{index}]",
            {"utterance_id", "source_kind", "raw_text", "text", "start", "end", "language", "words", "confidence", "chunk_ids"},
        )
        utterance_id = _text(item["utterance_id"], "utterance_id", limit=256)
        if utterance_id in utterance_ids:
            raise AsrError("INVALID_TRANSCRIPT", f"duplicate utterance id {utterance_id}")
        utterance_ids.add(utterance_id)
        if item["source_kind"] != "asr":
            raise AsrError("INVALID_TRANSCRIPT", "utterance source kind must be asr")
        _text(item["raw_text"], "utterance.raw_text", limit=8192)
        _text(item["text"], "utterance.text", limit=8192)
        utterance_language = _language(item["language"])
        if language != "und" and utterance_language != language:
            raise AsrError("INVALID_TRANSCRIPT", "utterance language differs from transcript language")
        start = _parse_time_point(item["start"], f"utterances[{index}].start")
        end = _parse_time_point(item["end"], f"utterances[{index}].end")
        utterance_interval = TimeInterval(start, end)
        _confidence(item["confidence"], f"utterances[{index}].confidence")
        if type(item["chunk_ids"]) is not list or not item["chunk_ids"] or len(set(item["chunk_ids"])) != len(item["chunk_ids"]):
            raise AsrError("INVALID_TRANSCRIPT", f"utterance {utterance_id} chunk ownership is invalid")
        if any(chunk_id not in chunks for chunk_id in item["chunk_ids"]):
            raise AsrError("INVALID_TRANSCRIPT", f"utterance {utterance_id} references an unknown chunk")
        previous_word_end: TimePoint | None = None
        word_ids: set[str] = set()
        if type(item["words"]) is not list or not item["words"]:
            raise AsrError("INVALID_TRANSCRIPT", f"utterance {utterance_id} needs words")
        for word_index, word in enumerate(item["words"]):
            word = _object(word, f"utterances[{index}].words[{word_index}]", {"word_id", "raw_text", "text", "start", "end", "confidence"})
            word_id = _text(word["word_id"], "word_id", limit=256)
            if word_id in word_ids:
                raise AsrError("INVALID_TRANSCRIPT", f"duplicate word id {word_id}")
            word_ids.add(word_id)
            _text(word["raw_text"], "word.raw_text", limit=512)
            _text(word["text"], "word.text", limit=512)
            word_start = _parse_time_point(word["start"], "word.start")
            word_end = _parse_time_point(word["end"], "word.end")
            word_interval = TimeInterval(word_start, word_end)
            if word_interval.time_base != utterance_interval.time_base or not utterance_interval.contains(word_start) or not utterance_interval.contains(word_end):
                raise AsrError("INVALID_TRANSCRIPT", f"word {word_id} falls outside utterance")
            if previous_word_end is not None and previous_word_end.compare(word_start) > 0:
                raise AsrError("INVALID_TRANSCRIPT", f"word {word_id} is out of order")
            _confidence(word["confidence"], f"word[{word_id}].confidence")
            previous_word_end = word_end

    for index, item in enumerate(value["suppressed_candidates"]):
        item = _object(item, f"suppressed_candidates[{index}]", {"candidate_id", "chunk_id", "raw_text", "start", "end", "confidence", "reason"})
        _text(item["candidate_id"], "candidate_id", limit=256)
        if item["chunk_id"] not in chunks:
            raise AsrError("INVALID_TRANSCRIPT", "suppressed candidate references an unknown chunk")
        _text(item["raw_text"], "candidate.raw_text", limit=8192)
        TimeInterval(_parse_time_point(item["start"], "candidate.start"), _parse_time_point(item["end"], "candidate.end"))
        _confidence(item["confidence"], "candidate.confidence")
        _text(item["reason"], "candidate.reason", limit=4096)


def parse_transcript_json(text: str | bytes) -> dict[str, Any]:
    """Parse JSON with duplicate/constant rejection and validate its wire shape."""

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise AsrError("DUPLICATE_FIELD", f"duplicate JSON member {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise AsrError("NON_FINITE_NUMBER", f"JSON constant {value!r} is not allowed")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except AsrError:
        raise
    except (TypeError, ValueError) as error:
        raise AsrError("MALFORMED_JSON", str(error)) from error
    validate_transcript(value)
    return value


@dataclass(frozen=True)
class ChunkCheckpoint:
    chunk_id: str
    artifact_hash: str
    result: BackendResult

    def __post_init__(self) -> None:
        _text(self.chunk_id, "checkpoint.chunk_id", limit=128)
        if not _SHA256.fullmatch(self.artifact_hash):
            raise AsrError("INVALID_CHECKPOINT", "checkpoint artifact hash must be sha256")


def _normalize_word(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    return " ".join(value.split())


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    return " ".join(value.split())


def _word_key(word: RawWord) -> tuple[str, int, int, TimeBase]:
    return (_normalize_word(word.text).casefold(), word.start.ticks, word.end.ticks, word.start.time_base)


def _interval_order(left: TimeInterval, right: TimeInterval) -> int:
    return left.start.compare(right.start) or left.end.compare(right.end)


def _make_input_hash(source: str | bytes | Path) -> str:
    if isinstance(source, bytes):
        payload = source
    elif isinstance(source, Path):
        payload = source.read_bytes()
    else:
        payload = source.encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def map_sample_interval(
    anchor: TimePoint,
    start_sample: int,
    end_sample: int,
    sample_rate: int,
) -> TimeInterval:
    """Map sample ticks into the anchor's canonical base without floats.

    The start is rounded toward negative infinity and the end toward positive
    infinity so the resulting half-open interval contains all source samples.
    A collapsed interval is rejected rather than inventing a timestamp.
    """

    _int(start_sample, "start_sample", minimum=0, maximum=U64_MAX)
    _int(end_sample, "end_sample", minimum=1, maximum=U64_MAX)
    _int(sample_rate, "sample_rate", minimum=1, maximum=U64_MAX)
    if end_sample <= start_sample:
        raise AsrError("INVALID_SAMPLE_INTERVAL", "sample interval must be positive")
    denominator = sample_rate * anchor.time_base.numerator

    def floor_positive(value: int) -> int:
        return value // denominator

    def ceil_positive(value: int) -> int:
        return (value + denominator - 1) // denominator

    start_offset = floor_positive(start_sample * anchor.time_base.denominator)
    end_offset = ceil_positive(end_sample * anchor.time_base.denominator)
    start_ticks = anchor.ticks + start_offset
    end_ticks = anchor.ticks + end_offset
    _int(start_ticks, "mapped start ticks", minimum=I64_MIN, maximum=I64_MAX)
    _int(end_ticks, "mapped end ticks", minimum=I64_MIN, maximum=I64_MAX)
    if end_ticks <= start_ticks:
        raise AsrError("TIMING_ROUNDING_COLLAPSED", "sample endpoints collapsed in canonical time base")
    return TimeInterval(TimePoint(start_ticks, anchor.time_base), TimePoint(end_ticks, anchor.time_base))


class LocalAsrAdapter:
    """Validate backend hypotheses and return a deterministic transcript."""

    def __init__(
        self,
        backend: AsrBackend,
        *,
        config: AdapterConfig,
        provenance: Provenance,
        fallback_backend: AsrBackend | None = None,
        fallback_profile: str = "cpu",
        fallback_provenance: Provenance | None = None,
        hallucination_policy: HallucinationPolicy | None = None,
    ) -> None:
        self.backend = backend
        self.fallback_backend = fallback_backend
        self.config = config
        self.provenance = provenance
        if fallback_profile not in {"cpu", "gpu", "fixture"}:
            raise AsrError("INVALID_PROFILE", "fallback profile is unsupported")
        self.fallback_profile = fallback_profile
        if fallback_provenance is not None:
            if fallback_provenance.input_hash != provenance.input_hash or fallback_provenance.config_hash != provenance.config_hash:
                raise AsrError("PROVENANCE_FALLBACK_MISMATCH", "fallback provenance must use the same input and config hash")
        self.fallback_provenance = fallback_provenance
        self.policy = hallucination_policy or HallucinationPolicy()

    def transcribe(
        self,
        duration: TimeInterval,
        *,
        input_hash: str,
        checkpoints: Mapping[str, ChunkCheckpoint] | None = None,
        audio_ref: str | None = None,
        sample_rate: int | None = None,
    ) -> Transcript:
        if input_hash != self.provenance.input_hash:
            raise AsrError("PROVENANCE_INPUT_MISMATCH", "transcribe input hash differs from provenance")
        if self.config.to_hash() != self.provenance.config_hash:
            raise AsrError("PROVENANCE_CONFIG_MISMATCH", "adapter config differs from provenance")
        planner = ChunkPlanner(self.config)
        chunks = planner.plan(duration, input_hash=input_hash, audio_ref=audio_ref, sample_rate=sample_rate)
        all_candidates: list[tuple[RawUtterance, AudioChunk]] = []
        vad: list[VadSegment] = []
        failures: list[Failure] = []
        chunk_records: list[dict[str, Any]] = []
        warnings: list[str] = []
        suppressed: list[SuppressedCandidate] = []
        successful = 0
        selected_provenance = self.provenance

        for chunk in chunks:
            result: BackendResult | None = None
            status = "completed"
            attempts = 0
            used_fallback = False
            checkpoint = checkpoints.get(chunk.chunk_id) if checkpoints else None
            if (
                checkpoint is not None
                and checkpoint.chunk_id == chunk.chunk_id
                and checkpoint.artifact_hash == self._chunk_artifact_hash(chunk, input_hash, checkpoint.result)
            ):
                result = checkpoint.result
                status = "skipped"
                attempts = 1
            else:
                result, backend_failures, attempts = self._invoke_backend(self.backend, chunk)
                failures.extend(backend_failures)
                if result is None and self.fallback_backend is not None:
                    used_fallback = True
                    result, fallback_failures, fallback_attempts = self._invoke_backend(self.fallback_backend, chunk)
                    attempts = max(attempts, fallback_attempts)
                    failures.extend(fallback_failures)
                    if result is not None and backend_failures:
                        selected_provenance = replace(
                            self.fallback_provenance or self.provenance,
                            backend_id=(self.fallback_provenance.backend_id if self.fallback_provenance else "fallback-cpu"),
                            model_id=(self.fallback_provenance.model_id if self.fallback_provenance else "fallback-cpu"),
                            model_version=(self.fallback_provenance.model_version if self.fallback_provenance else "1"),
                            hardware_profile=(self.fallback_provenance.hardware_profile if self.fallback_provenance else self.fallback_profile),
                            fallback_reason="primary backend failed; bounded fallback selected",
                        )
                        failures[-len(fallback_failures) - len(backend_failures) : -len(fallback_failures) if fallback_failures else None] = [
                            replace(item, fallback_used=True) for item in backend_failures
                        ]
                if result is None:
                    status = "failed"

            if result is not None:
                try:
                    self._validate_backend_result(result, chunk)
                    for item in result.utterances:
                        if not self._intersects_core(item, chunk.core):
                            continue
                        reason = self.policy.reason(item)
                        if reason is not None:
                            suppressed.append(
                                SuppressedCandidate(
                                    item.utterance_id,
                                    chunk.chunk_id,
                                    item.text,
                                    item.start,
                                    item.end,
                                    item.confidence,
                                    reason,
                                )
                            )
                            warnings.append(f"suppressed {item.utterance_id}: {reason}")
                            continue
                        all_candidates.append((item, chunk))
                    vad.extend(result.vad_segments)
                    successful += 1
                except AsrError as error:
                    failure = Failure.from_error(error, chunk_id=chunk.chunk_id, fallback_used=used_fallback)
                    failures.append(failure)
                    status = "failed"
                    result = None
                except Exception as error:
                    failure = AsrError("MALFORMED_BACKEND_RESULT", str(error), chunk_id=chunk.chunk_id)
                    failures.append(Failure.from_error(failure, chunk_id=chunk.chunk_id, fallback_used=used_fallback))
                    status = "failed"
                    result = None
            chunk_record = chunk.to_dict(
                status=status,
                attempt=max(1, attempts),
                error_code=failures[-1].code if status == "failed" and failures else None,
            )
            if status in {"completed", "skipped"} and result is not None:
                chunk_record["artifact_hash"] = self._chunk_artifact_hash(chunk, input_hash, result)
                chunk_record["checkpoint_id"] = f"{chunk.chunk_id}:safe"
            chunk_records.append(chunk_record)

        if successful == 0:
            transcript = Transcript(
                language="und",
                utterances=(),
                chunks=tuple(chunk_records),
                provenance=self.provenance,
                vad_segments=tuple(self._merge_vad(vad)),
                failures=tuple(failures),
                warnings=tuple(warnings),
                suppressed_candidates=tuple(suppressed),
            )
            validate_transcript(transcript.to_dict())
            raise AsrStageError(transcript)

        if failures:
            warnings.append("transcript is degraded: one or more chunks failed or used fallback")
        utterances = self._merge_utterances(all_candidates)
        languages = {utterance.language for utterance in utterances}
        language = next(iter(languages)) if len(languages) == 1 else "und"
        transcript = Transcript(
            language=language,
            utterances=tuple(utterances),
            chunks=tuple(chunk_records),
            provenance=selected_provenance,
            vad_segments=tuple(self._merge_vad(vad)),
            failures=tuple(failures),
            warnings=tuple(dict.fromkeys(warnings)),
            suppressed_candidates=tuple(suppressed),
        )
        validate_transcript(transcript.to_dict())
        return transcript

    def _invoke_backend(
        self,
        backend: AsrBackend,
        chunk: AudioChunk,
    ) -> tuple[BackendResult | None, list[Failure], int]:
        """Run a backend with a bounded retry budget and changed conditions."""

        failures: list[Failure] = []
        previous_condition: str | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = backend.transcribe(chunk)
                if not isinstance(result, BackendResult):
                    failures.append(
                        Failure(
                            "MALFORMED_BACKEND_RESULT",
                            chunk.chunk_id,
                            False,
                            attempt,
                            "backend did not return BackendResult",
                            False,
                        )
                    )
                    return None, failures, attempt
                return result, failures, attempt
            except AsrError as error:
                condition = error.condition
                failures.append(
                    Failure(
                        error.code,
                        chunk.chunk_id,
                        error.retryable,
                        attempt,
                        condition,
                        False,
                    )
                )
                if not error.retryable or attempt >= self.config.max_attempts:
                    return None, failures, attempt
                if previous_condition == condition:
                    failures.append(
                        Failure(
                            "RETRY_CONDITION_UNCHANGED",
                            chunk.chunk_id,
                            False,
                            attempt,
                            "retry condition did not materially change",
                            False,
                        )
                    )
                    return None, failures, attempt
                previous_condition = condition
            except Exception as error:  # backend boundary must never leak an opaque traceback
                condition = str(error) or "backend raised an unknown exception"
                failures.append(
                    Failure("ASR_BACKEND_FAILED", chunk.chunk_id, True, attempt, condition, False)
                )
                if attempt >= self.config.max_attempts or previous_condition == condition:
                    return None, failures, attempt
                previous_condition = condition
        return None, failures, self.config.max_attempts

    @staticmethod
    def _backend_result_dict(result: BackendResult | None) -> dict[str, Any]:
        if result is None:
            return {"status": "failed"}
        return {
            "utterances": [
                {
                    "utterance_id": item.utterance_id,
                    "start": item.start.to_dict(),
                    "end": item.end.to_dict(),
                    "language": item.language,
                    "text": item.text,
                    "confidence": item.confidence,
                    "words": [
                        {
                            "word_id": word.word_id,
                            "text": word.text,
                            "start": word.start.to_dict(),
                            "end": word.end.to_dict(),
                            "confidence": word.confidence,
                        }
                        for word in item.words
                    ],
                }
                for item in result.utterances
            ],
            "vad_segments": [
                {
                    "start": item.start.to_dict(),
                    "end": item.end.to_dict(),
                    "confidence": item.confidence,
                }
                for item in result.vad_segments
            ],
        }

    def _chunk_artifact_hash(self, chunk: AudioChunk, input_hash: str, result: BackendResult | None = None) -> str:
        payload = {
            "input_hash": input_hash,
            "config_hash": self.config.to_hash(),
            "chunk_id": chunk.chunk_id,
            "audio_ref": chunk.audio_ref,
            "sample_rate": chunk.sample_rate,
            "result": self._backend_result_dict(result),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + sha256(encoded).hexdigest()

    def _validate_backend_result(self, result: BackendResult, chunk: AudioChunk) -> None:
        if not isinstance(result, BackendResult):
            raise AsrError("MALFORMED_BACKEND_RESULT", "backend returned an unexpected result type")
        previous: RawUtterance | None = None
        for utterance in result.utterances:
            self._validate_utterance(utterance, chunk)
            if previous is not None and previous.start.compare(utterance.start) > 0:
                raise AsrError("NON_MONOTONE_TIMESTAMPS", "backend utterances are not ordered")
            previous = utterance
        for segment in result.vad_segments:
            if segment.start.time_base != chunk.window.time_base or not chunk.window.contains(segment.start) or not chunk.window.contains(segment.end):
                raise AsrError("TIMESTAMP_OUT_OF_RANGE", "VAD segment falls outside decode window")

    def _validate_utterance(self, utterance: RawUtterance, chunk: AudioChunk) -> None:
        _text(utterance.utterance_id, "utterance_id", limit=256)
        _language(utterance.language)
        _text(utterance.text, "utterance.text", limit=8192)
        _confidence(utterance.confidence, "utterance.confidence")
        if not utterance.words:
            raise AsrError("EMPTY_UTTERANCE", "backend utterance must contain at least one word")
        if utterance.start.time_base != chunk.window.time_base or utterance.end.time_base != chunk.window.time_base:
            raise AsrError("TIMESTAMP_TIME_BASE_MISMATCH", "backend timestamp uses a different time base")
        interval = TimeInterval(utterance.start, utterance.end)
        if not chunk.window.contains(utterance.start) or not chunk.window.contains(utterance.end):
            raise AsrError("TIMESTAMP_OUT_OF_RANGE", "utterance falls outside decode window")
        previous: RawWord | None = None
        for word in utterance.words:
            _text(word.word_id, "word_id", limit=256)
            _text(word.text, "word.text", limit=512)
            _confidence(word.confidence, "word.confidence")
            if word.start.time_base != interval.time_base or word.end.time_base != interval.time_base:
                raise AsrError("TIMESTAMP_TIME_BASE_MISMATCH", "word timestamp uses a different time base")
            word_interval = TimeInterval(word.start, word.end)
            if not interval.contains(word.start) or not interval.contains(word.end):
                raise AsrError("TIMESTAMP_OUT_OF_RANGE", "word falls outside utterance")
            if previous is not None and previous.end.compare(word.start) > 0:
                raise AsrError("NON_MONOTONE_TIMESTAMPS", "word timestamps overlap out of order")
            previous = word

    @staticmethod
    def _intersects_core(utterance: RawUtterance, core: TimeInterval) -> bool:
        return TimeInterval(utterance.start, utterance.end).intersects(core)

    def _merge_utterances(self, candidates: Sequence[tuple[RawUtterance, AudioChunk]]) -> list[Utterance]:
        groups: list[dict[str, Any]] = []
        for raw, chunk in candidates:
            candidate_words = tuple(raw.words)
            matching: dict[str, Any] | None = None
            for group in groups:
                if group["language"] != raw.language:
                    continue
                group_interval: TimeInterval = group["interval"]
                raw_interval = TimeInterval(raw.start, raw.end)
                if not group_interval.intersects(raw_interval):
                    continue
                shared = {_word_key(word) for word in group["raw_words"]} & {_word_key(word) for word in candidate_words}
                same_text = _normalize_text(group["raw_text"]).casefold() == _normalize_text(raw.text).casefold()
                matching_word_overlap = any(
                    _normalize_word(left.text).casefold() == _normalize_word(right.text).casefold()
                    and TimeInterval(left.start, left.end).intersects(TimeInterval(right.start, right.end))
                    for left in group["raw_words"]
                    for right in candidate_words
                )
                exact_span = group_interval.start.compare(raw.start) == 0 and group_interval.end.compare(raw.end) == 0
                cross_chunk = chunk.chunk_id not in group["chunks"]
                windows_overlap = any(window.intersects(chunk.window) for window in group["windows"])
                if cross_chunk and windows_overlap and (shared or matching_word_overlap or (same_text and exact_span)):
                    matching = group
                    break
            if matching is None:
                groups.append(
                    {
                        "raw_text": raw.text,
                        "language": raw.language,
                        "interval": TimeInterval(raw.start, raw.end),
                        "raw_words": list(candidate_words),
                        "confidence": raw.confidence,
                        "ids": [raw.utterance_id],
                        "chunks": [chunk.chunk_id],
                        "windows": [chunk.window],
                    }
                )
            else:
                matching["interval"] = TimeInterval(
                    matching["interval"].start if matching["interval"].start.compare(raw.start) <= 0 else raw.start,
                    matching["interval"].end if matching["interval"].end.compare(raw.end) >= 0 else raw.end,
                )
                keys = {_word_key(word) for word in matching["raw_words"]}
                for word in candidate_words:
                    if _word_key(word) in keys:
                        continue
                    duplicate = any(
                        _normalize_word(existing.text).casefold() == _normalize_word(word.text).casefold()
                        and TimeInterval(existing.start, existing.end).intersects(TimeInterval(word.start, word.end))
                        for existing in matching["raw_words"]
                    )
                    if not duplicate:
                        matching["raw_words"].append(word)
                        keys.add(_word_key(word))
                matching["raw_words"].sort(key=lambda word: (word.start.ticks, word.end.ticks, _normalize_word(word.text).casefold()))
                matching["confidence"] = max(matching["confidence"], raw.confidence)
                matching["ids"].append(raw.utterance_id)
                matching["chunks"].append(chunk.chunk_id)
                matching["windows"].append(chunk.window)

        output: list[Utterance] = []
        used_ids: set[str] = set()
        for group in sorted(groups, key=lambda item: (item["interval"].start.ticks, item["interval"].end.ticks, item["language"])):
            raw_words: list[RawWord] = group["raw_words"]
            words = tuple(
                Word(
                    word.word_id,
                    word.text,
                    _normalize_word(word.text),
                    word.start,
                    word.end,
                    word.confidence,
                )
                for word in raw_words
            )
            base_id = min(group["ids"])
            if base_id in used_ids:
                base_id = "utt-" + sha256(
                    f"{group['language']}|{group['interval'].start.ticks}|{group['interval'].end.ticks}|{group['raw_text']}".encode("utf-8")
                ).hexdigest()[:24]
            used_ids.add(base_id)
            normalized_text = " ".join(word.text for word in words)
            if not normalized_text:
                normalized_text = _normalize_text(group["raw_text"])
            output.append(
                Utterance(
                    base_id,
                    group["raw_text"],
                    normalized_text,
                    group["interval"].start,
                    group["interval"].end,
                    group["language"],
                    words,
                    group["confidence"],
                    tuple(sorted(set(group["chunks"]))),
                )
            )
        return output

    @staticmethod
    def _merge_vad(segments: Sequence[VadSegment]) -> list[VadSegment]:
        result: list[VadSegment] = []
        for segment in sorted(segments, key=lambda item: (item.start.ticks, item.end.ticks)):
            if not result:
                result.append(segment)
                continue
            previous = result[-1]
            if previous.start.time_base == segment.start.time_base and previous.end.compare(segment.start) >= 0:
                end = previous.end if previous.end.compare(segment.end) >= 0 else segment.end
                result[-1] = VadSegment(previous.start, end, max(previous.confidence, segment.confidence))
            else:
                result.append(segment)
        return result


class DeterministicFixtureBackend:
    """Small CPU-safe backend used by tests and offline contract demos."""

    def __init__(
        self,
        utterances: Sequence[RawUtterance],
        *,
        vad_segments: Sequence[VadSegment] | None = None,
        failures: Mapping[str, AsrBackendError] | None = None,
    ) -> None:
        self.utterances = tuple(utterances)
        self.vad_segments = tuple(vad_segments or ())
        self.failures = dict(failures or {})
        self.calls: list[str] = []

    def transcribe(self, chunk: AudioChunk) -> BackendResult:
        self.calls.append(chunk.chunk_id)
        failure = self.failures.get(chunk.chunk_id)
        if failure is not None:
            raise failure
        utterances = tuple(
            sorted(
                (
                    item
                    for item in self.utterances
                    if chunk.window.contains(item.start) and chunk.window.contains(item.end)
                ),
                key=lambda item: (item.start.ticks, item.end.ticks, item.utterance_id),
            )
        )
        vad = tuple(
            item
            for item in self.vad_segments
            if chunk.window.contains(item.start) and chunk.window.contains(item.end)
        )
        return BackendResult(utterances, vad)

    @classmethod
    def from_json(cls, path: Path) -> "DeterministicFixtureBackend":
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != ASR_CONTRACT_VERSION or value.get("kind") != "asr_fixture":
            raise AsrError("UNSUPPORTED_SCHEMA", "fixture schema version or kind is unsupported")
        base = TimeBase(int(value["time_base"]["numerator"]), int(value["time_base"]["denominator"]))
        utterances: list[RawUtterance] = []
        for item in value.get("utterances", []):
            words = tuple(
                RawWord(
                    word["word_id"],
                    word["text"],
                    TimePoint(int(word["start"]), base),
                    TimePoint(int(word["end"]), base),
                    float(word["confidence"]),
                )
                for word in item["words"]
            )
            utterances.append(
                RawUtterance(
                    item["utterance_id"],
                    TimePoint(int(item["start"]), base),
                    TimePoint(int(item["end"]), base),
                    item["language"],
                    item["text"],
                    words,
                    float(item["confidence"]),
                )
            )
        return cls(utterances)
