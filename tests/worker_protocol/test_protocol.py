from __future__ import annotations

import json
from pathlib import Path
import unittest

from engine.dubflow.worker.protocol import (
    CancellationController,
    CancellationState,
    Envelope,
    FakeWorker,
    MAX_LINE_BYTES,
    MessageType,
    ProgressBuffer,
    ProtocolError,
    RetryBudget,
    StreamValidator,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "worker_protocol" / "fixtures" / "valid.jsonl"
SCHEMA = ROOT / "contracts" / "worker" / "schema-v1.json"


class WorkerProtocolTests(unittest.TestCase):
    def test_schema_and_shared_fixture_are_explicit(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/worker/schema-v1.json")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            set(schema["properties"]["message_type"]["enum"]),
            {item.value for item in MessageType},
        )

        lines = FIXTURE.read_bytes().splitlines(keepends=True)
        self.assertEqual(len(lines), 7)
        validator = StreamValidator(heartbeat_timeout_ms=100, started_ms=0)
        messages = [Envelope.from_line(line) for line in lines]
        for message in messages:
            validator.accept(message)
        self.assertTrue(validator.terminal)
        self.assertEqual(validator.expected_sequence, 8)
        self.assertEqual(messages[0].payload["args"], {"chunk": "0"})
        self.assertEqual(messages[-1].payload["status"], "cancelled")

    def test_round_trip_preserves_wide_sequence_and_unicode(self) -> None:
        message = Envelope.create(
            MessageType.PROGRESS,
            "message-🙂",
            "job-🙂",
            "stage-🙂",
            (1 << 64) - 1,
            {"fraction": 1, "detail": "完成"},
        )
        self.assertEqual(Envelope.from_line(message.to_line()), message)
        with self.assertRaises(ProtocolError) as context:
            Envelope.create(MessageType.HEARTBEAT, "m", "j", "s", 1 << 64, {"monotonic_ms": 0})
        self.assertEqual(context.exception.code, "INVALID_INTEGER")

    def test_duplicate_unknown_future_and_wrong_types_are_rejected(self) -> None:
        valid = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
        cases = {
            "duplicate": valid.replace('"sequence":1', '"sequence":1,"sequence":2'),
            "unknown": valid.replace('"payload":', '"extra":0,"payload":'),
            "future": valid.replace('"schema_version":1', '"schema_version":2'),
            "numeric_string": valid.replace('"sequence":1', '"sequence":"1"'),
            "wrong_payload": valid.replace('"args":{"chunk":"0"}', '"args":[]'),
            "nested_duplicate": valid.replace('"args":{"chunk":"0"}', '"args":{"x":1,"x":2}'),
        }
        expected = {
            "duplicate": "DUPLICATE_FIELD",
            "unknown": "UNKNOWN_FIELD",
            "future": "UNSUPPORTED_VERSION",
            "numeric_string": "INVALID_INTEGER",
            "wrong_payload": "INVALID_TYPE",
            "nested_duplicate": "DUPLICATE_FIELD",
        }
        for name, line in cases.items():
            with self.subTest(name=name), self.assertRaises(ProtocolError) as context:
                Envelope.from_line(line)
            self.assertEqual(context.exception.code, expected[name])

        with self.assertRaises(ProtocolError) as context:
            Envelope.from_line(b"{\"schema_version\":1," + b"x" * MAX_LINE_BYTES)
        self.assertEqual(context.exception.code, "LINE_TOO_LARGE")
        with self.assertRaises(ProtocolError) as context:
            Envelope.from_line(b"\xff")
        self.assertEqual(context.exception.code, "INVALID_UTF8")

    def test_stream_rules_include_heartbeat_timeout_and_eof_error(self) -> None:
        heartbeat = Envelope.create(
            MessageType.HEARTBEAT, "m", "j", "s", 1, {"monotonic_ms": 10}
        )
        validator = StreamValidator(heartbeat_timeout_ms=50, started_ms=0)
        validator.accept(heartbeat)
        validator.check_heartbeat(60)
        with self.assertRaises(ProtocolError) as context:
            validator.check_heartbeat(61)
        self.assertEqual(context.exception.code, "HEARTBEAT_TIMEOUT")
        with self.assertRaises(ProtocolError) as context:
            Envelope.from_line(b"")
        self.assertEqual(context.exception.code, "INVALID_LINE")
        with self.assertRaises(ProtocolError) as context:
            validator.accept(
                Envelope.create(MessageType.HEARTBEAT, "m2", "j", "s", 3, {"monotonic_ms": 20})
            )
        self.assertEqual(context.exception.code, "SEQUENCE_GAP")

    def test_progress_buffer_is_bounded_and_coalesces_latest_stage(self) -> None:
        buffer = ProgressBuffer(max_items=2)
        first = Envelope.create(MessageType.PROGRESS, "m1", "j", "a", 1, {"fraction": 0.1})
        second = Envelope.create(MessageType.PROGRESS, "m2", "j", "b", 2, {"fraction": 0.2})
        latest = Envelope.create(MessageType.PROGRESS, "m3", "j", "a", 3, {"fraction": 0.3})
        third = Envelope.create(MessageType.PROGRESS, "m4", "j", "c", 4, {"fraction": 0.4})
        for message in (first, second, latest, third):
            buffer.push(message)
        self.assertEqual(len(buffer), 2)
        self.assertEqual(buffer.coalesced_count, 1)
        self.assertEqual(buffer.dropped_count, 1)
        self.assertEqual([message.stage_id for message in buffer.drain()], ["b", "c"])
        with self.assertRaises(ProtocolError):
            buffer.push(Envelope.create(MessageType.HEARTBEAT, "m5", "j", "s", 5, {"monotonic_ms": 1}))

    def test_cancellation_requires_safe_checkpoint_and_retries_change_condition(self) -> None:
        cancellation = CancellationController()
        with self.assertRaises(ProtocolError) as context:
            cancellation.complete()
        self.assertEqual(context.exception.code, "UNSAFE_CANCEL")
        cancellation.request("user")
        cancellation.checkpoint("safe-cancel")
        cancellation.complete()
        self.assertEqual(cancellation.state, CancellationState.COMPLETED)
        with self.assertRaises(ProtocolError) as context:
            cancellation.request("late")
        self.assertEqual(context.exception.code, "CANCEL_TOO_LATE")

        retries = RetryBudget(max_attempts=2)
        self.assertEqual(retries.record_retry("network-reset"), 1)
        with self.assertRaises(ProtocolError) as context:
            retries.record_retry("network-reset")
        self.assertEqual(context.exception.code, "RETRY_CONDITION_UNCHANGED")
        self.assertEqual(retries.record_retry("worker-restarted"), 2)
        with self.assertRaises(ProtocolError) as context:
            retries.record_retry("third-condition")
        self.assertEqual(context.exception.code, "RETRY_EXHAUSTED")

    def test_fake_worker_has_complete_and_cancelled_safe_paths(self) -> None:
        command = Envelope.create(MessageType.COMMAND, "command", "job", "stage", 1, {"command": "analyze", "args": {}})
        completed = list(FakeWorker("job", "stage").run(command))
        self.assertEqual(completed[-1].payload["status"], "completed")
        self.assertEqual(completed[-1].message_type, MessageType.SHUTDOWN)
        cancelled = list(FakeWorker("job", "stage").run(command, cancel_after_progress=True))
        self.assertEqual(cancelled[-1].payload["status"], "cancelled")
        self.assertEqual(cancelled[-2].message_type, MessageType.CHECKPOINT)


if __name__ == "__main__":
    unittest.main()
