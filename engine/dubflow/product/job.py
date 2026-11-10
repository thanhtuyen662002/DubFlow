"""Render every script variant of one product into its own narrated video."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
import wave
from typing import Any, Callable, Protocol, Sequence

from . import layout, render, reuse, script

MAX_VARIANTS = 5
MIN_SPEED, MAX_SPEED = 1.0, 1.3
MAX_SCRIPT_CHARS = 3000
MAX_VISUALS = 30
MAX_FEATURES = 8
MAX_FEATURE_CHARS = 200
SPEC_LIMIT = 256 * 1024


CANCEL_FILE = "cancel"


class Cancelled(Exception):
    """The desktop asked to stop (``.dubflow-work/cancel`` exists)."""


class ProductError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Spec:
    name: str
    price: str
    voice_id: str
    variants: tuple[str, ...]
    visuals: tuple[Path, ...]
    music: Path | None
    music_volume: float
    # The user's image behind the template (render.background decides frame or panel).
    background: Path | None = None
    template: str = "ban-tin"
    # VieNeu's tempo for the narration.
    speed: float = 1.0
    # The highlights "Chỉ chữ" shows one card at a time.
    features: tuple[str, ...] = ()
    # The part of the background kept when it is cropped to fit (0–1 across, 0–1 down).
    focus: tuple[float, float] = (0.5, 0.5)
    # An earlier run whose narration is reused for a script spoken the same way (Mẫu, #426).
    reuse_from: Path | None = None
    # Tối ưu hậu kỳ tự động (ADR-0052): None or level "off" renders exactly as before.
    mastering: Any = None


class Speaker(Protocol):
    sample_rate: int

    def speak(self, text: str) -> Sequence[float]: ...

    def close(self) -> None: ...


def _string(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if type(value) is not str or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise ProductError(f"{name} không hợp lệ")
    return value.strip()


def _file(value: Any, suffixes: frozenset[str], name: str) -> Path:
    if type(value) is not str or not value or len(value) > 1024:
        raise ProductError(f"{name} không hợp lệ")
    path = Path(value)
    if not path.is_absolute() or path.suffix.casefold() not in suffixes or not path.is_file():
        raise ProductError(f"Không mở được {name}: {path.name}")
    return path


def load_spec(path: Path) -> Spec:
    try:
        data = path.read_bytes()
        if len(data) > SPEC_LIMIT:
            raise ValueError("too large")
        value = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as error:
        raise ProductError("Không đọc được yêu cầu render") from error
    if type(value) is not dict or value.get("schema_version") != 1:
        raise ProductError("Yêu cầu render không đúng phiên bản")
    variants = value.get("variants")
    if type(variants) is not list or not 1 <= len(variants) <= MAX_VARIANTS:
        raise ProductError(f"Cần từ 1 đến {MAX_VARIANTS} kịch bản")
    texts = tuple(_string(item, "Kịch bản", MAX_SCRIPT_CHARS, required=True) for item in variants)
    if any(not script.sentences(text) for text in texts):
        raise ProductError("Có kịch bản không có câu nào để đọc")
    template = value.get("template", "ban-tin")
    if template not in layout.TEMPLATES:
        raise ProductError("Mẫu video không hợp lệ")
    visuals = value.get("visuals", [])
    # "Chỉ chữ" needs no photos or clips and ignores any it is given.
    fewest = 0 if template == layout.TEXT_ONLY else 1
    if type(visuals) is not list or not fewest <= len(visuals) <= MAX_VISUALS:
        raise ProductError(f"Cần từ 1 đến {MAX_VISUALS} ảnh hoặc clip")
    if template == layout.TEXT_ONLY:
        visuals = []
    features = value.get("features", [])
    if type(features) is not list or len(features) > MAX_FEATURES:
        raise ProductError("Điểm nổi bật không hợp lệ")
    focus = value.get("focus", {"x": 0.5, "y": 0.5})
    if type(focus) is not dict or any(isinstance(focus.get(axis), bool) or not isinstance(focus.get(axis), (int, float))
                                      or not 0 <= focus[axis] <= 1 for axis in ("x", "y")):
        raise ProductError("Vị trí ảnh nền không hợp lệ")
    music = value.get("music")
    volume = value.get("music_volume", 0.15)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 1:
        raise ProductError("Âm lượng nhạc nền không hợp lệ")
    voice = _string(value.get("voice_id"), "Giọng đọc", 96, required=True)
    speed = value.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ProductError("Tốc độ đọc không hợp lệ")
    reuse_from = value.get("reuse_audio_from")
    if reuse_from is not None and (type(reuse_from) is not str or len(reuse_from) > 1024 or "\x00" in reuse_from):
        raise ProductError("Bản cũ để dùng lại giọng đọc không hợp lệ")
    # Reusing is only a shortcut: a run that is gone is simply spoken again.
    reuse_dir = Path(reuse_from) if reuse_from and Path(reuse_from).is_absolute() and Path(reuse_from).is_dir() else None
    mastering = None
    if value.get("mastering") is not None:
        from engine.dubflow.mastering import plan as mastering_plan
        try:
            mastering = mastering_plan.Options.from_dict(value["mastering"])
        except mastering_plan.PlanError as error:
            raise ProductError("Tùy chọn tối ưu hậu kỳ không hợp lệ") from error
        if not mastering.enabled:
            mastering = None
    return Spec(
        name=_string(value.get("name"), "Tên sản phẩm", 120),
        price=_string(value.get("price"), "Giá", 60),
        voice_id=voice,
        variants=texts,
        visuals=tuple(_file(item, render.IMAGE_SUFFIXES | render.VIDEO_SUFFIXES, "ảnh hoặc clip") for item in visuals),
        music=None if music is None else _file(music, render.MUSIC_SUFFIXES, "nhạc nền"),
        music_volume=float(volume),
        background=None if value.get("background") is None else _file(value["background"], render.IMAGE_SUFFIXES, "ảnh nền"),
        template=template,
        speed=float(speed),
        features=tuple(text for text in (" ".join(_string(item, "Điểm nổi bật", MAX_FEATURE_CHARS).split()) for item in features) if text),
        focus=(float(focus["x"]), float(focus["y"])),
        reuse_from=reuse_dir,
        mastering=mastering,
    )


def _atomic_text(path: Path, text: str) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)


class Status:
    """``.dubflow-work/status.json``, which the desktop polls."""

    def __init__(self, path: Path, total: int) -> None:
        self.path = path
        self.value: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "done": 0, "total": total, "message": "Đang chuẩn bị giọng đọc (lần đầu cần tải mô hình giọng khoảng 520 MB)…", "videos": []}
        self.write()

    def write(self, **changes: Any) -> None:
        self.value.update(changes)
        _atomic_text(self.path, json.dumps(self.value, ensure_ascii=False))


def run_ffmpeg(argv: list[str], cwd: Path, cancelled: Callable[[], bool] = lambda: False, *, poll_seconds: float = 0.5) -> None:
    """Run FFmpeg, ending it early when ``cancelled()`` turns true."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # A file, not a pipe: nobody reads a pipe while this waits.
    log = cwd / "ffmpeg-error.log"
    with log.open("wb") as errors:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags)
        deadline = time.monotonic() + 3600
        while process.poll() is None:
            if cancelled() or time.monotonic() > deadline:
                process.kill()
                process.wait()
                if cancelled():
                    raise Cancelled()
                raise ProductError("Render video quá lâu nên đã dừng")
            try:
                process.wait(timeout=poll_seconds)
            except subprocess.TimeoutExpired:
                pass
    if process.returncode != 0:
        detail = " ".join(log.read_bytes().decode("utf-8", "replace").split())[-300:]
        raise ProductError(f"Render video thất bại: {detail or 'FFmpeg lỗi'}")


