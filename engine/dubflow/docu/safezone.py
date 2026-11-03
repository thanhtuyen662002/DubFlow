"""Framing that keeps burned-in captions out of the picture (ADR-0049, phase 2b).

The caption scan of the dubbing pipeline (PP-OCRv4, lower band, captions only:
logos and channel names are "fixed text" it keeps) gives the band where a
source's captions sit. Framing then zooms at most 114% and shifts the picture
up so the band leaves the frame; the project's look (letterbox or overscan)
applies to every source the same way.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .project import write_json

MAX_ZOOM = 1.14
OVERSCAN = 1.08
# 2.35:1 inside 16:9: each bar is this share of the height.
LETTERBOX_BAR = (1 - (16 / 9) / 2.35) / 2
# A source counts as captioned when captions are on screen this share of its speech.
MIN_CAPTION_SHARE = 0.25
MARGIN = 0.01


@dataclass(frozen=True)
class Band:
    top: float
    bottom: float
    share: float


@dataclass(frozen=True)
class Framing:
    """Show ``1/zoom`` of the source about its centre, with that window moved ``shift`` (a share of the source
    height) toward the top; bars cover ``letterbox`` of the output height at top and bottom. ``clear`` is
    false when the 114% limit still leaves part of the caption band visible."""

    zoom: float
    shift: float
    letterbox: float
    clear: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"zoom": round(self.zoom, 4), "shift": round(self.shift, 4), "letterbox": round(self.letterbox, 4), "clear": self.clear}


def caption_band(regions: Sequence[Any], speech_seconds: float) -> Band | None:
    """The vertical span the captions use, when they are on screen often enough to matter."""
    if not regions or speech_seconds <= 0:
        return None
    shown = sum(max(0, (r.end_ms or 0) - (r.start_ms or 0)) for r in regions) / 1000
    share = min(1.0, shown / speech_seconds)
    if share < MIN_CAPTION_SHARE:
        return None
    tops = sorted(r.y for r in regions)
    bottoms = sorted(r.y + r.h for r in regions)
    # The 10th/90th percentile ignore a stray high or low detection.
    top = tops[len(tops) // 10]
    bottom = bottoms[min(len(bottoms) - 1, len(bottoms) * 9 // 10)]
    return Band(round(top, 4), round(bottom, 4), round(share, 3))


def framing(band: Band | None, preset: str) -> Framing:
    """The smallest zoom (and upward shift) that hides the band, within the 114% limit and the preset."""
    letterbox = LETTERBOX_BAR if preset == "letterbox_235" else 0.0
    base = OVERSCAN if preset == "overscan_108" else 1.0
    if band is None:
        return Framing(base, 0.0, letterbox)
    # Visible picture after zoom z about the centre and shift s up: [0.5 - 0.5/z + s, 0.5 + 0.5/z + s] of the source,
    # with the letterbox bar covering its outer `letterbox` share. The caption top must be at or below the visible bottom.
    limit = band.top - MARGIN
    for step in range(0, 15):
        zoom = max(base, 1.0 + step * 0.01)
        if zoom > MAX_ZOOM:
            break
        visible = 1 / zoom
        bottom_hidden = visible * letterbox
        # Shift needed so the visible bottom (minus the bar) sits at the caption top; no black edge at the top.
        shift = max(0.0, (0.5 + visible / 2 - bottom_hidden) - limit)
        if shift <= (1 - visible) / 2:
            return Framing(zoom, shift, letterbox)
    zoom = max(base, MAX_ZOOM)
    return Framing(zoom, (1 - 1 / zoom) / 2, letterbox, clear=False)


def scan_band(video: Path, *, duration: float, speech: Iterable[tuple[float, float]], cache: Path,
              scanner: Callable[[], Any], identity: dict[str, Any]) -> Band | None:
    """Scan once per source file and recipe; ``scanner`` builds the OCR scanner only when needed."""
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == identity:
            band = saved.get("band")
            return Band(**band) if band else None
    except (OSError, ValueError, TypeError):
        pass
    spans = [(round(start * 1000), round(end * 1000)) for start, end in speech]
    result = scanner().scan(video, duration_s=duration, speech_ms=spans)
    band = caption_band(result.regions, sum(end - start for start, end in spans) / 1000)
    write_json(cache, {"schema_version": 1, "identity": identity, "band": band.__dict__ if band else None,
                       "summary": result.summary()})
    return band
