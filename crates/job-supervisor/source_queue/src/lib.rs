//! Supervisor-owned durable source enumeration.
//!
//! Source adapters only produce pages.  They never receive a SQLite handle and
//! cannot mutate queue state directly.  [`SourceQueue`] is the small durable
//! boundary used by the supervisor to checkpoint a page atomically, recover a
//! scan after a process restart, and track materialization progress for each
//! admitted source item.

use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use std::fmt;
use std::fs;
use std::io;
use std::path::Path;
use std::time::Duration;

pub const SCHEMA_VERSION: i64 = 2;
pub const MAX_ITEMS: usize = 10_000;
pub const MAX_SCAN_ID: usize = 128;
pub const MAX_PROVIDER_ID: usize = 64;
pub const MAX_SOURCE_REF: usize = 4_096;
pub const MAX_IDENTITY_KEY: usize = 1_024;
pub const MAX_SOURCE_ID: usize = 512;
pub const MAX_URL: usize = 4_096;
pub const MAX_CURSOR: usize = 1_024;
pub const MAX_ERROR: usize = 4_096;

const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS source_queue_schema (
    version INTEGER PRIMARY KEY NOT NULL,
    applied_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_scans (
    scan_id TEXT PRIMARY KEY NOT NULL,
    provider_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    cursor TEXT,
    status TEXT NOT NULL,
    max_items INTEGER NOT NULL,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_items (
    scan_id TEXT NOT NULL REFERENCES source_scans(scan_id) ON DELETE CASCADE,
    identity_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER,
    error_code TEXT,
    error_message TEXT,
    media_path TEXT,
    content_hash TEXT,
    created_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scan_id, identity_key),
    UNIQUE (scan_id, source_url)
);

CREATE INDEX IF NOT EXISTS source_items_scan_position
    ON source_items(scan_id, position, identity_key);
CREATE INDEX IF NOT EXISTS source_items_scan_status
    ON source_items(scan_id, status);

CREATE TABLE IF NOT EXISTS source_failures (
    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL REFERENCES source_scans(scan_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    retryable INTEGER NOT NULL,
    cursor TEXT,
    created_at_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS source_failures_scan ON source_failures(scan_id, failure_id);
"#;

#[derive(Debug)]
pub enum QueueError {
    Sqlite(rusqlite::Error),
    Io(io::Error),
    InvalidInput(String),
    NotFound {
        entity: &'static str,
        id: String,
    },
    NoProgress {
        scan_id: String,
        cursor: String,
    },
    Capacity {
        scan_id: String,
        limit: usize,
    },
    InvalidTransition {
        entity: &'static str,
        from: String,
        to: String,
    },
    StaleDispatch {
        scan_id: String,
    },
    ProducerBindingRequired {
        scan_id: String,
    },
}

impl fmt::Display for QueueError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(f, "SQLite error: {error}"),
            Self::Io(error) => write!(f, "filesystem error: {error}"),
            Self::InvalidInput(detail) => write!(f, "invalid source queue input: {detail}"),
            Self::NotFound { entity, id } => write!(f, "{entity} not found: {id}"),
            Self::NoProgress { scan_id, cursor } => {
                write!(f, "scan {scan_id} made no cursor progress at {cursor}")
            }
            Self::Capacity { scan_id, limit } => {
                write!(f, "scan {scan_id} reached the {limit}-item discovery bound")
            }
            Self::InvalidTransition { entity, from, to } => {
                write!(f, "invalid {entity} transition {from} -> {to}")
            }
            Self::StaleDispatch { scan_id } => write!(f, "source scan {scan_id} dispatch is stale"),
            Self::ProducerBindingRequired { scan_id } => {
                write!(
                    f,
                    "source scan {scan_id} requires a checked producer-bound dispatch"
                )
            }
        }
    }
}

impl std::error::Error for QueueError {}
impl From<rusqlite::Error> for QueueError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}
impl From<io::Error> for QueueError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub type Result<T> = std::result::Result<T, QueueError>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScanStatus {
    Queued,
    Running,
    Paused,
    Completed,
    Cancelled,
    Failed,
}

