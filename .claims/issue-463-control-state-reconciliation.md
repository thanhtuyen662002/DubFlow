DUBFLOW_PR_V1
Issue: #463
Lease-Owner: Lead/issue-463-live-control-state-reconciliation-r2
Lease-Heartbeat: 2026-11-25T21:08:21Z
Tested-Base-SHA: f2e67098af3422f1865f3554de8029f2555b1914
Conflict-Domains: repo-control-state-docs,completed-claim-release-metadata
Expected-Paths: .claims/issue-463-control-state-reconciliation.md,.claims/issue-416-release-tests-fail-closed.md,.claims/issue-417-desktop-editor-subtitles-ci.md,.claims/issue-464-docu-output-root-confinement.md,.claims/issue-439-current-main-chinese-review-qualification.md,docs/PROJECT_STATE.yaml,docs/ROADMAP.md
Required-CI: PR Fast

## History
The previous #463 control-state lease was INTEGRATED / ACCEPTED / RELEASED at PR #471, merge f76b602ea879cfc41712c1b0879ce821c8975d79. Its historical Tested-Base-SHA was bc801585b65c893db2269b525e75d9fb155e2aaa. Preserve that completion record; this r2 lease is a new bounded reconciliation task.

## Current bounded outcome
Reconcile only the live Project State / Roadmap snapshot and release stale completed claim metadata. No product implementation, tests, scripts, workflows, ADRs, or other docs.

## Current base
Main and initial Tested-Base-SHA: f2e67098af3422f1865f3554de8029f2555b1914.

## Allowed paths
- .claims/issue-463-control-state-reconciliation.md
- .claims/issue-416-release-tests-fail-closed.md
- .claims/issue-417-desktop-editor-subtitles-ci.md
- .claims/issue-464-docu-output-root-confinement.md
- .claims/issue-439-current-main-chinese-review-qualification.md
- docs/PROJECT_STATE.yaml
- docs/ROADMAP.md

The historical #487 claim file is absent on Main; do not recreate it. Do not modify Claude's active r2 claim for #439 or Codex's active #489 claim on their own branches.

## Forbidden
All product/source code, tests, scripts, workflows, registry/selector, contracts, migrations, lockfiles, ADRs, other docs, media/models, credentials, signing, releases, publishing, customer data and coder schedules/prompts. No direct Main push, force-push, branch deletion, or test/scanner disabling.

## Required evidence
- Preserve phase, first-user-value and baseline-dubbing gates unless live accepted evidence proves a change.
- Set state_validated_base_sha to the exact Main above.
- Keep 59/59 full-registry run 41780693790 / source SHA 713bd236 as historical, explicitly not current-Main full-registry evidence.
- Keep the previous release/soak run and SHA labelled historical; separately record the newer current-Main Windows Release/Soak test evidence with its one skipped test.
- Release completed claim files #416, #417 and #464 with actual PR/merge SHA and historical Tested-Base-SHA.
- Mark the old #439 claim as historical accepted/released at PR #462 / merge d92a0ba1da4225072dcd140a966f42b4ebb7753c, preserving Tested-Base-SHA 99edc85d12c53d536ac56adcf2830cbca8093bda. The current-Main #439 r2 task remains separate and OPEN.
- Do not recreate #487 claim because it is absent; record that fact.
- Update Roadmap's 2026-11-04 snapshot to the live Issue/PR state: #439 Claude / #973, #489 Codex / #975, #818 deferred, #490 closed, #884 open follow-up, #175/#291/#323 not planned, #382 completed, #463 this reconciliation.
- PR Fast must pass on final HEAD/current Main base.

## Ownership
This is Lead-owned control-plane documentation work on paths with no active coder lease. It does not consume or change either existing coder assignment: Codex remains sole owner of #489; Claude remains sole owner of #439. No coder schedule/prompt/permission changes.

## Phase ledger
ASSIGNED=YES (Lead control-doc writer)
STARTED=YES (Draft lease only)
CODED=YES (Lead docs/control only)
TESTED=YES (PR Fast exact HEAD; PR Integration control tests also SUCCESS, no product component selected)
INTEGRATED=NO
ACCEPTED=NO
