DUBFLOW_PR_V1
Issue: #884
Lease-Owner: Claude/issue-884-proxy-refuse
Lease-Heartbeat: 2026-11-18T03:38:35Z
Tested-Base-SHA: 1d2714b10bc95019e36419ac84af7ba6bb1c845e
Conflict-Domains: source-public-egress,authenticated-helper
Expected-Paths:
- .claims/issue-884-proxy-refuse.md
- apps/desktop/src-tauri/src/source.rs
- contracts/source/README.md
- contracts/source/schema-v1.json
- crates/job-supervisor/app/src/source_server.rs
- engine/dubflow/download/authenticated.py
- engine/dubflow/download/authenticated_native.py
- engine/dubflow/download/bilibili/adapter.py
- engine/dubflow/download/douyin/adapter.py
- engine/dubflow/download/generic/adapter.py
- engine/dubflow/download/generic/sdk.py
- engine/dubflow/download/materializer.py
- engine/dubflow/download/source_adapter.py
- tests/source_adapter/test_authenticated.py
- tests/source_adapter/test_contract.py
- tests/source_bilibili/test_adapter.py
- tests/source_douyin/test_page_detail.py
- tests/source_generic/test_adapter.py
- tests/source_generic/test_native_egress.py
- tests/source_generic/test_sdk.py
- tests/source_materializer/test_public_egress.py
Required-CI: PR Fast + PR Integration (source-generic, source-materializer, source-adapter, bilibili-source, douyin-source and the supervisor selected)
