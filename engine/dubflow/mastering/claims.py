"""What a sentence says is *written or displayed* in the picture, and whether a shot's evidence shows it (#802).

A sentence such as "Bản đồ có đường thẳng đánh dấu khoảng cách một nghìn năm trăm dặm" names a fact the picture
must show: the number 1 500 and the unit (miles). Words shared with the description of any map ("bản đồ", "đánh
dấu", "đường") are no proof of that fact: they let the planner put a map of 500 000 square miles, or a map with no
number at all, under the sentence and call it READY (rc13 / rc15 W3 i16.u1). The same holds for a title named in
the sentence ("Dòng chữ lớn The Battle of Russia…", i1.u2) and for what a card is said to state ("Một tấm thẻ chữ…
cho biết bộ phim có giá trị lịch sử", i1.u1).

Three kinds of claims are read from a sentence:

* **quantity**: a number, written with digits ("1.500", "1,500", "1500") or spelled in Vietnamese ("một nghìn năm
  trăm"), with the unit after it when there is one (dặm / mile, dặm vuông / square miles, km, %, người…);
* **phrase**: a foreign-language title or quote the sentence says is written ("dòng chữ The Battle of Russia",
  "tiêu đề …", a quoted string);
* **written content**: what a card, a line of text or a screen is said to state ("thẻ chữ … cho biết …").

A shot's content evidence (vision, transcript, the user's notes; never a file name) *verifies* a claim when it
shows it, and *contradicts* a quantity when it shows another number of the same unit (500 000 square miles for
1 500 miles). Nothing here reads the ground truth, and nothing lowers a threshold: a verified claim is a reason to
prefer a shot, a contradicted one a reason against it, and a sentence about something displayed whose chosen shots
verify none of what it says is a sentence a person must look at.

A number is read apart from what it counts (#817). A count of people ("193.000.000 người", or a unit-less figure
after "dân số": "biểu đồ dân số 1.500") is shown only by a figure the same evidence line ties to people: the unit
after it, or one of a few words for a population right before or after it (``MEASURE_WORDS``). A bare figure, a
figure with another unit ("1500 MILES"), a year, a price, an ordinal ("No. 1500") never shows a count. A different
figure tied to people ("POPULATION 93,000,000" for 193 000 000 người) is *disputed*: a reason for a person to look,
never a hard conflict, because a misread digit must not move the planner off the right shot.

Read time: :func:`statuses` gives each claim of a log's ``signals.claims`` one of ``CONFIRMED``, ``CONTRADICTED``,
``UNVERIFIED`` or ``NOT_APPLICABLE`` (a sentence that speaks of nothing displayed); :func:`unresolved` lists the
displayed claims a person still has to settle. A user's approval settles the sentence, never the claim's status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import functools
import re
import unicodedata
from typing import Iterable, Mapping

from . import evidence as ev

# --- numbers ------------------------------------------------------------------------------------------------------

_DIGIT_WORDS = {"không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "lăm": 5, "nhăm": 5,
                "sáu": 6, "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9}
_SCALES = {"nghìn": 1000, "ngàn": 1000, "triệu": 1_000_000, "tỷ": 1_000_000_000, "tỉ": 1_000_000_000}
# after digits only ("193 MILLION" on a chart is 193 000 000); never part of a Vietnamese spelled number
_SCALES_EN = {"thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_FILLERS = {"linh", "lẻ"}
_NUMBER_WORDS = set(_DIGIT_WORDS) | set(_SCALES) | _FILLERS | {"mươi", "mười", "trăm"}
# "km2" / "m2" are one word (square kilometres / metres), never a number 2 after "km"
_TOKEN = re.compile(r"(?<![^\W\d_])(?:km|m)2(?!\d)|\d+(?:[.,]\d+)*|[^\W\d_]+|%", re.UNICODE)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text.casefold())


def _spelled(tokens: list[str], start: int) -> tuple[int, int] | None:
    """The value of the Vietnamese number spelled from ``tokens[start]`` and the index after it, or None.

    Only a run with a multiplier (mười, mươi, trăm, nghìn, triệu, tỷ) is a number: "năm" alone is "year" and "hai
    người" names no amount the picture must show. "năm" is a digit only before a multiplier or after "linh/lẻ"
    ("một nghìn năm trăm" = 1 500, "hai mươi năm" = twenty years, "hai mươi lăm" = 25).
    """
    total = current = 0
    digit: int | None = None
    seen_multiplier = False
    i = start
    while i < len(tokens):
        word = tokens[i]
        following = tokens[i + 1] if i + 1 < len(tokens) else ""
        if word in _DIGIT_WORDS:
            if digit is not None:
                break
            if word == "năm" and following not in _NUMBER_WORDS - set(_DIGIT_WORDS) and not (i > start and tokens[i - 1] in _FILLERS):
                break
            if word in ("lăm", "nhăm", "mốt", "tư") and i == start:
                return None  # these forms only end a number
            digit = _DIGIT_WORDS[word]
        elif word == "mười":
            if digit is not None:
                break
            current += 10
            seen_multiplier = True
        elif word == "mươi":
            if digit is None:
                break
            current += digit * 10
            digit = None
            seen_multiplier = True
        elif word == "trăm":
            if digit is None:
                break
            current += digit * 100
            digit = None
            seen_multiplier = True
        elif word in _SCALES:
            if digit is None and current == 0:
                break
            total += (current + (digit or 0)) * _SCALES[word]
            current, digit = 0, None
            seen_multiplier = True
        elif word in _FILLERS:
            if not seen_multiplier:
                break
        else:
            break
        i += 1
    if not seen_multiplier:
        return None
    return total + current + (digit or 0), i


def _digits(token: str) -> int | None:
    """``1500``, ``1.500``, ``1,500`` and ``500,000`` as integers; a decimal ("1,5", "2.75") is not read."""
    if re.fullmatch(r"\d+", token):
        return int(token)
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        return int(re.sub(r"[.,]", "", token))
    return None


def _scaled(token: str, scale: int) -> int | None:
    """``token`` times a scale word after it: "193" million, and a decimal only before a scale ("1.5 million",
    "1,5 tỷ"), which names a whole amount."""
    whole = _digits(token)
    if whole is not None:
        return whole * scale
    found = re.fullmatch(r"(\d+)[.,](\d{1,2})", token)
    if not found:
        return None
    return int(found.group(1)) * scale + int(found.group(2)) * scale // 10 ** len(found.group(2))


# unit words after a number: (words, unit, base). Two units with one base (miles, square miles) are two statements
# of the same kind: one shown where the other is said contradicts it. A count of people, planes or tanks is not: a
# description saying "3 người lính" counts who is in the picture, it does not display a figure (_COUNTED).
_UNITS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("dặm", "vuông"), "sq_mile", "mile"), (("square", "miles"), "sq_mile", "mile"), (("square", "mile"), "sq_mile", "mile"),
    (("dặm",), "mile", "mile"), (("miles",), "mile", "mile"), (("mile",), "mile", "mile"),
    (("km", "vuông"), "sq_km", "km"), (("km2",), "sq_km", "km"), (("km²",), "sq_km", "km"), (("square", "kilometers"), "sq_km", "km"),
    (("ki", "lô", "mét", "vuông"), "sq_km", "km"), (("cây", "số"), "km", "km"), (("km",), "km", "km"),
    (("kilometers",), "km", "km"), (("kilometres",), "km", "km"), (("ki", "lô", "mét"), "km", "km"),
    (("mét", "vuông"), "sq_m", "m"), (("m2",), "sq_m", "m"), (("m²",), "sq_m", "m"),
    (("mét",), "m", "m"), (("meters",), "m", "m"), (("metres",), "m", "m"),
    (("phần", "trăm"), "%", "%"), (("%",), "%", "%"), (("percent",), "%", "%"),
    (("tấn",), "ton", "ton"), (("tons",), "ton", "ton"), (("kg",), "kg", "kg"),
    (("người",), "people", "people"), (("lính",), "people", "people"), (("binh", "sĩ"), "people", "people"),
    (("men",), "people", "people"), (("soldiers",), "people", "people"),
    (("people",), "people", "people"),
    (("máy", "bay"), "planes", "planes"), (("planes",), "planes", "planes"), (("aircraft",), "planes", "planes"),
    (("xe", "tăng"), "tanks", "tanks"), (("tanks",), "tanks", "tanks"),
    (("đồng",), "vnd", "money"), (("vnd",), "vnd", "money"), (("vnđ",), "vnd", "money"), (("đ",), "vnd", "money"),
)


_CLASSIFIERS = frozenset({"chiếc", "con", "cái", "đoàn", "tốp"})
_COUNTED = frozenset({"people", "planes", "tanks"})  # verified by the same count, never contradicted by another
PEOPLE = "people"
# what a figure with no unit counts, read from the words right next to it on the same line (#817). Kept small: the
# words a chart, a caption or a vision answer uses for a population. "người" / "people" after a figure are its unit.
MEASURE_WORDS: tuple[tuple[str, ...], ...] = (
    ("population",), ("people",), ("persons",), ("inhabitants",), ("residents",), ("citizens",),
    ("dân", "số"), ("cư", "dân"), ("người",), ("dân",))
_MEASURE_AFTER: tuple[tuple[str, ...], ...] = (
    ("persons",), ("inhabitants",), ("residents",), ("citizens",), ("cư", "dân"), ("dân",))
# what may stand between a measure word and its figure: "population of 193,000,000", "dân số khoảng 193 triệu",
# and a year ("dân số năm 1941 là …", read apart as a year)
_MEASURE_LINKS = frozenset("of is was about around approximately nearly almost over total là khoảng gần hơn chừng đạt tới lên".split())
MEASURE_GAP = 2  # link words at most between the measure word and the figure
# a figure followed by one of these is an age, a duration, an ordinal or a price, never a count of people
_NOT_A_COUNT = frozenset("tuổi năm tháng ngày giờ phút giây years year months days hours minutes seconds th st nd rd "
                         "usd dollars dollar euro euros đô".split())
_MONEY_MARK = "¤"  # where a currency word stood ("1500 đồng"): ``ev.numbers`` drops it, a count must not lose it
_CURRENCY = re.compile(r"(\d)\s*(?:vnđ|vnd|đồng|đ)(?![^\W\d_])", re.IGNORECASE)  # as ``ev.numbers`` reads it
_MARKS_BEFORE = frozenset("$€£¥#№")
_MARKS_AFTER = frozenset("$€£¥" + _MONEY_MARK)
_LINE = re.compile(r"[\n;]")  # one evidence item, one line of visible text: a measure word never crosses it
# "1 500", "193 000 000" (a space, a no-break or a thin space between groups of three) read as "1.500"
_SPACED = re.compile(r"(?<![\d.,])\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?![\d]|[.,]\d)")
# a number with no unit names a displayed fact only when it is distinctive: 1941, 1500. A day, a month, "3 mũi tên",
# "mười năm sau" are no fact a picture must show, and a small number in any description would "verify" them
DISTINCT_FROM = 100


@dataclass(frozen=True)
class Quantity:
    value: int
    unit: str | None = None
    base: str | None = None
    measure: str | None = None  # what a figure counts, from the words next to it ("POPULATION 193,000,000": people)

    def spoken(self) -> str:
        shown = f"{self.value:,}".replace(",", ".")
        names = {"mile": "dặm", "sq_mile": "dặm vuông", "km": "km", "sq_km": "km²", "m": "mét", "sq_m": "m²", "%": "%", "ton": "tấn",
                 "kg": "kg", "people": "người", "planes": "máy bay", "tanks": "xe tăng", "vnd": "đồng", "year": ""}
        if self.unit == "year":
            return f"năm {self.value}"
        return f"{shown} {names.get(self.unit or '', '')}".strip()


def quantities(text: str) -> list[Quantity]:
    """Every amount ``text`` states, digits or spelled, with the unit right after it when there is one."""
    return list(_quantities(text))


def _ends_with(tokens: list[str], lines: list[int], end: int, line: int, words: tuple[str, ...]) -> bool:
    start = end - len(words) + 1
    return start >= 0 and all(lines[k] == line for k in range(start, end + 1)) and tuple(tokens[start:end + 1]) == words


def _measure_before(tokens: list[str], lines: list[int], i: int) -> str | None:
    """People when a measure word stands right before ``tokens[i]`` on its line (a few link words or a year apart)."""
    j, gap = i - 1, 0
    while j >= 0 and lines[j] == lines[i]:
        if any(_ends_with(tokens, lines, j, lines[i], words) for words in MEASURE_WORDS):
            return PEOPLE
        if tokens[j] in _MEASURE_LINKS and gap < MEASURE_GAP:
            j, gap = j - 1, gap + 1
        elif re.fullmatch(r"\d{4}", tokens[j]) and j and lines[j - 1] == lines[i] and tokens[j - 1] in ("năm", "year", "in") \
                and gap < MEASURE_GAP:
            j, gap = j - 2, gap + 1  # "dân số năm 1941 là 193 triệu"
        else:
            return None
    return None


def _measure_after(tokens: list[str], lines: list[int], after: int, line: int) -> str | None:
    for words in _MEASURE_AFTER:
        if after + len(words) <= len(tokens) and tuple(tokens[after:after + len(words)]) == words \
                and all(lines[k] == line for k in range(after, after + len(words))):
            return PEOPLE
    return None


@functools.lru_cache(maxsize=8192)
def _quantities(text: str) -> tuple[Quantity, ...]:
    # one token stream over the whole text, as before; each token knows its line and whether a currency or an
    # ordinal sign stands next to it (the signs are no tokens, so units and values read exactly as before)
    tokens: list[str] = []
    lines: list[int] = []
    marked: list[bool] = []
    grouped = _SPACED.sub(lambda m: re.sub(r"[ \u00a0\u202f\u2009]", ".", m.group(0)), text)
    read = _nfc(ev.numbers(_CURRENCY.sub(lambda m: m.group(1) + " " + _MONEY_MARK, grouped)))
    for number, line in enumerate(_LINE.split(read)):
        for found in _TOKEN.finditer(line):
            tokens.append(found.group(0))
            lines.append(number)
            before, after_text = line[:found.start()].rstrip()[-1:], line[found.end():].lstrip()[:1]
            marked.append(bool(before and before in _MARKS_BEFORE) or bool(after_text and after_text in _MARKS_AFTER))
    out: list[Quantity] = []
    i = 0
    while i < len(tokens):
        value: int | None = None
        after = i + 1
        if tokens[i][0].isdigit():
            value = _digits(tokens[i])
            scale = _SCALES.get(tokens[after]) or _SCALES_EN.get(tokens[after]) if after < len(tokens) else None
            if scale and _scaled(tokens[i], scale) is not None:  # "2 tỷ" is two billion, "193 MILLION" 193 000 000
                value, after = _scaled(tokens[i], scale), after + 1
        else:
            read = _spelled(tokens, i)
            if read:
                value, after = read
        if value is None:
            i += 1
            continue
        unit = base = None
        if i and tokens[i - 1] in ("năm", "year", "in") and 1000 <= value <= 2100 and tokens[i][0].isdigit():
            out.append(Quantity(value, "year", "year"))
            i = after
            continue
        if after < len(tokens) and tokens[after] in _CLASSIFIERS:
            after += 1
        for words, name, family in _UNITS:
            if tuple(tokens[after:after + len(words)]) == words:
                unit, base = name, family
                after += len(words)
                break
        measure = None
        # what the figure counts: only a figure with no unit or a head count, with no price, ordinal or time on it
        if unit in (None, PEOPLE) and not marked[i] and not marked[after - 1] \
                and not (after < len(tokens) and tokens[after] in _NOT_A_COUNT and lines[after] == lines[i]):
            measure = _measure_before(tokens, lines, i) or (_measure_after(tokens, lines, after, lines[i]) if unit is None else None)
        out.append(Quantity(value, unit, base, measure))
        i = after
    return tuple(out)


# --- what a sentence says is displayed -----------------------------------------------------------------------------

# a sentence about something displayed: what it says must be seen, not merely the kind of picture it is
_DISPLAY = re.compile(r"\b(ban do|bieu do|do thi|dong chu|the chu|khung chu|man hinh chu|tieu de|con so|danh dau|hien thi|"
                      r"hien len|ghi chu|chu thich|bang chu|tam bien|bien bao|bien hieu|khau hieu|bang so|chu tren|chu lon|chu trang|chu den)\b")
# a phrase the sentence says is written: introduced by a word for text, or between quotes
_PHRASE_CUE = re.compile(r"(?i:dòng chữ|chữ|tiêu đề|tựa đề|tên phim|biển (?:hiệu|báo|tên|chữ)|tấm biển|bảng|thẻ|khẩu hiệu|chú thích)"
                         r"(?:\s+(?i:lớn|nhỏ|to|trắng|đen|đỏ|in hoa|tiếng anh|tiếng nga|tiếng đức|ghi|là))*"
                         r"\s+([A-Z][\w'’.-]*(?:\s+(?:[A-Z0-9][\w'’.-]*|of|the|and|in|on|for|to|a|an|de|la|le|du))*)")
_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{3,80})[\"”'’]")
# what a card or a screen is said to state
_WRITTEN = re.compile(r"(?:thẻ|tấm thẻ|dòng chữ|chữ|màn hình|bảng|biển (?:hiệu|báo|tên|chữ)|tấm biển|khung chữ|tiêu đề|chú thích)\b.{0,40}?\b(?:cho biết|ghi rằng|ghi là|"
                      r"viết rằng|nói rằng|thông báo|khẳng định|nêu rõ|ghi)\s+(.+)$", re.IGNORECASE)
_FUNCTION_EN = frozenset("of the and in on for to a an we our de la le du".split())
LABEL_MAX = 120  # characters of a claim as the log and the reviewer's note show it (the contract allows 200)


def _ascii_phrase(phrase: str) -> bool:
    return all(ord(c) < 128 for c in phrase)


@dataclass(frozen=True)
class Claims:
    quantities: tuple[Quantity, ...] = ()
    phrases: tuple[str, ...] = ()
    written: tuple[str, ...] = ()   # content words of what a card is said to state
    display: bool = False           # the sentence speaks of something displayed (a map, a card, a line of text)

    @property
    def any(self) -> bool:
        return bool(self.quantities or self.phrases or self.written)


@functools.lru_cache(maxsize=4096)
def of(text: str) -> Claims:
    """The claims of one sentence."""
    folded = " " + ev.fold(text).replace("chu thap", "") + " "  # "chữ thập ngoặc" (a swastika) is not text
    display = bool(_DISPLAY.search(folded))
    phrases: list[str] = []
    for found in _PHRASE_CUE.finditer(text):
        words = found.group(1).strip(" .,;:").split()
        while words and words[-1].casefold() in _FUNCTION_EN:
            words.pop()
        phrase = " ".join(words)
        # a foreign phrase of at least two words (a title, a slogan), never a Vietnamese word that happens to follow
        if _ascii_phrase(phrase) and len([w for w in words if w.casefold() not in _FUNCTION_EN]) >= 2:
            phrases.append(phrase[:LABEL_MAX])
    # a quote is written only in a sentence about something displayed ("khẩu hiệu “…” trên tường"); "Stalin nói:
    # “…”" quotes speech, which no picture must show
    if display:
        for found in _QUOTED.finditer(text):
            phrases.append(found.group(1).strip()[:LABEL_MAX])
    written: list[str] = []
    match = _WRITTEN.search(text)
    if match:
        clause = match.group(1)
        written = list(dict.fromkeys(_content_words(clause)))  # in the sentence's order: the note quotes them
    amounts = tuple(q for q in quantities(text) if q.unit is not None or q.value >= DISTINCT_FROM)
    return Claims(amounts, tuple(dict.fromkeys(phrases)), tuple(written), display or bool(phrases or written))


_STOP_WRITTEN = frozenset("và với của cho là không có một những các này thì mà nên sẽ đang được bị rất cũng lại khi nếu vì "
                          "để như hơn nhất mỗi nhiều bộ cái con chiếc tấm".split())


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]+", _nfc(text)) if len(w) > 1 and w not in _STOP_WRITTEN]


# --- checking a shot -----------------------------------------------------------------------------------------------

WRITTEN_SHARE = 650  # ‰ of the stated content's words the evidence must show


@dataclass
class Check:
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    # of the unverified, the counts the evidence ties another figure to (#817): for a person to look at, never a
    # conflict (no penalty, the shot stays acceptable: a misread digit must not move the choice off the right shot)
    disputed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        out = {"verified": list(self.verified), "unverified": list(self.unverified), "conflicts": list(self.conflicts)}
        if self.disputed:  # absent when empty: a log without a disputed count reads as before
            out["disputed"] = list(self.disputed)
        return out


VISUAL = frozenset({"vision", "ocr"})  # what was seen in the frame: the only proof of what it displays


def content_text(found: ev.Evidence | None) -> str:
    """What was *seen* in a shot (the vision answer, text read off the frame). Never the source's speech: a narrator
    saying "1500 miles" over a map does not put the number on it; never a clip-wide note or a file name."""
    if found is None:
        return ""
    return " \n ".join(item.text for item in found.items if item.text and ev.provenance(item.origin, item.detail) in VISUAL)


def check(claims: Claims, text: str) -> Check:
    """Which of ``claims`` the evidence ``text`` of one shot verifies, leaves unverified or contradicts."""
    out = Check()
    if not claims.any:
        return out
    shown = quantities(text)
    for wanted in claims.quantities:
        label = wanted.spoken()
        same_value = [q for q in shown if q.value == wanted.value]
        kind = _counts(wanted)
        if kind is not None:
            # a count is shown only by a figure tied to the same kind of thing: "1,500 PEOPLE", "POPULATION 1500";
            # a bare "1500", "1500 MILES", a year, a price or "No. 1500" are not
            if any(_tied(q, kind) for q in same_value):
                out.verified.append(label)
            else:
                out.unverified.append(label)
                if kind == PEOPLE and any(q.measure == PEOPLE and q.value != wanted.value and q.value >= DISTINCT_FROM for q in shown):
                    out.disputed.append(label)  # "POPULATION 93,000,000" for 193 000 000 người
            continue
        # an amount with a unit is shown only with that unit ("1500 MILES" for 1 500 dặm; a bare "1500" is not)
        # (a year is the number itself: "1941" on a chart shows the year 1941)
        if any(wanted.unit in (None, "year") or q.unit == wanted.unit for q in same_value):
            out.verified.append(label)
        elif wanted.base and wanted.unit not in _COUNTED and any(q.base == wanted.base for q in shown):
            out.conflicts.append(label)  # the picture states another amount of the same kind (500 000 dặm vuông)
        else:
            out.unverified.append(label)
    folded = _folded(text)
    for phrase in claims.phrases:
        words = [w for w in ev.fold(phrase).split() if w not in _FUNCTION_EN or len(ev.fold(phrase).split()) <= 2]
        if words and (f" {' '.join(ev.fold(phrase).split())} " in folded or _in_order(words, folded.split())):
            out.verified.append(phrase)
        else:
            out.unverified.append(phrase)
    if claims.written:
        have = _have(text)
        found = sum(1 for w in claims.written if w in have)
        label = " ".join(claims.written)[:LABEL_MAX]
        if found * 1000 >= WRITTEN_SHARE * len(claims.written):
            out.verified.append(label)
        else:
            out.unverified.append(label)
    return out


def _counts(q: Quantity) -> str | None:
    """What a stated figure counts, when it is a count: its unit (people, planes, tanks), or people for a figure with
    no unit after a population word ("biểu đồ dân số 1.500")."""
    if q.unit in _COUNTED:
        return q.unit
    if q.unit is None and q.measure == PEOPLE:
        return PEOPLE
    return None


def _tied(q: Quantity, kind: str) -> bool:
    """Whether the evidence figure ``q`` counts ``kind``: by its unit, or (people) by a population word next to it."""
    return q.unit == kind or (kind == PEOPLE and q.unit is None and q.measure == PEOPLE)


PHRASE_GAP = 2  # words that may come between two words of a title in the evidence ("THE BATTLE OF RUSSIA", a line break)


def _in_order(words: list[str], tokens: list[str]) -> bool:
    """Whether ``words`` appear in ``tokens`` in order, each at most PHRASE_GAP words after the one before: a title
    written on the frame, not "battle" and "Russia" mentioned apart."""
    for start, token in enumerate(tokens):
        if token != words[0]:
            continue
        at, ok = start, True
        for word in words[1:]:
            ahead = tokens[at + 1:at + 2 + PHRASE_GAP]
            if word not in ahead:
                ok = False
                break
            at = at + 1 + ahead.index(word)
        if ok:
            return True
    return False


@functools.lru_cache(maxsize=8192)
def _folded(text: str) -> str:
    return " " + " ".join(ev.fold(text).split()) + " "


@functools.lru_cache(maxsize=8192)
def _have(text: str) -> frozenset[str]:
    return frozenset(_content_words(text))


def merged(checks: Iterable[Check]) -> Check:
    """The claims a sentence's pieces show together: a claim any piece verifies is verified; a conflict counts when
    no piece verifies it."""
    checks = list(checks)
    verified = list(dict.fromkeys(v for c in checks for v in c.verified))
    conflicts = list(dict.fromkeys(v for c in checks for v in c.conflicts if v not in verified))
    unverified = list(dict.fromkeys(v for c in checks for v in c.unverified if v not in verified and v not in conflicts))
    disputed = list(dict.fromkeys(v for c in checks for v in c.disputed if v in unverified))
    return Check(verified, unverified, conflicts, disputed)


def note(result: Check) -> str:
    """The reviewer's note for a sentence whose pieces leave a claim unshown or contradicted ("" when none)."""
    if result.conflicts or result.disputed:
        shown = list(dict.fromkeys([*result.conflicts, *result.disputed]))
        return "cảnh hiển thị con số khác với lời câu (" + ", ".join(shown[:3]) + "): cần kiểm tra"
    if result.unverified:
        return "chưa thấy trong cảnh điều lời câu nói là hiển thị (" + ", ".join(result.unverified[:3]) + "): cần kiểm tra"
    return ""


