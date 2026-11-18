DUBFLOW_PR_V1
Issue: #945
Lease-Owner: Claude/issue-945-link-service-warm-start
Lease-Heartbeat: 2026-11-18T17:39:46Z
Tested-Base-SHA: 46c58d523d95089883166098cd1ff68f8ed5baaa
Conflict-Domains: desktop-link-download,desktop-app-startup
Expected-Paths:
- .claims/issue-945-link-service-warm-start.md
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src-tauri/src/source.rs
Required-CI: PR Fast + PR Integration + PR Desktop Editor Windows (job desktop-editor-windows, full desktop host tests incl. the real child-process service test)
