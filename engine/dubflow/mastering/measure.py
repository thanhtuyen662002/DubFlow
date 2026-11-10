"""Measurements a mastering planner decides from (ADR-0055): loudness of a mix and the picture of sampled frames.

LGPL FFmpeg filters only (``loudnorm`` analysis; frames decoded to small grey rasters measured in Python), no
model, no GPU, no AI. Every function takes the FFmpeg path and an optional ``capture`` (argv → (stdout, stderr))
so tests can feed recorded answers.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

Capture = Callable[[list[str]], tuple[bytes, bytes]]
GREY_W, GREY_H = 160, 90
TIMEOUT_S = 1800


class MeasureError(RuntimeError):
    pass


def _capture(argv: list[str]) -> tuple[bytes, bytes]:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MeasureError(f"FFmpeg did not run: {type(error).__name__}") from error
    if done.returncode:
        raise MeasureError(f"FFmpeg failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[-300:]}")
    return done.stdout, done.stderr


@dataclass(frozen=True)
class Loudness:
    integrated: float      # LUFS
    true_peak: float       # dBTP
    lra: float             # LU
    threshold: float       # LUFS
    offset: float          # LU

    def to_dict(self) -> dict[str, float]:
        return {"integrated": self.integrated, "true_peak": self.true_peak, "lra": self.lra, "threshold": self.threshold,
                "offset": self.offset}


def loudness(ffmpeg: Path, audio: Path, *, target: float = -16.0, peak: float = -1.5, lra: float = 11.0,
             capture: Capture = _capture) -> Loudness | None:
    """``loudnorm``'s first pass over ``audio``; None for silence (nothing to normalise)."""
    _, err = capture([str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(audio), "-map", "0:a:0",
                      "-af", f"loudnorm=I={target}:TP={peak}:LRA={lra}:print_format=json", "-f", "null", "-"])
    text = err.decode("utf-8", "replace")
    found = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if not found:
        raise MeasureError("loudnorm printed no measurement")
    value = json.loads(found[-1])
    try:
        numbers = {key: float(value[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, ValueError, TypeError):
        return None  # "-inf": silence
    if any(abs(v) == float("inf") or v != v for v in numbers.values()):
        return None
    return Loudness(numbers["input_i"], numbers["input_tp"], numbers["input_lra"], numbers["input_thresh"],
                    numbers["target_offset"])


def frame_times(duration_ms: int, count: int) -> list[int]:
    """``count`` instants spread over the video, away from its first and last 3 %."""
    if duration_ms <= 0 or count <= 0:
        return []
    start, end = duration_ms * 3 // 100, duration_ms * 97 // 100
    step = (end - start) / count
    return [int(start + step * (i + 0.5)) for i in range(count)]


def grey_frames(ffmpeg: Path, video: Path, times_ms: Sequence[int], *, capture: Capture = _capture) -> list[bytes]:
    """One GREY_W×GREY_H 8-bit grey frame per instant (frames that cannot be decoded are left out)."""
    frames = []
    for at in times_ms:
        try:
            out, _ = capture([str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{at / 1000:.3f}", "-i", str(video),
                              "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={GREY_W}:{GREY_H}:flags=area,format=gray",
                              "-f", "rawvideo", "-"])
        except MeasureError:
            continue
        if len(out) == GREY_W * GREY_H:
            frames.append(out)
    return frames


@dataclass(frozen=True)
class Picture:
    """Luma of the sampled frames (video levels, 16–235 nominal) and dark bars around the picture."""

    frames: int
    low: int        # 0.5th percentile
    high: int       # 99.5th percentile
    mean: int
    bars: tuple[int, int, int, int]  # top, bottom, left, right in GREY_W×GREY_H pixels, dark on every frame

    def to_dict(self) -> dict[str, object]:
        return {"frames": self.frames, "low": self.low, "high": self.high, "mean": self.mean, "bars": list(self.bars)}


BAR_LEVEL = 24  # a row/column whose every pixel is at most this on every frame is a bar


def picture(frames: Sequence[bytes]) -> Picture | None:
    if not frames:
        return None
    histogram = [0] * 256
    total = 0
    for frame in frames:
        for value in frame:
            histogram[value] += 1
        total += len(frame)

    def percentile(share: float) -> int:
        wanted, seen = total * share, 0
        for level, count in enumerate(histogram):
            seen += count
            if seen >= wanted:
                return level
        return 255

    mean = sum(level * count for level, count in enumerate(histogram)) // total

    def dark_row(y: int) -> bool:
        return all(max(frame[y * GREY_W:(y + 1) * GREY_W]) <= BAR_LEVEL for frame in frames)

    def dark_column(x: int) -> bool:
        return all(max(frame[x::GREY_W]) <= BAR_LEVEL for frame in frames)

    top = next((y for y in range(GREY_H // 2) if not dark_row(y)), GREY_H // 2)
    bottom = next((y for y in range(GREY_H // 2) if not dark_row(GREY_H - 1 - y)), GREY_H // 2)
    left = next((x for x in range(GREY_W // 2) if not dark_column(x)), GREY_W // 2)
    right = next((x for x in range(GREY_W // 2) if not dark_column(GREY_W - 1 - x)), GREY_W // 2)
    return Picture(len(frames), percentile(0.005), percentile(0.995), mean, (top, bottom, left, right))


def loudest_window(wav: Path, *, window_ms: int = 5000, step_ms: int = 500, inside: tuple[float, float] = (0.2, 0.9)) -> dict | None:
    """The ``window_ms`` of ``wav`` (mono or stereo 16-bit PCM) with the most sound energy, between ``inside`` shares of
    its length: a candidate climax for a teaser *suggestion* (ADR-0055). None when the file is silent or too short."""
    import wave
    from array import array
    import sys

    try:
        reader = wave.open(str(wav), "rb")
    except (OSError, EOFError, wave.Error):
        return None
    with reader:
        if reader.getsampwidth() != 2:
            return None
        rate, channels = reader.getframerate(), reader.getnchannels()
        per_step = max(1, rate * step_ms // 1000)
        energy: list[float] = []
        while True:
            data = reader.readframes(per_step)
            if not data:
                break
            values = array("h")
            values.frombytes(data[:len(data) // 2 * 2])
            if sys.byteorder != "little":
                values.byteswap()
            energy.append(sum(v * v for v in values[::channels * 4]) / max(1, len(values[::channels * 4])))
    steps = window_ms // step_ms
    first, last = int(len(energy) * inside[0]), int(len(energy) * inside[1]) - steps
    if steps <= 0 or last <= first:
        return None
    total = sum(energy[first:first + steps])
    best, at = total, first
    for index in range(first + 1, last + 1):
        total += energy[index + steps - 1] - energy[index - 1]
        if total > best:
            best, at = total, index
    overall = sum(energy) / len(energy)
    if best <= 0 or overall <= 0:
        return None
    ratio = best / steps / overall
    return {"start_ms": at * step_ms, "end_ms": (at + steps) * step_ms, "energy_ratio_milli": round(ratio * 1000),
            "reasons": [f"đoạn {window_ms // 1000} giây có năng lượng âm thanh gốc cao nhất ({ratio:.1f}× trung bình)"]}


__all__ = ["Capture", "Loudness", "MeasureError", "Picture", "frame_times", "grey_frames", "loudest_window", "loudness", "picture"]
