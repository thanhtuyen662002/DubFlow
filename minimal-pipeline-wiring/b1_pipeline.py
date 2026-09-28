"""Deterministic orchestration for DubFlow's first user-value slice.

Issue #56 deliberately composes the already versioned adapter boundaries.  It
does not introduce a second media contract or make a renderer, OCR engine,
live website, GPU, or system executable part of the product path.  The
fixture backends make the complete flow executable in offline CPU CI while
keeping the seams used by production implementations explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from engine.dubflow.asr import (
    AdapterConfig,
    AsrBackend,
    BackendResult,
    ChunkCheckpoint,
    DeterministicFixtureBackend as AsrFixtureBackend,
    LocalAsrAdapter,
    Provenance as AsrProvenance,
    RawUtterance,
    RawWord,
    TimeBase,
    TimeInterval,
    TimePoint,
    Transcript,
    VadSegment,
)
from engine.dubflow.export import (
    DeterministicFixtureRenderer,
    ExportAsset,
    ExportConfig,
    ExportError,
    ExportProvenance,
    ExportRequest,
    ExportResult,
    LocalExportAdapter,
    MediaAsset,
    RenderBackend,
)
from engine.dubflow.subtitle.render import (
    LocalSubtitleComposer,
    SubtitleConfig,
    SubtitleDocument,
    SubtitleInput,
    SubtitleProvenance,
)
from engine.dubflow.translation import (
    DeterministicFixtureBackend as TranslationFixtureBackend,
    LocalTranslationAdapter,
    SourceSegment,
    TranslationBackend,
    TranslationConfig,
    TranslationDocument,
    TranslationProvenance,
)


PIPELINE_SCHEMA_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = 1
BASE = TimeBase(1, 1000)
SOURCE_BURNED_IN_TEXT_REMAINS = "SOURCE_BURNED_IN_TEXT_REMAINS"


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _point_wire(point: TimePoint) -> dict[str, int]:
    return {
        "ticks": point.ticks,
        "numerator": point.time_base.numerator,
        "denominator": point.time_base.denominator,
    }


def _point_from_wire(value: Mapping[str, Any]) -> TimePoint:
    return TimePoint(
        int(value["ticks"]),
        TimeBase(int(value["numerator"]), int(value["denominator"])),
    )


def _word_wire(word: RawWord) -> dict[str, Any]:
    return {
        "word_id": word.word_id,
        "text": word.text,
        "start": _point_wire(word.start),
        "end": _point_wire(word.end),
        "confidence": word.confidence,
    }


def _word_from_wire(value: Mapping[str, Any]) -> RawWord:
    return RawWord(
        str(value["word_id"]),
        str(value["text"]),
        _point_from_wire(value["start"]),
        _point_from_wire(value["end"]),
        float(value["confidence"]),
    )


def _utterance_wire(utterance: RawUtterance) -> dict[str, Any]:
    return {
        "utterance_id": utterance.utterance_id,
        "start": _point_wire(utterance.start),
        "end": _point_wire(utterance.end),
        "language": utterance.language,
        "text": utterance.text,
        "words": [_word_wire(word) for word in utterance.words],
        "confidence": utterance.confidence,
    }


def _utterance_from_wire(value: Mapping[str, Any]) -> RawUtterance:
    return RawUtterance(
        str(value["utterance_id"]),
        _point_from_wire(value["start"]),
        _point_from_wire(value["end"]),
        str(value["language"]),
        str(value["text"]),
        tuple(_word_from_wire(item) for item in value["words"]),
        float(value["confidence"]),
    )


def _backend_result_wire(result: BackendResult) -> dict[str, Any]:
    return {
        "utterances": [_utterance_wire(item) for item in result.utterances],
        "vad_segments": [
            {
                "start": _point_wire(item.start),
                "end": _point_wire(item.end),
                "confidence": item.confidence,
            }
            for item in result.vad_segments
        ],
    }


def _backend_result_from_wire(value: Mapping[str, Any]) -> BackendResult:
    return BackendResult(
        tuple(_utterance_from_wire(item) for item in value.get("utterances", ())),
        tuple(
            VadSegment(
                _point_from_wire(item["start"]),
                _point_from_wire(item["end"]),
                float(item["confidence"]),
            )
            for item in value.get("vad_segments", ())
        ),
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write a sidecar/checkpoint/report without exposing a partial path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent)
    )
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


@dataclass(frozen=True)
class B1PipelineConfig:
    """Bounded fixture configuration for one local-file B1 job."""

    video_width: int
    video_height: int
    duration_ticks: int = 3000
    rotation: int = 0
    source_has_audio: bool = True
    source_burned_in_text: bool = True
    sample_rate: int = 48000
    asr_chunk_ticks: int = 2000
    asr_overlap_ticks: int = 500

    def __post_init__(self) -> None:
        integer_fields = {
            "video_width": self.video_width,
            "video_height": self.video_height,
            "duration_ticks": self.duration_ticks,
            "sample_rate": self.sample_rate,
            "asr_chunk_ticks": self.asr_chunk_ticks,
            "asr_overlap_ticks": self.asr_overlap_ticks,
        }
        for name, value in integer_fields.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.video_width > 65535 or self.video_height > 65535:
            raise ValueError("video dimensions exceed the export contract limit")
        if self.duration_ticks > (1 << 63) - 1:
            raise ValueError("duration_ticks exceeds the canonical timeline limit")
        if type(self.rotation) is not int or not -360 <= self.rotation <= 360:
            raise ValueError("rotation must be an integer in [-360, 360]")
        if type(self.source_has_audio) is not bool or type(self.source_burned_in_text) is not bool:
            raise ValueError("source capability flags must be boolean")
        if self.asr_overlap_ticks >= self.asr_chunk_ticks:
            raise ValueError("ASR overlap must be smaller than chunk duration")

    @property
    def duration(self) -> TimeInterval:
        return TimeInterval(TimePoint(0, BASE), TimePoint(self.duration_ticks, BASE))

    @property
    def aspect(self) -> str:
        if self.video_width == self.video_height:
            return "square"
        return "landscape" if self.video_width > self.video_height else "portrait"


@dataclass(frozen=True)
class B1PipelineResult:
    transcript: Transcript
    translation: TranslationDocument
    subtitles: SubtitleDocument
    export: ExportResult
    report: Mapping[str, Any]
    report_path: Path
    checkpoint_path: Path
    srt_path: Path
    ass_path: Path
    checkpoint_reused_chunks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "kind": "b1_pipeline_result",
            "report": dict(self.report),
            "report_path": str(self.report_path),
            "checkpoint_path": str(self.checkpoint_path),
            "srt_path": str(self.srt_path),
            "ass_path": str(self.ass_path),
            "checkpoint_reused_chunks": list(self.checkpoint_reused_chunks),
            "audio_mode": self.export.audio_mode,
            "output_path": self.export.output["path"],
        }


class B1PipelineInterrupted(RuntimeError):
    """Raised at the durable analysis checkpoint used by restart tests."""

    def __init__(self, checkpoint_path: Path) -> None:
        self.stage = "analysis"
        self.checkpoint_path = checkpoint_path
        super().__init__(f"B1 pipeline interrupted after analysis checkpoint: {checkpoint_path}")


class RecordingAsrBackend:
    """Record ephemeral worker results so the supervisor can checkpoint them."""

    def __init__(self, delegate: AsrBackend) -> None:
        self.delegate = delegate
        self.calls: list[str] = []
        self.results: dict[str, BackendResult] = {}

    def transcribe(self, chunk: Any) -> BackendResult:
        result = self.delegate.transcribe(chunk)
        self.calls.append(chunk.chunk_id)
        self.results[chunk.chunk_id] = result
        return result


def default_fixture_utterances() -> tuple[RawUtterance, ...]:
    """Stable English source spans used by both portrait and landscape jobs."""

    first_words = (
        RawWord("w-1", "Hello", TimePoint(200, BASE), TimePoint(500, BASE), 0.96),
        RawWord("w-2", "everyone", TimePoint(520, BASE), TimePoint(900, BASE), 0.95),
    )
    second_words = (
        RawWord("w-3", "welcome", TimePoint(1200, BASE), TimePoint(1550, BASE), 0.94),
        RawWord("w-4", "back", TimePoint(1570, BASE), TimePoint(2050, BASE), 0.93),
    )
    return (
        RawUtterance.from_words("u-1", "en", first_words, confidence=0.95),
        RawUtterance.from_words("u-2", "en", second_words, confidence=0.93),
    )


class B1Pipeline:
    """Compose ASR, translation, subtitles, and validated export."""

    def __init__(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        config: B1PipelineConfig | None = None,
        video_width: int | None = None,
        video_height: int | None = None,
        checkpoint_path: str | Path | None = None,
        utterances: Sequence[RawUtterance] | None = None,
        asr_backend: AsrBackend | None = None,
        translation_backend: TranslationBackend | None = None,
        renderer: RenderBackend | None = None,
    ) -> None:
        if config is None:
            if video_width is None or video_height is None:
                raise ValueError("video_width and video_height are required when config is omitted")
            config = B1PipelineConfig(video_width, video_height)
        elif video_width is not None or video_height is not None:
            raise ValueError("pass either config or video dimensions, not both")
        self.config = config
        self.source_path = Path(source_path)
        self.output_path = Path(output_path)
        self.checkpoint_path = (
            Path(checkpoint_path)
            if checkpoint_path is not None
            else self.output_path.with_suffix(".analysis.checkpoint.json")
        )
        try:
            if self.checkpoint_path.resolve() in {self.source_path.resolve(), self.output_path.resolve()}:
                raise ValueError("checkpoint path must not overwrite source or final output")
        except OSError as error:
            raise ValueError(f"invalid checkpoint path: {error}") from error
        self.utterances = tuple(utterances or default_fixture_utterances())
        self.asr_backend = RecordingAsrBackend(asr_backend or AsrFixtureBackend(self.utterances))
        self.translation_backend = translation_backend or TranslationFixtureBackend(
            {"u-1": "Xin chào mọi người", "u-2": "Chào mừng trở lại"}
        )
        self.renderer = renderer or DeterministicFixtureRenderer()
        self._checkpoint_reused_chunks: tuple[str, ...] = ()

    @property
    def analysis_calls(self) -> tuple[str, ...]:
        return tuple(self.asr_backend.calls)

    def run(self, *, hard_kill_after_analysis: bool = False) -> B1PipelineResult:
        if not self.source_path.is_file():
            raise FileNotFoundError(self.source_path)
        if self.output_path.exists():
            raise ExportError("OUTPUT_EXISTS", "final output exists and overwrite is disabled")
        source_hash = _digest_file(self.source_path)

        asr_config = AdapterConfig(
            self.config.asr_chunk_ticks,
            self.config.asr_overlap_ticks,
            requested_profile="fixture",
            max_attempts=2,
        )
        asr_provenance = AsrProvenance(
            "dubflow-b1-pipeline",
            "1.0.0",
            "fixture-asr",
            "fixture-asr-model",
            "1",
            "python-stdlib",
            "timeline-v1",
            asr_config.to_hash(),
            source_hash,
            "fixture",
            "fixture",
        )
        checkpoints = self._load_checkpoints(source_hash, asr_config.to_hash())
        asr = LocalAsrAdapter(self.asr_backend, config=asr_config, provenance=asr_provenance)
        transcript = asr.transcribe(
            self.config.duration,
            input_hash=source_hash,
            checkpoints=checkpoints,
            audio_ref=str(self.source_path),
            sample_rate=self.config.sample_rate,
        )
        self._checkpoint_reused_chunks = tuple(
            item["chunk_id"] for item in transcript.chunks if item.get("status") == "skipped"
        )
        self._write_checkpoints(transcript, checkpoints, source_hash, asr_config.to_hash())
        if hard_kill_after_analysis:
            raise B1PipelineInterrupted(self.checkpoint_path)

        transcript_hash = _digest_bytes(transcript.to_bytes())
        translation_config = TranslationConfig(
            max_items_per_chunk=2,
            context_before=1,
            context_after=1,
            requested_profile="fixture",
            max_attempts=2,
        )
        translation_provenance = TranslationProvenance(
            "dubflow-b1-pipeline",
            "1.0.0",
            "fixture-translation",
            "fixture-vi-model",
            "1",
            "python-stdlib",
            "timeline-v1",
            translation_config.to_hash(),
            _digest_bytes(b"fixture-vi-model-v1"),
            _digest_json({}),
            transcript_hash,
            "fixture",
            "fixture",
        )
        translation = LocalTranslationAdapter(
            self.translation_backend,
            config=translation_config,
            provenance=translation_provenance,
        ).translate(
            tuple(SourceSegment.from_asr(item) for item in transcript.utterances),
            input_hash=transcript_hash,
        )

        translation_hash = _digest_bytes(translation.to_bytes())
        subtitle_config = SubtitleConfig(self.config.video_width, self.config.video_height)
        subtitle_provenance = SubtitleProvenance(
            "dubflow-b1-pipeline",
            "1.0.0",
            subtitle_config.to_hash(),
            translation_hash,
            "timeline-v1",
        )
        subtitles = LocalSubtitleComposer(
            config=subtitle_config,
            provenance=subtitle_provenance,
        ).compose(
            tuple(SubtitleInput.from_translation(item) for item in translation.translations),
            input_hash=translation_hash,
        )

        srt_path = self.output_path.with_suffix(".srt")
        ass_path = self.output_path.with_suffix(".ass")
        srt_bytes = subtitles.srt.encode("utf-8")
        ass_bytes = subtitles.ass.encode("utf-8")
        srt_hash = _digest_bytes(srt_bytes)
        ass_hash = _digest_bytes(ass_bytes)
        _atomic_write(srt_path, srt_bytes)
        _atomic_write(ass_path, ass_bytes)

        media = MediaAsset(
            "source",
            str(self.source_path),
            source_hash,
            self.config.duration,
            self.config.video_width,
            self.config.video_height,
            self.config.rotation,
            self.config.source_has_audio,
        )
        # Export v1 accepts one subtitle asset in its canonical editable pack.
        # The SRT sidecar remains beside the richer ASS asset and is included in
        # the B1 report so both standard subtitle forms are delivered.
        subtitle_asset = ExportAsset(
            "subtitle",
            "vietsub-ass",
            str(ass_path),
            ass_hash,
            "ass",
        )
        request = ExportRequest(
            media,
            self.output_path,
            subtitle=subtitle_asset,
            preserve_original_audio=True,
        )
        export_config = ExportConfig(requested_profile="fixture", max_attempts=2)
        export_provenance = ExportProvenance(
            "dubflow-b1-pipeline",
            "1.0.0",
            "fixture-renderer",
            "1",
            "python-stdlib",
            "timeline-v1",
            export_config.to_hash(),
            request.input_hash(),
            "fixture",
            "fixture",
        )
        exported = LocalExportAdapter(
            self.renderer,
            config=export_config,
            provenance=export_provenance,
        ).export(request)

        capability_downgrades = (
            (SOURCE_BURNED_IN_TEXT_REMAINS,)
            if self.config.source_burned_in_text
            else ()
        )
        report = {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "kind": "b1_pipeline_report",
            "execution_mode": "adapter_orchestrated",
            "manual_steps_required": [],
            "orientation": self.config.aspect,
            "source": {
                "path": str(self.source_path),
                "content_hash": source_hash,
                "metadata_source": "caller-supplied normalized local-file metadata (#14 boundary)",
                "burned_in_text_possible": self.config.source_burned_in_text,
            },
            "audio": {
                "policy": "preserve_original",
                "mode": exported.audio_mode,
                "source_has_audio": self.config.source_has_audio,
                "evidence": "export contract audio_mode=original; fixture renderer metadata",
            },
            "capability_downgrades": list(capability_downgrades),
            "subtitle_hashes": {"srt": srt_hash, "ass": ass_hash},
            "artifacts": {
                "mp4": exported.output["path"],
                "srt": str(srt_path),
                "ass": str(ass_path),
                "editable_pack": exported.editable_pack["path"],
            },
            "analysis": {
                "checkpoint_path": str(self.checkpoint_path),
                "reused_chunks": list(self._checkpoint_reused_chunks),
                "total_chunks": len(transcript.chunks),
            },
            "renderer": {
                "profile": "fixture",
                "contract": "mp4/h264/aac-compatible metadata",
                "production_backend_required_for_playable_media": True,
            },
        }
        report_path = self.output_path.with_suffix(".b1-report.json")
        _atomic_write(report_path, (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        return B1PipelineResult(
            transcript,
            translation,
            subtitles,
            exported,
            report,
            report_path,
            self.checkpoint_path,
            srt_path,
            ass_path,
            self._checkpoint_reused_chunks,
        )

    def _load_checkpoints(self, input_hash: str, config_hash: str) -> dict[str, ChunkCheckpoint]:
        if not self.checkpoint_path.is_file():
            return {}
        try:
            value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                return {}
            if value.get("kind") != "b1_analysis_checkpoint":
                return {}
            if value.get("input_hash") != input_hash or value.get("config_hash") != config_hash:
                return {}
            result: dict[str, ChunkCheckpoint] = {}
            for item in value.get("chunks", ()):
                checkpoint = ChunkCheckpoint(
                    str(item["chunk_id"]),
                    str(item["artifact_hash"]),
                    _backend_result_from_wire(item["result"]),
                )
                result[checkpoint.chunk_id] = checkpoint
            return result
        except (AttributeError, OSError, TypeError, ValueError, KeyError):
            # A truncated or incompatible checkpoint is data to invalidate,
            # not a reason to fail the whole media item.
            return {}

    def _write_checkpoints(
        self,
        transcript: Transcript,
        loaded: Mapping[str, ChunkCheckpoint],
        input_hash: str,
        config_hash: str,
    ) -> None:
        chunks: list[dict[str, Any]] = []
        for record in transcript.chunks:
            if record.get("status") not in {"completed", "skipped"}:
                continue
            chunk_id = str(record["chunk_id"])
            result = self.asr_backend.results.get(chunk_id)
            if result is None and chunk_id in loaded:
                result = loaded[chunk_id].result
            artifact_hash = record.get("artifact_hash")
            if result is None or not isinstance(artifact_hash, str):
                continue
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "artifact_hash": artifact_hash,
                    "result": _backend_result_wire(result),
                }
            )
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "kind": "b1_analysis_checkpoint",
            "input_hash": input_hash,
            "config_hash": config_hash,
            "time_base": {"numerator": BASE.numerator, "denominator": BASE.denominator},
            "duration_ticks": self.config.duration_ticks,
            "chunks": chunks,
        }
        _atomic_write(
            self.checkpoint_path,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )


__all__ = [
    "B1Pipeline",
    "B1PipelineConfig",
    "B1PipelineInterrupted",
    "B1PipelineResult",
    "RecordingAsrBackend",
    "SOURCE_BURNED_IN_TEXT_REMAINS",
    "default_fixture_utterances",
]
