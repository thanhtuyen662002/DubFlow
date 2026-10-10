"""Production local-file worker for the CPU baseline profile.

The worker is intentionally a protocol child.  It never opens the supervisor
durable database and it never decides durable job state.  A command contains a
source file and an output directory; this module performs real media I/O and
emits progress/checkpoint/artifact evidence through the versioned JSONL wire
contract.

The default packaged profile uses app-owned FFmpeg/FFprobe and pinned local
model packs.  Fixture adapters are not imported here.  A sidecar SRT/VTT is a
supported deterministic source-text input for offline recovery and subtitle
authoring; a missing sidecar selects the app-owned faster-whisper ASR pack.
Translation and optional TTS are also app-owned lazy adapters. A missing voice
pack produces an explicit downgrade in provenance while preserving the valid
subtitle result instead of silently returning fixture text.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
import wave
from typing import Any, Iterable, Mapping, Sequence


def _prepare_worker_import_path() -> None:
    """Put app code after the bundled runtime's third-party packages.

    The repository contains a top-level ``packaging`` package for release
    policy code.  Argos Translate (and other production dependencies) also
    imports the third-party ``packaging`` distribution.  The supervisor sets
    ``PYTHONPATH`` to the app root so that this worker can import ``engine``;
    Python would otherwise let the repository package shadow the bundled
    distribution and fail at ``packaging.version``.  Keep the app root
    available, but append it after normal site-packages so the app-owned
    runtime wins dependency resolution.
    """

    if os.environ.get("DUBFLOW_WORKER_PROCESS") != "1":
        return
    app_root = Path(__file__).resolve().parents[3]
    try:
        app_root_key = os.path.normcase(os.path.realpath(os.fspath(app_root)))
    except OSError:
        return
    retained: list[str] = []
    for entry in sys.path:
        candidate = entry or os.getcwd()
        try:
            candidate_key = os.path.normcase(os.path.realpath(candidate))
        except OSError:
            retained.append(entry)
            continue
        if candidate_key != app_root_key:
            retained.append(entry)
    retained.append(os.fspath(app_root))
    sys.path[:] = retained


_prepare_worker_import_path()

from engine.dubflow.media import CANONICAL_TIME_BASE, FfmpegMediaAdapter, MediaAdapterError, MediaProbe, MediaProbeResult, MediaTimeline
from engine.dubflow.models import ModelBootstrapError, ensure_model_profile
from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.translation.adapter import SourceSegment, TranslationError, resolve_source_language
from engine.dubflow.translation.argos_runtime import ArgosRuntime
from engine.dubflow.worker.b2_audio import B2AudioError, B2AudioResult, run_b2_audio
from engine.dubflow.worker import export_publication
from engine.dubflow.worker.protocol import Envelope, MessageType, ProtocolError


SCHEMA_VERSION = 1
TIMELINE_DENOMINATOR = 1000
_MAX_PATH_CHARS = 32_768
_SRT_TIME = re.compile(r"^(?P<hours>\d{2}):(?P<minutes>\d{2}):(?P<seconds>\d{2})[,.](?P<millis>\d{3})$")
_VTT_TIME = re.compile(r"^(?:(?P<hours>\d{2}):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})\.(?P<millis>\d{3})$")


class ProductionJobError(RuntimeError):
    """A typed worker failure safe to show in UI diagnostics."""

    def __init__(self, code: str, condition: str, *, retryable: bool = False) -> None:
        self.code = code[:128]
        self.condition = " ".join(str(condition).replace("\x00", " ").split())[:4096]
        self.retryable = retryable
        super().__init__(f"{self.code}: {self.condition}")


@dataclass(frozen=True)
class TextCue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.cue_id or len(self.cue_id) > 128:
            raise ValueError("cue_id is invalid")
        if type(self.start_ms) is not int or type(self.end_ms) is not int or not 0 <= self.start_ms < self.end_ms:
            raise ValueError("cue interval is invalid")
        if not isinstance(self.source_text, str) or not self.source_text.strip() or len(self.source_text) > 16_384:
            raise ValueError("cue source text is invalid")
        if self.translated_text is not None and (not isinstance(self.translated_text, str) or not self.translated_text.strip()):
            raise ValueError("cue translation is invalid")
        if type(self.confidence) not in (int, float) or not math.isfinite(float(self.confidence)) or not 0 <= float(self.confidence) <= 1:
            raise ValueError("cue confidence is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WorkerConfig:
    job_id: str
    stage_id: str
    source_path: Path
    output_dir: Path
    app_root: Path
    model_root: Path
    media_runtime_root: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    source_language: str = "auto"
    target_language: str = "vi"
    enable_dubbing: bool = False
    tts_voice_id: str | None = None
    burn_in_subtitles: bool = True
    translation_package: Path | None = None
    tts_model: Path | None = None
    tts_config: Path | None = None
    checkpoint_path: Path | None = None

    @classmethod
    def from_args(cls, args: Mapping[str, Any]) -> "WorkerConfig":
        required = ("job_id", "stage_id", "source_path", "output_dir", "app_root", "model_root", "media_runtime_root", "ffmpeg_path", "ffprobe_path")
        missing = [name for name in required if not isinstance(args.get(name), str) or not args[name].strip()]
        if missing:
            raise ProductionJobError("COMMAND_INVALID", f"missing command arguments: {', '.join(missing)}")

        def absolute(name: str) -> Path:
            value = Path(str(args[name])).expanduser()
            if not value.is_absolute() or len(str(value)) > _MAX_PATH_CHARS:
                raise ProductionJobError("COMMAND_INVALID", f"{name} must be an absolute path")
            return value.resolve()

        optional_path = lambda name: absolute(name) if isinstance(args.get(name), str) and args[name].strip() else None
        def optional_bool(name: str, default: bool) -> bool:
            value = args.get(name, default)
            if type(value) is not bool:
                raise ProductionJobError("COMMAND_INVALID", f"{name} must be a boolean")
            return value

        voice_id = args.get("tts_voice_id")
        if voice_id is not None and (not isinstance(voice_id, str) or re.fullmatch(r"[a-z0-9-]{1,96}", voice_id) is None):
            raise ProductionJobError("COMMAND_INVALID", "tts_voice_id must be a bounded preset identifier")

        config = cls(
            job_id=str(args["job_id"]),
            stage_id=str(args["stage_id"]),
            source_path=absolute("source_path"),
            output_dir=absolute("output_dir"),
            app_root=absolute("app_root"),
            model_root=absolute("model_root"),
            media_runtime_root=absolute("media_runtime_root"),
            ffmpeg_path=absolute("ffmpeg_path"),
            ffprobe_path=absolute("ffprobe_path"),
            source_language=str(args.get("source_language") or "auto"),
            target_language=str(args.get("target_language") or "vi"),
            enable_dubbing=optional_bool("enable_dubbing", False),
            tts_voice_id=voice_id,
            burn_in_subtitles=optional_bool("burn_in_subtitles", True),
            translation_package=optional_path("translation_package"),
            tts_model=optional_path("tts_model"),
            tts_config=optional_path("tts_config"),
            checkpoint_path=optional_path("checkpoint_path"),
        )
        if config.target_language != "vi":
            raise ProductionJobError("LANGUAGE_UNSUPPORTED", "the production baseline currently supports Vietnamese output only")
        if not config.source_path.is_file():
            raise ProductionJobError("MEDIA_INPUT_UNAVAILABLE", f"source file is unavailable: {config.source_path}")
        if not config.app_root.is_dir() or not config.media_runtime_root.is_dir():
            raise ProductionJobError("RUNTIME_ROOT_UNAVAILABLE", "app-owned application root is unavailable")
        try:
            config.ffmpeg_path.relative_to(config.media_runtime_root)
            config.ffprobe_path.relative_to(config.media_runtime_root)
        except ValueError as error:
            raise ProductionJobError("RUNTIME_ROOT_UNSAFE", "media executables must be inside the app-owned media runtime root") from error
        return config


class _Emitter:
    """Thread-safe protocol output with periodic heartbeats."""

    def __init__(self, job_id: str, stage_id: str) -> None:
        self.job_id = job_id
        self.stage_id = stage_id
        self.sequence = 1
        self._message_counter = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat_loop, name="dubflow-worker-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def send(self, message_type: MessageType, payload: dict[str, Any]) -> None:
        with self._lock:
            self._message_counter += 1
            envelope = Envelope.create(
                message_type,
                f"worker-{self._message_counter}-{uuid.uuid4().hex[:8]}",
                self.job_id,
                self.stage_id,
                self.sequence,
                payload,
            )
            self.sequence += 1
            sys.stdout.buffer.write(envelope.to_line())
            sys.stdout.buffer.flush()

    def progress(self, fraction: float, detail: str, *, units_done: int | None = None, units_total: int | None = None) -> None:
        payload: dict[str, Any] = {"fraction": max(0.0, min(1.0, float(fraction))), "detail": detail[:4096]}
        if units_done is not None:
            payload["units_done"] = max(0, int(units_done))
        if units_total is not None:
            payload["units_total"] = max(0, int(units_total))
        self.send(MessageType.PROGRESS, payload)

    def checkpoint(self, checkpoint_id: str, artifact_hash: str | None = None) -> None:
        payload: dict[str, Any] = {"checkpoint_id": checkpoint_id, "reusable": True}
        if artifact_hash:
            payload["artifact_hash"] = artifact_hash
        self.send(MessageType.CHECKPOINT, payload)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(5):
            try:
                self.send(MessageType.HEARTBEAT, {"monotonic_ms": int(time.monotonic() * 1000)})
            except (BrokenPipeError, OSError):
                self._stop.set()
                return


def _sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ProductionJobError("ARTIFACT_READ_FAILED", str(error), retryable=True) from error
    return "sha256:" + digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ProductionJobError("CHECKPOINT_WRITE_FAILED", str(error), retryable=True) from error


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ProductionJobError("CHECKPOINT_WRITE_FAILED", str(error), retryable=True) from error


def _copy_editable_artifact(source: Path, target: Path, *, expected_hash: str) -> None:
    """Publish one verified asset with bounded memory and preserve its prior file.

    Completed assets can be reused after interruption; an incomplete file never
    replaces the destination. The enclosing export transaction preserves the
    preceding validated video and editable directory until all assets pass QC.
    """
    export_publication.plain(source.absolute())
    export_publication.plain(target.absolute())
    if target.is_file() and _sha256(target) == expected_hash:
        if _sha256(source) != expected_hash:
            raise ProductionJobError("EDITABLE_ARTIFACT_CHANGED", "editable source differs from validated provenance")
        return
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        size = source.stat().st_size
        if shutil.disk_usage(target.parent).free < size + 16 * 1024 * 1024:
            raise ProductionJobError("EDITABLE_STORAGE_INSUFFICIENT", "not enough storage for the next editable asset")
        digest = sha256()
        copied = 0
        with source.open("rb") as reader, temporary.open("xb") as writer:
            before = os.fstat(reader.fileno())
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
                digest.update(block)
                copied += len(block)
                if copied > size:
                    raise ProductionJobError("EDITABLE_ARTIFACT_CHANGED", "editable source grew during copying")
            after = os.fstat(reader.fileno())
            writer.flush()
            os.fsync(writer.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or copied != size or "sha256:" + digest.hexdigest() != expected_hash or _sha256(temporary) != expected_hash:
            raise ProductionJobError("EDITABLE_ARTIFACT_CHANGED", "editable asset bytes differ from validated provenance")
        os.replace(temporary, target)
    except OSError as error:
        raise ProductionJobError("EDITABLE_COPY_FAILED", str(error), retryable=True) from error
    finally:
        temporary.unlink(missing_ok=True)


def _read_checkpoint(path: Path, source_hash: str) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "source_hash": source_hash, "stages": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionJobError("CHECKPOINT_INVALID", f"unable to read checkpoint: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or value.get("source_hash") != source_hash or not isinstance(value.get("stages"), dict):
        raise ProductionJobError("CHECKPOINT_CONFLICT", "checkpoint belongs to a different source or worker schema")
    return value


def _write_stage(checkpoint: dict[str, Any], path: Path, name: str, result: Mapping[str, Any]) -> None:
    checkpoint.setdefault("stages", {})[name] = {"status": "completed", **result}
    _atomic_json(path, checkpoint)


def _stage_ready(checkpoint: Mapping[str, Any], name: str, required_paths: Iterable[Path]) -> bool:
    stage = checkpoint.get("stages", {}).get(name)
    if not isinstance(stage, Mapping) or stage.get("status") != "completed":
        return False
    paths = tuple(required_paths)
    try:
        return bool(paths) and all(
            path.is_file() and path.stat().st_size > 0
            and stage.get("sha256" if len(paths) == 1 else f"{path.suffix[1:]}_sha256") == _sha256(path)
            for path in paths
        )
    except OSError:
        return False


def _parse_timestamp(value: str) -> int:
    match = _SRT_TIME.fullmatch(value.strip())
    if not match:
        raise ProductionJobError("SUBTITLE_INVALID", f"invalid subtitle timestamp: {value}")
    return (
        int(match.group("hours")) * 3_600_000
        + int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + int(match.group("millis"))
    )


def _parse_srt(path: Path) -> tuple[TextCue, ...]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ProductionJobError("SUBTITLE_READ_FAILED", str(error)) from error
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    cues: list[TextCue] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 3:
            continue
        timing_index = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[timing_index]:
            continue
        start_text, end_text = [part.strip() for part in lines[timing_index].split("-->", 1)]
        start_ms, end_ms = _parse_timestamp(start_text), _parse_timestamp(end_text)
        source = " ".join(lines[timing_index + 1:]).strip()
        if source and end_ms > start_ms:
            cues.append(TextCue(f"cue-{index}", start_ms, end_ms, source))
    if not cues:
        raise ProductionJobError("SUBTITLE_EMPTY", f"no usable cues found in {path}")
    return tuple(cues)


def _parse_vtt_timestamp(value: str) -> int:
    match = _VTT_TIME.fullmatch(value.strip())
    if not match:
        raise ProductionJobError("SUBTITLE_INVALID", f"invalid WebVTT timestamp: {value}")
    return (
        int(match.group("hours") or 0) * 3_600_000
        + int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + int(match.group("millis"))
    )


def _parse_vtt(path: Path) -> tuple[TextCue, ...]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ProductionJobError("SUBTITLE_READ_FAILED", str(error)) from error
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    cues: list[TextCue] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        timing_index = next((position for position, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = [part.strip().split()[0] for part in lines[timing_index].split("-->", 1)]
        start_ms, end_ms = _parse_vtt_timestamp(start_text), _parse_vtt_timestamp(end_text)
        source = " ".join(lines[timing_index + 1:]).strip()
        if source and end_ms > start_ms:
            cues.append(TextCue(f"cue-{index}", start_ms, end_ms, source))
    if not cues:
        raise ProductionJobError("SUBTITLE_EMPTY", f"no usable WebVTT cues found in {path}")
    return tuple(cues)


def _cues_from_json(path: Path) -> tuple[TextCue, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionJobError("CHECKPOINT_INVALID", f"unable to restore cues from {path}: {error}") from error
    values = document.get("cues") if isinstance(document, dict) else None
    if not isinstance(values, list) or not values:
        raise ProductionJobError("CHECKPOINT_INVALID", f"checkpoint contains no cues: {path}")
    cues: list[TextCue] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ProductionJobError("CHECKPOINT_INVALID", f"checkpoint cue is invalid: {path}")
        try:
            cues.append(
                TextCue(
                    str(value["cue_id"]),
                    int(value["start_ms"]),
                    int(value["end_ms"]),
                    str(value["source_text"]),
                    None if value.get("translated_text") is None else str(value["translated_text"]),
                    float(value.get("confidence", 1.0)),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProductionJobError("CHECKPOINT_INVALID", f"checkpoint cue is invalid: {path}") from error
    return tuple(cues)


def _format_timestamp(milliseconds: int, *, ass: bool = False) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_subtitles(output_dir: Path, cues: Sequence[TextCue]) -> tuple[Path, Path]:
    srt = output_dir / "captions_vi.srt"
    ass = output_dir / "captions_vi.ass"
    srt_text = "\n\n".join(
        f"{index}\n{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}\n{cue.translated_text or cue.source_text}"
        for index, cue in enumerate(cues, start=1)
    ) + "\n\n"
    ass_text = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,48,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n""" + "\n".join(
        f"Dialogue: 0,{_format_timestamp(cue.start_ms, ass=True)},{_format_timestamp(cue.end_ms, ass=True)},Default,,0,0,0,,{(cue.translated_text or cue.source_text).replace(chr(10), r'\\N')}"
        for cue in cues
    ) + "\n"
    try:
        _atomic_bytes(srt, srt_text.encode("utf-8"))
        _atomic_bytes(ass, ass_text.encode("utf-8"))
    except (OSError, ProductionJobError) as error:
        raise ProductionJobError("SUBTITLE_WRITE_FAILED", str(error), retryable=True) from error
    return srt, ass


