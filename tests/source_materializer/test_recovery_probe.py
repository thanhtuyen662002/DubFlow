"""Deterministic access evidence; no live source or completed-media claim."""
import io
import http.server
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock

from engine.dubflow.download.materializer import HttpResponse, MediaMaterializer, DownloadError, DownloadErrorCode
from engine.dubflow.download.source_adapter import MediaCandidate, SourceIdentity, SourceItem, SourceError, SourceErrorCode
from engine.dubflow.download.bilibili.adapter import BilibiliSourceAdapter
from engine.dubflow.download.douyin.adapter import DouyinSourceAdapter
from engine.dubflow.download.generic.adapter import GenericUrlAdapter


class Body(io.BytesIO):
    def __init__(self, value=b"abc"):
        super().__init__(value)
        self.reads = []

    def read(self, count=-1):
        self.reads.append(count)
        return super().read(count)


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, url, *, headers):
        self.requests.append((url, headers))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class RecoveryProbeTests(unittest.TestCase):
    def setUp(self):
        self.video = MediaCandidate("video", "https://cdn.example.test/video?token=private", "progressive", "video/mp4", 1920, 1080)
        self.audio = MediaCandidate("audio", "https://cdn.example.test/audio", "dash", "audio/mp4")

    def response(self, *, body=b"abc", status=206, headers=None):
        stream = Body(body)
        return HttpResponse(status, {"Content-Range": "bytes 0-0/3", **(headers or {})}, stream, self.video.locator)

    def test_actual_owned_http_transport_observes_outage_then_both_restored_streams(self):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Range"), self.headers.get("Accept-Encoding")))
                if len(requests) == 1:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Type", "audio/mp4" if self.path == "/audio" else "video/mp4")
                self.send_header("Content-Range", "bytes 0-0/3")
                self.send_header("Content-Length", "1")
                self.end_headers()
                self.wfile.write(b"x")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_port)
            video = replace(self.video, locator=origin + "/video", has_audio=False)
            audio = replace(self.audio, locator=origin + "/audio")
            materializer = MediaMaterializer()
            with self.assertRaises(SourceError) as failure:
                materializer.probe_access(video, audio)
            self.assertEqual(failure.exception.code, SourceErrorCode.NETWORK)
            self.assertEqual(materializer.probe_access(video, audio), 2)
            self.assertEqual(requests, [(path, "bytes=0-0", "identity") for path in ("/video", "/video", "/audio")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_split_streams_read_one_byte_each_and_close_without_publication(self):
        responses = [self.response(), self.response(headers={"Content-Type": "audio/mp4"})]
        transport = Transport(responses)
        video = replace(self.video, has_audio=False, kind="dash")
        self.assertEqual(MediaMaterializer(transport).probe_access(video, self.audio), 2)
        self.assertEqual([url for url, _ in transport.requests], [video.locator, self.audio.locator])
        for response, (_, headers) in zip(responses, transport.requests):
            self.assertTrue(response.body.closed)
            self.assertEqual(response.body.reads, [1])
            self.assertEqual(headers, {"Range": "bytes=0-0", "Accept-Encoding": "identity"})

    def test_audio_labelled_by_its_container_is_accepted_but_not_another_container(self):
        # Bilibili's CDN answers a DASH audio .m4s with Content-Type: video/mp4.
        video = replace(self.video, has_audio=False, kind="dash")
        responses = [self.response(headers={"Content-Type": "video/mp4"}), self.response(headers={"Content-Type": "video/mp4"})]
        self.assertEqual(MediaMaterializer(Transport(responses)).probe_access(video, self.audio), 2)
        responses = [self.response(headers={"Content-Type": "video/mp4"}), self.response(headers={"Content-Type": "audio/webm"})]
        with self.assertRaises(SourceError) as failure:
            MediaMaterializer(Transport(responses)).probe_access(video, self.audio)
        self.assertEqual(failure.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_ignored_range_is_bounded_and_no_metadata_only_pass(self):
        response = self.response(status=200, body=b"x" * 100000)
        self.assertEqual(MediaMaterializer(Transport([response])).probe_access(self.video), 1)
        self.assertEqual(response.body.reads, [1])
        self.assertTrue(response.body.closed)
        response = self.response(body=b"")
        with self.assertRaises(SourceError) as failure:
            MediaMaterializer(Transport([response])).probe_access(self.video)
        self.assertEqual(failure.exception.code, SourceErrorCode.NETWORK)
        self.assertTrue(response.body.closed)

    def test_http_auth_rate_limit_network_and_unexpected_status_refuse_before_read(self):
        for status, code in ((401, "AUTH_REQUIRED"), (403, "AUTH_REQUIRED"), (429, "RATE_LIMITED"),
                             (503, "NETWORK"), (404, "SOURCE_CHANGED"), (302, "SOURCE_CHANGED")):
            with self.subTest(status=status):
                response = self.response(status=status)
                with self.assertRaises(SourceError) as failure:
                    MediaMaterializer(Transport([response])).probe_access(self.video)
                self.assertEqual(failure.exception.code.value, code)
                self.assertEqual(response.body.reads, [])
                self.assertTrue(response.body.closed)
                self.assertNotIn("private", str(failure.exception))

    def test_login_html_encoded_or_invalid_partial_response_never_authorizes_retry(self):
        for headers in ({"Content-Type": "text/html"}, {"Content-Encoding": "gzip"},
                        {"Content-Range": "bytes 1-1/3"}, {"Content-Range": "bytes 0-0/*"},
                        {"Content-Range": "bytes 0-9/3"}, {"Content-Range": "bytes 0-0/0"},
                        {"Content-Range": "bytes 0-0/" + "9" * 5000}):
            with self.subTest(headers=headers):
                response = self.response(headers=headers)
                with self.assertRaises(SourceError):
                    MediaMaterializer(Transport([response])).probe_access(self.video)
                self.assertTrue(response.body.closed)
                self.assertEqual(response.body.reads, [])

    def test_transport_exception_is_typed_and_redacted(self):
        for error in (DownloadError(DownloadErrorCode.NETWORK, "private-token", retryable=True),
                      OSError("private-token")):
            with self.subTest(error=type(error).__name__), self.assertRaises(SourceError) as failure:
                MediaMaterializer(Transport([error])).probe_access(self.video)
            self.assertEqual(failure.exception.code, SourceErrorCode.NETWORK)
            self.assertNotIn("private-token", str(failure.exception))

    def test_second_stream_failure_preserves_first_close_and_refuses_recovery(self):
        first, second = self.response(), self.response(status=403)
        with self.assertRaises(SourceError):
            MediaMaterializer(Transport([first, second])).probe_access(replace(self.video, has_audio=False), self.audio)
        self.assertTrue(first.body.closed and second.body.closed)
        self.assertEqual(first.body.reads, [1])
        self.assertEqual(second.body.reads, [])

    def test_missing_audio_playlists_local_and_credential_locator_refuse(self):
        for video in (replace(self.video, has_audio=False), replace(self.video, kind="hls"),
                      replace(self.video, locator="https://cdn.example.test/file.mpd"),
                      replace(self.video, kind="local"),
                      replace(self.video, locator="https://user:password@example.test/file")):
            with self.subTest(video=video.kind), self.assertRaises(SourceError):
                MediaMaterializer(Transport([])).probe_access(video)

    def test_actual_provider_default_selection_probes_matching_video_and_audio(self):
        for cls, provider in ((BilibiliSourceAdapter, "bilibili"), (DouyinSourceAdapter, "douyin"), (GenericUrlAdapter, "generic")):
            with self.subTest(provider=provider):
                high = replace(self.video, kind="dash", has_audio=False)
                low = replace(self.video, candidate_id="low", locator="https://cdn.example.test/low", width=640, height=360)
                item = SourceItem(SourceIdentity(provider, "one", "https://example.test/one"), "one", media_candidates=(low, high, self.audio))
                responses = [self.response(), self.response()]
                transport = Transport(responses)
                adapter = cls(transport=object(), materializer=MediaMaterializer(transport))
                self.assertEqual(adapter.probe_download_access(item), 2)
                self.assertEqual([url for url, _ in transport.requests], [high.locator, self.audio.locator])
                with self.assertRaises(SourceError):
                    adapter.probe_download_access(replace(item, identity=SourceIdentity("other", "one", "https://example.test/one")))

    def test_integrity_and_resume_failures_are_not_misclassified_as_network_recovery(self):
        for cls, provider in ((BilibiliSourceAdapter, "bilibili"), (DouyinSourceAdapter, "douyin"), (GenericUrlAdapter, "generic")):
            for code in (DownloadErrorCode.CHECKSUM_MISMATCH, DownloadErrorCode.RESUME_INVALID, DownloadErrorCode.INVALID_DESTINATION):
                with self.subTest(provider=provider, code=code):
                    materializer = Mock()
                    materializer.download.side_effect = DownloadError(code, "recorded integrity/path refusal")
                    adapter = cls(transport=object(), materializer=materializer)
                    item = SourceItem(SourceIdentity(provider, "one", "https://example.test/one"), "one", media_candidates=(self.video,))
                    with self.assertRaises(SourceError) as failure:
                        adapter.download(item, "unused-output.mp4")
                    self.assertEqual(failure.exception.code, SourceErrorCode.SOURCE_CHANGED)
