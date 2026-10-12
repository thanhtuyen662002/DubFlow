//! Native admission for private retained mix media. No graph publication.
//! OS observations are fresh, path hints are not durable identity, and the
//! canonical storage crate owns locator/path validation. Standard export does
//! not depend on this optional local-NTFS Review path.
use super::review_assets::{MixStorageBudget, PcmPin, RetainedMixAudio, RetainedMixStore};
use super::{SupervisorError, SupervisorResult};
use dubflow_artifact_storage::ArtifactLocator;
use dubflow_job_storage::RootKind;
use serde_json::{json, Value};
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

impl LocatedMixAudio {
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

fn same_volume(expected: &str, observed: &Observation) -> SupervisorResult<()> {
    if observed.volume_id != expected {
        return Err(invalid(
            "Review output volume changed; restore or explicitly rebind it",
        ));
    }
    Ok(())
}

impl NativeMixStore {
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
}

#[cfg(test)]
mod tests {
    use super::*;
    const GUID: &str = r"\\?\Volume{01234567-89AB-CDEF-0123-456789abcdef}\";
    const HARD_LINKS: u32 = 0x0040_0000;

    #[test]
    fn volume_identity_and_quota_space_are_os_values_not_path_hints() {
        let observed = admit_observation(GUID, 3, "NTFS", HARD_LINKS, 7, 10, 20).unwrap();
        assert_eq!(
            observed.volume_id,
            "windows-volume-guid:01234567-89ab-cdef-0123-456789abcdef"
        );
        assert_eq!(observed.available_bytes, 7);
        assert!(same_volume(
            "windows-volume-guid:ffffffff-89ab-cdef-0123-456789abcdef",
            &observed
        )
        .is_err());
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
        assert!(fs::metadata(&object.audio.path)
            .unwrap()
            .permissions()
            .readonly());
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
        }
        assert!(reopened.verify(&object, Instant::now()).is_err());
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
        assert!(impossible_reserve
            .accept(&candidate, &path, &pcm, deadline)
            .unwrap_err()
            .to_string()
            .contains("reservation overflow"));
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
