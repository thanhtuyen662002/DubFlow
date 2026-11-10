"""Vietnamese captions timed from the final Vietnamese voice (ADR-0055, Balanced and Dynamic).

A dubbed phrase (one or more translated cues spoken as one TTS piece) is heard from its ``slot_start`` to its
``actual_end`` in the final mix. Its cues share that span by the length of their text, so a caption is on screen
while its words are spoken, not while the source-language speaker said the original. Source-language word
timestamps are never used. A cue that was not dubbed keeps its own time.

Then the captions are made readable: at most ``MAX_LINES`` lines of ``MAX_CHARS`` characters (longer text is split
into consecutive captions over its span, at word boundaries), at least ``MIN_MS`` on screen when the next caption
leaves room, and never overlapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

MAX_CHARS = 42
MAX_LINES = 2
MIN_MS = 1000
GAP_MS = 40


@dataclass(frozen=True)
class Caption:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str


def _wrap(text: str, width: int = MAX_CHARS) -> list[str]:
    """Greedy word wrap; a word longer than ``width`` stands on its own line."""
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def _balanced(lines: list[str]) -> str:
    """Two lines of similar length read better than a full line and a stub."""
    if len(lines) != 2:
        return "\n".join(lines)
    words = " ".join(lines).split()
    best = lines
    for cut in range(1, len(words)):
        a, b = " ".join(words[:cut]), " ".join(words[cut:])
        if len(a) <= MAX_CHARS and len(b) <= MAX_CHARS and abs(len(a) - len(b)) < abs(len(best[0]) - len(best[1])):
            best = [a, b]
    return "\n".join(best)


def _split(caption: Caption) -> list[Caption]:
    """Text longer than two lines becomes consecutive captions sharing the span by length."""
    lines = _wrap(" ".join(caption.text.split()))
    groups = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)] or [[caption.text]]
    total = sum(len(" ".join(group)) for group in groups)
    out, at = [], caption.start_ms
    span = caption.end_ms - caption.start_ms
    for index, group in enumerate(groups):
        end = caption.end_ms if index == len(groups) - 1 else at + max(1, span * len(" ".join(group)) // max(1, total))
        out.append(Caption(caption.cue_id if len(groups) == 1 else f"{caption.cue_id}#{index + 1}", at, max(at + 1, end),
                           _balanced(group)))
        at = max(at + 1, end)
    return out


def from_voice(cues: Sequence[tuple[str, int, int, str]], spoken: Mapping[str, tuple[Sequence[str], int, int]]) -> list[Caption]:
    """Captions of ``cues`` (id, start, end, text on screen) timed by ``spoken`` (phrase id → member cue ids, the ms the
    phrase's voice starts and ends in the final mix)."""
    timed: dict[str, tuple[int, int]] = {}
    text = {cue_id: shown for cue_id, _, _, shown in cues}
    for members, start, end in spoken.values():
        members = [m for m in members if m in text]
        if not members or end <= start:
            continue
        weights = [max(1, len(text[m])) for m in members]
        total, at = sum(weights), start
        for index, (member, weight) in enumerate(zip(members, weights)):
            stop = end if index == len(members) - 1 else at + (end - start) * weight // total
            timed[member] = (at, max(at + 1, stop))
            at = max(at + 1, stop)
    captions = [Caption(cue_id, *timed.get(cue_id, (start, end)), shown) for cue_id, start, end, shown in cues]
    captions.sort(key=lambda c: (c.start_ms, c.end_ms, c.cue_id))
    parts = [part for caption in captions for part in _split(caption)]
    result = []
    for index, caption in enumerate(parts):
        following = parts[index + 1].start_ms if index + 1 < len(parts) else None
        end = max(caption.end_ms, caption.start_ms + MIN_MS)
        if following is not None:
            end = min(end, following - GAP_MS)  # never over the next caption
        result.append(Caption(caption.cue_id, caption.start_ms, max(caption.start_ms + 1, end), caption.text))
    return result


__all__ = ["Caption", "GAP_MS", "MAX_CHARS", "MAX_LINES", "MIN_MS", "from_voice"]
