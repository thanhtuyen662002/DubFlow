# Parallel Execution and Path Ownership

This document turns the Issue dependency graph into a practical concurrency map. "Dependencies: none" does not mean two agents may safely edit the same broad root at the same time.

## Core rule

Every active implementation PR must own the **narrowest practical namespace**.

Broad roots such as `contracts/**`, `crates/job-supervisor/**`, `tests/**`, `fixtures/**`, `packaging/**`, `.github/workflows/**`, and lockfiles are not blanket ownership grants.

If a task must edit a shared root/index/manifest outside its assigned namespace, it must:
1. re-check live PRs;
2. declare the additional path/conflict domain in the PR lease;
3. serialize that shared edit with incompatible work;
4. keep the shared edit minimal.

## Foundation ownership map

### #3 Canonical media timeline
Primary ownership:
- `contracts/timeline/**`
- `crates/media-contracts/**`
- `tests/timeline/**`
- timeline-specific generated fixtures under `fixtures/timeline/**`

Must not casually edit:
- `contracts/worker/**`
- `contracts/source/**`
- `contracts/artifacts/**`
- `contracts/qc/**`
- supervisor state/scheduler/locking modules.

### #4 Supervisor ↔ Python worker protocol
Primary ownership:
- `contracts/worker/**`
- `crates/job-supervisor/protocol/**`
- `engine/dubflow/worker/**`
- `tests/worker_protocol/**`

Shared-root edits:
- any supervisor root module/index requires coordination with #5/#17/#18/#20.

### #5 Durable job state / crash recovery
Primary ownership:
- `crates/job-supervisor/state/**`
- `crates/artifact-store/state/**`
- `migrations/**`
- `tests/job_state/**`

Must consume #4 through an interface/mock rather than editing protocol ownership directly unless coordinated.

### #6 Desktop shell / queue UX
Primary ownership:
- `apps/desktop/**`
- mock fixtures local to desktop.

Do not edit core Rust/Python contracts to make the mock convenient. Adapt the mock to versioned contracts.

### #7 Source adapter contract / downloader fixtures
Primary ownership:
- `contracts/source/**`
- `engine/dubflow/download/**`
- `fixtures/source/**`
- `tests/source_adapter/**`

No live-source credential logic in required PR-fast workflows.

### #8 Media fixture / benchmark harness
Primary ownership:
- `fixtures/catalog/**`
- `fixtures/generated/**`
- `models/benchmark/catalog/**`
- `models/benchmark/metrics/**`
- `tests/benchmark_harness/**`

Timeline/source/QC fixtures can be referenced, but ownership of their contract schemas remains with their task.

### #9 Model/runtime/license manifest
Primary ownership:
- `models/manifests/**`
- `packaging/manifests/**`
- `docs/licenses/**`

Do not own installer/updater implementation wholesale.

### #10 CI lanes
Primary ownership:
- `.github/workflows/pr-fast.yml`
- `.github/workflows/pr-integration.yml`
- `.github/workflows/benchmark.yml`
- `.github/workflows/live-source.yml`
- `.github/workflows/soak-release.yml`
- `scripts/ci/**`

Workflow root/config shared files require coordination with #15.

### #15 Lease / stuck-CI watchdog
Primary ownership:
- `.github/workflows/watchdog.yml`
- `scripts/watchdog/**`
- watchdog fixtures/tests.

It must not mutate or rename required CI checks owned by #10 without coordination.

### #17 Artifact DAG / selective invalidation
Primary ownership:
- `contracts/artifacts/**`
- `crates/artifact-store/provenance/**`
- `crates/job-supervisor/invalidation/**`
- `tests/artifact_graph/**`

Consumes #3/#5 contracts through versioned interfaces.

### #18 Project ownership / multi-instance recovery
Primary ownership:
- `crates/job-supervisor/locking/**`
- `crates/artifact-store/locking/**`
- `tests/project_lock/**`
- desktop startup/IPC changes only when explicitly declared.

### #19 Input/path/archive/media security
Primary ownership:
- `crates/security/**`
- `engine/dubflow/security/**`
- `packaging/security/**`
- `tests/security/**`

Any change to #7 source or #9 manifest-owned files requires coordination instead of silently expanding scope.

### #20 Fair batch scheduler / disk guard
Primary ownership:
- `crates/job-supervisor/scheduler/**`
- `crates/job-supervisor/resources/**`
- `tests/scheduler/**`

No ownership of state/protocol/locking modules except versioned interfaces.

### #21 Model/runtime retention and GC
Primary ownership:
- `models/retention/**`
- `packaging/model-manager/**`
- `crates/job-supervisor/model_refs/**`
- `tests/model_retention/**`

Consumes #9 manifest schema. Changes to `models/manifests/**` are coordinated with #9.

### #22 QC provenance / confidence calibration
Primary ownership:
- `contracts/qc/**`
- `engine/dubflow/qc/**`
- `models/benchmark/calibration/**`
- `tests/qc/**`

Shared artifact provenance integration coordinates with #17.

### #14 First local-file vertical slice
This is intentionally an integration task after #3/#4/#5.

It may touch:
- integration harness;
- small wiring points;
- `tests/integration/local_file_slice/**`.

It must not fork/duplicate the owned contracts. Contract defects return to the owning Issue/ADR.

## Serialized shared resources

