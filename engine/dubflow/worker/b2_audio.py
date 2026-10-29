"""B2 production audio stage used by the local-file worker.

FFmpeg decodes the actual source audio, a pinned offline neural CPU voice creates
per-phrase WAV artifacts, and the bounded file-based AUD-0 mixer publishes original,
dialogue-stem and ducked final WAVs.  The worker can catch ``B2AudioError``
and continue with the already-valid B1 subtitle render.

Native inference is not deterministic or a speech-quality qualification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
import re
import shutil
from pathlib import Path
import tempfile
import wave
from typing import Any, Callable, Iterable, Mapping, Sequence

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.media import CANONICAL_TIME_BASE, FfmpegMediaAdapter, MediaAdapterError, MediaProbeResult, MediaTimeline
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
from .tts_cache import CachedSynthesisEngine
from .tts_checkpoints import TtsCheckpointStore
from .tts_rewrites import BoundedDurationRewriteEngine, SpokenTextStore


BASE_TIME = TimeBase(1, 1000)
DUBBING_PHRASE_RECIPE = "consecutive-gap250ms-span8000ms-count5-text512-v1"
DUBBING_WINDOW_RECIPE = "source-phrase-postroll-2000ms-gap120ms-v2"
MAX_PHRASE_GAP_MS = 250
MAX_PHRASE_SPAN_MS = 8000
MAX_PHRASE_CUES = 5
MAX_TTS_TEXT_CHARS = 512
MAX_POSTROLL_MS = 2000
MAX_FALLBACK_POSTROLL_MS = 4000
NEXT_CUE_GUARD_MS = 120
B2_TTS_PRODUCER_VERSION = "3.5.0"
# With a separated bed, source minus accompaniment estimates the dialogue. A
# window may then reach into measured silence, up to MEASURED_POSTROLL_MS,
# and never past where undubbed speech resumes.
UNMEASURED_GAP_EVIDENCE = "recognized-source-cue-intervals;silence-not-certified"
MEASURED_GAP_EVIDENCE = "recognized-source-cue-intervals;dialogue-silence-measured-rms260-frame20ms-tail300ms-onset60ms"
MEASURED_POSTROLL_MS = 6000
SILENCE_RMS = 260  # About -42 dBFS.
SILENCE_FRAME_MS = 20
SPEECH_TAIL_MS = 300  # The cue's own last syllable may outlast its recognized end.
SPEECH_ONSET_MS = 60


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
    paths = (metadata, source / "adapter.py", source / "vieneu.py", source / "vieneu_native.py", source / "neural_vits.py", source / "mimic3_native.py", source / "native_process.py", source / "windows_job.py", Path(__file__), Path(__file__).with_name("tts_checkpoints.py"), Path(__file__).with_name("tts_rewrites.py"), Path(__file__).with_name("tts_cache.py"))
    return sha256("".join(_digest(path) for path in paths).encode()).hexdigest()


def mix_recipe_identity() -> str:
    """Pin the selected PCM producer and code before reusing a B2 generation."""
    try:
        recipe = {"producer": streaming.PRODUCER_VERSION, "backend": streaming.BACKEND_ID,
                  "numpy": streaming.NUMPY_VERSION, "adapter": _digest(Path(streaming.__file__)),
                  "bed_recipe": _digest(Path(__file__))}
        return sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, TypeError, ValueError) as error:
        raise B2AudioError("MIX_RECIPE_UNAVAILABLE", "selected mixer recipe cannot be verified") from error


class B2AudioError(RuntimeError):
    """Typed, safe-to-report B2 failure that permits a B1 downgrade."""

    def __init__(self, code: str, condition: str, *, retryable: bool = False,
                 review: Mapping[str, Any] | None = None) -> None:
        self.code = str(code)[:128]
        self.condition = " ".join(str(condition).replace("\x00", " ").split())[:4096]
        self.retryable = bool(retryable)
        self.review = dict(review or {})
        super().__init__(f"{self.code}: {self.condition}")


@dataclass(frozen=True)
class B2AudioResult:
    source_audio_path: Path
    tts_document_path: Path
    mix_document_path: Path
    tts_document: TtsDocument
    mix_document: MixDocument
    voice: VoiceProfile
    placement_document_path: Path | None = None
    spoken_text_document_path: Path | None = None

    voices: tuple[str, ...] = ()
    tts_documents: tuple[TtsDocument, ...] = ()
    tts_document_paths: tuple[Path, ...] = ()
    spoken_text_document_paths: tuple[Path, ...] = ()
    background_stem_path: Path | None = None
    member_cue_ids: Mapping[str, tuple[str, ...]] | None = None
    gap_evidence: str | None = None

    def __post_init__(self) -> None:
        if not self.voices:
            object.__setattr__(self, "voices", (self.voice.voice_id,))
        if not self.tts_documents:
            object.__setattr__(self, "tts_documents", (self.tts_document,))
        if not self.tts_document_paths:
            object.__setattr__(self, "tts_document_paths", (self.tts_document_path,))
        if not self.spoken_text_document_paths and self.spoken_text_document_path is not None:
            object.__setattr__(self, "spoken_text_document_paths", (self.spoken_text_document_path,))

    @property
    def final_mix_path(self) -> Path:
        return Path(self.mix_document.final_mix.path)

    @property
    def dialogue_stem_path(self) -> Path:
        return Path(self.mix_document.dialogue_stem.path)

    @property
    def original_audio_path(self) -> Path:
        if self.background_stem_path is not None:
            return self.source_audio_path
        return Path(self.mix_document.original_audio.path)

    @property
    def bed(self) -> str:
        return "spleeter-2stems" if self.background_stem_path is not None else "source"


def _mix_input_hash(documents: Sequence[TtsDocument]) -> str:
    # A resumed document marks reused chunks skipped and warns about each reuse;
    # neither changes the audio, so neither may select a new mix generation.
    payload = [{key: value for key, value in document.to_dict().items() if key not in ("chunks", "warnings")}
               for document in documents]
    return "sha256:" + sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def _clamped_mappings(cues: Sequence[Any], extent_ms: int) -> tuple[dict[str, Any], ...]:
    """Cue mappings bounded by the media extent.

    VFR or container metadata can put the last recognized cue slightly past
    the video's end. Such a cue is shortened (or dropped when it starts after
    the end) instead of failing every cue's dubbing; the render ends there anyway.
    """
    return tuple(dict(item, end_ms=min(item["end_ms"], extent_ms))
                 for item in map(_cue_mapping, cues) if item["start_ms"] < extent_ms)


def _joined_text(previous: str, following: str) -> str:
    separator = " " if previous.rstrip().endswith((".", "!", "?", "…")) else ", "
    return previous + separator + following


def _dubbing_phrases(mappings: Sequence[Mapping[str, Any]], *, default_voice: str,
                     voice_by_cue: Mapping[str, str] | None = None,
                     speaker_by_cue: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
    """Greedily group consecutive source cues before partitioning by voice.

    Private phrase mappings leave the authoritative per-cue inputs untouched.
    Singletons retain their exact text, confidence, interval and segment ID.
    """
    ordered = sorted(mappings, key=lambda item: (item["start_ms"], item["end_ms"], item["cue_id"]))
    source_ids = {item["cue_id"] for item in ordered}
    if len(source_ids) != len(ordered):
        raise B2AudioError("TTS_INPUT_INVALID", "source cue identity is duplicated")
    phrases = []
    previous = None
    for item in ordered:
        voice = (voice_by_cue or {}).get(item["cue_id"], default_voice)
        if not isinstance(voice, str) or re.fullmatch(r"[a-z0-9-]{1,96}", voice) is None:
            raise B2AudioError("VOICE_ID_UNKNOWN", "invalid mapped voice identifier")
        speaker = (speaker_by_cue or {}).get(item["cue_id"])
        phrase = phrases[-1] if phrases else None
        text = _joined_text(phrase["translated_text"], item["translated_text"]) if phrase else item["translated_text"]
        if (phrase is not None and max(0, item["start_ms"] - previous["end_ms"]) < MAX_PHRASE_GAP_MS
                and (voice, speaker) == (phrase["voice_id"], phrase["speaker_id"])
                and max(phrase["coverage_end_ms"], item["end_ms"]) - phrase["start_ms"] <= MAX_PHRASE_SPAN_MS
                and len(phrase["member_cue_ids"]) < MAX_PHRASE_CUES and len(text) <= MAX_TTS_TEXT_CHARS):
            phrase.update(end_ms=item["end_ms"], translated_text=text,
                          coverage_end_ms=max(phrase["coverage_end_ms"], item["end_ms"]),
                          confidence=min(phrase["confidence"], item["confidence"]))
            phrase["member_cue_ids"].append(item["cue_id"])
        else:
            phrases.append(dict(item, member_cue_ids=[item["cue_id"]], voice_id=voice,
                                speaker_id=speaker, coverage_end_ms=item["end_ms"]))
        previous = item
    used = set(source_ids)
    for phrase in phrases:
        if len(phrase["member_cue_ids"]) > 1:
            identifier = "phrase-" + phrase["cue_id"]
            if len(identifier) > 256 or identifier in used:
                identifier = "phrase-" + sha256(phrase["cue_id"].encode("utf-8")).hexdigest()
                while identifier in used:
                    identifier = "phrase-" + sha256(identifier.encode("utf-8")).hexdigest()
            phrase["cue_id"] = identifier
            used.add(identifier)
    return tuple(phrases)


def _dubbing_windows(mappings: Sequence[Mapping[str, Any]], source_end: int, *, max_postroll_ms: int = MAX_POSTROLL_MS) -> tuple[dict[str, Any], ...]:
    """Derive render limits without changing canonical source cue intervals.

    The gaps are between recognized cues, not certified source silence. An
    overlap on either side keeps the original slot. Planning is O(n log n).
    """
    if type(max_postroll_ms) is not int or not 0 <= max_postroll_ms <= MAX_FALLBACK_POSTROLL_MS:
        raise B2AudioError("TTS_INPUT_INVALID", "postroll exceeds the bounded fit policy")
    ordered = sorted(mappings, key=lambda item: (item["start_ms"], item["end_ms"], item["cue_id"]))
    if len({item["cue_id"] for item in ordered}) != len(ordered):
        raise B2AudioError("TTS_INPUT_INVALID", "source cue identity is duplicated")
    result = []
    prior_end = -1
    for index, item in enumerate(ordered):
        start, end = item["start_ms"], item["end_ms"]
        if not 0 <= start < end <= source_end:
            raise B2AudioError("TTS_INPUT_INVALID", "source cue is outside the decoded/media extent")
        next_start = ordered[index + 1]["start_ms"] if index + 1 < len(ordered) else None
        coverage_end = item.get("coverage_end_ms", end)
        overlaps = start < prior_end or (next_start is not None and next_start < coverage_end)
        window_end = end
        if not overlaps:
            limit = source_end if next_start is None else next_start - NEXT_CUE_GUARD_MS
            window_end = max(end, min(end + max_postroll_ms, limit, source_end))
        result.append({"cue_id": item["cue_id"], "slot_start": TimePoint(start, BASE_TIME).to_dict(),
            "slot_end": TimePoint(end, BASE_TIME).to_dict(), "render_window_end": TimePoint(window_end, BASE_TIME).to_dict(),
            "next_source_start": None if next_start is None else TimePoint(next_start, BASE_TIME).to_dict(),
            "overlaps_source": overlaps, "confidence": item["confidence"]})
        prior_end = max(prior_end, coverage_end)
    return tuple(result)


def _speech_resumes_ms(source: Path, background: Path, spans: Sequence[tuple[str, int, int]]) -> dict[str, int]:
    """Where undubbed speech resumes after each cue, on source minus accompaniment.

    Each span starts at a cue's end. Loud frames within SPEECH_TAIL_MS are the
    cue's own tail; speech still sounding after that keeps the window at the
    cue. Otherwise the first SPEECH_ONSET_MS of loud frames marks the resume,
    and a span silent throughout is wholly usable. Formats that differ give
    no measurement.
    """
    import numpy as np
    result: dict[str, int] = {}
    with wave.open(str(source), "rb") as speech, wave.open(str(background), "rb") as bed:
        shape = (speech.getframerate(), speech.getnchannels(), speech.getsampwidth())
        if shape != (bed.getframerate(), bed.getnchannels(), bed.getsampwidth()) or shape[2] != 2:
            return result
        rate, channels = shape[0], shape[1]
        size = rate * SILENCE_FRAME_MS // 1000
        tail, onset = SPEECH_TAIL_MS // SILENCE_FRAME_MS, SPEECH_ONSET_MS // SILENCE_FRAME_MS
        frames = min(speech.getnframes(), bed.getnframes())
        for identifier, start_ms, end_ms in spans:
            first = start_ms * rate // 1000
            count = (min(frames, end_ms * rate // 1000) - first) // size * size
            if count <= 0:
                continue
            speech.setpos(first)
            bed.setpos(first)
            values = [np.frombuffer(reader.readframes(count), dtype="<i2") for reader in (speech, bed)]
            if any(len(item) != count * channels for item in values):
                continue
            residual = (values[0].astype(np.int64) - values[1]).reshape(-1, size * channels)
            loud = np.sqrt(np.mean(residual * residual, axis=1)) > SILENCE_RMS
            index = 0
            while index < len(loud) and loud[index]:
                index += 1
            if index > tail:
                result[identifier] = start_ms
                continue
            resume = end_ms
            for position in range(index, len(loud) - onset + 1):
                if loud[position:position + onset].all():
                    resume = start_ms + position * SILENCE_FRAME_MS
                    break
            result[identifier] = resume
    return result


def _measured_windows(windows: Sequence[dict[str, Any]], source: Path, background: Path,
                      source_end: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Bound each phrase's windows by measured dialogue silence after it."""
    spans = {}
    for window in windows:
        end, following = int(window["slot_end"]["ticks"]), window["next_source_start"]
        limit = min(end + MEASURED_POSTROLL_MS, source_end,
                    source_end if following is None else int(following["ticks"]) - NEXT_CUE_GUARD_MS)
        if not window["overlaps_source"] and limit > end:
            spans[window["cue_id"]] = (end, limit)
    resumes = _speech_resumes_ms(source, background, [(key, *span) for key, span in spans.items()])
    limits, measured = {}, []
    for window in windows:
        if window["cue_id"] in resumes:
            end, limit = spans[window["cue_id"]]
            resume = resumes[window["cue_id"]]
            # Speech found inside the span keeps the usual guard before it.
            usable = max(end, resume - NEXT_CUE_GUARD_MS if resume < limit else limit)
            window = dict(window, render_window_end=TimePoint(
                min(int(window["render_window_end"]["ticks"]), usable), BASE_TIME).to_dict())
            limits[window["cue_id"]] = usable
        measured.append(window)
    return measured, limits


