"""Prepare a cue from separately admitted current audio assignments.

This private format is distinct from the original-generation adapter. Its
caller must supply a native-admitted token and separate source/document pins.
There is no producer execution, filesystem authority or graph publication here.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from engine.dubflow.mix import FileSource
from engine.dubflow.tts import TtsInput
from .audio_composition import PinnedDocument, rebind_audio_composition
from .cue_plan import catalog_voice
from .export import MAX_JSON_BYTES, _hash
from .production import ReviewError, ReviewSnapshot, _tick
from .synthesis_input import _digest, _encode, prepare_cue_synthesis
from .synthesis_worker import admit_production_cue, production_cue_config
from .wire import decode_review_json, read_review_snapshot

CAPABILITY = "review_effective_audio_v1"


def prepare_effective_cue(raw: bytes, *, expected_token: Mapping, composition_raw: bytes,
                          composition_snapshot: ReviewSnapshot,
                          source: FileSource, source_sha256: str, original_inputs: Sequence[TtsInput],
                          documents: Sequence[PinnedDocument], mix_template: PinnedDocument,
                          app_root: Path, profile_path: Path) -> dict:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_JSON_BYTES:
        raise ReviewError("native effective cue context exceeds its byte bound")
    context = decode_review_json(raw.decode("utf-8"))
    fields = {"schema_version", "kind", "capability", "before_token", "before_snapshot_json",
              "after_snapshot_json", "current_composition_sha256", "composition_snapshot_sha256"}
    if (not isinstance(context, dict) or set(context) != fields or
        type(context["schema_version"]) is not int or context["schema_version"] != 1 or
        context["kind"] != "review_effective_cue_context" or context["capability"] != CAPABILITY):
        raise ReviewError("unknown effective cue context requires explicit compatibility")
    token = context["before_token"]
    token_fields = {"job_id", "revision", "snapshot_sha256", "graph_sha256", "original_manifest_sha256"}
    if (not isinstance(token, dict) or set(token) != token_fields or
        not isinstance(expected_token, Mapping) or set(expected_token) != token_fields or
        _encode(token, MAX_JSON_BYTES) != _encode(dict(expected_token), MAX_JSON_BYTES)):
        raise ReviewError("effective cue substitutes its admitted current token")
    if not isinstance(token["revision"], str):
        raise ReviewError("effective cue revision must retain its canonical public token")
    revision = int(_tick(token["revision"], "revision"))
    for key in ("snapshot_sha256", "graph_sha256", "original_manifest_sha256"):
        _hash(token[key])
    if any(not isinstance(context[key], str) for key in ("before_snapshot_json", "after_snapshot_json")):
        raise ReviewError("effective cue requires exact before/after JSON strings")
    before = read_review_snapshot(decode_review_json(context["before_snapshot_json"]))
    after = read_review_snapshot(decode_review_json(context["after_snapshot_json"]))
    if (not isinstance(before, ReviewSnapshot) or not isinstance(after, ReviewSnapshot) or
        before.job_id != token["job_id"] or before.revision != revision or
        _digest(context["before_snapshot_json"].encode("utf-8")) != token["snapshot_sha256"] or
        after.last_plan is None):
        raise ReviewError("effective cue differs from its admitted current composition or snapshot")
    if (not isinstance(composition_snapshot, ReviewSnapshot) or
        _digest(_encode(composition_snapshot.to_dict(), MAX_JSON_BYTES)) !=
        context["composition_snapshot_sha256"]):
        raise ReviewError("effective cue substitutes its admitted composition snapshot")
    current = rebind_audio_composition(composition_raw,
        expected_sha256=context["current_composition_sha256"], content_snapshot=composition_snapshot,
        current_snapshot=before,
        manifest_sha256=token["original_manifest_sha256"], source=source, source_sha256=source_sha256,
        original_inputs=original_inputs, documents=documents, mix_template=mix_template)
    finding = next((item for item in after.findings if item.finding_id == after.last_plan.finding_id), None)
    selected = next((item for item in current.cues if finding is not None and
                     item.canonical_input.segment_id == finding.cue_id), None)
    if selected is None:
        raise ReviewError("effective cue has no admitted current assignment")
    config = production_cue_config()
    previous = selected.provenance
    admit_production_cue(previous, config)
    if previous.config_hash != config.content_hash() or previous.resource != config.resource:
        raise ReviewError("current cue producer configuration requires explicit compatibility")
    voice = catalog_voice(app_root, profile_path, finding.voice)
    target = replace(previous, voice_id=voice.voice_id, voice_version=voice.voice_version,
                     voice_hash=voice.content_hash())
    request = prepare_cue_synthesis(before, after, manifest_sha256=current.manifest_sha256,
        original_input=selected.canonical_input, previous_provenance=previous,
        target_provenance=target, voice=voice, config=config)
    return {"schema_version": 1, "kind": "review_prepared_effective_cue_request",
            "context_sha256": _digest(raw), "before_token": dict(token),
            "current_composition_sha256": context["current_composition_sha256"],
            "composition_snapshot_sha256": context["composition_snapshot_sha256"],
            "effective_composition_json": _encode(current.to_dict(), MAX_JSON_BYTES).decode("utf-8"),
            "effective_composition_sha256": current.input_hash,
            "request_json": request.encoded.decode("utf-8"), "request_sha256": request.input_hash,
            "config": config.to_dict(), "voice": voice.to_dict(),
            "model_weights_verified": False, "active_graph_published": False}
