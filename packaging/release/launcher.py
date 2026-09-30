"""Installed release self-check and desktop entrypoint boundary.

The launcher validates the active pointer and release status before either
printing diagnostics or starting the manifest-owned native desktop host. It
never presents the preview queue backend as a production media implementation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import json
import subprocess
import sys


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from packaging.release.manifest import ManifestError, hash_file, load_manifest  # noqa: E402


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        # An unreadable path cannot be considered safe for the entrypoint.
        return True


def self_check() -> dict[str, object]:
    version_root = Path(__file__).resolve().parents[3]
    manifest_path = version_root / "release-manifest.json"
    status_path = version_root / "release-status.json"
    if _is_link(manifest_path):
        return {"ready": False, "code": "MANIFEST_UNSAFE", "message": str(manifest_path)}
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return {"ready": False, "code": "MANIFEST_INVALID", "message": str(exc)}
    runtime = version_root / "runtime" / "python.exe"
    if _is_link(runtime) or not runtime.is_file():
        return {"ready": False, "code": "RUNTIME_MISSING", "message": str(runtime)}
    pointer_root = version_root.parents[1]
    current_path = pointer_root / "current.json"
    if _is_link(current_path) or not current_path.is_file():
        return {"ready": False, "code": "CURRENT_POINTER_MISSING", "message": str(current_path)}
    try:
        pointer = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "code": "CURRENT_POINTER_INVALID", "message": str(current_path)}
    if not isinstance(pointer, dict) or pointer.get("schema_version") != 1:
        return {"ready": False, "code": "CURRENT_POINTER_INVALID", "message": "unsupported current pointer schema"}
    if pointer.get("current_version") != manifest.version:
        return {"ready": False, "code": "CURRENT_POINTER_MISMATCH", "message": "current pointer does not select this version"}
    if pointer.get("version_path") != f"versions/{manifest.version}":
        return {"ready": False, "code": "CURRENT_POINTER_MISMATCH", "message": "current pointer path does not select this version"}
    if pointer.get("manifest_sha256") != hash_file(manifest_path):
        return {"ready": False, "code": "CURRENT_MANIFEST_MISMATCH", "message": "current pointer hash does not match the active manifest"}
    if _is_link(status_path) or not status_path.is_file():
        return {"ready": False, "code": "STATUS_MISSING", "message": str(status_path)}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "code": "STATUS_INVALID", "message": str(status_path)}
    if (
        not isinstance(status, dict)
        or status.get("schema_version") != 1
        or status.get("source_sha") != manifest.source_sha
        or status.get("version") != manifest.version
        or status.get("release_channel") != manifest.release_channel
        or not isinstance(status.get("production_qualified"), bool)
    ):
        return {"ready": False, "code": "STATUS_MISMATCH", "message": "release status does not match the active manifest"}
    if manifest.release_channel == "candidate" and status["production_qualified"]:
        return {"ready": False, "code": "STATUS_TAMPERED", "message": "candidate release cannot claim production qualification"}
    evidence = status.get("external_evidence")
    if not isinstance(evidence, dict):
        return {"ready": False, "code": "STATUS_INVALID", "message": "release status external evidence is missing"}
    return {
        "ready": True,
        "version": manifest.version,
        "release_channel": manifest.release_channel,
        "production_qualified": bool(status.get("production_qualified", False)),
        "clean_machine": evidence.get("clean_machine", "unknown"),
        "gpu_hardware": evidence.get("gpu_hardware", "unknown"),
        "message": "Release bundle is installed; production qualification remains external to this deterministic self-check.",
    }


def _desktop_host_path(version_root: Path) -> Path:
    """Return the fixed, manifest-owned desktop host location.

    The host is deliberately kept outside the Python application tree.  This
    gives the launcher one stable boundary to validate before starting a GUI
    process, while allowing the Python runtime to remain the diagnostic and
    bootstrap implementation detail it is today.
    """

    return version_root / "app" / "bin" / "DubFlow.exe"


def _resolve_desktop_host(version_root: Path, manifest: object) -> tuple[Path | None, dict[str, object] | None]:
    """Validate and resolve the packaged host without following unsafe paths."""

    host = _desktop_host_path(version_root)
    if _is_link(host):
        return None, {"ready": False, "code": "DESKTOP_HOST_UNSAFE", "message": str(host)}
    if not host.is_file():
        return None, {"ready": False, "code": "DESKTOP_HOST_MISSING", "message": str(host)}

    artifacts = getattr(manifest, "artifacts", ())
    host_artifact = next(
        (
            artifact
            for artifact in artifacts
            if getattr(artifact, "path", "").casefold() == "app/bin/dubflow.exe"
        ),
        None,
    )
    if host_artifact is None or not bool(getattr(host_artifact, "executable", False)):
        return None, {
            "ready": False,
            "code": "DESKTOP_HOST_UNMANIFESTED",
            "message": "the desktop host is not an executable release artifact",
        }
    try:
        actual_hash = hash_file(host)
    except OSError as exc:
        return None, {"ready": False, "code": "DESKTOP_HOST_UNREADABLE", "message": str(exc)}
    if actual_hash != host_artifact.sha256:
        return None, {
            "ready": False,
            "code": "DESKTOP_HOST_TAMPERED",
            "message": "the desktop host hash does not match the active release manifest",
        }
    return host, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--version", action="store_true")
    args, host_args = parser.parse_known_args(argv)
    if args.version:
        manifest = load_manifest(Path(__file__).resolve().parents[3] / "release-manifest.json")
        print(manifest.version)
        return 0
    result = self_check()
    if args.self_check:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get("ready") else 2
    if not result.get("ready"):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 2

    version_root = Path(__file__).resolve().parents[3]
    try:
        manifest = load_manifest(version_root / "release-manifest.json")
    except ManifestError as exc:
        print(json.dumps({"ready": False, "code": "MANIFEST_INVALID", "message": str(exc)}, ensure_ascii=False, sort_keys=True, indent=2))
        return 2
    host, error = _resolve_desktop_host(version_root, manifest)
    if error is not None or host is None:
        print(json.dumps(error or {"ready": False, "code": "DESKTOP_HOST_MISSING"}, ensure_ascii=False, sort_keys=True, indent=2))
        return 3
    try:
        process = subprocess.Popen(
            [os.fspath(host), *host_args],
            cwd=os.fspath(host.parent),
            close_fds=True,
        )
    except OSError as exc:
        print(json.dumps({"ready": False, "code": "DESKTOP_HOST_START_FAILED", "message": str(exc)}, ensure_ascii=False, sort_keys=True, indent=2))
        return 3
    print(json.dumps({"ready": True, "launched": True, "pid": process.pid, "version": manifest.version}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "self_check"]
