"""#766 (P2-N3): writing ``status.json`` never kills a job, never leaves broken JSON, never says COMPLETED wrongly.

rc7 Windows D1: with ``status.json`` held open by another reader, ``os.replace`` raised WinError 5 on the first try,
the failure handler's own status write failed the same way, and the engine exited with the file still RUNNING.

The Windows case (a second process holding the file open) runs only on Windows, where CI runs it with no skip.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

from engine.dubflow.product import job


def busy(times: int):
    """An ``os.replace`` refused ``times`` times as Windows refuses a file held open, then working."""
    real = os.replace
    calls = {"n": 0}

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= times:
            error = PermissionError(13, "Access is denied")
            error.winerror = 5
            raise error
        return real(src, dst)
    return replace, calls


class AtomicTextTests(unittest.TestCase):
    def test_a_busy_file_is_retried_with_backoff(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text('{"state": "RUNNING"}', encoding="utf-8")
            replace, calls = busy(3)
            pauses = []
            with mock.patch.object(job.os, "replace", replace):
                job._atomic_text(path, '{"state": "COMPLETED"}', attempts=6, sleep=pauses.append)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "COMPLETED")
            self.assertEqual(calls["n"], 4)
            self.assertEqual(len(pauses), 3)
            self.assertTrue(all(b > a for a, b in zip(pauses, pauses[1:])), pauses)  # growing pauses
            self.assertFalse((Path(tmp) / "status.json.partial").exists())

    def test_retries_are_bounded_and_the_old_file_stays_whole(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text('{"state": "RUNNING", "done": 2}', encoding="utf-8")
            replace, calls = busy(1000)
            pauses = []
            with mock.patch.object(job.os, "replace", replace), self.assertRaises(PermissionError):
                job._atomic_text(path, '{"state": "COMPLETED"}', attempts=5, sleep=pauses.append)
            self.assertEqual(calls["n"], 5)  # no endless wait
            self.assertLessEqual(sum(pauses), 5.0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"state": "RUNNING", "done": 2})
            self.assertFalse((Path(tmp) / "status.json.partial").exists())

    def test_a_full_disk_fails_at_once_and_leaves_the_old_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text('{"state": "RUNNING"}', encoding="utf-8")

            def full(*_args, **_kwargs):
                raise OSError(errno.ENOSPC, "No space left on device")
            pauses = []
            with mock.patch.object(job.os, "fsync", full), self.assertRaises(OSError):
                job._atomic_text(path, '{"state": "COMPLETED"}', sleep=pauses.append)
            self.assertEqual(pauses, [])  # not a busy file: no retry
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"state": "RUNNING"})
            self.assertFalse((Path(tmp) / "status.json.partial").exists())


class StatusTests(unittest.TestCase):
    def test_a_progress_line_that_cannot_be_written_does_not_end_the_job(self):
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 3)
            replace, _ = busy(1000)
            with mock.patch.object(job.os, "replace", replace), mock.patch.object(job.time, "sleep", lambda _s: None), \
                    mock.patch("sys.stderr") as stderr:
                self.assertFalse(status.write(done=1, message="Đang dựng đoạn 1/3…"))
            self.assertIn("status.json not written", "".join(c.args[0] for c in stderr.write.call_args_list))
            self.assertEqual(status.value["done"], 1)  # kept for the next write
            self.assertTrue(status.write(done=2))
            written = json.loads((Path(tmp) / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(written["done"], 2)
            self.assertEqual(written["status_write_failures"], 1)  # the warning is kept

    def test_the_final_state_is_tried_for_longer(self):
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 1)
            replace, calls = busy(job.REPLACE_ATTEMPTS + 2)  # more than a progress line waits for
            with mock.patch.object(job.os, "replace", replace), mock.patch.object(job.time, "sleep", lambda _s: None):
                self.assertTrue(status.write(state="COMPLETED", message="Đã tạo 1/1 video"))
            self.assertEqual(json.loads((Path(tmp) / "status.json").read_text(encoding="utf-8"))["state"], "COMPLETED")

    def test_a_failed_job_never_reads_completed(self):
        # the failure handler's own write fails too (rc7): the file keeps RUNNING, never COMPLETED, and the result
        # the engine returns (its last stdout line) says FAILED
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 1)
            replace, _ = busy(1000)
            with mock.patch.object(job.os, "replace", replace), mock.patch.object(job.time, "sleep", lambda _s: None), \
                    mock.patch("sys.stderr"):
                self.assertFalse(status.write(state="FAILED", message="Lỗi khi tạo video"))
            self.assertEqual(json.loads((Path(tmp) / "status.json").read_text(encoding="utf-8"))["state"], "RUNNING")
            self.assertEqual(status.value["state"], "FAILED")

    def test_cancel_during_a_blocked_write_stays_cancelled(self):
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 2)
            replace, _ = busy(job.REPLACE_ATTEMPTS)  # a progress line lost, then the file frees up
            with mock.patch.object(job.os, "replace", replace), mock.patch.object(job.time, "sleep", lambda _s: None), \
                    mock.patch("sys.stderr"):
                status.write(done=1)
                self.assertTrue(status.write(state="CANCELLED", message="Đã hủy."))
            written = json.loads((Path(tmp) / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(written["state"], "CANCELLED")

    def test_reopening_after_a_crash_mid_write_reads_whole_json(self):
        # a crash between writing the partial file and renaming it: the status file is the previous whole one
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 2)
            status.write(done=1)

            def crash(_src, _dst):
                raise KeyboardInterrupt()
            with mock.patch.object(job.os, "replace", crash), self.assertRaises(KeyboardInterrupt):
                job._atomic_text(status.path, json.dumps({"state": "RUNNING", "done": 2}))
            self.assertEqual(json.loads(status.path.read_text(encoding="utf-8"))["done"], 1)
            self.assertFalse((Path(tmp) / "status.json.partial").exists())


HOLDER = """
import sys, time
with open(sys.argv[1], "rb"):
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
"""


@unittest.skipUnless(sys.platform == "win32", "Windows file sharing: a file held open cannot be replaced")
class WindowsHeldFileTests(unittest.TestCase):
    def _hold(self, path: Path, seconds: float) -> subprocess.Popen:
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(seconds)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        return holder

    def test_a_reader_holding_the_file_briefly_is_waited_out(self):
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 1)
            holder = self._hold(status.path, 0.6)
            try:
                started = time.monotonic()
                self.assertTrue(status.write(state="COMPLETED", message="ok"))
                self.assertGreater(time.monotonic() - started, 0.05)  # it really was refused at first
            finally:
                holder.wait(10)
            self.assertEqual(json.loads(status.path.read_text(encoding="utf-8"))["state"], "COMPLETED")

    def test_a_reader_that_never_lets_go_does_not_kill_the_job(self):
        with TemporaryDirectory() as tmp:
            status = job.Status(Path(tmp) / "status.json", 1)
            holder = self._hold(status.path, 30)
            try:
                with mock.patch("sys.stderr"):
                    self.assertFalse(status.write(done=1))  # bounded, then given up, no exception
            finally:
                holder.kill()
                holder.wait(10)
            self.assertEqual(json.loads(status.path.read_text(encoding="utf-8"))["state"], "RUNNING")
            self.assertTrue(status.write(done=1))
            self.assertEqual(json.loads(status.path.read_text(encoding="utf-8"))["status_write_failures"], 1)


if __name__ == "__main__":
    unittest.main()
