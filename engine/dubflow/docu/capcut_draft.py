"""The rough cut as a CapCut PC draft (ADR-0049, phase 4a).

CapCut is an adapter, never the canonical project: the draft is built from the
rendered slices of ``documentary.mp4`` and its narration, so it opens with the
same cut, and every slice stays a separate clip to trim, replace or decorate.

The draft follows CapCut 9.x (``draft_content.json`` version 360000), built
from a template derived from a CapCut-made empty draft with machine identifiers
removed. CapCut lists drafts from ``root_meta_info.json``; that file is backed
up and updated only while CapCut is closed, since CapCut rewrites it on exit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable
import uuid

from .project import DocuError, Spec, script_path, work_dir, write_json
from .script import load_draft

TEMPLATE = Path(__file__).with_name("capcut") / "capcut_9_template.json"
MICRO = 1_000_000
FPS = 30
MEDIA = "capcut_media"
INDEX = "root_meta_info.json"
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def default_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def capcut_running() -> bool:
    """CapCut rewrites its project list on exit, so nothing is written while it runs."""
    if os.name != "nt":
        return False
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "capcut.exe" in result.stdout.casefold()


def _id() -> str:
    return str(uuid.uuid4()).upper()


def _micro(seconds: float) -> int:
    return round(seconds * MICRO)


def _frames_to_micro(frame: int) -> int:
    return round(frame * MICRO / FPS)


def _template() -> dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def folder_name(title: str, root: Path) -> str:
    """``DubFlow - <title>``, numbered when a draft of that name exists."""
    base = re.sub(r"\s+", " ", _UNSAFE.sub(" ", f"DubFlow - {title}")).strip(" .")
    if len(base) > 80:
        # Cut at a word, not mid-word ("… 1.2 Million Pigs in a"): drop the trailing short words too.
        cut = base[:80].rsplit(" ", 1)[0]
        words = cut.split(" ")
        while len(words) > 3 and len(words[-1]) <= 3:
            words.pop()
        base = " ".join(words).rstrip(" .,:;-–")
    base = base or "DubFlow"
    name, number = base, 2
    while (root / name).exists():
        name, number = f"{base} ({number})", number + 1
    return name


def _link(source: Path, target: Path) -> None:
    """A hard link costs no disk space; a copy when the volumes differ."""
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _side(template: dict[str, Any], kind: str) -> dict[str, Any]:
    value = json.loads(json.dumps(template["side_materials"][kind]))
    value["id"] = _id()
    return value


def build(clips: list[tuple[Path, int, int]], narration: tuple[Path, float], *, title: str, folder: Path, root: Path,
          now_micro: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Content, meta and index entry for ``clips`` (path, first frame, frames) over ``narration`` (path, seconds)."""
    template = _template()
    content = json.loads(json.dumps(template["content"]))
    materials = content["materials"]
    draft_id = _id()
    total = max([_frames_to_micro(first + count) for _, first, count in clips] + [_micro(narration[1])])
    content.update({"id": draft_id, "name": title, "duration": total, "create_time": now_micro // MICRO,
                    "update_time": now_micro // MICRO, "fps": float(FPS)})
    content["canvas_config"] = {"ratio": "original", "width": 1920, "height": 1080, "background": None}
    video_track = json.loads(json.dumps(template["track"]))
    video_track.update({"id": _id(), "type": "video", "segments": []})
    media_items: list[dict[str, Any]] = []

    def media(path: Path, kind: str, micro: int, width: int, height: int) -> None:
        item = json.loads(json.dumps(template["media_item"]))
        item.update({"id": _id(), "file_Path": path.as_posix(), "extra_info": path.name, "duration": micro, "metetype": kind,
                     "width": width, "height": height, "roughcut_time_range": {"duration": micro, "start": 0}})
        media_items.append(item)

    for index, (path, first, count) in enumerate(clips):
        start, length = _frames_to_micro(first), _frames_to_micro(first + count) - _frames_to_micro(first)
        material = json.loads(json.dumps(template["video_material"]))
        material.update({"id": _id(), "unique_id": uuid.uuid4().hex, "local_material_id": str(uuid.uuid4()), "type": "video",
                         "duration": length, "path": path.as_posix(), "material_name": path.name, "width": 1920, "height": 1080,
                         "has_audio": False})
        materials["videos"].append(material)
        refs = [_side(template, kind) for kind in ("speeds", "placeholder_infos", "canvases", "sound_channel_mappings",
                                                    "material_colors", "vocal_separations")]
        for ref, kind in zip(refs, ("speeds", "placeholder_infos", "canvases", "sound_channel_mappings", "material_colors", "vocal_separations")):
            materials[kind].append(ref)
        segment = json.loads(json.dumps(template["video_segment"]))
        segment.update({"id": _id(), "material_id": material["id"], "extra_material_refs": [ref["id"] for ref in refs],
                        "source_timerange": {"start": 0, "duration": length}, "target_timerange": {"start": start, "duration": length},
                        "volume": 1.0, "render_index": 0, "track_render_index": 0})
        segment["clip"] = {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0},
                           "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0}
        video_track["segments"].append(segment)
        media(path, "video", length, 1920, 1080)
    voice_path, voice_seconds = narration
    voice = json.loads(json.dumps(template["audio_material"]))
    voice_length = _micro(voice_seconds)
    voice.update({"id": _id(), "unique_id": uuid.uuid4().hex, "local_material_id": str(uuid.uuid4()), "music_id": str(uuid.uuid4()),
                  "type": "music", "name": voice_path.name, "duration": voice_length, "path": voice_path.as_posix()})
    materials["audios"].append(voice)
    audio_refs = [_side(template, kind) for kind in ("speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations")]
    for ref, kind in zip(audio_refs, ("speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations")):
        materials[kind].append(ref)
    audio_segment = json.loads(json.dumps(template["audio_segment"]))
    audio_segment.update({"id": _id(), "material_id": voice["id"], "extra_material_refs": [ref["id"] for ref in audio_refs],
                          "source_timerange": {"start": 0, "duration": voice_length},
                          "target_timerange": {"start": 0, "duration": voice_length}, "volume": 1.0, "render_index": 0})
    audio_track = json.loads(json.dumps(template["track"]))
    audio_track.update({"id": _id(), "type": "audio", "attribute": 0, "segments": [audio_segment]})
    content["tracks"] = [video_track, audio_track]
    media(voice_path, "music", voice_length, 0, 0)

    meta = json.loads(json.dumps(template["meta"]))
    meta.update({"draft_id": draft_id, "draft_name": folder.name, "draft_fold_path": folder.as_posix(), "draft_root_path": str(root),
                 "draft_cover": "", "tm_draft_create": now_micro, "tm_draft_modified": now_micro, "tm_duration": total})
    meta["draft_materials"] = [{"type": 0, "value": media_items}] + [{"type": t, "value": []} for t in (1, 2, 3, 6, 7, 8)]
    entry = {"cloud_draft_cover": False, "cloud_draft_sync": False, "draft_cloud_last_action_download": False,
             "draft_cloud_purchase_info": "", "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
             "draft_cloud_videocut_purchase_info": "", "draft_cover": (folder / "draft_cover.jpg").as_posix(),
             "draft_fold_path": folder.as_posix(), "draft_id": draft_id, "draft_is_ai_shorts": False,
             "draft_is_cloud_temp_draft": False, "draft_is_infinite_canvas_draft": False, "draft_is_invisible": False,
             "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
             "draft_json_file": (folder / "draft_content.json").as_posix(), "draft_name": folder.name, "draft_new_version": "",
             "draft_root_path": root.as_posix(), "draft_timeline_materials_size": 0, "draft_type": "",
             "draft_web_article_video_enter_from": "", "pippit_avatar_url": "", "pippit_extra_info": "", "pippit_id": "",
             "pippit_user_name": "", "streaming_edit_draft_ready": True, "tm_draft_cloud_completed": "",
             "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1,
             "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1, "tm_draft_create": now_micro,
             "tm_draft_modified": now_micro, "tm_draft_removed": 0, "tm_duration": total}
    return content, meta, entry


def _side_files(folder: Path, content_id: str, now: int) -> None:
    (folder / "draft_settings").write_text(f"[General]\ndraft_create_time={now}\ndraft_last_edit_time={now}\nreal_edit_seconds=0\nreal_edit_keys=0\n",
                                           encoding="utf-8")
    write_json(folder / "timeline_layout.json", {"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [content_id],
                                                                 "timelineNames": ["Timeline 01"]}], "layoutOrientation": 1})
    write_json(folder / "draft_agency_config.json", {"is_auto_agency_enabled": False, "is_auto_agency_popup": False,
                                                     "is_single_agency_mode": False, "marterials": None, "use_converter": False,
                                                     "video_resolution": 1080})
    write_json(folder / "performance_opt_info.json", {"manual_cancle_precombine_segs": None, "need_auto_precombine_segs": None})


