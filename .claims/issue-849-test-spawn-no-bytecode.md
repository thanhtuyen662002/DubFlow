DUBFLOW_PR_V1
Issue: #849
Lease-Owner: Claude/issue-849-test-spawn-no-bytecode
Lease-Heartbeat: 2026-11-15T09:58:09Z
Tested-Base-SHA: 3e5c3b5ddd690e9906389c081e9c70487a97d02b
Depends-On: none
Conflict-Domains: test-harness
Expected-Paths: .claims/issue-849-test-spawn-no-bytecode.md,tests/production/tts/test_native_process.py,tests/production/worker/test_production_job.py,tests/production/worker/test_export_publication.py
Required-CI: PR Fast + PR Integration

## Scope
Owner sprint directive 2026-11-15 09:41Z: #849 as its own PR after #850 (#851 merged).
Test harness only: every child interpreter started by these tests gets `-B`. No production code change.
`test_export_publication.py` was added after measuring it (+48 .pyc), beyond the two files named in the issue.
