"""#490: user-entered URLs may only reach public Internet addresses.

Network-free except for loopback servers owned by the test. Resolvers are
injected, so no real DNS lookup happens.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from engine.dubflow.download import authenticated_native
from engine.dubflow.download import materializer as materializer_module
from engine.dubflow.download.generic import GenericUrlAdapter, YtDlpTransport
from engine.dubflow.download.generic.adapter import DirectUrlTransport
from engine.dubflow.download.generic.sdk import GenericSdkTransport
from engine.dubflow.download.materializer import (
    DownloadError, DownloadErrorCode, EgressPolicy, MediaMaterializer, UrllibHttpTransport, is_public_address,
)
from engine.dubflow.download.provider_transport import PublicProviderHttpTransport
from engine.dubflow.download.source_adapter import MediaCandidate, SourceError, SourceErrorCode


NON_PUBLIC = (
    "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254", "0.0.0.0", "100.64.0.1", "192.0.0.1", "198.18.0.7", "198.19.255.1", "224.0.0.1",
    "240.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "2002:7f00:1::1", "2002:a00:1::1",
    # RFC 6052 NAT64 (64:ff9b::/96) carries an IPv4 address; Python calls the IPv6 form global
    "64:ff9b::a00:1", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "64:ff9b::c612:7", "64:ff9b:1::a00:1",
    "not-an-address", "",
)
PUBLIC = ("93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "2001:4860:4860::8888", "::ffff:8.8.8.8",
          "64:ff9b::808:808")


class OnlyLoopback127(EgressPolicy):
    """Treat 127.0.0.1 as 'the public Internet' so a loopback server can play it."""

    def _allowed(self, address: str) -> bool:
        return address == "127.0.0.1" or super()._allowed(address)


@contextmanager
def server(handle):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            handle(self)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, hits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


PUBLIC_HEADERS = {"User-Agent": "Mozilla/5.0 recorded", "Accept": "*/*", "Accept-Language": "vi"}


@contextmanager
def proxy_listener():
    """A loopback 'proxy' that only records whether anything ever connected to it."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        yield f"http://user:synthetic-secret@127.0.0.1:{listener.getsockname()[1]}", listener
    finally:
        listener.close()


def never_connected(listener) -> bool:
    listener.settimeout(0.5)
    try:
        listener.accept()[0].close()
    except (socket.timeout, TimeoutError):
        return True
    return False


def proxy_env(proxy: str | None, **extra: str) -> dict[str, str]:
    """The test's own proxy settings only: inherited *_proxy variables are dropped (a developer's shell may set them)."""
    env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
    if proxy is not None:
        env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy, "https_proxy": proxy})
    env.update(extra)
    return env


def ok(request):
    request.send_response(200)
    request.send_header("Content-Type", "video/mp4")
    request.send_header("Content-Length", "2")
    request.end_headers()
    request.wfile.write(b"ok")


class AddressClassTests(unittest.TestCase):
    def test_non_global_addresses_are_refused(self):
        for value in NON_PUBLIC:
            with self.subTest(value=value):
                self.assertFalse(is_public_address(value))

    def test_global_unicast_addresses_are_accepted(self):
        for value in PUBLIC:
            with self.subTest(value=value):
                self.assertTrue(is_public_address(value))


