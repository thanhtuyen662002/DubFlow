from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.download.generic import SubprocessYtDlpRunner, YtDlpTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class NativeExtractorBoundaryTests(unittest.TestCase):
    def test_private_stdin_reaches_child_without_credentials_in_argv(self):
        runner = SubprocessYtDlpRunner()
        argv = [sys.executable, "-c", "import sys,json;data=json.loads(sys.stdin.buffer.read());sys.stdout.write(json.dumps({'length':len(data['cookie'])}))"]
        code, stdout, stderr = runner.run_with_input(argv, timeout_s=10, stdin_bytes=b'{"cookie":"synthetic-secret"}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"length": 16})
        self.assertNotIn("synthetic-secret", " ".join(argv))
        self.assertEqual(stderr, "")

    def test_actual_process_reads_utf8_metadata_and_preserves_exit_code(self):
        runner = SubprocessYtDlpRunner()
        argv = [sys.executable, "-c", "import sys;sys.stdout.buffer.write('{\"title\":\"Giọng Việt\"}'.encode('utf-8'));sys.stderr.buffer.write(b'error-tail\\xff');sys.exit(7)"]
        code, stdout, stderr = runner.run(argv, timeout_s=10)
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(stdout), {"title": "Giọng Việt"})
        self.assertIn("error-tail", stderr)
        self.assertIn("\ufffd", stderr)

    def _tracked_run(self, source, timeout_s, private_input=None):
        processes = []
        handles = []
        actual_popen = subprocess.Popen

        def start(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            processes.append(process)
            handles.extend((kwargs["stdout"], kwargs["stderr"]))
            return process

        with patch("engine.dubflow.download.generic.adapter.subprocess.Popen", side_effect=start):
            with self.assertRaises(SourceError) as context:
                runner = SubprocessYtDlpRunner()
                if private_input is None:
                    runner.run([sys.executable, "-c", source], timeout_s=timeout_s)
                else:
                    runner.run_with_input([sys.executable, "-c", source], timeout_s=timeout_s, stdin_bytes=private_input)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll(), "native process must be reaped")
        self.assertTrue(all(handle.closed for handle in handles))
        if private_input is not None:
            self.assertTrue(processes[0].stdin.closed)
        return context.exception

    def test_private_request_to_child_that_never_reads_stdin_is_deadline_bounded(self):
        error = self._tracked_run("import time;time.sleep(30)", 0.2, b"x" * 4096)
        self.assertEqual(error.code, SourceErrorCode.NETWORK)

    def test_actual_timeout_reaps_the_running_process(self):
        error = self._tracked_run("import time;time.sleep(30)", 0.2)
        self.assertEqual(error.code, SourceErrorCode.NETWORK)
        self.assertIn("timed out", error.condition)

    def test_actual_metadata_flood_is_rejected_and_process_reaped(self):
        error = self._tracked_run("import sys,time;sys.stdout.buffer.write(b'x'*(5*1024*1024));sys.stdout.flush();time.sleep(30)", 10)
        self.assertEqual(error.code, SourceErrorCode.SOURCE_CHANGED)

    def test_actual_diagnostic_flood_is_rejected_without_echoing_private_data(self):
        error = self._tracked_run("import sys,time;sys.stderr.buffer.write(b'private-cookie-'*6000);sys.stderr.flush();time.sleep(30)", 10)
        self.assertEqual(error.code, SourceErrorCode.SOURCE_CHANGED)
        self.assertNotIn("private", str(error))

    def test_extractor_pins_are_required_and_checked_again_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "extractor.exe"
            executable.write_bytes(b"version-one")
            pin = hashlib.sha256(b"version-one").hexdigest()
            runner = Mock()
            runner.run.return_value = (0, '{"id":"x"}', "")
            for options in ({}, {"trusted_root": root}, {"trusted_root": root, "expected_sha256": "0" * 64}):
                with self.subTest(options=options), self.assertRaises(SourceError):
                    YtDlpTransport(executable, runner=runner, **options)
            sibling = root / "other-runtime"
            sibling.mkdir()
            with self.assertRaises(SourceError):
                YtDlpTransport(executable, trusted_root=sibling, expected_sha256=pin, runner=runner)
            transport = YtDlpTransport(executable, trusted_root=root, expected_sha256=pin, runner=runner)
            executable.write_bytes(b"version-two")
            with self.assertRaises(SourceError) as context:
                transport.inspect_url("https://example.test/video")
            self.assertEqual(context.exception.code, SourceErrorCode.UNSUPPORTED)
            runner.run.assert_not_called()

    def test_injected_unbounded_metadata_is_rejected_before_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "extractor.exe"
            executable.write_bytes(b"pinned")
            runner = Mock()
            runner.run.return_value = (0, "x" * (4 * 1024 * 1024 + 1), "")
            transport = YtDlpTransport(executable, trusted_root=root,
                expected_sha256=hashlib.sha256(b"pinned").hexdigest(), runner=runner)
            with self.assertRaises(SourceError) as context:
                transport.inspect_url("https://example.test/video")
            self.assertEqual(context.exception.code, SourceErrorCode.SOURCE_CHANGED)


if __name__ == "__main__":
    unittest.main()
