from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from engine.dubflow.download.authenticated_native import enumerate_page, provider_request
from engine.dubflow.download.enumeration import EnumerationCoordinator
from engine.dubflow.download.generic import GenericUrlAdapter
from engine.dubflow.download.generic.sdk import GenericSdkTransport, sdk_identity
from engine.dubflow.download.provider_transport import PublicProviderHttpTransport
from engine.dubflow.download.materializer import DownloadError
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


def entry(number=1, **changes):
    return {"id": str(number), "ie_key": "RecordedVideo", "title": f"Video {number}",
            "url": f"https://video.example.test/watch/{number}", **changes}


class RecordedSDK:
    pins = {"python": "a" * 64, "helper": "b" * 64, "sdk_archive": "c" * 64}

    def __init__(self, entries=()):
        self.entries, self.calls = list(entries), []

    def enumerate_url(self, url, *, provider_id, offset, page_size):
        self.calls.append((url, provider_id, offset, page_size))
        values = self.entries[offset:offset + page_size + 1]
        return {"schema_version": 1, "offset": offset, "entries": values[:page_size], "has_more": len(values) > page_size}

    def inspect_url(self, url, *, provider_id):
        self.calls.append((url, provider_id))
        value = entry(int(url.rsplit("/", 1)[1]))
        return {"id": value["id"], "extractor_key": value["ie_key"], "webpage_url": value["url"], "title": value["title"],
                "description": "First line\r\n\tSecond line", "duration": 1.25,
                "formats": [{"format_id": "av", "url": "https://cdn.example.test/file.mp4?token=synthetic-secret", "vcodec": "h264", "acodec": "aac"}]}


