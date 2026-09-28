# Slice B1 Vietnamese subtitle integration

Issue #56 owns this integration namespace. The test harness exercises the
first useful offline localization path through the public ASR, translation,
subtitle, and export adapters:

`local fixture -> ASR -> local Vietnamese translation -> SRT/ASS -> validated MP4 + editable pack`

The fixture backends are deterministic and CPU-only. The harness records
checkpoint reuse across a simulated hard-kill/restart boundary, preserves
original audio metadata, reports source burned-in text as a capability
warning, and runs independent jobs so one failure cannot abort a successful
job. It does not add a second media contract or require CapCut, a live site,
GPU, system FFmpeg, or a manual Python/terminal step in the product flow.
