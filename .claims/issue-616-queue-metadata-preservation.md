DUBFLOW_PR_V1
Issue: #616
Lease-Owner: Claude/issue-616-queue-metadata-preservation
Lease-Heartbeat: 2026-11-15T08:38:00Z
Tested-Base-SHA: 08b25db079ed5baadd68000ffedd292c27d55a6d
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


## Lead base synchronization — 2026-11-16T18:39Z

- Existing branch was advanced non-force to include exact current Main `98738be8c962ab83b9721c13c2958984a09efc2a` using a two-parent merge commit; the existing lease commit remains in ancestry.
- This is integrator base maintenance only. No product source, tests, workflows, contracts, schemas or lockfiles were edited.
- `Tested-Base-SHA` remains the last evidenced base `08b25db079ed5baadd68000ffedd292c27d55a6d` until required checks are rerun on the new source HEAD; do not treat this sync as test evidence.
- `Lease-Heartbeat` remains `2026-11-15T08:38:00Z`; Lead synchronization is not coder progress and does not renew the lease.