def _validate_rendered_audio(source_probe: MediaProbeResult, output_probe: MediaProbeResult) -> None:
    """Ensure rendering did not silently drop the source audio stream."""

    if source_probe.has_audio and not output_probe.has_audio:
        raise ProductionJobError("QC_AUDIO_MISSING", "rendered output lost source audio")
    if output_probe.has_audio and any(
        stream.codec_name not in {"aac", "mp3", "opus", "vorbis"}
        for stream in output_probe.audio
    ):
        raise ProductionJobError("QC_AUDIO_CODEC", "output audio codec is not a supported playable profile")


def _load_sidecar(source: Path) -> tuple[TextCue, ...] | None:
    for suffix in (".srt", ".SRT"):
        candidate = source.with_suffix(suffix)
        if candidate.is_file():
            return _parse_srt(candidate)
    for suffix in (".vtt", ".VTT"):
        candidate = source.with_suffix(suffix)
        if candidate.is_file():
            return _parse_vtt(candidate)
    return None


@dataclass(frozen=True)
class TranscriptResult:
    cues: tuple[TextCue, ...]
    source_language: str
    authority: str
    probability: float | None = None
    asr_evidence: Mapping[str, Any] | None = None

    def language_metadata(self) -> dict[str, Any]:
        return {"source_language": self.source_language, "language_authority": self.authority, "language_probability": self.probability}


