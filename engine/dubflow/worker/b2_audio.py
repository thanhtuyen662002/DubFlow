"""B2 production audio stage used by the local-file worker.

FFmpeg decodes the actual source audio, a pinned offline neural CPU voice creates
per-cue WAV artifacts, and the bounded file-based AUD-0 mixer publishes original,
dialogue-stem and ducked final WAVs.  The worker can catch ``B2AudioError``
and continue with the already-valid B1 subtitle render.

Native inference is not deterministic or a speech-quality qualification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import wave
from typing import Any, Iterable, Mapping, Sequence

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.media import FfmpegMediaAdapter, MediaAdapterError, MediaProbeResult
from engine.dubflow.mix import FileSegment, FileSource, StreamingAudioMixer, MixConfig, MixDocument, ResourceProfile as MixResourceProfile
from engine.dubflow.mix import streaming
from engine.dubflow.tts import (
    LocalTtsAdapter,
    ResourceProfile as TtsResourceProfile,
    TtsConfig,
    TtsDocument,
    TtsInput,
    TtsProvenance,
    TtsStageError,
    VoiceProfile,
)
from engine.dubflow.tts.neural_vits import ENGINE_ID, ONNX_RUNTIME_VERSION, RUNTIME_VERSION, NeuralVietnameseTtsEngine, load_neural_voice
from engine.dubflow.tts.neural_vits import _child, _read_json, _digest
from engine.dubflow.tts import vieneu
from .tts_checkpoints import TtsCheckpointStore


BASE_TIME = TimeBase(1, 1000)


def tts_recipe_identity(app_root: Path, profile_path: Path) -> str:
    try:
        return _tts_recipe_identity(app_root, profile_path)
    except Exception as error:
        raise B2AudioError(getattr(error, "code", "TTS_RECIPE_UNAVAILABLE"), "selected TTS recipe cannot be verified") from error


def _tts_recipe_identity(app_root: Path, profile_path: Path) -> str:
    """Private B2 generations pin the selected recipe and native adapter code."""
    profile = _read_json(profile_path)
    relative = profile.get("tts_neural_profile")
    if not relative:
        return "legacy-profile-without-neural-recipe"
    metadata = _child(app_root, relative)
    source = Path(__file__).parents[1] / "tts"
    paths = (metadata, source / "adapter.py", source / "vieneu.py", source / "vieneu_native.py", source / "neural_vits.py", source / "mimic3_native.py", source / "native_process.py", source / "windows_job.py", Path(__file__).with_name("tts_checkpoints.py"))
    return sha256("".join(_digest(path) for path in paths).encode()).hexdigest()


def mix_recipe_identity() -> str:
    """Pin the selected PCM producer and code before reusing a B2 generation."""
    try:
        recipe = {"producer": streaming.PRODUCER_VERSION, "backend": streaming.BACKEND_ID,
                  "numpy": streaming.NUMPY_VERSION, "adapter": _digest(Path(streaming.__file__))}
        return sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, TypeError, ValueError) as error:
        raise B2AudioError("MIX_RECIPE_UNAVAILABLE", "selected mixer recipe cannot be verified") from error


class B2AudioError(RuntimeError):
    """Typed, safe-to-report B2 failure that permits a B1 downgrade."""

    def __init__(self, code: str, condition: str, *, retryable: bool = False) -> None:
        self.code = str(code)[:128]
        self.condition = " ".join(str(condition).replace("\x00", " ").split())[:4096]
        self.retryable = bool(retryable)
        super().__init__(f"{self.code}: {self.condition}")


@dataclass(frozen=True)
class B2AudioResult:
    source_audio_path: Path
    tts_document_path: Path
    mix_document_path: Path
    tts_document: TtsDocument
    mix_document: MixDocument
    voice: VoiceProfile

    @property
    def final_mix_path(self) -> Path:
        return Path(self.mix_document.final_mix.path)

    @property
    def dialogue_stem_path(self) -> Path:
        return Path(self.mix_document.dialogue_stem.path)

    @property
    def original_audio_path(self) -> Path:
        return Path(self.mix_document.original_audio.path)


def _input_hash(cues: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(cues), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def _wav_duration_ticks(path: Path, *, denominator: int = 1000) -> int:
    try:
        with wave.open(str(path), "rb") as reader:
            sample_rate = reader.getframerate()
            frame_count = reader.getnframes()
    except (OSError, EOFError, wave.Error) as error:
        raise B2AudioError("SOURCE_AUDIO_INVALID", f"decoded source WAV cannot be read: {error}") from error
    if sample_rate <= 0 or frame_count <= 0:
        raise B2AudioError("SOURCE_AUDIO_INVALID", "decoded source WAV has no positive samples")
    # Ceiling preserves the complete final sample in the canonical timeline.
    return (frame_count * denominator + sample_rate - 1) // sample_rate


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _source_language(value: str) -> str:
    value = str(value or "und")
    return value if value != "auto" and len(value) <= 3 and value.isalpha() else "und"


def _cue_mapping(cue: Any) -> dict[str, Any]:
    cue_id = str(getattr(cue, "cue_id"))
    start = int(getattr(cue, "start_ms"))
    end = int(getattr(cue, "end_ms"))
    source = str(getattr(cue, "source_text"))
    translated = str(getattr(cue, "translated_text") or source)
    confidence = float(getattr(cue, "confidence", 1.0))
    return {
        "cue_id": cue_id,
        "start_ms": start,
        "end_ms": end,
        "source_text": source,
        "translated_text": translated,
        "confidence": confidence,
    }


def run_b2_audio(
    *,
    media: FfmpegMediaAdapter,
    source_path: str | Path,
    source_probe: MediaProbeResult,
    translated_cues: Sequence[Any],
    source_language: str,
    app_root: str | Path,
    profile_path: str | Path,
    work_dir: str | Path,
    model_root: str | Path | None = None,
    tts_voice_id: str | None = None,
) -> B2AudioResult:
    """Execute real TTS and AUD-0 mixing for translated production cues."""

    if not source_probe.has_audio:
        raise B2AudioError("AUDIO_STREAM_MISSING", "source media has no audio stream")
    if not translated_cues:
        raise B2AudioError("TTS_INPUT_EMPTY", "translated production cues are empty")
    root = Path(work_dir).absolute()
    root.mkdir(parents=True, exist_ok=True)
    try:
        selection = _read_json(Path(profile_path))
        recipe = _read_json(_child(Path(app_root).absolute(), selection.get("tts_neural_profile", "")))
        if recipe.get("backend") == vieneu.ENGINE_ID:
            pack, voice = vieneu.load_vieneu_voice(app_root, model_root or root / "model-cache", profile_path, voice_id=tts_voice_id)
            engine = vieneu.VieNeuVietnameseTtsEngine(pack, ffmpeg_path=media.ffmpeg_path)
            backend_id, producer_version, runtime_id = vieneu.ENGINE_ID, vieneu.PRODUCER_VERSION, vieneu.RUNTIME_ID
            resources = TtsResourceProfile(max_threads=2, max_memory_mb=2048, max_batch_items=1)
        elif recipe.get("backend") == ENGINE_ID:
            if tts_voice_id is not None and tts_voice_id != recipe.get("voice_id"):
                raise B2AudioError("VOICE_ID_UNKNOWN", "requested voice is not provided by the historical backend")
            pack, voice = load_neural_voice(app_root, model_root or root / "model-cache", profile_path)
            engine = NeuralVietnameseTtsEngine(pack)
            backend_id, producer_version = ENGINE_ID, "2.1.0"
            runtime_id = "onnxruntime-" + ONNX_RUNTIME_VERSION + "+espeak-sherpa-" + RUNTIME_VERSION
            resources = TtsResourceProfile(max_threads=1, max_memory_mb=512, max_batch_items=8)
        else:
            raise B2AudioError("TTS_BACKEND_UNSUPPORTED", "selected production TTS backend is unsupported")
    except Exception as error:
        raise B2AudioError(getattr(error, "code", "TTS_BOOTSTRAP_FAILED"), str(error), retryable=bool(getattr(error, "retryable", False))) from error
    source_audio_path = root / "source_audio.wav"
    try:
        media.extract_audio(source_path, source_audio_path, sample_rate=pack.sample_rate, channels=1, overwrite=True)
    except MediaAdapterError as error:
        raise B2AudioError(error.code, error.condition, retryable=error.retryable) from error
    source_end_ticks = _wav_duration_ticks(source_audio_path)
    if source_end_ticks <= 0:
        raise B2AudioError("SOURCE_AUDIO_INVALID", "decoded source audio has no duration")

    try:
        tts_config = TtsConfig(
            sample_rate=pack.sample_rate,
            channels=pack.channels,
            requested_profile="cpu",
            max_attempts=1,
            max_text_chars=512,
            max_duration_error_ticks=80,
            min_speed_ratio_milli=800,
            max_speed_ratio_milli=1300,
            resource=resources,
        )
        mappings = tuple(_cue_mapping(cue) for cue in translated_cues)
        input_hash = _input_hash(mappings)
        provenance = TtsProvenance(
            "dubflow-production-tts",
            producer_version,
            backend_id,
            runtime_id,
            "timeline-v1",
            tts_config.content_hash(),
            input_hash,
            voice.model_id,
            voice.model_version,
            voice.model_hash,
            voice.content_hash(),
            voice.voice_id,
            voice.voice_version,
            "cpu",
            "cpu",
            tts_config.resource,
        )
        segments = tuple(
            TtsInput(
                item["cue_id"],
                item["cue_id"],
                item["translated_text"],
                TimePoint(item["start_ms"], BASE_TIME),
                TimePoint(item["end_ms"], BASE_TIME),
                source_language=_source_language(source_language),
                confidence=item["confidence"],
            )
            for item in mappings
        )
        tts_dir = root / "tts"
        checkpoint_identity = sha256(json.dumps({"provenance": provenance.to_dict(),
            "recipe": tts_recipe_identity(Path(app_root), Path(profile_path))},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checkpoints = TtsCheckpointStore(tts_dir, identity=checkpoint_identity)
        tts_document = LocalTtsAdapter(
            engine,
            config=tts_config,
            provenance=provenance,
            voice=voice,
            output_dir=tts_dir,
        ).synthesize(segments, input_hash=input_hash, checkpoints=checkpoints.load(segments),
                     on_checkpoint=checkpoints.commit)
        if checkpoints.warnings:
            tts_document = replace(tts_document, warnings=tts_document.warnings + tuple(checkpoints.warnings))
    except TtsStageError as error:
        raise B2AudioError("TTS_FAILED", str(error), retryable=error.retryable) from error
    except Exception as error:
        code = getattr(error, "code", "TTS_BOOTSTRAP_FAILED")
        condition = getattr(error, "condition", str(error))
        raise B2AudioError(code, condition, retryable=bool(getattr(error, "retryable", False))) from error
    finally:
        close = getattr(engine, "close", None)
        if close is not None:
            close()

    tts_document_path = root / "tts_document.json"
    try:
        _atomic_bytes(tts_document_path, tts_document.to_bytes())
    except OSError as error:
        raise B2AudioError("TTS_ARTIFACT_WRITE_FAILED", str(error), retryable=True) from error
    try:
        artifacts_by_id = {artifact.segment_id: artifact for artifact in tts_document.artifacts}
        segments_for_mix: list[FileSegment] = []
        for item in mappings:
            identifier = item["cue_id"]
            if identifier in artifacts_by_id:
                segments_for_mix.append(FileSegment.from_tts_artifact(artifacts_by_id[identifier]))
            else:
                segments_for_mix.append(
                    FileSegment(
                        identifier,
                        identifier,
                        TimePoint(item["start_ms"], BASE_TIME),
                        TimePoint(item["end_ms"], BASE_TIME),
                        status="failed",
                        condition="TTS segment did not produce a usable WAV artifact",
                        confidence=item["confidence"],
                    )
                )
        source = FileSource(
            source_audio_path,
            start=TimePoint(0, BASE_TIME),
            end=TimePoint(source_end_ticks, BASE_TIME),
            source_id=Path(source_path).name,
            layout="mono-source",
        )
        mixer = StreamingAudioMixer(
            config=MixConfig(
                requested_profile="cpu",
                resource=MixResourceProfile(max_threads=1, max_memory_mb=512, max_batch_items=8),
            ),
            output_dir=root / "mix",
        )
        mix_document = mixer.mix(
            source,
            tuple(segments_for_mix),
            input_hash="sha256:" + sha256(tts_document.to_bytes()).hexdigest(),
        )
    except Exception as error:
        code = getattr(error, "code", "AUDIO_MIX_FAILED")
        condition = getattr(error, "condition", str(error))
        raise B2AudioError(code, condition, retryable=bool(getattr(error, "retryable", False))) from error
    if not tts_document.artifacts:
        raise B2AudioError("TTS_FAILED", "no translated cue produced a usable dialogue waveform")
    if len(mix_document.failures) >= len(segments_for_mix):
        raise B2AudioError("AUDIO_MIX_DEGRADED", "every translated cue failed the AUD-0 mix")
    mix_document_path = root / "mix_document.json"
    try:
        _atomic_bytes(mix_document_path, (mix_document.to_json() + "\n").encode("utf-8"))
    except OSError as error:
        raise B2AudioError("MIX_ARTIFACT_WRITE_FAILED", str(error), retryable=True) from error
    return B2AudioResult(source_audio_path, tts_document_path, mix_document_path, tts_document, mix_document, voice)


__all__ = ["B2AudioError", "B2AudioResult", "BASE_TIME", "run_b2_audio", "mix_recipe_identity"]
