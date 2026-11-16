DUBFLOW_PR_V1
Issue: #879
Lease-Owner: Claude/issue-879-d1-same-shot
Lease-Heartbeat: 2026-11-16T09:10:00Z
Tested-Base-SHA: fab4b759fa99fa7d63dae3e73cf6dd0e14689f33
Conflict-Domains: mastering-research-x2
Expected-Paths: .claims/issue-879-d1-same-shot.md,tests/mastering/x2/d1_same_shot.py,tests/mastering/x2/test_d1_same_shot.py
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Owner request 2026-11-16 08:40Z: research-only D1 variant on a synthetic fixture, $0, explicit call only, default off.
Re-anchors the in-point inside the shot the region planner already chose; never changes source, shot id, piece count, order or length.
No engine, planner, READY, Final QC, Gate B/D or #844 change.
