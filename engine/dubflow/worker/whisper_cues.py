"""Word-aligned production ASR cues and uncalibrated model evidence.

Normalize SDK seconds once to decoded-source sample positions. Durable cue
boundaries and identity use those integers; floats never identify the cue.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
import math
from numbers import Real
from typing import Any

from engine.dubflow.asr import TimeBase, TimePoint, map_sample_interval

ASR_RECIPE = "faster-whisper-1.2.1-bounded-word-gap-v4"
SAMPLE_RATE = 16_000
MAX_GAP_SAMPLES = SAMPLE_RATE
MAX_WORD_SAMPLES = 5 * SAMPLE_RATE // 2
MAX_DRIFT_CUE_SAMPLES = 15 * SAMPLE_RATE
DRIFT_REVIEW_REASON = "word-alignment-drift"


@dataclass(frozen=True)
class AlignedCue:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    evidence: dict[str, Any]


def cue_from_words(words: list[dict[str, Any]], raw_scores: dict[str, Any], *, review_reason: str | None = None, origins: list[dict[str, Any]] | None = None, alignment_drift: dict[str, Any] | None = None) -> AlignedCue:
    """Rebuild identity from integer source positions after boundary reconciliation."""
    start = min(word["start_sample"] for word in words)
    end = max(word["end_sample"] for word in words)
    if end <= start:
        raise ValueError("a speech group must have a positive source interval")
    value = "".join(word["text"] for word in words).strip()
    interval = map_sample_interval(TimePoint(0, TimeBase(1, 1000)), start, end, SAMPLE_RATE)
    probabilities = [word["probability"] for word in words]
    available = all(probability is not None for probability in probabilities)
    confidence = min(probability for probability in probabilities if probability is not None) if available else 0.0
    evidence = {
        "recipe": ASR_RECIPE,
        "timing_basis": "drift-fallback" if alignment_drift is not None else "segment-fallback" if review_reason in {"word-alignment-unavailable", "word-alignment-invalid"} else "word-alignment",
        "confidence_basis": "minimum-word-probability-uncalibrated" if available else "unavailable",
        "review_reason": review_reason,
        "start_sample": start, "end_sample": end, "sample_rate": SAMPLE_RATE,
        "raw_segment_scores": raw_scores, "words": words,
    }
    if origins is not None:
        evidence["chunk_origins"] = origins
    if alignment_drift is not None:
        evidence["alignment_drift"] = alignment_drift
    identity = f"asr-{start}-{end}-{sha256(value.encode()).hexdigest()[:12]}"
    return AlignedCue(identity, interval.start.ticks, interval.end.ticks, value, confidence, evidence)


def _score(value: Any) -> float | None:
    if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return None


def _sample(value: Any, *, end: bool = False) -> int:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid ASR boundary")
    return int((Decimal(str(value)) * SAMPLE_RATE).to_integral_value(rounding=ROUND_CEILING if end else ROUND_FLOOR))


def _terminal(text: str) -> bool:
    return text.rstrip().rstrip("\"'’”」』）)]}").endswith(tuple(".!?。！？…"))


def _orphan_tail(previous: list[dict[str, Any]], tail: list[dict[str, Any]]) -> bool:
    before = "".join(word["text"] for word in previous)
    text = "".join(word["text"] for word in tail)
    first = next((character for character in text if character.isalpha()), "")
    count = sum(any(character.isalnum() for character in word["text"]) for word in tail)
    # Uncased scripts (including Chinese) can continue a sentence. An explicit
    # capital or a completed previous sentence always preserves the pause.
    return 0 < count <= 3 and bool(first) and not first.isupper() and _terminal(text) and not _terminal(before)


def _long_word(word: dict[str, Any]) -> bool:
    return word["end_sample"] - word["start_sample"] > MAX_WORD_SAMPLES


def _drift_fallback(words: list[dict[str, Any]], raw: dict[str, Any], segment_start: int, segment_end: int) -> AlignedCue | None:
    start = max(segment_start, min(word["start_sample"] for word in words))
    end = min(segment_end, max(word["end_sample"] for word in words))
    basis = "segment-bounded"
    if end - start > MAX_DRIFT_CUE_SAMPLES:
        # Keep observed timing up to and including the first stretched word:
        # its span is where the sentence was actually spoken, while the
        # orphaned tail's late timing is the drift. A bare pre-drift prefix
        # (e.g. 0.5 s for "Are you hurt?") is too short to read or dub.
        prefix: list[dict[str, Any]] = []
        previous_end = -1
        for word in words:
            if prefix and word["start_sample"] - previous_end > MAX_GAP_SAMPLES:
                break
            prefix.append(word)
            previous_end = max(previous_end, word["end_sample"])
            if _long_word(word):
                break
        if not prefix:
            return None
        start = max(segment_start, min(word["start_sample"] for word in prefix))
        end = min(segment_end, max(word["end_sample"] for word in prefix))
        basis = "through-drifted-word"
    if not 0 < end - start <= MAX_DRIFT_CUE_SAMPLES:
        return None
    fallback = [{"text": "".join(word["text"] for word in words), "start_sample": start, "end_sample": end, "probability": None}]
    return cue_from_words(fallback, raw, review_reason=DRIFT_REVIEW_REASON,
                          alignment_drift={"segment_start_sample": segment_start, "segment_end_sample": segment_end,
                                           "timing_basis": basis, "words": words})


def drift_fallback_matches(item: dict[str, Any], total_samples: int) -> bool:
    """Recompute fallback timing from retained observations on checkpoint admission."""
    drift = item.get("alignment_drift")
    if not isinstance(drift, dict) or set(drift) != {"segment_start_sample", "segment_end_sample", "timing_basis", "words"}:
        return False
    start, end = drift["segment_start_sample"], drift["segment_end_sample"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= total_samples:
        return False
    words = drift["words"]
    if not isinstance(words, list) or not 1 <= len(words) <= 16_384:
        return False
    groups: list[list[dict[str, Any]]] = [[]]
    previous_start = previous_end = -1
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("text"), str) or not word["text"].strip():
            return False
        word_start, word_end = word.get("start_sample"), word.get("end_sample")
        if type(word_start) is not int or type(word_end) is not int or not 0 <= word_start <= word_end <= total_samples or word_start < previous_start:
            return False
        if word.get("probability") is not None and _score(word["probability"]) is None:
            return False
        if groups[-1] and word_start - previous_end > MAX_GAP_SAMPLES:
            groups.append([])
        groups[-1].append(word)
        previous_start, previous_end = word_start, max(previous_end, word_end)
    if len(groups) != 2 or not _orphan_tail(*groups):
        return False
    expected = _drift_fallback(words, item["raw_segment_scores"], start, end)
    return expected is not None and all(item.get(key) == expected.evidence[key] for key in
                                        ("start_sample", "end_sample", "words", "review_reason", "timing_basis", "alignment_drift"))


def segment_cues(segment: Any, total_samples: int, *, sample_offset: int = 0) -> tuple[AlignedCue, ...]:
    """Split pauses without dropping words or fabricating certainty.

    Missing/malformed word alignment retains the original segment text and
    bounds as an explicit review fallback with unavailable confidence.
    """
    if type(total_samples) is not int or total_samples <= 0:
        raise ValueError("decoded audio must contain positive sample frames")
    if type(sample_offset) is not int or sample_offset < 0:
        raise ValueError("sample offset must be a non-negative source integer")
    text = str(getattr(segment, "text", "")).strip()
    if not text:
        return ()
    if len(text) > 16_384:
        raise ValueError("ASR segment text exceeds the cue limit")
    segment_start = _sample(segment.start)
    segment_end = min(_sample(segment.end, end=True), total_samples)
    if segment_start >= segment_end:
        raise ValueError("ASR segment lies outside decoded audio")
    raw = {}
    for key in ("avg_logprob", "no_speech_prob", "compression_ratio", "temperature"):
        value = getattr(segment, key, None)
        raw[key] = float(value) if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) else None
    words = getattr(segment, "words", None)
    groups: list[list[tuple[str, int, int, float | None]]] = []
    reason = None
    if not isinstance(words, (list, tuple)) or not words:
        reason = "word-alignment-unavailable"
    else:
        current: list[tuple[str, int, int, float | None]] = []
        previous_start = previous_end = -1
        try:
            if len(words) > 16_384:
                raise ValueError("too many aligned words")
            for word in words:
                value = getattr(word, "word", None)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("empty word")
                start = _sample(word.start)
                end = _sample(word.end, end=True)
                if start < previous_start or end < start or end > total_samples:
                    raise ValueError("invalid word alignment")
                if current and start - previous_end > MAX_GAP_SAMPLES:
                    groups.append(current)
                    current = []
                current.append((value, start, end, _score(getattr(word, "probability", None))))
                previous_start, previous_end = start, max(previous_end, end)
            groups.append(current)
            if "".join(word[0] for group in groups for word in group).strip() != text:
                raise ValueError("word text differs from segment text")
            if any(max(word[2] for word in group) <= min(word[1] for word in group) for group in groups):
                raise ValueError("zero-length speech group")
        except (ValueError, TypeError, AttributeError):
            reason = "word-alignment-invalid"
    if reason:
        groups = [[(text, segment_start, segment_end, None)]]
    aligned_groups = [[{"text": word[0], "start_sample": sample_offset + word[1], "end_sample": sample_offset + word[2], "probability": word[3]} for word in group] for group in groups]
    reasons = [reason or (DRIFT_REVIEW_REASON if any(_long_word(word) for word in group) else None) for group in aligned_groups]
    result = []
    index = 0
    while index < len(aligned_groups):
        group = aligned_groups[index]
        if not reason and index + 1 < len(aligned_groups) and _orphan_tail(group, aligned_groups[index + 1]):
            reasons[index] = reasons[index + 1] = DRIFT_REVIEW_REASON
            merged = _drift_fallback(group + aligned_groups[index + 1], raw, sample_offset + segment_start, sample_offset + segment_end)
            if merged is not None:
                result.append(merged)
                index += 2
                continue
        result.append(cue_from_words(group, raw, review_reason=reasons[index]))
        index += 1
    return tuple(result)
