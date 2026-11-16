DUBFLOW_PR_V1
Issue: #898
Lease-Owner: Claude/issue-898-subtle-motion
Lease-Heartbeat: 2026-11-16T18:11:26Z
Tested-Base-SHA: 5dc7e84684baa69f9d8a008334eeb2341d24a513
Conflict-Domains: edit-segment-look
Expected-Paths:
- .claims/issue-898-subtle-motion.md
- apps/desktop/src/features/edit/motion.ts
- apps/desktop/src/features/edit/look.ts
- apps/desktop/src/features/edit/look_view.ts
- apps/desktop/index.html
- apps/desktop/src-tauri/src/edit.rs
- apps/desktop/test/edit_motion_seeded.test.mjs
- apps/desktop/package.json
Required-CI: PR Fast + PR Integration (desktop) + desktop editor Windows (cargo test)
