# ADR-0014: Production editable import pack and controlled CapCut handoff

## Status

Accepted

## Context

The canonical DubFlow output must remain usable when CapCut is absent, changed,
or broken. Earlier adapters could make a portable folder, but the production
worker did not publish that folder as a first-class artifact and the manifest
did not carry the canonical tick mapping needed by downstream editors.

## Decision

Every successful local-file production job publishes a portable import pack
after canonical output and QC are valid. The pack copies the canonical video,
available original/dubbed audio and subtitles, records independent SHA-256
hashes and explicit track order, and includes an integer-tick timeline. The
pack is independently validated before its path is advertised.

Direct CapCut handoff is an optional adapter. It may run only for an explicitly
tested semantic version, a caller-selected controlled directory, and an
app-owned backend. Unknown versions, missing installations, backend errors and
validation failures return the already validated import pack and leave canonical
artifacts untouched. CapCut is never the canonical project format.

## Compatibility and migration

Import-pack schema v1 now requires `timeline` for newly published packs. The
Python validator rejects old packs without it at the production boundary; a
future migration tool may add a zero-cue timeline to archived packs without
rewriting media. Existing direct-draft consumers continue to consume the
validated pack manifest hash.

## Consequences

- Moving an export folder does not require path rewriting.
- A CapCut update cannot delete or invalidate a playable MP4, subtitles, audio,
  or timeline.
- The worker must publish the pack only after all required canonical artifacts
  exist; a pack failure is a scoped editable-output warning and does not turn a
  valid standard export into a failed job.
