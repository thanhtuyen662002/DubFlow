//! Hash-verified artifact locators independent of absolute drive letters.

use dubflow_job_storage::{validate_relative_path, validate_volume_id, resolve_relative_path, RootKind, SCHEMA_VERSION};
use sha2::{Digest, Sha256};
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ArtifactError {
    InvalidHash,
    InvalidPath(String),
    InvalidVolume(String),
    Io(String),
    NotRegularFile(PathBuf),
    VolumeMismatch { expected: String, actual: String },
    HashMismatch { expected: String, actual: String },
    SizeMismatch { expected: u64, actual: u64 },
    SymlinkEscape(PathBuf),
}

impl fmt::Display for ArtifactError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidHash => write!(f, "artifact hash must be sha256:<64 lowercase hex>"),
            Self::InvalidPath(detail) => write!(f, "invalid artifact path: {detail}"),
            Self::InvalidVolume(detail) => write!(f, "invalid artifact volume: {detail}"),
            Self::Io(detail) => write!(f, "artifact I/O failed: {detail}"),
            Self::NotRegularFile(path) => write!(f, "artifact is not a regular file: {}", path.display()),
            Self::VolumeMismatch { expected, actual } => write!(
                f,
                "artifact volume mismatch: expected {expected}, got {actual}"
            ),
            Self::HashMismatch { expected, actual } => write!(
                f,
                "artifact hash mismatch: expected {expected}, got {actual}"
            ),
            Self::SizeMismatch { expected, actual } => write!(
                f,
                "artifact size mismatch: expected {expected}, got {actual}"
            ),
            Self::SymlinkEscape(path) => write!(f, "artifact resolves outside its root: {}", path.display()),
        }
    }
}

impl std::error::Error for ArtifactError {}

impl From<io::Error> for ArtifactError {
    fn from(error: io::Error) -> Self {
        Self::Io(error.to_string())
    }
}

pub type Result<T> = std::result::Result<T, ArtifactError>;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactLocator {
    pub schema_version: u32,
    pub volume_id: String,
    pub root_kind: RootKind,
    pub relative_path: PathBuf,
    pub content_hash: String,
    pub size_bytes: u64,
}

impl ArtifactLocator {
    pub fn new(
        volume_id: impl Into<String>,
        root_kind: RootKind,
        relative_path: impl Into<PathBuf>,
        content_hash: impl Into<String>,
        size_bytes: u64,
    ) -> Result<Self> {
        let volume_id = volume_id.into();
        validate_volume_id(&volume_id).map_err(|error| ArtifactError::InvalidVolume(error.to_string()))?;
        let relative_path = relative_path.into();
        validate_relative_path(&relative_path)
            .map_err(|error| ArtifactError::InvalidPath(error.to_string()))?;
        let content_hash = content_hash.into();
        validate_hash(&content_hash)?;
        Ok(Self {
            schema_version: SCHEMA_VERSION,
            volume_id,
            root_kind,
            relative_path,
            content_hash,
            size_bytes,
        })
    }

    pub fn from_file(
        volume_id: impl Into<String>,
        root_kind: RootKind,
        relative_path: impl Into<PathBuf>,
        file_path: &Path,
    ) -> Result<Self> {
        let (content_hash, size_bytes) = hash_file(file_path)?;
        Self::new(volume_id, root_kind, relative_path, content_hash, size_bytes)
    }

    /// Stable key for cache/state comparisons. It contains no absolute path.
    pub fn identity_key(&self) -> String {
        let volume = &self.volume_id;
        let root = self.root_kind.as_str();
        let path = self.relative_path.to_string_lossy().replace('\\', "/");
        let hash = &self.content_hash;
        format!(
            "v{}|v{}:{}|r{}:{}|p{}:{}|h{}:{}|s{}",
            self.schema_version,
            volume.len(),
            volume,
            root.len(),
            root,
            path.len(),
            path,
            hash.len(),
            hash,
            self.size_bytes,
        )
    }

