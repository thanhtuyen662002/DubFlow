"""Script units and the footage that illustrates each one (ADR-0054 §4–6).

A narration is spoken sentence by sentence; each sentence is a *unit* on the
output timeline, from the middle of the pause before it to the middle of the
pause after it (the first from 0, the last to the end), so pictures change
between sentences, not inside a word. Each unit has a *function* (hook,
benefit, demonstration, feature, result, call to action, or plain narration)
from its position and its own words.

For a unit, every candidate gets a score in milli:

* relevance (45 %): the unit's terms found in the candidate's evidence, each
  weighted by how rare it is among the candidates (a product name on every clip
  says little) and by the evidence's trust;
* role fit (25 %): how well the candidate's labels suit the unit's function;
* quality (15 %), length fit (5 %);
* motion only as a small extra for hooks and quick narration (never alone);
* minus reuse, duplicate and same-source-as-before penalties.

Units are filled in order. A workflow without ``reorder`` keeps each source
playing forward. A user's edits pin candidates to a unit or exclude candidates
or sources before scoring. When no candidate has any content evidence, the
result is *signals only*: chosen by quality and diversity, every piece capped at
confidence 450 and marked for review, and the decision log says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

from . import evidence as ev
from .candidates import Candidate
from .policy import Pacing

FUNCTIONS = ("hook", "benefit", "demonstration", "feature", "result", "cta", "narration", "intro", "outro")
SIGNALS_ONLY_CAP = 450
REVIEW_BELOW = 600
REUSE_PENALTY = 350
DUPLICATE_PENALTY = 250
SAME_SOURCE_PENALTY = 100

# Folded cue words for a sentence's function (Vietnamese sales language).
CUES = {
    "cta": ("mua ngay", "dat hang", "dat mua", "chot don", "inbox", "lien he", "link", "gio hang", "nhanh tay", "uu dai",
            "giam gia", "khuyen mai", "qua tang", "chi con", "so luong co han", "freeship", "mien phi van chuyen", "hom nay"),
    "demonstration": ("chi can", "cho vao", "bam", "nhan", "xoay", "lap", "thao", "cach dung", "su dung", "dat vao", "do vao",
                      "cam dien", "bat", "mo nap", "dong nap", "keo", "van"),
    "feature": ("dung tich", "cong suat", "chat lieu", "chong dinh", "tinh nang", "che do", "cong nghe", "thiet ke", "kich thuoc",
                "trong luong", "pin", "inox", "lit", "watt", "w ", "cam ung", "man hinh", "bao hanh"),
    "result": ("ket qua", "gion", "thom", "sach", "chin deu", "vang deu", "mem", "min", "thanh pham", "sau khi", "chi sau",
               "trong vong", "tiet kiem", "nhanh chong"),
}
FIT: Mapping[str, Mapping[str, int]] = {
    "hook": {"product_result": 1000, "product_demonstration": 850, "product_feature": 800, "product_closeup": 700,
             "product_overview": 550, "reveal": 1000, "action": 900, "reaction": 800},
    "benefit": {"product_result": 900, "product_demonstration": 800, "product_overview": 650, "product_closeup": 550,
                "product_feature": 600},
    "demonstration": {"product_demonstration": 1000, "product_feature": 700, "product_closeup": 450, "action": 700},
    "feature": {"product_feature": 1000, "product_closeup": 900, "product_demonstration": 700},
    "result": {"product_result": 1000, "product_demonstration": 500, "product_closeup": 400},
    "cta": {"product_overview": 1000, "product_packaging": 900, "product_closeup": 600, "product_result": 600},
    "intro": {"establishing": 1000, "people": 600, "action": 600, "object": 500},
    "narration": {"action": 800, "reveal": 800, "explanation": 750, "reaction": 600, "establishing": 500, "people": 500,
                  "object": 500, "text_on_screen": 400},
    "outro": {"reaction": 900, "establishing": 800, "people": 600, "action": 500},
}


@dataclass(frozen=True)
class Unit:
    unit_id: str
    text: str
    start_ms: int
    end_ms: int
    function: str

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


def product_function(index: int, count: int, text: str) -> str:
    """A sales sentence's function: the first is the hook; then by its cue words; otherwise a benefit."""
    if index == 0:
        return "hook"
    folded = f" {ev.fold(text)} "
    if any(f" {cue} " in folded for cue in CUES["cta"]) and index >= count // 2:
        return "cta"
    for function in ("demonstration", "result", "feature"):
        if any(f" {cue.strip()} " in folded for cue in CUES[function]):
            return function
    return "benefit"