class GenericSdkTests(unittest.TestCase):
    def adapter(self, sdk, **kwargs):
        return GenericUrlAdapter(GenericSdkTransport(sdk), **kwargs)

    def test_restart_dedup_poison_isolation_and_private_entry(self):
        sdk = RecordedSDK([entry(1), entry(2), entry(2), None, entry(3, availability="private"), {"invalid": True}, entry(4)])
        sink = Mock()
        checkpoint, progress, _ = EnumerationCoordinator(self.adapter(sdk), sink).step("https://video.example.test/list/1", page_size=2)
        self.assertEqual((checkpoint.discovered_count, progress.completed), (2, False))
        final, progress = EnumerationCoordinator(self.adapter(sdk), sink).run("https://video.example.test/list/1", checkpoint=checkpoint, page_size=2)
        self.assertEqual((progress.discovered, progress.duplicates, progress.failed, progress.completed), (1, 1, 3, True))
        self.assertEqual(final.discovered_count, 3)
        self.assertEqual([call[2] for call in sdk.calls], [0, 2, 4, 6])
        self.assertEqual(sink.checkpoint_page.call_count, 4)
        failures = [failure for call in sink.checkpoint_page.call_args_list for failure in call.args[1].failures]
        self.assertEqual([failure.code for failure in failures], [SourceErrorCode.NOT_FOUND, SourceErrorCode.PRIVATE, SourceErrorCode.SOURCE_CHANGED])

    def test_flat_download_reinspects_same_identity_and_keeps_cdn_tokens_private(self):
        sdk, muxer = RecordedSDK([entry(1)]), Mock()
        adapter = self.adapter(sdk, stream_materializer=muxer)
        item = adapter.enumerate_channel("https://video.example.test/list/1").items[0]
        self.assertFalse(item.media_candidates)
        adapter.download(item, Path("destination.mp4"), candidate_id="av")
        self.assertEqual(sdk.calls[-1], (item.identity.canonical_url, "generic"))
        self.assertEqual(muxer.download.call_args.args[0].candidate_id, "av")
        self.assertNotIn("synthetic-secret", json.dumps(item.to_dict()))
        inspected = adapter.inspect(item.identity.canonical_url)
        self.assertEqual(inspected.identity, item.identity)
        self.assertEqual(inspected.description, "First line Second line")
        self.assertEqual(inspected.duration_ticks, 112500)

    def test_changed_flat_video_stops_before_materialization(self):
        sdk, materializer = RecordedSDK([entry(1)]), Mock()
        adapter = self.adapter(sdk, materializer=materializer)
        item = adapter.enumerate_channel("https://video.example.test/list/1").items[0]
        original = sdk.inspect_url
        sdk.inspect_url = lambda *args, **kwargs: {**original(*args, **kwargs), "id": "changed"}
        with self.assertRaises(SourceError) as failure:
            adapter.download(item, "destination.mp4")
        self.assertEqual(failure.exception.code, SourceErrorCode.SOURCE_CHANGED)
        materializer.download.assert_not_called()

    def test_extractor_namespaces_and_generic_filename_collisions(self):
        first = sdk_identity(entry(1))
        self.assertEqual(first, sdk_identity({**entry(1), "ie_key": "recordedvideo"}))
        self.assertNotEqual(first.source_id, sdk_identity(entry(1, ie_key="OtherVideo")).source_id)
        a = sdk_identity(entry(1, ie_key="Generic"))
        b = sdk_identity(entry(1, ie_key="Generic", url="https://other.example.test/watch/1"))
        self.assertNotEqual(a.source_id, b.source_id)
        self.assertEqual(a, sdk_identity(entry(1, ie_key="Generic", url=entry(1)["url"] + "?utm_source=tracking#fragment")))

    def test_cursor_source_all_pins_offset_and_schema_are_bound_before_sdk_call(self):
        sdk = RecordedSDK([entry(1), entry(2)])
        transport = GenericSdkTransport(sdk)
        cursor = transport.enumerate_playlist("https://video.example.test/list/1", cursor=None, page_size=1).next_cursor
        baseline = json.loads(cursor)
        for changes in ({"offset": 0}, {"offset": True}, {"offset": 10000}, {"v": True},
                        {"url": "foreign"}, {"recipe": "changed"}, {"extra": "secret"}):
            with self.assertRaises(SourceError) as failure:
                transport.enumerate_playlist("https://video.example.test/list/1", cursor=json.dumps({**baseline, **changes}), page_size=1)
            self.assertEqual(failure.exception.code, SourceErrorCode.CHECKPOINT_INVALID)
        with self.assertRaises(SourceError):
            transport.enumerate_playlist("https://other.example.test/list/1", cursor=cursor, page_size=1)
        for pin in sdk.pins:
            other = RecordedSDK()
            other.pins = {**sdk.pins, pin: "f" * 64}
            with self.assertRaises(SourceError):
                GenericSdkTransport(other).enumerate_playlist("https://video.example.test/list/1", cursor=cursor, page_size=1)
            self.assertFalse(other.calls)
        self.assertEqual(len(sdk.calls), 1)

    def test_malformed_no_progress_pages_and_bounds_fail_explicitly(self):
        sdk = RecordedSDK()
        transport = GenericSdkTransport(sdk)
        for value in (None, [], {}, {"schema_version": True, "offset": 0, "entries": [], "has_more": False},
                      {"schema_version": 1, "offset": 1, "entries": [], "has_more": False},
                      {"schema_version": 1, "offset": 0, "entries": [], "has_more": True},
                      {"schema_version": 1, "offset": 0, "entries": [entry(1)] * 3, "has_more": False}):
            sdk.enumerate_url = Mock(return_value=value)
            with self.assertRaises(SourceError) as failure:
                transport.enumerate_playlist("https://video.example.test/list/1", cursor=None, page_size=2)
            self.assertEqual(failure.exception.code, SourceErrorCode.SOURCE_CHANGED)
        for size in (0, True, 101, "2"):
            with self.assertRaises(SourceError) as failure:
                transport.enumerate_playlist("https://video.example.test/list/1", cursor=None, page_size=size)
            self.assertEqual(failure.exception.code, SourceErrorCode.INVALID_INPUT)

    def test_10000_slots_cannot_be_reported_complete_when_more_exist(self):
        sdk = RecordedSDK(entry(n) for n in range(10001))
        transport = GenericSdkTransport(sdk)
        first = transport.enumerate_playlist("https://video.example.test/list/1", cursor=None, page_size=100)
        cursor = json.loads(first.next_cursor)
        cursor["offset"] = 9998
        with self.assertRaises(SourceError) as failure:
            transport.enumerate_playlist("https://video.example.test/list/1", cursor=json.dumps(cursor), page_size=100)
        self.assertEqual(failure.exception.code, SourceErrorCode.UNSUPPORTED)
        self.assertEqual(sdk.calls[-1][2:], (9998, 2))

    def test_poisoned_identity_urls_titles_and_availability_are_item_failures(self):
        poisoned = [entry(1, url="file:///outside"), entry(2, url="https://user:synthetic-secret@host/2"),
                    entry(3, url="https://host/3?token=synthetic-secret"), entry(4, title="bad\n"),
                    entry(5, availability=[]), entry(6, ie_key=[]), entry(7, id=[])]
        page = self.adapter(RecordedSDK([*poisoned, entry(8)])).enumerate_channel("https://video.example.test/list/1")
        self.assertEqual((len(page.items), len(page.failures), page.completed), (1, 7, True))
        self.assertNotIn("synthetic-secret", json.dumps(page.to_dict()))

    def test_source_credentials_refused_without_echoing_or_network(self):
        sdk = RecordedSDK()
        for url in ("https://user:synthetic-secret@host/video", "https://host/video?ACCESS_TOKEN=synthetic-secret", "file:///outside", "https://host:bad/video"):
            with self.assertRaises(SourceError) as failure:
                self.adapter(sdk).inspect(url)
            self.assertNotIn("synthetic-secret", str(failure.exception))
        self.assertFalse(sdk.calls)

    def test_generic_media_headers_have_no_fabricated_referer_or_session(self):
        raw = Mock()
        public = {"User-Agent": "Reviewed UA", "Accept": "*/*", "Accept-Language": "en"}
        transport = PublicProviderHttpTransport("generic", public, transport=raw)
        transport.open("https://cdn.example.test/file.mp4", headers={"range": "bytes=2-"})
        self.assertEqual(raw.open.call_args.kwargs["headers"], {**public, "Range": "bytes=2-"})
        with self.assertRaises(DownloadError):
            transport.open("https://cdn.example.test/file.mp4", headers={"Cookie": "synthetic-secret"})
        self.assertEqual(raw.open.call_count, 1)

    def test_optional_description_normalizes_whitespace_but_other_controls_and_size_fail(self):
        sdk = RecordedSDK()
        baseline = sdk.inspect_url(entry()["url"], provider_id="generic")
        for description in (None, "", "\r\n\t"):
            sdk.inspect_url = Mock(return_value={**baseline, "description": description})
            self.assertIsNone(self.adapter(sdk).inspect(entry()["url"]).description)
        for description in ([], "x\x00", "x\x7f", "x" * 16385):
            sdk.inspect_url = Mock(return_value={**baseline, "description": description})
            with self.assertRaises(SourceError) as failure:
                self.adapter(sdk).inspect(entry()["url"])
            self.assertEqual(failure.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_native_helper_anonymous_capability_and_url_validation(self):
        for headers in ({"Cookie": "synthetic-secret"}, {"Authorization": "synthetic-secret"}, []):
            with self.assertRaises(ValueError):
                provider_request({"provider_id": "generic", "url": entry()["url"], "headers": headers})
        for url in ("file:///outside", "https://user:secret@host", "https://host:bad/path", "https://host/\npath"):
            with self.assertRaises(ValueError):
                provider_request({"provider_id": "generic", "url": url})
        self.assertEqual(provider_request({"provider_id": "generic", "url": entry()["url"]}), ("generic", entry()["url"], []))

    def test_native_helper_bounds_iterator_redacts_headers_and_rejects_nested_items(self):
        observed = []
        class Downloader:
            def __init__(self, options):
                observed.append(options)
                self.cookiejar = Mock()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                def values():
                    yield {**entry(1), "http_headers": {"Cookie": "synthetic-secret"}}
                    yield {"_type": "playlist", **entry(2)}
                    yield entry(3)
                    raise AssertionError("read beyond page lookahead")
                return {"_type": "playlist", "entries": values()}
        result = enumerate_page({"provider_id": "generic", "url": "https://video.example.test/list/1", "offset": 0, "page_size": 2}, Downloader)
        self.assertEqual(result["entries"], [entry(1), {"invalid": True}])
        self.assertTrue(result["has_more"])
        self.assertEqual(observed[0]["playlist_items"], "1:3")
        self.assertTrue(observed[0]["lazy_playlist"])
        self.assertNotIn("synthetic-secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
