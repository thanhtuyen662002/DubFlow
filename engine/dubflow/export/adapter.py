"""Validated standard export and editable-pack boundary.

The module deliberately keeps media engines behind ``RenderBackend``. The
adapter owns request semantics, optional-asset handling, provenance, temporary
output quarantine, atomic final publication and deterministic validation. A
fixture renderer exercises the complete boundary without requiring FFmpeg,
CapCut, a GPU or a system dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Protocol
import unicodedata
import os

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint


EXPORT_CONTRACT_VERSION = 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class ExportError(ValueError):
    """Stable export failure with retry and fallback classification."""

    def __init__(
        self,
        code: str,
        condition: str,
        *,
        retryable: bool = False,
        attempt: int = 1,
        fallback_used: bool = False,
    ) -> None:
        if not code or not condition:
            raise ValueError("export failures require code and condition")
        if type(retryable) is not bool or type(attempt) is not int or not 1 <= attempt <= 255:
            raise ValueError("invalid export failure metadata")
        if type(fallback_used) is not bool:
            raise ValueError("fallback_used must be boolean")
        self.code = code
        self.condition = _safe_condition(condition)
        self.retryable = retryable
        self.attempt = attempt
        self.fallback_used = fallback_used
        super().__init__(f"{code}: {self.condition}")


class RenderBackendError(ExportError):
    """A renderer failure that may be retried or sent to software fallback."""


class ExportStageError(ExportError):
    """Raised when the requested export cannot produce a validated result."""

    def __init__(self, failures: tuple["ExportFailure", ...]) -> None:
        self.failures = failures
        super().__init__(
            "EXPORT_FAILED",
            "render/export failed before a validated final artifact was published",
            retryable=any(item.retryable for item in failures),
        )


def _safe_condition(value: Any) -> str:
    text = _CONTROL.sub(" ", str(value) or "export backend returned an empty condition").strip()
    return text[:4096] or "export backend returned an empty condition"


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise ExportError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _integer(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ExportError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ExportError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ExportError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _ensure_hash(value: Any, name: str) -> str:
    value = _text(value, name, limit=80)
    if _SHA256.fullmatch(value) is None:
        raise ExportError("INVALID_HASH", f"{name} must be a sha256 digest")
    return value


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _safe_path(value: str | Path, name: str) -> Path:
    path = Path(value)
    _text(str(path), name, limit=4096)
    if path.name in {"", ".", ".."}:
        raise ExportError("INVALID_PATH", f"{name} must identify a file")
    return path


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    path: str
    content_hash: str
    duration: TimeInterval
    width: int
    height: int
    rotation: int = 0
    has_audio: bool = True

    def __post_init__(self) -> None:
        _text(self.asset_id, "media.asset_id", limit=256)
        _text(self.path, "media.path", limit=4096)
        _ensure_hash(self.content_hash, "media.content_hash")
        if not isinstance(self.duration, TimeInterval):
            raise ExportError("INVALID_TIMELINE", "media duration must be a canonical interval")
        _integer(self.width, "media.width", minimum=1, maximum=65535)
        _integer(self.height, "media.height", minimum=1, maximum=65535)
        _integer(self.rotation, "media.rotation", minimum=-360, maximum=360)
        if type(self.has_audio) is not bool:
            raise ExportError("INVALID_MEDIA", "media.has_audio must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "path": self.path,
            "content_hash": self.content_hash,
            "duration_start": self.duration.start.to_dict(),
            "duration_end": self.duration.end.to_dict(),
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "has_audio": self.has_audio,
        }


@dataclass(frozen=True)
class ExportAsset:
    kind: str
    asset_id: str
    path: str
    content_hash: str
    format: str | None = None
    duration: TimeInterval | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"subtitle", "dub_audio"}:
            raise ExportError("INVALID_ASSET", "optional asset kind is unsupported")
        _text(self.asset_id, "asset.asset_id", limit=256)
        _text(self.path, "asset.path", limit=4096)
        _ensure_hash(self.content_hash, "asset.content_hash")
        if self.format is not None:
            _text(self.format, "asset.format", limit=32)
        if self.duration is not None and not isinstance(self.duration, TimeInterval):
            raise ExportError("INVALID_TIMELINE", "optional asset duration must be canonical")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "asset_id": self.asset_id,
            "path": self.path,
            "content_hash": self.content_hash,
        }
        if self.format is not None:
            result["format"] = self.format
        if self.duration is not None:
            result["duration_start"] = self.duration.start.to_dict()
            result["duration_end"] = self.duration.end.to_dict()
        return result


@dataclass(frozen=True)
class ExportConfig:
    container: str = "mp4"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    requested_profile: str = "auto"
    max_attempts: int = 2
    overwrite: bool = False

    def __post_init__(self) -> None:
        if self.container != "mp4" or self.video_codec != "h264" or self.audio_codec != "aac":
            raise ExportError("UNSUPPORTED_PROFILE", "export v1 requires MP4/H.264/AAC compatibility profile")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise ExportError("INVALID_PROFILE", "requested profile is unsupported")
        _integer(self.max_attempts, "config.max_attempts", minimum=1, maximum=3)
        if type(self.overwrite) is not bool:
            raise ExportError("INVALID_CONFIG", "config.overwrite must be boolean")

    def to_hash(self) -> str:
        return _hash_json({
            "container": self.container,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "requested_profile": self.requested_profile,
            "max_attempts": self.max_attempts,
            "overwrite": self.overwrite,
        })


@dataclass(frozen=True)
class ExportProvenance:
    producer: str
    producer_version: str
    renderer_id: str
    renderer_version: str
    runtime: str
    timeline_contract: str
    config_hash: str
    input_hash: str
    requested_profile: str
    hardware_profile: str
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("producer", "producer_version", "renderer_id", "renderer_version", "runtime", "timeline_contract"):
            _text(getattr(self, name), f"provenance.{name}", limit=256)
        if self.timeline_contract != "timeline-v1":
            raise ExportError("UNSUPPORTED_TIMELINE_CONTRACT", "export v1 requires timeline-v1")
        _ensure_hash(self.config_hash, "provenance.config_hash")
        _ensure_hash(self.input_hash, "provenance.input_hash")
        if self.requested_profile not in {"auto", "cpu", "gpu", "fixture"}:
            raise ExportError("INVALID_PROFILE", "requested profile is unsupported")
        if self.hardware_profile not in {"cpu", "gpu", "fixture", "mixed"}:
            raise ExportError("INVALID_PROFILE", "selected hardware profile is unsupported")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "provenance.fallback_reason", limit=4096)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "renderer_id": self.renderer_id,
            "renderer_version": self.renderer_version,
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
class ExportRequest:
    source: MediaAsset
    output_path: Path
    subtitle: ExportAsset | None = None
    dub_audio: ExportAsset | None = None
    preserve_original_audio: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.source, MediaAsset):
            raise ExportError("INVALID_REQUEST", "source media is required")
        output = _safe_path(self.output_path, "request.output_path")
        if output.suffix.casefold() != ".mp4":
            raise ExportError("INVALID_PATH", "output path must use the .mp4 suffix")
        try:
            if output.resolve() == Path(self.source.path).resolve():
                raise ExportError("INVALID_PATH", "output path must not overwrite the source asset")
            optional_paths = [asset.path for asset in (self.subtitle, self.dub_audio) if asset is not None]
            if any(output.resolve() == Path(path).resolve() for path in optional_paths):
                raise ExportError("INVALID_PATH", "output path must not overwrite an input localization asset")
        except OSError as error:
            raise ExportError("INVALID_PATH", str(error)) from error
        if self.subtitle is not None and self.subtitle.kind != "subtitle":
            raise ExportError("INVALID_REQUEST", "subtitle field must contain a subtitle asset")
        if self.dub_audio is not None and self.dub_audio.kind != "dub_audio":
            raise ExportError("INVALID_REQUEST", "dub_audio field must contain a dub_audio asset")
        if type(self.preserve_original_audio) is not bool:
            raise ExportError("INVALID_REQUEST", "preserve_original_audio must be boolean")
        if self.dub_audio is not None and self.preserve_original_audio:
            raise ExportError("INVALID_AUDIO_MODE", "dubbed audio cannot be combined with preserved original audio")

    @property
    def audio_mode(self) -> str:
        if self.dub_audio is not None:
            return "dubbed"
        if self.preserve_original_audio and self.source.has_audio:
            return "original"
        return "none"

    def input_hash(self) -> str:
        return _hash_json({
            "source": self.source.to_dict(),
            "subtitle": self.subtitle.to_dict() if self.subtitle is not None else None,
            "dub_audio": self.dub_audio.to_dict() if self.dub_audio is not None else None,
            "audio_mode": self.audio_mode,
        })


@dataclass(frozen=True)
class RenderRequest:
    export: ExportRequest
    temporary_path: Path
    config: ExportConfig

    @property
    def source(self) -> MediaAsset:
        return self.export.source

    @property
    def subtitle(self) -> ExportAsset | None:
        return self.export.subtitle

    @property
    def dub_audio(self) -> ExportAsset | None:
        return self.export.dub_audio

    @property
    def audio_mode(self) -> str:
        return self.export.audio_mode


@dataclass(frozen=True)
class RenderedOutput:
    container: str
    video_codec: str
    audio_codec: str | None
    width: int
    height: int
    rotation: int
    duration: TimeInterval


class RenderBackend(Protocol):
    def render(self, request: RenderRequest) -> RenderedOutput:
        ...


@dataclass(frozen=True)
class ExportFailure:
    code: str
    attempt: int
    retryable: bool
    condition: str
    fallback_used: bool = False

    def __post_init__(self) -> None:
        _text(self.code, "failure.code", limit=128)
        _integer(self.attempt, "failure.attempt", minimum=1, maximum=255)
        if type(self.retryable) is not bool or type(self.fallback_used) is not bool:
            raise ExportError("INVALID_FAILURE", "failure booleans are invalid")
        _text(self.condition, "failure.condition", limit=4096)

    @classmethod
    def from_error(cls, error: ExportError, *, fallback_used: bool | None = None) -> "ExportFailure":
        return cls(
            error.code,
            error.attempt,
            error.retryable,
            error.condition,
            error.fallback_used if fallback_used is None else fallback_used,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "attempt": self.attempt,
            "retryable": self.retryable,
            "condition": self.condition,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class ExportResult:
    audio_mode: str
    output: dict[str, Any]
    editable_pack: dict[str, Any]
    provenance: ExportProvenance
    warnings: tuple[str, ...] = ()
    failures: tuple[ExportFailure, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXPORT_CONTRACT_VERSION,
            "kind": "export_result",
            "source_kind": "media",
            "status": "completed",
            "audio_mode": self.audio_mode,
            "output": self.output,
            "editable_pack": self.editable_pack,
            "provenance": self.provenance.to_dict(),
            "warnings": list(self.warnings),
            "failures": [item.to_dict() for item in self.failures],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ExportCheckpoint:
    output_hash: str
    output: dict[str, Any]

    def __post_init__(self) -> None:
        _ensure_hash(self.output_hash, "checkpoint.output_hash")


def _object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise ExportError("INVALID_RESULT", f"{name} must be an object")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        raise ExportError("INVALID_RESULT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise ExportError("INVALID_RESULT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise ExportError("INVALID_RESULT", f"{name} must be a decimal string")
    pattern = r"^(0|-?[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise ExportError("INVALID_RESULT", f"{name} is not canonical decimal")
    result = int(value)
    _integer(result, name, minimum=I64_MIN if signed else 1, maximum=I64_MAX if signed else U64_MAX)
    return result


def _parse_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or value["schema_version"] != EXPORT_CONTRACT_VERSION:
        raise ExportError("INVALID_RESULT", f"{name} discriminator/version is unsupported")
    base = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    try:
        return TimePoint(
            _decimal(value["ticks"], f"{name}.ticks", signed=True),
            TimeBase(
                _decimal(base["numerator"], f"{name}.time_base.numerator", signed=False),
                _decimal(base["denominator"], f"{name}.time_base.denominator", signed=False),
            ),
        )
    except ExportError:
        raise
    except Exception as error:
        raise ExportError("INVALID_TIMELINE", str(error)) from error


def _parse_interval(value: Mapping[str, Any], name: str) -> TimeInterval:
    start = _parse_point(value["duration_start"], f"{name}.duration_start")
    end = _parse_point(value["duration_end"], f"{name}.duration_end")
    try:
        return TimeInterval(start, end)
    except Exception as error:
        raise ExportError("INVALID_TIMELINE", f"{name}: {error}") from error


def validate_export_result(value: Mapping[str, Any]) -> None:
    """Validate strict export result metadata before it is exposed to callers."""

    value = _object(value, "result", {"schema_version", "kind", "source_kind", "status", "audio_mode", "output", "editable_pack", "provenance", "warnings", "failures"})
    if value["schema_version"] != EXPORT_CONTRACT_VERSION or value["kind"] != "export_result" or value["source_kind"] != "media" or value["status"] != "completed":
        raise ExportError("INVALID_RESULT", "export result discriminator/version/status is unsupported")
    if value["audio_mode"] not in {"original", "dubbed", "none"}:
        raise ExportError("INVALID_RESULT", "audio mode is unsupported")
    provenance = _object(
        value["provenance"],
        "provenance",
        {"producer", "producer_version", "renderer_id", "renderer_version", "runtime", "timeline_contract", "config_hash", "input_hash", "requested_profile", "hardware_profile"},
        {"fallback_reason"},
    )
    ExportProvenance(**provenance)
    output = _object(value["output"], "output", {"path", "content_hash", "container", "video_codec", "audio_codec", "width", "height", "rotation", "duration_start", "duration_end"})
    _text(output["path"], "output.path", limit=4096)
    _ensure_hash(output["content_hash"], "output.content_hash")
    if output["container"] != "mp4" or output["video_codec"] != "h264" or output["audio_codec"] not in {"aac", None}:
        raise ExportError("INVALID_RESULT", "output is not MP4/H.264 with optional AAC")
    _integer(output["width"], "output.width", minimum=1, maximum=65535)
    _integer(output["height"], "output.height", minimum=1, maximum=65535)
    _integer(output["rotation"], "output.rotation", minimum=-360, maximum=360)
    _parse_interval(output, "output")
    pack = _object(value["editable_pack"], "editable_pack", {"path", "content_hash", "assets"})
    _text(pack["path"], "editable_pack.path", limit=4096)
    _ensure_hash(pack["content_hash"], "editable_pack.content_hash")
    if type(pack["assets"]) is not list:
        raise ExportError("INVALID_RESULT", "editable_pack.assets must be an array")
    asset_ids: set[str] = set()
    for index, item in enumerate(pack["assets"]):
        asset = _object(item, f"editable_pack.assets[{index}]", {"kind", "asset_id", "path", "content_hash"}, {"format", "duration_start", "duration_end"})
        if asset["kind"] not in {"subtitle", "dub_audio"}:
            raise ExportError("INVALID_RESULT", "editable pack has an unsupported asset")
        asset_id = _text(asset["asset_id"], "editable asset ID", limit=256)
        if asset_id in asset_ids:
            raise ExportError("INVALID_RESULT", "editable pack asset IDs must be unique")
        asset_ids.add(asset_id)
        _text(asset["path"], "editable asset path", limit=4096)
        _ensure_hash(asset["content_hash"], "editable asset hash")
        if "format" in asset:
            _text(asset["format"], "editable asset format", limit=32)
        if "duration_start" in asset or "duration_end" in asset:
            if "duration_start" not in asset or "duration_end" not in asset:
                raise ExportError("INVALID_RESULT", "editable asset duration must have both boundaries")
            _parse_interval(asset, f"editable_pack.assets[{index}]")
    if type(value["warnings"]) is not list or any(type(item) is not str or not item.strip() for item in value["warnings"]):
        raise ExportError("INVALID_RESULT", "warnings must be non-empty strings")
    for index, item in enumerate(value["failures"]):
        item = _object(item, f"failures[{index}]", {"code", "attempt", "retryable", "condition", "fallback_used"})
        _text(item["code"], "failure.code", limit=128)
        _integer(item["attempt"], "failure.attempt", minimum=1, maximum=255)
        if type(item["retryable"]) is not bool or type(item["fallback_used"]) is not bool:
            raise ExportError("INVALID_RESULT", "failure booleans are invalid")
        _text(item["condition"], "failure.condition", limit=4096)


def parse_export_json(text: str | bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ExportError("DUPLICATE_FIELD", f"duplicate JSON member {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise ExportError("NON_FINITE_NUMBER", f"JSON constant {value!r} is not allowed")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except ExportError:
        raise
    except (TypeError, ValueError) as error:
        raise ExportError("MALFORMED_JSON", str(error)) from error
    validate_export_result(value)
    return value


class DeterministicFixtureRenderer:
    """Offline renderer that exercises temp/final publication and validation."""

    def __init__(self, *, fail_with: RenderBackendError | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[Path] = []

    def render(self, request: RenderRequest) -> RenderedOutput:
        self.calls.append(request.temporary_path)
        if self.fail_with is not None:
            raise self.fail_with
        payload = {
            "fixture": "dubflow-mp4",
            "source": request.source.content_hash,
            "subtitle": request.subtitle.content_hash if request.subtitle is not None else None,
            "dub_audio": request.dub_audio.content_hash if request.dub_audio is not None else None,
            "audio_mode": request.audio_mode,
            "profile": request.config.requested_profile,
        }
        request.temporary_path.write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return RenderedOutput(
            "mp4",
            "h264",
            "aac" if request.audio_mode in {"original", "dubbed"} else None,
            request.source.width,
            request.source.height,
            request.source.rotation,
            request.source.duration,
        )


class LocalExportAdapter:
    """Publish only validated output and editable-pack artifacts."""

    def __init__(
        self,
        backend: RenderBackend,
        *,
        config: ExportConfig,
        provenance: ExportProvenance,
        fallback_backend: RenderBackend | None = None,
        fallback_profile: str = "cpu",
    ) -> None:
        self.backend = backend
        self.fallback_backend = fallback_backend
        self.config = config
        self.provenance = provenance
        self.fallback_profile = fallback_profile
        if fallback_profile not in {"cpu", "gpu", "fixture"}:
            raise ExportError("INVALID_PROFILE", "fallback profile is unsupported")
        if provenance.config_hash != config.to_hash():
            raise ExportError("PROVENANCE_CONFIG_MISMATCH", "provenance config hash differs from export config")

    def export(self, request: ExportRequest) -> ExportResult:
        input_hash = request.input_hash()
        if input_hash != self.provenance.input_hash:
            raise ExportError("PROVENANCE_INPUT_MISMATCH", "export input hash differs from provenance")
        output_path = _safe_path(request.output_path, "request.output_path")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and not self.config.overwrite:
            raise ExportError("OUTPUT_EXISTS", "final output exists and overwrite is disabled")
        failures: list[ExportFailure] = []
        warnings: list[str] = []
        selected_provenance = self.provenance
        fallback_used = False
        rendered: RenderedOutput | None = None
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{output_path.stem}.",
                suffix=".partial",
                dir=str(output_path.parent),
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
            render_request = RenderRequest(request, temporary_path, self.config)
            rendered, backend_failures, attempts = self._invoke(self.backend, render_request)
            failures.extend(ExportFailure.from_error(item) for item in backend_failures)
            validation_error = self._validate_rendered(rendered, render_request) if rendered is not None else None
            if validation_error is not None:
                failures.append(ExportFailure.from_error(validation_error))
                rendered = None
            if rendered is not None and (not temporary_path.is_file() or temporary_path.stat().st_size == 0):
                failures.append(
                    ExportFailure(
                        "RENDER_OUTPUT_MISSING",
                        max(1, attempts),
                        False,
                        "renderer did not create a non-empty temporary output",
                        False,
                    )
                )
                rendered = None
            if rendered is None and self.fallback_backend is not None:
                fallback_used = True
                fallback_rendered, fallback_failures, fallback_attempts = self._invoke(self.fallback_backend, render_request)
                attempts = max(attempts, fallback_attempts)
                failures.extend(ExportFailure.from_error(item, fallback_used=True) for item in fallback_failures)
                failures[:] = [replace(item, fallback_used=True) for item in failures]
                fallback_error = self._validate_rendered(fallback_rendered, render_request) if fallback_rendered is not None else None
                if fallback_error is not None:
                    failures.append(ExportFailure.from_error(fallback_error, fallback_used=True))
                    fallback_rendered = None
                if fallback_rendered is not None and (not temporary_path.is_file() or temporary_path.stat().st_size == 0):
                    failures.append(
                        ExportFailure(
                            "RENDER_OUTPUT_MISSING",
                            max(1, attempts),
                            False,
                            "fallback renderer did not create a non-empty temporary output",
                            True,
                        )
                    )
                    fallback_rendered = None
                if fallback_rendered is not None:
                    rendered = fallback_rendered
                    selected_provenance = self._fallback_provenance()
            if rendered is None:
                raise ExportStageError(tuple(failures))
            assert temporary_path is not None
            # A backend may have returned metadata without writing a file. This
            # is a hard failure; publishing a stale path would violate export
            # validity.
            if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                failure = ExportFailure("RENDER_OUTPUT_MISSING", max(1, attempts), False, "renderer did not create a non-empty temporary output", fallback_used)
                failures.append(failure)
                raise ExportStageError(tuple(failures))
            output_hash = _hash_bytes(temporary_path.read_bytes())
            try:
                os.replace(temporary_path, output_path)
            except OSError as error:
                failures.append(ExportFailure("FINALIZE_FAILED", max(1, attempts), True, _safe_condition(error), fallback_used))
                raise ExportStageError(tuple(failures)) from error
            temporary_path = None
            assets = [asset.to_dict() for asset in (request.subtitle, request.dub_audio) if asset is not None]
            output = {
                "path": str(output_path),
                "content_hash": output_hash,
                "container": rendered.container,
                "video_codec": rendered.video_codec,
                "audio_codec": rendered.audio_codec,
                "width": rendered.width,
                "height": rendered.height,
                "rotation": rendered.rotation,
                "duration_start": rendered.duration.start.to_dict(),
                "duration_end": rendered.duration.end.to_dict(),
            }
            pack_path = output_path.with_name(output_path.name + ".editable-pack.json")
            pack = {
                "path": str(pack_path),
                "assets": assets,
                "source": request.source.to_dict(),
                "output": output,
                "audio_mode": request.audio_mode,
                "provenance": selected_provenance.to_dict(),
                "qc": {"validated": True, "warnings": list(warnings)},
            }
            pack_bytes = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            pack_temp = pack_path.with_name(f".{pack_path.name}.partial")
            try:
                pack_temp.write_bytes(pack_bytes)
                os.replace(pack_temp, pack_path)
            except OSError as error:
                pack_temp.unlink(missing_ok=True)
                failures.append(ExportFailure("PACK_WRITE_FAILED", max(1, attempts), True, _safe_condition(error), fallback_used))
                raise ExportStageError(tuple(failures)) from error
            editable_pack = {"path": str(pack_path), "content_hash": _hash_bytes(pack_bytes), "assets": assets}
            if failures:
                warnings.append("export is degraded: a fallback renderer was used")
            if fallback_used and selected_provenance is self.provenance:
                selected_provenance = self._fallback_provenance()
            result = ExportResult(request.audio_mode, output, editable_pack, selected_provenance, tuple(warnings), tuple(failures))
            validate_export_result(result.to_dict())
            return result
        except ExportStageError:
            raise
        finally:
            if temporary_path is not None and temporary_path.exists():
                quarantine = temporary_path.with_suffix(temporary_path.suffix + ".failed")
                try:
                    os.replace(temporary_path, quarantine)
                except OSError:
                    temporary_path.unlink(missing_ok=True)

    def _invoke(self, backend: RenderBackend, request: RenderRequest) -> tuple[RenderedOutput | None, list[ExportError], int]:
        failures: list[ExportError] = []
        previous_condition: str | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = backend.render(request)
                if not isinstance(result, RenderedOutput):
                    failures.append(ExportError("MALFORMED_RENDER_RESULT", "renderer did not return RenderedOutput", attempt=attempt))
                    return None, failures, attempt
                return result, failures, attempt
            except ExportError as error:
                failure = ExportError(error.code, error.condition, retryable=error.retryable, attempt=attempt, fallback_used=error.fallback_used)
                failures.append(failure)
                if previous_condition == error.condition:
                    failures.append(ExportError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", attempt=attempt))
                    return None, failures, attempt
                if not error.retryable or attempt >= self.config.max_attempts:
                    return None, failures, attempt
                previous_condition = error.condition
            except Exception as error:
                condition = _safe_condition(error)
                failures.append(ExportError("RENDER_BACKEND_FAILED", condition, retryable=True, attempt=attempt))
                if previous_condition == condition:
                    failures.append(ExportError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change", attempt=attempt))
                    return None, failures, attempt
                if attempt >= self.config.max_attempts:
                    return None, failures, attempt
                previous_condition = condition
        return None, failures, self.config.max_attempts

    @staticmethod
    def _validate_rendered(rendered: RenderedOutput | None, request: RenderRequest) -> ExportError | None:
        if rendered is None:
            return None
        try:
            if rendered.container != "mp4" or rendered.video_codec != "h264" or rendered.audio_codec not in {"aac", None}:
                raise ExportError("OUTPUT_PROFILE_INVALID", "renderer did not produce MP4/H.264/AAC-compatible metadata")
            if rendered.audio_codec == "aac" and request.audio_mode == "none":
                raise ExportError("OUTPUT_AUDIO_INVALID", "renderer created an audio track when audio mode is none")
            if rendered.audio_codec is None and request.audio_mode != "none":
                raise ExportError("OUTPUT_AUDIO_MISSING", "renderer omitted the requested audio track")
            if rendered.width != request.source.width or rendered.height != request.source.height:
                raise ExportError("OUTPUT_DIMENSION_CHANGED", "renderer changed source dimensions")
            if rendered.rotation != request.source.rotation:
                raise ExportError("OUTPUT_ROTATION_CHANGED", "renderer changed source rotation metadata")
            if rendered.duration.start != request.source.duration.start or rendered.duration.end != request.source.duration.end:
                raise ExportError("OUTPUT_TIMELINE_CHANGED", "renderer changed source duration boundaries")
            return None
        except ExportError as error:
            return error
        except Exception as error:
            return ExportError("MALFORMED_RENDER_RESULT", _safe_condition(error))

    def _fallback_provenance(self) -> ExportProvenance:
        return replace(
            self.provenance,
            renderer_id="fallback-software",
            renderer_version="1",
            hardware_profile=self.fallback_profile,
            fallback_reason="primary renderer failed or returned invalid output; software fallback selected",
        )
