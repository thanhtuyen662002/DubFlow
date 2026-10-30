"""A narration script as spoken sentences, and captions timed to their speech."""
from __future__ import annotations

from dataclasses import dataclass
import re

# One VieNeu request; well under the native process's 512-character limit.
MAX_SPOKEN_CHARS = 180
# One caption: two lines at most in the template's panel.
MAX_CAPTION_CHARS = 48
LEAD_MS = 300
GAP_MS = 250
TAIL_MS = 700

_SENTENCE = re.compile(r"[^.!?…\n]+[.!?…]*")
_CLAUSE = re.compile(r"[^,;:]+[,;:]?")


@dataclass(frozen=True)
class Caption:
    start_ms: int
    end_ms: int
    text: str


def _words(text: str, limit: int) -> list[str]:
    """Greedy word wrap into pieces of at most ``limit`` characters."""
    pieces: list[str] = []
    current = ""
    for word in text.split():
        word = word[:limit]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def _bounded(text: str, limit: int) -> list[str]:
    """Split at clause punctuation first, then between words."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    current = ""
    for clause in (match.group().strip() for match in _CLAUSE.finditer(text)):
        if not clause:
            continue
        candidate = f"{current} {clause}" if current else clause
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(clause) <= limit:
            current = clause
        else:
            *whole, current = _words(clause, limit)
            pieces.extend(whole)
    if current:
        pieces.append(current)
    return pieces


def sentences(script: str) -> list[str]:
    """The script's sentences, each short enough to speak in one request."""
    spoken: list[str] = []
    for match in _SENTENCE.finditer(script):
        text = " ".join(match.group().split())
        if any(character.isalnum() for character in text):
            spoken.extend(_bounded(text, MAX_SPOKEN_CHARS))
    return spoken


def captions(spoken: list[tuple[str, int]]) -> tuple[list[Caption], int]:
    """Captions for sentences spoken one after another, and the total length.

    ``spoken`` holds each sentence with its speech duration. A sentence longer
    than one caption shares its time between captions by character count.
    """
    result: list[Caption] = []
    cursor = LEAD_MS
    for text, duration_ms in spoken:
        chunks = _bounded(text, MAX_CAPTION_CHARS)
        total = sum(len(chunk) for chunk in chunks)
        start, used = cursor, 0
        for chunk in chunks:
            used += len(chunk)
            end = cursor + duration_ms * used // total
            result.append(Caption(start, end, chunk))
            start = end
        cursor += duration_ms + GAP_MS
    return result, cursor - GAP_MS + TAIL_MS
