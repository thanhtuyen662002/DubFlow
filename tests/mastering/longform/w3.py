"""Offline diagnosis of a long-form (W3) Tổng hợp run from its saved files: replay, score, attribute, experiment.

A raw Tổng hợp run keeps, in ``<run>/.dubflow-work/mastering``, everything the planner was called with at its final
call for each part: ``editorial-<part>.json`` (units, every candidate with its evidence), ``plan-<part>.json`` and the
scene analysis. ``plan_part`` is a pure function of those, so the run can be planned again here with any code, any
evidence, no media, no network and no key (the long-form feasibility directive, 2026-11-13: deterministic replay,
failure attribution per stage, oracle and provider-variability experiments).

The ground truth is read, never written. Its sha256 is checked against the lock before anything is scored, and the
scoring rules are those of the locked Windows harness (``score_w3.py``, fixed before the runs):

* top 5 of a unit = its chosen shots in order, then its alternatives in order, distinct, first five;
* a candidate hits a range when they overlap by >= 1000 ms or by >= half of the shorter of the two;
* recall@k = units with footage whose top k has a hit / units with footage;
* serious silent miss = a unit with footage, READY, whose chosen shots hit nothing; a "missing" unit READY too.

Oracle experiments change the evidence of a copy in memory only. Their numbers are upper bounds of a stage, never a
measure of the pipeline (they use the ground truth).

Usage (from the repository root, ``python -B``)::

    python -B -m tests.mastering.longform.w3 score     <gt.json> <lock> <run-dir> [<run-dir> ...]
    python -B -m tests.mastering.longform.w3 replay    <gt.json> <lock> <run-dir> [--out DIR]
    python -B -m tests.mastering.longform.w3 attribute <gt.json> <lock> <run-dir> [--json OUT]
    python -B -m tests.mastering.longform.w3 oracle    <gt.json> <lock> <run-dir>
    python -B -m tests.mastering.longform.w3 swap      <gt.json> <lock> <run-dir-A> <run-dir-B>
    python -B -m tests.mastering.longform.w3 pooled    <gt.json> <lock> <run-dir> <donor-run-dir> [...]
    python -B -m tests.mastering.longform.w3 variability <run-dir> [<run-dir> ...]
    python -B -m tests.mastering.longform.w3 hint      <gt.json> <lock> <run-dir> [--window-s 15]
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align  # noqa: E402
from engine.dubflow.mastering import evidence as ev  # noqa: E402
from engine.dubflow.mastering import plan as m  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate  # noqa: E402
from engine.dubflow.mastering.planners import compilation as compiler  # noqa: E402
from tests.mastering.real.capture import analysis_from, evidence_from  # noqa: E402

LEAD_MS, GAP_MS = 300, 250  # product.script.LEAD_MS / GAP_MS, the values every raw run plans with
TAIL_MS = 700


# --- ground truth ---

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".!?…").strip().lower()


def load_gt(gt_path: Path, lock: Path) -> dict:
    digest = hashlib.sha256(gt_path.read_bytes()).hexdigest()
    locked = lock.read_text(encoding="utf-8").split()[0]
    if locked != digest:
        raise SystemExit(f"ground truth changed after it was locked: {locked} != {digest}")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    gt["sha256"] = digest
    return gt


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def hits(c: Mapping[str, Any], ranges: Sequence[Sequence[float]]) -> bool:
    c0, c1 = c["start_ms"], c["end_ms"]
    for r0, r1 in ranges:
        r0, r1 = r0 * 1000, r1 * 1000
        o = overlap(c0, c1, r0, r1)
        if o >= 1000 or o >= 0.5 * min(c1 - c0, r1 - r0):
            return True
    return False


# --- a run ---

@dataclass
class Run:
    folder: Path
    logs: dict[str, dict]            # part id -> editorial log
    plans: dict[str, dict]           # part id -> plan
    analyses: dict[str, dict]        # identity -> analysis
    order: list[str]                 # part ids in programme order
    spec: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.folder.name


def mastering_dir(folder: Path) -> Path:
    inner = folder / ".dubflow-work" / "mastering"
    return inner if inner.is_dir() else folder


def _part_key(part: str) -> tuple[int, int]:
    if part == "hook":
        return (0, 0)
    if part == "intro":
        return (1, 0)
    if part == "outro":
        return (3, 0)
    match = re.fullmatch(r"i(\d+)", part)
    return (2, int(match.group(1)) if match else 0)


def load_run(folder: Path) -> Run:
    mdir = mastering_dir(folder)
    logs = {p.stem.removeprefix("editorial-"): json.loads(p.read_text(encoding="utf-8")) for p in mdir.glob("editorial-*.json")}
    plans = {p.stem.removeprefix("plan-"): json.loads(p.read_text(encoding="utf-8")) for p in mdir.glob("plan-*.json")}
    analyses = {}
    for p in mdir.glob("analysis-*.json"):
        value = json.loads(p.read_text(encoding="utf-8"))
        analyses[value["identity"]] = value
    order = sorted(logs, key=_part_key)
    program = folder / ".dubflow-work" / "program.json"
    if program.is_file():
        listed = [p["id"] for p in json.loads(program.read_text(encoding="utf-8")).get("parts") or ()]
        order = [p for p in listed if p in logs] + [p for p in order if p not in listed]
    spec_path = folder / ".dubflow-work" / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {}
    return Run(folder, logs, plans, analyses, order, spec)


def candidates_of(log: Mapping[str, Any]) -> dict[str, dict]:
    return {c["id"]: c for c in log.get("candidates") or ()}


def top(unit: Mapping[str, Any], k: int = 5) -> list[str]:
    return list(dict.fromkeys([*(unit.get("chosen") or ()), *(a["candidate_id"] for a in unit.get("alternatives") or ())]))[:k]


def units_by_text(run: Run) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for part in run.order:
        for u in run.logs[part]["units"]:
            out.setdefault(norm(u.get("text") or ""), (part, u))
    return out


# --- score (the locked rules) ---

def score(gt: Mapping[str, Any], run: Run) -> dict[str, Any]:
    found = units_by_text(run)
    rows, unmatched = [], []
    for g in gt["units"]:
        hit = found.get(norm(g["text"]))
        if hit is None:
            unmatched.append(g["text"][:80])
            continue
        part, u = hit
        cands = candidates_of(run.logs[part])
        review = u.get("review") or {}
        ranges = g.get("ranges") or []
        order = [i for i in top(u) if i in cands]
        chosen = [cands[i] for i in u.get("chosen") or () if i in cands]
        row = {"unit": u["unit_id"], "kind": g["kind"], "evidence": review.get("evidence"), "state": review.get("state"),
               "chosen_hit": bool(ranges) and any(hits(c, ranges) for c in chosen)}
        for k in (1, 3, 5):
            row[f"hit_at_{k}"] = bool(ranges) and any(hits(cands[i], ranges) for i in order[:k])
        rows.append(row)
    footage = [r for r in rows if r["kind"] != "missing"]
    missing = [r for r in rows if r["kind"] == "missing"]
    ready = [r for r in rows if r["state"] == "READY"]

    def share(key: str) -> list[int]:
        return [sum(r[key] for r in footage), len(footage)]

    return {
        "run": run.name, "ground_truth_sha256": gt.get("sha256"), "units_scored": len(rows), "unmatched": unmatched,
        "recall_at_1": share("hit_at_1"), "recall_at_3": share("hit_at_3"), "recall_at_5": share("hit_at_5"),
        "chosen_hit": share("chosen_hit"),
        "false_source_insufficient": [r["unit"] for r in footage if r["evidence"] == "SOURCE_INSUFFICIENT"],
        "serious_silent_misses": [r["unit"] for r in footage if r["state"] == "READY" and not r["chosen_hit"]]
                                 + [r["unit"] for r in missing if r["state"] == "READY"],
        "missing_flagged": [sum(r["state"] != "READY" for r in missing), len(missing)],
        # READY precision: READY units whose chosen shots hit / READY units with footage
        "ready_precision": [sum(r["chosen_hit"] for r in ready if r["kind"] != "missing"),
                            sum(1 for r in ready if r["kind"] != "missing")],
        "ready_rate": [len(ready), len(rows)],
        # a flag on a unit whose chosen shots do hit (the panel asks for a look that was not needed)
        "flagged_but_right": [sum(r["state"] != "READY" and r["chosen_hit"] for r in footage), len(footage)],
        "flagged_and_wrong": [sum(r["state"] != "READY" and not r["chosen_hit"] for r in footage), len(footage)],
        "states": {s: sum(r["state"] == s for r in rows) for s in sorted({str(r["state"]) for r in rows})},
        "rows": rows,
    }


# --- replay: plan every part again with the code checked out now ---

def spoken_of(units: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[str, int]], int]:
    """(text, ms) of each sentence and the narration length, from the unit bounds ``align.units`` drew."""
    total = int(units[-1]["end_ms"])
    out = []
    for index, u in enumerate(units):
        length = int(u["end_ms"]) - int(u["start_ms"])
        if len(units) == 1:
            ms = length - LEAD_MS - TAIL_MS
        elif index == 0:
            ms = length - LEAD_MS - GAP_MS // 2
        elif index == len(units) - 1:
            ms = length - GAP_MS + GAP_MS // 2 - TAIL_MS
        else:
            ms = length - GAP_MS
        out.append((str(u["text"]), ms))
    return out, total


def _trim_of(log: Mapping[str, Any], source_id: str) -> tuple[int, int] | None:
    starts = [c["start_ms"] for c in log.get("candidates") or () if c["source_id"] == source_id]
    ends = [c["end_ms"] for c in log.get("candidates") or () if c["source_id"] == source_id]
    return (min(starts), max(ends)) if starts else None


def spec_part(spec: Mapping[str, Any], part: str) -> dict[str, Any]:
    """Title, kept source sound and trims of ``part`` as the run's spec gave them (empty when there is no spec)."""
    if part in ("hook", "intro", "outro"):
        return {"title": str(spec.get("title") or ""), "keep": "off", "trims": {}}
    items = spec.get("items") or []
    for index, item in enumerate(items, 1):
        if str(item.get("id") or item.get("item_id") or f"i{index}") != part:
            continue
        trims = {}
        for number, source in enumerate(item.get("sources") or (), 1):
            start, end = source.get("start_ms"), source.get("end_ms")
            if start is not None or end is not None:
                trims[f"{part}-s{number}"] = (int(start or 0), int(end) if end is not None else None)
        return {"title": str(item.get("title") or ""), "keep": str(item.get("keep_audio") or "auto"), "trims": trims}
    return {"title": "", "keep": "auto", "trims": {}}


