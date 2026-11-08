# Issue #626 canvas aspect ratio

Owner request in the project thread (2026-11-08): a custom output frame size in Chỉnh sửa, such as 16:9, 9:16 or 1:1. Scope: the "Tỉ lệ" control under the preview (`canvas_view.ts`), the edit's `canvas` (model, draft, export plan, matchesExport), the player frame taking that shape (blurred backdrop, black bars, cover crop), the export dialog showing it, and the host accepting custom W:H shapes and a crop-to-fill reframe (`edit.rs`). Real-app check on the owner's PC is part of acceptance.
