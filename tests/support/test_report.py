"""The diagnostic report holds a job's reports and metadata, never its media."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
import zipfile

from engine.dubflow.support import report

REPO = Path(__file__).resolve().parents[2]


class ReportTests(unittest.TestCase):
    def job(self, root: Path) -> Path:
        output = root / "DubFlow Output" / "clip - vi" / "run-abc"
        files = {"job_manifest.json": b'{"job_id": "x"}', "qc_report.json": b"{}", "captions_vi.srt": b"1\n",
                 "final_vi.mp4": b"video" * 1000, "editable/final_mix.wav": b"audio" * 1000,
                 "editable/dubbing_spoken_text.json": b"{}", ".dubflow-work/checkpoint.json": b"{}",
                 ".dubflow-work/b2-audio/abc/def/tts_document.json": b"{}",
                 ".dubflow-work/b2-audio/abc/def/tts/tts-1.wav": b"speech"}
        for name, data in files.items():
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            (output / name).write_bytes(data)
        os.link(output / "editable/final_mix.wav", output / ".dubflow-work/b2-audio/abc/def/final.wav")
        return output

    def test_report_holds_reports_sizes_and_status_but_no_media(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            status = root / "status.json"
            status.write_text('{"state": "FAILED"}', encoding="utf-8")
            path = report.create_report(output_dir=output, job_status=status, database=root / "missing.sqlite3",
                                        destination=root / "desktop", now=0)
            self.assertEqual(path.parent, root / "desktop")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                summary = json.loads(archive.read("summary.json"))
                sizes = json.loads(archive.read("file-sizes.json"))
            self.assertTrue({"job/job_manifest.json", "job/qc_report.json", "job/captions_vi.srt", "job/.dubflow-work/checkpoint.json",
                             "job/editable/dubbing_spoken_text.json", "job/b2-audio/abc/def/tts_document.json", "job-status.json"} <= names)
            self.assertFalse(any(name.endswith((".mp4", ".wav")) for name in names), names)
            self.assertNotIn("jobs.sqlite3", names)
            # The hard-linked mix counts once in the real disk use.
            self.assertEqual(summary["disk"]["listed_bytes"] - summary["disk"]["unique_bytes"], 5000)
            self.assertEqual(len(sizes), 10)
            self.assertIn("machine", summary)
            self.assertFalse(list((root / "desktop").glob("*.partial")))

    def test_command_line_reports_the_zip_path_as_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            result = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.support.report", "--output-dir", str(output),
                                     "--destination", str(root / "out")], capture_output=True, text=True, cwd=REPO, timeout=120)
            value = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(value["ok"], result.stderr)
            self.assertTrue(Path(value["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
