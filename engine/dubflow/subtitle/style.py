"""User-chosen look of burned Vietnamese subtitles (Issue #414).

A style is optional job input. Without one the worker writes exactly the ASS
it always wrote; with one, only the ``Default`` style line, a scaled-border
header flag and an optional per-event position change. Fonts are limited to
stock Windows families whose fonts cover every precomposed Vietnamese letter,
so libass (DirectWrite provider in the bundled FFmpeg) never falls back
mid-word. Georgia, Trebuchet MS, Arial Black, Impact and Bahnschrift were
checked and left out: they lack Vietnamese letters (Bahnschrift lacks "ỷ").
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

SUBTITLE_STYLE_SCHEMA_VERSION = 1

# Stock Windows 10/11 families with full Vietnamese coverage (checked against
# the precomposed letters U+1EA0..U+1EF9 and the base Vietnamese set).
FONTS: tuple[str, ...] = (
    "Arial",
    "Segoe UI",
    "Tahoma",
    "Verdana",
    "Times New Roman",
    "Calibri",
    "Cambria",
    "Palatino Linotype",
    "Constantia",
    "Corbel",
    "Candara",
    "Segoe UI Black",
)
ALIGNMENTS = {"bottom": 2, "middle": 5, "top": 8}
MIN_SIZE, MAX_SIZE = 0.6, 2.0
MAX_OUTLINE = 8.0
# The worker's subtitle canvas and today's style values (production_job.py).
PLAY_RES_X, PLAY_RES_Y = 1920, 1080
BASE_FONT_SIZE = 48
MARGIN_X, MARGIN_V = 40, 48
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_FIELDS = {"font", "size", "primary", "outline", "box", "box_opacity", "outline_width", "bold", "align", "x", "y"}


class SubtitleStyleError(ValueError):
    """A subtitle style that the worker refuses to render."""


def _number(value: Any, name: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise SubtitleStyleError(f"{name} must be a number from {low:g} to {high:g}")
    return float(value)


def _color(value: Any, name: str) -> str:
    if not isinstance(value, str) or _COLOR.fullmatch(value) is None:
        raise SubtitleStyleError(f"{name} must be a #RRGGBB colour")
    return value.upper()


@dataclass(frozen=True)
class SubtitleStyle:
    font: str = "Arial"
    size: float = 1.0
    primary: str = "#FFFFFF"
    outline: str = "#000000"
    box: str | None = None
    box_opacity: float = 0.6
    outline_width: float = 3.0
    bold: bool = False
    align: str = "bottom"
    # Normalized centre of the subtitle block; both or neither.
    x: float | None = None
    y: float | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "SubtitleStyle":
        if not isinstance(value, Mapping):
            raise SubtitleStyleError("subtitle_style must be an object")
        unknown = set(value) - _FIELDS
        if unknown:
            raise SubtitleStyleError(f"subtitle_style has unknown fields: {', '.join(sorted(map(str, unknown)))}")
        missing = {"font", "size", "primary", "outline", "box", "box_opacity", "outline_width", "bold", "align"} - set(value)
        if missing:
            raise SubtitleStyleError(f"subtitle_style is missing fields: {', '.join(sorted(missing))}")
        font = value["font"]
        if font not in FONTS:
            raise SubtitleStyleError("subtitle_style.font must be one of the supported Vietnamese fonts")
        if type(value["bold"]) is not bool:
            raise SubtitleStyleError("subtitle_style.bold must be a boolean")
        if value["align"] not in ALIGNMENTS:
            raise SubtitleStyleError("subtitle_style.align must be bottom, middle or top")
        box = None if value["box"] is None else _color(value["box"], "subtitle_style.box")
        x, y = value.get("x"), value.get("y")
        if (x is None) != (y is None):
            raise SubtitleStyleError("subtitle_style.x and subtitle_style.y go together")
        return cls(
            font=font,
            size=_number(value["size"], "subtitle_style.size", MIN_SIZE, MAX_SIZE),
            primary=_color(value["primary"], "subtitle_style.primary"),
            outline=_color(value["outline"], "subtitle_style.outline"),
            box=box,
            box_opacity=_number(value["box_opacity"], "subtitle_style.box_opacity", 0.0, 1.0),
            outline_width=_number(value["outline_width"], "subtitle_style.outline_width", 0.0, MAX_OUTLINE),
            bold=value["bold"],
            align=value["align"],
            x=None if x is None else _number(x, "subtitle_style.x", 0.0, 1.0),
            y=None if y is None else _number(y, "subtitle_style.y", 0.0, 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"font": self.font, "size": self.size, "primary": self.primary, "outline": self.outline,
                                 "box": self.box, "box_opacity": self.box_opacity, "outline_width": self.outline_width,
                                 "bold": self.bold, "align": self.align}
        if self.x is not None:
            value.update(x=self.x, y=self.y)
        return value

    @property
    def font_size(self) -> int:
        return max(1, round(BASE_FONT_SIZE * self.size))

    @property
    def box_padding(self) -> int:
        """libass BorderStyle 4 pads the box by the Shadow value."""
        return max(2, round(self.font_size * 0.2))


def _ass_color(value: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS alpha 00 is opaque)."""
    red, green, blue = value[1:3], value[3:5], value[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def _number_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def ass_style_line(style: SubtitleStyle) -> str:
    """The ``Default`` style line for a chosen look (PlayRes 1920x1080).

    A box uses libass BorderStyle 4: the box in BackColour (padded by the
    Shadow value) with the text outline kept, unlike BorderStyle 3 which
    paints the box in the outline colour and drops the outline.
    """
    if style.box is not None:
        back = _ass_color(style.box, round((1.0 - style.box_opacity) * 255))
        border_style, shadow = 4, style.box_padding
    else:
        back, border_style, shadow = "&H64000000", 1, 0
    return (f"Style: Default,{style.font},{style.font_size},{_ass_color(style.primary)},&H000000FF,{_ass_color(style.outline)},{back},"
            f"{-1 if style.bold else 0},0,0,0,100,100,0,0,{border_style},{_number_text(style.outline_width)},{shadow},"
            f"{ALIGNMENTS[style.align]},{MARGIN_X},{MARGIN_X},{MARGIN_V},1")


def ass_event_prefix(style: SubtitleStyle) -> str:
    """Override tags placing a dragged block by its centre; empty when unplaced."""
    if style.x is None or style.y is None:
        return ""
    return f"{{\\an5\\pos({round(style.x * PLAY_RES_X)},{round(style.y * PLAY_RES_Y)})}}"


def escape_ass_text(value: str) -> str:
    """Reviewed text is literal: braces must not start override blocks."""
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\r", "").replace("\n", "\\N")
