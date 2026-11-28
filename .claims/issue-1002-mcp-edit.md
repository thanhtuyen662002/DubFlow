DUBFLOW_PR_V1
Issue: #1002
Lease-Owner: Claude/issue-1002-mcp-edit
Lease-Heartbeat: 2026-11-29T01:30:00Z
Tested-Base-SHA: 464e831850c1910679186b1d1a86e312557fe36a
Conflict-Domains: mcp-server, desktop-shell, desktop-editor
Expected-Paths:
- .claims/issue-1002-mcp-edit.md
- docs/adr/ADR-0059-mcp-edit-inbox.md
- engine/dubflow/mcp/editing.py
- engine/dubflow/mcp/server.py
- engine/dubflow/mcp/activity.py
- engine/dubflow/mcp/sources.py
- tests/docu/test_mcp.py
- tests/docu/test_mcp_editing.py
- apps/desktop/src-tauri/src/inbox.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src/features/edit/agent.ts
- apps/desktop/src/features/queue/inbox.ts
- apps/desktop/src/main.ts
- apps/desktop/test/edit_agent.test.mjs
- apps/desktop/test/inbox.test.mjs
- apps/desktop/package.json
Required-CI: PR Fast + PR Integration

## Status
MCP phase 5: get_edit, save_edit, get_edit_job through Chỉnh sửa's own draft in the running app (ADR-0059).
