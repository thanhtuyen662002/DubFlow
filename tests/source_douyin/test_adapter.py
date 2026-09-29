from __future__ import annotations

import unittest

from engine.dubflow.download.douyin import DouyinSourceAdapter, DouyinTransportError, normalize_source_ref
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


PAYLOAD = {
    "status_code": 0,
    "data": {
        "aweme_id": "7345678901234567890",
        "desc": "Recorded Douyin fixture",
        "duration": "1234.5",
        "video": {
            "width": 1080,
            "height": 1920,
            "play_addr": {"url_list": ["https://cdn.example.test/douyin-play.mp4"]},
            "download_addr": {"url_list": ["https://cdn.example.test/douyin-download.mp4"]},
        },
        "subtitle_infos": [{"language": "zh-Hans", "url": "https://cdn.example.test/subtitle.json", "format": "json"}],
    },
}


class RecordedTransport:
    def __init__(self, payload: object = PAYLOAD, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, object]] = []

    def fetch_video(self, source_ref: str, session: object = None) -> object:
        self.calls.append((source_ref, session))
        if self.error:
            raise self.error
        return self.payload


class Bridge:
    def __init__(self, headers: object) -> None:
        self.headers = headers

    def get_opaque_headers(self, provider_id: str) -> object:
        return self.headers


class DouyinAdapterTests(unittest.TestCase):
    def test_normalizes_long_and_short_urls(self) -> None:
        self.assertEqual(normalize_source_ref("https://www.douyin.com/video/7345678901234567890?from_tab=feed"), "7345678901234567890")
        self.assertEqual(normalize_source_ref("https://v.douyin.com/AbC_12/"), "short-AbC_12")

    def test_recorded_payload_maps_identity_ticks_media_and_subtitles(self) -> None:
        transport = RecordedTransport()
        item = DouyinSourceAdapter(transport).inspect("7345678901234567890")
        self.assertEqual(item.identity.identity_key, "douyin:7345678901234567890")
        self.assertEqual(item.duration_ticks, 111105)
        self.assertEqual(item.media_candidates[0].locator, "https://cdn.example.test/douyin-play.mp4")
        self.assertEqual(item.subtitle_candidates[0].language, "zh-Hans")

    def test_browser_session_is_opaque_and_never_in_error(self) -> None:
        transport = RecordedTransport(error=DouyinTransportError("challenge", status=403, challenge=True))
        secret = "session-cookie-value"
        with self.assertRaises(SourceError) as context:
            DouyinSourceAdapter(transport, Bridge({"Cookie": secret})).inspect("7345678901234567890")
        self.assertEqual(context.exception.code, SourceErrorCode.AUTH_REQUIRED)
        self.assertNotIn(secret, str(context.exception))
        self.assertEqual(transport.calls[0][1], {"Cookie": secret})

    def test_structured_rate_limit_and_site_change(self) -> None:
        with self.assertRaises(SourceError) as rate:
            DouyinSourceAdapter(RecordedTransport(error=DouyinTransportError("busy", status=429))).inspect("7345678901234567890")
        self.assertEqual(rate.exception.code, SourceErrorCode.RATE_LIMITED)
        with self.assertRaises(SourceError) as changed:
            DouyinSourceAdapter(RecordedTransport({"status_code": 0, "data": {"aweme_id": "7345678901234567890"}})).inspect("7345678901234567890")
        self.assertEqual(changed.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_invalid_host_does_not_call_transport(self) -> None:
        transport = RecordedTransport()
        with self.assertRaises(SourceError) as context:
            DouyinSourceAdapter(transport).inspect("https://example.test/video/7345678901234567890")
        self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
