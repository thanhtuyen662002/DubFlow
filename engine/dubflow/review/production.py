"""Dependency-free Review Center artifacts for B1/B2 production jobs.

The worker emits a snapshot after QC.  Corrections are versioned and planned
against exact downstream stages; old valid artifacts remain untouched until a
caller validates replacement outputs and commits the new snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from math import gcd
import re
from typing import Any, Iterable, Mapping, Sequence


REASONS = {
    "ASR_OCR_CONFLICT",
    "SPEAKER_UNCERTAINTY",
    "TRANSLATION_ANOMALY",
    "TTS_FAILURE",
    "INPAINT_RISK",
    "SYNC_QC_WARNING",
}
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_TICKS = re.compile(r"^(0|[1-9][0-9]*)$")
_DECIMAL = re.compile(r"^(0|0\.[0-9]+|1(?:\.0+)?)$")
_I64_MAX = (1 << 63) - 1

# Correction roots are determined by the changed artifact, independently of
# the diagnostic that brought the cue to the user's attention. QC/export are
# descendants and cannot retain a prior PASS after content changes.
_CORRECTION_EDGES = {
    "translation": ("subtitles", "tts"),
    "subtitles": ("render",),
    "tts": ("mix",),
    "mix": ("render",),
    "render": ("qc",),
    "qc": ("export",),
}
_CORRECTION_ORDER = ("translation", "subtitles", "tts", "mix", "render", "qc", "export")


def _correction_stages(*, translation_changed: bool, voice_changed: bool, has_voice: bool = True) -> tuple[str, ...]:
    if type(has_voice) is not bool:
        raise ReviewError("correction mode requires admitted voice provenance")
    if not has_voice and voice_changed:
        raise ReviewError("subtitle-only correction cannot change dubbing mode")
    roots = ({"translation"} if translation_changed else set()) | ({"tts"} if voice_changed else set())
    if not roots:
        raise ReviewError("a correction must change translation or voice")
    affected = set(roots)
    pending = list(roots)
    while pending:
        for descendant in _CORRECTION_EDGES.get(pending.pop(), ()):
            if descendant not in affected:
                affected.add(descendant)
                pending.append(descendant)
    return tuple(stage for stage in _CORRECTION_ORDER if stage in affected and
                 (has_voice or stage not in ("tts", "mix")))


class ReviewError(ValueError):
    pass


def _text(value: object, name: str, limit: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ReviewError(f"{name} must be non-empty bounded text")
    return value.strip()


def _tick(value: object, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0 or value > _I64_MAX:
            raise ReviewError(f"{name} is outside the signed 64-bit tick range")
        return str(value)
    if isinstance(value, str) and len(value) <= 19 and _TICKS.fullmatch(value) and int(value) <= _I64_MAX:
        return value
    raise ReviewError(f"{name} must be a canonical non-negative integer tick")


def _confidence(value: object) -> str:
    if isinstance(value, bool):
        raise ReviewError("confidence must be a finite decimal")
    if isinstance(value, str):
        if len(value) > 128 or not _DECIMAL.fullmatch(value):
            raise ReviewError("confidence must be in [0, 1]")
        parsed = Decimal(value)
    elif isinstance(value, (int, float)):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as error:
            raise ReviewError("confidence is invalid") from error
    else:
        raise ReviewError("confidence is invalid")
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise ReviewError("confidence must be in [0, 1]")
    if parsed == 0:
        return "0"
    if parsed == 1:
        return "1"
    return format(parsed.normalize(), "f")


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    reason: str
    start_ticks: str | None
    end_ticks: str | None
    source_text: str
    translated_text: str
    speaker: str | None
    voice: str | None
    output_preview: str | None
    confidence: str | None
    affected_stages: tuple[str, ...]
    cue_id: str | None = None
    warning: str | None = None
    confidence_source: str | None = None

    def __post_init__(self) -> None:
        if self.finding_id != _text(self.finding_id, "finding_id", 256) or not _ID.fullmatch(self.finding_id):
            raise ReviewError("finding_id has an invalid format")
        if not isinstance(self.reason, str) or self.reason not in REASONS:
            raise ReviewError("review reason is invalid")
        if self.start_ticks is None and self.end_ticks is None:
            start = end = None
            if self.cue_id is not None:
                raise ReviewError("a bound cue requires an exact range")
        else:
            start, end = _tick(self.start_ticks, "start_ticks"), _tick(self.end_ticks, "end_ticks")
            if int(end) <= int(start):
                raise ReviewError("review range must be positive")
        object.__setattr__(self, "start_ticks", start)
        object.__setattr__(self, "end_ticks", end)
        object.__setattr__(self, "source_text", _text(self.source_text, "source_text"))
        object.__setattr__(self, "translated_text", _text(self.translated_text, "translated_text"))
        original_cue_id = self.cue_id
        for name in ("speaker", "voice", "output_preview", "cue_id", "warning", "confidence_source"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name, 256 if name in {"speaker", "voice", "cue_id", "confidence_source"} else 4096))
        if self.cue_id is not None and (original_cue_id != self.cue_id or not _ID.fullmatch(self.cue_id)):
            raise ReviewError("cue_id has an invalid format")
        confidence = _confidence(self.confidence) if self.confidence is not None else None
        if confidence is None and self.confidence_source is not None:
            raise ReviewError("unknown confidence cannot claim a measured source")
        object.__setattr__(self, "confidence", confidence)
        stages = tuple(_text(item, "affected_stage", 256) for item in self.affected_stages)
        if not stages or len(stages) > 64 or len(set(stages)) != len(stages):
            raise ReviewError("affected stages must be unique and non-empty")
        object.__setattr__(self, "affected_stages", stages)

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "reason": self.reason,
            "range": {"start_ticks": self.start_ticks, "end_ticks": self.end_ticks} if self.start_ticks is not None else None,
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "speaker": self.speaker,
            "voice": self.voice,
            "output_preview": self.output_preview,
            "confidence": self.confidence,
            "affected_stages": list(self.affected_stages),
            "cue_id": self.cue_id,
            "warning": self.warning,
            "confidence_source": self.confidence_source,
        }


@dataclass(frozen=True)
class RegenerationPlan:
    finding_id: str
    stages_to_rerun: tuple[str, ...]
    preserved_artifacts: tuple[str, ...]
    replacement_artifacts: tuple[str, ...]
    retain_previous_until_validated: bool = True
    requires_full_manual_review: bool = False

    def __post_init__(self) -> None:
        if self.finding_id != _text(self.finding_id, "finding_id", 256) or not _ID.fullmatch(self.finding_id):
            raise ReviewError("plan finding_id is invalid")
        if self.retain_previous_until_validated is not True or self.requires_full_manual_review is not False:
            raise ReviewError("plan must retain prior valid artifacts until validation")
        for name in ("stages_to_rerun", "preserved_artifacts", "replacement_artifacts"):
            items = tuple(_text(item, name, 256) for item in getattr(self, name))
            if not items or len(items) > 64 or len(set(items)) != len(items):
                raise ReviewError(f"{name} must be unique and bounded")
            object.__setattr__(self, name, items)

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "stages_to_rerun": list(self.stages_to_rerun),
            "preserved_artifacts": list(self.preserved_artifacts),
            "replacement_artifacts": list(self.replacement_artifacts),
            "retain_previous_until_validated": self.retain_previous_until_validated,
            "requires_full_manual_review": self.requires_full_manual_review,
        }


@dataclass(frozen=True)
class ReviewSnapshot:
    job_id: str
    revision: int
    findings: tuple[ReviewFinding, ...]
    last_plan: RegenerationPlan | None = None
    time_base: tuple[str, str] = ("1", "1000")

    def __post_init__(self) -> None:
        if self.job_id != _text(self.job_id, "job_id", 256) or not _ID.fullmatch(self.job_id):
            raise ReviewError("job_id has an invalid format")
        if type(self.revision) is not int or not 0 <= self.revision <= _I64_MAX:
            raise ReviewError("revision is invalid")
        if len(self.findings) > 100000:
            raise ReviewError("too many review findings")
        if len(self.time_base) != 2:
            raise ReviewError("time_base is invalid")
        numerator, denominator = (_tick(item, "time_base") for item in self.time_base)
        if int(numerator) == 0 or int(denominator) == 0 or gcd(int(numerator), int(denominator)) != 1:
            raise ReviewError("time_base must be a reduced positive rational")
        object.__setattr__(self, "time_base", (numerator, denominator))
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ReviewError("duplicate review finding id")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "job_id": self.job_id,
            "revision": self.revision,
            "time_base": {"numerator": self.time_base[0], "denominator": self.time_base[1]},
            "findings": [finding.to_dict() for finding in self.findings],
            "last_plan": self.last_plan.to_dict() if self.last_plan else None,
        }


def _warning_finding(job_id: str, warning: str, *, cue: Mapping[str, Any] | None = None,
                     time_base: tuple[str, str] = ("1", "1000")) -> ReviewFinding:
    code = warning.split(":", 1)[0].strip().upper() or "SYNC_QC_WARNING"
    if "TTS" in code or "AUDIO" in code:
        reason = "TTS_FAILURE"
        stages = ("tts", "mix", "render")
    elif "OCR" in code or "TEXT" in code:
        reason = "ASR_OCR_CONFLICT"
        stages = ("ocr", "text_cleanup", "render")
    elif "SPEAKER" in code or "DIAR" in code:
        reason = "SPEAKER_UNCERTAINTY"
        stages = ("diarization", "tts", "mix", "render")
    elif "INPAINT" in code or "VIS" in code:
        reason = "INPAINT_RISK"
        stages = ("ocr", "visual_cleanup", "render")
    elif "TRANSL" in code:
        reason = "TRANSLATION_ANOMALY"
        stages = ("translation", "subtitles", "tts", "mix", "render")
    else:
        reason = "SYNC_QC_WARNING"
        stages = ("render", "qc")
    start = end = None
    cue_id = None
    confidence = confidence_source = None
    if cue is not None:
        cue_id = _text(cue.get("cue_id"), "cue_id", 256)
        # Production B1/B2 cues are source-mapped canonical milliseconds. Exact
        # rescaling is required when a caller declares another canonical base.
        converted = []
        for name in ("start_ms", "end_ms"):
            milliseconds = int(_tick(cue.get(name), name))
            ticks, remainder = divmod(milliseconds * int(time_base[1]), 1000 * int(time_base[0]))
            if remainder:
                raise ReviewError("cue range cannot be represented exactly in time_base")
            converted.append(_tick(ticks, name))
        start, end = converted
        if cue.get("confidence") is not None:
            confidence = _confidence(cue["confidence"])
            confidence_source = "cue.confidence"
    source = str(cue.get("source_text", "")) if cue else warning
    translated = str(cue.get("translated_text", source)) if cue else source
    return ReviewFinding(
        "finding-" + hashlib.sha256(json.dumps([job_id, cue_id, warning], ensure_ascii=False).encode("utf-8")).hexdigest()[:32],
        reason,
        start,
        end,
        source or "Production warning",
        translated or source or "Production warning",
        str(cue["speaker_id"]) if cue and cue.get("speaker_id") else None,
        str(cue["voice_id"]) if cue and cue.get("voice_id") else None,
        str(cue["output_preview"]) if cue and cue.get("output_preview") else None,
        confidence,
        stages,
        cue_id,
        warning,
        confidence_source,
    )


def build_review_snapshot(
    job_id: str,
    *,
    cues: Sequence[Mapping[str, Any]] = (),
    warnings: Iterable[str | Mapping[str, Any]] = (),
    findings: Sequence[ReviewFinding] = (),
    revision: int = 0,
    time_base: tuple[str, str] = ("1", "1000"),
) -> ReviewSnapshot:
    time_base = ReviewSnapshot(job_id, revision, (), time_base=time_base).time_base
    result = list(findings)
    cue_map = {}
    for cue in cues:
        cue_id = _text(cue.get("cue_id"), "cue_id", 256)
        if cue_id in cue_map:
            raise ReviewError("duplicate canonical cue_id")
        cue_map[cue_id] = cue
    for warning in warnings:
        if isinstance(warning, Mapping):
            code = _text(warning.get("code"), "warning code", 256)
            message = _text(warning.get("message"), "warning message", 4096)
            text = _text(code + ": " + message, "warning", 4096)
            cue_id = warning.get("cue_id")
            if cue_id is not None:
                cue_id = _text(cue_id, "warning cue_id", 256)
                if cue_id not in cue_map:
                    raise ReviewError("structured warning references an unknown cue_id")
        else:
            text = _text(warning, "warning", 4096)
            # Legacy production TTS diagnostics explicitly put their cue ID
            # immediately after ':'. No list-index or substring matching.
            token = text.partition(":")[2].strip().split(";", 1)[0].split(" ", 1)[0]
            cue_id = token if token in cue_map else None
        result.append(_warning_finding(job_id, text, cue=cue_map.get(cue_id), time_base=time_base))
    # Low-confidence cues remain review data even when no warning was emitted.
    for cue in cues:
        confidence = cue.get("confidence")
        if confidence is not None and Decimal(_confidence(confidence)) < Decimal("0.5"):
            result.append(_warning_finding(job_id, "LOW_CONFIDENCE_CUE", cue=cue, time_base=time_base))
    # Identical diagnostics are one finding, with identity stable across order.
    unique = {}
    for finding in result:
        if finding.finding_id in unique and unique[finding.finding_id] != finding:
            raise ReviewError("conflicting duplicate review finding id")
        unique[finding.finding_id] = finding
    return ReviewSnapshot(job_id, revision, tuple(unique.values()), time_base=time_base)


__all__ = ["ReviewError", "ReviewFinding", "ReviewSnapshot", "RegenerationPlan", "build_review_snapshot"]
