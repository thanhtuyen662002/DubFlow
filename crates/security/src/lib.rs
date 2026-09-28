//! Small, dependency-free security boundary primitives.
//!
//! The boundary is deliberately policy-first: callers receive validated path
//! components, an extraction plan, and an argv vector.  No helper ever turns
//! untrusted input into a shell command or permits an archive member to choose
//! a destination outside the staging root.

use std::collections::BTreeSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const MAX_RELATIVE_PATH_UTF16: usize = 32_767;
pub const MAX_FILENAME_UTF16: usize = 240;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SecurityError {
    EmptyValue(&'static str),
    AbsolutePath,
    ParentTraversal,
    InvalidPathComponent(String),
    PathTooLong,
    DuplicateArchiveMember(String),
    ArchiveLinkRejected,
    ArchiveMemberLimit,
    ArchiveSizeLimit,
    NulByte,
    EmptyCommand,
    InvalidHash,
    ManifestRejected(String),
}

impl fmt::Display for SecurityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyValue(name) => write!(f, "{name} must not be empty"),
            Self::AbsolutePath => write!(f, "absolute, UNC, or drive-qualified paths are not allowed"),
            Self::ParentTraversal => write!(f, "parent traversal is not allowed"),
            Self::InvalidPathComponent(component) => write!(f, "invalid path component {component:?}"),
            Self::PathTooLong => write!(f, "path exceeds the Windows UTF-16 limit"),
            Self::DuplicateArchiveMember(path) => write!(f, "archive member collides with {path:?}"),
            Self::ArchiveLinkRejected => write!(f, "archive links are not allowed"),
            Self::ArchiveMemberLimit => write!(f, "archive member limit exceeded"),
            Self::ArchiveSizeLimit => write!(f, "archive size limit exceeded"),
            Self::NulByte => write!(f, "NUL bytes are not allowed"),
            Self::EmptyCommand => write!(f, "command executable must not be empty"),
            Self::InvalidHash => write!(f, "SHA-256 must be exactly 64 lowercase hexadecimal characters"),
            Self::ManifestRejected(reason) => write!(f, "manifest rejected: {reason}"),
        }
    }
}

impl std::error::Error for SecurityError {}

/// Validate a path supplied by media metadata, an archive, or a manifest.
/// Both slash styles are treated as separators so validation is independent of
/// the host OS.  The returned path is not canonicalized and must still be
/// joined beneath an application-owned root.
pub fn validate_relative_path(value: &str) -> Result<(), SecurityError> {
    if value.is_empty() {
        return Err(SecurityError::EmptyValue("relative path"));
    }
    if value.encode_utf16().count() > MAX_RELATIVE_PATH_UTF16 {
        return Err(SecurityError::PathTooLong);
    }
    if value.contains('\0') {
        return Err(SecurityError::NulByte);
    }
    let normalized = value.replace('\\', "/");
    if normalized.starts_with('/')
        || normalized.starts_with("//")
        || normalized.contains("://")
        || (normalized.len() >= 2 && normalized.as_bytes()[1] == b':')
    {
        return Err(SecurityError::AbsolutePath);
    }

    for component in normalized.split('/') {
        if component.is_empty() {
            return Err(SecurityError::InvalidPathComponent(component.to_owned()));
        }
        if component == ".." {
            return Err(SecurityError::ParentTraversal);
        }
        if component == "."
            || component.ends_with('.')
            || component.ends_with(' ')
            || component.contains(':')
            || component.chars().any(|c| c.is_control())
            || is_windows_reserved_component(component)
        {
            return Err(SecurityError::InvalidPathComponent(component.to_owned()));
        }
    }
    Ok(())
}

fn is_windows_reserved_component(component: &str) -> bool {
    let stem = component.split('.').next().unwrap_or(component);
    matches!(
        stem.to_ascii_uppercase().as_str(),
        "CON" | "PRN" | "AUX" | "NUL"
            | "COM1" | "COM2" | "COM3" | "COM4" | "COM5" | "COM6" | "COM7" | "COM8" | "COM9"
            | "LPT1" | "LPT2" | "LPT3" | "LPT4" | "LPT5" | "LPT6" | "LPT7" | "LPT8" | "LPT9"
    )
}

/// Convert one untrusted display name into one safe filename component.
/// Separators and Windows-invalid characters become underscores; the result is
/// never empty, reserved, or trailing-dot/space.  Collision handling belongs to
/// [`FilenameAllocator`].
pub fn sanitize_filename(value: &str, fallback: &str) -> String {
    let mut output = value
        .chars()
        .map(|c| {
            if c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') {
                '_'
            } else {
                c
            }
        })
        .collect::<String>();
    while output.ends_with('.') || output.ends_with(' ') {
        output.pop();
    }
    if output.is_empty() {
        output = fallback.to_owned();
    }
    if is_windows_reserved_component(&output) {
        output.insert(0, '_');
    }
    truncate_utf16(&output, MAX_FILENAME_UTF16)
}

