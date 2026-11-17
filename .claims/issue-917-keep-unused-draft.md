DUBFLOW_PR_V1
Issue: #917
Lease-Owner: Claude/issue-917-keep-unused-draft
Lease-Heartbeat: 2026-11-17T04:18:28Z
Tested-Base-SHA: 1956cdf00634f554931a6a063eeef1b87a2150c0
Conflict-Domains: editor-draft-persistence
Expected-Paths:
- .claims/issue-917-keep-unused-draft.md
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src/features/edit/view.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/src-tauri/src/sfx.rs
Required-CI: PR Fast + desktop-editor-windows (cargo test; PR Integration selects no component for this path)
