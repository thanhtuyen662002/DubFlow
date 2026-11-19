DUBFLOW_PR_V1
Issue: #971
Lease-Owner: Claude/issue-971-export-stops-on-close
Lease-Heartbeat: 2026-11-19T14:03:27Z
Tested-Base-SHA: a37d2aee339a7a8ccb7288c1cc01d9c88de40903
Conflict-Domains: desktop-edit-export
Expected-Paths:
- .claims/issue-971-export-stops-on-close.md
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src-tauri/src/edit_parts.rs
- apps/desktop/src-tauri/src/main.rs
Required-CI: PR Fast + PR Integration

## Status
Fix found by the 1.2.0-rc1 Windows check (D7): closing DubFlow stops every Chỉnh sửa export's FFmpeg; regression test in edit.rs.