def call_of(run: Run, part: str, shown: Sequence[str], *, evidence: Mapping[str, ev.Evidence] | None = None,
            log: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The arguments ``plan_part`` had at the run's final call for ``part`` (evidence may be replaced)."""
    log = log or run.logs[part]
    plan = run.plans[part]
    kind = "item" if part.startswith("i") and part[1:].isdigit() else part
    given = spec_part(run.spec, part)
    spoken, total = spoken_of(log["units"])
    footage = []
    for source in plan["sources"]:
        sid = source["source_id"]
        identity = (plan.get("provenance") or {}).get("inputs", {}).get(sid)
        analysis = run.analyses.get(identity) if identity else None
        if analysis is None and len(run.analyses) == 1:
            analysis = next(iter(run.analyses.values()))
        fps = source.get("fps") or {"numerator": 30, "denominator": 1}
        found = analysis_from(analysis)
        trim = given["trims"].get(sid)
        if trim is not None:
            trim = (trim[0], trim[1] if trim[1] is not None else found.duration_ms)
            trim = None if trim == (0, found.duration_ms) else trim
        footage.append(compiler.Footage(sid, Path(f"{sid}.media"), found, int(source["width"]),
                                        int(source["height"]), (int(fps["numerator"]), int(fps["denominator"])), (), "",
                                        trim, (source.get("rights") or {}).get("status", "unknown")))
    cands: dict[str, list[Candidate]] = {}
    for c in log.get("candidates") or ():
        cands.setdefault(c["source_id"], []).append(Candidate.from_dict(c))
    if evidence is None:
        evidence = {c["id"]: evidence_from(c["evidence"]) for c in log.get("candidates") or () if c.get("evidence")}
    groups = {c["id"]: c["group"] for c in log.get("candidates") or () if c.get("group")}
    return {"part": part, "kind": kind, "spoken": spoken, "narration_ms": total, "footage": footage, "candidates": cands,
            "evidence": evidence, "groups": groups, "edits": align.Edits.from_dict(log.get("edits") or {}),
            "options": m.Options(plan["options"]["level"], plan["options"]["style"]),
            "inputs": (plan.get("provenance") or {}).get("inputs") or {}, "shown": list(shown) if kind in ("intro", "outro") else [],
            "title": given["title"], "keep": given["keep"]}


def plan_again(call: Mapping[str, Any]) -> tuple[dict, dict]:
    made = compiler.plan_part(call["part"], call["kind"], call["spoken"], call["narration_ms"], call["options"],
                              lead_ms=LEAD_MS, gap_ms=GAP_MS, footage=call["footage"], candidates=call["candidates"],
                              evidence=call["evidence"], groups=call["groups"], edits=call["edits"], keep_audio=call["keep"],
                              title=call["title"], inputs=call["inputs"], shown=call["shown"])
    from engine.dubflow.mastering import review as reviewing
    reviewing.annotate(made.log, made.plan.to_dict())
    return made.plan.to_dict(), made.log


def replay(run: Run, *, evidence_for: Any = None, parts: Iterable[str] | None = None) -> Run:
    """The run planned again part by part (``evidence_for(part, log) -> evidence`` replaces a part's evidence)."""
    wanted = set(parts) if parts is not None else None
    logs, plans, shown = dict(run.logs), dict(run.plans), set()
    for part in run.order:
        if wanted is not None and part not in wanted:
            shown.update(c for u in run.logs[part]["units"] for c in u.get("chosen") or ())
            continue
        evidence = evidence_for(part, run.logs[part]) if evidence_for else None
        plan, log = plan_again(call_of(run, part, sorted(shown), evidence=evidence))
        log["candidates"] = run.logs[part].get("candidates")  # the candidates (and their times) as the run listed them
        logs[part], plans[part] = log, plan
        shown.update(c for u in log["units"] for c in u.get("chosen") or ())
    return Run(run.folder, logs, plans, run.analyses, run.order, run.spec)


def same_plan(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    keys = ("duration", "segments", "sources")
    return all(json.dumps(a.get(k), sort_keys=True) == json.dumps(b.get(k), sort_keys=True) for k in keys)


# --- attribution ---

NUMBER_WORDS = re.compile(r"\b(một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười|mươi|trăm|nghìn|ngàn|triệu|tỷ|tỉ)\b", re.I)
LATIN_PHRASE = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:of|the|and|[A-Z][a-z]+))+\b")


