"""Versioned, dependency-free local translation adapter.

The adapter consumes canonical ASR utterances and emits Vietnamese translation
segments without changing source identity or timeline boundaries. Model
runtimes are supplied through a narrow backend protocol; the standard fixture
backend is deterministic and requires no network, credentials or model files.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping, Protocol, Sequence
import unicodedata

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint


TRANSLATION_CONTRACT_VERSION = 1
TARGET_LANGUAGE = "vi"
_LANGUAGE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class TranslationError(ValueError):
    """Stable, serializable failure at the translation/backend boundary."""

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
            raise ValueError("translation failures require a code and condition")
        if type(retryable) is not bool or type(attempt) is not int or not 1 <= attempt <= 255:
            raise ValueError("invalid translation failure metadata")
        if type(fallback_used) is not bool:
            raise ValueError("fallback_used must be boolean")
        self.code = code
        self.condition = _safe_condition(condition)
        self.retryable = retryable
        self.chunk_id = chunk_id
        self.attempt = attempt
        self.fallback_used = fallback_used
        super().__init__(f"{code}: {self.condition}")


class TranslationBackendError(TranslationError):
    """A backend-reported failure that can be retried or sent to fallback."""


class TranslationStageError(TranslationError):
    """Raised when every planned translation chunk failed."""

    def __init__(self, document: "TranslationDocument") -> None:
        self.document = document
        super().__init__(
            "TRANSLATION_FAILED",
            "every planned translation chunk failed",
            retryable=any(item.retryable for item in document.failures),
        )


def _safe_condition(value: Any) -> str:
    text = str(value) or "translation backend returned an empty condition"
    text = _CONTROL.sub(" ", text).strip()
    return text[:4096] or "translation backend returned an empty condition"


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise TranslationError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _language(value: Any, name: str = "language") -> str:
    value = _text(value, name, limit=32)
    if _LANGUAGE.fullmatch(value) is None:
        raise TranslationError("INVALID_LANGUAGE", f"unsupported language tag {value!r}")
    return value


def _canonical_source_language(value: Any, name: str = "source_language") -> str:
    """Normalize only the source tags whose routing semantics are defined by #190."""

    if value == "auto":
        return "auto"
    language = _language(value, name)
    lowered = language.lower()
    if lowered == "zh-cn":
        return "zh-CN"
    if lowered == "zh-tw":
        return "zh-TW"
    if lowered in {"zh", "en", "und"}:
        return lowered
    return language


def resolve_source_language(
    sources: Sequence["SourceSegment"],
    *,
    requested_source_language: str = "auto",
) -> str:
    """Resolve exactly one concrete source language before backend selection."""

    requested = _canonical_source_language(requested_source_language, "requested_source_language")
    observed = {
        _canonical_source_language(item.source_language, "source.source_language")
        for item in sources
    }
    if not observed:
        raise TranslationError("EMPTY_SOURCE", "translation input has no utterances")

    if requested != "auto":
        if requested == "und":
            raise TranslationError(
                "SOURCE_LANGUAGE_UNRESOLVED",
                "explicit source language must not be undetermined",
            )
        conflicts = sorted(item for item in observed if item not in {"und", requested})
        if conflicts:
            raise TranslationError(
                "SOURCE_LANGUAGE_MISMATCH",
                f"requested source language {requested} conflicts with observed languages {conflicts}",
            )
        return requested

    if "und" in observed:
        raise TranslationError(
            "SOURCE_LANGUAGE_UNRESOLVED",
            "automatic source-language routing requires concrete language evidence",
        )
    if len(observed) != 1:
        raise TranslationError(
            "SOURCE_LANGUAGE_AMBIGUOUS",
            f"automatic source-language routing observed multiple languages {sorted(observed)}",
        )
    return next(iter(observed))


