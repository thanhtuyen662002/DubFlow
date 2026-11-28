"""One footage search over several sources, for agents (MCP ``search_sources``, #994).

The same rules as Khám phá's "Tất cả nguồn" (apps/desktop/src/features/discovery/model.ts and view.ts):

* suffix chips (空镜, 航拍 4K, …) stay out of the translation and come back in each source's language;
* a Vietnamese phrase goes to Bilibili and SkyPixel in Chinese, and to YouTube in English, translated from
  the Chinese phrase when there is one (place names come out right that way);
* YouTube's phrase leaves out quality words its own filter covers, and a phrase with suffixes that finds
  nothing is searched once more without them (#990);
* results are mixed one by one, never the same page twice, each with its source and a quality label.

A failed source never hides the others: it is reported with its code and a hint.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .http import DiscoveryError, HttpClient
from .models import DiscoveryPage, VideoItem

# The chips of model.ts SUFFIXES, in the same order (tests/discovery/test_footage.py checks they match).
SUFFIXES: tuple[dict[str, str], ...] = (
    {"zh": "空镜", "en": "B-roll", "vi": "Cảnh trống: chỉ cảnh vật, máy móc, không người nói, không chữ"},
    {"zh": "航拍 4K", "en": "4K drone", "vi": "Flycam 4K"},
    {"zh": "无字幕", "en": "no text", "vi": "Không phụ đề"},
    {"zh": "素材", "en": "stock footage", "vi": "Tư liệu để dựng phim"},
    {"zh": "4K 超清", "en": "4K", "vi": "Siêu nét 4K"},
    {"zh": "延时摄影", "en": "timelapse", "vi": "Quay tua nhanh (timelapse)"},
    {"zh": "纪录片", "en": "documentary", "vi": "Phim tài liệu"},
    {"zh": "企业宣传片", "en": "corporate film", "vi": "Phim giới thiệu doanh nghiệp, nhà máy"},
    {"zh": "工业", "en": "industrial", "vi": "Công nghiệp"},
    {"zh": "工厂", "en": "factory", "vi": "Nhà máy"},
)
SOURCES = ("bilibili", "youtube", "skypixel")
YOUTUBE_FILTERS = ("4k", "hd", "any", "cc")
MAX_QUERY = 100
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_FORMS = sorted(((suffix[language], suffix) for suffix in SUFFIXES for language in ("zh", "en")), key=lambda item: -len(item[0]))
_QUALITY_WORDS = {"4k", "hd", "uhd", "2160p", "1080p"}


def suffix_by_form(form: str) -> dict[str, str] | None:
    """A suffix named by its Chinese or English form (case-insensitive for English)."""
    key = " ".join(str(form).split()).lower()
    return next((suffix for text, suffix in _FORMS if text.lower() == key), None)


def split_suffixes(text: str) -> tuple[str, list[dict[str, str]]]:
    """The phrase and the suffixes at its end, in the order they appear."""
    rest = " ".join(str(text).split())
    found: list[dict[str, str]] = []
    for _ in range(20):
        if not rest:
            break
        lower = rest.lower()
        hit = next(((form, suffix) for form, suffix in _FORMS
                    if lower.endswith(form.lower()) and (len(lower) == len(form) or lower[-len(form) - 1] == " ")), None)
        if hit is None:
            break
        rest = rest[: len(rest) - len(hit[0])].strip()
        if hit[1] not in found:
            found.insert(0, hit[1])
    return rest, found


def join_query(base: str, suffixes: Iterable[dict[str, str]], language: str) -> str:
    return " ".join(part for part in [base, *(suffix[language] for suffix in suffixes)] if part)[:MAX_QUERY]


def youtube_query(base: str, suffixes: Iterable[dict[str, str]], quality_filter: str) -> str:
    """YouTube narrows with every word: no quality word its filter covers, no repeated word."""
    quality = quality_filter in ("4k", "hd")
    seen = {word.lower() for word in base.split()}
    words: list[str] = []
    for suffix in suffixes:
        for word in suffix["en"].split():
            key = word.lower()
            if key in seen or (quality and key in _QUALITY_WORDS):
                continue
            seen.add(key)
            words.append(word)
    return " ".join(part for part in [base, *words] if part)[:MAX_QUERY]


def needs_chinese(text: str) -> bool:
    """Letters, no Chinese, not a link: translate before a Chinese source sees it."""
    value = text.strip()
    return bool(re.search(r"[^\W\d_]", value)) and not _HAN.search(value) and not re.match(r"^(https?://|www\.)", value, re.I)


def needs_english(text: str) -> bool:
    return bool(re.search(r"[^\x00-\x7f]", text)) and bool(re.search(r"[^\W\d_]", text))


def quality_label(item: dict[str, Any]) -> str | None:
    """4K/2K/1080p/720p from the reported size (short side), else the source's own label, else a 4K in the title."""
    sides = [side for side in (item.get("width"), item.get("height")) if isinstance(side, int) and side > 0]
    if sides:
        short = min(sides)
        return "4K" if short >= 2000 else "2K" if short >= 1400 else "1080p" if short >= 1000 else "720p" if short >= 700 else "SD"
    for label in ("8K", "4K", "HD"):
        if label in (item.get("badges") or ()):
            return label
    if re.search(r"(^|[^0-9a-z])(8k|4k|2160p)([^0-9a-z]|$)|超清|超高清", str(item.get("title") or ""), re.I):
        return "4K?"
    return None


