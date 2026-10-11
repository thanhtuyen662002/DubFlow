from __future__ import annotations

import hashlib
from http.cookiejar import CookieJar
from io import BytesIO
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit
import zipfile

from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.authenticated_native import scoped_cookies
from engine.dubflow.download.douyin import creator_native as native
from engine.dubflow.download.douyin import request_signature as signature
from engine.dubflow.download.enumeration.sdk import sdk_page
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode

URL = "https://www.douyin.com/user/MS4wRecordedCreator"
ROOT = Path(__file__).resolve().parents[2]


def raw_page(items=None, *, more=0, position=0):
    return {"status_code": 0, "aweme_list": items or [], "has_more": more, "max_cursor": position}


class Response(BytesIO):
    def __init__(self, request, data, status=200, final_url=None):
        super().__init__(data)
        self.status, self.url = status, final_url or request.full_url
    def geturl(self):
        return self.url


class Downloader:
    def __init__(self):
        self.cookiejar = CookieJar()
        for cookie in scoped_cookies("douyin", {"Cookie": "msToken=synthetic-secret"}):
            self.cookiejar.set_cookie(cookie)
    def extract_info(self, *args, **kwargs):
        raise AssertionError("creator backend must not enter generic extraction")


class DouyinNativePageTests(unittest.TestCase):
    def test_unpaired_surrogate_titles_are_item_failures_and_valid_emoji_survives(self):
        for bad in ("bad\ud800title", "bad\udffftitle"):
            with self.subTest(title=ascii(bad)):
                response = raw_page([
                    {"aweme_id": "123456", "desc": "Before"},
                    {"aweme_id": "234567", "desc": bad},
                    {"aweme_id": "345678", "desc": "After \U0001f600"},
                ])
                result = native.parse_page(json.dumps(response, ensure_ascii=True).encode(), page_size=3)
                self.assertEqual(result["entries"], [
                    {"id": "123456", "title": "Before"},
                    {"id": "234567", "error_code": "SOURCE_CHANGED"},
                    {"id": "345678", "title": "After \U0001f600"},
                ])
                json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_pinned_upstream_signature_vector_and_standard_sm3(self):
        self.assertEqual(signature._sm3(b"abc"), "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0")
        saved = random.getstate()
        try:
            random.seed(1234)
            with patch.object(signature.time, "time", return_value=1_700_000_000.125):
                actual = signature.ABogus(fp="fixed", user_agent="known-UA").generate_abogus("aid=6383&max_cursor=900&count=2")
            # Recorded directly from verified F2 7dab3e2, not recomputed by the test.
            self.assertEqual(actual[1], "E6mwBV81k3LBfE6Y56KLfY3q6f33Y//I0SVkMD2fgx3-qL39HMTt9exoIBGvXFbjwG/-Iabjy4gFC1BI7sm=")
            self.assertEqual(actual[0], "aid=6383&max_cursor=900&count=2&a_bogus=" + actual[1])
        finally:
            random.setstate(saved)
        with patch.object(signature.hashlib, "new", side_effect=ValueError("missing")), self.assertRaises(NotImplementedError):
            signature._sm3(b"abc")

    def test_provider_metadata_is_bounded_and_poisoned_items_keep_neighbors(self):
        values = [{"aweme_id": "123456", "desc": "Good\nvideo", "secret": "synthetic-secret"},
                  {"aweme_id": "234567", "status": {"is_private": 1}},
                  {"aweme_id": "345678", "status": {"is_delete": True}},
                  {"aweme_id": "456789", "images": [{}]}, {"aweme_id": "567890", "desc": "bad\0title"},
                  {"aweme_id": "678901", "status": {"is_private": 1.0}}, None,
                  {"aweme_id": "789012", "desc": "长" * 1500}]
        result = native.parse_page(json.dumps(raw_page(values, more=1, position=900)).encode(), page_size=10)
        self.assertEqual(result["entries"][0], {"id": "123456", "title": "Good video"})
        self.assertEqual([entry["error_code"] for entry in result["entries"][1:6]],
                         ["PRIVATE", "NOT_FOUND", "UNSUPPORTED", "SOURCE_CHANGED", "SOURCE_CHANGED"])
        self.assertIsNone(result["entries"][6])
        self.assertEqual(len(result["entries"][-1]["title"]), 1024)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_missing_status_duplicate_keys_empty_continuation_bounds_and_deep_json_refused(self):
        cases = [b"", b"<html>login</html>", b'{"status_code":0,"status_code":0}',
                 json.dumps({"aweme_list": []}).encode(), json.dumps(raw_page(more=True, position=900)).encode(),
                 json.dumps(raw_page([None], more=1, position=0)).encode(),
                 json.dumps(raw_page([None], more=1.0, position=900)).encode(),
                 json.dumps(raw_page([None], more=1, position=True)).encode(),
                 json.dumps(raw_page([None], more=1, position=2**63)).encode(),
                 json.dumps(raw_page([None, None])).encode(), b"[" * 2000 + b"0" + b"]" * 2000,
                 b" " * (native.MAX_RESPONSE_BYTES + 1)]
        for data in cases:
            with self.subTest(size=len(data)), self.assertRaises(native.ProviderPageError) as error:
                native.parse_page(data, page_size=1)
            self.assertEqual(error.exception.native_exit_code, 5)

    def test_request_origin_cookie_scope_one_call_no_redirect_and_bounded_read(self):
        calls = []
        class Opener:
            def open(self, request, *, timeout):
                calls.append((request, timeout))
                return Response(request, json.dumps(raw_page([{"aweme_id": "123456", "desc": "Good"}], more=1, position=600)).encode())
        def build(*handlers):
            redirect = next(handler for handler in handlers if isinstance(handler, native._NoRedirect))
            self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://evil.test/"))
            cookies = next(handler for handler in handlers if isinstance(handler, native.urllib.request.HTTPCookieProcessor))
            self.assertEqual([cookie.domain for cookie in cookies.cookiejar], [".douyin.com"])
            return Opener()
        with patch.object(native.urllib.request, "build_opener", side_effect=build):
            result = native.fetch_page(Downloader(), URL, position=900, page_size=2, signer=signature.ABogus)
        self.assertTrue(result["has_more"])
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", "www.douyin.com", "/aweme/v1/web/aweme/post/"))
        query = parse_qs(parsed.query)
        self.assertEqual(query["max_cursor"], ["900"])
        self.assertEqual(query["count"], ["2"])
        self.assertEqual(query["msToken"], ["synthetic-secret"])
        self.assertIn("a_bogus", query)
        self.assertEqual(timeout, 30)
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_http_failures_redirect_and_network_errors_are_redacted_without_retry(self):
        for status, expected in ((401, 2), (403, 2), (429, 3), (404, 4), (503, 6), (302, 5)):
            error = urllib.error.HTTPError("https://www.douyin.com/?synthetic-secret", status, "synthetic-secret", {}, None)
            opener = unittest.mock.Mock()
            opener.open.side_effect = error
            with self.subTest(status=status), patch.object(native.urllib.request, "build_opener", return_value=opener), self.assertRaises(native.ProviderPageError) as failure:
                native.fetch_page(Downloader(), URL, position=0, page_size=1, signer=signature.ABogus)
            self.assertEqual(failure.exception.native_exit_code, expected)
            self.assertNotIn("synthetic-secret", str(failure.exception))
            self.assertEqual(opener.open.call_count, 1)
        opener = unittest.mock.Mock()
        opener.open.side_effect = OSError("synthetic-secret")
        with patch.object(native.urllib.request, "build_opener", return_value=opener), self.assertRaises(native.ProviderPageError) as failure:
            native.fetch_page(Downloader(), URL, position=0, page_size=1, signer=signature.ABogus)
        self.assertEqual(failure.exception.native_exit_code, 6)

    def test_echoed_token_no_progress_and_invalid_request_never_publish_success(self):
        for data in (raw_page([{"aweme_id": "123456", "desc": "synthetic-secret"}]),
                     raw_page([{"aweme_id": "123456", "desc": "Good"}], more=1, position=900)):
            opener = unittest.mock.Mock()
            opener.open.side_effect = lambda request, **kwargs: Response(request, json.dumps(data).encode())
            with patch.object(native.urllib.request, "build_opener", return_value=opener), self.assertRaises(native.ProviderPageError) as error:
                native.fetch_page(Downloader(), URL, position=900, page_size=1, signer=signature.ABogus)
            self.assertEqual(error.exception.native_exit_code, 5)
            self.assertNotIn("synthetic-secret", str(error.exception))
        with patch.object(native.urllib.request, "build_opener") as opener:
            for url, position, size in (("https://evil.test/user/x", 0, 1), (URL + "?token=x", 0, 1),
                (URL, True, 1), (URL, 1.0, 1), (URL, 0, True), (URL, 0, 21)):
                with self.assertRaises(native.ProviderPageError):
                    native.fetch_page(Downloader(), url, position=position, page_size=size, signer=signature.ABogus)
            opener.assert_not_called()


