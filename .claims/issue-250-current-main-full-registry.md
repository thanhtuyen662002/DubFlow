DUBFLOW_PR_V1
Issue: #250
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-30T06:43:22Z
Tested-Base-SHA: 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55
Conflict-Domains: current-main-full-registry-evidence
Expected-Paths: .claims/issue-250-current-main-full-registry.md,docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED — Lead-created successor claim from exact current main. The heartbeat initializes this claim and is not evidence that Coder 1 has started or produced work. STARTED/CODED/TESTED/INTEGRATED/ACCEPTED remain unverified.

## Prior claim lineage
Supersedes Draft PR #356 (closed without merge; branch preserved), HEAD 2bf5883524345a920fa8503929a3ab1edc182973, base/Tested-Base 769fceea0a2ee127fbdbced688335800cc2805a2. Its heartbeat 2026-10-30T04:40:34Z is more than 90 minutes old; there is no coder-authored commit/checkpoint and both required PR checks are completed, not queued/running. #356 changed only its Lead-authored claim file. No full-registry evidence was produced there.

## Current baseline
Main is exact SHA 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55, signed merge #363. Post-merge PR Fast #41012691809 and PR Integration #41012691822 both completed SUCCESS on this exact Main. Integration is selective; it is not full-registry evidence. Existing docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md still records historical 50/50 evidence on 2fb02b58c7d6d9464e9aaae0237ec23f512629ee and must not be copied forward.

## Assignment
Coder 1 only: codex/01a11204/issue-174-continuation. This is the coder's sole active task and sole unclosed PR claim. Hard dependencies: none. Soft references: #175, #186, #188, #199.

## Acceptance
1. Use a complete checkout of exact Main 962bc0580fb4fbb86c638ac81f87fa4ee73d9f55.
2. Run, in order:
   - python -m unittest discover -s tests/ci
   - python scripts/ci/run_integration.py
   The integration command must have no --base-sha, no component filter and a complete checkout so the script selects every registered component.
3. Record exact source SHA, runner/OS/image, Python version, UTC start/end, exact commands, registered/selected/executed counts, every component and command result, skips separately, workflow/run URL if hosted, and exit code in docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md.
4. SKIPPED is not PASS. Selective Integration, old-SHA results, and doc-only PR CI do not satisfy full-registry acceptance.
5. If any component fails, preserve enough output to reproduce and stop. Do not edit selector, registry, workflows, tests, lockfiles or product code.
6. Run PR Fast + PR Integration on the final exact PR HEAD/current tested base. State explicitly that doc-PR CI does not replace the full-registry command.
7. If a complete checkout/runner is unavailable, record NOT_RUN with the exact missing capability; do not fabricate a table or copy historical results.

## Allowed material paths only
- docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md
- this claim file

Forbidden: scripts/ci/**, tests/ci/**, component registry, workflows, lockfiles, product source, contracts, migrations, docs/PROJECT_STATE.yaml, release/signing files, credentials and unrelated paths. No selector/CI repair in this evidence-only task.

## Lead environment note
The Lead's attempt to obtain a complete public checkout failed because this execution environment could not resolve github.com (DNS/network unavailable). This is not evidence that Coder 1's own runner is unavailable. Continue on a complete runner if available; otherwise record the exact NOT_RUN capability.