"""#884: the yt-dlp helper process checks the peer of every connection it makes.

The guard is installed in a child interpreter (it patches ``socket`` process-wide), exactly as the helper does before
importing the SDK. Loopback test servers stand in for private targets. 127.0.0.1 is treated as "public" only through
the guard's test seam, so a redirect or a rebinding to 127.0.0.2 / ::1 is the private hop.

The SDK parts need the pinned yt-dlp and ``curl_cffi`` runtime. ``DUBFLOW_TEST_SDK_LIB`` names a folder holding them;
the required CI lane provisions it from hash-pinned wheels (``run_sdk_egress.py``), and without it they skip.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import unittest

from engine.dubflow.download import authenticated_native as native

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "engine" / "dubflow" / "download" / "authenticated_native.py"
SDK_LIB = os.environ.get("DUBFLOW_TEST_SDK_LIB", "")


@contextmanager
def server(host="127.0.0.1", redirect_to=None):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer((host, 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], hits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def run_guarded(body: str, *, allowed=(), public_ips=("127.0.0.1",), extra_path: str = "", proxied=None,
                names=None) -> dict:
    """Run ``body`` (which sets ``result``) in a child with the helper's guard installed; returns ``result``."""
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(HELPER.parent)!r})
        {"sys.path.insert(0, " + repr(extra_path) + ")" if extra_path else ""}
        import authenticated_native as native
        public_ips = set({list(public_ips)!r})
        names = {names!r}
        def resolve(host, port):
            if names is None or host not in names:
                raise OSError("no such name")
            return names[host]
        native.install_egress_guard({list(allowed)!r}, proxied={proxied!r}, public=lambda ip: ip in public_ips,
                                    resolve=resolve)
        result = {{}}
        try:
    """) + textwrap.indent(textwrap.dedent(body), "    ") + textwrap.dedent("""
        except BaseException as error:
            result["error"] = type(error).__name__
            result["error_text"] = str(error)[:300]
        result["refused"] = list(native.EGRESS_REFUSED)
        result["exit_code"] = native.failure_exit_code(RuntimeError("x")) if native.EGRESS_REFUSED else None
        print(json.dumps(result))
    """)
    done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script], capture_output=True, text=True, timeout=60,
                          env={key: value for key, value in os.environ.items() if "proxy" not in key.lower()})
    if done.returncode != 0 or not done.stdout.strip():
        raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


class AddressTests(unittest.TestCase):
    def test_public_address_refuses_every_non_global_form_including_fake_ip(self):
        for value in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                      "198.18.0.7", "0.0.0.0", "224.0.0.1", "::1", "fe80::1%2", "fd00::1", "::ffff:127.0.0.1",
                      "::ffff:192.168.0.1", "2002:c0a8:101::1", "64:ff9b::a00:1", "64:ff9b::7f00:1",
                      "64:ff9b::a9fe:a9fe", "64:ff9b:1::a00:1", "not-an-ip"):
            with self.subTest(value=value):
                self.assertFalse(native.public_address(value))
        for value in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808"):
            with self.subTest(value=value):
                self.assertTrue(native.public_address(value))

    def test_proxy_endpoints_are_parsed_from_the_sdk_proxy_mapping(self):
        found = native.proxy_endpoints({"http": "http://127.0.0.1:7890", "https": "127.0.0.1:7891",
                                        "all": "socks5://127.0.0.1", "no": "localhost,::1",
                                        "global_agent_no": "localhost,::1,127.0.0.0/8", "electron_get_use": "1"})
        self.assertEqual(found, {("127.0.0.1", 7890), ("127.0.0.1", 7891), ("127.0.0.1", 1080)})

    def test_refusal_maps_to_exit_8_and_the_parent_maps_8_to_invalid_input(self):
        from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport  # noqa: F401
        source = (ROOT / "engine/dubflow/download/authenticated.py").read_text(encoding="utf-8")
        self.assertIn("8: SourceErrorCode.INVALID_INPUT", source)
        self.assertEqual(native.EgressRefused.native_exit_code, 8)


class SocketGuardTests(unittest.TestCase):
    def test_urllib_to_a_private_peer_is_refused_before_any_byte(self):
        with server("127.0.0.1") as (port, hits):
            result = run_guarded(f"""
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:{port}/x", timeout=5).read()
            """, public_ips=())
        self.assertEqual(hits, [])
        self.assertEqual(result["exit_code"], 8)
        self.assertIn("socket", result["refused"])

    def test_a_public_peer_is_reached(self):
        with server("127.0.0.1") as (port, hits):
            result = run_guarded(f"""
                import urllib.request
                result["body"] = urllib.request.urlopen("http://127.0.0.1:{port}/x", timeout=5).read().decode()
            """)
        self.assertEqual(result.get("body"), "ok")
        self.assertEqual(hits, ["/x"])
        self.assertEqual(result["refused"], [])

    def test_redirect_to_a_private_address_is_refused(self):
        with server("127.0.0.2") as (private_port, private_hits):
            with server("127.0.0.1", redirect_to=f"http://127.0.0.2:{private_port}/admin") as (port, hits):
                result = run_guarded(f"""
                    import urllib.request
                    urllib.request.urlopen("http://127.0.0.1:{port}/start", timeout=5).read()
                """)
        self.assertEqual(hits, ["/start"])
        self.assertEqual(private_hits, [])
        self.assertEqual(result["exit_code"], 8)

    def test_dns_rebinding_at_connect_time_is_refused(self):
        with server("127.0.0.2") as (port, hits):
            result = run_guarded(f"""
                import socket
                answers = iter([[("127.0.0.1",)], [("127.0.0.2",)]])
                real = socket.getaddrinfo
                def rebinding(host, port, *args, **kwargs):
                    if host != "rebind.example.test":
                        return real(host, port, *args, **kwargs)
                    (ip,) = next(answers)[0]
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
                socket.getaddrinfo = rebinding
                rebinding("rebind.example.test", {port})  # the first answer: "public"
                socket.create_connection(("rebind.example.test", {port}), timeout=5)
            """)
        self.assertEqual(hits, [])
        self.assertEqual(result["exit_code"], 8)

    def test_a_name_handed_straight_to_connect_is_resolved_and_checked(self):
        with server("127.0.0.1") as (port, hits):
            result = run_guarded(f"""
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(("localhost", {port}))
            """, public_ips=())
        self.assertEqual(result["exit_code"], 8)

    def test_ipv6_loopback_and_mapped_loopback_are_refused_before_connecting(self):
        import socket
        try:
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()
        except OSError:
            self.skipTest("no IPv6 sockets on this machine")
        result = run_guarded("""
            import socket
            for address in ("::1", "::ffff:127.0.0.1", "fe80::1"):
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                try:
                    sock.connect((address, 9))
                except PermissionError:
                    result.setdefault("refused_v6", []).append(address)
                except OSError:
                    result.setdefault("not_refused", []).append(address)
                finally:
                    sock.close()
        """, public_ips=())
        self.assertEqual(result.get("refused_v6"), ["::1", "::ffff:127.0.0.1", "fe80::1"])

    def test_configured_proxy_endpoint_is_the_only_private_peer_allowed(self):
        with server("127.0.0.2") as (port, hits):
            result = run_guarded(f"""
                import socket
                socket.create_connection(("127.0.0.2", {port}), timeout=5).close()
                result["proxy_ok"] = True
                socket.create_connection(("127.0.0.2", {port + 1 if port < 65535 else port - 1}), timeout=5)
            """, allowed=[("127.0.0.2", port)])
        self.assertTrue(result.get("proxy_ok"))
        self.assertEqual(result["exit_code"], 8)


@contextmanager
def listener(host="127.0.0.1"):
    """A bare listening socket: a TCP handshake completes in the kernel even without accept(), so a later accept()
    that times out proves no connection was ever made."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    sock.listen(8)
    try:
        yield sock
    finally:
        sock.close()


