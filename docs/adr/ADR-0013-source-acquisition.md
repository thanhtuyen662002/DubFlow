# ADR-0013 — App-owned source acquisition and supervisor-owned enumeration

Status: Accepted

## Native source controller and admission artifact v1

The installed supervisor's additive `source-serve` mode owns a separate source
database and private work namespace. Existing local-file commands/worker wire
and source schema remain unchanged. It acquires a lifetime OS claim before
writable SQLite open and `recover_running`: Windows opens the diagnostic lock
with no read/write/delete sharing; POSIX development uses nonblocking exclusive
flock. Process death releases ownership without guessing from a text file/PID.
This source-only handle proof is an additive implementation of ADR0008's
ownership requirement; its diagnostic metadata is not the create-new project
lock format and is never interpreted by the older project-lock primitive.
Only this compatible controller writes `control/sources.sqlite3`. A second
service cannot recover a live owner. The owner handle outlives the store and
active child. Linked roots, database sidecars and packet paths are refused.

The native admission receives the trusted installed manifest digest from its
host, checks raw bytes, every inventory file/hash/size and inventory equality
before executing Python. Checking only the entry point would allow altered
dependencies to execute before a Python verifier. Owned `-I -S -B` preparation
requires the native executable itself to be the inventoried supervisor in this
exact installed root, so another consumer binary cannot claim installed evidence
for the supplied runtime. Owned Python preparation
then applies the existing signature policy/full inventory verifier and SDK
factory. The parent independently recomputes sorted compact UTF-8 producer JSON,
requires original runtime/source/version/provider/reference/page-size identity,
and scopes each private packet by name, bounded size, hash, scan and stage.
The original ScanRecord captured before dispatch is the only commit token;
current-state rereads cannot authorize a late page.

New private admission artifact v1 is the closed public Producer JSON under
`control/source-admissions/<safe-scan-id>.json`. Its canonical fingerprint must
match the immutable SQLite binding. Create-new/write/fsync precedes first DB
admission. An orphan after a crash may be reused only with identical bytes; a
partial, changed or missing record cannot silently repair/repin the scan. Resume
loads its original page size and runtime/request pins before any producer
launch. A new runtime must retain the compatible original release or admit an
explicit new scan. Old schema2/unbound records without this artifact stay
inspectable through status/items; native resume refuses them. There is no schema3
migration, public worker/source/timeline/export format change or model upgrade.
Rollback preserves database, admission files and exports; the retained older
runtime/compatible controller is required for an existing bound scan. Never
delete producer pins or rewrite an old cursor to make rollback appear resumable.

One producer and one page are active per service; stdio/control queues and lines
are bounded. Heartbeat timeout, malformed packets and typed source failures stop
only the active scan and retain its last committed cursor. No automatic retry
loop exists. Pause/cancel invalidate durable dispatch first, send cooperative
cancel, then observe exit or terminate after a bounded deadline before emitting
the control result. A cancelled preparation creates no fake scan. Completion is
enumeration completion only, never a claim that media download completed.

Recorded native tests exercise actual child stdio and packet hashes, UTF-8
cross-language fingerprinting, OS exclusion, SQLite page commit, blocked-page
termination and reopen/resume at pageN. Admission/SDK in that process fixture is
explicitly substituted, not exposed as a production injection option. Other
tests reject tampered/unexpected imports before execution, changed admissions,
private URLs, wrong dispatch/scopes, malformed packets and unbounded input.
Installed real-provider dispatch, browser sessions, downloads/restart and full
desktop acceptance remain required. All exact-head/current-base lanes must run
for the successor before any readiness or merge decision.

## Owned page worker adapter over worker wire v1

The next producer adapter uses the existing version-1 JSONL envelopes, not a
new wire schema. `source_prepare` verifies the supplied immutable release
manifest hash, full inventory/signature policy, isolated owned Python origin
and worker path before constructing the existing verified SDK adapter. Its
identity binds the release manifest/source/version, source contract, worker
recipe, provider, canonical public reference and page size. The fingerprint is
canonical sorted UTF-8 JSON hashed with SHA-256; it is a verified producer
identity, never permission to repin an old scan. The supervisor must validate
this ready receipt and retain the original admission/producer before creating
or resuming a bound scan. A different release or recipe requires the retained
compatible runtime or an explicitly new admission.

