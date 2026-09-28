# ADR-0011: Exact model/runtime retention for resumable jobs

## Status

Accepted — 2026-09-29

## Decision

Every non-terminal job pins exact model/runtime references by id, version, and
SHA-256. Active worker/mmap leases also protect their exact package. Cleanup
orders unreferenced caches before models/runtimes and emits bounded,
resumable plans; it never evicts a protected reference.

If a required reference is missing, the manager either reacquires that exact
hash or explicitly invalidates the dependent stage. Another version cannot be
silently substituted, even when its manifest is otherwise compatible. The
supervisor owns durable pins; the model manager is an adapter and writes no
job database state directly.

## Compatibility and recovery

The policy consumes the versioned manifest from Issue #9 and uses synthetic
packages in deterministic tests. Low-disk cleanup can stop after a byte/action
budget and resume later without losing the remaining candidates.