def _confidence(value: Any, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise TranslationError("INVALID_CONFIDENCE", f"{name} must be finite")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise TranslationError("INVALID_CONFIDENCE", f"{name} must be between 0 and 1")
    return result


def _integer(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise TranslationError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise TranslationError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise TranslationError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _hash_mapping(value: Mapping[str, str]) -> str:
    return _hash_json({key: value[key] for key in sorted(value)})


def _ensure_hash(value: Any, name: str) -> str:
    value = _text(value, name, limit=80)
    if _SHA256.fullmatch(value) is None:
        raise TranslationError("INVALID_PROVENANCE", f"{name} must be a sha256 digest")
    return value


@dataclass(frozen=True)
class SourceSegment:
    """One immutable source utterance accepted by the translation adapter."""

    utterance_id: str
    source_text: str
    start: TimePoint
    end: TimePoint
    source_language: str
    confidence: float = 1.0
    normalized_text: str | None = None

    def __post_init__(self) -> None:
        _text(self.utterance_id, "source.utterance_id", limit=256)
        _text(self.source_text, "source.source_text", limit=8192)
        _language(self.source_language, "source.source_language")
        _confidence(self.confidence, "source.confidence")
        if not isinstance(self.start, TimePoint) or not isinstance(self.end, TimePoint):
            raise TranslationError("INVALID_TIMELINE", "source boundaries must be canonical time points")
        if self.start.time_base != self.end.time_base:
            raise TranslationError("TIMELINE_TIME_BASE_MISMATCH", "source boundaries use different time bases")
        try:
            TimeInterval(self.start, self.end)
        except Exception as error:
            raise TranslationError("INVALID_TIMELINE", str(error)) from error
        if self.normalized_text is not None:
            _text(self.normalized_text, "source.normalized_text", limit=8192)
        normalized = _normalize_text(self.source_text if self.normalized_text is None else self.normalized_text)
        if not normalized:
            raise TranslationError("INVALID_TEXT", "source.normalized_text must not be empty")
        object.__setattr__(self, "normalized_text", normalized)

    @classmethod
    def from_asr(cls, utterance: Any) -> "SourceSegment":
        """Adapt the merged ASR ``Utterance`` without importing model code."""

        try:
            return cls(
                utterance_id=utterance.utterance_id,
                source_text=utterance.raw_text,
                normalized_text=utterance.text,
                start=utterance.start,
                end=utterance.end,
                source_language=utterance.language,
                confidence=utterance.confidence,
            )
        except AttributeError as error:
            raise TranslationError("INVALID_SOURCE", "source does not expose the ASR utterance fields") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance_id": self.utterance_id,
            "source_text": self.source_text,
            "normalized_text": self.normalized_text,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "source_language": self.source_language,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class TranslationConfig:
    max_items_per_chunk: int = 16
    context_before: int = 1
    context_after: int = 1
    target_language: str = TARGET_LANGUAGE
    requested_profile: str = "auto"
    max_attempts: int = 2

    def __post_init__(self) -> None:
        _integer(self.max_items_per_chunk, "config.max_items_per_chunk", minimum=1, maximum=256)
        _integer(self.context_before, "config.context_before", minimum=0, maximum=32)
        _integer(self.context_after, "config.context_after", minimum=0, maximum=32)
        _integer(self.max_attempts, "config.max_attempts", minimum=1, maximum=3)
        if self.target_language != TARGET_LANGUAGE:
            raise TranslationError("UNSUPPORTED_TARGET", "translation v1 targets Vietnamese (vi)")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise TranslationError("INVALID_PROFILE", "requested profile is unsupported")

    def to_hash(self) -> str:
        return _hash_json({
            "max_items_per_chunk": self.max_items_per_chunk,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "target_language": self.target_language,
            "requested_profile": self.requested_profile,
            "max_attempts": self.max_attempts,
        })


@dataclass(frozen=True)
class TranslationProvenance:
    producer: str
    producer_version: str
    backend_id: str
    model_id: str
    model_version: str
    runtime: str
    timeline_contract: str
    config_hash: str
    model_hash: str
    glossary_hash: str
    input_hash: str
    requested_profile: str
    hardware_profile: str
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("producer", "producer_version", "backend_id", "model_id", "model_version", "runtime", "timeline_contract"):
            _text(getattr(self, name), f"provenance.{name}", limit=256)
        if self.timeline_contract != "timeline-v1":
            raise TranslationError("UNSUPPORTED_TIMELINE_CONTRACT", "translation v1 requires timeline-v1")
        _ensure_hash(self.config_hash, "provenance.config_hash")
        _ensure_hash(self.model_hash, "provenance.model_hash")
        _ensure_hash(self.glossary_hash, "provenance.glossary_hash")
        _ensure_hash(self.input_hash, "provenance.input_hash")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise TranslationError("INVALID_PROFILE", "requested profile is unsupported")
        if self.hardware_profile not in {"cpu", "gpu", "fixture", "mixed"}:
            raise TranslationError("INVALID_PROFILE", "selected hardware profile is unsupported")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "provenance.fallback_reason", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "backend_id": self.backend_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "runtime": self.runtime,
            "timeline_contract": self.timeline_contract,
            "config_hash": self.config_hash,
            "model_hash": self.model_hash,
            "glossary_hash": self.glossary_hash,
            "input_hash": self.input_hash,
            "requested_profile": self.requested_profile,
            "hardware_profile": self.hardware_profile,
        }
        if self.fallback_reason is not None:
            value["fallback_reason"] = self.fallback_reason
        return value


@dataclass(frozen=True)
class TranslationRequest:
    chunk_id: str
    core: tuple[SourceSegment, ...]
    context_before: tuple[SourceSegment, ...] = ()
    context_after: tuple[SourceSegment, ...] = ()
    target_language: str = TARGET_LANGUAGE
    glossary: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.chunk_id, "request.chunk_id", limit=128)
        if not self.core:
            raise TranslationError("INVALID_REQUEST", "translation request needs a core utterance")
        ids = [item.utterance_id for item in self.core]
        if len(ids) != len(set(ids)):
            raise TranslationError("INVALID_REQUEST", "translation request core IDs must be unique")
        if self.target_language != TARGET_LANGUAGE:
            raise TranslationError("UNSUPPORTED_TARGET", "translation v1 targets Vietnamese (vi)")

    @property
    def all_segments(self) -> tuple[SourceSegment, ...]:
        return self.context_before + self.core + self.context_after

    @property
    def context_ids(self) -> tuple[str, ...]:
        return tuple(item.utterance_id for item in self.context_before + self.context_after)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "core_ids": [item.utterance_id for item in self.core],
            "context_before_ids": [item.utterance_id for item in self.context_before],
            "context_after_ids": [item.utterance_id for item in self.context_after],
            "target_language": self.target_language,
            "glossary": list(self.glossary),
        }


