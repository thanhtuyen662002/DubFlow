"""Why hard negatives get footage, and an offline contrast check (Workstream C, V3 §VI). Research only.

ENGINEERING_FIXTURE_ONLY = TRUE for the fixtures; the d-s1 probes are agent-written and stay on the test machine.
Nothing here is wired into the planner, verification or Final QC, and it is not #844.

Four things are kept apart for each hard negative (a sentence whose footage does not exist, by construction):
1. *offered*: some shot reaches the planner's minimum relevance (it is a candidate);
2. *assigned*: the planner says it found matching footage (``coverage.matched_footage``);
3. *reviewed*: the sentence is NEEDS_REVIEW (or MATCH_NOT_FOUND), never READY;
4. *blocked*: Final QC on the whole part does not pass while the sentence is unreviewed.

``contrast`` is a prototype of a check that could explain an assignment to a reviewer: it compares the content words
of the sentence with those of the cue that matched it.
- ``number_conflict``: both name a number (a year) and they differ;
- ``substitution``: each side has a content word the other lacks while they share some (another entity or another
  action in the same frame: "the helicopter climbs the mountain" over "the locomotive climbs the mountain");
- ``consistent``: neither. A cue is long and says more than a sentence, so the cue side alone is never a signal.
It can only add a reason to look; it must never remove one or raise a confidence.
"""
from __future__ import annotations

import json, re, sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import evidence as ev  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
_NUMBER = re.compile(r"^\d+$")
# words of a narrator's frame, not of what is shown (fixture sentences start "We watch ..."; d-s1 "Next, ...")
_FRAME = frozenset({"we", "watch", "see", "look", "next", "then", "finally", "now", "here", "again", "also"})


def _stem(word: str) -> str:
    if word.endswith("ss"):  # cross, glass: no plural "s" to take off
        return word
    if word.endswith(("sses", "shes", "ches", "xes")):  # crosses, flashes, reaches, boxes
        return word[:-2]
    for suffix in ("ing", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def content(text: str) -> tuple[set[str], set[str]]:
    """(content word stems, numbers) of ``text``."""
    words = [w for w in ev.words(text) if w not in _FRAME]
    return {_stem(w) for w in words if not _NUMBER.match(w)}, {w for w in words if _NUMBER.match(w)}


def contrast(sentence: str, cue: str) -> dict[str, Any]:
    s_words, s_nums = content(sentence)
    c_words, c_nums = content(cue)
    shared = s_words & c_words
    only_s, only_c = s_words - c_words, c_words - s_words
    if s_nums and c_nums and not s_nums & c_nums:
        verdict = "number_conflict"
    elif shared and only_s and only_c:
        verdict = "substitution"
    else:
        verdict = "consistent"
    return {"verdict": verdict, "shared": sorted(shared), "sentence_only": sorted(only_s), "cue_only": sorted(only_c),
            "numbers": [sorted(s_nums), sorted(c_nums)]}


def best_cue(sentence: str, cues: Sequence[tuple[int, int, str]], start_ms: int, end_ms: int) -> str | None:
    """The cue inside ``[start_ms, end_ms)`` sharing the most words with the sentence."""
    wanted = ev.terms(sentence)
    inside = [(len(ev.terms(t) & wanted), -a, t) for a, b, t in cues if a < end_ms and b > start_ms]
    best = max(inside, default=None)
    return best[2] if best and best[0] else None


def stages(rows: Sequence[Mapping[str, Any]], *, final_qc_blocks: bool, min_relevance: int = 60) -> dict[str, Any]:
    """The four stages for the hard negatives among ``rows`` (solo_bench unit rows)."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        if "false_assignment" not in row:
            continue
        kind = out.setdefault(row["kind"], {"units": 0, "offered": 0, "assigned": 0, "reviewed": 0, "ready": 0})
        kind["units"] += 1
        kind["offered"] += (row.get("best_relevance") or 0) >= min_relevance
        kind["assigned"] += bool(row["matched_footage"])
        kind["reviewed"] += row["state"] in ("NEEDS_REVIEW", "MATCH_NOT_FOUND", "SOURCE_INSUFFICIENT")
        kind["ready"] += row["state"] == "READY"
    return {"by_kind": out, "final_qc_blocks_part": final_qc_blocks}


def solo(work: Path) -> dict[str, Any]:
    """solo_bench's hard negatives through the four stages, and ``contrast`` on every assigned unit (match or not)."""
    from tests.mastering.x2 import solo_bench as sb
    result = sb.run(work)
    said = sb.cues()
    checks = []
    for row in result["units"]:
        if not row["matched_footage"] or not row["played"]:
            continue
        _, start, end = row["played"][0]
        shot = start // sb.SHOT_MS
        cue = best_cue(next(u.text for u in sb.units() if u.uid == row["unit"]), said, shot * sb.SHOT_MS, (shot + 1) * sb.SHOT_MS)
        if cue:
            checks.append({"unit": row["unit"], "kind": row["kind"],
                           **contrast(next(u.text for u in sb.units() if u.uid == row["unit"]), cue)})
    return {"ENGINEERING_FIXTURE_ONLY": True,
            "stages": stages(result["units"], final_qc_blocks=not result["summary"]["final_qc"]["decisions"]),
            "contrast": checks, "summary": score(checks)}


def score(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    negatives = [c for c in checks if c["kind"] not in ("match", "montage", "pos")]
    positives = [c for c in checks if c["kind"] in ("match", "montage", "pos")]
    return {"negatives_assigned": len(negatives),
            "negatives_flagged": sum(c["verdict"] != "consistent" for c in negatives),
            "positives_assigned": len(positives),
            "positives_flagged": sum(c["verdict"] != "consistent" for c in positives)}


if __name__ == "__main__":
    out = solo(Path(sys.argv[1] if len(sys.argv) > 1 else "solo-bench"))
    print(json.dumps(out, indent=1, ensure_ascii=False))
