DUBFLOW_PR_V1
Issue: #949
Lease-Owner: Claude/issue-949-check-boxed-parts
Lease-Heartbeat: 2026-11-18T17:39:35Z
Tested-Base-SHA: 46c58d523d95089883166098cd1ff68f8ed5baaa
Conflict-Domains: desktop-edit-export-check,desktop-windows-real-ffmpeg-ci
Expected-Paths:
- .claims/issue-949-check-boxed-parts.md
- .github/workflows/pr-desktop-editor-windows.yml
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src-tauri/src/edit_check.rs
Required-CI: PR Fast + PR Integration + PR Desktop Editor Windows (job desktop-editor-windows, incl. the Kiểm tra kỹ thuật step against the pinned FFmpeg with the new boxed-check marker)