The following are high-contention and are effectively serialized unless changes are purely mechanical and compatible:
- workspace root manifests;
- Rust/npm/Python lockfiles when dependency versions differ;
- database migration sequence;
- `contracts/**` root exports/indexes;
- supervisor root exports/indexes;
- updater/release compatibility schema;
- release-signing workflows;
- required check names;
- repository governance scripts.

## Parallel groups

Safe to run in parallel when leases show only owned namespaces:

```text
Group A contracts:
  #3 timeline
  #4 worker
  #7 source
  #17 artifacts
  #22 qc

Group B product/infra:
  #6 desktop
  #8 fixtures
  #9 manifests
  #10 CI

Group C supervisor modules:
  #5 state
  #18 locking
  #20 scheduler
  #21 model refs
```

Group C is parallel only after the supervisor scaffold exposes stable module boundaries. Until then, root scaffold creation is a short coordination point, not four competing implementations.

## Hidden dependency rule

A path overlap is not automatically a hard product dependency.

Preferred order:
1. split namespace;
2. introduce interface/mock;
3. serialize a tiny shared-root commit;
4. only then add a hard Issue dependency if implementation truly cannot proceed.

## Lockfile rule

If two PRs need different dependency changes:
- isolate dependency addition in the PR that first requires it;
- later PR updates from main and regenerates lockfile;
- do not manually merge lockfile conflict by deleting one side;
- CI must run after the final lockfile state.

## Migration rule

Migrations are append-only once merged/released.

Parallel migration work must:
- allocate sequence/version only after valid claim;
- re-check main before finalizing sequence;
- never edit a migration already released;
- use corrective migration for fixes.

## Review/merge ordering

When two PRs are independently green but share a conflict domain:
1. merge the earlier/contract-owner PR;
2. update the second PR to current main;
3. rerun exact-current-head/merge compatibility;
4. then merge.

Green-before-first-merge is not sufficient evidence for the second PR.

## Escalation

If a worker discovers material overlap not declared at claim time, it must update the lease metadata before editing the overlapping path. If another valid lease already owns it, the worker pauses only that overlapping change and continues non-overlapping work.



## Extended ownership map — acquisition/runtime/CapCut/release

### #53 Bilibili source adapter
Primary ownership:
- `engine/dubflow/download/bilibili/**`
- `tests/source_bilibili/**`

Shared coordination:
- any edit to `contracts/source/**`, downloader root exports/indexes, or common auth/session abstractions coordinates with #7 and other live source adapters.

### #59 Douyin source adapter
Primary ownership:
- `engine/dubflow/download/douyin/**`
- `tests/source_douyin/**`

Shared coordination:
- any edit to `contracts/source/**`, downloader root exports/indexes, browser-session bridge, or common auth/session abstractions coordinates with #7/#53.

Bilibili and Douyin adapters may proceed in parallel only while their changes remain provider-specific.

### #60 Whole-channel / multi-URL acquisition queue
Primary ownership:
- `engine/dubflow/download/enumeration/**`
- `crates/job-supervisor/source_queue/**`
- `tests/source_queue/**`

Consumes provider adapters through SourceAdapter. It does not own provider-specific scraper/extractor code.

Shared coordination:
- supervisor root exports coordinate with #5/#20;
- source contract changes coordinate with #7.

### #61 Windows one-click installer/runtime bootstrap
Primary ownership:
- `packaging/windows/**`
- `packaging/runtime/**`
- `models/bootstrap/**`
- installer-specific tests/fixtures.

Does not own:
- `models/manifests/**` (#9);
- `packaging/model-manager/**` (#21);
- update switching/rollback logic (#62).

### #62 App/engine/model update + rollback
Primary ownership:
- `crates/updater/**`
- `packaging/update/**`
- `tests/update_recovery/**`

Shared coordination:
- compatibility manifest edits coordinate with #9;
- model deletion/refcount/retention behavior coordinates with #21;
- multi-instance/relaunch locking coordinates with #18;
- database migration sequencing coordinates with #5.

### #63 Stable CapCut import pack
Primary ownership:
- `engine/dubflow/capcut/import_pack/**`
- `contracts/capcut/import_pack/**`
- `tests/capcut_import/**`

Consumes #44 canonical editable assets. It does not own canonical export format.

### #64 Direct CapCut draft adapter
Primary ownership:
- `engine/dubflow/capcut/draft/**`
- `contracts/capcut/draft/**`
- `tests/capcut_compat/**`

Consumes #63 fallback and #44 assets. It must not change the stable import-pack contract merely to fit one CapCut version.

### #65 Release chaos / long-form soak qualification
Primary ownership:
- `tests/soak/**`
- `tests/chaos/**`
- release evidence manifests.

Workflow coordination:
- `.github/workflows/soak-release.yml` is jointly coordinated with #10 because #10 owns core CI conventions/check names.
- #65 must not edit PR Fast, universal required checks, or watchdog workflow.
- soak/release failures block release qualification only, never unrelated PR-fast merges.

## New shared serialization points

The following edits require explicit cross-lease coordination:
- `engine/dubflow/download/__init__.*` or downloader root registry/index shared by #7/#53/#59/#60;
- source auth/session abstraction shared by #53/#59;
- packaging root manifests shared by #9/#21/#61/#62;
- app/engine/model compatibility schema shared by #9/#21/#62;
- project/relaunch locks shared by #18/#62;
- `contracts/capcut/**` root exports shared by #63/#64;
- release workflow/check names shared by #10/#65.

These are short coordination points, not reasons to serialize entire feature lanes.
