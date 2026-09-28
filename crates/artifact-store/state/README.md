# Artifact state boundary

Artifact metadata is committed by the Rust job supervisor in the durable
state database. This namespace documents the artifact-store side of that
boundary: file writes and validation happen before the supervisor transaction;
the worker never writes SQLite directly. A later artifact implementation may
move file-I/O helpers here without changing the job-state schema or protocol.
