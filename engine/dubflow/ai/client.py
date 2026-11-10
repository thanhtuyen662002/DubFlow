"""One structured (JSON) answer from the user's own AI provider, over HTTPS.

Three wire dialects cover every provider DubFlow offers:

* ``anthropic``: Claude's Messages API;
* ``gemini``: Google's generateContent;
* ``openai``: the OpenAI Chat Completions shape, which OpenAI (ChatGPT),
  DeepSeek, Qwen, Grok, OpenRouter, Groq, Mistral and any custom
  "OpenAI-compatible" server (LM Studio, Ollama, vLLM, one-api, ...) speak.

The key belongs to the user of this desktop app. It travels in a header (never
in the URL), is never logged, and every error message is scrubbed of it.
Requests are bounded: at most three attempts, only on rate limits (429),
server errors (5xx) and time-outs, each after a pause.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from http.client import HTTPException
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ANTHROPIC_VERSION = "2023-06-01"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# Seconds without a byte from the server. Thinking models (DeepSeek V4 Pro, o-series) can stay quiet
# for minutes before answering a long window, so this is generous; a dead connection still fails.
DEFAULT_TIMEOUT = 300.0
# Google's free tier asks for waits of up to about a minute between bursts; waiting is cheaper
# than leaving a window to the offline translator.
MAX_WAIT_SECONDS = 65.0


@dataclass(frozen=True)
class Preset:
    label: str
    dialect: str
    base_url: str
    model: str
    vision: bool
    console: str
    # OpenAI-shaped providers that accept response_format {"type": "json_object"}.
    json_mode: bool = True


# Default models are the providers' stable, general-purpose names. The user can
# pick any other model the key can use in "Cài đặt AI" (the list comes from the
# provider's own /models endpoint).
PRESETS: dict[str, Preset] = {
    "claude": Preset("Claude (Anthropic)", "anthropic", "https://api.anthropic.com/v1", "claude-opus-5-5", True, "console.anthropic.com"),
    "gemini": Preset("Gemini (Google)", "gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-3.5-flash-lite", True, "aistudio.google.com/apikey"),
    "openai": Preset("ChatGPT (OpenAI)", "openai", "https://api.openai.com/v1", "gpt-5-mini", True, "platform.openai.com/api-keys"),
    "deepseek": Preset("DeepSeek", "openai", "https://api.deepseek.com", "deepseek-flash", True, "platform.deepseek.com/api_keys"),
    "qwen": Preset("Qwen (Alibaba Cloud)", "openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen-plus", False, "modelstudio.console.alibabacloud.com"),
    "kimi": Preset("Kimi (Moonshot AI)", "openai", "https://api.moonshot.ai/v1", "kimi-k3", True, "platform.kimi.ai"),
    "glm": Preset("GLM (Z.ai / Zhipu)", "openai", "https://api.z.ai/api/paas/v4", "glm-5.3", False, "z.ai/manage-apikey/apikey-list"),
    "grok": Preset("Grok (xAI)", "openai", "https://api.x.ai/v1", "grok-4.7", True, "console.x.ai"),
    "openrouter": Preset("OpenRouter (nhiều model)", "openai", "https://openrouter.ai/api/v1", "openrouter/auto", True, "openrouter.ai/keys"),
    "groq": Preset("Groq", "openai", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", False, "console.groq.com/keys"),
    "mistral": Preset("Mistral AI", "openai", "https://api.mistral.ai/v1", "mistral-medium-latest", True, "console.mistral.ai/api-keys"),
    "custom": Preset("Tùy chỉnh (tương thích OpenAI)", "openai", "", "", True, "máy chủ của bạn", json_mode=False),
}

# Providers whose server address the user may change: their own server, or Qwen's region
# (Singapore by default; Beijing keys use https://dashscope.aliyuncs.com/compatible-mode/v1).
EDITABLE_BASE_URL = frozenset({"custom", "qwen"})

# When a default model is retired, the first available name matching these is used instead.
_MODEL_PREFERENCES: dict[str, tuple[str, ...]] = {
    "claude": (r"^claude-(opus|sonnet)-", r"^claude-"),
    "gemini": (r"^gemini-[\d.]+-flash-lite$", r"^gemini-[\d.]+-flash$", r"^gemini-"),
    "openai": (r"^gpt-[\d.]+-mini$", r"^gpt-[\d.]+-(luna|nano)$", r"^gpt-[\d.]+$", r"^gpt-"),
    "deepseek": (r"^deepseek-flash$", r"^deepseek-.*flash", r"^deepseek-chat$", r"^deepseek-"),
    "qwen": (r"^qwen-plus$", r"^qwen[\d.]*-plus", r"^qwen[\d.]*-flash", r"^qwen"),
    "kimi": (r"^kimi-k[\d.]+$", r"^kimi-"),
    "glm": (r"^glm-[\d.]+$", r"^glm-"),
    "grok": (r"^grok-[\d.]+-fast", r"^grok-[\d.]+$", r"^grok-"),
    "openrouter": (r"^openrouter/auto$",),
    "groq": (r"^openai/gpt-oss-120b$", r"^openai/gpt-oss", r"^llama-.*versatile$"),
    "mistral": (r"^mistral-medium-latest$", r"^mistral-large-latest$", r"^mistral-small-latest$"),
}
# Models that do not answer chat: embeddings, speech, images, music, video, robots and screen agents.
_NOT_CHAT = re.compile(r"embed|whisper|tts|audio|realtime|transcri|image|dall-e|moderation|search|ocr|vision-exp|guard|codex|-code"
                       r"|nano-banana|lyria|veo|robotics|computer-use|-live", re.I)


def pick_model(provider: str, names: Sequence[str]) -> str | None:
    """The preferred chat model among the names a key can use, or None."""
    usable = [name for name in names if not _NOT_CHAT.search(name)]
    for pattern in _MODEL_PREFERENCES.get(provider, ()):
        matches = sorted((name for name in usable if re.search(pattern, name)), reverse=True)
        if matches:
            return matches[0]
    return None

# (request, timeout) -> (status, headers, body). Raises URLError/OSError for network failures.
Transport = Callable[[Request, float], tuple[int, Mapping[str, str], bytes]]


class AiError(Exception):
    """A failure the user can act on. ``code``: KEY, LIMIT, QUOTA, SERVER, TIMEOUT, NETWORK, REFUSED, FORMAT, INVALID, MODEL.

    LIMIT is a per-minute rate limit (worth waiting for); QUOTA is a daily quota that is used up,
    typical of a free key, which no retry today can fix.
    """

    def __init__(self, code: str, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{code}: {message}")

    @property
    def retryable(self) -> bool:
        return self.code in {"LIMIT", "SERVER", "TIMEOUT", "NETWORK"}


@dataclass(frozen=True)
class Route:
    """Who answers: a provider id, its model and (for OpenAI-shaped servers) the base URL."""

    provider: str
    model: str
    base_url: str
    key: str

    @property
    def preset(self) -> Preset:
        return PRESETS[self.provider]

    def public(self) -> dict[str, str]:
        """What may be recorded in manifests and checkpoints: never the key."""
        return {"provider": self.provider, "model": self.model, "base_url": self.base_url}


def _local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def check_base_url(value: str) -> str:
    """An https URL, or http only for a server on this computer (LM Studio, Ollama)."""
    text = value.strip().rstrip("/")
    parts = urlsplit(text)
    if len(text) > 300 or parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        raise AiError("INVALID", "Địa chỉ máy chủ AI không hợp lệ.")
    if parts.scheme != "https" and not (parts.scheme == "http" and _local_host(parts.hostname)):
        raise AiError("INVALID", "Địa chỉ máy chủ AI phải bắt đầu bằng https:// (http:// chỉ dùng cho máy chủ trên máy này).")
    return text


def make_route(provider: str, key: str, *, model: str | None = None, base_url: str | None = None) -> Route:
    if provider not in PRESETS:
        raise AiError("INVALID", "Nhà cung cấp AI không hợp lệ.")
    preset = PRESETS[provider]
    base = check_base_url(base_url or preset.base_url) if (base_url or preset.base_url) else ""
    if provider not in EDITABLE_BASE_URL:
        base = preset.base_url  # Presets always use their own server.
    elif not base:
        raise AiError("INVALID", "Hãy nhập địa chỉ máy chủ (Base URL) cho mục Tùy chỉnh.")
    chosen = (model or "").strip() or preset.model
    if not chosen or len(chosen) > 200 or re.search(r"[\s\x00-\x1f]", chosen):
        raise AiError("INVALID", "Hãy nhập tên model.")
    key = (key or "").strip()
    if not key and not (provider == "custom" and _local_host(urlsplit(base).hostname or "")):
        raise AiError("KEY", "Chưa có khóa API.")
    return Route(provider, chosen, base, key)


MAX_REDIRECTS = 3


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or {"https": 443, "http": 80}.get(parts.scheme)
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


class _SameOriginRedirects(HTTPRedirectHandler):
    """Follows a redirect only to the same scheme, host and port, at most MAX_REDIRECTS times.

    urllib's default handler copies every header (Authorization, x-api-key,
    x-goog-api-key) to wherever a server redirects, so a redirect to another origin
    would hand the user's key to it. Such a redirect fails closed as the 3xx itself.
    """

    max_redirections = MAX_REDIRECTS
    max_repeats = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's signature
        if _origin(newurl) != _origin(req.full_url):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_SameOriginRedirects)


def _urllib(request: Request, timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # https or a local server, checked above
            return response.status, dict(response.headers.items()), response.read(MAX_RESPONSE_BYTES)
    except HTTPError as error:
        return error.code, dict(error.headers.items()) if error.headers else {}, error.read(MAX_RESPONSE_BYTES)


def _scrub(text: str, key: str) -> str:
    text = str(text)
    if key:
        text = text.replace(key, "…")
    return re.sub(r"\s+", " ", text).strip()[:300]


def _retry_after(headers: Mapping[str, str], payload: Any) -> float | None:
    for name, value in headers.items():
        if name.lower() == "retry-after" and re.fullmatch(r"\d{1,6}(\.\d+)?", str(value).strip()):
            return float(value)
    details = payload.get("error", {}).get("details") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else None
    for detail in details if isinstance(details, list) else []:
        match = re.fullmatch(r"(\d{1,6}(?:\.\d+)?)s", str((detail or {}).get("retryDelay", "")).strip()) if isinstance(detail, dict) else None
        if match:
            return float(match.group(1))
    return None


def daily_quota(payload: Any, detail: str = "") -> bool:
    """Whether a 429 says the day's quota is used up (Gemini free tier: ``...PerDay...`` quota ids)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for item in details if isinstance(details, list) else []:
        for violation in (item.get("violations") or []) if isinstance(item, dict) and isinstance(item.get("violations"), list) else []:
            if isinstance(violation, dict) and re.search(r"per_?day|daily", f"{violation.get('quotaId', '')} {violation.get('quotaMetric', '')}", re.I):
                return True
    return bool(re.search(r"per[ _-]?day|daily (limit|quota)|requests? per day", detail, re.I))


