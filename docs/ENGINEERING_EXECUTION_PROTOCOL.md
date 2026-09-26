# Engineering Execution Protocol

## Goal
Keep development moving even when an agent, PR, CI run, runner, external website or architecture experiment stalls.

## State model
Leaf Issue states:
- READY
- CLAIMED
- IN_PROGRESS
- WAITING_CI
- REVIEW
- BLOCKED_EXTERNAL
- BLOCKED_DEPENDENCY
- STALE_RECLAIMABLE
- DONE

Epics are tracking containers and are never directly claimed.

## Draft PR lease
A claim is valid only when an open Draft PR contains:
```
DUBFLOW_PR_V1
Issue: #123
Lease-Owner: <agent-id>
Lease-Heartbeat: <ISO-8601 UTC>
Conflict-Domains: contracts,timeline
Expected-Paths: contracts/**,crates/media-contracts/**
```

The branch alone is not a lease. Issue assignment alone is not a lease.

Default stale rules:
- IN_PROGRESS without checkpoint/heartbeat for 90 minutes: inspect before reclaim.
- WAITING_CI: not stale while exact-head required CI is legitimately queued/running and within workflow timeout.
- CI beyond its configured timeout or with no active run: reclaimable/repairable.
- BLOCKED_EXTERNAL with concrete external blocker remains blocked, but unrelated Issues continue.
- Any lease with closed/missing PR is invalid.

A reclaiming worker records the previous PR/HEAD and either resumes the branch or opens a superseding PR with explicit lineage.

## Conflict domains
These are serialized more aggressively than ordinary paths:
- canonical timeline/time-base contracts;
- worker/event schemas;
- SQLite schema/migrations;
- app/engine/model compatibility manifests;
- workspace manifests and lockfiles when dependency versions differ;
- updater/release signing pipeline;
- shared artifact schema.

Two PRs may run concurrently only when their material changes do not overlap or rely on incompatible versions of a conflict domain.

## Dependency rules
- Dependencies are directed and must be acyclic.
- Use the smallest dependency edge necessary.
- Prefer interface/schema/mock-first tasks to let downstream work proceed.
- A blocked task does not block its siblings.
- Critical-path tasks need fallback owners and smaller slices.
- A dependency becoming stale triggers decomposition/reclaim, not indefinite waiting.

## CI lanes

### Lane A — PR Fast (required)
Target: deterministic and normally under 15 minutes.
- repository/governance validation;
- formatting/lint;
- type checks;
- Rust/Python/TypeScript unit tests for changed components;
- contract/schema compatibility;
- small media fixtures;
- build/compile smoke.

Every job has `timeout-minutes`. Matrix uses fail-fast=false so one failure does not hide evidence from other components.

### Lane B — PR Integration (required only when affected)
Target: under 30–45 minutes.
- supervisor↔worker protocol;
- SQLite migrations/recovery;
- FFmpeg fixture pipeline;
- installer/package smoke without release signing;
- cross-language contract tests.

Path/contract changes decide when this lane is required.

### Lane C — AI/GPU Benchmark
Not a universal merge gate.
- OCR/ASR/speaker/TTS/inpaint benchmarks;
- GPU memory/performance;
- golden-set quality.

Required for PRs changing a model/default/quality-critical algorithm, otherwise scheduled/on-demand.

### Lane D — Live Source Smoke
Scheduled/on-demand only.
- Douyin/Bilibili extractor health;
- session/auth smoke.
Third-party outage must not block unrelated merges.

### Lane E — Soak/Chaos/Release
Scheduled or release-gate.
- long video;
- 100+ queue;
- crash/reboot;
- disk pressure;
- updater rollback;
- packaging/signing.

## CI anti-stall rules
- Same PR + workflow uses a concurrency group; newer HEAD cancels superseded runs.
- Never accept green status from an older SHA.
- Every job has a timeout.
- Self-hosted GPU jobs use dedicated labels/concurrency and do not occupy normal CPU runners.
- External network/model downloads should be cached or mocked in required CI.
- Failed test and infrastructure failure are classified separately.
- Re-run flaky/infra failures selectively; deterministic code failures require a code change.
- Do not push unrelated commits while waiting for expensive exact-head CI because this invalidates evidence.

## Merge protocol
1. PR scope matches Issue.
2. Dependencies merged or explicitly compatible.
3. Exact HEAD read.
4. Required exact-head checks green.
5. Review threads resolved.
6. Branch updated against current main when shared conflict domains changed.
7. Merge.
8. Post-merge main CI is observed.
9. If main breaks, revert/hotfix immediately; do not stack new feature merges on unknown-red main.

## What happens when one coder/agent stops?
The project must continue.

- Other independent workstreams continue immediately.
- Its Draft PR preserves the lease and checkpoint for a bounded time.
- If heartbeat expires, another agent can recover from GitHub state.
- If CI is running normally, do not duplicate work.
- If CI hangs, timeout/watchdog classifies and reruns/cancels as appropriate.
- If the stalled task is a dependency, first try to split an interface/mock task so downstream work proceeds; otherwise reclaim only that leaf.
- Never freeze the whole board because one Issue is blocked.

## Watchdog duties
A watchdog/lead run reconstructs state from GitHub:
- open leaf Issues;
- Draft PR leases;
- PR head SHAs;
- exact-head CI;
- unresolved reviews;
- dependency graph;
- conflict-domain overlaps;
- stale heartbeats;
- red main.

The watchdog owns no implementation by default and keeps no unique hidden state.

