"""Douyin single-video metadata comes from a signed web detail request.

The pinned SDK sends that request unsigned, Douyin answers with an empty body
and the SDK reports "fresh cookies are needed". That surfaced as a login
prompt for users who were already logged in.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from engine.dubflow.download import authenticated_native as helper
from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.douyin import creator_native as native

VIDEO = "7400000000000000001"
DETAIL = {"aweme_id": VIDEO, "desc": "Video", "video": {"duration": 12000}}


class ParseDetailTests(unittest.TestCase):
    def test_detail_object_is_returned(self):
        data = json.dumps({"status_code": 0, "aweme_detail": DETAIL}).encode()
        self.assertEqual(native.parse_detail(data, VIDEO), DETAIL)

    def test_failures_map_to_native_codes(self):
        cases = [
            (b"", 5),
            (b"<html>", 5),
            (json.dumps({"status_code": 0, "aweme_detail": None, "filter_detail": {}}).encode(), 4),
            (json.dumps({"status_code": 8, "status_msg": "need login"}).encode(), 2),
            (json.dumps({"status_code": 0, "aweme_detail": {**DETAIL, "aweme_id": "7400000000000000002"}}).encode(), 5),
        ]
        for data, code in cases:
            with self.subTest(data=data[:40]), self.assertRaises(native.ProviderPageError) as error:
                native.parse_detail(data, VIDEO)
            self.assertEqual(error.exception.native_exit_code, code)

    def test_video_id_from_douyin_urls(self):
        self.assertEqual(native.aweme_id_from_url(f"https://www.douyin.com/video/{VIDEO}"), VIDEO)
        self.assertEqual(native.aweme_id_from_url(f"https://www.douyin.com/video/{VIDEO}?previous_page=search"), VIDEO)
        self.assertEqual(native.aweme_id_from_url(f"https://www.douyin.com/search/x?modal_id={VIDEO}"), VIDEO)
        self.assertIsNone(native.aweme_id_from_url("https://www.douyin.com/user/abc"))
        self.assertIsNone(native.aweme_id_from_url("https://v.douyin.com/abc/"))  # no network without a downloader


class Extractor:
    def _parse_aweme_video_app(self, detail):
        return {"id": detail["aweme_id"], "title": detail["desc"], "formats": [{"url": "https://v.douyinvod.com/a.mp4"}]}


class Downloader:
    def __init__(self, fallback=None):
        self.cookiejar = []
        self.fallback = fallback
        self.extracted = 0

    def get_info_extractor(self, key):
        assert key == "Douyin"
        return Extractor()

    def add_default_extra_info(self, info, extractor, url):
        info["webpage_url"] = url

    def process_ie_result(self, info, download):
        assert download is False
        return info

    def extract_info(self, url, download):
        self.extracted += 1
        if self.fallback is None:
            raise RuntimeError("Fresh cookies (not necessarily logged in) are needed")
        return self.fallback


class SignedMetadataTests(unittest.TestCase):
    def modules(self, fetch):
        creator = type("Creator", (), {"aweme_id_from_url": staticmethod(native.aweme_id_from_url),
                                        "fetch_detail": staticmethod(fetch), "ProviderPageError": native.ProviderPageError})
        return {"creator": creator, "signature": type("Signature", (), {"ABogus": object})}

    def run_signed(self, downloader, fetch):
        with patch.object(helper, "load_douyin_modules", return_value=self.modules(fetch)):
            return helper.douyin_signed_metadata(downloader, f"https://www.douyin.com/video/{VIDEO}", {"creator": "a", "signature": "b"})

    def test_signed_detail_is_used_without_the_unsigned_sdk_request(self):
        downloader = Downloader()
        result = self.run_signed(downloader, lambda d, aweme_id, signer: DETAIL)
        self.assertEqual((result["id"], result["title"]), (VIDEO, "Video"))
        self.assertEqual(downloader.extracted, 0)

    def test_sdk_extractor_is_the_fallback(self):
        def refused(*args, **kwargs):
            raise native.ProviderPageError(5, "Douyin detail response is empty")
        downloader = Downloader(fallback={"id": VIDEO, "formats": []})
        self.assertEqual(self.run_signed(downloader, refused)["id"], VIDEO)

    def test_signed_failure_is_reported_instead_of_the_sdk_cookie_message(self):
        def missing(*args, **kwargs):
            raise native.ProviderPageError(4, "Douyin video is unavailable")
        with self.assertRaises(native.ProviderPageError) as error:
            self.run_signed(Downloader(), missing)
        self.assertEqual(helper.failure_exit_code(error.exception), 4)


class TransportRequestTests(unittest.TestCase):
    def test_douyin_inspect_carries_the_owned_module_pins(self):
        transport = AuthenticatedYtDlpTransport.__new__(AuthenticatedYtDlpTransport)
        transport.paths = {"douyin_creator": None, "douyin_signer": None}
        transport.pins = {"douyin_creator": "c" * 64, "douyin_signer": "s" * 64}
        sent = {}
        transport._invoke = lambda request, provider_id=None: sent.update(request) or {}
        transport.inspect_url(f"https://www.douyin.com/video/{VIDEO}", provider_id="douyin")
        self.assertEqual(sent["douyin_modules"], {"creator": "c" * 64, "signature": "s" * 64})
        sent.clear()
        transport.inspect_url("https://www.bilibili.com/video/BV1xx411c7mD", provider_id="bilibili")
        self.assertNotIn("douyin_modules", sent)


if __name__ == "__main__":
    unittest.main()
