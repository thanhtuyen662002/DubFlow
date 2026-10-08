"""Generic URL acquisition behind the versioned source contract.

The adapter accepts either a recorded metadata transport (required CI), an
app-owned yt-dlp command boundary, or a direct progressive URL.  Provider
specific rules stay in their own adapters; this module only maps generic
metadata to stable source candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlsplit

from ..materializer import DownloadError, DownloadErrorCode, DownloadResult, HttpTransport, MediaMaterializer, UrllibHttpTransport, _reject_links, validate_http_url
from ..source_adapter import MediaCandidate, PageCursor, SourceAdapter, SourceError, SourceErrorCode, SourceIdentity, SourceItem, SourcePage, SubtitleCandidate, canonicalize_url
from ..stream_materializer import StreamMaterializer
from .windows_job import WindowsSourceJob


TICKS_PER_SECOND = 90_000
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_EXTRACTOR_BYTES = 256 * 1024 * 1024


class GenericTransport(Protocol):
    def inspect_url(self, source_url: str) -> Mapping[str, Any]:
        ...


class YtDlpRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        ...


class SubprocessYtDlpRunner:
    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        return self._run(argv, timeout_s=timeout_s)

    def run_with_input(self, argv: Sequence[str], *, timeout_s: float, stdin_bytes: bytes) -> tuple[int, str, str]:
        if not isinstance(stdin_bytes, bytes) or not 0 < len(stdin_bytes) <= 4096:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "extractor private request exceeds its input budget")
        return self._run(argv, timeout_s=timeout_s, stdin_bytes=stdin_bytes)

    def _run(self, argv: Sequence[str], *, timeout_s: float, stdin_bytes: bytes | None = None) -> tuple[int, str, str]:
        if not 0 < timeout_s <= 1800:
            raise ValueError("timeout_s must be in (0, 1800]")
        process = None
        writer = None
        owner = None
        try:
            # Disk-backed private handles avoid unbounded communicate() buffers.
            # Nothing from these raw metadata/diagnostic files is logged.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                process = subprocess.Popen(list(argv), stdin=subprocess.PIPE if stdin_bytes else subprocess.DEVNULL, stdout=stdout,
                    stderr=stderr, shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                owner = WindowsSourceJob(process)
                if stdin_bytes:
                    def write_request():
                        try:
                            process.stdin.write(stdin_bytes)
                            process.stdin.flush()
                        except (OSError, ValueError):
                            pass  # Early exit/timeout still returns a bounded process result.
                        finally:
                            try:
                                process.stdin.close()
                            except OSError:
                                pass
                    writer = threading.Thread(target=write_request, daemon=True)
                    writer.start()
                deadline = time.monotonic() + timeout_s
                while process.poll() is None:
                    self._check_output(stdout, stderr)
                    if time.monotonic() >= deadline:
                        raise SourceError(SourceErrorCode.NETWORK, "yt-dlp inspection timed out", retryable=True, action="retry_with_changed_conditions")
                    time.sleep(0.05)
                self._check_output(stdout, stderr)
                stdout.seek(0)
                stderr.seek(max(0, os.fstat(stderr.fileno()).st_size - 4096))
                return process.returncode, stdout.read(MAX_METADATA_BYTES + 1).decode("utf-8", errors="replace"), stderr.read(4096).decode("utf-8", errors="replace")
        except OSError as error:
            raise SourceError(SourceErrorCode.NETWORK, "yt-dlp process could not be started", retryable=True, action="retry") from error
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
            finally:
                if owner is not None:
                    owner.close()
                if writer is not None:
                    writer.join(timeout=5)

    @staticmethod
    def _check_output(stdout, stderr) -> None:
        if os.fstat(stdout.fileno()).st_size > MAX_METADATA_BYTES or os.fstat(stderr.fileno()).st_size > MAX_DIAGNOSTIC_BYTES:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp output exceeded its bounded inspection budget", action="update_extractor")


class YtDlpTransport:
    """App-owned yt-dlp JSON boundary.  It never invokes a shell."""

    def __init__(self, executable: str | Path, *, trusted_root: str | Path | None = None,
                 expected_sha256: str | None = None, runner: YtDlpRunner | None = None, timeout_s: float = 180.0) -> None:
        path = Path(executable)
        root = None if trusted_root is None else Path(trusted_root)
        if root is None or not root.is_absolute() or not root.is_dir() or not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise SourceError(SourceErrorCode.UNSUPPORTED, "yt-dlp requires an approved runtime root and checksum pin", provider_id="generic", action="repair_runtime")
        try:
            _reject_links(root)
            _reject_links(path)
            if not path.is_absolute() or not path.is_file():
                raise ValueError("not a regular absolute executable")
            path.resolve().relative_to(root.resolve())
        except (DownloadError, ValueError, OSError) as error:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "yt-dlp executable is outside its trusted runtime", provider_id="generic", action="repair_runtime") from error
        if not 0 < timeout_s <= 1800:
            raise ValueError("timeout_s must be in (0, 1800]")
        self.executable = path
        self.trusted_root = root
        self.expected_sha256 = expected_sha256
        self.runner = runner or SubprocessYtDlpRunner()
        self.timeout_s = timeout_s
        self._verify_extractor()

    def _verify_extractor(self) -> None:
        try:
            _reject_links(self.trusted_root)
            _reject_links(self.executable)
            self.executable.resolve().relative_to(self.trusted_root.resolve())
            before = self.executable.stat()
            if not 0 < before.st_size <= MAX_EXTRACTOR_BYTES:
                raise ValueError("extractor size out of range")
            digest = hashlib.sha256()
            read_bytes = 0
            with self.executable.open("rb") as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    read_bytes += len(chunk)
                    if read_bytes > MAX_EXTRACTOR_BYTES:
                        raise ValueError("extractor grew beyond its size budget")
                    digest.update(chunk)
            after = self.executable.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest.hexdigest() != self.expected_sha256:
                raise ValueError("extractor checksum mismatch")
        except (OSError, ValueError, DownloadError) as error:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "app-owned extractor checksum verification failed", provider_id="generic", action="repair_runtime") from error

    def inspect_url(self, source_url: str) -> Mapping[str, Any]:
        url = validate_http_url(source_url)
        self._verify_extractor()
        argv = (str(self.executable), "--dump-single-json", "--no-warnings", "--skip-download", "--no-playlist",
                "--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-js-runtimes", "--no-remote-components", "--", url)
        return_code, stdout, stderr = self.runner.run(argv, timeout_s=self.timeout_s)
        if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_METADATA_BYTES or not isinstance(stderr, str) or len(stderr.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp output exceeded its bounded inspection budget", provider_id="generic", action="update_extractor")
        if return_code != 0:
            raw_message = stderr if isinstance(stderr, str) else ""
            message = _safe_process_error(raw_message)
            lowered = raw_message.lower()
            if "login" in lowered or "private" in lowered or "authentication" in lowered:
                code = SourceErrorCode.AUTH_REQUIRED
                retryable = False
                action = "authenticate"
            elif "429" in lowered or "rate" in lowered or "too many" in lowered:
                code = SourceErrorCode.RATE_LIMITED
                retryable = True
                action = "retry_later"
            elif "not found" in lowered or "does not exist" in lowered or "unavailable" in lowered:
                code = SourceErrorCode.NOT_FOUND
                retryable = False
                action = None
            elif "unsupported" in lowered or "extractor" in lowered or "no suitable" in lowered:
                code = SourceErrorCode.SOURCE_CHANGED
                retryable = False
                action = "update_extractor"
            else:
                code = SourceErrorCode.NETWORK
                retryable = True
                action = "retry"
            raise SourceError(code, "yt-dlp could not inspect the source", provider_id="generic", retryable=retryable, action=action)
        try:
            payload = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp returned malformed metadata", provider_id="generic") from error
        if not isinstance(payload, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp metadata is not an object", provider_id="generic")
        return payload


def _safe_process_error(value: object) -> str:
    text = value if isinstance(value, str) else ""
    # stderr can contain the original URL and query tokens.  Keep only a
    # bounded, line-oriented diagnostic and redact common credential forms.
    text = text.replace("\x00", " ")[:4096]
    redacted: list[str] = []
    for line in text.splitlines()[:16]:
        if any(token in line.lower() for token in ("cookie", "authorization", "bearer", "token", "password", "secret")):
            redacted.append("[redacted]")
        else:
            redacted.append(line[:512])
    return " ".join(redacted)[:4096]


class DirectUrlTransport:
    """Inspect a direct media URL without retaining its response body."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self.transport = transport or UrllibHttpTransport()

    def inspect_url(self, source_url: str) -> Mapping[str, Any]:
        url = validate_http_url(source_url)
        try:
            response = self.transport.open(url, headers={"Accept-Encoding": "identity"})
        except DownloadError as error:
            code_map = {
                DownloadErrorCode.AUTH_REQUIRED: SourceErrorCode.AUTH_REQUIRED,
                DownloadErrorCode.RATE_LIMITED: SourceErrorCode.RATE_LIMITED,
                DownloadErrorCode.SOURCE_CHANGED: SourceErrorCode.SOURCE_CHANGED,
                DownloadErrorCode.NETWORK: SourceErrorCode.NETWORK,
            }
            raise SourceError(code_map.get(error.code, SourceErrorCode.NETWORK), "generic URL inspection failed", retryable=error.retryable, action=error.action) from error
        try:
            content_type = _header(response.headers, "content-type") or "application/octet-stream"
            content_length = _header(response.headers, "content-length")
            size = None
            if content_length is not None:
                try:
                    size = int(content_length)
                except ValueError:
                    raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic URL returned malformed content length", provider_id="generic")
            if content_type.split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}:
                raise SourceError(SourceErrorCode.UNSUPPORTED, "generic URL is an HTML page; an app-owned extractor is required", provider_id="generic")
            return {
                "id": hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest(),
                "title": _title_from_url(url),
                "webpage_url": url,
                "url": url,
                "protocol": _kind_for_url(url, content_type),
                "ext": _extension_for_url(url, content_type),
                "mime_type": content_type.split(";", 1)[0].strip().lower(),
                "filesize": size,
            }
        finally:
            response.close()


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _text(value: object, name: str, *, required: bool = True, limit: int = 4096) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"generic field {name} is malformed", provider_id="generic")
    return value.strip()


