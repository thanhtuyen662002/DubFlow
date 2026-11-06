"""Unexpected source-worker failures: network errors stay retryable, tracebacks reach the error report."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error

from engine.dubflow.download.enumeration import worker
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class FailureCodeTests(unittest.TestCase):
    def test_network_errors_are_retryable_for_page_and_download_commands(self):
        for error in (ConnectionResetError(), TimeoutError(), socket.timeout(), urllib.error.URLError("down")):
            for command in ("source_prepare", "source_page", "source_download"):
                self.assertEqual(worker.failure_code(error, command), ("NETWORK", True))

    def test_session_operations_and_other_errors_stay_worker_failures(self):
        self.assertEqual(worker.failure_code(ConnectionResetError(), "source_session"), ("SOURCE_WORKER_FAILED", False))
        self.assertEqual(worker.failure_code(KeyError("x"), "source_download"), ("SOURCE_WORKER_FAILED", False))

    def test_typed_source_errors_pass_through(self):
        error = SourceError(SourceErrorCode.NETWORK, "offline")
        self.assertEqual(worker.failure_code(error, "source_download"), (SourceErrorCode.NETWORK.value, error.retryable))


class ErrorLogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="dubflow-worker-log-")
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.log_dir = self.data / "source-work"
        (self.log_dir / "materialization" / "job").mkdir(parents=True)

    def test_log_folder_is_found_from_work_root_or_data_root(self):
        self.assertEqual(worker.error_log_dir({"work_root": str(self.log_dir / "materialization" / "job")}), self.log_dir)
        self.assertEqual(worker.error_log_dir({"data_root": str(self.data)}), self.log_dir)

    def test_failure_appends_traceback_and_keeps_the_log_bounded(self):
        try:
            {}["missing"]
        except KeyError as error:
            path = worker.log_failure(error, "source_download", self.log_dir, scan_id="link-a", now=0)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("--- 1970-01-01T00:00:00Z source_download KeyError"))
        self.assertIn("Traceback (most recent call last)", text)
        big = RuntimeError("x" * 4096)
        for _ in range(120):
            worker.log_failure(big, "source_page", self.log_dir, scan_id="link-a")
        text = path.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.encode("utf-8")), worker.ERROR_LOG_LIMIT)
        self.assertTrue(text.startswith("--- "))
        self.assertEqual(path, self.log_dir / "worker-errors" / "link-a.log")
        self.assertEqual(list(path.parent.glob("*.partial")), [])

    def test_typed_source_error_logs_its_code_cause_and_no_signed_query(self):
        try:
            try:
                raise ConnectionResetError("reset by https://upos-sz.bilivideo.com/a/b.m4s?deadline=1&upsig=SIGVALUE")
            except ConnectionResetError as cause:
                raise SourceError(SourceErrorCode.NETWORK, "Bilibili media download failed", retryable=True) from cause
        except SourceError as error:
            path = worker.log_failure(error, "source_download", self.log_dir, scan_id="link-a", now=0)
        text = path.read_text(encoding="utf-8")
        self.assertIn("source_download SourceError: NETWORK: Bilibili media download failed", text)
        self.assertIn("ConnectionResetError: reset by https://upos-sz.bilivideo.com/a/b.m4s?<query>", text)
        self.assertNotIn("SIGVALUE", text)

    def test_each_scan_has_its_own_log(self):
        worker.log_failure(RuntimeError("only A https://a.example/v?x=1"), "source_download", self.log_dir, scan_id="link-a")
        worker.log_failure(RuntimeError("only B"), "source_download", self.log_dir, scan_id="link-b")
        logs = self.log_dir / "worker-errors"
        self.assertNotIn("only B", (logs / "link-a.log").read_text(encoding="utf-8"))
        self.assertNotIn("only A", (logs / "link-b.log").read_text(encoding="utf-8"))
        # An id that is not a plain file name never escapes the folder.
        path = worker.log_failure(RuntimeError("odd"), None, self.log_dir, scan_id="../x")
        self.assertEqual(path.parent, logs)

    def test_credential_lines_never_reach_the_log(self):
        try:
            try:
                raise RuntimeError("request failed\nCookie: SESSDATA=COOKIEVALUE; bili_jct=JCTVALUE")
            except RuntimeError as cause:
                error = SourceError(SourceErrorCode.NETWORK, "Bilibili transport failed", retryable=True)
                error.add_note("Authorization: Bearer BEARERVALUE")
                raise error from cause
        except SourceError as error:
            path = worker.log_failure(error, "source_download", self.log_dir, scan_id="link-a", now=0)
        text = path.read_text(encoding="utf-8")
        for secret in ("COOKIEVALUE", "JCTVALUE", "BEARERVALUE"):
            self.assertNotIn(secret, text)
        self.assertIn("<line mentions credentials>", text)
        self.assertIn("Bilibili transport failed", text)

    def _link(self, link: Path, target: Path, *, folder: bool) -> None:
        try:
            link.symlink_to(target, target_is_directory=folder)
        except OSError:
            if not folder or os.name != "nt":
                self.skipTest("symlinks unavailable")
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)

    def test_a_linked_source_work_root_is_never_used(self):
        outside = self.data / "outside"
        outside.mkdir()
        linked = self.data / "linked-data"
        linked.mkdir()
        self._link(linked / "source-work", outside, folder=True)
        self.assertIsNone(worker.error_log_dir({"data_root": str(linked)}))
        self.assertIsNone(worker.error_log_dir({"work_root": str(linked / "source-work" / "materialization" / "job")}))
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, linked / "source-work", scan_id="link-a"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_linked_log_file_is_not_followed(self):
        outside = self.data / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        logs = self.log_dir / "worker-errors"
        logs.mkdir()
        self._link(logs / "link-a.log", outside, folder=False)
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, self.log_dir, scan_id="link-a"))
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
        # A pre-created alias with a predictable temporary name is not used either.
        self._link(logs / f"link-b.log.{os.getpid()}.partial", outside, folder=False)
        path = worker.log_failure(RuntimeError("for b"), None, self.log_dir, scan_id="link-b")
        self.assertIn("for b", path.read_text(encoding="utf-8"))
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_concurrent_writes_for_one_scan_leave_a_whole_log(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda index: worker.log_failure(RuntimeError(f"entry {index}"), None, self.log_dir,
                                                                    scan_id="link-a"), range(16)))
        path = self.log_dir / "worker-errors" / "link-a.log"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("--- ") and "entry " in text)
        self.assertEqual(list(path.parent.glob("*.partial")), [])
        self.assertTrue(all(item in (None, path) for item in paths))

    def test_old_scan_logs_are_pruned(self):
        logs = self.log_dir / "worker-errors"
        logs.mkdir()
        for index in range(worker.ERROR_LOG_KEEP + 5):
            stale = logs / f"old-{index}.log"
            stale.write_text("x", encoding="utf-8")
            os.utime(stale, (index, index))
        worker.log_failure(RuntimeError("new"), None, self.log_dir, scan_id="link-new")
        self.assertEqual(len(list(logs.glob("*.log"))), worker.ERROR_LOG_KEEP)
        self.assertTrue((logs / "link-new.log").exists() and not (logs / "old-0.log").exists())

    def test_linked_log_folder_is_not_written(self):
        outside = self.data / "outside"
        outside.mkdir()
        try:
            (self.log_dir / "worker-errors").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, self.log_dir, scan_id="link-a"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_folder_or_scan_logs_nothing(self):
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, None, scan_id="link-a"))
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, self.log_dir, scan_id=None))


if __name__ == "__main__":
    unittest.main()
