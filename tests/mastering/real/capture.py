"""Record what the editorial planners are called with on a real run, so the same evidence can be planned again.

A real run (Bán hàng or raw Tổng hợp) asks the vision model about its keyframes, then calls the planner with the
candidates and every piece of evidence it gathered. ``python capture.py <calls-dir> <module> [args…]`` runs the
engine's job module (``engine.dubflow.product`` or ``engine.dubflow.compilation``) as usual and writes, for each
plan the job makes, the planner's arguments (``<plan-id>.call.json``, the last call wins: a second call after more
frames were looked at replaces the first) and what it returned (``<plan-id>.result.json``: plan and decision log).

``replay(call)`` plans the recorded call again with the code checked out now: the deterministic replay of the
Gate S directive (same cached evidence, old code versus new code). Nothing here reads or writes a key; the
recorded evidence is what the vision model answered, never the request.
"""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align  # noqa: E402
from engine.dubflow.mastering import analysis as scene  # noqa: E402
from engine.dubflow.mastering import evidence as ev  # noqa: E402
from engine.dubflow.mastering import plan as m  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate  # noqa: E402
from engine.dubflow.mastering.planners import compilation as compiler  # noqa: E402
from engine.dubflow.mastering.planners import product as seller  # noqa: E402

SCHEMA = "dubflow-planner-call-v1"


# --- evidence and inputs as JSON, and back ---

def evidence_from(value: Mapping[str, Any]) -> ev.Evidence:
    found = ev.Evidence(str(value["candidate_id"]))
    for item in value.get("items") or ():
        found.add(ev.Item(item["origin"], int(item["confidence"]), item.get("text", ""), tuple(item.get("labels") or ()),
                          item.get("detail", "")))
    box = value.get("product_box")
    found.product_box = m.Box(**box) if box else None
    found.hook = int(value.get("hook") or 0)
    found.product_visible = value.get("product_visible")
    found.same_product = value.get("same_product")
    return found


def analysis_from(value: Mapping[str, Any] | None) -> scene.Analysis | None:
    if not value:
        return None
    return scene.Analysis(int(value["duration_ms"]), tuple(value["cuts_ms"]), tuple(scene.Shot(*s) for s in value["shots"]),
                          tuple(tuple(s) for s in value["silences_ms"]), bool(value["has_audio"]), str(value["identity"]))


def _candidates(value: Mapping[str, Any]) -> dict[Any, list[Candidate]]:
    return {key: [Candidate.from_dict(c) for c in mine] for key, mine in value.items()}


