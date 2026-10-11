"""Bounded production Whisper decode and private, source-bound chunk artifacts.

Uses the public integer ChunkPlanner. Private word evidence retains zero-length
SDK words, which cannot be passed through the public positive Word intervals.
The caller owns checkpoint publication and protocol events; no SQLite access.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any, Callable
import unicodedata
import wave

from engine.dubflow.asr import AdapterConfig, AudioChunk, ChunkPlanner, TimeBase, TimeInterval, TimePoint
from engine.dubflow.worker.whisper_cues import ASR_RECIPE, SAMPLE_RATE, MAX_GAP_SAMPLES, AlignedCue, cue_from_words, segment_cues
from engine.dubflow.worker.export_publication import plain

CORE_SAMPLES = 120 * SAMPLE_RATE
OVERLAP_SAMPLES = 10 * SAMPLE_RATE
MAX_RECORD_BYTES = 8 * 1024 * 1024


class ChunkAsrError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


def _digest(value: Any) -> str:
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def plan_chunks(total_samples: int, input_hash: str) -> tuple[AudioChunk, ...]:
    if type(total_samples) is not int or total_samples <= 0:
        raise ChunkAsrError("ASR_AUDIO_INVALID", "decoded audio must contain positive sample frames")
    # Balance cores so a recording just above a boundary has no tiny tail
    # inference containing only earlier context and silence.
    count = (total_samples + CORE_SAMPLES - 1) // CORE_SAMPLES
    width = max(2, (total_samples + count - 1) // count)
    config = AdapterConfig(width, min(OVERLAP_SAMPLES, width - 1), max_attempts=1)
    base = TimeBase(1, SAMPLE_RATE)
    return ChunkPlanner(config).plan(TimeInterval(TimePoint(0, base), TimePoint(total_samples, base)),
                                     input_hash=input_hash, audio_ref="analysis.wav", sample_rate=SAMPLE_RATE)


def _shape(chunk: AudioChunk) -> dict[str, Any]:
    return {"chunk_id": chunk.chunk_id, "index": chunk.index,
            "core_samples": [chunk.core.start.ticks, chunk.core.end.ticks],
            "window_samples": [chunk.window.start.ticks, chunk.window.end.ticks]}


def _probability(value: Any) -> float | None:
    return float(value) if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1 else None


def _cached(path: Path, chunk: AudioChunk, identity: str, language: str | None, validate: Callable) -> dict[str, Any] | None:
    plain(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw)
        payload = record["payload"]
        if (set(record) != {"sha256", "payload"} or record["sha256"] != _digest(payload)
                or set(payload) != {"schema_version", "recipe", "identity", "chunk", "decode_language", "observed_language", "language_probability", "cues"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or payload["recipe"] != ASR_RECIPE or payload["identity"] != identity
                or payload["chunk"] != _shape(chunk) or payload["decode_language"] != language
                or not isinstance(payload["observed_language"], str) or not 1 <= len(payload["observed_language"]) <= 16
                or (language is not None and payload["observed_language"] != language)
                or (payload["language_probability"] is not None and _probability(payload["language_probability"]) is None)
                or not isinstance(payload["cues"], list)):
            return None
        cues = tuple(AlignedCue(**cue) for cue in payload["cues"])
        if not validate(cues):
            return None
        for cue in cues:
            if not chunk.window.start.ticks <= cue.evidence["start_sample"] < cue.evidence["end_sample"] <= chunk.window.end.ticks:
                return None
        return payload
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _window_wav(source: Path, target: Path, chunk: AudioChunk) -> None:
    plain(target)
    start, end = chunk.window.start.ticks, chunk.window.end.ticks
    with wave.open(str(source), "rb") as reader, wave.open(str(target), "wb") as writer:
        writer.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        reader.setpos(start)
        remaining = end - start
        while remaining:
            count = min(remaining, 64_000)
            block = reader.readframes(count)
            if len(block) != count * 2:
                raise ChunkAsrError("ASR_AUDIO_INVALID", "analysis audio changed or ended during bounded decode")
            writer.writeframesraw(block)
            remaining -= count


def _overlap(left: dict, right: dict) -> bool:
    return left["start_sample"] < right["end_sample"] and right["start_sample"] < left["end_sample"]


def _word_text(word: dict) -> str:
    return unicodedata.normalize("NFC", word["text"]).strip().casefold()


def reconcile(chunks: tuple[AudioChunk, ...], payloads: list[dict]) -> tuple[tuple[AlignedCue, ...], list[dict]]:
    groups: list[dict] = []
    reviews: list[dict] = []
    frontier: list[dict] = []
    for chunk, payload in zip(chunks, payloads, strict=True):
        current_frontier: list[dict] = []
        for item in payload["cues"]:
            cue = AlignedCue(**item)
            selected = []
            anchor = next(((word["start_sample"] + word["end_sample"]) // 2 for word in cue.evidence["words"] if word["end_sample"] > word["start_sample"]), None)
            for word in cue.evidence["words"]:
                if word["end_sample"] > word["start_sample"]:
                    anchor = (word["start_sample"] + word["end_sample"]) // 2
                point = anchor if anchor is not None else word["start_sample"]
                if chunk.core.start.ticks <= point < chunk.core.end.ticks:
                    selected.append(dict(word))
            if not selected or max(word["end_sample"] for word in selected) <= min(word["start_sample"] for word in selected):
                continue
            origin = {"chunk_id": chunk.chunk_id, "cue_id": cue.cue_id, "raw_segment_scores": cue.evidence["raw_segment_scores"]}
            group = {"words": selected, "origins": [origin], "raw": cue.evidence["raw_segment_scores"],
                     "reason": cue.evidence["review_reason"], "indices": {chunk.index}}
            match = None
            for previous in reversed(frontier):
                if chunk.index - 1 not in previous["indices"] or chunk.index in previous["indices"]:
                    continue
                duplicates = [word for word in selected if any(_word_text(word) == _word_text(old) and _overlap(word, old) for old in previous["words"])]
                if duplicates and previous["reason"] == group["reason"]:
                    match = previous
                    group["words"] = [word for word in selected if word not in duplicates]
                    break
                if any(_overlap(word, old) for word in selected for old in previous["words"]):
                    reviews.append({"reason": "chunk-boundary-disagreement", "left": previous["origins"], "right": origin})
            if match is None:
                groups.append(group)
                current_frontier.append(group)
            else:
                match["words"].extend(group["words"])
                match["words"].sort(key=lambda word: (word["start_sample"], word["end_sample"]))
                match["origins"].append(origin)
                match["indices"].add(chunk.index)
                current_frontier.append(match)
        frontier = current_frontier
    output = []
    for group in groups:
        split: list[dict] = []
        end = -1
        for word in group["words"]:
            if split and word["start_sample"] - end > MAX_GAP_SAMPLES:
                output.append(cue_from_words(split, group["raw"], review_reason=group["reason"], origins=group["origins"]))
                split = []
            split.append(word)
            end = max(end, word["end_sample"])
        if split:
            output.append(cue_from_words(split, group["raw"], review_reason=group["reason"], origins=group["origins"]))
    output.sort(key=lambda cue: (cue.evidence["start_sample"], cue.evidence["end_sample"], cue.cue_id))
    if len({cue.cue_id for cue in output}) != len(output):
        raise ChunkAsrError("ASR_CUE_ID_COLLISION", "ASR chunk reconciliation produced duplicate source identity")
    return tuple(output), reviews


@dataclass(frozen=True)
class BoundedTranscript:
    cues: tuple[AlignedCue, ...]
    language: str
    probability: float | None
    chunks: tuple[dict, ...]
    reviews: tuple[dict, ...]
    identity: str
    binding: dict[str, Any]


def transcribe_bounded(audio_path: Path, *, total_samples: int, audio_hash: str, language: str | None,
                       model_binding: str, model_factory: Callable, chunks_dir: Path,
                       write_json: Callable, validate_cues: Callable, on_chunk: Callable | None = None) -> BoundedTranscript:
    binding = {"audio": audio_hash, "total_samples": total_samples, "language": language,
               "model_binding": model_binding, "recipe": ASR_RECIPE,
               "core_samples": CORE_SAMPLES, "overlap_samples": OVERLAP_SAMPLES}
    identity = _digest(binding)
    chunks = plan_chunks(total_samples, identity)
    chunks_dir = chunks_dir / identity.removeprefix("sha256:")
    plain(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    model = None
    pinned_language = language
    probability = None
    payloads = []
    summaries = []
    for chunk in chunks:
        path = chunks_dir / (chunk.chunk_id + ".json")
        payload = _cached(path, chunk, identity, pinned_language, validate_cues)
        if payload is None:
            if model is None:
                model = model_factory()
            window_path = chunks_dir / (chunk.chunk_id + ".wav")
            try:
                _window_wav(audio_path, window_path, chunk)
                segments, info = model.transcribe(str(window_path), language=pinned_language, beam_size=5, vad_filter=True, word_timestamps=True)
                cues = tuple(cue for segment in segments for cue in segment_cues(segment, chunk.window.end.ticks - chunk.window.start.ticks, sample_offset=chunk.window.start.ticks))
                if not validate_cues(cues):
                    raise ChunkAsrError("ASR_HYPOTHESIS_INVALID", "ASR chunk word evidence does not match source cues")
                observed = getattr(info, "language", None)
                if not isinstance(observed, str) or not 1 <= len(observed) <= 16:
                    raise ChunkAsrError("SOURCE_LANGUAGE_UNRESOLVED", "ASR chunk did not report a supported language identity")
                if pinned_language is not None and observed != pinned_language:
                    raise ChunkAsrError("SOURCE_LANGUAGE_MISMATCH", "ASR chunk language disagrees with the requested/pinned language")
                payload = {"schema_version": 1, "recipe": ASR_RECIPE, "identity": identity, "chunk": _shape(chunk),
                           "decode_language": pinned_language, "observed_language": observed,
                           "language_probability": _probability(getattr(info, "language_probability", None)),
                           "cues": [asdict(cue) for cue in cues]}
                write_json(path, {"sha256": _digest(payload), "payload": payload})
            finally:
                window_path.unlink(missing_ok=True)
        owned_count = sum(any(word["end_sample"] > word["start_sample"] and chunk.core.start.ticks <= (word["start_sample"] + word["end_sample"]) // 2 < chunk.core.end.ticks
                              for word in item["evidence"]["words"]) for item in payload["cues"])
        if pinned_language is None and owned_count:
            pinned_language = payload["observed_language"]
            probability = payload["language_probability"]
        payloads.append(payload)
        artifact_hash = "sha256:" + sha256(path.read_bytes()).hexdigest()
        summaries.append({**_shape(chunk), "artifact_hash": artifact_hash,
                          "decode_language": payload["decode_language"], "observed_language": payload["observed_language"],
                          "language_probability": payload["language_probability"], "owned_cue_count": owned_count})
        if on_chunk is not None:
            on_chunk(chunk.index + 1, len(chunks), chunk.chunk_id, artifact_hash)
    cues, reviews = reconcile(chunks, payloads)
    if not cues or pinned_language is None:
        raise ChunkAsrError("ASR_EMPTY", "ASR produced no source-owned speech groups")
    return BoundedTranscript(cues, pinned_language, probability if language is None else None, tuple(summaries), tuple(reviews), identity, binding)


def summary_matches(evidence: Any) -> bool:
    """Validate source-bound plan/coverage before admitting a full transcript."""
    try:
        if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 2
                or evidence.get("chunking") != {"core_samples": CORE_SAMPLES, "overlap_samples": OVERLAP_SAMPLES, "ownership": "word-midpoint-v1"}
                or not isinstance(evidence.get("chunks"), list) or not isinstance(evidence.get("boundary_reviews"), list)):
            return False
        binding = evidence["chunk_binding"]
        if (not isinstance(binding, dict) or set(binding) != {"audio", "total_samples", "language", "model_binding", "recipe", "core_samples", "overlap_samples"}
                or binding["audio"] != evidence["analysis_audio_sha256"] or type(binding["total_samples"]) is not int or binding["total_samples"] != evidence["total_samples"]
                or binding["recipe"] != ASR_RECIPE or binding["core_samples"] != CORE_SAMPLES or binding["overlap_samples"] != OVERLAP_SAMPLES
                or not isinstance(binding["model_binding"], str) or not binding["model_binding"]
                or evidence["chunk_identity"] != _digest(binding)):
            return False
        planned = plan_chunks(evidence["total_samples"], evidence["chunk_identity"])
        if len(planned) != len(evidence["chunks"]):
            return False
        language = binding["language"]
        probability = None
        for chunk, record in zip(planned, evidence["chunks"], strict=True):
            if any(record[key] != value for key, value in _shape(chunk).items()):
                return False
            if (type(record["index"]) is not int or any(type(value) is not int for key in ("core_samples", "window_samples") for value in record[key])
                    or not isinstance(record["artifact_hash"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["artifact_hash"])
                    or record["decode_language"] != language or type(record["owned_cue_count"]) is not int or record["owned_cue_count"] < 0
                    or not isinstance(record["observed_language"], str) or not 1 <= len(record["observed_language"]) <= 16
                    or (language is not None and record["observed_language"] != language)
                    or (record["language_probability"] is not None and _probability(record["language_probability"]) is None)):
                return False
            if language is None and record["owned_cue_count"]:
                language = record["observed_language"]
                probability = record["language_probability"]
        if evidence["language_pin"] != {"language": language, "probability": probability}:
            return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
