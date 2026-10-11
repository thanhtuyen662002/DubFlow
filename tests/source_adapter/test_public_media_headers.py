from __future__ import annotations

import unittest
from unittest.mock import Mock

from engine.dubflow.download.materializer import DownloadError
from engine.dubflow.download.provider_transport import PublicProviderHttpTransport


PUBLIC = {"User-Agent": "Recorded SDK UA", "Accept": "*/*", "Accept-Language": "en"}


class PublicMediaHeaderTests(unittest.TestCase):
    def test_exact_public_defaults_fixed_referer_and_range_validators(self):
        for provider in ("bilibili", "douyin"):
            delegate = Mock()
            defaults = dict(PUBLIC)
            transport = PublicProviderHttpTransport(provider, defaults, transport=delegate)
            defaults["User-Agent"] = "changed outside wrapper"
            transport.open("https://cdn.example.test/media", headers={"Range": "bytes=123-", "If-Range": '"recorded-etag"', "Accept-Encoding": "identity"})
            delegate.open.assert_called_once_with("https://cdn.example.test/media", headers={**PUBLIC,
                "Referer": f"https://www.{provider}.com/", "Range": "bytes=123-", "If-Range": '"recorded-etag"', "Accept-Encoding": "identity"})

    def test_credentials_and_header_injection_stop_before_delegate(self):
        delegate = Mock()
        transport = PublicProviderHttpTransport("bilibili", PUBLIC, transport=delegate)
        for headers in ({"Cookie": "synthetic-secret"}, {"Authorization": "synthetic-secret"},
                        {"Proxy-Authorization": "synthetic-secret"}, {"Referer": "https://user:synthetic-secret@bad.test/"},
                        {"Range": "bytes=0-\r\nCookie: synthetic-secret"}, {"User-Agent": "unreviewed"},
                        {"Range": "bytes=0-", "range": "bytes=2-"}, {"If-Range": "x" * 1025}):
            with self.subTest(headers=headers), self.assertRaises(DownloadError) as error:
                transport.open("https://cdn.example.test/media", headers=headers)
            self.assertNotIn("synthetic-secret", str(error.exception))
        delegate.open.assert_not_called()

    def test_descriptor_defaults_cannot_introduce_credentials_or_controls(self):
        for defaults in ({**PUBLIC, "Cookie": "synthetic-secret"}, {**PUBLIC, "Accept": "*/*\nCookie: bad"},
                         {"User-Agent": "recorded"}, {**PUBLIC, "User-Agent": "x" * 513}):
            with self.assertRaises(ValueError):
                PublicProviderHttpTransport("bilibili", defaults)


if __name__ == "__main__":
    unittest.main()
