"""Portable, deterministic guards for untrusted media and package input."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


MAX_RELATIVE_PATH_UTF16 = 32_767
MAX_FILENAME_UTF16 = 240
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "session",
}
_SENSITIVE_TEXT = re.compile(
    r"(?i)(\b(?:authorization|cookie|set-cookie|token|api[_-]?key|secret|password|session)\b\s*[:=]\s*)([^,;\s}\"]+)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)([^,;\s}\"]+)")


class SecurityBoundaryError(ValueError):
    """Input failed a boundary policy and must not reach an I/O primitive."""


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _reserved(component: str) -> bool:
    return component.split(".", 1)[0].upper() in _RESERVED


def validate_relative_path(value: str, *, max_utf16: int = MAX_RELATIVE_PATH_UTF16) -> str:
    """Return slash-normalized ``value`` if it is safe beneath an owned root."""

    if not isinstance(value, str) or not value:
        raise SecurityBoundaryError("relative path must not be empty")
    if _utf16_length(value) > max_utf16:
        raise SecurityBoundaryError("relative path exceeds UTF-16 limit")
    if "\x00" in value or any(ord(char) < 32 for char in value):
        raise SecurityBoundaryError("control characters are not allowed")
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or "://" in normalized
        or (len(normalized) >= 2 and normalized[1] == ":")
    ):
        raise SecurityBoundaryError("absolute, UNC, and drive-qualified paths are not allowed")
    components = normalized.split("/")
    for component in components:
        if not component:
            raise SecurityBoundaryError("empty path component")
        if component == "..":
            raise SecurityBoundaryError("parent traversal is not allowed")
        if component == "." or component.endswith((".", " ")):
            raise SecurityBoundaryError("dot, trailing-dot, and trailing-space components are not allowed")
        if ":" in component or _reserved(component):
            raise SecurityBoundaryError(f"invalid Windows path component: {component!r}")
    return normalized


def sanitize_filename(value: str, fallback: str = "file") -> str:
    """Turn one display name into one Windows-safe component."""

    if not isinstance(value, str):
        value = ""
    output = "".join(
        "_" if (ord(char) < 32 or char in '/\\:*?"<>|') else char for char in value
    ).rstrip(". ")
    output = output or fallback or "file"
    if _reserved(output):
        output = "_" + output
    encoded = output.encode("utf-16-le")
    if len(encoded) // 2 > MAX_FILENAME_UTF16:
        encoded = encoded[: MAX_FILENAME_UTF16 * 2]
        if len(encoded) % 2:
            encoded = encoded[:-1]
        output = encoded.decode("utf-16-le", errors="ignore") or "file"
    return output


def allocate_filename(value: str, used: set[str], fallback: str = "file") -> str:
    """Allocate a deterministic case-insensitive, collision-safe filename."""

    base = sanitize_filename(value, fallback)
    stem, dot, extension = base.rpartition(".")
    if not stem:
        stem, extension, dot = base, "", ""
    candidate = base
    suffix = 1
    folded = {item.casefold() for item in used}
    while candidate.casefold() in folded:
        marker = f" ({suffix})"
        budget = MAX_FILENAME_UTF16 - _utf16_length(marker + (dot + extension if dot else ""))
        truncated = stem
        while _utf16_length(truncated) > max(1, budget):
            truncated = truncated[:-1]
        candidate = f"{truncated}{marker}{dot}{extension}"
        candidate = sanitize_filename(candidate, fallback)
        suffix += 1
    used.add(candidate)
    return candidate


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 100_000
    max_member_bytes: int = 4 * 1024 * 1024 * 1024
    max_total_bytes: int = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    kind: str = "file"
    size_bytes: int = 0


def plan_archive_extract(
    staging_root: Path | str,
    members: Iterable[ArchiveMember],
    limits: ArchiveLimits = ArchiveLimits(),
) -> tuple[Path, ...]:
    """Plan safe archive destinations, rejecting Zip Slip, links, and bombs."""

    materialized = tuple(members)
    if len(materialized) > limits.max_members:
        raise SecurityBoundaryError("archive member limit exceeded")
    total = 0
    seen: set[str] = set()
    root = Path(staging_root)
    destinations: list[Path] = []
    for member in materialized:
        if member.kind.lower() in {"symlink", "hardlink", "link"}:
            raise SecurityBoundaryError("archive links are not allowed")
        if member.size_bytes < 0 or member.size_bytes > limits.max_member_bytes:
            raise SecurityBoundaryError("archive member size exceeds limit")
        total += member.size_bytes
        if total > limits.max_total_bytes:
            raise SecurityBoundaryError("archive total size exceeds limit")
        normalized = validate_relative_path(member.path)
        key = normalized.casefold()
        if key in seen:
            raise SecurityBoundaryError(f"duplicate archive member: {normalized}")
        seen.add(key)
        destinations.append(root.joinpath(*normalized.split("/")))
    return tuple(destinations)


@dataclass(frozen=True)
class SubprocessPlan:
    argv: tuple[str, ...]
    shell: bool = False


def build_subprocess_plan(executable: str, args: Iterable[str] = ()) -> SubprocessPlan:
    """Build an argv tuple; shell interpolation is structurally unavailable."""

    if not isinstance(executable, str) or not executable:
        raise SecurityBoundaryError("executable must not be empty")
    argv = (executable, *tuple(args))
    if any("\x00" in value for value in argv):
        raise SecurityBoundaryError("NUL bytes are not allowed in argv")
    return SubprocessPlan(argv=argv, shell=False)


def redact_diagnostics(value: Any) -> Any:
    """Return a deep redacted copy of mappings, lists, and log strings."""

    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if str(key).casefold() in _SENSITIVE_KEYS else redact_diagnostics(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_diagnostics(item) for item in value)
    if isinstance(value, str):
        value = _BEARER.sub(r"\1[REDACTED]", value)
        return _SENSITIVE_TEXT.sub(r"\1[REDACTED]", value)
    return value


def verify_sha256(path: Path | str, expected: str, *, expected_size: int | None = None) -> bool:
    """Verify a file's hash and optional exact byte size without loading it all."""

    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SecurityBoundaryError("expected SHA-256 must be lowercase hexadecimal")
    file_path = Path(path)
    if expected_size is not None and file_path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected
