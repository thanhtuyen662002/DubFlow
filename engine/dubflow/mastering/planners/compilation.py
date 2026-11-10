"""Tổng hợp from raw footage: one plan per programme part (ADR-0053 §5).

A programme is spoken part by part (hook, intro, each item, outro). Each part's
sentences are units on its own timeline; the shared editorial layer gives every
sentence the footage of the part's sources that matches it (transcript, notes,
file name, vision), each source playing forward (the policy has no reorder).
An item may then end on a *source moment*: a few seconds of its own sound
(speech in the transcript or audio that is not silent), when the user lets the
source sound play. Under the narration, the source sound plays quietly (mixed)
or not at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .. import align
from .. import analysis as scene
from .. import evidence as ev
from .. import plan as m
from .. import policy as rules
from ..candidates import Candidate

PLANNER = "dubflow.mastering.compilation"
VERSION = "1"
RECIPE = "compilation-program-v1"
WORKFLOW = "compilation_entertainment"
MOMENT_MIN_MS, MOMENT_TARGET_MS, MOMENT_MAX_MS = 2000, 3500, 6000
PART_FUNCTION = {"hook": "hook", "intro": "intro", "item": "narration", "outro": "outro"}


@dataclass(frozen=True)
class Footage:
    """One raw source of the programme, analysed."""

    source_id: str
    path: Path
    analysis: scene.Analysis
    width: int
    height: int
    fps: tuple[int, int] = (30, 1)
    cues: tuple[tuple[int, int, str], ...] = ()
    notes: str = ""
    trim: tuple[int, int] | None = None
    rights: str = "unknown"
    credit: str = ""

    def audible(self, start_ms: int, end_ms: int) -> int:
        """Milli of ``[start, end)`` that is not silent (0 without audio)."""
        if not self.analysis.has_audio or end_ms <= start_ms:
            return 0
        quiet = sum(max(0, min(end_ms, b) - max(start_ms, a)) for a, b in self.analysis.silences_ms)
        return 1000 - quiet * 1000 // (end_ms - start_ms)


@dataclass
class PartPlan:
    plan: m.Plan
    log: dict
    narration_ms: int
    moment_ms: int = 0
    audio: dict[str, str] = field(default_factory=dict)  # segment id → audio mode (also in the plan)


def evidence_for(footage: Sequence[Footage], candidates: Mapping[str, Sequence[Candidate]], item_notes: Mapping[str, str]) \
        -> dict[str, ev.Evidence]:
    """Transcript inside each candidate, the source's and the item's notes, the file name."""
    found = ev.gather(c.candidate_id for mine in candidates.values() for c in mine)
    for source in footage:
        name = ev.file_words(source.path.name)
        for candidate in candidates.get(source.source_id, ()):
            item = found[candidate.candidate_id]
            said = ev.transcript_text(source.cues, candidate.start_ms, candidate.end_ms)
            if said:
                item.add(ev.Item("transcript", 850, said[:400], (), "phụ đề nguồn"))
            if source.notes:
                item.add(ev.Item("user", 800, source.notes[:300], (), "ghi chú nguồn"))
            if item_notes.get(source.source_id):
                item.add(ev.Item("user", 600, item_notes[source.source_id][:300], (), "ghi chú mục"))
            if ev.words(name):
                item.add(ev.Item("metadata", 700, name[:200], (), "tên file"))
    return found


def inside_trim(candidates: Sequence[Candidate], trim: tuple[int, int] | None) -> list[Candidate]:
    """Candidates clipped to the user's trim of the source (none outside it)."""
    if trim is None:
        return list(candidates)
    start, end = trim
    out = []
    for c in candidates:
        a, b = max(c.start_ms, start), min(c.end_ms, end)
        if b - a >= 400:
            out.append(Candidate(c.candidate_id, c.source_id, a, b, c.shot_index, c.motion, c.quality, c.dhash, c.keyframe))
    return out


def _moment(footage: Mapping[str, Footage], candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence],
            title: str, used_end: Mapping[str, int], keep: str) -> tuple[Candidate, int, int, list[str]] | None:
    """The source moment that best ends an item: its own sound, after what the narration showed."""
    if keep == "off":
        return None
    wanted = ev.terms(title)
    best = None
    for candidate in candidates:
        source = footage[candidate.source_id]
        start = max(candidate.start_ms, used_end.get(candidate.source_id, 0))
        length = min(candidate.end_ms - start, MOMENT_MAX_MS)
        if length < MOMENT_MIN_MS or not candidate.quality.usable:
            continue
        end = start + min(length, MOMENT_TARGET_MS if length < MOMENT_TARGET_MS + MOMENT_MIN_MS else length)
        audible = source.audible(start, end)
        speech = ev.transcript_text(source.cues, start, end)
        if not source.analysis.has_audio or (audible < (300 if keep == "on" else 500) and not speech):
            continue
        info = evidence.get(candidate.candidate_id)
        relevance = len(wanted & set(info.terms())) if info else 0
        score = audible + (400 if speech else 0) + 150 * relevance + candidate.quality.score // 4 + candidate.motion // 10
        reasons = ["giữ âm thanh thật của nguồn"]
        if speech:
            reasons.append(f"có lời nói trong nguồn: “{speech[:60]}”")
        if audible >= 500:
            reasons.append(f"âm thanh rõ {audible // 10} % thời lượng")
        if best is None or score > best[0]:
            best = (score, candidate, start, end, reasons)
    if best is None:
        return None
    _, candidate, start, end, reasons = best
    return candidate, start, end, reasons


