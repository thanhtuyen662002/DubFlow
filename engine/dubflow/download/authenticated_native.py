"""Isolated -I -S helper: only bounded stdin owns provider credentials."""
from __future__ import annotations

import http.cookiejar
from http.cookies import SimpleCookie
import json
import sys
import time
from urllib.parse import urlsplit


def scoped_cookies(provider, headers):
    domains = {"bilibili": "bilibili.com", "douyin": "douyin.com"}
    if provider not in domains or not isinstance(headers, dict) or set(headers) != {"Cookie"}:
        raise ValueError("invalid session")
    text = headers["Cookie"]
    if not isinstance(text, str) or not 0 < len(text) <= 2048 or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        raise ValueError("invalid session size")
    parsed = SimpleCookie()
    parsed.load(text)
    if not 1 <= len(parsed) <= 64:
        raise ValueError("invalid cookie header")
    cookies = []
    for name, value in parsed.items():
        if any(value.values()):  # Domain/path/expiry always come from our provider boundary.
            raise ValueError("untrusted cookie scope")
        cookies.append(http.cookiejar.Cookie(0, name, value.value, None, False,
            "." + domains[provider], True, True, "/", True, True,
            int(time.time()) + 86400, False, None, None, {"HttpOnly": None}))
    return cookies


def downloader_options():
    class QuietLogger:
        def debug(self, message): pass
        def warning(self, message): pass
        def error(self, message): pass

    # Programmatic options never load CLI config. No generic custom headers,
    # disk cookie file, system JS runtime or mutable helper downloads.
    return {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "cachedir": False, "js_runtimes": {}, "remote_components": [], "logger": QuietLogger(),
            "socket_timeout": 30, "retries": 0, "extractor_retries": 0}


def inspect(request, youtube_dl):
    provider = request.get("provider_id")
    if provider not in {"bilibili", "douyin"}:
        raise ValueError("invalid provider")
    cookies = scoped_cookies(provider, request.get("headers")) if request.get("headers") else []
    url = request.get("url")
    if not isinstance(url, str) or not 0 < len(url) <= 1024 or any(ord(ch) < 33 for ch in url):
        raise ValueError("invalid provider URL")
    parts = urlsplit(url)
    domain = {"bilibili": "bilibili.com", "douyin": "douyin.com"}[provider]
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or parts.port not in {None, 443} or not (parts.hostname == domain or (parts.hostname or "").endswith("." + domain)):
        raise ValueError("foreign provider URL")

    with youtube_dl(downloader_options()) as downloader:
        for cookie in cookies:
            downloader.cookiejar.set_cookie(cookie)
        raw = downloader.extract_info(url, download=False)
        if not isinstance(raw, dict) or raw.get("_type") in {"playlist", "multi_video"}:
            raise ValueError("unexpected provider metadata")
        return public_metadata(raw)


def public_metadata(raw):
    # Preserve only fields used by provider mapping; never return Cookie,
    # Authorization, request headers or a serializable credential jar.
    result = {key: raw[key] for key in ("id", "title", "description", "duration") if key in raw}
    formats = []
    values = raw.get("formats", [])
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("invalid formats")
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("invalid format")
        formats.append({key: value[key] for key in ("format_id", "url", "width", "height", "vcodec", "acodec", "ext", "protocol", "tbr", "abr") if key in value})
    result["formats"] = formats
    subtitles = raw.get("subtitles", {})
    if not isinstance(subtitles, dict) or len(subtitles) > 256:
        raise ValueError("invalid subtitles")
    result["subtitles"] = {}
    for language, values in subtitles.items():
        if not isinstance(language, str) or not isinstance(values, list) or len(values) > 16:
            raise ValueError("invalid subtitle candidates")
        result["subtitles"][language] = [{key: value[key] for key in ("url", "ext") if key in value} for value in values if isinstance(value, dict)]
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("metadata exceeded budget")
    return result


def main():
    try:
        payload = sys.stdin.buffer.read(4097)
        if len(payload) > 4096:
            return 2
        request = json.loads(payload)
        if not isinstance(request, dict) or request.get("schema_version") != 1 or len(sys.argv) != 2:
            return 2
        operation = request.get("operation", "inspect")
        if operation not in {"inspect", "health_check"}:
            return 2
        if operation == "health_check" and set(request) != {"schema_version", "operation"}:
            return 2
        sys.path.insert(0, sys.argv[1])  # Parent verified the whole SDK archive hash.
        import yt_dlp
        from yt_dlp.globals import plugin_dirs
        plugin_dirs.value = []
        if operation == "health_check":
            from yt_dlp.version import __version__
            with yt_dlp.YoutubeDL(downloader_options()) as downloader:
                if list(downloader.cookiejar):
                    raise ValueError("unexpected health check cookies")
                result = {"schema_version": 1, "sdk_version": __version__,
                          "python_version": ".".join(map(str, sys.version_info[:3])),
                          "python_executable": sys.executable, "python_prefix": sys.prefix,
                          "python_base_prefix": sys.base_prefix, "import_roots": list(sys.path),
                          "isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site)}
        else:
            result = inspect(request, yt_dlp.YoutubeDL)
        sys.stdout.write(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        # Classification is bounded; raw errors can contain cookie/token URLs.
        text = str(error)[:4096].lower()
        if any(value in text for value in ("login", "private", "cookie", "auth", "challenge")):
            return 2
        if "429" in text or "too many" in text:
            return 3
        if "not found" in text or "unavailable" in text:
            return 4
        return 5 if isinstance(error, (ValueError, ImportError)) else 6


if __name__ == "__main__":
    raise SystemExit(main())
