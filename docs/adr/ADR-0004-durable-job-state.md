# ADR-0004 — Supervisor-owned durable job state

Status: Accepted

## Context

DubFlow must resume long jobs after process termination, sleep/resume, partial
output and missing artifacts. A worker-owned database or an output-drive
database would let a dead worker corrupt the control plane or make recovery
impossible. The repository currently has the worker protocol boundary (#4) but
no durable state implementation.

## Decision

1. The Rust supervisor owns one local SQLite control database in WAL mode. It
   stores jobs, stages, artifacts, checkpoints, retry budgets and migration
   versions. Python workers never receive a database handle or mutate SQLite.
2. Artifact completion is ordered: write and flush the temporary file, validate
   and hash it, atomically publish it into the artifact store, then commit its
   metadata and owning stage transition in one SQLite transaction before
   publishing a UI event. Temporary/partial files are never reported as final
   artifacts. The state crate also quarantines unregistered temporary files
   left when a process dies before its row transaction commits.
3. Recovery treats the database and filesystem as separate durable surfaces.
   A file written before its transaction is an orphan candidate; a committed
   row whose file is gone is a recoverable missing-artifact condition. Hash and
   size evidence decide whether a file can be rebound; stale paths alone never
   make an artifact valid.
4. State transitions and retry budgets are explicit and transactional. A
   retry has a finite persisted attempt count and a materially changed
   condition. Cancellation preserves reusable checkpoints.
5. Migrations are append-only, versioned and validated before a job resumes.
   Output, cache and source roots are independently configured and are never
   automatic fallbacks for the control database.

## Compatibility and migration

The first migration creates the durable tables and records schema version 1.
Future migrations append a new version and retain readers for existing rows;
no released migration is edited in place. The state API consumes the v1 worker
protocol through typed events, so protocol compatibility failures are surfaced
before a stage is resumed.

## Consequences

Crash recovery can preserve job metadata even when output media is missing,
and a single worker failure cannot corrupt unrelated jobs. WAL and local
storage add disk-management work, so low-space and missing-volume states remain
first-class recovery outcomes rather than hidden exceptions.
