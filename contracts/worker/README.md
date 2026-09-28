# Supervisor ↔ Python worker protocol

This directory owns the versioned local worker wire contract for Issue #4.

The transport is line-delimited UTF-8 JSON over a supervised stdio stream. Each
transport line, including its terminating newline, is at most 64 KiB. Each
line is one complete envelope with an explicit `schema_version`, `message_type`,
`message_id`, `job_id`, `stage_id` and monotonically increasing `sequence`.
Messages are rejected on malformed JSON, unknown versions/types, duplicate
fields, missing required fields, invalid sequence transitions or oversized
payloads. A worker never opens or mutates the supervisor's durable SQLite
database; checkpoints and state changes are returned as protocol messages for
the Rust supervisor to validate and commit.

v1 distinguishes commands, progress, checkpoints, heartbeats, cancellation,
structured failures and graceful shutdown. Progress is bounded and coalesced;
heartbeats prove liveness but do not replace durable checkpoint progress. EOF,
malformed output and a heartbeat timeout become typed worker failures. Cancel
is cooperative and only becomes complete after a reusable safe checkpoint or an
explicit cancellation failure. Retry metadata records a materially changed
condition and a finite attempt number.

The JSON schema, dependency-free Python reference and deterministic protocol
fixtures are added in the active Issue #4 Draft PR. The protocol is an adapter
boundary: desktop/UI and AI engines do not call each other directly, and the
Rust supervisor remains the sole durable-state writer.
