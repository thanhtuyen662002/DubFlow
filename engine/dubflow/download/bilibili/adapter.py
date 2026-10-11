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
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qsl, urlsplit

from engine.dubflow.download.source_adapter import (
    MediaCandidate,
    SourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceIdentity,
    SourceItem,
    SourcePage,
    SubtitleCandidate,
)
from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, DownloadResult, MediaMaterializer
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.sessions import ProtectedSessionBridge
from engine.dubflow.download.stream_materializer import StreamMaterializer


TICKS_PER_SECOND = 90_000
_BVID = re.compile(r"^BV[0-9A-Za-z]{6,32}$")
_AVID = re.compile(r"^(?:av)?[0-9]{1,20}$", re.IGNORECASE)
_PART_REF = re.compile(r"^(BV[0-9A-Za-z]{6,32}|av[0-9]{1,20})_p([0-9]{1,5})$", re.IGNORECASE)
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
    audio_candidate: MediaCandidate | None = None


def normalize_source_ref(source_ref: str) -> str:
    """Return a stable video/part id; explicit later parts never become part 1."""

    if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 4096:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili source reference is invalid", provider_id="bilibili")
    value = source_ref.strip()
    part_ref = _PART_REF.fullmatch(value)
    if part_ref:
        base = part_ref[1]
        base = "BV" + base[2:] if base.upper().startswith("BV") else base.lower()
        return _with_part(base, _part_number(part_ref[2]))
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
    base = "BV" + value[2:] if value.upper().startswith("BV") else "av" + value.lower().removeprefix("av")
    try:
        selectors = [item for key, item in parse_qsl(parts.query, keep_blank_values=True,
                     errors="strict", max_num_fields=64) if key == "p"]
    except ValueError:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili part query is invalid", provider_id="bilibili") from None
    if len(selectors) > 1:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili part query is ambiguous", provider_id="bilibili")
    return _with_part(base, _part_number(selectors[0]) if selectors else 1)


def _part_number(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 10_000:
        raise SourceError(SourceErrorCode.INVALID_INPUT, "Bilibili part must be between 1 and 10000", provider_id="bilibili")
    return int(value)


def _with_part(base: str, part: int) -> str:
    return base if part == 1 else f"{base}_p{part}"


def source_parts(source_ref: str) -> tuple[str, int]:
    value = normalize_source_ref(source_ref)
    if "_p" in value:
        base, part = value.rsplit("_p", 1)
        return base, int(part)
    return value, 1


def canonical_source_url(source_ref: str) -> str:
    base, part = source_parts(source_ref)
    return f"https://www.bilibili.com/video/{base}" + (f"?p={part}" if part != 1 else "")


def _text(value: Any, name: str, *, limit: int = 4096, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is missing or malformed", provider_id="bilibili")
    return value.strip()


def _description(value: Any) -> str | None:
    if value is None:
        return None
    # Provider descriptions commonly contain paragraphs. Normalize only their
    # presentation whitespace to the existing single-line SourceItem contract;
    # validate the raw size before normalization and reject other controls.
    if not isinstance(value, str) or len(value) > 16_384:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili field desc is malformed", provider_id="bilibili")
    normalized = re.sub(r"[\r\n\t]+", " ", value)
    if _CONTROL.search(normalized):
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili field desc is malformed", provider_id="bilibili")
    return normalized.strip() or None


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
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is malformed", provider_id="bilibili") from error
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username is not None or parts.password is not None:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} is not a URL", provider_id="bilibili")
    if port is not None and not 1 <= port <= 65535:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, f"Bilibili field {name} has an invalid port", provider_id="bilibili")
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
                    item.get("kind", "dash"),
                    item.get("mime_type", "video/mp4"),
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
                    result.append(MediaCandidate(f"dash-audio-{index}", _https_url(locator, "dash.audio.url"), item.get("kind", "dash"), item.get("mime_type", "audio/mp4"), has_audio=True))
    progressive = data.get("durl", data.get("download_url"))
    if isinstance(progressive, list):
        for index, raw in enumerate(progressive[:256]):
            item = _mapping(raw, f"durl[{index}]")
            locator = item.get("url", item.get("baseUrl"))
            if locator is not None:
                result.append(MediaCandidate(f"progressive-{index}", _https_url(locator, "durl.url"), "progressive", "video/mp4",
                    item.get("width") if type(item.get("width")) is int and item["width"] > 0 else None,
                    item.get("height") if type(item.get("height")) is int and item["height"] > 0 else None, has_audio=True))
    elif progressive is not None:
        result.append(MediaCandidate("progressive-0", _https_url(progressive, "download_url"), "progressive", "video/mp4", has_audio=True))
    # App-owned yt-dlp transports may expose normalized formats instead of
    # Bilibili's API-specific DASH/durl fields.  Keep this mapping here so the
    # provider adapter remains the only place that interprets format metadata.
    if not result and isinstance(data.get("formats"), list):
        for index, raw in enumerate(data["formats"][:256]):
            if not isinstance(raw, Mapping) or raw.get("url") is None:
                continue
            locator = _https_url(raw.get("url"), "format.url")
            has_audio = raw.get("acodec") not in {None, "none"}
            result.append(MediaCandidate(f"format-{index}", locator, "progressive", "video/mp4", raw.get("width") if type(raw.get("width")) is int else None, raw.get("height") if type(raw.get("height")) is int else None, bool(has_audio)))
    if not result:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili response contains no downloadable media", provider_id="bilibili")
    return tuple(result)


