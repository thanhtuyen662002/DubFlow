"""The visual plan: shots, safe framing and B-roll slices per scene (ADR-0049, phase 2b).

Reads the project, the source contexts, the saved script and the narration
timeline; writes ``visual_plan.json``, which phase 2b-2 renders.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import traceback
from typing import Any, Callable

from engine.dubflow.product.job import Cancelled, ProductError, run_ffmpeg
from . import keywords, matcher, safezone, shots
from .project import FRAMING_PRESETS, DocuError, Spec, context_path, load_spec, script_path, spec_path, work_dir, write_json
from .script import load_contexts, load_draft
from .narration import _Status

OCR_PROFILE = "models/manifests/production-ocr-det-v1.json"
TIMELINE = "master_audio_timeline.json"
PLAN = "visual_plan.json"

ScannerFactory = Callable[[], Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caption_warning(source_id: str, preset: str) -> str:
    """A caption band framing cannot clear: it is blurred and darkened in place; the letterbox would shrink it."""
    covered = f"Phụ đề gốc của {source_id} cao hơn mức phóng 114% che được; phần còn lại được làm mờ và phủ dải tối"
    return covered if preset == "letterbox_235" else f"{covered}. Chọn khung Viền đen điện ảnh 2,35:1 để vùng che nhỏ hơn"


def portrait_warning(source_id: str) -> str:
    return (f"{source_id} là video dọc: khung 16:9 chỉ giữ được một dải giữa hình, nên lát cắt chỉ lấy từ nguồn này "
            "khi không còn cảnh khác. Nên thay bằng video ngang")


PACING = "pacing.json"
MIN_PACE, MAX_PACE = 2.0, 8.0
# Slices pointed at other footage by hand (MCP replace_timeline_clip); a new plan keeps them.
OVERRIDES = "overrides.json"


def load_overrides(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((work_dir(run_dir) / OVERRIDES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "script_sha256": None, "slices": {}}
    slices = value.get("slices") if isinstance(value, dict) and isinstance(value.get("slices"), dict) else {}
    return {"schema_version": 1, "script_sha256": value.get("script_sha256") if isinstance(value, dict) else None, "slices": slices}


def save_override(run_dir: Path, script_sha256: str | None, scene_id: str, index: int, source_id: str, start: float) -> None:
    saved = load_overrides(run_dir)
    if saved["script_sha256"] != script_sha256:
        saved = {"schema_version": 1, "script_sha256": script_sha256, "slices": {}}
    saved["slices"].setdefault(scene_id, {})[str(index)] = {"source_id": source_id, "start": round(float(start), 3)}
    write_json(work_dir(run_dir) / OVERRIDES, saved)


def apply_overrides(run_dir: Path, plan: dict[str, Any]) -> list[str]:
    """Put hand-picked footage back into a new plan; the slice keeps the new plan's length. Returns warnings."""
    saved = load_overrides(run_dir)
    count = sum(len(items) for items in saved["slices"].values() if isinstance(items, dict))
    if not count:
        return []
    if saved["script_sha256"] != plan.get("script_sha256"):
        (work_dir(run_dir) / OVERRIDES).unlink(missing_ok=True)
        return [f"Kịch bản đã đổi nên {count} lát cắt đã thay tay không còn áp dụng"]
    durations = {source["id"]: float(source["duration"]) for source in plan["sources"]}
    scenes = {scene["scene_id"]: scene for scene in plan["scenes"]}
    dropped = 0
    for scene_id, items in saved["slices"].items():
        for index, chosen in (items.items() if isinstance(items, dict) else ()):
            scene = scenes.get(scene_id)
            position = int(index) if str(index).isdigit() else -1
            source_id = chosen.get("source_id") if isinstance(chosen, dict) else None
            if scene is None or not 0 <= position < len(scene["slices"]) or source_id not in durations:
                dropped += 1
                continue
            piece = scene["slices"][position]
            length = float(piece["end"]) - float(piece["start"])
            start = min(max(0.0, float(chosen.get("start") or 0.0)), durations[source_id] - length)
            if start < 0:
                dropped += 1
                continue
            piece.update({"source_id": source_id, "start": round(start, 3), "end": round(start + length, 3), "tier": -1})
            # Footage picked by hand is the user's call: it is framed as planned, not as a repeat.
            piece.pop("reused", None)
            piece.pop("variation", None)
    return [f"{dropped} lát cắt đã thay tay không còn chỗ trong kế hoạch mới"] if dropped else []


