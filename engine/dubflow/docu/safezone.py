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
# How the band is read from the scan. Part of the cache identity: a new rule rescans.
BAND_RECIPE = "caption-rows-densest-v2"
# Captions sit on one or two lines at one height; the band is the densest window of line centres this tall.
ROW_WINDOW = 0.12
# A second caption line sits about one line height (0.05–0.07) from the first; text further away is not a caption.
ROW_REACH = 0.08
SAMPLE_FPS = 2.0


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


def band_from_rows(rows: Sequence[tuple[float, float, float]], speech_seconds: float, *,
                   sample_fps: float = SAMPLE_FPS) -> Band | None:
    """Where the captions usually sit, from every caption line seen during speech.

    Title cards, big numbers and lower-thirds also pass the caption test now and then. A union or a low
    percentile of them pushes the band halfway up the frame, so only the lines within ``ROW_REACH``
    of the median line of the busiest window count; the band spans their 5th to 95th percentile.
    """
    if not rows or speech_seconds <= 0:
        return None
    share = min(1.0, len({time for time, _, _ in rows}) / (speech_seconds * sample_fps))
    if share < MIN_CAPTION_SHARE:
        return None
    centres = sorted((top + bottom) / 2 for _, top, bottom in rows)
    best, low = (0, 0.0), 0
    for high, centre in enumerate(centres):
        while centre - centres[low] > ROW_WINDOW:
            low += 1
        # Ties go to the lower window: captions live at the bottom, titles above them.
        if high - low + 1 >= best[0]:
            best = (high - low + 1, centres[low])
    # Centre the window on the captions' median line so stray text at its edge drops out.
    inside = [centre for centre in centres if best[1] <= centre <= best[1] + ROW_WINDOW]
    middle = inside[len(inside) // 2]
    kept = [(top, bottom) for _, top, bottom in rows if abs((top + bottom) / 2 - middle) <= ROW_REACH]
    tops, bottoms = sorted(top for top, _ in kept), sorted(bottom for _, bottom in kept)
    top = tops[len(tops) * 5 // 100]
    bottom = bottoms[min(len(bottoms) - 1, len(bottoms) * 95 // 100)]
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
              scanner: Callable[[], Any], identity: dict[str, Any]) -> tuple[Band | None, tuple[int, int] | None]:
    """The caption band and the picture size, scanned once per source file and recipe.

    ``scanner`` builds the OCR scanner only when needed. A scan saved under an older ``BAND_RECIPE`` (the band
    read from merged blur regions) is scanned again.
    """
    identity = {**identity, "band": BAND_RECIPE}
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == identity:
            band, size = saved.get("band"), saved.get("frame_size")
            return (Band(**band) if band else None), (tuple(size) if isinstance(size, list) and len(size) == 2 else None)
    except (OSError, ValueError, TypeError):
        pass
    spans = [(round(start * 1000), round(end * 1000)) for start, end in speech]
    result = scanner().scan(video, duration_s=duration, speech_ms=spans)
    band = band_from_rows(result.caption_rows, sum(end - start for start, end in spans) / 1000)
    size = tuple(result.frame_size) if result.frame_size else None
    write_json(cache, {"schema_version": 2, "identity": identity, "band": band.__dict__ if band else None,
                       "frame_size": list(size) if size else None, "summary": result.summary()})
    return band, size


LOGO_RECIPE = "fixed-text-full-frame-v4"
# Around each sample with big text (samples are 2 s apart), how much footage to leave out.
TEXT_PAD = 1.2
LOGO_PAD = 0.012


def scan_logos(video: Path, *, duration: float, cache: Path, scanner: Callable[[], Any],
               identity: dict[str, Any]) -> list[list[float]]:
    """Channel logos and watermarks burned into a source, as padded [x, y, w, h] frame fractions; scanned once."""
    return scan_frame_text(video, duration=duration, cache=cache, scanner=scanner, identity=identity)[0]


def text_spans(times: Sequence[float], duration: float, pad: float = TEXT_PAD) -> list[list[float]]:
    """Merged [start, end] stretches around the sample times that showed big text."""
    spans: list[list[float]] = []
    for time_s in sorted(times):
        start, end = max(0.0, time_s - pad), min(duration, time_s + pad)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [[round(a, 3), round(b, 3)] for a, b in spans]


def scan_frame_text(video: Path, *, duration: float, cache: Path, scanner: Callable[[], Any],
                    identity: dict[str, Any], band: Band | None = None) -> tuple[list[list[float]], list[list[float]]]:
    """Logos (padded boxes) and the stretches showing big burned-in text, scanned once per source."""
    # Pop-up banners are found relative to the caption band, so the band is part of the identity.
    identity = {**identity, "logos": LOGO_RECIPE, "band_top": band.top if band else None}
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == identity and isinstance(saved.get("logos"), list) and isinstance(saved.get("text_spans"), list):
            return saved["logos"], saved["text_spans"]
    except (OSError, ValueError, TypeError):
        pass
    boxes, times = scanner().scan_frame_text(video, duration_s=duration, band_top=band.top if band else None)
    logos = []
    for box in boxes:
        x, y = max(0.0, box.x - LOGO_PAD), max(0.0, box.y - LOGO_PAD)
        right, bottom = min(1.0, box.x + box.w + LOGO_PAD), min(1.0, box.y + box.h + LOGO_PAD)
        logos.append([round(x, 4), round(y, 4), round(right - x, 4), round(bottom - y, 4)])
    spans = text_spans(times, duration)
    write_json(cache, {"schema_version": 2, "identity": identity, "logos": logos, "text_spans": spans})
    return logos, spans


def is_portrait(size: Sequence[int] | None) -> bool:
    """A picture taller than wide (a phone video): a 16:9 frame keeps only a strip of it."""
    return bool(size) and len(size) == 2 and size[1] > size[0]
