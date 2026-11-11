"""Proper names across languages: the same place, person or thing written differently (Gate S, multilingual matching).

A narration written in Vietnamese names "Kavkaz", "Mátxcơva", "Điện Kremli"; the footage's own subtitles (often
English or the source's language) say "Caucasus", "Moscow", "Kremlin" or "Кремль", and a Chinese source says
"北京" where the script says "Bắc Kinh". Plain word matching finds none of these, so the shot that shows the place is
not found. This module recognises the names of a small, curated set of entities in any text and says how sure the
match is:

* ``EXACT`` (1000): the same written form on both sides (after case folding).
* ``NORMALIZED`` (950): the same form once diacritics are dropped ("Mátxcơva" / "Matxcova").
* ``VERIFIED`` (850): two forms of one entity from a verified alias list: the built-in table below, written by hand
  from standard Vietnamese exonyms, or an alias the user wrote in a note (``Kavkaz = Caucasus``).
* ``CONTEXTUAL`` (600): a form that is also an ordinary word or another place ("Áo" is a coat, "Georgia" is a
  US state too) found where the text gives the reading (a country word before it, a capital letter inside the
  sentence) — weaker than a verified alias.
* anything else is an *unverified candidate* and gives no match at all: two names are never taken for one entity
  because they are spelt alike ("Austria" / "Australia", "Kavkaz" / "Kazan").

No model, no translation, no fuzzy matching: a name absent from the table and the user's notes simply matches only
itself, as before.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

EXACT, NORMALIZED, VERIFIED, CONTEXTUAL = 1000, 950, 850, 600
TIERS = {"exact": EXACT, "normalized": NORMALIZED, "verified": VERIFIED, "contextual": CONTEXTUAL}
PREFIX = "@"  # the pseudo term of an entity in the aligner's term space

# Each entity: an id, its forms (any language and script), and the forms that are ambiguous on their own (an
# ordinary word, or the name of something else): those count only in context (CONTEXTUAL), and ``unless`` lists
# words that, near an ambiguous form, give it the other reading (Georgia near Atlanta is the US state).
_TABLE: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    # Caucasus and the eastern front
    ("caucasus", ("Kavkaz", "Caucasus", "Кавказ", "Kaukasus", "高加索"), (), ()),
    ("moscow", ("Mátxcơva", "Mạc Tư Khoa", "Moscow", "Moskva", "Москва", "Moskau", "莫斯科"), (), ()),
    ("kremlin", ("Kremli", "Điện Kremli", "Kremlin", "Кремль", "Kreml", "克里姆林宫"), (), ()),
    ("stalingrad", ("Stalingrad", "Xtalingrát", "Сталинград", "斯大林格勒"), (), ()),
    ("leningrad", ("Leningrad", "Lêningrát", "Ленинград", "列宁格勒"), (), ()),
    ("volga", ("Volga", "Vonga", "Волга", "伏尔加河"), (), ()),
    ("caspian", ("Caspi", "biển Caspi", "Caspian", "Каспийское", "里海"), (), ()),
    ("baku", ("Baku", "Bakou", "Баку", "巴库"), (), ()),
    ("siberia", ("Siberia", "Xibia", "Xi-bê-ri", "Сибирь", "西伯利亚"), (), ()),
    ("russia", ("Nga", "nước Nga", "Russia", "Russian", "Россия", "俄罗斯"), ("Nga",), ()),
    ("ukraine", ("Ukraina", "Ukraine", "Ukrainian", "Україна", "Украина", "乌克兰"), (), ()),
    ("georgia_country", ("Gruzia", "Georgia", "Грузия", "საქართველო", "格鲁吉亚"), ("Georgia",),
     ("atlanta", "savannah", "usa", "u s", "united states", "us state", "state of georgia", "mỹ", "hoa kỳ")),
    ("tbilisi", ("Tbilisi", "Тбилиси", "第比利斯"), (), ()),
    # Europe
    ("austria", ("Áo", "nước Áo", "Austria", "Austrian", "Österreich", "Австрия", "奥地利"), ("Áo",), ()),
    ("vienna", ("Viên", "Vienna", "Wien", "Вена", "维也纳"), ("Viên",), ()),
    ("germany", ("Đức", "nước Đức", "Germany", "German", "Deutschland", "Германия", "德国"), ("Đức",), ()),
    ("berlin", ("Berlin", "Béc-lin", "Берлин", "柏林"), (), ()),
    ("france", ("Pháp", "nước Pháp", "France", "French", "Франция", "法国"), ("Pháp",), ()),
    ("paris", ("Paris", "Pa-ri", "Париж", "巴黎"), (), ()),
    ("london", ("Luân Đôn", "London", "Лондон", "伦敦"), (), ()),
    ("rome", ("Roma", "Rome", "Рим", "罗马"), (), ()),
    ("italy", ("Ý", "Italia", "Italy", "Italian", "Италия", "意大利"), ("Ý",), ()),
    ("spain", ("Tây Ban Nha", "Spain", "Spanish", "España", "Испания", "西班牙"), (), ()),
    ("poland", ("Ba Lan", "Poland", "Polish", "Polska", "Польша", "波兰"), ("Polish",), ()),
    ("greece", ("Hy Lạp", "Greece", "Greek", "Греция", "希腊"), (), ()),
    ("netherlands", ("Hà Lan", "Netherlands", "Holland", "Dutch", "Nederland", "荷兰"), (), ()),
    ("switzerland", ("Thụy Sĩ", "Switzerland", "Swiss", "Schweiz", "瑞士"), (), ()),
    ("sweden", ("Thụy Điển", "Sweden", "Swedish", "Sverige", "瑞典"), (), ()),
    ("mediterranean", ("Địa Trung Hải", "Mediterranean", "地中海"), (), ()),
    # Asia
    ("china", ("Trung Quốc", "China", "Chinese", "中国", "中國"), ("China",), ()),
    ("beijing", ("Bắc Kinh", "Beijing", "Peking", "北京"), (), ()),
    ("shanghai", ("Thượng Hải", "Shanghai", "上海"), (), ()),
    ("guangzhou", ("Quảng Châu", "Guangzhou", "Canton", "广州"), (), ()),
    ("hong_kong", ("Hồng Kông", "Hong Kong", "香港"), (), ()),
    ("taiwan", ("Đài Loan", "Taiwan", "台湾", "台灣"), (), ()),
    ("tibet", ("Tây Tạng", "Tibet", "Tibetan", "西藏"), (), ()),
    ("yangtze", ("Trường Giang", "sông Dương Tử", "Yangtze", "长江"), (), ()),
    ("yellow_river", ("Hoàng Hà", "Yellow River", "黄河"), (), ()),
    ("japan", ("Nhật Bản", "Japan", "Japanese", "日本"), (), ()),
    ("tokyo", ("Tokyo", "Tô-ky-ô", "Đông Kinh", "東京", "东京"), (), ()),
    ("korea", ("Hàn Quốc", "Korea", "Korean", "한국", "韩国"), (), ()),
    ("seoul", ("Seoul", "Xơ-un", "서울", "首尔"), (), ()),
    ("mongolia", ("Mông Cổ", "Mongolia", "Mongolian", "蒙古"), (), ()),
    ("india", ("Ấn Độ", "India", "Indian", "印度"), (), ()),
    ("himalaya", ("Himalaya", "Hy Mã Lạp Sơn", "Himalayas", "喜马拉雅"), (), ()),
    ("thailand", ("Thái Lan", "Thailand", "Thai", "泰国"), ("Thai",), ()),
    ("singapore", ("Singapore", "Xinhgapo", "Xin-ga-po", "新加坡"), (), ()),
    ("cambodia", ("Campuchia", "Cambodia", "Cambodian", "柬埔寨"), (), ()),
    ("laos", ("Lào", "Laos", "Lao", "老挝"), ("Lào", "Lao"), ()),
    ("vietnam", ("Việt Nam", "Vietnam", "Viet Nam", "Vietnamese", "越南"), (), ()),
    ("hanoi", ("Hà Nội", "Hanoi", "河内"), (), ()),
    # Elsewhere
    ("united_states", ("Mỹ", "Hoa Kỳ", "United States", "America", "American", "USA", "美国"), ("Mỹ", "America", "American"), ()),
    ("australia", ("Úc", "nước Úc", "Australia", "Australian", "Австралия", "澳大利亚"), ("Úc",), ()),
    ("egypt", ("Ai Cập", "Egypt", "Egyptian", "埃及"), (), ()),
    ("pacific", ("Thái Bình Dương", "Pacific", "太平洋"), (), ()),
    ("atlantic", ("Đại Tây Dương", "Atlantic", "大西洋"), (), ()),
    ("arctic", ("Bắc Cực", "Arctic", "北极"), (), ()),
    ("antarctica", ("Nam Cực", "Antarctica", "Antarctic", "南极"), (), ()),
)

# Words before an ambiguous name that give it the reading of a place or a people.
_CUES = ("nước", "ở", "tại", "người", "quân", "sang", "đến", "tới", "từ", "thủ đô", "thành phố", "vùng", "miền", "dãy",
         "sông", "biển", "của", "và", "in", "of", "to", "from", "the", "at")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_SENTENCE_START = re.compile(r"(^|[.!?…:;\n]\s*|[\"“(]\s*)$")


def fold(text: str) -> str:
    """Case folded, diacritics dropped (đ → d), spaces normalised: the NORMALIZED form."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text.casefold())
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w]+", " ", plain).split())


