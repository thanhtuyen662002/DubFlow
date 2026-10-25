//! Signed self-update and rollback, delegated to the bundled Python runtime.
//!
//! `engine.dubflow.update` finds and verifies a signed update manifest on
//! GitHub Releases and downloads the setup executable with its SHA-256 pinned.
//! `packaging/release/bootstrap.py` lists installed versions and switches the
//! current pointer. Installs are side by side under `versions/<version>`, so a
//! rollback never deletes the newer files.

use std::path::{Path, PathBuf};

/// The per-user install root (`%LOCALAPPDATA%\DubFlow`) when this host runs
/// from `<root>\versions\<version>`; `None` for development builds.
pub fn install_root(version_root: &Path) -> Option<PathBuf> {
    let versions = version_root.parent()?;
    if versions.file_name()?.to_str()? != "versions" {
        return None;
    }
    versions.parent().map(Path::to_path_buf)
}

pub fn updates_dir(install_root: &Path) -> PathBuf {
    install_root.join("updates")
}

/// A downloaded setup the host may launch: a direct child of the updates
/// folder named like the release asset.
pub fn is_update_setup(install_root: &Path, path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else { return false };
    let shaped = name.starts_with("DubFlow-") && name.ends_with("-windows-x64-setup.exe") && name.len() <= 120
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"-._".contains(&b));
    shaped && path.parent() == Some(updates_dir(install_root).as_path()) && path.is_file()
}

/// Version identifiers accepted for activation (same shape as release versions).
pub fn valid_version(version: &str) -> bool {
    !version.is_empty() && version.len() <= 64 && !version.starts_with('.')
        && version.bytes().all(|b| b.is_ascii_alphanumeric() || b"-._+".contains(&b))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_root_only_for_versioned_layout() {
        assert_eq!(install_root(Path::new(r"C:\Users\u\AppData\Local\DubFlow\versions\0.2.0-rc6")), Some(PathBuf::from(r"C:\Users\u\AppData\Local\DubFlow")));
        assert_eq!(install_root(Path::new(r"D:\dev\DubFlow-dev")), None);
    }

    #[test]
    fn only_shaped_setups_inside_updates_are_launchable() {
        let root = std::env::temp_dir().join(format!("dubflow-updater-test-{}", std::process::id()));
        let updates = updates_dir(&root);
        std::fs::create_dir_all(&updates).unwrap();
        let good = updates.join("DubFlow-0.3.0-windows-x64-setup.exe");
        std::fs::write(&good, b"x").unwrap();
        let evil = updates.join("evil.exe");
        std::fs::write(&evil, b"x").unwrap();
        assert!(is_update_setup(&root, &good));
        assert!(!is_update_setup(&root, &evil));
        assert!(!is_update_setup(&root, &root.join("DubFlow-0.3.0-windows-x64-setup.exe")));
        assert!(!is_update_setup(&root, &updates.join("DubFlow-9.9.9-windows-x64-setup.exe")));
        std::fs::remove_dir_all(root).unwrap();
        assert!(valid_version("0.2.0-rc6") && !valid_version("..\\x") && !valid_version(".staging") && !valid_version(""));
    }
}
