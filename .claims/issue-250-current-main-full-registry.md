DUBFLOW_PR_V1
Issue: #250
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-30T10:37:30Z
Tested-Base-SHA: 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c
Conflict-Domains: current-main-full-registry-evidence
Expected-Paths: .claims/issue-250-current-main-full-registry.md,docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — this is a Lead-created lease claim only. The heartbeat initializes the claim and is not evidence that Coder 1 has started. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Current baseline
Branch: agent/issue-250-current-main-7918f522
Exact base: main 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c (signed merge #367).
The prior 50/50 registry evidence is bound to 2fb02b58c7d6d9464e9aaae0237ec23f512629ee and is historical, not current-main evidence. Main Integration #41017882081 is selective and selected no product component for #367; it is not a full-registry run.

## Prior claim lineage
Supersedes Draft PR #364, closed as STALE_BASE / NO_PROGRESS. Its HEAD a961e6d6d8e209e3493f37a6fb076def5420f1b6 and old base 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55 are preserved. The diff was only the Lead-authored claim file; no coder-authored evidence or implementation existed. Do not continue on #364's closed branch.

## Assignment — Coder 1 only
Identity: codex/01a11204/issue-174-continuation. This is Coder 1's sole active task and sole unclosed PR claim. Hard dependencies: none. Soft references: #175, #186, #188, #199.

## Acceptance
1. Use a complete checkout of exact Main 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c.
2. Run in order:
   - python -m unittest discover -s tests/ci
   - python scripts/ci/run_integration.py
   The integration command must have no --base-sha, no component filter, and a complete checkout so every registered component is selected.
3. Record exact source SHA, runner/OS/image, Python version, UTC start/end, exact commands, current registered/selected/executed component counts, every component/command result, skips separately, workflow/run URL if hosted, and exit code in docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md.
4. SKIPPED is not PASS. Selective Integration, old-SHA evidence and doc-only PR CI do not satisfy this acceptance.
5. If any component fails, preserve enough output to reproduce and stop. Do not edit selector, registry, workflows, tests, lockfiles or product code.
6. Run PR Fast + PR Integration on the final exact PR HEAD/current tested base. State explicitly that doc-PR CI does not replace the full-registry command evidence.
7. If a complete checkout/runner is unavailable, record NOT_RUN and the exact missing capability; do not fabricate a table or copy historical results.

## Hosted unblock
The existing .github/workflows/pr-integration.yml supports workflow_dispatch with no inputs. Dispatch on refs/heads/main; an empty BASE_SHA makes the runner select all components. Verify the run head_sha is exactly 7918f5228e0f112fcaaa77fa84875d00e4d8ea4c and inspect every component result. The Lead connector has no workflow-dispatch write action; coordinator/operator must launch this if Coder 1 cannot run the complete checkout locally.

## Allowed material paths only
- docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
- this claim file

Forbidden: scripts/ci/**, tests/ci/**, component registry, workflows, lockfiles, product source, contracts, migrations, docs/PROJECT_STATE.yaml, release/signing files, credentials and unrelated paths. No selector/CI repair in this evidence-only task.
