DUBFLOW_PR_V1
Issue: #250
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-31T03:36:53Z
Tested-Base-SHA: 1e90f9d2a5128d0f885d76efc62958eca3a36fac
Conflict-Domains: current-main-full-registry-evidence
Expected-Paths: .claims/issue-250-current-main-full-registry.md,docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — claim-only; no full-registry execution or implementation evidence yet.

## Bounded acceptance
1. From a complete checkout of exact Main 1e90f9d2a5128d0f885d76efc62958eca3a36fac, run python -m unittest discover -s tests/ci -p 'test_*.py' -v, then python scripts/ci/run_integration.py with no --base-sha and no component filter.
2. Record source SHA, runner/OS/Python, UTC start/end, registered/selected/executed component counts (verify count from this checkout), every component result, skips, workflow/run URL and overall exit code in docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md.
3. If any component fails, preserve the failure and stop. Do not suppress tests or edit selectors, registry, workflows, lockfiles, product code or tests.
4. Run PR Fast and PR Integration on the final documentation PR HEAD/current Main base. State explicitly that docs-only PR CI is not the full-registry proof.
5. No Windows Release, Release / Soak, publish, customer data or real-media spending.

## Allowed material path
- docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md

## Forbidden
- scripts/ci/**, .github/workflows/**, tests/**, lockfiles, product source, models/media, release/signing/secrets, docs/PROJECT_STATE.yaml, unrelated documentation and any other path.

## Dependencies
- Hard dependencies: none.
- Soft context: #175, #186, #188, #199.
- No overlap with Coder 2's #384 / PR #392.

## Recovery
If complete checkout or workflow-dispatch capability is unavailable, record the exact missing capability and leave the full-registry result NOT_RUN. Do not substitute selective CI or historical SHA evidence.