def probe_duration_ms(ffprobe: Path, path: Path) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, creationflags=flags, timeout=120)
    try:
        duration = round(float(result.stdout.strip()) * 1000)
    except ValueError:
        duration = 0
    if result.returncode != 0 or duration <= 0:
        raise ProductError(f"Không đọc được clip: {path.name}")
    return duration


def probe_size(ffprobe: Path, path: Path) -> tuple[int, int]:
    """An image's width and height in pixels."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                             "-of", "csv=p=0:s=x", str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, creationflags=flags, timeout=120)
    try:
        width, height = (int(value) for value in result.stdout.strip().split("x")[:2])
    except ValueError:
        width = height = 0
    if result.returncode != 0 or width <= 0 or height <= 0:
        raise ProductError(f"Không đọc được ảnh nền: {path.name}")
    return width, height


def _wav_rate(path: Path) -> int:
    with wave.open(str(path), "rb") as reader:
        return reader.getframerate()


def _mastered(spec: Spec, visuals: Sequence[render.Visual], total_ms: int, work: Path, number: int, *,
              inspect: Callable[[Path], Any] | None, measure: Callable[[Path], tuple[int, int]] | None, ffmpeg: Path,
              ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None],
              cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Shot-aware segments from the shared mastering planner (ADR-0052), or None segments and a warning.

    The plan is saved as ``mastering_plan-<n>.json``; made again from the same files, length and options, it is
    loaded instead of analysing the clips again. Anything that fails falls back to the usual segments.
    """
    from dataclasses import replace
    from engine.dubflow.mastering import analysis as scene, plan as mastering_plan
    from engine.dubflow.mastering.planners import product as planner

    options = spec.mastering
    name = f"mastering_plan-{number}.json"
    path = work / name
    report: dict[str, Any] = {"level": options.level, "style": options.style, "plan": name, "segments": None}
    try:
        wanted = planner.inputs([v.path for v in visuals], total_ms, options)
        saved = mastering_plan.load_matching(path, workflow=planner.WORKFLOW, options=options, planner_version=planner.VERSION,
                                             inputs=wanted)
        report["reused"] = saved is not None
        if saved is None:
            if inspect is None:
                raise scene.AnalysisError("no analyser")
            inputs = []
            for index, visual in enumerate(visuals, start=1):
                if cancelled():
                    raise Cancelled()
                if visual.duration_ms is None:
                    width, height = measure(visual.path) if measure is not None else (1, 1)
                    inputs.append(planner.Visual(visual.path, None, width, height))
                    continue
                info = replace(inspect(visual.path), duration_ms=visual.duration_ms)
                result = scene.analyse(visual.path, info, work / "mastering" / f"clip-{index}.json", ffmpeg=ffmpeg,
                                       run=ffmpeg_runner, cancelled=cancelled)
                inputs.append(planner.Visual(visual.path, visual.duration_ms, info.width, info.height, result))
            saved = planner.plan(inputs, total_ms, options, inputs=wanted, plan_id=f"product-{number}")
            mastering_plan.save(path, saved)
        report["segments"] = [render.Segment(visuals[p.visual], p.offset_ms, p.duration_ms) for p in planner.pieces(saved)]
        report["count"] = len(saved.segments)
        report["review"] = sum(segment.needs_review for segment in saved.segments)
        if saved.warnings:
            report["warnings"] = list(saved.warnings)
    except Cancelled:
        raise
    except Exception as error:  # noqa: BLE001 - mastering never stops a video the usual plan can make
        report["segments"] = None
        report["fallback"] = f"Không lập được kế hoạch hậu kỳ ({getattr(error, 'message', type(error).__name__)}); dùng cách ghép thường."
    return report


