"""Prepare one immutable TTS request from a supervisor-admitted correction.

The supervisor supplies the committed manifest pin, authoritative before/after
snapshots, original canonical TTS input and verified producer/voice/config pins.
This module grants no writer lease and performs no filesystem/SQLite mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import hashlib
import json

from engine.dubflow.tts import TtsConfig, TtsInput, TtsProvenance, VoiceProfile, ResourceProfile
from engine.dubflow.tts.adapter import _parse_point
from .export import MAX_JSON_BYTES, _hash
from .production import RegenerationPlan, ReviewError, ReviewSnapshot, _correction_stages, _ID, _tick
from .store import _validate_cue_consistency
from .wire import decode_review_json

MAX_REQUEST_BYTES = 32768


def _encode(value: object, limit: int) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > limit:
        raise ReviewError("cue synthesis input exceeds byte limit")
    return raw


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CueSynthesisInput:
    encoded: bytes
    segment: TtsInput
    provenance: TtsProvenance
    config: TtsConfig
    voice: VoiceProfile

    @property
    def input_hash(self) -> str:
        return _digest(self.encoded)

    def to_dict(self) -> dict:
        return json.loads(self.encoded)


def read_cue_synthesis_input(raw: bytes, *, expected_hash: str) -> CueSynthesisInput:
    """Read only exact bytes pinned by the supervisor's immutable work command."""
    _hash(expected_hash)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REQUEST_BYTES or _digest(raw) != expected_hash:
        raise ReviewError("cue synthesis request bytes differ from their trusted pin")
    try:
        value = decode_review_json(raw.decode("utf-8"))
        required = {"schema_version", "kind", "job_id", "revision", "finding_id", "cue_id", "manifest_sha256",
                    "before_snapshot_sha256", "after_snapshot_sha256", "tts_input", "producer_pin", "config", "voice"}
        if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "review_cue_synthesis_input":
            raise ReviewError("unsupported cue synthesis request format")
        for name in ("job_id", "finding_id", "cue_id"):
            if not isinstance(value[name], str) or not _ID.fullmatch(value[name]): raise ReviewError("invalid synthesis request identity")
        if not isinstance(value["revision"], str) or int(_tick(value["revision"], "revision")) == 0:
            raise ReviewError("synthesis revision must be a positive canonical tick string")
        for name in ("manifest_sha256", "before_snapshot_sha256", "after_snapshot_sha256"): _hash(value[name])
        if any(not isinstance(value[name], dict) for name in ("config", "voice", "producer_pin")):
            raise ReviewError("cue synthesis pins must be objects")
        original = value["tts_input"]
        fields = {"segment_id", "source_utterance_id", "text", "normalized_text", "start", "end", "source_language", "target_language", "confidence"}
        if not isinstance(original, dict) or set(original) not in (fields, fields | {"render_window_end"}):
            raise ReviewError("invalid single-cue TTS input")
        segment = TtsInput(original["segment_id"], original["source_utterance_id"], original["text"],
                           _parse_point(original["start"], "start"), _parse_point(original["end"], "end"),
                           source_language=original["source_language"], target_language=original["target_language"],
                           confidence=original["confidence"], render_window_end=_parse_point(original["render_window_end"], "render_window_end") if "render_window_end" in original else None)
        config_value = dict(value["config"]); config_value["resource"] = ResourceProfile(**config_value["resource"])
        config = TtsConfig(**config_value); voice = VoiceProfile(**value["voice"])
        provenance_value = dict(value["producer_pin"])
        if "input_hash" in provenance_value: raise ReviewError("producer pin cannot substitute its own input hash")
        provenance_value["resource"] = ResourceProfile(**provenance_value["resource"])
        provenance = TtsProvenance(**provenance_value, input_hash=expected_hash)
        exact_producer = {key: item for key, item in provenance.to_dict().items() if key != "input_hash"}
        if (config.to_dict() != value["config"] or voice.to_dict() != value["voice"] or exact_producer != value["producer_pin"] or
            segment.segment_id != value["cue_id"] or segment.to_dict() != original or len(segment.text) > config.max_text_chars or
            provenance.voice_id != voice.voice_id or provenance.voice_version != voice.voice_version or provenance.voice_hash != voice.content_hash() or
            provenance.model_id != voice.model_id or provenance.model_version != voice.model_version or provenance.model_hash != voice.model_hash or
            provenance.config_hash != config.content_hash() or provenance.resource != config.resource or provenance.requested_profile != config.requested_profile):
            raise ReviewError("cue synthesis payload differs from its canonical pins")
        if _encode(value, MAX_REQUEST_BYTES) != raw: raise ReviewError("cue synthesis request encoding is not canonical")
        return CueSynthesisInput(raw, segment, provenance, config, voice)
    except (TypeError, KeyError, UnicodeError, ValueError) as error:
        raise ReviewError("cue synthesis request is malformed: " + str(error)) from error


