DUBFLOW_PR_V1
Issue: #873
Lease-Owner: Claude/issue-873-number-parse
Lease-Heartbeat: 2026-11-16T05:07:43Z
Tested-Base-SHA: 98738be8c962ab83b9721c13c2958984a09efc2a
Conflict-Domains: mastering-research-x2
Expected-Paths: .claims/issue-873-number-parse.md,tests/mastering/x2/hard_negatives.py,tests/mastering/x2/solo_qa.py,tests/mastering/x2/test_number_parse.py
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Research tool only: the number check in tests/mastering/x2 reads value, unit and model id apart, and reports number_conflict, number_unknown and substitution separately. Decision 2026-11-16 (Codex, on the Owner's delegation). No engine or planner change; lineage, D1 and number_conflict stay off and outside the planner. No change to READY, Gate B/D or #844. Budget $0.