@dataclass(frozen=True)
class Entity:
    entity_id: str
    forms: tuple[str, ...]
    ambiguous: frozenset[str]  # forms (as written in the table) that need context
    unless: tuple[str, ...]    # folded words that give an ambiguous form its other reading
    origin: str = "builtin"


@dataclass(frozen=True)
class Mention:
    """A name found in a text: which entity, the form as written there, and how it was recognised."""

    entity_id: str
    form: str      # the table's (or note's) form that matched
    written: str   # the text as written at that place
    tier: int      # EXACT for an unambiguous form, CONTEXTUAL for an ambiguous one read in context
    words: frozenset[str]  # the folded words of the name (left out of the plain word match)


def strip_marks(text: str) -> str:
    """``text`` without diacritics, one character for one (so positions stay the same): the NORMALIZED reading."""
    out = []
    for ch in text:
        if ch in "đĐ":
            out.append("d" if ch == "đ" else "D")
            continue
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn")
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


class Table:
    """Entities and their forms; :meth:`with_notes` adds the aliases a user wrote."""

    def __init__(self, entities: Iterable[Entity]) -> None:
        self.entities = {e.entity_id: e for e in entities}
        # Text written with its marks is matched with them ("thủy điện" is not "Thụy Điển"); text written without
        # any (a file name, an unaccented AI answer) against the forms without theirs: "Matxcova" is "Mátxcơva".
        self._marked: list[tuple[re.Pattern[str], Entity, str, bool]] = []
        self._plain: list[tuple[re.Pattern[str], Entity, str, bool]] = []
        for entity in self.entities.values():
            for form in entity.forms:
                ambiguous = form in entity.ambiguous
                self._marked.append((_pattern(unicodedata.normalize("NFC", form)), entity, form, ambiguous))
                self._plain.append((_pattern(strip_marks(form)), entity, form, ambiguous))
        # longest forms first, so "biển Caspi" wins over "Caspi", "Điện Kremli" over "Kremli"
        self._marked.sort(key=lambda item: -len(item[2]))
        self._plain.sort(key=lambda item: -len(item[2]))

    def with_notes(self, notes: Sequence[str]) -> "Table":
        """This table plus the aliases written in ``notes`` ("A = B", "A (B)", "A / B" of capitalised names)."""
        found = aliases_in_notes(notes)
        if not found:
            return self
        extra = list(self.entities.values())
        for number, names in enumerate(found, 1):
            # a note's alias joins the entity one of its names already belongs to, or makes a new one
            known = next((e for e in extra if any(fold(n) == fold(f) for n in names for f in e.forms)), None)
            if known is not None:
                extra.remove(known)
                extra.append(Entity(known.entity_id, tuple(dict.fromkeys((*known.forms, *names))), known.ambiguous, known.unless,
                                    known.origin))
            else:
                extra.append(Entity(f"user{number}", tuple(names), frozenset(), (), "user"))
        return Table(extra)

    def mentions(self, text: str) -> list[Mention]:
        """Every entity named in ``text`` (each place in the text is read once, the longest form first)."""
        if not text:
            return []
        text = unicodedata.normalize("NFC", text)
        marked = _written_with_marks(text)
        searched, patterns = (text, self._marked) if marked else (strip_marks(text), self._plain)
        taken: list[tuple[int, int]] = []
        out: list[Mention] = []
        folded_text = f" {fold(text)} "
        for pattern, entity, form, ambiguous in patterns:
            for match in pattern.finditer(searched):
                start, end = match.span(1)
                if any(start < b and end > a for a, b in taken):
                    continue
                written = text[start:end]
                if ambiguous:
                    if not marked and _VIETNAMESE_MARKS.search(form):
                        continue  # "ao" in unaccented text is anything: an ambiguous name needs its marks

                    if not _in_context(text, start, written):
                        continue
                    if any(f" {fold(word)} " in folded_text for word in entity.unless):
                        continue
                taken.append((start, end))
                out.append(Mention(entity.entity_id, form, written, CONTEXTUAL if ambiguous else EXACT,
                                   frozenset(fold(written).split())))
        return out


