"""Pure ASS, audio and FFmpeg plans; the original export is always read-only."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from engine.dubflow.product.layout import _text, _time
from engine.dubflow.product.script import Caption

ENCODER = "h264_mf"
STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"


@dataclass(frozen=True)
class Video:
    width: int
    height: int
    fps: str

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or not 0 < Fraction(self.fps) <= 240:
            raise ValueError("invalid video geometry")


@dataclass(frozen=True)
class AudioInput:
    path: Path
    volume: float = 1.0
    loop: bool = False


def audio_plan(video: Path, music: Path | None, volume: float, background: str) -> tuple[AudioInput, ...]:
    if background == "keep":
        return (AudioInput(video),)
    if background not in ("replace", "duck") or music is None:
        raise ValueError("Cần chọn nhạc nền để thay nhạc")
    stems = video.parent / "editable"
    dialogue = stems / "dialogue_stem.wav"
    if not dialogue.is_file():
        raise ValueError("Không có bản giọng Việt riêng để thay nhạc")
    inputs = (AudioInput(dialogue), AudioInput(music, volume, True))
    if background == "duck":
        bed = stems / "background_stem.wav"
        if not bed.is_file():
            raise ValueError("Không có nền gốc đã tách để giữ nhỏ")
        inputs += (AudioInput(bed, 0.25),)
    return inputs


def ass_document(captions: Sequence[Caption], video: Video) -> str:
    # Size follows the short edge so portrait, landscape and square cards wrap.
    size = max(12, round(min(video.width, video.height) * 0.062))
    margin = max(8, round(video.width * 0.08))
    outline = max(1, round(size * 0.065))
    return "\n".join((
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {video.width}", f"PlayResY: {video.height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,Arial,{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},1,5,{margin},{margin},{margin},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *(f"Dialogue: 0,{_time(c.start_ms, ass=True)},{_time(c.end_ms, ass=True)},Caption,,0,0,0,,{_text(c.text)}" for c in captions),
    )) + "\n"


def seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def start(ffmpeg: Path) -> list[str]:
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]


def encoding(video: Video, encoder: str) -> list[str]:
    bitrate = round(max(6, min(10, 6 * video.width * video.height / (1920 * 1080))) * 1_000_000)
    return ["-c:v", encoder, "-b:v", str(bitrate), "-pix_fmt", "yuv420p", "-r", video.fps,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-f", "mp4"]


def card_argv(ffmpeg: Path, image: Path, narration: Path, ass: str, video: Video,
              duration_ms: int, output: Path, *, encoder: str = ENCODER) -> list[str]:
    return (start(ffmpeg) + ["-loop", "1", "-framerate", video.fps, "-i", str(image), "-i", str(narration),
            "-vf", f"scale={video.width}:{video.height},setsar=1,gblur=sigma=20,drawbox=color=black@0.45:t=fill,subtitles={ass}",
            "-af", STEREO, "-map", "0:v:0", "-map", "1:a:0", "-t", seconds(duration_ms)]
            + encoding(video, encoder) + [str(output)])


def main_argv(ffmpeg: Path, source: Path, audio: Sequence[AudioInput], video: Video,
              duration_ms: int, output: Path, *, encoder: str = ENCODER) -> list[str]:
    argv = start(ffmpeg) + ["-i", str(source)]
    filters = []
    for number, item in enumerate(audio):
        # The keep plan reads the main input; other plans add stems and music.
        index = 0 if item.path == source else number + 1
        if index:
            argv += (["-stream_loop", "-1"] if item.loop else []) + ["-i", str(item.path)]
        filters.append(f"[{index}:a:0]{STEREO},volume={item.volume:.4f},apad,atrim=duration={seconds(duration_ms)},asetpts=PTS-STARTPTS[a{number}]")
    if len(audio) == 1:
        filters.append("[a0]anull[audio]")
    else:
        filters.append("".join(f"[a{i}]" for i in range(len(audio)))
                       + f"amix=inputs={len(audio)}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.98:level=0:latency=1[audio]")
    argv += ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[audio]",
             "-vf", f"scale={video.width}:{video.height},setsar=1,fps={video.fps},format=yuv420p,setpts=PTS-STARTPTS",
             "-t", seconds(duration_ms)]
    return argv + encoding(video, encoder) + [str(output)]


def concat_argv(ffmpeg: Path, parts: Sequence[str], video: Video, output: Path, *, encoder: str = ENCODER) -> list[str]:
    argv = start(ffmpeg)
    filters = []
    for index, name in enumerate(parts):
        argv += ["-i", f"{name}.mp4"]
        filters += [f"[{index}:v:0]setpts=PTS-STARTPTS[v{index}]", f"[{index}:a:0]asetpts=PTS-STARTPTS[a{index}]"]
    filters.append("".join(f"[v{i}][a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[joined][audio]")
    filters.append(f"[joined]fps={video.fps}[video]")
    return argv + ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]"] + encoding(video, encoder) + [str(output)]
