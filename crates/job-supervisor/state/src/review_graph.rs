//! Optional supervisor-owned snapshot/graph CAS. Documents are admitted by
//! the native caller; this module does not validate media or accept worker IPC.
use super::{is_sha256, to_i64, validate_id, DurableStore, Result, StateError, MAX_ID_CHARS};
use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use sha2::{Digest, Sha256};
use std::collections::HashSet;

const SCHEMA: i64 = 1;
const SQL: &str = include_str!("../../../../migrations/0003_review_graph.sql");
const MAX_BYTES: u64 = 64 * 1024 * 1024;
const MAX_UNDO: u16 = 256;
const UNDO_REVISION_GROWTH: u64 = 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReviewToken {
    pub job_id: String,
    pub revision: u64,
    pub snapshot_sha256: String,
    pub graph_sha256: String,
    pub original_manifest_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReviewGraphState {
    pub token: ReviewToken,
    pub original_artifact_id: String,
    pub snapshot_json: String,
    pub graph_json: String,
    pub undo_target_revision: Option<u64>,
    pub undo_depth: u16,
}

fn invalid(message: &str) -> StateError {
    StateError::InvalidInput(message.into())
}

fn digest(value: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(value.as_bytes()))
}

fn payload_bytes(snapshot: &str, graph: &str) -> Result<u64> {
    let bytes = (snapshot.len() as u64)
        .checked_add(graph.len() as u64)
        .ok_or_else(|| invalid("review documents exceed byte limit"))?;
    if snapshot.trim().is_empty() || graph.trim().is_empty() || bytes > MAX_BYTES {
        return Err(invalid(
            "admitted review documents are empty or exceed byte limit",
        ));
    }
    Ok(bytes)
}

fn decimal(value: &str) -> Result<u64> {
    let number = value
        .parse::<u64>()
        .map_err(|_| invalid("invalid review revision"))?;
    if number.to_string() != value {
        return Err(invalid("noncanonical review revision"));
    }
    Ok(number)
}

fn schema(db: &Connection) -> Result<Option<i64>> {
    let exists: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_schema_migrations')", [], |r| r.get(0))?;
    if !exists {
        let partial: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name IN ('review_graph_revisions','review_graph_heads'))", [], |r| r.get(0))?;
        if partial {
            return Err(invalid(
                "unregistered Review tables require explicit recovery",
            ));
        }
        return Ok(None);
    }
    let version: Option<i64> = db.query_row(
        "SELECT MAX(version) FROM review_schema_migrations",
        [],
        |r| r.get(0),
    )?;
    if version != Some(SCHEMA) {
        return Err(invalid("unsupported Review feature schema"));
    }
    Ok(version)
}

fn require_schema(db: &Connection) -> Result<()> {
    if schema(db)?.is_none() {
        return Err(invalid("Review feature schema is not initialized"));
    }
    Ok(())
}

fn original_binding(db: &Connection, job: &str, artifact: &str, hash: &str) -> Result<()> {
    validate_id(job, "job_id", MAX_ID_CHARS)?;
    validate_id(artifact, "original artifact", MAX_ID_CHARS)?;
    if !is_sha256(hash) {
        return Err(invalid("invalid original review manifest hash"));
    }
    let valid: bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM artifacts a JOIN jobs j ON j.job_id=a.job_id JOIN stages s ON s.job_id=a.job_id AND s.stage_id=a.stage_id WHERE a.artifact_id=?1 AND a.job_id=?2 AND a.state='committed' AND a.content_hash=?3 AND a.size_bytes>0 AND (a.expected_hash IS NULL OR a.expected_hash=?3) AND j.status='succeeded' AND s.status='succeeded' AND EXISTS(SELECT 1 FROM job_start_requests q WHERE q.job_id=j.job_id))",
        params![artifact, job, hash], |r| r.get(0))?;
    if !valid {
        return Err(invalid(
            "Review requires its original completed, immutable-bound export",
        ));
    }
    Ok(())
}

