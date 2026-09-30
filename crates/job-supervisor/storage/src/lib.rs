//! Deterministic storage topology and recovery policy for DubFlow.
//!
//! The supervisor owns durable state, but media, model, cache and temporary
//! roots may live on different volumes. This crate keeps the policy pure and
//! injects operating-system probes so tests never need a NAS, removable disk,
//! power event or privileged disk API.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const SCHEMA_VERSION: u32 = 1;
pub const PPM_SCALE: u128 = 1_000_000;
pub const MAX_WINDOWS_PATH_UTF16: usize = 32_767;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub enum RootKind {
    Control,
    Source,
    Output,
    Model,
    Cache,
    Temp,
}

impl RootKind {
    pub const ALL: [Self; 6] = [
        Self::Control,
        Self::Source,
        Self::Output,
        Self::Model,
        Self::Cache,
        Self::Temp,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Control => "control",
            Self::Source => "source",
            Self::Output => "output",
            Self::Model => "model",
            Self::Cache => "cache",
            Self::Temp => "temp",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "control" => Ok(Self::Control),
            "source" => Ok(Self::Source),
            "output" => Ok(Self::Output),
            "model" => Ok(Self::Model),
            "cache" => Ok(Self::Cache),
            "temp" => Ok(Self::Temp),
            other => Err(StorageError::InvalidConfig(format!(
                "unknown root kind {other:?}"
            ))),
        }
    }

    fn requires_write(self) -> bool {
        matches!(self, Self::Control | Self::Output | Self::Cache | Self::Temp)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HealthState {
    Available,
    Missing,
    NotDirectory,
    ReadOnly,
    LowSpace,
    SpaceUnknown,
    Network,
    AtomicityUnsupported,
    ProbeUnavailable,
    NetworkControlForbidden,
    RemovableControlForbidden,
    ReprobeRequired,
}

impl HealthState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Available => "available",
            Self::Missing => "missing",
            Self::NotDirectory => "not_directory",
            Self::ReadOnly => "read_only",
            Self::LowSpace => "low_space",
            Self::SpaceUnknown => "space_unknown",
            Self::Network => "network",
            Self::AtomicityUnsupported => "atomicity_unsupported",
            Self::ProbeUnavailable => "probe_unavailable",
            Self::NetworkControlForbidden => "network_control_forbidden",
            Self::RemovableControlForbidden => "removable_control_forbidden",
            Self::ReprobeRequired => "reprobe_required",
        }
    }

    pub fn is_usable(self) -> bool {
        matches!(self, Self::Available | Self::Network)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JobImpact {
    Continue,
    PauseAffected,
    RebuildCache,
    UseApprovedFallback,
    ControlUnavailable,
    RecreateTemp,
}

impl JobImpact {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Continue => "continue",
            Self::PauseAffected => "pause_affected",
            Self::RebuildCache => "discard_and_rebuild_cache",
            Self::UseApprovedFallback => "use_approved_fallback",
            Self::ControlUnavailable => "control_unavailable",
            Self::RecreateTemp => "recreate_temp",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StorageError {
    InvalidConfig(String),
    InvalidPath(String),
    InvalidVolumeId(String),
    MissingRoot(RootKind),
    Probe(String),
    Io(String),
    InvalidSpace { available_bytes: u64, total_bytes: u64 },
    VolumeIdentityUnavailable(RootKind),
    VolumeIdentityMismatch { expected: String, actual: String },
    LeaseGenerationMismatch { expected: u64, actual: u64 },
    RootUnavailable { kind: RootKind, state: HealthState },
    QuarantineExhausted(PathBuf),
}

impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig(detail) => write!(f, "invalid storage configuration: {detail}"),
            Self::InvalidPath(detail) => write!(f, "invalid storage path: {detail}"),
            Self::InvalidVolumeId(detail) => write!(f, "invalid volume identity: {detail}"),
            Self::MissingRoot(kind) => write!(f, "missing configured root: {}", kind.as_str()),
            Self::Probe(detail) => write!(f, "storage probe failed: {detail}"),
            Self::Io(detail) => write!(f, "storage I/O failed: {detail}"),
            Self::InvalidSpace { available_bytes, total_bytes } => write!(
                f,
                "available bytes {available_bytes} exceed total bytes {total_bytes}"
            ),
            Self::VolumeIdentityUnavailable(kind) => write!(
                f,
                "root {} has no stable volume identity",
                kind.as_str()
            ),
            Self::VolumeIdentityMismatch { expected, actual } => write!(
                f,
                "volume identity mismatch: expected {expected}, got {actual}"
            ),
            Self::LeaseGenerationMismatch { expected, actual } => write!(
                f,
                "resource lease generation is stale: expected {expected}, got {actual}"
            ),
            Self::RootUnavailable { kind, state } => write!(
                f,
                "root {} is unavailable: {}",
                kind.as_str(),
                state.as_str()
            ),
            Self::QuarantineExhausted(path) => {
                write!(f, "could not allocate a quarantine name beside {}", path.display())
            }
        }
    }
}

