//! Native admission for private retained mix media. No graph publication.
//! OS observations are fresh, path hints are not durable identity, and the
//! canonical storage crate owns locator/path validation. Standard export does
//! not depend on this optional local-NTFS Review path.
use super::review_assets::{
    ChunkCapacity, ExportCopyInput, ExportFileSpec, MixStorageBudget, PcmPin, RetainedCueAudio,
    RetainedFileFormat, RetainedFilePin, RetainedMixAudio, RetainedMixStore, RetainedRenderFile,
};
use super::{SupervisorError, SupervisorResult};
use dubflow_artifact_storage::ArtifactLocator;
use dubflow_job_storage::RootKind;
use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use std::time::Instant;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

#[derive(Clone, Debug)]
struct Observation {
    volume_id: String,
    available_bytes: u64,
}

/// Admit only local NTFS with immutable-object hard-link publication. Quota
/// must use bytes available to this caller, not total volume free bytes.
fn admit_observation(
    guid_path: &str,
    drive_type: u32,
    filesystem: &str,
    flags: u32,
    available: u64,
    caller_total: u64,
    volume_free: u64,
) -> SupervisorResult<Observation> {
    let lowered = guid_path.to_ascii_lowercase();
    let guid = lowered
        .strip_prefix(r"\\?\volume{")
        .and_then(|s| s.strip_suffix(r"}\"))
        .filter(|s| s.len() == 36)
        .ok_or_else(|| invalid("Review requires an OS volume GUID, not a drive-letter hint"))?;
    if !guid.bytes().enumerate().all(|(i, b)| {
        if [8, 13, 18, 23].contains(&i) {
            b == b'-'
        } else {
            b.is_ascii_hexdigit()
        }
    }) || guid == "00000000-0000-0000-0000-000000000000"
        || drive_type != 3 // DRIVE_FIXED. Other providers need qualification.
        || filesystem != "NTFS"
        || flags & 0x0040_0000 == 0 // FILE_SUPPORTS_HARD_LINKS.
        || flags & 0x0008_0000 != 0 // FILE_READ_ONLY_VOLUME.
        || available > caller_total
        || available > volume_free
    {
        return Err(invalid(
            "unsupported, read-only or inconsistent Review output volume",
        ));
    }
    Ok(Observation {
        volume_id: format!("windows-volume-guid:{guid}"),
        available_bytes: available,
    })
}

#[cfg(windows)]
fn probe(path: &Path) -> SupervisorResult<Observation> {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Component;
    use std::ptr::null_mut;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetVolumePathNameW(path: *const u16, volume_path: *mut u16, length: u32) -> i32;
        fn GetVolumeNameForVolumeMountPointW(path: *const u16, name: *mut u16, length: u32) -> i32;
        fn GetDriveTypeW(path: *const u16) -> u32;
        fn GetVolumeInformationW(
            path: *const u16,
            name: *mut u16,
            name_length: u32,
            serial: *mut u32,
            maximum_component: *mut u32,
            flags: *mut u32,
            filesystem: *mut u16,
            filesystem_length: u32,
        ) -> i32;
        fn GetDiskFreeSpaceExW(
            path: *const u16,
            available: *mut u64,
            total: *mut u64,
            free: *mut u64,
        ) -> i32;
        fn SetThreadErrorMode(mode: u32, previous: *mut u32) -> i32;
        fn GetThreadErrorMode() -> u32;
    }
    struct ErrorMode(u32);
    impl Drop for ErrorMode {
        fn drop(&mut self) {
            // Restore this thread only, including every error return below.
            unsafe {
                SetThreadErrorMode(self.0, null_mut());
            }
        }
    }
    fn checked(result: i32) -> SupervisorResult<()> {
        if result == 0 {
            Err(std::io::Error::last_os_error().into())
        } else {
            Ok(())
        }
    }
    fn text(buffer: &[u16]) -> SupervisorResult<String> {
        let length = buffer
            .iter()
            .position(|c| *c == 0)
            .ok_or_else(|| invalid("unterminated Windows storage observation"))?;
        String::from_utf16(&buffer[..length])
            .map_err(|_| invalid("Windows storage observation is not valid UTF-16"))
    }
    super::review_pin::plain(path)?;
    if !path.is_absolute()
        || path.components().any(|c| matches!(c, Component::ParentDir))
        || !path.is_dir()
    {
        return Err(invalid(
            "Review output must be an existing absolute plain directory",
        ));
    }
    let canonical = std::fs::canonicalize(path)?;
    let mut wide: Vec<u16> = canonical.as_os_str().encode_wide().collect();
    if wide.contains(&0) || wide.len() >= 32767 {
        return Err(invalid(
            "Review output path exceeds the Windows storage probe bound",
        ));
    }
    if wide.last() != Some(&u16::from(b'\\')) {
        wide.push(u16::from(b'\\'));
    }
    wide.push(0);
    let mut previous = 0;
    // Suppress critical-error dialogs without changing another thread's mode.
    checked(unsafe { SetThreadErrorMode(GetThreadErrorMode() | 1, &mut previous) })?;
    let _mode = ErrorMode(previous);
    let mut mount = vec![0u16; 32768];
    checked(unsafe { GetVolumePathNameW(wide.as_ptr(), mount.as_mut_ptr(), mount.len() as u32) })?;
    let drive_type = unsafe { GetDriveTypeW(mount.as_ptr()) };
    if drive_type != 3 {
        return Err(invalid(
            "private retained Review media currently requires a fixed local volume",
        ));
    }
    let mut guid = [0u16; 64];
    checked(unsafe {
        GetVolumeNameForVolumeMountPointW(mount.as_ptr(), guid.as_mut_ptr(), guid.len() as u32)
    })?;
    let mut filesystem = [0u16; 64];
    let mut flags = 0;
    checked(unsafe {
        GetVolumeInformationW(
            mount.as_ptr(),
            null_mut(),
            0,
            null_mut(),
            null_mut(),
            &mut flags,
            filesystem.as_mut_ptr(),
            filesystem.len() as u32,
        )
    })?;
    let (mut available, mut total, mut free) = (0u64, 0u64, 0u64);
    checked(unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut available, &mut total, &mut free) })?;
    // Neither a missing folder nor a changed mount may silently fall back to C:.
    super::review_pin::plain(path)?;
    if std::fs::canonicalize(path)? != canonical {
        return Err(invalid(
            "Review output root moved during its volume observation",
        ));
    }
    admit_observation(
        &text(&guid)?,
        drive_type,
        &text(&filesystem)?,
        flags,
        available,
        total,
        free,
    )
}

#[cfg(not(windows))]
fn probe(_path: &Path) -> SupervisorResult<Observation> {
    Err(invalid(
        "native retained Review media requires a qualified Windows storage provider",
    ))
}

#[derive(Clone, Debug)]
pub(super) struct LocatedMixAudio {
    audio: RetainedMixAudio,
    locator: ArtifactLocator,
}

#[derive(Clone, Debug)]
pub(super) struct LocatedRenderFile {
    file: RetainedRenderFile,
    locator: ArtifactLocator,
}

#[derive(Clone, Debug)]
pub(super) struct LocatedCueAudio {
    audio: RetainedCueAudio,
    locator: ArtifactLocator,
}
impl LocatedCueAudio {
    pub(super) fn path(&self) -> &Path {
        &self.audio.path
    }
    pub(super) fn pin(&self) -> &PcmPin {
        &self.audio.pin
    }
    pub(super) fn descriptor(&self) -> Value {
        json!({"kind":"review_retained_cue_audio","path_hint":self.audio.path,
            "locator":{"schema_version":self.locator.schema_version,"volume_id":self.locator.volume_id,
                "root_kind":self.locator.root_kind.as_str(),"relative_path":self.locator.relative_path,
                "content_hash":self.locator.content_hash,"size_bytes":self.locator.size_bytes.to_string()},
            "sample_rate":48000,"channels":1,"frame_count":self.audio.pin.frame_count.to_string()})
    }
}

fn cue_descriptor(value: &Value, root: &Path, volume: &str) -> SupervisorResult<LocatedCueAudio> {
    use super::review_document::{closed, tick};
    closed(
        value,
        &[
            "kind",
            "path_hint",
            "locator",
            "sample_rate",
            "channels",
            "frame_count",
        ],
    )?;
    if value["kind"] != "review_retained_cue_audio"
        || value["sample_rate"].as_u64() != Some(48000)
        || value["channels"].as_u64() != Some(1)
    {
        return Err(invalid("retained cue changed its explicit PCM role/layout"));
    }
    let file = render_file_descriptor(
        &json!({"path_hint":value["path_hint"],"format":"json","locator":value["locator"]}),
        root,
        volume,
    )?;
    Ok(LocatedCueAudio {
        audio: RetainedCueAudio {
            path: file.file.path,
            pin: PcmPin {
                sha256: file.file.pin.sha256,
                size_bytes: file.file.pin.size_bytes,
                sample_rate: 48000,
                channels: 1,
                frame_count: tick(&value["frame_count"], true)?,
            },
        },
        locator: file.locator,
    })
}
impl LocatedRenderFile {
    pub(super) fn descriptor(&self) -> Value {
        json!({"path_hint":self.file.path,"format":self.file.pin.format.extension(),
            "locator":{"schema_version":self.locator.schema_version,"volume_id":self.locator.volume_id,
                "root_kind":self.locator.root_kind.as_str(),"relative_path":self.locator.relative_path,
                "content_hash":self.locator.content_hash,"size_bytes":self.locator.size_bytes.to_string()}})
    }

