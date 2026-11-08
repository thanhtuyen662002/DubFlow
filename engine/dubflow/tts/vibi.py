"""Vibi.pro voices (ElevenLabs, MiniMax) through the user's own API key (#632).

Vibi.pro resells ElevenLabs and MiniMax speech behind one API
(https://vibi.pro/docs): ``xi-api-key`` on every call, voices listed per
provider, and asynchronous speech: ``POST /v1/text-to-speech/{voice_id}``
answers 202 with a task id, ``GET /v1/history/{id}`` reports it until
``result.audio_url`` holds the MP3.

Everything here sits behind the adapter boundary:

* ``VibiClient``: the HTTP calls, bounded retries and plain Vietnamese errors.
* ``VoiceSettings``: one voice and all of its parameters, validated, with a
  stable identity used for caching (a sentence is never paid for twice).
* ``VibiSpeaker``: the narration ``Speaker`` (Phim tài liệu, products, …).
* ``VibiDubbingEngine``: the dubbing engine, a drop-in for the VieNeu engine:
  natural speech comes from Vibi once per line and the duration fit speeds it
  up with FFmpeg ``atempo`` on this computer.
* ``python -B -m engine.dubflow.tts.vibi``: the desktop's calls (check the key,
  list voices and models, fetch a preview, make a sample). One JSON request on
  stdin, the key in ``DUBFLOW_VIBI_KEY``, one ASCII JSON line on stdout.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.vibi.pro"
KEY_PAGE = "https://vibi.pro/docs"
KEY_ENV = "DUBFLOW_VIBI_KEY"
SAMPLE_RATE = 48_000
PROVIDERS = ("elevenlabs", "minimax")
# Where a provider's voices come from: the ElevenLabs shared library and its
# default (premade) voices; MiniMax system voices and the user's own clones.
SOURCES = {"elevenlabs": ("shared", "default"), "minimax": ("system", "cloned")}
MAX_TEXT = 5_000
MAX_AUDIO_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
POLL_SECONDS = 1.0
TASK_TIMEOUT = 600.0
REQUEST_TIMEOUT = 60.0
RETRIES = 3

# Shown before the key has listed the account's models (and if listing fails).
FALLBACK_MODELS = {
    "elevenlabs": (
        {"model_id": "eleven_multilingual_v2", "name": "Eleven Multilingual v2", "can_use_style": True, "can_use_speaker_boost": True,
         "description": "Giọng tự nhiên nhất, nhiều ngôn ngữ"},
        {"model_id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5", "can_use_style": True, "can_use_speaker_boost": True,
         "description": "Nhanh, rẻ hơn khoảng 50 % credit"},
        {"model_id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5", "can_use_style": False, "can_use_speaker_boost": True,
         "description": "Nhanh nhất, rẻ nhất"},
        {"model_id": "eleven_v3", "name": "Eleven v3", "can_use_style": False, "can_use_speaker_boost": False,
         "description": "Biểu cảm mạnh nhất"},
    ),
    "minimax": (
        {"model_id": "speech-2.8-hd", "name": "Speech 2.8 HD", "description": "Chất lượng cao nhất"},
        {"model_id": "speech-2.8-turbo", "name": "Speech 2.8 Turbo", "description": "Nhanh, tiết kiệm"},
    ),
}

# Oscar's settings for English "Mega Projects" documentaries: lively but not
# theatrical (stability under 70 % sounds like a newsreader), clear over a
# cinematic bed, a touch of drama.
MEGA_PROJECTS = {"provider": "elevenlabs", "model_id": "eleven_turbo_v2_5", "stability": 0.45, "similarity_boost": 0.78,
                 "style": 0.15, "use_speaker_boost": True, "speed": 1.0}


class VibiError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.condition = message
        self.retryable = retryable


def _number(value: Any, name: str, low: float, high: float, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise VibiError("SETTINGS_INVALID", f"{name} không hợp lệ")
    number = float(value)
    if not low <= number <= high:
        raise VibiError("SETTINGS_INVALID", f"{name} phải từ {low:g} đến {high:g}")
    return round(number, 3)


def _word(value: Any, name: str, limit: int, pattern: str = r"[A-Za-z0-9_.:()\- ]+") -> str:
    if type(value) is not str or not value.strip() or len(value) > limit or not re.fullmatch(pattern, value.strip()):
        raise VibiError("SETTINGS_INVALID", f"{name} không hợp lệ")
    return value.strip()


@dataclass(frozen=True)
class VoiceSettings:
    """One Vibi voice with every parameter the user chose."""

    provider: str
    voice_id: str
    model_id: str
    language_code: str
    voice_name: str = ""
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True
    speed: float = 1.0
    pitch: int = 0
    vol: float = 1.0

    @classmethod
    def from_mapping(cls, value: Any) -> "VoiceSettings":
        if not isinstance(value, Mapping):
            raise VibiError("SETTINGS_INVALID", "Cài đặt giọng Vibi không hợp lệ")
        provider = value.get("provider", "elevenlabs")
        if provider not in PROVIDERS:
            raise VibiError("SETTINGS_INVALID", "Nhà cung cấp giọng phải là ElevenLabs hoặc MiniMax")
        voice_id = _word(value.get("voice_id"), "Mã giọng", 128)
        model_id = _word(value.get("model_id") or FALLBACK_MODELS[provider][0]["model_id"], "Model", 80)
        language = _word(value.get("language_code") or ("en" if provider == "elevenlabs" else "English"), "Ngôn ngữ", 60)
        name = value.get("voice_name") or ""
        name = " ".join(str(name).split())[:120] if isinstance(name, str) else ""
        boost = value.get("use_speaker_boost", True)
        if type(boost) is not bool:
            raise VibiError("SETTINGS_INVALID", "Speaker boost không hợp lệ")
        if provider == "elevenlabs":
            speed = _number(value.get("speed"), "Tốc độ", 0.7, 1.2, 1.0)
        else:
            speed = _number(value.get("speed"), "Tốc độ", 0.5, 2.0, 1.0)
        pitch = value.get("pitch", 0)
        if isinstance(pitch, bool) or not isinstance(pitch, (int, float)) or int(pitch) != pitch or not -12 <= pitch <= 12:
            raise VibiError("SETTINGS_INVALID", "Cao độ phải là số nguyên từ -12 đến 12")
        return cls(provider, voice_id, model_id, language, name,
                   _number(value.get("stability"), "Stability", 0.0, 1.0, 0.5),
                   _number(value.get("similarity_boost"), "Similarity", 0.0, 1.0, 0.75),
                   _number(value.get("style"), "Style", 0.0, 1.0, 0.0),
                   boost, speed, int(pitch), _number(value.get("vol"), "Âm lượng", 0.01, 10.0, 1.0))

    def voice_settings(self) -> dict[str, Any]:
        if self.provider == "minimax":
            return {"speed": self.speed, "pitch": self.pitch, "vol": self.vol}
        return {"stability": self.stability, "similarity_boost": self.similarity_boost, "style": self.style,
                "use_speaker_boost": self.use_speaker_boost, "speed": self.speed}

    def request(self, text: str) -> dict[str, Any]:
        return {"text": text, "model_id": self.model_id, "provider": self.provider, "language_code": self.language_code,
                "voice_settings": self.voice_settings()}

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "voice_id": self.voice_id, "voice_name": self.voice_name, "model_id": self.model_id,
                "language_code": self.language_code, **self.voice_settings()}

    def identity(self) -> str:
        """What the audio depends on (not the display name)."""
        value = {"provider": self.provider, "voice_id": self.voice_id, "model_id": self.model_id,
                 "language_code": self.language_code, "voice_settings": self.voice_settings()}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def label(self) -> str:
        provider = "ElevenLabs" if self.provider == "elevenlabs" else "MiniMax"
        return f"{self.voice_name or self.voice_id} ({provider})"


def _http_error(status: int, body: bytes) -> VibiError:
    try:
        detail = json.loads(body.decode("utf-8", "replace"))
        text = str(detail.get("message") or detail.get("error") or "") if isinstance(detail, dict) else ""
    except ValueError:
        text = ""
    text = " ".join(text.split())[:200]
    if status in (401, 403):
        return VibiError("KEY_INVALID", "Khóa Vibi.pro không đúng hoặc đã bị đổi; hãy dán lại khóa trong Cài đặt")
    if status == 402:
        return VibiError("NO_CREDITS", "Tài khoản Vibi.pro đã hết credit; hãy nạp thêm rồi thử lại")
    if status == 404:
        return VibiError("NOT_FOUND", f"Vibi.pro không tìm thấy mục này{': ' + text if text else ''}")
    if status == 429:
        return VibiError("RATE_LIMITED", "Vibi.pro đang giới hạn số yêu cầu; thử lại sau ít phút", retryable=True)
    if status >= 500:
        return VibiError("SERVER", f"Máy chủ Vibi.pro đang lỗi ({status}); thử lại sau", retryable=True)
    return VibiError("REQUEST", f"Vibi.pro từ chối yêu cầu ({status}){': ' + text if text else ''}")


Opener = Callable[..., Any]


class VibiClient:
    def __init__(self, key: str, *, base_url: str = BASE_URL, opener: Opener | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        key = (key or "").strip()
        if not key:
            raise VibiError("KEY_MISSING", "Chưa có khóa Vibi.pro; hãy dán khóa trong Cài đặt › Giọng đọc Vibi.pro")
        if len(key) > 512 or not all(33 <= ord(char) < 127 for char in key):
            raise VibiError("KEY_INVALID", "Khóa Vibi.pro không đúng dạng; hãy dán lại nguyên khóa")
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.clock = clock

    def _open(self, request: urllib.request.Request, limit: int) -> bytes:
        last: VibiError | None = None
        for attempt in range(RETRIES):
            try:
                with self.opener(request, timeout=REQUEST_TIMEOUT) as response:
                    payload = response.read(limit + 1)
                if len(payload) > limit:
                    raise VibiError("TOO_LARGE", "Vibi.pro trả về dữ liệu quá lớn")
                return payload
            except urllib.error.HTTPError as error:
                last = _http_error(error.code, error.read(64 * 1024) if hasattr(error, "read") else b"")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                if isinstance(error, VibiError):
                    raise
                last = VibiError("NETWORK", f"Không kết nối được Vibi.pro ({type(error).__name__}); hãy kiểm tra mạng", retryable=True)
            if not last.retryable:
                raise last
            self.sleep(min(8.0, 1.5 * 2 ** attempt))
        assert last is not None
        raise last

    def call(self, method: str, path: str, params: Mapping[str, Any] | None = None, body: Any = None) -> Any:
        query = ""
        if params:
            pairs = []
            for key, value in params.items():
                if value is None or value == "" or value == []:
                    continue
                for item in value if isinstance(value, (list, tuple)) else (value,):
                    pairs.append((key, str(item).lower() if isinstance(item, bool) else str(item)))
            query = "?" + urllib.parse.urlencode(pairs) if pairs else ""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path + query, data=data, method=method,
                                         headers={"xi-api-key": self.key, "Accept": "application/json",
                                                  "User-Agent": "DubFlow", **({"Content-Type": "application/json"} if data else {})})
        payload = self._open(request, MAX_JSON_BYTES)
        try:
            return json.loads(payload.decode("utf-8")) if payload.strip() else {}
        except ValueError as error:
            raise VibiError("BAD_RESPONSE", "Vibi.pro trả về dữ liệu không đọc được") from error

    # -- account, models, voices -------------------------------------------------

    def me(self) -> dict[str, Any]:
        value = self.call("GET", "/v1/auth/me")
        if not isinstance(value, dict):
            raise VibiError("BAD_RESPONSE", "Vibi.pro trả về dữ liệu không đọc được")
        return {"email": str(value.get("email") or "")[:200], "name": str(value.get("name") or "")[:200],
                "credit_balance": value.get("credit_balance") if isinstance(value.get("credit_balance"), (int, float)) else None}

    def models(self, provider: str) -> list[dict[str, Any]]:
        if provider not in PROVIDERS:
            raise VibiError("SETTINGS_INVALID", "Nhà cung cấp giọng không hợp lệ")
        value = self.call("GET", "/v1/models", {"provider": None if provider == "elevenlabs" else provider})
        items = value if isinstance(value, list) else value.get("models", []) if isinstance(value, dict) else []
        models = []
        for item in items:
            if not isinstance(item, dict) or type(item.get("model_id")) is not str:
                continue
            if provider == "elevenlabs" and item.get("can_do_text_to_speech") is False:
                continue
            models.append({"model_id": item["model_id"][:80], "name": str(item.get("name") or item["model_id"])[:80],
                           "description": " ".join(str(item.get("description") or "").split())[:200],
                           "can_use_style": item.get("can_use_style", provider == "elevenlabs") is not False,
                           "can_use_speaker_boost": item.get("can_use_speaker_boost", provider == "elevenlabs") is not False,
                           "languages": [str(lang.get("language_id") if isinstance(lang, dict) else lang)[:20]
                                         for lang in item.get("languages", []) if isinstance(lang, (dict, str))][:80]})
        return models or [dict(model) for model in FALLBACK_MODELS[provider]]

    def voices(self, provider: str, source: str, *, page: int = 0, page_size: int = 30, search: str = "",
               gender: str = "", language: str = "", accent: str = "", age: str = "", use_case: str = "",
               sort: str = "") -> dict[str, Any]:
        """One page of voices, the same shape for every provider; ``page`` counts from 0."""
        if provider not in PROVIDERS or source not in SOURCES[provider]:
            raise VibiError("SETTINGS_INVALID", "Nguồn giọng không hợp lệ")
        page = max(0, int(page))
        page_size = max(1, min(100, int(page_size)))
        search = " ".join(str(search).split())[:100]
        if provider == "elevenlabs":
            if source == "shared":
                value = self.call("GET", "/v1/shared-voices", {
                    "page_size": page_size, "page": page, "search": search, "gender": gender.lower(), "age": age,
                    "accent": accent, "required_languages": language, "use_cases": [use_case] if use_case else None,
                    "sort": sort or "trending"})
            else:
                value = self.call("GET", "/v1/default-voices", {"page_size": page_size, "search": search})
            items = value.get("voices", []) if isinstance(value, dict) else []
            voices = [_eleven_voice(item) for item in items if isinstance(item, dict) and item.get("voice_id")]
            more = bool(value.get("has_more")) if isinstance(value, dict) and source == "shared" else False
            if source == "default":
                voices = [voice for voice in voices if (not gender or voice["gender"].lower() == gender.lower())
                          and (not accent or accent.lower() in voice["accent"].lower())]
            return {"voices": voices, "has_more": more, "total": None}
        if source == "system":
            value = self.call("GET", "/v1/minimax/system-voices", {
                "page": page + 1, "page_size": page_size, "search": search, "gender": gender.capitalize() if gender else "",
                "language": language, "accent": accent, "age": age, "use_cases": use_case})
            items = value.get("voice_list", []) if isinstance(value, dict) else []
            voices = [_minimax_voice(item) for item in items if isinstance(item, dict)]
            return {"voices": [voice for voice in voices if voice["voice_id"]], "has_more": bool(value.get("has_more")) if isinstance(value, dict) else False,
                    "total": value.get("total") if isinstance(value, dict) and isinstance(value.get("total"), int) else None}
        value = self.call("GET", "/v1/minimax/voices")
        items = value.get("voices", []) if isinstance(value, dict) else []
        voices = [_minimax_clone(item) for item in items if isinstance(item, dict) and item.get("status", "done") == "done"]
        if search:
            voices = [voice for voice in voices if search.casefold() in voice["name"].casefold()]
        return {"voices": voices[page * page_size:(page + 1) * page_size], "has_more": len(voices) > (page + 1) * page_size,
                "total": len(voices)}

    # -- speech --------------------------------------------------------------------

    def speak(self, settings: VoiceSettings, text: str, *, cancelled: Callable[[], bool] | None = None) -> bytes:
        """The MP3 of ``text`` in ``settings``' voice (one task, polled to the end)."""
        text = text.strip()
        if not text:
            raise VibiError("TEXT_EMPTY", "Câu đọc trống")
        if len(text) > MAX_TEXT:
            raise VibiError("TEXT_TOO_LONG", f"Câu đọc dài quá {MAX_TEXT} ký tự")
        created = self.call("POST", f"/v1/text-to-speech/{urllib.parse.quote(settings.voice_id, safe='')}", body=settings.request(text))
        task = created.get("id") if isinstance(created, dict) else None
        if type(task) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task):
            raise VibiError("BAD_RESPONSE", "Vibi.pro không trả về mã tác vụ đọc")
        deadline = self.clock() + TASK_TIMEOUT
        delay = POLL_SECONDS
        while True:
            if cancelled is not None and cancelled():
                raise VibiError("CANCELLED", "Đã hủy")
            status = self.call("GET", f"/v1/history/{task}")
            state = status.get("status") if isinstance(status, dict) else None
            if state == "completed":
                result = status.get("result") if isinstance(status.get("result"), dict) else {}
                url = result.get("audio_url")
                if type(url) is not str:
                    raise VibiError("BAD_RESPONSE", "Vibi.pro không trả về file giọng đọc")
                return self.download(url)
            if state == "failed":
                reason = " ".join(str(status.get("error") or "").split())[:200]
                raise VibiError("TASK_FAILED", f"Vibi.pro không đọc được câu này{': ' + reason if reason else ''}")
            if state not in ("pending", "processing"):
                raise VibiError("BAD_RESPONSE", "Vibi.pro trả về trạng thái không rõ")
            if self.clock() > deadline:
                raise VibiError("TIMEOUT", "Vibi.pro đọc quá lâu (hơn 10 phút); hãy thử lại", retryable=True)
            self.sleep(delay)
            delay = min(3.0, delay * 1.3)

    def download(self, url: str) -> bytes:
        """An audio file Vibi named (HTTPS only; the key goes only to Vibi's own host)."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise VibiError("BAD_RESPONSE", "Địa chỉ file giọng đọc không an toàn")
        own = parts.hostname == urllib.parse.urlsplit(self.base_url).hostname
        request = urllib.request.Request(url, headers={"User-Agent": "DubFlow", **({"xi-api-key": self.key} if own else {})})
        payload = self._open(request, MAX_AUDIO_BYTES)
        if len(payload) < 64:
            raise VibiError("BAD_RESPONSE", "File giọng đọc trống")
        return payload


def _text_of(value: Any, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _https(value: Any) -> str:
    return value if isinstance(value, str) and value.startswith("https://") and len(value) <= 2000 else ""


def _eleven_voice(item: Mapping[str, Any]) -> dict[str, Any]:
    languages = []
    for entry in item.get("verified_languages") or []:
        if isinstance(entry, dict) and entry.get("language"):
            languages.append(_text_of(entry["language"], 20))
    tags = [tag for tag in (_text_of(item.get("descriptive"), 40), _text_of(item.get("use_case"), 40).replace("_", " ")) if tag]
    return {"provider": "elevenlabs", "voice_id": _text_of(item.get("voice_id"), 128), "name": _text_of(item.get("name"), 120) or "Giọng",
            "gender": _text_of(item.get("gender"), 20), "age": _text_of(item.get("age"), 20).replace("_", " "),
            "accent": _text_of(item.get("accent"), 40), "language": _text_of(item.get("language"), 20),
            "languages": sorted(set(languages))[:12], "description": _text_of(item.get("description"), 300),
            "preview_url": _https(item.get("preview_url")), "image_url": _https(item.get("image_url")), "tags": tags,
            "popularity": item.get("cloned_by_count") if isinstance(item.get("cloned_by_count"), int) else None}


def _minimax_voice(item: Mapping[str, Any]) -> dict[str, Any]:
    tags = [_text_of(tag, 40) for tag in item.get("tag_list") or [] if _text_of(tag, 40)][:8]
    gender = next((tag for tag in tags if tag.lower() in ("male", "female")), "")
    voice_id = _text_of(item.get("uniq_id"), 128) or _text_of(item.get("voice_id"), 128)
    return {"provider": "minimax", "voice_id": voice_id, "name": _text_of(item.get("voice_name"), 120) or voice_id,
            "gender": gender, "age": "", "accent": "", "language": "", "languages": [],
            "description": _text_of(item.get("description"), 300), "preview_url": _https(item.get("sample_audio")),
            "image_url": _https(item.get("cover_url")), "tags": [tag for tag in tags if tag != gender], "popularity": None}


def _minimax_clone(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"provider": "minimax", "voice_id": _text_of(item.get("id"), 128), "name": _text_of(item.get("voice_name"), 120) or "Giọng nhân bản",
            "gender": _text_of(item.get("gender"), 20), "age": "", "accent": "", "language": _text_of(item.get("language_tag"), 40),
            "languages": [], "description": "Giọng nhân bản của bạn", "preview_url": _https(item.get("sample_audio_url")),
            "image_url": _https(item.get("cover_url")), "tags": ["Nhân bản"], "popularity": None}


# -- audio ---------------------------------------------------------------------------

def decode(ffmpeg: Path | str, encoded: bytes, *, rate: int = SAMPLE_RATE, tempo: float = 1.0) -> array:
    """Mono 16-bit samples of an MP3/WAV at ``rate``, optionally ``tempo`` times faster (FFmpeg atempo)."""
    filters = []
    remaining = float(tempo)
    if not 0.5 <= remaining <= 4.0:
        raise VibiError("TEMPO_INVALID", "Tốc độ khớp thời lượng không hợp lệ")
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-6:
        filters.append(f"atempo={remaining:.6f}")
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-i", "pipe:0",
            *(["-af", ",".join(filters)] if filters else []), "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(argv, input=encoded, capture_output=True, timeout=300, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VibiError("DECODE_FAILED", f"Không giải mã được giọng đọc ({type(error).__name__})") from error
    if result.returncode != 0 or len(result.stdout) < 2:
        raise VibiError("DECODE_FAILED", "Không giải mã được giọng đọc: " + result.stderr.decode("utf-8", "replace")[-200:].strip())
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _floats(samples: array) -> list[float]:
    return [value / 32768.0 for value in samples]


class AudioCache:
    """Vibi audio on disk, by voice identity and text: a sentence is paid for once."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path(self, settings: VoiceSettings, text: str) -> Path:
        key = hashlib.sha256(json.dumps([settings.identity(), text.strip()], ensure_ascii=False).encode("utf-8")).hexdigest()
        return self.root / key[:2] / f"{key}.mp3"

    def fetch(self, client: VibiClient, settings: VoiceSettings, text: str, cancelled: Callable[[], bool] | None = None) -> bytes:
        path = self.path(settings, text)
        try:
            return path.read_bytes()
        except OSError:
            pass
        audio = client.speak(settings, text, cancelled=cancelled)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_bytes(audio)
        partial.replace(path)
        return audio