fn truncate_utf16(value: &str, max_units: usize) -> String {
    let result = truncate_utf16_allow_empty(value, max_units);
    if result.is_empty() { "file".to_owned() } else { result }
}

fn truncate_utf16_allow_empty(value: &str, max_units: usize) -> String {
    let mut units = 0usize;
    let mut result = String::new();
    for character in value.chars() {
        let width = character.len_utf16();
        if units + width > max_units {
            break;
        }
        units += width;
        result.push(character);
    }
    result
}

#[derive(Debug, Default)]
pub struct FilenameAllocator {
    used: BTreeSet<String>,
}

impl FilenameAllocator {
    /// Allocate a deterministic, case-insensitive Windows-safe name.
    pub fn allocate(&mut self, requested: &str, fallback: &str) -> String {
        let base = sanitize_filename(requested, fallback);
        let (stem, extension) = match base.rfind('.') {
            Some(index) if index > 0 => (&base[..index], &base[index..]),
            _ => (base.as_str(), ""),
        };
        let mut candidate = base.clone();
        let mut suffix = 1u64;
        while !self.used.insert(candidate.to_ascii_lowercase()) {
            let suffix_text = format!(" ({suffix})");
            let budget = MAX_FILENAME_UTF16.saturating_sub(suffix_text.encode_utf16().count() + extension.encode_utf16().count());
            candidate = format!("{}{}{}", truncate_utf16_allow_empty(stem, budget), suffix_text, extension);
            candidate = truncate_utf16(&candidate, MAX_FILENAME_UTF16);
            suffix = suffix.saturating_add(1);
        }
        candidate
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveMemberKind {
    File,
    Directory,
    Symlink,
    Hardlink,
}

#[derive(Debug, Clone, Copy)]
pub struct ArchiveLimits {
    pub max_members: u64,
    pub max_member_bytes: u64,
    pub max_total_bytes: u64,
}

impl Default for ArchiveLimits {
    fn default() -> Self {
        Self { max_members: 100_000, max_member_bytes: 4 * 1024 * 1024 * 1024, max_total_bytes: 16 * 1024 * 1024 * 1024 }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct ArchiveMember<'a> {
    pub path: &'a str,
    pub kind: ArchiveMemberKind,
    pub size_bytes: u64,
}

/// Produce the only destinations an archive extractor may write.  Actual
/// extraction code must use this plan and open files without following links.
pub fn plan_archive_extract(
    staging_root: &Path,
    members: &[ArchiveMember<'_>],
    limits: ArchiveLimits,
) -> Result<Vec<PathBuf>, SecurityError> {
    if members.len() as u64 > limits.max_members {
        return Err(SecurityError::ArchiveMemberLimit);
    }
    let mut total = 0u64;
    let mut seen = BTreeSet::new();
    let mut destinations = Vec::with_capacity(members.len());
    for member in members {
        if matches!(member.kind, ArchiveMemberKind::Symlink | ArchiveMemberKind::Hardlink) {
            return Err(SecurityError::ArchiveLinkRejected);
        }
        if member.size_bytes > limits.max_member_bytes {
            return Err(SecurityError::ArchiveSizeLimit);
        }
        total = total.checked_add(member.size_bytes).ok_or(SecurityError::ArchiveSizeLimit)?;
        if total > limits.max_total_bytes {
            return Err(SecurityError::ArchiveSizeLimit);
        }
        validate_relative_path(member.path)?;
        let normalized = member.path.replace('\\', "/");
        let key = normalized.to_ascii_lowercase();
        if !seen.insert(key.clone()) {
            return Err(SecurityError::DuplicateArchiveMember(normalized));
        }
        let mut destination = staging_root.to_path_buf();
        for component in normalized.split('/') {
            destination.push(component);
        }
        destinations.push(destination);
    }
    Ok(destinations)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandInvocation {
    pub executable: String,
    pub args: Vec<String>,
}

impl CommandInvocation {
    /// Construct argv without a shell.  Shell metacharacters remain literal
    /// argument data; callers must pass this to `Command::arg` unchanged.
    pub fn new(executable: &str, args: &[&str]) -> Result<Self, SecurityError> {
        if executable.is_empty() {
            return Err(SecurityError::EmptyCommand);
        }
        if executable.contains('\0') || args.iter().any(|arg| arg.contains('\0')) {
            return Err(SecurityError::NulByte);
        }
        Ok(Self { executable: executable.to_owned(), args: args.iter().map(|arg| (*arg).to_owned()).collect() })
    }

    pub fn command(&self) -> Command {
        let mut command = Command::new(&self.executable);
        command.args(&self.args);
        command
    }
}

/// Redact common credential-bearing key/value fields before diagnostics leave
/// the process.  This intentionally handles plain logs as well as JSON-like
/// text and also removes bearer tokens with spaces in their scheme.
pub fn redact_sensitive_text(value: &str) -> String {
    let mut output = value.to_owned();
    for key in ["cookie", "set-cookie", "authorization", "token", "api_key", "apikey", "secret", "password", "session"] {
        output = redact_key_value(&output, key);
    }
    let lower = output.to_ascii_lowercase();
    if let Some(index) = lower.find("bearer ") {
        let start = index + "bearer ".len();
        let end = output[start..].find(|c: char| c.is_whitespace() || matches!(c, ',' | ';' | '"' | '\'' | '}')).map(|offset| start + offset).unwrap_or(output.len());
        output.replace_range(start..end, "[REDACTED]");
    }
    output
}

fn redact_key_value(value: &str, key: &str) -> String {
    let mut output = value.to_owned();
    loop {
        let lower = output.to_ascii_lowercase();
        let Some(key_start) = lower.find(key) else { break };
        let mut cursor = key_start + key.len();
        while cursor < output.len() && matches!(output.as_bytes()[cursor], b' ' | b'\t') { cursor += 1; }
        if cursor >= output.len() || !matches!(output.as_bytes()[cursor], b'=' | b':') { break }
        cursor += 1;
        while cursor < output.len() && matches!(output.as_bytes()[cursor], b' ' | b'\t') { cursor += 1; }
        let end = output[cursor..].find(|c: char| c.is_whitespace() || matches!(c, ',' | ';' | '"' | '\'' | '}')).map(|offset| cursor + offset).unwrap_or(output.len());
        if end == cursor { break; }
        output.replace_range(cursor..end, "[REDACTED]");
    }
    output
}

pub fn validate_sha256(value: &str) -> Result<(), SecurityError> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) {
        return Err(SecurityError::InvalidHash);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_reject_escape_drive_unc_reserved_and_controls() {
        for value in ["../x", "..\\x", "C:\\x", "\\\\server\\share", "CON.txt", "a/b.", "a/ b", "a\0b"] {
            assert!(validate_relative_path(value).is_err(), "{value:?}");
        }
        assert!(validate_relative_path("字幕/片段.mp4").is_ok());
    }

    #[test]
    fn filenames_are_safe_and_collision_free_case_insensitively() {
        let mut allocator = FilenameAllocator::default();
        assert_eq!(allocator.allocate("CON?.mp4", "video.mp4"), "_CON_.mp4");
        assert_eq!(allocator.allocate("clip.mp4", "video.mp4"), "clip.mp4");
        assert_eq!(allocator.allocate("CLIP.mp4", "video.mp4"), "CLIP (1).mp4");
        assert!(!allocator.allocate("a/b", "video").contains('/'));
    }

    #[test]
    fn archive_plan_blocks_zip_slip_links_duplicates_and_overflow() {
        let root = Path::new("stage");
        let members = [ArchiveMember { path: "ok/file.bin", kind: ArchiveMemberKind::File, size_bytes: 4 }];
        assert_eq!(plan_archive_extract(root, &members, ArchiveLimits::default()).unwrap()[0], root.join("ok/file.bin"));
        for bad in ["../escape", "C:\\escape", "\\\\server\\share\\x"] {
            let item = [ArchiveMember { path: bad, kind: ArchiveMemberKind::File, size_bytes: 1 }];
            assert!(plan_archive_extract(root, &item, ArchiveLimits::default()).is_err());
        }
        let links = [ArchiveMember { path: "link", kind: ArchiveMemberKind::Symlink, size_bytes: 0 }];
        assert_eq!(plan_archive_extract(root, &links, ArchiveLimits::default()), Err(SecurityError::ArchiveLinkRejected));
        let duplicate = [
            ArchiveMember { path: "A.txt", kind: ArchiveMemberKind::File, size_bytes: 1 },
            ArchiveMember { path: "a.TXT", kind: ArchiveMemberKind::File, size_bytes: 1 },
        ];
        assert!(matches!(plan_archive_extract(root, &duplicate, ArchiveLimits::default()), Err(SecurityError::DuplicateArchiveMember(_))));
        let limits = ArchiveLimits { max_total_bytes: 1, ..ArchiveLimits::default() };
        assert_eq!(plan_archive_extract(root, &members, limits), Err(SecurityError::ArchiveSizeLimit));
    }

    #[test]
    fn command_argv_keeps_shell_metacharacters_literal() {
        let command = CommandInvocation::new("ffmpeg", &["-i", "input; echo pwned", "--metadata", "$(whoami)"]).unwrap();
        assert_eq!(command.args[1], "input; echo pwned");
        assert_eq!(command.args[3], "$(whoami)");
    }

    #[test]
    fn diagnostics_redact_credentials_and_validate_hashes() {
        let redacted = redact_sensitive_text("Cookie=abc; Authorization: Bearer secret-token; api_key=key123");
        assert!(!redacted.contains("abc"));
        assert!(!redacted.contains("secret-token"));
        assert!(!redacted.contains("key123"));
        assert!(validate_sha256(&"a".repeat(64)).is_ok());
        assert!(validate_sha256(&"A".repeat(64)).is_err());
    }
}