def _resolved_language(cues: Sequence[TextCue], requested: str, observed: str | None = None) -> str:
    try:
        sources = tuple(SourceSegment(cue.cue_id, cue.source_text, TimePoint(cue.start_ms, TimeBase(1, 1000)), TimePoint(cue.end_ms, TimeBase(1, 1000)), observed or "und", cue.confidence) for cue in cues)
        return resolve_source_language(sources, requested_source_language=requested)
    except TranslationError as error:
        raise ProductionJobError(error.code, error.condition) from error


def _whisper_language(requested: str) -> str | None:
    if requested == "auto":
        return None
    return "zh" if requested.lower() in {"zh", "zh-cn", "zh-tw"} else requested.lower()


def _transcribe_with_faster_whisper(audio_path: Path, model_root: Path, source_language: str, *, chunks_dir: Path | None = None, model_binding: str | None = None, on_chunk: Any = None) -> TranscriptResult:
    from engine.dubflow.worker.whisper_cues import ASR_RECIPE, SAMPLE_RATE, MAX_GAP_SAMPLES
    from engine.dubflow.worker.whisper_chunks import CORE_SAMPLES, OVERLAP_SAMPLES, ChunkAsrError, transcribe_bounded
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as error:
        raise ProductionJobError("ASR_RUNTIME_MISSING", "the app-owned faster-whisper runtime is not installed") from error
    model_path = model_root / "asr" / "faster-whisper-small"
    if not model_path.is_dir():
        raise ProductionJobError("ASR_MODEL_MISSING", f"the pinned ASR model is missing: {model_path}")
    try:
        with wave.open(str(audio_path), "rb") as reader:
            if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise ProductionJobError("ASR_AUDIO_INVALID", "ASR requires the owned 16K mono PCM analysis audio")
            total_samples = reader.getnframes()
        audio_hash = _sha256(audio_path)
        base_evidence = {"schema_version": 1, "recipe": ASR_RECIPE, "analysis_audio_sha256": audio_hash,
                         "sample_rate": SAMPLE_RATE, "total_samples": total_samples,
                         "max_gap_samples": MAX_GAP_SAMPLES, "calibrated": False}
        def to_cues(aligned: Any) -> tuple[TextCue, ...]:
            return tuple(TextCue(item.cue_id, item.start_ms, item.end_ms, item.text, confidence=item.confidence) for item in aligned)
        def validate(aligned: Any) -> bool:
            try:
                return _asr_word_evidence_matches(to_cues(aligned), {**base_evidence, "cue_quality": {item.cue_id: item.evidence for item in aligned}})
            except (TypeError, ValueError):
                return False
        bounded = transcribe_bounded(
            audio_path, total_samples=total_samples, audio_hash=audio_hash, language=_whisper_language(source_language),
            model_binding=model_binding or ASR_RECIPE, chunks_dir=chunks_dir or (audio_path.parent / "asr-chunks"),
            model_factory=lambda: WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=max(1, min(8, os.cpu_count() or 1)), local_files_only=True),
            write_json=_atomic_json, validate_cues=validate, on_chunk=on_chunk,
        )
        cues = to_cues(bounded.cues)
        evidence = {**base_evidence, "schema_version": 2, "cue_quality": {item.cue_id: item.evidence for item in bounded.cues},
                    "chunking": {"core_samples": CORE_SAMPLES, "overlap_samples": OVERLAP_SAMPLES, "ownership": "word-midpoint-v1"},
                    "chunk_identity": bounded.identity, "chunk_binding": bounded.binding,
                    "language_pin": {"language": bounded.language, "probability": bounded.probability},
                    "chunks": list(bounded.chunks), "boundary_reviews": list(bounded.reviews)}
        if not _asr_evidence_matches(cues, evidence):
            raise ProductionJobError("ASR_HYPOTHESIS_INVALID", "reconciled ASR evidence does not match source cues")
    except ChunkAsrError as error:
        raise ProductionJobError(error.code, str(error), retryable=False) from error
    except ProductionJobError:
        raise
    except ValueError as error:
        raise ProductionJobError("ASR_HYPOTHESIS_INVALID", str(error), retryable=False) from error
    except Exception as error:
        raise ProductionJobError("ASR_FAILED", str(error), retryable=True) from error
    if not cues:
        raise ProductionJobError("ASR_EMPTY", "ASR produced no speech segments")
    resolved = _resolved_language(cues, source_language, bounded.language)
    return TranscriptResult(cues, resolved, "whisper-detected" if source_language == "auto" else "requested", bounded.probability, evidence)


