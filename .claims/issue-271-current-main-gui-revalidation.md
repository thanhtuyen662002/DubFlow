DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-30T10:37:30Z
Tested-Base-SHA: 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — this is a Lead-created lease claim only. The heartbeat initializes the claim and is not evidence that Coder 2 has started. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Current baseline
Branch: agent/issue-271-current-main-7918f522
Exact base: main 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c.
Hard dependencies #196, #267 and #269 are closed/completed. Soft references #175 and #250 do not block this leaf; this task does not qualify all of #175.
Stable v1.1.16 is published from 7918 but is not a non-published candidate.

## Prior claim lineage
Supersedes Draft PR #365, closed as STALE_BASE / NO_PROGRESS. Its HEAD cafc9ff90751b7acc4049a1f26ff837f84922565 and old base 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55 are preserved. The diff was only the Lead-authored claim file; no coder-authored harness/test/evidence or GUI/media progress existed. Do not continue on #365's closed branch.

## Assignment — Coder 2 only
Identity: Claude/issue-320-audio-track-storage. This is Coder 2's sole active task and sole unclosed PR claim.

## Acceptance
1. Reuse the existing files on Main; do not create duplicates:
   - scripts/release/chinese_desktop_review_smoke.py
   - tests/release/test_chinese_desktop_review_smoke.py
   - docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
2. Add a required --expected-source-sha argument to the existing harness. Validate it as exactly 40 hexadecimal characters. Read the installed release-manifest.json and require its source_sha to be present, valid and exactly equal to the expected SHA. Fail closed for missing argument, malformed expected SHA, missing/malformed manifest SHA, or mismatch. Do this before creating the output directory or invoking any supervisor/job. Never infer source identity from version text.
3. Add unit tests for exact match, mismatch, missing expected SHA, malformed expected SHA, missing manifest source_sha and malformed manifest source_sha. Assert every rejection happens before supervisor/job creation and before output creation.
4. Run and record exact commands, tool versions and outcomes:
   - python -m unittest tests.release.test_chinese_desktop_review_smoke
   - npm test --prefix apps/desktop
   - npm run build --prefix apps/desktop
5. Update the evidence doc without deleting historical evidence. Clearly separate deterministic harness/npm results from real-media and GUI results. Do not call a Python replay a GUI test.
6. Real acceptance requires a non-published Windows candidate built from exact Main 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c, authorized local Chinese media outside Git with no matching subtitle sidecar, explicit Chinese production CPU routing, non-empty Chinese transcript, Vietnamese translation provenance, playable output, SRT/ASS/editable artifacts, and manifest/QC/source-output linkage.
7. Drive the actual WebView/Tauri Review flow: edit/undo, reject blank save, save a valid revision, verify separate revision output, byte-identical original validated outputs, revision_of/provenance/QC, and failed-save/retry behavior.
8. Run PR Fast + PR Integration on the final exact HEAD/current tested base. CI and host-contract replay do not count as GUI/media acceptance.
9. If candidate, interactive Windows host or authorized media is unavailable, mark only that stage NOT_RUN with the exact missing capability. Do not use fixtures as real-media proof, download media/models, or publish.

## Allowed material paths only
- scripts/release/chinese_desktop_review_smoke.py
- tests/release/test_chinese_desktop_review_smoke.py
- docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
- this claim file

Forbidden: all other scripts/tests, apps/desktop/**, engine/**, crates/**, contracts/schemas/migrations, CI/workflows/registry, lockfiles/manifests, docs/PROJECT_STATE.yaml, signing/secrets/publication, media/model assets, credentials and unrelated paths. No product-code edits or QC weakening. One writer owns the three existing material paths.

## External GUI prerequisite
Coordinator/operator must dispatch .github/workflows/release.yml on refs/heads/main with a unique version, channel=candidate, publish=false. An interactive Windows host and authorized local Chinese media without a subtitle sidecar are also required. The Lead connector has no workflow-dispatch write action. Do not substitute stable v1.1.16 or its release artifacts for the candidate.
