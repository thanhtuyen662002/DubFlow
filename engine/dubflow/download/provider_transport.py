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


class YtDlpProviderTransport:
    def __init__(self, provider_id: str, executable: str | Path, *, runner: YtDlpRunner | None = None) -> None:
        if provider_id not in {"bilibili", "douyin"}:
            raise ValueError("provider_id must be bilibili or douyin")
        self.provider_id = provider_id
        self._transport = YtDlpTransport(executable, runner=runner)

    def fetch_video(self, source_ref: str, session: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        # Session headers are intentionally not passed as command-line
        # arguments.  A future credential bridge may provide a short-lived
        # cookie file through the app-owned process boundary.
        if session:
            raise SourceError(SourceErrorCode.AUTH_REQUIRED, "yt-dlp session bridge is not configured for this transport", provider_id=self.provider_id, action="authenticate")
        url = self._url(source_ref)
        raw = self._transport.inspect_url(url)
        if not isinstance(raw, Mapping):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "yt-dlp provider metadata is malformed", provider_id=self.provider_id)
        return _map_payload(self.provider_id, raw, source_ref)

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
        dash_video = [{"id": value.get("format_id", index), "baseUrl": _format_url(value), "width": value.get("width"), "height": value.get("height")} for index, value in enumerate(videos[:256])]
        dash_video = [value for value in dash_video if value["baseUrl"]]
        dash_audio = [{"id": value.get("format_id", index), "baseUrl": _format_url(value)} for index, value in enumerate(audios[:256])]
        dash_audio = [value for value in dash_audio if value["baseUrl"]]
        return {
            "bvid": raw.get("id") if isinstance(raw.get("id"), str) and str(raw.get("id")).startswith("BV") else None,
            "title": raw.get("title") or source_ref,
            "desc": raw.get("description"),
            "duration": raw.get("duration"),
            "dash": {"video": dash_video, "audio": dash_audio},
            "durl": [{"url": _format_url(best)}],
            "subtitle": {"list": _subtitles(raw)},
        }
    best_url = _format_url(best)
    return {
        "aweme_id": raw.get("id") if isinstance(raw.get("id"), str) else source_ref,
        "desc": raw.get("title") or source_ref,
        "duration": (float(raw.get("duration")) * 1000) if isinstance(raw.get("duration"), (int, float)) else raw.get("duration"),
        "video": {"width": best.get("width"), "height": best.get("height"), "play_addr": {"url_list": [best_url]}, "download_addr": {"url_list": [best_url]}},
        "subtitle_infos": [{"language": item["lan"], "url": item["subtitle_url"], "format": item["format"]} for item in _subtitles(raw)],
    }


__all__ = ["YtDlpProviderTransport"]
