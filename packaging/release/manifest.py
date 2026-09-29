"""Versioned release manifest and file-integrity contract.

The release manifest deliberately keeps byte counts as decimal strings. A
release can contain files larger than JavaScript's safe integer range, and a
manifest must not become another precision boundary between the builder,
bootstrapper, and desktop shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_U64 = (1 << 64) - 1
_HEX = re.compile(r"^[0-9a-f]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ManifestError(ValueError):
    """Raised when release metadata is malformed or unsafe."""


def parse_u64(value: Any, field: str) -> int:
    """Parse a canonical decimal u64 represented on the wire as a string."""

    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise ManifestError(f"{field} must be a canonical decimal string")
    parsed = int(value)
    if parsed > MAX_U64:
        raise ManifestError(f"{field} exceeds u64")
    return parsed


def _safe_relative_path(value: Any, field: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{field} must use a non-empty POSIX relative path")
    path = value.replace("/", "/")
    candidate = Path(path)
    if candidate.is_absolute() or ":" in path:
        raise ManifestError(f"{field} must be relative")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ManifestError(f"{field} contains an unsafe path component")
    for part in parts:
        if part.endswith((".", " ")):
            raise ManifestError(f"{field} contains a Windows-unsafe trailing character")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ManifestError(f"{field} contains a Windows reserved name")
    return "/".join(parts)


def _require_string(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ManifestError(f"{field} has an invalid format")
    return value


def _parse_timestamp(value: Any) -> str:
    timestamp = _require_string(value, "build_timestamp_utc")
    if not timestamp.endswith("Z"):
        raise ManifestError("build_timestamp_utc must use UTC Z notation")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError("build_timestamp_utc is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ManifestError("build_timestamp_utc must be UTC")
    return timestamp


@dataclass(frozen=True)
class ReleaseArtifact:
    """One file in the staged release payload."""

    artifact_id: str
    path: str
    size_bytes: int
    sha256: str
    executable: bool = False

    def __post_init__(self) -> None:
        _require_string(self.artifact_id, "artifact_id", _IDENTIFIER)
        normalized = _safe_relative_path(self.path)
        object.__setattr__(self, "path", normalized)
        if type(self.size_bytes) is not int or self.size_bytes < 0 or self.size_bytes > MAX_U64:
            raise ManifestError("size_bytes must be a u64 integer")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ManifestError("sha256 must be lowercase hexadecimal SHA-256")
        if type(self.executable) is not bool:
            raise ManifestError("executable must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.artifact_id,
            "path": self.path,
            "size_bytes": str(self.size_bytes),
            "sha256": self.sha256,
            "executable": self.executable,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseArtifact":
        if not isinstance(value, Mapping):
            raise ManifestError("artifacts must contain objects")
        return cls(
            _require_string(value.get("id"), "artifacts.id", _IDENTIFIER),
            _safe_relative_path(value.get("path"), "artifacts.path"),
            parse_u64(value.get("size_bytes"), "artifacts.size_bytes"),
            _require_string(value.get("sha256"), "artifacts.sha256", _SHA256),
            value.get("executable", False),
        )


@dataclass(frozen=True)
class ReleaseManifest:
    """Machine-readable metadata for one downloadable bundle."""

    version: str
    source_sha: str
    runtime_version: str
    package_manifest_version: str
    platform: str
    architecture: str
    build_timestamp_utc: str
    release_channel: str
    signature_required: bool
    signature_algorithm: str
    signature_key_id: str
    signature_value: str
    artifacts: tuple[ReleaseArtifact, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ManifestError(f"unsupported release manifest schema: {self.schema_version}")
        _require_string(self.version, "version", _VERSION)
        if not isinstance(self.source_sha, str) or not _SOURCE_SHA.fullmatch(self.source_sha):
            raise ManifestError("source_sha must be a 40- or 64-character lowercase Git SHA")
        _require_string(self.runtime_version, "runtime_version", _VERSION)
        _require_string(self.package_manifest_version, "package_manifest_version", _VERSION)
        if self.platform != "windows":
            raise ManifestError("release platform must be windows")
        if self.architecture != "x86_64":
            raise ManifestError("release architecture must be x86_64")
        _parse_timestamp(self.build_timestamp_utc)
        if self.release_channel not in {"candidate", "stable"}:
            raise ManifestError("release_channel must be candidate or stable")
        if type(self.signature_required) is not bool:
            raise ManifestError("signature_required must be boolean")
        _require_string(self.signature_algorithm, "signature_algorithm", _IDENTIFIER)
        _require_string(self.signature_key_id, "signature_key_id", _IDENTIFIER)
        if not isinstance(self.signature_value, str):
            raise ManifestError("signature_value must be a string")
        if self.signature_required and (
            self.signature_algorithm == "none" or not self.signature_value
        ):
            raise ManifestError("a signed release requires a non-empty signature")
        if not self.artifacts:
            raise ManifestError("release artifacts must be non-empty")
        ids: set[str] = set()
        paths: set[str] = set()
        folded_paths: set[str] = set()
        for artifact in self.artifacts:
            if artifact.artifact_id in ids:
                raise ManifestError(f"duplicate artifact id: {artifact.artifact_id}")
            if artifact.path in paths:
                raise ManifestError(f"duplicate artifact path: {artifact.path}")
            folded = artifact.path.casefold()
            if folded in folded_paths:
                raise ManifestError(f"Windows case-folded artifact path collision: {artifact.path}")
            ids.add(artifact.artifact_id)
            paths.add(artifact.path)
            folded_paths.add(folded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "source_sha": self.source_sha,
            "runtime_version": self.runtime_version,
            "package_manifest_version": self.package_manifest_version,
            "platform": self.platform,
            "architecture": self.architecture,
            "build_timestamp_utc": self.build_timestamp_utc,
            "release_channel": self.release_channel,
            "security": {
                "signature_required": self.signature_required,
                "algorithm": self.signature_algorithm,
                "key_id": self.signature_key_id,
                "value": self.signature_value,
            },
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseManifest":
        if not isinstance(value, Mapping):
            raise ManifestError("release manifest must be an object")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError("unsupported or missing release manifest schema_version")
        security = value.get("security")
        if not isinstance(security, Mapping):
            raise ManifestError("release manifest security metadata is required")
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list):
            raise ManifestError("release manifest artifacts must be a list")
        return cls(
            _require_string(value.get("version"), "version", _VERSION),
            _require_string(value.get("source_sha"), "source_sha", _SOURCE_SHA),
            _require_string(value.get("runtime_version"), "runtime_version", _VERSION),
            _require_string(value.get("package_manifest_version"), "package_manifest_version", _VERSION),
            _require_string(value.get("platform"), "platform", _IDENTIFIER),
            _require_string(value.get("architecture"), "architecture", _IDENTIFIER),
            _parse_timestamp(value.get("build_timestamp_utc")),
            _require_string(value.get("release_channel"), "release_channel", _IDENTIFIER),
            security.get("signature_required"),
            _require_string(security.get("algorithm"), "security.algorithm", _IDENTIFIER),
            _require_string(security.get("key_id"), "security.key_id", _IDENTIFIER),
            security.get("value", ""),
            tuple(ReleaseArtifact.from_mapping(item) for item in artifacts),
        )


def hash_file(path: Path | str) -> str:
    """Return a lowercase SHA-256 digest using bounded memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_manifest(manifest: ReleaseManifest, path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def load_manifest(path: Path | str) -> ReleaseManifest:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"unable to read release manifest: {target}") from exc
    return ReleaseManifest.from_mapping(value)


__all__ = [
    "ManifestError",
    "ReleaseArtifact",
    "ReleaseManifest",
    "SCHEMA_VERSION",
    "dump_manifest",
    "hash_file",
    "load_manifest",
    "parse_u64",
]
