"""Planning-time frame targeting for the offline Stage 2 harness (#815): which extra frames a run would look at.

Pure functions only: no files, no network, no model, no randomness. The input is what a run itself saved and a
replay of it produced (:class:`RunData`), turned into a :class:`PlanningView`; :func:`select` picks extra frames from
that view alone. Nothing the scorer reads ever reaches this module: ``targeted.py`` builds the view, calls
:func:`select`, and only then scores what was selected.

Strategies (``SelectParams.strategy``):

* ``uniform``: the production first look (``vision.allocate``) over the shots nobody has looked at;
* ``production``: the production targeted look (``vision.targets``, forward only), the budget shared by the parts;
* ``prior``: around where the sentence sits in its part, mapped onto the item's source trim;
* ``shuffled_prior``: the same centres, cyclically shifted between the sentences of a part (a negative control);
* ``anchor``: around the looked-at shot that best matches the sentence (``prior`` when none matches);
* ``gap``: evenly over the unseen shots of the sentence's forward window;
* ``mixed``: per sentence, non-vision matches, then ``anchor``, then ``prior``, then ``gap``;
* ``all_eligible``: every eligible shot (the ceiling arm; no budget).

Every strategy takes the same fair turns: each round gives one frame to each part that still has a proposal, the
sentences of a part take turns, and when fewer frames are left than parts the parts are served in ``spread_order``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from engine.dubflow.mastering.align import Scorer, Unit
from engine.dubflow.mastering.candidates import MAX_CANDIDATES, WITHIN_BASE, Candidate, _all_windows, numbered
from engine.dubflow.mastering.evidence import Evidence
from engine.dubflow.mastering.frames import SPLIT_MARK, extra_times
from engine.dubflow.mastering.vision import allocate, spread_order, targets

STRATEGIES = ("uniform", "production", "prior", "shuffled_prior", "anchor", "gap", "mixed", "all_eligible")
TARGET_UNITS = ("flagged", "not_ready")
SIGNALS_UNAVAILABLE = ("source_cues", "ocr", "per_sentence_citation_spans")
NUMBERED_FROM = WITHIN_BASE  # ids of a trimmed long source are numbered from here (candidates.numbered)


@dataclass(frozen=True)
class RunData:
    """A replay's own output, nothing else: no handle on any file and nothing the scorer reads."""

    name: str
    basis: str                                        # "A_replay_no_review" | "A0_replay_no_review"
    order: tuple[str, ...]
    logs: Mapping[str, Mapping[str, Any]]
    plans: Mapping[str, Mapping[str, Any]]
    spec: Mapping[str, Any]
    analyses: Mapping[str, Any]                       # identity -> scene.Analysis
    evidence: Mapping[str, Mapping[str, Evidence]]    # part -> candidate id -> evidence planned with
    trims: Mapping[str, tuple[tuple[str, int, int], ...]]
    source_keys: Mapping[str, Mapping[str, str]]      # part -> source id -> source key
    restrict_to: frozenset[str] | None = None         # closed-world regime only (donor-covered shots)


@dataclass(frozen=True)
class ShotView:
    candidate_id: str
    source_id: str
    source_key: str
    start_ms: int
    end_ms: int
    keyframe_ms: int
    keyframe_basis: str                               # "numbered_window" | "subshot_point" | "logged_mid"
    usable: bool
    has_keyframe: bool
    group: str
    observed: bool                                    # job-wide, over the item parts' evidence
    candidate: Candidate
    window: tuple[int, int] | None = None             # the unclipped numbered window (a sub-shot's parent window)


@dataclass(frozen=True)
class UnitView:
    unit_id: str
    text: str
    function: str
    start_ms: int
    end_ms: int
    chosen: tuple[str, ...]
    alternatives: tuple[str, ...]
    coverage_status: str
    needs_review: bool                                # the production coverage flag (not a user decision)
    coverage_milli: int


