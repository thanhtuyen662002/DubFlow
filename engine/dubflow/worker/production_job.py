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

from dataclasses import dataclass, field, replace
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
from engine.dubflow.media.adapter import AUDIO_DECODE_RECIPE, MAX_BLUR_REGIONS, BlurRegion, audio_origin_samples
from engine.dubflow.models import ModelBootstrapError, ensure_model_profile
from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.translation.adapter import SourceSegment, TranslationError, resolve_source_language
from engine.dubflow.translation.argos_runtime import ArgosRuntime
from engine.dubflow.translation.hachimi_runtime import ENGINES as TRANSLATION_ENGINES, create_runtime as create_translation_runtime
from engine.dubflow.worker.b2_audio import B2AudioError, B2AudioResult, MEASURED_GAP_EVIDENCE, run_b2_audio
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


# Whisper can loop on one token ("1.5.5.5.5…") and translation copies the loop.
# Such runs are noise in subtitles and can crash speech synthesis, so a unit of
# up to 16 characters repeated six or more times in a row is kept once.
_REPEATED_RUN = re.compile(r"(.{1,16}?)\1{5,}", re.DOTALL)


def _collapse_repeats(text: str | None) -> str | None:
    if not text:
        return text
    collapsed = _REPEATED_RUN.sub(r"\1", text)
    return collapsed if collapsed.strip() else text


def _without_repeat_loops(cues: Sequence["TextCue"]) -> tuple[tuple["TextCue", ...], int]:
    cleaned = tuple(replace(cue, source_text=_collapse_repeats(cue.source_text), translated_text=_collapse_repeats(cue.translated_text))
                    for cue in cues)
    return cleaned, sum(1 for before, after in zip(cues, cleaned) if before != after)


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
    # Opaque dark box behind burned subtitles, to cover source hard-subs.
    subtitle_box: bool = False
    # "hachimi" opts Chinese sources into HachimiMT-60; Argos stays the default.
    translation_engine: str = "argos"
    # Source-caption rectangles to blur before drawing the Vietnamese subtitles.
    blur_regions: tuple[BlurRegion, ...] = ()
    # Detect burned-in captions automatically and blur them while on screen.
    blur_auto: bool = False
    translation_package: Path | None = None
    tts_model: Path | None = None
    tts_config: Path | None = None
    checkpoint_path: Path | None = None
    multi_voice: bool = False
    speaker_voices: Mapping[str, str] = field(default_factory=dict)
    separate_background: bool = False
    # "auto" uses a supported NVIDIA GPU when present; "cpu" never probes one.
    accelerator: str = "auto"

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

        speaker_voices = args.get("speaker_voices", {})
        if not isinstance(speaker_voices, Mapping) or len(speaker_voices) > 8 or any(
            not isinstance(key, str) or re.fullmatch(r"S[1-8]", key) is None
            or not isinstance(value, str) or re.fullmatch(r"[a-z0-9-]{1,96}", value) is None
            for key, value in speaker_voices.items()
        ):
            raise ProductionJobError("COMMAND_INVALID", "speaker_voices must map up to eight S1..S8 speakers to preset voice identifiers")

        translation_engine = args.get("translation_engine") or "argos"
        if translation_engine not in TRANSLATION_ENGINES:
            raise ProductionJobError("COMMAND_INVALID", "translation_engine must be argos or hachimi")

        accelerator = args.get("accelerator", "auto")
        if not isinstance(accelerator, str) or accelerator not in {"auto", "cpu"}:
            raise ProductionJobError("COMMAND_INVALID", "accelerator must be auto or cpu")

        raw_blur = args.get("blur_regions") or []
        if not isinstance(raw_blur, list) or len(raw_blur) > MAX_BLUR_REGIONS:
            raise ProductionJobError("COMMAND_INVALID", "blur_regions must be a bounded list")
        try:
            blur_regions = tuple(BlurRegion.from_mapping(item) for item in raw_blur)
        except MediaAdapterError as error:
            raise ProductionJobError("COMMAND_INVALID", error.condition) from error

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
            subtitle_box=optional_bool("subtitle_box", False),
            translation_engine=translation_engine,
            blur_regions=blur_regions,
            blur_auto=optional_bool("blur_auto", False),
            translation_package=optional_path("translation_package"),
            tts_model=optional_path("tts_model"),
            tts_config=optional_path("tts_config"),
            checkpoint_path=optional_path("checkpoint_path"),
            multi_voice=optional_bool("multi_voice", False),
            speaker_voices=dict(speaker_voices),
            separate_background=optional_bool("separate_background", False),
            accelerator=accelerator,
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
        self._shut_down = False
        self._thread = threading.Thread(target=self._heartbeat_loop, name="dubflow-worker-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def send(self, message_type: MessageType, payload: dict[str, Any]) -> None:
        with self._lock:
            # Shutdown is terminal for the stream. A heartbeat racing the final
            # message would turn a finished (or cleanly failed) job into a
            # supervisor protocol failure, so nothing is written after it.
            if self._shut_down:
                return
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
            if message_type == MessageType.SHUTDOWN:
                self._shut_down = True
                self._stop.set()

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


def _copy_editable_artifact(source: Path, target: Path, *, expected_hash: str, link: bool = False) -> bool:
    """Publish one verified asset with bounded memory and preserve its prior file.

    Completed assets can be reused after interruption; an incomplete file never
    replaces the destination. The enclosing export transaction preserves the
    preceding validated video and editable directory until all assets pass QC.
    With ``link`` the asset shares the source's storage through a hard link
    when the volume supports one; the result says whether it does.
    """
    export_publication.plain(source.absolute())
    export_publication.plain(target.absolute())
    if target.is_file() and _sha256(target) == expected_hash:
        if _sha256(source) != expected_hash:
            raise ProductionJobError("EDITABLE_ARTIFACT_CHANGED", "editable source differs from validated provenance")
        if not link or os.path.samefile(source, target):
            return link
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        if link:
            try:
                os.link(source, temporary)
            except OSError:
                if target.is_file():
                    return False  # The verified copy above stays.
            else:
                if _sha256(temporary) != expected_hash:
                    raise ProductionJobError("EDITABLE_ARTIFACT_CHANGED", "editable asset bytes differ from validated provenance")
                os.replace(temporary, target)
                return True
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
        return False
    except OSError as error:
        raise ProductionJobError("EDITABLE_COPY_FAILED", str(error), retryable=True) from error
    finally:
        temporary.unlink(missing_ok=True)


def _share_identical(paths: Iterable[Path]) -> None:
    """Store identical regenerable audio once: later files become hard links.

    Only regular, unlinked-path files are considered. Bytes are compared by hash
    before linking, and a file the volume cannot link stays a separate copy.
    """
    groups: dict[tuple[int, str], Path] = {}
    for path in paths:
        try:
            export_publication.plain(path.absolute())
            if not path.is_file():
                continue
            key = (path.stat().st_size, _sha256(path))
            first = groups.setdefault(key, path)
            if first == path or os.path.samefile(first, path):
                continue
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
            try:
                os.link(first, temporary)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ProductionJobError, export_publication.PublicationError):
            continue