Subsequent `source_page` commands carry that same fingerprint, the captured
integer dispatch revision and cursor. Duplicate/non-increasing dispatches,
changed producer, malformed input, completed producers and non-progressing
pages fail explicitly. This producer emits source-contract-v1 public identities,
titles, integer duration ticks and typed item failures. It omits media/subtitle
locators, headers and raw provider diagnostics. Authenticated session capture
and materialization remain separate work; no anonymous generic request receives
provider cookies. Source item failures remain data and do not poison valid items.

Internal source-ready/source-page packet version 1 is a bounded private adapter
artifact, not a project/export format. Unique packets are fsynced and atomically
published outside the immutable release; checkpoint envelopes carry their leaf
name and SHA-256. Publication failures remove only the worker's own partial file
and preserve previous packets. The native consumer must enforce the private root,
packet budget/schema/hash, job/stage, original producer/revision/cursor and then
commit through `checkpoint_page_checked`. Emitting a packet is not committing
durable state. Existing jobs, source contract and worker wire v1 are unchanged;
an older runtime does not execute these new commands or consume these packets.

The parent keeps private stdin open until terminal output. Heartbeats continue
during preparation/extraction. Control input has a bounded queue; overflow or
invalid scope fails instead of blocking cancellation behind queued requests.
Cancellation acknowledges the last supervisor commit without inventing an
artifact hash for the producer's latest uncommitted page. The producer exits
immediately, closing nested SDK Windows Job handles. EOF/parent loss exits as
failure, never completion. The native supervisor must durably invalidate the
dispatch and observe process termination before declaring pause/cancel complete;
its prior committed page remains the recovery point. Retryability is returned
as data; only the supervisor may authorize a bounded retry with changed conditions.

Boundary tests substitute bundle admission/adapter outputs and do not qualify
the installed SDK or durable native flow. Real subprocess tests additionally
exercise cancellation during a blocked page, parent EOF and wrong-scan control
using unchanged wire v1; their admission is explicitly substituted. Actual
installed admission/enumeration, process ownership, native checked commits,
scan restart, download and desktop intake still require product evidence.

## Durable dispatch guards and producer binding — source queue schema 2

The version-1 store accepted a late page while Paused and restored Running.
Its scan read preceded a deferred write transaction, allowing another callback
or control request to change state between validation and commit. Cursor alone
cannot identify a dispatch: pause/resume can retain the same cursor and wall
clock millisecond. This is a prerequisite repair within production leaf #167;
it does not itself connect the owned provider worker to native/desktop intake.

Schema 2 adds a monotonic `dispatch_revision` and nullable
`producer_fingerprint`. Admission of a new bound scan requires a lowercase
SHA-256 fingerprint computed by the supervisor from the verified runtime,
adapter, SDK/recipe and request pins. The database stores it once; admission
never upserts or repins an existing ID. A hash is an identity, not evidence of
authenticity: the native caller must first verify the producer and must never
store cookies, signed URLs or credential material as durable producer pins.

The supervisor captures the running ScanRecord before dispatch. Its checked
page callback supplies that original record, rather than reading a fresh token
to authorize an old result. An IMMEDIATE SQLite transaction validates runnable
state, producer, revision, cursor, provider/source and capacity before recording
items/failures and advancing the cursor and revision atomically. Bound scans
refuse the unchecked compatibility callback. Pause, resume, cancel and recovery
increment the revision even when the clock and cursor are unchanged, invalidating
in-flight pages. Paused or cancelled scans reject item progress; item writes
and aggregate counts commit or roll back together. Revision exhaustion is an
explicit failure before writes, never an overflow or silent wrap.

Migration runs inside an IMMEDIATE transaction. Version-1 rows, identities,
cursors, download states and source artifacts are retained; legacy rows receive
revision zero and no invented producer binding. They remain inspectable through
the compatibility API. New bound execution cannot reinterpret or repin a legacy
scan; retain its compatible runtime or explicitly admit a new scan. Future
schema versions are refused before adding columns. Version-1 binaries already
refuse higher schema versions. Rollback therefore retains the old runtime with
its pre-migration database or a schema-2-compatible runtime; never remove columns,
strip producer pins, relabel old scans or silently resume with different helpers.
No public source/worker/timeline/artifact contract or model format changes.

