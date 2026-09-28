"""Dependency-free v1 supervisor/worker JSONL protocol reference.

The worker never writes durable state.  It emits validated envelopes and lets
the Rust supervisor decide when a checkpoint/artifact is committed.  This
module intentionally avoids third-party JSON/runtime dependencies so the
contract can be tested on a clean development machine; the shipped product
uses its app-owned runtime.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any, Iterable


SCHEMA_VERSION = 1
MAX_LINE_BYTES = 64 * 1024
MAX_ID_LENGTH = 128
MAX_PAYLOAD_TEXT = MAX_LINE_BYTES - 512
MAX_PROGRESS_BUFFER = 128
U64_MAX = (1 << 64) - 1


class ProtocolError(ValueError):
    """A deterministic, user-auditable protocol failure."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class MessageType(str, Enum):
    COMMAND = "command"
    PROGRESS = "progress"
    CHECKPOINT = "checkpoint"
    HEARTBEAT = "heartbeat"
    CANCEL = "cancel"
    FAILURE = "failure"
    SHUTDOWN = "shutdown"


MESSAGE_TYPES = frozenset(item.value for item in MessageType)
ENVELOPE_KEYS = frozenset(
    {"schema_version", "message_type", "message_id", "job_id", "stage_id", "sequence", "payload"}
)
PAYLOAD_KEYS: dict[MessageType, tuple[frozenset[str], frozenset[str]]] = {
    MessageType.COMMAND: (frozenset({"command", "args"}), frozenset()),
    MessageType.PROGRESS: (
        frozenset({"fraction"}),
        frozenset({"detail", "units_done", "units_total"}),
    ),
    MessageType.CHECKPOINT: (
        frozenset({"checkpoint_id", "reusable"}),
        frozenset({"artifact_hash"}),
    ),
    MessageType.HEARTBEAT: (frozenset({"monotonic_ms"}), frozenset()),
    MessageType.CANCEL: (frozenset({"reason"}), frozenset()),
    MessageType.FAILURE: (
        frozenset({"code", "retryable", "attempt", "condition"}),
        frozenset(),
    ),
    MessageType.SHUTDOWN: (frozenset({"status"}), frozenset()),
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("DUPLICATE_FIELD", f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError("NON_FINITE_NUMBER", f"JSON constant {value!r} is not allowed")


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProtocolError("INVALID_TYPE", f"{name} must be an object")
    return value


def _require_keys(value: dict[str, Any], required: frozenset[str], optional: frozenset[str], name: str) -> None:
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise ProtocolError("UNKNOWN_FIELD", f"{name} has unknown members: {sorted(unknown)}")
    if missing:
        raise ProtocolError("MISSING_FIELD", f"{name} is missing members: {sorted(missing)}")


def _require_string(value: Any, name: str, *, max_length: int = MAX_ID_LENGTH) -> str:
    if type(value) is not str or not value:
        raise ProtocolError("INVALID_STRING", f"{name} must be a non-empty string")
    if len(value) > max_length:
        raise ProtocolError("FIELD_TOO_LARGE", f"{name} exceeds {max_length} characters")
    return value


def _require_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ProtocolError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ProtocolError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ProtocolError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _validate_payload(message_type: MessageType, payload: Any) -> dict[str, Any]:
    payload = _require_object(payload, "payload")
    required, optional = PAYLOAD_KEYS[message_type]
    _require_keys(payload, required, optional, f"{message_type.value} payload")
    if message_type is MessageType.COMMAND:
        _require_string(payload["command"], "command", max_length=128)
        _require_object(payload["args"], "command.args")
    elif message_type is MessageType.PROGRESS:
        fraction = payload["fraction"]
        if type(fraction) not in (int, float) or isinstance(fraction, bool) or not math.isfinite(float(fraction)):
            raise ProtocolError("INVALID_PROGRESS", "fraction must be finite")
        if not 0 <= float(fraction) <= 1:
            raise ProtocolError("INVALID_PROGRESS", "fraction must be between 0 and 1")
        if "detail" in payload:
            _require_string(payload["detail"], "progress.detail", max_length=4096)
        for key in ("units_done", "units_total"):
            if key in payload:
                _require_int(payload[key], f"progress.{key}", minimum=0, maximum=U64_MAX)
    elif message_type is MessageType.CHECKPOINT:
        _require_string(payload["checkpoint_id"], "checkpoint_id", max_length=256)
        if type(payload["reusable"]) is not bool:
            raise ProtocolError("INVALID_BOOLEAN", "checkpoint.reusable must be boolean")
        if "artifact_hash" in payload:
            _require_string(payload["artifact_hash"], "artifact_hash", max_length=256)
    elif message_type is MessageType.HEARTBEAT:
        _require_int(payload["monotonic_ms"], "heartbeat.monotonic_ms", minimum=0, maximum=U64_MAX)
    elif message_type is MessageType.CANCEL:
        _require_string(payload["reason"], "cancel.reason", max_length=4096)
    elif message_type is MessageType.FAILURE:
        _require_string(payload["code"], "failure.code", max_length=128)
        if type(payload["retryable"]) is not bool:
            raise ProtocolError("INVALID_BOOLEAN", "failure.retryable must be boolean")
        _require_int(payload["attempt"], "failure.attempt", minimum=1, maximum=255)
        _require_string(payload["condition"], "failure.condition", max_length=4096)
    elif message_type is MessageType.SHUTDOWN:
        if payload["status"] not in {"completed", "cancelled", "failed"}:
            raise ProtocolError("INVALID_STATUS", "shutdown.status is invalid")
    return payload


@dataclass(frozen=True)
class Envelope:
    schema_version: int
    message_type: MessageType
    message_id: str
    job_id: str
    stage_id: str
    sequence: int
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        message_type: MessageType | str,
        message_id: str,
        job_id: str,
        stage_id: str,
        sequence: int,
        payload: dict[str, Any],
    ) -> "Envelope":
        value = {
            "schema_version": SCHEMA_VERSION,
            "message_type": message_type.value if isinstance(message_type, MessageType) else message_type,
            "message_id": message_id,
            "job_id": job_id,
            "stage_id": stage_id,
            "sequence": sequence,
            "payload": payload,
        }
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> "Envelope":
        value = _require_object(value, "envelope")
        _require_keys(value, ENVELOPE_KEYS, frozenset(), "envelope")
        if type(value["schema_version"]) is not int:
            raise ProtocolError("INVALID_VERSION", "schema_version must be an integer")
        if value["schema_version"] != SCHEMA_VERSION:
            raise ProtocolError("UNSUPPORTED_VERSION", f"schema version {value['schema_version']} is not supported")
        if type(value["message_type"]) is not str or value["message_type"] not in MESSAGE_TYPES:
            raise ProtocolError("UNKNOWN_MESSAGE_TYPE", f"unknown message type {value['message_type']!r}")
        message_type = MessageType(value["message_type"])
        message_id = _require_string(value["message_id"], "message_id")
        job_id = _require_string(value["job_id"], "job_id")
        stage_id = _require_string(value["stage_id"], "stage_id")
        sequence = _require_int(value["sequence"], "sequence", minimum=1, maximum=U64_MAX)
        payload = _validate_payload(message_type, value["payload"])
        return cls(SCHEMA_VERSION, message_type, message_id, job_id, stage_id, sequence, payload)

    @classmethod
    def from_line(cls, line: bytes | str) -> "Envelope":
        if isinstance(line, bytes):
            if len(line) > MAX_LINE_BYTES:
                raise ProtocolError("LINE_TOO_LARGE", f"line exceeds {MAX_LINE_BYTES} bytes")
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError("INVALID_UTF8", "worker output is not UTF-8") from exc
        elif isinstance(line, str):
            try:
                if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                    raise ProtocolError("LINE_TOO_LARGE", f"line exceeds {MAX_LINE_BYTES} bytes")
            except UnicodeEncodeError as exc:
                raise ProtocolError("INVALID_UTF8", "worker output cannot be encoded as UTF-8") from exc
            text = line
        else:
            raise ProtocolError("INVALID_TYPE", "line must be bytes or string")
        if text.endswith("\n"):
            text = text[:-1]
        if not text or "\n" in text or "\r" in text:
            raise ProtocolError("INVALID_LINE", "one JSON object is required per line")
        try:
            value = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except ProtocolError:
            raise
        except json.JSONDecodeError as exc:
            raise ProtocolError("MALFORMED_JSON", str(exc)) from exc
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "message_type": self.message_type.value,
            "message_id": self.message_id,
            "job_id": self.job_id,
            "stage_id": self.stage_id,
            "sequence": self.sequence,
            "payload": self.payload,
        }

    def to_line(self) -> bytes:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise ProtocolError("LINE_TOO_LARGE", f"line exceeds {MAX_LINE_BYTES} bytes")
        return encoded + b"\n"