def _title_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path).rstrip("/")
    name = Path(path).name if path else "download"
    return name[:4096] or "download"


def _extension_for_url(url: str, content_type: str) -> str:
    ext = Path(urlsplit(url).path).suffix.lower().lstrip(".")
    if ext and re_safe_extension(ext):
        return ext
    return content_type.split("/", 1)[-1].split(";", 1)[0].lower()[:16] or "bin"


def re_safe_extension(value: str) -> bool:
    return value.isascii() and value.replace("_", "").replace("-", "").isalnum() and len(value) <= 16


def _kind_for_url(url: str, content_type: str) -> str:
    lower = f"{url} {content_type}".lower()
    if "m3u8" in lower or "mpegurl" in lower:
        return "hls"
    if "mpd" in lower or "dash" in lower:
        return "dash"
    return "progressive"


def _duration_ticks(value: object) -> int | None:
    if value is None:
        return None
    try:
        seconds = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic duration is malformed", provider_id="generic")
    if not seconds.is_finite() or seconds < 0:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic duration is outside the valid range", provider_id="generic")
    ticks = (seconds * TICKS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_UP)
    if ticks > (1 << 64) - 1:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic duration exceeds the timeline range", provider_id="generic")
    return int(ticks)


def _candidate(raw: Mapping[str, Any], index: int) -> MediaCandidate | None:
    locator = raw.get("url", raw.get("manifest_url"))
    if not isinstance(locator, str) or not locator.strip():
        return None
    try:
        locator = validate_http_url(locator)
    except DownloadError as error:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic media candidate URL is malformed", provider_id="generic") from error
    audio_only = raw.get("vcodec") == "none" and raw.get("acodec") not in {None, "none"}
    container = "webm" if raw.get("ext") == "webm" else "mp4"
    mime = str(raw.get("mime_type", ("audio/" if audio_only else "video/") + container))[:128]
    protocol = str(raw.get("protocol", "https"))
    kind = "hls" if protocol in {"m3u8", "m3u8_native"} or "m3u8" in locator.lower() else ("dash" if protocol == "http_dash_segments" or ".mpd" in locator.lower() else "progressive")
    width = raw.get("width") if type(raw.get("width")) is int and raw.get("width") > 0 else None
    height = raw.get("height") if type(raw.get("height")) is int and raw.get("height") > 0 else None
    has_audio = "acodec" not in raw or raw.get("acodec") not in {None, "none"}
    return MediaCandidate(str(raw.get("format_id", f"format-{index}")), locator, kind, mime if "/" in mime else "video/mp4", width, height, bool(has_audio))


