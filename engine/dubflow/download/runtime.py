"""Prepare the reviewed SDK and construct adapters from verified bundle pins.

The release bootstrap/manager must verify the whole bundle and its signature
policy before passing its artifact inventory here. This module does not grant
authenticity to a manifest, discover system Python or install mutable packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit
import zipfile

from .authenticated import AuthenticatedYtDlpTransport
from .bilibili import BilibiliSourceAdapter
from .douyin import DouyinSourceAdapter
from .materializer import DownloadError, HttpTransport, MediaMaterializer, _reject_links
from .provider_transport import YtDlpProviderTransport
from .sessions import ProtectedSessionBridge
from .source_adapter import MediaCandidate, SourceError, SourceErrorCode
from .stream_materializer import FfmpegStreamMuxer, StreamMaterializer


PROFILE_PATH = Path(__file__).parent / "assets/yt-dlp-sdk-v1.json"
BUNDLE_PROFILE = "app/engine/dubflow/download/assets/yt-dlp-sdk-v1.json"
BUNDLE_HELPER = "app/engine/dubflow/download/authenticated_native.py"
MAX_PROFILE_BYTES = 16 * 1024
MAX_SDK_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _unavailable() -> SourceError:
    return SourceError(SourceErrorCode.UNSUPPORTED, "approved source runtime is unavailable or changed", action="repair_runtime")


def _read_profile(path: Path) -> dict:
    _reject_links(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("profile exceeds budget")
    profile = json.loads(raw)
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("unsupported profile")
    name, url, license = profile.get("filename"), profile.get("url"), profile.get("license")
    if (not isinstance(name, str) or not re.fullmatch(r"yt_dlp-[0-9.]+-py3-none-any\.whl", name)
        or not isinstance(url, str) or urlsplit(url).scheme != "https"
        or urlsplit(url).hostname != "files.pythonhosted.org" or urlsplit(url).username
        or urlsplit(url).port not in {None, 443} or urlsplit(url).path.rsplit("/", 1)[-1] != name
        or urlsplit(url).query or urlsplit(url).fragment):
        raise ValueError("invalid SDK origin or name")
    if not isinstance(profile.get("sha256"), str) or not _SHA256.fullmatch(profile["sha256"]):
        raise ValueError("invalid SDK checksum")
    _byte_count(profile.get("size_bytes"), MAX_SDK_BYTES)
    if (not isinstance(license, dict) or license.get("spdx") != "Unlicense"
        or license.get("approved") is not True or license.get("redistributable") is not True
        or not isinstance(license.get("sha256"), str) or not _SHA256.fullmatch(license["sha256"])
        or not isinstance(license.get("archive_path"), str)
        or not re.fullmatch(r"yt_dlp-[0-9.]+\.dist-info/licenses/LICENSE", license["archive_path"])):
        raise ValueError("license is not approved")
    _byte_count(license.get("size_bytes"), MAX_PROFILE_BYTES)
    if not isinstance(profile.get("producer_id"), str) or not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", profile["producer_id"]):
        raise ValueError("invalid producer")
    if not isinstance(profile.get("version"), str) or not re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", profile["version"]):
        raise ValueError("invalid SDK version")
    return profile


def _byte_count(value, maximum: int) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", value) or int(value) > maximum:
        raise ValueError("invalid size")
    return int(value)


def _check_file(path: Path, sha256: str, size: int) -> None:
    _reject_links(path)
    before = path.stat()
    if not path.is_file() or before.st_size != size or not _SHA256.fullmatch(sha256):
        raise ValueError("invalid runtime file")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            count += len(block)
            if count > size:
                raise ValueError("runtime file grew")
            digest.update(block)
    after = path.stat()
    if digest.hexdigest() != sha256 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("runtime file changed")


def _check_license(archive: Path, profile: Mapping) -> None:
    license = profile["license"]
    with zipfile.ZipFile(archive) as zipped:
        entries = [entry for entry in zipped.infolist() if entry.filename == license["archive_path"]]
        size = _byte_count(license["size_bytes"], MAX_PROFILE_BYTES)
        if len(entries) != 1 or entries[0].file_size != size:
            raise ValueError("missing or duplicate SDK notice")
        with zipped.open(entries[0]) as stream:
            notice = stream.read(size + 1)
        if len(notice) != size or hashlib.sha256(notice).hexdigest() != license["sha256"]:
            raise ValueError("SDK notice changed")


def provision_sdk(runtime_root: str | Path, *, transport: HttpTransport | None = None,
                  cancel: Callable[[], bool] | None = None) -> Path:
    """Build-time bounded/resumable acquisition; import the wheel without extraction.

    The existing bundle builder copies runtime/source and inventories the exact
    wheel bytes. A verified existing wheel is reused without a network request.
    Never update this code-bearing archive in place in an installed release.
    """
    try:
        root = Path(runtime_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("missing owned runtime")
        _reject_links(root)
        profile = _read_profile(PROFILE_PATH)
        destination = root / "source" / profile["filename"]
        _reject_links(destination)
        if destination.exists():
            _check_file(destination, profile["sha256"], int(profile["size_bytes"]))
        else:
            candidate = MediaCandidate("reviewed-sdk", profile["url"], "progressive", "application/zip")
            MediaMaterializer(transport, max_bytes=int(profile["size_bytes"])).download(
                candidate, destination, root=root, expected_sha256=profile["sha256"],
                expected_size=int(profile["size_bytes"]), cancel=cancel,
            )
        _check_license(destination, profile)
        return destination
    except (SourceError, DownloadError):
        raise
    except Exception:
        raise _unavailable() from None


def provider_from_verified_bundle(bundle_root: str | Path, *, artifacts: Sequence[Mapping],
                                  provider_id: str, session_bridge: ProtectedSessionBridge | None = None):
    """Use the bootstrap's verified inventory, never self-pin current disk bytes."""
    if provider_id not in {"bilibili", "douyin"}:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "unsupported source provider")
    try:
        root = Path(bundle_root)
        if not root.is_absolute() or not root.is_dir() or not isinstance(artifacts, (list, tuple)) or not 1 <= len(artifacts) <= 50_000:
            raise ValueError("missing verified bundle inventory")
        _reject_links(root)
        index, folded = {}, set()
        for item in artifacts:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise ValueError("invalid inventory")
            name = item["path"]
            if name.casefold() in folded:
                raise ValueError("duplicate inventory path")
            folded.add(name.casefold())
            index[name] = item

        def pin(name: str, maximum: int):
            item = index[name]
            digest = item["sha256"]
            if not isinstance(digest, str):
                raise ValueError("missing approved digest")
            size = _byte_count(item["size_bytes"], maximum)
            path = root / name
            _check_file(path, digest, size)
            return path, digest

        profile_path, _ = pin(BUNDLE_PROFILE, MAX_PROFILE_BYTES)
        profile = _read_profile(profile_path)
        sdk, sdk_sha = pin("runtime/source/" + profile["filename"], MAX_SDK_BYTES)
        if sdk_sha != profile["sha256"] or sdk.stat().st_size != int(profile["size_bytes"]):
            raise ValueError("SDK differs from reviewed inventory")
        _check_license(sdk, profile)
        python, python_sha = pin("runtime/python.exe", 256 * 1024 * 1024)
        helper, helper_sha = pin(BUNDLE_HELPER, 1024 * 1024)
        ffmpeg, ffmpeg_sha = pin("runtime/media/ffmpeg.exe", 512 * 1024 * 1024)
        ffprobe, ffprobe_sha = pin("runtime/media/ffprobe.exe", 512 * 1024 * 1024)
        transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper,
            sdk_archive=sdk, pins={"python": python_sha, "helper": helper_sha, "sdk_archive": sdk_sha})
        muxer = FfmpegStreamMuxer(ffmpeg, ffprobe, trusted_root=root,
            ffmpeg_sha256=ffmpeg_sha, ffprobe_sha256=ffprobe_sha)
        kwargs = {"transport": YtDlpProviderTransport(provider_id, authenticated_transport=transport),
                  "session_bridge": session_bridge, "stream_materializer": StreamMaterializer(muxer)}
        return BilibiliSourceAdapter(**kwargs) if provider_id == "bilibili" else DouyinSourceAdapter(**kwargs)
    except Exception:
        raise _unavailable() from None


