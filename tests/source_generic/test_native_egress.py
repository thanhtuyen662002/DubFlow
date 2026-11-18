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
        self.assertIn("9: SourceErrorCode.PROXY_UNSUPPORTED", source)
        self.assertEqual(native.EgressRefused.native_exit_code, 8)
        self.assertEqual(native.ProxiedEgressRefused.native_exit_code, 9)

    def test_only_the_proxy_refusal_maps_to_exit_9(self):
        # an SDK error that happens to carry native_exit_code 9 must not pass for the proxy refusal
        saved = list(native.EGRESS_REFUSED)
        try:
            native.EGRESS_REFUSED[:] = ["proxied"]
            self.assertEqual(native.failure_exit_code(RuntimeError("x")), 9)
            native.EGRESS_REFUSED[:] = ["socket"]
            self.assertEqual(native.failure_exit_code(RuntimeError("x")), 8)
            native.EGRESS_REFUSED[:] = ["socket", "proxied"]
            self.assertEqual(native.failure_exit_code(RuntimeError("x")), 8)
            native.EGRESS_REFUSED[:] = []
            stray = RuntimeError("unable to extract")
            stray.native_exit_code = 9
            self.assertNotEqual(native.failure_exit_code(stray), 9)
            self.assertEqual(native.failure_exit_code(native.ProxiedEgressRefused(13, "x")), 5)
        finally:
            native.EGRESS_REFUSED[:] = saved

    def test_sdk_options_never_carry_a_proxy(self):
        # The refusal reads getproxies(), the same place the SDK takes its proxies from, only while no proxy option is
        # ever passed to the SDK.
        seen = []

        class Recording:
            def __init__(self, options):
                seen.append(options)
                self.cookiejar = type("Jar", (), {"set_cookie": lambda self, cookie: None})()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                return {"_type": "playlist", "entries": []} if seen[-1].get("extract_flat") else {"id": "x", "formats": []}

        native.inspect({"provider_id": "generic", "url": "https://media.example.test/x"}, Recording)
        native.enumerate_page({"provider_id": "generic", "url": "https://media.example.test/list", "offset": 0,
                               "page_size": 5}, Recording)
        self.assertEqual(len(seen), 2)
        for options in (native.downloader_options(), *seen):
            self.assertFalse({"proxy", "geo_verification_proxy"} & set(options), options)


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


def _no_proxy_env() -> dict:
    return {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}


