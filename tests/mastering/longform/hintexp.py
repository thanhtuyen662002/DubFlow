"""Human-guided timestamp retrieval, offline (#832, proposal #815 option D). No AI call, no model, no planner change.

Read-only on saved W3 run folders. Three commands:

  study        The exploratory feasibility study over saved runs (2026-11-14). Groups are kept apart:
                 BASE      production ranking as saved (``w3.score``).
                 ORACLE5   window = ground-truth middle ± 5 s. The ground truth is the INPUT: a ceiling, never a
                           retrieval result.
                 SIM_e     simulated user mark = ground-truth middle + d, d ~ U[-e, e] (20 fixed seeds), and the worst
                           case d = ±e; search radius e. The ground truth builds the hint: a SIMULATION that measures
                           the precision a user would need, never a retrieval result.
                 POS_e     a hint without ground truth: the part's source trim (job input) and the sentence's place in
                           the part's narration, mapped linearly; search radius e. Ground truth only scores it.
               Each hint group also runs as a workflow (``+wf``): READY units keep the production pick and only
               flagged units get a mark. Ranking inside a window: ``rel`` (the run's own Scorer relevance, as
               ``w3.hint``) or ``dist`` (distance to the mark).
  suggest      For the human test: the shots inside ``--within`` seconds of a timestamp a PERSON gave, ranked
               like ``rel``. Prints candidate ids and source times only.
  score-human  For the human test: score a filled ``units.csv`` against locked ground truth, and apply the
               pre-registered criteria of docs/mastering/human-timestamp-test/PROTOCOL.md §6.

No output carries sentence text, file names or absolute paths.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.mastering.longform import w3  # noqa: E402
from tests.mastering.longform.w3 import align, Candidate, evidence_from, hits, norm, overlap  # noqa: E402

ERRS = (5, 15, 30)
SEEDS = range(20)
SUGGEST_K = 5

# the pre-registered criteria of the human test (PROTOCOL.md §6); changing one after a session voids that session
MIN_UNITS, MIN_VIDEOS, MIN_MISSING = 20, 2, 4
SUCCESS_MEDIAN_ERR_S, SUCCESS_WITHIN15 = 15.0, 0.70
SUCCESS_R5 = 0.75
SUCCESS_CHOSEN_MARGIN = -0.05
SUCCESS_TIME_RATIO, SUCCESS_CHOSEN_GAIN = 0.80, 0.15
NEGATIVE_MEDIAN_ERR_S, NEGATIVE_WITHIN15 = 30.0, 0.50
NEGATIVE_TIME_RATIO = 1.20


def mid(g: Mapping[str, Any]) -> float:
    """The middle of the first ground-truth range, in ms."""
    r0, r1 = g["ranges"][0]
    return (r0 + r1) / 2 * 1000


class Ctx:
    """One run: its units by text and one Scorer per part (built once)."""

    def __init__(self, run: w3.Run) -> None:
        self.run, self.found, self.sc = run, w3.units_by_text(run), {}

    def scorer(self, part: str) -> tuple[Any, dict[str, Candidate]]:
        if part not in self.sc:
            pool = self.run.logs[part].get("candidates") or []
            evd = {c["id"]: evidence_from(c["evidence"]) for c in pool if c.get("evidence")}
            objs = {c["id"]: Candidate.from_dict(c) for c in pool}
            self.sc[part] = (align.Scorer(list(objs.values()), evd), objs)
        return self.sc[part]

    def pos_hint(self, part: str, u: Mapping[str, Any]) -> float:
        """Without ground truth: the part's trim (spec, else candidate extent; both in ms) and the unit's place in the
        part's narration."""
        log = self.run.logs[part]
        sids = sorted({c["source_id"] for c in log.get("candidates") or ()})
        trims, _ = w3.trims_of(self.run, part, sids)
        t0, t1 = trims[0]
        if t1 == float("inf"):
            t1 = max(c["end_ms"] for c in log["candidates"])
        units = [x for x in log["units"] if x.get("function", "narration") == "narration"] or log["units"]
        total = max(x["end_ms"] for x in units) or 1
        frac = ((u["start_ms"] + u["end_ms"]) / 2) / total
        return t0 + frac * (t1 - t0)

    def rank(self, part: str, u: Mapping[str, Any], hint_ms: float, radius_s: float, how: str,
             source: str | None = None) -> list[dict]:
        pool = self.run.logs[part].get("candidates") or []
        lo, hi = hint_ms - radius_s * 1000, hint_ms + radius_s * 1000
        inside = [c for c in pool if overlap(c["start_ms"], c["end_ms"], lo, hi) > 0
                  and (source is None or c["source_id"] == source)]
        if how == "dist":
            return sorted(inside, key=lambda c: (abs((c["start_ms"] + c["end_ms"]) / 2 - hint_ms), c["start_ms"]))
        scorer, objs = self.scorer(part)
        unit = align.Unit(u["unit_id"], u["text"], int(u["start_ms"]), int(u["end_ms"]), "narration")
        return sorted(inside, key=lambda c: (-scorer.relevance(unit, objs[c["id"]])[0], c["start_ms"]))


