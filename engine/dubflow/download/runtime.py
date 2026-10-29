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
from .generic import GenericUrlAdapter
from .generic.sdk import GenericSdkTransport
from .materializer import DownloadError, HttpTransport, MediaMaterializer, _reject_links
from .provider_transport import PublicProviderHttpTransport, YtDlpProviderTransport
from .sessions import ProtectedSessionBridge
from .source_adapter import MediaCandidate, SourceError, SourceErrorCode
from .stream_materializer import FfmpegStreamMuxer, StreamMaterializer


PROFILE_PATH = Path(__file__).parent / "assets/yt-dlp-sdk-v1.json"
BUNDLE_PROFILE = "app/engine/dubflow/download/assets/yt-dlp-sdk-v1.json"
BUNDLE_HELPER = "app/engine/dubflow/download/authenticated_native.py"
BUNDLE_DOUYIN_CREATOR = "app/engine/dubflow/download/douyin/creator_native.py"
BUNDLE_DOUYIN_SIGNER = "app/engine/dubflow/download/douyin/request_signature.py"
BUNDLE_DOUYIN_LICENSE = "app/docs/licenses/f2-apache-2.0.txt"
BUNDLE_DOUYIN_NOTICE = "app/docs/licenses/f2-douyin-request-signature.md"
MAX_PROFILE_BYTES = 16 * 1024
MAX_SDK_BYTES = 16 * 1024 * 1024
# Pinned extras the SDK uses for sites that need them (ADR-0040): libraries for
# browser impersonation (TikTok) and EJS, and Deno for YouTube's JS challenges.
EXTENSION_LIB = "source/ytdlp-lib"
EXTENSION_DENO = "source/deno"
MAX_EXTENSION_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXTENSION_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_MEMBER = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,8}")


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
    PublicProviderHttpTransport("bilibili", profile.get("public_http_headers"))
    _extensions(profile)
    return profile


def _extensions(profile: Mapping) -> list[dict]:
    """Validated extension pins: PyPI wheels extracted as libraries, or Deno's executable."""
    extensions = profile.get("extensions", [])
    if not isinstance(extensions, list) or len(extensions) > 16:
        raise ValueError("invalid SDK extensions")
    names, denos = set(), 0
    for item in extensions:
        if not isinstance(item, dict) or item.get("target") not in {"lib", "deno"}:
            raise ValueError("invalid SDK extension")
        name, filename, url = item.get("name"), item.get("filename"), item.get("url")
        if (not isinstance(name, str) or not _EXTENSION_NAME.fullmatch(name) or name in names
                or not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}\.whl", filename)
                or not isinstance(url, str) or urlsplit(url).scheme != "https" or urlsplit(url).hostname != "files.pythonhosted.org"
                or urlsplit(url).username or urlsplit(url).port not in {None, 443} or urlsplit(url).query or urlsplit(url).fragment
                or urlsplit(url).path.rsplit("/", 1)[-1] != filename
                or not isinstance(item.get("sha256"), str) or not _SHA256.fullmatch(item["sha256"])
                or not isinstance(item.get("spdx"), str) or not 0 < len(item["spdx"]) <= 64):
            raise ValueError("invalid SDK extension pin")
        _byte_count(item.get("size_bytes"), MAX_EXTENSION_BYTES)
        if item["target"] == "deno":
            denos += 1
            for key in ("member", "license_member"):
                if not isinstance(item.get(key), str) or not _MEMBER.fullmatch(item[key]) or ".." in item[key].split("/"):
                    raise ValueError("invalid Deno member")
            if not isinstance(item.get("member_sha256"), str) or not _SHA256.fullmatch(item["member_sha256"]):
                raise ValueError("invalid Deno checksum")
            _byte_count(item.get("member_size_bytes"), 256 * 1024 * 1024)
        names.add(name)
    if extensions and denos != 1:
        raise ValueError("extensions need exactly one JS runtime")
    return extensions


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
        _provision_extensions(root, profile, transport=transport, cancel=cancel)
        return destination
    except (SourceError, DownloadError):
        raise
    except Exception:
        raise _unavailable() from None


def _safe_member(name: str) -> str:
    if not _MEMBER.fullmatch(name) or ".." in name.split("/"):
        raise ValueError("unsafe wheel member")
    return name


