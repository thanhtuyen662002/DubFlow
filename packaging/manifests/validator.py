"""Schema and one-click policy validation for model/runtime catalogs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


MAX_U64 = 2**64 - 1
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
REQUIRED_ENTRY_FIELDS = {
    "id", "version", "kind", "package_class", "source", "sha256", "size_bytes",
    "hardware", "license", "redistributable", "requires_credentials", "requires_clickthrough",
    "fallback", "compatibility", "update", "trust", "default_profile",
}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]


def parse_u64(value: Any, field: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise ManifestError(f"{field} must be a canonical decimal string")
    parsed = int(value)
    if parsed > MAX_U64:
        raise ManifestError(f"{field} exceeds u64")
    return parsed


def _require_bool(entry: Mapping[str, Any], field: str) -> None:
    if not isinstance(entry.get(field), bool):
        raise ManifestError(f"{field} must be boolean")


def _validate_entry(entry: Mapping[str, Any]) -> None:
    missing = REQUIRED_ENTRY_FIELDS - entry.keys()
    if missing:
        raise ManifestError(f"entry missing fields: {', '.join(sorted(missing))}")
    if not ID_RE.fullmatch(str(entry["id"])):
        raise ManifestError("entry id is not a safe manifest identifier")
    for field in ("version", "source"):
        if not isinstance(entry[field], str) or not entry[field]:
            raise ManifestError(f"{field} must be a non-empty string")
    if entry["kind"] not in {"model", "runtime", "ffmpeg"}:
        raise ManifestError("kind must be model, runtime, or ffmpeg")
    if entry["package_class"] not in {"weights-only", "code-bearing"}:
        raise ManifestError("package_class must be weights-only or code-bearing")
    if not isinstance(entry["sha256"], str) or not SHA256_RE.fullmatch(entry["sha256"]):
        raise ManifestError("sha256 must be lowercase hexadecimal")
    parse_u64(entry["size_bytes"], "size_bytes")
    hardware = entry["hardware"]
    if not isinstance(hardware, Mapping) or not hardware.get("architectures"):
        raise ManifestError("hardware.architectures must be non-empty")
    if any(not isinstance(value, str) or not value for value in hardware["architectures"]):
        raise ManifestError("hardware.architectures entries must be non-empty strings")
    parse_u64(hardware.get("min_ram_mb"), "hardware.min_ram_mb")
    parse_u64(hardware.get("min_vram_mb"), "hardware.min_vram_mb")
    license_data = entry["license"]
    if not isinstance(license_data, Mapping) or not license_data.get("spdx") or not license_data.get("distribution_decision"):
        raise ManifestError("license decision and SPDX identifier are required")
    _require_bool(license_data, "attribution_required")
    for field in ("redistributable", "requires_credentials", "requires_clickthrough", "default_profile"):
        _require_bool(entry, field)
    if entry["fallback"] is not None and (not isinstance(entry["fallback"], str) or not entry["fallback"]):
        raise ManifestError("fallback must be a non-empty id or null")
    if not isinstance(entry["compatibility"], Mapping) or not entry["compatibility"]:
        raise ManifestError("compatibility constraints are required")
    update = entry["update"]
    if not isinstance(update, Mapping) or update.get("channel") not in {"stable", "experimental", "internal"}:
        raise ManifestError("update channel is invalid")
    if not isinstance(update.get("manifest_version"), str) or not update["manifest_version"]:
        raise ManifestError("update manifest_version is required")
    if update.get("rollback_version") is not None and not isinstance(update["rollback_version"], str):
        raise ManifestError("rollback_version must be string or null")
    signature = update.get("signature")
    if not isinstance(signature, Mapping) or not all(isinstance(signature.get(key), str) and signature[key] for key in ("algorithm", "key_id", "value")):
        raise ManifestError("update signature metadata is required")
    trust = entry["trust"]
    if not isinstance(trust, Mapping):
        raise ManifestError("trust metadata is required")
    _require_bool(trust, "signed")
    _require_bool(trust, "trust_remote_code")
    if trust.get("audit_id") is not None and not isinstance(trust["audit_id"], str):
        raise ManifestError("trust.audit_id must be string or null")


def validate_catalog(catalog: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(catalog, Mapping) or catalog.get("schema_version") != 1:
        raise ManifestError("catalog schema_version must be 1")
    if not isinstance(catalog.get("catalog_id"), str) or not catalog["catalog_id"]:
        raise ManifestError("catalog_id is required")
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("catalog entries must be non-empty")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ManifestError("catalog entries must be objects")
        _validate_entry(entry)
        if entry["id"] in seen:
            raise ManifestError(f"duplicate entry id: {entry['id']}")
        seen.add(entry["id"])
    if not any(entry["kind"] == "ffmpeg" for entry in entries):
        raise ManifestError("catalog must include an FFmpeg distribution entry")
    return tuple(entries)


def load_catalog(path: Path | str) -> tuple[Mapping[str, Any], ...]:
    with Path(path).open(encoding="utf-8") as stream:
        return validate_catalog(json.load(stream))


def evaluate_default_eligibility(entry: Mapping[str, Any]) -> Eligibility:
    _validate_entry(entry)
    reasons: list[str] = []
    if not entry["default_profile"]:
        reasons.append("entry is not selected for the default profile")
    if not entry["redistributable"]:
        reasons.append("package is not redistributable")
    if entry["requires_credentials"] or entry["requires_clickthrough"]:
        reasons.append("credentials/click-through cannot be a mandatory one-click default")
    trust = entry["trust"]
    if trust["trust_remote_code"]:
        reasons.append("remote code trust is disabled for defaults")
    if entry["package_class"] == "code-bearing":
        if not trust["signed"]:
            reasons.append("code-bearing package is not signed")
        if not trust.get("audit_id"):
            reasons.append("code-bearing package lacks an audited approval id")
    return Eligibility(not reasons, tuple(reasons))


def verify_candidate(path: Path | str, entry: Mapping[str, Any]) -> bool:
    """Stream a candidate and verify its exact manifest size and SHA-256."""

    _validate_entry(entry)
    target = Path(path)
    if target.stat().st_size != parse_u64(entry["size_bytes"], "size_bytes"):
        return False
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == entry["sha256"]
