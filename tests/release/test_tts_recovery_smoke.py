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


if __name__ == "__main__":
    unittest.main()
