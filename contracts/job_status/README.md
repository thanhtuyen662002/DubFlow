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

The one-shot local-file supervisor reads checkpoint, actual worker-start count,
maximum attempts and retry condition from the durable stage before its first
status write and after completion/failure. A completed replay preserves these
fields; a retry scheduled but not started does not count as another attempt.
The existing status JSON is a projection, never authority for this history.

For this pipeline, `completed_units`/`total_units` use the worker's overall
fraction on a paired 1000-unit scale. Per-stage cue counts stay in raw progress
events and cannot replace that denominator. Running progress is capped at 999;
1000/1000 follows validated artifact commit and durable success. A fresh
invocation starts its projected event/heartbeat sequence at zero; it is not a
cumulative durable history counter. Reconciliation does not increment it.
