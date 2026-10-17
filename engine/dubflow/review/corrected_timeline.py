"""Derive a private replacement timeline from native-admitted original bytes.

This is a pure B1/B2 metadata boundary. It performs no inference, file/SQLite
writes, publication or media admission. The native caller must bind the exact
original timeline hash before calling it and retain that original for undo.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from .production import RegenerationPlan, ReviewError, ReviewSnapshot, _correction_stages, _tick
from .store import _validate_cue_consistency
from .synthesis_input import _encode
from .wire import decode_review_json

MAX_BYTES = 64 * 1024 * 1024
MAX_CUES = 10000


def admit_correction(before: ReviewSnapshot, after: ReviewSnapshot):
    """Admit only one cue's content edit, including every duplicate finding."""
    if not isinstance(before, ReviewSnapshot) or not isinstance(after, ReviewSnapshot):
        raise ReviewError("timeline correction requires versioned native snapshots")
    _validate_cue_consistency(before)
    _validate_cue_consistency(after)
    if (before.job_id != after.job_id or before.time_base != after.time_base or
            after.revision != before.revision + 1 or after.last_plan is None or
            len(before.findings) != len(after.findings)):
        raise ReviewError("timeline correction requires the exact next revision")
    identifier = after.last_plan.finding_id
    old = next((item for item in before.findings if item.finding_id == identifier), None)
    new = next((item for item in after.findings if item.finding_id == identifier), None)
    if old is None or new is None or old.cue_id is None:
        raise ReviewError("timeline correction requires a mapped canonical cue")
    # B1 has no assigned voice; adding one is a mode change, not a subtitle edit.
    if (old.voice is None) != (new.voice is None):
        raise ReviewError("changing dubbing mode requires explicit compatibility")
    stages = _correction_stages(translation_changed=old.translated_text != new.translated_text,
                                voice_changed=old.voice != new.voice,
                                has_voice=old.voice is not None)
    plan = RegenerationPlan(identifier, stages, ("asr", "ocr"),
                            tuple("replacement:" + stage for stage in stages))
    if after.last_plan != plan:
        raise ReviewError("timeline correction has the wrong downstream plan")
    for prior, updated in zip(before.findings, after.findings):
        expected = replace(prior, translated_text=new.translated_text, voice=new.voice) if prior.cue_id == old.cue_id else prior
        if updated != expected:
            raise ReviewError("timeline correction changed source mapping, diagnostics or another cue")
    return old, new


def _admit_timeline(original: bytes, before: ReviewSnapshot):
    if not isinstance(original, bytes) or not 1 <= len(original) <= MAX_BYTES:
        raise ReviewError("original timeline exceeds its byte bound")
    try:
        timeline = decode_review_json(original.decode("utf-8"))
    except UnicodeError as error:
        raise ReviewError("original timeline requires UTF-8 JSON") from error
    fields = {"schema_version", "time_base", "duration_ticks", "cues", "source_clock_mapping"}
    if (not isinstance(timeline, dict) or set(timeline) != fields or
            type(timeline["schema_version"]) is not int or timeline["schema_version"] != 1):
        raise ReviewError("unknown canonical timeline requires explicit compatibility")
    base = timeline["time_base"]
    if not isinstance(base, dict) or set(base) != {"numerator", "denominator"}:
        raise ReviewError("canonical timeline time base is missing")
    numerator, denominator = (int(_tick(base[name], name)) for name in ("numerator", "denominator"))
    if not numerator or not denominator:
        raise ReviewError("canonical timeline time base must be positive")
    duration = Fraction(int(_tick(timeline["duration_ticks"], "duration_ticks")) * numerator, denominator)
    cues = timeline["cues"]
    if not isinstance(cues, list) or not 1 <= len(cues) <= MAX_CUES:
        raise ReviewError("canonical timeline cue count is outside its bound")
    seen = set()
    by_id = {}
    cue_fields = {"cue_id", "start_ms", "end_ms", "source_text", "translated_text", "confidence"}
    # Reuse the production typed cue validator without invoking its pipeline.
    from engine.dubflow.worker.production_job import TextCue
    for cue in cues:
        if not isinstance(cue, dict) or set(cue) != cue_fields or not isinstance(cue["cue_id"], str):
            raise ReviewError("canonical timeline cue requires the existing v1 fields")
        try:
            item = TextCue(**cue)
        except (TypeError, ValueError) as error:
            raise ReviewError("invalid canonical timeline cue") from error
        if item.cue_id in seen or Fraction(item.end_ms, 1000) > duration:
            raise ReviewError("duplicate canonical cue or interval outside source duration")
        seen.add(item.cue_id)
        by_id[item.cue_id] = cue
    review_base = Fraction(int(before.time_base[0]), int(before.time_base[1]))
    for finding in before.findings:
        if finding.cue_id is None:
            continue
        cue = by_id.get(finding.cue_id)
        if (cue is None or cue["source_text"] != finding.source_text or
                cue["translated_text"] != finding.translated_text or
                Fraction(cue["start_ms"], 1000) != int(finding.start_ticks) * review_base or
                Fraction(cue["end_ms"], 1000) != int(finding.end_ticks) * review_base):
            raise ReviewError("Review finding differs from the original canonical timeline")
    return timeline, by_id


def derive_corrected_timeline(original: bytes, before: ReviewSnapshot, after: ReviewSnapshot) -> bytes:
    """Change one translation, preserving exact current bytes on voice edits.

    The caller separately admits timeline/snapshot provenance. Rational source
    ranges are compared without rounding; this helper grants no graph authority.
    """
    old, new = admit_correction(before, after)
    timeline, by_id = _admit_timeline(original, before)
    if old.translated_text == new.translated_text:
        return original
    # Never upgrade ASR confidence because a human changed a translation.
    by_id[old.cue_id]["translated_text"] = new.translated_text
    return _encode(timeline, MAX_BYTES)


def derive_effective_timeline(original: bytes, origin: ReviewSnapshot, current: bytes,
                              before: ReviewSnapshot, after: ReviewSnapshot) -> bytes:
    """Admit cumulative content against its original source, then edit one cue.

    Origin and current public revisions remain distinct. Every original cue,
    source interval/clock/confidence and unrepresented translation stays exact;
    earlier edits must equal the separately admitted current findings.
    """
    if (not isinstance(origin, ReviewSnapshot) or origin.revision != 0 or
        origin.job_id != before.job_id or origin.time_base != before.time_base):
        raise ReviewError("effective timeline requires its separate original archive")
    archived, _ = _admit_timeline(original, origin)
    actual, _ = _admit_timeline(current, before)
    expected = decode_review_json(_encode(archived, MAX_BYTES).decode())
    findings = {item.cue_id:item for item in before.findings if item.cue_id is not None}
    for cue in expected["cues"]:
        if cue["cue_id"] in findings:
            cue["translated_text"] = findings[cue["cue_id"]].translated_text
    if _encode(expected, MAX_BYTES) != _encode(actual, MAX_BYTES):
        raise ReviewError("effective timeline changed source/clock/confidence or lost prior edits")
    return derive_corrected_timeline(current, before, after)
