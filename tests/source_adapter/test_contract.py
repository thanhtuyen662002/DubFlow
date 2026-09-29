from __future__ import annotations

import unittest

from engine.dubflow.download.source_adapter import (
    FixtureSourceAdapter,
    MediaCandidate,
    SourceError,
    SourceErrorCode,
    SourceIdentity,
    SourceItem,
    SubtitleCandidate,
    canonicalize_url,
)


def item(source_id: str, url: str | None = None) -> SourceItem:
    identity = SourceIdentity(
        "fixture",
        source_id,
        url or f"https://video.example.test/watch/{source_id}",
    )
    return SourceItem(
        identity=identity,
        title=f"Fixture {source_id}",
        duration_ticks=90_000,
        media_candidates=(MediaCandidate("video", f"fixture://{source_id}.mp4", "local", "video/mp4"),),
        subtitle_candidates=(SubtitleCandidate("sub", f"fixture://{source_id}.srt", "zh", "srt"),),
    )


class SourceContractTests(unittest.TestCase):
    def test_url_canonicalization_is_stable_without_destroying_signed_query(self) -> None:
        self.assertEqual(
            canonicalize_url("HTTPS://Video.Example.Test:443/a/../watch/1/?utm_source=x&sig=abc#part"),
            "https://video.example.test/watch/1/?sig=abc",
        )
        with self.assertRaises(SourceError):
            canonicalize_url("https://user:password@example.test/video")

    def test_identity_key_deduplicates_redirect_variants(self) -> None:
        first = SourceIdentity("fixture", "abc", "https://example.test/watch/abc?utm_medium=x")
        second = SourceIdentity("fixture", "abc", "https://example.test/watch/abc#comments")
        self.assertEqual(first.identity_key, second.identity_key)
        self.assertEqual(first.canonical_url, second.canonical_url)

    def test_fixture_inspect_and_resume_from_opaque_cursor(self) -> None:
        records = [item(str(index)) for index in range(5)]
        adapter = FixtureSourceAdapter("fixture", records, {"creator": [str(index) for index in range(5)]})
        first = adapter.enumerate_channel("creator", page_size=2)
        self.assertEqual([record.identity.source_id for record in first.items], ["0", "1"])
        self.assertFalse(first.completed)
        second = adapter.enumerate_channel("creator", cursor=first.next_cursor, page_size=2)
        self.assertEqual([record.identity.source_id for record in second.items], ["2", "3"])
        third = adapter.enumerate_channel("creator", cursor=second.next_cursor, page_size=2)
        self.assertEqual([record.identity.source_id for record in third.items], ["4"])
        self.assertTrue(third.completed)

    def test_bad_cursor_and_missing_source_are_structured(self) -> None:
        adapter = FixtureSourceAdapter("fixture", [item("one")], {"creator": ["one"]})
        with self.assertRaises(SourceError) as missing:
            adapter.inspect("https://video.example.test/watch/missing")
        self.assertEqual(missing.exception.code, SourceErrorCode.NOT_FOUND)
        with self.assertRaises(SourceError) as bad_cursor:
            adapter.enumerate_channel("creator", cursor='{"v":1,"provider":"other","channel":"creator","offset":0}')
        self.assertEqual(bad_cursor.exception.code, SourceErrorCode.CHECKPOINT_INVALID)

    def test_serialized_item_uses_decimal_duration(self) -> None:
        payload = item("one").to_dict()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["duration_ticks"], "90000")
        self.assertEqual(payload["identity"]["identity_key"], "fixture:one")


if __name__ == "__main__":
    unittest.main()