def hints(text: str) -> list[str]:
    out = []
    if re.search(r"\d", text) or len(NUMBER_WORDS.findall(text)) >= 2:
        out.append("number")
    if LATIN_PHRASE.search(text):
        out.append("latin_phrase")
    if re.search(r"\b(chữ|tiêu đề|dòng chữ|thẻ chữ|bảng chữ|ghi)\b", text, re.I):
        out.append("written")
    return out


def attribute(gt: Mapping[str, Any], run: Run) -> dict[str, Any]:
    """Each ground-truth unit: where its footage was lost (R1 … R6, the earliest stage that explains a chosen shot that
    misses), and its review outcome (R7), from the saved files and a Scorer replay over the part's candidates."""
    found = units_by_text(run)
    rows = []
    for g in gt["units"]:
        match = found.get(norm(g["text"]))
        if match is None:
            rows.append({"text": g["text"][:80], "stage": "UNMATCHED"})
            continue
        part, u = match
        log = run.logs[part]
        cands = candidates_of(log)
        ranges = g.get("ranges") or []
        review = u.get("review") or {}
        order = [i for i in top(u) if i in cands]
        chosen = [i for i in u.get("chosen") or () if i in cands]
        row: dict[str, Any] = {"unit": u["unit_id"], "kind": g["kind"], "text": g["text"], "ranges": ranges,
                               "state": review.get("state"), "evidence_state": review.get("evidence"), "hints": hints(g["text"]),
                               "top5": order, "chosen": chosen}
        if g["kind"] == "missing":
            row["stage"] = "MISSING_OK" if review.get("state") != "READY" else "R7_MISSING_READY"
            rows.append(row)
            continue
        pool = list(cands.values())
        hit_ids = [c["id"] for c in pool if hits(c, ranges)]
        row["hit_ids"] = hit_ids
        rank = next((k + 1 for k, i in enumerate(order) if i in hit_ids), None)
        row["rank"] = rank
        row["chosen_hit"] = any(i in hit_ids for i in chosen)
        trim = _trim_of(log, pool[0]["source_id"]) if pool else None
        looked = {c["id"] for c in pool if any(i["origin"] in ev.CONTENT_ORIGINS and (i.get("text") or i.get("labels"))
                                                  for i in (c.get("evidence") or {}).get("items") or ())}
        row["hit_looked"] = [i for i in hit_ids if i in looked]
        usable = [i for i in hit_ids if not (cands[i].get("quality") or {}).get("problems")]
        # a Scorer over the part's usable candidates, as the planner built it at its final call
        evidence = {c["id"]: evidence_from(c["evidence"]) for c in pool if c.get("evidence")}
        cand_objs = [Candidate.from_dict(c) for c in pool]
        scorer = align.Scorer([c for c in cand_objs if c.quality.usable] or cand_objs, evidence)
        unit = align.Unit(u["unit_id"], u["text"], int(u["start_ms"]), int(u["end_ms"]), "narration")
        rel = {c.candidate_id: scorer.relevance(unit, c)[0] for c in scorer.candidates}
        best = max(rel.values(), default=0)
        accept = max(align.MIN_RELEVANCE, align.ACCEPT_SHARE * best // 1000) if best >= align.MIN_RELEVANCE else None
        row["best_relevance"] = best
        row["hit_relevance"] = max((rel.get(i, 0) for i in hit_ids), default=0)
        row["hit_text"] = [((cands[i].get("evidence") or {}).get("items") or [{}])[-1].get("text", "")[:240] for i in row["hit_looked"]][:3]
        # where the source stood when this unit was planned (forward only): the end of the earlier units' pieces
        before = 0
        for other in log["units"]:
            if other["unit_id"] == u["unit_id"]:
                break
            for piece in other.get("pieces") or ():
                before = max(before, int(piece["offset_ms"]) + int(piece["length_ms"]))
        row["cursor_ms"] = before
        taken = {c for other in log["units"] if other["unit_id"] != u["unit_id"] for c in other.get("chosen") or ()}
        # the earliest stage where the unit's own footage was lost; a unit whose chosen shots hit is "OK"
        if row["chosen_hit"]:
            stage = "OK"
        elif not trim or not any(overlap(trim[0], trim[1], r0 * 1000, r1 * 1000) > 0 for r0, r1 in ranges):
            stage = "R1_SOURCE"
        elif not hit_ids:
            stage = "R2_DISCOVERY"
        elif not usable:
            stage = "R2_UNUSABLE"
        elif not row["hit_looked"]:
            stage = "R3_NOT_LOOKED"
        elif row["hit_relevance"] == 0:
            stage = "R4_NO_SHARED_WORDS"
        elif accept is not None and row["hit_relevance"] < accept:
            stage = "R4_WEAK_MATCH"
        elif all(cands[i]["end_ms"] <= before for i in hit_ids):
            stage = "R6_BEHIND_CURSOR"
        elif all(i in taken for i in row["hit_looked"]):
            stage = "R6_TAKEN_BY_OTHER"
        else:
            stage = "R5_RANKING"
        row["stage"] = stage
        if review.get("state") == "READY" and not row["chosen_hit"]:
            row["r7"] = "SILENT_MISS"
        elif review.get("state") != "READY" and not row["chosen_hit"]:
            row["r7"] = "FLAGGED_MISS"
        elif review.get("state") != "READY":
            row["r7"] = "FLAGGED_HIT"
        else:
            row["r7"] = "READY_HIT"
        rows.append(row)
    footage = [r for r in rows if r.get("kind") == "footage" or (r.get("kind") not in ("missing", None))]
    stages: dict[str, int] = {}
    for r in footage:
        stages[r["stage"]] = stages.get(r["stage"], 0) + 1
    r7: dict[str, int] = {}
    for r in footage:
        if "r7" in r:
            r7[r["r7"]] = r7.get(r["r7"], 0) + 1
    return {"run": run.name, "ground_truth_sha256": gt.get("sha256"), "footage_units": len(footage), "stages": stages,
            "review": r7, "rows": rows}


# --- experiments ---

def oracle_evidence(gt: Mapping[str, Any], run: Run, *, only_looked: bool) -> Run:
    """Oracle (upper bound, offline only): the shots that show a unit get a vision answer saying exactly its sentence.
    ``only_looked``: only shots the model did look at (isolates interpretation from acquisition)."""
    found = units_by_text(run)
    extra: dict[str, dict[str, str]] = {}
    for g in gt["units"]:
        match = found.get(norm(g["text"]))
        if match is None or not g.get("ranges"):
            continue
        part, _ = match
        for c in run.logs[part].get("candidates") or ():
            if hits(c, g["ranges"]):
                looked = any(i["origin"] == "vision" for i in (c.get("evidence") or {}).get("items") or ())
                if looked or not only_looked:
                    extra.setdefault(part, {})[c["id"]] = g["text"]

    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence]:
        out = {}
        for c in log.get("candidates") or ():
            found_ev = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
            text = extra.get(part, {}).get(c["id"])
            if text:
                found_ev.items = [i for i in found_ev.items if i.origin != "vision"]
                found_ev.add(ev.Item("vision", 900, text, (), "oracle"))
            out[c["id"]] = found_ev
        return out

    return replay(run, evidence_for=evidence_for, parts=list(extra))


