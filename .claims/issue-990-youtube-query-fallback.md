DUBFLOW_PR_V1
Issue: #990
Lease-Owner: Claude/issue-990-youtube-query-fallback
Lease-Heartbeat: 2026-11-28T20:40:00Z
Tested-Base-SHA: d56ce8a3a5a4e96741d352ebcb72477eb03b1200
Conflict-Domains: desktop-discovery
Expected-Paths:
- .claims/issue-990-youtube-query-fallback.md
- apps/desktop/src/features/discovery/model.ts
- apps/desktop/src/features/discovery/view.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/test/discovery.test.mjs
Required-CI: PR Fast + PR Integration

## Status
YouTube drops redundant quality words and retries once without suffixes when the first page is empty.