RECORDED_SDK = '''
from http.cookiejar import CookieJar
from io import BytesIO
import json
import urllib.request
from urllib.parse import parse_qs, urlsplit
class Response(BytesIO):
    def __init__(self, url, data, status=200):
        super().__init__(data)
        self.status, self.url = status, url
    def geturl(self): return self.url
def build_opener(*handlers):
    jar = next(h.cookiejar for h in handlers if hasattr(h, 'cookiejar'))
    assert type(handlers[0]).__name__ == '_NoRedirect'
    assert len(list(jar)) == 1 and list(jar)[0].domain == '.douyin.com'
    assert list(jar)[0].value == 'synthetic-secret'
    class Opener:
        def open(self, request, timeout):
            assert timeout == 30 and 'Cookie' not in request.headers
            parts = urlsplit(request.full_url)
            assert parts.netloc == 'www.douyin.com' and parts.path == '/aweme/v1/web/aweme/post/'
            query = parse_qs(parts.query)
            assert query['msToken'] == ['synthetic-secret'] and 'a_bogus' in query
            creator = query['sec_user_id'][0]
            if creator == 'deny403': return Response(request.full_url, b'', 403)
            if creator == 'rate429': return Response(request.full_url, b'', 429)
            if creator == 'badshape': return Response(request.full_url, b'{}')
            position = int(query['max_cursor'][0])
            data = {'status_code':0, 'aweme_list':[{'aweme_id':'123456' if position == 0 else '234567','desc':'Recorded'}],
                'has_more':1 if position == 0 else 0, 'max_cursor':900 if position == 0 else 0}
            return Response(request.full_url, json.dumps(data).encode())
    return Opener()
urllib.request.build_opener = build_opener
class YoutubeDL:
    def __init__(self, options):
        assert options['js_runtimes'] == {} and options['cachedir'] is False and options['retries'] == 0
        self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, *args, **kwargs): raise AssertionError('GenericIE must not run')
'''


