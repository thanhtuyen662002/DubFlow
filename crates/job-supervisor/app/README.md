# DubFlow supervisor

## Native source service

`{"command":"start_video","scan_id":"film-1","provider_id":"generic","source_ref":"https://example.com/film.mp4"}`
admits a single public video through that provider's real inspection adapter.
Provider IDs are `generic`, `bilibili` and `douyin`. The one-item scan completes
enumeration only; the item remains discovered until explicit `download` succeeds.
No playlist cursor or adjustable collection capacity is accepted for this command.
Video mode uses producer recipe 2 and is pinned in its admission fingerprint.
Resume/download recover that original mode after restart. Existing `start`
channel/playlist scans retain recipe 1 and its original admission bytes.

The installed binary also exposes a separate source database service:

```text
dubflow-supervisor source-serve --root <installed-version> --data-root <private-data> --manifest-sha256 <trusted-installed-manifest-digest>
```

The desktop/installer must supply the already admitted manifest digest. The
service verifies the exact full inventory before executing owned Python with
`-I -S -B`; the worker additionally verifies release signature policy. The digest
must be supplied to the supervisor executable inside that exact installed bundle;
an external binary cannot claim a different runtime. Data must
be outside the immutable version directory. An OS lifetime lock protects
`control/sources.sqlite3` before writable open/recovery. A second service fails
without recovering the first service's running scans. Retained lock metadata is
diagnostic, and is replaced only after obtaining exclusive OS ownership.

Admission hashes every inventoried file during one tree walk. A final metadata
walk rechecks the exact file set, sizes and symlink/reparse nodes, including
leaves already hashed. Directory ancestry is checked before/after walking and
before return, then the trusted manifest digest is rechecked. Missing, unexpected,
case-duplicate, wrong-size/hash and linked entries are refused before imports.
No prior admission cache or model/file hash exemption is used. Internal paths
remain canonical; the existing Windows external-path conversion is applied to
both Python executable and script arguments, matching the normalized command
roots so the worker's strict runtime-origin checks remain effective.

Source commands and events use this service's bounded JSONL stream:

```json
{"command":"start","scan_id":"scan-1","provider_id":"generic","source_ref":"https://media.ccc.de/c/congress/2025","page_size":25,"max_items":10000}
{"command":"status","scan_id":"scan-1"}
{"command":"items","scan_id":"scan-1","offset":0,"limit":100}
{"command":"pause","scan_id":"scan-1"}
{"command":"resume","scan_id":"scan-1"}
{"command":"cancel","scan_id":"scan-1"}
{"command":"download","scan_id":"scan-1","identity_key":"generic:example-id","resume":false}
{"command":"pause_download","scan_id":"scan-1"}
{"command":"download","scan_id":"scan-1","identity_key":"generic:example-id","resume":true}
{"command":"cancel_download","scan_id":"scan-1"}
{"command":"shutdown"}
```

References must already be canonical public adapter references. For Bilibili,
use `https://space.bilibili.com/<numeric-id>/video`; Douyin uses
`https://www.douyin.com/user/<id>`. The owned SDK may return a scoped UNSUPPORTED
for unsupported provider enumeration. This service runs one producer at a time,
keeps one page in flight, and returns only bounded item windows. Ready admission
persists the original public producer record/page size in
`control/source-admissions/<scan-id>.json`, cross-checked against the SQLite
producer fingerprint on resume. Legacy scans without that record are inspectable
but cannot be repinned. Runtime changes are rejected before extraction.

Only a hashed, scoped packet matching the original dispatch may commit a page.
Pause/cancel durably invalidate that dispatch before stopping and observing the
child. A crash is recovered as paused under exclusive ownership; explicit resume
uses the last committed cursor. Source failures preserve their typed code and
retryability; no automatic retry is performed. Repair the condition before
resuming. Cancellation before ready produces no false durable scan.

After a producer-bound scan completes, `download` materializes one selected
discovered item using a fresh owned SDK worker. Fresh inspection must reproduce
its provider, ID, public canonical URL and identity key. The host chooses no
media or scratch path. The native service owns byte/failure/final item commits;
worker packets retain the original attempt revision and identity while each
successful checked transaction advances the native snapshot. `items` exposes
observed transfer bytes, optional total, error code and verified final media.
Transfer bytes include reused verified streams and can differ from muxed size;
they are not a guarantee of bytes retained after reboot.

Pause invalidates the attempt before observing the child exit. Partial streams
remain under `source-work/materialization/<manifest>/<scan>/<identity-hash>`.
Only explicit `resume:true` with the original admitted runtime can resume an
interrupted downloading row. Failed/cancelled items cannot automatically retry;
another discovered sibling can still run. Enumeration remains completed and
its cursor is preserved. Generic scan pause/cancel commands also control an
active download; shutdown pauses it. Completed owner recovery advances the
in-flight item epoch before admitting new callbacks.

Native streaming SHA verification requires a successful producer exit and
retains the verified file handle through publication. Windows denies concurrent
writes; final admission also denies deletion, and file identity is checked
before/after publication. Media publishes by a same-volume hard link under
`source-media/<manifest>/<scan>/<identity-hash>/<content-hash>.mp4`; filesystems
without this capability refuse publication. A failed checked commit retains
an orphan for rehashing during explicit original-producer recovery. A filename
alone never authorizes reuse. Final packet removal is best effort after commit;
private completed stream retention/GC remains a storage-policy integration task.
Windows mux/probe subprocesses use the existing source-owned kill-on-close Job
boundary, including descendants; worker parent loss closes these handles.