def _error_kind(payload: Any) -> str:
    """An OpenAI-shaped error's ``type`` and ``code`` (``insufficient_quota`` is an account without credit)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    return f"{error.get('type', '')} {error.get('code', '')}" if isinstance(error, dict) else ""


def _error_text(payload: Any, body: bytes) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
        if payload.get("message"):
            return str(payload["message"])
    return body[:200].decode("utf-8", "replace")


def _http_error(route: Route, status: int, headers: Mapping[str, str], body: bytes) -> AiError:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = None
    detail = _scrub(_error_text(payload, body), route.key)
    name = route.preset.label
    if status in {401, 403} or re.search(r"api[_ -]?key|unauthori[sz]ed|authentication|PERMISSION_DENIED|UNAUTHENTICATED", detail, re.I) and status == 400:
        return AiError("KEY", f"Khóa {name} không hợp lệ hoặc không có quyền ({status}): {detail}", status=status)
    # Gemini's free-tier 429 says "check your plan and billing details": a rate limit, not an empty balance.
    if status == 402 or status != 429 and re.search(r"insufficient[_ ]balance|insufficient_quota|billing|credit", detail, re.I) \
            or status == 429 and re.search(r"insufficient[_ ]balance|insufficient_quota", f"{detail} {_error_kind(payload)}", re.I):
        return AiError("BALANCE", f"Tài khoản {name} hết số dư hoặc hạn mức ({status}): {detail}", status=status)
    if status == 404 or re.search(r"model.*(not.*found|does not exist|not exist|invalid)|unknown model", detail, re.I):
        return AiError("MODEL", f"{name} không có model “{route.model}” ({status}): {detail}", status=status)
    if 300 <= status <= 399:
        return AiError("INVALID", f"{name} chuyển hướng sang địa chỉ khác ({status}); DubFlow không gửi khóa theo. Hãy kiểm tra địa chỉ máy chủ.", status=status)
    if status == 429 and daily_quota(payload, detail):
        return AiError("QUOTA", f"Khóa {name} đã hết lượt gọi của hôm nay (gói miễn phí giới hạn số lần gọi mỗi ngày). "
                       "Hãy thử lại vào ngày mai, hoặc dùng khóa trả phí hay nhà cung cấp khác trong Cài đặt › Tài khoản.", status=status)
    if status == 429:
        return AiError("LIMIT", f"Đã vượt giới hạn số lần gọi {name}; hãy chờ một phút rồi thử lại.", status=status, retry_after=_retry_after(headers, payload))
    if 500 <= status <= 599:
        return AiError("SERVER", f"{name} đang quá tải hoặc lỗi máy chủ ({status}); hãy thử lại sau ít phút.", status=status, retry_after=_retry_after(headers, payload))
    return AiError("INVALID", f"{name} từ chối yêu cầu ({status}): {detail}", status=status)


def _send(route: Route, request: Request, transport: Transport, timeout: float) -> Any:
    try:
        status, headers, body = transport(request, timeout)
    except TimeoutError as error:
        raise AiError("TIMEOUT", f"{route.preset.label} trả lời quá lâu.") from error
    except URLError as error:
        if isinstance(getattr(error, "reason", None), TimeoutError):
            raise AiError("TIMEOUT", f"{route.preset.label} trả lời quá lâu.") from error
        raise AiError("NETWORK", f"Không kết nối được {route.preset.label}: {_scrub(getattr(error, 'reason', error), route.key)}") from error
    except OSError as error:
        raise AiError("NETWORK", f"Không kết nối được {route.preset.label}: {_scrub(error, route.key)}") from error
    except HTTPException as error:
        # IncompleteRead, RemoteDisconnected, BadStatusLine: the connection was cut mid-answer.
        raise AiError("NETWORK", f"Kết nối tới {route.preset.label} bị ngắt giữa chừng ({type(error).__name__}).") from error
    if not 200 <= status <= 299:
        raise _http_error(route, status, headers, body)
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise AiError("FORMAT", f"{route.preset.label} trả về dữ liệu không đọc được.") from error


def _json_request(url: str, headers: Mapping[str, str], body: Mapping[str, Any] | None = None) -> Request:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    return Request(url, data=data, headers={"content-type": "application/json", "accept": "application/json",
                                            "user-agent": "DubFlow", **headers}, method="POST" if data else "GET")


def _auth_headers(route: Route) -> dict[str, str]:
    if route.preset.dialect == "anthropic":
        return {"x-api-key": route.key, "anthropic-version": ANTHROPIC_VERSION}
    if route.preset.dialect == "gemini":
        return {"x-goog-api-key": route.key}
    headers = {"authorization": f"Bearer {route.key}"} if route.key else {}
    if route.provider == "openrouter":
        headers.update({"http-referer": "https://github.com/thanhtuyen662002/DubFlow", "x-title": "DubFlow"})
    return headers


def parse_json_text(text: str) -> Any:
    """The JSON object in a model's text: bare, inside ```json fences, or after a short preamble."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(stripped[start:end + 1])
            except ValueError:
                pass
    raise AiError("FORMAT", "AI trả về dữ liệu sai định dạng JSON; hãy thử lại.")


