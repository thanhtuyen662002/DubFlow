# Canonical Timeline Contract

This directory owns the versioned cross-language timeline wire contract for Issue #3.

## Version and identity

The current wire contract is **schema version 1**. A document must carry both
`schema_version: 1` and a `kind` discriminator. Consumers reject an unknown
version; they do not reinterpret a newer shape as an older one.

Durable temporal identity is a signed 64-bit integer tick together with an
explicit positive rational time base (`seconds_per_tick = numerator /
denominator`). Time bases are reduced by their greatest common divisor. Decimal
seconds, frame numbers, nominal FPS and array positions are derived display or
analysis values and never identity. The source PTS is retained exactly,
including a non-zero or negative start PTS. VFR streams are therefore an
ordered set of PTS values, not a fabricated frame cadence.

All potentially wide integers are JSON decimal strings. This includes ticks,
time-base factors, and mapping anchors. A canonical decimal has no `+`, no
leading zero, no exponent and no floating-point syntax. The Rust reference
parser rejects numeric forms for these fields and rejects malformed or
unsupported-version objects.

The standard-library reference implementation is in
`crates/media-contracts/src/lib.rs`; its public types intentionally separate
absolute `TimePoint`, signed `TimeOffset`, non-negative `Duration`, and
half-open `Interval`. Rescaling and comparison use checked integer arithmetic,
GCD reduction and an explicit rounding mode. Overflow and non-integral exact
rescaling are typed errors, never wrapping behavior.

## Proxy/source mapping

`ProxySourceMapping` is an ordered list of non-overlapping, half-open rational
segments. Each segment contains source and proxy start/end anchors, preserving
their separate time bases. Adjacent segments may have gaps and may jump in
source time to represent trims or discontinuities. A point at an end anchor is
handled by the next segment (if any); gaps return `NoMappingSegment`.

Mapping is defined by exact rational interpolation between anchors. Callers
must choose `Exact`, `Floor`, `Ceil`, `TowardZero`, `AwayFromZero` or
`NearestTiesToEven` when a target time base cannot represent the result. No
proxy-seconds float or FPS/frame-index inference is permitted. The affine
parameter is the fraction of the proxy tick span, applied to the source tick
span; converting both spans to seconds produces the same ratio because their
time-base factors cancel. This guarantees that both explicit anchors remain
fixed even when source and proxy time bases differ.

## Geometry

Geometry records coded dimensions, a clockwise quarter-turn rotation (0, 90,
180 or 270 degrees), and pixel aspect ratio metadata. Rotation 90/270 swaps
display dimensions. Coordinates are integer pixel-bound coordinates: `(0, 0)`
is the top-left bound and `(width, height)` is the bottom-right bound. The same
convention is used for OCR polygons and masks; pixel centers are not a second
coordinate system. `to_display` and `from_display` are exact inverses and
reject points outside their dimensions.

Pixel aspect ratio is preserved as metadata and is not applied to the integer
pixel-bound transform. A future display-layout contract may apply it without
changing timeline identity.

## Compatibility and scope

`v1` is additive and self-contained. Existing producer records can be read by
retaining their source time base and converting to the normalized representation
with an explicit rounding policy. A producer must not silently emit a new
schema version. A consumer that cannot support a future version must preserve
the original record for migration rather than guessing its meaning.

FFmpeg probing, decode/encode, UI timelines and CapCut project generation are
outside this contract. CapCut remains an adapter; the stable timeline and
standard video/audio/subtitle exports remain usable without it.
