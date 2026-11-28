"""Footage search for agents (#994): the same rules as Khám phá's Tất cả nguồn, offline."""

from pathlib import Path
import re
import unittest

from engine.dubflow.discovery import footage
from engine.dubflow.discovery.http import DiscoveryError
from engine.dubflow.discovery.models import DiscoveryPage, VideoItem

ROOT = Path(__file__).resolve().parents[2]


def video(source, index, **extra):
    return VideoItem(source, f"{source}{index}", f"{source} {index}", "author", "1", 60, 100, None, "",
                     f"https://{source}.test/{index}", **extra)


class Recorder:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, source, query, options):
        self.calls.append((source, query, dict(options)))
        answer = self.answers[source]
        if callable(answer):
            return answer(query, options)
        if isinstance(answer, Exception):
            raise answer
        return answer


TRANSLATORS = (lambda text: {"đập thủy điện": "水电站大坝"}.get(text, text),
               lambda text: {"水电站大坝": "hydropower dam", "白鹤滩水电站": "Baihetan Hydropower Station"}.get(text, text))


class FootageTests(unittest.TestCase):
    def test_the_suffixes_match_the_app(self):
        model = (ROOT / "apps/desktop/src/features/discovery/model.ts").read_text(encoding="utf-8")
        block = model[model.index("export const SUFFIXES"):model.index("];", model.index("export const SUFFIXES"))]
        pairs = re.findall(r'zh: "([^"]+)", en: "([^"]+)"', block)
        self.assertEqual(pairs, [(suffix["zh"], suffix["en"]) for suffix in footage.SUFFIXES])

    def test_each_source_gets_its_language_and_results_mix(self):
        searcher = Recorder({
            "bilibili": DiscoveryPage((video("bilibili", 1), video("bilibili", 2)), next_page=2),
            "youtube": DiscoveryPage((video("youtube", 1, badges=("4K",)),), next_cursor="TokenToken1"),
            "skypixel": DiscoveryPage((video("skypixel", 1, width=3840, height=2160),), next_page=None)})
        result = footage.search("đập thủy điện 空镜", suffixes=["航拍 4K"], searcher=searcher, translators=TRANSLATORS)
        self.assertEqual(result["queries"], {"zh": "水电站大坝 空镜 航拍 4K", "en": "hydropower dam B-roll drone"})
        self.assertEqual(result["suffixes"], ["空镜", "航拍 4K"])
        self.assertEqual([item["page_url"] for item in result["items"]],
                         ["https://bilibili.test/1", "https://youtube.test/1", "https://skypixel.test/1", "https://bilibili.test/2"])
        self.assertEqual([item["quality"] for item in result["items"][:3]], [None, "4K", "4K"])
        self.assertEqual(result["sources"]["bilibili"]["next"], {"page": 2})
        self.assertEqual(result["sources"]["youtube"]["next"], {"cursor": "TokenToken1"})
        self.assertIsNone(result["sources"]["skypixel"]["next"])
        self.assertEqual(searcher.calls[1][2]["filter"], "4k")

    def test_youtube_retries_once_without_suffixes(self):
        def youtube(query, options):
            return DiscoveryPage(() if "B-roll" in query else (video("youtube", 7),))
        searcher = Recorder({"youtube": youtube})
        result = footage.search("白鹤滩水电站", suffixes=["空镜"], sources=["youtube"], searcher=searcher, translators=TRANSLATORS)
        self.assertEqual([call[1] for call in searcher.calls], ["Baihetan Hydropower Station B-roll", "Baihetan Hydropower Station"])
        self.assertTrue(result["sources"]["youtube"]["retried_without_suffixes"])
        self.assertEqual(result["sources"]["youtube"]["query"], "Baihetan Hydropower Station")
        self.assertEqual(len(result["items"]), 1)

    def test_a_failed_source_never_hides_the_others(self):
        searcher = Recorder({"bilibili": DiscoveryError("BLOCKED", "refused"),
                             "skypixel": DiscoveryPage((video("skypixel", 1),))})
        result = footage.search("白鹤滩", sources=["bilibili", "skypixel"], searcher=searcher, translators=TRANSLATORS)
        self.assertEqual(result["sources"]["bilibili"]["status"], "error")
        self.assertEqual(result["sources"]["bilibili"]["code"], "BLOCKED")
        self.assertIn("Khám phá", result["sources"]["bilibili"]["hint"])
        self.assertEqual(len(result["items"]), 1)

    def test_next_pages_and_no_translation(self):
        searcher = Recorder({"youtube": DiscoveryPage((video("youtube", 2),))})
        footage.search("Qingdao port", sources=["youtube"], more={"youtube": {"cursor": "TokenToken1"}}, translate=False,
                       searcher=searcher, translators=(lambda text: 1 / 0, lambda text: 1 / 0))
        self.assertEqual(searcher.calls[0][2]["cursor"], "TokenToken1")

    def test_bad_arguments(self):
        for kwargs in ({"sources": ["tiktok"]}, {"youtube_filter": "8k"}, {"suffixes": ["unknown"]}):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                footage.search("x", searcher=Recorder({}), translators=TRANSLATORS, **kwargs)
        with self.assertRaises(ValueError):
            footage.search("  ", searcher=Recorder({}), translators=TRANSLATORS)

    def test_helpers(self):
        self.assertEqual(footage.split_suffixes("dam 空镜  航拍 4K")[0], "dam")
        self.assertEqual(footage.youtube_query("Dam", [footage.SUFFIXES[1]], "any"), "Dam 4K drone")
        self.assertEqual(footage.youtube_query("Dam", [footage.SUFFIXES[1]], "4k"), "Dam drone")
        self.assertTrue(footage.needs_chinese("nhà máy"))
        self.assertFalse(footage.needs_chinese("白鹤滩"))
        self.assertTrue(footage.needs_english("白鹤滩"))
        self.assertFalse(footage.needs_english("Baihetan dam"))
        self.assertEqual(footage.quality_label({"width": 1920, "height": 1080}), "1080p")
        self.assertEqual(footage.quality_label({"title": "【4K】桥"}), "4K?")
        self.assertIs(footage.suffix_by_form("b-roll"), footage.SUFFIXES[0])


if __name__ == "__main__":
    unittest.main()
