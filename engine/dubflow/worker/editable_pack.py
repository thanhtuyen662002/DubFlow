"""Checkpointed portable pack publication and read-only export validation.

Copies stay in private worker storage until independently verified. The pack
then participates in the existing editable-directory publication transaction.
No SQLite writes or vendor executable/path discovery occurs here.
"""
from __future__ import annotations

from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import sys
from typing import Any

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("export validation requires the isolated app-owned interpreter")
    sys.path.append(str(Path(__file__).resolve().parents[3]))

from engine.dubflow.capcut.detection import discover_capcut_installation
from engine.dubflow.capcut.import_pack import ImportPackError, validate_import_pack
from engine.dubflow.capcut.production import create_pack_from_export
from engine.dubflow.worker.export_publication import plain

RECIPE = "production-editable-pack-v1"
RELATIVE_PATH = "editable/import-pack"
_ASSETS = {"final_video": "localized-video", "captions_srt": "vietsub-srt",
           "captions_ass": "vietsub-ass", "editable_timeline": "canonical-timeline"}
_AUDIO = {"source_audio": "source-audio", "dialogue_stem": "dialogue-stem", "final_mix": "final-mix"}


def _read(path: Path) -> dict[str, Any]:
    plain(path.absolute())
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ImportPackError("export metadata exceeds bounds")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ImportPackError("export metadata must be an object")
    return result


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + file_digest(stream, "sha256").hexdigest()


def _pins(document: dict[str, Any]) -> dict[str, Any]:
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1 or document.get("kind") != "dubflow_localization_job":
        raise ImportPackError("unsupported canonical export")
    artifacts = document["artifacts"]
    assets = {asset_id: artifacts[name]["sha256"] for name, asset_id in _ASSETS.items()}
    assets["source-video"] = document["source"]["sha256"]
    for name, asset_id in _AUDIO.items():
        descriptor = artifacts.get("editable_" + name, artifacts.get(name))
        if descriptor is not None:
            assets[asset_id] = descriptor["sha256"]
    return {"recipe": RECIPE, "job_id": document["job_id"], "assets": assets}


def _binding(pins: dict[str, Any]) -> str:
    # Producer updates invalidate private reuse without migrating old jobs.
    import engine.dubflow.capcut.production as production
    import engine.dubflow.capcut.import_pack.adapter as adapter
    source_hashes = [_hash(Path(path)) for path in (__file__, production.__file__, adapter.__file__)]
    return sha256(json.dumps({"pins": pins, "producer_sources": source_hashes},
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _verify(root: Path, pins: dict[str, Any]) -> dict[str, Any]:
    plain(root.absolute())
    manifest = validate_import_pack(root)
    if manifest["pack_id"] != pins["job_id"] or manifest["schema_version"] != 2:
        raise ImportPackError("pack identity differs from current export")
    actual = {asset["asset_id"]: asset["content_hash"] for asset in manifest["assets"]}
    if actual != pins["assets"]:
        raise ImportPackError("pack resources differ from current export")
    return manifest


def assemble_export_pack(manifest_path: Path, work_dir: Path) -> dict[str, Any]:
    """Create/reuse private content-bound copies, then promote into pending.

    Interrupted partial copies remain outside the published editable directory.
    A previously promoted candidate is reused only after every asset is checked
    against the current canonical hashes; stale/unknown data is retained/refused.
    """
    document = _read(manifest_path)
    pins = _pins(document)
    identity = _binding(pins)
    candidate = manifest_path.parent / RELATIVE_PATH
    private = work_dir / "editable-pack" / identity / "pack"
    for path in (candidate, private):
        plain(path.absolute())
    if candidate.exists():
        _verify(candidate, pins)
    else:
        if not private.exists():
            create_pack_from_export(manifest_path, private, expected_manifest_hash=_hash(manifest_path))
        _verify(private, pins)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        os.replace(private, candidate)
    _verify(candidate, pins)
    # Optional discovery must not invalidate a verified standard pack.
    try:
        discovery = discover_capcut_installation().to_dict()
    except Exception as error:
        discovery = {"schema_version": 1, "status": "INCOMPLETE", "version": None,
                     "reason": "CAPCUT_DISCOVERY_FAILED:" + type(error).__name__}
    return {"schema_version": 1, "state": "available", "recipe": RECIPE,
            "relative_path": RELATIVE_PATH, "manifest_sha256": _hash(candidate / "manifest.json"),
            "input_binding": "sha256:" + identity,
            "capcut": {"discovery": discovery, "direct_draft": None,
                       "reason": "CAPCUT_VERSION_UNSUPPORTED" if discovery.get("version") else "CAPCUT_IMPORT_PACK_AVAILABLE"}}


def validate_export_pack(export_dir: Path, *, expected_job_id: str | None = None) -> dict[str, Any]:
    """Independently validate the final folder for desktop/installed callers."""
    document = _read(export_dir / "job_manifest.json")
    if expected_job_id is not None and document.get("job_id") != expected_job_id:
        raise ImportPackError("export belongs to another job")
    descriptor = document.get("editable_pack")
    if descriptor is None:
        return {"schema_version": 1, "state": "unavailable", "reason": "LEGACY_EXPORT_WITHOUT_IMPORT_PACK"}
    if descriptor.get("state") != "available":
        return {"schema_version": 1, "state": "unavailable", "reason": descriptor.get("reason", "IMPORT_PACK_UNAVAILABLE")}
    if (type(descriptor.get("schema_version")) is not int or descriptor["schema_version"] != 1
            or descriptor.get("relative_path") != RELATIVE_PATH or descriptor.get("recipe") != RECIPE):
        raise ImportPackError("unsupported export pack descriptor")
    qc = _read(export_dir / "qc_report.json")
    qc_descriptor = document["artifacts"]["qc_report"]
    if (type(qc.get("schema_version")) is not int or qc["schema_version"] != 1 or qc.get("status") != "passed"
            or _hash(export_dir / "qc_report.json") != qc_descriptor["sha256"]):
        raise ImportPackError("canonical export QC is not verified")
    root = export_dir / RELATIVE_PATH
    _verify(root, _pins(document))
    if _hash(root / "manifest.json") != descriptor.get("manifest_sha256"):
        raise ImportPackError("pack manifest differs from the export pin")
    artifact = document["artifacts"].get("import_pack_manifest")
    if not isinstance(artifact, dict) or artifact.get("sha256") != descriptor["manifest_sha256"]:
        raise ImportPackError("pack descriptor differs from the canonical artifact pin")
    return {"schema_version": 1, "state": "available", "path": str(root.resolve()),
            "manifest_sha256": descriptor["manifest_sha256"], "capcut": descriptor.get("capcut")}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate a completed portable editable export")
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    try:
        result = validate_export_pack(args.export_dir, expected_job_id=args.job_id)
        capcut = result.get("capcut")
        if isinstance(capcut, dict):
            result["capcut"] = {"reason": capcut.get("reason"), "direct_draft": None}
    except Exception as error:
        result = {"schema_version": 1, "state": "unavailable",
                  "reason": "IMPORT_PACK_VALIDATION_FAILED:" + type(error).__name__}
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded) > 64 * 1024:
        encoded = '{"schema_version":1,"state":"unavailable","reason":"IMPORT_PACK_REPORT_TOO_LARGE"}'
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
