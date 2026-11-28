"""YouTube and SkyPixel footage search (#978), replayed offline from trimmed recorded responses."""

import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from engine.dubflow.discovery import DiscoveryError, HttpClient
from engine.dubflow.discovery.__main__ import main
from engine.dubflow.discovery.http import HttpResponse
from engine.dubflow.discovery.models import VideoItem
from engine.dubflow.discovery.skypixel import SkyPixel
from engine.dubflow.discovery.youtube import FILTERS, YouTube


SAMPLES = Path(__file__).with_name("samples")


def sample(name):
    return json.loads((SAMPLES / (name + ".json")).read_text(encoding="utf-8"))


def reply(payload, status=200):
    return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class Replay:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("Unexpected request: " + request.full_url)
        return self.replies.pop(0)


class SourceTest(unittest.TestCase):
    def setUp(self):
        guard = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Tests must never use the network"))
        guard.start()
        self.addCleanup(guard.stop)

    def client(self, *replies):
        transport = Replay(*replies)
        self.addCleanup(lambda: self.assertEqual(transport.replies, [], "Unconsumed replay response"))
        return HttpClient(transport, clock=lambda: 0.0, sleep=lambda seconds: None), transport

    def assert_error(self, code, call):
        with self.assertRaises(DiscoveryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)


class YouTubeTests(SourceTest):
    def test_search_reads_videos_badges_and_the_next_token(self):
        client, transport = self.client(reply(sample("youtube_search_4k")["response_json"]))
        page = YouTube(client).search("Baihetan Dam 4K drone", quality="4k")
        self.assertEqual(len(page.items), 4)
        first = page.items[0]
        self.assertEqual(first.platform, "youtube")
        self.assertRegex(first.video_id, r"^[0-9A-Za-z_-]{11}$")
        self.assertEqual(first.page_url, "https://www.youtube.com/watch?v=" + first.video_id)
        self.assertTrue(first.cover_url.startswith("https://i.ytimg.com/"))
        self.assertIn("4K", first.badges)
        self.assertIsInstance(first.duration_s, int)
        self.assertIsNone(page.next_page)
        self.assertTrue(page.next_cursor)
        request = transport.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urlsplit(request.full_url).path, "/youtubei/v1/search")
        body = json.loads(request.data)
        self.assertEqual((body["query"], body["params"]), ("Baihetan Dam 4K drone", FILTERS["4k"]))
        self.assertEqual(body["context"]["client"]["clientName"], "WEB")
        self.assertIsNone(request.get_header("Cookie"))

    def test_the_next_page_sends_only_the_token(self):
        first, _ = self.client(reply(sample("youtube_search_4k")["response_json"]))
        token = YouTube(first).search("x").next_cursor
        client, transport = self.client(reply(sample("youtube_search_next")["response_json"]))
        page = YouTube(client).search("x", cursor=token)
        self.assertEqual(len(page.items), 2)
        body = json.loads(transport.requests[0].data)
        self.assertEqual(body["continuation"], token)
        self.assertNotIn("query", body)

    def test_counts_durations_and_odd_rows(self):
        payload = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
            {"videoRenderer": {"videoId": "abcdefghijk", "title": {"runs": [{"text": "A "}, {"text": "B"}]},
                               "lengthText": {"simpleText": "1:02:03"}, "viewCountText": {"simpleText": "No views"},
                               "ownerText": {"runs": [{"text": "Chan"}]}}},
            {"videoRenderer": {"videoId": "bad id", "title": {"simpleText": "skipped"}}},
            {"videoRenderer": {"videoId": "lmnopqrstuv", "title": {"simpleText": "Short count"},
                               "viewCountText": {"simpleText": "1.2M views"}, "lengthText": {"simpleText": "LIVE"},
                               "thumbnail": {"thumbnails": [{"url": "https://tracker.example/x.jpg"}]}}},
            {"shelfRenderer": {"title": {"simpleText": "People also watched"}}},
        ]}}]}}}
        client, _ = self.client(reply(payload))
        page = YouTube(client).search("x")
        self.assertEqual([item.video_id for item in page.items], ["abcdefghijk", "lmnopqrstuv"])
        self.assertEqual((page.items[0].title, page.items[0].duration_s, page.items[0].plays, page.items[0].author),
                         ("A B", 3723, 0, "Chan"))
        self.assertEqual((page.items[1].plays, page.items[1].duration_s, page.items[1].cover_url), (None, None, ""))
        self.assertIsNone(page.next_cursor)

    def test_no_results_is_an_empty_page_but_a_missing_list_is_an_error(self):
        empty = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
            {"backgroundPromoRenderer": {"title": {"simpleText": "No results found"}}}]}}]}}}
        client, _ = self.client(reply(empty))
        self.assertEqual(YouTube(client).search("x").items, ())
        client, _ = self.client(reply({"responseContext": {}}))
        self.assert_error("BAD_RESPONSE", lambda: YouTube(client).search("x"))
        client, _ = self.client(reply({"error": {"code": 400}}, status=400))
        self.assert_error("BAD_RESPONSE", lambda: YouTube(client).search("x"))
        client, _ = self.client(reply({}, status=429))
        self.assert_error("BLOCKED", lambda: YouTube(client).search("x"))

    def test_bad_arguments_send_nothing(self):
        client, transport = self.client()
        for call in (lambda: YouTube(client).search("x", quality="8k"), lambda: YouTube(client).search("x", cursor="a b")):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(transport.requests, [])
        self.assert_error("UNSUPPORTED", YouTube(client).trends)


