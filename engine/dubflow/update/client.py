"""GitHub release adapter and bounded, integrity-checked setup downloads."""

from __future__ import annotations

from functools import cmp_to_key
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import UpdateError
from .trust import VERSION_PATTERN, _validate_setup, verify_manifest
from .versions import compare


RELEASES_URL = "https://api.github.com/repos/thanhtuyen662002/DubFlow/releases?per_page=20"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "DubFlow-updater"}
TIMEOUT = 15
MAX_MANIFEST_BYTES = 64 * 1024
MAX_RELEASES_BYTES = 2 * 1024 * 1024
_NETWORK_ERRORS = (OSError, URLError, HTTPException)


def urllib_fetch(url: str, *, max_bytes: int) -> bytes:
    """Fetch bounded JSON bytes; the injectable fetch API uses these arguments."""

    with urlopen(Request(url, headers=HEADERS), timeout=TIMEOUT) as response:
        return response.read(max_bytes + 1)


def _json_fetch(fetch: Callable, url: str, max_bytes: int):
    try:
        raw = fetch(url, max_bytes=max_bytes)
    except _NETWORK_ERRORS as exc:
        raise UpdateError("NETWORK", "unable to fetch update metadata") from exc
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise UpdateError("MANIFEST_INVALID", "update metadata exceeds the response limit or is not bytes")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateError("MANIFEST_INVALID", "update metadata is not valid JSON") from exc


def check(current_version: str, channel: str, *, fetch: Callable = urllib_fetch) -> dict:
    """Return the highest newer eligible release after verifying its manifest."""

    if channel not in {"stable", "candidate"}:
        raise UpdateError("MANIFEST_INVALID", "channel must be stable or candidate")
    try:
        compare(current_version, current_version)
    except (ValueError, TypeError) as exc:
        raise UpdateError("MANIFEST_INVALID", "invalid current version") from exc
    releases = _json_fetch(fetch, RELEASES_URL, MAX_RELEASES_BYTES)
    if not isinstance(releases, list):
        raise UpdateError("MANIFEST_INVALID", "GitHub releases response must be a list")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft", False):
            continue
        if type(release.get("prerelease")) is not bool:
            continue
        if channel == "stable" and release["prerelease"]:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or not VERSION_PATTERN.fullmatch(tag[1:]):
            continue
        version = tag[1:]
        try:
            if compare(version, current_version) <= 0:
                continue
        except ValueError:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == "update-manifest.json":
                candidates.append((version, asset.get("browser_download_url")))
                break
    if not candidates:
        return {"available": False}
    candidates.sort(key=cmp_to_key(lambda left, right: compare(left[0], right[0])), reverse=True)
    version, url = candidates[0]
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
    except ValueError as exc:
        raise UpdateError("MANIFEST_INVALID", "invalid update manifest asset URL") from exc
    if parsed is None or parsed.scheme != "https" or not parsed.netloc:
        raise UpdateError("MANIFEST_INVALID", "update manifest asset must use HTTPS")
    manifest = verify_manifest(_json_fetch(fetch, url, MAX_MANIFEST_BYTES))
    if manifest["version"] != version:
        raise UpdateError("MANIFEST_INVALID", "update manifest version does not match release tag")
    return {"available": True, **{field: manifest[field] for field in (
        "version", "channel", "notes", "published_at_utc", "setup",
    )}}


def urllib_fetch_stream(url: str) -> Iterable[bytes]:
    with urlopen(Request(url, headers=HEADERS), timeout=TIMEOUT) as response:
        while chunk := response.read(1024 * 1024):
            yield chunk


def download(
    manifest_setup: dict,
    dest_dir: Path,
    *,
    fetch_stream: Callable = urllib_fetch_stream,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Stream to a partial file, then publish only a verified setup executable.

    fetch_stream(url) yields byte chunks; progress receives (received, expected).
    Network failures and integrity failures remove this attempt's partial file.
    """

    _validate_setup(manifest_setup)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / manifest_setup["name"]
    partial = target.with_name(target.name + ".partial")
    if partial.is_symlink() or target.is_symlink():
        raise UpdateError("DOWNLOAD_CORRUPT", "setup destination cannot be a symbolic link")
    expected = manifest_setup["size_bytes"]
    digest = hashlib.sha256()
    received = 0
    stream = None
    try:
        # Remove an interrupted attempt, then exclusively create our own file.
        partial.unlink(missing_ok=True)
        with partial.open("xb") as output:
            try:
                stream = iter(fetch_stream(manifest_setup["url"]))
            except _NETWORK_ERRORS as exc:
                raise UpdateError("NETWORK", "unable to download update setup") from exc
            while True:
                try:
                    chunk = next(stream)
                except StopIteration:
                    break
                except _NETWORK_ERRORS as exc:
                    raise UpdateError("NETWORK", "update setup download was interrupted") from exc
                if not isinstance(chunk, bytes):
                    raise UpdateError("DOWNLOAD_CORRUPT", "setup download did not yield bytes")
                received += len(chunk)
                if received > expected:
                    raise UpdateError("DOWNLOAD_CORRUPT", "setup download exceeds expected size")
                output.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(received, expected)
            if received != expected or digest.hexdigest() != manifest_setup["sha256"]:
                raise UpdateError("DOWNLOAD_CORRUPT", "setup download size or SHA-256 does not match")
            output.flush()
            os.fsync(output.fileno())
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    return target