@dataclass
class StreamValidator:
    heartbeat_timeout_ms: int = 30_000
    started_ms: int = 0
    expected_sequence: int = 1
    last_heartbeat_ms: int = field(init=False)
    terminal: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_int(self.heartbeat_timeout_ms, "heartbeat_timeout_ms", minimum=1)
        _require_int(self.started_ms, "started_ms", minimum=0, maximum=U64_MAX)
        self.last_heartbeat_ms = self.started_ms

    def accept(self, envelope: Envelope) -> None:
        if self.terminal:
            raise ProtocolError("STREAM_TERMINAL", "messages after shutdown are forbidden")
        if envelope.sequence != self.expected_sequence:
            raise ProtocolError(
                "SEQUENCE_GAP",
                f"expected sequence {self.expected_sequence}, received {envelope.sequence}",
            )
        if self.expected_sequence == (1 << 64) - 1:
            raise ProtocolError("SEQUENCE_OVERFLOW", "sequence cannot advance further")
        self.expected_sequence += 1
        if envelope.message_type is MessageType.HEARTBEAT:
            heartbeat = envelope.payload["monotonic_ms"]
            if heartbeat < self.last_heartbeat_ms:
                raise ProtocolError("HEARTBEAT_REGRESSION", "worker monotonic time moved backwards")
            self.last_heartbeat_ms = heartbeat
        if envelope.message_type is MessageType.SHUTDOWN:
            self.terminal = True

    def check_heartbeat(self, now_ms: int) -> None:
        _require_int(now_ms, "now_ms", minimum=0, maximum=U64_MAX)
        if now_ms - self.last_heartbeat_ms > self.heartbeat_timeout_ms:
            raise ProtocolError("HEARTBEAT_TIMEOUT", "worker exceeded heartbeat deadline")


