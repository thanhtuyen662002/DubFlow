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
from typing import Any, Iterable, Mapping, Sequence

from . import align, evidence as ev

SCHEMA_VERSION = 2  # 2: review later, no suitable footage, the decision record, the queue position (1 still reads)
READABLE_VERSIONS = (1, 2)
KIND = "editorial_review"
GENERIC = ("hook", "cta", "intro", "outro")
READY_CONFIDENCE = align.REVIEW_BELOW
MAX_HISTORY = 30
MAX_UNITS = 500
MAX_IDS = 50
MAX_RECORDS = 1000
MAX_SHOTS = 300

MATCH_AVAILABLE, MATCH_NOT_FOUND, SOURCE_INSUFFICIENT, NOT_NEEDED = (
    "MATCH_AVAILABLE", "MATCH_NOT_FOUND", "SOURCE_INSUFFICIENT", "NOT_NEEDED")
READY, NEEDS_REVIEW = "READY", "NEEDS_REVIEW"
USER_APPROVED, INVALIDATED = "USER_APPROVED", "INVALIDATED"
USER_SOURCE_MISSING = "USER_SOURCE_MISSING"   # the user found no suitable footage: the sentence stays without its own shot

# The Human Decision Contract (directive "Assisted Editorial Production" §B): each decision is recorded with these
# types. Panel actions map onto them: approve → APPROVE, pin of a shot already shown → PIN, of another → REPLACE,
# exclude → EXCLUDE, later → REVIEW_LATER, missing → MARK_SOURCE_MISSING, undo/reset/unapprove → UNDO; a decision a
# new plan voids is recorded by the system as INVALIDATED.
DECISION_TYPES = ("APPROVE", "PIN", "EXCLUDE", "REPLACE", "REVIEW_LATER", "MARK_SOURCE_MISSING", "UNDO", "INVALIDATED")
# Why a decision no longer holds (the conflict record of an INVALIDATED decision, P2-N5) and what the user can do.
CONFLICT_OPTIONS = {
    "sentence_removed": ("Bỏ qua: câu không còn trong kịch bản",),
    "sentence_changed": ("Duyệt lại câu với lời mới", "Chọn cảnh khác"),
    "source_changed": ("Duyệt lại cảnh với nội dung nguồn mới", "Chọn cảnh khác"),
    "shot_gone": ("Chọn cảnh khác",),
    "excluded": ("Bỏ loại trừ cảnh", "Chọn cảnh khác cho câu này"),
    "pinned_elsewhere": ("Ghim cảnh khác cho câu kia", "Chọn cảnh khác cho câu này"),
    "timing_changed": ("Duyệt lại câu với thời lượng mới", "Chọn cảnh khác"),
    "shown_again": ("Duyệt lại cảnh chiếu lại", "Chọn cảnh khác"),
    "plan_changed": ("Duyệt lại cảnh mới", "Chọn cảnh khác"),
}

# The review queue (uncalibrated: the groups say what the planner and the checks flagged, not a measured risk)
CRITICAL, REVIEW, OPTIONAL = "critical", "review", "optional"
PRIORITY_ORDER = {CRITICAL: 0, REVIEW: 1, OPTIONAL: 2}


class ReviewError(ValueError):
    pass


def _usable(candidate: Mapping[str, Any]) -> bool:
    return not (candidate.get("quality") or {}).get("problems")


def _has_content(candidate: Mapping[str, Any]) -> bool:
    """Whether something says what the shot shows: vision, a transcript, a user's note; never only its file name."""
    found = candidate.get("evidence") or {}
    return any(item.get("origin") in ev.CONTENT_ORIGINS and (str(item.get("text") or "").strip() or item.get("labels"))
               for item in found.get("items") or ())


def provenance_of(log: Mapping[str, Any]) -> dict[str, int]:
    """Provenance → candidates with evidence of it, from the log's own candidates (for logs written before it)."""
    out = {name: 0 for name in ev.PROVENANCES}
    for c in log.get("candidates") or ():
        kinds = {ev.provenance(str(i.get("origin")), str(i.get("detail") or ""))
                 for i in (c.get("evidence") or {}).get("items") or ()
                 if i.get("origin") == "signal" or str(i.get("text") or "").strip() or i.get("labels")}
        for kind in kinds:
            out[kind] = out.get(kind, 0) + 1
    return out


