//! Supervisor-owned durable job state and crash recovery.
//!
//! This crate is the only owner of the SQLite connection. Workers communicate
//! through the versioned protocol and never receive a database handle.

use rusqlite::{params, Connection, OptionalExtension};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

const MIGRATION_VERSION: i64 = 1;
const MAX_ID_CHARS: usize = 128;
const MAX_KIND_CHARS: usize = 128;
const MAX_ERROR_CHARS: usize = 4096;
const MAX_RETRY_ATTEMPTS: u8 = 255;
const MIGRATION_SQL: &str = include_str!("../../../../migrations/0001_job_state.sql");

#[derive(Debug)]
pub enum StateError {
    Sqlite(rusqlite::Error),
    Io(io::Error),
    InvalidInput(String),
    InvalidTransition { entity: &'static str, from: String, to: String },
    NotFound { entity: &'static str, id: String },
    UnsupportedMigration(i64),
    HashMismatch { artifact_id: String, expected: String, actual: String },
    MissingArtifact { artifact_id: String, path: String },
}

impl fmt::Display for StateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(f, "SQLite error: {error}"),
            Self::Io(error) => write!(f, "filesystem error: {error}"),
            Self::InvalidInput(detail) => write!(f, "invalid state input: {detail}"),
            Self::InvalidTransition { entity, from, to } => write!(f, "invalid {entity} transition {from} -> {to}"),
            Self::NotFound { entity, id } => write!(f, "{entity} not found: {id}"),
            Self::UnsupportedMigration(version) => write!(f, "unsupported state migration version {version}"),
            Self::HashMismatch { artifact_id, expected, actual } => write!(f, "artifact {artifact_id} hash mismatch: expected {expected}, got {actual}"),
            Self::MissingArtifact { artifact_id, path } => write!(f, "artifact {artifact_id} is missing at {path}"),
        }
    }
}

impl std::error::Error for StateError {}
impl From<rusqlite::Error> for StateError { fn from(error: rusqlite::Error) -> Self { Self::Sqlite(error) } }
impl From<io::Error> for StateError { fn from(error: io::Error) -> Self { Self::Io(error) } }
pub type Result<T> = std::result::Result<T, StateError>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JobStatus { Queued, Running, Paused, Recovering, Succeeded, Failed, Cancelled }

impl JobStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued", Self::Running => "running", Self::Paused => "paused",
            Self::Recovering => "recovering", Self::Succeeded => "succeeded",
            Self::Failed => "failed", Self::Cancelled => "cancelled",
        }
    }
    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "queued" => Ok(Self::Queued), "running" => Ok(Self::Running),
            "paused" => Ok(Self::Paused), "recovering" => Ok(Self::Recovering),
            "succeeded" => Ok(Self::Succeeded), "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            _ => Err(StateError::InvalidInput(format!("unknown job status {value:?}"))),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StageStatus { Pending, Running, Paused, Recovering, Succeeded, Failed, Cancelled }

impl StageStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending", Self::Running => "running", Self::Paused => "paused",
            Self::Recovering => "recovering", Self::Succeeded => "succeeded",
            Self::Failed => "failed", Self::Cancelled => "cancelled",
        }
    }
    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "pending" => Ok(Self::Pending), "running" => Ok(Self::Running),
            "paused" => Ok(Self::Paused), "recovering" => Ok(Self::Recovering),
            "succeeded" => Ok(Self::Succeeded), "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            _ => Err(StateError::InvalidInput(format!("unknown stage status {value:?}"))),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArtifactState { Writing, Validated, Committed, Missing, Quarantined }

