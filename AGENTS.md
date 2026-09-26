# AGENTS.md — DubFlow Engineering Constitution

GitHub is the durable source of truth. Chat state, local worktrees and an individual agent's memory are disposable.

## Mandatory reading before any work
1. README.md
2. docs/PRODUCT_SYSTEM_DESIGN.md
3. docs/RISK_REGISTER.md
4. docs/ENGINEERING_EXECUTION_PROTOCOL.md
5. docs/PROJECT_STATE.yaml
6. docs/PARALLEL_EXECUTION.md
7. relevant contracts/ and ADRs when they exist

## Non-negotiable architecture invariants
- CapCut is an adapter, never the canonical project format.
- OCR alone never defines dialogue semantics.
- Speaker diarization never equals visual-character identity.
- Canonical timeline identity uses source-derived integer ticks/time-base mapping; frame index/floating seconds are not durable identity.
- All expensive stages are chunkable/checkpointable/resumable.
- Workers never write durable SQLite state directly; supervisor owns durable mutations.
- External systems/models/downloaders sit behind adapters.
- No system Python dependency in the shipped product.
- Existing jobs pin producer/model/contract versions.
- One failed media item cannot stop a batch.
- Retry is bounded and must change a condition.
- Low confidence is data, not an exception.
- CapCut/live websites/GPU availability cannot be required to preserve a usable standard export.

## Work claiming
Do not start implementation from an Epic. Claim only a leaf Issue with explicit acceptance criteria.

Before claim:
- inspect all open Issues and PRs;
- inspect dependencies;
- inspect expected changed paths/conflict domains;
- read the ownership map in docs/PARALLEL_EXECUTION.md;
- verify no valid live Draft PR already owns the Issue or an incompatible shared namespace.

Claim by creating branch `agent/issue-<n>-<slug>` and immediately opening a Draft PR containing `DUBFLOW_PR_V1`, `Issue: #n`, `Lease-Owner`, `Lease-Heartbeat`, `Tested-Base-SHA`, `Conflict-Domains`, and `Expected-Paths`. `Tested-Base-SHA` starts as the current `main` SHA the branch was created from and must be refreshed whenever evidence is rerun against a newer base.

Earliest valid Draft PR claim wins. A later racing claim must stop before material changes.

## Progress requirement
A work session may not be status-only. It must produce at least one concrete outcome: code/test/docs change, CI repair, resolved review thread, merge/release action, or explicit blocked evidence with the exact external dependency.

## Stalled work
Ownership is a renewable lease, not a permanent assignment. A stale lease may be reclaimed under docs/ENGINEERING_EXECUTION_PROTOCOL.md. Never delete another worker's branch; resume or supersede with traceable history.

## Pull requests
- One executable Issue should normally map to one focused PR.
- Draft while incomplete.
- Never merge based on stale CI. Required checks must be green for exact HEAD **and** the evidence must correspond to the current base/main SHA. Same HEAD + older tested base is `STALE_BASE`, not merge-ready.
- Every lane declared in the Issue's `Required-CI` must actually exist and emit applicable evidence. A missing required lane is `WAITING_CI_INFRA`, never an implicit pass.
- Resolve review threads before ready/merge.
- Shared contracts, migrations, lockfiles and workspace manifests are conflict domains and require extra coordination.
- A broad root such as `contracts/**`, `crates/job-supervisor/**`, `packaging/**`, `.github/workflows/**`, `fixtures/**`, or `tests/**` is not blanket ownership; use the narrow namespace defined in docs/PARALLEL_EXECUTION.md.
- Do not mix unrelated dependency upgrades/refactors with feature scope.
- Generated model weights and large media do not belong in normal Git history.

## Testing
Fast deterministic tests protect PRs. GPU/live-site/long soak tests are separate lanes and must not freeze unrelated development.

## Architecture changes
Any change to an invariant, durable schema, worker protocol, canonical timeline, updater compatibility or artifact format requires an ADR and migration/compatibility plan.

## Safety of repository progress
No single agent, chat, Issue, PR, CI runner, live website, or benchmark is allowed to be the only route for the whole project to progress.


## Work selection priority

Do not select arbitrary open tasks when autonomous capacity becomes available.

1. If `main` is red from a repository-wide deterministic failure, repair it before claiming new feature work.
2. Read `docs/PROJECT_STATE.yaml` for the nearest active integration/user-value gate.
3. Read that Issue's `DUBFLOW_TASK_V1` metadata and traverse only `Hard-Dependencies`.
4. Prefer unblocked root hard dependencies whose own hard dependencies are satisfied.
5. For each candidate root, read `Required-CI`. If a required lane does not yet exist or cannot emit evidence for that path, mark merge readiness `WAITING_CI_INFRA` and promote the configured `ci_infrastructure_issue` as an enabling root. Coding may continue in Draft; merge may not.
6. Do not treat `Soft-Dependencies`, Epics, prose references, live-site smoke, GPU benchmarks or release-soak work as blockers unless the active gate explicitly makes them hard.
7. Before claiming a root, inspect Draft PR leases. If another valid lease owns it, take a different independent root.
8. When several roots are READY, prefer the one that unlocks more downstream gate work with less conflict-domain contention.
9. Advanced research may proceed in parallel only after available workers cover current gate roots.

The goal is not maximum number of open PRs; the goal is shortest safe path to the next user-visible gate.


## Tested-base merge invariant

Before autonomous merge:
- read current PR `head_sha`;
- read current `main` SHA;
- read `Tested-Base-SHA` from the PR lease/evidence;
- require the PR head to match the green required-check evidence;
- require `Tested-Base-SHA == current main SHA`.

If `main` advanced after the successful CI run, mark the PR `STALE_BASE`. Update/rebase/merge-main into the branch according to repository policy, set the new tested base, and rerun required CI. File-level `mergeable=true` does not waive this rule.
