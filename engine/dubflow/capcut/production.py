"""Production-facing editable export and optional CapCut handoff.

The portable import pack is the durable output.  This module is deliberately
small: worker code can call one boundary after the canonical MP4/QC artifacts
are valid, and any CapCut integration failure is returned as a scoped fallback
without touching the canonical output or deleting the pack.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import json
import re
from hashlib import file_digest, sha256
from pathlib import Path
from typing import Any, Mapping

from .draft.adapter import DraftBackend, DraftResult, DraftStatus, VersionedDraftAdapter
from .import_pack import ImportPackAsset, ImportPackError, ImportPackRequest, ImportPackResult, create_import_pack, validate_import_pack


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
    additional_assets: tuple[ImportPackAsset, ...] = (),
    source_content_hash: str | None = None,
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
            additional_assets=additional_assets,
            source_content_hash=source_content_hash,
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
        if controlled_capcut_root is None and installed_capcut_version in supported_capcut_versions:
            direct = DraftResult(DraftStatus.FALLBACK_IMPORT_PACK, None, pack.root.resolve(), "CAPCUT_CONTROLLED_DIRECTORY_REQUIRED")
        else:
            direct = VersionedDraftAdapter(set(supported_capcut_versions)).generate_or_fallback(
                installed_version=installed_capcut_version,
                pack_root=pack.root,
                controlled_root=controlled_capcut_root or output_root / "capcut-controlled",
                backend=draft_backend if draft_backend is not None else _UnavailableDraftBackend(),
            )
    return ProductionPackResult(pack, _manifest_hash(pack.root), direct)


def _read_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ImportPackError("canonical metadata file is absent, linked or oversized")
    try:
        with path.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ImportPackError("canonical metadata exceeds bounded storage")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("document is not an object")
        return raw, value
    except (OSError, ValueError) as error:
        raise ImportPackError("canonical metadata is unreadable") from error


def _content_hash(value: Any) -> str:
    if not isinstance(value, str):
        raise ImportPackError("canonical content hash is absent")
    result = value if value.startswith("sha256:") else "sha256:" + value
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
        raise ImportPackError("canonical content hash is invalid")
    return result


def create_pack_from_export(
    manifest_path: Path,
    output_root: Path,
    *,
    expected_manifest_hash: str | None = None,
    installed_capcut_version: str | None = None,
    supported_capcut_versions: set[str] | frozenset[str] = frozenset(),
    controlled_capcut_root: Path | None = None,
    draft_backend: DraftBackend | None = None,
) -> ProductionPackResult:
    """Assemble all verified standard resources without modifying the export.

    The caller handles an optional-pack failure as a visible scoped downgrade.
    This function never substitutes fixture media, launches CapCut or mutates
    durable supervisor state. Every source/copy is bound to canonical hashes.
    """
    raw, document = _read_document(manifest_path)
    if expected_manifest_hash is not None and _content_hash(expected_manifest_hash) != "sha256:" + sha256(raw).hexdigest():
        raise ImportPackError("canonical manifest differs from the caller's pin")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1 or document.get("kind") != "dubflow_localization_job":
        raise ImportPackError("unsupported canonical job manifest")
    root = manifest_path.parent.resolve()
    if output_root.resolve() == root or root.is_relative_to(output_root.resolve()):
        raise ImportPackError("pack output overlaps the canonical export root")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ImportPackError("canonical artifact descriptors are missing")

    def artifact(name: str, *, relative_fallback: str | None = None) -> tuple[Path, str]:
        entry = artifacts.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ImportPackError(f"canonical artifact is missing: {name}")
        path = root / relative_fallback if relative_fallback is not None else Path(entry["path"])
        # Native Windows admissions retain \\?\ prefixes. Compare physical
        # ancestor identity and rebase onto the caller's root spelling.
        resolved = path.resolve()
        ancestor = next((parent for parent in resolved.parents if parent.exists() and os.path.samefile(parent, root)), None)
        if ancestor is None:
            raise ImportPackError(f"canonical artifact is outside its export: {name}")
        path = root / resolved.relative_to(ancestor)
        expected = _content_hash(entry.get("sha256"))
        size = entry.get("size_bytes")
        if type(size) is not int or size < 0 or path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ImportPackError(f"canonical artifact size/path is invalid: {name}")
        with path.open("rb") as stream:
            if "sha256:" + file_digest(stream, "sha256").hexdigest() != expected:
                raise ImportPackError(f"canonical artifact hash differs: {name}")
        return path, expected

    qc_path, _ = artifact("qc_report")
    _, qc = _read_document(qc_path)
    if type(qc.get("schema_version")) is not int or qc["schema_version"] != 1 or qc.get("status") != "passed":
        raise ImportPackError("canonical export did not pass QC")
    source = document.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("path"), str):
        raise ImportPackError("canonical source identity is missing")
    source_path, source_hash = Path(source["path"]), _content_hash(source.get("sha256"))
    timeline_path, timeline_hash = artifact("editable_timeline")
    _, canonical_timeline = _read_document(timeline_path)
    if type(canonical_timeline.get("schema_version")) is not int or canonical_timeline["schema_version"] != 1 or not isinstance(canonical_timeline.get("cues"), list):
        raise ImportPackError("unsupported canonical editable timeline")
    cue_fields = {"cue_id", "start_ticks", "end_ticks", "start_ms", "end_ms", "asset_ids", "track_id", "speaker_id", "voice_id"}
    cues = []
    for cue in canonical_timeline["cues"]:
        if not isinstance(cue, dict):
            raise ImportPackError("canonical timeline cue is invalid")
        # Complete text, confidence and native clock mapping stay in the copied
        # canonical timeline. The pack descriptor projects only tick identity.
        cues.append({key: value for key, value in cue.items() if key in cue_fields})
    timeline = {"time_base": canonical_timeline.get("time_base"), "duration_ticks": canonical_timeline.get("duration_ticks"), "cues": cues}
    extras = [ImportPackAsset("canonical-timeline", "timeline", timeline_path, "json", timeline_hash)]
    for name, asset_id, kind, format_name in (("final_video", "localized-video", "video", "mp4"),
                                             ("captions_srt", "vietsub-srt", "subtitle", "srt"),
                                             ("captions_ass", "vietsub-ass", "subtitle", "ass")):
        path, content_hash = artifact(name)
        extras.append(ImportPackAsset(asset_id, kind, path, format_name, content_hash))
    for name, kind, filename in (("source_audio", "original_audio", "source_audio.wav"),
                                 ("dialogue_stem", "dub_audio", "dialogue_stem.wav"),
                                 ("final_mix", "mixed_audio", "final_mix.wav")):
        copied = "editable_" + name
        if copied in artifacts:
            path, content_hash = artifact(copied)
        elif name in artifacts:
            path, content_hash = artifact(name, relative_fallback="editable/" + filename)
        else:
            continue  # B1/no-audio exports do not invent missing audio stems.
        extras.append(ImportPackAsset(name.replace("_", "-"), kind, path, "wav", content_hash))
    return create_production_pack(pack_id=document.get("job_id"), source_path=source_path,
        output_root=output_root, timeline=timeline, additional_assets=tuple(extras), source_content_hash=source_hash,
        installed_capcut_version=installed_capcut_version, supported_capcut_versions=supported_capcut_versions,
        controlled_capcut_root=controlled_capcut_root, draft_backend=draft_backend)


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


__all__ = ["ProductionPackResult", "create_production_pack", "create_pack_from_export", "detect_capcut_version"]
