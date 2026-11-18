DUBFLOW_PR_V1
Issue: #935
Lease-Owner: Claude/issue-935-export-check
Lease-Heartbeat: 2026-11-18T03:54:15Z
Tested-Base-SHA: 0e84ab4aa9a972b6fe0f7f948f77915da0980825
Conflict-Domains: desktop-edit-export,desktop-compilation
Expected-Paths:
- .claims/issue-935-export-check.md
- .github/workflows/pr-desktop-editor-windows.yml
- apps/desktop/index.html
- apps/desktop/package.json
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/src-tauri/src/edit_check.rs
- apps/desktop/src-tauri/src/edit_parts.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src/features/compilation/view.ts
- apps/desktop/src/features/edit/export_check.ts
- apps/desktop/src/features/edit/view.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/src/styles.css
- apps/desktop/test/compilation.test.mjs
- apps/desktop/test/edit_export_check.test.mjs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles selected) + PR Desktop Editor Windows (incl. the real-FFmpeg export check step)
