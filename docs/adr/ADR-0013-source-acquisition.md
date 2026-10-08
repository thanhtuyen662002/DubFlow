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

## Complete stream materialization — Issue #167 continuation

`StreamMaterializer` now accepts selected complete video/audio MP4 objects,
including Bilibili DASH `baseUrl` objects that contain their own initialization
and media data. A URL ending in `.mpd`/`.m3u8`, a playlist, or incomplete
fragment remains unsupported. This does not implement segment enumeration.
Both local demux and mux processes restrict protocols to `file` and demuxers
to MOV/Matroska; signed source URLs never become process arguments. Selected
streams are copied into MP4 without reencoding, `-shortest`, or implicit
audio substitution. Native probes require bounded timestamps, matching starts
and durations within one second, and output codec/duration preservation before
fsync and atomic publication. Unsupported/mismatched sources leave an existing
output intact and report a scoped failure.

The caller supplies an absolute app-owned runtime root and SHA-256 pins for
FFmpeg/FFprobe. Pins are verified before each launch. A source scratch namespace
is a digest of the selected candidates and producer fingerprint; it stores
only digest/size/version receipts, never raw candidate URLs or query tokens.
Each completed stream is verified by size and SHA-256 on worker restart. A
crash or cancellation between streams retains the healthy completed stream.
An OS file lease serializes writers to the destination and is released by
process exit; workers continue to own no durable SQLite mutations.

HTTP partial resume now additionally requires a caller-pinned final hash or a
strong ETag, a matching locator digest, and verified private prefix receipt.
`If-Range` and returned validators protect against a changed remote object.
Unbound legacy partials are restarted rather than spliced using length alone.
Local partials are compared with the current source prefix before reuse.
These private receipts use schema version 1; an unavailable/corrupt receipt
causes safe reacquisition. Existing completed source artifacts, canonical
timeline identity, source schema v1 and supervisor queue schema do not change.
Rollback may ignore the new scratch, but must never consume a mux scratch as
a completed source. The installed intake must explicitly configure the muxer;
absence yields `UNSUPPORTED`, not a video-only success.

Deterministic tests protect interruption, changed validators/producer, corrupt
receipts, cancellation, writer contention and atomic output preservation. The
opt-in `tests/source_materializer/native_stream_probe.py` exercises a real
local HTTP interruption and native H.264/AAC copy/decode. Generated media and
runtime binaries stay outside Git. Neither this bounded probe nor the generic
soak rehearsal proves live provider authentication, durable channel integration,
or full installed intake; all remaining #167/#175 acceptance stays open.