def run(spec: Spec, output_dir: Path, *, speaker: Callable[[Status], Speaker], probe: Callable[[Path], int],
        measure: Callable[[Path], tuple[int, int]] | None = None,
        ffmpeg: Path, ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER, inspect: Callable[[Path], Any] | None = None) -> dict[str, Any]:
    """Render every variant; one variant's failure does not stop the next.

    A cancel request stops at the next sentence or video and ends a running
    render; finished videos are kept.
    """
    work = output_dir / ".dubflow-work"
    work.mkdir(parents=True, exist_ok=True)
    status = Status(work / "status.json", len(spec.variants))
    visuals, cache, videos = [], {}, []
    voice = None
    partial = None

    def cancelled() -> bool:
        return (work / CANCEL_FILE).exists()

    def check() -> None:
        if cancelled():
            raise Cancelled()
    try:
        for path in spec.visuals:
            visuals.append(render.Visual(path, None if path.suffix.casefold() in render.IMAGE_SUFFIXES else probe(path)))
        backdrop = None
        # Full-screen media leaves nothing of a background visible; "Chỉ chữ" always fills the frame.
        if spec.background is not None and spec.template == layout.TEXT_ONLY:
            backdrop = render.Background(spec.background, True, spec.focus)
        elif spec.background is not None and spec.template == "ban-tin":
            if measure is None:
                raise ProductError("Không đọc được ảnh nền")
            backdrop = render.background(spec.background, *measure(spec.background), spec.focus)
        check()
        for number, text in enumerate(spec.variants, start=1):
            check()
            narration = work / f"narration-{number}.wav"
            # The same script, voice and speed as in the earlier run: its narration, not the voice model.
            reused = reuse.find(spec.reuse_from, text, spec.voice_id, spec.speed) if spec.reuse_from is not None else None
            if reused is not None:
                try:
                    reuse.copy(reused, narration)
                except OSError:
                    reused = None
            if reused is None and voice is None:
                # Outside the variant's own errors: a voice that cannot start fails the batch.
                voice = speaker(status)
            status.write(message=f"Dùng lại giọng đọc cũ cho video {number}/{len(spec.variants)}…" if reused
                         else f"Đang đọc kịch bản {number}/{len(spec.variants)}…")
            try:
                if reused is not None:
                    spoken = list(reused.spoken)
                    rate = _wav_rate(narration)
                else:
                    speech = []
                    for sentence in script.sentences(text):
                        check()
                        if sentence not in cache:
                            try:
                                cache[sentence] = voice.speak(sentence)
                            except Exception as error:
                                raise ProductError(f"Không đọc được câu “{sentence[:80]}”. Hãy sửa câu này rồi thử lại.") from error
                        speech.append((sentence, cache[sentence]))
                    spoken = [(sentence, len(samples) * 1000 // voice.sample_rate) for sentence, samples in speech]
                    rate = voice.sample_rate
                    render.write_narration(narration, [samples for _, samples in speech], rate,
                                           lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, tail_ms=script.TAIL_MS)
                # What this narration says, so a later Mẫu change can reuse it too.
                reuse.write_sidecar(work, number, voice_id=spec.voice_id, speed=spec.speed, text=text, sample_rate=rate, spoken=spoken)
                captions, total_ms = script.captions(spoken)
                _atomic_text(work / "captions.ass", layout.ass_document(spec.name, spec.price, captions, total_ms, outlined=backdrop is not None,
                                                                             template=spec.template, features=spec.features))
                status.write(message=f"Đang render video {number}/{len(spec.variants)}…")
                final = output_dir / f"video-{number}.mp4"
                partial = work / f"video-{number}.partial.mp4"
                mastered = None
                if spec.mastering is not None and spec.template != layout.TEXT_ONLY:
                    status.write(message=f"Đang phân tích cảnh cho video {number}/{len(spec.variants)}…")
                    mastered = _mastered(spec, visuals, total_ms, work, number, inspect=inspect, measure=measure,
                                         ffmpeg=ffmpeg, ffmpeg_runner=ffmpeg_runner, cancelled=cancelled)
                    status.write(message=f"Đang render video {number}/{len(spec.variants)}…")
                if mastered is not None and mastered["segments"] is not None:
                    segments = mastered.pop("segments")
                else:
                    segments = [] if spec.template == layout.TEXT_ONLY else render.plan(visuals, total_ms)
                    if mastered is not None:
                        mastered.pop("segments")
                ffmpeg_runner(render.ffmpeg_argv(ffmpeg, segments, narration, spec.music,
                                                 spec.music_volume, total_ms, partial, encoder=encoder, backdrop=backdrop,
                                                 media_height=layout.media_height(spec.template)), work, cancelled)
                check()
                os.replace(partial, final)
                partial = None
                _atomic_text(output_dir / f"script-{number}.txt", text.strip() + "\n")
                _atomic_text(output_dir / f"captions-{number}.srt", layout.srt_document(captions))
                videos.append({"index": number, "ok": True, "path": str(final), "duration_ms": total_ms, "reused_voice": reused is not None,
                               **({"mastering": mastered} if mastered is not None else {})})
            except ProductError as error:
                videos.append({"index": number, "ok": False, "message": error.message})
            status.write(done=number, videos=videos)
    except Cancelled:
        if partial is not None:
            partial.unlink(missing_ok=True)
        made = sum(1 for video in videos if video["ok"])
        status.write(state="CANCELLED", message=f"Đã hủy. Giữ lại {made} video đã xong." if made else "Đã hủy.", videos=videos)
        return status.value
    except ProductError as error:
        status.write(state="FAILED", message=error.message, videos=videos)
        return status.value
    except Exception as error:  # noqa: BLE001 - the desktop shows the reason
        status.write(state="FAILED", message=f"Lỗi khi tạo video: {getattr(error, 'code', type(error).__name__)}: {str(error)[:200]}", videos=videos)
        return status.value
    finally:
        if voice is not None:
            voice.close()
    made = sum(1 for video in videos if video["ok"])
    state = "COMPLETED" if made else "FAILED"
    message = f"Đã tạo {made}/{len(videos)} video" if made else "Không tạo được video nào"
    status.write(state=state, message=message)
    return status.value
