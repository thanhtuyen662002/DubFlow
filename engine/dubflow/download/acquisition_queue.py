"""Checkpointed multi-source discovery built on the SourceAdapter boundary."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from .source_adapter import SourceAdapter, SourceError, SourceErrorCode, SourceItem


MAX_DISCOVERED_ITEMS = 10_000
MAX_FAILURES = 10_000


class QueueError(ValueError):
    pass


def _safe_ref(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme and parts.hostname:
            return urlunsplit((parts.scheme, parts.hostname, parts.path, parts.query, ""))
    except ValueError:
        pass
    return "<local-or-invalid-source>"


@dataclass(frozen=True)
class DiscoveryFailure:
    source_ref: str
    code: str
    condition: str
    retryable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_ref": _safe_ref(self.source_ref),
            "code": self.code,
            "condition": self.condition,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class QueueCheckpoint:
    provider_id: str
    channel_id: str
    cursor: str | None
    seen_identity_keys: tuple[str, ...]
    discovered_count: int

    def __post_init__(self) -> None:
        if len(self.seen_identity_keys) > MAX_DISCOVERED_ITEMS:
            raise QueueError("checkpoint exceeds the bounded discovery capacity")
        if len(set(self.seen_identity_keys)) != len(self.seen_identity_keys):
            raise QueueError("checkpoint contains duplicate identity keys")
        if type(self.discovered_count) is not int or not 0 <= self.discovered_count <= MAX_DISCOVERED_ITEMS:
            raise QueueError("checkpoint discovered_count is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "provider_id": self.provider_id,
            "channel_id": self.channel_id,
            "cursor": self.cursor,
            "seen_identity_keys": list(self.seen_identity_keys),
            "discovered_count": self.discovered_count,
        }

    def encode(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def decode(cls, value: str) -> "QueueCheckpoint":
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise QueueError("checkpoint is not valid JSON") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise QueueError("unsupported queue checkpoint schema")
        provider = data.get("provider_id")
        channel = data.get("channel_id")
        cursor = data.get("cursor")
        keys = data.get("seen_identity_keys")
        count = data.get("discovered_count")
        if not isinstance(provider, str) or not isinstance(channel, str) or (cursor is not None and not isinstance(cursor, str)) or not isinstance(keys, list) or not all(isinstance(item, str) for item in keys):
            raise QueueError("queue checkpoint fields are malformed")
        return cls(provider, channel, cursor, tuple(keys), count)


@dataclass(frozen=True)
class DiscoveryProgress:
    discovered: int
    duplicates: int
    failed: int
    completed: bool


class AcquisitionQueue:
    def __init__(self, *, max_items: int = MAX_DISCOVERED_ITEMS) -> None:
        if type(max_items) is not int or not 1 <= max_items <= MAX_DISCOVERED_ITEMS:
            raise QueueError("max_items must be between 1 and 10000")
        self.max_items = max_items
        self._items: dict[str, SourceItem] = {}
        self._failures: list[DiscoveryFailure] = []

    @property
    def items(self) -> tuple[SourceItem, ...]:
        return tuple(self._items.values())

    @property
    def failures(self) -> tuple[DiscoveryFailure, ...]:
        return tuple(self._failures)

    def add_urls(self, adapter: SourceAdapter, source_refs: Iterable[str]) -> DiscoveryProgress:
        discovered = duplicates = failed = 0
        for source_ref in source_refs:
            try:
                item = adapter.inspect(source_ref)
            except SourceError as error:
                self._record_failure(source_ref, error)
                failed += 1
                continue
            if item.identity.identity_key in self._items:
                duplicates += 1
                continue
            self._admit(item)
            discovered += 1
        return DiscoveryProgress(discovered, duplicates, failed, True)

    def resume_channel(
        self,
        adapter: SourceAdapter,
        channel_id: str,
        *,
        checkpoint: QueueCheckpoint | None = None,
        page_size: int = 50,
    ) -> tuple[QueueCheckpoint, DiscoveryProgress]:
        if checkpoint is not None and (checkpoint.provider_id != adapter.provider_id or checkpoint.channel_id != channel_id):
            raise QueueError("checkpoint belongs to another provider/channel")
        page = adapter.enumerate_channel(channel_id, cursor=None if checkpoint is None else checkpoint.cursor, page_size=page_size)
        seen = set(() if checkpoint is None else checkpoint.seen_identity_keys)
        discovered = duplicates = failed = 0
        for item in page.items:
            key = item.identity.identity_key
            if key in seen or key in self._items:
                duplicates += 1
                seen.add(key)
                continue
            self._admit(item)
            seen.add(key)
            discovered += 1
        next_checkpoint = QueueCheckpoint(adapter.provider_id, channel_id, page.next_cursor, tuple(sorted(seen)), (0 if checkpoint is None else checkpoint.discovered_count) + discovered)
        return next_checkpoint, DiscoveryProgress(discovered, duplicates, failed, page.completed)

    def scan_channel(self, adapter: SourceAdapter, channel_id: str, *, checkpoint: QueueCheckpoint | None = None, page_size: int = 50) -> tuple[QueueCheckpoint, DiscoveryProgress]:
        current = checkpoint
        total = DiscoveryProgress(0, 0, 0, False)
        while not total.completed:
            current, page_progress = self.resume_channel(adapter, channel_id, checkpoint=current, page_size=page_size)
            total = DiscoveryProgress(total.discovered + page_progress.discovered, total.duplicates + page_progress.duplicates, total.failed + page_progress.failed, page_progress.completed)
        if current is None:
            raise QueueError("channel scan did not produce a checkpoint")
        return current, total

    def _admit(self, item: SourceItem) -> None:
        if len(self._items) >= self.max_items:
            raise QueueError("discovery queue capacity reached; checkpoint and resume with a larger batch")
        self._items[item.identity.identity_key] = item

    def _record_failure(self, source_ref: str, error: SourceError) -> None:
        if len(self._failures) >= MAX_FAILURES:
            return
        self._failures.append(DiscoveryFailure(_safe_ref(source_ref), error.code.value, error.condition, error.retryable))
