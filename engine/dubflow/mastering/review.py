"""Review of an editorial plan: what the planner knows about each sentence, and what the user decided (Phase 5 MVP).

Two separate things, never mixed:

* the planner's own verdict, written into the decision log by :func:`annotate`, per unit:

  - ``evidence``: whether footage that illustrates the unit was found, apart from the render:
    ``MATCH_AVAILABLE`` (the log has matching footage), ``MATCH_NOT_FOUND`` (none found, but not every usable shot
    was looked at for content, so the footage may still hold it) or ``SOURCE_INSUFFICIENT`` (every usable shot has
    content evidence and none matches: the footage given does not show it). A generic unit (an opening or a call to
    action that needs no match of its own) has ``NOT_NEEDED``.
  - ``state``: ``READY`` (matched, covered, confident), ``NEEDS_REVIEW``, ``MATCH_NOT_FOUND`` or
    ``SOURCE_INSUFFICIENT``.

* the user's decisions, saved as ``review-<n>.json`` beside the run (:class:`Review`): per unit an approval of the
  footage as it was shown, pinned shots and excluded shots, plus the warnings the user has read. A decision whose
  shots are gone from a new plan, or an approval of footage that has since changed, becomes ``INVALIDATED``.

An approval never changes the planner's verdict or its confidence: ``USER_APPROVED`` is the user's word, kept
apart, so a score computed from the plan stays the planner's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from . import align

SCHEMA_VERSION = 1
KIND = "editorial_review"
GENERIC = ("hook", "cta", "intro", "outro")
READY_CONFIDENCE = align.REVIEW_BELOW
MAX_HISTORY = 30
MAX_UNITS = 500
MAX_IDS = 50

MATCH_AVAILABLE, MATCH_NOT_FOUND, SOURCE_INSUFFICIENT, NOT_NEEDED = (
    "MATCH_AVAILABLE", "MATCH_NOT_FOUND", "SOURCE_INSUFFICIENT", "NOT_NEEDED")
READY, NEEDS_REVIEW = "READY", "NEEDS_REVIEW"
USER_APPROVED, INVALIDATED = "USER_APPROVED", "INVALIDATED"


class ReviewError(ValueError):
    pass


def _usable(candidate: Mapping[str, Any]) -> bool:
    return not (candidate.get("quality") or {}).get("problems")


def _has_content(candidate: Mapping[str, Any]) -> bool:
    found = candidate.get("evidence") or {}
    return any(item.get("origin") != "signal" and (str(item.get("text") or "").strip() or item.get("labels"))
               for item in found.get("items") or ())


def evidence_state(unit: Mapping[str, Any], log: Mapping[str, Any]) -> str:
    """Whether footage for ``unit`` was found, and if not whether the footage given can be said to lack it."""
    coverage = unit.get("coverage") or {}
    if coverage.get("matched_footage") or (log.get("edits") or {}).get("pin", {}).get(unit.get("unit_id")):
        return MATCH_AVAILABLE
    if coverage.get("status") == "generic":
        return NOT_NEEDED
    usable = [c for c in log.get("candidates") or () if _usable(c)]
    # Only when every usable shot was looked at for what it shows can the footage be said to lack this sentence.
    if log.get("mode") == "content" and usable and all(_has_content(c) for c in usable):
        return SOURCE_INSUFFICIENT
    return MATCH_NOT_FOUND


def unit_state(unit: Mapping[str, Any], log: Mapping[str, Any], segments: Iterable[Mapping[str, Any]] = ()) -> str:
    """The planner's verdict on one unit (no user decision in it)."""
    found = evidence_state(unit, log)
    if found in (MATCH_NOT_FOUND, SOURCE_INSUFFICIENT):
        return found
    coverage = unit.get("coverage") or {}
    if log.get("mode") != "content" or coverage.get("needs_review"):
        return NEEDS_REVIEW
    if any(s.get("needs_review") for s in segments):
        return NEEDS_REVIEW
    return READY