def default_cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "DubFlow" / "vibi-cache"


class VibiSpeaker:
    """A narration ``Speaker``: float samples at 48 kHz for each text."""

    sample_rate = SAMPLE_RATE

    def __init__(self, settings: VoiceSettings, key: str, ffmpeg: Path | str, *, cache_root: Path | str | None = None,
                 client: VibiClient | None = None, cancelled: Callable[[], bool] | None = None) -> None:
        self.settings = settings
        self.client = client or VibiClient(key)
        self.ffmpeg = ffmpeg
        self.cache = AudioCache(cache_root or default_cache_root())
        self.cancelled = cancelled

    def speak(self, text: str) -> Sequence[float]:
        return _floats(decode(self.ffmpeg, self.cache.fetch(self.client, self.settings, text, self.cancelled)))

    def close(self) -> None:
        pass


class _VibiSpeech:
    """The ``generate(text, sid, speed)`` shape the VieNeu engine drives, backed by Vibi."""

    def __init__(self, client: VibiClient, settings: VoiceSettings, cache: AudioCache, ffmpeg: Path | str) -> None:
        self.client, self.settings, self.cache, self.ffmpeg = client, settings, cache, ffmpeg
        self.warnings: list[str] = []
        self._natural: dict[str, bytes] = {}

    def generate(self, text: str, sid: int = 0, speed: float = 1.0) -> Any:
        encoded = self._natural.get(text)
        if encoded is None:
            encoded = self.cache.fetch(self.client, self.settings, text)
            self._natural = {text: encoded}  # Only the line being fitted stays in memory.
        return SimpleNamespace(samples=_floats(decode(self.ffmpeg, encoded, tempo=float(speed))), sample_rate=SAMPLE_RATE,
                               warnings=())

    def close(self) -> None:
        self._natural = {}


