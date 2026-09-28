# ADR-0007: Durable job liveness and actionable recovery

## Status

Accepted — 2026-09-29

## Context

A percentage can remain unchanged during valid long inference or rendering, and
a process can remain alive while making no durable progress. Treating elapsed
wall-clock time as proof of a stall either kills useful work or leaves a user
with a frozen job that owns scarce resources. Liveness also has to survive an
app restart and must not become a second durable-state writer.

## Decision

The supervisor classifies liveness from three durable/observable signals:

1. the last durable checkpoint and progress units;
2. monotonic worker heartbeat sequence or an explicit EOF/failure signal; and
3. scheduler state, including queued and resource-wait states.

An advancing heartbeat keeps slow work `RUNNING` even when progress is flat.
Queued work and GPU/CPU/disk/network/worker-slot waits are distinct statuses.
External authentication or site-change waits become one actionable
`WAITING_EXTERNAL` message and release a held resource.

Worker death can restart from the last checkpoint only within a bounded retry
budget. After the first attempt, a retry requires a materially different
condition fingerprint. Exhaustion or an unchanged condition becomes
`BLOCKED_NEEDS_ACTION`; the watchdog never loops forever. A recovery decision
can request an approved fallback while preserving the checkpoint.

The desktop displays the versioned status and message supplied by the
supervisor. It does not infer liveness from a timer or progress percentage.
The watchdog is advisory; SQLite writes remain supervisor-owned and durable
state is enough to reconstruct the status after restart.

## Compatibility and migration

The status contract is additive and versioned. Existing jobs without a
liveness record are reconstructed from their durable checkpoint and stage state
with a conservative `QUEUED`/`RUNNING` classification. No existing SQLite
schema is rewritten by this prototype. Future durable fields require an
append-only migration owned by the state lane.
