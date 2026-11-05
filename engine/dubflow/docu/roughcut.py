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
from . import overlays, safezone, soundscape
from .narration import _Status
from .project import DocuError, Spec, replace, script_path, work_dir, write_json
from .script import CHAPTERS, load_draft

WIDTH, HEIGHT, FPS = 1920, 1080, 30
ENCODERS = ("h264_mf", "libopenh264")
VIDEO_BITRATE = "8M"
RECIPE = "docu-roughcut-v4"
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


def video_filter(width: int, height: int, framing: dict[str, Any], *, frames: int, ken_burns: bool,
                 zoom_push: float = KEN_BURNS_ZOOM, labels: str | None = None) -> str:
    """One slice's picture: cover the caption band if framing cannot clear it, frame, fill 1080p, look, motion, bars."""
    steps: list[str] = []
    graph: list[str] = []
    band = framing.get("band")
    label = "0:v"
    # Another channel's logo or watermark: blurred where it sits (the bundled LGPL FFmpeg has no delogo). The blur
    # grows with the box, so bold white text like "太空记" cannot be read through it, and it fades out over a margin
    # round the box instead of ending on a hard edge (#556).
    for number, logo in enumerate(framing.get("logos") or ()):
        x, y = _even(float(logo[0]) * width) - 2 if logo[0] else 0, _even(float(logo[1]) * height) - 2 if logo[1] else 0
        x, y = max(0, x), max(0, y)
        w, h = min(width - x, _even(float(logo[2]) * width)), min(height - y, _even(float(logo[3]) * height))
        if w < 4 or h < 4:
            continue
        margin = _even(max(8.0, safezone.LOGO_FEATHER * min(w, h)))
        left, top = max(0, x - margin), max(0, y - margin)
        right, bottom = min(width, x + w + margin), min(height, y + h + margin)
        outer_w, outer_h = right - left, bottom - top
        # Opaque over the box and a quarter of the margin past it, then a slow fade over the rest; along the frame's
        # own edges it stays opaque to the border.
        reach = margin // 4
        inner_x, inner_y = (x - left) - reach if x > left else 0, (y - top) - reach if y > top else 0
        inner_r = outer_w - ((right - x - w) - reach if right > x + w else 0)
        inner_b = outer_h - ((bottom - y - h) - reach if bottom > y + h else 0)
        sigma = max(safezone.LOGO_SIGMA_MIN, int(round(min(w, h) * safezone.LOGO_SIGMA)))
        graph.append(f"[{label}]split=2[logo{number}base][logo{number}cut];[logo{number}cut]crop={outer_w}:{outer_h}:{left}:{top},"
                     f"gblur=sigma={sigma}:steps=3,format=yuva420p[logo{number}soft];"
                     f"color=c=black:s={outer_w}x{outer_h},drawbox=x={inner_x}:y={inner_y}:w={inner_r - inner_x}:h={inner_b - inner_y}"
                     f":color=white:t=fill,gblur=sigma={max(2, margin // 4)}:steps=3,format=gray[logo{number}mask];"
                     f"[logo{number}soft][logo{number}mask]alphamerge[logo{number}blur];"
                     f"[logo{number}base][logo{number}blur]overlay={left}:{top}:shortest=1[logo{number}]")
        label = f"logo{number}"
    if band and not framing.get("clear", True):
        # What framing cannot clear: blurred and darkened in place, its top edge feathered into the picture (#548).
        box = safezone.cover_box(safezone.Band(float(band["top"]), float(band["bottom"]), float(band.get("share", 1.0))), height)
        if box:
            top, feather, strip = box
            graph.append(f"[{label}]split=2[base][band];[band]crop=iw:{strip}:0:{top},"
                         f"gblur=sigma={safezone.COVER_SIGMA}:steps={safezone.COVER_STEPS},"
                         f"curves=all='0/0 1/{1 - safezone.COVER_DARKEN:g}',format=yuva420p[soft];"
                         f"gradients=s={width}x{strip}:c0=black:c1=white:x0=0:y0=0:x1=0:y1={feather}:d=0.1:r=10,format=gray[mask];"
                         f"[soft][mask]alphamerge[strip];[base][strip]overlay=0:{top}[covered]")
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
    if ken_burns and zoom_push > 0:
        # Upscaled first, so the slow push does not step from pixel to pixel.
        after.append(f"scale={WIDTH * 2}:{HEIGHT * 2},zoompan=z='1+{zoom_push:g}*on/{max(1, frames - 1)}':d=1"
                     f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={WIDTH}x{HEIGHT}:fps={FPS}")
    bar = _even(float(framing.get("letterbox") or 0.0) * HEIGHT) if framing.get("letterbox") else 0
    if bar:
        after.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    if labels:
        # English labels over foreign text in the picture (MCP apply_smart_label_overlay), drawn last.
        after.append(f"subtitles={labels}")
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


def mux_argv(ffmpeg: Path, concat: str, narration: Path, seconds: float, output: Path, bed: Path | None = None,
             ambience: Path | None = None) -> list[str]:
    """Picture, narration and, when given, the ducked music bed and the scenes' room tone with the act hits."""
    head = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-i", str(narration)]
    if bed is None and ambience is None:
        audio = ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", "apad"]
    else:
        inputs = [path for path in (bed, ambience) if path is not None]
        for path in inputs:
            head += ["-i", str(path)]
        audio = ["-filter_complex", overlays.ducked_mix(bed=bed is not None, ambience=(2 + int(bed is not None)) if ambience else None),
                 "-map", "0:v:0", "-map", "[audio]", "-c:v", "copy"]
    return head + audio + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{seconds:.3f}",
                           "-movflags", "+faststart", "-f", "mp4", str(output)]


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"


