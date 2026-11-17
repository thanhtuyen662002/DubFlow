DUBFLOW_PR_V1
Issue: #921
Lease-Owner: Claude/issue-921-rc22-folder-thumb
Lease-Heartbeat: 2026-11-17T07:00:27Z
Tested-Base-SHA: dcc3cc05ba78f8d4ae899c6ecf155da2ed6326cc
Conflict-Domains: edit-export-folder,thumbnail-templates
Expected-Paths:
- .claims/issue-921-rc22-folder-thumb.md
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src/features/edit/thumbnail.ts
- apps/desktop/src/features/edit/thumbnail_view.ts
- apps/desktop/test/edit_thumbnail.test.mjs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles component: desktop suites incl. edit_thumbnail test) + desktop-editor-windows (npm test, cargo test)