def content_mode(log: Mapping[str, Any]) -> str:
    """The log's mode, read honestly: a log written before P2-14 may say ``content`` when its only evidence was
    file names; such a log is ``signals_only`` (nothing it holds can be READY or say the footage lacks a line)."""
    mode = log.get("mode")
    if mode == "content" and not any(_has_content(c) for c in log.get("candidates") or ()):
        return "signals_only"
    return mode


def evidence_state(unit: Mapping[str, Any], log: Mapping[str, Any]) -> str:
    """Whether footage for ``unit`` was found, and if not whether the footage given can be said to lack it."""
    coverage = unit.get("coverage") or {}
    # only what the AI matched is evidence; a user's pin is the user's decision, shown as theirs (rc9 P2-N14)
    if coverage.get("matched_footage"):
        return MATCH_AVAILABLE
    if (log.get("edits") or {}).get("pin", {}).get(unit.get("unit_id")) and "user_ms" not in coverage:
        return MATCH_AVAILABLE  # a log from before user_ms: its planner counted the pin as a match
    if coverage.get("status") == "generic":
        return NOT_NEEDED
    usable = [c for c in log.get("candidates") or () if _usable(c)]
    # Only when every usable shot was looked at for what it shows can the footage be said to lack this sentence.
    # ... and only when none of them shares a word with it: a near miss (some of its words, under a match) is a shot
    # that may show it in other words, so it is not found, not absent (rc5: "hàng domino… đứng xem" at 37, "hai anh em
    # ngồi thử máy" at 57 were called absent while those shots showed them)
    if content_mode(log) == "content" and usable and all(_has_content(c) for c in usable) \
            and not coverage.get("best_relevance"):
        return SOURCE_INSUFFICIENT
    return MATCH_NOT_FOUND


