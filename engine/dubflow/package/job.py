"""Package one finished export, with atomic publication and cooperative cancel."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable
import unicodedata

from engine.dubflow.product import render as narration_render, script
from engine.dubflow.product.job import Cancelled, ProductError, Speaker, _atomic_text, _file, _string, run_ffmpeg
from . import render

PackageError = ProductError
CANCEL_FILE = "cancel"
MAX_TEXT_CHARS = 600


@dataclass(frozen=True)
class Spec:
    video: Path
    intro: str
    outro: str
    voice_id: str
    speed: float
    music: Path | None
    music_volume: float
    background: str


def load_spec(path: Path) -> Spec:
    try:
        if path.stat().st_size > 32 * 1024:
            raise ValueError("too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageError("Không đọc được yêu cầu đóng gói") from error
    if type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise PackageError("Yêu cầu đóng gói không đúng phiên bản")
    video = _file(value.get("video"), frozenset({".mp4"}), "video đã lồng tiếng")
    if video.name != "final_vi.mp4":
        raise PackageError("Hãy chọn video final_vi.mp4 đã hoàn tất")
    intro = _string(value.get("intro"), "Mở đầu", MAX_TEXT_CHARS)
    outro = _string(value.get("outro"), "Kết thúc", MAX_TEXT_CHARS)
    if any(unicodedata.category(c) == "Cc" and c not in "\r\n\t"
           for text in (value.get("intro") or "", value.get("outro") or "") for c in text):
        raise PackageError("Mở đầu và kết thúc không được chứa ký tự điều khiển")
    if any(text and not script.sentences(text) for text in (intro, outro)):
        raise PackageError("Mở đầu hoặc kết thúc không có câu nào để đọc")
    voice = _string(value.get("voice_id"), "Giọng đọc", 96, required=True)
    speed, volume = value.get("speed", 1.0), value.get("music_volume", 0.15)
    for number, low, high, label in ((speed, 1.0, 1.3, "Tốc độ đọc"), (volume, 0, 1, "Âm lượng nhạc")):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high:
            raise PackageError(f"{label} không hợp lệ")
    music = None if value.get("music") is None else _file(value["music"], narration_render.MUSIC_SUFFIXES, "nhạc nền")
    if not intro and not outro and music is None:
        raise PackageError("Hãy nhập mở đầu, kết thúc hoặc chọn nhạc")
    background = value.get("background", "keep")
    try:
        render.audio_plan(video, music, float(volume), background)
    except ValueError as error:
        raise PackageError(str(error)) from error
    return Spec(video, intro, outro, voice, float(speed), music, float(volume), background)


class Status:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.value: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "message": "Đang chuẩn bị đóng gói…"}
        self.write()

    def write(self, **changes: Any) -> None:
        self.value.update(changes)
        _atomic_text(self.path, json.dumps(self.value, ensure_ascii=False))


def probe_video(ffprobe: Path, path: Path) -> render.Video:
    result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height,avg_frame_rate,r_frame_rate", "-of", "json", str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stream = json.loads(result.stdout)["streams"][0]
        fps = stream.get("avg_frame_rate")
        if not fps or fps == "0/0":
            fps = stream["r_frame_rate"]
        video = render.Video(stream["width"], stream["height"], fps)
        if result.returncode:
            raise ValueError("probe failed")
        return video
    except (ValueError, KeyError, IndexError, ZeroDivisionError) as error:
        raise PackageError("Không đọc được kích thước và tốc độ khung hình video") from error


def run(spec: Spec, *, speaker: Callable[[Status], Speaker], probe: Callable[[Path], int],
        measure: Callable[[Path], render.Video], ffmpeg: Path,
        ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER, producer_key: str = "") -> dict[str, Any]:
    work = spec.video.parent / ".dubflow-work" / "package"
    work.mkdir(parents=True, exist_ok=True)
    status = Status(work / "status.json")
    final = spec.video.parent / "final_vi_packaged.mp4"
    partial = work / "final_vi_packaged.mp4.partial"
    voice = None

    def cancelled() -> bool:
        return (work / CANCEL_FILE).exists()

    def check() -> None:
        if cancelled():
            raise Cancelled()

    def execute(argv: list[str]) -> None:
        check()
        ffmpeg_runner(argv, work, cancelled)
        check()

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                check()
                value.update(chunk)
        return value.hexdigest()

    def cached(name: str, key: str) -> int | None:
        try:
            receipt = json.loads((work / f"{name}.receipt.json").read_text(encoding="utf-8"))
            if receipt["key"] == key and digest(work / f"{name}.mp4") == receipt["sha256"]:
                return int(receipt["duration_ms"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    try:
        check()
        duration = probe(spec.video)
        video = measure(spec.video)
        if duration <= 0:
            raise PackageError("Video không có thời lượng")
        audio = render.audio_plan(spec.video, spec.music, spec.music_volume, spec.background)
        # Content hashes, recipe and voice-pack identity prevent stale checkpoint reuse.
        sources = {str(path): digest(path) for path in {spec.video, *(item.path for item in audio)}}
        code = hashlib.sha256()
        for folder in (Path(__file__).parent, Path(script.__file__).parent):
            for path in sorted(folder.glob("*.py")):
                code.update(path.read_bytes())
        recipe = json.dumps({"spec": vars(spec), "sources": sources, "encoder": encoder, "producer": producer_key,
                             "ffmpeg": digest(ffmpeg) if ffmpeg.is_file() else str(ffmpeg),
                             "code": code.hexdigest(), "video": vars(video), "duration": duration}, default=str, sort_keys=True)
        recipe_key = hashlib.sha256(recipe.encode()).hexdigest()
        parts, total = [], duration
        for name, text in (("intro", spec.intro), ("main", ""), ("outro", spec.outro)):
            check()
            if name != "main" and not text:
                continue
            part = work / f"{name}.mp4"
            part_partial = work / f"{name}.partial.mp4"
            reused_ms = cached(name, recipe_key)
            if reused_ms is not None:
                if name != "main":
                    total += reused_ms
                parts.append(name)
                continue
            if name == "main":
                status.write(message="Đang chuẩn bị video và âm thanh chính…")
                execute(render.main_argv(ffmpeg, spec.video, audio, video, duration, part_partial, encoder=encoder))
                part_ms = duration
            elif text:
                status.write(message="Đang đọc mở đầu…" if name == "intro" else "Đang đọc kết thúc…")
                if voice is None:
                    voice = speaker(status)
                speech = []
                for sentence in script.sentences(text):
                    check()
                    speech.append((sentence, voice.speak(sentence)))
                check()
                captions, card_ms = script.captions([(sentence, len(samples) * 1000 // voice.sample_rate) for sentence, samples in speech])
                wav = work / f"{name}.wav"
                narration_render.write_narration(wav, [samples for _, samples in speech], voice.sample_rate,
                                                 lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, tail_ms=script.TAIL_MS)
                _atomic_text(work / f"{name}.ass", render.ass_document(captions, video))
                # Very short clips may have no decoded frame at t=1 even when
                # their container lasts a few milliseconds longer than a second.
                seek = (1000 if duration > 1000 + 1000 / float(Fraction(video.fps)) else 0) if name == "intro" else max(0, duration - 1000)
                image = work / f"{name}.png"
                execute(render.start(ffmpeg) + ["-ss", render.seconds(seek), "-i", str(spec.video), "-frames:v", "1", str(image)])
                status.write(message="Đang tạo thẻ mở đầu…" if name == "intro" else "Đang tạo thẻ kết thúc…")
                execute(render.card_argv(ffmpeg, image, wav, f"{name}.ass", video, card_ms, part_partial, encoder=encoder))
                total += card_ms
                part_ms = card_ms
            os.replace(part_partial, part)
            _atomic_text(work / f"{name}.receipt.json", json.dumps({"key": recipe_key, "sha256": digest(part), "duration_ms": part_ms}))
            parts.append(name)
        status.write(message="Đang ghép video đóng gói…")
        execute(render.concat_argv(ffmpeg, parts, video, partial, encoder=encoder))
        # Validate before publication, keeping a previous packaged result on failure.
        if abs(probe(partial) - total) > max(300, round(3000 / float(Fraction(video.fps)))):
            raise PackageError("Thời lượng video đóng gói không khớp")
        result_video = measure(partial)
        if (result_video.width, result_video.height) != (video.width, video.height):
            raise PackageError("Kích thước hoặc tốc độ khung hình video đóng gói không khớp")
        if Fraction(result_video.fps) != Fraction(video.fps):
            raise PackageError("Tốc độ khung hình video đóng gói không khớp")
        check()
        os.replace(partial, final)
        status.write(state="COMPLETED", message="Đã tạo video đóng gói.", path=str(final), duration_ms=total)
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy đóng gói.")
    except (PackageError, ValueError) as error:
        status.write(state="FAILED", message=str(error))
    except Exception as error:  # noqa: BLE001 - the desktop shows the reason
        detail = f"{getattr(error, 'code', type(error).__name__)}: {str(error)[:200]}"
        status.write(state="FAILED", message=f"Không đóng gói được video ({detail}). Hãy kiểm tra file và thử lại.")
    finally:
        partial.unlink(missing_ok=True)
        for unfinished in work.glob("*.partial.mp4"):
            unfinished.unlink(missing_ok=True)
        if voice is not None:
            voice.close()
    return status.value
