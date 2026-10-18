//! Fingerprint the installed manifest for the source service's own admission.
//! This does not admit a release: the native service still verifies its complete
//! manifest, executable, inventory and owner before reporting source_ready.
use crate::source_transport::service::LaunchPin;
use sha2::{Digest, Sha256};
use std::fs::{self, Metadata, OpenOptions};
use std::io::Read;
use std::path::PathBuf;
use std::time::{Duration, Instant};

const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const FINGERPRINT_LIMIT: Duration = Duration::from_secs(5);

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

/// The entrypoint supplies these paths from native installed-release context.
/// No webview command may choose a root, expected hash or admission allowance.
pub fn launch_pin(root: PathBuf, data_root: PathBuf) -> Result<LaunchPin, &'static str> {
    if !root.is_absolute() || !data_root.is_absolute() {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let installed = root
        .canonicalize()
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    let profile = data_root
        .canonicalize()
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    if !installed.is_dir() || !profile.is_dir() || profile.starts_with(&installed) {
        return Err("SOURCE_INSTALL_UNAVAILABLE");
    }
    let path = root.join("release-manifest.json");
    let before = fs::symlink_metadata(&path).map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    if !plain_file(&before) || before.len() == 0 || before.len() > MANIFEST_LIMIT {
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
        // A concurrently writable/deletable manifest is not a stable pin.
        options.share_mode(1);
    }
    let mut file = options
        .open(&path)
        .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
    let start = Instant::now();
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut bytes = 0_u64;
    loop {
        if start.elapsed() > FINGERPRINT_LIMIT {
            return Err("SOURCE_INSTALL_UNAVAILABLE");
        }
        let count = file
            .read(&mut buffer)
            .map_err(|_| "SOURCE_INSTALL_UNAVAILABLE")?;
        if count == 0 {
            break;
        }
        bytes += count as u64;
        if bytes > MANIFEST_LIMIT {
            return Err("SOURCE_INSTALL_UNAVAILABLE");
        }
        digest.update(&buffer[..count]);
    }
    let held = file.metadata().map_err(|_| "SOURCE_INSTALL_CHANGED")?;
    let after = fs::symlink_metadata(&path).map_err(|_| "SOURCE_INSTALL_CHANGED")?;
    if start.elapsed() > FINGERPRINT_LIMIT
        || bytes != before.len()
        || !plain_file(&held)
        || !plain_file(&after)
        || held.len() != bytes
        || after.len() != bytes
        || held.modified().ok() != Some(modified)
        || after.modified().ok() != Some(modified)
    {
        return Err("SOURCE_INSTALL_CHANGED");
    }
    Ok(LaunchPin {
        root,
        data_root,
        manifest_sha256: format!("{:x}", digest.finalize()),
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
            "dubflow-source-bootstrap-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        assert!(base.is_absolute() && base.parent() == Some(parent.as_path()));
        fs::create_dir(&base).unwrap();
        let root = base.join("installed release");
        let data = base.join("external profile");
        fs::create_dir(&root).unwrap();
        fs::create_dir(&data).unwrap();
        (base, root, data)
    }
    #[test]
    fn fingerprints_exact_bytes_and_keeps_native_paths_without_admitting_content() {
        let (base, root, data) = fixture();
        // Deliberately not a valid release. Only the native validator can admit it.
        fs::write(root.join("release-manifest.json"), b"abc").unwrap();
        let pin = launch_pin(root.clone(), data.clone()).unwrap();
        assert_eq!(pin.root, root);
        assert_eq!(pin.data_root, data);
        assert_eq!(
            pin.manifest_sha256,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        fs::write(root.join("release-manifest.json"), b"abd").unwrap();
        assert_ne!(
            launch_pin(root, data).unwrap().manifest_sha256,
            pin.manifest_sha256
        );
        fs::remove_dir_all(base).unwrap();
    }
    #[test]
    fn refuses_missing_oversized_nonfile_and_runtime_internal_profiles() {
        let (base, root, data) = fixture();
        assert!(launch_pin(PathBuf::from("relative"), data.clone()).is_err());
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        let manifest = root.join("release-manifest.json");
        fs::create_dir(&manifest).unwrap();
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        fs::remove_dir(&manifest).unwrap();
        let file = fs::File::create(&manifest).unwrap();
        file.set_len(MANIFEST_LIMIT + 1).unwrap();
        drop(file);
        assert!(launch_pin(root.clone(), data.clone()).is_err());
        fs::write(&manifest, b"abc").unwrap();
        let inside = root.join("profile");
        fs::create_dir(&inside).unwrap();
        assert!(launch_pin(root, inside).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
