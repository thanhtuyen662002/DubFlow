"""Fixed request rules for public media CDNs that refuse plain requests (#982).

Xinpianchang's CDN (``*.xpccdn.com``) serves its signed MP4 links only to a
request that carries the site's Referer **and** a byte Range: a plain GET of
the whole file is refused with 403, ``Range: bytes=0-`` gets the whole file as
206. The rules are fixed per host suffix: a URL never chooses a value, and no
cookie or authorization is ever added. Such links are inspected directly
(yt-dlp's generic extractor makes the plain GET the CDN refuses).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SiteRule:
    referer: str
    # Ask for "bytes=0-" when the caller asks for no range.
    range_only: bool


RULES = {"xpccdn.com": SiteRule("https://www.xinpianchang.com/", True)}


def rule_for(url: str) -> SiteRule | None:
    """The rule of a public media URL's host, or None."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    return next((rule for suffix, rule in RULES.items() if host == suffix or host.endswith("." + suffix)), None)


__all__ = ["RULES", "SiteRule", "rule_for"]
