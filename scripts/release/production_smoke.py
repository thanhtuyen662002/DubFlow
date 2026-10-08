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
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
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


def _make_source(ffmpeg: Path, root: Path, *, seconds: int, stem: str) -> Path:
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
            f"color=c=0x18324a:s=320x180:r=25:d={seconds}",
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
) -> tuple[dict[str, Any] | None, bool, str]:
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


def _verify_output(ffprobe: Path, output_dir: Path, source_duration_seconds: int, *, expect_dubbing: bool = False, expect_voice_id: str | None = None) -> dict[str, Any]:
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
        if provenance.get("backend_id") != "vieneu-v3-turbo-onnx-v1" or provenance.get("producer_version") != "3.0.0":
            raise SmokeError(f"B2 TTS receipt differs from the selected native producer: {provenance!r}")
        if expect_voice_id is not None and provenance.get("voice_id") != expect_voice_id:
            raise SmokeError("packaged TTS did not preserve the explicitly selected preset")
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
    if not audios or audios[0].get("codec_name") != "aac":
        raise SmokeError(f"output audio is not AAC: {audios}")
    duration = float((probe.get("format") or {}).get("duration", "0"))
    if duration <= 0 or duration + 2 < source_duration_seconds:
        raise SmokeError(f"output duration is invalid: {duration}")
    return {
        "output": str(final),
        "duration_seconds": duration,
        "video_codec": videos[0].get("codec_name"),
        "audio_codec": audios[0].get("codec_name"),
        "artifacts": [str(path.relative_to(output_dir)) for path in required],
        "selected_voice_id": expect_voice_id,
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
        with sqlite3.connect(data_root / "control/jobs.sqlite3") as connection:
            if connection.execute("SELECT status FROM jobs WHERE job_id='smoke-good'").fetchone() != ("succeeded",):
                raise SmokeError(f"{kind} replay changed original durable status")
        outcomes.append({"changed": kind, "code": "JOB_ID_CONFLICT", "original_status_and_video": "preserved"})
    return {"status": "passed", "cases": outcomes}


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
    profile_marker = data_root / "models" / ".profile-ready"
    if not profile_marker.is_file() or profile_marker.stat().st_size <= 0:
        raise SmokeError(f"model profile did not become ready: {profile_marker}")

    return {
        "schema_version": 1,
        "profile": "cpu-local-file-b2" if args.enable_dubbing else "cpu-local-file-b1",
        "batch_failure_isolation": "passed",
        "immutable_job_replay": replay_isolation,
        "corrupt_job_state": bad_status["status"],
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
