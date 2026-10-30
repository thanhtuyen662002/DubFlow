DUBFLOW_PR_V1
Issue: #undefined
Lease-Owner: codex/01a11204/issue-174-continuation
Lease-Heartbeat: 2026-10-31T00:38:06+07:00
Tested-Base-SHA: 1091653d9c8a6ed781f40ff94325346c591bd776
Conflict-Domains: supervisor-project-ownership,media-operation-ipc,package-video-artifacts
Expected-Paths: .claims/issue-384-supervisor-media-ownership.md,contracts/project_lock/**,contracts/worker/**,crates/job-supervisor/app/**,crates/job-supervisor/locking/**,crates/job-supervisor/protocol/**,crates/artifact-store/locking/**,apps/desktop/src-tauri/src/package.rs,apps/desktop/src-tauri/src/package/**,apps/desktop/src-tauri/Cargo.toml,crates/job-supervisor/Cargo.lock,tests/project_lock/**,tests/worker_protocol/**,tests/package/**
Required-CI: PR Fast + PR Integration

## Status
ASSIGNED only — Lead-created claim. No coder progress is implied. Coder 1 is the sole writer for the shared supervisor lock/IPC/protocol and package-host API. Coder 2 consumes the API in #382 only after this PR merges.