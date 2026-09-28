# ADR-0003 — Versioned supervisor ↔ worker protocol

Status: Accepted

## Context

DubFlow's Rust supervisor owns durable state while Python workers host optional
media/AI engines. Direct calls or worker-owned SQLite writes would couple
lifetimes, make cancellation unsafe and make a dead worker indistinguishable
from slow work. The boundary must remain useful when a real model is absent,
and a malformed or noisy worker must not exhaust supervisor memory.

## Decision

1. v1 is line-delimited UTF-8 JSON over supervised stdio. Every envelope has
   `schema_version`, `message_type`, IDs for job/stage/message, and a positive
   monotonic `sequence`. The maximum transport line, including its newline
   delimiter, is 64 KiB.
2. The message set is closed: `command`, `progress`, `checkpoint`,
   `heartbeat`, `cancel`, `failure` and `shutdown`. Unknown versions, types,
   members, duplicate members and malformed payloads are typed protocol errors.
3. The supervisor is the only durable-state writer. A worker reports
   checkpoints, artifacts and failures; the supervisor validates and commits
   them before exposing progress to the UI.
4. Sequence numbers are contiguous per stream. Progress is bounded and may be
   coalesced/dropped with an observable dropped count; checkpoints and failures
   are never silently dropped. Heartbeats prove liveness but are not durable
   progress.
5. Cancellation is cooperative. A cancel request reaches a safe checkpoint
   before the worker emits `shutdown: cancelled`; failure to checkpoint is a
   structured cancellation failure. Retry metadata has a finite attempt and a
   materially changed condition.
6. EOF, malformed output and heartbeat expiry become structured worker
   failures. A fake worker and deterministic fixtures exercise these rules
   without loading a model or contacting a live service.

## Compatibility and migration

Readers reject a future `schema_version` and preserve the raw line for a
versioned adapter/migration. A producer cannot add a new message type or
payload field to v1 without a compatibility review. A future version may add
messages or fields behind a new schema while retaining the v1 fake-worker
adapter during a bounded migration window. The wire schema and fixtures are
the cross-language source of truth; the Rust and Python implementations must
agree on validation and error classes.

## Consequences

- Rust/Python implementations can be upgraded independently and tested with
  the same JSONL fixtures.
- A worker crash loses only in-flight work after the last supervisor-committed
  checkpoint; it cannot corrupt SQLite or make a progress queue unbounded.
- The protocol remains adapter-level. Model/runtime/version compatibility is
  recorded by later job/artifact contracts rather than hidden in this stream.