_VIETNAMESE_MARKS = re.compile(r"[^\x00-\x7f]")


def _written_with_marks(text: str) -> bool:
    """Text with a mark on some Latin letter: then marks are part of the words (``evidence`` reads it the same way,
    over a quarter of the words; one marked letter is enough here, since a name is matched whole)."""
    return any(ord(c) > 127 and c.isalpha() and unicodedata.normalize("NFD", c) != c for c in text)


def _pattern(form: str) -> re.Pattern[str]:
    """``form`` as a whole word or phrase, case-insensitive; with or without its diacritics when it has them.

    CJK forms have no spaces around words: they are found anywhere in the text.
    """
    if _CJK.search(form):
        return re.compile(f"({re.escape(form)})")
    parts = [re.escape(p) for p in form.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![^\W_])({body})(?![^\W_])", re.IGNORECASE)


def _in_context(text: str, start: int, written: str) -> bool:
    """An ambiguous name reads as the name when it is capitalised inside a sentence or follows a place word."""
    if not written[:1].isupper():
        return False
    before = text[:start]
    words_before = fold(before).split()
    cues = {fold(c) for c in _CUES}
    if words_before and (words_before[-1] in cues or " ".join(words_before[-2:]) in cues):
        return True
    return not _SENTENCE_START.search(before)


