DUBFLOW_PR_V1
Issue: #439
Lease-Owner: Claude/issue-439-current-main-chinese-review-qualification-r3
Lease-Heartbeat: 2026-11-28T08:07:35Z
Tested-Base-SHA: d56ce8a3a5a4e96741d352ebcb72477eb03b1200
Conflict-Domains: desktop-current-main-media-qualification,production-evidence
Expected-Paths: .claims/issue-439-current-main-chinese-review-qualification-r2.md,docs/production/CHINESE_DESKTOP_CURRENT_MAIN_QUALIFICATION.md
Required-CI: PR Fast

## Coder 2 / Claude — sole active bounded task (r3 reactivation)
This fresh lease supersedes the closed, metadata-only PR #973 for the same Issue. PR #973 and its branch remain preserved as history. Owner's prior redirect task #987 / PR #988 is now CLOSED / COMPLETED and merged into Main at d56ce8a3a5a4e96741d352ebcb72477eb03b1200; Coder 2 has no other open PR. This assignment is one bounded Chinese-to-Vietnamese desktop review/revision/output evidence leaf serving the historical #175 objective. It does not reopen, accept, or claim Epic #175.

## Exact base and current evidence boundary
- Main at claim creation: `d56ce8a3a5a4e96741d352ebcb72477eb03b1200`.
- Existing report `docs/production/CHINESE_DESKTOP_CURRENT_MAIN_QUALIFICATION.md` is historical and records build/source SHA `99edc85d12c53d536ac56adcf2830cbca8093bda`; it is not evidence for this Main.
- Already Owner-authorized non-customer source SHA-256: `bbba2c9be2d6717d694c05eba090b5f85af341123d313732bbf7cef9224785b1`.
- The source must already exist on the same Owner-authorized isolated Windows host as an installed build whose manifest `source_sha` exactly equals `d56ce8a3a5a4e96741d352ebcb72477eb03b1200`. This Lead has not verified those same-host prerequisites.
- A Windows release-bundle workflow for this Main is currently in progress; do not use that as permission to build, install, sign, publish, or otherwise create a new candidate for this evidence-only task.

## Hard / soft dependencies
- Hard-Dependencies: #414, #418, #420, #422, #426 — all live-verified CLOSED / COMPLETED.
- Soft-Dependencies: #175, #267, #271, #417. #175 is CLOSED / NOT_PLANNED and is not an acceptance target; #267 and #417 are CLOSED / COMPLETED; #271 is CLOSED / NOT_PLANNED.
- Redirect predecessor #987 is CLOSED / COMPLETED; PR #988 is merged into this Main.

## Allowed paths — exactly two
1. `.claims/issue-439-current-main-chinese-review-qualification-r2.md`
2. `docs/production/CHINESE_DESKTOP_CURRENT_MAIN_QUALIFICATION.md`

Claude is the sole writer of the evidence report and this claim's coder-authored progress/heartbeat. The Lead-created initial claim commit is metadata only.

## Forbidden
All product/source/test/workflow files; Tauri/Rust or desktop UI changes; contracts/schemas/migrations; manifests/lockfiles; ADRs; `docs/PROJECT_STATE.yaml`, `docs/ROADMAP.md` and other shared control files; credentials, customer data and model files; media acquisition, copying, upload, download or commit; new build/install, release, signing, retagging, publish or deploy; unrelated cleanup. No synthetic/concatenated media or harness-only run may substitute for the real Desktop/Tauri flow.

## First action — read-only same-host prerequisite check
Before any real-media test, read-only verify on the already Owner-authorized isolated Windows host that:
1. the installed app manifest has `source_sha=d56ce8a3a5a4e96741d352ebcb72477eb03b1200`; and
2. the already Owner-authorized source with SHA-256 `bbba2c9be2d6717d694c05eba090b5f85af341123d313732bbf7cef9224785b1` is already present on that same host.

Do not copy, acquire, upload, download, or otherwise move the source. Do not build or install anything. If either prerequisite is absent or cannot be verified, update only the allowed report with the exact observed limitation and keep real-media TESTED=NOT_RUN, BLOCKED_EXTERNAL, ACCEPTED=NO. Do not substitute an older build, synthetic media, browser preview, or replay harness.

## Acceptance if both prerequisites are already satisfied
Record one continuous real Windows Desktop UI + Tauri host session proving:
1. Chinese source with no subtitle sidecar, explicit Chinese routing, Vietnamese translation, and source/backend/package provenance.
2. Desktop review edit, undo, reapply, valid save, and invalid blank-save rejection.
3. Regeneration into a distinct sibling revision folder.
4. The original five validated outputs remain byte-identical; `revision_of.json` binds the revision to the original manifest SHA.
5. Revision video is playable with expected video/audio streams; manifest, QC and revision provenance match the actual output.
6. Record exact Main/build/source SHA, release/artifact/version already installed, OS/app/engine/supervisor versions, source hash/metadata, timestamps, UI steps, redacted paths, output hashes and QC facts.

PR Fast must pass on final PR HEAD with Tested-Base-SHA equal to current Main. PR Fast is metadata CI only, not product acceptance. No skipped required assertion counts as pass.

## Recovery
If a product defect is reproduced, stop at the smallest reproducible failure and record the exact Main file/command/output. Do not expand this evidence-only assignment into product code; any repair needs a separately coordinated leaf and single writer.

## Phase ledger
ASSIGNED=YES (Owner-reopened #439, one bounded Claude task)
STARTED=NO (Lead metadata lease only; Claude execution not yet observed)
CODED=NO
TESTED=NOT_RUN (real Desktop/Tauri/media)
INTEGRATED=NO
ACCEPTED=NO
BLOCKED_EXTERNAL=YES_PENDING_SAME_HOST_BUILD_AND_SOURCE_READ_ONLY_VERIFICATION