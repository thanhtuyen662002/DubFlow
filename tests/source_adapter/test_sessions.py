from __future__ import annotations

from http.cookiejar import CookieJar
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.request import Request

from engine.dubflow.download.authenticated_native import inspect, scoped_cookies
from engine.dubflow.download.sessions import ProtectedSessionBridge, WindowsUserDpapi, validated_session_headers
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class RecordedProtector:
    """Hermetic capability fixture; native DPAPI is tested separately."""
    def __init__(self):
        self.values = {}

    def protect(self, data, context):
        key = hashlib.sha256(data + context).digest()
        self.values[(key, context)] = data
        return key

    def unprotect(self, data, context):
        return self.values[(data, context)]


class ProtectedSessionTests(unittest.TestCase):
    def test_restart_expiry_binding_clear_and_no_plaintext_in_record(self):
        protector, now = RecordedProtector(), [1000]
        with tempfile.TemporaryDirectory() as temp:
            bridge = ProtectedSessionBridge(temp, protector=protector, clock=lambda: now[0])
            bridge.save("douyin", {"Cookie": "session=private-cookie"}, expires_at=1500)
            record = Path(temp) / "douyin.session.json"
            self.assertNotIn(b"private-cookie", record.read_bytes())
            restarted = ProtectedSessionBridge(temp, protector=protector, clock=lambda: now[0])
            self.assertEqual(restarted.get_opaque_headers("douyin"), {"Cookie": "session=private-cookie"})
            # Identical encrypted bytes cannot be rebound to another provider.
            (Path(temp) / "bilibili.session.json").write_bytes(record.read_bytes())
            with self.assertRaises(SourceError):
                restarted.get_opaque_headers("bilibili")
            now[0] = 1500
            with self.assertRaises(SourceError) as context:
                restarted.get_opaque_headers("douyin")
            self.assertEqual(context.exception.code, SourceErrorCode.AUTH_REQUIRED)
            self.assertNotIn("private-cookie", str(context.exception))
            restarted.clear("douyin")
            self.assertFalse(record.exists())

    def test_malformed_oversized_or_injected_headers_and_provider_rejected(self):
        for headers in ({}, {"Cookie": "x=y\r\nInjected: bad"}, {"Authorization": "Bearer secret"},
                        {"Cookie": "x" * 2049}, {"Cookie": "x=y", "cookie": "z=w"}):
            with self.subTest(headers=headers), self.assertRaises(SourceError):
                validated_session_headers(headers)
        with tempfile.TemporaryDirectory() as temp:
            bridge = ProtectedSessionBridge(temp, protector=RecordedProtector(), clock=lambda: 1000)
            for provider, expiry in (("../escape", 1200), ("douyin", 999), ("douyin", 1000+86401), ("douyin", True)):
                with self.subTest(provider=provider, expiry=expiry), self.assertRaises(SourceError):
                    bridge.save(provider, {"Cookie": "x=y"}, expires_at=expiry)
            record = Path(temp) / "douyin.session.json"
            for content in (b"garbled", json.dumps({"schema_version": 1, "ciphertext": "!"}).encode(), b"x" * (32*1024+1)):
                record.write_bytes(content)
                with self.assertRaises(SourceError):
                    bridge.get_opaque_headers("douyin")

    @unittest.skipUnless(os.name == "nt", "native Windows-user DPAPI lane")
    def test_native_windows_dpapi_restart_tamper_and_provider_entropy(self):
        with tempfile.TemporaryDirectory() as temp:
            expiry = int(time.time()) + 600
            bridge = ProtectedSessionBridge(temp)
            bridge.save("bilibili", {"Cookie": "SESSDATA=synthetic-native-secret"}, expires_at=expiry)
            record = Path(temp) / "bilibili.session.json"
            self.assertNotIn(b"synthetic-native-secret", record.read_bytes())
            self.assertEqual(ProtectedSessionBridge(temp).get_opaque_headers("bilibili"), {"Cookie": "SESSDATA=synthetic-native-secret"})
            (Path(temp) / "douyin.session.json").write_bytes(record.read_bytes())
            with self.assertRaises(SourceError):
                ProtectedSessionBridge(temp).get_opaque_headers("douyin")
            raw = json.loads(record.read_text())
            import base64
            ciphertext = bytearray(base64.b64decode(raw["ciphertext"]))
            ciphertext[-1] ^= 1
            raw["ciphertext"] = base64.b64encode(ciphertext).decode()
            record.write_text(json.dumps(raw))
            with self.assertRaises(SourceError):
                ProtectedSessionBridge(temp).get_opaque_headers("bilibili")

    def test_cookie_jar_does_not_send_credentials_outside_provider_or_over_http(self):
        jar = CookieJar()
        for cookie in scoped_cookies("bilibili", {"Cookie": "SESSDATA=secret"}):
            jar.set_cookie(cookie)
        for url, allowed in (("https://www.bilibili.com/video/one", True), ("https://api.bilibili.com/api", True),
                             ("http://www.bilibili.com/video/one", False), ("https://bilibili.com.evil.test/", False),
                             ("https://cdn.example.test/video", False), ("https://www.douyin.com/", False)):
            with self.subTest(url=url):
                request = Request(url)
                jar.add_cookie_header(request)
                self.assertEqual(request.has_header("Cookie"), allowed)
        with self.assertRaises(ValueError):
            scoped_cookies("bilibili", {"Cookie": "secret=x; Domain=evil.test"})

    def test_sdk_options_cookie_scope_and_metadata_strip_credential_fields(self):
        observations = {}

        class Downloader:
            def __init__(self, options):
                observations["options"] = options
                self.cookiejar = CookieJar()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                observations["url"] = url
                observations["cookies"] = list(self.cookiejar)
                return {"id": "123456", "title": "video", "cookies": "secret", "http_headers": {"Cookie": "secret"},
                        "formats": [{"url": "https://cdn.example.test/v.mp4", "cookies": "secret", "http_headers": {"Authorization": "secret"}}],
                        "subtitles": {"vi": [{"url": "https://cdn.example.test/sub.vtt", "ext": "vtt", "cookies": "secret"}]}}

        request = {"provider_id": "douyin", "url": "https://www.douyin.com/video/123456", "headers": {"Cookie": "session=secret"}}
        result = inspect(request, Downloader)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("http_headers", observations["options"])
        self.assertFalse(observations["options"]["cachedir"])
        self.assertEqual(observations["options"]["js_runtimes"], {})
        self.assertTrue(all(cookie.domain == ".douyin.com" and cookie.secure for cookie in observations["cookies"]))
        request["url"] = "https://evil.test/video/123456"
        with self.assertRaises(ValueError):
            inspect(request, Downloader)


if __name__ == "__main__":
    unittest.main()
