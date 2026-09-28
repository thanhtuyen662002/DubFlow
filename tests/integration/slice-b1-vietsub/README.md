# Slice B1 Vietnamese subtitle integration

Issue #56 owns this integration namespace. The test harness exercises the
first useful offline localization path through the public ASR, translation,
subtitle, and export adapters:

`local fixture -> ASR -> local Vietnamese translation -> SRT/ASS -> validated MP4 + editable pack`

The fixture backends are deterministic and CPU-only. The caller supplies
normalized dimensions, duration, and audio capability metadata from the
local-file probe boundary established by Issue #14; B1 begins at the adapter
boundary and carries that metadata through
the canonical integer timeline. The harness records checkpoint reuse across
an actual abrupt subprocess exit/restart boundary, preserves the original
audio policy in the export contract, reports source burned-in text as a
capability warning, and runs independent jobs so one failure cannot abort a
successful job. It does not add a second media contract or require CapCut, a
live site, GPU, system FFmpeg, or a manual terminal/Python step in the
product flow.

The checked-in renderer is the deterministic #44 fixture renderer. It emits
the validated MP4/H.264/AAC-compatible metadata and editable-pack evidence
without pretending that a system encoder is available in PR CI; a production
`RenderBackend` supplies playable media behind the same boundary.
