# ADR-0018: Recoverable publication after output QC

- Status: Proposed, Issue #196 / Draft PR #197.
- Scope: private filesystem publication journal and worker checkpoints.
- Public artifact names, timeline, JSONL and supervisor SQLite: unchanged.

## Decision

A failed route change must preserve the preceding validated export. Generate
replacement MP4, SRT, ASS, QC, editable assets and manifest under the job's
`.dubflow-work/export-pending` directory. Check the media before publication;
the manifest records final public paths and hashes of the validated bytes.
Unchanged B1 rendering can reuse a hash-verified public MP4 without copying it.
Source media cannot overlap the controlled output entries or private work tree.

Publication uses same-filesystem renames and a flushed, atomically replaced
version-1 `publication.json` journal. Write `publishing` before any public
mutation, move preceding entries to `export-previous`, move candidate entries
to their public names, and publish the manifest last. Write `committed` only
after every selected entry is installed. Recovery before continuing a job
rolls back `publishing` and finishes cleanup for `committed`. Recovery itself
can be interrupted and repeated. An unregistered backup, invalid journal or
linked path requires explicit diagnostics; it is never silently removed.

This is recoverable multi-file publication, not an atomic directory snapshot
for concurrent external readers. A reader must consume the completed job
manifest after the supervisor receives completion. The supervisor serializes
writers to a job output; the worker never writes durable SQLite state.

## Compatibility and migration

Existing jobs without a journal retain their public exports. No SQLite or
public contract migration is needed. Missing legacy checkpoint hashes force
recomputation. Render reuse also requires source, translation recipe/input,
subtitle, burn-in setting and selected audio identity. Failed QC invalidates
the rejected render checkpoint. Private audio generations referenced by a
published manifest are immutable; failed unpublished generations can resume
their own TTS chunks. Changed input/profile/producer code selects a new
generation, preserving preceding audio provenance and editable assets.

Do not remove preceding exports before replacement QC. Never use a source
file as publication storage. Retain source and pending recovery evidence on
failure. Fault tests interrupt every rename boundary, exercise I/O rollback
and death after commit, and verify previous exports survive failed QC. These
tests establish deterministic recovery behavior; real media and packaged
Windows qualification remain separate acceptance gates.
