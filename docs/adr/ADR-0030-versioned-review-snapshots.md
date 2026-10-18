# ADR-0030 — Versioned review snapshots and atomic correction history

Status: Proposed by #174 / PR #185. Production integration and installed
qualification remain required before acceptance.

## Corrected canonical timeline derivation (Draft integration)

The private pure timeline adapter consumes native-admitted original timeline
v1 bytes and exact before/after snapshots. It verifies every bound finding
against the original source text, translation and rational cue interval, then
changes only the selected translation. All other cues, source clock mapping,
timing and original confidence remain intact. Human translation edits do not
claim improved ASR confidence. Voice-only edits return the original timeline
bytes, preserving subtitle cache identity. B1 has no voice and cannot become
B2 through this path. The existing logical downstream plan remains conservative;
actual B1 execution must exclude unavailable TTS/mix stages explicitly.

No released timeline format changes or implicit migration occurs. The adapter
does not bind an arbitrary caller's hash, write files/SQLite, render, publish a
graph or authorize undo. Native source/archive admission, complete validated
replacement outputs, retained media and CAS remain required before UI enablement.

## Native corrected timeline preparation

The opt-in retained-mix successor also derives a sealed private timeline from
the already archived original timeline and native before/after snapshots. The
native transformation independently re-derives the exact correction proposal,
checks all mapped findings against original source/text/rational ranges, and
changes only the selected translation. Cues without findings, source clock,
duration and measured confidence retain their original values. Voice-only
edits retain exact original timeline bytes and therefore subtitle cache identity.
Wide rational calculations use integer quotient/remainder without floating
seconds; fractional millisecond cue mappings are refused rather than rounded.

The closed existing timeline v1 shape is required; legacy/unknown shapes need
explicit compatibility only for this new opt-in preparation. Other commands
and original exports keep their existing behavior. The private response adds
`corrected_timeline` as a sealed metadata descriptor, not a published artifact.
Internal B1 derivation has subtitle/render/QC/export descendants without TTS or
mix, but no B1 execution command is enabled by this helper. B2 preparation still
uses the original immutable model/runtime/catalog pins. No schema migration,
ASR/OCR rerun, worker/model dispatch change or active SQLite graph occurs.

Corrected subtitle/video/QC/standard and editable export production, native
complete-graph admission/CAS, undo and installed qualification remain required.
An authored native oracle test or a sealed timeline is not that qualification.

## Decision

The released review v1 payload remains unchanged: only `schema_version` and
`findings`. New production snapshots use schema 2, explicit job identity,
monotonic revision, canonical rational time base, cue identity, nullable global
range/confidence, diagnostic text and confidence provenance. An absent measured
confidence stays unknown. Warning order never identifies a dialogue cue.

Readers dispatch on the declared version and reject unknown versions/fields.
V1 remains readable for display. Its missing job/time-base/cue mapping is not
invented; it cannot authorize a correction. A future explicit migration must
bind it to the original pinned job/timeline and produce a separate v2 artifact,
retaining the original v1 bytes. The malformed historical Draft payload with
v1 plus job/revision/plan fields is rejected, not silently relabelled.
The desktop keeps ticks as strings/BigInt and rejects revisions above its exact
integer range rather than rounding a stale-client token. Confidence precision
is bounded to 128 characters in proposed v2; released v1 is unchanged.

Only a bound cue can be corrected. Translation and voice edits derive their
downstream closure from the changed fields, independently of warning category.
These plans are policy data, not proof that regeneration was executed or that
replacement media passed validation. ADR-0010 governs actual artifact reuse.
Multiple findings for one canonical cue share corrected text/voice. Conflicting
cue content/ranges fail admission; a compact delta can therefore restore all
related findings without overwriting diagnostics or unrelated cues. Undo cannot
change cue identity, source text or canonical range.

The supervisor-owned review store is a separate versioned envelope containing
one v2 snapshot and compact undo deltas. Workers may emit an immutable snapshot;
they never mutate this store or durable SQLite. Current state and undo stack are
serialized, fsynced and replaced together in one file. A failed replacement
leaves both unchanged. There is no second history file to partially truncate.

Each mutation takes a nonblocking OS file lock and checks the caller's exact
snapshot. Undo restores the prior finding/plan while incrementing revision, so
a stale pre-undo client cannot exploit revision reuse (ABA). OS locks release
on process death. Limits on serialized bytes and undo depth reject further
mutation explicitly; they do not silently discard history.

## Compatibility and recovery

Public snapshot schema and private store format have separate discriminators.
Unknown store versions and mismatched jobs fail closed. Existing v1 sidecars and
their `.history` files remain untouched; original exports stay usable. No
implicit upgrade, destructive reverse migration or cross-version writer exists.
Updater retention/rollback must retain matching artifacts and producers before
this format is shipped. These helpers do not qualify updater compatibility.

The temporary replacement lives beside its destination on the same filesystem.
Before replace, interrupted writes leave the old coherent store; after replace,
the new coherent store is authoritative. This does not claim power-loss
durability of every Windows filesystem. Artifact replacement validation and
prior valid graph retirement remain separate supervisor integration work.

## Completed-export read adapter

The supervisor supplies a trusted completed-manifest hash and job ID. The
read-only adapter verifies used timeline/QC/TTS JSON hashes, sizes, versions and
controlled paths, retaining their pins as input provenance for later artifact
registration. It derives assigned voice from the strict TTS document's pinned
provenance, including default requests with no explicit voice. TTS segment IDs
and original slot ranges must match canonical cues; requested voice and warning
order never substitute for those identities.

Preview paths belong to the pinned TTS descriptors. Playback must separately
verify the selected bounded WAV's complete content hash, PCM header and frame
count. Metadata loading does not scan a long MP4 or all speech files. An absent
speech file is a bound actionable warning, while other findings and canonical
exports remain available. These helpers do not themselves expose native IPC,
perform playback, register durable artifact nodes, or regenerate media.

The read-only CLI requires the owned isolated runtime flags `-I -S -B`, a
trusted manifest hash and matching job ID. Snapshot output uses public schema2;
the selected verified WAV descriptor uses independent preview-v1. Responses
and inputs are bounded; validation failure emits only bounded error data and
an unsuccessful exit, never a playable path. This optional adapter performs no
durable write. Native callers must additionally own the trusted job pin and
enforce child-process/output limits. A returned preview pin still needs the
native playback admission policy; it does not make a filesystem path an
unrestricted webview asset URL. Existing v1 snapshots/exports need no migration.

The standalone native `review-pin` read uses an existing supported SQLite schema
through a SQLite-enforced read-only connection, without creation or migration. It
requires a succeeded job/stage, the latest committed manifest artifact, its
immutable start-request output binding and exact bounded current bytes matching
the persisted hash/size. Export-pin-v1 is separate from snapshots and worker
messages. Unsupported/legacy/missing/corrupt inputs leave the original job and
exports untouched. This command does not grant a writer lease or qualify
desktop playback, correction execution or durable graph replacement.

## Read-only desktop transport v1

The desktop uses optional `review_snapshot_v1`, `review_audio_v1` and
`review_cancel_v1` commands. Native snapshot admission pins the completed
SQLite export and retains that pin in a bounded, expiring in-memory session.
Audio requests carry only session/job/revision/finding/cue identities. The
native host rereads the committed pin and requires equality with the session;
it never treats browser paths or hashes as authority. The owned Python CLI's
private `audio-v1` command binds the finding and revision, reads one bounded WAV
buffer, verifies its complete hash/PCM header/frame count, then returns those
same bytes. The native host does not reopen a verified filesystem path.

Tauri returns a raw buffer to an in-memory `audio/wav` Blob. The CSP permits
only `blob:` media in addition to same-origin media, without enabling file
assets or external network sources. Selection/reload/disposal cancels readers,
stops playback and releases object URLs. Commands run off the UI thread with
finite process/output limits, four active readers, eight sessions, and bounded
cancellation tombstones for abort-before-registration races. Expired sessions
require explicit reload. No model, downloader or render is invoked.

This transport changes no public snapshot/worker/durable schema. Existing
snapshot/preview CLI actions retain their format. Older hosts/runtimes lacking
the optional commands report review unavailability; queue and standard exports
remain usable. This first integration is read-only: correction controls are
disabled until supervisor-owned CAS and validated artifact replacement/undo
are integrated and qualified. It does not complete #174 or #175.

## Single-cue synthesis admission (Draft integration)

A new private `review_cue_synthesis_input` schema1 binds the native-admitted
manifest, before/after snapshot hashes, exact next revision, one canonical cue,
its source utterance/range/render window, explicit target voice, and complete
producer/model/runtime/config/resource pins. Its SHA256 covers canonical UTF8
JSON (sorted keys, no whitespace, non-ASCII text retained, non-finite numbers
refused); the TTS adapter uses that exact request hash as input provenance and
checkpoint identity. The worker reads only bytes pinned by a supervisor command.
Wide revisions are decimal strings. There are no browser paths or mutable
default voice selections in this request.

Preparation permits only the selected cue's translated text/voice to change;
all source mapping, confidence, diagnostics and other findings remain intact.
Producer/model/runtime/config changes fail with explicit migration-required
evidence. An unchanged voice also retains its exact profile/version. The B1
subtitle-only path and absent voices need separate handling; this adapter does
not invent a voice. Workers produce private candidates without SQLite mutation;
native CAS, physical validation, graph commit and undo remain separate work.

This additive private format changes no released TTS/mix/public snapshot/worker
schema. Old runtimes that lack this command retain their current exports; unknown
requests fail without mutation, and no implicit durable-store migration occurs.
Mixed-voice generations must retain separate original/replacement TTS document
pins rather than relabel every old WAV with the newly selected voice. This Draft
adapter and fixture synthesis do not qualify actual targeted production replay.

The owned isolated `synthesis_worker.py` entry point admits only the currently
installed B2 VieNeu producer/config and the exact verified explicit voice. An
older producer fails with compatibility-required evidence. It synthesizes one
cue into a request-hash generation, with an OS writer lock, pinned immutable
input and the existing per-cue TTS/spoken-text checkpoints. Resume validates
both receipts and waveform bytes; missing or corrupt evidence rebuilds only
that candidate. The existing bounded lexical duration policy retains separate
spoken-text evidence. The worker closes native inference on success/failure.
It writes existing TTS and spoken-text documents in private storage only;
these files have no graph pointer and cannot authorize retirement by themselves.
The supervisor must independently pin and validate both before any later graph
transaction. A partial document write remains a resumable private candidate.

## Per-cue audio composition v1 (Draft integration)

The private composition identity binds the admitted source manifest and Review
snapshot, original source WAV/range, canonical inputs, one TTS-document hash
assignment per cue, and the original mix producer/config/resource pin. Each
assigned TTS document retains its own voice/model/producer provenance. Several
successive edits can therefore coexist without relabelling unrelated waveforms.
The canonical UTF8 JSON SHA256 is the downstream mix input identity. Unknown or
missing cue assignments, conflicting document roots, changed source/WAV bytes,
noncanonical mapping and unadmitted producer/config changes fail explicitly.

Replacement admission reconstructs the exact next request from the active cue
and before/after snapshots. A replacement must contain exactly one successful
artifact with matching request provenance. The existing AUD-0 streaming adapter
mixes the new assignment into private, checkpointable candidates; it invokes no
ASR/OCR/TTS and revalidates every admitted source/document/WAV before and after.
Explicit prior TTS failures retain the original source fallback for their cues.

This additive private identity changes no released TTS/mix/public snapshot or
worker schema and implicitly migrates no durable state. Native graph CAS and
physical publication remain required. The supervisor must copy/pin validated
candidates into separate immutable accepted storage rather than make a graph
refer directly to worker-writable generations. Graph pointer, accepted Review
state and undo history must then move atomically; composition alone is not that
transaction, downstream render/QC/export qualification or completion of #174.

## Optional native Review graph state (Draft integration)

The production supervisor will own Review graph revisions in its existing
local control database. `0003_review_graph.sql` is an append-only, explicitly
enabled feature migration: `review_schema_migrations` version1 is independent
of the existing core `schema_migrations` version2. It adds namespaced tables
and a namespaced original-artifact/job index without changing released
job/stage/artifact columns or the core version.
Existing schema2 runtimes can still open their normal job state; they ignore
this optional feature. Unknown Review versions and partial/unregistered Review
tables require explicit recovery rather than implicit repair. Read-only access
never creates tables. The native mutation command explicitly enables the schema.
Read observations use one deferred SQLite snapshot for feature-schema discovery,
head/version lookup and original-export binding. Concurrent feature enablement
cannot mix earlier absent metadata with later tables into a false partial-schema
diagnostic. Read transactions require no writer reservation or migration.

Each immutable revision stores a bounded admitted snapshot and graph-pin
document together. A head binds the original completed manifest artifact/hash.
An immediate SQLite transaction inserts the revision and changes the head;
the expected job, exact decimal u64 revision, snapshot hash, graph hash and
original manifest hash must all match. Undo follows an explicit prior-valid
revision, retains its graph bytes and inserts a new monotonic revision so ABA
cannot authorize an old client. Snapshot, graph and undo linkage move together.
No previous version or physical artifact is removed by these state operations.

The state layer is a trusted-supervisor API, not a worker/browser JSON endpoint.
It hashes and bounds opaque admitted documents; the native caller must validate
their closed schemas, snapshot revision/content and graph identity, canonical
cue/model/producer provenance, and every physical candidate before calling it.
Undo must restore exactly the target snapshot content except its new revision;
its canonical serialization can grow at most32 bytes, and graph bytes remain
identical. Metadata history is bounded to64MiB per job and256 pending undos.
Correction admission reserves the bytes needed for all pending undos rather
than accepting a correction that exhausts the metadata budget. A failed CAS,
quota check or SQL statement leaves both the head and version history intact.

Legacy file-based Review stores are not automatically imported or dual-written.
An explicit future migration must prove original-job/cue/graph bindings and
preserve the old bytes. This additive database foundation does not itself
admit worker-writable files, copy accepted media, retire a graph, expose native
IPC or qualify undo on installed media. Accepted files must be separately
copied, flushed and validated before the transaction; pre-commit orphan files
remain private recovery evidence. Updater/GC retention must preserve the current
graph and its pending undo targets before this feature can be shipped. No
destructive reverse migration is needed for ordinary schema2 rollback readers.

## Private native accepted-cue storage (Draft integration)

The supervisor has an internal PCM-copy API under its durable control root:
`control/review-assets/<sha256(job-id)>/<sha256(wav)>.wav`. A job digest avoids
unsafe filesystem interpretations of otherwise valid colon-bearing contract
IDs. Callers supply roots resolved by native configuration and semantically
admitted cue pins, not browser-provided paths. This storage API validates the
physical pin only; cue identity, canonical timing and producer/model/voice
provenance still require the native graph caller's closed-schema admission.

One candidate is copied from a single open handle into a create-new private
file using a32KiB buffer. SHA256, exact bytes and bounded16-bit PCM RIFF chunks
must match before the file is flushed and exclusively published by a same-volume
hard link. The accepted object is sealed readonly and revalidated before being
returned. It is a separate copy, so later worker candidate changes do not modify
it. Existing hash-named objects are revalidated and never overwritten or silently
repaired. Symlinks/reparse points, traversal, oversized/truncated files, duplicate
format/data chunks and PCM parameter mismatches are refused. Ancillary RIFF
chunks are bounded and hash-covered. The currently supported cue file limit is
64MiB; this is not a whole long-form mix-storage API.

Candidates must be regular files before open and again on the opened handle.
On Linux, nonblocking open prevents a raced FIFO from blocking admission before
the handle check. A bounded FIFO regression exercises this refusal; pipe input
is not accepted as durable cue media.

Only a create-new-owned private partial file can be removed on failure. Original
exports, candidates and previous accepted objects remain intact. A publication
error may retain a private orphan for explicit recovery, but creates no graph
head. Unix flushes the containing directory; Windows package/fault evidence is
still required before making power-loss guarantees for this copy API. Readonly
is a supervisor lifecycle rule, not a sandbox against another process running
with the user's filesystem permissions; graph reuse/undo must revalidate pins.

This foundation does not expose a CLI/IPC mutation command, alter SQLite,
regenerate models, rewrite TTS document paths or update the active graph.
Native semantic graph admission, object/document binding, atomic graph CAS,
pending-undo retention and installed GUI edit/undo remain required before this
path can be enabled in the product.

## Native snapshot admission and correction proposals (Draft integration)

The native graph caller admits the closed publicv2 snapshot before using it as
mutation input. Duplicate object keys at any depth, trailing JSON values,
unknown/missing fields (including nullable fields), wrong schema/job/revision,
floating integer substitutions, noncanonical signed64 tick strings, unreduced
time bases, duplicate finding/stage identities and inconsistent same-cue
findings are refused. Low/unknown confidence remains data; unknown confidence
cannot claim a measured source. Native mutation admission accepts normalized
producer text and canonical confidence decimals; existing display-only v1 and
noncanonical legacy data require their explicit read/migration adapter.

Canonical snapshot serialization sorts keys, preserves array order and exact
integer numbers, emits UTF-8 compact JSON and bounds bytes. This matches the
Python cue-input hash encoding without depending on serde map feature order.
One-cue proposals update every finding for the selected canonical cue while
preserving all source/speaker/range/confidence/warning/preview fields and other
cues. Candidate admission re-derives and compares the entire proposed document.
This is the logical decision snapshot used to bind the worker request, not the
final published playback/QC snapshot. Preview paths and prior risk evidence stay
unchanged until replacement media exists. The native graph caller must derive
current preview bindings and risk data from validated producer results before
the state transaction; the request's logical snapshot hash remains provenance
while the published snapshot receives its own hash. This avoids a circular hash
dependency on a waveform path that cannot be known before synthesis. Publishing
the proposal directly would leave failed-cue previews unavailable or stale.

The native graph mode, never a UI override, determines actual descendants:
B1 translation reruns translation/subtitles/render/QC/export without TTS/mix;
B2 voice-only changes rerun TTS/mix/render/QC/export, while B2 text changes also
rerun translation/subtitles. A B1 proposal cannot invent voice/model authority.
B2 requires an existing mapped voice; catalog/model/producer verification is
still a separate required graph admission, not established by a voice string.

Undo proposals restore the entire pinned prior snapshot, including its prior
plan and all cue content, with one new monotonic revision. Canonical encoding
growth remains within the32-byte undo reservation. Publicv2's signed64 revision
limit is explicitly enforced even though the internal state layer can retain
exact u64 revisions for future contract versions. Reaching the public limit
requires explicit compatibility admission rather than wraparound or coercion.

The small JSON fixture is generated by the real owned isolated Python snapshot
constructors and ReviewStore correction path. It contains all six reason
classes, two findings for one cue, unknown global confidence, Chinese/Vietnamese
text and ticks/revisions above2^53; B2 native proposals are compared with actual
Python proposals. This is synthetic contract evidence, not real media or4h
qualification. These functions are internal proposals only: no CLI/IPC mutation
or active graph is enabled until original-cue/document/model/physical bindings,
native state CAS and undo asset retention are wired and qualified.

## Native cue-request binding (Draft integration)

The internal request boundary binds an owned-adapter packet to the native before
and logical-after snapshots, original export hash, selected plan/finding/cue,
exact next decimal revision and immutable canonical TTS input. Original input,
previous producer, configuration and target voice must come from native graph
and verified release/model catalog pins; caller JSON does not establish that
authority. An unchanged voice cannot acquire a new version/profile silently.
Only voice identity/version/profile hash may change. Producer/runtime/model,
configuration/resource/fallback stay pinned. The prior input hash remains
original-graph provenance; the request receives its new exact-byte input hash.

Canonical time points are compared as reduced rational values using u128
numerators, without floating seconds or overflow-prone cross-products. Equivalent
reduced bases may represent the same source cue; request start/end/window fields
must still retain the original input representation. Source identity, language,
confidence and render window cannot be substituted. The native bound packet
retains exact adapter bytes and hashes those bytes for worker dispatch.

This boundary checks provenance/timing and bounded closed fields. The separately
owned contract adapter must validate NFC text normalization, language/confidence
semantics and canonical packet encoding before dispatch; the physical output
graph must separately validate produced media. The binding API does not execute
a producer, accept model downloads, create graph authority, write SQLite or
qualify GUI regeneration/undo. The small fixture uses actual Python request
preparation/read/schema checks for two synthetic requests, wide revisions and
different exact bases; it is not real model/media qualification.

## Immutable prepared JSON documents (Draft integration)

The native accepted-object store also retains graph-prepared JSON documents as
exact UTF-8 bytes under their SHA256 identity. The graph caller must admit the
TTS/spoken/timeline/QC contract and producer bindings before calling this API.
Storage checks object-root JSON syntax, rejects duplicate keys/trailing values,
bounds each document to64MiB and preserves the producer's exact encoding and
wide integer literals. Syntax is not semantic graph admission.

Publication shares the PCM store's create-new private copy, verified exact
bytes, synced file, exclusive hard link and readonly seal. Existing accepted
objects are never overwritten or silently repaired. Every metadata reuse or
undo reads a single sealed regular-file handle, bounded against concurrent
growth, and revalidates size/hash/UTF-8/syntax. The stored native pathname and
hash must agree. Only an owned unpublished private copy can be removed on an
error path; accepted objects remain available for explicit recovery.

This internal API enables immutable retargeted documents without changing
their encoding, but does not retarget a TTS artifact, validate model authority,
publish a graph, write SQLite, dispatch a worker or expose a UI mutation.
Native semantic graph orchestration and pending-undo retention remain required.

## Prepared cue documents after native WAV acceptance (Draft integration)

The owned read-only adapter prepares one TTS document and spoken-text sidecar
after the native supervisor accepts its WAV. It re-reads the exact admitted
request, binds the successful single cue's producer/input/model/voice/config,
source utterance, confidence, rational slot/window and PCM identity, and verifies
the accepted sealed WAV. The shared candidate checkpoint-identity function binds
the sidecar's original/spoken text and lexical rewrite evidence to the pinned
recipe. A copied file or caller-supplied descriptor alone grants no authority.

Retargeting changes only the TTS artifact's WAV path and the sidecar's dependent
exact TTS-document hash. Producer/chunk/artifact hashes and all other semantic
fields remain unchanged; TTS artifact hashing excludes storage path. Prepared
documents are revalidated and returned as bounded immutable bytes for native
JSON storage. The adapter never reads a mutable candidate path, writes files,
executes inference or mutates the active graph. A v1 TTS document without a
render window retains v1; v1 cannot bind a request that requires a render window.
No version or recipe is upgraded silently.

Upstream producer/quality admission, native WAV revalidation before CAS, complete
mix/render/QC/export regeneration and retained-undo graph publication remain the
supervisor caller's requirements. Real-model metadata adapter evidence uses two
previous actual installed169 candidates with QA-only copies. It does not prove
native Windows accepted-object storage, installed GUI edit/undo or4h behavior.

## Native original-state initialization and archived read (Draft integration)

The internal CLI `review-original` reads an existing supervisor database;
creating revision zero requires explicit `--initialize-original` and the native
job execution lock. Job, succeeded stage, immutable start request, current
attempt, original artifact ID, manifest pathname, committed size/hash and
manifest job/version come from the supervisor. Browser-supplied manifests or
metadata cannot initialize a graph. No system Python fallback is available.

The owned isolated Python adapter reconstructs the current CPU producer's
original B1/B2 metadata only. B1 needs canonical timeline and passed QC. B2 also
requires exact original TTS, placement, spoken-text and mix documents, source
IDs/order, canonical inputs, pinned producer and the known placement recipe.
Missing legacy evidence or unknown producer recipes requires an explicit
compatibility adapter; it never invents a render window or upgrades an existing
job. The released display-only reader remains available.

Native admission binds snapshot revision zero, rational source ranges, source
and translated text, speaker, measured confidence/source, original voice and
non-null preview paths to those verified original documents. The original
snapshot cannot contain a prior correction plan. Each consumed JSON and their
combined archive are bounded to64MiB; the snapshot/graph combined state has its
separate64MiB limit. Media bytes, inference and long ASR/OCR scans are excluded.

Before creating the SQL head, native storage archives the exact manifest and
each consumed JSON under their sealed content identity. The v1 bound-original
envelope retains the source graph as an exact string; floats and producer
encoding are not reserialized into a different provenance hash. The graph stores
native accepted-object descriptors, not mutable export paths as its only route
to metadata. Original paths remain audit/provenance and future media bindings.

Reopening revision zero uses only the sealed archive and durable job/start/
artifact records. It requires neither original export JSON nor an installed
Python interpreter. Reads do not create directories or migrate schemas. Every
archived document is size/hash/syntax/seal checked again; missing/corrupt archives,
unknown feature/root formats or an already corrected generation refuse without
overwriting a head or silently repairing objects. Core SQLite version2 and the
optional Review feature version1 remain unchanged; no released migration is
edited. Unpublished copies may remain for explicit later orphan recovery.

The original graph preserves explicit revalidate-before-reuse media policy and
explicit-release-admission producer policy. Display preview paths are metadata,
not physical playback admission. This CLI returns correction_ready=false and
does not enable regeneration, media publication, model upgrades or undo. The
future active graph reader must separately admit original/corrected physical
assets and release/model pins before those operations.

Synthetic native tests cover completed-job binding, exact archive bytes,
read-only recovery after original metadata deletion, other-job isolation,
substituted seed fields, changed exports, corrupt objects, unknown formats and
bounded owned-adapter process outcomes. Actual real-media adapter evidence is
separate from installed Windows native archive/GUI and4h qualification.

