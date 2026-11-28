"""SkyPixel (DJI's drone community) video search and media lookup (#978).

The site's public JSON API needs no session. Search results carry the upload's
resolution and duration; the playable renditions (``cdn_url``: sd, 720p,
1080p MP4 on DJI's video CDN) and the licence come from the video's own
record, asked for only when the user adds that video.
"""

import re
from urllib.parse import quote, urlsplit

from ._base import DiscoveryAdapter, object_at, rows_at
from .http import DiscoveryError
from .models import DiscoveryPage, VideoItem, optional_int


API = "https://www.skypixel.com/api/v2"
PAGE_SIZE = 20
_SLUG = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,159}$")
# DJI's video CDN (cn-videos.dji.net, ...) and image hosts.
MEDIA_HOSTS = ("dji.net", "djicdn.com", "djivideos.com")
IMAGE_HOSTS = ("djiits.com", "dji.net", "djicdn.com")


def _on(url: object, domains: tuple[str, ...]) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    ok = parts.scheme == "https" and parts.username is None and parts.port is None and any(
        host == domain or host.endswith("." + domain) for domain in domains)
    return url if ok else ""


def _video(row: dict) -> VideoItem | None:
    slug, title = row.get("slug"), row.get("title")
    if row.get("type") not in (None, "video") or not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        return None
    title = title.strip() if isinstance(title, str) else ""
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    image = row.get("image") if isinstance(row.get("image"), dict) else {}
    width, height = optional_int(row.get("width")), optional_int(row.get("height"))
    return VideoItem(
        "skypixel", slug, (title or slug)[:300],
        str(user.get("name") or "")[:120], str(user.get("slug") or "")[:80],
        optional_int(row.get("duration")), optional_int(row.get("view_count")), optional_int(row.get("like_count")),
        _on(image.get("medium") or image.get("small"), IMAGE_HOSTS),
        "https://www.skypixel.com/videos/" + quote(slug, safe="-_"),
        width=width or None, height=height or None,
    )


class SkyPixel(DiscoveryAdapter):
    platform = "skypixel"

    def search(self, query: str, page: int = 1) -> DiscoveryPage:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 50:
            raise ValueError("page must be between 1 and 50")
        payload = self.client.json(self.platform, API + "/search/videos", params={
            "keyword": query, "limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE})
        rows = rows_at(payload, "data", "items")
        items = tuple(item for item in (_video(row) for row in rows) if item is not None)
        if rows and not items:
            raise DiscoveryError("BAD_RESPONSE", "No usable video in the SkyPixel response.")
        return DiscoveryPage(items, page + 1 if len(rows) >= PAGE_SIZE and page < 50 else None)

    def media(self, slug: str) -> dict:
        """The video's best MP4 rendition (1080p when there is one) and its licence."""
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise ValueError("invalid SkyPixel video id")
        item = object_at(self.client.json(self.platform, API + "/videos/" + quote(slug, safe="-_")), "data", "item")
        if item.get("slug") != slug:
            raise DiscoveryError("BAD_RESPONSE", "SkyPixel returned another video.")
        renditions = item.get("cdn_url") if isinstance(item.get("cdn_url"), dict) else {}
        for key, label in (("large", "1080p"), ("medium", "720p"), ("small", "sd")):
            url = _on(renditions.get(key), MEDIA_HOSTS)
            if url and urlsplit(url).path.lower().endswith(".mp4"):
                licence = item.get("license") if isinstance(item.get("license"), str) else ""
                return {"url": url, "quality": label, "license": licence[:80],
                        "width": optional_int(item.get("width")), "height": optional_int(item.get("height"))}
        raise DiscoveryError("UNSUPPORTED", "This SkyPixel video has no downloadable rendition.")
