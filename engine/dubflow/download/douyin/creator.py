"""Provider cursor pages; only the supervisor commits durable source state."""
from __future__ import annotations

from hashlib import sha256
import json
import re

from ..source_adapter import SourceError, SourceErrorCode, SourceIdentity, SourceItem, SourcePage, SourcePageFailure

MAX_ITEMS = 10_000
MAX_PAGE = 20
MAX_POSITION = 2**63 - 1


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cursor key")
        result[key] = value
    return result


def creator_page(transport, url: str, *, cursor: str | None, page_size: int, session=None) -> SourcePage:
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "invalid Douyin creator page size", provider_id="douyin")
    recipe = sha256(json.dumps({"recipe": "douyin-creator-api-v2", "pins": transport.pins}, sort_keys=True).encode()).hexdigest()
    binding = sha256(url.encode()).hexdigest()
    position, offset = 0, 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor.encode()) > 1024:
                raise ValueError("cursor bound")
            value = json.loads(cursor, object_pairs_hook=_unique_object)
            if (not isinstance(value, dict) or set(value) != {"v", "provider", "channel", "recipe", "position", "offset"}
                    or type(value["v"]) is not int or value["v"] != 2 or value["provider"] != "douyin"
                    or value["channel"] != binding or value["recipe"] != recipe
                    or type(value["position"]) is not int or not 0 < value["position"] <= MAX_POSITION
                    or type(value["offset"]) is not int or not 0 < value["offset"] < MAX_ITEMS):
                raise ValueError("cursor identity")
            position, offset = value["position"], value["offset"]
        except (ValueError, TypeError, KeyError):
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID,
                "Douyin cursor requires its pinned producer and channel or an explicit new scan", provider_id="douyin") from None
    fetch = getattr(transport, "enumerate_douyin_page", None)
    if not callable(fetch):
        raise SourceError(SourceErrorCode.UNSUPPORTED, "owned Douyin creator backend is unavailable", provider_id="douyin")
    size = min(page_size, MAX_PAGE, MAX_ITEMS - offset)
    raw = fetch(url, position=position, page_size=size, headers=session)
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "max_cursor", "has_more", "entries"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or type(raw["has_more"]) is not bool or type(raw["max_cursor"]) is not int
            or not 0 <= raw["max_cursor"] <= MAX_POSITION or not isinstance(raw["entries"], list)
            or len(raw["entries"]) > size
            or (raw["has_more"] and (not raw["entries"] or raw["max_cursor"] == 0
                or (position > 0 and raw["max_cursor"] >= position)))):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin creator page is malformed or made no progress", provider_id="douyin")
    items, failures = [], []
    for index, entry in enumerate(raw["entries"]):
        fallback = f"unavailable-at-{offset + index + 1}"
        if entry is None:
            failures.append(SourcePageFailure(fallback, SourceErrorCode.NOT_FOUND, "creator item is unavailable"))
            continue
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{6,32}", identifier):
            failures.append(SourcePageFailure(fallback, SourceErrorCode.SOURCE_CHANGED, "creator item identity is malformed"))
            continue
        if set(entry) == {"id", "error_code"}:
            codes = {code.value: code for code in (SourceErrorCode.NOT_FOUND, SourceErrorCode.PRIVATE,
                     SourceErrorCode.UNSUPPORTED, SourceErrorCode.SOURCE_CHANGED)}
            code = codes.get(entry["error_code"]) if isinstance(entry["error_code"], str) else None
            failures.append(SourcePageFailure(identifier, code or SourceErrorCode.SOURCE_CHANGED,
                "creator item is unavailable or unsupported"))
            continue
        title = entry.get("title")
        if (set(entry) != {"id", "title"} or not isinstance(title, str) or not title.strip()
                or len(title) > 1024 or any(ord(ch) < 32 or ord(ch) == 127 for ch in title)):
            failures.append(SourcePageFailure(identifier, SourceErrorCode.SOURCE_CHANGED, "creator item metadata is malformed"))
            continue
        items.append(SourceItem(SourceIdentity("douyin", identifier, f"https://www.douyin.com/video/{identifier}"), title))
    next_offset = offset + len(raw["entries"])
    if raw["has_more"] and next_offset >= MAX_ITEMS:
        raise SourceError(SourceErrorCode.UNSUPPORTED, "Douyin creator exceeds the 10000 item discovery bound", provider_id="douyin")
    next_cursor = json.dumps({"v": 2, "provider": "douyin", "channel": binding, "recipe": recipe,
                             "position": raw["max_cursor"], "offset": next_offset}, separators=(",", ":")) if raw["has_more"] else None
    return SourcePage(tuple(items), next_cursor, not raw["has_more"], tuple(failures))
