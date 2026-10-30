DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-30T04:40:34Z
Tested-Base-SHA: 769fceea0a2ee127fbdbced688335800cc2805a2
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — Lead-created claim shell from exact current main. The timestamp above initializes the claim; it is not evidence that Coder 2 has started or produced work. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Prior lease lineage
Supersedes stale Draft PR #350 (closed without merge; branch preserved), HEAD d4df58b8b31a276d6777f82848b6cd6b76936390, base/Tested-Base 6290baa4d42964006e587d41d64fa7c11cf8e7d0. #350 contained only Lead-authored claim metadata, had no coder-authored implementation/evidence or reviews, and heartbeat 2026-10-30T02:45:08Z exceeded the 90-minute stale threshold. This successor starts from exact Main 769fceea0a2ee127fbdbced688335800cc2805a2.

## Dependencies
Hard #196/#267/#269 are CLOSED. Soft #175/#250 are compatibility references; #175 remains OPEN / NOT_ACCEPTED.

## Acceptance
1. Reuse the existing files on Main; do not create duplicates:
   - scripts/release/chinese_desktop_review_smoke.py
   - tests/release/test_chinese_desktop_review_smoke.py
   - docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
2. Enforce expected-source-SHA preflight in the existing harness. Validate the installed release manifest before creating/running a job; fail closed for missing, malformed or mismatched SHA. Include expected and actual SHA in the redacted evidence. Never infer source identity from version text.
3. Add tests for exact match, mismatch, missing/invalid SHA and verify failure occurs before supervisor/job creation.
4. Run and record exact commands, versions and outcomes:
   - python -m unittest tests.release.test_chinese_desktop_review_smoke
   - npm test --prefix apps/desktop
   - npm run build --prefix apps/desktop
5. For real acceptance, use a non-published Windows candidate built from exact Main 769fceea0a2ee127fbdbced688335800cc2805a2 and authorized local Chinese media outside Git with no matching subtitle sidecar. Run explicit Chinese production CPU routing; verify non-empty Chinese transcript, Vietnamese provenance, playable output, SRT/ASS/editable artifacts, manifest/QC/source-output linkage.
6. Drive actual WebView/Tauri Review: edit/undo, reject blank save, save a valid revision, verify separate revision output, byte-identical original validated outputs, revision_of/provenance/QC and failed-save/retry behavior.
7. Run PR Fast + PR Integration on final exact HEAD/current tested base. Python host-contract replay and CI do not count as GUI/media acceptance.
8. If exact-Main candidate, interactive Windows host or authorized media is unavailable, mark only that stage NOT_RUN with the exact missing capability. No fixtures, loops, media/model downloads or old stable binary substitution.

## Allowed paths only
- scripts/release/chinese_desktop_review_smoke.py
- tests/release/test_chinese_desktop_review_smoke.py
- docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
- this claim file

Forbidden: all other scripts/tests, apps/desktop/**, engine/**, crates/**, contracts/schemas/migrations, CI/workflows/registry, lockfiles/manifests, docs/PROJECT_STATE.yaml, signing/secrets/publication, media/model assets, credentials and unrelated paths. No product-code edits or QC weakening. One writer owns the three existing material paths.

## External GUI prerequisite
The latest stable v1.1.13 release targets d7440647c26a9c70b7dda9b6dd56d939327d8573, while current Main is 769fceea0a2ee127fbdbced688335800cc2805a2 after PR #355. Do not substitute stable v1.1.13. Coordinator/operator must dispatch .github/workflows/release.yml on refs/heads/main with a unique version, channel=candidate, publish=false. The Lead connector has no workflow-dispatch action. Use an interactive Windows host and previously authorized local Chinese media, or record the exact missing capability as NOT_RUN.