def never_connected(sock) -> bool:
    import socket
    sock.settimeout(0.5)
    try:
        conn, _ = sock.accept()
    except socket.timeout:
        return True
    conn.close()
    return False


@contextmanager
def redirecting_proxy(location):
    """A forward HTTP proxy on 127.0.0.2 that answers every request with a redirect to ``location``."""
    with server("127.0.0.2", redirect_to=location) as (port, hits):
        yield port, hits


def _curl_available() -> bool:
    if not SDK_LIB:
        return False
    probe = subprocess.run([sys.executable, "-I", "-S", "-c", f"import sys; sys.path.insert(0, {SDK_LIB!r}); import curl_cffi"],
                           capture_output=True)
    return probe.returncode == 0


@unittest.skipUnless(SDK_LIB and (Path(SDK_LIB) / "yt_dlp").is_dir(), "pinned yt-dlp not available (set DUBFLOW_TEST_SDK_LIB)")
class HelperProcessTests(unittest.TestCase):
    """The real helper with the real SDK: a generic link to a private peer exits 8 and the peer sees nothing."""

    def helper(self, request: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(HELPER), SDK_LIB], input=json.dumps(request).encode(),
                              capture_output=True, timeout=120,
                              env={key: value for key, value in os.environ.items() if "proxy" not in key.lower()})

    def test_generic_inspection_of_a_private_peer_exits_8(self):
        with server("127.0.0.1") as (port, hits):
            for url in (f"http://127.0.0.1:{port}/video.mp4", f"http://localhost:{port}/watch"):
                done = self.helper({"schema_version": 1, "provider_id": "generic", "url": url})
                with self.subTest(url=url):
                    self.assertEqual(done.returncode, 8, done.stderr[-500:])
        self.assertEqual(hits, [])

    def test_behind_a_proxy_a_private_target_exits_8_and_the_proxy_sees_nothing(self):
        # the proxy's own private endpoint is allowed as a peer, never as a target (direct, no_proxy or redirect)
        with server("127.0.0.2") as (proxy_port, proxy_hits):
            for url in ("http://127.0.0.1:9/video.mp4", f"http://127.0.0.2:{proxy_port}/admin"):
                env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
                env.update(http_proxy=f"http://127.0.0.2:{proxy_port}", https_proxy=f"http://127.0.0.2:{proxy_port}")
                done = subprocess.run([sys.executable, "-I", "-S", "-B", str(HELPER), SDK_LIB],
                                      input=json.dumps({"schema_version": 1, "provider_id": "generic", "url": url}).encode(),
                                      capture_output=True, timeout=120, env=env)
                with self.subTest(url=url):
                    self.assertEqual(done.returncode, 8, done.stderr[-500:])
        self.assertEqual(proxy_hits, [])

    def test_health_check_still_works_with_the_guard_installed(self):
        done = self.helper({"schema_version": 1, "operation": "health_check"})
        self.assertEqual(done.returncode, 0, done.stderr[-500:])


