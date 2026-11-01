"""Product-video templates.

"ban-tin" (Bản tin): media on top, a green panel with name, captions and price.
"toan-man-hinh" (Toàn màn hình): media over the whole frame; name, captions and
price on translucent boxes over it.
"chi-chu" (Chỉ chữ): no media; the name, one animated card per highlight, the
price and the captions over the user's background or a plain gradient.

The desktop's live preview draws the same layout: ``preview_layout()`` is the
source of ``apps/desktop/src/features/product/layout.json`` (print it with
``python -m engine.dubflow.product.layout``), and a test keeps the two equal.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Sequence

from .script import Caption

WIDTH, HEIGHT = 1080, 1920
MEDIA_HEIGHT = 1200
FPS = 30
FONT = "Arial"
PANEL_COLOR = "0x0E7A3B"
STRIP_HEIGHT = 10
TEXT_ONLY = "chi-chu"
TEMPLATES = ("ban-tin", "toan-man-hinh", TEXT_ONLY)
# Taller than this (height / width) fills the whole frame; anything wider fills the panel.
FULL_FRAME_RATIO = 1.2
# Chỉ chữ without a background: top and bottom of a vertical gradient.
GRADIENT = (0x14213D, 0x0E7A3B)
# Chỉ chữ over the user's background: black at this ASS alpha (0 opaque, 255 clear) keeps the text readable.
SCRIM_ALPHA = 0xA0
# Chỉ chữ: the highlight card (left, top, width, height), its corner radius and fill alpha.
CARD = (90, 560, 900, 560)
CARD_RADIUS = 36
CARD_ALPHA = 0x50
# Chỉ chữ: the gold bar inside the card's left edge (left, top, width, height), relative to the card.
CARD_BAR = (36, 64, 12, 432)
# Chỉ chữ: the card's "1/3" counter, relative to the card.
CARD_COUNTER = (72, 52)
# Chỉ chữ: each highlight card shows at least this long; extra highlights are left out.
MIN_CARD_MS = 1800
# Chỉ chữ: cards slide in from this far right over SLIDE_MS and fade in and out.
SLIDE_PX = 60
SLIDE_MS = 300
FADE_IN_MS, FADE_OUT_MS = 250, 200

_GOLD, _WHITE, _BLACK, _RED = 0xFFD400, 0xFFFFFF, 0x000000, 0xD81C1C


def _hex(rgb: int) -> str:
    return f"#{rgb:06X}"


def _ass_colour(rgb: int, alpha: int = 0) -> str:
    """ASS colours are &HAABBGGRR."""
    red, green, blue = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}"


@dataclass(frozen=True)
class Style:
    """One ASS style; ``box`` draws an opaque box (border style 3) in the outline colour."""

    name: str
    size: int
    colour: int
    outline_colour: int = _BLACK
    outline_alpha: int = 0
    back_alpha: int = 0
    bold: bool = True
    box: bool = False
    outline: int = 0
    shadow: int = 0
    alignment: int = 5
    margin: int = 60

    def line(self) -> str:
        return (f"Style: {self.name},{FONT},{self.size},{_ass_colour(self.colour)},&H000000FF,"
                f"{_ass_colour(self.outline_colour, self.outline_alpha)},{_ass_colour(_BLACK, self.back_alpha)},"
                f"{-1 if self.bold else 0},0,0,0,100,100,0,0,{3 if self.box else 1},{self.outline},{self.shadow},"
                f"{self.alignment},{self.margin},{self.margin},0,1")

    def preview(self) -> dict[str, Any]:
        """What the desktop needs to draw this text like libass does."""
        return {
            "size": self.size, "colour": _hex(self.colour), "bold": self.bold, "align": self.alignment,
            "wrap": WIDTH - 2 * self.margin,
            "box": {"colour": _hex(self.outline_colour), "opacity": round(1 - self.outline_alpha / 255, 3), "pad": self.outline} if self.box else None,
            "outline": 0 if self.box else self.outline,
            "shadow": {"offset": self.shadow, "opacity": round(1 - self.back_alpha / 255, 3)} if self.shadow else None,
        }


@dataclass(frozen=True)
class Place:
    style: Style
    x: int
    y: int

    def tag(self) -> str:
        return f"\\an{self.style.alignment}\\pos({self.x},{self.y})"

    def preview(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, **self.style.preview()}


_TITLE = Style("Title", 60, _GOLD, shadow=2, alignment=8)
_CAPTION = Style("Caption", 66, _WHITE, back_alpha=0x64, shadow=3, alignment=5)
_PRICE = Style("Price", 62, _WHITE, outline_colour=_RED, box=True, outline=16, alignment=2)
_STRIP = Style("Strip", 10, _GOLD, bold=False, alignment=7, margin=0)

# Bản tin: the name under the gold strip, captions in the middle of the panel, the price at the bottom.
BAN_TIN = {
    "title": Place(_TITLE, WIDTH // 2, MEDIA_HEIGHT + 44),
    "caption": Place(_CAPTION, WIDTH // 2, (MEDIA_HEIGHT + HEIGHT) // 2),
    "price": Place(_PRICE, WIDTH // 2, HEIGHT - 70),
}
# On the user's own background, a dark outline keeps the name and captions readable.
BAN_TIN_OUTLINED = {
    "title": replace(BAN_TIN["title"], style=replace(_TITLE, outline=3)),
    "caption": replace(BAN_TIN["caption"], style=replace(_CAPTION, outline=4, shadow=2)),
}
# Full screen: text on boxes of translucent black, the price on red.
TOAN_MAN_HINH = {
    "title": Place(Style("Title", 56, _GOLD, outline_alpha=0x60, box=True, outline=14, alignment=8), WIDTH // 2, 90),
    "caption": Place(Style("Caption", 64, _WHITE, outline_alpha=0x60, box=True, outline=14, alignment=2), WIDTH // 2, HEIGHT - 260),
    "price": BAN_TIN["price"],
}
# Chỉ chữ: the name on top, the highlight card in the upper middle, the price under it, captions low.
CHI_CHU = {
    "title": Place(Style("Title", 64, _GOLD, outline=3, shadow=2, alignment=8), WIDTH // 2, 230),
    "card": Place(Style("Card", 76, _WHITE, alignment=5, margin=150), WIDTH // 2, CARD[1] + CARD[3] // 2),
    "counter": Place(Style("Counter", 36, _GOLD, alignment=7), CARD[0] + CARD_COUNTER[0], CARD[1] + CARD_COUNTER[1]),
    "price": Place(Style("Price", 72, _WHITE, outline_colour=_RED, box=True, outline=18, alignment=5), WIDTH // 2, 1300),
    "caption": Place(Style("Caption", 60, _WHITE, back_alpha=0x64, outline=4, shadow=2, alignment=5), WIDTH // 2, 1580),
}
_SHAPE = Style("Shape", 10, _BLACK, bold=False, alignment=7, margin=0)


def media_height(template: str) -> int:
    """How much of the frame, from the top, the photos and clips fill."""
    return {"toan-man-hinh": HEIGHT, TEXT_ONLY: 0}.get(template, MEDIA_HEIGHT)


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


def _rounded(width: int, height: int, radius: int) -> str:
    """An ASS drawing of a rounded rectangle from (0, 0)."""
    r, w, h = radius, width, height
    return (f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
            f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} l 0 {r} b 0 0 0 0 {r} 0")


def card_times(count: int, duration_ms: int) -> list[tuple[int, int]]:
    """Equal slots for ``count`` highlight cards, each at least MIN_CARD_MS; fewer when the video is short."""
    shown = min(count, max(1, duration_ms // MIN_CARD_MS)) if count and duration_ms > 0 else 0
    return [(duration_ms * index // shown, duration_ms * (index + 1) // shown) for index in range(shown)]


def _text_only_events(name: str, price: str, features: Sequence[str], duration_ms: int, *, scrim: bool) -> list[str]:
    whole = f"0:00:00.00,{_time(duration_ms, ass=True)}"
    events = []
    if scrim:
        events.append(f"Dialogue: 0,{whole},Shape,,0,0,0,,{{\\an7\\pos(0,0)\\1a&H{SCRIM_ALPHA:02X}&\\p1}}m 0 0 l {WIDTH} 0 {WIDTH} {HEIGHT} 0 {HEIGHT}{{\\p0}}")
    cards = [_text(item) for item in features if _text(item)]
    title = _text(name)
    if not cards and title:
        # Without highlights the name is the card.
        cards, title = [title], ""
    place = CHI_CHU["title"]
    if title:
        events.append(f"Dialogue: 2,{whole},Title,,0,0,0,,{{\\an8\\move({place.x},{place.y - 40},{place.x},{place.y},0,400)\\fad(400,0)}}{title}")
    left, top, width, height = CARD
    fade = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})"
    slots = card_times(len(cards), duration_ms)
    for index, (start, end) in enumerate(slots):
        span = f"{_time(start, ass=True)},{_time(end, ass=True)}"

        def move(x: int, y: int) -> str:
            return f"\\move({x + SLIDE_PX},{y},{x},{y},0,{SLIDE_MS})"
        events.append(f"Dialogue: 1,{span},Shape,,0,0,0,,{{\\an7{move(left, top)}{fade}\\1a&H{CARD_ALPHA:02X}&\\p1}}{_rounded(width, height, CARD_RADIUS)}{{\\p0}}")
        bar_x, bar_y, bar_w, bar_h = CARD_BAR
        events.append(f"Dialogue: 2,{span},Shape,,0,0,0,,{{\\an7{move(left + bar_x, top + bar_y)}{fade}\\1c&H{_ass_colour(_GOLD)[4:]}&\\p1}}"
                      f"{_rounded(bar_w, bar_h, bar_w // 2)}{{\\p0}}")
        if len(slots) > 1:
            counter = CHI_CHU["counter"]
            events.append(f"Dialogue: 3,{span},Counter,,0,0,0,,{{\\an7{move(counter.x, counter.y)}{fade}}}{index + 1}/{len(slots)}")
        text = CHI_CHU["card"]
        events.append(f"Dialogue: 3,{span},Card,,0,0,0,,{{\\an5{move(text.x, text.y)}{fade}}}{cards[index]}")
    if price.strip():
        place = CHI_CHU["price"]
        events.append(f"Dialogue: 2,{whole},Price,,0,0,0,,{{{place.tag()}\\fad(300,0)\\fscx70\\fscy70\\t(0,350,\\fscx100\\fscy100)}}{_text(price)}")
    return events


def ass_document(name: str, price: str, captions: Sequence[Caption], duration_ms: int, *, outlined: bool = False,
                 template: str = "ban-tin", features: Sequence[str] = ()) -> str:
    """The template's ASS subtitles; ``outlined`` means the user's background is behind the text."""
    whole = f"0:00:00.00,{_time(duration_ms, ass=True)}"
    if template == TEXT_ONLY:
        places = CHI_CHU
        styles = tuple(item.style.line() for item in CHI_CHU.values()) + (_SHAPE.line(),)
        events = _text_only_events(name, price, features, duration_ms, scrim=outlined)
    else:
        if template == "toan-man-hinh":
            places = TOAN_MAN_HINH
            events = []
            extra: tuple[Style, ...] = ()
        else:
            places = {**BAN_TIN, **BAN_TIN_OUTLINED} if outlined else BAN_TIN
            events = [f"Dialogue: 0,{whole},Strip,,0,0,0,,{{\\an7\\pos(0,{MEDIA_HEIGHT})\\p1}}m 0 0 l {WIDTH} 0 {WIDTH} {STRIP_HEIGHT} 0 {STRIP_HEIGHT}{{\\p0}}"]
            extra = (_STRIP,)
        styles = tuple(places[key].style.line() for key in ("title", "caption", "price")) + tuple(style.line() for style in extra)
        if name.strip():
            events.append(f"Dialogue: 0,{whole},Title,,0,0,0,,{{{places['title'].tag()}}}{_text(name)}")
        if price.strip():
            events.append(f"Dialogue: 0,{whole},Price,,0,0,0,,{{{places['price'].tag()}}}{_text(price)}")
    caption_at = places["caption"].tag()
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


def preview_layout() -> dict[str, Any]:
    """Every position, size and colour the desktop's live preview draws, in frame pixels."""
    def places(group: dict[str, Place]) -> dict[str, Any]:
        return {key: place.preview() for key, place in group.items()}
    return {
        "width": WIDTH, "height": HEIGHT, "font": FONT, "fullFrameRatio": FULL_FRAME_RATIO,
        "templates": {
            "ban-tin": {
                "media": MEDIA_HEIGHT, "panel": _hex(int(PANEL_COLOR, 16)),
                "strip": {"y": MEDIA_HEIGHT, "height": STRIP_HEIGHT, "colour": _hex(_GOLD)},
                "background": {"panel": [WIDTH, HEIGHT - MEDIA_HEIGHT], "frame": [WIDTH, HEIGHT]},
                **places(BAN_TIN), "outlined": places(BAN_TIN_OUTLINED),
            },
            "toan-man-hinh": {"media": HEIGHT, **places(TOAN_MAN_HINH)},
            TEXT_ONLY: {
                "media": 0, "gradient": [_hex(colour) for colour in GRADIENT], "scrim": round(1 - SCRIM_ALPHA / 255, 3),
                "background": {"frame": [WIDTH, HEIGHT]},
                "cardBox": {"x": CARD[0], "y": CARD[1], "width": CARD[2], "height": CARD[3], "radius": CARD_RADIUS,
                            "opacity": round(1 - CARD_ALPHA / 255, 3),
                            "bar": {"x": CARD_BAR[0], "y": CARD_BAR[1], "width": CARD_BAR[2], "height": CARD_BAR[3], "colour": _hex(_GOLD)}},
                "minCardMs": MIN_CARD_MS, "slide": {"px": SLIDE_PX, "ms": SLIDE_MS},
                **places(CHI_CHU),
            },
        },
    }


if __name__ == "__main__":
    print(json.dumps(preview_layout(), ensure_ascii=False, indent=2))
