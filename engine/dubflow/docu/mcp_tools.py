"""What the MCP server can do to a documentary project (ADR-0049, phase 4b).

Every tool names a project by its run folder and touches nothing outside
documentary run folders, CapCut's draft folder (``sync_to_capcut_draft``) and
the bundled FFmpeg. A run folder counts only directly under a DubFlow Output
root the server was given (the default one, or one passed to
``list_projects``), and only while no symlink or junction inside it leads
elsewhere. Tools return plain data; the server turns it into MCP results.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable

from . import capcut_draft, roughcut, visual
from .project import DocuError, load_spec, script_path, spec_path, work_dir, write_json
from .script import CHAPTERS

_RUN = re.compile(r"run-[0-9a-f]{12}")
PREFIX = "Phim tai lieu - "
MAX_LABELS = 60
MAX_LABEL_TEXT = 160
# IsReparseTagNameSurrogate: the reparse point names another file (symlink, junction, mount point),
# unlike a OneDrive placeholder or an app execution alias.
_NAME_SURROGATE = 0x20000000


@dataclass(frozen=True)
class Install:
    """Where the bundled runtime keeps FFmpeg and the models."""

    app_root: Path
    ffmpeg: Path
    ffprobe: Path
    model_root: Path

    @classmethod
    def here(cls) -> "Install":
        app = Path(__file__).resolve().parents[3]
        media = app.parent / "runtime" / "media"
        control = app.parent.parent.parent
        models = control / "models" if (control / "models").is_dir() else app.parent / "models"
        ffmpeg = Path(os.environ.get("DUBFLOW_FFMPEG") or media / "ffmpeg.exe")
        ffprobe = Path(os.environ.get("DUBFLOW_FFPROBE") or media / "ffprobe.exe")
        return cls(app, ffmpeg, ffprobe, models)


def redirects(info: os.stat_result) -> bool:
    """Whether an lstat result is a symlink, junction or other link to another place."""
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                and getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE)


def linked(path: Path) -> bool:
    try:
        return redirects(path.lstat())
    except OSError:
        return False


def output_root(value: Any) -> Path:
    """A DubFlow Output folder, canonical, for the server to look for projects in."""
    if not isinstance(value, str) or not value.strip():
        raise DocuError("Cần đường dẫn thư mục DubFlow Output (root)")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DocuError("root phải là đường dẫn đầy đủ tới thư mục DubFlow Output")
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise DocuError("Không tìm thấy thư mục DubFlow Output đó") from None
    if not path.is_dir():
        raise DocuError("root phải là một thư mục")
    return path


def _no_links(run: Path) -> None:
    """Refuse a run holding a symlink or junction: a read or write through it would leave the project."""
    pending = [run]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if redirects(info):
                    inside = Path(entry.path).relative_to(run)
                    raise DocuError(f"Dự án có liên kết (symlink/junction) trỏ ra nơi khác: {inside}. DubFlow không đọc hay ghi qua liên kết")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))


def project(value: Any, roots: list[Path]) -> Path:
    """A documentary run folder ``<root>\\Phim tai lieu - <title>\\run-<12 hex>`` with its request, ``root`` one of ``roots`` (canonical)."""
    if not isinstance(value, str) or not value.strip():
        raise DocuError("Cần đường dẫn thư mục dự án (project)")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DocuError("project phải là đường dẫn đầy đủ tới thư mục run-… của dự án")
    try:
        real = path.resolve(strict=True)
    except OSError:
        real = None
    if real is None or not (_RUN.fullmatch(real.name) and real.parent.name.startswith(PREFIX)):
        raise DocuError("Không phải thư mục dự án Phim tài liệu (…\\Phim tai lieu - <tên>\\run-<id>)")
    if real.parent.parent not in roots:
        raise DocuError("Dự án nằm ngoài thư mục DubFlow Output đang dùng. Nếu dự án ở thư mục DubFlow Output khác, "
                        "gọi list_projects với root là thư mục đó trước")
    if linked(path) or linked(path.parent):
        raise DocuError("Đường dẫn dự án đi qua liên kết (symlink/junction); hãy dùng đường dẫn từ list_projects")
    _no_links(real)
    if not spec_path(real).is_file():
        raise DocuError("Không phải thư mục dự án Phim tài liệu (…\\Phim tai lieu - <tên>\\run-<id>)")
    return real


def _publish(path: Path, value: Any) -> None:
    # write_json writes <name>.partial first; drop a leftover one so a hard link planted there cannot take the write elsewhere.
    path.with_name(path.name + ".partial").unlink(missing_ok=True)
    write_json(path, value)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_projects(roots: list[Path]) -> list[dict[str, Any]]:
    """Projects directly under ``roots``; a linked title or run folder is skipped, not followed."""
    found = []
    for base in roots:
        try:
            base = output_root(str(base))
        except DocuError:
            continue
        for folder in sorted(base.glob(f"{PREFIX}*")):
            if linked(folder):
                continue
            for run in folder.glob("run-*"):
                try:
                    run = project(str(run), [base])
                except DocuError:
                    continue
                script = _json(script_path(run)) or {}
                found.append({"project": str(run), "title": script.get("project_title") or (_json(spec_path(run)) or {}).get("title"),
                              "script": script_path(run).is_file(), "narration": (run / "narration.wav").is_file(),
                              "plan": (run / "visual_plan.json").is_file(), "rough_cut": (run / roughcut.VIDEO).is_file(),
                              "modified": int(run.stat().st_mtime)})
    return sorted(found, key=lambda item: -item["modified"])


def default_roots() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return [home / "Videos" / "DubFlow Output"]


def inspect(run: Path) -> dict[str, Any]:
    spec = load_spec(spec_path(run))
    script = _json(script_path(run)) or {"chapters": []}
    timeline = _json(run / "master_audio_timeline.json") or {}
    plan = _json(run / "visual_plan.json") or {}
    times = {scene["scene_id"]: scene for scene in timeline.get("scenes", [])}
    planned = {scene["scene_id"]: scene for scene in plan.get("scenes", [])}
    labels = roughcut.load_labels(run)
    chapters = []
    for chapter in script.get("chapters", []):
        scenes = []
        for scene in chapter.get("scenes", []):
            timed, slots = times.get(scene["scene_id"], {}), planned.get(scene["scene_id"], {})
            scenes.append({"scene_id": scene["scene_id"], "narration": scene.get("narration_text"),
                           "start": timed.get("start"), "end": timed.get("slot_end"),
                           "slices": [{"index": i, "source_id": piece["source_id"], "start": piece["start"], "end": piece["end"],
                                       "at": piece["at"], "from_cited_footage": piece.get("tier", 0) <= 0}
                                      for i, piece in enumerate(slots.get("slices", []))],
                           "plan_warnings": slots.get("warnings", []), "on_screen_graphic": scene.get("on_screen_graphic"),
                           "needs_review": scene.get("needs_review"), "review_notes": scene.get("review_notes", []),
                           "labels": [label for label in labels if label.get("scene_id") == scene["scene_id"]]})
        chapters.append({"chapter_type": chapter.get("chapter_type"), "scenes": scenes})
    render = _json(work_dir(run) / "render" / "status.json") or {}
    return {"project": str(run), "title": script.get("project_title") or spec.title, "framing": spec.cinematic_framing_preset,
            "narration_seconds": timeline.get("duration"),
            "sources": [{"source_id": item["id"], "duration": item["duration"], "framing": item.get("framing")} for item in plan.get("sources", [])],
            "plan_warnings": plan.get("warnings", []), "pacing": visual.load_pacing(run), "chapters": chapters,
            "rough_cut": str(run / roughcut.VIDEO) if (run / roughcut.VIDEO).is_file() else None,
            "render_state": render.get("state"), "capcut": _json(work_dir(run) / "capcut.json")}


def replace_clip(run: Path, scene_id: str, slice_index: int, source_id: str, start: float) -> dict[str, Any]:
    """Point one planned slice at other footage, keeping its length (the narration sets it)."""
    plan_path = run / "visual_plan.json"
    plan = _json(plan_path)
    if not plan:
        raise DocuError("Dự án chưa có kế hoạch hình ảnh")
    scene = next((item for item in plan["scenes"] if item["scene_id"] == scene_id), None)
    if scene is None or not isinstance(slice_index, int) or not 0 <= slice_index < len(scene["slices"]):
        raise DocuError("Không có lát cắt đó trong cảnh")
    source = next((item for item in plan["sources"] if item["id"] == source_id), None)
    if source is None:
        raise DocuError(f"Không có nguồn {source_id}")
    piece = scene["slices"][slice_index]
    length = float(piece["end"]) - float(piece["start"])
    if not isinstance(start, (int, float)) or start < 0 or start + length > float(source["duration"]) + 1e-3:
        raise DocuError(f"Đoạn {start}–{start + length:.3f} s nằm ngoài video {source_id} ({source['duration']} s)")
    piece.update({"source_id": source_id, "start": round(float(start), 3), "end": round(float(start) + length, 3), "tier": -1})
    _publish(plan_path, plan)
    return {"scene_id": scene_id, "slice": piece, "note": "Dựng lại bản nháp (render_rough_cut) để thấy thay đổi"}


def apply_label(run: Path, scene_id: str, text: str, x: float, y: float, width: float) -> dict[str, Any]:
    plan = _json(run / "visual_plan.json") or {}
    if scene_id not in {scene["scene_id"] for scene in plan.get("scenes", [])}:
        raise DocuError(f"Không có cảnh {scene_id} trong kế hoạch hình ảnh")
    words = " ".join(str(text or "").replace("\r", "").split(" ")).strip()
    if not words or len(words) > MAX_LABEL_TEXT or any(ord(c) < 32 and c != "\n" for c in words):
        raise DocuError(f"Nhãn cần 1–{MAX_LABEL_TEXT} ký tự")
    for name, value, low, high in (("pos_x", x, 0.0, 1.0), ("pos_y", y, 0.0, 1.0), ("width", width, 0.05, 1.0)):
        if not isinstance(value, (int, float)) or not low <= value <= high:
            raise DocuError(f"{name} phải từ {low} đến {high} (tỉ lệ khung hình)")
    labels = roughcut.load_labels(run)
    if len(labels) >= MAX_LABELS:
        raise DocuError(f"Tối đa {MAX_LABELS} nhãn mỗi dự án")
    label = {"id": f"l{len(labels) + 1:03d}", "scene_id": scene_id, "text": words, "x": round(float(x), 4), "y": round(float(y), 4),
             "w": round(float(width), 4)}
    _publish(work_dir(run) / roughcut.LABELS, {"schema_version": 1, "labels": labels + [label]})
    return {"label": label, "note": "Dựng lại bản nháp (render_rough_cut) để thấy nhãn"}


def remove_label(run: Path, label_id: str) -> dict[str, Any]:
    labels = roughcut.load_labels(run)
    kept = [label for label in labels if label.get("id") != label_id]
    if len(kept) == len(labels):
        raise DocuError(f"Không có nhãn {label_id}")
    _publish(work_dir(run) / roughcut.LABELS, {"schema_version": 1, "labels": kept})
    return {"removed": label_id}


def adjust_pacing(run: Path, install: Install, *, chapter: str | None, max_clip_duration: float | None,
                  ken_burns_intensity: float | None, replan: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    pacing = visual.load_pacing(run)
    paces = dict(pacing["max_slice"])
    if max_clip_duration is not None:
        if not isinstance(max_clip_duration, (int, float)) or not visual.MIN_PACE <= max_clip_duration <= visual.MAX_PACE:
            raise DocuError(f"max_clip_duration phải từ {visual.MIN_PACE} đến {visual.MAX_PACE} giây")
        targets = CHAPTERS if chapter in (None, "", "all") else (chapter,)
        if any(target not in CHAPTERS for target in targets):
            raise DocuError(f"chapter phải là all hoặc một trong {', '.join(CHAPTERS)}")
        for target in targets:
            paces[target] = float(max_clip_duration)
    burns = pacing["ken_burns"]
    if ken_burns_intensity is not None:
        if not isinstance(ken_burns_intensity, (int, float)) or not 0 <= ken_burns_intensity <= 0.1:
            raise DocuError("ken_burns_intensity phải từ 0 (tắt) đến 0.1 (phóng 10 %)")
        burns = float(ken_burns_intensity)
    _publish(work_dir(run) / visual.PACING, {"schema_version": 1, "max_slice": paces, "ken_burns": burns})
    status = replan(run)
    if status.get("state") != "COMPLETED":
        raise DocuError(f"Lập lại kế hoạch hình ảnh không thành công: {status.get('message')}")
    plan = _json(run / "visual_plan.json") or {}
    return {"pacing": {"max_slice": paces, "ken_burns": burns}, "slices": sum(len(scene["slices"]) for scene in plan.get("scenes", [])),
            "note": "Kế hoạch đã lập lại; dựng lại bản nháp (render_rough_cut) để thấy thay đổi"}


def frame(run: Path, install: Install, seconds: float, source_id: str | None) -> bytes:
    """One PNG frame, 960 px wide, of the rough cut or of a source."""
    if source_id:
        spec = load_spec(spec_path(run))
        video = next((source.video for source in spec.sources if source.id == source_id), None)
        if video is None:
            raise DocuError(f"Không có nguồn {source_id}")
    else:
        video = run / roughcut.VIDEO
    if not video.is_file():
        raise DocuError("Chưa có video để lấy khung hình (hãy dựng bản nháp hoặc chọn source_id)")
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise DocuError("time phải là số giây ≥ 0")
    result = subprocess.run([str(install.ffmpeg), "-nostdin", "-v", "error", "-ss", f"{float(seconds):.3f}", "-i", str(video),
                             "-frames:v", "1", "-vf", "scale=960:-2", "-f", "image2pipe", "-c:v", "png", "-"],
                            capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not result.stdout.startswith(b"\x89PNG"):
        raise DocuError("Không lấy được khung hình ở thời điểm đó")
    return result.stdout


def png_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
