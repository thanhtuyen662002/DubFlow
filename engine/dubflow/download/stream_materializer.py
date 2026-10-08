"""Checkpoint complete source streams, then publish a verified local MP4.

Only complete MP4/WebM objects are accepted. DASH manifests, HLS playlists
and fragment enumeration require a separate bounded segment implementation.
The media processes receive private local files, never provider URLs/tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .materializer import (
    DownloadError, DownloadErrorCode, DownloadResult, MAX_DOWNLOAD_BYTES,
    MediaMaterializer, _file_digest, _reject_links, _validate_destination,
    _write_receipt, validate_http_url,
)
from .source_adapter import MediaCandidate


PRODUCER_VERSION = "source-stream-mux/1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TICKS_PER_SECOND = 90_000
_MAX_PROBE_BYTES = 1024 * 1024
_MAX_LOG_BYTES = 64 * 1024
_FORMATS = "mov,matroska,webm"


@contextmanager
def _ownership(destination: Path):
    """An OS lease prevents two workers mutating the same stream scratch."""
    path = destination.with_name(destination.name + ".streams.lock")
    _reject_links(path)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DownloadError(DownloadErrorCode.RESUME_INVALID, "source destination already has a live owner", retryable=True, action="wait_for_owner") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _cancelled(cancel: Callable[[], bool] | None) -> None:
    if cancel and cancel():
        raise DownloadError(DownloadErrorCode.CANCELLED, "source acquisition cancelled at a safe boundary")


@dataclass(frozen=True)
class StreamInfo:
    kind: str
    codec: str
    start_ticks: int
    duration_ticks: int


def _ticks(value: object) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > 10_000_000:
            raise ValueError()
        return int((number * _TICKS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError, OverflowError) as error:
        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "stream timestamps are malformed or unbounded") from error


def _stream_info(raw: object) -> StreamInfo | None:
    if not isinstance(raw, dict) or raw.get("codec_type") not in {"video", "audio"}:
        return None
    codec = raw.get("codec_name")
    if not isinstance(codec, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", codec):
        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "source stream codec is unavailable")
    try:
        base = Fraction(raw["time_base"])
        duration = raw.get("duration_ts")
        start = raw.get("start_pts")
        if base <= 0 or base > 1 or base.denominator > (1 << 32):
            raise ValueError()
        if type(duration) is int and type(start) is int:
            duration_ticks = round(duration * base * _TICKS_PER_SECOND)
            start_ticks = round(start * base * _TICKS_PER_SECOND)
        else:
            duration_ticks = _ticks(raw.get("duration"))
            start_ticks = _ticks(raw.get("start_time", 0))
        if not 0 < duration_ticks <= 10_000_000 * _TICKS_PER_SECOND or abs(start_ticks) > 10_000_000 * _TICKS_PER_SECOND:
            raise ValueError()
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as error:
        raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "source stream lacks bounded duration/time base") from error
    return StreamInfo(raw["codec_type"], codec, start_ticks, duration_ticks)


class FfmpegStreamMuxer:
    """Pinned app-owned subprocess boundary for local stream copy and probe."""

    def __init__(self, ffmpeg_path: str | Path, ffprobe_path: str | Path, *,
                 trusted_root: str | Path, ffmpeg_sha256: str, ffprobe_sha256: str,
                 timeout_s: float = 600.0) -> None:
        root = Path(trusted_root)
        if not root.is_absolute() or not root.is_dir():
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "media runtime root is unavailable")
        _reject_links(root)
        if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 1800:
            raise ValueError("timeout_s must be finite and in (0, 1800]")
        self.timeout_s = float(timeout_s)
        self._pins: tuple[tuple[Path, str], ...] = tuple(
            self._pin(Path(path), digest, root) for path, digest in
            ((ffmpeg_path, ffmpeg_sha256), (ffprobe_path, ffprobe_sha256))
        )
        self.ffmpeg_path, self.ffprobe_path = (entry[0] for entry in self._pins)
        self.fingerprint = hashlib.sha256(json.dumps(
            [PRODUCER_VERSION, ffmpeg_sha256, ffprobe_sha256], separators=(",", ":")
        ).encode("ascii")).hexdigest()

    @staticmethod
    def _pin(path: Path, digest: str, root: Path) -> tuple[Path, str]:
        if not path.is_absolute() or not path.is_file() or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "media executable or SHA-256 pin is invalid")
        _reject_links(path)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "media executable escapes its runtime root") from error
        if _file_digest(path) != digest:
            raise DownloadError(DownloadErrorCode.CHECKSUM_MISMATCH, "media executable differs from its pinned version")
        return path, digest

    def _run(self, argv: tuple[str, ...], cancel: Callable[[], bool] | None, *,
             output: Path | None = None, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
        _cancelled(cancel)
        # Recheck before launch: a mutable installation may not silently change
        # the producer for a resumed source acquisition.
        for path, digest in self._pins:
            if _file_digest(path) != digest:
                raise DownloadError(DownloadErrorCode.CHECKSUM_MISMATCH, "pinned media runtime changed before launch")
        process = None
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                deadline = time.monotonic() + self.timeout_s
                waiter = threading.Event()
                while True:
                    code = process.poll()
                    _cancelled(cancel)
                    if os.fstat(stdout.fileno()).st_size > _MAX_PROBE_BYTES or os.fstat(stderr.fileno()).st_size > _MAX_LOG_BYTES:
                        raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "media process diagnostics exceed the bounded limit")
                    if output is not None and output.stat().st_size > max_bytes:
                        raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "muxed media exceeds the configured size limit")
                    if code is not None:
                        break
                    if time.monotonic() >= deadline:
                        raise DownloadError(DownloadErrorCode.NETWORK, "media process timed out", retryable=True, action="reduce_item_or_change_runtime")
                    waiter.wait(0.1)
                if code != 0:
                    # Raw diagnostics may contain provider metadata embedded in
                    # the media. They never leave this process boundary.
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "local stream probe or mux failed")
                stdout.seek(0)
                return stdout.read(_MAX_PROBE_BYTES)
            except OSError as error:
                raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "media runtime could not read or write private files") from error
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def probe(self, path: Path, *, cancel: Callable[[], bool] | None = None) -> tuple[StreamInfo, ...]:
        _reject_links(path)
        if not path.is_absolute() or not path.is_file():
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "local stream is unavailable")
        raw = self._run((str(self.ffprobe_path), "-v", "error", "-protocol_whitelist", "file",
            "-format_whitelist", _FORMATS, "-print_format", "json", "-show_streams", str(path)), cancel)
        try:
            payload = json.loads(raw)
            streams = payload.get("streams") if isinstance(payload, dict) else None
            if not isinstance(streams, list) or not 1 <= len(streams) <= 32:
                raise ValueError()
            return tuple(info for value in streams if (info := _stream_info(value)) is not None)
        except (ValueError, UnicodeError) as error:
            raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "local stream probe metadata is malformed") from error

    def mux(self, video: Path, audio: Path | None, output: Path, *,
            cancel: Callable[[], bool] | None, max_bytes: int) -> None:
        args = [str(self.ffmpeg_path), "-v", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file", "-format_whitelist", _FORMATS, "-i", str(video)]
        if audio is not None:
            args.extend(("-protocol_whitelist", "file", "-format_whitelist", _FORMATS, "-i", str(audio)))
        args.extend(("-map", "0:v:0", "-map", "1:a:0" if audio is not None else "0:a:0",
                     "-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1",
                     "-movflags", "+faststart", "-f", "mp4", str(output)))
        self._run(tuple(args), cancel, output=output, max_bytes=max_bytes)


class StreamMaterializer:
    """Recover independently completed streams; publish only a verified pair."""

    def __init__(self, muxer: FfmpegStreamMuxer, *, materializer: MediaMaterializer | None = None) -> None:
        self.muxer = muxer
        self.materializer = materializer or MediaMaterializer()

    @staticmethod
    def _direct(candidate: MediaCandidate) -> MediaCandidate:
        if candidate.kind == "local":
            return candidate
        url = validate_http_url(candidate.locator)
        path = urlsplit(url).path.lower()
        if (candidate.kind not in {"progressive", "dash"} or path.endswith((".mpd", ".m3u8"))
            or candidate.mime_type not in {"video/mp4", "audio/mp4", "video/webm", "audio/webm"}):
            raise DownloadError(DownloadErrorCode.UNSUPPORTED, "source requires bounded segment acquisition or an unsupported container")
        # Bilibili DASH baseUrl can identify a complete fragmented MP4 object.
        # The local restricted demux/probe must still prove its stream/duration.
        return replace(candidate, kind="progressive")

    def download(self, video: MediaCandidate, destination: str | Path, *,
                 audio: MediaCandidate | None = None, root: str | Path | None = None,
                 expected_sha256: str | None = None, expected_size: int | None = None,
                 resume: bool = True, cancel: Callable[[], bool] | None = None) -> DownloadResult:
        destination, _ = _validate_destination(Path(destination), None if root is None else Path(root))
        with _ownership(destination):
            return self._download(video, destination, audio=audio, expected_sha256=expected_sha256,
                expected_size=expected_size, resume=resume, cancel=cancel)

    def _download(self, video: MediaCandidate, destination: Path, *,
                  audio: MediaCandidate | None, expected_sha256: str | None,
                  expected_size: int | None, resume: bool, cancel: Callable[[], bool] | None) -> DownloadResult:
        self._direct(video)
        if not video.mime_type.startswith("video/") or (audio is None and not video.has_audio):
            raise DownloadError(DownloadErrorCode.UNSUPPORTED, "selected video has no audio stream to preserve")
        if audio is not None:
            self._direct(audio)
            if not audio.mime_type.startswith("audio/") or not audio.has_audio:
                raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "selected companion is not an audio candidate")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)):
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "final SHA-256 pin is invalid")
        if expected_size is not None and (type(expected_size) is not int or not 0 < expected_size <= self.materializer.max_bytes):
            raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "final size is outside the configured limit")
        if destination.suffix.lower() != ".mp4":
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "stream acquisition output must be MP4")
        inputs = [video, audio]
        # Only digests reach disk. IDs/locators can contain provider tokens.
        identity = hashlib.sha256(json.dumps([self.muxer.fingerprint,
            [candidate.to_dict() if candidate else None for candidate in inputs]],
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        private = destination.with_name(destination.name + ".streams") / identity
        _reject_links(private)
        private.mkdir(parents=True, exist_ok=True)
        recovered = False
        paths: list[Path] = []
        for index, candidate in enumerate(inputs):
            if candidate is None:
                continue
            _cancelled(cancel)
            path = private / ("video.stream" if index == 0 else "audio.stream")
            receipt = path.with_name(path.name + ".json")
            _reject_links(path)
            _reject_links(receipt)
            valid = False
            if resume and path.is_file() and receipt.is_file() and receipt.stat().st_size <= 4096:
                try:
                    record = json.loads(receipt.read_text(encoding="utf-8"))
                    valid = (isinstance(record, dict) and record.get("schema_version") == 1
                        and record.get("identity_sha256") == identity and type(record.get("size_bytes")) is int
                        and 0 < record["size_bytes"] <= self.materializer.max_bytes
                        and path.stat().st_size == record["size_bytes"]
                        and _file_digest(path) == record.get("sha256"))
                except (OSError, ValueError, UnicodeError):
                    valid = False
            if valid:
                recovered = True
            else:
                result = self.materializer.download(self._direct(candidate), path, root=private,
                    resume=resume, cancel=cancel)
                recovered |= result.resumed
                _write_receipt(receipt, {"schema_version": 1, "producer_version": PRODUCER_VERSION,
                    "identity_sha256": identity, "size_bytes": result.size_bytes, "sha256": result.sha256})
            paths.append(path)
        video_info = next((info for info in self.muxer.probe(paths[0], cancel=cancel) if info.kind == "video"), None)
        audio_info = next((info for info in self.muxer.probe(paths[-1], cancel=cancel) if info.kind == "audio"), None)
        if video_info is None or audio_info is None:
            raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "selected objects do not contain both video and audio")
        # Avoid disguising a truncated companion with FFmpeg's -shortest.
        if (abs(video_info.start_ticks - audio_info.start_ticks) > _TICKS_PER_SECOND
            or abs(video_info.duration_ticks - audio_info.duration_ticks) > _TICKS_PER_SECOND):
            raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "selected audio/video timelines are incompatible")
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".source-mux-", suffix=".mp4", dir=destination.parent)
            os.close(descriptor)
            temporary = Path(name)
            self.muxer.mux(paths[0], paths[1] if audio is not None else None, temporary,
                cancel=cancel, max_bytes=self.materializer.max_bytes)
            actual = self.muxer.probe(temporary, cancel=cancel)
            if len(actual) != 2 or {info.kind for info in actual} != {"video", "audio"}:
                raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "muxed output is missing selected streams")
            for expected in (video_info, audio_info):
                current = next(info for info in actual if info.kind == expected.kind)
                if current.codec != expected.codec or abs(current.duration_ticks - expected.duration_ticks) > _TICKS_PER_SECOND:
                    raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "muxed output altered the selected codec or duration")
            size = temporary.stat().st_size
            digest = _file_digest(temporary)
            if not 0 < size <= self.materializer.max_bytes or (expected_size is not None and size != expected_size):
                raise DownloadError(DownloadErrorCode.SIZE_LIMIT, "muxed output violates the final size contract")
            if expected_sha256 is not None and digest != expected_sha256:
                raise DownloadError(DownloadErrorCode.CHECKSUM_MISMATCH, "muxed output violates the final SHA-256 contract")
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            _cancelled(cancel)
            _reject_links(destination)
            os.replace(temporary, destination)
            return DownloadResult(destination, size, digest, recovered)
        except OSError as error:
            raise DownloadError(DownloadErrorCode.INVALID_DESTINATION, "verified source could not be atomically published") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


__all__ = ["FfmpegStreamMuxer", "StreamInfo", "StreamMaterializer"]
