from __future__ import annotations

import hashlib
import json
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
    def test_progress_reports_actual_resume_prefix_and_unknown_http_total_before_publication(self):
        payload = b"0123456789"
        for prefix, headers in ((5, {"content-length": "5", "content-range": "bytes 5-9/10"}), (0, {})):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / "media.mp4"
                destination.write_bytes(b"previous")
                if prefix:
                    destination.with_name("media.mp4.part").write_bytes(payload[:prefix])
                transport = FakeTransport([response(206 if prefix else 200, payload[prefix:], headers)])
                observed = []

                def progress(downloaded, total):
                    self.assertEqual(destination.read_bytes(), b"previous")
                    observed.append((downloaded, total))

                result = MediaMaterializer(transport, chunk_bytes=3).download(
                    MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"),
                    destination, expected_sha256=hashlib.sha256(payload).hexdigest(), progress=progress)
                self.assertEqual(observed[0], (prefix, 10 if prefix else None))
                self.assertEqual(observed[-1], (10, 10 if prefix else None))
                self.assertEqual([value[0] for value in observed], [5, 8, 10] if prefix else [0, 3, 6, 9, 10])
                self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(result.resumed, bool(prefix))

    def test_observer_failure_preserves_previous_output_and_recoverable_partial(self):
        payload = b"0123456789"
        transport = FakeTransport([response(200, payload, {"content-length": "10"})])
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.write_bytes(b"previous")

            def progress(downloaded, total):
                if downloaded >= 3:
                    raise RuntimeError("supervisor stopped accepting this dispatch")

            with self.assertRaisesRegex(RuntimeError, "stopped accepting"):
                MediaMaterializer(transport, chunk_bytes=3).download(
                    MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4"),
                    destination, progress=progress)
            self.assertEqual(destination.read_bytes(), b"previous")
            self.assertEqual(destination.with_name("media.mp4.part").read_bytes(), payload[:3])

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

    def test_committed_prefix_resumes_without_uncommitted_tail(self) -> None:
        for etag, status in (('"v1"', 206), ('"v2"', 206), ('"v2"', 200)):
            with self.subTest(etag=etag, status=status), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / "media.mp4"
                destination.write_bytes(b"old-export")
                part = destination.with_name("media.mp4.part")
                part.write_bytes(b"01234uncommitted-tail")
                candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4")
                receipt = part.with_name(part.name + ".resume.json")
                original = json.dumps({"schema_version": 1, "locator_sha256": hashlib.sha256(candidate.locator.encode()).hexdigest(),
                    "size_bytes": 5, "sha256": hashlib.sha256(b"01234").hexdigest(), "etag": '"v1"'})
                receipt.write_text(original)
                body = b"56789" if status == 206 else b"new-object"
                headers = {"content-length": str(len(body)), "etag": etag}
                if status == 206:
                    headers["content-range"] = "bytes 5-9/10"
                transport = FakeTransport([response(status, body, headers)])
                if status == 206 and etag == '"v2"':
                    with self.assertRaises(DownloadError) as context:
                        MediaMaterializer(transport).download(candidate, destination)
                    self.assertEqual(context.exception.code, DownloadErrorCode.RESUME_INVALID)
                    self.assertEqual(part.read_bytes(), b"01234uncommitted-tail")
                    self.assertEqual(receipt.read_text(), original)
                    self.assertEqual(destination.read_bytes(), b"old-export")
                else:
                    result = MediaMaterializer(transport).download(candidate, destination)
                    self.assertEqual(result.resumed, status == 206)
                    self.assertEqual(destination.read_bytes(), b"0123456789" if status == 206 else body)
                    self.assertFalse(receipt.exists())
                self.assertEqual(transport.calls[0][1]["Range"], "bytes=5-")
                self.assertEqual(transport.calls[0][1]["If-Range"], '"v1"')

    def test_tampered_committed_prefix_is_never_used_for_range_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            part = destination.with_name("media.mp4.part")
            part.write_bytes(b"XXXXXuncommitted-tail")
            candidate = MediaCandidate("media", "https://cdn.example.test/media.mp4", "progressive", "video/mp4")
            part.with_name(part.name + ".resume.json").write_text(json.dumps({
                "schema_version": 1, "locator_sha256": hashlib.sha256(candidate.locator.encode()).hexdigest(),
                "size_bytes": 5, "sha256": hashlib.sha256(b"01234").hexdigest(), "etag": '"v1"'}))
            transport = FakeTransport([response(200, b"new-object", {"content-length": "10", "etag": '"v1"'})])
            result = MediaMaterializer(transport).download(candidate, destination)
            self.assertNotIn("Range", transport.calls[0][1])
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), b"new-object")

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
