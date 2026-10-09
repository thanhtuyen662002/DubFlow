"""Exercise the packaged CPU local-file production path on Windows.

This smoke is deliberately independent of the desktop UI.  It drives the same
supervisor executable that the Tauri host launches, uses the bundled FFmpeg
runtime to create real media, and verifies the playable output plus every
editable/provenance artifact.  A corrupt job is run before a valid job to prove
that one failed item does not poison the batch.  The optional hard-kill pass
starts a job while the first-run model profile is being downloaded, kills the
supervisor tree, and reissues the same job ID to exercise checkpoint recovery.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import wave
from typing import Any, Sequence


class SmokeError(RuntimeError):
    pass


def _run(command: Sequence[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [os.fspath(item) for item in command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SmokeError(f"command failed to start or timed out: {command[0]}: {error}") from error
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "").split())[:4096]
        raise SmokeError(f"command exited {result.returncode}: {command[0]}: {detail}")
    return result


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeError(f"invalid JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise SmokeError(f"expected JSON object at {path}")
    return value


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def _write_sidecar(source: Path, *, seconds: int) -> Path:
    sidecar = source.with_suffix(".srt")
    end = max(1, min(seconds, 59))
    sidecar.write_text(
        "1\n00:00:00,000 --> 00:00:{:02d},500\nHello from DubFlow\n\n".format(end),
        encoding="utf-8",
    )
    return sidecar


def _make_source(ffmpeg: Path, root: Path, *, seconds: int, stem: str, size: str = "320x180") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{stem}.mp4"
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x18324a:s={size}:r=25:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "h264_mf",
            "-quality",
            "90",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            source,
        ],
        timeout=300,
    )
    if not source.is_file() or source.stat().st_size <= 0:
        raise SmokeError(f"FFmpeg did not create a source media file: {source}")
    _write_sidecar(source, seconds=seconds)
    return source


def _supervisor_command(
    supervisor: Path,
    root: Path,
    data_root: Path,
    source: Path,
    output_dir: Path,
    job_id: str,
    *,
    enable_dubbing: bool = False,
    tts_voice_id: str | None = None,
    source_language: str | None = None,
) -> list[str]:
    status_path = data_root / "control" / "jobs" / f"{job_id}.json"
    command = [
        supervisor,
        "run",
        "--root",
        root,
        "--data-root",
        data_root,
        "--model-root",
        data_root / "models",
        "--job-id",
        job_id,
        "--source",
        source,
        "--output-dir",
        output_dir,
        "--status-path",
        status_path,
    ]
    if enable_dubbing:
        command.append("--enable-dubbing")
    if tts_voice_id is not None:
        command.extend(("--tts-voice-id", tts_voice_id))
    if source_language is not None:
        command.extend(("--source-language", source_language))
    return [os.fspath(item) for item in command]


def _run_supervisor(
    supervisor: Path,
    root: Path,
    data_root: Path,
    source: Path,
    output_dir: Path,
    job_id: str,
    *,
    timeout: float,
    kill_after: float | None = None,
    enable_dubbing: bool = False,
    tts_voice_id: str | None = None,
    source_language: str | None = None,
) -> tuple[dict[str, Any] | None, bool, str]:
    status_path = data_root / "control" / "jobs" / f"{job_id}.json"
    command = _supervisor_command(supervisor, root, data_root, source, output_dir, job_id,
                                  enable_dubbing=enable_dubbing, tts_voice_id=tts_voice_id,
                                  source_language=source_language)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [os.fspath(item) for item in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    killed = False
    if kill_after is not None:
        time.sleep(max(0.0, kill_after))
        if process.poll() is None:
            _terminate_tree(process)
            killed = True
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _terminate_tree(process)
        raise SmokeError(f"supervisor timed out for {job_id}") from error
    if killed:
        return None, True, stdout[-4096:] + stderr[-4096:]
    if process.returncode != 0:
        detail = " ".join((stderr or stdout or "").split())[:4096]
        raise SmokeError(f"supervisor exited {process.returncode} for {job_id}: {detail}")
    if not status_path.is_file():
        raise SmokeError(f"supervisor did not write status for {job_id}: {status_path}")
    return _json(status_path), False, stdout[-8192:] + stderr[-4096:]


def _require_status(status: dict[str, Any], expected: str, job_id: str) -> dict[str, Any]:
    if status.get("job_id") != job_id:
        raise SmokeError(f"status job identity mismatch for {job_id}")
    value = status.get("status")
    if not isinstance(value, dict) or value.get("state") != expected:
        raise SmokeError(f"job {job_id} expected {expected}, got {value!r}")
    return value


def _verify_corrupt_failure(log: str, job_id: str) -> dict[str, Any]:
    events = []
    for line in log.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("job_id") == job_id:
            events.append(event)
    failures = [event for event in events if event.get("event") == "failed"]
    if len(failures) != 1 or any(event.get("event") == "retrying" for event in events):
        raise SmokeError("corrupt media did not fail once without repeating unchanged input")
    failure = failures[0]
    if failure.get("code") != "MEDIA_PROBE_FAILED" or failure.get("retryable") is not False or failure.get("attempt") != 1:
        raise SmokeError(f"corrupt media lost its typed single-attempt failure: {failure!r}")
    return {key: failure[key] for key in ("code", "attempt", "retryable")}


def _verify_output(ffprobe: Path, output_dir: Path, source_duration_seconds: int, *, expect_dubbing: bool = False, expect_voice_id: str | None = None, expect_audio: bool = True) -> dict[str, Any]:
    final = output_dir / "final_vi.mp4"
    required = [
        final,
        output_dir / "captions_vi.srt",
        output_dir / "captions_vi.ass",
        output_dir / "qc_report.json",
        output_dir / "job_manifest.json",
        output_dir / "editable" / "timeline.json",
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size <= 0:
            raise SmokeError(f"required production artifact is missing or empty: {path}")
    manifest = _json(output_dir / "job_manifest.json")
    streaming_mix = None
    if expect_dubbing:
        editable = output_dir / "editable"
        manifest = _json(output_dir / "job_manifest.json")
        for name in ("source_audio.wav", "dialogue_stem.wav", "final_mix.wav"):
            path = editable / name
            if not path.is_file() or path.stat().st_size <= 0:
                audio = manifest.get("audio")
                warnings = manifest.get("warnings")
                raise SmokeError(f"B2 editable audio artifact is missing or empty: {path}; audio={audio!r}; warnings={warnings!r}")
        audio = manifest.get("audio")
        if not isinstance(audio, dict) or audio.get("mode") != "dubbed" or audio.get("backend") != "vieneu-v3-turbo-onnx-v1":
            raise SmokeError(f"B2 manifest does not prove the app-owned voice path: {audio!r}")
        tts_document = _json(Path(audio["tts_document"]))
        provenance = tts_document.get("provenance", {})
        if provenance.get("backend_id") != "vieneu-v3-turbo-onnx-v1" or provenance.get("producer_version") != "3.1.0":
            raise SmokeError(f"B2 TTS receipt differs from the selected native producer: {provenance!r}")
        if expect_voice_id is not None and provenance.get("voice_id") != expect_voice_id:
            raise SmokeError("packaged TTS did not preserve the explicitly selected preset")
        tts_artifacts = tts_document.get("artifacts")
        if not isinstance(tts_artifacts, list) or not tts_artifacts:
            raise SmokeError("packaged TTS has no committed per-cue artifacts")
        tts_root = Path(audio["tts_document"]).parent / "tts"
        for artifact in tts_artifacts:
            path = Path(artifact["path"])
            if path.parent != tts_root or not re.fullmatch(r"tts-[a-f0-9]{32}\.wav", path.name):
                raise SmokeError("packaged TTS checkpoint audio escapes its private generation")
            record = tts_root / "checkpoints" / (hashlib.sha256(artifact["segment_id"].encode()).hexdigest() + ".json")
            try:
                with record.open("rb") as stream:
                    payload = stream.read(65537)
                if len(payload) > 65536:
                    raise ValueError("record exceeds bounds")
                checkpoint = json.loads(payload)
                metadata_hash = hashlib.sha256(json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if (type(checkpoint.get("schema_version")) is not int or checkpoint["schema_version"] != 1 or
                        not re.fullmatch(r"[a-f0-9]{64}", checkpoint.get("identity", "")) or
                        checkpoint.get("artifact") != artifact or checkpoint.get("artifact_record_hash") != metadata_hash):
                    raise ValueError("record differs from TTS artifact")
                with path.open("rb") as stream:
                    if "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() != artifact["content_hash"]:
                        raise ValueError("checkpoint waveform hash differs")
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                raise SmokeError("packaged TTS per-cue checkpoint verification failed") from error
        mix_document = _json(Path(audio["mix_document"]))
        mix_provenance = mix_document.get("provenance", {})
        if (mix_provenance.get("backend_id") != "pcm-stream-duck-v1" or
                mix_provenance.get("producer_version") != "2.0.1" or
                mix_provenance.get("runtime") != "owned-python/numpy-2.2.6" or
                mix_provenance.get("non_destructive") is not True or
                mix_provenance != audio.get("mix_provenance")):
            raise SmokeError("packaged B2 did not use the pinned streaming mixer")
        for key in ("original_audio", "dialogue_stem", "final_mix"):
            artifact = mix_document[key]
            path = Path(artifact["path"])
            with path.open("rb") as stream:
                actual_hash = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_hash != artifact["content_hash"] or actual_hash != artifact["metrics"]["content_hash"]:
                raise SmokeError(f"packaged B2 mix artifact hash differs: {key}")
            editable_name = {"original_audio": "source_audio.wav", "dialogue_stem": "dialogue_stem.wav",
                             "final_mix": "final_mix.wav"}[key]
            with (editable / editable_name).open("rb") as stream:
                if "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() != actual_hash:
                    raise SmokeError(f"packaged B2 editable audio hash differs: {key}")
            with wave.open(str(path), "rb") as reader:
                if (reader.getnframes() != artifact["frame_count"] or reader.getnchannels() != artifact["channels"] or
                        reader.getframerate() != artifact["sample_rate"] or reader.getsampwidth() != 2):
                    raise SmokeError(f"packaged B2 mix artifact PCM differs: {key}")
        if (mix_document["original_audio"]["content_hash"] != mix_provenance["source_hash"] or
                mix_document["final_mix"]["metrics"]["clipped_samples"] != 0):
            raise SmokeError("packaged B2 did not preserve source or safe final normalization")
        streaming_mix = {"backend": mix_provenance["backend_id"], "producer_version": mix_provenance["producer_version"],
                         "runtime": mix_provenance["runtime"], "frames": mix_document["final_mix"]["frame_count"],
                         "source_preserved": True, "artifact_hashes_verified": True,
                         "editable_hashes_verified": True, "tts_checkpoint_records_verified": len(tts_artifacts)}
    # The JSON is captured directly to avoid relying on a shell redirection.
    result = _run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", final],
        timeout=120,
    )
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeError(f"ffprobe returned invalid JSON: {error}") from error
    streams = probe.get("streams", []) if isinstance(probe, dict) else []
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos or videos[0].get("codec_name") != "h264":
        raise SmokeError(f"output video is not H.264: {videos}")
    if expect_audio and (not audios or audios[0].get("codec_name") != "aac"):
        raise SmokeError(f"output audio is not AAC: {audios}")
    if not expect_audio and audios:
        raise SmokeError(f"no-audio source unexpectedly gained an audio stream: {audios}")
    duration = float((probe.get("format") or {}).get("duration", "0"))
    if duration <= 0 or duration + 2 < source_duration_seconds:
        raise SmokeError(f"output duration is invalid: {duration}")
    return {
        "output": str(final),
        "duration_seconds": duration,
        "video_codec": videos[0].get("codec_name"),
        "audio_codec": audios[0].get("codec_name") if audios else None,
        "width": videos[0].get("width"),
        "height": videos[0].get("height"),
        "artifacts": [str(path.relative_to(output_dir)) for path in required],
        "selected_voice_id": expect_voice_id,
        "streaming_mix": streaming_mix,
    }


def _verify_replay_isolation(supervisor: Path, root: Path, data_root: Path, source: Path,
                             output: Path, *, voice_id: str | None) -> dict[str, Any]:
    status_path = data_root / "control/jobs/smoke-good.json"
    original_status = status_path.read_bytes()
    final = output / "final_vi.mp4"
    with final.open("rb") as stream:
        original_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    cases = [("output", source, output.with_name("foreign output"), voice_id),
             ("source", source.with_name("foreign source.mp4"), output, voice_id)]
    cases[1][1].write_bytes(b"different media")
    if voice_id is not None:
        cases.append(("voice", source, output, "vi-thai-son-vieneu3-v1"))
    outcomes = []
    for kind, candidate_source, candidate_output, candidate_voice in cases:
        command = [str(supervisor), "run", "--root", str(root), "--data-root", str(data_root),
            "--model-root", str(data_root / "models"), "--job-id", "smoke-good", "--source", str(candidate_source),
            "--output-dir", str(candidate_output), "--status-path", str(status_path)]
        if voice_id is not None:
            command.append("--enable-dubbing")
        if candidate_voice is not None:
            command.extend(("--tts-voice-id", candidate_voice))
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if completed.returncode == 0 or "JOB_ID_CONFLICT" not in completed.stderr or '"event":"completed"' in completed.stdout:
            raise SmokeError(f"{kind} replay incorrectly accepted another job's identity")
        if status_path.read_bytes() != original_status:
            raise SmokeError(f"{kind} replay overwrote the original completed status")
        with final.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != original_digest:
                raise SmokeError(f"{kind} replay changed the original video")
        with closing(sqlite3.connect(data_root / "control/jobs.sqlite3")) as connection:
            if connection.execute("SELECT status FROM jobs WHERE job_id='smoke-good'").fetchone() != ("succeeded",):
                raise SmokeError(f"{kind} replay changed original durable status")
        outcomes.append({"changed": kind, "code": "JOB_ID_CONFLICT", "original_status_and_video": "preserved"})
    return {"status": "passed", "cases": outcomes}


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_downgrade_receipt(output: Path, status: dict[str, Any], job_id: str,
                              *, partial: bool, fallback_code: str = "TTS_FAILED") -> dict[str, Any]:
    value = _require_status(status, "COMPLETED", job_id)
    expected_reason = "completed_partial_dubbing" if partial else "completed_b1_fallback"
    if value.get("reason") != expected_reason or not isinstance(value.get("message"), str):
        raise SmokeError("native completion hid the actual dubbing downgrade")
    phrase = "lồng tiếng chưa đầy đủ" if partial else "lồng tiếng không khả dụng"
    if phrase not in value["message"]:
        raise SmokeError("native completion omitted the user-visible dubbing limitation")
    manifest = _json(output / "job_manifest.json")
    qc = _json(output / "qc_report.json")
    if (manifest.get("job_id") != job_id or manifest.get("dubbing", {}).get("enabled") is not True or
            qc.get("status") != "passed" or qc.get("downgrade") is not True or
            qc.get("audio") != manifest.get("audio")):
        raise SmokeError("downgrade completion differs from the validated job/QC receipt")
    audio = manifest["audio"]
    if partial:
        tts = _json(Path(audio["tts_document"]))
        mix = _json(Path(audio["mix_document"]))
        failures, artifacts = tts.get("failures", []), tts.get("artifacts", [])
        if (audio.get("mode") != "dubbed" or audio.get("tts_failures") != 1 or audio.get("mix_failures") != 1 or
                len(failures) != 1 or len(artifacts) != 1 or failures[0].get("code") != "TTS_TEXT_UNSUPPORTED" or
                failures[0].get("retryable") is not False or failures[0].get("attempt") != 1 or
                failures[0].get("segment_id") == artifacts[0].get("segment_id")):
            raise SmokeError("native per-cue refusal poisoned following speech or retried bad input")
        states = {item["segment_id"]: item["status"] for item in mix.get("segments", [])}
        if (states.get(failures[0]["segment_id"]) != "failed" or
                states.get(artifacts[0]["segment_id"]) != "completed" or
                [item["segment_id"] for item in mix.get("duck_windows", [])] != [artifacts[0]["segment_id"]]):
            raise SmokeError("failed speech changed source ducking or lost the following valid cue")
    elif (audio.get("mode") != "original" or manifest.get("production_profile") != "cpu-local-file-b1-downgraded-from-b2" or
          not any(item.startswith(f"B2_AUDIO_FALLBACK_TO_B1: {fallback_code}:") for item in manifest.get("warnings", []))):
        raise SmokeError("total speech refusal did not preserve the explicit B1/audio fallback")
    return {"reason": value["reason"], "message": value["message"],
            "qc_sha256": _file_digest(output / "qc_report.json"),
            "following_cue_generated": partial, "b1_fallback": not partial}


def _verify_visible_downgrades(supervisor: Path, root: Path, data_root: Path, work: Path,
                               ffmpeg: Path, ffprobe: Path, *, voice_id: str, timeout: float) -> dict[str, Any]:
    if any((work / name).exists() for name in ("cue refusal output", "all refused output", "no audio output")):
        raise SmokeError("visible downgrade qualification requires fresh outputs and job evidence")
    source = _make_source(ffmpeg, work / "cue refusal input", seconds=18, stem="bounded cue refusal", size="180x320")
    # Authored extreme numeric input exercises the real pinned phonemizer's
    # content guard. Explicit Vietnamese makes this a TTS/fallback qualification;
    # generated sine audio cannot establish ASR language or translation quality.
    rejected_text = "12345678901234567890123456789012345678901234567890" * 10
    first_cue = f"1\n00:00:00,000 --> 00:00:09,000\n{rejected_text}\n\n"
    source.with_suffix(".srt").write_text(first_cue +
        "2\n00:00:09,000 --> 00:00:17,500\nĐừng đi. Tôi còn điều muốn nói với anh.\n\n", encoding="utf-8")
    output = work / "cue refusal output"
    job_id = "smoke-cue-refusal"
    status, killed, _ = _run_supervisor(supervisor, root, data_root, source, output, job_id,
        timeout=timeout, enable_dubbing=True, tts_voice_id=voice_id, source_language="vi")
    if killed or status is None:
        raise SmokeError("cue refusal qualification was unexpectedly interrupted")
    partial = _verify_downgrade_receipt(output, status, job_id, partial=True)
    partial["output"] = _verify_output(ffprobe, output, 18, expect_dubbing=True, expect_voice_id=voice_id)
    if (partial["output"]["width"], partial["output"]["height"]) != (180, 320):
        raise SmokeError("portrait dubbing changed the source video dimensions")
    _run([ffmpeg, "-v", "error", "-i", output / "final_vi.mp4", "-f", "null", "-"], timeout=timeout)
    original = {str(path.relative_to(output)): (_file_digest(path), path.stat().st_mtime_ns)
                for path in output.rglob("*") if path.is_file()}
    replay, killed, log = _run_supervisor(supervisor, root, data_root, source, output, job_id,
        timeout=timeout, enable_dubbing=True, tts_voice_id=voice_id, source_language="vi")
    if killed or replay is None:
        raise SmokeError("completed degraded replay was unexpectedly interrupted")
    replay_summary = _verify_downgrade_receipt(output, replay, job_id, partial=True)
    current = {str(path.relative_to(output)): (_file_digest(path), path.stat().st_mtime_ns)
               for path in output.rglob("*") if path.is_file()}
    events = []
    for line in log.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if (current != original or replay_summary != {key: partial[key] for key in replay_summary} or
            not any(isinstance(event, dict) and event.get("event") == "completed" and event.get("resumed") is True for event in events)):
        raise SmokeError("completed degraded replay changed outputs, regenerated speech or lost its visible limitation")
    partial["completed_replay_preserved"] = True

    fallback_source = _make_source(ffmpeg, work / "all refused input", seconds=18, stem="all speech refused")
    fallback_source.with_suffix(".srt").write_text(first_cue, encoding="utf-8")
    fallback_output = work / "all refused output"
    fallback_id = "smoke-all-cues-refused"
    fallback, killed, _ = _run_supervisor(supervisor, root, data_root, fallback_source, fallback_output, fallback_id,
        timeout=timeout, enable_dubbing=True, tts_voice_id=voice_id, source_language="vi")
    if killed or fallback is None:
        raise SmokeError("B1 fallback qualification was unexpectedly interrupted")
    fallback_summary = _verify_downgrade_receipt(fallback_output, fallback, fallback_id, partial=False)
    fallback_summary["output"] = _verify_output(ffprobe, fallback_output, 18)
    _run([ffmpeg, "-v", "error", "-i", fallback_output / "final_vi.mp4", "-f", "null", "-"], timeout=timeout)
    no_audio_source = work / "no audio input.mp4"
    _run([ffmpeg, "-v", "error", "-y", "-i", fallback_source, "-map", "0:v:0", "-c:v", "copy", "-an", no_audio_source], timeout=timeout)
    no_audio_source.with_suffix(".srt").write_text(
        "1\n00:00:00,000 --> 00:00:08,500\nĐừng đi. Tôi còn điều muốn nói với anh.\n\n", encoding="utf-8")
    no_audio_output = work / "no audio output"
    no_audio_id = "smoke-no-audio"
    no_audio_status, killed, _ = _run_supervisor(supervisor, root, data_root, no_audio_source, no_audio_output, no_audio_id,
        timeout=timeout, enable_dubbing=True, tts_voice_id=voice_id, source_language="vi")
    if killed or no_audio_status is None:
        raise SmokeError("no-audio qualification was unexpectedly interrupted")
    no_audio_summary = _verify_downgrade_receipt(no_audio_output, no_audio_status, no_audio_id,
        partial=False, fallback_code="AUDIO_STREAM_MISSING")
    if (_json(no_audio_output / "qc_report.json").get("source_probe", {}).get("has_audio") is not False or
            "không có audio" not in no_audio_summary["message"] or "audio gốc" in no_audio_summary["message"]):
        raise SmokeError("no-audio source lost its explicit limitation or claimed nonexistent original audio")
    no_audio_summary["output"] = _verify_output(ffprobe, no_audio_output, 18, expect_audio=False)
    _run([ffmpeg, "-v", "error", "-i", no_audio_output / "final_vi.mp4", "-f", "null", "-"], timeout=timeout)
    return {"partial_dubbing": partial, "all_cues_refused": fallback_summary, "no_audio_source": no_audio_summary,
            "scope": "actual pinned TTS/content refusal, native status and source-preserving export; authored VI sidecars and generated media"}


def _tts_checkpoint_snapshot(output: Path) -> dict[str, Any] | None:
    records = list(output.glob(".dubflow-work/b2-audio/*/*/tts/checkpoints/*.json"))
    if not records:
        return None
    if len(records) >= 3:
        raise SmokeError("TTS recovery missed the partial-synthesis interruption window")
    record = sorted(records)[0]
    with record.open("rb") as stream:
        payload = stream.read(65537)
    if len(payload) > 65536:
        raise SmokeError("TTS recovery checkpoint exceeds its bound")
    try:
        value = json.loads(payload)
        artifact = value["artifact"]
        encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or
                re.fullmatch(r"[a-f0-9]{64}", value["identity"]) is None or
                value["artifact_record_hash"] != hashlib.sha256(encoded).hexdigest()):
            raise ValueError("invalid checkpoint identity or metadata")
        audio = Path(artifact["path"])
        if (not audio.is_absolute() or audio.parent != record.parent.parent or
                re.fullmatch(r"tts-[a-f0-9]{32}\.wav", audio.name) is None):
            raise ValueError("checkpoint audio escapes its generation")
        for path in (record, audio, *record.parents):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("checkpoint uses a linked path")
        before = audio.stat()
        digest = _file_digest(audio)
        after = audio.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("checkpoint audio changed during verification")
        if artifact["content_hash"] != "sha256:" + digest:
            raise ValueError("checkpoint audio checksum differs")
        with wave.open(str(audio), "rb") as reader:
            if (reader.getnframes() <= 0 or reader.getnframes() != artifact["frame_count"] or
                    reader.getframerate() != artifact["sample_rate"] or
                    reader.getnchannels() != artifact["channels"] or reader.getsampwidth() != 2):
                raise ValueError("checkpoint audio has invalid PCM")
        return {"segment_id": artifact["segment_id"], "audio": str(audio), "sha256": digest,
                "mtime_ns": after.st_mtime_ns, "record": str(record),
                "record_sha256": hashlib.sha256(payload).hexdigest(), "committed_cues": len(records)}
    except (ValueError, TypeError, KeyError, OSError, wave.Error, EOFError) as error:
        raise SmokeError(f"TTS recovery checkpoint is invalid: {error}") from error


def _verify_tts_recovery(supervisor: Path, root: Path, data_root: Path, work_root: Path,
                         ffmpeg: Path, ffprobe: Path, *, voice_id: str, timeout: float) -> dict[str, Any]:
    """Interrupt actual packaged synthesis after a committed cue, then recover it."""
    source = _make_source(ffmpeg, work_root / "tts recovery input", seconds=27, stem="dialogue")
    source.with_suffix(".srt").write_text(
        "1\n00:00:00,000 --> 00:00:09,000\nPlease wait. I have something to tell you.\n\n"
        "2\n00:00:09,000 --> 00:00:18,000\nBe careful. Someone is behind the door.\n\n"
        "3\n00:00:18,000 --> 00:00:27,000\nThank you. I am glad you came back.\n",
        encoding="utf-8")
    output = work_root / "tts recovery output"
    if output.exists():
        raise SmokeError("TTS recovery requires a fresh output directory")
    job_id = "smoke-tts-recovery"
    command = _supervisor_command(supervisor, root, data_root, source, output, job_id,
                                  enable_dubbing=True, tts_voice_id=voice_id, source_language="en")
    snapshot = None
    # File-backed logs keep child pipes draining while the controller observes
    # the actual fsynced record; elapsed time alone cannot prove a TTS restart.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                snapshot = _tts_checkpoint_snapshot(output)
                if snapshot is not None:
                    if (output / "job_manifest.json").exists():
                        raise SmokeError("TTS recovery reached publication before interruption")
                    _terminate_tree(process)
                    break
                if time.monotonic() >= deadline:
                    raise SmokeError("TTS recovery timed out waiting for a committed speech cue")
                time.sleep(0.05)
            if snapshot is None or process.poll() is None or process.returncode == 0:
                detail = []
                for stream in (stdout, stderr):
                    stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, stream.tell() - 4096))
                    detail.append(stream.read().decode("utf-8", errors="replace"))
                raise SmokeError("TTS recovery did not hard-kill an active native supervisor: " + "\n".join(detail))
            if (output / "job_manifest.json").exists():
                raise SmokeError("interrupted TTS unexpectedly published its output")
        finally:
            _terminate_tree(process)
    status_path = data_root / "control/jobs" / f"{job_id}.json"
    _require_status(_json(status_path), "RUNNING", job_id)
    resumed, killed, _ = _run_supervisor(supervisor, root, data_root, source, output, job_id,
        timeout=timeout, enable_dubbing=True, tts_voice_id=voice_id, source_language="en")
    if killed or resumed is None:
        raise SmokeError("TTS recovery restart was interrupted")
    _require_status(resumed, "COMPLETED", job_id)
    result = _verify_output(ffprobe, output, 27, expect_dubbing=True, expect_voice_id=voice_id)
    audio = Path(snapshot["audio"])
    if (_file_digest(audio) != snapshot["sha256"] or audio.stat().st_mtime_ns != snapshot["mtime_ns"] or
            _file_digest(Path(snapshot["record"])) != snapshot["record_sha256"]):
        raise SmokeError("TTS recovery regenerated or modified previously committed speech")
    manifest = _json(output / "job_manifest.json")
    tts = _json(Path(manifest["audio"]["tts_document"]))
    if (len(tts["artifacts"]) != 3 or tts.get("failures") or
            "reused TTS checkpoint for " + snapshot["segment_id"] not in tts.get("warnings", ()) or
            not any(item["segment_id"] == snapshot["segment_id"] and item["path"] == snapshot["audio"]
                    and item["content_hash"] == "sha256:" + snapshot["sha256"] for item in tts["artifacts"])):
        raise SmokeError("TTS recovery did not report actual same-cue checkpoint reuse")
    with closing(sqlite3.connect(data_root / "control/jobs.sqlite3")) as connection:
        if connection.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone() != ("succeeded",):
            raise SmokeError("TTS recovery did not durably complete the original job")
    _run([ffmpeg, "-nostdin", "-v", "error", "-i", output / "final_vi.mp4", "-f", "null", "-"], timeout=120)
    return {"status": "passed", "first_supervisor_hard_killed": True,
            "interrupted_after_committed_cues": snapshot["committed_cues"], "reused_cue": snapshot,
            "wave_and_record_unchanged": True, "durable_same_job_completed": True,
            "full_output_decode": True, "output": result,
            "scope": "packaged supervisor/owned runtime; synthetic media with real translation and selected speech"}


def _verify_voice_version(supervisor: Path, root: Path, data_root: Path, source: Path,
                          original: Path, ffprobe: Path, *, voice_id: str, timeout: float) -> dict[str, Any]:
    original_files = {str(path.relative_to(original)): _file_digest(path)
                      for path in original.rglob("*") if path.is_file()}
    original_status = (data_root / "control/jobs/smoke-good.json").read_bytes()
    next_voice = "vi-thai-son-vieneu3-v1" if voice_id != "vi-thai-son-vieneu3-v1" else "vi-thuy-dung-vieneu3-v1"
    output = original.with_name("new voice version [spaces]")
    status, killed, _ = _run_supervisor(supervisor, root, data_root, source, output, "smoke-voice-version",
                                        timeout=timeout, enable_dubbing=True, tts_voice_id=next_voice)
    if killed or status is None:
        raise SmokeError("new voice version was unexpectedly killed")
    _require_status(status, "COMPLETED", "smoke-voice-version")
    result = _verify_output(ffprobe, output, 3, expect_dubbing=True, expect_voice_id=next_voice)
    current_files = {str(path.relative_to(original)): _file_digest(path)
                     for path in original.rglob("*") if path.is_file()}
    if current_files != original_files or (data_root / "control/jobs/smoke-good.json").read_bytes() != original_status:
        raise SmokeError("new voice version changed the original export or status")
    with closing(sqlite3.connect(data_root / "control/jobs.sqlite3")) as connection:
        rows = dict(connection.execute("SELECT job_id, status FROM jobs WHERE job_id IN ('smoke-good','smoke-voice-version')"))
    if rows != {"smoke-good": "succeeded", "smoke-voice-version": "succeeded"}:
        raise SmokeError("new voice version did not preserve both durable completed jobs")
    old_dialogue = _file_digest(original / "editable/dialogue_stem.wav")
    new_dialogue = _file_digest(output / "editable/dialogue_stem.wav")
    if old_dialogue == new_dialogue:
        raise SmokeError("different selected voices reused identical dialogue audio")
    return {"status": "passed", "original_voice_id": voice_id, "new_voice_id": next_voice,
            "original_exports_and_status": "preserved", "original_file_count": len(original_files),
            "original_dialogue_sha256": old_dialogue, "new_dialogue_sha256": new_dialogue, "new_job": result,
            "scope": "native supervisor with separate output directories; desktop default path is tested by native host tests"}


def run(args: argparse.Namespace) -> dict[str, Any]:
    supervisor = Path(args.supervisor).resolve()
    root = Path(args.root).resolve()
    data_root = Path(args.data_root).resolve()
    work_root = Path(args.work_root).resolve()
    ffmpeg = Path(args.ffmpeg).resolve()
    ffprobe = Path(args.ffprobe).resolve()
    for path, label in ((supervisor, "supervisor"), (ffmpeg, "ffmpeg"), (ffprobe, "ffprobe")):
        if not path.is_file():
            raise SmokeError(f"{label} is unavailable: {path}")
    work_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    short_source = _make_source(ffmpeg, work_root / "short input [spaces]", seconds=3, stem="sample video [spaces]")
    selected_voice = "vi-truc-ly-vieneu3-v1" if args.enable_dubbing else None
    resume_report: dict[str, Any] | None = None
    if args.exercise_hard_kill:
        kill_output = work_root / "resume output"
        first, killed, kill_log = _run_supervisor(
            supervisor,
            root,
            data_root,
            short_source,
            kill_output,
            "smoke-resume",
            timeout=args.timeout,
            kill_after=args.kill_after,
            enable_dubbing=args.enable_dubbing,
            tts_voice_id=selected_voice,
        )
        if not killed:
            raise SmokeError(
                "hard-kill qualification did not interrupt the first supervisor invocation"
            )
        resumed, was_killed, resume_log = _run_supervisor(
            supervisor,
            root,
            data_root,
            short_source,
            kill_output,
            "smoke-resume",
            timeout=args.timeout,
            enable_dubbing=args.enable_dubbing,
            tts_voice_id=selected_voice,
        )
        if was_killed or resumed is None:
            raise SmokeError("resume invocation was unexpectedly killed")
        _require_status(resumed, "COMPLETED", "smoke-resume")
        resume_report = {
            "first_invocation_killed": killed,
            "first_status_present": first is not None,
            "resume_output": _verify_output(ffprobe, kill_output, 3, expect_dubbing=args.enable_dubbing, expect_voice_id=selected_voice),
            "logs": (kill_log + resume_log)[-4096:],
        }

    corrupt_source = work_root / "corrupt video.mp4"
    corrupt_source.write_bytes(b"not a media container")
    bad_output = work_root / "bad output"
    bad_status, killed, bad_log = _run_supervisor(
        supervisor,
        root,
        data_root,
        corrupt_source,
        bad_output,
        "smoke-corrupt",
        timeout=args.timeout,
        enable_dubbing=args.enable_dubbing,
        tts_voice_id=selected_voice,
    )
    if killed or bad_status is None:
        raise SmokeError("corrupt job was unexpectedly killed")
    _require_status(bad_status, "FAILED", "smoke-corrupt")
    corrupt_failure = _verify_corrupt_failure(bad_log, "smoke-corrupt")

    good_output = work_root / "good output [spaces]"
    good_status, killed, good_log = _run_supervisor(
        supervisor,
        root,
        data_root,
        short_source,
        good_output,
        "smoke-good",
        timeout=args.timeout,
        enable_dubbing=args.enable_dubbing,
        tts_voice_id=selected_voice,
    )
    if killed or good_status is None:
        raise SmokeError("valid job was unexpectedly killed")
    _require_status(good_status, "COMPLETED", "smoke-good")
    good_output_report = _verify_output(ffprobe, good_output, 3, expect_dubbing=args.enable_dubbing, expect_voice_id=selected_voice)
    replay_isolation = _verify_replay_isolation(supervisor, root, data_root, short_source, good_output, voice_id=selected_voice)
    voice_version = _verify_voice_version(supervisor, root, data_root, short_source, good_output, ffprobe,
                                          voice_id=selected_voice, timeout=args.timeout) if args.enable_dubbing else None

    long_output_report: dict[str, Any] | None = None
    if args.long_seconds > 3:
        long_source = _make_source(ffmpeg, work_root / "long input", seconds=args.long_seconds, stem="synthetic long video")
        long_output = work_root / "long output"
        long_status, killed, long_log = _run_supervisor(
            supervisor,
            root,
            data_root,
            long_source,
            long_output,
            "smoke-long",
            timeout=args.timeout,
            enable_dubbing=args.enable_dubbing,
            tts_voice_id=selected_voice,
        )
        if killed or long_status is None:
            raise SmokeError("long-form job was unexpectedly killed")
        _require_status(long_status, "COMPLETED", "smoke-long")
        long_output_report = _verify_output(ffprobe, long_output, args.long_seconds, expect_dubbing=args.enable_dubbing, expect_voice_id=selected_voice)
    tts_recovery = _verify_tts_recovery(supervisor, root, data_root, work_root, ffmpeg, ffprobe,
        voice_id=selected_voice, timeout=args.timeout) if selected_voice is not None else None
    visible_downgrades = _verify_visible_downgrades(supervisor, root, data_root, work_root, ffmpeg, ffprobe,
        voice_id=selected_voice, timeout=args.timeout) if selected_voice is not None else None
    profile_marker = data_root / "models" / ".profile-ready"
    if not profile_marker.is_file() or profile_marker.stat().st_size <= 0:
        raise SmokeError(f"model profile did not become ready: {profile_marker}")

    return {
        "schema_version": 1,
        "profile": "cpu-local-file-b2" if args.enable_dubbing else "cpu-local-file-b1",
        "batch_failure_isolation": "passed",
        "immutable_job_replay": replay_isolation,
        "voice_version_isolation": voice_version,
        "tts_checkpoint_recovery": tts_recovery,
        "visible_dubbing_downgrades": visible_downgrades,
        "corrupt_job_state": bad_status["status"],
        "corrupt_media_failure": corrupt_failure,
        "good_job": good_output_report,
        "resume": resume_report,
        "synthetic_long_form": long_output_report,
        "model_root": str(data_root / "models"),
        "model_profile_ready": True,
        "logs": bad_log[-4096:] + good_log[-4096:],
    }


def main(argv: Sequence[str] | None = None) -> int:
    # GitHub-hosted Windows runners may expose a legacy cp1252 console.  The
    # qualification report intentionally contains localized diagnostics, so
    # make both success and failure output UTF-8 before writing anything.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # Some embedded callers expose a stream whose encoding cannot
                # be changed; the report file remains UTF-8 in that case.
                pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supervisor", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--kill-after", type=float, default=3.0)
    parser.add_argument("--long-seconds", type=int, default=60)
    parser.add_argument("--exercise-hard-kill", action="store_true")
    parser.add_argument("--enable-dubbing", action="store_true", help="exercise the app-owned offline B2 TTS and AUD-0 mixer")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except SmokeError as error:
        print(json.dumps({"passed": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
