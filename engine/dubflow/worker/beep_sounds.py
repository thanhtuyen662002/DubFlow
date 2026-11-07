"""The sound laid over a flagged word ("Từ nhạy cảm" → bíp).

``beep`` is the classic 1 kHz tone. ``bird`` (chim hót) and ``dog`` (chó sủa)
are synthesised here, so nothing is downloaded and no recording's licence is
involved: a bird is a run of short falling whistles, a dog a pair of barks
built from a gliding voice with three formants and a breathy onset. ``custom``
is a sound file the user picked in Cài đặt, decoded with the bundled FFmpeg.

Every sound is a mono clip of floats in -1..1 at the stem's sample rate; a
span longer than the clip repeats it, a shorter one takes its beginning. The
same recipes are mirrored in apps/desktop/src/features/ai/beep_sound.ts for
"Nghe thử" in Cài đặt.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import subprocess
from typing import Sequence

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


def _bark(rate: int, seconds: float, start_hz: float, seed: int) -> list[float]:
    rng = _Lcg(seed)
    count = int(seconds * rate)
    formants = ((650.0, 220.0, 1.0), (1450.0, 260.0, 0.55), (2700.0, 400.0, 0.3))
    out: list[float] = []
    phase = 0.0
    noise = 0.0
    for frame in range(count):
        t = frame / rate
        f0 = start_hz * (0.72 + 0.28 * math.exp(-t / 0.05)) * (1.0 - 0.18 * t / seconds)
        phase += 2 * math.pi * f0 / rate
        voice = 0.0
        for harmonic in range(1, 14):
            freq = harmonic * f0
            if freq >= rate / 2:
                break
            weight = sum(gain * math.exp(-((freq - centre) / width) ** 2) for centre, width, gain in formants) + 0.04
            voice += weight / harmonic ** 0.5 * math.sin(harmonic * phase)
        noise = 0.6 * noise + 0.4 * (rng.random() * 2 - 1)
        attack = min(1.0, t / 0.006)
        envelope = attack * math.exp(-t / (seconds * 0.38))
        breath = math.exp(-t / 0.03)
        out.append(envelope * (0.32 * voice + 0.35 * breath * noise))
    peak = max(abs(value) for value in out) or 1.0
    return [0.5 * value / peak for value in out]


def dog_clip(rate: int) -> list[float]:
    """Two barks ("gâu gâu"), the second a little lower, then a pause."""
    out = _bark(rate, 0.2, 560.0, 1)
    out.extend([0.0] * int(0.09 * rate))
    out.extend(_bark(rate, 0.22, 500.0, 2))
    out.extend([0.0] * int(0.14 * rate))
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