@dataclass(frozen=True)
class TranslationCandidate:
    source_utterance_id: str
    translated_text: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _text(self.source_utterance_id, "candidate.source_utterance_id", limit=256)
        _text(self.translated_text, "candidate.translated_text", limit=8192)
        _confidence(self.confidence, "candidate.confidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_utterance_id": self.source_utterance_id,
            "translated_text": self.translated_text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class TranslationBackendResult:
    translations: tuple[TranslationCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"translations": [item.to_dict() for item in self.translations]}


class TranslationBackend(Protocol):
    def translate(self, request: TranslationRequest) -> TranslationBackendResult:
        ...


@dataclass(frozen=True)
class TranslationChunkPlan:
    chunk_id: str
    index: int
    core: tuple[SourceSegment, ...]
    context_before: tuple[SourceSegment, ...]
    context_after: tuple[SourceSegment, ...]

    def request(self, *, glossary: tuple[tuple[str, str], ...]) -> TranslationRequest:
        return TranslationRequest(
            self.chunk_id,
            self.core,
            self.context_before,
            self.context_after,
            TARGET_LANGUAGE,
            glossary,
        )

    def to_dict(self, *, status: str, attempt: int, error_code: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "core_utterance_ids": [item.utterance_id for item in self.core],
            "context_utterance_ids": [item.utterance_id for item in self.context_before + self.context_after],
            "status": status,
            "attempt": attempt,
        }
        if error_code is not None:
            value["error_code"] = error_code
        return value


@dataclass(frozen=True)
class TranslationCheckpoint:
    chunk_id: str
    artifact_hash: str
    result: TranslationBackendResult

    def __post_init__(self) -> None:
        _text(self.chunk_id, "checkpoint.chunk_id", limit=128)
        _ensure_hash(self.artifact_hash, "checkpoint.artifact_hash")


@dataclass(frozen=True)
class TranslationFailure:
    code: str
    chunk_id: str
    utterance_ids: tuple[str, ...]
    retryable: bool
    attempt: int
    condition: str
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.code, "failure.code", limit=128)
        _text(self.chunk_id, "failure.chunk_id", limit=128)
        if len(self.utterance_ids) != len(set(self.utterance_ids)):
            raise TranslationError("INVALID_FAILURE", "failure utterance IDs must be unique")
        for item in self.utterance_ids:
            _text(item, "failure.utterance_id", limit=256)
        if type(self.retryable) is not bool or type(self.fallback_used) is not bool:
            raise TranslationError("INVALID_FAILURE", "failure booleans are invalid")
        _integer(self.attempt, "failure.attempt", minimum=1, maximum=255)
        _text(self.condition, "failure.condition", limit=4096)

    @classmethod
    def from_error(cls, error: TranslationError, *, chunk: TranslationChunkPlan, fallback_used: bool | None = None) -> "TranslationFailure":
        return cls(
            error.code,
            chunk.chunk_id,
            tuple(item.utterance_id for item in chunk.core),
            error.retryable,
            error.attempt,
            error.condition,
            error.fallback_used if fallback_used is None else fallback_used,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "chunk_id": self.chunk_id,
            "utterance_ids": list(self.utterance_ids),
            "retryable": self.retryable,
            "attempt": self.attempt,
            "condition": self.condition,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class TranslatedSegment:
    source_utterance_id: str
    source_text: str
    source_normalized_text: str
    translated_text: str
    normalized_text: str
    source_language: str
    target_language: str
    start: TimePoint
    end: TimePoint
    confidence: float
    chunk_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_utterance_id": self.source_utterance_id,
            "source_text": self.source_text,
            "source_normalized_text": self.source_normalized_text,
            "translated_text": self.translated_text,
            "normalized_text": self.normalized_text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "confidence": self.confidence,
            "chunk_id": self.chunk_id,
        }


