"""Install a verified release bundle into a user-owned Windows directory.

The script is designed to run from the app-owned Python runtime shipped in the
bundle. It never mutates an active version in place: files are copied into a
versioned staging directory, verified, activated with a current pointer, and
only then exposed through the user launcher.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, Sequence
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
from packaging.release.trust import RELEASE_KEY_ID, RELEASE_PUBLIC_KEY_B64  # noqa: E402


STATE_SCHEMA_VERSION = 1


class BootstrapInstallError(RuntimeError):
    """Raised for a fail-closed bundle installation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message[:4096]
        super().__init__(f"{code}: {self.message}")


def _is_link(path: Path) -> bool:
    """Return true for symbolic links and Windows junction/reparse links."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError as exc:
        raise BootstrapInstallError("PATH_CHECK_FAILED", f"unable to inspect release path: {path}") from exc


def _reject_link_components(path: Path, label: str) -> None:
    """Reject links in every existing component without resolving through them."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor) if absolute.anchor else Path()
    for part in absolute.parts:
        if not part or part == absolute.anchor:
            continue
        current = current / part
        if _is_link(current):
            raise BootstrapInstallError("SYMLINK_REJECTED", f"{label} contains a symlink or junction: {current}")


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
    _reject_link_components(root, "release path")
    root = root.resolve()
    current = root
    for component in Path(relative).parts:
        current = current / component
        if _is_link(current):
            raise BootstrapInstallError("SYMLINK_REJECTED", f"symlink is not allowed in release path: {relative}")
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BootstrapInstallError("UNSAFE_PATH", f"path escapes bundle root: {relative}") from exc
    return candidate


def _verify_signature_metadata(manifest: ReleaseManifest) -> None:
    if manifest.release_channel == "stable" and not manifest.signature_required:
        raise BootstrapInstallError("UNSIGNED_STABLE", "stable release metadata must require a signature")
    if manifest.signature_algorithm not in {"none", "ed25519"}:
        raise BootstrapInstallError("SIGNATURE_ALGORITHM", "unsupported release signature algorithm")
    if manifest.signature_required:
        if manifest.signature_algorithm != "ed25519" or manifest.signature_key_id != RELEASE_KEY_ID:
            raise BootstrapInstallError("SIGNATURE_TRUST", "release signature key is not trusted")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(RELEASE_PUBLIC_KEY_B64, validate=True))
            signature = base64.b64decode(manifest.signature_value, validate=True)
            value = manifest.to_dict()
            security = dict(value["security"])
            security["value"] = ""
            value["security"] = security
            canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            public_key.verify(signature, canonical)
        except Exception as error:
            raise BootstrapInstallError("SIGNATURE_INVALID", "release manifest signature verification failed") from error


def _expected_paths(manifest: ReleaseManifest) -> set[str]:
    paths = {artifact.path for artifact in manifest.artifacts}
    if "release-manifest.json" in paths:
        raise BootstrapInstallError("MANIFEST_LAYOUT", "release-manifest.json is reserved and cannot be an artifact")
    paths.add("release-manifest.json")
    return paths


def _actual_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if _is_link(path):
            raise BootstrapInstallError("SYMLINK_REJECTED", f"symlink is not allowed in release bundle: {relative}")
        # Python can create bytecode caches while the bundled bootstrapper is
        # running. They are transient implementation details, never durable
        # release artifacts, and are intentionally excluded from the manifest.
        relative_parts = relative.split("/")
        if "__pycache__" in relative_parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            files.add(relative)
    return files


