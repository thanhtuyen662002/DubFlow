"""Preflight: refuse any third-party API request that goes beyond a source's confirmed processing scope ($0).

The manifest says per source whether data may leave the test machine (``third_party_processing_allowed``) and
how much of it (``third_party_processing_scope``). A boolean alone is not enough: the REAL-DEV Owner decision
(2026-11-15) allows the transcript text and nothing else. This module checks a request *before* it is sent:

- every source the request carries must be named, cleared (``True``) and have a known scope;
- the narrowest scope of those sources applies to the whole request;
- ``transcript_text_only``: only text content blocks; no image, document, audio, video or file block; no media
  smuggled into text (``data:`` URIs, media URLs, long base64 runs, the source's own file path or name).

Passing preflight is a data-scope answer only. It never means money may be spent: the cost gate is the
``Ledger`` in ``script_draft.py`` and needs its own Owner approval (1a, then 1b separately).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

TRANSCRIPT_TEXT_ONLY = "transcript_text_only"
SCOPES = (TRANSCRIPT_TEXT_ONLY,)  # known scopes, narrowest first; anything else is refused
TEXT_BLOCKS = frozenset({"text"})
_MEDIA_EXT = r"(?:mp4|mov|mkv|webm|avi|m4v|m4a|mp3|wav|aac|flac|ogg|opus|jpg|jpeg|png|webp|gif|bmp|tiff?|heic)"
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,", re.IGNORECASE)
_MEDIA_URL = re.compile(r"\b(?:https?|ftp|file|s3|gs)://\S+?\." + _MEDIA_EXT + r"\b", re.IGNORECASE)
_MEDIA_FILE = re.compile(r"[\w\-. ]+\." + _MEDIA_EXT + r"\b", re.IGNORECASE)
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


class ScopeError(ValueError):
    """The request is BLOCKED: it carries more than the sources allow (or the scope is unknown)."""


def scope_of(source: Mapping[str, Any]) -> str:
    """The source's processing scope, or ScopeError when it is not cleared or not known."""
    sid = source.get("source_id") or "?"
    if source.get("third_party_processing_allowed") is not True:
        raise ScopeError(f"{sid}: third-party processing not cleared")
    raw = source.get("third_party_processing_scope")
    if not isinstance(raw, str) or not raw.strip():
        raise ScopeError(f"{sid}: third_party_processing_scope missing")
    name = raw.split(":", 1)[0].strip()
    if name not in SCOPES:
        raise ScopeError(f"{sid}: unknown third_party_processing_scope {name!r}")
    return name


def _blocks(request: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    system = request.get("system")
    if isinstance(system, str):
        yield {"type": "text", "text": system}
    elif system is not None:
        yield from system
    for message in request.get("messages") or ():
        content = message.get("content")
        if isinstance(content, str):
            yield {"type": "text", "text": content}
        else:
            for block in content or ():
                yield block


def _forbidden_text(text: str, source_names: Iterable[str]) -> str | None:
    for pattern, what in ((_DATA_URI, "data URI"), (_MEDIA_URL, "media URL"), (_BASE64_RUN, "base64 run")):
        if pattern.search(text):
            return what
    folded = text.casefold()
    for name in source_names:
        if name and name.casefold() in folded:
            return f"source media name {name!r}"
    if _MEDIA_FILE.search(text):
        return "media file name"
    return None


def preflight(request: Mapping[str, Any], sources: Iterable[Mapping[str, Any]]) -> str:
    """The scope the request falls under, or ScopeError (BLOCKED). ``sources``: the manifest entries it carries."""
    sources = list(sources)
    if not sources:
        raise ScopeError("the request names no source, so its scope cannot be checked")
    scopes = [scope_of(s) for s in sources]
    scope = min(scopes, key=SCOPES.index)  # the narrowest applies to the whole request
    names = set()
    for s in sources:
        path = str(s.get("path") or "").replace("\\", "/")
        if path:
            names.update({path, path.rsplit("/", 1)[-1]})
    if request.get("tools") or request.get("mcp_servers") or request.get("container"):
        raise ScopeError("tools, MCP servers or containers can fetch media: BLOCKED for transcript_text_only")
    for block in _blocks(request):
        kind = block.get("type")
        if kind not in TEXT_BLOCKS:
            raise ScopeError(f"{kind!r} content block: BLOCKED for {scope} (only text may be sent)")
        what = _forbidden_text(str(block.get("text") or ""), names)
        if what:
            raise ScopeError(f"{what} inside text: BLOCKED for {scope}")
    return scope
