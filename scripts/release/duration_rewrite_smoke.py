"""Qualify real bounded rewrite/reuse in a verified, owned Windows bundle.

Two authored measured windows exercise actual speech. They are not real-video
timing, human fidelity/intelligibility, or general film voice qualification.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time


def _outside(path: Path, root: Path) -> None:
    # samefile also handles Windows extended-prefix versus ordinary paths.
    if any(parent.exists() and os.path.samefile(parent, root) for parent in (path, *path.parents)):
        raise ValueError("rewrite qualification outputs must stay outside immutable runtime/model roots")


def run(args):
    root, model_root, work = args.root.resolve(), args.model_root.resolve(), args.work_root.resolve()
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or not os.path.samefile(sys.executable, root / "runtime/python.exe")):
        raise ValueError("rewrite qualification requires the selected owned Python with -I -S -B")
    for output in (work, args.report.resolve()):
        _outside(output, root)
        _outside(output, model_root)
    sys.path[:0] = [str(root / "app"), str(root / "runtime/Lib/site-packages")]
    from packaging.release.bootstrap import verify_bundle
    from packaging.release.manifest import ReleaseManifest
    with (root / "release-manifest.json").open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("release manifest exceeds bound")
    manifest = ReleaseManifest.from_mapping(json.loads(raw))
    if manifest.source_sha != args.expected_source_sha:
        raise ValueError("rewrite qualification bundle source SHA differs")
    verify_bundle(root, manifest)
    work.mkdir(parents=True, exist_ok=False)
    (work / "scratch").mkdir()
    os.environ["TEMP"] = os.environ["TMP"] = str(work / "scratch")
    from engine.dubflow.asr import TimeBase, TimePoint
    from engine.dubflow.tts import LocalTtsAdapter, ResourceProfile, TtsConfig, TtsInput, TtsProvenance
    from engine.dubflow.tts.vieneu import load_vieneu_voice, VieNeuVietnameseTtsEngine, ENGINE_ID, RUNTIME_ID
    from engine.dubflow.worker.b2_audio import B2_TTS_PRODUCER_VERSION, tts_recipe_identity
    from engine.dubflow.worker.tts_checkpoints import TtsCheckpointStore
    from engine.dubflow.worker.tts_rewrites import (BoundedDurationRewriteEngine, SpokenTextStore,
                                                   plan_rewrite, rewrite_warning, validate_spoken_text_document)
    if B2_TTS_PRODUCER_VERSION != "3.5.0":
        raise ValueError("rewrite qualification requires the current3.5 worker pipeline")
    profile = root / "app/models/manifests/production-cpu-v1.json"
    ffmpeg = root / "runtime/media/ffmpeg.exe"
    config = TtsConfig(sample_rate=48000, channels=1, requested_profile="cpu", max_attempts=1,
        max_text_chars=512, max_duration_error_ticks=80, min_speed_ratio_milli=800,
        max_speed_ratio_milli=1300, resource=ResourceProfile(max_threads=2, max_memory_mb=2048, max_batch_items=1))
    text = "Khi lần đầu tiên chúng tôi gặp nhau,"
    plan = plan_rewrite(text)
    if plan is None:
        raise ValueError("expected lexical rewrite is unavailable")
    rows = []
    started = time.monotonic()
    for voice_id in ("vi-truc-ly-vieneu3-v1", "vi-thai-son-vieneu3-v1"):
        pack, voice = load_vieneu_voice(root / "app", model_root, profile, voice_id=voice_id)
        engine = VieNeuVietnameseTtsEngine(pack, ffmpeg_path=ffmpeg)
        reopened = None
        try:
            health = engine.healthcheck(voice)
            if not health.ready:
                raise ValueError("approved voice healthcheck failed: " + health.code)
            original = engine._tts.generate(text, sid=0, speed=1.0)
            candidate = engine._tts.generate(plan.spoken_text, sid=0, speed=1.0)
            lower = math.ceil(len(candidate.samples) / 1.20 / 48)
            upper = math.floor((len(original.samples) - 1) / 1.30 / 48)
            if not 0 < lower < upper:
                raise ValueError("measured speech does not supply a bounded changed-text control window")
            duration = (lower + upper) // 2
            base = TimeBase(1, 1000)
            segment = TtsInput("duration-rewrite-control", "duration-rewrite-control", text,
                TimePoint(0, base), TimePoint(duration, base), confidence=0.0, render_window_end=TimePoint(duration, base))
            input_hash = "sha256:" + hashlib.sha256(json.dumps(segment.to_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            provenance = TtsProvenance("dubflow-production-tts", B2_TTS_PRODUCER_VERSION, ENGINE_ID, RUNTIME_ID,
                "timeline-v1", config.content_hash(), input_hash, voice.model_id, voice.model_version, voice.model_hash,
                voice.content_hash(), voice.voice_id, voice.voice_version, "cpu", "cpu", config.resource)
            identity = hashlib.sha256((json.dumps(provenance.to_dict(), sort_keys=True) + tts_recipe_identity(root / "app", profile)).encode()).hexdigest()
            audio = work / voice_id / "tts"
            checkpoints = TtsCheckpointStore(audio, identity=identity)
            spoken = SpokenTextStore(audio, identity=identity, segments=(segment,))
            wrapped = BoundedDurationRewriteEngine(engine, spoken)
            def commit(value):
                spoken.commit(value)
                checkpoints.commit(value)
            document = LocalTtsAdapter(wrapped, config=config, provenance=provenance, voice=voice, output_dir=audio).synthesize(
                (segment,), input_hash=input_hash, on_checkpoint=commit)
            if (document.failures or len(document.artifacts) != 1 or len(wrapped.rewritten_requests) != 1
                    or rewrite_warning(segment.segment_id) not in document.artifacts[0].warnings):
                raise ValueError("real speech did not execute and validate the bounded rewrite")
            evidence = spoken.document(document)
            validate_spoken_text_document(evidence, document.to_bytes(), (segment,), identity=identity)
            (audio.parent / "tts_document.json").write_bytes(document.to_bytes())
            (audio.parent / "spoken_text_document.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact = document.artifacts[0]
            path = Path(artifact.path)
            before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
            engine.close()
            reopened = VieNeuVietnameseTtsEngine(pack, ffmpeg_path=ffmpeg)
            resumed_spoken = SpokenTextStore(audio, identity=identity, segments=(segment,))
            replay = LocalTtsAdapter(BoundedDurationRewriteEngine(reopened, resumed_spoken), config=config,
                provenance=provenance, voice=voice, output_dir=audio).synthesize((segment,), input_hash=input_hash,
                checkpoints=resumed_spoken.filter_checkpoints(checkpoints.load((segment,))))
            resumed_spoken.document(replay)
            if (replay.artifacts != document.artifacts or reopened._tts is not None
                    or before != (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)):
                raise ValueError("fresh-engine rewrite replay regenerated or changed the waveform")
            rows.append({"voice": voice.to_dict(), "natural_original_frames": len(original.samples),
                         "natural_candidate_frames": len(candidate.samples), "control": segment.to_dict(),
                         "artifact": artifact.to_dict(), "fresh_engine_reuse_without_inference": True,
                         "spoken_text_document_sha256": hashlib.sha256((audio.parent / "spoken_text_document.json").read_bytes()).hexdigest()})
        finally:
            engine.close()
            if reopened is not None:
                reopened.close()
    return {"schema_version": 1, "source_sha": manifest.source_sha,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "verified_release_tree": True,
            "producer_version": B2_TTS_PRODUCER_VERSION, "cases": rows,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "scope": "two actual approved CPU voices; authored measured windows; bounded rewrite and fresh-engine reuse",
            "real_video_timing": "NOT_RUN", "human_fidelity_intelligibility": "NOT_ASSESSED",
            "production_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report), "source_sha": report["source_sha"], "cases": len(report["cases"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
