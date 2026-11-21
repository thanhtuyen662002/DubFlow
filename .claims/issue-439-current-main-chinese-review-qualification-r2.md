DUBFLOW_PR_V1
Issue: #439
Lease-Owner: Claude/issue-439-current-main-chinese-review-qualification-r2
Lease-Heartbeat: 2026-11-21T00:36:16Z
Tested-Base-SHA: f2e67098af3422f1865f3554de8029f2555b1914
Conflict-Domains: desktop-current-main-media-qualification,production-evidence
Expected-Paths: .claims/issue-439-current-main-chinese-review-qualification-r2.md,docs/production/CHINESE_DESKTOP_CURRENT_MAIN_QUALIFICATION.md
Required-CI: PR Fast

## Lead-established Draft lease — implementation not started
The Lead created this claim branch and metadata-only Draft PR to establish the Coordinator-authorized Claude assignment. This is coordination/lease setup only; Claude has not yet started implementation or media qualification. Claude is the sole owner of the evidence report and any subsequent claim heartbeat.

## Outcome
Revalidate the Chinese-to-Vietnamese desktop review/revision/output leaf on the exact current Main. This is evidence-only and does not reopen or accept Epic #175.

## Hard dependencies
#414, #418, #420, #422 and #426 are CLOSED / COMPLETED. #175 is CLOSED / NOT_PLANNED and is not a dependency or acceptance target.

## Allowed paths — exactly two
1. .claims/issue-439-current-main-chinese-review-qualification-r2.md
2. docs/production/CHINESE_DESKTOP_CURRENT_MAIN_QUALIFICATION.md

## Forbidden
All product source, tests, workflows, manifests, lockfiles, ADRs, PROJECT_STATE.yaml, ROADMAP.md, other shared control files, credentials, customer data, media acquisition/copy/upload/download, release/build/install/signing/publishing changes and unrelated cleanup. Do not expand this evidence-only task into a product repair.

## Host and media prerequisite
Use only an already available Owner-authorized isolated Windows host with an installed build whose manifest source_sha equals the current Main, and only the already Owner-authorized non-customer Chinese source with SHA-256:
bbba2c9be2d6717d694c05eba090b5f85af341123d313732bbf7cef9224785b1
The source must already be present on that same host. Do not acquire, copy, upload, download, or commit media. Do not build, install, release, sign, or publish anything for this task.

If either prerequisite is unavailable, record the exact unavailable host/source capability in the report and mark BLOCKED_EXTERNAL / real-media TESTED=NOT_RUN / ACCEPTED=NO. Do not substitute synthetic media, a harness, or a demo.

## Acceptance
If both prerequisites are available, record one continuous real desktop UI + Tauri host session proving:
1. Chinese source without subtitle sidecar, explicit Chinese routing, Vietnamese translation, and source/backend/package provenance.
2. Desktop review edit, undo, reapply, valid save, and invalid-save rejection.
3. Regeneration into a separate revision folder.
4. The original five validated outputs remain byte-identical and revision_of.json binds to the original manifest SHA.
5. Revision video is playable with expected video/audio streams; manifest, QC and revision provenance match the actual output.

Record current Main/release/install source SHA, OS/app/engine/supervisor versions, source hash/metadata, timestamps, UI steps, redacted output paths, original/revision hashes and QC facts. PR Fast must pass on the final PR HEAD/current Main base; PR Fast is not product acceptance.

## Phase ledger
ASSIGNED=YES
STARTED=YES (valid Draft lease established by Lead; Claude implementation/evidence work not yet started)
CODED=NO
TESTED=NOT_RUN
INTEGRATED=NO
ACCEPTED=NO
BLOCKED_EXTERNAL=HOST_AND_SAME_HOST_SOURCE_NOT_YET_VERIFIED
