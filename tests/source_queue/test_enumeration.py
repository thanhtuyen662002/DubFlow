from __future__ import annotations

import unittest

from engine.dubflow.download.enumeration import EnumerationCheckpoint, EnumerationCoordinator, EnumerationError
from engine.dubflow.download.source_adapter import FixtureSourceAdapter, MediaCandidate, SourceIdentity, SourceItem


def source(source_id: str) -> SourceItem:
    return SourceItem(
        SourceIdentity("fixture", source_id, f"https://example.test/{source_id}"),
        source_id,
        media_candidates=(MediaCandidate("media", f"fixture://{source_id}", "local", "video/mp4"),),
    )


class Sink:
    def __init__(self) -> None:
        self.pages = []

    def checkpoint_page(self, checkpoint, page) -> None:
        self.pages.append((checkpoint, page))


class EnumerationCoordinatorTests(unittest.TestCase):
    def test_sink_receives_each_page_and_resume_is_deterministic(self) -> None:
        adapter = FixtureSourceAdapter("fixture", [source(str(i)) for i in range(3)], {"creator": ["0", "1", "2"]})
        sink = Sink()
        coordinator = EnumerationCoordinator(adapter, sink, max_items=10)
        checkpoint, first, _ = coordinator.step("creator", page_size=2)
        self.assertEqual((first.discovered, first.completed), (2, False))
        checkpoint, second, _ = coordinator.step("creator", checkpoint=checkpoint, page_size=2)
        self.assertEqual((second.discovered, second.completed), (1, True))
        self.assertEqual(len(sink.pages), 2)
        self.assertEqual(checkpoint.discovered_count, 3)

    def test_no_progress_and_cancel_are_actionable(self) -> None:
        adapter = FixtureSourceAdapter("fixture", [source("one")], {"creator": ["one"]})
        coordinator = EnumerationCoordinator(adapter)
        # A completed fixture page cannot make a no-progress incomplete page;
        # use a deliberately malformed adapter to exercise the guard.
        class Stalled(FixtureSourceAdapter):
            def enumerate_channel(self, channel_id, *, cursor=None, page_size=50):
                from engine.dubflow.download.source_adapter import SourcePage
                return SourcePage((source("one"),), "same", False)

        stalled = EnumerationCoordinator(Stalled("fixture", [source("one")], {"creator": ["one"]}))
        checkpoint = EnumerationCheckpoint("fixture", "creator", "same", frozenset({"fixture:one"}), 1)
        with self.assertRaises(EnumerationError):
            stalled.step("creator", checkpoint=checkpoint)
        with self.assertRaises(EnumerationError):
            coordinator.run("creator", cancel=lambda: True)


if __name__ == "__main__":
    unittest.main()
