"""Bounded SDK pages; caller/supervisor owns their durable checkpoint commits."""
from __future__ import annotations

from hashlib import sha256
import json
import re

from ..source_adapter import SourceError, SourceErrorCode, SourceIdentity, SourceItem, SourcePage, SourcePageFailure

MAX_ITEMS = 10000
MAX_PAGE = 100


def channel_url(provider: str, channel: str) -> str:
    if not isinstance(channel, str) or not 1 <= len(channel) <= 512:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "invalid channel reference", provider_id=provider)
    if provider == "bilibili":
        if re.fullmatch(r"[0-9]{1,20}", channel):
            return f"https://space.bilibili.com/{channel}/video"
        match = re.fullmatch(r"https://space\.bilibili\.com/([0-9]{1,20})(?:/(?:upload/)?video)?/?", channel)
        if match:
            return f"https://space.bilibili.com/{match[1]}/video"
    elif provider == "douyin":
        identifier = channel
        if channel.startswith("https://"):
            match = re.fullmatch(r"https://(?:www\.)?douyin\.com/user/([A-Za-z0-9_-]{1,128})/?", channel)
            identifier = match[1] if match else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            return f"https://www.douyin.com/user/{identifier}"
    raise SourceError(SourceErrorCode.INVALID_INPUT, "invalid provider channel reference", provider_id=provider)


def sdk_page(transport, provider: str, channel: str, *, cursor: str | None, page_size: int, session=None) -> SourcePage:
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "channel page size must be between 1 and 100", provider_id=provider)
    url = channel_url(provider, channel)
    # SDK/helper bytes are pinned by the verified runtime. A new recipe cannot
    # resume an old offset under changed extraction behavior.
    recipe = sha256(json.dumps({"recipe": "flat-sdk-channel-v1", "pins": transport.pins}, sort_keys=True).encode()).hexdigest()
    binding = sha256(url.encode()).hexdigest()
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor.encode()) > 1024:
                raise ValueError("cursor bound")
            value = json.loads(cursor)
            if (not isinstance(value, dict) or set(value) != {"v", "provider", "channel", "recipe", "offset"}
                    or type(value["v"]) is not int or value["v"] != 1 or value["provider"] != provider
                    or value["channel"] != binding or value["recipe"] != recipe
                    or type(value["offset"]) is not int or not 0 < value["offset"] < MAX_ITEMS):
                raise ValueError("cursor identity")
            offset = value["offset"]
        except (ValueError, TypeError, KeyError):
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "channel cursor does not match the pinned provider/channel recipe", provider_id=provider) from None
    size = min(page_size, MAX_ITEMS - offset)
    raw = transport.enumerate_url(url, provider_id=provider, offset=offset, page_size=size, headers=session)
    if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1
            or type(raw.get("offset")) is not int or raw["offset"] != offset or type(raw.get("has_more")) is not bool
            or not isinstance(raw.get("entries"), list) or len(raw["entries"]) > size
            or (raw["has_more"] and len(raw["entries"]) != size)):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "SDK channel page is malformed or made no progress", provider_id=provider)
    items, failures = [], []
    for index, entry in enumerate(raw["entries"]):
        fallback_id = f"unavailable-at-{offset + index + 1}"
        if isinstance(entry, dict) and entry.get("invalid") is True:
            failures.append(SourcePageFailure(fallback_id, SourceErrorCode.SOURCE_CHANGED, "channel video metadata is invalid"))
            continue
        identifier = entry.get("id") if isinstance(entry, dict) else None
        valid = (isinstance(identifier, str) and
            re.fullmatch(r"(?:BV[A-Za-z0-9]{10}|av[0-9]+)" if provider == "bilibili" else r"[0-9]{5,32}", identifier))
        if not valid:
            failures.append(SourcePageFailure(fallback_id, SourceErrorCode.NOT_FOUND if entry is None else SourceErrorCode.UNSUPPORTED,
                "channel item is unavailable or is not a supported video"))
            continue
        availability = entry.get("availability")
        if availability is not None and (not isinstance(availability, str) or len(availability) > 64
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in availability)):
            failures.append(SourcePageFailure(identifier, SourceErrorCode.SOURCE_CHANGED, "channel video availability is invalid"))
            continue
        if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
            failures.append(SourcePageFailure(identifier, SourceErrorCode.PRIVATE, "channel video requires authorized access"))
            continue
        title = entry.get("title", identifier)
        if not isinstance(title, str) or not title.strip() or len(title) > 1024 or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
            failures.append(SourcePageFailure(identifier, SourceErrorCode.SOURCE_CHANGED, "channel video metadata is invalid"))
            continue
        items.append(SourceItem(SourceIdentity(provider, identifier, f"https://www.{provider}.com/video/{identifier}"), title))
    next_offset = offset + len(raw["entries"])
    if raw["has_more"] and next_offset >= MAX_ITEMS:
        # Never call a truncated channel complete or publish a cursor that can
        # escape the bounded queue. Caller can retain the previous safe page.
        raise SourceError(SourceErrorCode.UNSUPPORTED, "channel exceeds the 10000 item discovery bound", provider_id=provider)
    next_cursor = json.dumps({"v": 1, "provider": provider, "channel": binding, "recipe": recipe,
                              "offset": next_offset}, separators=(",", ":")) if raw["has_more"] else None
    return SourcePage(tuple(items), next_cursor, not raw["has_more"], tuple(failures))
