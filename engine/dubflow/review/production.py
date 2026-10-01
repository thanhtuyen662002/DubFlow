"""Dependency-free Review Center artifacts for B1/B2 production jobs.

The worker emits a snapshot after QC.  Corrections are versioned and planned
against exact downstream stages; old valid artifacts remain untouched until a
caller validates replacement outputs and commits the new snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
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
    if isinstance(value, str) and _TICKS.fullmatch(value) and int(value) <= _I64_MAX:
        return value
    raise ReviewError(f"{name} must be a canonical non-negative integer tick")


def _confidence(value: object) -> str:
    if isinstance(value, bool):
        raise ReviewError("confidence must be a finite decimal")
    if isinstance(value, str):
        if not _DECIMAL.fullmatch(value):
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
    start_ticks: str
    end_ticks: str
    source_text: str
    translated_text: str
    speaker: str | None
    voice: str | None
    output_preview: str | None
    confidence: str
    affected_stages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _ID.fullmatch(_text(self.finding_id, "finding_id", 256)):
            raise ReviewError("finding_id has an invalid format")
        if self.reason not in REASONS:
            raise ReviewError("review reason is invalid")
        start, end = _tick(self.start_ticks, "start_ticks"), _tick(self.end_ticks, "end_ticks")
        if int(end) <= int(start):
            raise ReviewError("review range must be positive")
        object.__setattr__(self, "start_ticks", start)
        object.__setattr__(self, "end_ticks", end)
        object.__setattr__(self, "source_text", _text(self.source_text, "source_text"))
        object.__setattr__(self, "translated_text", _text(self.translated_text, "translated_text"))
        for name in ("speaker", "voice", "output_preview"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name, 4096))
        confidence = _confidence(self.confidence)
        object.__setattr__(self, "confidence", confidence)
        stages = tuple(_text(item, "affected_stage", 256) for item in self.affected_stages)
        if not stages or len(set(stages)) != len(stages):
            raise ReviewError("affected stages must be unique and non-empty")
        object.__setattr__(self, "affected_stages", stages)

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "reason": self.reason,
            "range": {"start_ticks": self.start_ticks, "end_ticks": self.end_ticks},
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "speaker": self.speaker,
            "voice": self.voice,
            "output_preview": self.output_preview,
            "confidence": self.confidence,
            "affected_stages": list(self.affected_stages),
        }


@dataclass(frozen=True)
class RegenerationPlan:
    finding_id: str
    stages_to_rerun: tuple[str, ...]
    preserved_artifacts: tuple[str, ...]
    replacement_artifacts: tuple[str, ...]
    retain_previous_until_validated: bool = True
    requires_full_manual_review: bool = False

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

    def __post_init__(self) -> None:
        _text(self.job_id, "job_id", 256)
        if type(self.revision) is not int or self.revision < 0:
            raise ReviewError("revision is invalid")
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ReviewError("duplicate review finding id")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "revision": self.revision,
            "findings": [finding.to_dict() for finding in self.findings],
            "last_plan": self.last_plan.to_dict() if self.last_plan else None,
        }


def _warning_finding(job_id: str, index: int, warning: str, *, cue: Mapping[str, Any] | None = None) -> ReviewFinding:
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
    start = cue.get("start_ms", 0) if cue else 0
    end = cue.get("end_ms", max(int(start) + 1, 1000)) if cue else 1000
    source = str(cue.get("source_text", "")) if cue else "Production warning"
    translated = str(cue.get("translated_text", source)) if cue else source
    return ReviewFinding(
        f"{job_id}:finding:{index}",
        reason,
        _tick(start, "start_ticks"),
        _tick(end, "end_ticks"),
        source or "Production warning",
        translated or source or "Production warning",
        str(cue["speaker_id"]) if cue and cue.get("speaker_id") else None,
        str(cue["voice_id"]) if cue and cue.get("voice_id") else None,
        None,
        "0.5" if reason != "SYNC_QC_WARNING" else "0.7",
        stages,
    )


def build_review_snapshot(
    job_id: str,
    *,
    cues: Sequence[Mapping[str, Any]] = (),
    warnings: Iterable[str] = (),
    findings: Sequence[ReviewFinding] = (),
    revision: int = 0,
) -> ReviewSnapshot:
    result = list(findings)
    for index, warning in enumerate(warnings, start=1):
        result.append(_warning_finding(job_id, index, _text(warning, "warning", 4096), cue=cues[index - 1] if index <= len(cues) else None))
    # Low-confidence cues remain review data even when no warning was emitted.
    for cue in cues:
        confidence = float(cue.get("confidence", 1.0))
        if confidence < 0.5:
            result.append(_warning_finding(job_id, len(result) + 1, "LOW_CONFIDENCE_CUE", cue=cue))
    return ReviewSnapshot(job_id, revision, tuple(result))


class ReviewStore:
    """Atomic JSON snapshot store with undo-safe replacement semantics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.history_path = self.path.with_suffix(self.path.suffix + ".history")

    def load(self, job_id: str) -> ReviewSnapshot:
        if not self.path.is_file():
            return ReviewSnapshot(job_id, 0, ())
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReviewError(f"review snapshot is unreadable: {error}") from error
        if not isinstance(value, Mapping) or value.get("job_id") != job_id:
            raise ReviewError("review snapshot belongs to another job")
        findings: list[ReviewFinding] = []
        for raw in value.get("findings", []):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("range"), Mapping):
                raise ReviewError("review finding is malformed")
            item = raw["range"]
            findings.append(ReviewFinding(str(raw["finding_id"]), str(raw["reason"]), str(item["start_ticks"]), str(item["end_ticks"]), str(raw["source_text"]), str(raw["translated_text"]), raw.get("speaker"), raw.get("voice"), raw.get("output_preview"), str(raw["confidence"]), tuple(raw.get("affected_stages", ()))))
        return ReviewSnapshot(job_id, int(value.get("revision", 0)), tuple(findings))

    def commit_correction(self, snapshot: ReviewSnapshot, finding_id: str, *, translation: str | None = None, voice: str | None = None) -> tuple[ReviewSnapshot, RegenerationPlan]:
        current = self.load(snapshot.job_id)
        if current.revision != snapshot.revision:
            raise ReviewError("review snapshot changed; reload before applying a correction")
        selected = next((finding for finding in current.findings if finding.finding_id == finding_id), None)
        if selected is None:
            raise ReviewError("unknown review finding")
        if translation is None and voice is None:
            raise ReviewError("a correction must change translation or voice")
        replacement = ReviewFinding(selected.finding_id, selected.reason, selected.start_ticks, selected.end_ticks, selected.source_text, translation.strip() if translation is not None else selected.translated_text, selected.speaker, voice if voice is not None else selected.voice, selected.output_preview, selected.confidence, selected.affected_stages)
        updated = tuple(replacement if item.finding_id == finding_id else item for item in current.findings)
        plan = RegenerationPlan(finding_id, selected.affected_stages, ("asr", "ocr", "review:" + finding_id), tuple("replacement:" + stage for stage in selected.affected_stages))
        next_snapshot = ReviewSnapshot(current.job_id, current.revision + 1, updated, plan)
        self._atomic_write(next_snapshot.to_dict())
        return next_snapshot, plan

    def undo(self, job_id: str) -> ReviewSnapshot:
        history = self.history_path
        if not history.is_file():
            raise ReviewError("no review correction to undo")
        lines = history.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise ReviewError("no review correction to undo")
        previous = json.loads(lines[-1])
        history.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")
        self._atomic_write(previous)
        return self.load(job_id)

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = self.path.read_text(encoding="utf-8") if self.path.is_file() else None
        if previous is not None:
            try:
                previous_value = json.loads(previous)
            except json.JSONDecodeError as error:
                raise ReviewError(f"existing review snapshot is invalid: {error}") from error
            with self.history_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(previous_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".partial", dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise


__all__ = ["ReviewError", "ReviewFinding", "ReviewSnapshot", "RegenerationPlan", "ReviewStore", "build_review_snapshot"]
