"""MCP tools for editing (Chỉnh sửa, #1002): an agent edits a dubbed video's own editor draft (ADR-0059).

``get_edit`` reads the draft (``.dubflow-work\\edit\\project.json``, schema 4) or starts one as the editor does.
``save_edit`` completes the facts of every file the draft uses (by probing them), then hands the draft to the running
app through the inbox (ADR-0058). The app reads it with the editor's own model, saves it as the video's draft (the
user's previous draft set aside), and exports it like Xuất video when asked. ``get_edit_job`` reports the app's answer
and the export's progress.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from engine.dubflow.docu.project import DocuError, write_json
from engine.dubflow.mcp import dubbing, outputs

SCHEMA = 4
MAX_REQUEST = 1024 * 1024
# features/edit/model.ts DEFAULT_TEXT_LOOK; tests/docu/test_mcp_editing.py checks they match.
TEXT_LOOK: dict[str, Any] = {"x": 0.5, "y": 0.5, "w": 0.8, "font": "Arial", "size": 72, "color": "#FFFFFF", "outline": "#000000",
                             "outline_width": 4, "box": None, "box_opacity": 0.6, "bold": True, "italic": False, "align": "center",
                             "animation": "none", "anim_duration": 0.5, "shadow": 0}
FONTS = ("Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman", "Calibri", "Cambria", "Palatino Linotype", "Constantia", "Corbel",
         "Candara", "Segoe UI Black")
TRANSITIONS = ("fade", "dissolve", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright", "zoomin", "circleopen")
QUALITIES = ("source", "1080p", "720p")

GUIDE = (
    "The draft is Chỉnh sửa's own project (schema 4). Times are seconds. Ids are a letter and a number, unique in the draft: "
    "s1 parts, m2 media, o3 overlays, a4 sounds, t5 texts, k6 markers; next_id must be larger than every number used. "
    "segments: the parts in output order, each {id, mediaId: 'main' or a media id, start, end (seconds of that media), "
    "speed 0.25-4, volume 0-2}; cut, trim, reorder or repeat by editing this list. "
    "transitions: {'<id of the part before the joint>': {type, duration 0.2-2}}, type one of " + ", ".join(TRANSITIONS) + ". "
    "media: other videos or pictures the parts and overlays use, each {id: 'm<n>', path}; save_edit fills the rest. "
    "overlays: picture-in-picture or B-roll over the video {id, mediaId, at (output s), start, end (media s), x, y, w "
    "(fractions of the frame: centre x/y, width), volume, opacity?, radius?, fadeIn?, fadeOut?}. "
    "texts: words over the picture {id, text, at, duration, x, y, w, font, size, color '#RRGGBB', outline, outline_width, box "
    "(colour or null), box_opacity, bold, italic, align left|center|right, animation none|fade|pop|slide-up|typewriter, "
    "anim_duration, shadow}; fields you leave out get the editor's defaults; fonts: " + ", ".join(FONTS) + ". "
    "audio: music and sound effects {id, source: {kind: 'file', path}, at (output s), start, end (file s), volume 0-2, "
    "fade_in, fade_out (0-3), duck_db (0-24: how far the clip lowers under speech), loop?}; save_edit fills title and duration. "
    "main_volume: the video's own sound 0-2. canvas: {aspect '9:16'|'16:9'|'1:1'|'4:5'|…, fill blur|black|fill} to change "
    "the frame shape. Subtitles follow the cuts by themselves. Keep the fields you do not change as get_edit gave them.")


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


TOOLS = [
    _tool("get_edit", "A dubbed video's Chỉnh sửa draft (the app's video editor), to edit it: its parts, transitions, texts, "
          "overlays, sounds and frame shape, or a fresh draft (the whole video as one part). Also the video's size and its "
          "reviewed subtitles with times (in the video's own time), and a guide to the draft's fields.",
          {"video": outputs._VIDEO}, ["video"], read_only=True),
    _tool("save_edit", "Save your edited draft as the video's Chỉnh sửa draft, and export it when export is true, like the "
          "editor's Xuất video. The DubFlow app must be open and the video must not be open in Chỉnh sửa. The app checks the "
          "draft with the editor's own rules, keeps the user's previous draft aside, and answers through get_edit_job. Media, "
          "pictures and sounds the draft uses must be files on this computer.",
          {"video": outputs._VIDEO, "draft": {"type": "object", "description": "The draft from get_edit, edited"},
           "export": {"type": "boolean", "description": "Also export the video (default false: only save the draft)"},
           "file_name": {"type": "string", "maxLength": 120, "description": "Export file name without folder; default '<title> (sửa N)'"},
           "quality": {"type": "string", "enum": list(QUALITIES), "description": "Export size; default source"}},
          ["video", "draft"]),
    _tool("get_edit_job", "The app's answer to save_edit: WAITING_FOR_APP, IN_APP, SAVED, REJECTED (with why, e.g. the draft "
          "breaks a rule or the video is open in Chỉnh sửa), EXPORTING with percent, COMPLETED with the exported file, or FAILED.",
          {"id": dubbing._ID_ARG}, ["id"], read_only=True),
]


def _draft_path(video: Path) -> Path:
    return video.parent / ".dubflow-work" / "edit" / "project.json"


def probe_media(ffprobe: Path, path: Path) -> dict[str, Any]:
    """Duration, size, frame rate and sound of a media file, as the editor keeps them."""
    try:
        done = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration",
                               "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
        value = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        value = {}
    streams = value.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    try:
        fps = float(Fraction(str(video.get("avg_frame_rate") or "0/1"))) if "/0" not in str(video.get("avg_frame_rate")) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        duration = float((value.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0 and video.get("width"):
        duration = 5.0  # a still picture: the editor's default length for one
    return {"duration": round(duration, 3), "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "fps": round(fps, 3), "hasAudio": any(stream.get("codec_type") == "audio" for stream in streams)}


def starter(video: Path, facts: dict[str, Any]) -> dict[str, Any]:
    """The editor's fresh draft (model.ts createEdit + serializeProject): the whole video as one part."""
    duration = facts["duration"]
    return {"schema_version": SCHEMA, "duration": duration, "next_id": 2, "changes": 0, "selected": "s1",
            "media": [{"id": "main", "path": str(video), "title": outputs._title(video.parent), "duration": duration, "width": facts["width"],
                       "height": facts["height"], "fps": facts["fps"] or 30, "hasAudio": facts["hasAudio"], "kind": "main"}],
            "segments": [{"id": "s1", "mediaId": "main", "start": 0, "end": duration, "speed": 1, "volume": 1}],
            "transitions": {}, "overlays": [], "audio": [], "main_volume": 1, "texts": [], "markers": []}


