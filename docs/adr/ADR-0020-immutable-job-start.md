# ADR-0020 — Independent job IDs and immutable supervisor start requests

Status: Proposed by #166 / PR #195; native migration/recovery qualification pending.

## Context and decision

The desktop used row-derived `job-1` counters. Removing all rows and reopening
could reuse a still-durable SQLite ID for a different source/voice. The supervisor
returned an already completed job without checking its original start options.

New desktop IDs contain 128 bits from Web Crypto. Existing queue IDs remain
unchanged under snapshot schema 1. Allocation collisions are bounded failures;
there is no timestamp/counter fallback. An explicit terminal/blocked retry adds
a new queued executable ID with the saved voice, preserving the original row,
options, result path and durable job history.

The supervisor admits a bounded immutable JSON start request with the job in one
SQLite transaction. It records source content SHA-256/size, accepted SRT/VTT
sidecar content, source/output paths, languages, dubbing enablement, explicit
voice, subtitle mode, runtime/model roots, worker/runtime binary hashes, installed
release manifest and model-manifest inventory. Replays must match before recovery
or status-file changes, a completed shortcut, or starting another worker. A
conflict is `JOB_ID_CONFLICT`; it has no authority to fail/rebind the original.
Source hashing uses bounded reads and rejects a file changing during hashing.
This admission guard does not replace acquisition snapshots, runtime integrity
verification or the model manager's content/license checks.

## Migration and compatibility

Append-only migration `0002_job_start_requests.sql` adds a request table keyed by
job ID. Migration 1 is unchanged; each migration commits its version in the same
transaction as its tables. Admission never upserts/rewrites a request. A restart
after admission but before stage creation recreates only the missing stage of a
verified queued job. Workers never write durable SQLite state.

Migration 2 leaves historical jobs unbound because their output/voice/producer
options cannot be proved from the source URI alone. Their status/artifacts remain
accessible. Starting such an ID returns `JOB_START_UNVERIFIED` and requires an
explicit new executable ID; it does not relabel or discard existing exports.
This is a compatibility limit for old unfinished jobs and must be exposed in
release notes/intake recovery before stable promotion. New bound jobs recover
with their original options. A different installed producer requires its original
retained runtime or an explicit new job; the supervisor cannot silently retarget
it. Complete old-runtime resolution remains part of the operations gate.

An old supervisor rejects schema version 2 rather than ignoring request pins.
Before an upgrade crosses this schema, the updater must snapshot the database
and retain the matching application/runtime. Rollback restores that coherent
snapshot only at a safe checkpoint; it must not run a schema-1 supervisor against
a schema-2 database. No destructive reverse migration or automatic binding of
historical data is allowed. This Draft does not qualify updater compatibility or
authorize a stable release before the upgrade/rollback integration gate.

## Evidence

Desktop regressions cover deletion/restart, legacy IDs, independent retries and
bounded collision failure. Real SQLite tests cover migration preservation,
atomic immutable admission and restart persistence. Supervisor tests reject
changed voice, source bytes, subtitle, output/language/mode and model inventory.
Windows qualification runs explicit Trúc Ly through the packaged worker and
attempts source/output/voice conflicts against a completed native job, requiring
the original status/video to remain unchanged. Hosted exact-HEAD results and
installed/hard-kill evidence are still required; parser/fixture tests alone do
not prove native supervisor or full #175 production readiness.