@dataclass
class ProgressBuffer:
    max_items: int = MAX_PROGRESS_BUFFER
    _items: deque[Envelope] = field(default_factory=deque, init=False)
    dropped_count: int = field(default=0, init=False)
    coalesced_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        _require_int(self.max_items, "max_items", minimum=1, maximum=MAX_PROGRESS_BUFFER)

    def push(self, envelope: Envelope) -> None:
        if envelope.message_type is not MessageType.PROGRESS:
            raise ProtocolError("INVALID_BUFFER_MESSAGE", "only progress messages may enter this buffer")
        for index in range(len(self._items) - 1, -1, -1):
            if self._items[index].stage_id == envelope.stage_id:
                self._items[index] = envelope
                self.coalesced_count += 1
                return
        if len(self._items) >= self.max_items:
            self._items.popleft()
            self.dropped_count += 1
        self._items.append(envelope)

    def drain(self) -> list[Envelope]:
        values = list(self._items)
        self._items.clear()
        return values

    def __len__(self) -> int:
        return len(self._items)


class CancellationState(str, Enum):
    RUNNING = "running"
    REQUESTED = "requested"
    CHECKPOINTED = "checkpointed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CancellationController:
    state: CancellationState = CancellationState.RUNNING
    reason: str | None = None
    checkpoint_id: str | None = None

    def request(self, reason: str) -> None:
        _require_string(reason, "cancel.reason", max_length=4096)
        if self.state in {CancellationState.COMPLETED, CancellationState.FAILED}:
            raise ProtocolError("CANCEL_TOO_LATE", "cancellation arrived after terminal state")
        self.state = CancellationState.REQUESTED
        self.reason = reason

    def checkpoint(self, checkpoint_id: str) -> None:
        _require_string(checkpoint_id, "checkpoint_id", max_length=256)
        if self.state is not CancellationState.REQUESTED:
            raise ProtocolError("CHECKPOINT_NOT_PENDING", "a cancellation checkpoint was not requested")
        self.state = CancellationState.CHECKPOINTED
        self.checkpoint_id = checkpoint_id

    def complete(self) -> None:
        if self.state is not CancellationState.CHECKPOINTED:
            raise ProtocolError("UNSAFE_CANCEL", "worker may complete cancellation only after a safe checkpoint")
        self.state = CancellationState.COMPLETED

    def fail(self, condition: str) -> None:
        _require_string(condition, "failure.condition", max_length=4096)
        self.state = CancellationState.FAILED


