DUBFLOW_PR_V1
Issue: #881
Lease-Owner: Claude/issue-881-d1-clamp-shot-end
Lease-Heartbeat: 2026-11-16T10:15:00Z
Tested-Base-SHA: 8a55f42a2cdd98ca6998cbb728669ed196b16610
Conflict-Domains: mastering-research-x2
Expected-Paths: .claims/issue-881-d1-clamp-shot-end.md,tests/mastering/x2/d1_same_shot.py,tests/mastering/x2/test_d1_same_shot.py,tests/mastering/x2/d1_ds2_timeline.json
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Owner request 2026-11-16 10:02Z: default-off clamp-to-shot-end option in the research runner d1_same_shot.py ($0).
Reproduction tests first on a numbers-only copy of the d-s2 22-piece region timeline. No engine, planner, READY, Final QC, Gate B/D or #844 change.
