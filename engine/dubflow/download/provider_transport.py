"""App-owned yt-dlp provider transport mappings.

The extractor process is shared, while provider adapters retain all response
normalization and identity rules.  This module only converts yt-dlp's stable
JSON fields into the small provider payload shapes consumed by those adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .generic import YtDlpRunner, YtDlpTransport
from .source_adapter import SourceError, SourceErrorCode
from .authenticated import AuthenticatedYtDlpTransport
from .materializer import DownloadError, DownloadErrorCode, HttpTransport, UrllibHttpTransport


class PublicProviderHttpTransport:
    """Approved public SDK defaults, never a provider credential capability."""

    def __init__(self, provider_id: str, public_headers: Mapping[str, str], *, transport: HttpTransport | None = None):
        if provider_id not in {"bilibili", "douyin"}:
            raise ValueError("unsupported public media provider")
        if not isinstance(public_headers, Mapping) or set(public_headers) != {"User-Agent", "Accept", "Accept-Language"}:
            raise ValueError("approved public SDK header defaults are required")
        if any(not isinstance(value, str) or not 1 <= len(value) <= 512
               or any(ord(ch) < 32 or ord(ch) > 126 for ch in value) for value in public_headers.values()):
            raise ValueError("invalid public SDK headers")
        self._headers = {**public_headers, "Referer": "https://www." + provider_id + ".com/"}
        self._transport = transport or UrllibHttpTransport()

    def open(self, url: str, *, headers: Mapping[str, str] | None = None):
        overrides = headers or {}
        # The materializer owns range validators only. Do not forward cookies,
        # authorization or mutable generic headers to a media CDN.
        allowed = {"range": "Range", "if-range": "If-Range", "accept-encoding": "Accept-Encoding"}
        safe = {}
        if not isinstance(overrides, Mapping) or len(overrides) > len(allowed):
            raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "invalid public media request headers")
        for name, value in overrides.items():
            key = allowed.get(name.lower()) if isinstance(name, str) else None
            if (key is None or key in safe or not isinstance(value, str) or not 1 <= len(value) <= 1024
                or any(ord(ch) < 32 or ord(ch) > 126 for ch in value)):
                raise DownloadError(DownloadErrorCode.INVALID_SOURCE, "invalid public media request headers")
            safe[key] = value
        return self._transport.open(url, headers={**self._headers, **safe})


class YtDlpProviderTransport:
    def __init__(self, provider_id: str, executable: str | Path | None = None, *, trusted_root: str | Path | None = None,
                 expected_sha256: str | None = None, runner: YtDlpRunner | None = None,
                 authenticated_transport: AuthenticatedYtDlpTransport | None = None) -> None:
        if provider_id not in {"bilibili", "douyin"}:
            raise ValueError("provider_id must be bilibili or douyin")
        self.provider_id = provider_id
        self._transport = YtDlpTransport(executable, trusted_root=trusted_root, expected_sha256=expected_sha256, runner=runner) if executable is not None else None
        self._authenticated = authenticated_transport
        if self._transport is None and self._authenticated is None:
            raise ValueError("a pinned extractor transport is required")

    def fetch_video(self, source_ref: str, session: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        url = self._url(source_ref)
        if session and self._authenticated is None:
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "yt-dlp session bridge is not configured for this transport", provider_id=self.provider_id, action="authenticate")
        raw = self._authenticated.inspect_url(url, provider_id=self.provider_id, headers=session) if self._authenticated is not None else self._transport.inspect_url(url)
        if not isinstance(raw, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp provider metadata is malformed", provider_id=self.provider_id)
        payload = _map_payload(self.provider_id, raw, source_ref)
        # The Bilibili adapter consumes the API's code/data envelope.
        return {"code": 0, "data": payload} if self.provider_id == "bilibili" else payload

    def _url(self, source_ref: str) -> str:
        if self.provider_id == "bilibili":
            return f"https://www.bilibili.com/video/{source_ref}"
        if str(source_ref).startswith("short-"):
            return f"https://v.douyin.com/{str(source_ref)[6:]}"
        return f"https://www.douyin.com/video/{source_ref}"


def _format_url(value: Mapping[str, Any]) -> str | None:
    url = value.get("url")
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def _formats(raw: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    videos: list[Mapping[str, Any]] = []
    audios: list[Mapping[str, Any]] = []
    values = raw.get("formats", [])
    if not isinstance(values, list):
        return videos, audios
    for value in values[:256]:
        if not isinstance(value, Mapping) or _format_url(value) is None:
            continue
        if value.get("vcodec") not in {None, "none"}:
            videos.append(value)
        elif value.get("acodec") not in {None, "none"}:
            audios.append(value)
    return videos, audios


def _subtitles(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    values = raw.get("subtitles", {})
    if not isinstance(values, Mapping):
        return result
    for language, candidates in list(values.items())[:256]:
        if not isinstance(language, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates[:16]:
            if isinstance(candidate, Mapping) and _format_url(candidate):
                result.append({"lan": language, "subtitle_url": _format_url(candidate) or "", "format": str(candidate.get("ext", "vtt"))})
    return result


def _map_payload(provider_id: str, raw: Mapping[str, Any], source_ref: str) -> dict[str, Any]:
    videos, audios = _formats(raw)
    if not videos:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp returned no video format", provider_id=provider_id, source_id=source_ref)
    videos.sort(key=lambda value: (int(value.get("height", 0)) if isinstance(value.get("height"), int) else 0, int(value.get("tbr", 0)) if isinstance(value.get("tbr"), (int, float)) else 0), reverse=True)
    audios.sort(key=lambda value: float(value.get("abr", 0)) if isinstance(value.get("abr"), (int, float)) else 0, reverse=True)
    best = videos[0]
    if provider_id == "bilibili":
        # A video-only format is never a progressive audio/video fallback.
        # Preserve protocol/container hints so playlists cannot masquerade as
        # complete MP4 objects at the local mux boundary.
        split = [value for value in videos if value.get("acodec") in {None, "none"}]
        combined = [value for value in videos if value.get("acodec") not in {None, "none"}
                    and value.get("protocol", "https") in {"http", "https"}]
        dash_video = [{"id": value.get("format_id", index), "baseUrl": _format_url(value), "width": value.get("width"), "height": value.get("height"),
                       "kind": "hls" if value.get("protocol") in {"m3u8", "m3u8_native"} else "dash",
                       "mime_type": "video/webm" if value.get("ext") == "webm" else "video/mp4"} for index, value in enumerate(split[:256])]
        dash_video = [value for value in dash_video if value["baseUrl"]]
        dash_audio = [{"id": value.get("format_id", index), "baseUrl": _format_url(value),
                       "kind": "hls" if value.get("protocol") in {"m3u8", "m3u8_native"} else "dash",
                       "mime_type": "audio/webm" if value.get("ext") == "webm" else "audio/mp4"} for index, value in enumerate(audios[:256])]
        dash_audio = [value for value in dash_audio if value["baseUrl"]]
        return {
            "bvid": raw.get("id") if isinstance(raw.get("id"), str) and str(raw.get("id")).startswith("BV") else None,
            "title": raw.get("title") or source_ref,
            "desc": raw.get("description"),
            "duration": raw.get("duration"),
            "dash": {"video": dash_video, "audio": dash_audio},
            "durl": [{"url": _format_url(value), "width": value.get("width"), "height": value.get("height")} for value in combined[:256]],
            "subtitle": {"list": _subtitles(raw)},
        }
    best_url = _format_url(best)
    streams = []
    for role, values in (("video", videos), ("audio", audios)):
        for index, value in enumerate(values):
            has_audio = value.get("acodec") not in {None, "none"}
            protocol = value.get("protocol", "https")
            kind = ("hls" if protocol in {"m3u8", "m3u8_native"} else
                    "dash" if protocol == "http_dash_segments" or not has_audio else "progressive")
            streams.append({"candidate_id": f"sdk-{role}-{index}", "locator": _format_url(value),
                "kind": kind, "mime_type": role + ("/webm" if value.get("ext") == "webm" else "/mp4"),
                "width": value.get("width") if role == "video" else None,
                "height": value.get("height") if role == "video" else None, "has_audio": has_audio})
    if len(streams) > 256:
        raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp returned too many media formats", provider_id=provider_id)
    return {
        "aweme_id": raw.get("id") if isinstance(raw.get("id"), str) else source_ref,
        "desc": raw.get("title") or source_ref,
        "duration": (float(raw.get("duration")) * 1000) if isinstance(raw.get("duration"), (int, float)) else raw.get("duration"),
        "video": {"width": best.get("width"), "height": best.get("height"), "play_addr": {"url_list": [best_url]}, "download_addr": {"url_list": [best_url]}},
        "source_streams": streams,
        "subtitle_infos": [{"language": item["lan"], "url": item["subtitle_url"], "format": item["format"]} for item in _subtitles(raw)],
    }


__all__ = ["YtDlpProviderTransport"]
