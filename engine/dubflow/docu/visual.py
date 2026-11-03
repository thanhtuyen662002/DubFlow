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
from . import matcher, safezone, shots
from .project import DocuError, Spec, context_path, script_path, work_dir, write_json
from .script import load_contexts, load_draft
from .narration import _Status

OCR_PROFILE = "models/manifests/production-ocr-det-v1.json"
TIMELINE = "master_audio_timeline.json"
PLAN = "visual_plan.json"

ScannerFactory = Callable[[], Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caption_warning(source_id: str, preset: str) -> str:
    """A caption band framing cannot clear: it is covered by edge stretch; the letterbox would shrink it."""
    covered = f"Phụ đề gốc của {source_id} cao hơn mức phóng 114% che được; phần còn lại được che bằng cách kéo giãn mép ảnh"
    return covered if preset == "letterbox_235" else f"{covered}. Chọn khung Letterbox 2.35:1 để vùng che nhỏ hơn"


PACING = "pacing.json"
MIN_PACE, MAX_PACE = 2.0, 8.0


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
    refs = {scene["scene_id"]: scene.get("matched_source_refs", []) for chapter in script["chapters"] for scene in chapter["scenes"]}
    paces = paces or {}
    result = []
    for timed in timeline["scenes"]:
        cited = tuple(matcher.Ref(ref["source_id"], float(ref["start"]), float(ref["end"])) for ref in refs.get(timed["scene_id"], []))
        pace = paces.get(timed.get("chapter_type"), matcher.MAX_SLICE)
        result.append(matcher.Scene(timed["scene_id"], float(timed["start"]), float(timed["slot_end"]), cited, pace))
    if result:
        # The film starts with the first picture, not with the first word.
        first = result[0]
        result[0] = matcher.Scene(first.scene_id, 0.0, first.end, first.refs, first.max_slice)
    return result


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
            band = None
            if scanner is not None and ocr_identity is not None:
                status.write(done=done, message=f"Đang dò phụ đề gốc của {source.id}…")
                speech = [(float(s["start"]), float(s["end"])) for s in contexts[source.id]["segments"]]
                stat = source.video.stat()
                band = safezone.scan_band(source.video, duration=duration, speech=speech,
                                          cache=context_path(run_dir, source.id).with_name("captions.json"), scanner=scanner,
                                          identity={**ocr_identity, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            done += 1
            framing = safezone.framing(band, spec.cinematic_framing_preset)
            if not framing.clear:
                warnings.append(caption_warning(source.id, spec.cinematic_framing_preset))
            framings[source.id] = {"band": band.__dict__ if band else None, **framing.to_dict()}
            sources.append(matcher.Source(source.id, duration, tuple(cuts)))
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
                                    "duration": round(piece.duration, 3), "tier": piece.tier} for piece in slices]}
                       for scene, slices, notes in planned],
            "warnings": warnings,
        }
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
