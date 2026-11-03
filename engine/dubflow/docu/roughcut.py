"""The rough cut: the visual plan and the narration as ``documentary.mp4`` (ADR-0049, phase 2b).

Every slice becomes its own short H.264 file (1920×1080, 30 fps, no sound),
frame-exact against the timeline and kept with a receipt so a cancelled or
repeated render only redoes what changed. The files are joined without
re-encoding and muxed with the narration.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import traceback
from typing import Any, Callable, Sequence

from engine.dubflow.product.job import Cancelled, ProductError, run_ffmpeg
from .narration import _Status
from .project import DocuError, Spec, script_path, work_dir, write_json
from .script import CHAPTERS, load_draft

WIDTH, HEIGHT, FPS = 1920, 1080, 30
ENCODERS = ("h264_mf", "libopenh264")
VIDEO_BITRATE = "8M"
RECIPE = "docu-roughcut-v1"
# The bundled LGPL FFmpeg has no `eq`: a gentle S-curve for contrast and +8 % saturation, the same on every source.
LOOK = "curves=all='0/0 0.25/0.235 0.75/0.77 1/1',huesaturation=saturation=0.08"
KEN_BURNS_SHOT = 6.0
KEN_BURNS_ZOOM = 0.04
# Sources narrower than this are fitted over a blurred fill instead of cropped to 16:9.
WIDE = 1.5
END_CARD = 6.0
VIDEO = "documentary.mp4"
CHAPTER_TITLES = {"HOOK": "The Hook", "PROBLEM": "The Problem", "ENGINEERING_SOLUTION": "The Engineering",
                  "CRITICAL_EVALUATION": "The Verdict"}

Probe = Callable[[Path], tuple[int, int]]


@dataclass(frozen=True)
class Piece:
    index: int
    source_id: str
    start: float
    frames: int
    long_shot: bool


def probe_display(ffprobe: Path, video: Path) -> tuple[int, int]:
    """The picture's size as FFmpeg's filters see it: phone videos tagged as rotated come out turned."""
    result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height:stream_side_data=rotation:stream_tags=rotate", "-of", "json", str(video)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        rotation = stream.get("tags", {}).get("rotate") or next(
            (item.get("rotation") for item in stream.get("side_data_list", []) if "rotation" in item), 0)
        if abs(int(float(rotation))) % 180 == 90:
            width, height = height, width
        if result.returncode or width <= 0 or height <= 0:
            raise ValueError("probe failed")
        return width, height
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise DocuError(f"Không đọc được kích thước video {video.name}") from error


def frame_counts(starts: Sequence[float], total: float) -> list[int]:
    """Frames per slice from rounded boundaries, so the sum is exactly the film's frames."""
    bounds = [round(start * FPS) for start in starts] + [round(total * FPS)]
    return [max(1, bounds[i + 1] - bounds[i]) for i in range(len(starts))]


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def video_filter(width: int, height: int, framing: dict[str, Any], *, frames: int, ken_burns: bool) -> str:
    """One slice's picture: cover the caption band if framing cannot clear it, frame, fill 1080p, look, motion, bars."""
    steps: list[str] = []
    graph: list[str] = []
    band = framing.get("band")
    label = "0:v"
    if band and not framing.get("clear", True):
        # Edge stretch: the two rows just above the captions, stretched down over them.
        top, bottom = _even(band["top"] * height), _even(band["bottom"] * height)
        if 2 <= top < bottom <= height:
            graph.append(f"[0:v]split=2[base][edge];[edge]crop=iw:2:0:{top - 2},scale=iw:{bottom - top},setsar=1[strip];"
                         f"[base][strip]overlay=0:{top}[covered]")
            label = "covered"
    zoom, shift = float(framing.get("zoom", 1.0)), float(framing.get("shift", 0.0))
    crop_w, crop_h = _even(width / zoom), _even(height / zoom)
    x = (width - crop_w) // 2
    y = max(0, min(height - crop_h, int(round((height - crop_h) / 2 - shift * height))))
    steps.append(f"crop={crop_w}:{crop_h}:{x}:{y}")
    if crop_w / crop_h >= WIDE:
        steps.append(f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1")
        picture = ",".join(steps)
        graph.append(f"[{label}]{picture}[framed]")
    else:
        graph.append(f"[{label}]{','.join(steps)},split=2[back][front];"
                     f"[back]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},gblur=sigma=24,setsar=1[fill];"
                     f"[front]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,setsar=1[fit];"
                     f"[fill][fit]overlay=(W-w)/2:(H-h)/2[framed]")
    after = [LOOK]
    if ken_burns:
        # Upscaled first, so the slow push does not step from pixel to pixel.
        after.append(f"scale={WIDTH * 2}:{HEIGHT * 2},zoompan=z='1+{KEN_BURNS_ZOOM}*on/{max(1, frames - 1)}':d=1"
                     f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={WIDTH}x{HEIGHT}:fps={FPS}")
    bar = _even(float(framing.get("letterbox") or 0.0) * HEIGHT) if framing.get("letterbox") else 0
    if bar:
        after.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    after.append(f"fps={FPS},format=yuv420p,setsar=1")
    graph.append(f"[framed]{','.join(after)}[video]")
    return ";".join(graph)


def slice_argv(ffmpeg: Path, video: Path, start: float, frames: int, graph: str, encoder: str, output: Path) -> list[str]:
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(video),
            "-filter_complex", graph, "-map", "[video]", "-an", "-frames:v", str(frames),
            "-c:v", encoder, "-b:v", VIDEO_BITRATE, "-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "15360",
            "-movflags", "+faststart", "-f", "mp4", str(output)]


def card_argv(ffmpeg: Path, ass: str, frames: int, encoder: str, output: Path) -> list[str]:
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}",
            "-vf", f"subtitles={ass},format=yuv420p,setsar=1", "-frames:v", str(frames), "-an",
            "-c:v", encoder, "-b:v", VIDEO_BITRATE, "-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "15360",
            "-f", "mp4", str(output)]


