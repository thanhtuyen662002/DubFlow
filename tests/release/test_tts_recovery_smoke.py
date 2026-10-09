"""Deterministic rejection tests for native TTS recovery qualification evidence."""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

from scripts.release import production_smoke as smoke


def checkpoint(output: Path) -> tuple[Path, Path]:
    audio = output / ".dubflow-work/b2-audio/recipe/generation/tts" / ("tts-" + "a" * 32 + ".wav")
    audio.parent.mkdir(parents=True)
    with wave.open(str(audio), "wb") as writer:
        writer.setparams((1, 2, 8000, 8000, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x01" * 8000)
    artifact = {"segment_id": "cue-1", "path": str(audio), "content_hash": "sha256:" + smoke._file_digest(audio),
                "frame_count": 8000, "sample_rate": 8000, "channels": 1}
    record = audio.parent / "checkpoints/cue.json"
    record.parent.mkdir()
    write_record(record, artifact)
    return audio, record


def write_record(record: Path, artifact: dict) -> None:
    encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    record.write_text(json.dumps({"schema_version": 1, "identity": "1" * 64, "artifact": artifact,
        "artifact_record_hash": sha256(encoded).hexdigest()}), encoding="utf-8")


class LiveOwnershipEvidenceTests(unittest.TestCase):
    def test_native_guard_rejects_actual_race_signatures_and_missing_overlap(self):
        # These are deterministic guard tests with controlled child results,
        # never evidence of native execution, actual TTS or human voice quality.
        for defect in (None, "neighbor_recovered", "server_recovered", "server_accepted",
                       "duplicate_accepted", "duplicate_status", "no_overlap", "extra_start", "missing_speech"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                work = Path(directory)
                data = work / "data"
                (data / "control/jobs").mkdir(parents=True)
                database = data / "control/jobs.sqlite3"
                job_id = "smoke-live-owner"
                output = work / "ownership output"
                source = work / "source.mp4"
                source.write_bytes(b"generated fixture only")
                status_path = data / f"control/jobs/{job_id}.json"
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute("CREATE TABLE jobs(job_id TEXT, status TEXT, last_error TEXT)")
                    db.execute("CREATE TABLE stages(job_id TEXT, stage_id TEXT, status TEXT, attempt INTEGER, "
                               "max_attempts INTEGER, last_error TEXT, retry_condition TEXT, checkpoint_id TEXT)")
                    db.execute("INSERT INTO jobs VALUES (?, 'running', NULL)", (job_id,))
                    db.execute("INSERT INTO stages VALUES (?, 'local-file', 'running', 1, 3, NULL, NULL, 'transcript')", (job_id,))
                child = SimpleNamespace(returncode=None)
                child.poll = lambda: child.returncode

                def start(*args, **kwargs):
                    status_path.write_text(json.dumps({"job_id": job_id, "status": {"state": "RUNNING"}}))
                    return child

                def finish(timeout):
                    child.returncode = 0
                    with closing(sqlite3.connect(database)) as db, db:
                        db.execute("UPDATE jobs SET status='succeeded'")
                        db.execute("UPDATE stages SET status='succeeded', checkpoint_id='qc', attempt=?",
                                   (2 if defect == "extra_start" else 1,))
                    output.mkdir()
                    speech = output / "tts.json"
                    speech.write_text(json.dumps({"failures": [], "artifacts": [{}] * (35 if defect == "missing_speech" else 36)}))
                    (output / "job_manifest.json").write_text(json.dumps({"audio": {"tts_document": str(speech)}}))
                    status_path.write_text(json.dumps({"job_id": job_id, "status": {"state": "COMPLETED",
                        "checkpoint_id": "qc", "retry": {"attempt": "1", "max_attempts": "3", "condition_fingerprint": None},
                        "progress": {"completed_units": "1000", "total_units": "1000"}}}))
                child.wait = finish

                def corrupt(*args, **kwargs):
                    if defect == "neighbor_recovered":
                        with closing(sqlite3.connect(database)) as db, db:
                            db.execute("UPDATE jobs SET status='recovering', last_error='PROCESS_RESTART'")
                            db.execute("UPDATE stages SET status='recovering', last_error='PROCESS_RESTART'")
                    if defect == "no_overlap": child.returncode = 0
                    bad_id = "smoke-live-corrupt-neighbor"
                    log = json.dumps({"event": "failed", "job_id": bad_id,
                                      "code": "MEDIA_PROBE_FAILED", "attempt": 1, "retryable": False})
                    return {"job_id": bad_id, "status": {"state": "FAILED"}}, False, log

                def secondary(command, **kwargs):
                    if command[1] == "--root":
                        self.assertNotIn("serve", command)
                        events = [{"event": "ready", "recovered_stages": 1 if defect == "server_recovered" else 0}]
                        events.extend({"event": "accepted" if defect == "server_accepted" else "error", "job_id": job_id,
                                       "code": "JOB_ALREADY_RUNNING", "retryable": False} for _ in range(2))
                        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(row) for row in events), stderr="")
                    if defect == "duplicate_status":
                        Path(command[command.index("--status-path") + 1]).write_text("forbidden publication")
                    return SimpleNamespace(returncode=0 if defect == "duplicate_accepted" else 1,
                                           stdout="", stderr="JOB_ALREADY_RUNNING")

                def terminate(process):
                    if process.returncode is None: process.returncode = -9

                with mock.patch.object(smoke, "_make_source", return_value=source), \
                        mock.patch.object(smoke.subprocess, "Popen", side_effect=start), \
                        mock.patch.object(smoke.subprocess, "run", side_effect=secondary), \
                        mock.patch.object(smoke, "_run_supervisor", side_effect=corrupt), \
                        mock.patch.object(smoke, "_terminate_tree", side_effect=terminate), \
                        mock.patch.object(smoke, "_verify_output", return_value={"fixture_only": True}), \
                        mock.patch.object(smoke, "_run"):
                    if defect is None:
                        report = smoke._verify_live_job_ownership(work / "supervisor", work, data, work,
                            work / "ffmpeg", work / "ffprobe", voice_id="fixture", timeout=10)
                        self.assertTrue(report["overlap_verified"])
                        self.assertEqual(report["completed"]["attempt"], 1)
                    else:
                        with self.assertRaises(smoke.SmokeError):
                            smoke._verify_live_job_ownership(work / "supervisor", work, data, work,
                                work / "ffmpeg", work / "ffprobe", voice_id="fixture", timeout=10)


class RecoverySnapshotTests(unittest.TestCase):
    def test_snapshot_waits_for_commit_and_validates_actual_waveform(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertIsNone(smoke._tts_checkpoint_snapshot(output))
            audio, record = checkpoint(output)
            snapshot = smoke._tts_checkpoint_snapshot(output)
            self.assertEqual(snapshot["sha256"], smoke._file_digest(audio))
            self.assertEqual(snapshot["mtime_ns"], audio.stat().st_mtime_ns)
            self.assertEqual(snapshot["committed_cues"], 1)
            with audio.open("r+b") as stream:
                stream.seek(-2, 2)
                stream.write(b"\x02\x01")
            with self.assertRaisesRegex(smoke.SmokeError, "checksum"):
                smoke._tts_checkpoint_snapshot(output)

    def test_invalid_metadata_paths_and_completed_synthesis_cannot_qualify(self):
        for defect in ("metadata", "foreign_path", "oversized", "completed"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                output = Path(directory)
                _, record = checkpoint(output)
                value = json.loads(record.read_text())
                if defect == "metadata":
                    value["artifact"]["frame_count"] = 1
                    record.write_text(json.dumps(value))
                elif defect == "foreign_path":
                    value["artifact"]["path"] = str(output / "foreign.wav")
                    write_record(record, value["artifact"])
                elif defect == "oversized":
                    record.write_bytes(b" " * 65537)
                else:
                    for name in ("second.json", "third.json"):
                        record.with_name(name).write_bytes(record.read_bytes())
                with self.assertRaises(smoke.SmokeError):
                    smoke._tts_checkpoint_snapshot(output)


class RecoveryEvidenceTests(unittest.TestCase):
    def test_qualification_rejects_regeneration_missing_reuse_and_incomplete_durable_state(self):
        for defect in (None, "wave_regenerated", "record_rewritten", "no_reuse_warning", "durable_failed"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                root = Path(directory)
                work = root / "work"
                work.mkdir()
                data = root / "data"
                (data / "control/jobs").mkdir(parents=True)
                status_path = data / "control/jobs/smoke-tts-recovery.json"
                output = work / "tts recovery output"
                source = work / "source.mp4"
                source.write_bytes(b"fixture source, not native qualification")
                process = SimpleNamespace(returncode=None)
                process.poll = lambda: process.returncode

                def start(*args, **kwargs):
                    self.assertIn("--source-language", args[0])
                    self.assertEqual(args[0][args[0].index("--source-language") + 1], "en")
                    checkpoint(output)
                    status_path.write_text(json.dumps({"job_id": "smoke-tts-recovery", "status": {"state": "RUNNING"}}))
                    return process

                def terminate(child):
                    child.returncode = -9

                def resume(*args, **kwargs):
                    self.assertEqual(kwargs["source_language"], "en")
                    audio, = output.glob(".dubflow-work/b2-audio/*/*/tts/*.wav")
                    record, = audio.parent.glob("checkpoints/*.json")
                    artifact = json.loads(record.read_text())["artifact"]
                    warnings = [] if defect == "no_reuse_warning" else ["reused TTS checkpoint for cue-1"]
                    tts = {"artifacts": [artifact, {"segment_id": "cue-2"}, {"segment_id": "cue-3"}],
                           "failures": [], "warnings": warnings}
                    tts_path = output / "tts.json"
                    tts_path.write_text(json.dumps(tts))
                    (output / "job_manifest.json").write_text(json.dumps({"audio": {"tts_document": str(tts_path)}}))
                    if defect == "wave_regenerated":
                        with audio.open("r+b") as stream:
                            stream.seek(-2, 2)
                            stream.write(b"\x02\x01")
                    elif defect == "record_rewritten":
                        record.write_text(record.read_text() + " ")
                    with closing(sqlite3.connect(data / "control/jobs.sqlite3")) as db, db:
                        db.execute("CREATE TABLE jobs(job_id TEXT, status TEXT)")
                        db.execute("INSERT INTO jobs VALUES (?, ?)", ("smoke-tts-recovery",
                            "failed" if defect == "durable_failed" else "succeeded"))
                    return {"job_id": "smoke-tts-recovery", "status": {"state": "COMPLETED"}}, False, ""

                with mock.patch.object(smoke, "_make_source", return_value=source), \
                        mock.patch.object(smoke.subprocess, "Popen", side_effect=start), \
                        mock.patch.object(smoke, "_terminate_tree", side_effect=terminate), \
                        mock.patch.object(smoke, "_run_supervisor", side_effect=resume), \
                        mock.patch.object(smoke, "_verify_output", return_value={"fixture_only": True}), \
                        mock.patch.object(smoke, "_run"):
                    if defect is None:
                        report = smoke._verify_tts_recovery(root / "supervisor", root, data, work,
                            root / "ffmpeg", root / "ffprobe", voice_id="test-voice", timeout=10)
                        self.assertTrue(report["wave_and_record_unchanged"])
                        self.assertEqual(report["interrupted_after_committed_cues"], 1)
                    else:
                        with self.assertRaises(smoke.SmokeError):
                            smoke._verify_tts_recovery(root / "supervisor", root, data, work,
                                root / "ffmpeg", root / "ffprobe", voice_id="test-voice", timeout=10)


class VisibleDowngradeTests(unittest.TestCase):
    def receipt(self, output, *, partial=True):
        output.mkdir(parents=True, exist_ok=True)
        tts_path, mix_path = output / "tts.json", output / "mix.json"
        tts = {"artifacts": [{"segment_id": "cue-2"}], "failures": [
            {"segment_id": "cue-1", "code": "TTS_TEXT_UNSUPPORTED", "attempt": 1, "retryable": False}]}
        mix = {"segments": [{"segment_id": "cue-1", "status": "failed"},
            {"segment_id": "cue-2", "status": "completed"}], "duck_windows": [{"segment_id": "cue-2"}]}
        tts_path.write_text(json.dumps(tts))
        mix_path.write_text(json.dumps(mix))
        audio = {"mode": "dubbed", "tts_failures": 1, "mix_failures": 1,
                 "tts_document": str(tts_path), "mix_document": str(mix_path)} if partial else {"mode": "original"}
        job_id = "smoke-cue-refusal" if partial else "smoke-all-cues-refused"
        manifest = {"job_id": job_id, "dubbing": {"enabled": True}, "audio": audio,
                    "warnings": ["B2_AUDIO_FALLBACK_TO_B1: TTS_FAILED: no speech"] if not partial else [],
                    "production_profile": "cpu-local-file-b1-downgraded-from-b2"}
        qc = {"status": "passed", "downgrade": True, "audio": audio}
        (output / "job_manifest.json").write_text(json.dumps(manifest))
        (output / "qc_report.json").write_text(json.dumps(qc))
        status = {"job_id": job_id, "status": {"state": "COMPLETED",
            "reason": "completed_partial_dubbing" if partial else "completed_b1_fallback",
            "message": "lồng tiếng chưa đầy đủ" if partial else "lồng tiếng không khả dụng",
            "checkpoint_id": "qc", "retry": {"attempt": "1", "max_attempts": "3", "condition_fingerprint": None},
            "progress": {"completed_units": "1000", "total_units": "1000", "heartbeat_sequence": "23"}}}
        return status, tts, mix

    def test_downgrade_validator_rejects_hidden_retry_poisoning_and_bad_source_ducking(self):
        for defect in (None, "hidden", "retry", "repeat", "missing_next", "same_id", "duck_failed", "qc_diff"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                output = Path(directory)
                status, tts, mix = self.receipt(output)
                if defect == "hidden": status["status"]["reason"] = "completed"
                if defect == "retry": tts["failures"][0]["retryable"] = True
                if defect == "repeat": tts["failures"][0]["attempt"] = 2
                if defect == "missing_next": tts["artifacts"] = []
                if defect == "same_id": tts["artifacts"][0]["segment_id"] = "cue-1"
                if defect == "duck_failed": mix["duck_windows"].append({"segment_id": "cue-1"})
                if defect == "qc_diff": (output / "qc_report.json").write_text(json.dumps({"status": "passed", "downgrade": False}))
                (output / "tts.json").write_text(json.dumps(tts))
                (output / "mix.json").write_text(json.dumps(mix))
                if defect is None:
                    receipt = smoke._verify_downgrade_receipt(output, status, "smoke-cue-refusal", partial=True)
                    self.assertTrue(receipt["following_cue_generated"])
                else:
                    with self.assertRaises(smoke.SmokeError):
                        smoke._verify_downgrade_receipt(output, status, "smoke-cue-refusal", partial=True)

    def test_all_refused_requires_visible_b1_and_original_audio(self):
        for defect in (None, "audio", "profile", "warning", "message"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                output = Path(directory)
                status, _, _ = self.receipt(output, partial=False)
                manifest = smoke._json(output / "job_manifest.json")
                if defect == "audio": manifest["audio"]["mode"] = "dubbed"
                if defect == "profile": manifest["production_profile"] = "cpu-local-file-b2"
                if defect == "warning": manifest["warnings"] = []
                if defect == "message": status["status"]["message"] = "Đã xuất video"
                (output / "job_manifest.json").write_text(json.dumps(manifest))
                if defect is None:
                    self.assertTrue(smoke._verify_downgrade_receipt(output, status, "smoke-all-cues-refused", partial=False)["b1_fallback"])
                else:
                    with self.assertRaises(smoke.SmokeError):
                        smoke._verify_downgrade_receipt(output, status, "smoke-all-cues-refused", partial=False)

    def test_native_guard_rejects_regenerated_completed_replay(self):
        for defect in ("changed_output", "missing_resume", "lost_checkpoint", "reset_attempt", "changed_attempt", "lost_total"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                work = Path(directory)
                source = work / "source.mp4"
                source.write_bytes(b"fixture media, no native inference")
                count = 0

                def run_supervisor(*args, **kwargs):
                    nonlocal count
                    count += 1
                    output = args[4]
                    if count == 1:
                        status, _, _ = self.receipt(output)
                        return status, False, ""
                    status = {"job_id": "smoke-cue-refusal", "status": {"state": "COMPLETED",
                        "reason": "completed_partial_dubbing", "message": "lồng tiếng chưa đầy đủ",
                        "checkpoint_id": None if defect == "lost_checkpoint" else "qc",
                        "retry": {"attempt": "0" if defect == "reset_attempt" else "2" if defect == "changed_attempt" else "1",
                            "max_attempts": "3", "condition_fingerprint": None},
                        "progress": {"completed_units": "1000", "total_units": None if defect == "lost_total" else "1000"}}}
                    if defect == "changed_output": (output / "regenerated.wav").write_bytes(b"unwanted new speech")
                    return status, False, json.dumps({"event": "completed", "resumed": defect != "missing_resume"})

                with mock.patch.object(smoke, "_make_source", return_value=source), \
                        mock.patch.object(smoke, "_run_supervisor", side_effect=run_supervisor), \
                        mock.patch.object(smoke, "_verify_output", return_value={"fixture_only": True, "width": 180, "height": 320}), \
                        mock.patch.object(smoke, "_run"):
                    with self.assertRaisesRegex(smoke.SmokeError, "completed degraded replay"):
                        smoke._verify_visible_downgrades(work / "supervisor", work, work / "data", work,
                            work / "ffmpeg", work / "ffprobe", voice_id="fixture", timeout=10)

    def test_completed_replay_history_ignores_only_invocation_counter(self):
        with TemporaryDirectory() as directory:
            status, _, _ = self.receipt(Path(directory))
            before = smoke._completed_replay_history(status)
            status["status"]["progress"]["heartbeat_sequence"] = "0"
            self.assertEqual(smoke._completed_replay_history(status), before)
            status["status"]["retry"]["condition_fingerprint"] = "different retry"
            self.assertNotEqual(smoke._completed_replay_history(status), before)


    def test_explicit_no_audio_evidence_keeps_default_aac_requirement(self):
        for expect_audio, has_audio in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(expect_audio=expect_audio, has_audio=has_audio), TemporaryDirectory() as directory:
                output = Path(directory)
                (output / "editable").mkdir()
                for name in ("final_vi.mp4", "captions_vi.srt", "captions_vi.ass", "qc_report.json", "editable/timeline.json"):
                    (output / name).write_text("fixture only")
                (output / "job_manifest.json").write_text("{}")
                streams = [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180}]
                if has_audio: streams.append({"codec_type": "audio", "codec_name": "aac"})
                probe = {"format": {"duration": "18"}, "streams": streams}
                with mock.patch.object(smoke, "_run", return_value=SimpleNamespace(stdout=json.dumps(probe))):
                    if expect_audio == has_audio:
                        report = smoke._verify_output(output / "ffprobe", output, 18, expect_audio=expect_audio)
                        self.assertEqual(report["audio_codec"], "aac" if has_audio else None)
                    else:
                        with self.assertRaises(smoke.SmokeError):
                            smoke._verify_output(output / "ffprobe", output, 18, expect_audio=expect_audio)


if __name__ == "__main__":
    unittest.main()
