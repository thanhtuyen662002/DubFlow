//! Read the installed selection's expected manifest pin on explicit source start.
//! Full release/inventory/owner admission remains in the native source service.
use crate::source_transport::service::LaunchPin;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::fs::{self, Metadata, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const POINTER_LIMIT: u64 = 64 * 1024;
const FINGERPRINT_LIMIT: Duration = Duration::from_secs(5);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CurrentPointer {
    schema_version: u32,
    current_version: String,
    version_path: String,
    manifest_sha256: String,
    updated_at_utc: String,
}

fn plain_file(metadata: &Metadata) -> bool {
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    true
}

fn stable_read(path: &Path, limit: u64, until: Instant) -> Result<Vec<u8>, &'static str> {
    if Instant::now() >= until {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let before = fs::symlink_metadata(path).map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    if !plain_file(&before) || before.len() == 0 || before.len() > limit {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let modified = before
        .modified()
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(1);
    }
    let mut file = options
        .open(path)
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    let mut raw = Vec::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        if Instant::now() >= until {
            return Err("SOURCE_INSTALL_UNAVAILABLE");
        }
        let count = file
            .read(&mut buffer)
            .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
        if count == 0 {
            break;
        }
        if raw.len() as u64 + count as u64 > limit {
            return Err("SOURCE_INSTALL_UNAVAILABLE");
        }
        raw.extend_from_slice(&buffer[..count]);
    }
    let held = file.metadata().map_err(|_| "SOURCE_INSTALL_CHANGED")?;
    let after = fs::symlink_metadata(path).map_err(|_| "SOURCE_INSTALL_CHANGED")?;
    if Instant::now() >= until
        || raw.len() as u64 != before.len()
        || !plain_file(&held)
        || !plain_file(&after)
        || held.len() != before.len()
        || after.len() != before.len()
        || held.modified().ok() != Some(modified)
        || after.modified().ok() != Some(modified)
    {
        return Err("SOURCE_INSTALL_CHANGED");
    }
    Ok(raw)
}

/// Called only by the deferred native host, never during window construction.
/// A time check refuses late completion; it cannot interrupt a blocked OS read.
/// The host executes this off the UI thread and fences cancellation before launch.
pub fn launch_pin(root: PathBuf, data_root: PathBuf) -> Result<LaunchPin, &'static str> {
    let until = Instant::now() + FINGERPRINT_LIMIT;
    if !root.is_absolute() || !data_root.is_absolute() {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let version = root
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("SOURCE_INSTALL_UNAVAILABLE")?;
    if version.is_empty()
        || version.len() > 96
        || version == "."
        || version == ".."
        || !version
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._+-".contains(&b))
    {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let versions = root.parent().ok_or("SOURCE_INSTALL_UNAVAILABLE")?;
    if versions.file_name().and_then(|s| s.to_str()) != Some("versions") {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let install = versions.parent().ok_or("SOURCE_INSTALL_UNAVAILABLE")?;
    let installed = root
        .canonicalize()
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    let profile = data_root
        .canonicalize()
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    if !installed.is_dir() || !profile.is_dir() || profile.starts_with(&installed) {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let pointer_path = install.join("current.json");
    let pointer_bytes = stable_read(&pointer_path, POINTER_LIMIT, until)?;
    let pointer: CurrentPointer =
        serde_json::from_slice(&pointer_bytes).map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    if pointer.schema_version != 1
        || pointer.current_version != version
        || pointer.version_path != format!("versions/{version}")
        || pointer.updated_at_utc.is_empty()
        || pointer.updated_at_utc.len() > 64
        || pointer.manifest_sha256.len() != 64
        || !pointer
            .manifest_sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let manifest = stable_read(&root.join("release-manifest.json"), MANIFEST_LIMIT, until)?;
    if format!("{:x}", Sha256::digest(&manifest)) != pointer.manifest_sha256 {
        return Err("SOURCE_INSTALL_CHANGED");
    }
    if stable_read(&pointer_path, POINTER_LIMIT, until)? != pointer_bytes {
        return Err("SOURCE_INSTALL_CHANGED");
    }
    Ok(LaunchPin {
        root,
        data_root,
        manifest_sha256: pointer.manifest_sha256,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn fixture() -> (PathBuf, PathBuf, PathBuf) {
        let parent = std::env::temp_dir();
        let base = parent.join(format!(
            "dubflow-source-selection-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        assert!(base.is_absolute() && base.parent() == Some(parent.as_path()));
        fs::create_dir(&base).unwrap();
        let root = base.join("versions").join("0.1.0-review");
        let data = base.join("external profile");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir(&data).unwrap();
        fs::write(root.join("release-manifest.json"), b"abc").unwrap();
        select(&base, "0.1.0-review", "versions/0.1.0-review");
        (base, root, data)
    }
    fn select(base: &Path, version: &str, path: &str) {
        fs::write(base.join("current.json"), serde_json::json!({
            "schema_version":1,"current_version":version,"version_path":path,
            "manifest_sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "updated_at_utc":"2026-10-18T07:00:00Z"
        }).to_string()).unwrap();
    }
    #[test]
    fn pins_exact_installed_selection_and_refuses_same_length_manifest_drift() {
        let (base, root, data) = fixture();
        let pin = launch_pin(root.clone(), data.clone()).unwrap();
        assert_eq!(pin.root, root);
        assert_eq!(pin.data_root, data);
        assert_eq!(
            pin.manifest_sha256,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        fs::write(root.join("release-manifest.json"), b"abd").unwrap();
        assert_eq!(launch_pin(root, data).err(), Some("SOURCE_INSTALL_CHANGED"));
        fs::remove_dir_all(base).unwrap();
    }
    #[test]
    fn refuses_foreign_version_path_or_missing_invalid_and_oversized_pointer() {
        let (base, root, data) = fixture();
        for (version, path) in [
            ("other", "versions/other"),
            ("0.1.0-review", "versions/../0.1.0-review"),
        ] {
            select(&base, version, path);
            assert!(launch_pin(root.clone(), data.clone()).is_err());
        }
        for raw in [b"{}".as_slice(), b"not json".as_slice()] {
            fs::write(base.join("current.json"), raw).unwrap();
            assert!(launch_pin(root.clone(), data.clone()).is_err());
        }
        let file = fs::File::create(base.join("current.json")).unwrap();
        file.set_len(POINTER_LIMIT + 1).unwrap();
        drop(file);
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        fs::remove_file(base.join("current.json")).unwrap();
        assert!(launch_pin(root, data).is_err());
        fs::remove_dir_all(base).unwrap();
    }
    #[test]
    fn refuses_missing_oversized_nonfile_and_runtime_internal_profiles() {
        let (base, root, data) = fixture();
        assert!(launch_pin(PathBuf::from("relative"), data.clone()).is_err());
        let manifest = root.join("release-manifest.json");
        fs::remove_file(&manifest).unwrap();
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        fs::create_dir(&manifest).unwrap();
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        fs::remove_dir(&manifest).unwrap();
        let file = fs::File::create(&manifest).unwrap();
        file.set_len(MANIFEST_LIMIT + 1).unwrap();
        drop(file);
        assert!(launch_pin(root.clone(), data).is_err());
        fs::write(&manifest, b"abc").unwrap();
        let inside = root.join("profile");
        fs::create_dir(&inside).unwrap();
        assert!(launch_pin(root, inside).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
