"""MCP source tools (#994): search_sources, suffixes, trends and download_video's URL handling."""
from __future__ import annotations

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.docu import mcp_tools
from engine.dubflow.docu.project import DocuError
from engine.dubflow.mcp import sources
from engine.dubflow.mcp.server import SERVER, TOOLS, Server, serve


class SourceToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.server = Server(mcp_tools.Install(root, Path("ffmpeg"), Path("ffprobe"), root), roots=lambda: [], activity=False)

    def tearDown(self):
        self.temp.cleanup()

    def rpc(self, *calls):
        lines = [{"jsonrpc": "2.0", "id": index + 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
                 for index, (name, args) in enumerate(calls)]
        out = io.StringIO()
        serve(self.server, io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n"), out)
        return [json.loads(line)["result"] for line in out.getvalue().splitlines()]

    def test_the_server_is_dubflow_wide_and_lists_the_source_tools(self):
        self.assertEqual(SERVER["title"], "DubFlow")
        names = [tool["name"] for tool in TOOLS]
        for name in ("search_sources", "list_footage_suffixes", "get_trends", "suggest_keywords", "download_video", "create_project"):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)))
        search = next(tool for tool in TOOLS if tool["name"] == "search_sources")
        self.assertTrue(search["annotations"]["readOnlyHint"])
        self.assertEqual(search["inputSchema"]["required"], ["query"])

    def test_search_sources_passes_its_arguments_and_reports_errors(self):
        calls = []

        def fake(query, **kwargs):
            calls.append({"query": query, **kwargs})
            if kwargs["youtube_filter"] not in ("4k", "hd", "any", "cc"):
                raise ValueError("youtube_filter must be 4k, hd, any or cc")
            return {"queries": {}, "sources": {"youtube": {"count": 1}}, "items": [{"page_url": "https://x.test/1"}]}
        with patch("engine.dubflow.mcp.sources.footage.search", fake):
            ok, bad = self.rpc(("search_sources", {"query": "白鹤滩", "suffixes": ["空镜"], "sources": ["youtube"], "youtube_filter": "cc",
                                                  "translate": False}),
                               ("search_sources", {"query": "x", "youtube_filter": "8k"}))
        self.assertFalse(ok["isError"])
        seen = calls[0]
        self.assertEqual((seen["query"], seen["suffixes"], seen["sources"], seen["youtube_filter"], seen["translate"]),
                         ("白鹤滩", ["空镜"], ["youtube"], "cc", False))
        self.assertTrue(bad["isError"])

    def test_every_tool_has_a_vietnamese_activity_label(self):
        from engine.dubflow.mcp.activity import label
        for tool in TOOLS:
            with self.subTest(tool["name"]):
                self.assertNotEqual(label(tool["name"], {}), tool["name"])

    def test_suffixes_are_listed(self):
        [result] = self.rpc(("list_footage_suffixes", {}))
        listed = json.loads(result["content"][0]["text"])
        self.assertEqual(listed[0]["zh"], "空镜")
        self.assertTrue(all(set(item) == {"zh", "en", "vi"} for item in listed))

    def test_download_urls(self):
        url, extra = sources.resolve_download_url("https://www.skypixel.com/videos/4k-dji-air-3-54c90d82",
                                                  media=lambda slug: {"url": f"https://cn-videos.dji.net/{slug}/1080.mp4",
                                                                      "quality": "1080p", "license": "SkyPixel Use"})
        self.assertEqual(url, "https://cn-videos.dji.net/4k-dji-air-3-54c90d82/1080.mp4")
        self.assertEqual(extra, {"license": "SkyPixel Use", "rendition": "1080p"})
        self.assertEqual(sources.resolve_download_url("https://www.youtube.com/watch?v=93kgTcANF1E"),
                         ("https://www.youtube.com/watch?v=93kgTcANF1E", {}))
        with self.assertRaises(DocuError):
            sources.resolve_download_url("https://www.xinpianchang.com/a13885930")


if __name__ == "__main__":
    unittest.main()
