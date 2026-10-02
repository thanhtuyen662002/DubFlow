"""Real-media CPU attenuation backend for the optional AUD-2 stage.

This app-owned energy gate writes independent PCM source, dialogue and
background stems from the input WAV. Activity and clipping statistics are
measured signal properties, not speech-separation quality measurements. Until
reference benchmarks qualify this backend, callers retain the valid AUD-0 mix.
"""

from __future__ import annotations

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


def _atomic_write(path: Path, frames: bytes, channels: int, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            with wave.open(output, "wb") as writer:
                writer.setnchannels(channels)
                writer.setsampwidth(2)
                writer.setframerate(rate)
                writer.writeframes(frames)
                output.flush()
                os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


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


class CpuAttenuationBackend:
    """Streaming-safe CPU gate that produces validated independent stems."""

    backend_id = "dubflow-cpu-gate-v2"

    def __init__(self, config: SeparationConfig | None = None) -> None:
        self.config = config or SeparationConfig()

    def process(self, source_path: str | Path, output_dir: str | Path) -> SeparationResult:
        source = Path(source_path).expanduser().resolve()
        destination = Path(output_dir).expanduser().resolve()
        rate, channels, frame_count = _read_pcm_metadata(source, self.config)
        window = max(1, rate * self.config.window_ms // 1000)
        hop = max(1, rate * self.config.hop_ms // 1000)
        levels: list[float] = []
        analysis_buffer: list[float] = []
        for block in _iter_pcm(source, channels, frame_count):
            # Average channel energies so opposite-phase stereo cannot cancel.
            analysis_buffer.extend(sum(sample * sample for sample in frame) / channels for frame in block)
            while len(analysis_buffer) >= window:
                values = analysis_buffer[:window]
                levels.append(math.sqrt(sum(values) / len(values)))
                del analysis_buffer[:hop]
        if analysis_buffer:
            levels.append(math.sqrt(sum(analysis_buffer) / len(analysis_buffer)))
        positive = sorted(level for level in levels if level > 0)
        noise = positive[min(len(positive) - 1, len(positive) // 5)] if positive else 0
        threshold = max(float(self.config.minimum_rms), noise * 2.4)
        active: list[bool] = []
        for level in levels:
            active.append(level >= threshold)
        destination.mkdir(parents=True, exist_ok=True)
        source_copy = destination / "source_audio.wav"
        dialogue_path = destination / "dialogue_stem.wav"
        background_path = destination / "background_stem.wav"
        temporary_paths: list[Path] = []
        handles = []
        writers = []
        try:
            for target in (source_copy, dialogue_path, background_path):
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=str(destination))
                handle = os.fdopen(descriptor, "wb")
                handles.append(handle)
                temporary_paths.append(Path(temporary_name))
                writer = wave.open(handle, "wb")
                writer.setnchannels(channels)
                writer.setsampwidth(2)
                writer.setframerate(rate)
                writers.append(writer)
        except OSError as error:
            for handle in handles:
                handle.close()
            for temporary in temporary_paths:
                temporary.unlink(missing_ok=True)
            raise SeparationError("AUDIO_OUTPUT_UNAVAILABLE", str(error), retryable=True) from error
        active_samples = 0
        clipped = 0
        frame_index = 0
        try:
            for block in _iter_pcm(source, channels, frame_count):
                for frame in block:
                    window_index = min(len(active) - 1, frame_index // hop)
                    is_active = active[window_index] if active else False
                    if is_active:
                        active_samples += 1
                    scale_background = 10 ** (-self.config.attenuation_db / 20.0) if is_active else 1.0
                    scale_dialogue = 1.0 if is_active else 0.0
                    source_bytes = b"".join(struct.pack("<h", sample) for sample in frame)
                    dialogue_bytes = b"".join(struct.pack("<h", max(-32766, min(32766, int(round(sample * scale_dialogue))))) for sample in frame)
                    background_bytes = b"".join(struct.pack("<h", max(-32766, min(32766, int(round(sample * scale_background))))) for sample in frame)
                    clipped += sum(1 for sample in frame if abs(sample) >= 32767)
                    writers[0].writeframesraw(source_bytes)
                    writers[1].writeframesraw(dialogue_bytes)
                    writers[2].writeframesraw(background_bytes)
                    frame_index += 1
            for writer, handle in zip(writers, handles):
                writer.close()
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
            for temporary, target in zip(temporary_paths, (source_copy, dialogue_path, background_path)):
                os.replace(temporary, target)
        except BaseException:
            for writer in writers:
                try:
                    writer.close()
                except Exception:
                    pass
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass
            for temporary in temporary_paths:
                temporary.unlink(missing_ok=True)
            raise
        active_fraction = (active_samples * 1000 + max(1, frame_count) // 2) // max(1, frame_count)
        # Activity does not measure residual speech or background damage.
        # Do not manufacture quality scores or promote an unqualified backend.
        residual = None
        artifact = None
        clipping_fraction = clipped * 1_000_000 // max(1, frame_count * channels)
        decision = "AUD-0"
        metrics = SeparationMetrics(_hash(source_copy), _hash(dialogue_path), _hash(background_path), rate, channels, frame_count, active_fraction, residual, artifact, clipping_fraction, decision, self.backend_id)
        warnings = ("Residual speech and background artifacts are not measured; retain AUD-0 source ducking",)
        return SeparationResult(source_copy, dialogue_path, background_path, metrics, warnings)


__all__ = ["CpuAttenuationBackend", "SeparationConfig", "SeparationError", "SeparationMetrics", "SeparationResult"]
