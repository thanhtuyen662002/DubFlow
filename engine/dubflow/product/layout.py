"""Product-video templates.

"ban-tin" (Bản tin): media on top, a green panel with name, captions and price.
"toan-man-hinh" (Toàn màn hình): media over the whole frame; name, captions and
price on translucent boxes over it.
"""
from __future__ import annotations

from typing import Sequence

from .script import Caption

WIDTH, HEIGHT = 1080, 1920
MEDIA_HEIGHT = 1200
FPS = 30
PANEL_COLOR = "0x0E7A3B"
TEMPLATES = ("ban-tin", "toan-man-hinh")

# ASS colours are &HAABBGGRR.
_STYLES = (
    "Style: Title,Arial,60,&H0000D4FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,2,8,60,60,0,1",
    "Style: Caption,Arial,66,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,0,3,5,60,60,0,1",
    "Style: Price,Arial,62,&H00FFFFFF,&H000000FF,&H001C1CD8,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,2,60,60,0,1",
    "Style: Strip,Arial,10,&H0000D4FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
)
# On the user's own background, a dark outline keeps the name and captions readable.
_OUTLINED = {
    "Title": _STYLES[0].replace(",1,0,2,8,", ",1,3,2,8,"),
    "Caption": _STYLES[1].replace(",1,0,3,5,", ",1,4,2,5,"),
}


# Full screen: text on boxes (border style 3) of translucent black, the price on red.
_FULL_STYLES = (
    "Style: Title,Arial,56,&H0000D4FF,&H000000FF,&H60000000,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,8,60,60,0,1",
    "Style: Caption,Arial,64,&H00FFFFFF,&H000000FF,&H60000000,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,2,60,60,0,1",
    _STYLES[2],
)


def media_height(template: str) -> int:
    """How much of the frame, from the top, the photos and clips fill."""
    return HEIGHT if template == "toan-man-hinh" else MEDIA_HEIGHT


def _time(milliseconds: int, *, ass: bool) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _text(value: str) -> str:
    """User text as plain ASS text: no override blocks or escapes."""
    return " ".join(value.replace("\\", "/").replace("{", "(").replace("}", ")").split())


def ass_document(name: str, price: str, captions: Sequence[Caption], duration_ms: int, *, outlined: bool = False,
                 template: str = "ban-tin") -> str:
    whole = f"0:00:00.00,{_time(duration_ms, ass=True)}"
    full = template == "toan-man-hinh"
    if full:
        styles = _FULL_STYLES
        events = []
        title_at, caption_at = f"\\an8\\pos({WIDTH // 2},90)", f"\\an2\\pos({WIDTH // 2},{HEIGHT - 260})"
    else:
        styles = tuple(_OUTLINED.get(style.split(",", 1)[0][len("Style: "):], style) for style in _STYLES) if outlined else _STYLES
        events = [f"Dialogue: 0,{whole},Strip,,0,0,0,,{{\\an7\\pos(0,{MEDIA_HEIGHT})\\p1}}m 0 0 l {WIDTH} 0 {WIDTH} 10 0 10{{\\p0}}"]
        title_at, caption_at = f"\\an8\\pos({WIDTH // 2},{MEDIA_HEIGHT + 44})", f"\\an5\\pos({WIDTH // 2},{(MEDIA_HEIGHT + HEIGHT) // 2})"
    if name.strip():
        events.append(f"Dialogue: 0,{whole},Title,,0,0,0,,{{{title_at}}}{_text(name)}")
    if price.strip():
        events.append(f"Dialogue: 0,{whole},Price,,0,0,0,,{{\\an2\\pos({WIDTH // 2},{HEIGHT - 70})}}{_text(price)}")
    events.extend(
        f"Dialogue: 1,{_time(caption.start_ms, ass=True)},{_time(caption.end_ms, ass=True)},Caption,,0,0,0,,{{{caption_at}}}{_text(caption.text)}"
        for caption in captions)
    return "\n".join((
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    )) + "\n"


def srt_document(captions: Sequence[Caption]) -> str:
    return "\n\n".join(
        f"{index}\n{_time(caption.start_ms, ass=False)} --> {_time(caption.end_ms, ass=False)}\n{caption.text}"
        for index, caption in enumerate(captions, start=1)) + "\n"
