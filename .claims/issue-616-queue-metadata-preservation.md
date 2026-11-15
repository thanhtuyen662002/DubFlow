DUBFLOW_PR_V1
Issue: #616
Lease-Owner: Claude/issue-616-queue-metadata-preservation
Lease-Heartbeat: 2026-11-15T08:38:00Z
Tested-Base-SHA: 98738be8c962ab83b9721c13c2958984a09efc2a
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