def vision_texts(run: Run) -> dict[str, str]:
    """Candidate id -> the run's vision answer text for it (first vision item)."""
    out = {}
    for log in run.logs.values():
        for c in log.get("candidates") or ():
            for i in (c.get("evidence") or {}).get("items") or ():
                if i["origin"] == "vision":
                    out.setdefault(c["id"], i.get("text", ""))
                    break
    return out


def swap(run: Run, donor: Run, parts: Iterable[str] | None = None) -> Run:
    """``run`` planned again with ``donor``'s vision answers wherever the donor looked at the same shot (provider
    variability: the same frames, another cold-cache answer)."""
    donor_items: dict[str, list[dict]] = {}
    for log in donor.logs.values():
        for c in log.get("candidates") or ():
            items = [i for i in (c.get("evidence") or {}).get("items") or () if i["origin"] == "vision"]
            if items:
                donor_items.setdefault(c["id"], items)

    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence]:
        out = {}
        for c in log.get("candidates") or ():
            found_ev = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
            if c["id"] in donor_items:
                found_ev.items = [i for i in found_ev.items if i.origin != "vision"]
                for i in donor_items[c["id"]]:
                    found_ev.add(ev.Item("vision", int(i["confidence"]), i.get("text", ""), tuple(i.get("labels") or ()),
                                         i.get("detail", "")))
            out[c["id"]] = found_ev
        return out

    return replay(run, evidence_for=evidence_for, parts=parts)


