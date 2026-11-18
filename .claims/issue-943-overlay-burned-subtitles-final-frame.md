DUBFLOW_PR_V1
Issue: #943
Lease-Owner: Claude/issue-943-overlay-burned-subtitles-final-frame
Lease-Heartbeat: 2026-11-18T15:33:47Z
Tested-Base-SHA: 41c624d6c4416a371bc6148a1ad4f3b3908b5fc1
Conflict-Domains: desktop-edit-export,ffmpeg-render-graph,desktop-windows-real-ffmpeg-ci
Expected-Paths:
- .claims/issue-943-overlay-burned-subtitles-final-frame.md
- .github/workflows/pr-desktop-editor-windows.yml
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src-tauri/src/edit_parts.rs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles selected) + PR Desktop Editor Windows (pinned real FFmpeg test for single-run and parallel-parts export)
