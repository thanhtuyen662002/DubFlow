DUBFLOW_PR_V1
Issue: #616
Lease-Owner: Claude/issue-616-queue-metadata-preservation
Lease-Heartbeat: 2026-11-15T08:38:00Z
Tested-Base-SHA: fab4b759fa99fa7d63dae3e73cf6dd0e14689f33
Conflict-Domains: desktop-shell,media-persistence,local-storage-recovery
Expected-Paths: .claims/issue-616-queue-metadata-preservation.md,apps/desktop/src/main.ts,apps/desktop/src/features/media/model.ts,apps/desktop/src/features/media/view.ts,apps/desktop/test/media.test.mjs
Required-CI: PR Fast + PR Integration (desktop-editor-subtitles selected) + PR Desktop Editor Windows

## Lease handoff
This branch and Draft PR were created by the Lead solely to establish the already-authorized Claude assignment from Issue #616 comment #6891053301. Lead has made no product-source or test changes. Claude is the sole implementation writer for this lease. Before material edits, re-read current Main, all open PRs and claims, verify this PR remains the sole live claim for #616, and confirm the exact base/HEAD.

## Outcome
Preserve manually curated Hàng đợi metadata when browser storage reads or recovery-backup writes fail. Never silently replace unreadable saved state with a seed derived only from Lồng tiếng jobs.

## Hard dependencies
none

## Soft dependencies
#601 (CLOSED / COMPLETED)

## Allowed paths — exactly five
- .claims/issue-616-queue-metadata-preservation.md
- apps/desktop/src/main.ts
- apps/desktop/src/features/media/model.ts
- apps/desktop/src/features/media/view.ts
- apps/desktop/test/media.test.mjs

## Forbidden
All other desktop features, Rust host, database/schema/migrations, contracts, lockfiles, workflows, downloader/MCP, media/models, credentials, customer data, releases, signing, deployment, publishing and unrelated changes.

## Acceptance
1. Distinguish storage outcomes: missing, valid, corrupt and unavailable/read-error. A getItem exception must never be treated as a missing key.
2. If stored content is corrupt, preserve the original raw value before replacing the primary key. If the .corrupt backup write fails, do not overwrite the primary value; keep reconstructed state session-only and show an actionable warning.
3. Check the initial seed write result. If it fails, clearly tell the user the list is memory-only until storage recovers; do not claim it was saved.
4. Add deterministic fault-injection regressions for getItem throwing, corrupt-backup setItem failing while a primary seed write would otherwise succeed, and initial seed write failure. Assert original primary bytes remain unchanged whenever backup is not confirmed.
5. Preserve normal add/remove behavior, deduplication, the 5,000-item cap and existing user-facing recovery messages.

## Required CI
- PR Fast on final PR HEAD.
- PR Integration must visibly select desktop-editor-subtitles.
- PR Desktop Editor Windows must run desktop npm tests/build and Rust host tests on the final PR HEAD/current Main base.
- Any skipped required test is not a pass.

## Phase ledger at lease creation
ASSIGNED: YES
STARTED: YES (valid Draft PR lease created; implementation has not started)
CODED: NO
TESTED: NOT_RUN
INTEGRATED: NO
ACCEPTED: NO


## Lead base synchronization — 2026-11-15T16:42:14.187Z

- Lead incorporated exact Main `08b25db079ed5baadd68000ffedd292c27d55a6d` into this existing branch with a non-force two-parent merge commit. The existing claim commit remains the first parent; current Main is the second parent.
- Only this claim file was changed by Lead. No product source, tests, workflows, contracts, schemas, lockfiles or other paths were edited.
- The coder heartbeat remains `2026-11-15T08:38:00Z`; this Lead synchronization is not a coder heartbeat and does not renew coder liveness.
- Phase remains: ASSIGNED=YES; STARTED=YES (Draft lease only); CODED=NO; TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.


## Lead base synchronization / CI evidence — 2026-11-16T18:39Z