def annotate(log: dict[str, Any], plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write each unit's ``review`` (evidence, state) into the decision log ``log`` and return it.

    ``plan`` is the saved mastering plan (its dict); a unit is not ready when any of its segments needs review.
    """
    by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for segment in (plan or {}).get("segments") or ():
        for ref in segment.get("speech_refs") or ():
            by_unit.setdefault(ref, []).append(segment)
    for unit in log.get("units") or ():
        unit["review"] = {"evidence": evidence_state(unit, log), "state": unit_state(unit, log, by_unit.get(unit["unit_id"], ()))}
    counts: dict[str, int] = {}
    for unit in log.get("units") or ():
        counts[unit["review"]["state"]] = counts.get(unit["review"]["state"], 0) + 1
    log["review_summary"] = dict(sorted(counts.items()))
    return log


def warning_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class UnitDecision:
    approved: list[str] | None = None   # the chosen shots the user saw and approved, in order
    pin: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    invalidated: str = ""               # why an earlier decision no longer holds

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"pin": list(self.pin), "exclude": list(self.exclude)}
        if self.approved is not None:
            out["approved"] = list(self.approved)
        if self.invalidated:
            out["invalidated"] = self.invalidated
        return out

    @property
    def empty(self) -> bool:
        return self.approved is None and not self.pin and not self.exclude and not self.invalidated


def _ids(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_IDS or any(type(v) is not str or not 0 < len(v) <= 64 for v in value):
        raise ReviewError(f"{name} must be a list of shot ids")
    return list(dict.fromkeys(value))


@dataclass
class Review:
    """The user's decisions on one video's plan (``review-<n>.json``)."""

    plan_id: str = ""
    revision: int = 0
    units: dict[str, UnitDecision] = field(default_factory=dict)
    acknowledged: list[str] = field(default_factory=list)   # warning ids the user has read
    accept_unreviewed: bool = False                          # the user chose to export with unreviewed units
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Any) -> "Review":
        if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != KIND:
            raise ReviewError("not an editorial review v1")
        units_in = value.get("units") or {}
        if not isinstance(units_in, Mapping) or len(units_in) > MAX_UNITS:
            raise ReviewError("units must map unit ids to decisions")
        units: dict[str, UnitDecision] = {}
        for unit_id, raw in units_in.items():
            if type(unit_id) is not str or not 0 < len(unit_id) <= 32 or not isinstance(raw, Mapping) \
                    or set(raw) - {"approved", "pin", "exclude", "invalidated"}:
                raise ReviewError("a unit decision has unknown fields")
            approved = raw.get("approved")
            units[unit_id] = UnitDecision(None if approved is None else _ids(approved, "approved"),
                                          _ids(raw.get("pin", []), "pin"), _ids(raw.get("exclude", []), "exclude"),
                                          str(raw.get("invalidated") or "")[:300])
        acknowledged = value.get("acknowledged") or []
        if not isinstance(acknowledged, list) or len(acknowledged) > 500 or any(type(v) is not str or len(v) != 16 for v in acknowledged):
            raise ReviewError("acknowledged must list warning ids")
        history = value.get("history") or []
        if not isinstance(history, list):
            raise ReviewError("history must be a list")
        revision = value.get("revision", 0)
        if type(revision) is not int or revision < 0:
            raise ReviewError("revision must be a whole number")
        return cls(str(value.get("plan_id") or "")[:100], revision, units, list(dict.fromkeys(acknowledged)),
                   value.get("accept_unreviewed") is True, history[-MAX_HISTORY:])

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": KIND, "plan_id": self.plan_id, "revision": self.revision,
                "units": {k: v.to_dict() for k, v in sorted(self.units.items()) if not v.empty},
                "acknowledged": list(self.acknowledged), "accept_unreviewed": self.accept_unreviewed,
                "history": self.history[-MAX_HISTORY:]}

    def snapshot(self) -> dict[str, Any]:
        return {"revision": self.revision, "units": {k: v.to_dict() for k, v in self.units.items() if not v.empty},
                "acknowledged": list(self.acknowledged), "accept_unreviewed": self.accept_unreviewed}

    def edits(self, base: align.Edits | None = None) -> align.Edits:
        """The decisions as the planner's edits (pins and exclusions), on top of ``base``."""
        out = align.Edits.from_dict(base.to_dict() if base else None)
        for unit_id, decision in sorted(self.units.items()):
            if decision.invalidated:
                continue
            if decision.pin:
                out.pin[unit_id] = list(decision.pin)
            out.exclude.update(decision.exclude)
        return out

    # --- changes (each one bumps the revision and can be undone) ---

    def _change(self) -> None:
        self.history.append(self.snapshot())
        del self.history[:-MAX_HISTORY]
        self.revision += 1

    def decide(self, log: Mapping[str, Any], unit_id: str, action: str, ids: list[str] | None = None) -> None:
        """Apply one action of the review panel to ``unit_id`` of the decision log ``log``."""
        units = {u["unit_id"]: u for u in log.get("units") or ()}
        known = {c["id"] for c in log.get("candidates") or ()}
        if unit_id not in units:
            raise ReviewError("unknown sentence")
        wanted = _ids(ids or [], "shots")
        if any(i not in known for i in wanted):
            raise ReviewError("unknown shot")
        current = self.units.get(unit_id, UnitDecision())
        decision = UnitDecision(current.approved, list(current.pin), list(current.exclude))
        if action == "approve":
            decision.approved = list(units[unit_id].get("chosen") or [])
        elif action == "unapprove":
            decision.approved = None
        elif action == "pin":
            if not wanted:
                raise ReviewError("pin needs a shot")
            decision.pin = wanted
            decision.exclude = [i for i in decision.exclude if i not in wanted]
            decision.approved = None
        elif action == "exclude":
            if not wanted:
                raise ReviewError("exclude needs a shot")
            decision.exclude = list(dict.fromkeys([*decision.exclude, *wanted]))
            decision.pin = [i for i in decision.pin if i not in wanted]
            decision.approved = None
        elif action == "reset":
            decision = UnitDecision()
        else:
            raise ReviewError("unknown action")
        self._change()
        self.units[unit_id] = decision

    def acknowledge(self, warning: str) -> None:
        wid = warning_id(warning)
        if wid not in self.acknowledged:
            self._change()
            self.acknowledged.append(wid)

    def set_accept_unreviewed(self, value: bool) -> None:
        if value != self.accept_unreviewed:
            self._change()
            self.accept_unreviewed = value

    def undo(self) -> bool:
        if not self.history:
            return False
        last = self.history.pop()
        restored = Review.from_dict({"schema_version": SCHEMA_VERSION, "kind": KIND, **last})
        self.units, self.acknowledged, self.accept_unreviewed = restored.units, restored.acknowledged, restored.accept_unreviewed
        self.revision += 1
        return True

    def reconcile(self, log: Mapping[str, Any]) -> list[str]:
        """Check the decisions against a new decision log: drop what no longer exists, invalidate what changed.

        Returns the messages for the user (one per invalidated decision)."""
        units = {u["unit_id"]: u for u in log.get("units") or ()}
        known = {c["id"] for c in log.get("candidates") or ()}
        messages = []
        for unit_id, decision in list(self.units.items()):
            unit = units.get(unit_id)
            if unit is None:
                if not decision.empty:
                    decision.invalidated = "Câu này không còn trong kịch bản."
                    messages.append(f"Câu {unit_id[1:]}: {decision.invalidated}")
                continue
            gone = [i for i in [*decision.pin, *decision.exclude] if i not in known]
            if gone:
                decision.invalidated = "Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại."
                messages.append(f"Câu {unit_id[1:]}: {decision.invalidated}")
                continue
            if decision.approved is not None and list(decision.approved) != list(unit.get("chosen") or []):
                decision.approved = None
                decision.invalidated = "Cảnh của câu đã đổi sau khi bạn duyệt; hãy xem lại."
                messages.append(f"Câu {unit_id[1:]}: {decision.invalidated}")
                continue
            if decision.invalidated and decision.pin and all(i in known for i in decision.pin):
                decision.invalidated = ""
        return messages


def user_state(decision: UnitDecision | None) -> str | None:
    if decision is None:
        return None
    if decision.invalidated:
        return INVALIDATED
    if decision.approved is not None:
        return USER_APPROVED
    return None


def unreviewed(log: Mapping[str, Any], review: Review) -> list[str]:
    """Units the planner is not sure of and the user has neither approved nor replaced (pinned)."""
    out = []
    for unit in log.get("units") or ():
        state = (unit.get("review") or {}).get("state")
        if state in (None, READY):
            continue
        decision = review.units.get(unit["unit_id"])
        if decision is not None and not decision.invalidated and (decision.approved is not None or decision.pin):
            continue
        out.append(unit["unit_id"])
    return out


def view(log: Mapping[str, Any], plan: Mapping[str, Any] | None, review: Review, *, warnings: Iterable[str] = ()) -> dict[str, Any]:
    """What the review panel shows for one video: per sentence the chosen pieces, three alternatives, the planner's
    verdict and the user's decision; the warnings (read or not) and the sentences still unreviewed."""
    candidates = {c["id"]: c for c in log.get("candidates") or ()}

    def shot(candidate_id: str) -> dict[str, Any] | None:
        c = candidates.get(candidate_id)
        if c is None:
            return None
        return {"id": c["id"], "source_id": c["source_id"], "start_ms": c["start_ms"], "end_ms": c["end_ms"],
                "keyframe": c.get("keyframe"), "summary": c.get("summary") or "", "usable": _usable(c),
                "problems": list((c.get("quality") or {}).get("problems") or [])}

    pieces: dict[str, list[dict[str, Any]]] = {}
    for segment in (plan or {}).get("segments") or ():
        refs = segment.get("speech_refs") or []
        if not refs:
            continue
        src, tl = segment.get("source") or {}, segment.get("timeline") or {}
        pieces.setdefault(refs[0], []).append({
            "segment_id": segment.get("segment_id"), "source_id": segment.get("source_id"), "role": segment.get("role"),
            "source_start": int(src.get("start", 0)), "source_end": int(src.get("end", 0)),
            "timeline_start": int(tl.get("start", 0)), "timeline_end": int(tl.get("end", 0)),
            "confidence": (segment.get("confidence_milli")), "needs_review": bool(segment.get("needs_review")),
            "reasons": list(segment.get("reasons") or [])})
    acknowledged = set(review.acknowledged)
    open_units = set(unreviewed(log, review))
    units = []
    for unit in log.get("units") or ():
        decision = review.units.get(unit["unit_id"])
        verdict = unit.get("review") or {"evidence": evidence_state(unit, log), "state": unit_state(unit, log)}
        units.append({
            "unit_id": unit["unit_id"], "text": unit.get("text", ""), "function": unit.get("function"),
            "start_ms": unit.get("start_ms"), "end_ms": unit.get("end_ms"), "coverage": unit.get("coverage") or {},
            "notes": list(unit.get("notes") or []), "evidence": verdict["evidence"], "state": verdict["state"],
            "user_state": user_state(decision), "decision": decision.to_dict() if decision else None,
            "chosen": [s for s in (shot(i) for i in dict.fromkeys(unit.get("chosen") or [])) if s],
            "pieces": pieces.get(unit["unit_id"], []),
            # a shot the sentence already uses is no alternative (rc2 Windows check, D4)
            "alternatives": [dict(s, score=a.get("score"), reasons=list(a.get("reasons") or [])[:4])
                             for a in [a for a in (unit.get("alternatives") or [])
                                       if a.get("candidate_id") not in set(unit.get("chosen") or [])][:3]
                             if (s := shot(a.get("candidate_id", "")))],
            "unreviewed": unit["unit_id"] in open_units})
    texts = list(dict.fromkeys([*warnings, *(log.get("warnings") or [])]))
    return {"schema_version": 1, "kind": "editorial_review_view", "plan_id": log.get("plan_id"), "mode": log.get("mode"),
            "revision": review.revision, "can_undo": bool(review.history), "units": units,
            "warnings": [{"id": warning_id(t), "text": t, "acknowledged": warning_id(t) in acknowledged} for t in texts],
            "unreviewed": [u for u in (x["unit_id"] for x in units) if u in open_units],
            "accept_unreviewed": review.accept_unreviewed, "summary": log.get("review_summary") or {}}


def path_for(work: Path, number: int) -> Path:
    return work / f"review-{number}.json"


def load(path: Path) -> Review:
    if not path.is_file():
        return Review()
    if path.stat().st_size > 1024 * 1024:
        raise ReviewError("review file too large")
    return Review.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save(path: Path, review: Review) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(review.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    partial.replace(path)


def _read_json(path: Path, limit: int = 32 * 1024 * 1024) -> Any:
    if not path.is_file() or path.stat().st_size > limit:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


PART = re.compile(r"[A-Za-z0-9_-]{1,40}")


def part_path(work: Path, part: str) -> Path:
    """The user's decisions on one part of a Tổng hợp programme (``review-<part>.json``)."""
    if not PART.fullmatch(part):
        raise ReviewError("unknown part")
    return work / f"review-{part}.json"


def main(argv: list[str] | None = None) -> int:
    """``python -m engine.dubflow.mastering.review --work DIR (--video N | --part ID) <action> …``: the desktop's
    review calls, for a Bán hàng video (``--video``) or a part of a Tổng hợp programme (``--part``: hook, intro, an
    item id, outro).

    Prints the panel's view (JSON) after the action; exit 2 with a message on a refused action."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="dubflow.mastering.review")
    parser.add_argument("--work", type=Path, required=True)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--video", type=int)
    which.add_argument("--part")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("view")
    decide_p = sub.add_parser("decide")
    decide_p.add_argument("unit")
    decide_p.add_argument("what", choices=["approve", "unapprove", "pin", "exclude", "reset"])
    decide_p.add_argument("ids", nargs="*")
    sub.add_parser("undo")
    ack = sub.add_parser("ack")
    ack.add_argument("warning_id")
    accept = sub.add_parser("accept")
    accept.add_argument("value", choices=["true", "false"])
    args = parser.parse_args(argv)
    work: Path = args.work
    extra: list[str] = []
    if args.part is not None:
        if not PART.fullmatch(args.part):
            parser.error("--part must be a part id")
        log = _read_json(work / "mastering" / f"editorial-{args.part}.json")
        plan = _read_json(work / "mastering" / f"plan-{args.part}.json")
        path = part_path(work, args.part)
    else:
        if not 1 <= args.video <= 99:
            parser.error("--video must be 1-99")
        log = _read_json(work / f"editorial-{args.video}.json")
        plan = _read_json(work / f"mastering_plan-{args.video}.json")
        status = _read_json(work / "status.json", 4 * 1024 * 1024) or {}
        for video in status.get("videos") or ():
            if isinstance(video, dict) and video.get("index") == args.video:
                extra = [w for w in ((video.get("mastering") or {}).get("warnings") or []) if isinstance(w, str)]
        path = path_for(work, args.video)
    if not isinstance(log, dict):
        print(json.dumps({"error": "Phần này chưa có kế hoạch dựng để duyệt."}, ensure_ascii=False))
        return 2
    try:
        review = load(path)
        if args.action == "decide":
            review.decide(log, args.unit, args.what, args.ids)
        elif args.action == "undo":
            review.undo()
        elif args.action == "ack":
            texts = list(dict.fromkeys([*extra, *(log.get("warnings") or [])]))
            match = [t for t in texts if warning_id(t) == args.warning_id]
            if not match:
                raise ReviewError("unknown warning")
            review.acknowledge(match[0])
        elif args.action == "accept":
            review.set_accept_unreviewed(args.value == "true")
        if args.action != "view":
            review.plan_id = str(log.get("plan_id") or "")
            save(path, review)
    except (ReviewError, ValueError) as error:
        print(json.dumps({"error": str(error)[:300]}, ensure_ascii=False))
        return 2
    sys.stdout.write(json.dumps(view(log, plan, review, warnings=extra), ensure_ascii=False))
    return 0


__all__ = ["INVALIDATED", "MATCH_AVAILABLE", "MATCH_NOT_FOUND", "NEEDS_REVIEW", "NOT_NEEDED", "READY", "Review",
           "ReviewError", "SOURCE_INSUFFICIENT", "USER_APPROVED", "UnitDecision", "annotate", "evidence_state", "load",
           "part_path", "path_for", "save", "unit_state", "unreviewed", "user_state", "main", "view", "warning_id"]


if __name__ == "__main__":
    raise SystemExit(main())