fn version(
    db: &Connection,
    job: &str,
    revision: &str,
    artifact: &str,
    original: &str,
) -> Result<ReviewGraphState> {
    let row: (String, String, String, String, Option<String>, i64, i64) = db.query_row(
        "SELECT snapshot_json,snapshot_sha256,graph_json,graph_sha256,undo_target_revision,undo_depth,payload_bytes FROM review_graph_revisions WHERE job_id=?1 AND revision=?2",
        params![job,revision], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?)))?;
    let revision = decimal(revision)?;
    let target = row.4.as_deref().map(decimal).transpose()?;
    if digest(&row.0) != row.1
        || digest(&row.2) != row.3
        || payload_bytes(&row.0, &row.2)?
            != u64::try_from(row.6).map_err(|_| invalid("invalid review byte count"))?
        || !(0..=i64::from(MAX_UNDO)).contains(&row.5)
        || (row.5 == 0) != target.is_none()
        || target.map(|t| t >= revision).unwrap_or(false)
    {
        return Err(invalid("stored Review version is inconsistent"));
    }
    Ok(ReviewGraphState {
        token: ReviewToken {
            job_id: job.into(),
            revision,
            snapshot_sha256: row.1,
            graph_sha256: row.3,
            original_manifest_sha256: original.into(),
        },
        original_artifact_id: artifact.into(),
        snapshot_json: row.0,
        graph_json: row.2,
        undo_target_revision: target,
        undo_depth: row.5 as u16,
    })
}

fn active(db: &Connection, job: &str) -> Result<Option<ReviewGraphState>> {
    let head: Option<(String,String,String)> = db.query_row(
        "SELECT revision,original_artifact_id,original_manifest_sha256 FROM review_graph_heads WHERE job_id=?1",
        params![job], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
    head.map(|(revision, artifact, original)| {
        original_binding(db, job, &artifact, &original)?;
        version(db, job, &revision, &artifact, &original)
    })
    .transpose()
}

fn expected(db: &Connection, token: &ReviewToken) -> Result<ReviewGraphState> {
    validate_id(&token.job_id, "job_id", MAX_ID_CHARS)?;
    require_schema(db)?;
    let current =
        active(db, &token.job_id)?.ok_or_else(|| invalid("Review graph is not initialized"))?;
    if current.token != *token {
        return Err(invalid("Review head changed; reload before mutation"));
    }
    Ok(current)
}

fn budget(
    db: &Connection,
    job: &str,
    added_bytes: u64,
    mut target: Option<u64>,
    mut depth: u16,
    limit: u64,
) -> Result<()> {
    let retained: i64 = db.query_row(
        "SELECT COALESCE(SUM(payload_bytes),0) FROM review_graph_revisions WHERE job_id=?1",
        params![job],
        |r| r.get(0),
    )?;
    let mut total = u64::try_from(retained)
        .map_err(|_| invalid("invalid review history byte count"))?
        .checked_add(added_bytes)
        .ok_or_else(|| invalid("review history byte overflow"))?;
    let mut seen = HashSet::new();
    while let Some(revision) = target {
        if depth == 0 || !seen.insert(revision) {
            return Err(invalid("invalid Review undo chain"));
        }
        let row: (i64,Option<String>,i64) = db.query_row(
            "SELECT payload_bytes,undo_target_revision,undo_depth FROM review_graph_revisions WHERE job_id=?1 AND revision=?2",
            params![job,revision.to_string()], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?)))?;
        depth -= 1;
        if row.2 != i64::from(depth) || row.0 <= 0 {
            return Err(invalid("invalid Review undo depth/bytes"));
        }
        total = total
            .checked_add(row.0 as u64)
            .and_then(|n| n.checked_add(UNDO_REVISION_GROWTH))
            .ok_or_else(|| invalid("review history reservation overflow"))?;
        target = row.1.as_deref().map(decimal).transpose()?;
        if target.map(|t| t >= revision).unwrap_or(false) {
            return Err(invalid("invalid Review undo ancestry"));
        }
    }
    if depth != 0 || total > limit {
        return Err(invalid(
            "Review metadata budget exhausted; prior graph and undo remain valid",
        ));
    }
    Ok(())
}