    pub fn resolve_and_verify(
        &self,
        candidate_root: &Path,
        candidate_volume_id: &str,
    ) -> Result<ResolvedArtifact> {
        validate_volume_id(candidate_volume_id)
            .map_err(|error| ArtifactError::InvalidVolume(error.to_string()))?;
        if candidate_volume_id != self.volume_id {
            return Err(ArtifactError::VolumeMismatch {
                expected: self.volume_id.clone(),
                actual: candidate_volume_id.to_owned(),
            });
        }
        let candidate = resolve_relative_path(candidate_root, &self.relative_path)
            .map_err(|error| ArtifactError::InvalidPath(error.to_string()))?;
        let canonical_root = fs::canonicalize(candidate_root)?;
        let canonical_file = fs::canonicalize(&candidate)?;
        if !canonical_file.starts_with(&canonical_root) {
            return Err(ArtifactError::SymlinkEscape(canonical_file));
        }
        let metadata = fs::metadata(&canonical_file)?;
        if !metadata.is_file() {
            return Err(ArtifactError::NotRegularFile(canonical_file));
        }
        let (actual_hash, actual_size) = hash_file(&canonical_file)?;
        if actual_size != self.size_bytes {
            return Err(ArtifactError::SizeMismatch {
                expected: self.size_bytes,
                actual: actual_size,
            });
        }
        if actual_hash != self.content_hash {
            return Err(ArtifactError::HashMismatch {
                expected: self.content_hash.clone(),
                actual: actual_hash,
            });
        }
        Ok(ResolvedArtifact {
            path: canonical_file,
            content_hash: actual_hash,
            size_bytes: actual_size,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResolvedArtifact {
    pub path: PathBuf,
    pub content_hash: String,
    pub size_bytes: u64,
}

pub fn validate_hash(value: &str) -> Result<()> {
    if value.len() != 71
        || !value.starts_with("sha256:")
        || !value[7..].bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(ArtifactError::InvalidHash);
    }
    Ok(())
}

pub fn hash_file(path: &Path) -> Result<(String, u64)> {
    let mut file = File::open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(ArtifactError::NotRegularFile(path.to_owned()));
    }
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
        size = size
            .checked_add(read as u64)
            .ok_or_else(|| ArtifactError::Io("artifact size overflow".into()))?;
    }
    let bytes = digest.finalize();
    let mut hex = String::with_capacity(64);
    for byte in bytes {
        hex.push_str(&format!("{byte:02x}"));
    }
    Ok((format!("sha256:{hex}"), size))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    fn temp_root(label: &str) -> PathBuf {
        let path = env::temp_dir().join(format!("dubflow-artifact-{label}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn moved_folder_rebind_requires_volume_and_content_hash() {
        let first_root = temp_root("first");
        let second_root = temp_root("second");
        let first = first_root.join("nested");
        let second = second_root.join("nested");
        fs::create_dir_all(&first).unwrap();
        fs::create_dir_all(&second).unwrap();
        fs::write(first.join("字幕.ass"), b"stable subtitle").unwrap();
        fs::write(second.join("字幕.ass"), b"stable subtitle").unwrap();
        let locator = ArtifactLocator::from_file("volume-A", RootKind::Output, "nested/字幕.ass", &first.join("字幕.ass")).unwrap();
        let rebound = locator.resolve_and_verify(&second_root, "volume-A").unwrap();
        assert_eq!(rebound.size_bytes, locator.size_bytes);
        assert!(locator.identity_key().starts_with("v1|v8:volume-A|r6:output|p"));
        assert!(matches!(locator.resolve_and_verify(&second_root, "volume-B"), Err(ArtifactError::VolumeMismatch { .. })));
        fs::write(second.join("字幕.ass"), b"changed").unwrap();
        assert!(matches!(locator.resolve_and_verify(&second_root, "volume-A"), Err(ArtifactError::SizeMismatch { .. } | ArtifactError::HashMismatch { .. })));
        let _ = fs::remove_dir_all(first_root);
        let _ = fs::remove_dir_all(second_root);
    }

    #[test]
    fn path_policy_blocks_absolute_escape_reserved_and_unc_values() {
        for value in ["C:\\video.mp4", "../video.mp4", "..\\video.mp4", "\\\\server\\share\\video.mp4", "NUL.txt"] {
            assert!(ArtifactLocator::new("volume-A", RootKind::Output, value, "sha256:0000000000000000000000000000000000000000000000000000000000000000", 0).is_err());
        }
        assert!(ArtifactLocator::new("volume-A", RootKind::Output, "字幕/片段.mp4", "sha256:0000000000000000000000000000000000000000000000000000000000000000", 0).is_ok());
    }

    #[test]
    fn hash_vector_is_stable_and_rejects_uppercase_or_short_hashes() {
        let root = temp_root("hash");
        let path = root.join("abc.txt");
        fs::write(&path, b"abc").unwrap();
        assert_eq!(hash_file(&path).unwrap().0, "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert!(validate_hash("sha256:BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD").is_err());
        assert!(validate_hash("sha256:00").is_err());
        let _ = fs::remove_dir_all(root);
    }
}
