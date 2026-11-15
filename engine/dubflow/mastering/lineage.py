"""Cue lineage (EXPERIMENT, off by default): a narration sentence cites the source cues it was written from.

The chain is ``source_id -> cue_id -> source time -> sentence -> shot holding the cue -> played span``. It lets a
sentence in one language find the shot whose speech, in another language, says the same thing.

Rules (Owner/Codex decision 2026-11-15):
- a cue file is used only when its sha256 is the one recorded for it (``CueIndex.load``);
- a citation names a known ``source_id`` and an existing ``cue_id``; anything else is rejected, with its reason, and
  never repaired; times come only from the verified cue file, never from the narration;
- a cue says where words are spoken, never what the picture shows: every link carries ``picture_verified: False``;
  lineage raises no confidence, lowers no review flag and never makes a sentence READY;
- two modes, kept apart: ``region`` (only shots inside the cited cues' region; no valid cue or no shot there:
  the sentence has no match, never a whole-source fallback) and ``ranked`` (every shot, the cited cues' words
  added to the sentence's words for ranking; no valid cue: ranked as today).

Nothing in production passes a :class:`Lineage`; ``align.assign(..., lineage=None)`` is the default everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

MODES = ("region", "ranked")
REJECT_UNKNOWN_SOURCE = "unknown_source"
REJECT_UNKNOWN_CUE = "unknown_cue"
REJECT_MALFORMED = "malformed_ref"


class LineageError(ValueError):
    pass


@dataclass(frozen=True)
class Cue:
    source_id: str
    cue_id: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class CueIndex:
    """The cues of one source, read from a cue file whose sha256 was checked."""

    source_id: str
    sha256: str
    cues: Mapping[str, Cue]

    @classmethod
    def from_document(cls, source_id: str, document: Mapping[str, Any], sha256: str) -> "CueIndex":
        if not isinstance(source_id, str) or not source_id:
            raise LineageError("source_id must be a non-empty string")
        out: dict[str, Cue] = {}
        for raw in document.get("cues") or ():
            if not isinstance(raw, Mapping):
                raise LineageError(f"{source_id}: malformed cue {raw!r}")
            cid, a, b, text = raw.get("id"), raw.get("start_ms"), raw.get("end_ms"), raw.get("text")
            if not isinstance(cid, str) or not cid or type(a) is not int or type(b) is not int or not 0 <= a < b \
                    or not isinstance(text, str):
                raise LineageError(f"{source_id}: malformed cue {raw!r}")
            if cid in out:
                raise LineageError(f"{source_id}: duplicate cue id {cid}")
            out[cid] = Cue(source_id, cid, a, b, text)
        return cls(source_id, sha256, out)

    @classmethod
    def load(cls, source_id: str, path: Path, expected_sha256: str) -> "CueIndex":
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise LineageError(f"{source_id}: cue file sha256 {digest} is not the recorded {expected_sha256}")
        return cls.from_document(source_id, json.loads(data.decode("utf-8")), digest)


@dataclass(frozen=True)
class Lineage:
    """What the planner gets: the mode and, per narration sentence in order, its verified cues and its rejections."""

    mode: str
    cues: tuple[tuple[Cue, ...], ...]
    rejected: tuple[tuple[tuple[str, str], ...], ...]  # per sentence: (ref, reason)
    window_ms: int = 0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise LineageError(f"lineage mode must be one of {MODES}")
        if type(self.window_ms) is not int or not 0 <= self.window_ms <= 60_000:
            raise LineageError("window_ms must be 0–60000")
        if len(self.cues) != len(self.rejected):
            raise LineageError("cues and rejections must cover the same sentences")

    def reason(self, index: int) -> str | None:
        """X2 §2b: why sentence ``index`` has no valid cue (``None`` when it has one)."""
        if self.cues[index]:
            return None
        return "cue_rejected" if self.rejected[index] else "no_cue_written"


def resolve(mode: str, refs: Sequence[Sequence[str] | None], indexes: Mapping[str, CueIndex], *,
            window_ms: int = 0) -> Lineage:
    """Each sentence's citations (``"source_id:cue_id"``; ``None`` or empty: none written) against the verified
    indexes. Old narration without ``cue_refs`` passes ``None`` for every sentence."""
    cues: list[tuple[Cue, ...]] = []
    rejected: list[tuple[tuple[str, str], ...]] = []
    for sentence_refs in refs:
        mine: list[Cue] = []
        bad: list[tuple[str, str]] = []
        for ref in sentence_refs or ():
            if not isinstance(ref, str) or ref.count(":") != 1 or not all(ref.split(":")):
                bad.append((str(ref), REJECT_MALFORMED))
                continue
            source_id, cue_id = ref.split(":")
            index = indexes.get(source_id)
            if index is None:
                bad.append((ref, REJECT_UNKNOWN_SOURCE))
            elif cue_id not in index.cues:
                bad.append((ref, REJECT_UNKNOWN_CUE))
            elif index.cues[cue_id] not in mine:
                mine.append(index.cues[cue_id])
        cues.append(tuple(mine))
        rejected.append(tuple(bad))
    return Lineage(mode, tuple(cues), tuple(rejected), window_ms)


def link(cue: Cue, holder: Any, played: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """One logged lineage step. ``holder``: the shot containing the cue start (a Candidate) or ``None``."""
    over = sum(max(0, min(e, cue.end_ms) - max(o, cue.start_ms)) for o, e in played)
    return {"source_id": cue.source_id, "cue_id": cue.cue_id, "cue_ms": [cue.start_ms, cue.end_ms],
            "candidate_id": getattr(holder, "candidate_id", None),
            "played_ms": [[o, e] for o, e in played], "played_over_cue_ms": over, "picture_verified": False}
