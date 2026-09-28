# Durable job state boundary

Issue #5 owns this namespace. The Rust supervisor is the only component that
opens and mutates the durable SQLite database. Worker processes receive the
versioned protocol from `crates/job-supervisor/protocol` and return validated
events; they never receive a database handle or a write path.

The state boundary will provide:

- WAL-backed jobs, stages, artifacts, checkpoints and persisted retry budget;
- explicit state transitions and transactional artifact completion;
- recovery scans for files written before a crash and DB rows whose files
  disappeared;
- quarantine for partial/unverified output; and
- deterministic migration/version checks before a job can resume.

The migration sequence is append-only under `/migrations`. Output, cache and
source roots remain external to the control database so loss of one volume
cannot move or corrupt the sole durable job record.
