# Supervisor project ownership lock

The lock is an OS-visible create-new metadata file. It covers the SQLite
project database, artifact namespace, and temp namespace as one ownership unit.
The process probe is injected so production can validate PID plus process-start
token using the platform API while tests use deterministic fake processes.
