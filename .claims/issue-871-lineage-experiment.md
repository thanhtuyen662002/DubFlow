DUBFLOW_PR_V1
Issue: #871
Lease-Owner: Claude/issue-871-lineage-experiment
Lease-Heartbeat: 2026-11-15T17:30:14Z
Tested-Base-SHA: 3ce2ce38e86c833ed0aee914626ff9a7b519aecf
Conflict-Domains: mastering-planner
Expected-Paths: .claims/issue-871-lineage-experiment.md,engine/dubflow/mastering/align.py,engine/dubflow/mastering/lineage.py,engine/dubflow/mastering/planners/compilation.py,tests/mastering/
Required-CI: PR Fast + PR Integration (mastering selected)

## Scope
Cue lineage as a planner experiment that is off by default (`lineage=None` on every production path). Decision 2026-11-15 17:19Z (Codex, on the Owner's delegation). No change to READY, confidence, review flags, Final QC, Gate B/D or #844. The default is never switched on.
