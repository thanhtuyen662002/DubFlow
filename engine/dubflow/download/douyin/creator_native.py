"""Isolated Douyin-only HTTP page backend; no durable writes or raw diagnostics."""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_POSITION = 2**63 - 1
ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/post/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"


class ProviderPageError(RuntimeError):
    def __init__(self, native_exit_code: int, message: str):
        self.native_exit_code = native_exit_code
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider key")
        result[key] = value
    return result


def _http_failure(status):
    code = 2 if status in {401, 403} else 3 if status == 429 else 4 if status == 404 else 6 if isinstance(status, int) and status >= 500 else 5
    raise ProviderPageError(code, "Douyin creator request was refused or unavailable") from None


def _entry(value):
    if not isinstance(value, dict):
        return None
    identifier = value.get("aweme_id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{6,32}", identifier):
        return {"id": None, "error_code": "SOURCE_CHANGED"}
    status = value.get("status", {})
    if not isinstance(status, dict) or any(type(status[key]) not in {bool, int} or status[key] not in (0, 1)
            for key in ("is_delete", "is_private") if key in status):
        return {"id": identifier, "error_code": "SOURCE_CHANGED"}
    if status.get("is_delete") in (True, 1):
        return {"id": identifier, "error_code": "NOT_FOUND"}
    if status.get("is_private") in (True, 1):
        return {"id": identifier, "error_code": "PRIVATE"}
    if isinstance(value.get("images"), list) and value["images"]:
        return {"id": identifier, "error_code": "UNSUPPORTED"}
    title = value.get("desc")
    if title is None or title == "":
        title = identifier
    if (not isinstance(title, str) or len(title) > 4096
            or any((ord(ch) < 32 and ch not in "\r\n\t") or ord(ch) == 127
                   or 0xD800 <= ord(ch) <= 0xDFFF for ch in title)):
        return {"id": identifier, "error_code": "SOURCE_CHANGED"}
    title = " ".join(title.split()) or identifier
    return {"id": identifier, "title": title if len(title) <= 1024 else title[:1023] + "…"}


def parse_page(data: bytes, *, page_size: int):
    if not isinstance(data, bytes) or not data or len(data) > MAX_RESPONSE_BYTES:
        raise ProviderPageError(5, "Douyin creator response exceeds the bound or is empty")
    try:
        value = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProviderPageError(5, "Douyin creator response is not bounded JSON") from None
    if not isinstance(value, dict) or type(value.get("status_code")) is not int:
        raise ProviderPageError(5, "Douyin creator response has no explicit provider status")
    if value["status_code"] != 0:
        # Response text is inspected in memory only; it never crosses stdout.
        message = str(value.get("status_msg", ""))[:2048].lower()
        code = 2 if any(word in message for word in ("login", "auth", "captcha", "challenge", "登录", "验证")) else 3 if any(word in message for word in ("rate", "too many", "频繁")) else 4 if any(word in message for word in ("not found", "不存在")) else 5
        raise ProviderPageError(code, "Douyin creator returned an unsuccessful provider status")
    values, more, position = value.get("aweme_list"), value.get("has_more"), value.get("max_cursor")
    if (type(more) not in {int, bool} or more not in (0, 1) or type(position) is not int
            or not 0 <= position <= MAX_POSITION or not isinstance(values, list) or len(values) > page_size
            or (more and (not values or position == 0))):
        raise ProviderPageError(5, "Douyin creator returned a malformed or empty continuation page")
    return {"schema_version": 1, "max_cursor": position, "has_more": bool(more), "entries": [_entry(item) for item in values]}


def fetch_page(downloader, url: str, *, position: int, page_size: int, signer):
    match = re.fullmatch(r"https://www\.douyin\.com/user/([A-Za-z0-9_-]{1,128})", url) if isinstance(url, str) else None
    if (not match or type(position) is not int or not 0 <= position <= MAX_POSITION
            or type(page_size) is not int or not 1 <= page_size <= 20):
        raise ProviderPageError(5, "invalid Douyin creator request bounds")
    params = {"device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "sec_user_id": match[1], "max_cursor": position, "locate_query": "false",
        "show_live_replay_strategy": 1, "need_time_list": 1, "time_list_query": 0,
        "whale_cut_token": "", "cut_version": 1, "count": page_size,
        "publish_video_strategy_type": 2, "from_user_page": 1, "update_version_code": "170400",
        "pc_client_type": 1, "pc_libra_divert": "Windows", "support_h265": 1, "support_dash": 0,
        "version_code": "290100", "version_name": "29.1.0", "cookie_enabled": "true",
        "screen_width": 1920, "screen_height": 1080, "browser_language": "zh-CN",
        "browser_platform": "Win32", "browser_name": "Edge", "browser_version": "131.0.0.0",
        "browser_online": "true", "engine_name": "Blink", "engine_version": "131.0.0.0",
        "os_name": "Windows", "os_version": "10", "cpu_core_num": 12, "device_memory": 8,
        "platform": "PC", "downlink": 10, "effective_type": "4g", "round_trip_time": 50}
    # Provider-scoped cookies are installed by the parent helper, only in memory.
    # A protected msToken may be required by the endpoint; never log its URL.
    for cookie in downloader.cookiejar:
        if cookie.name == "msToken" and cookie.domain.lstrip(".") == "douyin.com":
            params["msToken"] = cookie.value
            break
    try:
        signed = signer(user_agent=USER_AGENT).generate_abogus(urlencode(params))[0]
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPCookieProcessor(downloader.cookiejar))
        with opener.open(urllib.request.Request(ENDPOINT + "?" + signed, headers={"User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/", "Accept-Encoding": "identity"}), timeout=30) as response:
            if response.status != 200:
                _http_failure(response.status)
            # Reject redirected success: an unrelated login/captcha page is not
            # a provider page, even when its HTTP status is 200.
            if response.geturl() != ENDPOINT + "?" + signed:
                raise ProviderPageError(5, "Douyin creator endpoint redirected")
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except ProviderPageError:
        raise
    except NotImplementedError:
        raise ProviderPageError(7, "owned Douyin request signer is unavailable") from None
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
            _http_failure(error.code)
        raise ProviderPageError(6, "Douyin creator network request failed") from None
    result = parse_page(data, page_size=page_size)
    secrets = [cookie.value for cookie in downloader.cookiejar if len(cookie.value) >= 8]
    if any(secret in entry.get("title", "") for entry in result["entries"] if isinstance(entry, dict) for secret in secrets):
        raise ProviderPageError(5, "Douyin creator response echoed a protected session value")
    if result["has_more"] and position > 0 and result["max_cursor"] >= position:
        raise ProviderPageError(5, "Douyin creator cursor did not advance")
    return result
