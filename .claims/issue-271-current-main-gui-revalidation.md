DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-30T20:38:45Z
Tested-Base-SHA: 1e90f9d2a5128d0f885d76efc62958eca3a36fac
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED only — Lead-created claim for the existing Coder 2 lane. No coder progress is implied. This is the only active Coder 2 task and one Draft PR; #386 is parked/unclaimed.

## Current-Main deterministic work
Reuse the existing harness, test and evidence files; do not duplicate them. Add `--expected-source-sha`, validate lowercase 40- or 64-hex and exact-match the installed `release-manifest.json` `source_sha`. Missing/malformed/mismatched expected or actual values must fail before creating work/output directories or invoking the supervisor/job. Add match, mismatch, missing expected, malformed expected, missing actual and malformed actual tests. Update the evidence header so historical installed 1.1.0/1.1.1 GUI evidence remains historical and current Main revalidation is NOT_RUN until proven.

Required local tests:
- `python -m unittest tests.release.test_chinese_desktop_review_smoke`
- `npm test --prefix apps/desktop`
- `npm run build --prefix apps/desktop`
- PR Fast + PR Integration on final exact HEAD/current tested base.

## Real GUI/media gate (separate)
Requires a non-published Windows candidate built from exact Main 1e90f9d2a5128d0f885d76efc62958eca3a36fac, interactive Windows host, and one authorized local Chinese video outside Git with no matching subtitle sidecar. Run production supervisor with explicit Chinese routing and pinned CPU profile; verify Chinese transcript, Vietnamese translation provenance, playable MP4, SRT/ASS/editable assets, manifest/QC hashes and source/output linkage. Drive actual WebView/Tauri Review edit/undo/save/revision, confirm separate output and byte-identical original outputs, revision provenance/QC and failed-save/retry. If candidate, host or authorized media is unavailable, record exact NOT_RUN blocker and leave #271 unaccepted.

Allowed material paths only:
- `scripts/release/chinese_desktop_review_smoke.py`
- `tests/release/test_chinese_desktop_review_smoke.py`
- `docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md`
- this claim file

Forbidden: `apps/desktop/**`, `engine/**`, `crates/**`, `contracts/**`, migrations, `.github/workflows/**`, `scripts/ci/**`, package/lockfiles, existing release/production smoke scripts, model/manifests, signing/secrets, publication, media files, `docs/PROJECT_STATE.yaml`, unrelated paths.

Hard dependencies #196/#267/#269 are CLOSED. #175/#250 are soft dependencies. #382/#384 are not hard dependencies of this evidence slice; do not touch their paths. If a product defect is found, stop and report exact reproduction for a separately scoped task.

Status: ASSIGNED. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED not evidenced.