## Native original-cue request preparation (Draft integration)

The internal `review-prepare-cue` command accepts only configured roots, job/
finding identities, text/voice edits and the complete native revision/snapshot/
graph token. It acquires the job execution lock, reads supported SQLite through
a read-only connection, checks the stale-client token and re-admits the sealed
original state. It currently prepares revision-zero B2 requests; B1 needs its
subtitle regeneration path and corrected generations need the active reader.
Neither path invents TTS authority or replaces an existing graph.

Before Python imports, a native boundary requires the original runtime root,
release-manifest fingerprint, Python/production-worker/media fingerprints and
model-manifest roster from the immutable job start record. It hashes the entire
listed app/runtime import trees, refuses missing/extra/linked/nonregular files,
ambiguous portable/case-insensitive paths and changed bytes, then fences the
actual inventory and release/entry-point pins again. Catalog/config metadata is
bound to this original release; runtime admission does not verify model weights.
The tree walk has a bounded observation deadline and no provisioning side effect.

An actual mixed-version Windows run of source97ceccd refused before inference
after180.156s at the original runtime validation deadline. The original pinned
import trees contain35,842files/1,938,122,706bytes; original job/database/source
and export hashes remained unchanged. This is a real failed qualification,
not evidence that retained audio or corrected output ran successfully.

Admission now shares one import traversal with the final inventory fence. It
uses directory-entry type only to select the directory/file branch, validates
each directory when entered, and leaves the fresh complete ancestor checks
before/after the no-follow file open in `regular` unchanged. Every nonempty
file still receives its full streamed SHA256 on every admission; empty-file,
extra/missing/nonregular/reparse, size, root and final release/entry-point checks
remain mandatory. This removes a redundant full ancestor walk and metadata read
per file rather than caching hashes or trusting enumeration metadata as proof.
The180-second bounds stay unchanged. Deadline errors include processed file/byte
counts and elapsed milliseconds to distinguish traversal and throughput limits.
The contribution of redundant checks to the observed timeout remains unproven
until a new source-matched Windows execution is measured; fixture/CI success
alone cannot qualify cold real-runtime performance.

Source3b33d51 was then executed with its verified Windows supervisor against
the unchanged original e1c producer/job. Original and active-original reads and
the stale-token refusal passed, but the first runtime admission still refused
after180.235s:25,280files/1,285,967,977bytes had been verified at the shared
deadline. All31original database/source/export/release hashes were unchanged;
inference, retention, timeline, render and resume were not reached. This proves
the single-walk optimization did not qualify this real runtime's latency.

The import traversal now enumerates at most50,000plain file paths and verifies
them with at most four scoped workers. Each callback still performs the fresh
complete ancestor/no-follow/regular-handle checks; every nonempty file receives
its full streamed SHA256 and empty files retain exact empty-hash admission.
Enumeration metadata and a queued path confer no authority. The final inventory
fence uses the same bounded workers and fresh file admission, retaining all
missing/extra/size/link/reparse refusals. No hash or metadata cache is introduced.
One180-second phase deadline covers enumeration, waiting in the queue, each
streamed read and completion of every worker. Errors, deadline exhaustion,
thread creation failure or verifier panic refuse the whole admission; all
started workers are joined and partial inventories cannot be returned. As
before, deadlines are cooperative and cannot interrupt a stalled OS file call.

This is a bounded I/O concurrency hypothesis, not proven cold performance or
product acceptance. Hosted compilation/regressions and a new source-matched
Windows execution remain required; source3b's green CI does not qualify this
successor. No pinned runtime/producer/model bytes, schema, SQL, worker protocol,
shared storage/source implementation or GUI enablement changes.

Actual sourceae1e7aa Windows execution subsequently reached the explicit old
producer's missing-render-recipe refusal after36.813s and produced/retained a
new translation mix after24.375s. This is mixed-version fresh-process evidence,
not controlled OS-cold timing or a complete installed/render/CAS/undo test.
QA path assertions were corrected to admit contractv1 Windows relative paths
and the canonical NT/DOS spelling of the same physical file; no native process
was repeated during those metadata-only diagnostics. Three retained48kHz stereo
16-bit PCM roles passed full hash/layout/volume/filename admission, but the
exact path-only metadata check correctly refused two changed confidences:
0.9838564395904541 became0.983856439590454 and0.9168733358383179 became
0.916873335838318. All31original hash/mtime pins remained unchanged. The three
remaining resume/after-state calls were not run; this is a real metadata defect.

The supervisor enables the existing locked serde_json1.0.151 `float_roundtrip`
feature (no package/version/lockfile additions) so the strict JSON visitor
receives correctly rounded binary64 values before canonical serialization.
Measured floating values must retain their original bits; a tolerance or
confidence normalization cannot waive the path-only transformation contract.
Wide integers/decimal strings, duplicate-key refusal, nonfinite/overflow refusal,
closed schemas, size/depth bounds and producer/model/runtime pins remain intact.
The feature applies to supervisor JSON decoding, so the app-manifest edit is
serialized and coordinated with the source-intake lease. No shared source/storage
implementation, worker format, SQL or durable schema change is introduced.
Existing sealed metadata and hashes are preserved rather than rewritten. A
derived generation carrying previously rounded values may refuse the exact
semantic fence and requires correct regeneration before publication; its old
objects remain retained for inspection/undo policy, without silent repair or
new authority. Original raw archives remain available. Successor hosted/native
verification is required; sourceae1's green CI and partial native evidence do
not qualify the new numeric admission behavior.

This changes neither producer/model/runtime pins nor a durable format. Old
original release roots are never patched or rebound, and a refusal still keeps
their existing exports/active graph usable. No SQLite, worker protocol,
core2/optionalReview1 or updater migration is introduced.

Legacy jobs lacking those pins, an unavailable original root or a release without
the cue adapter require explicit compatibility. A newly installed release is
never substituted silently. Such jobs retain their display/canonical exports;
this draft boundary does not yet provide every historical compatibility route.

Native code retains exact before/after/source-graph strings in a sealed context
object, preserving producer floats/encoding. The owned `-I -S -B` adapter waits
for the native containment byte gate, reconstructs the current pinned CPU config
and selected approved VieNeu preset metadata, validates the full model inventory
recipe without reading/downloading weights, and prepares the exact single-cue
request. Original source mapping, producer/runtime/model/config/resource and
unrelated findings remain unchanged. Only voice identity/version/hash may change
when the selected voice changes; unchanged voices cannot acquire new profiles.

Native admission independently derives the target voice from its verified
catalog and binds the returned request to original source input, complete
producer/model/config pins, native before/after hashes, context identity and exact
request bytes/hash. Prepared context/request objects use the immutable JSON
store; SQLite and the active graph remain unchanged. The job head and original
archive are reread before returning. A later producer must still physically
verify models and re-admit the request before inference.

The response explicitly reports model_weights_verified=false and
correction_ready=false. Request preparation is not synthesis, validated
mix/render/QC/export replacement or undo. Failed preparation may leave sealed
unreferenced request/context objects for explicit orphan recovery; it never
repairs/replaces earlier accepted objects. Core2/optional Review1 stay unchanged.

Synthetic native tests exercise producer inventory drift/rosters/root binding,
preparation packet substitution and exact Python request bytes. Owned-runtime
Python tests also exercise real isolated CLI gate/UTF8 output, pinned catalog
changes, stale/wrong contexts and unrelated-cue refusal. Real original169
metadata request evidence remains separate from installed Windows/native graph,
actual producer/model admission and full4h regeneration qualification.

## Required evidence

### Private full-mix compatibility

Windows journal replacement may return access/sharing denial while a temporary
reader/scanner holds the destination. The private Review caller permits at most
three attempts at each already-fsynced block's journal replacement only for the exact native composition/stream checkpoint replacement
(Win32 errors5/32). Before each retry, a bounded native DELETE-sharing probe must
actually succeed; waiting alone never authorizes retry. Readonly/linked/foreign,
nonjournal, other I/O, missing and persistent refusals retain the prior checkpoint
and return failure. No ACL/permission repair, deletion, producer/model replacement
or SQLite mutation occurs. A Review-specific saver retries only that journal write,
with the same already-fsynced block state; it never redoes a full mixer call. The
unchanged mixer re-admits inputs and its committed prefixes on resume.
PCM/config/producer/journal contracts remain unchanged; the new
caller code is pinned by its physical release inventory. Older jobs retain their
original release/producer and are never patched or rebound silently.

Private cue metadata identifies the validated artifact rather than whether this
particular invocation inferred or reused it. Verified single-cue chunks remain
completed; the exact operational reused-checkpoint notice is recorded separately
in a private execution-observation v1 file. All quality/fallback warnings and
producer/model/input pins remain in the accepted TTS/spoken documents. Repeating
the same correction therefore preserves exact document/composition hashes and
reuses the complete mix. This introduces no durable graph/SQL or producer-model
migration; old physical release roots remain unchanged and pinned.

The opt-in `review-mix-cue` command uses the same closed stale-client/edit input
and read-only original B2 admission. It prepares and synthesizes exactly the
selected cue, preserving the checkpointable producer, then supplies a sealed
native mix context to an isolated owned producer. That context separates exact
archived JSON roots from original/accepted waveform roots; broad common-parent
paths never grant media authority. Original producer releases lacking this
physical adapter require explicit compatibility before inference.

Private audio-composition v2 adds explicit metadata_root to every document.
Existing v1 compositions and hashes are unchanged; v2 readers must be selected
explicitly. No active graph or SQLite schema migration is introduced. The
original bound graph remains v1 and is reread after production; future graph
publication must retain immutable waveform dependencies and record exact v2
composition provenance rather than relabeling original per-cue voice documents.

The worker reuses canonical source PCM and every unrelated speech artifact,
runs the existing block/checkpoint AUD-0 mixer and returns exact composition/mix
bytes. It never calls ASR/OCR/translation/TTS or opens SQLite. Native code binds
context/request/snapshot/source/assignments/producer and independently streams
all three full-length PCM hashes and checks exact headers/frame counts/scope.
Mix production has a45minute process-tree observation bound; physical admission
shares180seconds with32KiB buffers and a six-hour48k stereo RIFF size bound.
Those ceilings are resource guards, not executed six-hour qualification.

The response reports private_audio_verified=true, retained_audio=false,
active_graph_published=false and correction_ready=false. Candidate WAVs remain
mutable checkpoint files: they cannot authorize an active playback graph or undo.
Quota/retention/orphan cleanup, accepted large media, full render/QC/editable
exports, published snapshot derivation, final CAS/undo, B1 and installed4h
acceptance remain required. Same-user concurrent file mutation is observed with
size/hash/mtime checks; it is not a malicious-writer filesystem sandbox.

Private candidate WAVs/scratch derive from the native-admitted original export
output root under `.dubflow-review-candidates/<job-digest>`. Full-length stereo
files stay on that output volume; local control continues to hold bounded sealed
metadata and accepted cue objects. Caller/browser paths cannot select this root.
Missing/linked/nonregular/parent-traversed output roots refuse; the supervisor
does not create a replacement output root or fall back to local control storage.

This private-placement change introduces no durable schema/graph migration or
producer/model/release rebinding. Existing control-side candidates remain
unmodified; no implicit move/delete or checkpoint relabeling occurs. The new
namespace may need fresh private synthesis/mix checkpoints, preserving all prior
accepted/canonical objects. Quota/orphan recovery and explicit stable-volume
locator integration with ADR0005/#173 remain required before public use. A path
hint alone does not qualify volume relocation, NAS atomicity or durable undo.

The placement root is the parent of the native archived manifest's immutable
`job_manifest.json` binding, not the parent of `tts_document.json`, which may
live several checkpoint levels below the export. Original input media retains
its separately admitted narrow artifact root. This changes private output
placement only; it never broadens input read authority or repins the producer.
Old nested candidates remain unchanged and are not silently moved or reused.
Archived metadata remains readable with the output volume offline; availability
and plain-storage checks apply before creating candidates, not while reopening
the sealed original snapshot.

Scratch uses the shared `.dubflow-review-candidates/temp` parent on that output
volume, with uniquely created native temporary subdirectories. Job checkpoint
and waveform namespaces retain the complete job digest. On Windows, scratch
parents exceeding200 UTF-16 code units are refused before any child spawn,
with an actionable shorter-output-folder message and original export preserved.
The reserve covers native temporary prefixes, random suffixes and PCM filenames.
This boundary follows a reproduced Windows `CreateProcess` hang with deep TEMP;
it is not a longer timeout, system-temp fallback or canonical path migration.
Crash orphan retention/quota recovery remains required before public use.

### Internal retained full-length PCM storage

An internal native storage foundation now accepts six-hour 48kHz stereo16-bit
RIFF PCM independently of the64MiB cue store. It copies with a32KiB buffer to
`.dubflow-review-media-v1/<sha256(job-id)>/<sha256(wav)>.wav` on the admitted
original output volume. Copy, exact hash/size, PCM admission, flush and exclusive
readonly publication precede any returned physical descriptor. The candidate
is never hard-linked as an accepted object or modified in place. Existing
objects must pass sealed hash/size/PCM validation; corruption is never replaced
implicitly. Identical objects reuse their bytes without a new space reservation.

One nonblocking kernel writer lock covers every job's quota scan through
publication within this output namespace. Its empty lock file is retained;
process exit releases the OS lease without unlink/reclaim races. Aggregate
quota includes all old/undo media and partial crash orphans. The inventory is
bounded to4096 entries, with links, unexpected namespace directories, nested
objects and unavailable observations refusing explicitly. Before allocating a
copy, a native caller must provide a fresh free-space observation sufficient for
the complete file plus its configured reserve. Unknown capacity, quota pressure,
overflow and shared deadline exhaustion refuse; no fallback writes large media
under local control. Only the current create-new-owned partial can be removed
on an ordinary failure. Other orphans need explicit recovery; no implicit GC or
eviction can discard pending undo references.

#### Native Windows volume and retained locator admission

The additive opt-in native command `review-retain-mix-cue` accepts the same
revision/finding/edit arguments as `review-mix-cue`; it accepts no extra path,
volume label, storage marker or quota argument from the browser. Before owned
producer execution it observes the immutable job's output root with Win32
`GetVolumePathNameW`, `GetVolumeNameForVolumeMountPointW`, `GetDriveTypeW`,
`GetVolumeInformationW` and `GetDiskFreeSpaceExW`. It binds a canonical local
root hint and normalized OS volume GUID before expensive work. Missing,
nonplain, network, removable, non-NTFS, read-only, unknown-capacity and
hard-link-incapable roots refuse this optional command. The native error-mode
guard preserves/restores thread flags and suppresses critical-error dialogs.
Standard export and the older private mix command remain available.

Retention re-probes the initial volume identity before opening the output-wide
writer lease, after opening it, before each copy's admission and after every
physical verification. Copy admission uses **bytes available to the calling
user**, respecting OS quota; total volume free bytes cannot authorize a write.
This private command has a 32GiB aggregate output-root quota and 2GiB free-space
reserve. All old/undo/partial objects count and nothing is implicitly evicted.
The three admitted full mix roles share a cooperative720-second retention
deadline. Failed partial generations remain counted recovery objects, never a
successful graph. A changed/missing root cannot fall back to control storage.

Each accepted PCM object receives the existing ADR0005 `ArtifactLocator`, using
the canonical storage crate rather than a parallel path/locator validator:
schema1, `windows-volume-guid:<uuid>`, root kind output, validated relative path,
content SHA-256 and byte size. Descriptors serialize wide counts as canonical
decimal strings. Absolute paths are transient hints. Every reuse verifies
locator equality plus native bounded streamed checksum/PCM/sealedness; a new
copy is independent of mutable worker candidate files. Reopen creates no
replacement root/lock and refuses changed locator pins or absent media.

The new response kind is `review_retained_mixed_original_cue` schema1. Its
private mix reports `retained_audio=true` only after all three retained roles
are verified together, and returns their physical locators separately. It
still reports `mix_metadata_retargeted=false`, `active_graph_published=false`
and `correction_ready=false`: sealed original candidate mix/composition JSON
still refer to the private producer generation. No desktop edit is enabled
until those semantics are retargeted/admitted and a complete render/QC/export
graph and undo references are validated before native CAS. The preexisting
command's response and behavior remain unchanged. This additive private
response does not migrate producer contracts, SQLite or old jobs.

OS observations and plain-path checks are cooperative, not an atomic filesystem
sandbox against a same-user remount/race, and cannot interrupt blocked kernel
I/O. The volume mount manager can return a different GUID alias; a mismatch
requires explicit recovery rather than treating a drive letter as identity.
This first native provider deliberately excludes removable/network storage;
future providers require their own admission and product qualification. These
constraints, physical fixture tests and a successful Windows build do not
qualify process-death/power-loss durability, real six-hour media or installed
GUI/undo behavior. Older binaries ignore the retained namespace/command;
rollback preserves existing originals and leaves private objects for recovery.

Win32 reference signatures and semantics:
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamew
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumenameforvolumemountpointw
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationw
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdiskfreespaceexw
- https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-setthreaderrormode

The preexisting `review-mix-cue` command and desktop remain unchanged; that
command continues to report `retained_audio=false`. The new opt-in command
above connects native OS/volume admission and existing versioned locators only.
Its caller must still retarget admitted mix metadata and retain every pending
undo dependency before final graph CAS. A physical path descriptor alone is
not a durable locator or semantic/provenance admission token.
Filesystem observation and I/O deadlines are cooperative; they cannot promise
to interrupt a blocked NAS/OS operation. Windows volume, process-death and
power-loss qualification remain required. This code does not claim them from
the deterministic physical-file tests or their injected space observations.

The namespace is additive and private, with no SQLite, public Review, worker,
producer/model or updater format change. Old cue/control assets, private mix
checkpoints and original exports are untouched. Older binaries ignore the new
namespace and cannot publish its contents; rollback leaves them for explicit
recovery. No automatic migration, relabeling or destructive reverse migration
is performed. Full mix/render/QC/export, active graph publication, real undo,
B1 and installed long-form acceptance remain incomplete.

### Private retained mix metadata preparation

An additive read-only adapter consumes native-retained-mix-context schema1:
exact candidate mix bytes/hash, admitted composition hash, native job/output
root/volume pins and the three retained role descriptors. It checks the closed
context, canonical decimal PCM counts, ordered roles, job namespace, content
hashes and full-mix layout against the admitted candidate. Only the three mix
artifact paths change; all producer/source/input provenance, timing, metrics,
chunks, confidence, warnings and failures retain their original semantics.
The composition remains unchanged: its source and per-cue pins describe the
inputs used to produce these exact audio bytes, not the publication location.

This transformation deliberately performs no media I/O, inference or SQLite
mutation. A missing mutable candidate generation cannot invalidate already
retained audio. Its private schema1 result binds the exact context and new mix
document hashes and explicitly requires native_media_revalidation_required.
Caller JSON never grants storage authority. Before sealing/graph CAS, native
code must independently admit path-only differences against its original
candidate, validate existing canonical locators and revalidate all retained
physical media under the original job/volume token. The native retention
command performs that path-only transformation directly over typed native
objects, using the strict decoder/canonical serializer. It revalidates all
three canonical locators/physical media before sealing and again after sealing
the retained mix document. The result separates candidate_mix_document from
the retained mix_document and sets mix_metadata_retargeted=true only after
those operations pass. Active graph publication remains false. The older
non-retaining mix command is unchanged.

The Python adapter is a deterministic oracle, not a dispatched replacement
producer or an admission token. Windows/POSIX metadata fixtures compare the
native transformation against it without claiming physical volume authority.
Original producer/runtime/model pins are unchanged; no new Python helper is
silently substituted into an old pinned runtime. Desktop use and native active
graph CAS still require complete downstream media/export/QC admission.

These formats are additive/private, retain mix schema1 and leave prior exact
bytes/hashes readable. They neither rewrite old assets nor migrate SQLite or
updater contracts. Rollback ignores the unreferenced prepared document and
preserves the current original export/graph. Fixture metadata/schema/CLI tests
only qualify this transformation, not physical media, GUI, final CAS or undo.

### Original physical single-cue dispatch

The opt-in native `review-synthesize-cue` command repeats the original head and
request admission, then uses only the model root recorded in the immutable job
start and the admitted owned media executable. It requires the physical cue
adapter in the original release inventory. Missing roots/adapters need explicit
restoration/compatibility; Review never installs, downloads or repairs models.

Before inference the isolated owned worker validates catalog/config/producer
pins and every file of the already installed VieNeu pack with streamed bounded
hashes. Missing, foreign, partial, linked/nonregular, changed-size/checksum or
changed inventory data refuses. It verifies the physical preset roster and
constructs the existing production engine without the provisioning loader.
Model verification is a bounded read, not an atomic filesystem sandbox against
malicious concurrent same-user changes. Subsequent engine/PCM validation still
applies; no synthetic pack can qualify real inference or Windows integration.

Native process-tree containment precedes the byte gate and the executable
defers app/dependency imports until that gate. Production has a 300-second
observation bound, 256KiB stdout and 4096-byte stderr bounds; model hashing has
a shared 180-second deadline. Retargeting has a separate 60-second bound. Scratch
paths derive from the admitted original export's short private scratch parent
described above and child temporary environments point there; caller/browser
paths cannot choose a producer/model/request/output.
The worker owns checkpointable private candidate files, never SQLite.

Native code binds the returned single-cue document to exact request/provenance,
source mapping/timing/window/confidence/config, confines waveform reads to the
private generation and verifies copied PCM bytes/header/frame count before
sealing an immutable accepted object. An owned metadata adapter validates full
TTS/spoken semantics and retargets only WAV path/dependent document hash. Native
code independently verifies those changes and rechecks accepted audio/documents,
the request, original archive and unchanged head before returning descriptors.

The response reports model_weights_verified=true only after that physical
producer path, active_graph_published=false and correction_ready=false. No SQL
generation mutation occurs. Complete immutable mix/render/QC/export, published
snapshot derivation, final CAS/undo and B1 remain required. Retention/quota and
orphan/scratch recovery must cover all preparation, candidate and accepted
objects before this feature becomes user-facing. Core2/optional Review1 are
unchanged; the existing display/export path remains usable on refusal.

Tests validate actual emitted JSON against the declared schemas, v1 read-only
compatibility, unknown-version rejection, sparse/reordered warning cue binding,
unknown confidence, two corrections/two undos, stale-client refusal, replacement
failure and concurrent-writer admission. Full #174 still requires installed
desktop, actual B1/B2 regeneration and four-hour/real-media evidence that
unrelated ASR/OCR artifacts are reused.

### Private corrected render and QC execution

The additive private native-render-context1/render-candidate1 formats bind exact
native before/after snapshots, archived manifest/timeline and corrected timeline
bytes. The owned worker independently rederives the selected translation,
checks original source size/hash/probe and, in B2, all three retained PCM
hashes/headers/frames plus mix/composition/revision bindings. It cannot silently
downgrade B2 to original audio. Translation edits regenerate subtitles; voice
edits keep exact original SRT/ASS/timeline bytes and diagnostic warnings.

The worker uses app-owned FFmpeg/FFprobe through the existing media adapters.
Execution identity includes exact context, executable and render recipe source
hashes. A private OS writer lease serializes a generation. Only a full successful
H.264/audio/duration check and full AV decode can produce its result checkpoint.
Completed resume requires closed role inventory, exact expected output paths,
content/size verification and fresh source/mix/probe/full-decode checks; it does
not rerender or rewrite the prior completed files. Deadline/byte-bounded hashes
and metadata reads never authorize SQLite writes or active graph publication.

The opt-in native `review-render-cue` connects B2 cue synthesis and retained
mix preparation to this private renderer. For a B1 graph it instead derives the
subtitle-only proposal and corrected timeline from the native archived seed,
then directly dispatches that same original-pinned renderer. The B1 branch runs
before voice selection, physical model loading, cue synthesis or mix execution;
its context has `retained_mix=null`. A voice assignment refuses through the
existing subtitle-only correction semantics. B2 must supply its retained mix
and cannot downgrade to this branch. Native result/QC admission binds the
original mode; B1 preserves source audio or silence, while B2 requires audio.
Both return private render candidates with the original graph unchanged.
This wiring is not installed B1/B2 or real long-form qualification.

It admits all original recipe source
and media executable bytes before inference, reconstructs the context from
native archived metadata, exact corrected timeline and sealed retained mix,
and preserves the immutable original burn-in option. Caller arguments cannot
select worker/context/source/media/output paths. A historical producer missing
the new recipe refuses before inference; no newer adapter is copied into it.
Native admission independently binds the worker result to its exact recipe,
context, revision, generation, five role paths/complete byte hashes, exact
corrected timeline and source/QC/codec/audio/duration evidence. Recipe and source
bytes are rechecked after execution; the original archive/head is rechecked by
the caller before response. Owned isolated Python containment and the native
byte gate precede imports; the existing 900-second component render bound stays.

The worker's five-role result still records the exact private generation used
for execution and QC. Native code now copies its admitted MP4, SRT, ASS, timeline
and QC into the same sealed output-volume media namespace as retained PCM,
under one output-wide OS writer lease. Typed format pins restrict extensions
and role-specific byte bounds; MP4 may stream up to128GiB, while each metadata
role stays within64MiB. Every copy streams32KiB buffers, verifies complete
source and copied SHA/size, flushes and publishes without replacing a prior
object, then revalidates readonly bytes. Existing objects deduplicate only when
their sealed bytes still match; corrupt/unsealed objects are retained/refused.
Reopen verifies retained bytes without recreating missing namespaces or needing
the old mutable candidate. There is no eviction of previous/undo dependencies.

