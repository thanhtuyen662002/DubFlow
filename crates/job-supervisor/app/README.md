# DubFlow supervisor

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
