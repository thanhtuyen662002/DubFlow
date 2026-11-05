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
import tempfile
import time
import unittest

from engine.dubflow.download.douyin.adapter import DouyinSourceAdapter
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

    def test_without_a_details_folder_nothing_changes(self):
        adapter = DouyinSourceAdapter(self.transport)
        self.write(DETAIL)
        with self.assertRaises(SourceError):
            adapter.inspect(VIDEO)


if __name__ == "__main__":
    unittest.main()
