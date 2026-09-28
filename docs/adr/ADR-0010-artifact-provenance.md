# ADR-0010: Versioned artifact provenance and selective invalidation

## Status

Accepted — 2026-09-29

## Decision

Every reusable artifact is a node in an explicit, cycle-free DAG. Its cache
identity includes producer version, input artifact hashes, configuration hash,
model hash, and contract version. Reuse requires a `VALID` node and exact
provenance equality; file existence alone is never sufficient.

Changes invalidate by semantic scope. A voice edit invalidates only matching
TTS nodes and descendants. A translation edit invalidates only matching
translation nodes and descendants. A canonical timeline edit invalidates all
time-dependent descendants. Missing or corrupt nodes invalidate their complete
downstream closure. QC nodes are descendants and therefore cannot remain PASS
after affected content changes.

## Recovery and compatibility

The graph and invalidation facade are pure policy and do not write SQLite or
depend on AI engines. Durable supervisor state records the resulting statuses.
The v1 schema is additive; future provenance fields require a versioned
compatibility decision and deterministic fixtures.