class EgressPolicyTests(unittest.TestCase):
    def test_any_private_answer_refuses_the_whole_host(self):
        policy = EgressPolicy(resolver=lambda host, port: ["93.184.216.34", "10.0.0.7"])
        with self.assertRaises(DownloadError) as context:
            policy.check_url("https://mixed.example.test/video.mp4")
        self.assertEqual(context.exception.code, DownloadErrorCode.INVALID_SOURCE)
        self.assertFalse(context.exception.retryable)

    def test_resolution_failure_or_empty_answer_fails_closed_as_retryable_network(self):
        def broken(host, port):
            raise OSError("no such host")

        for resolver in (broken, lambda host, port: []):
            with self.subTest(resolver=resolver), self.assertRaises(DownloadError) as context:
                EgressPolicy(resolver=resolver).check_url("https://gone.example.test/")
            self.assertEqual(context.exception.code, DownloadErrorCode.NETWORK)

    def test_literal_private_hosts_are_refused_without_dns(self):
        for url in ("http://127.0.0.1:8080/x", "http://[::1]/x", "http://169.254.169.254/latest/meta-data",
                    "http://[::ffff:127.0.0.1]/x", "http://10.1.2.3/x", "http://localhost./x"):
            with self.subTest(url=url), self.assertRaises(DownloadError) as context:
                EgressPolicy().check_url(url)
            self.assertIn(context.exception.code, {DownloadErrorCode.INVALID_SOURCE, DownloadErrorCode.NETWORK})

    def test_connect_goes_to_the_validated_address_only_once_resolved(self):
        calls = []

        def rebinding(host, port):
            calls.append(host)
            return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

        sock = Mock()
        sock.getpeername.return_value = ("93.184.216.34", 443)
        with patch.object(materializer_module.socket, "create_connection", return_value=sock) as create:
            result = EgressPolicy(resolver=rebinding).connect(("cdn.example.test", 443), 5)
        self.assertIs(result, sock)
        self.assertEqual(calls, ["cdn.example.test"])
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0], ("93.184.216.34", 443))

    def test_peer_mismatch_closes_the_socket(self):
        sock = Mock()
        sock.getpeername.return_value = ("10.0.0.9", 443)
        with patch.object(materializer_module.socket, "create_connection", return_value=sock):
            with self.assertRaises(DownloadError):
                EgressPolicy(resolver=lambda host, port: ["93.184.216.34"]).connect(("cdn.example.test", 443), 5)
        sock.close.assert_called_once()


