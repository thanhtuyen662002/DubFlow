//! Read the supervisor's committed export identity without changing job state.
use super::{emit_value, SupervisorError, SupervisorResult, STAGE_ID};
use dubflow_job_state::{ArtifactState, DurableStore, JobStatus, StageStatus};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

const MANIFEST_LIMIT: u64 = 64 * 1024 * 1024;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

pub(super) fn plain(path: &Path) -> SupervisorResult<()> {
    if !path.is_absolute() {
        return Err(invalid("review path must be absolute"));
    }
    for node in path.ancestors() {
        let metadata = fs::symlink_metadata(node)?;
        if metadata.file_type().is_symlink() {
            return Err(invalid("review path cannot use links"));
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err(invalid("review path cannot use reparse points"));
            }
        }
    }
    Ok(())
}

fn trusted_pin(store: &DurableStore, job_id: &str) -> SupervisorResult<Value> {
    super::validate_job_id(job_id)?;
    if !job_id
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
    {
        return Err(invalid(
            "job ID cannot be represented by the review contract",
        ));
    }
    if store.job_status(job_id)? != JobStatus::Succeeded
        || store.stage_status(job_id, STAGE_ID)? != StageStatus::Succeeded
    {
        return Err(invalid("review requires a completed job and stage"));
    }
    let request = store
        .job_start_request(job_id)?
        .ok_or_else(|| invalid("legacy job has no immutable output binding"))?;
    let request: Value = serde_json::from_str(&request)?;
    if request.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return Err(invalid("unsupported immutable start request"));
    }
    let output = super::absolute_path(
        request
            .get("output_dir")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("output binding is missing"))?,
        "output_dir",
    )?;
    let manifest_path = output.join("job_manifest.json");
    let attempt = store.stage_history(job_id, STAGE_ID)?.attempt;
    if !(1..=super::MAX_ATTEMPTS).contains(&attempt) {
        return Err(invalid("unsupported production attempt"));
    }
    let artifact = store.artifact(&format!("{job_id}-{STAGE_ID}-job-manifest-a{attempt}"))?;
    let size = artifact
        .size_bytes
        .ok_or_else(|| invalid("manifest size is not committed"))?;
    if artifact.job_id != job_id
        || artifact.stage_id != STAGE_ID
        || artifact.state != ArtifactState::Committed
        || Path::new(&artifact.path) != manifest_path
        || size == 0
        || size > MANIFEST_LIMIT
    {
        return Err(invalid("manifest is not the current committed export"));
    }
    plain(&manifest_path)?;
    if !fs::symlink_metadata(&manifest_path)?.file_type().is_file() {
        return Err(invalid("manifest is not a regular file"));
    }
    let mut bytes = Vec::new();
    fs::File::open(&manifest_path)?
        .take(MANIFEST_LIMIT + 1)
        .read_to_end(&mut bytes)?;
    let hash = format!("sha256:{:x}", Sha256::digest(&bytes));
    if bytes.len() as u64 != size || artifact.content_hash.as_deref() != Some(hash.as_str()) {
        return Err(invalid("committed review manifest bytes changed"));
    }
    let document: Value = serde_json::from_slice(&bytes)?;
    if document.get("schema_version").and_then(Value::as_u64) != Some(1)
        || document.get("kind").and_then(Value::as_str) != Some("dubflow_localization_job")
        || document.get("job_id").and_then(Value::as_str) != Some(job_id)
    {
        return Err(invalid("committed manifest identity/version is invalid"));
    }
    Ok(
        json!({"schema_version":1,"kind":"review_export_pin","job_id":job_id,
              "manifest_path":manifest_path,"manifest_sha256":hash,"manifest_size_bytes":size,"attempt":attempt}),
    )
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let mut data_root = None;
    let mut job_id = None;
    while let Some(flag) = args.next() {
        match flag.to_str() {
            Some("--data-root") if data_root.is_none() => {
                data_root = Some(PathBuf::from(
                    args.next().ok_or_else(|| invalid("missing data root"))?,
                ))
            }
            Some("--job-id") if job_id.is_none() => {
                job_id = Some(
                    args.next()
                        .ok_or_else(|| invalid("missing job ID"))?
                        .into_string()
                        .map_err(|_| invalid("job ID is not UTF-8"))?,
                )
            }
            _ => return Err(invalid("unknown or duplicate review-pin argument")),
        }
    }
    let data_root = data_root.ok_or_else(|| invalid("review-pin requires --data-root"))?;
    let job_id = job_id.ok_or_else(|| invalid("review-pin requires --job-id"))?;
    plain(&data_root)?;
    if !data_root.is_dir() {
        return Err(invalid("data root is not a directory"));
    }
    let db = data_root.join("control/jobs.sqlite3");
    plain(&db)?;
    let store = DurableStore::open_read_only(db)?;
    let value = trusted_pin(&store, &job_id)?;
    emit_value(&mut io::stdout().lock(), value)
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture(output_binding: Option<&str>) -> (Fixture, DurableStore, PathBuf) {
        let root = std::env::temp_dir().join(format!(
            "dubflow-review-pin-{}-{}-{}",
            std::process::id(),
            super::super::now_ms(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        fs::create_dir_all(&root).unwrap();
        let manifest = root.join("job_manifest.json");
        fs::write(
            &manifest,
            br#"{"schema_version":1,"kind":"dubflow_localization_job","job_id":"job-pin","note":"a"}"#,
        )
        .unwrap();
        let store = DurableStore::open_in_memory().unwrap();
        let output = output_binding
            .map(PathBuf::from)
            .unwrap_or_else(|| root.clone());
        store
            .create_job_with_start_request(
                "job-pin",
                "file:///source.mp4",
                &json!({"schema_version":1,"output_dir":output}).to_string(),
                1,
            )
            .unwrap();
        store
            .create_stage("job-pin", STAGE_ID, "production-local-file", 3)
            .unwrap();
        store.start_job("job-pin", 2).unwrap();
        store.start_stage("job-pin", STAGE_ID, 3).unwrap();
        store
            .record_artifact_written(
                "job-pin-local-file-job-manifest-a1",
                "job-pin",
                STAGE_ID,
                &manifest,
                None,
                true,
                4,
            )
            .unwrap();
        store
            .commit_artifact("job-pin-local-file-job-manifest-a1", 5)
            .unwrap();
        store.complete_job("job-pin", 6).unwrap();
        (Fixture(root), store, manifest)
    }
    #[test]
    fn completed_identity_comes_from_committed_bytes_not_a_caller_path() {
        let (_fixture, store, path) = fixture(None);
        let pin = trusted_pin(&store, "job-pin").unwrap();
        assert_eq!(pin["manifest_path"], json!(path));
        assert_eq!(pin["attempt"], 1);
        assert_eq!(
            pin["manifest_sha256"],
            format!("sha256:{:x}", Sha256::digest(fs::read(&path).unwrap()))
        );
        let original_size = fs::metadata(&path).unwrap().len();
        fs::write(&path, br#"{"schema_version":1,"kind":"dubflow_localization_job","job_id":"job-pin","note":"b"}"#).unwrap();
        assert_eq!(fs::metadata(&path).unwrap().len(), original_size);
        assert!(trusted_pin(&store, "job-pin").is_err());
        assert_eq!(store.job_status("job-pin").unwrap(), JobStatus::Succeeded);
    }
    #[test]
    fn mismatched_immutable_output_and_unfinished_jobs_refuse() {
        let (_fixture, store, _path) = fixture(Some("relative/other"));
        assert!(trusted_pin(&store, "job-pin").is_err());
        store
            .create_job("unfinished", "file:///other.mp4", 10)
            .unwrap();
        assert!(trusted_pin(&store, "unfinished").is_err());
        assert!(trusted_pin(&store, "unknown").is_err());
    }
    #[test]
    fn pin_command_rejects_duplicate_or_missing_arguments_before_opening_state() {
        for args in [
            vec![],
            vec!["--job-id", "a", "--job-id", "b"],
            vec!["--manifest", "other"],
            vec!["--data-root"],
        ] {
            assert!(run(args.into_iter().map(OsString::from)).is_err());
        }
    }
}