def _common(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    return {"evidence": {k: e.to_dict() for k, e in kwargs["evidence"].items()},
            "groups": dict(kwargs.get("groups") or {}),
            "edits": (kwargs.get("edits") or align.Edits()).to_dict(),
            "inputs": dict(kwargs.get("inputs") or {}), "lead_ms": kwargs["lead_ms"], "gap_ms": kwargs["gap_ms"]}


def product_call(args: tuple, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    visuals, spoken, total_ms, options = args
    return {"schema": SCHEMA, "planner": "product", "plan_id": kwargs.get("plan_id", "product"),
            "visuals": [{"name": Path(v.path).name, "duration_ms": v.duration_ms, "width": v.width, "height": v.height,
                         "analysis": v.analysis.to_dict() if v.analysis else None} for v in visuals],
            "spoken": [list(s) for s in spoken], "total_ms": total_ms,
            "options": {"level": options.level, "style": options.style},
            "candidates": {str(k): [c.to_dict() for c in mine] for k, mine in kwargs["candidates"].items()},
            "facts": kwargs.get("facts", ""), "notes": list(kwargs.get("notes") or ()), **_common(kwargs)}


def compilation_call(args: tuple, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    part_id, kind, spoken, narration_ms, options = args
    return {"schema": SCHEMA, "planner": "compilation", "plan_id": part_id, "kind": kind,
            "spoken": [list(s) for s in spoken], "narration_ms": narration_ms,
            "options": {"level": options.level, "style": options.style},
            "footage": [{"source_id": f.source_id, "name": Path(f.path).name, "analysis": f.analysis.to_dict(), "width": f.width,
                         "height": f.height, "fps": list(f.fps), "cues": [list(c) for c in f.cues], "notes": f.notes,
                         "trim": list(f.trim) if f.trim else None, "rights": f.rights} for f in kwargs["footage"]],
            "candidates": {k: [c.to_dict() for c in mine] for k, mine in kwargs["candidates"].items()},
            "keep_audio": kwargs.get("keep_audio", "auto"), "title": kwargs.get("title", ""),
            "moment": kwargs.get("moment", True), "shown": list(kwargs.get("shown") or ()), **_common(kwargs)}


def replay(call: Mapping[str, Any]) -> tuple[m.Plan, dict]:
    """Plan the recorded call again with the current code: (plan, decision log)."""
    if call.get("schema") != SCHEMA:
        raise ValueError("not a recorded planner call")
    evidence = {k: evidence_from(v) for k, v in call["evidence"].items()}
    edits = align.Edits.from_dict(call["edits"])
    options = m.Options(call["options"]["level"], call["options"]["style"])
    if call["planner"] == "product":
        visuals = [seller.Visual(Path(v["name"]), v["duration_ms"], v["width"], v["height"], analysis_from(v.get("analysis")))
                   for v in call["visuals"]]
        candidates = {int(k): mine for k, mine in _candidates(call["candidates"]).items()}
        return seller.plan_editorial(visuals, [tuple(s) for s in call["spoken"]], call["total_ms"], options,
                                     lead_ms=call["lead_ms"], gap_ms=call["gap_ms"], candidates=candidates, evidence=evidence,
                                     groups=call["groups"], edits=edits, facts=call["facts"], inputs=call["inputs"],
                                     notes=call["notes"], plan_id=call["plan_id"])
    footage = [compiler.Footage(f["source_id"], Path(f["name"]), analysis_from(f["analysis"]), f["width"], f["height"],
                                tuple(f["fps"]), tuple(tuple(c) for c in f["cues"]), f["notes"],
                                tuple(f["trim"]) if f["trim"] else None, f["rights"]) for f in call["footage"]]
    made = compiler.plan_part(call["plan_id"], call["kind"], [tuple(s) for s in call["spoken"]], call["narration_ms"], options,
                              lead_ms=call["lead_ms"], gap_ms=call["gap_ms"], footage=footage,
                              candidates=_candidates(call["candidates"]), evidence=evidence, groups=call["groups"], edits=edits,
                              keep_audio=call["keep_audio"], title=call["title"], inputs=call["inputs"],
                              moment=call["moment"], shown=call["shown"])
    return made.plan, made.log


# --- the recording run ---

def _record(folder: Path, call: dict, plan: m.Plan, log: Mapping[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(call["plan_id"]))[:80]
    (folder / f"{name}.call.json").write_text(json.dumps(call, ensure_ascii=False), encoding="utf-8")
    (folder / f"{name}.result.json").write_text(json.dumps({"plan": plan.to_dict(), "log": log}, ensure_ascii=False),
                                                encoding="utf-8")


def install(folder: Path) -> None:
    """Wrap both planners so every call is recorded in ``folder`` (the jobs look them up on the module)."""
    plan_editorial, plan_part = seller.plan_editorial, compiler.plan_part

    def recorded_product(*args, **kwargs):
        plan, log = plan_editorial(*args, **kwargs)
        _record(folder, product_call(args, kwargs), plan, log)
        return plan, log

    def recorded_part(*args, **kwargs):
        made = plan_part(*args, **kwargs)
        _record(folder, compilation_call(args, kwargs), made.plan, made.log)
        return made

    seller.plan_editorial, compiler.plan_part = recorded_product, recorded_part


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: capture.py <calls-dir> <job module> [args…]", file=sys.stderr)
        return 2
    install(Path(argv[0]))
    sys.argv = [argv[1], *argv[2:]]
    runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
