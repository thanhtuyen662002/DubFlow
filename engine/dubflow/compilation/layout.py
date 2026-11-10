"""1920×1080 layouts and safe ASS editorial panels."""
from __future__ import annotations

from dataclasses import dataclass
import textwrap
from typing import Sequence

from engine.dubflow.product.layout import _text, _time
from engine.dubflow.product.script import Caption

WIDTH, HEIGHT, FPS = 1920, 1080, 30
PANEL_COLOR = "352A17"  # ASS BGR, RGB #172A35.
LAYOUTS = ("bang-thong-tin", "toan-man-hinh")
HEADING_MS = 6000  # How long a full-frame clip shows its part name.


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    x: int
    y: int
    panel_width: int


def geometry(width: int, height: int, layout: str) -> Geometry:
    if width <= 0 or height <= 0 or layout not in LAYOUTS:
        raise ValueError("invalid compilation geometry")
    scale = min(WIDTH / width, HEIGHT / height)
    w, h = max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)
    panel = layout == "bang-thong-tin" and height > width
    x = WIDTH - w - 60 if panel else (WIDTH - w) // 2
    return Geometry(w, h, x, (HEIGHT - h) // 2, x - 40 if panel else 0)


def ass_document(title: str, duration_ms: int, *, number: int = 0, panel_width: int = 0,
                 score: int | None = None, pros: Sequence[str] = (), cons: Sequence[str] = (),
                 captions: Sequence[Caption] = (), card: bool = False, show_score: bool = True,
                 summary: str = "") -> str:
    end = _time(duration_ms, ass=True)
    events: list[str] = []

    def wrap(value: str, width: int) -> str:
        return r"\N".join(_text(part) for part in textwrap.wrap(value, width=width))

    def line(text: str, x: int, y: int, size: int = 40, *, align: int = 7, start: int = 0,
             stop: int = duration_ms, layer: int = 1, color: str = "FFFFFF") -> None:
        events.append(f"Dialogue: {layer},{_time(start, ass=True)},{_time(stop, ass=True)},Text,,0,0,0,,"
                      f"{{\\an{align}\\pos({x},{y})\\fs{size}\\c&H{color}&}}{text}")

    def box(x: int, y: int, w: int, h: int, color: str, alpha: str = "00") -> None:
        events.append(f"Dialogue: 0,0:00:00.00,{end},Text,,0,0,0,,"
                      f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c&H{color}&\\1a&H{alpha}&\\p1}}"
                      f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

    heading = f"Phần {number}: {title}" if number else title
    if panel_width:
        box(0, 0, panel_width, HEIGHT, PANEL_COLOR)
        if number:
            line(_text(f"PHẦN {number}"), 60, 60, 32, color="80DCC4")
        line(wrap(title, max(22, (panel_width - 120) // 22)), 60, 120, 44)
        if score is not None:
            line(f"{score}/10", 60, 300, 52, color="80DCC4")
            box(60, 370, panel_width - 120, 14, "67594B")
            if score:
                box(60, 370, round((panel_width - 120) * score / 10), 14, "80DCC4")
        y = 435
        for symbol, values, color in (("✓", pros, "80DCC4"), ("✗", cons, "A6ABFF")):
            for value in values:
                wrapped = textwrap.wrap(f"{symbol} {value}", width=max(24, (panel_width - 120) // (16 if summary else 19)))
                line(r"\N".join(_text(part) for part in wrapped), 60, y, 28 if summary else 32, color=color)
                y += (31 if summary else 42) * len(wrapped) + (8 if summary else 12)
        if summary:
            line(wrap(summary, max(24, (panel_width - 120) // 16)), 60, 920, 28)
    elif card:
        line(wrap(heading, 58), WIDTH // 2, 90, 58, align=8)
    else:
        # The part's name sits at the top for its first seconds: dubbed clips carry their
        # subtitles at the bottom, and a bar there for the whole clip would hide them.
        shown = min(duration_ms, HEADING_MS)
        events.append(f"Dialogue: 0,0:00:00.00,{_time(shown, ass=True)},Text,,0,0,0,,"
                      f"{{\\an7\\pos(40,40)\\bord0\\shad0\\1c&H000000&\\1a&H55&\\p1}}m 0 0 l 1500 0 1500 130 0 130{{\\p0}}")
        line(wrap(heading, 68), 65, 60, 38, stop=shown)
        if show_score and score is not None:
            box(1680, 45, 190, 90, "000000", "55")
            line(f"{score}/10", 1775, 90, 48, align=5)
    cx = (panel_width + WIDTH) // 2 if panel_width else WIDTH // 2
    for caption in captions:
        line(wrap(caption.text, max(18, (WIDTH - panel_width - 100) // 26)), cx, HEIGHT // 2, 50, align=5,
             start=caption.start_ms, stop=caption.end_ms, layer=2)
    return "\n".join((
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Text,Arial,40,&H00FFFFFF,&H000000FF,&H00101820,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events,
    )) + "\n"


def program_ass(duration_ms: int, *, programme: str, title: str = "", rank: int | None = None,
                captions: Sequence[Caption] = (), heading_ms: int = 3500) -> str:
    """The overlay of one part of a raw programme (ADR-0053 §6).

    An item shows its rank badge and title at the top for its first seconds; the hook and intro show the
    programme's title; the narration's captions sit at the bottom, where raw footage rarely carries its own.
    """
    events: list[str] = []
    shown = min(duration_ms, heading_ms)

    def dialogue(start: int, stop: int, layer: int, text: str) -> None:
        events.append(f"Dialogue: {layer},{_time(start, ass=True)},{_time(stop, ass=True)},Text,,0,0,0,,{text}")

    def wrap(value: str, width: int) -> str:
        return r"\N".join(_text(part) for part in textwrap.wrap(value, width=width))

    heading = title or programme
    if heading:
        x = 60
        if rank is not None:
            dialogue(0, shown, 1, "{\\an7\\pos(40,40)\\bord0\\shad0\\1c&H80DCC4&\\p1}m 0 0 l 150 0 150 130 0 130{\\p0}")
            dialogue(0, shown, 2, f"{{\\an5\\pos(115,105)\\fs84\\b1\\c&H17202A&\\bord0}}#{rank}")
            x = 215
        dialogue(0, shown, 1, f"{{\\an7\\pos({x - 20},40)\\bord0\\shad0\\1c&H000000&\\1a&H50&\\p1}}m 0 0 l {1860 - x} 0 {1860 - x} 130 0 130{{\\p0}}")
        dialogue(0, shown, 2, f"{{\\an4\\pos({x},105)\\fs{52 if len(heading) < 40 else 40}}}{wrap(heading, 60)}")
    for caption in captions:
        dialogue(caption.start_ms, caption.end_ms, 3,
                 f"{{\\an2\\pos({WIDTH // 2},{HEIGHT - 70})\\fs52\\bord4\\shad1}}{wrap(caption.text, 52)}")
    return "\n".join((
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Text,Arial,40,&H00FFFFFF,&H000000FF,&H00101820,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events,
    )) + "\n"
