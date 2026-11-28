"""MCP tools for dubbing (lồng tiếng, #998): the running app queues an agent's request (ADR-0058).

The server never starts a supervisor. It writes ``<data root>\\control\\inbox\\<id>.json``. The app's ``take_inbox``
checks it again and moves it to ``inbox\\accepted`` (or ``inbox\\rejected`` with a ``.reason.txt``), and the job runs
in Hàng đợi under the same id, one at a time, with the user's saved keys. Progress is the supervisor's own status file,
``control\\jobs\\<id>.json``.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from engine.dubflow.docu.project import DocuError, write_json

SCHEMA_VERSION = 1
ID = re.compile(r"^mcp-[0-9a-z-]{8,64}$")
# The app's VIDEO_EXTENSIONS (apps/desktop/src-tauri/src/main.rs).
VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts", ".m2ts", ".mts", ".3gp", ".wmv", ".mpg",
                            ".mpeg"})
_LANGUAGES = {"auto": "auto", "zh": "zh", "en": "en"}
_TRANSLATION = {"offline": "argos", "hachimi": "hachimi"}
_FLAGS = {"dub": "dub", "burn_subtitles": "burn_in", "blur_captions": "blur_auto", "separate_background": "separate_background",
          "multi_voice": "multi_voice"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


def _flag(text: str) -> dict[str, Any]:
    return {"type": "boolean", "description": text + "; leave out to use the user's own default"}


_ID_ARG = {"type": "string", "description": "The id queue_dubbing_job returned (mcp-…)"}

TOOLS = [
    _tool("queue_dubbing_job", "Dub a local video into Vietnamese (lồng tiếng: Vietnamese subtitles and a Vietnamese voice over the "
          "original), as the app's Hàng đợi does. The request goes to the DubFlow app, which must be open. It queues the video under "
          "the returned id and runs it after the jobs ahead of it, one at a time, with the user's saved keys and settings. Download "
          "a link first with download_video. Poll get_dubbing_job: WAITING_FOR_APP (app not open yet), QUEUED, RUNNING, then "
          "COMPLETED with the MP4. A 10-minute video takes roughly 10-30 minutes.",
          {"video": {"type": "string", "description": "Absolute path of the source video (MP4, MKV, MOV, WEBM, …)"},
           "title": {"type": "string", "maxLength": 200, "description": "Name shown in Hàng đợi; default the file name"},
           "dub": _flag("true: Vietnamese voice-over (default); false: translated subtitles only"),
           "voice_id": {"type": "string", "description": "From list_vietnamese_voices; default the user's chosen voice"},
           "source_language": {"type": "string", "enum": list(_LANGUAGES), "description": "Spoken language; auto detects it"},
           "burn_subtitles": _flag("Burn the Vietnamese subtitles into the picture (false: subtitle files only)"),
           "blur_captions": _flag("Find and blur the source's burned-in captions"),
           "separate_background": _flag("Keep the music and effects cleanly under the new voice"),
           "multi_voice": _flag("Experimental: a different Vietnamese voice for each speaker"),
           "translation": {"type": "string", "enum": list(_TRANSLATION),
                           "description": "offline (default) or hachimi (better Chinese translation). AI translation is used only "
                                          "when the user has turned it on in the app"}},
          ["video"]),
    _tool("get_dubbing_job", "State of a dubbing request: WAITING_FOR_APP, QUEUED, RUNNING (with percent), COMPLETED (with the MP4 and "
          "its folder), FAILED, CANCELLED, BLOCKED_NEEDS_ACTION, or REJECTED (with the reason), plus the app's message.",
          {"id": _ID_ARG}, ["id"], read_only=True),
    _tool("list_dubbing_jobs", "Dubbing requests made through MCP, newest first, with their state.", {}, [], read_only=True),
]


def _inbox(data_root: Path) -> Path:
    return data_root / "control" / "inbox"


def _video(value: Any) -> str:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_file():
        raise DocuError(f"video: cần đường dẫn đầy đủ tới một file video có thật ({value})")
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise DocuError("video: định dạng không hỗ trợ; dùng MP4, MKV, MOV, WEBM, AVI, M4V, FLV, TS, 3GP, WMV hoặc MPG")
    return str(path)


def queue_dubbing_job(args: dict[str, Any], *, data_root: Path, voices: Callable[[], list[dict[str, Any]]],
                      now: Callable[[], float] = time.time, token: Callable[[], str] = lambda: secrets.token_hex(4)) -> dict[str, Any]:
    video = _video(args.get("video"))
    options: dict[str, Any] = {key: None for key in ("dub", "voice_id", "source_language", "burn_in", "blur_auto", "separate_background",
                                                     "multi_voice", "translation_engine")}
    for name, key in _FLAGS.items():
        if args.get(name) is not None:
            if not isinstance(args[name], bool):
                raise DocuError(f"{name} phải là true hoặc false")
            options[key] = args[name]
    if args.get("voice_id"):
        voice_id = str(args["voice_id"])
        if voice_id not in {voice["voice_id"] for voice in voices()}:
            raise DocuError("Giọng không có trong danh mục; xem list_vietnamese_voices")
        options["voice_id"] = voice_id
    if args.get("source_language"):
        if args["source_language"] not in _LANGUAGES:
            raise DocuError("source_language phải là auto, zh hoặc en")
        options["source_language"] = _LANGUAGES[args["source_language"]]
    if args.get("translation"):
        if args["translation"] not in _TRANSLATION:
            raise DocuError("translation phải là offline hoặc hachimi")
        options["translation_engine"] = _TRANSLATION[args["translation"]]
    title = " ".join("".join(" " if ord(c) < 32 else c for c in str(args.get("title") or "")).split())[:200]
    job_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now()))}-{token()}"
    request = {"schema_version": SCHEMA_VERSION, "kind": "dubbing", "id": job_id,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "source_path": video, "title": title,
               "options": options}
    write_json(_inbox(data_root) / f"{job_id}.json", request)
    return {"id": job_id, "state": "WAITING_FOR_APP", "video": video,
            "next": "get_dubbing_job. DubFlow must be open: it takes the request within seconds and runs it in Hàng đợi after "
                    "the videos ahead of it."}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _percent(progress: Any) -> float | None:
    try:
        done, total = float(progress["completed_units"]), float(progress["total_units"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(100 * done / total, 1) if total > 0 else None


def dubbing_job(value: Any, *, data_root: Path) -> dict[str, Any]:
    job_id = str(value or "")
    if not ID.match(job_id):
        raise DocuError("id phải là mã mcp-… mà queue_dubbing_job trả về")
    inbox = _inbox(data_root)
    request = _read(inbox / f"{job_id}.json") or _read(inbox / "accepted" / f"{job_id}.json") \
        or _read(inbox / "rejected" / f"{job_id}.json") or {}
    base = {"id": job_id, "video": request.get("source_path"), "title": request.get("title") or None}
    record = _read(data_root / "control" / "jobs" / f"{job_id}.json")
    if record is not None:
        status = record.get("status") if isinstance(record.get("status"), dict) else {}
        output = record.get("output_path") or None
        result = {**base, "state": status.get("state"), "percent": _percent(status.get("progress")), "message": status.get("message"),
                  "reason": status.get("reason")}
        if status.get("state") == "COMPLETED" and output:
            folder = Path(output).parent
            result.update(video_out=output, folder=str(folder),
                          files=sorted(str(path) for path in folder.iterdir() if path.suffix.lower() in (".mp4", ".srt", ".ass"))
                          if folder.is_dir() else [])
        return result
    if (inbox / "rejected" / f"{job_id}.json").is_file():
        try:
            reason = (inbox / "rejected" / f"{job_id}.json.reason.txt").read_text(encoding="utf-8").strip()[:400]
        except OSError:
            reason = ""
        return {**base, "state": "REJECTED", "message": reason or "Ứng dụng không nhận yêu cầu này"}
    if (inbox / "accepted" / f"{job_id}.json").is_file():
        return {**base, "state": "QUEUED", "message": "Đã vào Hàng đợi; chờ các video trước chạy xong"}
    if (inbox / f"{job_id}.json").is_file():
        return {**base, "state": "WAITING_FOR_APP", "message": "Chờ DubFlow nhận yêu cầu: hãy mở ứng dụng DubFlow"}
    raise DocuError("Không có yêu cầu lồng tiếng này")


def list_dubbing_jobs(*, data_root: Path) -> list[dict[str, Any]]:
    inbox = _inbox(data_root)
    ids = {path.stem for folder in (inbox, inbox / "accepted", inbox / "rejected") if folder.is_dir()
           for path in folder.glob("mcp-*.json") if ID.match(path.stem)}
    return [dubbing_job(job_id, data_root=data_root) for job_id in sorted(ids, reverse=True)[:60]]


__all__ = ["ID", "TOOLS", "dubbing_job", "list_dubbing_jobs", "queue_dubbing_job"]
