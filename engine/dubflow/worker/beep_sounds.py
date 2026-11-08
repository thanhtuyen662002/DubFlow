"""The sound laid over a flagged word ("Từ nhạy cảm" → bíp).

``beep`` is the classic 1 kHz tone. ``bird`` (chim hót) is synthesised here: a
run of short falling whistles. ``dog`` (chó sủa) is a real CC0 recording of two
barks, embedded in dog_bark_data.py so nothing is downloaded. ``custom``
is a sound file the user picked in Cài đặt, decoded with the bundled FFmpeg.

Every sound is a mono clip of floats in -1..1 at the stem's sample rate; a
span longer than the clip repeats it, a shorter one takes its beginning. The
same recipes are mirrored in apps/desktop/src/features/ai/beep_sound.ts for
"Nghe thử" in Cài đặt.
"""
from __future__ import annotations

from array import array
import base64
import math
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from engine.dubflow.worker import dog_bark_data

SOUNDS = ("beep", "bird", "dog", "custom")
SOUND_LABELS = {"beep": "tiếng bíp", "bird": "tiếng chim hót", "dog": "tiếng chó sủa", "custom": "âm thanh tự chọn"}
MAX_CUSTOM_SECONDS = 10.0
CUSTOM_PEAK = 0.5
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")


class BeepSoundError(ValueError):
    pass


class _Lcg:
    """A tiny seeded generator, the same one beep_sound.ts uses, so both make the same sound."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def random(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % 2147483648
        return self.state / 2147483648


def bird_clip(rate: int) -> list[float]:
    """About 0.9 s of chirps: falling whistles of 60–90 ms with a little trill, then a pause."""
    rng = _Lcg(7)
    out: list[float] = []
    for index in range(6):
        length = 0.06 + 0.03 * rng.random()
        high, low = 5200 + 600 * rng.random(), 2900 + 500 * rng.random()
        count = int(length * rate)
        phase = 0.0
        for frame in range(count):
            t = frame / count
            freq = low + (high - low) * math.exp(-4.0 * t) + 180 * math.sin(2 * math.pi * 38 * frame / rate)
            phase += 2 * math.pi * freq / rate
            envelope = math.sin(math.pi * t) ** 0.6
            out.append(0.42 * envelope * (math.sin(phase) + 0.18 * math.sin(2 * phase)))
        gap = 0.035 if index % 3 != 2 else 0.11
        out.extend([0.0] * int(gap * rate))
    return out


def dog_clip(rate: int) -> list[float]:
    """The recorded bark ("gâu gâu"), resampled to ``rate`` in straight lines."""
    raw = array("h")
    raw.frombytes(base64.b64decode(dog_bark_data.PCM_BASE64))
    if sys.byteorder == "big":
        raw.byteswap()
    source = [value / 32768.0 for value in raw]
    count = len(source) * rate // dog_bark_data.RATE
    out = []
    for index in range(count):
        position = index * dog_bark_data.RATE / rate
        left = int(position)
        right = min(left + 1, len(source) - 1)
        out.append(source[left] + (source[right] - source[left]) * (position - left))
    return out


def custom_clip(path: Path, rate: int, ffmpeg: Path) -> list[float]:
    """The first 10 s of the user's file as mono floats, peak at -6 dBFS."""
    if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise BeepSoundError(f"không đọc được file âm thanh {path.name}")
    try:
        result = subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-t", str(MAX_CUSTOM_SECONDS), "-i", str(path),
                                 "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BeepSoundError(f"không giải mã được file âm thanh {path.name}") from error
    samples = array("f")
    samples.frombytes(result.stdout[:len(result.stdout) - len(result.stdout) % 4])
    if result.returncode or not samples:
        raise BeepSoundError(f"không giải mã được file âm thanh {path.name}")
    # Leading silence would leave the word audible: start at the first sound.
    peak = max(abs(value) for value in samples)
    if peak <= 1e-4:
        raise BeepSoundError(f"file âm thanh {path.name} không có tiếng")
    first = next(index for index, value in enumerate(samples) if abs(value) >= peak * 0.05)
    return [CUSTOM_PEAK * value / peak for value in samples[first:]]


def clip_for(sound: str, rate: int, *, sound_file: str | None = None, ffmpeg: Path | None = None) -> Sequence[float] | None:
    """The clip to lay over flagged words, or None for the plain tone."""
    if sound == "bird":
        return bird_clip(rate)
    if sound == "dog":
        return dog_clip(rate)
    if sound == "custom":
        if not sound_file or ffmpeg is None:
            raise BeepSoundError("chưa chọn file âm thanh thay tiếng bíp")
        return custom_clip(Path(sound_file), rate, ffmpeg)
    return None
