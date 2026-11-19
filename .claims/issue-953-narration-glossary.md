DUBFLOW_PR_V1
Issue: #953
Lease-Owner: Claude/issue-953-narration-glossary
Lease-Heartbeat: 2026-11-19T02:39:07Z
Tested-Base-SHA: 0f9c9d29aaea642db0b87683d3c405ddbaeb9d01
Conflict-Domains: translation-glossary,narration
Expected-Paths:
- .claims/issue-953-narration-glossary.md
- apps/desktop/src/features/ai/glossary.ts
- apps/desktop/src/features/narration/ai.ts
- apps/desktop/src/main.ts
- apps/desktop/test/glossary.test.mjs
- apps/desktop/test/narration.test.mjs
- tests/translation/test_glossary.py
- tests/ai/mock_openai_server.py
- tests/ai/test_mock_openai_server.py
Required-CI: PR Fast + PR Integration (translation, ai-providers, desktop node tests) + PR Desktop Editor Windows
