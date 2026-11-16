DUBFLOW_PR_V1
Issue: #901
Lease-Owner: Claude/issue-901-thumbnail
Lease-Heartbeat: 2026-11-16T18:18:25Z
Tested-Base-SHA: 8837dc40dc09fc5b7c3671b322c03e8dc6cc8ce4
Conflict-Domains: edit-thumbnail
Expected-Paths:
- .claims/issue-901-thumbnail.md
- apps/desktop/index.html
- apps/desktop/package.json
- apps/desktop/src/styles.css
- apps/desktop/src/main.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/src/features/edit/model.ts
- apps/desktop/src/features/edit/thumbnail.ts
- apps/desktop/src/features/edit/thumbnail_view.ts
- apps/desktop/src/features/edit/view.ts
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src-tauri/src/thumbnail.rs
- apps/desktop/test/edit.test.mjs
- apps/desktop/test/edit_thumbnail.test.mjs
Required-CI: PR Fast + PR Integration (desktop) + desktop editor Windows (cargo test)
