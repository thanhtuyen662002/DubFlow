# ADR-0005 — Separate durable control storage from media roots

Status: Accepted

## Context

ADR-0004 establishes supervisor-owned local SQLite state and independently
configured output, cache and source roots. Large media is commonly placed on
removable SSDs, NAS/SMB shares or paths whose Windows drive letter changes.
The state database must remain recoverable when those roots disappear, become
read-only, fill up, or become temporarily unreachable. A resume after sleep or
hibernate also invalidates assumptions made by workers and resource leases.

## Decision

1. The storage topology models `control`, `source`, `output`, `model`, `cache`
   and `temp` as separate roots with independent health policies.
2. The control root defaults to app-owned local storage. Configuration rejects
   network/removable control storage and never falls back to an output or cache
   root automatically.
3. Artifact references use a versioned locator containing a provider-supplied
   stable volume identity, root kind, safe relative path, SHA-256 content hash
   and byte size. Absolute paths are resolution hints only.
4. Output/source loss pauses affected work and quarantines partial files;
   cache loss permits rebuild. Unrelated jobs keep running. A stale path or
   matching filename cannot make an artifact valid.
5. Rebinding a moved folder requires a healthy candidate root, matching volume
   identity and recomputed content hash/size. The old absolute path is not
   part of the durable identity.
6. Sleep/hibernate/resume increments a resource generation and re-probes every
   root before workers continue. Leases created under an older generation are
   rejected and reacquired.

## Compatibility and migration

The first implementation is an additive storage adapter and does not rewrite
the released job-state migration `0001_job_state.sql`. Existing absolute path
columns remain readable as legacy hints; a committed artifact is reusable only
after the current file passes its stored hash/size check. New locator records
use schema version 1. A future SQLite migration may add a locator column only
as an append-only migration, with dual-read of legacy paths and a verified
backfill; it must never delete the old path until the new locator is validated.

The root health and resume-generation APIs are adapters around OS-specific
volume/free-space probes. The deterministic tests inject fake probes, so PR
CI does not require a removable disk, NAS, Windows sleep event or GPU.

## Consequences

Control metadata survives output/cache loss and can explain exactly which jobs
are paused or degraded. Rebinding is explicit and hash-verified, so a stale or
reused path cannot create a false-success artifact. Callers must handle
`space_unknown` and network atomicity as real health outcomes instead of
assuming a local filesystem.