def checks_for(claims: Claims, evidence: Mapping[str, ev.Evidence], ids: Iterable[str]) -> dict[str, Check]:
    return {cid: check(claims, content_text(evidence.get(cid))) for cid in ids}


# --- a claim's status, read from a decision log ---------------------------------------------------------------------

CONFIRMED = "CONFIRMED"            # the chosen shots show it
CONTRADICTED = "CONTRADICTED"      # a chosen shot shows another figure of the same kind (a conflict, or a disputed count)
UNVERIFIED = "UNVERIFIED"          # nothing the chosen shots were seen to show states it
NOT_APPLICABLE = "NOT_APPLICABLE"  # the sentence speaks of nothing displayed: its numbers are no fact a shot must show
STATUSES = (CONFIRMED, CONTRADICTED, UNVERIFIED, NOT_APPLICABLE)


def _labels(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def statuses(signals_claims: Mapping[str, object] | None) -> list[tuple[str, str]]:
    """Each claim of a unit's ``signals.claims`` with its status, the ones to settle first. Nothing (an older log,
    a pinned sentence, a sentence with no claim): no claim. A user's decision changes none of these: it settles the
    sentence, it does not show the claim."""
    if not isinstance(signals_claims, Mapping):
        return []
    verified, unverified = _labels(signals_claims.get("verified")), _labels(signals_claims.get("unverified"))
    against = set(_labels(signals_claims.get("conflicts"))) | set(_labels(signals_claims.get("disputed")))
    display = signals_claims.get("display") is True
    out = []
    for label in dict.fromkeys([*_labels(signals_claims.get("conflicts")), *_labels(signals_claims.get("disputed")),
                                *unverified, *verified]):
        if not display:
            out.append((label, NOT_APPLICABLE))
        elif label in against:  # a conflict wins over a piece that verifies it: the viewer sees the other figure too
            out.append((label, CONTRADICTED))
        elif label in unverified:
            out.append((label, UNVERIFIED))
        else:
            out.append((label, CONFIRMED))
    return out


def unresolved(signals_claims: Mapping[str, object] | None) -> list[str]:
    """The displayed claims the chosen shots contradict or do not show: what a person must settle."""
    return [label for label, status in statuses(signals_claims) if status in (CONTRADICTED, UNVERIFIED)]
