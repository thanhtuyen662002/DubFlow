"""Quantities in Chinese and English text, US conversions and benchmark comparisons.

Code computes every conversion and comparison; the AI only phrases them. The
same tables check the finished narration: a number is accepted when it matches
a source quantity, a benchmark ratio of one, or a benchmark constant named in
the same sentence (ADR-0049).
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Sequence

# Approximate; the narration check allows ±10 % for amounts converted from yuan.
USD_PER_CNY = 0.14
USD_PER_CNY_DATE = "2026-11"

SOURCE_TOLERANCE = 0.06
MONEY_TOLERANCE = 0.10
RATIO_TOLERANCE = 0.15
CONSTANT_TOLERANCE = 0.03
TEMPERATURE_TOLERANCE_C = 2.0
PERCENT_TOLERANCE = 1.0


@dataclass(frozen=True)
class Quantity:
    text: str
    # SI base units (m, m², m³, kg, W, Wh, m/s, °C), USD, or the plain number.
    value: float
    # length area volume mass power energy speed temperature money percent ratio count year
    dimension: str
    start: int = 0
    end: int = 0
    # Benchmark the number counts ("3,200 Empire State Buildings") or is a multiple of.
    benchmark: str | None = None
    # Converted from yuan.
    from_cny: bool = False
    # A throughput (per day, per year): no single-object comparison applies.
    rate: bool = False


@dataclass(frozen=True)
class Benchmark:
    key: str
    name: str
    aliases: tuple[str, ...]
    # dimension -> (SI value, phrase used in comparisons)
    values: dict[str, tuple[float, str]]


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark("empire_state", "the Empire State Building", ("Empire State Buildings", "Empire State Building"), {
        "length": (443.2, "the height of the Empire State Building"),
        "volume": (1_047_723.0, "the volume of the Empire State Building"),
        "mass": (331_122_000.0, "the weight of the Empire State Building"),
    }),
    Benchmark("statue_of_liberty", "the Statue of Liberty", ("Statues of Liberty", "Statue of Liberty"), {
        "length": (93.0, "the height of the Statue of Liberty"),
        "mass": (204_117.0, "the weight of the Statue of Liberty"),
    }),
    Benchmark("football_field", "an American football field", (
        "American football fields", "American football field", "football fields", "football field"), {
        "area": (5_351.2, "an American football field"),
        "length": (109.73, "the length of a football field"),
    }),
    Benchmark("boeing_747", "a Boeing 747", ("Boeing 747s", "Boeing 747", "jumbo jets", "jumbo jet"), {
        "mass": (396_893.0, "a fully loaded Boeing 747"),
        "length": (70.66, "the length of a Boeing 747"),
    }),
    Benchmark("olympic_pool", "an Olympic swimming pool", (
        "Olympic-size swimming pools", "Olympic-size swimming pool", "Olympic swimming pools",
        "Olympic swimming pool", "Olympic pools", "Olympic pool"), {
        "volume": (2_500.0, "an Olympic swimming pool"),
    }),
    Benchmark("manhattan", "Manhattan", ("Manhattans", "Manhattan"), {
        "area": (58_800_000.0, "the area of Manhattan"),
        "length": (21_600.0, "the length of Manhattan"),
    }),
    Benchmark("hoover_dam", "the Hoover Dam", ("Hoover Dams", "Hoover Dam"), {
        "volume": (2_485_000.0, "the concrete in the Hoover Dam"),
        "length": (221.4, "the height of the Hoover Dam"),
    }),
    Benchmark("golden_gate", "the Golden Gate Bridge", ("Golden Gate Bridges", "Golden Gate Bridge"), {
        "length": (2_737.0, "the length of the Golden Gate Bridge"),
    }),
    Benchmark("washington_monument", "the Washington Monument", ("Washington Monuments", "Washington Monument"), {
        "length": (169.3, "the height of the Washington Monument"),
    }),
    Benchmark("central_park", "Central Park", ("Central Parks", "Central Park"), {
        "area": (3_410_000.0, "the area of Central Park"),
    }),
    Benchmark("blue_whale", "a blue whale", ("blue whales", "blue whale"), {
        "mass": (150_000.0, "a blue whale"),
        "length": (30.0, "the length of a blue whale"),
    }),
    Benchmark("african_elephant", "an African elephant", ("African elephants", "African elephant"), {
        "mass": (6_000.0, "an African elephant"),
    }),
)
_BENCHMARK = {benchmark.key: benchmark for benchmark in BENCHMARKS}

# (dimension, factor to SI); "temperature_f" converts with an offset.
_ZH_UNITS: dict[str, tuple[str, float]] = {
    "平方公里": ("area", 1e6), "平方千米": ("area", 1e6), "平方米": ("area", 1.0), "平米": ("area", 1.0),
    "公顷": ("area", 1e4), "亩": ("area", 10_000 / 15),
    "立方米": ("volume", 1.0), "立方": ("volume", 1.0), "方": ("volume", 1.0), "升": ("volume", 0.001),
    "千瓦时": ("energy", 1e3), "度电": ("energy", 1e3),
    "毫米": ("length", 0.001), "厘米": ("length", 0.01), "千米": ("length", 1000.0), "公里": ("length", 1000.0),
    "米": ("length", 1.0),
    "千克": ("mass", 1.0), "公斤": ("mass", 1.0), "吨": ("mass", 1000.0), "斤": ("mass", 0.5), "克": ("mass", 0.001),
    "吉瓦": ("power", 1e9), "兆瓦": ("power", 1e6), "千瓦": ("power", 1e3), "瓦": ("power", 1.0),
    "摄氏度": ("temperature", 1.0), "℃": ("temperature", 1.0),
    "美元": ("money", 1.0), "人民币": ("money_cny", 1.0), "元": ("money_cny", 1.0),
    "百分点": ("percent", 1.0),
    "倍": ("ratio", 1.0),
    "年": ("year", 1.0),
    "名": ("count", 1.0), "人": ("count", 1.0), "位": ("count", 1.0), "台": ("count", 1.0), "座": ("count", 1.0),
    "辆": ("count", 1.0), "艘": ("count", 1.0), "架": ("count", 1.0), "根": ("count", 1.0), "个": ("count", 1.0),
    "层": ("count", 1.0), "条": ("count", 1.0), "头": ("count", 1.0), "只": ("count", 1.0), "栋": ("count", 1.0),
    "部": ("count", 1.0), "家": ("count", 1.0), "公里/小时": ("speed", 1 / 3.6), "千米/小时": ("speed", 1 / 3.6),
    "公里每小时": ("speed", 1 / 3.6), "千米每小时": ("speed", 1 / 3.6),
}
_ZH_UNIT_ORDER = sorted(_ZH_UNITS, key=len, reverse=True)

_EN_UNITS: tuple[tuple[str, str, float], ...] = tuple(sorted((
    ("square feet", "area", 0.09290304), ("square foot", "area", 0.09290304), ("sq ft", "area", 0.09290304),
    ("ft²", "area", 0.09290304), ("square miles", "area", 2_589_988.11), ("square mile", "area", 2_589_988.11),
    ("square kilometers", "area", 1e6), ("square kilometres", "area", 1e6), ("km²", "area", 1e6),
    ("square meters", "area", 1.0), ("square metres", "area", 1.0), ("m²", "area", 1.0),
    ("acres", "area", 4_046.8564224), ("acre", "area", 4_046.8564224), ("hectares", "area", 1e4), ("hectare", "area", 1e4),
    ("cubic feet", "volume", 0.028316846592), ("cubic foot", "volume", 0.028316846592), ("cu ft", "volume", 0.028316846592),
    ("ft³", "volume", 0.028316846592), ("cubic yards", "volume", 0.764554857984), ("cubic yard", "volume", 0.764554857984),
    ("cubic meters", "volume", 1.0), ("cubic metres", "volume", 1.0), ("m³", "volume", 1.0),
    ("acre-feet", "volume", 1_233.48183754752), ("gallons", "volume", 0.003785411784), ("gallon", "volume", 0.003785411784),
    ("liters", "volume", 0.001), ("litres", "volume", 0.001),
    ("feet", "length", 0.3048), ("foot", "length", 0.3048), ("ft", "length", 0.3048), ("inches", "length", 0.0254),
    ("inch", "length", 0.0254),
    ("yards", "length", 0.9144), ("yard", "length", 0.9144), ("miles", "length", 1_609.344), ("mile", "length", 1_609.344),
    ("kilometers", "length", 1000.0), ("kilometres", "length", 1000.0), ("km", "length", 1000.0),
    ("meters", "length", 1.0), ("metres", "length", 1.0), ("meter", "length", 1.0), ("metre", "length", 1.0),
    ("millimeters", "length", 0.001), ("centimeters", "length", 0.01), ("mm", "length", 0.001), ("cm", "length", 0.01),
    ("m", "length", 1.0),
    ("short tons", "mass", 907.18474), ("metric tons", "mass", 1000.0), ("tonnes", "mass", 1000.0), ("tonne", "mass", 1000.0),
    ("tons", "mass", 907.18474), ("ton", "mass", 907.18474), ("pounds", "mass", 0.45359237), ("pound", "mass", 0.45359237),
    ("lbs", "mass", 0.45359237), ("lb", "mass", 0.45359237), ("kilograms", "mass", 1.0), ("kilogram", "mass", 1.0),
    ("kg", "mass", 1.0),
    ("gigawatts", "power", 1e9), ("gigawatt", "power", 1e9), ("GW", "power", 1e9), ("megawatts", "power", 1e6),
    ("megawatt", "power", 1e6), ("MW", "power", 1e6), ("kilowatts", "power", 1e3), ("kilowatt", "power", 1e3),
    ("kW", "power", 1e3), ("horsepower", "power", 745.699872), ("hp", "power", 745.699872), ("watts", "power", 1.0),
    ("TWh", "energy", 1e12), ("GWh", "energy", 1e9), ("MWh", "energy", 1e6), ("kWh", "energy", 1e3),
    ("terawatt-hours", "energy", 1e12), ("gigawatt-hours", "energy", 1e9), ("megawatt-hours", "energy", 1e6),
    ("kilowatt-hours", "energy", 1e3),
    ("mph", "speed", 0.44704), ("miles per hour", "speed", 0.44704), ("km/h", "speed", 1 / 3.6), ("kph", "speed", 1 / 3.6),
    ("knots", "speed", 0.514444),
    ("°F", "temperature_f", 1.0), ("degrees Fahrenheit", "temperature_f", 1.0), ("°C", "temperature", 1.0),
    ("degrees Celsius", "temperature", 1.0),
    ("percent", "percent", 1.0), ("%", "percent", 1.0),
    ("dollars", "money", 1.0), ("USD", "money", 1.0), ("yuan", "money_cny", 1.0), ("RMB", "money_cny", 1.0),
    ("CNY", "money_cny", 1.0),
    ("times", "ratio", 1.0), ("x", "ratio", 1.0),
), key=lambda unit: len(unit[0]), reverse=True))
# Metric units an American narration should not keep (watts are fine).
_METRIC = frozenset({"meters", "metres", "meter", "metre", "kilometers", "kilometres", "km", "m", "mm", "cm",
                     "millimeters", "centimeters", "square meters", "square metres", "m²", "square kilometers",
                     "square kilometres", "km²", "hectares", "hectare", "cubic meters", "cubic metres", "m³",
                     "liters", "litres", "kilograms", "kilogram", "kg", "tonnes", "tonne", "metric tons",
                     "km/h", "kph", "°C", "degrees Celsius"})
_METRIC_LABEL = {"meters": "mét", "metres": "mét", "meter": "mét", "metre": "mét", "m": "mét", "kilometers": "km",
                 "kilometres": "km", "km": "km", "kg": "kg", "kilograms": "kg", "kilogram": "kg",
                 "tonnes": "tấn", "tonne": "tấn", "metric tons": "tấn", "°C": "°C", "degrees Celsius": "°C"}

_EN_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12, "bn": 1e9}
_LETTER_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_SMALL = {"十": 10, "百": 100, "千": 1000}
_ZH_BIG = {"万": 1e4, "亿": 1e8}
_ZH_CHARS = "零〇一二两三四五六七八九十百千万亿"
# Speech recognition writes Traditional characters for some speakers (Taiwan, Hong Kong, or just the model's
# choice). One character for one character, so positions stay the same.
_TRADITIONAL = str.maketrans({
    "萬": "万", "億": "亿", "兩": "两", "點": "点", "噸": "吨", "畝": "亩", "頃": "顷", "釐": "厘", "時": "时",
    "攝": "摄", "幣": "币", "圓": "元", "臺": "台", "個": "个", "層": "层", "輛": "辆", "條": "条", "頭": "头",
    "隻": "只", "棟": "栋", "車": "车", "電": "电", "處": "处", "產": "产", "欄": "栏", "數": "数",
})
# 七点八八 -> 7.88: a Chinese numeral with a decimal part.
_ZH_DECIMAL = "点"

_NUMBER = re.compile(
    r"(?P<dollar>\$\s?)?(?P<num>(?<![A-Za-z0-9_.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9_.,])\d+(?:\.\d+)?)"
    r"|(?P<zh>[" + _ZH_CHARS + r"]+(?:点[零〇一二两三四五六七八九]+)?)")
# A quantity per day or year (wastewater treated daily, pigs sold yearly) is a rate, not an object to compare.
_RATE_BEFORE = ("每天", "每日", "日处理", "日产", "每年", "年产", "年出栏", "年处理", "每小时", "一年", "一天", "日均", "年均")
_RATE_AFTER = ("/天", "/日", "/年", "每天", "每日", "每年", "一天", "一年")
_ZH_SCALE = re.compile(r"[十百千万亿]+")
_ORDINAL = re.compile(r"(?:st|nd|rd|th)\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"⟦B:([a-z_0-9]+)⟧")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])")


def chinese_number(text: str) -> float | None:
    """一亿三千万 -> 130000000, 七点八八 -> 7.88; None when the characters are not a number."""
    if _ZH_DECIMAL in text:
        whole, _, fraction = text.partition(_ZH_DECIMAL)
        integer = chinese_number(whole) if whole else None
        if integer is None or not fraction or any(char not in _ZH_DIGITS for char in fraction):
            return None
        return integer + float("0." + "".join(str(_ZH_DIGITS[char]) for char in fraction))
    total, section, number, seen = 0.0, 0.0, 0.0, False
    for char in text:
        if char in _ZH_DIGITS:
            number, seen = _ZH_DIGITS[char], True
        elif char in _ZH_SMALL:
            section += (number if seen else 1) * _ZH_SMALL[char]
            number, seen = 0, False
        elif char in _ZH_BIG:
            section += number
            if _ZH_BIG[char] == 1e8:
                total = (total + section) * 1e8
            else:
                total += (section or 1) * 1e4
            section, number, seen = 0, 0, False
        else:
            return None
    return total + section + number


def _scale_of(chars: str) -> float:
    scale = 1.0
    for char in chars:
        scale *= _ZH_SMALL.get(char, 1) * _ZH_BIG.get(char, 1)
    return scale


def _mask_benchmarks(text: str) -> str:
    """Benchmark names become placeholders so "Boeing 747" is not a number."""
    for benchmark in BENCHMARKS:
        for alias in sorted(benchmark.aliases, key=len, reverse=True):
            text = re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", f"⟦B:{benchmark.key}⟧", text, flags=re.IGNORECASE)
    return text


def _english_unit(rest: str) -> tuple[str, str, float, int] | None:
    stripped = rest.lstrip(" - ")
    offset = len(rest) - len(stripped)
    for name, dimension, factor in _EN_UNITS:
        if stripped[:len(name)].casefold() == name.casefold():
            after = stripped[len(name):len(name) + 1]
            if name[-1].isalpha() and after and (after.isalpha() or after == "²" or after == "³"):
                continue
            if name in {"m", "x"} and stripped[:len(name)] != name:
                continue
            return name, dimension, factor, offset + len(name)
    return None


def _make(text: str, value: float, dimension: str, start: int, end: int, benchmark: str | None = None) -> Quantity:
    if dimension == "money_cny":
        return Quantity(text, value * USD_PER_CNY, "money", start, end, benchmark, True)
    if dimension == "temperature_f":
        return Quantity(text, (value - 32) * 5 / 9, "temperature", start, end, benchmark)
    return Quantity(text, value, dimension, start, end, benchmark)


def _rated(quantity: Quantity, text: str) -> Quantity:
    """Mark a Chinese quantity said per day or per year."""
    before = text[max(0, quantity.start - 12):quantity.start]
    after = text[quantity.end:quantity.end + 3]
    if any(word in before for word in _RATE_BEFORE) or after.startswith(_RATE_AFTER):
        return Quantity(quantity.text, quantity.value, quantity.dimension, quantity.start, quantity.end,
                        quantity.benchmark, quantity.from_cny, True)
    return quantity


def extract(text: str) -> list[Quantity]:
    """Every number in ``text`` with its unit, scale and dimension (Chinese or English)."""
    masked = _mask_benchmarks(text).translate(_TRADITIONAL)
    found: list[Quantity] = []
    consumed = 0
    for match in _NUMBER.finditer(masked):
        start, end = match.span()
        if found:
            consumed = max(consumed, found[-1].end)
        if start < consumed:
            continue
        if match.group("zh"):
            chars = match.group("zh")
            value = chinese_number(chars)
            if value is None or not any(c in _ZH_DIGITS or c in _ZH_SMALL for c in chars):
                continue
            unit = next((u for u in _ZH_UNIT_ORDER if masked.startswith(u, end)), None)
            prefix = masked[max(0, start - 4):start]
            if "百分之" in prefix:
                found.append(Quantity(masked[start - 3:end], value, "percent", start - 3, end))
                continue
            # A bare Chinese numeral is too often an ordinary word (一些, 万一).
            if unit is None or (_ZH_UNITS[unit][0] in {"count", "year"} and value < 10):
                continue
            dimension, factor = _ZH_UNITS[unit]
            if unit == "方" and value < 1000:
                continue
            if "时速" in prefix and dimension == "length":
                dimension, factor = "speed", factor / 3600
            if "零下" in prefix and dimension == "temperature":
                value = -value
            found.append(_rated(_make(masked[start:end + len(unit)], value * factor, dimension, start, end + len(unit)), masked))
            continue
        number = float(match.group("num").replace(",", ""))
        dollar = bool(match.group("dollar"))
        cursor = end
        rest = masked[cursor:]
        if _ORDINAL.match(rest):
            continue
        # Chinese scale characters after Arabic digits: 800万, 3.5亿.
        zh_scale = _ZH_SCALE.match(rest)
        if zh_scale:
            number *= _scale_of(zh_scale.group())
            cursor += zh_scale.end()
            rest = masked[cursor:]
        else:
            word = re.match(r"\s*(thousand|million|billion|trillion|bn)\b", rest, re.IGNORECASE)
            letter = re.match(r"([kKmMbB])(?![\w²³])", rest)
            if word:
                number *= _EN_SCALE[word.group(1).casefold()]
                cursor += word.end()
                rest = masked[cursor:]
            elif letter and (dollar or letter.group(1) in "KMB"):
                number *= _LETTER_SCALE[letter.group(1).casefold()]
                cursor += letter.end()
                rest = masked[cursor:]
        if dollar:
            found.append(Quantity(masked[start:cursor].strip(), number, "money", start, cursor))
            continue
        unit = next((u for u in _ZH_UNIT_ORDER if rest.startswith(u)), None)
        if unit:
            dimension, factor = _ZH_UNITS[unit]
            prefix = masked[max(0, start - 4):start]
            if "时速" in prefix and dimension == "length":
                dimension, factor = "speed", factor / 3600
            if "零下" in prefix and dimension == "temperature":
                number = -number
            found.append(_rated(_make(masked[start:cursor + len(unit)], number * factor, dimension, start, cursor + len(unit)), masked))
            continue
        benchmark = re.match(r"\s*(?:times\s+(?:as\s+\w+\s+as\s+|the\s+\w+\s+of\s+|\w+\s+than\s+)?)?(?:an?\s+|the\s+)?⟦B:([a-z_0-9]+)⟧", rest)
        if benchmark:
            dimension = "ratio" if "times" in benchmark.group(0) else "count"
            named = _BENCHMARK[benchmark.group(1)]
            label = masked[start:cursor].strip()
            label += f" times {named.name}" if dimension == "ratio" else f" {named.aliases[0] if number != 1 else named.aliases[-1]}"
            found.append(Quantity(label, number, dimension, start, cursor + benchmark.end(), benchmark.group(1)))
            continue
        english = _english_unit(rest)
        if english:
            name, dimension, factor, length = english
            found.append(_make(masked[start:cursor + length].strip(), number * factor, dimension, start, cursor + length))
            continue
        plain = match.group("num")
        if re.fullmatch(r"(1[89]|20)\d\d", plain):
            found.append(Quantity(plain, number, "year", start, cursor))
        else:
            found.append(Quantity(masked[start:cursor].strip(), number, "count", start, cursor))
    return found


def _figure(value: float) -> str:
    rounded = float(f"{value:.4g}")
    if rounded >= 1000:
        return f"{rounded:,.0f}"
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


def _big(value: float) -> str:
    for scale, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if abs(value) >= scale:
            return f"{_figure(value / scale)} {word}"
    return _figure(value)


def _times(ratio: float) -> str:
    """Spoken precision for a multiple: 7.6, 32, 3,200."""
    if ratio < 10:
        return f"{ratio:.1f}".rstrip("0").rstrip(".")
    if ratio < 100:
        return str(round(ratio))
    return _big(float(f"{ratio:.2g}"))


def imperial(quantity: Quantity) -> str | None:
    """The US way to say a quantity: "282.5 million cubic feet", "$4.76 billion"."""
    value, dimension = quantity.value, quantity.dimension
    if dimension == "length":
        if value < 0.3048:
            return f"{_figure(value / 0.0254)} inches"
        return f"{_big(value / 0.3048)} feet" if value < 1609.344 else f"{_big(value / 1609.344)} miles"
    if dimension == "area":
        if value < 40_000:
            return f"{_big(value / 0.09290304)} square feet"
        if value < 25_899_881:
            return f"{_big(value / 4046.8564224)} acres"
        return f"{_big(value / 2_589_988.11)} square miles"
    if dimension == "volume":
        return f"{_big(value / 0.028316846592)} cubic feet"
    if dimension == "mass":
        if value < 0.45359237:
            return f"{_figure(value / 0.028349523125)} ounces"
        return f"{_big(value / 0.45359237)} pounds" if value < 907.18474 else f"{_big(value / 907.18474)} tons"
    if dimension == "power":
        for scale, unit in ((1e9, "gigawatts"), (1e6, "megawatts"), (1e3, "kilowatts")):
            if value >= scale:
                return f"{_big(value / scale)} {unit}"
        return f"{_figure(value)} watts"
    if dimension == "energy":
        for scale, unit in ((1e12, "terawatt-hours"), (1e9, "gigawatt-hours"), (1e6, "megawatt-hours")):
            if value >= scale:
                return f"{_big(value / scale)} {unit}"
        return f"{_big(value / 1e3)} kilowatt-hours"
    if dimension == "speed":
        return f"{_figure(value / 0.44704)} mph"
    if dimension == "temperature":
        return f"{_figure(value * 9 / 5 + 32)}°F"
    if dimension == "money":
        return f"${_big(value)}"
    return None


def comparisons(quantity: Quantity, limit: int = 2) -> list[str]:
    """Benchmark comparisons of one quantity, the most graspable ratios first."""
    found: list[tuple[float, str]] = []
    if quantity.rate:
        # "10,000 tons of wastewater a day" is not 25 jumbo jets.
        return []
    for benchmark in BENCHMARKS:
        entry = benchmark.values.get(quantity.dimension)
        if not entry or quantity.value <= 0:
            continue
        ratio = quantity.value / entry[0]
        if not 0.2 <= ratio <= 100_000:
            continue
        if ratio >= 1.5:
            phrase = f"about {_times(ratio)} times {entry[1]}"
        elif ratio >= 0.95:
            phrase = f"about the same as {entry[1]}"
        else:
            phrase = f"about {round(ratio * 100)}% of {entry[1]}"
        found.append((abs(math.log10(max(ratio, 1e-9)) - 0.6), phrase))
    return [phrase for _, phrase in sorted(found)[:limit]]


def describe(quantity: Quantity) -> dict[str, object]:
    return {"text": quantity.text, "dimension": quantity.dimension, "si": round(quantity.value, 6),
            "imperial": imperial(quantity), "comparisons": comparisons(quantity)}


def _close(a: float, b: float, tolerance: float) -> bool:
    return b != 0 and abs(a - b) <= abs(b) * tolerance


def _matches(quantity: Quantity, sources: Sequence[Quantity], sentence_benchmarks: Iterable[str]) -> bool:
    sentence_benchmarks = tuple(sentence_benchmarks)
    named = [quantity.benchmark] if quantity.benchmark else []
    if named:
        benchmark = _BENCHMARK[named[0]]
        for source in sources:
            entry = benchmark.values.get(source.dimension)
            if entry and _close(quantity.value, source.value / entry[0], RATIO_TOLERANCE):
                return True
    if quantity.dimension == "ratio":
        for key in sentence_benchmarks:
            for source in sources:
                entry = _BENCHMARK[key].values.get(source.dimension)
                if entry and _close(quantity.value, source.value / entry[0], RATIO_TOLERANCE):
                    return True
    for source in sources:
        if quantity.dimension == "temperature" and source.dimension == "temperature":
            if abs(quantity.value - source.value) <= TEMPERATURE_TOLERANCE_C:
                return True
        elif quantity.dimension == "percent" and source.dimension == "percent":
            if abs(quantity.value - source.value) <= PERCENT_TOLERANCE or _close(quantity.value, source.value, SOURCE_TOLERANCE):
                return True
        elif quantity.dimension == source.dimension or {quantity.dimension, source.dimension} <= {"count", "year"}:
            tolerance = MONEY_TOLERANCE if quantity.from_cny or source.from_cny else SOURCE_TOLERANCE
            if _close(quantity.value, source.value, tolerance):
                return True
    for key in sentence_benchmarks:
        entry = _BENCHMARK[key].values.get(quantity.dimension)
        if entry and _close(quantity.value, entry[0], CONSTANT_TOLERANCE):
            return True
    return False


def unverified(text: str, sources: Sequence[Quantity], acknowledged: Iterable[str] = ()) -> list[str]:
    """Numbers in narration that no source quantity, benchmark ratio or benchmark constant explains."""
    skipped = {value.casefold().strip() for value in acknowledged}
    flagged: list[str] = []
    for sentence in filter(None, _SENTENCE.split(text)):
        masked = _mask_benchmarks(sentence)
        benchmarks = _PLACEHOLDER.findall(masked)
        for quantity in extract(sentence):
            label = _PLACEHOLDER.sub(lambda m: _BENCHMARK[m.group(1)].name, quantity.text).strip()
            if quantity.dimension == "count" and not quantity.benchmark and quantity.value <= 10 and quantity.value == int(quantity.value):
                continue
            if label.casefold() in skipped or label in flagged:
                continue
            if not _matches(quantity, sources, benchmarks):
                flagged.append(label)
    return flagged


def metric_units(text: str) -> list[str]:
    """Metric units left in English narration, as Vietnamese labels."""
    labels: list[str] = []
    for quantity in extract(text):
        rest = quantity.text
        for name in sorted(_METRIC, key=len, reverse=True):
            if re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", rest):
                label = _METRIC_LABEL.get(name, name)
                if label not in labels:
                    labels.append(label)
                break
    return labels
