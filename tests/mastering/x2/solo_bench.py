"""SOLO automation engineering benchmark: a synthetic video whose answers are known by construction ($0, offline).

ENGINEERING_FIXTURE_ONLY = TRUE. Nothing here is REAL-DEV, REAL-HOLDOUT or a human label, and no result of it may
be reported as Human Validation or used to change Gate B/D or the READY threshold.

The fixture (made with ffmpeg, no download, no model):
- ``N_SHOTS`` coloured shots of ``SHOT_MS`` each; a moving box gives each shot motion. The shot boundaries are the
  ground truth of *where a picture is*.
- a transcript (cues) with one line per shot, placed at a fixed, varied offset inside its shot. Each line names a
  thing only that shot "shows" (a fixture assumption, stated: speech describes its own shot). The cue span is the
  ground truth of *where the words are said*.
- narration units of known kind: ``match`` (paraphrase of one shot's line), ``montage`` (two shots), and hard
  negatives with known answers: ``entity`` (thing never said), ``action`` (different verb), ``time`` (different
  year), ``none`` (unrelated). The answer for every hard negative is "no footage".

Measured per unit, against the construction (never against an AI judge):
- retrieval: is a shot of the right segment chosen (top-1) / among the unit's alternatives (recall@k);
- placement: does the played span overlap the cue span, and the start offset error (played start - cue start);
- verification state (``review.evidence_state``/``unit_state``): READY must stay 0 without picture evidence;
- no-footage handling: a hard negative given footage counts as a false assignment;
- Final QC on the whole part; wall time.
"""
from __future__ import annotations

import json, os, shutil, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENGINEERING_FIXTURE_ONLY = True
N_SHOTS, SHOT_MS = 12, 6000
# mid-light and mid-dark colours alternate so every cut is a clear luma step; noise gives texture (quality filter)
COLORS = ["0xB0B040", "0x405080", "0x60C0A0", "0x804040", "0xC0A060", "0x406040", "0xA0A0D0", "0x605020", "0xD09080",
          "0x304870", "0x90C060", "0x703060"]
# one line per shot: (thing, verb, place, year); every thing/verb/place word is unique to its shot
LINES = [("tractor", "crosses", "river", 2021), ("lighthouse", "flashes", "harbor", 2018),
         ("glacier", "melts", "valley", 2023), ("locomotive", "climbs", "mountain", 2019),
         ("volcano", "erupts", "island", 2025), ("orchard", "blossoms", "hillside", 2020),
         ("satellite", "orbits", "planet", 2017), ("submarine", "dives", "ocean", 2022),
         ("windmill", "spins", "meadow", 2016), ("bridge", "sways", "canyon", 2024),
         ("kangaroo", "jumps", "desert", 2015), ("cathedral", "glows", "plaza", 2014)]
CUE_AT = [1200, 3600, 400, 2800, 3900, 1800, 4100, 600, 3300, 2200, 4200, 1000]  # cue offset inside its shot
CUE_MS = 1700


@dataclass(frozen=True)
class Unit:
    uid: str
    kind: str  # match | montage | entity | action | time | none
    text: str
    shots: tuple[int, ...]  # segments that show it (empty: no footage by construction)


def cues() -> list[tuple[int, int, str]]:
    out = []
    for i, (thing, verb, place, year) in enumerate(LINES):
        start = i * SHOT_MS + CUE_AT[i]
        out.append((start, start + CUE_MS, f"In {year} the {thing} {verb} over the {place}."))
    return out


def units() -> list[Unit]:
    out = []
    for i in (0, 2, 4, 6, 8, 10, 11, 5):
        thing, verb, place, year = LINES[i]
        out.append(Unit(f"m{i}", "match", f"We watch the {thing} as it {verb} over the {place}.", (i,)))
    a, b = LINES[1], LINES[9]
    out.append(Unit("mt1", "montage", f"The {a[0]} {a[1]} and the {b[0]} {b[1]}.", (1, 9)))
    out += [Unit("e1", "entity", f"The helicopter {LINES[3][1]} over the {LINES[3][2]}.", ()),
            Unit("e2", "entity", f"A giraffe {LINES[7][1]} into the {LINES[7][2]}.", ()),
            Unit("a1", "action", f"The {LINES[0][0]} sinks in the swamp.", ()),
            Unit("a2", "action", f"The {LINES[4][0]} sleeps quietly.", ()),
            Unit("t1", "time", f"In 1959 the {LINES[2][0]} {LINES[2][1]} over the {LINES[2][2]}.", ()),
            Unit("n1", "none", "Grandmother bakes bread for the village festival.", ()),
            Unit("n2", "none", "Stock prices fell sharply after the announcement.", ())]
    # "time": the picture of the glacier exists, but not in 1959; by construction the sentence has no footage.
    return out