def verify_bundle(bundle_root: Path | str, manifest: ReleaseManifest) -> None:
    """Verify every manifest file before any destination mutation."""

    _reject_link_components(Path(bundle_root), "bundle root")
    root = Path(bundle_root).resolve()
    if not root.is_dir():
        raise BootstrapInstallError("BUNDLE_MISSING", f"bundle root does not exist: {root}")
    _verify_signature_metadata(manifest)
    expected = _expected_paths(manifest)
    actual = _actual_files(root)
    extra = sorted(actual - expected)
    missing = sorted(expected - actual)
    if extra:
        raise BootstrapInstallError("UNMANIFESTED_ARTIFACT", f"bundle contains files absent from the manifest: {', '.join(extra[:8])}")
    if missing:
        raise BootstrapInstallError("ARTIFACT_MISSING", f"bundle is missing manifest files: {', '.join(missing[:8])}")
    for artifact in manifest.artifacts:
        source = _safe_join(root, artifact.path)
        if _is_link(source) or not source.is_file():
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
    if _is_link(manifest_path) or not manifest_path.is_file():
        raise BootstrapInstallError("MANIFEST_MISSING", "release-manifest.json is missing")


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _copy_bundle(
    bundle_root: Path,
    destination: Path,
    manifest: ReleaseManifest,
    manifest_sha256: str,
    progress: set[str],
    progress_callback: Callable[[set[str]], None],
) -> None:
    expected: dict[str, str] = {artifact.path: artifact.sha256 for artifact in manifest.artifacts}
    expected["release-manifest.json"] = manifest_sha256
    for relative in sorted(expected):
        source = _safe_join(bundle_root, relative)
        target = _safe_join(destination, relative)
        if _is_link(source) or not source.is_file():
            raise BootstrapInstallError("ARTIFACT_MISSING", f"release artifact is not a regular file: {relative}")
        if _is_link(target):
            raise BootstrapInstallError("SYMLINK_REJECTED", f"symlink is not allowed in install path: {relative}")
        if target.is_file() and hash_file(target) == expected[relative]:
            progress.add(relative)
            progress_callback(progress)
            continue
        if target.exists() and not target.is_file():
            raise BootstrapInstallError("INSTALL_PATH_CONFLICT", f"install path is not a regular file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
        try:
            shutil.copy2(source, temporary)
            if hash_file(temporary) != expected[relative]:
                raise BootstrapInstallError("STAGED_HASH_MISMATCH", f"copied artifact changed during staging: {relative}")
            os.replace(temporary, target)
        except OSError as exc:
            raise BootstrapInstallError("STAGING_COPY_FAILED", f"unable to stage {relative}: {exc}") from exc
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        progress.add(relative)
        progress_callback(progress)


def _installed_version_path(install_root: Path, version: str) -> Path:
    # Manifest versions are validated as safe identifiers, so this path is
    # stable and cannot escape the user-owned root.
    return install_root / "versions" / version


def _write_launcher(install_root: Path, version: str) -> None:
    content = f"""@echo off
setlocal
set \"DUBFLOW_ROOT=%~dp0\"
\"%DUBFLOW_ROOT%versions\\{version}\\runtime\\python.exe\" -B \"%DUBFLOW_ROOT%versions\\{version}\\app\\packaging\\release\\launcher.py\" %*
set \"EXIT_CODE=%errorlevel%\"
endlocal & exit /b %EXIT_CODE%
"""
    _atomic_write(install_root / "DubFlow.cmd", content.replace("\n", "\r\n").encode("utf-8"))


def install_bundle(bundle_root: Path | str, install_root: Path | str) -> dict[str, Any]:
    """Verify and activate one bundle version atomically."""

    root = Path(bundle_root).resolve()
    requested_destination = Path(install_root).expanduser()
    _reject_link_components(requested_destination, "install root")
    destination = requested_destination.resolve()
    try:
        manifest = load_manifest(root / "release-manifest.json")
    except ManifestError as exc:
        raise BootstrapInstallError("MANIFEST_INVALID", str(exc)) from exc
    verify_bundle(root, manifest)
    source_manifest_hash = hash_file(root / "release-manifest.json")
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapInstallError("INSTALL_ROOT_UNAVAILABLE", f"unable to create install root: {destination}") from exc
    _reject_link_components(destination, "install root")
    versions = destination / "versions"
    if _is_link(versions):
        raise BootstrapInstallError("SYMLINK_REJECTED", f"versions directory cannot be a symlink: {versions}")
    try:
        versions.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapInstallError("INSTALL_PATH_CONFLICT", f"unable to create versions directory: {versions}") from exc
    if not versions.is_dir():
        raise BootstrapInstallError("INSTALL_PATH_CONFLICT", f"versions path is not a directory: {versions}")
    version_path = _installed_version_path(destination, manifest.version)
    if _is_link(version_path):
        raise BootstrapInstallError("SYMLINK_REJECTED", f"installed version cannot be a symlink: {version_path}")
    progress_path = destination / f"install-progress-{manifest.version}.json"
    if _is_link(progress_path):
        raise BootstrapInstallError("SYMLINK_REJECTED", f"progress state cannot be a symlink: {progress_path}")
    if version_path.exists():
        if not version_path.is_dir():
            raise BootstrapInstallError("VERSION_CONFLICT", f"installed version path is not a directory: {version_path}")
        installed_manifest = version_path / "release-manifest.json"
        if not installed_manifest.is_file() or hash_file(installed_manifest) != source_manifest_hash:
            raise BootstrapInstallError("VERSION_CONFLICT", f"version {manifest.version} is already installed with different bytes")
        # An interrupted or tampered version directory must never be silently
        # promoted just because its manifest has the expected bytes.
        try:
            verify_bundle(version_path, manifest)
        except BootstrapInstallError as exc:
            raise BootstrapInstallError("INSTALLED_VERSION_INVALID", str(exc)) from exc
        progress_path.unlink(missing_ok=True)
    else:
        staging = versions / f".{manifest.version}.staging"
        if _is_link(staging):
            raise BootstrapInstallError("SYMLINK_REJECTED", f"staging path cannot be a symlink: {staging}")
        if staging.exists() and not staging.is_dir():
            raise BootstrapInstallError("STAGING_CONFLICT", f"staging path is not a directory: {staging}")
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BootstrapInstallError("STAGING_UNAVAILABLE", f"unable to create staging path: {staging}") from exc
        completed: set[str] = set()
        if progress_path.exists() and not progress_path.is_file():
            raise BootstrapInstallError("RESUME_STATE_INVALID", f"progress state is not a regular file: {progress_path}")
        if progress_path.is_file():
            try:
                progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BootstrapInstallError("RESUME_STATE_INVALID", f"unable to read {progress_path}") from exc
            expected_paths = _expected_paths(manifest)
            if not isinstance(progress_payload, dict):
                raise BootstrapInstallError("RESUME_CONFLICT", "existing staging state belongs to a different release manifest")
            progress_status = progress_payload.get("status")
            progress_staging = progress_payload.get("staging_path")
            same_staging = False
            if isinstance(progress_staging, str):
                _reject_link_components(Path(progress_staging), "resume staging path")
                same_staging = os.path.normcase(os.path.realpath(progress_staging)) == os.path.normcase(os.path.realpath(staging))
            if (
                progress_payload.get("schema_version") != STATE_SCHEMA_VERSION
                or progress_status not in {"staging", "failed"}
                or progress_payload.get("version") != manifest.version
                or progress_payload.get("manifest_sha256") != source_manifest_hash
                or not isinstance(progress_payload.get("completed"), list)
                or not same_staging
            ):
                raise BootstrapInstallError("RESUME_CONFLICT", "existing staging state belongs to a different release manifest")
            completed = {item for item in progress_payload["completed"] if isinstance(item, str)}
            if not completed.issubset(expected_paths):
                raise BootstrapInstallError("RESUME_STATE_INVALID", "resume state contains an unrecognized artifact path")

        def save_progress(done: set[str], *, status: str = "staging", error: str | None = None) -> None:
            payload: dict[str, Any] = {
                "schema_version": STATE_SCHEMA_VERSION,
                "status": status,
                "version": manifest.version,
                "manifest_sha256": source_manifest_hash,
                "staging_path": str(staging),
                "completed": sorted(done),
            }
            if error:
                payload["error"] = error[:4096]
            _write_progress(progress_path, payload)

        save_progress(completed)
        try:
            for partial in staging.rglob("*.partial"):
                if _is_link(partial):
                    raise BootstrapInstallError("SYMLINK_REJECTED", f"staging partial cannot be a symlink: {partial}")
                if partial.is_file():
                    partial.unlink()
            _copy_bundle(root, staging, manifest, source_manifest_hash, completed, save_progress)
            verify_bundle(staging, manifest)
            os.replace(staging, version_path)
            progress_path.unlink(missing_ok=True)
        except BaseException as exc:
            try:
                save_progress(completed, status="failed", error=str(exc))
            except OSError:
                pass
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
    qualification_path = version_path / "release-status.json"
    production_qualified = False
    try:
        qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
        production_qualified = bool(
            isinstance(qualification, dict)
            and qualification.get("schema_version") == 1
            and qualification.get("source_sha") == manifest.source_sha
            and qualification.get("version") == manifest.version
            and qualification.get("release_channel") == manifest.release_channel
            and qualification.get("production_qualified") is True
        )
    except (OSError, json.JSONDecodeError):
        raise BootstrapInstallError("STATUS_INVALID", "installed release qualification status is unreadable")
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "current_version": manifest.version,
        "manifest_sha256": manifest_hash,
        "release_channel": manifest.release_channel,
        "production_qualified": production_qualified,
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
        "production_qualified": production_qualified,
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
