"""Data slides (Tier-3): a full-screen "engineering blueprint" for the film's key figures.

Explaining a loss per pig or a market share over yet another shot of pens is
weak and burns source footage. So up to four scenes per film whose number card
says something comparable become a 3.5–4.5 s slide instead of footage, mostly
in the Problem and Verdict acts (a pattern interrupt where viewers drift):

* ``compare`` — two amounts in the card ("cost $238, price $168"): two bars,
  the larger in red, the gap between them called out;
* ``share`` — a percentage ("58%"): a 10×10 grid filling up to it;
* ``scale`` — a count of a familiar thing ("about 150 American football
  fields", "about 60 truckloads"): that many little silhouettes appearing;
* ``figure`` — a big sum of money ("$560 million"): the number counting up.

The picture is a dark blue-black canvas (``#0B101B``) with a faint CAD grid
drifting across it and the scene's own footage blurred behind at 15 %; bars,
icons and text are drawn by libass and animate in. The scene's number card is
not drawn over the slide (the slide is the card).
"""
from __future__ import annotations

import math
import re
from typing import Any, Sequence

import numpy as np

from . import graphics
from .graphics import FPS, HEIGHT, WIDTH, colour, drawing, escape, event, rect

KINDS = ("compare", "share", "scale", "figure")
MIN_SECONDS, MAX_SECONDS, SECONDS = 3.5, 4.5, 4.0
MAX_SLIDES = 4
PER_ACT = 2
SCORES = {"compare": 4.0, "share": 3.0, "scale": 3.0, "figure": 1.5}
ACT_BONUS = {"PROBLEM": 1.5, "CRITICAL_EVALUATION": 1.5}
BACKGROUND = "#0B101B"
RED, GREY, TEAL, AMBER, WHITE = "#E5484D", "#8A94A6", "#5FE3D8", "#FAC822", "#FFFFFF"
DRAW_W, DRAW_H = WIDTH // 2, HEIGHT // 2
BACKDROP = 0.15

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(million|billion|thousand|bn|m|k)\b)?", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
_COUNT = re.compile(r"^(?:about|roughly|nearly|almost|over|more than|≈|~)?\s*(\d[\d,]*(?:\.\d+)?)\s+([A-Za-z][A-Za-z' -]{2,39})$", re.I)
_SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "k": 1e3}
_FILLER = {"about", "roughly", "around", "nearly", "almost", "a", "an", "the", "for", "of", "at", "and", "to", "is", "was"}


def _amount(match: re.Match[str]) -> float:
    return float(match.group(1).replace(",", "")) * _SCALE.get((match.group(2) or "").casefold(), 1.0)


def _label_before(text: str, position: int) -> str:
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'-]*", text[:position])[-3:] if word.casefold() not in _FILLER]
    return (words[-1] if words else "").capitalize()


def classify(graphic: Any, narration: str = "") -> dict[str, Any] | None:
    """The slide a number card makes, or None when it is not a comparable figure."""
    if not isinstance(graphic, dict) or graphic.get("type") == "LOWER_THIRD":
        return None
    title, value, subtext = (str(graphic.get(key) or "").strip() for key in ("title", "value", "subtext"))
    if not value:
        return None
    base = {"title": title[:60], "value": value[:40], "subtext": subtext[:80]}
    amounts = list(_MONEY.finditer(subtext))
    if len(amounts) >= 2:
        items = [{"label": _label_before(subtext, match.start()) or f"Item {index + 1}", "amount": _amount(match), "text": match.group(0).strip()}
                 for index, match in enumerate(amounts[:2])]
        if all(item["amount"] > 0 for item in items) and items[0]["amount"] != items[1]["amount"]:
            return {"kind": "compare", **base, "items": items}
    percent = _PERCENT.search(value)
    if percent and 0 < float(percent.group(1)) <= 100:
        return {"kind": "share", **base, "percent": float(percent.group(1))}
    count = _COUNT.match(subtext)
    if count:
        number = float(count.group(1).replace(",", ""))
        unit = count.group(2).strip().rstrip(".")
        if unit.casefold().split()[0] in {"to", "or", "and", "per", "of"}:
            return None
        if (number >= 2 and number <= 300 and number == int(number)) or (1 < number <= 12):
            return {"kind": "scale", **base, "count": number, "unit": unit, "icon": icon_for(unit)}
    money = _MONEY.search(value)
    if money and _amount(money) >= 1e6:
        return {"kind": "figure", **base}
    return None