@dataclass(frozen=True)
class PartView:
    part: str
    kind: str
    narration_ms: int
    trims: tuple[tuple[str, int, int], ...]
    cuts: tuple[tuple[str, tuple[int, ...]], ...]
    cues: tuple[tuple[int, int, str], ...]
    units: tuple[UnitView, ...]
    shots: tuple[ShotView, ...]
    evidence: Mapping[str, Evidence]


@dataclass(frozen=True)
class PlanningView:
    run_name: str
    basis: str
    parts: tuple[PartView, ...]
    signals_unavailable: tuple[str, ...]
    eligible_restriction: str                         # "open" | "closed_world_donor_covered"
    restrict_to: frozenset[str] | None = None
    # what is derived from the view alone, computed once (a Scorer per part, the eligible shots, the groups seen)
    derived: dict[Any, Any] = field(default_factory=dict, compare=False, repr=False)


def _once(view: PlanningView, key: Any, make: Any) -> Any:
    if key not in view.derived:
        view.derived[key] = make()
    return view.derived[key]


@dataclass(frozen=True)
class SelectParams:
    strategy: str
    budget: int | None                                # extra frames per job; None only for "all_eligible"
    window_ms: int | None                             # None: the whole forward window
    target_units: str = "flagged"
    frames: str = "mid"


@dataclass(frozen=True)
class Sample:
    part: str
    candidate_id: str
    source_id: str
    source_key: str
    t_ms: int
    t_basis: str
    unit_id: str | None
    reason: str
    turn: int


@dataclass(frozen=True)
class Selection:
    samples: tuple[Sample, ...]
    eligible: tuple[str, ...]                         # what the strategy could propose, one id per duplicate group
    notes: Mapping[str, Any]


# --- ids and keyframe times ---

def split_id(candidate_id: str, source_id: str) -> tuple[int | None, int | None]:
    """(window number, sub-shot number) of ``<source>c<N>`` / ``<source>c<N>f<n>``; None where the id says nothing."""
    rest = candidate_id[len(source_id):] if candidate_id.startswith(source_id) else ""
    if not rest.startswith("c"):
        return None, None
    body = rest[1:]
    number, sub = (body.split(SPLIT_MARK, 1) + [""])[:2] if SPLIT_MARK in body else (body, "")
    if not number.isdigit():
        return None, None
    return int(number), (int(sub) if sub.isdigit() else None)


def parent_id(candidate_id: str, source_id: str) -> str:
    number, sub = split_id(candidate_id, source_id)
    return f"{source_id}c{number}" if number is not None and sub is not None else candidate_id


def windows_by_number(analysis: Any, trim: tuple[int, int] | None) -> dict[int, tuple[int, int]]:
    """Window number -> (start, end) exactly as ``candidates.extract`` numbered them for this trim."""
    if analysis is None:
        return {}
    within = None if len(_all_windows(analysis)) <= MAX_CANDIDATES else trim
    return {n: (w[0], w[1]) for n, w in numbered(analysis, within)}


def keyframe_time(c: Candidate, numbered_windows: Mapping[int, tuple[int, int]],
                  siblings: Sequence[Candidate] = ()) -> tuple[int, str]:
    """Source time of the frame a candidate's keyframe was grabbed at, and how it was found.

    A plain window: its middle (``extract`` grabs the middle of the unclipped window). A sub-shot ``f<n>``: the n-th
    of the sorted frame times of its parent (the extra frames that were grabbed, by their file names, and the
    middle). Otherwise the middle of the logged range."""
    number, sub = split_id(c.candidate_id, c.source_id)
    window = numbered_windows.get(number) if number is not None else None
    if window is not None and window[1] > c.start_ms and window[0] < c.end_ms:
        if sub is None:
            return (window[0] + window[1]) // 2, "numbered_window"
        parent = Candidate(f"{c.source_id}c{number}", c.source_id, window[0], window[1], c.shot_index, c.motion,
                           c.quality, c.dhash, None)
        early, late = extra_times(parent)
        names = [s.keyframe or "" for s in siblings]
        present = []
        if any(n.endswith(f"{SPLIT_MARK}1.jpg") for n in names):
            present.append(early)
        if any(n.endswith(f"{SPLIT_MARK}3.jpg") for n in names):
            present.append(late)
        if not present and len(siblings) >= 3:
            present = [early, late]
        points = sorted({*present, parent.middle_ms})
        if 1 <= sub <= len(points) and len(points) == max(len(siblings), 2):
            return points[sub - 1], "subshot_point"
    return (c.start_ms + c.end_ms) // 2, "logged_mid"


