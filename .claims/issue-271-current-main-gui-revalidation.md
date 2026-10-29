DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-29T06:51:00Z
Tested-Base-SHA: 23c2fa82c55fd799cf9b343aecdc29375f521aaa
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main desktop revalidation only
Existing GUI evidence is from installed 1.1.1, before #320, #321 and #323 leaf 3. Re-run the bounded Chinese→Vietnamese review/revision flow on a non-published Windows build from exact source 23c2fa82c55fd799cf9b343aecdc29375f521aaa. This is not #175 acceptance.

### Acceptance
- Use the previously owner-authorized real Chinese local video outside Git; verify no matching subtitle sidecar; record source SHA-256, duration/probe, runtime/model/profile IDs and exact source SHA. No media/model downloads or private paths/assets in Git.
- Drive the actual WebView/Tauri UI: add/drop source, explicitly select Tiếng Trung, start, inspect review, edit/undo a cue, verify blank text cannot save, save/reprocess a valid edit, wait for completion.
- Verify Chinese routing/Vietnamese provenance, playable output, SRT/ASS/editable pack, manifest/QC hashes, separate revision folder, byte-identical original validated outputs and correct revision_of link. Record failed-save/retry and UTC timings/status.
- Run npm test --prefix apps/desktop and npm run build --prefix apps/desktop; also PR Fast + Integration on final PR HEAD/current tested base. Separate CI evidence from real-media GUI evidence.
- If Windows GUI, authorized media or current-main build is unavailable, mark NOT_RUN with exact missing capability. No fixture substitution, QC weakening, product-code edits or publication.
- If a product defect appears, stop and report reproduction; do not expand this evidence-only lease.

Allowed material path: docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md only, plus this claim file.
Forbidden: apps/desktop/**, engine/**, crates/**, contracts/**, migrations/**, tests/**, scripts/**, workflows/CI, lockfiles, models/manifests, docs/PROJECT_STATE.yaml, signing/secrets, release/publication, unrelated paths.
