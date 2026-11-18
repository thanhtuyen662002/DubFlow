DUBFLOW_PR_V1
Issue: #945
Lease-Owner: Claude/issue-945-link-service-warm-start
Lease-Heartbeat: 2026-11-18T17:02:21Z
Tested-Base-SHA: 41c624d6c4416a371bc6148a1ad4f3b3908b5fc1
Conflict-Domains: desktop-link-download,desktop-app-startup
Expected-Paths:
- .claims/issue-945-link-service-warm-start.md
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src-tauri/src/source.rs
Required-CI: PR Fast + PR Integration + PR Desktop Editor Windows (job desktop-editor-windows, full desktop host tests incl. the real child-process service test)
