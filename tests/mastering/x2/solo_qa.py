"""SOLO-QA: pick at most a handful of the decisions most worth one look by the Owner (optional, never a gate).

Owner decision 2026-11-15 16:51Z: approved as optional. An answer is recorded as ``owner_spot_check``. It is **not**
a label in the X2 sense: there is one person, who knows the system, nothing is adjudicated, and no answer may be
reported as Human Validation, REAL-DEV or REAL-HOLDOUT evidence. Engineering never waits for it.

Input: a decision log as ``review.annotate`` leaves it (``units`` with text, notes, pieces, coverage and review
state), optionally the saved plan (its segments' reasons) and the source cues ``[(start, end, text)]``.

Each case gets one question that a glance can answer, the source span to look at, and an empty answer slot.
Priority, highest first:
1. ``number_conflict`` / ``substitution`` (``hard_negatives.contrast``): footage found through words, but the
   sentence names another year, entity or action than the speech over the shot;
2. ``cue_inpoint`` (#867 experiment): the piece moved onto the cue; does the picture still show the sentence?
3. ``speech_only``: footage found through the speech over the shot, nothing saw the picture;
4. ``unclear``: footage found, partly illustrated, still to review.
Units the planner already says have no footage are left out: they need no look to stay safe.
"""
from __future__ import annotations

import json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.mastering.x2 import hard_negatives as hn  # noqa: E402

LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 5, 10, 8
ANSWERS = ("ok", "wrong", "unsure")
EXPERIMENT_REASON = "thử nghiệm #867"
PRIORITY = {"number_conflict": 0, "substitution": 1, "cue_inpoint": 2, "speech_only": 3, "unclear": 4}
QUESTION = {
    "number_conflict": "Câu nói một năm/con số khác với lời trong cảnh. Hình có đúng là điều câu nói không?",
    "substitution": "Câu nói về một thứ/hành động khác với lời trong cảnh. Hình có đúng là điều câu nói không?",
    "cue_inpoint": "Đoạn phát đã dời tới chỗ lời được nói. Hình ở đoạn này có còn đúng với câu không?",
    "speech_only": "Cảnh được chọn chỉ vì lời nói trong cảnh. Hình có thật sự cho thấy điều câu nói không?",
    "unclear": "Cảnh chỉ minh họa một phần câu. Có chấp nhận được không?",
}


def _reasons(plan: Mapping[str, Any] | None, unit: Mapping[str, Any]) -> list[str]:
    """The unit's notes and the reasons of the plan's segments that speak it."""
    out = list(unit.get("notes") or ())
    for segment in (plan or {}).get("segments") or ():
        if unit["unit_id"] in (segment.get("speech_refs") or ()):
            out += list(segment.get("reasons") or ())
    return out


def select(log: Mapping[str, Any], cues: Sequence[tuple[int, int, str]] = (), *, plan: Mapping[str, Any] | None = None,
           limit: int = LIMIT_DEFAULT) -> list[dict[str, Any]]:
    if not LIMIT_MIN <= limit <= LIMIT_MAX:
        raise ValueError(f"limit must be {LIMIT_MIN}–{LIMIT_MAX}")
    cases = []
    for unit in log.get("units") or ():
        coverage = unit.get("coverage") or {}
        state = (unit.get("review") or {}).get("state")
        pieces = unit.get("pieces") or []
        if not coverage.get("matched_footage") or not pieces or state == "READY":
            continue
        first = pieces[0]
        start, end = first["offset_ms"], first["offset_ms"] + first["length_ms"]
        reasons = _reasons(plan, unit)
        why, detail = None, {}
        cue = hn.best_cue(unit.get("text", ""), cues, start - 4000, end + 4000) if cues else None
        if cue:
            check = hn.contrast(unit.get("text", ""), cue)
            if check["verdict"] != "consistent":
                why, detail = check["verdict"], {"cue": cue, **{k: check[k] for k in ("sentence_only", "cue_only", "numbers")}}
        if why is None and any(EXPERIMENT_REASON in r for r in reasons):
            why = "cue_inpoint"
        if why is None and any("chỉ khớp lời nói" in r for r in reasons):
            why = "speech_only"
        if why is None and coverage.get("status") == "partial":
            why = "unclear"
        if why is None:
            continue
        cases.append({"unit_id": unit["unit_id"], "text": unit.get("text", ""), "why": why, "question": QUESTION[why],
                      "look_at": {"candidate_id": first["id"], "from_ms": start, "to_ms": end}, "detail": detail,
                      "owner_spot_check": None, "answered_at": None})
    cases.sort(key=lambda c: (PRIORITY[c["why"]], c["unit_id"]))
    return cases[:limit]


def manifest(cases: Sequence[Mapping[str, Any]], *, source: str) -> dict[str, Any]:
    return {"kind": "dubflow.solo_qa.v1", "evidence_level": "OWNER_SPOT_CHECK_NOT_A_LABEL", "optional": True,
            "is_gate": False, "source": source, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "how": "Xem từng đoạn một lần khi tiện. Ghi ok / wrong / unsure. Bỏ trống cũng được.", "cases": list(cases)}


def answer(document: dict[str, Any], unit_id: str, value: str, *, at: str | None = None) -> dict[str, Any]:
    """Record one Owner answer; never anything else (no label fields, no confidence)."""
    if value not in ANSWERS:
        raise ValueError(f"answer must be one of {ANSWERS}")
    case = next(c for c in document["cases"] if c["unit_id"] == unit_id)
    case["owner_spot_check"] = value
    case["answered_at"] = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return document


def markdown(document: Mapping[str, Any]) -> str:
    out = ["# SOLO-QA (tùy chọn, không phải nhãn)", "", document["how"], "",
           "| # | Câu | Xem đoạn | Câu hỏi | Trả lời |", "|---|---|---|---|---|"]
    for i, c in enumerate(document["cases"], 1):
        look = c["look_at"]
        out.append(f"| {i} | {c['text'][:70]} | {look['candidate_id']} {look['from_ms'] / 1000:.1f}–{look['to_ms'] / 1000:.1f} s | "
                   f"{c['question']} | {c['owner_spot_check'] or ''} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # argv: log.json out_dir [cues.json] [plan.json]
    log_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    cues_path = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    plan = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8")) if len(sys.argv) > 4 else None
    log = json.loads(log_path.read_text(encoding="utf-8"))
    cues = [(c["start_ms"], c["end_ms"], c["text"]) for c in json.loads(cues_path.read_text(encoding="utf-8"))["cues"]] \
        if cues_path else []
    doc = manifest(select(log, cues, plan=plan), source=log_path.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "solo-qa.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "solo-qa.md").write_text(markdown(doc), encoding="utf-8")
    print(f"{len(doc['cases'])} case(s)")
