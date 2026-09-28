-- Migration 0001. The supervisor creates and records schema_migrations before
-- applying this append-only table set; this file must remain replayable.
-- WAL mode is configured by the supervisor connection before migrations run.

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY NOT NULL,
    source_uri TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'paused', 'recovering', 'succeeded', 'failed', 'cancelled')),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS stages (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'paused', 'recovering', 'succeeded', 'failed', 'cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0 AND attempt <= 255),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1 AND max_attempts <= 255),
    retry_condition TEXT,
    checkpoint_id TEXT,
    checkpoint_hash TEXT,
    started_at_ms INTEGER,
    finished_at_ms INTEGER,
    last_error TEXT,
    last_failure_retryable INTEGER NOT NULL DEFAULT 0 CHECK (last_failure_retryable IN (0, 1)),
    PRIMARY KEY (job_id, stage_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    path TEXT NOT NULL,
    expected_hash TEXT,
    content_hash TEXT,
    size_bytes INTEGER,
    state TEXT NOT NULL CHECK (state IN ('writing', 'validated', 'committed', 'missing', 'quarantined')),
    reusable INTEGER NOT NULL CHECK (reusable IN (0, 1)),
    created_at_ms INTEGER NOT NULL,
    committed_at_ms INTEGER,
    FOREIGN KEY (job_id, stage_id) REFERENCES stages(job_id, stage_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    content_hash TEXT,
    reusable INTEGER NOT NULL CHECK (reusable IN (0, 1)),
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (job_id, stage_id, checkpoint_id),
    FOREIGN KEY (job_id, stage_id) REFERENCES stages(job_id, stage_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stages_status ON stages(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_job_state ON artifacts(job_id, state);
CREATE INDEX IF NOT EXISTS idx_checkpoints_stage ON checkpoints(job_id, stage_id, created_at_ms);