impl std::error::Error for StorageError {}

impl From<io::Error> for StorageError {
    fn from(error: io::Error) -> Self {
        Self::Io(error.to_string())
    }
}

pub type Result<T> = std::result::Result<T, StorageError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RootPolicy {
    minimum_free_bytes: u64,
    minimum_free_ratio_ppm: u32,
}

impl RootPolicy {
    pub fn new(minimum_free_bytes: u64, minimum_free_ratio_ppm: u32) -> Result<Self> {
        if u64::from(minimum_free_ratio_ppm) > PPM_SCALE as u64 {
            return Err(StorageError::InvalidConfig(
                "minimum_free_ratio_ppm must be at most 1_000_000".into(),
            ));
        }
        Ok(Self {
            minimum_free_bytes,
            minimum_free_ratio_ppm,
        })
    }

    pub fn minimum_free_bytes(self) -> u64 {
        self.minimum_free_bytes
    }

    pub fn minimum_free_ratio_ppm(self) -> u32 {
        self.minimum_free_ratio_ppm
    }

    fn requires_space(self) -> bool {
        self.minimum_free_bytes != 0 || self.minimum_free_ratio_ppm != 0
    }

    fn is_low_space(self, space: DiskSpace) -> bool {
        let below_bytes = space.available_bytes < self.minimum_free_bytes;
        let left = u128::from(space.available_bytes) * PPM_SCALE;
        let right = u128::from(space.total_bytes) * u128::from(self.minimum_free_ratio_ppm);
        below_bytes || left < right
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RootConfig {
    pub kind: RootKind,
    pub path: PathBuf,
    pub required: bool,
    pub policy: RootPolicy,
}

impl RootConfig {
    pub fn new(kind: RootKind, path: impl Into<PathBuf>, required: bool, policy: RootPolicy) -> Result<Self> {
        let path = path.into();
        validate_root_path(&path)?;
        Ok(Self {
            kind,
            path,
            required,
            policy,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TopologyConfig {
    roots: Vec<RootConfig>,
}

impl TopologyConfig {
    pub fn new(roots: Vec<RootConfig>) -> Result<Self> {
        if roots.len() != RootKind::ALL.len() {
            return Err(StorageError::InvalidConfig(format!(
                "exactly {} roots are required, got {}",
                RootKind::ALL.len(),
                roots.len()
            )));
        }
        let mut seen = BTreeMap::new();
        for root in &roots {
            if seen.insert(root.kind, true).is_some() {
                return Err(StorageError::InvalidConfig(format!(
                    "duplicate root kind {}",
                    root.kind.as_str()
                )));
            }
            if root.kind == RootKind::Control && is_network_path(&root.path) {
                return Err(StorageError::InvalidConfig(
                    "control root cannot be a network or UNC path".into(),
                ));
            }
            if matches!(root.kind, RootKind::Control | RootKind::Source | RootKind::Output)
                && !root.required
            {
                return Err(StorageError::InvalidConfig(format!(
                    "{} root must be required",
                    root.kind.as_str()
                )));
            }
        }
        for kind in RootKind::ALL {
            if !seen.contains_key(&kind) {
                return Err(StorageError::MissingRoot(kind));
            }
        }
        for (index, left) in roots.iter().enumerate() {
            for right in roots.iter().skip(index + 1) {
                if !roots_are_distinct(&left.path, &right.path) {
                    return Err(StorageError::InvalidConfig(format!(
                        "root paths may not alias or nest: {} and {}",
                        left.kind.as_str(),
                        right.kind.as_str()
                    )));
                }
            }
        }
        Ok(Self { roots })
    }

    pub fn roots(&self) -> &[RootConfig] {
        &self.roots
    }

    pub fn root(&self, kind: RootKind) -> &RootConfig {
        self.roots.iter().find(|root| root.kind == kind).expect("validated topology has every root")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DiskSpace {
    pub available_bytes: u64,
    pub total_bytes: u64,
}

impl DiskSpace {
    pub fn new(available_bytes: u64, total_bytes: u64) -> Result<Self> {
        if available_bytes > total_bytes || total_bytes == 0 {
            return Err(StorageError::InvalidSpace {
                available_bytes,
                total_bytes,
            });
        }
        Ok(Self {
            available_bytes,
            total_bytes,
        })
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct VolumeObservation {
    pub exists: bool,
    pub is_directory: bool,
    pub writable: bool,
    pub atomic_rename_supported: bool,
    pub network: bool,
    pub removable: bool,
    pub volume_id: Option<String>,
    pub space: Option<DiskSpace>,
    pub probe_error: Option<String>,
}

impl VolumeObservation {
    pub fn validate(&self) -> Result<()> {
        if let Some(volume_id) = &self.volume_id {
            validate_volume_id(volume_id)?;
        }
        if let Some(space) = self.space {
            DiskSpace::new(space.available_bytes, space.total_bytes)?;
        }
        Ok(())
    }
}

pub trait VolumeProbe {
    fn observe(&self, root: &RootConfig) -> Result<VolumeObservation>;
}

/// Portable fallback probe. Platform adapters should add a stable volume ID
/// and disk-space snapshot; without those fields the policy reports
/// `space_unknown` when a non-zero threshold was configured and refuses to
/// rebind artifacts by path alone.
#[derive(Clone, Copy, Debug, Default)]
pub struct FilesystemProbe;

impl VolumeProbe for FilesystemProbe {
    fn observe(&self, root: &RootConfig) -> Result<VolumeObservation> {
        let metadata = match fs::metadata(&root.path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(VolumeObservation {
                    network: is_network_path(&root.path),
                    ..VolumeObservation::default()
                });
            }
            Err(error) => {
                return Ok(VolumeObservation {
                    network: is_network_path(&root.path),
                    probe_error: Some(error.to_string()),
                    ..VolumeObservation::default()
                });
            }
        };
        let is_directory = metadata.is_dir();
        let network = is_network_path(&root.path);
        let writable = is_directory && root.kind.requires_write() && probe_write(&root.path).is_ok();
        let atomic_rename_supported = is_directory && !network && probe_atomic_rename(&root.path).is_ok();
        let (volume_id, marker_error) = match read_volume_marker(&root.path) {
            Ok(value) => (value, None),
            Err(error) => (None, Some(error.to_string())),
        };
        let space = match (fs2::available_space(&root.path), fs2::total_space(&root.path)) {
            (Ok(available_bytes), Ok(total_bytes)) => DiskSpace::new(available_bytes, total_bytes).ok(),
            _ => None,
        };
        Ok(VolumeObservation {
            exists: true,
            is_directory,
            writable: if root.kind.requires_write() { writable } else { true },
            atomic_rename_supported,
            network,
            removable: false,
            volume_id,
            space,
            probe_error: marker_error,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RootHealth {
    pub kind: RootKind,
    pub path: PathBuf,
    pub state: HealthState,
    pub volume_id: Option<String>,
    pub space: Option<DiskSpace>,
    pub atomic_rename_supported: bool,
    pub network: bool,
    pub removable: bool,
    pub required: bool,
    pub probe_error: Option<String>,
    pub checked_at_ms: u64,
    pub generation: u64,
}

impl RootHealth {
    pub fn is_usable(&self) -> bool {
        self.state.is_usable()
    }

    pub fn impact(&self) -> JobImpact {
        if !self.required && !self.is_usable() {
            return match self.kind {
                RootKind::Model => JobImpact::UseApprovedFallback,
                RootKind::Cache => JobImpact::RebuildCache,
                RootKind::Temp => JobImpact::RecreateTemp,
                RootKind::Control | RootKind::Source | RootKind::Output => JobImpact::Continue,
            };
        }
        match self.kind {
            RootKind::Control => {
                if self.is_usable() {
                    JobImpact::Continue
                } else {
                    JobImpact::ControlUnavailable
                }
            }
            RootKind::Source | RootKind::Output => {
                if self.is_usable() {
                    JobImpact::Continue
                } else {
                    JobImpact::PauseAffected
                }
            }
            RootKind::Model => {
                if self.is_usable() {
                    JobImpact::Continue
                } else {
                    JobImpact::UseApprovedFallback
                }
            }
            RootKind::Cache => {
                if self.is_usable() {
                    JobImpact::Continue
                } else {
                    JobImpact::RebuildCache
                }
            }
            RootKind::Temp => {
                if self.is_usable() {
                    JobImpact::Continue
                } else {
                    JobImpact::RecreateTemp
                }
            }
        }
    }

    pub fn lease(&self) -> Result<StorageLease> {
        if !self.is_usable() {
            return Err(StorageError::RootUnavailable {
                kind: self.kind,
                state: self.state,
            });
        }
        let volume_id = self
            .volume_id
            .clone()
            .ok_or(StorageError::VolumeIdentityUnavailable(self.kind))?;
        Ok(StorageLease {
            kind: self.kind,
            volume_id,
            generation: self.generation,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HealthReport {
    pub schema_version: u32,
    pub generation: u64,
    roots: BTreeMap<RootKind, RootHealth>,
}

impl HealthReport {
    pub fn root(&self, kind: RootKind) -> &RootHealth {
        self.roots.get(&kind).expect("validated report has every root")
    }

    pub fn roots(&self) -> impl Iterator<Item = &RootHealth> {
        self.roots.values()
    }

    pub fn lease(&self, kind: RootKind) -> Result<StorageLease> {
        self.root(kind).lease()
    }

    pub fn validate_lease(&self, lease: &StorageLease) -> Result<()> {
        if lease.generation != self.generation {
            return Err(StorageError::LeaseGenerationMismatch {
                expected: self.generation,
                actual: lease.generation,
            });
        }
        let root = self.root(lease.kind);
        let actual = root
            .volume_id
            .as_deref()
            .ok_or(StorageError::VolumeIdentityUnavailable(lease.kind))?;
        if actual != lease.volume_id {
            return Err(StorageError::VolumeIdentityMismatch {
                expected: lease.volume_id.clone(),
                actual: actual.to_owned(),
            });
        }
        if !root.is_usable() {
            return Err(StorageError::RootUnavailable {
                kind: lease.kind,
                state: root.state,
            });
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageLease {
    pub kind: RootKind,
    pub volume_id: String,
    pub generation: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageTopology {
    config: TopologyConfig,
}

impl StorageTopology {
    pub fn new(config: TopologyConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &TopologyConfig {
        &self.config
    }

    pub fn control_db_path(&self) -> PathBuf {
        self.config.root(RootKind::Control).path.join("state").join("dubflow.sqlite3")
    }

    pub fn inspect(&self, probe: &dyn VolumeProbe, checked_at_ms: u64) -> Result<HealthReport> {
        self.inspect_at_generation(probe, checked_at_ms, 0)
    }

    pub fn revalidate_after_resume(
        &self,
        probe: &dyn VolumeProbe,
        previous_generation: u64,
        checked_at_ms: u64,
    ) -> Result<HealthReport> {
        let generation = previous_generation
            .checked_add(1)
            .ok_or_else(|| StorageError::InvalidConfig("resume generation overflow".into()))?;
        self.inspect_at_generation(probe, checked_at_ms, generation)
    }

    fn inspect_at_generation(
        &self,
        probe: &dyn VolumeProbe,
        checked_at_ms: u64,
        generation: u64,
    ) -> Result<HealthReport> {
        let mut roots = BTreeMap::new();
        for root in self.config.roots() {
            let observation = match probe.observe(root) {
                Ok(observation) => observation,
                Err(error) => VolumeObservation {
                    network: is_network_path(&root.path),
                    probe_error: Some(error.to_string()),
                    ..VolumeObservation::default()
                },
            };
            let state = match observation.validate() {
                Ok(()) => classify_health(root, &observation)?,
                Err(_) => HealthState::ProbeUnavailable,
            };
            roots.insert(
                root.kind,
                RootHealth {
                    kind: root.kind,
                    path: root.path.clone(),
                    state,
                    volume_id: observation.volume_id,
                    space: observation.space,
                    atomic_rename_supported: observation.atomic_rename_supported,
                    network: observation.network,
                    removable: observation.removable,
                    required: root.required,
                    probe_error: observation.probe_error,
                    checked_at_ms,
                    generation,
                },
            );
        }
        Ok(HealthReport {
            schema_version: SCHEMA_VERSION,
            generation,
            roots,
        })
    }
}

fn classify_health(root: &RootConfig, observation: &VolumeObservation) -> Result<HealthState> {
    if observation.probe_error.is_some() {
        return Ok(HealthState::ProbeUnavailable);
    }
    if !observation.exists {
        return Ok(HealthState::Missing);
    }
    if !observation.is_directory {
        return Ok(HealthState::NotDirectory);
    }
    if root.kind == RootKind::Control && observation.network {
        return Ok(HealthState::NetworkControlForbidden);
    }
    if root.kind == RootKind::Control && observation.removable {
        return Ok(HealthState::RemovableControlForbidden);
    }
    if root.kind.requires_write() && !observation.atomic_rename_supported {
        return Ok(HealthState::AtomicityUnsupported);
    }
    if root.kind.requires_write() && !observation.writable {
        return Ok(HealthState::ReadOnly);
    }
    if root.policy.requires_space() {
        let space = observation.space.ok_or_else(|| {
            StorageError::InvalidConfig(format!(
                "root {} has a non-zero free-space policy but its probe returned no space snapshot",
                root.kind.as_str()
            ))
        });
        let space = match space {
            Ok(space) => space,
            Err(StorageError::InvalidConfig(_)) => return Ok(HealthState::SpaceUnknown),
            Err(error) => return Err(error),
        };
        if root.policy.is_low_space(space) {
            return Ok(HealthState::LowSpace);
        }
    }
    if observation.network {
        return Ok(HealthState::Network);
    }
    Ok(HealthState::Available)
}

fn validate_root_path(path: &Path) -> Result<()> {
    let raw = path.to_string_lossy();
    if raw.is_empty() || raw.chars().any(|character| character.is_control() || character == '\0') {
        return Err(StorageError::InvalidPath(
            "root path must be non-empty and contain no control characters".into(),
        ));
    }
    if raw.encode_utf16().count() > MAX_WINDOWS_PATH_UTF16 {
        return Err(StorageError::InvalidPath(
            "root path exceeds the Windows extended-path limit".into(),
        ));
    }
    for component in raw.split(['/', '\\']) {
        if component == "." || component == ".." {
            return Err(StorageError::InvalidPath(
                "root path may not contain '.' or '..' components".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_relative_path(path: &Path) -> Result<()> {
    let raw = path.to_string_lossy();
    if raw.is_empty() || raw == "." {
        return Err(StorageError::InvalidPath(
            "relative artifact path must not be empty or '.'".into(),
        ));
    }
    if path.is_absolute()
        || raw.starts_with('/')
        || raw.starts_with('\\')
        || is_windows_absolute(&raw)
        || raw.starts_with("smb://")
        || raw.starts_with("\\\\?\\")
    {
        return Err(StorageError::InvalidPath(
            "relative artifact path must not be absolute, UNC or SMB".into(),
        ));
    }
    if raw.encode_utf16().count() > MAX_WINDOWS_PATH_UTF16 {
        return Err(StorageError::InvalidPath(
            "relative artifact path exceeds the Windows extended-path limit".into(),
        ));
    }
    if raw.chars().any(|character| character.is_control() || character == '\0') {
        return Err(StorageError::InvalidPath(
            "relative artifact path contains a control character".into(),
        ));
    }

    let mut saw_component = false;
    for component in raw.split(['/', '\\']) {
        if component.is_empty() {
            return Err(StorageError::InvalidPath(
                "relative artifact path contains an empty component".into(),
            ));
        }
        if component == "." || component == ".." {
            return Err(StorageError::InvalidPath(
                "relative artifact path may not contain '.' or '..'".into(),
            ));
        }
        if component.ends_with(' ') || component.ends_with('.') || component.contains(':') {
            return Err(StorageError::InvalidPath(format!(
                "unsafe Windows path component {component:?}"
            )));
        }
        if is_windows_reserved_component(component) {
            return Err(StorageError::InvalidPath(format!(
                "reserved Windows device name {component:?}"
            )));
        }
        saw_component = true;
    }
    if !saw_component {
        return Err(StorageError::InvalidPath("relative artifact path is empty".into()));
    }
    Ok(())
}

pub fn resolve_relative_path(root: &Path, relative: &Path) -> Result<PathBuf> {
    validate_root_path(root)?;
    validate_relative_path(relative)?;
    Ok(root.join(relative))
}

fn is_windows_absolute(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3
        && bytes[1] == b':'
        && (bytes[2] == b'/' || bytes[2] == b'\\')
}

fn is_network_path(path: &Path) -> bool {
    let value = path.to_string_lossy().replace('\\', "/");
    value.starts_with("//") || value.starts_with("smb://")
}

fn is_windows_reserved_component(component: &str) -> bool {
    let stem = component.split('.').next().unwrap_or_default().to_ascii_uppercase();
    matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "COM1" | "COM2" | "COM3" | "COM4" | "COM5"
            | "COM6" | "COM7" | "COM8" | "COM9" | "LPT1" | "LPT2" | "LPT3" | "LPT4"
            | "LPT5" | "LPT6" | "LPT7" | "LPT8" | "LPT9"
    )
}

pub fn validate_volume_id(value: &str) -> Result<()> {
    if value.is_empty() || value.len() > 256 || value.chars().any(|character| character.is_control()) {
        return Err(StorageError::InvalidVolumeId(
            "volume ID must be 1..256 characters without controls".into(),
        ));
    }
    Ok(())
}

/// Parse canonical decimal wire values without a floating-point conversion.
/// This is shared by storage adapters that read the schema's uint64 strings.
pub fn parse_u64_decimal(value: &str) -> Result<u64> {
    if value.is_empty()
        || (value.len() > 1 && value.starts_with('0'))
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(StorageError::InvalidConfig(
            "expected canonical unsigned decimal string".into(),
        ));
    }
    value.parse::<u64>().map_err(|_| {
        StorageError::InvalidConfig("unsigned decimal string exceeds u64".into())
    })
}

fn read_volume_marker(root: &Path) -> Result<Option<String>> {
    let marker = root.join(".dubflow-volume-id");
    match fs::read_to_string(marker) {
        Ok(value) => {
            let value = value.trim();
            validate_volume_id(value)?;
            Ok(Some(value.to_owned()))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(StorageError::Probe(error.to_string())),
    }
}

fn probe_write(root: &Path) -> io::Result<()> {
    let token = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let partial = root.join(format!(".dubflow-health-{token}.tmp"));
    let published = root.join(format!(".dubflow-health-{token}.probe"));
    let result = (|| {
        let mut file = fs::File::create(&partial)?;
        file.write_all(b"dubflow-health")?;
        file.sync_all()?;
        drop(file);
        fs::rename(&partial, &published)?;
        Ok(())
    })();
    if result.is_ok() {
        let _ = fs::remove_file(published);
    } else {
        let _ = fs::remove_file(partial);
        let _ = fs::remove_file(published);
    }
    result
}

fn probe_atomic_rename(root: &Path) -> io::Result<()> {
    probe_write(root)
}

pub fn quarantine_partial(path: &Path, artifact_id: &str) -> Result<PathBuf> {
    validate_token(artifact_id, "artifact_id")?;
    if !path.exists() {
        return Err(StorageError::Io(format!("partial artifact is missing: {}", path.display())));
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| StorageError::InvalidPath("partial artifact filename is not valid UTF-8".into()))?;
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    for suffix in 0..=1000u16 {
        let suffix_text = if suffix == 0 {
            String::new()
        } else {
            format!(".{suffix}")
        };
        let candidate = parent.join(format!(
            "{file_name}.partial.quarantine.{artifact_id}{suffix_text}"
        ));
        if candidate.exists() {
            continue;
        }
        match fs::rename(path, &candidate) {
            Ok(()) => return Ok(candidate),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(StorageError::Io(error.to_string())),
        }
    }
    Err(StorageError::QuarantineExhausted(path.to_owned()))
}

fn validate_token(value: &str, name: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || value == "."
        || value == ".."
        || value.chars().any(|character| {
            character.is_control() || character == '/' || character == '\\' || character == ':'
        })
    {
        return Err(StorageError::InvalidPath(format!(
            "{name} must be one safe filename token"
        )));
    }
    Ok(())
}

fn normalized_path_for_compare(path: &Path) -> String {
    path.to_string_lossy()
        .replace('\\', "/")
        .trim_end_matches('/')
        .to_ascii_lowercase()
}

pub fn roots_are_distinct(left: &Path, right: &Path) -> bool {
    let left = normalized_path_for_compare(left);
    let right = normalized_path_for_compare(right);
    left != right && !left.starts_with(&(right.clone() + "/")) && !right.starts_with(&(left + "/"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::env;

    #[derive(Clone)]
    struct FakeProbe {
        observations: BTreeMap<RootKind, VolumeObservation>,
        calls: RefCell<usize>,
    }

    impl VolumeProbe for FakeProbe {
        fn observe(&self, root: &RootConfig) -> Result<VolumeObservation> {
            *self.calls.borrow_mut() += 1;
            self.observations
                .get(&root.kind)
                .cloned()
                .ok_or_else(|| StorageError::Probe(format!("missing fake root {}", root.kind.as_str())))
        }
    }

    fn config() -> TopologyConfig {
        let policy = RootPolicy::new(100, 100_000).unwrap();
        TopologyConfig::new(vec![
            RootConfig::new(RootKind::Control, "C:\\DubFlow\\control", true, policy).unwrap(),
            RootConfig::new(RootKind::Source, "D:\\media", true, policy).unwrap(),
            RootConfig::new(RootKind::Output, "E:\\output", true, policy).unwrap(),
            RootConfig::new(RootKind::Model, "C:\\DubFlow\\models", false, policy).unwrap(),
            RootConfig::new(RootKind::Cache, "D:\\cache", false, policy).unwrap(),
            RootConfig::new(RootKind::Temp, "C:\\DubFlow\\temp", true, policy).unwrap(),
        ])
        .unwrap()
    }

    fn observation(id: &str) -> VolumeObservation {
        VolumeObservation {
            exists: true,
            is_directory: true,
            writable: true,
            atomic_rename_supported: true,
            network: false,
            removable: false,
            volume_id: Some(id.into()),
            space: Some(DiskSpace::new(900, 1_000).unwrap()),
            probe_error: None,
        }
    }

    fn healthy_probe() -> FakeProbe {
        let observations = RootKind::ALL
            .into_iter()
            .enumerate()
            .map(|(index, kind)| (kind, observation(&format!("vol-{index}"))))
            .collect();
        FakeProbe {
            observations,
            calls: RefCell::new(0),
        }
    }

    #[test]
    fn topology_requires_exactly_one_of_each_root() {
        let policy = RootPolicy::new(0, 0).unwrap();
        let mut roots = RootKind::ALL
            .into_iter()
            .map(|kind| RootConfig::new(kind, format!("root-{}", kind.as_str()), false, policy).unwrap())
            .collect::<Vec<_>>();
        roots[0].kind = RootKind::Source;
        assert!(matches!(TopologyConfig::new(roots), Err(StorageError::InvalidConfig(_))));
    }

    #[test]
    fn control_network_and_removable_roots_are_forbidden() {
        let policy = RootPolicy::new(0, 0).unwrap();
        assert!(TopologyConfig::new(vec![
            RootConfig::new(RootKind::Control, "\\\\server\\share", true, policy).unwrap(),
            RootConfig::new(RootKind::Source, "source", true, policy).unwrap(),
            RootConfig::new(RootKind::Output, "output", true, policy).unwrap(),
            RootConfig::new(RootKind::Model, "model", false, policy).unwrap(),
            RootConfig::new(RootKind::Cache, "cache", false, policy).unwrap(),
            RootConfig::new(RootKind::Temp, "temp", true, policy).unwrap(),
        ]).is_err());
    }

    #[test]
    fn root_paths_reject_dot_escape_and_nested_aliases() {
        let policy = RootPolicy::new(0, 0).unwrap();
        assert!(RootConfig::new(RootKind::Output, "C:\\DubFlow\\out\\..\\other", true, policy).is_err());
        let roots = vec![
            RootConfig::new(RootKind::Control, "C:\\DubFlow", true, policy).unwrap(),
            RootConfig::new(RootKind::Source, "D:\\source", true, policy).unwrap(),
            RootConfig::new(RootKind::Output, "C:\\DubFlow\\output", true, policy).unwrap(),
            RootConfig::new(RootKind::Model, "C:\\models", false, policy).unwrap(),
            RootConfig::new(RootKind::Cache, "D:\\cache", false, policy).unwrap(),
            RootConfig::new(RootKind::Temp, "E:\\temp", false, policy).unwrap(),
        ];
        assert!(TopologyConfig::new(roots).is_err());
    }

    #[test]
    fn health_is_independent_and_integer_thresholds_are_exact() {
        assert!(DiskSpace::new(0, 0).is_err());
        let mut probe = healthy_probe();
        probe.observations.get_mut(&RootKind::Output).unwrap().space = Some(DiskSpace::new(99, 1_000).unwrap());
        let report = StorageTopology::new(config()).inspect(&probe, 10).unwrap();
        assert_eq!(report.root(RootKind::Output).state, HealthState::LowSpace);
        assert_eq!(report.root(RootKind::Cache).state, HealthState::Available);
        assert_eq!(report.root(RootKind::Output).impact(), JobImpact::PauseAffected);
        assert_eq!(report.root(RootKind::Cache).impact(), JobImpact::Continue);
    }

    #[test]
    fn missing_cache_rebuilds_and_missing_output_pauses_only_affected_jobs() {
        let mut probe = healthy_probe();
        probe.observations.get_mut(&RootKind::Cache).unwrap().exists = false;
        probe.observations.get_mut(&RootKind::Output).unwrap().exists = false;
        let report = StorageTopology::new(config()).inspect(&probe, 10).unwrap();
        assert_eq!(report.root(RootKind::Cache).impact(), JobImpact::RebuildCache);
        assert_eq!(report.root(RootKind::Output).impact(), JobImpact::PauseAffected);
        assert_eq!(report.root(RootKind::Control).impact(), JobImpact::Continue);
    }

    #[test]
    fn one_inaccessible_root_does_not_abort_unrelated_health_checks() {
        let mut probe = healthy_probe();
        probe.observations.get_mut(&RootKind::Output).unwrap().probe_error = Some("SMB timeout".into());
        let report = StorageTopology::new(config()).inspect(&probe, 10).unwrap();
        assert_eq!(report.root(RootKind::Output).state, HealthState::ProbeUnavailable);
        assert_eq!(report.root(RootKind::Output).impact(), JobImpact::PauseAffected);
        assert_eq!(report.root(RootKind::Control).state, HealthState::Available);
    }

    #[test]
    fn network_write_root_without_atomic_publish_is_not_usable() {
        let mut probe = healthy_probe();
        let output = probe.observations.get_mut(&RootKind::Output).unwrap();
        output.network = true;
        output.atomic_rename_supported = false;
        let report = StorageTopology::new(config()).inspect(&probe, 10).unwrap();
        assert_eq!(report.root(RootKind::Output).state, HealthState::AtomicityUnsupported);
        assert!(!report.root(RootKind::Output).is_usable());
    }

    #[test]
    fn resume_invalidates_old_leases_and_rechecks_every_root() {
        let probe = healthy_probe();
        let topology = StorageTopology::new(config());
        let first = topology.inspect(&probe, 10).unwrap();
        let lease = first.lease(RootKind::Output).unwrap();
        let second = topology.revalidate_after_resume(&probe, first.generation, 20).unwrap();
        assert_eq!(second.generation, 1);
        assert_eq!(*probe.calls.borrow(), 12);
        assert!(matches!(second.validate_lease(&lease), Err(StorageError::LeaseGenerationMismatch { .. })));
    }

    #[test]
    fn path_policy_rejects_escape_drive_unc_reserved_and_long_paths() {
        for value in ["", ".", "../outside", "..\\outside", "C:\\outside", "\\\\server\\share", "CON.txt"] {
            assert!(validate_relative_path(Path::new(value)).is_err(), "accepted {value:?}");
        }
        let unicode = Path::new("字幕\\角色\\台詞.ass");
        assert!(validate_relative_path(unicode).is_ok());
        let long = "a".repeat(MAX_WINDOWS_PATH_UTF16 + 1);
        assert!(validate_relative_path(Path::new(&long)).is_err());
    }

    #[test]
    fn decimal_wire_values_are_canonical_and_bounded_to_u64() {
        assert_eq!(parse_u64_decimal("0").unwrap(), 0);
        assert_eq!(parse_u64_decimal("18446744073709551615").unwrap(), u64::MAX);
        assert!(parse_u64_decimal("01").is_err());
        assert!(parse_u64_decimal("18446744073709551616").is_err());
    }

    #[test]
    fn quarantine_uses_same_directory_and_never_overwrites() {
        let root = env::temp_dir().join(format!("dubflow-storage-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let partial = root.join("render.mp4.partial");
        fs::write(&partial, b"partial").unwrap();
        let first = quarantine_partial(&partial, "job-1").unwrap();
        fs::write(&partial, b"partial-2").unwrap();
        let second = quarantine_partial(&partial, "job-1").unwrap();
        assert_ne!(first, second);
        assert!(!partial.exists());
        assert!(first.exists() && second.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn quarantine_rejects_path_injection_tokens() {
        let root = env::temp_dir().join(format!("dubflow-storage-token-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let partial = root.join("render.partial");
        fs::write(&partial, b"partial").unwrap();
        assert!(quarantine_partial(&partial, "job/1").is_err());
        assert!(partial.exists());
        let _ = fs::remove_dir_all(root);
    }
}
