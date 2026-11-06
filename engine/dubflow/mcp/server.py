"""DubFlow's Model Context Protocol server over stdio (ADR-0049, phase 4b).

Newline-delimited JSON-RPC 2.0 on stdin/stdout, standard library only, so the
bundled runtime needs no extra package. Logs go to stderr; stdout carries
protocol messages only.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, TextIO

from engine.dubflow.mcp.activity import Activity, default_path
from engine.dubflow.docu import capcut_draft, intake, mcp_tools, overlays, roughcut, visual
from engine.dubflow.docu.project import DocuError, load_spec, spec_path

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "dubflow", "title": "DubFlow Phim tài liệu", "version": "1"}
INSTRUCTIONS = ("Tools for DubFlow documentary projects (Phim tài liệu). A new film: discover_videos -> download_video (2-15 sources, 6-12 give the richest B-roll) -> "
                "create_project (speech is recognised when a source has no .srt) -> get_script_brief -> write the script yourself and "
                "save_script -> narrate -> plan_visuals -> render_rough_cut. An existing one: list_projects, then inspect_timeline_state. "
                "Plan edits (replace_timeline_clip, apply_smart_label_overlay, adjust_pacing_and_transitions, set_framing) take effect after "
                "render_rough_cut. Use get_frame to look at a moment, e.g. to read a foreign label before covering it.")

_PROJECT = {"type": "string", "description": "The project's run folder (…\\Phim tai lieu - <title>\\run-<id>), from list_projects; "
            "it must be in the default DubFlow Output folder or one passed to list_projects as root"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


_SOURCE = {"type": "object", "additionalProperties": False, "required": ["video"], "properties": {
    "video": {"type": "string", "description": "Absolute path of the video (e.g. from download_video)"},
    "subtitles": {"type": "string", "description": "Optional .srt of its speech; without it DubFlow recognises the speech (minutes)"},
    "title": {"type": "string", "maxLength": 120}, "url": {"type": "string"}, "creator": {"type": "string"}}}

TOOLS = [
    _tool("discover_videos", "Search Bilibili, Douyin or Kuaishou for source videos (Chinese keywords work best). Douyin and Kuaishou, "
          "and Bilibili when it asks for verification, need the in-app Khám phá browser instead.",
          {"platform": {"type": "string", "enum": ["bilibili", "douyin", "kuaishou"]}, "query": {"type": "string"},
           "page": {"type": "integer", "minimum": 1}}, ["platform", "query"], read_only=True),
    _tool("download_video", "Download one video through DubFlow's verified downloader (Bilibili, Douyin with a saved session, direct "
          "links) into Videos\\DubFlow Sources. Only use footage you have the right to use.",
          {"url": {"type": "string"}}, ["url"]),
    _tool("create_project", "Create a documentary project from 2-15 local videos (6-12 give a rich B-roll pool), like the page's Tạo dự án. "
          "A source without subtitles has its speech recognised (faster-whisper on this computer, a few minutes per video).",
          {"title": {"type": "string", "maxLength": 120}, "topic": {"type": "string", "maxLength": 600},
           "target_minutes": {"type": "integer", "minimum": 3, "maximum": 20},
           "framing": {"type": "string", "enum": ["none", "letterbox_235", "overscan_108"], "description": "letterbox_235 hides low captions best"},
           "burn_end_credits": {"type": "boolean"}, "source_language": {"type": "string", "enum": ["zh", "en"]},
           "sources": {"type": "array", "minItems": 2, "maxItems": 15, "items": _SOURCE}},
          ["title", "target_minutes", "sources"]),
    _tool("get_script_brief", "What to write the script from: the rules, each chapter's purpose and word budget, the timed source lines "
          "and the quantity facts with US conversions.", {"project": _PROJECT}, ["project"], read_only=True),
    _tool("save_script", "Check and save a script you wrote (format in get_script_brief). Numbers the sources do not support are "
          "flagged, not removed; add them to a scene's acknowledged_numbers once checked.",
          {"project": _PROJECT, "script": {"type": "object"}}, ["project", "script"]),
    _tool("list_voices", "The English narration voices (Kokoro, on this computer).", {}, [], read_only=True),
    _tool("narrate", "Speak the saved script into narration.wav (-16 LUFS), captions_en.srt and the timeline. The first run downloads "
          "the voice (about 370 MB). Unchanged lines are not spoken again.",
          {"project": _PROJECT, "voice": {"type": "string", "description": "voice_id from list_voices, e.g. am_michael"},
           "speed": {"type": "number", "minimum": 0.8, "maximum": 1.2}}, ["project", "voice"]),
    _tool("plan_visuals", "Cut the sources into B-roll slices for every scene (shots, burned-in captions, logos), like the page's "
          "Lập kế hoạch hình ảnh: each slice inside one camera shot (1.8-6.5 s), logos cropped away where possible, heavily "
          "watermarked sources last, plus a satellite fly-in at the script's coordinates and up to 4 data slides. Hand-picked slices are kept.", {"project": _PROJECT}, ["project"]),
    _tool("list_projects", "List DubFlow documentary projects, newest first, with what each has (script, narration, plan, rough cut).",
          {"root": {"type": "string", "description": "A 'DubFlow Output' folder to search (absolute path); its projects "
                            "can then be used in this session. Default: the user's Videos\\DubFlow Output"}}, [],
          read_only=True),
    _tool("inspect_timeline_state", "The project's timeline: chapters, scenes with narration and times, the B-roll slices of each scene, "
          "labels, plan warnings (e.g. burned-in captions), review flags, pacing and the rough cut / CapCut state.",
          {"project": _PROJECT}, ["project"], read_only=True),
    _tool("replace_timeline_clip", "Point one B-roll slice of a scene at other footage (another source or time). The slice keeps its "
          "length, set by the narration. The choice survives a new plan (pacing, framing). Re-render to see it.",
          {"project": _PROJECT, "scene_id": {"type": "string"}, "clip_index": {"type": "integer", "minimum": 0},
           "new_source_id": {"type": "string", "description": "src_01…"},
           "start_time": {"type": "number", "minimum": 0, "description": "Seconds into the source"}},
          ["project", "scene_id", "clip_index", "new_source_id", "start_time"]),
    _tool("apply_smart_label_overlay", "Put an English label box (dark slate, white words) over the picture during one B-roll slice "
          "(clip_index) or the whole scene, e.g. over a Chinese label in a technical graphic. Positions are fractions of the 16:9 output "
          "frame (centre x/y, width): check them with get_frame on the rough cut. Burned into the rough cut on the next render and "
          "offered in the editor's first draft.",
          {"project": _PROJECT, "scene_id": {"type": "string"},
           "clip_index": {"type": "integer", "minimum": 0, "description": "Only during this slice of the scene (recommended); omit for the whole scene"},
           "text_en": {"type": "string", "maxLength": 160},
           "pos_x": {"type": "number", "minimum": 0, "maximum": 1}, "pos_y": {"type": "number", "minimum": 0, "maximum": 1},
           "width": {"type": "number", "minimum": 0.05, "maximum": 1}},
          ["project", "scene_id", "text_en", "pos_x", "pos_y", "width"]),
    _tool("remove_label", "Remove a label added with apply_smart_label_overlay.", {"project": _PROJECT, "label_id": {"type": "string"}},
          ["project", "label_id"]),
    _tool("adjust_pacing_and_transitions", "Change the longest B-roll slice for one chapter (or all) and the Ken Burns push, then rebuild "
          "the visual plan (shots and caption scans are cached). Transitions are hard cuts.",
          {"project": _PROJECT, "chapter": {"type": "string", "enum": ["all", "HOOK", "PROBLEM", "ENGINEERING_SOLUTION", "CRITICAL_EVALUATION"]},
           "max_clip_duration": {"type": "number", "minimum": 2, "maximum": 8},
           "ken_burns_intensity": {"type": "number", "minimum": 0, "maximum": 0.1, "description": "0 turns it off; 0.04 is the default"}},
          ["project"]),
    _tool("set_framing", "Change the project's framing look without redoing narration, scans or hand-picked slices: none, "
          "letterbox_235 (2.35:1 bars, hides low captions best) or overscan_108. Returns which sources still need their caption band "
          "blurred and darkened in place. Re-render to see it.",
          {"project": _PROJECT, "preset": {"type": "string", "enum": ["none", "letterbox_235", "overscan_108"]}}, ["project", "preset"]),
    _tool("set_render_options", "What the next render_rough_cut draws and plays: the script's number cards and lower-thirds "
          "(graphics, on by default), English captions burned in (captions, off by default: YouTube takes captions_en.srt), "
          "the music bed (music: generated per chapter mood, file with music_path, or none), ducked under the narration, "
          "and each scene's ambient sound with a hit between acts (sfx, on by default).",
          {"project": _PROJECT, "graphics": {"type": "boolean"}, "captions": {"type": "boolean"}, "sfx": {"type": "boolean"},
           "music": {"type": "string", "enum": ["generated", "file", "none"]},
           "music_path": {"type": "string", "description": "Absolute path of the user's own music file (MP3, WAV, M4A, AAC, FLAC, OGG, OPUS)"}},
          ["project"]),
    _tool("render_rough_cut", "Render documentary.mp4 from the plan and narration (unchanged slices are reused). Can take minutes.",
          {"project": _PROJECT}, ["project"]),
    _tool("sync_to_capcut_draft", "Write the rough cut as a CapCut PC draft (one clip per slice plus the narration) and list it first in "
          "CapCut's projects. CapCut must be closed.",
          {"project": _PROJECT, "capcut_drafts_dir": {"type": "string", "description": "Default: CapCut's own draft folder"}}, ["project"]),
    _tool("get_frame", "One frame (PNG, 960 px wide) of the rough cut, or of a source when source_id is given, at a time in seconds.",
          {"project": _PROJECT, "time": {"type": "number", "minimum": 0}, "source_id": {"type": "string"}}, ["project", "time"],
          read_only=True),
]


def _project_of(content: list[dict[str, Any]]) -> str | None:
    """The project a call created (create_project returns it)."""
    try:
        value = json.loads(content[0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return value.get("project") if isinstance(value, dict) and isinstance(value.get("project"), str) else None


def _summary(tool: str, content: list[dict[str, Any]]) -> str:
    """A few words on how a call ended, for the app's activity list."""
    try:
        value = json.loads(content[0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return "Đã xong"
    if isinstance(value, list):
        return f"{len(value)} kết quả"
    if not isinstance(value, dict):
        return "Đã xong"
    if tool == "discover_videos":
        return f"{len(value.get('items') or [])} video"
    if tool == "download_video":
        return f"Đã tải: {value.get('title') or value.get('video')}"
    if tool == "save_script":
        return f"{value.get('scenes')} cảnh, {value.get('words')} từ, {len(value.get('needs_review') or [])} cảnh cần kiểm tra"
    return str(value.get("message") or value.get("note") or "Đã xong")


def _text(value: Any) -> list[dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=1)}]