def mux_argv(ffmpeg: Path, concat: str, narration: Path, seconds: float, output: Path) -> list[str]:
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat,
            "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", "apad",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{seconds:.3f}",
            "-movflags", "+faststart", "-f", "mp4", str(output)]


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"


def credits(spec: Spec) -> list[dict[str, Any]]:
    return [{"source_id": source.id, "title": source.title, "origin_url": source.origin_url, "credit": source.credit}
            for source in spec.sources]


def credit_line(item: dict[str, Any]) -> str:
    credit = item.get("credit") if isinstance(item.get("credit"), dict) else {}
    creator = str(credit.get("creator") or "").strip()[:120]
    platform = str(credit.get("platform") or "").strip()[:40]
    url = str(credit.get("url") or item.get("origin_url") or "").strip()[:500]
    author = f"{creator} ({platform})" if creator and platform else creator or platform or str(item["title"])[:120]
    return " – ".join(part for part in (author, url) if part)


def description(script: dict[str, Any], timeline: dict[str, Any], spec: Spec) -> str:
    lines = [str(script.get("project_title") or spec.title), ""]
    chapters = [chapter for chapter in timeline.get("chapters", []) if chapter.get("chapter_type") in CHAPTERS]
    for index, chapter in enumerate(chapters):
        # YouTube reads chapters only when the first one starts at 0:00.
        lines.append(f"{_clock(0 if index == 0 else chapter['start'])} {CHAPTER_TITLES[chapter['chapter_type']]}")
    lines += ["", "Footage:"] + [f"- {credit_line(item)}" for item in credits(spec)]
    lines += ["", "Narration: synthetic voice (Kokoro). Figures converted to US units and checked against the sources."]
    return "\n".join(lines) + "\n"


