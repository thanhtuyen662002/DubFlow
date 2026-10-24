"""Opt-in VieNeu voice groups using the existing validated TTS/mix boundaries.

One preset-bound engine is alive at a time. Global placement windows are
computed before grouping, so a different speaker still bounds the next cue.
The legacy B2 producer stays byte-identical, including its recipe fingerprint.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from engine.dubflow.asr import TimePoint
from engine.dubflow.mix import FileSegment, FileSource, StreamingAudioMixer, MixConfig, ResourceProfile as MixResourceProfile
from engine.dubflow.tts import LocalTtsAdapter, TtsConfig, TtsDocument, TtsInput, TtsProvenance, TtsStageError
from engine.dubflow.tts import ResourceProfile as TtsResourceProfile, vieneu
from engine.dubflow.tts.adapter import validate_tts_document
from .b2_audio import (BASE_TIME, B2_TTS_PRODUCER_VERSION, DUBBING_WINDOW_RECIPE,
    B2AudioError, B2AudioResult, _atomic_bytes, _cue_mapping, _digest, _dubbing_windows,
    _input_hash, _media_extent_ms, _source_language, _wav_duration_ticks, tts_recipe_identity)
from .speaker_casting import plan_identity
from .tts_cache import CachedSynthesisEngine
from .tts_checkpoints import TtsCheckpointStore
from .tts_rewrites import BoundedDurationRewriteEngine, SpokenTextStore


def _bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def run_speaker_audio(*, media, source_path, source_probe, translated_cues, source_language,
                      app_root, profile_path, work_dir, speaker_plan, model_root=None,
                      tts_voice_id=None, synthesis_cache_dir=None) -> B2AudioResult:
    """Synthesize voice groups sequentially, retaining per-cue checkpoints."""
    try:
        return _run(media=media, source_path=source_path, source_probe=source_probe,
            translated_cues=translated_cues, source_language=source_language, app_root=app_root,
            profile_path=profile_path, work_dir=work_dir, plan=speaker_plan,
            model_root=model_root, synthesis_cache_dir=synthesis_cache_dir)
    except B2AudioError:
        raise
    except Exception as error:
        raise B2AudioError(getattr(error, "code", "TTS_BOOTSTRAP_FAILED"),
            getattr(error, "condition", str(error)), retryable=bool(getattr(error, "retryable", False))) from error


def _run(*, media, source_path, source_probe, translated_cues, source_language,
         app_root, profile_path, work_dir, plan, model_root, synthesis_cache_dir):
    if not translated_cues:
        raise B2AudioError("TTS_INPUT_EMPTY", "translated production cues are empty")
    root = Path(work_dir).absolute()
    root.mkdir(parents=True, exist_ok=True)
    primary_id = plan["primary_voice_id"]
    if plan["fallback_reason"] is not None:
        # Reuse the primary voice path for silent/single/failed diarization,
        # including its existing no-audio duration/silence policy.
        from .b2_audio import run_b2_audio
        return run_b2_audio(media=media, source_path=source_path, source_probe=source_probe,
            translated_cues=translated_cues, source_language=source_language, app_root=app_root,
            profile_path=profile_path, work_dir=root, model_root=model_root,
            tts_voice_id=primary_id, synthesis_cache_dir=synthesis_cache_dir)
    pack, primary = vieneu.load_vieneu_voice(app_root, model_root or root / "model-cache", profile_path, voice_id=primary_id)
    source_audio = root / "source_audio.wav"
    media.extract_audio(source_path, source_audio, sample_rate=pack.sample_rate, channels=2,
        video_stream=source_probe.video, overwrite=True)
    source_end = _wav_duration_ticks(source_audio)
    mappings = tuple(_cue_mapping(cue) for cue in translated_cues)
    windows = _dubbing_windows(mappings, _media_extent_ms(source_probe, source_end))
    placement = {"schema_version": 1, "kind": "dubbing_placement", "recipe": DUBBING_WINDOW_RECIPE,
        "time_base": BASE_TIME.to_dict(), "source_end": TimePoint(_media_extent_ms(source_probe, source_end), BASE_TIME).to_dict(),
        "source_audio_sha256": "sha256:" + _digest(source_audio), "canonical_cue_input_hash": _input_hash(mappings),
        "gap_evidence": "recognized-source-cue-intervals;silence-not-certified", "windows": windows}
    placement_path = root / "dubbing_placement.json"
    _atomic_bytes(placement_path, _bytes(placement))
    identity = plan_identity(plan)
    input_hash = "sha256:" + sha256(_bytes({"placement": placement, "speaker_plan": identity})).hexdigest()
    window_by_id = {item["cue_id"]: item for item in windows}
    segments = tuple(TtsInput(item["cue_id"], item["cue_id"], item["translated_text"],
        TimePoint(item["start_ms"], BASE_TIME), TimePoint(item["end_ms"], BASE_TIME),
        source_language=_source_language(source_language), confidence=item["confidence"],
        render_window_end=TimePoint(int(window_by_id[item["cue_id"]]["render_window_end"]["ticks"]), BASE_TIME)) for item in mappings)
    config = TtsConfig(sample_rate=pack.sample_rate, channels=pack.channels, requested_profile="cpu",
        max_attempts=1, max_text_chars=512, max_duration_error_ticks=80,
        min_speed_ratio_milli=800, max_speed_ratio_milli=1300,
        resource=TtsResourceProfile(max_threads=2, max_memory_mb=2048, max_batch_items=1))
    recipe = tts_recipe_identity(Path(app_root), Path(profile_path))
    checkpoint_identity = sha256(_bytes({"input": input_hash, "recipe": recipe, "plan": identity})).hexdigest()
    spoken = SpokenTextStore(root / "tts", identity=checkpoint_identity, segments=segments)
    documents, profiles = [], []
    # The primary profile remains available for fallback/metadata even if no cue uses it.
    used_voices = sorted(set(plan["cue_to_voice"].values()))
    for voice_id in used_voices:
        voice_pack, voice = (pack, primary) if voice_id == primary_id else vieneu.load_vieneu_voice(
            app_root, model_root or root / "model-cache", profile_path, voice_id=voice_id)
        if (voice_pack.sample_rate, voice_pack.channels) != (pack.sample_rate, pack.channels):
            raise B2AudioError("VOICE_FORMAT_MISMATCH", "speaker voices require the same PCM format")
        provenance = TtsProvenance("dubflow-production-tts", B2_TTS_PRODUCER_VERSION, vieneu.ENGINE_ID,
            vieneu.RUNTIME_ID, "timeline-v1", config.content_hash(), input_hash, voice.model_id,
            voice.model_version, voice.model_hash, voice.content_hash(), voice.voice_id, voice.voice_version,
            "cpu", "cpu", config.resource)
        group = tuple(item for item in segments if plan["cue_to_voice"][item.segment_id] == voice_id)
        directory = root / "tts" / voice_id
        checkpoints = TtsCheckpointStore(directory, identity=checkpoint_identity)
        reusable = spoken.filter_checkpoints(checkpoints.load(group))
        engine = vieneu.VieNeuVietnameseTtsEngine(voice_pack, ffmpeg_path=media.ffmpeg_path)
        try:
            if synthesis_cache_dir is not None:
                cache_identity = sha256(_bytes({"recipe": recipe, "speaker_recipe": plan["producer_identity"],
                    "voices": plan["requested_voices"], "provenance": {key: value for key, value in provenance.to_dict().items() if key != "input_hash"}})).hexdigest()
                engine = CachedSynthesisEngine(engine, Path(synthesis_cache_dir), identity=cache_identity)
            engine = BoundedDurationRewriteEngine(engine, spoken)

            def commit(checkpoint):
                spoken.commit(checkpoint)
                checkpoints.commit(checkpoint)

            try:
                document = LocalTtsAdapter(engine, config=config, provenance=provenance, voice=voice,
                    output_dir=directory).synthesize(group, input_hash=input_hash, checkpoints=reusable, on_checkpoint=commit)
            except TtsStageError as error:
                # A refused speaker group must not discard another group's speech.
                document = error.document
            document = replace(document, warnings=document.warnings + tuple(checkpoints.warnings))
            document_path = directory / "tts_document.json"
            _atomic_bytes(document_path, document.to_bytes())
            profiles.append({"voice": voice.to_dict(), "provenance": provenance.to_dict(),
                "segment_ids": [item.segment_id for item in group], "tts_document": str(document_path),
                "sha256": "sha256:" + _digest(document_path)})
            documents.append(document)
        finally:
            close = getattr(engine, "close", None)
            if close is not None:
                close()
    roster_hash = "sha256:" + sha256(_bytes([
        {"voice": item["voice"], "segment_ids": item["segment_ids"]} for item in profiles])).hexdigest()
    provenance = replace(documents[0].provenance, voice_id="composite", voice_version="1",
        voice_hash=roster_hash)
    document = TtsDocument("vi", tuple(item.segment_id for item in segments),
        tuple(item for group in documents for item in group.artifacts),
        tuple(dict(item, index=index) for index, item in enumerate(item for group in documents for item in group.chunks)), provenance,
        tuple(item for group in documents for item in group.failures),
        tuple(dict.fromkeys([warning for group in documents for warning in group.warnings] + spoken.warnings)), 2)
    validate_tts_document(document.to_dict())
    if not document.artifacts:
        raise B2AudioError("TTS_FAILED", "no translated cue produced a usable dialogue waveform")
    document_path = root / "tts_document.json"
    _atomic_bytes(document_path, document.to_bytes())
    spoken_path = root / "dubbing_spoken_text.json"
    _atomic_bytes(spoken_path, _bytes(spoken.document(document)))
    plan["voice_documents"] = profiles
    artifacts = {item.segment_id: item for item in document.artifacts}
    mix_segments = tuple(FileSegment.from_tts_artifact(artifacts[item.segment_id]) if item.segment_id in artifacts else
        FileSegment(item.segment_id, item.source_utterance_id, item.start, item.end,
            status="failed", condition="TTS segment did not produce a usable WAV artifact", confidence=item.confidence) for item in segments)
    source = FileSource(source_audio, start=TimePoint(0, BASE_TIME), end=TimePoint(source_end, BASE_TIME),
        source_id=Path(source_path).name, layout="stereo-source")
    mixer = StreamingAudioMixer(config=MixConfig(requested_profile="cpu",
        resource=MixResourceProfile(max_threads=1, max_memory_mb=512, max_batch_items=8)), output_dir=root / "mix")
    mix = mixer.mix(source, mix_segments, input_hash="sha256:" + sha256(document.to_bytes()).hexdigest())
    if len(mix.failures) >= len(mix_segments):
        raise B2AudioError("AUDIO_MIX_DEGRADED", "every translated cue failed the AUD-0 mix")
    mix_path = root / "mix_document.json"
    _atomic_bytes(mix_path, (mix.to_json() + "\n").encode("utf-8"))
    return B2AudioResult(source_audio, document_path, mix_path, document, mix, primary, placement_path, spoken_path)