def interleave(lists: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    lists = list(lists)
    out, seen = [], set()
    for index in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if index < len(items) and items[index]["page_url"] not in seen:
                seen.add(items[index]["page_url"])
                out.append(items[index])
    return out


Translator = Callable[[str], str]
Searcher = Callable[[str, str, dict[str, Any]], DiscoveryPage]


def default_searcher(client: HttpClient | None = None) -> Searcher:
    from .bilibili import Bilibili
    from .skypixel import SkyPixel
    from .youtube import YouTube

    adapters = {"bilibili": Bilibili(client), "youtube": YouTube(client), "skypixel": SkyPixel(client)}

    def search(source: str, query: str, more: dict[str, Any]) -> DiscoveryPage:
        if source == "youtube":
            return adapters[source].search(query, cursor=more.get("cursor"), quality=more.get("filter") or "4k")
        return adapters[source].search(query, int(more.get("page") or 1))
    return search


def default_translators() -> tuple[Translator, Translator]:
    from .to_chinese import TranslateError, to_chinese, to_english

    def chinese(text: str) -> str:
        try:
            return to_chinese(text)[0]
        except TranslateError:
            return text

    def english(text: str) -> str:
        try:
            return to_english(text)[0]
        except TranslateError:
            return text
    return chinese, english


_HINTS = {
    "BLOCKED": "the site refused automated search for now; open this source in DubFlow's Khám phá page with the same phrase, or try later",
    "LOGIN_REQUIRED": "the site asks for a login; open it in DubFlow's Khám phá page",
    "NETWORK": "network error; try again",
}


def _row(item: VideoItem, source: str) -> dict[str, Any]:
    data = item.to_dict()
    data["source"] = source
    data["quality"] = quality_label(data)
    return data


def search(query: str, *, sources: Iterable[str] = SOURCES, suffixes: Iterable[str] = (), youtube_filter: str = "4k",
           translate: bool = True, more: dict[str, dict[str, Any]] | None = None,
           searcher: Searcher | None = None, translators: tuple[Translator, Translator] | None = None) -> dict[str, Any]:
    """Search every requested source once (or their next pages with ``more``) and mix the results."""
    sources = [source for source in SOURCES if source in set(sources)]
    if not sources:
        raise ValueError("sources must name bilibili, youtube or skypixel")
    if youtube_filter not in YOUTUBE_FILTERS:
        raise ValueError("youtube_filter must be 4k, hd, any or cc")
    base, chips = split_suffixes(query)
    for form in suffixes:
        suffix = suffix_by_form(form)
        if suffix is None:
            raise ValueError(f"unknown suffix {form!r}; list_footage_suffixes names them")
        if suffix not in chips:
            chips.append(suffix)
    if not base and not chips:
        raise ValueError("query is empty")
    searcher = searcher or default_searcher()
    chinese_of, english_of = translators or default_translators()
    chinese_base = chinese_of(base) if translate and base and needs_chinese(base) else base
    english_base = english_of(chinese_base or base) if translate and needs_english(chinese_base or base) else (chinese_base or base)
    queries = {"zh": join_query(chinese_base, chips, "zh"), "en": youtube_query(english_base, chips, youtube_filter)}
    more = more or {}
    states: dict[str, dict[str, Any]] = {}
    found: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        text = queries["en"] if source == "youtube" else queries["zh"]
        options = {**(more.get(source) or {}), "filter": youtube_filter}
        state: dict[str, Any] = {"query": text, "status": "ok", "count": 0, "next": None}
        try:
            page = searcher(source, text, options)
            # YouTube: a phrase with suffixes that finds nothing is searched once without them (#990).
            if source == "youtube" and not page.items and not options.get("cursor") and english_base and english_base != text:
                state["query"] = text = english_base
                state["retried_without_suffixes"] = True
                page = searcher(source, text, {"filter": youtube_filter})
        except DiscoveryError as error:
            state.update(status="error", code=error.code, message=error.message, hint=_HINTS.get(error.code, ""))
            states[source] = state
            found[source] = []
            continue
        rows = [_row(item, source) for item in page.items if isinstance(item, VideoItem)]
        found[source] = rows
        state["count"] = len(rows)
        if page.next_cursor:
            state["next"] = {"cursor": page.next_cursor}
        elif page.next_page is not None:
            state["next"] = {"page": page.next_page}
        states[source] = state
    return {"queries": queries, "suffixes": [suffix["zh"] for suffix in chips], "youtube_filter": youtube_filter,
            "sources": states, "items": interleave(found[source] for source in sources)}


__all__ = ["SOURCES", "SUFFIXES", "YOUTUBE_FILTERS", "interleave", "join_query", "needs_chinese", "needs_english",
           "quality_label", "search", "split_suffixes", "suffix_by_form", "youtube_query"]
