DUBFLOW_PR_V1
Issue: #489
Lease-Owner: Claude/issue-489-mcp-create-project-path-confinement
Lease-Heartbeat: 2026-11-13T07:18:30.521Z
Tested-Base-SHA: 74c16d04ebec0a0fa357eeca54d76c4eac1dc545
Conflict-Domains: documentary-mcp-create,source-media-path-confinement,documentary-windows-lane
Expected-Paths: .claims/issue-489-mcp-create-project-path-confinement.md,engine/dubflow/docu/intake.py,engine/dubflow/mcp/server.py,tests/docu/test_intake.py,.github/workflows/pr-documentary-windows.yml,docs/adr/ADR-0049-documentary.md
Required-CI: PR Fast + PR Integration (documentary selected) + PR Documentary Windows

## Outcome
Confine MCP documentary project creation outside the reserved <data_root>/source-media store. Prevent output aliases through symlinks, NTFS junctions or other reparse points before any project/work/transcript/spec write or ASR.

## Dependencies
Hard-Dependencies: none
Soft-Dependencies: #458,#464,#485,#487 (all CLOSED / COMPLETED; compatibility context only).

## Allowed paths only
- .claims/issue-489-mcp-create-project-path-confinement.md
- engine/dubflow/docu/intake.py
- engine/dubflow/mcp/server.py
- tests/docu/test_intake.py
- .github/workflows/pr-documentary-windows.yml (sole workflow writer for this task; only path filters and focused Python test setup/step)
- docs/adr/ADR-0049-documentary.md (Phase 4b only)

## Forbidden
All other product/source/test paths; engine/dubflow/download/**; contracts, schemas, migrations; registry/selector; PR Fast/Integration workflows; other workflows; manifests/lockfiles; desktop UI/Tauri host; unrelated ADR/docs; media/models; credentials/customer data; signing, releases, deployment, publishing and unrelated changes. No new dependency.

## Acceptance
1. Pass the actual data root into create_project from the MCP host. Resolve the destination through its nearest existing ancestor and enforce canonical containment against the actual data_root/source-media store; fail closed on unresolved paths.
2. Reject symlink/junction/reparse-point aliases in output ancestry, including Videos/DubFlow Output redirected into source-media. Do not use literal component-name or string-prefix checks.
3. Recheck the guarded destination immediately before project/work/transcript/spec writes and before ASR starts; preserve ordinary output roots outside the reserved store.
4. Add deterministic Linux symlink and Windows NTFS-junction tests for the MCP create_project path. Prove rejection before any project write/ASR and preserve a reserved-store marker's bytes, SHA-256 and mtime. Windows assertion must execute and report ok, not skip.
5. Update this workflow's pull_request path filters to cover engine/dubflow/docu/**, engine/dubflow/mcp/** and tests/docu/**; add pinned Python 3.12 setup and the focused intake security test while preserving the existing Rust host/junction assertions. Keep the lane read-only.
6. Update only Phase 4b of ADR-0049 to document the expanded MCP tools and the actual boundaries for discovery/download destinations, new-project output, existing-project roots and read-only source inputs.
7. PR Fast and PR Integration must pass on final PR HEAD/current Main base; Integration must visibly select documentary. PR Documentary Windows must execute both the Python MCP intake symlink/junction regression and existing Rust junction regressions without skips.

## Evidence and recovery
No product implementation or tests are written by the Lead. This commit establishes only the Draft lease. If a Windows runner or required test capability is missing, report the exact lane/blocker and remain Draft; do not substitute a synthetic test for the required Windows junction assertion.

## Phase ledger
ASSIGNED=YES; STARTED=YES (valid Draft lease only; implementation not started); CODED=NO; TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.