# Every network operation the helper accepts, as the parent sends it. The refusal happens before any request field is
# read beyond the operation, so these need not be complete.
NETWORK_REQUESTS = {
    "generic inspect": {"schema_version": 1, "provider_id": "generic", "url": "http://93.184.216.34/watch"},
    "bilibili inspect": {"schema_version": 1, "provider_id": "bilibili", "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                         "headers": {}},
    "douyin inspect": {"schema_version": 1, "provider_id": "douyin", "url": "https://www.douyin.com/video/7300000000000000000",
                       "headers": {}},
    "enumerate": {"schema_version": 1, "operation": "enumerate", "provider_id": "generic",
                  "url": "http://93.184.216.34/list", "offset": 0, "page_size": 5, "headers": {}},
    "douyin_creator": {"schema_version": 1, "operation": "douyin_creator", "provider_id": "douyin",
                       "url": "https://www.douyin.com/user/MS4wLjABAAAA", "position": 0, "page_size": 5, "headers": {},
                       "douyin_modules": {"creator": "0" * 64, "signature": "0" * 64}},
}


class ProxiedRefusalTests(unittest.TestCase):
    """#884 interim, SDK-free: behind a configured proxy the real helper refuses every network operation before the SDK
    is imported. The SDK folder holds a poison ``yt_dlp`` that exits 42 on import, so reaching the SDK shows as 42."""

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory(prefix="dubflow-poison-sdk-")
        self.addCleanup(holder.cleanup)
        self.sdk = Path(holder.name)
        (self.sdk / "yt_dlp").mkdir()
        (self.sdk / "yt_dlp" / "__init__.py").write_text("raise SystemExit(42)\n", encoding="utf-8")

    def helper(self, request: dict, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(HELPER), str(self.sdk)],
                              input=json.dumps(request).encode(), capture_output=True, timeout=60, env=env)

    def test_every_network_operation_behind_a_proxy_exits_9_before_any_request(self):
        with listener("127.0.0.2") as proxy:
            port = proxy.getsockname()[1]
            sources = {
                "HTTPS_PROXY": {"HTTPS_PROXY": f"http://user:synthetic-secret@127.0.0.2:{port}"},
                "http_proxy only": {"http_proxy": f"http://127.0.0.2:{port}"},
                "ALL_PROXY socks5h": {"ALL_PROXY": f"socks5h://127.0.0.2:{port}"},
                # NO_PROXY never lifts the refusal: a bypass list does not make the proxy's peer checkable, and a
                # target on it must not become a way past the refusal either (#884).
                "NO_PROXY *": {"HTTPS_PROXY": f"http://127.0.0.2:{port}", "HTTP_PROXY": f"http://127.0.0.2:{port}",
                               "NO_PROXY": "*", "no_proxy": "*"},
                "no_proxy lists the targets": {"HTTPS_PROXY": f"http://127.0.0.2:{port}", "HTTP_PROXY": f"http://127.0.0.2:{port}",
                                               "no_proxy": "93.184.216.34,www.bilibili.com,www.douyin.com",
                                               "NO_PROXY": "93.184.216.34,www.bilibili.com,www.douyin.com"},
            }
            for source, variables in sources.items():
                for name, request in NETWORK_REQUESTS.items():
                    done = self.helper(request, {**_no_proxy_env(), **variables})
                    with self.subTest(source=source, operation=name):
                        stderr = done.stderr.decode("utf-8", "replace")
                        self.assertEqual(done.returncode, 9, stderr[-500:])
                        self.assertEqual(done.stdout, b"")
                        self.assertIn("ProxiedEgressRefused", stderr)
                        self.assertNotIn("synthetic-secret", stderr)
            self.assertTrue(never_connected(proxy))

    def test_a_system_proxy_from_getproxies_is_refused_the_same_way(self):
        # Windows registry and macOS settings reach the helper only through urllib.request.getproxies(), which Linux
        # CI cannot configure; patch that call in a child and run the real main()
        with listener("127.0.0.2") as proxy:
            address = f"http://127.0.0.2:{proxy.getsockname()[1]}"
            script = textwrap.dedent(f"""
                import sys, urllib.request
                sys.path.insert(0, {str(HELPER.parent)!r})
                import authenticated_native as native
                urllib.request.getproxies = lambda: {{"https": {address!r}}}
                sys.argv = [{str(HELPER)!r}, {str(self.sdk)!r}]
                raise SystemExit(native.main())
            """)
            done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script], capture_output=True, timeout=60,
                                  input=json.dumps(NETWORK_REQUESTS["generic inspect"]).encode(), env=_no_proxy_env())
            self.assertEqual(done.returncode, 9, done.stderr[-500:])
            self.assertEqual(done.stdout, b"")
            self.assertTrue(never_connected(proxy))

    def test_without_a_proxy_the_helper_reaches_the_sdk_as_before(self):
        # Only http, https, all and ftp proxies count: stray *_proxy variables and NO_PROXY change nothing
        stray = {"GLOBAL_AGENT_HTTPS_PROXY": "http://127.0.0.2:1", "npm_config_https_proxy": "http://127.0.0.2:1",
                 "ELECTRON_GET_USE_PROXY": "1", "NO_PROXY": "*"}
        for label, variables in (("no proxy", {}), ("stray variables", stray)):
            for name, request in NETWORK_REQUESTS.items():
                done = self.helper(request, {**_no_proxy_env(), **variables})
                with self.subTest(env=label, operation=name):
                    self.assertEqual(done.returncode, 42, done.stderr[-500:])

    def test_health_check_behind_a_proxy_is_not_refused(self):
        with listener("127.0.0.2") as proxy:
            env = {**_no_proxy_env(), "https_proxy": f"http://127.0.0.2:{proxy.getsockname()[1]}"}
            done = self.helper({"schema_version": 1, "operation": "health_check"}, env)
            self.assertEqual(done.returncode, 42, done.stderr[-500:])  # it went on to import the SDK
            self.assertTrue(never_connected(proxy))


def _curl_available() -> bool:
    if not SDK_LIB:
        return False
    probe = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", f"import sys; sys.path.insert(0, {SDK_LIB!r}); import curl_cffi"],
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

    def test_behind_a_proxy_every_network_operation_exits_9_and_the_proxy_sees_nothing(self):
        # #884 interim: the proxy would resolve and reach the target itself, so a private target, the proxy's own
        # endpoint and a public target alike are refused before any request
        with server("127.0.0.2") as (proxy_port, proxy_hits):
            for url in ("http://127.0.0.1:9/video.mp4", f"http://127.0.0.2:{proxy_port}/admin",
                        "http://93.184.216.34/watch"):
                env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
                env.update(http_proxy=f"http://127.0.0.2:{proxy_port}", https_proxy=f"http://127.0.0.2:{proxy_port}")
                done = subprocess.run([sys.executable, "-I", "-S", "-B", str(HELPER), SDK_LIB],
                                      input=json.dumps({"schema_version": 1, "provider_id": "generic", "url": url}).encode(),
                                      capture_output=True, timeout=120, env=env)
                with self.subTest(url=url):
                    self.assertEqual(done.returncode, 9, done.stderr[-500:])
                    self.assertEqual(done.stdout, b"")
        self.assertEqual(proxy_hits, [])

    def test_health_check_still_works_with_the_guard_installed(self):
        done = self.helper({"schema_version": 1, "operation": "health_check"})
        self.assertEqual(done.returncode, 0, done.stderr[-500:])

    def test_health_check_behind_a_proxy_still_works_and_never_connects(self):
        # health_check makes no request, so it is the one operation still allowed behind a proxy
        with listener("127.0.0.2") as proxy:
            address = f"http://127.0.0.2:{proxy.getsockname()[1]}"
            env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
            env.update(http_proxy=address, https_proxy=address)
            done = subprocess.run([sys.executable, "-I", "-S", "-B", str(HELPER), SDK_LIB],
                                  input=json.dumps({"schema_version": 1, "operation": "health_check"}).encode(),
                                  capture_output=True, timeout=120, env=env)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])
            self.assertEqual(json.loads(done.stdout)["schema_version"], 1)
            self.assertTrue(never_connected(proxy))


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