def _subtitles(raw: Mapping[str, Any]) -> tuple[SubtitleCandidate, ...]:
    values = raw.get("subtitles", raw.get("automatic_captions", {}))
    if not isinstance(values, Mapping):
        return ()
    result: list[SubtitleCandidate] = []
    for language, candidates in list(values.items())[:256]:
        if not isinstance(language, str) or not isinstance(candidates, list):
            continue
        for index, candidate in enumerate(candidates[:256]):
            if not isinstance(candidate, Mapping) or not isinstance(candidate.get("url"), str):
                continue
            try:
                locator = validate_http_url(candidate["url"])
                fmt = str(candidate.get("ext", "vtt")).lower()
                if fmt not in {"srt", "vtt", "ass", "json"}:
                    fmt = "vtt"
                result.append(SubtitleCandidate(f"{language}-{index}", locator, language, fmt))
            except (DownloadError, SourceError):
                continue
    return tuple(result[:256])


class GenericUrlAdapter:
    provider_id = "generic"

    def __init__(self, transport: GenericTransport | None = None, *, materializer: MediaMaterializer | None = None, stream_materializer: StreamMaterializer | None = None) -> None:
        self._transport = transport or DirectUrlTransport()
        self._materializer = materializer or MediaMaterializer()
        self._stream_materializer = stream_materializer

    @classmethod
    def can_handle(cls, source_ref: str) -> bool:
        try:
            validate_http_url(source_ref)
            return True
        except DownloadError:
            return False

    def inspect(self, source_ref: str) -> SourceItem:
        try:
            canonical = canonicalize_url(source_ref)
        except (SourceError, ValueError) as error:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "generic source URL is malformed", provider_id=self.provider_id) from error
        try:
            payload = self._transport.inspect_url(canonical)
        except SourceError:
            raise
        except DownloadError as error:
            raise SourceError(SourceErrorCode.NETWORK, "generic source inspection failed", provider_id=self.provider_id, retryable=error.retryable, action=error.action) from error
        except Exception as error:
            raise SourceError(SourceErrorCode.NETWORK, "generic source inspection failed", provider_id=self.provider_id, retryable=True, action="retry") from error
        if not isinstance(payload, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic metadata is not an object", provider_id=self.provider_id)
        source_id = _text(payload.get("id"), "id", required=False, limit=512) or hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        title = _text(payload.get("title"), "title", required=False) or _title_from_url(canonical)
        raw_formats = payload.get("formats")
        candidates: list[MediaCandidate] = []
        if isinstance(raw_formats, list):
            for index, raw in enumerate(raw_formats[:256]):
                if isinstance(raw, Mapping):
                    candidate = _candidate(raw, index)
                    if candidate is not None:
                        candidates.append(candidate)
        if not candidates:
            candidate = _candidate(payload, 0)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "generic source has no downloadable media candidate", provider_id=self.provider_id, source_id=source_id)
        duration = payload.get("duration_ticks")
        if duration is None:
            duration = _duration_ticks(payload.get("duration"))
        elif type(duration) is not int:
            duration = _duration_ticks(duration)
        webpage_url = payload.get("webpage_url")
        if not isinstance(webpage_url, str) or not webpage_url.strip():
            webpage_url = canonical
        return SourceItem(
            SourceIdentity(self.provider_id, source_id, webpage_url),
            title,
            _text(payload.get("description"), "description", required=False, limit=16_384),
            duration,
            tuple(candidates),
            _subtitles(payload),
        )

    def enumerate_channel(self, channel_id: str, *, cursor: str | None = None, page_size: int = 50) -> SourcePage:
        raise SourceError(SourceErrorCode.UNSUPPORTED, "generic URL enumeration requires a playlist-capable extractor", provider_id=self.provider_id)

    def download(self, item: SourceItem, destination: str | Path, *, candidate_id: str | None = None, **kwargs: object) -> DownloadResult:
        candidates = [candidate for candidate in item.media_candidates if candidate.mime_type.startswith("video/") and (candidate_id is None or candidate.candidate_id == candidate_id)]
        if not candidates:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "requested generic media candidate was not found", provider_id=self.provider_id, source_id=item.identity.source_id)
        try:
            candidates.sort(key=lambda candidate: (-((candidate.width or 0) * (candidate.height or 0)), -(candidate.height or 0), not candidate.has_audio, candidate.candidate_id))
            selected = candidates[0]
            audio = None if selected.has_audio else next((candidate for candidate in item.media_candidates if candidate.mime_type.startswith("audio/") and candidate.has_audio), None)
            if self._stream_materializer is not None:
                return self._stream_materializer.download(selected, destination, audio=audio, **kwargs)
            if not selected.has_audio:
                raise DownloadError(DownloadErrorCode.UNSUPPORTED, "selected streams require the app-owned source muxer")
            return self._materializer.download(selected, destination, **kwargs)
        except DownloadError as error:
            mapping = {
                DownloadErrorCode.AUTH_REQUIRED: SourceErrorCode.AUTH_REQUIRED,
                DownloadErrorCode.RATE_LIMITED: SourceErrorCode.RATE_LIMITED,
                DownloadErrorCode.SOURCE_CHANGED: SourceErrorCode.SOURCE_CHANGED,
                DownloadErrorCode.NETWORK: SourceErrorCode.NETWORK,
                DownloadErrorCode.UNSUPPORTED: SourceErrorCode.UNSUPPORTED,
            }
            raise SourceError(mapping.get(error.code, SourceErrorCode.NETWORK), "generic media download failed", provider_id=self.provider_id, source_id=item.identity.source_id, retryable=error.retryable, action=error.action) from error


__all__ = ["DirectUrlTransport", "GenericUrlAdapter", "GenericTransport", "SubprocessYtDlpRunner", "YtDlpRunner", "YtDlpTransport"]