    pub(super) fn path(&self) -> &Path {
        &self.file.path
    }
}

// Bind persisted descriptors to the freshly admitted root/volume. The object
// store independently derives the permitted job/content/format filename.
fn render_file_descriptor(
    value: &Value,
    root: &Path,
    volume: &str,
) -> SupervisorResult<LocatedRenderFile> {
    use super::review_document::{closed, tick};
    closed(value, &["path_hint", "format", "locator"])?;
    value["path_hint"]
        .as_str()
        .filter(|hint| hint.len() <= 4096 && !hint.contains('\0'))
        .ok_or_else(|| invalid("retained render path hint is malformed"))?;
    let format = match value["format"].as_str() {
        Some("mp4") => RetainedFileFormat::Mp4,
        Some("srt") => RetainedFileFormat::Srt,
        Some("ass") => RetainedFileFormat::Ass,
        Some("json") => RetainedFileFormat::Json,
        _ => return Err(invalid("unsupported retained render format")),
    };
    let locator = &value["locator"];
    closed(
        locator,
        &[
            "schema_version",
            "volume_id",
            "root_kind",
            "relative_path",
            "content_hash",
            "size_bytes",
        ],
    )?;
    if locator["schema_version"].as_u64() != Some(1)
        || locator["volume_id"] != volume
        || locator["root_kind"] != "output"
    {
        return Err(invalid(
            "retained render recovery changed volume/root/version",
        ));
    }
    let relative = locator["relative_path"]
        .as_str()
        .ok_or_else(|| invalid("retained render relative path is missing"))?;
    let sha256 = locator["content_hash"]
        .as_str()
        .ok_or_else(|| invalid("retained render content hash is missing"))?;
    let size_raw = locator["size_bytes"]
        .as_str()
        .ok_or_else(|| invalid("retained render size must be a decimal string"))?;
    let size_bytes = tick(&Value::String(size_raw.into()), true)?;
    let admitted = ArtifactLocator::new(volume, RootKind::Output, relative, sha256, size_bytes)
        .map_err(|error| {
            invalid(&format!(
                "retained render recovery locator refused: {error}"
            ))
        })?;
    if admitted.relative_path != Path::new(relative) {
        return Err(invalid("retained render recovery path is not canonical"));
    }
    Ok(LocatedRenderFile {
        file: RetainedRenderFile {
            path: root.join(&admitted.relative_path),
            pin: RetainedFilePin {
                sha256: sha256.into(),
                size_bytes,
                format,
            },
        },
        locator: admitted,
    })
}

impl LocatedMixAudio {
    pub(super) fn path(&self) -> &Path {
        &self.audio.path
    }

    pub(super) fn descriptor(&self) -> Value {
        json!({"path_hint":self.audio.path,
            "locator":{"schema_version":self.locator.schema_version,
            "volume_id":self.locator.volume_id,"root_kind":self.locator.root_kind.as_str(),
            "relative_path":self.locator.relative_path,"content_hash":self.locator.content_hash,
            "size_bytes":self.locator.size_bytes.to_string()},
            "sample_rate":self.audio.pin.sample_rate,"channels":self.audio.pin.channels,
            "frame_count":self.audio.pin.frame_count.to_string()})
    }
}

// The caller has already admitted producer/source/composition semantics. This
// private transformation cannot admit physical media or publish a graph. Its
// production entry below verifies typed native objects before calling it.
fn retarget_metadata(
    admitted: &str,
    composition_hash: &str,
    objects: &[(&str, LocatedMixAudio)],
) -> SupervisorResult<String> {
    use super::review_document::{canonical, decode};
    const ROLES: [&str; 3] = ["original_audio", "dialogue_stem", "final_mix"];
    let mut mix = decode(admitted, 64 * 1024 * 1024)?;
    if mix["schema_version"].as_u64() != Some(1)
        || mix["kind"] != "audio_mix_document"
        || mix["provenance"]["input_hash"] != composition_hash
        || mix["sample_rate"].as_u64() != Some(48000)
        || mix["channels"].as_u64() != Some(2)
        || objects.len() != ROLES.len()
        || objects
            .iter()
            .zip(ROLES)
            .any(|((role, _), expected)| *role != expected)
    {
        return Err(invalid(
            "retained mix differs from its admitted composition or roles",
        ));
    }
    let frames = mix["original_audio"]["frame_count"].as_u64();
    for (role, object) in objects {
        let pin = &object.audio.pin;
        let artifact = &mix[*role];
        let path = object
            .audio
            .path
            .to_str()
            .filter(|p| !p.is_empty() && p.len() <= 4096)
            .ok_or_else(|| {
                invalid("retained mix path cannot be represented by its artifact contract")
            })?;
        if pin.sample_rate != 48000
            || pin.channels != 2
            || !(1..=48000 * 6 * 3600).contains(&pin.frame_count)
            || !(44..=u64::from(u32::MAX) + 8).contains(&pin.size_bytes)
            || pin
                .frame_count
                .checked_mul(4)
                .and_then(|n| n.checked_add(44))
                .is_none_or(|minimum| minimum > pin.size_bytes)
            || frames != Some(pin.frame_count)
            || artifact["kind"] != *role
            || artifact["format"] != "wav"
            || artifact["bits_per_sample"].as_u64() != Some(16)
            || artifact["sample_rate"].as_u64() != Some(u64::from(pin.sample_rate))
            || artifact["channels"].as_u64() != Some(u64::from(pin.channels))
            || artifact["frame_count"].as_u64() != Some(pin.frame_count)
            || artifact["content_hash"] != pin.sha256
            || artifact["metrics"]["content_hash"] != artifact["content_hash"]
            || artifact["metrics"]["frame_count"] != artifact["frame_count"]
        {
            return Err(invalid(
                "retained mix substitutes admitted role content, metrics or PCM layout",
            ));
        }
        // All other fields, including wide source ticks and producer/model
        // pins, keep their admitted values. No mutable producer path is read.
        mix[*role]["path"] = json!(path);
    }
    let mut result = String::new();
    canonical(&mix, &mut result)?;
    result.push('\n');
    decode(&result, 64 * 1024 * 1024)?;
    Ok(result)
}

pub(super) struct NativeMixStore {
    root: PathBuf,
    volume_id: String,
    objects: RetainedMixStore,
}

/// Captured before any expensive work, then compared at retention admission.
/// No caller-controlled volume label or filesystem marker is accepted.
pub(super) struct NativeOutputPin {
    root: PathBuf,
    volume_id: String,
}

impl NativeOutputPin {
    pub(super) fn root(&self) -> &Path {
        &self.root
    }
    pub(super) fn volume_id(&self) -> &str {
        &self.volume_id
    }

    pub(super) fn read(root: &Path) -> SupervisorResult<Self> {
        let observed = probe(root)?;
        let root = std::fs::canonicalize(root)?;
        same_volume(&observed.volume_id, &probe(&root)?)?;
        Ok(Self {
            root,
            volume_id: observed.volume_id,
        })
    }
}

fn scratch_budget() -> MixStorageBudget {
    MixStorageBudget {
        maximum_bytes: 32 * 1024 * 1024 * 1024,
        minimum_free_bytes: 2 * 1024 * 1024 * 1024,
    }
}

fn scratch_owner(pin: &NativeOutputPin) -> String {
    use sha2::{Digest, Sha256};
    format!(
        "{:x}",
        Sha256::digest(
            format!(
                "review-scratch-owner-v1\n{}\n{}",
                pin.volume_id,
                pin.root.display()
            )
            .as_bytes()
        )
    )
}

// This is an output-wide storage namespace for credits, NOT a job/model pin.
const SCRATCH_CREDITS: &str = super::review_assets::SCRATCH_CREDITS;

