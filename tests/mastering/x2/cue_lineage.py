"""Cross-language cue lineage: a research prototype and its $0 bilingual benchmark (Workstream B, V3 §V).

ENGINEERING_FIXTURE_ONLY = TRUE. Nothing here is a production planner change, an X2 arm result, REAL-DEV, REAL-HOLDOUT
or a human label. The Vietnamese sentences were written by the agent for the fixture.

Problem: the source speaks English, DubFlow narrates in Vietnamese, and the planner matches words. A Vietnamese
sentence shares almost no word with the English speech over its shot.

Lineage: a narration sentence cites the cue ids it was written from, and the planner follows the chain
``source_id -> cue_id -> source time -> narration ref -> candidate window -> played interval``.

- Every cue id must exist in the cue file, whose sha256 is checked first (``CueIndex.load``). An unknown id is
  rejected and recorded, never repaired. A time never comes from the narration: only from the hashed cue file.
- A sentence that cites several cues keeps each one, in order. Several sentences may cite one cue.
- A cue link says where words are spoken. It never says what the picture shows: ``picture_verified`` is always
  False, confidence and review state come from the planner unchanged, and Final QC is not touched.

Retrieval compared on the fixture (names follow X2_POC_DESIGN_V2 §2/§2b, but these are fixture proxies, never the
X2 arms' results):
- ``none``: no transcript at all (a raw source without speech text);
- ``lexical``: today's planner, the Vietnamese sentence against the English speech over each shot;
- ``region`` (B): only shots inside the cited cues' region (±``window_ms``), matched by the cited cues' own words; a
  sentence with no valid cue gets no footage (``no_cue_region``), never a whole-source fallback;
- ``ranked`` (C): every shot, the cited cues' words added to the sentence as a ranking signal; no cue: as ``lexical``;
- ``block`` (D, ablation): every shot, the whole source transcript added to every sentence (no cue-sentence link).
"""
from __future__ import annotations

import hashlib, json, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align, evidence as ev, quality, review as rv  # noqa: E402
from engine.dubflow.mastering import policy as rules  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate, Quality  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
ARMS = ("none", "lexical", "region", "ranked", "block")
LEAD_MS = 300


class LineageError(ValueError):
    pass


@dataclass(frozen=True)
class Cue:
    source_id: str
    cue_id: str
    start_ms: int
    end_ms: int
    text: str


@dataclass
class CueIndex:
    """The cues of one source, from a cue file whose sha256 was checked."""

    source_id: str
    sha256: str
    cues: dict[str, Cue]

    @classmethod
    def from_document(cls, source_id: str, document: Mapping[str, Any], sha256: str) -> "CueIndex":
        out: dict[str, Cue] = {}
        for raw in document.get("cues") or ():
            cid, a, b, text = raw.get("id"), raw.get("start_ms"), raw.get("end_ms"), raw.get("text")
            if not isinstance(cid, str) or type(a) is not int or type(b) is not int or not 0 <= a < b or not isinstance(text, str):
                raise LineageError(f"{source_id}: malformed cue {raw!r}")
            if cid in out:
                raise LineageError(f"{source_id}: duplicate cue id {cid}")
            out[cid] = Cue(source_id, cid, a, b, text)
        return cls(source_id, sha256, out)

    @classmethod
    def load(cls, source_id: str, path: Path, expected_sha256: str) -> "CueIndex":
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise LineageError(f"{source_id}: cue file sha256 {digest} != expected {expected_sha256}")
        return cls.from_document(source_id, json.loads(data.decode("utf-8")), digest)

    def as_tuples(self) -> list[tuple[int, int, str]]:
        return sorted((c.start_ms, c.end_ms, c.text) for c in self.cues.values())


@dataclass(frozen=True)
class Sentence:
    uid: str
    text: str  # Vietnamese narration
    refs: tuple[str, ...]  # cited "source_id:cue_id"
    shows: frozenset[str]  # picture truth tags (empty: no footage by construction)
    kind: str = "match"


@dataclass
class Resolved:
    sentence: Sentence
    cues: list[Cue] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str | None:
        """X2 §2b: why a sentence has no valid cue (None when it has one)."""
        if self.cues:
            return None
        return "cue_rejected" if self.rejected else "no_cue_written"


