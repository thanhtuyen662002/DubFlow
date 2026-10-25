"""Offline endpoint replay. Run with unittest discover (no tests-package import)."""

from dataclasses import FrozenInstanceError
from email.message import Message
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
import zlib

from engine.dubflow.discovery import DiscoveryError, HttpClient, TrendItem, VideoItem
from engine.dubflow.discovery.__main__ import main
from engine.dubflow.discovery.bilibili import API, Bilibili, sign_wbi
from engine.dubflow.discovery.douyin import Douyin
from engine.dubflow.discovery.http import HttpResponse, MIN_INTERVAL, REFERERS, TIMEOUT
from engine.dubflow.discovery.kuaishou import HOT_QUERY, Kuaishou
from engine.dubflow.discovery.models import duration_seconds, optional_int, plain_title


SAMPLES = Path(__file__).with_name("samples")
ROOT = Path(__file__).resolve().parents[2]


def sample(name):
    return json.loads((SAMPLES / (name + ".json")).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def response(payload, *, status=200, encoding="", headers=None):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if encoding == "gzip":
        raw = gzip.compress(raw)
    elif encoding == "deflate":
        raw = zlib.compress(raw)
    elif encoding == "raw-deflate":
        raw = zlib.compress(raw)[2:-4]
    hdr = Message()
    hdr["Content-Type"] = "application/json; charset=utf-8"
    if encoding:
        hdr["Content-Encoding"] = "deflate" if encoding == "raw-deflate" else encoding
    for key, value in (headers or {}).items():
        hdr[key] = value
    return HttpResponse(status, hdr, raw)


def replay(name, *, path=None, payload=None):
    saved = sample(name)
    parsed = urlsplit(saved["url"])
    url = parsed.scheme + "://" + parsed.netloc + (path or parsed.path)
    body = response(saved["response_json"] if payload is None else payload,
                    status=saved["http_status"], encoding=saved.get("content_encoding") or "")
    return saved["method"], url, body


def homepage(platform, **cookies):
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    for name, value in cookies.items():
        headers.add_header("Set-Cookie", f"{name}={value}; Path=/; Secure; HttpOnly")
    return "GET", REFERERS[platform], HttpResponse(200, headers, b"<!doctype html><html></html>")


class ReplayTransport:
    def __init__(self, *steps, clock):
        self.steps = list(steps)
        self.calls = []
        self.clock = clock

    def __call__(self, request, timeout):
        self.calls.append((request, timeout, self.clock()))
        if not self.steps:
            raise AssertionError("Unexpected request: " + request.full_url)
        method, endpoint, reply = self.steps.pop(0)
        actual = urlsplit(request.full_url)
        expected = urlsplit(endpoint)
        if (request.get_method(), actual.scheme, actual.netloc, actual.path) != (
                method, expected.scheme, expected.netloc, expected.path):
            raise AssertionError(f"Unexpected request: {request.get_method()} {request.full_url}")
        if isinstance(reply, Exception):
            raise reply
        return reply


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch("urllib.request.OpenerDirector.open",
                                   side_effect=AssertionError("Tests must never use the network"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def client(self, *steps):
        clock = FakeClock()
        transport = ReplayTransport(*steps, clock=clock)
        client = HttpClient(transport, clock=clock, sleep=clock.sleep)
        self.addCleanup(lambda: self.assertEqual(transport.steps, [], "Unconsumed replay response"))
        return client, transport, clock

    def assert_error(self, code, call):
        with self.assertRaises(DiscoveryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.message)
        return caught.exception

    def signed_steps(self, *steps):
        return (replay("bilibili_finger_spi"), replay("bilibili_nav"), *steps)


class ModelTests(OfflineTest):
    def test_frozen_models_and_json_nulls(self):
        trend = TrendItem("kuaishou", "word", 1, None, "https://example.test/")
        video = VideoItem("bilibili", "BVtest", "title", "author", "123", None,
                          None, None, "", "https://www.bilibili.com/video/BVtest")
        for item in (trend, video):
            with self.assertRaises(FrozenInstanceError):
                item.platform = "other"
            encoded = json.loads(json.dumps(item.to_dict()))
            self.assertEqual(encoded, item.to_dict())
        self.assertIsNone(trend.to_dict()["heat"])
        for key in ("duration_s", "plays", "likes"):
            self.assertIsNone(video.to_dict()[key])

    def test_nullable_counts(self):
        for value in (None, "", "--", "1万", "1,000", -1, "-1", False, 1.5, "1.5", "１２", "9" * 5000):
            with self.subTest(value=value):
                self.assertIsNone(optional_int(value))
        self.assertEqual(optional_int(0), 0)
        self.assertEqual(optional_int(" 123 "), 123)

    def test_duration_numeric_minutes_and_variable_width_seconds(self):
        for value, expected in (("43:9", 2589), ("24:02", 1442), ("48:13", 2893),
                                ("120:5", 7205), ("0:0", 0), (108, 108), (None, None),
                                ("bad", None), ("1:60", None), ("1:2:3", None), (True, None)):
            with self.subTest(value=value):
                self.assertEqual(duration_seconds(value), expected)

    def test_html_tags_and_entities(self):
        self.assertEqual(plain_title('<em class="keyword">仙侠</em> &amp; &#29483; &lt;3'),
                         "仙侠 & 猫 <3")
        self.assertEqual(plain_title("2 < 3 &quot;你好&quot;"), '2 < 3 "你好"')


class DouyinTests(OfflineTest):
    def test_hot_words_unsigned_no_bootstrap(self):
        client, transport, _ = self.client(replay("douyin_hot_fresh"))
        page = Douyin(client).trends()
        item = page.items[0]
        self.assertEqual((item.platform, item.keyword, item.rank, item.heat),
                         ("douyin", "台湾光复81周年", 1, 11979409))
        self.assertEqual(item.url, "https://www.douyin.com/search/" + quote(item.keyword, safe=""))
        self.assertIsNone(page.next_page)
        request = transport.calls[0][0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["source"], ["6"])
        self.assertFalse({"a_bogus", "X-Bogus", "w_rid"} & query.keys())
        self.assertIsNone(request.get_header("Cookie"))

    def test_both_autocomplete_samples(self):
        for name, query, expected in (("douyin_suggest", "美食", "美食附近抖音团购"),
                                      ("douyin_followup_suggest_fresh", "仙侠", "仙侠手游要充钱吗")):
            with self.subTest(name=name):
                client, transport, _ = self.client(replay(name))
                item = Douyin(client).suggest(query).items[0]
                self.assertEqual(item.keyword, expected)
                self.assertEqual(item.rank, 1)
                self.assertIsNone(item.heat)
                self.assertEqual(parse_qs(urlsplit(transport.calls[0][0].full_url).query)["keyword"], [query])


class BilibiliTests(OfflineTest):
    def test_hotword_heat_score_not_score(self):
        client, _, _ = self.client(replay("bilibili_hotword"))
        item = Bilibili(client).trends().items[0]
        self.assertEqual((item.rank, item.heat), (1, 6614542))
        self.assertEqual(item.keyword, sample("bilibili_hotword")["first_item"]["keyword"])
        payload = sample("bilibili_hotword")["response_json"]
        del payload["list"][0]["heat_score"]
        client, _, _ = self.client(replay("bilibili_hotword", payload=payload))
        self.assertIsNone(Bilibili(client).trends().items[0].heat)

    def test_suggestions_use_plain_value_or_term(self):
        for drop_value in (False, True):
            payload = sample("bilibili_suggest")["response_json"]
            if drop_value:
                del payload["result"]["tag"][0]["value"]
            client, transport, _ = self.client(replay("bilibili_suggest", payload=payload))
            item = Bilibili(client).suggest("仙侠").items[0]
            self.assertEqual(item.keyword, "仙侠游戏")
            self.assertIsNone(item.heat)
            params = parse_qs(urlsplit(transport.calls[0][0].full_url).query, keep_blank_values=True)
            self.assertEqual(params["highlight"], [""])
            self.assertEqual(params["term"], ["仙侠"])

    def test_popular_all_fields_and_pagination(self):
        client, transport, _ = self.client(replay("bilibili_popular"))
        page = Bilibili(client).popular(page=3, ps=1)
        self.assertEqual(page.next_page, 4)
        self.assertEqual(page.items[0].to_dict(), {
            "platform": "bilibili", "video_id": "BV1w2Hw6KE1j", "title": "写了全国所有省份的love me！",
            "author": "西柚甜瓜", "author_id": "33015300", "duration_s": 108,
            "plays": 2223727, "likes": 241907,
            "cover_url": "http://i2.hdslb.com/bfs/archive/f1d952d55688049f5e74a18819b92696d7433bd4.jpg",
            "page_url": "https://www.bilibili.com/video/BV1w2Hw6KE1j",
        })
        params = parse_qs(urlsplit(transport.calls[0][0].full_url).query)
        self.assertEqual((params["pn"], params["ps"]), (["3"], ["1"]))

    def test_popular_final_page(self):
        payload = sample("bilibili_popular")["response_json"]
        payload["data"]["no_more"] = True
        client, _, _ = self.client(replay("bilibili_popular", payload=payload))
        self.assertIsNone(Bilibili(client).popular().next_page)

    def test_ranking_is_one_snapshot_not_nested_others(self):
        client, transport, _ = self.client(replay("bilibili_ranking"))
        page = Bilibili(client).ranking()
        self.assertEqual(len(page.items), 1)
        self.assertIsNone(page.next_page)
        item = page.items[0]
        self.assertEqual((item.video_id, item.author_id, item.duration_s, item.plays, item.likes),
                         ("BV1U3n46GEzY", "1636034895", 83, 4028287, 603282))
        self.assertEqual(parse_qs(urlsplit(transport.calls[0][0].full_url).query), {"rid": ["0"], "type": ["all"]})

    def test_wbi_search_bootstrap_nav_minus101_and_exact_fields(self):
        # The trimmed successful legacy search has the same documented result
        # schema as WBI search; replay its body on the required WBI route.
        client, transport, _ = self.client(*self.signed_steps(
            replay("bilibili_search_plain", path="/x/web-interface/wbi/search/type")))
        page = Bilibili(client, clock=lambda: 1702204169).search("仙侠", page=1, ps=1)
        item = page.items[0]
        self.assertEqual((item.video_id, item.title, item.author, item.author_id),
                         ("BV1cpni6dExM", "【完结文】古言/仙侠：震撼美味，快来看看吧！~", "听书小能手V", "3546826967877972"))
        self.assertEqual((item.duration_s, item.plays, item.likes), (2589, 10453, 410))
        self.assertTrue(item.cover_url.startswith("https://i1.hdslb.com/"))
        self.assertEqual(item.page_url, "https://www.bilibili.com/video/BV1cpni6dExM")
        self.assertEqual(page.next_page, 2)
        request = transport.calls[-1][0]
        params = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(params["wts"], ["1702204169"])
        self.assertEqual(params["page_size"], ["1"])
        self.assertEqual(params["page"], ["1"])
        self.assertEqual(len(params["w_rid"][0]), 32)
        cookies = request.get_header("Cookie")
        spi = sample("bilibili_finger_spi")["response_json"]["data"]
        self.assertIn("buvid3=" + spi["b_3"], cookies)
        self.assertIn("buvid4=" + spi["b_4"], cookies)
        self.assertIsNone(transport.calls[0][0].get_header("Cookie"))
        self.assertIn("buvid3=", transport.calls[1][0].get_header("Cookie"))

    def test_search_last_page_and_unknown_counts(self):
        payload = sample("bilibili_search_plain")["response_json"]
        payload["data"]["numPages"] = 1
        row = payload["data"]["result"][0]
        row.pop("play")
        row.pop("like")
        row["duration"] = None
        client, _, _ = self.client(*self.signed_steps(replay(
            "bilibili_search_plain", path="/x/web-interface/wbi/search/type", payload=payload)))
        page = Bilibili(client).search("仙侠", ps=1)
        self.assertIsNone(page.next_page)
        self.assertIsNone(page.items[0].plays)
        self.assertIsNone(page.items[0].likes)
        self.assertIsNone(page.items[0].duration_s)

    def test_search_skips_non_video_cards_but_not_an_all_unusable_page(self):
        payload = sample("bilibili_search_plain")["response_json"]
        video = payload["data"]["result"][0]
        payload["data"]["result"] = [{"type": "media_bangumi", "title": "番剧"}, video]
        client, _, _ = self.client(*self.signed_steps(replay(
            "bilibili_search_plain", path="/x/web-interface/wbi/search/type", payload=payload)))
        page = Bilibili(client).search("仙侠", ps=2)
        self.assertEqual([item.video_id for item in page.items], [video["bvid"]])
        payload = sample("bilibili_search_plain")["response_json"]
        payload["data"]["result"] = [{"type": "media_bangumi", "title": "番剧"}]
        client, _, _ = self.client(*self.signed_steps(replay(
            "bilibili_search_plain", path="/x/web-interface/wbi/search/type", payload=payload)))
        with self.assertRaises(DiscoveryError) as caught:
            Bilibili(client).search("仙侠", ps=1)
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_creator_pages_cache_keys_and_never_use_collection_likes(self):
        client, transport, _ = self.client(*self.signed_steps(
            replay("bilibili_space_wbi_p1"), replay("bilibili_followup_space_wbi_p2")))
        adapter = Bilibili(client)
        page1 = adapter.creator("546195", page=1, ps=1)
        page2 = adapter.creator("546195", page=2, ps=1)
        self.assertEqual(page1.items[0].video_id, "BV1gKJ86JEuP")
        self.assertEqual(page2.items[0].video_id, "BV1iqpt6cEMU")
        self.assertEqual((page1.items[0].duration_s, page2.items[0].duration_s), (1442, 2893))
        self.assertEqual((page1.items[0].plays, page2.items[0].plays), (4483924, 3330320))
        self.assertIsNone(page1.items[0].likes)
        self.assertIsNone(page2.items[0].likes)
        self.assertEqual((page1.next_page, page2.next_page), (2, 3))
        for page_number, call in enumerate(transport.calls[2:], 1):
            params = parse_qs(urlsplit(call[0].full_url).query)
            self.assertEqual(params["pn"], [str(page_number)])
            self.assertEqual(params["mid"], ["546195"])
            self.assertEqual(params["ps"], ["1"])
            self.assertEqual(params["order"], ["pubdate"])

    def test_creator_final_page(self):
        payload = sample("bilibili_space_wbi_p1")["response_json"]
        payload["data"]["page"]["count"] = 1
        client, _, _ = self.client(*self.signed_steps(replay("bilibili_space_wbi_p1", payload=payload)))
        self.assertIsNone(Bilibili(client).creator("546195", ps=1).next_page)

    def test_missing_board_counts_are_null_and_known_zero_stays_zero(self):
        payload = sample("bilibili_popular")["response_json"]
        payload["data"]["list"][0]["stat"] = {"view": 0}
        client, _, _ = self.client(replay("bilibili_popular", payload=payload))
        item = Bilibili(client).popular().items[0]
        self.assertEqual(item.plays, 0)
        self.assertIsNone(item.likes)

    def test_key_refresh_after_one_hour_without_repeating_spi(self):
        client, transport, _ = self.client(*self.signed_steps(
            replay("bilibili_space_wbi_p1")), replay("bilibili_nav"), replay("bilibili_space_wbi_p1"))
        signing_clock = FakeClock()
        adapter = Bilibili(client, clock=signing_clock)
        adapter.creator("546195", ps=1)
        signing_clock.now += 3600
        adapter.creator("546195", ps=1)
        self.assertEqual(len(transport.calls), 5)

    def test_input_validation_happens_before_network(self):
        client, _, _ = self.client()
        adapter = Bilibili(client)
        for call in (lambda: adapter.popular(0), lambda: adapter.search("x", ps=0),
                     lambda: adapter.creator("not-a-mid"), lambda: adapter.creator("0")):
            with self.assertRaises(ValueError):
                call()


class WbiTests(OfflineTest):
    def test_fixed_keys_time_with_independent_reference_algorithm(self):
        keys = {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}
        params = {"keyword": "仙侠 !'()* &/=", "page": 2, "search_type": "video", "page_size": 20}
        timestamp = 1702204169
        # Independent literal of the table in the research report's script;
        # do not import the implementation's table or its encoding helper.
        permutation = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,
                       27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
                       37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
                       22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
        raw_key = "7cd084941338484aae1ad9425b84077c4932caff0ff746eab6f01bf08b70ac45"
        secret = "".join(map(raw_key.__getitem__, permutation))[:32]
        reference = {**params, "wts": timestamp}
        translation = str.maketrans("", "", "!'()*")
        expected_query = "&".join(quote_plus(key) + "=" + quote_plus(str(reference[key]).translate(translation))
                                  for key in sorted(reference))
        expected_rid = hashlib.md5((expected_query + secret).encode("utf-8")).hexdigest()
        signed = sign_wbi(params, keys, timestamp)
        self.assertEqual(signed["w_rid"], expected_rid)
        self.assertEqual(signed["wts"], str(timestamp))
        self.assertEqual(signed["keyword"], "仙侠  &/=")
        self.assertNotIn("wts", params)
        self.assertEqual(sign_wbi({**params, "w_rid": "stale"}, keys, timestamp), signed)

    def test_bad_keys_report_typed_failure(self):
        for keys in ({}, {"img_url": "x", "sub_url": "y"},
                     {"img_url": None, "sub_url": []},
                     {"img_url": "http://[", "sub_url": "y"}):
            self.assert_error("BAD_RESPONSE", lambda: sign_wbi({}, keys, 1))


class KuaishouTests(OfflineTest):
    def test_recorded_homepage_destination_redirect_is_paced_and_keeps_cookies(self):
        saved = sample("kuaishou_homepage")
        redirect_headers = Message()
        redirect_headers["Location"] = saved["final_url"]
        redirect_headers["Set-Cookie"] = "did=anonymous-test; Path=/; Secure"
        final_headers = Message()
        final_headers["Content-Encoding"] = "gzip"
        for name in saved["set_cookie_names"]:
            final_headers.add_header("Set-Cookie", f"{name}=anonymous-test; Path=/; Secure")
        final_body = gzip.compress(saved["response_text_prefix"].encode("utf-8"))
        client, transport, _ = self.client(
            ("GET", saved["url"], HttpResponse(302, redirect_headers, b"")),
            ("GET", saved["final_url"], HttpResponse(saved["http_status"], final_headers, final_body)),
            replay("kuaishou_hot_rank_candidate"))
        self.assertTrue(Kuaishou(client).trends().items)
        self.assertIn("did=anonymous-test", transport.calls[1][0].get_header("Cookie"))
        for name in saved["set_cookie_names"]:
            self.assertIn(name + "=anonymous-test", transport.calls[2][0].get_header("Cookie"))
        for first, second in zip(transport.calls, transport.calls[1:]):
            self.assertAlmostEqual(second[2] - first[2], 1.1)

    def test_graphql_exact_operation_cookie_bootstrap_and_null_heat(self):
        client, transport, _ = self.client(homepage("kuaishou", did="anonymous-test", clientid="test"),
                                            replay("kuaishou_hot_rank_candidate"),
                                            replay("kuaishou_hot_rank_candidate"))
        adapter = Kuaishou(client)
        page = adapter.trends()
        adapter.trends()
        self.assertEqual(len(transport.calls), 3)
        item = page.items[0]
        self.assertEqual((item.platform, item.keyword, item.rank, item.heat),
                         ("kuaishou", "总书记深情讲述这条伟大胜利之路", 1, None))
        self.assertEqual(item.url, "https://www.kuaishou.com/search/video?searchKey=" + quote(item.keyword, safe=""))
        self.assertIsNone(page.next_page)
        request = transport.calls[1][0]
        self.assertEqual(json.loads(request.data), {"operationName": "visionHotRank", "variables": {}, "query": HOT_QUERY})
        self.assertEqual(json.loads(request.data), sample("kuaishou_hot_rank_candidate")["request_body"])
        self.assertIn("did=anonymous-test", request.get_header("Cookie"))
        self.assertEqual(request.get_header("Origin"), "https://www.kuaishou.com")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_non_success_empty_graphql_is_not_success(self):
        payload = {"data": {"visionHotRank": {"result": 2, "items": []}}}
        client, _, _ = self.client(homepage("kuaishou"), replay("kuaishou_hot_rank_candidate", payload=payload))
        self.assert_error("BAD_RESPONSE", Kuaishou(client).trends)


class ErrorTests(OfflineTest):
    def test_recorded_login_voucher_and_captcha(self):
        for name, platform, code in (("douyin_search_video", "douyin", "LOGIN_REQUIRED"),
                                      ("bilibili_search_wbi_p1", "bilibili", "BLOCKED"),
                                      ("kuaishou_schema_profile_corrected", "kuaishou", "BLOCKED")):
            with self.subTest(name=name):
                client, _, _ = self.client(replay(name))
                saved = sample(name)
                kwargs = {"json_body": saved["request_body"]} if saved["method"] == "POST" else {}
                self.assert_error(code, lambda: client.json(platform, saved["url"], **kwargs))

    def test_voucher_propagates_from_adapter_without_retry(self):
        client, transport, _ = self.client(*self.signed_steps(replay("bilibili_search_wbi_p1")))
        self.assert_error("BLOCKED", lambda: Bilibili(client).search("仙侠"))
        self.assertEqual(len(transport.calls), 3)

    def test_http_blocks_and_network_http_errors(self):
        for status, code in ((403, "BLOCKED"), (412, "BLOCKED"), (429, "BLOCKED"),
                              (503, "NETWORK"), (400, "BAD_RESPONSE")):
            with self.subTest(status=status):
                client, _, _ = self.client(("GET", API, response({}, status=status)))
                self.assert_error(code, lambda: client.json("bilibili", API))

    def test_application_block_shapes(self):
        cases = [("bilibili", {"code": -352}), ("bilibili", {"code": -412}),
                 ("bilibili", {"code": 0, "data": {"v_voucher": "verify"}}),
                 ("kuaishou", {"data": {"result": 400002}}),
                 ("kuaishou", {"data": {"visionSearchPhoto": {"result": 50, "feeds": []}}}),
                 ("douyin", {"captcha": {"url": "https://example.test"}}),
                 ("kuaishou", {"errors": [{"message": "captcha required"}]}),
                 ("bilibili", {"code": 0, "data": {"url": "https://captcha.example.test/"}})]
        for platform, payload in cases:
            with self.subTest(platform=platform, payload=payload):
                client, _, _ = self.client(("GET", REFERERS[platform], response(payload)))
                self.assert_error("BLOCKED", lambda: client.json(platform, REFERERS[platform]))

    def test_transport_errors_do_not_leak_details(self):
        for error in (URLError("private-cookie-value"), TimeoutError("private-cookie-value"), OSError("secret")):
            client, _, _ = self.client(("GET", API, error))
            caught = self.assert_error("NETWORK", lambda: client.json("bilibili", API))
            self.assertNotIn("private", caught.message)
            self.assertNotIn("secret", caught.message)

    def test_malformed_or_empty_json_is_not_empty_success(self):
        for body in (b"", b"{}invalid", b"null", b"[]", b"\xff", b"<html>ordinary error</html>"):
            client, _, _ = self.client(("GET", API, HttpResponse(200, {}, body)))
            self.assert_error("BAD_RESPONSE", lambda: client.json("bilibili", API))

    def test_html_challenge_is_blocked(self):
        for body in (b"<html>captcha</html>", b"<script>__ac_signature</script>", b"ArgusSecurityPlugin"):
            client, _, _ = self.client(("GET", API, HttpResponse(200, {}, body)))
            self.assert_error("BLOCKED", lambda: client.json("bilibili", API))

    def test_missing_status_missing_list_and_unknown_codes(self):
        for payload in ({"data": {"word_list": []}}, {"status_code": 0},
                        {"status_code": 0, "data": {"word_list": None}},
                        {"status_code": 0, "data": {"word_list": [None]}},
                        {"status_code": 123, "data": {"word_list": []}},
                        {"status_code": False, "data": {"word_list": []}}):
            client, _, _ = self.client(replay("douyin_hot_fresh", payload=payload))
            self.assert_error("BAD_RESPONSE", Douyin(client).trends)

    def test_explicit_successful_empty_list(self):
        client, _, _ = self.client(replay("douyin_hot_fresh", payload={"status_code": 0, "data": {"word_list": []}}))
        self.assertEqual(Douyin(client).trends().items, ())

    def test_unsupported_actions_make_zero_requests(self):
        client, transport, _ = self.client()
        for adapter in (Douyin(client), Kuaishou(client)):
            for call in (lambda: adapter.search("x"), lambda: adapter.creator("id"), lambda: adapter.popular()):
                self.assert_error("UNSUPPORTED", call)
        self.assert_error("UNSUPPORTED", lambda: Kuaishou(client).suggest("x"))
        self.assertEqual(transport.calls, [])


class HttpTests(OfflineTest):
    def test_unverified_redirects_are_not_followed(self):
        for destination in ("https://example.test/new-reco", "https://www.kuaishou.com/unknown"):
            client, transport, _ = self.client(("GET", REFERERS["kuaishou"], HttpResponse(
                302, {"Location": destination}, b"")))
            self.assert_error("BAD_RESPONSE", lambda: client.request(
                "kuaishou", REFERERS["kuaishou"], accept="text/html"))
            self.assertEqual(len(transport.calls), 1)

    def test_headers_timeout_and_all_compression_forms(self):
        for encoding in ("", "gzip", "deflate", "raw-deflate"):
            for platform, referer in REFERERS.items():
                client, transport, _ = self.client(("GET", referer, response({"word": "你好"}, encoding=encoding)))
                self.assertEqual(client.json(platform, referer), {"word": "你好"})
                request, timeout, _ = transport.calls[0]
                self.assertEqual(timeout, 15)
                self.assertEqual(timeout, TIMEOUT)
                self.assertIn("Windows NT", request.get_header("User-agent"))
                self.assertIn("Chrome/", request.get_header("User-agent"))
                self.assertEqual(request.get_header("Referer"), referer)
                self.assertTrue(request.get_header("Accept-language").startswith("zh-CN"))
                self.assertEqual(request.get_header("Accept-encoding"), "gzip, deflate")

    def test_corrupted_compression_and_unknown_encoding(self):
        for encoding in ("gzip", "deflate", "br"):
            client, _, _ = self.client(("GET", API, HttpResponse(200, {"Content-Encoding": encoding}, b"bad")))
            self.assert_error("BAD_RESPONSE", lambda: client.json("bilibili", API))

    def test_spacing_is_per_platform_and_includes_failures(self):
        client, transport, clock = self.client(
            ("GET", REFERERS["douyin"], response({})),
            ("GET", REFERERS["bilibili"], response({})),
            ("GET", REFERERS["douyin"], TimeoutError()),
            ("GET", REFERERS["douyin"], response({})),
            ("GET", REFERERS["douyin"], response({})))
        client.json("douyin", REFERERS["douyin"])
        client.json("bilibili", REFERERS["bilibili"])
        self.assert_error("NETWORK", lambda: client.json("douyin", REFERERS["douyin"]))
        client.json("douyin", REFERERS["douyin"])
        self.assertEqual(transport.calls[0][2], transport.calls[1][2])
        self.assertAlmostEqual(transport.calls[2][2] - transport.calls[0][2], MIN_INTERVAL)
        self.assertAlmostEqual(transport.calls[3][2] - transport.calls[2][2], MIN_INTERVAL)
        clock.now += 5
        client.json("douyin", REFERERS["douyin"])
        self.assertEqual(len(clock.sleeps), 2)

    def test_bootstrap_requests_are_rate_limited_too(self):
        client, transport, _ = self.client(*self.signed_steps(replay("bilibili_space_wbi_p1")))
        Bilibili(client).creator("546195", ps=1)
        times = [call[2] for call in transport.calls]
        for start, end in zip(times, times[1:]):
            self.assertAlmostEqual(end - start, 1.1)

    def test_in_memory_cookie_domain_and_platform_isolation(self):
        client, transport, _ = self.client(homepage("kuaishou", did="test-only"),
                                            ("GET", REFERERS["bilibili"], response({})),
                                            ("GET", REFERERS["kuaishou"], response({})))
        client.request("kuaishou", REFERERS["kuaishou"], accept="text/html")
        client.json("bilibili", REFERERS["bilibili"])
        client.json("kuaishou", REFERERS["kuaishou"])
        self.assertIsNone(transport.calls[1][0].get_header("Cookie"))
        self.assertEqual(transport.calls[2][0].get_header("Cookie"), "did=test-only")
        self.assertEqual(len(list(client.cookie_jars["bilibili"])), 0)
        fresh = HttpClient(clock=FakeClock(), sleep=lambda _: None)
        self.assertEqual(len(list(fresh.cookie_jars["kuaishou"])), 0)

    def test_default_urllib_transport_handles_httperror_as_block(self):
        clock = FakeClock()
        client = HttpClient(clock=clock, sleep=clock.sleep)
        error = HTTPError(API, 412, "refused", Message(), io.BytesIO(b'{"code":-412}'))
        with patch("engine.dubflow.discovery.http.build_opener") as factory:
            opened = factory.return_value.open
            opened.side_effect = error
            self.assert_error("BLOCKED", lambda: client.json("bilibili", API))
        self.assertEqual(opened.call_args.kwargs["timeout"], 15)


class CliTests(OfflineTest):
    def invoke(self, argv, client):
        stdout = io.StringIO()
        self.assertEqual(main(argv, client=client, stdout=stdout), 0)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        return json.loads(stdout.getvalue())

    def test_success_shape_and_video_nulls(self):
        client, _, _ = self.client(*self.signed_steps(replay("bilibili_space_wbi_p1")))
        result = self.invoke(["bilibili", "creator", "--mid", "546195", "--page", "1"], client)
        self.assertEqual(set(result), {"ok", "items", "next_page"})
        self.assertIs(result["ok"], True)
        self.assertIsNone(result["items"][0]["likes"])
        self.assertEqual(result["next_page"], 2)

    def test_trends_suggest_popular_search_and_ranking_dispatch(self):
        cases = [(["douyin", "trends"], (replay("douyin_hot_fresh"),)),
                 (["douyin", "suggest", "--query", "美食"], (replay("douyin_suggest"),)),
                 (["bilibili", "popular", "--page", "2"], (replay("bilibili_popular"),)),
                 (["bilibili", "ranking"], (replay("bilibili_ranking"),)),
                 (["bilibili", "search", "--query", "仙侠"], self.signed_steps(
                     replay("bilibili_search_plain", path="/x/web-interface/wbi/search/type"))),
                 (["kuaishou", "trends"], (homepage("kuaishou"), replay("kuaishou_hot_rank_candidate")))]
        for argv, steps in cases:
            with self.subTest(argv=argv):
                client, _, _ = self.client(*steps)
                result = self.invoke(argv, client)
                self.assertIs(result["ok"], True)
                self.assertTrue(result["items"])

    def test_typed_failure_shape_and_zero_exit(self):
        client, _, _ = self.client()
        result = self.invoke(["douyin", "search", "--query", "test"], client)
        self.assertEqual(set(result), {"ok", "code", "message"})
        self.assertIs(result["ok"], False)
        self.assertEqual(result["code"], "UNSUPPORTED")
        client, _, _ = self.client(replay("douyin_hot_fresh", payload=sample("douyin_search_video")["response_json"]))
        result = self.invoke(["douyin", "trends"], client)
        self.assertEqual(result["code"], "LOGIN_REQUIRED")

    def test_usage_errors_only_are_nonzero_without_json(self):
        for argv in (["unknown", "trends"], ["douyin", "search"],
                     ["bilibili", "creator"], ["bilibili", "creator", "--mid", "abc"],
                     ["bilibili", "popular", "--page", "0"], ["douyin", "suggest", "--query", " "]):
            with self.subTest(argv=argv):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as caught:
                    main(argv, stdout=stdout)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("error:", stderr.getvalue())

    def test_actual_module_entrypoint_offline_unsupported(self):
        process = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.discovery", "kuaishou", "search",
                                  "--query", "test"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stderr, "")
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertEqual(json.loads(process.stdout)["code"], "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
