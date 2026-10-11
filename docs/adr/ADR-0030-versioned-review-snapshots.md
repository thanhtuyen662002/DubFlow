# ADR-0030 — Versioned review snapshots and atomic correction history

Status: Proposed by #174 / PR #185. Production integration and installed
qualification remain required before acceptance.

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

Tests validate actual emitted JSON against the declared schemas, v1 read-only
compatibility, unknown-version rejection, sparse/reordered warning cue binding,
unknown confidence, two corrections/two undos, stale-client refusal, replacement
failure and concurrent-writer admission. Full #174 still requires installed
desktop, actual B1/B2 regeneration and four-hour/real-media evidence that
unrelated ASR/OCR artifacts are reused.
