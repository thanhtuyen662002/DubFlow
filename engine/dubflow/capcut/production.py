"""Production-facing editable export and optional CapCut handoff.

The portable import pack is the durable output.  This module is deliberately
small: worker code can call one boundary after the canonical MP4/QC artifacts
are valid, and any CapCut integration failure is returned as a scoped fallback
without touching the canonical output or deleting the pack.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from .draft.adapter import DraftBackend, DraftResult, VersionedDraftAdapter
from .import_pack import ImportPackRequest, ImportPackResult, create_import_pack, validate_import_pack


@dataclass(frozen=True)
class ProductionPackResult:
    pack: ImportPackResult
    manifest_hash: str
    direct_draft: DraftResult | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "pack_path": str(self.pack.root),
            "manifest_hash": self.manifest_hash,
            "manifest": self.pack.manifest,
            "direct_draft": None,
        }
        if self.direct_draft is not None:
            result["direct_draft"] = {
                "status": self.direct_draft.status.value,
                "path": str(self.direct_draft.path) if self.direct_draft.path else None,
                "fallback_pack": str(self.direct_draft.fallback_pack),
                "reason": self.direct_draft.reason,
            }
        return result


def _manifest_hash(pack_root: Path) -> str:
    from hashlib import sha256

    return "sha256:" + sha256((pack_root / "manifest.json").read_bytes()).hexdigest()


def create_production_pack(
    *,
    pack_id: str,
    source_path: Path,
    output_root: Path,
    subtitle_path: Path | None = None,
    dub_audio_path: Path | None = None,
    original_audio_path: Path | None = None,
    timeline: Mapping[str, Any] | None = None,
    installed_capcut_version: str | None = None,
    supported_capcut_versions: set[str] | frozenset[str] = frozenset(),
    controlled_capcut_root: Path | None = None,
    draft_backend: DraftBackend | None = None,
) -> ProductionPackResult:
    """Create and independently validate the production editable handoff.

    Direct handoff is attempted only when all of the following are explicit:
    an installed version, a non-empty tested-version set, a controlled output
    directory, and a caller-supplied backend.  Otherwise the result is still a
    successful portable pack with no direct draft attempt.
    """

    pack = create_import_pack(
        ImportPackRequest(
            pack_id=pack_id,
            source_path=source_path,
            subtitle_path=subtitle_path,
            dub_audio_path=dub_audio_path,
            original_audio_path=original_audio_path,
            timeline=timeline,
        ),
        output_root,
    )
    validated = validate_import_pack(pack.root)
    if validated != pack.manifest:
        raise RuntimeError("validated import pack differs from the published manifest")

    direct: DraftResult | None = None
    if installed_capcut_version is not None or draft_backend is not None or controlled_capcut_root is not None:
        # VersionedDraftAdapter always returns a safe fallback for unknown
        # versions and cleans partial output.  Do not infer a CapCut install
        # or pass an uncontrolled user path implicitly.
        if controlled_capcut_root is None:
            controlled_capcut_root = output_root / "capcut-controlled"
        direct = VersionedDraftAdapter(set(supported_capcut_versions)).generate_or_fallback(
            installed_version=installed_capcut_version,
            pack_root=pack.root,
            controlled_root=controlled_capcut_root,
            backend=draft_backend if draft_backend is not None else _UnavailableDraftBackend(),
        )
    return ProductionPackResult(pack, _manifest_hash(pack.root), direct)


class _UnavailableDraftBackend:
    """Fail closed when no tested CapCut backend is installed."""

    def generate(self, *, version, pack_root: Path, output_path: Path) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError(f"no app-owned CapCut backend is available for {version}")


def detect_capcut_version(env: Mapping[str, str] | None = None) -> str | None:
    """Read an explicitly supplied version hint without probing arbitrary paths.

    The desktop may supply a version obtained from a permission-safe native
    registry adapter.  Environment input is intentionally opt-in for tests and
    enterprise packaging; absent/blank values mean that direct handoff is
    unavailable and the portable pack remains the advertised result.
    """

    values = env if env is not None else os.environ
    value = values.get("DUBFLOW_CAPCUT_VERSION")
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["ProductionPackResult", "create_production_pack", "detect_capcut_version"]
