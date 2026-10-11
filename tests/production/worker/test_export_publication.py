from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.worker import export_publication as publication


class ExportPublicationTests(unittest.TestCase):
    def populate(self, output, prefix):
        for name in publication.ENTRIES:
            path = output / name
            if name == "editable":
                path.mkdir(exist_ok=True)
                path /= "timeline.json"
            path.write_bytes((prefix + name).encode())

    def snapshot(self, output):
        return {name: ((output / name / "timeline.json") if name == "editable" else output / name).read_bytes() for name in publication.ENTRIES}

    def test_mid_publication_io_failure_restores_every_previous_artifact(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            self.populate(output, "previous-")
            old = self.snapshot(output)
            pending = publication.prepare(output)
            self.populate(pending, "candidate-")
            new = self.snapshot(pending)
            replace = os.replace
            failed = False

            def interrupt(source, destination):
                nonlocal failed
                if Path(source) == pending / "captions_vi.ass" and not failed:
                    failed = True
                    raise OSError("disk pressure during publication")
                return replace(source, destination)

            with patch.object(publication.os, "replace", side_effect=interrupt), self.assertRaisesRegex(OSError, "disk pressure"):
                publication.publish(output)
            self.assertEqual(self.snapshot(output), old)
            self.assertEqual(self.snapshot(pending), new)
            publication.publish(output)
            self.assertEqual(self.snapshot(output), new)
            self.assertFalse(pending.exists())

    def test_process_death_at_each_move_rolls_back_and_can_resume(self):
        script = """
import os, sys
from pathlib import Path
from engine.dubflow.worker import export_publication as p
original, count = os.replace, 0
def interrupt(source, destination):
    global count
    original(source, destination)
    if Path(destination).name != 'publication.json':
        count += 1
        if count == int(sys.argv[2]):
            os._exit(23)
p.os.replace = interrupt
p.publish(Path(sys.argv[1]))
"""
        for point in range(1, 13):
            with self.subTest(point=point), TemporaryDirectory() as directory:
                output = Path(directory)
                self.populate(output, "previous-")
                old = self.snapshot(output)
                pending = publication.prepare(output)
                self.populate(pending, "candidate-")
                new = self.snapshot(pending)
                child = subprocess.run([sys.executable, "-c", script, str(output), str(point)], capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 23, child.stderr.decode(errors="replace"))
                publication.recover(output)
                publication.recover(output)
                self.assertEqual(self.snapshot(output), old)
                self.assertEqual(self.snapshot(pending), new)
                publication.publish(output)
                self.assertEqual(self.snapshot(output), new)

    def test_death_after_commit_keeps_new_export_and_finishes_cleanup(self):
        script = """
import os, sys
from pathlib import Path
from engine.dubflow.worker import export_publication as p
original = p.write_journal
def interrupt(path, value):
    original(path, value)
    if value['state'] == 'committed':
        os._exit(24)
p.write_journal = interrupt
p.publish(Path(sys.argv[1]))
"""
        with TemporaryDirectory() as directory:
            output = Path(directory)
            self.populate(output, "previous-")
            pending = publication.prepare(output)
            self.populate(pending, "candidate-")
            new = self.snapshot(pending)
            child = subprocess.run([sys.executable, "-c", script, str(output)], capture_output=True, timeout=15)
            self.assertEqual(child.returncode, 24, child.stderr.decode(errors="replace"))
            publication.recover(output)
            publication.recover(output)
            self.assertEqual(self.snapshot(output), new)
            self.assertFalse(pending.exists())
            self.assertFalse((output / ".dubflow-work/export-previous").exists())

    def test_unregistered_backup_is_preserved_for_diagnostics(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            backup = output / ".dubflow-work/export-previous"
            backup.mkdir(parents=True)
            (backup / "precious.txt").write_bytes(b"keep")
            with self.assertRaisesRegex(publication.PublicationError, "unregistered"):
                publication.prepare(output)
            self.assertEqual((backup / "precious.txt").read_bytes(), b"keep")

    def test_first_export_interruption_recovers_without_inventing_previous_files(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            pending = publication.prepare(output)
            self.populate(pending, "candidate-")
            new = self.snapshot(pending)
            items = [{"name": name, "had_previous": False} for name in publication.ENTRIES]
            publication.write_journal(output / ".dubflow-work/publication.json", {"schema_version": 1, "state": "publishing", "items": items})
            os.replace(pending / "final_vi.mp4", output / "final_vi.mp4")
            os.replace(pending / "editable", output / "editable")
            publication.recover(output)
            self.assertEqual(self.snapshot(pending), new)
            self.assertFalse((output / "final_vi.mp4").exists())
            publication.publish(output)
            self.assertEqual(self.snapshot(output), new)

    def test_invalid_journal_cannot_select_paths_outside_export_storage(self):
        for invalid in ({"name": "../source", "had_previous": False}, {"name": {}, "had_previous": False}, {"name": "editable", "had_previous": 1}):
            with self.subTest(invalid=invalid), TemporaryDirectory() as directory:
                output = Path(directory)
                publication.prepare(output)
                journal = output / ".dubflow-work/publication.json"
                journal.write_text(json.dumps({"schema_version": 1, "state": "publishing", "items": [invalid]}))
                with self.assertRaises(publication.PublicationError):
                    publication.recover(output)
                self.assertTrue(journal.exists())

    def test_linked_pending_file_is_rejected_before_writing(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            source = Path(directory) / "source"
            source.write_bytes(b"keep")
            pending = publication.prepare(output)
            try:
                (pending / "captions_vi.srt").symlink_to(source)
            except OSError:
                self.skipTest("symlink creation unavailable on this host")
            with self.assertRaises(publication.PublicationError):
                publication.prepare(output)
            self.assertEqual(source.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
