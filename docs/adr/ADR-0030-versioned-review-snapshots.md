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

## Required evidence

Tests validate actual emitted JSON against the declared schemas, v1 read-only
compatibility, unknown-version rejection, sparse/reordered warning cue binding,
unknown confidence, two corrections/two undos, stale-client refusal, replacement
failure and concurrent-writer admission. Full #174 still requires installed
desktop, actual B1/B2 regeneration and four-hour/real-media evidence that
unrelated ASR/OCR artifacts are reused.
