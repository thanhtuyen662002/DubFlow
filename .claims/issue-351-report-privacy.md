DUBFLOW_PR_V1
Issue: #351
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-30T02:51:46Z
Tested-Base-SHA: 6290baa4d42964006e587d41d64fa7c11cf8e7d0
Conflict-Domains: support-report-privacy,desktop-report-command
Expected-Paths: .claims/issue-351-report-privacy.md,apps/desktop/src-tauri/src/main.rs,engine/dubflow/support/report.py,tests/support/test_report.py
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — bounded privacy/security follow-up. This claim commit is Lead-authored coordination metadata only; STARTED/CODED/TESTED/INTEGRATED/ACCEPTED are not claimed.

## Lineage
Original diagnostic-report feature merged via PR #346, merge SHA `c09f1f9ea69197ad691bae5d0c26721aba63c5d1`. The owner closed Issue #345 as COMPLETED; the Lead respected that disposition and closed claim-only PR #349 without implementation, preserving its branch. This separate security follow-up is tracked in Issue #351. Coder 1's earlier #329/#291 claim was also stale and closed; #291 remains OPEN / NOT_ACCEPTED / NOT_RUN.

## Security findings
- Tauri `create_report` passes the global `control/jobs.sqlite3`; report.py archives it when present and <=50 MiB, potentially exposing records beyond the selected job.
- `summary.json` includes the absolute local `output_dir`.
- Leaf-only symlink checks do not prevent traversal through a symlinked parent; all report and file-size paths need canonical containment.

## Acceptance
1. Remove global `jobs.sqlite3` and all cross-job database/state from a one-job report. Remove the Tauri/CLI database argument; retain required per-job diagnostics.
2. Do not write absolute local paths into any archive entry. Redact path-bearing metadata to safe relative labels/basenames or omit it.
3. Resolve the selected output root once. Every included report and file-size row must be a regular file whose resolved path remains inside that root. Reject symlinked roots/parents and escapes; do not follow symlinks while enumerating.
4. Add regression tests proving an existing small SQLite DB is not archived, the absolute test root is absent from archive bytes, a symlinked parent pointing outside is rejected/excluded, media/model files remain excluded, and normal per-job reports still work.
5. Run `python -m unittest tests.support.test_report`, `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --locked`, PR Fast and PR Integration on final exact HEAD/current tested base.

## Allowed paths only
- `apps/desktop/src-tauri/src/main.rs` — `create_report` command only
- `engine/dubflow/support/report.py`
- `tests/support/test_report.py`
- this claim file

Forbidden: all other `apps/desktop/**`, `engine/**`, `tests/**`, `crates/**`, contracts/schemas/migrations, workflows/CI/registry, lockfiles/manifests, `docs/PROJECT_STATE.yaml`, secrets/signing/release publication, media/model assets, credentials and unrelated paths. No UI expansion, schema change or product-video edits. One writer only for the three material paths.

## Dependencies and next action
Hard dependencies: none. Soft dependencies: none. Coder 1 should begin with regression tests and narrow report hardening. Keep Draft until all acceptance and exact-head CI pass.