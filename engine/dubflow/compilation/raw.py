"""Run a v2 programme: Tổng hợp from un-dubbed footage (ADR-0053).

1. Resolve the programme: the user's order, or an AI ranking suggestion with
   reasons (applied only when asked); narration the user left empty is drafted
   by their AI from the item's own title, notes and transcript and flagged.
2. Analyse every source once (shots, motion, silences, candidates; cached by
   content) and gather evidence (transcript, notes, file name, optional vision).
3. For each part (hook, intro, items, outro): speak its narration, plan its
   footage per sentence (``mastering/planners/compilation``), write its overlay
   and render it in one encode (``render.montage_argv``), reusing the part when
   its receipt matches.
4. Join the parts (stream copy, ``render.concat_argv`` only as the fallback), write chapters, a description with
   every source's credit and rights, the programme's captions and
   ``program.json``.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from engine.dubflow.mastering import align
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import frames as more
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import vision as seeing
from engine.dubflow.mastering.planners import compilation as planner
from engine.dubflow.product import render as narration_render, script
from engine.dubflow.product.job import Cancelled, ProductError, Speaker, Status, _atomic_text, run_ffmpeg
from engine.dubflow.product.layout import srt_document
from . import layout, render
from .program import Program

CompilationError = ProductError
AskText = Callable[[str, Mapping[str, Any], list[dict[str, Any]]], Any]

DRAFT_SYSTEM = (
    "Bạn viết lời bình tiếng Việt cho một mục của video tổng hợp giải trí. Chỉ dùng thông tin được cho (tiêu đề, ghi chú, "
    "phụ đề nguồn). Không thêm sự kiện, con số, tên người hay địa điểm không có trong thông tin. Viết 2–4 câu ngắn, tự nhiên, "
    "dễ đọc thành tiếng, không emoji. needs_check liệt kê những điều người dùng nên kiểm chứng trước khi đăng."
)
DRAFT_SCHEMA = {"type": "object", "properties": {"narration": {"type": "string"}, "needs_check": {"type": "array", "items": {"type": "string"}}},
                "required": ["narration", "needs_check"]}
RANK_SYSTEM = (
    "Bạn xếp hạng các mục của một video Top N theo mức thú vị với người xem, chỉ dựa trên thông tin được cho. "
    "Trả về thứ tự phát (mục đầu tiên phát trước) và một lý do ngắn bằng tiếng Việt cho mỗi mục."
)
RANK_SCHEMA = {"type": "object", "properties": {"order": {"type": "array", "items": {"type": "string"}},
                                                "reasons": {"type": "object", "additionalProperties": {"type": "string"}}},
               "required": ["order", "reasons"]}


def _digest_file(path: Path, cancelled: Callable[[], bool]) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if cancelled():
                raise Cancelled()
            sha.update(chunk)
    return sha.hexdigest()


class _Answers:
    """The AI's answers kept beside the job, so running again keeps the same draft and suggestion.

    Keyed by the question (instructions, schema and facts): changing an item's facts asks again;
    the user's own narration always wins over a kept draft.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.values: dict[str, Any] = {}
        try:
            if path is not None and path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                value = json.loads(path.read_text(encoding="utf-8"))
                if type(value) is dict and value.get("schema_version") == 1 and type(value.get("answers")) is dict:
                    self.values = value["answers"]
        except (OSError, ValueError):
            self.values = {}

    def ask(self, ask_text: AskText, system: str, schema: Mapping[str, Any], facts: Any, valid: Callable[[Any], bool]) -> Any:
        text = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(json.dumps([system, schema, text], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in self.values:
            return self.values[key]
        answer = ask_text(system, schema, [{"text": text}])
        if not valid(answer):
            return answer  # not kept: the next run asks again
        self.values[key] = answer
        if self.path is not None:
            _atomic_text(self.path, json.dumps({"schema_version": 1, "answers": self.values}, ensure_ascii=False))
        return answer


def resolve(spec: Program, ask_text: AskText | None, warnings: list[str], answers: Path | None = None) \
        -> tuple[Program, dict[str, Any]]:
    """The programme as it will be made: order, drafted narration, and what the AI suggested (with reasons)."""
    record: dict[str, Any] = {"ranking": spec.ranking, "suggestion": None, "drafted": {}}
    kept = _Answers(answers)
    items = list(spec.items)
    if spec.ranking == "suggested":
        if ask_text is None:
            warnings.append("Chưa có AI để đề xuất thứ hạng: giữ thứ tự bạn đã xếp.")
        else:
            facts = [{"id": i.item_id, "title": i.title, "notes": i.notes[:300], "narration": i.narration[:400]} for i in items]
            try:
                answer = kept.ask(ask_text, RANK_SYSTEM, RANK_SCHEMA, facts, lambda a: isinstance(a, Mapping)
                                  and sorted(a.get("order") or []) == sorted(i.item_id for i in items)
                                  and isinstance(a.get("reasons"), Mapping))
                order = answer.get("order") if isinstance(answer, Mapping) else None
                reasons = answer.get("reasons") if isinstance(answer, Mapping) else None
                if sorted(order or []) != sorted(i.item_id for i in items) or not isinstance(reasons, Mapping):
                    raise ValueError("not a ranking of these items")
                record["suggestion"] = {"order": list(order), "reasons": {k: " ".join(str(reasons.get(k, "")).split())[:200] for k in order}}
                if spec.apply_suggestion:
                    by_id = {i.item_id: i for i in items}
                    count = len(items)
                    items = [replace(by_id[item_id], rank=count - index if spec.kind == "top_n" else index + 1)
                             for index, item_id in enumerate(order)]
                    record["applied"] = True
            except Exception as error:  # noqa: BLE001 - a suggestion is optional
                warnings.append(f"AI không đề xuất được thứ hạng ({getattr(error, 'message', type(error).__name__)}); giữ thứ tự của bạn.")
    drafted = []
    for item in items:
        if item.narration.strip():
            drafted.append(item)
            continue
        if ask_text is None or not spec.editorial.draft_narration:
            raise CompilationError(f"Mục “{item.title}” chưa có lời bình (và chưa chọn AI để viết nháp)")
        said = []
        for source in item.sources:
            if source.transcript is not None:
                said += [text for _, _, text in ev.parse_srt(source.transcript.read_text(encoding="utf-8", errors="replace"))]
        facts = {"title": item.title, "notes": item.notes, "source_notes": [s.notes for s in item.sources if s.notes],
                 "transcript": " ".join(said)[:1500]}
        try:
            answer = kept.ask(ask_text, DRAFT_SYSTEM, DRAFT_SCHEMA, facts, lambda a: isinstance(a, Mapping)
                              and bool(script.sentences(" ".join(str(a.get("narration", "")).split()))))
            text = " ".join(str(answer.get("narration", "")).split())[:1500] if isinstance(answer, Mapping) else ""
            if not script.sentences(text):
                raise ValueError("empty narration")
            check = [" ".join(str(v).split())[:200] for v in (answer.get("needs_check") or [])[:6]]
        except Exception as error:  # noqa: BLE001
            raise CompilationError(f"Không viết được lời bình cho “{item.title}” ({getattr(error, 'message', type(error).__name__)}); "
                                   "hãy nhập lời bình") from error
        record["drafted"][item.item_id] = {"narration": text, "needs_check": check}
        warnings.append(f"Lời bình mục “{item.title}” do AI viết nháp: hãy đọc lại trước khi đăng.")
        drafted.append(replace(item, narration=text, verify=item.verify + tuple(check)))
    return replace(spec, items=tuple(drafted)), record


TARGETED_PER_PART = 8  # targeted looks one part may take from what the broad look left


def run_program(spec: Program, output_dir: Path, *, speaker: Callable[[Status], Speaker], probe: Callable[[Path], int],
                inspect: Callable[[Path], scene.StreamInfo], ffmpeg: Path,
                ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
                encoder: str = render.ENCODER, vision: Callable[..., Any] | None = None,
                ask_text: AskText | None = None, producer_key: str = "") -> dict[str, Any]:
    work = output_dir / ".dubflow-work"
    work.mkdir(parents=True, exist_ok=True)
    folder = work / "mastering"
    parts_total = len(spec.items) + bool(spec.hook) + bool(spec.intro) + bool(spec.outro)
    status = Status(work / "status.json", parts_total)
    status.write(path=None, message="Đang chuẩn bị chương trình…")
    partial = work / "compilation.partial.mp4"
    voice = None
    warnings: list[str] = []
    context = ""

    def cancelled() -> bool:
        return (work / "cancel").exists()

    def check() -> None:
        if cancelled():
            raise Cancelled()

    def execute(argv: list[str]) -> None:
        check()
        ffmpeg_runner(argv, work, cancelled)
        check()

    try:
        resolved, record = resolve(spec, ask_text, warnings, work / "ai-answers.json")
        check()
        # 1. Every source once.
        footage: dict[str, planner.Footage] = {}
        render_path: dict[str, Path] = {}  # a source the renderer reads from another file (same streams)
        candidates: dict[str, list[shots.Candidate]] = {}
        item_notes: dict[str, str] = {}
        sources_of: dict[str, list[str]] = {}
        credits: list[str] = []
        for number, item in enumerate(resolved.items, 1):
            sources_of[item.item_id] = []
            for k, source in enumerate(item.sources, 1):
                check()
                context = f"Mục {number}: {item.title}: "
                status.write(message=f"{context}Đang phân tích nguồn {k}/{len(item.sources)}…")
                source_id = f"{item.item_id}-s{k}"
                info = inspect(source.video)
                duration = info.duration_ms
                trim = (source.start_ms or 0, source.end_ms if source.end_ms is not None else duration)
                if trim[0] >= duration or trim[1] > duration or trim[1] - trim[0] < 1000:
                    raise CompilationError("Mốc cắt nằm ngoài thời lượng video nguồn")
                found = scene.analyse(source.video, info, scene.cache_path(folder, source.video), ffmpeg=ffmpeg,
                                      run=ffmpeg_runner, cancelled=cancelled)
                cues = ()
                if source.transcript is not None:
                    try:
                        cues = tuple(ev.parse_srt(source.transcript.read_text(encoding="utf-8", errors="replace")))
                    except OSError:
                        warnings.append(f"{context}không đọc được phụ đề nguồn {source.transcript.name}")
                fps = info.fps if info.fps else 30
                footage[source_id] = planner.Footage(source_id, source.video, found, info.width, info.height,
                                                     (fps.numerator, fps.denominator) if hasattr(fps, "numerator") else (int(fps), 1),
                                                     cues, source.notes, None if trim == (0, duration) else trim, source.rights,
                                                     source.credit.line())
                candidates[source_id] = shots.extract(source.video, source_id, found, folder, ffmpeg=ffmpeg, run=ffmpeg_runner,
                                                      cancelled=cancelled)
                item_notes[source_id] = item.notes
                if source.video.suffix.casefold() == ".webm":
                    # FFmpeg fails ("Invalid data found") when one WebM (AV1/Opus) is opened twice at different
                    # seeks and the parts are joined; the same streams in Matroska play fine. Copy, no re-encode.
                    playable = folder / f"source-{m.file_identity(source.video, 'remux-mkv-v1')[:24]}.mkv"
                    if not playable.is_file():
                        status.write(message=f"{context}Đang chuẩn bị nguồn WebM {k}/{len(item.sources)}…")
                        unfinished_copy = playable.with_suffix(".partial.mkv")
                        execute([str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source.video),
                                 "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(unfinished_copy)])
                        os.replace(unfinished_copy, playable)
                    render_path[source_id] = playable
                sources_of[item.item_id].append(source_id)
                line = source.credit.line()
                rights = {"owned": "của bạn", "licensed": "có giấy phép", "permitted": "được cho phép", "unknown": "chưa xác nhận"}[source.rights]
                credits.append(f"{item.title}: {line or source.video.name} (quyền sử dụng: {rights})")
        context = ""
        evidence = planner.evidence_for(list(footage.values()), candidates, item_notes)
        pool = [c for mine in candidates.values() for c in mine]
        groups = shots.duplicate_groups(pool)
        vision_report = None
        if vision is not None:
            status.write(message="AI đang xem các cảnh…")
            describer = vision(folder / "vision", context=f"Video tổng hợp: {resolved.title}", labels=ev.MOMENT_LABELS)
            everything_seen = seeing.frames_for(pool, folder)
            # Adaptive sampling: a broad look spread over every source, keeping a third of the budget for the
            # sentences the first plan cannot cover (targeted looks below), when the budget does not reach every shot.
            room = describer.budget - describer.budget // 3 if len(everything_seen) > describer.budget else describer.budget
            first = seeing.broad(pool, folder, room)
            looked = {candidate_id for candidate_id, _ in first}
            seen = describer.describe(first, cancelled)
            check()
            seeing.apply(seen, evidence, f"AI {describer.provider}/{describer.used_model}")
            vision_report = {"provider": describer.provider, "model": describer.used_model, "frames": len(seen), "asked": describer.asked,
                             "broad": len(first), "targeted": 0}
            warnings += describer.warnings
            warned = len(describer.warnings)
            if len(everything_seen) > describer.budget:
                warnings.append(f"Giới hạn {describer.budget} khung hình cho AI nhìn: {len(first)} cảnh trải đều các nguồn, phần còn lại "
                                f"dành cho câu thiếu hình; {len(everything_seen) - describer.budget} cảnh trở lên chọn theo thông tin khác.")
        # 2. Parts.
        parts: list[tuple[str, str, str, str, int | None, list[str], str]] = []  # id, kind, text, title, rank, sources, keep
        everything = list(footage)
        if resolved.hook:
            parts.append(("hook", "hook", resolved.hook, resolved.title, None, everything, "off"))
        if resolved.intro:
            parts.append(("intro", "intro", resolved.intro, resolved.title, None, everything, "off"))
        for number, item in enumerate(resolved.items, 1):
            rank = item.rank if item.rank is not None else (len(resolved.items) - number + 1 if resolved.kind == "top_n" else number)
            parts.append((item.item_id, "item", item.narration, item.title, rank, sources_of[item.item_id], item.keep_audio))
        if resolved.outro:
            parts.append(("outro", "outro", resolved.outro, resolved.title, None, everything, "off"))
        code = hashlib.sha256()
        for module in (render, layout, planner, align, more, shots, ev, scene):
            code.update(Path(module.__file__).read_bytes())
        blur = render.available_blur(ffmpeg) if ffmpeg.is_file() else "boxblur=20:2"
        music_hash = _digest_file(resolved.music, cancelled) if resolved.music else None
        names: list[str] = []
        sampled: set[str] = set()  # shots whose extra frames were described (a shot is looked at again only once)
        shown: set[str] = set()  # candidates the parts so far showed
        timeline: list[tuple[str, int]] = []
        all_captions: list[script.Caption] = []
        review = 0
        modes = {}
        cursor = 0
        for index, (part_id, kind, text, title, rank, source_ids, keep) in enumerate(parts, 1):
            check()
            context = f"{title}: " if kind == "item" else ""
            status.write(message=f"{context}Đang đọc lời bình ({index}/{len(parts)})…")
            if voice is None:
                voice = speaker(status)
            speech = []
            for sentence in script.sentences(text):
                check()
                try:
                    speech.append((sentence, voice.speak(sentence)))
                except Exception as error:
                    raise CompilationError(f"Không đọc được câu “{sentence[:80]}”. Hãy sửa câu này rồi thử lại.") from error
            spoken = [(s, len(samples) * 1000 // voice.sample_rate) for s, samples in speech]
            captions, narration_ms = script.captions(spoken)
            wav = work / f"{part_id}.wav"
            narration_render.write_narration(wav, [samples for _, samples in speech], voice.sample_rate,
                                             lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, tail_ms=script.TAIL_MS)
            status.write(message=f"{context}Đang chọn cảnh ({index}/{len(parts)})…")
            part_footage = [footage[s] for s in source_ids]
            avoid = sorted(shown) if kind in ("intro", "outro") else []  # the opening and closing show something new
            inputs = {s: m.file_identity(footage[s].path, scene.RECIPE) for s in source_ids}
            inputs["part"] = m.digest_json({"spoken": spoken, "kind": kind, "title": title, "keep": keep,
                                            "editorial": resolved.editorial.digest_input(part_id),
                                            # what the AI saw, not how many frames it was asked this run (cached answers)
                                            "vision": None if vision_report is None else
                                            {k: vision_report[k] for k in ("provider", "model")},
                                            "shown": avoid, "trim": [footage[s].trim for s in source_ids]})
            made = planner.plan_part(part_id, kind, spoken, narration_ms, resolved.mastering, lead_ms=script.LEAD_MS,
                                     gap_ms=script.GAP_MS, footage=part_footage, candidates=candidates, evidence=evidence,
                                     groups=groups, edits=resolved.editorial.edits.get(part_id), keep_audio=keep, title=title,
                                     inputs=inputs, shown=avoid)
            room = describer.budget - describer.taken if vision_report is not None else 0
            if room > 0:
                # Targeted look: shots not yet seen that may show what an uncovered sentence says; plan again with them.
                ids = seeing.targets(made.log["units"], [c for s in source_ids for c in candidates[s]], evidence, looked,
                                     min(room, TARGETED_PER_PART), forward_only=True)
                if ids:
                    status.write(message=f"{context}AI xem thêm cảnh cho câu chưa đủ hình ({index}/{len(parts)})…")
                    by_id = {c.candidate_id: c for c in pool}
                    extra = describer.describe([(i, folder / by_id[i].keyframe) for i in ids], cancelled)
                    check()
                    looked.update(ids)
                    seeing.apply(extra, evidence, f"AI {describer.provider}/{describer.used_model}")
                    vision_report["targeted"] += len(extra)
                    vision_report["frames"] += len(extra)
                    vision_report["asked"] = describer.asked
                    warnings += describer.warnings[warned:]
                    warned = len(describer.warnings)
                    if extra:
                        made = planner.plan_part(part_id, kind, spoken, narration_ms, resolved.mastering, lead_ms=script.LEAD_MS,
                                                 gap_ms=script.GAP_MS, footage=part_footage, candidates=candidates, evidence=evidence,
                                                 groups=groups, edits=resolved.editorial.edits.get(part_id), keep_audio=keep, title=title,
                                                 inputs=inputs, shown=avoid)
            room = describer.budget - describer.taken if vision_report is not None else 0
            if room >= 2:
                # More frames of the shots one frame does not represent (long takes, unsure answers), then plan again.
                edits = resolved.editorial.edits.get(part_id)
                locked = ({i for ids in edits.pin.values() for i in ids} | set(edits.exclude)) if edits is not None else set()
                part_pool = [c for s in source_ids for c in candidates[s]]
                ids = more.wanted(made.log["units"], part_pool, evidence, sampled, limit=min(more.EXTRA_SHOTS, room // 2),
                                  forward_only=True, locked={i for i in locked if more.parent(i) == i},
                                  again={more.parent(i) for i in locked if more.parent(i) != i})
                if ids:
                    status.write(message=f"{context}AI xem thêm khung hình của cảnh dài ({index}/{len(parts)})…")
                    mine = {s: candidates[s] for s in source_ids}
                    mine, described = more.look_again(
                        ids, mine, evidence, describer, folder, {s: footage[s].path for s in source_ids},
                        grab=lambda video, at, jpeg, grey: ffmpeg_runner(shots.grab_argv(ffmpeg, video, at, jpeg, grey), folder, cancelled),
                        measure=shots.measure, detail=f"AI {describer.provider}/{describer.used_model}", cancelled=cancelled)
                    check()
                    sampled.update(ids)
                    candidates.update(mine)
                    pool = [c for every in candidates.values() for c in every]
                    groups = shots.duplicate_groups(pool)
                    vision_report["multi_frame"] = vision_report.get("multi_frame", 0) + described
                    vision_report["frames"] += described
                    vision_report["asked"] = describer.asked
                    warnings += describer.warnings[warned:]
                    warned = len(describer.warnings)
                    if described:
                        made = planner.plan_part(part_id, kind, spoken, narration_ms, resolved.mastering, lead_ms=script.LEAD_MS,
                                                 gap_ms=script.GAP_MS, footage=part_footage, candidates=candidates, evidence=evidence,
                                                 groups=groups, edits=resolved.editorial.edits.get(part_id), keep_audio=keep, title=title,
                                                 inputs=inputs, shown=avoid)
            m.save(folder / f"plan-{part_id}.json", made.plan)
            shown.update(choice for unit in made.log["units"] for choice in unit["chosen"])
            made.log["vision"] = vision_report
            _atomic_text(folder / f"editorial-{part_id}.json", json.dumps(made.log, ensure_ascii=False, indent=1))
            review += sum(s.needs_review for s in made.plan.segments)
            modes[part_id] = made.log["mode"]
            duration_ms = made.plan.duration
            ass_name = f"{part_id}.ass"
            ass = layout.program_ass(duration_ms, programme=resolved.title, title="" if kind in ("hook",) else title,
                                     rank=rank if kind == "item" and resolved.kind == "top_n" else None, captions=captions)
            _atomic_text(work / ass_name, ass)
            pieces = []
            for segment in made.plan.segments:
                f = footage[segment.source_id]
                start = m.to_milliseconds(segment.source.start, made.plan.time_base, "floor")
                length = m.to_milliseconds(segment.timeline.end, made.plan.time_base) - m.to_milliseconds(segment.timeline.start, made.plan.time_base)
                pieces.append((render_path.get(segment.source_id, f.path), scene.StreamInfo(f.analysis.duration_ms, f.width, f.height, 30, f.analysis.has_audio),
                               start, length, segment.audio_mode, f.analysis.has_audio, segment.role == "still"))
            # what is drawn, not how the plan came about: a plan made again from more evidence that picks the same
            # footage does not render again
            drawn = {k: v for k, v in made.plan.to_dict().items() if k not in ("provenance", "warnings")}
            drawn["segments"] = [{k: v for k, v in segment.items() if k not in ("reasons", "confidence_milli", "needs_review")}
                                 for segment in drawn["segments"]]
            key = hashlib.sha256(json.dumps({"plan": drawn, "wav": _digest_file(wav, cancelled), "ass": ass,
                                             "layout": resolved.layout, "encoder": encoder, "blur": blur, "music": music_hash,
                                             "volume": resolved.music_volume, "code": code.hexdigest(), "producer": producer_key},
                                            sort_keys=True, default=str).encode()).hexdigest()
            name = f"part-{part_id}"
            part, unfinished, receipt_path = work / f"{name}.mp4", work / f"{name}.partial.mp4", work / f"{name}.receipt.json"
            part_ms = None
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt["key"] == key and _digest_file(part, cancelled) == receipt["sha256"]:
                    part_ms = int(receipt["duration_ms"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if part_ms is None:
                status.write(message=f"{context}Đang dựng đoạn ({index}/{len(parts)})…")
                execute(render.montage_argv(ffmpeg, pieces, wav, resolved.music, resolved.music_volume, duration_ms, ass_name,
                                            unfinished, layout=resolved.layout, encoder=encoder, blur=blur,
                                            fade=resolved.mastering.level != "light"))
                part_ms = probe(unfinished)
                if abs(part_ms - duration_ms) > 150:
                    raise CompilationError("Thời lượng đoạn video không khớp")
                os.replace(unfinished, part)
                _atomic_text(receipt_path, json.dumps({"key": key, "sha256": _digest_file(part, cancelled), "duration_ms": part_ms}))
            names.append(name)
            chapter = {"hook": "", "intro": "Mở đầu", "outro": "Kết thúc"}.get(kind, f"#{rank} {title}" if resolved.kind == "top_n" else title)
            timeline.append((chapter, part_ms))
            all_captions += [script.Caption(c.start_ms + cursor, c.end_ms + cursor, c.text) for c in captions]
            cursor += part_ms
            status.write(done=index, message=f"Đã dựng {index}/{len(parts)} đoạn…")
        context = ""
        from .job import chapters
        chapter_text = chapters(timeline)
        description_text = chapter_text.rstrip() + "\n\nNguồn:\n" + "\n".join(f"- {line}" for line in credits) + "\n"
        status.write(message="Đang ghép chương trình…")
        expected = sum(ms for _, ms in timeline)
        tolerance = max(300, len(names) * 34)
        # Every part has the same encoding: join them as they are; encode again only if the copy fails or drifts.
        joined = "copy"
        _atomic_text(work / "parts.ffconcat", render.concat_list(names))
        try:
            execute(render.concat_copy_argv(ffmpeg, "parts.ffconcat", partial))
            if abs(probe(partial) - expected) > tolerance:
                joined = "encode"
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 - the encoded join below is the fallback
            joined = "encode"
        if joined == "encode":
            partial.unlink(missing_ok=True)
            execute(render.concat_argv(ffmpeg, names, partial, encoder=encoder))
            if abs(probe(partial) - expected) > tolerance:
                raise CompilationError("Thời lượng video tổng hợp không khớp")
        _atomic_text(output_dir / "chapters.txt", chapter_text)
        _atomic_text(output_dir / "description.txt", description_text)
        _atomic_text(output_dir / "captions.srt", srt_document(all_captions))
        _atomic_text(work / "program.json", json.dumps({
            "schema_version": 1, "kind": "compilation_program", "title": resolved.title, "program": resolved.kind,
            "ranking": record, "parts": [{"id": p[0], "kind": p[1], "title": p[3], "rank": p[4], "sources": p[5],
                                          "narration": p[2], "mode": modes.get(p[0])} for p in parts],
            "verify": {i.item_id: list(i.verify) for i in resolved.items if i.verify}, "credits": credits,
            "warnings": warnings, "vision": vision_report, "joined": joined}, ensure_ascii=False, indent=1))
        final = output_dir / "compilation.mp4"
        os.replace(partial, final)
        status.write(state="COMPLETED", message="Đã tạo video tổng hợp.", path=str(final), duration_ms=expected,
                     review=review, warnings=warnings[:20], modes=modes)
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong.")
    except (CompilationError, ValueError, scene.AnalysisError, m.PlanError) as error:
        status.write(state="FAILED", message=f"{context}{getattr(error, 'message', None) or error}")
    except Exception as error:  # noqa: BLE001 - the host watcher also handles abrupt exits
        status.write(state="FAILED", message=f"{context}Không tạo được video ({type(error).__name__}: {str(error)[:160]}).")
    finally:
        partial.unlink(missing_ok=True)
        for unfinished in work.glob("*.partial.mp4"):
            unfinished.unlink(missing_ok=True)
        if voice is not None:
            voice.close()
    return status.value


__all__ = ["resolve", "run_program"]
