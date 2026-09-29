"""Deterministic SourceAdapter boundary.

The shipped product will provide provider adapters behind this module.  The
fixture adapter below intentionally performs no network I/O, owns no durable
state and returns the same identity/page/error shapes as a live adapter.  This
keeps provider breakage isolated from the core queue and required CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import posixpath
import re
from typing import Iterable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SOURCE_CONTRACT_VERSION = 1
MAX_TEXT = 4096
MAX_URL = 4096
MAX_CURSOR = 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_LANGUAGE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
_TRACKING_QUERY = {"fbclid", "gclid", "spm_id_from"}


class SourceErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    NOT_FOUND = "NOT_FOUND"
    PRIVATE = "PRIVATE"
    NETWORK = "NETWORK"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_INPUT = "INVALID_INPUT"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"


def _text(value: object, name: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise SourceError(SourceErrorCode.INVALID_INPUT, f"{name} must be non-empty, bounded and control-free")
    return value.strip()


def _provider(value: object) -> str:
    value = _text(value, "provider_id", limit=64).lower()
    if not _PROVIDER_ID.fullmatch(value):
        raise SourceError(SourceErrorCode.INVALID_INPUT, "provider_id has an invalid format")
    return value


def canonicalize_url(value: str) -> str:
    """Canonicalize a display/lookup URL without exposing credentials.

    Fragments and common analytics parameters do not identify a source.  Other
    query parameters are retained because signed media URLs may depend on them;
    source identity itself is always provider + provider-owned source_id.
    """

    value = _text(value, "url", limit=MAX_URL)
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "source URL must be http(s) with a host")
    if parts.username is not None or parts.password is not None:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "source URL must not contain credentials")
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    if port is not None and not ((parts.scheme.lower() == "http" and port == 80) or (parts.scheme.lower() == "https" and port == 443)):
        host = f"{host}:{port}"
    path = posixpath.normpath(parts.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if parts.path.endswith("/") and not path.endswith("/"):
        path += "/"
    query_pairs = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY and not key.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query_pairs, doseq=True), ""))


@dataclass(frozen=True)
class SourceIdentity:
    provider_id: str
    source_id: str
    canonical_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _provider(self.provider_id))
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id", limit=512))
        object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url))

    @property
    def identity_key(self) -> str:
        return f"{self.provider_id}:{self.source_id}"

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "source_id": self.source_id,
            "canonical_url": self.canonical_url,
            "identity_key": self.identity_key,
        }


@dataclass(frozen=True)
class MediaCandidate:
    candidate_id: str
    locator: str
    kind: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    has_audio: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id", limit=256))
        object.__setattr__(self, "locator", _text(self.locator, "media.locator", limit=MAX_URL))
        object.__setattr__(self, "kind", _text(self.kind, "media.kind", limit=32))
        if self.kind not in {"progressive", "hls", "dash", "local"}:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "unsupported media candidate kind")
        object.__setattr__(self, "mime_type", _text(self.mime_type, "media.mime_type", limit=128))
        for name, value in (("width", self.width), ("height", self.height)):
            if value is not None and (type(value) is not int or not 1 <= value <= (1 << 64) - 1):
                raise SourceError(SourceErrorCode.INVALID_INPUT, f"media.{name} is outside the u64 range")
        if type(self.has_audio) is not bool:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "media.has_audio must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "locator": self.locator,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
        }


@dataclass(frozen=True)
class SubtitleCandidate:
    candidate_id: str
    locator: str
    language: str
    format: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id", limit=256))
        object.__setattr__(self, "locator", _text(self.locator, "subtitle.locator", limit=MAX_URL))
        language = _text(self.language, "subtitle.language", limit=32)
        if not _LANGUAGE.fullmatch(language):
            raise SourceError(SourceErrorCode.INVALID_INPUT, "subtitle language is invalid")
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "format", _text(self.format, "subtitle.format", limit=16).lower())
        if self.format not in {"srt", "vtt", "ass", "json"}:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "unsupported subtitle format")

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.candidate_id,
            "locator": self.locator,
            "language": self.language,
            "format": self.format,
        }


@dataclass(frozen=True)
class SourceItem:
    identity: SourceIdentity
    title: str
    description: str | None = None
    duration_ticks: int | None = None
    media_candidates: tuple[MediaCandidate, ...] = ()
    subtitle_candidates: tuple[SubtitleCandidate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _text(self.title, "title"))
        if self.description is not None:
            object.__setattr__(self, "description", _text(self.description, "description", limit=16_384))
        if self.duration_ticks is not None and (type(self.duration_ticks) is not int or not 0 <= self.duration_ticks <= (1 << 64) - 1):
            raise SourceError(SourceErrorCode.INVALID_INPUT, "duration_ticks is outside the u64 range")
        if len(self.media_candidates) > 256 or len(self.subtitle_candidates) > 256:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "source candidate list is too large")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": SOURCE_CONTRACT_VERSION,
            "identity": self.identity.to_dict(),
            "title": self.title,
            "description": self.description,
            "duration_ticks": None if self.duration_ticks is None else str(self.duration_ticks),
            "media_candidates": [candidate.to_dict() for candidate in self.media_candidates],
            "subtitle_candidates": [candidate.to_dict() for candidate in self.subtitle_candidates],
        }
        return result


@dataclass(frozen=True)
class PageCursor:
    provider_id: str
    channel_id: str
    offset: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _provider(self.provider_id))
        object.__setattr__(self, "channel_id", _text(self.channel_id, "channel_id", limit=512))
        if type(self.offset) is not int or not 0 <= self.offset <= (1 << 64) - 1:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "cursor offset is outside the u64 range")

    def encode(self) -> str:
        value = json.dumps({"v": 1, "provider": self.provider_id, "channel": self.channel_id, "offset": self.offset}, separators=(",", ":"))
        if len(value) > MAX_CURSOR:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "cursor is too large")
        return value

    @classmethod
    def decode(cls, value: str, *, provider_id: str, channel_id: str) -> "PageCursor":
        try:
            parsed = json.loads(_text(value, "cursor", limit=MAX_CURSOR))
        except json.JSONDecodeError as exc:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "cursor is not valid JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("v") != 1 or parsed.get("provider") != _provider(provider_id) or parsed.get("channel") != channel_id:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "cursor does not belong to this provider/channel")
        return cls(provider_id, channel_id, parsed.get("offset"))


@dataclass(frozen=True)
class SourcePage:
    items: tuple[SourceItem, ...]
    next_cursor: str | None
    completed: bool

    def __post_init__(self) -> None:
        if len(self.items) > 1000:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "source page is too large")
        if self.next_cursor is not None:
            _text(self.next_cursor, "next_cursor", limit=MAX_CURSOR)
        if type(self.completed) is not bool:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "completed must be boolean")
        if self.completed and self.next_cursor is not None:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "completed pages cannot have a next cursor")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SOURCE_CONTRACT_VERSION,
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "completed": self.completed,
        }


class SourceError(ValueError):
    def __init__(
        self,
        code: SourceErrorCode,
        condition: str,
        *,
        provider_id: str | None = None,
        source_id: str | None = None,
        retryable: bool = False,
        action: str | None = None,
    ) -> None:
        self.code = SourceErrorCode(code)
        self.condition = _text(condition, "condition", limit=MAX_TEXT)
        self.provider_id = None if provider_id is None else _provider(provider_id)
        self.source_id = None if source_id is None else _text(source_id, "source_id", limit=512)
        if type(retryable) is not bool:
            raise ValueError("retryable must be boolean")
        self.retryable = retryable
        self.action = None if action is None else _text(action, "action", limit=MAX_TEXT)
        super().__init__(f"{self.code.value}: {self.condition}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SOURCE_CONTRACT_VERSION,
            "code": self.code.value,
            "condition": self.condition,
            "provider_id": self.provider_id,
            "source_id": self.source_id,
            "retryable": self.retryable,
            "action": self.action,
        }


class SourceAdapter(Protocol):
    provider_id: str

    def inspect(self, source_ref: str) -> SourceItem:
        ...

    def enumerate_channel(self, channel_id: str, *, cursor: str | None = None, page_size: int = 50) -> SourcePage:
        ...


class FixtureSourceAdapter:
    """Offline SourceAdapter used by deterministic tests and local development."""

    def __init__(
        self,
        provider_id: str,
        items: Iterable[SourceItem] = (),
        channels: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.provider_id = _provider(provider_id)
        self._items: dict[str, SourceItem] = {}
        self._aliases: dict[str, str] = {}
        for item in items:
            if item.identity.provider_id != self.provider_id:
                raise SourceError(SourceErrorCode.INVALID_INPUT, "fixture item provider mismatch")
            key = item.identity.identity_key
            if key in self._items:
                raise SourceError(SourceErrorCode.INVALID_INPUT, "duplicate fixture source identity")
            self._items[key] = item
            self._aliases[item.identity.canonical_url] = key
        self._channels: dict[str, tuple[str, ...]] = {}
        for channel_id, source_ids in (channels or {}).items():
            channel = _text(channel_id, "channel_id", limit=512)
            keys = tuple(f"{self.provider_id}:{_text(source_id, 'source_id', limit=512)}" for source_id in source_ids)
            missing = [key for key in keys if key not in self._items]
            if missing:
                raise SourceError(SourceErrorCode.INVALID_INPUT, f"channel references unknown source {missing[0]}")
            if len(set(keys)) != len(keys):
                raise SourceError(SourceErrorCode.INVALID_INPUT, "channel contains duplicate source identity")
            self._channels[channel] = keys

    def inspect(self, source_ref: str) -> SourceItem:
        value = _text(source_ref, "source_ref", limit=MAX_URL)
        key = self._aliases.get(canonicalize_url(value))
        if key is None:
            key = f"{self.provider_id}:{value}" if not value.startswith(("http://", "https://")) else None
        if key is None or key not in self._items:
            raise SourceError(SourceErrorCode.NOT_FOUND, "fixture source was not found", provider_id=self.provider_id, source_id=value)
        return self._items[key]

    def enumerate_channel(self, channel_id: str, *, cursor: str | None = None, page_size: int = 50) -> SourcePage:
        channel = _text(channel_id, "channel_id", limit=512)
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "page_size must be between 1 and 1000")
        keys = self._channels.get(channel)
        if keys is None:
            raise SourceError(SourceErrorCode.NOT_FOUND, "fixture channel was not found", provider_id=self.provider_id, source_id=channel)
        offset = 0 if cursor is None else PageCursor.decode(cursor, provider_id=self.provider_id, channel_id=channel).offset
        if offset > len(keys):
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "cursor is beyond the channel end", provider_id=self.provider_id, source_id=channel)
        page_keys = keys[offset : offset + page_size]
        next_offset = offset + len(page_keys)
        completed = next_offset == len(keys)
        next_cursor = None if completed else PageCursor(self.provider_id, channel, next_offset).encode()
        return SourcePage(tuple(self._items[key] for key in page_keys), next_cursor, completed)
