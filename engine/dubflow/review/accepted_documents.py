"""Prepare pinned single-cue metadata after native immutable WAV acceptance.

The supervisor supplies the admitted request, candidate bytes, recipe and
accepted-object pin. This read-only adapter preserves producer/input/semantic
evidence while changing only the WAV path and dependent TTS-document hash.
It neither grants graph authority nor publishes files or writes SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from engine.dubflow.mix import streaming
from engine.dubflow.tts import validate_tts_document
from engine.dubflow.tts.adapter import _parse_point
from engine.dubflow.worker.tts_rewrites import validate_spoken_text_document
from .export import MAX_JSON_BYTES, _hash
from .production import ReviewError
from .synthesis_input import CueSynthesisInput, _digest, _encode, read_cue_synthesis_input
from .synthesis_worker import cue_checkpoint_identity
from .wire import decode_review_json

MAX_CUE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class AcceptedWavePin:
    """Native storage descriptor; caller JSON alone does not establish authority."""
    path: Path
    root: Path
    sha256: str
    size_bytes: int
    sample_rate: int
    channels: int
    frame_count: int

    def verify(self) -> None:
        path, root = Path(self.path), Path(self.root)
        _hash(self.sha256)
        if (not root.is_absolute() or ".." in root.parts or ".." in path.parts or
            path != root / (self.sha256[7:] + ".wav") or
            any(type(item) is not int for item in (self.size_bytes, self.sample_rate, self.channels, self.frame_count)) or
            not 44 <= self.size_bytes <= MAX_CUE_BYTES or not 8000 <= self.sample_rate <= 96000 or
            self.channels not in (1, 2) or not 1 <= self.frame_count <= (self.size_bytes - 44) // (self.channels * 2)):
            raise ReviewError("accepted cue pin/path is outside native storage bounds")
        streaming._plain(root); streaming._plain(path)
        metadata = path.stat()
        readonly = ((metadata.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY) != 0
                    if hasattr(metadata, "st_file_attributes") else metadata.st_mode & 0o222 == 0)
        if not stat.S_ISREG(metadata.st_mode) or not readonly or metadata.st_size != self.size_bytes:
            raise ReviewError("accepted cue is missing, unsealed or has changed size")
        wave = streaming._wave(path, MAX_CUE_BYTES, self.sha256)
        if (wave.rate, wave.channels, wave.frames) != (self.sample_rate, self.channels, self.frame_count):
            raise ReviewError("accepted cue PCM header differs from its native pin")


@dataclass(frozen=True)
class PreparedCueDocuments:
    tts: bytes
    spoken_text: bytes

    @property
    def tts_sha256(self) -> str:
        return _digest(self.tts)

    @property
    def spoken_text_sha256(self) -> str:
        return _digest(self.spoken_text)


def _document(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_JSON_BYTES:
        raise ReviewError("cue document bytes exceed their bound")
    value = decode_review_json(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ReviewError("cue document requires an object root")
    return value


def prepare_accepted_cue_documents(request: CueSynthesisInput, *, candidate_tts: bytes,
                                   candidate_spoken_text: bytes, accepted_wave: AcceptedWavePin,
                                   recipe_identity: str) -> PreparedCueDocuments:
    """Return new immutable document bytes, preserving all unrelated fields.

    Upstream producer/quality admission and native final WAV revalidation before
    CAS remain the graph caller's responsibility. No mutable candidate path is
    read or retained; a discarded producer directory cannot break accepted audio.
    """
    if read_cue_synthesis_input(request.encoded, expected_hash=request.input_hash) != request:
        raise ReviewError("cue request object differs from its exact admitted bytes")
    accepted_wave.verify()
    identity = cue_checkpoint_identity(request, recipe_identity)
    speech, spoken = _document(candidate_tts), _document(candidate_spoken_text)
    validate_tts_document(speech)
    if ((speech["schema_version"] == 1 and request.segment.render_window_end is not None) or
        speech["source_segment_ids"] != [request.segment.segment_id] or
        len(speech["artifacts"]) != 1 or speech["failures"] or speech["provenance"] != request.provenance.to_dict()):
        raise ReviewError("accepted metadata needs exactly one successful pinned cue and compatible version")
    artifact, segment = speech["artifacts"][0], request.segment
    actual_ticks = segment.start.ticks + (
        accepted_wave.frame_count * segment.start.time_base.denominator +
        accepted_wave.sample_rate * segment.start.time_base.numerator - 1
    ) // (accepted_wave.sample_rate * segment.start.time_base.numerator)
    if (artifact["source_utterance_id"] != segment.source_utterance_id or
        _parse_point(artifact["slot_start"], "start") != segment.start or
        _parse_point(artifact["slot_end"], "end") != segment.end or
        artifact.get("render_window_end") != (segment.render_window_end.to_dict() if segment.render_window_end else None) or
        artifact["confidence"] != segment.confidence or artifact["fallback_used"] or
        artifact["content_hash"] != accepted_wave.sha256 or
        (artifact["sample_rate"], artifact["channels"], artifact["frame_count"]) !=
        (accepted_wave.sample_rate, accepted_wave.channels, accepted_wave.frame_count) or
        (artifact["sample_rate"], artifact["channels"]) != (request.config.sample_rate, request.config.channels) or
        _parse_point(artifact["actual_end"], "actual_end") != type(segment.start)(actual_ticks, segment.start.time_base) or
        accepted_wave.size_bytes > request.config.max_wave_bytes):
        raise ReviewError("accepted cue metadata differs from native input/audio/config pins")
    validate_spoken_text_document(spoken, candidate_tts, (segment,), identity=identity)
    artifact["path"] = str(accepted_wave.path)
    tts = _encode(speech, MAX_JSON_BYTES - 1) + b"\n"
    # Artifact hashes deliberately exclude storage path; producer and chunk
    # hashes remain pinned. Only this exact document's byte hash changes.
    spoken["tts_document_hash"] = _digest(tts)
    spoken_text = _encode(spoken, MAX_JSON_BYTES - 1) + b"\n"
    validate_tts_document(_document(tts))
    validate_spoken_text_document(_document(spoken_text), tts, (segment,), identity=identity)
    accepted_wave.verify()
    return PreparedCueDocuments(tts, spoken_text)
