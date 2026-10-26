//! Stable `DubFlow.exe` at the root of a per-user install.
//!
//! Shortcuts point here instead of at `versions\<version>\app\bin\DubFlow.exe`,
//! so they keep working after an update or a rollback switches the active
//! version. The stub reads the `version_path` that the bootstrapper's atomic
//! `current.json` pointer names and starts that version's desktop host.

#![windows_subsystem = "windows"]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

/// `versions/<version>` from `current.json`, validated like a release version.
fn version_path(pointer: &str) -> Option<&str> {
    let key = pointer.find("\"version_path\"")?;
    let rest = &pointer[key + "\"version_path\"".len()..];
    let rest = rest.trim_start().strip_prefix(':')?.trim_start().strip_prefix('"')?;
    let value = &rest[..rest.find('"')?];
    let version = value.strip_prefix("versions/")?;
    let valid = !version.is_empty() && version.len() <= 64 && !version.starts_with('.')
        && version.bytes().all(|b| b.is_ascii_alphanumeric() || b"-._+".contains(&b));
    valid.then_some(value)
}

fn host(root: &Path) -> Option<PathBuf> {
    let pointer = fs::read_to_string(root.join("current.json")).ok()?;
    let mut path = root.to_path_buf();
    for part in version_path(&pointer)?.split('/') {
        path.push(part);
    }
    let host = path.join("app").join("bin").join("DubFlow.exe");
    host.is_file().then_some(host)
}

fn main() -> ExitCode {
    let Some(root) = env::current_exe().ok().and_then(|exe| exe.parent().map(Path::to_path_buf)) else {
        return ExitCode::from(2);
    };
    let Some(host) = host(&root) else { return ExitCode::from(2) };
    let started = Command::new(&host)
        .args(env::args_os().skip(1))
        .current_dir(host.parent().unwrap_or(&root))
        .spawn();
    if started.is_ok() { ExitCode::SUCCESS } else { ExitCode::from(3) }
}

#[cfg(test)]
mod tests {
    use super::version_path;

    #[test]
    fn reads_only_safe_version_paths() {
        assert_eq!(version_path("{\n  \"version_path\": \"versions/1.1.4\"\n}"), Some("versions/1.1.4"));
        assert_eq!(version_path("{\"version_path\":\"versions/0.2.0-rc7\"}"), Some("versions/0.2.0-rc7"));
        assert_eq!(version_path("{\"version_path\": \"versions/../x\"}"), None);
        assert_eq!(version_path("{\"version_path\": \"versions/a\\\\b\"}"), None);
        assert_eq!(version_path("{\"version_path\": \"other/1.0\"}"), None);
        assert_eq!(version_path("{}"), None);
    }
}