def _remove_scratch(work_dir: Path, *paths: Path) -> None:
    """Delete regenerable files or folders, only inside this job's work folder.

    Cleanup is best effort: a file that cannot be removed now stays as scratch.
    """
    root = work_dir.absolute()
    for path in paths:
        path = path.absolute()
        if not path.is_relative_to(root) or path == root:
            raise ProductionJobError("SCRATCH_OUTSIDE_WORK", "scratch cleanup is outside the job work folder")
        try:
            export_publication.plain(path)
            if path.is_dir():
                for entry in path.rglob("*"):
                    export_publication.plain(entry)
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except (OSError, export_publication.PublicationError):
            continue


MAX_UNDUBBED_STARTS = 20
# Notes about how the job ran, not about the quality of its output.
INFORMATIONAL_WARNINGS = ("ASR_ACCELERATOR:",)


def _undubbed_summary(cues: Sequence[TextCue], members: Mapping[str, Sequence[str]], failed: Iterable[str]) -> dict[str, Any]:
    """Speech that keeps the source language: phrase count, length, share and starts."""
    by_id = {cue.cue_id: cue for cue in cues}
    spans = sorted({(min(by_id[cue].start_ms for cue in group), max(by_id[cue].end_ms for cue in group))
                    for group in (members.get(identifier, (identifier,)) for identifier in set(failed))
                    if group and all(cue in by_id for cue in group)})
    speech = sum(cue.end_ms - cue.start_ms for cue in cues)
    return {"phrases": len(spans), "duration_ms": sum(end - start for start, end in spans), "speech_ms": speech,
            "starts_ms": [start for start, _ in spans[:MAX_UNDUBBED_STARTS]]}


def _retire_generations(work_dir: Path, keep: Path) -> None:
    """After publication, remove B2 generations nothing references or can reuse.

    The checkpoint names only the current generation, and the new manifest
    references only it, so earlier generations are unreachable scratch.
    """
    root = work_dir / "b2-audio"
    try:
        for identity in [path for path in root.iterdir() if path.is_dir()] if root.is_dir() else ():
            for generation in [path for path in identity.iterdir() if path.is_dir()]:
                if generation.absolute() != keep.absolute():
                    _remove_scratch(work_dir, generation)
            if not any(identity.iterdir()):
                identity.rmdir()
    except OSError:
        pass  # Cleanup never fails a published job; the rest stays as scratch.


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


TRANSLATION_OVERRIDES_FILE = "translation_overrides.json"
_MAX_OVERRIDE_TEXT = 2000


