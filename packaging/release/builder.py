"""Build a deterministic Windows release-candidate bundle.

The builder is intentionally stdlib-only so PR packaging smoke can run on a
clean runner. It copies an app-owned Python runtime supplied by the release
workflow, records hashes for every staged file, and creates a fixed-timestamp
ZIP. Large model weights and media are never copied from the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Iterable, Sequence
import zipfile

from .manifest import ReleaseArtifact, ReleaseManifest, dump_manifest, hash_file


class BuildError(RuntimeError):
    """Raised when a release bundle cannot be built safely."""


@dataclass(frozen=True)
class BuildResult:
    staging_dir: Path
    bundle_path: Path
    checksum_path: Path
    manifest: ReleaseManifest


_SOURCE_DIRS: tuple[str, ...] = (
    "apps/desktop",
    "contracts",
    "engine",
    "minimal-pipeline-wiring",
    "models",
    "packaging",
)
_SOURCE_FILES: tuple[str, ...] = ("README.md",)
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".partial"}


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in relative_parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        yield path


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"release source directory is missing: {source}")
    for source_file in _iter_files(source):
        relative = source_file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)


def _copy_runtime(runtime_root: Path, destination: Path) -> None:
    if not runtime_root.is_dir():
        raise BuildError(f"runtime root is missing: {runtime_root}")
    executable = runtime_root / "python.exe"
    if not executable.is_file():
        raise BuildError("runtime root must contain an app-owned python.exe")
    _copy_tree(runtime_root, destination)


def _safe_file_id(index: int) -> str:
    return f"file-{index:06d}"


def _timestamp_from_epoch(source_date_epoch: int | None) -> str:
    epoch = int(time.time()) if source_date_epoch is None else source_date_epoch
    if epoch < 0:
        raise BuildError("source_date_epoch must be non-negative")
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _zip_timestamp(source_date_epoch: int | None) -> tuple[int, int, int, int, int, int]:
    epoch = int(time.time()) if source_date_epoch is None else source_date_epoch
    # ZIP dates cannot represent dates before 1980 and have two-second
    # resolution. Clamp and normalize so identical inputs produce identical
    # archive metadata.
    epoch = max(epoch, 315532800)
    value = datetime.fromtimestamp(epoch, tz=timezone.utc)
    second = value.second - (value.second % 2)
    return (value.year, value.month, value.day, value.hour, value.minute, second)


def _write_setup_scripts(stage: Path) -> None:
    (stage / "setup.cmd").write_text(
        """@echo off
setlocal
rem Keep a non-trailing separator in the bundle root.  A quoted Windows path
rem ending in a backslash can escape the closing quote when Python parses the
rem command line, which drops the following --install-root argument.
set \"BUNDLE_ROOT=%~dp0.\"
if not exist \"%BUNDLE_ROOT%\\runtime\\python.exe\" (
  echo DubFlow release is missing its app-owned Python runtime. 1>&2
  exit /b 20
)
\"%BUNDLE_ROOT%\\runtime\\python.exe\" -B \"%BUNDLE_ROOT%\\app\\packaging\\release\\bootstrap.py\" --bundle-root \"%BUNDLE_ROOT%\" --install-root \"%LOCALAPPDATA%\\DubFlow\"
if errorlevel 1 exit /b %errorlevel%
echo DubFlow was installed for the current Windows user.
echo Run \"%LOCALAPPDATA%\\DubFlow\\DubFlow.cmd --self-check\" to verify the installation.
endlocal
""",
        encoding="utf-8",
        newline="\r\n",
    )
    (stage / "setup.ps1").write_text(
        """$ErrorActionPreference = 'Stop'
$bundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $bundleRoot 'runtime\\python.exe'
if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
    throw 'DubFlow release is missing its app-owned Python runtime.'
}
& $runtime -B (Join-Path $bundleRoot 'app\\packaging\\release\\bootstrap.py') --bundle-root $bundleRoot --install-root (Join-Path $env:LOCALAPPDATA 'DubFlow')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'DubFlow was installed for the current Windows user.'
""",
        encoding="utf-8",
        newline="\r\n",
    )


def _write_status(stage: Path, *, version: str, source_sha: str, channel: str) -> None:
    status = {
        "schema_version": 1,
        "release_status": "candidate",
        "production_qualified": False,
        "source_sha": source_sha,
        "version": version,
        "release_channel": channel,
        "external_evidence": {
            "clean_machine": "pending_issue_36",
            "gpu_hardware": "pending_issue_38",
            "long_form_media": "pending_issue_38",
        },
    }
    (stage / "release-status.json").write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_manifest(
    stage: Path,
    *,
    version: str,
    source_sha: str,
    runtime_version: str,
    package_manifest_version: str,
    build_timestamp_utc: str,
    release_channel: str,
    signature_required: bool,
) -> ReleaseManifest:
    files = list(_iter_files(stage))
    artifacts = tuple(
        ReleaseArtifact(
            _safe_file_id(index),
            file.relative_to(stage).as_posix(),
            file.stat().st_size,
            hash_file(file),
            file.suffix.lower() in {".exe", ".cmd", ".ps1"},
        )
        for index, file in enumerate(files, start=1)
    )
    return ReleaseManifest(
        version,
        source_sha,
        runtime_version,
        package_manifest_version,
        "windows",
        "x86_64",
        build_timestamp_utc,
        release_channel,
        signature_required,
        "ed25519" if signature_required else "none",
        "dubflow-release-v1" if signature_required else "unsigned-candidate",
        "" if not signature_required else "RELEASE_SIGNING_VALUE_REQUIRED",
        artifacts,
    )


def _write_checksums(stage: Path, output: Path) -> Path:
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    lines: list[str] = []
    for path in sorted(_iter_files(stage), key=lambda item: item.relative_to(stage).as_posix()):
        lines.append(f"{hash_file(path)}  {path.relative_to(stage).as_posix()}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def _write_zip(stage: Path, output: Path, source_date_epoch: int | None) -> None:
    timestamp = _zip_timestamp(source_date_epoch)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(_iter_files(stage), key=lambda item: item.relative_to(stage).as_posix()):
            relative = file.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(relative, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            archive.writestr(info, file.read_bytes())


def build_bundle(
    *,
    source_root: Path | str,
    output_dir: Path | str,
    version: str,
    source_sha: str,
    runtime_root: Path | str,
    runtime_version: str = "3.12.10",
    package_manifest_version: str = "v1",
    release_channel: str = "candidate",
    signature_required: bool = False,
    source_date_epoch: int | None = None,
) -> BuildResult:
    """Build and hash a release bundle from an exact source tree."""

    source = Path(source_root).resolve()
    output = Path(output_dir).resolve()
    if not source.is_dir():
        raise BuildError(f"source root is missing: {source}")
    if not source_sha or any(character not in "0123456789abcdef" for character in source_sha) or len(source_sha) not in {40, 64}:
        raise BuildError("source_sha must be a lowercase 40- or 64-character Git SHA")
    if release_channel == "stable" and not signature_required:
        raise BuildError("stable releases require signature_required=true")
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dubflow-release-", dir=output) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        app = stage / "app"
        for relative in _SOURCE_DIRS:
            _copy_tree(source / relative, app / relative)
        for relative in _SOURCE_FILES:
            source_file = source / relative
            if source_file.is_file():
                target = app / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
        _copy_runtime(Path(runtime_root).resolve(), stage / "runtime")
        _write_setup_scripts(stage)
        _write_status(stage, version=version, source_sha=source_sha, channel=release_channel)

        manifest = _build_manifest(
            stage,
            version=version,
            source_sha=source_sha,
            runtime_version=runtime_version,
            package_manifest_version=package_manifest_version,
            build_timestamp_utc=_timestamp_from_epoch(source_date_epoch),
            release_channel=release_channel,
            signature_required=signature_required,
        )
        dump_manifest(manifest, stage / "release-manifest.json")
        # The manifest is intentionally excluded from its own artifact list;
        # include it in the external checksum file instead.
        checksums_placeholder = output / f"DubFlow-{version}-windows-x64.zip"
        _write_zip(stage, checksums_placeholder, source_date_epoch)
        checksum_path = _write_checksums(stage, checksums_placeholder)
        # Copy the staged tree to a stable diagnostic location only when asked
        # for by callers; the returned path remains valid for this build call.
        diagnostic = output / f"DubFlow-{version}-windows-x64-staging"
        if diagnostic.exists():
            shutil.rmtree(diagnostic)
        shutil.copytree(stage, diagnostic)
        return BuildResult(diagnostic, checksums_placeholder, checksum_path, manifest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-version", default="3.12.10")
    parser.add_argument("--package-manifest-version", default="v1")
    parser.add_argument("--release-channel", choices=("candidate", "stable"), default="candidate")
    parser.add_argument("--signature-required", action="store_true")
    parser.add_argument("--source-date-epoch", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_bundle(
        source_root=args.source_root,
        output_dir=args.output_dir,
        version=args.version,
        source_sha=args.source_sha,
        runtime_root=args.runtime_root,
        runtime_version=args.runtime_version,
        package_manifest_version=args.package_manifest_version,
        release_channel=args.release_channel,
        signature_required=args.signature_required,
        source_date_epoch=args.source_date_epoch,
    )
    print(json.dumps({
        "bundle": str(result.bundle_path),
        "checksum": str(result.checksum_path),
        "manifest": result.manifest.to_dict(),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BuildError", "BuildResult", "build_bundle", "main"]
