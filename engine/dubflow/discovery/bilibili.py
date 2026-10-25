"""Verified Bilibili metadata routes with anonymous SPI cookies and WBI."""

from collections.abc import Callable, Mapping
import hashlib
import time
from urllib.parse import quote, urlencode, urlsplit

from ._base import DiscoveryAdapter, object_at, require_success, rows_at, text_at
from .http import DiscoveryError, HttpClient
from .models import (DiscoveryPage, TrendItem, VideoItem, duration_seconds,
                     normalize_cover, optional_int, plain_title)


API = "https://api.bilibili.com"
SEARCH = "https://s.search.bilibili.com"
MIXIN = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
         27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
         37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
         22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)


def sign_wbi(params: Mapping, wbi_img: Mapping, timestamp: int | None = None) -> dict[str, str]:
    """Research algorithm: permuted 32-char key, sorted escaped query, MD5.

    WBI image URLs carry keys even in anonymous nav responses with code -101.
    Signing does not clear captcha, vouchers or HTTP 412. Never retry those.
    """
    stems = []
    for field in ("img_url", "sub_url"):
        url = wbi_img.get(field)
        if not isinstance(url, str):
            raise DiscoveryError("BAD_RESPONSE", "WBI keys are missing.")
        try:
            stem = urlsplit(url).path.rsplit("/", 1)[-1].split(".")[0]
        except ValueError as error:
            raise DiscoveryError("BAD_RESPONSE", "WBI keys are malformed.") from error
        if len(stem) != 32 or any(c not in "0123456789abcdefABCDEF" for c in stem):
            raise DiscoveryError("BAD_RESPONSE", "WBI keys are malformed.")
        stems.append(stem)
    source = "".join(stems)
    mixin = "".join(source[i] for i in MIXIN)[:32]
    values = {key: value for key, value in params.items() if key != "w_rid"}
    values["wts"] = int(time.time()) if timestamp is None else int(timestamp)
    values = {key: "".join(c for c in str(value) if c not in "!'()*")
              for key, value in sorted(values.items())}
    values["w_rid"] = hashlib.md5((urlencode(values) + mixin).encode("utf-8")).hexdigest()
    return values


def _trend(word: str, rank: int, heat: object = None) -> TrendItem:
    return TrendItem("bilibili", word, rank, optional_int(heat),
                     "https://search.bilibili.com/all?keyword=" + quote(word, safe=""))


def _videos(rows, kind: str) -> tuple[VideoItem, ...]:
    """Parse every usable row. Search pages can mix in non-video cards (an
    anime season, a live room) without a BV id; skip those instead of failing
    the whole page. A page with rows but no usable video is still an error."""
    items = []
    for row in rows:
        try:
            items.append(_video(row, kind))
        except DiscoveryError:
            continue
    if rows and not items:
        raise DiscoveryError("BAD_RESPONSE", "No usable video in the discovery response.")
    return tuple(items)


def _video(row: dict, kind: str) -> VideoItem:
    video_id = text_at(row, "bvid")
    if kind == "board":
        owner = row.get("owner")
        stat = row.get("stat")
        if owner is None:
            owner = {}
        if stat is None:
            stat = {}
        if not isinstance(owner, dict) or not isinstance(stat, dict):
            raise DiscoveryError("BAD_RESPONSE", "Malformed Bilibili video metadata.")
        author, author_id = owner.get("name"), owner.get("mid")
        plays, likes = stat.get("view"), stat.get("like")
    else:
        author, author_id = row.get("author"), row.get("mid")
        plays, likes = row.get("play"), row.get("like")
    # Creator meta.stat contains COLLECTION likes, never the video's likes.
    duration = row.get("length") if kind == "creator" else row.get("duration")
    cover = row.get("pic")
    if cover is not None and not isinstance(cover, str):
        raise DiscoveryError("BAD_RESPONSE", "Malformed Bilibili cover URL.")
    return VideoItem(
        "bilibili", video_id, plain_title(text_at(row, "title")),
        str(author) if author is not None else "",
        str(author_id) if author_id is not None else "",
        duration_seconds(duration), optional_int(plays), optional_int(likes),
        normalize_cover(cover or ""), "https://www.bilibili.com/video/" + video_id,
    )