The existing32GiB aggregate Review quota now accounts for PCM and all render
roles together, including other jobs, pending undo and crash orphans. Caller
free space must cover the remaining copy plus2GiB reserve before writing, is
reobserved every4MiB during copy and before sealing, and comes from the same
fixed-local writable NTFS volume GUID captured before expensive work. Unknown,
insufficient or changed-volume observations refuse without control-root
fallback. This explicit quota can refuse a high-bitrate long render even when
the per-file bound permits it; it is not evidence that every6h profile fits.
Storage policy/configuration and actual long-form space qualification remain
required. Cooperative probes/copy deadlines do not provide atomic reservation
against other applications or interruption of blocked kernel I/O.

Only after all five roles have canonical ArtifactLocators and physical readonly
hashes revalidated does `review-render-cue` return `retained_final_output=true`.
A sealed private schema1 receipt binds job/context, original candidate result,
execution identity and ordered retained role descriptors. It preserves exact
candidate/QC provenance instead of rewriting the worker's generation paths.
It is not a published active graph. `correction_ready=false` and
`active_graph_published=false` remain; the desktop is not enabled by this command.
Partially completed retention leaves unreferenced sealed objects; no old valid
graph/output is retired on failure. Native graph admission/CAS, undo dependencies,
standard/editable publication and real installed/power-loss qualification remain.
No public worker/SQLite/core2/optionalReview1 or original producer/model format
changes. Older binaries ignore the private receipt and account these regular
files in the existing namespace quota, preserving originals on rollback.

Before returning the opt-in render result, native code now releases its writer
lease and reopens the existing namespace from the original admitted output
pin. It reads the sealed retained receipt, context and candidate result from
the native accepted-document store, binds their exact job/recipe/before/after
snapshot and corrected-timeline bytes, and reconstructs all five files from
closed canonical locators. The absolute `path_hint` is bounded diagnostic data,
never an IO target. Root kind, volume, relative path, hash, decimal-string size,
format and native job/content filename must agree; unknown or substituted
descriptors refuse without creating/replacing a namespace or object.

The same result/timeline/QC semantic admission used for the live candidate is
then rerun against those retained physical paths. Original candidate paths
remain QC/execution provenance, but no candidate media is read during recovery.
Only after that check may the private response add
`retained_receipt_reopened=true`. This does not activate a corrected graph or
perform SQLite CAS/undo. The future active-graph reader must invoke the same
physical and semantic checks, bind its own current durable token, and validate
all retained mix/original dependencies before publication. Reopening within
one native process and deterministic missing-candidate fixtures do not replace
actual installed crash/restart, volume-change or long-form qualification.
An original pinned release must explicitly inventory/admit the worker; no
historical worker/model/runtime pin is upgraded by this addition. Actual native
output quota/volume/locator and immutable retention qualification, final graph CAS/undo and
editable export remain required. This draft worker checkpoints only completed
renders: chunk scheduling/checkpoints and bounded long-form recovery are still
required before activation, as are B1/B2 installed and actual four-hour media
tests. Small physical fixtures qualify only the component. No durable schema,
core2/optionalReview1 or existing job format changes. Rollback ignores private
unreferenced candidates and keeps the original export/active graph usable.

The opt-in render response also prepares a separate sealed snapshot for future
graph publication. Only native admission of the reopened five-role render can
construct the private preview proof. Its source proposal must match exact
canonical bytes and the worker result's admitted after-snapshot hash. Every
finding, including unchanged cues and global warnings, receives the physically
verified retained full-video path. Candidate paths and absolute locator hints
cannot supply that preview. All other proposal fields remain identical: cue
text/voice edits, source integer ticks/time base, measured confidence, existing
warnings, finding order and the invalidation plan. Fresh render QC remains a
separate sealed role; preview preparation does not invent confidence or clear
prior risk evidence. Non-UTF8, relative or out-of-bound preview paths refuse.

The additive private `prepared_snapshot` descriptor is distinct from the
logical proposal and context fingerprint used by the worker. It neither changes
the retained receipt format nor commits a SQLite graph, enables desktop edits
or marks correction readiness. A future native graph transaction must read and
revalidate this prepared snapshot against retained output admission, retain all
undo and mix dependencies, and bind the current durable token before CAS. Old
snapshots and source exports stay untouched. Older binaries ignore the extra
private descriptor; unreferenced sealed metadata remains subject to the existing
accepted-document budget. Pure snapshot/undo tests do not qualify media, crash
recovery, cumulative graph edits or the installed product.

A separate opt-in native `review-commit-render-cue` command now runs the same
original B1/B2 correction/render boundary, reopens every retained render and mix
dependency, checks the current durable token, then calls the existing supervisor
SQLite graph CAS. It preserves the prior row as the undo target; validation,
storage and CAS failure cannot retire the original export. Existing private
commands remain SQLite read-only. A private schema1 publication document stores
hash-bound sealed before-state/proposal/timeline/render/context/preview objects,
original artifact/manifest identity and the pre-inference output volume GUID.
It does not copy recursive graph history into each row. The corrected graph is
a closed native pointer envelope to that publication document, never worker
JSON authorizing a mutation. All objects and original exports remain retained.

`review-active` re-admits that envelope, original canonical metadata, exact
logical proposal and derived timeline, all five canonical output locators,
retained B2 PCM locators/headers/full hashes and the exact path-only mix retarget.
Absolute hints and mutable generation files cannot become recovery IO targets.
Every displayed snapshot must match native prepared playback except its current
monotonic revision. `review-undo-active` admits both current and prior graphs
before the existing CAS restores the prior snapshot/graph at a new revision;
an unknown/corrupt/offline graph refuses without silently repairing or falling
back. Restored original metadata is normalized only for original semantic
admission, while its public token remains monotonic; original media hashes are
rechecked before it can be used. No worker, SQLite schema, public worker protocol
or original producer/model/runtime pin is changed. Rollback to an older native
binary refuses the unknown optional corrected graph while the untouched standard
export remains available, rather than overwriting optional state.

These commands are an integration draft, not enabled in the desktop. The current
pinned cue recipe accepts only the original revision; cumulative edits and a
new correction after undo still require the versioned effective-generation
producer path. Chunked longform scheduling, corrected standard/editable export
publication, actual installed CAS/undo/power-loss and native four-hour tests also
remain mandatory. `correction_ready=false` stays even when the opt-in native
graph CAS succeeds. Deterministic metadata/SQLite tests and placeholder media
cannot qualify native render quality, an installed workflow or full Issue174.

Actual current-source B1 admission found Python correction planning traversing
the B2 TTS/mix descendants even when both admitted finding voices were absent.
The native B1 proposal correctly retained only translation, subtitles, render,
QC and export, so the original renderer refused that proposal before rendering.
Python store planning and timeline admission now derive those five B1 stages
from the existing absent-voice provenance; adding a voice requires explicit mode
compatibility and refuses. Assigned-voice B2 planning remains unchanged. ASR/OCR,
source ticks, confidence, unrelated findings and exact downstream-plan equality
remain fenced. There is no schema, SQL, worker protocol or producer pin change.
Old sealed B1 proposals with TTS/mix stages are not silently rewritten. Existing
pinned producers/jobs retain their code and failed evidence; the repaired recipe
requires a new admitted producer/processing version and source-matched native
qualification. The original export remains usable during refusal or rollback.

Actual current-source B2 reached successful cue synthesis and retained mixing,
then its renderer refused native extended Windows WAV paths against a DOS-spelled
output root. Those spellings identify the same admitted NTFS directory. Worker
render containment now checks plain existing directory identity for a lexical
root mismatch, preserving the original path string. It does not strip arbitrary
prefixes, follow a reparse/link into an allowed root, widen the root, read hints,
or rewrite sealed mix metadata. Both paths retain the existing no-follow,
named-stream and multi-link refusals; full file/header/hash and native output
volume/final fences remain required. Foreign directories with identical bytes
still refuse. This is an OS-spelling compatibility fix, with no schema, SQL,
worker protocol, producer/model pin or runtime replacement. Old pinned producers
and the actual failed B2 context remain immutable; the new processing source
requires exact-head Windows and current native qualification before activation.

The subsequent actual B2 worker completed its private render and full AV/QC,
but native post-render admission still compared retained extended Windows PCM
paths to a DOS-spelled output prefix. That fence now reopens the three ordered
native PCM locators in the existing store under the output volume pin captured
before expensive work. Reopen independently checks the exact job namespace,
readonly/no-link media, header/frame/size/full hashes and fresh volume identity;
the original admitted candidate is retargeted again and must equal the complete
sealed retained document byte-for-byte. Hints and lexical prefixes grant no
authority, and unexpected roles or any semantic/numeric change refuse. The
writer lease is released before final render retention acquires it. Budgets,
deadlines, original producer/model pins and schemas are unchanged. The failed
producer/job and private rendered evidence remain immutable and unpublished;
successor Windows/native render retention/CAS/undo still require qualification.

### Planned successor: admitted effective generations

Status: implementation requirements; the current private commands still admit
only the original generation. This section does not enable desktop correction.

An actual additional native48 case used the revision2 token restored by the
completed B2 undo/reopen sequence, then requested a different translation. The
native process refused with `existing generation requires its active graph
reader; never overwrite`. Original exports, the complete private checkpoint
inventory/hash/mtime, retained media, historical pins and all logical SQLite
schema/rows remained identical. Full source, request, response and preservation
evidence is in PR185 comments6163946535/6163947073, gzip66352B,
SHA256a9e781940eccffb6d64f1d749f41a06ac06e27ecb113281839161d1ce1b8eef0.
The attempt plus post-audit took2.000s; this is an unsupported-acceptance result.

`review_seed::read_original` requires revision0 and empty undo ancestry. The
restored-original branch of `review_active::read_active` verifies origin bytes
at revision0, then restores the public monotonic revision for playback. That
playback operation does not grant a new producer request. The owned cue and mix
adapters additionally require an original revision0 graph. Removing one counter
check cannot establish the effective media, assignment or producer binding.

The successor must introduce explicitly tagged private effective-generation
contexts and a native admitted-effective-state reader. An older original-only
reader must refuse these tags. Existing original context encodings and sealed
objects keep their current meaning and bytes. Public revision counters stay
monotonic; origin revision0 is used solely to verify the original archive.

The admitted state must independently bind:

- immutable original job/manifest/source/media clock identity;
- the current durable job/revision/snapshot/graph token;
- the exact current logical snapshot and current canonical editable timeline;
- B2 current per-cue TTS/spoken-text assignments and composition, with their
  admitted producer/model/configuration provenance and retained PCM pins;
- the current five retained render/QC roles and source-pinned render recipe;
- explicit capabilities of the processing release that consumes the new format.

The new generation must derive its selected cue from that admitted current
state. Every other cue retains its current translated text, voice assignment,
timing, confidence and spoken checkpoint. Original source-derived integer
ticks/time-base mapping and original confidence bits remain the semantic
reference. Native code must independently admit the complete before/after
change and duplicate-finding consistency, rather than reconstructing current
state from mutable candidate files or playback hints. A voice-only correction
preserves current subtitle/timeline bytes. B1 retains its source audio/silence
semantics; B2 cannot fall back to B1 when a mix dependency is unavailable.

The current canonical timeline and B2 composition require explicit roles in
the new context. The original manifest continues to identify the original
export; a derived timeline or mix cannot impersonate that original artifact.
All metadata reads are byte/hash bounded. Physical dependencies reopen through
native canonical locators under their admitted fixed output volume, with fresh
no-follow/regular/readonly/header/frame/size/full-hash checks. Each producer
receives confined physical input paths after this admission; JSON hints do not
grant file or volume authority. Existing aggregate Review quota, undo/orphan
accounting, fresh reserve and copy/deadline fences also cover new generations.

An original pinned producer lacking the new capability must refuse. Supporting
an older completed job requires an explicitly admitted compatible processing
release and recorded source/recipe/model/contract identity; replacing helper
files inside its original bundle or changing its existing job pins is invalid.
No model, runtime, default Truc Ly voice or25-voice catalog change is required
by this design. The exact source-matched producer/native acceptance remains a
separate gate from metadata compatibility.

Replacement validation and publication remain supervisor-owned. A generation
publication stores sealed closed references to its effective before-state,
proposal, timeline, composition, render/QC and native preview evidence. It must
avoid recursively embedding every prior generation. Only a final fresh token
check and existing graph CAS may replace the active pointer. Undo validates
both graphs and all required physical objects, restores the prior effective
content at a new public revision, and retains all dependencies. A subsequent
correction uses this restored effective state as its baseline. Failure, stale
CAS or missing/offline/corrupt objects cannot retire the prior valid graph.

Compatibility work is additive in private tagged context/publication formats;
core2, optionalReview1 and the SQLite tables retain their existing boundaries.
An older binary refuses an unknown active private graph while preserving the
canonical standard export. There is no silent migration of sealed originals,
history or model/runtime pins. Any implementation needing a durable schema or
public worker-protocol change requires its own explicit migration/rollback
review before activation.

Required successor evidence includes:

1. Two consecutive corrections to different cues while preserving the first
   correction, then a second correction to the same cue or voice mapping.
2. Undo of a corrected generation, a new correction from the restored state,
   repeated undo to original content, and monotonic public tokens throughout.
3. Original/source/unaffected cue and checkpoint reuse verified independently;
   completed TTS/render media bytes and mtimes remain stable. Mutable execution
   observations are recorded separately, with unchanged canonical document
   content. No inference or render is relabeled as cache reuse.
4. Actual separate-process stale-token, concurrent writer, corrupt/missing
   dependency, volume and crash/restart refusal/recovery checks, preserving a
   usable prior standard export on every failed replacement.
5. Corrected standard and editable publication from the same accepted effective
   generation, with exact canonical caption timing, actual retained audio and
   full output verification. Integration with Issue172 export ownership is
   coordinated before shared publication paths change.
6. Chunked/checkpointed long-form replacement with bounded memory, disk and
   recovery work; actual four-hour and representative real-media evidence that
   unrelated ASR/OCR is reused. Completed-only whole-video checkpoints do not
   satisfy this requirement.
7. Current source-matched whole-producer B1/B2 and installed desktop preview,
   editing, liveness/QC and undo evidence, with measured media/voice quality and
   existing warnings retained. Small or mixed-producer proofs alone do not
   enable `correction_ready` or qualify full Issue174/175.

The current native48 retention/CAS/undo compatibility proof and its recorded
three-file checkpoint-metadata limitation remain unchanged historical evidence.
The existing source and all completed QA/native cases must not be replayed or
rewritten merely to produce a passing successor report.

#### Successor implementation checkpoint: current composition and cue preparation

The first successor layer reopens existing v1/v2 audio compositions against
separately admitted native source and document pins. It reconstructs all cue
assignments from original canonical mapping and the current logical snapshot,
then requires exact canonical composition bytes. JSON paths grant no authority;
foreign inventories, producer pins, missing/duplicate/reordered cues, changed
timing/confidence and lost prior edits refuse. Existing schema encodings remain
unchanged. Metadata and waveform hashes/headers are rechecked without inference
or writes.

The additive private `review_effective_cue_context` and
`review_prepared_effective_cue_request` tags bind the current native token,
composition hash and exact before/after snapshot bytes. The selected cue uses
its current document's producer/voice provenance, preserving a prior voice edit.
A restored revision2 remains revision2 and proposes revision3; no public counter
is normalized. Original-only adapters reject the successor tag. The capability
field is format dispatch, not independent release/inventory admission.

This layer is not wired to the current native commands. Their original-only
guards and the actual after-undo refusal remain unchanged. Native admitted
effective-state recovery, processing-release capability checks, independent
request/mix/render validation and cumulative publication must be implemented
before an actual successor correction can run. Deterministic synthetic metadata
tests of these readers do not satisfy installed/native/GUI/long-form acceptance.

#### Native effective-state reader checkpoint

The read-only `review-effective-state` path now constructs a private current
view from the same native playback admission, followed by a fresh durable token
comparison. It binds current and origin as separate identities, preserves
current public revision counters, and carries the exact current timeline and
native sealed metadata pins. For original B2 content it independently checks
every original cue PCM full hash/header/frame count under the archived source
root, preserves source confidence bits, and records the original per-cue
TTS/spoken-text assignments. Source PCM identity is matched to the archived mix.
Unknown graphs, stale state, changed media and missing dependencies refuse.

Inspection of native-publication-v1 shows that its retained mix references the
accepted changed TTS document but omits the accepted changed spoken-text
document. Existing playback recovery remains valid; a successor generation
cannot infer the omitted reference from a private checkpoint or filename.
The effective view reports an incomplete generation inventory for that legacy
B2 publication and keeps its recorded composition snapshot identity separate
from the current public token. Explicit compatible successor publications must
carry all current per-cue TTS/spoken references. No sealed v1 record is rewritten.

This reader still grants no processing-release capability, generation execution
or cumulative publication. The original-only generation guards remain intact.
Rust formatting and source-scope checks are separate from compilation and actual
native acceptance, which still require the source-matched hosted/Windows gates.

#### Versioned speech-reference publication checkpoint

An additive native-publication-v2 record now stores a sealed closed per-cue
speech inventory. A changed cue retains its accepted TTS/spoken documents, native
acceptance context, recipe identity and sealed PCM proof. Every other cue keeps
the original accepted TTS/spoken references. Recovery rebuilds those assignments
from the native source, proposal and composition, checks PCM/header/full hash,
canonical source confidence bits and exact path-only/hash-only retargets, and
preserves checkpoint and bounded duration-rewrite advice. Missing, substituted
or ambiguous references refuse. Recorded lexical rewrite evidence remains a
restricted producer claim without semantic-confidence calibration.

The distinct retained-render-graph-v2 tag dispatches to that reader; original
publication-v1 playback and sealed bytes keep their meaning. No legacy record is
augmented from private checkpoints or automatically migrated. The effective
reader reports v2 speech references separately from its original/current token
and composition-snapshot identities. This first-generation retention change does
not relax original-only generation guards or enable cumulative edits. Its cue
proof still refers to the native control-store acceptance format. A cumulative
successor must admit compatible processing capability and integrate per-cue
physical output identity/quota plus current timeline/mix/render/CAS/undo before
actual correction after undo, corrected exports or desktop activation qualifies.

#### Native per-cue output retention checkpoint

The retained-generation synthesis path now copies selected cue PCM directly into
the admitted fixed output volume before accepting its TTS/spoken documents.
The native store gives it a distinct 48k mono signed16 cue type and canonical
output-volume/job/content locator; the full-mix stereo contract is unchanged.
It uses the existing output-wide OS writer lock, aggregate quota and orphan/undo
accounting. Streamed copy/hash/header verification checks fresh caller reserve
every four MiB and again before sealing. Dedup reopens sealed bytes and preserves
their timestamp; a corrupt collision is refused, never replaced.

New cue recovery ignores preview hints, derives only the canonical path, and
checks readonly/header/frame/full SHA plus fresh volume and single-link handle
identity. Unix uses native device/inode/link count; Windows uses the documented
GetFileInformationByHandle/BY_HANDLE_FILE_INFORMATION layout. Unknown platforms
refuse the cue identity operation. Native publication-v2 speech proof requires
this locator and checks it against its accepted metadata and PCM pin. Metadata
stays in the existing sealed control store, and only the separate legacy private
non-retained synthesis command can use the legacy control PCM format. There is
no retained-generation control fallback or implicit migration of existing media.

This closes the selected per-cue physical retention boundary; it does not grant
processing-release compatibility, current-composition rebinding or cumulative
execution. Hosted/native source-matched tests, actual after-undo/cumulative
corrections, corrected standard/editable publication and installed/long-form/
crash acceptance remain required. Old actual failures and all pinned contexts,
producer/model roots, artifacts, media and proofs remain immutable.

#### Effective composition projection checkpoint

Pending native current-state recovery now carries an independently computed
effective composition beside its immutable content composition and admitted
content snapshot. A retained proposal predates native preview-path attachment;
undo changes the public revision while reusing prior content. The projection
permits exactly those differences, checks monotonic revision and exact remaining
snapshot content including diagnostic/plan fields, and changes only the new
composition's snapshot hash. Current public tokens are never changed or sent as
an archived revision-zero token. Accepted old metadata/media remain immutable.

The owned Python effective-cue preparation independently reopens original
composition bytes against separately admitted source/document pins and the
explicit content-snapshot hash. It recomputes the same current projection before
deriving a request, preserving earlier text and voice edits. The prepared packet
keeps both immutable and projected composition identities. Changed warnings,
confidence, source mapping, plans, unrelated cues, foreign snapshot pins or
backwards public counters refuse. Playback hints never authorize file reads.

These fields extend the still-unreleased private successor contexts only. Older
original readers remain unchanged and reject successor tags; no sealed original
or publication-v1 record is migrated. Processing release admission, cumulative
mix/render/CAS/undo and corrected standard/editable/installed/long-form/crash
acceptance are still mandatory; projection is not execution permission.

#### Separate compatible processing release admission checkpoint

The pending effective-state command accepts a complete explicit processing
selection (root, release manifest SHA/size and source commit). Original job
producer/model/root bindings remain immutable and are admitted from the durable
job request, not from this new selection. Processing admission verifies the
original and processing import trees through the existing complete bounded
parallel verifier. Runtime files and TTS/mix/model/media dependency inventories
must match exactly, including missing/extra files and empty-file hashes. No
catalog/default/model/CPU recipe or runtime replacement is authorized. The
processing release worker is a distinct inventory descriptor; no old bundle is
modified and no durable job pin is rewritten.

The release-inventoried Review capability declaration describes only the owned
Python `prepare_effective_cue` function and its closed private formats. It does
not advertise a runnable CLI, cumulative execution or graph publication. Native
admission binds that declaration and function module to the selected source and
manifest, records a separate receipt and rechecks source/entry/capability and
final inventories before returning. The current durable token is fenced again
after processing admission. Weights remain unverified until actual production
loading; the original model root is preserved without provisioning or copying.

The effective-state view reports this receipt separately from current/original
tokens and physical content dependencies. No receipt can turn an incomplete
legacy speech inventory into a complete one. Cumulative request/producer
dispatch, current mix/render, source-bound closed publication/CAS/undo, corrected
standard/editable exports and installed/long-form/crash acceptance remain
mandatory. These unreleased private fields have no migration of sealed legacy
graphs or implicit old-job/model compatibility. Original-only commands keep
their current refusal guards.

#### Native admitted current cue preparation checkpoint

The pending `review-prepare-effective-cue` command now reads complete current
speech dependencies through the native effective-state reader, derives a
duplicate-finding-consistent proposal, selects the current cue input and TTS
document/provenance and admits a separate processing release. Restored/current
public counters are preserved; original revision zero is used solely for the
immutable archive. Legacy B2 publications with incomplete spoken references
refuse, and the speech command cannot silently convert B1 to B2.

Native code seals a closed current binding/context pair and dispatches the
inventoried owned Python adapter with -I -S -B and the existing native byte gate.
The processing declaration now records both the preparation function and its
isolated dispatch entry. The adapter independently reopens exact content,
source/document/PCM and original canonical-input pins without loading models or
writing media/SQLite. Its returned projection must equal the complete native
projection, and BoundCueRequest rechecks current snapshots, producer/config/
voice, rational source mapping and exact descendants. Immutable input fields
also require canonical byte equality, preserving confidence bits and numeric
types instead of relying on Value numeric equality.

The native reader and durable current token are fenced again after dispatch;
only then are request, proposal and projected composition sealed privately.
No old producer/model/root/job binding is repinned and no original-only guard
is removed. This metadata command creates neither an active graph nor inference,
mix, render, CAS or undo. Current cumulative execution/publication, corrected
standard/editable exports, chunked long-form and installed GUI/crash/real-media
acceptance remain mandatory before activation. All prior actual failures and
completed native proofs retain their recorded scope and bytes.

#### Current-generation cue TTS execution checkpoint

The opt-in `review-synthesize-effective-cue` path now shares the admitted current
preparation flow and requires an additional inventoried
`processing-cue-synthesis-v1.json` declaration. Existing preparation-only releases
remain usable for preparation; their receipt grants no TTS permission. Native
code independently hashes the ordered original catalog/TTS backend/checkpoint/
rewrite dependencies and compares the returned producer recipe before retaining
audio. The processing root is a separate versioned dependency, and the original
job/model/runtime root is never repinned or rewritten. Missing or incompatible
capabilities, catalog drift and unlisted/changed recipe files refuse.

The current request retains its public before/proposed revision, selected current
voice/provenance and full independently admitted projection. The existing owned
isolated byte-gated cue producer verifies the original physical model pack before
loading and never provisions or repairs weights. Only the selected cue enters
the private checkpoint generation keyed by its complete request and recipe;
other cue assignments, source analysis and existing media remain untouched.
Resume observations may change to skipped/reused and same-byte metadata may get
new timestamps, as already recorded by the original producer. No claim of whole
checkpoint timestamp invariance is introduced.

Selected PCM is retained directly on the preadmitted fixed output volume through
the existing writer lock, aggregate quota, reserve checks and canonical mono cue
locator. The native speech fence is shared by original and current request types
only after each caller independently binds its request. It reopens accepted PCM,
TTS/spoken objects and acceptance context, preserves confidence IEEE bits and
checks source mapping/actual end, producer/config pins, exact path-only/hash-only
retargets, checkpoint identity and recorded bounded rewrite advice. Candidate and
retarget comparisons also preserve signed zero. Current token/dependencies and
the processing release are rechecked after synthesis; stale results remain
private and cannot change playback or durable SQLite state.

