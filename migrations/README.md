# Durable state migrations

SQLite migrations are append-only and run inside a supervisor-owned transaction
before a job is opened. A migration records its integer version in the
`schema_migrations` table and must leave the prior durable state recoverable on
power loss or process termination.
