"""Douyin video details read through DubFlow's own Douyin page.

Douyin refuses the SDK's non-browser detail request (the SDK then reports
"fresh cookies are needed", which surfaced as a login prompt for users who
were logged in). The desktop app reads the details through douyin.com in its
own browser and leaves them in control/source-page-details.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from engine.dubflow.download import materializer as materializer_module
from engine.dubflow.download.douyin.adapter import DouyinSourceAdapter
from engine.dubflow.download.materializer import MediaMaterializer
from engine.dubflow.download.provider_transport import PublicProviderHttpTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode

VIDEO = "7699053575086918966"
DETAIL = {"aweme_id": VIDEO, "desc": "Phim hay", "duration": 143489,
          "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/720.mp4", "https://v26-web.douyinvod.com/720.mp4"]},
                    "width": 720, "height": 1280}}


class RefusingTransport:
    def __init__(self):
        self.calls = 0

    def fetch_video(self, source_ref, session=None):
        self.calls += 1
        raise SourceError(SourceErrorCode.AUTH_REQUIRED, "fresh cookies are needed", provider_id="douyin")


class PageDetailTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.transport = RefusingTransport()
        self.adapter = DouyinSourceAdapter(self.transport, page_details=self.folder)

    def write(self, value, name=VIDEO):
        path = self.folder / f"douyin-{name}.json"
        path.write_text(json.dumps(value))
        return path

    def test_page_details_are_used_without_the_refused_request(self):
        self.write(DETAIL)
        item = self.adapter.inspect(f"https://www.douyin.com/video/{VIDEO}")
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual((item.identity.source_id, item.title), (VIDEO, "Phim hay"))
        self.assertEqual(item.duration_ticks, 143489 * 90)
        selected = self.adapter.select_download(item)
        self.assertTrue(selected.locator.startswith("https://") and "douyinvod.com" in selected.locator)
        self.assertEqual((selected.width, selected.height, selected.has_audio), (720, 1280, True))

    def test_stale_foreign_or_broken_details_fall_back_to_the_service(self):
        cases = {
            "stale": lambda: os.utime(self.write(DETAIL), (time.time() - 3600, time.time() - 3600)),
            "other video": lambda: self.write({**DETAIL, "aweme_id": "7699053575086918967"}),
            "broken": lambda: (self.folder / f"douyin-{VIDEO}.json").write_text("{"),
            "missing": lambda: None,
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                for path in self.folder.iterdir():
                    path.unlink()
                prepare()
                calls = self.transport.calls
                with self.assertRaises(SourceError):
                    self.adapter.inspect(VIDEO)
                self.assertEqual(self.transport.calls, calls + 1)

    def test_page_details_behind_a_proxy_never_send_media_through_it(self):
        # #884 interim: fresh page details skip the helper (and its exit 9), so the media transport itself must refuse
        # a proxied request. The download fails with the typed, non-retryable code and the proxy sees nothing.
        self.write(DETAIL)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        proxy = f"http://127.0.0.1:{listener.getsockname()[1]}"
        environment = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
        environment.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy, "https_proxy": proxy})
        headers = {"User-Agent": "Mozilla/5.0 recorded", "Accept": "*/*", "Accept-Language": "vi"}
        with patch.dict(os.environ, environment, clear=True):
            adapter = DouyinSourceAdapter(self.transport, page_details=self.folder,
                                          materializer=MediaMaterializer(PublicProviderHttpTransport("douyin", headers)))
        item = adapter.inspect(VIDEO)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(materializer_module.socket, "create_connection") as create, \
                self.assertRaises(SourceError) as context:
            adapter.download(item, Path(temp) / "final.mp4", root=temp)
        self.assertIs(context.exception.code, SourceErrorCode.PROXY_UNSUPPORTED)
        self.assertFalse(context.exception.retryable)
        self.assertEqual(context.exception.action, "disable_proxy")
        self.assertEqual(self.transport.calls, 0)
        create.assert_not_called()
        listener.settimeout(0.5)
        with self.assertRaises((socket.timeout, TimeoutError)):
            listener.accept()[0].close()

    def test_without_a_details_folder_nothing_changes(self):
        adapter = DouyinSourceAdapter(self.transport)
        self.write(DETAIL)
        with self.assertRaises(SourceError):
            adapter.inspect(VIDEO)


if __name__ == "__main__":
    unittest.main()