# --- the view ---

def _is_item(part: str) -> bool:
    return part.startswith("i") and part[1:].isdigit()


def _vision(found: Evidence | None) -> bool:
    return found is not None and any(i.origin == "vision" for i in found.items)


def observed_ids(data: RunData) -> frozenset[str]:
    """Shots with a vision answer in some item part (intro and outro looks stay theirs, as in production)."""
    return frozenset(i for part in data.order if _is_item(part) for i, found in (data.evidence.get(part) or {}).items()
                     if _vision(found))


def planning_view(data: RunData) -> PlanningView:
    if not isinstance(data, RunData):
        raise TypeError("planning_view takes a RunData")
    seen = observed_ids(data)
    parts = []
    for part in data.order:
        log = data.logs[part]
        plan = data.plans.get(part) or {}
        inputs = (plan.get("provenance") or {}).get("inputs") or {}
        trims = tuple(data.trims.get(part) or ())
        trim_of = {sid: (a, b) for sid, a, b in trims}
        keys = data.source_keys.get(part) or {}
        listed = [Candidate.from_dict(c) for c in log.get("candidates") or ()]
        groups = {c["id"]: c.get("group") or c["id"] for c in log.get("candidates") or ()}
        by_parent: dict[str, list[Candidate]] = {}
        for c in listed:
            by_parent.setdefault(parent_id(c.candidate_id, c.source_id), []).append(c)
        numbered_of: dict[str, dict[int, tuple[int, int]]] = {}
        cuts = []
        for sid in sorted({c.source_id for c in listed} | set(trim_of)):
            analysis = data.analyses.get(inputs.get(sid, ""))
            if analysis is None and len(data.analyses) == 1:
                analysis = next(iter(data.analyses.values()))
            numbered_of[sid] = windows_by_number(analysis, trim_of.get(sid))
            cuts.append((sid, tuple(analysis.cuts_ms) if analysis is not None else ()))
        shots = []
        for c in sorted(listed, key=lambda c: (c.source_id, c.start_ms, c.candidate_id)):
            parent = parent_id(c.candidate_id, c.source_id)
            siblings = by_parent.get(parent, ()) if parent != c.candidate_id else ()
            t, basis = keyframe_time(c, numbered_of.get(c.source_id, {}), siblings)
            number, _ = split_id(c.candidate_id, c.source_id)
            window = numbered_of.get(c.source_id, {}).get(number) if number is not None else None
            if window is not None and not (window[1] > c.start_ms and window[0] < c.end_ms):
                window = None
            shots.append(ShotView(c.candidate_id, c.source_id, str(keys.get(c.source_id) or inputs.get(c.source_id) or ""),
                                  c.start_ms, c.end_ms, t, basis, c.quality.usable, bool(c.keyframe), groups[c.candidate_id],
                                  c.candidate_id in seen, c, window))
        units = []
        for u in log.get("units") or ():
            cov = u.get("coverage") or {}
            units.append(UnitView(str(u["unit_id"]), str(u.get("text") or ""), str(u.get("function") or "narration"),
                                  int(u["start_ms"]), int(u["end_ms"]), tuple(u.get("chosen") or ()),
                                  tuple(a["candidate_id"] for a in u.get("alternatives") or ()), str(cov.get("status") or ""),
                                  bool(cov.get("needs_review")), int(cov.get("milli") or 0)))
        narration = int(units[-1].end_ms) if units else 0
        kind = "item" if _is_item(part) else part
        parts.append(PartView(part, kind, narration, trims, tuple(cuts), (), tuple(units), tuple(shots),
                              MappingProxyType(dict(data.evidence.get(part) or {}))))
    restriction = "open" if data.restrict_to is None else "closed_world_donor_covered"
    return PlanningView(data.name, data.basis, tuple(parts), SIGNALS_UNAVAILABLE, restriction, data.restrict_to)


