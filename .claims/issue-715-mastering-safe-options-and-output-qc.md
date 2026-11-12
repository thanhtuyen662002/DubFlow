DUBFLOW_PR_V1
Issue: #715
Lease-Owner: Claude/issue-715-mastering-safe-options-and-output-qc
Lease-Heartbeat: 2026-11-12T16:54:30Z
Tested-Base-SHA: f46723aa93553da321e480e6cc0d6e62b6d9a08e
Conflict-Domains: mastering-options-write,mastering-output-qc,component-registry
Expected-Paths: .claims/issue-715-mastering-safe-options-and-output-qc.md,apps/desktop/src-tauri/src/mastering.rs,engine/dubflow/worker/production_job.py,apps/desktop/src/features/mastering/qc.ts,apps/desktop/test/mastering.test.mjs,tests/production/worker/test_dubbing_mastering.py,scripts/ci/component_registry.json
Required-CI: PR Fast + PR Integration full 61/61 + PR Desktop Editor Windows

## Lead-created lease / implementation handoff
This branch and Draft PR were created by the Lead solely to establish the already-authorized Claude lease. No product source or tests were edited. Claude is the sole implementation owner. Start material edits only after verifying this Draft PR is still open, the branch head is the claim commit below, and no earlier competing lease exists.

## Outcome
Harden Lồng tiếng mastering options writes against link/collision races and report actual post-AAC output loudness independently from whether normalization was applied.

## Hard dependencies
none

## Allowed paths — exactly seven
- .claims/issue-715-mastering-safe-options-and-output-qc.md
- apps/desktop/src-tauri/src/mastering.rs
- engine/dubflow/worker/production_job.py
- apps/desktop/src/features/mastering/qc.ts
- apps/desktop/test/mastering.test.mjs
- tests/production/worker/test_dubbing_mastering.py
- scripts/ci/component_registry.json (Claude is sole writer; add only the three desktop mastering roots specified by Issue #715)

## Forbidden
All other product/engine/test paths; schemas/contracts/migrations; workflow files; CI files other than the one registry path; lockfiles/manifests; customer media; credentials; signing/releases; deployment/publishing; unrelated refactors.

## Acceptance
1. Replace predictable .mastering.json.tmp + fs::write with a unique exclusively-created temp; never follow/truncate pre-existing symlink, hard-link or reparse point; preserve atomic replacement; clean up only this invocation's temp. Add collision/concurrency and marker bytes + SHA-256 + mtime regressions, including supported Linux symlink and Windows-safe hard-link/reparse cases.
2. Measure actual post-AAC final MP4 LUFS/true-peak/LRA whenever mastering is enabled and the final output has audio, including B2 with audio_filter=None and B1 fallback. Measurement failure remains null/non-fatal. Keep loudness meaning normalization/filter actually applied.
3. UI shows available post-AAC measurement even when loudness=false; mastering-off remains hidden and B1 fallback explanation remains.
4. Add production-branch tests for no-filter B2, measurement failure, B1 fallback, and UI visibility. Deliberately update the old B1 output_loudness-is-None assertion; do not weaken or skip it.
5. Registry adds only the three desktop mastering roots and demonstrably selects all 61 components.
6. Required hosted CI on final PR HEAD/current Main base: PR Fast, PR Integration full 61/61, PR Desktop Editor Windows. Changed-code tests must execute; SKIPPED is not PASS.
7. Separate ACCEPTED gate: Owner-authorized non-customer Windows desktop smoke, finished dubbing output, actual post-AAC card measurement and playback. If unavailable, record BLOCKED_EXTERNAL / NOT_ACCEPTED; synthetic tests do not ACCEPT.

## Recovery checkpoint
No product implementation or tests have been performed by the Lead. If Claude cannot proceed, leave this Draft intact and report the exact blocker; do not create a second task/PR. A stale lease is handled by inspecting this branch/PR and preserving its history, never deleting or resetting it.

## Phase ledger at lease creation
ASSIGNED=YES; STARTED=YES (valid Draft lease created; implementation not yet started); CODED=NO; TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.