@dataclass(frozen=True)
class TranslationDocument:
    source_language: str
    target_language: str
    source_utterance_ids: tuple[str, ...]
    translations: tuple[TranslatedSegment, ...]
    chunks: tuple[dict[str, Any], ...]
    provenance: TranslationProvenance
    failures: tuple[TranslationFailure, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRANSLATION_CONTRACT_VERSION,
            "kind": "translation_document",
            "source_kind": "asr",
            "source_language": self.source_language,
            "target_language": self.target_language,
            "source_utterance_ids": list(self.source_utterance_ids),
            "translations": [item.to_dict() for item in self.translations],
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
        raise TranslationError("INVALID_DOCUMENT", f"{name} must be an object")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise TranslationError("INVALID_DOCUMENT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise TranslationError("INVALID_DOCUMENT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise TranslationError("INVALID_DOCUMENT", f"{name} must be a decimal string")
    pattern = r"^(0|-?[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise TranslationError("INVALID_DOCUMENT", f"{name} is not canonical decimal")
    result = int(value)
    _integer(result, name, minimum=I64_MIN if signed else 1, maximum=I64_MAX if signed else U64_MAX)
    return result


def _parse_time_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or value["schema_version"] != TRANSLATION_CONTRACT_VERSION:
        raise TranslationError("INVALID_DOCUMENT", f"{name} discriminator/version is unsupported")
    base = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    try:
        return TimePoint(
            _decimal(value["ticks"], f"{name}.ticks", signed=True),
            TimeBase(
                _decimal(base["numerator"], f"{name}.time_base.numerator", signed=False),
                _decimal(base["denominator"], f"{name}.time_base.denominator", signed=False),
            ),
        )
    except TranslationError:
        raise
    except Exception as error:
        raise TranslationError("INVALID_TIMELINE", str(error)) from error


def _interval(start: TimePoint, end: TimePoint, name: str) -> TimeInterval:
    try:
        return TimeInterval(start, end)
    except Exception as error:
        raise TranslationError("INVALID_TIMELINE", f"{name}: {error}") from error


def validate_translation_document(value: Mapping[str, Any]) -> None:
    """Validate the strict v1 wire shape and cross-field identity rules."""

    value = _object(
        value,
        "document",
        {
            "schema_version", "kind", "source_kind", "source_language", "target_language",
            "source_utterance_ids", "translations", "chunks", "provenance", "failures", "warnings",
        },
    )
    if value["schema_version"] != TRANSLATION_CONTRACT_VERSION or value["kind"] != "translation_document" or value["source_kind"] != "asr":
        raise TranslationError("INVALID_DOCUMENT", "document discriminator/version is unsupported")
    source_language = _language(value["source_language"], "source_language")
    if value["target_language"] != TARGET_LANGUAGE:
        raise TranslationError("INVALID_DOCUMENT", "target_language must be vi")

    source_ids_value = value["source_utterance_ids"]
    if type(source_ids_value) is not list or not source_ids_value:
        raise TranslationError("INVALID_DOCUMENT", "source_utterance_ids must be a non-empty array")
    source_ids: list[str] = []
    for item in source_ids_value:
        source_ids.append(_text(item, "source_utterance_id", limit=256))
    if len(source_ids) != len(set(source_ids)):
        raise TranslationError("INVALID_DOCUMENT", "source_utterance_ids must be unique")
    source_id_set = set(source_ids)

    provenance_value = _object(
        value["provenance"],
        "provenance",
        {
            "producer", "producer_version", "backend_id", "model_id", "model_version", "runtime",
            "timeline_contract", "config_hash", "model_hash", "glossary_hash", "input_hash",
            "requested_profile", "hardware_profile",
        },
        {"fallback_reason"},
    )
    TranslationProvenance(**provenance_value)

    chunks_value = value["chunks"]
    if type(chunks_value) is not list or not chunks_value:
        raise TranslationError("INVALID_DOCUMENT", "chunks must be a non-empty array")
    chunks: dict[str, dict[str, Any]] = {}
    claimed_core_ids: set[str] = set()
    for position, raw_item in enumerate(chunks_value):
        item = _object(
            raw_item,
            f"chunks[{position}]",
            {"chunk_id", "index", "core_utterance_ids", "context_utterance_ids", "status", "attempt"},
            {"error_code", "artifact_hash", "checkpoint_id"},
        )
        chunk_id = _text(item["chunk_id"], "chunk.chunk_id", limit=128)
        if chunk_id in chunks:
            raise TranslationError("INVALID_DOCUMENT", f"duplicate chunk id {chunk_id}")
        if _integer(item["index"], "chunk.index", minimum=0) != position:
            raise TranslationError("INVALID_DOCUMENT", "chunks must be ordered by contiguous index")
        core = item["core_utterance_ids"]
        context = item["context_utterance_ids"]
        if type(core) is not list or not core or len(core) != len(set(core)):
            raise TranslationError("INVALID_DOCUMENT", f"chunk {chunk_id} core IDs are invalid")
        if type(context) is not list or len(context) != len(set(context)):
            raise TranslationError("INVALID_DOCUMENT", f"chunk {chunk_id} context IDs are invalid")
        core_ids = {_text(item, "chunk.core_utterance_id", limit=256) for item in core}
        context_ids = {_text(item, "chunk.context_utterance_id", limit=256) for item in context}
        if not core_ids <= source_id_set or not context_ids <= source_id_set or core_ids & context_ids:
            raise TranslationError("INVALID_DOCUMENT", f"chunk {chunk_id} references invalid source IDs")
        if claimed_core_ids & core_ids:
            raise TranslationError("INVALID_DOCUMENT", f"source IDs are assigned to multiple core chunks: {sorted(claimed_core_ids & core_ids)}")
        claimed_core_ids.update(core_ids)
        status = item["status"]
        if status not in {"completed", "failed", "skipped"}:
            raise TranslationError("INVALID_DOCUMENT", f"chunk {chunk_id} status is invalid")
        _integer(item["attempt"], "chunk.attempt", minimum=1, maximum=255)
        if status in {"completed", "skipped"}:
            if "error_code" in item or "artifact_hash" not in item or "checkpoint_id" not in item:
                raise TranslationError("INVALID_DOCUMENT", f"chunk {chunk_id} lacks reusable completion evidence")
            _ensure_hash(item["artifact_hash"], "chunk.artifact_hash")
        elif "error_code" not in item:
            raise TranslationError("INVALID_DOCUMENT", f"failed chunk {chunk_id} lacks an error code")
        chunks[chunk_id] = item
    if claimed_core_ids != source_id_set:
        raise TranslationError("INVALID_DOCUMENT", "every source utterance must belong to exactly one core chunk")

    translations_value = value["translations"]
    if type(translations_value) is not list:
        raise TranslationError("INVALID_DOCUMENT", "translations must be an array")
    translated_ids: set[str] = set()
    for position, raw_item in enumerate(translations_value):
        item = _object(
            raw_item,
            f"translations[{position}]",
            {
                "source_utterance_id", "source_text", "source_normalized_text", "translated_text",
                "normalized_text", "source_language", "target_language", "start", "end", "confidence", "chunk_id",
            },
        )
        source_id = _text(item["source_utterance_id"], "translation.source_utterance_id", limit=256)
        if source_id in translated_ids or source_id not in source_id_set:
            raise TranslationError("INVALID_DOCUMENT", f"translation source ID is duplicate or unknown: {source_id}")
        translated_ids.add(source_id)
        _text(item["source_text"], "translation.source_text", limit=8192)
        _text(item["source_normalized_text"], "translation.source_normalized_text", limit=8192)
        _text(item["translated_text"], "translation.translated_text", limit=8192)
        _text(item["normalized_text"], "translation.normalized_text", limit=8192)
        item_language = _language(item["source_language"], "translation.source_language")
        if source_language != "und" and item_language != source_language:
            raise TranslationError("INVALID_DOCUMENT", "translation source language differs from document")
        if item["target_language"] != TARGET_LANGUAGE:
            raise TranslationError("INVALID_DOCUMENT", "translation target language must be vi")
        start = _parse_time_point(item["start"], f"translations[{position}].start")
        end = _parse_time_point(item["end"], f"translations[{position}].end")
        _interval(start, end, f"translation {source_id}")
        _confidence(item["confidence"], "translation.confidence")
        chunk_id = _text(item["chunk_id"], "translation.chunk_id", limit=128)
        if chunk_id not in chunks or source_id not in set(chunks[chunk_id]["core_utterance_ids"]):
            raise TranslationError("INVALID_DOCUMENT", f"translation {source_id} has invalid chunk ownership")

    for position, raw_item in enumerate(value["failures"]):
        item = _object(
            raw_item,
            f"failures[{position}]",
            {"code", "chunk_id", "utterance_ids", "retryable", "attempt", "condition", "fallback_used"},
        )
        _text(item["code"], "failure.code", limit=128)
        chunk_id = _text(item["chunk_id"], "failure.chunk_id", limit=128)
        if chunk_id != "global" and chunk_id not in chunks:
            raise TranslationError("INVALID_DOCUMENT", "failure references an unknown chunk")
        ids = item["utterance_ids"]
        if type(ids) is not list or len(ids) != len(set(ids)):
            raise TranslationError("INVALID_DOCUMENT", "failure utterance IDs are invalid")
        if any(_text(identifier, "failure.utterance_id", limit=256) not in source_id_set for identifier in ids):
            raise TranslationError("INVALID_DOCUMENT", "failure references an unknown source utterance")
        if type(item["retryable"]) is not bool or type(item["fallback_used"]) is not bool:
            raise TranslationError("INVALID_DOCUMENT", "failure booleans are invalid")
        _integer(item["attempt"], "failure.attempt", minimum=1, maximum=255)
        _text(item["condition"], "failure.condition", limit=4096)

    if type(value["warnings"]) is not list or any(type(item) is not str or not item.strip() for item in value["warnings"]):
        raise TranslationError("INVALID_DOCUMENT", "warnings must be non-empty strings")


def parse_translation_json(text: str | bytes) -> dict[str, Any]:
    """Parse canonical JSON while rejecting duplicate fields and non-finite values."""

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise TranslationError("DUPLICATE_FIELD", f"duplicate JSON member {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise TranslationError("NON_FINITE_NUMBER", f"JSON constant {value!r} is not allowed")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except TranslationError:
        raise
    except (TypeError, ValueError) as error:
        raise TranslationError("MALFORMED_JSON", str(error)) from error
    validate_translation_document(value)
    return value


class TranslationPlanner:
    """Plan bounded core batches with explicit before/after context windows."""

    def __init__(self, config: TranslationConfig) -> None:
        self.config = config

    def plan(self, sources: Sequence[SourceSegment], *, input_hash: str) -> tuple[TranslationChunkPlan, ...]:
        if not sources:
            raise TranslationError("EMPTY_SOURCE", "translation input has no utterances")
        ordered = tuple(sources)
        seen: set[str] = set()
        base = ordered[0].start.time_base
        for item in ordered:
            if item.utterance_id in seen:
                raise TranslationError("DUPLICATE_SOURCE_ID", f"duplicate source utterance {item.utterance_id}")
            seen.add(item.utterance_id)
            if item.start.time_base != base:
                raise TranslationError("TIMELINE_TIME_BASE_MISMATCH", "translation sources must share one time base")
        ordered = tuple(sorted(ordered, key=lambda item: (item.start.ticks, item.end.ticks, item.utterance_id)))
        plans: list[TranslationChunkPlan] = []
        for index, start in enumerate(range(0, len(ordered), self.config.max_items_per_chunk)):
            core = ordered[start : start + self.config.max_items_per_chunk]
            before = ordered[max(0, start - self.config.context_before) : start]
            end = start + len(core)
            after = ordered[end : end + self.config.context_after]
            payload = {
                "input_hash": input_hash,
                "config_hash": self.config.to_hash(),
                "index": index,
                "core": [item.utterance_id for item in core],
                "before": [item.utterance_id for item in before],
                "after": [item.utterance_id for item in after],
            }
            chunk_id = "tr-chunk-" + sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
            plans.append(TranslationChunkPlan(chunk_id, index, core, before, after))
        return tuple(plans)


class LocalTranslationAdapter:
    """Validate a local backend and emit deterministic, checkpointable output."""

    def __init__(
        self,
        backend: TranslationBackend,
        *,
        config: TranslationConfig,
        provenance: TranslationProvenance,
        supported_source_language: str | None = None,
        fallback_backend: TranslationBackend | None = None,
        fallback_profile: str = "cpu",
        fallback_provenance: TranslationProvenance | None = None,
    ) -> None:
        self.backend = backend
        self.config = config
        self.provenance = provenance
        self.supported_source_language: str | None = None
        if supported_source_language is not None:
            capability = _canonical_source_language(
                supported_source_language,
                "adapter.supported_source_language",
            )
            if capability in {"auto", "und"}:
                raise TranslationError(
                    "TRANSLATION_ROUTE_CAPABILITY_INVALID",
                    "translation route capability must name one concrete source language",
                )
            self.supported_source_language = capability
        self.fallback_backend = fallback_backend
        self.fallback_profile = fallback_profile
        if fallback_profile not in {"cpu", "gpu", "fixture"}:
            raise TranslationError("INVALID_PROFILE", "fallback profile is unsupported")
        if provenance.config_hash != config.to_hash():
            raise TranslationError("PROVENANCE_CONFIG_MISMATCH", "provenance config hash differs from adapter config")
        if fallback_provenance is not None:
            if (
                fallback_provenance.config_hash != provenance.config_hash
                or fallback_provenance.input_hash != provenance.input_hash
                or fallback_provenance.glossary_hash != provenance.glossary_hash
            ):
                raise TranslationError("PROVENANCE_FALLBACK_MISMATCH", "fallback provenance must use the same input, config and glossary hash")
        self.fallback_provenance = fallback_provenance

    def translate(
        self,
        sources: Sequence[SourceSegment] | Sequence[Any],
        *,
        input_hash: str,
        glossary: Mapping[str, str] | None = None,
        checkpoints: Mapping[str, TranslationCheckpoint] | None = None,
    ) -> TranslationDocument:
        if input_hash != self.provenance.input_hash:
            raise TranslationError("PROVENANCE_INPUT_MISMATCH", "translate input hash differs from provenance")
        source_items = tuple(item if isinstance(item, SourceSegment) else SourceSegment.from_asr(item) for item in sources)
        glossary_value = self._validate_glossary(glossary or {})
        glossary_pairs = tuple(sorted(glossary_value.items()))
        if _hash_mapping(glossary_value) != self.provenance.glossary_hash:
            raise TranslationError("PROVENANCE_GLOSSARY_MISMATCH", "glossary hash differs from provenance")
        plans = TranslationPlanner(self.config).plan(source_items, input_hash=input_hash)
        source_by_id = {item.utterance_id: item for item in source_items}
        source_language = source_items[0].source_language if len({item.source_language for item in source_items}) == 1 else "und"
        source_ids = tuple(item.utterance_id for item in sorted(source_items, key=lambda item: (item.start.ticks, item.end.ticks, item.utterance_id)))
        failures: list[TranslationFailure] = []
        warnings: list[str] = []
        translated: list[TranslatedSegment] = []
        chunk_records: list[dict[str, Any]] = []
        selected_provenance = self.provenance
        successful = 0
        primary_successes = 0
        fallback_successes = 0

        for plan in plans:
            failure_start = len(failures)
            request = plan.request(glossary=glossary_pairs)
            result: TranslationBackendResult | None = None
            status = "completed"
            attempts = 0
            used_fallback = False
            checkpoint = checkpoints.get(plan.chunk_id) if checkpoints else None
            if checkpoint is not None and checkpoint.chunk_id == plan.chunk_id and checkpoint.artifact_hash == self._artifact_hash(plan, input_hash, glossary_pairs, checkpoint.result):
                checkpoint_error = self._validation_error(checkpoint.result, request)
                if checkpoint_error is None:
                    result = checkpoint.result
                    status = "skipped"
                    attempts = 1
                else:
                    warnings.append(f"checkpoint {plan.chunk_id} invalidated: {checkpoint_error.code}")
            if result is None:
                result, backend_failures, attempts = self._invoke_backend(self.backend, request)
                failures.extend(TranslationFailure.from_error(item, chunk=plan) for item in backend_failures)
                validation_error = self._validation_error(result, request) if result is not None else None
                if validation_error is not None:
                    failures.append(TranslationFailure.from_error(validation_error, chunk=plan))
                    result = None
                if result is None and self.fallback_backend is not None:
                    used_fallback = True
                    fallback_result, fallback_failures, fallback_attempts = self._invoke_backend(self.fallback_backend, request)
                    attempts = max(attempts, fallback_attempts)
                    failures.extend(TranslationFailure.from_error(item, chunk=plan, fallback_used=True) for item in fallback_failures)
                    failures[failure_start:] = [replace(item, fallback_used=True) for item in failures[failure_start:]]
                    fallback_error = self._validation_error(fallback_result, request) if fallback_result is not None else None
                    if fallback_error is not None:
                        failures.append(TranslationFailure.from_error(fallback_error, chunk=plan, fallback_used=True))
                        fallback_result = None
                    if fallback_result is not None:
                        result = fallback_result
                        selected_provenance = self._fallback_selected_provenance()
            if result is not None:
                order = {item.utterance_id: index for index, item in enumerate(plan.core)}
                for candidate in sorted(result.translations, key=lambda item: order[item.source_utterance_id]):
                    source = source_by_id[candidate.source_utterance_id]
                    translated.append(
                        TranslatedSegment(
                            source.utterance_id,
                            source.source_text,
                            source.normalized_text or source.source_text,
                            candidate.translated_text,
                            _normalize_text(candidate.translated_text),
                            source.source_language,
                            TARGET_LANGUAGE,
                            source.start,
                            source.end,
                            candidate.confidence,
                            plan.chunk_id,
                        )
                    )
                successful += 1
                if used_fallback:
                    fallback_successes += 1
                else:
                    primary_successes += 1
            else:
                status = "failed"
            error_code = failures[-1].code if status == "failed" and failures else None
            record = plan.to_dict(status=status, attempt=max(1, attempts), error_code=error_code)
            if status in {"completed", "skipped"} and result is not None:
                record["artifact_hash"] = self._artifact_hash(plan, input_hash, glossary_pairs, result)
                record["checkpoint_id"] = f"{plan.chunk_id}:safe"
            chunk_records.append(record)

        if successful == 0:
            document = TranslationDocument(
                source_language,
                TARGET_LANGUAGE,
                source_ids,
                (),
                tuple(chunk_records),
                self.provenance,
                tuple(failures),
                tuple(dict.fromkeys(warnings)),
            )
            validate_translation_document(document.to_dict())
            raise TranslationStageError(document)

        if failures:
            warnings.append("translation is degraded: one or more chunks failed or used fallback")
        if primary_successes and fallback_successes:
            selected_provenance = replace(
                self.provenance,
                backend_id="composite",
                model_id="composite",
                model_version="mixed",
                model_hash=_hash_json({
                    "primary": self.provenance.model_hash,
                    "fallback": self._fallback_model_hash(),
                }),
                hardware_profile="mixed",
                fallback_reason="document contains primary and fallback chunk results; see per-chunk failures",
            )
        document = TranslationDocument(
            source_language,
            TARGET_LANGUAGE,
            source_ids,
            tuple(translated),
            tuple(chunk_records),
            selected_provenance,
            tuple(failures),
            tuple(dict.fromkeys(warnings)),
        )
        validate_translation_document(document.to_dict())
        return document

    @staticmethod
    def _validate_glossary(glossary: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(glossary, Mapping):
            raise TranslationError("INVALID_GLOSSARY", "glossary must be a mapping")
        result: dict[str, str] = {}
        for key, value in glossary.items():
            result[_text(key, "glossary.source", limit=256)] = _text(value, "glossary.target", limit=256)
        return result

    def _fallback_selected_provenance(self) -> TranslationProvenance:
        fallback = self.fallback_provenance or self.provenance
        return replace(
            fallback,
            backend_id=self.fallback_provenance.backend_id if self.fallback_provenance else "fallback-cpu",
            model_id=self.fallback_provenance.model_id if self.fallback_provenance else "fallback-cpu",
            model_version=self.fallback_provenance.model_version if self.fallback_provenance else "1",
            model_hash=self.fallback_provenance.model_hash if self.fallback_provenance else self._fallback_model_hash(),
            hardware_profile=self.fallback_provenance.hardware_profile if self.fallback_provenance else self.fallback_profile,
            fallback_reason="primary translation backend failed or returned invalid data; bounded fallback selected",
        )

    def _fallback_model_hash(self) -> str:
        return _hash_json({
            "profile": self.fallback_profile,
            "backend": (
                f"{type(self.fallback_backend).__module__}.{type(self.fallback_backend).__qualname__}"
                if self.fallback_backend is not None else "none"
            ),
        })

    def _invoke_backend(self, backend: TranslationBackend, request: TranslationRequest) -> tuple[TranslationBackendResult | None, list[TranslationError], int]:
        failures: list[TranslationError] = []
        previous_condition: str | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = backend.translate(request)
                if not isinstance(result, TranslationBackendResult):
                    failures.append(TranslationError("MALFORMED_BACKEND_RESULT", "backend did not return TranslationBackendResult", attempt=attempt))
                    return None, failures, attempt
                return result, failures, attempt
            except TranslationError as error:
                failure = TranslationError(
                    error.code,
                    error.condition,
                    retryable=error.retryable,
                    attempt=attempt,
                    fallback_used=error.fallback_used,
                )
                failures.append(failure)
                if previous_condition == error.condition:
                    failures.append(TranslationError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", attempt=attempt))
                    return None, failures, attempt
                if not error.retryable or attempt >= self.config.max_attempts:
                    return None, failures, attempt
                previous_condition = error.condition
            except Exception as error:
                condition = _safe_condition(error)
                failures.append(TranslationError("TRANSLATION_BACKEND_FAILED", condition, retryable=True, attempt=attempt))
                if previous_condition == condition:
                    failures.append(TranslationError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", attempt=attempt))
                    return None, failures, attempt
                if attempt >= self.config.max_attempts:
                    return None, failures, attempt
                previous_condition = condition
        return None, failures, self.config.max_attempts

    def _validation_error(self, result: TranslationBackendResult | None, request: TranslationRequest) -> TranslationError | None:
        if result is None:
            return None
        try:
            self._validate_backend_result(result, request)
        except TranslationError as error:
            return error
        except Exception as error:
            return TranslationError("MALFORMED_BACKEND_RESULT", _safe_condition(error))
        return None

    @staticmethod
    def _validate_backend_result(result: TranslationBackendResult, request: TranslationRequest) -> None:
        if not isinstance(result, TranslationBackendResult):
            raise TranslationError("MALFORMED_BACKEND_RESULT", "backend returned an unexpected result type")
        expected = {item.utterance_id for item in request.core}
        seen: set[str] = set()
        for candidate in result.translations:
            if not isinstance(candidate, TranslationCandidate):
                raise TranslationError("MALFORMED_BACKEND_RESULT", "backend returned an invalid candidate")
            if candidate.source_utterance_id in seen:
                raise TranslationError("DUPLICATE_TRANSLATION", "backend returned a duplicate source utterance")
            seen.add(candidate.source_utterance_id)
            if candidate.source_utterance_id not in expected:
                raise TranslationError("UNKNOWN_SOURCE_ID", "backend returned a non-core source utterance")
        if seen != expected:
            missing = sorted(expected - seen)
            raise TranslationError("MISSING_TRANSLATION", f"backend omitted core utterances: {missing}")

    @staticmethod
    def _backend_result_dict(result: TranslationBackendResult | None) -> dict[str, Any]:
        return {"status": "failed"} if result is None else result.to_dict()

    def _artifact_hash(
        self,
        plan: TranslationChunkPlan,
        input_hash: str,
        glossary: tuple[tuple[str, str], ...],
        result: TranslationBackendResult | None,
    ) -> str:
        payload = {
            "input_hash": input_hash,
            "config_hash": self.config.to_hash(),
            "model_hash": self.provenance.model_hash,
            "glossary": list(glossary),
            "chunk": plan.to_dict(status="completed", attempt=1),
            "backend": f"{type(self.backend).__module__}.{type(self.backend).__qualname__}",
            "fallback_backend": (
                f"{type(self.fallback_backend).__module__}.{type(self.fallback_backend).__qualname__}"
                if self.fallback_backend is not None else None
            ),
            "result": self._backend_result_dict(result),
        }
        if self.supported_source_language is not None:
            payload["route"] = {
                "source_language": self.supported_source_language,
                "target_language": TARGET_LANGUAGE,
                "backend_id": self.provenance.backend_id,
                "model_id": self.provenance.model_id,
                "model_version": self.provenance.model_version,
            }
        return _hash_json(payload)


class RoutedTranslationAdapter:
    """Select one installed local source->Vietnamese route before translation."""

    def __init__(self, routes: Mapping[str, LocalTranslationAdapter]) -> None:
        if not isinstance(routes, Mapping) or not routes:
            raise TranslationError(
                "TRANSLATION_ROUTE_INVALID",
                "at least one installed local translation route is required",
            )
        normalized: dict[str, LocalTranslationAdapter] = {}
        for raw_source_language, route in routes.items():
            source_language = _canonical_source_language(raw_source_language, "route.source_language")
            if source_language in {"auto", "und"}:
                raise TranslationError(
                    "TRANSLATION_ROUTE_INVALID",
                    "route source language must be concrete",
                )
            if not isinstance(route, LocalTranslationAdapter):
                raise TranslationError(
                    "TRANSLATION_ROUTE_INVALID",
                    "route must be a LocalTranslationAdapter",
                )
            if route.supported_source_language is None:
                raise TranslationError(
                    "TRANSLATION_ROUTE_CAPABILITY_MISSING",
                    f"route {source_language}->{TARGET_LANGUAGE} does not declare its source capability",
                )
            if route.supported_source_language != source_language:
                raise TranslationError(
                    "TRANSLATION_ROUTE_CAPABILITY_MISMATCH",
                    (
                        f"route key {source_language}->{TARGET_LANGUAGE} does not match "
                        f"adapter capability {route.supported_source_language}->{TARGET_LANGUAGE}"
                    ),
                )
            if route.fallback_backend is not None:
                raise TranslationError(
                    "TRANSLATION_ROUTE_FALLBACK_UNSAFE",
                    (
                        f"route {source_language}->{TARGET_LANGUAGE} must not use an "
                        "unfenced fallback backend"
                    ),
                )
            if source_language in normalized:
                raise TranslationError(
                    "TRANSLATION_ROUTE_INVALID",
                    f"duplicate route for {source_language}->{TARGET_LANGUAGE}",
                )
            normalized[source_language] = route
        self.routes = normalized

    def translate(
        self,
        sources: Sequence[SourceSegment] | Sequence[Any],
        *,
        input_hash: str,
        source_language: str = "auto",
        glossary: Mapping[str, str] | None = None,
        checkpoints: Mapping[str, TranslationCheckpoint] | None = None,
    ) -> TranslationDocument:
        source_items = tuple(
            item if isinstance(item, SourceSegment) else SourceSegment.from_asr(item)
            for item in sources
        )
        resolved = resolve_source_language(
            source_items,
            requested_source_language=source_language,
        )
        route = self.routes.get(resolved)
        if route is None:
            raise TranslationError(
                "TRANSLATION_ROUTE_UNAVAILABLE",
                f"no installed local {resolved}->{TARGET_LANGUAGE} route is available",
            )

        normalized_sources = tuple(
            item
            if item.source_language == resolved
            else replace(item, source_language=resolved)
            for item in source_items
        )
        document = route.translate(
            normalized_sources,
            input_hash=input_hash,
            glossary=glossary,
            checkpoints=checkpoints,
        )
        if document.source_language != resolved:
            raise TranslationError(
                "TRANSLATION_ROUTE_PROVENANCE_MISMATCH",
                "selected route returned a document for a different source language",
            )
        if document.target_language != TARGET_LANGUAGE:
            raise TranslationError(
                "TRANSLATION_ROUTE_PROVENANCE_MISMATCH",
                "selected route returned a document for a different target language",
            )
        if document.provenance != route.provenance:
            raise TranslationError(
                "TRANSLATION_ROUTE_PROVENANCE_MISMATCH",
                "selected route returned provenance for a different backend or model",
            )
        if any(
            item.source_language != resolved or item.target_language != TARGET_LANGUAGE
            for item in document.translations
        ):
            raise TranslationError(
                "TRANSLATION_ROUTE_PROVENANCE_MISMATCH",
                "selected route returned segment language provenance that does not match the route",
            )
        return document


class DeterministicFixtureBackend:
    """Offline fixture backend preserving exact punctuation, names and numbers."""

    def __init__(
        self,
        translations: Mapping[str, str],
        *,
        confidences: Mapping[str, float] | None = None,
        failures: Mapping[str, TranslationBackendError] | None = None,
    ) -> None:
        self.translations = dict(translations)
        self.confidences = dict(confidences or {})
        self.failures = dict(failures or {})
        self.calls: list[str] = []
        self.contexts: list[tuple[str, ...]] = []

    def translate(self, request: TranslationRequest) -> TranslationBackendResult:
        self.calls.append(request.chunk_id)
        self.contexts.append(tuple(item.utterance_id for item in request.all_segments))
        failure = self.failures.get(request.chunk_id)
        if failure is not None:
            raise failure
        return TranslationBackendResult(
            tuple(
                TranslationCandidate(
                    item.utterance_id,
                    self.translations.get(item.utterance_id, item.source_text),
                    self.confidences.get(item.utterance_id, 1.0),
                )
                for item in request.core
            )
        )


# Short compatibility name for callers that do not need to distinguish the
# local implementation from the versioned translation boundary.
TranslationAdapter = LocalTranslationAdapter