def pooled(run: Run, donors: Sequence[Run]) -> Run:
    """``run`` planned again as if it had also looked at every shot one of ``donors`` looked at (the same frames, the
    donors' real cold-cache answers): a budget proxy with real answers, no new AI call. Its own answers are kept."""
    extra: dict[str, list[dict]] = {}
    for donor in donors:
        for log in donor.logs.values():
            for c in log.get("candidates") or ():
                items = [i for i in (c.get("evidence") or {}).get("items") or () if i["origin"] == "vision"]
                if items:
                    extra.setdefault(c["id"], items)
    added: set[str] = set()

    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence]:
        out = {}
        for c in log.get("candidates") or ():
            found_ev = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
            if not any(i.origin == "vision" for i in found_ev.items) and c["id"] in extra:
                added.add(c["id"])
                for i in extra[c["id"]]:
                    found_ev.add(ev.Item("vision", int(i["confidence"]), i.get("text", ""), tuple(i.get("labels") or ()),
                                         i.get("detail", "")))
            out[c["id"]] = found_ev
        return out

    again = replay(run, evidence_for=evidence_for)
    again.spec = {**again.spec, "_pooled_shots_added": len(added)}
    return again


def variability(runs: Sequence[Run]) -> dict[str, Any]:
    """How the same shots were described across cold-cache runs."""
    texts = [vision_texts(r) for r in runs]
    ids = sorted(set.intersection(*(set(t) for t in texts))) if texts else []
    same = sum(1 for i in ids if len({t[i] for t in texts}) == 1)

    def jaccard(a: str, b: str) -> float:
        x, y = ev.terms(a), ev.terms(b)
        return len(x & y) / max(1, len(x | y))

    pairs = [jaccard(texts[0][i], texts[k][i]) for i in ids for k in range(1, len(texts))]
    return {"runs": [r.name for r in runs], "looked_each": [len(t) for t in texts], "looked_in_all": len(ids),
            "identical_text": same, "mean_term_jaccard_vs_first": round(sum(pairs) / max(1, len(pairs)), 3),
            "union_looked": len(set().union(*(set(t) for t in texts))) if texts else 0}