impl ArtifactState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Writing => "writing", Self::Validated => "validated",
            Self::Committed => "committed", Self::Missing => "missing",
            Self::Quarantined => "quarantined",
        }
    }
    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "writing" => Ok(Self::Writing), "validated" => Ok(Self::Validated),
            "committed" => Ok(Self::Committed), "missing" => Ok(Self::Missing),
            "quarantined" => Ok(Self::Quarantined),
            _ => Err(StateError::InvalidInput(format!("unknown artifact state {value:?}"))),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArtifactRecord {
    pub artifact_id: String,
    pub job_id: String,
    pub stage_id: String,
    pub path: String,
    pub expected_hash: Option<String>,
    pub content_hash: Option<String>,
    pub size_bytes: Option<u64>,
    pub state: ArtifactState,
    pub reusable: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RecoveryEvent {
    RecoveredArtifact { artifact_id: String, hash: String },
    QuarantinedArtifact { artifact_id: String, path: String },
    MissingArtifact { artifact_id: String, path: String, was_committed: bool },
    OrphanArtifact { path: String, quarantine_path: String },
}

/// The only durable-state owner. It exposes no connection or SQL escape hatch.
pub struct DurableStore { connection: Connection }

impl DurableStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if path != Path::new(":memory:") {
            if let Some(parent) = path.parent() {
                if !parent.as_os_str().is_empty() { fs::create_dir_all(parent)?; }
            }
        }
        Self::from_connection(Connection::open(path)?)
    }

    pub fn open_in_memory() -> Result<Self> { Self::from_connection(Connection::open_in_memory()?) }

    fn from_connection(connection: Connection) -> Result<Self> {
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.execute_batch("PRAGMA foreign_keys = ON; PRAGMA synchronous = FULL; PRAGMA journal_mode = WAL;")?;
        let store = Self { connection };
        store.apply_migrations()?;
        Ok(store)
    }

    fn apply_migrations(&self) -> Result<()> {
        self.connection.execute_batch("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY NOT NULL, applied_at_ms INTEGER NOT NULL);")?;
        let current: Option<i64> = self.connection.query_row("SELECT MAX(version) FROM schema_migrations", [], |row| row.get(0))?;
        let current = current.unwrap_or(0);
        if current > MIGRATION_VERSION { return Err(StateError::UnsupportedMigration(current)); }
        if current < MIGRATION_VERSION {
            let tx = self.connection.unchecked_transaction()?;
            tx.execute_batch(MIGRATION_SQL)?;
            tx.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?1, 0)", params![MIGRATION_VERSION])?;
            tx.commit()?;
        }
        Ok(())
    }

    pub fn schema_version(&self) -> Result<i64> {
        Ok(self.connection.query_row("SELECT MAX(version) FROM schema_migrations", [], |row| row.get::<_, Option<i64>>(0))?.unwrap_or(0))
    }

    pub fn create_job(&self, job_id: &str, source_uri: &str, now_ms: u64) -> Result<()> {
        validate_id(job_id, "job_id", MAX_ID_CHARS)?;
        validate_non_empty(source_uri, "source_uri", 4096)?;
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute("INSERT INTO jobs(job_id, source_uri, status, created_at_ms, updated_at_ms) VALUES (?1, ?2, 'queued', ?3, ?3)", params![job_id, source_uri, now])?;
        Ok(())
    }

    pub fn create_stage(&self, job_id: &str, stage_id: &str, kind: &str, max_attempts: u8) -> Result<()> {
        validate_id(job_id, "job_id", MAX_ID_CHARS)?;
        validate_id(stage_id, "stage_id", MAX_ID_CHARS)?;
        validate_non_empty(kind, "stage kind", MAX_KIND_CHARS)?;
        if max_attempts == 0 { return Err(StateError::InvalidInput("max_attempts must be positive".into())); }
        self.require_job(job_id)?;
        self.connection.execute("INSERT INTO stages(job_id, stage_id, kind, status, max_attempts) VALUES (?1, ?2, ?3, 'pending', ?4)", params![job_id, stage_id, kind, i64::from(max_attempts)])?;
        Ok(())
    }

    pub fn job_status(&self, job_id: &str) -> Result<JobStatus> {
        self.connection.query_row("SELECT status FROM jobs WHERE job_id = ?1", params![job_id], |row| row.get::<_, String>(0))
            .optional()?.ok_or_else(|| StateError::NotFound { entity: "job", id: job_id.into() }).and_then(JobStatus::parse)
    }

    pub fn stage_status(&self, job_id: &str, stage_id: &str) -> Result<StageStatus> {
        self.connection.query_row("SELECT status FROM stages WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id], |row| row.get::<_, String>(0))
            .optional()?.ok_or_else(|| StateError::NotFound { entity: "stage", id: format!("{job_id}/{stage_id}") }).and_then(StageStatus::parse)
    }

    pub fn artifact(&self, artifact_id: &str) -> Result<ArtifactRecord> {
        let row: Option<(String, String, String, String, Option<String>, Option<String>, Option<i64>, String, i64)> =
            self.connection.query_row(
                "SELECT artifact_id, job_id, stage_id, path, expected_hash, content_hash, size_bytes, state, reusable FROM artifacts WHERE artifact_id = ?1",
                params![artifact_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?)),
            ).optional()?;
        let row = row.ok_or_else(|| StateError::NotFound { entity: "artifact", id: artifact_id.into() })?;
        Ok(ArtifactRecord {
            artifact_id: row.0, job_id: row.1, stage_id: row.2, path: row.3,
            expected_hash: row.4, content_hash: row.5, size_bytes: row.6.map(|value| value as u64),
            state: ArtifactState::parse(row.7)?, reusable: row.8 != 0,
        })
    }

    pub fn start_job(&self, job_id: &str, now_ms: u64) -> Result<()> {
        let current = self.job_status(job_id)?;
        transition_job(current, JobStatus::Running)?;
        self.update_job_status(job_id, JobStatus::Running, now_ms, None)
    }

    pub fn pause_job(&self, job_id: &str, reason: &str, now_ms: u64) -> Result<()> {
        validate_non_empty(reason, "pause reason", MAX_ERROR_CHARS)?;
        let current = self.job_status(job_id)?;
        transition_job(current, JobStatus::Paused)?;
        self.update_job_status(job_id, JobStatus::Paused, now_ms, Some(reason))
    }

    pub fn complete_job(&self, job_id: &str, now_ms: u64) -> Result<()> {
        let now = to_i64(now_ms, "now_ms")?;
        let tx = self.connection.unchecked_transaction()?;
        let current: String = tx.query_row("SELECT status FROM jobs WHERE job_id = ?1", params![job_id], |row| row.get(0)).optional()?
            .ok_or_else(|| StateError::NotFound { entity: "job", id: job_id.into() })?;
        transition_job(JobStatus::parse(current)?, JobStatus::Succeeded)?;
        let unfinished: Option<String> = tx.query_row(
            "SELECT stage_id FROM stages WHERE job_id = ?1 AND status <> 'succeeded' LIMIT 1",
            params![job_id],
            |row| row.get(0),
        ).optional()?;
        if let Some(stage_id) = unfinished {
            return Err(StateError::InvalidTransition { entity: "stage", from: "incomplete".into(), to: format!("job {job_id} succeeded ({stage_id})") });
        }
        tx.execute("UPDATE jobs SET status = 'succeeded', updated_at_ms = ?2, last_error = NULL WHERE job_id = ?1", params![job_id, now])?;
        tx.commit()?;
        Ok(())
    }

    pub fn fail_job(&self, job_id: &str, reason: &str, now_ms: u64) -> Result<()> {
        validate_non_empty(reason, "failure reason", MAX_ERROR_CHARS)?;
        let current = self.job_status(job_id)?;
        transition_job(current, JobStatus::Failed)?;
        self.update_job_status(job_id, JobStatus::Failed, now_ms, Some(reason))
    }

    pub fn cancel_job(&self, job_id: &str, reason: &str, now_ms: u64) -> Result<()> {
        validate_non_empty(reason, "cancel reason", MAX_ERROR_CHARS)?;
        let now = to_i64(now_ms, "now_ms")?;
        let tx = self.connection.unchecked_transaction()?;
        let current: String = tx.query_row("SELECT status FROM jobs WHERE job_id = ?1", params![job_id], |row| row.get(0)).optional()?
            .ok_or_else(|| StateError::NotFound { entity: "job", id: job_id.into() })?;
        transition_job(JobStatus::parse(current)?, JobStatus::Cancelled)?;
        tx.execute("UPDATE jobs SET status = 'cancelled', updated_at_ms = ?2, last_error = ?3 WHERE job_id = ?1", params![job_id, now, reason])?;
        tx.execute("UPDATE stages SET status = 'cancelled', finished_at_ms = ?2, last_error = ?3 WHERE job_id = ?1 AND status IN ('pending', 'running', 'paused', 'recovering')", params![job_id, now, reason])?;
        tx.commit()?;
        Ok(())
    }

    pub fn start_stage(&self, job_id: &str, stage_id: &str, now_ms: u64) -> Result<u8> {
        let now = to_i64(now_ms, "now_ms")?;
        let tx = self.connection.unchecked_transaction()?;
        let job_status: String = tx.query_row("SELECT status FROM jobs WHERE job_id = ?1", params![job_id], |row| row.get(0)).optional()?
            .ok_or_else(|| StateError::NotFound { entity: "job", id: job_id.into() })?;
        let job_status = JobStatus::parse(job_status)?;
        if !matches!(job_status, JobStatus::Running | JobStatus::Recovering) {
            return Err(StateError::InvalidTransition { entity: "job", from: job_status.as_str().into(), to: JobStatus::Running.as_str().into() });
        }
        let row: Option<(String, i64, i64)> = tx.query_row("SELECT status, attempt, max_attempts FROM stages WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?))).optional()?;
        let row = row.ok_or_else(|| StateError::NotFound { entity: "stage", id: format!("{job_id}/{stage_id}") })?;
        let current = StageStatus::parse(row.0)?;
        if !matches!(current, StageStatus::Pending | StageStatus::Recovering | StageStatus::Paused) {
            return Err(StateError::InvalidTransition { entity: "stage", from: current.as_str().into(), to: StageStatus::Running.as_str().into() });
        }
        let attempt = row.1 + 1;
        if attempt > row.2 || attempt > i64::from(MAX_RETRY_ATTEMPTS) { return Err(StateError::InvalidInput("stage retry budget is exhausted".into())); }
        tx.execute("UPDATE stages SET status = 'running', attempt = ?3, started_at_ms = ?4, finished_at_ms = NULL, last_error = NULL WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id, attempt, now])?;
        tx.commit()?;
        Ok(attempt as u8)
    }

    pub fn complete_stage(&self, job_id: &str, stage_id: &str, now_ms: u64) -> Result<()> {
        let current = self.stage_status(job_id, stage_id)?;
        if current != StageStatus::Running {
            return Err(StateError::InvalidTransition { entity: "stage", from: current.as_str().into(), to: StageStatus::Succeeded.as_str().into() });
        }
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute("UPDATE stages SET status = 'succeeded', finished_at_ms = ?3 WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id, now])?;
        Ok(())
    }

    pub fn fail_stage(&self, job_id: &str, stage_id: &str, reason: &str, retryable: bool, now_ms: u64) -> Result<()> {
        validate_non_empty(reason, "stage failure", MAX_ERROR_CHARS)?;
        let current = self.stage_status(job_id, stage_id)?;
        if current != StageStatus::Running {
            return Err(StateError::InvalidTransition { entity: "stage", from: current.as_str().into(), to: StageStatus::Failed.as_str().into() });
        }
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute("UPDATE stages SET status = 'failed', finished_at_ms = ?3, last_error = ?4, last_failure_retryable = ?5 WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id, now, reason, if retryable { 1 } else { 0 }])?;
        Ok(())
    }

    pub fn retry_stage(&self, job_id: &str, stage_id: &str, condition: &str, now_ms: u64) -> Result<u8> {
        validate_non_empty(condition, "retry condition", MAX_ERROR_CHARS)?;
        let tx = self.connection.unchecked_transaction()?;
        let row: Option<(String, i64, i64, Option<String>, i64)> = tx.query_row(
            "SELECT status, attempt, max_attempts, retry_condition, last_failure_retryable FROM stages WHERE job_id = ?1 AND stage_id = ?2",
            params![job_id, stage_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
        ).optional()?;
        let row = row.ok_or_else(|| StateError::NotFound { entity: "stage", id: format!("{job_id}/{stage_id}") })?;
        let current = StageStatus::parse(row.0)?;
        if current != StageStatus::Failed || row.4 == 0 {
            return Err(StateError::InvalidTransition { entity: "stage", from: current.as_str().into(), to: "retryable-failed".into() });
        }
        if row.3.as_deref() == Some(condition) {
            return Err(StateError::InvalidInput("retry condition must materially change".into()));
        }
        // `attempt` counts actual worker starts.  The retry transition only
        // records the new condition; `start_stage` performs the single
        // increment when the replacement attempt really starts.
        let next_attempt = row.1 + 1;
        if next_attempt > row.2 || next_attempt > i64::from(MAX_RETRY_ATTEMPTS) { return Err(StateError::InvalidInput("stage retry budget is exhausted".into())); }
        let now = to_i64(now_ms, "now_ms")?;
        tx.execute("UPDATE stages SET status = 'recovering', retry_condition = ?3, started_at_ms = NULL, finished_at_ms = NULL, last_error = NULL, last_failure_retryable = 0 WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id, condition])?;
        tx.execute("UPDATE jobs SET status = CASE WHEN status = 'failed' THEN 'recovering' ELSE status END, updated_at_ms = ?2 WHERE job_id = ?1", params![job_id, now])?;
        tx.commit()?;
        Ok(next_attempt as u8)
    }

    pub fn record_checkpoint(&self, job_id: &str, stage_id: &str, checkpoint_id: &str, content_hash: Option<&str>, reusable: bool, now_ms: u64) -> Result<()> {
        validate_id(job_id, "job_id", MAX_ID_CHARS)?;
        validate_id(stage_id, "stage_id", MAX_ID_CHARS)?;
        validate_id(checkpoint_id, "checkpoint_id", MAX_ID_CHARS)?;
        validate_optional_hash(content_hash)?;
        let now = to_i64(now_ms, "now_ms")?;
        let tx = self.connection.unchecked_transaction()?;
        let stage_status: String = tx.query_row("SELECT status FROM stages WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id], |row| row.get(0)).optional()?
            .ok_or_else(|| StateError::NotFound { entity: "stage", id: format!("{job_id}/{stage_id}") })?;
        let stage_status = StageStatus::parse(stage_status)?;
        if !matches!(stage_status, StageStatus::Running | StageStatus::Recovering | StageStatus::Paused) {
            return Err(StateError::InvalidTransition { entity: "stage", from: stage_status.as_str().into(), to: "checkpoint-recorded".into() });
        }
        tx.execute("INSERT OR REPLACE INTO checkpoints(job_id, stage_id, checkpoint_id, content_hash, reusable, created_at_ms) VALUES (?1, ?2, ?3, ?4, ?5, ?6)", params![job_id, stage_id, checkpoint_id, content_hash, if reusable { 1 } else { 0 }, now])?;
        tx.execute("UPDATE stages SET checkpoint_id = ?3, checkpoint_hash = ?4 WHERE job_id = ?1 AND stage_id = ?2", params![job_id, stage_id, checkpoint_id, content_hash])?;
        tx.commit()?;
        Ok(())
    }

    pub fn record_artifact_written(&self, artifact_id: &str, job_id: &str, stage_id: &str, path: &Path, expected_hash: Option<&str>, reusable: bool, now_ms: u64) -> Result<()> {
        validate_id(artifact_id, "artifact_id", MAX_ID_CHARS)?;
        validate_id(job_id, "job_id", MAX_ID_CHARS)?;
        validate_id(stage_id, "stage_id", MAX_ID_CHARS)?;
        let path_text = path.to_str().ok_or_else(|| StateError::InvalidInput("artifact path is not UTF-8".into()))?;
        validate_non_empty(path_text, "artifact path", 32768)?;
        validate_optional_hash(expected_hash)?;
        let stage_status = self.stage_status(job_id, stage_id)?;
        if !matches!(stage_status, StageStatus::Running | StageStatus::Recovering) {
            return Err(StateError::InvalidTransition { entity: "stage", from: stage_status.as_str().into(), to: "artifact-written".into() });
        }
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute("INSERT INTO artifacts(artifact_id, job_id, stage_id, path, expected_hash, state, reusable, created_at_ms) VALUES (?1, ?2, ?3, ?4, ?5, 'writing', ?6, ?7)", params![artifact_id, job_id, stage_id, path_text, expected_hash, if reusable { 1 } else { 0 }, now])?;
        Ok(())
    }

    pub fn commit_artifact(&self, artifact_id: &str, now_ms: u64) -> Result<String> {
        let record = self.artifact(artifact_id)?;
        if !matches!(record.state, ArtifactState::Writing | ArtifactState::Validated) {
            return Err(StateError::InvalidTransition { entity: "artifact", from: record.state.as_str().into(), to: ArtifactState::Committed.as_str().into() });
        }
        let stage_state = self.stage_status(&record.job_id, &record.stage_id)?;
        if !matches!(stage_state, StageStatus::Running | StageStatus::Recovering) {
            return Err(StateError::InvalidTransition {
                entity: "stage",
                from: stage_state.as_str().into(),
                to: StageStatus::Succeeded.as_str().into(),
            });
        }
        let path = PathBuf::from(&record.path);
        let (hash, size) = match hash_file(&path) {
            Ok(value) => value,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                self.mark_artifact_missing(artifact_id)?;
                self.mark_stage_recovering(&record.job_id, artifact_id, now_ms)?;
                return Err(StateError::MissingArtifact { artifact_id: artifact_id.into(), path: record.path });
            }
            Err(error) => return Err(StateError::Io(error)),
        };
        if let Some(expected) = &record.expected_hash {
            if expected != &hash {
                let quarantine = quarantine_path(&path, artifact_id);
                fs::rename(&path, &quarantine)?;
                let now = to_i64(now_ms, "now_ms")?;
                let tx = self.connection.unchecked_transaction()?;
                tx.execute("UPDATE artifacts SET state = 'quarantined' WHERE artifact_id = ?1", params![artifact_id])?;
                tx.execute("UPDATE stages SET status = CASE WHEN status = 'cancelled' THEN status ELSE 'recovering' END, finished_at_ms = NULL, last_error = 'ARTIFACT_HASH_MISMATCH' WHERE job_id = ?1 AND stage_id = ?2", params![record.job_id, record.stage_id])?;
                tx.execute("UPDATE jobs SET status = CASE WHEN status = 'cancelled' THEN status ELSE 'recovering' END, updated_at_ms = ?2, last_error = 'ARTIFACT_HASH_MISMATCH' WHERE job_id = ?1", params![record.job_id, now])?;
                tx.commit()?;
                return Err(StateError::HashMismatch { artifact_id: artifact_id.into(), expected: expected.clone(), actual: hash });
            }
        }
        self.commit_artifact_row(artifact_id, &record.job_id, &record.stage_id, &hash, size, now_ms)?;
        Ok(hash)
    }

    fn commit_artifact_row(&self, artifact_id: &str, job_id: &str, stage_id: &str, hash: &str, size: u64, now_ms: u64) -> Result<()> {
        let now = to_i64(now_ms, "now_ms")?;
        let size = i64::try_from(size).map_err(|_| StateError::InvalidInput("artifact size exceeds i64".into()))?;
        let tx = self.connection.unchecked_transaction()?;
        if tx.execute(
            "UPDATE artifacts SET state = 'committed', content_hash = ?4, size_bytes = ?5, committed_at_ms = ?6 WHERE artifact_id = ?1 AND job_id = ?2 AND stage_id = ?3",
            params![artifact_id, job_id, stage_id, hash, size, now],
        )? == 0 {
            return Err(StateError::NotFound { entity: "artifact", id: artifact_id.into() });
        }
        // The artifact row and the stage transition share one SQLite
        // transaction. A stage is complete only when no sibling artifact
        // remains in a non-committed state.
        let incomplete: i64 = tx.query_row(
            "SELECT COUNT(*) FROM artifacts WHERE job_id = ?1 AND stage_id = ?2 AND state <> 'committed'",
            params![job_id, stage_id],
            |row| row.get(0),
        )?;
        if incomplete == 0 {
            tx.execute("UPDATE stages SET status = 'succeeded', finished_at_ms = ?3, last_error = NULL WHERE job_id = ?1 AND stage_id = ?2 AND status IN ('running', 'recovering')", params![job_id, stage_id, now])?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn reconcile_job(&self, job_id: &str, now_ms: u64) -> Result<Vec<RecoveryEvent>> {
        self.require_job(job_id)?;
        let records: Vec<(String, String, String, Option<String>, Option<String>, String)> = {
            let mut statement = self.connection.prepare("SELECT artifact_id, path, stage_id, expected_hash, content_hash, state FROM artifacts WHERE job_id = ?1")?;
            let rows = statement.query_map(params![job_id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?)))?;
            rows.collect::<std::result::Result<Vec<_>, _>>()?
        };
        let mut events = Vec::new();
        for (artifact_id, path_text, stage_id, expected, content_hash, state_text) in records {
            let state = ArtifactState::parse(state_text)?;
            let path = PathBuf::from(&path_text);

            if matches!(state, ArtifactState::Committed) {
                if !path.is_file() {
                    self.mark_artifact_missing(&artifact_id)?;
                    self.mark_stage_recovering(job_id, &artifact_id, now_ms)?;
                    events.push(RecoveryEvent::MissingArtifact { artifact_id, path: path_text, was_committed: true });
                    continue;
                }
                let (hash, size) = hash_file(&path)?;
                let reference = expected.as_deref().or(content_hash.as_deref());
                if reference.map(|value| value != hash.as_str()).unwrap_or(true) {
                    let quarantine = quarantine_path(&path, &artifact_id);
                    fs::rename(&path, &quarantine)?;
                    self.connection.execute("UPDATE artifacts SET state = 'quarantined' WHERE artifact_id = ?1", params![artifact_id])?;
                    self.mark_stage_recovering(job_id, &artifact_id, now_ms)?;
                    events.push(RecoveryEvent::QuarantinedArtifact { artifact_id, path: quarantine.to_string_lossy().into_owned() });
                } else {
                    // Rebind the verified size/hash and finish a stage whose
                    // artifact commit survived a process restart.
                    self.commit_artifact_row(&artifact_id, job_id, &stage_id, &hash, size, now_ms)?;
                }
                continue;
            }

            if !matches!(state, ArtifactState::Writing | ArtifactState::Validated) { continue; }
            if !path.is_file() {
                self.mark_artifact_missing(&artifact_id)?;
                self.mark_stage_recovering(job_id, &artifact_id, now_ms)?;
                events.push(RecoveryEvent::MissingArtifact { artifact_id, path: path_text, was_committed: false });
                continue;
            }
            let (hash, size) = hash_file(&path)?;
            if expected.as_deref().map(|value| value == hash.as_str()).unwrap_or(true) {
                self.commit_artifact_row(&artifact_id, job_id, &stage_id, &hash, size, now_ms)?;
                events.push(RecoveryEvent::RecoveredArtifact { artifact_id, hash });
            } else {
                let quarantine = quarantine_path(&path, &artifact_id);
                fs::rename(&path, &quarantine)?;
                self.connection.execute("UPDATE artifacts SET state = 'quarantined' WHERE artifact_id = ?1", params![artifact_id])?;
                self.mark_stage_recovering(job_id, &artifact_id, now_ms)?;
                events.push(RecoveryEvent::QuarantinedArtifact { artifact_id, path: quarantine.to_string_lossy().into_owned() });
            }
        }
        Ok(events)
    }

    /// Quarantine unregistered temporary outputs in an artifact staging root.
    ///
    /// A process can die after closing a temp file but before its artifact row
    /// commits. The durable database cannot name that row, so recovery uses
    /// the staging convention (`.partial`, `.tmp`, or `.writing`) and moves
    /// the file out of the publishable namespace. Final-looking unregistered
    /// files are left for an explicit manifest-driven import.
    pub fn scan_orphan_artifacts(&self, root: &Path) -> Result<Vec<RecoveryEvent>> {
        let referenced: HashSet<PathBuf> = {
            let mut statement = self.connection.prepare("SELECT path FROM artifacts")?;
            let rows = statement.query_map([], |row| row.get::<_, String>(0).map(PathBuf::from))?;
            rows.collect::<std::result::Result<HashSet<_>, _>>()?
        };
        let mut events = Vec::new();
        for entry in fs::read_dir(root)? {
            let entry = entry?;
            let path = entry.path();
            if !path.is_file() || referenced.contains(&path) { continue; }
            let name = path.file_name().and_then(|value| value.to_str()).unwrap_or_default();
            if !(name.ends_with(".partial") || name.ends_with(".tmp") || name.ends_with(".writing")) { continue; }
            let quarantine = path.with_file_name(format!("{name}.orphan.quarantine"));
            fs::rename(&path, &quarantine)?;
            events.push(RecoveryEvent::OrphanArtifact { path: path.to_string_lossy().into_owned(), quarantine_path: quarantine.to_string_lossy().into_owned() });
        }
        Ok(events)
    }

    pub fn recover_after_restart(&self, now_ms: u64) -> Result<u64> {
        let now = to_i64(now_ms, "now_ms")?;
        let tx = self.connection.unchecked_transaction()?;
        let stages = tx.execute("UPDATE stages SET status = 'recovering', last_error = 'PROCESS_RESTART' WHERE status = 'running'", [])?;
        tx.execute("UPDATE jobs SET status = 'recovering', updated_at_ms = ?1, last_error = 'PROCESS_RESTART' WHERE status = 'running'", params![now])?;
        tx.execute("UPDATE jobs SET status = 'recovering', updated_at_ms = ?1, last_error = 'PROCESS_RESTART' WHERE status NOT IN ('recovering', 'cancelled') AND job_id IN (SELECT DISTINCT job_id FROM stages WHERE status = 'recovering' AND last_error = 'PROCESS_RESTART')", params![now])?;
        tx.commit()?;
        Ok(stages as u64)
    }

    fn require_job(&self, job_id: &str) -> Result<()> {
        let found: Option<i64> = self.connection.query_row("SELECT 1 FROM jobs WHERE job_id = ?1", params![job_id], |row| row.get(0)).optional()?;
        if found.is_none() { return Err(StateError::NotFound { entity: "job", id: job_id.into() }); }
        Ok(())
    }
    fn update_job_status(&self, job_id: &str, status: JobStatus, now_ms: u64, error: Option<&str>) -> Result<()> {
        let now = to_i64(now_ms, "now_ms")?;
        if self.connection.execute("UPDATE jobs SET status = ?2, updated_at_ms = ?3, last_error = ?4 WHERE job_id = ?1", params![job_id, status.as_str(), now, error])? == 0 {
            return Err(StateError::NotFound { entity: "job", id: job_id.into() });
        }
        Ok(())
    }
    fn mark_artifact_missing(&self, artifact_id: &str) -> Result<()> {
        self.connection.execute("UPDATE artifacts SET state = 'missing' WHERE artifact_id = ?1", params![artifact_id])?;
        Ok(())
    }
    fn mark_stage_recovering(&self, job_id: &str, artifact_id: &str, now_ms: u64) -> Result<()> {
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute("UPDATE stages SET status = CASE WHEN status = 'cancelled' THEN status ELSE 'recovering' END, last_error = 'MISSING_ARTIFACT' WHERE job_id = ?1 AND stage_id = (SELECT stage_id FROM artifacts WHERE artifact_id = ?2)", params![job_id, artifact_id])?;
        self.connection.execute("UPDATE jobs SET status = CASE WHEN status = 'cancelled' THEN status ELSE 'recovering' END, updated_at_ms = ?2, last_error = 'MISSING_ARTIFACT' WHERE job_id = ?1", params![job_id, now])?;
        Ok(())
    }
}

fn transition_job(from: JobStatus, to: JobStatus) -> Result<()> {
    let allowed = match from {
        JobStatus::Queued => matches!(to, JobStatus::Running | JobStatus::Cancelled | JobStatus::Failed),
        JobStatus::Running => matches!(to, JobStatus::Paused | JobStatus::Recovering | JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled),
        JobStatus::Paused => matches!(to, JobStatus::Running | JobStatus::Recovering | JobStatus::Cancelled),
        JobStatus::Recovering => matches!(to, JobStatus::Running | JobStatus::Paused | JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled),
        JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled => false,
    };
    if allowed { Ok(()) } else {
        Err(StateError::InvalidTransition { entity: "job", from: from.as_str().into(), to: to.as_str().into() })
    }
}

fn validate_id(value: &str, name: &'static str, max_chars: usize) -> Result<()> {
    validate_non_empty(value, name, max_chars)?;
    if value == "." || value == ".." || value.contains('/') || value.contains('\\') {
        return Err(StateError::InvalidInput(format!("{name} must be a path-safe identifier")));
    }
    Ok(())
}
fn validate_non_empty(value: &str, name: &'static str, max_chars: usize) -> Result<()> {
    if value.is_empty() || value.chars().count() > max_chars || value.chars().any(|character| character.is_control()) {
        return Err(StateError::InvalidInput(format!("{name} is empty, too long or contains a control character")));
    }
    Ok(())
}
fn validate_optional_hash(value: Option<&str>) -> Result<()> {
    if let Some(value) = value {
        if !is_sha256(value) { return Err(StateError::InvalidInput("artifact hashes must be sha256:<64 lowercase hex>".into())); }
    }
    Ok(())
}
fn is_sha256(value: &str) -> bool {
    value.len() == 71 && value.starts_with("sha256:") && value[7..].bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn to_i64(value: u64, name: &'static str) -> Result<i64> {
    i64::try_from(value).map_err(|_| StateError::InvalidInput(format!("{name} exceeds SQLite INTEGER range")))
}
fn quarantine_path(path: &Path, artifact_id: &str) -> PathBuf {
    let file_name = path.file_name().and_then(|name| name.to_str()).unwrap_or("artifact");
    path.with_file_name(format!("{file_name}.{artifact_id}.quarantine"))
}
fn hash_file(path: &Path) -> io::Result<(String, u64)> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 { break; }
        digest.update(&buffer[..read]);
        size = size.saturating_add(read as u64);
    }
    let bytes = digest.finalize();
    let mut hex = String::with_capacity(64);
    for byte in bytes { hex.push_str(&format!("{byte:02x}")); }
    Ok((format!("sha256:{hex}"), size))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    fn temp_path(name: &str, extension: &str) -> PathBuf {
        let mut path = env::temp_dir();
        path.push(format!("dubflow-{name}-{}-{}.{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos(), extension));
        let _ = fs::remove_file(&path);
        path
    }
    fn setup(store: &DurableStore) {
        store.create_job("job-1", "file:///source.mp4", 1).unwrap();
        store.create_stage("job-1", "analysis", "fake-analysis", 3).unwrap();
    }

    #[test]
    fn migration_creates_state() {
        let store = DurableStore::open_in_memory().unwrap();
        assert_eq!(store.schema_version().unwrap(), 1);
        setup(&store);
        assert_eq!(store.job_status("job-1").unwrap(), JobStatus::Queued);
        assert_eq!(store.stage_status("job-1", "analysis").unwrap(), StageStatus::Pending);
    }

    #[test]
    fn transitions_and_persisted_retry_budget_are_bounded() {
        let store = DurableStore::open_in_memory().unwrap();
        setup(&store);
        store.start_job("job-1", 2).unwrap();
        assert_eq!(store.start_stage("job-1", "analysis", 3).unwrap(), 1);
        store.fail_stage("job-1", "analysis", "worker EOF", true, 4).unwrap();
        assert_eq!(store.retry_stage("job-1", "analysis", "worker-restarted", 5).unwrap(), 2);
        store.start_stage("job-1", "analysis", 6).unwrap();
        store.fail_stage("job-1", "analysis", "worker EOF again", true, 7).unwrap();
        assert!(matches!(store.retry_stage("job-1", "analysis", "worker-restarted", 8), Err(StateError::InvalidInput(_))));
        assert_eq!(store.retry_stage("job-1", "analysis", "smaller-chunk", 9).unwrap(), 3);
        store.start_stage("job-1", "analysis", 10).unwrap();
        store.fail_stage("job-1", "analysis", "third failure", true, 11).unwrap();
        assert!(store.retry_stage("job-1", "analysis", "fourth-condition", 12).is_err());
    }

    #[test]
    fn hard_kill_recovery_marks_running_work_recovering() {
        let path = temp_path("restart", "sqlite");
        {
            let store = DurableStore::open(&path).unwrap();
            setup(&store);
            store.start_job("job-1", 2).unwrap();
            store.start_stage("job-1", "analysis", 3).unwrap();
        }
        let store = DurableStore::open(&path).unwrap();
        assert_eq!(store.recover_after_restart(10).unwrap(), 1);
        assert_eq!(store.job_status("job-1").unwrap(), JobStatus::Recovering);
        assert_eq!(store.stage_status("job-1", "analysis").unwrap(), StageStatus::Recovering);
        let _ = fs::remove_file(&path);
        let _ = fs::remove_file(path.with_extension("sqlite-wal"));
        let _ = fs::remove_file(path.with_extension("sqlite-shm"));
    }

    #[test]
    fn artifact_completion_hashes_before_commit_and_keeps_reusable_checkpoint_on_cancel() {
        let output = temp_path("artifact", "mp4");
        fs::write(&output, b"complete output").unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        setup(&store);
        store.start_job("job-1", 2).unwrap();
        store.start_stage("job-1", "analysis", 3).unwrap();
        let (hash, _) = hash_file(&output).unwrap();
        store.record_checkpoint("job-1", "analysis", "safe-1", Some(&hash), true, 4).unwrap();
        store.record_artifact_written("artifact-1", "job-1", "analysis", &output, Some(&hash), true, 5).unwrap();
        assert_eq!(store.commit_artifact("artifact-1", 6).unwrap(), hash);
        assert_eq!(store.stage_status("job-1", "analysis").unwrap(), StageStatus::Succeeded);
        store.cancel_job("job-1", "user requested", 7).unwrap();
        assert_eq!(store.artifact("artifact-1").unwrap().state, ArtifactState::Committed);
        assert_eq!(store.job_status("job-1").unwrap(), JobStatus::Cancelled);
        let _ = fs::remove_file(output);
    }

    #[test]
    fn missing_and_partial_artifacts_are_recoverable() {
        let missing_path = temp_path("missing", "mp4");
        let partial_path = temp_path("partial", "mp4");
        fs::write(&partial_path, b"partial").unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        setup(&store);
        store.start_job("job-1", 2).unwrap();
        store.start_stage("job-1", "analysis", 3).unwrap();
        store.record_artifact_written("missing", "job-1", "analysis", &missing_path, None, true, 3).unwrap();
        store.record_artifact_written("partial", "job-1", "analysis", &partial_path, Some("sha256:0000000000000000000000000000000000000000000000000000000000000000"), true, 3).unwrap();
        let events = store.reconcile_job("job-1", 4).unwrap();
        assert!(events.iter().any(|event| matches!(event, RecoveryEvent::MissingArtifact { artifact_id, was_committed: false, .. } if artifact_id == "missing")));
        assert!(events.iter().any(|event| matches!(event, RecoveryEvent::QuarantinedArtifact { artifact_id, .. } if artifact_id == "partial")));
        assert_eq!(store.artifact("partial").unwrap().state, ArtifactState::Quarantined);
        assert!(!partial_path.exists());
        let _ = fs::remove_file(partial_path.with_file_name(format!("{}.partial.quarantine", partial_path.file_name().unwrap().to_string_lossy())));
    }

    #[test]
    fn committed_missing_or_corrupt_artifacts_invalidate_the_stage() {
        let output = temp_path("committed-corrupt", "mp4");
        fs::write(&output, b"durable output").unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        setup(&store);
        store.start_job("job-1", 2).unwrap();
        store.start_stage("job-1", "analysis", 3).unwrap();
        let (hash, _) = hash_file(&output).unwrap();
        store.record_artifact_written("committed", "job-1", "analysis", &output, Some(&hash), true, 3).unwrap();
        store.commit_artifact("committed", 4).unwrap();
        fs::write(&output, b"changed after commit").unwrap();
        let events = store.reconcile_job("job-1", 5).unwrap();
        assert!(events.iter().any(|event| matches!(event, RecoveryEvent::QuarantinedArtifact { artifact_id, .. } if artifact_id == "committed")));
        assert_eq!(store.artifact("committed").unwrap().state, ArtifactState::Quarantined);
        assert_eq!(store.stage_status("job-1", "analysis").unwrap(), StageStatus::Recovering);
        assert_eq!(store.job_status("job-1").unwrap(), JobStatus::Recovering);
        let quarantine = output.with_file_name(format!("{}.committed.quarantine", output.file_name().unwrap().to_string_lossy()));
        assert!(!output.exists());
        assert!(quarantine.exists());
        let _ = fs::remove_file(quarantine);

        let missing = temp_path("committed-missing", "mp4");
        fs::write(&missing, b"another output").unwrap();
        let (missing_hash, _) = hash_file(&missing).unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        setup(&store);
        store.start_job("job-1", 2).unwrap();
        store.start_stage("job-1", "analysis", 3).unwrap();
        store.record_artifact_written("missing-committed", "job-1", "analysis", &missing, Some(&missing_hash), true, 3).unwrap();
        store.commit_artifact("missing-committed", 4).unwrap();
        fs::remove_file(&missing).unwrap();
        let events = store.reconcile_job("job-1", 5).unwrap();
        assert!(events.iter().any(|event| matches!(event, RecoveryEvent::MissingArtifact { artifact_id, was_committed: true, .. } if artifact_id == "missing-committed")));
        assert_eq!(store.artifact("missing-committed").unwrap().state, ArtifactState::Missing);
        assert_eq!(store.stage_status("job-1", "analysis").unwrap(), StageStatus::Recovering);
    }

    #[test]
    fn retry_budget_survives_reopen() {
        let path = temp_path("retry-persistence", "sqlite");
        {
            let store = DurableStore::open(&path).unwrap();
            setup(&store);
            store.start_job("job-1", 2).unwrap();
            store.start_stage("job-1", "analysis", 3).unwrap();
            store.fail_stage("job-1", "analysis", "worker timeout", true, 4).unwrap();
            assert_eq!(store.retry_stage("job-1", "analysis", "restarted worker", 5).unwrap(), 2);
        }
        {
            let store = DurableStore::open(&path).unwrap();
            assert_eq!(store.start_stage("job-1", "analysis", 6).unwrap(), 2);
        }
        let _ = fs::remove_file(&path);
        let _ = fs::remove_file(path.with_extension("sqlite-wal"));
        let _ = fs::remove_file(path.with_extension("sqlite-shm"));
    }

    #[test]
    fn unregistered_temp_files_are_quarantined_as_orphans() {
        let root = env::temp_dir().join(format!("dubflow-orphans-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(&root).unwrap();
        let orphan = root.join("unregistered.partial");
        fs::write(&orphan, b"orphan output").unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        let events = store.scan_orphan_artifacts(&root).unwrap();
        assert!(events.iter().any(|event| matches!(event, RecoveryEvent::OrphanArtifact { path, .. } if path.ends_with("unregistered.partial"))));
        assert!(!orphan.exists());
        assert!(root.join("unregistered.partial.orphan.quarantine").exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn sha256_matches_standard_vector() {
        let path = temp_path("hash", "txt");
        fs::write(&path, b"abc").unwrap();
        let (hash, size) = hash_file(&path).unwrap();
        assert_eq!(size, 3);
        assert_eq!(hash, "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let _ = fs::remove_file(path);
    }
}