def make_video(path: Path, ffmpeg: str = "ffmpeg") -> None:
    inputs, chains = [], []
    for i in range(N_SHOTS):
        inputs += ["-f", "lavfi", "-i", f"color=c={COLORS[i]}:s=320x180:r=25:d={SHOT_MS / 1000}"]
        chains.append(f"[{i}:v]noise=alls=45:allf=t,drawbox=x='mod(t*60\\,260)':y=60:w=50:h=50:color=white@0.7:t=fill[v{i}]")
    joined = "".join(f"[v{i}]" for i in range(N_SHOTS))
    graph = ";".join(chains) + f";{joined}concat=n={N_SHOTS}:v=1:a=0[v]"
    total = N_SHOTS * SHOT_MS / 1000
    subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-y", *inputs, "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-filter_complex", graph, "-map", "[v]", "-map", f"{N_SHOTS}:a", "-t", str(total),
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _segment(ms: int) -> int:
    return ms // SHOT_MS


def cue_aligned(played, unit_text: str, by_id, said, lead_ms: int = 300):
    """Research simulation of a proposed in-point rule (NOT production): start a matched shot just before the cue
    inside it that shares the most terms with the sentence, keeping the piece's length inside the shot."""
    from engine.dubflow.mastering import evidence as ev
    wanted = ev.terms(unit_text)
    out = []
    for cid, start, end in played:
        cand, length = by_id[cid], end - start
        inside = [(len(ev.terms(t) & wanted), c0) for c0, c1, t in said if c0 < cand.end_ms and c1 > cand.start_ms]
        best = max(inside, default=(0, None))
        if best[0] and best[1] is not None:
            start = min(max(cand.start_ms, best[1] - lead_ms), max(cand.start_ms, cand.end_ms - length))
        out.append((cid, start, start + length))
    return out