Hint = Callable[[Mapping[str, Any], str, Mapping[str, Any]], float]


def evaluate(ctx: Ctx, gt: Mapping[str, Any], base_rows: Sequence[Mapping[str, Any]], hint_of: Hint, radius: float,
             how: str, workflow: bool) -> dict[str, Any]:
    """One group over the footage units (and the units without footage). ``hint_of(g, part, u)`` gives the mark in ms."""
    rows = []
    for g, b in zip(gt["units"], base_rows):
        m = ctx.found.get(norm(g["text"]))
        if g["kind"] == "missing":
            # a unit without footage gets a mark like any other flagged unit; a window with any shot would assign one
            if m is not None and (not workflow or b["state"] != "READY"):
                part, u = m
                got = ctx.rank(part, u, hint_of(g, part, u), radius, how)
                rows.append({"kind": "missing", "picked": bool(got), "marked": True})
            else:
                rows.append({"kind": "missing", "picked": False, "marked": False})
            continue
        if m is None:
            rows.append({"kind": "footage", "h1": False, "h3": False, "h5": False, "marked": False, "empty": False, "unmatched": True})
            continue
        part, u = m
        if workflow and b["state"] == "READY":
            rows.append({"kind": "footage", "h1": b["hit_at_1"], "h3": b["hit_at_3"], "h5": b["hit_at_5"], "marked": False,
                         "empty": False, "kept": True})
            continue
        got = ctx.rank(part, u, hint_of(g, part, u), radius, how)
        h = [hits(c, g["ranges"]) for c in got]
        rows.append({"kind": "footage", "h1": any(h[:1]), "h3": any(h[:3]), "h5": any(h[:5]), "marked": True,
                     "empty": not got, "n": len(got)})
    f = [r for r in rows if r["kind"] == "footage"]
    marked = [r for r in f if r["marked"]]
    return {"r1": sum(r["h1"] for r in f), "r3": sum(r["h3"] for r in f), "r5": sum(r["h5"] for r in f),
            "wrong_top1": sum(1 for r in f if r["marked"] and not r["empty"] and not r["h1"]),
            "empty_window": sum(r.get("empty", False) for r in f),
            "manual_needed": sum(1 for r in f if not r["h3"]),
            "marks": len(marked) + sum(r["marked"] for r in rows if r["kind"] == "missing"),
            "missing_given_a_pick": sum(r.get("picked", False) for r in rows if r["kind"] == "missing"),
            "mean_shots_in_window": round(statistics.mean([r["n"] for r in marked if "n" in r]), 1) if any("n" in r for r in marked) else 0}


