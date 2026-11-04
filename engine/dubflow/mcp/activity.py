"""What an MCP client (Claude) is doing in DubFlow, for the desktop app to show.

The server writes ``<data root>/control/mcp-activity.json`` around every tool
call: the call running now (with its project, so the page can follow that
project's narration, plan or render progress) and the last calls with how
they ended. The app reads it every few seconds; nothing reads it back here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

FILE = "mcp-activity.json"
KEEP = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def label(tool: str, args: dict[str, Any]) -> str:
    """The call in the app's words."""
    def arg(name: str, limit: int = 80) -> str:
        return " ".join(str(args.get(name) or "").split())[:limit]
    labels: dict[str, Callable[[], str]] = {
        "discover_videos": lambda: f"Tìm video trên {arg('platform')}: {arg('query', 60)}",
        "download_video": lambda: f"Tải video {arg('url', 120)}",
        "create_project": lambda: f"Tạo dự án “{arg('title', 100)}” ({len(args.get('sources') or [])} nguồn)",
        "get_script_brief": lambda: "Đọc lời gốc và số liệu của các nguồn",
        "save_script": lambda: "Lưu kịch bản",
        "list_voices": lambda: "Xem danh sách giọng đọc",
        "narrate": lambda: f"Tạo lời đọc (giọng {arg('voice', 30)})",
        "plan_visuals": lambda: "Lập kế hoạch hình ảnh",
        "list_projects": lambda: "Xem danh sách dự án",
        "inspect_timeline_state": lambda: "Xem dòng thời gian",
        "replace_timeline_clip": lambda: f"Thay lát cắt {arg('scene_id', 20)} #{arg('clip_index', 4)} bằng {arg('new_source_id', 10)} từ {arg('start_time', 10)} s",
        "apply_smart_label_overlay": lambda: f"Thêm nhãn “{arg('text_en', 60)}” ở {arg('scene_id', 20)}",
        "remove_label": lambda: f"Bỏ nhãn {arg('label_id', 20)}",
        "adjust_pacing_and_transitions": lambda: "Đổi nhịp cắt và lập lại kế hoạch",
        "set_framing": lambda: f"Đổi khung hình: {arg('preset', 20)}",
        "set_render_options": lambda: "Đổi thẻ số liệu, phụ đề hoặc nhạc nền của bản nháp",
        "render_rough_cut": lambda: "Dựng bản nháp",
        "sync_to_capcut_draft": lambda: "Xuất sang CapCut",
        "get_frame": lambda: f"Xem khung hình ở {arg('time', 10)} s" + (f" của {arg('source_id', 10)}" if args.get("source_id") else ""),
    }
    return labels.get(tool, lambda: tool)()


class Activity:
    """The activity file of one server process; a failed write never fails a tool."""

    def __init__(self, path: Path | None, *, clock: Callable[[], str] = _now) -> None:
        self.path = path
        self.clock = clock
        self.session = f"{os.getpid()}-{int(time.time())}"
        self.recent: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        try:
            saved = json.loads(path.read_text(encoding="utf-8")) if path else {}
            if isinstance(saved.get("recent"), list):
                self.recent = [item for item in saved["recent"] if isinstance(item, dict)][:KEEP]
        except (OSError, ValueError, AttributeError):
            pass

    def start(self, tool: str, args: dict[str, Any]) -> None:
        self.current = {"tool": tool, "label": label(tool, args), "project": args.get("project") if isinstance(args.get("project"), str) else None,
                        "started": self.clock()}
        self._write()

    def finish(self, ok: bool, message: str = "", project: str | None = None) -> None:
        if self.current is None:
            return
        done = {**self.current, "finished": self.clock(), "state": "done" if ok else "failed", "message": " ".join(message.split())[:300]}
        if project and not done.get("project"):
            done["project"] = project
        self.recent = [done, *self.recent][:KEEP]
        self.current = None
        self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_name(self.path.name + ".partial")
            partial.write_text(json.dumps({"schema_version": 1, "session": self.session, "updated": self.clock(),
                                           "current": self.current, "recent": self.recent}, ensure_ascii=False), encoding="utf-8")
            partial.replace(self.path)
        except OSError:
            pass


def default_path(model_root: Path) -> Path:
    """``<data root>/control/mcp-activity.json``, next to the app's job status files."""
    return model_root.parent / "control" / FILE
