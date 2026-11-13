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
figure tied to people ("POPULATION 93,000,000" or "93,000,000 PEOPLE" for 193 000 000 người) is *disputed*: a reason
for a person to look, never a hard conflict, because a misread digit must not move the planner off the right shot. A
bare year beside the population word ("Population (1941): 193,000,000", "dân số 193 triệu năm 1941") is the year of
the figure, never a figure of its own.

The tie is read narrowly on the evidence, the same way for people, planes and tanks:

* a line ends at every line break, at ";" and at a tab (a table cell); nothing is read across it;
* a counted unit is the figure's only right after it, spaces or a footnote mark between ("1.500 người", "193,000,000*
  people"; not "1941, người dân…", "1500 - người bán hàng"), and never when the figure carries a currency sign
  ("$1500", "₽1500", "1.500đ/người"), is joined to another figure ("22/6/1941", "22-6-1941", "1941-1945", "07:55",
  "1:1500"), is glued to a code ("T-34", "Q1", "5h30"), is the bare year of a date ("JUNE 22, 1941", "tháng Sáu
  1941", "Mùa hè 1941", "Since 1941"), or follows a word that numbers or prices it ("Lô số 1500", "UNIT 731", "Điều
  58", "Giá vé 1500", "USD 1500"). A figure written with a thousands separator ("1.500", "1,500", "1 500") is a
  count, never a year ("Tháng 6 1.500 người"), as is a figure after a colon ("June 22: 1500 people");
* a unit that heads a density over an area is no head count ("1500 người/km2", "persons to the square mile"); a
  count per day, per month or per side is that count. A unit that only starts another noun is none ("1500 aircraft
  carriers", "1500 người máy", "1500 MEN'S COATS");
* a population word before a unit-less figure ties it only in the same clause ("Biểu đồ về dân số. 1500" ties
  nothing; a colon right after the word or its year is a caption: "Population (1941): 193,000,000"; four or more
  leader dots lead to the figure, "..." is an ellipsis), only when the word names the head count itself (not "Mật độ dân số", "GDP đầu người", "GROWTH
  OF POPULATION", "nghìn người", "nhân dân số 1500", "10.000 dân: 15"), and only when what follows the figure is on a
  closed list: nothing, a sentence end, a closing quote, a people word, a year (with its link word), the next field
  of the caption ("AREA", "Diện tích", "(EST.)") or a word for where the caption stands; "and" and a year are passed
  over and the list applies to what stands after them. Any other word is the figure's unit or noun ("POPULATION 1500
  FEET", "dân số 1500 hộ", "POPULATION 1500 AND FEET"). Two bare years side by side are an axis or a range
  ("POPULATION 1500-2000", "POPULATION 1500 1600 1700"), no count;
* groups of three joined by a space are one figure only where an amount is expected (the start of a line, after a
  sign that opens a value, a population or a link word, a few verbs): "Hình 1 500 người", "Đợt 1 500 máy bay",
  "JUNE 22 300 SOLDIERS" are a number and a count.

A sentence is read more widely than the evidence (``Quantity.near``, ``Quantity.wide``): a narrated "dân số 1500 tăng
nhanh", "1.500 người/km2" or "Top 1500 người" still states that count, so a bare "1500" or "1500 MILES" on the frame
does not show it. A narrated price, date or model ("1.500 đồng/người", "22/6/1941: người Đức…", "T-34 xe tăng")
states no count. Left as they were: a bound, a negation or the end of a spaced range ("dưới 1500 người", "không phải
1500 người") still show the figure tied to people.

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
_TOKEN = re.compile("(?<![^\\W\\d_])(?:km|m)2(?!\\d)|\\d+(?:[.,]\\d+)*\u2060?|[^\\W\\d_]+|%", re.UNICODE)


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
# a figure followed by one of these (a space apart, nothing between) is an age, a duration, an ordinal or a price,
# never a count of people. "năm" / "year" followed by a year is the year the figure is for ("193 triệu năm 1941")
_NOT_A_COUNT = frozenset("tuổi năm tháng ngày giờ phút giây years year months days hours minutes seconds th st nd rd "
                         "usd dollars dollar euro euros đô".split())
_YEAR_WORDS = frozenset({"năm", "year"})
# a bare figure in these bounds may be a year ("POPULATION 1941", "Dân số (1941): …"): it never disputes a count, and
# it may stand between a measure word and the figure it is for
YEAR_FROM, YEAR_TO = 1000, 2100
_MONEY_MARK = "¤"  # where a currency word stood ("1500 đồng"): ``ev.numbers`` drops it, a count must not lose it
_CURRENCY = re.compile(r"(\d)\s*(?:vnđ|vnd|đồng|đ)(?![^\W\d_])", re.IGNORECASE)  # as ``ev.numbers`` reads it
_ORDINAL_SIGNS = frozenset("#№")  # before a figure; any currency sign (Unicode Sc) marks it on either side
# a figure written with a thousands separator ("1.500", "1,500", "1 500") is a count, never a year: the mark stands
# after it once ``ev.numbers`` took the separator out. Not before a scale word ("1.500 nghìn" reads as before)
_COUNT_MARK = "⁠"
_SEPARATED = re.compile(r"(?<![\d.,])([12])[.,](\d{3})(?![\d.,]*\d)(?!\s*(?:k|nghìn|ngàn|nghin|ngan|tr|triệu|trieu)(?![^\W\d_]))",
                        re.IGNORECASE)
# one evidence item, one line of visible text, one cell of a table (a tab): a word never crosses it
_LINE = re.compile("[\n\r\x0b\x0c\x1c\x1d\x1e\x85  \t;]")
_SPACES = frozenset("    ")  # between a figure and its unit; anything else (a comma, a dash) parts them
_FOOTNOTE = frozenset("*+†")  # "193,000,000* people", "1,500+ men"
_SENTENCE_END = frozenset(".!?…")  # between a population word and a figure: another sentence, no tie
# "POPULATION ....... 193,000,000", "POPULATION . . . . 193,000,000": dots that lead to the figure. Three dots are an
# ellipsis, a sentence that trails off, as "…" is
_LEADER = re.compile(r"\.{4,}|(?:\.\s){3,}\.?")
_CLOSING_QUOTE = frozenset("'\"”’»")  # "… the text 'POPULATION 193,000,000' appears over a map"
_JOINS = frozenset({"-", "–", ".", ":"})  # nothing else between two figures: a date, a range, a time, a ratio
_RUN = frozenset({"", ",", "-", "–", "—"})  # between the years of an axis: "1500 1600", "1500, 1600", "1500 - 1900"
# what may follow a figure a population word ties to people, besides nothing, a sentence end, a year or a people
# word (a closed list: any other word reads as the figure's unit or noun, "POPULATION 1500 FEET", "dân số 1500 hộ")
_YEAR_LINKS = frozenset("in năm year vào by trong as".split())  # only before a year: "in 1941", "vào năm 1941", "as of 1941"
_AND = frozenset({"và", "and"})  # "DÂN SỐ 1500 VÀ DIỆN TÍCH …": what stands after it is read by the same list
# … the name of the next field of a caption ("POPULATION 193,000,000 AREA 8,500,000 SQ MI", "(EST.)") …
_FIELD_AFTER = frozenset("area diện elevation altitude est estimated estimate ước census capital thủ".split())
# … and where a vision answer says the caption stands ("POPULATION 25,000 visible on the right", "dân số 25.000 bên đường")
_SEEN_AFTER = frozenset("over below above visible shown appears appearing displayed written hiện xuất bên phía dưới ở".split())
# after a counted unit: the head of a density ("1500 people per sq mi", "người/km2", "persons to the square mile"), no
# head count. A count per day, per month or per side is still that count ("1.500 người mỗi ngày")
_PER = frozenset("per mỗi trên a an each every một to".split())
_PER_FILL = frozenset("the mỗi each every một".split())
_AREA = frozenset("km km2 km² sq square mi mile miles dặm ha hecta hectare hectares m2 m² acre acres kilometer kilometers "
                  "kilometre kilometres cây mét meter meters".split())
# a counted word that is only the first part of another noun: "1500 aircraft carriers", "1500 người máy", "1500 TANKS OF OIL"
_TAILS = {"people": frozenset("máy chì".split()),
          "planes": frozenset("workers worker carriers carrier engines engine factory factories parts giấy of".split()),
          "tanks": frozenset("of đồ".split())}
_MONTHS = frozenset("january february march april may june july august september october november december "
                    "jan feb mar apr jun jul aug sep sept oct nov dec".split())
_MONTHS_VI = frozenset("một giêng hai ba tư bốn năm sáu bảy bẩy tám chín mười chạp".split())  # after "tháng"
_SEASONS_VI = frozenset("xuân hè hạ thu đông".split())  # after "mùa"
# right before a bare year: "Since 1941 people have suffered", "Sau 1945 người dân trở về", "SUMMER 1941 PEOPLE OF RUSSIA"
_BEFORE_YEAR = frozenset("since until till before after during spring summer autumn fall winter early late mid "
                         "từ đến trước sau vào hồi".split())
# a word that numbers the figure after it (a lot, a page, a rank, a unit, an article, a room, a phone): "Lô số 1500
# người bán hàng", "UNIT 731 SOLDIERS", "Điều 58 người dân có quyền" count no one
_ORDINAL_BEFORE = frozenset("no nr number lô mã thứ hạng top trang chương tập câu bài mục page chapter rank "
                            "division regiment battalion brigade corps unit century điều phòng room area khu km tr p pp vol "
                            "id code stt hotline đt tel phone fax tuổi lúc type model mark mk kiểu".split())
_ORDINAL_PAIRS = frozenset({("sư", "đoàn"), ("trung", "đoàn"), ("tiểu", "đoàn"), ("quân", "đoàn"), ("lữ", "đoàn"), ("đơn", "vị"),
                            ("thế", "kỷ"), ("thế", "kỉ"), ("chỉ", "thị"), ("nghị", "định"), ("quyết", "định"), ("thông", "tư"),
                            ("điện", "thoại")})
# … a price word or a currency code before it: "Giá vé 1500 người lớn", "USD 1500 people"
_PRICE_BEFORE = frozenset("giá vé phí thuế price cost fare fee usd vnd vnđ eur gbp rm rp đô rúp".split())
# … and "số", unless it ends a word for an amount: "dân số 1500", "tổng số 1500 người" (never "nhân dân số 1500")
_AMOUNT_BEFORE_SO = frozenset("dân tổng quân con sĩ".split())
_NOT_PEOPLE_BEFORE_DAN = frozenset({"nhân", "công"})  # "nhân dân", "công dân": an institution or a document, no population
# a population word that names a rate, an attribute or a scale, not the head count: "Mật độ dân số 1500", "GROWTH OF
# POPULATION: 1500", "Tuổi thọ người dân: 73" (within three words before it) …
_ATTRIBUTE_BEFORE = (" mật độ ", " density ", " growth ", " tăng ", " giảm ", " thu nhập ", " income ", " gdp ", " gnp ", " giá ",
                     " cost ", " price ", " tuổi thọ ", " expectancy ", " bình quân ", " trung bình ", " average ", " tỷ lệ ",
                     " tỉ lệ ", " rate ", " ratio ", " percent ", " % ", " phần trăm ", " lương ", " wage ", " thuế ", " tax ",
                     " increase ", " decrease ", " đơn vị ")
# … "Giá mỗi người 1500", "GDP đầu người 1500", "POPULATION (THOUSAND PEOPLE): 1500" (the word right before it)
_PER_BEFORE = frozenset("per mỗi đầu trên each every".split()) | frozenset(_SCALES) | frozenset(_SCALES_EN) \
    | frozenset({"thousands", "millions", "billions"})
# "1 500", "193 000 000" (a space, a no-break or a thin space between groups of three) read as "1.500" only where an
# amount is expected: at the start of a line, after punctuation that opens a value, after a population or a link
# word, or after one of a few verbs. After any other word the first group is its number ("Hình 1 500 người", "Đợt 1
# 500 máy bay", "JUNE 22 300 SOLDIERS"), as after a code or a date ("Q1 500", "22-6 300 lính")
_SPACED = re.compile(r"(?<![\d.,])\d{1,3}(?:[    ]\d{3})+(?![\d]|[.,]\d)")
_GROUPED_AFTER_SIGN = frozenset(":([{\"'“‘,;|•=«")
_GROUPED_AFTER = frozenset(w[-1] for w in MEASURE_WORDS) | _MEASURE_LINKS | frozenset(
    "ghi có gồm với và còn mất tổng cộng and with has have had lost".split())
_WORDS_BEFORE = re.compile(r"(?:([^\W\d_]+)\W+)?([^\W\d_]+)$")  # the last two words before a figure
# a number with no unit names a displayed fact only when it is distinctive: 1941, 1500. A day, a month, "3 mũi tên",
# "mười năm sau" are no fact a picture must show, and a small number in any description would "verify" them
DISTINCT_FROM = 100


@dataclass(frozen=True)
class Quantity:
    value: int
    unit: str | None = None
    base: str | None = None
    measure: str | None = None  # what a figure counts, from the words next to it ("POPULATION 193,000,000": people)
    # a population word before a unit-less figure, whatever follows it ("dân số 1500 tăng nhanh"): what a *sentence*
    # states is read widely, what the evidence shows (``measure``) narrowly. Not part of a figure's identity
    near: str | None = field(default=None, compare=False)
    # the counted unit right after the figure where the narrow reading does not take it for a head count ("1.500 người
    # /km2", "Top 1500 người", "tháng 6 1500 người"): a *sentence* still states that count, so a bare "1500" or "1500
    # MILES" on the frame does not show it. Not part of a figure's identity
    wide: str | None = field(default=None, compare=False)

    def spoken(self) -> str:
        shown = f"{self.value:,}".replace(",", ".")
        names = {"mile": "dặm", "sq_mile": "dặm vuông", "km": "km", "sq_km": "km²", "m": "mét", "sq_m": "m²", "%": "%", "ton": "tấn",
                 "kg": "kg", "people": "người", "planes": "máy bay", "tanks": "xe tăng", "vnd": "đồng", "year": ""}
        if self.unit == "year":
            return f"năm {self.value}"
        return f"{shown} {names.get(self.unit or self.wide or '', '')}".strip()


def quantities(text: str) -> list[Quantity]:
    """Every amount ``text`` states, digits or spelled, with the unit right after it when there is one."""
    return list(_quantities(text))


@dataclass
class _Read:
    """The tokens of a text, each with its line and what stands around it."""
    words: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    seps: list[str] = field(default_factory=list)  # between this token and the one before it on its line ("" for a line's first)
    spaced: list[bool] = field(default_factory=list)  # only spaces there ("1500 tuổi", not "1500, năm", "1500 - người")
    marked: list[bool] = field(default_factory=list)  # a currency or an ordinal sign next to it ("$1500", "1500₫", "#1500")
    separated: list[bool] = field(default_factory=list)  # written with a thousands separator: a count, never a year

    def word(self, k: int, line: int) -> str:
        """The token at ``k`` when it is on ``line``, else ""."""
        return self.words[k] if 0 <= k < len(self.words) and self.lines[k] == line else ""

    def year(self, k: int, line: int) -> bool:
        """A bare four-digit figure that may be a year ("1941"; "1.500" and "1,941" are counts)."""
        word = self.word(k, line)
        return bool(re.fullmatch(r"\d{4}", word)) and YEAR_FROM <= int(word) <= YEAR_TO and not self.separated[k]

    def phrase(self, end: int, line: int, words: tuple[str, ...]) -> bool:
        """Whether ``words`` end at token ``end`` on ``line``, only spaces between them ("dân số"; not "nhân dân - Số")."""
        start = end - len(words) + 1
        return start >= 0 and tuple(self.words[start:end + 1]) == words and self.lines[start] == line == self.lines[end] \
            and all(self.spaced[k] for k in range(start + 1, end + 1))

    def starts(self, at: int, line: int, words: tuple[str, ...]) -> bool:
        end = at + len(words) - 1
        return end < len(self.words) and self.phrase(end, line, words)


def _numbers_it(before: str, word: str, earlier: str = "") -> bool:
    """Whether ``word`` (after ``before``, after ``earlier``) numbers the figure after it: an ordinal, a label or a
    price, no amount."""
    if word == "số":
        return before not in _AMOUNT_BEFORE_SO or (before == "dân" and earlier in _NOT_PEOPLE_BEFORE_DAN)
    return word in _ORDINAL_BEFORE or word in _PRICE_BEFORE or (before, word) in _ORDINAL_PAIRS


def _measure_before(r: _Read, i: int) -> str | None:
    """People when a measure word stands right before ``r.words[i]`` in its clause (a few link words or a year apart:
    "population of 193,000,000", "Population (1941): 193,000,000"). A sentence end between them ties nothing; a
    colon is a caption's only right after the measure word or its year. The word must name the head count itself:
    not a density, an income per head, a growth, a scale ("nghìn người"), another figure's unit ("10.000 dân: 15") or
    part of "nhân dân" / "công dân"."""
    line = r.lines[i]
    if r.year(i, line) and r.year(i - 1, line) and r.seps[i].strip() in _RUN:
        return None  # the ticks of a year axis: "YEAR POPULATION 1500 1600 1700"
    j, gap, following = i - 1, 0, i
    while j >= 0 and r.lines[j] == line:
        between = _LEADER.sub(" ", r.seps[following])
        if _SENTENCE_END & set(between):
            return None
        for words in MEASURE_WORDS:
            if r.phrase(j, line, words):
                return PEOPLE if _head_count(r, j - len(words) + 1, line) else None
        if ":" in between and not r.year(j, line):
            return None  # "dân số là: …", "population of: …"
        if re.fullmatch(r"\d{4}", r.words[j]) and r.word(j - 1, line) in ("năm", "year", "in") \
                and gap < MEASURE_GAP and not _SENTENCE_END & set(r.seps[j]):
            j, gap, following = j - 2, gap + 1, j - 1  # "dân số năm 1941 là 193 triệu"
        elif (r.words[j] in _MEASURE_LINKS or r.year(j, line)) and gap < MEASURE_GAP:
            j, gap, following = j - 1, gap + 1, j  # "population of …", "Population (1941): …"
        else:
            return None
    return None


def _head_count(r: _Read, start: int, line: int) -> bool:
    """Whether the population word starting at ``r.words[start]`` names a head count, by what stands before it."""
    while r.spaced[start] and (r.word(start - 1, line),) in MEASURE_WORDS:
        start -= 1  # "người dân", "people citizens": one name
    before = r.word(start - 1, line) if r.spaced[start] else ""
    if r.words[start] == "dân" and before in _NOT_PEOPLE_BEFORE_DAN:
        return False  # "Ủy ban nhân dân số 1500", "Căn cước công dân số 1500"
    if before and (before[0].isdigit() or before in _PER_BEFORE):
        return False  # "10.000 dân: 15", "Giá mỗi người 1500", "Dân số (nghìn người): 1500"
    window, k = [], start
    while len(window) < 3 and r.spaced[k] and r.word(k - 1, line):
        k -= 1
        window.append(r.words[k])
    shown = " " + " ".join(reversed(window)) + " "
    return not any(phrase in shown for phrase in _ATTRIBUTE_BEFORE)


def _density(r: _Read, after: int, line: int) -> bool:
    """Whether a counted unit ending before ``r.words[after]`` is the head of a density: "người/km2", "people per sq
    mi", "persons to the square mile", "người trên 1 km2"."""
    word = r.word(after, line)
    if not word:
        return False
    if r.seps[after].strip() == "/":
        at = after
    elif r.spaced[after] and word in _PER:
        at = after + 1
    else:
        return False
    for _ in range(2):  # "to the square mile", "trên mỗi km vuông", "trên 1 km2"
        if r.word(at, line) in _PER_FILL or r.word(at, line)[:1].isdigit():
            at += 1
    return r.word(at, line) in _AREA


def _compound(r: _Read, i: int, unit: str, after: int, line: int) -> bool:
    """Whether the counted word ending before ``r.words[after]`` is the first part of another noun: "1500 MEN'S
    COATS", "1500 men-hours", "1500 aircraft carriers", "1500 người máy", "1941 PEOPLE OF RUSSIA"."""
    word = r.word(after, line)
    if not word:
        return False
    if r.seps[after][:1] in ("'", "’") or r.seps[after] == "-":
        return True
    return r.spaced[after] and (word in _TAILS[unit] or (word == "of" and r.year(i, line)))


def _measure_after(r: _Read, i: int, after: int, line: int) -> str | None:
    for words in _MEASURE_AFTER:
        end = after + len(words)
        if r.starts(after, line, words) and _follows(r, after, line) and not _density(r, end, line) \
                and not _compound(r, i, PEOPLE, end, line):
            return PEOPLE
    return None


def _people_word(r: _Read, at: int, line: int) -> bool:
    """A people word at ``r.words[at]`` that heads no density ("1500 (people)"; not "1500 (people per sq mi)")."""
    return any(r.starts(at, line, words) and not _density(r, at + len(words), line)
               for words in (*_MEASURE_AFTER, *(w for w, unit, _ in _UNITS if unit == PEOPLE)))


def _may_follow(r: _Read, i: int, after: int, line: int) -> bool:
    """Whether what follows a figure (from ``r.words[after]``) leaves it a count of the people named before it:
    nothing, a sentence end, a closing quote, a people word, a year (with its link word), the next field of the
    caption, or a word for where the caption stands. "and" and a year are passed over and what stands after them is
    read by the same list. Any other word is the figure's unit or noun."""
    at = after
    while r.word(at, line):
        sep, word = r.seps[at], r.words[at]
        if _SENTENCE_END & set(sep) or sep[:1] in _CLOSING_QUOTE:
            return True
        if _people_word(r, at, line) or word in _FIELD_AFTER or word in _SEEN_AFTER:
            return True
        if r.year(at, line):
            if at == after and r.year(i, line) and sep.strip() in _RUN:
                return False  # a year axis or a range of years: "POPULATION 1500-2000", "POPULATION 1500, 1600, 1700"
            at += 1
        elif word in _AND:
            at += 1
        elif word in _YEAR_LINKS:  # "in 1941", "năm 1941", "vào năm 1941", "trong năm 1941", "as of 1941"
            year = at + 2 if (word in ("vào", "trong") and r.word(at + 1, line) == "năm") or (word, r.word(at + 1, line)) == ("as", "of") \
                else at + 1
            if word in ("trong", "as") and year == at + 1 or not r.year(year, line):
                return False
            at = year + 1
        else:
            return False
    return True


def _coded(r: _Read, i: int) -> bool:
    """Whether the figure at ``r.words[i]`` is glued to letters before it: a model, a code, a time ("T-34", "KV-1",
    "Q1", "x1500", "Rp1500", "5h30")."""
    return i > 0 and r.lines[i - 1] == r.lines[i] and r.seps[i] in ("", "-", "–") and r.words[i][0].isdigit() \
        and not r.words[i - 1][0].isdigit() and r.words[i - 1] != "%"


def _joined(r: _Read, i: int, after: int) -> bool:
    """Whether the figure at ``r.words[i]`` is joined to another figure: "22/6/1941", "22-6-1941", "1941-1945",
    "07:55", "1:1500", "1 bác sĩ / 1500 người"."""
    line = r.lines[i]
    if r.word(i - 1, line) and (r.seps[i].strip() == "/" or (r.seps[i] in _JOINS and r.words[i - 1][0].isdigit())):
        return True  # after a slash the figure divides what stands before it
    return r.word(after, line)[:1].isdigit() and (r.seps[after].strip() == "/" or r.seps[after] in _JOINS)


def _year_of_a_date(r: _Read, i: int) -> bool:
    """Whether the bare year-like figure at ``r.words[i]`` is the year of a date: "JUNE 22, 1941", "tháng 6 1941",
    "tháng Sáu 1941", "Mùa hè 1941", "Since 1941". A figure with a thousands separator, or after a colon ("June 22:
    1500 people"), is the caption's value."""
    line = r.lines[i]
    if not r.year(i, line):
        return False
    one, two, three = r.word(i - 1, line), r.word(i - 2, line), r.word(i - 3, line)
    loose = r.seps[i].strip() in ("", ",")  # only spaces or a comma before the year
    if not loose:
        return False
    if one in _MONTHS:
        return True  # "June 1941", "22 June 1941"
    if r.spaced[i] and ((one in _BEFORE_YEAR and (one, two) != ("đến", "lên")) or (one in _SEASONS_VI and two == "mùa")):
        return True  # "Sau 1945 người dân trở về"; "lên đến 1500 người" is an amount
    if two == "tháng" and one in _MONTHS_VI or three == "tháng" and two == "mười" and one in ("một", "hai"):
        return True  # "tháng Sáu 1941", "tháng mười hai 1941"
    # "June 22, 1941", "tháng 6 1941" ("sau 3 tháng 1500 người…" is no date)
    return (two in _MONTHS or two == "tháng") and bool(re.fullmatch(r"\d{1,2}", one)) and r.spaced[i - 1]


def _grouped(match: re.Match[str]) -> str:
    """"1 500" as "1.500" where an amount is expected: at the start of a line, after a sign that opens a value, after
    a population word, a link word or a verb of amount. Never after another word, a code or a date ("Hình 1 500
    người", "ngày 15 300 lính", "Q1 500", "22-6 300 lính")."""
    head = match.string[:match.start()]
    before = head.rstrip("".join(_SPACES))
    last = before[-1:]
    joins = not last or bool(_LINE.fullmatch(last)) or last in _GROUPED_AFTER_SIGN
    if not joins and last.isalpha() and before != head:
        words = _WORDS_BEFORE.search(before)
        if words:
            earlier, word = _nfc(words.group(1) or ""), _nfc(words.group(2))
            joins = earlier in _AMOUNT_BEFORE_SO if word == "số" else word in _GROUPED_AFTER
    return re.sub(r"[    ]", ".", match.group(0)) if joins else match.group(0)


def _not_a_count(r: _Read, after: int) -> bool:
    """Whether the word right after a figure (``r.words[after]``) makes it an age, a duration, an ordinal or a price."""
    if after >= len(r.words) or r.lines[after] != r.lines[after - 1] or not r.spaced[after] or r.words[after] not in _NOT_A_COUNT:
        return False
    # "193 triệu năm 1941" is the population in 1941, "1500 năm" fifteen hundred years
    return not (r.words[after] in _YEAR_WORDS and r.year(after + 1, r.lines[after]))


def _follows(r: _Read, start: int, line: int) -> bool:
    """Whether the word at ``r.words[start]`` stands right after its figure: the same line, only spaces or a footnote
    mark between ("1.500 người", "193,000,000* people"; not "1500. Người…", "1500 đồng/người", "1500 - người")."""
    return r.word(start, line) != "" and (r.spaced[start] or (r.seps[start].strip() != "" and set(r.seps[start].strip()) <= _FOOTNOTE
                                                              and set(r.seps[start]) <= _FOOTNOTE | _SPACES))


def _tokens(text: str) -> _Read:
    r = _Read()
    grouped = _SPACED.sub(_grouped, text.replace(_COUNT_MARK, ""))
    priced = _CURRENCY.sub(lambda m: m.group(1) + " " + _MONEY_MARK, grouped)
    read = _nfc(ev.numbers(_SEPARATED.sub(lambda m: m.group(1) + m.group(2) + _COUNT_MARK, priced)))
    for number, line in enumerate(_LINE.split(read)):
        end = None
        for found in _TOKEN.finditer(line):
            token = found.group(0)
            r.words.append(token.rstrip(_COUNT_MARK))
            r.separated.append(token.endswith(_COUNT_MARK))
            r.lines.append(number)
            sep = line[end:found.start()] if end is not None else ""
            r.seps.append(sep)
            r.spaced.append(end is not None and set(sep) <= _SPACES)
            end = found.end()
            before, after = line[:found.start()].rstrip()[-1:], line[found.end():].lstrip()[:1]
            r.marked.append(bool(before) and (before in _ORDINAL_SIGNS or unicodedata.category(before) == "Sc" and before != _MONEY_MARK)
                            or bool(after) and unicodedata.category(after) == "Sc")
    return r


@functools.lru_cache(maxsize=8192)
def _quantities(text: str) -> tuple[Quantity, ...]:
    # one token stream over the whole text; each token knows its line and whether a currency or an ordinal sign
    # stands next to it (the signs are no tokens, so units and values read as before)
    r = _tokens(text)
    tokens = r.words
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
        unit = base = wide = None
        line = r.lines[i]
        if i and tokens[i - 1] in ("năm", "year", "in") and 1000 <= value <= 2100 and tokens[i][0].isdigit():
            out.append(Quantity(value, "year", "year"))
            i = after
            continue
        # a figure with a currency or an ordinal sign, joined to another figure or glued to a code names no count …
        hard = r.marked[i] or r.marked[after - 1] or _joined(r, i, after) or _coded(r, i)
        # … nor does one in a date or after a word that numbers or prices it
        plain = not hard and not _year_of_a_date(r, i) \
            and not (r.word(i - 1, line) and _numbers_it(r.word(i - 2, line), tokens[i - 1], r.word(i - 3, line)))
        figure_end = after
        if after < len(tokens) and tokens[after] in _CLASSIFIERS:
            after += 1
        for words, name, family in _UNITS:
            if tuple(tokens[after:after + len(words)]) == words:
                unit, base = name, family
                after += len(words)
                break
        if unit in _COUNTED:
            follows = _follows(r, figure_end, line) and all(r.lines[k] == line and r.spaced[k] for k in range(figure_end + 1, after))
            if not (plain and follows and not _density(r, after, line) and not _compound(r, i, unit, after, line)):
                # "1.500 đồng/người", "1941, người dân…", "Lô số 1500 - người bán hàng": the word is no unit of the
                # figure; "1500 người/km2" is a density, no head count. A sentence that says "Top 1500 người" or
                # "1.500 người/km2" still states that count (``wide``); a price, a date or a code states none
                wide = unit if follows and not hard else None
                unit = base = None
                after = figure_end
        measure = near = None
        # what the figure counts: only a figure with no unit or a head count, with no price, ordinal or time on it
        if unit in (None, PEOPLE) and plain and not _not_a_count(r, after):
            near = _measure_before(r, i)
            if unit is not None or _may_follow(r, i, after, line):
                measure = near
            if unit is None:
                measure = measure or _measure_after(r, i, after, line)
        out.append(Quantity(value, unit, base, measure, near or measure, wide))
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
    amounts = tuple(q for q in quantities(text) if q.unit is not None or q.wide is not None or q.value >= DISTINCT_FROM)
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
    # the counts the evidence ties another figure to (#817): for a person to look at, never a conflict (no penalty,
    # the shot stays acceptable: a misread digit must not move the choice off the right shot). One piece's disputed
    # count is unverified; merged over pieces it may also be verified by another piece, and stays disputed
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
                if kind == PEOPLE and any(_disputes(q, wanted) for q in shown):
                    out.disputed.append(label)  # "POPULATION 93,000,000" or "93,000,000 PEOPLE" for 193 000 000 người
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
    if q.unit is None and PEOPLE in (q.measure, q.near):  # a sentence is read widely: "dân số 1500 tăng nhanh"
        return PEOPLE
    return q.wide if q.unit is None else None  # "… 1.500 máy bay/km2", "Top 1500 người": still that count


def _tied(q: Quantity, kind: str) -> bool:
    """Whether the evidence figure ``q`` counts ``kind``: by its unit, or (people) by a population word next to it."""
    return q.unit == kind or (kind == PEOPLE and q.unit is None and q.measure == PEOPLE)


def _disputes(q: Quantity, wanted: Quantity) -> bool:
    """Whether the evidence figure ``q`` is another count of the people ``wanted`` counts: tied to people by its unit or
    a population word, distinctive, and no bare year ("POPULATION 1941" is the year of the chart, not its figure)."""
    return _tied(q, PEOPLE) and q.value != wanted.value and q.value >= DISTINCT_FROM \
        and not (q.unit is None and YEAR_FROM <= q.value <= YEAR_TO)


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
    # a disputed count stays disputed when another piece shows it: the viewer sees the other figure too, as with a
    # conflict (rc15 i16.u1). Never both a conflict and disputed
    disputed = list(dict.fromkeys(v for c in checks for v in c.disputed if v not in conflicts))
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