## Issue contract
Every executable Issue must state:
- Outcome.
- Why it matters.
- In scope / out of scope.
- Dependencies.
- Expected paths.
- Conflict domains.
- Acceptance tests.
- Required CI lane.
- Evidence required.
- Recovery/decomposition note if critical-path.

This prevents an Issue from being "mostly done" while nobody can prove it is mergeable.


## Claim-race hardening

Creating a Draft PR is only the first half of a claim.

Immediately after opening the Draft PR, the worker MUST re-read all open PRs and verify:
- no earlier valid Draft PR already claims the same Issue;
- no incompatible live PR owns the same conflict domain/material paths;
- its own PR is still open and points at the expected branch/head.

If two agents race:
- earliest valid claim wins;
- loser stops before additional material edits;
- loser may preserve its branch for forensic/reuse purposes but must not keep advancing it as the active claim.

A claim is not durable until the Draft PR exists on GitHub. Local branches, unpushed commits, terminal notes and chat messages are disposable.

## Durable progress checkpoints

A heartbeat alone is not proof of useful progress.

A valid lease heartbeat must be accompanied by at least one durable GitHub-visible checkpoint when work materially advances:
- pushed commit;
- updated PR body/recovery checkpoint;
- review resolution;
- CI repair evidence;
- explicit blocker comment with reproducible details.

An agent that repeatedly refreshes heartbeat without durable progress is considered no-progress and may become reclaimable.

Server-observed GitHub timestamps are authoritative for lease age. Agent-local clocks are advisory only.

## CI source-head versus merge-ref evidence

GitHub `pull_request` workflows commonly check out a synthetic merge ref. Therefore “exact-head CI” must distinguish two different questions:

1. **Source-head evidence:** did the PR source SHA itself receive the expected checks?
2. **Merge-compatibility evidence:** did GitHub test the source combined with current base/main?

Rules:
- record the PR source `head_sha` when deciding lease ownership and whether new commits invalidated old evidence;
- record the workflow run's tested SHA/ref as separate evidence;
- when a required workflow uses the pull-request merge ref, success proves merge compatibility for the base snapshot used by that run, not that the synthetic merge SHA equals the source SHA;
- if main advances after a high-risk shared-contract run, re-run/update before merge when policy requires current-base compatibility;
- never treat a green run whose PR source head no longer matches the current PR head as valid.

CI tooling/watchdog must preserve both `pr_head_sha` and `tested_sha`.

## CI queue-age policy

`timeout-minutes` only limits a job after it starts running; it does not bound time spent queued waiting for a runner.

The watchdog therefore applies a separate queue-age policy:
- hosted PR-fast queued unusually beyond the repository threshold is classified as runner/platform blockage;
- self-hosted/GPU queues use a longer explicit threshold based on expected capacity;
- queued age must not make an implementation lease stale if the worker has valid exact-head evidence that required CI is waiting for infrastructure;
- a queue blockage cannot freeze unrelated lanes;
- repeated queue saturation becomes an infrastructure Issue rather than repeated code reruns.

Do not cancel/requeue endlessly because that sends a queued job to the back of the queue.

## External dependency and CI truthfulness

A workflow is deterministic only when its required path does not depend on:
- live Douyin/Bilibili availability;
- a user browser session;
- mutable remote model files without pinned hashes;
- scarce GPU runners shared with unrelated work;
- release signing services.

Those belong to isolated lanes. A green PR-fast run means only its declared deterministic contract passed; it must never be presented as proof that live sources/models/CapCut currently work.

## Stale lease decision table

```text
Draft PR + recent durable checkpoint + CI running within timeout
  -> ACTIVE / WAIT

Draft PR + valid head + CI queued within queue-age threshold
  -> WAITING_INFRA

Draft PR + heartbeat but no durable checkpoint beyond threshold
  -> NO_PROGRESS / INSPECT

Draft PR + failed deterministic CI + owner active
  -> OWNER_FIX

Draft PR + failed deterministic CI + stale owner
  -> RECLAIMABLE

Draft PR + no current-head CI and no progress
  -> RECLAIMABLE / REPAIR

Closed/missing PR
  -> LEASE_INVALID
```



## Machine-readable Issue dependency contract

Executable leaf Issues use this block near the top of the Issue body:

```text
DUBFLOW_TASK_V1
Hard-Dependencies: #3,#4
Soft-Dependencies: #6
Conflict-Domains: timeline,worker-protocol
Expected-Paths: contracts/timeline/**,tests/timeline/**
Required-CI: PR Fast + Integration
```

Rules:
- `Hard-Dependencies` are the only dependency edges that may block claim/merge on dependency completion.
- `Soft-Dependencies` are compatibility/reconciliation relationships. They must not make a worker idle when a mock/versioned interface can preserve progress.
- A dependency may not appear in both hard and soft lists.
- A leaf may not hard-depend on an Epic.
- The hard graph must remain acyclic.
- `none` is explicit and preferred over an empty dependency field.
- References elsewhere in prose are explanatory only; watchdogs/agents must not infer hard edges from arbitrary `#123` mentions.
- A hard dependency closed as completed satisfies the edge.
- A hard dependency closed as not-planned/superseded does not silently satisfy the edge; the dependent task must be reviewed/repointed.
- GitHub Issues remain the authoritative live work graph. Do not copy the complete Issue graph into PROJECT_STATE.yaml.

Critical-path and user-value Issues should be migrated first. During transition, legacy Issues without `DUBFLOW_TASK_V1` remain human-readable, but automation must report them as legacy/unstructured rather than guessing all prose references are hard dependencies.
