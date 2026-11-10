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
import threading
import wave
from typing import Any, Callable, Mapping, Sequence

from engine.dubflow.security import SecurityBoundaryError, build_subprocess_plan, redact_diagnostics
from engine.dubflow.media import encoders
from engine.dubflow import resources


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
# Media Foundation is missing on Windows "N" editions and refuses some inputs;
# the bundled Cisco OpenH264 encoder keeps the same H.264/MP4 contract then.
_FALLBACK_H264_ENCODER = "libopenh264"
# H.264 4:2:0 needs even dimensions; odd inputs (853x479) are rounded down by
# at most one pixel instead of failing the whole export.
_EVEN_DIMENSIONS = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
# A mastering filter is one of these with plain numeric options (no labels, no graph syntax, no paths).
_MASTERING_FILTER = re.compile(r"(?:(?:crop|scale|setsar|colorlevels)(?:=[0-9a-z_.:=]+)?)(?:,(?:crop|scale|setsar|colorlevels)(?:=[0-9a-z_.:=]+)?)*")
# Windows caps a whole command line at 32,767 characters. A long video can carry
# hundreds of timed blur regions; a graph longer than this is read from a file.
_INLINE_FILTER_LIMIT = 8_000
# Up to this many blur regions, per-region blurs are cheaper than one mask (measured at 480p and 1080p).
_OVERLAY_BLUR_REGIONS = 12


# A video at least this long renders as parts at once; each part is about this long.
_PARALLEL_MIN_SECONDS = 4 * 60
_PART_SECONDS = 10 * 60
_PART_SEEK_MARGIN = Fraction(30)


@dataclass(frozen=True)
class _PartPlan:
    origin: Fraction
    parts: tuple[tuple[Fraction, Fraction], ...]
    workers: int
    seconds: Fraction
    fps: Fraction | None


def _decimal(value: Fraction) -> str:
    """Seconds with microsecond precision for FFmpeg options."""
    micro = round(value * 1_000_000)
    return f"{'-' if micro < 0 else ''}{abs(micro) // 1_000_000}.{abs(micro) % 1_000_000:06d}"


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
AUDIO_DECODE_RECIPE = "source-video-origin-pcm-v1"


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


MAX_BLUR_REGIONS = 256


@dataclass(frozen=True)
class BlurRegion:
    """A frame rectangle to blur, as fractions of the displayed width/height.

    ``start_ms``/``end_ms`` bound it in time (source-relative); both unset
    blurs the whole video. Fractions keep a region valid for any resolution
    or display rotation, since FFmpeg evaluates them against the real frame.
    """

    x: float
    y: float
    w: float
    h: float
    start_ms: int | None = None
    end_ms: int | None = None

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.w, self.h)
        if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
            raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur region coordinates must be finite numbers")
        if self.x < 0 or self.y < 0 or self.w < 0.01 or self.h < 0.01 or self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur region must lie inside the frame and be at least 1% wide and tall")
        for value in (self.start_ms, self.end_ms):
            if value is not None and (type(value) is not int or not 0 <= value <= 86_400_000):
                raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur region times must be milliseconds within a day")
        if (self.start_ms is None) != (self.end_ms is None) or (self.start_ms is not None and self.start_ms >= self.end_ms):
            raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur region time range must have start before end")

    @classmethod
    def from_mapping(cls, value: Any) -> "BlurRegion":
        if not isinstance(value, Mapping) or not {"x", "y", "w", "h"} <= set(value) or not set(value) <= {"x", "y", "w", "h", "start_ms", "end_ms"}:
            raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur region needs x, y, w, h and optional start_ms/end_ms")
        return cls(value["x"], value["y"], value["w"], value["h"], value.get("start_ms"), value.get("end_ms"))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"x": self.x, "y": self.y, "w": self.w, "h": self.h}
        if self.start_ms is not None:
            value.update(start_ms=self.start_ms, end_ms=self.end_ms)
        return value


