DUBFLOW_PR_V1
Issue: #592
Lease-Owner: Claude/agent-bilibili-download-report
Lease-Heartbeat: 2026-11-06T17:25:00Z
Tested-Base-SHA: ce7bed36d5cd1f4ab3c3dbcdbb7fd96cd1446c78
Conflict-Domains: source-worker,source-link-report,support-report
Expected-Paths: .claims/issue-592-link-failure-report-context.md,apps/desktop/src-tauri/src/main.rs,engine/dubflow/download/authenticated.py,engine/dubflow/download/authenticated_native.py,engine/dubflow/download/enumeration/worker.py,engine/dubflow/support/report.py,tests/source_adapter/test_authenticated.py,tests/source_queue/test_extractor_diagnostics.py,tests/source_queue/test_worker_diagnostics.py,tests/support/test_report.py
Required-CI: PR Fast + PR Integration + PR Desktop Editor Windows

## Status
Fix in PR #587. Worker failures go to one log per scan (`source-work/worker-errors/<scan>.log`);
a link report reads only its own scan's log. Link status reaches the report as a command-line
argument, so nothing is staged on disk.
