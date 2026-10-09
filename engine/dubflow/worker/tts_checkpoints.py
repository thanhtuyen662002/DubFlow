"""Bounded, worker-private per-cue recovery records; never durable job state.

The supervisor still owns SQLite. A record is committed only after the adapter
fsyncs its WAV. Incomplete or invalid records reprocess only the affected cue.
The adapter independently verifies waveform hashes, timing and quality on reuse.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Sequence

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts import AudioMetrics, TtsArtifact, TtsCheckpoint, TtsError, TtsInput
from .export_publication import plain

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 65536


def _encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _point(value: dict) -> TimePoint:
    if set(value) != {"kind", "schema_version", "ticks", "time_base"} or value["kind"] != "time_point" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid time point")
    base = value["time_base"]
    if set(base) != {"numerator", "denominator"}:
        raise ValueError("invalid time base")
    values = (value["ticks"], base["numerator"], base["denominator"])
    if any(not isinstance(item, str) or not re.fullmatch(r"-?(0|[1-9][0-9]{0,19})", item) for item in values):
        raise ValueError("invalid integer ticks")
    ticks, numerator, denominator = map(int, values)
    return TimePoint(ticks, TimeBase(numerator, denominator))


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate checkpoint field")
        value[key] = item
    return value


class TtsCheckpointStore:
    def __init__(self, audio_directory: Path, *, identity: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise TtsError("TTS_CHECKPOINT_IDENTITY_INVALID", "checkpoint recipe identity is invalid")
        self.audio_directory = audio_directory.absolute()
        self.directory = self.audio_directory / "checkpoints"
        plain(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.warnings: list[str] = []

    def _path(self, segment_id: str) -> Path:
        return self.directory / (sha256(segment_id.encode("utf-8")).hexdigest() + ".json")

    def _audio_path(self, artifact: TtsArtifact) -> None:
        path = Path(artifact.path)
        if not path.is_absolute() or path.parent != self.audio_directory or not re.fullmatch(r"tts-[a-f0-9]{32}\.wav", path.name):
            raise ValueError("checkpoint audio is outside its private generation")
        plain(path)

    def load(self, segments: Sequence[TtsInput]) -> dict[str, TtsCheckpoint]:
        result = {}
        for segment in segments:
            path = self._path(segment.segment_id)
            try:
                plain(path)
                with path.open("rb") as stream:
                    payload = stream.read(MAX_RECORD_BYTES + 1)
                if len(payload) > MAX_RECORD_BYTES:
                    raise ValueError("checkpoint record exceeds bounds")
                value = json.loads(payload, object_pairs_hook=_unique)
                if not isinstance(value, dict) or set(value) != {"schema_version", "identity", "artifact", "artifact_record_hash"} or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION or value["identity"] != self.identity:
                    raise ValueError("checkpoint recipe changed or record is invalid")
                fields = value["artifact"]
                if value["artifact_record_hash"] != sha256(_encoded(fields)).hexdigest():
                    raise ValueError("checkpoint metadata checksum differs")
                fields = dict(fields)
                for key in ("slot_start", "slot_end", "actual_end"):
                    fields[key] = _point(fields[key])
                fields["metrics"] = AudioMetrics(**fields["metrics"])
                if not isinstance(fields["warnings"], list):
                    raise ValueError("invalid checkpoint warnings")
                fields["warnings"] = tuple(fields["warnings"])
                artifact = TtsArtifact(**fields)
                if artifact.segment_id != segment.segment_id:
                    raise ValueError("checkpoint cue identity differs")
                self._audio_path(artifact)
                result[segment.segment_id] = TtsCheckpoint(artifact.segment_id, artifact.artifact_hash, artifact)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                self.warnings.append("discarded invalid TTS checkpoint for " + segment.segment_id)
        return result

    def commit(self, checkpoint: TtsCheckpoint) -> None:
        self._audio_path(checkpoint.artifact)
        fields = checkpoint.artifact.to_dict()
        payload = _encoded({"schema_version": SCHEMA_VERSION, "identity": self.identity, "artifact": fields,
                            "artifact_record_hash": sha256(_encoded(fields)).hexdigest()})
        if len(payload) > MAX_RECORD_BYTES:
            raise TtsError("TTS_CHECKPOINT_TOO_LARGE", "per-cue checkpoint exceeds bounds")
        path = self._path(checkpoint.segment_id)
        plain(path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".partial", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
