from __future__ import annotations

import unittest

from engine.dubflow.download.bilibili import BilibiliSourceAdapter, BilibiliTransportError, normalize_source_ref
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


PAYLOAD = {
    "code": 0,
    "data": {
        "aid": 123456,
        "bvid": "BV1AbC2345",
        "title": "  Demo 字幕  ",
        "desc": "recorded fixture",
        "duration": "1.234567",
        "dash": {
            "video": [{"id": 80, "baseUrl": "https://cdn.example.test/video.m4s", "width": 1080, "height": 1920}],
            "audio": [{"id": 30280, "baseUrl": "https://cdn.example.test/audio.m4s"}],
        },
        "subtitle": {"list": [{"lan": "zh-Hans", "subtitle_url": "//subtitle.example.test/cue.json"}]},
    },
}


class RecordedTransport:
    def __init__(self, payload: object = PAYLOAD, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.refs: list[str] = []

    def fetch_video(self, source_ref: str) -> object:
        self.refs.append(source_ref)
        if self.error:
            raise self.error
        return self.payload


class BilibiliAdapterTests(unittest.TestCase):
    def test_normalizes_bvid_and_av_urls(self) -> None:
        self.assertEqual(normalize_source_ref("https://www.bilibili.com/video/BV1AbC2345/?utm_source=test"), "BV1AbC2345")
        self.assertEqual(normalize_source_ref("https://b23.tv/av123456"), "av123456")

    def test_recorded_payload_maps_identity_ticks_media_and_subtitles(self) -> None:
        transport = RecordedTransport()
        item = BilibiliSourceAdapter(transport).inspect("BV1AbC2345")
        self.assertEqual(transport.refs, ["BV1AbC2345"])
        self.assertEqual(item.identity.identity_key, "bilibili:BV1AbC2345")
        self.assertEqual(item.duration_ticks, 111111)
        self.assertEqual(item.subtitle_candidates[0].locator, "https://subtitle.example.test/cue.json")
        choice = BilibiliSourceAdapter(transport).select_download(item)
        self.assertEqual(choice.candidate.kind, "dash")
        self.assertEqual(choice.candidate.width, 1080)

    def test_api_errors_are_structured_and_non_secret(self) -> None:
        for code, expected in [(-101, SourceErrorCode.AUTH_REQUIRED), (-412, SourceErrorCode.RATE_LIMITED), (-404, SourceErrorCode.NOT_FOUND)]:
            with self.subTest(code=code):
                with self.assertRaises(SourceError) as context:
                    BilibiliSourceAdapter(RecordedTransport({"code": code, "data": {}})).inspect("BV1AbC2345")
                self.assertEqual(context.exception.code, expected)

    def test_transport_status_and_site_change_fallback(self) -> None:
        with self.assertRaises(SourceError) as auth:
            BilibiliSourceAdapter(RecordedTransport(error=BilibiliTransportError("response rejected", status=403))).inspect("BV1AbC2345")
        self.assertEqual(auth.exception.code, SourceErrorCode.AUTH_REQUIRED)
        with self.assertRaises(SourceError) as changed:
            BilibiliSourceAdapter(RecordedTransport({"code": 0, "data": {"bvid": "BV1AbC2345"}})).inspect("BV1AbC2345")
        self.assertEqual(changed.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_invalid_url_never_reaches_transport(self) -> None:
        transport = RecordedTransport()
        with self.assertRaises(SourceError) as context:
            BilibiliSourceAdapter(transport).inspect("https://example.test/video/BV1AbC2345")
        self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        self.assertEqual(transport.refs, [])


if __name__ == "__main__":
    unittest.main()
