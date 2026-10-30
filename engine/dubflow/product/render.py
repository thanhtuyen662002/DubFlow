"""Visual segments and the FFmpeg command that renders one product video."""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
import os
from pathlib import Path
import sys
from typing import Sequence
import wave

from .layout import FPS, HEIGHT, MEDIA_HEIGHT, PANEL_COLOR, WIDTH

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
MUSIC_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"})
MAX_SEGMENT_MS = 5000
ENCODER = "h264_mf"
# Photos are scaled past the frame so a slow pan has room to move.
_PAN_SCALE = 1.12


@dataclass(frozen=True)
class Visual:
    path: Path
    # A clip's length; None for a photo.
    duration_ms: int | None = None


@dataclass(frozen=True)
class Segment:
    visual: Visual
    offset_ms: int
    duration_ms: int


def plan(visuals: Sequence[Visual], total_ms: int) -> list[Segment]:
    """Equal segments of at most 5 s, the visuals in order and repeated as needed.

    Each reuse of a clip continues where its last segment stopped.
    """
    if not visuals or total_ms <= 0:
        raise ValueError("a product video needs visuals and a positive length")
    count = max(len(visuals), math.ceil(total_ms / MAX_SEGMENT_MS))
    offsets = {visual: 0 for visual in visuals}
    segments, used = [], 0
    for index in range(count):
        visual = visuals[index % len(visuals)]
        duration = total_ms * (index + 1) // count - used
        used += duration
        offset = offsets[visual]
        if visual.duration_ms is not None:
            if offset + duration > visual.duration_ms:
                offset = 0
            offsets[visual] = offset + duration
        segments.append(Segment(visual, offset, duration))
    return segments


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def ffmpeg_argv(ffmpeg: Path, segments: Sequence[Segment], narration: Path, music: Path | None,
                music_volume: float, total_ms: int, output: Path, *, subtitles: str = "captions.ass",
                encoder: str = ENCODER) -> list[str]:
    """The FFmpeg command, run with the folder holding ``subtitles`` as its working directory."""
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]
    filters = []
    for index, segment in enumerate(segments):
        seconds = _seconds(segment.duration_ms)
        if segment.visual.duration_ms is None:
            argv += ["-loop", "1", "-framerate", str(FPS), "-t", seconds, "-i", str(segment.visual.path)]
            travel = f"(iw-{WIDTH})*t/{seconds}" if index % 2 == 0 else f"(iw-{WIDTH})*(1-t/{seconds})"
            fit = (f"scale={round(WIDTH * _PAN_SCALE)}:{round(MEDIA_HEIGHT * _PAN_SCALE)}:force_original_aspect_ratio=increase,"
                   f"crop={WIDTH}:{MEDIA_HEIGHT}:x='{travel}':y='(ih-{MEDIA_HEIGHT})/2'")
        else:
            argv += ["-stream_loop", "-1", "-ss", _seconds(segment.offset_ms), "-t", seconds, "-i", str(segment.visual.path)]
            fit = f"scale={WIDTH}:{MEDIA_HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{MEDIA_HEIGHT}"
        filters.append(f"[{index}:v]{fit},setsar=1,fps={FPS},format=yuv420p,trim=duration={seconds},setpts=PTS-STARTPTS[v{index}]")
    voice = len(segments)
    argv += ["-i", str(narration)]
    total = _seconds(total_ms)
    filters.append("".join(f"[v{index}]" for index in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[media]")
    filters.append(f"color=c={PANEL_COLOR}:s={WIDTH}x{HEIGHT}:r={FPS}:d={total}[base]")
    filters.append(f"[base][media]overlay=0:0:eof_action=repeat,subtitles={subtitles}[video]")
    stereo = "aformat=sample_rates=48000:channel_layouts=stereo"
    if music is None:
        filters.append(f"[{voice}:a]{stereo}[audio]")
    else:
        argv += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(f"[{voice}:a]{stereo}[voice]")
        filters.append(f"[{voice + 1}:a]{stereo},volume={music_volume:.2f}[bed]")
        filters.append("[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[audio]")
    argv += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", total,
             "-c:v", encoder, "-quality", "90", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-f", "mp4", str(output)]
    return argv


def write_narration(path: Path, speech: Sequence[Sequence[float]], sample_rate: int,
                    *, lead_ms: int, gap_ms: int, tail_ms: int) -> None:
    """Mono 16-bit WAV: the sentences in order, with silence before, between and after."""
    def silence(milliseconds: int) -> bytes:
        return bytes(2 * (sample_rate * milliseconds // 1000))

    def pcm(samples: Sequence[float]) -> bytes:
        values = array("h", (max(-32768, min(32767, round(sample * 32767))) for sample in samples))
        if sys.byteorder != "little":
            values.byteswap()
        return values.tobytes()

    partial = path.with_name(path.name + ".partial")
    with wave.open(str(partial), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(silence(lead_ms))
        for index, samples in enumerate(speech):
            if index:
                output.writeframes(silence(gap_ms))
            output.writeframes(pcm(samples))
        output.writeframes(silence(tail_ms))
    os.replace(partial, path)
