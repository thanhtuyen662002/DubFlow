# Job status contract v1

Status is derived from durable checkpoint/progress, worker heartbeat sequence,
and scheduler/resource state. Wall-clock age alone never kills work. A worker
that continues heartbeating while inference or rendering is slow remains
`RUNNING`; queued and resource-wait states are separate.

`RETRYING` is bounded by `max_attempts` and requires a materially different
condition fingerprint after the first attempt. Exhausted or repeated identical
conditions become `BLOCKED_NEEDS_ACTION`. `WAITING_EXTERNAL` carries one
actionable message such as login required or source changed. Every response
preserves the last durable checkpoint so restart can reconstruct status.