LABELS = "labels.json"


def load_labels(run_dir: Path) -> list[dict[str, Any]]:
    """Label boxes set over MCP: scene, words and normalized centre and width."""
    try:
        value = json.loads((work_dir(run_dir) / LABELS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = value.get("labels") if isinstance(value, dict) else None
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)] if isinstance(items, list) else []


def labels_ass(labels: Sequence[dict[str, Any]], seconds: float, overlay: Sequence[str] = (), bar: int = 0) -> str:
    """Dark slate boxes (90 % opaque) with white words, each at its centre, wrapped to its width; then the
    slice's number cards, lower-thirds and captions (``overlay``: rows from ``overlays.overlay_rows``)."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: Label,Segoe UI,44,&H00FFFFFF,&H192A170F,&H192A170F,1,0,3,14,0,5,0,0,0\n"
            + (overlays.styles(bar) if overlay else "") + "\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    end = f"0:{int(seconds) // 60:02d}:{seconds % 60:05.2f}"
    rows = []
    for label in labels:
        x, y, w = float(label["x"]) * WIDTH, float(label["y"]) * HEIGHT, float(label["w"]) * WIDTH
        margin = max(0, round((WIDTH - w) / 2))
        rows.append(f"Dialogue: 0,0:00:00.00,{end},Label,,{margin},{margin},0,,{{\\pos({round(x)},{round(y)})}}{_ass_text(label['text'])}\n")
    return head + "".join(rows) + "".join(overlay)


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
        from .visual import load_pacing
        pace = load_pacing(run_dir)["ken_burns"]
        push = KEN_BURNS_ZOOM if pace is None else pace
        labels = load_labels(run_dir)
        options = overlays.load_options(run_dir)
        # The letterbox is the same for every source; overlays sit inside the picture, above the bars.
        bar = max((_even(float(item.get("letterbox") or 0.0) * HEIGHT) if item.get("letterbox") else 0 for item in framings.values()),
                  default=0)
        events = overlays.all_events(run_dir, script, timeline, options, bar)
        scene_of = {id(piece): (scene["scene_id"], index) for scene in plan["scenes"] for index, piece in enumerate(scene["slices"])}
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
            name = f"slice-{index:04d}.mp4"
            scene_id, position = scene_of[id(piece)]
            shown = [label for label in labels if label.get("scene_id") == scene_id
                     and label.get("clip_index", position) == position]
            overlay = overlays.overlay_rows(events, float(piece["at"]), count / FPS)
            ass = None
            if shown or overlay:
                ass = f"slice-{index:04d}.ass"
                (work / ass).write_text(labels_ass(shown, count / FPS, overlay, bar), encoding="utf-8")
            graph = video_filter(*sizes[source_id], framings.get(source_id, {}), frames=count,
                                 ken_burns=shot_end - shot_start >= KEN_BURNS_SHOT, zoom_push=push, labels=ass)
            stat = video.stat()
            output = work / name
            key = {"recipe": RECIPE, "video": [str(video), stat.st_size, stat.st_mtime_ns], "start": round(start, 3),
                   "frames": count, "graph": graph, "labels": shown, "overlay": overlay}
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
        bed = None
        if options["music"] != "none":
            status.write(message="Đang tạo nhạc nền…")
            if options["music"] == "file":
                music = Path(options["music_path"])
                if not music.is_file():
                    raise DocuError(f"Không tìm thấy file nhạc nền: {music.name}")
                argv = overlays.file_bed_argv(ffmpeg, music, seconds, work / "bed.partial.wav")
                stat = music.stat()
                key = {"argv": argv[1:], "music": [stat.st_size, stat.st_mtime_ns]}
            else:
                argv = overlays.bed_argv(ffmpeg, overlays.chapter_moods(script, timeline, seconds), seconds, work / "bed.partial.wav")
                key = {"argv": argv[1:]}
            bed = work / "bed.wav"
            if _receipt(work / "bed.json") != {"key": key} or not bed.is_file():
                ffmpeg_runner(argv, work, cancel.exists)
                (work / "bed.partial.wav").replace(bed)
                write_json(work / "bed.json", {"key": key})
        ambience = None
        if options["sfx"]:
            built = soundscape.ambience_argv(ffmpeg, script, timeline, seconds, work / "ambience.partial.wav",
                                             soundscape.folders(run_dir))
            if built is not None:
                status.write(message="Đang tạo âm thanh môi trường…")
                argv, files = built
                key = {"argv": argv[1:], "files": [[str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in files]}
                ambience = work / "ambience.wav"
                if _receipt(work / "ambience.json") != {"key": key} or not ambience.is_file():
                    ffmpeg_runner(argv, work, cancel.exists)
                    (work / "ambience.partial.wav").replace(ambience)
                    write_json(work / "ambience.json", {"key": key})
        status.write(message="Đang ghép hình với lời đọc…")
        partial = work / "documentary.partial.mp4"
        ffmpeg_runner(mux_argv(ffmpeg, "concat.txt", narration, seconds, partial, bed, ambience), work, cancel.exists)
        try:
            replace(partial, run_dir / VIDEO)
        except PermissionError as error:
            raise DocuError("documentary.mp4 đang mở ở chỗ khác (trình phát hay trình chỉnh sửa); hãy đóng nó rồi dựng lại") from error
        write_json(run_dir / "source_credit.json", {"schema_version": 1, "sources": credits(spec)})
        (run_dir / "description.txt").write_text(description(script, timeline, spec), encoding="utf-8", newline="\n")
        status.write(state="COMPLETED", done=status.value["total"], message="Đã dựng bản nháp", path=str(run_dir / VIDEO),
                     duration=round(seconds, 3), overlays=overlays.describe(options))
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