/// Pure command intent. Filesystem admission belongs to the collector's one
/// original deadline, after all command arguments have been built.
pub(super) struct PendingScratch {
    output: PathBuf,
    scratch: PathBuf,
}
impl PendingScratch {
    pub(super) fn admit(self, deadline: Instant) -> SupervisorResult<NativeScratchLease> {
        if Instant::now() >= deadline {
            return Err(invalid("scratch admission deadline expired"));
        }
        let pin = NativeOutputPin::read(&self.output)?;
        let media = NativeMixStore::new_until(
            &pin,
            SCRATCH_CREDITS,
            scratch_budget(),
            deadline,
            &mut || false,
        )?;
        let owner = scratch_owner(&pin);
        let expected = pin.volume_id().to_owned();
        // The worker receives the original DOS/extended spelling. Identity and
        // quota admission use the pinned canonical root, never a shorter alias.
        let relative = self
            .scratch
            .strip_prefix(&self.output)
            .map_err(|_| invalid("scratch escaped output"))?;
        let canonical_scratch = pin.root().join(relative);
        let credit = media.objects.reserve_scratch(
            &canonical_scratch,
            &owner,
            super::review_assets::SCRATCH_ALLOWANCE,
            |path| {
                let observed = probe(path)?;
                same_volume(&expected, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        same_volume(pin.volume_id(), &probe(&self.scratch)?)?;
        if self.scratch.canonicalize()? != canonical_scratch || Instant::now() >= deadline {
            return Err(invalid(
                "scratch identity or original deadline changed during admission",
            ));
        }
        Ok(NativeScratchLease { pin, credit })
    }
}

pub(super) struct NativeScratchLease {
    pin: NativeOutputPin,
    credit: super::review_assets::ScratchCredit,
}

impl NativeScratchLease {
    fn retire(self, deadline: Instant) -> SupervisorResult<()> {
        if Instant::now() >= deadline {
            return Err(invalid(
                "scratch retirement deadline expired; credit retained",
            ));
        }
        let media = NativeMixStore::open_existing(&self.pin, SCRATCH_CREDITS, scratch_budget())?;
        let expected = self.pin.volume_id().to_owned();
        media.objects.retire_scratch(
            self.credit,
            |path| {
                let observed = probe(path)?;
                same_volume(&expected, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )
    }
    pub(super) fn retire_unstarted(self, deadline: Instant) -> SupervisorResult<()> {
        self.retire(deadline)
    }
    pub(super) fn retire_stopped(
        self,
        _proof: super::worker_tree::RetiredTree,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        self.retire(deadline)
    }
}

/// Ordinary commands carry no new authority. A scratch command cannot be
/// converted back into a bare Command and silently drop a live reservation.
pub(super) struct OwnedReviewCommand {
    command: std::process::Command,
    scratch: Option<PendingScratch>,
    prepared_at: Option<Instant>,
}
impl From<std::process::Command> for OwnedReviewCommand {
    fn from(command: std::process::Command) -> Self {
        Self {
            command,
            scratch: None,
            prepared_at: None,
        }
    }
}
impl std::ops::Deref for OwnedReviewCommand {
    type Target = std::process::Command;
    fn deref(&self) -> &Self::Target {
        &self.command
    }
}
impl std::ops::DerefMut for OwnedReviewCommand {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.command
    }
}
impl OwnedReviewCommand {
    pub(super) fn into_parts(
        self,
    ) -> (
        std::process::Command,
        Option<PendingScratch>,
        Option<Instant>,
    ) {
        (self.command, self.scratch, self.prepared_at)
    }
}

pub(super) fn own_scratch_command(
    command: std::process::Command,
    scratch: &Path,
) -> SupervisorResult<OwnedReviewCommand> {
    let output = scratch
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| invalid("missing admitted scratch output"))?;
    if !output.is_absolute()
        || scratch
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
        || (scratch != output.join(".dfr/temp")
            && scratch != output.join(".dubflow-review-candidates/temp"))
    {
        return Err(invalid(
            "scratch intent escaped its admitted output namespace",
        ));
    }
    Ok(OwnedReviewCommand {
        command,
        scratch: Some(PendingScratch {
            output: output.to_path_buf(),
            scratch: scratch.to_path_buf(),
        }),
        prepared_at: Some(Instant::now()),
    })
}

fn same_volume(expected: &str, observed: &Observation) -> SupervisorResult<()> {
    if observed.volume_id != expected {
        return Err(invalid(
            "Review output volume changed; restore or explicitly rebind it",
        ));
    }
    Ok(())
}

fn ordered_pcm_entries(value: &Value) -> SupervisorResult<Vec<(&'static str, &Value)>> {
    let entries = value
        .as_array()
        .filter(|entries| entries.len() == 3)
        .ok_or_else(|| invalid("retained mix requires three ordered PCM roles"))?;
    entries
        .iter()
        .zip(["original_audio", "dialogue_stem", "final_mix"])
        .map(|(entry, role)| {
            super::review_document::closed(entry, &["role", "audio"])?;
            if entry["role"] != role {
                return Err(invalid("retained PCM role substituted"));
            }
            Ok((role, &entry["audio"]))
        })
        .collect()
}

impl NativeMixStore {
    pub(super) fn editable_pending_directory(&self, generation: &str) -> SupervisorResult<PathBuf> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.editable_pending_directory(generation)?;
        same_volume(&self.volume_id, &probe(&path)?)?;
        Ok(path)
    }

    pub(super) fn editable_published_directory(
        &self,
        generation: &str,
    ) -> SupervisorResult<Option<PathBuf>> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.editable_published_directory(generation)?;
        if let Some(path) = &path {
            same_volume(&self.volume_id, &probe(path)?)?;
        }
        Ok(path)
    }

    pub(super) fn editable_capacity(&self, deadline: Instant) -> SupervisorResult<()> {
        let observed = probe(&self.root)?;
        same_volume(&self.volume_id, &observed)?;
        self.objects
            .editable_capacity(observed.available_bytes, deadline)
    }

    pub(super) fn publish_editable_directory(
        &self,
        generation: &str,
        files: &[super::review_assets::EditableFileSpec],
        fence: impl FnMut() -> SupervisorResult<()>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.publish_editable_directory(
            generation,
            files,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            fence,
            deadline,
        )?;
        same_volume(&self.volume_id, &probe(&path)?)?;
        Ok(path)
    }

    pub(super) fn export_remaining_bytes(
        &self,
        generation: &str,
        files: &[ExportFileSpec],
        deadline: Instant,
    ) -> SupervisorResult<Vec<u64>> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        self.objects
            .export_remaining_bytes(generation, files, deadline)
    }

