# ADR-0013 — App-owned source acquisition and supervisor-owned enumeration

Status: Accepted

## Context

DubFlow accepts local files without a network dependency, while optional source
acquisition needs to handle generic URLs, Bilibili, Douyin, multiple URLs and
channel enumeration.  Provider sites and browser sessions are unstable and
must not become part of the canonical project format or a required CI lane.
Enumeration can produce thousands of items and a crash after page N must not
restart at page one or mark a partial scan complete.  Workers also must not
write supervisor-owned SQLite state.

## Decision

Source acquisition is split into three boundaries:

1. `SourceAdapter` implementations own provider identity, metadata, subtitle
   candidates and provider error classification.  Generic URL logic is kept
   separate from Bilibili and Douyin adapters.  Adapters return structured
   `NOT_FOUND`, `PRIVATE`, `AUTH_REQUIRED`, `RATE_LIMITED`, `SOURCE_CHANGED`,
   `NETWORK` and `UNSUPPORTED` errors.  A provider adapter may use an
   app-owned, pinned yt-dlp executable through an argument-vector transport;
   shell evaluation and credentials on command lines are prohibited.
2. `MediaMaterializer` owns HTTP/local materialization.  It validates HTTP(S)
   URLs, bounds redirects, strips credential headers across hosts, resumes
   `.part` files only after a valid range response, verifies content length and
   SHA-256, and atomically publishes the final path.  HLS/DASH candidates are
   handed to a provider muxer; they are never silently saved as a playable
   file.  Temporary files and failure records retain no query-token or cookie
   values.
3. The supervisor-owned `dubflow-source-queue` SQLite crate owns scan cursors,
   page checkpoints, deduplicated source identities, per-item status/retry/
   download progress and poison-item failures.  Each page and cursor is one
   transaction.  A no-progress cursor, path/control-character input, or more
   than 10,000 admitted identities is rejected before a partial commit.
   Pause, cancel, explicit resume and restart recovery are state transitions;
   reopening the database preserves page N and byte progress.  The Python
   enumeration coordinator can call a supervisor checkpoint sink but never
   opens SQLite itself.

Browser/session credentials cross the adapter boundary through an
OS-protected provider integration owned by the application.  The source
contract receives only capability/error outcomes and redacted diagnostics;
credentials never enter manifests, URL identity, subprocess argv or logs.

## Compatibility and rollback

`contracts/source/schema-v1.json` remains additive: page failures are optional,
and existing fixture adapters and local-file jobs continue to work.  A queue
database has schema version 1 and is independent from job-state migrations.
An adapter or provider can be disabled while queued local jobs continue.  A
failed or cancelled source item remains visible as failed/cancelled; an
incomplete scan cannot be represented as completed.  Required CI uses recorded
fixtures only.  Live provider probes, when available, run in a separate lane
and cannot gate local-file operation.

## Consequences

- Production source work is resumable and deduplicated without making provider
  availability part of deterministic CI.
- The queue schema is intentionally supervisor-owned; adding a worker-side
  SQLite writer would violate the worker protocol and requires a new ADR.
- A provider requiring HLS/DASH muxing must supply an explicit app-owned muxer;
  direct HTTP materialization reports `UNSUPPORTED` until then.