Real SQLite tests cover two competing connections, callbacks after pause/resume
at one millisecond, reopen/recovery, producer substitution and unchecked callback
refusal, version-1 migration, future schemas, cancelled/paused progress, injected
aggregate-update failure and exhausted revisions. Exact-head/current-base lanes
must rerun before readiness. Installed native provider dispatch, process lease,
per-scan ownership-aware recovery, authenticated sources, download/restart and
desktop integration remain full #167/#168/#175 work. `recover_running` is a
compatibility-wide recovery helper, not proof that another process is dead; the
production connector must establish ownership before recovering any scan.

## Anonymous generic SDK and playlist identity compatibility

The verified-bundle factory now constructs a generic adapter using the same
app-owned isolated Python, reviewed SDK wheel, inventory-pinned helper and media
muxer. The generic boundary receives no provider session bridge, Cookie,
Authorization or arbitrary headers. The helper separately enforces this boundary
before extraction. Source/display URLs with recognized credential query keys
are refused; signed media candidates remain transient inputs to the existing
materializer. Public SDK default headers carry no invented provider Referer.
Programmatic extraction retains disabled cache, plugins, external JS runtimes
and remote components. Bilibili/Douyin URL and cookie scopes are unchanged.

New generic SDK identities use opaque `sdk-v1-<sha256>` source IDs derived from
recipe `anonymous-generic-sdk-v1`, normalized extractor key and the SDK video ID.
GenericIE's filename-derived IDs additionally bind the canonical page URL to
avoid collisions between unrelated sites. Flat playlist entries use their own
`ie_key`, rather than the parent playlist's extractor identity. Full reinspection
must resolve the same provider/source key before materializing a flat item.
This allows extractor-owned redirect aliases to deduplicate without conflating
identical numeric IDs from different extractors. Legacy recorded/direct/CLI
adapter IDs are preserved and never rewritten or silently mixed with SDK IDs.

Generic playlist cursors bind the source URL digest, every verified producer pin,
the recipe and an integer offset. Each SDK request reads at most 100 slots plus
one lookahead, using lazy flat extraction. Deleted/private/invalid slots produce
item failures; they advance the page offset while remaining valid items continue.
A malformed or non-progressing page, changed cursor binding, nested playlist item
or discovery beyond 10,000 slots fails explicitly. An incomplete last page cannot
claim completion. The coordinator/supervisor remains responsible for checkpoint
transactions, identity deduplication and durable state; no worker writes SQLite.

Public source contract v1 and queue schema do not change. Existing jobs keep
their pinned adapter/runtime/identity. New SDK scans require a fresh scan ID;
an old cursor cannot resume with changed helper/runtime bytes. Rollback retains
completed artifacts and old queue rows and refuses incompatible SDK cursors.
Reacquisition with a legacy adapter may form a separate identity; no speculative
migration infers equivalence. Helper changes are pinned by each fresh bundle's
verified inventory, never by re-pinning an existing installed release.

Actual pinned SDK qualification retains the six recorded Bilibili page cases
and adds six generic page cases, full generic video/subtitle resolution and
generic session refusal. Network APIs are forbidden. Those receipts prove the
SDK/helper boundaries in their recorded environment, not live-site availability,
authenticated providers, Douyin creator support, desktop scan/recovery or full
Issue167/175 acceptance. Segmented HLS/DASH remains unsupported by the current
complete-object muxer and is never published as a complete media file.

## Bilibili selected-part identity compatibility

The video adapter retains an explicit bounded positive `p` selector. Bare URLs,
`p=1` and SDK `BVID_p1` keep the existing first-part `BVID` identity. Later parts
use distinct opaque `BVID_pN` identities (or `avID_pN` before the authoritative
BVID is resolved), with canonical URLs containing `?p=N`. The selected part is
preserved through SDK inspection and fresh canonical reinspection before download.
SDK/API metadata must confirm that same part; missing, different or malformed part
evidence fails as `SOURCE_CHANGED` before materialization. Ambiguous selectors and
parts outside the application bound of 1–10000 fail before calling the extractor.
Tracking parameters do not enter identity. This selects one part, not a whole
anthology or generic playlist.

