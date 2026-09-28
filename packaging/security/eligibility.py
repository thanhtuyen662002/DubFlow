"""One-click eligibility and supply-chain checks for runtime/model packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any, Mapping

from engine.dubflow.security import SecurityBoundaryError, verify_sha256


class PackageClass(str, Enum):
    WEIGHTS_ONLY = "weights-only"
    CODE_BEARING = "code-bearing"


@dataclass(frozen=True)
class PackageManifest:
    package_id: str
    version: str
    source: str
    sha256: str
    size_bytes: int
    package_class: PackageClass
    license_id: str
    redistributable: bool
    requires_credentials: bool = False
    requires_clickthrough: bool = False
    fallback: str | None = None
    compatibility: Mapping[str, str] = field(default_factory=dict)
    signed: bool = False
    code_audit_id: str | None = None
    rollback_version: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackageManifest":
        required = ("id", "version", "source", "sha256", "size_bytes", "class", "license", "redistributable")
        missing = [key for key in required if key not in data]
        if missing:
            raise SecurityBoundaryError(f"manifest missing fields: {', '.join(missing)}")
        try:
            package_class = PackageClass(str(data["class"]))
        except ValueError as exc:
            raise SecurityBoundaryError("manifest class must be weights-only or code-bearing") from exc
        size_bytes = data["size_bytes"]
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > 2**64 - 1
        ):
            raise SecurityBoundaryError("manifest size_bytes must be a u64 integer")
        for boolean_field in ("redistributable", "requires_credentials", "requires_clickthrough", "signed"):
            if boolean_field in data and not isinstance(data[boolean_field], bool):
                raise SecurityBoundaryError(f"manifest {boolean_field} must be boolean")
        if not re.fullmatch(r"[0-9a-f]{64}", str(data["sha256"])):
            raise SecurityBoundaryError("manifest sha256 must be lowercase hexadecimal")
        return cls(
            package_id=str(data["id"]),
            version=str(data["version"]),
            source=str(data["source"]),
            sha256=str(data["sha256"]),
            size_bytes=size_bytes,
            package_class=package_class,
            license_id=str(data["license"]),
            redistributable=bool(data["redistributable"]),
            requires_credentials=bool(data.get("requires_credentials", False)),
            requires_clickthrough=bool(data.get("requires_clickthrough", False)),
            fallback=str(data["fallback"]) if data.get("fallback") is not None else None,
            compatibility=dict(data.get("compatibility", {})),
            signed=bool(data.get("signed", False)),
            code_audit_id=str(data["code_audit_id"]) if data.get("code_audit_id") else None,
            rollback_version=str(data["rollback_version"]) if data.get("rollback_version") else None,
        )


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_default(manifest: PackageManifest) -> Eligibility:
    reasons: list[str] = []
    if not manifest.package_id or not manifest.version or not manifest.source:
        reasons.append("identity, version, and source are required")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.sha256):
        reasons.append("package hash is not a lowercase SHA-256")
    if manifest.size_bytes < 0:
        reasons.append("package size is negative")
    if not manifest.redistributable:
        reasons.append("package is not redistributable")
    if manifest.requires_credentials or manifest.requires_clickthrough:
        reasons.append("manual credentials/click-through cannot be a mandatory default")
    if manifest.package_class is PackageClass.CODE_BEARING:
        if not manifest.signed:
            reasons.append("code-bearing package is not signed")
        if not manifest.code_audit_id:
            reasons.append("code-bearing package lacks an audited approval id")
    return Eligibility(not reasons, tuple(reasons))


def verify_package(path: Path | str, manifest: PackageManifest) -> bool:
    """Verify a staged package before activation or rollback selection."""

    return verify_sha256(path, manifest.sha256, expected_size=manifest.size_bytes)
