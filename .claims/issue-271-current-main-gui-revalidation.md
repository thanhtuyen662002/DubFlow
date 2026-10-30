DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-30T06:43:22Z
Tested-Base-SHA: 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — Lead-created successor claim from exact current main. The heartbeat initializes this claim and is not evidence that Coder 2 has started or produced work. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Prior claim lineage
Supersedes Draft PR #357 (closed without merge; branch preserved), HEAD 652f19f50193ecf7c32231962adb11d7268d9214, base/Tested-Base 769fceea0a2ee127fbdbced688335800cc2805a2. Its heartbeat 2026-10-30T04:40:34Z is more than 90 minutes old; there is no coder-authored commit/checkpoint and both required PR checks are completed, not queued/running. #357 changed only its Lead-authored claim file. No harness, npm, GUI or media evidence was produced there.

## Current baseline and dependencies
Main is exact SHA 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55, signed merge #363. Post-merge PR Fast #41012691809 and PR Integration #41012691822 both completed SUCCESS on this exact Main; these checks are not harness/npm/GUI/media acceptance.
Hard dependencies #196, #267 and #269 are completed; soft references #175 and #250 do not block this leaf. #175 remains OPEN / NOT_ACCEPTED.

## Assignment
Coder 2 only: Claude/issue-320-audio-track-storage. This is the coder's sole active task and sole unclosed PR claim.

## Acceptance
1. Reuse the existing files on Main; do not create duplicates:
   - scripts/release/chinese_desktop_review_smoke.py
   - tests/release/test_chinese_desktop_review_smoke.py
   - docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
2. Enforce expected-source-SHA preflight in the existing harness. Validate the installed release manifest before creating/running a job; fail closed for missing, malformed or mismatched SHA. Record expected and actual SHA in redacted evidence. Never infer source identity from version text.
3. Add tests for exact match, mismatch, missing/invalid SHA and verify failure occurs before supervisor/job creation.
4. Run and record exact commands, tool versions and outcomes:
   - python -m unittest tests.release.test_chinese_desktop_review_smoke
   - npm test --prefix apps/desktop
   - npm run build --prefix apps/desktop
5. Real acceptance requires a non-published Windows candidate built from exact Main 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55, authorized local Chinese media outside Git with no matching subtitle sidecar, explicit Chinese production CPU routing, non-empty Chinese transcript, Vietnamese translation provenance, playable output, SRT/ASS/editable artifacts, manifest/QC/source-output linkage.
6. Drive actual WebView/Tauri Review: edit/undo, reject blank save, save a valid revision, verify separate revision output, byte-identical original validated outputs, revision_of/provenance/QC and failed-save/retry behavior.
7. Run PR Fast + PR Integration on final exact HEAD/current tested base. Python host-contract replay and CI do not count as GUI/media acceptance.
8. If candidate, interactive Windows host or authorized media is unavailable, mark only that stage NOT_RUN with the exact missing capability. Do not use fixtures as real-media proof, download media/models, or publish.

## Allowed paths only
- scripts/release/chinese_desktop_review_smoke.py
- tests/release/test_chinese_desktop_review_smoke.py
- docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
- this claim file

Forbidden: all other scripts/tests, apps/desktop/**, engine/**, crates/**, contracts/schemas/migrations, CI/workflows/registry, lockfiles/manifests, docs/PROJECT_STATE.yaml, signing/secrets/publication, media/model assets, credentials and unrelated paths. No product-code edits or QC weakening. One writer owns the three existing material paths.

## External GUI prerequisite
The owner-triggered Windows Release run #41013478693 is currently building version 1.1.15 with RELEASE_CHANNEL=stable from exact Main 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55; it is not a non-published candidate. Do not substitute it. Coordinator/operator must dispatch .github/workflows/release.yml on refs/heads/main with a unique version, channel=candidate, publish=false. An interactive Windows host and authorized local Chinese media without a subtitle sidecar are also required. The Lead connector has no workflow-dispatch write action.