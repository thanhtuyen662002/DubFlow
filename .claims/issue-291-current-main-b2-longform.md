DUBFLOW_PR_V1
Issue: #291
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-29T06:51:00Z
Tested-Base-SHA: a35553cd40190ce481cd00db13649eafced331cd
Conflict-Domains: q175-single-continuous-longform-evidence
Expected-Paths: .claims/issue-291-current-main-b2-longform.md,docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main requalification only
Previous long-form evidence is from signed 1.1.6 and is historical for the later #320/#321 changes. This evidence-only leaf does not change product code or qualify all of #175.

### Source target and base freshness
Current main is `a35553cd40190ce481cd00db13649eafced331cd`. Signed stable v1.1.10 targets older source SHA `506b0ec6ec9bb649a859e7ced2fc0c0487c1cd26`, not current main. Use a non-published Windows build from exactly `a35553cd40190ce481cd00db13649eafced331cd`; if that build/host is unavailable, mark NOT_RUN. Record the actual binary/source identity and do not infer it from a version label.
Lead recovery sync: current main eebc0be36587165c32f4ce66100d984b856fb7ae is incorporated by this coordination-only merge commit. The resulting branch HEAD is recorded in the latest PR checkpoint. This does not imply coder STARTED/CODED/TESTED or product acceptance. PR Fast [40867667498](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40867667498) and Integration [40867667604](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40867667604) passed on that pre-metadata HEAD. This claim now records the incorporated/tested base `eebc0be36587165c32f4ce66100d984b856fb7ae`; the metadata change requires fresh exact-HEAD PR Fast + Integration. The real continuous 2–6 h recording remains NOT_RUN until the existing coder executes it, so this PR is not merge-ready.

### Acceptance
- Run one authorized/licensed, single continuous 2–6 h recording with production CPU B2. No concatenation, loop, synthetic substitute, media download or committing media/models/private paths.
- Verify output duration/playability, QC, transcript/dubbing coverage and undubbed ratio, source/output hashes, peak memory/free disk, work+editable footprint, timings and recoverability. Check #320's <=10 GB target for the 2 h 52 min source and whether #321 improves the historical 1.0% undubbed speech.
- Record source SHA-256, probe/time base, exact code SHA and binary source, runtime/model/profile, UTC start/end, wall time, command/UI path, terminal status and failures.
- Preserve prior validated output on failure. If the Windows host is shared with Coder 2, do not start this heavy run until #271's GUI slot is finished; preparation may proceed.
- If the authorized source or isolated CPU runner is unavailable, record NOT_RUN and the exact missing capability.
- Run PR Fast + Integration on the final PR HEAD/current tested base. CI is separate from real-media evidence.

Allowed material paths: `docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md` plus this claim file only.
Forbidden: product code, tests, scripts, workflows, registry, contracts, lockfiles, `docs/PROJECT_STATE.yaml`, release/signing/publication, media/model assets, credentials and unrelated paths.

Coordination status: this is still ASSIGNED. The claim heartbeat above is intentionally not refreshed by the Lead; no coder start, code, test or media run is implied by this metadata repair.

## Live recovery checkpoint — 2026-10-29T17:42Z
- Authoritative main SHA: `a35553cd40190ce481cd00db13649eafced331cd` (merge #342; includes the zero-byte extension fix from #341).
- Lead performed a coordination-only branch sync with current main using an expected-head guard. No product code or media evidence changed.
- Previous claim-only PR Fast/Integration evidence on HEAD `0d097e9bee6d7763f8fb69124e94bcaa99c9d105` is stale for this new HEAD. Exact-head PR Fast + PR Integration must pass before this claim is considered current-base CI-tested.
- Lease heartbeat remains `2026-10-29T06:51:00Z`; this Lead sync is not coder progress.
- The continuous 2–6 hour production CPU recording remains `NOT_RUN`. Do not start it without an authorized single continuous source and a build from this exact main SHA.