class Server:
    def __init__(self, install: mcp_tools.Install, *, roots: Callable[[], list[Path]] = mcp_tools.default_roots,
                 activity: Path | None | bool = True) -> None:
        self.install = install
        # What the desktop app shows while a client works; False/None turns it off (tests).
        self.activity = Activity(default_path(install.model_root) if activity is True else (activity or None))
        self.roots = roots
        self.chosen: list[Path] = []  # roots passed to list_projects, canonical

    def allowed_roots(self) -> list[Path]:
        """The canonical DubFlow Output folders whose projects the tools may open."""
        found = []
        for root in self.roots():
            try:
                found.append(mcp_tools.output_root(str(root)))
            except DocuError:
                continue
        return found + [root for root in self.chosen if root not in found]

    def _replan(self, run: Path) -> dict[str, Any]:
        from engine.dubflow.ocr import caption_scan
        from engine.dubflow.models.runtime import ensure_model_profile
        import hashlib

        spec = load_spec(spec_path(run))
        profile = self.install.app_root / visual.OCR_PROFILE
        if not profile.is_file():
            return visual.run(run, spec, ffmpeg=self.install.ffmpeg, scanner=None, ocr_identity=None)
        manifest = json.loads(profile.read_text(encoding="utf-8"))
        identity = {"recipe": caption_scan.SCAN_RECIPE, "profile": hashlib.sha256(profile.read_bytes()).hexdigest()}

        def scanner():
            ensure_model_profile(profile, self.install.model_root)
            from engine.dubflow import resources
            return caption_scan.CaptionScanner(self.install.model_root / manifest["model_path"], self.install.ffmpeg, threads=resources.cpu_threads(4))
        return visual.run(run, spec, ffmpeg=self.install.ffmpeg, scanner=scanner, ocr_identity=identity)

    def call(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        install = self.install
        if name == "discover_videos":
            return _text(intake.discover(args.get("platform"), args.get("query"), args.get("page") or 1))
        if name == "download_video":
            return _text(intake.download(str(args.get("url") or ""), version_root=install.app_root.parent,
                                         data_root=install.model_root.parent, folder=intake.default_base() / "DubFlow Sources"))
        if name == "list_voices":
            return _text(intake.voices(install.app_root))
        if name == "create_project":
            created = intake.create_project(title=str(args.get("title") or ""), topic=str(args.get("topic") or ""),
                                            target_minutes=args.get("target_minutes"), framing=args.get("framing") or "letterbox_235",
                                            burn_end_credits=bool(args.get("burn_end_credits", True)), sources=args.get("sources"),
                                            app_root=install.app_root, model_root=install.model_root, ffmpeg=install.ffmpeg,
                                            ffprobe=install.ffprobe, language=args.get("source_language") or "zh")
            return _text({**created, "next": "get_script_brief, then save_script"})
        if name == "list_projects":
            if args.get("root"):
                root = mcp_tools.output_root(args["root"])
                if root not in self.chosen:
                    self.chosen.append(root)
                return _text(mcp_tools.list_projects([root]))
            return _text(mcp_tools.list_projects(self.allowed_roots()))
        run = mcp_tools.project(args.get("project"), self.allowed_roots())
        if name == "get_script_brief":
            return _text(intake.script_brief(run))
        if name == "save_script":
            return _text(intake.save_script(run, args.get("script")))
        if name == "narrate":
            status = intake.narrate(run, app_root=install.app_root, model_root=install.model_root, ffmpeg=install.ffmpeg,
                                    voice=str(args.get("voice") or ""), speed=float(args.get("speed") or 1.0))
            if status.get("state") != "COMPLETED":
                raise DocuError(str(status.get("message")))
            return _text({**status, "next": "plan_visuals"})
        if name == "plan_visuals":
            status = self._replan(run)
            if status.get("state") != "COMPLETED":
                raise DocuError(str(status.get("message")))
            return _text({**status, "warnings": (mcp_tools.inspect(run).get("plan_warnings")), "next": "render_rough_cut"})
        if name == "inspect_timeline_state":
            return _text(mcp_tools.inspect(run))
        if name == "replace_timeline_clip":
            return _text(mcp_tools.replace_clip(run, args.get("scene_id"), args.get("clip_index"), args.get("new_source_id"), args.get("start_time")))
        if name == "apply_smart_label_overlay":
            return _text(mcp_tools.apply_label(run, args.get("scene_id"), args.get("text_en"), args.get("pos_x"), args.get("pos_y"),
                                               args.get("width"), args.get("clip_index")))
        if name == "set_render_options":
            chosen = overlays.save_options(run, graphics=args.get("graphics"), captions=args.get("captions"),
                                           music_path=args.get("music_path"), music=args.get("music"), sfx=args.get("sfx"))
            return _text({"options": overlays.describe(chosen), "note": "Dựng lại bản nháp (render_rough_cut) để áp dụng"})
        if name == "set_framing":
            return _text({**visual.reframe(run, args.get("preset")), "note": "Dựng lại bản nháp (render_rough_cut) để thấy khung mới"})
        if name == "remove_label":
            return _text(mcp_tools.remove_label(run, args.get("label_id")))
        if name == "adjust_pacing_and_transitions":
            return _text(mcp_tools.adjust_pacing(run, self.install, chapter=args.get("chapter"), max_clip_duration=args.get("max_clip_duration"),
                                                 ken_burns_intensity=args.get("ken_burns_intensity"), replan=self._replan))
        if name == "render_rough_cut":
            status = roughcut.run(run, load_spec(spec_path(run)), ffmpeg=self.install.ffmpeg,
                                  probe=lambda video: roughcut.probe_display(self.install.ffprobe, video))
            if status.get("state") != "COMPLETED":
                raise DocuError(str(status.get("message")))
            return _text(status)
        if name == "sync_to_capcut_draft":
            root = Path(args["capcut_drafts_dir"]) if args.get("capcut_drafts_dir") else capcut_draft.default_root()
            return _text(capcut_draft.export(run, load_spec(spec_path(run)), root=root))
        if name == "get_frame":
            data = mcp_tools.frame(run, self.install, args.get("time"), args.get("source_id"))
            return [{"type": "image", "data": mcp_tools.png_base64(data), "mimeType": "image/png"}]
        raise KeyError(name)

    def handle(self, message: Any) -> dict[str, Any] | None:
        """One JSON-RPC message in, its response out (None for notifications)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        method, ident = message.get("method"), message.get("id")
        if ident is None:
            return None
        params = message.get("params") if isinstance(message.get("params"), dict) else {}

        def ok(result: dict[str, Any]) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": ident, "result": result}

        if method == "initialize":
            asked = params.get("protocolVersion")
            return ok({"protocolVersion": asked if asked in SUPPORTED else PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": SERVER, "instructions": INSTRUCTIONS})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name not in {tool["name"] for tool in TOOLS}:
                return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
            self.activity.start(name, args)
            try:
                content = self.call(name, args)
            except DocuError as error:
                self.activity.finish(False, error.message)
                return ok({"content": [{"type": "text", "text": error.message}], "isError": True})
            except Exception as error:  # a tool failing must not end the session
                traceback.print_exc(file=sys.stderr)
                self.activity.finish(False, f"{type(error).__name__}: {error}")
                return ok({"content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}], "isError": True})
            self.activity.finish(True, _summary(name, content), _project_of(content))
            return ok({"content": content, "isError": False})
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(server: Server, stdin: TextIO, stdout: TextIO) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = server.handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
