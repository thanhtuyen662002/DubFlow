"""Turn a Vietnamese search phrase into a Simplified Chinese one for Khám phá.

``python -m engine.dubflow.discovery.to_chinese -- TEXT`` prints one JSON line:
``{"ok": true, "text": "养猪", "engine": "google"}`` or
``{"ok": false, "code": "NETWORK", "message": "…"}``.

Douyin, Bilibili and Kuaishou only find Chinese keywords, so the desktop sends
what the user typed here when they press "Tìm". The phrase goes to a public web
translator (Google first, Microsoft as the fallback), the same kind of request
a browser makes; nothing is stored. The searches themselves are online anyway.

``--to en`` gives the English phrase instead, for YouTube in "Tất cả nguồn"
(#978): the source language is detected, so Chinese or Vietnamese both work.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .http import USER_AGENT

MAX_CHARS = 100
TIMEOUT = 8.0
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# (request, timeout) -> response body bytes; raises on HTTP/network failure.
Transport = Callable[[Request, float], bytes]


class TranslateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _urllib(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https endpoints below
        return response.read(256 * 1024)


def _clean(text: str) -> str:
    """One line, single spaces, no quotes or trailing punctuation the translator adds."""
    text = " ".join(text.split()).strip(" \"'“”‘’「」《》。.!！?？,，")
    return text[:MAX_CHARS]


def _google(text: str, send: Transport, target: str = "zh") -> str:
    source, goal = ("vi", "zh-CN") if target == "zh" else ("auto", "en")
    query = urlencode({"client": "gtx", "sl": source, "tl": goal, "dt": "t", "q": text})
    request = Request(f"https://translate.googleapis.com/translate_a/single?{query}", headers={"User-Agent": USER_AGENT})
    payload = json.loads(send(request, TIMEOUT).decode("utf-8"))
    parts = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    return "".join(part[0] for part in parts if isinstance(part, list) and part and isinstance(part[0], str))


def _microsoft(text: str, send: Transport, target: str = "zh") -> str:
    token = send(Request("https://edge.microsoft.com/translate/auth", headers={"User-Agent": USER_AGENT}), TIMEOUT).decode("ascii").strip()
    if not token or len(token) > 4096 or not token.isascii() or any(character.isspace() for character in token):
        raise ValueError("bad token")
    request = Request(
        "https://api-edge.cognitive.microsofttranslator.com/translate?" + urlencode(
            {"from": "vi", "to": "zh-Hans", "api-version": "3.0"} if target == "zh" else {"to": "en", "api-version": "3.0"}),
        data=json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(send(request, TIMEOUT).decode("utf-8"))
    return payload[0]["translations"][0]["text"]


ENGINES: tuple[tuple[str, Callable[..., str]], ...] = (("google", _google), ("microsoft", _microsoft))


def to_chinese(text: str, send: Transport = _urllib) -> tuple[str, str]:
    """(Chinese phrase, engine name). Raises TranslateError when no engine gives Chinese."""
    text = _clean(text)
    if not text:
        raise TranslateError("EMPTY", "Không có gì để dịch.")
    if CJK.search(text):
        return text, "none"
    offline = True
    for name, engine in ENGINES:
        try:
            result = _clean(engine(text, send))
        except (HTTPError, ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
            offline = False  # The service answered, but not with a translation.
            continue
        except (URLError, OSError):
            continue
        if CJK.search(result):
            return result, name
        offline = False
    if offline:
        raise TranslateError("NETWORK", "Không kết nối được dịch vụ dịch.")
    raise TranslateError("UNAVAILABLE", "Dịch vụ dịch không trả về tiếng Trung.")


def to_english(text: str, send: Transport = _urllib) -> tuple[str, str]:
    """(English phrase, engine name) from Vietnamese or Chinese. Raises TranslateError when no engine answers."""
    text = _clean(text)
    if not text:
        raise TranslateError("EMPTY", "Không có gì để dịch.")
    offline = True
    for name, engine in ENGINES:
        try:
            result = _clean(engine(text, send, "en"))
        except (HTTPError, ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
            offline = False
            continue
        except (URLError, OSError):
            continue
        if result and not CJK.search(result) and re.search(r"[A-Za-z]", result):
            return result, name
        offline = False
    if offline:
        raise TranslateError("NETWORK", "Không kết nối được dịch vụ dịch.")
    raise TranslateError("UNAVAILABLE", "Dịch vụ dịch không trả về tiếng Anh.")


def main(argv: list[str] | None = None, *, send: Transport = _urllib, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", choices=("zh", "en"), default="zh")
    parser.add_argument("text")
    args = parser.parse_args(argv)
    try:
        text, engine = (to_english if args.to == "en" else to_chinese)(args.text, send)
        result = {"ok": True, "text": text, "engine": engine}
    except TranslateError as error:
        result = {"ok": False, "code": error.code, "message": error.message}
    except Exception as error:  # noqa: BLE001 - the host always needs one JSON line.
        result = {"ok": False, "code": "FAILED", "message": f"{type(error).__name__}: {str(error)[:200]}"}
    print(json.dumps(result, ensure_ascii=True), file=stdout or sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
