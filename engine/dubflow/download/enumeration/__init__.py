"""Provider-neutral enumeration orchestration.

The downloader owns provider calls and page validation.  Durable records are
written by the supervisor queue (``crates/job-supervisor/source_queue``) via a
checkpoint sink supplied by the caller; this module deliberately never opens
or mutates SQLite.  Keeping that boundary explicit prevents a provider worker
from bypassing supervisor-owned state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..source_adapter import SourceAdapter, SourceError, SourceErrorCode, SourceItem, SourcePage


MAX_ITEMS = 10_000


class EnumerationError(ValueError):
    """A bounded, actionable enumeration failure."""

    def __init__(self, message: str, *, code: str = "CHECKPOINT_INVALID", retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class EnumerationCheckpoint:
    provider_id: str
    channel_id: str
    cursor: str | None = None
    seen_identity_keys: frozenset[str] = field(default_factory=frozenset)
    discovered_count: int = 0

    def __post_init__(self) -> None:
        if not self.provider_id or not self.channel_id:
            raise EnumerationError("provider and channel are required")
        if len(self.seen_identity_keys) > MAX_ITEMS or self.discovered_count != len(self.seen_identity_keys):
            raise EnumerationError("checkpoint exceeds the bounded discovery capacity")
        if self.discovered_count < 0 or self.discovered_count > MAX_ITEMS:
            raise EnumerationError("checkpoint discovered_count is invalid")


@dataclass(frozen=True)
class EnumerationProgress:
    discovered: int = 0
    duplicates: int = 0
    failed: int = 0
    completed: bool = False


class CheckpointSink(Protocol):
    """Supervisor callback; implementation owns durable queue mutations."""

    def checkpoint_page(self, checkpoint: EnumerationCheckpoint, page: SourcePage) -> None:
        ...


class EnumerationCoordinator:
    """Run bounded source pages while keeping durable state outside the worker."""

    def __init__(self, adapter: SourceAdapter, sink: CheckpointSink | None = None, *, max_items: int = MAX_ITEMS) -> None:
        if type(max_items) is not int or not 1 <= max_items <= MAX_ITEMS:
            raise EnumerationError("max_items must be between 1 and 10000")
        self.adapter = adapter
        self.sink = sink
        self.max_items = max_items

    def step(
        self,
        channel_id: str,
        *,
        checkpoint: EnumerationCheckpoint | None = None,
        page_size: int = 50,
    ) -> tuple[EnumerationCheckpoint, EnumerationProgress, SourcePage]:
        if checkpoint is not None and (checkpoint.provider_id != self.adapter.provider_id or checkpoint.channel_id != channel_id):
            raise EnumerationError("checkpoint belongs to another provider/channel")
        previous_cursor = None if checkpoint is None else checkpoint.cursor
        try:
            page = self.adapter.enumerate_channel(channel_id, cursor=previous_cursor, page_size=page_size)
        except SourceError as error:
            raise EnumerationError(str(error), code=error.code.value, retryable=error.retryable) from error
        seen = set() if checkpoint is None else set(checkpoint.seen_identity_keys)
        discovered = duplicates = 0
        for item in page.items:
            if item.identity.provider_id != self.adapter.provider_id:
                raise EnumerationError("enumeration item provider does not match adapter", code=SourceErrorCode.SOURCE_CHANGED.value)
            key = item.identity.identity_key
            if key in seen:
                duplicates += 1
                continue
            if len(seen) >= self.max_items:
                raise EnumerationError("discovery queue capacity reached", code="CAPACITY")
            seen.add(key)
            discovered += 1
        if not page.completed and page.next_cursor == previous_cursor:
            raise EnumerationError("enumeration made no cursor progress")
        next_checkpoint = EnumerationCheckpoint(self.adapter.provider_id, channel_id, page.next_cursor, frozenset(seen), len(seen))
        if self.sink is not None:
            self.sink.checkpoint_page(next_checkpoint, page)
        return next_checkpoint, EnumerationProgress(discovered, duplicates, len(page.failures), page.completed), page

    def run(
        self,
        channel_id: str,
        *,
        checkpoint: EnumerationCheckpoint | None = None,
        page_size: int = 50,
        cancel: Callable[[], bool] | None = None,
        max_pages: int | None = None,
    ) -> tuple[EnumerationCheckpoint, EnumerationProgress]:
        if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
            raise EnumerationError("max_pages must be positive")
        current = checkpoint
        total = EnumerationProgress()
        page_count = 0
        while not total.completed:
            if cancel is not None and cancel():
                if current is None:
                    raise EnumerationError("scan cancelled before the first checkpoint", code="CANCELLED")
                return current, total
            if max_pages is not None and page_count >= max_pages:
                if current is None:
                    raise EnumerationError("scan did not produce a checkpoint")
                return current, total
            current, progress, _page = self.step(channel_id, checkpoint=current, page_size=page_size)
            page_count += 1
            total = EnumerationProgress(total.discovered + progress.discovered, total.duplicates + progress.duplicates, total.failed + progress.failed, progress.completed)
        assert current is not None
        return current, total


__all__ = ["CheckpointSink", "EnumerationCheckpoint", "EnumerationCoordinator", "EnumerationError", "EnumerationProgress"]
