from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from engine.dubflow.download.generic import GenericUrlAdapter, YtDlpTransport
from engine.dubflow.download.materializer import HttpResponse, MediaMaterializer
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class MetadataTransport:
    def __init__(self, payload: object):
        self.payload = payload
        self.urls: list[str] = []

    def inspect_url(self, source_url: str):
        self.urls.append(source_url)
        return self.payload


class MediaTransport:
    def open(self, url: str, *, headers=None):
        return HttpResponse(200, {"content-length": "4"}, BytesIO(b"data"), url)


class Runner:
    def __init__(self, code: int = 0, payload: str = "{}", error: str = ""):
        self.code = code
        self.payload = payload
        self.error = error
        self.argv = None

    def run(self, argv, *, timeout_s: float):
        self.argv = tuple(argv)
        return self.code, self.payload, self.error


class GenericAdapterTests(unittest.TestCase):
    def test_metadata_maps_identity_formats_subtitles_and_download(self) -> None:
        payload = {
            "id": "video-1",
            "title": "Recorded URL",
            "webpage_url": "https://video.example.test/watch/video-1?utm_source=x",
            "duration": "1.25",
            "formats": [{"format_id": "low", "url": "https://cdn.example.test/low.mp4", "width": 640, "height": 360, "vcodec": "h264", "acodec": "aac"}],
            "subtitles": {"zh-Hans": [{"url": "https://cdn.example.test/sub.vtt", "ext": "vtt"}]},
        }
        adapter = GenericUrlAdapter(MetadataTransport(payload), materializer=MediaMaterializer(MediaTransport()))
        item = adapter.inspect("https://video.example.test/watch/video-1?utm_source=x#comments")
        self.assertEqual(item.identity.identity_key, "generic:video-1")
        self.assertEqual(item.duration_ticks, 112500)
        self.assertEqual(item.subtitle_candidates[0].language, "zh-Hans")
        with tempfile.TemporaryDirectory() as temp:
            result = adapter.download(item, Path(temp) / "video.mp4")
            self.assertEqual(result.sha256, "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7")

    def test_ytdlp_boundary_uses_argv_without_shell_and_redacts_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "yt-dlp.exe"
            executable.write_bytes(b"fixture")
            runner = Runner(payload='{"id":"x","title":"X"}')
            payload = YtDlpTransport(executable, runner=runner).inspect_url("https://video.example.test/watch/1")
            self.assertEqual(payload["id"], "x")
            self.assertEqual(runner.argv[1:5], ("--dump-single-json", "--no-warnings", "--skip-download", "--no-playlist"))
            self.assertEqual(runner.argv[-1], "https://video.example.test/watch/1")

            failing = Runner(code=1, error="Cookie: secret-token private video")
            transport = YtDlpTransport(executable, runner=failing)
            with self.assertRaises(SourceError) as context:
                transport.inspect_url("https://video.example.test/watch/1")
            self.assertEqual(context.exception.code, SourceErrorCode.AUTH_REQUIRED)
            self.assertNotIn("secret-token", str(context.exception))

    def test_unsupported_playlist_enumeration_is_structured(self) -> None:
        adapter = GenericUrlAdapter(MetadataTransport({"id": "x", "title": "X", "url": "https://cdn.example.test/x.mp4"}))
        with self.assertRaises(SourceError) as context:
            adapter.enumerate_channel("playlist")
        self.assertEqual(context.exception.code, SourceErrorCode.UNSUPPORTED)

    def test_audio_first_metadata_selects_video_and_preserves_companion(self) -> None:
        payload = {"id": "split", "formats": [
            {"format_id": "audio", "url": "https://cdn.example.test/a.m4a", "vcodec": "none", "acodec": "aac"},
            {"format_id": "small", "url": "https://cdn.example.test/small.mp4", "vcodec": "h264", "acodec": "aac", "height": 360, "width": 640},
            {"format_id": "large", "url": "https://cdn.example.test/large.mp4", "vcodec": "h264", "acodec": "none", "height": 1080, "width": 1920},
        ]}
        muxer = Mock()
        adapter = GenericUrlAdapter(MetadataTransport(payload), stream_materializer=muxer)
        item = adapter.inspect("https://video.example.test/watch/split")
        self.assertEqual(item.media_candidates[0].mime_type, "audio/mp4")
        adapter.download(item, Path("/unused/final.mp4"))
        self.assertEqual(muxer.download.call_args.args[0].candidate_id, "large")
        self.assertEqual(muxer.download.call_args.kwargs["audio"].candidate_id, "audio")
        adapter.download(item, Path("/unused/final.mp4"), candidate_id="small")
        self.assertEqual(muxer.download.call_args.args[0].candidate_id, "small")
        self.assertIsNone(muxer.download.call_args.kwargs["audio"])

    def test_video_only_without_runtime_is_explicitly_unsupported(self) -> None:
        materializer = Mock()
        adapter = GenericUrlAdapter(MetadataTransport({"id": "x", "url": "https://cdn.example.test/v.mp4", "vcodec": "h264", "acodec": "none"}), materializer=materializer)
        with self.assertRaises(SourceError) as context:
            adapter.download(adapter.inspect("https://video.example.test/x"), Path("/unused/final.mp4"))
        self.assertEqual(context.exception.code, SourceErrorCode.UNSUPPORTED)
        materializer.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