# --- eligibility, units and windows ---

def observed_groups(view: PlanningView) -> frozenset[str]:
    return _once(view, "observed_groups",
                 lambda: frozenset(s.group for p in view.parts if p.kind == "item" for s in p.shots if s.observed))


def eligible_shots(view: PlanningView, part: PartView) -> list[ShotView]:
    """Unseen, usable shots with a keyframe, inside the part's trim, outside every group already looked at."""
    return list(_once(view, ("eligible", part.part), lambda: tuple(_eligible(view, part))))


def _eligible(view: PlanningView, part: PartView) -> list[ShotView]:
    if part.kind != "item":
        return []
    taken = observed_groups(view)
    trim_of = {sid: (a, b) for sid, a, b in part.trims}
    out = []
    for s in part.shots:
        if s.observed or not s.usable or not s.has_keyframe or s.group in taken:
            continue
        a, b = trim_of.get(s.source_id, (0, s.end_ms))
        if not (s.end_ms > a and s.start_ms < b):
            continue
        if view.restrict_to is not None and s.candidate_id not in view.restrict_to:
            continue
        out.append(s)
    return out


def wanted_units(part: PartView, target_units: str) -> list[UnitView]:
    if target_units == "flagged":  # the production trigger (vision.targets, frames.wanted)
        return [u for u in part.units if u.needs_review and u.coverage_status in ("partial", "none")]
    if target_units == "not_ready":
        return [u for u in part.units if u.needs_review or u.coverage_status != "full"]
    raise ValueError(f"unknown target_units {target_units!r}")


def forward_bounds(part: PartView, unit: UnitView) -> dict[str, tuple[int, int]]:
    """Per source: after what the earlier sentences chose, before what the later ones chose, inside the trim
    (the bounds of ``vision.targets``)."""
    by_id = {s.candidate_id: s for s in part.shots if s.usable and s.has_keyframe}
    position = {u.unit_id: k for k, u in enumerate(part.units)}
    here = position[unit.unit_id]
    low: dict[str, int] = {}
    high: dict[str, int] = {}
    for other in part.units:
        for chosen in other.chosen:
            s = by_id.get(chosen)
            if s is None:
                continue
            if position[other.unit_id] < here:
                low[s.source_id] = max(low.get(s.source_id, 0), s.end_ms)
            elif position[other.unit_id] > here:
                high[s.source_id] = min(high.get(s.source_id, s.start_ms), s.start_ms)
    out = {}
    sources = {sid for sid, _, _ in part.trims} | {s.source_id for s in part.shots}
    trim_of = {sid: (a, b) for sid, a, b in part.trims}
    for sid in sorted(sources):
        a, b = trim_of.get(sid, (0, max((s.end_ms for s in part.shots if s.source_id == sid), default=0)))
        out[sid] = (max(a, low.get(sid, 0)), min(b, high.get(sid, b)))
    return out


def _in_window(s: ShotView, bounds: Mapping[str, tuple[int, int]]) -> bool:
    low, high = bounds.get(s.source_id, (0, 0))
    return s.start_ms >= low and s.end_ms <= high


def prior_centres(part: PartView, unit: UnitView) -> dict[str, int]:
    """Where the sentence sits in its part (its middle over the narration), mapped onto each source's trim."""
    mid = (unit.start_ms + unit.end_ms) // 2
    out = {}
    for sid, a, b in part.trims:
        out[sid] = a + (b - a) * mid // max(1, part.narration_ms)
    return out


