"""Provider-scoped Bilibili adapter.

The adapter deliberately accepts an injected transport.  Production code can
provide an HTTP/session implementation, while required CI uses a recorded
transport with no network access.  Bilibili response quirks are translated at
this boundary into the versioned SourceAdapter contract; generic downloader
code never needs provider-specific branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from engine.dubflow.download.source_adapter import (
    MediaCandidate,
    SourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceIdentity,
    SourceItem,
    SubtitleCandidate,
)


TICKS_PER_SECOND = 90_000
_BVID = re.compile(r"^BV[0-9A-Za-z]{6,32}$")
_AVID = re.compile(r"^(?:av)?[0-9]{1,20}$", re.IGNORECASE)
_VIDEO_PATH = re.compile(r"/(?:video/)?(BV[0-9A-Za-z]{6,32}|av[0-9]{1,20})(?:/|$)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv"}


class BilibiliTransport(Protocol):
    def fetch_video(self, source_ref: str) -> Mapping[str, Any]:
        ...


class BilibiliTransportError(RuntimeError):
    """Transport failure that carries an HTTP/API status without secrets."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        # Never retain or expose a URL, cookie, authorization header or body.
        super().__init__(message[:256])


@dataclass(frozen=True)
class BilibiliDownloadChoice:
    """A selected metadata candidate for the caller's downloader."""

    source_id: str
    candidate: MediaCandidate


def normalize_source_ref(source_ref: str) -> str:
    """Return a stable BVID/AV source id from a URL or provider id."""

    if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 4096:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili source reference is invalid", provider_id="bilibili")
    value = source_ref.strip()
    if _BVID.fullmatch(value):
        return value
    if _AVID.fullmatch(value):
        return "av" + value.lower().removeprefix("av")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili source URL is invalid", provider_id="bilibili") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in _HOSTS or parts.username is not None or parts.password is not None:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "source must be a Bilibili video URL or BVID/AV id", provider_id="bilibili")
    match = _VIDEO_PATH.search(parts.path)
    if match is None:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili URL does not contain a video id", provider_id="bilibili")
    value = match.group(1)
    return value if value.upper().startswith("BV") else "av" + value.lower().removeprefix("av")