def plan_part(part_id: str, kind: str, spoken: Sequence[tuple[str, int]], narration_ms: int, options: m.Options, *,
              lead_ms: int, gap_ms: int, footage: Sequence[Footage], candidates: Mapping[str, Sequence[Candidate]],
              evidence: Mapping[str, ev.Evidence], groups: Mapping[str, str] | None = None, edits: align.Edits | None = None,
              keep_audio: str = "auto", title: str = "", inputs: Mapping[str, str], moment: bool = True) -> PartPlan:
    """The plan of one part: the narration's sentences over matching footage, then (items) a source moment."""
    policy = rules.resolve(WORKFLOW, options)
    if not policy.enabled:
        raise ValueError("the raw compilation path is always planned")
    if not spoken or narration_ms <= 0:
        raise ValueError(f"{part_id}: a part needs narration")
    function = PART_FUNCTION[kind]
    found_units = align.units(spoken, narration_ms, lead_ms=lead_ms, gap_ms=gap_ms, prefix=f"{part_id}.u",
                              function=lambda index, count, text: "hook" if kind == "hook" and index == 0 else function)
    by_source = {f.source_id: f for f in footage}
    pool = [c for f in footage for c in inside_trim(candidates.get(f.source_id, ()), f.trim)]
    if not pool:
        raise ValueError(f"{part_id}: no usable footage in its sources")
    choices, decisions, mode = align.assign(found_units, pool, evidence, policy.pacing, forward_only=not policy.allows(rules.REORDER),
                                            edits=edits, groups=groups)
    segments: list[m.Segment] = []
    audio: dict[str, str] = {}
    at = 0
    used_end: dict[str, int] = {}
    for index, choice in enumerate(choices):
        candidate = choice.candidate
        source = by_source[candidate.source_id]
        unit_text = next(u.text for u in found_units if u.unit_id == choice.unit_id)
        mode_audio = "mixed" if keep_audio != "off" and source.analysis.has_audio else "narration"
        role = "broll"
        reasons = [f"minh họa câu “{unit_text[:60]}”", *choice.reasons]
        review = choice.needs_review
        if kind == "hook" and index == 0 and policy.hook != "off" and choice.length_ms <= policy.hook_max_ms:
            role = "hook"
            review = True
            reasons.append("cảnh mở màn lấy từ phần sau của chương trình")
        span = m.Span(choice.offset_ms, choice.offset_ms + choice.length_ms)
        if role != "hook" and choice.offset_ms < used_end.get(candidate.source_id, 0):
            # The source has nothing left ahead of what was shown: hold one of its frames instead of playing earlier
            # footage again (the source keeps its order).
            span = m.Span(candidate.middle_ms, candidate.middle_ms)
            role, mode_audio, review = "still", "narration", True
            reasons.append("giữ một khung hình vì nguồn ngắn hơn lời bình")
        segment_id = f"{part_id}.s{len(segments) + 1}"
        segments.append(m.Segment(segment_id, candidate.source_id, span,
                                  m.Span(at, at + choice.length_ms), role=role, audio_mode=mode_audio,
                                  speech_refs=(choice.unit_id,), confidence=choice.confidence, needs_review=review,
                                  reasons=tuple(r[:200] for r in reasons[:16])))
        audio[segment_id] = mode_audio
        at += choice.length_ms
        if role not in ("still", "hook"):
            used_end[candidate.source_id] = max(used_end.get(candidate.source_id, 0), span.end)
    moment_ms = 0
    warnings: list[str] = []
    if mode == "signals_only":
        warnings.append(f"{part_id}: chưa có bằng chứng nội dung (phụ đề nguồn, ghi chú, AI nhìn hình); cảnh chọn theo điểm cắt và chất lượng.")
    if moment and kind == "item" and policy.keep_source_audio:
        picked = _moment(by_source, pool, evidence, title, used_end, keep_audio)
        if picked is not None:
            candidate, start, end, reasons = picked
            segment_id = f"{part_id}.s{len(segments) + 1}"
            segments.append(m.Segment(segment_id, candidate.source_id, m.Span(start, end), m.Span(at, at + end - start),
                                      role="reaction", audio_mode="source", confidence=700, reasons=tuple(reasons)))
            audio[segment_id] = "source"
            moment_ms = end - start
            at += moment_ms
    sources = tuple(m.Source(f.source_id, f.analysis.duration_ms, max(1, f.width), max(1, f.height), f.fps[0], f.fps[1],
                             has_audio=f.analysis.has_audio, rights=m.Rights(f.rights if f.rights in m.RIGHTS else "unknown",
                                                                               "Người dùng xác nhận quyền sử dụng" if f.rights != "unknown" else "Chưa xác nhận"),
                             credit=f.credit or None) for f in footage)
    result = m.Plan(part_id, WORKFLOW, options, at, sources, tuple(segments),
                    m.Provenance(PLANNER, VERSION, RECIPE, tuple(sorted(inputs.items()))), warnings=tuple(w[:500] for w in warnings))
    problems = rules.check(result, policy)
    if problems:
        raise ValueError("; ".join(problems))
    log = {"schema_version": 1, "kind": "editorial_decisions", "workflow": WORKFLOW, "plan_id": part_id, "part": kind, "mode": mode,
           "units": [d.to_dict() for d in decisions],
           "candidates": [{**c.to_dict(), "group": (groups or {}).get(c.candidate_id, c.candidate_id),
                           "evidence": evidence[c.candidate_id].to_dict() if c.candidate_id in evidence else None,
                           "summary": evidence[c.candidate_id].summary() if c.candidate_id in evidence else ""} for c in pool],
           "edits": (edits or align.Edits()).to_dict(), "warnings": warnings}
    return PartPlan(result, log, narration_ms, moment_ms, audio)


__all__ = ["Footage", "PartPlan", "evidence_for", "inside_trim", "plan_part"]
