DUBFLOW_PR_V1
Issue: #320
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-29T05:53:42Z
Tested-Base-SHA: 41c823c5ad3013ae46bfd7ece566ac7a7f90a138
Conflict-Domains: b2-audio-work-layout,separation-publication,editable-audio-export
Expected-Paths: .claims/issue-320-audio-track-storage.md,engine/dubflow/worker/b2_audio.py,engine/dubflow/worker/production_job.py,engine/dubflow/separation/**,engine/dubflow/mix/streaming.py,tests/integration/production_local_file/**,tests/production/worker/test_multi_voice.py

## Status
Implementation in progress by the lease owner after the owner reassigned all open work (stale lease reclaimed, branch history kept).

## Acceptance summary
Reduce duplicate full-length B2 audio storage and disk-exhaustion risk while preserving hashes, editable outputs, QC, and resume checkpoints. Keep all deletion strictly inside the job-owned work folder; preserve user media and editable outputs. Required CI: PR Fast + Integration.
