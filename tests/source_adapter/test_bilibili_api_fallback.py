from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.provider_transport import _map_payload
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode

# Recorded SDK: the page extractor always fails (Bilibili risk control), and
# urlopen serves recorded public API documents keyed by bvid.
SDK = '''
import json
from http.cookiejar import CookieJar
from urllib.parse import urlsplit, parse_qs
from yt_dlp.networking.exceptions import HTTPError

VIEW = {
    "BV1live00001": {"code": 0, "data": {"bvid": "BV1live00001", "title": "recorded", "desc": "d", "duration": 83, "cid": 11,
                      "pages": [{"page": 1, "cid": 11, "part": "one", "duration": 83}, {"page": 2, "cid": 12, "part": "two", "duration": 40}]}},
    "BV1gone00001": {"code": -404, "message": "missing"},
}
DASH = {"code": 0, "data": {"dash": {"video": [{"id": 32, "codecid": 7, "baseUrl": "https://cdn.test/v480.m4s", "width": 852, "height": 480, "codecs": "avc1.64001F", "bandwidth": 500000}],
                                      "audio": [{"id": 30280, "baseUrl": "https://cdn.test/a.m4s", "codecs": "mp4a.40.2", "bandwidth": 128000}]}}}
HTML5 = {"code": 0, "data": {"durl": [{"url": "https://cdn.test/360.mp4"}]}}
# Risk control answers the view API with HTML 412; the server-rendered page
# and the page list still answer.
PAGE_STATE = {"videoData": {"bvid": "BV1page00001", "title": "正片", "desc": "d", "duration": 16690, "cid": 11,
                            "pages": [{"page": 1, "cid": 11, "part": "one", "duration": 16690}]}}
PAGELIST = {"code": 0, "data": [{"page": 1, "cid": 11, "part": "only part", "duration": 120}]}

class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.body[:limit]

class YoutubeDL:
    def __init__(self, options): self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, url, download):
        raise RuntimeError("[BiliBili] Unable to extract initial state")
    def urlopen(self, request):
        assert request.headers["Referer"] == "https://www.bilibili.com/"
        parts = urlsplit(request.url)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if parts.netloc == "www.bilibili.com":
            assert request.headers["Accept-Language"].startswith("zh-CN")
            if parts.path != "/zh-Hans/video/BV1page00001/":
                raise HTTPError(404)
            state = json.dumps(PAGE_STATE)
            return Response(f"<script>window.__INITIAL_STATE__={state};(function(){{}})()</script>".encode())
        if query.get("bvid") == "BV1limit0001":
            raise HTTPError(412)
        if parts.path == "/x/web-interface/view" and query["bvid"] in {"BV1page00001", "BV1list00001"}:
            return Response(b"<!DOCTYPE html><html>412</html>")
        if parts.path == "/x/player/pagelist":
            return Response(json.dumps(PAGELIST).encode())
        if parts.path == "/x/web-interface/view":
            return Response(json.dumps(VIEW[query["bvid"]]).encode())
        assert query["cid"] in {"11", "12"}
        return Response(json.dumps(HTML5 if query.get("platform") == "html5" else DASH).encode())
'''
NETWORKING = '''
class Request:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = dict(headers or {})
'''
EXCEPTIONS = '''
class HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP Error {status}")
        self.status = status
'''


class BilibiliApiFallbackTests(unittest.TestCase):
    def setUp(self):
        python = Path(sys.executable).resolve()
        self._temp = tempfile.TemporaryDirectory(dir=python.parent)
        temp = Path(self._temp.name)
        helper = temp / "authenticated_native.py"
        helper.write_bytes((Path(__file__).resolve().parents[2] / "engine/dubflow/download/authenticated_native.py").read_bytes())
        archive = temp / "recorded-sdk.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("yt_dlp/__init__.py", SDK)
            zipped.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
            zipped.writestr("yt_dlp/version.py", "__version__ = 'recorded-sdk'\n")
            zipped.writestr("yt_dlp/networking/__init__.py", NETWORKING)
            zipped.writestr("yt_dlp/networking/exceptions.py", EXCEPTIONS)
        root = Path(os.path.commonpath([str(python), str(helper)]))
        pins = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                {"python": python, "helper": helper, "sdk_archive": archive}.items()}
        self.transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper,
                                                     sdk_archive=archive, pins=pins, timeout_s=10)

    def tearDown(self):
        self._temp.cleanup()

    def inspect(self, url):
        return self.transport.inspect_url(url, provider_id="bilibili")

    def test_extractor_failure_falls_back_to_public_api_formats(self):
        result = self.inspect("https://www.bilibili.com/video/BV1live00001")
        self.assertEqual((result["id"], result["title"], result["duration"]), ("BV1live00001", "recorded - one", 83))
        ids = [value["format_id"] for value in result["formats"]]
        self.assertEqual(ids, ["dash-v32-7", "dash-a30280", "html5-mp4"])
        payload = _map_payload("bilibili", result, "BV1live00001")
        self.assertEqual(payload["bvid"], "BV1live00001")
        self.assertEqual([value["baseUrl"] for value in payload["dash"]["video"]], ["https://cdn.test/v480.m4s"])
        self.assertEqual([value["baseUrl"] for value in payload["dash"]["audio"]], ["https://cdn.test/a.m4s"])
        self.assertEqual([value["url"] for value in payload["durl"]], ["https://cdn.test/360.mp4"])

    def test_selected_part_keeps_part_identity(self):
        result = self.inspect("https://www.bilibili.com/video/BV1live00001?p=2")
        self.assertEqual((result["id"], result["duration"]), ("BV1live00001_p2", 40))
        self.assertEqual(_map_payload("bilibili", result, "BV1live00001_p2")["part"], 2)

    def test_deleted_video_is_not_found_not_network(self):
        with self.assertRaises(SourceError) as context:
            self.inspect("https://www.bilibili.com/video/BV1gone00001")
        self.assertEqual(context.exception.code, SourceErrorCode.NOT_FOUND)

    def test_blocked_view_api_uses_the_server_rendered_page(self):
        result = self.inspect("https://www.bilibili.com/video/BV1page00001/")
        self.assertEqual((result["id"], result["title"], result["duration"]), ("BV1page00001", "正片", 16690))
        self.assertEqual([value["format_id"] for value in result["formats"]], ["dash-v32-7", "dash-a30280", "html5-mp4"])

    def test_blocked_view_api_and_page_fall_back_to_the_page_list(self):
        result = self.inspect("https://www.bilibili.com/video/BV1list00001/")
        self.assertEqual((result["id"], result["title"], result["duration"]), ("BV1list00001", "only part", 120))
        self.assertEqual(len(result["formats"]), 3)

    def test_risk_control_is_rate_limited_and_retryable(self):
        with self.assertRaises(SourceError) as context:
            self.inspect("https://www.bilibili.com/video/BV1limit0001")
        self.assertEqual(context.exception.code, SourceErrorCode.RATE_LIMITED)
        self.assertTrue(context.exception.retryable)


if __name__ == "__main__":
    unittest.main()