def get_edit(args: dict[str, Any], *, ffprobe: Path) -> dict[str, Any]:
    video = outputs.finished_video(args.get("video"))
    facts = probe_media(ffprobe, video)
    try:
        draft = json.loads(_draft_path(video).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        draft = None
    if not isinstance(draft, dict):
        draft = starter(video, facts)
    draft.pop("undo", None)  # the editor's own undo steps: large, and only meaningful to it
    return {"video": str(video), **facts, "draft": draft, "subtitles": outputs.subtitles(video.parent)[:outputs.MAX_CUES],
            "text_defaults": TEXT_LOOK, "guide": GUIDE}


def _file(value: Any, what: str) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_file():
        raise DocuError(f"{what}: cần đường dẫn đầy đủ tới một file có thật ({value})")
    return path


def complete(draft: dict[str, Any], video: Path, probe: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    """The draft with every file's facts from the file itself, and the texts' look filled with the editor's defaults."""
    if not isinstance(draft.get("segments"), list) or not draft["segments"]:
        raise DocuError("Bản nháp cần ít nhất một đoạn (segments)")
    result = dict(draft)
    result["schema_version"] = SCHEMA
    main = probe(video)
    result["duration"] = main["duration"]
    media = [{"id": "main", "path": str(video), "title": outputs._title(video.parent), **main, "fps": main["fps"] or 30, "kind": "main"}]
    for item in draft.get("media") or []:
        if not isinstance(item, dict) or item.get("id") == "main":
            continue
        if not re.fullmatch(r"m\d{1,9}", str(item.get("id"))):
            raise DocuError(f"Media id phải là m<số> (m2, m3…), không phải {item.get('id')!r}")
        path = _file(item.get("path"), f"Media {item.get('id')}")
        media.append({"id": item.get("id"), "path": str(path), "title": str(item.get("title") or path.name)[:300], **probe(path),
                      "kind": item.get("kind") if item.get("kind") in ("job", "file") else "file"})
    result["media"] = media
    sounds = []
    for clip in draft.get("audio") or []:
        if not isinstance(clip, dict):
            continue
        clip = dict(clip)
        source = clip.get("source") if isinstance(clip.get("source"), dict) else {"kind": "file", "path": clip.get("path")}
        clip.pop("path", None)
        if source.get("kind") == "file":
            path = _file(source.get("path"), f"Âm thanh {clip.get('id')}")
            facts = probe(path)
            source = {"kind": "file", "path": str(path)}
            clip.setdefault("title", path.name)
            clip["duration"] = facts["duration"]
        clip["source"] = source
        for key, default in (("at", 0), ("start", 0), ("volume", 1), ("fade_in", 0), ("fade_out", 0), ("duck_db", 0)):
            clip.setdefault(key, default)
        clip.setdefault("end", clip.get("duration"))
        sounds.append(clip)
    result["audio"] = sounds
    result["texts"] = [{**TEXT_LOOK, **text} for text in draft.get("texts") or [] if isinstance(text, dict)]
    for key, empty in (("transitions", {}), ("overlays", []), ("markers", []), ("main_volume", 1)):
        result.setdefault(key, empty)
    return result


def save_edit(args: dict[str, Any], *, data_root: Path, probe: Callable[[Path], dict[str, Any]],
              now: Callable[[], float] = time.time, token: Callable[[], str] = lambda: secrets.token_hex(4)) -> dict[str, Any]:
    video = outputs.finished_video(args.get("video"))
    if not isinstance(args.get("draft"), dict):
        raise DocuError("draft phải là bản nháp (đối tượng) từ get_edit")
    draft = complete(args["draft"], video, probe)
    export = None
    if args.get("export") is True:
        quality = args.get("quality") or None
        if quality is not None and quality not in QUALITIES:
            raise DocuError("quality phải là source, 1080p hoặc 720p")
        name = " ".join(str(args.get("file_name") or "").split()) or None
        if name and (len(name) > 120 or any(ch in name for ch in '\\/:*?"<>|')):
            raise DocuError("file_name là tên file, không có thư mục hoặc ký tự \\ / : * ? \" < > |")
        export = {"file_name": name, "quality": quality}
    job_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now()))}-{token()}"
    request = {"schema_version": 1, "kind": "edit", "id": job_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())),
               "output_path": str(video), "project": draft, "export": export}
    if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > MAX_REQUEST:
        raise DocuError("Bản nháp quá lớn (tối đa 1 MiB)")
    write_json(data_root / "control" / "inbox" / f"{job_id}.json", request)
    return {"id": job_id, "state": "WAITING_FOR_APP",
            "next": "get_edit_job. DubFlow must be open, with this video not open in Chỉnh sửa; it takes the edit within seconds."}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def edit_job(value: Any, *, data_root: Path) -> dict[str, Any]:
    job_id = str(value or "")
    if not dubbing.ID.match(job_id):
        raise DocuError("id phải là mã mcp-… mà save_edit trả về")
    inbox = data_root / "control" / "inbox"
    request = _read(inbox / f"{job_id}.json") or _read(inbox / "accepted" / f"{job_id}.json") or _read(inbox / "rejected" / f"{job_id}.json")
    if request is None or request.get("kind") != "edit":
        raise DocuError("Không có yêu cầu chỉnh sửa này")
    base = {"id": job_id, "video": request.get("output_path")}
    if (inbox / "rejected" / f"{job_id}.json").is_file():
        try:
            reason = (inbox / "rejected" / f"{job_id}.json.reason.txt").read_text(encoding="utf-8").strip()[:400]
        except OSError:
            reason = ""
        return {**base, "state": "REJECTED", "message": reason or "Ứng dụng không nhận yêu cầu này"}
    if (inbox / f"{job_id}.json").is_file():
        return {**base, "state": "WAITING_FOR_APP", "message": "Chờ DubFlow nhận yêu cầu: hãy mở ứng dụng DubFlow"}
    answer = _read(inbox / "results" / f"{job_id}.json")
    if answer is None:
        return {**base, "state": "IN_APP", "message": "DubFlow đang áp dụng bản nháp"}
    result = {**base, **{key: answer.get(key) for key in ("state", "message", "dropped") if answer.get(key) is not None}}
    if answer.get("state") != "EXPORTING":
        return result
    status = _read(Path(str(request.get("output_path"))).parent / ".dubflow-work" / "edit" / "status.json") or {}
    state = status.get("state")
    if state == "RUNNING":
        return {**result, "percent": status.get("percent"), "message": status.get("message") or result.get("message")}
    expected = str(answer.get("file_name") or "")
    if state == "COMPLETED" and status.get("output_path") and expected in Path(str(status["output_path"])).stem:
        return {**result, "state": "COMPLETED", "message": status.get("message"), "output": status.get("output_path")}
    if state in ("FAILED", "CANCELLED"):
        return {**result, "state": state, "message": status.get("message")}
    return result


__all__ = ["GUIDE", "TEXT_LOOK", "TOOLS", "complete", "edit_job", "get_edit", "probe_media", "save_edit", "starter"]