Source contract v1 and supervisor schemas remain unchanged: `source_id` is already
an opaque provider value and dedup uses the complete provider/source key. No old
first-part artifact or queue row is renamed. Historical versions discarded `p`
before inspection and cannot prove which part was intended; no migration guesses
from those rows. Users must explicitly reacquire a later part in the corrected
release, producing a separate identity/output. Existing jobs retain their pinned
release/runtime. Rollback keeps prior completed artifacts and first-part jobs;
an older adapter rejects a new `_pN` id rather than treating it as part 1. Channel
page recipes/IDs/cursors are unchanged because their current flat entries select
the first part only. Full authenticated media, whole-channel and desktop recovery
acceptance remains separate.

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

## Public media CDN headers

An actual public Bilibili probe at 10aa6c8 retrieved metadata but the default
materializer's generic Python headers were refused by the CDN. Fixed public
Referer alone did not change the result. A separate diagnostic transport using
the pinned SDK's public User-Agent/Accept/Accept-Language plus video Referer
downloaded and decoded a 38,026,136-byte, 640x360 H.264/AAC MP4 of roughly nine
minutes without credentials. Report SHA-256:
`0de88d3439aeb6f89e0ade9a33d90408ee131c39ad00c5a895052210aec7533a`.
That injected diagnostic is historical evidence, not proof for the default path.

The reviewed descriptor now pins these three public SDK defaults. The bundle
factory uses a provider HTTP wrapper for direct and split materializers, with a
fixed public provider-root Referer. Only bounded Range/If-Range/Accept-Encoding
request overrides are allowed. Cookies, authorization, custom SDK headers,
header injection and overridden public defaults are rejected before HTTP. The
existing HTTP redirect boundary and hashed stream receipts remain unchanged.
No source/artifact schema change or credential metadata field is introduced.
Providers requiring cookies on their media endpoints still fail explicitly;
this public wrapper does not grant authenticated media support by inference.

Release integration now provisions the exact reviewed SDK before building either
bundle. It executes the source runtime smoke with staged and installed owned
Python using `-I -S -B`, requires the workflow source SHA and uploads both reports.
This checks the complete inventory and actual SDK imports without calling live
providers in a required lane. Source SDK health remains a narrow evidence scope;
the release evidence explicitly leaves live source, browser session and durable
intake/enumeration NOT_RUN. Windows Release must pass at the new source HEAD
before this packaging path has hosted staging/installation evidence.

## Bounded SDK channel pages — #167 follow-up

The private isolated helper adds `enumerate` beside the compatible version-1
inspect/health operations. It requests only a bounded flat SDK playlist window
(up to 100 entries plus one lookahead) with retries disabled, strips all media
locators/headers, preserves deleted slots and reports malformed item fields as
individual failures. Public SourcePage/SourceItem schema 1 remains unchanged.
Bilibili's pinned creator extractor uses provider pages; opaque cursors bind the
canonical channel, provider, absolute offset and all Python/helper/SDK producer
pins. A changed producer or foreign channel cannot reuse a cursor. Page offsets
are bounded below 10,000. A scan exceeding this limit fails explicitly and keeps
the previous checkpoint rather than declaring a truncated channel complete.

The existing EnumerationCoordinator/supervisor owns atomic page persistence,
deduplication and item status. Adapters do not write SQLite. Flat items contain
only stable identity/metadata. Selecting one for download reinspects its metadata
immediately and rejects an unexpected identity change before materialization;
expiring signed URLs are not stored in page records. Protected sessions still
cross only the scoped private stdin/cookie-jar boundary. Existing native process
lifetime, media resume and publication rules are preserved.

This pin has a Bilibili creator extractor and only a Douyin single-video
extractor. Douyin creator requests therefore return typed UNSUPPORTED before
network access, instead of falling through to generic HTML discovery and
claiming an empty channel succeeded. A supported Douyin creator implementation,
generic playlists, browser capture/authenticated media and durable desktop
scheduling remain acceptance work. Offset pagination describes a changing
provider listing, not an immutable snapshot; deduplication handles repeats, and
no consistency guarantee for videos added/deleted during a scan is inferred.

Deterministic tests exercise actual isolated child stdio using a recorded SDK,
cross-producer cursor rejection, page-N recovery, duplicates, poisoned/private/
deleted entries, capacity and fresh media inspection. The separate reproducible
`tests/source_adapter/qualify_sdk_pages.py` runs with explicitly selected isolated
Python and the exact reviewed upstream wheel. Its recorded paged extractor uses
real YoutubeDL/OnDemandPagedList to verify selected absolute IDs, bounded provider
page callbacks, deleted-slot preservation, lookahead and capacity. This is SDK
semantics evidence; it does not qualify live websites or installed desktop scans.
Existing jobs/artifacts need no migration. A paired runtime update retains old
producer pins; an old cursor remains resumable only with its original producer,
or the user starts an explicit new scan with identity deduplication.