def shuffled_centres(part: PartView, units: Sequence[UnitView]) -> dict[str, dict[str, int]]:
    """The negative control: each sentence takes the ``prior`` centres of the sentence ceil(n/2) places after it
    (cyclic), a derangement when n >= 2; a part with one targeted sentence keeps its own."""
    if len(units) < 2:
        return {u.unit_id: prior_centres(part, u) for u in units}
    shift = math.ceil(len(units) / 2)
    return {u.unit_id: prior_centres(part, units[(k + shift) % len(units)]) for k, u in enumerate(units)}


def _scorer(view: PlanningView, part: PartView) -> Scorer:
    def make() -> Scorer:
        pool = [s.candidate for s in part.shots]
        return Scorer([c for c in pool if c.quality.usable] or pool, part.evidence)
    return _once(view, ("scorer", part.part), make)


def relevance(scorer: Scorer, unit: Unit, shot: ShotView) -> int:
    """The planner's relevance of a shot to a sentence (0 for a shot the Scorer was not built over)."""
    try:
        return scorer.relevance(unit, shot.candidate)[0]
    except KeyError:
        return 0


def _unit(u: UnitView) -> Unit:
    return Unit(u.unit_id, u.text, u.start_ms, u.end_ms, u.function or "narration")


def anchor_centres(part: PartView, unit: UnitView, scorer: Scorer, bounds: Mapping[str, tuple[int, int]]) -> dict[str, int]:
    """The keyframe of the looked-at shot in the forward window that best matches the sentence (relevance > 0),
    for its source; ``prior`` elsewhere."""
    centres = prior_centres(part, unit)
    wanted = _unit(unit)
    best: tuple[int, int, str] | None = None
    for s in part.shots:
        if not (s.observed or _vision(part.evidence.get(s.candidate_id))) or not _in_window(s, bounds):
            continue
        value = relevance(scorer, wanted, s)
        if value > 0 and (best is None or (-value, s.start_ms, s.candidate_id) < (-best[0], best[1], best[2])):
            best = (value, s.start_ms, s.candidate_id)
            centres[s.source_id] = s.keyframe_ms
    return centres


def _region(shots: Sequence[ShotView], bounds: Mapping[str, tuple[int, int]], centres: Mapping[str, int],
            window_ms: int | None) -> list[ShotView]:
    out = []
    for s in shots:
        if not _in_window(s, bounds) or s.source_id not in centres:
            continue
        if window_ms is not None and abs(s.keyframe_ms - centres[s.source_id]) > window_ms:
            continue
        out.append(s)
    return sorted(out, key=lambda s: (abs(s.keyframe_ms - centres[s.source_id]), s.start_ms, s.candidate_id))


# --- fair turns ---

Proposal = tuple[ShotView, "str | None", str]


def _interleave(queues: Sequence[Sequence[Proposal]]) -> list[Proposal]:
    """The sentences of a part take turns; a shot is proposed once (for the first sentence that wants it)."""
    out: list[Proposal] = []
    seen: set[str] = set()
    queues = [list(q) for q in queues]
    while any(queues):
        for q in queues:
            while q and q[0][0].candidate_id in seen:
                q.pop(0)
            if q:
                proposal = q.pop(0)
                seen.add(proposal[0].candidate_id)
                out.append(proposal)
    return out


def _turns(view: PlanningView, queues: Mapping[str, list[Proposal]], budget: int | None) -> list[Sample]:
    order = [p.part for p in view.parts if p.part in queues]
    pending = {p: list(q) for p, q in queues.items()}
    taken: set[str] = set()
    out: list[Sample] = []
    turn = 0

    def next_of(part: str) -> Proposal | None:
        q = pending[part]
        while q and q[0][0].group in taken:
            q.pop(0)
        return q.pop(0) if q else None

    while budget is None or len(out) < budget:
        active = [p for p in order if any(s.group not in taken for s, _, _ in pending[p])]
        if not active:
            break
        left = None if budget is None else budget - len(out)
        serve = active if left is None or left >= len(active) else spread_order(active)[:left]
        turn += 1
        for part in serve:
            proposal = next_of(part)
            if proposal is None:
                continue
            s, unit_id, reason = proposal
            taken.add(s.group)
            out.append(Sample(part, s.candidate_id, s.source_id, s.source_key, s.keyframe_ms, s.keyframe_basis, unit_id,
                              reason, turn))
    return out


