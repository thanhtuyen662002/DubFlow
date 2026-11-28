"""MCP tools for finding and fetching source footage (#994): every source Khám phá searches, one call.

Metadata comes from the discovery adapters (no browser, no session). Downloads go through DubFlow's verified
downloader, as a pasted link does: Bilibili with the saved login (1080p/4K), YouTube through the generic
adapter (2160p), SkyPixel through its 1080p MP4. Xinpianchang and Douyin pages need the app's own browser.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from engine.dubflow.discovery import footage
from engine.dubflow.docu.project import DocuError

_SOURCES = {"type": "array", "items": {"type": "string", "enum": list(footage.SOURCES)}, "uniqueItems": True,
            "description": "Which sources to search; default all three"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


TOOLS = [
    _tool("search_sources", "Search Bilibili, YouTube and SkyPixel (DJI drone footage) at once, like Khám phá's Tất cả nguồn, and get one "
          "mixed list: each video with its source, page_url, title, author, duration, views, cover, and quality (4K/2K/1080p from the "
          "upload size, the source's 4K/CC label, or '4K?' when only the title says so). Type Vietnamese, Chinese or English: Bilibili "
          "and SkyPixel get Chinese, YouTube English (translated from the Chinese phrase, so place names come out right). Add B-roll "
          "suffixes (see list_footage_suffixes) for clean footage. A source that fails is reported with a hint; the others still answer. "
          "Pass a source's 'next' back in more to get its next page. Best results: write the phrase in Chinese yourself (e.g. the "
          "Chinese name of a dam or factory) and set translate false; machine translation of Vietnamese place names is weak. "
          "Only use footage you have the right to use: YouTube 'CC' videos allow reuse with credit.",
          {"query": {"type": "string", "maxLength": 100, "description": "e.g. 白鹤滩水电站, 'đập thủy điện', 'Qingdao automated port'"},
           "suffixes": {"type": "array", "items": {"type": "string"}, "maxItems": 6, "description": "Suffix forms, e.g. ['空镜', '航拍 4K']"},
           "sources": _SOURCES,
           "youtube_filter": {"type": "string", "enum": list(footage.YOUTUBE_FILTERS), "description": "Default 4k; cc = Creative Commons"},
           "translate": {"type": "boolean", "description": "Default true: translate a Vietnamese phrase for each source"},
           "more": {"type": "object", "description": "Next pages: {source: next} from a previous answer's sources.<source>.next",
                    "additionalProperties": {"type": "object"}}},
          ["query"], read_only=True),
    _tool("list_footage_suffixes", "The B-roll suffix chips Khám phá offers (空镜 = B-roll without people or captions, 航拍 4K = drone 4K, "
          "无字幕 = no subtitles, …) with their Chinese form, English form for YouTube and Vietnamese meaning.", {}, [], read_only=True),
    _tool("get_trends", "Hot keywords on Bilibili, Douyin or Kuaishou right now (Chinese), to pick a topic before searching.",
          {"platform": {"type": "string", "enum": ["bilibili", "douyin", "kuaishou"]}}, ["platform"], read_only=True),
    _tool("suggest_keywords", "Bilibili's or Douyin's keyword suggestions for a Chinese phrase.",
          {"platform": {"type": "string", "enum": ["bilibili", "douyin"]}, "query": {"type": "string", "maxLength": 100}},
          ["platform", "query"], read_only=True),
]


def _page(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else value


def call(name: str, args: dict[str, Any], *, search: Callable[..., dict[str, Any]] | None = None) -> Any:
    from engine.dubflow.discovery.http import DiscoveryError

    if name == "search_sources":
        search = search or footage.search
        try:
            return search(str(args.get("query") or ""), sources=args.get("sources") or footage.SOURCES,
                          suffixes=args.get("suffixes") or (), youtube_filter=args.get("youtube_filter") or "4k",
                          translate=args.get("translate", True) is not False, more=args.get("more") or None)
        except ValueError as error:
            raise DocuError(str(error)) from error
    if name == "list_footage_suffixes":
        return [dict(suffix) for suffix in footage.SUFFIXES]
    if name in ("get_trends", "suggest_keywords"):
        from engine.dubflow.discovery.bilibili import Bilibili
        from engine.dubflow.discovery.douyin import Douyin
        from engine.dubflow.discovery.kuaishou import Kuaishou

        adapter = {"bilibili": Bilibili, "douyin": Douyin, "kuaishou": Kuaishou}.get(str(args.get("platform")))
        if adapter is None:
            raise DocuError("platform phải là bilibili, douyin hoặc kuaishou")
        try:
            if name == "get_trends":
                return _page(adapter().trends())
            query = str(args.get("query") or "").strip()
            if not query:
                raise DocuError("Cần từ khóa")
            return _page(adapter().suggest(query))
        except DiscoveryError as error:
            raise DocuError(f"{args.get('platform')}: {error.message}") from error
    raise KeyError(name)


_SKYPIXEL_PAGE = ("skypixel.com", "www.skypixel.com")


def resolve_download_url(url: str, *, media: Callable[[str], dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
    """The URL the downloader fetches: a SkyPixel page becomes its 1080p MP4 (with its licence). Pages only the app's own
    browser can read are refused with what to do instead."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _SKYPIXEL_PAGE and parts.path.startswith("/videos/"):
        slug = parts.path[len("/videos/"):].strip("/")
        if media is None:
            from engine.dubflow.discovery.skypixel import SkyPixel
            media = SkyPixel().media
        try:
            found = media(slug)
        except Exception as error:  # noqa: BLE001 - reported to the agent
            raise DocuError(f"SkyPixel: không lấy được bản tải ({error})") from error
        return found["url"], {"license": found.get("license"), "rendition": found.get("quality")}
    if host == "xinpianchang.com" or host.endswith(".xinpianchang.com"):
        raise DocuError("Xinpianchang cần trình duyệt của DubFlow (ô xác minh): mở Khám phá › Xinpianchang trong app, "
                        "kéo ô xác minh rồi bấm Làm video trên thẻ video.")
    return url, {}


def probe_size(ffprobe: Path, video: Path) -> dict[str, Any]:
    """Width, height and duration of a downloaded file, so the agent knows what quality it got."""
    try:
        done = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                               "-of", "json", str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
        value = json.loads(done.stdout or "{}")
        stream = (value.get("streams") or [{}])[0]
        duration = float((value.get("format") or {}).get("duration") or 0) or None
        width, height = stream.get("width"), stream.get("height")
        return {"width": width, "height": height, "duration_s": duration,
                "quality": footage.quality_label({"width": width, "height": height})}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


__all__ = ["TOOLS", "call", "probe_size", "resolve_download_url"]