def _text(value: Any, name: str, *, limit: int = 4096, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is missing or malformed", provider_id="bilibili")
    return value.strip()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is malformed", provider_id="bilibili")
    return value


def _decimal_ticks(value: Any) -> int | None:
    if value is None:
        return None
    try:
        seconds = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili duration is malformed", provider_id="bilibili")
    if not seconds.is_finite() or seconds < 0:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili duration is outside the valid range", provider_id="bilibili")
    ticks = (seconds * TICKS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_UP)
    if ticks > (1 << 64) - 1:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili duration exceeds the timeline range", provider_id="bilibili")
    return int(ticks)


def _https_url(value: Any, name: str) -> str:
    text = _text(value, name, limit=4096)
    if text is None:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is missing", provider_id="bilibili")
    if text.startswith("//"):
        text = "https:" + text
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is not a URL", provider_id="bilibili")
    return text


def _subtitle_candidates(data: Mapping[str, Any]) -> tuple[SubtitleCandidate, ...]:
    subtitle = data.get("subtitle")
    if subtitle is None:
        return ()
    listing = _mapping(subtitle, "subtitle").get("list", [])
    if not isinstance(listing, list) or len(listing) > 256:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili subtitle list is malformed", provider_id="bilibili")
    result: list[SubtitleCandidate] = []
    for index, raw in enumerate(listing):
        item = _mapping(raw, f"subtitle.list[{index}]")
        locator = item.get("subtitle_url", item.get("url"))
        if locator is None:
            continue
        language = _text(item.get("lan", item.get("language", "und")), "subtitle.language", limit=32)
        fmt = str(item.get("format", "json")).lower()
        if fmt not in {"srt", "vtt", "ass", "json"}:
            fmt = "json"
        result.append(SubtitleCandidate(f"subtitle-{index}", _https_url(locator, "subtitle.url"), language or "und", fmt))
    return tuple(result)


def _media_candidates(data: Mapping[str, Any]) -> tuple[MediaCandidate, ...]:
    result: list[MediaCandidate] = []
    dash = data.get("dash")
    if isinstance(dash, Mapping):
        videos = dash.get("video", [])
        if not isinstance(videos, list) or len(videos) > 256:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili DASH video list is malformed", provider_id="bilibili")
        for index, raw in enumerate(videos):
            item = _mapping(raw, f"dash.video[{index}]")
            locator = item.get("baseUrl", item.get("base_url", item.get("url")))
            if locator is None:
                continue
            result.append(
                MediaCandidate(
                    f"dash-video-{index}",
                    _https_url(locator, "dash.video.url"),
                    "dash",
                    "video/mp4",
                    int(item["width"]) if isinstance(item.get("width"), int) else None,
                    int(item["height"]) if isinstance(item.get("height"), int) else None,
                    False,
                )
            )
        audios = dash.get("audio", [])
        if isinstance(audios, list):
            for index, raw in enumerate(audios[:256]):
                item = _mapping(raw, f"dash.audio[{index}]")
                locator = item.get("baseUrl", item.get("base_url", item.get("url")))
                if locator is not None:
                    result.append(MediaCandidate(f"dash-audio-{index}", _https_url(locator, "dash.audio.url"), "dash", "audio/mp4", has_audio=True))
    progressive = data.get("durl", data.get("download_url"))
    if isinstance(progressive, list):
        for index, raw in enumerate(progressive[:256]):
            item = _mapping(raw, f"durl[{index}]")
            locator = item.get("url", item.get("baseUrl"))
            if locator is not None:
                result.append(MediaCandidate(f"progressive-{index}", _https_url(locator, "durl.url"), "progressive", "video/mp4", has_audio=True))
    elif progressive is not None:
        result.append(MediaCandidate("progressive-0", _https_url(progressive, "download_url"), "progressive", "video/mp4", has_audio=True))
    if not result:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili response contains no downloadable media", provider_id="bilibili")
    return tuple(result)


class BilibiliSourceAdapter:
    provider_id = "bilibili"

    def __init__(self, transport: BilibiliTransport) -> None:
        self._transport = transport

    def inspect(self, source_ref: str) -> SourceItem:
        source_id = normalize_source_ref(source_ref)
        try:
            payload = self._transport.fetch_video(source_id)
        except BilibiliTransportError as exc:
            status = exc.status
            if status in {401, 403}:
                raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Bilibili authentication is required", provider_id=self.provider_id, source_id=source_id, action="authenticate") from exc
            if status == 429:
                raise SourceError(SourceErrorCode.RATE_LIMITED, "Bilibili rate limit reached", provider_id=self.provider_id, source_id=source_id, retryable=True, action="retry_later") from exc
            raise SourceError(SourceErrorCode.NETWORK, "Bilibili transport failed", provider_id=self.provider_id, source_id=source_id, retryable=True, action="retry") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise SourceError(SourceErrorCode.NETWORK, "Bilibili transport failed", provider_id=self.provider_id, source_id=source_id, retryable=True, action="retry") from exc
        if not isinstance(payload, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili response is not an object", provider_id=self.provider_id, source_id=source_id)
        code = payload.get("code", 0)
        if code in {-101, -400}:
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "Bilibili authentication is required", provider_id=self.provider_id, source_id=source_id, action="authenticate")
        if code in {-412, -352}:
            raise SourceError(SourceErrorCode.RATE_LIMITED, "Bilibili request was rate limited or challenged", provider_id=self.provider_id, source_id=source_id, retryable=True, action="retry_later")
        if code in {-404, -403}:
            raise SourceError(SourceErrorCode.NOT_FOUND, "Bilibili video is unavailable", provider_id=self.provider_id, source_id=source_id)
        if code not in {0, None}:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili API returned an unsupported error", provider_id=self.provider_id, source_id=source_id)
        data = _mapping(payload.get("data"), "data")
        bvid = data.get("bvid")
        aid = data.get("aid")
        actual_id = str(bvid) if isinstance(bvid, str) and _BVID.fullmatch(bvid) else ("av" + str(aid) if isinstance(aid, int) and aid > 0 else source_id)
        title = _text(data.get("title"), "title")
        description = _text(data.get("desc"), "desc", limit=16_384, required=False)
        canonical = f"https://www.bilibili.com/video/{actual_id}"
        return SourceItem(
            identity=SourceIdentity(self.provider_id, actual_id, canonical),
            title=title or actual_id,
            description=description,
            duration_ticks=_decimal_ticks(data.get("duration")),
            media_candidates=_media_candidates(data),
            subtitle_candidates=_subtitle_candidates(data),
        )

    def select_download(self, item: SourceItem, *, prefer_progressive: bool = False) -> BilibiliDownloadChoice:
        """Choose a video candidate without logging or downloading credentials."""

        candidates = [candidate for candidate in item.media_candidates if candidate.mime_type.startswith("video/")]
        if not candidates:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "Bilibili item has no video download candidate", provider_id=self.provider_id, source_id=item.identity.source_id)
        candidates.sort(key=lambda candidate: (candidate.kind != ("progressive" if prefer_progressive else "dash"), candidate.candidate_id))
        return BilibiliDownloadChoice(item.identity.source_id, candidates[0])


__all__ = ["BilibiliDownloadChoice", "BilibiliSourceAdapter", "BilibiliTransportError", "normalize_source_ref"]