def icon_for(unit: str) -> str:
    words = unit.casefold()
    if "field" in words or "pitch" in words or "court" in words:
        return "field"
    if "truck" in words or "lorry" in words or "load" in words:
        return "truck"
    if "elephant" in words or "whale" in words or "bus" in words or "car" in words:
        return "block"
    return "block"


def select(scenes: Sequence[dict[str, Any]], avoid: Sequence[int] = ()) -> dict[int, dict[str, Any]]:
    """{scene index: slide} for at most four scenes, at most two per act, never next to each other or to ``avoid``.

    ``scenes`` are in narration order with ``chapter_type``, ``on_screen_graphic``, ``narration_text`` and ``slot``.
    """
    ranked = []
    for index, scene in enumerate(scenes):
        slide = classify(scene.get("on_screen_graphic"), str(scene.get("narration_text") or ""))
        if slide is None or float(scene.get("slot") or 0.0) < MIN_SECONDS:
            continue
        score = SCORES[slide["kind"]] + ACT_BONUS.get(str(scene.get("chapter_type")), 0.0)
        if slide["kind"] == "scale" and (slide["count"] != int(slide["count"]) or slide["count"] < 6):
            # "1.7 elephants" is a weak picture: a few icons say less than a bar chart or a big number.
            score -= 1.0
        ranked.append((-score, index, slide))
    chosen: dict[int, dict[str, Any]] = {}
    per_act: dict[str, int] = {}
    blocked = set(avoid)
    for _, index, slide in sorted(ranked, key=lambda item: (item[0], item[1])):
        act = str(scenes[index].get("chapter_type"))
        if len(chosen) >= MAX_SLIDES or per_act.get(act, 0) >= PER_ACT or {index - 1, index, index + 1} & blocked:
            continue
        chosen[index] = slide
        per_act[act] = per_act.get(act, 0) + 1
        blocked.add(index)
    return dict(sorted(chosen.items()))


def placement(slot: float, minimum: float) -> tuple[float, float] | None:
    """(offset, length) of a slide in a scene's slot: it opens the scene; footage before or after makes a whole slice."""
    for length in (SECONDS, MAX_SECONDS, 3.8, 4.2, MIN_SECONDS):
        for before in (0.0, minimum, 2.2, 2.6, 3.0):
            after = slot - before - length
            if (before == 0.0 or before >= minimum) and (abs(after) < 1e-6 or after >= minimum):
                return before, length
    if MIN_SECONDS <= slot <= MAX_SECONDS + 1e-6:
        return 0.0, slot
    return None


# ---- drawing ----------------------------------------------------------------------------------------------------