def resolve(sentences: Sequence[Sentence], indexes: Mapping[str, CueIndex]) -> list[Resolved]:
    """Each sentence's cited cues, from the hashed index only; an unknown source or cue id is rejected, never fixed."""
    out = []
    for sentence in sentences:
        item = Resolved(sentence)
        for ref in sentence.refs:
            source_id, _, cue_id = ref.partition(":")
            cue = indexes.get(source_id).cues.get(cue_id) if source_id in indexes else None
            if cue is None:
                item.rejected.append(ref)
            else:
                item.cues.append(cue)
        out.append(item)
    return out


@dataclass(frozen=True)
class Link:
    """One step of lineage: a cited cue, the shot holding it, and what played of that shot for the sentence."""

    narration_uid: str
    source_id: str
    cue_id: str
    cue_start_ms: int
    cue_end_ms: int
    candidate_id: str | None
    window: tuple[int, int] | None
    played: tuple[tuple[int, int], ...]
    played_over_cue_ms: int
    picture_verified: bool = False  # a cue is where words are spoken, never what is seen

    def to_dict(self) -> dict[str, Any]:
        return {"narration": self.narration_uid, "source_id": self.source_id, "cue_id": self.cue_id,
                "cue_ms": [self.cue_start_ms, self.cue_end_ms], "candidate_id": self.candidate_id,
                "window_ms": list(self.window) if self.window else None, "played_ms": [list(p) for p in self.played],
                "played_over_cue_ms": self.played_over_cue_ms, "picture_verified": False}


def lineage(item: Resolved, candidates: Sequence[Candidate], played: Sequence[tuple[str, int, int]]) -> list[Link]:
    out = []
    for cue in item.cues:
        holder = next((c for c in candidates if c.source_id == cue.source_id and c.start_ms <= cue.start_ms < c.end_ms), None)
        mine = tuple((o, e) for cid, o, e in played if holder is not None and cid == holder.candidate_id)
        over = sum(max(0, min(e, cue.end_ms) - max(o, cue.start_ms)) for o, e in mine)
        out.append(Link(item.sentence.uid, cue.source_id, cue.cue_id, cue.start_ms, cue.end_ms,
                        holder.candidate_id if holder else None, (holder.start_ms, holder.end_ms) if holder else None,
                        mine, over))
    return out


# --- the bilingual fixture -------------------------------------------------------------------------------------------

@dataclass
class Fixture:
    shots: list[tuple[str, str, int, int, str]]  # (candidate id, source id, start, end, picture tag)
    cue_docs: dict[str, dict[str, Any]]
    sentences: list[Sentence]
    sentence_ms: int = 3000


def _s(uid: str, text: str, refs: Iterable[str], *tags: str, kind: str = "match") -> Sentence:
    return Sentence(uid, text, tuple(refs), frozenset(tags), kind)