def _write_member(zipped: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, *, sha256: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with zipped.open(info) as source, target.open("xb") as stream:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            stream.write(block)
    if sha256 is not None and digest.hexdigest() != sha256:
        raise ValueError("extension member differs from its pin")


def _provision_extensions(root: Path, profile: Mapping, *, transport: HttpTransport | None,
                          cancel: Callable[[], bool] | None) -> None:
    """Build-time: extract the pinned extension wheels into the owned runtime.

    The folders are rebuilt from verified wheels every time; the bundle
    inventory then pins every extracted file. Wheel ``.data`` members are
    never installed, except Deno's single pinned executable.
    """
    extensions = _extensions(profile)
    if not extensions:
        return
    import shutil

    wheels = root / "source" / ".extension-wheels"
    for folder in (root / EXTENSION_LIB, root / EXTENSION_DENO):
        _reject_links(folder)
        if folder.exists():
            shutil.rmtree(folder)
    wheels.mkdir(parents=True, exist_ok=True)
    try:
        for item in extensions:
            archive, size = wheels / item["filename"], int(item["size_bytes"])
            if archive.exists():
                _check_file(archive, item["sha256"], size)
            else:
                candidate = MediaCandidate("reviewed-sdk-extension", item["url"], "progressive", "application/zip")
                MediaMaterializer(transport, max_bytes=size).download(candidate, archive, root=root,
                    expected_sha256=item["sha256"], expected_size=size, cancel=cancel)
            with zipfile.ZipFile(archive) as zipped:
                if item["target"] == "deno":
                    for key, name in (("member", "deno.exe"), ("license_member", "LICENSE")):
                        info = zipped.getinfo(_safe_member(item[key]))
                        _write_member(zipped, info, root / EXTENSION_DENO / name,
                                      sha256=item["member_sha256"] if key == "member" else None)
                    continue
                for info in zipped.infolist():
                    if info.is_dir() or ".data/" in info.filename:
                        continue
                    _write_member(zipped, info, root / EXTENSION_LIB / _safe_member(info.filename))
    finally:
        shutil.rmtree(wheels, ignore_errors=True)


def _verified_extensions(root: Path, index: Mapping[str, Mapping], profile: Mapping) -> dict | None:
    """Every extension file must be in the verified inventory, unchanged, and nothing else may sit beside it."""
    extensions = _extensions(profile)
    if not extensions:
        return None
    prefixes = ("runtime/" + EXTENSION_LIB + "/", "runtime/" + EXTENSION_DENO + "/")
    files = {}
    for name, item in index.items():
        if name.startswith(prefixes):
            size = _byte_count(item["size_bytes"], 256 * 1024 * 1024)
            if not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
                raise ValueError("missing extension digest")
            files[name] = (item["sha256"], size)
    deno = "runtime/" + EXTENSION_DENO + "/deno.exe"
    expected = next(item for item in extensions if item["target"] == "deno")
    if (files.get(deno, ("", 0))[0] != expected["member_sha256"]
            or not any(name.startswith(prefixes[0]) for name in files)):
        raise ValueError("extension inventory differs from its pins")
    return {"lib": root / "runtime" / EXTENSION_LIB, "deno": root / deno, "files": files}


def provider_from_verified_bundle(bundle_root: str | Path, *, artifacts: Sequence[Mapping],
                                  provider_id: str, session_bridge: ProtectedSessionBridge | None = None):
    """Use the bootstrap's verified inventory, never self-pin current disk bytes."""
    if provider_id not in {"bilibili", "douyin", "generic"}:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "unsupported source provider")
    if provider_id == "generic" and session_bridge is not None:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "generic acquisition cannot receive provider sessions", provider_id="generic")
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
        provider_modules, pins = {}, {"python": python_sha, "helper": helper_sha, "sdk_archive": sdk_sha}
        if provider_id == "douyin":
            for key, name in {"douyin_creator": BUNDLE_DOUYIN_CREATOR, "douyin_signer": BUNDLE_DOUYIN_SIGNER}.items():
                provider_modules[key], pins[key] = pin(name, 1024 * 1024)
            license_path, _ = pin(BUNDLE_DOUYIN_LICENSE, 64 * 1024)
            # Inventory pins the actual bytes. Canonical text also checks the
            # upstream notice across Git's Windows CRLF/Unix LF checkouts.
            license_text_sha = hashlib.sha256(license_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            if license_text_sha != "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4":
                raise ValueError("Douyin signer license notice differs from reviewed upstream")
            pin(BUNDLE_DOUYIN_NOTICE, 64 * 1024)
        transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper,
            sdk_archive=sdk, pins=pins, extensions=_verified_extensions(root, index, profile), **provider_modules)
        muxer = FfmpegStreamMuxer(ffmpeg, ffprobe, trusted_root=root,
            ffmpeg_sha256=ffmpeg_sha, ffprobe_sha256=ffprobe_sha)
        materializer = MediaMaterializer(PublicProviderHttpTransport(provider_id, profile["public_http_headers"]))
        if provider_id == "generic":
            return GenericUrlAdapter(GenericSdkTransport(transport), materializer=materializer,
                stream_materializer=StreamMaterializer(muxer, materializer=materializer))
        kwargs = {"transport": YtDlpProviderTransport(provider_id, authenticated_transport=transport),
                  "session_bridge": session_bridge, "materializer": materializer,
                  "stream_materializer": StreamMaterializer(muxer, materializer=materializer)}
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
        # A release with extensions must actually load them (ADR-0040).
        if _extensions(profile) and (health.get("impersonation") is not True or health.get("js_runtime") != "deno"):
            raise ValueError("SDK extensions did not load")
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