def units(spoken: Sequence[tuple[str, int]], total_ms: int, *, lead_ms: int, gap_ms: int,
          function: Any = None, prefix: str = "u") -> list[Unit]:
    """The sentences as contiguous units covering ``[0, total_ms)``; cuts sit in the middle of each pause."""
    if not spoken:
        return []
    starts, cursor = [], lead_ms
    for _, duration in spoken:
        starts.append(cursor)
        cursor += duration + gap_ms
    bounds = [0] + [start - gap_ms // 2 for start in starts[1:]] + [total_ms]
    classify = function or (lambda index, count, text: "narration")
    made = []
    for index, (text, _) in enumerate(spoken):
        if bounds[index + 1] > bounds[index]:
            made.append(Unit(f"{prefix}{index + 1}", text, bounds[index], bounds[index + 1], classify(index, len(spoken), text)))
    return made


@dataclass
class Edits:
    """A user's changes to the choice of footage (``editorial.edits``)."""

    pin: dict[str, list[str]] = field(default_factory=dict)   # unit id → candidate ids, in order
    exclude: set[str] = field(default_factory=set)             # candidate ids
    exclude_sources: set[str] = field(default_factory=set)     # source ids

    @classmethod
    def from_dict(cls, value: Any) -> "Edits":
        if value is None:
            return cls()
        if not isinstance(value, Mapping) or set(value) - {"pin", "exclude", "exclude_sources"}:
            raise ValueError("editorial.edits must have only pin, exclude, exclude_sources")
        pin = value.get("pin", {})
        if not isinstance(pin, Mapping) or len(pin) > 500:
            raise ValueError("editorial.edits.pin must map unit ids to candidate ids")
        out = cls()
        for unit_id, ids in pin.items():
            if type(unit_id) is not str or not isinstance(ids, list) or len(ids) > 20 or not all(type(i) is str and i for i in ids):
                raise ValueError("editorial.edits.pin must map unit ids to candidate ids")
            out.pin[unit_id] = list(ids)
        for name in ("exclude", "exclude_sources"):
            ids = value.get(name, [])
            if not isinstance(ids, list) or len(ids) > 2000 or not all(type(i) is str and i for i in ids):
                raise ValueError(f"editorial.edits.{name} must be a list of ids")
            setattr(out, name, set(ids))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"pin": self.pin, "exclude": sorted(self.exclude), "exclude_sources": sorted(self.exclude_sources)}


@dataclass(frozen=True)
class Choice:
    unit_id: str
    candidate: Candidate
    offset_ms: int
    length_ms: int
    score: int
    confidence: int
    needs_review: bool
    reasons: tuple[str, ...]


@dataclass
class Decision:
    """The log of one unit: what was chosen, the next best, and why."""

    unit: Unit
    chosen: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"unit_id": self.unit.unit_id, "text": self.unit.text, "function": self.unit.function,
                "start_ms": self.unit.start_ms, "end_ms": self.unit.end_ms, "chosen": self.chosen,
                "alternatives": self.alternatives, "notes": self.notes}


