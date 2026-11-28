"""Fixed CDN rules (#982): Xinpianchang's media needs its site's Referer and a byte Range, nothing else."""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from engine.dubflow.download.generic.adapter import GenericUrlAdapter
from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, HttpResponse, MediaMaterializer
from engine.dubflow.download.provider_transport import PublicProviderHttpTransport
from engine.dubflow.download.site_rules import rule_for


PUBLIC = {"User-Agent": "Recorded SDK UA", "Accept": "*/*", "Accept-Language": "en"}
MEDIA = "https://us-xpc5.xpccdn.com/9d49762b.mp4?e=1795856518&s=20965c503d9ff3421b6a&sv=3"
BODY = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40


class XpcCdn:
    """Answers like xpccdn.com: 403 without the Referer or without a Range, 206 for a range."""

    def __init__(self):
        self.requests = []

    def open(self, url, *, headers=None):
        headers = dict(headers or {})
        self.requests.append(headers)
        if headers.get("Referer") != "https://www.xinpianchang.com/" or "Range" not in headers or "Cookie" in headers:
            raise DownloadError(DownloadErrorCode.AUTH_REQUIRED, "media endpoint requires authentication", action="authenticate")
        first, _, last = headers["Range"].removeprefix("bytes=").partition("-")
        start, end = int(first), int(last) if last else len(BODY) - 1
        part = BODY[start:end + 1]
        return HttpResponse(206, {"Content-Type": "video/mp4", "Content-Length": str(len(part)), "Accept-Ranges": "bytes",
                                  "Content-Range": f"bytes {start}-{start + len(part) - 1}/{len(BODY)}"}, io.BytesIO(part), url)


class RuleTests(unittest.TestCase):
    def test_only_xinpianchang_media_hosts_have_a_rule(self):
        rule = rule_for(MEDIA)
        self.assertEqual((rule.referer, rule.range_only), ("https://www.xinpianchang.com/", True))
        self.assertIsNotNone(rule_for("https://xpccdn.com/a.mp4"))
        for url in ("http://us-xpc5.xpccdn.com/a.mp4", "https://evilxpccdn.com/a.mp4", "https://xpccdn.com.evil.test/a.mp4",
                    "https://v2.kwaicdn.com/a.mp4", "https://cn-videos.dji.net/v/1080.mp4", "not a url", "https://[bad/"):
            self.assertIsNone(rule_for(url), url)

    def test_the_public_transport_adds_the_referer_and_a_whole_range(self):
        delegate = Mock()
        transport = PublicProviderHttpTransport("generic", PUBLIC, transport=delegate)
        transport.open(MEDIA, headers={"Accept-Encoding": "identity"})
        delegate.open.assert_called_with(MEDIA, headers={**PUBLIC, "Referer": "https://www.xinpianchang.com/", "Range": "bytes=0-",
                                                         "Accept-Encoding": "identity"})
        # A caller's own range (segments, resume, probes) is kept as it is.
        transport.open(MEDIA, headers={"Range": "bytes=100-199"})
        delegate.open.assert_called_with(MEDIA, headers={**PUBLIC, "Referer": "https://www.xinpianchang.com/", "Range": "bytes=100-199"})
        # Any other host: nothing added.
        transport.open("https://v2.kwaicdn.com/a.mp4")
        delegate.open.assert_called_with("https://v2.kwaicdn.com/a.mp4", headers=PUBLIC)
        # Provider media keeps its own fixed Referer and gets no range.
        bilibili = Mock()
        PublicProviderHttpTransport("bilibili", PUBLIC, transport=bilibili).open(MEDIA)
        bilibili.open.assert_called_once_with(MEDIA, headers={**PUBLIC, "Referer": "https://www.bilibili.com/"})


class GenericXinpianchangTests(unittest.TestCase):
    def test_a_rule_url_is_inspected_directly_and_downloaded_whole(self):
        cdn = XpcCdn()
        materializer = MediaMaterializer(PublicProviderHttpTransport("generic", PUBLIC, transport=cdn))
        extractor = Mock()
        extractor.inspect_url.side_effect = AssertionError("the extractor's plain GET is refused by this CDN")
        adapter = GenericUrlAdapter(extractor, materializer=materializer)
        item = adapter.inspect(MEDIA)
        extractor.inspect_url.assert_not_called()
        self.assertEqual(item.media_candidates[0].mime_type, "video/mp4")
        with tempfile.TemporaryDirectory() as folder:
            result = adapter.download(item, Path(folder) / "xpc.mp4")
            self.assertEqual(Path(result.path).read_bytes(), BODY)
        self.assertTrue(all(request.get("Range") for request in cdn.requests))
        self.assertTrue(all("Cookie" not in request and "Authorization" not in request for request in cdn.requests))

    def test_other_urls_still_go_to_the_extractor(self):
        extractor = Mock()
        extractor.inspect_url.return_value = {"id": "x", "title": "t", "url": "https://v2.kwaicdn.com/a.mp4", "ext": "mp4"}
        adapter = GenericUrlAdapter(extractor, materializer=MediaMaterializer(PublicProviderHttpTransport("generic", PUBLIC, transport=Mock())))
        adapter.inspect("https://v2.kwaicdn.com/a.mp4")
        extractor.inspect_url.assert_called_once()


if __name__ == "__main__":
    unittest.main()
