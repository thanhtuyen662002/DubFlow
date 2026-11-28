"""Nullable, JSON-ready metadata; discovery never downloads media."""

from dataclasses import asdict, dataclass
from html import unescape
from html.parser import HTMLParser


@dataclass(frozen=True)
class TrendItem:
    platform: str
    keyword: str
    rank: int
    heat: int | None
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VideoItem:
    platform: str
    video_id: str
    title: str
    author: str
    author_id: str
    duration_s: int | None
    plays: int | None
    likes: int | None
    cover_url: str
    page_url: str
    # Only sources that say so (#978): the upload's picture size, and the
    # source's own quality/licence labels such as "4K" or "CC".
    width: int | None = None
    height: int | None = None
    badges: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        # Unknown sizes and no labels are left out, so older sources keep their exact shape.
        for key in ("width", "height"):
            if data[key] is None:
                del data[key]
        if data["badges"]:
            data["badges"] = list(data["badges"])
        else:
            del data["badges"]
        return data


@dataclass(frozen=True)
class DiscoveryPage:
    items: tuple[TrendItem | VideoItem, ...]
    next_page: int | None = None
    # Sources that page by token (YouTube) instead of by number.
    next_cursor: str | None = None

    def to_dict(self) -> dict:
        data = {"items": [item.to_dict() for item in self.items], "next_page": self.next_page}
        if self.next_cursor is not None:
            data["next_cursor"] = self.next_cursor
        return data


def optional_int(value: object) -> int | None:
    """Only actual nonnegative integers are counts; unknown is never zero."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def duration_seconds(value: object) -> int | None:
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        if len(parts) != 2:
            return None
        minutes, seconds = (optional_int(part) for part in parts)
        if minutes is None or seconds is None or seconds >= 60:
            return None
        return minutes * 60 + seconds
    return optional_int(value)


def normalize_cover(value: str) -> str:
    return "https:" + value if value.startswith("//") else value


class _TitleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_title(value: str) -> str:
    parser = _TitleText()
    parser.feed(value)
    parser.close()
    return unescape("".join(parser.parts))
