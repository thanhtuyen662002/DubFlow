"""Unexpected source-worker failures: network errors stay retryable, tracebacks reach the error report."""
from __future__ import annotations

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
            path = worker.log_failure(error, "source_download", self.log_dir, now=0)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("--- 1970-01-01T00:00:00Z source_download KeyError"))
        self.assertIn("Traceback (most recent call last)", text)
        big = RuntimeError("x" * 4096)
        for _ in range(120):
            worker.log_failure(big, "source_page", self.log_dir)
        text = path.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.encode("utf-8")), worker.ERROR_LOG_LIMIT)
        self.assertTrue(text.startswith("--- "))
        self.assertFalse(path.with_name(path.name + ".partial").exists())

    def test_missing_folder_logs_nothing(self):
        self.assertIsNone(worker.log_failure(RuntimeError("x"), None, None))


if __name__ == "__main__":
    unittest.main()
