"""Safe app-owned FFmpeg/FFprobe media adapters.

This module is deliberately independent from the fixture renderer and from
the higher-level export adapter.  It is the process boundary used by the
production worker:

* FFprobe output is parsed as JSON and represented with reduced rational
  time-bases and integer timestamp/duration values.
* FFmpeg receives an argv tuple with ``shell=False``.  User paths never pass
  through a command shell or a filter expression by default.
* Every output is written to a private temporary sibling and atomically
  renamed only after the command succeeds and produced non-empty bytes.
* Executables must be app-owned absolute regular files.  A bare ``ffmpeg``
  command from ``PATH`` is rejected so a clean machine cannot silently use a
  different installation.

The adapter does not choose a model, infer speech, or provide fixture output.
Those decisions belong to the worker and its versioned model/runtime
manifests.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from engine.dubflow.security import SecurityBoundaryError, build_subprocess_plan, redact_diagnostics


_U64_MAX = (1 << 64) - 1
_I64_MIN = -(1 << 63)
_I64_MAX = (1 << 63) - 1
_RATIONAL = re.compile(r"^(?P<numerator>[0-9]+)\s*/\s*(?P<denominator>[0-9]+)$")
_STREAM_TYPES = frozenset({"video", "audio", "subtitle", "data", "attachment"})
_MAX_DIAGNOSTIC = 4096
_DEFAULT_TIMEOUT_SECONDS = 15 * 60
# The shipped Windows FFmpeg runtime is the LGPL build, so GPL-only libx264
# is intentionally unavailable. Media Foundation's H.264 encoder is part of
# the Windows FFmpeg build and keeps the production bundle license-compatible
# while preserving the H.264 output contract without requiring a GPU.
_WINDOWS_H264_ENCODER = "h264_mf"


class MediaAdapterError(RuntimeError):
    """Structured failure at the app-owned media process boundary."""

    def __init__(self, code: str, condition: str, *, retryable: bool = False) -> None:
        self.code = _safe_text(code, limit=128)
        self.condition = _safe_text(condition, limit=_MAX_DIAGNOSTIC)
        self.retryable = bool(retryable)
        super().__init__(f"{self.code}: {self.condition}")


def _safe_text(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\x00", " ").replace("\r", " ").replace("\n", " ").strip()
    return (text or "media adapter failure")[:limit]


def _require_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


@dataclass(frozen=True, order=True)
class Rational:
    """A positive reduced rational used for media time-bases and rates."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _require_int(self.numerator, "rational numerator", minimum=1, maximum=_U64_MAX)
        _require_int(self.denominator, "rational denominator", minimum=1, maximum=_U64_MAX)
        divisor = math.gcd(self.numerator, self.denominator)
        if divisor != 1:
            object.__setattr__(self, "numerator", self.numerator // divisor)
            object.__setattr__(self, "denominator", self.denominator // divisor)

    @classmethod
    def parse(cls, value: Any, name: str = "rational") -> "Rational":
        if not isinstance(value, str):
            raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be a rational string")
        match = _RATIONAL.fullmatch(value.strip())
        if match is None:
            raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must use numerator/denominator form")
        try:
            return cls(int(match.group("numerator")), int(match.group("denominator")))
        except (MediaAdapterError, ValueError) as error:
            if isinstance(error, MediaAdapterError):
                raise
            raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} is not a valid rational") from error

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}


# The durable application timeline uses integer milliseconds.  Stream and
# container timestamps remain available in their native bases in the probe,
# while all comparisons and exported timeline identities use this explicit
# canonical base through exact rational rescaling.
CANONICAL_TIME_BASE = Rational(1, 1000)


