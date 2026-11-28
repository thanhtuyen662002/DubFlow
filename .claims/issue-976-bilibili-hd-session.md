DUBFLOW_PR_V1
Issue: #976
Lease-Owner: Claude/issue-976-bilibili-hd-session
Lease-Heartbeat: 2026-11-28T15:30:00Z
Tested-Base-SHA: 696355f6aa061b4276ff933046d42f7961b33db9
Conflict-Domains: desktop-link-source,desktop-shell-ui,source-adapter-sdk
Expected-Paths:
- .claims/issue-976-bilibili-hd-session.md
- apps/desktop/index.html
- apps/desktop/src/main.ts
- apps/desktop/src/styles.css
- apps/desktop/src/features/bilibili/view.ts
- apps/desktop/src/features/links/status.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/test/links.test.mjs
- apps/desktop/src-tauri/src/bilibili_session.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src-tauri/src/source.rs
- engine/dubflow/download/authenticated_native.py
- tests/source_adapter/test_bilibili_api_fallback.py
Required-CI: PR Fast + PR Integration

## Status
Bilibili links download with the browser's Bilibili login (1080p, 4K for 大会员) instead of anonymous 480p; HDR/Dolby Vision streams are left out beside SDR.