def _page_params(page: int, ps: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if isinstance(ps, bool) or not isinstance(ps, int) or not 1 <= ps <= 50:
        raise ValueError("ps must be between 1 and 50")


class Bilibili(DiscoveryAdapter):
    platform = "bilibili"

    def __init__(self, client: HttpClient | None = None, *,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(client)
        self._clock = clock
        self._bootstrapped = False
        self._wbi: dict | None = None
        self._keys_at = 0.0

    def _signed(self, params: dict) -> dict[str, str]:
        if not self._bootstrapped:
            payload = self.client.json(self.platform, API + "/x/frontend/finger/spi")
            require_success(payload)
            data = object_at(payload, "data")
            for field, name in (("b_3", "buvid3"), ("b_4", "buvid4")):
                self.client.set_cookie(self.platform, name, text_at(data, field))
            self._bootstrapped = True
        now = self._clock()
        if self._wbi is None or now - self._keys_at >= 3600:
            payload = self.client.json(self.platform, API + "/x/web-interface/nav")
            require_success(payload, allowed=(0, -101))
            wbi = object_at(payload, "data", "wbi_img")
            # Validate keys before caching them.
            sign_wbi({}, wbi, int(now))
            self._wbi, self._keys_at = wbi, now
        return sign_wbi(params, self._wbi, int(self._clock()))

    def trends(self) -> DiscoveryPage:
        payload = self.client.json(self.platform, SEARCH + "/main/hotword", params={"limit": 10})
        require_success(payload)
        return DiscoveryPage(tuple(_trend(text_at(row, "keyword"), optional_int(row.get("pos")) or i,
                                          row.get("heat_score"))
                                   for i, row in enumerate(rows_at(payload, "list"), 1)))

    def suggest(self, query: str) -> DiscoveryPage:
        payload = self.client.json(self.platform, SEARCH + "/main/suggest", params={
            "term": query, "main_ver": "v1", "func": "suggest", "suggest_type": "accurate",
            "sub_type": "tag", "tag_num": 10, "highlight": "",
        })
        require_success(payload)
        return DiscoveryPage(tuple(_trend(text_at(row, "value" if row.get("value") else "term"), i)
                                   for i, row in enumerate(rows_at(payload, "result", "tag"), 1)))

    def popular(self, page: int = 1, ps: int = 20) -> DiscoveryPage:
        _page_params(page, ps)
        payload = self.client.json(self.platform, API + "/x/web-interface/popular",
                                   params={"pn": page, "ps": ps})
        require_success(payload)
        data = object_at(payload, "data")
        if not isinstance(data.get("no_more"), bool):
            raise DiscoveryError("BAD_RESPONSE", "Missing popular pagination status.")
        return DiscoveryPage(_videos(rows_at(data, "list"), "board"),
                             None if data["no_more"] else page + 1)

    def ranking(self) -> DiscoveryPage:
        payload = self.client.json(self.platform, API + "/x/web-interface/ranking/v2",
                                   params={"rid": 0, "type": "all"})
        require_success(payload)
        return DiscoveryPage(_videos(rows_at(payload, "data", "list"), "board"))

    def search(self, query: str, page: int = 1, ps: int = 20) -> DiscoveryPage:
        _page_params(page, ps)
        params = self._signed({"search_type": "video", "keyword": query, "page": page, "page_size": ps})
        payload = self.client.json(self.platform, API + "/x/web-interface/wbi/search/type", params=params)
        require_success(payload)
        data = object_at(payload, "data")
        num_pages = optional_int(data.get("numPages"))
        if num_pages is None:
            raise DiscoveryError("BAD_RESPONSE", "Missing search pagination status.")
        return DiscoveryPage(_videos(rows_at(data, "result"), "search"),
                             page + 1 if page < num_pages else None)

    def creator(self, mid: str, page: int = 1, ps: int = 20) -> DiscoveryPage:
        _page_params(page, ps)
        if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or int(mid) < 1:
            raise ValueError("mid must be a positive numeric Bilibili UID")
        params = self._signed({"mid": mid, "pn": page, "ps": ps, "order": "pubdate", "tid": 0,
                               "platform": "web", "web_location": "1550101", "order_avoided": "true"})
        payload = self.client.json(self.platform, API + "/x/space/wbi/arc/search", params=params)
        require_success(payload)
        data = object_at(payload, "data")
        paging = object_at(data, "page")
        count, size, returned_page = (optional_int(paging.get(key)) for key in ("count", "ps", "pn"))
        if count is None or not size or returned_page != page:
            raise DiscoveryError("BAD_RESPONSE", "Malformed creator pagination status.")
        return DiscoveryPage(_videos(rows_at(data, "list", "vlist"), "creator"),
                             page + 1 if page * size < count else None)
