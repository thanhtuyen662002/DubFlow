DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-29T06:48:30Z
Tested-Base-SHA: e089778e0f88024956b5ce9ff667a810f0fc0d7d
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main desktop revalidation only
Existing evidence is from installed 1.1.1 and predates merged #320, #321 and #323 leaf 3. Re-run the bounded Chinese-to-Vietnamese review/revision flow on a non-published Windows build from exact source e089778e0f88024956b5ce9ff667a810f0fc0d7d. This is not #175 acceptance.

### Acceptance
- Use the previously owner-authorized real Chinese local video outside Git; verify no matching subtitle sidecar and record its SHA-256, duration/probe, model/runtime/profile identities and exact source SHA. Do not download media/models or commit private paths/assets.
- Drive the actual WebView/Tauri desktop UI: add/drop source, explicitly select Tiếng Trung, start job, inspect review, edit and undo a cue, verify blank edit cannot save, save/reprocess a valid edit, and wait for completion.
- Verify Chinese source routing and Vietnamese translation provenance; playable final video, SRT/ASS/editable output, manifest/QC hashes; revision is in a new folder, original validated outputs remain byte-identical, and revision_of links to the original. Record failed-save/retry behavior and actual UTC timings/status.
- Run `npm test --prefix apps/desktop` and `npm run build --prefix apps/desktop`, plus PR Fast + Integration on the final PR HEAD/current tested base. Record CI separately from real-media/GUI evidence.
- If Windows GUI, authorized media or current-main build capability is unavailable, record NOT_RUN with the exact missing capability. Do not substitute fixture/harness evidence, alter QC, or publish.
- If a product defect is found, stop and report exact reproduction; do not edit product code under this evidence-only lease.

Allowed material path: docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md only, plus this required claim file.
Forbidden: apps/desktop/**, engine/**, crates/**, contracts/**, migrations/**, tests/**, scripts/**, workflows/CI, lockfiles, models/manifests, docs/PROJECT_STATE.yaml, signing/secrets, release/publication, unrelated paths.
