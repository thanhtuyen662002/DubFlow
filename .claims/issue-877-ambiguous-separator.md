DUBFLOW_PR_V1
Issue: #877
Lease-Owner: Claude/issue-877-ambiguous-separator
Lease-Heartbeat: 2026-11-16T07:00:12Z
Tested-Base-SHA: b44eb8ab0c69ddf077743113da3815d7d55eb11c
Conflict-Domains: mastering-research-x2
Expected-Paths: .claims/issue-877-ambiguous-separator.md,tests/mastering/x2/hard_negatives.py,tests/mastering/x2/solo_qa.py,tests/mastering/x2/test_number_parse.py,tests/mastering/x2/test_solo_qa.py
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Research tool only: without a source language, a number whose vi and en readings lead to different outcomes is number_unknown (review), never number_conflict; solo_qa.select can pass languages through. Owner request 2026-11-16 06:57Z. No engine or planner change; lineage, D1 and number_conflict stay off and outside the planner. Budget $0.
