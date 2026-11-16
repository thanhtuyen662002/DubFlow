DUBFLOW_PR_V1
Issue: #884
Lease-Owner: Claude/issue-884-ytdlp-egress
Lease-Heartbeat: 2026-11-16T16:21:15Z
Tested-Base-SHA: b69261e864af075c41cac657c4b640ab1f23d48c
Conflict-Domains: source-public-egress,generic-url-extractor,authenticated-helper
Expected-Paths:
- .claims/issue-884-ytdlp-egress.md
- engine/dubflow/download/authenticated_native.py
- engine/dubflow/download/authenticated.py
- engine/dubflow/download/materializer.py
- tests/source_generic/test_native_egress.py
- tests/source_materializer/test_public_egress.py
- tests/source_generic/requirements-sdk-ci.txt
- tests/source_generic/run_sdk_egress.py
- scripts/ci/component_registry.json
Required-CI: PR Fast + PR Integration (source-generic incl. pinned-SDK lane, source-materializer, source-adapter, bilibili-source, douyin-source selected)
