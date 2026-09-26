# DubFlow Foundation Roadmap

## Current phase
Foundation is ready to be claimed through leaf Issues. Bootstrap PR #1 is merged and main CI is green. Main is still technically unprotected; Issue #11 remains an S5 administrative risk until a repository ruleset is enabled.

## Foundation Epic
- #2 — Foundation: resumable one-click vertical slice. Tracking only; do not claim directly.

## Independent work that may start immediately
- #3 — Canonical media timeline contract.
- #4 — Supervisor ↔ Python worker protocol.
- #6 — Desktop shell and queue UX against mocks.
- #7 — Source adapter contract and deterministic downloader fixtures.
- #8 — Media fixture and golden benchmark harness.
- #9 — Model/runtime/license manifest and one-click eligibility rules.
- #10 — Extend CI into stable fast and selective integration lanes.

These tasks intentionally occupy mostly separate paths/conflict domains. A stalled worker in one lane must not stop the others.

## Durable job state
- #5 — Durable job state and crash-resume skeleton.

#5 has a **soft integration dependency** on #4. Work may proceed against the documented fake/mock protocol; final integration must use the accepted versioned protocol. This prevents #4 from becoming a project-wide stop-the-world dependency.

## Hard critical path
Only the first real local-file passthrough vertical slice should require the accepted outputs of:
1. #3 canonical timeline,
2. #4 worker protocol,
3. #5 durable job/resume.

Everything else should avoid inheriting those hard dependencies unless technically necessary.

## Administrative control
- #11 — Protect main with PR + required exact-head checks.

This does not block product coding by policy, but it remains the highest GitHub integrity risk because current API state reports `protected=false` and no rulesets.

## Anti-deadlock review
Current hard dependency graph is acyclic.

```text
#3 ─┐
#4 ─┼──> first local-file vertical slice
#5 ─┘

#6 independent
#7 independent
#8 independent
#9 independent
#10 independent
#11 administrative
```

#5 can prototype against the fake protocol and therefore does not need to sit idle waiting for #4.

## Rules for future decomposition
- Epics are never implementation leases.
- Add hard dependency edges only when mocks/interfaces cannot preserve progress.
- Every critical-path Issue must include a recovery/decomposition note.
- Shared conflict domains are serialized; unrelated paths remain parallel.
- If one Issue becomes stale, reclaim/split only that leaf.
- Never make live Douyin/Bilibili availability, GPU runners, CapCut, or release signing a universal PR dependency.