    pub(super) fn export_pending_directory(&self, generation: &str) -> SupervisorResult<PathBuf> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.export_pending_directory(generation)?;
        same_volume(&self.volume_id, &probe(&path)?)?;
        Ok(path)
    }

    pub(super) fn export_published_directory(
        &self,
        generation: &str,
    ) -> SupervisorResult<Option<PathBuf>> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.export_published_directory(generation)?;
        if let Some(path) = &path {
            same_volume(&self.volume_id, &probe(path)?)?;
        }
        Ok(path)
    }

    pub(super) fn publish_export_directory(
        &self,
        generation: &str,
        files: &[ExportFileSpec],
        fence: impl FnMut() -> SupervisorResult<()>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.publish_export_directory(
            generation,
            files,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            fence,
            deadline,
        )?;
        same_volume(&self.volume_id, &probe(&path)?)?;
        Ok(path)
    }

    pub(super) fn copy_export_file(
        &self,
        input: &ExportCopyInput<'_>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let path = self.objects.copy_export_file(
            input,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        same_volume(
            &self.volume_id,
            &probe(
                path.parent()
                    .ok_or_else(|| invalid("export copy parent missing"))?,
            )?,
        )?;
        Ok(path)
    }

    pub(super) fn accept_cue(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &PcmPin,
        deadline: Instant,
    ) -> SupervisorResult<LocatedCueAudio> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let audio = self.objects.accept_cue(
            candidate_root,
            candidate,
            pin,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        let relative = audio
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained cue escaped output root"))?;
        let locator = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &pin.sha256,
            pin.size_bytes,
        )
        .map_err(|error| invalid(&format!("retained cue locator refused: {error}")))?;
        let result = LocatedCueAudio { audio, locator };
        self.verify_cue(&result, deadline)?;
        Ok(result)
    }

    pub(super) fn reopen_cue(
        &self,
        value: &Value,
        deadline: Instant,
    ) -> SupervisorResult<LocatedCueAudio> {
        let object = cue_descriptor(value, &self.root, &self.volume_id)?;
        self.verify_cue(&object, deadline)?;
        Ok(object)
    }

    pub(super) fn verify_cue(
        &self,
        object: &LocatedCueAudio,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let relative = object
            .audio
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained cue outside output root"))?;
        let expected = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &object.audio.pin.sha256,
            object.audio.pin.size_bytes,
        )
        .map_err(|error| invalid(&format!("retained cue locator refused: {error}")))?;
        if expected != object.locator {
            return Err(invalid(
                "retained cue locator changed job/volume/content identity",
            ));
        }
        self.objects.verify_cue(&object.audio, deadline)?;
        same_volume(
            &self.volume_id,
            &probe(
                object
                    .audio
                    .path
                    .parent()
                    .ok_or_else(|| invalid("retained cue parent missing"))?,
            )?,
        )?;
        Ok(())
    }

    pub(super) fn new(
        pin: &NativeOutputPin,
        job: &str,
        budget: MixStorageBudget,
    ) -> SupervisorResult<Self> {
        Self::open(pin, job, budget, false)
    }

    pub(super) fn open_existing(
        pin: &NativeOutputPin,
        job: &str,
        budget: MixStorageBudget,
    ) -> SupervisorResult<Self> {
        Self::open(pin, job, budget, true)
    }

    pub(super) fn new_until(
        pin: &NativeOutputPin,
        job: &str,
        budget: MixStorageBudget,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<Self> {
        if cancelled() || Instant::now() >= deadline {
            return Err(invalid("native output writer wait cancelled or expired"));
        }
        let observed = probe(&pin.root)?;
        same_volume(&pin.volume_id, &observed)?;
        let objects = RetainedMixStore::new_until(&pin.root, job, budget, deadline, cancelled)?;
        // Acquisition may have waited for another supervisor. Never carry an
        // earlier volume observation across that wait as a fresh admission.
        same_volume(&pin.volume_id, &probe(&pin.root)?)?;
        if cancelled() || Instant::now() >= deadline {
            return Err(invalid("native output writer wait exhausted its phase"));
        }
        Ok(Self {
            root: pin.root.clone(),
            volume_id: observed.volume_id,
            objects,
        })
    }

    fn open(
        pin: &NativeOutputPin,
        job: &str,
        budget: MixStorageBudget,
        existing: bool,
    ) -> SupervisorResult<Self> {
        let observed = probe(&pin.root)?;
        same_volume(&pin.volume_id, &observed)?;
        let root = pin.root.clone();
        let objects = if existing {
            RetainedMixStore::open_existing(&root, job, budget)?
        } else {
            RetainedMixStore::new(&root, job, budget)?
        };
        same_volume(&observed.volume_id, &probe(&root)?)?;
        Ok(Self {
            root,
            volume_id: observed.volume_id,
            objects,
        })
    }

    pub(super) fn accept(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &PcmPin,
        deadline: Instant,
    ) -> SupervisorResult<LocatedMixAudio> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let audio = self.objects.accept(
            candidate_root,
            candidate,
            pin,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        let relative = audio
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained Review audio escaped its native output root"))?;
        let locator = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &pin.sha256,
            pin.size_bytes,
        )
        .map_err(|e| invalid(&format!("retained Review locator refused: {e}")))?;
        let result = LocatedMixAudio { audio, locator };
        self.verify(&result, deadline)?;
        Ok(result)
    }

    pub(super) fn accept_file(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &RetainedFilePin,
        deadline: Instant,
    ) -> SupervisorResult<LocatedRenderFile> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let file = self.objects.accept_file(
            candidate_root,
            candidate,
            pin,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        let relative = file
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained render escaped output root"))?;
        let locator = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &pin.sha256,
            pin.size_bytes,
        )
        .map_err(|error| invalid(&format!("retained render locator refused: {error}")))?;
        let result = LocatedRenderFile { file, locator };
        self.verify_file(&result, deadline)?;
        Ok(result)
    }

    /// Small native checkpoint metadata is physically retained on the same
    /// output volume and under the same writer/quota lease as video/PCM.
    pub(super) fn accept_json_record(
        &self,
        raw: &str,
        deadline: Instant,
    ) -> SupervisorResult<LocatedRenderFile> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let file = self.objects.accept_json_record(
            raw,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        let relative = file
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("checkpoint metadata escaped output root"))?;
        let locator = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &file.pin.sha256,
            file.pin.size_bytes,
        )
        .map_err(|e| invalid(&format!("checkpoint metadata locator refused: {e}")))?;
        let result = LocatedRenderFile { file, locator };
        self.verify_file(&result, deadline)?;
        Ok(result)
    }

    pub(super) fn read_json_record(
        &self,
        object: &LocatedRenderFile,
        deadline: Instant,
    ) -> SupervisorResult<String> {
        self.verify_file(object, deadline)?;
        let raw = self.objects.read_json_record(&object.file, deadline)?;
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        self.verify_file(object, deadline)?;
        Ok(raw)
    }

    pub(super) fn publish_chunk_checkpoint(
        &self,
        plan: &str,
        ordinal: u16,
        raw: &str,
        deadline: Instant,
    ) -> SupervisorResult<String> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let saved = self.objects.publish_chunk_checkpoint(
            plan,
            ordinal,
            raw,
            |path| {
                let observed = probe(path)?;
                same_volume(&self.volume_id, &observed)?;
                Ok(observed.available_bytes)
            },
            deadline,
        )?;
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        Ok(saved)
    }

    pub(super) fn reserve_chunk_capacity(
        &self,
        scope: &str,
        maximum: u64,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<ChunkCapacity> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let result = self.objects.reserve_chunk_capacity(
            scope,
            maximum,
            |path| {
                let observation = probe(path)?;
                same_volume(&self.volume_id, &observation)?;
                Ok(observation.available_bytes)
            },
            deadline,
            cancelled,
        )?;
        same_volume(&self.volume_id, &probe(result.work_root())?)?;
        Ok(result)
    }

    pub(super) fn check_chunk_capacity(
        &self,
        token: &ChunkCapacity,
        additional: u64,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<()> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        self.objects.check_chunk_capacity(
            token,
            additional,
            |path| {
                let observation = probe(path)?;
                same_volume(&self.volume_id, &observation)?;
                Ok(observation.available_bytes)
            },
            deadline,
            cancelled,
        )?;
        same_volume(&self.volume_id, &probe(token.work_root())?)?;
        Ok(())
    }

    pub(super) fn release_chunk_capacity(
        &self,
        token: ChunkCapacity,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<()> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        same_volume(&self.volume_id, &probe(token.work_root())?)?;
        self.objects
            .release_chunk_capacity(token, deadline, cancelled)?;
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        Ok(())
    }

    pub(super) fn read_chunk_checkpoints(
        &self,
        plan: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<Vec<(u16, String)>> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let records = self
            .objects
            .read_chunk_checkpoints(plan, deadline, cancelled)?;
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        Ok(records)
    }

    pub(super) fn verify_file(
        &self,
        object: &LocatedRenderFile,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let relative = object
            .file
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained render is outside output root"))?;
        let expected = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &object.file.pin.sha256,
            object.file.pin.size_bytes,
        )
        .map_err(|error| invalid(&format!("retained render locator refused: {error}")))?;
        if expected != object.locator {
            return Err(invalid(
                "retained render locator changed volume/path/content pins",
            ));
        }
        self.objects.verify_file(&object.file, deadline)?;
        same_volume(&self.volume_id, &probe(object.file.path.parent().unwrap())?)?;
        Ok(())
    }

    /// Reconstruct only from its persisted locator. Never read the absolute
    /// hint, adopt a candidate, create replacement files or repair bytes.
    pub(super) fn reopen_file(
        &self,
        descriptor: &Value,
        deadline: Instant,
    ) -> SupervisorResult<LocatedRenderFile> {
        let object = render_file_descriptor(descriptor, &self.root, &self.volume_id)?;
        self.verify_file(&object, deadline)?;
        Ok(object)
    }

    /// Recover PCM only from canonical locators; hints never authorize IO.
    pub(super) fn reopen_audio(
        &self,
        value: &Value,
        deadline: Instant,
    ) -> SupervisorResult<LocatedMixAudio> {
        use super::review_document::{closed, tick};
        closed(
            value,
            &[
                "path_hint",
                "locator",
                "sample_rate",
                "channels",
                "frame_count",
            ],
        )?;
        if value["sample_rate"].as_u64() != Some(48000) || value["channels"].as_u64() != Some(2) {
            return Err(invalid("retained recovery changed PCM layout"));
        }
        let frames = tick(&value["frame_count"], true)?;
        if frames > 48000 * 6 * 3600 {
            return Err(invalid("retained recovery PCM duration exceeds its bound"));
        }
        // Reuse the closed locator parser, then independently verify the PCM
        // namespace/content filename and full streamed header/bytes below.
        let file = render_file_descriptor(
            &json!({"path_hint":value["path_hint"],"format":"json","locator":value["locator"]}),
            &self.root,
            &self.volume_id,
        )?;
        let object = LocatedMixAudio {
            audio: RetainedMixAudio {
                path: file.file.path,
                pin: PcmPin {
                    sha256: file.file.pin.sha256,
                    size_bytes: file.file.pin.size_bytes,
                    sample_rate: 48000,
                    channels: 2,
                    frame_count: frames,
                },
            },
            locator: file.locator,
        };
        self.verify(&object, deadline)?;
        Ok(object)
    }

    pub(super) fn verify(
        &self,
        object: &LocatedMixAudio,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        same_volume(&self.volume_id, &probe(&self.root)?)?;
        let relative = object
            .audio
            .path
            .strip_prefix(&self.root)
            .map_err(|_| invalid("retained Review audio is outside its output root"))?;
        let expected = ArtifactLocator::new(
            &self.volume_id,
            RootKind::Output,
            relative,
            &object.audio.pin.sha256,
            object.audio.pin.size_bytes,
        )
        .map_err(|e| invalid(&format!("retained Review locator refused: {e}")))?;
        if object.locator != expected {
            return Err(invalid(
                "retained Review locator changed volume, path or content pins",
            ));
        }
        // Existing native verifier is streamed, deadline-bound and rejects
        // links/nonregular objects; do not replace it with unbounded from_file.
        self.objects.verify(&object.audio, deadline)?;
        same_volume(
            &self.volume_id,
            &probe(object.audio.path.parent().unwrap())?,
        )?;
        Ok(())
    }

    /// Bind metadata only to this native store's canonical locators and sealed
    /// physical media. Neither caller JSON nor a Python result grants authority.
    pub(super) fn retarget_admitted_document(
        &self,
        admitted: &str,
        composition_hash: &str,
        objects: &[(&str, LocatedMixAudio)],
        deadline: Instant,
    ) -> SupervisorResult<String> {
        for (_, object) in objects {
            self.verify(object, deadline)?;
        }
        retarget_metadata(admitted, composition_hash, objects)
    }

    /// Re-fence an already retained mix through its canonical native locators.
    /// The output pin predates inference; neither a path hint nor DOS spelling
    /// establishes volume/root authority. Reopen checks the job namespace,
    /// readonly PCM header/complete hashes and fresh volume observations.
    pub(super) fn revalidate_admitted_document(
        &self,
        candidate: &str,
        composition_hash: &str,
        entries: &Value,
        retained: &str,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        let mut objects = Vec::new();
        for (role, descriptor) in ordered_pcm_entries(entries)? {
            objects.push((role, self.reopen_audio(descriptor, deadline)?));
        }
        if self.retarget_admitted_document(candidate, composition_hash, &objects, deadline)?
            != retained
        {
            return Err(invalid(
                "retained mix is not its exact native path-only retarget",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordinary_command_conversion_has_no_scratch_credit_or_changed_arguments() {
        let mut command: OwnedReviewCommand = std::process::Command::new("owned-fixture").into();
        command.args(["first", "second"]);
        let (command, lease, started) = command.into_parts();
        assert!(lease.is_none() && started.is_none());
        assert_eq!(
            command
                .get_args()
                .map(|s| s.to_str().unwrap())
                .collect::<Vec<_>>(),
            ["first", "second"]
        );
    }
    #[cfg(windows)]
    #[test]
    fn scratch_command_intent_never_creates_storage_before_collector_deadline() {
        let root =
            std::env::temp_dir().join(format!("absent-scratch-intent-{}", std::process::id()));
        assert!(!root.exists());
        let owned = own_scratch_command(
            std::process::Command::new("never-spawn-fixture"),
            &root.join(".dfr/temp"),
        )
        .unwrap();
        let (_, intent, _) = owned.into_parts();
        assert!(intent.unwrap().admit(Instant::now()).is_err());
        assert!(!root.exists());
        assert!(
            own_scratch_command(
                std::process::Command::new("never-spawn-fixture"),
                &root.join("ambient/temp")
            )
            .is_err()
        );
    }

    const GUID: &str = r"\\?\Volume{01234567-89AB-CDEF-0123-456789abcdef}\";
    const HARD_LINKS: u32 = 0x0040_0000;

    #[cfg(windows)]
    #[test]
    fn native_json_record_uses_output_volume_canonical_locator_and_independent_reopen() {
        use std::fs;
        let parent = std::env::temp_dir();
        let root = parent.join(format!(
            "dubflow-native-json-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        assert!(!root.exists());
        fs::create_dir(&root).unwrap();
        let output = NativeOutputPin::read(&root).unwrap();
        let until = Instant::now() + std::time::Duration::from_secs(30);
        let budget = super::super::review_render::storage_budget();
        let kept;
        let raw = "{\"private_checkpoint\":9007199254740993}\n";
        {
            let store = NativeMixStore::new(&output, "native-json", budget).unwrap();
            kept = store.accept_json_record(raw, until).unwrap();
            assert_eq!(store.read_json_record(&kept, until).unwrap(), raw);
            let mut hinted = kept.descriptor();
            hinted["path_hint"] = json!("Z:/untrusted-preview-hint.json");
            assert_eq!(
                store
                    .read_json_record(&store.reopen_file(&hinted, until).unwrap(), until)
                    .unwrap(),
                raw
            );
            let mut foreign = kept.descriptor();
            foreign["locator"]["volume_id"] =
                json!("windows-volume-guid:00000000-0000-0000-0000-000000000000");
            assert!(store.reopen_file(&foreign, until).is_err());
        }
        let before = fs::metadata(kept.path()).unwrap().modified().unwrap();
        {
            let store = NativeMixStore::open_existing(
                &output,
                "native-json",
                super::super::review_render::storage_budget(),
            )
            .unwrap();
            let reopened = store.reopen_file(&kept.descriptor(), until).unwrap();
            assert_eq!(store.read_json_record(&reopened, until).unwrap(), raw);
            assert_eq!(
                fs::metadata(reopened.path()).unwrap().modified().unwrap(),
                before
            );
        }
        assert_eq!(root.parent(), Some(parent.as_path()));
        assert!(
            root.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("dubflow-native-json-")
        );
        fn unseal(root: &Path) {
            for entry in fs::read_dir(root).unwrap() {
                let path = entry.unwrap().path();
                let m = fs::symlink_metadata(&path).unwrap();
                assert!(!m.file_type().is_symlink());
                if m.is_dir() {
                    unseal(&path)
                } else {
                    let mut p = m.permissions();
                    p.set_readonly(false);
                    fs::set_permissions(path, p).unwrap();
                }
            }
        }
        unseal(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cue_locator_dispatch_is_closed_mono_and_keeps_hints_without_io_authority() {
        let root = std::env::temp_dir();
        let volume = "windows-volume-guid:01234567-89ab-cdef-0123-456789abcdef";
        let hash = format!("sha256:{}", "a".repeat(64));
        let relative = std::path::Path::new(".dubflow-review-media-v1")
            .join("b".repeat(64))
            .join(format!("{}.wav", "a".repeat(64)));
        let descriptor = json!({"kind":"review_retained_cue_audio","path_hint":"ignored preview hint",
            "locator":{"schema_version":1,"volume_id":volume,"root_kind":"output","relative_path":relative,
                "content_hash":hash,"size_bytes":"46"},"sample_rate":48000,"channels":1,"frame_count":"1"});
        let admitted = cue_descriptor(&descriptor, &root, volume).unwrap();
        assert_eq!(admitted.audio.path, root.join(&relative));
        for case in 0..6 {
            let mut changed = descriptor.clone();
            match case {
                0 => changed["kind"] = "review_retained_mix_audio".into(),
                1 => changed["channels"] = 2.into(),
                2 => changed["sample_rate"] = 16000.into(),
                3 => changed["frame_count"] = 1.into(),
                4 => changed["locator"]["volume_id"] = "other-volume".into(),
                _ => {
                    changed
                        .as_object_mut()
                        .unwrap()
                        .insert("caller_authorized".into(), true.into());
                }
            }
            assert!(
                cue_descriptor(&changed, &root, volume).is_err(),
                "case {case}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn actual_windows_cue_locator_reopens_single_link_mono_and_refuses_foreign_identity() {
        use sha2::{Digest, Sha256};
        use std::fs;
        use std::sync::atomic::Ordering;
        let root = (0..32)
            .find_map(|_| {
                let path = std::env::temp_dir().join(format!(
                    "dubflow-cue-locator-{}-{}",
                    std::process::id(),
                    super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
                ));
                match fs::create_dir(&path) {
                    Ok(()) => Some(path),
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => None,
                    Err(error) => panic!("exclusive cue fixture: {error}"),
                }
            })
            .expect("new exclusive cue fixture");
        let output = root.join("output");
        let candidate = root.join("candidate");
        fs::create_dir(&output).unwrap();
        fs::create_dir(&candidate).unwrap();
        let mut wav = Vec::new();
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&38u32.to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&48000u32.to_le_bytes());
        wav.extend_from_slice(&96000u32.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&2u32.to_le_bytes());
        wav.extend_from_slice(&[1u8, 0]);
        let source = candidate.join("cue.wav");
        fs::write(&source, &wav).unwrap();
        let pcm = PcmPin {
            sha256: format!("sha256:{:x}", Sha256::digest(&wav)),
            size_bytes: 46,
            sample_rate: 48000,
            channels: 1,
            frame_count: 1,
        };
        let pin = NativeOutputPin::read(&output).unwrap();
        let budget = MixStorageBudget {
            maximum_bytes: 1000,
            minimum_free_bytes: 0,
        };
        let store = NativeMixStore::new(&pin, "cue-physical", budget).unwrap();
        let deadline = Instant::now() + std::time::Duration::from_secs(30);
        let object = store
            .accept_cue(&candidate, &source, &pcm, deadline)
            .unwrap();
        let descriptor = object.descriptor();
        let modified = fs::metadata(object.path()).unwrap().modified().unwrap();
        assert_eq!(descriptor["locator"]["root_kind"], "output");
        assert_eq!(descriptor["locator"]["volume_id"], pin.volume_id());
        drop(store);
        let reader = NativeMixStore::open_existing(&pin, "cue-physical", budget).unwrap();
        let mut hint = descriptor.clone();
        hint["path_hint"] = "ignored nonexistent preview".into();
        assert_eq!(
            reader.reopen_cue(&hint, deadline).unwrap().path(),
            object.path()
        );
        for case in 0..4 {
            let mut foreign = descriptor.clone();
            match case {
                0 => foreign["locator"]["volume_id"] = "foreign-volume".into(),
                1 => foreign["channels"] = 2.into(),
                2 => foreign["locator"]["relative_path"] = "../foreign.wav".into(),
                _ => foreign["frame_count"] = "2".into(),
            }
            assert!(
                reader.reopen_cue(&foreign, deadline).is_err(),
                "case {case}"
            );
        }
        let extra = root.join("extra.wav");
        fs::hard_link(object.path(), &extra).unwrap();
        assert!(reader.reopen_cue(&descriptor, deadline).is_err());
        let mut permissions = fs::metadata(&extra).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&extra, permissions).unwrap();
        fs::remove_file(extra).unwrap();
        let mut permissions = fs::metadata(object.path()).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(object.path(), permissions).unwrap();
        reader.reopen_cue(&descriptor, deadline).unwrap();
        assert_eq!(
            fs::metadata(object.path()).unwrap().modified().unwrap(),
            modified
        );
        assert_eq!(fs::read(&source).unwrap(), wav);
        assert!(reader.reopen_cue(&descriptor, Instant::now()).is_err());
        let mut permissions = fs::metadata(object.path()).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(object.path(), permissions).unwrap();
        let mut corrupted = wav.clone();
        *corrupted.last_mut().unwrap() ^= 1;
        fs::write(object.path(), corrupted).unwrap();
        let mut permissions = fs::metadata(object.path()).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(object.path(), permissions).unwrap();
        assert!(reader.reopen_cue(&descriptor, deadline).is_err()); // same-size header-valid corrupt PCM.
        drop(reader);
        let mut permissions = fs::metadata(object.path()).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(object.path(), permissions).unwrap();
        assert!(
            root.canonicalize()
                .unwrap()
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn retained_pcm_fence_requires_closed_distinct_ordered_roles() {
        let entries = json!([
            {"role":"original_audio","audio":{"locator":"original"}},
            {"role":"dialogue_stem","audio":{"locator":"dialogue"}},
            {"role":"final_mix","audio":{"locator":"final"}}
        ]);
        let admitted = ordered_pcm_entries(&entries).unwrap();
        assert_eq!(admitted[2].0, "final_mix");
        assert_eq!(admitted[2].1["locator"], "final");
        for case in 0..6 {
            let mut bad = entries.clone();
            match case {
                0 => bad.as_array_mut().unwrap().swap(0, 1),
                1 => bad[1]["role"] = json!("original_audio"),
                2 => bad[0]["role"] = json!("foreign"),
                3 => bad[2]
                    .as_object_mut()
                    .unwrap()
                    .remove("audio")
                    .map(|_| ())
                    .unwrap(),
                4 => bad[0]["extra"] = json!(true),
                _ => {
                    bad.as_array_mut().unwrap().pop();
                }
            }
            assert!(ordered_pcm_entries(&bad).is_err(), "case {case}");
        }
    }

    #[test]
    fn persisted_render_descriptor_uses_native_root_and_refuses_locator_substitution() {
        let volume = "windows-volume-guid:01234567-89ab-cdef-0123-456789abcdef";
        let root = std::env::temp_dir().join("native-rebound-output");
        let hash = format!("sha256:{}", "a".repeat(64));
        let relative = format!(".dubflow-review-media-v1/job/{}.mp4", &hash[7..]);
        let value = json!({"path_hint":"missing-candidate/never-read.mp4","format":"mp4",
            "locator":{"schema_version":1,"volume_id":volume,"root_kind":"output",
                "relative_path":relative,"content_hash":hash,"size_bytes":"9007199254740993"}});
        let object = render_file_descriptor(&value, &root, volume).unwrap();
        assert_eq!(object.path(), root.join(&relative));
        assert_eq!(object.locator.size_bytes, 9007199254740993);
        for case in 0..10 {
            let mut bad = value.clone();
            match case {
                0 => bad["locator"]["volume_id"] = json!("foreign-volume"),
                1 => bad["locator"]["root_kind"] = json!("control"),
                2 => bad["locator"]["schema_version"] = json!(2),
                3 => bad["locator"]["relative_path"] = json!("../escape.mp4"),
                4 => bad["locator"]["content_hash"] = json!("sha256:invalid"),
                5 => bad["locator"]["size_bytes"] = json!(100),
                6 => bad["locator"]["size_bytes"] = json!("0100"),
                7 => bad["format"] = json!("exe"),
                8 => bad["locator"]["extra"] = json!(true),
                _ => bad["path_hint"] = json!(true),
            }
            assert!(
                render_file_descriptor(&bad, &root, volume).is_err(),
                "case {case}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn actual_windows_render_retention_pins_canonical_locator_and_reopens_sealed_bytes() {
        use sha2::Digest;
        use std::fs;
        use std::sync::atomic::Ordering;
        use std::time::Duration;
        let output = std::env::temp_dir().join(format!(
            "dubflow-render-volume-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&output).unwrap();
        let candidate = output.join("candidate");
        fs::create_dir(&candidate).unwrap();
        let path = candidate.join("final.mp4");
        let raw = b"physical hash fixture, not decoded MP4";
        fs::write(&path, raw).unwrap();
        let pin = RetainedFilePin {
            sha256: format!("sha256:{:x}", sha2::Sha256::digest(raw)),
            size_bytes: raw.len() as u64,
            format: super::super::review_assets::RetainedFileFormat::Mp4,
        };
        let output_pin = NativeOutputPin::read(&output).unwrap();
        let budget = MixStorageBudget {
            maximum_bytes: 1000,
            minimum_free_bytes: 0,
        };
        let store = NativeMixStore::new(&output_pin, "render-native-locator", budget).unwrap();
        let deadline = Instant::now() + Duration::from_secs(30);
        let object = store
            .accept_file(&candidate, &path, &pin, deadline)
            .unwrap();
        assert_eq!(
            object.descriptor()["locator"]["size_bytes"],
            pin.size_bytes.to_string()
        );
        assert_eq!(object.descriptor()["format"], "mp4");
        assert_eq!(object.locator.root_kind, RootKind::Output);
        fs::remove_file(&path).unwrap();
        for index in 0..5 {
            let mut wrong = object.clone();
            match index {
                0 => {
                    wrong.locator.volume_id =
                        "windows-volume-guid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee".into()
                }
                1 => wrong.locator.root_kind = RootKind::Control,
                2 => wrong.locator.relative_path = "foreign.mp4".into(),
                3 => wrong.locator.content_hash = format!("sha256:{}", "b".repeat(64)),
                _ => wrong.locator.size_bytes += 1,
            }
            assert!(store.verify_file(&wrong, deadline).is_err());
        }
        drop(store);
        let reopened =
            NativeMixStore::open_existing(&output_pin, "render-native-locator", budget).unwrap();
        reopened.verify_file(&object, deadline).unwrap();
        let mut persisted = object.descriptor();
        persisted["path_hint"] = json!("missing-old-drive/candidate.mp4");
        let restored = reopened.reopen_file(&persisted, deadline).unwrap();
        assert_eq!(restored.path(), object.path());
        for case in 0..4 {
            let mut bad = persisted.clone();
            match case {
                0 => bad["locator"]["relative_path"] = json!("foreign.mp4"),
                1 => bad["locator"]["content_hash"] = json!(format!("sha256:{}", "c".repeat(64))),
                2 => bad["locator"]["size_bytes"] = json!((pin.size_bytes + 1).to_string()),
                _ => bad["format"] = json!("srt"),
            }
            assert!(reopened.reopen_file(&bad, deadline).is_err());
        }
        assert_eq!(fs::read(&object.file.path).unwrap(), raw);
        drop(reopened);
        let mut permissions = fs::metadata(&object.file.path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&object.file.path, permissions).unwrap();
        fs::remove_dir_all(output).unwrap();
    }

    fn metadata_fixture() -> (Value, Vec<(&'static str, LocatedMixAudio)>) {
        let fixture = super::super::review_document::decode(
            include_str!("../../../../tests/review/native_retained_mix_document_v1.json"),
            1024 * 1024,
        )
        .unwrap();
        let fixture = fixture[if cfg!(windows) { "windows" } else { "posix" }].clone();
        let objects = ["original_audio", "dialogue_stem", "final_mix"]
            .into_iter()
            .enumerate()
            .map(|(i, role)| {
                let pin = &fixture["context"]["retained_mix_audio"][i]["audio"];
                // A typed metadata fixture for the private pure function only.
                // Never pass these fake descriptors as native physical proof.
                (
                    role,
                    LocatedMixAudio {
                        audio: RetainedMixAudio {
                            path: pin["path_hint"].as_str().unwrap().into(),
                            pin: PcmPin {
                                sha256: pin["locator"]["content_hash"].as_str().unwrap().into(),
                                size_bytes: pin["locator"]["size_bytes"]
                                    .as_str()
                                    .unwrap()
                                    .parse()
                                    .unwrap(),
                                sample_rate: 48000,
                                channels: 2,
                                frame_count: pin["frame_count"].as_str().unwrap().parse().unwrap(),
                            },
                        },
                        locator: ArtifactLocator {
                            schema_version: 1,
                            root_kind: RootKind::Output,
                            volume_id: pin["locator"]["volume_id"].as_str().unwrap().into(),
                            relative_path: pin["locator"]["relative_path"].as_str().unwrap().into(),
                            content_hash: pin["locator"]["content_hash"].as_str().unwrap().into(),
                            size_bytes: pin["locator"]["size_bytes"]
                                .as_str()
                                .unwrap()
                                .parse()
                                .unwrap(),
                        },
                    },
                )
            })
            .collect();
        (fixture, objects)
    }

    #[test]
    fn retained_metadata_matches_owned_oracle_and_changes_only_three_paths() {
        use super::super::review_document::decode;
        let (fixture, objects) = metadata_fixture();
        let source = fixture["context"]["candidate_mix_json"].as_str().unwrap();
        let composition = fixture["context"]["composition_sha256"].as_str().unwrap();
        let raw = retarget_metadata(source, composition, &objects).unwrap();
        let actual = decode(&raw, 64 * 1024 * 1024).unwrap();
        let oracle = decode(
            fixture["prepared"]["mix_document_json"].as_str().unwrap(),
            64 * 1024 * 1024,
        )
        .unwrap();
        assert_eq!(actual, oracle);
        let mut expected = decode(source, 64 * 1024 * 1024).unwrap();
        for (role, object) in &objects {
            expected[*role]["path"] = json!(object.audio.path);
        }
        assert_eq!(actual, expected); // Every semantic field survives unchanged.
        assert_ne!(raw, source);
        assert_eq!(
            raw,
            retarget_metadata(source, composition, &objects).unwrap()
        );
    }

    #[test]
    fn retained_metadata_keeps_actual_measured_confidence_bits_except_three_paths() {
        use super::super::review_document::{canonical, decode};
        let (fixture, objects) = metadata_fixture();
        let mut source = decode(
            fixture["context"]["candidate_mix_json"].as_str().unwrap(),
            1024 * 1024,
        )
        .unwrap();
        let scores = [0.9838564395904541_f64, 0.9168733358383179_f64];
        assert!(source["segments"].as_array().unwrap().len() >= scores.len());
        for (index, score) in scores.iter().enumerate() {
            // Construct the baseline independently of JSON decimal decoding.
            source["segments"][index]["confidence"] = json!(*score);
        }
        let mut raw = String::new();
        canonical(&source, &mut raw).unwrap();
        let result = retarget_metadata(
            &raw,
            fixture["context"]["composition_sha256"].as_str().unwrap(),
            &objects,
        )
        .unwrap();
        let admitted = decode(&result, 1024 * 1024).unwrap();
        for (index, score) in scores.iter().enumerate() {
            assert_eq!(
                admitted["segments"][index]["confidence"]
                    .as_f64()
                    .unwrap()
                    .to_bits(),
                score.to_bits()
            );
        }
        for (role, object) in &objects {
            source[*role]["path"] = json!(object.audio.path);
        }
        assert_eq!(admitted, source); // Exact semantic equality; no epsilon.
    }

    #[test]
    fn retained_metadata_refuses_role_content_pcm_and_composition_substitution() {
        let (fixture, objects) = metadata_fixture();
        let source = fixture["context"]["candidate_mix_json"].as_str().unwrap();
        let composition = fixture["context"]["composition_sha256"].as_str().unwrap();
        assert!(retarget_metadata(source, "foreign", &objects).is_err());
        assert!(retarget_metadata(source, composition, &objects[..2]).is_err());
        for change in 0..9 {
            let mut altered = objects.clone();
            match change {
                0 => altered.reverse(),
                1 => altered[1].0 = "original_audio",
                2 => altered[0].1.audio.pin.sha256 = format!("sha256:{}", "0".repeat(64)),
                3 => altered[0].1.audio.pin.frame_count += 1,
                4 => altered[0].1.audio.pin.channels = 1,
                5 => altered[0].1.audio.pin.sample_rate = 16000,
                6 => altered[0].1.audio.pin.size_bytes = 44,
                7 => altered[0].1.audio.pin.size_bytes = u64::MAX,
                _ => altered[0].1.audio.path = "".into(),
            }
            assert!(
                retarget_metadata(source, composition, &altered).is_err(),
                "case{change}"
            );
        }
        let mut repeated = source.to_owned();
        repeated = repeated.replacen(
            "\"schema_version\":1",
            "\"schema_version\":1,\"schema_version\":1",
            1,
        );
        assert!(retarget_metadata(&repeated, composition, &objects).is_err());
    }

    #[test]
    fn volume_identity_and_quota_space_are_os_values_not_path_hints() {
        let observed = admit_observation(GUID, 3, "NTFS", HARD_LINKS, 7, 10, 20).unwrap();
        assert_eq!(
            observed.volume_id,
            "windows-volume-guid:01234567-89ab-cdef-0123-456789abcdef"
        );
        assert_eq!(observed.available_bytes, 7);
        assert!(
            same_volume(
                "windows-volume-guid:ffffffff-89ab-cdef-0123-456789abcdef",
                &observed
            )
            .is_err()
        );
        for guid in [
            r"D:\",
            "",
            r"\\?\Volume{00000000-0000-0000-0000-000000000000}\",
            r"\\?\Volume{01234567-89AB-CDEF-0123-456789abcdeg}\",
        ] {
            assert!(admit_observation(guid, 3, "NTFS", HARD_LINKS, 7, 10, 20).is_err());
        }
    }

    #[test]
    fn network_removable_readonly_unknown_and_inconsistent_space_refuse() {
        for drive in [0, 1, 2, 4, 5, 6] {
            assert!(admit_observation(GUID, drive, "NTFS", HARD_LINKS, 7, 10, 20).is_err());
        }
        for (filesystem, flags, available, total, free) in [
            ("ReFS", HARD_LINKS, 7, 10, 20),
            ("FAT32", HARD_LINKS, 7, 10, 20),
            ("NTFS", 0, 7, 10, 20),
            ("NTFS", HARD_LINKS | 0x80000, 7, 10, 20),
            ("NTFS", HARD_LINKS, 11, 10, 20),
            ("NTFS", HARD_LINKS, 21, 30, 20),
        ] {
            assert!(admit_observation(GUID, 3, filesystem, flags, available, total, free).is_err());
        }
        assert_eq!(
            admit_observation(GUID, 3, "NTFS", HARD_LINKS, 0, 10, 20)
                .unwrap()
                .available_bytes,
            0
        );
    }

    #[cfg(windows)]
    #[test]
    fn actual_windows_local_root_is_reprobed_without_volume_marker_files() {
        let root = std::env::temp_dir();
        let first = probe(&root).unwrap();
        let second = probe(&root).unwrap();
        same_volume(&first.volume_id, &second).unwrap();
        assert!(first.volume_id.starts_with("windows-volume-guid:"));
        assert!(probe(&root.join("dubflow-definitely-absent-volume-probe-child")).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn native_locator_copy_reopen_reuse_and_substitution_refusals() {
        use sha2::{Digest, Sha256};
        use std::fs;
        use std::sync::atomic::Ordering;
        use std::time::Duration;
        struct Fixture(PathBuf);
        impl Drop for Fixture {
            fn drop(&mut self) {
                // Only this create-new-owned test directory; the retained file
                // is explicitly unsealed below before fixture cleanup.
                assert!(self.0.starts_with(std::env::temp_dir()));
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let root = (0..32)
            .find_map(|_| {
                let sequence = super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed);
                let path = std::env::temp_dir().join(format!(
                    "dubflow-native-retention-{}-{sequence}",
                    std::process::id()
                ));
                match fs::create_dir(&path) {
                    Ok(()) => Some(Fixture(path)),
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => None,
                    Err(e) => panic!("test fixture create: {e}"),
                }
            })
            .expect("exclusive native retention fixture");
        let output = root.0.join("output");
        let candidate = root.0.join("candidate");
        fs::create_dir(&output).unwrap();
        fs::create_dir(&candidate).unwrap();
        let path = candidate.join("final.wav");
        let mut wav = Vec::new();
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&52u32.to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&48000u32.to_le_bytes());
        wav.extend_from_slice(&192000u32.to_le_bytes());
        wav.extend_from_slice(&4u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&[0u8; 16]);
        fs::write(&path, &wav).unwrap();
        let pcm = PcmPin {
            sha256: format!("sha256:{:x}", Sha256::digest(&wav)),
            size_bytes: 60,
            sample_rate: 48000,
            channels: 2,
            frame_count: 4,
        };
        let budget = MixStorageBudget {
            maximum_bytes: 1000,
            minimum_free_bytes: 0,
        };
        let native = NativeOutputPin::read(&output).unwrap();
        let store = NativeMixStore::new(&native, "job-physical-locator", budget).unwrap();
        let deadline = Instant::now() + Duration::from_secs(30);
        let object = store.accept(&candidate, &path, &pcm, deadline).unwrap();
        let before = fs::metadata(&object.audio.path)
            .unwrap()
            .modified()
            .unwrap();
        assert!(
            fs::metadata(&object.audio.path)
                .unwrap()
                .permissions()
                .readonly()
        );
        assert_eq!(object.descriptor()["locator"]["size_bytes"], "60");
        assert_eq!(object.descriptor()["frame_count"], "4");
        assert_eq!(object.locator.root_kind, RootKind::Output);
        assert!(!object.locator.relative_path.is_absolute());
        fs::write(&path, b"changed private producer file").unwrap();
        // Dedup reuses sealed bytes, not a hard link to mutable candidate media.
        let again = store.accept(&candidate, &path, &pcm, deadline).unwrap();
        assert_eq!(again.locator, object.locator);
        assert_eq!(
            fs::metadata(&object.audio.path)
                .unwrap()
                .modified()
                .unwrap(),
            before
        );
        drop(store);
        let reopened =
            NativeMixStore::open_existing(&native, "job-physical-locator", budget).unwrap();
        reopened.verify(&object, deadline).unwrap();
        // Four-frame synthetic metadata exercises physical binding only. It
        // does not qualify a full producer, long media or an active graph.
        let (fixture, _) = metadata_fixture();
        let mut source = super::super::review_document::decode(
            fixture["context"]["candidate_mix_json"].as_str().unwrap(),
            64 * 1024 * 1024,
        )
        .unwrap();
        let roles = ["original_audio", "dialogue_stem", "final_mix"];
        for role in roles {
            source[role]["content_hash"] = json!(pcm.sha256);
            source[role]["frame_count"] = json!(pcm.frame_count);
            source[role]["metrics"]["content_hash"] = json!(pcm.sha256);
            source[role]["metrics"]["frame_count"] = json!(pcm.frame_count);
            source[role]["metrics"]["sample_count"] = json!(pcm.frame_count * 2);
        }
        let source_raw = serde_json::to_string(&source).unwrap();
        let composition = fixture["context"]["composition_sha256"].as_str().unwrap();
        let retained_roles: Vec<_> = roles
            .into_iter()
            .map(|role| (role, object.clone()))
            .collect();
        let retargeted = reopened
            .retarget_admitted_document(&source_raw, composition, &retained_roles, deadline)
            .unwrap();
        // Actual Windows canonical retained paths use the extended spelling,
        // while the original output pin was obtained from a DOS-spelled root.
        // Reproduce the old render prefix refusal without stripping prefixes.
        assert!(
            !object
                .audio
                .path
                .starts_with(output.join(".dubflow-review-media-v1"))
        );
        let entries = json!(
            retained_roles
                .iter()
                .map(|(role, audio)| json!({"role":role,"audio":audio.descriptor()}))
                .collect::<Vec<_>>()
        );
        reopened
            .revalidate_admitted_document(&source_raw, composition, &entries, &retargeted, deadline)
            .unwrap();
        for change in 0..6 {
            let mut foreign = entries.clone();
            match change {
                0 => foreign[0]["audio"]["locator"]["volume_id"] = json!("foreign-volume"),
                1 => foreign[0]["audio"]["locator"]["root_kind"] = json!("control"),
                2 => foreign[0]["audio"]["locator"]["relative_path"] = json!("../foreign.wav"),
                3 => foreign[0]["audio"]["frame_count"] = json!("5"),
                4 => foreign[1]["role"] = json!("original_audio"),
                _ => {
                    foreign[0]["audio"]["locator"]["content_hash"] =
                        json!(format!("sha256:{}", "0".repeat(64)))
                }
            }
            assert!(
                reopened
                    .revalidate_admitted_document(
                        &source_raw,
                        composition,
                        &foreign,
                        &retargeted,
                        deadline,
                    )
                    .is_err(),
                "case {change}"
            );
        }
        let mut changed_metadata =
            super::super::review_document::decode(&retargeted, 64 * 1024 * 1024).unwrap();
        changed_metadata["segments"][0]["confidence"] = json!(0.5);
        assert!(
            reopened
                .revalidate_admitted_document(
                    &source_raw,
                    composition,
                    &entries,
                    &changed_metadata.to_string(),
                    deadline,
                )
                .is_err()
        );
        assert!(
            reopened
                .revalidate_admitted_document(
                    &source_raw,
                    composition,
                    &entries,
                    &retargeted,
                    Instant::now(),
                )
                .is_err()
        );
        for role in roles {
            source[role]["path"] = json!(object.audio.path);
        }
        assert_eq!(
            super::super::review_document::decode(&retargeted, 64 * 1024 * 1024).unwrap(),
            source
        );
        for change in 0..6 {
            let mut foreign = object.clone();
            match change {
                0 => {
                    foreign.locator.volume_id =
                        "windows-volume-guid:ffffffff-89ab-cdef-0123-456789abcdef".into()
                }
                1 => foreign.locator.relative_path = "../elsewhere.wav".into(),
                2 => foreign.locator.root_kind = RootKind::Source,
                3 => foreign.locator.content_hash = format!("sha256:{}", "0".repeat(64)),
                4 => foreign.locator.size_bytes += 1,
                _ => foreign.locator.schema_version += 1,
            }
            assert!(reopened.verify(&foreign, deadline).is_err());
            let mut foreign_roles = retained_roles.clone();
            foreign_roles[0].1 = foreign;
            assert!(
                reopened
                    .retarget_admitted_document(&source_raw, composition, &foreign_roles, deadline,)
                    .is_err()
            );
        }
        assert!(reopened.verify(&object, Instant::now()).is_err());
        let mut hint_only = object.descriptor();
        hint_only["path_hint"] = "missing mutable candidate, never read".into();
        let restored_audio = reopened.reopen_audio(&hint_only, deadline).unwrap();
        assert_eq!(restored_audio.audio, object.audio);
        for field in ["sample_rate", "channels", "frame_count"] {
            let mut substituted = hint_only.clone();
            substituted[field] = if field == "frame_count" {
                json!("0001")
            } else {
                json!(1)
            };
            assert!(reopened.reopen_audio(&substituted, deadline).is_err());
        }
        let foreign_volume = NativeOutputPin {
            root: native.root.clone(),
            volume_id: "windows-volume-guid:ffffffff-89ab-cdef-0123-456789abcdef".into(),
        };
        assert!(
            NativeMixStore::open_existing(&foreign_volume, "job-physical-locator", budget)
                .err()
                .unwrap()
                .to_string()
                .contains("volume changed")
        );
        let mut permissions = fs::metadata(&object.audio.path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&object.audio.path, permissions).unwrap();
        wav[59] = 1;
        fs::write(&object.audio.path, &wav).unwrap();
        let mut sealed = fs::metadata(&object.audio.path).unwrap().permissions();
        sealed.set_readonly(true);
        fs::set_permissions(&object.audio.path, sealed).unwrap();
        assert!(reopened.verify(&object, deadline).is_err());
        assert!(
            reopened
                .revalidate_admitted_document(
                    &source_raw,
                    composition,
                    &entries,
                    &retargeted,
                    deadline,
                )
                .is_err()
        );
        assert!(
            reopened
                .retarget_admitted_document(&source_raw, composition, &retained_roles, deadline,)
                .is_err()
        );
        drop(reopened);
        let mut cleanup = fs::metadata(&object.audio.path).unwrap().permissions();
        cleanup.set_readonly(false);
        fs::set_permissions(&object.audio.path, cleanup).unwrap();
        let impossible_reserve = NativeMixStore::new(
            &native,
            "job-no-space",
            MixStorageBudget {
                maximum_bytes: 1000,
                minimum_free_bytes: u64::MAX,
            },
        )
        .unwrap();
        wav[59] = 0;
        fs::write(&path, &wav).unwrap();
        assert!(
            impossible_reserve
                .accept(&candidate, &path, &pcm, deadline)
                .unwrap_err()
                .to_string()
                .contains("reservation overflow")
        );
        drop(impossible_reserve);
        let moved = root.0.join("moved-output");
        fs::rename(&output, &moved).unwrap();
        assert!(NativeMixStore::open_existing(&native, "job-physical-locator", budget).is_err());
        fs::rename(&moved, &output).unwrap();
    }

    #[cfg(not(windows))]
    #[test]
    fn unqualified_native_provider_does_not_create_a_retained_namespace() {
        let root = std::env::temp_dir();
        assert!(NativeOutputPin::read(&root).is_err());
    }
}