def _round_fraction(value: Fraction, mode: str, name: str) -> int:
    if mode == "exact":
        if value.denominator != 1:
            raise MediaAdapterError("MEDIA_TIMELINE_NON_INTEGRAL", f"{name} does not map to an integral tick")
        result = value.numerator
    elif mode == "floor":
        result = value.numerator // value.denominator
    elif mode == "ceil":
        result = -((-value.numerator) // value.denominator)
    else:
        raise MediaAdapterError("MEDIA_TIMELINE_INVALID", f"unsupported rounding mode: {mode}")
    if result < _I64_MIN or result > _I64_MAX:
        raise MediaAdapterError("MEDIA_TIMELINE_OVERFLOW", f"{name} exceeds signed 64-bit timeline range")
    return result


def rescale_ticks(value: int, source: Rational, target: Rational, *, rounding: str = "exact", name: str = "ticks") -> int:
    """Rescale integer ticks between rational bases without floating point."""

    _require_int(value, name, minimum=_I64_MIN, maximum=_I64_MAX)
    fraction = Fraction(value * source.numerator * target.denominator, source.denominator * target.numerator)
    return _round_fraction(fraction, rounding, name)


def _optional_int(value: Any, name: str, *, minimum: int, maximum: int) -> int | None:
    if value is None or (isinstance(value, str) and value in {"", "N/A", "unknown"}):
        return None
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        parsed = int(value.strip())
    else:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be an integer")
    return _require_int(parsed, name, minimum=minimum, maximum=maximum)


def _optional_nonnegative_int(value: Any, name: str) -> int | None:
    return _optional_int(value, name, minimum=0, maximum=_U64_MAX)


def _duration_seconds_to_ticks(value: Any, time_base: Rational, name: str) -> int | None:
    """Convert ffprobe decimal seconds to a covering integer tick count.

    FFprobe normally supplies ``duration_ts``.  The decimal fallback uses
    ``Decimal``/``Fraction`` instead of binary floats and rounds upward so a
    finite media stream is never shortened by representation error.
    """

    if value is None or (isinstance(value, str) and value in {"", "N/A", "unknown"}):
        return None
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be a finite decimal")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} is not a decimal") from error
    if not decimal_value.is_finite() or decimal_value < 0:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be finite and non-negative")
    seconds = Fraction(decimal_value)
    ticks = seconds * time_base.denominator / time_base.numerator
    result = (ticks.numerator + ticks.denominator - 1) // ticks.denominator
    if result > _U64_MAX:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} exceeds the duration limit")
    return result


