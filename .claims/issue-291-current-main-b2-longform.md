DUBFLOW_PR_V1
Issue: #291
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-29T06:51:00Z
Tested-Base-SHA: 23c2fa82c55fd799cf9b343aecdc29375f521aaa
Conflict-Domains: q175-single-continuous-longform-evidence
Expected-Paths: .claims/issue-291-current-main-b2-longform.md,docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main requalification only
Previous long-form evidence is from signed 1.1.6, before merged #320 B2 disk-preflight/dedup and #321 TTS condensation/partial-dubbing wording. This is evidence-only; no product edits.

### Acceptance
- On a non-published build from exact source 23c2fa82c55fd799cf9b343aecdc29375f521aaa, run one authorized single continuous 2–6 h recording with production CPU B2. No concatenation, loop, media download or committing media/models/private paths.
- Verify output duration/playability, QC, transcript/dubbing coverage and undubbed ratio, hashes, peak memory/free disk, work+editable footprint, timings and recoverability. Check #320 target <=10 GB for the 2 h 52 min source and whether #321 reduces the old 1.0% undubbed speech.
- Record source SHA-256, probe/time base, code SHA, runtime/model/profile, UTC start/end, wall time, command/UI path, terminal status and failures.
- Preserve prior validated output on failure. If the Windows host is shared with Coder 2, do not start the heavy run until #271 GUI work completes; prep may proceed.
- If the authorized source or isolated CPU runner is unavailable, record NOT_RUN and exact missing capability. No synthetic/concatenated substitute.
- Run PR Fast + Integration on final PR HEAD/current tested base. CI is not real-media evidence.

Allowed material path: docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md only, plus this claim file.
Forbidden: product code, tests, scripts, workflows, registry, contracts, lockfiles, docs/PROJECT_STATE.yaml, release/signing/publication, media/model assets, credentials, unrelated paths.
