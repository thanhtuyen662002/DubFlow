DUBFLOW_PR_V1
Issue: #893
Lease-Owner: Claude/issue-893-source-contradiction
Lease-Heartbeat: 2026-11-16T17:49:31Z
Tested-Base-SHA: 0b51ca57c5501656521d53cbeb758b22bc624b57
Conflict-Domains: claims-source-speech
Expected-Paths:
- .claims/issue-893-source-contradiction.md
- engine/dubflow/mastering/claims.py
- engine/dubflow/mastering/align.py
- engine/dubflow/mastering/review.py
- engine/dubflow/mastering/planners/compilation.py
- engine/dubflow/mastering/planners/product.py
- contracts/mastering/editorial-v1.json
- tests/mastering/test_heard_conflicts.py
- tests/mastering/longform/test_targeted.py
Required-CI: PR Fast + PR Integration (mastering, compilation-video, product)