The closed `review_native_synthesized_effective_cue` result records private
dependencies and TTS acceptance. Its caller must still revalidate those native
dependencies before any later mix/publication, rather than treating a response
path or boolean as authority. This path does not implement current mix/render,
cumulative publication/CAS/undo, corrected standard/editable exports or desktop
activation. The actual historical after-undo refusal remains failed; source
authoring and tiny synthetic Python tests do not qualify source-matched native
Windows execution, installed B1/B2, crash/recovery or real long-form acceptance.

#### Current-assignment streamed mix checkpoint

The opt-in `review-mix-effective-cue` path requires an additional inventoried
current-mix capability before cue inference. The owned isolated mix adapter has
its own closed effective-mix context, distinct from the original-only context.
It reopens current native binding/source/document pins, independently derives
the same current request and projection, verifies the accepted cue's complete
TTS/spoken path-only/hash-only retarget, and replaces exactly that assignment.
Other cue text, voice, producer, source mapping and physical media stay pinned.
It uses the existing protected AUD-0 bounded streaming/checkpoint producer and
executes no TTS, model provisioning, ASR/OCR or SQLite mutation.

Native code independently constructs the complete replacement composition from
its admitted current projection and the accepted cue descriptor. It retains all
referenced prior documents, replaces only the selected cue, and removes only
document references that have become unused. Returned composition values must
match that independent result, including original confidence bits and protected
source/mix producer. Mix segment/failure evidence is reconstructed from each
native sealed assigned TTS document. Duck windows use rational source ticks and
integer floor/ceil sample mapping, with the inventoried protected DSP policy.
The producer's explicit interval ordering is used for physical mix evidence; it
does not reorder logical cue assignments or Review stage plans. Chunk coverage,
status and per-cue failure evidence are independently fenced.

Original and current paths share only the already-admitted physical mix
boundary. Three full PCM roles are checked against private generation shape,
header/frame/hash/metrics and original source length, then copied/sealed through
the existing canonical fixed-volume store, output writer lock, aggregate quota
and reserve checks. Native retargeting changes exactly their three paths. Source
metadata is checked before and after that boundary, and current token/content,
speech dependencies and processing capability are reopened before returning.
Returned paths and flags are private evidence, never publication authority.

Tiny synthetic speech with actual two-second streamed DSP and isolated-process
tests cover prior voice preservation, wrong current/request/accepted pins and
completed checkpoint reuse. Their WAV byte/hash/mtime fences do not establish
whole checkpoint metadata invariance, actual native Windows/after-undo behavior,
speech quality or long-form capacity. Native interop/refusal tests remain a
separate hosted gate. Current rendering, versioned cumulative publication/CAS/
undo, matching standard/editable exports and installed/GUI/crash/real4h/full172/
175 acceptance remain required; no original job/model/worker pin is replaced.

#### Current B1/B2 cumulative timeline and render worker checkpoint

The separately admitted original archive remains revision zero. The current
timeline command accepts the complete current token and next native proposal,
including restored or wide public counters. Native and owned Python derive the
expected current timeline from the original source plus every current finding,
then apply only the selected next change. Full canonical comparison preserves
all source text, rational ranges/clock mapping, duration, unwarned cues and IEEE
confidence bits, including signed zero. Voice-only proposals preserve exact
current timeline bytes. The original-only reader/generation guard is unchanged.

The optional current timeline capability is inventoried and closed. Its isolated
Python entry requires the native byte gate before imports or context reads;
native independently checks context/current token, proposal and current/derived
timeline hashes before retaining private metadata. The processing release and
full current dependency view are reopened after preparation. This does not
execute media, provision models, publish SQLite state or mark correction ready.

Current rendering uses a new closed context and a separate recipe. It admits
the original manifest/source independently of the current timeline, proposal,
complete public token and physical current caption pins. Current SRT bytes must
contain every current cue at its exact source interval; ASS events must match
the existing admitted producer formatting. The two existing LF/CRLF file
framings are recognized without rewriting input bytes. In a voice-only edit,
current captions are copied byte-for-byte rather than read from the original
manifest. Prior captions are therefore retained after an earlier translation.

Original and current entry points share only a private render kernel after
their own tag/admission checks. Current capability admission fingerprints the
original-compatible owned media and all twelve ordered executable/source pins,
including the current render/timeline entries. Context hash plus the distinct
current recipe identify checkpoints; no original-tag candidate is relabeled.
Source/three-PCM header/frame/hash fences, source probe/geometry/FPS, full AV
decode and all five output roles remain required. Current captions are reopened
after render/QC, and completed render checkpoints retain their artifact bytes
and mtimes rather than render again. The original runtime methods are unchanged.

Current timeline metadata tests and tiny private-render tests are component
evidence. One initial tiny-media test used an unavailable GPL-only libx264
fixture encoder before any render-worker dispatch; that QA failure is preserved.
The corrected fixture uses the shipped Windows h264_mf software encoder. The
actual tiny B1 isolated render then passed full AV decode; mocked B2 caption and
checkpoint tests are not real voice/native/long-form qualification. Native
timeline/recipe interop/refusal tests remain authored but unrun without the
required hosted/native source gate.

Native current-render dispatch, physical final retention and versioned
cumulative publication/CAS/monotonic undo are still required. Corrected
standard/editable publication, chunked long-form processing, source-matched
whole-producer B1/B2, installed GUI/liveness, actual crash/restart, representative
real four-hour and full172/175 gates are unchanged. No old producer/job/model
root is repinned or modified; private worker success does not satisfy those
acceptance gates or authorize merge/Production.

#### Native current render dispatch and physical retention source checkpoint

Distinct `review-render-effective-b1` and `review-render-effective-b2` commands
now connect the current graph reader, separate processing capability and owned
current render worker in source. B1 uses its unchanged original source audio;
B2 reuses current per-cue assignments and regenerates only the selected speech
cue before the current mix/render. Original-only commands and their runtime
methods retain their existing meanings. Public current/restored counters remain
in the new context and are never rewritten to satisfy the original guard.

The native boundary independently reconstructs cumulative timeline content and
verifies current SRT/ASS events before dispatch. Caption IO uses native original
export pins or already reopened retained-role locators; mutable hints do not
authorize a read. Voice-only output must retain exact current caption byte pins.
Result admission verifies all five physical role byte/hash pins, complete
caption events, archived source probe/warnings, H.264/AAC, original geometry,
exact rational FPS and duration within one source frame, without epsilon.

All five roles are copied/sealed under the existing fixed writable NTFS volume,
no-follow/readonly/full-hash fences, output writer lock, aggregate 32GiB quota
and fresh 2GiB reserve. The writer is dropped before independent locator reopen.
The separate private receipt binds complete current token, processing-release
recipe, context and candidate provenance. Reopen uses canonical retained paths
for IO while candidate paths remain provenance. Source, current captions,
retained three-role mix, accepted metadata, processing recipe and durable graph
token are fenced again around rendering/retention. Prepared playback paths
derive from the reopened retained final video, never client preview hints.

Four native caption/QC/physical-result/recovery tests are authored; they are not
actual native execution or installed qualification. A separate actual tiny
Python-worker probe checks its output against the newly authored native
geometry/FPS/one-frame/AAC policy: 320x180,25fps,0ms source-duration delta, full AV
decode and unchanged input byte/hash/mtime. It uses a synthetic native context
and proves no native CLI/SQLite/CAS/undo/model or representative long-form run.

Current receipts deliberately have no publication authority. Versioned
cumulative graph publication, complete speech-reference preservation, CAS,
monotonic undo/recovery and a new correction after undo still require source
implementation and actual acceptance. Current source-matched hosted/native
B1/B2, corrected standard/editable exports, chunked long-form, installed GUI,
liveness, actual crash/restart, representative real4h/full172/175 gates remain
mandatory. Private retained source code or component media success does not
qualify those gates or authorize merge/Production.

#### Cumulative publication-v3 and complete speech-inventory-v2 implementation

Status: additive implementation in progress; no current CAS/publication or
desktop readiness is enabled by this declaration. Existing publication-v1/v2
and speech-inventory-v1 bytes remain immutable and keep their old meaning.

Publication-v3 must bind original archive separately from the complete native
effective before token/snapshot/graph, one next proposal, current/corrected
timeline, processing release, retained render/QC and prepared playback. Before
state references include undo depth/target as admitted from the supervisor;
the original archive remains revision zero. Legacy first-generation publishers
continue refusing cumulative input. New history is admitted only under a closed
versioned graph tag and existing SQLite CAS, with no feature schema migration,
model/producer repin, relaxed source mapping or public revision normalization.

B2 speech-inventory-v2 must replace exactly the selected cue row in the complete
currently admitted inventory. Every other cue keeps its existing TTS document,
spoken-text document and native acceptance proof. A changed cue binds a sealed
generation proof containing the native current before/after snapshots, bound
request, current baseline TTS assignment/provenance and processing capability.
Recovery independently rebinds that request, checks original source clocks and
confidence bits, reopens canonical PCM, verifies exact native TTS/spoken retarget
and checkpoint identity, then checks every assignment against the cumulative
composition. No old private worker checkpoint is treated as a missing proof.

Legacy inventory-v1 may provide admitted prior rows after its existing verifier
passes; its selected cue must gain an explicit compatible proof reference when
consumed by v3. The original archive contributes unmodified rows with no speech
generation proof. A v1 publication without complete changed-speech evidence
remains incomplete/refused. Inventory rows are kept in original canonical cue
order, and source/mapping/confidence are unchanged across a new text/voice edit.

Only a verified cumulative before-state and full retained result may prepare a
v3 record. Final admission reopens every physical dependency and source-bound
processing receipt before existing graph CAS. Undo verifies the current graph
and prior effective content, restores that content at the next public revision
and preserves the prior proof chain. A later correction uses the restored token
and current assignments. No recursive JSON embedding, candidate/hint authority,
partial admission, metadata normalization or silent migration is permitted.
Proof traversal/work remains explicitly bounded by shared deadlines and the
existing history/quota/reserve policy; a bounded refusal preserves the previous
standard export and active graph.

The source implementation and its test vectors must cover different-cue edits,
same-cue replacement, a prior voice/text change, wide/restored revisions, lost
or reordered assignments, changed spoken/provenance/PCM and stale CAS. Actual
source-matched native B1/B2/cumulative/undo/after-undo, separate process/crash,
standard/editable publication, installed GUI/liveness and chunked/real4h/full172/
175 gates remain mandatory. Metadata source or fixtures cannot qualify them.

##### Native historical identity and physical-reader boundary

The pending v3 recovery source introduces a closed generation-state reference.
It keeps the real public decimal token, original artifact/manifest identity,
accepted snapshot and graph references, undo depth/target and exact native
effective-state view. Snapshot plus graph retain the existing64MiB state
payload limit; decimal parsing keeps integer counters exact and refuses aliases.
Sealing requires a native admitted effective state and exact live store state
before and after accepted-object sealing. No client path/token or reset tozero
is used to manufacture a generation baseline.

Reference parsing reopens full accepted bytes under the same deadline and
checks state hashes and undo ancestry. The returned reference is deliberately
distinct from AdmittedEffectiveState. Historical content must pass the same
physical original/legacy graph reader, canonical source/timeline, retained
render/mix and speech checks as current content. Its recovered effective view
must match the sealed native view exactly before becoming a typed baseline.
Live current reads continue checking the durable head before and after that
shared physical reader; a historical read does not claim to be the live head.

Historical references must also match the complete immutable SQLite version
recorded for that exact token, including undo fields. A read-only
review_graph_revision API validates the original completed job binding, rejects
future/wrong tokens and rechecks recorded hashes/payload/undo consistency in a
deferred observation transaction. It does not initialize/migrate the optional
feature or change its live head, rows, CAS or undo semantics. Parsing metadata
alone cannot invent a historical baseline. Two native store tests for actual
commit/undo/read-only reopen and wrong-token/corrupted-row refusals are authored;
they are not actual product crash or installed acceptance.

The new traversal owns one deadline for all nested legacy physical work and
rejects cycles, more than256 active proof scopes, more than4096 proof reads,
panic or late completion. No proof admission is cached between references.
For existing publications, before and origin remain the same original state;
they are separate native fields so a future v3 before may have a later public
counter while its origin remains the immutable archive. Existing publication
tags, original-only preparation guard, SQL CAS/schema and old worker meanings
are unchanged. The full v3 record reader, speech-proof recovery and CAS/undo
remain in progress; metadata source and authored native tests do not qualify
actual cumulative/after-undo/Windows/installed/long-form acceptance.

##### Effective selected-speech and current-mix proof recovery source

Preparation and recovery use the same native current request binder. It keeps
the independently admitted immutable TTS input fields, numeric types and IEEE
confidence bits, and binds the actual before/after snapshots, previous producer
provenance, configuration and target catalog voice. Recovery verifies the exact
immutable recorded before-state and complete native effective view rather than
accepting a worker metadata projection as an admission.

The selected effective-speech proof must bind its own current token, sealed
before-state, snapshots, baseline composition, request, processing synthesis
receipt/recipe and native accepted cue. Full canonical retained cue PCM and exact
TTS/spoken retarget evidence are reopened. Every unselected inventory row keeps
its native prior TTS/spoken/proof references; a legacy proof cannot stand in for
the new selected effective generation.

The current mix executor additionally seals the exact accepted worker response
as private recovery evidence without adding fields to existing command packets.
Recovery reconstructs native preparation and bindings from the admitted current
and original archive, rechecks the source PCM/manifest, current assignment set,
request and accepted cue, and reopens the source-matched processing mix receipt.
The existing independent mix metadata oracle verifies segments, failures, duck
windows, chunk ownership/status and protected provenance. All three physical
retained PCM roles then pass full byte/header/locator/volume and exact path-only
retarget checks under the same deadline and32GiB/2GiB policy. No inference/DSP
execution or SQLite mutation is used to manufacture missing recovery evidence.

Full publication-v3 record dispatch, render recovery/factory, CAS/monotonic undo
and actual acceptance remain incomplete. This source checkpoint may be compiled
and tested as Draft progress; green source/component checks do not qualify
cumulative product correction, installed GUI, crash or long-form behavior.
##### Cumulative publication v3 reader and explicit CAS source

The pending successor introduces a separate closed `review_native_publication_v3`
and `review_bound_retained_render_graph_v3` tag. Recovery walks native immutable
before-state references through actual recorded revisions, then physically admits
that exact state. One caller-owned deadline and cycle/depth/read bounds traverse
the complete tree; repeated references are read again. No v1/v2 meaning changes,
revision-zero guard relaxation or public token normalization provides admission.

The render context builder is shared by execution and historical recovery. It
derives cumulative timeline, immutable original metadata/source and burn choice,
current physical caption pins and the retained mix from typed admitted content.
Recovery compares the entire saved context, source-bound processing render recipe
and execution identity, reopens all five physical roles and derives the prepared
playback snapshot. Its context/dependencies are rebuilt after reopening. The
original180s source verification bound remains within the shared720s phase bound.

B2 verifies selected effective speech generation and full current-mix packet,
all three canonical retained PCM roles, and every unchanged prior speech/proof
assignment. B1 refuses all speech/mix proof fields. The factory seals a native
before-state and exercises this complete reader before returning a private
publication. Separate opt-in `review-commit-effective-b1` and
`review-commit-effective-b2` commands use the same existing supervisor-owned CAS;
active/undo dispatch recognizes v3 and revalidates before using existing monotonic
state transitions. Private commands stay read-only/private; no SQL migration,
worker/model/runtime repin or desktop readiness promotion is introduced.

This code is pending compilation and actual current whole-producer qualification.
It does not supersede the preserved actual48 after-undo refusal or establish new
cumulative/same-cue/different-cue/undo-afterundo results. Corrected standard/editable
exports, chunked long-form, installed B1/B2 GUI/liveness, actual crash and real4h
and all full172/175 acceptance remain mandatory before merge or Production.

Review of the pending source adds the v3 tag to the physically admitted active
response and revalidates the live processing render receipt after recovery of
all five retained roles. Re-reading only the sealed receipt would prove its bytes,
not that the processing dependencies remained admitted during that phase.

Three further native regression cases are authored, not yet executed: real
supervisor metadata/SQLite reference round-trip before and after undo with a
forged effective view refusal; equal-size corruption of original artifact bytes
behind an intact sealed reference; and a v3 publication claiming readiness while
missing processing/physical render, which must leave the restored head and undo
unchanged. They use owned metadata fixtures and do not qualify encoded media or
the actual installed native product.

##### Native selection of current corrected export inputs

The optional `review-prepare-active-export` command is a read-only prerequisite
for corrected standard/editable publication. Its separate closed input-v1 tag
binds the complete actual public token, distinct immutable origin, current
snapshot/timeline, active publication and retained render references. An opaque
native constructor first runs full current physical graph recovery with the
same caller-owned720s walk, then reopens all five ordered retained roles through
canonical output-volume locators. The physical timeline must equal the admitted
current cumulative timeline exactly. B2 reopens all three canonical PCM roles
and selects its current final mix. B1 cannot acquire a mix. The original source
must still match the immutable manifest and physically admitted render context.
All existing no-follow/hash/readonly/volume/quota/reserve policies remain intact.

Both live head fences preserve actual counters after undo; no playback or token
normalization makes a different generation current. Original/restored-original
states refuse this corrected-only route and retain their existing export path.
The command opens SQLite read-only, copies no assets, performs no inference and
leaves original outputs and all durable Review rows/head/undo unchanged.

Serialized input packets cannot authorize later reuse. The future publisher
must independently re-admit the same complete token, reopen/copy/full-hash every
asset, validate the resulting portable pack and fence current head immediately
before atomic publication. Destination admission, quota/copy/journal recovery,
standard/editable publication and installed qualification are still required.
No172 adapter,173 storage/liveness or168 intake implementation is imported or
changed. This additive source and two native refusal tests are pending hosted
compilation; they establish no new product acceptance or migration of old jobs.

##### Restartable corrected export copies and aggregate storage compatibility

The next publication stage uses versioned `.exports-pending-v1/<generation>`
and `.exports-published-v1/<generation>` directories inside each existing native
job media namespace. Generation IDs are native-derived lowercase SHA256 keys.
All generation files, pending prefixes, published/old exports and crash orphans
count with retained PCM/render bytes from every job under the same output-wide
OS writer lease and32GiB aggregate policy. The bounded inventory permits only
these explicit directory levels; unknown nested directories or links refuse.
No separate unaccounted export cache or quota bypass is introduced.

Copies use32KiB buffers and a fresh2GiB reserve before copying, every4MiB and
before sealing. Forecast reservation includes all remaining files. Restart
compares every existing partial prefix byte against a fresh admitted source
before appending, then checks the complete source and destination SHA256 and
source metadata again. Changed prefixes refuse without truncation. Single-link,
no-follow targets seal read-only; same-volume native rename refuses an existing
destination atomically. Existing sealed copies are revalidated and reused
without rewriting bytes or mtime. Errors preserve partials and previous exports.

This adds an explicit filesystem layout. New inventories read legacy flat job
media and account both export directory tags. Older inventory/writer code refuses
these directories; it must not run writes after the new layout is introduced.
Existing original exports remain usable. Qualification uses a new source-bound
current producer/root, preserving all prior immutable roots/jobs and evidence.
No SQL schema, worker protocol, model pin or173 storage implementation changes.

The implemented copy primitive is pending compilation and four native filesystem
regressions are authored, not executed. It is not yet connected to native current
input selection, complete portable manifest/pack validation, the final full-head
fence or atomic directory publication/recovery. Those stages and actual installed
standard/editable export, crash/restart and long-form acceptance remain required.

