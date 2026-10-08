"""Provider-scoped Douyin adapter with an opaque browser-session boundary.

No cookie, token, or browser profile is accepted in durable state.  A caller
may inject a session bridge and transport; both are intentionally protocols so
recorded fixtures can exercise the full mapping without contacting Douyin.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from engine.dubflow.download.source_adapter import (
    MediaCandidate,
    SourceError,
    SourceErrorCode,
    SourceIdentity,
    SourceItem,
    SubtitleCandidate,
)
from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, DownloadResult, MediaMaterializer
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.stream_materializer import StreamMaterializer


TICKS_PER_SECOND = 90_000
_AWEME_ID = re.compile(r"^[0-9]{6,32}$")
_SHORT_TOKEN = re.compile(r"^[A-Za-z0-9._-]{2,128}$")
_VIDEO_PATH = re.compile(r"/(?:video/)?([0-9]{6,32})(?:/|$)")
_HOSTS = {"douyin.com", "www.douyin.com", "m.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class DouyinTransport(Protocol):
    def fetch_video(self, source_ref: str, session: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        ...


class BrowserSessionBridge(Protocol):
    def get_opaque_headers(self, provider_id: str) -> Mapping[str, str]:
        ...


class DouyinTransportError(RuntimeError):
    """Transport failure with only safe status metadata."""

    def __init__(self, message: str, *, status: int | None = None, code: str | int | None = None, challenge: bool = False) -> None:
        self.status = status
        self.code = str(code)[:32] if code is not None else None
        self.challenge = challenge
        # The message is deliberately bounded and must be supplied by the
        # transport without request URLs, cookies or response bodies.
        super().__init__(message[:256])


def normalize_source_ref(source_ref: str) -> str:
    """Normalize an aweme id, long video URL, or short-link token."""

    if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 4096:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Douyin source reference is invalid", provider_id="douyin")
    value = source_ref.strip()
    if _AWEME_ID.fullmatch(value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Douyin source URL is invalid", provider_id="douyin") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in _HOSTS or parts.username is not None or parts.password is not None:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "source must be a Douyin video URL or aweme id", provider_id="douyin")
    match = _VIDEO_PATH.search(parts.path)
    if match:
        return match.group(1)
    # Short URLs are resolved by the injected transport.  Keep the token
    # opaque and bounded so it can never become an accidental URL log entry.
    token = parts.path.strip("/").split("/")[0]
    if _SHORT_TOKEN.fullmatch(token):
        return "short-" + token
    raise SourceError(SourceErrorCode.INVALID_INPUT, "Douyin URL does not contain a video id", provider_id="douyin")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} is malformed", provider_id="douyin")
    return value


def _text(value: Any, name: str, *, limit: int = 4096, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} is missing or malformed", provider_id="douyin")
    return value.strip()


def _duration_ticks(value: Any) -> int | None:
    if value is None:
        return None
    try:
        # Douyin payloads commonly use milliseconds; Decimal avoids a binary
        # float rounding decision at a tick boundary.
        milliseconds = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin duration is malformed", provider_id="douyin")
    if not milliseconds.is_finite() or milliseconds < 0:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin duration is outside the valid range", provider_id="douyin")
    ticks = (milliseconds * TICKS_PER_SECOND / Decimal(1000)).to_integral_value(rounding=ROUND_HALF_UP)
    if ticks > (1 << 64) - 1:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin duration exceeds the timeline range", provider_id="douyin")
    return int(ticks)


def _url(value: Any, name: str) -> str:
    text = _text(value, name, limit=4096)
    if text is None:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} is missing", provider_id="douyin")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} is malformed", provider_id="douyin") from error
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username is not None or parts.password is not None:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} is not a URL", provider_id="douyin")
    if port is not None and not 1 <= port <= 65535:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Douyin field {name} has an invalid port", provider_id="douyin")
    return text


def _url_list(value: Any, name: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if isinstance(value, Mapping):
        value = value.get("url_list", value.get("urlList"))
    if not isinstance(value, list) or len(value) > 256:
        return []
    result: list[str] = []
    for item in value:
        try:
            result.append(_url(item, name))
        except SourceError:
            continue
    return result


def _subtitles(data: Mapping[str, Any]) -> tuple[SubtitleCandidate, ...]:
    raw = data.get("subtitle_infos", data.get("subtitle", data.get("captions", [])))
    if isinstance(raw, Mapping):
        raw = raw.get("list", raw.get("items", []))
    if not isinstance(raw, list):
        return ()
    result: list[SubtitleCandidate] = []
    for index, value in enumerate(raw[:256]):
        item = _mapping(value, f"subtitle[{index}]")
        locator = item.get("url", item.get("url_list"))
        urls = _url_list(locator, "subtitle.url")
        if not urls:
            continue
        language = _text(item.get("language", item.get("lang", "und")), "subtitle.language", limit=32) or "und"
        fmt = str(item.get("format", "json")).lower()
        if fmt not in {"srt", "vtt", "ass", "json"}:
            fmt = "json"
        result.append(SubtitleCandidate(f"subtitle-{index}", urls[0], language, fmt))
    return tuple(result)


def _media(data: Mapping[str, Any]) -> tuple[MediaCandidate, ...]:
    # SDK mapping supplies actual codec/protocol roles. Do not treat a
    # video-only format as the provider's historical combined play_addr.
    if "source_streams" in data:
        values = data["source_streams"]
        if not isinstance(values, list) or not 1 <= len(values) <= 256:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin SDK media formats are malformed", provider_id="douyin")
        try:
            return tuple(MediaCandidate(**{**value, "locator": _url(value.get("locator"), "SDK media URL")}) for value in values)
        except (TypeError, SourceError):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin SDK media formats are malformed", provider_id="douyin") from None
    video = _mapping(data.get("video"), "video")
    # play_addr is preferred because it represents the playable rendition;
    # download_addr is retained as a fallback candidate.
    addresses = [video.get("play_addr"), video.get("download_addr")]
    urls: list[str] = []
    for address in addresses:
        for value in _url_list(address, "video.url"):
            if value not in urls:
                urls.append(value)
    if not urls and isinstance(data.get("formats"), list):
        for raw in data["formats"][:256]:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("url"), str):
                continue
            try:
                candidate_url = _url(raw["url"], "format.url")
            except SourceError:
                continue
            if candidate_url not in urls:
                urls.append(candidate_url)
        if urls:
            video = dict(video)
            video["width"] = max((raw.get("width", 0) for raw in data["formats"] if isinstance(raw, Mapping) and type(raw.get("width")) is int), default=None)
            video["height"] = max((raw.get("height", 0) for raw in data["formats"] if isinstance(raw, Mapping) and type(raw.get("height")) is int), default=None)
    if not urls:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin response contains no downloadable media", provider_id="douyin")
    width = video.get("width") if isinstance(video.get("width"), int) else None
    height = video.get("height") if isinstance(video.get("height"), int) else None
    return tuple(MediaCandidate(f"progressive-{index}", value, "progressive", "video/mp4", width, height, True) for index, value in enumerate(urls[:256]))


class DouyinSourceAdapter:
    provider_id = "douyin"

    def __init__(self, transport: DouyinTransport | None = None, session_bridge: BrowserSessionBridge | None = None, *, ytdlp_executable: str | Path | None = None,
                 ytdlp_root: str | Path | None = None, ytdlp_sha256: str | None = None, materializer: MediaMaterializer | None = None,
                 stream_materializer: StreamMaterializer | None = None) -> None:
        if transport is None:
            if ytdlp_executable is None:
                raise ValueError("a Douyin transport or app-owned yt-dlp executable is required")
            transport = YtDlpProviderTransport(self.provider_id, ytdlp_executable, trusted_root=ytdlp_root, expected_sha256=ytdlp_sha256)
        self._transport = transport
        self._session_bridge = session_bridge
        self._materializer = materializer or MediaMaterializer()
        self._stream_materializer = stream_materializer

    @classmethod
    def can_handle(cls, source_ref: str) -> bool:
        try:
            normalize_source_ref(source_ref)
            return True
        except SourceError:
            return False

    def _session(self) -> Mapping[str, str] | None:
        if self._session_bridge is None:
            return None
        try:
            session = self._session_bridge.get_opaque_headers(self.provider_id)
        except Exception as exc:  # bridge failures become a bounded auth error
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin browser session is unavailable", provider_id=self.provider_id, action="authenticate") from exc
        if not isinstance(session, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in session.items()):
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin browser session is invalid", provider_id=self.provider_id, action="authenticate")
        # Copy only into the immediate transport call; never serialize it or
        # include it in SourceError text.
        return dict(session)

    def inspect(self, source_ref: str) -> SourceItem:
        normalized = normalize_source_ref(source_ref)
        session = self._session()
        try:
            payload = self._transport.fetch_video(normalized, session)
        except DouyinTransportError as exc:
            if exc.challenge:
                raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin requires a browser challenge", provider_id=self.provider_id, source_id=normalized, action="complete_challenge") from exc
            if exc.status in {401, 403}:
                raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin authentication is required", provider_id=self.provider_id, source_id=normalized, action="authenticate") from exc
            if exc.status == 429:
                raise SourceError(SourceErrorCode.RATE_LIMITED, "Douyin rate limit reached", provider_id=self.provider_id, source_id=normalized, retryable=True, action="retry_later") from exc
            raise SourceError(SourceErrorCode.NETWORK, "Douyin transport failed", provider_id=self.provider_id, source_id=normalized, retryable=True, action="retry") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise SourceError(SourceErrorCode.NETWORK, "Douyin transport failed", provider_id=self.provider_id, source_id=normalized, retryable=True, action="retry") from exc
        if not isinstance(payload, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin response is not an object", provider_id=self.provider_id, source_id=normalized)
        status = payload.get("status_code", payload.get("code", 0))
        if status in {-106, -100, 10001}:
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin authentication is required", provider_id=self.provider_id, source_id=normalized, action="authenticate")
        if status in {-997, 429, 10002}:
            raise SourceError(SourceErrorCode.RATE_LIMITED, "Douyin request was rate limited", provider_id=self.provider_id, source_id=normalized, retryable=True, action="retry_later")
        if status not in {0, None}:
            message = str(payload.get("status_msg", payload.get("message", ""))).lower()
            if "captcha" in message or "challenge" in message:
                raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Douyin requires a browser challenge", provider_id=self.provider_id, source_id=normalized, action="complete_challenge")
            if "private" in message:
                raise SourceError(SourceErrorCode.PRIVATE, "Douyin video is private", provider_id=self.provider_id, source_id=normalized, action="authenticate")
            if "not found" in message or "deleted" in message:
                raise SourceError(SourceErrorCode.NOT_FOUND, "Douyin video is unavailable", provider_id=self.provider_id, source_id=normalized)
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Douyin response returned an unsupported status", provider_id=self.provider_id, source_id=normalized)
        data = payload.get("data", payload)
        if isinstance(data, Mapping) and isinstance(data.get("item_list"), list):
            if not data["item_list"] or not isinstance(data["item_list"][0], Mapping):
                raise SourceError(SourceErrorCode.NOT_FOUND, "Douyin video is unavailable", provider_id=self.provider_id, source_id=normalized)
            data = data["item_list"][0]
        data = _mapping(data, "data")
        actual_id = data.get("aweme_id", data.get("item_id", normalized))
        if not isinstance(actual_id, str) or not _AWEME_ID.fullmatch(actual_id):
            actual_id = normalized
        title = _text(data.get("desc") or data.get("title"), "desc")
        canonical = f"https://www.douyin.com/video/{actual_id}" if _AWEME_ID.fullmatch(actual_id) else f"https://www.douyin.com/{actual_id}"
        return SourceItem(
            identity=SourceIdentity(self.provider_id, actual_id, canonical),
            title=title or actual_id,
            description=None,
            duration_ticks=_duration_ticks(data.get("duration", data.get("duration_ms"))),
            media_candidates=_media(data),
            subtitle_candidates=_subtitles(data),
        )

    def enumerate_channel(self, channel_id: str, *, cursor: str | None = None, page_size: int = 50):
        raise SourceError(SourceErrorCode.UNSUPPORTED, "Douyin channel enumeration requires the durable enumeration adapter", provider_id=self.provider_id)

    def select_download(self, item: SourceItem) -> MediaCandidate:
        candidates = [candidate for candidate in item.media_candidates if candidate.mime_type.startswith("video/")]
        if not candidates:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "Douyin item has no video download candidate", provider_id=self.provider_id, source_id=item.identity.source_id)
        return max(candidates, key=lambda candidate: ((candidate.width or 0) * (candidate.height or 0), candidate.height or 0, candidate.candidate_id))

    def download(self, item: SourceItem, destination: str | Path, *, candidate: MediaCandidate | None = None, **kwargs: object) -> DownloadResult:
        selected = candidate or self.select_download(item)
        if selected not in item.media_candidates:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "download candidate belongs to another source", provider_id=self.provider_id)
        try:
            audio = None if selected.has_audio else next((value for value in item.media_candidates if value.mime_type.startswith("audio/")), None)
            if self._stream_materializer is not None:
                return self._stream_materializer.download(selected, destination, audio=audio, **kwargs)
            if not selected.has_audio or selected.kind not in {"local", "progressive"}:
                raise DownloadError(DownloadErrorCode.UNSUPPORTED, "selected streams require the app-owned source muxer")
            return self._materializer.download(selected, destination, **kwargs)
        except DownloadError as error:
            mapping = {
                DownloadErrorCode.AUTH_REQUIRED: SourceErrorCode.AUTH_REQUIRED,
                DownloadErrorCode.RATE_LIMITED: SourceErrorCode.RATE_LIMITED,
                DownloadErrorCode.SOURCE_CHANGED: SourceErrorCode.SOURCE_CHANGED,
                DownloadErrorCode.NETWORK: SourceErrorCode.NETWORK,
                DownloadErrorCode.UNSUPPORTED: SourceErrorCode.UNSUPPORTED,
            }
            raise SourceError(mapping.get(error.code, SourceErrorCode.NETWORK), "Douyin media download failed", provider_id=self.provider_id, source_id=item.identity.source_id, retryable=error.retryable, action=error.action) from error


__all__ = ["BrowserSessionBridge", "DouyinSourceAdapter", "DouyinTransportError", "normalize_source_ref"]