fn insert(db: &Connection, state: &ReviewGraphState, now: i64) -> Result<()> {
    db.execute("INSERT INTO review_graph_revisions(job_id,revision,snapshot_json,snapshot_sha256,graph_json,graph_sha256,undo_target_revision,undo_depth,payload_bytes,created_at_ms) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
        params![state.token.job_id,state.token.revision.to_string(),state.snapshot_json,state.token.snapshot_sha256,
            state.graph_json,state.token.graph_sha256,state.undo_target_revision.map(|r| r.to_string()),
            i64::from(state.undo_depth),payload_bytes(&state.snapshot_json,&state.graph_json)? as i64,now])?;
    Ok(())
}

fn successor(
    current: &ReviewGraphState,
    snapshot: &str,
    graph: &str,
    target: Option<u64>,
    depth: u16,
) -> Result<ReviewGraphState> {
    payload_bytes(snapshot, graph)?;
    let revision = current
        .token
        .revision
        .checked_add(1)
        .ok_or_else(|| invalid("Review revision is exhausted"))?;
    Ok(ReviewGraphState {
        token: ReviewToken {
            job_id: current.token.job_id.clone(),
            revision,
            snapshot_sha256: digest(snapshot),
            graph_sha256: digest(graph),
            original_manifest_sha256: current.token.original_manifest_sha256.clone(),
        },
        original_artifact_id: current.original_artifact_id.clone(),
        snapshot_json: snapshot.into(),
        graph_json: graph.into(),
        undo_target_revision: target,
        undo_depth: depth,
    })
}

fn publish(
    db: &Connection,
    before: &ReviewGraphState,
    after: &ReviewGraphState,
    now: i64,
) -> Result<()> {
    budget(
        db,
        &before.token.job_id,
        payload_bytes(&after.snapshot_json, &after.graph_json)?,
        after.undo_target_revision,
        after.undo_depth,
        MAX_BYTES,
    )?;
    insert(db, after, now)?;
    let changed = db.execute("UPDATE review_graph_heads SET revision=?3 WHERE job_id=?1 AND revision=?2 AND original_artifact_id=?4 AND original_manifest_sha256=?5",
        params![before.token.job_id,before.token.revision.to_string(),after.token.revision.to_string(),before.original_artifact_id,before.token.original_manifest_sha256])?;
    if changed != 1 {
        return Err(invalid("Review head changed; reload before mutation"));
    }
    Ok(())
}