class Scorer:
    """Scores candidates for units from their evidence (idf over the candidates of this job)."""

    def __init__(self, candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence]) -> None:
        self.candidates = list(candidates)
        self.evidence = evidence
        self.terms = {c.candidate_id: evidence[c.candidate_id].terms() if c.candidate_id in evidence else {} for c in self.candidates}
        self.loose = {key: ev.loose(found) for key, found in self.terms.items()}
        self.labels = {c.candidate_id: evidence[c.candidate_id].labels() if c.candidate_id in evidence else {} for c in self.candidates}
        frequency: dict[str, int] = {}
        for key, found in self.terms.items():
            for term in {*found, *self.loose[key]}:
                frequency[term] = frequency.get(term, 0) + 1
        count = max(1, len(self.candidates))
        self.idf = {term: math.log(1 + count / (1 + seen)) for term, seen in frequency.items()}
        self.default_idf = math.log(1 + count)
        self.content = any(evidence[c.candidate_id].content for c in self.candidates if c.candidate_id in evidence)

    def relevance(self, unit: Unit, candidate: Candidate) -> tuple[int, list[str]]:
        """0–1000 and the matched words (for the reason)."""
        wanted = ev.terms(unit.text)
        if not wanted:
            return 0, []
        found, loose = self.terms[candidate.candidate_id], self.loose[candidate.candidate_id]
        matched, total, words = 0.0, 0.0, []
        for term in wanted:
            weight = self.idf.get(term, self.default_idf) * (2 if " " in term else 1)
            total += weight
            value = ev.match(term, found, loose)
            if value:
                matched += weight * value / 1000
                words.append(term.removeprefix(ev.LOOSE))
        if not matched:
            return 0, []
        value = min(1.0, matched / max(1.5, 0.45 * total))
        words.sort(key=lambda w: (-(" " in w), -self.idf.get(w, 0), w))
        return round(value * 1000), words[:4]

    def fit(self, unit: Unit, candidate: Candidate) -> tuple[int, str | None]:
        table = FIT.get(unit.function, {})
        best, label = 0, None
        for name, confidence in self.labels[candidate.candidate_id].items():
            value = table.get(name, 0) * confidence // 1000
            if value > best:
                best, label = value, name
        return best, label


def _length_fit(candidate_ms: int, wanted_ms: int) -> int:
    return 1000 if candidate_ms >= wanted_ms else candidate_ms * 1000 // max(1, wanted_ms)


