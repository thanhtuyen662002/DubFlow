"""Compose immutable per-cue speech pins into a private AUD-0 candidate.

Inputs must already be admitted by the supervisor. Each cue retains its own
TTS document/voice provenance; a replacement never relabels old waveforms.
This module does not execute ASR/OCR/TTS or mutate the active output graph.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Mapping, Sequence

from engine.dubflow.mix import FileSegment, FileSource, MixConfig, MixDocument, ResourceProfile
from .mix_io import ReviewStreamingAudioMixer as StreamingAudioMixer
from engine.dubflow.mix import streaming
from engine.dubflow.mix.adapter import MixProvenance, parse_mix_json
from engine.dubflow.tts import ResourceProfile as TtsResourceProfile, TtsInput, TtsProvenance, parse_tts_json
from engine.dubflow.tts.adapter import _parse_point
from .export import MAX_JSON_BYTES, _hash, _json_bytes
from .production import ReviewError, ReviewSnapshot
from .store import _validate_cue_consistency
from .synthesis_input import CueSynthesisInput, _digest, _encode, prepare_cue_synthesis
from .synthesis_worker import _writer

MAX_CUES = 10000


@dataclass(frozen=True)
class PinnedDocument:
    path: Path
    sha256: str
    size_bytes: int
    artifact_root: Path
    metadata_root: Path | None = None

    def __post_init__(self):
        path, root = Path(self.path).absolute(), Path(self.artifact_root).absolute()
        metadata = Path(self.metadata_root).absolute() if self.metadata_root is not None else root
        streaming._plain(path); streaming._plain(root); streaming._plain(metadata)
        if not path.is_relative_to(metadata):
            raise ReviewError("audio document is outside its admitted artifact root")
        _hash(self.sha256)
        if type(self.size_bytes) is not int or not 1 <= self.size_bytes <= MAX_JSON_BYTES:
            raise ReviewError("audio document byte count is invalid")
        object.__setattr__(self, "path", path); object.__setattr__(self, "artifact_root", root)
        if self.metadata_root is not None:
            object.__setattr__(self, "metadata_root", metadata)

    def read(self) -> bytes:
        streaming._plain(self.path)
        return _json_bytes(self.path, self.sha256, self.size_bytes)

    def to_dict(self) -> dict:
        value = {"path": str(self.path), "sha256": self.sha256, "size_bytes": self.size_bytes,
                 "artifact_root": str(self.artifact_root)}
        if self.metadata_root is not None:
            value["metadata_root"] = str(self.metadata_root)
        return value


@dataclass(frozen=True)
class CueAudio:
    canonical_input: TtsInput
    document: PinnedDocument
    provenance: TtsProvenance
    segment: FileSegment


def _provenance(value: dict) -> TtsProvenance:
    fields = dict(value); fields["resource"] = TtsResourceProfile(**fields["resource"])
    return TtsProvenance(**fields)


def _cue(document: PinnedDocument, value: dict, segment: TtsInput) -> CueAudio:
    if segment.segment_id not in value["source_segment_ids"]:
        raise ReviewError("assigned TTS document does not contain its canonical cue")
    provenance = _provenance(value["provenance"])
    artifact = next((item for item in value["artifacts"] if item["segment_id"] == segment.segment_id), None)
    if artifact is None:
        failure = next((item for item in value["failures"] if item["segment_id"] == segment.segment_id), None)
        if failure is None:
            raise ReviewError("cue has neither an artifact nor explicit failure evidence")
        output = FileSegment(segment.segment_id, segment.source_utterance_id, segment.start, segment.end,
                             status="failed", condition=failure["code"] + ": " + failure["condition"],
                             confidence=segment.confidence)
    else:
        if (artifact["source_utterance_id"] != segment.source_utterance_id or
            _parse_point(artifact["slot_start"], "start") != segment.start or
            _parse_point(artifact["slot_end"], "end") != segment.end or
            artifact.get("render_window_end") != (segment.render_window_end.to_dict() if segment.render_window_end else None) or
            artifact["confidence"] != segment.confidence):
            raise ReviewError("TTS artifact differs from canonical cue mapping/window/confidence")
        path = Path(artifact["path"])
        streaming._plain(path)
        if not path.is_relative_to(document.artifact_root):
            raise ReviewError("TTS waveform is outside its admitted artifact root")
        audio = streaming._wave(path, 32 * 1024 * 1024, artifact["content_hash"])
        if (audio.rate, audio.channels, audio.frames) != (artifact["sample_rate"], artifact["channels"], artifact["frame_count"]):
            raise ReviewError("TTS waveform header differs from its pinned artifact")
        actual_end = _parse_point(artifact["actual_end"], "actual_end")
        output = FileSegment(segment.segment_id, segment.source_utterance_id, segment.start,
                actual_end if segment.render_window_end is not None else segment.end, path,
                artifact["content_hash"], confidence=segment.confidence, fallback_used=artifact["fallback_used"])
    return CueAudio(segment, document, provenance, output)


@dataclass(frozen=True)
class AudioComposition:
    snapshot: ReviewSnapshot
    manifest_sha256: str
    source: FileSource
    source_sha256: str
    cues: tuple[CueAudio, ...]
    mix_producer_pin: str

    def to_dict(self) -> dict:
        documents = {item.document.sha256: item.document for item in self.cues}
        version = 2 if any(item.metadata_root is not None for item in documents.values()) else 1
        return {"schema_version": version, "kind": "review_audio_composition", "job_id": self.snapshot.job_id,
                "manifest_sha256": self.manifest_sha256,
                "review_snapshot_sha256": _digest(_encode(self.snapshot.to_dict(), MAX_JSON_BYTES)),
                "source": {"path": str(self.source.path), "sha256": self.source_sha256,
                    "start": self.source.start.to_dict(), "end": self.source.end.to_dict(),
                    "source_id": self.source.source_id, "layout": self.source.layout},
                "documents": [documents[key].to_dict() for key in sorted(documents)],
                "cues": [{"tts_input": item.canonical_input.to_dict(), "document_sha256": item.document.sha256} for item in self.cues],
                "mix_producer_pin": json.loads(self.mix_producer_pin)}

    @property
    def input_hash(self) -> str:
        return _digest(_encode(self.to_dict(), MAX_JSON_BYTES))


def compose_audio(snapshot: ReviewSnapshot, *, manifest_sha256: str, source: FileSource,
                  source_sha256: str, canonical_inputs: Sequence[TtsInput],
                  assignments: Mapping[str, PinnedDocument], mix_template: PinnedDocument) -> AudioComposition:
    """Read a complete active assignment; mixed voices retain separate documents."""
    _hash(manifest_sha256); _hash(source_sha256); _validate_cue_consistency(snapshot)
    inputs = tuple(canonical_inputs)
    identifiers = {item.segment_id for item in inputs}
    if not 1 <= len(inputs) <= MAX_CUES or len(identifiers) != len(inputs) or set(assignments) != identifiers:
        raise ReviewError("audio composition needs one assignment per unique canonical cue")
    if (source.start.time_base.numerator, source.start.time_base.denominator) != tuple(map(int, snapshot.time_base)):
        raise ReviewError("audio composition source differs from the canonical time base")
    if streaming.file_hash(source.path) != source_sha256:
        raise ReviewError("composition source audio differs from its trusted hash")
    mix = parse_mix_json(mix_template.read())
    if (mix["source_id"] != source.source_id or mix["source_layout"] != source.layout or
        _parse_point(mix["source_start"], "start") != source.start or _parse_point(mix["source_end"], "end") != source.end or
        mix["provenance"]["source_hash"] != source_sha256):
        raise ReviewError("mix template differs from its original source identity/range/hash")
    producer = {key: value for key, value in mix["provenance"].items() if key not in {"input_hash", "source_hash"}}
    parsed, descriptors = {}, {}
    cues = []
    for segment in inputs:
        if segment.start.time_base != source.start.time_base or segment.start.ticks < source.start.ticks or segment.end.ticks > source.end.ticks:
            raise ReviewError("canonical cue lies outside the source interval/time base")
        descriptor = assignments[segment.segment_id]
        if descriptor.sha256 in descriptors and descriptors[descriptor.sha256] != descriptor:
            raise ReviewError("one document hash cannot authorize conflicting paths/roots")
        descriptors[descriptor.sha256] = descriptor
        if descriptor.sha256 not in parsed:
            value = parse_tts_json(descriptor.read())
            if not set(value["source_segment_ids"]) <= identifiers:
                raise ReviewError("TTS document contains an unknown source cue")
            parsed[descriptor.sha256] = value
        cues.append(_cue(descriptor, parsed[descriptor.sha256], segment))
    by_id = {item.canonical_input.segment_id: item for item in cues}
    for finding in snapshot.findings:
        if finding.cue_id is None: continue
        cue = by_id.get(finding.cue_id)
        if (cue is None or finding.translated_text != cue.canonical_input.text or finding.voice != cue.provenance.voice_id or
            finding.start_ticks != str(cue.canonical_input.start.ticks) or finding.end_ticks != str(cue.canonical_input.end.ticks)):
            raise ReviewError("review findings differ from the active canonical audio assignment")
    return AudioComposition(snapshot, manifest_sha256, source, source_sha256, tuple(cues),
                            json.dumps(producer, sort_keys=True, separators=(",", ":")))


def replace_audio_cue(current: AudioComposition, after: ReviewSnapshot, *, request: CueSynthesisInput,
                      replacement_document: PinnedDocument) -> AudioComposition:
    selected = next((item for item in current.cues if item.canonical_input.segment_id == request.segment.segment_id), None)
    if selected is None: raise ReviewError("replacement refers to an unknown canonical audio cue")
    expected = prepare_cue_synthesis(current.snapshot, after, manifest_sha256=current.manifest_sha256,
                original_input=selected.canonical_input, previous_provenance=selected.provenance,
                target_provenance=request.provenance, voice=request.voice, config=request.config)
    if expected.encoded != request.encoded:
        raise ReviewError("replacement request differs from the authoritative active audio composition")
    document = parse_tts_json(replacement_document.read())
    if (document["source_segment_ids"] != [request.segment.segment_id] or len(document["artifacts"]) != 1 or
        document["failures"] or _provenance(document["provenance"]) != request.provenance):
        raise ReviewError("replacement document is not exactly one successful pinned cue")
    replacement = _cue(replacement_document, document, request.segment)
    return replace(current, snapshot=after, cues=tuple(replacement if item is selected else item for item in current.cues))


def mix_audio_candidate(composition: AudioComposition, *, output_root: Path) -> MixDocument:
    """Run the existing bounded/checkpointable mixer with the pinned producer."""
    config = MixConfig(requested_profile="cpu", resource=ResourceProfile(max_threads=1, max_memory_mb=512, max_batch_items=8))
    config_hash = streaming._hash_json({"dsp": config.to_dict(), "limits": streaming.StreamLimits().__dict__,
                                      "producer": streaming.PRODUCER_VERSION, "runtime": streaming.NUMPY_VERSION})
    expected = MixProvenance("dubflow-aud-0", streaming.PRODUCER_VERSION, streaming.BACKEND_ID,
            "owned-python/numpy-" + streaming.NUMPY_VERSION, "timeline-v1", config_hash,
            composition.source_sha256, composition.input_hash, "cpu", "cpu", config.resource).to_dict()
    if {key: value for key, value in expected.items() if key not in {"input_hash", "source_hash"}} != json.loads(composition.mix_producer_pin):
        raise ReviewError("installed mix producer/config differs from pinned job; compatibility admission required")
    root = Path(output_root).absolute() / ("mix-" + composition.input_hash[7:])
    streaming._plain(root); root.mkdir(parents=True, exist_ok=True)
    with _writer(root):
        # Recheck caller-admitted inputs immediately before and after the mixer;
        # its private candidates never become an active graph on file existence.
        def validate_inputs():
            if streaming.file_hash(composition.source.path) != composition.source_sha256:
                raise ReviewError("composition source audio changed")
            parsed = {}
            for item in composition.cues:
                if item.document.sha256 not in parsed: parsed[item.document.sha256] = parse_tts_json(item.document.read())
                if _cue(item.document, parsed[item.document.sha256], item.canonical_input) != item:
                    raise ReviewError("composition TTS assignment changed")
        validate_inputs()
        document = StreamingAudioMixer(config=config, output_dir=root, generation_root=root).mix(composition.source,
                tuple(item.segment for item in composition.cues), input_hash=composition.input_hash)
        validate_inputs()
        if document.provenance.source_hash != composition.source_sha256 or document.provenance.input_hash != composition.input_hash:
            raise ReviewError("mix candidate differs from its admitted input/source")
        successful = {item.segment.segment_id for item in composition.cues if item.segment.status == "available"}
        if any(item.segment_id in successful for item in document.failures):
            raise ReviewError("a verified composition waveform failed mixing")
        return document
