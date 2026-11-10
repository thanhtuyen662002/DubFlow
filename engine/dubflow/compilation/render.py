"""Normalized FFmpeg segments; music is an input only to narrated cards."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Sequence

from engine.dubflow.package.render import STEREO, Video, concat_argv as package_concat, seconds, start
from .layout import FPS, HEIGHT, WIDTH, geometry

ENCODER = "h264_mf"
# Clips from different videos play at one loudness (their sources differ by 10 dB or more).
LEVEL = "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
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
             f"[0:a:0]{STEREO},{LEVEL},apad,atrim=duration={seconds(duration_ms)},asetpts=PTS-STARTPTS[audio]")
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


# How loud a source plays under the narration (about −18 dB) and on its own moment.
UNDER_NARRATION = 0.12
FADE_S = 0.3


def montage_argv(ffmpeg: Path, pieces: Sequence[tuple], narration: Path | None, music: Path | None, volume: float,
                 duration_ms: int, ass: str, output: Path, *, layout: str, encoder: str = ENCODER,
                 blur: str = "boxblur=20:2", fade: bool = True) -> list[str]:
    """One programme part (ADR-0053 §6): its planned pieces of raw sources, its ASS, narration, source sound and music.

    ``pieces`` are (source path, Video, start ms, length ms, audio mode, has audio, still) in timeline order. Each
    piece gets the same fit as ``clip_argv`` (blurred fill behind the whole picture); a still holds the frame at its
    start. The source sound plays at full level on its own moment (``source``), quietly under the narration
    (``mixed``), or not at all. One encode for the whole part.
    """
    argv = start(ffmpeg)
    graph: list[str] = []
    for index, (source, video, start_ms, length_ms, mode, has_audio, still) in enumerate(pieces):
        length = seconds(length_ms)
        if still:
            argv += ["-ss", seconds(start_ms), "-t", "0.5", "-i", str(source)]  # one frame, held
        else:
            argv += ["-ss", seconds(start_ms), "-t", length, "-i", str(source)]
        g = geometry(video.width, video.height, layout)
        hold = f"trim=end_frame=1,loop=loop=-1:size=1,trim=duration={length}," if still else ""
        # The same fit as clip_argv: a blurred fill behind the whole, unstretched picture.
        graph.append(f"[{index}:v]{hold}fps={FPS},setpts=PTS-STARTPTS,split=2[b{index}][f{index}];"
                     f"[b{index}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},{blur},setsar=1[bg{index}];"
                     f"[f{index}]scale={g.width}:{g.height},setsar=1[fg{index}];"
                     f"[bg{index}][fg{index}]overlay={g.x}:{g.y},format=yuv420p,trim=duration={length},setpts=PTS-STARTPTS[v{index}]")
        gain = {"source": 1.0, "mixed": UNDER_NARRATION}.get(mode, 0.0)
        if has_audio and gain > 0 and not still:
            graph.append(f"[{index}:a:0]{STEREO},{LEVEL},volume={gain:.3f},apad,atrim=duration={length},asetpts=PTS-STARTPTS[a{index}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length},asetpts=PTS-STARTPTS[a{index}]")
    count = len(pieces)
    total = seconds(duration_ms)
    joined = "".join(f"[v{i}][a{i}]" for i in range(count))
    graph.append(f"{joined}concat=n={count}:v=1:a=1[pic][bed]")
    fades = (f",fade=t=in:st=0:d={FADE_S},fade=t=out:st={max(0.0, duration_ms / 1000 - FADE_S):.3f}:d={FADE_S}" if fade else "")
    graph.append(f"[pic]subtitles={ass}{fades},format=yuv420p[video]")
    mix = ["[bed]"]
    inputs = count
    if narration is not None:
        argv += ["-i", str(narration)]
        graph.append(f"[{inputs}:a]{STEREO},apad,atrim=duration={total}[voice]")
        mix.append("[voice]")
        inputs += 1
    if music is not None:
        argv += ["-stream_loop", "-1", "-i", str(music)]
        graph.append(f"[{inputs}:a]{STEREO},volume={volume:.4f},apad,atrim=duration={total}[music]")
        mix.append("[music]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,"
                 f"alimiter=limit=0.98:level=0:latency=1,atrim=duration={total}[audio]")
    return (argv + ["-filter_complex", ";".join(graph), "-map", "[video]", "-map", "[audio]", "-t", total]
            + encoding(encoder) + [str(output)])
