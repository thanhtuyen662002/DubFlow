from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest import mock

from engine.dubflow.security import verified_json
from engine.dubflow.security.verified_json import VerifiedJsonError, read_verified_json


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class ObservedStream(io.BytesIO):
    """Detect unbounded reads and over-consumption independently of the reader."""

    def __init__(self, payload: bytes, limit: int, *, fail_after: int | None = None, on_eof=None) -> None:
        super().__init__(payload)
        self.limit = limit
        self.requests: list[int] = []
        self.consumed = 0
        self.fail_after = fail_after
        self.on_eof = on_eof

    def read(self, size: int = -1) -> bytes:
        if size <= 0 or size > 64 * 1024 or size > self.limit + 1 - self.consumed:
            raise AssertionError(f"unbounded or over-budget read: {size}")
        self.requests.append(size)
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            raise OSError("injected interrupted read")
        chunk = super().read(size)
        self.consumed += len(chunk)
        if not chunk and self.on_eof is not None:
            callback, self.on_eof = self.on_eof, None
            callback()
        return chunk


class VerifiedJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "translation.json"
        self.checkpoint = self.root / "checkpoint.json"
        self.checkpoint.write_bytes(b'{"untouched":true}\n')
        self.checkpoint_bytes = self.checkpoint.read_bytes()

    def assert_preserved(self, payload: bytes) -> None:
        self.assertEqual(self.path.read_bytes(), payload)
        self.assertEqual(self.checkpoint.read_bytes(), self.checkpoint_bytes)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"translation.json", "checkpoint.json"})

    def assert_rejected(self, payload: bytes, code: str, *, expected: str | None = None, limit: int | None = None) -> None:
        self.path.write_bytes(payload)
        with self.assertRaises(VerifiedJsonError) as caught:
            read_verified_json(self.path, expected_sha256=expected or _digest(payload), max_bytes=limit or max(1, len(payload)))
        self.assertEqual(caught.exception.code, code)
        self.assert_preserved(payload)

    def test_unicode_object_and_exact_byte_boundary(self) -> None:
        document = {"source_language": "zh", "target_language": "vi", "cues": [{"text": "你好 → Xin chào 🐈", "start_ms": 125}]}
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(payload)
        self.assertGreater(len(payload), len(payload.decode("utf-8")))
        self.assertEqual(read_verified_json(str(self.path), expected_sha256=_digest(payload), max_bytes=len(payload)), document)
        self.assert_preserved(payload)

    def test_smallest_object_at_exact_limit(self) -> None:
        self.path.write_bytes(b"{}")
        self.assertEqual(read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2), {})
        self.assert_preserved(b"{}")

    def test_oversize_by_one_byte_and_by_many_bytes(self) -> None:
        for payload in (b'{"a":1}', b'{"a":"' + b"x" * 100_000 + b'"}'):
            with self.subTest(size=len(payload)):
                self.assert_rejected(payload, "TOO_LARGE", limit=6)

    def test_changed_valid_json_fails_before_parser(self) -> None:
        original, changed = b'{"text":"original"}', b'{"text":"tampered"}'
        self.assertEqual(json.loads(changed), {"text": "tampered"})
        with mock.patch.object(verified_json.json, "loads") as parser:
            self.assert_rejected(changed, "DIGEST_MISMATCH", expected=_digest(original))
        parser.assert_not_called()

    def test_digest_covers_raw_bytes_without_newline_normalization(self) -> None:
        self.assert_rejected(b'{"a":1}\r\n', "DIGEST_MISMATCH", expected=_digest(b'{"a":1}\n'))

    def test_invalid_digest_arguments_do_not_open_or_parse(self) -> None:
        invalid = (None, 1, "", "a" * 64, "sha256:" + "A" * 64, "sha256:" + "g" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 64 + "\n")
        with mock.patch.object(Path, "open") as opening, mock.patch.object(verified_json.json, "loads") as parser:
            for expected in invalid:
                with self.subTest(expected=expected), self.assertRaises(VerifiedJsonError) as caught:
                    read_verified_json(self.path, expected_sha256=expected, max_bytes=10)
                self.assertEqual(caught.exception.code, "INVALID_DIGEST")
        opening.assert_not_called()
        parser.assert_not_called()

    def test_invalid_limits_and_path_do_not_open(self) -> None:
        with mock.patch.object(Path, "open") as opening:
            for limit in (None, True, False, 0, -1, 1.5, "10"):
                with self.subTest(limit=limit), self.assertRaises(VerifiedJsonError) as caught:
                    read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=limit)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
            with self.assertRaises(VerifiedJsonError) as caught:
                read_verified_json(None, expected_sha256=_digest(b"{}"), max_bytes=2)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        opening.assert_not_called()

    def test_missing_file_and_open_failure(self) -> None:
        with self.assertRaises(VerifiedJsonError) as caught:
            read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2)
        self.assertEqual(caught.exception.code, "READ_FAILED")
        self.assertFalse(self.path.exists())
        self.path.write_bytes(b"{}")
        with mock.patch.object(Path, "open", side_effect=PermissionError("injected denied read")):
            with self.assertRaises(VerifiedJsonError) as caught:
                read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2)
        self.assertEqual(caught.exception.code, "READ_FAILED")
        self.assert_preserved(b"{}")

    def test_interrupted_read_closes_handle_and_never_parses(self) -> None:
        payload = b'{"text":"' + b"x" * 100_000 + b'"}'
        self.path.write_bytes(payload)
        stream = ObservedStream(payload, len(payload), fail_after=1)
        with mock.patch.object(Path, "open", return_value=stream) as opening, mock.patch.object(verified_json.json, "loads") as parser:
            with self.assertRaises(VerifiedJsonError) as caught:
                read_verified_json(self.path, expected_sha256=_digest(payload), max_bytes=len(payload))
        self.assertEqual(caught.exception.code, "READ_FAILED")
        opening.assert_called_once_with("rb")
        parser.assert_not_called()
        self.assertTrue(stream.closed)
        self.assert_preserved(payload)

    def test_hash_and_parse_failures_also_close_handle(self) -> None:
        for payload, expected, code in ((b"{}", _digest(b"[]"), "DIGEST_MISMATCH"), (b"{", _digest(b"{"), "INVALID_JSON"), (b"\xff", _digest(b"\xff"), "INVALID_ENCODING"), (b"[]", _digest(b"[]"), "INVALID_ROOT")):
            with self.subTest(code=code):
                stream = ObservedStream(payload, max(1, len(payload)))
                with mock.patch.object(Path, "open", return_value=stream), self.assertRaises(VerifiedJsonError) as caught:
                    read_verified_json(self.path, expected_sha256=expected, max_bytes=max(1, len(payload)))
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(stream.closed)

    def test_close_failure_is_reported_without_parsing(self) -> None:
        class ClosingStream(ObservedStream):
            def close(self) -> None:
                was_closed = self.closed
                super().close()
                if not was_closed:
                    raise OSError("injected close failure")

        payload = b"{}"
        self.path.write_bytes(payload)
        stream = ClosingStream(payload, len(payload))
        with mock.patch.object(Path, "open", return_value=stream), mock.patch.object(verified_json.json, "loads") as parser:
            with self.assertRaises(VerifiedJsonError) as caught:
                read_verified_json(self.path, expected_sha256=_digest(payload), max_bytes=len(payload))
        self.assertEqual(caught.exception.code, "READ_FAILED")
        self.assertTrue(stream.closed)
        parser.assert_not_called()
        self.assert_preserved(payload)

    def test_empty_truncated_and_invalid_utf8_are_predictable(self) -> None:
        for payload, code in ((b"", "INVALID_JSON"), (b'{"cues":', "INVALID_JSON"), (b'{"text":"\xff"}', "INVALID_ENCODING")):
            with self.subTest(payload=payload):
                self.assert_rejected(payload, code)

    def test_non_object_roots_are_rejected(self) -> None:
        for payload in (b"[]", b"null", b"true", b"123", b'"text"'):
            with self.subTest(payload=payload):
                self.assert_rejected(payload, "INVALID_ROOT")

    def test_non_json_numeric_constants_are_rejected_at_every_level(self) -> None:
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            for payload in (constant, b'{"value":' + constant + b"}", b'{"nested":[{"value":' + constant + b"}]}"):
                with self.subTest(payload=payload):
                    self.assert_rejected(payload, "INVALID_JSON")

    def test_numeric_overflow_to_non_finite_float_is_rejected(self) -> None:
        for number in (b"1e309", b"-1e309", b"1e4000"):
            for payload in (b'{"value":' + number + b"}", b'{"nested":[{"value":' + number + b"}]}"):
                with self.subTest(payload=payload):
                    self.assert_rejected(payload, "INVALID_JSON")

    def test_finite_floats_and_constant_names_in_strings_remain_valid(self) -> None:
        payload = b'{"values":[1.25,-0.0,1e308,1e-4000],"text":"NaN Infinity -Infinity"}'
        self.path.write_bytes(payload)
        self.assertEqual(
            read_verified_json(self.path, expected_sha256=_digest(payload), max_bytes=len(payload)),
            {"values": [1.25, -0.0, 1e308, 0.0], "text": "NaN Infinity -Infinity"},
        )
        self.assert_preserved(payload)

    def _assert_public_traceback(self, operation, code: str, *sensitive: str) -> None:
        try:
            operation()
        except VerifiedJsonError as error:
            self.assertEqual(error.code, code)
            rendered = "".join(traceback.format_exception(error))
            self.assertIn("VerifiedJsonError", rendered)
            for value in sensitive:
                self.assertNotIn(value, rendered)
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
        else:
            self.fail("public error was not raised")

    def test_read_failure_tracebacks_hide_artifact_path_and_oserror_text(self) -> None:
        sensitive_path = str(self.path)
        secret = "injected-private-storage-detail-731"
        operation = lambda: read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2)
        self._assert_public_traceback(operation, "READ_FAILED", sensitive_path)
        self.path.write_bytes(b"{}")
        with mock.patch.object(Path, "open", side_effect=PermissionError(13, secret, sensitive_path)):
            self._assert_public_traceback(operation, "READ_FAILED", sensitive_path, secret)

        class FailingStream(ObservedStream):
            def read(self, size: int = -1) -> bytes:
                raise OSError(5, secret, sensitive_path)

        stream = FailingStream(b"{}", 2)
        with mock.patch.object(Path, "open", return_value=stream):
            self._assert_public_traceback(operation, "READ_FAILED", sensitive_path, secret)
        self.assertTrue(stream.closed)

        class CloseFailingStream(ObservedStream):
            def close(self) -> None:
                was_closed = self.closed
                super().close()
                if not was_closed:
                    raise OSError(5, secret, sensitive_path)

        stream = CloseFailingStream(b"{}", 2)
        with mock.patch.object(Path, "open", return_value=stream):
            self._assert_public_traceback(operation, "READ_FAILED", sensitive_path, secret)
        self.assertTrue(stream.closed)
        self.assert_preserved(b"{}")

    def test_argument_encoding_and_parser_tracebacks_hide_original_details(self) -> None:
        sensitive_path = str(self.path)
        secret = "injected-private-parser-detail-947"
        with mock.patch.object(verified_json, "Path", side_effect=TypeError(secret + sensitive_path)):
            operation = lambda: read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2)
            self._assert_public_traceback(operation, "INVALID_ARGUMENT", sensitive_path, secret)
        for payload, code in ((b"\xff", "INVALID_ENCODING"), (b'{"private":', "INVALID_JSON")):
            self.path.write_bytes(payload)
            operation = lambda: read_verified_json(self.path, expected_sha256=_digest(payload), max_bytes=len(payload))
            self._assert_public_traceback(operation, code, sensitive_path)
            self.assert_preserved(payload)
        self.path.write_bytes(b"{}")
        with mock.patch.object(verified_json.json, "loads", side_effect=ValueError(secret + sensitive_path)):
            operation = lambda: read_verified_json(self.path, expected_sha256=_digest(b"{}"), max_bytes=2)
            self._assert_public_traceback(operation, "INVALID_JSON", sensitive_path, secret)
        self.assert_preserved(b"{}")

    def test_public_errors_suppress_unrelated_active_exception_context(self) -> None:
        secret = "injected-private-caller-context-582"
        cases = ((b"{}", None, 2, "INVALID_DIGEST"), (b"{}", _digest(b"{}"), 0, "INVALID_ARGUMENT"), (b"{}", _digest(b"{}"), 1, "TOO_LARGE"), (b"{}", _digest(b"[]"), 2, "DIGEST_MISMATCH"), (b"[]", _digest(b"[]"), 2, "INVALID_ROOT"))
        for payload, expected, limit, code in cases:
            with self.subTest(code=code):
                self.path.write_bytes(payload)
                try:
                    raise OSError(secret)
                except OSError:
                    operation = lambda: read_verified_json(self.path, expected_sha256=expected, max_bytes=limit)
                    self._assert_public_traceback(operation, code, str(self.path), secret)
                self.assert_preserved(payload)

    def test_parser_resource_errors_have_predictable_failure(self) -> None:
        # Integer-digit and recursion limits differ between Python runtimes.
        # Inject their failures without changing global decoder policy.
        payload = b'{"n":1}'
        self.path.write_bytes(payload)
        for failure in (ValueError("injected integer digit limit"), RecursionError("injected parser recursion limit")):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(verified_json.json, "loads", side_effect=failure):
                    with self.assertRaises(VerifiedJsonError) as caught:
                        read_verified_json(self.path, expected_sha256=_digest(payload), max_bytes=len(payload))
                self.assertEqual(caught.exception.code, "INVALID_JSON")
                self.assert_preserved(payload)

    def _assert_mismatch_contract(self, reader) -> None:
        changed = b'{"text":"tampered"}'
        self.path.write_bytes(changed)
        with self.assertRaises(VerifiedJsonError) as caught:
            reader(self.path, expected_sha256=_digest(b'{"text":"original"}'), max_bytes=len(changed))
        self.assertEqual(caught.exception.code, "DIGEST_MISMATCH")

    def test_negative_control_omitted_digest_is_detected(self) -> None:
        def unchecked(path, *, expected_sha256, max_bytes):
            with path.open("rb") as stream:
                return json.loads(stream.read(max_bytes + 1).decode("utf-8"))

        self._assert_mismatch_contract(read_verified_json)
        with self.assertRaises(AssertionError):
            self._assert_mismatch_contract(unchecked)

    def _assert_same_bytes_contract(self, reader) -> None:
        original, changed = b'{"text":"original"}', b'{"text":"replaced"}'
        self.path.write_bytes(original)
        replacement = self.root / "replacement.json"
        replacement.write_bytes(changed)
        stream = ObservedStream(original, len(original), on_eof=lambda: replacement.replace(self.path))
        other = ObservedStream(changed, len(original))
        self.addCleanup(other.close)
        with mock.patch.object(Path, "open", side_effect=[stream, other]) as opening:
            result = reader(self.path, expected_sha256=_digest(original), max_bytes=len(original))
        self.assertEqual(result, {"text": "original"})
        opening.assert_called_once_with("rb")
        self.assertTrue(stream.closed)
        self.assert_preserved(changed)

    def test_path_replacement_uses_captured_bytes_and_one_open(self) -> None:
        self._assert_same_bytes_contract(read_verified_json)

    def test_negative_control_hash_then_reopen_is_detected(self) -> None:
        def reopen(path, *, expected_sha256, max_bytes):
            with path.open("rb") as stream:
                payload = bytearray()
                while chunk := stream.read(min(64 * 1024, max_bytes + 1 - len(payload))):
                    payload.extend(chunk)
            if _digest(payload) != expected_sha256:
                raise VerifiedJsonError("DIGEST_MISMATCH", "control mismatch")
            with path.open("rb") as stream:
                return json.loads(stream.read(max_bytes + 1).decode("utf-8"))

        self._assert_same_bytes_contract(read_verified_json)
        with self.assertRaises(AssertionError):
            self._assert_same_bytes_contract(reopen)

    def _assert_bounded_contract(self, reader, *, oversized: bool = False) -> None:
        payload = b'{"text":"' + b"x" * 100_000 + b'"}'
        limit = 70_000 if oversized else len(payload)
        stream = ObservedStream(payload, limit)
        with mock.patch.object(Path, "open", return_value=stream) as opening:
            if oversized:
                with self.assertRaises(VerifiedJsonError) as caught:
                    reader(self.path, expected_sha256=_digest(payload), max_bytes=limit)
                self.assertEqual(caught.exception.code, "TOO_LARGE")
            else:
                self.assertEqual(reader(self.path, expected_sha256=_digest(payload), max_bytes=limit), {"text": "x" * 100_000})
        opening.assert_called_once_with("rb")
        self.assertTrue(stream.closed)
        self.assertLessEqual(stream.consumed, limit + 1)
        self.assertGreater(len(stream.requests), 1)

    def test_chunked_reads_and_oversize_close_within_budget(self) -> None:
        self._assert_bounded_contract(read_verified_json)
        self._assert_bounded_contract(read_verified_json, oversized=True)

    def test_negative_control_unbounded_read_is_detected(self) -> None:
        def unbounded(path, *, expected_sha256, max_bytes):
            with path.open("rb") as stream:
                payload = stream.read()
            if _digest(payload) != expected_sha256:
                raise VerifiedJsonError("DIGEST_MISMATCH", "control mismatch")
            return json.loads(payload.decode("utf-8"))

        self._assert_bounded_contract(read_verified_json)
        with self.assertRaises(AssertionError):
            self._assert_bounded_contract(unbounded)


if __name__ == "__main__":
    unittest.main()
