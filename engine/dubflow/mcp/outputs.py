"""MCP tools for what DubFlow makes from finished dubbed videos (#1000): Shorts, Đổi khung 16:9, Gói đăng bài, a post
suggestion, and Ghép video (Tổng hợp).

Each tool writes what the app's own panel writes and starts the same engine:

* Shorts, Đổi khung and Gói đăng bài work inside the video's folder (``.dubflow-work\\shorts``, ``reframe``,
  ``package``). ``engine.dubflow.mcp.leased`` holds the folder's lease for the engine's whole life, as the app does, so
  the app refuses a second start on the same video, and the app shows the run when the video is selected.
* Ghép video makes ``<base>\\DubFlow Output\\Tong hop - <title>\\run-<id>`` with its run record, so the app lists it.

Every spec goes through the engine's own ``load_spec`` before anything starts.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from engine.dubflow.docu.project import DocuError, write_json
from engine.dubflow.mcp import leased, studio

FINAL_NAME = "final_vi.mp4"
COMPILATION_PREFIX = "Tong hop - "
NAME_CHARS = 40  # product.rs NAME_CHARS: the app's folder names
MUSIC_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"})
MAX_CUES = 600

KINDS: dict[str, dict[str, Any]] = {
    "shorts": {"module": "engine.dubflow.shorts", "starting": "Đang khởi động cắt Shorts…", "busy": "Video này đang được cắt Shorts",
               "models": False},
    "reframe": {"module": "engine.dubflow.reframe", "starting": "Đang khởi động đổi khung 16:9…",
                "busy": "Video này đang được đổi khung", "models": False},
    "package": {"module": "engine.dubflow.package", "starting": "Đang khởi động đóng gói…", "busy": "Video này đang được đóng gói",
                "models": True},
}
RESULTS = {"reframe": "final_vi_16x9.mp4", "package": "final_vi_packaged.mp4"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


_VIDEO = {"type": "string", "description": "A finished dubbed video: the final_vi.mp4 path from get_dubbing_job or list_dubbed_videos"}
_KIND = {"type": "string", "enum": list(KINDS), "description": "shorts, reframe (Đổi khung 16:9) or package (Gói đăng bài)"}
_POINTS = {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 60}}
_VOICE = {"type": "string", "description": "From list_vietnamese_voices"}
_SPEED = {"type": "number", "minimum": 1.0, "maximum": 1.3, "description": "Reading speed, default 1.0"}
_MUSIC = {"type": "string", "description": "Absolute path of a music file (MP3, M4A, AAC, WAV, OGG, FLAC)"}
_VOLUME = {"type": "number", "minimum": 0, "maximum": 1, "description": "Music volume, default 0.15"}
_CPU = {"type": "boolean", "description": "Read the voice on the CPU only (default false: the GPU when there is one)"}
_RUN = {"type": "string", "description": "The run folder (…\\DubFlow Output\\Tong hop - <title>\\run-<id>) from create_compilation or "
        "list_compilations"}

TOOLS = [
    _tool("list_dubbed_videos", "Finished dubbed videos (lồng tiếng xong), newest first: the final_vi.mp4 each tool here works on, "
          "with its title and which of Shorts, Đổi khung and Gói đăng bài were already made.",
          {"root": {"type": "string", "description": "A 'DubFlow Output' folder to look in as well"}}, [], read_only=True),
    _tool("get_dubbed_video", "One finished dubbed video: size, duration, vertical or not, its voice, the Vietnamese subtitles with "
          "times (to choose Shorts moments or write a post), and the state of its Shorts, Đổi khung and Gói đăng bài.",
          {"video": _VIDEO, "subtitles": {"type": "boolean", "description": "Default true"}}, ["video"], read_only=True),
    _tool("make_shorts", "Cut 1-3 vertical Shorts (1080x1920, 5-60 s each, with a title on screen) from a finished dubbed video, "
          "like the app's Shorts panel. Pick moments from get_dubbed_video's subtitles. Starts in the background; poll "
          "get_video_job with kind shorts.",
          {"video": _VIDEO,
           "items": {"type": "array", "minItems": 1, "maxItems": 3, "description": "The moments, 5-60 s each",
                     "items": {"type": "object", "additionalProperties": False, "required": ["start_ms", "end_ms", "title"],
                               "properties": {"start_ms": {"type": "integer", "minimum": 0}, "end_ms": {"type": "integer", "minimum": 0},
                                              "title": {"type": "string", "maxLength": 80}}}},
           "layout": {"type": "string", "enum": ["auto", "ban-tin", "mo-nen"],
                      "description": "auto (default), ban-tin (video in a frame, title band) or mo-nen (blurred fill)"}},
          ["video", "items"]),
    _tool("make_landscape_16x9", "Đổi khung: make a 16:9 YouTube version (1920x1080) of a vertical dubbed video, like the app's "
          "Đổi khung panel. bang-thong-tin puts the video beside an info panel (title, score, pros, cons, summary); cat-ngang crops "
          "to 16:9 around the focus (percent of the height from the top; focus_start at the start, focus_end at the end, default 50). Optionally only start_ms-end_ms. Poll get_video_job "
          "with kind reframe.",
          {"video": _VIDEO, "mode": {"type": "string", "enum": ["bang-thong-tin", "cat-ngang"]},
           "title": {"type": "string", "maxLength": 80}, "score": {"type": "integer", "minimum": 0, "maximum": 10},
           "pros": _POINTS, "cons": _POINTS, "summary": {"type": "string", "maxLength": 120},
           "focus_start": {"type": "number", "minimum": 0, "maximum": 100}, "focus_end": {"type": "number", "minimum": 0, "maximum": 100},
           "start_ms": {"type": "integer", "minimum": 0}, "end_ms": {"type": "integer", "minimum": 1}},
          ["video", "mode"]),
    _tool("package_video", "Gói đăng bài: add a spoken intro and/or outro (you write them in Vietnamese) and background music to a "
          "finished dubbed video, like the app's panel. background keep (default) keeps the video's sound; replace puts the music "
          "instead of the original background; duck lowers the original background under the music. Poll get_video_job with "
          "kind package.",
          {"video": _VIDEO, "intro": {"type": "string", "maxLength": 600}, "outro": {"type": "string", "maxLength": 600},
           "voice_id": {**_VOICE, "description": "Default: the video's own dubbing voice"}, "speed": _SPEED, "music": _MUSIC,
           "music_volume": _VOLUME, "background": {"type": "string", "enum": ["keep", "replace", "duck"]}, "cpu_only": _CPU},
          ["video"]),
    _tool("get_video_job", "State of a Shorts, Đổi khung or Gói đăng bài run on a dubbed video (RUNNING, COMPLETED, FAILED, "
          "CANCELLED), its message and the files it made.", {"video": _VIDEO, "kind": _KIND}, ["video", "kind"], read_only=True),
    _tool("cancel_video_job", "Stop a running Shorts, Đổi khung or Gói đăng bài run.", {"video": _VIDEO, "kind": _KIND}, ["video", "kind"]),
    _tool("suggest_post", "DubFlow's offline post suggestion for a dubbed video, from its Vietnamese subtitles: titles, a description, "
          "hashtags and keywords (no AI). Use it as a starting point; write the final post yourself.",
          {"video": _VIDEO, "source_title": {"type": "string", "maxLength": 200, "description": "The source video's title in Vietnamese"}},
          ["video"], read_only=True),
    _tool("create_compilation", "Ghép video (Tổng hợp): join 2-30 finished dubbed videos into one, with a spoken intro, a lead before "
          "each part and an outro (you write them in Vietnamese), optional trims, a score and pros/cons per part, and music, like "
          "the app's Tổng hợp page. bang-thong-tin shows each part beside an info panel; toan-man-hinh fills the frame. Makes "
          "compilation.mp4, chapters.txt and description.txt. Starts in the background; poll get_compilation.",
          {"title": {"type": "string", "maxLength": 120}, "layout": {"type": "string", "enum": ["bang-thong-tin", "toan-man-hinh"]},
           "voice_id": _VOICE, "speed": _SPEED, "music": _MUSIC, "music_volume": _VOLUME,
           "intro": {"type": "string", "maxLength": 800}, "outro": {"type": "string", "maxLength": 800},
           "items": {"type": "array", "minItems": 2, "maxItems": 30,
                     "items": {"type": "object", "additionalProperties": False, "required": ["video", "title"],
                               "properties": {"video": _VIDEO, "title": {"type": "string", "maxLength": 80},
                                              "lead": {"type": "string", "maxLength": 400, "description": "Spoken before the part"},
                                              "score": {"type": "integer", "minimum": 0, "maximum": 10}, "pros": _POINTS, "cons": _POINTS,
                                              "start_ms": {"type": "integer", "minimum": 0}, "end_ms": {"type": "integer", "minimum": 1}}}},
           "output_root": {"type": "string", "description": "Folder that gets 'DubFlow Output'; default the first video's"},
           "cpu_only": _CPU},
          ["title", "layout", "voice_id", "items"]),
    _tool("get_compilation", "Progress of a Ghép video run (RUNNING, COMPLETED, FAILED, CANCELLED), done/total, the message, and "
          "compilation.mp4, chapters and description when done.", {"run": _RUN}, ["run"], read_only=True),
    _tool("list_compilations", "Ghép video runs, newest first, with their state.",
          {"root": {"type": "string", "description": "A 'DubFlow Output' folder; default the user's"}}, [], read_only=True),
]


def _read(path: Path, limit: int = 64 * 1024 * 1024) -> Any:
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def finished_video(value: Any) -> Path:
    """A finished dubbed video, as the app's review_dir checks it: final_vi.mp4 inside DubFlow Output, with its translation."""
    path = Path(str(value or ""))
    if path.is_absolute():
        path = path.resolve()
        if (path.name == FINAL_NAME and path.is_file() and any(parent.name == "DubFlow Output" for parent in path.parents)
                and (path.parent / ".dubflow-work" / "translation.json").is_file()):
            return path
    raise DocuError("Cần final_vi.mp4 của một video lồng tiếng đã xong (trong DubFlow Output); xem list_dubbed_videos")