def run(work: Path, *, with_cues: bool = True, ffmpeg: str | None = None, ffprobe: str | None = None,
        unit_ms: int = 3000, planner_hook: Callable[[Any], Any] | None = None,
        cue_inpoint_lead_ms: int | None = None) -> dict:
    """Build (or reuse) the fixture under ``work`` and score every unit. Returns the result document."""
    from engine.dubflow.mastering import analysis as scene, candidates as shots, plan as m, review as rv
    from engine.dubflow.mastering import final_qc as qc
    from engine.dubflow.mastering.planners import compilation as planner
    from engine.dubflow.product.job import run_ffmpeg

    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    ffprobe = ffprobe or shutil.which("ffprobe")
    work.mkdir(parents=True, exist_ok=True)
    video = work / "solo-fixture.mp4"
    if not video.is_file():
        make_video(video, ffmpeg)
    began = time.perf_counter()
    info = scene.probe(Path(ffprobe), video)
    found = scene.analyse(video, info, scene.cache_path(work, video), ffmpeg=Path(ffmpeg), run=run_ffmpeg)
    cands = shots.extract(video, "fx", found, work, ffmpeg=Path(ffmpeg), run=run_ffmpeg)
    said = tuple(cues()) if with_cues else ()
    footage = planner.Footage("fx", video, found, info.width, info.height, (25, 1), said, "", None, "owned", "")
    evidence = planner.evidence_for([footage], {"fx": cands}, {"fx": ""})
    by_id = {c.candidate_id: c for c in cands}
    rows = []
    options = m.Options("balanced", "entertainment")
    for unit in units():
        made = planner.plan_part(f"p-{unit.uid}", "item", [(unit.text, unit_ms)], unit_ms, options, lead_ms=200, gap_ms=300,
                                 footage=[footage], candidates={"fx": cands}, evidence=evidence, inputs={}, moment=False,
                                 cue_inpoint_lead_ms=cue_inpoint_lead_ms)
        log = rv.annotate(made.log, made.plan.to_dict())
        u = log["units"][0]
        pieces = u.get("pieces") or []
        played = [(p["id"], p["offset_ms"], p["offset_ms"] + p["length_ms"]) for p in pieces]
        coverage = u.get("coverage") or {}
        matched = bool(coverage.get("matched_footage"))
        alternatives = [a if isinstance(a, str) else a.get("id") for a in (u.get("alternatives") or [])]
        row = {"unit": unit.uid, "kind": unit.kind, "state": u["review"]["state"], "evidence": u["review"]["evidence"],
               "matched_footage": matched, "best_relevance": coverage.get("best_relevance"), "played": played}
        if unit.shots:
            gt_cues = [cues()[i] for i in unit.shots]
            seg_hit = [any(_segment(o) == s or _segment(e - 1) == s for _, o, e in played) for s in unit.shots]
            row["top1_segment_hit"] = bool(played) and _segment(played[0][1]) in unit.shots
            row["segments_hit"] = sum(seg_hit)
            ranked = [cid for cid, _, _ in played] + [a for a in alternatives if a]
            want = {cid for cid, c in by_id.items() for s in unit.shots if _overlap(c.start_ms, c.end_ms, s * SHOT_MS, (s + 1) * SHOT_MS) > 0}
            row["recall_at_5"] = bool(set(ranked[:5]) & want)
            row["cue_overlap_ms"] = sum(_overlap(o, e, c0, c1) for _, o, e in played for c0, c1, _ in gt_cues)
            first = played[0][1] if played else None
            row["start_offset_ms"] = None if first is None else first - gt_cues[0][0]
            moved = cue_aligned(played, unit.text, by_id, said) if matched else played
            row["sim_cue_overlap_ms"] = sum(_overlap(o, e, c0, c1) for _, o, e in moved for c0, c1, _ in gt_cues)
            row["sim_start_offset_ms"] = moved[0][1] - gt_cues[0][0] if moved else None
        else:
            row["false_assignment"] = matched  # footage claimed for a sentence the fixture has no picture for
        rows.append(row)
    # the whole narration as one part, for the review summary and Final QC
    spoken = [(u.text, unit_ms) for u in units()]
    whole = planner.plan_part("whole", "item", spoken, len(spoken) * (unit_ms + 300), options, lead_ms=200, gap_ms=300,
                              footage=[footage], candidates={"fx": cands}, evidence=evidence, inputs={}, moment=False,
                                 cue_inpoint_lead_ms=cue_inpoint_lead_ms)
    log = rv.annotate(whole.log, whole.plan.to_dict())
    checks = {c["id"]: c.get("ok", c.get("state")) for c in qc.check_parts([("whole", log, whole.plan.to_dict(), rv.Review())])}
    elapsed = time.perf_counter() - began
    match = [r for r in rows if r["kind"] in ("match", "montage")]
    negatives = [r for r in rows if "false_assignment" in r]
    summary = {
        "ENGINEERING_FIXTURE_ONLY": True, "with_cues": with_cues, "candidates": len(cands),
        "shots_detected": len(found.shots), "shots_true": N_SHOTS, "units": len(rows),
        "ready": sum(r["state"] == rv.READY for r in rows),
        "match_top1_segment": sum(bool(r.get("top1_segment_hit")) for r in match),
        "match_recall_at_5": sum(bool(r.get("recall_at_5")) for r in match),
        "match_played_over_cue": sum((r.get("cue_overlap_ms") or 0) > 0 for r in match),
        "match_units": len(match),
        "median_start_offset_ms": _median([r["start_offset_ms"] for r in match if r.get("start_offset_ms") is not None]),
        "sim_cue_aligned_played_over_cue": sum((r.get("sim_cue_overlap_ms") or 0) > 0 for r in match),
        "sim_cue_aligned_median_start_offset_ms": _median([r["sim_start_offset_ms"] for r in match
                                                           if r.get("sim_start_offset_ms") is not None]),
        "hard_negatives": len(negatives), "false_assignments": sum(r["false_assignment"] for r in negatives),
        "false_assignments_by_kind": {k: sum(r["false_assignment"] for r in negatives if r["kind"] == k)
                                      for k in ("entity", "action", "time", "none")},
        "whole_part_review": log.get("review_summary"), "final_qc": checks, "seconds": round(elapsed, 2)}
    return {"summary": summary, "units": rows}


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "solo-bench")
    result = {"cues": run(out), "no_cues": run(out, with_cues=False)}
    (out / "solo-bench.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v["summary"] for k, v in result.items()}, indent=1))
