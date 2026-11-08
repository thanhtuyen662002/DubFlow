"""The visual plan: shots, safe framing and B-roll slices per scene (ADR-0049, phase 2b).

Reads the project, the source contexts, the saved script and the narration
timeline; writes ``visual_plan.json``, which phase 2b-2 renders.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import traceback
from typing import Any, Callable

from engine.dubflow.product.job import Cancelled, ProductError, run_ffmpeg
from . import infographic, keywords, mapgen, matcher, safezone, shots
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


def logo_first_warning(source_id: str) -> str:
    """Framing that crops a big logo away and covers the caption band instead (Tier-3)."""
    return (f"Logo của {source_id} quá to để làm mờ: khung hình đẩy logo ra ngoài, phụ đề gốc ở đáy được phủ dải tối "
            "sát viền đen")


def portrait_warning(source_id: str) -> str:
    return (f"{source_id} là video dọc: khung 16:9 chỉ giữ được một dải giữa hình, nên lát cắt chỉ lấy từ nguồn này "
            "khi không còn cảnh khác. Nên thay bằng video ngang")


def heavy_warning(source_id: str) -> str:
    return (f"{source_id} có logo/watermark lớn không cắt ra khỏi khung được: chỉ dùng khi các nguồn sạch đã hết cảnh")


def unusable_warning(source_id: str) -> str:
    return (f"{source_id} bị bỏ qua: logo/watermark lớn và chữ đồ họa chiếm hơn 60% thời lượng "
            "(chỉ dùng nếu không còn nguồn nào khác)")


def slice_json(piece: matcher.Slice) -> dict[str, Any]:
    value = {"source_id": piece.source_id, "start": piece.start, "end": piece.end, "at": piece.at,
             "duration": round(piece.duration, 3), "tier": piece.tier}
    if piece.kind != matcher.FOOTAGE:
        value.update({"kind": piece.kind, "ref": piece.ref})
    if piece.crossed:
        value["crossed"] = True
    if piece.reused:
        value.update({"reused": True, "variation": "alt_crop"})
    return value


PACING = "pacing.json"
MIN_PACE, MAX_PACE = 2.0, 8.0
# Slices pointed at other footage by hand (MCP replace_timeline_clip); a new plan keeps them.
OVERRIDES = "overrides.json"


def load_overrides(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((work_dir(run_dir) / OVERRIDES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "script_sha256": None, "slices": {}, "scenes": {}}
    slices = value.get("slices") if isinstance(value, dict) and isinstance(value.get("slices"), dict) else {}
    scenes = value.get("scenes") if isinstance(value, dict) and isinstance(value.get("scenes"), dict) else {}
    return {"schema_version": 1, "script_sha256": value.get("script_sha256") if isinstance(value, dict) else None, "slices": slices,
            "scenes": scenes}


def save_override(run_dir: Path, script_sha256: str | None, scene_id: str, index: int, source_id: str, start: float) -> None:
    saved = load_overrides(run_dir)
    if saved["script_sha256"] != script_sha256:
        saved = {"schema_version": 1, "script_sha256": script_sha256, "slices": {}, "scenes": {}}
    cut = saved["scenes"].get(scene_id)
    if isinstance(cut, dict) and isinstance(cut.get("slices"), list) and 0 <= index < len(cut["slices"]):
        # The scene was cut by hand (#630): the new footage goes into that cut, at its length.
        piece = cut["slices"][index]
        length = float(piece["end"]) - float(piece["start"])
        piece.update({"source_id": source_id, "start": round(float(start), 3), "end": round(float(start) + length, 3), "tier": -1})
        for key in ("reused", "variation", "kind", "ref", "crossed"):
            piece.pop(key, None)
    else:
        saved["slices"].setdefault(scene_id, {})[str(index)] = {"source_id": source_id, "start": round(float(start), 3)}
    write_json(work_dir(run_dir) / OVERRIDES, saved)


def apply_overrides(run_dir: Path, plan: dict[str, Any]) -> list[str]:
    """Put hand-picked footage back into a new plan; the slice keeps the new plan's length. Returns warnings."""
    saved = load_overrides(run_dir)
    count = sum(len(items) for items in saved["slices"].values() if isinstance(items, dict))
    count += sum(len(cut.get("slices") or []) for cut in saved["scenes"].values() if isinstance(cut, dict))
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
            # Footage picked by hand is the user's call: framed as planned, not as a repeat; it replaces a generated
            # picture (map, data slide) too.
            for key in ("reused", "variation", "kind", "ref", "crossed"):
                piece.pop(key, None)
    graphics = plan.get("graphics") if isinstance(plan.get("graphics"), dict) else {}
    for scene_id, cut in saved["scenes"].items():
        # A scene cut by hand in Chỉnh sửa (#630) comes back whole while its narration timing is unchanged.
        scene = scenes.get(scene_id)
        pieces = cut.get("slices") if isinstance(cut, dict) else None
        if (scene is None or not isinstance(pieces, list) or not pieces
                or abs(float(cut.get("start", -1)) - float(scene["start"])) > 1e-3 or abs(float(cut.get("end", -1)) - float(scene["end"])) > 1e-3):
            dropped += len(pieces) if isinstance(pieces, list) else 1
            continue
        try:
            items = [{**piece, "from": None} for piece in pieces]
            kept = _edited_scene(scene, items, {}, durations)
        except (DocuError, AttributeError, TypeError, ValueError):
            dropped += len(pieces)
            continue
        for piece, before in zip(kept, pieces):
            piece["tier"] = before.get("tier", -1)
            piece.update({key: before[key] for key in PLANNED_KEYS if key in before})
            if piece.get("kind") and str(piece.get("ref")) not in graphics:
                for key in PLANNED_KEYS:
                    piece.pop(key, None)
        scene["slices"] = kept
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
        source["framing"] = {**saved, **safezone.frame_logos(band, saved.get("logos") or [], preset).to_dict()}
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
    source["framing"] = {**saved, "band": band.__dict__ if band else None, **safezone.frame_logos(band, logos, preset).to_dict(),
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


# Cuts edited by hand in Chỉnh sửa (#630): the shortest a trimmed cut may get, and what a piece carries over
# from the plan only while it still shows the planned footage.
MIN_CUT = 0.5
MAX_CUTS = 400
PLANNED_KEYS = ("kind", "ref", "reused", "variation", "crossed")


def cover_framing(saved: dict[str, Any], cover: dict[str, Any] | None, preset: str) -> dict[str, Any]:
    """A source's framing with one cut's own caption band and logo boxes (#630); the source's when it has none."""
    if not isinstance(cover, dict):
        return saved
    band = cover.get("band")
    checked = safezone.Band(float(band["top"]), float(band["bottom"]), float(band.get("share", 1.0))) if isinstance(band, dict) else None
    logos = [list(logo) for logo in cover.get("logos") or []]
    return {**saved, "band": checked.__dict__ if checked else None, **safezone.frame_logos(checked, logos, preset).to_dict(), "logos": logos}


def _checked_cover(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DocuError("Vùng che của lát cắt không hợp lệ")
    band = _checked_band(value.get("band"))
    return {"band": band.__dict__ if band else None, "logos": _checked_logos(value.get("logos") or [])}


def _edited_scene(scene: dict[str, Any], items: Any, originals: dict[tuple[str, int], dict[str, Any]],
                  durations: dict[str, float]) -> list[dict[str, Any]]:
    """One scene's cuts as edited: they tile the scene (its narration timing stays), each inside its source."""
    if not isinstance(items, list) or not items or len(items) > MAX_CUTS:
        raise DocuError(f"Cảnh {scene['scene_id']} cần ít nhất một lát cắt")
    begin, finish = float(scene["start"]), float(scene["end"])
    ats: list[float] = []
    for item in items:
        at = item.get("at") if isinstance(item, dict) else None
        if not isinstance(at, (int, float)):
            raise DocuError("Lát cắt thiếu thời điểm bắt đầu")
        ats.append(float(at))
    # The first cut starts with the scene, whatever the editor rounded.
    if abs(ats[0] - begin) > 0.05:
        raise DocuError(f"Lát cắt đầu của cảnh {scene['scene_id']} phải bắt đầu cùng cảnh")
    ats[0] = begin
    ends = ats[1:] + [finish]
    pieces: list[dict[str, Any]] = []
    for item, at, end in zip(items, ats, ends):
        length = round(end - at, 3)
        if length < MIN_CUT - 1e-6:
            raise DocuError(f"Mỗi lát cắt cần dài ít nhất {MIN_CUT} giây")
        source_id = item.get("source_id")
        if source_id not in durations:
            raise DocuError(f"Không có nguồn {source_id}")
        start = item.get("start")
        if not isinstance(start, (int, float)) or start < 0:
            raise DocuError("Điểm bắt đầu trong video nguồn không hợp lệ")
        if length > durations[source_id] + 1e-3:
            raise DocuError(f"Video {source_id} ngắn hơn lát cắt ({length:.1f} s)")
        # A cut that grew past the end of its footage starts earlier instead.
        start = round(max(0.0, min(float(start), durations[source_id] - length)), 3)
        piece: dict[str, Any] = {"source_id": source_id, "start": start, "end": round(start + length, 3), "at": round(at, 3),
                                 "duration": length, "tier": -1}
        origin = item.get("from")
        planned = originals.get((str(origin[0]), int(origin[1]))) if (isinstance(origin, list) and len(origin) == 2
                                                                      and isinstance(origin[1], int)) else None
        if planned and planned.get("source_id") == source_id and abs(float(planned.get("start", -1)) - start) < 2e-3:
            # Still the planned footage: it keeps how it was planned (a map, a data slide, a repeat's other crop).
            piece["tier"] = planned.get("tier", 0)
            piece.update({key: planned[key] for key in PLANNED_KEYS if key in planned})
        cover = _checked_cover(item.get("cover"))
        if cover is not None:
            piece["cover"] = cover
        pieces.append(piece)
    return pieces


def edit_slices(run_dir: Path, request: Any) -> dict[str, Any]:
    """Save the cuts edited in Chỉnh sửa (#630): trimmed, swapped, removed, replaced, each with its own cover.

    ``request`` is ``{"scenes": [{"scene_id", "slices": [{"from": [scene_id, index] | null, "source_id", "start",
    "at", "cover": {"band", "logos"} | null}]}]}``; only the scenes it names change. The next render uses them and
    a new plan keeps them while the scene's narration timing is the same.
    """
    scenes_in = request.get("scenes") if isinstance(request, dict) else None
    if not isinstance(scenes_in, list) or not scenes_in:
        raise DocuError("Không có lát cắt nào để lưu")
    plan_path = run_dir / PLAN
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Dự án chưa có kế hoạch hình ảnh") from error
    durations = {source["id"]: float(source["duration"]) for source in plan["sources"]}
    scenes = {scene["scene_id"]: scene for scene in plan["scenes"]}
    originals = {(scene["scene_id"], index): dict(piece) for scene in plan["scenes"] for index, piece in enumerate(scene["slices"])}
    edited: dict[str, list[dict[str, Any]]] = {}
    for raw in scenes_in:
        scene_id = raw.get("scene_id") if isinstance(raw, dict) else None
        if scene_id not in scenes or scene_id in edited:
            raise DocuError(f"Không có cảnh {scene_id} trong kế hoạch hình ảnh")
        edited[scene_id] = _edited_scene(scenes[scene_id], raw.get("slices"), originals, durations)
    graphics = plan.get("graphics") if isinstance(plan.get("graphics"), dict) else {}
    for pieces in edited.values():
        for piece in pieces:
            if piece.get("kind") and str(piece.get("ref")) not in graphics:
                for key in PLANNED_KEYS:
                    piece.pop(key, None)
    for scene_id, pieces in edited.items():
        scenes[scene_id]["slices"] = pieces
    write_json(plan_path, plan)
    saved = load_overrides(run_dir)
    if saved["script_sha256"] != plan.get("script_sha256"):
        saved = {"schema_version": 1, "script_sha256": plan.get("script_sha256"), "slices": {}, "scenes": {}}
    cuts = saved.get("scenes") if isinstance(saved.get("scenes"), dict) else {}
    for scene_id, pieces in edited.items():
        # The whole scene as cut by hand wins over single replaced slices in it.
        saved["slices"].pop(scene_id, None)
        cuts[scene_id] = {"start": scenes[scene_id]["start"], "end": scenes[scene_id]["end"], "slices": pieces}
    saved["scenes"] = cuts
    write_json(work_dir(run_dir) / OVERRIDES, saved)
    return {"scenes": len(edited), "slices": sum(len(scene["slices"]) for scene in plan["scenes"])}


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
    for index, timed in enumerate(timeline["scenes"]):
        scene = by_id.get(timed["scene_id"], {})
        cited = tuple(matcher.Ref(ref["source_id"], float(ref["start"]), float(ref["end"])) for ref in scene.get("matched_source_refs", []))
        pace = paces.get(timed.get("chapter_type"), matcher.MAX_SLICE)
        tags = [tag for tag in scene.get("visual_search_tags") or [] if isinstance(tag, str)]
        terms = keywords.scene_terms(tags, str(scene.get("narration_text") or ""))
        result.append(matcher.Scene(timed["scene_id"], float(timed["start"]), float(timed["slot_end"]), cited, pace, terms,
                                    _hold(timeline["scenes"], index)))
    if result:
        # The film starts with the first picture, not with the first word.
        result[0] = replace(result[0], start=0.0)
    return result


def _hold(timed: list[dict[str, Any]], index: int) -> str:
    """A held 5–6 s shot opens the hook and the engineering act and closes the verdict (Tier-3 pacing)."""
    kind = timed[index].get("chapter_type")
    before = timed[index - 1].get("chapter_type") if index else None
    after = timed[index + 1].get("chapter_type") if index + 1 < len(timed) else None
    if kind in ("HOOK", "ENGINEERING_SOLUTION") and before != kind:
        return "first"
    if kind == "CRITICAL_EVALUATION" and after != kind:
        return "last"
    return ""


def add_graphics(scenes: list[matcher.Scene], script: dict[str, Any], timeline: dict[str, Any], place: dict[str, Any] | None,
                 ) -> tuple[list[matcher.Scene], dict[str, dict[str, Any]]]:
    """Reserve the satellite fly-in and up to four data slides in the scenes' slots (Tier-3); {ref: what to draw}."""
    by_id = {scene["scene_id"]: (chapter.get("chapter_type"), scene) for chapter in script["chapters"] for scene in chapter["scenes"]}
    rows = []
    for planned in scenes:
        chapter, scene = by_id.get(planned.scene_id, (None, {}))
        rows.append({"chapter_type": chapter, "on_screen_graphic": scene.get("on_screen_graphic"),
                     "narration_text": scene.get("narration_text"), "slot": planned.end - planned.start})
    graphics: dict[str, dict[str, Any]] = {}
    inserts: dict[int, list[matcher.Insert]] = {}
    map_index = mapgen.scene_for(rows) if place else None
    if map_index is not None:
        spot = mapgen.placement(rows[map_index]["slot"], matcher.shortest(scenes[map_index].max_slice))
        if spot is not None:
            graphics["map_01"] = {"kind": "map", **place, "scene_id": scenes[map_index].scene_id}
            inserts.setdefault(map_index, []).append(matcher.Insert("map", spot[0], spot[1], "map_01"))
        else:
            map_index = None
    for number, (index, slide) in enumerate(infographic.select(rows, () if map_index is None else (map_index,)).items(), 1):
        spot = infographic.placement(rows[index]["slot"], matcher.shortest(scenes[index].max_slice))
        if spot is None:
            continue
        ref = f"info_{number:02d}"
        backdrop = scenes[index].refs[0] if scenes[index].refs else None
        graphics[ref] = {**slide, "scene_id": scenes[index].scene_id,
                         "backdrop": {"source_id": backdrop.source_id, "at": round((backdrop.start + backdrop.end) / 2, 3)} if backdrop else None}
        inserts.setdefault(index, []).append(matcher.Insert("infographic", spot[0], spot[1], ref, backdrop.source_id if backdrop else ""))
    result = [replace(scene, inserts=tuple(inserts[index])) if index in inserts else scene for index, scene in enumerate(scenes)]
    return result, graphics


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
            # Logos cropped away where a small move or zoom can do it (Tier-3), blurred only where it cannot.
            framing = safezone.frame_logos(band, logos, spec.cinematic_framing_preset)
            if not framing.clear:
                warnings.append(logo_first_warning(source.id) if safezone.framing(band, spec.cinematic_framing_preset).clear
                                else caption_warning(source.id, spec.cinematic_framing_preset))
            portrait = safezone.is_portrait(size)
            if portrait:
                warnings.append(portrait_warning(source.id))
            marks = safezone.watermark_state(framing, logos, texts, duration)
            if not marks["usable"]:
                warnings.append(unusable_warning(source.id))
            elif marks["heavy_watermark"]:
                warnings.append(heavy_warning(source.id))
            framings[source.id] = {"band": band.__dict__ if band else None, **framing.to_dict(),
                                   "frame_size": list(size) if size else None, "portrait": portrait, "logos": logos,
                                   "text_spans": texts, **marks}
            sources.append(matcher.Source(source.id, duration, tuple(cuts), portrait, tuple((a, b) for a, b in texts),
                                          source_lines(contexts[source.id]), marks["heavy_watermark"], marks["usable"]))
        place = mapgen.target(script)
        if place is not None:
            # Fetched now so the render works offline; without a network the map is drawn as a vector chart.
            status.write(done=done, message="Đang tải ảnh vệ tinh của địa điểm…")
            mapgen.fetch_tiles(place["lat"], place["lon"], mapgen.tile_folder(work_dir(run_dir)), cancelled=cancel.exists)
        status.write(done=done, message="Đang ghép lát cắt cho từng cảnh…")
        scenes, graphics = add_graphics(_scenes(script, timeline, load_pacing(run_dir)["max_slice"]), script, timeline, place)
        try:
            planned = matcher.plan(scenes, sources)
        except ValueError as error:
            raise DocuError("Video nguồn quá ngắn để dựng đủ hình cho lời đọc") from error
        plan = {
            "schema_version": 1, "script_sha256": script.get("script_sha256"), "timeline_sha256": _sha256(timeline_path),
            "duration": timeline["duration"], "cinematic_framing_preset": spec.cinematic_framing_preset,
            "rules": {"min_slice": matcher.MIN_SLICE, "max_slice": matcher.MAX_SLICE, "same_source_gap": matcher.SAME_SOURCE_GAP,
                      "shot_margin": matcher.SHOT_MARGIN, "one_shot_per_slice": True},
            "sources": [{"id": source.id, "duration": source.duration, "shots": len(source.cuts) + 1, "framing": framings[source.id],
                         "heavy_watermark": source.heavy, "usable": source.usable} for source in sources],
            "scenes": [{"scene_id": scene.scene_id, "start": scene.start, "end": scene.end, "warnings": notes,
                        "slices": [slice_json(piece) for piece in slices]}
                       for scene, slices, notes in planned],
            "graphics": graphics,
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
