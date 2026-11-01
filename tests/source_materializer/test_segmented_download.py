"""Deterministic ranged-download coverage against a local range-capable server."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from engine.dubflow.download.materializer import (
    DownloadError, DownloadErrorCode, HttpResponse, MediaMaterializer, UrllibHttpTransport,
)
from engine.dubflow.download.source_adapter import MediaCandidate
from engine.dubflow.download.stream_materializer import StreamInfo, StreamMaterializer


KIB = 1024
BLOCK = 16 * KIB
SEGMENT = 256 * KIB


def generated(size: int, seed: bytes = b"v1") -> bytes:
    blocks = (hashlib.sha256(seed + index.to_bytes(4, "big")).digest() for index in range(size // 32 + 1))
    return b"".join(blocks)[:size]


class RangeServer:
    """Serves one object with Range/If-Range and scripted faults."""

    def __init__(self, payload: bytes, etag: str | None = '"v1"', *, ranges: bool = True,
                 accept_ranges: str | None = "bytes", delay: float = 0.0) -> None:
        self.payload, self.etag = payload, etag
        self.ranges, self.accept_ranges, self.delay = ranges, accept_ranges, delay
        self.lock = threading.Lock()
        self.requests: list[tuple[str | None, str | None]] = []
        self.drops: dict[int, int] = {}  # Range start -> bytes to send before closing (once).
        self.change_on_drop: tuple[bytes, str] | None = None
        self.change_on_range = False
        self.max_ranges_in_flight: int | None = None
        self.in_flight = 0
        self.rejected = 0
        self.stale = 0  # If-Range requests answered with a full changed body.
        self.served: dict[int, int] = {}  # Request index -> body bytes written.
        self.gate_after: int | None = None  # Range bytes sent before holding the response.
        self.gate = threading.Event()

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        range_value, if_range = request.headers.get("Range"), request.headers.get("If-Range")
        with self.lock:
            self.requests.append((range_value, if_range))
            index = len(self.requests) - 1
            if range_value and self.change_on_range and len(self.requests) > 1:
                self.payload = generated(len(self.payload), seed=str(len(self.requests)).encode())
                self.etag = f'"v{len(self.requests)}"'
            payload, etag = self.payload, self.etag
            limited = range_value is not None and self.max_ranges_in_flight is not None
            if limited and self.in_flight >= self.max_ranges_in_flight:
                self.rejected += 1
                request.send_response(429)
                request.send_header("Content-Length", "0")
                request.end_headers()
                return
            if limited:
                self.in_flight += 1
        released = []

        def release() -> None:
            # Free the slot before the client can observe the final byte.
            if limited and not released:
                released.append(True)
                with self.lock:
                    self.in_flight -= 1

        try:
            self._respond(request, index, payload, etag, range_value, if_range, release)
        finally:
            release()

    def _respond(self, request, index, payload, etag, range_value, if_range, release) -> None:
        start, end = 0, len(payload) - 1
        partial = bool(range_value and self.ranges and (if_range is None or if_range == etag))
        if range_value and self.ranges and not partial:
            with self.lock:
                self.stale += 1
        if partial:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_value)
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else end, len(payload) - 1)
            if start >= len(payload):
                request.send_response(416)
                request.send_header("Content-Length", "0")
                request.end_headers()
                return
        body = payload[start:end + 1]
        request.send_response(206 if partial else 200)
        request.send_header("Content-Length", str(len(body)))
        if etag is not None:
            request.send_header("ETag", etag)
        if self.accept_ranges is not None:
            request.send_header("Accept-Ranges", self.accept_ranges)
        if partial:
            request.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        request.end_headers()
        with self.lock:
            cut = self.drops.pop(start, None) if partial else None
        limit = len(body) if cut is None else cut
        gate_after = self.gate_after if partial else None
        try:
            for offset in range(0, limit, BLOCK):
                if gate_after is not None and offset >= gate_after:
                    self.gate.wait(15)
                    gate_after = None
                if offset + BLOCK >= limit:
                    release()
                request.wfile.write(body[offset:min(limit, offset + BLOCK)])
                request.wfile.flush()
                with self.lock:
                    self.served[index] = self.served.get(index, 0) + min(limit, offset + BLOCK) - offset
                if self.delay and offset + BLOCK < limit:
                    time.sleep(self.delay)
            if cut is not None:
                with self.lock:
                    if self.change_on_drop is not None:
                        self.payload, self.etag = self.change_on_drop
                        self.change_on_drop = None
                request.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # The client closed a response it no longer needs.


@contextmanager
def serve(server: RangeServer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            server.handle(self)

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/media.mp4"
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def materializer(**overrides) -> MediaMaterializer:
    options = {"chunk_bytes": BLOCK, "segments": 4, "min_segment_bytes": 128 * KIB, "segment_threshold_bytes": 512 * KIB}
    options.update(overrides)
    return MediaMaterializer(UrllibHttpTransport(timeout_s=10), **options)


def candidate(url: str) -> MediaCandidate:
    return MediaCandidate("media", url, "progressive", "video/mp4")


def bounds(total: int, count: int) -> list[tuple[int, int]]:
    edges = [total * index // count for index in range(count + 1)]
    return [(edges[index], edges[index + 1]) for index in range(count)]


def segment_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name.startswith("dubflow-segment-")]


class SegmentLayoutTests(unittest.TestCase):
    def test_default_layout_is_bounded_contiguous_and_needs_a_strong_validator(self):
        mib = 1024 * 1024
        default = MediaMaterializer()
        for total, count in ((7 * mib, None), (8 * mib, 4), (9 * mib + 3, 4), (17 * mib, 8), (4096 * mib + 7, 8)):
            with self.subTest(total=total):
                plan = default._plan_segments({"etag": '"x"'}, total)
                if count is None:
                    self.assertIsNone(plan)
                    continue
                self.assertEqual([(s.start, s.end) for s in plan.segments], bounds(total, count))
                self.assertTrue(all(s.length >= 2 * mib for s in plan.segments))
        self.assertEqual(len(MediaMaterializer(segments=16)._plan_segments({"etag": '"x"'}, 1024 * mib).segments), 16)
        for headers in ({}, {"etag": 'W/"weak"'}, {"etag": '"x"', "accept-ranges": "none"}):
            with self.subTest(headers=headers):
                self.assertIsNone(default._plan_segments(headers, 64 * mib))
        self.assertIsNone(MediaMaterializer(segments=1)._plan_segments({"etag": '"x"'}, 64 * mib))
        with self.assertRaises(ValueError):
            MediaMaterializer(segments=17)


class SegmentedDownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.destination = self.root / "media.mp4"
        self.part = self.destination.with_name("media.mp4.part")
        self.receipt = self.part.with_name(self.part.name + ".resume.json")
        self.payload = generated(1024 * KIB)

    def assertProgress(self, observed, total, first=0):
        self.assertEqual(observed[0], (first, total))
        self.assertEqual(observed[-1], (total, total))
        values = [value[0] for value in observed]
        self.assertEqual(values, sorted(values), "progress must be monotonic")
        self.assertTrue(all(value[1] == total for value in observed))

    def test_segments_are_fetched_concurrently_at_their_offsets(self):
        server = RangeServer(self.payload)
        observed = []
        with serve(server) as url:
            result = materializer().download(candidate(url), self.destination,
                expected_sha256=hashlib.sha256(self.payload).hexdigest(), expected_size=len(self.payload),
                progress=lambda done, total: observed.append((done, total)))
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual(result.sha256, hashlib.sha256(self.payload).hexdigest())
        self.assertFalse(result.resumed)
        self.assertEqual(server.requests[0], (None, None))
        self.assertEqual(sorted(server.requests[1:]),
            sorted((f"bytes={start}-{end - 1}", '"v1"') for start, end in bounds(len(self.payload), 4)))
        self.assertFalse(self.part.exists())
        self.assertFalse(self.receipt.exists())
        self.assertProgress(observed, len(self.payload))
        self.assertEqual(segment_threads(), [])

    def test_dropped_connection_retries_from_the_segment_offset(self):
        server = RangeServer(self.payload)
        second = bounds(len(self.payload), 4)[1]
        server.drops[second[0]] = 3 * BLOCK
        with serve(server) as url:
            materializer().download(candidate(url), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        retries = [value for value, _ in server.requests if value == f"bytes={second[0] + 3 * BLOCK}-{second[1] - 1}"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(len(server.requests), 6)

    def test_interrupted_download_resumes_only_missing_ranges(self):
        server = RangeServer(self.payload)
        server.gate_after = 5 * BLOCK
        self.addCleanup(server.gate.set)
        stop = threading.Event()

        def progress(done, total):
            if done >= 4 * 5 * BLOCK:  # Every segment is held at its gate.
                stop.set()

        with serve(server) as url:
            with self.assertRaises(DownloadError) as context:
                materializer().download(candidate(url), self.destination, cancel=stop.is_set, progress=progress)
            self.assertEqual(context.exception.code, DownloadErrorCode.CANCELLED)
            record = json.loads(self.receipt.read_text())
            self.assertEqual(record["schema_version"], 2)
            self.assertNotIn("127.0.0.1", self.receipt.read_text())
            self.assertEqual(record["total_bytes"], len(self.payload))
            rows = record["segments"]
            committed = sum(row[2] for row in rows)
            self.assertEqual([row[2] for row in rows], [5 * BLOCK] * 4)
            for start, end, done, digest in rows:
                self.assertEqual(digest, hashlib.sha256(self.payload[start:start + done]).hexdigest())
            server.gate_after = None
            server.gate.set()
            first_call = len(server.requests)
            observed = []
            result = materializer().download(candidate(url), self.destination,
                progress=lambda done, total: observed.append((done, total)))
        self.assertTrue(result.resumed)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual(sorted(server.requests[first_call:]),
            sorted((f"bytes={start + done}-{end - 1}", '"v1"') for start, end, done, _ in rows if done < end - start))
        self.assertEqual(sum(sent for index, sent in server.served.items() if index >= first_call),
            len(self.payload) - committed)
        self.assertProgress(observed, len(self.payload), first=committed)
        self.assertFalse(self.receipt.exists())

    def test_tampered_segment_is_refetched_from_its_start(self):
        server = RangeServer(self.payload)
        server.gate_after = 5 * BLOCK
        self.addCleanup(server.gate.set)
        stop = threading.Event()
        with serve(server) as url:
            with self.assertRaises(DownloadError):
                materializer().download(candidate(url), self.destination, cancel=stop.is_set,
                    progress=lambda done, total: stop.set() if done >= 4 * 5 * BLOCK else None)
            server.gate_after = None
            server.gate.set()
            rows = json.loads(self.receipt.read_text())["segments"]
            start, end, done, _ = rows[0]
            self.assertGreater(done, 0)
            with self.part.open("r+b") as stream:
                stream.write(b"\0" * 8)
            first_call = len(server.requests)
            materializer().download(candidate(url), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertIn((f"bytes={start}-{end - 1}", '"v1"'), server.requests[first_call:])

    def test_server_without_ranges_falls_back_to_one_connection(self):
        for accept, expected_requests in ((None, None), ("none", 1)):
            with self.subTest(accept_ranges=accept), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / "media.mp4"
                server = RangeServer(self.payload, ranges=False, accept_ranges=accept)
                with serve(server) as url:
                    result = materializer().download(candidate(url), destination,
                        expected_sha256=hashlib.sha256(self.payload).hexdigest())
                self.assertEqual(destination.read_bytes(), self.payload)
                self.assertFalse(result.resumed)
                self.assertEqual(server.requests[-1], (None, None))
                if expected_requests is not None:
                    self.assertEqual(len(server.requests), expected_requests)
                else:
                    # Ranges were attempted once, then the single path ran.
                    self.assertEqual(sum(value is None for value, _ in server.requests), 2)
                    self.assertLessEqual(len(server.requests), 6)
                self.assertFalse(destination.with_name("media.mp4.part.resume.json").exists())
                self.assertEqual(segment_threads(), [])

    def test_small_or_unvalidated_objects_keep_the_single_connection(self):
        for payload, etag in ((generated(300 * KIB), '"v1"'), (self.payload, None), (self.payload, 'W/"weak"')):
            with self.subTest(size=len(payload), etag=etag), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / "media.mp4"
                server = RangeServer(payload, etag)
                with serve(server) as url:
                    materializer().download(candidate(url), destination)
                self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(server.requests, [(None, None)])

    def test_validator_change_mid_download_restarts_cleanly(self):
        server = RangeServer(self.payload)
        changed = generated(len(self.payload), seed=b"v2")
        second = bounds(len(self.payload), 4)[1]
        server.drops[second[0]] = 2 * BLOCK
        server.change_on_drop = (changed, '"v2"')
        with serve(server) as url:
            result = materializer().download(candidate(url), self.destination)
        # Never a splice of two versions: the whole new object, verified.
        self.assertEqual(self.destination.read_bytes(), changed)
        self.assertEqual(result.sha256, hashlib.sha256(changed).hexdigest())
        self.assertFalse(result.resumed)
        # Whichever old-validator request arrived first was answered with
        # the new full body, which triggered exactly one clean restart.
        self.assertGreaterEqual(server.stale, 1)
        self.assertEqual(sum(value is None for value, _ in server.requests), 2)
        restart = server.requests.index((None, None), 1)
        self.assertTrue(all(if_range == '"v2"' for value, if_range in server.requests[restart + 1:]))
        self.assertFalse(self.receipt.exists())

    def test_repeated_validator_change_falls_back_to_one_connection(self):
        server = RangeServer(self.payload)
        server.change_on_range = True
        with serve(server) as url:
            result = materializer().download(candidate(url), self.destination)
        self.assertEqual(result.sha256, hashlib.sha256(self.destination.read_bytes()).hexdigest())
        self.assertEqual(sum(value is None for value, _ in server.requests), 3)
        self.assertLessEqual(len(server.requests), 3 + 2 * 4)
        self.assertEqual(server.requests[-1], (None, None))

    def test_resumed_validator_change_restarts_instead_of_splicing(self):
        server = RangeServer(self.payload)
        server.gate_after = 5 * BLOCK
        self.addCleanup(server.gate.set)
        stop = threading.Event()
        with serve(server) as url:
            with self.assertRaises(DownloadError):
                materializer().download(candidate(url), self.destination, cancel=stop.is_set,
                    progress=lambda done, total: stop.set() if done >= 4 * 5 * BLOCK else None)
            self.assertEqual(json.loads(self.receipt.read_text())["schema_version"], 2)
            changed = generated(len(self.payload), seed=b"v2")
            server.payload, server.etag, server.gate_after = changed, '"v2"', None
            server.gate.set()
            result = materializer().download(candidate(url), self.destination)
        self.assertFalse(result.resumed)
        self.assertEqual(self.destination.read_bytes(), changed)

    def test_rate_limited_segments_narrow_concurrency(self):
        server = RangeServer(self.payload, delay=0.05)
        server.max_ranges_in_flight = 2
        with serve(server) as url:
            materializer().download(candidate(url), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertGreaterEqual(server.rejected, 1)

    def test_cancel_stops_every_segment_promptly_and_keeps_the_partial(self):
        server = RangeServer(self.payload, delay=0.5)
        self.destination.write_bytes(b"previous-export")
        started = threading.Event()
        cancelled_at = []

        def cancel():
            if started.is_set() and not cancelled_at:
                cancelled_at.append(time.monotonic())
            return bool(cancelled_at)

        with serve(server) as url:
            with self.assertRaises(DownloadError) as context:
                materializer().download(candidate(url), self.destination, cancel=cancel,
                    progress=lambda done, total: started.set() if done else None)
            elapsed = time.monotonic() - cancelled_at[0]
        self.assertEqual(context.exception.code, DownloadErrorCode.CANCELLED)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(segment_threads(), [])
        self.assertEqual(self.destination.read_bytes(), b"previous-export")
        self.assertEqual(self.part.stat().st_size, len(self.payload))
        self.assertEqual(json.loads(self.receipt.read_text())["schema_version"], 2)

    def test_cancel_interrupts_a_stalled_connection(self):
        server = RangeServer(self.payload)
        server.gate_after = BLOCK  # Every range stalls after one block.
        self.addCleanup(server.gate.set)
        stop = threading.Event()
        cancelled_at = []

        def progress(done, total):
            if done >= 4 * BLOCK and not stop.is_set():
                cancelled_at.append(time.monotonic())
                stop.set()

        with serve(server) as url:
            with self.assertRaises(DownloadError) as context:
                materializer().download(candidate(url), self.destination, cancel=stop.is_set, progress=progress)
            elapsed = time.monotonic() - cancelled_at[0]
            server.gate.set()
        self.assertEqual(context.exception.code, DownloadErrorCode.CANCELLED)
        self.assertLess(elapsed, 3.0)  # Not the 10 s socket timeout.
        self.assertEqual(segment_threads(), [])
        self.assertEqual([row[2] for row in json.loads(self.receipt.read_text())["segments"]], [BLOCK] * 4)

    def test_checksum_pin_failure_never_publishes_and_restarts_next_time(self):
        server = RangeServer(self.payload)
        self.destination.write_bytes(b"previous-export")
        with serve(server) as url:
            with self.assertRaises(DownloadError) as context:
                materializer().download(candidate(url), self.destination, expected_sha256="0" * 64)
            self.assertEqual(context.exception.code, DownloadErrorCode.CHECKSUM_MISMATCH)
            self.assertEqual(self.destination.read_bytes(), b"previous-export")
            self.assertFalse(self.receipt.exists())
            first_call = len(server.requests)
            result = materializer().download(candidate(url), self.destination,
                expected_sha256=hashlib.sha256(self.payload).hexdigest())
        self.assertFalse(result.resumed)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertNotIn((f"bytes={len(self.payload)}-", '"v1"'), server.requests[first_call + 1:])

    def test_schema_one_receipt_keeps_its_single_connection_resume(self):
        server = RangeServer(self.payload)
        committed = 300 * KIB
        self.part.write_bytes(self.payload[:committed])
        with serve(server) as url:
            self.receipt.write_text(json.dumps({"schema_version": 1, "size_bytes": committed,
                "sha256": hashlib.sha256(self.payload[:committed]).hexdigest(), "etag": '"v1"',
                "locator_sha256": hashlib.sha256(url.encode()).hexdigest()}))
            result = materializer().download(candidate(url), self.destination)
        self.assertTrue(result.resumed)
        self.assertEqual(server.requests, [(f"bytes={committed}-", '"v1"')])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_unusable_segmented_receipt_never_resumes_a_holed_partial(self):
        server = RangeServer(self.payload)
        self.part.write_bytes(b"\0" * len(self.payload))
        with serve(server) as url:
            self.receipt.write_text(json.dumps({"schema_version": 2, "etag": '"v1"', "total_bytes": len(self.payload),
                "locator_sha256": "0" * 64, "segments": [[0, len(self.payload), 10, "0" * 64]]}))
            materializer().download(candidate(url), self.destination,
                expected_sha256=hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(server.requests[0], (None, None))
        self.assertEqual(self.destination.read_bytes(), self.payload)


class ConcurrentStreamTests(unittest.TestCase):
    def test_dash_video_and_audio_transfer_concurrently(self):
        both = threading.Barrier(2, timeout=5)
        calls = []

        class Transport:
            def open(self, url, *, headers=None):
                calls.append(url)
                both.wait()  # Sequential transfers would break this barrier.
                data = b"audio" if "/a.m4s" in url else b"video"
                return HttpResponse(200, {"content-length": str(len(data))}, BytesIO(data), url)

        class Muxer:
            fingerprint = "a" * 64

            def probe(self, path, *, cancel=None):
                video, audio = StreamInfo("video", "h264", 0, 180000), StreamInfo("audio", "aac", 0, 180000)
                return {"video.stream": (video,), "audio.stream": (audio,)}.get(path.name, (video, audio))

            def mux(self, video, audio, output, *, cancel, max_bytes):
                self.inputs = (video.read_bytes(), audio.read_bytes())
                output.write_bytes(b"video+audio")

        observed = []
        muxer = Muxer()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = StreamMaterializer(muxer, materializer=MediaMaterializer(Transport())).download(
                MediaCandidate("v", "https://cdn.example.test/v.m4s", "dash", "video/mp4", 1280, 720, False),
                root / "final.mp4", audio=MediaCandidate("a", "https://cdn.example.test/a.m4s", "dash", "audio/mp4"),
                root=root, progress=lambda done, total: observed.append((done, total)))
            self.assertEqual((root / "final.mp4").read_bytes(), b"video+audio")
            self.assertEqual(len(list(root.rglob("*.stream.json"))), 2)
        self.assertEqual(muxer.inputs, (b"video", b"audio"))
        self.assertFalse(result.resumed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(observed[-1], (10, 10))
        values = [value[0] for value in observed]
        self.assertEqual(values, sorted(values))


if __name__ == "__main__":
    unittest.main()
