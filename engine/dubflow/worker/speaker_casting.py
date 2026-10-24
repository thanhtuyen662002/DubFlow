"""Opt-in audio speaker casting and its private generation identity."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Sequence

from engine.dubflow.diarization import ProductionDiarizationConfig, diarize_wav
from engine.dubflow.tts import vieneu
from .b2_audio import B2AudioError


RECIPE = "speaker-duration-order-cycle-v1"


def validate_speaker_voices(value, app_root: Path) -> tuple[str, ...]:
    if type(value) not in (list, tuple) or not 2 <= len(value) <= 64 or any(
        type(item) is not str or re.fullmatch(r"[a-z0-9-]{1,96}", item) is None for item in value
    ) or len(set(value)) != len(value):
        raise B2AudioError("SPEAKER_VOICES_INVALID", "speaker_voices requires 2 to 64 distinct approved preset identifiers")
    profile = vieneu._read_json(Path(app_root) / vieneu.PROFILE_PATH)
    approved = {item["voice_id"] for item in vieneu.voice_choices(profile)}
    if not set(value) <= approved:
        raise B2AudioError("VOICE_ID_UNKNOWN", "speaker voice is absent from the approved VieNeu catalog")
    return tuple(value)


def speaker_recipe_identity(app_root: Path, voices: Sequence[str]) -> str:
    """Only enabled generations depend on the new producer/setting."""
    directory = Path(__file__).parents[1]
    paths = (Path(__file__), Path(__file__).with_name("speaker_audio.py"),
        directory / "diarization/production.py", directory / "diarization/baseline.py",
        Path(app_root) / "models/manifests/production-speaker-v1.json")
    value = {"recipe": RECIPE, "voices": list(voices), "config": asdict(ProductionDiarizationConfig()),
        "sources": [sha256(path.read_bytes()).hexdigest() for path in paths]}
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assign_speaker_voices(segments, voices: Sequence[str]) -> dict[str, str]:
    """Rank by speaking ticks, then first occurrence and stable cluster ID."""
    intervals: dict[str, list[tuple[int, int]]] = {}
    for segment in segments:
        if segment.role == "speaker":
            for identifier in segment.speaker_cluster_ids:
                intervals.setdefault(identifier, []).append((segment.start_ticks, segment.end_ticks))
    ranks = []
    for identifier, ranges in intervals.items():
        total, prior_end = 0, -1
        for start, end in sorted(ranges):
            total += max(0, end - max(start, prior_end))
            prior_end = max(prior_end, end)
        ranks.append((-total, min(start for start, _end in ranges), identifier))
    return {identifier: voices[index % len(voices)] for index, (_ticks, _start, identifier) in enumerate(sorted(ranks))}


def speaker_plan(audio_path: Path | None, job_id: str, cues, voices: Sequence[str], primary_voice: str) -> dict:
    """Retain uncertainty and failure as evidence while preserving usable speech."""
    warnings = []
    evidence = None
    segments = ()
    reason = None
    try:
        if audio_path is None:
            raise B2AudioError("AUDIO_STREAM_MISSING", "source has no analysis audio")
        report = diarize_wav(audio_path, job_id)
        evidence = report.to_dict()
        segments = report.result.segments
        warnings.extend(report.warnings)
    except Exception as error:
        reason = getattr(error, "code", "DIARIZATION_FAILED")
        evidence = {"failure": {"code": reason, "condition": "source diarization unavailable"}}
    mapping = assign_speaker_voices(segments, voices)
    if reason is None and len(mapping) < 2:
        reason = "SINGLE_SPEAKER" if mapping else "NO_SPEAKERS"
    if reason is not None:
        mapping = {identifier: primary_voice for identifier in mapping}
        warnings.append("SPEAKER_DIARIZATION_FALLBACK: " + reason + "; using primary voice " + primary_voice)
    cue_voices, cue_speakers = {}, {}
    for cue in cues:
        matches = sorted(((max(0, min(cue.end_ms, item.end_ticks) - max(cue.start_ms, item.start_ticks)), item)
            for item in segments), key=lambda pair: (pair[0], pair[1].confidence, pair[1].segment_id), reverse=True)
        winner = matches[0][1] if matches and matches[0][0] > 0 else None
        identifier = winner.speaker_cluster_ids[0] if winner is not None and winner.role == "speaker" else None
        cue_voices[cue.cue_id] = mapping.get(identifier, primary_voice)
        cue_speakers[cue.cue_id] = {"speaker_cluster_id": identifier,
            "confidence": str(winner.confidence) if winner is not None else "0"}
        if identifier is None and reason is None:
            warnings.append("SPEAKER_CUE_UNRESOLVED: " + cue.cue_id + "; using primary voice")
    return {"schema_version": 1, "recipe": RECIPE, "requested_voices": list(voices),
        "primary_voice_id": primary_voice, "speaker_to_voice": mapping, "cue_to_voice": cue_voices,
        "cue_speakers": cue_speakers, "fallback_reason": reason, "diarization": evidence,
        "warnings": warnings, "confidence_calibrated": False}


def plan_identity(plan: dict) -> str:
    # Timings/resource measurements in the report are evidence, not identity.
    evidence = plan["diarization"]
    stable = {key: value for key, value in plan.items() if key not in {"diarization", "voice_documents"}}
    if "result" in evidence:
        stable["diarization"] = {"audio_hash": evidence["audio"]["content_hash"], "backend": evidence["backend"],
            "segments": evidence["result"]["segments"], "vad": evidence["vad"]}
    else:
        stable["diarization"] = evidence
    return sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
