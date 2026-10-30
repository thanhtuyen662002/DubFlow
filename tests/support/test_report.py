"""The diagnostic report holds one job's reports and metadata: no media, other jobs or local paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
import zipfile

from engine.dubflow.support import report

REPO = Path(__file__).resolve().parents[2]


def _link_folder(link: Path, target: Path) -> None:
    """A folder link: a symlink where allowed, else a Windows junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError) as error:
        raise unittest.SkipTest(f"folder links are unavailable: {error}")


def _archive_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return b"".join(archive.read(name) for name in archive.namelist())


class ReportTests(unittest.TestCase):
    def job(self, root: Path) -> Path:
        output = root / "DubFlow Output" / "clip - vi" / "run-abc"
        source = str(root / "Private Videos" / "clip.mp4")
        files = {"job_manifest.json": json.dumps({"job_id": "x", "source": {"path": source}, "output_dir": str(output)}).encode(),
                 "qc_report.json": b"{}", "captions_vi.srt": b"1\n",
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
            path = report.create_report(output_dir=output, job_status=status, destination=root / "desktop", now=0)
            self.assertEqual(path.parent, root / "desktop")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                summary = json.loads(archive.read("summary.json"))
                sizes = json.loads(archive.read("file-sizes.json"))
            self.assertTrue({"job/job_manifest.json", "job/qc_report.json", "job/captions_vi.srt", "job/.dubflow-work/checkpoint.json",
                             "job/editable/dubbing_spoken_text.json", "job/b2-audio/abc/def/tts_document.json", "job-status.json"} <= names, names)
            self.assertFalse(any(name.endswith((".mp4", ".wav")) for name in names), names)
            # The hard-linked mix counts once in the real disk use.
            self.assertEqual(summary["disk"]["listed_bytes"] - summary["disk"]["unique_bytes"], 5000)
            self.assertEqual(len(sizes), 10)
            self.assertIn("machine", summary)
            self.assertFalse(list((root / "desktop").glob("*.partial")))

    def test_no_job_database_or_other_job_state_is_archived(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            for database in (root / "control" / "jobs.sqlite3", output / ".dubflow-work" / "jobs.sqlite3"):
                database.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(database) as connection:
                    connection.execute("CREATE TABLE jobs (job_id TEXT, source_uri TEXT)")
                    connection.execute("INSERT INTO jobs VALUES ('other-job', 'D:/Secret/other.mp4')")
                connection.close()
            path = report.create_report(output_dir=output, job_status=None, destination=root / "desktop", now=0)
            with zipfile.ZipFile(path) as archive:
                self.assertFalse([name for name in archive.namelist() if name.endswith(".sqlite3")])
            self.assertNotIn(b"other-job", _archive_bytes(path))
            result = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.support.report", "--database", str(root / "control" / "jobs.sqlite3")],
                                    capture_output=True, text=True, cwd=REPO, timeout=120)
            self.assertNotEqual(result.returncode, 0, "the job database is no longer an input")

    def test_no_absolute_local_path_reaches_the_archive(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            status = root / "status.json"
            status.write_text(json.dumps({"state": "FAILED", "message": f"ASR_FAILED: [Errno 2] No such file or directory: '{output / 'x.wav'}'"}), encoding="utf-8")
            path = report.create_report(output_dir=output, job_status=status, destination=root / "desktop", now=0)
            archived = _archive_bytes(path)
            for local in {str(root), str(root.resolve()), str(output), str(output.resolve())}:
                for form in (local, json.dumps(local)[1:-1], local.replace("\\", "/")):
                    self.assertNotIn(form.encode(), archived)
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("job/job_manifest.json"))
                summary = json.loads(archive.read("summary.json"))
            self.assertEqual(manifest["source"]["path"], "<path>/clip.mp4")
            self.assertNotIn("output_dir", summary)

    def test_files_behind_a_linked_folder_are_not_read(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            outside = root / "outside"
            (outside / "secret").mkdir(parents=True)
            (outside / "timeline.json").write_text('{"secret": "OUTSIDE-TIMELINE"}', encoding="utf-8")
            (outside / "secret" / "tts_document.json").write_text('{"secret": "OUTSIDE-TTS"}', encoding="utf-8")
            _link_folder(output / ".dubflow-work" / "b2-audio" / "linked", outside / "secret")
            for name in os.listdir(output / "editable"):
                (output / "editable" / name).unlink()
            (output / "editable").rmdir()
            _link_folder(output / "editable", outside)
            path = report.create_report(output_dir=output, job_status=None, destination=root / "desktop", now=0)
            archived = _archive_bytes(path)
            self.assertNotIn(b"OUTSIDE-TIMELINE", archived)
            self.assertNotIn(b"OUTSIDE-TTS", archived)
            with zipfile.ZipFile(path) as archive:
                listed = [row["path"] for row in json.loads(archive.read("file-sizes.json"))]
                self.assertIn("job/job_manifest.json", archive.namelist())
            self.assertFalse([item for item in listed if item.startswith(("editable/", ".dubflow-work/b2-audio/linked/"))], listed)

    def test_a_linked_output_folder_is_not_read(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            link = root / "DubFlow Output" / "link - vi"
            _link_folder(link, output)
            path = report.create_report(output_dir=link, job_status=None, destination=root / "desktop", now=0)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(sorted(archive.namelist()), ["summary.json"])

    def test_command_line_reports_the_zip_path_as_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.job(root)
            result = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.support.report", "--output-dir", str(output),
                                     "--destination", str(root / "out")], capture_output=True, text=True, cwd=REPO, timeout=120)
            value = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(value["ok"], result.stderr)
            self.assertTrue(Path(value["path"]).is_file())


class RedactTests(unittest.TestCase):
    def test_local_paths_keep_only_their_last_component(self):
        cases = {
            json.dumps({"path": "C:\\Users\\Vo Thanh Tuyen\\Videos\\clip 1.mp4"}): '{"path": "<path>/clip 1.mp4"}',
            "D:\\Tools\\DubFlow\\DubFlow Output\\x - vi\\run-1": "<path>/run-1",
            "C:/Users/a/b.json": "<path>/b.json",
            "\\\\server\\share\\dir\\f.txt": "<path>/f.txt",
            "/Users/me/x.txt": "<path>/x.txt",
            "lỗi: 'E:\\a b\\c.wav'": "lỗi: '<path>/c.wav'",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(report.redact(text), expected)

    def test_other_text_is_unchanged(self):
        for text in ("http://example.com/a/b", "tỉ lệ 1:2, lúc 10:30", "editable/timeline.json", "Đã xuất video"):
            with self.subTest(text=text):
                self.assertEqual(report.redact(text), text)


if __name__ == "__main__":
    unittest.main()
