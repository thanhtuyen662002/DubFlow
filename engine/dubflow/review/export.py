"""Read-only adapter from a pinned completed export to production review.

The supervisor supplies the trusted manifest pin. Metadata loading does not
scan a long MP4 or mutate prior output. Audio previews are verified on demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import wave

from engine.dubflow.tts import parse_tts_json, TtsError
from engine.dubflow.worker.export_publication import plain, PublicationError
from .production import ReviewError, ReviewSnapshot, _tick, build_review_snapshot
from .wire import decode_review_json

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_BYTES = 64 * 1024 * 1024
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _hash(value: object) -> str:
    if not isinstance(value, str) or HASH.fullmatch(value) is None:
        raise ReviewError("artifact hash is invalid")
    return value


def _controlled(path: object, output: Path) -> Path:
    if not isinstance(path, (str, Path)):
        raise ReviewError("artifact path is invalid")
    candidate = Path(path).absolute()
    try:
        plain(candidate)
    except PublicationError as error:
        raise ReviewError(str(error)) from error
    candidate = candidate.resolve()
    if not candidate.is_relative_to(output.resolve()) or not candidate.is_file():
        raise ReviewError("review artifact is outside output or unavailable")
    return candidate


def _json_bytes(path: Path, expected: str, expected_size: object = None) -> bytes:
    _hash(expected)
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ReviewError("review input exceeds JSON byte limit")
    if expected_size is not None and (type(expected_size) is not int or expected_size != len(raw)):
        raise ReviewError("review artifact size changed")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected:
        raise ReviewError("review artifact hash changed")
    return raw


def _point_seconds(value: Mapping[str, Any]) -> Fraction:
    base = value["time_base"]
    return Fraction(int(value["ticks"]) * int(base["numerator"]), int(base["denominator"]))


@dataclass(frozen=True)
class BoundReview:
    snapshot: ReviewSnapshot
    source_pins: tuple[tuple[str, str, str], ...]
    output_root: str
    tts_provenance_json: str | None
    previews_json: str


def build_review_from_export(manifest_path: str | Path, *, expected_job_id: str,
                             expected_manifest_sha256: str, revision: int = 0) -> BoundReview:
    manifest_path = Path(manifest_path).absolute()
    output = manifest_path.parent
    manifest_path = _controlled(manifest_path, output)
    output = manifest_path.parent
    manifest = decode_review_json(_json_bytes(manifest_path, expected_manifest_sha256).decode("utf-8"))
    if not isinstance(manifest, Mapping) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 or manifest.get("kind") != "dubflow_localization_job" or manifest.get("job_id") != expected_job_id:
        raise ReviewError("export manifest version/kind/job does not match")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ReviewError("export artifact descriptors are missing")
    pins = [("job_manifest", str(manifest_path), expected_manifest_sha256)]

    def load(role: str) -> bytes:
        descriptor = artifacts.get(role)
        if not isinstance(descriptor, Mapping) or set(descriptor) != {"path", "sha256", "size_bytes"}:
            raise ReviewError("required review artifact descriptor is malformed: " + role)
        path = _controlled(descriptor["path"], output)
        raw = _json_bytes(path, descriptor["sha256"], descriptor["size_bytes"])
        pins.append((role, str(path), descriptor["sha256"]))
        return raw

    timeline = decode_review_json(load("editable_timeline").decode("utf-8"))
    qc = decode_review_json(load("qc_report").decode("utf-8"))
    if not isinstance(timeline, Mapping) or type(timeline.get("schema_version")) is not int or timeline["schema_version"] != 1 or not isinstance(timeline.get("cues"), list):
        raise ReviewError("canonical export timeline is unsupported")
    if not isinstance(qc, Mapping) or type(qc.get("schema_version")) is not int or qc["schema_version"] != 1 or qc.get("status") != "passed" or not isinstance(qc.get("warnings"), list):
        raise ReviewError("export QC is unavailable or not passed")
    base = timeline.get("time_base")
    if not isinstance(base, Mapping) or set(base) != {"numerator", "denominator"}:
        raise ReviewError("canonical timeline time_base is missing")
    time_base = ReviewSnapshot(expected_job_id, revision, (), time_base=(_tick(base["numerator"], "numerator"), _tick(base["denominator"], "denominator"))).time_base
    duration = Fraction(int(_tick(timeline.get("duration_ticks"), "duration_ticks")) * int(time_base[0]), int(time_base[1]))
    cues = {}
    for raw in timeline["cues"]:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("cue_id"), str) or raw["cue_id"] in cues:
            raise ReviewError("canonical cue identity is invalid or duplicated")
        start = int(_tick(raw.get("start_ms"), "start_ms"))
        end = int(_tick(raw.get("end_ms"), "end_ms"))
        if end <= start or Fraction(end, 1000) > duration:
            raise ReviewError("canonical cue lies outside source timeline")
        cues[raw["cue_id"]] = dict(raw)
    warnings = list(qc["warnings"])
    previews = {}
    provenance = None
    if "tts_document" in artifacts:
        try:
            tts = parse_tts_json(load("tts_document"))
        except TtsError as error:
            raise ReviewError("export TTS document is invalid: " + str(error)) from error
        if not set(tts["source_segment_ids"]) <= set(cues):
            raise ReviewError("TTS source IDs do not belong to canonical cues")
        provenance = json.dumps(tts["provenance"], sort_keys=True)
        for cue_id in tts["source_segment_ids"]:
            cues[cue_id]["voice_id"] = tts["provenance"]["voice_id"]
        for artifact in tts["artifacts"]:
            cue = cues[artifact["segment_id"]]
            if _point_seconds(artifact["slot_start"]) != Fraction(cue["start_ms"], 1000) or _point_seconds(artifact["slot_end"]) != Fraction(cue["end_ms"], 1000):
                raise ReviewError("TTS slot differs from canonical cue range")
            # The pinned descriptor supplies the controlled path. Playback must
            # call validate_review_preview to verify the current PCM bytes.
            try:
                path = _controlled(artifact["path"], output)
                cue["output_preview"] = str(path)
                previews[artifact["segment_id"]] = artifact
            except (ReviewError, OSError) as error:
                warnings.append({"code": "TTS_PREVIEW_UNAVAILABLE", "message": str(error), "cue_id": artifact["segment_id"]})
        for failure in tts["failures"]:
            warnings.append({"code": "TTS_FAILURE", "message": failure["code"] + ": " + failure["condition"], "cue_id": failure["segment_id"]})
    snapshot = build_review_snapshot(expected_job_id, cues=tuple(cues.values()), warnings=warnings,
                                     revision=revision, time_base=time_base)
    return BoundReview(snapshot, tuple(pins), str(output), provenance, json.dumps(previews, sort_keys=True))


def validate_review_preview(review: BoundReview, cue_id: str) -> Path:
    artifact = json.loads(review.previews_json).get(cue_id)
    if not isinstance(artifact, Mapping):
        raise ReviewError("cue has no validated TTS preview descriptor")
    path = _controlled(artifact["path"], Path(review.output_root))
    if path.stat().st_size > MAX_PREVIEW_BYTES:
        raise ReviewError("TTS preview exceeds bounded playback size")
    frames = 0
    try:
        with path.open("rb") as source:
            # TTS content_hash covers the entire WAV, including its header.
            digest = hashlib.sha256()
            total = 0
            while payload := source.read(131072):
                total += len(payload)
                if total > MAX_PREVIEW_BYTES:
                    raise ReviewError("TTS preview exceeds bounded playback size")
                digest.update(payload)
            if "sha256:" + digest.hexdigest() != artifact["content_hash"]:
                raise ReviewError("TTS preview WAV content changed")
            source.seek(0)
            with wave.open(source, "rb") as reader:
                if reader.getcomptype() != "NONE" or reader.getnchannels() != artifact["channels"] or reader.getsampwidth() != 2 or reader.getframerate() != artifact["sample_rate"] or reader.getnframes() != artifact["frame_count"]:
                    raise ReviewError("TTS preview PCM header changed")
                while payload := reader.readframes(32768):
                    frames += len(payload) // (2 * artifact["channels"])
                    if frames * 2 * artifact["channels"] > MAX_PREVIEW_BYTES:
                        raise ReviewError("TTS preview exceeds bounded playback size")
    except (OSError, EOFError, wave.Error) as error:
        raise ReviewError("TTS preview is unreadable: " + str(error)) from error
    if frames != artifact["frame_count"]:
        raise ReviewError("TTS preview PCM frame count changed")
    return path