def unit_state(unit: Mapping[str, Any], log: Mapping[str, Any], segments: Iterable[Mapping[str, Any]] = ()) -> str:
    """The planner's verdict on one unit (no user decision in it)."""
    found = evidence_state(unit, log)
    if found in (MATCH_NOT_FOUND, SOURCE_INSUFFICIENT):
        return found
    coverage = unit.get("coverage") or {}
    if content_mode(log) != "content" or coverage.get("needs_review"):
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
    later: bool = False                 # the user will come back to it (REVIEW_LATER): still unreviewed
    source_missing: bool = False        # the user found no suitable footage (MARK_SOURCE_MISSING)
    # what the approval was given on (P2-N5): the pieces as they played ([shot, source offset ms, length ms]) and
    # the material (the sentence and the sources' content, :func:`basis`). Absent in reviews saved before.
    pieces: list[list[Any]] | None = None
    basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"pin": list(self.pin), "exclude": list(self.exclude)}
        if self.approved is not None:
            out["approved"] = list(self.approved)
            if self.pieces is not None:
                out["pieces"] = [list(p) for p in self.pieces]
            if self.basis:
                out["basis"] = self.basis
        if self.invalidated:
            out["invalidated"] = self.invalidated
        if self.later:
            out["later"] = True
        if self.source_missing:
            out["source_missing"] = True
        return out

    @property
    def empty(self) -> bool:
        return self.approved is None and not self.pin and not self.exclude and not self.invalidated \
            and not self.later and not self.source_missing


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
    records: list[dict[str, Any]] = field(default_factory=list)  # every decision as the contract records it
    position: str = ""                                        # the sentence the user was on (kept across reopen)
    conflicts: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False, repr=False)  # last reconcile's

    @classmethod
    def from_dict(cls, value: Any) -> "Review":
        if not isinstance(value, Mapping) or value.get("schema_version") not in READABLE_VERSIONS or value.get("kind") != KIND:
            raise ReviewError("not an editorial review v1/v2")
        units_in = value.get("units") or {}
        if not isinstance(units_in, Mapping) or len(units_in) > MAX_UNITS:
            raise ReviewError("units must map unit ids to decisions")
        units: dict[str, UnitDecision] = {}
        for unit_id, raw in units_in.items():
            if type(unit_id) is not str or not 0 < len(unit_id) <= 32 or not isinstance(raw, Mapping) \
                    or set(raw) - {"approved", "pin", "exclude", "invalidated", "later", "source_missing", "pieces", "basis"}:
                raise ReviewError("a unit decision has unknown fields")
            approved = raw.get("approved")
            basis = raw.get("basis") or ""
            if type(basis) is not str or len(basis) > 64:
                raise ReviewError("basis must be a fingerprint")
            units[unit_id] = UnitDecision(None if approved is None else _ids(approved, "approved"),
                                          _ids(raw.get("pin", []), "pin"), _ids(raw.get("exclude", []), "exclude"),
                                          str(raw.get("invalidated") or "")[:300], raw.get("later") is True,
                                          raw.get("source_missing") is True, _pieces(raw.get("pieces")), basis)
        acknowledged = value.get("acknowledged") or []
        if not isinstance(acknowledged, list) or len(acknowledged) > 500 or any(type(v) is not str or len(v) != 16 for v in acknowledged):
            raise ReviewError("acknowledged must list warning ids")
        history = value.get("history") or []
        if not isinstance(history, list):
            raise ReviewError("history must be a list")
        revision = value.get("revision", 0)
        if type(revision) is not int or revision < 0:
            raise ReviewError("revision must be a whole number")
        records = value.get("records") or []
        if not isinstance(records, list) or any(not isinstance(r, Mapping) or r.get("type") not in DECISION_TYPES
                                                for r in records):
            raise ReviewError("records must list decisions")
        position = value.get("position") or ""
        if type(position) is not str or len(position) > 32:
            raise ReviewError("position must be a sentence id")
        return cls(str(value.get("plan_id") or "")[:100], revision, units, list(dict.fromkeys(acknowledged)),
                   value.get("accept_unreviewed") is True, history[-MAX_HISTORY:],
                   [dict(r) for r in records[-MAX_RECORDS:]], position)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": KIND, "plan_id": self.plan_id, "revision": self.revision,
                "units": {k: v.to_dict() for k, v in sorted(self.units.items()) if not v.empty},
                "acknowledged": list(self.acknowledged), "accept_unreviewed": self.accept_unreviewed,
                "history": self.history[-MAX_HISTORY:], "records": self.records[-MAX_RECORDS:], "position": self.position}

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
            elif decision.approved and not decision.source_missing:
                # an approval is a constraint of the next plan (P2-N5): the sentence keeps the footage approved, as
                # it played when the pieces are known
                out.keep[unit_id] = [(p[0], p[1], p[2], len(p) > 3 and p[3] is True) for p in decision.pieces] \
                    if decision.pieces else [(i, -1, 0, False) for i in decision.approved]
                if decision.basis:  # the words it was given on: no constraint for a sentence that is now another one
                    out.keep_words[unit_id] = decision.basis[:8]
            out.exclude.update(decision.exclude)
        return out

    # --- changes (each one bumps the revision and can be undone) ---

    def _change(self) -> None:
        self.history.append(self.snapshot())
        del self.history[:-MAX_HISTORY]
        self.revision += 1

    def decide(self, log: Mapping[str, Any], unit_id: str, action: str, ids: list[str] | None = None, *,
               run: str = "", now: str | None = None) -> None:
        """Apply one action of the review panel to ``unit_id`` of the decision log ``log``, and record it.

        ``focus`` only remembers the sentence the user is on: no revision, no undo step, no record."""
        units = {u["unit_id"]: u for u in log.get("units") or ()}
        known = {c["id"] for c in log.get("candidates") or ()}
        if unit_id not in units:
            raise ReviewError("unknown sentence")
        wanted = _ids(ids or [], "shots")
        if any(i not in known for i in wanted):
            raise ReviewError("unknown shot")
        if action == "focus":
            self.position = unit_id
            return
        current = self.units.get(unit_id, UnitDecision())
        decision = UnitDecision(current.approved, list(current.pin), list(current.exclude), "", current.later,
                                current.source_missing, current.pieces, current.basis)
        shown = list(units[unit_id].get("chosen") or [])
        kind = {"approve": "APPROVE", "exclude": "EXCLUDE", "later": "REVIEW_LATER", "missing": "MARK_SOURCE_MISSING",
                "unapprove": "UNDO", "reset": "UNDO"}.get(action)
        if action == "pin":
            kind = "PIN" if all(i in shown for i in wanted) else "REPLACE"
        if action == "approve":
            # the shots, each once (a shot shown twice is one shot; its pieces say how it plays)
            chosen = list(dict.fromkeys(units[unit_id].get("chosen") or []))
            # approving a pin before the render that applies it approves the user's own pick (P2-5): what the
            # sentence will show is the pinned shots, not the planner's choice still on screen
            decision.approved = chosen if not decision.pin or _applied(decision.pin, chosen) else list(decision.pin)
            showing = decision.approved == chosen
            decision.pieces = pieces_of(units[unit_id]) if showing else None
            decision.basis = basis(units[unit_id], log) if showing else ""
        elif action == "unapprove":
            decision.approved, decision.pieces, decision.basis = None, None, ""
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
        elif action == "later":
            decision.later = not decision.later
        elif action == "missing":
            # no suitable footage: the user's word that the sentence stays without its own shot; an approval of the
            # shown footage would contradict it, so it goes
            decision.source_missing = not decision.source_missing
            if decision.source_missing:
                decision.approved = None
                decision.later = False
        else:
            raise ReviewError("unknown action")
        if action in ("approve", "pin"):
            decision.later = False
            decision.source_missing = False
        self._change()
        self.units[unit_id] = decision
        self.position = unit_id
        self.record(log, unit_id, kind or "UNDO", wanted or (decision.approved or shown)[:1], run=run, now=now,
                    detail=action if action in ("reset", "unapprove") or (action in ("later", "missing")
                                                                       and not (decision.later or decision.source_missing))
                    else "")

    def record(self, log: Mapping[str, Any], unit_id: str, kind: str, shots: list[str], *, run: str = "",
               now: str | None = None, detail: str = "", reason: str = "", conflict: Mapping[str, Any] | None = None) -> None:
        """Append one decision as the Human Decision Contract records it."""
        units = {u["unit_id"]: u for u in log.get("units") or ()}
        candidates = {c["id"]: c for c in log.get("candidates") or ()}
        unit = units.get(unit_id) or {}
        shot = candidates.get(shots[0]) if shots else None
        decision = self.units.get(unit_id)
        entry: dict[str, Any] = {
            "type": kind, "run_id": run[:120], "plan_revision": str(log.get("plan_id") or "")[:100],
            "speech_ref": unit_id, "source_id": shot.get("source_id") if shot else None,
            "shot_id": shot.get("id") if shot else None,
            "source_ms": [shot.get("start_ms"), shot.get("end_ms")] if shot else None,
            "decision_revision": self.revision, "approval_state": user_state(decision) or "NONE",
            "input_fingerprint": fingerprint(unit, candidates), "decided_at": now or _now(),
            "invalidation_reason": reason[:300] or None}
        if detail:
            entry["detail"] = detail
        if conflict:
            entry["conflict"] = dict(conflict)
        self.records.append(entry)
        del self.records[:-MAX_RECORDS]

    def acknowledge(self, warning: str) -> None:
        wid = warning_id(warning)
        if wid not in self.acknowledged:
            self._change()
            self.acknowledged.append(wid)

    def set_accept_unreviewed(self, value: bool) -> None:
        if value != self.accept_unreviewed:
            self._change()
            self.accept_unreviewed = value

    def undo(self, log: Mapping[str, Any] | None = None, *, run: str = "", now: str | None = None) -> bool:
        if not self.history:
            return False
        last = self.history.pop()
        before = {k: v.to_dict() for k, v in self.units.items()}
        restored = Review.from_dict({"schema_version": SCHEMA_VERSION, "kind": KIND, **last})
        self.units, self.acknowledged, self.accept_unreviewed = restored.units, restored.acknowledged, restored.accept_unreviewed
        self.revision += 1
        if log is not None:
            after = {k: v.to_dict() for k, v in self.units.items()}
            for unit_id in sorted(set(before) | set(after)):
                if before.get(unit_id) != after.get(unit_id):
                    self.record(log, unit_id, "UNDO", [], run=run, now=now, detail="undo")
        return True

    def reconcile(self, log: Mapping[str, Any], *, run: str = "") -> list[str]:
        """Check the decisions against a new decision log: drop what no longer exists, invalidate what changed.

        Returns the messages for the user (one per invalidated decision); each invalidation is recorded, with the
        run (its folder name) like every other record."""
        before = {k: v.invalidated for k, v in self.units.items()}
        self.conflicts = {}
        messages = self._reconcile(log)
        for unit_id, decision in sorted(self.units.items()):
            if decision.invalidated and decision.invalidated != before.get(unit_id):
                self.record(log, unit_id, "INVALIDATED", [], run=run, reason=decision.invalidated,
                            conflict=self.conflicts.get(unit_id))
        return messages

    def _void(self, unit_id: str, cause: str, reason: str, *, shots: Iterable[str] = (), other: str | None = None) -> str:
        """Invalidate the decision on ``unit_id`` for ``cause``; the conflict record says what to do (P2-N5)."""
        self.units[unit_id].invalidated = reason
        self.conflicts[unit_id] = {"cause": cause, "with": other, "shots": list(shots)[:MAX_IDS],
                                   "options": list(CONFLICT_OPTIONS.get(cause, CONFLICT_OPTIONS["plan_changed"]))}
        return f"Câu {unit_id[1:]}: {reason}"

    def _reconcile(self, log: Mapping[str, Any]) -> list[str]:
        units = {u["unit_id"]: u for u in log.get("units") or ()}
        known = {c["id"] for c in log.get("candidates") or ()}
        messages = []
        for unit_id, decision in list(self.units.items()):
            unit = units.get(unit_id)
            if unit is None:
                if not decision.empty:
                    messages.append(self._void(unit_id, "sentence_removed", "Câu này không còn trong kịch bản."))
                continue
            gone = [i for i in [*decision.pin, *decision.exclude] if i not in known]
            if gone:
                messages.append(self._void(unit_id, "shot_gone", "Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại.",
                                           shots=gone))
                continue
            # each shot once, as an approval is stored: a shot the sentence shows twice is not another plan (rc12
            # W5: i1.u1 chose [c3f1, c3f1, c2f1] and its approval [c3f1, c2f1] was voided as plan_changed)
            chosen = list(dict.fromkeys(unit.get("chosen") or []))
            # A pin approved and then applied by a re-render is still approved (P2-5): a pin resets an earlier
            # approval, so an approval that sits with a pin was given to the pinned shots. It goes only when the
            # sentence no longer shows them (the plan could not honour the pin) or shows something else.
            if decision.approved is not None and decision.pin and _applied(decision.pin, chosen):
                if decision.approved != chosen or not decision.basis:
                    decision.approved, decision.pieces, decision.basis = chosen, pieces_of(unit), basis(unit, log)
            if decision.approved is not None:
                # what the approval was given on: the sentence and the content of its sources (P2-N5 materiality: a
                # new plan revision alone is no reason; a changed sentence or source is)
                now = basis(unit, log)
                if decision.basis and now[:8] != decision.basis[:8]:
                    decision.approved = None
                    messages.append(self._void(unit_id, "sentence_changed", "Lời câu đã đổi sau khi bạn duyệt; hãy xem lại."))
                    continue
                if decision.basis and list(decision.approved) == chosen and _material(decision.basis) is not None \
                        and _material(now) is not None and _material(decision.basis) not in (
                            _material(now), _material(basis(unit, log, repeats=True))):
                    decision.approved = None
                    messages.append(self._void(unit_id, "source_changed",
                                               "Nội dung nguồn của cảnh đã đổi sau khi bạn duyệt; hãy xem lại.", shots=chosen))
                    continue
            playing = pieces_of(unit)
            moved = decision.approved is not None and list(decision.approved) == chosen and decision.pieces is not None \
                and playing is not None and playing != decision.pieces  # another range, or now shown twice
            if moved and sum(p[2] for p in decision.pieces) != sum(p[2] for p in playing):
                # the sentence is spoken longer or shorter (another voice or speed): the approved pieces no longer fit it
                decision.approved = None
                messages.append(self._void(unit_id, "timing_changed", "Thời lượng câu đã đổi sau khi bạn duyệt; hãy xem lại.",
                                           shots=chosen))
                continue
            pinned_elsewhere = {i for other, ids in (log.get("edits") or {}).get("pin", {}).items() if other != unit_id for i in ids}
            if moved and [p[:3] for p in playing] == [p[:3] for p in decision.pieces] and not pinned_elsewhere & set(chosen):
                # the same footage, now behind what has already played (forward only: another sentence's new footage
                # comes from later in the source): it is shown again, which the approval was not given on
                decision.approved = None
                messages.append(self._void(unit_id, "shown_again", "Cảnh bạn đã duyệt giờ chiếu lại đoạn đã chiếu (do câu khác đổi "
                                           "cảnh); hãy xem lại.", shots=chosen))
                continue
            if decision.approved is not None and (list(decision.approved) != chosen or moved):
                # the same shots from another part of them are other footage: never a silent change
                approved, decision.approved = list(decision.approved), None
                edits = log.get("edits") or {}
                candidates = {c["id"]: c for c in log.get("candidates") or ()}
                excluded = [i for i in approved if i in set(edits.get("exclude") or ())
                            or (candidates.get(i) or {}).get("source_id") in set(edits.get("exclude_sources") or ())]
                by = next((other for other, d in sorted(self.units.items()) if other != unit_id
                           and set(excluded) & set(d.exclude)), None)
                taken = {i: other for other, ids in sorted((edits.get("pin") or {}).items()) if other != unit_id
                         for i in ids if i in approved}
                if excluded:
                    where = f" ở câu {by[1:]}" if by else ""
                    messages.append(self._void(unit_id, "excluded", f"Cảnh bạn đã duyệt bị loại trừ{where}; hãy chọn cảnh khác "
                                               "hoặc bỏ loại trừ.", shots=excluded, other=by))
                elif taken:
                    other = sorted(taken.values())[0]
                    messages.append(self._void(unit_id, "pinned_elsewhere", f"Cảnh bạn đã duyệt đã được ghim cho câu {other[1:]}; "
                                               "hãy chọn cảnh khác cho câu này.", shots=sorted(taken), other=other))
                elif any(i not in known for i in approved):
                    messages.append(self._void(unit_id, "shot_gone", "Cảnh bạn đã duyệt không còn trong nguồn; hãy chọn lại.",
                                               shots=[i for i in approved if i not in known]))
                elif decision.pieces and playing and sum(p[2] for p in decision.pieces) != sum(p[2] for p in playing):
                    # the sentence is spoken longer or shorter: its footage is planned again for the new length
                    messages.append(self._void(unit_id, "timing_changed", "Thời lượng câu đã đổi sau khi bạn duyệt; hãy xem lại.",
                                               shots=approved))
                else:
                    messages.append(self._void(unit_id, "plan_changed", "Cảnh của câu đã đổi sau khi bạn duyệt; hãy xem lại.",
                                               shots=approved))
                continue
            if decision.approved is not None and decision.approved == chosen:
                # an approval that holds is given on what plays now (and an approval saved before the pieces were
                # logged gets them here, so the next replan keeps it exactly)
                decision.pieces = pieces_of(unit) if unit.get("pieces") else decision.pieces
                if _material(decision.basis) is None or not decision.basis \
                        or _material(decision.basis) != _material(basis(unit, log)):
                    # an approval from before the sources' content was logged, or taken the rc12 way (a repeat hashed
                    # twice; the material was found unchanged above)
                    decision.basis = basis(unit, log)
            if decision.invalidated and decision.pin and all(i in known for i in decision.pin):
                decision.invalidated = ""
        return messages


