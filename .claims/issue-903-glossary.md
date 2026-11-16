DUBFLOW_PR_V1
Issue: #903
Lease-Owner: Claude/issue-903-glossary
Lease-Heartbeat: 2026-11-16T18:27:31Z
Tested-Base-SHA: b14a8982270e5aa6e582afd3ade5854ba25a2974
Conflict-Domains: translation-glossary
Expected-Paths:
- .claims/issue-903-glossary.md
- engine/dubflow/translation/glossary.py
- engine/dubflow/translation/context_ai.py
- engine/dubflow/worker/production_job.py
- tests/translation/test_glossary.py
- apps/desktop/index.html
- apps/desktop/package.json
- apps/desktop/src/styles.css
- apps/desktop/src/main.ts
- apps/desktop/src/shell/preview_invoke.ts
- apps/desktop/src/features/ai/glossary.ts
- apps/desktop/src/features/ai/glossary_view.ts
- apps/desktop/src/features/queue/model.ts
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/test/glossary.test.mjs
Required-CI: PR Fast + PR Integration (translation, worker, desktop) + desktop editor Windows (cargo test)