def fixture() -> Fixture:
    shots = [("a0", "en", 0, 6000, "tractor"), ("a1", "en", 6000, 12000, "lighthouse"),
             ("a2", "en", 12000, 18000, "glacier"), ("a3", "en", 18000, 24000, "presenter"),
             ("a4", "en", 24000, 30000, "geyser"), ("a5", "en", 30000, 36000, "locomotive"),
             ("a6", "en", 36000, 42000, "bridge"), ("a7", "en", 42000, 48000, "harbor"),
             ("b0", "mute", 0, 6000, "meadow"), ("b1", "mute", 6000, 12000, "forest")]
    en = [("c1", 3500, 5200, "The tractor crosses the river."),
          ("c2", 9800, 11500, "The lighthouse flashes over the harbor."),
          ("c3", 13500, 15200, "In 2023 the glacier melts into the valley."),
          # a presenter talks over his own picture: the year is right, the picture is not the volcano
          ("c4", 19000, 20600, "In 1959 the volcano erupted on the island."),
          # the speech recogniser split the name: "Steam boat" for "Steamboat"
          ("c5", 26000, 27500, "Steam boat geyser threw muddy water."),
          ("c6", 31000, 32600, "The locomotive climbs the mountain."),
          ("c7", 39000, 40500, "The bridge sways over the canyon."),
          ("c8", 43000, 44500, "Fishing boats return to the harbor at dusk.")]
    docs = {"en": {"cues": [{"id": i, "start_ms": a, "end_ms": b, "text": t} for i, a, b, t in en]},
            "mute": {"cues": []}}
    sentences = [
        _s("v1", "Chiếc máy kéo băng qua dòng sông.", ["en:c1"], "tractor"),
        _s("v2", "Ngọn hải đăng nhấp nháy trên bến cảng.", ["en:c2"], "lighthouse"),
        _s("v3", "Năm 2023, sông băng tan chảy xuống thung lũng.", ["en:c3"], "glacier"),
        _s("v4", "Năm 1959, núi lửa phun trào trên đảo.", ["en:c4"], "volcano", kind="match_wrong_picture"),
        _s("v5", "Mạch phun Steamboat phun nước bùn.", ["en:c5"], "geyser", kind="asr_name_error"),
        _s("v6", "Đầu máy leo núi, rồi cây cầu đung đưa trên hẻm núi.", ["en:c6", "en:c7"], "locomotive", "bridge",
           kind="montage"),
        _s("v7", "Thuyền đánh cá về bến lúc hoàng hôn.", ["en:c8"], "harbor"),
        _s("v8", "Một lần nữa, máy kéo vượt sông.", ["en:c1"], "tractor", kind="shared_cue"),
        _s("v9", "Đồng cỏ yên tĩnh buổi sáng.", [], "meadow", kind="no_cue_source"),
        _s("v10", "Hải đăng sáng lên.", ["en:c99"], "lighthouse", kind="cue_rejected"),
        # hard negatives: no footage by construction
        _s("h1", "Chiếc máy kéo chìm trong đầm lầy.", [], kind="neg_action"),
        _s("h2", "Chiếc máy kéo chìm trong đầm lầy.", ["en:c1"], kind="neg_action_bad_citation"),
        _s("h3", "Chiếc trực thăng leo lên đỉnh núi.", [], kind="neg_entity"),
        _s("h4", "Năm 1959, sông băng tan chảy.", [], kind="neg_year"),
        _s("h5", "Bà nội nướng bánh mì cho lễ hội.", [], kind="neg_none"),
    ]
    return Fixture(shots, docs, sentences)


def indexes_of(fx: Fixture) -> dict[str, CueIndex]:
    out = {}
    for source_id, doc in fx.cue_docs.items():
        raw = json.dumps(doc, sort_keys=True).encode("utf-8")
        out[source_id] = CueIndex.from_document(source_id, doc, hashlib.sha256(raw).hexdigest())
    return out


def _candidates(fx: Fixture) -> list[Candidate]:
    return [Candidate(cid, src, a, b, i, 50, Quality(120, 50, 300, ()), i + 1, f"{cid}.jpg")
            for i, (cid, src, a, b, _) in enumerate(fx.shots)]


def _evidence(fx: Fixture, candidates: Sequence[Candidate], indexes: Mapping[str, CueIndex], *, speech: bool) -> dict[str, ev.Evidence]:
    found = {}
    for c in candidates:
        item = ev.Evidence(c.candidate_id)
        if speech:
            cues = indexes[c.source_id].as_tuples()
            item.cues = tuple(x for x in cues if x[0] < c.end_ms and x[1] > c.start_ms)
            said = ev.transcript_text(cues, c.start_ms, c.end_ms)
            if said:
                item.add(ev.Item("transcript", 850, said[:400], (), "phụ đề nguồn"))
        found[c.candidate_id] = item
    return found


def _plan(texts: Sequence[str], candidates, found, ms: int, *, forward_only: bool, lead: int | None):
    spoken = [(t, ms) for t in texts]
    # no product (Bán hàng) functions: an opening without a match is "generic" there, which is not this question
    units = align.units(spoken, ms * len(spoken), lead_ms=0, gap_ms=0, function=None)
    choices, decisions, mode = align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=forward_only,
                                            cue_inpoint_lead_ms=lead)
    log = {"mode": mode, "candidates": [{"id": c.candidate_id, "quality": {}, "evidence": found[c.candidate_id].to_dict()}
                                        for c in candidates]}
    out = []
    for unit, decision in zip(units, sorted(decisions, key=lambda d: d.unit.start_ms)):
        mine = [c for c in choices if c.unit_id == unit.unit_id]
        d = decision.to_dict()
        out.append({"played": [(c.candidate.candidate_id, c.offset_ms, c.offset_ms + c.length_ms) for c in mine],
                    "state": rv.unit_state(d, log, [{"needs_review": c.needs_review} for c in mine]),
                    "matched": bool((d.get("coverage") or {}).get("matched_footage"))})
    problems = quality.timeline_problems(quality.pieces_from_choices(choices), duration_ms=ms * len(texts), forward_only=forward_only)
    return out, sorted(p["code"] for p in problems)