def _material(fingerprint: str) -> str | None:
    """The material half of a :func:`basis`, or None when it was taken without the sources' content identity (a log
    from before it was written, or a basis from before it was marked): such a half cannot tell a changed source."""
    return fingerprint[8:16] if len(fingerprint) == 17 and fingerprint[16] == "c" else None


def _applied(pin: Iterable[str], chosen: list[str]) -> bool:
    """Whether the sentence opens with every pinned shot, in the pin's order: the plan honoured the pin. A pinned
    shot shorter than the sentence is followed by what the planner adds to fill it (rc5: Câu 1 pinned to v1c1 shows
    v1c1 then v1c2); that filling is the pin applied, not a change of the user's decision."""
    pinned = list(dict.fromkeys(pin))
    return bool(pinned) and list(dict.fromkeys(chosen))[:len(pinned)] == pinned


def user_state(decision: UnitDecision | None) -> str | None:
    if decision is None:
        return None
    if decision.invalidated:
        return INVALIDATED
    if decision.source_missing:
        return USER_SOURCE_MISSING
    if decision.approved is not None:
        return USER_APPROVED
    return None


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pieces(value: Any) -> list[list[Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_IDS or any(
            not isinstance(p, list) or len(p) not in (3, 4) or type(p[0]) is not str or not 0 < len(p[0]) <= 64
            or any(type(n) is not int or n < 0 for n in p[1:3]) or (len(p) == 4 and p[3] is not True) for p in value):
        raise ReviewError("pieces must list [shot, offset, length, (shown again)]")
    return [list(p) for p in value]


def pieces_of(unit: Mapping[str, Any]) -> list[list[Any]] | None:
    """The pieces a sentence plays ([shot, source offset ms, length ms] and ``True`` for a piece shown again), when
    the log has them."""
    found = unit.get("pieces")
    if not isinstance(found, list) or not found:
        return None
    return [[p["id"], int(p["offset_ms"]), int(p["length_ms"]), *([True] if p.get("repeat") else [])] for p in found][:MAX_IDS]


def basis(unit: Mapping[str, Any], log: Mapping[str, Any], *, repeats: bool = False) -> str:
    """What an approval is given on, in two halves: the sentence (its words) and the material it shows (each shot's
    source, range and the source's content identity when the log has it). A new plan revision is neither.

    ``repeats``: hash a shot shown twice twice, as builds up to 1.1.96-rc12 did (a basis they saved is read as it
    was taken, never as a changed source)."""
    candidates = {c["id"]: c for c in log.get("candidates") or ()}
    identity = log.get("sources") or {}
    words = align.words_digest(str(unit.get("unit_id")), str(unit.get("text")))
    shown = [(i, (candidates.get(i) or {}).get("source_id"), (candidates.get(i) or {}).get("start_ms"),
              (candidates.get(i) or {}).get("end_ms"), identity.get((candidates.get(i) or {}).get("source_id") or ""))
             for i in (list if repeats else dict.fromkeys)(unit.get("chosen") or ())]  # each shot once, as an approval names it
    # comparable only when every shown source's content was read: a source the log leaves out (unreadable when the log
    # was written) has an unknown identity, never a stand-in that a new analysis recipe would change
    content = bool(identity) and log.get("source_identity") == "content" and all(s[4] is not None for s in shown)
    material = hashlib.sha256(json.dumps(shown, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    # "c": the material half holds the sources' content identity (not the analysis recipe), so it can tell a changed
    # source; "-": it cannot, and is not compared (P2-N5)
    return words[:8] + material[:8] + ("c" if content else "-")


def fingerprint(unit: Mapping[str, Any], candidates: Mapping[str, Mapping[str, Any]]) -> str:
    """What a decision was made on: the sentence and the shots it showed (ids and source ranges)."""
    shown = [(i, (candidates.get(i) or {}).get("source_id"), (candidates.get(i) or {}).get("start_ms"),
              (candidates.get(i) or {}).get("end_ms")) for i in unit.get("chosen") or ()]
    payload = json.dumps([unit.get("unit_id"), unit.get("text"), shown], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _not_ready(log: Mapping[str, Any]) -> list[str]:
    """Units the planner did not mark READY (in an honest mode, its word; otherwise recomputed)."""
    honest = content_mode(log) == log.get("mode")
    out = []
    for unit in log.get("units") or ():
        state = (unit.get("review") or {}).get("state") if honest else unit_state(unit, log)
        if state not in (None, READY):
            out.append(unit["unit_id"])
    return out


def _settled(decision: UnitDecision | None) -> bool:
    return decision is not None and not decision.invalidated and (decision.approved is not None or bool(decision.pin)
                                                                   or decision.source_missing)


def unreviewed(log: Mapping[str, Any], review: Review) -> list[str]:
    """Units the planner is not sure of and the user has neither approved nor replaced (pinned)."""
    return [u for u in _not_ready(log) if not _settled(review.units.get(u))]


def settled_by_user(log: Mapping[str, Any], review: Review) -> list[str]:
    """Units the planner was not sure of that the user settled: the user's word, never an automatic pass (P1-N3)."""
    return [u for u in _not_ready(log) if _settled(review.units.get(u))]


_CLAIM_NOTES = ("không thấy sản phẩm",)


def priority(unit: Mapping[str, Any], state: str, decision: UnitDecision | None, mode: str,
             warnings: Iterable[str] = ()) -> tuple[str | None, list[str]]:
    """The sentence's place in the review queue and why (None: the user has settled it). Uncalibrated."""
    if decision is not None and not decision.invalidated and (decision.approved is not None or decision.pin
                                                               or decision.source_missing):
        return None, []
    reasons: list[str] = []
    number = re.search(r"(\d+)$", str(unit.get("unit_id") or ""))
    said = [w for w in warnings if number and re.match(rf"Câu {number.group(1)}\b", w)]
    if decision is not None and decision.invalidated:
        reasons.append("lựa chọn trước không còn hợp lệ: " + decision.invalidated)
    if mode != "content":
        reasons.append("chưa có bằng chứng nội dung (AI chưa nhìn được hình)")
    if state in (MATCH_NOT_FOUND, SOURCE_INSUFFICIENT):
        reasons.append("thiếu cảnh cho câu này" if state == SOURCE_INSUFFICIENT else "chưa tìm thấy cảnh khớp")
    notes = [str(n) for n in unit.get("notes") or ()]
    if any(cue in n for n in notes for cue in _CLAIM_NOTES):
        reasons.append("câu nêu thông tin cụ thể nhưng không có bằng chứng hình")
    reasons += [w for w in said if "nhưng thông tin sản phẩm không có" in w]
    if reasons:
        return CRITICAL, reasons
    if state == NEEDS_REVIEW:
        return REVIEW, notes[:3] or ["AI chưa chắc về cảnh của câu này"]
    return OPTIONAL, []


def queue(units: list[dict[str, Any]], review: Review) -> list[str]:
    """The sentences still to look at, most serious first; one put off for later goes to the end of its group."""
    waiting = [u for u in units if u.get("priority")]
    later = {k for k, v in review.units.items() if v.later}
    waiting.sort(key=lambda u: (PRIORITY_ORDER[u["priority"]], u["unit_id"] in later, units.index(u)))
    return [u["unit_id"] for u in waiting]


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
    texts_all = list(dict.fromkeys([*warnings, *(log.get("warnings") or [])]))
    units = []
    for unit in log.get("units") or ():
        decision = review.units.get(unit["unit_id"])
        stored = unit.get("review") if content_mode(log) == log.get("mode") else None  # an old file-name-only log
        verdict = stored or {"evidence": evidence_state(unit, log), "state": unit_state(unit, log)}
        rank, why = priority(unit, verdict["state"], decision, content_mode(log), texts_all)
        units.append({
            "priority": rank, "queue_reasons": why, "later": bool(decision and decision.later),
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
    return {"schema_version": 1, "kind": "editorial_review_view", "plan_id": log.get("plan_id"), "mode": content_mode(log),
            "provenance": log.get("provenance") or provenance_of(log),
            "revision": review.revision, "can_undo": bool(review.history), "units": units,
            "warnings": [{"id": warning_id(t), "text": t, "acknowledged": warning_id(t) in acknowledged} for t in texts],
            "unreviewed": [u for u in (x["unit_id"] for x in units) if u in open_units],
            "accept_unreviewed": review.accept_unreviewed, "summary": log.get("review_summary") or {},
            # the review queue (its groups are not calibrated), where the user was, and every shot to choose from
            "queue": queue(units, review), "queue_calibrated": False, "position": review.position,
            "shots": [s for s in (shot(c["id"]) for c in (log.get("candidates") or ())[:MAX_SHOTS]) if s and s["usable"]]}


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
    decide_p.add_argument("what", choices=["approve", "unapprove", "pin", "exclude", "reset", "later", "missing", "focus"])
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
        run = work.parent.name  # the run folder: what the decision record names the run by (never a full path)
        if args.action == "decide":
            review.decide(log, args.unit, args.what, args.ids, run=run)
        elif args.action == "undo":
            review.undo(log, run=run)
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


__all__ = ["CRITICAL", "DECISION_TYPES", "OPTIONAL", "REVIEW", "USER_SOURCE_MISSING", "fingerprint", "priority", "queue",
           "INVALIDATED", "MATCH_AVAILABLE", "MATCH_NOT_FOUND", "NEEDS_REVIEW", "NOT_NEEDED", "READY", "Review",
           "ReviewError", "SOURCE_INSUFFICIENT", "USER_APPROVED", "UnitDecision", "annotate", "evidence_state", "load",
           "part_path", "path_for", "save", "unit_state", "unreviewed", "user_state", "main", "view", "warning_id"]


if __name__ == "__main__":
    raise SystemExit(main())
