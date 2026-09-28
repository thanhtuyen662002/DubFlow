# ADR-0002 — Versioned canonical timeline contract

Status: Accepted

## Context

ADR-0001 establishes integer ticks, explicit rational time bases, reversible
proxy mapping and rotation-aware media geometry. Issue #3 makes those decisions
executable across Rust, Python and TypeScript. Without a wire version and
distinct timestamp/offset/duration types, a downstream stage can silently
reinterpret a VFR PTS, round a wide integer through JavaScript, or use a frame
index as durable identity.

## Decision

1. Timeline v1 uses an explicit `schema_version: 1` and `kind` discriminator.
   Unsupported versions are structured errors; shape-based guessing is not
   allowed.
2. Absolute PTS (`TimePoint`), signed relative offsets (`TimeOffset`),
   non-negative durations (`Duration`) and half-open intervals (`Interval`) are
   separate contract types. Each carries a reduced positive rational time base.
3. JSON ticks, rational factors and mapping anchors are canonical decimal
   strings. The reference implementation uses checked integer arithmetic and
   explicit rounding modes; overflow or non-integral exact conversion fails.
4. Proxy/source conversion is an ordered piecewise map of explicit half-open
   rational anchor segments. Gaps and source discontinuities remain visible.
5. Geometry uses coded pixel-bound coordinates, clockwise quarter-turns,
   dimension swapping and preserved pixel-aspect metadata. The transform is
   reversible and does not infer geometry from FPS or display floats.

The standard-library Rust crate in `crates/media-contracts` is the executable
reference. The JSON schema and deterministic fixtures are the cross-language
compatibility surface; other producers must match them and retain the source
time-base metadata needed by media adapters.

## Compatibility and migration

This is an additive contract introduction. Existing probe records migrate by
copying the source PTS and time base into `TimePoint` and recording an explicit
rounding policy for any derived representation. A migration must fail closed if
the old record has a zero/invalid time base or cannot be represented in signed
64-bit ticks. Consumers may preserve an unknown future-version record as an
opaque artifact, but may not reinterpret it as v1. Changing the meaning of a
field or the pixel-coordinate convention requires a new schema version and a
new ADR.

## Consequences

- VFR and non-zero PTS survive every cross-language boundary without float
  identity loss.
- Mapping gaps and discontinuities become testable data instead of hidden
  drift.
- The reference crate has no runtime dependency, while media adapters remain
  responsible for FFmpeg-specific raw metadata.
- A future implementation must add a compatibility adapter rather than
  changing v1 semantics in place.
