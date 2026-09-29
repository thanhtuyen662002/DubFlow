"""Provider-independent source acquisition contracts and fixture adapters."""

from .source_adapter import (
    FixtureSourceAdapter,
    MediaCandidate,
    PageCursor,
    SourceError,
    SourceErrorCode,
    SourceIdentity,
    SourceItem,
    SourcePage,
    SubtitleCandidate,
    canonicalize_url,
)

__all__ = [
    "FixtureSourceAdapter",
    "MediaCandidate",
    "PageCursor",
    "SourceError",
    "SourceErrorCode",
    "SourceIdentity",
    "SourceItem",
    "SourcePage",
    "SubtitleCandidate",
    "canonicalize_url",
]