def hint(gt: Mapping[str, Any], run: Run, window_s: float) -> dict[str, Any]:
    """User-guided retrieval (offline): the user says roughly where (the middle of the ground truth ± ``window_s``);
    rank only the shots inside that window with the run's own Scorer. Uses the ground truth only as the user's hint."""
    found = units_by_text(run)
    rows = []
    for g in gt["units"]:
        match = found.get(norm(g["text"]))
        if match is None or not g.get("ranges"):
            continue
        part, u = match
        pool = run.logs[part].get("candidates") or []
        mid = (g["ranges"][0][0] + g["ranges"][0][1]) / 2 * 1000
        inside = [c for c in pool if overlap(c["start_ms"], c["end_ms"], mid - window_s * 1000, mid + window_s * 1000) > 0]
        if not inside:
            continue
        evidence = {c["id"]: evidence_from(c["evidence"]) for c in pool if c.get("evidence")}
        cand_objs = [Candidate.from_dict(c) for c in pool]
        scorer = align.Scorer(cand_objs, evidence)
        unit = align.Unit(u["unit_id"], u["text"], int(u["start_ms"]), int(u["end_ms"]), "narration")
        ranked = sorted(inside, key=lambda c: (-scorer.relevance(unit, Candidate.from_dict(c))[0], c["start_ms"]))
        first_hit = next((k + 1 for k, c in enumerate(ranked) if hits(c, g["ranges"])), None)
        rows.append({"unit": u["unit_id"], "shots_in_window": len(inside), "rank": first_hit})
    n = len(rows)
    return {"run": run.name, "window_s": window_s, "units": n,
            "hit_at_1": [sum(1 for r in rows if r["rank"] == 1), n], "hit_at_3": [sum(1 for r in rows if r["rank"] and r["rank"] <= 3), n],
            "mean_shots_in_window": round(sum(r["shots_in_window"] for r in rows) / max(1, n), 1), "rows": rows}


