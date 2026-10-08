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
            result = MediaMaterializer(transport).download(MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"), destination, expected_size=10, expected_sha256=hashlib.sha256(payload).hexdigest())
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

    def test_unbound_partial_without_hash_is_restarted(self) -> None:
        transport = FakeTransport([response(200, b"changed", {"content-length": "7"})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.with_name("media.mp4.part").write_bytes(b"stale")
            result = MediaMaterializer(transport).download(MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"), destination)
            self.assertNotIn("Range", transport.calls[0][1])
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), b"changed")

    def test_strong_validator_resumes_interrupted_response(self) -> None:
        transport = FakeTransport([
            response(200, b"01234", {"content-length": "10", "etag": '"version-1"'}),
            response(206, b"56789", {"content-length": "5", "content-range": "bytes 5-9/10", "etag": '"version-1"'}),
        ])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4?token=secret", "progressive", "video/mp4")
            materializer = MediaMaterializer(transport, chunk_bytes=2)
            with self.assertRaises(DownloadError):
                materializer.download(candidate, destination)
            record = destination.with_name("media.mp4.part.resume.json")
            self.assertNotIn("secret", record.read_text())
            self.assertNotIn("example.test", record.read_text())
            result = materializer.download(candidate, destination)
            self.assertTrue(result.resumed)
            self.assertEqual(transport.calls[1][1]["If-Range"], '"version-1"')
            self.assertEqual(destination.read_bytes(), b"0123456789")
            self.assertFalse(record.exists())

    def test_changed_validator_does_not_append_or_overwrite_final(self) -> None:
        transport = FakeTransport([
            response(200, b"01234", {"content-length": "10", "etag": '"old"'}),
            response(206, b"abcde", {"content-length": "5", "content-range": "bytes 5-9/10", "etag": '"new"'}),
        ])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.write_bytes(b"existing")
            candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4")
            materializer = MediaMaterializer(transport)
            with self.assertRaises(DownloadError):
                materializer.download(candidate, destination)
            with self.assertRaises(DownloadError) as context:
                materializer.download(candidate, destination)
            self.assertEqual(context.exception.code, DownloadErrorCode.RESUME_INVALID)
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(destination.with_name("media.mp4.part").read_bytes(), b"01234")

    def test_tampered_partial_restarts_even_with_valid_remote_etag(self) -> None:
        transport = FakeTransport([
            response(200, b"01234", {"content-length": "10", "etag": '"v1"'}),
            response(200, b"0123456789", {"content-length": "10", "etag": '"v1"'}),
        ])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4")
            materializer = MediaMaterializer(transport)
            with self.assertRaises(DownloadError):
                materializer.download(candidate, destination)
            destination.with_name("media.mp4.part").write_bytes(b"xxxxx")
            result = materializer.download(candidate, destination)
            self.assertNotIn("Range", transport.calls[1][1])
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), b"0123456789")

    def test_local_changed_prefix_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"new-content")
            destination = Path(temp) / "copy.mp4"
            destination.with_name("copy.mp4.part").write_bytes(b"old-")
            result = MediaMaterializer().download(MediaCandidate("local", str(source), "local", "video/mp4"), destination)
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_hash_only_range_cannot_certify_an_unbound_prefix_with_new_etag(self) -> None:
        transport = FakeTransport([
            response(206, b"56", {"content-length": "5", "content-range": "bytes 5-9/10", "etag": '"remote"'}),
            response(200, b"0123456789", {"content-length": "10", "etag": '"remote"'}),
        ])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.with_name("media.mp4.part").write_bytes(b"wrong")
            candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4")
            materializer = MediaMaterializer(transport)
            with self.assertRaises(DownloadError):
                materializer.download(candidate, destination, expected_sha256=hashlib.sha256(b"0123456789").hexdigest())
            result = materializer.download(candidate, destination)
            self.assertNotIn("Range", transport.calls[1][1])
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), b"0123456789")

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