def _schema_hint(schema: Mapping[str, Any]) -> str:
    return ("\n\nChỉ trả lời bằng MỘT đối tượng JSON hợp lệ (không markdown, không giải thích), đúng theo JSON Schema sau:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


def _gemini_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, str):
            result["type"] = value.upper()
        elif key == "type" and isinstance(value, list):
            # Gemini's schema has one type and a ``nullable`` flag, not a list of types.
            kinds = [kind for kind in value if isinstance(kind, str) and kind != "null"]
            result["type"] = (kinds[0] if kinds else "string").upper()
            if "null" in value:
                result["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            result["properties"] = {name: _gemini_schema(field) for name, field in value.items()}
        else:
            result[key] = _gemini_schema(value)
    return result


Part = Mapping[str, Any]  # {"text": str} or {"image": {"mediaType": "image/jpeg", "data": base64}}


def _once(route: Route, system: str, schema: Mapping[str, Any], parts: Sequence[Part], transport: Transport, timeout: float,
          max_tokens: int, json_mode: bool) -> Any:
    preset = route.preset
    images = [part["image"] for part in parts if "image" in part]
    if images and not preset.vision:
        raise AiError("INVALID", f"{preset.label} không đọc được ảnh; hãy chọn Claude, Gemini hoặc ChatGPT cho việc này.")
    if preset.dialect == "anthropic":
        content = [{"type": "text", "text": part["text"]} if "text" in part else
                   {"type": "image", "source": {"type": "base64", "media_type": part["image"]["mediaType"], "data": part["image"]["data"]}}
                   for part in parts]
        reply = _send(route, _json_request(f"{route.base_url}/messages", _auth_headers(route), {
            "model": route.model, "max_tokens": max_tokens, "system": system + _schema_hint(schema),
            "messages": [{"role": "user", "content": content}]}), transport, timeout)
        if reply.get("stop_reason") == "refusal":
            raise AiError("REFUSED", "Claude từ chối yêu cầu này.")
        if reply.get("stop_reason") == "max_tokens":
            raise AiError("FORMAT", "Câu trả lời bị cắt ngắn; hãy thử lại.")
        text = "".join(block.get("text", "") for block in reply.get("content", []) if isinstance(block, dict) and block.get("type") == "text")
        return parse_json_text(text)
    if preset.dialect == "gemini":
        reply = _send(route, _json_request(f"{route.base_url}/models/{route.model}:generateContent", _auth_headers(route), {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": part["text"]} if "text" in part else
                                                    {"inlineData": {"mimeType": part["image"]["mediaType"], "data": part["image"]["data"]}} for part in parts]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": _gemini_schema(schema), "maxOutputTokens": max_tokens},
        }), transport, timeout)
        if (reply.get("promptFeedback") or {}).get("blockReason"):
            raise AiError("REFUSED", "Gemini từ chối yêu cầu này.")
        candidate = (reply.get("candidates") or [{}])[0] or {}
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise AiError("FORMAT", "Câu trả lời bị cắt ngắn; hãy thử lại.")
        if candidate.get("finishReason") in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"}:
            raise AiError("REFUSED", "Gemini từ chối yêu cầu này.")
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []) if isinstance(part, dict) and not part.get("thought"))
        if not text.strip():
            raise AiError("SERVER", "Gemini không trả về nội dung; hãy thử lại.")
        return parse_json_text(text)
    content: Any = "\n\n".join(part["text"] for part in parts if "text" in part)
    if images:
        content = [{"type": "text", "text": part["text"]} if "text" in part else
                   {"type": "image_url", "image_url": {"url": f"data:{part['image']['mediaType']};base64,{part['image']['data']}"}}
                   for part in parts]
    body: dict[str, Any] = {"model": route.model, "messages": [{"role": "system", "content": system + _schema_hint(schema)},
                                                               {"role": "user", "content": content}]}
    # OpenAI's newer models take max_completion_tokens and reject max_tokens; the others take max_tokens.
    body["max_completion_tokens" if route.provider == "openai" else "max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    reply = _send(route, _json_request(f"{route.base_url}/chat/completions", _auth_headers(route), body), transport, timeout)
    choice = (reply.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise AiError("REFUSED", f"{preset.label} từ chối yêu cầu này.")
    if choice.get("finish_reason") == "length":
        raise AiError("FORMAT", "Câu trả lời bị cắt ngắn; hãy thử lại.")
    if choice.get("finish_reason") == "content_filter":
        raise AiError("REFUSED", f"{preset.label} từ chối yêu cầu này.")
    text = message.get("content")
    if isinstance(text, list):  # Some servers return content parts.
        text = "".join(item.get("text", "") for item in text if isinstance(item, dict))
    if not isinstance(text, str) or not text.strip():
        raise AiError("SERVER", f"{preset.label} không trả về nội dung; hãy thử lại.")
    return parse_json_text(text)


def ask_json(route: Route, system: str, schema: Mapping[str, Any], parts: Sequence[Part], *, transport: Transport = _urllib,
             sleep: Callable[[float], None] = time.sleep, timeout: float = DEFAULT_TIMEOUT, max_tokens: int = 16000) -> Any:
    """One JSON answer. Retries at most twice, only on 429/5xx/time-outs; a format error is asked once more.

    A custom server that rejects ``response_format`` (400) is asked again without it.
    """
    json_mode = route.preset.json_mode
    last: AiError | None = None
    format_retry = True
    for attempt in range(3):
        if attempt and last is not None:
            wait = last.retry_after if last.retry_after is not None else (3.0 * attempt)
            if wait > MAX_WAIT_SECONDS:
                raise AiError(last.code, f"{route.preset.label} yêu cầu chờ khoảng {max(1, round(wait / 60))} phút trước khi gọi lại; hãy thử lại sau.", status=last.status)
            sleep(wait)
        try:
            return _once(route, system, schema, parts, transport, timeout, max_tokens, json_mode)
        except AiError as error:
            if error.code == "INVALID" and error.status == 400 and json_mode and route.preset.dialect == "openai" and "response_format" in error.message:
                json_mode = False
                last = None
                continue
            if error.code == "FORMAT" and format_retry:
                format_retry = False
                last = AiError("FORMAT", error.message, retry_after=1.0)
                continue
            if not error.retryable:
                raise
            last = error
    assert last is not None
    raise last


def list_models(route: Route, *, transport: Transport = _urllib, timeout: float = 30.0) -> list[str]:
    """The model names this key can use; a working call also proves the key and the server address."""
    dialect = route.preset.dialect
    if dialect == "gemini":
        reply = _send(route, _json_request(f"{route.base_url}/models?pageSize=1000", _auth_headers(route)), transport, timeout)
        names = [str(item.get("name", "")).removeprefix("models/") for item in reply.get("models", [])
                 if isinstance(item, dict) and "generateContent" in (item.get("supportedGenerationMethods") or [])]
    else:
        if route.provider == "openrouter":
            # OpenRouter lists its models without a key; its key endpoint is what proves the key.
            _send(route, _json_request(f"{route.base_url}/key", _auth_headers(route)), transport, timeout)
        reply = _send(route, _json_request(f"{route.base_url}/models" + ("?limit=1000" if dialect == "anthropic" else ""), _auth_headers(route)), transport, timeout)
        names = [str(item.get("id", "")) for item in reply.get("data", []) if isinstance(item, dict)]
    return sorted({name for name in names if name and len(name) <= 200})[:1000]


def ask(route: Route, system: str, schema: Mapping[str, Any], parts: Sequence[Part], *, transport: Transport = _urllib,
        sleep: Callable[[float], None] = time.sleep, timeout: float = DEFAULT_TIMEOUT, max_tokens: int = 16000) -> tuple[Any, Route]:
    """Like ask_json, and when the chosen model no longer exists, once more with the provider's current model.

    Returns the answer and the route that gave it (its ``model`` is the one actually used).
    """
    try:
        return ask_json(route, system, schema, parts, transport=transport, sleep=sleep, timeout=timeout, max_tokens=max_tokens), route
    except AiError as error:
        if error.code != "MODEL" or route.provider == "custom":
            raise
        try:
            names = list_models(route, transport=transport)
        except AiError:
            raise error from None
        chosen = pick_model(route.provider, names)
        if not chosen or chosen == route.model:
            raise
        fallback = replace(route, model=chosen)
        return ask_json(fallback, system, schema, parts, transport=transport, sleep=sleep, timeout=timeout, max_tokens=max_tokens), fallback
