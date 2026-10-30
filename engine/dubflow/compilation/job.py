"""Versioned compilation specs, segment checkpoints and atomic publication."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Sequence
import unicodedata

from engine.dubflow.package.job import probe_video  # shared ffprobe adapter
from engine.dubflow.package.render import Video, seconds, start
from engine.dubflow.product import render as narration_render, script
from engine.dubflow.product.job import Cancelled, ProductError, Speaker, Status, _atomic_text, _file, run_ffmpeg
from . import layout, render

CompilationError = ProductError


@dataclass(frozen=True)
class Item:
    video: Path
    title: str
    lead: str
    score: int | None
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class Spec:
    title: str
    layout: str
    voice_id: str
    speed: float
    music: Path | None
    music_volume: float
    intro: str
    outro: str
    items: tuple[Item, ...]


def text(value: Any, label: str, limit: int, *, required: bool = False, spoken: bool = False) -> str:
    if (type(value) is not str or len(value) > limit or (required and not value.strip())
            or any(unicodedata.category(c) == "Cc" and c not in "\r\n\t" for c in value)):
        raise CompilationError(f"{label} không hợp lệ (tối đa {limit} ký tự)")
    value = value.strip()
    if spoken and value and not script.sentences(value):
        raise CompilationError(f"{label} không có câu nào để đọc")
    return value


def load_spec(path: Path) -> Spec:
    try:
        if path.stat().st_size > 256 * 1024:
            raise ValueError("too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CompilationError("Không đọc được yêu cầu tổng hợp") from error
    fields = {"schema_version", "title", "layout", "voice_id", "speed", "music", "music_volume", "intro", "outro", "items"}
    if (type(value) is not dict or set(value) != fields or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1):
        raise CompilationError("Yêu cầu tổng hợp không đúng phiên bản hoặc trường dữ liệu")
    if value["layout"] not in layout.LAYOUTS:
        raise CompilationError("Bố cục không hợp lệ")
    for field, low, high in (("speed", 1, 1.3), ("music_volume", 0, 1)):
        number = value[field]
        if type(number) not in (int, float) or not low <= number <= high:
            raise CompilationError("Tốc độ đọc hoặc âm lượng không hợp lệ")
    if type(value["items"]) is not list or not 2 <= len(value["items"]) <= 30:
        raise CompilationError("Cần từ 2 đến 30 video đã hoàn tất")
    items = []
    for number, item in enumerate(value["items"], 1):
        label = f"Phần {number}"
        required = {"video", "title", "lead", "score", "pros", "cons"}
        if type(item) is not dict or not required <= set(item) or set(item) - required - {"start_ms", "end_ms"}:
            raise CompilationError(f"{label}: thông tin không hợp lệ")
        try:
            video = _file(item["video"], frozenset({".mp4"}), "video đã hoàn tất").resolve()
            if video.name != "final_vi.mp4" or not any(p.name == "DubFlow Output" for p in video.parents):
                raise CompilationError("Cần final_vi.mp4 bên trong DubFlow Output")
            score = item["score"]
            if score is not None and (type(score) is not int or not 0 <= score <= 10):
                raise CompilationError("Điểm phải là số nguyên từ 0 đến 10 hoặc để trống")
            lists = []
            for field in ("pros", "cons"):
                values = item[field]
                if type(values) is not list or len(values) > 3:
                    raise CompilationError("Mỗi mục ưu/nhược điểm tối đa 3 dòng")
                lists.append(tuple(text(v, "Ưu/nhược điểm", 60, required=True) for v in values))
            trim_start, trim_end = item.get("start_ms"), item.get("end_ms")
            for trim in (trim_start, trim_end):
                if trim is not None and (type(trim) is not int or not 0 <= trim <= 2**53 - 1):
                    raise CompilationError("Mốc cắt phải là số mili giây nguyên không âm")
            if trim_end is not None and trim_end <= (trim_start or 0):
                raise CompilationError("Mốc kết thúc phải sau mốc bắt đầu")
            items.append(Item(video, text(item["title"], "Tiêu đề phần", 80, required=True),
                              text(item["lead"], "Lời dẫn", 400, spoken=True), score, *lists, trim_start, trim_end))
        except CompilationError as error:
            raise CompilationError(f"{label}: {error.message}") from error
    return Spec(text(value["title"], "Tiêu đề", 120, required=True), value["layout"],
                text(value["voice_id"], "Giọng đọc", 96, required=True), float(value["speed"]),
                None if value["music"] is None else _file(value["music"], narration_render.MUSIC_SUFFIXES, "nhạc nền"),
                float(value["music_volume"]), text(value["intro"], "Mở đầu", 800, spoken=True),
                text(value["outro"], "Kết thúc", 800, spoken=True), tuple(items))


def chapter_time(ms: int) -> str:
    hours, remainder = divmod(max(0, ms) // 1000, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def chapters(segments: Sequence[tuple[str, int]]) -> str:
    cursor, lines = 0, []
    for label, duration in segments:
        if label:
            lines.append(f"{chapter_time(cursor)} {' '.join(label.split())}")
        cursor += duration
    return "\n".join(lines) + "\n"


def description(chapter_text: str, items: Sequence[Item]) -> str:
    credits = []
    for number, item in enumerate(items, 1):
        path = item.video.parent / "source_credit.json"
        if not path.exists():
            continue
        try:
            if path.stat().st_size > 16 * 1024:
                raise ValueError("too large")
            value = json.loads(path.read_text(encoding="utf-8"))
            if type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
                raise ValueError("invalid credit")
            creator, platform, url = (text(value.get(k), "Nguồn", n) for k, n in (("creator", 120), ("platform", 40), ("url", 500)))
            author = f"{creator} ({platform})" if creator and platform else creator or platform
            line = " – ".join(part for part in (author, url) if part)
            if line:
                credits.append(f"Nguồn: {line}")
        except (OSError, ValueError, CompilationError) as error:
            raise CompilationError(f"Phần {number}: {item.title}: thông tin nguồn không hợp lệ") from error
    return chapter_text.rstrip() + ("\n\n" + "\n".join(credits) if credits else "") + "\n"


def run(spec: Spec, output_dir: Path, *, speaker: Callable[[Status], Speaker], probe: Callable[[Path], int],
        measure: Callable[[Path], Video], ffmpeg: Path,
        ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER, producer_key: str = "") -> dict[str, Any]:
    work = output_dir / ".dubflow-work"
    work.mkdir(parents=True, exist_ok=True)
    total = len(spec.items) + sum(bool(i.lead) for i in spec.items) + bool(spec.intro) + bool(spec.outro)
    status = Status(work / "status.json", total)
    status.write(path=None, message="Đang chuẩn bị tổng hợp…")
    partial = work / "compilation.partial.mp4"
    voice = None
    context = ""
    parts: list[str] = []
    timeline: list[tuple[str, int]] = []

    def cancelled() -> bool:
        return (work / "cancel").exists()

    def check() -> None:
        if cancelled():
            raise Cancelled()

    def execute(argv: list[str]) -> None:
        check()
        ffmpeg_runner(argv, work, cancelled)
        check()

    def digest(path: Path) -> str:
        sha = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                check()
                sha.update(chunk)
        return sha.hexdigest()

    try:
        check()
        code = hashlib.sha256()
        for folder in (Path(__file__).parent, Path(script.__file__).parent, Path(probe_video.__code__.co_filename).parent):
            for source in sorted(folder.glob("*.py")):
                code.update(source.read_bytes())
        common = {"code": code.hexdigest(), "encoder": encoder, "producer": producer_key,
                  "ffmpeg": digest(ffmpeg) if ffmpeg.is_file() else str(ffmpeg)}
        blur = render.available_blur(ffmpeg) if ffmpeg.is_file() else "boxblur=20:2"
        common["blur"] = blur
        music_hash = digest(spec.music) if spec.music else None
        for index, item in enumerate(spec.items, 1):
            context = f"Phần {index}: {item.title}: "
            check()
            duration, video = probe(item.video), measure(item.video)
            offset, end = item.start_ms or 0, item.end_ms if item.end_ms is not None else duration
            if duration <= 0 or offset >= duration or end > duration or end <= offset:
                raise CompilationError("Mốc cắt nằm ngoài thời lượng video")
            source_hash = digest(item.video)
            segments = []
            if index == 1 and spec.intro:
                segments.append(("intro", spec.intro, "Mở đầu"))
            if item.lead:
                segments.append((f"lead-{index}", item.lead, f"Phần {index}: {item.title}"))
            segments.append((f"clip-{index}", "", "" if item.lead else f"Phần {index}: {item.title}"))
            if index == len(spec.items) and spec.outro:
                segments.append(("outro", spec.outro, "Kết thúc"))
            for name, narration, chapter in segments:
                check()
                card = bool(narration)
                recipe = {**common, "item": asdict(item), "source": source_hash, "video": asdict(video),
                          "duration": duration, "layout": spec.layout, "name": name, "title": spec.title,
                          "text": narration, "voice": spec.voice_id if card else None, "speed": spec.speed if card else None,
                          "music": music_hash if card else None, "volume": spec.music_volume if card else None}
                key = hashlib.sha256(json.dumps(recipe, default=str, sort_keys=True).encode()).hexdigest()
                part, unfinished = work / f"{name}.mp4", work / f"{name}.partial.mp4"
                receipt_path = work / f"{name}.receipt.json"
                part_ms = None
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if receipt["key"] == key and digest(part) == receipt["sha256"] and type(receipt["duration_ms"]) is int and receipt["duration_ms"] > 0:
                        part_ms = receipt["duration_ms"]
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                if part_ms is None:
                    status.write(message=f"{context}Đang {'đọc lời dẫn' if card else 'tạo đoạn video'}…")
                    captions = []
                    if card:
                        if voice is None:
                            voice = speaker(status)
                        speech = []
                        for sentence in script.sentences(narration):
                            check()
                            speech.append((sentence, voice.speak(sentence)))
                        check()
                        captions, planned_ms = script.captions([(s, len(samples) * 1000 // voice.sample_rate) for s, samples in speech])
                        wav = work / f"{name}.wav"
                        narration_render.write_narration(wav, [samples for _, samples in speech], voice.sample_rate,
                                                         lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, tail_ms=script.TAIL_MS)
                    else:
                        planned_ms = end - offset
                    g = layout.geometry(video.width, video.height, spec.layout)
                    editorial = name.startswith(("lead-", "clip-"))
                    # Leads always introduce the part over an information panel.
                    panel_width = (g.panel_width or 1000) if name.startswith("lead-") else g.panel_width if editorial else 0
                    _atomic_text(work / f"{name}.ass", layout.ass_document(
                        item.title if editorial else spec.title, planned_ms, number=index if editorial else 0,
                        panel_width=panel_width, score=item.score, pros=item.pros, cons=item.cons,
                        captions=captions, card=card, show_score=spec.layout == "bang-thong-tin"))
                    if card:
                        image = work / f"{name}.png"
                        execute(start(ffmpeg) + ["-ss", seconds(offset), "-i", str(item.video), "-frames:v", "1", str(image)])
                        execute(render.card_argv(ffmpeg, image, wav, spec.music, spec.music_volume, planned_ms,
                                                 f"{name}.ass", unfinished, encoder=encoder, blur=blur))
                    else:
                        execute(render.clip_argv(ffmpeg, item.video, video, spec.layout, offset, planned_ms,
                                                 f"{name}.ass", unfinished, encoder=encoder, blur=blur))
                    part_ms = probe(unfinished)
                    if abs(part_ms - planned_ms) > 150:
                        raise CompilationError("Thời lượng đoạn video không khớp")
                    os.replace(unfinished, part)
                    _atomic_text(receipt_path, json.dumps({"key": key, "sha256": digest(part), "duration_ms": part_ms}))
                parts.append(name)
                timeline.append((chapter, part_ms))
                status.write(done=len(parts), message=f"Đã tạo {len(parts)}/{total} đoạn…")
        context = ""
        chapter_text = chapters(timeline)
        description_text = description(chapter_text, spec.items)
        status.write(message="Đang ghép video tổng hợp…")
        execute(render.concat_argv(ffmpeg, parts, partial, encoder=encoder))
        expected = sum(ms for _, ms in timeline)
        if abs(probe(partial) - expected) > max(300, len(parts) * 34):
            raise CompilationError("Thời lượng video tổng hợp không khớp")
        result = measure(partial)
        if (result.width, result.height) != (layout.WIDTH, layout.HEIGHT) or Fraction(result.fps) != layout.FPS:
            raise CompilationError("Kích thước hoặc tốc độ khung hình tổng hợp không khớp")
        check()
        _atomic_text(output_dir / "chapters.txt", chapter_text)
        _atomic_text(output_dir / "description.txt", description_text)
        final = output_dir / "compilation.mp4"
        os.replace(partial, final)
        status.write(state="COMPLETED", message="Đã tạo video tổng hợp.", path=str(final), duration_ms=expected)
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong.")
    except (CompilationError, ValueError) as error:
        status.write(state="FAILED", message=f"{context}{error}")
    except Exception as error:  # The host watcher also handles abrupt process exit.
        status.write(state="FAILED", message=f"{context}Không tạo được video ({type(error).__name__}). Hãy kiểm tra file và thử lại.")
    finally:
        partial.unlink(missing_ok=True)
        for unfinished in work.glob("*.partial.mp4"):
            unfinished.unlink(missing_ok=True)
        if voice is not None:
            voice.close()
    return status.value
