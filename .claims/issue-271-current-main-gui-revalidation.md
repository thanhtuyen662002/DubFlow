DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-29T06:51:00Z
Tested-Base-SHA: a35553cd40190ce481cd00db13649eafced331cd
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main desktop revalidation only
Existing GUI evidence from installed 1.1.1 is historical. This bounded harness/evidence leaf revalidates the Chinese→Vietnamese review/revision flow after #320, #321 and #323; it does not qualify all of #175.

### Source target and base freshness
Current main is `a35553cd40190ce481cd00db13649eafced331cd`. Signed stable v1.1.10 targets older source SHA `506b0ec6ec9bb649a859e7ced2fc0c0487c1cd26`, not current main. Use a non-published Windows build from exactly `a35553cd40190ce481cd00db13649eafced331cd`; if that build/host is unavailable, mark NOT_RUN. Record actual binary/source identity.
Lead recovery sync: current main eebc0be36587165c32f4ce66100d984b856fb7ae is incorporated by this coordination-only merge commit. The resulting branch HEAD is recorded in the latest PR checkpoint. This does not imply coder STARTED/CODED/TESTED or product acceptance. PR Fast [40867700605](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40867700605) and Integration [40867700424](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40867700424) passed on that pre-metadata HEAD. This claim now records the incorporated/tested base `eebc0be36587165c32f4ce66100d984b856fb7ae`; the metadata change requires fresh exact-HEAD PR Fast + Integration. Desktop npm tests/build, harness tests and real GUI/media acceptance remain NOT_RUN until the existing coder executes them, so this PR is not merge-ready.

### Acceptance
- Add a standalone smoke harness accepting an explicit real local Chinese media path and app-owned installed runtime/supervisor paths. The authorized media stays outside Git; do not download media or models.
- Reject a source with a matching subtitle sidecar. Record source SHA-256, duration/probe, exact source SHA, runtime/model/profile identities, command, start/end UTC and exit status, without recording private absolute paths.
- Run the production supervisor with explicit Chinese routing and pinned CPU profile. Verify non-empty Chinese transcript, Vietnamese translation provenance, playable output, SRT/ASS/editable artifacts, manifest/QC hashes and source/output linkage.
- Open the generated result in the actual WebView/Tauri Review flow; edit and undo a cue, verify blank text cannot save, save/reprocess a valid edit, wait for completion, and verify separate revision output, byte-identical original validated outputs and correct revision_of/provenance/QC. Record failed-save/retry and timings.
- Run `npm test --prefix apps/desktop`, `npm run build --prefix apps/desktop`, unit tests for the new harness, PR Fast and PR Integration on final HEAD/current tested base. Keep CI evidence separate from real-media GUI evidence.
- If Windows GUI, authorized media or current-main build is unavailable, mark that stage NOT_RUN with the exact missing capability. No fixtures as real-media evidence, no QC weakening, product-code edits or publication. If a product defect appears, stop and report exact reproduction.

Allowed material paths only:
- `scripts/release/chinese_desktop_review_smoke.py` (new harness)
- `tests/release/test_chinese_desktop_review_smoke.py` (new validation tests)
- `docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md` (evidence)
- this claim file

Forbidden: all other `scripts/**` and `tests/**` paths; `apps/desktop/**`, `engine/**`, `crates/**`, `contracts/**`, `migrations/**`, `.github/workflows/**`, `scripts/ci/**`, existing release workflow/production smoke scripts, lockfiles, model/manifests, `docs/PROJECT_STATE.yaml`, signing/secrets, release/publication, media/model assets and unrelated paths.

Coordination status: this is still ASSIGNED. The claim heartbeat above is intentionally not refreshed by the Lead; no coder start, code, test or real-media GUI run is implied by this metadata repair.

## Live recovery checkpoint — 2026-10-29T17:42Z
- Authoritative main SHA: `a35553cd40190ce481cd00db13649eafced331cd` (merge #342; includes the zero-byte extension fix from #341).
- Lead performed a coordination-only branch sync with current main using an expected-head guard. No product code, harness, tests or real-media evidence changed.
- Previous claim-only PR Fast/Integration evidence on HEAD `9251f7eeb88db1438415eeb5cc45103f1db60d52` is stale for this new HEAD. Exact-head PR Fast + PR Integration must pass before this claim is considered current-base CI-tested.
- Lease heartbeat remains `2026-10-29T06:51:00Z`; this Lead sync is not coder progress.
- Harness, desktop npm tests/build and real Chinese-media GUI review remain pending/NOT_RUN. If the Windows GUI, authorized media or current-main build is unavailable, record the exact missing capability; do not substitute fixtures.