def run_arm(arm: str, fx: Fixture | None = None, *, forward_only: bool = False, window_ms: int = 0,
            lead_ms: int | None = LEAD_MS) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError(arm)
    fx = fx or fixture()
    indexes = indexes_of(fx)
    resolved = resolve(fx.sentences, indexes)
    candidates = _candidates(fx)
    found = _evidence(fx, candidates, indexes, speech=arm != "none")
    picture = {cid: (a, b, tag) for cid, _, a, b, tag in fx.shots}
    every_cue = " ".join(t for idx in indexes.values() for _, _, t in idx.as_tuples())
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    if arm == "region":
        for item in resolved:
            if not item.cues:
                rows.append({"played": [], "state": rv.MATCH_NOT_FOUND, "matched": False, "why": "no_cue_region"})
                continue
            spans = [(c.source_id, c.start_ms - window_ms, c.end_ms + window_ms) for c in item.cues]
            pool = [c for c in candidates if any(c.source_id == s and c.start_ms < b and c.end_ms > a for s, a, b in spans)]
            text = " ".join(c.text for c in item.cues)
            planned, codes = _plan([text], pool, {c.candidate_id: found[c.candidate_id] for c in pool}, fx.sentence_ms,
                                   forward_only=False, lead=lead_ms)
            rows.append(planned[0])
            problems += codes
    else:
        if arm == "ranked":
            texts = [f"{i.sentence.text} {' '.join(c.text for c in i.cues)}".strip() for i in resolved]
        elif arm == "block":
            texts = [f"{i.sentence.text} {every_cue}" for i in resolved]
        else:
            texts = [i.sentence.text for i in resolved]
        rows, problems = _plan(texts, candidates, found, fx.sentence_ms, forward_only=forward_only,
                               lead=lead_ms if arm in ("lexical", "ranked") else None)
    units = []
    for item, row in zip(resolved, rows):
        s = item.sentence
        right = sum(max(0, min(e, picture[cid][1]) - max(o, picture[cid][0])) for cid, o, e in row["played"]
                    if picture[cid][2] in s.shows)
        played = sum(e - o for _, o, e in row["played"])
        links = lineage(item, candidates, row["played"])
        first = row["played"][0] if row["played"] else None
        units.append({"uid": s.uid, "kind": s.kind, "refs": list(s.refs), "rejected": item.rejected, "why_no_cue": item.reason,
                      "has_footage": bool(s.shows), "state": row["state"], "matched": row["matched"], "played": row["played"],
                      "picture_right_ms": right, "played_ms": played,
                      "top1_right": bool(first) and picture[first[0]][2] in s.shows,
                      "cue_covered": bool(links) and all(link.played_over_cue_ms > 0 for link in links),
                      "lineage": [link.to_dict() for link in links]})
    pos = [u for u in units if u["has_footage"]]
    neg = [u for u in units if not u["has_footage"]]
    return {"ENGINEERING_FIXTURE_ONLY": True, "arm": arm, "forward_only": forward_only, "window_ms": window_ms,
            "summary": {"with_footage": len(pos), "top1_right": sum(u["top1_right"] for u in pos),
                        "footage_matched": sum(u["matched"] for u in pos),
                        "picture_right_ms": sum(u["picture_right_ms"] for u in pos),
                        "played_ms_with_footage": sum(u["played_ms"] for u in pos),
                        "cited_cue_covered": sum(u["cue_covered"] for u in pos if u["lineage"]),
                        "with_valid_cue": sum(bool(u["lineage"]) for u in pos),
                        "no_footage": len(neg), "no_footage_assigned": sum(u["matched"] for u in neg),
                        "no_footage_assigned_ids": [u["uid"] for u in neg if u["matched"]],
                        "ready": sum(u["state"] == rv.READY for u in units),
                        "timeline_problems": problems},
            "units": units}