def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def end_card(spec: Spec) -> str:
    rows = "\\N".join(_ass_text(credit_line(item)) for item in credits(spec))
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: Title,Arial,64,&H00FFFFFF,&H00000000,&H00000000,1,0,1,0,0,8,120,120,300\n"
            "Style: Body,Arial,38,&H00DDDDDD,&H00000000,&H00000000,0,0,1,0,0,8,120,120,420\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,0:00:{END_CARD:05.2f},Title,,0,0,0,,Footage\n"
            f"Dialogue: 0,0:00:00.00,0:00:{END_CARD:05.2f},Body,,0,0,0,,{rows}\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run(run_dir: Path, spec: Spec, *, ffmpeg: Path, probe: Probe, ffmpeg_runner: Callable = run_ffmpeg,
        encoders: Sequence[str] = ENCODERS) -> dict[str, Any]:
    work = work_dir(run_dir) / "render"
    work.mkdir(parents=True, exist_ok=True)
    cancel = work / "cancel"
    cancel.unlink(missing_ok=True)
    status = _Status(work / "status.json", 1, message="Đang chuẩn bị dựng bản nháp…")
    try:
        script = load_draft(script_path(run_dir))
        try:
            timeline_path = run_dir / "master_audio_timeline.json"
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            plan = json.loads((run_dir / "visual_plan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DocuError("Hãy tạo lời đọc và lập kế hoạch hình ảnh trước khi dựng bản nháp") from error
        if plan.get("script_sha256") != script.get("script_sha256") or plan.get("timeline_sha256") != _sha256(timeline_path):
            raise DocuError("Kế hoạch hình ảnh đã cũ so với kịch bản hoặc lời đọc; hãy lập lại kế hoạch")
        narration = run_dir / "narration.wav"
        if not narration.is_file():
            raise DocuError("Không tìm thấy narration.wav; hãy tạo lại lời đọc")
        videos = {source.id: source.video for source in spec.sources}
        framings = {source["id"]: source["framing"] for source in plan["sources"]}
        cuts: dict[str, list[float]] = {}
        for source in spec.sources:
            shots_file = work_dir(run_dir) / "sources" / source.id / "shots.json"
            try:
                cuts[source.id] = [0.0, *json.loads(shots_file.read_text(encoding="utf-8"))["cuts"]]
            except (OSError, ValueError, KeyError):
                cuts[source.id] = [0.0]
        slices = [piece for scene in plan["scenes"] for piece in scene["slices"]]
        if not slices:
            raise DocuError("Kế hoạch hình ảnh chưa có lát cắt nào")
        total = float(plan["duration"])
        frames = frame_counts([float(piece["at"]) for piece in slices], total)
        card = spec.burn_end_credits
        status.write(total=len(slices) + int(card) + 1)
        sizes: dict[str, tuple[int, int]] = {}
        encoder = encoders[0]
        parts: list[str] = []
        for index, (piece, count) in enumerate(zip(slices, frames), 1):
            if cancel.exists():
                raise Cancelled()
            source_id = piece["source_id"]
            video = videos.get(source_id)
            if video is None or not video.is_file():
                raise DocuError(f"Không tìm thấy video của {source_id}")
            if source_id not in sizes:
                sizes[source_id] = probe(video)
            start = float(piece["start"])
            shot_start = max(cut for cut in cuts[source_id] if cut <= start + 1e-3)
            later = [cut for cut in cuts[source_id] if cut > start + 1e-3]
            shot_end = later[0] if later else math.inf
            graph = video_filter(*sizes[source_id], framings.get(source_id, {}), frames=count,
                                 ken_burns=shot_end - shot_start >= KEN_BURNS_SHOT)
            stat = video.stat()
            name = f"slice-{index:04d}.mp4"
            output = work / name
            key = {"recipe": RECIPE, "video": [str(video), stat.st_size, stat.st_mtime_ns], "start": round(start, 3),
                   "frames": count, "graph": graph}
            receipt = work / f"slice-{index:04d}.json"
            saved = _receipt(receipt)
            if not (output.is_file() and saved and saved.get("key") == key):
                encoder = _encode(ffmpeg_runner, encoders, encoder, work, cancel.exists,
                                  lambda chosen: slice_argv(ffmpeg, video, start, count, graph, chosen, work / f"{name}.partial.mp4"),
                                  work / f"{name}.partial.mp4", output)
                write_json(receipt, {"key": key, "encoder": encoder})
            parts.append(name)
            status.write(done=index, message=f"Đang dựng lát cắt {index}/{len(slices)}")
        seconds = total
        if card:
            (work / "credits.ass").write_text(end_card(spec), encoding="utf-8")
            count = round(END_CARD * FPS)
            encoder = _encode(ffmpeg_runner, encoders, encoder, work, cancel.exists,
                              lambda chosen: card_argv(ffmpeg, "credits.ass", count, chosen, work / "card.partial.mp4"),
                              work / "card.partial.mp4", work / "card.mp4")
            parts.append("card.mp4")
            seconds += END_CARD
        (work / "concat.txt").write_text("".join(f"file '{part}'\n" for part in parts), encoding="utf-8")
        status.write(message="Đang ghép hình với lời đọc…")
        partial = work / "documentary.partial.mp4"
        ffmpeg_runner(mux_argv(ffmpeg, "concat.txt", narration, seconds, partial), work, cancel.exists)
        partial.replace(run_dir / VIDEO)
        write_json(run_dir / "source_credit.json", {"schema_version": 1, "sources": credits(spec)})
        (run_dir / "description.txt").write_text(description(script, timeline, spec), encoding="utf-8", newline="\n")
        status.write(state="COMPLETED", done=status.value["total"], message="Đã dựng bản nháp", path=str(run_dir / VIDEO),
                     duration=round(seconds, 3))
        return status.value
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy dựng bản nháp")
        return status.value
    except (DocuError, ProductError) as error:
        status.write(state="FAILED", message=getattr(error, "message", str(error)))
        return status.value
    except Exception as error:  # a source that will not decode, a full disk…: say so instead of dying
        traceback.print_exc()
        status.write(state="FAILED", message=f"Không dựng được bản nháp ({type(error).__name__}: {str(error)[:200]}); hãy thử lại")
        return status.value


def _encode(runner: Callable, encoders: Sequence[str], current: str, work: Path, cancelled: Callable[[], bool],
            argv: Callable[[str], list[str]], partial: Path, output: Path) -> str:
    """Encode with the current encoder; on a failure, the next one (Media Foundation is missing on some systems)."""
    order = [current] + [name for name in encoders if name != current]
    last: Exception | None = None
    for name in order:
        try:
            runner(argv(name), work, cancelled)
        except ProductError as error:
            last = error
            partial.unlink(missing_ok=True)
            continue
        partial.replace(output)
        return name
    raise DocuError("Không mã hóa được video bản nháp") from last
