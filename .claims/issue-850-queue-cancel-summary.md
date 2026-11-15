DUBFLOW_PR_V1
Issue: #850
Lease-Owner: Claude/issue-850-queue-cancel-summary
Lease-Heartbeat: 2026-11-15T09:44:13Z
Tested-Base-SHA: 39128a77e7c93f02b4526f59d4c2088556b3a0f4
Depends-On: none
Conflict-Domains: desktop-queue
Expected-Paths: .claims/issue-850-queue-cancel-summary.md,apps/desktop/src/features/queue/model.ts,apps/desktop/test/queue.test.mjs
Required-CI: PR Fast + PR Integration (desktop selected) + PR Desktop Editor Windows

## Scope
Owner directive 2026-11-15 09:41Z ("NEXT EXECUTION SPRINT", #850 first, small P3 PR may merge after review/CI).
`summarizeQueue` counts a user cancel (FAILED/cancelled, job_status v1) as `cancelled` instead of `failed`.
No contract, schema, persisted-state or UI markup change. `main.ts` is deliberately not touched, because Draft #847 (#616) holds it; a "N đã hủy" label can follow after #847.