def _sidecar_language(cues: tuple[TextCue, ...], config: WorkerConfig, audio_path: Path) -> TranscriptResult:
    if config.source_language != "auto":
        return TranscriptResult(cues, _resolved_language(cues, config.source_language), "requested")
    if not audio_path.is_file():
        raise ProductionJobError("SOURCE_LANGUAGE_UNRESOLVED", "sidecar-only input requires an explicit source language")
    try:
        import numpy as np
        import wave
        from faster_whisper import WhisperModel
        with wave.open(str(audio_path), "rb") as reader:
            if reader.getframerate() != 16000 or reader.getnchannels() != 1 or reader.getsampwidth() != 2:
                raise ProductionJobError("LANGUAGE_AUDIO_INVALID", "language detection needs 16 kHz mono signed-16 analysis audio")
            audio = np.frombuffer(reader.readframes(60 * 16000), dtype="<i2").astype(np.float32) / 32768
        model = WhisperModel(str(config.model_root / "asr/faster-whisper-small"), device="cpu", compute_type="int8", cpu_threads=4)
        observed, probability, _all = model.detect_language(audio=audio, vad_filter=True)
        resolved = _resolved_language(cues, "auto", observed)
        probability = float(probability) if 0 <= float(probability) <= 1 else None
        return TranscriptResult(cues, resolved, "whisper-audio-probe", probability)
    except ProductionJobError:
        raise
    except Exception as error:
        raise ProductionJobError("SOURCE_LANGUAGE_UNRESOLVED", "unable to establish sidecar source language from audio") from error


def _language_checkpoint(cues: tuple[TextCue, ...], document: Mapping[str, Any], requested: str) -> TranscriptResult | None:
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 4 or document.get("requested_source_language") != requested:
        return None
    observed = document.get("source_language")
    authority = document.get("language_authority")
    if not isinstance(observed, str) or authority not in {"requested", "whisper-detected", "whisper-audio-probe"}:
        return None
    if (requested == "auto") == (authority == "requested"):
        return None
    resolved = _resolved_language(cues, requested, observed)
    probability = document.get("language_probability")
    if probability is not None and (type(probability) not in (int, float) or not 0 <= probability <= 1):
        return None
    evidence = document.get("asr_evidence")
    if document.get("source") == "faster-whisper" and not _asr_evidence_matches(cues, evidence):
        return None
    if document.get("source") == "faster-whisper" and (_whisper_language(resolved) != evidence["language_pin"]["language"] or probability != evidence["language_pin"]["probability"]):
        return None
    return TranscriptResult(cues, resolved, authority, probability, evidence)


def _asr_evidence_matches(cues: Sequence[TextCue], evidence: Any) -> bool:
    from engine.dubflow.worker.whisper_chunks import summary_matches
    return summary_matches(evidence) and _asr_word_evidence_matches(cues, {**evidence, "schema_version": 1})


def _asr_word_evidence_matches(cues: Sequence[TextCue], evidence: Any) -> bool:
    from engine.dubflow.worker.whisper_cues import ASR_RECIPE, SAMPLE_RATE, MAX_GAP_SAMPLES
    from engine.dubflow.asr import map_sample_interval
    if not isinstance(evidence, Mapping) or type(evidence.get("schema_version")) is not int or evidence.get("schema_version") != 1 or evidence.get("recipe") != ASR_RECIPE or evidence.get("calibrated") is not False:
        return False
    if evidence.get("sample_rate") != SAMPLE_RATE or evidence.get("max_gap_samples") != MAX_GAP_SAMPLES or type(evidence.get("total_samples")) is not int or evidence["total_samples"] <= 0:
        return False
    if not isinstance(evidence.get("analysis_audio_sha256"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["analysis_audio_sha256"]):
        return False
    quality = evidence.get("cue_quality")
    if not isinstance(quality, Mapping) or len(quality) != len(cues) or set(quality) != {cue.cue_id for cue in cues}:
        return False
    try:
        for cue in cues:
            item = quality[cue.cue_id]
            if not isinstance(item, Mapping) or item.get("recipe") != ASR_RECIPE or item.get("sample_rate") != SAMPLE_RATE:
                return False
            start, end = item.get("start_sample"), item.get("end_sample")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= evidence["total_samples"]:
                return False
            interval = map_sample_interval(TimePoint(0, TimeBase(1, 1000)), start, end, SAMPLE_RATE)
            if (interval.start.ticks, interval.end.ticks) != (cue.start_ms, cue.end_ms):
                return False
            words = item.get("words")
            if not isinstance(words, list) or not words or "".join(word["text"] for word in words).strip() != cue.source_text:
                return False
            if len(words) > 16_384:
                return False
            previous_start = previous_end = -1
            for word in words:
                word_start, word_end = word.get("start_sample"), word.get("end_sample")
                if type(word_start) is not int or type(word_end) is not int or not start <= word_start <= word_end <= end or word_start < previous_start:
                    return False
                if previous_end >= 0 and word_start - previous_end > MAX_GAP_SAMPLES:
                    return False
                previous_start, previous_end = word_start, max(previous_end, word_end)
                value = word.get("probability")
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
                    return False
            if min(word["start_sample"] for word in words) != start or max(word["end_sample"] for word in words) != end:
                return False
            if cue.cue_id != f"asr-{start}-{end}-{sha256(cue.source_text.encode()).hexdigest()[:12]}":
                return False
            probabilities = [word.get("probability") for word in words]
            available = all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 for value in probabilities)
            basis = "minimum-word-probability-uncalibrated" if available else "unavailable"
            if item.get("confidence_basis") != basis or cue.confidence != (min(probabilities) if available else 0.0):
                return False
            if item.get("timing_basis") not in {"word-alignment", "segment-fallback"}:
                return False
            if item["timing_basis"] == "segment-fallback":
                if available or len(words) != 1 or item.get("review_reason") not in {"word-alignment-unavailable", "word-alignment-invalid"}:
                    return False
            elif item.get("review_reason") is not None:
                return False
            raw = item.get("raw_segment_scores")
            if not isinstance(raw, Mapping) or set(raw) != {"avg_logprob", "no_speech_prob", "compression_ratio", "temperature"}:
                return False
            if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value)) for value in raw.values()):
                return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
    return True


