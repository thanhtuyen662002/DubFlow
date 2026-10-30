DUBFLOW_PR_V1
Issue: #345
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-30T02:45:08Z
Tested-Base-SHA: 6290baa4d42964006e587d41d64fa7c11cf8e7d0
Conflict-Domains: support-report-privacy,desktop-report-command
Expected-Paths: .claims/issue-345-report-and-hints.md,apps/desktop/src-tauri/src/main.rs,engine/dubflow/support/report.py,tests/support/test_report.py
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — privacy-hardening follow-up to the feature merged in PR #346. This claim commit is Lead-authored coordination metadata only; STARTED/CODED/TESTED/INTEGRATED/ACCEPTED are not claimed.

## Prior lease lineage
The original feature was implemented by `Claude/issue-345-report-and-hints` and merged in PR #346, merge SHA `c09f1f9ea69197ad691bae5d0c26721aba63c5d1`. The follow-up reuses Issue #345 because the original issue remains open. Coder 1's stale claim PR #329 (Issue #291), HEAD `0917fd72fe89a24c30b1c26afa832ea8f6fdaf84`, was closed/reclaimed after >90 minutes without coder-authored progress; its branch is preserved. Issue #291 remains OPEN / NOT_ACCEPTED / NOT_RUN and is parked, not completed.

## Security findings to fix
- The Tauri `create_report` command passes the global `control/jobs.sqlite3`; the report builder archives it when present and <=50 MiB. This can expose records beyond the selected job.
- `summary.json` includes the absolute local `output_dir`.
- Report inclusion checks only whether the final path is a symlink; a symlinked parent can resolve a report path outside the selected output root. Size enumeration must use the same confinement.

## Acceptance
1. Remove global `jobs.sqlite3` inclusion and its Tauri/CLI argument. Keep only per-job diagnostics.
2. Do not write absolute local paths into any archive entry. Redact path-bearing metadata to safe basenames/relative labels or omit it; do not include other jobs' records.
3. Resolve the output root once. Every included report and file-size row must be a regular file whose resolved path remains inside that root. Reject symlinked roots/parents and escapes; do not follow symlinks while enumerating.
4. Add tests proving an existing small SQLite DB is not archived, the absolute test root is absent from archive bytes, a symlinked parent pointing outside is rejected/excluded, media/model files remain excluded, and normal per-job reports still work.
5. Run `python -m unittest tests.support.test_report`, `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --locked`, PR Fast and PR Integration on the final exact HEAD/current tested base.

## Allowed material paths only
- `apps/desktop/src-tauri/src/main.rs` — `create_report` command only
- `engine/dubflow/support/report.py`
- `tests/support/test_report.py`
- this claim file

Forbidden: other `apps/desktop/**`, `engine/**`, `tests/**`, `crates/**`, contracts/schemas/migrations, workflows/CI/registry, lockfiles/manifests, `docs/PROJECT_STATE.yaml`, secrets/signing/release publication, media/model assets, credentials and unrelated paths. No UI expansion, schema change, or product-video edits. One writer only for the three material paths.

## Dependencies and next action
Hard dependencies: none. Soft dependencies: none. Coder 1 should start with the regression tests and narrow report hardening; keep Draft until all acceptance and exact-head CI pass.