def run_engine(mode: str, fx: Fixture | None = None, *, forward_only: bool = False, window_ms: int = 0,
               lead_ms: int | None = LEAD_MS) -> dict[str, Any]:
    """The same fixture through the planner's own lineage experiment (``align.assign(..., lineage=...)``), the whole
    narration in one plan (so a shared cue, forward-only order and the timeline are real)."""
    from engine.dubflow.mastering import lineage as lin
    fx = fx or fixture()
    indexes = {sid: lin.CueIndex.from_document(sid, doc, idx.sha256) for sid, doc in fx.cue_docs.items()
               for idx in [indexes_of(fx)[sid]]}
    plan = lin.resolve(mode, [s.refs for s in fx.sentences], indexes, window_ms=window_ms)
    candidates = _candidates(fx)
    found = _evidence(fx, candidates, indexes_of(fx), speech=True)
    picture = {cid: (a, b, tag) for cid, _, a, b, tag in fx.shots}
    spoken = [(s.text, fx.sentence_ms) for s in fx.sentences]
    units = align.units(spoken, fx.sentence_ms * len(spoken), lead_ms=0, gap_ms=0, function=None)
    choices, decisions, run_mode = align.assign(units, candidates, found, rules.STYLE_PACING["natural"],
                                                forward_only=forward_only, cue_inpoint_lead_ms=lead_ms, lineage=plan)
    log = {"mode": run_mode, "candidates": [{"id": c.candidate_id, "quality": {}, "evidence": found[c.candidate_id].to_dict()}
                                            for c in candidates]}
    rows = []
    for s, unit, decision in zip(fx.sentences, units, sorted(decisions, key=lambda d: d.unit.start_ms)):
        mine = [c for c in choices if c.unit_id == unit.unit_id]
        d = decision.to_dict()
        played = [(c.candidate.candidate_id, c.offset_ms, c.offset_ms + c.length_ms) for c in mine]
        first = played[0] if played else None
        rows.append({"uid": s.uid, "kind": s.kind, "has_footage": bool(s.shows), "played": played,
                     "state": rv.unit_state(d, log, [{"needs_review": c.needs_review} for c in mine]),
                     "matched": bool((d.get("coverage") or {}).get("matched_footage")),
                     "confidence": [c.confidence for c in mine], "lineage": d.get("lineage"), "notes": d.get("notes"),
                     "picture_right_ms": sum(max(0, min(e, picture[cid][1]) - max(o, picture[cid][0]))
                                             for cid, o, e in played if picture[cid][2] in s.shows),
                     "top1_right": bool(first) and picture[first[0]][2] in s.shows,
                     "cue_covered": bool(d.get("lineage", {}).get("links"))
                                    and all(k["played_over_cue_ms"] > 0 for k in d["lineage"]["links"])})
    problems = quality.timeline_problems(quality.pieces_from_choices(choices), duration_ms=fx.sentence_ms * len(spoken),
                                         forward_only=forward_only)
    pos = [r for r in rows if r["has_footage"]]
    neg = [r for r in rows if not r["has_footage"]]
    return {"ENGINEERING_FIXTURE_ONLY": True, "engine": True, "mode": mode, "forward_only": forward_only,
            "summary": {"with_footage": len(pos), "top1_right": sum(r["top1_right"] for r in pos),
                        "footage_matched": sum(r["matched"] for r in pos),
                        "picture_right_ms": sum(r["picture_right_ms"] for r in pos),
                        "cited_cue_covered": sum(r["cue_covered"] for r in pos if r["lineage"] and r["lineage"]["links"]),
                        "with_valid_cue": sum(bool(r["lineage"] and r["lineage"]["links"]) for r in pos),
                        "no_footage": len(neg), "no_footage_assigned_ids": [r["uid"] for r in neg if r["matched"]],
                        "ready": sum(r["state"] == rv.READY for r in rows),
                        "timeline_problems": sorted(p["code"] for p in problems)},
            "units": rows}


def run(forward_only: bool = False) -> dict[str, Any]:
    return {"ENGINEERING_FIXTURE_ONLY": True, "arms": {arm: run_arm(arm, forward_only=forward_only) for arm in ARMS}}


if __name__ == "__main__":
    result = {"reorder": run(False), "forward_only": run(True)}
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    for mode, r in result.items():
        for arm, a in r["arms"].items():
            print(mode, arm, json.dumps(a["summary"], ensure_ascii=False))
    for forward in (False, True):
        for mode in ("region", "ranked"):
            print("engine", "forward_only" if forward else "reorder", mode,
                  json.dumps(run_engine(mode, forward_only=forward)["summary"], ensure_ascii=False))
