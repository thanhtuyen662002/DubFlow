//! Single-writer project ownership lock with explicit read-only/handoff modes.
//!
//! The lock file is deliberately separate from SQLite. `create_new` is the
//! ownership primitive; metadata is written and flushed before acquisition is
//! returned. Stale reclamation requires a caller-supplied PID/start-token
//! process probe and preserves the old record under a unique `.stale-*` name.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

pub const SCHEMA_VERSION: u32 = 1;
pub const LOCK_SCOPE: &str = "project-db-artifacts-temp";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OwnerRole {
    Supervisor,
    Worker,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockOwner {
    pub role: OwnerRole,
    pub pid: u32,
    pub start_token: String,
}

impl LockOwner {
    pub fn supervisor(pid: u32, start_token: impl Into<String>) -> Self {
        Self { role: OwnerRole::Supervisor, pid, start_token: start_token.into() }
    }

    fn validate(&self) -> Result<(), LockError> {
        if self.role != OwnerRole::Supervisor {
            return Err(LockError::WorkerCannotOwnProject);
        }
        if self.pid == 0 || self.start_token.is_empty() || self.start_token.len() > 128 || !self.start_token.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-')) {
            return Err(LockError::InvalidOwner);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockMetadata {
    pub owner: LockOwner,
    pub acquired_epoch_ms: u64,
}

impl LockMetadata {
    pub fn encode(&self) -> String {
        format!(
            "{{\"schema_version\":1,\"role\":\"supervisor\",\"pid\":\"{}\",\"start_token\":\"{}\",\"scope\":\"{}\",\"acquired_epoch_ms\":\"{}\"}}\n",
            self.owner.pid, self.owner.start_token, LOCK_SCOPE, self.acquired_epoch_ms
        )
    }

    pub fn decode(value: &str) -> Result<Self, LockError> {
        let schema = field(value, "schema_version")?;
        if schema != "1" || field(value, "role")? != "supervisor" || field(value, "scope")? != LOCK_SCOPE {
            return Err(LockError::CorruptMetadata);
        }
        let pid = field(value, "pid")?.parse::<u32>().map_err(|_| LockError::CorruptMetadata)?;
        let start_token = field(value, "start_token")?;
        let acquired_epoch_ms = field(value, "acquired_epoch_ms")?.parse::<u64>().map_err(|_| LockError::CorruptMetadata)?;
        let owner = LockOwner::supervisor(pid, start_token);
        owner.validate()?;
        Ok(Self { owner, acquired_epoch_ms })
    }
}

fn field<'a>(value: &'a str, name: &str) -> Result<&'a str, LockError> {
    let quoted_marker = format!("\"{name}\":\"");
    if let Some(start) = value.find(&quoted_marker) {
        let start = start + quoted_marker.len();
        let end = value[start..].find('"').ok_or(LockError::CorruptMetadata)? + start;
        return Ok(&value[start..end]);
    }
    let numeric_marker = format!("\"{name}\":");
    let start = value.find(&numeric_marker).ok_or(LockError::CorruptMetadata)? + numeric_marker.len();
    let end = value[start..].find(|character: char| matches!(character, ',' | '}')).ok_or(LockError::CorruptMetadata)? + start;
    let value = value[start..end].trim();
    if value.is_empty() { return Err(LockError::CorruptMetadata); }
    Ok(value)
}

pub trait ProcessProbe {
    fn is_live(&self, owner: &LockOwner) -> bool;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExistingLockPolicy {
    Fail,
    ReadOnly,
    RequestHandoff,
}

#[derive(Debug, PartialEq, Eq)]
pub enum OpenResult {
    Acquired,
    ReadOnly(LockMetadata),
    HandoffRequested(LockMetadata),
}

#[derive(Debug)]
pub struct ProjectLock {
    path: PathBuf,
    owner: LockOwner,
    file: Option<File>,
}

impl ProjectLock {
    pub fn path(&self) -> &Path { &self.path }
    pub fn owner(&self) -> &LockOwner { &self.owner }

    /// Release only if the on-disk token still belongs to this handle.
    pub fn release(mut self) -> Result<(), LockError> {
        self.release_inner()
    }

    fn release_inner(&mut self) -> Result<(), LockError> {
        self.file.take();
        let metadata = fs::read_to_string(&self.path).map_err(LockError::Io)?;
        let current = LockMetadata::decode(&metadata)?;
        if current.owner != self.owner {
            return Err(LockError::OwnershipChanged);
        }
        fs::remove_file(&self.path).map_err(LockError::Io)
    }
}

impl Drop for ProjectLock {
    fn drop(&mut self) {
        let _ = self.release_inner();
    }
}

#[derive(Debug)]
pub enum LockError {
    Io(io::Error),
    AlreadyOwned(LockMetadata),
    CorruptMetadata,
    InvalidOwner,
    WorkerCannotOwnProject,
    OwnershipChanged,
    ReclaimRace,
}

impl From<io::Error> for LockError {
    fn from(error: io::Error) -> Self { Self::Io(error) }
}

/// Open a project lock under an explicit second-instance policy.
pub fn open<P: AsRef<Path>, Q: ProcessProbe>(
    path: P,
    owner: LockOwner,
    now_epoch_ms: u64,
    probe: &Q,
    policy: ExistingLockPolicy,
) -> Result<(OpenResult, Option<ProjectLock>), LockError> {
    owner.validate()?;
    let path = path.as_ref().to_path_buf();
    for attempt in 0..=1 {
        match create_lock(&path, &owner, now_epoch_ms) {
            Ok((file, _metadata)) => {
                return Ok((OpenResult::Acquired, Some(ProjectLock { path, owner, file: Some(file) })));
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(LockError::Io(error)),
        }

        let current = read_metadata(&path)?;
        if probe.is_live(&current.owner) {
            return match policy {
                ExistingLockPolicy::Fail => Err(LockError::AlreadyOwned(current)),
                ExistingLockPolicy::ReadOnly => Ok((OpenResult::ReadOnly(current), None)),
                ExistingLockPolicy::RequestHandoff => Ok((OpenResult::HandoffRequested(current), None)),
            };
        }
        if attempt == 1 {
            return Err(LockError::ReclaimRace);
        }
        let stale_path = stale_path(&path, &owner, now_epoch_ms);
        match fs::rename(&path, &stale_path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(LockError::Io(error)),
        }
    }
    Err(LockError::ReclaimRace)
}

fn create_lock(path: &Path, owner: &LockOwner, now_epoch_ms: u64) -> io::Result<(File, LockMetadata)> {
    let metadata = LockMetadata { owner: owner.clone(), acquired_epoch_ms: now_epoch_ms };
    let mut file = OpenOptions::new().write(true).read(true).create_new(true).open(path)?;
    file.write_all(metadata.encode().as_bytes())?;
    file.sync_all()?;
    Ok((file, metadata))
}

fn read_metadata(path: &Path) -> Result<LockMetadata, LockError> {
    let mut file = File::open(path).map_err(LockError::Io)?;
    let mut value = String::new();
    file.read_to_string(&mut value).map_err(LockError::Io)?;
    LockMetadata::decode(&value)
}

fn stale_path(path: &Path, owner: &LockOwner, now_epoch_ms: u64) -> PathBuf {
    let name = path.file_name().and_then(|name| name.to_str()).unwrap_or("project.lock");
    path.with_file_name(format!("{name}.stale-{}-{now_epoch_ms}-{}", owner.pid, owner.start_token))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    use std::sync::Mutex;

    struct FakeProbe { live: Mutex<BTreeSet<(u32, String)>> }
    impl FakeProbe {
        fn with(owner: &LockOwner) -> Self { let mut live = BTreeSet::new(); live.insert((owner.pid, owner.start_token.clone())); Self { live: Mutex::new(live) } }
        fn mark_dead(&self, owner: &LockOwner) { self.live.lock().unwrap().remove(&(owner.pid, owner.start_token.clone())); }
    }
    impl ProcessProbe for FakeProbe { fn is_live(&self, owner: &LockOwner) -> bool { self.live.lock().unwrap().contains(&(owner.pid, owner.start_token.clone())) } }

    fn temp_lock(name: &str) -> PathBuf { std::env::temp_dir().join(format!("dubflow-{name}-{}-{}.lock", std::process::id(), 1u64)) }

    #[test]
    fn second_supervisor_is_denied_or_explicitly_read_only_or_handoff() {
        let path = temp_lock("owner"); let _ = fs::remove_file(&path);
        let first = LockOwner::supervisor(101, "start-a"); let second = LockOwner::supervisor(202, "start-b"); let probe = FakeProbe::with(&first);
        let (_, lock) = open(&path, first.clone(), 1, &probe, ExistingLockPolicy::Fail).unwrap();
        assert!(matches!(open(&path, second.clone(), 2, &probe, ExistingLockPolicy::Fail), Err(LockError::AlreadyOwned(_))));
        assert!(matches!(open(&path, second.clone(), 2, &probe, ExistingLockPolicy::ReadOnly), Ok((OpenResult::ReadOnly(_), None))));
        assert!(matches!(open(&path, second, 2, &probe, ExistingLockPolicy::RequestHandoff), Ok((OpenResult::HandoffRequested(_), None))));
        drop(lock); let _ = fs::remove_file(&path);
    }

    #[test]
    fn stale_lock_is_reclaimed_only_after_process_probe_and_old_record_survives() {
        let path = temp_lock("stale"); let _ = fs::remove_file(&path);
        let old = LockOwner::supervisor(303, "old"); let new = LockOwner::supervisor(404, "new"); let probe = FakeProbe::with(&old);
        let (_, lock) = open(&path, old.clone(), 10, &probe, ExistingLockPolicy::Fail).unwrap(); drop(lock);
        let _ = fs::write(&path, LockMetadata { owner: old.clone(), acquired_epoch_ms: 10 }.encode());
        probe.mark_dead(&old);
        let (_, acquired) = open(&path, new.clone(), 20, &probe, ExistingLockPolicy::Fail).unwrap();
        assert!(acquired.is_some());
        assert!(fs::read_dir(path.parent().unwrap()).unwrap().flatten().any(|entry| entry.file_name().to_string_lossy().contains(".stale-404-20")));
        drop(acquired); let _ = fs::remove_file(&path);
        for entry in fs::read_dir(path.parent().unwrap()).unwrap().flatten() { if entry.file_name().to_string_lossy().contains(".stale-404-20") { let _ = fs::remove_file(entry.path()); } }
    }

    #[test]
    fn corrupt_or_worker_owners_are_never_accepted() {
        let path = temp_lock("corrupt"); let _ = fs::remove_file(&path); fs::write(&path, b"not-json").unwrap();
        let owner = LockOwner::supervisor(505, "new"); let probe = FakeProbe::with(&owner);
        assert!(matches!(open(&path, owner.clone(), 1, &probe, ExistingLockPolicy::Fail), Err(LockError::CorruptMetadata)));
        let worker = LockOwner { role: OwnerRole::Worker, pid: 1, start_token: "worker".to_owned() };
        assert!(matches!(open(&path, worker, 1, &probe, ExistingLockPolicy::Fail), Err(LockError::WorkerCannotOwnProject)));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn release_is_token_checked_and_hard_kill_leaves_metadata_recoverable() {
        let path = temp_lock("release"); let _ = fs::remove_file(&path);
        let owner = LockOwner::supervisor(606, "start"); let other = LockOwner::supervisor(707, "other"); let probe = FakeProbe::with(&owner);
        let (_, mut lock) = open(&path, owner.clone(), 1, &probe, ExistingLockPolicy::Fail).unwrap();
        lock.as_mut().unwrap().file.take();
        let lock = lock.unwrap();
        fs::write(&path, LockMetadata { owner: other.clone(), acquired_epoch_ms: 2 }.encode()).unwrap();
        assert!(matches!(lock.release(), Err(LockError::OwnershipChanged)));
        assert!(path.exists());
        let _ = fs::remove_file(path);
    }
}
