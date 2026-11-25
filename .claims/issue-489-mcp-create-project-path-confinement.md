DUBFLOW_PR_V1
Issue: #489
Lease-Owner: codex/01a11204/issue-489-mcp-create-project-path-confinement-r2
Lease-Heartbeat: 2026-11-25T19:35:11Z
Tested-Base-SHA: f2e67098af3422f1865f3554de8029f2555b1914
Conflict-Domains: documentary-mcp-create,source-media-path-confinement,documentary-windows-lane
Expected-Paths: .claims/issue-489-mcp-create-project-path-confinement.md,engine/dubflow/docu/intake.py,engine/dubflow/mcp/server.py,tests/docu/test_intake.py,.github/workflows/pr-documentary-windows.yml,docs/adr/ADR-0049-documentary.md
Required-CI: PR Fast + PR Integration (documentary selected) + documentary-windows

## Bounded assignment — Coder 1 / Codex (sole active task)
This is Codex's only active DubFlow task under the current Lead/Coordinator priority: close the MCP create_project source-media confinement gap. It supersedes the queued #818 assignment until #489 is integrated or the Coordinator explicitly reorders work. Do not work on #818 concurrently.

## Hard / soft dependencies
- Hard-Dependencies: none.
- Soft-Dependencies: #458, #464, #485, #487 (all must be rechecked live; soft references do not block the task).
- Historical branch `agent/issue-489-mcp-create-project-path-confinement` and PR #820 are preserved as history only. The old branch is 1 ahead / 182 behind Main and contains only a stale claim; it is not a valid lease. This r2 branch is the only current claim.

## Outcome and acceptance
1. MCP create_project must enforce the reserved `<data_root>/source-media` boundary before its first filesystem write. Resolve candidate paths through the nearest existing ancestor and compare canonical path components against the actual data-root source-media store; fail closed on unresolved paths.
2. Reject symlink/junction/reparse-point aliases in destination ancestry, including a Videos/DubFlow Output junction into source-media. Do not rely on literal component names or string-prefix checks.
3. Recheck the guarded destination immediately before project/work/transcript/spec writes and before ASR; preserve ordinary output roots outside the reserved store.
4. Add deterministic MCP create_project regressions for Linux symlink and Windows NTFS junction/reparse aliases. Prove rejection before project write/ASR; a marker in source-media retains identical bytes, SHA-256 and mtime. Windows assertion must execute and report ok, not skip.
5. Extend the Documentary Windows lane to execute the focused Python MCP intake regression in addition to existing Rust host/junction tests. No secrets, signing, release, upload or publish.
6. Update only ADR-0049 Phase 4b to describe the actual MCP tool filesystem boundaries (discovery/download destinations, new-project output, existing-project roots, read-only source inputs); do not change ADR decisions.
7. PR Fast and PR Integration must pass on final PR HEAD/current Main base; Integration must visibly select documentary; documentary-windows must execute and pass the Python MCP junction regression and existing Rust junction regressions.

## Allowed paths — exactly six
1. `.claims/issue-489-mcp-create-project-path-confinement.md`
2. `engine/dubflow/docu/intake.py`
3. `engine/dubflow/mcp/server.py`
4. `tests/docu/test_intake.py`
5. `.github/workflows/pr-documentary-windows.yml` — Codex is the sole writer for this workflow in this task; add only the focused Python test setup/step.
6. `docs/adr/ADR-0049-documentary.md` — Phase 4b only.

## Forbidden
All other product/source files; downloader/network policy; contracts/schemas/migrations; manifests/lockfiles; PR Fast/Integration workflows; registry/selector; watchdog; other CI workflows; desktop UI/Tauri; PROJECT_STATE/ROADMAP; other ADRs; media/models; credentials/customer data; signing/releases/publishing; unrelated cleanup. Do not weaken #458/#464 guards or alter generic downloader policy.

## Required tests
- Focused Python MCP intake tests on Linux, including symlink alias rejection before any write/ASR.
- The same focused Python MCP intake regression on Windows, proving the NTFS junction path executes and passes.
- Existing Rust host/junction tests remain green.
- PR Fast on final PR HEAD/current Main base.
- PR Integration visibly selects `documentary`; report selected components and skipped counts separately.
- documentary-windows executes the Python and Rust junction assertions with zero skips for the required assertions.

## Start protocol
After this Draft PR is opened, re-read all open PRs and claims. If any earlier valid lease or incompatible path owner is discovered, stop before material edits. Do not start product changes until the Owner-provisioned Codex Cloud Environment and write-capable Codex execution are available. Do not refresh this heartbeat without a new durable commit/checkpoint.

## Phase ledger
ASSIGNED=YES (Lead-created bounded lease for Codex)
STARTED=YES (metadata/lease only; Codex execution has not started)
CODED=NO
TESTED=NOT_RUN
INTEGRATED=NO
ACCEPTED=NO
BLOCKED_EXTERNAL=Codex Cloud Environment and write-capable execution not provisioned