The native no-replace calls use Linux `renameat2(RENAME_NOREPLACE)` and Windows
`MoveFileExW` without replacement/cross-volume-copy flags, with write-through.
Their flags and failure behavior follow the [Linux manual](https://man7.org/linux/man-pages/man2/rename.2.html)
and [Microsoft API documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw).
Actual crash/power-loss qualification remains a separate required gate.

##### Native standard bundle publication and portable validation

`review-publish-active-export` now connects opaque native current inputs to the
versioned copy stage. It holds the existing job execution guard, opens SQLite
read-only and verifies the complete actual token. The source list is constructed
only by physical native current admission: five retained roles, immutable source
video, and all three retained48k stereo PCM files in B2. It includes the exact
current cumulative timeline; B1 has no invented PCM. No serialized path table or
browser packet can manufacture these typed source paths.

The closed `native-current-export-manifest-v1.json` uses portable relative
basenames and full SHA/decimal byte pins. Its identity includes the complete
current/restored token, separate original token, exact current snapshot bytes,
native publication/render hashes, mode and complete ordered resource list. Its
generation key hashes every field other than the generation key itself. The
current snapshot bytes retain their native identity, including original preview
metadata; only the explicit relative file table is used for portable file IO.
This preserves source integer ticks/time-base mapping in the exact timeline
bytes and avoids rewriting public revisions or historical provenance.

After resumable copies, the independent bundle validator requires every file,
full hashes/sizes, sealed single-link no-follow objects and an exact directory
inventory. Missing, extra, partial, substituted-role or traversal resources
refuse. It also verifies closed manifest shape, mode-specific resources,
snapshot identity and the generation key. This validates byte integrity; native
render/source/QC/media semantics are admitted separately by the current reader.

The copy phase releases its writer lease before a second full native current
admission opens it. Recovered typed inputs must reproduce the exact manifest.
The publisher then reacquires the output writer lease, validates all target
files, checks aggregate quota/fresh reserve and the entire current state directly
before native no-replace directory rename. It reopens the published directory
and checks the current state again before returning success. Cooperating Review
mutations are excluded by the existing job guard. A late refusal preserves the
complete version for retry; nothing deletes or replaces an earlier export.
Separate720s admission/copy/final-recovery phase bounds permit bounded restart;
within a recovery phase one caller-owned walk covers nested proofs and IO.

Restart derives the same complete-token generation. Published folders are
fully validated and reused; otherwise verified partial prefixes resume. A whole
generation appears under `.exports-published-v1` only after directory rename.
There is no mutable active-export pointer or overwrite of original standard
files, no SQLite export record/migration, and no deletion/GC on error.

`review-validate-export-bundle --bundle-root ... --manifest-sha256 ...` accepts a
trusted publication hash and can rehash an independently moved folder without
opening the original job/store. Its result explicitly does not check live current
generation or re-decode media. It cannot authorize new native reuse or prove
installed-product/video acceptance. The standard bundle is not relabeled as a
production editable pack: the current PR183 adapter/contract integration remains
required, together with actual current whole-producer B1B2 cumulative/undo/
after-undo/export/GUI/crash/long-form and full172/175 acceptance. This additive
source is pending hosted compilation and native qualification; prior old-root
jobs/proofs remain immutable. No173/168/172 implementation is changed.

Allocation forecasts subtract physically present pending prefixes and reverified
sealed files from future copy requirements while the same writer lease is held.
They do not grant content authority: each prefix still undergoes the complete
source comparison before append. This avoids charging already retained resume
bytes twice while keeping all actual/orphan bytes in the aggregate inventory.
Three further native tests are authored, not executed: directory-fence refusal
with restart after complete rename and mtime-preserving reuse; moved byte-bundle
verification with a public revision above JavaScript's exact integer range and
equal-size corruption refusal; extra partial/foreign trusted hash/role/mode/
traversal/snapshot substitutions. Byte fixtures do not qualify encoded media.

##### Separate editable-copy processing capability

An additive `review_editable_pack_v1` capability admits the PR183 app-owned
private worker explicitly. The closed declaration identifies the worker,
context/result versions, v2 pack format and guarded prefix-copy policy; it
advertises no live-current, model provisioning, vendor or graph authority.
Native requires an already admitted separate processing release, matching
source/release manifest and unchanged complete protected original runtime/TTS/
mix/model/media inventory. It fingerprints the capability and ten ordered
worker/adapter/package/media dependencies. Their complete source-bound receipt
produces a native recipe hash; a separate context hash binds execution identity.
No worker/browser recipe string, original job rebind or helper copied into an
old runtime provides admission. New context/path hints are not native file reuse.

The capability is implemented in PR183; none of its source is copied into the
PR185 checkout. A combined source-matched processing release must actually
inventory those files before native can admit the capability. The existing
original/effective runtime admission bodies and all protected path rules are
unchanged. One new native regression is authored for missing capability, exact
ten-pin order, distinct context execution identities, changed worker byte and
original producer/root preservation. Local compilation is not retried with the
known missing MSVC linker; native execution and product qualification are unproven.

The private worker counts old/pending/orphan bytes and receives an optional
storage guard from its closed native context, while native must hold the writer
lease and fixed-volume authority. Native dispatch, bounded editable directory
inventory, full candidate semantic/physical admission, final current-token fence
and atomic editable retention/publication remain to integrate. Existing native
storage rejects the new editable staging tag until that explicit layout support
is delivered. No prior ROOT/job/native driver is used to bypass this boundary.
Eight new/changed PR183 Python component cases pass; they do not compile or
qualify this native receipt or establish installed/cumulative/crash/long-form
acceptance. Full172/174/175 gates and the ordered goal remain unchanged.

##### Native corrected editable publication and storage layout

`review-publish-active-editable-pack` connects the separate processing receipt
to the current standard bundle. The command requires the full actual token,
explicit processing root/source/manifest pin and a previously published exact
current bundle. SQLite remains read-only; the existing job guard excludes
cooperating mutations across admission, copy, final recovery and publication.
Native derives the context, output root and generation from opaque current
inputs, never a browser/worker path. The original/restored-original corrected
route still refuses. Existing standard exports and original job pins remain.

Native holds the same output-wide writer lease while the isolated app-owned
Python worker copies the private candidate. The sealed context includes the
native OS volume ID. The worker freshly compares that ID and fixed writable
NTFS policy before and during its four-MiB storage checks; caller-available
space remains distinct from total free space. POSIX device observations support
byte fixtures only; native production volume admission still requires Windows.
No worker observation grants native volume or live-generation authority.

The new bounded layout is `.editable-pending-v1/<generation>/import-pack` with
only the existing assembler's recognized sibling partial checkpoint form and
one resource-directory level. `.editable-published-v1` uses the same shape.
Inventories include every regular old/orphan/partial/other-job byte in the
existing32GiB aggregate quota; the same fresh2GiB caller reserve applies. Unknown
depth, links, reparse points and multi-link resources refuse. Older inventories
refuse these tags; qualification must use a new source-matched combined release
and output root, preserving every prior producer/root/job/model/evidence pin.

The native validator independently rebuilds the v2 complete resource manifest,
track order/default audio/subtitle policy and integer rational cue projection.
It checks exact copied canonical-timeline bytes and full resource hashes/sizes,
including all three B2 PCM files and the bound original/current bundle manifest.
It also reproduces the existing guide1 bytes and checkpoint marker so guidance
cannot redirect import to another resource. Complete directory inventory,
single-link handle identity and full SHA precede readonly sealing and sync.
Already sealed candidates are revalidated without rewriting bytes or mtime.

After copy, native rechecks the context and complete processing receipt,
releases its output lease and runs a second full physical current admission.
That admission must reproduce the exact standard-bundle identity. Under a fresh
writer lease it revalidates every candidate file, volume/quota/reserve and the
full durable current state before atomic no-replace directory publication.
It then reopens the published pack and fences current state before returning.
A late failure retains the complete version for explicit recovery; no prior
export is replaced/deleted. A known complete published version skips the worker
and is fully readmitted. The result grants no vendor dispatch or graph mutation,
and desktop `correction_ready` remains false until the remaining integration.

This source is pending hosted compilation/native execution. Six native tests
are authored for Python-generated B1/B2 byte compatibility, exact clocks/wide
tokens/confidence preservation, forged packets/manifests/roles/tracks/guide,
inventory/quota/unknown-depth refusals and atomic fence/restart/reuse behavior.
The Python volume-guard component run is4PASS in4.940s; two additional fresh
byte-fixture cases passed sealed-candidate recovery before the volume guard was
added. Those results are not summed with prior runs and do not execute Rust,
decode media, qualify installation or establish actual crash/long-form evidence.
All cumulative/after-undo/standard+editable/current installed B1B2/GUI/liveness/
actual-crash/real4h and full172/175 acceptance remain required. No source push,
duplicate dispatch, native product invocation, old-root replay or cleanup occurs.

##### Installed desktop current-state discovery and exact display projection

`review-desktop-state --data-root ... --job-id ...` is an additive read-only
native discovery route. It reads the actual durable head and runs the complete
physical effective-state reader before projecting findings for the desktop.
The separate display schema-v3 retains the entire actual token, including the
signed64 public revision as a canonical decimal string. It copies finding/plan/
source time-base fields from the admitted snapshot; it never rewrites durable
v2 snapshot bytes, hashes, graph identity or monotonic undo counters. The native
head is fenced again before returning. A corrupt/unsupported current graph is
an error, never permission to display the original as if it were current.

Only an absent native graph produces `original_only`. The new Tauri snapshot-v2
session may then read the existing original Python snapshot, but rechecks native
absence before delivery and binds the original export pin. A native current
session stores its full token in host memory and cannot use the old revision0
audio route. Original-only sessions recheck native absence before and after
reading audio so a newly initialized/corrected generation cannot play stale
original audio. Cancellation, bounded requests/sessions and existing deadlines
remain. Native physical recovery has a720s bound; its parent observation permits
740s for startup/pipe completion and remains cancellable off the UI thread.

The frontend strictly validates the new projection/token, preserves revisions
above JavaScript's exact integer range, displays the actual current revision and
uses the same integer tick labels. Public v1/v2 readers retain their formats.
The installed transport calls only the new session endpoint; native errors do
not fall back to v1 or stale original output. The old endpoint remains available
for explicit old clients. A desktop/supervisor version mismatch leaves optional
Review unavailable while standard output remains usable.

This closes current-state display and stale-preview selection, not the full
product workflow. Current-v3 views keep `correction_ready:false` and expose no
legacy revision-only edit/audio action. Full-token native cue playback,
correction/preview-confirmation, undo and standard/editable export actions still
need desktop wiring and current installed qualification. The Node component
suite and TypeScript no-emit check pass; two native supervisor and two Tauri
metadata/session tests are authored but unrun. No Rust compilation, GUI launch,
native product invocation, old job replay or acceptance completion is claimed.

##### Full-token current B2 cue audio

`review-current-audio` accepts all five current token fields plus finding/cue
identities. It opens durable state read-only, rejects a stale complete token,
runs full physical current recovery with the same720s walk, binds the finding
to its canonical cue and selects the cue's assigned TTS document from the
admitted effective composition. The accepted metadata store reopens that exact
document by full SHA/size; neither the display preview hint nor metadata-root
hints authorize file IO. Missing/ambiguous/failed cue audio remains an explicit
refusal. B1 cannot acquire invented synthesized speech.

The selected waveform uses its native-admitted artifact root and full content/
sample/channel/frame pins. A bounded memory reader checks every SHA byte,
PCM16 header/layout/frame count, single-link handle identity and path/mtime/
no-follow fences. Only the exact accepted original TTS metadata object retains
the archived producer's pinned-source policy, which does not promise a readonly
waveform. Every changed/retained document requires a readonly waveform. The
classifier compares the native accepted path, SHA and size; it does not fall back
to original audio when current audio is missing. It rereads metadata and the durable head before any
stdout audio byte is emitted. No synthesis, render, FFmpeg extraction, waveform
copy, job-root mutation or original-worker repin is performed. Restored and
cumulative heads select their actual effective document assignments while the
public revision remains unchanged.

Tauri `review_audio_v2` accepts the closed full-token request only for a current
session whose token is held in native host memory. It also checks the trusted
original completed-export pin before/after reading, rechecks cancellation/session
selection, and validates the bounded RIFF/WAVE envelope. Browser requests supply
no media path; playback still owns only an admitted memory Blob. The installed
transport separates old numeric-revision audio from current full-token audio,
rejects missing/forged/extra fields and cancels late replies. Current B2 views
enable the cue's listen action when a validated preview descriptor exists;
current B1 views preserve the absence of synthesized cue audio. Correction/undo/
export actions remain separately gated; correction_ready staysfalse.

The changed Node component suite and TypeScript no-emit check pass, covering
exact wide tokens, all token-field substitutions, path/revision injection,
current IPC routing and late cancellation while retaining old producer/transport
cases. Five new Rust/Tauri tests are authored but unrun for exact cue/document
selection, sealed PCM/hash/identity capture and refusals, host token binding and
pipe-envelope corruption. Native compilation, current whole-producer/installed
GUI/media/audibility/cumulative/after-undo/crash/long-form qualification remain
unproven. No product-native invocation or old completed QA replay is claimed.

##### Read-only current correction preview before confirmation

The installed client's proposal route is `review-current-plan`, with every
actual current token field, exact finding/cue identities and a closed changes
object. It opens SQLite read-only, physically readmits current content, checks
that complete generation dependencies exist, derives the mode from the native
source graph, and calls the existing native logical correction planner. It
rechecks the durable head before returning. No worker, processing release,
model, waveform, retained artifact or SQL mutation is executed by this command.

The proposal includes the exact before token, normalized changed values, native
stage plan, next decimal revision and canonical candidate snapshot hash. That
candidate is a logical proposal only: its unchanged preview paths are not new
physical media and it must never be published directly. The result explicitly
declares read_only:true, processing_release_admitted:false,
active_graph_published:false and execution_ready:false. Voice syntax and logical
scope do not prove model/catalog/processing compatibility; execution must still
admit the processing release, selected voice/model and complete physical result.

B1 translation plans contain translation/subtitles/render/qc/export and refuse
voice assignment. B2 translation plans also include TTS/mix; voice-only changes
start at TTS. Both retain ASR/OCR and the prior valid output. The original-only
readers and existing published snapshots are unchanged. Native counters after
undo remain monotonic; preview preserves their exact decimal identity, including
values above JavaScript's safe Number range.

Tauri `review_plan_v1` verifies the full host-held current session token and
trusted original export pin before invoking the reader, passes no browser path,
and rechecks session/cancellation/original identity on return. The client checks
the closed response against its actual current token, selected cue, proposed
changes, candidate revision/hash and exact native stage/retention policy. A late
response for another job, revision or edited draft is discarded. Preview does
not advance the connection's current token, manufacture a next graph hash or
invoke the old numeric-revision correction route.

Current B1/B2 views now allow a draft change and an explicit scope check. Draft
changes invalidate in-flight checks; no repeated full admission runs on each
keystroke. Navigation remains guarded while unsaved content exists. The separate
apply/confirmation action stays disabled until the full-token execution/CAS
route is integrated and qualified. Source execution, undo and standard/editable
desktop actions remain required for the full user workflow.

Actual Node client tests and TypeScript no-emit pass for exact wide tokens, B1/B2
scope, voice-only scope, forged fields/paths/plans, unchanged current snapshots
and cancellation. The first client assertion used the fixture's unchanged hash
as a purported forged hash; that QA failure is retained and the test now proves
each mutation differs before expecting refusal. Two native planner tests and one
Tauri session test are authored but unrun. Native compilation, physical current
product execution, installed GUI, actual crashes and long-form qualification are
not established by these component results. Full mandatory acceptance persists.

##### Explicit native confirmation and installed processing selection

`review-confirm-current` consumes an explicit confirmation of the complete native
read-only proposal. It readmits the current graph, re-derives that proposal and
requires exact canonical equality of before token, selected cue, changed values,
plan and candidate metadata identity. Extra fields and a missing/false confirm
are refused. The logical candidate remains unpublished; only physical retention,
semantic validation and the existing graph CAS may publish a replacement.

The command selects processing from its own installed `app/bin` layout. Its
compiled `SOURCE_SHA` must be an exact source commit; runtime environment values
and manifest self-assertions cannot replace a missing compiled identity. A fresh
bounded no-follow read checks release source identity, a unique native executable
inventory entry and the full running executable's hash/size. A second manifest
read must reproduce the same bytes/hash/size. No browser-selected processing
root, release label, copied helper or original worker repin is accepted.

This selection is not full processing admission. B1/B2 commit still performs
complete original/import/model/media/capability admission, native volume/retention
and quota fences, worker checkpoints, validated render/QC and exact graph CAS.
All existing original protected pins and processing compatibility rules apply.
The bridge changes no model/runtime/worker/contract pins in existing jobs.

The explicit current B1/B2 commit commands now require original manifest hash,
exact cue ID and the confirmed candidate snapshot hash in addition to the actual
revision/snapshot/graph token. Under the existing job execution guard they
re-derive and compare the candidate before context acceptance or worker execution.
A stale/mismatching confirmation fails before inference; non-commit preparation
commands refuse confirmation fields. The gap between preview and job-lock
acquisition therefore cannot silently switch to another current proposal.

Two native tests cover confirmation closure and source/executable selection;
two commit-gate tests cover missing/forged original/cue/candidate identity and
preservation of durable state. They are authored but not locally executed because
the known MSVC linker is unavailable. Rust parser/format checks pass. The desktop
apply handler, execution cancellation/liveness, refreshed result/undo/export
integration and actual current whole-producer/installed/crash/long-form tests
remain required before this becomes a qualified user workflow.

Code Review was restored during this work. It confirms published bb6 Fast,
Integration and Windows Release success, with stable publication skipped.
Returned Windows/Integration logs are truncated, so unseen counts are unclaimed.
That terminal evidence covers published bb6 only; pending cumulative/export/
desktop/confirmation source requires its own exact-head CI and native acceptance.

##### Current desktop apply session and uncertain-result recovery

The versioned desktop apply handler consumes an explicit confirmation of the
exact proposal returned by the native plan reader. Each current host session
retains at most one bounded pending plan; changing the native plan replaces it.
The host compares complete plan bytes as structured values and every token field,
then consumes the plan once. It invalidates existing sessions for that job and
holds a host mutation claim until the operation ends. A concurrent duplicate or
an attempt to confirm an unobserved/substituted plan refuses before native work.
The existing durable job lock/CAS still arbitrates other processes.
Snapshot readers cannot save sessions for the job while this claim is held.
Only the apply caller holding the same host's mutation guard may save the fresh
post-CAS session; a racing reload therefore cannot recreate its old token.

Only `review-confirm-current` may execute this request. Browser arguments contain
no paths or selected processing release. The native command independently
re-derives the proposal, selects its own source-bound installed processing and
passes through the existing physical/runtime/model/volume/quota/render/QC/CAS
gates. The old public v1/v2 correction routes and immutable original readers are
not used for current-state mutation. Snapshot `correction_ready:false` and plan
`execution_ready:false` remain provenance/qualification limits; this opt-in
handler does not turn a read-only plan into processing or product qualification.

The apply call runs off the UI thread with a bounded overall eight-hour host
observation window, separate from existing native phase/chunk bounds. Windows
cancellation kills its owned native process; that process already owns workers
through private non-inherited kill-on-close jobs before their execution byte
gate. This source relationship is not new physical cancellation/crash evidence.
An uncertain/cancelled result never authorizes automatic resubmission or a claim
that nothing committed. The client discards its binding and requires fresh
native discovery of the actual durable head. Standard output remains usable.

After native commit, Tauri compares the exact decimal acknowledgement with a
fresh physically admitted current graph, including revision, snapshot/graph SHA,
job/original binding and mode. A concurrent head advance refuses delivery as
uncertain. It creates a new native session only after those checks; no numeric
revision conversion or logical candidate hash is used as produced-media proof.
The acknowledgement's complete findings, time base and last-plan metadata must
also equal the fresh display projection; token agreement alone cannot hide a
different metadata response.
The logical proposal hash can differ from the final snapshot hash because only
native-retained preview paths are added after physical render admission.

The transport rotates to this fresh session/token only on a matching response.
The connection rejects changed source ticks/text/confidence/provenance, unrelated
cue content, mode, stage plan or unexpected revision. All finding preview paths
must identify the one retained render; they remain display data, not browser IO
authority. The client does not authenticate a proposed text/hash relationship;
the host-held exact plan and native re-derivation provide that authority.

Current B1/B2 views now separate draft, explicit scope check and user confirmation.
Editing/discarding a draft invalidates its checked plan. A duplicate submission
is blocked, cancellation is exposed during apply, and loss/mismatch of the reply
requires reload. The UI refreshes only from the fresh native committed snapshot.
Source initialization, current undo/export actions and measured progress/liveness
still require their own full workflow integration and physical acceptance.

TypeScript no-emit and the changed Node client suite pass for exact wide tokens,
B1/B2 current results, session rotation, duplicate/stale confirmations, immutable
source evidence and uncertain/cancelled replies. The first suite invocation was
a QA syntax failure from reusing a prior test's variable name; its log is retained,
the added identifiers are distinct, and the full suite then passed. Three new
Tauri unit tests are authored but unrun for saved-plan consumption/cancellation
and commit/fresh-head identity. Local MSVC compilation remains unavailable.
This pending source was not included in the in-progress f5 Windows build and
does not qualify installed GUI, native worker execution, actual crash or long
form. All full172/174/175 acceptance gates remain mandatory.

### Explicit full-token desktop undo integration

The optional desktop adds `review_undo_v1` and the closed
`desktop-current-undo-v1.json` request/result envelopes. They add no durable
schema, graph format, native undo command, processing version selection, worker
protocol or model/runtime pin. The webview confirms all five actual current token
fields; it cannot select an undo snapshot, graph, output root or filename.

The host consumes the bound current session before reading or mutating and
invalidates every older session and checked plan for the same job. Undo shares
the apply job guard, including refusal of racing snapshot-session admission. The
original export pin is rechecked before and after work. A fresh physical native
current read must equal the complete host token before calling the existing
`review-undo-active` with its exact revision/snapshot/graph binding. That command
already readmits both current and prior physical graphs and performs monotonic
undo under JobExecutionGuard. No native reader guard is relaxed or old job
producer repinned, and no inference/render is dispatched by the undo route.

After the command, its closed acknowledgement must agree with a newly physically
readmitted native schema3 projection: next signed64-range decimal revision,
snapshot/graph hashes, original manifest identity, all findings/time-base/last
plan metadata and the same B1/B2 mode. Restoration may reuse a prior content graph
but never its older public revision. Source text, cue/finding identities, integer
ranges, confidence, warnings and other immutable evidence remain exact; only
translation, voice, preview and last-plan content can return to a prior value.
Only the request holding the live job guard can admit the replacement session.

The UI requires an explicit second confirmation, blocks apply during undo and
offers cancellation without discarding existing exports. An old/foreign token,
offline/corrupt/no-history native refusal, cancellation or uncertain reply cannot
silently fall back, automatically resubmit or revive an old binding. Reload must
discover the actual durable head. A positive revision is not an availability
claim for remaining undo entries; native history remains the authority.

The changed Node suite executes actual client routing and the existing isolated
owned-Python component cases, including wide counters, stale/foreign token,
immutable-evidence refusal, post-undo planning/session rotation, B1, shared
mutation blocking and late cancelled/uncertain results. TypeScript noEmit passes.
Three additional Tauri unit tests are authored for request/guard/ack boundaries;
they have not run because local MSVC link.exe remains unavailable. Rustfmt/parser
checks are not Rust execution. No installed GUI/native undo/actual crash/long-form
test has run for this pending source. Source initialization, current standard and
editable desktop actions, measured liveness and all full172/174/175 cumulative,
after-undo, current whole B1B2, GUI/crash/real4h gates remain mandatory.

### Explicit current standard/editable desktop publication

The optional `review-confirm-current-export` broker consumes a closed explicit
`desktop-current-export-v1.json` confirmation over format and the actual complete
current token. It accepts only an owned control root and request JSON on its CLI;
browser-supplied processing/destination/worker/vendor paths are forbidden. It
opens durable state read-only and requires exact current-token equality before
publication. Standard format calls the existing native standard publisher without
processing capability selection. Editable format reuses the existing executable
compiled-source/installed-layout/release-manifest/native-full-hash selector,
performs full AdmittedProcessingRuntime admission and calls the existing native
editable publisher. That publisher still requires the exact published current
standard bundle. No old producer is repinned or worker copied into an old root.

Both publishers retain existing physical original/current graph admission, fixed
output-volume/canonical locator fences, aggregate quota/reserve, job and output
writer guards, streamed full hashes, sealed immutable resources and atomic
directory publication. Existing publishers and processing selection bodies are
unchanged. The broker rechecks the whole durable current state and result token
after publication; it performs no active graph mutation, schema migration, model
provisioning or vendor execution. Existing original and previous exports remain.

`review_export_v1` in Tauri requires the exact full-token current session,
consumes every older session/plan for the job and shares the apply/undo job guard.
It rechecks the original export pin and a full physical current read before and
after the owned native command. Fresh current content/token must remain exact;
exports never invent a revision increment. Closed native receipt fields must
agree on format, source-bound token, generation, manifest hashes and B1/B2 file
count. Editable processing/standard binding and no-vendor/no-graph-change flags
are checked before returning a smaller receipt and replacement session.

The client requires an admitted standard receipt before an editable action and
compares the editable generation/standard-manifest hash to it. UI confirmations
are separate; an editable failure preserves prior standard output/receipt.
Output directories are display text, not browser IO/playback/launch capabilities.
Old/foreign/cancelled/late/lost responses require discovery and never auto-retry.
Apply/undo invalidate in-memory export bindings, retaining actual old files.
Mixed versions, missing processing capability or original-only/non-exportable
graphs refuse; existing canonical standard output remains independent of Review.

Actual changed Node client/owned-Python component tests and TypeScript noEmit
pass, including both formats/B1B2, exact unchanged wide tokens and immutable
evidence, standard/editable linkage, session rotation, failed editable capability,
shared operation blocking and uncertain/cancelled replies. Two supervisor broker
tests and two Tauri boundary tests are authored but unrun; local MSVC link.exe
remains unavailable. Source initialization, action availability/measured liveness
and actual source-matched whole B1B2/cumulative/after-undo/standard+editable/GUI/
crash/real4h/full172/vendor/timing/175 acceptance remain mandatory. In particular,
the PR183 editable worker is still pending and absent from the published f5
bundle; no combined172+174 installation qualification is implied by this source.

### Windows raw export rename boundary repair

Exact f5 Windows38694611823/job116288155182 completed with237 supervisor passes,
2 failures and2 ignored tests. Both owned export-resume/directory tests failed
with Windows error3 at the successful-copy unwrap (review_assets.rs2643/2837).
The raw `MoveFileExW` boundary passed ordinary DOS paths directly while the Rust
filesystem calls had created/hashed/sealed deeply nested generation files.
Microsoft documents MAX_PATH by default unless extended path spelling or process/
OS long-path opt-in applies:
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw.
This source boundary is the diagnosed portability defect; actual successor
Windows execution is still required to prove those failures are repaired.

The Windows-only marshaller requires an already admitted absolute disk/UNC/
verbatim path, refuses device/relative/dot/parent/NUL/overbound input and encodes
the extended UTF16 form only for the raw call. Existing verbatim spelling is
preserved; stored locators, source roots and all native path/volume authority
remain unchanged. No canonicalize/samefile/prefix removal, source repin, fixture
shortening, registry/manifest opt-in or deadline increase is introduced.
`MOVEFILE_WRITE_THROUGH` stays set with neither replacement nor cross-volume copy
allowed. All existing no-follow/regular-file/full-hash/seal/identity/quota/reserve/
writer/current-state fences remain around the rename.

Two new Windows tests cover Unicode/DOS/verbatim/UNC encoding and refusal, plus
physical paths exceeding260 UTF16 units for sealed file and directory moves,
no-replacement preservation of both sides, content/mtime/seal and staging recovery.
Existing failing tests remain unchanged. Local rustfmt/parser/diff passes; the
new Rust tests are unrun because MSVC link.exe remains unavailable.

Two OS API diagnostic hosts on this machine accepted ordinary long paths, so they
do not reproduce the hosted failure or qualify this Rust repair. Their fresh
fixtures/results are retained. The second diagnostic's reproduction assertion
failed after a successful rename; it is QA evidence, not a native product failure.
No unmodified diagnostic/old native driver is retried. Published f5 remains
FAILED; successor exact Required-CI and actual product acceptance are mandatory.

### Explicit original-only desktop history initialization

The optional `review_initialize_v1` Tauri route and
`desktop-current-initialize-v1.json` accept only schema/kind/explicit confirmation
and original job ID. No browser snapshot/hash/root/worker/model/producer selection
is accepted. Only an Uninitialized session created by native-current-absent
discovery can proceed; legacy and already-current bindings are refused. That
session retains the exact bounded original display view in native memory with
the committed export pin, within the existing session count and metadata bounds.

The host consumes every prior job session and saved plan under the existing
apply/undo/export job guard. It rechecks the original export pin and native
absence before invoking the existing owned native `review-original --root
<owned-install> --data-root <owned-control> --job-id <bound-job>
--initialize-original`. Native original metadata validation, explicit schema
initialization and JobExecutionGuard are unchanged. No old producer/model/runtime
is repinned, worker copied, inference/render dispatched or existing generation
normalized to bypass the original reader. The native command's existing identical
original revision0 idempotence and later/corrupt generation refusal remain exact.

After initialization, the closed original acknowledgement must match the complete
stored original view at revision0, original manifest identity and new native
snapshot/graph hashes. A fresh full native current reader must agree on those
tokens and all time-base/findings/last-plan content before the request can admit
a replacement current session. Null confidence/preview and source/cue/ticks/
warnings remain exact. This grants metadata history only; later media reuse,
processing execution and correction CAS still require their full admission.

The UI requires a second confirmation, offers cancellation and refreshes actions
from the returned current connection. The first correction still requires its
separate native scope check and confirmation. Late/cancelled/lost/mismatching
results invalidate the binding and require actual reload, never automatic retry.
The original standard output remains usable if optional history admission fails.

The changed Node suite and TypeScript noEmit pass for actual owned-Python original
metadata plus native response fixtures: exact original-to-current projection,
closed request/token/path refusal, revision0, B1, action/session refresh and first
current plan, concurrent initialization block and cancelled/uncertain replies.
The first QA refusal test assigned null to an already-null preview; its failure
is preserved. It now asserts a genuinely substituted preview, and preserves the
actual fixture's missing-audio refusal instead of inventing a playable waveform.
Three Tauri tests are authored but unrun; known MSVC link.exe remains unavailable.
No actual native driver/original job/initialization is replayed for these source
tests. Publishedb11 Windows evidence cannot qualify this pending source. Measured
liveness/action availability, current whole B1B2/installed GUI/cumulative/afterundo/
standard+editable/crash/real4h and all full172/174/175 gates remain mandatory.


### Desktop Review request observation v1 (pending source)

The optional read-only `review_request_status_v1` IPC observes only host-owned
in-memory requests admitted under the existing mutation guard. Browser input is
only the opaque request ID. Status contains no root/path, model/producer pin,
revision, snapshot, durable writer instruction, raw stderr or retry permission.
The host records checking, native command processing and final verification,
monotonic total/phase elapsed times, and age of an actual child `try_wait` result.
Running and exited describe the process, not stage progress or successful CAS.
Request drop records ended even on failure; success still requires the unchanged
validated operation reply and current physical generation admission. Cancellation
records intent and retains existing bounded kill/reap behavior; if reaping cannot
be observed, process state is unobserved. No progress percentage, ETA or healthy
worker assertion follows from a process being alive.

The closed versioned client observation binds exact request/job/operation,
rejects unexpected fields and backwards time, and cannot change operation
results. At most one status IPC is outstanding; a five-second missing observation
becomes unknown, not a stopped task. Uncertain/late/cancelled results retain the
existing reload/no-auto-retry rules. Observation callbacks stop on completion or
selection change and never grant a stale session access. Tombstone/active limits
and expiry remain; status reads neither prolong leases nor alter durable state.

Compatibility/migration: additive optional IPC/callback, no durable schema or
worker format change. Existing hosts/callers without the observer retain their
behavior. Legacy snapshots and exact original/current producer pins remain.
Deterministic client tests and authored native process tests establish only this
component. Native chunk/stage advancement, actionable QC diagnostics, installed
GUI/process termination/recovery, real four-hour and long-form acceptance remain
mandatory. This pending source is not qualified by the earlier b11 binary/CI.


### Native Review stage/file observation v1 (pending source)

The desktop opts the owned native command into `DUBFLOW_REVIEW_PROGRESS_V1=1`.
Without that explicit observation opt-in the native CLI stays silent on the
progress channel. The native module writes fixed closed records on stderr with
`DUBFLOW_REVIEW_STAGE_V1 ` prefix, <=512 bytes each, <=32768 per invocation. A
16-record nonblocking queue and try-lock drop observations under contention;
a separate writer never holds the pipeline observation lock while writing.
No arbitrary text/path/job/token/producer bytes enter records, stdout is unchanged,
and no observation participates in admission, retries, inference decisions or CAS.
The host drains valid records separately (16MiB progress cap) while retaining
4096-byte ordinary stderr and existing stdout/deadline/cancellation/kill bounds.
Malformed prefixed diagnostics remain ordinary diagnostics and invalidate native
observation. Wire cap violations retain fail-closed output refusal.

Events mark actual current/processing admission, cue preparation/synthesis,
validation/mix/timeline/render/retention/commit and current reopen or export calls.
Only the existing successful complete per-file verifier increments runtime counts;
parallel counts are monotonic per walk epoch and a final count follows joined
workers and the existing complete-inventory/deadline check. Contention may drop
observations, never invent an extrapolated count or percentage. New walk epochs
reset counters explicitly. Native stage entry, worker wait and file validation
are not evidence of completed synthesis, chunk advancement, QC or durable success.

The separate `review_request_stage_v1` envelope retains the closed host activity
v1 unchanged and adds optional native observation. Native sequence/epoch/time/
counts and exact stage fields are validated independently of the durable result;
reordered, malformed or unavailable telemetry becomes unknown. Same-count records
do not reset advancement age. The UI may show the last observed stage, real file/
byte counts and age since the last change, while retaining cancellation/reload.
Host elapsed is sampled after the native event to avoid a cross-thread clock cut.
Cancellation/selection/disposal suppress late updates; no automatic mutation retry.

Compatibility: additive optional observation module and IPC, no durable/schema/
worker contract change, no processing/model repin or old-job replay. Existing
native binaries without events yield unavailable native observation; unchanged
host-only status remains readable. Compiled/current source and installed tests are
still required. Native worker/chunk internal advancement, actionable QC diagnostics,
source-matched complete producer B1B2, installed GUI/liveness/actual crash/recovery,
cumulative/new-after-undo/exports and real four-hour/long-form acceptance remain.
This pending source is not qualified by the earlier b11 binary or its CI.


### Optional bound desktop Review errors v1 (pending source)

The four explicit mutation IPC commands retain their success payloads/core
admission algorithms, but may return `review_desktop_request_failure` on the error
channel after actual host request/job/operation binding. Pre-binding/legacy errors
can remain strings. The typed error contains only schema/kind, exact opaque
request/job/operation, host failing phase, optional admitted native stage label,
bounded host message and optional bounded ordinary stderr. It never contains a
current token/root/snapshot/candidate, retry permission, success or a committed/
not-committed assertion. Outcome is always unconfirmed; failures can occur after
a possible durable CAS, so actual reload is required before another mutation.

An existing nonzero process exit captures <=4096UTF8 bytes from the ordinary stderr
reader already covered by its cap. Successful/unobserved/cancel-before-result
processes do not invent native diagnostics. Host message<=1024UTF8 bytes. Control
characters except newline/tab are filtered, UTF8 truncation keeps character
boundaries, and the existing64request/4active/120second tombstone limits bound
retained memory. Request drop retains the prior actual phase before ended; ended
is still not success. Success stderr/warnings never become failures or change low
confidence data. Native supervisor/worker/model/schema/deadline/physical/CAS logic
and source-version pins are unchanged by this display-only error continuation.

The transport validates the exact operation invocation before constructing typed
error details. The connection retains existing uncertain-result invalidation;
the generation-bound view displays details through textContent in a collapsed
wrapped disclosure. It cannot execute a diagnostic command, resolve a path, open
a link, play media, grant a session or retry work. Wrong-job/operation/late/cancelled
responses keep existing reload rules. Compatibility is additive on the optional
error channel: older clients continue generic failure handling, success contracts
remain exact, and no durable/worker migration or original pin rewrite occurs.

Deterministic client tests and authored Tauri process tests cover only this
component; the pending source is not earlier b11CI or installed product evidence.
Actionable native diagnostics do not substitute for structured full QC, actual
worker/chunk advancement, current whole B1B2/GUI/liveness/crash/recovery, cumulative/
new-after-undo/standard+editable exports/long-form/real4h or175acceptance.


Failure metadata additionally requires an invocation-local capture handle created
before that mutation task. Only successful fresh `begin` registration records its
actual cancellation-Arc identity in that handle; error serialization requires
pointer equality with the still-retained registry slot. A duplicate/pre-binding
attempt cannot borrow another request's error context even when IDs/job/operation
match. Lock contention or missing/expired capture degrades to the existing generic
error, never a false binding. This observation capability is separate from token/
producer authority and does not change shared session/apply guard ownership.

### Private source-PTS longform chunk planning v1 (pending source)

The current effective-render-v1 recipe still renders the entire video and only
reuses a fully completed candidate. It explicitly declares chunked_longform=false.
That declaration and every existing producer/job/context remain unchanged.

The new private longform-plan-v1 is the first scheduling component for a distinct
future render recipe. It consumes an ordered stream of decoded presentation
timestamps in the admitted source's reduced rational time base, retaining the
exact signed64 source origin (including negative/nonzero and values above2^53).
No float seconds, nominal FPS or frame ordinal becomes identity. Adjacent half-open
chunk intervals cover the entire source interval exactly once; every boundary
starts at a real next frame. target_ticks is a scheduling target, not permission
to fabricate frames or split a sparse held frame. A separate maximum frame count
limits dense chunks. Source duration is at most six hours by exact rational
comparison. The planner keeps only running SHA256 states and at most4096 chunk
descriptors, with a4MiB encoded metadata limit and a bounded planning deadline.
The process supplying the clock still requires its own native containment bound.

Each video chunk identity binds exact source bytes, processing/encoder/config
recipe, reduced time base, absolute interval, frame count, exact ordered PTS hash
and interval-local visual dependency hash. The full plan separately binds the
current request/context and whole frame-clock hash. A new request token or one
caption elsewhere therefore does not itself invalidate this chunk. The admitted
caller must include all local burn-in cues/styles/geometry/cleanup dependencies,
and any global configuration change in each affected dependency/recipe hash.
Audio is deliberately not made a per-video-chunk dependency: final admitted audio
and its continuity remain a separately required assembly/QC gate. This design
alone makes no assertion about AAC/encoder seams or exact VFR mux behavior.

The closed pinned-byte reader checks format/recipe, canonical decimal strings,
signed64 bounds, reduced rational clock, complete interval/frame-count coverage,
per-chunk keys and canonical byte encoding. A plan is policy data, never source,
caption, filesystem, volume, quota, checkpoint, render, QC or CAS authority.
Neither decoded PTS nor an untrusted dependency hash alone admits an artifact.
Native physical checkpoint admission and independent source-clock revalidation,
bounded FFmpeg chunk execution, caption global clocks, exact output PTS/seams,
final audio/subtitle mux/full AV QC, crash recovery and retained publication must
be connected before a new capability may advertise chunked rendering. Existing
32GiB aggregate/fresh2GiB reserve/no-follow/volume/hash/CAS/undo fences still apply;
actual six-hour policy fit and real four-hour regeneration remain unproven.

Compatibility is additive and private: no released format, original guard,
worker protocol, durable schema, shared media adapter, runtime/model pin or v1
capability changes. Planner tests qualify exact metadata and selective key reuse
only. They do not qualify chunked product execution, installed B1/B2/GUI/liveness,
actual crash/recovery, corrected standard/editable output or full174/172/175.

#### Bounded decoded-frame clock consumer (pending source)

longform_clock.py connects an incremental owned-ffprobe XML frame=pts stream to
the private planner. It uses bounded SAX parsing, not a complete JSON/frame array:
4096B input blocks,8192B unfinished tokens, at most32 bounded attributes per
element, a five-element stack, bounded total bytes/frame count and a cooperative
deadline. It retains exact signed64 source PTS, refuses missing/noncanonical/
duplicate/reversed values and never substitutes FPS, frame ordinal or a
best-effort timestamp. Known bounded codec side data supplies no clock identity.
DTD/entities/processing instructions/CDATA/comments/unsupported encoding and
unknown frame fields refuse. A partial XML tail cannot produce a complete plan
or clock summary, even after valid earlier frame records were observed.

The reader is a private consumer, not a new native process grant. The producer
must still be app-owned/inventory-pinned, select the admitted video stream, run
inside native timeout/cancellation/output containment, exit successfully with
admitted diagnostics and revalidate exact source bytes. Clock indexing itself
must use checkpointed bounded work windows rather than one full-longform decode
under a metadata deadline. ffprobe read_intervals seeks are not exact and cannot
establish a source boundary; actual decoded PTS and independently proved complete
coverage are mandatory. No seek normalization or floating-second identity is
introduced. Native indexed-window/checkpoint execution, FFmpeg render/assembly,
PTS/VFR/audio/caption seam checks and final physical/QC/CAS admission are still
unimplemented; effective-render-v1 and chunked_longform=false stay unchanged.

#### Bounded owned probe process adapter (pending source)

The private longform_probe module connects the decoded clock consumer/planner
to an explicit app-owned ffprobe executable, full executable SHA/size and a
no-follow source locator/SHA/size under an admitted root. Pins are rechecked
before and after execution. It selects the admitted integer video stream index,
requests exact frame PTS/known side-data XML, uses argv/shell=false/no interactive
window and never passes an approximate seek or user filter expression. The
processing release, executable inventory/compatibility and native process-tree
containment are still required outside this private adapter.

Two pipe readers feed a16-block bounded queue. Stdout uses4096B blocks and an
explicit byte bound; ordinary error diagnostics have a4096B cap. Deadline and
cancellation remain active while waiting for pipe output, EOF and process exit.
Every reader must finish and join; every child must reap under a separate2second
cleanup bound. EOF alone is not success: nonzero exit, any error-level stderr,
partial XML, malformed clock, early consumer return, read failure, output cap or
late source/executable changes refuse the entire plan. No partial observed
frame/chunk becomes a valid checkpoint. Native containment must recover if an
inherited pipe cannot join; this path cannot report success.

Deterministic actual app-owned Python child fixtures exercise streaming,
deadline/cancellation/reaping, output floods, diagnostics, early-consumer failure
and partial tails; mocked ffprobe dispatch exercises exact locator/pin checks.
They are process-boundary tests, not actual ffprobe/VFR/media qualification.
No render-v1 capability/deadline, original worker/model/producer pin, durable
schema, canonical source or graph publication changes. Native indexed-window
coverage/checkpoints, chunk encode/assembly/PTS/audio/caption seams, full QC,
actual crash/longform/installed product acceptance remain mandatory.

An explicit optional local owned-media component fixture additionally generated
two seconds of96x64 FFV1 video with32VFR frames at a nonzero2000tick origin in a
1/1000 source time base. The actual ffprobe process preserved the independently
defined frame PTS sequence and four500tick plan intervals; source and both owned
media executable hashes/mtimes stayed unchanged. This small synthetic source
does not qualify H.264 chunk rendering/mux, physical checkpoint reuse, installed
Review, B1/B2 regeneration or real four-hour product/longform acceptance.

#### Native physical-only longform chunk checkpoints (pending source)

The additive review_chunks module independently reads the closed4MiB planner
format against separate native plan/context/source/processing-recipe hashes. It
checks canonical bytes, signed64/reduced rational clock and six-hour duration,
ordered exact interval coverage/frame totals, bounded inventory and each local
chunk key. A Python-to-native fixture covers negative and above2^53 source PTS.
This metadata admission is not independent decoded-clock/media/QC admission.

Physical chunk retention uses the existing NativeOutputPin/NativeMixStore and
AcceptedAudioStore, without a second storage implementation or schema/migration.
The same output-wide OS writer lease,32GiB aggregate including old/undo/orphans/
other jobs, fresh2GiB reserve,32KiB copy/full SHA, regular/no-follow/readonly seal,
job/content/format namespace and canonical volume GUID locators remain mandatory.
The existing render budget is shared by visibility only; its values/behavior are
unchanged. After storing an immutable content-addressed metadata receipt, the
writer lease drops and an independent reopen reacquires the existing fence and
validates every physical byte. Hints never authorize IO. Missing/corrupt/unsealed/
foreign-job/changed-volume/changed-size objects refuse; no automatic regeneration
or fallback root is introduced.

The new retained-chunk-v1 receipt binds job, exact local chunk/clock/source/recipe,
native retained MP4 locator and archived originating plan/context hashes. Reuse
matches the current plan's local chunk dependencies, rather than requiring an
unchanged whole-request token after an unrelated correction or undo. Archived
origin pins remain provenance, not a grant to consume an old current graph.
Receipts explicitly require semantic_qc_admitted=false and
active_graph_published=false: this storage component does not admit codec,
decoded source-window completeness, actual output PTS/frame/geometry/audio/seams,
caption semantics, processing compatibility or CAS. The future complete native
render caller must independently perform those gates. Content-addressed receipts
may remain harmless quota-accounted orphans if a caller dies before recording
its private checkpoint index; no valid prior receipt is overwritten.

Actual Windows retention/canonical reopen/mtime/corruption tests are authored but
require newsource hosted Windows evidence; local missingMSVClink compile is not
retried. Cross-language deterministic metadata tests do not substitute for the
native physical test. No original job/worker/model/runtime/processing pin, durable
SQLite state or released format changes; full native indexed-window/checkpoint
execution, chunk rendering/assembly/QC/installed/crash/real4h gates remain. Current
effective-render-v1 still declares chunked_longform=false.

#### Source-clock fragmented video assembly (pending source)

A first new actual three-chunk H.264 component experiment failed despite clean
ffprobe/full-decode exits: each independent empty-moov mux reset tfdt to zero,
so unchanged fragment concatenation recovered only17 of49 expected VFR frames.
The failed complete experiment/source/raw probes/call logs/media are preserved.
This is an assembly clock failure, not proof that a clean decode preserves source
coverage. A distinct read-only-input continuation derives global fragment decode
time and each final frame hold from the exact source interval, restoring all49
independent expected PTS across sparse seams without any chunk re-encoding.

The new private longform_fragments module implements a bounded one-video-track
AVC fragmented-MP4 subset. Full pinned immutable input bytes, identical decoder
initialization, admitted coded geometry/exact timescale, a single explicit track,
default-base-is-moof, adjacent bounded moof/mdat pairs, explicit sample durations,
exact payload offsets/sizes, independent first sync sample and zero composition
offsets are required. Encryption/multi-track/reordered clocks/edit lists/unknown
sample tables refuse rather than silently normalize. The ordered encoded sample
PTS/count hash must equal the plan's exact source-PTS hash in every chunk. A
nonunit reduced rational clock rescales by checked integer multiplication; wide
or negative source origins never pass through floating seconds.

Only new candidate metadata is derived: monotonic mfhd sequence, global64bit
tfdt anchored at original source start and the final sample duration ending at
the admitted next-frame/source-end boundary. Interior sample durations and every
encoded mdat byte remain unchanged. Source-bound intervals, rather than inferred
last-chunk duration sums, determine all seams. Inputs are never rewritten. The
stale absolute random-access trailer is excluded from the new candidate; native
final mux/seekability/PTS/AV QC remains required before standard publication.

Memory is bounded to1MiB initialization/moof metadata,16MiB aggregate retained
fragment headers,32768 fragment pairs and32KiB media copy blocks; the encoded
candidate remains under the existing128GiB per-video ceiling, which does not
waive32GiB aggregate Review quota. A mandatory native-derived capacity callback
checks remaining copy bytes before creation/writes/every4MiB/preseal, alongside
deadline/cancellation and full readonly input hash/mtime rechecks. Errors remove
only that newly created private partial candidate, preserving prior artifacts.
This callback interface does not itself admit volume/quota/root authority.

Actual tiny owned-media tests cover49VFR frames across three h264_mf software
encoded fragments, exact source-derived output PTS, geometry/codec/timebase and
full video decode with all inputs/executables hash/mtime unchanged. Deterministic
tests cover signed/wide origins, rational rescaling, foreign clock/codec/offset/
composition/sample inventory, mutable inputs, capacity failure and cancellation.
These are component tests, not native current B1/B2/product qualification.
The source-clock indexed-window producer, checkpoint scheduler/native semantic
admission, global caption clocks, audio continuity/final mux, hardware fallback,
fullAV/QC/CAS, installed GUI, actual crash and real four-hour acceptance still
must be connected. Original render-v1/current capability remains unchanged and
chunked_longform=false; no original worker/model/runtime/processing pins migrate.

#### Bounded source-clock chunk encode and global captions (pending source)

The private longform_captions reader binds complete UTF8 ASS bytes to an exact
current pin and retains each selected event's original global centisecond clock
and bytes. Exact integer rational overlap selects only the events that intersect
a source chunk; events crossing a seam are neither clipped nor rebased. Header
and local event bytes determine the visual dependency. An unrelated later cue
does not invalidate an earlier chunk, while a style/header change does. Native
current timeline/caption provenance and subtitle semantics still need admission.

longform_encode executes a fixed h264_mf software recipe with no B frames. It
binds owned FFmpeg/ffprobe bytes, helper source bytes, selected integer video
stream, geometry/time base, subtitle choice and the native-admitted runtime
environment hash. The environment must cover Media Foundation codec/OS and
DirectWrite/GDI/font selection/fallback bytes and configuration; an FFmpeg hash
alone does not bind those dependencies. A mandatory native observation callback
must independently revalidate that environment before and after each process.
This callback interface does not implement or grant native closure admission.
The tiny Latin/Arial component fixture pins its observed font file only; complete
installed font/codec/provider closure remains unimplemented and unqualified.

An actual first bounded encode failed with an empty MP4 initialization despite
exit zero: copyts plus accurate absolute input seek discarded the selected
nonzero-origin source frames. Its raw logs/media and independent changed-option
diagnosis are retained. The fixed recipe uses absolute seek only as an earlier
microsecond hint with noaccurate_seek, keeps the pre-roll source PTS, and applies
exact integer trim. It stops output at the independently planned frame count;
seek position or inferred FPS never decides identity or coverage. Full encoded
sample PTS/count hash must still equal the independently admitted source clock.

Filters trim on source PTS, subtract the original source origin while applying
the unmodified global ASS events, and only then place the encoded chunk at its
local zero origin. The existing strict fragment assembler derives its final
sample hold. This recipe refuses absolute filter PTS above binary64's exact
integer range and unsupported MP4 timescales before execution; signed64 metadata
support does not waive the still-required exact wide-clock render mapping.

Each bounded child has no stdin/window, a 16-block pipe queue, streamed private
output, native capacity callback checks and the existing shared cancellation/
deadline/diagnostic/reap fences. Exact source/executable/environment pins are
rechecked around execution. Raw plus normalized private chunks have explicit
byte bounds; only fresh scratch is removed on failure, and hard-crash orphans
remain quota-accounted. No unchanged retained input or original worker pin is
rewritten. native-encoded-chunk-candidate-v1 records provenance/full video
decode with semantic_qc_admitted=false and active_graph_published=false; its path
is a hint, never a canonical native locator or authority to reuse/publish.

An additional QA failure used an incomplete ASS style fixture, yielding no
visible glyphs while source PTS were already correct. The failure is preserved;
the corrected fixture supplies the full existing producer style fields. New
tiny tests independently compare the selected nonzero-origin VFR PTS and two
decoded image samples before/during a global caption interval. They do not
qualify current native B1/B2, full runtime closure or installed product behavior.
Native indexed-window coverage/checkpoint scheduling, independent physical and
semantic chunk admission, audio continuity/final mux, hardware fallback, AV/QC/
CAS, standard/editable publication, installed GUI, actual crash and real four-hour
acceptance still must be connected. Current effective-render-v1 and
chunked_longform=false remain unchanged.

#### Native ordered physical chunk scheduling (pending source)

The native review_chunks scheduler walks the separately pinned current plan in
source order. A matching local key may select an older request's checkpoint;
every selected receipt independently reopens through the existing native media
store. A changed local dependency invokes the native processing executor rather
than treating an unrelated request token as a reason to repeat all chunks.
Corrupt/foreign/missing selected checkpoints refuse without an implicit encode
retry. Current job/token/source/processing/runtime and cancellation fences apply
around dispatch, retention and checkpoint boundaries; each phase uses the lesser
of the caller's absolute deadline and the existing900second render bound.

An encoded worker packet must bind exact current plan/context, source, processing
recipe/runtime environment, local chunk/caption/frame-clock hashes and frame
count. Its closed private candidate is not trusted semantic media evidence.
MP4 bytes still retain/reopen through the existing no-follow/readonly/fullSHA/
canonical-volume/output-writer/32GiB media quota and fresh2GiB reserve. Native
independent decoded source coverage/codec/geometry/caption/audio/QC is mandatory
before any final use. No worker boolean, path hint, callback or hash grants those
admissions. This scheduler remains unavailable through the current render entry.

After each physical chunk completes, the supervisor accepts one immutable index
node and requires the native caller's durable checkpoint callback to record its
pointer before advancing. A node contains just this chunk and its previous
content-addressed node, not a repeated whole-prefix array. Walking requires exact
node bytes/SHA/size, same job/source/recipe/clock/origin pins, decreasing bounded
ordinal, no repeated key and a valid root. A current-plan chain must be its exact
completed ordered prefix. Older origin pins preserve history only; every current
required key is reconsidered. Each attempt creates a fresh chain from its own
root, so unrelated edits/resumes do not accumulate old chains into one walk.
At most4096nodes/16MiB metadata are read or emitted per chain. Metadata capacity
must independently account all old/undo/orphan accepted metadata under the native
aggregate budget before and after accepting records; the native callback must be
implemented before this interface becomes an execution route. Failure can leave
quota-accounted immutable orphan nodes but cannot overwrite an earlier index.

New deterministic Rust tests are authored for ordered local reuse, partial-prefix
resume, cancellation/stale/deadline/checkpoint failures, corrupt reuse refusal,
foreign candidate/index fields and a4096node linear metadata bound. They have NOT
RUN: known missing localMSVClink is not retried, and exact hosted source evidence
is still required. Source parses/formats with rustfmt only. The native processing
executor, bounded source-clock indexed-window producer, runtime font/codec closure,
durable pointer/capacity implementation, current-render wiring, final audio/mux/
QC/CAS/standard+editable output and installedGUI/crash/real4h acceptance remain
unimplemented/unqualified. No SQLite/worker protocol/model/original producer
change or public longform capability activation is introduced.

#### Output-owned bounded checkpoint metadata prerequisite (pending source)

Connecting an executor exposed a storage gap in the authored scheduler: its
AcceptedAudioStore index lives under DATA, while the native media quota and
writer lease protect the output volume. An opaque capacity callback is not an
implementation of that admission. Holding another output writer lease around
the existing retain function would recursively acquire the same lease. No such
callback is implemented or routed into current rendering.

RetainedMixStore now has a separate bounded JSON-record primitive for native
checkpoint metadata of at most64KiB. It uses the same already-held output-wide
OS writer lease, content/format/job namespace,32GiB aggregate count of media,
metadata, old/undo/unreferenced/copy-orphan and other-job bytes, and fresh2GiB
caller reserve. Known sealed exact bytes reuse without a new copy or changing
mtime. New writes reserve before creation and before each32KiB block, fsync,
validate exact raw JSON/SHA/size, recheck reserve and deadline before exclusive
publication, and reopen readonly single-link metadata. Corrupt/unsealed/linked
collisions refuse without repair. Only that new private temporary is disposable;
prior records remain untouched. Exact wide integers/UTF8 bytes are not rewritten.
This storage primitive grants no record semantics or graph authority.

NativeMixStore wraps that primitive in fresh volume observations, the existing
output GUID locator, no-follow/readonly/fullSHA/identity checks and independent
canonical reopening. Absolute hints do not authorize IO. No metadata is copied
to a control-root fallback, and no original ROOT/job/worker/model is repinned.
The existing JSON retained-file format/path namespace is reused; original
artifact/schema/SQL/worker formats are unchanged.

The old global4096 filesystem-entry bound cannot contain a4096chunk plan plus
its receipts and index nodes. The finite entry ceiling is raised to65536 while
the32GiB aggregate byte budget, fresh2GiB reserve and all cooperative deadlines
remain unchanged. Every entry is still observed/refused on links or unknown
nested namespaces. Tests use a smaller private fixture limit to exercise the
same actual filesystem refusal without creating65536 files in ordinary CI.
This is a bounded inventory policy change, not proof of six-hour fit or latency;
actual Windows/full-longform inventory admission remains mandatory. An older
controller may refuse new large inventories; it must never evict or silently
repin them. Existing standard exports stay independent of Review admission.

Four new core Rust cases cover exact sealed reuse/reopen, shared media/orphan/
other-job quota and writer exclusion, changing/unknown space/invalid JSON/
deadline bounds, and linked/unsealed/corrupt/foreign-role refusals. One Windows
case additionally covers the real native volume/locator/hint/independent reopen.
All five are AUTHORED/NOT_RUN; rustfmt/diff checks are syntax/source evidence only,
and known missing localMSVClink is not retried. The DATA-based prototype scheduler
still needs explicit versioned output-metadata integration and durable checkpoint
discovery/pointer handling before an admitted executor may use it. Source-clock
window coverage, complete runtime closure, actual worker containment/current
render integration/final audio-mux/QC/CAS/installed/crash/real4h acceptance remain
unimplemented/unqualified. Current chunked_longform=false remains unchanged.

#### Output-only physical checkpoint receipt/index v2 (pending source)

The new private review_chunk_output backend connects the ordered native chunk
scheduler to output-owned metadata. A completed chunk's MP4 and receipt-v2 JSON
share the existing output store; index-v2 predecessor and receipt references
are canonical output JSON locators, never AcceptedAudioStore paths under DATA.
The old DATA-v1 API/format remains unchanged and independently readable. A v1
pointer supplied to v2 refuses; no implicit conversion, copied old helper,
producer repin or original-job rewrite occurs. No SQLite migration or original
worker/runtime/model format changes are introduced.

The existing ordered scheduler loop now abstracts its index type/packet builder
alongside its physical backend. The same cancellation/deadline/local-key reuse,
completed-prefix checkpoint and aggregate chain-byte checks serve v1 and v2.
The new v2 walker independently reads bounded native output metadata, verifies
locator/content hash/size, exact canonical closed bytes and version, source/
recipe/timebase/job, immutable origin pins, decreasing ordinals/root/unique local
keys and the current plan's completed prefix. It retains the4096node/16MiB chain
limits. All selected receipts reopen their canonical video locators independently.
Hints cannot authorize filesystem reads. Unknown/missing/corrupt/foreign-volume
objects refuse without an encode repair or fallback to DATA.

One existing output-wide OS writer covers video plus its small receipt creation
and shared32GiB aggregate old/undo/orphan/alljob quota with fresh2GiB reserve.
The writer scope ends before independent reopen. Each new index node similarly
uses the output JSON API and drops its writer before invoking native current/
runtime fences or the durable checkpoint-pointer callback. An independent reopen
follows that callback. This removes the prototype's opaque metadata quota callback
and avoids recursive output writer acquisition. No storage lease crosses native
state callback boundaries. Failed pointer publication may leave quota-accounted
immutable output orphans but never retires the previous valid chain.

Two new deterministic Rust cases are authored for the same-loop partial resume/
unrelated-context reuse and strict v2 chain/locator/origin/format/byte refusals.
A separate Windows test is authored for actual output volume/canonical record
reopen, physical reuse preserving video/receipt mtimes, no DATA/control artifacts,
foreign-volume refusal and reacquiring the real writer inside the pointer
callback. Its video bytes and executor are explicit physical-storage fixtures,
not real encoding/runtime/semantic/QC evidence; the callback observes pointers
and does not claim an actual crash-safe state writer. All three new tests are
NOT_RUN until hosted evidence for this new source. rustfmt/diff is syntax/source
evidence only and the known missing localMSVClink is not retried. The previously
published f6 crossplatform scheduler/metadata tests passed, but do not qualify
this successor. Required f6 Windows diagnostics remain independent.

The admitted processing executor, native current token/source/runtime closure,
complete indexed-window source clock,
audio/final mux/QC/CAS/current render routing and standard/editable/installed GUI/
actual crash/real4h acceptance still require implementation/qualification.
This backend adds no current renderer entry or capability grant: both receipt
and index require semantic_qc_admitted=false and active_graph_published=false,
and effective-render-v1/chunked_longform=false remain unchanged.

#### Immutable output checkpoint journal v2 (pending source)

The native output backend now persists each completed index before releasing
the existing output-wide writer. A sealed marker lives under the same job's
output object store as `.chunk-checkpoint-v2-<64planhex>-<ordinal4digits>.json`.
Its closed version2 packet contains the native job/current full plan/context,
ordinal and canonical output index descriptor. This private discovery namespace
is separate from content-addressed media/records and introduces no mutable head,
SQL stage write, DATA fallback, worker protocol or original producer change.
An exact existing marker reuses bytes and mtime; any changed collision refuses.
No old marker, chain, media, undo generation or orphan is overwritten/evicted.

Startup scans only this admitted job store with the existing finite65536entry
bound, cooperative deadline/cancellation and fresh native volume observations.
Matching marker names must be canonical contiguous ordinals1..N. Each record is
bounded64KiB, the complete journal4096markers/16MiB. Markers require no-follow
regular handles, readonly seal, single link, stable identity/mtime/size and full
byte hash/syntax readback. These bytes participate in the same32GiB aggregate
old/undo/orphan/alljob quota and fresh2GiB reserve, including private copies if a
process dies before publication. Writes use exclusive private creation,32KiB
blocks, fresh space checks, fsync, exclusive publication and independent sealed
readback. A crash may leave quota-accounted private or index orphans; it cannot
turn an incomplete private copy into completed progress.

Names only locate candidate markers. The native reader admits exact canonical
packets against its current plan and independently reopens every index through
the canonical output locator. Native job/source/recipe/timebase/context, ordinal,
ordered local chunk key, exact predecessor descriptor and final completion flag
must match the current completed prefix. A gap, foreign origin/role/volume,
changed byte or fork refuses with explicit recovery required. Preserved path
hints remain exact archived metadata and supply no IO authority after relocation.
The current durable prefix supplies reuse even when no UI/cache pointer arrives.
Archived local-key caches can fill later chunks but cannot replace this prefix.

The optional executor checkpoint callback is now an observation after native
journal publication and writer release; persistence does not depend on it. If
it fails, the next fresh invocation discovers the already sealed prefix and
revalidates all physical receipts instead of repeating completed encoding. This
implements the physical prefix discovery/publication that the earlier output-v2
paragraph listed as missing; full processing and graph publication are separate
remaining requirements. Old DATA-v1 files/APIs remain unchanged. Old controllers
may refuse an unfamiliar output inventory; they must not repair/evict/repin it.
Standard exports remain usable independently of Review admission.

Four journal storage tests and two marker/current-prefix refusal tests are
AUTHORED/NOT_RUN. The storage kill test launches only its exact ignored child
entry in a validated fresh OS-temp fixture, with bounded output/waits and no
Windows console window. After the child's sealed-marker signal it forcibly
kills that actual process, reopens the output writer/marker, and appends the next
ordinal while checking prior bytes/mtime. This is an authored actual-process
storage test, not executed crash evidence for the processing worker, real media,
full Review graph, installed GUI or real4h acceptance. The ignored child entry
adds one intentional helper skip in future hosted suites. The existing Windows
output fixture now retries without a UI/cache index and requires exact recovered
index identity; all successor Rust tests await source-matched hosted evidence.
Targeted rustfmt/diff checks cannot prove compilation or test success, and the
known missing localMSVClink is not retried. No current render route/capability,
semantic QC, active graph or public longform flag is enabled by this journal.

#### Private native chunk callback transport v1 (pending source)

The existing Python encoder calls capacity/runtime callbacks while consuming
bounded output. Those checks must execute in its native supervisor, where the
current token, canonical volumes, aggregate quota/reserve and runtime closure
are authoritative. A new private Review child channel carries only exact bound
requests, acknowledgements and one unadmitted result. It does not alter the
original worker daemon/event protocol, original producer/model pins, SQLite,
DATA-v1 or the completed original job. Its separate version1 contract refuses
unknown versions, fields, operations, sequence reuse or binding changes; there
is no implicit compatibility conversion or replay after a refused callback.

The native binding contains current context/plan/local chunk/environment hashes.
Each canonical UTF8 JSON frame ends in one LF and is bounded64KiB, total child
stdout16MiB and4096requests per process. Capacity is a canonical decimal0..512MiB;
a fence request carries zero bytes. Acknowledgements bind the complete exact
request hash, sequence and native binding, and can only report accepted=true.
Any native refusal ends the contained process instead of sending a reusable
negative grant. A final result preserves its exact nested JSON bytes, but is
transport data only: existing encoded-candidate/physical source-clock/AV/QC
admission must still independently validate it before retention/publication.

The native collector reuses AdapterProcess/WorkerTree and attaches containment
before the owned Python byte gate. One absolute native deadline of at most900s
covers request handling, process completion and every callback; it is never
renewed per message. Pipe readers use a bounded16event queue and bounded line
reads. A dedicated writer and one-entry acknowledgement queue prevent a child
that stops reading stdin from blocking supervisor deadline/cancel observation.
The lightweight native observation callback runs between bounded polls; each
capacity/runtime request invokes its separate authoritative native check. No
storage writer should cross those callbacks. Empty stderr, complete stdout EOF,
zero exit and all acknowledgements written are required before returning one
result. Extra/truncated/oversized/replayed packets, pipe errors, diagnostics,
cancel or timeout refuse. Error paths terminate/reap the owned process tree,
release pipe queues and bound worker-thread cleanup; a partial result is never
returned as success. Original collect callers retain their existing behavior.

NativeChunkChannel connects encode_chunk's existing live capacity/environment/
cancel callbacks to this transport and emits its sole result only after encoder
success. The caller cannot substitute those callbacks. Native requests and
acknowledgements must keep the same exact context/plan/chunk/environment; a
refused or malformed acknowledgement or an encoder error poisons the channel.
This helper grants no filesystem, processing-release or media authority and is
not a public worker capability or a browser-provided command entrypoint.

Seven new owned-Python cases passed, including an actual isolated owned child with
live capacity/fence/result pipe exchange, foreign/replayed/noncanonical/oversize
acknowledgements, encoder callback wiring/failure, callback replacement refusal,
wide integers and request/deadline/result bounds. Shared exact request/ack/result
golden frames are consumed by both Python and the authored native protocol test;
only the Python side has executed. The test wrapper's trailing-LF assertion
failed after the seven-test exit0/PASS record on Windows CRLF diagnostics. A
separate read-only audit checks the unchanged raw log and source pins without
replaying tests; production transport bytes still require exact LF/canonical
framing. The failed QA wrapper is retained. The real pipe fixture has no
media/descendants and uses a Python test parent; it does not prove the authored
Rust collector's actual Windows containment or product crash behavior. Three
new native protocol/refusal tests are AUTHORED/NOT_RUN; localMSVClink is missing
and not retried. The admitted processing-release dispatcher/current token and
source indexed-window admission, complete font/codec/OS closure, concrete native
capacity implementation, full encoder-worker/native pipe integration, final
audio/mux/AV/QC/CAS/current renderer/installed GUI/actualcrash/real4h gates still
require implementation/qualification. Neither this transport nor its component
tests enable effective-render-v1/chunked_longform or change standard exports.

#### Native chunk private-capacity extents v1 (pending source)

The native callback transport needs a concrete private-work capacity fence that
does not retain the output-wide writer throughout encoding. Each native job and
current scope receives one sparse regular control file under its existing
retained job store. Its logical length accounts for the bounded private-work
allowance plus its small header. Existing used_bytes already counts every
regular logical file length, including foreign/orphan files and every job, so
older quota writers account for this allowance without a new namespace parser.
The shared32GiB quota and fresh2GiB caller reserve remain unchanged. Sparse
allocation reserves logical quota; fresh physical caller-free space is observed
separately for every requested allocation and output volume GUID.

The closed version1 canonical header contains native job namespace/scope/private
limit and the pinned work directory identity. It is prefixed by a four-byte
big-endian length and bounded4096bytes; the full logical length must equal that
prefix/header plus native private allowance. The native maximum is512MiB media
plus64MiB auxiliary metadata. The dispatcher must derive the actual scope and
allowance from admitted current/source/processing recipe/local chunk/runtime
bindings. Worker/browser packets cannot select paths or grant that admission.
The native work path derives only from output/job/scope. It is separate from old
original jobs/checkpoints; preexisting nonempty work cannot be adopted as a new
reservation. Same-scope recovery requires the exact sealed header/limit and work
identity. A live control owner refuses competing recovery.

Creation/recovery runs under the short existing output writer. Its returned
opaque control/work handles survive after that writer drops. Windows disallows
delete-share for these pinned handles; the control owns an independent OS lock.
Each native capacity callback reacquires the normal output store, rechecks
control identity/readonly/single-link/no-follow/header/sparse/logical size and
work directory identity, walks bounded private regular files, and enforces
existing private bytes plus requested additional bytes within the allowance.
The walk is cancellation/deadline aware, at most4096entries/depth4, and refuses
links or unknown nonregular entries. It checks shared old/undo/orphan/alljob
quota and fresh caller-free bytes on the original NTFS volume before returning.
No output-wide writer crosses a worker callback or current/runtime fence.

Drop or process death releases handles while preserving the complete counted
extent and all private work. A new native caller may recover the same exact
scope after owner exit; corrupt/unsealed/wrong-limit/foreign-job/work-identity
changes refuse with explicit recovery required. Explicit successful release
requires zero private files, including zero-byte files. It clears only its own
control seal and requests Windows deletion by the pinned DELETE-capable handle,
then confirms the extent is absent. No workspace/media/evidence file is deleted
by this API, and empty native work directories remain. This resource lifecycle
does not authorize retrying any previously denied payload cleanup. A dispatcher
still must validate/retain completed media, publish the native journal and retire
only its fresh owned scratch before requesting release. Crash orphan recovery
and final cleanup ordering remain product acceptance requirements.

The sparse control extent is quota metadata, with exact bounded header checks;
all retained media and JSON records retain their separate full-byte SHA/physical
and semantic admission. Original worker/SQL/model/runtime formats, DATA-v1 and
canonical retained locators are unchanged. Older controllers continue counting
the regular extent and may leave an unfamiliar resource for explicit recovery;
they must not overwrite/evict it. No existing original root/job is repinned.
Windows uses documented [FSCTL_SET_SPARSE](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_set_sparse),
[SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
and one-byte [FILE_DISPOSITION_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_disposition_info).
An independent owned-Python Win32 API probe passed on actual fixed NTFS: its
8388941logical-byte quota extent allocated65536physical bytes, remained readable
for a legacy metadata-length observer, blocked active-owner recovery and file/
directory rename, reopened with identical header/identity after owner handle
close, and released only the pinned control handle. The fresh empty fixture was
removed. This API component probe executed no Rust/media/worker/product/crash
path. Six native capacity tests are AUTHORED/NOT_RUN for alljob quota/writer
release, remaining private bytes/caller space/cancel, owner recovery and empty-
work release, foreign job/limit/seal/header refusals, depth/directory identity/
Windows rename protection and missing-work refusal without implicit recreation.
Native compiled execution, complete dispatcher/runtime/source-clock/AV/QC/CAS,
installed GUI/crash/real4h qualification remain unproven. No public capability
or current renderer route is enabled by these capacity primitives.

#### Isolated pinned chunk processing entry v1 (pending source)

The new longform_execution entry connects a sealed private execution context to
the existing encoder and native callback channel. It requires owned Python
-I -S -B and the native process-containment byte gate before importing the engine
or reading the context. Only a context path and its independently admitted full
SHA are accepted as command arguments. The closed canonical context is at most
128KiB and binds the existing current-context/plan/local-chunk/environment hashes,
source/processing recipe, exact physical input pins, native roots, source geometry,
stream, caption mode, private media limit and bounded execution duration.

Context/plan/optional captions are read-only regular single-link/no-follow inputs.
Every bounded read checks exact handle/path identity, size, seal, full SHA and
mtime before and after reading; the original observations survive across encoder
execution. Replacing metadata with identical bytes at a new physical identity
still refuses. Plan is at most4MiB, ASS at most64MiB, private chunk at most256MiB
and execution at most900seconds. Exact signed64 plan metadata is preserved; the
encoder's existing explicit FFmpeg timestamp compatibility bounds remain intact.
No float/FPS/ordinal replaces canonical source clock identity.

The worker verifies plan/source/recipe/local chunk membership before encoding.
Initial and final environment/capacity requests use the existing monotonic native
request/ack wire; live encoder callbacks use that same channel. Context, plan and
caption metadata are independently reopened before the sole final response. Any
metadata change, refused native callback, failed encoder or deadline prevents
that response. Output remains an unadmitted encoded candidate, and partial
scratch stays under the existing native quota/recovery policy.

The additive processing-chunk-dispatch-v1 declaration is admitted only through
the existing exact processing-release/original protected-runtime compatibility
reader. Its explicit entry and ordered source dependencies must all match the
complete inventoried release. Preparation-only and older releases remain valid
for their existing operations and refuse this missing/unknown capability. No
old producer/root/job/worker/model/runtime pin, durable SQL or public protocol is
rewritten. This receipt explicitly grants no current/source/environment, media,
semantic QC, graph publication or public chunked_longform authority.

The complete native executor must still connect this processing receipt and
entry with independently derived current/source/environment admission, indexed
source-clock windows, the native capacity token and output journal, bounded
writer contention, retained media/seam/audio/full-QC/CAS and scratch retirement.
Existing effective-render-v1 remains unchanged. Installed B1/B2/GUI/liveness,
actual processing-worker crash, corrected standard/editable publication and
real four-hour/longform qualification remain mandatory.

The first exact2338259 Windows build compiled but failed six capacity tests
before any quota allocation: work_identity passed a directory handle into the
existing regular cue-media identity checker. This is a native implementation
failure, not an inference or fixture pass. The pending repair uses a separate
directory-only BY_HANDLE_FILE_INFORMATION reader, requiring the directory bit,
exactly one link and no reparse attribute. Retained regular media continue using
the unchanged single_cue_file guard, which still refuses directory handles.
The native directory regression checks the52byte ABI, stable reopened directory
identity, regular-file refusal on the directory path and continued directory
refusal on the media path. Hosted newsource Windows execution remains required;
the earlier independent Win32 primitive probe did not execute this Rust helper.

#### Native chunk encoder recipe derivation (pending source)

The native processing-release reader now derives the existing Python encoder
recipe from freshly fingerprinted inventoried FFmpeg/ffprobe bytes and the exact
eight encoder helper files. It first requires the separate closed private chunk
dispatch capability and the complete original/protected processing inventories.
Missing or altered helpers and foreign executable paths refuse before a recipe
is returned. No helper is copied into an old producer or silently repinned.

The typed native selection bounds source geometry, requires a positive reduced
signed64 time base, preserves the selected video-stream index and burn-in choice,
and includes the independently supplied native environment SHA. Native hashing
uses the same closed sorted canonical object as Python, including decimal-string
file sizes in context pins versus numeric sizes in the release inventory. Every
helper/executable/configuration/environment input contributes to the identity.
A Python-generated oracle covers B1 non-burn and B2 burn metadata selections,
including a non-unit rational time base. Native regressions compare exact bytes
and hashes, reject malformed/missing/extra dependencies and reopen actual helper
files to refuse same-size corruption or disappearance.

A recipe identity is not an admission of the selected current graph, source
geometry/decoded clock or codec/font/provider/OS dependency closure. Those values
must still come from independent native readers before the contained executor
may consume this receipt. The receipt therefore keeps all current/source,
environment, semantic-QC, public-longform and publication flags false. Existing
effective-render-v1 and all original worker/model/runtime/job bindings remain
unchanged. The source dispatcher, indexed clock windows, native capacity and
output-journal connection, final audio/seam/AV/QC/CAS and real installed B1/B2,
GUI/liveness/actual-processing-crash/representative-four-hour acceptance remain
mandatory; fixture or recipe equality cannot substitute for any of them.

#### Native current source and chunk-plan binding (pending source)

CurrentChunkSource first connects the existing native effective-render reader
and separately derived processing recipe. Its exact sealed context/source/recipe
pins are available before clock planning. Consuming bind_plan then produces a
CurrentChunkPlan for those pins, avoiding a circular dependency between deriving
the context and generating its plan. The effective
reader reopens the current full token, immutable original manifest/source bytes,
cumulative corrected timeline and retained captions/mix. The new component
accepts no client geometry, source root, video-stream choice or time-base fields.
It seals the derived current render context and binds the plan's context/source/
recipe hashes to those exact native values before accepting the plan metadata.

Geometry, primary stream and the exact signed64 PTS interval come from the
archived original stream probe. Selection matches the pinned production adapter's
first video stream and refuses duplicate or unsupported stream indices. A
positive reduced rational and exact positive duration_ts/duration_ticks agreement
are required; missing exact duration needs explicit compatibility. Negative and
above2^53 PTS remain integer values. End overflow, over-six-hour sources, inferred
float/FPS clocks and rounded canonical-millisecond interval substitutions refuse.
BoundPlan independently requires the complete source interval and time base to
equal this selection after its existing canonical/hash/local-chunk admission.

Before returning and on explicit revalidation, native reconstructs the complete
effective context, reopens source bytes/current dependencies, rederives all
processing recipe pins and reopens the immutable plan. Changed tokens, content,
source or processing cannot be repaired by overwriting a saved context. The
context/plan are private metadata in the existing accepted store; no SQL schema,
old job/model/worker/runtime pin or public contract is migrated.

This component binds archived stream metadata and exact source bytes. It does
not independently decode frame coverage or admit the codec/font/provider/OS
environment, and it cannot dispatch a worker or publish a graph. Both admissions
must still be supplied to the complete contained executor, indexed-clock windows,
native capacity/output journal and final audio/seam/AV/QC/CAS integration. Public
chunked_longform remains false. The Python stream-timeline oracle and authored
native parser/interval regressions are component evidence only; complete current
installed B1/B2, corrected standard/editable outputs, GUI/liveness, actual worker
crash/recovery and representative real-four-hour acceptance remain mandatory.


#### Resumable decoded source-clock windows v1 (pending source)

The previous one-shot probe remains supported. The additive resumable path uses
app-owned ffprobe with selected-stream decoded PTS, keyframe/packet positions and
exact source/executable pins. One packet-limited process is bounded by 16MiB
stdout, 65536 decoded frames, at most16384 selected packets and180seconds.
An approximate microsecond seek only locates a candidate. The first resumed
decoded frame must equal the saved integer PTS, keyframe and packet position.
No floating time/FPS/frame ordinal replaces canonical signed64 source identity.

A non-final window requires three advancing observed keyframes and ends at the
third-last one. Lookahead extends through the second-last keyframe only. This
keeps a complete guard GOP before the packet-limited decoder flush tail: that
tail can emit a final keyframe before its preceding B frames have even been read.
The next observation must reproduce the exact stable lookahead before new points
are usable. The comparison is never relaxed to subsequence or approximate PTS.
Saved records also cross-check overlapping PTS before planning. Seeking or a
packet limit does not grant complete coverage. A short packet-limited candidate
must match an independently executed suffix with no packet/end limit under the
same remaining absolute deadline and byte/frame limits. EOF is accepted only
when the last decoded PTS plus its explicit positive duration equals the native
archived exact source end. Missing timestamps, packet positions, keyframe
lookahead, exact duration, unknown shapes or incompatible seek ranges refuse
with compatibility evidence; no best-effort or inferred timestamp is substituted.

A closed canonical record binds the source/executable/stream/time-base/full
interval specification and stores bounded exact PTS plus next-anchor/lookahead.
Native-owned read/checkpoint/fence callbacks reopen the contiguous saved prefix,
publish each immutable record and return exactly independently reopened bytes.
Each invocation decodes at most the configured number of new windows, allowing
pause/crash recovery to reuse saved observations. The journal has4096records,
16MiB aggregate metadata and1000000frames limits; only hashes of prior records
remain in memory. A complete plan streams PTS by independently reopening every
record again and uses the existing interval-local dependency planner.

This adapter writes no worker-owned SQLite, publication pointer or file. The
native caller must implement contained workers, full source/current/runtime
admission, immutable output-volume journal/capacity and authoritative contiguous
inventory. A record/seek/packet position/hash is not native decoded-media or
codec/font/provider/OS authority; both media/QC admission flags remain false.
A complete native decoder/index policy for supported codecs, concrete longform
executor, audio/finalmux/seam/fullAVQC/CAS/currentrenderer, installed wholeB1B2,
standard/editable publication, GUI/liveness/actualprocessingcrash/real4h gates
remain mandatory. Existing effective-render-v1/chunked_longform=false and old
producer/model/runtime/job pins are unchanged; older releases simply lack this
private entry and cannot consume the new format. No SQL migration is needed.

FFprobe documentation explicitly distinguishes requested seek start from the
actual start and defines packet-count interval stops. The implementation keeps
these positions as non-authoritative locators:
https://ffmpeg.org/ffprobe.html#Main-options


#### Native output-volume clock journal v1 (pending source)

CurrentChunkSource now derives the Python clock specification from its already
native-selected primary stream/source interval and exact owned ffprobe pin.
The physical journal namespace is domain separated from video-plan checkpoints
and includes the complete native processing recipe identity. Unrelated cue
edits do not change decoded source-clock identity, while a changed source,
probe/processing recipe, stream or rational requires a different namespace.

Native independently parses canonical window bytes before retention or reuse.
It checks exact signed64 PTS/anchor text, numeric wide lookahead PTS/packet
positions, strict source coverage, per-window frame hashes, exact overlapping
PTS, EOF/full-interval shape and false media/QC authority flags. The contiguous
journal keeps the existing4096ordinal,16MiB raw metadata and1000000frame limits.
Window/marker/page bytes alone never prove that a trusted contained decoder
produced them; actual decoder admission remains a separate required step.

A window is at most4MiB. It is divided into immutable24KiB byte pages encoded as
lowercase hex in small canonical JSON, each below the existing64KiB record
limit. Each page binds namespace, complete record SHA, exact byte offset and
the next page's native canonical output locator. Pages are sealed from tail to
head; only after all are reopened/hash-checked is a small ordinal marker
published. Recovery reconstructs the exact UTF8 record through at most172pages,
refuses cycles/noncontiguous offsets/foreign namespaces/changed sizes or hashes,
then independently checks the complete native clock prefix.

This reuses the existing NativeMixStore writer, same actual volume identity,
read-only single-link/no-follow/fullSHA checks, aggregate32GiB old/undo/orphan/
other-job accounting, fresh2GiB caller reserve and immutable ordinal marker
publication. Existing64KiB small-record limits are unchanged. No larger-record
waiver, DATA fallback, SQLite stage, mutable head or old-object cleanup is added.
A failed page/marker publication leaves ordinary accounted orphan bytes;
a changed collision refuses rather than overwriting. Current/source/processing
fences run before and after; no output-wide writer crosses the final fence.

Native metadata and physical page tests are authored but not executed locally
because the known MSVC linker is absent. Python independently admits negative
and above2^53 golden source-clock records; that does not qualify the Rust code.
Actual contained decoder dispatch/new processing capability and entry, complete
codec/font/provider/OS admission, indexed-clock production, capacity integration,
final audio/mux/seams/AV-QC-CAS/currentrenderer and full installed B1/B2,
standard/editable, GUI/liveness/actualprocessingcrash/real4h remain required.
Publicchunked_longform=false and all old producer/model/runtime/job pins remain
unchanged. Old releases/other record kinds cannot interpret this distinct
private page/marker format; no automatic format or SQL migration occurs.


#### Contained native source-clock execution v1 (pending source)

The explicit processing-clock-dispatch-v1 capability introduces one owned
isolated Python entry for one bounded clock window. Native first revalidates
the complete original/protected processing release and all20 direct clock
dependency files plus the owned ffprobe executable. The recipe binds the closed
capability, exact file/executable pins and complete processing-release identity.
Missing/unknown capability or changed imports refuse before Python starts;
nothing is copied into or repinned inside an old job/producer/model tree.

CurrentChunkSource advances at most one ordinal selected from its physically
reopened native clock journal. Native derives/seals the source specification,
current cursor, exact anchor/lookahead, source/probe paths and pins, decoder
recipe/release hashes and bounded packet/time policy. No browser-selected
command/root/geometry/clock can supply these values. A changed complete
processing release now uses a domain-separated namespace_v2 identity; prior
private clock markers remain immutable and are not rewritten or auto-migrated.

The owned entry requires Python-I-S-B and the native byte gate before engine
imports or context reads. Its read-only regular single-link context is at most
4MiB and uses exact handle/path identity/fullSHA/size/mtime checks before and
after decoding. The file role distinction is preserved: inventoried executable
dedup hard links are validated by the existing owned-executable reader; media
source/context retain single-link/no-reparse checks. The first actual isolated
fixture exposed this distinction and failed before decoding; the scoped role
repair and regression preserve that failure observation.

A separate native collector leaves the original metadata collector unchanged.
It attaches WorkerTree before opening the byte gate, uses bounded stdout and
4096byte diagnostic readers, one absolute at-most180second deadline and native
cancel/current observations. Failure/deadline/pipe overflow/diagnostics refuse
the result; the tree is terminated and readers are joined under a bounded
cleanup interval. Successful child exit alone never authorizes a journal append.

The sole closed result binds exact context/spec/decoder-recipe/ordinal and
complete window bytes/hash, with all media/environment/QC/graph flags false.
After child completion native rereads the sealed context, re-admits the complete
processing receipt and current/source dependencies, independently validates
the result and window prefix, then calls the existing output journal append.
No writer lock crosses execution/current-runtime fences. The worker writes no
durable SQLite or checkpoint file. Actual full native execution/containment and
processing crash qualification must still be demonstrated with source-matched
Windows binaries, complete source coverage/index policy and installed product.

Two schema signed64 extrema were authored through a JavaScript numeric path
that rounded them. The pending correction writes exact decimal JSON integer
tokens -9223372036854775808 and9223372036854775807 in both affected contracts,
with an independent Python exact-integer regression. Native existing signed64
bounds remain unchanged; no runtime clock compatibility or precision waiver.

Local Python context/gate/result/refusal/schema cases and an actual isolated
owned-ffprobe tinyVFR entry are component tests. Native collector/capability/
result tests are authored but locally NOT_RUN due known missing MSVC linker.
Full decoded coverage, codec/font/provider/OS closure, admitted chunk execution,
capacity/writerwait/scratch retirement, final audio/mux/seams/fullAVQC/CAS/current
renderer, whole installedB1B2/cumulative/newafterundo/standardeditable/GUI/liveness/
actualprocessingcrash/real4h and full172/175 gates remain mandatory. Public
chunked_longform=false and existing effective-render-v1/old runtime-model-job
pins are unchanged. Unknown context/capability versions refuse; no SQL migration
or implicit public protocol/graph-format conversion is introduced.


#### Native source-clock command and decoder-only reuse namespace v3 (pending source)

The private `review-advance-source-clock` command now reaches the native
contained one-window collector and physical clock journal from the supervisor
command dispatcher. It accepts only an absolute control root, job, all five
actual current-token fields and the explicit processing-release selection.
Unknown/duplicate/incomplete flags refuse, including caller source/probe, clock,
ordinal, candidate text/voice and encoder environment. JobExecutionGuard covers
the invocation; SQLite is opened read-only. Fresh native current/original
admission determines the immutable original manifest, selected primary stream,
source-derived signed64 ticks/reduced rational, source full bytes and output
volume. The command never synthesizes a cue, creates a candidate correction or
mixes audio to decode the original source clock. It supports original/current
B1 and B2 through the same typed current reader without changing their meaning.

This supersedes only the earlier pending clock API placement on
CurrentChunkSource: decoder execution now has a separate CurrentSourceClock
type with a full admitted clock-processing receipt. The encoder-plan type and
its environment checks remain unchanged. No synthetic environment identity or
encoder recipe is required or supplied as decoder authority. Namespace_v3 binds
the complete clock specification, full decoder recipe and complete processing
release hash. It intentionally excludes candidate edits and encoder labels so
unchanged source observations can be reopened after an unrelated edit/undo.
Old v1/v2 namespaces and pages remain immutable, without conversion, rewrite,
eviction or cleanup. A future caller must independently join complete source
coverage to its current encoder plan; reuse never grants that admission itself.

One call reopens the physically verified prefix, derives the next ordinal and
exact overlap/anchor, executes at most one <=180second contained window and
appends only after native result/source/current/processing revalidation. An
already complete prefix is returned without another decoder process. Existing
720second phase bound, output NTFS volume/no-follow/singlelink/read-only/fullSHA,
32GiB shared old/undo/orphan/other-job quota, fresh2GiB reserve and writer-drop
fences apply. A worker or failed invocation writes no SQLite state or mutable
clock head. Process interruption leaves only existing accounted immutable
pages/markers; full actual processing crash/Windows qualification is pending.

The closed native-source-clock-progress-v1 observation includes the actual
current token and journal cursor/count/complete flag; all decoded admission,
environment, semantic QC, chunked_longform and graph flags remain false. This
private command/format has no public desktop capability, migration or old-job
producer/model/runtime repin. Existing apply/render/publication routes remain
unchanged. Native token/parser/namespace regressions are authored, locally
NOT_RUN because the known MSVC linker is absent. Source-matched hosted tests
and actual installed producer execution are still required, along with full
clock coverage/index policy, codec/font/provider/OS closure, contained chunk
encoding/capacity/writerwait/scratch retirement, audio/finalmux/AV-seams/QC/CAS,
whole installed B1B2/cumulative/newafterundo/standardeditable, GUI/liveness,
actualprocessingcrash/real4h and every full Issue172/175 acceptance gate.

#### Fresh processing receipt path admission (source preparation)

Actual source-matched d8 Windows qualification observed 864.437 seconds for
current B2 CAS, 191.703 seconds for a separate current reopen and 401.765
seconds for current standard export. These whole native operations repeatedly
walk complete runtime inventories. The raw stage evidence records separate
epochs, never a global unique-file total or controlled OS-cold benchmark.

A private preparation_paths helper now returns both effective-cue and native
dispatch paths after one fresh effective_entry admission and the full pinned
dispatch fingerprint. receipt consumes that pair directly. The public
preparation_dispatch wrapper performs the same fresh helper call on every
invocation. No admitted paths or inventory result is cached across calls.

This removes the duplicate effective_entry call within one receipt. The
complete original and processing final inventory walks remain in the unchanged
effective_entry body. Both manifests, the capability and effective entry are
reopened there; the native dispatch fingerprint still runs after that fence.
Original AdmittedRuntime methods, complete full-hash import admission,
missing/extra/link refusals, deadlines, current and publication fences,
processing receipts, recipes, public capability flags and job/model bindings
retain their existing meanings. No schema, SQLite, worker format or package
migration is needed. Rollback uses the old implementation with unchanged
receipt bytes and old immutable jobs/producer roots.

A new native regression first creates a valid receipt, then changes each
entry/capability/release with equal-length bytes, introduces an unlisted import
and removes an expected import in either tree. Every subsequent receipt must
refuse; restoring exact bytes/inventory must restore the same receipt. It is
authored but NOT_RUN locally because the known MSVC linker is unavailable.
Rustfmt/static source guards do not qualify runtime behavior or latency.
Source-specific hosted compilation and actual Windows whole-pipeline timing
remain required. Existing d8 native results do not qualify this new source.
All full Issue172/174/175 cumulative/undo/new-after-undo/current standard and
production editable/installed GUI/liveness/actual crash/longform/real4h gates
remain mandatory. Public chunked_longform and authority flags stay false.


#### Bounded source-clock output writer contention (pending native qualification)

The current source-clock journal's actual read/append path now uses an explicit
short writer wait. Existing generic storage constructors retain their immediate
contention refusal. The new path waits at most five seconds and never beyond the
caller's existing absolute phase deadline; it polls cancellation between native
nonblocking lock attempts and sleeps at most25ms per interval. A deadline or
cancellation before open creates no namespace, lock or job directory. Reaching
the bound refuses the operation without retrying a child or changing any source.

One opened writer handle is retained for the complete wait. On acquisition its
regular/single-link/no-follow physical identity is compared with a fresh open of
the same lock path. A replaced path cannot authorize locking a second file while
an earlier supervisor still owns the original writer. The empty writer file is
never truncated, unlinked, cloned or unlocked early. The existing all-job quota,
old/undo/orphan accounting, fresh reserve and immutable page/marker rules remain.
Native fixed writable NTFS/GUID identity is checked both before and after a wait;
no drive-letter hint or previous volume observation becomes fresh authority.

The source-clock caller retains its pre/post current/source/processing fences,
one-window limit and original phase deadline. No writer crosses those fences.
This change does not add an encoding/current-render route or grant decoded media,
codec/font/provider/OS closure, semantic QC, graph or public longform authority.
It does not alter any SQLite schema, durable format, worker protocol, producer,
model or existing job pin. Old releases preserve immediate contention refusal;
rollback requires no migration and keeps every saved clock page/marker intact.

Three native regressions are authored: real OS-lock contention across jobs with
release, cancellation/deadline without taking the current owner's lock, and a
Unix late-path replacement refusal. Rustfmt/parser and exact-source guards are
static evidence only. Local native compilation/execution remains NOT_RUN because
the required MSVC linker is known missing; no repeated compiler/SDK/cache action
is attempted. Own-source hosted CI and Windows physical integration remain
required. This is one connected contention fix, not complete chunk execution or
product acceptance. All full172/174/175, current installed B1/B2 cumulative edits,
standard/editable outputs, GUI/actionable liveness, actual processing crash,
source coverage/index, environment closure, chunk capacity/scratch/audio/finalmux/
seam/fullAVQC/CAS/current renderer and representative real4h gates remain required.
Publicchunked_longform stays false and defaultTrucLy25voices is unchanged.

#### Native current plan versus complete reopened clock (source preparation)

Native current chunk-plan binding and revalidation now require the complete
physical source-clock journal for the same native source pin, exact stream and
integer source interval. Preparation/readback use the existing admitted clock
processing release and existing output-volume writer scope. The writer drops
before the current/source/processing fences are checked again. The bind and
revalidation APIs take the caller's cancellation observation and existing absolute
deadline; no retry or larger deadline is introduced.

One bounded window at a time supplies exact signed64 points. Native recomputes
the complete and per-chunk newline-delimited point digests, counts and first/final
coverage, and derives each cut from the pinned target-tick/max-frame policy.
Missing, extra, changed, duplicate, reordered or noncanonical points, an incomplete
prefix, a different source, a self-consistent plan with different clock hashes or
non-policy cuts, cancellation, expiry and point-reader errors all refuse binding.
Existing16MiB/4096-window/one-million-frame limits remain. No whole-source point
array, persistent admission cache, FPS substitution or float-seconds identity is
introduced. Existing physical marker/page verification remains unchanged.

This is a connected additional check in the private current-plan API. A physically
reopened observation still is not independently admitted decoded media, encoder/
font/provider/OS closure, semantic QC, rendered chunks or graph/public capability.
All existing packet authority flags remain false. The current render/clock command
bodies, original job/model/runtime pins, SQL, worker protocols and on-disk formats
are unchanged. No migration is needed; rollback accepts the same saved journal and
plan formats, and cannot acquire new authority from this check.

Six native regressions are authored for negative/wide VFR points, changed streams,
valid metadata with foreign hashes/policy, late cancellation, multi-window
iteration and incomplete/corrupt journals. They remain NOT_RUN locally because
the known MSVC linker is absent; source formatting is not compilation or execution
evidence. Own-source hosted compilation and actual native/installed qualification
remain required. Full independent decoded/index admission, native chunk executor,
capacity/scratch/audio/finalmux/seams/fullAVQC/CAS, current installed B1B2/cumulative/
standardeditable/GUI/liveness, representative real4h and full172/174/175 acceptance
are not waived. The pending CB B2 recovery cohort keeps its original source and
producer while this isolated source preparation proceeds.


#### Chunk worker observation after containment and before permission

The bounded private chunk collector now performs a second native current/cancel
observation after attaching the child to WorkerTree and before taking its pipes
or starting the writer that sends the permission byte. Process creation and
containment can consume time or overlap a head/cancellation change after the
pre-spawn observation. A refusal at this point drops the already-owned process,
terminates/reaps it and sends no permission byte. Successful observation is
followed by the same absolute-deadline check, including late callback completion.

This changes no worker frame/ack/result format, capability, byte bound, 900-second
maximum, native callback, output quota/reserve or original metadata collector.
No new current renderer/public executor is enabled and no decoded-clock, codec/
font/provider/OS, semantic QC or active graph authority is granted. Original
producer/model/job pins and every saved private/public format retain their
meaning; rollback needs no migration. Published CB qualification stays pinned
while this isolated successor source is prepared.

One real-owned-child native regression is authored for both a post-containment
current/cancel refusal and a callback that finishes after the shared deadline.
The child records reaching its byte gate and records work only after receiving
the permission byte; both refused cases require no work marker or native
callback. The ignored helper is launched only by that test. Local native compile/
execution remains NOT_RUN because the MSVC linker is known absent; formatting
and source guards are static evidence only. Own-source hosted compilation and
Windows execution remain required. This collector fence is not a complete chunk
executor or product qualification. Full indexed/source/environment admission,
capacity/output journal/scratch/audio/finalmux/seams/fullAVQC/CAS/current renderer,
installed B1B2/cumulative/standardeditable/GUI/actionable liveness/real4h and
all full172/174/175 gates remain mandatory; public chunked_longform stays false.


#### Identical physical processing inventory fence (source preparation)

Actual current CB B2 caller qualification reached its unchanged2400s bound.
The failed result, full native streams and complete unchanged revision2 SQLite
closure remain preserved. The separate successful different-cue call observed
342 runtime import-tree epochs; this label includes full hash admission and
metadata-only final inventory, not342 proven full-hash passes or unique files.
The original immutable runtime uses an NT path spelling and explicit processing
selection uses a DOS spelling. Equal text/pins or stripped prefixes alone cannot
prove physical identity.

The processing receipt and editable receipt now select one fresh metadata walk
only when their entire original and processing inventory maps are exactly equal
and Windows independently proves both plain directory handles share a volume
GUID path,64-bit volume serial and128-bit file ID. Bounded ancestor/root handles
(up to64 per spelling) deny write/delete sharing through that one walk. Each
probe/recheck has a one-second observation bound. The transient read root is
derived only from the held handle's volume-GUID path; it is never serialized,
reused as a job/worker root, accepted from a client or stored for a later phase.
Both original spellings and held handles are reopened/rechecked before and after
the complete unchanged file walker. Any selected-proof revalidation failure
refuses; there is no retry through a changed path.

Unavailable physical proof, unsupported platform/filesystem, excessive ancestry
or any unequal pin map retains the original two independent complete walks.
There is no admission cache, skipped per-file guard, relative path/prefix
normalization waiver, original producer/model/job repin, quota/reserve/deadline
increase or public authority change. Original full-byte runtime admissions,
manifest/entry fingerprints and the metadata walk bodies remain unchanged.
Later receipts and execution/publication fences perform new physical checks.
Existing180s per walk and enclosing phase/caller bounds retain their values.
No durable format or SQLite migration is needed; rollback uses the unchanged
receipt/plan/worker/job formats with the previous duplicate walks.

Win32 references: [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[FILE_ID_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info),
[GetFinalPathNameByHandleW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew).
The GUID-path observation does not grant media, environment, QC or graph authority.

Four native regressions are authored: actual NT/DOS alias one-walk with fresh
missing/extra/resized refusal, distinct roots/equal maps and same-root/different
map refusal, held owned-root/parent replacement refusal followed by release, and
bounded excessive ancestry. They are NOT_RUN locally because the MSVC linker is
known absent; source formatting/static guards are not compilation or execution.
Own-source hosted compilation and actual Windows native/installed timing and
mutation tests remain required. No measured native speedup or full product
acceptance is claimed. All complete172/174/175, installed B1B2/cumulative/exports,
GUI/liveness, decoded/index/environment/chunk executor/capacity/scratch/audio/
finalmux/seams/fullAVQC/CAS/current renderer/representative real4h gates remain.
Publicchunked_longform stays false and approved TrucLy25voices is unchanged.


#### Directory read access required for Windows sharing exclusion

The exact847 Windows supervisor suite compiled and ran311passing tests, one
failing physical test and five ignored helpers. The unchanged owned-root/parent
regression observed a successful root rename while the proposed fence was held.
That failure remains preserved; no same-source rerun or artifact admission is
authorized by the earlier Linux passes.

The directory handle requested only FILE_READ_ATTRIBUTES (0x80). Attribute-only
access does not establish the read/share exclusion needed to refuse a later
DELETE-access open. A new owned Windows direct-access probe independently showed
that such an open succeeds under0x80, whereas FILE_LIST_DIRECTORY combined with
FILE_READ_ATTRIBUTES (0x81) refuses it with ERROR_SHARING_VIOLATION while held and
permits it after release. All marker bytes/mtimes/inodes remained exact; the probe
performed no delete or rename. An earlier Python os.rename hypothesis failed
locally and remains recorded separately, without replay or a false pass claim.

The optional proof now requests the minimal directory-list plus attribute READ
access. Write/delete access and sharing remain absent. The complete ancestor
handle lifetime, GUID/volume serial/file ID checks, both before/after path
reopens, one-second probe bounds and complete file walker remain unchanged.
A directory/ancestor that cannot grant this read access makes the optional proof
unavailable and retains the existing two independent inventory walks. No
authority, format, migration, quota/reserve/deadline, original job/model/runtime
pin, processing root or worker protocol changes. The failing native physical
test is unchanged and must pass on this successor's own Windows build.

References: [CreateFileW sharing rules](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[directory access masks](https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/access-mask),
[Rust OpenOptionsExt access override](https://doc.rust-lang.org/std/os/windows/fs/trait.OpenOptionsExt.html).
Local Rust compilation remains NOT_RUN with the known missing MSVC linker; no
SDK/cache mutation or unchanged compile retry. The Win32 component observation
does not qualify the native Rust implementation, latency, installed B1B2,
cumulative edits/exports/GUI, longform or full172/174/175 acceptance.

#### Immediate current publication may carry prepare's private admission

The actual f68 same-cue B2 caller reached its unchanged2400-second bound while
runtime validation was still observed. Its original failure, native streams,
checkpoints and unchanged revision2 state remain preserved. This successor
removes one redundant initial reconstruction between prepare and the immediate
current-only commit. It does not infer a measured latency improvement.

Prepare still performs the complete existing physical v3 admission. It returns
a process-local Rust value with private fields, no serialization/deserialization
or Clone, consumed once by the current B1/B2 commit. The value binds the exact
before token/state, original artifact/manifest and origin, sealed publication
descriptor, proposal, snapshot and graph. Its existing prepare deadline must
still be live when commit begins. Client JSON, an old receipt or a restarted
process cannot mint or recover this value.

Commit always creates a new RecoveryWalk and invokes the complete existing
recursive publication reader. The final independent history/runtime/import and
media verification, full hashes, missing/extra and no-follow checks, proof/read
limits and720-second final admission budget remain mandatory. Fresh before,
origin, snapshot, graph and record must equal prepare's admission. Sealed object
and proposal are reread, current state is checked again, and the existing SQLite
token CAS remains the publication boundary. A refusal leaves prior rows/media
intact; a concurrent successful writer keeps its winning head. Legacy JSON
commit and recovery readers retain their previous admissions unchanged.

There is no stored admission cache, phase-wide verification reuse, durable format,
schema, worker protocol, job/model/root repin, cleanup or deadline/quota change.
Rollback reads the same saved records and restores the previous duplicate
reconstruction; no migration is needed. Six new candidate Ubuntu regressions
exercise orchestration, late mutation, expiry, exact comparison and CAS races.
Their injected private oracle is not positive encoded-v3 native admission.
Source-matched Windows supervisor/junction/native execution and actual timing on
the final integrated source remain required. The previous caller failure is not
relabelled. Full installed B1B2/cumulative/exports/GUI/liveness and172/174/175 gates
remain; public chunked_longform stays false, AUD-0 and TrucLy25voices unchanged.


#### Source enumeration completion is distinct from native retirement

A completed source page commits its discovered rows before the enumerator has
necessarily exited. The desktop keeps Download unavailable until the matching
private source_status carries enumeration_retired=true for the same current
scan/revision. Only a checked completed terminal packet followed by successful
observed child exit/reap creates this session-local observation. Items, completed
SQLite rows, a missing child after reopen and elapsed time cannot create it.
An explicit status read can recover a lost observation; delivery of that read
is not acknowledgement. One event pump consumes the result. Local video intake
continues independently while online intake is pending or unavailable.

Cancelling after the committed final page stops only its matching enumerator.
The private source_enumeration_stopped event closes the intake operation while
retaining completed scan rows/items/admissions and prior media. It does not
rewrite Completed as Cancelled. Native/controller must ship together in a new
admitted immutable release: the new controller fails closed with an old native
that omits the additive field; an old controller does not gain this gate. No
public source/worker schema, SQLite migration or old producer/job repin occurs.

#### Graceful output-writer retirement owns an explicit unlock scope

A successful output-writer lock acquisition immediately constructs a private
non-clone scope guard, before subsequent fallible identity or store-construction
checks. The guard remains held throughout the operation and explicitly unlocks
on graceful success/error. This releases the operation even if a transient
inherited descriptor still shares its Unix open-file description. Closing an
old duplicate later cannot unlock a successor opened independently. No lockfile
unlink/reopen, production handle clone, new wait, quota or deadline is added.
The path/empty-file format and abrupt-death kernel behavior are unchanged;
rollback uses the same persisted records and needs no migration.

Actual candidate Ubuntu source-retirement cases and retained-descriptor writer
regressions pass; final integrated source still needs its own required CI and
Windows/native/GUI evidence. The separate execution-guard graceful-release
finding remains open until its own changed-source proof. Full172/174/175 gates,
old caller failures, public chunked_longform=false, AUD-0 and TrucLy25voices stay.

The separately assigned execution-guard repair now applies the same explicit
scope-exit unlock to JobExecutionGuard after successful acquisition. Acquire,
canonical naming and prior alias/competing-handle/hard-kill behavior are unchanged.
Its real-duplicate regression was RED before repair and candidate Ubuntu CI is
GREEN, including actual spawned-child hard-kill/reacquisition. These candidate
results do not replace the final integrated-source or Windows product gates.
