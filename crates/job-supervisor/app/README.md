# DubFlow supervisor

## Native source service

The installed binary also exposes a separate source database service:

```text
dubflow-supervisor source-serve --root <installed-version> --data-root <private-data> --manifest-sha256 <trusted-installed-manifest-digest>
```

The desktop/installer must supply the already admitted manifest digest. The
service verifies the exact full inventory before executing owned Python with
`-I -S -B`; the worker additionally verifies release signature policy. Data must
be supplied to the supervisor executable inside that exact installed bundle;
an external binary cannot claim a different runtime. Data must
be outside the immutable version directory. An OS lifetime lock protects
`control/sources.sqlite3` before writable open/recovery. A second service fails
without recovering the first service's running scans. Retained lock metadata is
diagnostic, and is replaced only after obtaining exclusive OS ownership.

Source commands and events use this service's bounded JSONL stream:

```json
{"command":"start","scan_id":"scan-1","provider_id":"generic","source_ref":"https://media.ccc.de/c/congress/2025","page_size":25,"max_items":10000}
{"command":"status","scan_id":"scan-1"}
{"command":"items","scan_id":"scan-1","offset":0,"limit":100}
{"command":"pause","scan_id":"scan-1"}
{"command":"resume","scan_id":"scan-1"}
{"command":"cancel","scan_id":"scan-1"}
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

The native recorded-process tests use a substituted SDK/worker admission while
exercising real Python stdio, packet hashing, OS ownership and SQLite reopen.
They do not qualify installed live sources, authentication, download scheduling
or desktop intake. Those remain #167/#168/#175 acceptance work.

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
