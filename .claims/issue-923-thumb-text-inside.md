DUBFLOW_PR_V1
Issue: #923
Lease-Owner: Claude/issue-923-thumb-text-inside
Lease-Heartbeat: 2026-11-17T11:43:54Z
Tested-Base-SHA: e5aaec31aa273187ac0a19f99a7a92139610de64
Conflict-Domains: thumbnail-render
Expected-Paths:
- .claims/issue-923-thumb-text-inside.md
- apps/desktop/src/features/edit/thumbnail.ts
- apps/desktop/src/features/edit/thumbnail_view.ts
- apps/desktop/test/edit_thumbnail.test.mjs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles component: desktop suites incl. edit_thumbnail test) + desktop-editor-windows (npm test, cargo test)
