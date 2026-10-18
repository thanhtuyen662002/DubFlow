"""Real-media CPU attenuation backend for the optional AUD-2 stage.

This app-owned energy gate writes independent PCM source, dialogue and
background stems from the input WAV. Activity and clipping statistics are
measured signal properties, not speech-separation quality measurements. Until
reference benchmarks qualify this backend, callers retain the valid AUD-0 mix.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import os
import struct
import tempfile
import wave


class SeparationError(ValueError):
    def __init__(self, code: str, condition: str, *, retryable: bool = False) -> None:
        self.code = str(code)[:128]
        self.condition = " ".join(str(condition).replace("\x00", " ").split())[:4096]
        self.retryable = bool(retryable)
        super().__init__(f"{self.code}: {self.condition}")


@dataclass(frozen=True)
class SeparationConfig:
    window_ms: int = 30
    hop_ms: int = 15
    minimum_rms: int = 180
    attenuation_db: int = 18
    max_duration_seconds: int = 24 * 60 * 60
    max_bytes: int = 8 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value, low, high in (
            ("window_ms", self.window_ms, 10, 2000),
            ("hop_ms", self.hop_ms, 5, 2000),
            ("minimum_rms", self.minimum_rms, 1, 32767),
            ("attenuation_db", self.attenuation_db, 0, 48),
            ("max_duration_seconds", self.max_duration_seconds, 1, 7 * 24 * 60 * 60),
            ("max_bytes", self.max_bytes, 1, 8 * 1024 * 1024 * 1024),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} is outside the bounded separation policy")
        if self.hop_ms > self.window_ms:
            raise ValueError("hop_ms must not exceed window_ms")


@dataclass(frozen=True)
class SeparationMetrics:
    source_hash: str
    dialogue_hash: str
    background_hash: str
    sample_rate: int
    channels: int
    frame_count: int
    active_fraction_milli: int
    residual_speech_millidb: int | None
    background_artifact_milli: int | None
    clipping_fraction_ppm: int
    decision: str
    backend_id: str = "dubflow-cpu-gate-v2"

    def __post_init__(self) -> None:
        for name in ("source_hash", "dialogue_hash", "background_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
                raise ValueError(f"{name} must be a sha256 digest")
        for name, value, high in (
            ("sample_rate", self.sample_rate, 384000),
            ("channels", self.channels, 8),
            ("frame_count", self.frame_count, 2**63 - 1),
            ("active_fraction_milli", self.active_fraction_milli, 1000),
            ("residual_speech_millidb", self.residual_speech_millidb, 120000),
            ("background_artifact_milli", self.background_artifact_milli, 1000),
            ("clipping_fraction_ppm", self.clipping_fraction_ppm, 1_000_000),
        ):
            if value is None and name in {"residual_speech_millidb", "background_artifact_milli"}:
                continue
            if type(value) is not int or value < 0 or value > high:
                raise ValueError(f"{name} is outside the metric bound")
        if self.decision not in {"AUD-2", "AUD-0", "REVIEW"}:
            raise ValueError("separation decision is invalid")
        if self.decision == "AUD-2" and (self.residual_speech_millidb is None or self.background_artifact_milli is None):
            raise ValueError("AUD-2 requires measured residual speech and background artifacts")

    def to_dict(self) -> dict[str, object]:
        return {"backend_id": self.backend_id, "source_hash": self.source_hash, "dialogue_hash": self.dialogue_hash, "background_hash": self.background_hash, "sample_rate": self.sample_rate, "channels": self.channels, "frame_count": self.frame_count, "active_fraction_milli": self.active_fraction_milli, "residual_speech_millidb": self.residual_speech_millidb, "background_artifact_milli": self.background_artifact_milli, "clipping_fraction_ppm": self.clipping_fraction_ppm, "decision": self.decision}


@dataclass(frozen=True)
class SeparationResult:
    source_path: Path
    dialogue_path: Path
    background_path: Path
    metrics: SeparationMetrics
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 2, "source_path": str(self.source_path), "dialogue_path": str(self.dialogue_path), "background_path": str(self.background_path), "metrics": self.metrics.to_dict(), "warnings": list(self.warnings), "fallback": self.metrics.decision != "AUD-2"}


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_pcm_metadata(path: Path, config: SeparationConfig) -> tuple[int, int, int]:
    if not path.is_file() or path.is_symlink():
        raise SeparationError("AUDIO_INPUT_UNAVAILABLE", "source WAV is not a regular file")
    if path.stat().st_size > config.max_bytes:
        raise SeparationError("AUDIO_TOO_LARGE", "source WAV exceeds the separation size bound")
    try:
        with wave.open(str(path), "rb") as reader:
            channels, width, rate, frames, compression = reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes(), reader.getcomptype()
    except (OSError, EOFError, wave.Error) as error:
        raise SeparationError("AUDIO_CORRUPT", str(error)) from error
    if compression != "NONE" or width != 2 or not 1 <= channels <= 2 or not 8_000 <= rate <= 96_000 or frames <= 0:
        raise SeparationError("AUDIO_FORMAT_UNSUPPORTED", "separation requires PCM signed-16 mono/stereo WAV")
    if (frames + rate - 1) // rate > config.max_duration_seconds:
        raise SeparationError("AUDIO_DURATION_TOO_LONG", "source WAV exceeds the separation duration bound")
    if frames * channels * 2 > config.max_bytes:
        raise SeparationError("AUDIO_TOO_LARGE", "source WAV payload exceeds the separation size bound")
    return rate, channels, frames


def _iter_pcm(path: Path, channels: int, frames: int, *, chunk_frames: int = 4096):
    try:
        with wave.open(str(path), "rb") as reader:
            seen = 0
            while seen < frames:
                raw = reader.readframes(min(chunk_frames, frames - seen))
                if not raw:
                    break
                width = channels * 2
                if len(raw) % width:
                    raise SeparationError("AUDIO_TRUNCATED", "source WAV frame data is incomplete")
                block = [tuple(int(item) for item in sample) for sample in struct.iter_unpack("<" + "h" * channels, raw)]
                seen += len(block)
                yield block
            if seen != frames:
                raise SeparationError("AUDIO_TRUNCATED", "source WAV frame count is inconsistent")
    except (OSError, EOFError, wave.Error, struct.error) as error:
        if isinstance(error, SeparationError):
            raise
        raise SeparationError("AUDIO_CORRUPT", str(error)) from error


def _iter_levels(stream):
    stream.seek(0)
    while raw := stream.read(32 * 1024):
        yield from (value for (value,) in struct.iter_unpack(">d", raw))


def _noise_floor(stream) -> float:
    """Exact positive-level 20th percentile in constant memory (radix select)."""
    prefix = 0
    rank = None
    for shift in range(56, -1, -8):
        counts = [0] * 256
        stream.seek(0)
        while raw := stream.read(32 * 1024):
            for (bits,) in struct.iter_unpack(">Q", raw):
                if bits and bits >> (shift + 8) == prefix:
                    counts[(bits >> shift) & 255] += 1
        if rank is None:
            total = sum(counts)
            if not total:
                return 0.0
            rank = total // 5
        for bucket, count in enumerate(counts):
            if rank < count:
                prefix = (prefix << 8) | bucket
                break
            rank -= count
    return struct.unpack(">d", prefix.to_bytes(8, "big"))[0]


class CpuAttenuationBackend:
    """Bounded PCM gate publishing immutable, independently validated stems."""

    backend_id = "dubflow-cpu-gate-v2"

    def __init__(self, config: SeparationConfig | None = None) -> None:
        self.config = config or SeparationConfig()

    def process(self, source_path: str | Path, output_dir: str | Path) -> SeparationResult:
        from .publication import NAMES, prepare, publish

        source = Path(source_path).expanduser().resolve()
        destination = Path(output_dir).expanduser().resolve()
        rate, channels, frame_count = _read_pcm_metadata(source, self.config)
        source_hash = _hash(source)
        window = max(1, rate * self.config.window_ms // 1000)
        hop = max(1, rate * self.config.hop_ms // 1000)
        pending, final = prepare(destination)
        with ExitStack() as resources:
            # Keep exact historical gate decisions without duration-sized lists.
            levels = resources.enter_context(tempfile.TemporaryFile(mode="w+b"))
            analysis_buffer: list[float] = []
            for block in _iter_pcm(source, channels, frame_count):
                analysis_buffer.extend(sum(sample * sample for sample in frame) / channels for frame in block)
                while len(analysis_buffer) >= window:
                    values = analysis_buffer[:window]
                    levels.write(struct.pack(">d", math.sqrt(sum(values) / len(values))))
                    del analysis_buffer[:hop]
            if analysis_buffer:
                levels.write(struct.pack(">d", math.sqrt(sum(analysis_buffer) / len(analysis_buffer))))
            levels.flush()
            threshold = max(float(self.config.minimum_rms), _noise_floor(levels) * 2.4)
            decisions = iter(_iter_levels(levels))
            level_index = -1
            active = False
            handles, writers = [], []
            for name in NAMES:
                handle = resources.enter_context((pending / name).open("xb"))
                writer = resources.enter_context(wave.open(handle, "wb"))
                writer.setnchannels(channels)
                writer.setsampwidth(2)
                writer.setframerate(rate)
                handles.append(handle)
                writers.append(writer)
            active_samples = clipped = frame_index = 0
            for block in _iter_pcm(source, channels, frame_count):
                payloads = [bytearray(), bytearray(), bytearray()]
                for frame in block:
                    while level_index < frame_index // hop:
                        level = next(decisions, None)
                        if level is None:
                            break
                        active = level >= threshold
                        level_index += 1
                    if active:
                        active_samples += 1
                    scale_background = 10 ** (-self.config.attenuation_db / 20.0) if active else 1.0
                    scale_dialogue = 1.0 if active else 0.0
                    payloads[0].extend(struct.pack("<" + "h" * channels, *frame))
                    payloads[1].extend(struct.pack("<" + "h" * channels, *(max(-32766, min(32766, int(round(sample * scale_dialogue)))) for sample in frame)))
                    payloads[2].extend(struct.pack("<" + "h" * channels, *(max(-32766, min(32766, int(round(sample * scale_background)))) for sample in frame)))
                    clipped += sum(1 for sample in frame if abs(sample) >= 32767)
                    frame_index += 1
                for writer, payload in zip(writers, payloads):
                    writer.writeframesraw(payload)
            if _hash(source) != source_hash or _read_pcm_metadata(source, self.config) != (rate, channels, frame_count):
                raise SeparationError("AUDIO_INPUT_CHANGED", "source changed during separation")
            for writer, handle in zip(writers, handles):
                writer.close()
                handle.flush()
                os.fsync(handle.fileno())
        active_fraction = (active_samples * 1000 + frame_count // 2) // frame_count
        clipping_fraction = clipped * 1_000_000 // (frame_count * channels)
        hashes = tuple(_hash(pending / name) for name in NAMES)
        # Activity is not residual-speech/background-quality evidence.
        metrics = SeparationMetrics(*hashes, rate, channels, frame_count, active_fraction, None, None, clipping_fraction, "AUD-0", self.backend_id)
        warnings = ("Residual speech and background artifacts are not measured; retain AUD-0 source ducking",)
        result = SeparationResult(*(final / name for name in NAMES), metrics, warnings)
        publish(pending, final, result)
        return result


__all__ = ["CpuAttenuationBackend", "SeparationConfig", "SeparationError", "SeparationMetrics", "SeparationResult"]
