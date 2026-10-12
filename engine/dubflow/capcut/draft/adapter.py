"""Experimental exact-version CapCut draft adapter with safe import-pack fallback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Protocol


_VERSION = re.compile(
    r"^(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})"
    r"(?:\.(0|[1-9][0-9]{0,9}))?$"
)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class DirectDraftError(ValueError):
    pass


class DraftStatus(str, Enum):
    DIRECT = "DIRECT"
    FALLBACK_IMPORT_PACK = "FALLBACK_IMPORT_PACK"


@total_ordering
@dataclass(frozen=True)
class CapCutVersion:
    major: int
    minor: int
    patch: int
    build: int | None = None

    @classmethod
    def parse(cls, value: str) -> "CapCutVersion":
        match = _VERSION.fullmatch(value)
        if not match:
            raise DirectDraftError("CapCut version must be exact major.minor.patch[.build]")
        major, minor, patch, build = match.groups()
        return cls(int(major), int(minor), int(patch), None if build is None else int(build))

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.build is None else f"{base}.{self.build}"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CapCutVersion):
            return NotImplemented
        # A buildless legacy version is distinct from every concrete build.
        # Ordering is retained for callers, never used to authorize compatibility.
        return (self.major, self.minor, self.patch, -1 if self.build is None else self.build) < (
            other.major, other.minor, other.patch, -1 if other.build is None else other.build
        )


@dataclass(frozen=True)
class DraftResult:
    status: DraftStatus
    path: Path | None
    fallback_pack: Path
    reason: str | None = None


class DraftBackend(Protocol):
    def generate(self, *, version: CapCutVersion, pack_root: Path, output_path: Path) -> None:
        ...


def _contained(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DirectDraftError("direct draft path is outside the controlled directory") from exc
    return candidate


def _manifest_hash(pack_root: Path) -> str:
    manifest = pack_root / "manifest.json"
    if not manifest.is_file():
        raise DirectDraftError("import pack manifest is missing")
    return "sha256:" + sha256(manifest.read_bytes()).hexdigest()


def _validate_draft(path: Path, *, version: CapCutVersion, pack_root: Path) -> None:
    if not path.is_file():
        raise DirectDraftError("direct draft was not created")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DirectDraftError("direct draft is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("kind") != "capcut_direct_draft" or value.get("capcut_version") != str(version):
        raise DirectDraftError("direct draft metadata does not match the tested version")
    fields = {"schema_version", "kind", "capcut_version", "pack_manifest_hash", "assets"}
    schema = value.get("schema_version")
    if set(value) != fields or type(schema) is not int or schema not in (1, 2):
        raise DirectDraftError("direct draft envelope schema is unsupported")
    # Published v1 admits exactly three components. Never silently extend it
    # for a concrete build or rewrite an archived object during validation.
    if schema == 1 and version.build is not None:
        raise DirectDraftError("direct draft v1 cannot represent an exact build")
    if value.get("pack_manifest_hash") != _manifest_hash(pack_root) or not _HASH.fullmatch(str(value.get("pack_manifest_hash"))):
        raise DirectDraftError("direct draft does not bind the import pack manifest")
    assets = value.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 256 or any(
        not isinstance(asset, str) or not 1 <= len(asset) <= 4096 for asset in assets
    ):
        raise DirectDraftError("direct draft asset inventory is invalid")
    expected_assets = [item["relative_path"] for item in json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))["assets"]]
    if assets != expected_assets:
        raise DirectDraftError("direct draft asset inventory differs from the pinned pack")


class FixtureDraftBackend:
    """Deterministic backend used by tests; it does not launch CapCut."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def generate(self, *, version: CapCutVersion, pack_root: Path, output_path: Path) -> None:
        if self.fail:
            raise DirectDraftError("fixture CapCut adapter failed")
        output_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "kind": "capcut_direct_draft",
                    "capcut_version": str(version),
                    "pack_manifest_hash": _manifest_hash(pack_root),
                    "assets": [item["relative_path"] for item in json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))["assets"]],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )


class VersionedDraftAdapter:
    def __init__(self, supported_versions: set[str]) -> None:
        self.supported_versions = frozenset(str(CapCutVersion.parse(value)) for value in supported_versions)

    def generate_or_fallback(
        self,
        *,
        installed_version: str | None,
        pack_root: Path,
        controlled_root: Path,
        backend: DraftBackend,
    ) -> DraftResult:
        if not pack_root.is_dir() or not (pack_root / "manifest.json").is_file():
            raise DirectDraftError("a validated import pack is required before direct draft generation")
        fallback_pack = pack_root.resolve()
        if installed_version is None or installed_version not in self.supported_versions:
            return DraftResult(DraftStatus.FALLBACK_IMPORT_PACK, None, fallback_pack, "CAPCUT_VERSION_UNSUPPORTED")
        version = CapCutVersion.parse(installed_version)
        controlled_root.mkdir(parents=True, exist_ok=True)
        output_path = _contained(controlled_root, controlled_root / f"dubflow-{version}.draft.json")
        descriptor, partial_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".partial", dir=controlled_root)
        os.close(descriptor)
        partial = Path(partial_name)
        try:
            backend.generate(version=version, pack_root=fallback_pack, output_path=partial)
            _validate_draft(partial, version=version, pack_root=fallback_pack)
            partial.replace(output_path)
            return DraftResult(DraftStatus.DIRECT, output_path, fallback_pack)
        except Exception as error:
            partial.unlink(missing_ok=True)
            return DraftResult(DraftStatus.FALLBACK_IMPORT_PACK, None, fallback_pack, str(error))
