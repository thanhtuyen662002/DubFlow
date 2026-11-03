DUBFLOW_PR_V1
Issue: #458
Lease-Owner: Claude/issue-458-docu-mcp-path-confinement
Lease-Heartbeat: 2026-11-03T17:28:24Z
Tested-Base-SHA: 3f059de5eaea809663374e5e83af89ea0217491f
Conflict-Domains: docu-mcp-filesystem-boundary,filesystem-security
Expected-Paths: .claims/issue-458-docu-mcp-path-confinement.md,engine/dubflow/docu/mcp_tools.py,engine/dubflow/mcp/server.py,tests/docu/test_mcp.py
Required-CI: PR Fast + PR Integration (documentary selected)

## Status
Coded. Project calls bound to the default or list_projects-chosen DubFlow Output root; symlink/junction escapes refused; tests in tests/docu/test_mcp.py.