class HttpTransportEgressTests(unittest.TestCase):
    def test_proxied_request_is_refused_before_any_lookup_or_connection(self):
        # #884 interim: behind a user-configured proxy the proxy resolves and reaches the target itself, so a name
        # check here proves nothing. Every proxied request is refused with the typed, non-retryable code; the target
        # is never looked up and no connection is opened, whatever the name would have resolved to.
        lookups = []

        def recorded(answer):
            def resolver(host, port):
                lookups.append(host)
                if answer is None:
                    raise OSError("no local DNS")
                return [answer]
            return resolver

        for answer in (None, "93.184.216.34", "198.18.0.9", "192.168.0.10"):
            policy = EgressPolicy(resolver=recorded(answer))
            for handler, url in ((materializer_module._EgressHTTPHandler(policy), "http://cdn.example.test/x"),
                                 (materializer_module._EgressHTTPSHandler(policy), "https://cdn.example.test/x")):
                request = Request(url)
                request.set_proxy("user:synthetic-secret@127.0.0.1:9", request.type)
                with self.subTest(answer=answer, url=url), patch.object(handler, "do_open", return_value="opened") as do_open:
                    with self.assertRaises(DownloadError) as context:
                        getattr(handler, request.type + "_open")(request)
                    self.assertEqual(context.exception.code, DownloadErrorCode.PROXY_UNSUPPORTED)
                    self.assertFalse(context.exception.retryable)
                    self.assertEqual(context.exception.action, "disable_proxy")
                    self.assertNotIn("synthetic-secret", str(context.exception))
                    self.assertNotIn("127.0.0.1", str(context.exception))
                    do_open.assert_not_called()
        self.assertEqual(lookups, [])

    def test_media_requests_never_reach_a_configured_proxy(self):
        # #884: the Douyin page-details path skips the helper and fetches media through this transport. With a proxy
        # configured (as Clash "System Proxy" or HTTPS_PROXY does), nothing may reach the proxy: not the request,
        # not even a TCP handshake, and the target name is not looked up either.
        with proxy_listener() as (proxy, listener):
            with patch.dict(os.environ, proxy_env(proxy), clear=True):
                transport = PublicProviderHttpTransport("douyin", PUBLIC_HEADERS)
                materializer = MediaMaterializer(PublicProviderHttpTransport("douyin", PUBLIC_HEADERS))
            with patch.object(materializer_module.socket, "create_connection") as create, \
                    patch.object(materializer_module.socket, "getaddrinfo") as lookup:
                for url in ("http://93.184.216.34/aweme/v1/play/?video_id=1", "https://v3-web.douyinvod.com/720.mp4"):
                    with self.subTest(url=url), self.assertRaises(DownloadError) as context:
                        transport.open(url)
                    self.assertEqual(context.exception.code, DownloadErrorCode.PROXY_UNSUPPORTED)
                    self.assertFalse(context.exception.retryable)
                    self.assertNotIn("synthetic-secret", str(context.exception))
                with tempfile.TemporaryDirectory() as temp, self.assertRaises(DownloadError) as downloaded:
                    materializer.download(MediaCandidate("video", "https://v3-web.douyinvod.com/720.mp4", "progressive",
                                                         "video/mp4"), Path(temp) / "final.mp4", root=temp)
                self.assertEqual(downloaded.exception.code, DownloadErrorCode.PROXY_UNSUPPORTED)
                with self.assertRaises(SourceError) as probed:
                    materializer.probe_access(MediaCandidate("video", "https://v3-web.douyinvod.com/720.mp4",
                                                             "progressive", "video/mp4"))
                self.assertEqual(probed.exception.code, SourceErrorCode.PROXY_UNSUPPORTED)
                self.assertFalse(probed.exception.retryable)
                self.assertEqual(probed.exception.action, "disable_proxy")
            create.assert_not_called()
            lookup.assert_not_called()
            self.assertTrue(never_connected(listener))

    def test_no_proxy_cannot_turn_a_proxied_media_request_into_an_unchecked_one(self):
        # A host on NO_PROXY is fetched directly, which is the peer-bound path: the target is resolved here and only
        # a validated public address is connected. NO_PROXY never sends traffic through the proxy, so nothing reaches
        # it, and a private target is still refused.
        with proxy_listener() as (proxy, listener):
            with server(ok) as (port, hits):
                for no_proxy in ("*", "media.example.test,127.0.0.1,localhost"):
                    with patch.dict(os.environ, proxy_env(proxy, NO_PROXY=no_proxy, no_proxy=no_proxy), clear=True):
                        transport = UrllibHttpTransport(egress=OnlyLoopback127(resolver=lambda host, port: ["127.0.0.1"]))
                        private = UrllibHttpTransport()
                        with self.subTest(no_proxy=no_proxy):
                            response = transport.open(f"http://media.example.test:{port}/media.mp4")
                            try:
                                self.assertEqual(response.body.read(), b"ok")
                            finally:
                                response.close()
                            with self.assertRaises(DownloadError) as refused:
                                private.open(f"http://127.0.0.1:{port}/media.mp4")
                            self.assertEqual(refused.exception.code, DownloadErrorCode.INVALID_SOURCE)
            self.assertEqual(hits, ["/media.mp4", "/media.mp4"])
            self.assertTrue(never_connected(listener))

    def test_fake_ip_answer_is_refused_with_an_actionable_reason(self):
        # #884, Owner directive 2026-11-16: a proxy fake-IP address is never trusted by default; the refusal says why
        # and what the user can change (real-IP DNS, or excluding DubFlow from TUN). System-proxy mode is no longer
        # suggested: proxied source access is refused until an app-owned validating proxy exists (#884 interim).
        with self.assertRaises(DownloadError) as context:
            EgressPolicy(resolver=lambda host, port: ["198.18.0.9"]).resolve("www.youtube.com", 443)
        self.assertEqual(context.exception.code, DownloadErrorCode.INVALID_SOURCE)
        self.assertIn("fake-IP", str(context.exception))
        self.assertIn("real-IP DNS", str(context.exception))
        self.assertNotIn("system-proxy", str(context.exception))
        with self.assertRaises(DownloadError) as private:
            EgressPolicy(resolver=lambda host, port: ["10.0.0.1"]).resolve("intranet.example.test", 443)
        self.assertNotIn("fake-IP", str(private.exception))

    def test_default_transport_never_reaches_a_loopback_server(self):
        with server(ok) as (port, hits):
            for url in (f"http://127.0.0.1:{port}/media.mp4", f"http://localhost:{port}/media.mp4"):
                with self.subTest(url=url), self.assertRaises(DownloadError) as context:
                    UrllibHttpTransport().open(url)
                self.assertEqual(context.exception.code, DownloadErrorCode.INVALID_SOURCE)
                self.assertNotIn(str(port), str(context.exception))
            with self.assertRaises(SourceError) as probed:
                MediaMaterializer().probe_access(MediaCandidate("video", f"http://127.0.0.1:{port}/media.mp4", "progressive", "video/mp4"))
            self.assertFalse(probed.exception.retryable)
        self.assertEqual(hits, [])

    def test_public_host_is_fetched_through_the_validated_peer(self):
        with server(ok) as (port, hits):
            policy = OnlyLoopback127(resolver=lambda host, port: ["127.0.0.1"])
            response = UrllibHttpTransport(egress=policy).open(f"http://media.example.test:{port}/media.mp4")
            try:
                self.assertEqual(response.body.read(), b"ok")
            finally:
                response.close()
        self.assertEqual(hits, ["/media.mp4"])

    def test_redirect_to_a_private_target_is_refused_before_connecting(self):
        def resolver(host, port):
            return {"public.example.test": ["127.0.0.1"], "internal.example.test": ["10.20.30.40"]}[host]

        with server(lambda request: (request.send_response(302),
                                     request.send_header("Location", "http://internal.example.test/admin"),
                                     request.send_header("Content-Length", "0"), request.end_headers())) as (port, hits):
            with patch.object(materializer_module.socket, "create_connection", wraps=materializer_module.socket.create_connection) as create:
                with self.assertRaises(DownloadError) as context:
                    UrllibHttpTransport(egress=OnlyLoopback127(resolver=resolver)).open(f"http://public.example.test:{port}/start")
        self.assertEqual(context.exception.code, DownloadErrorCode.INVALID_SOURCE)
        self.assertEqual(hits, ["/start"])
        self.assertTrue(all(call.args[0][0] == "127.0.0.1" for call in create.call_args_list))