def source_runtime_health(bundle_root: str | Path, *, artifacts: Sequence[Mapping]) -> dict:
    """Check the actual owned interpreter/SDK without contacting a provider."""
    root = Path(bundle_root)
    adapter = provider_from_verified_bundle(root, artifacts=artifacts, provider_id="bilibili")
    profile = _read_profile(root / BUNDLE_PROFILE)
    health = adapter._transport._authenticated.health_check(expected_sdk_version=profile["version"])
    try:
        runtime = (root / "runtime").resolve()
        if Path(health["python_prefix"]).resolve() != runtime or Path(health["python_base_prefix"]).resolve() != runtime:
            raise ValueError("Python found an external installation")
        paths = health["import_roots"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 16:
            raise ValueError("invalid native import roots")
        for value in paths:
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError("unowned native import root")
            Path(value).resolve().relative_to(root.resolve())
        return {"producer_id": profile["producer_id"], "sdk_sha256": profile["sha256"], "native": health}
    except (KeyError, TypeError, ValueError):
        raise _unavailable() from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision the reviewed source SDK into a build-owned runtime")
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = provision_sdk(args.runtime_root)
    except (SourceError, DownloadError) as error:
        print(json.dumps(error.to_dict(), ensure_ascii=True))
        return 2
    print(json.dumps({"sdk_path": str(path), "producer_id": _read_profile(PROFILE_PATH)["producer_id"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