def backdrop(footage: np.ndarray | None) -> np.ndarray:
    """The still canvas (DRAW_W × DRAW_H): blue-black, the scene's footage blurred behind at 15 %, a vignette."""
    canvas = np.broadcast_to(graphics.solid(BACKGROUND), (DRAW_H, DRAW_W, 3)).astype(np.float32)
    if footage is not None:
        small = graphics.resize(footage, DRAW_W // 4, DRAW_H // 4)
        soft = graphics.resize(graphics.box_blur(small, 3), DRAW_W, DRAW_H)
        luma = soft @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        tinted = luma[..., None] * np.array([0.55, 0.75, 1.0], dtype=np.float32)
        canvas = canvas * (1 - BACKDROP) + tinted * BACKDROP
    canvas = canvas * graphics.vignette(0.35, DRAW_W, DRAW_H)
    return np.clip(canvas, 0, 255).astype(np.uint8)


def _grid(seconds: float, bar: int) -> list[str]:
    """The CAD grid: fine lines every 48 px, strong every 240 px, drifting slowly down and right."""
    fine, strong = [], []
    for x in range(0, WIDTH + 481, 48):
        (strong if x % 240 == 0 else fine).append(rect(x, 0, 1 if x % 240 else 2, HEIGHT + 480))
    for y in range(0, HEIGHT + 481, 48):
        (strong if y % 240 == 0 else fine).append(rect(0, y, WIDTH + 480, 1 if y % 240 else 2))
    move = f"\\move(-240,-240,-204,-216,0,{int(seconds * 1000)})"
    return [event(0, seconds, drawing(" ".join(fine), f"\\an7{move}\\1c{colour(TEAL)}\\1a&HEE&\\fad(200,150)"), layer=1),
            event(0, seconds, drawing(" ".join(strong), f"\\an7{move}\\1c{colour(TEAL)}\\1a&HD8&\\fad(200,150)"), layer=1)]


def _header(slide: dict[str, Any], seconds: float, top: int) -> list[str]:
    rows = [event(0.05, seconds, f"{{\\an7\\pos(120,{top})\\fs24\\1c{colour(TEAL)}\\fad(200,150)}}DATA  //  {escape(slide['kind'].upper())}", "Mono", 4),
            event(0.12, seconds, f"{{\\an7\\pos(120,{top + 36})\\fs56\\fad(250,150)\\move(120,{top + 52},120,{top + 36},0,350)}}{escape(slide['title'].upper() or slide['value'])}", "Head", 4)]
    rows.append(event(0.15, seconds, drawing(rect(0, 0, 90, 5), f"\\an7\\pos(120,{top + 112})\\1c{colour(AMBER)}\\fscx0\\t(150,600,\\fscx100)"), layer=4))
    return rows


def _bar(x: int, base: int, width: int, height: int, fill: str, start: float, end: float, grow: float = 0.75) -> str:
    """A bar standing on ``base``, growing up from zero."""
    tags = f"\\an1\\pos({x},{base})\\1c{colour(fill)}\\fscy0\\t({int(start * 1000)},{int((start + grow) * 1000)},0.5,\\fscy100)"
    return event(0, end, drawing(rect(0, 0, width, max(2, height)), tags), layer=3)


def _compare(slide: dict[str, Any], seconds: float, top: int, bottom: int) -> list[str]:
    items = slide["items"]
    big = max(item["amount"] for item in items)
    base, tallest, width = bottom - 70, max(200, bottom - top - 330), 230
    rows: list[str] = []
    centres = (WIDTH // 2 - 230, WIDTH // 2 + 170)
    heights = []
    for index, (item, centre) in enumerate(zip(items, centres)):
        height = int(tallest * item["amount"] / big)
        heights.append(height)
        fill = RED if item["amount"] == big else GREY
        start = 0.35 + index * 0.25
        rows.append(_bar(centre - width // 2, base, width, height, fill, start, seconds))
        rows += graphics.count_up(start + 0.4, seconds, item["text"], centre - width // 2, base - height - 92, "Big", "\\fs76\\fad(150,150)")
        rows.append(event(start, seconds, f"{{\\an8\\pos({centre},{base + 14})\\fs34\\fad(200,150)}}{escape(item['label'].upper())}", "Body", 4))
    # The gap, called out beside the taller bar.
    gap_x = centres[1] + width // 2 + 60
    high, low = base - max(heights), base - min(heights)
    rows.append(event(1.35, seconds, drawing(rect(0, 0, 4, low - high) + " " + rect(-18, 0, 22, 4) + " " + rect(-18, low - high - 4, 22, 4),
                                             f"\\an7\\pos({gap_x},{high})\\1c{colour(RED)}\\fad(250,150)"), layer=4))
    rows.append(event(1.45, seconds, f"{{\\an4\\pos({gap_x + 28},{(high + low) // 2})\\fs64\\1c{colour(RED)}\\fad(250,150)}}{escape(slide['value'])}", "Head", 4))
    if slide.get("title"):
        rows.append(event(1.6, seconds, f"{{\\an7\\pos({gap_x + 28},{(high + low) // 2 + 40})\\fs30\\fad(250,150)}}{escape(slide['title'].lower())}", "Body", 4))
    return rows


def _share(slide: dict[str, Any], seconds: float, top: int, bottom: int) -> list[str]:
    percent = float(slide["percent"])
    cell, gap = 34, 7
    size = 10 * cell + 9 * gap
    left, upper = 160, max(top + 150, (top + 150 + bottom - size) // 2)
    rows = [event(0.2, seconds, drawing(" ".join(rect(c * (cell + gap), r * (cell + gap), cell, cell) for r in range(10) for c in range(10)),
                                        f"\\an7\\pos({left},{upper})\\1c{colour(GREY)}\\1a&HC0&\\fad(250,150)"), layer=2)]
    filled = int(round(percent))
    for number in range(filled):
        # Fills from the bottom-left, row by row, like a gauge.
        r, c = 9 - number // 10, number % 10
        start = 0.45 + 1.3 * number / max(1, filled)
        rows.append(event(start, seconds, drawing(rect(0, 0, cell, cell), f"\\an7\\pos({left + c * (cell + gap)},{upper + r * (cell + gap)})\\1c{colour(AMBER)}\\fad(120,150)"), layer=3))
    text_x = left + size + 120
    rows += graphics.count_up(0.45, seconds, slide["value"], text_x, upper + 10, "Big", "\\fs190\\fad(150,150)", seconds=1.3)
    if slide.get("subtext"):
        rows.append(event(1.2, seconds, f"{{\\an7\\pos({text_x},{upper + 240})\\fs44\\fad(250,150)}}{escape(slide['subtext'])}", "Body", 4))
    return rows


def _icon(kind: str, w: int, h: int) -> str:
    if kind == "field":
        return " ".join([rect(0, 0, w, h), ])
    if kind == "truck":
        return " ".join([rect(0, h * 0.15, w * 0.68, h * 0.6), rect(w * 0.72, h * 0.35, w * 0.28, h * 0.4),
                         graphics.circle(h * 0.13, w * 0.2, h * 0.86), graphics.circle(h * 0.13, w * 0.82, h * 0.86)])
    return rect(0, 0, w, h)


def _scale(slide: dict[str, Any], seconds: float, top: int, bottom: int) -> list[str]:
    count = float(slide["count"])
    whole = int(math.ceil(count))
    area_left, area_top = WIDTH // 2 - 60, top + 160
    area_w, area_h = WIDTH - 140 - area_left, bottom - 40 - area_top
    columns = max(1, int(math.ceil(math.sqrt(whole * area_w / area_h * 0.62))))
    rows_n = int(math.ceil(whole / columns))
    cell_w = min(area_w / columns, 200.0)
    cell_h = min(area_h / rows_n, cell_w * 0.62)
    icon_w, icon_h = max(6, int(cell_w * 0.82)), max(4, int(cell_h * 0.78))
    fill = TEAL if slide["icon"] != "truck" else AMBER
    rows: list[str] = []
    for number in range(whole):
        r, c = number // columns, number % columns
        x, y = int(area_left + c * cell_w), int(area_top + r * cell_h)
        start = 0.4 + 1.4 * number / max(1, whole)
        width = icon_w if number + 1 <= count + 1e-6 else max(2, int(icon_w * (count - int(count))))
        shape = _icon(slide["icon"], width, icon_h)
        tags = f"\\an7\\pos({x},{y})\\1c{colour(fill)}\\fad(120,150)"
        if slide["icon"] == "field":
            tags += "\\1a&H40&"
        rows.append(event(start, seconds, drawing(shape, tags), layer=3))
        if slide["icon"] == "field" and width > 10:
            rows.append(event(start, seconds, drawing(rect(width // 2, 2, 1, max(2, icon_h - 4)), f"\\an7\\pos({x},{y})\\1c&HFFFFFF&\\1a&H90&\\fad(120,150)"), layer=4))
    rows += graphics.count_up(0.4, seconds, slide["value"], 120, top + 190, "Big", "\\fs110\\fad(150,150)", seconds=1.2)
    rows.append(event(1.0, seconds, f"{{\\an7\\pos(120,{top + 330})\\fs40\\1c{colour(fill)}\\fad(250,150)}}≈ {escape(slide['subtext'].removeprefix('about ').removeprefix('About '))}", "Body", 4))
    return rows


def _figure(slide: dict[str, Any], seconds: float, top: int, bottom: int) -> list[str]:
    middle = (top + bottom) // 2
    rows = graphics.count_up(0.35, seconds, slide["value"], 120, middle - 110, "Big", "\\fs200\\fad(150,150)", seconds=1.3)
    rows.append(event(0.3, seconds, drawing(rect(0, 0, WIDTH - 240, 8), f"\\an7\\pos(120,{middle + 120})\\1c{colour(AMBER)}\\fscx0\\t(300,1500,0.5,\\fscx100)"), layer=3))
    if slide.get("subtext"):
        rows.append(event(1.1, seconds, f"{{\\an7\\pos(120,{middle + 150})\\fs44\\fad(250,150)}}{escape(slide['subtext'])}", "Body", 4))
    return rows


def slide_ass(slide: dict[str, Any], seconds: float, bar: int) -> str:
    top, bottom = bar + 44, HEIGHT - bar - 30
    rows = _grid(seconds, bar) + _header(slide, seconds, top)
    draw = {"compare": _compare, "share": _share, "scale": _scale, "figure": _figure}[slide["kind"]]
    rows += draw(slide, seconds, top, bottom)
    return graphics.ass_head() + "".join(rows)


def frames(footage: np.ndarray | None, count: int) -> graphics.FrameSource:
    still = backdrop(footage)
    return graphics.frames_of(lambda index: still, count)


__all__ = ["KINDS", "classify", "frames", "placement", "select", "slide_ass"]
