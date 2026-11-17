DUBFLOW_PR_V1
Issue: #911
Lease-Owner: Claude/issue-911-keyless-sandbox
Lease-Heartbeat: 2026-11-17T03:54:44Z
Tested-Base-SHA: 53f4756ae73ed81be1aca0b5ac809e97d6d46c2f
Conflict-Domains: ai-keys
Expected-Paths:
- .claims/issue-911-keyless-sandbox.md
- apps/desktop/src-tauri/src/ai_keys.rs
- apps/desktop/src-tauri/src/main.rs
- apps/desktop/src-tauri/src/single_instance.rs
Required-CI: PR Fast + desktop-editor-windows (cargo test; PR Integration selects no component for this path)
