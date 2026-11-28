DUBFLOW_PR_V1
Issue: #998
Lease-Owner: Claude/issue-998-mcp-dubbing
Lease-Heartbeat: 2026-11-28T23:59:00Z
Tested-Base-SHA: 67e96168795f428c59e6f0187d08e2d640d3839c
Conflict-Domains: mcp-server, desktop-shell
Expected-Paths:
- .claims/issue-998-mcp-dubbing.md
- docs/adr/ADR-0058-mcp-queue-inbox.md
- engine/dubflow/mcp/dubbing.py
- engine/dubflow/mcp/server.py
- engine/dubflow/mcp/activity.py
- tests/docu/test_mcp.py
- tests/docu/test_mcp_dubbing.py
- apps/desktop/src-tauri/src/inbox.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src/features/queue/inbox.ts
- apps/desktop/src/features/queue/model.ts
- apps/desktop/src/features/ai/ai_context.ts
- apps/desktop/src/main.ts
- apps/desktop/test/inbox.test.mjs
- apps/desktop/test/ai_context.test.mjs
- apps/desktop/package.json
Required-CI: PR Fast + PR Integration

## Status
MCP phase 3: queue_dubbing_job, get_dubbing_job, list_dubbing_jobs through the app's queue inbox (ADR-0058).
