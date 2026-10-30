DUBFLOW_PR_V1
Issue: #271
Lease-Owner: Claude/issue-320-audio-track-storage
Lease-Heartbeat: 2026-10-30T02:45:08Z
Tested-Base-SHA: 6290baa4d42964006e587d41d64fa7c11cf8e7d0
Conflict-Domains: desktop-real-chinese-media-smoke
Expected-Paths: .claims/issue-271-current-main-gui-revalidation.md,scripts/release/chinese_desktop_review_smoke.py,tests/release/test_chinese_desktop_review_smoke.py,docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — current-Main revalidation only. This claim commit is Lead-authored coordination metadata; no coder STARTED/CODED/TESTED or GUI/media evidence is claimed.

## Prior lease lineage
Supersedes stale Draft PR #330, branch `agent/issue-271-current-main-gui-revalidation-23c2fa8`, HEAD `d2bff24bf8f682dc5d8ab96b1fe05301901c53b3`, base/Tested-Base `a35553cd40190ce481cd00db13649eafced331cd`. It contained only claim metadata, had no coder-authored implementation/evidence or reviews, and its heartbeat exceeded the 90-minute stale threshold. The old branch is preserved. This successor starts from exact Main `6290baa4d42964006e587d41d64fa7c11cf8e7d0`.

## Dependencies
Hard dependencies #196, #267 and #269 are CLOSED. Soft references #175 and #250 are CLOSED; #175 itself remains OPEN / NOT_ACCEPTED.

## Acceptance
1. Reuse the existing three files on Main; do not create duplicates:
   - `scripts/release/chinese_desktop_review_smoke.py`
   - `tests/release/test_chinese_desktop_review_smoke.py`
   - `docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md`
2. Run `python -m unittest tests.release.test_chinese_desktop_review_smoke`, `npm test --prefix apps/desktop`, and `npm run build --prefix apps/desktop`. Record exact command results and tool versions in the existing evidence document.
3. Add/verify an expected-source-SHA preflight in the existing harness/tests. The current release manifest source SHA must equal the exact tested Main SHA; never infer source identity from a version label.
4. For real acceptance, use a non-published Windows build from exact Main `6290baa4d42964006e587d41d64fa7c11cf8e7d0` and authorized local Chinese media outside Git with no matching subtitle sidecar. Run explicit Chinese production CPU routing; verify non-empty Chinese transcript, Vietnamese provenance, playable output, SRT/ASS/editable artifacts, manifest/QC/source-output linkage.
5. Drive the actual WebView/Tauri Review flow: edit/undo, reject blank save, save a valid revision, verify separate revision output, byte-identical original validated outputs, revision_of/provenance/QC and failed-save/retry behavior.
6. Run PR Fast + PR Integration on final exact HEAD/current tested base. CI and the Python host-contract replay do not count as GUI/media acceptance.
7. If the exact-Main Windows GUI host or authorized local Chinese media is unavailable, mark only that stage NOT_RUN with the exact missing capability. Fixtures, loops and old installed 1.1.1 evidence do not substitute.

## Allowed material paths only
- `scripts/release/chinese_desktop_review_smoke.py`
- `tests/release/test_chinese_desktop_review_smoke.py`
- `docs/production/CHINESE_DESKTOP_REVIEW_EVIDENCE.md`
- this claim file

Forbidden: all other `scripts/**` and `tests/**`, `apps/desktop/**`, `engine/**`, `crates/**`, contracts/schemas/migrations, CI/workflows/registry, lockfiles/manifests, `docs/PROJECT_STATE.yaml`, signing/secrets/publication, media/model assets, credentials and unrelated paths. No product-code edits or QC weakening. One writer only for the three material paths.

## Next action
Start with the codeable unit-test/npm test/build work now. Use the current-Main Windows artifact only after run #40991347284 finishes and its source SHA is verified. Keep Draft until the real-media GUI stage and required evidence are complete.