def blur_filtergraph(regions: Sequence[BlurRegion]) -> str:
    """FFmpeg filtergraph fragment that blurs each region in turn.

    Takes one unlabeled input and ends on an unlabeled overlay, so it can sit
    between ordinary comma-joined filters inside ``-vf``. Coordinates are kept
    even for 4:2:0 chroma. The shipped LGPL FFmpeg has no ``boxblur``; a
    strong Gaussian blur keeps captions unreadable at any resolution.

    Each region's blur and overlay run on every frame, also while the region
    is off. Beyond a few regions that dominates the export (a 4.6-hour video
    with 256 regions would spend about 18 hours here), so many regions instead
    blur the full-width band holding them once per frame, draw their boxes on
    one mask while they are on, and show the blur through that mask.
    """
    if not regions or len(regions) > MAX_BLUR_REGIONS:
        raise MediaAdapterError("MEDIA_BLUR_INVALID", f"between 1 and {MAX_BLUR_REGIONS} blur regions are required")
    even = lambda size, fraction: f"trunc({size}*{fraction:.6f}/2)*2"
    blur = "gblur=sigma=40:steps=3"
    enabled = lambda region: "" if region.start_ms is None else f":enable=between(t\\,{region.start_ms / 1000:.3f}\\,{region.end_ms / 1000:.3f})"
    count = len(regions)
    if count <= _OVERLAY_BLUR_REGIONS:
        parts = ["split=" + str(count + 1) + "[blur_b0]" + "".join(f"[blur_r{index}]" for index in range(count))]
        for index, region in enumerate(regions):
            parts.append(f"[blur_r{index}]crop=w={even('iw', region.w)}:h={even('ih', region.h)}:x={even('iw', region.x)}:y={even('ih', region.y)},{blur}[blur_k{index}]")
        for index, region in enumerate(regions):
            label = "" if index == count - 1 else f"[blur_b{index + 1}]"
            parts.append(f"[blur_b{index}][blur_k{index}]overlay=x={even('W', region.x)}:y={even('H', region.y)}{enabled(region)}{label}")
        return ";".join(parts)
    top = min(region.y for region in regions)
    bottom = min(1.0, max(region.y + region.h for region in regions))
    # Rounded outward, so every region's even-rounded box lies inside the band.
    band = f"crop=w=iw:h=min(ih\\,{even('ih', bottom)}+2)-{even('ih', top)}:x=0:y={even('ih', top)}"
    # drawbox reads a zero width or height as the whole frame.
    boxes = [f"drawbox=x={even('iw', region.x)}:y={even('ih', region.y)}:w=max(2\\,{even('iw', region.w)}):"
             f"h=max(2\\,{even('ih', region.h)}):color=white:t=fill{enabled(region)}" for region in regions]
    return ";".join([
        "split=3[blur_b0][blur_band][blur_canvas]",
        f"[blur_band]{band},{blur}[blur_soft]",
        f"[blur_canvas][blur_soft]overlay=x=0:y={even('H', top)},split=2[blur_full][blur_m]",
        "[blur_m]format=gray,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill," + ",".join(boxes) + "[blur_mask]",
        "[blur_full][blur_mask]alphamerge[blur_top]",
        "[blur_b0][blur_top]overlay",
    ])


COVER_MODES = ("stretch", "blur", "color", "mosaic")
COVER_DIRECTIONS = ("top", "bottom", "both")
# Distinct rectangles that get a chain of their own; more are merged into
# shared rectangles, so the per-frame cost stays bounded for any region count.
_COVER_CHAINS = 12
# Timed windows per ``enable`` expression; a group with more gets one overlay
# (or drawbox) per chunk, all fed by the same cover picture.
_ENABLE_TERMS = 48
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class CoverStyle:
    """How source captions are hidden; absent keeps the Gaussian blur.

    * ``stretch``: a ``strip_px`` row strip (on the 1080-line scale) right
      above (``top``), below (``bottom``) or on both sides of the region is
      stretched over it, so the scene seems to run on in streaks;
    * ``blur``: today's Gaussian blur;
    * ``color``: a solid ``color`` box;
    * ``mosaic``: square blocks of ``block`` px (on the 1080-line scale).

    ``feather`` (px on the 1080-line scale) fades the cover's edges into the
    picture for stretch and mosaic.
    """

    mode: str = "blur"
    direction: str = "top"
    strip_px: int = 4
    color: str = "#000000"
    block: int = 24
    feather: int = 0

    def __post_init__(self) -> None:
        def bounded(value: Any, low: int, high: int) -> bool:
            return type(value) is int and low <= value <= high

        if self.mode not in COVER_MODES or self.direction not in COVER_DIRECTIONS:
            raise MediaAdapterError("MEDIA_COVER_INVALID", "cover mode must be stretch, blur, color or mosaic; direction top, bottom or both")
        if not bounded(self.strip_px, 2, 12) or not bounded(self.block, 8, 64) or not bounded(self.feather, 0, 8):
            raise MediaAdapterError("MEDIA_COVER_INVALID", "cover strip_px must be 2-12, block 8-64 and feather 0-8")
        if not isinstance(self.color, str) or not _HEX_COLOR.match(self.color):
            raise MediaAdapterError("MEDIA_COVER_INVALID", "cover color must be #RRGGBB")

    @classmethod
    def from_mapping(cls, value: Any) -> "CoverStyle":
        known = {"mode", "direction", "strip_px", "color", "block", "feather"}
        if not isinstance(value, Mapping) or "mode" not in value or not set(value) <= known:
            raise MediaAdapterError("MEDIA_COVER_INVALID", "cover style needs a mode and only direction, strip_px, color, block and feather")
        return cls(**{key: value[key] for key in known if key in value})

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "direction": self.direction, "strip_px": self.strip_px, "color": self.color.upper(),
                "block": self.block, "feather": self.feather}


def _even(size: str, fraction: float) -> str:
    return f"trunc({size}*{fraction:.6f}/2)*2"


def _size(size: str, fraction: float) -> str:
    """An even length of at least 2 px (scale reads 0 as "keep the aspect ratio")."""
    return f"max(2\\,{_even(size, fraction)})"


