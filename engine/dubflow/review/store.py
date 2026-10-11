"""Supervisor-owned atomic review state; does not access durable SQLite."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping

from .production import ReviewError, ReviewFinding, ReviewSnapshot, RegenerationPlan, _correction_stages
from .wire import LegacyReviewPayload, decode_review_json, finding_from_dict, plan_from_dict, read_review_snapshot

MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_UNDO = 256


def _cue_scope(finding: ReviewFinding) -> tuple[Any, ...]:
    return (finding.start_ticks, finding.end_ticks, finding.source_text, finding.translated_text,
            finding.speaker, finding.voice)


def _validate_cue_consistency(snapshot: ReviewSnapshot) -> None:
    scopes = {}
    for finding in snapshot.findings:
        if finding.cue_id is None:
            continue
        scope = _cue_scope(finding)
        if finding.cue_id in scopes and scopes[finding.cue_id] != scope:
            raise ReviewError("findings disagree on canonical cue content/mapping")
        scopes[finding.cue_id] = scope


def _apply_cue_content(snapshot: ReviewSnapshot, cue: ReviewFinding) -> tuple[ReviewFinding, ...]:
    return tuple(replace(item, translated_text=cue.translated_text, voice=cue.voice)
                 if item.cue_id == cue.cue_id else item for item in snapshot.findings)


class ReviewStore:
    def __init__(self, path: str | Path) -> None:
        candidate = Path(path).expanduser().absolute()
        if candidate.is_symlink():
            raise ReviewError("review store cannot be a symbolic link")
        self.path = candidate.resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        # Compatibility locator only: legacy sidecars are never mutated.
        self.history_path = self.path.with_suffix(self.path.suffix + ".history")

    @contextmanager
    def _writer(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise ReviewError("review store/lock cannot be a symbolic link")
        with self.lock_path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            locked = False
            try:
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as error:
                    raise ReviewError("review writer is busy; reload before retrying") from error
                yield
            finally:
                if locked:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self, job_id: str) -> tuple[ReviewSnapshot, list[dict[str, Any]]]:
        if not self.path.exists():
            return ReviewSnapshot(job_id, 0, ()), []
        try:
            with self.path.open("rb") as stream:
                data = stream.read(MAX_STORE_BYTES + 1)
            if len(data) > MAX_STORE_BYTES:
                raise ReviewError("review store exceeds byte limit")
            value = decode_review_json(data.decode("utf-8"))
        except (OSError, UnicodeError) as error:
            raise ReviewError(f"review store is unreadable: {error}") from error
        if isinstance(value, Mapping) and "schema_version" in value:
            snapshot, undo = read_review_snapshot(value), []
        else:
            if not isinstance(value, Mapping) or set(value) != {"store_version", "snapshot", "undo"} or type(value["store_version"]) is not int or value["store_version"] != 1:
                raise ReviewError("unsupported review store format")
            snapshot, undo = read_review_snapshot(value["snapshot"]), value["undo"]
        if isinstance(snapshot, LegacyReviewPayload):
            raise ReviewError("v1 review is display-only; explicit original-job mapping is required")
        if snapshot.job_id != job_id:
            raise ReviewError("review snapshot belongs to another job")
        _validate_cue_consistency(snapshot)
        if not isinstance(undo, list) or len(undo) > MAX_UNDO:
            raise ReviewError("review undo stack is invalid")
        finding_ids = {finding.finding_id for finding in snapshot.findings}
        for item in undo:
            if not isinstance(item, Mapping) or set(item) != {"before", "last_plan"}:
                raise ReviewError("review undo delta is malformed")
            before = finding_from_dict(item["before"])
            if before.finding_id not in finding_ids:
                raise ReviewError("undo references an unknown finding")
            plan = plan_from_dict(item["last_plan"])
            if plan is not None and plan.finding_id not in finding_ids:
                raise ReviewError("undo plan references an unknown finding")
        return snapshot, undo

    def load(self, job_id: str) -> ReviewSnapshot:
        return self._read(job_id)[0]

    def initialize(self, snapshot: ReviewSnapshot) -> None:
        with self._writer():
            if self.path.exists():
                raise ReviewError("review store already exists")
            _validate_cue_consistency(snapshot)
            self._atomic_write({"store_version": 1, "snapshot": snapshot.to_dict(), "undo": []})

    def commit_correction(self, snapshot: ReviewSnapshot, finding_id: str, *, translation: str | None = None,
                          voice: str | None = None) -> tuple[ReviewSnapshot, RegenerationPlan]:
        with self._writer():
            current, history = self._read(snapshot.job_id)
            if current.to_dict() != snapshot.to_dict():
                raise ReviewError("review snapshot changed; reload before applying a correction")
            selected = next((finding for finding in current.findings if finding.finding_id == finding_id), None)
            if selected is None:
                raise ReviewError("unknown review finding")
            if selected.cue_id is None or selected.start_ticks is None:
                raise ReviewError("global/unmapped findings cannot authorize cue regeneration")
            if translation is not None and not isinstance(translation, str):
                raise ReviewError("translation must be text")
            replacement = replace(selected, translated_text=translation.strip() if translation is not None else selected.translated_text,
                                  voice=voice if voice is not None else selected.voice)
            stages = _correction_stages(translation_changed=replacement.translated_text != selected.translated_text,
                                        voice_changed=replacement.voice != selected.voice)
            if len(history) >= MAX_UNDO:
                raise ReviewError("review undo depth limit reached")
            plan = RegenerationPlan(finding_id, stages, ("asr", "ocr"), tuple("replacement:" + stage for stage in stages))
            updated = _apply_cue_content(current, replacement)
            next_snapshot = replace(current, revision=current.revision + 1, findings=updated, last_plan=plan)
            history = history + [{"before": selected.to_dict(), "last_plan": current.last_plan.to_dict() if current.last_plan else None}]
            self._atomic_write({"store_version": 1, "snapshot": next_snapshot.to_dict(), "undo": history})
            return next_snapshot, plan

    def undo(self, snapshot: ReviewSnapshot) -> ReviewSnapshot:
        with self._writer():
            current, history = self._read(snapshot.job_id)
            if current.to_dict() != snapshot.to_dict():
                raise ReviewError("review snapshot changed; reload before undo")
            if not history:
                raise ReviewError("no review correction to undo")
            previous = finding_from_dict(history[-1]["before"])
            selected = next(item for item in current.findings if item.finding_id == previous.finding_id)
            if previous.cue_id is None or previous.cue_id != selected.cue_id or previous.start_ticks != selected.start_ticks or previous.end_ticks != selected.end_ticks or previous.source_text != selected.source_text:
                raise ReviewError("undo cannot change canonical cue identity/range/source")
            findings = _apply_cue_content(current, previous)
            restored = replace(current, revision=current.revision + 1, findings=findings,
                               last_plan=plan_from_dict(history[-1]["last_plan"]))
            self._atomic_write({"store_version": 1, "snapshot": restored.to_dict(), "undo": history[:-1]})
            return restored

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(encoded) > MAX_STORE_BYTES:
            raise ReviewError("review store exceeds byte limit")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".partial", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
