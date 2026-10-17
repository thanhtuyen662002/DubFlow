# Durable state migrations

SQLite migrations are append-only and run inside a supervisor-owned transaction
before a job is opened. A migration records its integer version in the
`schema_migrations` table and must leave the prior durable state recoverable on
power loss or process termination.

Migration `0003_review_graph.sql` explicitly enables an optional Review feature
under `review_schema_migrations` version1. It leaves core schema version2 and
existing job/stage/artifact columns unchanged so earlier readers retain ordinary
job access. Only the supervisor mutation path enables it; observation does not.
See ADR-0030 for feature compatibility, admission and recovery requirements.