class GenericSourceEgressTests(unittest.TestCase):
    PRIVATE_URLS = ("http://127.0.0.1/watch", "http://[::1]/watch", "http://169.254.169.254/latest/meta-data",
                    "http://192.168.1.1/admin", "http://[fd00::1]/x")

    def setUp(self):
        # These cases are about the no-proxy path; a developer's own *_proxy variables must not decide them.
        environment = patch.dict(os.environ, proxy_env(None), clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_generic_link_behind_a_proxy_is_refused_before_any_local_lookup(self):
        # #884 interim: the helper would exit 9 on a configured proxy. The parent checks the same predicate first, so
        # a name that does not resolve here cannot turn the refusal into a retryable NETWORK error, and the target is
        # never looked up outside the proxy. NO_PROXY (even "*" or the target host itself) does not lift it.
        lookups = []

        def broken(host, port):
            lookups.append(host)
            raise OSError("no local DNS behind a proxy-only network")

        proxy = "http://user:synthetic-secret@127.0.0.1:9"
        environments = {
            "https": proxy_env(None, HTTPS_PROXY=proxy),
            "http": proxy_env(None, http_proxy=proxy),
            "all": proxy_env(None, ALL_PROXY="socks5://127.0.0.1:9"),
            "ftp": proxy_env(None, FTP_PROXY=proxy),
            "no_proxy *": proxy_env(proxy, NO_PROXY="*", no_proxy="*"),
            "no_proxy host": proxy_env(proxy, NO_PROXY="video.example.test,93.184.216.34",
                                       no_proxy="video.example.test,93.184.216.34"),
        }
        for name, environment in environments.items():
            sdk = Mock()
            sdk.pins = {"sdk": "fixture"}
            transport = GenericSdkTransport(sdk, egress=EgressPolicy(resolver=broken))
            with self.subTest(name), patch.dict(os.environ, environment, clear=True):
                self.assertTrue(authenticated_native.proxy_configured())
                for call in (lambda: transport.inspect_url("https://video.example.test/watch/1"),
                             lambda: transport.enumerate_playlist("https://video.example.test/list/1", cursor=None, page_size=5)):
                    with self.assertRaises(SourceError) as context:
                        call()
                    self.assertIs(context.exception.code, SourceErrorCode.PROXY_UNSUPPORTED)
                    self.assertFalse(context.exception.retryable)
                    self.assertEqual(context.exception.action, "disable_proxy")
                    self.assertNotIn("synthetic-secret", str(context.exception))
            sdk.inspect_url.assert_not_called()
            sdk.enumerate_url.assert_not_called()
        self.assertEqual(lookups, [])

    def test_the_parent_refuses_exactly_what_the_helper_refuses(self):
        # One predicate on both sides: stray *_proxy variables and a bare NO_PROXY are not proxies, so without a real
        # proxy the old resolve-time check still runs (and a failed lookup is still the retryable NETWORK error).
        def broken(host, port):
            raise OSError("no such host")

        for environment in (proxy_env(None), proxy_env(None, NO_PROXY="*"), proxy_env(None, SOCKS_PROXY="socks5://127.0.0.1:9")):
            sdk = Mock()
            with self.subTest(environment=sorted(set(environment) - set(proxy_env(None)))), \
                    patch.dict(os.environ, environment, clear=True):
                self.assertFalse(authenticated_native.proxy_configured())
                with self.assertRaises(SourceError) as context:
                    GenericSdkTransport(sdk, egress=EgressPolicy(resolver=broken)).inspect_url("https://gone.example.test/x")
                self.assertIs(context.exception.code, SourceErrorCode.NETWORK)
                self.assertTrue(context.exception.retryable)
            sdk.inspect_url.assert_not_called()

    def test_generic_sdk_is_never_invoked_for_a_private_target(self):
        for url in self.PRIVATE_URLS:
            sdk = Mock()
            transport = GenericSdkTransport(sdk)
            with self.subTest(url=url):
                with self.assertRaises(SourceError) as inspected:
                    transport.inspect_url(url)
                self.assertEqual(inspected.exception.code, SourceErrorCode.INVALID_INPUT)
                with self.assertRaises(SourceError):
                    transport.enumerate_playlist(url, cursor=None, page_size=5)
                sdk.inspect_url.assert_not_called()
                sdk.enumerate_url.assert_not_called()

    def test_generic_sdk_refuses_a_name_with_any_private_answer(self):
        sdk = Mock()
        transport = GenericSdkTransport(sdk, egress=EgressPolicy(resolver=lambda host, port: ["93.184.216.34", "10.0.0.1"]))
        with self.assertRaises(SourceError) as context:
            transport.inspect_url("https://video.example.test/watch/1")
        self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        sdk.inspect_url.assert_not_called()

    def test_playlist_slot_resolution_refuses_private_entries_without_sdk_inspection(self):
        def resolver(host, port):
            return ["10.9.9.9"] if host == "intranet.example.test" else ["93.184.216.34"]

        sdk = Mock()
        sdk.pins = {"sdk": "fixture"}
        sdk.enumerate_url.return_value = {"schema_version": 1, "offset": 0, "has_more": False,
                                          "entries": [{"url": "https://intranet.example.test/v/1"}]}
        page = GenericSdkTransport(sdk, egress=EgressPolicy(resolver=resolver)).enumerate_playlist(
            "https://video.example.test/list/1", cursor=None, page_size=5)
        self.assertEqual(page.items, ())
        self.assertEqual(len(page.failures), 1)
        sdk.inspect_url.assert_not_called()

    def test_ytdlp_runner_is_not_invoked_for_a_private_target(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "yt-dlp.exe"
            executable.write_bytes(b"pinned")
            runner = Mock()
            transport = YtDlpTransport(executable, trusted_root=Path(temp),
                                       expected_sha256=hashlib.sha256(b"pinned").hexdigest(), runner=runner)
            for url in self.PRIVATE_URLS:
                with self.subTest(url=url), self.assertRaises(SourceError) as context:
                    transport.inspect_url(url)
                self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
            runner.run.assert_not_called()

    def test_direct_link_to_loopback_is_an_invalid_input(self):
        with server(ok) as (port, hits):
            with self.assertRaises(SourceError) as context:
                GenericUrlAdapter(DirectUrlTransport()).inspect(f"http://127.0.0.1:{port}/media.mp4")
        self.assertEqual(context.exception.code, SourceErrorCode.INVALID_INPUT)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
