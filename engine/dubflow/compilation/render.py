"""Normalized FFmpeg segments; music is an input only to narrated cards."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Sequence

from engine.dubflow.package.render import STEREO, Video, concat_argv as package_concat, seconds, start
from .layout import FPS, HEIGHT, WIDTH, geometry

ENCODER = "h264_mf"
VIDEO = Video(WIDTH, HEIGHT, str(FPS))


def available_blur(ffmpeg: Path) -> str:
    """The shipped LGPL runtime omits boxblur; match package's Gaussian fallback."""
    result = subprocess.run([str(ffmpeg), "-hide_banner", "-filters"], capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Không đọc được bộ lọc FFmpeg")
    return "boxblur=20:2" if b" boxblur " in result.stdout else "gblur=sigma=20"


def encoding(encoder: str) -> list[str]:
    return ["-c:v", encoder, "-b:v", "8M", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-f", "mp4"]


def blurred_fill(source: str = "0:v", blur: str = "boxblur=20:2") -> str:
    # Only the background is filled/cropped; the foreground keeps every source edge.
    return (f"[{source}]split=2[back][front];"
            f"[back]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
            f"{blur},setsar=1[bg];")


def clip_argv(ffmpeg: Path, source: Path, video: Video, layout: str, start_ms: int, duration_ms: int,
              ass: str, output: Path, *, encoder: str = ENCODER, blur: str = "boxblur=20:2") -> list[str]:
    g = geometry(video.width, video.height, layout)
    graph = (blurred_fill(blur=blur) + f"[front]scale={g.width}:{g.height},setsar=1[fg];"
             f"[bg][fg]overlay={g.x}:{g.y},fps={FPS},format=yuv420p,setpts=PTS-STARTPTS,subtitles={ass}[video];"
             f"[0:a:0]{STEREO},apad,atrim=duration={seconds(duration_ms)},asetpts=PTS-STARTPTS[audio]")
    return (start(ffmpeg) + ["-ss", seconds(start_ms), "-i", str(source), "-filter_complex", graph,
            "-map", "[video]", "-map", "[audio]", "-t", seconds(duration_ms)] + encoding(encoder) + [str(output)])


def card_argv(ffmpeg: Path, image: Path, narration: Path, music: Path | None, volume: float,
              duration_ms: int, ass: str, output: Path, *, encoder: str = ENCODER, blur: str = "boxblur=20:2") -> list[str]:
    argv = start(ffmpeg) + ["-loop", "1", "-framerate", str(FPS), "-i", str(image), "-i", str(narration)]
    graph = [f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
             f"setsar=1,{blur},drawbox=color=black@0.4:t=fill,subtitles={ass},format=yuv420p[video]",
             f"[1:a]{STEREO},apad,atrim=duration={seconds(duration_ms)}[voice]"]
    if music is not None:
        argv += ["-stream_loop", "-1", "-i", str(music)]
        graph += [f"[2:a]{STEREO},volume={volume:.4f},apad,atrim=duration={seconds(duration_ms)}[music]",
                  "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                  "alimiter=limit=0.98:level=0:latency=1[audio]"]
    else:
        graph.append("[voice]anull[audio]")
    return (argv + ["-filter_complex", ";".join(graph), "-map", "[video]", "-map", "[audio]",
                    "-t", seconds(duration_ms)] + encoding(encoder) + [str(output)])


def concat_argv(ffmpeg: Path, parts: Sequence[str], output: Path, *, encoder: str = ENCODER) -> list[str]:
    argv = package_concat(ffmpeg, parts, VIDEO, output, encoder=encoder)
    argv[argv.index("-b:v") + 1] = "8M"
    return argv