@dataclass
class RetryBudget:
    """Finite retry bookkeeping with a materially changed condition rule."""

    max_attempts: int = 3
    attempts: int = 0
    last_condition: str | None = None

    def __post_init__(self) -> None:
        _require_int(self.max_attempts, "max_attempts", minimum=1, maximum=255)
        _require_int(self.attempts, "attempts", minimum=0, maximum=self.max_attempts)

    def record_retry(self, condition: str) -> int:
        _require_string(condition, "failure.condition", max_length=4096)
        if self.attempts >= self.max_attempts:
            raise ProtocolError("RETRY_EXHAUSTED", "retry budget is exhausted")
        if self.last_condition == condition:
            raise ProtocolError("RETRY_CONDITION_UNCHANGED", "retry condition did not materially change")
        self.attempts += 1
        self.last_condition = condition
        return self.attempts


@dataclass
class FakeWorker:
    job_id: str
    stage_id: str
    next_sequence: int = 1
    message_counter: int = 0

    def _message(self, message_type: MessageType, payload: dict[str, Any]) -> Envelope:
        self.message_counter += 1
        envelope = Envelope.create(
            message_type,
            f"fake-{self.message_counter}",
            self.job_id,
            self.stage_id,
            self.next_sequence,
            payload,
        )
        self.next_sequence += 1
        return envelope

    def run(self, command: Envelope, *, cancel_after_progress: bool = False) -> Iterable[Envelope]:
        if command.message_type is not MessageType.COMMAND:
            raise ProtocolError("INVALID_COMMAND", "fake worker needs a command envelope")
        yield self._message(MessageType.HEARTBEAT, {"monotonic_ms": 0})
        yield self._message(MessageType.PROGRESS, {"fraction": 0.5, "detail": "fake analysis"})
        if cancel_after_progress:
            yield self._message(MessageType.CANCEL, {"reason": "requested by test"})
            yield self._message(
                MessageType.CHECKPOINT,
                {"checkpoint_id": "fake-safe-point", "reusable": True},
            )
            yield self._message(MessageType.SHUTDOWN, {"status": "cancelled"})
            return
        yield self._message(
            MessageType.CHECKPOINT,
            {"checkpoint_id": "fake-complete", "reusable": True},
        )
        yield self._message(MessageType.SHUTDOWN, {"status": "completed"})
