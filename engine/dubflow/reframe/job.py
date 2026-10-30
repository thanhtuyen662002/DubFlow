"""Strict private v1 spec, cancellable rendering and verified atomic publication."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
import unicodedata

from engine.dubflow.package.job import probe_video
from engine.dubflow.product.job import Cancelled, ProductError, _atomic_text, run_ffmpeg
from . import render

CANCEL_FILE = "cancel"
MAX_MS = 9007199254740991


class ReframeError(ProductError):
    """A displayable error containing no private paths or FFmpeg diagnostics."""


@dataclass(frozen=True)
class Spec:
    video: Path
    mode: str
    title: str = ""
    score: int | None = None
    pros: tuple[str, ...] = ()
    cons: tuple[str, ...] = ()
    summary: str = ""
    focus_start: float = 50
    focus_end: float = 50
    start_ms: int = 0
    end_ms: int | None = None


def load_spec(path: Path) -> Spec:
    def text(value: Any, limit: int) -> str:
        if type(value) is not str or len(value) > limit or any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("text")
        return value.strip()

    def points(value: Any) -> tuple[str, ...]:
        if type(value) is not list or len(value) > 3:
            raise ValueError("points")
        result = tuple(text(item, 60) for item in value)
        if any(not item for item in result):
            raise ValueError("blank point")
        return result

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate field")
        return result

    try:
        if path.stat().st_size > 16 * 1024:
            raise ValueError("size")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
        fields = {"schema_version", "video", "mode", "title", "score", "pros", "cons", "summary",
                  "focus_start", "focus_end", "start_ms", "end_ms"}
        if type(value) is not dict or not {"schema_version", "video", "mode"} <= value.keys() or value.keys() - fields:
            raise ValueError("fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("version")
        if type(value["video"]) is not str or any(unicodedata.category(c) == "Cc" for c in value["video"]):
            raise ValueError("video")
        video = Path(value["video"])
        if not video.is_absolute() or video.name != "final_vi.mp4" or not video.is_file():
            raise ValueError("video")
        video = video.resolve()
        if video.name != "final_vi.mp4" or not any(p.name.casefold() == "dubflow output" for p in video.parents):
            raise ValueError("boundary")
        if value["mode"] not in render.MODES:
            raise ValueError("mode")
        score = value.get("score")
        if score is not None and (type(score) is not int or not 0 <= score <= 10):
            raise ValueError("score")
        focus = [value.get(name, 50) for name in ("focus_start", "focus_end")]
        if any(type(v) not in (int, float) or not 0 <= v <= 100 for v in focus):
            raise ValueError("focus")
        start, end = value.get("start_ms") or 0, value.get("end_ms")
        if type(start) is not int or not 0 <= start <= MAX_MS:
            raise ValueError("start")
        if end is not None and (type(end) is not int or not start < end <= MAX_MS):
            raise ValueError("end")
        return Spec(video, value["mode"], text(value.get("title", ""), 80), score,
                    points(value.get("pros", [])), points(value.get("cons", [])), text(value.get("summary", ""), 120),
                    *focus, start, end)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ReframeError("Yêu cầu đổi khung không hợp lệ: chọn final_vi.mp4 trong DubFlow Output, tiêu đề ≤80, tóm tắt ≤120, tối đa 3 ý mỗi loại (≤60), điểm 0–10 và vị trí 0–100.") from error


class Status:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.value: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "message": "Đang chuẩn bị đổi khung 16:9…"}
        self.write()

    def write(self, **changes: Any) -> None:
        self.value.update(changes)
        _atomic_text(self.path, json.dumps(self.value, ensure_ascii=False))


def run(spec: Spec, *, probe: Callable[[Path], int], measure: Callable[[Path], render.Video], ffmpeg: Path,
        ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER) -> dict[str, Any]:
    work = spec.video.parent / ".dubflow-work" / "reframe"
    work.mkdir(parents=True, exist_ok=True)
    status = Status(work / "status.json")
    partial, final = work / "reframe.partial.mp4", spec.video.parent / "final_vi_16x9.mp4"
    receipt = work / "receipt.json"

    def cancelled() -> bool:
        return (work / CANCEL_FILE).exists()

    def check() -> None:
        if cancelled():
            raise Cancelled()

    def digest(path: Path) -> str:
        hashed = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                check()
                hashed.update(chunk)
        return hashed.hexdigest()

    def verify(path: Path, duration: int) -> None:
        check()
        if abs(probe(path) - duration) > 300:
            raise ReframeError("Thời lượng video 16:9 không khớp đoạn đã chọn")
        video = measure(path)
        if (video.width, video.height, Fraction(video.fps)) != (1920, 1080, 30):
            raise ReframeError("Kích thước hoặc tốc độ khung hình video 16:9 không khớp")
        check()

    try:
        check()
        video, source_ms = measure(spec.video), probe(spec.video)
        if video.height <= video.width:
            raise ReframeError("Chỉ đổi khung 16:9 cho video dọc (chiều cao lớn hơn chiều rộng).")
        end = source_ms if spec.end_ms is None else spec.end_ms
        if not 0 <= spec.start_ms < end <= source_ms:
            raise ReframeError("Mốc cắt nằm ngoài thời lượng video")
        duration = end - spec.start_ms
        code_files = [*sorted(Path(__file__).parent.glob("*.py")),
                      *(Path(__file__).parents[1] / p for p in ("compilation/layout.py", "compilation/render.py",
                         "package/render.py", "product/layout.py", "product/job.py", "product/script.py"))]
        key = hashlib.sha256(json.dumps([vars(spec), vars(video), source_ms, digest(spec.video), encoder,
            digest(ffmpeg) if ffmpeg.is_file() else str(ffmpeg),
            hashlib.sha256(b"".join(p.read_bytes() for p in code_files)).hexdigest()], default=str, sort_keys=True).encode()).hexdigest()
        reuse = False
        try:
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            reuse = saved["key"] == key and saved["sha256"] == digest(final)
            if reuse:
                verify(final, duration)
        except (OSError, ValueError, KeyError, TypeError, ReframeError):
            reuse = False
        if not reuse:
            status.write(message="Đang tạo video 16:9…")
            if spec.mode == "bang-thong-tin":
                panel = render.geometry(video.width, video.height, spec.mode).panel_width
                ass = render.panel_ass(spec.title, duration, panel_width=panel, score=spec.score,
                                       pros=spec.pros, cons=spec.cons, summary=spec.summary)
                blur = render.available_blur(ffmpeg) if ffmpeg.is_file() else "gblur=sigma=20"
                argv = render.clip_argv(ffmpeg, spec.video, video, spec.mode, spec.start_ms, duration,
                                        "panel.ass", partial, encoder=encoder, blur=blur)
            else:
                ass = render.title_ass(spec.title, duration)
                argv = render.crop_argv(ffmpeg, spec.video, spec.start_ms, duration, spec.focus_start,
                                       spec.focus_end, bool(spec.title), partial, encoder=encoder)
            _atomic_text(work / "panel.ass", ass)
            check()
            ffmpeg_runner(argv, work, cancelled)
            verify(partial, duration)
            result_hash = digest(partial)
            check()
            os.replace(partial, final)
            _atomic_text(receipt, json.dumps({"key": key, "sha256": result_hash}))
        check()
        status.write(state="COMPLETED", message="Đã tạo video 16:9.", path=str(final), duration_ms=duration)
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy đổi khung 16:9.")
    except ReframeError as error:
        status.write(state="FAILED", message=error.message)
    except Exception:
        status.write(state="FAILED", message="Không tạo được video 16:9. Hãy kiểm tra video, bộ mã hóa và dung lượng trống.")
    finally:
        partial.unlink(missing_ok=True)
    return status.value
