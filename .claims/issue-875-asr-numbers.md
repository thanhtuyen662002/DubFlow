DUBFLOW_PR_V1
Issue: #875
Lease-Owner: Claude/issue-875-asr-numbers
Lease-Heartbeat: 2026-11-16T06:39:43Z
Tested-Base-SHA: 425373bada2e76d17e29506864cb2e1d4a9b34ac
Conflict-Domains: mastering-research-x2
Expected-Paths: .claims/issue-875-asr-numbers.md,tests/mastering/x2/hard_negatives.py,tests/mastering/x2/test_number_parse.py
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Research tool only: the X2 number check takes an optional source-language hint, reads Vietnamese spoken decimals ("X phẩy Y") and reports garbled units as unknown. Owner request 2026-11-16 06:37Z. No engine or planner change; lineage, D1 and number_conflict stay off and outside the planner. No change to READY, Gate B/D or #844. Budget $0.
