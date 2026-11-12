"""Bán hàng: the seller's clips cut at their own shot boundaries (Phase 1 hookup, ADR-0052).

Without mastering a product video splits its narration into equal parts of at
most 5 s and plays the visuals in turn (``product/render.plan``), so a part
often runs across a cut inside a clip and shows two camera angles in one
"scene". With mastering on, each part lies inside one shot of its clip (kept
``SHOT_MARGIN_MS`` away from the cut, so no frame of the next angle shows),
lasts about the style's target length, and the visuals still take turns in the
seller's order. A shot used again continues where it stopped. Photos stay
stills. The plan is checked against the workflow policy before it is used.

Hook, zoom, captions and colour are allowed by the policy at higher levels but
are planned in Phase 2; this planner only decides the cuts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import re

from .. import align
from .. import analysis as scene
from .. import evidence as ev
from .. import plan as m
from .. import policy as rules
from ..candidates import Candidate

PLANNER = "dubflow.mastering.product"
# 1: shot-aware parts in turn (Phase 1). 2: each sentence illustrated by the footage that matches it (ADR-0054).
VERSION = "8"
RECIPE = "product-editorial-v17"  # v17: a pinned shot without an AI match is the user's, never AI coverage (P2-N14); v16: a product opening is READY only where the vision saw the product (P1-N1); v15: subtitled footage is not a text card, per-sentence signals (Gate S); v14: multi-frame evidence (#724); v13: proper names (#722); v12: semantic root-cause fixes (#705); v11: every piece inside its shot
WORKFLOW = "product_showcase"
SHOT_MARGIN_MS = 120
# A shot shorter than this is skipped while longer ones exist; it is used when nothing else is left.
MIN_PIECE_MS = 1000


@dataclass(frozen=True)
class Visual:
    path: Path
    # None for a photo.
    duration_ms: int | None
    width: int
    height: int
    # The clip's analysis, or None for a photo.
    analysis: scene.Analysis | None = None


@dataclass(frozen=True)
class Piece:
    """A segment of the output: ``duration_ms`` of ``visual`` from ``offset_ms`` (0 for a photo).

    ``focus`` (0–1 across, 0–1 down) is where the renderer's cover crop is centred: the product's box when the
    plan has one, else None (the renderer's usual centre crop).
    """

    visual: int
    offset_ms: int
    duration_ms: int
    focus: tuple[float, float] | None = None


class _Clip:
    """The usable windows of one clip, each inside one shot, and where each was left off."""

    def __init__(self, analysis: scene.Analysis) -> None:
        windows = []
        for shot in analysis.shots:
            start = shot.start_ms + (SHOT_MARGIN_MS if shot.start_ms > 0 else 0)
            end = shot.end_ms - (SHOT_MARGIN_MS if shot.end_ms < analysis.duration_ms else 0)
            if end - start > 0:
                windows.append((start, end))
        long = [w for w in windows if w[1] - w[0] >= MIN_PIECE_MS]
        self.windows = long or windows or [(0, analysis.duration_ms)]
        self.crossed = not long
        self.turn = 0
        self.resume = {index: start for index, (start, _) in enumerate(self.windows)}

    def take(self, wanted: int) -> tuple[int, int, int]:
        """(window index, offset, length) of the next piece, at most ``wanted`` long."""
        index = self.turn % len(self.windows)
        self.turn += 1
        start, end = self.windows[index]
        offset = self.resume[index]
        if end - offset < min(wanted, MIN_PIECE_MS, end - start):
            offset = start  # what was left of this shot is too short: play it from its start again
        length = min(wanted, end - offset)
        self.resume[index] = offset + length if offset + length < end else start
        return index, offset, length


def plan(visuals: Sequence[Visual], total_ms: int, options: m.Options, *, inputs: Mapping[str, str],
         plan_id: str = "product") -> m.Plan:
    """The mastering plan for one product video of ``total_ms``; ``inputs`` are the digests it is made from."""
    policy = rules.resolve(WORKFLOW, options)
    if not policy.enabled:
        raise ValueError("mastering is off: render the legacy plan")
    if not visuals or total_ms <= 0:
        raise ValueError("a product video needs visuals and a positive length")
    pacing = policy.pacing
    clips = {i: _Clip(v.analysis) for i, v in enumerate(visuals) if v.duration_ms is not None and v.analysis is not None}
    warnings = [f"Clip {i + 1} không có cú máy nào dài từ {MIN_PIECE_MS / 1000:g} s; đoạn có thể ngắn"
                for i, clip in clips.items() if clip.crossed]
    segments: list[m.Segment] = []
    at, turn, last = 0, 0, None
    while at < total_ms:
        remaining = total_ms - at
        # The last part takes what is left when that is shorter than a part should be.
        wanted = remaining if remaining < pacing.target + pacing.minimum else pacing.target
        index = turn % len(visuals)
        turn += 1
        visual = visuals[index]
        reasons: list[str] = []
        confidence = 900
        if visual.duration_ms is None:
            length, source = wanted, m.Span(0, 0)
            role = "still"
        else:
            clip = clips[index]
            window, offset, length = clip.take(wanted)
            if (index, window) == last:
                reasons.append("same shot as the previous part (only one usable shot)")
                confidence = 600
            last = (index, window)
            source = m.Span(offset, offset + length)
            role = "demonstration"
            reasons.append("inside one shot of the clip")
            if length < wanted:
                reasons.append("shot ends before the target length")
        if visual.duration_ms is None:
            last = None
        segments.append(m.Segment(f"p{len(segments) + 1}", f"v{index + 1}", source, m.Span(at, at + length), role=role,
                                  audio_mode="narration", confidence=confidence, needs_review=confidence < 700,
                                  reasons=tuple(reasons)))
        at += length
    sources = tuple(m.Source(f"v{i + 1}", 0 if v.duration_ms is None else v.duration_ms, max(1, v.width), max(1, v.height),
                             has_audio=bool(v.analysis and v.analysis.has_audio),
                             rights=m.Rights("unknown", "Ảnh/clip do người dùng cung cấp")) for i, v in enumerate(visuals))
    result = m.Plan(plan_id, WORKFLOW, options, total_ms, sources, tuple(segments),
                    m.Provenance(PLANNER, VERSION, RECIPE, tuple(sorted(inputs.items()))), warnings=tuple(warnings))
    problems = rules.check(result, policy)
    if problems:
        raise ValueError("; ".join(problems))
    return result


def pieces(saved: m.Plan) -> list[Piece]:
    """The plan as the product renderer's parts, in milliseconds."""
    out = []
    for segment in saved.segments:
        index = int(segment.source_id[1:]) - 1
        start = m.to_milliseconds(segment.source.start, saved.time_base, "floor")
        begin = m.to_milliseconds(segment.timeline.start, saved.time_base, "floor")
        end = m.to_milliseconds(segment.timeline.end, saved.time_base, "floor")
        focus = None
        if segment.framing is not None and segment.framing.protect:
            focus = (segment.framing.center_x / m.BASIS, segment.framing.center_y / m.BASIS)
        out.append(Piece(index, start, end - begin, focus))
    return out


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def unchecked_numbers(text: str, facts: str) -> list[str]:
    """Numbers a sentence states that the product's own information (name, price, highlights, notes) does not."""
    known = {n.replace(",", ".").rstrip(".") for n in _NUMBER.findall(facts)}
    known |= {n.replace(".", "") for n in known}
    found = []
    for number in _NUMBER.findall(text):
        value = number.replace(",", ".").rstrip(".")
        if value not in known and value.replace(".", "") not in known and number not in found:
            found.append(number)
    return found


def plan_editorial(visuals: Sequence[Visual], spoken: Sequence[tuple[str, int]], total_ms: int, options: m.Options, *,
                   lead_ms: int, gap_ms: int, candidates: Mapping[int, Sequence[Candidate]],
                   evidence: Mapping[str, ev.Evidence], groups: Mapping[str, str] | None = None,
                   edits: align.Edits | None = None, facts: str = "", inputs: Mapping[str, str],
                   notes: Sequence[str] = (), plan_id: str = "product") -> tuple[m.Plan, dict]:
    """The plan for one product video whose narration is ``spoken`` (sentence, spoken ms), and its decision log.

    Each sentence is a unit; its footage is chosen by content (``align.assign``) from the candidates of every
    visual (a photo is one candidate it can hold). ``facts`` is what the product's information says, for the check
    of numbers in the script.
    """
    policy = rules.resolve(WORKFLOW, options)
    if not policy.enabled:
        raise ValueError("mastering is off: render the legacy plan")
    if not visuals or total_ms <= 0 or not spoken:
        raise ValueError("a product video needs visuals, a narration and a positive length")
    found_units = align.units(spoken, total_ms, lead_ms=lead_ms, gap_ms=gap_ms, function=align.product_function)
    pool: list[Candidate] = []
    stills = []
    for index, visual in enumerate(visuals):
        mine = list(candidates.get(index, ()))
        if visual.duration_ms is None:
            stills += [c.candidate_id for c in mine]
        pool += mine
    choices, decisions, mode = align.assign(found_units, pool, evidence, policy.pacing,
                                            forward_only=not policy.allows(rules.REORDER), edits=edits, groups=groups, stills=stills,
                                            product=True)
    warnings: list[str] = []
    if mode == "signals_only":
        warnings.append("Chưa có bằng chứng nội dung (ghi chú clip hoặc AI nhìn hình; tên file không tính): cảnh được chọn theo "
                        "điểm cắt, độ nét và đa dạng, cần kiểm tra.")
    flagged: dict[str, list[str]] = {}
    for unit in found_units:
        numbers = unchecked_numbers(unit.text, facts)
        if numbers:
            flagged[unit.unit_id] = numbers
            warnings.append(f"Câu {unit.unit_id[1:]} nêu {', '.join(numbers)} nhưng thông tin sản phẩm không có: hãy kiểm tra trước khi đăng.")
    # Different products: the clips' main products differ (each clip by the name the AI gave most often, spelled
    # loosely so "espresso-cup" and "espresso_cup" are one name). Several names inside one clip are one shoot.
    seen_in: dict[str, dict[str, int]] = {}
    source_of = {c.candidate_id: c.source_id for c in pool}
    for candidate_id, item in evidence.items():
        if item.same_product and item.product_visible and candidate_id in source_of:
            name = ev.fold(item.same_product)
            if name:
                names = seen_in.setdefault(source_of[candidate_id], {})
                names[name] = names.get(name, 0) + 1
    main = {source: max(names.items(), key=lambda kv: (kv[1], kv[0]))[0] for source, names in seen_in.items()}
    # names the AI makes up vary ("may pha espresso", "tay cam pha cafe" for one machine): only names with no word
    # in common count as different products
    distinct = [name for name in sorted(set(main.values()))
                if all(not set(name.split()) & set(other.split()) for other in set(main.values()) if other != name)]
    if len(distinct) >= 2:
        warnings.append("Các clip có dấu hiệu quay những sản phẩm hoặc biến thể khác nhau: "
                        + ", ".join(distinct) + ". Hãy kiểm tra trước khi ghép chung.")
    unusable = [c.candidate_id for c in pool if not c.quality.usable]
    if unusable:
        warnings.append(f"{len(unusable)} cảnh bị bỏ vì tối, mờ hoặc trống.")
    by_unit = {unit.unit_id: unit for unit in found_units}
    segments: list[m.Segment] = []
    at = 0
    first_hook = True
    for choice in choices:
        unit = by_unit[choice.unit_id]
        candidate = choice.candidate
        index = int(candidate.source_id[1:]) - 1
        still = visuals[index].duration_ms is None
        source = m.Span(0, 0) if still else m.Span(choice.offset_ms, choice.offset_ms + choice.length_ms)
        reasons = [f"minh họa câu {unit.unit_id[1:]} ({unit.function})", *choice.reasons]
        if still:
            role = "still"
        elif unit.function == "hook" and first_hook and policy.hook != "off" and choice.length_ms <= policy.hook_max_ms:
            role = "hook"
            reasons.append("cảnh mở đầu chọn theo điểm hook (kết quả/thao tác rõ, độ nét)")
        elif unit.function in ("demonstration", "feature"):
            role = "demonstration"
        else:
            role = "primary"
        if unit.function == "hook":
            first_hook = False
        review = choice.needs_review or (role == "hook" and policy.hook == "suggest") or unit.unit_id in flagged
        if unit.unit_id in flagged:
            reasons.append("câu có thông số chưa có trong thông tin sản phẩm")
        info = evidence.get(candidate.candidate_id)
        framing = None
        if info is not None and info.product_box is not None and not still:
            box = info.product_box
            framing = m.Framing(1000, 1000, box.x + box.w // 2, box.y + box.h // 2, (box,))
            reasons.append("khung cắt giữ sản phẩm trong hình")
        segments.append(m.Segment(f"p{len(segments) + 1}", candidate.source_id, source, m.Span(at, at + choice.length_ms),
                                  role=role, audio_mode="narration", speech_refs=(unit.unit_id,), framing=framing,
                                  confidence=choice.confidence, needs_review=review, reasons=tuple(r[:200] for r in reasons[:16])))
        at += choice.length_ms
    sources = tuple(m.Source(f"v{i + 1}", 0 if v.duration_ms is None else v.duration_ms, max(1, v.width), max(1, v.height),
                             has_audio=bool(v.analysis and v.analysis.has_audio),
                             rights=m.Rights("unknown", "Ảnh/clip do người dùng cung cấp")) for i, v in enumerate(visuals))
    result = m.Plan(plan_id, WORKFLOW, options, total_ms, sources, tuple(segments),
                    m.Provenance(PLANNER, VERSION, RECIPE, tuple(sorted(inputs.items()))), warnings=tuple(w[:500] for w in warnings))
    problems = rules.check(result, policy)
    if problems:
        raise ValueError("; ".join(problems))
    log = {
        "schema_version": 1, "kind": "editorial_decisions", "workflow": WORKFLOW, "plan_id": plan_id, "mode": mode,
        "units": [decision.to_dict() for decision in decisions],
        "candidates": [{**c.to_dict(), "group": (groups or {}).get(c.candidate_id, c.candidate_id),
                        "evidence": evidence[c.candidate_id].to_dict() if c.candidate_id in evidence else None,
                        "summary": evidence[c.candidate_id].summary() if c.candidate_id in evidence else ""} for c in pool],
        "edits": (edits or align.Edits()).to_dict(), "warnings": warnings,
        # each source's content identity: what an approval was given on (P2-N5 materiality)
        "sources": {f"v{i + 1}": inputs[f"v{i + 1}"] for i in range(len(visuals)) if f"v{i + 1}" in inputs},
        # what the evidence came from (P2-14): file names alone are never content
        "provenance": ev.provenance_counts({c.candidate_id: evidence[c.candidate_id] for c in pool if c.candidate_id in evidence}),
    }
    return result, log


def gather_evidence(visuals: Sequence[Path], candidates: Mapping[int, Sequence[Candidate]], notes: Sequence[str | None] = ()) \
        -> dict[str, ev.Evidence]:
    """Evidence from the user's notes on each visual and from its file name (vision is added by the caller)."""
    found = ev.gather(c.candidate_id for mine in candidates.values() for c in mine)
    for index, path in enumerate(visuals):
        note = notes[index] if index < len(notes) else None
        name = ev.file_words(path.name)
        for candidate in candidates.get(index, ()):
            item = found[candidate.candidate_id]
            if note and note.strip():
                item.add(ev.Item("user", 1000, note.strip()[:300], tuple(ev.cue_labels(note)), "ghi chú của bạn"))
            if ev.words(name):
                item.add(ev.Item("metadata", 1000, name[:200], tuple(ev.cue_labels(name)), "tên file"))
    return found


def inputs(visuals: Sequence[Path], total_ms: int, options: m.Options, *, spoken: Sequence[tuple[str, int]] = (),
           template: str = "", editorial: Mapping[str, object] | None = None) -> dict[str, str]:
    """What a saved plan must have been made from to be reused.

    Each file by content (``m.file_identity``) and analysis recipe; the narration (each sentence and how long it
    is spoken, so an edited script or another voice or speed plans again); the length, template and options; the
    editorial inputs (notes, edits, the vision provider and model, the product facts).
    """
    found = {f"v{i + 1}": m.file_identity(path, scene.RECIPE) for i, path in enumerate(visuals)}
    found["timeline"] = m.digest_json({"total_ms": total_ms, "visuals": len(visuals), "level": options.level, "style": options.style,
                                       "template": template, "recipe": RECIPE})
    found["script"] = m.digest_json([[text, duration] for text, duration in spoken])
    found["editorial"] = m.digest_json(dict(editorial or {}))
    return found
