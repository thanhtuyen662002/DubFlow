DUBFLOW_PR_V1
Issue: #940
Lease-Owner: Claude/issue-940-zoompan-fps-hang
Lease-Heartbeat: 2026-11-18T14:52:34Z
Tested-Base-SHA: 4cd1f30f8e46ac1e6537fce4fbdac6868d8849e2
Conflict-Domains: desktop-edit-export
Expected-Paths:
- .claims/issue-940-zoompan-end.md
- .github/workflows/pr-desktop-editor-windows.yml
- apps/desktop/src-tauri/src/edit.rs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles selected) + PR Desktop Editor Windows (job desktop-editor-windows, incl. the zoomed-parts export end step against the pinned FFmpeg)
