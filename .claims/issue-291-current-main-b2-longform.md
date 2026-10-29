DUBFLOW_PR_V1
Issue: #291
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-29T06:51:00Z
Tested-Base-SHA: 23c2fa82c55fd799cf9b343aecdc29375f521aaa
Conflict-Domains: q175-single-continuous-longform-evidence
Expected-Paths: .claims/issue-291-current-main-b2-longform.md,docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main requalification only
Previous long-form evidence is from signed 1.1.6 and is historical for the later #320/#321 changes. This evidence-only leaf does not change product code or qualify all of #175.

### Source target and base freshness
Current main is `506b0ec6ec9bb649a859e7ced2fc0c0487c1cd26`. Signed stable v1.1.10 targets that exact source SHA. Use that installed signed build if available; otherwise use a non-published build from exactly that SHA. Record the actual binary/source identity and do not infer it from a version label.
The current PR/claim Tested-Base-SHA `23c2fa82c55fd799cf9b343aecdc29375f521aaa` is STALE_BASE. Merge current main into this branch normally (no force-push), then run required CI. Update Tested-Base-SHA only to the base actually incorporated and tested; until then this PR is not merge-ready.

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