impl ScanStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::Paused => "paused",
            Self::Completed => "completed",
            Self::Cancelled => "cancelled",
            Self::Failed => "failed",
        }
    }

    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "queued" => Ok(Self::Queued),
            "running" => Ok(Self::Running),
            "paused" => Ok(Self::Paused),
            "completed" => Ok(Self::Completed),
            "cancelled" => Ok(Self::Cancelled),
            "failed" => Ok(Self::Failed),
            _ => Err(QueueError::InvalidInput(format!(
                "unknown scan status {value:?}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ItemStatus {
    Discovered,
    Downloading,
    Downloaded,
    Failed,
    Skipped,
    Cancelled,
}

impl ItemStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Discovered => "discovered",
            Self::Downloading => "downloading",
            Self::Downloaded => "downloaded",
            Self::Failed => "failed",
            Self::Skipped => "skipped",
            Self::Cancelled => "cancelled",
        }
    }

    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "discovered" => Ok(Self::Discovered),
            "downloading" => Ok(Self::Downloading),
            "downloaded" => Ok(Self::Downloaded),
            "failed" => Ok(Self::Failed),
            "skipped" => Ok(Self::Skipped),
            "cancelled" => Ok(Self::Cancelled),
            _ => Err(QueueError::InvalidInput(format!(
                "unknown source item status {value:?}"
            ))),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScanRecord {
    pub scan_id: String,
    pub provider_id: String,
    pub source_ref: String,
    pub cursor: Option<String>,
    pub status: ScanStatus,
    pub max_items: usize,
    pub discovered_count: usize,
    pub completed_count: usize,
    pub failed_count: usize,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    /// Changes on page commits and control transitions, independently of wall clock.
    pub dispatch_revision: u64,
    /// Fingerprint of the verified runtime/adapter/recipe request; never repinned.
    pub producer_fingerprint: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceQueueItem {
    pub scan_id: String,
    pub identity_key: String,
    pub source_id: String,
    pub source_url: String,
    pub position: u64,
    pub status: ItemStatus,
    pub retry_count: u8,
    pub downloaded_bytes: u64,
    pub total_bytes: Option<u64>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub media_path: Option<String>,
    pub content_hash: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PageItem {
    pub identity_key: String,
    pub source_id: String,
    pub source_url: String,
    pub position: u64,
}

impl PageItem {
    pub fn new(
        identity_key: impl Into<String>,
        source_id: impl Into<String>,
        source_url: impl Into<String>,
        position: u64,
    ) -> Self {
        Self {
            identity_key: identity_key.into(),
            source_id: source_id.into(),
            source_url: source_url.into(),
            position,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PageFailure {
    pub source_id: String,
    pub error_code: String,
    pub error_message: String,
    pub retryable: bool,
}

impl PageFailure {
    pub fn new(
        source_id: impl Into<String>,
        error_code: impl Into<String>,
        error_message: impl Into<String>,
        retryable: bool,
    ) -> Self {
        Self {
            source_id: source_id.into(),
            error_code: error_code.into(),
            error_message: error_message.into(),
            retryable,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PageCheckpoint {
    pub next_cursor: Option<String>,
    pub completed: bool,
    pub items: Vec<PageItem>,
    pub failures: Vec<PageFailure>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ItemProgress {
    pub status: ItemStatus,
    pub retry_count: u8,
    pub downloaded_bytes: u64,
    pub total_bytes: Option<u64>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub media_path: Option<String>,
    pub content_hash: Option<String>,
}

impl ItemProgress {
    pub fn discovered() -> Self {
        Self {
            status: ItemStatus::Discovered,
            retry_count: 0,
            downloaded_bytes: 0,
            total_bytes: None,
            error_code: None,
            error_message: None,
            media_path: None,
            content_hash: None,
        }
    }
}

pub struct SourceQueue {
    connection: Connection,
}

/// Names used by integrations that refer to this component as a queue store.
pub type QueueStore = SourceQueue;
pub type DurableSourceQueue = SourceQueue;

impl SourceQueue {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if path != Path::new(":memory:") {
            if let Some(parent) = path.parent() {
                if !parent.as_os_str().is_empty() {
                    fs::create_dir_all(parent)?;
                }
            }
        }
        Self::from_connection(Connection::open(path)?)
    }

    pub fn open_in_memory() -> Result<Self> {
        Self::from_connection(Connection::open_in_memory()?)
    }

    fn from_connection(connection: Connection) -> Result<Self> {
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.execute_batch(
            "PRAGMA foreign_keys = ON; PRAGMA synchronous = FULL; PRAGMA journal_mode = WAL;",
        )?;
        let queue = Self { connection };
        queue.migrate()?;
        Ok(queue)
    }

    fn migrate(&self) -> Result<()> {
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS source_queue_schema (version INTEGER PRIMARY KEY NOT NULL, applied_at_ms INTEGER NOT NULL);")?;
        let current: Option<i64> =
            tx.query_row("SELECT MAX(version) FROM source_queue_schema", [], |row| {
                row.get(0)
            })?;
        let current = current.unwrap_or(0);
        if current > SCHEMA_VERSION {
            return Err(QueueError::InvalidInput(format!(
                "unsupported source queue schema {current}"
            )));
        }
        if current == 0 {
            tx.execute_batch(SCHEMA_SQL)?;
            tx.execute(
                "INSERT INTO source_queue_schema(version, applied_at_ms) VALUES (1, 0)",
                [],
            )?;
        }
        if current < 2 {
            tx.execute_batch("ALTER TABLE source_scans ADD COLUMN dispatch_revision INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_revision >= 0); ALTER TABLE source_scans ADD COLUMN producer_fingerprint TEXT; INSERT INTO source_queue_schema(version, applied_at_ms) VALUES (2, 0);")?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn schema_version(&self) -> Result<i64> {
        Ok(self
            .connection
            .query_row("SELECT MAX(version) FROM source_queue_schema", [], |row| {
                row.get::<_, Option<i64>>(0)
            })?
            .unwrap_or(0))
    }

    pub fn create_scan(
        &self,
        scan_id: &str,
        provider_id: &str,
        source_ref: &str,
        max_items: usize,
        now_ms: u64,
    ) -> Result<ScanRecord> {
        self.create_scan_inner(scan_id, provider_id, source_ref, max_items, now_ms, None)
    }

    /// Admit a fresh scan using a fingerprint computed from verified producer pins.
    /// Existing scan IDs and legacy rows cannot be rebound to another producer.
    pub fn create_bound_scan(
        &self,
        scan_id: &str,
        provider_id: &str,
        source_ref: &str,
        max_items: usize,
        now_ms: u64,
        producer_fingerprint: &str,
    ) -> Result<ScanRecord> {
        if producer_fingerprint.len() != 64
            || !producer_fingerprint
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(QueueError::InvalidInput(
                "producer fingerprint must be a lowercase SHA-256".into(),
            ));
        }
        self.create_scan_inner(
            scan_id,
            provider_id,
            source_ref,
            max_items,
            now_ms,
            Some(producer_fingerprint),
        )
    }

    fn create_scan_inner(
        &self,
        scan_id: &str,
        provider_id: &str,
        source_ref: &str,
        max_items: usize,
        now_ms: u64,
        producer_fingerprint: Option<&str>,
    ) -> Result<ScanRecord> {
        validate_text(scan_id, "scan_id", MAX_SCAN_ID)?;
        validate_text(provider_id, "provider_id", MAX_PROVIDER_ID)?;
        validate_text(source_ref, "source_ref", MAX_SOURCE_REF)?;
        validate_capacity(max_items)?;
        let now = to_i64(now_ms, "now_ms")?;
        self.connection.execute(
            "INSERT INTO source_scans(scan_id, provider_id, source_ref, status, max_items, created_at_ms, updated_at_ms, producer_fingerprint) VALUES (?1, ?2, ?3, 'queued', ?4, ?5, ?5, ?6)",
            params![scan_id, provider_id, source_ref, max_items as i64, now, producer_fingerprint],
        )?;
        self.scan(scan_id)
    }

    pub fn scan(&self, scan_id: &str) -> Result<ScanRecord> {
        let row = self.connection.query_row(
            "SELECT scan_id, provider_id, source_ref, cursor, status, max_items, discovered_count, completed_count, failed_count, created_at_ms, updated_at_ms, dispatch_revision, producer_fingerprint FROM source_scans WHERE scan_id = ?1",
            params![scan_id],
            |row| Ok((
                row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, Option<String>>(3)?, row.get::<_, String>(4)?, row.get::<_, i64>(5)?, row.get::<_, i64>(6)?, row.get::<_, i64>(7)?, row.get::<_, i64>(8)?, row.get::<_, i64>(9)?, row.get::<_, i64>(10)?, row.get::<_, i64>(11)?, row.get::<_, Option<String>>(12)?
            )),
        ).optional()?.ok_or_else(|| QueueError::NotFound { entity: "scan", id: scan_id.into() })?;
        Ok(ScanRecord {
            scan_id: row.0,
            provider_id: row.1,
            source_ref: row.2,
            cursor: row.3,
            status: ScanStatus::parse(row.4)?,
            max_items: to_usize(row.5, "max_items")?,
            discovered_count: to_usize(row.6, "discovered_count")?,
            completed_count: to_usize(row.7, "completed_count")?,
            failed_count: to_usize(row.8, "failed_count")?,
            created_at_ms: to_u64(row.9, "created_at_ms")?,
            updated_at_ms: to_u64(row.10, "updated_at_ms")?,
            dispatch_revision: to_u64(row.11, "dispatch_revision")?,
            producer_fingerprint: row.12,
        })
    }

    /// Commit one enumeration page and its cursor as one durable transaction.
    /// Duplicate identity keys and canonical URLs are ignored, while unrelated
    /// page failures are retained and do not abort the remaining items.
    pub fn checkpoint_page(
        &self,
        scan_id: &str,
        page: &PageCheckpoint,
        now_ms: u64,
    ) -> Result<ScanRecord> {
        self.commit_page(scan_id, None, page, now_ms)
    }

    /// Commit only the page dispatched from this exact producer/revision/cursor.
    /// Capturing a new record after a late callback does not authorize its page.
    pub fn checkpoint_page_checked(
        &self,
        dispatched: &ScanRecord,
        page: &PageCheckpoint,
        now_ms: u64,
    ) -> Result<ScanRecord> {
        self.commit_page(&dispatched.scan_id, Some(dispatched), page, now_ms)
    }

    fn commit_page(
        &self,
        scan_id: &str,
        dispatched: Option<&ScanRecord>,
        page: &PageCheckpoint,
        now_ms: u64,
    ) -> Result<ScanRecord> {
        validate_page(page)?;
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let existing = self.scan(scan_id)?;
        if !matches!(existing.status, ScanStatus::Queued | ScanStatus::Running) {
            return Err(QueueError::InvalidTransition {
                entity: "scan",
                from: existing.status.as_str().into(),
                to: "checkpointed".into(),
            });
        }
        if let Some(expected) = dispatched {
            if existing.producer_fingerprint.is_none() {
                return Err(QueueError::ProducerBindingRequired {
                    scan_id: scan_id.into(),
                });
            }
            if expected.status != ScanStatus::Running
                || existing.status != ScanStatus::Running
                || expected.dispatch_revision != existing.dispatch_revision
                || expected.cursor != existing.cursor
                || expected.producer_fingerprint != existing.producer_fingerprint
                || expected.provider_id != existing.provider_id
                || expected.source_ref != existing.source_ref
                || expected.max_items != existing.max_items
            {
                return Err(QueueError::StaleDispatch {
                    scan_id: scan_id.into(),
                });
            }
        } else if existing.producer_fingerprint.is_some() {
            return Err(QueueError::ProducerBindingRequired {
                scan_id: scan_id.into(),
            });
        }
        ensure_next_revision(existing.dispatch_revision)?;
        if !page.completed && page.next_cursor == existing.cursor {
            return Err(QueueError::NoProgress {
                scan_id: scan_id.into(),
                cursor: existing.cursor.clone().unwrap_or_default(),
            });
        }
        let now = to_i64(now_ms, "now_ms")?;
        let current_count: i64 = tx.query_row(
            "SELECT COUNT(*) FROM source_items WHERE scan_id = ?1",
            params![scan_id],
            |row| row.get(0),
        )?;
        let mut new_count = 0usize;
        let mut page_identity_keys = std::collections::HashSet::new();
        let mut page_urls = std::collections::HashSet::new();
        for item in &page.items {
            validate_page_item(item)?;
            if !page_identity_keys.insert(&item.identity_key) || !page_urls.insert(&item.source_url)
            {
                continue;
            }
            let by_identity: Option<i64> = tx
                .query_row(
                    "SELECT 1 FROM source_items WHERE scan_id = ?1 AND identity_key = ?2 LIMIT 1",
                    params![scan_id, item.identity_key],
                    |row| row.get(0),
                )
                .optional()?;
            let by_url: Option<i64> = tx
                .query_row(
                    "SELECT 1 FROM source_items WHERE scan_id = ?1 AND source_url = ?2 LIMIT 1",
                    params![scan_id, item.source_url],
                    |row| row.get(0),
                )
                .optional()?;
            if by_identity.is_some() || by_url.is_some() {
                continue;
            }
            new_count += 1;
        }
        if current_count < 0
            || (current_count as usize).saturating_add(new_count) > existing.max_items
        {
            return Err(QueueError::Capacity {
                scan_id: scan_id.into(),
                limit: existing.max_items,
            });
        }
        for item in &page.items {
            let by_identity: Option<i64> = tx
                .query_row(
                    "SELECT 1 FROM source_items WHERE scan_id = ?1 AND identity_key = ?2 LIMIT 1",
                    params![scan_id, item.identity_key],
                    |row| row.get(0),
                )
                .optional()?;
            let by_url: Option<i64> = tx
                .query_row(
                    "SELECT 1 FROM source_items WHERE scan_id = ?1 AND source_url = ?2 LIMIT 1",
                    params![scan_id, item.source_url],
                    |row| row.get(0),
                )
                .optional()?;
            if by_identity.is_some() || by_url.is_some() {
                continue;
            }
            tx.execute(
                "INSERT INTO source_items(scan_id, identity_key, source_id, source_url, position, status, created_at_ms, updated_at_ms) VALUES (?1, ?2, ?3, ?4, ?5, 'discovered', ?6, ?6)",
                params![scan_id, item.identity_key, item.source_id, item.source_url, to_i64(item.position, "position")?, now],
            )?;
        }
        for failure in &page.failures {
            validate_failure(failure)?;
            tx.execute(
                "INSERT INTO source_failures(scan_id, source_id, error_code, error_message, retryable, cursor, created_at_ms) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![scan_id, failure.source_id, failure.error_code, failure.error_message, if failure.retryable { 1 } else { 0 }, existing.cursor, now],
            )?;
        }
        let next_status = if page.completed {
            ScanStatus::Completed
        } else {
            ScanStatus::Running
        };
        tx.execute(
            "UPDATE source_scans SET cursor = ?1, status = ?2, discovered_count = (SELECT COUNT(*) FROM source_items WHERE scan_id = ?3), completed_count = (SELECT COUNT(*) FROM source_items WHERE scan_id = ?3 AND status = 'downloaded'), failed_count = (SELECT COUNT(*) FROM source_failures WHERE scan_id = ?3) + (SELECT COUNT(*) FROM source_items WHERE scan_id = ?3 AND status = 'failed'), updated_at_ms = ?4, dispatch_revision = dispatch_revision + 1 WHERE scan_id = ?3",
            params![page.next_cursor, next_status.as_str(), scan_id, now],
        )?;
        tx.commit()?;
        self.scan(scan_id)
    }

    pub fn items(&self, scan_id: &str) -> Result<Vec<SourceQueueItem>> {
        self.scan(scan_id)?;
        let mut statement = self.connection.prepare("SELECT scan_id, identity_key, source_id, source_url, position, status, retry_count, downloaded_bytes, total_bytes, error_code, error_message, media_path, content_hash FROM source_items WHERE scan_id = ?1 ORDER BY position, identity_key")?;
        let rows = statement.query_map(params![scan_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, i64>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, i64>(6)?,
                row.get::<_, i64>(7)?,
                row.get::<_, Option<i64>>(8)?,
                row.get::<_, Option<String>>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, Option<String>>(11)?,
                row.get::<_, Option<String>>(12)?,
            ))
        })?;
        let mut items = Vec::new();
        for row in rows {
            let row = row?;
            items.push(SourceQueueItem {
                scan_id: row.0,
                identity_key: row.1,
                source_id: row.2,
                source_url: row.3,
                position: to_u64(row.4, "position")?,
                status: ItemStatus::parse(row.5)?,
                retry_count: to_u8(row.6, "retry_count")?,
                downloaded_bytes: to_u64(row.7, "downloaded_bytes")?,
                total_bytes: row
                    .8
                    .map(|value| to_u64(value, "total_bytes"))
                    .transpose()?,
                error_code: row.9,
                error_message: row.10,
                media_path: row.11,
                content_hash: row.12,
            });
        }
        Ok(items)
    }

    pub fn update_item_progress(
        &self,
        scan_id: &str,
        identity_key: &str,
        progress: &ItemProgress,
        now_ms: u64,
    ) -> Result<()> {
        validate_text(identity_key, "identity_key", MAX_IDENTITY_KEY)?;
        validate_progress(progress)?;
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let scan = self.scan(scan_id)?;
        if matches!(
            scan.status,
            ScanStatus::Paused | ScanStatus::Cancelled | ScanStatus::Failed
        ) {
            return Err(QueueError::InvalidTransition {
                entity: "scan",
                from: scan.status.as_str().into(),
                to: "item-progress".into(),
            });
        }
        let changed = tx.execute(
            "UPDATE source_items SET status = ?1, retry_count = ?2, downloaded_bytes = ?3, total_bytes = ?4, error_code = ?5, error_message = ?6, media_path = ?7, content_hash = ?8, updated_at_ms = ?9 WHERE scan_id = ?10 AND identity_key = ?11",
            params![progress.status.as_str(), i64::from(progress.retry_count), to_i64(progress.downloaded_bytes, "downloaded_bytes")?, progress.total_bytes.map(|value| to_i64(value, "total_bytes")).transpose()?, progress.error_code, progress.error_message, progress.media_path, progress.content_hash, to_i64(now_ms, "now_ms")?, scan_id, identity_key],
        )?;
        if changed == 0 {
            return Err(QueueError::NotFound {
                entity: "source item",
                id: format!("{scan_id}/{identity_key}"),
            });
        }
        tx.execute(
            "UPDATE source_scans SET completed_count = (SELECT COUNT(*) FROM source_items WHERE scan_id = ?1 AND status = 'downloaded'), failed_count = (SELECT COUNT(*) FROM source_failures WHERE scan_id = ?1) + (SELECT COUNT(*) FROM source_items WHERE scan_id = ?1 AND status = 'failed'), updated_at_ms = ?2 WHERE scan_id = ?1",
            params![scan_id, to_i64(now_ms, "now_ms")?],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// Compatibility alias for callers that model a page checkpoint as a
    /// queue operation rather than a source enumeration operation.
    pub fn checkpoint(
        &self,
        scan_id: &str,
        page: &PageCheckpoint,
        now_ms: u64,
    ) -> Result<ScanRecord> {
        self.checkpoint_page(scan_id, page, now_ms)
    }

    pub fn update_item(
        &self,
        scan_id: &str,
        identity_key: &str,
        progress: &ItemProgress,
        now_ms: u64,
    ) -> Result<()> {
        self.update_item_progress(scan_id, identity_key, progress, now_ms)
    }

    pub fn pause_scan(&self, scan_id: &str, now_ms: u64) -> Result<ScanRecord> {
        self.transition_scan(
            scan_id,
            ScanStatus::Paused,
            now_ms,
            &[ScanStatus::Queued, ScanStatus::Running],
        )
    }
    pub fn cancel_scan(&self, scan_id: &str, now_ms: u64) -> Result<ScanRecord> {
        self.transition_scan(
            scan_id,
            ScanStatus::Cancelled,
            now_ms,
            &[ScanStatus::Queued, ScanStatus::Running, ScanStatus::Paused],
        )
    }

    pub fn resume_scan(&self, scan_id: &str, now_ms: u64) -> Result<ScanRecord> {
        self.transition_scan(
            scan_id,
            ScanStatus::Running,
            now_ms,
            &[ScanStatus::Paused, ScanStatus::Queued],
        )
    }

    /// Mark scans that were running when the supervisor process stopped as
    /// paused.  A later explicit resume starts them from the persisted cursor.
    pub fn recover_running(&self, now_ms: u64) -> Result<usize> {
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let exhausted: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM source_scans WHERE status='running' AND dispatch_revision=9223372036854775807)", [], |row| row.get(0))?;
        if exhausted {
            return Err(QueueError::InvalidInput(
                "source dispatch revision exhausted".into(),
            ));
        }
        let changed = tx.execute("UPDATE source_scans SET status = 'paused', updated_at_ms = ?1, dispatch_revision = dispatch_revision + 1 WHERE status = 'running'", params![to_i64(now_ms, "now_ms")?])?;
        tx.commit()?;
        Ok(changed)
    }

    fn transition_scan(
        &self,
        scan_id: &str,
        target: ScanStatus,
        now_ms: u64,
        allowed: &[ScanStatus],
    ) -> Result<ScanRecord> {
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let existing = self.scan(scan_id)?;
        if !allowed.contains(&existing.status) {
            return Err(QueueError::InvalidTransition {
                entity: "scan",
                from: existing.status.as_str().into(),
                to: target.as_str().into(),
            });
        }
        ensure_next_revision(existing.dispatch_revision)?;
        tx.execute(
            "UPDATE source_scans SET status = ?1, updated_at_ms = ?2, dispatch_revision = dispatch_revision + 1 WHERE scan_id = ?3",
            params![target.as_str(), to_i64(now_ms, "now_ms")?, scan_id],
        )?;
        tx.commit()?;
        self.scan(scan_id)
    }
}

// Keep this helper private while allowing the transaction type to be named in
// future extensions without exposing a database handle to adapters.
#[allow(dead_code)]
fn _transaction<'a>(queue: &'a SourceQueue) -> Result<Transaction<'a>> {
    Ok(queue.connection.unchecked_transaction()?)
}

fn ensure_next_revision(revision: u64) -> Result<()> {
    if revision >= i64::MAX as u64 {
        return Err(QueueError::InvalidInput(
            "source dispatch revision exhausted".into(),
        ));
    }
    Ok(())
}

fn validate_page(page: &PageCheckpoint) -> Result<()> {
    if page.items.len() > MAX_ITEMS || page.failures.len() > MAX_ITEMS {
        return Err(QueueError::InvalidInput(
            "page exceeds the bounded source queue capacity".into(),
        ));
    }
    if page.completed && page.next_cursor.is_some() {
        return Err(QueueError::InvalidInput(
            "completed pages cannot carry a cursor".into(),
        ));
    }
    if !page.completed && page.next_cursor.is_none() {
        return Err(QueueError::InvalidInput(
            "incomplete pages must carry a cursor".into(),
        ));
    }
    if let Some(cursor) = page.next_cursor.as_deref() {
        validate_text(cursor, "cursor", MAX_CURSOR)?;
    }
    Ok(())
}

fn validate_page_item(item: &PageItem) -> Result<()> {
    validate_text(&item.identity_key, "identity_key", MAX_IDENTITY_KEY)?;
    validate_text(&item.source_id, "source_id", MAX_SOURCE_ID)?;
    validate_text(&item.source_url, "source_url", MAX_URL)?;
    to_i64(item.position, "position")?;
    Ok(())
}

fn validate_failure(failure: &PageFailure) -> Result<()> {
    validate_text(&failure.source_id, "failure.source_id", MAX_SOURCE_ID)?;
    validate_text(&failure.error_code, "failure.error_code", 128)?;
    validate_text(&failure.error_message, "failure.error_message", MAX_ERROR)?;
    Ok(())
}

fn validate_progress(progress: &ItemProgress) -> Result<()> {
    if progress
        .error_code
        .as_deref()
        .is_some_and(|value| validate_text(value, "error_code", 128).is_err())
    {
        return Err(QueueError::InvalidInput("error_code is invalid".into()));
    }
    if progress
        .error_message
        .as_deref()
        .is_some_and(|value| validate_text(value, "error_message", MAX_ERROR).is_err())
    {
        return Err(QueueError::InvalidInput("error_message is invalid".into()));
    }
    for (name, value) in [
        ("media_path", progress.media_path.as_deref()),
        ("content_hash", progress.content_hash.as_deref()),
    ] {
        if let Some(value) = value {
            validate_text(value, name, MAX_URL)?;
        }
    }
    Ok(())
}

fn validate_capacity(value: usize) -> Result<()> {
    if !(1..=MAX_ITEMS).contains(&value) {
        return Err(QueueError::InvalidInput(format!(
            "max_items must be between 1 and {MAX_ITEMS}"
        )));
    }
    Ok(())
}

fn validate_text(value: &str, name: &str, max: usize) -> Result<()> {
    if value.is_empty() || value.len() > max || value.chars().any(|ch| ch.is_control()) {
        return Err(QueueError::InvalidInput(format!(
            "{name} must be non-empty, bounded and control-free"
        )));
    }
    Ok(())
}

fn to_i64(value: u64, name: &str) -> Result<i64> {
    i64::try_from(value)
        .map_err(|_| QueueError::InvalidInput(format!("{name} exceeds SQLite integer range")))
}
fn to_u64(value: i64, name: &str) -> Result<u64> {
    u64::try_from(value).map_err(|_| QueueError::InvalidInput(format!("{name} is negative")))
}
fn to_usize(value: i64, name: &str) -> Result<usize> {
    usize::try_from(value)
        .map_err(|_| QueueError::InvalidInput(format!("{name} is outside the usize range")))
}
fn to_u8(value: i64, name: &str) -> Result<u8> {
    u8::try_from(value)
        .map_err(|_| QueueError::InvalidInput(format!("{name} is outside the u8 range")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn page(ids: &[&str], cursor: Option<&str>, completed: bool) -> PageCheckpoint {
        PageCheckpoint {
            next_cursor: cursor.map(str::to_owned),
            completed,
            items: ids
                .iter()
                .enumerate()
                .map(|(index, id)| {
                    PageItem::new(
                        format!("fixture:{id}"),
                        *id,
                        format!("https://example.test/{id}"),
                        index as u64,
                    )
                })
                .collect(),
            failures: Vec::new(),
        }
    }

    #[test]
    fn page_commit_is_deduplicated_and_resumes_after_reopen() {
        let database = TestDatabase::new();
        let path = database.path();
        {
            let queue = SourceQueue::open(&path).unwrap();
            queue
                .create_scan("scan", "fixture", "creator", 10_000, 1)
                .unwrap();
            queue.resume_scan("scan", 2).unwrap();
            let record = queue
                .checkpoint_page("scan", &page(&["one", "two"], Some("cursor-2"), false), 3)
                .unwrap();
            assert_eq!(record.discovered_count, 2);
            assert_eq!(record.cursor.as_deref(), Some("cursor-2"));
            let duplicate = PageCheckpoint {
                next_cursor: None,
                completed: true,
                items: vec![
                    PageItem::new("fixture:two", "two", "https://example.test/two", 0),
                    PageItem::new("fixture:three", "three", "https://example.test/three", 1),
                ],
                failures: vec![],
            };
            let record = queue.checkpoint_page("scan", &duplicate, 4).unwrap();
            assert_eq!(record.discovered_count, 3);
            assert_eq!(record.status, ScanStatus::Completed);
        }
        let queue = SourceQueue::open(&path).unwrap();
        assert_eq!(queue.items("scan").unwrap().len(), 3);
        assert_eq!(queue.scan("scan").unwrap().status, ScanStatus::Completed);
    }

    #[test]
    fn no_progress_and_capacity_are_rejected_without_partial_writes() {
        let queue = SourceQueue::open_in_memory().unwrap();
        queue
            .create_scan("scan", "fixture", "creator", 1, 1)
            .unwrap();
        queue.resume_scan("scan", 2).unwrap();
        queue
            .checkpoint_page("scan", &page(&["one"], Some("cursor"), false), 3)
            .unwrap();
        let unchanged = page(&["two"], Some("cursor"), false);
        assert!(matches!(
            queue.checkpoint_page("scan", &unchanged, 4),
            Err(QueueError::NoProgress { .. })
        ));
        let too_many = PageCheckpoint {
            next_cursor: None,
            completed: true,
            items: vec![PageItem::new(
                "fixture:two",
                "two",
                "https://example.test/two",
                1,
            )],
            failures: vec![],
        };
        assert!(matches!(
            queue.checkpoint_page("scan", &too_many, 5),
            Err(QueueError::Capacity { .. })
        ));
        assert_eq!(queue.items("scan").unwrap().len(), 1);
    }

    #[test]
    fn page_failure_isolated_and_progress_persists() {
        let queue = SourceQueue::open_in_memory().unwrap();
        queue
            .create_scan("scan", "fixture", "creator", 10, 1)
            .unwrap();
        queue.resume_scan("scan", 2).unwrap();
        let page = PageCheckpoint {
            next_cursor: None,
            completed: true,
            items: vec![PageItem::new(
                "fixture:one",
                "one",
                "https://example.test/one",
                0,
            )],
            failures: vec![PageFailure::new(
                "private",
                "PRIVATE",
                "source is private",
                false,
            )],
        };
        let record = queue.checkpoint_page("scan", &page, 3).unwrap();
        assert_eq!(record.discovered_count, 1);
        assert_eq!(record.failed_count, 1);
        queue
            .update_item_progress(
                "scan",
                "fixture:one",
                &ItemProgress {
                    status: ItemStatus::Downloaded,
                    retry_count: 0,
                    downloaded_bytes: 10,
                    total_bytes: Some(10),
                    error_code: None,
                    error_message: None,
                    media_path: Some("/tmp/one.mp4".into()),
                    content_hash: Some("sha256:abc".into()),
                },
                4,
            )
            .unwrap();
        assert_eq!(queue.scan("scan").unwrap().completed_count, 1);
    }

    #[test]
    fn pause_cancel_recover_are_explicit() {
        let queue = SourceQueue::open_in_memory().unwrap();
        queue
            .create_scan("one", "fixture", "creator", 2, 1)
            .unwrap();
        queue.resume_scan("one", 2).unwrap();
        assert_eq!(queue.recover_running(3).unwrap(), 1);
        assert_eq!(queue.scan("one").unwrap().status, ScanStatus::Paused);
        queue.resume_scan("one", 4).unwrap();
        queue.pause_scan("one", 5).unwrap();
        queue.cancel_scan("one", 6).unwrap();
        assert_eq!(queue.scan("one").unwrap().status, ScanStatus::Cancelled);
    }

    struct TestDatabase {
        directory: std::path::PathBuf,
    }

    impl TestDatabase {
        fn new() -> Self {
            static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let nonce = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let index = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let directory = std::env::temp_dir().join(format!(
                "dubflow-source-dispatch-{}-{nonce}-{index}",
                std::process::id()
            ));
            fs::create_dir(&directory).unwrap();
            Self { directory }
        }
        fn path(&self) -> std::path::PathBuf {
            self.directory.join("queue.sqlite3")
        }
    }

    impl Drop for TestDatabase {
        fn drop(&mut self) {
            if let (Ok(target), Ok(parent)) = (
                fs::canonicalize(&self.directory),
                fs::canonicalize(std::env::temp_dir()),
            ) {
                if target.parent() == Some(parent.as_path())
                    && target
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with("dubflow-source-dispatch-")
                {
                    let _ = fs::remove_dir_all(target);
                }
            }
        }
    }

    fn bound(queue: &SourceQueue) -> ScanRecord {
        queue
            .create_bound_scan("bound", "fixture", "creator", 10, 1, &"a".repeat(64))
            .unwrap();
        queue.resume_scan("bound", 1).unwrap()
    }

    #[test]
    fn paused_page_cannot_restart_scan_or_commit_items() {
        let queue = SourceQueue::open_in_memory().unwrap();
        queue
            .create_scan("scan", "fixture", "creator", 10, 1)
            .unwrap();
        queue.resume_scan("scan", 1).unwrap();
        queue.pause_scan("scan", 1).unwrap();
        let before = queue.scan("scan").unwrap();
        assert!(matches!(
            queue.checkpoint_page("scan", &page(&["late"], Some("late"), false), 1),
            Err(QueueError::InvalidTransition { .. })
        ));
        assert_eq!(queue.scan("scan").unwrap(), before);
        assert!(queue.items("scan").unwrap().is_empty());
    }

    #[test]
    fn bound_pages_require_the_original_producer_and_dispatch() {
        let queue = SourceQueue::open_in_memory().unwrap();
        let dispatched = bound(&queue);
        let result = page(&["one"], Some("page-1"), false);
        assert!(matches!(
            queue.checkpoint_page("bound", &result, 1),
            Err(QueueError::ProducerBindingRequired { .. })
        ));
        let mut wrong = dispatched.clone();
        wrong.producer_fingerprint = Some("b".repeat(64));
        assert!(matches!(
            queue.checkpoint_page_checked(&wrong, &result, 1),
            Err(QueueError::StaleDispatch { .. })
        ));
        assert_eq!(queue.scan("bound").unwrap(), dispatched);
        let committed = queue
            .checkpoint_page_checked(&dispatched, &result, 1)
            .unwrap();
        assert_eq!(
            committed.dispatch_revision,
            dispatched.dispatch_revision + 1
        );
        assert_eq!(
            committed.producer_fingerprint,
            dispatched.producer_fingerprint
        );
        assert!(matches!(
            queue.checkpoint_page_checked(&dispatched, &result, 1),
            Err(QueueError::StaleDispatch { .. })
        ));
        assert_eq!(queue.items("bound").unwrap().len(), 1);
        assert!(queue
            .create_bound_scan("bound", "fixture", "other", 10, 1, &"b".repeat(64))
            .is_err());
        assert_eq!(queue.scan("bound").unwrap(), committed);
    }

    #[test]
    fn pause_resume_same_millisecond_invalidates_inflight_page() {
        let queue = SourceQueue::open_in_memory().unwrap();
        let dispatched = bound(&queue);
        let paused = queue.pause_scan("bound", 1).unwrap();
        let resumed = queue.resume_scan("bound", 1).unwrap();
        assert_eq!(resumed.cursor, dispatched.cursor);
        assert_eq!(resumed.updated_at_ms, dispatched.updated_at_ms);
        assert!(paused.dispatch_revision > dispatched.dispatch_revision);
        assert!(resumed.dispatch_revision > paused.dispatch_revision);
        let mut late = page(&["late"], Some("late-cursor"), false);
        late.failures
            .push(PageFailure::new("deleted", "NOT_FOUND", "deleted", false));
        assert!(matches!(
            queue.checkpoint_page_checked(&dispatched, &late, 1),
            Err(QueueError::StaleDispatch { .. })
        ));
        assert_eq!(queue.scan("bound").unwrap(), resumed);
        assert!(queue.items("bound").unwrap().is_empty());
        let failures: i64 = queue
            .connection
            .query_row("SELECT COUNT(*) FROM source_failures", [], |row| row.get(0))
            .unwrap();
        assert_eq!(failures, 0);
        queue.checkpoint_page_checked(&resumed, &late, 1).unwrap();
    }

    #[test]
    fn reopened_scan_keeps_binding_cursor_and_invalidates_recovered_dispatch() {
        let database = TestDatabase::new();
        let before;
        {
            let queue = SourceQueue::open(database.path()).unwrap();
            let dispatched = bound(&queue);
            before = queue
                .checkpoint_page_checked(&dispatched, &page(&["one"], Some("page-1"), false), 2)
                .unwrap();
        }
        let queue = SourceQueue::open(database.path()).unwrap();
        assert_eq!(queue.scan("bound").unwrap(), before);
        assert_eq!(queue.recover_running(2).unwrap(), 1);
        let resumed = queue.resume_scan("bound", 2).unwrap();
        assert!(matches!(
            queue.checkpoint_page_checked(&before, &page(&["late"], None, true), 2),
            Err(QueueError::StaleDispatch { .. })
        ));
        let finished = queue
            .checkpoint_page_checked(&resumed, &page(&["two"], None, true), 2)
            .unwrap();
        assert_eq!(finished.status, ScanStatus::Completed);
        assert_eq!(finished.discovered_count, 2);
        assert_eq!(finished.producer_fingerprint, Some("a".repeat(64)));
    }

    #[test]
    fn competing_connections_commit_only_one_dispatch() {
        let database = TestDatabase::new();
        {
            let queue = SourceQueue::open(database.path()).unwrap();
            bound(&queue);
        }
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        // Open both connections before spawning: an open failure must fail the
        // test rather than strand a peer forever at the dispatch barrier.
        let queues: Vec<_> = (0..2)
            .map(|_| SourceQueue::open(database.path()).unwrap())
            .collect();
        let handles: Vec<_> = queues
            .into_iter()
            .enumerate()
            .map(|(index, queue)| {
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    let dispatched = queue.scan("bound").unwrap();
                    barrier.wait();
                    let name = format!("item-{index}");
                    let mut result = page(&[&name], Some(&name), false);
                    result
                        .failures
                        .push(PageFailure::new(&name, "PRIVATE", "private", false));
                    match queue.checkpoint_page_checked(&dispatched, &result, 2) {
                        Ok(_) => true,
                        Err(QueueError::StaleDispatch { .. }) => false,
                        other => panic!("unexpected concurrent result: {other:?}"),
                    }
                })
            })
            .collect();
        let successes = handles
            .into_iter()
            .map(|handle| usize::from(handle.join().unwrap()))
            .sum::<usize>();
        assert_eq!(successes, 1);
        let queue = SourceQueue::open(database.path()).unwrap();
        let scan = queue.scan("bound").unwrap();
        assert_eq!(scan.discovered_count, 1);
        assert_eq!(scan.failed_count, 1);
        assert_eq!(scan.dispatch_revision, 2);
        assert_eq!(queue.items("bound").unwrap().len(), 1);
    }

    #[test]
    fn version_one_rows_migrate_without_inventing_producer_pins() {
        let database = TestDatabase::new();
        {
            let connection = Connection::open(database.path()).unwrap();
            connection.execute_batch(SCHEMA_SQL).unwrap();
            connection.execute_batch("INSERT INTO source_queue_schema VALUES(1,0); INSERT INTO source_scans(scan_id,provider_id,source_ref,cursor,status,max_items,discovered_count) VALUES('legacy','fixture','creator','page-4','running',10,1); INSERT INTO source_items(scan_id,identity_key,source_id,source_url,position,status) VALUES('legacy','fixture:one','one','https://example.test/one',1,'discovered');").unwrap();
        }
        let queue = SourceQueue::open(database.path()).unwrap();
        assert_eq!(queue.schema_version().unwrap(), 2);
        let legacy = queue.scan("legacy").unwrap();
        assert_eq!(legacy.cursor.as_deref(), Some("page-4"));
        assert_eq!(legacy.producer_fingerprint, None);
        assert_eq!(legacy.dispatch_revision, 0);
        assert_eq!(queue.items("legacy").unwrap().len(), 1);
        assert!(matches!(
            queue.checkpoint_page_checked(&legacy, &page(&["two"], None, true), 1),
            Err(QueueError::ProducerBindingRequired { .. })
        ));
        assert!(queue
            .create_bound_scan("legacy", "fixture", "creator", 10, 1, &"a".repeat(64))
            .is_err());
        assert_eq!(queue.scan("legacy").unwrap(), legacy);
        queue
            .checkpoint_page("legacy", &page(&["two"], None, true), 1)
            .unwrap();
    }

    #[test]
    fn invalid_fingerprint_and_future_schema_do_not_admit_or_migrate() {
        let queue = SourceQueue::open_in_memory().unwrap();
        for fingerprint in ["", "abc", &"A".repeat(64), &"g".repeat(64)] {
            assert!(queue
                .create_bound_scan("bad", "fixture", "creator", 10, 1, fingerprint)
                .is_err());
        }
        assert!(matches!(
            queue.scan("bad"),
            Err(QueueError::NotFound { .. })
        ));
        let database = TestDatabase::new();
        {
            let connection = Connection::open(database.path()).unwrap();
            connection.execute_batch(SCHEMA_SQL).unwrap();
            connection
                .execute("INSERT INTO source_queue_schema VALUES(3,0)", [])
                .unwrap();
        }
        assert!(SourceQueue::open(database.path()).is_err());
        let connection = Connection::open(database.path()).unwrap();
        let columns: i64 = connection.query_row("SELECT COUNT(*) FROM pragma_table_info('source_scans') WHERE name IN ('dispatch_revision','producer_fingerprint')", [], |row| row.get(0)).unwrap();
        assert_eq!(columns, 0);
        let versions: i64 = connection
            .query_row("SELECT COUNT(*) FROM source_queue_schema", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(versions, 1);
    }

    #[test]
    fn paused_cancelled_progress_and_count_failure_preserve_items_atomically() {
        let queue = SourceQueue::open_in_memory().unwrap();
        let dispatched = bound(&queue);
        queue
            .checkpoint_page_checked(&dispatched, &page(&["one"], Some("page-1"), false), 2)
            .unwrap();
        let mut progress = ItemProgress::discovered();
        progress.status = ItemStatus::Downloaded;
        progress.downloaded_bytes = 10;
        progress.total_bytes = Some(10);
        queue.pause_scan("bound", 3).unwrap();
        assert!(queue
            .update_item_progress("bound", "fixture:one", &progress, 3)
            .is_err());
        queue.resume_scan("bound", 3).unwrap();
        queue.connection.execute_batch("CREATE TRIGGER refuse_count BEFORE UPDATE OF completed_count ON source_scans BEGIN SELECT RAISE(ABORT,'injected count failure'); END;").unwrap();
        assert!(queue
            .update_item_progress("bound", "fixture:one", &progress, 3)
            .is_err());
        let item = &queue.items("bound").unwrap()[0];
        assert_eq!(item.status, ItemStatus::Discovered);
        assert_eq!(item.downloaded_bytes, 0);
        assert_eq!(queue.scan("bound").unwrap().completed_count, 0);
        queue.cancel_scan("bound", 3).unwrap();
        assert!(queue
            .update_item_progress("bound", "fixture:one", &progress, 3)
            .is_err());
        assert_eq!(queue.items("bound").unwrap()[0], *item);
    }

    #[test]
    fn exhausted_revision_fails_without_wrapping_or_partial_page() {
        let queue = SourceQueue::open_in_memory().unwrap();
        bound(&queue);
        queue.connection.execute("UPDATE source_scans SET dispatch_revision=9223372036854775807 WHERE scan_id='bound'", []).unwrap();
        let before = queue.scan("bound").unwrap();
        assert!(queue.pause_scan("bound", 3).is_err());
        assert!(queue.recover_running(3).is_err());
        assert!(queue
            .checkpoint_page_checked(&before, &page(&["one"], None, true), 3)
            .is_err());
        assert_eq!(queue.scan("bound").unwrap(), before);
        assert!(queue.items("bound").unwrap().is_empty());
    }
}
