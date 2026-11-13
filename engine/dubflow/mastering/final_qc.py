"""Final QC of an Assisted video (directive "Assisted Editorial Production" §B, Finalization/QC Gate).

A successful render is not a passed QC. Before a Bán hàng video or a Tổng hợp programme is finalised, this reads
what the run already holds (the plans, the decision logs, the user's review files, the status, the captions) and,
when FFmpeg is given, measures the output itself:

* ``render``: the run finished (COMPLETED) and the output file exists;
* ``timeline``: every plan parses (no segment past its source's end, no gap in the timeline) and no piece is
  negative, empty, overlapping or a video frame frozen to fill time;
* ``duration``: the output lasts what the plan says, within one 25 fps frame and a little (``SYNC_TOLERANCE_MS``);
* ``captions``: no caption runs past the end of the video;
* ``audio``: no clipping (true peak above ``CLIP_DBTP``) in the output;
* ``pending``: no pin or exclusion the user made after the last render (it would not be in the video);
* ``decisions``: no sentence left unreviewed unless the user accepted exporting it;
* ``claims``: no sentence the queue calls critical left unsettled, accepted or not, is shown as if it were checked:
  every one is counted and listed by number (the first ``LISTED``, then how many more), every time. A sentence about
  something displayed whose shots contradict or do not show it is critical (#817); one the user settled by hand
  passes and is named as the user's acceptance, its claim still unconfirmed. The line is for a person and bounded;
  the check's ``units`` (the result's ``critical_units``) and ``user_accepted`` list every such sentence for a
  machine: ``{"part", "unit_id", "number", "claims": [{"label", "status"}]}`` (the claims still unresolved).

The result's ``state`` maps the workflow states: ``RENDERING``, ``RENDER_PENDING`` (decisions not rendered yet),
``REVIEW_PENDING``, ``FINAL_QC_FAILED`` or ``READY_TO_FINALIZE``. ``Finalized``/``Stale Final`` are the host's (it
holds the final export's record).

``python -m engine.dubflow.mastering.final_qc --work DIR (--video N | --program) [--ffmpeg F --ffprobe P]`` prints it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence

from . import align, claims, plan as m, quality, review as reviewing

SYNC_TOLERANCE_MS = 120
LISTED = 12  # sentences named on a line; the rest are counted, never dropped silently
ACCEPTED_LISTED = 6  # the user's own acceptances, informational (the desktop shows 400 characters of a detail)
CLIP_DBTP = 0.0
FAILING_CODES = ("NEGATIVE", "EMPTY", "GAP", "OVERLAP", "HELD_FRAME")
_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})")


def _check(check_id: str, label: str, ok: bool | None, detail: str = "") -> dict[str, Any]:
    """``ok`` None: not measured (said so, never counted as passed)."""
    return {"id": check_id, "label": label, "ok": ok, "detail": detail}


def _read(path: Path, limit: int = 32 * 1024 * 1024) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def caption_end_ms(text: str) -> int:
    ends = [((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(ms) for *_, h, mi, s, ms in _SRT_TIME.findall(text)]
    return max(ends, default=0)


def pending(log: Mapping[str, Any], review: reviewing.Review) -> list[str]:
    """Sentences whose pins or exclusions the user changed after the plan the video was rendered from."""
    used = align.Edits.from_dict(log.get("edits"))
    wanted = review.edits()
    out = []
    for unit_id, pin in wanted.pin.items():
        if used.pin.get(unit_id) != pin:
            out.append(unit_id)
    # footage the video shows that an exclusion now leaves out (#805): an id excluded again for the other footage it
    # names now has the same id as before, so the ids alone do not tell; what the final shows does. Footage the plan
    # had to use anyway (all excluded) is a conflict of its own, not a change waiting for a render.
    pool = reviewing._log_candidates(log)
    reused = set(((log.get("exclusions") or {}).get("reused")) or ())
    shown = {i for u in log.get("units") or () for i in u.get("chosen") or ()} - reused
    for unit_id, decision in review.units.items():
        if decision.invalidated or unit_id in out:
            continue
        if any(i not in used.exclude for i in decision.exclude) or (shown and review._covering(pool, decision, shown)):
            out.append(unit_id)
    # an exclusion taken back after the render (#805): "Bỏ loại" names the sentence that let it back in; an undo or a
    # reset does too, and anything still unaccounted for is never dropped
    taken_back = used.exclude - wanted.exclude
    for entry in reversed(review.records):
        if not taken_back:
            break
        named = set(entry.get("shots") or ()) & taken_back if entry.get("detail") == "include" else set()
        if named or entry.get("detail") in ("undo", "reset"):
            if entry.get("speech_ref") and entry["speech_ref"] not in out:
                out.append(entry["speech_ref"])
            taken_back -= named or taken_back
    if taken_back:
        first = next((u["unit_id"] for u in log.get("units") or () if u.get("unit_id")), None)
        if first and first not in out:
            out.append(first)
    return out


def check_parts(parts: Sequence[tuple[str, Mapping[str, Any] | None, Mapping[str, Any] | None, reviewing.Review]]
                ) -> list[dict[str, Any]]:
    """The content checks over every (part id, decision log, plan dict, review) of the video."""
    timeline, waiting, open_units, critical, user_accepted = [], [], [], [], []
    critical_units: list[dict[str, Any]] = []  # every one, for a machine: the line names only the first LISTED
    accepted_units: list[dict[str, Any]] = []

    def entry(unit: Mapping[str, Any]) -> dict[str, Any]:
        return {"part": part, "unit_id": unit["unit_id"], "number": reviewing_number(unit["unit_id"]),
                "claims": [dict(c) for c in unit.get("claims") or () if c["status"] in (claims.CONTRADICTED, claims.UNVERIFIED)]}
    accepted = True
    for part, log, plan_dict, review in parts:
        label = f"{part}: " if len(parts) > 1 else ""
        if not isinstance(log, Mapping) or not isinstance(plan_dict, Mapping):
            timeline.append(f"{label}thiếu kế hoạch dựng")
            continue
        try:
            plan = m.Plan.from_dict(plan_dict)  # a segment past its source's end or a gap does not parse
        except (m.PlanError, ValueError, KeyError, TypeError) as error:
            timeline.append(f"{label}kế hoạch không hợp lệ ({str(error)[:120]})")
            continue
        problems = [p for p in quality.timeline_problems(quality.pieces_from_plan(plan, part=part),
                                                         duration_ms=m.to_milliseconds(plan.duration, plan.time_base))
                    if p["code"] in FAILING_CODES]
        timeline += [f"{label}{p['code']} {p['unit']} {p['detail']}" for p in problems[:5]]
        waiting += [f"{label}câu {reviewing_number(u)}" for u in pending(log, review)]
        shown = reviewing.view(log, plan_dict, review)
        if shown["unreviewed"]:
            open_units += [f"{label}câu {reviewing_number(u)}" for u in shown["unreviewed"]]
            accepted = accepted and review.accept_unreviewed
        critical += [f"{label}câu {reviewing_number(u['unit_id'])}" for u in shown["units"] if u.get("priority") == reviewing.CRITICAL]
        critical_units += [entry(u) for u in shown["units"] if u.get("priority") == reviewing.CRITICAL]
        # a displayed claim the shots do not show that the user settled (approved, no suitable footage): the user's
        # word, said so; the claim is not counted as confirmed. A pinned sentence has no claim left once replanned: the
        # planner checks none on the user's own shot
        settled = [u for u in shown["units"] if u.get("priority") is None and u.get("decision")
                   and any(c["status"] in (claims.CONTRADICTED, claims.UNVERIFIED) for c in u.get("claims") or ())]
        user_accepted += [f"{label}câu {reviewing_number(u['unit_id'])}" for u in settled]
        accepted_units += [entry(u) for u in settled]
    checks = [
        _check("timeline", "Dòng thời gian", not timeline, "; ".join(timeline[:8]) or "không có khoảng trống, chồng lấn, đoạn âm hay khung đứng"),
        _check("pending", "Lựa chọn đã render", not waiting,
               ("chưa render lại sau khi bạn đổi cảnh: " + ", ".join(waiting[:10])) if waiting else "mọi lựa chọn đã có trong video"),
    ]
    if not open_units:
        checks.append(_check("decisions", "Duyệt", True, "mọi câu đã đạt hoặc đã được bạn xử lý"))
    else:
        checks.append(_check("decisions", "Duyệt", accepted,
                             f"{len(open_units)} câu chưa duyệt" + (" (bạn đã chấp nhận xuất)" if accepted else "")))
    detail = (f"{len(critical)} câu chưa xử lý, không được coi là đã kiểm: " + listed(critical)) if critical \
        else "không còn mục nghiêm trọng"
    if user_accepted:
        detail += "; cảnh chưa xác nhận điều câu nói, người dùng đã chấp nhận: " + listed(user_accepted, ACCEPTED_LISTED)
    checks.append({**_check("claims", "Mục nghiêm trọng", not critical, detail),
                   "units": critical_units, "user_accepted": accepted_units})
    return checks


def listed(names: Sequence[str], limit: int = LISTED) -> str:
    """The first ``limit`` names, then how many more: "câu 1, …, câu 12 và 3 câu khác"."""
    shown = ", ".join(names[:limit])
    return shown + (f" và {len(names) - limit} câu khác" if len(names) > limit else "")


def reviewing_number(unit_id: str) -> str:
    found = re.search(r"(\d+)$", unit_id)
    return found.group(1) if found else unit_id


def probe_ms(ffprobe: Path, path: Path) -> int | None:
    try:
        done = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return round(float(done.stdout.strip()) * 1000) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def check_output(video: Path | None, expected_ms: int | None, captions: Path | None, *,
                 probe: Callable[[Path], int | None] | None = None,
                 peak: Callable[[Path], float | None] | None = None) -> list[dict[str, Any]]:
    """What the output file itself says: its length against the plan, its captions, its audio peak."""
    if video is None or not video.is_file():
        return [_check("render", "Bản dựng", False, "không tìm thấy video đã dựng")]
    checks = [_check("render", "Bản dựng", True, video.name)]
    actual = probe(video) if probe else None
    if actual is None or expected_ms is None:
        checks.append(_check("duration", "Thời lượng", None, "chưa đo"))
    else:
        checks.append(_check("duration", "Thời lượng", abs(actual - expected_ms) <= SYNC_TOLERANCE_MS,
                             f"video {actual} ms, kế hoạch {expected_ms} ms"))
    if captions is not None and captions.is_file():
        end = caption_end_ms(captions.read_text(encoding="utf-8", errors="replace"))
        limit = (actual or expected_ms or 0) + SYNC_TOLERANCE_MS
        checks.append(_check("captions", "Phụ đề", end <= limit, f"phụ đề cuối kết thúc ở {end} ms"))
    else:
        checks.append(_check("captions", "Phụ đề", None, "không có tệp phụ đề"))
    top = peak(video) if peak else None
    if top is None:
        checks.append(_check("audio", "Âm thanh", None, "chưa đo đỉnh âm"))
    else:
        checks.append(_check("audio", "Âm thanh", top <= CLIP_DBTP, f"đỉnh {top:.1f} dBTP"))
    return checks


def result(checks: list[dict[str, Any]], status_state: str | None) -> dict[str, Any]:
    by = {c["id"]: c for c in checks}
    if status_state == "RUNNING":
        state = "RENDERING"
    elif by.get("pending", {}).get("ok") is False:
        state = "RENDER_PENDING"
    elif by.get("decisions", {}).get("ok") is False:
        state = "REVIEW_PENDING"
    elif status_state != "COMPLETED" or any(c["ok"] is False for c in checks):
        state = "FINAL_QC_FAILED"
    else:
        state = "READY_TO_FINALIZE"
    return {"schema_version": 1, "kind": "final_qc", "state": state, "passed": state == "READY_TO_FINALIZE",
            "checks": checks, "unmeasured": [c["id"] for c in checks if c["ok"] is None],
            # every critical sentence, none dropped: the "claims" line names the first LISTED and counts the rest
            "critical_units": list(by.get("claims", {}).get("units") or ())}


def _load_review(path: Path) -> reviewing.Review:
    try:
        return reviewing.load(path)
    except (reviewing.ReviewError, ValueError, OSError):
        return reviewing.Review()


def product_qc(work: Path, number: int, **measure: Any) -> dict[str, Any]:
    status = _read(work / "status.json", 4 * 1024 * 1024) or {}
    entry = next((v for v in status.get("videos") or () if isinstance(v, dict) and v.get("index") == number), {})
    checks = check_parts([(f"video {number}", _read(work / f"editorial-{number}.json"),
                           _read(work / f"mastering_plan-{number}.json"), _load_review(reviewing.path_for(work, number)))])
    video = Path(entry["path"]) if entry.get("ok") and isinstance(entry.get("path"), str) else None
    captions = work.parent / f"captions-{number}.srt"
    checks = check_output(video, entry.get("duration_ms"), captions, **measure) + checks
    return result(checks, status.get("state"))


def program_qc(work: Path, **measure: Any) -> dict[str, Any]:
    status = _read(work / "status.json", 4 * 1024 * 1024) or {}
    program = _read(work / "program.json") or {}
    parts = []
    for part in program.get("parts") or ():
        part_id = str(part.get("id") or "") if isinstance(part, dict) else ""
        if not reviewing.PART.fullmatch(part_id):
            continue
        parts.append((part_id, _read(work / "mastering" / f"editorial-{part_id}.json"),
                      _read(work / "mastering" / f"plan-{part_id}.json"), _load_review(reviewing.part_path(work, part_id))))
    checks = check_parts(parts) if parts else [_check("timeline", "Dòng thời gian", False, "không đọc được chương trình")]
    video = work.parent / "compilation.mp4"
    checks = check_output(video, status.get("duration_ms"), work.parent / "captions.srt", **measure) + checks
    return result(checks, status.get("state"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from . import measure as measuring
    parser = argparse.ArgumentParser(prog="dubflow.mastering.final_qc")
    parser.add_argument("--work", type=Path, required=True)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--video", type=int)
    which.add_argument("--program", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    args = parser.parse_args(argv)
    tools: dict[str, Any] = {}
    if args.ffprobe:
        tools["probe"] = lambda path: probe_ms(args.ffprobe, path)
    if args.ffmpeg:
        def peak(path: Path) -> float | None:
            try:
                found = measuring.loudness(args.ffmpeg, path)
            except measuring.MeasureError:
                return None
            return found.true_peak if found else None
        tools["peak"] = peak
    if args.video is not None and not 1 <= args.video <= 99:
        parser.error("--video must be 1-99")
    out = product_qc(args.work, args.video, **tools) if args.video is not None else program_qc(args.work, **tools)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


__all__ = ["CLIP_DBTP", "SYNC_TOLERANCE_MS", "caption_end_ms", "check_output", "check_parts", "pending", "product_qc",
           "program_qc", "result", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
