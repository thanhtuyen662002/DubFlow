DUBFLOW_PR_V1
Issue: #320
Lease-Owner: Claude/issue-317-shutdown-retry-budget
Lease-Heartbeat: 2026-10-27T15:35:40Z
Tested-Base-SHA: f35a28bc80688d152932734f17121becee11c188
Conflict-Domains: b2-audio-work-layout,separation-publication,editable-audio-export
Expected-Paths: .claims/issue-320-audio-track-storage.md,engine/dubflow/worker/b2_audio.py,engine/dubflow/worker/production_job.py,engine/dubflow/separation/**,engine/dubflow/mix/streaming.py,tests/integration/production_local_file/**

## Lead-prepared claim shell
Coordination metadata only. Implementation has not started; Coder 2 must take over this existing Draft PR before any material edits. The lead did not run product tests or alter product source.

## Acceptance summary
Reduce duplicate full-length B2 audio storage and disk-exhaustion risk while preserving hashes, editable outputs, QC, and resume checkpoints. Keep all deletion strictly inside the job-owned work folder; preserve user media and editable outputs. Required CI: PR Fast + Integration.
