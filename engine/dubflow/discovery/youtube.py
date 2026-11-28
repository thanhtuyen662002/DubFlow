"""YouTube video search, the request youtube.com's own results page makes (#978).

``/youtubei/v1/search`` answers an anonymous POST with the same renderer tree
the site draws. Only ``videoRenderer`` entries become results (shelves, Shorts
rows, channels and ads are skipped); the next page is a continuation token,
not a page number. Filters are YouTube's own search parameters.

Links download through the generic adapter (ADR-0040), so the result's page
URL is also what is fetched.
"""

import re

from ._base import DiscoveryAdapter
from .http import DiscoveryError
from .models import DiscoveryPage, VideoItem, duration_seconds, optional_int


API = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
CLIENT = {"clientName": "WEB", "clientVersion": "2.20251120.01.00", "hl": "en", "gl": "US"}
# YouTube's own "sp" values: type video; HD; 4K; Creative Commons (reuse allowed).
FILTERS = {"any": "EgIQAQ==", "hd": "EgIgAQ==", "4k": "EgJwAQ==", "cc": "EgIwAQ=="}
_VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")
_TOKEN = re.compile(r"^[0-9A-Za-z_=%-]{8,4096}$")
MAX_NODES = 200_000


def _text(node: object) -> str:
    """simpleText, or the joined runs of a YouTube text object."""
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(run.get("text", "") for run in runs if isinstance(run, dict) and isinstance(run.get("text"), str))
    return ""


def _views(text: str) -> int | None:
    """'3,576 views' -> 3576; 'No views' -> 0; anything shortened ('1.2M') stays unknown."""
    text = text.strip().lower()
    if text.startswith("no views"):
        return 0
    match = re.fullmatch(r"([\d,]+) views?", text)
    return optional_int(match.group(1).replace(",", "")) if match else None


def _duration(text: str) -> int | None:
    parts = text.strip().split(":")
    if len(parts) == 3:
        hours = optional_int(parts[0])
        rest = duration_seconds(":".join(parts[1:]))
        return None if hours is None or rest is None else hours * 3600 + rest
    return duration_seconds(text) if len(parts) == 2 else None


def _cover(renderer: dict) -> str:
    thumbnails = (renderer.get("thumbnail") or {}).get("thumbnails")
    if not isinstance(thumbnails, list):
        return ""
    urls = [item.get("url") for item in thumbnails if isinstance(item, dict) and isinstance(item.get("url"), str)]
    # The largest is last; only YouTube's image host is kept.
    for url in reversed(urls):
        if url.startswith("https://i.ytimg.com/"):
            return url
    return ""


def _video(renderer: dict) -> VideoItem | None:
    video_id = renderer.get("videoId")
    title = _text(renderer.get("title")).strip()
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or not title:
        return None
    owner = renderer.get("ownerText") or renderer.get("longBylineText")
    runs = owner.get("runs") if isinstance(owner, dict) else None
    browse = ""
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        endpoint = (runs[0].get("navigationEndpoint") or {}).get("browseEndpoint") or {}
        browse = endpoint.get("browseId") if isinstance(endpoint.get("browseId"), str) else ""
    badges = []
    for badge in renderer.get("badges") or ():
        label = (badge.get("metadataBadgeRenderer") or {}).get("label") if isinstance(badge, dict) else None
        if isinstance(label, str) and label in ("4K", "8K", "HD", "CC", "VR180", "360°", "HDR"):
            badges.append(label)
    return VideoItem(
        "youtube", video_id, title[:300], _text(owner).strip()[:120], browse,
        _duration(_text(renderer.get("lengthText"))), _views(_text(renderer.get("viewCountText"))), None,
        _cover(renderer), "https://www.youtube.com/watch?v=" + video_id, badges=tuple(badges),
    )


def _walk(payload: dict) -> tuple[list[dict], str | None, bool]:
    """Video renderers in page order, the first continuation token, and whether a result list was seen at all."""
    videos: list[dict] = []
    token: str | None = None
    listed = False
    stack: list[object] = [payload]
    budget = MAX_NODES
    while stack and budget > 0:
        budget -= 1
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("videoRenderer"), dict):
            videos.append(node["videoRenderer"])
            continue
        if "itemSectionRenderer" in node or "appendContinuationItemsAction" in node:
            listed = True
        command = node.get("continuationCommand")
        if token is None and isinstance(command, dict) and isinstance(command.get("token"), str):
            token = command["token"]
        stack.extend(reversed(list(node.values())))
    return videos, token, listed


class YouTube(DiscoveryAdapter):
    platform = "youtube"

    def search(self, query: str, page: int = 1, *, cursor: str | None = None, quality: str = "any") -> DiscoveryPage:
        if quality not in FILTERS:
            raise ValueError("quality must be any, hd, 4k or cc")
        if cursor is not None and not _TOKEN.fullmatch(cursor):
            raise ValueError("invalid continuation")
        body: dict = {"context": {"client": dict(CLIENT)}}
        if cursor is None:
            body.update({"query": query, "params": FILTERS[quality]})
        else:
            body["continuation"] = cursor
        payload = self.client.json(self.platform, API, json_body=body)
        renderers, token, listed = _walk(payload)
        if not listed and not renderers:
            raise DiscoveryError("BAD_RESPONSE", "Missing YouTube result list.")
        items = tuple(item for item in (_video(renderer) for renderer in renderers) if item is not None)
        if renderers and not items:
            raise DiscoveryError("BAD_RESPONSE", "No usable video in the YouTube response.")
        return DiscoveryPage(items, next_cursor=token if items and token and _TOKEN.fullmatch(token) else None)
