from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from engine.dubflow.download.materializer import (
    DownloadError,
    DownloadErrorCode,
    HttpResponse,
    MediaMaterializer,
    validate_http_url,
)
from engine.dubflow.download.source_adapter import MediaCandidate


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def open(self, url: str, *, headers=None) -> HttpResponse:
        self.calls.append((url, dict(headers or {})))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        return self.responses.pop(0)


def response(status: int, body: bytes, headers: dict[str, str]) -> HttpResponse:
    return HttpResponse(status, headers, BytesIO(body), "https://cdn.example.test/media.mp4")


class MaterializerTests(unittest.TestCase):
    def test_fresh_download_hashes_and_publishes_atomically(self) -> None:
        payload = b"dubflow-media"
        transport = FakeTransport([response(200, payload, {"content-length": str(len(payload) )})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            result = MediaMaterializer(transport, chunk_bytes=3).download(
                MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"),
                destination,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_size=len(payload),
            )
            self.assertEqual(result.size_bytes, len(payload))
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_name("media.mp4.part").exists())

    def test_range_resume_hashes_existing_private_partial(self) -> None:
        payload = b"0123456789"
        rest = payload[5:]
        transport = FakeTransport([response(206, rest, {"content-length": "5", "content-range": "bytes 5-9/10"})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.with_name("media.mp4.part").write_bytes(payload[:5])
            result = MediaMaterializer(transport).download(MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"), destination, expected_size=10)
            self.assertTrue(result.resumed)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(transport.calls[0][1]["Range"], "bytes=5-")

    def test_server_ignoring_range_restarts_safely(self) -> None:
        payload = b"complete"
        transport = FakeTransport([response(200, payload, {"content-length": str(len(payload))})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.with_name("media.mp4.part").write_bytes(b"stale")
            result = MediaMaterializer(transport).download(MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"), destination)
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), payload)

    def test_invalid_range_and_hash_leave_partial_for_retry(self) -> None:
        transport = FakeTransport([response(206, b"rest", {"content-length": "4", "content-range": "bytes 3-5/6"})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.with_name("media.mp4.part").write_bytes(b"12")
            with self.assertRaises(DownloadError) as context:
                MediaMaterializer(transport).download(MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"), destination)
            self.assertEqual(context.exception.code, DownloadErrorCode.RESUME_INVALID)
            self.assertTrue(destination.with_name("media.mp4.part").exists())

    def test_rejects_credential_urls_and_non_progressive_candidates(self) -> None:
        with self.assertRaises(DownloadError) as credential:
            validate_http_url("https://user:secret@example.test/media.mp4")
        self.assertEqual(credential.exception.code, DownloadErrorCode.INVALID_SOURCE)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(DownloadError) as manifest:
                MediaMaterializer().download(MediaCandidate("manifest", "https://cdn.example.test/index.m3u8", "hls", "application/x-mpegURL"), Path(temp) / "media.mp4")
            self.assertEqual(manifest.exception.code, DownloadErrorCode.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
