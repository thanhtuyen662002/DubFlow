"""App-owned media process adapters.

The production worker uses this package as the only boundary to FFmpeg and
FFprobe.  Executable paths are injected by the runtime/model manager and are
required to be absolute regular files; no command is resolved from ``PATH``.
"""

from .adapter import (
    CANONICAL_TIME_BASE,
    FfmpegMediaAdapter,
    MediaAdapterError,
    MediaProbe,
    MediaProbeResult,
    MediaStream,
    MediaTimeline,
    Rational,
    parse_ffprobe_json,
    rescale_ticks,
)

__all__ = [
    "FfmpegMediaAdapter",
    "CANONICAL_TIME_BASE",
    "MediaAdapterError",
    "MediaProbe",
    "MediaProbeResult",
    "MediaStream",
    "MediaTimeline",
    "Rational",
    "parse_ffprobe_json",
    "rescale_ticks",
]
