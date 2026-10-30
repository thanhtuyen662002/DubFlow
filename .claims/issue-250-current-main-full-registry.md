DUBFLOW_PR_V1
Issue: #250
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-30T04:40:34Z
Tested-Base-SHA: 769fceea0a2ee127fbdbced688335800cc2805a2
Conflict-Domains: current-main-full-registry-evidence
Expected-Paths: .claims/issue-250-current-main-full-registry.md,docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — Lead-created claim shell from exact current main. The timestamp above initializes the claim; it is not evidence that Coder 1 has started or produced work. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Why reopened
The prior #250 evidence and PR #319 completed on source SHA 2fb02b58c7d6d9464e9aaae0237ec23f512629ee (50/50 registry). Main has since advanced through #353 and #355 to 769fceea0a2ee127fbdbced688335800cc2805a2. Current Main Integration run #41003698911 used BASE_SHA=d7440647c26a9c70b7dda9b6dd56d939327d8573 and selected only product-video (19 tests, 1 skipped); this is selective evidence, not a full-registry run. Historical 50/50 must not be copied forward.

## Coder assignment
Coder 1 only: codex/01a11204/issue-174-continuation. This is the coder's sole active task and sole unclosed PR claim. Prior PR #319 is merged; no other live Coder 1 claim exists.

## Acceptance
1. Use a complete checkout of exact Main 769fceea0a2ee127fbdbced688335800cc2805a2.
2. Run, in order:
   - python -m unittest discover -s tests/ci
   - python scripts/ci/run_integration.py
   The integration command must have no --base-sha, no component filter and a complete checkout.
3. Record exact source SHA, runner/OS, Python version, UTC start/end, command, registered/selected/executed component counts, every component and command result, skips separately, run URL if hosted, and exit code in docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md.
4. SKIPPED is not PASS. Selective Integration, old-SHA results, and doc-only PR CI do not satisfy full-registry acceptance.
5. If any component fails, preserve the failure sufficiently to reproduce and stop. Do not edit selector, registry, workflows, tests, lockfiles or product code.
6. Run PR Fast + PR Integration on final exact PR HEAD/current tested base. Clearly state that these doc-PR checks do not replace the full-registry command.
7. If a complete checkout/runner is unavailable, record NOT_RUN with the exact missing capability; do not fabricate a table or copy historical results.

## Allowed material paths only
- docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
- this claim file

Forbidden: scripts/ci/**, tests/ci/**, component registry, workflows, lockfiles, product source, contracts, migrations, docs/PROJECT_STATE.yaml, release/signing files, credentials and unrelated paths. No selector/CI repair in this evidence-only task.

## Dependencies
Hard-Dependencies: none. Soft-Dependencies: #175, #186, #188, #199. Keep Draft until exact-current-Main full registry evidence and required PR checks are complete.