- Existing branch was advanced non-force to include exact current Main `98738be8c962ab83b9721c13c2958984a09efc2a` using a two-parent merge commit; the original lease commit remains in ancestry.
- PR Fast #43748411177 passed on the synchronized claim-only HEAD: governance plus 14 deterministic tests.
- PR Integration #43748411229 passed with `BASE_SHA=98738be8c962ab83b9721c13c2958984a09efc2a`, but explicitly selected no registered integration component because only the claim file changed. This is not #616 product acceptance.
- `Tested-Base-SHA` is updated to `98738be8c962ab83b9721c13c2958984a09efc2a`; exact-head PR Fast/Integration rerun after the following metadata commit remains pending. Desktop Editor Windows is not applicable to the current claim-only diff and must run after product/test paths change.
- No product source, tests, workflows, contracts, schemas or lockfiles were edited by Lead.
- `Lease-Heartbeat` remains `2026-11-15T08:38:00Z`; Lead synchronization is not coder progress and does not renew the lease.


## Lead CI base confirmation — 2026-11-16T18:44Z

- On preceding HEAD `6d0b036ffb1ddf41e7e7327825bf3808673b4892`, PR Fast #43748411177 passed (governance + 14 deterministic tests).
- PR Integration #43748411229 passed on `98738be8c962ab83b9721c13c2958984a09efc2a` but explicitly selected no registered component because this branch remains claim-only.
- This does not satisfy #616 product acceptance; exact final HEAD checks must rerun after this metadata update, and the desktop Windows lane remains pending until product/test paths are changed.
- The claim now records the current base `98738be8c962ab83b9721c13c2958984a09efc2a`; the stale coder heartbeat remains unchanged.


## Lead non-force base synchronization — 2026-11-16 13:42 ICT

- Live Main re-read immediately before this update: `425373bada2e76d17e29506864cb2e1d4a9b34ac` (merge PR #874).
- Existing branch HEAD before synchronization: `1998cf2a13af7aaf0e59697e24c42c7683eafee2`; prior comparison was ahead 5 / behind 2 because Main advanced by two commits.
- The only branch-specific path is this claim file. Main's two intervening commits are preserved as the second parent; no product source/test, workflow, contract, schema, migration or lockfile path is changed.
- Updated the top-level `Tested-Base-SHA` to the exact Main above. The original stale heartbeat `2026-11-15T08:38:00Z` is deliberately unchanged: this Lead sync is not Claude progress and does not renew the coder lease.
- Phase remains ASSIGNED=YES; STARTED=YES (Draft lease only); CODED=NO; product TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.


## Lead non-force post-merge base synchronization — 2026-11-16 13:53 ICT

- Post-merge Main after Owner merged PR #876: `b44eb8ab0c69ddf077743113da3815d7d55eb11c` (merge SHA).
- Previous branch HEAD: `3123678d771c770c6a14bf45f16ed196420ff741`; previous tested base: `425373bada2e76d17e29506864cb2e1d4a9b34ac`.
- Preserved previous branch HEAD as first parent and current Main as second parent. The tree is current Main plus this claim file; no product source/test path is edited by Lead.
- Updated the top-level `Tested-Base-SHA` to current Main. Required PR checks on the new HEAD are pending/being verified; this metadata sync is not product testing or coder progress.
- Lease heartbeat remains `2026-11-15T08:38:00Z`; it is not renewed by this Lead sync.
- Phase remains ASSIGNED=YES; STARTED=YES (Draft lease only); CODED=NO; product TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.


## Lead base synchronization after PR #878 — 2026-11-16 14:51 ICT
- Exact Main re-read before sync: `fab4b759fa99fa7d63dae3e73cf6dd0e14689f33` (Owner-merged PR #878; parent `b44eb8ab0c69ddf077743113da3815d7d55eb11c`).
- Existing branch HEAD before sync: `9e80b10e88ac028054ce75bf4394e1b981c94993`; Main→branch comparison was ahead 7 / behind 3. The only branch-specific path was this claim file; Main's three commits touched `.claims/issue-877-ambiguous-separator.md` and `tests/mastering/x2/**`, with no overlap against the #616 allowlist.
- Lead incorporated Main non-force as the second parent while preserving the prior branch HEAD as first parent. No product source/test path was changed by Lead.
- `Tested-Base-SHA` now names the incorporated Main; PR Fast and Integration on the new exact HEAD are pending verification. This metadata sync is not coder progress and does not refresh the stale Lease-Heartbeat.
- Phase remains ASSIGNED=YES; STARTED=YES (Draft lease only); CODED=NO; product TESTED=NOT_RUN; INTEGRATED=NO; ACCEPTED=NO.
