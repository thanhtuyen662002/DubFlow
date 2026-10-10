"""Bounded, file-based AUD-0 production mixing; durable state stays in supervisor.

The byte/fixture adapter is retained. This adapter uses PCM blocks and private
generation journals, and returns the same version-1 mix document. A caller must
serialize a generation and use a verified app-owned Python/NumPy runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256, file_digest
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import struct
import wave
from typing import Callable, Sequence

from engine.dubflow.asr import TimePoint
from engine.dubflow.tts import map_timepoint_to_sample
from .adapter import (AudioMetrics, DuckWindow, MixArtifact, MixConfig, MixDocument,
    MixError, MixFailure, MixProvenance, ResourceProfile, parse_mix_json,
    _canonical_interval, _duration_ticks_to_samples, _ensure_hash, _hash_json,
    _integer, _text, _parse_point, _parse_artifact, validate_mix_document)

PRODUCER_VERSION = "2.0.1"
BACKEND_ID = "pcm-stream-duck-v1"
NUMPY_VERSION = "2.2.6"
_HEADER = struct.Struct("<4sI4s4sIHHIIHH4sI")
_JOURNAL_LIMIT = 32 * 1024 * 1024


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return "sha256:" + file_digest(handle, "sha256").hexdigest()


def _plain(path: Path) -> Path:
    if not path.is_absolute():
        raise MixError("AUDIO_PATH_UNSAFE", "audio and generation paths must be absolute")
    if ".." in path.parts or any(":" in part for part in path.parts[1:]):
        raise MixError("AUDIO_PATH_UNSAFE", "parent traversal and named streams are not accepted")
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or bool(getattr(part.lstat(), "st_file_attributes", 0) & 0x400)):
            raise MixError("AUDIO_PATH_UNSAFE", "linked audio/generation paths are not accepted")
        if part.is_file() and part.stat().st_nlink != 1:
            raise MixError("AUDIO_PATH_UNSAFE", "multiply linked audio/generation files are not accepted")
    return path


def _atomic(path: Path, payload: bytes) -> None:
    _plain(path)
    temporary = path.with_name(path.name + ".partial")
    _plain(temporary)
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _restore(value):
    """Reconstruct only a schema-validated version-1 document."""
    provenance = dict(value["provenance"])
    provenance["resource"] = ResourceProfile(**provenance["resource"])
    return MixDocument(value["source_id"], tuple(value["segment_ids"]), tuple(value["segments"]),
        value["source_layout"], _parse_point(value["source_start"], "source_start"),
        _parse_point(value["source_end"], "source_end"), value["sample_rate"], value["channels"],
        *(_parse_artifact(value[key], key) for key in ("original_audio", "dialogue_stem", "final_mix")),
        tuple(DuckWindow(**item) for item in value["duck_windows"]), tuple(value["chunks"]),
        MixProvenance(**provenance), tuple(MixFailure(**item) for item in value["failures"]), tuple(value["warnings"]))


@dataclass(frozen=True)
class StreamLimits:
    block_frames: int = 262144
    max_source_bytes: int = (1 << 32) - 1
    max_segments: int = 10000
    max_blocks: int = 16384
    disk_reserve_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        _integer(self.block_frames, "block_frames", minimum=1, maximum=1048576)
        _integer(self.max_source_bytes, "max_source_bytes", minimum=1024, maximum=(1 << 32) - 1)
        _integer(self.max_segments, "max_segments", minimum=1, maximum=10000)
        _integer(self.max_blocks, "max_blocks", minimum=1, maximum=16384)
        _integer(self.disk_reserve_bytes, "disk_reserve_bytes", minimum=0)


@dataclass(frozen=True)
class FileSource:
    path: Path
    start: TimePoint
    end: TimePoint
    source_id: str = "source-audio"
    layout: str = "source"

    def __post_init__(self):
        object.__setattr__(self, "path", _plain(Path(self.path)))
        _canonical_interval(self.start, self.end, "source")
        _text(self.source_id, "source_id", limit=256)
        _text(self.layout, "layout", limit=128)


@dataclass(frozen=True)
class FileSegment:
    segment_id: str
    source_utterance_id: str
    start: TimePoint
    end: TimePoint
    path: Path | None = None
    content_hash: str | None = None
    status: str = "available"
    condition: str | None = None
    confidence: float = 1.0
    fallback_used: bool = False

    def __post_init__(self):
        _text(self.segment_id, "segment_id", limit=256)
        _text(self.source_utterance_id, "source_utterance_id", limit=256)
        _canonical_interval(self.start, self.end, "segment")
        if self.status not in {"available", "missing", "failed"} or (self.status == "available" and self.path is None):
            raise MixError("INVALID_SEGMENT", "available segment requires a file reference")
        if self.path is not None: object.__setattr__(self, "path", _plain(Path(self.path)))
        if self.content_hash is not None: _ensure_hash(self.content_hash, "segment.content_hash")
        if type(self.confidence) not in (int, float) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise MixError("INVALID_CONFIDENCE", "confidence must be finite and in [0,1]")
        if type(self.fallback_used) is not bool: raise MixError("INVALID_BOOLEAN", "fallback must be boolean")
        if self.condition is not None: _text(self.condition, "condition", limit=4096)

    @classmethod
    def from_tts_artifact(cls, artifact):
        return cls(artifact.segment_id, artifact.source_utterance_id, artifact.slot_start,
                   artifact.actual_end if getattr(artifact, "render_window_end", None) is not None else artifact.slot_end, Path(artifact.path), artifact.content_hash,
                   confidence=artifact.confidence, fallback_used=artifact.fallback_used)


@dataclass(frozen=True)
class _Wave:
    path: Path
    rate: int
    channels: int
    frames: int
    digest: str


def _wave(path: Path, maximum: int, expected_hash: str | None = None) -> _Wave:
    _plain(path)
    if not path.is_file(): raise MixError("AUDIO_MISSING", "WAV is not an available regular file")
    size = path.stat().st_size
    if size > maximum: raise MixError("AUDIO_SIZE_BOUND", "WAV exceeds its file-size bound")
    if size < 12: raise MixError("AUDIO_CORRUPT", "WAV header is truncated")
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
        if header[:4] != b"RIFF" or header[8:] != b"WAVE" or struct.unpack("<I", header[4:8])[0] + 8 != size:
            raise MixError("AUDIO_CORRUPT", "WAV RIFF size/header differs from actual bytes")
        with wave.open(str(path), "rb") as reader:
            rate, channels, frames = reader.getframerate(), reader.getnchannels(), reader.getnframes()
            if reader.getcomptype() != "NONE" or reader.getsampwidth() != 2 or channels not in {1, 2} or not 0 < rate <= 96000 or frames < 1:
                raise MixError("UNSUPPORTED_AUDIO_FORMAT", "WAV must be positive mono/stereo PCM16")
            reader.setpos(frames - 1)
            if len(reader.readframes(1)) != channels * 2:
                raise MixError("AUDIO_CORRUPT", "WAV frame payload is truncated")
    except (OSError, wave.Error, EOFError) as error:
        raise MixError("AUDIO_CORRUPT", "WAV cannot be decoded") from error
    digest = file_hash(path)
    if expected_hash is not None and digest != expected_hash:
        raise MixError("AUDIO_HASH_MISMATCH", "WAV differs from its pinned TTS hash")
    return _Wave(path, rate, channels, frames, digest)


@dataclass
class _Stats:
    samples: int = 0
    frames: int = 0
    squared: int = 0
    peak: int = 0
    clipped: int = 0

    def add(self, values, np):
        # Every reduction is bounded to one block; accumulate into Python ints.
        self.samples += int(values.size); self.frames += int(values.shape[0])
        self.squared += int(np.sum(values * values, dtype=np.int64))
        absolute = np.abs(values)
        self.peak = max(self.peak, int(np.max(absolute, initial=0)))
        self.clipped += int(np.count_nonzero(absolute >= 32767))

    def rms(self):
        return min(1000, round(math.sqrt(self.squared / self.samples) * 1000 / 32768)) if self.samples else 0

    def metrics(self, digest):
        return AudioMetrics(self.samples, self.frames, self.rms(), self.peak, self.clipped, digest)


class StreamingAudioMixer:
    def __init__(self, *, output_dir: Path, config: MixConfig | None = None, limits: StreamLimits | None = None,
                 checkpoint: Callable[[str, int, int, str], None] | None = None):
        self.output_dir = _plain(Path(output_dir))
        self.config = config or MixConfig(resource=ResourceProfile(max_memory_mb=512))
        self.limits = limits or StreamLimits()
        self.checkpoint = checkpoint or (lambda *args: None)
        if self.config.resource.max_memory_mb < 128:
            raise MixError("AUDIO_MEMORY_BUDGET", "streaming numeric runtime needs at least 128MiB stage budget")
        # Block arrays and the largest single TTS read, independent of duration.
        if self.limits.block_frames * 2 * 8 * 12 + self.config.max_tts_bytes * 4 > self.config.resource.max_memory_mb * 1024 * 1024:
            raise MixError("AUDIO_MEMORY_BUDGET", "configured block/input buffers exceed stage budget")

    def _runtime(self):
        try:
            version = importlib.metadata.version("numpy")
        except importlib.metadata.PackageNotFoundError as error:
            raise MixError("AUDIO_RUNTIME_REQUIRED", "pinned numeric runtime is unavailable") from error
        if version != NUMPY_VERSION:
            raise MixError("AUDIO_RUNTIME_VERSION_MISMATCH", "numeric runtime differs from pinned NumPy")
        try:
            import numpy
        except ImportError as error:
            raise MixError("AUDIO_RUNTIME_REQUIRED", "pinned numeric runtime cannot be loaded") from error
        return numpy

    def _disk(self, root, remaining):
        if shutil.disk_usage(root).free < remaining + self.limits.disk_reserve_bytes:
            raise MixError("AUDIO_DISK_REQUIRED", "insufficient free disk for pending mix artifacts")

    def _save(self, root, state):
        payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > _JOURNAL_LIMIT:
            raise MixError("MIX_CHECKPOINT_BOUND", "private journal exceeds its size bound")
        _atomic(root / "checkpoint.json", payload)
        self.checkpoint(state["phase"], len(state["stages"][state["phase"]]), state["block_count"], file_hash(root / "checkpoint.json"))

    @staticmethod
    def _scale(values, factor, np):
        return np.where(values >= 0, values * factor // 1000, -((-values * factor) // 1000))

    def _normalization(self, stats):
        factor, warnings = 1000, []
        if stats.peak > self.config.max_peak:
            factor = min(factor, self.config.max_peak * 1000 // stats.peak)
            warnings.append("mix peak was reduced to the configured headroom")
        if stats.rms() > self.config.max_rms_milli:
            factor = min(factor, self.config.max_rms_milli * 1000 // max(1, stats.rms()))
            warnings.append("mix RMS was reduced to the configured loudness ceiling")
        return factor, warnings

    def _tts_block(self, audio, output_start, count, rate, channels, np):
        indices = np.arange(output_start, output_start + count, dtype=np.int64) * audio.rate // rate
        indices = np.minimum(indices, audio.frames - 1)
        first, last = int(indices[0]), int(indices[-1])
        with wave.open(str(audio.path), "rb") as reader:
            reader.setpos(first); payload = reader.readframes(last - first + 1)
        if len(payload) != (last - first + 1) * audio.channels * 2:
            raise MixError("AUDIO_INPUT_CHANGED", "TTS became truncated during bounded read")
        values = np.frombuffer(payload, dtype="<i2").reshape(-1, audio.channels).astype(np.int64)[indices - first]
        if audio.channels == channels: return values
        if channels == 2: return np.repeat(values, 2, axis=1)
        return (np.sum(values, axis=1, dtype=np.int64) // 2).reshape(-1, 1)

    def _block(self, source_pcm, offset, valid, np, *, with_dialogue=True):
        count, channels = source_pcm.shape
        dialogue = np.zeros((count, channels), dtype=np.int64)
        envelope = np.full(count, 1000, dtype=np.int64)
        positions = np.arange(offset, offset + count, dtype=np.int64)
        for segment, audio, window, start in valid:
            end = start + (audio.frames * self._source_rate + audio.rate - 1) // audio.rate
            lo, hi = max(offset, start), min(offset + count, end)
            if with_dialogue and lo < hi:
                samples = self._tts_block(audio, lo - start, hi - lo, self._source_rate, channels, np)
                dialogue[lo - offset:hi - offset] += self._scale(samples, self.config.dialogue_gain_milli, np)
            wstart, wend = window.start_sample - self._source_start, window.end_sample - self._source_start
            # Legacy AUD-0 clips duck slot to the actual source before ramps.
            wstart, wend = max(0, wstart), min(self._source_frames, wend)
            if wend <= wstart or wend + window.release_samples <= offset or wstart - window.attack_samples >= offset + count: continue
            levels = np.full(count, 1000, dtype=np.int64)
            inside = (positions >= wstart) & (positions < wend)
            levels[inside] = window.duck_gain_milli
            attack = (positions >= wstart - window.attack_samples) & (positions < wstart)
            levels[attack] = 1000 - ((1000 - window.duck_gain_milli) * (window.attack_samples - (wstart - positions[attack]) + 1) // (window.attack_samples + 1))
            release = (positions >= wend) & (positions < wend + window.release_samples)
            levels[release] = window.duck_gain_milli + ((1000 - window.duck_gain_milli) * (positions[release] - wend + 1) // (window.release_samples + 1))
            envelope = np.minimum(envelope, np.clip(levels, 0, 1000))
        return np.clip(dialogue, -32768, 32767), self._scale(source_pcm, envelope[:, None], np)

    def mix(self, source: FileSource, segments: Sequence[FileSegment], *, input_hash: str) -> MixDocument:
        try:
            return self._mix(source, segments, input_hash=input_hash)
        except (OSError, wave.Error, EOFError) as error:
            raise MixError("MIX_IO_FAILED", "mix I/O failed; verified checkpoints and prior generations are retained") from error

    def _mix(self, source: FileSource, segments: Sequence[FileSegment], *, input_hash: str) -> MixDocument:
        input_hash = _ensure_hash(input_hash, "mix.input_hash")
        if len(segments) > self.limits.max_segments or len({item.segment_id for item in segments}) != len(segments):
            raise MixError("AUDIO_SEGMENT_BOUND", "segment count/identity exceeds its bound")
        np = self._runtime()
        audio = _wave(source.path, self.limits.max_source_bytes)
        count = (audio.frames + self.limits.block_frames - 1) // self.limits.block_frames
        if count > self.limits.max_blocks: raise MixError("AUDIO_BLOCK_BOUND", "configured block count exceeds its bound")
        actual_ticks = (audio.frames * source.start.time_base.denominator + audio.rate * source.start.time_base.numerator - 1) // (audio.rate * source.start.time_base.numerator)
        if abs(actual_ticks - (source.end.ticks - source.start.ticks)) > self.config.max_duration_error_ticks:
            raise MixError("SOURCE_DURATION_MISMATCH", "source duration differs from canonical interval")
        self._source_rate, self._source_frames = audio.rate, audio.frames
        self._source_start = map_timepoint_to_sample(source.start, audio.rate)
        failures, valid, metadata, identities, windows, references = [], [], [], [], [], []
        ordered = sorted(segments, key=lambda item: (item.start.ticks, item.end.ticks, item.segment_id))
        for item in ordered:
            status, digest = "failed", None
            try:
                if item.start.time_base != source.start.time_base: raise MixError("TIMELINE_TIME_BASE_MISMATCH", "segment and source time bases differ")
                if item.status != "available": raise MixError("TTS_MISSING" if item.status == "missing" else "TTS_FAILED", item.condition or "TTS has no usable file")
                # Bound and pin even a malformed WAV; its exact rejected input
                # belongs to the generation identity and publication recheck.
                if item.path.is_file() and item.path.stat().st_size <= self.config.max_tts_bytes:
                    digest = file_hash(_plain(item.path)); references.append((item.path, digest))
                tts = _wave(item.path, self.config.max_tts_bytes, item.content_hash); digest = tts.digest
                ticks = (tts.frames * source.start.time_base.denominator + tts.rate * source.start.time_base.numerator - 1) // (tts.rate * source.start.time_base.numerator)
                if abs(ticks - (item.end.ticks - item.start.ticks)) > self.config.max_duration_error_ticks: raise MixError("TTS_DURATION_MISMATCH", "TTS duration differs from slot")
                start, end = map_timepoint_to_sample(item.start, audio.rate), map_timepoint_to_sample(item.end, audio.rate, end=True)
                window = DuckWindow(item.segment_id, start, end,
                    _duration_ticks_to_samples(self.config.attack_ticks, source.start.time_base, audio.rate),
                    _duration_ticks_to_samples(self.config.release_ticks, source.start.time_base, audio.rate), self.config.duck_gain_milli)
                if max(window.attack_samples, window.release_samples) > ((1 << 63) - 1) // 1000:
                    raise MixError("AUDIO_TIMELINE_BOUND", "duck ramp exceeds bounded native integer arithmetic")
                offset = start - self._source_start
                if offset >= audio.frames or offset + (tts.frames * audio.rate + tts.rate - 1) // tts.rate <= 0: raise MixError("TTS_OUTSIDE_SOURCE", "TTS does not overlap source")
                valid.append((item, tts, window, offset)); windows.append(window); status = "completed"
            except (MixError, OSError) as error:
                failures.append(MixFailure(getattr(error, "code", "TTS_READ_FAILED"), item.segment_id, getattr(error, "condition", "TTS file cannot be read"), True, item.fallback_used))
            metadata.append({"segment_id": item.segment_id, "source_utterance_id": item.source_utterance_id, "status": status, "confidence": float(item.confidence), "fallback_used": item.fallback_used})
            identities.append({**metadata[-1], "start": item.start.to_dict(), "end": item.end.to_dict(),
                               "hash": digest, "declared_hash": item.content_hash})
        config_hash = _hash_json({"dsp": self.config.to_dict(), "limits": self.limits.__dict__, "producer": PRODUCER_VERSION, "runtime": NUMPY_VERSION})
        identity = _hash_json({"source": audio.digest, "source_id": source.source_id, "layout": source.layout,
            "start": source.start.to_dict(), "end": source.end.to_dict(), "segments": identities,
            "failures": [item.to_dict() for item in failures], "input": input_hash, "config": config_hash})
        root = _plain(self.output_dir / ("stream-" + identity[7:])); root.mkdir(parents=True, exist_ok=True)
        completed = root / "mix_document.json"
        if completed.exists():
            _plain(completed)
            if completed.stat().st_size > _JOURNAL_LIMIT:
                raise MixError("MIX_CHECKPOINT_BOUND", "completed document exceeds its size bound")
            try:
                document = _restore(parse_mix_json(completed.read_text(encoding="utf-8")))
            except MixError as error:
                raise MixError("MIX_CHECKPOINT_INVALID", "completed mix document cannot be validated") from error
            if (document.provenance.config_hash != config_hash or document.provenance.input_hash != input_hash
                or document.provenance.source_hash != audio.digest or document.source_id != source.source_id
                or document.original_audio.content_hash != audio.digest
                or document.source_layout != source.layout or document.source_start != source.start or document.source_end != source.end
                or document.provenance.producer_version != PRODUCER_VERSION or document.provenance.backend_id != BACKEND_ID
                or document.provenance.runtime != "owned-python/numpy-" + NUMPY_VERSION
                or list(document.segments) != metadata or document.duck_windows != tuple(windows)
                or document.failures != tuple(failures)):
                raise MixError("MIX_CHECKPOINT_INVALID", "completed mix identity differs")
            for artifact, name in zip((document.original_audio, document.dialogue_stem, document.final_mix), ("original.wav", "dialogue.wav", "final.wav")):
                if Path(artifact.path) != root / name or self._artifact(root / name, artifact.kind, audio, np) != artifact:
                    raise MixError("MIX_CHECKPOINT_INVALID", "completed artifact differs from recorded bytes")
            return document
        return self._compute(root, source, audio, valid, windows, metadata, failures, config_hash, input_hash, identity, references, np)

    def _compute(self, root, source, audio, valid, windows, metadata, failures, config_hash, input_hash, identity, references, np):
        pcm_bytes = audio.frames * audio.channels * 2
        if pcm_bytes + 36 > (1 << 32) - 1:
            raise MixError("AUDIO_RIFF_BOUND", "PCM artifact exceeds the RIFF size limit")
        block_bytes = self.limits.block_frames * audio.channels * 2
        block_count = (audio.frames + self.limits.block_frames - 1) // self.limits.block_frames
        source_bytes = audio.path.stat().st_size
        copy_count = (source_bytes + block_bytes - 1) // block_bytes
        if copy_count > self.limits.max_blocks:
            raise MixError("AUDIO_BLOCK_BOUND", "source snapshot exceeds its checkpoint block bound")
        phases = ("copy", "dialogue", "combined", "output")
        counts = {"copy": copy_count, **{phase: block_count for phase in phases[1:]}}
        pinned = {"version": 1, "identity": identity, "producer": PRODUCER_VERSION,
                  "runtime": NUMPY_VERSION, "counts": counts}
        journal = _plain(root / "checkpoint.json")
        if journal.exists():
            if not journal.is_file() or journal.stat().st_size > _JOURNAL_LIMIT:
                raise MixError("MIX_CHECKPOINT_INVALID", "private journal is not a bounded regular file")
            try:
                state = json.loads(journal.read_text(encoding="utf-8"))
                if set(state) != {*pinned, "phase", "block_count", "stages"} or any(state[key] != value for key, value in pinned.items()):
                    raise ValueError("identity")
                if state["phase"] not in phases or state["block_count"] != counts[state["phase"]] or set(state["stages"]) != set(phases):
                    raise ValueError("phase")
                for phase in phases:
                    entries = state["stages"][phase]
                    if not isinstance(entries, list) or len(entries) > counts[phase]: raise ValueError("count")
                    if phases.index(phase) < phases.index(state["phase"]) and len(entries) != counts[phase]: raise ValueError("incomplete earlier phase")
                    if phases.index(phase) > phases.index(state["phase"]) and entries: raise ValueError("future phase")
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                raise MixError("MIX_CHECKPOINT_INVALID", "private journal identity/shape differs") from error
        else:
            # A lost journal cannot confer ownership of existing PCM/output files.
            if any(root.glob("*.pcm")) or any(root.glob("*.wav*")):
                raise MixError("MIX_CHECKPOINT_INVALID", "generation data exists without its ownership journal")
            state = {**pinned, "phase": "copy", "block_count": copy_count, "stages": {phase: [] for phase in phases}}
            self._save(root, state)

        # Maximum additional payload on a new generation: exact source + raw
        # dialogue (S16) + raw combined (S32) + two final PCM16 tracks.
        required = source_bytes + pcm_bytes * 5 + 88
        owned_names = {"original.wav", "original.wav.part", "dialogue.pcm", "combined.pcm",
                       "dialogue.wav", "dialogue.wav.part", "final.wav", "final.wav.part"}
        allocated = sum(_plain(path).stat().st_size for path in root.iterdir() if path.name in owned_names and path.is_file())
        self._disk(root, max(0, required - allocated))
        original = root / "original.wav"
        with audio.path.open("rb") as reader:
            def copy_block(index):
                reader.seek(index * block_bytes)
                payload = reader.read(min(block_bytes, source_bytes - index * block_bytes))
                return [payload]
            self._pass(root, state, "copy", [original], [b""],
                       lambda index: [min(block_bytes, source_bytes - index * block_bytes)], copy_block)
        snapshot = original if original.exists() else original.with_name(original.name + ".part")
        if file_hash(snapshot) != audio.digest:
            raise MixError("AUDIO_INPUT_CHANGED", "source snapshot differs from pinned source bytes")
        self._promote(snapshot, original)

        raw_dialogue, raw_combined = root / "dialogue.pcm", root / "combined.pcm"
        def frame_count(index): return min(self.limits.block_frames, audio.frames - index * self.limits.block_frames)
        with wave.open(str(original), "rb") as reader:
            def dialogue_block(index):
                reader.setpos(index * self.limits.block_frames)
                payload = reader.readframes(frame_count(index))
                if len(payload) != frame_count(index) * audio.channels * 2:
                    raise MixError("AUDIO_INPUT_CHANGED", "source snapshot became truncated")
                values = np.frombuffer(payload, dtype="<i2").reshape(-1, audio.channels).astype(np.int64)
                dialogue, _ = self._block(values, index * self.limits.block_frames, valid, np)
                return [dialogue.astype("<i2").tobytes()]
            self._pass(root, state, "dialogue", [raw_dialogue], [b""],
                       lambda index: [frame_count(index) * audio.channels * 2], dialogue_block, private=True)
        dialogue_factor, dialogue_warnings = self._normalization(self._stats(raw_dialogue, audio, np, "<i2"))

        with wave.open(str(original), "rb") as reader, raw_dialogue.open("rb") as dialogue_reader:
            def combined_block(index):
                offset = index * self.limits.block_frames
                reader.setpos(offset)
                payload = reader.readframes(frame_count(index))
                if len(payload) != frame_count(index) * audio.channels * 2:
                    raise MixError("AUDIO_INPUT_CHANGED", "source snapshot became truncated")
                values = np.frombuffer(payload, dtype="<i2").reshape(-1, audio.channels).astype(np.int64)
                _, ducked = self._block(values, offset, valid, np, with_dialogue=False)
                dialogue_reader.seek(offset * audio.channels * 2)
                dialogue = self._numeric_read(dialogue_reader, frame_count(index), audio.channels, "<i2", np)
                combined = ducked + self._scale(dialogue, dialogue_factor, np)
                return [combined.astype("<i4").tobytes()]
            self._pass(root, state, "combined", [raw_combined], [b""],
                       lambda index: [frame_count(index) * audio.channels * 4], combined_block, private=True)
        final_factor, final_warnings = self._normalization(self._stats(raw_combined, audio, np, "<i4"))
        header = _HEADER.pack(b"RIFF", pcm_bytes + 36, b"WAVE", b"fmt ", 16, 1, audio.channels,
            audio.rate, audio.rate * audio.channels * 2, audio.channels * 2, 16, b"data", pcm_bytes)
        stem, final = root / "dialogue.wav", root / "final.wav"
        with raw_dialogue.open("rb") as dialogue_reader, raw_combined.open("rb") as combined_reader:
            def output_block(index):
                dialogue_reader.seek(index * block_bytes)
                combined_reader.seek(index * block_bytes * 2)
                dialogue = self._numeric_read(dialogue_reader, frame_count(index), audio.channels, "<i2", np)
                combined = self._numeric_read(combined_reader, frame_count(index), audio.channels, "<i4", np)
                return [np.clip(self._scale(values, factor, np), -32768, 32767).astype("<i2").tobytes()
                        for values, factor in ((dialogue, dialogue_factor), (combined, final_factor))]
            self._pass(root, state, "output", [stem, final], [header, header],
                       lambda index: [frame_count(index) * audio.channels * 2] * 2, output_block)

        # Revalidate external inputs before any final artifact promotion. A new
        # caller input selects a different generation; it never rewrites this one.
        if file_hash(audio.path) != audio.digest or any(file_hash(path) != digest for path, digest in references):
            raise MixError("AUDIO_INPUT_CHANGED", "source/TTS changed before publication")
        # Successful write/fsync does not prove the private file still contains
        # those bytes at publication. Verify all committed prefixes against the
        # original journal, including the spools used to produce output. Merely
        # hashing whatever is now on disk would bless silent changed content.
        def no_compute(_index):
            raise MixError("MIX_CHECKPOINT_INVALID", "publication requires every committed stage complete")
        for phase, paths, headers, sizes, private in (
            ("copy", [original], [b""], lambda index: [min(block_bytes, source_bytes - index * block_bytes)], False),
            ("dialogue", [raw_dialogue], [b""], lambda index: [frame_count(index) * audio.channels * 2], True),
            ("combined", [raw_combined], [b""], lambda index: [frame_count(index) * audio.channels * 4], True),
            ("output", [stem, final], [header, header], lambda index: [frame_count(index) * audio.channels * 2] * 2, False),
        ):
            if len(state["stages"][phase]) != state["counts"][phase]:
                raise MixError("MIX_CHECKPOINT_INVALID", "publication requires every committed stage complete")
            self._pass(root, state, phase, paths, headers, sizes, no_compute, private=private)
        candidates = [original, *[path if path.exists() else path.with_name(path.name + ".part") for path in (stem, final)]]
        artifacts = [self._artifact(path, kind, audio, np) for path, kind in
                     zip(candidates, ("original_audio", "dialogue_stem", "final_mix"))]
        if artifacts[0].content_hash != audio.digest:
            raise MixError("MIX_CHECKPOINT_INVALID", "original snapshot differs from pinned source hash")
        for artifact in artifacts[1:]:
            metrics = artifact.metrics
            if metrics.clipped_samples * 1_000_000 > metrics.sample_count * self.config.max_clip_fraction_ppm:
                raise MixError("AUDIO_CLIPPING", "mix clipping exceeds configured fraction")
        for candidate, path in zip(candidates[1:], (stem, final)):
            self._promote(candidate, path)
        artifacts = [replace(artifact, path=str(path)) for artifact, path in zip(artifacts, (original, stem, final))]
        warnings = [*dialogue_warnings, *final_warnings]
        if artifacts[0].metrics.clipped_samples: warnings.append("original source contains clipped samples; it was preserved as supplied")
        if artifacts[2].metrics.rms_milli == 0: warnings.append("final mix is silent because no usable source or TTS energy was available")
        elif artifacts[2].metrics.rms_milli < self.config.target_rms_milli: warnings.append("final mix is below the configured target RMS; source and dialogue were preserved")
        if failures: warnings.append("one or more TTS segments were unavailable; the original source remains in the final mix")
        chunks = []
        for index in range(0, len(metadata), self.config.max_segments_per_chunk):
            group = metadata[index:index + self.config.max_segments_per_chunk]
            ids = [item["segment_id"] for item in group]
            statuses = {item["status"] for item in group}
            status = next(iter(statuses)) if len(statuses) == 1 else "degraded"
            chunk = {"chunk_id": "stream-chunk-" + _hash_json({"identity": identity, "ids": ids})[7:31],
                     "index": len(chunks), "segment_ids": ids, "status": status, "attempt": 1}
            if status != "completed": chunk["error_code"] = next(item.code for item in failures if item.segment_id in ids)
            chunks.append(chunk)
        document = MixDocument(source.source_id, tuple(item["segment_id"] for item in metadata), tuple(metadata),
            source.layout, source.start, source.end, audio.rate, audio.channels, *artifacts, tuple(windows), tuple(chunks),
            MixProvenance("dubflow-aud-0", PRODUCER_VERSION, BACKEND_ID, "owned-python/numpy-" + NUMPY_VERSION,
                "timeline-v1", config_hash, audio.digest, input_hash, self.config.requested_profile, "cpu", self.config.resource),
            tuple(failures), tuple(dict.fromkeys(warnings)))
        validate_mix_document(document.to_dict())
        _atomic(root / "mix_document.json", document.to_json().encode("utf-8"))
        return document

    def _pass(self, root, state, phase, paths, headers, sizes, compute, *, private=False):
        """Verify committed prefixes, discard only uncommitted private tails.

        At most two output handles and one block of each output are active.
        The caller opens at most two input handles; TTS uses one extra reader.
        """
        entries = state["stages"][phase]
        count = state["counts"][phase]
        handles = []
        try:
            for path, header in zip(paths, headers):
                path = _plain(path)
                candidate = path if private or path.exists() else path.with_name(path.name + ".part")
                _plain(candidate)
                if candidate.exists() and not candidate.is_file():
                    raise MixError("MIX_CHECKPOINT_INVALID", "checkpoint output is not a regular file")
                public = not private and candidate == path
                if public and len(entries) != count:
                    raise MixError("MIX_CHECKPOINT_INVALID", "partial stage has a published artifact")
                if not candidate.exists() and entries:
                    raise MixError("MIX_CHECKPOINT_INVALID", "checkpoint output is missing")
                handle = candidate.open("r+b" if candidate.exists() else "w+b")
                handles.append((handle, public))
                if handle.seek(0, os.SEEK_END) == 0 and not entries:
                    handle.write(header); handle.flush(); os.fsync(handle.fileno())
                handle.seek(0)
                if handle.read(len(header)) != header:
                    raise MixError("MIX_CHECKPOINT_INVALID", "checkpoint WAV header differs")
            for index, entry in enumerate(entries):
                expected = sizes(index)
                if (not isinstance(entry, dict) or set(entry) != {"sizes", "hashes"} or entry["sizes"] != expected
                    or not isinstance(entry["hashes"], list) or len(entry["hashes"]) != len(handles)):
                    raise MixError("MIX_CHECKPOINT_INVALID", "checkpoint block shape differs")
                for (handle, _), size, digest in zip(handles, expected, entry["hashes"]):
                    if type(size) is not int or size < 1 or not isinstance(digest, str):
                        raise MixError("MIX_CHECKPOINT_INVALID", "checkpoint block metadata differs")
                    payload = handle.read(size)
                    if len(payload) != size or "sha256:" + sha256(payload).hexdigest() != digest:
                        raise MixError("MIX_CHECKPOINT_INVALID", "committed PCM block differs from its hash")
            for handle, public in handles:
                committed = handle.tell()
                if public and handle.seek(0, os.SEEK_END) != committed:
                    raise MixError("MIX_CHECKPOINT_INVALID", "published artifact has an uncommitted tail")
                handle.seek(committed)
                if not public: handle.truncate(committed)
            if len(entries) == count: return
            # Advance only after every previous phase has been fully verified.
            state["phase"], state["block_count"] = phase, count
            self._save(root, state)
            for index in range(len(entries), count):
                expected = sizes(index)
                self._disk(root, sum(expected))
                payloads = compute(index)
                if len(payloads) != len(handles) or [len(payload) for payload in payloads] != expected:
                    raise MixError("MIX_BLOCK_INVALID", "computed block differs from bounded shape")
                for (handle, _), payload in zip(handles, payloads):
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                entries.append({"sizes": expected, "hashes": ["sha256:" + sha256(payload).hexdigest() for payload in payloads]})
                self._save(root, state)
        finally:
            for handle, _ in handles: handle.close()

    @staticmethod
    def _promote(candidate, path):
        _plain(candidate); _plain(path)
        if path.exists():
            if candidate != path and candidate.exists():
                raise MixError("MIX_CHECKPOINT_INVALID", "both published and partial artifact exist")
            return
        os.replace(candidate, path)

    @staticmethod
    def _numeric_read(handle, frames, channels, dtype, np):
        payload = handle.read(frames * channels * np.dtype(dtype).itemsize)
        if len(payload) != frames * channels * np.dtype(dtype).itemsize:
            raise MixError("MIX_CHECKPOINT_INVALID", "private PCM spool is truncated")
        return np.frombuffer(payload, dtype=dtype).reshape(-1, channels).astype(np.int64)

    def _stats(self, path, audio, np, dtype, *, wav=False):
        result = _Stats()
        with (wave.open(str(path), "rb") if wav else path.open("rb")) as reader:
            for offset in range(0, audio.frames, self.limits.block_frames):
                count = min(self.limits.block_frames, audio.frames - offset)
                if wav:
                    payload = reader.readframes(count)
                    if len(payload) != count * audio.channels * 2:
                        raise MixError("MIX_CHECKPOINT_INVALID", "artifact PCM payload is truncated")
                    values = np.frombuffer(payload, dtype="<i2").reshape(-1, audio.channels).astype(np.int64)
                else: values = self._numeric_read(reader, count, audio.channels, dtype, np)
                result.add(values, np)
        return result

    def _artifact(self, path, kind, audio, np):
        actual = _wave(path, self.limits.max_source_bytes)
        if (actual.rate, actual.channels, actual.frames) != (audio.rate, audio.channels, audio.frames):
            raise MixError("MIX_CHECKPOINT_INVALID", "artifact header differs from source shape")
        stats = self._stats(path, audio, np, "<i2", wav=True)
        return MixArtifact(kind.replace("_", "-"), kind, str(path), actual.digest, "wav", audio.rate,
                           audio.channels, 16, audio.frames, stats.metrics(actual.digest))