# --- command line ---

def _brief(result: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "rows"}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    out_json = None
    if "--json" in rest:
        at = rest.index("--json")
        out_json = Path(rest[at + 1])
        rest = rest[:at] + rest[at + 2:]
    if command == "variability":
        print(json.dumps(variability([load_run(Path(p)) for p in rest]), ensure_ascii=False, indent=1))
        return 0
    gt = load_gt(Path(rest[0]), Path(rest[1]))
    folders = [Path(p) for p in rest[2:] if not p.startswith("--")]
    result: Any
    if command == "score":
        result = [score(gt, load_run(f)) for f in folders]
        print(json.dumps([_brief(r) for r in result], ensure_ascii=False, indent=1))
    elif command == "replay":
        run = load_run(folders[0])
        again = replay(run)
        equal = {p: same_plan(run.plans[p], again.plans[p]) for p in run.order}
        result = {"equal_plans": sum(equal.values()), "parts": len(equal), "different": [p for p, v in equal.items() if not v],
                  "saved": _brief(score(gt, run)), "replayed": _brief(score(gt, again))}
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif command == "attribute":
        result = attribute(gt, load_run(folders[0]))
        print(json.dumps(_brief(result), ensure_ascii=False, indent=1))
    elif command == "oracle":
        run = load_run(folders[0])
        result = {"saved": _brief(score(gt, run)), "replayed": _brief(score(gt, replay(run))),
                  "oracle_looked_only": _brief(score(gt, oracle_evidence(gt, run, only_looked=True))),
                  "oracle_all_hits": _brief(score(gt, oracle_evidence(gt, run, only_looked=False)))}
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif command == "swap":
        run, donor = load_run(folders[0]), load_run(folders[1])
        result = {"run": run.name, "donor": donor.name, "replayed": _brief(score(gt, replay(run))),
                  "with_donor_vision": _brief(score(gt, swap(run, donor)))}
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif command == "pooled":
        run, donors = load_run(folders[0]), [load_run(f) for f in folders[1:]]
        again = pooled(run, donors)
        result = {"run": run.name, "donors": [d.name for d in donors], "shots_added": again.spec["_pooled_shots_added"],
                  "replayed": _brief(score(gt, replay(run))), "pooled": _brief(score(gt, again))}
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif command == "hint":
        window = float(rest[rest.index("--window-s") + 1]) if "--window-s" in rest else 15.0
        result = hint(gt, load_run(folders[0]), window)
        print(json.dumps(_brief(result), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        return 2
    if out_json is not None:
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