The source runtime release smoke now also runs those six recorded SDK cases
under each staged/installed owned isolated interpreter. It takes the helper,
descriptor and wheel from the already verified release inventory, rechecks
the bounded helper/descriptor bytes before execution and rejects a foreign
interpreter or previously imported SDK. Helper execution uses the verified bytes;
the probe blocks DNS and socket connection APIs. A failed case fails the existing
required Windows qualification step, without adding live-site dependencies.
The additive `offline_sdk_pages` report preserves the existing runtime-health
scope and explicitly leaves browser sessions, live sources and durable desktop
scans unqualified. This is qualification evidence only; public source/worker
contracts, producer pins, SQLite and old artifacts/cursors are unchanged.

## Public URL-only flat playlist entries

The actual pinned SDK's CCC playlist extractor returns selected entries with a
public URL and extractor key but no ID or title. The exact858 live three-page
probe refused all six entries, while anonymous full inspection of the first URL
returned its stable ID and media candidates. These entries need metadata
resolution before SourceItem admission; a missing flat ID is not a deleted item.

Generic enumeration resolves only selected entries missing an ID or extractor
namespace through the existing owned anonymous SDK inspection boundary. Known
flat identities remain lazy, and lookahead is never resolved. Public URL and
credential-query validation precedes each resolution. Known private entries
are refused without inspection. A resolved known ID or non-generic extractor
cannot change. Typed inspection failures retain their code/retryability as
per-item failures; siblings and cursor progression continue. Page records contain
identity/title only, with no signed media locators or headers.

Source identity recipe `anonymous-generic-sdk-v1` remains unchanged so full
inspection, flat selection and later download agree. Enumeration recipe becomes
`anonymous-generic-paging-resolve-v2`; old mapping cursors return
CHECKPOINT_INVALID on the new adapter before SDK/network work. Retained old
runtime versions resume their original scans. A new runtime starts an explicit
new scan with identity deduplication; no existing cursor, SQLite row, artifact,
job producer or public schema is rewritten. Rollback preserves old exports and
local-file processing, and may again refuse URL-only flat entries.

Focused deterministic regressions cover selected-only resolution, lookahead,
private/credential refusals, per-item network failure and identity substitution.
The actual pinned-SDK qualification additionally resolves two consecutive
two-item URL-only pages with recorded extractors and network forbidden. The
required staged/installed Windows source smoke requires this evidence. Live
public webpage evidence remains separate and does not qualify durable desktop
scans, browser authentication or the full #167/#175 product gate.

## Public Bilibili descriptions at the strict metadata boundary

The actual public SDK returned the requested `BV1o4411M71o_p2`, but the adapter
rejected its ordinary 297-character description containing six LF paragraph
breaks before media download. Line breaks in auxiliary metadata are not evidence
that the source identity changed.

Bilibili bounds the raw optional description at 16,384 characters before
normalizing CR/LF/TAB runs to a space. Empty descriptions become null. Other
ASCII controls, malformed types and oversized metadata remain typed refusals.
Title, URL, source/part identity, credential and stream validation remain strict.
This is a provider presentation normalization into the existing single-line
SourceItem contract; description text never becomes dialogue semantics.

Source schema 1, identities/cursors, SDK/helper pins, worker/producer/model
versions and SQLite are unchanged; no migration rewrites previous metadata or
media. Older consumers read the same bounded field. A retained old adapter may
again refuse multiline public descriptions on rollback, while preserving prior
validated exports and the local-file route. Existing pinned jobs retain their
original runtime; this correction does not grant producer rebinding.

Whole-adapter SDK mapping tests cover multiline/empty descriptions, raw limits,
control refusals and unchanged selected-part identity/title checks. Actual live
evidence uses the current adapter with a previously verified standalone owned
SDK/media runtime; it is not installed current-release or desktop evidence.
All required exact new HEAD/current-base lanes must rerun. Generic playlists,
browser sessions, authenticated providers, Douyin creators and durable desktop
enumeration remain acceptance work under #167/#175.