def _compact(path: Path, value: Any) -> None:
    """CapCut writes its JSON on one line; so do we, through a temporary file."""
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(partial, path)


def register(root: Path, entry: dict[str, Any]) -> Path:
    """Put the draft first in CapCut's project list, keeping a backup of the list."""
    index = root / INDEX
    try:
        value = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Không đọc được danh sách dự án của CapCut; hãy mở CapCut một lần rồi thử lại") from error
    if not isinstance(value, dict) or not isinstance(value.get("all_draft_store"), list):
        raise DocuError("Danh sách dự án của CapCut không đúng định dạng")
    backup = root / f"{INDEX}.dubflow-backup"
    shutil.copy2(index, backup)
    value["all_draft_store"] = [entry] + [item for item in value["all_draft_store"] if item.get("draft_fold_path") != entry["draft_fold_path"]]
    value["draft_ids"] = int(value.get("draft_ids") or 0) + 1
    value.setdefault("root_path", root.as_posix())
    _compact(index, value)
    return backup


def export(run_dir: Path, spec: Spec, *, root: Path, running: Callable[[], bool] = capcut_running,
           clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Write the draft and list it in CapCut; the result names the draft folder."""
    if running():
        raise DocuError("Hãy đóng CapCut trước khi xuất: CapCut ghi đè danh sách dự án khi thoát")
    if not (root / INDEX).is_file():
        raise DocuError("Không tìm thấy thư mục dự án của CapCut; hãy mở CapCut một lần hoặc chọn đúng thư mục")
    render = work_dir(run_dir) / "render"
    plan_path, narration = run_dir / "visual_plan.json", run_dir / "narration.wav"
    if not (run_dir / "documentary.mp4").is_file() or not narration.is_file() or not plan_path.is_file():
        raise DocuError("Hãy dựng bản nháp trước khi xuất sang CapCut")
    try:
        receipts = sorted(render.glob("slice-*.json"))
        parts = (render / "concat.txt").read_text(encoding="utf-8").split("\n")
    except (OSError, ValueError) as error:
        raise DocuError("Không đọc được bản nháp đã dựng; hãy dựng lại") from error
    names = [line[len("file '"):-1] for line in parts if line.startswith("file '")]
    if not names or len(receipts) < sum(1 for name in names if name.startswith("slice-")):
        raise DocuError("Bản nháp đã dựng thiếu lát cắt; hãy dựng lại")
    frames: list[int] = []
    for name in names:
        receipt = render / name.replace(".mp4", ".json")
        if name == "card.mp4":
            frames.append(round(6.0 * FPS))
            continue
        try:
            frames.append(int(json.loads(receipt.read_text(encoding="utf-8"))["key"]["frames"]))
        except (OSError, ValueError, KeyError) as error:
            raise DocuError("Bản nháp đã dựng thiếu thông tin lát cắt; hãy dựng lại") from error
    media = run_dir / MEDIA
    media.mkdir(exist_ok=True)
    clips: list[tuple[Path, int, int]] = []
    first = 0
    for name, count in zip(names, frames):
        target = media / name
        _link(render / name, target)
        clips.append((target, first, count))
        first += count
    script = load_draft(script_path(run_dir))
    title = str(script.get("project_title") or spec.title)
    folder = root / folder_name(title, root)
    now = clock()
    seconds = float(json.loads((run_dir / "master_audio_timeline.json").read_text(encoding="utf-8"))["duration"])
    content, meta, entry = build(clips, (narration, seconds), title=title, folder=folder, root=root, now_micro=int(now * MICRO))
    partial = root / f".{folder.name}.dubflow-partial"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir()
    _compact(partial / "draft_content.json", content)
    _compact(partial / "draft_meta_info.json", meta)
    _side_files(partial, content["id"], int(now))
    os.replace(partial, folder)
    backup = register(root, entry)
    result = {"schema_version": 1, "folder": str(folder), "name": folder.name, "clips": len(clips),
              "duration": round(content["duration"] / MICRO, 3), "backup": str(backup), "exported_at": int(now),
              "captions": str(run_dir / "captions_en.srt")}
    write_json(work_dir(run_dir) / "capcut.json", result)
    return result
