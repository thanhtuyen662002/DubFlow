"""Beep over flagged words in the Vietnamese voice ("Từ nhạy cảm" → Beep).

The voice is synthesised one phrase at a time and there are no word
timestamps, so each word is located from the phrase's own audio: the voiced
span of the phrase on the dialogue stem, divided by syllables (Vietnamese
syllables are close to even in length). The beep is padded on both sides, so
it covers the word even when the estimate is a little early or late, and the
voice under it already says the respelled word ("giớt"), never the original.

Beeped copies are written next to the mixer's files; the mixer's own outputs
(and their provenance hashes) are left untouched.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
import re
from typing import Any
import wave

from array import array

PAD_SECONDS = 0.12
MIN_BEEP_SECONDS = 0.30
TONE_HZ = 1000.0
TONE_LEVEL = 0.25  # about -12 dBFS
FADE_SECONDS = 0.008
FRAME_SECONDS = 0.02
RECIPE = "beep-syllable-estimate-v1"


def _syllables(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in re.finditer(r"[^\s,.!?…;:\"“”()\-–—]+", text)]


def word_fractions(text: str, spans: Sequence[tuple[int, int, str]]) -> list[tuple[float, float]]:
    """Where each flagged span sits in ``text``, as (start, end) fractions of its syllables."""
    syllables = _syllables(text)
    if not syllables:
        return []
    total = len(syllables)
    result = []
    for start, end, _ in spans:
        inside = [index for index, (left, right) in enumerate(syllables) if left < end and right > start]
        if inside:
            result.append((inside[0] / total, (inside[-1] + 1) / total))
    return result


def _levels(reader: wave.Wave_read, start: float, end: float) -> tuple[int, list[float]]:
    channels, rate = reader.getnchannels(), reader.getframerate()
    first, last = max(0, int(start * rate)), min(reader.getnframes(), int(end * rate))
    if last <= first:
        return first, []
    reader.setpos(first)
    samples = array("h")
    samples.frombytes(reader.readframes(last - first))
    step = max(1, int(FRAME_SECONDS * rate)) * channels
    levels = [math.sqrt(sum(value * value for value in samples[index:index + step]) / len(samples[index:index + step]))
              for index in range(0, len(samples), step)]
    return first, levels


def voiced_span(reader: wave.Wave_read, start: float, end: float) -> tuple[float, float] | None:
    """The first and last voiced 20 ms frame between ``start`` and ``end`` seconds of a 16-bit WAV."""
    rate = reader.getframerate()
    first, levels = _levels(reader, start, end)
    if not levels or max(levels) < 200:
        return None
    threshold = max(levels) * 0.1
    voiced = [index for index, level in enumerate(levels) if level >= threshold]
    step = max(1, int(FRAME_SECONDS * rate))
    return (first + voiced[0] * step) / rate, (first + (voiced[-1] + 1) * step) / rate


def beep_spans(windows: Sequence[Mapping[str, Any]], texts: Mapping[str, str], flagged: Mapping[str, Sequence[tuple[int, int, str]]],
               stem: Path) -> list[tuple[float, float]]:
    """Seconds to beep. ``windows``: phrase id, start and end seconds; ``texts``/``flagged``: the phrase's text and flagged spans."""
    result = []
    with wave.open(str(stem), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM can be beeped")
        result = _estimate(reader, windows, texts, flagged)
    result.sort()
    merged: list[tuple[float, float]] = []
    for begin, finish in result:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged


def _estimate(reader: wave.Wave_read, windows: Sequence[Mapping[str, Any]], texts: Mapping[str, str],
              flagged: Mapping[str, Sequence[tuple[int, int, str]]]) -> list[tuple[float, float]]:
    result = []
    for window in windows:
        phrase = window["id"]
        spans = flagged.get(phrase)
        if not spans:
            continue
        voiced = voiced_span(reader, window["start"], window["end"]) or (window["start"], window["end"])
        length = voiced[1] - voiced[0]
        for left, right in word_fractions(texts[phrase], spans):
            begin, finish = voiced[0] + left * length - PAD_SECONDS, voiced[0] + right * length + PAD_SECONDS
            if finish - begin < MIN_BEEP_SECONDS:
                middle = (begin + finish) / 2
                begin, finish = middle - MIN_BEEP_SECONDS / 2, middle + MIN_BEEP_SECONDS / 2
            result.append((max(0.0, begin), finish))
    return result


def write_beeped(source: Path, target: Path, spans: Sequence[tuple[float, float]], *, block_frames: int = 1 << 16) -> Path:
    """A copy of ``source`` with a tone replacing every span (short fades, so no clicks), streamed in blocks."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp.wav")
    with wave.open(str(source), "rb") as reader, wave.open(str(temporary), "wb") as writer:
        params = reader.getparams()
        if params.sampwidth != 2:
            raise ValueError("only 16-bit PCM can be beeped")
        writer.setparams(params)
        channels, rate = params.nchannels, params.framerate
        fade = max(1, int(FADE_SECONDS * rate))
        frames = [(max(0, int(begin * rate)), min(params.nframes, int(finish * rate))) for begin, finish in spans]
        position = 0
        while position < params.nframes:
            samples = array("h")
            samples.frombytes(reader.readframes(block_frames))
            count = len(samples) // channels
            if not count:
                break
            for first, last in frames:
                for frame in range(max(first, position), min(last, position + count)):
                    offset, length = frame - first, last - first
                    ramp = min(1.0, offset / fade, (length - offset) / fade)
                    tone = 32767 * TONE_LEVEL * ramp * math.sin(2 * math.pi * TONE_HZ * offset / rate)
                    for channel in range(channels):
                        index = (frame - position) * channels + channel
                        samples[index] = max(-32768, min(32767, int(samples[index] * (1.0 - ramp) + tone)))
            writer.writeframes(samples.tobytes())
            position += count
    temporary.replace(target)
    return target
