"""Read bounded original metadata for explicit native Review initialization.

The native supervisor supplies the completed export pin. This adapter grants
no writer authority and does not certify referenced long media for reuse.
Every later regeneration/undo must revalidate the physical assets it uses.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from engine.dubflow.mix import parse_mix_json
from engine.dubflow.tts import TtsInput, parse_tts_json
from engine.dubflow.tts.adapter import _parse_point
from engine.dubflow.worker.b2_audio import _dubbing_windows, _input_hash, _source_language
from engine.dubflow.worker.tts_rewrites import validate_spoken_text_document
from .export import MAX_JSON_BYTES, _controlled, _json_bytes, _point_seconds, build_review_from_export
from .production import ReviewError
from .synthesis_input import _digest, _encode
from .wire import decode_review_json

MAX_CUES = 10000


def original_seed(manifest_path: Path, *, expected_job_id: str, expected_manifest_sha256: str) -> dict:
    bound = build_review_from_export(manifest_path, expected_job_id=expected_job_id,
                                    expected_manifest_sha256=expected_manifest_sha256)
    output = Path(bound.output_root)
    path = _controlled(manifest_path, output)
    manifest_raw = _json_bytes(path, expected_manifest_sha256)
    manifest = decode_review_json(manifest_raw.decode("utf-8"))
    descriptors = manifest["artifacts"]
    documents, raw_documents = {}, {}
    archive_bytes = len(manifest_raw)

    def load(role: str) -> bytes:
        nonlocal archive_bytes
        descriptor = descriptors.get(role)
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256", "size_bytes"}:
            raise ReviewError("original metadata needs a pinned descriptor or explicit compatibility adapter: " + role)
        controlled = _controlled(descriptor["path"], output)
        raw = _json_bytes(controlled, descriptor["sha256"], descriptor["size_bytes"])
        archive_bytes += len(raw)
        if archive_bytes > MAX_JSON_BYTES:
            raise ReviewError("original archived metadata exceeds its combined JSON byte limit")
        decode_review_json(raw.decode("utf-8"))
        documents[role] = dict(descriptor)
        raw_documents[role] = raw
        return raw

    timeline = decode_review_json(load("editable_timeline").decode("utf-8"))
    load("qc_report")
    cues = timeline["cues"]
    if len(cues) > MAX_CUES:
        raise ReviewError("original Review cue count exceeds its bound")
    dubbing = manifest.get("dubbing")
    if not isinstance(dubbing, dict) or type(dubbing.get("enabled")) is not bool:
        raise ReviewError("original export does not declare its actual B1/B2 mode")
    b2 = "tts_document" in descriptors
    if dubbing["enabled"] != b2:
        raise ReviewError("original dubbing mode and published TTS disagree")
    inputs, producer = [], None
    if b2:
        speech_raw = load("tts_document")
        speech = parse_tts_json(speech_raw)
        placement_raw = load("dubbing_placement")
        placement = decode_review_json(placement_raw.decode("utf-8"))
        spoken = decode_review_json(load("dubbing_spoken_text").decode("utf-8"))
        mix = parse_mix_json(load("mix_document"))
        fields = {"schema_version", "kind", "recipe", "canonical_cue_input_hash", "source_audio_sha256",
                  "source_end", "time_base", "windows", "gap_evidence"}
        if (not isinstance(placement, dict) or set(placement) != fields or
            type(placement["schema_version"]) is not int or placement["schema_version"] != 1 or
            placement["kind"] != "dubbing_placement" or placement["recipe"] != "source-intercue-postroll-2000ms-gap120ms-v1" or
            placement["gap_evidence"] != "recognized-source-cue-intervals;silence-not-certified" or
            placement["time_base"] != {"numerator": "1", "denominator": "1000"}):
            raise ReviewError("original placement recipe/version requires explicit compatibility admission")
        mappings = [{key: cue[key] for key in ("cue_id", "start_ms", "end_ms", "source_text", "translated_text", "confidence")}
                    for cue in cues]
        source_end = _parse_point(placement["source_end"], "source_end")
        if (source_end.time_base.to_dict() != placement["time_base"] or
            placement["canonical_cue_input_hash"] != _input_hash(mappings) or
            placement["windows"] != list(_dubbing_windows(mappings, source_end.ticks)) or
            speech["provenance"]["input_hash"] != _digest(placement_raw) or
            mix["provenance"]["source_hash"] != placement["source_audio_sha256"] or
            mix["source_end"] != placement["source_end"] or
            descriptors.get("source_audio", {}).get("sha256") != placement["source_audio_sha256"]):
            raise ReviewError("original placement/TTS/mix/source bindings differ")
        windows = {item["cue_id"]: item for item in placement["windows"]}
        source_language = _source_language(manifest.get("language", {}).get("source_language"))
        for cue in mappings:
            window = windows[cue["cue_id"]]
            segment = TtsInput(cue["cue_id"], cue["cue_id"], cue["translated_text"],
                               _parse_point(window["slot_start"], "start"), _parse_point(window["slot_end"], "end"),
                               source_language=source_language, confidence=cue["confidence"],
                               render_window_end=_parse_point(window["render_window_end"], "render_window_end"))
            inputs.append(segment)
        if speech["source_segment_ids"] != [item.segment_id for item in inputs]:
            raise ReviewError("original TTS source order differs from canonical cues")
        inputs_by_id = {item.segment_id: item for item in inputs}
        for artifact in speech["artifacts"]:
            segment = inputs_by_id[artifact["segment_id"]]
            if (artifact["source_utterance_id"] != segment.source_utterance_id or
                _point_seconds(artifact["slot_start"]) != Fraction(segment.start.ticks, 1000) or
                _point_seconds(artifact["slot_end"]) != Fraction(segment.end.ticks, 1000) or
                artifact.get("render_window_end") != segment.render_window_end.to_dict() or artifact["confidence"] != segment.confidence):
                raise ReviewError("original TTS artifact differs from canonical input/window")
        validate_spoken_text_document(spoken, speech_raw, tuple(inputs), identity=spoken.get("checkpoint_identity"))
        producer = speech["provenance"]
    # Re-read exactly the metadata we consumed, before returning to the native
    # transaction. No long MP4/PCM scan or inference is hidden in this command.
    for role, raw in raw_documents.items():
        descriptor = documents[role]
        if _json_bytes(_controlled(descriptor["path"], output), descriptor["sha256"], descriptor["size_bytes"]) != raw:
            raise ReviewError("original metadata changed during seed preparation")
    if _json_bytes(path, expected_manifest_sha256) != manifest_raw:
        raise ReviewError("original manifest changed during seed preparation")
    snapshot = _encode(bound.snapshot.to_dict(), MAX_JSON_BYTES)
    graph = _encode({"schema_version": 1, "kind": "review_original_graph", "job_id": expected_job_id,
        "revision": "0", "mode": "b2" if b2 else "b1", "manifest_sha256": expected_manifest_sha256,
        "documents": documents, "time_base": bound.snapshot.to_dict()["time_base"],
        "source_clock_mapping": timeline.get("source_clock_mapping"),
        "canonical_inputs": [item.to_dict() for item in inputs], "tts_provenance": producer,
        "media_policy": "revalidate-before-reuse", "producer_policy": "explicit-release-admission"}, MAX_JSON_BYTES)
    if len(snapshot) + len(graph) > MAX_JSON_BYTES:
        raise ReviewError("original Review state exceeds its combined byte budget")
    return {"schema_version": 1, "kind": "review_original_seed", "job_id": expected_job_id,
            "manifest_sha256": expected_manifest_sha256,
            "snapshot_json": snapshot.decode("utf-8"), "graph_json": graph.decode("utf-8")}