@unittest.skipUnless(SDK_LIB and (Path(SDK_LIB) / "yt_dlp").is_dir(), "pinned yt-dlp not available (set DUBFLOW_TEST_SDK_LIB)")
class ProxyTargetTests(unittest.TestCase):
    """Behind a proxy the peer is the proxy, so the SDK's request targets and urllib redirects are checked by name."""

    def fetch(self, url: str, proxy_port: int, names: dict) -> dict:
        return run_guarded(f"""
            import yt_dlp
            native.guard_sdk_targets()
            with yt_dlp.YoutubeDL({{"quiet": True, "no_warnings": True, "proxy": "http://127.0.0.2:{proxy_port}",
                                    "retries": 0, "socket_timeout": 5}}) as ydl:
                from yt_dlp.networking import Request
                ydl.urlopen(Request({url!r})).read()
                result["fetched"] = True
        """, extra_path=SDK_LIB, allowed=[("127.0.0.2", proxy_port)], public_ips=("93.184.216.34",), names=names)

    def test_proxy_redirect_to_a_private_target_is_not_followed(self):
        with redirecting_proxy("http://10.0.0.1/admin") as (port, hits):
            result = self.fetch("http://media.example.test/x", port, {"media.example.test": ["93.184.216.34"]})
        self.assertEqual(hits, ["http://media.example.test/x"])  # the first hop only; the redirect never went out
        self.assertIn("target", result["refused"])
        self.assertEqual(result["exit_code"], 8)

    def test_public_target_through_the_proxy_works(self):
        with server("127.0.0.2") as (port, hits):
            result = self.fetch("http://media.example.test/x", port, {"media.example.test": ["93.184.216.34"]})
        self.assertTrue(result.get("fetched"), result)
        self.assertEqual(hits, ["http://media.example.test/x"])

    def test_private_or_unresolvable_targets_never_reach_the_proxy(self):
        # a private name (no_proxy or not), the proxy endpoint itself, a NAT64 private literal and a name local DNS
        # cannot resolve (fail closed) are all refused before any byte goes to the proxy
        with server("127.0.0.2") as (port, hits):
            for url in ("http://intranet.example.test/x", f"http://127.0.0.2:{port}/admin", "http://[64:ff9b::a00:1]/x",
                        "http://nowhere.example.test/x"):
                result = self.fetch(url, port, {"intranet.example.test": ["10.0.0.5"]})
                with self.subTest(url=url):
                    self.assertIn("target", result["refused"])
        self.assertEqual(hits, [])


