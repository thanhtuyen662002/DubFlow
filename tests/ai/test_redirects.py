"""Credentials never follow a redirect to another origin (scheme, host, port)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.request import Request

from engine.dubflow.ai import client
from engine.dubflow.ai.client import AiError, ask_json, make_route

CREDENTIALS = {"authorization": "Bearer test-only-1", "x-api-key": "test-only-2", "x-goog-api-key": "test-only-3"}


class Server:
    def __init__(self, behaviour):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def handle_any(self):
                outer.seen.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
                status, location, body = behaviour(self.path)
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                data = json.dumps(body).encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.other = Server(lambda path: (200, None, {"data": [{"id": "stolen"}]}))
        self.addCleanup(self.other.close)

    def test_cross_origin_redirects_never_receive_any_credential_header(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                first = Server(lambda path, status=status: (status, self.other.url + "/v1/models", {}))
                self.addCleanup(first.close)
                request = Request(first.url + "/v1/models", headers=CREDENTIALS)
                code, _, _ = client._urllib(request, 5)
                self.assertEqual(code, status)
                self.assertEqual(self.other.seen, [])

    def test_a_same_origin_redirect_is_followed(self):
        def behaviour(path):
            return (308, "/v1/models-moved", {}) if path == "/v1/models" else (200, None, {"data": [{"id": "m"}]})
        first = Server(behaviour)
        self.addCleanup(first.close)
        code, _, body = client._urllib(Request(first.url + "/v1/models", headers=CREDENTIALS), 5)
        self.assertEqual((code, json.loads(body)), (200, {"data": [{"id": "m"}]}))
        self.assertEqual(first.seen[1][1]["x-api-key"], "test-only-2")

    def test_redirect_loops_are_bounded(self):
        first = Server(lambda path: (302, "/again", {}))
        self.addCleanup(first.close)
        code, _, _ = client._urllib(Request(first.url + "/start", headers=CREDENTIALS), 5)
        self.assertEqual(code, 302)
        self.assertLessEqual(len(first.seen), client.MAX_REDIRECTS + 1)

    def test_a_custom_server_redirecting_elsewhere_is_a_clear_error(self):
        first = Server(lambda path: (307, self.other.url + "/v1/chat/completions", {}))
        self.addCleanup(first.close)
        route = make_route("custom", "test-only-key-0000", model="m", base_url=first.url.replace("127.0.0.1", "localhost") + "/v1")
        with self.assertRaises(AiError) as caught:
            ask_json(route, "s", {"type": "object"}, [{"text": "t"}])
        self.assertEqual(caught.exception.code, "INVALID")
        self.assertIn("chuyển hướng", caught.exception.message)
        self.assertEqual(self.other.seen, [])


if __name__ == "__main__":
    unittest.main()
