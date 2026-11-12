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


def part_samples(duration_ms: int) -> int:
    """The samples of a part's sound: its whole frames (a part is rendered on the frame grid)."""
    return max(1, (duration_ms * FPS + 500) // 1000) * FRAME_SAMPLES


def _joined_sound(parts: Sequence[str], durations_ms: Sequence[int], first_input: int) -> tuple[list[str], str]:
    """Inputs and the graph that join the parts' sound sample-exactly: each part's decoded sound cut to its own
    length (AAC adds priming and padding to every encoded part; joined as packets they add up, +21 to +32 ms a
    join, measured; P2-9), then encoded once."""
    argv: list[str] = []
    graph = []
    for index, (name, ms) in enumerate(zip(parts, durations_ms)):
        argv += ["-i", f"{name}.mp4"]
        n = part_samples(ms)  # never short either: a part's sound that ends early would move every later part's sound
        graph.append(f"[{first_input + index}:a:0]{STEREO},asetpts=N/SR/TB,apad=whole_len={n},atrim=end_sample={n},"
                     f"asetpts=PTS-STARTPTS[s{index}]")
    graph.append("".join(f"[s{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[audio]")
    return argv, ";".join(graph)


def concat_argv(ffmpeg: Path, parts: Sequence[str], output: Path, *, encoder: str = ENCODER,
                durations_ms: Sequence[int] | None = None) -> list[str]:
    """The encoded join (the fallback of :func:`concat_copy_argv`); with ``durations_ms`` each part's pictures and
    sound are cut to its own frames, so the sound never drifts at a join."""
    if durations_ms is None:
        argv = package_concat(ffmpeg, parts, VIDEO, output, encoder=encoder)
        argv[argv.index("-b:v") + 1] = "8M"
        return argv
    argv = start(ffmpeg)
    graph = []
    for index, (name, ms) in enumerate(zip(parts, durations_ms)):
        argv += ["-i", f"{name}.mp4"]
        frames = part_samples(ms) // FRAME_SAMPLES
        graph += [f"[{index}:v:0]setpts=PTS-STARTPTS,fps={FPS},trim=end_frame={frames},setpts=PTS-STARTPTS[v{index}]",
                  f"[{index}:a:0]{STEREO},asetpts=N/SR/TB,apad=whole_len={part_samples(ms)},atrim=end_sample={part_samples(ms)},"
                  f"asetpts=PTS-STARTPTS[a{index}]"]
    graph.append("".join(f"[v{i}][a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[video][audio]")
    return argv + ["-filter_complex", ";".join(graph), "-map", "[video]", "-map", "[audio]"] + encoding(encoder) + [str(output)]


def concat_list(parts: Sequence[str]) -> str:
    """An ffconcat list of the parts (``<name>.mp4`` beside the list)."""
    return "ffconcat version 1.0\n" + "".join(f"file '{name}.mp4'\n" for name in parts)


def concat_copy_argv(ffmpeg: Path, list_name: str, output: Path, *, parts: Sequence[str] = (),
                     durations_ms: Sequence[int] = ()) -> list[str]:
    """Join parts that share one encoding (``encoding()``): the pictures without encoding them again, rebased to
    start at 0 (the concat demuxer shifts B-frame video by its first negative dts, +21 ms measured), and — given the
    parts and their lengths — the sound joined sample-exactly and encoded once (P2-9)."""
    argv = start(ffmpeg) + ["-f", "concat", "-safe", "0", "-i", list_name]
    if not parts:
        return argv + ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(output)]
    inputs, graph = _joined_sound(parts, durations_ms, 1)
    return argv + inputs + ["-filter_complex", graph, "-map", "0:v:0", "-map", "[audio]", "-c:v", "copy",
                            "-bsf:v", "setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS",
                            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-f", "mp4",
                            str(output)]


FRAME_SAMPLES = 48000 // FPS  # audio samples in one frame of the programme (48 kHz, FPS frames a second)
LEAD_MS = 200                  # source read past a piece's end, so its last frame is never missing


def frame_counts(lengths_ms: Sequence[int]) -> list[int]:
    """Frames of each piece of a part on one grid: piece k covers frames round(start_k × FPS) to round(end_k × FPS)
    (at least one), so the cumulative error to the planned cuts stays within half a frame (P2-9)."""
    out, at, last = [], 0, 0
    for length in lengths_ms:
        at += length
        edge = max(last + 1, (at * FPS + 500) // 1000)
        out.append(edge - last)
        last = edge
    return out


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
    # Every cut on one frame grid (P2-9): piece k ends on frame round(end_k × FPS), so the pictures never drift from
    # the plan (and the narration) by more than half a frame, however many pieces; its sound is exactly as long as
    # its pictures (FRAME_SAMPLES a frame), padded to the sample: loudnorm drops up to 100 ms at the end of a
    # piece of 3 s or more, which put the source sound of every later piece early (−61 to −77 ms, measured).
    frames = frame_counts([p[3] for p in pieces])
    for index, (source, video, start_ms, length_ms, mode, has_audio, still) in enumerate(pieces):
        count = frames[index]
        length = seconds(count * 1000 // FPS + 1)
        samples = count * FRAME_SAMPLES
        if still:
            argv += ["-ss", seconds(start_ms), "-t", "0.5", "-i", str(source)]  # one frame, held
        else:
            argv += ["-ss", seconds(start_ms), "-t", seconds(count * 1000 // FPS + LEAD_MS), "-i", str(source)]
        g = geometry(video.width, video.height, layout)
        hold = f"trim=end_frame=1,loop=loop=-1:size=1,trim=duration={length}," if still else ""
        # The same fit as clip_argv: a blurred fill behind the whole, unstretched picture. A source that ends a
        # frame early repeats its last frame for that frame, never the next piece's.
        graph.append(f"[{index}:v]{hold}fps={FPS},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=2,split=2[b{index}][f{index}];"
                     f"[b{index}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},{blur},setsar=1[bg{index}];"
                     f"[f{index}]scale={g.width}:{g.height},setsar=1[fg{index}];"
                     f"[bg{index}][fg{index}]overlay={g.x}:{g.y},format=yuv420p,trim=end_frame={count},setpts=PTS-STARTPTS[v{index}]")
        gain = {"source": 1.0, "mixed": UNDER_NARRATION}.get(mode, 0.0)
        if has_audio and gain > 0 and not still:
            graph.append(f"[{index}:a:0]{STEREO},{LEVEL},volume={gain:.3f},asetpts=N/SR/TB,apad=whole_len={samples},"
                         f"atrim=end_sample={samples},asetpts=PTS-STARTPTS[a{index}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=end_sample={samples},asetpts=PTS-STARTPTS[a{index}]")
    count = len(pieces)
    # the part itself is a whole number of frames, its sound exactly as long: a part whose sound ends before its
    # pictures moves every later part's sound earlier at the join (P2-9)
    total_frames = max(1, (duration_ms * FPS + 500) // 1000)
    total_samples = total_frames * FRAME_SAMPLES
    total = f"{total_frames / FPS:.6f}"
    joined = "".join(f"[v{i}][a{i}]" for i in range(count))
    graph.append(f"{joined}concat=n={count}:v=1:a=1[pic][bed]")
    fades = (f",fade=t=in:st=0:d={FADE_S},fade=t=out:st={max(0.0, duration_ms / 1000 - FADE_S):.3f}:d={FADE_S}" if fade else "")
    graph.append(f"[pic]subtitles={ass}{fades},format=yuv420p[video]")
    mix = ["[bed]"]
    inputs = count
    if narration is not None:
        argv += ["-i", str(narration)]
        graph.append(f"[{inputs}:a]{STEREO},asetpts=N/SR/TB,apad=whole_len={total_samples},atrim=end_sample={total_samples}[voice]")
        mix.append("[voice]")
        inputs += 1
    if music is not None:
        argv += ["-stream_loop", "-1", "-i", str(music)]
        graph.append(f"[{inputs}:a]{STEREO},volume={volume:.4f},asetpts=N/SR/TB,apad=whole_len={total_samples},"
                     f"atrim=end_sample={total_samples}[music]")
        mix.append("[music]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,"
                 f"alimiter=limit=0.98:level=0:latency=1,asetpts=N/SR/TB,apad=whole_len={total_samples},"
                 f"atrim=end_sample={total_samples}[audio]")
    return (argv + ["-filter_complex", ";".join(graph), "-map", "[video]", "-map", "[audio]", "-t", total]
            + encoding(encoder) + [str(output)])