def prepare_cue_synthesis(before: ReviewSnapshot, after: ReviewSnapshot, *,
                          manifest_sha256: str, original_input: TtsInput,
                          previous_provenance: TtsProvenance,
                          target_provenance: TtsProvenance,
                          voice: VoiceProfile, config: TtsConfig) -> CueSynthesisInput:
    """Bind one changed cue and keep every unrelated finding and producer pin.

    An absent voice/B1 path needs the separate subtitle regeneration adapter;
    this function never invents a default voice. Producer/model/config migration
    needs a separately admitted compatibility plan, not a silent TTS fallback.
    """
    _hash(manifest_sha256)
    _validate_cue_consistency(before); _validate_cue_consistency(after)
    if before.job_id != after.job_id or before.time_base != after.time_base or after.revision != before.revision + 1 or after.last_plan is None:
        raise ReviewError("cue synthesis requires the exact next correction revision")
    if len(before.findings) != len(after.findings):
        raise ReviewError("correction cannot add or remove findings")
    identifier = after.last_plan.finding_id
    old = next((item for item in before.findings if item.finding_id == identifier), None)
    new = next((item for item in after.findings if item.finding_id == identifier), None)
    if old is None or new is None or old.cue_id is None or old.start_ticks is None or new.voice is None:
        raise ReviewError("cue synthesis requires a mapped finding and an explicit assigned voice")
    stages = _correction_stages(translation_changed=old.translated_text != new.translated_text,
                                voice_changed=old.voice != new.voice)
    expected = RegenerationPlan(identifier, stages, ("asr", "ocr"), tuple("replacement:" + stage for stage in stages))
    if after.last_plan != expected:
        raise ReviewError("correction has the wrong downstream regeneration plan")
    for prior, updated in zip(before.findings, after.findings):
        if prior.cue_id == old.cue_id:
            admitted = replace(prior, translated_text=new.translated_text, voice=new.voice)
        else:
            admitted = prior
        if admitted != updated:
            raise ReviewError("correction changed source mapping, provenance or an unrelated cue")
    base = Fraction(int(before.time_base[0]), int(before.time_base[1]))
    if (original_input.segment_id != old.cue_id or original_input.text != old.translated_text or
        Fraction(original_input.start.ticks * original_input.start.time_base.numerator, original_input.start.time_base.denominator) != int(old.start_ticks) * base or
        Fraction(original_input.end.ticks * original_input.end.time_base.numerator, original_input.end.time_base.denominator) != int(old.end_ticks) * base):
        raise ReviewError("original synthesis input differs from the canonical cue")
    if len(new.translated_text) > config.max_text_chars:
        raise ReviewError("corrected cue exceeds the pinned TTS text limit")
    if voice.voice_id != new.voice or target_provenance.voice_id != voice.voice_id or target_provenance.voice_version != voice.voice_version or target_provenance.voice_hash != voice.content_hash():
        raise ReviewError("selected voice does not match the verified voice profile")
    if (target_provenance.model_id != voice.model_id or target_provenance.model_version != voice.model_version or
        target_provenance.model_hash != voice.model_hash or target_provenance.config_hash != config.content_hash() or
        target_provenance.resource != config.resource or target_provenance.requested_profile != config.requested_profile):
        raise ReviewError("TTS model/config/resource pins do not match")
    previous = previous_provenance.to_dict(); target = target_provenance.to_dict()
    if previous_provenance.voice_id != old.voice:
        raise ReviewError("previous TTS voice does not match the authoritative cue")
    mutable = {"voice_id", "voice_version", "voice_hash", "input_hash"}
    if {key: value for key, value in previous.items() if key not in mutable} != {key: value for key, value in target.items() if key not in mutable}:
        raise ReviewError("producer/model/config migration needs explicit admission")
    if old.voice == new.voice and any(previous[key] != target[key] for key in ("voice_version", "voice_hash")):
        raise ReviewError("unchanged voice cannot silently change its version or profile")
    # dataclasses.replace otherwise retains normalized_text from the old cue,
    # which would synthesize the prior words despite the corrected text field.
    segment = replace(original_input, text=new.translated_text, normalized_text=None)
    producer_pin = {key: value for key, value in target.items() if key != "input_hash"}
    value = {"schema_version": 1, "kind": "review_cue_synthesis_input", "job_id": before.job_id,
             "revision": str(after.revision), "finding_id": identifier, "cue_id": old.cue_id,
             "manifest_sha256": manifest_sha256,
             "before_snapshot_sha256": _digest(_encode(before.to_dict(), MAX_JSON_BYTES)),
             "after_snapshot_sha256": _digest(_encode(after.to_dict(), MAX_JSON_BYTES)),
             "tts_input": segment.to_dict(), "producer_pin": producer_pin,
             "config": config.to_dict(), "voice": voice.to_dict()}
    encoded = _encode(value, MAX_REQUEST_BYTES)
    return CueSynthesisInput(encoded, segment, replace(target_provenance, input_hash=_digest(encoded)), config, voice)
