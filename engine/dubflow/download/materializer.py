"""Safe, resumable media materialization behind the source adapter boundary.

The downloader deliberately has no provider logic.  Adapters resolve a source
into a bounded :class:`MediaCandidate`; this module turns a progressive HTTP
(or local) candidate into an atomically published file while retaining a
private ``.part`` file after interruption.  The default transport uses only
Python's standard library so the installed runtime owns the network boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO, Callable, Mapping, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .source_adapter import MediaCandidate, SourceError, SourceErrorCode


MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024 * 1024  # 32 GiB safety ceiling per item.
MAX_REDIRECTS = 5
MAX_CHUNK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class DownloadErrorCode(str, Enum):
    INVALID_SOURCE = "INVALID_SOURCE"
    INVALID_DESTINATION = "INVALID_DESTINATION"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK = "NETWORK"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    UNSUPPORTED = "UNSUPPORTED"
    SIZE_LIMIT = "SIZE_LIMIT"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    RESUME_INVALID = "RESUME_INVALID"
    CANCELLED = "CANCELLED"


class DownloadError(ValueError):
    """Bounded, serializable media materialization failure."""

    def __init__(
        self,
        code: DownloadErrorCode,
        condition: str,
        *,
        retryable: bool = False,
        action: str | None = None,
    ) -> None:
        self.code = DownloadErrorCode(code)
        self.condition = str(condition)[:4096]
        self.retryable = bool(retryable)
        self.action = None if action is None else str(action)[:4096]
        super().__init__(f"{self.code.value}: {self.condition}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "code": self.code.value,
            "condition": self.condition,
            "retryable": self.retryable,
            "action": self.action,
        }


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: BinaryIO
    url: str

    def close(self) -> None:
        close = getattr(self.body, "close", None)
        if callable(close):
            close()


class HttpTransport(Protocol):
    def open(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibHttpTransport:
    """Small app-owned HTTP boundary with bounded, credential-free redirects."""

    def __init__(self, *, timeout_s: float = 60.0, max_redirects: int = MAX_REDIRECTS) -> None:
        if not 0 < timeout_s <= 600:
            raise ValueError("timeout_s must be in (0, 600]")
        if not 0 <= max_redirects <= MAX_REDIRECTS:
            raise ValueError(f"max_redirects must be between 0 and {MAX_REDIRECTS}")
        self.timeout_s = timeout_s
        self.max_redirects = max_redirects
        self._opener = build_opener(_NoRedirect)

    def open(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        current = validate_http_url(url)
        request_headers = {key: value for key, value in (headers or {}).items() if isinstance(key, str) and isinstance(value, str)}
        for hop in range(self.max_redirects + 1):
            request = Request(current, headers=request_headers, method="GET")
            try:
                response = self._opener.open(request, timeout=self.timeout_s)
            except HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    error.close()
                    if not location:
                        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "redirect response omitted Location") from error
                    if hop >= self.max_redirects:
                        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "redirect limit exceeded") from error
                    redirected = validate_http_url(urljoin(current, location))
                    if urlsplit(redirected).hostname != urlsplit(current).hostname:
                        request_headers = {key: value for key, value in request_headers.items() if key.lower() not in {"authorization", "cookie", "proxy-authorization"}}
                    current = redirected
                    continue
                if error.code in {401, 403}:
                    raise DownloadError(DownloadErrorCode.AUTH_REQUIRED, "media endpoint requires authentication", action="authenticate") from error
                if error.code == 429:
                    raise DownloadError(DownloadErrorCode.RATE_LIMITED, "media endpoint rate limited", retryable=True, action="retry_later") from error
                if 400 <= error.code < 500:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, f"media endpoint rejected request ({error.code})") from error
                raise DownloadError(DownloadErrorCode.NETWORK, "media endpoint returned a server error", retryable=True, action="retry") from error
            except (URLError, OSError, TimeoutError) as error:
                raise DownloadError(DownloadErrorCode.NETWORK, "media request failed", retryable=True, action="retry") from error
            status = int(getattr(response, "status", response.getcode()))
            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "redirect response omitted Location")
                if hop >= self.max_redirects:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "redirect limit exceeded")
                redirected = validate_http_url(urljoin(current, location))
                if urlsplit(redirected).hostname != urlsplit(current).hostname:
                    request_headers = {key: value for key, value in request_headers.items() if key.lower() not in {"authorization", "cookie", "proxy-authorization"}}
                current = redirected
                continue
            return HttpResponse(status, _headers(response.headers), response, current)
        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "redirect limit exceeded")


def _headers(headers: object) -> dict[str, str]:
    if not hasattr(headers, "items"):
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}  # type: ignore[union-attr]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def validate_http_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "source URL is empty, overlong or contains control characters")
    value = value.strip()
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "source URL is malformed") from error
    if scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "source URL must be http(s) without credentials")
    if port is not None and not 1 <= port <= 65535:
        raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "source URL port is invalid")
    if parts.fragment:
        # Fragments are never sent to HTTP servers and can conceal a different
        # source reference in logs; callers must canonicalize them first.
        value = value.split("#", 1)[0]
    return value


def _validate_destination(destination: Path, root: Path | None) -> tuple[Path, Path]:
    if not isinstance(destination, Path) or not destination.is_absolute():
        raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "destination must be an absolute path")
    if destination.name in {"", ".", ".."} or any(part in {"", ".", ".."} for part in destination.parts):
        raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "destination contains an unsafe path component")
    if destination.exists() and destination.is_symlink():
        raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "destination must not be a symbolic link")
    if root is not None:
        if not root.is_absolute():
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "download root must be absolute")
        root_resolved = root.resolve()
        destination_resolved = destination.resolve(strict=False)
        try:
            destination_resolved.relative_to(root_resolved)
        except ValueError as error:
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "destination escapes the download root") from error
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "unable to create destination directory") from error
    part = destination.with_name(destination.name + ".part")
    if part.exists() and part.is_symlink():
        raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "partial destination must not be a symbolic link")
    return destination, part


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    resumed: bool

    def to_dict(self) -> dict[str, object]:
        return {"path": str(self.path), "size_bytes": self.size_bytes, "sha256": self.sha256, "resumed": self.resumed}


class MediaMaterializer:
    """Materialize progressive/local candidates with atomic publication."""

    def __init__(self, transport: HttpTransport | None = None, *, max_bytes: int = MAX_DOWNLOAD_BYTES, chunk_bytes: int = 1024 * 1024) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOWNLOAD_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_DOWNLOAD_BYTES}")
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= MAX_CHUNK_BYTES:
            raise ValueError(f"chunk_bytes must be between 1 and {MAX_CHUNK_BYTES}")
        self.transport = transport or UrllibHttpTransport()
        self.max_bytes = max_bytes
        self.chunk_bytes = chunk_bytes

    def download(
        self,
        candidate: MediaCandidate,
        destination: str | Path,
        *,
        root: str | Path | None = None,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        resume: bool = True,
        cancel: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        if not isinstance(candidate, MediaCandidate):
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "candidate is invalid")
        if candidate.kind not in {"progressive", "local"}:
            raise DownloadError(DownloadErrorCode.UNSUPPORTED, "HLS/DASH candidates require a provider muxer")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256.lower())):
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "expected SHA-256 must be 64 lowercase hexadecimal characters")
        if expected_size is not None and (type(expected_size) is not int or not 0 <= expected_size <= self.max_bytes):
            raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "expected size is outside the bounded range")
        destination_path, part_path = _validate_destination(Path(destination), None if root is None else Path(root))
        if candidate.kind == "local":
            result = self._copy_local(candidate.locator, destination_path, part_path, expected_sha256, expected_size, resume, cancel)
        else:
            result = self._download_http(candidate.locator, destination_path, part_path, expected_sha256, expected_size, resume, cancel)
        return result

    def _copy_local(self, locator: str, destination: Path, part: Path, expected_sha256: str | None, expected_size: int | None, resume: bool, cancel: Callable[[], bool] | None) -> DownloadResult:
        source = Path(locator)
        if not source.is_absolute() or not source.is_file() or source.is_symlink():
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "local media source is unavailable or unsafe")
        source_size = source.stat().st_size
        if source_size > self.max_bytes or (expected_size is not None and source_size != expected_size):
            raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "local media exceeds the size contract")
        resumed = False
        start = part.stat().st_size if resume and part.exists() else 0
        if start > source_size:
            start = 0
        if start and not resume:
            start = 0
        mode = "ab" if start else "wb"
        digest = hashlib.sha256()
        if start:
            with part.open("rb") as existing:
                _hash_stream(existing, digest, start, self.chunk_bytes)
            resumed = True
        try:
            with source.open("rb") as src, part.open(mode) as out:
                src.seek(start)
                copied = start
                while True:
                    if cancel and cancel():
                        raise DownloadError(DownloadErrorCode.CANCELLED, "download cancelled at a safe boundary")
                    chunk = src.read(self.chunk_bytes)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > self.max_bytes:
                        raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "download exceeds the configured size limit")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except DownloadError:
            raise
        except OSError as error:
            raise DownloadError(DownloadErrorCode.NETWORK, "local media read failed", retryable=True, action="retry") from error
        return self._publish(part, destination, copied, digest.hexdigest(), expected_sha256, expected_size, resumed)

    def _download_http(self, locator: str, destination: Path, part: Path, expected_sha256: str | None, expected_size: int | None, resume: bool, cancel: Callable[[], bool] | None) -> DownloadResult:
        url = validate_http_url(locator)
        start = part.stat().st_size if resume and part.exists() else 0
        headers = {"Accept-Encoding": "identity"}
        if start:
            headers["Range"] = f"bytes={start}-"
        try:
            response = self.transport.open(url, headers=headers)
        except DownloadError:
            raise
        except Exception as error:
            raise DownloadError(DownloadErrorCode.NETWORK, "media transport failed", retryable=True, action="retry") from error
        try:
            status = int(response.status)
            if status == 416 and start:
                # The server no longer serves the partial range. Restarting is
                # safe because the partial file is private and never published.
                response.close()
                start = 0
                response = self.transport.open(url, headers={"Accept-Encoding": "identity"})
                status = int(response.status)
            if start and status == 200:
                start = 0
            elif status not in {200, 206}:
                if status in {401, 403}:
                    raise DownloadError(DownloadErrorCode.AUTH_REQUIRED, "media endpoint requires authentication", action="authenticate")
                if status == 429:
                    raise DownloadError(DownloadErrorCode.RATE_LIMITED, "media endpoint rate limited", retryable=True, action="retry_later")
                raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, f"media endpoint returned status {status}")
            if status == 206:
                content_range = _header(response.headers, "content-range")
                match = _CONTENT_RANGE.fullmatch(content_range or "")
                if not match or int(match.group(1)) != start:
                    raise DownloadError(DownloadErrorCode.RESUME_INVALID, "server returned an invalid content range")
                total_text = match.group(3)
                total = None if total_text == "*" else int(total_text)
            else:
                total = None
            length_text = _header(response.headers, "content-length")
            length = None
            if length_text is not None:
                try:
                    length = int(length_text)
                except ValueError as error:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "media endpoint returned an invalid content length") from error
                if length < 0:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "media endpoint returned a negative content length")
            if total is None and length is not None:
                total = start + length if status == 206 else length
            if total is not None and total > self.max_bytes:
                raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "download exceeds the configured size limit")
            if expected_size is not None and total is not None and total != expected_size:
                raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "download size does not match the expected size")
            digest = hashlib.sha256()
            resumed = bool(start)
            if start:
                if not part.exists() or part.stat().st_size != start:
                    raise DownloadError(DownloadErrorCode.RESUME_INVALID, "partial file size changed before resume")
                with part.open("rb") as existing:
                    _hash_stream(existing, digest, start, self.chunk_bytes)
            mode = "ab" if start else "wb"
            copied = start
            with part.open(mode) as out:
                while True:
                    if cancel and cancel():
                        raise DownloadError(DownloadErrorCode.CANCELLED, "download cancelled at a safe boundary")
                    chunk = response.body.read(self.chunk_bytes)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise DownloadError(DownloadErrorCode.NETWORK, "media transport returned a non-byte chunk", retryable=True)
                    copied += len(chunk)
                    if copied > self.max_bytes:
                        raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "download exceeds the configured size limit")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if total is not None and copied != total:
                raise DownloadError(DownloadErrorCode.NETWORK, "media response ended before its declared size", retryable=True, action="retry")
            return self._publish(part, destination, copied, digest.hexdigest(), expected_sha256, expected_size, resumed)
        except DownloadError:
            raise
        except (OSError, ValueError) as error:
            raise DownloadError(DownloadErrorCode.NETWORK, "media response could not be written", retryable=True, action="retry") from error
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _publish(self, part: Path, destination: Path, size: int, digest: str, expected_sha256: str | None, expected_size: int | None, resumed: bool) -> DownloadResult:
        if expected_size is not None and size != expected_size:
            raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "download size does not match the expected size")
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise DownloadError(DownloadErrorCode.CHECKSUM_MISMATCH, "download content hash does not match the expected hash")
        if destination.exists() and destination.is_symlink():
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "destination became a symbolic link before publish")
        try:
            os.replace(part, destination)
        except OSError as error:
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "atomic media publication failed") from error
        return DownloadResult(destination, size, digest, resumed)


def _hash_stream(stream: BinaryIO, digest: "hashlib._Hash", size: int, chunk_bytes: int) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(chunk_bytes, remaining))
        if not chunk:
            raise DownloadError(DownloadErrorCode.RESUME_INVALID, "partial file ended before its recorded size")
        digest.update(chunk)
        remaining -= len(chunk)


__all__ = [
    "DownloadError",
    "DownloadErrorCode",
    "DownloadResult",
    "HttpResponse",
    "HttpTransport",
    "MediaMaterializer",
    "UrllibHttpTransport",
    "validate_http_url",
]
