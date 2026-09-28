# Job liveness watchdog

This crate is advisory recovery logic. It consumes a durable snapshot, worker
heartbeat signal, scheduler state, and optional external wait; it returns an
explicit status, user message, checkpoint-preserving retry action, and resource
release decision. It does not write SQLite and it never uses wall-clock time as
the sole stall criterion.