def _title(folder: Path) -> str:
    name = folder.parent.name
    return name[: -len(" - vi")] if name.endswith(" - vi") else name


def subtitles(folder: Path) -> list[dict[str, Any]]:
    """The Vietnamese lines as the user last reviewed them (translation_overrides.json wins), with their times."""
    translation = _read(folder / ".dubflow-work" / "translation.json") or {}
    overrides = (_read(folder / "translation_overrides.json", 4 * 1024 * 1024) or {}).get("cues") or {}
    lines = []
    for cue in translation.get("cues") or []:
        if not isinstance(cue, dict) or not isinstance(cue.get("start_ms"), int) or not isinstance(cue.get("end_ms"), int):
            continue
        text = overrides.get(cue.get("cue_id")) if isinstance(overrides.get(cue.get("cue_id")), str) else cue.get("translated_text")
        lines.append({"start_ms": cue["start_ms"], "end_ms": cue["end_ms"], "text": str(text or ""),
                      "source_text": str(cue.get("source_text") or "")})
    return lines


def _work(video: Path, kind: str) -> Path:
    return video.parent / ".dubflow-work" / kind


def video_job(video_value: Any, kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise DocuError("kind phải là shorts, reframe hoặc package")
    video = finished_video(video_value)
    status = _read(_work(video, kind) / "status.json", 1024 * 1024)
    if not isinstance(status, dict):
        return {"video": str(video), "kind": kind, "state": None, "message": "Chưa làm"}
    result = {"video": str(video), "kind": kind, "state": status.get("state"), "message": status.get("message")}
    if kind == "shorts":
        result.update(done=status.get("done"), total=status.get("total"),
                      outputs=[{"video": item.get("path"), "description": item.get("text_path"), "title": item.get("title")}
                               for item in status.get("outputs") or [] if isinstance(item, dict)])
    elif status.get("state") == "COMPLETED" and (video.parent / RESULTS[kind]).is_file():
        result.update(output=str(video.parent / RESULTS[kind]), duration_ms=status.get("duration_ms"))
    return result


def _state(video: Path, kind: str) -> str | None:
    status = _read(_work(video, kind) / "status.json", 1024 * 1024)
    return status.get("state") if isinstance(status, dict) else None


def recorded_voice(folder: Path, app_root: Path) -> str | None:
    """The voice the video was dubbed with (package.rs recorded_voice), when the catalog still has it."""
    manifest = _read(folder / "job_manifest.json", 16 * 1024 * 1024) or {}
    known = {voice["voice_id"] for voice in studio.voices(app_root)}
    for voice in ((manifest.get("audio") or {}).get("voice_id"), (manifest.get("dubbing") or {}).get("requested_voice_id")):
        if voice in known:
            return voice
    return None


def dubbed_video(args: dict[str, Any], install: Any, *, probe: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    video = finished_video(args.get("video"))
    size = probe(video)
    result: dict[str, Any] = {"video": str(video), "folder": str(video.parent), "title": _title(video.parent), **size,
                              "vertical": bool(size.get("width") and size.get("height") and size["height"] > size["width"]),
                              "voice_id": recorded_voice(video.parent, install.app_root),
                              "made": {kind: _state(video, kind) for kind in KINDS}}
    if args.get("subtitles", True) is not False:
        lines = subtitles(video.parent)
        result["subtitles"] = lines[:MAX_CUES]
        result["subtitles_truncated"] = len(lines) > MAX_CUES
    return result


def list_dubbed_videos(data_root: Path, roots: list[Path]) -> list[dict[str, Any]]:
    """Finished videos the queue made (its job records), and any in the given DubFlow Output folders."""
    found: dict[str, float] = {}
    jobs = data_root / "control" / "jobs"
    records = sorted(jobs.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:400] if jobs.is_dir() else []
    for record in records:
        value = _read(record, 1024 * 1024)
        if isinstance(value, dict) and (value.get("status") or {}).get("state") == "COMPLETED" and value.get("output_path"):
            try:
                found.setdefault(str(finished_video(value["output_path"])), record.stat().st_mtime)
            except DocuError:
                continue
    for root in roots:
        for video in list(root.glob(f"* - vi/run-*/{FINAL_NAME}"))[:400]:
            try:
                found.setdefault(str(finished_video(video)), video.stat().st_mtime)
            except (DocuError, OSError):
                continue
    newest = sorted(found.items(), key=lambda item: -item[1])[:60]
    return [{"video": path, "title": _title(Path(path).parent), "finished": when,
             "made": {kind: _state(Path(path), kind) for kind in KINDS}} for path, when in newest]


def _music(value: Any) -> str | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in MUSIC_SUFFIXES:
        raise DocuError("Nhạc nền: cần đường dẫn đầy đủ tới file MP3, M4A, AAC, WAV, OGG hoặc FLAC")
    return str(path)


def _engine_check(module: str, spec_path: Path) -> None:
    import importlib

    from engine.dubflow.product.job import ProductError

    try:
        importlib.import_module(module + ".job").load_spec(spec_path)
    except ProductError as error:
        raise DocuError(error.message) from error


def _python(install: Any) -> str:
    return str(studio._python(install.app_root))


def start_video_job(kind: str, video: Path, spec: dict[str, Any], install: Any, *, cpu_only: bool = False,
                    spawn: Callable[..., None] = studio._spawn, total: int = 0) -> dict[str, Any]:
    """Start Shorts, Đổi khung or Gói đăng bài on ``video``'s folder the way its panel does, under the folder's lease."""
    config = KINDS[kind]
    work = _work(video, kind)
    work.mkdir(parents=True, exist_ok=True)
    if not leased.lease_free(work):
        raise DocuError(config["busy"] + " (trong DubFlow hoặc một yêu cầu khác)")
    check = work / "spec.mcp-check.json"
    write_json(check, spec)
    try:
        _engine_check(config["module"], check)
    except DocuError:
        check.unlink(missing_ok=True)
        raise
    (work / "cancel").unlink(missing_ok=True)
    spec_path = work / "spec.json"
    os.replace(check, spec_path)
    status: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "message": config["starting"]}
    if kind == "shorts":
        status.update(done=0, total=total, outputs=[])
    write_json(work / "status.json", status)
    python = _python(install)
    engine = [python, "-B", "-m", config["module"], "--spec", str(spec_path), "--output-dir", str(video.parent)]
    if config["models"]:
        engine += ["--app-root", str(install.app_root), "--model-root", str(install.model_root)]
    engine += ["--ffmpeg", str(install.ffmpeg), "--ffprobe", str(install.ffprobe)]
    if cpu_only:
        engine.append("--cpu-only")
    spawn([python, "-B", "-m", "engine.dubflow.mcp.leased", "--kind", kind, "--work", str(work), "--", *engine], work, install.app_root)
    return {"video": str(video), "kind": kind, "state": "RUNNING", "next": f"get_video_job with kind {kind}"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def make_shorts(args: dict[str, Any], install: Any, **options: Any) -> dict[str, Any]:
    video = finished_video(args.get("video"))
    items = [{"start_ms": item.get("start_ms"), "end_ms": item.get("end_ms"), "title": _clean(item.get("title"))}
             for item in (args.get("items") or []) if isinstance(item, dict)]
    spec = {"schema_version": 1, "video": str(video), "items": items, "layout": args.get("layout") or "auto"}
    return start_video_job("shorts", video, spec, install, total=len(items), **options)


def make_landscape(args: dict[str, Any], install: Any, *, probe: Callable[[Path], dict[str, Any]], **options: Any) -> dict[str, Any]:
    video = finished_video(args.get("video"))
    size = probe(video)
    if size.get("width") and size.get("height") and size["height"] <= size["width"]:
        raise DocuError("Đổi khung 16:9 chỉ dành cho video dọc; video này đã là khung ngang")
    spec = {"schema_version": 1, "video": str(video), "mode": args.get("mode"), "title": _clean(args.get("title")),
            "score": args.get("score"), "pros": [_clean(item) for item in args.get("pros") or []],
            "cons": [_clean(item) for item in args.get("cons") or []], "summary": _clean(args.get("summary")),
            "focus_start": args.get("focus_start", 50), "focus_end": args.get("focus_end", 50),
            "start_ms": args.get("start_ms", 0), "end_ms": args.get("end_ms")}
    return start_video_job("reframe", video, spec, install, **options)


def package(args: dict[str, Any], install: Any, **options: Any) -> dict[str, Any]:
    video = finished_video(args.get("video"))
    voices = studio.voices(install.app_root)
    voice_id = args.get("voice_id") or recorded_voice(video.parent, install.app_root) or (voices[0]["voice_id"] if voices else None)
    if voice_id not in {voice["voice_id"] for voice in voices}:
        raise DocuError("Giọng không có trong danh mục; xem list_vietnamese_voices")
    spec = {"schema_version": 1, "video": str(video), "intro": str(args.get("intro") or "").strip(),
            "outro": str(args.get("outro") or "").strip(), "voice_id": voice_id, "speed": float(args.get("speed") or 1.0),
            "music": _music(args.get("music")), "music_volume": float(args.get("music_volume", 0.15)),
            "background": args.get("background") or "keep"}
    return start_video_job("package", video, spec, install, cpu_only=args.get("cpu_only") is True, **options)


def cancel_video_job(video_value: Any, kind: str) -> dict[str, Any]:
    job = video_job(video_value, kind)
    if job["state"] != "RUNNING":
        raise DocuError("Không có lần chạy nào đang chạy để hủy")
    (_work(Path(job["video"]), kind) / "cancel").write_bytes(b"")
    return {**job, "message": "Đã gửi yêu cầu hủy; lần chạy dừng sau bước đang làm"}


def suggest(args: dict[str, Any]) -> dict[str, Any]:
    from engine.dubflow.seo.suggest import suggest_post

    video = finished_video(args.get("video"))
    lines = [line["text"] for line in subtitles(video.parent)]
    if not any(line.strip() for line in lines):
        raise DocuError("Video này chưa có phụ đề tiếng Việt để gợi ý")
    title = _clean(args.get("source_title"))[:200] or _title(video.parent)
    return {"video": str(video), **suggest_post(lines, source_title=title)}


# Ghép video (Tổng hợp) ------------------------------------------------------------------------------------------------

def _folder_title(title: str) -> str:
    cleaned = " ".join("".join(" " if ord(c) < 32 or c in '<>:"/\\|?*' else c for c in title).split())
    return cleaned[:NAME_CHARS].rstrip(" .") or "san pham"


def create_compilation(args: dict[str, Any], install: Any, *, record_run: Callable[[str, Path], None],
                       spawn: Callable[..., None] = studio._spawn) -> dict[str, Any]:
    from engine.dubflow.compilation.job import load_spec
    from engine.dubflow.product.job import ProductError

    title = str(args.get("title") or "").strip()
    raw_items = [item for item in (args.get("items") or []) if isinstance(item, dict)]
    if not title or not 2 <= len(raw_items) <= 30:
        raise DocuError("Cần tiêu đề và 2–30 video đã lồng tiếng xong")
    voice_id = str(args.get("voice_id") or "")
    if voice_id not in {voice["voice_id"] for voice in studio.voices(install.app_root)}:
        raise DocuError("Giọng không có trong danh mục; xem list_vietnamese_voices")
    items = []
    for number, item in enumerate(raw_items, 1):
        try:
            video = finished_video(item.get("video"))
        except DocuError as error:
            raise DocuError(f"Phần {number}: {error}") from error
        entry = {"video": str(video), "title": str(item.get("title") or "").strip(), "lead": str(item.get("lead") or "").strip(),
                 "score": item.get("score"), "pros": [_clean(point) for point in item.get("pros") or []],
                 "cons": [_clean(point) for point in item.get("cons") or []]}
        for key in ("start_ms", "end_ms"):
            if item.get(key) is not None:
                entry[key] = item[key]
        items.append(entry)
    if args.get("output_root"):
        base = Path(str(args["output_root"]))
        if not base.is_absolute() or not base.is_dir():
            raise DocuError("output_root phải là thư mục có thật (đường dẫn đầy đủ)")
    else:
        first = Path(items[0]["video"])
        base = next(parent for parent in first.parents if parent.name == "DubFlow Output").parent
    intro, outro = str(args.get("intro") or "").strip(), str(args.get("outro") or "").strip()
    spec = {"schema_version": 1, "title": title, "layout": args.get("layout"), "voice_id": voice_id,
            "speed": float(args.get("speed") or 1.0), "music": _music(args.get("music")),
            "music_volume": float(args.get("music_volume", 0.15)), "intro": intro, "outro": outro, "items": items}
    seed = f"{time.time_ns()}:{os.getpid()}:{title}"
    run = base / "DubFlow Output" / f"{COMPILATION_PREFIX}{_folder_title(title)}" / f"run-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    work = run / ".dubflow-work"
    work.mkdir(parents=True, exist_ok=False)
    spec_path = work / "spec.json"
    write_json(spec_path, spec)
    total = len(items) + sum(1 for item in items if item["lead"]) + bool(intro) + bool(outro)
    try:
        load_spec(spec_path)  # the engine's own check, before anything starts
    except ProductError as error:
        write_json(work / "status.json", {"schema_version": 1, "state": "FAILED", "done": 0, "total": total, "message": error.message,
                                          "path": None})
        raise DocuError(error.message) from error
    write_json(work / "status.json", {"schema_version": 1, "state": "RUNNING", "done": 0, "total": total,
                                      "message": "Đang khởi động tổng hợp…", "path": None})
    record_run("compilation", run)
    argv = [_python(install), "-B", "-m", "engine.dubflow.compilation", "--spec", str(spec_path), "--output-dir", str(run),
            "--app-root", str(install.app_root), "--model-root", str(install.model_root), "--ffmpeg", str(install.ffmpeg),
            "--ffprobe", str(install.ffprobe)]
    if args.get("cpu_only") is True:
        argv.append("--cpu-only")
    spawn(argv, work, install.app_root)
    return {"run": str(run), "parts": len(items), "next": "get_compilation until state is COMPLETED (several minutes)"}


def _compilation_dir(value: Any) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        raise DocuError("run phải là đường dẫn đầy đủ của thư mục run-…")
    path = path.resolve()
    if (not studio._RUN.match(path.name) or not path.parent.name.startswith(COMPILATION_PREFIX)
            or path.parent.parent.name != "DubFlow Output" or not (path / ".dubflow-work" / "spec.json").is_file()):
        raise DocuError("Không phải thư mục Ghép video của DubFlow")
    return path


def compilation(run_value: Any) -> dict[str, Any]:
    run = _compilation_dir(run_value)
    status = _read(run / ".dubflow-work" / "status.json", 1024 * 1024)
    status = status if isinstance(status, dict) else {}
    result = {"run": str(run), "state": status.get("state"), "done": status.get("done"), "total": status.get("total"),
              "message": status.get("message")}
    if status.get("state") == "COMPLETED":
        for key, name in (("video", "compilation.mp4"), ("chapters", "chapters.txt"), ("description", "description.txt")):
            if (run / name).is_file():
                result[key] = str(run / name)
        try:
            result["description_text"] = (run / "description.txt").read_text(encoding="utf-8")[:20000]
        except OSError:
            pass
    return result


def list_compilations(roots: list[Path]) -> list[dict[str, Any]]:
    found = []
    for root in roots:
        for title in sorted(root.glob(COMPILATION_PREFIX + "*")):
            for run in title.glob("run-*"):
                if studio._RUN.match(run.name) and (run / ".dubflow-work" / "spec.json").is_file():
                    status = _read(run / ".dubflow-work" / "status.json", 1024 * 1024)
                    found.append({"run": str(run), "title": title.name[len(COMPILATION_PREFIX):],
                                  "state": status.get("state") if isinstance(status, dict) else None,
                                  "created": run.stat().st_mtime, "video": str(run / "compilation.mp4") if (run / "compilation.mp4").is_file() else None})
    return sorted(found, key=lambda item: -item["created"])[:60]


__all__ = ["KINDS", "TOOLS", "cancel_video_job", "compilation", "create_compilation", "dubbed_video", "finished_video",
           "list_compilations", "list_dubbed_videos", "make_landscape", "make_shorts", "package", "recorded_voice", "start_video_job",
           "subtitles", "suggest", "video_job"]