class BilibiliSourceAdapter:
    provider_id = "bilibili"

    def __init__(self, transport: BilibiliTransport | None = None, *, ytdlp_executable: str | Path | None = None,
                 ytdlp_root: str | Path | None = None, ytdlp_sha256: str | None = None,
                 materializer: MediaMaterializer | None = None, stream_materializer: StreamMaterializer | None = None,
                 session_bridge: ProtectedSessionBridge | None = None) -> None:
        if transport is None:
            if ytdlp_executable is None:
                raise ValueError("a Bilibili transport or app-owned yt-dlp executable is required")
            transport = YtDlpProviderTransport(self.provider_id, ytdlp_executable, trusted_root=ytdlp_root, expected_sha256=ytdlp_sha256)
        self._transport = transport
        self._materializer = materializer or MediaMaterializer()
        self._stream_materializer = stream_materializer
        self._session_bridge = session_bridge

    @classmethod
    def can_handle(cls, source_ref: str) -> bool:
        try:
            normalize_source_ref(source_ref)
            return True
        except SourceError:
            return False

    def inspect(self, source_ref: str) -> SourceItem:
        source_id = normalize_source_ref(source_ref)
        try:
            if self._session_bridge is None:
                payload = self._transport.fetch_video(source_id)
            else:
                headers = self._session_bridge.get_opaque_headers(self.provider_id)
                payload = self._transport.fetch_video(source_id, headers)
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
        if code == -403:
            raise SourceError(SourceErrorCode.PRIVATE, "Bilibili video is private or permission-restricted", provider_id=self.provider_id, source_id=source_id, action="authenticate")
        if code == -404:
            raise SourceError(SourceErrorCode.NOT_FOUND, "Bilibili video is unavailable", provider_id=self.provider_id, source_id=source_id)
        if code not in {0, None}:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili API returned an unsupported error", provider_id=self.provider_id, source_id=source_id)
        data = _mapping(payload.get("data"), "data")
        bvid = data.get("bvid")
        aid = data.get("aid")
        requested_base, requested_part = source_parts(source_id)
        reported_part = data.get("part", 1)
        if type(reported_part) is not int or not 1 <= reported_part <= 10_000 or reported_part != requested_part:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "Bilibili returned another or unverified video part", provider_id=self.provider_id, source_id=source_id)
        actual_base = str(bvid) if isinstance(bvid, str) and _BVID.fullmatch(bvid) else ("av" + str(aid) if type(aid) is int and aid > 0 else requested_base)
        actual_id = _with_part(actual_base, reported_part)
        title = _text(data.get("title"), "title")
        description = _description(data.get("desc"))
        canonical = canonical_source_url(actual_id)
        return SourceItem(
            identity=SourceIdentity(self.provider_id, actual_id, canonical),
            title=title or actual_id,
            description=description,
            duration_ticks=_decimal_ticks(data.get("duration")),
            media_candidates=_media_candidates(data),
            subtitle_candidates=_subtitle_candidates(data),
        )

    def enumerate_channel(self, channel_id: str, *, cursor: str | None = None, page_size: int = 50) -> SourcePage:
        fetch = getattr(self._transport, "fetch_channel", None)
        if not callable(fetch):
            raise SourceError(SourceErrorCode.UNSUPPORTED, "Bilibili channel enumeration requires the pinned SDK runtime", provider_id=self.provider_id)
        session = self._session_bridge.get_opaque_headers(self.provider_id) if self._session_bridge is not None else None
        return fetch(channel_id, cursor=cursor, page_size=page_size, session=session)

    def select_download(self, item: SourceItem, *, prefer_progressive: bool = False) -> BilibiliDownloadChoice:
        """Choose the highest-resolution video and a matching audio stream."""

        candidates = [candidate for candidate in item.media_candidates if candidate.mime_type.startswith("video/")]
        if not candidates:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "Bilibili item has no video download candidate", provider_id=self.provider_id, source_id=item.identity.source_id)
        preferred_kind = "progressive" if prefer_progressive else "dash"
        candidates.sort(key=lambda candidate: (candidate.kind != preferred_kind, -((candidate.width or 0) * (candidate.height or 0)), -(candidate.height or 0), candidate.candidate_id))
        audio = next((candidate for candidate in item.media_candidates if candidate.mime_type.startswith("audio/")), None)
        selected = candidates[0]
        return BilibiliDownloadChoice(item.identity.source_id, selected, None if selected.has_audio else audio)

    def download(self, item: SourceItem, destination: str | Path, *, choice: BilibiliDownloadChoice | None = None, **kwargs: object) -> DownloadResult:
        if item.identity.provider_id != self.provider_id:
            raise SourceError(SourceErrorCode.INVALID_INPUT, "download item belongs to another provider", provider_id=self.provider_id)
        # Enumeration deliberately returns identities without expiring signed
        # media URLs. Inspect only the selected item immediately before download.
        if not item.media_candidates and choice is None:
            inspected = self.inspect(item.identity.source_id)
            if inspected.identity.identity_key != item.identity.identity_key:
                raise SourceError(SourceErrorCode.SOURCE_CHANGED, "enumerated video identity changed before download", provider_id=self.provider_id)
            item = inspected
        selected = choice or self.select_download(item)
        if (selected.source_id != item.identity.source_id or selected.candidate not in item.media_candidates
            or (selected.audio_candidate is not None and selected.audio_candidate not in item.media_candidates)):
            raise SourceError(SourceErrorCode.INVALID_INPUT, "download choice belongs to another source", provider_id=self.provider_id, source_id=item.identity.source_id)
        try:
            if self._stream_materializer is not None:
                return self._stream_materializer.download(selected.candidate, destination, audio=selected.audio_candidate, **kwargs)
            if not selected.candidate.has_audio:
                raise DownloadError(DownloadErrorCode.UNSUPPORTED, "selected streams require the app-owned source muxer")
            return self._materializer.download(selected.candidate, destination, **kwargs)
        except DownloadError as error:
            mapping = {
                DownloadErrorCode.AUTH_REQUIRED: SourceErrorCode.AUTH_REQUIRED,
                DownloadErrorCode.RATE_LIMITED: SourceErrorCode.RATE_LIMITED,
                DownloadErrorCode.SOURCE_CHANGED: SourceErrorCode.SOURCE_CHANGED,
                DownloadErrorCode.NETWORK: SourceErrorCode.NETWORK,
                DownloadErrorCode.UNSUPPORTED: SourceErrorCode.UNSUPPORTED,
            }
            raise SourceError(mapping.get(error.code, SourceErrorCode.NETWORK), "Bilibili media download failed", provider_id=self.provider_id, source_id=item.identity.source_id, retryable=error.retryable, action=error.action) from error


__all__ = ["BilibiliDownloadChoice", "BilibiliSourceAdapter", "BilibiliTransportError", "normalize_source_ref"]