def _translation_identity(cues: Sequence[TextCue], language: str, provenance: Mapping[str, Any]) -> str:
    value = {"cues": [cue.to_dict() for cue in cues], "source_language": language, "route": dict(provenance)}
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _translate_with_argos(cues: Sequence[TextCue], model_root: Path, source_language: str, *, runtime: ArgosRuntime, chunks_dir: Path | None = None) -> tuple[TextCue, ...]:
    resolved = _resolved_language(cues, source_language, source_language if source_language != "auto" else None)
    if resolved != runtime.source_language:
        raise ProductionJobError("SOURCE_LANGUAGE_MISMATCH", "translation route differs from resolved transcript language")
    try:
        runtime.prepare(model_root)
        translated: list[TextCue] = []
        for cue in cues:
            identity = _translation_identity((cue,), resolved, runtime.provenance())
            chunk_path = chunks_dir / (identity.removeprefix("sha256:") + ".json") if chunks_dir else None
            if chunk_path is not None and chunk_path.is_file():
                try:
                    if chunk_path.stat().st_size > 65536:
                        raise ValueError("oversized translation chunk")
                    saved = json.loads(chunk_path.read_text(encoding="utf-8"))
                    candidates = _cues_from_json(chunk_path)
                    cached = candidates[0]
                    if len(candidates) == 1 and saved.get("schema_version") == 2 and saved.get("input_hash") == identity and saved.get("output_hash") == _translation_identity(candidates, resolved, runtime.provenance()) and (cached.cue_id, cached.start_ms, cached.end_ms, cached.source_text, cached.confidence) == (cue.cue_id, cue.start_ms, cue.end_ms, cue.source_text, cue.confidence) and isinstance(cached.translated_text, str) and cached.translated_text.strip():
                        translated.append(cached)
                        continue
                except (OSError, ValueError, ProductionJobError, KeyError, IndexError):
                    pass  # A bad private chunk is recomputed, never trusted.
            text = runtime.translate_text(cue.source_text)
            if not isinstance(text, str) or not text.strip():
                raise ProductionJobError("TRANSLATION_EMPTY", f"translation returned no text for {cue.cue_id}")
            result = TextCue(cue.cue_id, cue.start_ms, cue.end_ms, cue.source_text, text.strip(), cue.confidence)
            if chunk_path is not None:
                _atomic_json(chunk_path, {"schema_version": 2, "input_hash": identity, "output_hash": _translation_identity((result,), resolved, runtime.provenance()), "cues": [result.to_dict()]})
            translated.append(result)
        return tuple(translated)
    except ProductionJobError:
        raise
    except (TranslationError, ModelBootstrapError) as error:
        raise ProductionJobError(error.code, str(error), retryable=getattr(error, "retryable", False)) from error
    except Exception as error:
        raise ProductionJobError("TRANSLATION_FAILED", str(error), retryable=False) from error


def _write_manifest(
    output_dir: Path,
    config: WorkerConfig,
    probe: MediaProbeResult,
    cues: Sequence[TextCue],
    artifacts: Mapping[str, Path],
    warnings: Sequence[str],
    *,
    audio: Mapping[str, Any] | None = None,
    language: Mapping[str, Any] | None = None,
    translation: Mapping[str, Any] | None = None,
    asr: Mapping[str, Any] | None = None,
) -> Path:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dubflow_localization_job",
        "job_id": config.job_id,
        "source": {"path": str(config.source_path), "sha256": _sha256(config.source_path), "probe": probe.to_dict()},
        "target_language": config.target_language,
        "dubbing": {"enabled": config.enable_dubbing, "requested_voice_id": getattr(config, "tts_voice_id", None)},
        "cues": [cue.to_dict() for cue in cues],
        "artifacts": {name: {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size} for name, path in artifacts.items()},
        "warnings": list(warnings),
        "production_profile": "cpu-local-file-b2" if audio and audio.get("mode") == "dubbed" else "cpu-local-file-b1" if not config.enable_dubbing else "cpu-local-file-b1-downgraded-from-b2",
    }
    if audio is not None:
        manifest["audio"] = dict(audio)
    if language is not None:
        manifest["language"] = dict(language)
    if translation is not None:
        manifest["translation"] = dict(translation)
    if asr is not None:
        manifest["asr"] = dict(asr)
    path = output_dir / "job_manifest.json"
    _atomic_json(path, manifest)
    return path


