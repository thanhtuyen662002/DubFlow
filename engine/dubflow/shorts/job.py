"""Strict spec validation, cancellable per-clip checkpoints and atomic publication."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
from typing import Any, Callable
import unicodedata

from engine.dubflow.package.job import probe_video
from engine.dubflow.product.job import Cancelled, ProductError, _atomic_text, run_ffmpeg
from . import render

class ShortsError(ProductError):
    """A validation message safe to display without local media paths."""


CANCEL_FILE = "cancel"


@dataclass(frozen=True)
class Item:
    start_ms: int
    end_ms: int
    title: str


@dataclass(frozen=True)
class Spec:
    video: Path
    items: tuple[Item, ...]
    layout: str


def load_spec(path: Path) -> Spec:
    try:
        if path.stat().st_size > 16 * 1024:
            raise ValueError("too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict or set(value) != {"schema_version", "video", "items", "layout"}:
            raise ValueError("fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("version")
        if type(value["video"]) is not str or "\x00" in value["video"]:
            raise ValueError("video")
        video = Path(value["video"])
        if not video.is_absolute() or not video.is_file() or video.name != "final_vi.mp4":
            raise ValueError("video")
        video = video.resolve()
        if video.name != "final_vi.mp4" or not any(p.name.casefold() == "dubflow output" for p in video.parents):
            raise ValueError("output boundary")
        if value["layout"] not in render.LAYOUTS:
            raise ValueError("layout")
        items = value["items"]
        if type(items) is not list or not 1 <= len(items) <= 3:
            raise ValueError("items")
        parsed = []
        for item in items:
            if type(item) is not dict or set(item) != {"start_ms", "end_ms", "title"}:
                raise ValueError("item")
            start, end, title = item["start_ms"], item["end_ms"], item["title"]
            if type(start) is not int or type(end) is not int or start < 0 or not 5000 <= end - start <= 60000:
                raise ValueError("time")
            if type(title) is not str or not title.strip() or len(title) > 80 or any(unicodedata.category(c) == "Cc" for c in title):
                raise ValueError("title")
            parsed.append(Item(start, end, title.strip()))
        return Spec(video, tuple(parsed), value["layout"])
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ShortsError("Yêu cầu Shorts không hợp lệ: chọn final_vi.mp4 trong DubFlow Output, 1–3 đoạn dài 5–60 giây và tiêu đề tối đa 80 ký tự.") from error


def description(title: str, video_title: str, credit: dict[str, Any] | None = None) -> str:
    lines = [title, f"Xem bản đầy đủ: {video_title} trên kênh."]
    if credit:
        def clean(key: str) -> str:
            value = credit.get(key, "")
            if not isinstance(value, str) or any(unicodedata.category(c) == "Cc" for c in value):
                raise ShortsError("Thông tin nguồn không hợp lệ")
            return value.strip()
        creator, platform, url = clean("creator"), clean("platform"), clean("url")
        author = f"{creator} ({platform})" if creator and platform else creator or platform
        parts = [part for part in (author, url) if part]
        if parts:
            lines.append("Nguồn: " + " – ".join(parts))
    return "\n".join(lines) + "\n"


def video_title(video: Path) -> str:
    manifest = video.parent / "job_manifest.json"
    try:
        if manifest.stat().st_size <= 16 * 1024 * 1024:
            source = json.loads(manifest.read_text(encoding="utf-8"))["source"]["path"]
            if isinstance(source, str):
                # Read just the filename on either OS; never copy private directories.
                title = PureWindowsPath(source).stem.strip()
                if title and not any(unicodedata.category(c) == "Cc" for c in title):
                    return title
    except (OSError, ValueError, TypeError, KeyError):
        pass
    # Job output names retain the source title: <title> - vi/run-<id>.
    folder = video.parent.parent if video.parent.name.startswith("run-") else video.parent
    return folder.name.removesuffix(" - vi")


class Status:
    def __init__(self, path: Path, total: int) -> None:
        self.path = path
        self.value: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "done": 0, "total": total,
                                      "message": "Đang chuẩn bị cắt Shorts…", "outputs": []}
        self.write()

    def write(self, **changes: Any) -> None:
        self.value.update(changes)
        _atomic_text(self.path, json.dumps(self.value, ensure_ascii=False))


def run(spec: Spec, *, probe: Callable[[Path], int], measure: Callable[[Path], render.Video], ffmpeg: Path,
        ffmpeg_runner: Callable[[list[str], Path, Callable[[], bool]], None] = run_ffmpeg,
        encoder: str = render.ENCODER) -> dict[str, Any]:
    work = spec.video.parent / ".dubflow-work" / "shorts"
    work.mkdir(parents=True, exist_ok=True)
    output = spec.video.parent / "shorts"
    output.mkdir(exist_ok=True)
    status = Status(work / "status.json", len(spec.items))
    partial = work / "short.partial.mp4"

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

    try:
        check()
        duration, video = probe(spec.video), measure(spec.video)
        if duration <= 0 or any(item.end_ms > duration for item in spec.items):
            raise ShortsError("Đoạn Shorts nằm ngoài thời lượng video")
        source_key = digest(spec.video)
        code_paths = [*sorted(Path(__file__).parent.glob("*.py")),
                      Path(__file__).parents[1] / "package/render.py", Path(__file__).parents[1] / "product/layout.py"]
        code_key = hashlib.sha256(b"".join(p.read_bytes() for p in code_paths)).hexdigest()
        tool_key = digest(ffmpeg) if ffmpeg.is_file() else str(ffmpeg)
        credit_path = spec.video.parent / "source_credit.json"
        credit = None
        if credit_path.is_file():
            if credit_path.stat().st_size > 16 * 1024:
                raise ShortsError("Thông tin nguồn quá lớn")
            credit = json.loads(credit_path.read_text(encoding="utf-8"))
            if not isinstance(credit, dict):
                raise ShortsError("Thông tin nguồn không hợp lệ")
        for number, item in enumerate(spec.items, 1):
            check()
            text = description(item.title, video_title(spec.video), credit)
            final = output / f"short-{number}.mp4"
            receipt = work / f"short-{number}.receipt.json"
            key = hashlib.sha256(json.dumps([source_key, code_key, tool_key, vars(video), vars(item), spec.layout, encoder], sort_keys=True).encode()).hexdigest()
            reuse = False
            try:
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                reuse = saved["key"] == key and saved["sha256"] == digest(final)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            status.write(message=f"Đang tạo Shorts {number}/{len(spec.items)}…")
            if not reuse:
                _atomic_text(work / "title.ass", render.ass_document(item.title, item.end_ms - item.start_ms, render.effective_layout(video, spec.layout)))
                ffmpeg_runner(render.ffmpeg_argv(ffmpeg, spec.video, video, spec.layout, item.start_ms, item.end_ms, partial, encoder=encoder), work, cancelled)
                check()
                if abs(probe(partial) - (item.end_ms - item.start_ms)) > 300:
                    raise ShortsError("Thời lượng Shorts không khớp đoạn đã chọn")
                result = measure(partial)
                if (result.width, result.height) != (1080, 1920) or Fraction(result.fps) != 30:
                    raise ShortsError("Kích thước hoặc tốc độ khung hình Shorts không khớp")
                result_hash = digest(partial)
                check()
                os.replace(partial, final)
                _atomic_text(receipt, json.dumps({"key": key, "sha256": result_hash}))
            text_path = output / f"short-{number}.txt"
            _atomic_text(text_path, text)
            status.value["outputs"].append({"path": str(final), "text_path": str(text_path), "text": text, "title": item.title})
            status.write(done=number)
        status.write(state="COMPLETED", message="Đã tạo Shorts.")
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy cắt Shorts.")
    except ShortsError as error:
        status.write(state="FAILED", message=error.message)
    except ProductError:
        # FFmpeg diagnostics can contain private input paths; keep them in local logs.
        status.write(state="FAILED", message="Không tạo được Shorts. Hãy kiểm tra đoạn đã chọn và bộ mã hóa video.")
    except Exception:
        status.write(state="FAILED", message="Không tạo được Shorts. Hãy kiểm tra video, thông tin nguồn và dung lượng trống.")
    finally:
        partial.unlink(missing_ok=True)
    return status.value
