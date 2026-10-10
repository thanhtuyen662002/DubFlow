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

ASR_RECIPE = "faster-whisper-1.2.1-word-gap-v2"
SAMPLE_RATE = 16_000
MAX_GAP_SAMPLES = SAMPLE_RATE


@dataclass(frozen=True)
class AlignedCue:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    evidence: dict[str, Any]


def _score(value: Any) -> float | None:
    if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return None


def _sample(value: Any, *, end: bool = False) -> int:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid ASR boundary")
    return int((Decimal(str(value)) * SAMPLE_RATE).to_integral_value(rounding=ROUND_CEILING if end else ROUND_FLOOR))


def segment_cues(segment: Any, total_samples: int) -> tuple[AlignedCue, ...]:
    """Split pauses without dropping words or fabricating certainty.

    Missing/malformed word alignment retains the original segment text and
    bounds as an explicit review fallback with unavailable confidence.
    """
    if type(total_samples) is not int or total_samples <= 0:
        raise ValueError("decoded audio must contain positive sample frames")
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
    result = []
    for group in groups:
        start = min(word[1] for word in group)
        end = max(word[2] for word in group)
        value = "".join(word[0] for word in group).strip()
        interval = map_sample_interval(TimePoint(0, TimeBase(1, 1000)), start, end, SAMPLE_RATE)
        probabilities = [word[3] for word in group]
        available = all(probability is not None for probability in probabilities)
        confidence = min(probability for probability in probabilities if probability is not None) if available else 0.0
        evidence = {
            "recipe": ASR_RECIPE,
            "timing_basis": "segment-fallback" if reason else "word-alignment",
            "confidence_basis": "minimum-word-probability-uncalibrated" if available else "unavailable",
            "review_reason": reason,
            "start_sample": start, "end_sample": end, "sample_rate": SAMPLE_RATE,
            "raw_segment_scores": raw,
            "words": [{"text": word[0], "start_sample": word[1], "end_sample": word[2], "probability": word[3]} for word in group],
        }
        identity = f"asr-{start}-{end}-{sha256(value.encode()).hexdigest()[:12]}"
        result.append(AlignedCue(identity, interval.start.ticks, interval.end.ticks, value, confidence, evidence))
    return tuple(result)
