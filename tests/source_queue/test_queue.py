from __future__ import annotations

import unittest

from engine.dubflow.download.acquisition_queue import AcquisitionQueue, QueueCheckpoint, QueueError
from engine.dubflow.download.source_adapter import FixtureSourceAdapter, MediaCandidate, SourceError, SourceIdentity, SourceItem


def item(source_id: str) -> SourceItem:
    return SourceItem(
        SourceIdentity("fixture", source_id, f"https://example.test/watch/{source_id}"),
        f"Item {source_id}",
        media_candidates=(MediaCandidate("media", f"fixture://{source_id}", "local", "video/mp4"),),
    )


class AcquisitionQueueTests(unittest.TestCase):
    def test_multi_url_deduplicates_aliases_and_isolates_missing_item(self) -> None:
        first = item("one")
        adapter = FixtureSourceAdapter("fixture", [first])
        progress = AcquisitionQueue().add_urls(adapter, [first.identity.canonical_url, first.identity.canonical_url + "#fragment", "https://example.test/watch/missing"])
        self.assertEqual((progress.discovered, progress.duplicates, progress.failed), (1, 1, 1))

    def test_channel_checkpoint_resumes_without_restarting_page_one(self) -> None:
        records = [item(str(index)) for index in range(5)]
        adapter = FixtureSourceAdapter("fixture", records, {"creator": [str(index) for index in range(5)]})
        queue = AcquisitionQueue()
        checkpoint, first = queue.resume_channel(adapter, "creator", page_size=2)
        self.assertEqual((first.discovered, first.completed), (2, False))
        encoded = QueueCheckpoint.decode(checkpoint.encode())
        checkpoint, second = queue.resume_channel(adapter, "creator", checkpoint=encoded, page_size=2)
        self.assertEqual((second.discovered, second.duplicates), (2, 0))
        checkpoint, third = queue.resume_channel(adapter, "creator", checkpoint=checkpoint, page_size=2)
        self.assertEqual((third.discovered, third.completed), (1, True))
        self.assertEqual(len(queue.items), 5)
        self.assertEqual(checkpoint.discovered_count, 5)

    def test_scan_handles_ten_thousand_items_with_bounded_checkpoint(self) -> None:
        records = [item(str(index)) for index in range(10_000)]
        adapter = FixtureSourceAdapter("fixture", records, {"creator": [str(index) for index in range(10_000)]})
        queue = AcquisitionQueue()
        checkpoint, progress = queue.scan_channel(adapter, "creator", page_size=1000)
        self.assertTrue(progress.completed)
        self.assertEqual(progress.discovered, 10_000)
        self.assertEqual(len(checkpoint.seen_identity_keys), 10_000)
        self.assertEqual(len(queue.items), 10_000)

    def test_capacity_failure_is_explicit(self) -> None:
        records = [item("one"), item("two")]
        adapter = FixtureSourceAdapter("fixture", records, {"creator": ["one", "two"]})
        with self.assertRaises(QueueError):
            AcquisitionQueue(max_items=1).scan_channel(adapter, "creator", page_size=2)


if __name__ == "__main__":
    unittest.main()