_NOTE_ALIAS = re.compile(
    r"([^\W\d_][\w\-’']*(?:\s+[^\W\d_][\w\-’']*){0,3})\s*(?:=|còn gọi là|tức là|aka|also known as)\s*"
    r"([^\W\d_][\w\-’']*(?:\s+[^\W\d_][\w\-’']*){0,3})", re.IGNORECASE)


def aliases_in_notes(notes: Sequence[str]) -> list[tuple[str, ...]]:
    """Name pairs a user wrote in a note: ``Kavkaz = Caucasus``, ``Kavkaz còn gọi là Caucasus``, ``Kavkaz tức là
    Caucasus``, ``Kavkaz aka Caucasus``. Both names must start with a capital (a proper name): "nồi = màu đen" is no
    alias."""
    out: list[tuple[str, ...]] = []
    for note in notes:
        for match in _NOTE_ALIAS.finditer(note or ""):
            a, b = match.group(1).strip(), match.group(2).strip()
            # the names: from the last capitalised word run of the left side, the capitalised run of the right
            a = _capitalised_tail(a)
            b = _capitalised_head(b)
            if a and b and fold(a) != fold(b):
                out.append((a, b))
    return out


def _capitalised_tail(text: str) -> str:
    words = text.split()
    kept: list[str] = []
    for word in reversed(words):
        if not word[:1].isupper():
            break
        kept.insert(0, word)
    return " ".join(kept)


def _capitalised_head(text: str) -> str:
    words = text.split()
    kept: list[str] = []
    for word in words:
        if not word[:1].isupper():
            break
        kept.append(word)
    return " ".join(kept)


BUILTIN = Table(Entity(i, forms, frozenset(amb), tuple(unless)) for i, forms, amb, unless in _TABLE)


def tier(unit_side: Mention, shot_side: Mention, table: Table) -> int:
    """How sure the match of two mentions of one entity is (0 when they are different entities)."""
    if unit_side.entity_id != shot_side.entity_id:
        return 0
    if unit_side.written.casefold() == shot_side.written.casefold():
        base = EXACT
    elif fold(unit_side.written) == fold(shot_side.written):
        base = NORMALIZED
    else:
        base = VERIFIED
    return min(base, unit_side.tier, shot_side.tier)


def term(entity_id: str) -> str:
    return PREFIX + entity_id


def found_in(texts: Mapping[str, int], table: Table) -> dict[str, list[tuple[Mention, int]]]:
    """Entity id → its mentions in ``texts`` (text → the trust-weighted confidence of the text)."""
    out: dict[str, list[tuple[Mention, int]]] = {}
    for text, weight in texts.items():
        for mention in table.mentions(text):
            out.setdefault(mention.entity_id, []).append((mention, weight))
    return out


__all__ = ["BUILTIN", "CONTEXTUAL", "EXACT", "Entity", "Mention", "NORMALIZED", "PREFIX", "TIERS", "Table", "VERIFIED",
           "aliases_in_notes", "found_in", "fold", "term", "tier"]
