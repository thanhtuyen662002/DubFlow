# AGENTS.md — DubFlow Engineering Constitution

GitHub is the durable source of truth. Chat state, local worktrees and an individual agent's memory are disposable.

## Mandatory reading before any work
1. README.md
2. docs/PRODUCT_SYSTEM_DESIGN.md
3. docs/RISK_REGISTER.md
4. docs/ENGINEERING_EXECUTION_PROTOCOL.md
5. docs/PROJECT_STATE.yaml
6. relevant contracts/ and ADRs when they exist

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
- verify no valid live Draft PR already owns the Issue.

Claim by creating branch `agent/issue-<n>-<slug>` and immediately opening a Draft PR containing `DUBFLOW_PR_V1`, `Issue: #n`, `Lease-Owner`, `Lease-Heartbeat`, `Conflict-Domains`, and `Expected-Paths`.

Earliest valid Draft PR claim wins. A later racing claim must stop before material changes.

## Progress requirement
A work session may not be status-only. It must produce at least one concrete outcome: code/test/docs change, CI repair, resolved review thread, merge/release action, or explicit blocked evidence with the exact external dependency.

## Stalled work
Ownership is a renewable lease, not a permanent assignment. A stale lease may be reclaimed under docs/ENGINEERING_EXECUTION_PROTOCOL.md. Never delete another worker's branch; resume or supersede with traceable history.

## Pull requests
- One executable Issue should normally map to one focused PR.
- Draft while incomplete.
- Never merge based on stale CI. Required checks must be green for exact HEAD.
- Resolve review threads before ready/merge.
- Shared contracts, migrations, lockfiles and workspace manifests are conflict domains and require extra coordination.
- Do not mix unrelated dependency upgrades/refactors with feature scope.
- Generated model weights and large media do not belong in normal Git history.

## Testing
Fast deterministic tests protect PRs. GPU/live-site/long soak tests are separate lanes and must not freeze unrelated development.

## Architecture changes
Any change to an invariant, durable schema, worker protocol, canonical timeline, updater compatibility or artifact format requires an ADR and migration/compatibility plan.

## Safety of repository progress
No single agent, chat, Issue, PR, CI runner, live website, or benchmark is allowed to be the only route for the whole project to progress.
