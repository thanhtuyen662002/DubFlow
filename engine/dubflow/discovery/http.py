"""Small anonymous urllib client with isolated, in-memory platform sessions.

An injected transport receives (urllib.request.Request, timeout_seconds) and
returns HttpResponse with *wire* bytes, including the declared compression.
One client serializes each platform's requests, including bootstraps/failures.
There are no automatic retries or persistent/browser cookies.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
import gzip
from http.client import HTTPException
from http.cookiejar import Cookie, CookieJar
import json
from threading import Lock
import time
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener
import zlib


ErrorCode = Literal["BLOCKED", "LOGIN_REQUIRED", "NETWORK", "UNSUPPORTED", "BAD_RESPONSE"]


class DiscoveryError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Message | Mapping[str, str]
    body: bytes

    def info(self) -> Message:
        if isinstance(self.headers, Message):
            return self.headers
        headers = Message()
        for key, value in self.headers.items():
            headers.add_header(key, value)
        return headers


Transport = Callable[[Request, float], HttpResponse]
REFERERS = {
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://www.bilibili.com/",
    "kuaishou": "https://www.kuaishou.com/",
}
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.1
TIMEOUT = 15.0


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must not issue a hidden, unthrottled metadata request.
        return None


def check_application_errors(payload: dict, platform: str) -> None:
    """Inspect error envelopes, not arbitrary video titles or descriptions."""
    for key, value in payload.items():
        if key in ("code", "status_code", "result", "error_code"):
            if value in (-352, -412, 400002) or (platform == "kuaishou" and value == 50):
                raise DiscoveryError("BLOCKED", "Platform requires verification in its browser.")
            if platform == "douyin" and value == 2483:
                raise DiscoveryError("LOGIN_REQUIRED", "Douyin requires login in its browser.")
        if value and key.lower() in ("v_voucher", "voucher", "captcha", "captcha_url", "verify_url"):
            raise DiscoveryError("BLOCKED", "Platform requires verification in its browser.")
        if isinstance(value, str) and key in ("url", "message", "msg", "status_msg", "error_msg"):
            if any(marker in value.lower() for marker in
                   ("captcha", "anticrawl", "verification", "风控", "验证")):
                raise DiscoveryError("BLOCKED", "Platform requires verification in its browser.")
        if isinstance(value, dict):
            check_application_errors(value, platform)
        elif key == "errors" and isinstance(value, list):
            for error in value:
                if isinstance(error, dict):
                    check_application_errors(error, platform)


class HttpClient:
    def __init__(self, transport: Transport | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._transport = transport
        self._clock, self._sleep = clock, sleep
        self.cookie_jars = {platform: CookieJar() for platform in REFERERS}
        # A fake transport never initializes urllib's proxy/network machinery.
        self._openers = {}
        self._locks = {platform: Lock() for platform in REFERERS}
        self._last_start: dict[str, float] = {}

    def set_cookie(self, platform: str, name: str, value: str) -> None:
        self.cookie_jars[platform].set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain="." + platform + ".com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=True,
            expires=None, discard=True, comment=None, comment_url=None, rest={},
        ))

    def _send(self, platform: str, request: Request) -> HttpResponse:
        """Called under the platform lock, including for the verified redirect."""
        jar = self.cookie_jars[platform]
        jar.add_cookie_header(request)
        if self._transport is None and platform not in self._openers:
            self._openers[platform] = build_opener(_NoRedirect(), HTTPCookieProcessor(jar))
        if platform in self._last_start:
            delay = MIN_INTERVAL - (self._clock() - self._last_start[platform])
            if delay > 0:
                self._sleep(delay)
        self._last_start[platform] = self._clock()
        try:
            if self._transport is not None:
                response = self._transport(request, TIMEOUT)
            else:
                try:
                    opened = self._openers[platform].open(request, timeout=TIMEOUT)
                except HTTPError as error:
                    opened = error
                with opened:
                    response = HttpResponse(opened.code, opened.headers, opened.read())
            jar.extract_cookies(response, request)
        except (URLError, OSError, HTTPException) as error:
            raise DiscoveryError("NETWORK", "Discovery request failed or timed out.") from error
        return response

    def request(self, platform: str, url: str, *, params: Mapping | None = None,
                json_body: dict | None = None, accept: str = "application/json, text/plain, */*") -> bytes:
        if platform not in REFERERS:
            raise DiscoveryError("UNSUPPORTED", "Unknown discovery platform.")
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                   "Accept-Encoding": "gzip, deflate", "Referer": REFERERS[platform],
                   "Accept": accept}
        body = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": REFERERS[platform].rstrip("/")})
        request = Request(url, data=body, headers=headers)
        with self._locks[platform]:
            response = self._send(platform, request)
            # The research homepage sample records /new-reco as its final URL.
            # Follow that one observed GET hop explicitly so it is paced and
            # cookies are recomputed. No other redirect routes are enabled.
            if (platform == "kuaishou" and url == REFERERS[platform] and body is None
                    and accept == "text/html" and response.status in (301, 302, 303, 307, 308)):
                destination = urljoin(url, response.info().get("Location", ""))
                if destination == "https://www.kuaishou.com/new-reco":
                    response = self._send(platform, Request(destination, headers=headers))
        if response.status in (403, 412, 429):
            raise DiscoveryError("BLOCKED", "Platform refused the request; open it in its browser.")
        try:
            encoding = response.info().get("Content-Encoding", "").lower().strip()
            raw = response.body
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            elif encoding not in ("", "identity"):
                raise ValueError("Unsupported content encoding")
        except (OSError, EOFError, zlib.error, ValueError) as error:
            raise DiscoveryError("BAD_RESPONSE", "Invalid discovery response encoding.") from error
        # HTTP 400 can also contain a useful captcha/error envelope.
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            text = raw[:65536].decode("utf-8", errors="replace").lower()
            markers = ("__ac_signature", "argussecurity") if accept == "text/html" else (
                "captcha", "__ac_signature", "argussecurity", "verification", "challenge")
            if any(marker in text for marker in markers):
                raise DiscoveryError("BLOCKED", "Platform requires verification in its browser.")
        else:
            if isinstance(payload, dict):
                check_application_errors(payload, platform)
        if not 200 <= response.status < 300:
            code = "NETWORK" if response.status >= 500 else "BAD_RESPONSE"
            raise DiscoveryError(code, f"Discovery endpoint returned HTTP {response.status}.")
        return raw

    def json(self, platform: str, url: str, **kwargs) -> dict:
        raw = self.request(platform, url, **kwargs)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise DiscoveryError("BAD_RESPONSE", "Discovery endpoint did not return valid JSON.") from error
        if not isinstance(payload, dict):
            raise DiscoveryError("BAD_RESPONSE", "Expected a discovery response object.")
        return payload
