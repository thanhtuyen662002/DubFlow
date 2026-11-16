DUBFLOW_PR_V1
Issue: #890
Lease-Owner: Claude/issue-890-status-and-final-qc
Lease-Heartbeat: 2026-11-16T17:15:43Z
Tested-Base-SHA: c29065ba06e5433a723cd90a06058aafa45bfb30
Conflict-Domains: run-status-liveness,final-qc-state
Expected-Paths:
- .claims/issue-890-status-and-final-qc.md
- engine/dubflow/support/engine_lock.py
- engine/dubflow/compilation/__main__.py
- engine/dubflow/product/__main__.py
- engine/dubflow/mastering/final_qc.py
- apps/desktop/src-tauri/src/engine_run.rs
- apps/desktop/src-tauri/src/compilation.rs
- apps/desktop/src-tauri/src/compilation_raw.rs
- apps/desktop/src-tauri/src/product.rs
- tests/support/test_engine_lock.py
- tests/mastering/test_final_qc.py
Required-CI: PR Fast + PR Integration (mastering, compilation-video, support-report, product) + desktop editor Windows (cargo test)
