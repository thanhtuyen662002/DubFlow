from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from engine.dubflow.download.bilibili.adapter import BilibiliSourceAdapter, normalize_source_ref
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode, SourceIdentity, SourceItem


BVID = "BV13x41117TL"
BASE = f"https://www.bilibili.com/video/{BVID}"


def metadata(identifier: str) -> dict:
    return {"id": identifier, "title": "recorded selected part", "duration": 9,
            "formats": [{"format_id": "av", "url": "https://cdn.example.test/second.mp4",
                         "vcodec": "h264", "acodec": "aac", "ext": "mp4"}]}


class MultipartTests(unittest.TestCase):
    def test_identity_keeps_later_parts_distinct_and_first_part_compatible(self):
        for ref, expected in ((BASE, BVID), (BASE + "?p=1", BVID), (BVID + "_p1", BVID),
                              (BASE + "?p=02&utm_source=test", BVID + "_p2"),
                              (BVID + "_p2", BVID + "_p2"),
                              ("https://www.bilibili.com/video/av123?p=3", "av123_p3"),
                              (BASE + "?p=10000", BVID + "_p10000")):
            with self.subTest(ref=ref):
                self.assertEqual(normalize_source_ref(ref), expected)

    def test_invalid_or_ambiguous_selector_stops_before_extraction(self):
        delegate = Mock()
        adapter = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate))
        queries = ("p=", "p", "p=0", "p=-1", "p=1.0", "p=10001", "p=999999",
                   "p=2&p=3", "p=2&p=2", "p=%0A2", "p=%FF", "p=hello")
        refs = [BASE + "?" + value for value in queries]
        refs += [BVID + "_p0", BVID + "_p10001", BASE + "?" + "&".join(f"a{i}=x" for i in range(65))]
        for ref in refs:
            with self.subTest(ref=ref), self.assertRaises(SourceError) as context:
                adapter.inspect(ref)
            self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        delegate.inspect_url.assert_not_called()

    def test_selected_part_reaches_sdk_and_maps_to_canonical_identity(self):
        delegate = Mock()
        delegate.inspect_url.return_value = metadata(BVID + "_p2")
        adapter = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate))
        item = adapter.inspect(BASE + "?p=2&utm_source=tracking")
        delegate.inspect_url.assert_called_once_with(BASE + "?p=2", provider_id="bilibili", headers=None)
        self.assertEqual(item.identity.identity_key, "bilibili:" + BVID + "_p2")
        self.assertEqual(item.identity.canonical_url, BASE + "?p=2")
        self.assertEqual(item.media_candidates[0].locator, "https://cdn.example.test/second.mp4")

    def test_refresh_of_enumerated_part_downloads_that_part(self):
        delegate, muxer = Mock(), Mock()
        delegate.inspect_url.return_value = metadata(BVID + "_p2")
        adapter = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate), stream_materializer=muxer)
        pending = SourceItem(SourceIdentity("bilibili", BVID + "_p2", BASE + "?p=2"), "recorded pending")
        adapter.download(pending, Path("/unused/second.mp4"))
        delegate.inspect_url.assert_called_once_with(BASE + "?p=2", provider_id="bilibili", headers=None)
        self.assertEqual(muxer.download.call_args.args[0].locator, "https://cdn.example.test/second.mp4")
        self.assertEqual(muxer.download.call_count, 1)

    def test_wrong_or_missing_reported_part_never_downloads_first_part(self):
        for identifier in (BVID, BVID + "_p1", BVID + "_p3", BVID + "_p0", "malformed", None):
            with self.subTest(identifier=identifier):
                delegate, muxer = Mock(), Mock()
                delegate.inspect_url.return_value = metadata(identifier)
                adapter = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate), stream_materializer=muxer)
                pending = SourceItem(SourceIdentity("bilibili", BVID + "_p2", BASE + "?p=2"), "recorded pending")
                with self.assertRaises(SourceError) as context:
                    adapter.download(pending, Path("/unused/second.mp4"))
                self.assertEqual(context.exception.code, SourceErrorCode.SOURCE_CHANGED)
                muxer.download.assert_not_called()

    def test_av_alias_resolves_to_same_selected_bvid_part(self):
        delegate = Mock()
        delegate.inspect_url.return_value = metadata(BVID + "_p2")
        adapter = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate))
        item = adapter.inspect("https://www.bilibili.com/video/av123?p=2")
        delegate.inspect_url.assert_called_once_with("https://www.bilibili.com/video/av123?p=2", provider_id="bilibili", headers=None)
        self.assertEqual(item.identity.identity_key, "bilibili:" + BVID + "_p2")
        self.assertEqual(item.identity.canonical_url, BASE + "?p=2")

    def test_sdk_explicit_first_part_remains_legacy_identity(self):
        delegate = Mock()
        delegate.inspect_url.return_value = metadata(BVID + "_p1")
        item = BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=delegate)).inspect(BASE + "?p=1")
        self.assertEqual(item.identity.source_id, BVID)
        self.assertEqual(item.identity.canonical_url, BASE)

    def test_api_part_evidence_is_required_and_typed(self):
        for part in (None, False, True, "2", 0, 1, 3, 10001):
            with self.subTest(part=part):
                transport = Mock()
                transport.fetch_video.return_value = {"data": {"bvid": BVID, "part": part,
                    "title": "recorded API", "durl": [{"url": "https://cdn.example.test/first.mp4"}]}}
                with self.assertRaises(SourceError) as context:
                    BilibiliSourceAdapter(transport).inspect(BASE + "?p=2")
                self.assertEqual(context.exception.code, SourceErrorCode.SOURCE_CHANGED)


if __name__ == "__main__":
    unittest.main()
