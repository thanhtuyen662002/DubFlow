DUBFLOW_PR_V1
Issue: #818
Lease-Owner: Claude/issue-818-review-vietnamese-piece-count
Lease-Heartbeat: 2026-11-13T16:39:00Z
Tested-Base-SHA: 1687771341792874c55cc532d1bc593c8ac23e95
Conflict-Domains: mastering-editorial,desktop-review
Expected-Paths: .claims/issue-818-review-vietnamese-piece-count.md,engine/dubflow/mastering/review.py,apps/desktop/src/features/mastering/review_panel.ts,tests/mastering/test_review.py,apps/desktop/test/mastering_panel.test.mjs
Required-CI: PR Fast + PR Integration (mastering and desktop-editor-subtitles selected)

## Outcome
Fix the two English review validation messages and preserve the whole-shot exclusion's current piece count after the same source footage is re-split under new shot IDs.

## Hard dependencies
none

## Soft dependencies
none

## Allowed paths — exactly five
- .claims/issue-818-review-vietnamese-piece-count.md
- engine/dubflow/mastering/review.py
- apps/desktop/src/features/mastering/review_panel.ts
- tests/mastering/test_review.py
- apps/desktop/test/mastering_panel.test.mjs

## Forbidden
All other product/engine/test paths; model.ts and unrelated UI modules; contracts/schemas/migrations; CI workflows and component registry; manifests/lockfiles; ADRs/docs outside this claim; media/models; credentials/customer data; signing/releases; deployment/publishing; unrelated refactors. Do not edit the separate edit-history-dependent moment-choice behavior mentioned in the Issue; keep it outside this leaf and report it for later triage if still reproducible.

## Acceptance
1. Unsupported review schema versions are still rejected without rewriting the saved review; the surfaced message is Vietnamese and names the supported v1–v3 range. Add a regression.
2. An empty `include` request is still rejected without mutation; the surfaced message is Vietnamese and tells the user what to select. Add a regression.
3. A whole-shot exclusion made against one shot ID survives a re-split of the same source range into three new IDs. Reconciliation/view must preserve the whole-shot meaning and report the current three pieces, not silently downgrade to a single-shot exclusion. Add a Python regression asserting the current `covers` IDs/count and a desktop panel regression asserting the side list displays `(3 đoạn)` after re-split.
4. Preserve partial-piece exclusion, undo, reuse-conflict and existing v1/v2/v3 read compatibility; do not weaken or skip tests.
5. Required CI on final PR HEAD/current Main base: PR Fast and PR Integration must visibly select both `mastering` and `desktop-editor-subtitles`. Changed tests must execute; SKIPPED is not PASS.

## CI routing evidence
On Main 1687771341792874c55cc532d1bc593c8ac23e95, `scripts/ci/component_registry.json` has 61 components. `mastering` roots include `engine/dubflow/mastering`, `tests/mastering` and `apps/desktop/src/features/mastering`; `desktop-editor-subtitles` includes `apps/desktop/test`. No registry/workflow change is authorized or needed.

## Recovery / ownership
Claude is the sole implementation writer for this leaf and may have no second active Issue or new unclosed PR. The Lead created only this claim and Draft PR; no product source or test code was changed. Keep Draft until all acceptance and exact-base CI evidence is complete.

## Phase ledger at lease creation
ASSIGNED=YES; STARTED=YES (valid Draft lease only; implementation not yet started); CODED=NO; TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.
