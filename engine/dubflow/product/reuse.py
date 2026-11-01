"""Narration an earlier run already spoke, reused when a video is made again
from the editor's Mẫu mode (Issue #426) with the same script, voice and speed.

Each finished variant leaves ``narration-<n>.wav`` and, from this version on,
``narration-<n>.json`` (the sentences and how long each one speaks) in its
run's ``.dubflow-work``. Runs made before the sidecar existed are matched by
their ``spec.json`` and ``captions-<n>.srt``, whose cue times give the same
sentence lengths. Anything that does not match exactly is spoken again.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Sequence
import wave

from . import script

SCHEMA = 1
MAX_VARIANTS = 5
JSON_LIMIT = 256 * 1024
SRT_LIMIT = 1024 * 1024
_SRT_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")


@dataclass(frozen=True)
class Narration:
    """A narration WAV and each spoken sentence with its length in ms."""
    wav: Path
    spoken: tuple[tuple[str, int], ...]


def sidecar(work: Path, number: int) -> Path:
    return work / f"narration-{number}.json"


def write_sidecar(work: Path, number: int, *, voice_id: str, speed: float, text: str, sample_rate: int,
                  spoken: Sequence[tuple[str, int]]) -> None:
    """Record what ``narration-<number>.wav`` says, so a later run can reuse it."""
    value = {"schema_version": SCHEMA, "voice_id": voice_id, "speed": speed, "text": text.strip(), "sample_rate": sample_rate,
             "sentences": [[sentence, ms] for sentence, ms in spoken]}
    path = sidecar(work, number)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


def _json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > JSON_LIMIT:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _same_speed(value: Any, speed: float) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and abs(float(value) - speed) < 1e-9


def _wav_fits(path: Path, spoken: Sequence[tuple[str, int]], sample_rate: int | None) -> bool:
    """The WAV is mono 16-bit and as long as the sentences with their silences."""
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() <= 0:
                return False
            if sample_rate is not None and reader.getframerate() != sample_rate:
                return False
            frames = reader.getnframes()
            # A cut-off file still declares its full length.
            if len(reader.readframes(frames)) != frames * 2:
                return False
            actual_ms = frames * 1000 / reader.getframerate()
    except (OSError, EOFError, wave.Error):
        return False
    expected_ms = script.LEAD_MS + sum(ms for _, ms in spoken) + script.GAP_MS * (len(spoken) - 1) + script.TAIL_MS
    # Each sentence's length was rounded down to the millisecond.
    return expected_ms - 2 <= actual_ms <= expected_ms + len(spoken) + 2


def _from_sidecar(work: Path, number: int, wanted: list[str], text: str, voice_id: str, speed: float) -> Narration | None:
    value = _json(sidecar(work, number))
    if type(value) is not dict or value.get("schema_version") != SCHEMA or value.get("voice_id") != voice_id:
        return None
    if not _same_speed(value.get("speed"), speed) or value.get("text") != text.strip():
        return None
    rate, sentences = value.get("sample_rate"), value.get("sentences")
    if type(rate) is not int or rate <= 0 or type(sentences) is not list or len(sentences) != len(wanted):
        return None
    spoken = []
    for item, sentence in zip(sentences, wanted):
        if type(item) is not list or len(item) != 2 or item[0] != sentence or type(item[1]) is not int or item[1] <= 0:
            return None
        spoken.append((sentence, item[1]))
    wav = work / f"narration-{number}.wav"
    return Narration(wav, tuple(spoken)) if _wav_fits(wav, spoken, rate) else None


def _srt_cues(path: Path) -> list[tuple[int, int, str]] | None:
    try:
        if not path.is_file() or path.stat().st_size > SRT_LIMIT:
            return None
        blocks = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip().split("\n\n")
    except (OSError, ValueError):
        return None
    cues = []
    for block in blocks:
        lines = block.split("\n")
        match = _SRT_TIME.match(lines[1].strip()) if len(lines) >= 3 else None
        if not match:
            return None
        h1, m1, s1, f1, h2, m2, s2, f2 = (int(part) for part in match.groups())
        cues.append(((h1 * 3600 + m1 * 60 + s1) * 1000 + f1, (h2 * 3600 + m2 * 60 + s2) * 1000 + f2, "\n".join(lines[2:]).strip()))
    return cues


def _from_captions(run_dir: Path, wanted: list[str], text: str, voice_id: str, speed: float) -> Narration | None:
    """A run from before the sidecar: its spec says what was spoken, its captions how long each sentence took."""
    spec = _json(run_dir / ".dubflow-work" / "spec.json")
    if type(spec) is not dict or spec.get("schema_version") != 1 or spec.get("voice_id") != voice_id:
        return None
    if not _same_speed(spec.get("speed", 1.0), speed) or type(spec.get("variants")) is not list:
        return None
    for index, variant in enumerate(spec["variants"][:MAX_VARIANTS], start=1):
        if type(variant) is not str or variant.strip() != text.strip():
            continue
        cues = _srt_cues(run_dir / f"captions-{index}.srt")
        if cues is None:
            continue
        spoken, cursor, position, ok = [], script.LEAD_MS, 0, True
        for sentence in wanted:
            chunks = script.caption_chunks(sentence)
            part = cues[position:position + len(chunks)]
            if len(part) != len(chunks) or [cue[2] for cue in part] != chunks or part[0][0] != cursor:
                ok = False
                break
            duration = part[-1][1] - part[0][0]
            if duration <= 0:
                ok = False
                break
            spoken.append((sentence, duration))
            position += len(chunks)
            cursor += duration + script.GAP_MS
        wav = run_dir / ".dubflow-work" / f"narration-{index}.wav"
        if ok and position == len(cues) and _wav_fits(wav, spoken, None):
            return Narration(wav, tuple(spoken))
    return None


def find(run_dir: Path, text: str, voice_id: str, speed: float) -> Narration | None:
    """The narration of ``text`` in ``voice_id`` at ``speed`` that ``run_dir`` already holds, if any."""
    wanted = script.sentences(text)
    if not wanted:
        return None
    work = run_dir / ".dubflow-work"
    for number in range(1, MAX_VARIANTS + 1):
        found = _from_sidecar(work, number, wanted, text, voice_id, speed)
        if found is not None:
            return found
    return _from_captions(run_dir, wanted, text, voice_id, speed)


def copy(narration: Narration, target: Path) -> None:
    """Copy the WAV into this run, so the new run stands alone."""
    partial = target.with_name(target.name + ".partial")
    shutil.copyfile(narration.wav, partial)
    os.replace(partial, target)
