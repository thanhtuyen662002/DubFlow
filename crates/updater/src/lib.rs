//! Atomic app/engine/model update controller.
//!
//! The implementation deliberately uses only the Rust standard library. The
//! updater never mutates the live version in place: it stages files under a
//! temporary directory, verifies them, atomically publishes a version pointer,
//! health-checks the candidate, and restores the old pointer on any failure.

use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};

pub const STATE_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateFile { pub relative_path: String, pub bytes: Vec<u8> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdatePackage { pub version: String, pub digest: String, pub files: Vec<UpdateFile> }

impl UpdatePackage {
    fn validate(&self) -> Result<(), UpdateError> {
        if !safe_component(&self.version) || self.digest.is_empty() || self.files.is_empty() { return Err(UpdateError::InvalidPackage("version, digest and files are required".into())); }
        let mut seen = BTreeSet::new();
        for file in &self.files {
            if !safe_relative_path(&file.relative_path) || !seen.insert(file.relative_path.clone()) { return Err(UpdateError::InvalidPackage("package file path is unsafe or duplicated".into())); }
        }
        Ok(())
    }
}

pub trait PackageVerifier { fn verify(&self, package: &UpdatePackage) -> Result<(), UpdateError>; }
pub trait Healthcheck { fn check(&self, version_root: &Path) -> Result<(), UpdateError>; }
pub trait MigrationHooks {
    fn backup(&self, root: &Path) -> Result<(), UpdateError>;
    fn prepare(&self, root: &Path, from: Option<&str>, to: &str) -> Result<(), UpdateError>;
    fn validate(&self, root: &Path, to: &str) -> Result<(), UpdateError>;
    fn commit(&self, root: &Path, to: &str) -> Result<(), UpdateError>;
    fn rollback(&self, root: &Path, from: Option<&str>, to: &str);
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateError { Io(String), InvalidPackage(String), Verification(String), Healthcheck(String), Migration(String), LockHeld, Recovery(String) }
impl From<io::Error> for UpdateError { fn from(error: io::Error) -> Self { Self::Io(error.to_string()) } }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateResult { pub from_version: Option<String>, pub to_version: String, pub committed: bool, pub rolled_back: bool }

struct WriterLease { path: PathBuf, _file: File }
impl WriterLease {
    fn acquire(path: &Path) -> Result<Self, UpdateError> {
        let file = OpenOptions::new().write(true).create_new(true).open(path).map_err(|error| if error.kind() == io::ErrorKind::AlreadyExists { UpdateError::LockHeld } else { UpdateError::Io(error.to_string()) })?;
        Ok(Self { path: path.to_path_buf(), _file: file })
    }
}
impl Drop for WriterLease { fn drop(&mut self) { let _ = fs::remove_file(&self.path); } }

pub struct Updater<V, H, M> { root: PathBuf, verifier: V, healthcheck: H, migrations: M }

impl<V, H, M> Updater<V, H, M>
where V: PackageVerifier, H: Healthcheck, M: MigrationHooks {
    pub fn new(root: impl Into<PathBuf>, verifier: V, healthcheck: H, migrations: M) -> Self { Self { root: root.into(), verifier, healthcheck, migrations } }

    pub fn apply(&self, package: &UpdatePackage, pinned_versions: &[String]) -> Result<UpdateResult, UpdateError> {
        package.validate()?;
        fs::create_dir_all(self.versions_root())?;
        fs::create_dir_all(self.staging_root())?;
        let _lease = WriterLease::acquire(&self.root.join("updater.writer.lock"))?;
        let from = self.current_version()?;
        if from.as_deref() == Some(package.version.as_str()) { return Ok(UpdateResult { from_version: from, to_version: package.version.clone(), committed: true, rolled_back: false }); }
        self.write_state("staging", from.as_deref(), &package.version)?;
        self.verifier.verify(package)?;
        let staged = self.stage(package)?;
        self.write_state("verified", from.as_deref(), &package.version)?;
        self.migrations.backup(&self.root).map_err(|error| UpdateError::Migration(format!("backup: {error:?}")))?;
        self.migrations.prepare(&self.root, from.as_deref(), &package.version).map_err(|error| UpdateError::Migration(format!("prepare: {error:?}")))?;
        self.write_state("switching", from.as_deref(), &package.version)?;
        let published = self.versions_root().join(&package.version);
        if published.exists() { fs::remove_dir_all(&published)?; }
        fs::rename(&staged, &published)?;
        self.write_pointer(&package.version)?;
        self.write_state("healthcheck", from.as_deref(), &package.version)?;
        if let Err(error) = self.healthcheck.check(&published) { self.rollback(from.as_deref(), &package.version); self.migrations.rollback(&self.root, from.as_deref(), &package.version); return Err(error); }
        if let Err(error) = self.migrations.validate(&self.root, &package.version) { self.rollback(from.as_deref(), &package.version); self.migrations.rollback(&self.root, from.as_deref(), &package.version); return Err(UpdateError::Migration(format!("validate: {error:?}"))); }
        if let Err(error) = self.migrations.commit(&self.root, &package.version) { self.rollback(from.as_deref(), &package.version); self.migrations.rollback(&self.root, from.as_deref(), &package.version); return Err(UpdateError::Migration(format!("commit: {error:?}"))); }
        // Version directories are intentionally retained. Pinned versions are
        // therefore never deleted; the slice is reserved for future GC.
        let _ = pinned_versions;
        self.write_state("committed", from.as_deref(), &package.version)?;
        Ok(UpdateResult { from_version: from, to_version: package.version.clone(), committed: true, rolled_back: false })
    }

    pub fn recover(&self) -> Result<(), UpdateError> {
        let state = self.read_state()?;
        if state.phase == "committed" || state.phase.is_empty() { return Ok(()); }
        self.rollback(state.from.as_deref(), &state.to);
        self.write_state("recovered", state.from.as_deref(), &state.to)?;
        Ok(())
    }

    pub fn current_version(&self) -> Result<Option<String>, UpdateError> {
        match fs::read_to_string(self.root.join("current.version")) {
            Ok(value) => { let value = value.trim().to_string(); if value.is_empty() { Ok(None) } else if safe_component(&value) { Ok(Some(value)) } else { Err(UpdateError::Recovery("current pointer is unsafe".into())) } }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(UpdateError::Io(error.to_string())),
        }
    }

    fn stage(&self, package: &UpdatePackage) -> Result<PathBuf, UpdateError> {
        let stage = self.staging_root().join(format!("{}-partial", package.version));
        if stage.exists() { fs::remove_dir_all(&stage)?; }
        fs::create_dir_all(&stage)?;
        for file in &package.files {
            let target = stage.join(&file.relative_path);
            if let Some(parent) = target.parent() { fs::create_dir_all(parent)?; }
            let temporary = target.with_extension("tmp");
            let mut output = File::create(&temporary)?;
            output.write_all(&file.bytes)?;
            output.sync_all()?;
            fs::rename(temporary, target)?;
        }
        Ok(stage)
    }

    fn write_pointer(&self, version: &str) -> Result<(), UpdateError> {
        let next = self.root.join("current.version.next");
        let mut file = File::create(&next)?;
        file.write_all(version.as_bytes())?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(next, self.root.join("current.version"))?;
        Ok(())
    }

    fn rollback(&self, from: Option<&str>, to: &str) {
        if let Some(previous) = from { let _ = self.write_pointer(previous); } else { let _ = fs::remove_file(self.root.join("current.version")); }
        let _ = fs::remove_dir_all(self.versions_root().join(to));
        let _ = self.write_state("rolled_back", from, to);
    }

    fn write_state(&self, phase: &str, from: Option<&str>, to: &str) -> Result<(), UpdateError> {
        let temporary = self.root.join("update.state.next");
        let mut file = File::create(&temporary)?;
        writeln!(file, "schema_version=1")?;
        writeln!(file, "phase={phase}")?;
        writeln!(file, "from={}", from.unwrap_or(""))?;
        writeln!(file, "to={to}")?;
        file.sync_all()?;
        fs::rename(temporary, self.root.join("update.state"))?;
        Ok(())
    }

    fn read_state(&self) -> Result<State, UpdateError> {
        let mut value = String::new();
        match File::open(self.root.join("update.state")) {
            Ok(mut file) => { file.read_to_string(&mut value)?; }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(State::default()),
            Err(error) => return Err(UpdateError::Io(error.to_string())),
        }
        let mut state = State::default();
        for line in value.lines() {
            let mut parts = line.splitn(2, '=');
            match (parts.next().unwrap_or(""), parts.next().unwrap_or("")) { ("phase", item) => state.phase = item.into(), ("from", item) if !item.is_empty() => state.from = Some(item.into()), ("to", item) => state.to = item.into(), _ => {} }
        }
        if state.to.is_empty() { return Err(UpdateError::Recovery("update state has no target version".into())); }
        Ok(state)
    }

    fn versions_root(&self) -> PathBuf { self.root.join("versions") }
    fn staging_root(&self) -> PathBuf { self.root.join("staging") }
}

#[derive(Default)] struct State { phase: String, from: Option<String>, to: String }
fn safe_component(value: &str) -> bool { value != "." && value != ".." && !value.is_empty() && value.len() <= 128 && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-')) }
fn safe_relative_path(value: &str) -> bool { let path = Path::new(value); !path.is_absolute() && !value.is_empty() && !value.contains('\\') && !value.contains('\0') && path.components().all(|component| matches!(component, Component::Normal(_))) }

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};
    struct Accept; impl PackageVerifier for Accept { fn verify(&self, _: &UpdatePackage) -> Result<(), UpdateError> { Ok(()) } }
    struct Reject; impl PackageVerifier for Reject { fn verify(&self, _: &UpdatePackage) -> Result<(), UpdateError> { Err(UpdateError::Verification("bad signature".into())) } }
    struct Health { fail: bool } impl Healthcheck for Health { fn check(&self, _: &Path) -> Result<(), UpdateError> { if self.fail { Err(UpdateError::Healthcheck("candidate failed".into())) } else { Ok(()) } } }
    #[derive(Default)] struct Migrations { rolled_back: Cell<bool> }
    impl MigrationHooks for Migrations { fn backup(&self, _: &Path) -> Result<(), UpdateError> { Ok(()) } fn prepare(&self, _: &Path, _: Option<&str>, _: &str) -> Result<(), UpdateError> { Ok(()) } fn validate(&self, _: &Path, _: &str) -> Result<(), UpdateError> { Ok(()) } fn commit(&self, _: &Path, _: &str) -> Result<(), UpdateError> { Ok(()) } fn rollback(&self, _: &Path, _: Option<&str>, _: &str) { self.rolled_back.set(true); } }
    fn root(name: &str) -> PathBuf { let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos(); std::env::temp_dir().join(format!("dubflow-updater-{name}-{stamp}")) }
    fn package(version: &str) -> UpdatePackage { UpdatePackage { version: version.into(), digest: "fixture".into(), files: vec![UpdateFile { relative_path: "engine.bin".into(), bytes: version.as_bytes().to_vec() }] } }
    #[test] fn stages_switches_healthchecks_and_commits() { let root = root("happy"); let updater = Updater::new(&root, Accept, Health { fail: false }, Migrations::default()); let result = updater.apply(&package("2"), &[]).unwrap(); assert!(result.committed); assert_eq!(updater.current_version().unwrap(), Some("2".into())); assert_eq!(fs::read(root.join("versions/2/engine.bin")).unwrap(), b"2"); let _ = fs::remove_dir_all(root); }
    #[test] fn health_failure_restores_last_known_good() { let root = root("rollback"); fs::create_dir_all(root.join("versions/1")).unwrap(); fs::write(root.join("versions/1/engine.bin"), b"1").unwrap(); fs::write(root.join("current.version"), b"1\n").unwrap(); let updater = Updater::new(&root, Accept, Health { fail: true }, Migrations::default()); assert!(updater.apply(&package("2"), &[]).is_err()); assert_eq!(updater.current_version().unwrap(), Some("1".into())); let _ = fs::remove_dir_all(root); }
    #[test] fn verifier_and_lock_are_fail_closed() { let root = root("verify"); let updater = Updater::new(&root, Reject, Health { fail: false }, Migrations::default()); assert!(matches!(updater.apply(&package("2"), &[]), Err(UpdateError::Verification(_)))); fs::create_dir_all(&root).unwrap(); let _lock = OpenOptions::new().write(true).create_new(true).open(root.join("updater.writer.lock")).unwrap(); assert_eq!(updater.apply(&package("3"), &[]), Err(UpdateError::LockHeld)); let _ = fs::remove_dir_all(root); }
    #[test] fn recovery_rolls_back_interrupted_switch() { let root = root("recovery"); fs::create_dir_all(root.join("versions/1")).unwrap(); fs::write(root.join("current.version"), b"2\n").unwrap(); fs::write(root.join("update.state"), b"schema_version=1\nphase=healthcheck\nfrom=1\nto=2\n").unwrap(); let updater = Updater::new(&root, Accept, Health { fail: false }, Migrations::default()); updater.recover().unwrap(); assert_eq!(updater.current_version().unwrap(), Some("1".into())); let _ = fs::remove_dir_all(root); }
    #[test] fn unsafe_package_paths_are_rejected() { let root = root("paths"); let updater = Updater::new(&root, Accept, Health { fail: false }, Migrations::default()); let mut item = package("2"); item.files[0].relative_path = "../escape".into(); assert!(matches!(updater.apply(&item, &[]), Err(UpdateError::InvalidPackage(_)))); let _ = fs::remove_dir_all(root); }
}
