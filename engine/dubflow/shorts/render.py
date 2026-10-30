"""Geometry, title ASS and FFmpeg plans for unmodified chronological excerpts."""
from __future__ import annotations

from pathlib import Path
import textwrap

from engine.dubflow.package.render import Video, encoding, seconds, start
from engine.dubflow.product.layout import HEIGHT, MEDIA_HEIGHT, PANEL_COLOR, WIDTH, _text, _time

ENCODER = "h264_mf"
LAYOUTS = ("auto", "ban-tin", "mo-nen")


def effective_layout(video: Video, layout: str) -> str:
    if layout not in LAYOUTS:
        raise ValueError("Bố cục Shorts không hợp lệ")
    return "vertical" if video.height >= video.width else "ban-tin" if layout == "auto" else layout


def geometry(video: Video, layout: str) -> str:
    mode = effective_layout(video, layout)
    if mode == "vertical":
        # Fit, never stretch: a 9:16 source fills the canvas; 4:5 or 3:4 sit on a blurred fill of themselves.
        fit = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,setsar=1"
    else:
        fit = f"scale={WIDTH}:-2,setsar=1"
    if mode == "ban-tin":
        return (f"[0:v]{fit},pad={WIDTH}:{MEDIA_HEIGHT}:0:(oh-ih)/2:color=black,"
                f"pad={WIDTH}:{HEIGHT}:0:0:color={PANEL_COLOR}[base]")
    if mode == "vertical" and video.width * HEIGHT == video.height * WIDTH:
        return f"[0:v]{fit}[base]"
    return (f"[0:v]split=2[front][back];[back]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,gblur=sigma=30[blur];"
            f"[front]{fit}[media];[blur][media]overlay=(W-w)/2:(H-h)/2[base]")


def ass_document(title: str, duration_ms: int, mode: str) -> str:
    panel = mode == "ban-tin"
    at = 1310 if panel else 100
    wrapped = r"\N".join(textwrap.wrap(_text(title), width=26, break_long_words=True, break_on_hyphens=False))
    whole = f"0:00:00.00,{_time(duration_ms, ass=True)}"
    return "\n".join((
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,Arial,66,&H00FFFFFF,&H000000FF,&H60000000,&H60000000,-1,0,0,0,100,100,0,0,{1 if panel else 3},{2 if panel else 18},0,8,70,70,0,1",
        "Style: Footer,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,70,70,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{whole},Title,,0,0,0,,{{\\an8\\pos(540,{at})}}{wrapped}",
        *([f"Dialogue: 0,{whole},Footer,,0,0,0,,{{\\an2\\pos(540,1820)}}Xem bản đầy đủ trên kênh"] if panel else []),
    )) + "\n"


def ffmpeg_argv(ffmpeg: Path, source: Path, video: Video, layout: str, start_ms: int, end_ms: int,
                output: Path, *, encoder: str = ENCODER) -> list[str]:
    filters = geometry(video, layout) + ";[base]fps=30,format=yuv420p,subtitles=title.ass[video]"
    return (start(ffmpeg) + ["-ss", seconds(start_ms), "-i", str(source), "-filter_complex", filters,
            "-map", "[video]", "-map", "0:a:0?", "-t", seconds(end_ms - start_ms)]
            + encoding(Video(WIDTH, HEIGHT, "30"), encoder) + [str(output)])