def _cover_groups(regions: Sequence[BlurRegion]) -> list[list[Any]]:
    """Regions sharing one rectangle, as ``[x0, y0, x1, y1, windows]`` in first-seen order.

    ``windows`` holds sorted, coalesced ``(start_ms, end_ms)`` pairs, or is
    None when the rectangle is covered for the whole video.
    """
    groups: dict[tuple[float, float, float, float], list[Any]] = {}
    for region in regions:
        key = (region.x, region.y, region.w, region.h)
        group = groups.setdefault(key, [region.x, region.y, region.x + region.w, region.y + region.h, []])
        if region.start_ms is None:
            group[4] = None
        elif group[4] is not None:
            group[4].append((region.start_ms, region.end_ms))
    result = list(groups.values())
    for group in result:
        group[4] = _coalesce(group[4])
    return result


def _coalesce(windows: list[tuple[int, int]] | None) -> list[tuple[int, int]] | None:
    if windows is None:
        return None
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _union(a: list[Any], b: list[Any]) -> list[Any]:
    windows = None if a[4] is None or b[4] is None else _coalesce(a[4] + b[4])
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), windows]


def _merge_groups(groups: list[list[Any]], limit: int) -> list[list[Any]]:
    """At most ``limit`` rectangles: first rows of the same caption line, then nearest neighbours.

    Auto-detected captions are one band at slightly different widths; one
    rectangle covering them all keeps a single chain per line.
    """
    rows: list[list[Any]] = []
    for group in sorted(groups, key=lambda group: (group[1] + group[3], group[0])):
        height = group[3] - group[1]
        for row in rows:
            overlap = min(row[3], group[3]) - max(row[1], group[1])
            if overlap >= 0.5 * min(height, row[3] - row[1]):
                row[:] = _union(row, group)
                break
        else:
            rows.append(list(group))
    rows.sort(key=lambda row: (row[1] + row[3], row[0]))
    area = lambda group: (group[2] - group[0]) * (group[3] - group[1])
    while len(rows) > limit:
        # The neighbouring pair (top to bottom) whose shared rectangle adds the least area.
        index = min(range(len(rows) - 1), key=lambda i: area(_union(rows[i], rows[i + 1])) - area(rows[i]) - area(rows[i + 1]))
        rows[index:index + 2] = [_union(rows[index], rows[index + 1])]
    return rows


def _enables(windows: list[tuple[int, int]] | None) -> list[str]:
    """``enable`` option suffixes for a group: one per chunk of windows, or one empty for always."""
    if windows is None:
        return [""]
    terms = [f"between(t\\,{start / 1000:.3f}\\,{end / 1000:.3f})" for start, end in windows]
    return [":enable=" + "+".join(terms[index:index + _ENABLE_TERMS]) for index in range(0, len(terms), _ENABLE_TERMS)]


def _strip_crop(x: float, w: float, y0: float, y1: float, strip: float, side: str) -> str:
    """Crop of the strip just outside a rectangle's ``side``, falling back to the other side at the frame edge."""
    if side == "top" and y0 < strip and y1 + strip <= 1:
        side = "bottom"
    elif side == "bottom" and y1 + strip > 1 and y0 >= strip:
        side = "top"
    height = f"max(2\\,{_even('ih', strip)})"
    y = f"max(0\\,{_even('ih', y0)}-oh)" if side == "top" else f"min(ih-oh\\,{_even('ih', y1)})"
    return f"crop=w={_size('iw', w)}:h={height}:x={_even('iw', x)}:y={y}"


def _feathered(label: str, height: float, feather: int) -> list[str]:
    """Fade a cover picture's edges: an inset white box on black, blurred, becomes its alpha."""
    if not feather:
        return []
    # The picture is ``height`` of the frame tall, so this is ``feather`` px on the 1080-line scale.
    inset = f"trunc(ih*{feather / (1080 * height):.6f})"
    return [
        f"[{label}]format=yuva420p,split=2[{label}a][{label}m]",
        f"[{label}m]format=gray,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,"
        f"drawbox=x={inset}:y={inset}:w=max(2\\,iw-2*{inset}):h=max(2\\,ih-2*{inset}):color=white:t=fill,"
        # drawbox paints video-range grey (16-235); alpha needs the full 0-255.
        f"lut=c0=clip((val-16)*255/219\\,0\\,255),gblur=sigma={feather / 2:g}[{label}k]",
        f"[{label}a][{label}k]alphamerge[{label}f]",
    ]


