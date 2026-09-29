"""Install a verified release bundle into a user-owned Windows directory.

The script is designed to run from the app-owned Python runtime shipped in the
bundle. It never mutates an active version in place: files are copied into a
versioned staging directory, verified, activated with a current pointer, and
only then exposed through the user launcher.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Sequence
import uuid


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from packaging.release.manifest import (  # noqa: E402
    ManifestError,
    ReleaseManifest,
    hash_file,
    load_manifest,
)


STATE_SCHEMA_VERSION = 1


class BootstrapInstallError(RuntimeError):
    """Raised for a fail-closed bundle installation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message[:4096]
        super().__init__(f"{code}: {self.message}")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _safe_join(root: Path, relative: str) -> Path:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise BootstrapInstallError("UNSAFE_PATH", f"path escapes bundle root: {relative}") from exc
    return candidate


def _verify_signature_metadata(manifest: ReleaseManifest) -> None:
    if manifest.release_channel == "stable" and not manifest.signature_required:
        raise BootstrapInstallError("UNSIGNED_STABLE", "stable release metadata must require a signature")
    if manifest.signature_required and (
        manifest.signature_algorithm == "none"
        or not manifest.signature_value
        or manifest.signature_value == "RELEASE_SIGNING_VALUE_REQUIRED"
    ):
        raise BootstrapInstallError("SIGNATURE_INVALID", "required release signature is missing or is a placeholder")
    if manifest.signature_algorithm not in {"none", "ed25519"}:
        raise BootstrapInstallError("SIGNATURE_ALGORITHM", "unsupported release signature algorithm")


def verify_bundle(bundle_root: Path | str, manifest: ReleaseManifest) -> None:
    """Verify every manifest file before any destination mutation."""

    root = Path(bundle_root).resolve()
    if not root.is_dir():
        raise BootstrapInstallError("BUNDLE_MISSING", f"bundle root does not exist: {root}")
    _verify_signature_metadata(manifest)
    for artifact in manifest.artifacts:
        source = _safe_join(root, artifact.path)
        if source.is_symlink() or not source.is_file():
            raise BootstrapInstallError("ARTIFACT_MISSING", f"bundle artifact is missing: {artifact.path}")
        actual_size = source.stat().st_size
        if actual_size != artifact.size_bytes:
            raise BootstrapInstallError(
                "SIZE_MISMATCH",
                f"{artifact.path}: expected {artifact.size_bytes} bytes, received {actual_size}",
            )
        actual_hash = hash_file(source)
        if actual_hash != artifact.sha256:
            raise BootstrapInstallError(
                "HASH_MISMATCH",
                f"{artifact.path}: expected {artifact.sha256}, received {actual_hash}",
            )

    manifest_path = root / "release-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BootstrapInstallError("MANIFEST_MISSING", "release-manifest.json is missing")


def _copy_bundle(bundle_root: Path, destination: Path) -> None:
    for source in sorted(bundle_root.rglob("*"), key=lambda item: item.as_posix().lower()):
        relative = source.relative_to(bundle_root)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        target = _safe_join(destination, relative.as_posix())
        if source.is_symlink():
            raise BootstrapInstallError("SYMLINK_REJECTED", f"symlink is not allowed in release bundle: {relative}")
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _installed_version_path(install_root: Path, version: str) -> Path:
    # Manifest versions are validated as safe identifiers, so this path is
    # stable and cannot escape the user-owned root.
    return install_root / "versions" / version


def _write_launcher(install_root: Path, version: str) -> None:
    content = f"""@echo off
setlocal
set \"DUBFLOW_ROOT=%~dp0\"
\"%DUBFLOW_ROOT%versions\\{version}\\runtime\\python.exe\" \"%DUBFLOW_ROOT%versions\\{version}\\app\\packaging\\release\\launcher.py\" %*
set \"EXIT_CODE=%errorlevel%\"
endlocal & exit /b %EXIT_CODE%
"""
    _atomic_write(install_root / "DubFlow.cmd", content.replace("\n", "\r\n").encode("utf-8"))


def install_bundle(bundle_root: Path | str, install_root: Path | str) -> dict[str, Any]:
    """Verify and activate one bundle version atomically."""

    root = Path(bundle_root).resolve()
    destination = Path(install_root).expanduser().resolve()
    try:
        manifest = load_manifest(root / "release-manifest.json")
    except ManifestError as exc:
        raise BootstrapInstallError("MANIFEST_INVALID", str(exc)) from exc
    verify_bundle(root, manifest)
    destination.mkdir(parents=True, exist_ok=True)
    versions = destination / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_path = _installed_version_path(destination, manifest.version)
    if version_path.exists():
        installed_manifest = version_path / "release-manifest.json"
        if not installed_manifest.is_file() or hash_file(installed_manifest) != hash_file(root / "release-manifest.json"):
            raise BootstrapInstallError("VERSION_CONFLICT", f"version {manifest.version} is already installed with different bytes")
    else:
        staging = versions / f".{manifest.version}.{uuid.uuid4().hex}.staging"
        try:
            staging.mkdir(parents=True)
            _copy_bundle(root, staging)
            copied_manifest = staging / "release-manifest.json"
            if hash_file(copied_manifest) != hash_file(root / "release-manifest.json"):
                raise BootstrapInstallError("STAGED_HASH_MISMATCH", "staged release manifest changed during copy")
            os.replace(staging, version_path)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    manifest_hash = hash_file(version_path / "release-manifest.json")
    pointer = {
        "schema_version": STATE_SCHEMA_VERSION,
        "current_version": manifest.version,
        "version_path": f"versions/{manifest.version}",
        "manifest_sha256": manifest_hash,
        "updated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _atomic_write(
        destination / "current.json",
        (json.dumps(pointer, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "current_version": manifest.version,
        "manifest_sha256": manifest_hash,
        "release_channel": manifest.release_channel,
        "production_qualified": False,
    }
    _atomic_write(
        destination / "install-state.json",
        (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    _write_launcher(destination, manifest.version)
    return {
        "ready": True,
        "version": manifest.version,
        "install_root": str(destination),
        "manifest_sha256": manifest_hash,
        "release_channel": manifest.release_channel,
        "production_qualified": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(install_bundle(args.bundle_root, args.install_root), ensure_ascii=False, sort_keys=True))
    except BootstrapInstallError as exc:
        print(json.dumps({"ready": False, "code": exc.code, "message": exc.message}, ensure_ascii=False, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BootstrapInstallError", "install_bundle", "main", "verify_bundle"]
