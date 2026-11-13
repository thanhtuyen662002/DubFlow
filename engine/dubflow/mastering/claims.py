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
_FILLERS = {"linh", "lẻ"}
_NUMBER_WORDS = set(_DIGIT_WORDS) | set(_SCALES) | _FILLERS | {"mươi", "mười", "trăm"}
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+|%", re.UNICODE)


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


# unit words after a number: (words, unit, base). Two units with one base (miles, square miles) are two statements
# of the same kind: one shown where the other is said contradicts it.
_UNITS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("dặm", "vuông"), "sq_mile", "mile"), (("square", "miles"), "sq_mile", "mile"), (("square", "mile"), "sq_mile", "mile"),
    (("dặm",), "mile", "mile"), (("miles",), "mile", "mile"), (("mile",), "mile", "mile"),
    (("km", "vuông"), "sq_km", "km"), (("km2",), "sq_km", "km"), (("square", "kilometers"), "sq_km", "km"),
    (("ki", "lô", "mét", "vuông"), "sq_km", "km"), (("cây", "số"), "km", "km"), (("km",), "km", "km"),
    (("kilometers",), "km", "km"), (("kilometres",), "km", "km"), (("ki", "lô", "mét"), "km", "km"),
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


@dataclass(frozen=True)
class Quantity:
    value: int
    unit: str | None = None
    base: str | None = None

    def spoken(self) -> str:
        shown = f"{self.value:,}".replace(",", ".")
        names = {"mile": "dặm", "sq_mile": "dặm vuông", "km": "km", "sq_km": "km²", "m": "mét", "%": "%", "ton": "tấn",
                 "kg": "kg", "people": "người", "planes": "máy bay", "tanks": "xe tăng", "vnd": "đồng", "year": ""}
        if self.unit == "year":
            return f"năm {self.value}"
        return f"{shown} {names.get(self.unit or '', '')}".strip()


def quantities(text: str) -> list[Quantity]:
    """Every amount ``text`` states, digits or spelled, with the unit right after it when there is one."""
    return list(_quantities(text))


@functools.lru_cache(maxsize=8192)
def _quantities(text: str) -> tuple[Quantity, ...]:
    tokens = _TOKEN.findall(_nfc(ev.numbers(text)))
    out: list[Quantity] = []
    i = 0
    while i < len(tokens):
        value: int | None = None
        after = i + 1
        if tokens[i][0].isdigit():
            value = _digits(tokens[i])
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
        out.append(Quantity(value, unit, base))
        i = after
    return tuple(out)


# --- what a sentence says is displayed -----------------------------------------------------------------------------

# a sentence about something displayed: what it says must be seen, not merely the kind of picture it is
_DISPLAY = re.compile(r"\b(ban do|bieu do|do thi|dong chu|the chu|khung chu|man hinh chu|tieu de|con so|danh dau|hien thi|"
                      r"hien len|ghi chu|chu thich|bang chu|tam bien|bien bao|bang so|chu tren|chu lon|chu trang|chu den)\b")
# a phrase the sentence says is written: introduced by a word for text, or between quotes
_PHRASE_CUE = re.compile(r"(?i:dòng chữ|chữ|tiêu đề|tựa đề|tên phim|biển|bảng|thẻ|khẩu hiệu|chú thích)"
                         r"(?:\s+(?i:lớn|nhỏ|to|trắng|đen|đỏ|in hoa|tiếng anh|tiếng nga|tiếng đức|ghi|là))*"
                         r"\s+([A-Z][\w'’.-]*(?:\s+(?:[A-Z0-9][\w'’.-]*|of|the|and|in|on|for|to|a|an|de|la|le|du))*)")
_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{3,80})[\"”'’]")
# what a card or a screen is said to state
_WRITTEN = re.compile(r"(?:thẻ|tấm thẻ|dòng chữ|chữ|màn hình|bảng|biển|khung chữ|tiêu đề|chú thích)\b.{0,40}?\b(?:cho biết|ghi rằng|ghi là|"
                      r"viết rằng|nói rằng|thông báo|khẳng định|nêu rõ|ghi)\s+(.+)$", re.IGNORECASE)