def _one_per_group(queues: Mapping[str, list[Proposal]]) -> tuple[str, ...]:
    first: dict[str, str] = {}
    for part in sorted(queues):
        for s, _, _ in queues[part]:
            first.setdefault(s.group, s.candidate_id)
    return tuple(sorted(first.values()))


# --- strategies ---

def _queues(view: PlanningView, params: SelectParams, notes: dict[str, Any]) -> dict[str, list[Proposal]]:
    strategy, window = params.strategy, params.window_ms
    queues: dict[str, list[Proposal]] = {}
    items = [p for p in view.parts if p.kind == "item"]
    if strategy == "all_eligible":
        for part in items:
            queues[part.part] = [(s, None, "all_eligible") for s in spread_order(eligible_shots(view, part))]
        return queues
    if strategy == "uniform":
        return queues  # chosen by vision.allocate in select_detail
    if strategy == "production":
        weak_parts = [p for p in items if wanted_units(p, "flagged") and eligible_shots(view, p)]
        budget = params.budget or 0
        share = {p.part: budget // max(1, len(weak_parts)) for p in weak_parts}
        for part in spread_order(weak_parts)[:budget - sum(share.values()) if weak_parts else 0]:
            share[part.part] += 1
        notes["production_share"] = {k: share[k] for k in sorted(share)}
        for part in weak_parts:
            fresh = {s.candidate_id for s in eligible_shots(view, part)}
            pool = [s.candidate for s in part.shots if s.usable and s.has_keyframe]
            not_fresh = {s.candidate_id for s in part.shots if s.candidate_id not in fresh}
            units = [{"unit_id": u.unit_id, "text": u.text, "function": u.function, "chosen": list(u.chosen),
                      "coverage": {"needs_review": u.needs_review, "status": u.coverage_status}} for u in part.units]
            first = targets(units, pool, part.evidence, set(not_fresh), share[part.part], forward_only=True)
            more = targets(units, pool, part.evidence, set(not_fresh), len(pool), forward_only=True)
            by_id = {s.candidate_id: s for s in part.shots}
            ids = list(dict.fromkeys([*first, *more]))
            queues[part.part] = [(by_id[i], None, "production" if i in first else "production_more") for i in ids]
        return queues
    unshuffled = 0
    for part in items:
        units = wanted_units(part, params.target_units)
        fresh = eligible_shots(view, part)
        if not units or not fresh:
            continue
        scorer = _scorer(view, part)
        bounds = {u.unit_id: forward_bounds(part, u) for u in units}
        prior = {u.unit_id: prior_centres(part, u) for u in units}
        if strategy == "shuffled_prior":
            unshuffled += len(units) < 2
            prior = shuffled_centres(part, units)
        per_unit = []
        for u in units:
            b = bounds[u.unit_id]
            if strategy in ("prior", "shuffled_prior"):
                q = [(s, u.unit_id, strategy) for s in _region(fresh, b, prior[u.unit_id], window)]
            elif strategy == "anchor":
                q = [(s, u.unit_id, "anchor") for s in _region(fresh, b, anchor_centres(part, u, scorer, b), window)]
            elif strategy == "gap":
                q = [(s, u.unit_id, "gap") for s in spread_order([s for s in fresh if _in_window(s, b)])]
            elif strategy == "mixed":
                wanted = _unit(u)
                matched = sorted(((relevance(scorer, wanted, s), s) for s in fresh if _in_window(s, b)),
                                 key=lambda pair: (-pair[0], pair[1].start_ms, pair[1].candidate_id))
                q = [(s, u.unit_id, "mixed:words") for value, s in matched if value > 0]
                q += [(s, u.unit_id, "mixed:anchor") for s in _region(fresh, b, anchor_centres(part, u, scorer, b), window)]
                q += [(s, u.unit_id, "mixed:prior") for s in _region(fresh, b, prior_centres(part, u), window)]
                q += [(s, u.unit_id, "mixed:gap") for s in spread_order([s for s in fresh if _in_window(s, b)])]
            else:
                raise ValueError(f"unknown strategy {strategy!r}")
            per_unit.append(q)
        queues[part.part] = _interleave(per_unit)
    if strategy == "shuffled_prior":
        notes["unshuffled_parts"] = unshuffled
    return queues


def select_centred(view: PlanningView, params: SelectParams, centres: Mapping[str, Mapping[str, int]]) -> Selection:
    """The ``prior`` machinery with given centres (unit id -> source id -> ms) instead of planning-time ones: for an
    upper-bound arm built outside this module. Units without a centre propose nothing."""
    if not isinstance(view, PlanningView):
        raise TypeError("select_centred takes a PlanningView")
    queues: dict[str, list[Proposal]] = {}
    for part in view.parts:
        if part.kind != "item":
            continue
        fresh = eligible_shots(view, part)
        per_unit = []
        for u in wanted_units(part, params.target_units):
            if u.unit_id not in centres:
                continue
            per_unit.append([(s, u.unit_id, "centred") for s in
                             _region(fresh, forward_bounds(part, u), centres[u.unit_id], params.window_ms)])
        if per_unit:
            queues[part.part] = _interleave(per_unit)
    return Selection(tuple(_turns(view, queues, params.budget)), _one_per_group(queues), MappingProxyType({}))


def select_detail(view: PlanningView, params: SelectParams) -> Selection:
    if not isinstance(view, PlanningView):
        raise TypeError("select takes a PlanningView")
    if not isinstance(params, SelectParams):
        raise TypeError("select takes SelectParams")
    if params.strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {params.strategy!r}")
    if params.frames != "mid":
        raise ValueError("only the keyframe (mid) frame is simulated")
    if params.budget is None and params.strategy != "all_eligible":
        raise ValueError("a budget is required")
    notes: dict[str, Any] = {}
    if params.strategy == "uniform":
        items = [p for p in view.parts if p.kind == "item"]
        pools = [eligible_shots(view, p) for p in items]
        part_of = {s.candidate_id: p.part for p, pool in zip(items, pools) for s in pool}
        by_id = {s.candidate_id: s for pool in pools for s in pool}
        groups = {s.candidate_id: s.group for pool in pools for s in pool}
        picked = allocate([[s.candidate for s in pool] for pool in pools], _Here(), params.budget or 0, groups)
        samples = []
        for turn, (candidate_id, _) in enumerate(picked, 1):
            s = by_id[candidate_id]
            samples.append(Sample(part_of[candidate_id], s.candidate_id, s.source_id, s.source_key, s.keyframe_ms,
                                  s.keyframe_basis, None, "uniform", turn))
        queues = {p.part: [(s, None, "uniform") for s in pool] for p, pool in zip(items, pools) if pool}
        return Selection(tuple(samples), _one_per_group(queues), MappingProxyType(notes))
    queues = _queues(view, params, notes)
    samples = _turns(view, queues, params.budget)
    return Selection(tuple(samples), _one_per_group(queues), MappingProxyType(notes))


def select(view: PlanningView, params: SelectParams) -> tuple[Sample, ...]:
    return select_detail(view, params).samples


class _Here:
    """``vision.allocate`` joins a folder and a keyframe name; the selection needs neither."""

    def __truediv__(self, other: Any) -> Any:
        return other


__all__ = ["PartView", "PlanningView", "RunData", "Sample", "SelectParams", "Selection", "ShotView", "STRATEGIES",
           "UnitView", "eligible_shots", "forward_bounds", "keyframe_time", "planning_view", "prior_centres", "select",
           "select_centred", "select_detail", "shuffled_centres", "split_id", "wanted_units", "windows_by_number"]
