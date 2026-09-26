# ADR-0001 — Local-first boundaries and canonical time

Status: Accepted for bootstrap

## Decision 1 — Local-first
A standard DubFlow job must be able to complete without a required cloud API. Optional cloud translation/TTS providers may exist behind adapters but are opt-in and must not become hidden dependencies.

Downloading public model/runtime packs is compatible with local-first; media processing after installation remains local unless the user explicitly enables a remote provider.

## Decision 2 — Canonical timeline
Durable timestamps are represented as integer ticks with explicit rational/time-base mapping. Decimal seconds are derived presentation values. Frame numbers are never durable timeline identity for VFR media.

Every proxy or normalized stream records reversible mapping back to source time. Cross-stage schemas exchange canonical time, not UI floats.

## Consequences
- long-form A/V drift from repeated float conversions is reduced;
- VFR/rotation/proxy transformations are explicit;
- contracts and fixtures must test non-zero PTS and irregular time bases;
- APIs may still expose seconds for display but not as primary keys.