@dataclass(frozen=True)
class MediaStream:
    """Validated stream metadata returned by FFprobe."""

    index: int
    codec_type: str
    codec_name: str | None
    time_base: Rational | None
    start_pts: int | None
    duration_ts: int | None
    duration_ticks: int | None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    frame_rate: Rational | None = None

    def __post_init__(self) -> None:
        _require_int(self.index, "stream.index", minimum=0, maximum=_U64_MAX)
        if self.codec_type not in _STREAM_TYPES:
            raise MediaAdapterError("MEDIA_METADATA_INVALID", "stream.codec_type is unsupported")
        if self.codec_name is not None and (not isinstance(self.codec_name, str) or not self.codec_name.strip()):
            raise MediaAdapterError("MEDIA_METADATA_INVALID", "stream.codec_name must be a non-empty string")
        for name, value in (("stream.start_pts", self.start_pts), ("stream.duration_ts", self.duration_ts)):
            if value is not None:
                _require_int(
                    value,
                    name,
                    minimum=_I64_MIN if name.endswith("start_pts") else 0,
                    maximum=_I64_MAX if name.endswith("start_pts") else _U64_MAX,
                )
        if self.duration_ticks is not None:
            _require_int(self.duration_ticks, "stream.duration_ticks", minimum=0, maximum=_U64_MAX)
        for name, value in (("stream.width", self.width), ("stream.height", self.height), ("stream.sample_rate", self.sample_rate), ("stream.channels", self.channels)):
            if value is not None:
                _require_int(value, name, minimum=1, maximum=_U64_MAX)

    @property
    def is_video(self) -> bool:
        return self.codec_type == "video"

    @property
    def is_audio(self) -> bool:
        return self.codec_type == "audio"

    def timeline(
        self,
        target_time_base: Rational | None = None,
        *,
        start_rounding: str = "exact",
        duration_rounding: str = "ceil",
    ) -> "MediaTimeline":
        if self.time_base is None or self.start_pts is None or self.duration_ticks is None:
            raise MediaAdapterError("MEDIA_TIMELINE_INVALID", "stream lacks time_base, start_pts or duration")
        target = target_time_base or self.time_base
        start = rescale_ticks(self.start_pts, self.time_base, target, rounding=start_rounding, name="start_pts")
        duration = rescale_ticks(
            self.duration_ticks,
            self.time_base,
            target,
            rounding=duration_rounding,
            name="duration_ticks",
        )
        if duration < 0:
            raise MediaAdapterError("MEDIA_TIMELINE_INVALID", "duration cannot be negative")
        end = start + duration
        if end < _I64_MIN or end > _I64_MAX:
            raise MediaAdapterError("MEDIA_TIMELINE_OVERFLOW", "timeline end exceeds signed 64-bit range")
        return MediaTimeline(target, start, duration, end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "codec_type": self.codec_type,
            "codec_name": self.codec_name,
            "time_base": self.time_base.to_dict() if self.time_base else None,
            "start_pts": self.start_pts,
            "duration_ts": self.duration_ts,
            "duration_ticks": self.duration_ticks,
            "width": self.width,
            "height": self.height,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_rate": self.frame_rate.to_dict() if self.frame_rate else None,
        }


@dataclass(frozen=True)
class MediaTimeline:
    """Integer source timeline after an explicit rational-base mapping."""

    time_base: Rational
    start_pts: int
    duration_ticks: int
    end_pts: int

    def __post_init__(self) -> None:
        _require_int(self.start_pts, "timeline.start_pts", minimum=_I64_MIN, maximum=_I64_MAX)
        _require_int(self.duration_ticks, "timeline.duration_ticks", minimum=0, maximum=_I64_MAX)
        _require_int(self.end_pts, "timeline.end_pts", minimum=_I64_MIN, maximum=_I64_MAX)
        if self.end_pts != self.start_pts + self.duration_ticks:
            raise MediaAdapterError("MEDIA_TIMELINE_INVALID", "timeline end does not match start plus duration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_base": self.time_base.to_dict(),
            "start_pts": self.start_pts,
            "duration_ticks": self.duration_ticks,
            "end_pts": self.end_pts,
        }


