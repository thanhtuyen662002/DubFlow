"""Render every script variant of one product into its own narrated video."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Protocol, Sequence

from . import layout, render, script

MAX_VARIANTS = 5
MAX_SCRIPT_CHARS = 3000
MAX_VISUALS = 30
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
    visuals = value.get("visuals")
    if type(visuals) is not list or not 1 <= len(visuals) <= MAX_VISUALS:
        raise ProductError(f"Cần từ 1 đến {MAX_VISUALS} ảnh hoặc clip")
    music = value.get("music")
    volume = value.get("music_volume", 0.15)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 1:
        raise ProductError("Âm lượng nhạc nền không hợp lệ")
    voice = _string(value.get("voice_id"), "Giọng đọc", 96, required=True)
    return Spec(
        name=_string(value.get("name"), "Tên sản phẩm", 120),
        price=_string(value.get("price"), "Giá", 60),
        voice_id=voice,
        variants=texts,
        visuals=tuple(_file(item, render.IMAGE_SUFFIXES | render.VIDEO_SUFFIXES, "ảnh hoặc clip") for item in visuals),
        music=None if music is None else _file(music, render.MUSIC_SUFFIXES, "nhạc nền"),
        music_volume=float(volume),
        background=None if value.get("background") is None else _file(value["background"], render.IMAGE_SUFFIXES, "ảnh nền"),
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


def run(spec: Spec, output_dir: Path, *, speaker: Callable[[Status], Speaker], probe: Callable[[Path], int],
        measure: Callable[[Path], tuple[int, int]] | None = None,
        ffmpeg: Path, ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER) -> dict[str, Any]:
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
        if spec.background is not None:
            if measure is None:
                raise ProductError("Không đọc được ảnh nền")
            backdrop = render.background(spec.background, *measure(spec.background))
        check()
        voice = speaker(status)
        for number, text in enumerate(spec.variants, start=1):
            check()
            status.write(message=f"Đang đọc kịch bản {number}/{len(spec.variants)}…")
            try:
                speech = []
                for sentence in script.sentences(text):
                    check()
                    if sentence not in cache:
                        try:
                            cache[sentence] = voice.speak(sentence)
                        except Exception as error:
                            raise ProductError(f"Không đọc được câu “{sentence[:80]}”. Hãy sửa câu này rồi thử lại.") from error
                    speech.append((sentence, cache[sentence]))
                captions, total_ms = script.captions([(sentence, len(samples) * 1000 // voice.sample_rate) for sentence, samples in speech])
                narration = work / f"narration-{number}.wav"
                render.write_narration(narration, [samples for _, samples in speech], voice.sample_rate,
                                       lead_ms=script.LEAD_MS, gap_ms=script.GAP_MS, tail_ms=script.TAIL_MS)
                _atomic_text(work / "captions.ass", layout.ass_document(spec.name, spec.price, captions, total_ms, outlined=backdrop is not None))
                status.write(message=f"Đang render video {number}/{len(spec.variants)}…")
                final = output_dir / f"video-{number}.mp4"
                partial = work / f"video-{number}.partial.mp4"
                ffmpeg_runner(render.ffmpeg_argv(ffmpeg, render.plan(visuals, total_ms), narration, spec.music,
                                                 spec.music_volume, total_ms, partial, encoder=encoder, backdrop=backdrop), work, cancelled)
                check()
                os.replace(partial, final)
                partial = None
                _atomic_text(output_dir / f"script-{number}.txt", text.strip() + "\n")
                _atomic_text(output_dir / f"captions-{number}.srt", layout.srt_document(captions))
                videos.append({"index": number, "ok": True, "path": str(final), "duration_ms": total_ms})
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
