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
    SourcePageFailure,
    SubtitleCandidate,
    canonicalize_url,
)
from .materializer import DownloadError, DownloadErrorCode, DownloadResult, MediaMaterializer, UrllibHttpTransport
from .generic import GenericUrlAdapter, YtDlpTransport

__all__ = [
    "FixtureSourceAdapter",
    "MediaCandidate",
    "PageCursor",
    "SourceError",
    "SourceErrorCode",
    "SourceIdentity",
    "SourceItem",
    "SourcePage",
    "SourcePageFailure",
    "SubtitleCandidate",
    "canonicalize_url",
    "DownloadError",
    "DownloadErrorCode",
    "DownloadResult",
    "GenericUrlAdapter",
    "MediaMaterializer",
    "UrllibHttpTransport",
    "YtDlpTransport",
]