class SkyPixelTests(SourceTest):
    def test_search_reads_resolution_duration_and_pages_by_offset(self):
        client, transport = self.client(reply(sample("skypixel_search_videos")["response_json"]))
        page = SkyPixel(client).search("港珠澳大桥", page=2)
        first = page.items[0]
        self.assertEqual(first.platform, "skypixel")
        self.assertEqual((first.width, first.height), (3840, 2160))
        self.assertEqual(first.duration_s, 23)
        self.assertEqual(first.page_url, "https://www.skypixel.com/videos/" + first.video_id)
        self.assertTrue(first.cover_url.startswith("https://skp-"))
        # Three rows of a 20-row page: the last page.
        self.assertIsNone(page.next_page)
        query = parse_qs(urlsplit(transport.requests[0].full_url).query)
        self.assertEqual((query["keyword"], query["limit"], query["offset"]), (["港珠澳大桥"], ["20"], ["20"]))
        self.assertEqual(first.to_dict()["width"], 3840)

    def test_a_full_page_has_a_next_page_and_photos_are_skipped(self):
        payload = copy.deepcopy(sample("skypixel_search_videos")["response_json"])
        row = payload["data"]["items"][0]
        payload["data"]["items"] = [dict(row, slug=f"v-{index}") for index in range(19)] + [dict(row, type="photo", slug="p")]
        client, _ = self.client(reply(payload))
        page = SkyPixel(client).search("x")
        self.assertEqual(len(page.items), 19)
        self.assertEqual(page.next_page, 2)

    def test_media_picks_the_1080p_mp4_on_dji_hosts(self):
        saved = sample("skypixel_video_detail")["response_json"]
        slug = saved["data"]["item"]["slug"]
        client, transport = self.client(reply(saved))
        media = SkyPixel(client).media(slug)
        self.assertEqual(media["quality"], "1080p")
        self.assertTrue(media["url"].endswith("/1080.mp4"))
        self.assertEqual(urlsplit(media["url"]).hostname.split(".", 1)[1], "dji.net")
        self.assertEqual(media["license"], "SkyPixel Use")
        self.assertEqual(urlsplit(transport.requests[0].full_url).path, "/api/v2/videos/" + slug)

    def test_media_refuses_foreign_hosts_another_video_and_bad_ids(self):
        saved = copy.deepcopy(sample("skypixel_video_detail")["response_json"])
        slug = saved["data"]["item"]["slug"]
        saved["data"]["item"]["cdn_url"] = {"large": "https://evil.example/1080.mp4", "medium": "http://cn-videos.dji.net/720.mp4"}
        client, _ = self.client(reply(saved))
        self.assert_error("UNSUPPORTED", lambda: SkyPixel(client).media(slug))
        other = copy.deepcopy(sample("skypixel_video_detail")["response_json"])
        other["data"]["item"]["slug"] = "someone-else"
        client, _ = self.client(reply(other))
        self.assert_error("BAD_RESPONSE", lambda: SkyPixel(client).media(slug))
        client, transport = self.client()
        for bad in ("../x", "a/b", "", "x" * 200):
            with self.assertRaises(ValueError):
                SkyPixel(client).media(bad)
        self.assertEqual(transport.requests, [])


class CliTests(SourceTest):
    def invoke(self, argv, client):
        stdout = io.StringIO()
        self.assertEqual(main(argv, client=client, stdout=stdout), 0)
        return json.loads(stdout.getvalue())

    def test_youtube_search_gives_a_cursor_and_skypixel_media_its_url(self):
        client, _ = self.client(reply(sample("youtube_search_4k")["response_json"]))
        result = self.invoke(["youtube", "search", "--query", "dam", "--filter", "4k"], client)
        self.assertEqual(set(result), {"ok", "items", "next_page", "next_cursor"})
        self.assertIn("badges", result["items"][0])
        saved = sample("skypixel_video_detail")["response_json"]
        client, _ = self.client(reply(saved))
        result = self.invoke(["skypixel", "media", "--id", saved["data"]["item"]["slug"]], client)
        self.assertEqual(set(result), {"ok", "url", "quality", "license", "width", "height"})

    def test_usage_errors(self):
        for argv in (["skypixel", "media"], ["bilibili", "media", "--id", "x"], ["bilibili", "search", "--query", "x", "--cursor", "abc"],
                     ["youtube", "search", "--query", "x", "--filter", "8k"]):
            with self.subTest(argv=argv), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(argv, stdout=io.StringIO())
            self.assertEqual(caught.exception.code, 2)

    def test_older_sources_keep_their_exact_shape(self):
        item = VideoItem("bilibili", "BV1", "t", "a", "1", None, None, None, "", "https://www.bilibili.com/video/BV1")
        self.assertNotIn("width", item.to_dict())
        self.assertNotIn("badges", item.to_dict())


if __name__ == "__main__":
    unittest.main()
