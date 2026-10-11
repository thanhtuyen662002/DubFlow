from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from engine.dubflow.download.bilibili import BilibiliSourceAdapter, BilibiliTransportError, normalize_source_ref
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode
from engine.dubflow.download.bilibili.adapter import BilibiliDownloadChoice
from engine.dubflow.download.provider_transport import _map_payload


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

    def test_split_download_passes_the_selected_audio_to_muxer(self) -> None:
        muxer = Mock()
        adapter = BilibiliSourceAdapter(RecordedTransport(), stream_materializer=muxer)
        item = adapter.inspect("BV1AbC2345")
        choice = adapter.select_download(item)
        adapter.download(item, Path("/unused/final.mp4"), cancel=lambda: False)
        self.assertEqual(muxer.download.call_args.args[0], choice.candidate)
        self.assertEqual(muxer.download.call_args.kwargs["audio"], choice.audio_candidate)
        self.assertIsNotNone(choice.audio_candidate)

    def test_split_video_without_runtime_is_not_published_silent(self) -> None:
        materializer = Mock()
        adapter = BilibiliSourceAdapter(RecordedTransport(), materializer=materializer)
        with self.assertRaises(SourceError) as context:
            adapter.download(adapter.inspect("BV1AbC2345"), Path("/unused/final.mp4"))
        self.assertEqual(context.exception.code, SourceErrorCode.UNSUPPORTED)
        materializer.download.assert_not_called()

    def test_combined_choice_does_not_replace_its_audio_with_a_companion(self) -> None:
        payload = deepcopy(PAYLOAD)
        payload["data"]["durl"] = [{"url": "https://cdn.example.test/combined.mp4"}]
        adapter = BilibiliSourceAdapter(RecordedTransport(payload))
        choice = adapter.select_download(adapter.inspect("BV1AbC2345"), prefer_progressive=True)
        self.assertTrue(choice.candidate.has_audio)
        self.assertIsNone(choice.audio_candidate)

    def test_extractor_video_only_never_becomes_combined_progressive(self) -> None:
        raw = {"id": "BV1AbC2345", "title": "source", "formats": [
            {"format_id": "v", "url": "https://cdn.example.test/v.mp4", "vcodec": "h264", "acodec": "none", "height": 1080},
            {"format_id": "a", "url": "https://cdn.example.test/a.m4a", "vcodec": "none", "acodec": "aac"},
            {"format_id": "av", "url": "https://cdn.example.test/av.mp4", "vcodec": "h264", "acodec": "aac", "height": 360},
        ]}
        normalized = _map_payload("bilibili", raw, "BV1AbC2345")
        self.assertEqual(normalized["durl"], [{"url": "https://cdn.example.test/av.mp4", "width": None, "height": 360}])
        adapter = BilibiliSourceAdapter(RecordedTransport({"data": normalized}))
        item = adapter.inspect("BV1AbC2345")
        choice = adapter.select_download(item)
        self.assertFalse(choice.candidate.has_audio)
        self.assertIsNotNone(choice.audio_candidate)
        self.assertTrue(adapter.select_download(item, prefer_progressive=True).candidate.has_audio)

    def test_foreign_choice_is_rejected_before_downloading(self) -> None:
        muxer = Mock()
        adapter = BilibiliSourceAdapter(RecordedTransport(), stream_materializer=muxer)
        item = adapter.inspect("BV1AbC2345")
        choice = BilibiliDownloadChoice("BVwrong000", item.media_candidates[0], item.media_candidates[1])
        with self.assertRaises(SourceError) as context:
            adapter.download(item, Path("/unused/final.mp4"), choice=choice)
        self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        muxer.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
