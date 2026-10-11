"""Qualifier failure guards; actual DPAPI/native execution is Windows Release."""
import importlib.util
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/source_session_smoke.py"
spec = importlib.util.spec_from_file_location("source_session_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SourceSessionSmokeTests(unittest.TestCase):
    def test_plaintext_audit_requires_native_close_and_does_not_run_after_failed_exit(self):
        # Orchestration fixture stops before any factory/crypto qualification.
        # Actual Windows release still exercises the real owner and DPAPI.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps({"source_sha": "b" * 40}).encode()
            (root / "release-manifest.json").write_bytes(raw)
            for exit_failed in (False, True):
                with patch.object(smoke, "_require_windows"), patch.object(smoke, "_Native") as native, patch.object(smoke, "_plaintext_absent") as audit:
                    def after_exit(*args):
                        native.return_value.close.assert_called_once_with()
                        raise RuntimeError("stop_after_checked_exit")
                    audit.side_effect = after_exit
                    if exit_failed:
                        native.return_value.close.side_effect = ValueError("checked_exit_failed")
                        with self.assertRaisesRegex(ValueError, "checked_exit_failed"):
                            smoke.qualify_source_sessions(root, hashlib.sha256(raw).hexdigest(), "b" * 40)
                        audit.assert_not_called()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "stop_after_checked_exit"):
                            smoke.qualify_source_sessions(root, hashlib.sha256(raw).hexdigest(), "b" * 40)
                        audit.assert_called_once()

    def test_plaintext_scan_includes_released_owner_metadata_and_partial_cookie_value(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            control = data / "control"
            control.mkdir()
            lock = control / "source-owner.lock"
            lock.write_text('{"pid":123,"owner":"opaque"}', encoding="utf-8")
            smoke._plaintext_absent(data, "qa=sensitive-marker")
            lock.write_text('{"unexpected":"sensitive-marker"}', encoding="utf-8")
            with self.assertRaises(ValueError) as refused:
                smoke._plaintext_absent(data, "qa=sensitive-marker")
            self.assertNotIn("sensitive-marker", str(refused.exception))
            lock.write_bytes(b"x")
            oversized = control / "oversized"
            with oversized.open("wb") as stream:
                stream.truncate(4 * 1024 * 1024 + 1)
            with self.assertRaises(ValueError):
                smoke._plaintext_absent(data, "qa=sensitive-marker")

    def test_partial_malformed_oversized_and_secret_events_are_refused_without_echo(self):
        secret = "qa_sid=sensitive-fixture-value"
        for raw in (b"", b"{}", b"[]\n", b"{invalid}\n", b"{}\n",
                    b"x" * (smoke.MAX_LINE + 1) + b"\n",
                    b'{"event":"source_session","unexpected":"sensitive-fixture-value"}\n',
                    b'{"event":"source_session","headers":{}}\n',
                    b'{"event":"source_session","ciphertext":"opaque"}\n'):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError) as refused:
                smoke._event(raw, secret)
            self.assertNotIn("sensitive-fixture-value", str(refused.exception))

    def test_event_parser_only_accepts_complete_nonsecret_native_frame(self):
        value = {"event": "source_session", "provider_id": "douyin", "operation": "status", "state": "expired_or_unavailable"}
        self.assertEqual(smoke._event(json.dumps(value).encode() + b"\n", "qa=private"), value)

    def test_non_windows_cannot_claim_native_session_qualification(self):
        with patch.object(smoke.os, "name", "posix"), self.assertRaisesRegex(ValueError, "requires Windows"):
            smoke.qualify_source_sessions(Path("unavailable"), "a" * 64, "b" * 40)

    def test_wrong_native_completion_or_provider_state_cannot_be_passed(self):
        native = smoke._Native.__new__(smoke._Native)
        native.send = lambda request: None
        preparing = {"event": "source_session_preparing", "provider_id": "bilibili", "operation": "save"}
        for event in ({"event": "source_session_error", "code": "AUTH_REQUIRED"},
                      {"event": "source_session", "provider_id": "douyin", "operation": "save", "state": "ready"},
                      {"event": "source_session", "provider_id": "bilibili", "operation": "save", "state": "missing"}):
            responses = iter((preparing, event))
            native.receive = lambda: next(responses)
            with self.subTest(event=event), self.assertRaises(ValueError):
                native.operation("bilibili", "save", "ready")


if __name__ == "__main__":
    unittest.main()
