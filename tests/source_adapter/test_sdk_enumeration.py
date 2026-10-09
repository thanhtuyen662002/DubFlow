from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.authenticated_native import enumerate_page
from engine.dubflow.download.bilibili import BilibiliSourceAdapter
from engine.dubflow.download.douyin import DouyinSourceAdapter
from engine.dubflow.download.enumeration import EnumerationCoordinator, EnumerationError
from engine.dubflow.download.enumeration.sdk import channel_url, sdk_page
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.source_adapter import (
    MediaCandidate, SourceError, SourceErrorCode, SourceIdentity, SourceItem,
)


def video(number):
    return {"id": f"BV{number:010d}", "title": f"Video {number}"}


class RecordedSDK:
    pins = {"python": "a" * 64, "helper": "b" * 64, "sdk_archive": "c" * 64}

    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def enumerate_url(self, url, *, provider_id, offset, page_size, headers):
        self.calls.append((url, provider_id, offset, page_size, headers))
        values = self.entries[offset:offset + page_size + 1]
        return {"schema_version": 1, "offset": offset,
                "entries": values[:page_size], "has_more": len(values) > page_size}


class SDKEnumerationTests(unittest.TestCase):
    def adapter(self, sdk):
        return BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=sdk))

    def test_checkpoint_resume_dedup_and_poison_isolation(self):
        sdk = RecordedSDK([video(1), video(2), video(2), None,
                           {**video(3), "availability": "private"}, video(4)])
        sink = Mock()
        first_coordinator = EnumerationCoordinator(self.adapter(sdk), sink)
        checkpoint, first, _ = first_coordinator.step("123", page_size=2)
        self.assertEqual((checkpoint.discovered_count, first.completed), (2, False))
        # A fresh adapter/coordinator uses only the emitted checkpoint.
        second_coordinator = EnumerationCoordinator(self.adapter(sdk), sink)
        checkpoint, second = second_coordinator.run("123", checkpoint=checkpoint, page_size=2)
        self.assertEqual((second.discovered, second.duplicates, second.failed, second.completed), (1, 1, 2, True))
        self.assertEqual(checkpoint.discovered_count, 3)
        self.assertEqual([call[2] for call in sdk.calls], [0, 2, 4])
        self.assertEqual(sink.checkpoint_page.call_count, 3)
        emitted = sink.checkpoint_page.call_args_list[-1].args[1]
        self.assertFalse(emitted.items[0].media_candidates)
        self.assertEqual(emitted.failures[0].code, SourceErrorCode.PRIVATE)

    def test_page_failure_preserves_last_checkpoint_and_retries_same_offset(self):
        sdk = RecordedSDK([video(1), video(2), video(3)])
        sink = Mock()
        coordinator = EnumerationCoordinator(self.adapter(sdk), sink)
        checkpoint, _, _ = coordinator.step("123", page_size=2)
        original = sdk.enumerate_url
        sdk.enumerate_url = Mock(side_effect=SourceError(SourceErrorCode.RATE_LIMITED, "provider rate limit", retryable=True))
        with self.assertRaises(EnumerationError) as failure:
            coordinator.step("123", checkpoint=checkpoint, page_size=2)
        self.assertEqual(failure.exception.code, "RATE_LIMITED")
        self.assertEqual(sink.checkpoint_page.call_count, 1)
        sdk.enumerate_url = original  # Explicitly changed transport condition.
        resumed, progress, _ = coordinator.step("123", checkpoint=checkpoint, page_size=2)
        self.assertTrue(progress.completed)
        self.assertEqual(resumed.discovered_count, 3)
        self.assertEqual(sdk.calls[-1][2], 2)

    def test_cursor_binds_channel_provider_and_all_producer_pins(self):
        sdk = RecordedSDK([video(1), video(2)])
        cursor = sdk_page(sdk, "bilibili", "123", cursor=None, page_size=1).next_cursor
        baseline = json.loads(cursor)
        for changes in ({"offset": 0}, {"offset": True}, {"offset": 10000},
                        {"v": True}, {"provider": "douyin"}, {"channel": "foreign"},
                        {"recipe": "changed"}, {"extra": "secret"}):
            with self.subTest(changes=changes):
                with self.assertRaises(SourceError) as error:
                    sdk_page(sdk, "bilibili", "123", cursor=json.dumps({**baseline, **changes}), page_size=1)
                self.assertEqual(error.exception.code, SourceErrorCode.CHECKPOINT_INVALID)
        with self.assertRaises(SourceError):
            sdk_page(sdk, "bilibili", "456", cursor=cursor, page_size=1)
        for pin in sdk.pins:
            changed = RecordedSDK([])
            changed.pins = {**sdk.pins, pin: "f" * 64}
            with self.assertRaises(SourceError):
                sdk_page(changed, "bilibili", "123", cursor=cursor, page_size=1)
            self.assertFalse(changed.calls)
        self.assertEqual(len(sdk.calls), 1)

    def test_malformed_pages_cannot_claim_completion_or_no_progress(self):
        sdk = RecordedSDK([])
        for raw in (None, [], {}, {"schema_version": True, "offset": 0, "entries": [], "has_more": False},
                    {"schema_version": 1, "offset": 1, "entries": [], "has_more": False},
                    {"schema_version": 1, "offset": 0, "entries": [], "has_more": True},
                    {"schema_version": 1, "offset": 0, "entries": [video(1)] * 3, "has_more": False}):
            sdk.enumerate_url = Mock(return_value=raw)
            with self.subTest(raw=raw), self.assertRaises(SourceError) as error:
                sdk_page(sdk, "bilibili", "123", cursor=None, page_size=2)
            self.assertEqual(error.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_metadata_poison_does_not_stop_next_video(self):
        sdk = RecordedSDK([{"invalid": True}, {**video(1), "availability": []},
                           {**video(2), "title": "\x7f"}, {**video(3), "title": "   "},
                           {"id": "123_collection", "title": "collection"}, video(4)])
        page = sdk_page(sdk, "bilibili", "123", cursor=None, page_size=10)
        self.assertEqual([item.identity.source_id for item in page.items], [video(4)["id"]])
        self.assertEqual(len(page.failures), 5)
        self.assertTrue(page.completed)

    def test_channel_and_page_bounds_fail_before_provider_request(self):
        sdk = RecordedSDK([])
        for ref in ("https://space.bilibili.com.evil.test/123/video", "https://secret@space.bilibili.com/123/video",
                    "https://space.bilibili.com/123/video?token=secret", "https://space.bilibili.com/../video",
                    "https://space.bilibili.com/123/video#secret", "１２３", "123\n", "x" * 513):
            with self.subTest(ref=ref), self.assertRaises(SourceError) as error:
                sdk_page(sdk, "bilibili", ref, cursor=None, page_size=2)
            self.assertNotIn("secret", str(error.exception))
        for size in (True, 0, 101, 2.5, "2"):
            with self.assertRaises(SourceError):
                sdk_page(sdk, "bilibili", "123", cursor=None, page_size=size)
        self.assertFalse(sdk.calls)
        self.assertEqual(channel_url("bilibili", "https://space.bilibili.com/123/upload/video/"), channel_url("bilibili", "123"))

    def test_ten_thousand_boundary_is_never_false_completion(self):
        sdk = RecordedSDK([video(i) for i in range(10001)])
        cursor = sdk_page(sdk, "bilibili", "123", cursor=None, page_size=100).next_cursor
        value = json.loads(cursor)
        value["offset"] = 9998
        with self.assertRaises(SourceError) as error:
            sdk_page(sdk, "bilibili", "123", cursor=json.dumps(value), page_size=100)
        self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)
        sdk.entries.pop()
        page = sdk_page(sdk, "bilibili", "123", cursor=json.dumps(value), page_size=100)
        self.assertEqual(len(page.items), 2)
        self.assertTrue(page.completed)

    def test_provider_bridge_and_flat_download_reinspection(self):
        for adapter_class, provider, source_id in ((BilibiliSourceAdapter, "bilibili", video(1)["id"]),
                                                    (DouyinSourceAdapter, "douyin", "7345678901234567890")):
            with self.subTest(provider=provider):
                bridge = Mock()
                bridge.get_opaque_headers.return_value = {"Cookie": "test=synthetic-secret"}
                transport, materializer = Mock(), Mock()
                adapter = adapter_class(transport, session_bridge=bridge, materializer=materializer)
                adapter.enumerate_channel("123", cursor="opaque", page_size=7)
                transport.fetch_channel.assert_called_once_with("123", cursor="opaque", page_size=7, session=bridge.get_opaque_headers.return_value)
                identity = SourceIdentity(provider, source_id, f"https://www.{provider}.com/video/{source_id}")
                flat = SourceItem(identity, "flat video")
                media = MediaCandidate("media", "https://cdn.example.test/file.mp4", "progressive", "video/mp4", has_audio=True)
                adapter.inspect = Mock(return_value=SourceItem(identity, "fresh", media_candidates=(media,)))
                adapter.download(flat, "destination.mp4")
                adapter.inspect.assert_called_once_with(source_id)
                materializer.download.assert_called_once_with(media, "destination.mp4")
                adapter.inspect.return_value = SourceItem(SourceIdentity(provider, "999999", f"https://www.{provider}.com/video/999999"), "changed", media_candidates=(media,))
                with self.assertRaises(SourceError) as error:
                    adapter.download(flat, "destination.mp4")
                self.assertEqual(error.exception.code, SourceErrorCode.SOURCE_CHANGED)
                self.assertEqual(materializer.download.call_count, 1)

    def test_native_helper_lookahead_bounds_and_item_redaction(self):
        class LazyDownloader:
            def __init__(self, options):
                self.options = options
                self.cookiejar = Mock()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                self.options_checked = True
                self.assert_options = (self.options["playlist_items"], self.options["lazy_playlist"], self.options["extract_flat"])
                def entries():
                    yield {**video(1), "url": "https://cdn.example.test/?token=synthetic-secret", "http_headers": {"Cookie": "secret"}}
                    yield {**video(2), "title": ["bad"]}
                    yield video(3)
                    raise AssertionError("helper consumed beyond page lookahead")
                return {"_type": "playlist", "entries": entries()}
        downloader = None
        def factory(options):
            nonlocal downloader
            downloader = LazyDownloader(options)
            return downloader
        result = enumerate_page({"provider_id": "bilibili", "url": channel_url("bilibili", "123"), "offset": 0, "page_size": 2}, factory)
        self.assertEqual(downloader.assert_options, ("1:3", True, "in_playlist"))
        self.assertEqual(result["entries"], [video(1), {"invalid": True}])
        self.assertTrue(result["has_more"])
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_actual_isolated_child_enumerates_and_rejects_unsupported_creator(self):
        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory(dir=python.parent) as temp:
            helper, archive = Path(temp) / "helper.py", Path(temp) / "recorded-sdk.zip"
            helper.write_bytes((Path(__file__).resolve().parents[2] / "engine/dubflow/download/authenticated_native.py").read_bytes())
            source = '''
from http.cookiejar import CookieJar
class YoutubeDL:
    def __init__(self, options):
        assert options['lazy_playlist'] and options['extract_flat'] == 'in_playlist'
        assert not options['noplaylist'] and options['cachedir'] is False
        assert 'http_headers' not in options and options['js_runtimes'] == {}
        self.options, self.cookiejar = options, CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, url, download):
        assert download is False
        cookie, = self.cookiejar
        assert cookie.value == 'synthetic-secret' and cookie.domain == '.bilibili.com'
        first, last = map(int, self.options['playlist_items'].split(':'))
        def entries():
            for n in range(first, min(last, 5) + 1):
                yield {'id': f'BV{n:010d}', 'title': f'Video {n}', 'url': 'https://cdn.example.test/?token=synthetic-secret'}
        return {'_type': 'playlist', 'entries': entries()}
'''
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("yt_dlp/__init__.py", source)
                zipped.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
            paths = {"python": python, "helper": helper, "sdk_archive": archive}
            transport = AuthenticatedYtDlpTransport(runtime_root=Path(os.path.commonpath([str(python), str(helper)])),
                **paths, pins={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}, timeout_s=10)
            adapter = self.adapter(transport)
            adapter._session_bridge = Mock()
            adapter._session_bridge.get_opaque_headers.return_value = {"Cookie": "SESSDATA=synthetic-secret"}
            page = adapter.enumerate_channel("123", page_size=2)
            final = adapter.enumerate_channel("123", cursor=page.next_cursor, page_size=3)
            self.assertEqual([item.identity.source_id for item in page.items], [video(1)["id"], video(2)["id"]])
            self.assertEqual([item.identity.source_id for item in final.items], [video(i)["id"] for i in (3, 4, 5)])
            self.assertTrue(final.completed)
            self.assertNotIn("synthetic-secret", json.dumps(page.to_dict()))
            douyin = DouyinSourceAdapter(YtDlpProviderTransport("douyin", authenticated_transport=transport))
            with self.assertRaises(SourceError) as error:
                douyin.enumerate_channel("creator123", page_size=2)
            self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)
            self.assertFalse(error.exception.retryable)


if __name__ == "__main__":
    unittest.main()