def _media_extent_ms(probe: MediaProbeResult, decoded_end: int) -> int:
    video = getattr(probe, "video", None)
    base, ticks = getattr(video, "time_base", None), getattr(video, "duration_ticks", None)
    if base is None or ticks is None:
        base, ticks = CANONICAL_TIME_BASE, getattr(probe, "duration_ticks", None)
    if base is not None and type(ticks) is int and ticks > 0:
        duration = ticks * base.numerator * 1000 // base.denominator
        if duration > 0:
            return min(decoded_end, duration)
    return decoded_end


def _restore_source_windows(background: Path, source: Path, output: Path,
                            windows: Sequence[tuple[int, int]], *, block_frames: int = 262144) -> None:
    """Stream a disposable PCM16 bed; union failed slots before 20 ms fades."""
    import numpy as np
    limits = streaming.StreamLimits()
    original = streaming._wave(source, limits.max_source_bytes)
    bed = streaming._wave(background, limits.max_source_bytes)
    if (original.rate != bed.rate or original.channels != bed.channels
            or abs(original.frames - bed.frames) * 1000 > original.rate * 50):
        raise B2AudioError("SEPARATION_BED_FAILED", "accompaniment format/duration differs from source")
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        start = max(0, start * original.rate // 1000)
        end = min(original.frames, (end * original.rate + 999) // 1000)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    streaming._plain(output)
    descriptor, name = tempfile.mkstemp(prefix=".restored-bed-", suffix=".partial", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle, wave.open(str(background), "rb") as bg, wave.open(str(source), "rb") as src:
            with wave.open(handle, "wb") as writer:
                writer.setparams((original.channels, 2, original.rate, 0, "NONE", "not compressed"))
                first = 0
                for offset in range(0, original.frames, block_frames):
                    count = min(block_frames, original.frames - offset)
                    raw = bg.readframes(count)
                    raw += bytes(count * original.channels * 2 - len(raw))
                    values = np.frombuffer(raw, dtype="<i2").reshape(count, original.channels).astype(np.int64)
                    source_values = np.frombuffer(src.readframes(count), dtype="<i2").reshape(count, original.channels).astype(np.int64)
                    while first < len(merged) and merged[first][1] <= offset:
                        first += 1
                    for index in range(first, len(merged)):
                        start, end = merged[index]
                        if start >= offset + count:
                            break
                        left, right = max(start, offset), min(end, offset + count)
                        positions = np.arange(left, right, dtype=np.int64)
                        fade = max(1, min(original.rate * 20 // 1000, (end - start - 1) // 2))
                        weight = np.minimum(fade, np.minimum(positions - start, end - 1 - positions))
                        if end - start < 3:
                            weight[:] = fade
                        weight = weight[:, None]
                        selection = slice(left - offset, right - offset)
                        values[selection] = (values[selection] * (fade - weight) + source_values[selection] * weight) // fade
                    writer.writeframesraw(values.astype("<i2").tobytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _synthesize_voice(*, pack, voice, engine, resources, backend_id, producer_version, runtime_id,
                      mappings, windows_by_id, input_hash, source_language, tts_dir,
                      app_root, profile_path, synthesis_cache_dir, render_window_limits=None, allow_all_failed=False,
                      on_segment=None):
    """One voice's existing synthesis/checkpoint/cache/rewrite pipeline."""
    try:
        tts_config = TtsConfig(
            sample_rate=pack.sample_rate,
            channels=pack.channels,
            requested_profile="cpu",
            max_attempts=1,
            max_text_chars=MAX_TTS_TEXT_CHARS,
            max_duration_error_ticks=80,
            min_speed_ratio_milli=800,
            max_speed_ratio_milli=1300,
            resource=resources,
        )
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
                render_window_end=TimePoint(int(windows_by_id[item["cue_id"]]["render_window_end"]["ticks"]), BASE_TIME) if windows_by_id else None,
            )
            for item in mappings
        )
        checkpoint_identity = sha256(json.dumps({"provenance": provenance.to_dict(),
            "recipe": tts_recipe_identity(Path(app_root), Path(profile_path))},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checkpoints = TtsCheckpointStore(tts_dir, identity=checkpoint_identity)
        if synthesis_cache_dir is not None:
            # Same recipe minus the document-wide input hash: one edited cue
            # must not invalidate the speech of every other cue.
            cache_identity = sha256(json.dumps({"provenance": {key: value for key, value in provenance.to_dict().items() if key != "input_hash"},
                "recipe": tts_recipe_identity(Path(app_root), Path(profile_path))},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            # Inside the rewrite wrapper, so rewritten spoken text is cached too.
            engine = CachedSynthesisEngine(engine, Path(synthesis_cache_dir), identity=cache_identity)
        spoken_store = None
        reusable = checkpoints.load(segments)
        on_checkpoint = checkpoints.commit
        if backend_id == vieneu.ENGINE_ID:
            spoken_store = SpokenTextStore(tts_dir, identity=checkpoint_identity, segments=segments)
            engine = BoundedDurationRewriteEngine(engine, spoken_store, render_window_limits=render_window_limits)
            reusable = spoken_store.filter_checkpoints(reusable)

            def on_checkpoint(checkpoint):
                # The adapter has already validated and atomically published the
                # waveform. Evidence precedes the reusable checkpoint; an orphan
                # receipt cannot authorize reuse when the checkpoint is absent.
                spoken_store.commit(checkpoint)
                checkpoints.commit(checkpoint)

        tts_document = LocalTtsAdapter(
            engine,
            config=tts_config,
            provenance=provenance,
            voice=voice,
            output_dir=tts_dir,
            render_window_limits=render_window_limits,
            allow_fast_fit=backend_id == vieneu.ENGINE_ID,
        ).synthesize(segments, input_hash=input_hash, checkpoints=reusable,
                     on_checkpoint=on_checkpoint, on_segment=on_segment)
        if checkpoints.warnings:
            tts_document = replace(tts_document, warnings=tts_document.warnings + tuple(checkpoints.warnings))
        if spoken_store is not None and spoken_store.warnings:
            tts_document = replace(tts_document, warnings=tts_document.warnings + tuple(spoken_store.warnings))
    except TtsStageError as error:
        if not allow_all_failed:
            raise B2AudioError("TTS_FAILED", str(error), retryable=error.retryable) from error
        # A voice group may contain only one refused cue. Retain its failure
        # document so other groups can still contribute usable Vietnamese PCM.
        tts_document = replace(error.document, warnings=error.document.warnings + tuple(checkpoints.warnings)
                               + (() if spoken_store is None else tuple(spoken_store.warnings)))
    except Exception as error:
        code = getattr(error, "code", "TTS_BOOTSTRAP_FAILED")
        condition = getattr(error, "condition", str(error))
        raise B2AudioError(code, condition, retryable=bool(getattr(error, "retryable", False))) from error
    finally:
        close = getattr(engine, "close", None)
        if close is not None:
            close()

    return tts_document, spoken_store


def _write_voice_documents(tts_document: TtsDocument, spoken_store: SpokenTextStore | None,
                           tts_document_path: Path, spoken_text_document_path: Path, mappings) -> None:
    try:
        members = {item["cue_id"]: item["member_cue_ids"] for item in mappings}
        # A private B2 envelope keeps the frozen public TTS1/2 schemas intact.
        document = {"schema_version": 1, "kind": "dubbing_tts_document", "recipe": DUBBING_PHRASE_RECIPE,
                    "member_cue_ids": members, "tts_document": tts_document.to_dict()}
        payload = (json.dumps(document, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        _atomic_bytes(tts_document_path, payload)
        if spoken_store is not None:
            spoken = spoken_store.document(tts_document)
            spoken.update(member_cue_ids=members, tts_document_hash="sha256:" + sha256(payload).hexdigest())
            _atomic_bytes(spoken_text_document_path,
                          (json.dumps(spoken, ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
    except OSError as error:
        raise B2AudioError("TTS_ARTIFACT_WRITE_FAILED", str(error), retryable=True) from error
    except (ValueError, TypeError, KeyError) as error:
        raise B2AudioError("TTS_SPOKEN_TEXT_EVIDENCE_INVALID", str(error), retryable=False) from error


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
    synthesis_cache_dir: str | Path | None = None,
    voice_by_cue: Mapping[str, str] | None = None,
    speaker_by_cue: Mapping[str, str] | None = None,
    background_path: str | Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> B2AudioResult:
    """Execute TTS/AUD-0; optional VieNeu casting keeps per-voice provenance.

    A missing voice map retains the flat TTS directory layout. ``on_progress``
    receives (settled phrases, all phrases) across every voice group; the
    final (total, total) call comes before the mix starts.
    """

    if not translated_cues:
        raise B2AudioError("TTS_INPUT_EMPTY", "translated production cues are empty")
    silence_duration: MediaTimeline | None = None
    if not source_probe.has_audio:
        video = source_probe.video
        if video.time_base is not None and video.duration_ticks is not None and video.duration_ticks > 0:
            silence_duration = MediaTimeline(video.time_base, 0, video.duration_ticks, video.duration_ticks)
        elif source_probe.duration_ticks is not None and source_probe.duration_ticks > 0:
            silence_duration = MediaTimeline(CANONICAL_TIME_BASE, 0, source_probe.duration_ticks, source_probe.duration_ticks)
        else:
            raise B2AudioError("MEDIA_DURATION_INVALID", "no-audio dubbing requires a known positive source duration")
    root = Path(work_dir).absolute()
    root.mkdir(parents=True, exist_ok=True)
    try:
        selection = _read_json(Path(profile_path))
        recipe = _read_json(_child(Path(app_root).absolute(), selection.get("tts_neural_profile", "")))
        if recipe.get("backend") == vieneu.ENGINE_ID:
            pack, voice = vieneu.load_vieneu_voice(app_root, model_root or root / "model-cache", profile_path, voice_id=tts_voice_id)
            engine = vieneu.VieNeuVietnameseTtsEngine(pack, ffmpeg_path=media.ffmpeg_path)
            backend_id, producer_version, runtime_id = vieneu.ENGINE_ID, B2_TTS_PRODUCER_VERSION, vieneu.RUNTIME_ID
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
        if silence_duration is not None:
            media.create_silent_audio(source_path, source_audio_path, video_duration=silence_duration, overwrite=True)
        else:
            # Keep the source bed stereo; mono TTS is centered by the mixer.
            media.extract_audio(source_path, source_audio_path, sample_rate=pack.sample_rate, channels=2,
                                video_stream=source_probe.video, overwrite=True)
        source_end_ticks = _wav_duration_ticks(source_audio_path)
    except BaseException as error:
        close = getattr(engine, "close", None)
        if close is not None:
            close()
        if isinstance(error, MediaAdapterError):
            raise B2AudioError(error.code, error.condition, retryable=error.retryable) from error
        raise

    try:
        mappings = _clamped_mappings(translated_cues, _media_extent_ms(source_probe, source_end_ticks))
        _dubbing_windows(mappings, _media_extent_ms(source_probe, source_end_ticks))
        input_hash = _input_hash(mappings)
        multivoice = voice_by_cue is not None and backend_id == vieneu.ENGINE_ID
        mappings = _dubbing_phrases(mappings, default_voice=voice.voice_id,
            voice_by_cue=voice_by_cue if multivoice else None,
            speaker_by_cue=speaker_by_cue if multivoice else None)
        phrase_hash = _input_hash(mappings)
        placement_document_path = None
        windows_by_id: dict[str, dict[str, Any]] = {}
        render_window_limits = {}
        if backend_id == vieneu.ENGINE_ID:
            windows = _dubbing_windows(mappings, _media_extent_ms(source_probe, source_end_ticks))
            wider = _dubbing_windows(mappings, _media_extent_ms(source_probe, source_end_ticks), max_postroll_ms=MAX_FALLBACK_POSTROLL_MS)
            render_window_limits = {item["cue_id"]: TimePoint(int(item["render_window_end"]["ticks"]), BASE_TIME) for item in wider}
            gap_evidence = UNMEASURED_GAP_EVIDENCE
            if background_path is not None:
                try:
                    windows, measured = _measured_windows(windows, source_audio_path, Path(background_path),
                                                          _media_extent_ms(source_probe, source_end_ticks))
                except (OSError, EOFError, wave.Error, ValueError) as error:
                    raise B2AudioError("SEPARATION_BED_FAILED", f"accompaniment cannot be measured: {error}") from error
                if measured:
                    gap_evidence = MEASURED_GAP_EVIDENCE
                    render_window_limits.update({key: TimePoint(value, BASE_TIME) for key, value in measured.items()})
            members = {item["cue_id"]: item["member_cue_ids"] for item in mappings}
            for window in windows:
                window["member_cue_ids"] = members[window["cue_id"]]
            placement = {"schema_version": 1, "kind": "dubbing_placement", "recipe": DUBBING_WINDOW_RECIPE,
                "phrase_recipe": DUBBING_PHRASE_RECIPE, "phrase_input_hash": phrase_hash,
                "time_base": BASE_TIME.to_dict(), "source_end": TimePoint(_media_extent_ms(source_probe, source_end_ticks), BASE_TIME).to_dict(),
                "source_audio_sha256": "sha256:" + _digest(source_audio_path), "canonical_cue_input_hash": input_hash,
                "gap_evidence": gap_evidence, "windows": windows}
            placement_bytes = (json.dumps(placement, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
            input_hash = "sha256:" + sha256(placement_bytes).hexdigest()
            placement_document_path = root / "dubbing_placement.json"
            _atomic_bytes(placement_document_path, placement_bytes)
            windows_by_id = {item["cue_id"]: item for item in windows}
        else:
            input_hash = phrase_hash
    except BaseException as error:
        close = getattr(engine, "close", None)
        if close is not None:
            close()
        if isinstance(error, Exception):
            raise B2AudioError(getattr(error, "code", "TTS_BOOTSTRAP_FAILED"),
                               getattr(error, "condition", str(error)),
                               retryable=bool(getattr(error, "retryable", False))) from error
        raise

    groups = {voice.voice_id: mappings}
    if multivoice:
        groups = {}
        for item in mappings:
            identifier = item["voice_id"]
            groups.setdefault(identifier, []).append(item)
        # If every cue was explicitly reassigned, the first used group owns the
        # base document. Never fabricate an empty primary-voice TTS document.
        first = voice.voice_id if voice.voice_id in groups else next(iter(groups))
        groups = {key: groups[key] for key in (first, *sorted(set(groups) - {first}))}

    documents, document_paths, spoken_paths = [], [], []
    primary_voice = voice
    settled = 0

    def segment_settled() -> None:
        nonlocal settled
        settled += 1
        on_progress(settled, len(mappings))
    for index, (identifier, items) in enumerate(groups.items()):
        if index != 0 or identifier != voice.voice_id:
            if index == 0:
                close = getattr(engine, "close", None)
                if close is not None:
                    close()
            try:
                pack, voice = vieneu.load_vieneu_voice(app_root, model_root or root / "model-cache", profile_path, voice_id=identifier)
                engine = vieneu.VieNeuVietnameseTtsEngine(pack, ffmpeg_path=media.ffmpeg_path)
            except Exception as error:
                raise B2AudioError(getattr(error, "code", "TTS_BOOTSTRAP_FAILED"), str(error)) from error
        if index == 0:
            primary_voice = voice
        group_hash = input_hash
        if multivoice:
            group_hash = _input_hash(({"placement_hash": input_hash, "voice_id": identifier,
                                      "cue_ids": [item["cue_id"] for item in items]},))
        tts_document, spoken_store = _synthesize_voice(
            pack=pack, voice=voice, engine=engine, resources=resources, backend_id=backend_id,
            producer_version=producer_version, runtime_id=runtime_id, mappings=items,
            windows_by_id=windows_by_id, input_hash=group_hash, source_language=source_language,
            tts_dir=root / "tts" / identifier if multivoice else root / "tts",
            app_root=app_root, profile_path=profile_path, synthesis_cache_dir=synthesis_cache_dir,
            render_window_limits={item["cue_id"]: render_window_limits[item["cue_id"]] for item in items} if render_window_limits else None,
            allow_all_failed=True, on_segment=segment_settled if on_progress is not None else None)
        document_path = root / ("tts_document.json" if index == 0 else f"tts_document.{identifier}.json")
        spoken_path = root / ("dubbing_spoken_text.json" if index == 0 else f"dubbing_spoken_text.{identifier}.json")
        _write_voice_documents(tts_document, spoken_store, document_path, spoken_path, items)
        documents.append(tts_document)
        document_paths.append(document_path)
        if spoken_store is not None:
            spoken_paths.append(spoken_path)
    if on_progress is not None and settled != len(mappings):
        on_progress(len(mappings), len(mappings))
    tts_document, tts_document_path = documents[0], document_paths[0]
    spoken_text_document_path = spoken_paths[0] if spoken_paths else None
    if not any(document.artifacts for document in documents):
        # Keep review evidence even when B1/source is the usable export.
        raise B2AudioError("TTS_FAILED", "no translated cue produced a usable dialogue waveform",
            review={"tts_document": str(tts_document_path), "tts_documents": [str(path) for path in document_paths],
                    "tts_failures": sum(len(item["member_cue_ids"]) for item in mappings)})
    try:
        artifacts_by_id = {artifact.segment_id: artifact for document in documents for artifact in document.artifacts}
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
        background_stem_path = None
        restored = {item["cue_id"] for item in mappings if item["cue_id"] not in artifacts_by_id}
        bed_path = source_audio_path
        if background_path is not None:
            # A generation retains its own immutable bed even when the optional
            # analysis stage later regenerates work_dir/separation/background.wav.
            background_stem_path = root / "background_stem.wav"
            shutil.copyfile(background_path, background_stem_path)
            bed_path = background_stem_path
            if restored:
                bed_path = root / "mix_bed.wav"
                _restore_source_windows(background_stem_path, source_audio_path, bed_path,
                    [(item["start_ms"], item["end_ms"]) for item in mappings if item["cue_id"] in restored])
        source = FileSource(
            bed_path,
            start=TimePoint(0, BASE_TIME),
            end=TimePoint(source_end_ticks, BASE_TIME),
            source_id=Path(source_path).name,
            layout="stereo-accompaniment" if background_stem_path is not None else "generated-silence-stereo" if silence_duration is not None else "stereo-source",
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
            input_hash=_mix_input_hash(documents),
        )
        if background_stem_path is not None:
            rejected = {failure.segment_id for failure in mix_document.failures} - restored
            if rejected:
                # A validated TTS file can still be unavailable at mix time.
                # Change the bed once for these newly observed failures.
                restored.update(rejected)
                bed_path = root / "mix_bed.wav"
                _restore_source_windows(background_stem_path, source_audio_path, bed_path,
                    [(item["start_ms"], item["end_ms"]) for item in mappings if item["cue_id"] in restored])
                mix_document = mixer.mix(replace(source, path=bed_path), tuple(segments_for_mix),
                    input_hash=_mix_input_hash(documents))
                if any(failure.segment_id not in restored for failure in mix_document.failures):
                    raise B2AudioError("SEPARATION_BED_FAILED", "TTS inputs changed during source restoration; use the original bed")
            mix_document = replace(mix_document, warnings=mix_document.warnings + tuple(
                "SEPARATION_SOURCE_RESTORED: " + identifier for item in mappings if item["cue_id"] in restored
                for identifier in item["member_cue_ids"]))
            # The mixer keeps a verified snapshot of the restored bed, and a
            # resumed generation rebuilds it deterministically.
            try:
                (root / "mix_bed.wav").unlink(missing_ok=True)
            except OSError:
                pass
    except Exception as error:
        if background_path is not None:
            raise B2AudioError("SEPARATION_BED_FAILED", getattr(error, "condition", str(error))) from error
        code = getattr(error, "code", "AUDIO_MIX_FAILED")
        condition = getattr(error, "condition", str(error))
        raise B2AudioError(code, condition, retryable=bool(getattr(error, "retryable", False))) from error
    if len(mix_document.failures) >= len(segments_for_mix):
        raise B2AudioError("AUDIO_MIX_DEGRADED", "every translated cue failed the AUD-0 mix")
    mix_document_path = root / "mix_document.json"
    try:
        _atomic_bytes(mix_document_path, (mix_document.to_json() + "\n").encode("utf-8"))
    except OSError as error:
        raise B2AudioError("MIX_ARTIFACT_WRITE_FAILED", str(error), retryable=True) from error
    return B2AudioResult(source_audio_path, tts_document_path, mix_document_path, tts_document, mix_document, primary_voice, placement_document_path, spoken_text_document_path,
                         tuple(groups), tuple(documents), tuple(document_paths), tuple(spoken_paths), background_stem_path,
                         {item["cue_id"]: tuple(item["member_cue_ids"]) for item in mappings},
                         placement["gap_evidence"] if placement_document_path is not None else None)


__all__ = ["B2AudioError", "B2AudioResult", "BASE_TIME", "run_b2_audio", "mix_recipe_identity"]
