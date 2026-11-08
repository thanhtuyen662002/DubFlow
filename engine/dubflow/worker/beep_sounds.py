"""The sound laid over a flagged word ("Từ nhạy cảm" → bíp).

``beep`` is the classic 1 kHz tone. ``bird`` (chim hót) and ``dog`` (chó sủa)
are synthesised here, so nothing is downloaded and no recording's licence is
involved: a bird is a run of short falling whistles, a dog a pair of barks
built from a rough glottal buzz through a mouth opening from "w" to "a". ``custom``
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


def _resonator(centre: float, width: float, rate: int) -> tuple[float, float, float]:
    """A two-pole band-pass at ``centre`` Hz, ``width`` Hz wide: (gain, a1, a2)."""
    radius = math.exp(-math.pi * width / rate)
    return 1.0 - radius, 2.0 * radius * math.cos(2.0 * math.pi * centre / rate), -radius * radius


def _glide(t: float, points: tuple[tuple[float, float], ...]) -> float:
    """A value moving in straight lines through (time, value) points."""
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def _bark(rate: int, seconds: float, peak_hz: float, seed: int) -> list[float]:
    """One "gâu": a rough, breathy glottal buzz whose pitch leaps up, through a mouth that
    opens from "w" to "a" (F1 and F2 rise in the first 22 ms), then a quick fall. Alternate
    periods are weaker (period doubling) and the pitch wobbles: what makes a bark harsh
    rather than sung. Tuned until an audio classifier (BirdNET's "Dog" class) hears a dog."""
    rng = _Lcg(seed)
    count = int(seconds * rate)
    pitch = ((0.0, 0.5), (min(0.06, seconds * 0.4), 1.0), (max(seconds * 0.6, 0.07), 0.67), (seconds, 0.79))
    f1 = ((0.0, 326.0), (0.022, 1157.0), (seconds, 1100.0))
    f2 = ((0.0, 944.0), (0.022, 1625.0), (seconds, 2156.0))
    fixed = ((3055.0, 320.0, 0.53), (5500.0, 500.0, 0.17))
    states = [[0.0, 0.0] for _ in range(4)]
    out: list[float] = []
    cycle = 0.0
    periods = 0
    wobble = 0.0
    previous = 0.0
    hold = seconds * 0.34
    for frame in range(count):
        t = frame / rate
        wobble += (0.26 * (rng.random() - 0.5) - wobble) * (25.0 / rate)
        cycle += peak_hz * _glide(t, pitch) * (1.0 + 7.0 * wobble) / rate
        if cycle >= 1.0:
            cycle -= 1.0
            periods += 1
        # Rosenberg glottal pulse; its slope excites the tract (rich in harmonics).
        if cycle < 0.4:
            pulse = 0.5 * (1.0 - math.cos(math.pi * cycle / 0.4))
        elif cycle < 0.56:
            pulse = math.cos(math.pi * (cycle - 0.4) / 0.32)
        else:
            pulse = 0.0
        slope = (pulse - previous) * rate / (peak_hz * 8.0)
        previous = pulse
        strength = 1.0 if periods % 2 == 0 else 0.78
        noise = (rng.random() * 2.0 - 1.0) * (0.36 + 0.64 * pulse)
        source = strength * slope + 1.28 * noise
        sample = 0.0
        bands = ((_glide(t, f1), 233.0, 1.0), (_glide(t, f2), 192.0, 1.17)) + fixed
        for state, (centre, width, gain) in zip(states, bands):
            g, a1, a2 = _resonator(centre, width, rate)
            value = g * source + a1 * state[0] + a2 * state[1]
            state[1], state[0] = state[0], value
            sample += gain * value
        attack = min(1.0, t / 0.015)
        tail = 1.0 if t < hold else math.exp(-(t - hold) / (seconds * 0.08))
        out.append(attack * tail * sample)
    peak = max(abs(value) for value in out) or 1.0
    return [0.55 * value / peak for value in out]


def dog_clip(rate: int) -> list[float]:
    """Two barks ("gâu gâu"): a long one, then a short higher one, then a pause."""
    out = _bark(rate, 0.3, 780.0, 1)
    out.extend([0.0] * int(0.2 * rate))
    out.extend(_bark(rate, 0.15, 830.0, 2))
    out.extend([0.0] * int(0.16 * rate))
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
