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

## Required evidence

Tests validate actual emitted JSON against the declared schemas, v1 read-only
compatibility, unknown-version rejection, sparse/reordered warning cue binding,
unknown confidence, two corrections/two undos, stale-client refusal, replacement
failure and concurrent-writer admission. Full #174 still requires installed
desktop, actual B1/B2 regeneration and four-hour/real-media evidence that
unrelated ASR/OCR artifacts are reused.