def agg(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = results[0].keys()
    return {k: {"mean": round(statistics.mean(r[k] for r in results), 1), "min": min(r[k] for r in results),
                "max": max(r[k] for r in results)} for k in keys}


def study_run(gt: Mapping[str, Any], run: w3.Run) -> dict[str, Any]:
    """Every group for one run, in the order of the 2026-11-14 study output."""
    ctx = Ctx(run)
    base = w3.score(gt, run)
    rows = base["rows"]
    res: dict[str, Any] = {"BASE": {"r1": base["recall_at_1"][0], "r3": base["recall_at_3"][0], "r5": base["recall_at_5"][0],
                                    "chosen_hit": base["chosen_hit"][0], "silent_misses": len(base["serious_silent_misses"]),
                                    "flagged": sum(1 for r in rows if r["kind"] == "footage" and r["state"] != "READY"),
                                    "ready": base["ready_rate"][0], "footage": base["recall_at_5"][1]}}
    # how far the POS hint is from the truth (evaluation only; the hint itself never sees the truth)
    errs = []
    for g in gt["units"]:
        m = ctx.found.get(norm(g["text"]))
        if g["kind"] == "footage" and m:
            p = ctx.pos_hint(*m)
            errs.append(min(abs(p - (r0 + r1) / 2 * 1000) if not (r0 * 1000 <= p <= r1 * 1000) else 0 for r0, r1 in g["ranges"]) / 1000)
    res["POS_error_s"] = {"median": round(statistics.median(errs), 1), "le5": sum(e <= 5 for e in errs),
                          "le15": sum(e <= 15 for e in errs), "le30": sum(e <= 30 for e in errs), "n": len(errs)}
    idx = {id(g): i for i, g in enumerate(gt["units"])}

    def truth_or_pos(g: Mapping[str, Any], p: str, u: Mapping[str, Any]) -> float:
        # a unit without footage has no truth to centre on; it gets the POS mark in every group
        return mid(g) if g.get("ranges") else ctx.pos_hint(p, u)

    for how in ("rel", "dist"):
        for wf in (False, True):
            tag = f"{how}{'+wf' if wf else ''}"
            res[f"ORACLE5:{tag}"] = evaluate(ctx, gt, rows, truth_or_pos, 5, how, wf)
            for e in ERRS:
                sims = []
                for s in SEEDS:
                    rnd = random.Random(f"{s}")
                    offs = {i: rnd.uniform(-e, e) * 1000 for i in range(len(gt["units"]))}
                    sims.append(evaluate(ctx, gt, rows, lambda g, p, u, offs=offs: truth_or_pos(g, p, u) + offs[idx[id(g)]], e, how, wf))
                res[f"SIM{e}:{tag}"] = agg(sims)
                worst = [evaluate(ctx, gt, rows, lambda g, p, u, sg=sg: truth_or_pos(g, p, u) + sg * e * 1000, e, how, wf)
                         for sg in (-1, 1)]
                res[f"SIMworst{e}:{tag}"] = agg(worst)
                res[f"POS{e}:{tag}"] = evaluate(ctx, gt, rows, lambda g, p, u: ctx.pos_hint(p, u), e, how, wf)
    return res


def study(gt: Mapping[str, Any], runs: Mapping[str, w3.Run]) -> dict[str, Any]:
    report: dict[str, Any] = {"ground_truth_sha256": gt["sha256"], "runs": {}}
    for name, run in runs.items():
        report["runs"][name] = study_run(gt, run)
    return report


def dumps(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=1)


# --- the human test ---

def parse_time(text: str) -> float:
    """'mm:ss', 'h:mm:ss' or seconds, to ms."""
    parts = [float(p) for p in str(text).strip().split(":")]
    if not parts or len(parts) > 3 or any(p < 0 for p in parts):
        raise ValueError(f"not a timestamp: {text!r}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds * 1000


def unit_by_id(run: w3.Run, unit_id: str) -> tuple[str, dict]:
    for part in run.order:
        for u in run.logs[part]["units"]:
            if u["unit_id"] == unit_id:
                return part, u
    raise KeyError(f"no unit {unit_id} in this run")


def suggest(run: w3.Run, unit_id: str, at_ms: float, within_s: float, k: int = SUGGEST_K,
            source: str | None = None) -> list[dict[str, Any]]:
    """The shots a person's timestamp points to: inside ±within_s, ranked by the run's own Scorer (``rel``)."""
    ctx = Ctx(run)
    part, u = unit_by_id(run, unit_id)
    ranked = ctx.rank(part, u, at_ms, within_s, "rel", source=source)[:k]
    return [{"rank": n, "id": c["id"], "source": c["source_id"], "start_s": round(c["start_ms"] / 1000, 2),
             "end_s": round(c["end_ms"] / 1000, 2)} for n, c in enumerate(ranked, 1)]


def error_s(at_ms: float, ranges: Sequence[Sequence[float]]) -> float:
    """Seconds from a mark to the nearest ground-truth range (0 inside one)."""
    t = at_ms / 1000
    return min(0.0 if r0 <= t <= r1 else min(abs(t - r0), abs(t - r1)) for r0, r1 in ranges)


def _num(value: Any) -> float | None:
    text = str(value if value is not None else "").strip().replace(",", ".")
    return float(text) if text else None


def _yes(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "yes", "y", "true", "có", "co", "x")


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _share(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def score_human(rows: Sequence[Mapping[str, Any]], gts: Mapping[str, Mapping[str, Any]],
                runs: Mapping[str, w3.Run]) -> dict[str, Any]:
    """Score the filled form of a human session (one row per sentence and condition) and apply PROTOCOL.md §6."""
    per: dict[str, list[dict]] = {"RP": [], "TS": []}
    deviations = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        cond = str(row.get("condition", "")).strip().upper()
        video, unit_id = str(row.get("video", "")).strip(), str(row.get("unit_id", "")).strip()
        if cond not in per or video not in runs or video not in gts:
            deviations.append(f"row skipped: condition or video not known ({cond or '-'}, {video or '-'})")
            continue
        key = (str(row.get("participant_code", "")).strip(), video, unit_id)
        if key in seen:
            deviations.append(f"{video}/{unit_id}: the same person did this sentence twice (PROTOCOL §3.1)")
        seen.add(key)
        run = runs[video]
        part, u = unit_by_id(run, unit_id)
        g = next((x for x in gts[video]["units"] if norm(x["text"]) == norm(u.get("text") or "")), None)
        if g is None:
            deviations.append(f"{video}/{unit_id}: no ground truth for this sentence")
            continue
        cands = w3.candidates_of(run.logs[part])
        ranges = g.get("ranges") or []
        footage = g["kind"] != "missing"
        chosen = str(row.get("chosen_id", "")).strip()
        marked_missing = _yes(row.get("marked_missing"))
        out: dict[str, Any] = {"video": video, "footage": footage, "marked_missing": marked_missing,
                               "chosen_right": (bool(chosen) and chosen in cands and hits(cands[chosen], ranges)) if footage
                               else marked_missing,
                               "assigned_on_missing": (not footage) and bool(chosen) and not marked_missing,
                               "missed_as_missing": footage and marked_missing,
                               "total_s": _num(row.get("total_s")), "actions": _num(row.get("actions")),
                               "re_searches": _num(row.get("re_searches")), "assisted": _yes(row.get("assisted"))}
        if cond == "RP":
            order = [i for i in w3.top(u) if i in cands]
            for k in (1, 3, 5):
                out[f"h{k}"] = footage and any(hits(cands[i], ranges) for i in order[:k])
        else:
            first = str(row.get("ts_hint", "")).strip()
            within = _num(row.get("ts_precision_s")) or 15.0
            if first:
                at = parse_time(first)
                got = [c["id"] for c in suggest(run, unit_id, at, within)]
                out["err_s"] = round(error_s(at, ranges), 1) if footage else None
                for k in (1, 3, 5):
                    out[f"h{k}"] = footage and any(hits(cands[i], ranges) for i in got[:k])
            else:
                deviations.append(f"{video}/{unit_id}: TS row without a timestamp")
                for k in (1, 3, 5):
                    out[f"h{k}"] = False
        per[cond].append(out)

    def summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        f = [x for x in items if x["footage"]]
        miss = [x for x in items if not x["footage"]]
        times = [x["total_s"] for x in items if x["total_s"] is not None]
        res = {"units": len(items), "footage": len(f), "missing": len(miss),
               "r1": sum(x["h1"] for x in f), "r3": sum(x["h3"] for x in f), "r5": sum(x["h5"] for x in f),
               "chosen_right_footage": sum(x["chosen_right"] for x in f),
               "missing_detected": sum(x["chosen_right"] for x in miss),
               "assigned_on_missing": sum(x["assigned_on_missing"] for x in miss),
               "footage_marked_missing": sum(x["missed_as_missing"] for x in f),
               "median_time_s": _median(times),
               "median_actions": _median([x["actions"] for x in items if x["actions"] is not None]),
               "re_searches": sum(x["re_searches"] or 0 for x in items),
               "assisted": sum(x["assisted"] for x in items)}
        errs = [x["err_s"] for x in f if x.get("err_s") is not None]
        if errs:
            res["timestamp_error_s"] = {"median": _median(errs), "le5": sum(e <= 5 for e in errs),
                                        "le15": sum(e <= 15 for e in errs), "le30": sum(e <= 30 for e in errs), "n": len(errs)}
        return res

    rp, ts = summary(per["RP"]), summary(per["TS"])
    verdict, reasons = decide(rp, ts, videos=len({x["video"] for x in per["RP"] + per["TS"]}), deviations=deviations)
    return {"label": "HUMAN (real participants)", "RP": rp, "TS": ts, "verdict": verdict, "reasons": reasons,
            "deviations": deviations}


def decide(rp: Mapping[str, Any], ts: Mapping[str, Any], *, videos: int, deviations: Sequence[str]) -> tuple[str, list[str]]:
    """PROTOCOL.md §6, fixed before any session."""
    reasons = []
    units, missing = rp["units"] + ts["units"], rp["missing"] + ts["missing"]
    if units < MIN_UNITS or videos < MIN_VIDEOS or missing < MIN_MISSING or deviations:
        if units < MIN_UNITS:
            reasons.append(f"{units} sentences < {MIN_UNITS}")
        if videos < MIN_VIDEOS:
            reasons.append(f"{videos} videos < {MIN_VIDEOS}")
        if missing < MIN_MISSING:
            reasons.append(f"{missing} sentences without footage < {MIN_MISSING}")
        if deviations:
            reasons.append(f"{len(deviations)} protocol deviation(s)")
        return "INCONCLUSIVE", reasons
    err = ts.get("timestamp_error_s") or {}
    med, n = err.get("median"), err.get("n", 0)
    within15 = _share(err.get("le15", 0), n)
    chosen_rp, chosen_ts = _share(rp["chosen_right_footage"], rp["footage"]), _share(ts["chosen_right_footage"], ts["footage"])
    time_ratio = (ts["median_time_s"] / rp["median_time_s"]) if rp["median_time_s"] and ts["median_time_s"] is not None else None
    negative = []
    if med is None or med > NEGATIVE_MEDIAN_ERR_S or (within15 is not None and within15 < NEGATIVE_WITHIN15):
        negative.append(f"timestamp error median {med} s, within ±15 s {within15}")
    if time_ratio is not None and time_ratio >= NEGATIVE_TIME_RATIO and (chosen_ts or 0) <= (chosen_rp or 0):
        negative.append(f"TS time {round(time_ratio, 2)}x RP with no gain in right choices")
    if ts["assigned_on_missing"] > rp["assigned_on_missing"]:
        negative.append("TS assigned a shot to more sentences without footage than RP")
    if negative:
        return "NEGATIVE", negative
    checks = {
        "median error ≤ 15 s": med is not None and med <= SUCCESS_MEDIAN_ERR_S,
        "≥ 70 % within ±15 s": within15 is not None and within15 >= SUCCESS_WITHIN15,
        "TS recall@5 ≥ 75 %": ts["footage"] > 0 and ts["r5"] / ts["footage"] >= SUCCESS_R5,
        "TS right choices not 5 points below RP": chosen_ts is not None and chosen_rp is not None
                                                  and chosen_ts - chosen_rp >= SUCCESS_CHOSEN_MARGIN,
        "TS ≥ 20 % faster or ≥ 15 points more right": (time_ratio is not None and time_ratio <= SUCCESS_TIME_RATIO)
                                                      or (chosen_ts is not None and chosen_rp is not None
                                                          and chosen_ts - chosen_rp >= SUCCESS_CHOSEN_GAIN),
        "TS no worse on sentences without footage": ts["assigned_on_missing"] <= rp["assigned_on_missing"],
    }
    if all(checks.values()):
        return "SUCCESS", list(checks)
    return "INCONCLUSIVE", [k for k, ok in checks.items() if not ok]


# --- command line ---

def _arg(rest: list[str], name: str, many: bool = False) -> Any:
    values = [rest[i + 1] for i, a in enumerate(rest[:-1]) if a == name]
    if many:
        return values
    if not values:
        raise SystemExit(f"missing {name}")
    return values[-1]


def _pairs(values: Sequence[str]) -> dict[str, str]:
    out = {}
    for v in values:
        name, _, path = v.partition("=")
        if not name or not path:
            raise SystemExit(f"expected NAME=PATH, got {v!r}")
        out[name] = path
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("study", "suggest", "score-human"):
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "study":
        gt = w3.load_gt(Path(_arg(rest, "--gt")), Path(_arg(rest, "--lock")))
        runs = {name: w3.load_run(Path(path)) for name, path in _pairs(_arg(rest, "--run", many=True)).items()}
        report = study(gt, runs)
        out = Path(_arg(rest, "--out"))
        out.write_text(dumps(report), encoding="utf-8")
        for name, res in report["runs"].items():
            print(name, json.dumps(res["BASE"]), json.dumps(res["POS_error_s"]), flush=True)
        return 0
    if command == "suggest":
        run = w3.load_run(Path(_arg(rest, "--run")))
        source = _arg(rest, "--source", many=True)
        rows = suggest(run, _arg(rest, "--unit"), parse_time(_arg(rest, "--at")), float(_arg(rest, "--within")),
                       source=source[-1] if source else None)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    gts = {}
    for name, pair in _pairs(_arg(rest, "--gt", many=True)).items():
        gt_path, _, lock = pair.partition(",")
        gts[name] = w3.load_gt(Path(gt_path), Path(lock))
    runs = {name: w3.load_run(Path(path)) for name, path in _pairs(_arg(rest, "--run", many=True)).items()}
    with open(_arg(rest, "--units"), encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = score_human(rows, gts, runs)
    result["ground_truth_sha256"] = {name: gt["sha256"] for name, gt in gts.items()}
    Path(_arg(rest, "--out")).write_text(dumps(result), encoding="utf-8")
    print(result["verdict"], "; ".join(result["reasons"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
