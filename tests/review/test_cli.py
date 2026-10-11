from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review import read_review_snapshot
from schema_assertions import assert_schema
import test_export as export_helpers

def sha(path):
    return export_helpers.sha(Path(path))

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "engine/dubflow/review/cli.py"


class ReviewCliTests(unittest.TestCase):
    def command(self, path, action="snapshot", *extra, isolated=True):
        flags = ["-I", "-S", "-B"] if isolated else ["-B"]
        return subprocess.run([sys.executable, *flags, str(CLI), action, "--manifest", str(path),
                               "--job-id", "job-review", "--manifest-sha256", sha(path), *extra],
                              capture_output=True, check=False, timeout=30)

    def test_owned_runtime_reads_actual_snapshot_and_pcm_preview_without_writes(self):
        with TemporaryDirectory(prefix="dubflow-review-cli-") as directory:
            path, manifest = export_helpers.ExportReviewTests().fixture(directory, tts=True)
            before = {role: sha(item["path"]) for role, item in manifest["artifacts"].items()}
            snapshot = self.command(path)
            self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
            self.assertEqual(snapshot.stderr, b"")
            payload = json.loads(snapshot.stdout)
            assert_schema(payload, "schema-v2.json")
            self.assertEqual(read_review_snapshot(payload).job_id, "job-review")
            preview = self.command(path, "preview", "--cue-id", "cue-a")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            value = json.loads(preview.stdout)
            assert_schema(value, "preview-v1.json")
            self.assertEqual(value["content_hash"], sha(value["path"]))
            finding = next(item for item in payload["findings"] if item["cue_id"] == "cue-a" and item["output_preview"])
            audio = self.command(path, "audio-v1", "--cue-id", "cue-a", "--finding-id", finding["finding_id"], "--revision", "0")
            self.assertEqual(audio.returncode, 0, audio.stderr)
            self.assertEqual(audio.stdout, Path(value["path"]).read_bytes())
            self.assertEqual(audio.stderr, b"")
            self.assertEqual(before, {role: sha(item["path"]) for role, item in manifest["artifacts"].items()})

    def test_binary_audio_refuses_stale_or_wrong_binding_and_corrupt_bytes(self):
        with TemporaryDirectory(prefix="dubflow-review-cli-audio-") as directory:
            path, _ = export_helpers.ExportReviewTests().fixture(directory, tts=True)
            snapshot = json.loads(self.command(path).stdout)
            finding = next(item for item in snapshot["findings"] if item["cue_id"] == "cue-a" and item["output_preview"])
            for cue, identifier, revision in (("other-cue", finding["finding_id"], "0"),
                                              ("cue-a", "other-finding", "0"),
                                              ("cue-a", finding["finding_id"], "1"),
                                              ("cue-a", finding["finding_id"], "-1")):
                result = self.command(path, "audio-v1", "--cue-id", cue, "--finding-id", identifier, "--revision", revision)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
            preview = Path(finding["output_preview"])
            raw = bytearray(preview.read_bytes()); raw[-1] ^= 1; preview.write_bytes(raw)
            result = self.command(path, "audio-v1", "--cue-id", "cue-a", "--finding-id", finding["finding_id"], "--revision", "0")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")

    def test_failed_pin_and_unknown_or_corrupt_preview_never_return_success(self):
        with TemporaryDirectory(prefix="dubflow-review-cli-refuse-") as directory:
            path, manifest = export_helpers.ExportReviewTests().fixture(directory, tts=True)
            unknown = self.command(path, "preview", "--cue-id", "other-cue")
            self.assertEqual(unknown.returncode, 2)
            self.assertEqual(unknown.stdout, b"")
            self.assertEqual(json.loads(unknown.stderr)["code"], "REVIEW_INPUT_INVALID")
            document = json.loads(Path(manifest["artifacts"]["tts_document"]["path"]).read_text(encoding="utf-8"))
            Path(document["artifacts"][0]["path"]).write_bytes(b"corrupt")
            corrupt = self.command(path, "preview", "--cue-id", "cue-a")
            self.assertEqual(corrupt.returncode, 2)
            self.assertEqual(corrupt.stdout, b"")
            pinned = subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI), "snapshot", "--manifest", str(path),
                                    "--job-id", "job-review", "--manifest-sha256", "sha256:" + "0" * 64],
                                   capture_output=True, check=False, timeout=30)
            self.assertEqual(pinned.returncode, 2)
            self.assertEqual(pinned.stdout, b"")

    def test_unsafe_runtime_and_action_mismatch_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-cli-runtime-") as directory:
            path, _ = export_helpers.ExportReviewTests().fixture(directory)
            for result in (self.command(path, isolated=False), self.command(path, "preview"),
                           self.command(path, "snapshot", "--cue-id", "cue-a"),
                           self.command(path, "audio-v1", "--cue-id", "cue-a"),
                           self.command(path, "preview", "--cue-id", "cue-a", "--revision", "0"),
                           self.command(path, "snapshot", "--finding-id", "cue-a")):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(json.loads(result.stderr)["code"], "REVIEW_INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
