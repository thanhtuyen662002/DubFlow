DUBFLOW_PR_V1
Issue: #291
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-29T06:48:30Z
Tested-Base-SHA: e089778e0f88024956b5ce9ff667a810f0fc0d7d
Conflict-Domains: q175-single-continuous-longform-evidence
Expected-Paths: .claims/issue-291-current-main-b2-longform.md,docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md
Required-CI: PR Fast + Integration

## Current-main requalification only
The previous long-form report is from signed 1.1.6, before merged #320 B2 disk-preflight/dedup and #321 TTS condensation. This claim is evidence-only and must not change product code.

### Acceptance
- On a non-published build from exact source e089778e0f88024956b5ce9ff667a810f0fc0d7d, run one authorized, single continuous 2–6 hour recording using the production CPU B2 profile. No concatenation, loop, media download, or committing media/models/private absolute paths.
- Verify output duration/playability, QC, transcript/dubbing coverage and undubbed ratio, output hashes, peak memory, peak/free disk, total work+editable footprint, stage timings and resume behavior. Specifically verify the #320 storage target (<=10 GB for the 2 h 52 min source) and whether the #321 change reduces the previously measured 1.0% undubbed speech.
- Record source SHA-256, media probe/time base, exact code SHA, runtime/model/profile IDs, UTC start/end, wall time, command/UI path, terminal status and reproducible failures.
- Preserve prior validated outputs on failure. If the same Windows host is used by Coder 2 for #271, do not start the heavy long-form run until that GUI run finishes; prep and dependency checks may proceed.
- If the authorized source or isolated CPU runner is unavailable, record NOT_RUN with the exact missing capability. No synthetic/concatenated substitute.
- Run PR Fast + Integration on the final PR HEAD/current tested base. CI is not the real-media pass.

Allowed material path: docs/production/Q175_CONTINUOUS_LONGFORM_EVIDENCE.md only, plus this required claim file.
Forbidden: product code, tests, scripts, workflows, registry, contracts, lockfiles, docs/PROJECT_STATE.yaml, release/signing/publishing, media/model assets, credentials, unrelated paths.