def assign(found_units: Sequence[Unit], candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence], pacing: Pacing,
           *, forward_only: bool, edits: Edits | None = None, groups: Mapping[str, str] | None = None,
           stills: Iterable[str] = ()) -> tuple[list[Choice], list[Decision], str]:
    """Footage for every unit, the decision log and the mode (``content`` or ``signals_only``).

    ``stills`` are candidate ids of photos: they can be held as long as needed. ``groups`` maps a candidate to its
    duplicate group. ``forward_only`` keeps each source playing forward (no reorder); when a source has nothing
    left ahead, its earliest unused footage is shown again and flagged.
    """
    edits = edits or Edits()
    groups = groups or {}
    still_ids = set(stills)
    pool = [c for c in candidates if c.candidate_id not in edits.exclude and c.source_id not in edits.exclude_sources]
    by_id = {c.candidate_id: c for c in candidates}
    if not pool:
        raise ValueError("no footage left after the edits")
    scorer = Scorer(pool, evidence)
    mode = "content" if scorer.content else "signals_only"
    usable = [c for c in pool if c.quality.usable] or pool
    uses: dict[str, int] = {}
    group_uses: dict[str, int] = {}
    resume: dict[str, int] = {}
    source_end: dict[str, int] = {}
    choices: list[Choice] = []
    decisions: list[Decision] = []
    previous_source: str | None = None

    def remaining(candidate: Candidate) -> tuple[int, int]:
        if candidate.candidate_id in still_ids:
            return 0, 10**9
        start = resume.get(candidate.candidate_id, candidate.start_ms)
        return start, candidate.end_ms - start

    def allowed(candidate: Candidate) -> bool:
        if not forward_only or candidate.candidate_id in still_ids:
            return True
        return candidate.start_ms >= source_end.get(candidate.source_id, 0) or (
            candidate.candidate_id in resume and resume[candidate.candidate_id] < candidate.end_ms
            and resume[candidate.candidate_id] >= source_end.get(candidate.source_id, 0))

    def score(unit: Unit, candidate: Candidate, wanted: int) -> tuple[int, int, list[str]]:
        relevance, matched = scorer.relevance(unit, candidate)
        fit, label = scorer.fit(unit, candidate)
        quality = candidate.quality.score
        _, left = remaining(candidate)
        total = 450 * relevance + 250 * fit + 150 * quality + 50 * _length_fit(left, min(wanted, pacing.target))
        total //= 1000
        info = evidence.get(candidate.candidate_id)
        if unit.function == "hook":
            total += min(60, candidate.motion // 4) + (info.hook // 10 if info else 0)
        elif unit.function == "narration":
            total += min(30, candidate.motion // 8)
        total -= REUSE_PENALTY * uses.get(candidate.candidate_id, 0)
        total -= DUPLICATE_PENALTY * (group_uses.get(groups.get(candidate.candidate_id, candidate.candidate_id), 0)
                                      - uses.get(candidate.candidate_id, 0))
        if candidate.source_id == previous_source and len({c.source_id for c in usable}) > 1:
            total -= SAME_SOURCE_PENALTY
        reasons = []
        if matched:
            origins = info.origins() if info else []
            reasons.append(f"khớp lời “{', '.join(matched)}” ({', '.join(origins[:2]) or 'mô tả'})")
        if label:
            reasons.append(f"cảnh {ev.LABEL_NAMES_VI.get(label, label)} hợp câu {unit.function}")
        if not candidate.quality.usable:
            reasons.append("chất lượng thấp: " + "; ".join(candidate.quality.problems))
        if mode == "content":
            confidence = 250 + (550 * relevance + 300 * fit) // 1000
            if info:
                confidence = min(confidence, max([i.confidence * ev.TRUST[i.origin] // 1000 for i in info.items if i.origin != "signal"]
                                                 or [SIGNALS_ONLY_CAP]) + 150)
        else:
            confidence = min(SIGNALS_ONLY_CAP, 250 + quality // 5)
        return total, max(0, min(1000, confidence)), reasons

    for unit in found_units:
        decision = Decision(unit)
        need = unit.length_ms
        pinned = [by_id[i] for i in edits.pin.get(unit.unit_id, []) if i in by_id]
        if len(pinned) != len(edits.pin.get(unit.unit_id, [])):
            decision.notes.append("một số cảnh đã ghim không còn trong danh sách nên được bỏ qua")
        first = True
        while need > 0:
            wanted = need if need < pacing.target + pacing.minimum else pacing.target
            ranked = []
            if pinned:
                candidate = pinned.pop(0)
                total, confidence, reasons = 1000, 1000, ["chọn bởi người dùng"]
                ranked = [(total, confidence, reasons, candidate)]
            else:
                open_pool = [c for c in usable if allowed(c) and remaining(c)[1] >= min(pacing.minimum, need)]
                if not open_pool:  # nothing long enough ahead: anything left, then repeats
                    open_pool = [c for c in usable if allowed(c) and remaining(c)[1] > 0] or usable
                for candidate in open_pool:
                    total, confidence, reasons = score(unit, candidate, wanted)
                    ranked.append((total, confidence, reasons, candidate))
                ranked.sort(key=lambda item: (-item[0], item[3].source_id, item[3].start_ms))
            total, confidence, reasons, candidate = ranked[0]
            if first:
                decision.alternatives = [{"candidate_id": c.candidate_id, "score": s, "reasons": list(r)}
                                         for s, _, r, c in ranked[1:4]]
                first = False
            offset, left = remaining(candidate)
            repeat = left <= 0 or (forward_only and candidate.candidate_id not in still_ids and not allowed(candidate))
            if left <= 0:
                offset, left = candidate.start_ms, candidate.length_ms
            length = min(wanted, left) if candidate.candidate_id not in still_ids else wanted
            if need - length < pacing.minimum and left >= need:
                length = need  # take the rest rather than leave a sliver
            reasons = list(reasons)
            if repeat or uses.get(candidate.candidate_id):
                reasons.append("dùng lại cảnh đã chiếu (không còn cảnh phù hợp khác)")
                confidence = min(confidence, 550)
            if mode == "signals_only":
                reasons.append("chưa có bằng chứng nội dung: chọn theo điểm cắt, độ nét, đa dạng")
            needs_review = confidence < REVIEW_BELOW or not candidate.quality.usable
            choices.append(Choice(unit.unit_id, candidate, offset, length, total, confidence, needs_review, tuple(reasons[:8])))
            decision.chosen.append(candidate.candidate_id)
            uses[candidate.candidate_id] = uses.get(candidate.candidate_id, 0) + 1
            group = groups.get(candidate.candidate_id, candidate.candidate_id)
            group_uses[group] = group_uses.get(group, 0) + 1
            if candidate.candidate_id not in still_ids:
                resume[candidate.candidate_id] = offset + length
                source_end[candidate.source_id] = max(source_end.get(candidate.source_id, 0), offset + length)
            previous_source = candidate.source_id
            need -= length
        decisions.append(decision)
    return choices, decisions, mode


__all__ = ["Choice", "Decision", "Edits", "FUNCTIONS", "Scorer", "Unit", "assign", "product_function", "units"]
