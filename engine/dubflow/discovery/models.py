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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryPage:
    items: tuple[TrendItem | VideoItem, ...]
    next_page: int | None = None

    def to_dict(self) -> dict:
        return {"items": [item.to_dict() for item in self.items],
                "next_page": self.next_page}


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