impl DurableStore {
    /// Explicit optional migration. Core schema2 and existing rows stay intact.
    pub fn initialize_review_schema(&self) -> Result<()> {
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        if schema(&tx)?.is_none() {
            tx.execute_batch(SQL)?;
            tx.execute(
                "INSERT INTO review_schema_migrations(version) VALUES (?1)",
                params![SCHEMA],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    /// Observation never initializes/migrates the optional feature.
    pub fn review_graph_state(&self, job: &str) -> Result<Option<ReviewGraphState>> {
        validate_id(job, "job_id", MAX_ID_CHARS)?;
        if schema(&self.connection)?.is_none() {
            return Ok(None);
        }
        active(&self.connection, job)
    }

    /// The caller has validated the completed manifest, snapshot and graph.
    /// This does not import a legacy sidecar or grant worker publication rights.
    pub fn create_review_graph(
        &self,
        job: &str,
        artifact: &str,
        original: &str,
        snapshot: &str,
        graph: &str,
        now_ms: u64,
    ) -> Result<ReviewGraphState> {
        let bytes = payload_bytes(snapshot, graph)?;
        let now = to_i64(now_ms, "now_ms")?;
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        require_schema(&tx)?;
        original_binding(&tx, job, artifact, original)?;
        if active(&tx, job)?.is_some() {
            return Err(invalid("Review graph already exists; reload"));
        }
        budget(&tx, job, bytes, None, 0, MAX_BYTES)?;
        let state = ReviewGraphState {
            token: ReviewToken {
                job_id: job.into(),
                revision: 0,
                snapshot_sha256: digest(snapshot),
                graph_sha256: digest(graph),
                original_manifest_sha256: original.into(),
            },
            original_artifact_id: artifact.into(),
            snapshot_json: snapshot.into(),
            graph_json: graph.into(),
            undo_target_revision: None,
            undo_depth: 0,
        };
        insert(&tx, &state, now)?;
        tx.execute("INSERT INTO review_graph_heads(job_id,revision,original_artifact_id,original_manifest_sha256) VALUES (?1,'0',?2,?3)",params![job,artifact,original])?;
        tx.commit()?;
        Ok(state)
    }

    /// Only after native closed-schema/provenance/physical graph admission.
    pub fn commit_review_graph(
        &self,
        token: &ReviewToken,
        snapshot: &str,
        graph: &str,
        now_ms: u64,
    ) -> Result<ReviewGraphState> {
        let now = to_i64(now_ms, "now_ms")?;
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let current = expected(&tx, token)?;
        let depth = current
            .undo_depth
            .checked_add(1)
            .filter(|d| *d <= MAX_UNDO)
            .ok_or_else(|| invalid("Review undo depth limit reached"))?;
        let after = successor(
            &current,
            snapshot,
            graph,
            Some(current.token.revision),
            depth,
        )?;
        publish(&tx, &current, &after, now)?;
        tx.commit()?;
        Ok(after)
    }

    /// Return immutable prior graph for native validation before undo. CAS is
    /// checked again when mutation runs; this reader authorizes no file reuse.
    pub fn review_undo_target(&self, token: &ReviewToken) -> Result<Option<ReviewGraphState>> {
        let current = expected(&self.connection, token)?;
        current
            .undo_target_revision
            .map(|r| {
                version(
                    &self.connection,
                    &token.job_id,
                    &r.to_string(),
                    &current.original_artifact_id,
                    &token.original_manifest_sha256,
                )
            })
            .transpose()
    }

    /// Native caller verifies target media and restores exactly its snapshot
    /// content except new revision. The prior graph bytes are retained here.
    pub fn undo_review_graph(
        &self,
        token: &ReviewToken,
        restored_snapshot: &str,
        now_ms: u64,
    ) -> Result<ReviewGraphState> {
        let now = to_i64(now_ms, "now_ms")?;
        let tx = Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let current = expected(&tx, token)?;
        let target = current
            .undo_target_revision
            .ok_or_else(|| invalid("no Review correction to undo"))?;
        let target = version(
            &tx,
            &token.job_id,
            &target.to_string(),
            &current.original_artifact_id,
            &token.original_manifest_sha256,
        )?;
        if target.undo_depth.checked_add(1) != Some(current.undo_depth)
            || restored_snapshot.len() as u64
                > target.snapshot_json.len() as u64 + UNDO_REVISION_GROWTH
        {
            return Err(invalid(
                "restored Review snapshot exceeds its admitted undo depth/reservation",
            ));
        }
        let after = successor(
            &current,
            restored_snapshot,
            &target.graph_json,
            target.undo_target_revision,
            target.undo_depth,
        )?;
        publish(&tx, &current, &after, now)?;
        tx.commit()?;
        Ok(after)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::{Arc, Barrier};

    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            assert!(self.0.starts_with(std::env::temp_dir()));
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn fixture() -> (Fixture, DurableStore, String) {
        let root = std::env::temp_dir().join(format!(
            "dubflow-review-graph-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("manifest.json");
        fs::write(&path, br#"{"kind":"fixture-export","job_id":"job-r"}"#).unwrap();
        let store = DurableStore::open(root.join("jobs.sqlite3")).unwrap();
        store
            .create_job_with_start_request(
                "job-r",
                "file:///fixture.mp4",
                r#"{"schema_version":1}"#,
                1,
            )
            .unwrap();
        store
            .create_stage("job-r", "export", "fixture-export", 1)
            .unwrap();
        store.start_job("job-r", 2).unwrap();
        store.start_stage("job-r", "export", 3).unwrap();
        store
            .record_artifact_written("manifest", "job-r", "export", &path, None, true, 4)
            .unwrap();
        let hash = store.commit_artifact("manifest", 5).unwrap();
        store.complete_job("job-r", 6).unwrap();
        (Fixture(root), store, hash)
    }

    fn initial(store: &DurableStore, hash: &str) -> ReviewGraphState {
        store.initialize_review_schema().unwrap();
        store
            .create_review_graph(
                "job-r",
                "manifest",
                hash,
                r#"{"revision":"0","text":"Trúc Ly"}"#,
                r#"{"graph":"original"}"#,
                7,
            )
            .unwrap()
    }

    fn count(store: &DurableStore) -> i64 {
        store
            .connection
            .query_row(
                "SELECT COUNT(*) FROM review_graph_revisions WHERE job_id='job-r'",
                [],
                |r| r.get(0),
            )
            .unwrap()
    }

    #[test]
    fn explicit_feature_migration_keeps_core_and_read_only_observation() {
        let (fixture, store, hash) = fixture();
        assert!(store.review_graph_state("job-r").unwrap().is_none());
        assert!(store
            .create_review_graph("job-r", "manifest", &hash, "{}", "{}", 7)
            .is_err());
        let reader = DurableStore::open_read_only(fixture.0.join("jobs.sqlite3")).unwrap();
        assert!(reader.review_graph_state("job-r").unwrap().is_none());
        assert!(reader.initialize_review_schema().is_err());
        assert_eq!(reader.schema_version().unwrap(), 2);
        let zero = initial(&store, &hash);
        store.initialize_review_schema().unwrap();
        assert_eq!(reader.schema_version().unwrap(), 2);
        assert_eq!(
            reader.job_status("job-r").unwrap(),
            super::super::JobStatus::Succeeded
        );
        assert_eq!(
            reader.review_graph_state("job-r").unwrap(),
            Some(zero.clone())
        );
        assert!(reader
            .commit_review_graph(&zero.token, "{}", "{}", 8)
            .is_err());
        assert_eq!(count(&store), 1);
    }

    #[test]
    fn two_corrections_and_two_undos_preserve_graphs_and_prevent_aba() {
        let (fixture, store, hash) = fixture();
        let zero = initial(&store, &hash);
        let artifact = store.artifact("manifest").unwrap();
        let source = fs::read(fixture.0.join("manifest.json")).unwrap();
        let one = store
            .commit_review_graph(
                &zero.token,
                r#"{"revision":"1","text":"Ngồi yên."}"#,
                r#"{"graph":"one"}"#,
                8,
            )
            .unwrap();
        let two = store
            .commit_review_graph(
                &one.token,
                r#"{"revision":"2","text":"Hai"}"#,
                r#"{"graph":"two"}"#,
                9,
            )
            .unwrap();
        assert_eq!(
            store.review_undo_target(&two.token).unwrap(),
            Some(one.clone())
        );
        let three = store
            .undo_review_graph(&two.token, r#"{"revision":"3","text":"Ngồi yên."}"#, 10)
            .unwrap();
        assert_eq!(three.graph_json, one.graph_json);
        assert_eq!(three.undo_depth, 1);
        let four = store
            .undo_review_graph(&three.token, r#"{"revision":"4","text":"Trúc Ly"}"#, 11)
            .unwrap();
        assert_eq!(four.graph_json, zero.graph_json);
        assert_eq!(four.undo_depth, 0);
        assert!(store.review_undo_target(&four.token).unwrap().is_none());
        assert!(store
            .commit_review_graph(&zero.token, "{}", "{}", 12)
            .is_err());
        let mut aba = zero.token.clone();
        aba.revision = four.token.revision;
        assert!(store.commit_review_graph(&aba, "{}", "{}", 12).is_err());
        assert_eq!(count(&store), 5);
        assert_eq!(store.artifact("manifest").unwrap(), artifact);
        assert_eq!(fs::read(fixture.0.join("manifest.json")).unwrap(), source);
    }

    #[test]
    fn every_cas_pin_and_original_job_binding_are_required() {
        let (_fixture, store, hash) = fixture();
        let zero = initial(&store, &hash);
        let mut tokens = vec![zero.token.clone(); 5];
        tokens[0].revision = 1;
        tokens[1].snapshot_sha256 = digest("wrong");
        tokens[2].graph_sha256 = digest("wrong");
        tokens[3].original_manifest_sha256 = digest("wrong");
        tokens[4].job_id = "another-job".into();
        for token in tokens {
            assert!(store.commit_review_graph(&token, "{}", "{}", 8).is_err());
        }
        assert!(store
            .create_review_graph("another-job", "manifest", &hash, "{}", "{}", 8)
            .is_err());
        assert!(store
            .create_review_graph("job-r", "manifest", &digest("wrong"), "{}", "{}", 8)
            .is_err());
        assert_eq!(store.review_graph_state("job-r").unwrap(), Some(zero));
        assert_eq!(count(&store), 1);
    }

    #[test]
    fn sql_failure_after_revision_insert_rolls_back_head_and_undo() {
        let (_fixture, store, hash) = fixture();
        let zero = initial(&store, &hash);
        let trigger="CREATE TRIGGER reject_review_head BEFORE UPDATE ON review_graph_heads BEGIN SELECT RAISE(ABORT,'injected'); END;";
        store.connection.execute_batch(trigger).unwrap();
        assert!(store
            .commit_review_graph(&zero.token, "{}", "{}", 8)
            .is_err());
        assert_eq!(
            store.review_graph_state("job-r").unwrap(),
            Some(zero.clone())
        );
        assert_eq!(count(&store), 1);
        store
            .connection
            .execute_batch("DROP TRIGGER reject_review_head;")
            .unwrap();
        let one = store
            .commit_review_graph(&zero.token, r#"{"revision":"1"}"#, "{}", 9)
            .unwrap();
        store.connection.execute_batch(trigger).unwrap();
        assert!(store
            .undo_review_graph(&one.token, r#"{"revision":"2","text":"Trúc Ly"}"#, 10)
            .is_err());
        assert_eq!(
            store.review_graph_state("job-r").unwrap(),
            Some(one.clone())
        );
        assert_eq!(count(&store), 2);
        assert_eq!(store.review_undo_target(&one.token).unwrap(), Some(zero));
    }

    #[test]
    fn concurrent_connections_admit_only_one_same_token_writer() {
        let (fixture, store, hash) = fixture();
        let zero = initial(&store, &hash);
        let barrier = Arc::new(Barrier::new(2));
        let mut handles = vec![];
        for name in ["first", "second"] {
            let db = fixture.0.join("jobs.sqlite3");
            let token = zero.token.clone();
            let barrier = barrier.clone();
            handles.push(std::thread::spawn(move || {
                let writer = DurableStore::open(db).unwrap();
                barrier.wait();
                writer.commit_review_graph(
                    &token,
                    &format!("{{\"revision\":\"1\",\"writer\":\"{name}\"}}"),
                    "{}",
                    8,
                )
            }));
        }
        let results = handles
            .into_iter()
            .map(|h| h.join().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
        assert_eq!(count(&store), 2);
        let reopened = DurableStore::open_read_only(fixture.0.join("jobs.sqlite3")).unwrap();
        assert_eq!(
            reopened
                .review_graph_state("job-r")
                .unwrap()
                .unwrap()
                .token
                .revision,
            1
        );
    }

    #[test]
    fn decimal_revisions_are_exact_above_js_range_and_overflow_refuses() {
        for revision in [9_007_199_254_740_993, u64::MAX] {
            let (_fixture, store, hash) = fixture();
            let mut wide = initial(&store, &hash);
            wide.token.revision = revision;
            wide.snapshot_json = format!("{{\"revision\":\"{revision}\"}}");
            wide.token.snapshot_sha256 = digest(&wide.snapshot_json);
            insert(&store.connection, &wide, 8).unwrap();
            store
                .connection
                .execute(
                    "UPDATE review_graph_heads SET revision=?1 WHERE job_id='job-r'",
                    params![revision.to_string()],
                )
                .unwrap();
            assert_eq!(
                store.review_graph_state("job-r").unwrap(),
                Some(wide.clone())
            );
            let result = store.commit_review_graph(&wide.token, "{}", "{}", 9);
            if revision == u64::MAX {
                assert!(result.is_err());
                assert_eq!(count(&store), 2);
            } else {
                let after = result.unwrap();
                assert_eq!(after.token.revision, 9_007_199_254_740_994);
                let stored: String = store
                    .connection
                    .query_row(
                        "SELECT revision FROM review_graph_heads WHERE job_id='job-r'",
                        [],
                        |r| r.get(0),
                    )
                    .unwrap();
                assert_eq!(stored, "9007199254740994");
            }
        }
    }

    #[test]
    fn byte_budget_reserves_undo_and_counts_unicode_bytes() {
        let (_fixture, store, hash) = fixture();
        let zero = initial(&store, &hash);
        let bytes = payload_bytes(&zero.snapshot_json, &zero.graph_json).unwrap();
        assert!(
            bytes > (zero.snapshot_json.chars().count() + zero.graph_json.chars().count()) as u64
        );
        assert!(budget(&store.connection, "job-r", 2, Some(0), 1, bytes + 2).is_err());
        budget(
            &store.connection,
            "job-r",
            2,
            Some(0),
            1,
            bytes + 2 + bytes + UNDO_REVISION_GROWTH,
        )
        .unwrap();
        let one = store
            .commit_review_graph(&zero.token, "{}", "{}", 8)
            .unwrap();
        let oversized = format!(
            "{{\"padding\":\"{}\"}}",
            "x".repeat(zero.snapshot_json.len() + 33)
        );
        let error = store
            .undo_review_graph(&one.token, &oversized, 9)
            .unwrap_err();
        assert!(error.to_string().contains("reservation"));
        assert_eq!(store.review_graph_state("job-r").unwrap(), Some(one));
        assert_eq!(count(&store), 2);
    }

    #[test]
    fn unknown_or_partial_feature_refuses_without_breaking_core_reader() {
        let (old_fixture, store, hash) = fixture();
        initial(&store, &hash);
        store
            .connection
            .execute(
                "INSERT INTO review_schema_migrations(version) VALUES (2)",
                [],
            )
            .unwrap();
        assert!(store.review_graph_state("job-r").is_err());
        assert!(store.initialize_review_schema().is_err());
        let reader = DurableStore::open_read_only(old_fixture.0.join("jobs.sqlite3")).unwrap();
        assert_eq!(reader.schema_version().unwrap(), 2);
        assert_eq!(
            reader.job_status("job-r").unwrap(),
            super::super::JobStatus::Succeeded
        );
        let (_fixture, store, _) = fixture();
        store
            .connection
            .execute_batch("CREATE TABLE review_graph_heads(unregistered TEXT);")
            .unwrap();
        assert!(store.review_graph_state("job-r").is_err());
        assert!(store.initialize_review_schema().is_err());
        assert_eq!(store.schema_version().unwrap(), 2);
    }
}
