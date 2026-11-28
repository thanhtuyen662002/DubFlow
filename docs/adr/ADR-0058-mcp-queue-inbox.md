# ADR-0058: MCP dubbing requests go through the desktop queue (inbox)

Status: Accepted. Issue #998. Builds on ADR-0049 Phase 4b (the MCP server) and the DubFlow-wide MCP work (#994, #996).

## Context

Agents drive DubFlow through its MCP server (`engine/dubflow/mcp`). Documentary and product runs are run folders the
app finds by scanning, so the MCP server can make them itself. Dubbing jobs are different:

- The queue (Hàng đợi) lives in the desktop app (`dubflow.queue.v2` in its web storage). The app starts one job at a time
  through `dubflow-supervisor.exe`, polls its status file, and owns retries, new versions and review.
- A job's AI translation key and Vibi.pro voice key come from Windows Credential Manager through the host.
- Two supervisors started side by side compete for the GPU, the models and the disk.

A supervisor started by the MCP server would be invisible in Hàng đợi, would run beside the app's own job, and could not
use the user's saved keys.

## Decision

The MCP server does not start dubbing jobs. It **asks the running app** to queue them:

1. `queue_dubbing_job` checks the request (an existing local video, a voice from the catalog, known options) and writes
   `<data root>\control\inbox\<id>.json` atomically. The id is `mcp-<time>-<random>` and becomes the queue job id.
   Schema 1:
   `{"schema_version": 1, "kind": "dubbing", "id", "created_at", "source_path", "title", "options": {"dub", "voice_id",
   "source_language", "burn_in", "blur_auto", "separate_background", "multi_voice", "translation_engine"}}`.
2. The app's host command `take_inbox` reads at most 20 requests of at most 64 KiB each and checks them again (schema,
   id form, an absolute existing file, option values). It moves each accepted file to `inbox\accepted\` and each refused
   one to `inbox\rejected\` with a `.reason.txt`, then returns the accepted ones. The frontend polls it every few seconds,
   once the voice catalog has loaded. An option a request leaves `null` is the user's own default; a manual blur
   rectangle never carries over (an agent's video gets automatic blur or none), and Hachimi only once the user accepted
   its terms in the app.
3. The frontend adds each request as a queue job **with the request id** and sets its options. An agent's video starts
   on its own once no other video is running; the user's own queued videos still wait for "Bắt đầu … video đang chờ".
   It never opens the one-time AI question: it translates with AI only when the user has already chosen AI, and
   otherwise offline.
4. The MCP server reads progress from the supervisor's own status file, `control\jobs\<id>.json`. Before the app has
   started the job, it reports `WAITING_FOR_APP` (request still in the inbox) or `QUEUED` (accepted, waiting its turn).

## Consequences

- The app must be open for an agent's dubbing to run. The answer says so while the request waits.
- One queue and one supervisor at a time; jobs keep the user's saved keys and settings, and show in Hàng đợi like any other.
- The inbox is a folder inside the user's own data root. Anything that can write there can already run DubFlow as the
  user. The host checks every request as untrusted input: it never runs a command from a request, it only queues a file
  the user's account can read.
- Link requests (download first) are not part of schema 1: an agent uses `download_video`, then queues the file.
