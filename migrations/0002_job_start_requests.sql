-- Append-only migration 0002. Historical jobs remain unbound; their unknown
-- voice/output/producer options must never be inferred from a later caller.
CREATE TABLE job_start_requests (
    job_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    request_json TEXT NOT NULL CHECK (length(request_json) BETWEEN 1 AND 16384),
    created_at_ms INTEGER NOT NULL
);