@unittest.skipUnless(_curl_available(), "curl_cffi runtime not available (set DUBFLOW_TEST_SDK_LIB)")
class CurlGuardTests(unittest.TestCase):
    def curl(self, url: str, **kwargs) -> dict:
        return run_guarded(f"""
            from curl_cffi import requests
            response = requests.get({url!r}, timeout=5, impersonate="chrome", max_redirects=5)
            result["status"] = response.status_code
        """, extra_path=SDK_LIB, **kwargs)

    def test_impersonated_request_to_a_private_peer_never_connects(self):
        # OPENSOCKETFUNCTION refuses before the socket exists: the private peer never sees a TCP handshake
        with listener("127.0.0.1") as sock:
            port = sock.getsockname()[1]
            result = self.curl(f"http://127.0.0.1:{port}/x", public_ips=())
            self.assertTrue(never_connected(sock))
        self.assertEqual(result["exit_code"], 8)
        self.assertIn("curl", result["refused"])

    def test_impersonated_request_to_a_public_peer_works(self):
        with server("127.0.0.1") as (port, hits):
            result = self.curl(f"http://127.0.0.1:{port}/x")
        self.assertEqual(result.get("status"), 200)
        self.assertEqual(hits, ["/x"])

    def test_libcurl_redirect_to_a_private_address_never_connects(self):
        with listener("127.0.0.2") as private:
            private_port = private.getsockname()[1]
            with server("127.0.0.1", redirect_to=f"http://127.0.0.2:{private_port}/admin") as (port, hits):
                result = self.curl(f"http://127.0.0.1:{port}/start")
            self.assertTrue(never_connected(private))
        self.assertEqual(hits, ["/start"])
        self.assertEqual(result["exit_code"], 8)

    def test_behind_a_proxy_curl_fails_closed(self):
        # libcurl follows redirects inside C, where the target check cannot see them: no impersonation behind a proxy
        with server("127.0.0.2") as (proxy_port, proxy_hits):
            result = run_guarded(f"""
                from curl_cffi import requests
                requests.get("http://media.example.test/x", proxy="http://127.0.0.2:{proxy_port}", timeout=5,
                             impersonate="chrome")
            """, extra_path=SDK_LIB, allowed=[("127.0.0.2", proxy_port)], public_ips=("93.184.216.34",),
                names={"media.example.test": ["93.184.216.34"]})
        self.assertEqual(proxy_hits, [])
        self.assertIn("curl-proxied", result["refused"])

    def test_curl_without_the_hook_fails_closed(self):
        with server("127.0.0.1") as (port, hits):
            result = run_guarded(f"""
                from curl_cffi import Curl, CurlOpt
                curl = Curl()
                curl._dubflow_unguarded = True  # as if OPENSOCKETFUNCTION could not be installed
                curl.setopt(CurlOpt.URL, b"http://127.0.0.1:{port}/x")
                curl.perform()
            """, extra_path=SDK_LIB)
        self.assertEqual(hits, [])
        self.assertIn("curl-unguarded", result["refused"])


if __name__ == "__main__":
    unittest.main()
