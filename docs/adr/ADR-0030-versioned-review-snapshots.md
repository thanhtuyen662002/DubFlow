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
An original pinned release must explicitly inventory/admit the worker; no
historical worker/model/runtime pin is upgraded by this addition. Actual native
output quota/volume/locator and immutable retention qualification, final graph CAS/undo and
editable export remain required. This draft worker checkpoints only completed
renders: chunk scheduling/checkpoints and bounded long-form recovery are still
required before activation, as are B1/B2 installed and actual four-hour media
tests. Small physical fixtures qualify only the component. No durable schema,
core2/optionalReview1 or existing job format changes. Rollback ignores private
unreferenced candidates and keeps the original export/active graph usable.
