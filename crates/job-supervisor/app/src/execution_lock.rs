//! Kernel-owned execution exclusion; an empty retained file is not a live lease.

use crate::{SupervisorError, SupervisorResult};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions, TryLockError};
use std::path::Path;

/// Never clone this handle or unlink its file: either would weaken exclusion.
pub(super) struct JobExecutionGuard {
    _file: File,
}

impl JobExecutionGuard {
    pub(super) fn acquire(db: &Path, job_id: &str) -> SupervisorResult<Self> {
        let parent = db.parent().ok_or_else(|| {
            SupervisorError::Invalid("job database must have a parent directory".into())
        })?;
        fs::create_dir_all(parent)?;
        // Ensure canonical identity even for a new database and alternate path
        // spellings. This creates only an empty file, never SQL state or rows.
        let db_file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(db)?;
        let canonical = fs::canonicalize(db)?;
        drop(db_file);
        let directory = canonical
            .parent()
            .expect("canonical database has parent")
            .join(".execution-locks");
        fs::create_dir_all(&directory)?;
        let mut key = Sha256::new();
        key.update(canonical.as_os_str().as_encoded_bytes());
        key.update([0]);
        key.update(job_id.as_bytes());
        let path = directory.join(format!("{:x}.lock", key.finalize()));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        match file.try_lock() {
            Ok(()) => Ok(Self { _file: file }),
            Err(TryLockError::WouldBlock) => Err(SupervisorError::Worker {
                code: "JOB_ALREADY_RUNNING".into(),
                condition: "another supervisor owns this job execution".into(),
                retryable: false,
            }),
            Err(TryLockError::Error(error)) => Err(SupervisorError::Io(error)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn database() -> std::path::PathBuf {
        std::env::temp_dir()
            .join(format!(
                "dubflow-execution-lock-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ))
            .join("jobs.sqlite3")
    }

    #[test]
    fn independent_handles_reject_same_job_and_release_on_drop() {
        let db = database();
        let guard = JobExecutionGuard::acquire(&db, "job-one").unwrap();
        let error = JobExecutionGuard::acquire(&db, "job-one").err().unwrap();
        assert!(
            matches!(error, SupervisorError::Worker { ref code, retryable: false, .. }
            if code == "JOB_ALREADY_RUNNING")
        );
        let other = JobExecutionGuard::acquire(&db, "job-two").unwrap();
        drop(other);
        drop(guard);
        let _reclaimed = JobExecutionGuard::acquire(&db, "job-one").unwrap();
    }

    #[test]
    fn database_alias_and_job_path_text_do_not_bypass_execution_exclusion() {
        let db = database();
        let _guard = JobExecutionGuard::acquire(&db, "../same-job").unwrap();
        let alias = db.parent().unwrap().join(".").join("jobs.sqlite3");
        assert!(JobExecutionGuard::acquire(&alias, "../same-job").is_err());
        assert!(!db.parent().unwrap().join("same-job.lock").exists());
        let _separate_database =
            JobExecutionGuard::acquire(&db.with_file_name("other.sqlite3"), "../same-job").unwrap();
    }

    #[test]
    #[ignore = "helper is executed in a child by the hard-kill regression"]
    fn child_holds_kernel_lease() {
        use std::io::Write;
        let db = std::env::var_os("DUBFLOW_LOCK_TEST_DATABASE").expect("child database");
        let _guard = JobExecutionGuard::acquire(Path::new(&db), "hard-kill-job").unwrap();
        println!("execution-lease-ready");
        std::io::stdout().flush().unwrap();
        loop {
            std::thread::park();
        }
    }

    #[test]
    fn another_process_blocks_and_hard_kill_releases_without_stale_reclamation() {
        use std::io::{BufRead, BufReader};
        use std::process::{Command, Stdio};
        use std::time::Duration;
        let db = database();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "execution_lock::tests::child_holds_kernel_lease",
                "--ignored",
                "--nocapture",
            ])
            .env("DUBFLOW_LOCK_TEST_DATABASE", &db)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, rx) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                if line.unwrap_or_default() == "execution-lease-ready" {
                    let _ = tx.send(());
                    break;
                }
            }
        });
        let ready = rx.recv_timeout(Duration::from_secs(10)).is_ok();
        let refused = if ready {
            matches!(JobExecutionGuard::acquire(&db, "hard-kill-job"),
                Err(SupervisorError::Worker { ref code, retryable: false, .. }) if code == "JOB_ALREADY_RUNNING")
        } else {
            false
        };
        let killed = child.kill();
        let _ = child.wait();
        reader.join().unwrap();
        assert!(
            ready,
            "owned child never reported its acquired kernel lease"
        );
        assert!(refused, "another process bypassed the live execution lease");
        killed.unwrap();
        let _after_death = JobExecutionGuard::acquire(&db, "hard-kill-job").unwrap();
    }
}
