DUBFLOW_PR_V1
Issue: #386
Lease-Owner: Claude/issue-386-gemini-resilience
Lease-Heartbeat: 2026-10-31T04:45:00Z
Tested-Base-SHA: ea5287bcfdb417b52a307e405c520846f13076c1
Conflict-Domains: desktop-ai-provider,ci-component-registry
Expected-Paths: .claims/issue-386-gemini-resilience.md,apps/desktop/src/features/ai/provider.ts,apps/desktop/src/features/reframe/ai.ts,apps/desktop/src/features/shorts/ai.ts,apps/desktop/test/ai.test.mjs,scripts/ci/component_registry.json
Required-CI: PR Fast + PR Integration + desktop-ai-provider

## Status
ASSIGNED — Lead-created successor claim on current Main. No coder-authored follow-up implementation or test result is implied.

## Bounded acceptance
1. Keep gemini-3.5-flash-lite first, one bounded same-model retry, then gemini-3.8-flash fallback; each attempt has a 90-second timeout.
2. Retry 429, all HTTP 5xx (500–599), and timeouts only. Do not retry invalid/missing keys, other 4xx, refusal/safety, malformed JSON, or schema/format failures. Final Vietnamese error mapping must cover the same complete retryable set.
3. Honor valid Retry-After or Google's retryDelay. Use a bounded wait; if requested delay exceeds the safe cap, stop and return a Vietnamese retry-later message instead of retrying early.
4. Preserve diacritics and prove existing panel/Shorts normalizer limits remain enforced.
5. Add exactly one desktop-ai-provider component entry in scripts/ci/component_registry.json. It must select provider/reframe-AI/Shorts-AI/tests and run npm ci, npm test and npm run build under apps/desktop.
6. Add tests for model order, 500/501/503/599, 429 header/metadata/no-hint behavior, timeout, non-retryable key/4xx/refusal/malformed response and normalizer limits.
7. PR Fast + PR Integration must pass on the final exact HEAD/current Main. Integration log must explicitly show desktop-ai-provider and successful npm commands. No live keys/secrets in CI.

## Allowed paths only
- .claims/issue-386-gemini-resilience.md
- apps/desktop/src/features/ai/provider.ts
- apps/desktop/src/features/reframe/ai.ts
- apps/desktop/src/features/shorts/ai.ts
- apps/desktop/test/ai.test.mjs
- scripts/ci/component_registry.json (one new component entry only; sole writer)

## Forbidden
- All other scripts/ci/** and all .github/workflows/**
- package manifests/lockfiles, Tauri IPC/host, other desktop features, engine, supervisor, contracts, migrations, models/media, release/signing/secrets, production publish and unrelated paths
- No test/scanner disabling, force-push, direct Main write, dependency upgrade or second PR.

## Dependencies and ownership
- Hard dependencies: none.
- Coder 2 is sole writer for the one desktop-ai-provider registry entry.
- Coder 1 / Issue #250 owns only docs/production/CI_FULL_REGISTRY_CURRENT_MAIN.md and must not edit scripts/ci/**.
- The former #271 evidence lane was closed at the owner's request after the installed-runtime real-media smoke; do not reopen or duplicate it.