def _apply_translation_overrides(cues: Sequence[TextCue], output_dir: Path) -> tuple[tuple[TextCue, ...], str | None]:
    """Replace machine translations with the user's reviewed text, per cue id.

    The file lives beside the outputs so a re-run in the same directory reuses
    ASR and translation checkpoints and regenerates only downstream stages.
    Returns the effective cues and a hash of the applied overrides (None when
    there is no file), which callers fold into downstream identities.
    """
    path = output_dir / TRANSLATION_OVERRIDES_FILE
    if not path.is_file():
        return tuple(cues), None
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("file is too large")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("cues"), dict):
            raise ValueError("schema_version 1 with a cues object is required")
        overrides: dict[str, str] = {}
        for cue_id, text in document["cues"].items():
            if not isinstance(cue_id, str) or not isinstance(text, str):
                raise ValueError("cue ids and texts must be strings")
            text = " ".join(text.split())
            if not text or len(text) > _MAX_OVERRIDE_TEXT:
                raise ValueError(f"override for {cue_id} is empty or longer than {_MAX_OVERRIDE_TEXT} characters")
            overrides[cue_id] = text
    except (OSError, ValueError) as error:
        raise ProductionJobError("TRANSLATION_OVERRIDES_INVALID", f"{path.name}: {error}") from error
    known = {cue.cue_id for cue in cues}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ProductionJobError("TRANSLATION_OVERRIDES_INVALID",
                                 f"{path.name} names cues that this transcript does not contain: {', '.join(unknown[:5])}")
    effective = tuple(replace(cue, translated_text=overrides.get(cue.cue_id, cue.translated_text)) for cue in cues)
    applied = {cue_id: overrides[cue_id] for cue_id in sorted(overrides)}
    return effective, "sha256:" + sha256(json.dumps(applied, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _format_timestamp(milliseconds: int, *, ass: bool = False) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


ASS_STYLE_DEFAULT = "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,48,1"
# Cover band: a full-width dark rectangle (alpha 0x10, ~94% opaque) over the
# subtitle area for the whole video, drawn under the text, so source
# hard-subs stay hidden even between Vietnamese lines or past their width.
ASS_STYLE_BAND = "Style: Band,Arial,48,&H10000000,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
ASS_BAND_EVENT = r"Dialogue: 0,0:00:00.00,9:59:59.99,Band,,0,0,0,,{\an7\pos(0,0)\p1}m 0 820 l 1920 820 1920 1064 0 1064{\p0}"


def _write_subtitles(output_dir: Path, cues: Sequence[TextCue], *, box: bool = False) -> tuple[Path, Path]:
    srt = output_dir / "captions_vi.srt"
    ass = output_dir / "captions_vi.ass"
    srt_text = "\n\n".join(
        f"{index}\n{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}\n{cue.translated_text or cue.source_text}"
        for index, cue in enumerate(cues, start=1)
    ) + "\n\n"
    # Without the band the file is byte-identical to earlier releases.
    layer = 1 if box else 0
    ass_text = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n""" + ASS_STYLE_DEFAULT + ("\n" + ASS_STYLE_BAND if box else "") + """\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n""" + (ASS_BAND_EVENT + "\n" if box else "") + "\n".join(
        f"Dialogue: {layer},{_format_timestamp(cue.start_ms, ass=True)},{_format_timestamp(cue.end_ms, ass=True)},Default,,0,0,0,,{(cue.translated_text or cue.source_text).replace(chr(10), r'\\N')}"
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


def _asr_device(config: WorkerConfig, emitter: Any, warnings: list[str]) -> Any:
    """Pick CUDA or the CPU for ASR; any GPU problem becomes a CPU choice and a warning."""
    from engine.dubflow.accelerator import nvidia

    last = [0.0]
    def download(_artifact: str, done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - last[0] < 1.0:
            return
        last[0] = now
        emitter.progress(0.18, f"Đang tải thư viện GPU NVIDIA lần đầu: {done // 1_000_000}/{total // 1_000_000} MB")
    device = nvidia.select_asr_device(config.app_root, config.model_root, getattr(config, "accelerator", "auto"), progress=download)
    if device.device == "cuda":
        warnings.append(f"ASR_ACCELERATOR: cuda {device.compute_type} on {device.gpu.label()}")
    elif device.reason:
        warnings.append(f"ACCELERATOR_FALLBACK: ASR ran on the CPU: {device.reason}")
    return device


def _transcribe_with_faster_whisper(audio_path: Path, model_root: Path, source_language: str, *, chunks_dir: Path | None = None, model_binding: str | None = None, on_chunk: Any = None,
                                    device: Any = None, on_fallback: Any = None) -> TranscriptResult:
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
        cpu_model = lambda: WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=max(1, min(8, os.cpu_count() or 1)), local_files_only=True)
        on_gpu = device is not None and device.device == "cuda"
        bounded = transcribe_bounded(
            audio_path, total_samples=total_samples, audio_hash=audio_hash, language=_whisper_language(source_language),
            model_binding=model_binding or ASR_RECIPE, chunks_dir=chunks_dir or (audio_path.parent / "asr-chunks"),
            model_factory=(lambda: WhisperModel(str(model_path), device="cuda", device_index=0, compute_type=device.compute_type, local_files_only=True)) if on_gpu else cpu_model,
            write_json=_atomic_json, validate_cues=validate, on_chunk=on_chunk,
            fallback_factory=cpu_model if on_gpu else None, on_fallback=on_fallback,
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
    from engine.dubflow.worker.whisper_cues import ASR_RECIPE, SAMPLE_RATE, MAX_GAP_SAMPLES, DRIFT_REVIEW_REASON, drift_fallback_matches
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
            if item.get("timing_basis") not in {"word-alignment", "segment-fallback", "drift-fallback"}:
                return False
            if item["timing_basis"] == "drift-fallback":
                if available or len(words) != 1 or not drift_fallback_matches(item, evidence["total_samples"]):
                    return False
            elif item.get("alignment_drift") is not None:
                return False
            elif item["timing_basis"] == "segment-fallback":
                if available or len(words) != 1 or item.get("review_reason") not in {"word-alignment-unavailable", "word-alignment-invalid"}:
                    return False
            elif item.get("review_reason") not in {None, DRIFT_REVIEW_REASON}:
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


def _translate_with_argos(cues: Sequence[TextCue], model_root: Path, source_language: str, *, runtime: Any, chunks_dir: Path | None = None) -> tuple[TextCue, ...]:
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
    speakers: Sequence[Mapping[str, Any]] | None = None,
    background: Mapping[str, Any] | None = None,
) -> Path:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dubflow_localization_job",
        "job_id": config.job_id,
        "source": {"path": str(config.source_path), "sha256": _sha256(config.source_path), "probe": probe.to_dict(),
                   "clock_mapping": _source_clock_mapping(probe)},
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
    if speakers is not None:
        manifest["speakers"] = list(speakers)
    if background is not None:
        manifest["background"] = dict(background)
    path = output_dir / "job_manifest.json"
    _atomic_json(path, manifest)
    return path


OCR_PROFILE_PATH = "models/manifests/production-ocr-det-v1.json"
SEPARATION_PROFILE_PATH = "models/manifests/production-separation-spleeter-v1.json"
SPEAKER_PROFILE_PATH = "models/manifests/production-speaker-campplus-v1.json"
SPEAKER_THRESHOLD = 0.3


def _separate_background_stage(config: WorkerConfig, source_hash: str, probe: MediaProbeResult,
                               media: FfmpegMediaAdapter, work_dir: Path, checkpoint: dict[str, Any],
                               checkpoint_path: Path, emitter: Any, warnings: list[str]):
    """Opt-in analysis; source-video origin and the B2 PCM format are retained."""
    try:
        from engine.dubflow.separation import spleeter
        from engine.dubflow.tts.neural_vits import _child, _read_json

        profile_path = config.app_root / SEPARATION_PROFILE_PATH
        profile = _read_json(profile_path)
        selector = _read_json(config.app_root / "models/manifests/production-cpu-v1.json")
        voice_profile = _read_json(_child(config.app_root, selector.get("tts_neural_profile", "")))
        rate = voice_profile["sample_rate"]
        if type(rate) is not int or not 8000 <= rate <= 96000:
            raise ValueError("invalid B2 PCM sample rate")
        identity = "sha256:" + sha256(json.dumps({"source": source_hash,
            "audio": _audio_decode_identity(source_hash, probe), "sample_rate": rate, "channels": 2,
            "ffmpeg": _sha256(config.ffmpeg_path), "profile": _sha256(profile_path),
            "adapter": _sha256(Path(spleeter.__file__))}, sort_keys=True).encode()).hexdigest()
        directory = work_dir / "separation"
        background_path = directory / "background.wav"
        source_audio = directory / "source_audio.wav"
        emitter.progress(0.68, "Đang tách nhạc nền khỏi giọng gốc (thử nghiệm)")
        stage = checkpoint.get("stages", {}).get("separation", {})
        if _stage_ready(checkpoint, "separation", (background_path,)) and stage.get("input_hash") == identity:
            metrics = dict(stage["metrics"])
            if metrics.get("backend") != spleeter.BACKEND or any(
                type(metrics.get(key)) not in (int, float) or not math.isfinite(metrics[key])
                for key in ("seconds", "source_rms_db", "background_rms_db", "removed_db", "peak")
            ) or not 0 <= metrics["peak"] <= .999:
                raise ValueError("invalid separation checkpoint metrics")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            ensure_model_profile(profile_path, config.model_root,
                progress=lambda *_: emitter.progress(0.68, "Đang tách nhạc nền khỏi giọng gốc (thử nghiệm)"))
            # Same B2 extraction recipe (including leading gaps and video origin).
            # Independent analysis avoids introducing an optional model into TTS.
            media.extract_audio(config.source_path, source_audio, sample_rate=rate, channels=2,
                                video_stream=probe.video, overwrite=True)
            metrics = spleeter.separate_background(source_audio, background_path,
                model_dir=_child(config.model_root, profile["model_dir"]), ffmpeg_path=config.ffmpeg_path,
                progress=lambda fraction: emitter.progress(0.68 + .01 * fraction,
                    "Đang tách nhạc nền khỏi giọng gốc (thử nghiệm)"))
            _write_stage(checkpoint, checkpoint_path, "separation", {"path": str(background_path),
                "sha256": _sha256(background_path), "input_hash": identity, "metrics": metrics,
                "profile_sha256": _sha256(profile_path), "adapter_sha256": _sha256(Path(spleeter.__file__))})
        # The committed stem is all a resume reads; the decoded input and the
        # model's batch checkpoints are regenerable scratch.
        _remove_scratch(work_dir, source_audio, directory / ".spleeter-chunks")
        emitter.checkpoint("separation", _sha256(background_path))
        return background_path, {"mode": "spleeter-2stems", **metrics, "input_hash": identity,
                                 "profile_sha256": _sha256(profile_path)}
    except Exception as error:
        code = getattr(error, "code", type(error).__name__)
        condition = getattr(error, "condition", getattr(error, "detail", str(error)))
        condition = " ".join(str(condition).replace("\x00", " ").split())[:300]
        warnings.append(f"SEPARATION_SKIPPED: {code}: {condition}")
        return None, {"mode": "original"}


def _multi_voice_cast(config: WorkerConfig, source_hash: str, cues: Sequence[TextCue], probe: MediaProbeResult,
                      media: FfmpegMediaAdapter, work_dir: Path, checkpoint: dict[str, Any],
                      checkpoint_path: Path, emitter: Any, warnings: list[str]):
    """Optional, checkpointed audio clusters; every failure retains one-voice B2."""
    try:
        from engine.dubflow.diarization import speakers
        from engine.dubflow.tts import vieneu
        from engine.dubflow.tts.neural_vits import _child, _read_json

        selector = _read_json(config.app_root / "models/manifests/production-cpu-v1.json")
        catalog = _read_json(_child(config.app_root, selector.get("tts_neural_profile", "")))
        if catalog.get("backend") != vieneu.ENGINE_ID:
            raise ValueError("multi-voice requires the VieNeu backend")
        voices = vieneu.voice_choices(catalog)
        primary = config.tts_voice_id or catalog["voice_id"]
        profile_path = config.app_root / SPEAKER_PROFILE_PATH
        identity = "sha256:" + sha256(json.dumps({"source": source_hash,
            "audio": _audio_decode_identity(source_hash, probe),
            "cues": [[cue.cue_id, cue.start_ms, cue.end_ms] for cue in cues],
            "profile": _sha256(profile_path), "adapter": _sha256(Path(speakers.__file__)),
            "threshold": SPEAKER_THRESHOLD}, sort_keys=True).encode()).hexdigest()
        result_path = work_dir / "speakers.json"
        emitter.progress(0.39, "Đang nhận diện người nói (thử nghiệm)")
        stage = checkpoint.get("stages", {}).get("speakers", {})
        if _stage_ready(checkpoint, "speakers", (result_path,)) and stage.get("input_hash") == identity:
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            result = speakers.SpeakerResult(saved["labels"], saved["speakers"])
        else:
            if not probe.has_audio:
                raise ValueError("source has no audio for speaker recognition")
            ensure_model_profile(profile_path, config.model_root)
            model_path = _child(config.model_root, _read_json(profile_path)["model_path"])
            audio_path = work_dir / "analysis.wav"
            audio_identity = _audio_decode_identity(source_hash, probe)
            if not _stage_ready(checkpoint, "audio", (audio_path,)) or checkpoint["stages"]["audio"].get("input_hash") != audio_identity:
                media.extract_audio(config.source_path, audio_path, sample_rate=16000, channels=1,
                                    video_stream=probe.video, overwrite=True)
                _write_stage(checkpoint, checkpoint_path, "audio", {"path": str(audio_path), "sha256": _sha256(audio_path),
                    "input_hash": audio_identity, "clock_mapping": _source_clock_mapping(probe)})
            result = speakers.assign_speakers(audio_path, cues, model_path, threshold=SPEAKER_THRESHOLD,
                checkpoint_dir=work_dir / "speaker-chunks" / identity.removeprefix("sha256:"))
            _atomic_json(result_path, {"labels": result.labels, "speakers": result.speakers})
            _write_stage(checkpoint, checkpoint_path, "speakers", {"path": str(result_path),
                "sha256": _sha256(result_path), "input_hash": identity})
        ids = {speaker["id"] for speaker in result.speakers}
        if not 1 <= len(ids) <= 8 or len(ids) != len(result.speakers) or ids != {f"S{i}" for i in range(1, len(ids) + 1)} or set(result.labels) != {cue.cue_id for cue in cues} or set(result.labels.values()) != ids:
            raise ValueError("speaker checkpoint does not cover current cues")
        for speaker in result.speakers:
            expected = {identifier for identifier, label in result.labels.items() if label == speaker["id"]}
            if speaker["gender"] not in {"male", "female", "unknown"} or not isinstance(speaker["cues"], list) or set(speaker["cues"]) != expected or len(speaker["cues"]) != len(expected):
                raise ValueError("speaker checkpoint has invalid cluster metadata")
        casting = speakers.cast_voices(result.speakers, voices, primary, getattr(config, "speaker_voices", {}))
        emitter.checkpoint("speakers", _sha256(result_path))
        return result, casting
    except Exception as error:  # optional experimental analysis must never stop a job
        detail = " ".join(str(error).replace("\x00", " ").split())[:300]
        warnings.append(f"MULTI_VOICE_SKIPPED: {getattr(error, 'code', type(error).__name__)}: {detail}")
        return None, {}


def _auto_blur_regions(config: WorkerConfig, source_hash: str, duration_ms: int | None, cues: Sequence[TextCue],
                       work_dir: Path, checkpoint: dict[str, Any], checkpoint_path: Path, emitter: Any,
                       warnings: list[str]) -> tuple[tuple[BlurRegion, ...], dict[str, Any] | None]:
    """Detected caption regions, or none with a warning; this stage never fails a job."""
    from engine.dubflow.ocr import caption_scan

    scan_path = work_dir / "caption_scan.json"
    profile_path = config.app_root / OCR_PROFILE_PATH
    speech = [[cue.start_ms, cue.end_ms] for cue in cues]
    try:
        identity = "sha256:" + sha256(json.dumps({"source": source_hash, "profile": _sha256(profile_path), "recipe": caption_scan.SCAN_RECIPE,
                                                  "scanner": _sha256(Path(caption_scan.__file__)), "speech": speech}, sort_keys=True).encode()).hexdigest()
        stage = checkpoint.get("stages", {}).get("caption_scan", {})
        if _stage_ready(checkpoint, "caption_scan", (scan_path,)) and stage.get("input_hash") == identity:
            saved = json.loads(scan_path.read_text(encoding="utf-8"))
            return tuple(BlurRegion.from_mapping(item) for item in saved["regions"]), saved["summary"]
        # The scan runs after dubbing, so its band follows the dubbing band.
        emitter.progress(0.84, "Đang chuẩn bị model dò phụ đề gốc")
        ensure_model_profile(profile_path, config.model_root)
        model_path = config.model_root / json.loads(profile_path.read_text(encoding="utf-8"))["model_path"]
        emitter.progress(0.84, "Đang dò phụ đề gốc để làm mờ")
        scanner = caption_scan.CaptionScanner(model_path, config.ffmpeg_path)
        result = scanner.scan(config.source_path, duration_s=(duration_ms or 0) / 1000, speech_ms=speech,
                              progress=lambda fraction: emitter.progress(0.84 + 0.02 * fraction, "Đang dò phụ đề gốc để làm mờ"))
        summary = result.summary()
        _atomic_json(scan_path, {"schema_version": 1, "input_hash": identity, "summary": summary,
                                 "regions": [region.to_dict() for region in result.regions]})
        _write_stage(checkpoint, checkpoint_path, "caption_scan", {"path": str(scan_path), "sha256": _sha256(scan_path), "input_hash": identity})
        emitter.checkpoint("caption_scan", _sha256(scan_path))
        return result.regions, summary
    except Exception as error:  # noqa: BLE001 - a failed scan downgrades to an unblurred render.
        code = getattr(error, "code", type(error).__name__)
        warnings.append(f"AUTO_BLUR_SKIPPED: caption scan failed: {code}: {str(error)[:300]}")
        return (), None


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
        generation = result.pop("b2_generation", None)
        manifest_path = Path(result["manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest["artifacts"].values():
            path = Path(artifact["path"])
            if path.is_relative_to(pending):
                artifact["path"] = str(output / path.relative_to(pending))
        _atomic_json(manifest_path, manifest)
        export_publication.publish(output)
        if generation is not None:
            _retire_generations(output / ".dubflow-work", Path(generation))
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
    if reusable and directory.exists():
        # A file shared with an editable asset belongs to a publication; the
        # mixer refuses multiply linked inputs, so start a fresh generation.
        reusable = all(path.lstat().st_nlink == 1 for path in directory.rglob("*") if path.is_file())
    if not reusable:
        generation = uuid.uuid4().hex
        directory = work_dir / "b2-audio" / identity / generation
        _write_stage(checkpoint, checkpoint_path, "b2-generation", {"input_hash": identity, "generation": generation})
    export_publication.plain(directory.absolute())
    return directory


# Full-length PCM tracks a dubbed job holds at its peak, the last mix pass: the
# generation's source, the mixer's snapshot, raw dialogue, the 32-bit combined
# spool (two tracks) and both outputs. A separated bed adds the separation
# stem, the generation's copy and the restored bed.
DUBBING_PEAK_TRACKS = 7
SEPARATION_PEAK_TRACKS = 3
STORAGE_RESERVE_BYTES = 1 << 30


def _b2_sample_rate(app_root: Path) -> int:
    from engine.dubflow.tts.neural_vits import _child, _read_json
    try:
        selector = _read_json(app_root / "models/manifests/production-cpu-v1.json")
        rate = _read_json(_child(app_root, selector.get("tts_neural_profile", "")))["sample_rate"]
    except Exception:  # noqa: BLE001 - the estimate falls back to the largest shipped rate.
        return 48_000
    return rate if type(rate) is int and 8000 <= rate <= 96000 else 48_000


def _held_bytes(root: Path) -> int:
    seen, total = set(), 0
    for path in root.rglob("*") if root.is_dir() else ():
        try:
            status = path.lstat()
        except OSError:
            continue
        if path.is_file() and (status.st_dev, status.st_ino) not in seen:
            seen.add((status.st_dev, status.st_ino))
            total += status.st_size
    return total


def _storage_admission(config: WorkerConfig, probe: MediaProbeResult, work_dir: Path) -> None:
    """Refuse a job its output volume cannot hold before any large allocation.

    The estimate is the job's peak, from duration and the B2 sample rate, less
    what a resumed attempt already holds in its work folder.
    """
    milliseconds = probe.duration_ticks
    if milliseconds is None or milliseconds <= 0:
        return
    required = milliseconds * 16 * 2 + 2 * config.source_path.stat().st_size + STORAGE_RESERVE_BYTES
    if config.enable_dubbing:
        tracks = DUBBING_PEAK_TRACKS
        if getattr(config, "separate_background", False) and probe.has_audio:
            tracks += SEPARATION_PEAK_TRACKS
        required += tracks * (milliseconds * _b2_sample_rate(config.app_root) // 1000 + 1) * 4
    held, free = _held_bytes(work_dir), shutil.disk_usage(config.output_dir).free
    if free + held < required:
        need, have = (required - held + 10**9 - 1) // 10**9, free // 10**9
        raise ProductionJobError("STORAGE_INSUFFICIENT",
            f"Ổ đĩa chứa thư mục xuất cần khoảng {need} GB trống cho video này nhưng chỉ còn {have} GB. "
            "Hãy giải phóng dung lượng hoặc chọn thư mục xuất trên ổ khác rồi xử lý lại.")


def _source_render_duration(probe: MediaProbeResult) -> MediaTimeline | None:
    video = probe.video
    if video.time_base is not None and video.duration_ticks is not None and video.duration_ticks > 0:
        return MediaTimeline(video.time_base, 0, video.duration_ticks, video.duration_ticks)
    ticks = probe.duration_ticks
    return MediaTimeline(CANONICAL_TIME_BASE, 0, ticks, ticks) if ticks is not None and ticks > 0 else None


def _source_clock_mapping(probe: MediaProbeResult) -> dict[str, Any]:
    video = probe.video
    audio_origin_samples(video, 16_000)  # Never guess from duration, FPS or container floats.
    return {"schema_version": 1, "recipe": AUDIO_DECODE_RECIPE,
            "source_video_origin": {"ticks": str(video.start_pts),
                                    "time_base": {"numerator": str(video.time_base.numerator), "denominator": str(video.time_base.denominator)}},
            "output_video_origin_ticks": "0", "pcm_origin_rounding": "floor",
            "analysis_sample_rate": 16_000, "analysis_source_origin_samples": str(audio_origin_samples(video, 16_000))}


def _audio_decode_identity(source_hash: str, probe: MediaProbeResult) -> str:
    return sha256(json.dumps({"source_hash": source_hash, "clock_mapping": _source_clock_mapping(probe),
                             "channels": 1, "adapter": _sha256(Path(__file__).parents[1] / "media" / "adapter.py")},
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    _storage_admission(config, probe, work_dir)

    audio_path = work_dir / "analysis.wav"
    audio_identity = _audio_decode_identity(source_hash, probe)
    media = FfmpegMediaAdapter(config.ffmpeg_path, trusted_root=config.media_runtime_root)
    sidecar = _load_sidecar(config.source_path)
    if sidecar is None or config.source_language == "auto":
        if not probe.has_audio:
            if sidecar is not None:
                raise ProductionJobError("SOURCE_LANGUAGE_UNRESOLVED", "sidecar without audio requires an explicit source language")
            raise ProductionJobError("AUDIO_STREAM_MISSING", "ASR requires an audio stream when no sidecar captions are present")
        if checkpoint["stages"].get("audio", {}).get("input_hash") != audio_identity or not _stage_ready(checkpoint, "audio", (audio_path,)):
            emitter.progress(0.08, "Đang trích xuất audio")
            media.extract_audio(config.source_path, audio_path, video_stream=probe.video, overwrite=True)
            _write_stage(checkpoint, checkpoint_path, "audio", {"path": str(audio_path), "sha256": _sha256(audio_path), "input_hash": audio_identity, "clock_mapping": _source_clock_mapping(probe)})
            emitter.checkpoint("audio", _sha256(audio_path))

    transcript_path = work_dir / "transcript.json"
    from engine.dubflow.worker.whisper_cues import ASR_RECIPE
    transcript_input = "sha256:" + sha256(json.dumps({"source_hash": source_hash, "audio_decode_identity": audio_identity, "requested_language": config.source_language, "sidecar_cues": [cue.to_dict() for cue in sidecar] if sidecar is not None else None, "asr_profile_hash": _sha256(profile_path), "recipe": ASR_RECIPE}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    transcript = None
    asr_warnings: list[str] = []
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
            device = _asr_device(config, emitter, asr_warnings)
            label = [device.label]
            emitter.progress(0.18, f"Đang nhận dạng lời thoại bằng {label[0]}")
            def asr_checkpoint(done: int, total: int, chunk_id: str, artifact_hash: str) -> None:
                emitter.progress(0.18 + 0.19 * done / total, f"Đang nhận dạng lời thoại bằng {label[0]}", units_done=done, units_total=total)
                emitter.checkpoint("asr:" + chunk_id, artifact_hash)
            def asr_fallback(index: int, error: Exception) -> None:
                label[0] = "model CPU"
                detail = " ".join(f"{type(error).__name__}: {error}".replace("\x00", " ").split())[:300]
                asr_warnings.append(f"ACCELERATOR_FALLBACK: ASR chunk {index} and later chunks ran on the CPU after a GPU failure: {detail}")
            transcript = _transcribe_with_faster_whisper(audio_path, config.model_root, config.source_language, chunks_dir=work_dir / "asr-chunks", model_binding=transcript_input, on_chunk=asr_checkpoint,
                                                         device=device, on_fallback=asr_fallback)
            transcript_source = "faster-whisper"
        cues = transcript.cues
        _atomic_json(transcript_path, {"schema_version": 4, "input_hash": transcript_input, "source": transcript_source, "requested_source_language": config.source_language, **transcript.language_metadata(), "asr_evidence": transcript.asr_evidence, "cues": [cue.to_dict() for cue in cues]})
        _write_stage(checkpoint, checkpoint_path, "transcript", {"source": transcript_source, "path": str(transcript_path), "sha256": _sha256(transcript_path)})
    emitter.progress(0.38, "Đã nhận dạng lời thoại", units_done=len(cues), units_total=len(cues))
    emitter.checkpoint("transcript", _sha256(work_dir / "transcript.json"))

    warnings: list[str] = list(asr_warnings)
    cues, looped = _without_repeat_loops(cues)
    if looped:
        warnings.append(f"ASR_REPETITION_COLLAPSED: {looped} cue(s) had a repeated-token loop removed")
    speaker_result, casting = None, {}
    voice_by_cue = None
    speaker_summary = None
    if config.enable_dubbing and getattr(config, "multi_voice", False):
        speaker_result, casting = _multi_voice_cast(config, source_hash, cues, probe, media, work_dir,
                                                   checkpoint, checkpoint_path, emitter, warnings)
        if speaker_result is not None:
            voice_by_cue = {identifier: casting[speaker] for identifier, speaker in speaker_result.labels.items()}
            speaker_summary = [{"id": speaker["id"], "gender": speaker["gender"],
                "voice_id": casting[speaker["id"]], "cues": speaker["cues"]} for speaker in speaker_result.speakers]

    translation_path = work_dir / "translation.json"
    try:
        translation_runtime = create_translation_runtime(getattr(config, "translation_engine", "argos"), config.app_root, config.model_root, transcript.source_language)
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
        emitter.progress(0.44, "Đang dịch bằng HachimiMT-60 (thử nghiệm)" if translation_provenance.get("backend", "").startswith("hachimi") else "Đang dịch cục bộ sang tiếng Việt")
        translated, _ = _without_repeat_loops(_translate_with_argos(cues, config.model_root, transcript.source_language, runtime=translation_runtime, chunks_dir=work_dir / "translation-chunks"))
        _atomic_json(translation_path, {"schema_version": 2, "source_language": transcript.source_language, "target_language": "vi", "provenance": translation_provenance, "input_hash": translation_input, "cues": [cue.to_dict() for cue in translated]})
        _write_stage(checkpoint, checkpoint_path, "translation", {"path": str(translation_path), "sha256": _sha256(translation_path)})
    translated, overrides_identity = _apply_translation_overrides(translated, config.output_dir)
    emitter.progress(0.58, "Đã dịch lời thoại", units_done=len(translated), units_total=len(translated))
    emitter.checkpoint("translation", _sha256(work_dir / "translation.json"))

    srt_path, ass_path = _write_subtitles(export_dir, translated, box=getattr(config, "subtitle_box", False))
    _write_stage(checkpoint, checkpoint_path, "subtitles", {"srt": str(srt_path), "ass": str(ass_path), "srt_sha256": _sha256(srt_path), "ass_sha256": _sha256(ass_path)})
    emitter.progress(0.66, "Đã tạo subtitle SRT/ASS")
    emitter.checkpoint("subtitles", _sha256(srt_path))

    b2_audio: B2AudioResult | None = None
    audio_path: Path | None = None
    audio_metadata: dict[str, Any] = {"mode": "original", "backend": "source-audio"}
    background_path, background_summary = None, {"mode": "original"}
    if config.enable_dubbing and getattr(config, "separate_background", False) and probe.has_audio:
        background_path, background_summary = _separate_background_stage(config, source_hash, probe, media,
            work_dir, checkpoint, checkpoint_path, emitter, warnings)
    if config.enable_dubbing:
        emitter.progress(0.70, "Đang tổng hợp giọng Việt CPU và trộn audio AUD-0")
        last_report = [0.0]

        def dubbing_progress(done: int, total: int) -> None:
            # Resumed phrases settle in milliseconds; one report a second is enough.
            now = time.monotonic()
            if done < total and now - last_report[0] < 1.0:
                return
            last_report[0] = now
            emitter.progress(0.70 + 0.14 * done / total,
                             f"Đang lồng tiếng Việt: đoạn {done}/{total}" if done < total else "Đang trộn giọng Việt với audio nền",
                             units_done=done, units_total=total)
        try:
            from engine.dubflow.worker.b2_audio import tts_recipe_identity, mix_recipe_identity
            selected_voice = getattr(config, "tts_voice_id", None)
            def dub(cue_voices):
                nonlocal background_path, background_summary
                casting_identity = json.dumps({"casting": casting, "voice_by_cue": cue_voices,
                    "speaker_by_cue": speaker_result.labels}, sort_keys=True) if cue_voices is not None else ""
                bed_hash = None
                if background_path is not None:
                    try:
                        bed_hash = _sha256(background_path)
                    except ProductionJobError as error:
                        warnings.append(f"SEPARATION_SKIPPED: {error.code}: {error.condition}")
                        background_path, background_summary = None, {"mode": "original"}
                bed_identity = json.dumps({"requested": True, "mode": background_summary["mode"],
                    "input_hash": background_summary.get("input_hash"), "sha256": bed_hash}, sort_keys=True) if getattr(config, "separate_background", False) else ""
                generation_identity = sha256((translation_input + (overrides_identity or "") + audio_identity + _sha256(profile_path) + _sha256(Path(__file__).with_name("b2_audio.py")) + tts_recipe_identity(config.app_root, profile_path) + mix_recipe_identity() + json.dumps(selected_voice) + casting_identity + bed_identity).encode()).hexdigest()
                try:
                    return run_b2_audio(
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
                        # Shared by every generation in this output folder, so a reviewed
                        # re-run synthesises only the cues whose request changed.
                        synthesis_cache_dir=work_dir / "tts-cache",
                        **({"voice_by_cue": cue_voices, "speaker_by_cue": speaker_result.labels} if cue_voices is not None else {}),
                        **({"background_path": background_path} if background_path is not None else {}),
                        on_progress=dubbing_progress,
                    )
                except B2AudioError as error:
                    if background_path is None or error.code != "SEPARATION_BED_FAILED":
                        raise
                    warnings.append(f"SEPARATION_SKIPPED: {error.code}: {error.condition}")
                    background_path, background_summary = None, {"mode": "original"}
                    return dub(cue_voices)  # One changed-condition retry with AUD-0.

            try:
                b2_audio = dub(voice_by_cue)
            except B2AudioError as error:
                if voice_by_cue is None:
                    raise
                warnings.append(f"MULTI_VOICE_SKIPPED: grouped synthesis failed: {error.code}: {error.condition[:300]}")
                # One changed-condition retry: remove the experimental casting.
                # Its separate generation cannot overwrite grouped artifacts.
                b2_audio = dub(None)
                voice_by_cue = None
                for speaker in speaker_summary:
                    speaker["voice_id"] = b2_audio.voice.voice_id
            tts_documents = getattr(b2_audio, "tts_documents", (b2_audio.tts_document,))
            members = getattr(b2_audio, "member_cue_ids", None) or {}
            audio_path = b2_audio.final_mix_path
            audio_metadata = {
                "mode": "dubbed",
                "source_audio_origin": "decoded-source" if probe.has_audio else "generated-silence",
                "backend": b2_audio.tts_document.provenance.backend_id,
                "voice_id": b2_audio.voice.voice_id,
                "voice_version": b2_audio.voice.voice_version,
                "voice_hash": b2_audio.voice.content_hash(),
                "tts_document": str(b2_audio.tts_document_path),
                "mix_document": str(b2_audio.mix_document_path),
                "mix_provenance": b2_audio.mix_document.provenance.to_dict(),
                "tts_failures": len({cue for document in tts_documents for failure in document.failures
                                     for cue in members.get(failure.segment_id, (failure.segment_id,))}),
                "mix_failures": len({cue for failure in b2_audio.mix_document.failures
                                     for cue in members.get(failure.segment_id, (failure.segment_id,))}),
                "mix_warnings": list(b2_audio.mix_document.warnings),
                "bed": "spleeter-2stems" if background_path is not None else "source",
                "undubbed": _undubbed_summary(translated, members, (*(failure.segment_id for document in tts_documents
                    for failure in document.failures), *(failure.segment_id for failure in b2_audio.mix_document.failures))),
            }
            if voice_by_cue is not None:
                audio_metadata["voices"] = list(b2_audio.voices)
            if b2_audio.placement_document_path is not None:
                audio_metadata["dubbing_placement"] = {"schema_version": 1, "path": str(b2_audio.placement_document_path),
                    "sha256": _sha256(b2_audio.placement_document_path)}
                measured = getattr(b2_audio, "gap_evidence", None) == MEASURED_GAP_EVIDENCE
                warnings.extend(warning.replace("an unverified inter-cue gap", "an inter-cue gap measured silent on the separated dialogue")
                                if measured else warning
                                for document in tts_documents for warning in document.warnings if warning.startswith("TTS_INTERCUE_GAP_USED:"))
            if b2_audio.spoken_text_document_path is not None:
                audio_metadata["dubbing_spoken_text"] = {"schema_version": 1,
                    "path": str(b2_audio.spoken_text_document_path), "sha256": _sha256(b2_audio.spoken_text_document_path)}
                warnings.extend(warning for document in tts_documents for warning in document.warnings
                                if warning.startswith(("TTS_DURATION_REWRITE_APPLIED:", "TTS_SPOKEN_TEXT_CHECKPOINT_INVALID:",
                                                       "TTS_RENDER_WINDOW_WIDENED:", "TTS_FAST_FIT:", "TTS_DURATION_FIT_REFUSED:")))
            warnings.extend(warning for warning in b2_audio.mix_document.warnings
                            if warning.startswith("SEPARATION_SOURCE_RESTORED:"))
            _write_stage(
                checkpoint,
                checkpoint_path,
                "b2-audio",
                {
                    "source_audio": str(b2_audio.source_audio_path),
                    "tts_document": str(b2_audio.tts_document_path),
                    "tts_documents": [str(path) for path in getattr(b2_audio, "tts_document_paths", (b2_audio.tts_document_path,))],
                    "mix_document": str(b2_audio.mix_document_path),
                    "final_mix": str(b2_audio.final_mix_path),
                    "dialogue_stem": str(b2_audio.dialogue_stem_path),
                    "bed": audio_metadata["bed"],
                    "sha256": _sha256(b2_audio.final_mix_path),
                },
            )
            if any(document.failures for document in tts_documents) or b2_audio.mix_document.failures:
                warnings.append("B2_AUDIO_DEGRADED: one or more dialogue cues used bounded phrase fallback; "
                                + ("source audio was preserved" if probe.has_audio else "source has no audio; refused cues remain silent"))
            emitter.checkpoint("b2-audio", _sha256(b2_audio.final_mix_path))
        except B2AudioError as error:
            b2_audio = None
            audio_path = None
            if error.review:
                _write_stage(checkpoint, checkpoint_path, "b2-audio", {**error.review, "status": "failed"})
                audio_metadata.update({key: error.review[key] for key in ("tts_failures", "mix_failures") if key in error.review})
            warnings.append(f"B2_AUDIO_FALLBACK_TO_B1: {error.code}: {error.condition}")
            emitter.progress(0.70, "B2 audio không khả dụng; giữ Vietsub và audio gốc" if probe.has_audio
                             else "Video nguồn không có audio; tiếp tục xuất Vietsub")

    final_path = export_dir / "final_vi.mp4"
    render_stage = "render-dubbed" if audio_path is not None else "render"
    video_duration = _source_render_duration(probe)
    blur_regions = tuple(getattr(config, "blur_regions", ()))
    caption_scan_summary: dict[str, Any] | None = None
    if getattr(config, "blur_auto", False):
        detected, caption_scan_summary = _auto_blur_regions(config, source_hash, probe.duration_ticks, cues, work_dir,
                                                            checkpoint, checkpoint_path, emitter, warnings)
        # Manual regions keep their slots when the combined list would be too long.
        blur_regions = (blur_regions + detected)[:MAX_BLUR_REGIONS]
    # A successful B2 run always re-renders against the newly verified mix.
    # This prevents a changed voice-pack hash or regenerated mix from being
    # hidden by a stale final-video checkpoint after a resumable restart.
    def render_identity() -> str:
        return sha256(json.dumps({"source": source_hash, "translation": translation_input, "subtitles": _sha256(ass_path), "burn_in": config.burn_in_subtitles, "audio": _sha256(audio_path) if audio_path is not None else "original", "recipe": "h264-aac-source-duration-v2", "duration": video_duration.to_dict() if video_duration else None, "adapter": _sha256(Path(__file__).parents[1] / "media" / "adapter.py"), **({"blur": [region.to_dict() for region in blur_regions]} if blur_regions else {})}, sort_keys=True).encode()).hexdigest()

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
        emitter.progress(0.86, "Đang render video H.264/AAC")
        try:
            media.render(
                config.source_path,
                final_path,
                subtitle_path=ass_path,
                audio_path=audio_path,
                video_duration=video_duration,
                source_probe=probe,
                preserve_original_audio=audio_path is None,
                burn_in_subtitles=config.burn_in_subtitles,
                blur_regions=blur_regions,
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
                source_probe=probe,
                burn_in_subtitles=config.burn_in_subtitles,
                blur_regions=blur_regions,
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
            source_probe=probe,
            burn_in_subtitles=config.burn_in_subtitles,
            blur_regions=blur_regions,
            overwrite=True,
        )
        output_probe = MediaProbe(config.ffprobe_path, trusted_root=config.media_runtime_root).probe(final_path)
        validate_output(output_probe)
        _write_stage(checkpoint, checkpoint_path, "render", {"path": str(final_path), "sha256": _sha256(final_path), "audio_mode": "original", "input_hash": render_identity()})
    output_duration = output_probe.duration_ticks
    if b2_audio is None:
        background_summary = {"mode": "original"}
    qc_path = export_dir / "qc_report.json"
    qc = {"schema_version": 1, "status": "passed", "source_probe": probe.to_dict(), "output_probe": output_probe.to_dict(), "audio": audio_metadata,
          **({"caption_blur": {"auto": caption_scan_summary, "regions": len(blur_regions)}} if blur_regions or caption_scan_summary else {}), "warnings": warnings, "downgrade": any(not warning.startswith(INFORMATIONAL_WARNINGS) for warning in warnings), "validated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if speaker_summary is not None:
        qc["speakers"] = speaker_summary
    qc["background"] = background_summary
    _atomic_json(qc_path, qc)
    editable_dir = export_dir / "editable"
    editable_names = {"captions_vi.srt", "captions_vi.ass", "timeline.json"}
    if b2_audio is not None:
        editable_names.update({"source_audio.wav", "dialogue_stem.wav", "final_mix.wav"})
        if getattr(b2_audio, "background_stem_path", None) is not None:
            editable_names.add("background_stem.wav")
        if b2_audio.placement_document_path is not None:
            editable_names.add("dubbing_placement.json")
        if b2_audio.spoken_text_document_path is not None:
            editable_names.add("dubbing_spoken_text.json")
        editable_names.update(path.name for path in getattr(b2_audio, "spoken_text_document_paths", ()))
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
            (b2_audio.original_audio_path, "source_audio.wav", _sha256(b2_audio.original_audio_path) if background_path is not None else b2_audio.mix_document.original_audio.content_hash),
            (b2_audio.dialogue_stem_path, "dialogue_stem.wav", b2_audio.mix_document.dialogue_stem.content_hash),
            (b2_audio.final_mix_path, "final_mix.wav", b2_audio.mix_document.final_mix.content_hash),
        ]
        if getattr(b2_audio, "background_stem_path", None) is not None:
            # The mixer may have used a bed with original audio restored over
            # undubbed phrases; the editable stem is always the pure accompaniment.
            editable_audio.append((b2_audio.background_stem_path, "background_stem.wav", _sha256(b2_audio.background_stem_path)))
        if b2_audio.placement_document_path is not None:
            editable_audio.append((b2_audio.placement_document_path, "dubbing_placement.json", _sha256(b2_audio.placement_document_path)))
        if b2_audio.spoken_text_document_path is not None:
            editable_audio.append((b2_audio.spoken_text_document_path, "dubbing_spoken_text.json", _sha256(b2_audio.spoken_text_document_path)))
        for path in getattr(b2_audio, "spoken_text_document_paths", ())[1:]:
            editable_audio.append((path, path.name, _sha256(path)))
        copied = [name for source, name, expected_hash in editable_audio
                  if not _copy_editable_artifact(source, editable_dir / name, expected_hash=expected_hash, link=name.endswith(".wav"))
                  and name.endswith(".wav")]
        if copied:
            warnings.append("EDITABLE_LINK_FALLBACK: the output volume cannot hard-link; editable audio was copied: " + ", ".join(copied))
            qc["downgrade"] = True
            _atomic_json(qc_path, qc)
        # The separation stem, the generation's bed copy and the mixer's source
        # snapshot repeat the editable tracks byte for byte.
        _share_identical((*(editable_dir / name for _, name, _ in editable_audio if name.endswith(".wav")),
                          *((background_path,) if background_path is not None else ()), b2_audio.source_audio_path,
                          Path(b2_audio.mix_document.original_audio.path)))
    timeline_path = editable_dir / "timeline.json"
    timeline_cues = [dict(cue.to_dict(), **({"speaker": speaker_result.labels[cue.cue_id]} if speaker_result is not None else {})) for cue in translated]
    _atomic_json(timeline_path, {"schema_version": 1, "time_base": {"numerator": 1, "denominator": TIMELINE_DENOMINATOR}, "duration_ticks": output_duration, "cues": timeline_cues, "source_clock_mapping": _source_clock_mapping(probe)})
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
        if getattr(b2_audio, "background_stem_path", None) is not None:
            artifacts["background_stem"] = b2_audio.background_stem_path
            artifacts["editable_background_stem"] = editable_dir / "background_stem.wav"
        if b2_audio.placement_document_path is not None:
            artifacts["dubbing_placement"] = b2_audio.placement_document_path
            artifacts["editable_dubbing_placement"] = editable_dir / "dubbing_placement.json"
        if b2_audio.spoken_text_document_path is not None:
            artifacts["dubbing_spoken_text"] = b2_audio.spoken_text_document_path
            artifacts["editable_dubbing_spoken_text"] = editable_dir / "dubbing_spoken_text.json"
        for path in getattr(b2_audio, "tts_document_paths", ())[1:]:
            artifacts[path.stem] = path
        for path in getattr(b2_audio, "spoken_text_document_paths", ())[1:]:
            artifacts[path.stem] = path
            artifacts["editable_" + path.stem] = editable_dir / path.name
    manifest_path = _write_manifest(export_dir, config, probe, translated, artifacts, warnings, audio=audio_metadata, background=background_summary, language=transcript.language_metadata(), translation=dict(translation_provenance, reviewed_overrides=overrides_identity), asr=transcript.asr_evidence,
                                    **({"speakers": speaker_summary} if speaker_summary is not None else {}))
    _write_stage(checkpoint, checkpoint_path, "qc", {"path": str(qc_path), "sha256": _sha256(qc_path), "manifest": str(manifest_path)})
    emitter.checkpoint("qc", _sha256(qc_path))
    return {"final_video": str(final_path), "srt": str(srt_path), "ass": str(ass_path), "qc": str(qc_path), "manifest": str(manifest_path),
            **({"b2_generation": str(b2_audio.tts_document_path.parent)} if b2_audio is not None else {})}


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