The native recorded-process tests use a substituted SDK/worker admission while
exercising real Python stdio, packet hashing, OS ownership and SQLite reopen.
Download tests additionally cover actual recorded-child stdio, checked item
progress, publication/orphan recovery, pause/cancel/reopen, changed producer,
invalid hash/exit, foreign/late packets and replaced file handles. The recorded
media bytes and adapter are fixtures. The named Windows process-tree test uses
real child/grandchild processes, without claiming a live provider or real mux.
Installed live acquisition, authentication, retries after changed conditions,
single-video desktop intake and automatic production handoff still need their
#167/#168/#175 acceptance evidence.

`dubflow-supervisor` is the durable process owner for the local-file
production profile.  It owns the SQLite connection, launches only the
app-owned Python runtime, validates every worker JSONL envelope with the
versioned worker protocol, and commits checkpoints and artifacts through
`dubflow-job-state`.

The binary exposes a small JSONL command stream on standard input and emits
JSONL events on standard output.  The desktop host keeps the process alive for
the lifetime of the app.

## Runtime layout

The `--root` argument is the installed DubFlow version directory.  The
supervisor resolves these paths below it and never invokes a Python or FFmpeg
executable from `PATH`:

```text
<root>/runtime/python.exe
<root>/runtime/media/ffmpeg.exe
<root>/runtime/media/ffprobe.exe
<root>/app/engine/dubflow/worker/production_job.py
<data-root>/models/
<data-root>/control/jobs.sqlite3
```

On POSIX development hosts the executable candidates use the corresponding
extensionless names.  The release builder supplies the Windows layout.

## Commands

Each input line is one JSON object:

```json
{"command":"ping"}
{"command":"start","job_id":"job-1","source_path":"C:\\video.mp4","output_dir":"C:\\out","source_language":"auto","target_language":"vi","enable_dubbing":false,"burn_in_subtitles":true}
{"command":"status","job_id":"job-1"}
{"command":"cancel","job_id":"job-1","reason":"user requested"}
{"command":"shutdown"}
```

`start` accepts an omitted `job_id`; the supervisor generates a path-safe ID.
`source_path` and `output_dir` must be absolute paths.  The source must exist;
the output directory is created by the worker.  Runtime/model paths are not
accepted from the UI, so a request cannot silently select a system Python,
FFmpeg, or model directory.

Events include `accepted`, `progress`, `checkpoint`, `status`, `retrying`,
`completed`, `failed`, `cancelled`, `error`, `pong`, and `shutdown`.  A failed
job is reported as an event and does not terminate the supervisor; subsequent
jobs can still run.  The SQLite state is reopened and interrupted running
stages are marked `recovering` when the supervisor starts.  Reissuing `start`
with the same job ID resumes that durable job and lets artifact reconciliation
decide whether a checkpoint is reusable.

For the desktop host's one-shot launch path, the binary also accepts:

```text
dubflow-supervisor run --root <version-root> --data-root <app-data-root> \
  --job-id <id> --source <absolute file> --output-dir <absolute dir> \
  [--status-path <absolute JSON file>] [--model-root <data-root child>]
```

The command waits for the worker, emits the same progress events, and writes
an atomic status file containing `status`, `job_id`, and `output_path`.  A
lightweight cancellation command can durably cancel a queued or recovered job
without loading the Python runtime:

```text
dubflow-supervisor cancel --data-root <app-data-root> --job-id <id>
```

The release layout may use either `runtime/media` or the legacy
`runtime/ffmpeg` directory; both are validated as app-owned paths.  The model
directory is created below `--data-root` on first start and an explicit
`--model-root` is rejected if it escapes that data root or the installed
version root.

The worker is intentionally a child process.  It never receives a SQLite
handle and it cannot make a durable state transition by itself.  A cancelled
worker is terminated by the supervisor and the job is durably marked
`cancelled`; a hung worker is killed after the protocol heartbeat deadline.

## Protected source sessions

The installed `source-serve` controller accepts private stdin JSON commands:

- `session_save`: `provider_id` (`bilibili` or `douyin`), `headers` (one Cookie
  header, at most 2048 printable ASCII bytes), and integer Unix `expires_at`
  (future, at most 24 hours).
- `session_status`: only `provider_id`.
- `session_clear`: only `provider_id`.

Use the existing admitted `--data-root`; callers cannot select a credential
filename. The owned worker writes only current-user DPAPI ciphertext beneath
`control/source-sessions`. Never put headers into argv, URLs, diagnostics,
SQLite or manifests. Request debug output is redacted. The host supplies the
headers through its private control pipe; browser capture/login UI is separate.

Each accepted operation emits `source_session_preparing`, then `source_session`
with `provider_id`, `operation` and `state` (`ready`, `missing`, or status-only
`expired_or_unavailable`) after the validated worker actually exits successfully.
Worker/protocol/deadline failures emit `source_session_error` with a typed code
and no automatic retry. Invalid requests emit `SOURCE_REQUEST_REJECTED`.
Operations are refused while source work is active; shutdown remains available
during session work. Generic sources stay anonymous. Existing saved provider
records are connected to the verified adapter on new worker preparation;
expired or unavailable records require authentication before acquisition.

This API needs the matching pinned native/worker release. Older installed
releases retain their previous protocol and acquisition behavior. Windows
Release requires synthetic-cookie native/DPAPI and owned-factory proofs in both
staged and installed source-runtime reports; this is not live-login approval.
