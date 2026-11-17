DUBFLOW_PR_V1
Issue: #919
Lease-Owner: Claude/issue-919-compilation-reselect
Lease-Heartbeat: 2026-11-17T04:26:04Z
Tested-Base-SHA: 177d9397f71c60d9f6da56590691191d431b9fe4
Conflict-Domains: compilation-page-status
Expected-Paths:
- .claims/issue-919-compilation-reselect.md
- apps/desktop/src/features/compilation/view.ts
- apps/desktop/test/compilation_raw.test.mjs
Required-CI: PR Fast + desktop-editor-windows (npm test; PR Integration selects no component for this path)
