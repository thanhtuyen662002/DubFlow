"""Validate and normalise ``documentary_script.json`` (ADR-0049).

Only an unusable draft is an error. Everything else is repaired and noted, and
numbers are never removed: a number that the sources, the conversions or the
benchmark table do not explain marks its scene ``needs_review`` for the user.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Sequence
import unicodedata

from . import units
from .project import DocuError, Spec, context_path

CHAPTERS = ("HOOK", "PROBLEM", "ENGINEERING_SOLUTION", "CRITICAL_EVALUATION")
CHAPTER_NAMES = {"HOOK": "Mở đầu", "PROBLEM": "Vấn đề", "ENGINEERING_SOLUTION": "Giải pháp kỹ thuật",
                 "CRITICAL_EVALUATION": "Đánh giá"}
MOODS = ("cinematic_tension_drone", "epic_build", "curious_pulse", "reflective_ambient", "hopeful_rise", "dark_suspense")
DEFAULT_MOODS = {"HOOK": "cinematic_tension_drone", "PROBLEM": "dark_suspense",
                 "ENGINEERING_SOLUTION": "curious_pulse", "CRITICAL_EVALUATION": "reflective_ambient"}
SFX_TAGS = ("wind_high_altitude", "heavy_machinery_hum", "water_turbine_roar", "data_beeps", "construction_site",
            "city_traffic", "flowing_water", "rain", "crowd_murmur", "industrial_metal")
GRAPHIC_TYPES = ("METRIC_CALLOUT", "LOWER_THIRD", "LABEL")
MAX_SCENES = 160
MAX_NARRATION = 600
MAX_REFS = 6
MAX_TAGS = 6
MAX_ACKNOWLEDGED = 30
MIN_REF_SECONDS = 0.5
SCRIPT_LIMIT = 4 * 1024 * 1024
CONTEXT_LIMIT = 64 * 1024 * 1024


def _text(value: Any, limit: int) -> str:
    if type(value) is not str:
        return ""
    value = "".join(" " if unicodedata.category(c) == "Cc" else c for c in value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _number(value: Any) -> float | None:
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    return None


def load_contexts(run_dir: Path, spec: Spec) -> dict[str, dict[str, Any]]:
    contexts = {}
    for source in spec.sources:
        path = context_path(run_dir, source.id)
        try:
            if path.stat().st_size > CONTEXT_LIMIT:
                raise ValueError("too large")
            contexts[source.id] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DocuError(f"Chưa có ngữ cảnh của {source.id}; hãy tạo lại dự án") from error
    return contexts


def source_quantities(contexts: dict[str, dict[str, Any]]) -> list[units.Quantity]:
    """Recomputed from the source lines, so a stale or edited context cannot vouch for a number."""
    found: list[units.Quantity] = []
    for context in contexts.values():
        for segment in context.get("segments", []):
            if type(segment) is dict and type(segment.get("original_text")) is str:
                found.extend(units.extract(segment["original_text"]))
    return found


def _refs(value: Any, durations: dict[str, float]) -> tuple[list[dict[str, Any]], int]:
    """Valid refs clamped to their video, and how many were dropped."""
    refs: list[dict[str, Any]] = []
    dropped = 0
    for item in value if type(value) is list else []:
        if type(item) is not dict or item.get("source_id") not in durations:
            dropped += 1
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        duration = durations[item["source_id"]]
        if start is None or end is None:
            dropped += 1
            continue
        start, end = max(0.0, start), min(end, duration)
        if end - start < MIN_REF_SECONDS:
            dropped += 1
            continue
        ref = {"source_id": item["source_id"], "start": round(start, 3), "end": round(end, 3)}
        if ref not in refs:
            refs.append(ref)
    return refs[:MAX_REFS], dropped


def _graphic(value: Any) -> dict[str, str] | None:
    if type(value) is not dict:
        return None
    graphic = {"type": value.get("type") if value.get("type") in GRAPHIC_TYPES else "METRIC_CALLOUT",
               "title": _text(value.get("title"), 60), "value": _text(value.get("value"), 60),
               "subtext": _text(value.get("subtext"), 120)}
    return graphic if graphic["title"] or graphic["value"] else None


def _coordinates(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict:
        return None
    lat, lng = _number(value.get("lat")), _number(value.get("lng"))
    name = _text(value.get("location_name"), 120)
    if lat is None or lng is None or not -90 <= lat <= 90 or not -180 <= lng <= 180 or not name:
        return None
    return {"lat": round(lat, 6), "lng": round(lng, 6), "location_name": name}


def check(draft: Any, spec: Spec, contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if type(draft) is not dict or type(draft.get("chapters")) is not list:
        raise DocuError("Kịch bản không đúng định dạng")
    durations = {source_id: float(context.get("duration") or 0) for source_id, context in contexts.items()}
    quantities = source_quantities(contexts)
    notes: list[str] = []
    grouped: dict[str, list[Any]] = {chapter: [] for chapter in CHAPTERS}
    moods: dict[str, str] = {}
    unknown = 0
    for chapter in draft["chapters"]:
        kind = chapter.get("chapter_type") if type(chapter) is dict else None
        if kind not in grouped:
            unknown += 1
            continue
        grouped[kind].extend(chapter.get("scenes") if type(chapter.get("scenes")) is list else [])
        mood = chapter.get("recommended_bgm_mood")
        if kind not in moods:
            moods[kind] = mood if mood in MOODS else DEFAULT_MOODS[kind]
    if unknown:
        notes.append(f"Đã bỏ {unknown} chương không rõ loại")
    chapters: list[dict[str, Any]] = []
    total = dropped_refs = 0
    for kind in CHAPTERS:
        if kind not in moods:
            notes.append(f"Thiếu chương “{CHAPTER_NAMES[kind]}”")
            continue
        scenes = []
        for raw in grouped[kind]:
            if type(raw) is not dict:
                continue
            narration = _text(raw.get("narration_text"), MAX_NARRATION + 1)
            if not narration:
                continue
            if total >= MAX_SCENES:
                notes.append(f"Kịch bản dài quá {MAX_SCENES} cảnh; đã bỏ các cảnh sau")
                break
            total += 1
            scene_notes: list[str] = []
            if len(narration) > MAX_NARRATION:
                narration = narration[:MAX_NARRATION].rsplit(" ", 1)[0]
                scene_notes.append(f"Lời đọc dài quá {MAX_NARRATION} ký tự; đã cắt bớt")
            refs, dropped = _refs(raw.get("matched_source_refs"), durations)
            dropped_refs += dropped
            if not refs:
                scene_notes.append("Chưa có đoạn video nguồn nào khớp với cảnh này")
            graphic = _graphic(raw.get("on_screen_graphic"))
            sfx = raw.get("ambient_sfx_tag")
            if sfx is not None and sfx not in SFX_TAGS:
                sfx = None
            acknowledged = [_text(value, 60) for value in raw.get("acknowledged_numbers", [])
                            if type(value) is str and _text(value, 60)][:MAX_ACKNOWLEDGED] \
                if type(raw.get("acknowledged_numbers")) is list else []
            spoken = " ".join([narration] + ([graphic["title"], graphic["value"], graphic["subtext"]] if graphic else []))
            flagged = units.unverified(spoken, quantities, acknowledged)
            for number in flagged:
                scene_notes.append(f"Số liệu “{number}” không thấy trong nguồn hay bảng quy đổi; hãy kiểm tra")
            if not is_english(narration):
                scene_notes.append("Lời đọc không phải tiếng Anh; hãy viết lại cảnh này bằng tiếng Anh")
            metric = units.metric_units(narration)
            if metric:
                scene_notes.append("Còn đơn vị hệ mét (" + ", ".join(metric) + "); khán giả Mỹ quen feet, miles, pounds")
            tags = [_text(tag, 60) for tag in raw.get("visual_search_tags", []) if type(tag) is str and _text(tag, 60)] \
                if type(raw.get("visual_search_tags")) is list else []
            scenes.append({"scene_id": f"sc_{total:03d}", "narration_text": narration, "matched_source_refs": refs,
                           "visual_search_tags": tags[:MAX_TAGS], "on_screen_graphic": graphic,
                           "ambient_sfx_tag": sfx, "acknowledged_numbers": acknowledged,
                           "needs_review": bool(scene_notes), "review_notes": scene_notes, "review_numbers": flagged})
        chapters.append({"chapter_type": kind, "recommended_bgm_mood": moods[kind], "scenes": scenes})
    if dropped_refs:
        notes.append(f"Đã bỏ {dropped_refs} đoạn nguồn không hợp lệ hoặc ngoài thời lượng video")
    if not total:
        raise DocuError("Kịch bản chưa có cảnh nào có lời đọc")
    result = {"schema_version": 1, "project_title": _text(draft.get("project_title"), 100) or spec.title,
              "target_Coordinates_3D": _coordinates(draft.get("target_Coordinates_3D")), "chapters": chapters,
              "review_notes": notes}
    # The narration's timeline carries the same hash; a difference means it is out of date.
    result["script_sha256"] = script_sha256(result)
    return result


def is_english(text: str) -> bool:
    """Mostly plain Latin letters: English, not Vietnamese or Chinese (the page uses the same rule)."""
    letters = sum(1 for char in text if char.isalpha())
    plain = sum(1 for char in text if "a" <= char.lower() <= "z" and char.isascii())
    return letters == 0 or plain / letters >= 0.95


def script_sha256(script: dict[str, Any]) -> str:
    """The spoken content only: flags and tags do not make a narration stale."""
    spoken = [[chapter.get("chapter_type"), [[scene.get("scene_id"), scene.get("narration_text")] for scene in chapter.get("scenes", [])]]
              for chapter in script.get("chapters", [])]
    return hashlib.sha256(json.dumps(spoken, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_draft(path: Path) -> Any:
    try:
        if path.stat().st_size > SCRIPT_LIMIT:
            raise ValueError("too large")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Không đọc được kịch bản") from error


def scenes(script: dict[str, Any]) -> Sequence[dict[str, Any]]:
    return [scene for chapter in script["chapters"] for scene in chapter["scenes"]]
