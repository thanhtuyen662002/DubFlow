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

## Native extractor verification and bounded process — #167 follow-up

An absolute file name alone does not prove an app-owned approved producer.
Native yt-dlp construction requires an explicit trusted runtime root and the
approved manifest's SHA-256. Link/junction paths, files outside that root,
missing pins and changed binaries fail before launch. Each invocation rechecks
the pin with bounded reads; it does not learn/trust a hash from a downloaded
binary. Provider constructors forward the same fields. Existing injected
recorded transports remain independent of native runtime availability.

This tightens constructor compatibility: callers that supplied only an
executable must supply the verified manifest pin/root. Otherwise they receive
an actionable `UNSUPPORTED`/`repair_runtime`; no PATH fallback is permitted.
The API accepts caller-owned pins; installing and license-verifying the actual
extractor release and wiring these pins into desktop intake remain required
production work, not evidence supplied by an injected runner.

Native metadata inspection uses private temporary file handles rather than
unbounded pipe capture, a deadline, 4 MiB metadata/64 KiB diagnostic budgets,
bounded UTF-8 decoding and kill/wait on failure. Raw metadata/errors are not
logged. Per-invocation flags ignore system/user config and plugins, disable
cache writes and unconfigured JS runtimes/remote components. A site requiring
an external JS helper still needs a separately approved app-owned helper;
system Deno/Node and mutable helper downloads are not a production substitute.
These options follow the [upstream CLI contract](https://github.com/yt-dlp/yt-dlp#usage-and-options).
Real development-runtime child probes cover UTF-8/exit status, timeout and
stdout/stderr floods with process reaping; they do not qualify an installed
yt-dlp binary, live website, parent-crash containment or authenticated session.

## Protected provider sessions and isolated SDK inspection — #167 follow-up

The Windows bridge stores only a bounded DPAPI ciphertext envelope, using
current-user protection with UI forbidden and provider-specific entropy. The
encrypted payload binds schema, provider, expiry (at most 24 hours) and one
validated Cookie header. Unknown providers, malformed/header-injection input,
changed scope, expired/tampered/overlarge records and foreign provider ciphertext
fail as `AUTH_REQUIRED`; plaintext credentials are never added to job state.
Replacing a saved session is atomic. Clearing/re-authentication changes a
condition explicitly; there is no automatic plaintext migration/fallback.
Schema 1 is a new optional private auth store; older apps may ignore it. Windows
user accounts or machines cannot share this store as a browser sync format.

Authenticated inspection uses a separately approved runtime/helper/SDK archive
with explicit SHA-256 pins and a bounded private stdin request. The helper runs
under the owned Python with `-I -S -B`; an in-memory cookie jar restricts cookies
to `.bilibili.com` or `.douyin.com` and HTTPS. Global Cookie/Authorization flags,
plaintext cookie files, system/user config/plugins, default JS runtimes and
remote helper downloads are not used. Provider adapters forward the immediate
bridge capability only to this boundary. The helper strips credential jar and
HTTP-header fields from returned metadata, suppresses raw diagnostics and
returns bounded typed failures. A cleared/expired session prevents the provider
call, leaving independent local jobs available. Public SDK calls use an empty
jar. The Bilibili transport preserves the adapter's required `code/data` envelope.

Authorization headers, browser capture/consent UI, per-cookie browser domain
import beyond these provider scopes, and installed SDK provisioning remain
separate integration work. Caller-provided checksum pins must come from the
approved release inventory; observing a file hash does not approve that file.
The portable runtime integrity/signature boundary must cover its DLL/standard
library dependencies too; the three helper pins are not a complete installer
integrity claim. This does not qualify parent-crash containment or live sites.

Deterministic tests use recorded transport/SDK capabilities; native Windows
tests exercise actual DPAPI, reload, tamper/provider binding and child stdio.
The Soak lane retains its existing Release/Soak job and adds an isolated Windows
session boundary job without model/GPU/site dependencies. Actual SDK wheel
2026.8.19 (SHA-256 `1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf`)
was probed locally: its cookie jar sends the synthetic session to the provider
HTTPS API and omits it for HTTP, unrelated CDN and lookalike domains. This is
development SDK semantics evidence, not authenticated video acquisition.
SDK API/options were checked against [upstream source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py)
and the [official wheel inventory](https://pypi.org/project/yt-dlp/2026.8.19/).

## Reviewed SDK provisioning and bundle factory — #167 follow-up

`download/assets/yt-dlp-sdk-v1.json` pins the exact reviewed PyPI wheel, size,
SHA-256 and embedded Unlicense notice. The build command
`python -m engine.dubflow.download.runtime --runtime-root <owned-build-runtime>`
uses the bounded resumable HTTP materializer to place it in `runtime/source`.
A correct existing archive is reused offline; a changed archive is rejected
without automatically replacing code. Nothing is extracted or installed with
pip, and no system interpreter is discovered. The existing bundle builder can
copy/inventory this optional runtime directory. Older releases without the SDK
keep local files usable and report source runtime unavailable.

`provider_from_verified_bundle` consumes the release bootstrap/manager's already
verified artifact inventory. It verifies the reviewed descriptor, exact SDK and
notice, owned Python, helper and FFmpeg/FFprobe against those approved pins;
it never approves their currently observed hashes. Complete bundle integrity
and signature verification, including the Python DLL/stdlib dependencies, is
the caller's precondition. The private helper request gains an additive
`health_check` operation that loads the actual SDK under isolated/no-site Python,
constructs an empty-cookie downloader without network acquisition and returns
its producer/runtime identity. Missing/mismatched health evidence fails explicitly.
The default version-1 inspect request remains compatible.

Both provider adapters receive the pinned helper and local mux boundary. Douyin
SDK mapping now preserves each format's actual audio/video codec and protocol
instead of pretending the best video is a combined play URL. Split streams use
the selected companion; HLS/segment-manifest candidates remain explicit
unsupported cases until bounded segment acquisition is implemented. Historical
recorded provider response mappings remain compatible. SourceItem schema 1 and
canonical integer ticks are unchanged. The optional source runtime directory is
release-owned code and follows paired updates/retained-runtime policy; it is not
a mutable model cache or an automatically updating downloader.

The build preparation and provider factory are not yet wired into the release
workflow or desktop intake. Installed production acquisition, browser capture/
consent, durable enumeration and parent-crash containment remain open. Native
SDK/runtime/media probes are distinct from deterministic recorded fixtures.

Native build-stage probe on 2026-10-08: 2,449 isolated Python runtime files were
copied from historical candidate rc13 after matching its anchored manifest and
each original file checksum. The exact upstream wheel was acquired through the
new provisioner and then reused offline. The actual helper reported SDK
2026.08.19, Python 3.12.10, its owned executable/prefix and isolated/no-site flags.
Both factories processed recorded split metadata with real local HTTP media and
actual FFmpeg mux/probe/decode into H.264/AAC. Report SHA-256:
`b62dd669d59efeac500686340971a76337a878e77778bf9ab53f53e6c07dc58f`.
This is a constructed build-stage runtime, not a newly installed/signed release.
Media is synthetic; provider metadata is recorded; no live source, credentials,
browser capture, channel enumeration or durable desktop intake is qualified.

## Windows source process lifetime

The source runner assigns an anonymous, non-inherited Windows kill-on-close
Job Object before the private request writer starts. The isolated SDK helper
cannot import the SDK before it receives a valid complete request. Parent death
before assignment closes stdin, causing the actual helper to exit without loading
the archive. After assignment, closing the parent's job handle terminates the
helper and descendants; cleanup closes it after normal exit, timeout and failure.
An assignment/configuration failure prevents the writer from sending credentials
and yields a typed runtime-repair outcome. The source-owned helper follows the
same OS design as native TTS without requiring an unmerged TTS version.

Actual Windows tests retain process handles to rule out PID reuse. They hard-kill
the parent while assignment is deliberately paused and observe the real SDK
entrypoint exiting on EOF; a second case waits for a real initialized helper and
descendant, kills the parent and observes both handles signaled. They use recorded
code/development Python, not a new packaged/live provider qualification. The
existing Windows Soak source job executes these regressions. POSIX retains bounded
direct-child cleanup; its complete parent-death/tree behavior is not qualified.
The legacy standalone CLI has no private request handshake, so this evidence
does not close its initial-launch handoff race. The approved bundle factory uses
the SDK helper path; a future standalone profile needs separate startup evidence.

The release smoke entrypoint `scripts/release/source_runtime_smoke.py` requires
the expected source SHA and calls the existing complete release tree/signature
verifier before native execution. SDK health additionally requires both Python
prefixes and every isolated import search root to remain inside the bundle;
an external installed interpreter/stdlib cannot silently complete this check.
Its report is written outside the bundle and declares SDK/runtime health only,
with website/session/intake/enumeration NOT_RUN and production qualification false.
It is ready for staged/installed workflow integration; mocked orchestration tests
do not qualify a complete release or replace actual native execution evidence.