def dubbing_engine(settings: VoiceSettings, key: str, ffmpeg: Path | str, *, cache_root: Path | str | None = None,
                   client: VibiClient | None = None):
    """``(pack, voice, engine)`` that run_b2_audio drives exactly like a VieNeu voice."""
    from .adapter import EngineCapabilities, EngineHealth, VoiceProfile
    from .vieneu import ENGINE_ID, VieNeuVietnameseTtsEngine, VieNeuVoicePack

    identity = settings.identity()
    root = Path(cache_root or default_cache_root())
    # The voice describes the producer of the audio; the user's own account pays
    # for it. ``vibi-<hash>`` keeps every setting in checkpoint/cache identities.
    pack = VieNeuVoicePack(root, SAMPLE_RATE, 1, "sha256:" + identity, "sha256:" + identity, "vibi-pro-user-account",
                           f"vibi-{identity[:16]}", "1", f"vibi-{settings.provider}-{settings.model_id}"[:200], settings.provider,
                           voice_name=settings.voice_id)
    voice = VoiceProfile(voice_id=pack.voice_id, voice_version="1", language="vi", display_name=settings.label()[:200],
                         model_id=pack.model_id, model_version=settings.provider, model_hash=pack.model_hash,
                         manifest_hash=pack.manifest_hash, license_id=pack.license_id, default=True)
    speech = _VibiSpeech(client or VibiClient(key), settings, AudioCache(root), ffmpeg)

    class VibiDubbingEngine(VieNeuVietnameseTtsEngine):
        def __init__(self) -> None:
            super().__init__(pack, ffmpeg_path=ffmpeg)
            self._tts = speech

        def capabilities(self):
            return EngineCapabilities(ENGINE_ID, sample_rates=(SAMPLE_RATE,), deterministic=False)

        def healthcheck(self, requested):
            if requested.model_hash != pack.model_hash or requested.voice_id != pack.voice_id:
                return EngineHealth(False, "VOICE_PACK_ID_MISMATCH", "requested voice differs from the chosen Vibi voice")
            return EngineHealth(True)

        def close(self) -> None:
            speech.close()

    return pack, voice, VibiDubbingEngine()


