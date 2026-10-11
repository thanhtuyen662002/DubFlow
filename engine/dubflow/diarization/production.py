"""App-owned CPU speaker diarization over real PCM WAV media.

The production boundary deliberately uses only standard-library signal features.
It is a conservative fallback for machines without a neural diarization model:
voice activity is measured from the source waveform, short-time energy/zero
crossing/differential energy form a bounded feature vector, and an online
cosine clusterer emits stable within-job speaker IDs.  The implementation is
streaming and checkpoint-friendly; it never treats a visual character as an
audio speaker.  A stronger model can implement the same result contract later
without changing the worker or editable-artifact boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import math
from pathlib import Path
import struct
import time
import tracemalloc
from typing import Callable, Iterable, Mapping, Sequence
import wave

from engine.dubflow.diarization.baseline import (
    DiarizationBaseline,
    DiarizationResult,
    ResourceProfile,
    SpeakerObservation,
)
from engine.dubflow.tts.casting import MultiVoicePlan, TtsCue, VoiceCaster


PRODUCTION_DIARIZATION_VERSION = "1.0.0"
PRODUCTION_DIARIZATION_MODEL_ID = "dubflow-cpu-energy-diarizer"
PRODUCTION_DIARIZATION_MODEL_VERSION = PRODUCTION_DIARIZATION_VERSION
PRODUCTION_DIARIZATION_ALGORITHM = "pcm-energy-zcr-online-cosine-v1"
_MAX_WAV_BYTES = 8 * 1024 * 1024 * 1024
_MAX_JOB_TICKS = (1 << 63) - 1


class ProductionDiarizationError(ValueError):
    """A bounded, actionable failure at the real-media diarization boundary."""

    def __init__(self, code: str, condition: str, *, retryable: bool = False) -> None:
        self.code = str(code)[:128]
        self.condition = " ".join(str(condition).replace("\x00", " ").split())[:4096]
        self.retryable = bool(retryable)
        super().__init__(f"{self.code}: {self.condition}")


@dataclass(frozen=True)
class ProductionDiarizationConfig:
    """Bounded signal and resource policy for the app-owned CPU backend."""

    window_ms: int = 30
    hop_ms: int = 15
    min_speech_ms: int = 120
    silence_gap_ms: int = 240
    warmup_windows: int = 160
    rms_floor: int = 180
    max_speakers: int = 8
    cluster_similarity: float = 0.965
    max_duration_seconds: int = 24 * 60 * 60
    max_windows: int = 6_000_000

    def __post_init__(self) -> None:
        for name, value, minimum, maximum in (
            ("window_ms", self.window_ms, 10, 2000),
            ("hop_ms", self.hop_ms, 5, 2000),
            ("min_speech_ms", self.min_speech_ms, 20, 60_000),
            ("silence_gap_ms", self.silence_gap_ms, 20, 60_000),
            ("warmup_windows", self.warmup_windows, 1, 10_000),
            ("rms_floor", self.rms_floor, 1, 32_767),
            ("max_speakers", self.max_speakers, 1, 32),
            ("max_duration_seconds", self.max_duration_seconds, 1, 7 * 24 * 60 * 60),
            ("max_windows", self.max_windows, 1, 20_000_000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside the production bound")
        if self.hop_ms > self.window_ms:
            raise ValueError("hop_ms must not exceed window_ms")
        if type(self.cluster_similarity) not in (int, float) or not math.isfinite(float(self.cluster_similarity)) or not 0.5 <= float(self.cluster_similarity) <= 0.9999:
            raise ValueError("cluster_similarity must be finite and between 0.5 and 0.9999")


@dataclass(frozen=True)
class AudioWaveMetadata:
    path: str
    sample_rate: int
    channels: int
    frame_count: int
    duration_ticks: int
    content_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "format": "wav-pcm-s16le",
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_count": self.frame_count,
            "duration_ticks": str(self.duration_ticks),
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class ProductionDiarizationReport:
    """Serializable evidence emitted by the production CPU stage."""

    result: DiarizationResult
    audio: AudioWaveMetadata
    vad_threshold_rms: int
    feature_window_ms: int
    feature_hop_ms: int
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.vad_threshold_rms) is not int or not 0 <= self.vad_threshold_rms <= 32_767:
            raise ValueError("vad threshold is invalid")
        if type(self.feature_window_ms) is not int or self.feature_window_ms <= 0 or type(self.feature_hop_ms) is not int or self.feature_hop_ms <= 0:
            raise ValueError("feature timing is invalid")
        if type(self.warnings) is not tuple or any(not isinstance(item, str) or not item for item in self.warnings):
            raise ValueError("warnings must be non-empty strings")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "backend": {
                "model_id": PRODUCTION_DIARIZATION_MODEL_ID,
                "model_version": PRODUCTION_DIARIZATION_MODEL_VERSION,
                "algorithm": PRODUCTION_DIARIZATION_ALGORITHM,
                "runtime": "python-stdlib",
                "network_required": False,
                "credential_required": False,
            },
            "audio": self.audio.to_dict(),
            "vad": {
                "threshold_rms": self.vad_threshold_rms,
                "window_ms": self.feature_window_ms,
                "hop_ms": self.feature_hop_ms,
            },
            "result": self.result.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SpeakerAwarePlan:
    """Voice assignments plus duration checks for translated timed cues."""

    cues: tuple[TtsCue, ...]
    multi_voice: MultiVoicePlan
    fallback_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "fallback_count": self.fallback_count,
            "cues": [
                {
                    "segment_id": cue.segment_id,
                    "speaker_cluster_id": cue.speaker_cluster_id,
                    "role": cue.role,
                    "start_ticks": str(cue.start_ticks),
                    "end_ticks": str(cue.end_ticks),
                    "text": cue.text,
                }
                for cue in self.cues
            ],
            "assignments": [
                {
                    "segment_id": item.segment_id,
                    "track_id": item.track_id,
                    "voice_id": item.voice_id,
                    "speaker_cluster_id": item.speaker_cluster_id,
                }
                for item in self.multi_voice.assignments
            ],
            "durations": [
                {
                    "segment_id": item.segment_id,
                    "slot_start_ticks": str(item.slot_start_ticks),
                    "slot_end_ticks": str(item.slot_end_ticks),
                    "generated_duration_ticks": str(item.generated_duration_ticks),
                    "fit_mode": item.fit_mode,
                    "speed_ratio_milli": item.speed_ratio_milli,
                }
                for item in self.multi_voice.durations
            ],
        }


@dataclass
class _Cluster:
    cluster_id: str
    centroid: tuple[float, ...]
    count: int


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ProductionDiarizationError("AUDIO_READ_FAILED", str(error), retryable=True) from error
    return "sha256:" + digest.hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return -1.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)))


def _normalize(values: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 1e-12:
        return (1.0,) + (0.0,) * (len(values) - 1)
    return tuple(value / norm for value in values)


def _feature(samples: Sequence[int]) -> tuple[int, tuple[float, ...]]:
    if not samples:
        return 0, (1.0, 0.0, 0.0)
    squared = sum(sample * sample for sample in samples)
    rms = int(round(math.sqrt(squared / len(samples))))
    crossings = sum(1 for left, right in zip(samples, samples[1:]) if (left < 0 <= right) or (right < 0 <= left))
    zcr = crossings / max(1, len(samples) - 1)
    differences = sum((right - left) * (right - left) for left, right in zip(samples, samples[1:]))
    differential = math.sqrt(differences / max(1, len(samples) - 1)) / 32768.0
    # Normalized short-lag autocorrelation supplies a cheap spectral shape
    # feature. It separates voices with similar loudness/zero-crossing rates
    # while remaining bounded and deterministic without an external model.
    denominator = max(1, squared)
    autocorrelation = tuple(
        sum(samples[index] * samples[index - lag] for index in range(lag, len(samples))) / denominator
        for lag in (20, 32, 48, 64, 80)
        if lag < len(samples)
    )
    vector = _normalize((math.log1p(rms) / 12.0, zcr, differential, *autocorrelation))
    return rms, vector


def _read_window(reader: wave.Wave_read, frames: int, channels: int) -> list[int]:
    raw = reader.readframes(frames)
    if not raw:
        return []
    width = 2 * channels
    if len(raw) % width:
        raise ProductionDiarizationError("AUDIO_TRUNCATED", "WAV frame data is not aligned to complete samples")
    mono: list[int] = []
    format_string = "<" + ("h" * channels)
    try:
        for frame in struct.iter_unpack(format_string, raw):
            mono.append(int(round(sum(frame) / channels)))
    except struct.error as error:
        raise ProductionDiarizationError("AUDIO_CORRUPT", str(error)) from error
    return mono


def _metadata(path: Path, config: ProductionDiarizationConfig) -> AudioWaveMetadata:
    if not path.is_file():
        raise ProductionDiarizationError("AUDIO_INPUT_UNAVAILABLE", f"WAV does not exist: {path}")
    try:
        if path.stat().st_size > _MAX_WAV_BYTES:
            raise ProductionDiarizationError("AUDIO_TOO_LARGE", "WAV exceeds the production size limit")
        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            frame_count = reader.getnframes()
            compression = reader.getcomptype()
    except (OSError, EOFError, wave.Error) as error:
        raise ProductionDiarizationError("AUDIO_CORRUPT", f"WAV header could not be read: {error}") from error
    if compression != "NONE" or sample_width != 2:
        raise ProductionDiarizationError("AUDIO_FORMAT_UNSUPPORTED", "production diarization requires PCM signed-16 WAV")
    if not 1 <= channels <= 2 or not 8_000 <= sample_rate <= 96_000 or frame_count <= 0:
        raise ProductionDiarizationError("AUDIO_METADATA_INVALID", "WAV channels, rate or frame count is outside the production bound")
    duration_seconds = (frame_count + sample_rate - 1) // sample_rate
    if duration_seconds > config.max_duration_seconds:
        raise ProductionDiarizationError("AUDIO_DURATION_TOO_LONG", "WAV exceeds the production duration limit")
    duration_ticks = (frame_count * 1000 + sample_rate - 1) // sample_rate
    if duration_ticks > _MAX_JOB_TICKS:
        raise ProductionDiarizationError("AUDIO_DURATION_OVERFLOW", "WAV duration exceeds canonical tick range")
    return AudioWaveMetadata(str(path), sample_rate, channels, frame_count, duration_ticks, _sha256_file(path))


def _threshold(windows: Sequence[tuple[int, tuple[float, ...]]], floor: int) -> int:
    levels = sorted(level for level, _ in windows if level > 0)
    if not levels:
        return floor
    low_index = min(len(levels) - 1, max(0, len(levels) // 5))
    low = levels[low_index]
    high = levels[-1]
    # A bounded adaptive threshold handles quiet recordings while preventing a
    # single loud sample from classifying every noise floor window as speech.
    return max(floor, min(max(floor, high // 4), int(round(low * 2.4))))


def _iter_features(path: Path, audio: AudioWaveMetadata, window_frames: int, hop_frames: int):
    """Yield overlapping short-time features without loading a long WAV."""
    with wave.open(str(path), "rb") as reader:
        buffered: list[int] = []
        consumed = 0
        while consumed < audio.frame_count and len(buffered) < window_frames:
            block = _read_window(reader, min(hop_frames, audio.frame_count - consumed), audio.channels)
            if not block:
                break
            buffered.extend(block)
            consumed += len(block)
        index = 0
        while buffered:
            window = buffered[:window_frames]
            if not window:
                break
            yield index, _feature(window)
            index += 1
            drop = min(hop_frames, len(buffered))
            buffered = buffered[drop:]
            if consumed < audio.frame_count:
                block = _read_window(reader, min(hop_frames, audio.frame_count - consumed), audio.channels)
                if block:
                    buffered.extend(block)
                    consumed += len(block)


def diarize_wav(
    path: str | Path,
    job_id: str,
    *,
    config: ProductionDiarizationConfig | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ProductionDiarizationReport:
    """Diarize an app-owned PCM WAV using a streaming CPU feature backend.

    ``progress`` receives ``(windows_done, windows_total)`` and is optional;
    callbacks are never allowed to alter durable state.  All time boundaries
    are integer milliseconds, matching the worker's timeline-v1 denominator.
    """

    if not isinstance(job_id, str) or not job_id or len(job_id) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in job_id):
        raise ValueError("job_id must be bounded text")
    policy = config or ProductionDiarizationConfig()
    source = Path(path).expanduser().resolve()
    audio = _metadata(source, policy)
    window_frames = max(1, (audio.sample_rate * policy.window_ms) // 1000)
    hop_frames = max(1, (audio.sample_rate * policy.hop_ms) // 1000)
    expected_windows = max(1, (max(0, audio.frame_count - window_frames) + hop_frames - 1) // hop_frames + 1)
    total_windows = min(policy.max_windows, expected_windows)
    tracemalloc.start()
    started = time.monotonic()
    try:
        # A small warm-up is retained to derive a noise floor, then the same
        # windows are processed once.  We do not load long-form audio into RAM.
        warmup: list[tuple[int, tuple[float, ...]]] = []
        for _index, item in _iter_features(source, audio, window_frames, hop_frames):
            warmup.append(item)
            if len(warmup) >= policy.warmup_windows:
                break
        if not warmup:
            raise ProductionDiarizationError("AUDIO_EMPTY", "WAV contains no analysable windows")
        vad = _threshold(warmup, policy.rms_floor)
        clusters: list[_Cluster] = []
        observations: list[SpeakerObservation] = []
        active_cluster: str | None = None
        active_role = "speaker"
        active_start = 0
        active_end = 0
        active_confidences: list[float] = []
        silence_start: int | None = None
        segment_number = 0

        def close_active() -> None:
            nonlocal active_cluster, active_role, active_start, active_end, active_confidences, silence_start, segment_number
            if active_cluster is None and active_role == "speaker":
                return
            if active_end <= active_start:
                active_cluster = None
                active_role = "speaker"
                active_confidences = []
                silence_start = None
                return
            segment_number += 1
            confidence = max(0.0, min(1.0, sum(active_confidences) / max(1, len(active_confidences))))
            duration = active_end - active_start
            if duration < policy.min_speech_ms:
                # A one-window boundary blip is a VAD/cluster transition, not
                # useful dialogue. Drop it instead of producing review noise;
                # longer uncertain regions remain explicit in the contract.
                if duration >= policy.hop_ms * 2:
                    observations.append(SpeakerObservation(f"audio-{segment_number:08d}", active_start, active_end, None, "unresolved", Decimal(str(round(confidence * 0.5, 6))), None, "wav"))
            else:
                observations.append(SpeakerObservation(f"audio-{segment_number:08d}", active_start, active_end, active_cluster, active_role, Decimal(str(round(confidence, 6))), None, "wav"))
            active_cluster = None
            active_role = "speaker"
            active_start = 0
            active_end = 0
            active_confidences = []
            silence_start = None

        for index, (rms, vector) in _iter_features(source, audio, window_frames, hop_frames):
            if index >= policy.max_windows:
                raise ProductionDiarizationError("AUDIO_WINDOW_LIMIT", "WAV exceeds the bounded diarization window limit")
            start_ms = (index * policy.hop_ms)
            end_ms = min(audio.duration_ticks, start_ms + policy.window_ms)
            if end_ms <= start_ms:
                continue
            if rms < vad:
                if active_cluster is not None or active_role != "speaker":
                    silence_start = silence_start if silence_start is not None else start_ms
                    if start_ms - silence_start >= policy.silence_gap_ms:
                        close_active()
                if progress is not None and index % 32 == 0:
                    progress(index + 1, total_windows)
                continue
            silence_start = None
            matches = sorted(((_cosine(vector, cluster.centroid), cluster) for cluster in clusters), key=lambda item: (item[0], item[1].cluster_id), reverse=True)
            best_score = matches[0][0] if matches else -1.0
            second_score = matches[1][0] if len(matches) > 1 else -1.0
            if matches and (best_score >= float(policy.cluster_similarity) or len(clusters) >= policy.max_speakers):
                cluster = matches[0][1]
                # A small bounded update follows changing microphones without
                # allowing a speaker ID to jump on a single noisy frame.
                weight = min(0.25, 1.0 / max(1, cluster.count))
                cluster.centroid = _normalize(tuple((1.0 - weight) * old + weight * new for old, new in zip(cluster.centroid, vector)))
                cluster.count += 1
            else:
                seed = f"{job_id}|{len(clusters)}|{','.join(format(value, '.6f') for value in vector)}".encode()
                cluster = _Cluster("spk-" + sha256(seed).hexdigest()[:16], vector, 1)
                clusters.append(cluster)
            confidence = max(0.05, min(1.0, 0.5 + 0.5 * (best_score if best_score >= 0 else 0.0) + 0.25 * max(0.0, best_score - second_score)))
            if active_cluster is None:
                active_cluster, active_start, active_end, active_confidences = cluster.cluster_id, start_ms, end_ms, [confidence]
            elif active_cluster == cluster.cluster_id:
                active_end = end_ms
                active_confidences.append(confidence)
            else:
                close_active()
                active_cluster, active_start, active_end, active_confidences = cluster.cluster_id, start_ms, end_ms, [confidence]
            if progress is not None and index % 32 == 0:
                progress(index + 1, total_windows)
        close_active()
        baseline = DiarizationBaseline(model_id=PRODUCTION_DIARIZATION_MODEL_ID, model_version=PRODUCTION_DIARIZATION_MODEL_VERSION, similarity_threshold=0.999)
        elapsed = max(0, int((time.monotonic() - started) * 1000))
        _, peak_memory = tracemalloc.get_traced_memory()
        merged = baseline.merge_chunks(job_id, [observations], resource_profile=ResourceProfile(audio.duration_ticks, len(observations), elapsed, peak_memory, backend=PRODUCTION_DIARIZATION_ALGORITHM))
        result = DiarizationResult(job_id, PRODUCTION_DIARIZATION_MODEL_ID, PRODUCTION_DIARIZATION_MODEL_VERSION, PRODUCTION_DIARIZATION_ALGORITHM, merged.segments, merged.resource_profile)
        return ProductionDiarizationReport(result, audio, vad, policy.window_ms, policy.hop_ms, ("overlap_not_inferred_by_single_channel_cpu_backend",))
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # ``ResourceProfile`` is immutable and already emitted above.  Peak
        # tracing is intentionally kept out of the result if it is unavailable
        # on a constrained interpreter; the path remains valid and bounded.
        _ = peak


def build_speaker_aware_plan(
    translated_cues: Iterable[Mapping[str, object]],
    diarization: DiarizationResult,
    caster: VoiceCaster,
    generated_duration_ticks: Mapping[str, int],
) -> SpeakerAwarePlan:
    """Attach each translation cue to the strongest overlapping audio segment."""

    segments = tuple(diarization.segments)
    cues: list[TtsCue] = []
    fallback_count = 0
    for raw in translated_cues:
        try:
            segment_id = str(raw["segment_id"])
            start = int(raw["start_ticks"])
            end = int(raw["end_ticks"])
            text = str(raw.get("translated_text") or raw["text"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProductionDiarizationError("CUE_INVALID", "translated cue is missing bounded timing/text fields") from error
        overlaps = sorted(((max(0, min(end, segment.end_ticks) - max(start, segment.start_ticks)), segment) for segment in segments), key=lambda item: (item[0], item[1].confidence, item[1].segment_id), reverse=True)
        winner = overlaps[0][1] if overlaps and overlaps[0][0] > 0 else None
        if winner is None or winner.role != "speaker" or not winner.speaker_cluster_ids:
            role = "narrator" if winner is None or winner.role in {"narrator", "offscreen"} else "unresolved"
            cluster_id = None
            fallback_count += 1
        else:
            role = "speaker"
            cluster_id = winner.speaker_cluster_ids[0]
        cues.append(TtsCue(segment_id, cluster_id, role, start, end, text))
    plan = caster.plan(cues, generated_duration_ticks)
    return SpeakerAwarePlan(tuple(cues), plan, fallback_count)


__all__ = [
    "AudioWaveMetadata",
    "PRODUCTION_DIARIZATION_ALGORITHM",
    "PRODUCTION_DIARIZATION_MODEL_ID",
    "PRODUCTION_DIARIZATION_MODEL_VERSION",
    "PRODUCTION_DIARIZATION_VERSION",
    "ProductionDiarizationConfig",
    "ProductionDiarizationError",
    "ProductionDiarizationReport",
    "SpeakerAwarePlan",
    "build_speaker_aware_plan",
    "diarize_wav",
]