def run_local_file(config: WorkerConfig, emitter: _Emitter) -> dict[str, Any]:
    output = config.output_dir.absolute()
    source = config.source_path.resolve()
    for name in (*export_publication.ENTRIES, ".dubflow-work"):
        controlled = (output / name).resolve()
        if source == controlled or controlled in source.parents:
            raise ProductionJobError("OUTPUT_SOURCE_OVERLAP", "source media overlaps export storage")
    try:
        pending = export_publication.prepare(output)
        result = _run_local_file(config, emitter, pending)
        manifest_path = Path(result["manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest["artifacts"].values():
            path = Path(artifact["path"])
            if path.is_relative_to(pending):
                artifact["path"] = str(output / path.relative_to(pending))
        _atomic_json(manifest_path, manifest)
        export_publication.publish(output)
        result = {name: str(output / Path(path).relative_to(pending)) if Path(path).is_relative_to(pending) else path for name, path in result.items()}
    except (export_publication.PublicationError, OSError) as error:
        raise ProductionJobError("OUTPUT_PUBLICATION_FAILED", str(error)) from error
    emitter.progress(1.0, "Hoàn tất video Việt hóa")
    return result


def _b2_generation(checkpoint: dict[str, Any], checkpoint_path: Path, work_dir: Path, output_dir: Path, identity: str) -> Path:
    stage = checkpoint.get("stages", {}).get("b2-generation", {})
    generation = stage.get("generation", "")
    reusable = stage.get("input_hash") == identity and isinstance(generation, str) and re.fullmatch(r"[a-f0-9]{32}", generation)
    directory = work_dir / "b2-audio" / identity / generation if reusable else None
    manifest_path = output_dir / "job_manifest.json"
    if reusable and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Published private audio is immutable. A failed attempt can resume
            # its own chunks without modifying the preceding validated export.
            paths = [Path(item["path"]) for item in manifest["artifacts"].values()]
            reusable = not any(path.is_relative_to(directory) for path in paths)
        except (OSError, ValueError, KeyError, TypeError):
            reusable = False
    if not reusable:
        generation = uuid.uuid4().hex
        directory = work_dir / "b2-audio" / identity / generation
        _write_stage(checkpoint, checkpoint_path, "b2-generation", {"input_hash": identity, "generation": generation})
    export_publication.plain(directory.absolute())
    return directory


def _source_render_duration(probe: MediaProbeResult) -> MediaTimeline | None:
    video = probe.video
    if video.time_base is not None and video.duration_ticks is not None and video.duration_ticks > 0:
        return MediaTimeline(video.time_base, 0, video.duration_ticks, video.duration_ticks)
    ticks = probe.duration_ticks
    return MediaTimeline(CANONICAL_TIME_BASE, 0, ticks, ticks) if ticks is not None and ticks > 0 else None


def _run_local_file(config: WorkerConfig, emitter: _Emitter, export_dir: Path) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = config.output_dir / ".dubflow-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = config.checkpoint_path or (work_dir / "checkpoint.json")
    source_hash = _sha256(config.source_path)
    checkpoint = _read_checkpoint(checkpoint_path, source_hash)

    profile_path = config.app_root / "models" / "manifests" / "production-cpu-v1.json"
    if not profile_path.is_file():
        raise ProductionJobError("MODEL_MANIFEST_MISSING", f"pinned production model profile is missing: {profile_path}")
    marker = config.model_root / ".profile-ready"
    profile_ready = False
    try:
        profile_ready = marker.is_file() and marker.read_bytes() == profile_path.read_bytes()
    except OSError:
        profile_ready = False
    if not _stage_ready(checkpoint, "models", (marker,)) or not profile_ready:
        emitter.progress(0.005, "Đang kiểm tra và tải model cục bộ lần đầu")
        try:
            ensure_model_profile(
                profile_path,
                config.model_root,
                progress=lambda _artifact, done, total: emitter.progress(
                    0.005 + 0.04 * (done / max(total, 1)),
                    "Đang tải model cục bộ",
                ),
            )
        except ModelBootstrapError as error:
            raise ProductionJobError(error.code, error.detail, retryable=error.retryable) from error
        _atomic_bytes(marker, profile_path.read_bytes())
        _write_stage(checkpoint, checkpoint_path, "models", {"profile": str(profile_path), "marker": str(marker), "sha256": _sha256(marker)})

    emitter.progress(0.02, "Đang kiểm tra media nguồn")
    probe = MediaProbe(config.ffprobe_path, trusted_root=config.media_runtime_root).probe(config.source_path)
    _write_stage(checkpoint, checkpoint_path, "probe", {"probe": probe.to_dict()})
    emitter.checkpoint("probe", source_hash)

    audio_path = work_dir / "analysis.wav"
    media = FfmpegMediaAdapter(config.ffmpeg_path, trusted_root=config.media_runtime_root)
    sidecar = _load_sidecar(config.source_path)
    if sidecar is None or config.source_language == "auto":
        if not probe.has_audio:
            if sidecar is not None:
                raise ProductionJobError("SOURCE_LANGUAGE_UNRESOLVED", "sidecar without audio requires an explicit source language")
            raise ProductionJobError("AUDIO_STREAM_MISSING", "ASR requires an audio stream when no sidecar captions are present")
        if not _stage_ready(checkpoint, "audio", (audio_path,)):
            emitter.progress(0.08, "Đang trích xuất audio")
            media.extract_audio(config.source_path, audio_path, overwrite=True)
            _write_stage(checkpoint, checkpoint_path, "audio", {"path": str(audio_path), "sha256": _sha256(audio_path)})
            emitter.checkpoint("audio", _sha256(audio_path))

    transcript_path = work_dir / "transcript.json"
    from engine.dubflow.worker.whisper_cues import ASR_RECIPE
    transcript_input = "sha256:" + sha256(json.dumps({"source_hash": source_hash, "requested_language": config.source_language, "sidecar_cues": [cue.to_dict() for cue in sidecar] if sidecar is not None else None, "asr_profile_hash": _sha256(profile_path), "recipe": ASR_RECIPE}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    transcript = None
    if _stage_ready(checkpoint, "transcript", (transcript_path,)):
        transcript_document = json.loads(transcript_path.read_text(encoding="utf-8"))
        cues = _cues_from_json(transcript_path)
        transcript_source = str(transcript_document.get("source", "checkpoint"))
        if transcript_document.get("input_hash") == transcript_input:
            transcript = _language_checkpoint(cues, transcript_document, config.source_language)
    if transcript is None:
        if sidecar is not None:
            transcript = _sidecar_language(sidecar, config, audio_path)
            transcript_source = "sidecar-srt"
        else:
            emitter.progress(0.18, "Đang nhận dạng lời thoại bằng model CPU")
            def asr_checkpoint(done: int, total: int, chunk_id: str, artifact_hash: str) -> None:
                emitter.progress(0.18 + 0.19 * done / total, "Đang nhận dạng lời thoại", units_done=done, units_total=total)
                emitter.checkpoint("asr:" + chunk_id, artifact_hash)
            transcript = _transcribe_with_faster_whisper(audio_path, config.model_root, config.source_language, chunks_dir=work_dir / "asr-chunks", model_binding=transcript_input, on_chunk=asr_checkpoint)
            transcript_source = "faster-whisper"
        cues = transcript.cues
        _atomic_json(transcript_path, {"schema_version": 4, "input_hash": transcript_input, "source": transcript_source, "requested_source_language": config.source_language, **transcript.language_metadata(), "asr_evidence": transcript.asr_evidence, "cues": [cue.to_dict() for cue in cues]})
        _write_stage(checkpoint, checkpoint_path, "transcript", {"source": transcript_source, "path": str(transcript_path), "sha256": _sha256(transcript_path)})
    emitter.progress(0.38, "Đã nhận dạng lời thoại", units_done=len(cues), units_total=len(cues))
    emitter.checkpoint("transcript", _sha256(work_dir / "transcript.json"))

    translation_path = work_dir / "translation.json"
    try:
        translation_runtime = ArgosRuntime(config.app_root, config.model_root, transcript.source_language)
        translation_provenance = translation_runtime.provenance()
    except TranslationError as error:
        raise ProductionJobError(error.code, error.condition) from error
    translation_input = _translation_identity(cues, transcript.source_language, translation_provenance)
    translation_changed = True
    if _stage_ready(checkpoint, "translation", (translation_path,)):
        previous = json.loads(translation_path.read_text(encoding="utf-8"))
        if previous.get("schema_version") == 2 and previous.get("input_hash") == translation_input:
            translated = _cues_from_json(translation_path)
            translation_changed = False
    if translation_changed:
        emitter.progress(0.44, "Đang dịch cục bộ sang tiếng Việt")
        translated = _translate_with_argos(cues, config.model_root, transcript.source_language, runtime=translation_runtime, chunks_dir=work_dir / "translation-chunks")
        _atomic_json(translation_path, {"schema_version": 2, "source_language": transcript.source_language, "target_language": "vi", "provenance": translation_provenance, "input_hash": translation_input, "cues": [cue.to_dict() for cue in translated]})
        _write_stage(checkpoint, checkpoint_path, "translation", {"path": str(translation_path), "sha256": _sha256(translation_path)})
    emitter.progress(0.58, "Đã dịch lời thoại", units_done=len(translated), units_total=len(translated))
    emitter.checkpoint("translation", _sha256(work_dir / "translation.json"))

    srt_path, ass_path = _write_subtitles(export_dir, translated)
    _write_stage(checkpoint, checkpoint_path, "subtitles", {"srt": str(srt_path), "ass": str(ass_path), "srt_sha256": _sha256(srt_path), "ass_sha256": _sha256(ass_path)})
    emitter.progress(0.66, "Đã tạo subtitle SRT/ASS")
    emitter.checkpoint("subtitles", _sha256(srt_path))

    warnings: list[str] = []
    b2_audio: B2AudioResult | None = None
    audio_path: Path | None = None
    audio_metadata: dict[str, Any] = {"mode": "original", "backend": "source-audio"}
    if config.enable_dubbing:
        emitter.progress(0.70, "Đang tổng hợp giọng Việt CPU và trộn audio AUD-0")
        try:
            from engine.dubflow.worker.b2_audio import tts_recipe_identity, mix_recipe_identity
            selected_voice = getattr(config, "tts_voice_id", None)
            generation_identity = sha256((translation_input + _sha256(profile_path) + _sha256(Path(__file__).with_name("b2_audio.py")) + tts_recipe_identity(config.app_root, profile_path) + mix_recipe_identity() + json.dumps(selected_voice)).encode()).hexdigest()
            b2_audio = run_b2_audio(
                media=media,
                source_path=config.source_path,
                source_probe=probe,
                translated_cues=translated,
                source_language=transcript.source_language,
                app_root=config.app_root,
                profile_path=profile_path,
                work_dir=_b2_generation(checkpoint, checkpoint_path, work_dir, config.output_dir.absolute(), generation_identity),
                model_root=config.model_root,
                tts_voice_id=selected_voice,
            )
            audio_path = b2_audio.final_mix_path
            audio_metadata = {
                "mode": "dubbed",
                "backend": b2_audio.tts_document.provenance.backend_id,
                "voice_id": b2_audio.voice.voice_id,
                "voice_version": b2_audio.voice.voice_version,
                "voice_hash": b2_audio.voice.content_hash(),
                "tts_document": str(b2_audio.tts_document_path),
                "mix_document": str(b2_audio.mix_document_path),
                "mix_provenance": b2_audio.mix_document.provenance.to_dict(),
                "tts_failures": len(b2_audio.tts_document.failures),
                "mix_failures": len(b2_audio.mix_document.failures),
                "mix_warnings": list(b2_audio.mix_document.warnings),
            }
            _write_stage(
                checkpoint,
                checkpoint_path,
                "b2-audio",
                {
                    "source_audio": str(b2_audio.source_audio_path),
                    "tts_document": str(b2_audio.tts_document_path),
                    "mix_document": str(b2_audio.mix_document_path),
                    "final_mix": str(b2_audio.final_mix_path),
                    "dialogue_stem": str(b2_audio.dialogue_stem_path),
                    "sha256": _sha256(b2_audio.final_mix_path),
                },
            )
            if b2_audio.tts_document.failures or b2_audio.mix_document.failures:
                warnings.append("B2_AUDIO_DEGRADED: one or more dialogue cues used bounded per-cue fallback; source audio was preserved")
            emitter.checkpoint("b2-audio", _sha256(b2_audio.final_mix_path))
        except B2AudioError as error:
            b2_audio = None
            audio_path = None
            warnings.append(f"B2_AUDIO_FALLBACK_TO_B1: {error.code}: {error.condition}")
            emitter.progress(0.70, "B2 audio không khả dụng; giữ Vietsub và audio gốc" if probe.has_audio
                             else "Video nguồn không có audio; tiếp tục xuất Vietsub")

    final_path = export_dir / "final_vi.mp4"
    render_stage = "render-dubbed" if audio_path is not None else "render"
    video_duration = _source_render_duration(probe)
    # A successful B2 run always re-renders against the newly verified mix.
    # This prevents a changed voice-pack hash or regenerated mix from being
    # hidden by a stale final-video checkpoint after a resumable restart.
    def render_identity() -> str:
        return sha256(json.dumps({"source": source_hash, "translation": translation_input, "subtitles": _sha256(ass_path), "burn_in": config.burn_in_subtitles, "audio": _sha256(audio_path) if audio_path is not None else "original", "recipe": "h264-aac-source-duration-v2", "duration": video_duration.to_dict() if video_duration else None, "adapter": _sha256(Path(__file__).parents[1] / "media" / "adapter.py")}, sort_keys=True).encode()).hexdigest()

    render_input = render_identity()
    render_ready = False
    if audio_path is None and checkpoint["stages"].get(render_stage, {}).get("input_hash") == render_input:
        for candidate in (final_path, config.output_dir.absolute() / "final_vi.mp4"):
            if _stage_ready(checkpoint, render_stage, (candidate,)):
                if candidate != final_path:
                    final_path.unlink(missing_ok=True)
                final_path, render_ready = candidate, True
                break
    if not render_ready:
        emitter.progress(0.74, "Đang render video H.264/AAC")
        try:
            media.render(
                config.source_path,
                final_path,
                subtitle_path=ass_path,
                audio_path=audio_path,
                video_duration=video_duration,
                preserve_original_audio=audio_path is None,
                burn_in_subtitles=config.burn_in_subtitles,
                overwrite=True,
            )
        except MediaAdapterError as error:
            if audio_path is None:
                raise ProductionJobError(error.code, error.condition, retryable=error.retryable) from error
            # A valid B1 render is safer than failing the whole localization
            # job when a real mix cannot be muxed by the media runtime.
            warnings.append(f"B2_AUDIO_FALLBACK_TO_B1: render audio failed: {error.code}: {error.condition}")
            b2_audio = None
            audio_path = None
            audio_metadata = {"mode": "original", "backend": "source-audio"}
            render_stage = "render"
            render_input = render_identity()
            media.render(
                config.source_path,
                final_path,
                subtitle_path=ass_path,
                preserve_original_audio=True,
                video_duration=video_duration,
                burn_in_subtitles=config.burn_in_subtitles,
                overwrite=True,
            )
        _write_stage(checkpoint, checkpoint_path, render_stage, {"path": str(final_path), "sha256": _sha256(final_path), "audio_mode": audio_metadata["mode"], "input_hash": render_input})
    emitter.checkpoint(render_stage, _sha256(final_path))

    emitter.progress(0.88, "Đang kiểm tra codec, thời lượng và khả năng đọc output")
    output_probe = MediaProbe(config.ffprobe_path, trusted_root=config.media_runtime_root).probe(final_path)
    source_duration = probe.duration_ticks

    def validate_output(candidate: MediaProbeResult) -> None:
        if candidate.video.codec_name != "h264":
            raise ProductionJobError("QC_VIDEO_CODEC", f"expected H.264 output, received {candidate.video.codec_name}")
        _validate_rendered_audio(probe, candidate)
        candidate_duration = candidate.duration_ticks
        if source_duration is not None and candidate_duration is not None and candidate_duration + 2_000 < source_duration:
            raise ProductionJobError("QC_DURATION_SHORT", "rendered output is materially shorter than the source")

    try:
        validate_output(output_probe)
    except ProductionJobError as error:
        if b2_audio is None:
            checkpoint["stages"].pop(render_stage, None)
            _atomic_json(checkpoint_path, checkpoint)
            raise
        warnings.append(f"B2_AUDIO_FALLBACK_TO_B1: output QC failed: {error.code}: {error.condition}")
        b2_audio = None
        audio_path = None
        audio_metadata = {"mode": "original", "backend": "source-audio"}
        media.render(
            config.source_path,
            final_path,
            subtitle_path=ass_path,
            preserve_original_audio=True,
            video_duration=video_duration,
            burn_in_subtitles=config.burn_in_subtitles,
            overwrite=True,
        )
        output_probe = MediaProbe(config.ffprobe_path, trusted_root=config.media_runtime_root).probe(final_path)
        validate_output(output_probe)
        _write_stage(checkpoint, checkpoint_path, "render", {"path": str(final_path), "sha256": _sha256(final_path), "audio_mode": "original", "input_hash": render_identity()})
    output_duration = output_probe.duration_ticks
    qc_path = export_dir / "qc_report.json"
    qc = {"schema_version": 1, "status": "passed", "source_probe": probe.to_dict(), "output_probe": output_probe.to_dict(), "audio": audio_metadata, "warnings": warnings, "downgrade": bool(warnings), "validated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _atomic_json(qc_path, qc)
    editable_dir = export_dir / "editable"
    editable_names = {"captions_vi.srt", "captions_vi.ass", "timeline.json"}
    if b2_audio is not None:
        editable_names.update({"source_audio.wav", "dialogue_stem.wav", "final_mix.wav"})
    if editable_dir.exists():
        for path in editable_dir.iterdir():
            export_publication.plain(path.absolute())
            if path.name not in editable_names:
                path.unlink()
    editable_dir.mkdir(parents=True, exist_ok=True)
    for source, name in ((srt_path, "captions_vi.srt"), (ass_path, "captions_vi.ass")):
        target = editable_dir / name
        _copy_editable_artifact(source, target, expected_hash=_sha256(source))
    editable_audio: list[tuple[Path, str, str]] = []
    if b2_audio is not None:
        editable_audio = [
            (b2_audio.original_audio_path, "source_audio.wav", b2_audio.mix_document.original_audio.content_hash),
            (b2_audio.dialogue_stem_path, "dialogue_stem.wav", b2_audio.mix_document.dialogue_stem.content_hash),
            (b2_audio.final_mix_path, "final_mix.wav", b2_audio.mix_document.final_mix.content_hash),
        ]
        for source, name, expected_hash in editable_audio:
            target = editable_dir / name
            _copy_editable_artifact(source, target, expected_hash=expected_hash)
    timeline_path = editable_dir / "timeline.json"
    _atomic_json(timeline_path, {"schema_version": 1, "time_base": {"numerator": 1, "denominator": TIMELINE_DENOMINATOR}, "duration_ticks": output_duration, "cues": [cue.to_dict() for cue in translated]})
    artifacts = {"final_video": final_path, "captions_srt": srt_path, "captions_ass": ass_path, "qc_report": qc_path, "editable_timeline": timeline_path}
    if b2_audio is not None:
        artifacts.update({
            "source_audio": b2_audio.original_audio_path,
            "dialogue_stem": b2_audio.dialogue_stem_path,
            "final_mix": b2_audio.final_mix_path,
            "tts_document": b2_audio.tts_document_path,
            "mix_document": b2_audio.mix_document_path,
            "editable_source_audio": editable_dir / "source_audio.wav",
            "editable_dialogue_stem": editable_dir / "dialogue_stem.wav",
            "editable_final_mix": editable_dir / "final_mix.wav",
        })
    manifest_path = _write_manifest(export_dir, config, probe, translated, artifacts, warnings, audio=audio_metadata, language=transcript.language_metadata(), translation=translation_provenance, asr=transcript.asr_evidence)
    _write_stage(checkpoint, checkpoint_path, "qc", {"path": str(qc_path), "sha256": _sha256(qc_path), "manifest": str(manifest_path)})
    emitter.checkpoint("qc", _sha256(qc_path))
    return {"final_video": str(final_path), "srt": str(srt_path), "ass": str(ass_path), "qc": str(qc_path), "manifest": str(manifest_path)}


def _read_command() -> Envelope:
    line = sys.stdin.buffer.readline()
    if not line:
        raise ProductionJobError("COMMAND_MISSING", "supervisor did not send a command")
    try:
        command = Envelope.from_line(line)
    except ProtocolError as error:
        raise ProductionJobError("COMMAND_PROTOCOL_INVALID", str(error)) from error
    if command.message_type is not MessageType.COMMAND or command.payload.get("command") != "run_local_file":
        raise ProductionJobError("COMMAND_UNSUPPORTED", "worker accepts only run_local_file command")
    return command


def main() -> int:
    command: Envelope | None = None
    emitter: _Emitter | None = None
    try:
        command = _read_command()
        raw_args = command.payload.get("args")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError as error:
                raise ProductionJobError("COMMAND_INVALID", "command args are not valid JSON") from error
        if not isinstance(raw_args, Mapping):
            raise ProductionJobError("COMMAND_INVALID", "command args must be an object")
        config = WorkerConfig.from_args(raw_args)
        emitter = _Emitter(command.job_id, command.stage_id)
        emitter.start()
        result = run_local_file(config, emitter)
        emitter.send(MessageType.SHUTDOWN, {"status": "completed"})
        return 0
    except (ProductionJobError, MediaAdapterError) as error:
        # A failed probe of the same immutable media has no changed condition.
        # MediaAdapterError predates that admission rule and marks process
        # rejection retryable; preserve its code but require a new input/action.
        retryable = error.retryable and error.code != "MEDIA_PROBE_FAILED"
        if emitter is not None:
            try:
                emitter.send(MessageType.FAILURE, {"code": error.code, "retryable": retryable, "attempt": 1, "condition": error.condition})
                emitter.send(MessageType.SHUTDOWN, {"status": "failed"})
            except (BrokenPipeError, OSError, ProtocolError):
                pass
        else:
            print(f"DubFlow worker failed before protocol startup: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # defensive boundary; never leak a traceback to protocol stdout
        detail = " ".join(str(error).replace("\x00", " ").split())[:4096]
        if emitter is not None:
            try:
                emitter.send(MessageType.FAILURE, {"code": "WORKER_UNHANDLED", "retryable": False, "attempt": 1, "condition": detail or "unhandled worker error"})
                emitter.send(MessageType.SHUTDOWN, {"status": "failed"})
            except (BrokenPipeError, OSError, ProtocolError):
                pass
        else:
            print(f"DubFlow worker failed before protocol startup: {detail}", file=sys.stderr)
        return 3
    finally:
        if emitter is not None:
            emitter.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ProductionJobError", "TextCue", "WorkerConfig", "main", "run_local_file"]