def cover_filtergraph(regions: Sequence[BlurRegion], style: CoverStyle | None = None) -> str:
    """FFmpeg filtergraph fragment that hides each region in the chosen style.

    Without a style, or with ``blur``, this is exactly
    :func:`blur_filtergraph`. Like it, the fragment takes one unlabeled input
    and ends unlabeled, with even coordinates for 4:2:0 chroma.

    Regions that share a rectangle share one chain whose ``enable`` sums
    their time windows; beyond ``_COVER_CHAINS`` rectangles, rows of the same
    caption line and then the nearest neighbours are merged into shared
    rectangles. Each chain reads the picture the chains before it already
    covered, top to bottom (bottom to top for strips from below), so a
    region's strip never repeats a caption covered just above it.
    """
    if style is None or style.mode == "blur":
        return blur_filtergraph(regions)
    if not isinstance(style, CoverStyle):
        raise MediaAdapterError("MEDIA_COVER_INVALID", "cover style must be a CoverStyle")
    if not regions or len(regions) > MAX_BLUR_REGIONS:
        raise MediaAdapterError("MEDIA_BLUR_INVALID", f"between 1 and {MAX_BLUR_REGIONS} blur regions are required")
    groups = _cover_groups(regions)
    if style.mode == "color":
        # drawbox costs nothing while off, so every rectangle keeps its own box.
        color = "0x" + style.color[1:].upper()
        return ",".join(
            f"drawbox=x={_even('iw', x0)}:y={_even('ih', y0)}:w=max(2\\,{_even('iw', x1 - x0)}):h=max(2\\,{_even('ih', y1 - y0)}):"
            f"color={color}:t=fill{enable}"
            for x0, y0, x1, y1, windows in groups for enable in _enables(windows))
    if len(groups) > _COVER_CHAINS:
        groups = _merge_groups(groups, _COVER_CHAINS)
    if style.mode == "stretch" and style.direction == "bottom":
        groups.sort(key=lambda group: -group[3])
    elif style.mode == "stretch":
        groups.sort(key=lambda group: group[1])
    strip = style.strip_px / 1080
    parts: list[str] = []
    last = len(groups) - 1
    for index, (x0, y0, x1, y1, windows) in enumerate(groups):
        w, h = x1 - x0, y1 - y0
        name = f"cv{index}"
        source = "" if index == 0 else f"[{name}]"
        size = f"w={_size('rw', w)}:h={_size('rh', h)}"
        if style.mode == "mosaic":
            # Blocks across the height, the same count at any resolution; square blocks across the width.
            rows = max(1, round(h * 1080 / style.block))
            parts += [
                f"{source}split=3[{name}m][{name}s][{name}r]",
                f"[{name}s]crop=w={_size('iw', w)}:h={_size('ih', h)}:x={_even('iw', x0)}:y={_even('ih', y0)},"
                f"scale=w=max(1\\,round(iw*{rows}/ih)):h={rows}:flags=area[{name}d]",
                f"[{name}d][{name}r]scale={size}:flags=neighbor[{name}p]",
            ]
        elif style.direction == "both":
            half = h / 2
            parts += [
                f"{source}split=5[{name}m][{name}s][{name}t][{name}r][{name}q]",
                f"[{name}s]{_strip_crop(x0, w, y0, y1, strip, 'top')}[{name}c]",
                f"[{name}t]{_strip_crop(x0, w, y0, y1, strip, 'bottom')}[{name}e]",
                f"[{name}c][{name}r]scale=w={_size('rw', w)}:h={_size('rh', half)}:flags=bilinear[{name}u]",
                f"[{name}e][{name}q]scale=w={_size('rw', w)}:h=max(2\\,{_even('rh', h)}-{_even('rh', half)}):flags=bilinear[{name}l]",
                f"[{name}u][{name}l]vstack[{name}p]",
            ]
        else:
            parts += [
                f"{source}split=3[{name}m][{name}s][{name}r]",
                f"[{name}s]{_strip_crop(x0, w, y0, y1, strip, style.direction)}[{name}c]",
                f"[{name}c][{name}r]scale={size}:flags=bilinear[{name}p]",
            ]
        feather = _feathered(f"{name}p", h, style.feather)
        parts += feather
        picture = f"{name}pf" if feather else f"{name}p"
        enables = _enables(windows)
        if len(enables) > 1:
            parts.append(f"[{picture}]split={len(enables)}" + "".join(f"[{name}p{chunk}]" for chunk in range(len(enables))))
        base = f"{name}m"
        for chunk, enable in enumerate(enables):
            top = picture if len(enables) == 1 else f"{name}p{chunk}"
            final = chunk == len(enables) - 1
            label = ("" if index == last else f"[cv{index + 1}]") if final else f"[{name}m{chunk + 1}]"
            parts.append(f"[{base}][{top}]overlay=x={_even('W', x0)}:y={_even('H', y0)}{enable}{label}")
            base = f"{name}m{chunk + 1}"
    return ";".join(parts)


def video_bitrate(video: MediaStream | None) -> int:
    """Target H.264 bitrate, in bit/s, for a rendered copy of ``video``.

    Without a target Media Foundation encodes at a few hundred kbit/s (its
    ``-quality`` applies only in quality rate control), and a 576p drama came
    out heavily blocked. 0.12 bits per pixel per frame keeps fine detail; the
    bounds keep small sources sharp and 4K files a sane size.
    """
    if video is None or not video.width or not video.height:
        return 8_000_000
    rate = video.frame_rate
    fps = min(120.0, max(1.0, rate.numerator / rate.denominator)) if rate and rate.numerator > 0 else 30.0
    return round(min(16_000_000, max(1_500_000, 0.12 * video.width * video.height * fps)))


