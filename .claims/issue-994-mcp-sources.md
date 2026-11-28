DUBFLOW_PR_V1
Issue: #994
Lease-Owner: Claude/issue-994-mcp-sources
Lease-Heartbeat: 2026-11-28T22:30:00Z
Tested-Base-SHA: 6f0b205ebef6944072d117c8edd8ad721b5086d3
Conflict-Domains: mcp-server, discovery-adapters
Expected-Paths:
- .claims/issue-994-mcp-sources.md
- engine/dubflow/discovery/footage.py
- engine/dubflow/mcp/server.py
- engine/dubflow/mcp/sources.py
- engine/dubflow/mcp/activity.py
- tests/discovery/test_footage.py
- tests/docu/test_mcp.py
- tests/docu/test_mcp_sources.py
Required-CI: PR Fast + PR Integration

## Status
MCP phase 1: DubFlow-wide server with search_sources, list_footage_suffixes, get_trends, suggest_keywords and download_video quality.