def source_warnings(sources: list[dict[str, Any]], preset: str) -> list[str]:
    warnings = [caption_warning(source["id"], preset) for source in sources if not source["framing"].get("clear", True)]
    return warnings + [portrait_warning(source["id"]) for source in sources if source["framing"].get("portrait")]


def reframe(run_dir: Path, preset: str) -> dict[str, Any]:
    """Change the project's framing; the plan's slices, hand edits and scans stay, only each source's framing is redone."""
    if preset not in FRAMING_PRESETS:
        raise DocuError("Khung hình không hợp lệ")
    path = spec_path(run_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Không đọc được spec.json của phim tài liệu") from error
    raw["cinematic_framing_preset"] = preset
    write_json(path, raw)
    load_spec(path)
    plan_path = run_dir / PLAN
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"framing": preset, "plan": False, "warnings": []}
    for source in plan["sources"]:
        saved = source.get("framing") or {}
        band = safezone.Band(**saved["band"]) if saved.get("band") else None
        source["framing"] = {**saved, **safezone.framing(band, preset).to_dict()}
    plan["cinematic_framing_preset"] = preset
    others = [note for note in plan.get("warnings", []) if not note.startswith("Phụ đề gốc của") and "là video dọc" not in note]
    plan["warnings"] = source_warnings(plan["sources"], preset) + others
    write_json(plan_path, plan)
    return {"framing": preset, "plan": True, "warnings": plan["warnings"],
            "sources": [{"source_id": source["id"], "clear": source["framing"]["clear"]} for source in plan["sources"]]}


COVER_EDITS = "cover_edits.json"