# -- the desktop's calls ------------------------------------------------------------------

def _safe_out(value: Any) -> Path:
    if type(value) is not str or not value or len(value) > 1024:
        raise VibiError("REQUEST", "Đường dẫn ra không hợp lệ")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() not in (".mp3", ".wav"):
        raise VibiError("REQUEST", "Đường dẫn ra không hợp lệ")
    return path


SAMPLE_TEXT = {
    "en": "This bridge didn't just cost millions... it swallowed a staggering — twenty billion dollars.",
    "vi": "Xin chào, mình là giọng lồng tiếng của DubFlow. Bạn nghe thử rồi chọn giọng hợp với video nhé.",
}


def handle(request: Mapping[str, Any], key: str, *, client: VibiClient | None = None, ffmpeg: Path | str | None = None) -> dict[str, Any]:
    action = request.get("action")
    if action == "presets":
        return {"ok": True, "presets": {"mega_projects": MEGA_PROJECTS}, "models": FALLBACK_MODELS, "key_page": KEY_PAGE}
    client = client or VibiClient(key)
    if action == "me":
        return {"ok": True, **client.me()}
    if action == "models":
        return {"ok": True, "models": client.models(str(request.get("provider", "")))}
    if action == "voices":
        filters = request.get("filters") if isinstance(request.get("filters"), dict) else {}
        page = request.get("page", 0)
        page = page if type(page) is int and 0 <= page <= 1000 else 0
        result = client.voices(str(request.get("provider", "")), str(request.get("source", "")), page=page,
                               **{name: str(filters.get(name) or "")[:60] for name in ("search", "gender", "language", "accent", "age", "use_case", "sort")})
        return {"ok": True, **result}
    if action == "preview":
        out = _safe_out(request.get("out"))
        url = _https(request.get("url"))
        if not url:
            raise VibiError("REQUEST", "Giọng này không có bản nghe thử")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(client.download(url))
        return {"ok": True, "path": str(out)}
    if action == "sample":
        settings = VoiceSettings.from_mapping(request.get("settings"))
        out = _safe_out(request.get("out"))
        text = request.get("text")
        if type(text) is not str or not text.strip():
            text = SAMPLE_TEXT["vi" if settings.language_code.lower() in ("vi", "vietnamese") else "en"]
        audio = AudioCache(default_cache_root()).fetch(client, settings, text[:400])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        return {"ok": True, "path": str(out)}
    raise VibiError("REQUEST", "Yêu cầu Vibi không hợp lệ")


def main(argv: Sequence[str] | None = None) -> int:
    key = os.environ.pop(KEY_ENV, "")
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise VibiError("REQUEST", "Yêu cầu quá lớn")
        request = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(request, dict):
            raise VibiError("REQUEST", "Yêu cầu Vibi không hợp lệ")
        result = handle(request, key)
    except VibiError as error:
        result = {"ok": False, "code": error.code, "message": error.message}
    except (ValueError, OSError) as error:
        result = {"ok": False, "code": type(error).__name__, "message": f"Lỗi Vibi.pro: {str(error)[:200]}"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