class DouyinNativeChildTests(unittest.TestCase):
    def test_actual_isolated_child_pinned_modules_stdin_session_pages_and_tamper_refusal(self):
        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory(dir=python.parent) as temp:
            work = Path(temp)
            helper = work / "authenticated_native.py"
            helper.write_bytes((ROOT / "engine/dubflow/download/authenticated_native.py").read_bytes())
            paths = {"python": python, "helper": helper, "sdk_archive": work / "recorded-sdk.zip"}
            for key, filename in (("douyin_creator", "creator_native.py"), ("douyin_signer", "request_signature.py")):
                target = work / "douyin" / filename
                target.parent.mkdir(exist_ok=True)
                target.write_bytes((ROOT / "engine/dubflow/download/douyin" / filename).read_bytes())
                paths[key] = target
            with zipfile.ZipFile(paths["sdk_archive"], "w") as archive:
                archive.writestr("yt_dlp/__init__.py", RECORDED_SDK)
                archive.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
            pins = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
            root = Path(os.path.commonpath([str(python), str(helper)]))
            transport = AuthenticatedYtDlpTransport(runtime_root=root, pins=pins, timeout_s=10,
                python=python, helper=helper, sdk_archive=paths["sdk_archive"],
                douyin_creator=paths["douyin_creator"], douyin_signer=paths["douyin_signer"])
            headers = {"Cookie": "msToken=synthetic-secret"}
            first = sdk_page(transport, "douyin", URL, cursor=None, page_size=1, session=headers)
            second = sdk_page(transport, "douyin", URL, cursor=first.next_cursor, page_size=1, session=headers)
            self.assertEqual(second.items[0].identity.source_id, "234567")
            self.assertTrue(second.completed)
            self.assertNotIn("synthetic-secret", first.next_cursor)
            for creator, expected in (("deny403", SourceErrorCode.AUTH_REQUIRED), ("rate429", SourceErrorCode.RATE_LIMITED), ("badshape", SourceErrorCode.SOURCE_CHANGED)):
                with self.assertRaises(SourceError) as error:
                    transport.enumerate_douyin_page("https://www.douyin.com/user/" + creator, position=0, page_size=1, headers=headers)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("synthetic-secret", str(error.exception))
            old = paths["douyin_signer"].read_bytes()
            paths["douyin_signer"].write_bytes(old + b'\nraise RuntimeError("synthetic-secret")\n')
            with patch.object(transport.runner, "run_with_input") as runner, self.assertRaises(SourceError) as error:
                transport.enumerate_douyin_page(URL, position=0, page_size=1, headers=headers)
            self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)
            runner.assert_not_called()
            # Exercise the child's second verification directly, bypassing the
            # parent preflight deliberately. Tampered bytes must never execute.
            request = {"schema_version": 1, "operation": "douyin_creator", "provider_id": "douyin", "url": URL,
                "position": 0, "page_size": 1, "headers": headers,
                "douyin_modules": {"creator": pins["douyin_creator"], "signature": pins["douyin_signer"]}}
            code, stdout, stderr = transport.runner.run_with_input([str(python), "-I", "-S", "-B", str(helper), str(paths["sdk_archive"])],
                timeout_s=10, stdin_bytes=json.dumps(request).encode())
            self.assertEqual(code, 7)
            self.assertEqual(stdout, "")
            self.assertNotIn("synthetic-secret", stderr)


if __name__ == "__main__":
    unittest.main()