_FUNCTION_EN = frozenset("of the and in on for to a an we our de la le du".split())


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
            phrases.append(phrase)
    for found in _QUOTED.finditer(text):
        phrases.append(found.group(1).strip())
    written: list[str] = []
    match = _WRITTEN.search(text)
    if match:
        clause = match.group(1)
        written = sorted({w for w in _content_words(clause)})
    return Claims(tuple(quantities(text)), tuple(dict.fromkeys(phrases)), tuple(written), display or bool(phrases or written))


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

    def to_dict(self) -> dict[str, list[str]]:
        return {"verified": list(self.verified), "unverified": list(self.unverified), "conflicts": list(self.conflicts)}


def content_text(found: ev.Evidence | None) -> str:
    """What the content evidence of a shot says (vision, transcript, the user's notes; never a file name)."""
    if found is None:
        return ""
    return " \n ".join(item.text for item in found.items if item.origin in ev.CONTENT_ORIGINS and item.text)


def check(claims: Claims, text: str) -> Check:
    """Which of ``claims`` the evidence ``text`` of one shot verifies, leaves unverified or contradicts."""
    out = Check()
    if not claims.any:
        return out
    shown = quantities(text)
    for wanted in claims.quantities:
        label = wanted.spoken()
        same_value = [q for q in shown if q.value == wanted.value]
        if any(wanted.unit is None or q.unit is None or q.unit == wanted.unit for q in same_value):
            out.verified.append(label)
        elif wanted.base and any(q.base == wanted.base for q in shown):
            out.conflicts.append(label)  # the picture states another amount of the same kind (500 000 dặm vuông)
        else:
            out.unverified.append(label)
    folded_words = ev.fold(text).split()
    folded = " " + " ".join(folded_words) + " "
    for phrase in claims.phrases:
        words = [w for w in ev.fold(phrase).split() if w not in _FUNCTION_EN or len(ev.fold(phrase).split()) <= 2]
        if words and (f" {' '.join(ev.fold(phrase).split())} " in folded or all(f" {w} " in folded for w in words)):
            out.verified.append(phrase)
        else:
            out.unverified.append(phrase)
    if claims.written:
        have = set(_content_words(text))
        found = sum(1 for w in claims.written if w in have)
        label = " ".join(claims.written)
        if found * 1000 >= WRITTEN_SHARE * len(claims.written):
            out.verified.append(label)
        else:
            out.unverified.append(label)
    return out


def merged(checks: Iterable[Check]) -> Check:
    """The claims a sentence's pieces show together: a claim any piece verifies is verified; a conflict counts when
    no piece verifies it."""
    checks = list(checks)
    verified = list(dict.fromkeys(v for c in checks for v in c.verified))
    conflicts = list(dict.fromkeys(v for c in checks for v in c.conflicts if v not in verified))
    unverified = list(dict.fromkeys(v for c in checks for v in c.unverified if v not in verified and v not in conflicts))
    return Check(verified, unverified, conflicts)


def note(result: Check) -> str:
    """The reviewer's note for a sentence whose pieces leave a claim unshown or contradicted ("" when none)."""
    if result.conflicts:
        return "cảnh hiển thị con số khác với lời câu (" + ", ".join(result.conflicts[:3]) + "): cần kiểm tra"
    if result.unverified:
        return "chưa thấy trong cảnh điều lời câu nói là hiển thị (" + ", ".join(result.unverified[:3]) + "): cần kiểm tra"
    return ""


def checks_for(claims: Claims, evidence: Mapping[str, ev.Evidence], ids: Iterable[str]) -> dict[str, Check]:
    return {cid: check(claims, content_text(evidence.get(cid))) for cid in ids}