@dataclass(frozen=True)
class MediaProbeResult:
    """Canonical source metadata and stream list from one FFprobe call."""

    source_path: Path
    format_name: str | None
    format_duration_ticks: int | None
    format_time_base: Rational
    streams: tuple[MediaStream, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise MediaAdapterError("MEDIA_METADATA_INVALID", "source_path must be absolute")
        if not self.streams:
            raise MediaAdapterError("MEDIA_METADATA_INVALID", "ffprobe returned no streams")
        if not any(stream.is_video for stream in self.streams):
            raise MediaAdapterError("VIDEO_STREAM_MISSING", "media has no video stream")
        if self.format_duration_ticks is not None:
            _require_int(self.format_duration_ticks, "format.duration_ticks", minimum=0, maximum=_U64_MAX)

    @property
    def video(self) -> MediaStream:
        return next(stream for stream in self.streams if stream.is_video)

    @property
    def audio(self) -> tuple[MediaStream, ...]:
        return tuple(stream for stream in self.streams if stream.is_audio)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    def video_timeline(
        self,
        target_time_base: Rational | None = None,
        *,
        start_rounding: str = "exact",
        duration_rounding: str = "ceil",
    ) -> MediaTimeline:
        return self.video.timeline(
            target_time_base,
            start_rounding=start_rounding,
            duration_rounding=duration_rounding,
        )

    @property
    def duration_ticks(self) -> int | None:
        video = self.video
        if video.duration_ticks is not None and video.time_base is not None:
            return rescale_ticks(
                video.duration_ticks,
                video.time_base,
                CANONICAL_TIME_BASE,
                rounding="ceil",
                name="video.duration_ticks",
            )
        if self.format_duration_ticks is not None:
            return rescale_ticks(
                self.format_duration_ticks,
                self.format_time_base,
                CANONICAL_TIME_BASE,
                rounding="ceil",
                name="format.duration_ticks",
            )
        return None

    def to_dict(self) -> dict[str, Any]:
        timeline = None
        video = self.video
        if video.time_base is not None and video.start_pts is not None and video.duration_ticks is not None:
            timeline = self.video_timeline(
                CANONICAL_TIME_BASE,
                start_rounding="floor",
                duration_rounding="ceil",
            ).to_dict()
        return {
            "source_path": os.fspath(self.source_path),
            "format_name": self.format_name,
            "format_duration_ticks": self.format_duration_ticks,
            "format_time_base": self.format_time_base.to_dict(),
            "streams": [stream.to_dict() for stream in self.streams],
            "has_audio": self.has_audio,
            "canonical_time_base": CANONICAL_TIME_BASE.to_dict(),
            "canonical_duration_ticks": self.duration_ticks,
            "video_timeline": timeline,
        }


def _metadata_object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MediaAdapterError("MEDIA_METADATA_INVALID", f"{name} must be an object")
    return value


def parse_ffprobe_json(payload: str | bytes, *, source_path: Path) -> MediaProbeResult:
    """Parse one ffprobe JSON document without trusting optional fields."""

    if not isinstance(source_path, Path) or not source_path.is_absolute():
        raise MediaAdapterError("MEDIA_METADATA_INVALID", "source_path must be absolute")
    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise MediaAdapterError("MEDIA_METADATA_INVALID", "ffprobe returned malformed JSON") from error
    root = _metadata_object(document, "ffprobe document")
    streams_value = root.get("streams")
    if not isinstance(streams_value, list):
        raise MediaAdapterError("MEDIA_METADATA_INVALID", "ffprobe streams must be an array")
    format_value = root.get("format", {})
    format_object = _metadata_object(format_value, "ffprobe format")
    # FFprobe's container object commonly omits ``time_base`` even when its
    # decimal ``duration`` is present.  Keep that fallback in the same
    # explicit millisecond base used by the worker rather than pretending a
    # missing base is one-second ticks (which would inflate 40 ms to 1000 ms
    # after rescaling).
    format_time_base_value = format_object.get("time_base")
    format_tb = (
        CANONICAL_TIME_BASE
        if format_time_base_value in (None, "N/A")
        else Rational.parse(format_time_base_value, "format.time_base")
    )
    streams: list[MediaStream] = []
    for position, item in enumerate(streams_value):
        stream = _metadata_object(item, f"ffprobe streams[{position}]")
        codec_type = stream.get("codec_type")
        if not isinstance(codec_type, str):
            raise MediaAdapterError("MEDIA_METADATA_INVALID", f"streams[{position}].codec_type is required")
        time_base_value = stream.get("time_base")
        time_base = Rational.parse(time_base_value, f"streams[{position}].time_base") if time_base_value not in (None, "N/A") else None
        duration_ts = _optional_int(stream.get("duration_ts"), f"streams[{position}].duration_ts", minimum=0, maximum=_U64_MAX)
        start_pts = _optional_int(stream.get("start_pts"), f"streams[{position}].start_pts", minimum=_I64_MIN, maximum=_I64_MAX)
        duration_ticks = duration_ts
        if duration_ticks is None and time_base is not None:
            duration_ticks = _duration_seconds_to_ticks(stream.get("duration"), time_base, f"streams[{position}].duration")
        width = _optional_nonnegative_int(stream.get("width"), f"streams[{position}].width")
        height = _optional_nonnegative_int(stream.get("height"), f"streams[{position}].height")
        sample_rate = _optional_nonnegative_int(stream.get("sample_rate"), f"streams[{position}].sample_rate")
        channels = _optional_nonnegative_int(stream.get("channels"), f"streams[{position}].channels")
        frame_rate = None
        if stream.get("r_frame_rate") not in (None, "N/A", "0/0"):
            frame_rate = Rational.parse(stream["r_frame_rate"], f"streams[{position}].r_frame_rate")
        streams.append(
            MediaStream(
                index=_require_int(stream.get("index"), f"streams[{position}].index", minimum=0, maximum=_U64_MAX),
                codec_type=codec_type,
                codec_name=stream.get("codec_name"),
                time_base=time_base,
                start_pts=start_pts,
                duration_ts=duration_ts,
                duration_ticks=duration_ticks,
                width=width,
                height=height,
                sample_rate=sample_rate,
                channels=channels,
                frame_rate=frame_rate,
            )
        )
    format_duration_ticks = _optional_nonnegative_int(format_object.get("duration_ts"), "format.duration_ts")
    if format_duration_ticks is None:
        format_duration_ticks = _duration_seconds_to_ticks(format_object.get("duration"), format_tb, "format.duration")
    format_name = format_object.get("format_name")
    if format_name is not None and (not isinstance(format_name, str) or not format_name.strip()):
        raise MediaAdapterError("MEDIA_METADATA_INVALID", "format.format_name must be a non-empty string")
    return MediaProbeResult(source_path, format_name, format_duration_ticks, format_tb, tuple(streams))


def _validate_executable(value: str | Path, name: str, *, trusted_root: str | Path | None) -> Path:
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as error:
        raise MediaAdapterError("EXECUTABLE_INVALID", f"{name} is not a valid path") from error
    if not path.is_absolute():
        raise MediaAdapterError("EXECUTABLE_NOT_APP_OWNED", f"{name} must be an absolute app-owned path")
    if trusted_root is None:
        raise MediaAdapterError("EXECUTABLE_ROOT_REQUIRED", f"{name} requires an explicit trusted runtime root")
    try:
        root_path = Path(trusted_root).expanduser()
    except (TypeError, ValueError) as error:
        raise MediaAdapterError("EXECUTABLE_ROOT_INVALID", "trusted runtime root is not a valid path") from error
    if not root_path.is_absolute():
        raise MediaAdapterError("EXECUTABLE_ROOT_INVALID", "trusted runtime root must be absolute")
    try:
        if root_path.is_symlink() or not root_path.is_dir():
            raise MediaAdapterError("EXECUTABLE_ROOT_INVALID", "trusted runtime root must be a regular directory")
        resolved_root = root_path.resolve(strict=True)
        if path.is_symlink():
            raise MediaAdapterError("EXECUTABLE_UNAVAILABLE", f"{name} must not be a symlink")
    except OSError as error:
        raise MediaAdapterError("EXECUTABLE_ROOT_INVALID", "trusted runtime root is unavailable") from error
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise MediaAdapterError("EXECUTABLE_UNAVAILABLE", f"{name} is unavailable") from error
    if not resolved.is_file():
        raise MediaAdapterError("EXECUTABLE_UNAVAILABLE", f"{name} is not a regular executable file")
    try:
        common = os.path.commonpath((os.fspath(resolved_root), os.fspath(resolved)))
    except ValueError as error:
        raise MediaAdapterError("EXECUTABLE_OUTSIDE_ROOT", f"{name} is outside the trusted runtime root") from error
    if os.path.normcase(common) != os.path.normcase(os.fspath(resolved_root)):
        raise MediaAdapterError("EXECUTABLE_OUTSIDE_ROOT", f"{name} is outside the trusted runtime root")
    return resolved


def _validate_input_file(value: str | Path, name: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise MediaAdapterError("MEDIA_INPUT_UNAVAILABLE", f"{name} is unavailable") from error
    if not path.is_file():
        raise MediaAdapterError("MEDIA_INPUT_INVALID", f"{name} must be a regular file")
    return path


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(os.fspath(left.resolve())) == os.path.normcase(os.fspath(right.resolve()))
    except OSError:
        return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


CommandRunner = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]


class MediaProbe:
    """Run the injected app-owned FFprobe and return validated metadata."""

    def __init__(
        self,
        ffprobe_path: str | Path,
        *,
        trusted_root: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        runner: CommandRunner | None = None,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.ffprobe_path = _validate_executable(ffprobe_path, "ffprobe_path", trusted_root=trusted_root)
        self.timeout_seconds = float(timeout_seconds)
        self.runner = runner

    def probe(self, source_path: str | Path) -> MediaProbeResult:
        source = _validate_input_file(source_path, "source_path")
        argv = (os.fspath(self.ffprobe_path), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", os.fspath(source))
        completed = self._run(argv)
        if completed.returncode != 0:
            raise MediaAdapterError("MEDIA_PROBE_FAILED", _diagnostic(completed.stderr), retryable=True)
        return parse_ffprobe_json(completed.stdout, source_path=source)

    def _run(self, argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        try:
            plan = build_subprocess_plan(argv[0], argv[1:])
        except SecurityBoundaryError as error:
            raise MediaAdapterError("MEDIA_COMMAND_INVALID", str(error)) from error
        try:
            if self.runner is not None:
                return self.runner(plan.argv, self.timeout_seconds)
            return subprocess.run(
                plan.argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise MediaAdapterError("MEDIA_COMMAND_TIMEOUT", f"media command exceeded {self.timeout_seconds:g}s", retryable=True) from error
        except OSError as error:
            raise MediaAdapterError("MEDIA_COMMAND_START_FAILED", _diagnostic(str(error)), retryable=True) from error


def _diagnostic(value: Any) -> str:
    return _safe_text(redact_diagnostics(str(value or "media command failed")), limit=_MAX_DIAGNOSTIC)


class FfmpegMediaAdapter:
    """Execute safe FFmpeg extract, render and audio-mux operations."""

    def __init__(
        self,
        ffmpeg_path: str | Path,
        *,
        trusted_root: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        runner: CommandRunner | None = None,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.ffmpeg_path = _validate_executable(ffmpeg_path, "ffmpeg_path", trusted_root=trusted_root)
        self.timeout_seconds = float(timeout_seconds)
        self.runner = runner

    def extract_audio(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        overwrite: bool = False,
    ) -> Path:
        source = _validate_input_file(source_path, "source_path")
        output = self._prepare_output(output_path, source, overwrite=overwrite, suffix=".wav")
        if type(sample_rate) is not int or not 8_000 <= sample_rate <= 384_000:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "sample_rate must be between 8000 and 384000")
        if type(channels) is not int or not 1 <= channels <= 8:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "channels must be between 1 and 8")
        return self._write_atomic(
            output,
            (
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", os.fspath(source),
                "-map", "0:a:0?", "-vn", "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le",
            ),
            missing_code="AUDIO_STREAM_MISSING",
            output_format="wav",
        )

    def mux_audio(
        self,
        source_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        source = _validate_input_file(source_path, "source_path")
        audio = _validate_input_file(audio_path, "audio_path")
        output = self._prepare_output(output_path, source, overwrite=overwrite)
        if _same_path(audio, output):
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path must not overwrite audio_path")
        return self._write_atomic(
            output,
            (
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", os.fspath(source), "-i", os.fspath(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart",
            ),
            output_format="mp4",
        )

    def render(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        subtitle_path: str | Path | None = None,
        audio_path: str | Path | None = None,
        video_duration: MediaTimeline | None = None,
        preserve_original_audio: bool = True,
        burn_in_subtitles: bool = False,
        overwrite: bool = False,
    ) -> Path:
        source = _validate_input_file(source_path, "source_path")
        subtitle = _validate_input_file(subtitle_path, "subtitle_path") if subtitle_path is not None else None
        audio = _validate_input_file(audio_path, "audio_path") if audio_path is not None else None
        if video_duration is not None and (not isinstance(video_duration, MediaTimeline) or video_duration.duration_ticks <= 0):
            raise MediaAdapterError("MEDIA_DURATION_INVALID", "render duration requires a positive integer source timeline")
        output = self._prepare_output(output_path, source, overwrite=overwrite)
        if audio is not None and preserve_original_audio:
            raise MediaAdapterError("AUDIO_MODE_INVALID", "dubbed audio cannot preserve the original audio")
        if audio is not None and _same_path(audio, output):
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path must not overwrite audio_path")
        if subtitle is not None and _same_path(subtitle, output):
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path must not overwrite subtitle_path")

        subtitle_staging: Path | None = None
        try:
            args: list[str] = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", os.fspath(source)]
            if audio is not None:
                args.extend(("-i", os.fspath(audio)))
            if subtitle is not None and not burn_in_subtitles:
                args.extend(("-i", os.fspath(subtitle)))
            if burn_in_subtitles and subtitle is not None:
                # FFmpeg's subtitles filter has its own escaping grammar. A
                # Windows drive letter, spaces and bracket characters can
                # otherwise be interpreted as filter options even when argv is
                # shell-safe. Copy the subtitle to a safe basename and run
                # FFmpeg from the output directory so the filter receives no
                # user-controlled path syntax at all.
                suffix = subtitle.suffix.casefold()
                if suffix not in {".srt", ".ass", ".vtt"}:
                    raise MediaAdapterError("MEDIA_SUBTITLE_INVALID", "burn-in subtitles require .srt, .ass or .vtt")
                try:
                    descriptor, staging_name = tempfile.mkstemp(
                        prefix=".dubflow-subtitle-", suffix=suffix, dir=os.fspath(output.parent)
                    )
                except OSError as error:
                    raise MediaAdapterError(
                        "MEDIA_SUBTITLE_STAGING_FAILED",
                        _diagnostic(str(error)),
                        retryable=True,
                    ) from error
                os.close(descriptor)
                subtitle_staging = Path(staging_name)
                try:
                    shutil.copyfile(subtitle, subtitle_staging)
                except OSError as error:
                    raise MediaAdapterError(
                        "MEDIA_SUBTITLE_STAGING_FAILED",
                        _diagnostic(str(error)),
                        retryable=True,
                    ) from error
                args.extend(("-vf", f"subtitles={subtitle_staging.name}"))

            args.extend(("-map", "0:v:0"))
            if audio is not None:
                args.extend(("-map", "1:a:0"))
            elif preserve_original_audio:
                args.extend(("-map", "0:a:0?"))
            else:
                args.append("-an")
            if subtitle is not None and not burn_in_subtitles:
                subtitle_input = 2 if audio is not None else 1
                args.extend(("-map", f"{subtitle_input}:0", "-c:s", "mov_text"))
            else:
                args.append("-sn")
            args.extend(("-c:v", _WINDOWS_H264_ENCODER, "-quality", "90", "-pix_fmt", "yuv420p"))
            if audio is not None or preserve_original_audio:
                args.extend(("-c:a", "aac", "-b:a", "192k"))
            if video_duration is not None:
                # A sparse subtitle may end well before the video. -shortest
                # includes that stream and would truncate the whole export.
                # Bound all streams by source-derived integer video duration;
                # rounding up to microseconds never discards a partial tick.
                numerator = video_duration.duration_ticks * video_duration.time_base.numerator * 1_000_000
                microseconds = (numerator + video_duration.time_base.denominator - 1) // video_duration.time_base.denominator
                args.extend(("-t", f"{microseconds // 1_000_000}.{microseconds % 1_000_000:06d}"))
            elif audio is not None and (subtitle is None or burn_in_subtitles):
                args.append("-shortest")
            args.extend(("-movflags", "+faststart"))
            return self._write_atomic(output, tuple(args), output_format="mp4", cwd=output.parent if subtitle_staging else None)
        finally:
            if subtitle_staging is not None:
                subtitle_staging.unlink(missing_ok=True)

    def _prepare_output(self, output_path: str | Path, source: Path, *, overwrite: bool, suffix: str = ".mp4") -> Path:
        try:
            output = Path(output_path).expanduser()
        except (TypeError, ValueError) as error:
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path is invalid") from error
        if not output.is_absolute():
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path must be absolute")
        output = output.resolve()
        if output.name in {"", ".", ".."} or output.suffix.casefold() != suffix.casefold():
            raise MediaAdapterError("MEDIA_PATH_INVALID", f"output_path must use the {suffix} suffix")
        if _same_path(output, source):
            raise MediaAdapterError("MEDIA_PATH_INVALID", "output_path must not overwrite source_path")
        if output.exists() and not overwrite:
            raise MediaAdapterError("OUTPUT_EXISTS", f"output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        return output

    def _write_atomic(
        self,
        output: Path,
        args: Sequence[str],
        *,
        missing_code: str = "MEDIA_COMMAND_FAILED",
        output_format: str,
        cwd: Path | None = None,
    ) -> Path:
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.stem}.", suffix=f"{output.suffix}.partial", dir=os.fspath(output.parent)
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            # The temporary name ends in `.partial`, so FFmpeg cannot infer a
            # muxer from the path.  Pin the format explicitly before the
            # output path instead of weakening the atomic temporary-file
            # convention or relying on a platform-specific suffix.
            argv = (os.fspath(self.ffmpeg_path), *tuple(args), "-f", output_format, os.fspath(temporary))
            try:
                plan = build_subprocess_plan(argv[0], argv[1:])
            except SecurityBoundaryError as error:
                raise MediaAdapterError("MEDIA_COMMAND_INVALID", str(error)) from error
            completed = self._run(plan.argv, cwd=cwd)
            if completed.returncode != 0:
                condition = _diagnostic(completed.stderr)
                if "matches no streams" in condition.casefold() or "stream map" in condition.casefold():
                    raise MediaAdapterError(missing_code, condition, retryable=False)
                raise MediaAdapterError("MEDIA_COMMAND_FAILED", condition, retryable=True)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise MediaAdapterError("MEDIA_OUTPUT_INVALID", "FFmpeg succeeded without producing a non-empty output")
            os.replace(temporary, output)
            temporary = None
            return output
        except MediaAdapterError:
            raise
        except OSError as error:
            raise MediaAdapterError("MEDIA_OUTPUT_FAILED", _diagnostic(str(error)), retryable=True) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _run(self, argv: tuple[str, ...], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        try:
            if self.runner is not None:
                return self.runner(argv, self.timeout_seconds)
            return subprocess.run(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                cwd=os.fspath(cwd) if cwd is not None else None,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise MediaAdapterError("MEDIA_COMMAND_TIMEOUT", f"media command exceeded {self.timeout_seconds:g}s", retryable=True) from error
        except OSError as error:
            raise MediaAdapterError("MEDIA_COMMAND_START_FAILED", _diagnostic(str(error)), retryable=True) from error


__all__ = [
    "FfmpegMediaAdapter",
    "MediaAdapterError",
    "MediaProbe",
    "MediaProbeResult",
    "MediaStream",
    "Rational",
    "CANONICAL_TIME_BASE",
    "parse_ffprobe_json",
    "rescale_ticks",
]
