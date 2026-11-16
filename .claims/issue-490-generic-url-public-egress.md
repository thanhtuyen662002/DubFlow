DUBFLOW_PR_V1
Issue: #490
Lease-Owner: Claude/issue-490-generic-url-public-egress
Lease-Heartbeat: 2026-11-16T15:40:00Z
Tested-Base-SHA: 95c992782523e8e2e6c902c93aa67d1f262b81c3
Conflict-Domains: source-public-egress,generic-url-extractor,materializer-transport
Expected-Paths:
- .claims/issue-490-generic-url-public-egress.md
- engine/dubflow/download/materializer.py
- engine/dubflow/download/generic/adapter.py
- engine/dubflow/download/generic/sdk.py
- tests/source_materializer/test_public_egress.py
- tests/source_materializer/test_http_transport.py
- tests/source_materializer/test_segmented_download.py
- tests/source_materializer/test_recovery_probe.py
- tests/source_materializer/native_stream_probe.py
- tests/source_generic/test_adapter.py
- tests/source_generic/test_runner.py
- tests/source_generic/test_sdk.py
- tests/source_adapter/qualify_sdk_pages.py
- tests/source_queue/test_worker.py
Required-CI: PR Fast + PR Integration (source-materializer and source-generic selected)

## Scope decision (Owner 2026-11-16: Claude owns 100% of the project; no Lead/Codex)
The Issue's allowed-path list predates the change. Existing tests that talk to their own loopback server now opt in to
loopback explicitly (`EgressPolicy(allow_loopback=True)`), and tests that use `.test` hosts inject a fake public DNS
answer. No production path outside the three engine files changes.

## Residual risk, stated
The yt-dlp helper/SDK opens its own sockets. It is refused before launch when the page host is (or resolves to) a
non-public address, but its own connections are not peer-bound, so a DNS rebinding window remains for the page fetch
only. The media it returns is fetched through the peer-bound `UrllibHttpTransport`. Failing closed for every generic
page would disable all generic (yt-dlp) sources for users, so this PR does not do that; a follow-up issue tracks
binding the helper through an app-owned validating proxy.