def audio_origin_samples(video: MediaStream, sample_rate: int) -> int:
    """Map the first video PTS to PCM with explicit floor rounding.

    The exact source point stays in probe/provenance. Flooring introduces less
    than one output sample of error; it also works for negative/non-integral
    origins without truncation toward zero or float timestamp identity.
    """
    if not isinstance(video, MediaStream) or not video.is_video or video.time_base is None or video.start_pts is None:
        raise MediaAdapterError("MEDIA_TIMELINE_INVALID", "source video origin requires its integer PTS and time base")
    if type(sample_rate) is not int or not 8_000 <= sample_rate <= 384_000:
        raise MediaAdapterError("AUDIO_CONFIG_INVALID", "sample_rate must be between 8000 and 384000")
    return rescale_ticks(video.start_pts, video.time_base, Rational(1, sample_rate), rounding="floor", name="video audio origin")


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
        accelerator: str = "auto",
        video_encoders: Sequence[str] | None = None,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.ffmpeg_path = _validate_executable(ffmpeg_path, "ffmpeg_path", trusted_root=trusted_root)
        self.timeout_seconds = float(timeout_seconds)
        self.runner = runner
        if accelerator not in {"auto", "cpu"}:
            raise ValueError("accelerator must be auto or cpu")
        self.accelerator = accelerator
        self._encoders: tuple[str, ...] | None = tuple(video_encoders) if video_encoders else None

    def video_encoders(self) -> tuple[str, ...]:
        """H.264 encoders in the order render tries them (NVENC only when it really works here)."""
        if self._encoders is None:
            # An injected runner is a test double: never probe a real GPU behind it.
            self._encoders = (encoders.CPU_ENCODERS if self.runner is not None
                              else encoders.h264_encoders(self.ffmpeg_path, self.accelerator))
        return self._encoders

    def extract_audio(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        video_stream: MediaStream | None = None,
        overwrite: bool = False,
    ) -> Path:
        source = _validate_input_file(source_path, "source_path")
        output = self._prepare_output(output_path, source, overwrite=overwrite, suffix=".wav")
        if type(sample_rate) is not int or not 8_000 <= sample_rate <= 384_000:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "sample_rate must be between 8000 and 384000")
        if type(channels) is not int or not 1 <= channels <= 8:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "channels must be between 1 and 8")
        args = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if video_stream is not None:
            origin = audio_origin_samples(video_stream, sample_rate)
            args.append("-copyts")
        args.extend(("-i", os.fspath(source), "-map", "0:a:0?", "-vn"))
        if video_stream is not None:
            # Preserve gaps, trim pre-video audio and compensate timestamp
            # discontinuities while streaming PCM. WAV itself starts at zero.
            args.extend(("-af", f"aresample={sample_rate}:async=1:first_pts={origin}"))
        args.extend(("-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le"))
        return self._write_atomic(
            output,
            tuple(args),
            missing_code="AUDIO_STREAM_MISSING",
            output_format="wav",
        )

    def create_silent_audio(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        video_duration: MediaTimeline,
        sample_rate: int = 48_000,
        channels: int = 2,
        overwrite: bool = False,
    ) -> Path:
        """Publish a silent PCM bed for media with no audio, never infer speech.

        Only source-derived integer duration chooses the sample count. Writes
        use at most64KiB, so a long silent video does not allocate its full WAV.
        """
        source = _validate_input_file(source_path, "source_path")
        if not isinstance(video_duration, MediaTimeline) or video_duration.duration_ticks <= 0:
            raise MediaAdapterError("MEDIA_DURATION_INVALID", "silent audio requires a positive integer source timeline")
        if type(sample_rate) is not int or not 8_000 <= sample_rate <= 384_000:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "sample_rate must be between8000 and384000")
        if type(channels) is not int or not 1 <= channels <= 8:
            raise MediaAdapterError("AUDIO_CONFIG_INVALID", "channels must be between1 and8")
        numerator = video_duration.duration_ticks * video_duration.time_base.numerator * sample_rate
        frames = (numerator + video_duration.time_base.denominator - 1) // video_duration.time_base.denominator
        frame_bytes = channels * 2
        # Python's PCM RIFF writer uses a36-byte RIFF header before the data.
        if frames * frame_bytes > (1 << 32) - 1 - 36:
            raise MediaAdapterError("AUDIO_SIZE_UNSUPPORTED", "silent PCM exceeds the WAV RIFF size limit")
        output = self._prepare_output(output_path, source, overwrite=overwrite, suffix=".wav")
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".wav.partial", dir=output.parent)
            temporary = Path(name)
            chunk_frames = 65_536 // frame_bytes
            zeros = bytes(chunk_frames * frame_bytes)
            with os.fdopen(descriptor, "w+b") as handle:
                with wave.open(handle, "wb") as writer:
                    writer.setnchannels(channels)
                    writer.setsampwidth(2)
                    writer.setframerate(sample_rate)
                    remaining = frames
                    while remaining:
                        count = min(remaining, chunk_frames)
                        writer.writeframesraw(zeros[:count * frame_bytes])
                        remaining -= count
                handle.flush()
                os.fsync(handle.fileno())
            with wave.open(str(temporary), "rb") as reader:
                actual = (reader.getnframes(), reader.getframerate(), reader.getnchannels(), reader.getsampwidth(), reader.getcomptype())
            if actual != (frames, sample_rate, channels, 2, "NONE") or temporary.stat().st_size != 44 + frames * frame_bytes:
                raise MediaAdapterError("SOURCE_AUDIO_INVALID", "silent PCM duration or format failed validation")
            os.replace(temporary, output)
            return output
        except (OSError, EOFError, wave.Error) as error:
            raise MediaAdapterError("AUDIO_WRITE_FAILED", "silent PCM could not be written or validated", retryable=True) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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
        source_probe: MediaProbeResult | None = None,
        preserve_original_audio: bool = True,
        burn_in_subtitles: bool = False,
        blur_regions: Sequence[BlurRegion] = (),
        cover_style: CoverStyle | None = None,
        mastering_filters: Sequence[str] = (),
        overwrite: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> Path:
        """Render H.264/AAC; ``progress`` receives the encoded position in microseconds.

        ``mastering_filters`` (ADR-0055) are measured framing/colour filters from the dubbing mastering plan; they
        run after the source-caption cover and before the burned subtitles, so captions are never graded. Empty
        (the default) renders exactly as before.
        """
        for item in mastering_filters:
            if not isinstance(item, str) or not _MASTERING_FILTER.fullmatch(item):
                raise MediaAdapterError("MEDIA_MASTERING_INVALID", "mastering filters must be crop/scale/setsar/colorlevels")
        source = _validate_input_file(source_path, "source_path")
        if not all(isinstance(region, BlurRegion) for region in blur_regions):
            raise MediaAdapterError("MEDIA_BLUR_INVALID", "blur_regions must contain BlurRegion values")
        if cover_style is not None and not isinstance(cover_style, CoverStyle):
            raise MediaAdapterError("MEDIA_COVER_INVALID", "cover_style must be a CoverStyle")
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
        filter_staging: Path | None = None
        try:
            args: list[str] = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            video_filters: list[str] = []
            if source_probe is not None:
                if not isinstance(source_probe, MediaProbeResult) or source_probe.source_path.resolve() != source:
                    raise MediaAdapterError("MEDIA_TIMELINE_INVALID", "render probe must describe the actual source")
                audio_origin_samples(source_probe.video, 48_000)  # Require known source origin even for silent video.
                args.append("-copyts")
                video_filters.append("setpts=PTS-STARTPTS")
            decode_at = len(args)
            args.extend(("-i", os.fspath(source)))
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
                video_filters.append(f"subtitles={subtitle_staging.name}")
            if blur_regions:
                # Cover the source captions before drawing the Vietnamese ones on top.
                position = 1 if source_probe is not None else 0
                video_filters.insert(position, cover_filtergraph(blur_regions, cover_style))
            if mastering_filters:
                position = (1 if source_probe is not None else 0) + (1 if blur_regions else 0)
                video_filters[position:position] = list(mastering_filters)

            video_filters.append(_EVEN_DIMENSIONS)
            graph = ",".join(video_filters)
            filter_at = len(args)
            if len(graph) <= _INLINE_FILTER_LIMIT:
                args.extend(("-vf", graph))
            else:
                # FFmpeg reads an option's value from a file when its name starts with "/".
                try:
                    descriptor, staging_name = tempfile.mkstemp(
                        prefix=".dubflow-filter-", suffix=".txt", dir=os.fspath(output.parent)
                    )
                    filter_staging = Path(staging_name)
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                        stream.write(graph)
                except OSError as error:
                    raise MediaAdapterError(
                        "MEDIA_FILTER_STAGING_FAILED",
                        _diagnostic(str(error)),
                        retryable=True,
                    ) from error
                args.extend(("-/vf", os.fspath(filter_staging)))
            if source_probe is not None and audio is None and preserve_original_audio and source_probe.has_audio:
                rate = source_probe.audio[0].sample_rate
                origin = audio_origin_samples(source_probe.video, rate)
                args.extend(("-af", f"aresample={rate}:async=1:first_pts={origin},asetpts=PTS-STARTPTS"))

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
            encoder_at = len(args)
            bitrate = video_bitrate(source_probe.video if source_probe is not None else None)
            args.extend(("-pix_fmt", "yuv420p"))
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
            cwd = output.parent if subtitle_staging else None
            plan = self._chunk_plan(source_probe, video_duration)
            if plan is not None:
                # A long video renders as several parts at once: one FFmpeg runs
                # its filters (blur, subtitles) on about one CPU core, so a
                # single process leaves a fast GPU and most cores waiting.
                try:
                    return self._render_parts(output, args, filter_at, encoder_at, graph, plan, bitrate, cwd, progress)
                except MediaAdapterError as error:
                    if error.code == "MEDIA_COMMAND_TIMEOUT":
                        raise
                    # Any part that will not render: the proven single pass below.
            first: MediaAdapterError | None = None
            # NVENC (GPU decode + encode) when it works, then Media Foundation,
            # then OpenH264: a failed encoder never fails the export by itself.
            for encoder in self.video_encoders():
                argv = [*args[:decode_at], *encoders.decode_args(encoder), *args[decode_at:encoder_at],
                        *encoders.rate_args(encoder, bitrate), *resources.ffmpeg_thread_args(), *args[encoder_at:]]
                try:
                    return self._write_atomic(output, tuple(argv), output_format="mp4", cwd=cwd, progress=progress)
                except MediaAdapterError as error:
                    if error.code != "MEDIA_COMMAND_FAILED":
                        raise
                    first = first or error
            assert first is not None
            raise first
        finally:
            if subtitle_staging is not None:
                subtitle_staging.unlink(missing_ok=True)
            if filter_staging is not None:
                filter_staging.unlink(missing_ok=True)

    def _chunk_plan(self, probe: MediaProbeResult | None, duration: MediaTimeline | None) -> "_PartPlan | None":
        """Parts and how many render at once, or None for one FFmpeg (short video, unknown origin, one core)."""
        if probe is None or duration is None or probe.video.time_base is None or probe.video.start_pts is None:
            return None
        seconds = Fraction(duration.duration_ticks * duration.time_base.numerator, duration.time_base.denominator)
        if seconds < _PARALLEL_MIN_SECONDS:
            return None
        chain = self.video_encoders()
        workers = resources.parallel_renders(gpu=chain[:1] == (encoders.NVENC,))
        if workers < 2:
            return None
        count = max(workers, math.ceil(seconds / _PART_SECONDS))
        origin = Fraction(probe.video.start_pts * probe.video.time_base.numerator, probe.video.time_base.denominator)
        bounds = [seconds * index / count for index in range(count + 1)]
        rate = probe.video.frame_rate
        fps = Fraction(rate.numerator, rate.denominator) if rate is not None else None
        return _PartPlan(origin, tuple(zip(bounds[:-1], bounds[1:])), workers, seconds, fps)

    def _render_parts(self, output: Path, args: list[str], filter_at: int, encoder_at: int, graph: str, plan: "_PartPlan",
                      bitrate: int, cwd: Path | None, progress: Callable[[int], None] | None) -> Path:
        """Render ``plan``'s parts in parallel with one encoder, then join them and add the sound in one copy pass."""
        source = args[args.index("-i") + 1]
        parts_dir = Path(tempfile.mkdtemp(prefix=f".{output.stem}.parts-", dir=os.fspath(output.parent)))
        positions = [0] * len(plan.parts)
        counts = [0] * len(plan.parts)
        lock = threading.Lock()
        per_part = resources.part_thread_args(plan.workers)
        try:
            # Absolute times stay as in the single pass (blur and subtitle times
            # match); each part then starts at zero for the join.
            # Each part is cut exactly by its own times ("trim"); FFmpeg seeks
            # well before it, since a seek near a late key frame can land past it.
            body = graph.split(",", 1)[1]
            def filters(index: int) -> list[str]:
                start, end = plan.parts[index]
                part_graph = (f"trim=start={_decimal(plan.origin + start)}:end={_decimal(plan.origin + end)},"
                              f"setpts=PTS-{_decimal(plan.origin)}/TB,{body},setpts=PTS-STARTPTS")
                if len(part_graph) <= _INLINE_FILTER_LIMIT:
                    return ["-vf", part_graph]
                graph_file = parts_dir / f"graph-{index:04d}.txt"
                graph_file.write_text(part_graph, encoding="utf-8")
                return ["-/vf", os.fspath(graph_file)]
            last: MediaAdapterError | None = None
            for encoder in self.video_encoders():
                def part(index: int, encoder: str = encoder) -> Path:
                    start, end = plan.parts[index]
                    target = parts_dir / f"part-{index:04d}.mp4"
                    def moved(count: int) -> None:
                        # Frames, not FFmpeg's clock: with -copyts its out_time can read 0.
                        with lock:
                            counts[index] = count
                            positions[index] = round(count * 1_000_000 / plan.fps) if plan.fps else 0
                            if progress is not None:
                                progress(sum(positions))
                    # The trim's end stops the read; an input "-t" counts from a different clock under -copyts.
                    seek = max(Fraction(0), start - _PART_SEEK_MARGIN)
                    argv = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-copyts", *encoders.decode_args(encoder),
                            "-ss", _decimal(seek), "-i", source,
                            *filters(index), "-map", "0:v:0", "-an", "-sn", "-dn",
                            *encoders.rate_args(encoder, bitrate), *per_part, "-pix_fmt", "yuv420p"]
                    made = self._write_atomic(target, tuple(argv), output_format="mp4", cwd=cwd, frames=moved if self.runner is None else None)
                    # A part that came out short (a seek past its frames) must not
                    # shift the picture against the sound: use the single pass.
                    # Half the nominal frames: a variable frame rate can run well below r_frame_rate.
                    if self.runner is None and plan.fps and counts[index] < (end - start) * plan.fps / 2:
                        raise MediaAdapterError("MEDIA_PART_SHORT", f"part {index} rendered {counts[index]} of about {float((end - start) * plan.fps):.0f} frames")
                    return made
                try:
                    made = self._run_parts(part, len(plan.parts), plan.workers)
                    break
                except MediaAdapterError as error:
                    if error.code != "MEDIA_COMMAND_FAILED":
                        raise
                    last = last or error
                    positions[:] = [0] * len(positions)
            else:
                assert last is not None
                raise last
            listing = parts_dir / "parts.txt"
            listing.write_text("".join(f"file '{path.name}'\n" for path in made), encoding="utf-8")
            # The single pass's own inputs, maps and sound; the picture is the joined parts, copied.
            final = [*args[:filter_at], "-f", "concat", "-safe", "0", "-i", os.fspath(listing)]
            inputs = sum(1 for item in args[:filter_at] if item == "-i")
            rest = args[filter_at + 2:]
            rest[rest.index("0:v:0")] = f"{inputs}:v:0"
            final += [*rest[:encoder_at - filter_at - 2], "-c:v", "copy", *rest[encoder_at - filter_at - 2:]]
            final.remove("-pix_fmt")
            final.remove("yuv420p")
            return self._write_atomic(output, tuple(final), output_format="mp4", cwd=cwd)
        finally:
            shutil.rmtree(parts_dir, ignore_errors=True)

    def _run_parts(self, part: Callable[[int], Path], count: int, workers: int) -> list[Path]:
        """Run every part, ``workers`` at a time; a failed part gets one more try alone (an NVENC session may have been busy)."""
        from concurrent.futures import ThreadPoolExecutor

        results: list[Path | MediaAdapterError | None] = [None] * count
        def attempt(index: int) -> None:
            try:
                results[index] = part(index)
            except MediaAdapterError as error:
                results[index] = error
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dubflow-part") as pool:
            list(pool.map(attempt, range(count)))
        for index, result in enumerate(results):
            if isinstance(result, MediaAdapterError):
                if result.code != "MEDIA_COMMAND_FAILED":
                    raise result
                attempt(index)
                if isinstance(results[index], MediaAdapterError):
                    raise results[index]  # type: ignore[misc]
        return [result for result in results if isinstance(result, Path)]

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
        progress: Callable[[int], None] | None = None,
        frames: Callable[[int], None] | None = None,
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
            completed = (self._run_reporting(plan.argv, cwd=cwd, progress=progress, frames=frames)
                         if (progress is not None or frames is not None) and self.runner is None else self._run(plan.argv, cwd=cwd))
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

    def _run_reporting(self, argv: tuple[str, ...], *, cwd: Path | None, progress: Callable[[int], None] | None,
                       frames: Callable[[int], None] | None = None) -> subprocess.CompletedProcess[str]:
        """Like ``_run`` and reports each encoded microsecond, so a 20-hour render shows it is moving."""
        command = (argv[0], "-progress", "pipe:1", "-nostats", *argv[1:])
        try:
            child = subprocess.Popen(command, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", cwd=os.fspath(cwd) if cwd is not None else None,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            raise MediaAdapterError("MEDIA_COMMAND_START_FAILED", _diagnostic(str(error)), retryable=True) from error
        errors: list[str] = []
        reader = threading.Thread(target=lambda: errors.append(child.stderr.read() if child.stderr else ""), daemon=True)
        reader.start()
        expired = threading.Event()
        def expire() -> None:
            expired.set()
            child.kill()
        watchdog = threading.Timer(self.timeout_seconds, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            for line in child.stdout or ():
                key, _, value = line.strip().partition("=")
                observer = progress if key == "out_time_us" else frames if key == "frame" else None
                if observer is not None and value.isdigit():
                    try:
                        observer(int(value))
                    except Exception:  # An observer never fails the render.
                        pass
            child.wait()
        finally:
            watchdog.cancel()
            if child.poll() is None:
                child.kill()
                child.wait()
            reader.join(timeout=5)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        if expired.is_set():
            raise MediaAdapterError("MEDIA_COMMAND_TIMEOUT", f"media command exceeded {self.timeout_seconds:g}s", retryable=True)
        return subprocess.CompletedProcess(command, child.returncode, "", "".join(errors))

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
    "BlurRegion",
    "COVER_DIRECTIONS",
    "COVER_MODES",
    "CoverStyle",
    "FfmpegMediaAdapter",
    "MediaAdapterError",
    "MediaProbe",
    "MediaProbeResult",
    "MediaStream",
    "Rational",
    "CANONICAL_TIME_BASE",
    "blur_filtergraph",
    "cover_filtergraph",
    "parse_ffprobe_json",
    "rescale_ticks",
]
