"""Anonymous generic acquisition through the reviewed, pinned SDK boundary."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from ..source_adapter import SourceError, SourceErrorCode, SourceIdentity, SourceItem, SourcePage, SourcePageFailure, canonicalize_url

MAX_ITEMS = 10_000
MAX_PAGE = 100
RECIPE = "anonymous-generic-sdk-v1"
_CREDENTIAL_QUERY = {"token", "access_token", "auth", "authorization", "cookie", "password", "secret", "signature", "sig", "api_key", "session", "sessionid"}


def source_url(value: str) -> str:
    """Source/display URLs are public; signed CDN candidates stay in memory."""
    try:
        url = canonicalize_url(value)
        if any(key.casefold() in _CREDENTIAL_QUERY for key, _ in parse_qsl(urlsplit(url).query)):
            raise ValueError("credential query")
        return url
    except (SourceError, ValueError, TypeError):
        raise SourceError(SourceErrorCode.INVALID_INPUT, "generic source requires a public HTTP(S) URL without credentials", provider_id="generic") from None


def _text(value, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("invalid bounded metadata")
    return value.strip()


def sdk_identity(raw: Mapping) -> SourceIdentity:
    try:
        identifier = _text(raw.get("id"), maximum=512)
        extractor = _text(raw.get("ie_key") or raw.get("extractor_key"), maximum=128).casefold()
        canonical = source_url(raw.get("webpage_url") or raw.get("url"))
        # GenericIE IDs often come from a filename, shared across unrelated
        # sites. Its canonical page URL is part of that namespace instead.
        namespace = [RECIPE, extractor, identifier]
        if extractor == "generic":
            namespace.append(canonical)
        digest = sha256(json.dumps(namespace, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        return SourceIdentity("generic", "sdk-v1-" + digest, canonical)
    except (SourceError, ValueError, TypeError, AttributeError):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic SDK source identity is malformed", provider_id="generic") from None


class GenericSdkTransport:
    """No session bridge or custom request headers are granted to this adapter."""

    def __init__(self, transport):
        self._sdk = transport

    def inspect_url(self, url: str) -> Mapping:
        raw = self._sdk.inspect_url(source_url(url), provider_id="generic")
        if not isinstance(raw, dict):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic SDK metadata is malformed", provider_id="generic")
        identity = sdk_identity(raw)
        value = dict(raw, id=identity.source_id, webpage_url=identity.canonical_url)
        description = raw.get("description")
        if description is not None:
            if (not isinstance(description, str) or len(description) > 16_384
                    or any((ord(ch) < 32 and ch not in "\r\n\t") or ord(ch) == 127 for ch in description)):
                raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic SDK description is malformed", provider_id="generic")
            value["description"] = " ".join(description.split()) or None
        return value

    def enumerate_playlist(self, url: str, *, cursor: str | None, page_size: int) -> SourcePage:
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "playlist page size must be between 1 and 100", provider_id="generic")
        url = source_url(url)
        binding = sha256(url.encode()).hexdigest()
        recipe = sha256(json.dumps({"recipe": RECIPE, "pins": self._sdk.pins}, sort_keys=True).encode()).hexdigest()
        offset = 0
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor.encode()) > 1024:
                    raise ValueError("cursor bound")
                value = json.loads(cursor)
                if (not isinstance(value, dict) or set(value) != {"v", "url", "recipe", "offset"}
                        or type(value["v"]) is not int or value["v"] != 1 or value["url"] != binding
                        or value["recipe"] != recipe or type(value["offset"]) is not int
                        or not 0 < value["offset"] < MAX_ITEMS):
                    raise ValueError("cursor identity")
                offset = value["offset"]
            except (ValueError, TypeError):
                raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "playlist cursor does not match the pinned source recipe", provider_id="generic") from None
        size = min(page_size, MAX_ITEMS - offset)
        raw = self._sdk.enumerate_url(url, provider_id="generic", offset=offset, page_size=size)
        if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1
                or type(raw.get("offset")) is not int or raw["offset"] != offset or type(raw.get("has_more")) is not bool
                or not isinstance(raw.get("entries"), list) or len(raw["entries"]) > size
                or (raw["has_more"] and len(raw["entries"]) != size)):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "generic SDK playlist is malformed or made no progress", provider_id="generic")
        items, failures = [], []
        for index, entry in enumerate(raw["entries"]):
            fallback = f"unavailable-at-{offset + index + 1}"
            try:
                if entry is None:
                    failures.append(SourcePageFailure(fallback, SourceErrorCode.NOT_FOUND, "playlist video is unavailable"))
                    continue
                if not isinstance(entry, dict) or entry.get("invalid") is True:
                    raise ValueError("invalid entry")
                identity = sdk_identity(entry)
                availability = entry.get("availability")
                if availability is not None:
                    availability = _text(availability, maximum=64)
                if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
                    failures.append(SourcePageFailure(identity.source_id, SourceErrorCode.PRIVATE, "playlist video requires authorized access"))
                    continue
                title = _text(entry.get("title", entry.get("id")), maximum=1024)
                items.append(SourceItem(identity, title))
            except (SourceError, ValueError, TypeError):
                failures.append(SourcePageFailure(fallback, SourceErrorCode.SOURCE_CHANGED, "playlist video metadata is invalid"))
        next_offset = offset + len(raw["entries"])
        if raw["has_more"] and next_offset >= MAX_ITEMS:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "playlist exceeds the 10000 item discovery bound", provider_id="generic")
        next_cursor = json.dumps({"v": 1, "url": binding, "recipe": recipe, "offset": next_offset}, separators=(",", ":")) if raw["has_more"] else None
        return SourcePage(tuple(items), next_cursor, not raw["has_more"], tuple(failures))
