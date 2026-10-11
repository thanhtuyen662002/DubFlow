from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError

from engine.dubflow.download.materializer import (
    DownloadError, DownloadErrorCode, MediaMaterializer, UrllibHttpTransport,
)
from engine.dubflow.download.source_adapter import MediaCandidate


@contextmanager
def endpoint(handle):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            handle(self)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/media.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class HttpTransportTests(unittest.TestCase):
    def test_actual_killed_downloader_resumes_fsynced_prefix_over_http(self):
        chunk = 1024 * 1024
        committed = 17 * chunk  # First chunk plus the next16 MiB checkpoint.
        stopped_at = 18 * chunk  # Actual uncommitted tail after that checkpoint.
        payload = bytes(range(256)) * (20 * chunk // 256)
        released = threading.Event()
        calls = []

        def handle(request):
            range_value = request.headers.get("Range")
            calls.append((range_value, request.headers.get("If-Range")))
            start = int(range_value.removeprefix("bytes=").removesuffix("-")) if range_value else 0
            request.send_response(206 if range_value else 200)
            request.send_header("Content-Length", str(len(payload) - start))
            request.send_header("ETag", '"fixture-version-1"')
            if range_value:
                request.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
            request.end_headers()
            try:
                if not range_value:
                    request.wfile.write(payload[:stopped_at])
                    request.wfile.flush()
                    released.wait(15)
                    start = stopped_at
                request.wfile.write(payload[start:])
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected after the actual owning process was killed.

        with endpoint(handle) as url, tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.write_bytes(b"previous-good-export")
            repo = Path(__file__).resolve().parents[2]
            code = "\n".join((
                "import sys; from pathlib import Path",
                "sys.path.insert(0, " + repr(str(repo)) + ")",
                "from engine.dubflow.download.materializer import MediaMaterializer",
                "from engine.dubflow.download.source_adapter import MediaCandidate",
                "def progress(done, total):",
                f"    if done == {stopped_at}: print('COMMITTED', flush=True)",
                f"MediaMaterializer(chunk_bytes={chunk}).download(MediaCandidate('media',sys.argv[1],'progressive','video/mp4'),Path(sys.argv[2]),progress=progress)",
            ))
            process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, url, str(destination)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            observed = queue.Queue(maxsize=1)
            reader = threading.Thread(target=lambda: observed.put(process.stdout.readline()), daemon=True)
            reader.start()
            try:
                self.assertEqual(observed.get(timeout=10), "COMMITTED\n")
                self.assertIsNone(process.poll())
                part = destination.with_name("media.mp4.part")
                receipt = part.with_name(part.name + ".resume.json")
                checkpoint = json.loads(receipt.read_text())
                self.assertEqual(checkpoint["size_bytes"], committed)
                self.assertEqual(checkpoint["sha256"], hashlib.sha256(payload[:committed]).hexdigest())
                process.kill()  # No finally, cooperative cancel or injected receipt.
                process.wait(timeout=10)
                self.assertNotEqual(process.returncode, 0)
                released.set()
                self.assertEqual(part.read_bytes(), payload[:stopped_at])
                self.assertEqual(json.loads(receipt.read_text()), checkpoint)
                self.assertEqual(destination.read_bytes(), b"previous-good-export")
                result = MediaMaterializer().download(MediaCandidate("media", url, "progressive", "video/mp4"), destination)
                self.assertTrue(result.resumed)
                self.assertEqual(calls, [(None, None), (f"bytes={committed}-", '"fixture-version-1"')])
                self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
                self.assertFalse(receipt.exists())
            finally:
                released.set()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                reader.join(timeout=2)
                process.stdout.close()
                process.stderr.close()

    def test_real_range_refusal_restarts_once_and_verifies_publication(self):
        payload = b"complete-current-object"
        calls = []

        def handle(request):
            calls.append(request.headers.get("Range"))
            refused = bool(calls[-1])
            body = b"range refused; never media" if refused else payload
            request.send_response(416 if refused else 200)
            request.send_header("Content-Length", str(len(body)))
            request.end_headers()
            request.wfile.write(body)

        with endpoint(handle) as url, tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.write_bytes(b"previous-good-export")
            destination.with_name("media.mp4.part").write_bytes(b"stale")
            result = MediaMaterializer().download(
                MediaCandidate("media", url, "progressive", "video/mp4"), destination,
                expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
            )
            self.assertEqual(calls, ["bytes=5-", None])
            self.assertFalse(result.resumed)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())

    def test_repeated_range_refusal_is_bounded_and_preserves_old_export(self):
        calls = []

        def handle(request):
            calls.append(request.headers.get("Range"))
            request.send_response(416)
            request.send_header("Content-Length", "0")
            request.end_headers()

        with endpoint(handle) as url, tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "media.mp4"
            destination.write_bytes(b"previous-good-export")
            partial = destination.with_name("media.mp4.part")
            partial.write_bytes(b"stale")
            with self.assertRaises(DownloadError) as context:
                MediaMaterializer().download(
                    MediaCandidate("media", url, "progressive", "video/mp4"), destination,
                    expected_sha256="0" * 64,
                )
            self.assertEqual(context.exception.code, DownloadErrorCode.SOURCE_CHANGED)
            self.assertEqual(calls, ["bytes=5-", None])
            self.assertEqual(destination.read_bytes(), b"previous-good-export")
            self.assertEqual(partial.read_bytes(), b"stale")

    def test_real_redirect_to_same_host_different_port_drops_credentials(self):
        captured = []

        def target(request):
            captured.append(dict(request.headers.items()))
            request.send_response(200)
            request.send_header("Content-Length", "2")
            request.end_headers()
            request.wfile.write(b"ok")

        with endpoint(target) as target_url:
            def redirect(request):
                request.send_response(302)
                request.send_header("Location", target_url)
                request.send_header("Content-Length", "0")
                request.end_headers()

            with endpoint(redirect) as source_url:
                response = UrllibHttpTransport().open(source_url, headers={
                    "Authorization": "Bearer private", "Cookie": "session=private",
                    "Proxy-Authorization": "Basic private", "Accept-Encoding": "identity",
                })
                try:
                    self.assertEqual(response.body.read(), b"ok")
                finally:
                    response.close()
        headers = {key.lower(): value for key, value in captured[0].items()}
        for name in ("authorization", "cookie", "proxy-authorization"):
            self.assertNotIn(name, headers)
        self.assertEqual(headers["accept-encoding"], "identity")

    def test_redirect_origin_handles_downgrade_and_default_ports(self):
        for destination, preserved in (
            ("http://same.test/media", False),
            ("https://same.test:444/media", False),
            ("https://other.test/media", False),
            ("https://same.test:443/media", True),
            ("https://same.test/other", True),
        ):
            with self.subTest(destination=destination):
                captured = []
                redirect_body = BytesIO()
                final_body = BytesIO(b"ok")
                final_body.headers = {}
                final_body.status = 200
                final_body.getcode = lambda: 200

                class Opener:
                    def open(self, request, timeout):
                        captured.append(dict(request.header_items()))
                        if len(captured) == 1:
                            raise HTTPError(request.full_url, 302, "redirect", {"Location": destination}, redirect_body)
                        return final_body

                transport = UrllibHttpTransport()
                transport._opener = Opener()
                result = transport.open("https://same.test/media", headers={"Cookie": "secret"})
                result.close()
                self.assertTrue(redirect_body.closed)
                headers = {key.lower(): value for key, value in captured[1].items()}
                self.assertEqual("cookie" in headers, preserved)

    def test_typed_http_errors_close_response_without_token_in_diagnostics(self):
        for status, code in (
            (401, DownloadErrorCode.AUTH_REQUIRED), (403, DownloadErrorCode.AUTH_REQUIRED),
            (429, DownloadErrorCode.RATE_LIMITED), (404, DownloadErrorCode.SOURCE_CHANGED),
            (500, DownloadErrorCode.NETWORK),
        ):
            with self.subTest(status=status):
                body = BytesIO(b"untrusted error page")

                class Opener:
                    def open(self, request, timeout):
                        raise HTTPError(request.full_url, status, "failure", {}, body)

                transport = UrllibHttpTransport()
                transport._opener = Opener()
                with self.assertRaises(DownloadError) as context:
                    transport.open("https://same.test/media?token=private")
                self.assertEqual(context.exception.code, code)
                self.assertTrue(body.closed)
                self.assertNotIn("private", str(context.exception))


if __name__ == "__main__":
    unittest.main()