def load_cover_edits(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Per-source caption band and logo boxes set by hand in Chỉnh sửa (#520); they win over the scans."""
    try:
        value = json.loads((work_dir(run_dir) / COVER_EDITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    edits = value.get("sources") if isinstance(value, dict) else None
    return {key: edit for key, edit in edits.items() if isinstance(edit, dict)} if isinstance(edits, dict) else {}


def _checked_band(band: Any) -> safezone.Band | None:
    if band is None:
        return None
    if not isinstance(band, dict) or not all(isinstance(band.get(key), (int, float)) for key in ("top", "bottom")):
        raise DocuError("Dải che không hợp lệ")
    top, bottom = float(band["top"]), float(band["bottom"])
    if not 0.0 <= top < bottom <= 1.0 or bottom - top < 0.01:
        raise DocuError("Dải che phải nằm trong khung hình, mép trên cao hơn mép dưới")
    return safezone.Band(round(top, 4), round(bottom, 4), float(band.get("share", 1.0)) if isinstance(band.get("share"), (int, float)) else 1.0)


def _checked_logos(logos: Any) -> list[list[float]]:
    if not isinstance(logos, list) or len(logos) > 16:
        raise DocuError("Danh sách logo không hợp lệ")
    result = []
    for logo in logos:
        if (not isinstance(logo, (list, tuple)) or len(logo) != 4 or not all(isinstance(part, (int, float)) for part in logo)):
            raise DocuError("Vùng logo không hợp lệ")
        x, y, w, h = (float(part) for part in logo)
        if not (0 <= x and 0 <= y and w >= 0.01 and h >= 0.01 and x + w <= 1.000001 and y + h <= 1.000001):
            raise DocuError("Vùng logo phải nằm trong khung hình")
        result.append([round(x, 4), round(y, 4), round(w, 4), round(h, 4)])
    return result


def _apply_cover(source: dict[str, Any], band: safezone.Band | None, logos: list[list[float]], preset: str) -> None:
    saved = source.get("framing") or {}
    source["framing"] = {**saved, "band": band.__dict__ if band else None, **safezone.framing(band, preset).to_dict(),
                         "logos": logos, "cover_edited": True}


def set_cover(run_dir: Path, source_id: str, band: Any, logos: Any) -> dict[str, Any]:
    """Set one source's caption band (None: no cover) and logo boxes by hand; kept across re-planning, applied on the next render."""
    checked_band, checked_logos = _checked_band(band), _checked_logos(logos)
    spec = load_spec(spec_path(run_dir))
    if source_id not in {source.id for source in spec.sources}:
        raise DocuError("Không có nguồn này trong phim tài liệu")
    edits = load_cover_edits(run_dir)
    edits[source_id] = {"band": checked_band.__dict__ if checked_band else None, "logos": checked_logos}
    write_json(work_dir(run_dir) / COVER_EDITS, {"schema_version": 1, "sources": edits})
    plan_path = run_dir / PLAN
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"plan": False, "warnings": []}
    preset = plan.get("cinematic_framing_preset") or spec.cinematic_framing_preset
    for source in plan["sources"]:
        if source.get("id") == source_id:
            _apply_cover(source, checked_band, checked_logos, preset)
    others = [note for note in plan.get("warnings", []) if not note.startswith("Phụ đề gốc của") and "là video dọc" not in note]
    plan["warnings"] = source_warnings(plan["sources"], preset) + others
    write_json(plan_path, plan)
    return {"plan": True, "warnings": plan["warnings"]}


def load_pacing(run_dir: Path) -> dict[str, Any]:
    """Per-chapter longest slice and the Ken Burns strength, as set over MCP; defaults when absent."""
    try:
        value = json.loads((work_dir(run_dir) / PACING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    paces = value.get("max_slice") if isinstance(value, dict) and isinstance(value.get("max_slice"), dict) else {}
    paces = {chapter: float(seconds) for chapter, seconds in paces.items()
             if isinstance(seconds, (int, float)) and MIN_PACE <= seconds <= MAX_PACE}
    burns = value.get("ken_burns") if isinstance(value, dict) else None
    return {"max_slice": paces, "ken_burns": float(burns) if isinstance(burns, (int, float)) and 0 <= burns <= 0.1 else None}


def _scenes(script: dict[str, Any], timeline: dict[str, Any], paces: dict[str, float] | None = None) -> list[matcher.Scene]:
    by_id = {scene["scene_id"]: scene for chapter in script["chapters"] for scene in chapter["scenes"]}
    paces = paces or {}
    result = []
    for timed in timeline["scenes"]:
        scene = by_id.get(timed["scene_id"], {})
        cited = tuple(matcher.Ref(ref["source_id"], float(ref["start"]), float(ref["end"])) for ref in scene.get("matched_source_refs", []))
        pace = paces.get(timed.get("chapter_type"), matcher.MAX_SLICE)
        tags = [tag for tag in scene.get("visual_search_tags") or [] if isinstance(tag, str)]
        terms = keywords.scene_terms(tags, str(scene.get("narration_text") or ""))
        result.append(matcher.Scene(timed["scene_id"], float(timed["start"]), float(timed["slot_end"]), cited, pace, terms))
    if result:
        # The film starts with the first picture, not with the first word.
        first = result[0]
        result[0] = matcher.Scene(first.scene_id, 0.0, first.end, first.refs, first.max_slice, first.terms)
    return result


def source_lines(context: dict[str, Any]) -> tuple[tuple[float, float, frozenset[str]], ...]:
    """Each source line's time and concepts, for the matcher's keyword ranking."""
    lines = []
    for segment in context.get("segments", []):
        if isinstance(segment, dict) and isinstance(segment.get("original_text"), str):
            try:
                start, end = float(segment["start"]), float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            found = keywords.concepts(segment["original_text"])
            if found:
                lines.append((start, end, found))
    return tuple(sorted(lines, key=lambda line: (line[0], line[1])))


def run(run_dir: Path, spec: Spec, *, ffmpeg: Path, scanner: ScannerFactory | None, ocr_identity: dict[str, Any] | None,
        ffmpeg_runner: Callable = run_ffmpeg) -> dict[str, Any]:
    work = work_dir(run_dir) / "visual"
    work.mkdir(parents=True, exist_ok=True)
    cancel = work / "cancel"
    cancel.unlink(missing_ok=True)
    status = _Status(work / "status.json", len(spec.sources) * 2 + 1, message="Đang chuẩn bị kế hoạch hình ảnh…")
    try:
        script = load_draft(script_path(run_dir))
        timeline_path = run_dir / TIMELINE
        try:
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DocuError("Hãy tạo lời đọc trước khi lập kế hoạch hình ảnh") from error
        if timeline.get("script_sha256") != script.get("script_sha256"):
            raise DocuError("Lời đọc đã cũ so với kịch bản; hãy tạo lại lời đọc trước")
        contexts = load_contexts(run_dir, spec)
        sources: list[matcher.Source] = []
        framings: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        done = 0
        for source in spec.sources:
            if cancel.exists():
                raise Cancelled()
            if not source.video.is_file():
                raise DocuError(f"Không tìm thấy video của {source.id}: {source.video.name}")
            duration = float(contexts[source.id]["duration"])
            status.write(done=done, message=f"Đang tách cảnh {source.id}…")
            cuts = shots.detect(source.video, duration, context_path(run_dir, source.id).with_name("shots.json"),
                                ffmpeg=ffmpeg, run=ffmpeg_runner, cancelled=cancel.exists)
            done += 1
            band, size, logos, texts = None, None, [], []
            if scanner is not None and ocr_identity is not None:
                status.write(done=done, message=f"Đang dò phụ đề gốc của {source.id}…")
                speech = [(float(s["start"]), float(s["end"])) for s in contexts[source.id]["segments"]]
                stat = source.video.stat()
                band, size = safezone.scan_band(source.video, duration=duration, speech=speech,
                                          cache=context_path(run_dir, source.id).with_name("captions.json"), scanner=scanner,
                                          identity={**ocr_identity, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
                status.write(done=done, message=f"Đang dò logo kênh trong {source.id}…")
                logos, texts = safezone.scan_frame_text(source.video, duration=duration, cache=context_path(run_dir, source.id).with_name("logos.json"),
                                            scanner=scanner, identity={**ocr_identity, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                                                        band=band)
            done += 1
            edit = load_cover_edits(run_dir).get(source.id)
            if edit is not None:
                # A band or logos set by hand in Chỉnh sửa win over this scan (#520).
                band, logos = _checked_band(edit.get("band")), _checked_logos(edit.get("logos", []))
            framing = safezone.framing(band, spec.cinematic_framing_preset)
            if not framing.clear:
                warnings.append(caption_warning(source.id, spec.cinematic_framing_preset))
            portrait = safezone.is_portrait(size)
            if portrait:
                warnings.append(portrait_warning(source.id))
            framings[source.id] = {"band": band.__dict__ if band else None, **framing.to_dict(),
                                   "frame_size": list(size) if size else None, "portrait": portrait, "logos": logos,
                                   "text_spans": texts}
            sources.append(matcher.Source(source.id, duration, tuple(cuts), portrait, tuple((a, b) for a, b in texts),
                                          source_lines(contexts[source.id])))
        status.write(done=done, message="Đang ghép lát cắt cho từng cảnh…")
        try:
            planned = matcher.plan(_scenes(script, timeline, load_pacing(run_dir)["max_slice"]), sources)
        except ValueError as error:
            raise DocuError("Video nguồn quá ngắn để dựng đủ hình cho lời đọc") from error
        plan = {
            "schema_version": 1, "script_sha256": script.get("script_sha256"), "timeline_sha256": _sha256(timeline_path),
            "duration": timeline["duration"], "cinematic_framing_preset": spec.cinematic_framing_preset,
            "rules": {"min_slice": matcher.MIN_SLICE, "max_slice": matcher.MAX_SLICE, "same_source_gap": matcher.SAME_SOURCE_GAP},
            "sources": [{"id": source.id, "duration": source.duration, "shots": len(source.cuts) + 1, "framing": framings[source.id]}
                        for source in sources],
            "scenes": [{"scene_id": scene.scene_id, "start": scene.start, "end": scene.end, "warnings": notes,
                        "slices": [{"source_id": piece.source_id, "start": piece.start, "end": piece.end, "at": piece.at,
                                    "duration": round(piece.duration, 3), "tier": piece.tier,
                                    **({"reused": True, "variation": "alt_crop"} if piece.reused else {})} for piece in slices]}
                       for scene, slices, notes in planned],
            "warnings": warnings,
        }
        plan["warnings"] += apply_overrides(run_dir, plan)
        write_json(run_dir / PLAN, plan)
        count = sum(len(scene["slices"]) for scene in plan["scenes"])
        status.write(state="COMPLETED", done=status.value["total"], message=f"Đã lập kế hoạch: {count} lát cắt")
        return status.value
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy lập kế hoạch hình ảnh")
        return status.value
    except (DocuError, ProductError) as error:
        status.write(state="FAILED", message=getattr(error, "message", str(error)))
        return status.value
    except Exception as error:  # the OCR model download, a corrupt video…: say so instead of dying
        traceback.print_exc()
        status.write(state="FAILED", message=f"Không lập được kế hoạch hình ảnh ({type(error).__name__}: {str(error)[:200]}); hãy thử lại")
        return status.value
