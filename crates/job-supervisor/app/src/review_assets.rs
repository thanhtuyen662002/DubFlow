//! Private native object storage for a future semantic Review graph caller.
//!
//! This API takes supervisor-derived roots and already admitted cue pins, never
//! browser paths. It neither admits producer/model provenance nor writes SQLite.
//! Audio requires hash, size and PCM verification. JSON preserves exact bytes
//! and requires unambiguous syntax; document semantics need prior admission. A
//! later graph transaction must retain these objects and all pending undo assets.
use super::{review_pin::plain, SupervisorError, SupervisorResult, ID_COUNTER};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions, TryLockError};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::Ordering;
use std::time::Instant;

const MAX_CUE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DOCUMENT_BYTES: u64 = 64 * 1024 * 1024;
const BUFFER_BYTES: usize = 32 * 1024;
const MAX_RIFF_CHUNKS: usize = 1024;
// One longform plan may retain4096 video chunks plus independent receipt/index
// records. This finite inventory bound never increases the aggregate byte quota
// or cooperative deadline, and old/undo/orphan/other-job entries still count.
const MAX_MIX_STORE_ENTRIES: usize = 65536;
const MAX_RETAINED_METADATA_BYTES: usize = 64 * 1024;

#[path = "review_chunk_journal.rs"]
mod chunk_journal;

#[path = "review_chunk_capacity.rs"]
mod chunk_capacity;
pub(super) use chunk_capacity::ChunkCapacity;

#[path = "review_export_copy.rs"]
mod export_copy;
pub(super) use export_copy::{verify_export_files, ExportCopyInput, ExportFileSpec};

#[path = "review_editable_storage.rs"]
mod editable_storage;
pub(super) use editable_storage::{verify_editable_files, EditableFileSpec};

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn valid_hash(hash: &str) -> bool {
    hash.len() == 71
        && hash.starts_with("sha256:")
        && hash[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct PcmPin {
    pub(super) sha256: String,
    pub(super) size_bytes: u64,
    pub(super) sample_rate: u32,
    pub(super) channels: u16,
    pub(super) frame_count: u64,
}

impl PcmPin {
    fn validate_cue(&self) -> SupervisorResult<()> {
        self.validate()?;
        if self.sample_rate != 48000 || self.channels != 1 {
            return Err(invalid(
                "retained cue requires admitted 48k mono signed16 PCM",
            ));
        }
        Ok(())
    }

    fn validate(&self) -> SupervisorResult<()> {
        if !valid_hash(&self.sha256)
            || !(44..=MAX_CUE_BYTES).contains(&self.size_bytes)
            || !(8000..=192000).contains(&self.sample_rate)
            || !(1..=2).contains(&self.channels)
            || self.frame_count == 0
            || self
                .frame_count
                .checked_mul(u64::from(self.channels) * 2)
                .filter(|n| *n <= self.size_bytes - 44)
                .is_none()
        {
            return Err(invalid("invalid bounded cue PCM pin"));
        }
        Ok(())
    }

    fn validate_mix(&self) -> SupervisorResult<()> {
        if !valid_hash(&self.sha256)
            || self.sample_rate != 48000
            || self.channels != 2
            || !(1..=48000 * 6 * 3600).contains(&self.frame_count)
            || !(44..=u64::from(u32::MAX) + 8).contains(&self.size_bytes)
            || self
                .frame_count
                .checked_mul(4)
                .filter(|n| *n <= self.size_bytes - 44)
                .is_none()
        {
            return Err(invalid("invalid private six-hour stereo mix pin"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct AcceptedCueAudio {
    pub(super) path: PathBuf,
    pub(super) pin: PcmPin,
}

/// A distinct output-retained cue; it cannot relax the full-mix stereo contract.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct RetainedCueAudio {
    pub(super) path: PathBuf,
    pub(super) pin: PcmPin,
}

/// Exact-byte storage identity, not a schema/provenance admission token.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct AcceptedJsonDocument {
    pub(super) path: PathBuf,
    pub(super) sha256: String,
    pub(super) size_bytes: u64,
}

pub(super) struct AcceptedAudioStore {
    root: PathBuf,
}

fn directory(path: &Path) -> SupervisorResult<()> {
    plain(
        path.parent()
            .ok_or_else(|| invalid("missing accepted parent"))?,
    )?;
    match fs::create_dir(path) {
        Ok(()) => (),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(e) => return Err(e.into()),
    }
    plain(path)?;
    if !fs::symlink_metadata(path)?.file_type().is_dir() {
        return Err(invalid("accepted root is not a plain directory"));
    }
    Ok(())
}

pub(super) fn regular(path: &Path) -> SupervisorResult<File> {
    plain(path)?;
    if path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("cue path cannot contain parent traversal"));
    }
    if !fs::symlink_metadata(path)?.file_type().is_file() {
        return Err(invalid("cue asset is not a regular file"));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // O_NOFOLLOW | O_NONBLOCK: a raced FIFO must not block the supervisor
        // before the open handle's regular-file check. Regular files ignore it.
        options.custom_flags(0x20000 | 0x800);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT.
    }
    let file = options.open(path)?;
    plain(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(invalid("cue asset is not a regular file"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(invalid("cue file cannot be a reparse point"));
        }
    }
    Ok(file)
}

/// Fresh open-handle identity and exactly one link for retained cue media.
/// Model/runtime inventories have separate explicit deduplication rules.
fn single_cue_file(file: &File) -> SupervisorResult<(u64, u64)> {
    native_identity(file, false)
}

fn native_identity(file: &File, directory: bool) -> SupervisorResult<(u64, u64)> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = file.metadata()?;
        if !directory && metadata.nlink() != 1 {
            return Err(invalid("cue media cannot have multiple hard links"));
        }
        Ok((metadata.dev(), metadata.ino()))
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        // Win32 BY_HANDLE_FILE_INFORMATION, DWORD/FILETIME layout:
        // https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information
        #[repr(C)]
        struct Information {
            attributes: u32,
            created: [u32; 2],
            accessed: [u32; 2],
            written: [u32; 2],
            volume: u32,
            size_high: u32,
            size_low: u32,
            links: u32,
            index_high: u32,
            index_low: u32,
        }
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn GetFileInformationByHandle(
                handle: *mut std::ffi::c_void,
                information: *mut Information,
            ) -> i32;
        }
        let mut information: Information = unsafe { std::mem::zeroed() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut information) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        if information.attributes & 0x400 != 0
            || (information.attributes & 0x10 != 0) != directory
            || (!directory && information.links != 1)
        {
            return Err(invalid(
                "cue media must be a single-link regular no-reparse file",
            ));
        }
        Ok((
            u64::from(information.volume),
            u64::from(information.index_high) << 32 | u64::from(information.index_low),
        ))
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = file;
        Err(invalid(
            "retained cue identity requires a qualified native filesystem",
        ))
    }
}

fn stream_pin(source: &mut File, target: Option<&mut File>, pin: &PcmPin) -> SupervisorResult<()> {
    stream_pin_before(source, target, pin, None)
}

/// A caller supplies an exact archived original path and its admitted root.
/// This never promotes a candidate/hint into canonical retained storage.
pub(super) fn verify_original_cue_audio(
    root: &Path,
    path: &Path,
    pin: &PcmPin,
    deadline: Instant,
) -> SupervisorResult<()> {
    pin.validate()?;
    plain(root)?;
    if !root.is_dir()
        || root.components().any(|c| matches!(c, Component::ParentDir))
        || !path.is_absolute()
        || !path.starts_with(root)
    {
        return Err(invalid("original cue is outside its admitted source root"));
    }
    let mut file = regular(path)?;
    let before = file.metadata()?;
    if before.len() != pin.size_bytes {
        return Err(invalid("original cue size changed"));
    }
    stream_pin_before(&mut file, None, pin, Some(deadline))?;
    pcm(&mut file, pin)?;
    plain(root)?;
    plain(path)?;
    let after = file.metadata()?;
    if before.len() != after.len()
        || before.modified()? != after.modified()?
        || fs::metadata(path)?.modified()? != before.modified()?
    {
        return Err(invalid(
            "original cue changed during effective-state admission",
        ));
    }
    time_left(Some(deadline))
}

fn time_left(deadline: Option<Instant>) -> SupervisorResult<()> {
    if deadline.is_some_and(|value| Instant::now() >= value) {
        return Err(invalid("mix storage exceeded its shared deadline"));
    }
    Ok(())
}

/// Read an already admitted current cue into memory. The caller must derive
/// root/path/pin from native current composition, never a preview path hint.
pub(super) enum CueAudioKind {
    ArchivedOriginal,
    Retained,
}

pub(super) fn read_current_cue_audio(
    root: &Path,
    path: &Path,
    pin: &PcmPin,
    kind: CueAudioKind,
    deadline: Instant,
) -> SupervisorResult<Vec<u8>> {
    time_left(Some(deadline))?;
    pin.validate()?;
    plain(root)?;
    if !root.is_absolute()
        || !root.is_dir()
        || !path.is_absolute()
        || !path.starts_with(root)
        || root.components().any(|c| matches!(c, Component::ParentDir))
    {
        return Err(invalid("current cue escaped its admitted waveform root"));
    }
    let mut file = regular(path)?;
    let identity = single_cue_file(&file)?;
    let before = file.metadata()?;
    let retained = matches!(kind, CueAudioKind::Retained);
    if before.len() != pin.size_bytes || retained && !before.permissions().readonly() {
        return Err(invalid(
            "current cue bytes are not the sealed admitted size",
        ));
    }
    let mut raw = Vec::with_capacity(pin.size_bytes as usize);
    let mut digest = Sha256::new();
    let mut block = [0u8; BUFFER_BYTES];
    loop {
        time_left(Some(deadline))?;
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        if raw.len() as u64 + n as u64 > pin.size_bytes {
            return Err(invalid("current cue grew"));
        }
        digest.update(&block[..n]);
        raw.extend_from_slice(&block[..n]);
    }
    if raw.len() as u64 != pin.size_bytes || format!("sha256:{:x}", digest.finalize()) != pin.sha256
    {
        return Err(invalid("current cue full hash changed"));
    }
    pcm(&mut file, pin)?;
    plain(root)?;
    plain(path)?;
    let fresh = regular(path)?;
    if single_cue_file(&fresh)? != identity
        || single_cue_file(&file)? != identity
        || fresh.metadata()?.len() != before.len()
        || fresh.metadata()?.modified()? != before.modified()?
        || file.metadata()?.modified()? != before.modified()?
        || fresh.metadata()?.permissions().readonly() != before.permissions().readonly()
        || retained && !fresh.metadata()?.permissions().readonly()
    {
        return Err(invalid("current cue changed its readonly file identity"));
    }
    time_left(Some(deadline))?;
    Ok(raw)
}

#[cfg(test)]
mod current_audio_read_tests {
    use super::*;
    use std::time::Duration;

    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Only this fresh flat fixture directory is owned. No parent or
            // historical QA root is removed and no recursive deletion is used.
            if let Ok(entries) = fs::read_dir(&self.0) {
                for entry in entries.flatten() {
                    if entry.file_type().is_ok_and(|v| v.is_file()) {
                        if let Ok(meta) = entry.metadata() {
                            let mut p = meta.permissions();
                            #[cfg(windows)]
                            p.set_readonly(false);
                            #[cfg(unix)]
                            {
                                use std::os::unix::fs::PermissionsExt;
                                p.set_mode(0o600);
                            }
                            let _ = fs::set_permissions(entry.path(), p);
                            let _ = fs::remove_file(entry.path());
                        }
                    }
                }
            }
            let _ = fs::remove_dir(&self.0);
        }
    }
    fn fixture() -> (Fixture, PathBuf, Vec<u8>, PcmPin) {
        let root = std::env::temp_dir().join(format!(
            "dubflow-current-cue-{}-{}",
            std::process::id(),
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let path = root.join("current.wav");
        let mut raw = Vec::new();
        raw.extend(b"RIFF");
        raw.extend(100u32.to_le_bytes());
        raw.extend(b"WAVEfmt ");
        raw.extend(16u32.to_le_bytes());
        raw.extend(1u16.to_le_bytes());
        raw.extend(1u16.to_le_bytes());
        raw.extend(48000u32.to_le_bytes());
        raw.extend(96000u32.to_le_bytes());
        raw.extend(2u16.to_le_bytes());
        raw.extend(16u16.to_le_bytes());
        raw.extend(b"data");
        raw.extend(64u32.to_le_bytes());
        for i in 0..32i16 {
            raw.extend(i.to_le_bytes());
        }
        fs::write(&path, &raw).unwrap();
        let mut p = fs::metadata(&path).unwrap().permissions();
        p.set_readonly(true);
        fs::set_permissions(&path, p).unwrap();
        let pin = PcmPin {
            sha256: format!("sha256:{:x}", Sha256::digest(&raw)),
            size_bytes: raw.len() as u64,
            sample_rate: 48000,
            channels: 1,
            frame_count: 32,
        };
        (Fixture(root), path, raw, pin)
    }
    fn writable(path: &Path) {
        let mut p = fs::metadata(path).unwrap().permissions();
        #[cfg(windows)]
        p.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            p.set_mode(0o600);
        }
        fs::set_permissions(path, p).unwrap();
    }
    #[test]
    fn current_cue_capture_is_full_hash_pcm_readonly_and_mtime_preserving() {
        let (root, path, raw, pin) = fixture();
        let mtime = fs::metadata(&path).unwrap().modified().unwrap();
        assert_eq!(
            read_current_cue_audio(
                &root.0,
                &path,
                &pin,
                CueAudioKind::Retained,
                Instant::now() + Duration::from_secs(3)
            )
            .unwrap(),
            raw
        );
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), mtime);
        assert!(read_current_cue_audio(
            &root.0,
            &path,
            &pin,
            CueAudioKind::Retained,
            Instant::now()
        )
        .is_err());
        writable(&path);
        assert!(read_current_cue_audio(
            &root.0,
            &path,
            &pin,
            CueAudioKind::Retained,
            Instant::now() + Duration::from_secs(3)
        )
        .is_err());
        assert_eq!(
            read_current_cue_audio(
                &root.0,
                &path,
                &pin,
                CueAudioKind::ArchivedOriginal,
                Instant::now() + Duration::from_secs(3)
            )
            .unwrap(),
            raw
        );
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), mtime);
        assert_eq!(fs::read(&path).unwrap(), raw);
    }
    #[cfg(any(unix, windows))]
    #[test]
    fn current_cue_capture_refuses_hardlink_equal_size_corruption_and_header_changes_without_repair(
    ) {
        let (root, path, raw, pin) = fixture();
        let alias = root.0.join("alias.wav");
        fs::hard_link(&path, &alias).unwrap();
        assert!(read_current_cue_audio(
            &root.0,
            &path,
            &pin,
            CueAudioKind::Retained,
            Instant::now() + Duration::from_secs(3)
        )
        .is_err());
        writable(&alias);
        fs::remove_file(&alias).unwrap();
        let mut corrupt = raw.clone();
        corrupt[45] ^= 1;
        fs::write(&path, &corrupt).unwrap();
        let mut p = fs::metadata(&path).unwrap().permissions();
        p.set_readonly(true);
        fs::set_permissions(&path, p).unwrap();
        assert!(read_current_cue_audio(
            &root.0,
            &path,
            &pin,
            CueAudioKind::Retained,
            Instant::now() + Duration::from_secs(3)
        )
        .is_err());
        assert_eq!(fs::read(&path).unwrap(), corrupt);
        writable(&path);
        let mut header = raw.clone();
        header[34..36].copy_from_slice(&32u16.to_le_bytes());
        fs::write(&path, &header).unwrap();
        let mut p = fs::metadata(&path).unwrap().permissions();
        p.set_readonly(true);
        fs::set_permissions(&path, p).unwrap();
        let mut changed = pin;
        changed.sha256 = format!("sha256:{:x}", Sha256::digest(&header));
        assert!(read_current_cue_audio(
            &root.0,
            &path,
            &changed,
            CueAudioKind::Retained,
            Instant::now() + Duration::from_secs(3)
        )
        .is_err());
        assert_eq!(fs::read(&path).unwrap(), header);
    }
}

fn stream_pin_before(
    source: &mut File,
    mut target: Option<&mut File>,
    pin: &PcmPin,
    deadline: Option<Instant>,
) -> SupervisorResult<()> {
    source.seek(SeekFrom::Start(0))?;
    let mut digest = Sha256::new();
    let mut bytes = 0u64;
    let mut buffer = [0u8; BUFFER_BYTES];
    loop {
        time_left(deadline)?;
        let count = source.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        bytes += count as u64;
        if bytes > pin.size_bytes {
            return Err(invalid("cue asset grew beyond its admitted size"));
        }
        digest.update(&buffer[..count]);
        if let Some(output) = target.as_deref_mut() {
            output.write_all(&buffer[..count])?;
        }
    }
    if bytes != pin.size_bytes || format!("sha256:{:x}", digest.finalize()) != pin.sha256 {
        return Err(invalid("cue asset size or checksum changed"));
    }
    Ok(())
}

fn u16le(bytes: &[u8]) -> u16 {
    u16::from_le_bytes(bytes.try_into().unwrap())
}

fn u32le(bytes: &[u8]) -> u32 {
    u32::from_le_bytes(bytes.try_into().unwrap())
}

fn pcm(file: &mut File, pin: &PcmPin) -> SupervisorResult<()> {
    file.seek(SeekFrom::Start(0))?;
    let mut header = [0u8; 12];
    file.read_exact(&mut header)?;
    if &header[..4] != b"RIFF"
        || &header[8..] != b"WAVE"
        || u64::from(u32le(&header[4..8])) + 8 != pin.size_bytes
    {
        return Err(invalid("cue asset is not an exact RIFF/WAVE file"));
    }
    let mut offset = 12u64;
    let mut format_seen = false;
    let mut data_seen = false;
    let mut chunks = 0;
    while offset < pin.size_bytes {
        chunks += 1;
        if chunks > MAX_RIFF_CHUNKS || pin.size_bytes - offset < 8 {
            return Err(invalid("malformed or excessive WAV chunks"));
        }
        let mut chunk = [0u8; 8];
        file.read_exact(&mut chunk)?;
        let length = u64::from(u32le(&chunk[4..]));
        let end = offset + 8 + length + (length % 2);
        if end > pin.size_bytes {
            return Err(invalid("WAV chunk exceeds the admitted file"));
        }
        match &chunk[..4] {
            b"fmt " => {
                if format_seen || data_seen || length != 16 {
                    return Err(invalid("unsupported or duplicated WAV format"));
                }
                let mut format = [0u8; 16];
                file.read_exact(&mut format)?;
                let align = pin.channels * 2;
                if u16le(&format[..2]) != 1
                    || u16le(&format[2..4]) != pin.channels
                    || u32le(&format[4..8]) != pin.sample_rate
                    || u32le(&format[8..12]) != pin.sample_rate * u32::from(align)
                    || u16le(&format[12..14]) != align
                    || u16le(&format[14..]) != 16
                {
                    return Err(invalid("WAV PCM format differs from its admitted cue"));
                }
                format_seen = true;
            }
            b"data" => {
                if !format_seen
                    || data_seen
                    || length != pin.frame_count * u64::from(pin.channels) * 2
                {
                    return Err(invalid("WAV PCM frame count differs from its admitted cue"));
                }
                data_seen = true;
            }
            _ => (), // Bounded ancillary RIFF chunks are hash-covered.
        }
        file.seek(SeekFrom::Start(end))?;
        offset = end;
    }
    if !format_seen || !data_seen {
        return Err(invalid("WAV is missing PCM format or data"));
    }
    Ok(())
}

/// Stream-verify a private full-length stereo candidate. This grants no retained
/// object identity: publication/undo must first copy into a quota-managed store.
pub(super) fn verify_private_mix_audio(
    root: &Path,
    path: &Path,
    pin: &PcmPin,
    deadline: std::time::Instant,
) -> SupervisorResult<()> {
    pin.validate_mix()?;
    if !path.is_absolute() || !root.is_absolute() || !path.starts_with(root) {
        return Err(invalid("mix waveform is outside its admitted root"));
    }
    let mut file = regular(path)?;
    let before = file.metadata()?;
    if before.len() != pin.size_bytes {
        return Err(invalid("private mix size changed"));
    }
    stream_pin_before(&mut file, None, pin, Some(deadline))?;
    pcm(&mut file, pin)?;
    plain(path)?;
    let after = file.metadata()?;
    if after.len() != before.len()
        || after.modified()? != before.modified()?
        || fs::metadata(path)?.modified()? != before.modified()?
    {
        return Err(invalid("private mix changed during verification"));
    }
    Ok(())
}

fn json_syntax(raw: &str) -> SupervisorResult<()> {
    if !super::review_document::decode(raw, MAX_DOCUMENT_BYTES as usize)?.is_object() {
        return Err(invalid("accepted JSON document requires an object root"));
    }
    Ok(())
}

fn read_json_file(file: &mut File, hash: &str, size: u64) -> SupervisorResult<String> {
    if !valid_hash(hash) || !(1..=MAX_DOCUMENT_BYTES).contains(&size) {
        return Err(invalid("invalid bounded JSON document pin"));
    }
    if file.metadata()?.len() != size {
        return Err(invalid("accepted JSON document size changed"));
    }
    file.seek(SeekFrom::Start(0))?;
    let mut bytes = Vec::new();
    // A concurrently grown regular file cannot cause an unbounded read.
    file.take(size + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size || format!("sha256:{:x}", Sha256::digest(&bytes)) != hash {
        return Err(invalid("accepted JSON document checksum or size changed"));
    }
    let raw = String::from_utf8(bytes).map_err(|_| invalid("accepted JSON is not UTF-8"))?;
    json_syntax(&raw)?;
    Ok(raw)
}

struct PrivateCopy(PathBuf);

impl Drop for PrivateCopy {
    fn drop(&mut self) {
        // Only this create_new-owned file is removable. Accepted objects and
        // original/candidate resources are never deleted by an error path.
        let _ = fs::remove_file(&self.0);
    }
}

/// Internal physical storage only. A later native caller must supply fresh
/// space observations and ADR0005 volume/locator admission before graph use.
/// Neither a path nor this object is a durable graph/provenance identity.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct RetainedMixAudio {
    pub(super) path: PathBuf,
    pub(super) pin: PcmPin,
}

/// Only an already admitted render role can choose this internal format.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum RetainedFileFormat {
    Mp4,
    Srt,
    Ass,
    Json,
}
impl RetainedFileFormat {
    pub(super) fn extension(self) -> &'static str {
        match self {
            Self::Mp4 => "mp4",
            Self::Srt => "srt",
            Self::Ass => "ass",
            Self::Json => "json",
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct RetainedFilePin {
    pub(super) sha256: String,
    pub(super) size_bytes: u64,
    pub(super) format: RetainedFileFormat,
}
impl RetainedFilePin {
    fn validate(&self) -> SupervisorResult<()> {
        let maximum = if self.format == RetainedFileFormat::Mp4 {
            128 * 1024 * 1024 * 1024
        } else {
            MAX_DOCUMENT_BYTES
        };
        if !valid_hash(&self.sha256) || !(1..=maximum).contains(&self.size_bytes) {
            return Err(invalid("retained render file pin exceeds its role bound"));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct RetainedRenderFile {
    pub(super) path: PathBuf,
    pub(super) pin: RetainedFilePin,
}

fn stream_render_file(
    source: &mut File,
    mut target: Option<&mut File>,
    pin: &RetainedFilePin,
    deadline: Instant,
    mut space_guard: Option<&mut dyn FnMut(u64) -> SupervisorResult<()>>,
) -> SupervisorResult<()> {
    pin.validate()?;
    source.seek(SeekFrom::Start(0))?;
    let mut sha = Sha256::new();
    let mut count = 0u64;
    let mut buffer = [0u8; BUFFER_BYTES];
    let mut next_space_check = 0u64;
    loop {
        time_left(Some(deadline))?;
        let n = source.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        count = count
            .checked_add(n as u64)
            .ok_or_else(|| invalid("retained render byte count overflow"))?;
        if count > pin.size_bytes {
            return Err(invalid("render candidate grew beyond its admitted pin"));
        }
        sha.update(&buffer[..n]);
        if let Some(target) = target.as_deref_mut() {
            let copied_before_write = count - n as u64;
            if copied_before_write >= next_space_check {
                if let Some(guard) = space_guard.as_deref_mut() {
                    guard(copied_before_write)?;
                }
                next_space_check = copied_before_write.saturating_add(4 * 1024 * 1024);
            }
            target.write_all(&buffer[..n])?;
        }
    }
    if count != pin.size_bytes || format!("sha256:{:x}", sha.finalize()) != pin.sha256 {
        return Err(invalid(
            "retained render bytes differ from their admitted pin",
        ));
    }
    Ok(())
}

#[derive(Clone, Copy)]
pub(super) struct MixStorageBudget {
    /// Aggregate accepted AND orphan bytes for every job in this output root.
    pub(super) maximum_bytes: u64,
    pub(super) minimum_free_bytes: u64,
}

// SCRATCH_CORE_BEGIN: std-only filesystem boundary, also exercised by the owned Windows probe.
const SCRATCH_HEADER: u64 = 4096;
// TEMP contains one sequential native waveform (Mimic3: 8Mi frames *4;
// VieNeu: at most three 48k*32*4 buffers). Full B2/mix/render PCM remains in
// candidate/chunk storage and is NEVER compared with this TEMP-only allowance.
pub(super) const SCRATCH_ALLOWANCE: u64 = 64 * 1024 * 1024;
pub(super) const SCRATCH_CREDITS: &str = "native-scratch-credits-v1";

fn scratch_directory(path: &Path) -> SupervisorResult<(File, (u64, u64))> {
    plain(path)?;
    if !fs::symlink_metadata(path)?.is_dir() {
        return Err(invalid("scratch directory is not plain"));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Hold the directory without delete sharing for the whole invocation.
        options.custom_flags(0x02200000).share_mode(3);
    }
    let file = options.open(path)?;
    if !file.metadata()?.is_dir() {
        return Err(invalid("scratch directory handle changed type"));
    }
    let identity = native_identity(&file, true)?;
    plain(path)?;
    Ok((file, identity))
}

fn scratch_inventory(
    root: &Path,
    deadline: Instant,
    count: &mut usize,
    limit: usize,
) -> SupervisorResult<u64> {
    match fs::symlink_metadata(root) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(e) => return Err(e.into()),
        Ok(_) => (),
    }
    fn visit(
        path: &Path,
        depth: usize,
        deadline: Instant,
        count: &mut usize,
        limit: usize,
    ) -> SupervisorResult<u64> {
        time_left(Some(deadline))?;
        *count = count
            .checked_add(1)
            .ok_or_else(|| invalid("scratch entry overflow"))?;
        if *count > limit || depth > 32 {
            return Err(invalid("scratch inventory exceeds shared bound"));
        }
        plain(path)?;
        if !fs::symlink_metadata(path)?.is_dir() {
            let file = regular(path)?;
            single_cue_file(&file)?;
            return Ok(file.metadata()?.len());
        }
        // One directory iterator per depth, never a pending Vec of all children.
        let mut total = 0u64;
        let mut entries = fs::read_dir(path)?;
        loop {
            time_left(Some(deadline))?;
            let Some(entry) = entries.next() else {
                break;
            };
            if *count >= limit {
                return Err(invalid("scratch enumeration exceeds shared bound"));
            }
            total = total
                .checked_add(visit(&entry?.path(), depth + 1, deadline, count, limit)?)
                .ok_or_else(|| invalid("scratch byte inventory overflow"))?;
        }
        Ok(total)
    }
    visit(root, 0, deadline, count, limit)
}

fn scratch_actual(
    output: &Path,
    deadline: Instant,
    count: &mut usize,
    limit: usize,
) -> SupervisorResult<u64> {
    // Chunk workspace is covered by its existing .chunk-capacity-v1 extent.
    // Do not rescan/double-charge the job candidate tree or change its recovery.
    scratch_inventory(
        &output.join(".dubflow-review-candidates/temp"),
        deadline,
        count,
        limit,
    )?
    .checked_add(scratch_inventory(
        &output.join(".dfr"),
        deadline,
        count,
        limit,
    )?)
    .ok_or_else(|| invalid("scratch inventory overflow"))
}

fn scratch_preflight(
    used: u64,
    outstanding: u64,
    maximum: u64,
    available: u64,
    budget: MixStorageBudget,
) -> SupervisorResult<()> {
    let planned = maximum
        .checked_add(2 * SCRATCH_HEADER)
        .ok_or_else(|| invalid("scratch reservation overflow"))?;
    if used
        .checked_add(planned)
        .is_none_or(|n| n > budget.maximum_bytes)
        || planned
            .checked_add(outstanding)
            .and_then(|n| n.checked_add(budget.minimum_free_bytes))
            .is_none_or(|n| available < n)
    {
        return Err(invalid(
            "scratch lacks shared quota or fresh caller reserve",
        ));
    }
    Ok(())
}

fn claim_scratch(
    output: &Path,
    scratch: &Path,
    owner: &str,
    create: bool,
) -> SupervisorResult<Vec<File>> {
    if owner.len() != 64 || !owner.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(invalid("scratch owner must be native-derived"));
    }
    let (output_file, output_id) = scratch_directory(output)?;
    let legacy = output.join(".dubflow-review-candidates");
    let compact = output.join(".dfr");
    let compact_selected = scratch == compact.join("temp");
    if !compact_selected && scratch != legacy.join("temp") {
        return Err(invalid("scratch escaped its exact output namespace"));
    }
    let root = if compact_selected { compact } else { legacy };
    let created = if !create {
        false
    } else {
        match fs::create_dir(&root) {
            Ok(()) => true,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => false,
            Err(e) => return Err(e.into()),
        }
    };
    let (root_file, root_id) = scratch_directory(&root)?;
    // Never turn an ambient compact directory (even empty) into owned storage.
    if compact_selected && !created {
        drop(regular(&root.join(".owner-v1"))?);
    }
    if created || (create && !compact_selected) {
        directory(scratch)?;
    }
    let (scratch_file, scratch_id) = scratch_directory(scratch)?;
    if output_id.0 != root_id.0 || output_id.0 != scratch_id.0 {
        return Err(invalid("scratch directory escaped output device"));
    }
    if compact_selected {
        let expected = format!(
            "dubflow-owned-scratch-v1\n{owner}\n{output_id:?}\n{root_id:?}\n{scratch_id:?}\n"
        )
        .into_bytes();
        let marker = root.join(".owner-v1");
        if created {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&marker)?;
            file.write_all(&expected)?;
            file.sync_all()?;
            let mut mode = file.metadata()?.permissions();
            mode.set_readonly(true);
            file.set_permissions(mode)?;
        }
        let mut file = regular(&marker)?;
        single_cue_file(&file)?;
        if file.metadata()?.len() != expected.len() as u64
            || !file.metadata()?.permissions().readonly()
        {
            return Err(invalid("scratch ownership record changed"));
        }
        let mut actual = Vec::new();
        Read::by_ref(&mut file)
            .take(SCRATCH_HEADER + 1)
            .read_to_end(&mut actual)?;
        if actual != expected {
            return Err(invalid("foreign scratch output/directory ownership"));
        }
    }
    Ok(vec![output_file, root_file, scratch_file])
}

/// A sealed bounded header plus a logical extent is visible to legacy quota
/// readers. A separate zero-byte locked file owns it while headers stay readable.
/// Drop closes handles but NEVER refunds uncertain or interrupted writers.
pub(super) struct ScratchCredit {
    file: File,
    path: PathBuf,
    lock: File,
    lock_path: PathBuf,
    identity: (u64, u64),
    lock_identity: (u64, u64),
    header: Vec<u8>,
    maximum: u64,
    private_before: u64,
    directories: Vec<File>,
    directory_paths: Vec<PathBuf>,
}

fn scratch_credit_header(
    owner: &str,
    maximum: u64,
    before: u64,
    compact: bool,
    ids: &[(u64, u64); 5],
) -> Vec<u8> {
    format!("dubflow-scratch-credit-v1\n{owner}\n{maximum}\n{before}\n{}\n{:?}\n{:?}\n{:?}\n{:?}\n{:?}\n", u8::from(compact), ids[0], ids[1], ids[2], ids[3], ids[4]).into_bytes()
}

fn read_scratch_credit(path: &Path, output: &Path) -> SupervisorResult<(u64, u64)> {
    let mut file = regular(path)?;
    let identity = single_cue_file(&file)?;
    if file.metadata()?.len() < SCRATCH_HEADER || !file.metadata()?.permissions().readonly() {
        return Err(invalid(
            "scratch credit is unsealed or oversized; explicit recovery required",
        ));
    }
    let mut block = vec![0; SCRATCH_HEADER as usize];
    file.read_exact(&mut block)?;
    let end = block
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| invalid("scratch credit lacks bounded header terminator"))?;
    if block[end..].iter().any(|b| *b != 0) {
        return Err(invalid("scratch credit header padding changed"));
    }
    let bytes = block[..end].to_vec();
    let text = std::str::from_utf8(&bytes).map_err(|_| invalid("scratch credit is not UTF8"))?;
    let lines: Vec<_> = text.lines().collect();
    if lines.len() != 10 || lines[0] != "dubflow-scratch-credit-v1" {
        return Err(invalid("scratch credit schema changed"));
    }
    let maximum: u64 = lines[2]
        .parse()
        .map_err(|_| invalid("invalid scratch maximum"))?;
    let before: u64 = lines[3]
        .parse()
        .map_err(|_| invalid("invalid scratch baseline"))?;
    if file.metadata()?.len()
        != before
            .checked_add(maximum)
            .and_then(|n| n.checked_add(SCRATCH_HEADER))
            .ok_or_else(|| invalid("scratch extent overflow"))?
    {
        return Err(invalid(
            "scratch reservation extent changed; explicit recovery required",
        ));
    }
    let compact = match lines[4] {
        "0" => false,
        "1" => true,
        _ => return Err(invalid("invalid scratch layout")),
    };
    if maximum > SCRATCH_ALLOWANCE {
        return Err(invalid("unknown scratch producer bound"));
    }
    let scratch = output.join(if compact {
        ".dfr/temp"
    } else {
        ".dubflow-review-candidates/temp"
    });
    let directories = claim_scratch(output, &scratch, lines[1], false)?;
    let lock_path = path.with_extension("lock");
    let lock = regular(&lock_path)?;
    if lock.metadata()?.len() != 0 {
        return Err(invalid("scratch credit lock changed"));
    }
    let ids = [
        native_identity(&directories[0], true)?,
        native_identity(&directories[1], true)?,
        native_identity(&directories[2], true)?,
        identity,
        single_cue_file(&lock)?,
    ];
    if scratch_credit_header(lines[1], maximum, before, compact, &ids) != bytes {
        return Err(invalid(
            "scratch credit copied or output/directory/file identity changed",
        ));
    }
    Ok((maximum, before))
}

struct ScratchUsage {
    actual: u64,
    charge: u64,
    outstanding: u64,
    floor: u64,
    covered_extents: u64,
}

fn scratch_usage(
    output: &Path,
    credits: &Path,
    deadline: Instant,
    count: &mut usize,
    limit: usize,
) -> SupervisorResult<ScratchUsage> {
    let actual = scratch_actual(output, deadline, count, limit)?;
    let mut reserved = 0u64;
    let mut baseline = 0u64;
    let mut covered_extents = 0u64;
    match fs::symlink_metadata(credits) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
        Err(e) => return Err(e.into()),
        Ok(_) => {
            let _held = scratch_directory(credits)?;
            let mut entries = fs::read_dir(credits)?;
            loop {
                time_left(Some(deadline))?;
                let Some(entry) = entries.next() else {
                    break;
                };
                *count = count
                    .checked_add(1)
                    .ok_or_else(|| invalid("scratch entry overflow"))?;
                if *count > limit {
                    return Err(invalid("scratch credit inventory exceeds shared bound"));
                }
                let path = entry?.path();
                let name = path
                    .file_name()
                    .and_then(|s| s.to_str())
                    .ok_or_else(|| invalid("nonUTF8 scratch credit name"))?;
                if !name.starts_with(".scratch-credit-v1-") {
                    return Err(invalid(
                        "foreign scratch credit namespace; explicit recovery required",
                    ));
                }
                match path.extension().and_then(|s| s.to_str()) {
                    Some("bin") => {
                        let (maximum, before) = read_scratch_credit(&path, output)?;
                        reserved = reserved
                            .checked_add(maximum)
                            .ok_or_else(|| invalid("scratch reservation overflow"))?;
                        baseline = baseline.max(before);
                        covered_extents = covered_extents
                            .checked_add(maximum)
                            .and_then(|n| n.checked_add(before))
                            .ok_or_else(|| invalid("scratch extent inventory overflow"))?;
                    }
                    Some("lock") => {
                        let lock = regular(&path)?;
                        single_cue_file(&lock)?;
                        if lock.metadata()?.len() != 0 {
                            return Err(invalid("scratch lock is not empty"));
                        }
                        // A crash between lock and header is an unknown credit,
                        // not permission to refund or reuse this namespace.
                        drop(regular(&path.with_extension("bin"))?);
                    }
                    _ => {
                        return Err(invalid(
                            "unknown scratch credit entry; explicit recovery required",
                        ))
                    }
                }
            }
        }
    }
    let floor = baseline
        .checked_add(reserved)
        .ok_or_else(|| invalid("scratch credit floor overflow"))?;
    let charge = actual.max(floor);
    Ok(ScratchUsage {
        actual,
        charge,
        outstanding: charge.saturating_sub(actual),
        floor,
        covered_extents,
    })
}

// SCRATCH_CORE_END

pub(super) struct RetainedMixStore {
    namespace: PathBuf,
    objects: AcceptedAudioStore,
    budget: MixStorageBudget,
    inventory_limit: usize,
    // One OS lock covers quota scan through copy/publication, across jobs and
    // supervisor processes. Never clone the handle or unlink its empty file.
    _lease: MixWriterLease,
}

/// Own only a successfully acquired lock, including before store construction.
/// Explicit unlock retires this operation's claim even if a concurrent Unix
/// fork retains a descriptor sharing its open-file description until exec.
struct MixWriterLease {
    file: File,
}

impl Drop for MixWriterLease {
    fn drop(&mut self) {
        // Keep the handle locked for the complete operation. Never unlink or
        // reopen its path: a later close of an old raw duplicate cannot unlock
        // a successor's independently opened claim on this same writer file.
        let _ = self.file.unlock();
    }
}

fn mix_writer_file(namespace: &Path, create: bool) -> SupervisorResult<File> {
    let path = namespace.join(".writer.lock");
    match fs::symlink_metadata(&path) {
        Ok(_) => {
            // Refuse a preexisting pipe/link/directory before writable open.
            drop(regular(&path)?);
        }
        Err(error) if create && error.kind() == std::io::ErrorKind::NotFound => (),
        Err(error) => return Err(error.into()),
    }
    let mut options = OpenOptions::new();
    options
        .read(true)
        .write(true)
        .create(create)
        .truncate(false);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000 | 0x800); // O_NOFOLLOW | O_NONBLOCK.
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT.
    }
    let file = options.open(&path)?;
    plain(&path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() != 0 {
        return Err(invalid("mix writer lock is not an empty regular file"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(invalid("mix writer lock cannot be a reparse point"));
        }
    }
    Ok(file)
}

fn mix_writer_lock(namespace: &Path, create: bool) -> SupervisorResult<MixWriterLease> {
    let file = mix_writer_file(namespace, create)?;
    match file.try_lock() {
        Ok(()) => Ok(MixWriterLease { file }),
        Err(TryLockError::WouldBlock) => Err(invalid(
            "another supervisor owns mix storage; wait for its checkpoint",
        )),
        Err(TryLockError::Error(error)) => Err(error.into()),
    }
}

fn mix_writer_lock_until(
    namespace: &Path,
    create: bool,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<MixWriterLease> {
    let until = deadline.min(Instant::now() + std::time::Duration::from_secs(5));
    let observe = |cancelled: &mut dyn FnMut() -> bool| {
        if cancelled() || Instant::now() >= until {
            Err(invalid(
                "output writer wait cancelled or reached its bounded deadline",
            ))
        } else {
            Ok(())
        }
    };
    observe(cancelled)?;
    // Keep one handle for the entire wait. Reopening each retry could lock a
    // replacement pathname while the original writer still owns the old file.
    let file = mix_writer_file(namespace, create)?;
    let identity = single_cue_file(&file)?;
    loop {
        observe(cancelled)?;
        match file.try_lock() {
            Ok(()) => {
                // Own the lock before any fallible identity/cancellation fence
                // so an error after acquisition also explicitly retires it.
                let lease = MixWriterLease { file };
                let fresh = regular(&namespace.join(".writer.lock"))?;
                if single_cue_file(&lease.file)? != identity
                    || single_cue_file(&fresh)? != identity
                    || lease.file.metadata()?.len() != 0
                    || fresh.metadata()?.len() != 0
                {
                    return Err(invalid("output writer identity changed during its wait"));
                }
                observe(cancelled)?;
                return Ok(lease);
            }
            Err(TryLockError::WouldBlock) => {
                std::thread::sleep(
                    std::time::Duration::from_millis(25)
                        .min(until.saturating_duration_since(Instant::now())),
                );
            }
            Err(TryLockError::Error(error)) => return Err(error.into()),
        }
    }
}

impl RetainedMixStore {
    /// Bounded single-cue PCM shares the existing output writer/quota/orphan
    /// accounting. Copy guards observe fresh caller reserve every four MiB.
    pub(super) fn accept_cue(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &PcmPin,
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<RetainedCueAudio> {
        pin.validate_cue()?;
        time_left(Some(deadline))?;
        plain(candidate_root)?;
        if !candidate_root.is_dir()
            || candidate_root
                .components()
                .any(|c| matches!(c, Component::ParentDir))
            || !candidate.is_absolute()
            || !candidate.starts_with(candidate_root)
            || candidate
                .components()
                .any(|c| matches!(c, Component::ParentDir))
            || candidate.starts_with(&self.namespace)
        {
            return Err(invalid(
                "cue candidate escaped its admitted private producer root",
            ));
        }
        let retained = RetainedCueAudio {
            path: self.objects.root.join(format!("{}.wav", &pin.sha256[7..])),
            pin: pin.clone(),
        };
        match fs::symlink_metadata(&retained.path) {
            Ok(_) => {
                self.verify_cue(&retained, deadline)?;
                return Ok(retained);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
        }
        let used = self.used_bytes(deadline)?;
        if used
            .checked_add(pin.size_bytes)
            .is_none_or(|n| n > self.budget.maximum_bytes)
        {
            return Err(invalid(
                "cue retention quota exhausted; preserve all prior/undo objects",
            ));
        }
        let needed = pin
            .size_bytes
            .checked_add(self.budget.minimum_free_bytes)
            .ok_or_else(|| invalid("cue retention reservation overflow"))?;
        if available_bytes(&self.namespace)? < needed {
            return Err(invalid(
                "cue output has insufficient fresh caller reserve; no control fallback",
            ));
        }
        let mut source = regular(candidate)?;
        single_cue_file(&source)?;
        if source.metadata()?.len() != pin.size_bytes {
            return Err(invalid("cue candidate size changed before retention"));
        }
        let (mut copy, private) = self.objects.private_copy()?;
        let mut space_guard = |copied: u64| -> SupervisorResult<()> {
            let remaining = pin
                .size_bytes
                .checked_sub(copied)
                .ok_or_else(|| invalid("cue copy reservation changed"))?;
            let needed = remaining
                .checked_add(self.budget.minimum_free_bytes)
                .ok_or_else(|| invalid("cue copy reserve overflow"))?;
            if available_bytes(&self.namespace)? < needed {
                return Err(invalid(
                    "cue caller reserve changed during copy; retain prior generation",
                ));
            }
            Ok(())
        };
        // The generic bounded byte streamer is shared; the cue's PCM header
        // is independently verified below. No render/file role is published.
        let bytes = RetainedFilePin {
            sha256: pin.sha256.clone(),
            size_bytes: pin.size_bytes,
            format: RetainedFileFormat::Json,
        };
        stream_render_file(
            &mut source,
            Some(&mut copy),
            &bytes,
            deadline,
            Some(&mut space_guard),
        )?;
        drop(space_guard);
        copy.sync_all()?;
        stream_pin_before(&mut copy, None, pin, Some(deadline))?;
        pcm(&mut copy, pin)?;
        if available_bytes(&self.namespace)? < self.budget.minimum_free_bytes {
            return Err(invalid("cue caller reserve changed before sealing"));
        }
        time_left(Some(deadline))?;
        drop(copy);
        self.objects.publish(&private, &retained.path)?;
        // Publication removes its owned temporary link before sealing. End
        // the copy guard before checking the final single-link media identity.
        drop(private);
        self.verify_cue(&retained, deadline)?;
        Ok(retained)
    }

    pub(super) fn verify_cue(
        &self,
        retained: &RetainedCueAudio,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        retained.pin.validate_cue()?;
        time_left(Some(deadline))?;
        if retained.path
            != self
                .objects
                .root
                .join(format!("{}.wav", &retained.pin.sha256[7..]))
        {
            return Err(invalid(
                "retained cue differs from its native job/content namespace",
            ));
        }
        let mut file = regular(&retained.path)?;
        let before = file.metadata()?;
        let identity = single_cue_file(&file)?;
        if !before.permissions().readonly() || before.len() != retained.pin.size_bytes {
            return Err(invalid("retained cue is unsealed or changed size"));
        }
        stream_pin_before(&mut file, None, &retained.pin, Some(deadline))?;
        pcm(&mut file, &retained.pin)?;
        let after = regular(&retained.path)?;
        if single_cue_file(&after)? != identity
            || single_cue_file(&file)? != identity
            || after.metadata()?.modified()? != before.modified()?
            || !after.metadata()?.permissions().readonly()
            || after.metadata()?.len() != retained.pin.size_bytes
            || file.metadata()?.len() != retained.pin.size_bytes
        {
            return Err(invalid("retained cue identity changed during verification"));
        }
        time_left(Some(deadline))
    }

    /// output_root is resolved from native immutable job configuration. The
    /// caller must not supply browser/worker roots or fall back to control.
    pub(super) fn new(
        output_root: &Path,
        job_id: &str,
        budget: MixStorageBudget,
    ) -> SupervisorResult<Self> {
        Self::open(output_root, job_id, budget, true)
    }

    /// Reopen/revalidate without creating a replacement namespace or lock.
    pub(super) fn open_existing(
        output_root: &Path,
        job_id: &str,
        budget: MixStorageBudget,
    ) -> SupervisorResult<Self> {
        Self::open(output_root, job_id, budget, false)
    }

    fn open(
        output_root: &Path,
        job_id: &str,
        budget: MixStorageBudget,
        create: bool,
    ) -> SupervisorResult<Self> {
        Self::open_with_writer(output_root, job_id, budget, create, mix_writer_lock)
    }

    /// Explicit short wait for callers that already own a bounded phase and
    /// cancellation observation. Existing fail-fast constructors stay unchanged.
    pub(super) fn new_until(
        output_root: &Path,
        job_id: &str,
        budget: MixStorageBudget,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<Self> {
        if cancelled() || Instant::now() >= deadline {
            return Err(invalid(
                "output writer wait cancelled or expired before open",
            ));
        }
        Self::open_with_writer(output_root, job_id, budget, true, |namespace, create| {
            mix_writer_lock_until(namespace, create, deadline, cancelled)
        })
    }

    fn open_with_writer(
        output_root: &Path,
        job_id: &str,
        budget: MixStorageBudget,
        create: bool,
        writer: impl FnOnce(&Path, bool) -> SupervisorResult<MixWriterLease>,
    ) -> SupervisorResult<Self> {
        super::validate_job_id(job_id)?;
        if budget.maximum_bytes == 0
            || output_root
                .components()
                .any(|c| matches!(c, Component::ParentDir))
        {
            return Err(invalid("invalid mix storage root or zero quota"));
        }
        plain(output_root)?;
        if !output_root.is_dir() {
            return Err(invalid(
                "mix output volume is unavailable; restore it explicitly",
            ));
        }
        let namespace = output_root.join(".dubflow-review-media-v1");
        if create {
            directory(&namespace)?;
        } else {
            plain(&namespace)?;
        }
        let lease = writer(&namespace, create)?;
        let root = namespace.join(format!("{:x}", Sha256::digest(job_id.as_bytes())));
        if create {
            directory(&root)?;
        } else {
            plain(&root)?;
            if !root.is_dir() {
                return Err(invalid("retained mix namespace is missing"));
            }
        }
        Ok(Self {
            namespace,
            objects: AcceptedAudioStore { root },
            budget,
            inventory_limit: MAX_MIX_STORE_ENTRIES,
            _lease: lease,
        })
    }

    fn used_bytes(&self, deadline: Instant) -> SupervisorResult<u64> {
        plain(&self.namespace)?;
        let mut count = 0usize;
        let mut total = 0u64;
        let mut account = |path: &Path| -> SupervisorResult<()> {
            time_left(Some(deadline))?;
            count += 1;
            if count > self.inventory_limit {
                return Err(invalid(
                    "mix object inventory limit; explicit recovery required",
                ));
            }
            plain(path)?;
            Ok(())
        };
        for entry in fs::read_dir(&self.namespace)? {
            let entry = entry?;
            let path = entry.path();
            account(&path)?;
            if entry.file_name() == ".writer.lock" {
                let lock = regular(&path)?;
                if lock.metadata()?.len() != 0 {
                    return Err(invalid("mix writer lock changed"));
                }
                continue;
            }
            let name = entry.file_name();
            let name = name.to_str().unwrap_or("");
            if name.len() != 64
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                || !entry.file_type()?.is_dir()
            {
                return Err(invalid(
                    "unknown mix storage namespace; explicit recovery required",
                ));
            }
            for object in fs::read_dir(&path)? {
                let object = object?;
                account(&object.path())?;
                if object.file_name() == ".editable-pending-v1"
                    || object.file_name() == ".editable-published-v1"
                {
                    if !object.file_type()?.is_dir() {
                        return Err(invalid("editable namespace is not a plain directory"));
                    }
                    total = total
                        .checked_add(editable_storage::inventory_bytes(
                            &object.path(),
                            &mut account,
                        )?)
                        .ok_or_else(|| invalid("editable aggregate byte count overflow"))?;
                    continue;
                }
                if object.file_name() == ".exports-pending-v1"
                    || object.file_name() == ".exports-published-v1"
                {
                    if !object.file_type()?.is_dir() {
                        return Err(invalid("Review export namespace is not a plain directory"));
                    }
                    for generation in fs::read_dir(object.path())? {
                        let generation = generation?;
                        account(&generation.path())?;
                        let name = generation.file_name();
                        let name = name.to_str().unwrap_or("");
                        if !valid_hash(&format!("sha256:{name}"))
                            || !generation.file_type()?.is_dir()
                        {
                            return Err(invalid("unknown Review export generation namespace"));
                        }
                        for file in fs::read_dir(generation.path())? {
                            let file = file?;
                            account(&file.path())?;
                            // Count every regular byte, including partial,
                            // foreign, old and unreferenced export files.
                            total = total
                                .checked_add(regular(&file.path())?.metadata()?.len())
                                .ok_or_else(|| invalid("Review export byte count overflow"))?;
                        }
                    }
                    continue;
                }
                let file = regular(&object.path())?;
                // Include .copy crash orphans, foreign regular files and all
                // old/undo objects. Never evict a potentially referenced file.
                total = total
                    .checked_add(file.metadata()?.len())
                    .ok_or_else(|| invalid("mix storage byte count overflow"))?;
            }
        }
        let output = self
            .namespace
            .parent()
            .ok_or_else(|| invalid("missing output namespace"))?;
        let usage = scratch_usage(
            output,
            &self.scratch_credit_root(),
            deadline,
            &mut count,
            self.inventory_limit,
        )?;
        // Materialized TEMP and logical credits cover the SAME bytes. Keep the
        // larger amount, rather than adding full reservations a second time.
        total
            .checked_sub(usage.covered_extents)
            .and_then(|n| n.checked_add(usage.charge))
            .ok_or_else(|| invalid("shared scratch/media quota overflow"))
    }

    fn scratch_credit_root(&self) -> PathBuf {
        self.namespace
            .join(format!("{:x}", Sha256::digest(SCRATCH_CREDITS.as_bytes())))
    }

    pub(super) fn reserve_scratch(
        &self,
        scratch: &Path,
        owner: &str,
        maximum: u64,
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<ScratchCredit> {
        time_left(Some(deadline))?;
        let output = self
            .namespace
            .parent()
            .ok_or_else(|| invalid("missing scratch output"))?;
        if self.objects.root != self.scratch_credit_root()
            || maximum == 0
            || maximum > SCRATCH_ALLOWANCE
        {
            return Err(invalid(
                "scratch credit requires its exact output-wide producer namespace",
            ));
        }
        let mut count = 0;
        let usage = scratch_usage(
            output,
            &self.objects.root,
            deadline,
            &mut count,
            self.inventory_limit,
        )?;
        scratch_preflight(
            self.used_bytes(deadline)?,
            usage.outstanding,
            maximum,
            available(output)?,
            self.budget,
        )?;
        time_left(Some(deadline))?;
        let mut directories = claim_scratch(output, scratch, owner, true)?;
        let mut directory_paths = vec![
            output.to_path_buf(),
            scratch.parent().unwrap().to_path_buf(),
            scratch.to_path_buf(),
        ];
        for path in [&self.namespace, &self.objects.root] {
            directories.push(scratch_directory(path)?.0);
            directory_paths.push(path.to_path_buf());
        }
        let private_before = scratch_actual(output, deadline, &mut 0, self.inventory_limit)?;
        let path = self.objects.root.join(format!(
            ".scratch-credit-v1-{}-{}.bin",
            std::process::id(),
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let lock_path = path.with_extension("lock");
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // Exact owned handles may delete on proven retirement. Other
            // writers cannot replace or rename their live paths.
            options
                .access_mode(0xC0010000)
                .custom_flags(0x00200000)
                .share_mode(3);
        }
        time_left(Some(deadline))?;
        let lock = options.open(&lock_path)?;
        lock.try_lock()
            .map_err(|_| invalid("scratch credit ownership refused"))?;
        let lock_identity = single_cue_file(&lock)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // Legacy quota readers may read metadata/header. No competing data
            // writer may change the active extent or record while we own it.
            options.share_mode(1);
        }
        let mut file = options.open(&path)?;
        let identity = single_cue_file(&file)?;
        let ids = [
            native_identity(&directories[0], true)?,
            native_identity(&directories[1], true)?,
            native_identity(&directories[2], true)?,
            identity,
            lock_identity,
        ];
        let header = scratch_credit_header(
            owner,
            maximum,
            private_before,
            scratch == output.join(".dfr/temp"),
            &ids,
        );
        if header.len() as u64 > SCRATCH_HEADER {
            return Err(invalid("scratch credit header bound"));
        }
        let extent = private_before
            .checked_add(maximum)
            .and_then(|n| n.checked_add(SCRATCH_HEADER))
            .ok_or_else(|| invalid("scratch extent overflow"))?;
        if available(output)?
            < extent
                .checked_add(self.budget.minimum_free_bytes)
                .and_then(|n| n.checked_add(usage.outstanding))
                .ok_or_else(|| invalid("scratch allocation overflow"))?
        {
            return Err(invalid("scratch backing extent lacks fresh reserve"));
        }
        time_left(Some(deadline))?;
        file.set_len(extent)?;
        let mut block = vec![0; SCRATCH_HEADER as usize];
        block[..header.len()].copy_from_slice(&header);
        file.write_all(&block)?;
        file.sync_all()?;
        let mut permissions = file.metadata()?.permissions();
        permissions.set_readonly(true);
        file.set_permissions(permissions)?;
        let credit = ScratchCredit {
            file,
            path,
            lock,
            lock_path,
            identity,
            lock_identity,
            header,
            maximum,
            private_before,
            directories,
            directory_paths,
        };
        let usage = scratch_usage(
            output,
            &self.objects.root,
            deadline,
            &mut 0,
            self.inventory_limit,
        )?;
        if self.used_bytes(deadline)? > self.budget.maximum_bytes
            || available(output)?
                < self
                    .budget
                    .minimum_free_bytes
                    .checked_add(usage.outstanding)
                    .ok_or_else(|| invalid("scratch reserve overflow"))?
        {
            return Err(invalid("scratch admission changed; credit remains charged"));
        }
        time_left(Some(deadline))?;
        Ok(credit)
    }

    pub(super) fn retire_scratch(
        &self,
        mut credit: ScratchCredit,
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        time_left(Some(deadline))?;
        let output = self
            .namespace
            .parent()
            .ok_or_else(|| invalid("missing scratch output"))?;
        if single_cue_file(&regular(&credit.path)?)? != credit.identity
            || single_cue_file(&credit.file)? != credit.identity
            || single_cue_file(&regular(&credit.lock_path)?)? != credit.lock_identity
            || single_cue_file(&credit.lock)? != credit.lock_identity
            || credit.lock.metadata()?.len() != 0
            || credit.file.metadata()?.len()
                != credit
                    .private_before
                    .checked_add(credit.maximum)
                    .and_then(|n| n.checked_add(SCRATCH_HEADER))
                    .ok_or_else(|| invalid("scratch extent overflow"))?
        {
            return Err(invalid("scratch credit identity/extent changed"));
        }
        credit.file.seek(SeekFrom::Start(0))?;
        let mut header = vec![0; credit.header.len()];
        credit.file.read_exact(&mut header)?;
        if header != credit.header
            || read_scratch_credit(&credit.path, output)? != (credit.maximum, credit.private_before)
        {
            return Err(invalid("scratch credit bytes changed"));
        }
        for (path, directory) in credit.directory_paths.iter().zip(&credit.directories) {
            time_left(Some(deadline))?;
            if scratch_directory(path)?.1 != native_identity(directory, true)? {
                return Err(invalid(
                    "scratch directory pathname changed its held identity",
                ));
            }
        }
        let usage = scratch_usage(
            output,
            &self.objects.root,
            deadline,
            &mut 0,
            self.inventory_limit,
        )?;
        if usage.actual > usage.floor
            || self.used_bytes(deadline)? > self.budget.maximum_bytes
            || available(output)?
                < self
                    .budget
                    .minimum_free_bytes
                    .checked_add(usage.outstanding)
                    .ok_or_else(|| invalid("scratch reserve overflow"))?
        {
            return Err(invalid(
                "scratch retirement lacks quota/reserve; preserve charged orphan",
            ));
        }
        time_left(Some(deadline))?;
        // The stopped writer no longer needs its maximum. Preserve a sealed
        // retired ledger extent covering all remaining TEMP/orphans. Older
        // writers still see those bytes through metadata.len(); no old object
        // or unknown credit is deleted/refunded. A crash while rewriting leaves
        // an invalid record requiring explicit recovery, never implicit success.
        let ids = [
            native_identity(&credit.directories[0], true)?,
            native_identity(&credit.directories[1], true)?,
            native_identity(&credit.directories[2], true)?,
            credit.identity,
            credit.lock_identity,
        ];
        let text = std::str::from_utf8(&credit.header)
            .map_err(|_| invalid("scratch credit UTF8 changed"))?;
        let owner = text
            .lines()
            .nth(1)
            .ok_or_else(|| invalid("scratch owner missing"))?;
        let compact = credit.directory_paths[2] == output.join(".dfr/temp");
        let retired = scratch_credit_header(owner, 0, usage.actual, compact, &ids);
        let mut block = vec![0; SCRATCH_HEADER as usize];
        block[..retired.len()].copy_from_slice(&retired);
        let mut permissions = credit.file.metadata()?.permissions();
        permissions.set_readonly(false); // Exact owned credit only, after stopped-tree proof.
        credit.file.set_permissions(permissions)?;
        credit.file.seek(SeekFrom::Start(0))?;
        credit.file.write_all(&block)?;
        credit.file.sync_all()?;
        time_left(Some(deadline))?;
        credit.file.set_len(
            usage
                .actual
                .checked_add(SCRATCH_HEADER)
                .ok_or_else(|| invalid("retired extent overflow"))?,
        )?;
        credit.file.sync_all()?;
        let mut permissions = credit.file.metadata()?.permissions();
        permissions.set_readonly(true);
        credit.file.set_permissions(permissions)?;
        time_left(Some(deadline))?;
        drop(credit);
        Ok(())
    }

    /// Native small checkpoint records share the exact output writer/aggregate
    /// quota with media. No candidate path/control-root fallback or semantic
    /// grant is introduced. Keep every prior/orphan byte and never replace a
    /// collision, including corrupt/unsealed metadata.
    pub(super) fn accept_json_record(
        &self,
        raw: &str,
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<RetainedRenderFile> {
        time_left(Some(deadline))?;
        if raw.is_empty() || raw.len() > MAX_RETAINED_METADATA_BYTES {
            return Err(invalid(
                "retained checkpoint metadata exceeds its small-record bound",
            ));
        }
        json_syntax(raw)?;
        let pin = RetainedFilePin {
            sha256: format!("sha256:{:x}", Sha256::digest(raw.as_bytes())),
            size_bytes: raw.len() as u64,
            format: RetainedFileFormat::Json,
        };
        let retained = RetainedRenderFile {
            path: self.objects.root.join(format!("{}.json", &pin.sha256[7..])),
            pin,
        };
        match fs::symlink_metadata(&retained.path) {
            Ok(_) => {
                self.read_json_record(&retained, deadline)?;
                return Ok(retained);
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.into()),
        }
        let used = self.used_bytes(deadline)?;
        if used
            .checked_add(retained.pin.size_bytes)
            .is_none_or(|n| n > self.budget.maximum_bytes)
        {
            return Err(invalid(
                "retained checkpoint metadata exceeds shared media quota; preserve prior objects",
            ));
        }
        let reserve = |remaining: u64| {
            remaining
                .checked_add(self.budget.minimum_free_bytes)
                .ok_or_else(|| invalid("retained checkpoint metadata reserve overflow"))
        };
        if available_bytes(&self.namespace)? < reserve(retained.pin.size_bytes)? {
            return Err(invalid(
                "retained checkpoint metadata has insufficient fresh caller space",
            ));
        }
        let (mut copy, private) = self.objects.private_copy()?;
        let mut written = 0u64;
        for bytes in raw.as_bytes().chunks(BUFFER_BYTES) {
            time_left(Some(deadline))?;
            if available_bytes(&self.namespace)? < reserve(retained.pin.size_bytes - written)? {
                return Err(invalid(
                    "retained checkpoint metadata caller reserve changed during write",
                ));
            }
            copy.write_all(bytes)?;
            written += bytes.len() as u64;
        }
        copy.sync_all()?;
        read_json_file(&mut copy, &retained.pin.sha256, retained.pin.size_bytes)?;
        if available_bytes(&self.namespace)? < self.budget.minimum_free_bytes {
            return Err(invalid(
                "retained checkpoint metadata caller reserve changed before sealing",
            ));
        }
        time_left(Some(deadline))?;
        drop(copy);
        self.objects.publish(&private, &retained.path)?;
        drop(private); // Remove our temporary link before single-file reopening.
        self.read_json_record(&retained, deadline)?;
        Ok(retained)
    }

    pub(super) fn read_json_record(
        &self,
        retained: &RetainedRenderFile,
        deadline: Instant,
    ) -> SupervisorResult<String> {
        if retained.pin.format != RetainedFileFormat::Json
            || retained.pin.size_bytes > MAX_RETAINED_METADATA_BYTES as u64
        {
            return Err(invalid(
                "retained checkpoint record has a foreign format or bound",
            ));
        }
        self.verify_file(retained, deadline)?;
        let mut file = regular(&retained.path)?;
        let identity = single_cue_file(&file)?;
        let modified = file.metadata()?.modified()?;
        let raw = read_json_file(&mut file, &retained.pin.sha256, retained.pin.size_bytes)?;
        let after = regular(&retained.path)?;
        if single_cue_file(&after)? != identity
            || single_cue_file(&file)? != identity
            || after.metadata()?.modified()? != modified
            || !after.metadata()?.permissions().readonly()
        {
            return Err(invalid(
                "retained checkpoint metadata handle identity or seal changed",
            ));
        }
        self.verify_file(retained, deadline)?;
        Ok(raw)
    }

    /// Same writer/quota namespace as PCM, including old/undo/crash objects.
    /// Full media semantics and QC must be admitted by the render caller first.
    pub(super) fn accept_file(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &RetainedFilePin,
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<RetainedRenderFile> {
        pin.validate()?;
        time_left(Some(deadline))?;
        plain(candidate_root)?;
        if !candidate_root.is_dir()
            || !candidate_root.is_absolute()
            || !candidate.is_absolute()
            || !candidate.starts_with(candidate_root)
            || candidate
                .components()
                .any(|c| matches!(c, Component::ParentDir))
            || candidate.starts_with(&self.namespace)
        {
            return Err(invalid(
                "render candidate escaped its admitted private generation",
            ));
        }
        let retained = RetainedRenderFile {
            path: self.objects.root.join(format!(
                "{}.{}",
                &pin.sha256[7..],
                pin.format.extension()
            )),
            pin: pin.clone(),
        };
        match fs::symlink_metadata(&retained.path) {
            Ok(_) => {
                self.verify_file(&retained, deadline)?;
                return Ok(retained);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
        }
        let used = self.used_bytes(deadline)?;
        if used
            .checked_add(pin.size_bytes)
            .filter(|n| *n <= self.budget.maximum_bytes)
            .is_none()
        {
            return Err(invalid(
                "Review media quota exhausted; preserve prior outputs and undo objects",
            ));
        }
        let needed = pin
            .size_bytes
            .checked_add(self.budget.minimum_free_bytes)
            .ok_or_else(|| invalid("render retention reservation overflow"))?;
        if available_bytes(&self.namespace)? < needed {
            return Err(invalid(
                "render retention has insufficient fresh caller space; no fallback",
            ));
        }
        let mut source = regular(candidate)?;
        if source.metadata()?.len() != pin.size_bytes {
            return Err(invalid("render candidate size changed before retention"));
        }
        let (mut copy, private) = self.objects.private_copy()?;
        let mut space_guard = |copied: u64| -> SupervisorResult<()> {
            let remaining = pin
                .size_bytes
                .checked_sub(copied)
                .ok_or_else(|| invalid("render reservation accounting changed"))?;
            let required = remaining
                .checked_add(self.budget.minimum_free_bytes)
                .ok_or_else(|| invalid("render reservation overflow"))?;
            if available_bytes(&self.namespace)? < required {
                return Err(invalid(
                    "render retention caller space changed during copy; preserve previous outputs",
                ));
            }
            Ok(())
        };
        stream_render_file(
            &mut source,
            Some(&mut copy),
            pin,
            deadline,
            Some(&mut space_guard),
        )?;
        drop(space_guard);
        copy.sync_all()?;
        stream_render_file(&mut copy, None, pin, deadline, None)?;
        if available_bytes(&self.namespace)? < self.budget.minimum_free_bytes {
            return Err(invalid(
                "render retention free reserve changed before sealing",
            ));
        }
        time_left(Some(deadline))?;
        drop(copy);
        self.objects.publish(&private, &retained.path)?;
        self.verify_file(&retained, deadline)?;
        Ok(retained)
    }

    pub(super) fn verify_file(
        &self,
        retained: &RetainedRenderFile,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        retained.pin.validate()?;
        time_left(Some(deadline))?;
        if retained.path
            != self.objects.root.join(format!(
                "{}.{}",
                &retained.pin.sha256[7..],
                retained.pin.format.extension()
            ))
        {
            return Err(invalid(
                "retained render file differs from its native content/format identity",
            ));
        }
        let mut file = regular(&retained.path)?;
        let before = file.metadata()?;
        if !before.permissions().readonly() || before.len() != retained.pin.size_bytes {
            return Err(invalid(
                "retained render file is unsealed or changed size; explicit recovery required",
            ));
        }
        stream_render_file(&mut file, None, &retained.pin, deadline, None)?;
        let after = regular(&retained.path)?.metadata()?;
        if before.modified()? != after.modified()?
            || before.len() != after.len()
            || !after.permissions().readonly()
        {
            return Err(invalid("retained render changed during verification"));
        }
        Ok(())
    }

    pub(super) fn accept(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &PcmPin,
        // A native OS/storage adapter, invoked after the locked quota scan.
        // Unknown space MUST return Err; this API never assumes free capacity.
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<RetainedMixAudio> {
        pin.validate_mix()?;
        time_left(Some(deadline))?;
        plain(candidate_root)?;
        if !candidate_root.is_dir()
            || !candidate.is_absolute()
            || !candidate.starts_with(candidate_root)
            || candidate
                .components()
                .any(|c| matches!(c, Component::ParentDir))
            || candidate.starts_with(&self.namespace)
        {
            return Err(invalid(
                "mix candidate is outside its private producer root",
            ));
        }
        let accepted = RetainedMixAudio {
            path: self.objects.root.join(format!("{}.wav", &pin.sha256[7..])),
            pin: pin.clone(),
        };
        match fs::symlink_metadata(&accepted.path) {
            Ok(_) => {
                // Reuse needs no copy/extra quota, even when the volume is full.
                // A corrupt/unsealed collision never authorizes replacement.
                self.verify(&accepted, deadline)?;
                return Ok(accepted);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
        }
        let used = self.used_bytes(deadline)?;
        if used
            .checked_add(pin.size_bytes)
            .filter(|n| *n <= self.budget.maximum_bytes)
            .is_none()
        {
            return Err(invalid(
                "mix storage quota exhausted; preserve outputs and undo assets",
            ));
        }
        let needed = pin
            .size_bytes
            .checked_add(self.budget.minimum_free_bytes)
            .ok_or_else(|| invalid("mix storage reservation overflow"))?;
        if available_bytes(&self.namespace)? < needed {
            return Err(invalid(
                "mix volume has insufficient free space; no control-root fallback",
            ));
        }
        time_left(Some(deadline))?;
        let mut source = regular(candidate)?;
        if source.metadata()?.len() != pin.size_bytes {
            return Err(invalid("mix candidate size changed before copy"));
        }
        let (mut copy, private) = self.objects.private_copy()?;
        stream_pin_before(&mut source, Some(&mut copy), pin, Some(deadline))?;
        copy.sync_all()?;
        stream_pin_before(&mut copy, None, pin, Some(deadline))?;
        pcm(&mut copy, pin)?;
        time_left(Some(deadline))?;
        drop(copy);
        self.objects.publish(&private, &accepted.path)?;
        self.verify(&accepted, deadline)?;
        Ok(accepted)
    }

    /// No silent repair/eviction: every reuse/undo rechecks sealed bytes and PCM.
    pub(super) fn verify(
        &self,
        accepted: &RetainedMixAudio,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        accepted.pin.validate_mix()?;
        if accepted.path
            != self
                .objects
                .root
                .join(format!("{}.wav", &accepted.pin.sha256[7..]))
        {
            return Err(invalid(
                "retained mix path differs from its native identity",
            ));
        }
        if !regular(&accepted.path)?
            .metadata()?
            .permissions()
            .readonly()
        {
            return Err(invalid(
                "retained mix is no longer sealed; explicit recovery required",
            ));
        }
        verify_private_mix_audio(&self.objects.root, &accepted.path, &accepted.pin, deadline)
    }
}

impl AcceptedAudioStore {
    /// The caller resolves the durable data root from supervisor configuration.
    fn root(data_root: &Path, job_id: &str, create: bool) -> SupervisorResult<Self> {
        super::validate_job_id(job_id)?;
        plain(data_root)?;
        if !data_root.is_dir() {
            return Err(invalid("accepted audio requires an existing data root"));
        }
        let control = data_root.join("control");
        plain(&control)?;
        if !control.is_dir() {
            return Err(invalid(
                "accepted audio requires the supervisor control root",
            ));
        }
        let assets = control.join("review-assets");
        if create {
            directory(&assets)?;
        } else {
            plain(&assets)?;
        }
        // A digest namespace supports colon-bearing contract IDs on Windows.
        let root = assets.join(format!("{:x}", Sha256::digest(job_id.as_bytes())));
        if create {
            directory(&root)?;
        } else {
            plain(&root)?;
        }
        if !assets.is_dir() || !root.is_dir() {
            return Err(invalid("accepted store is missing"));
        }
        Ok(Self { root })
    }

    pub(super) fn new(data_root: &Path, job_id: &str) -> SupervisorResult<Self> {
        Self::root(data_root, job_id, true)
    }

    pub(super) fn open_existing(data_root: &Path, job_id: &str) -> SupervisorResult<Self> {
        Self::root(data_root, job_id, false)
    }

    fn private_copy(&self) -> SupervisorResult<(File, PrivateCopy)> {
        plain(&self.root)?;
        let part = self.root.join(format!(
            ".copy-{}-{}",
            std::process::id(),
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let copy = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&part)?;
        Ok((copy, PrivateCopy(part)))
    }

    fn publish(&self, private: &PrivateCopy, destination: &Path) -> SupervisorResult<()> {
        // Exclusive publication cannot replace any prior accepted object. A
        // competing publisher's unsealed/incomplete object refuses on reuse.
        match fs::hard_link(&private.0, destination) {
            Ok(()) => {
                fs::remove_file(&private.0)?;
                let mut permissions = fs::metadata(destination)?.permissions();
                permissions.set_readonly(true);
                fs::set_permissions(destination, permissions)?;
                #[cfg(unix)]
                File::open(&self.root)?.sync_all()?;
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(e.into()),
        }
        Ok(())
    }

    /// The graph caller supplies already semantically admitted metadata bytes,
    /// never a browser/worker path. Syntax alone does not authorize graph reuse.
    pub(super) fn accept_json(&self, raw: &str) -> SupervisorResult<AcceptedJsonDocument> {
        json_syntax(raw)?;
        let hash = format!("sha256:{:x}", Sha256::digest(raw.as_bytes()));
        let size = raw.len() as u64;
        let (mut copy, private) = self.private_copy()?;
        copy.write_all(raw.as_bytes())?;
        copy.sync_all()?;
        read_json_file(&mut copy, &hash, size)?;
        drop(copy);
        let destination = self.root.join(format!("{}.json", &hash[7..]));
        self.publish(&private, &destination)?;
        let accepted = AcceptedJsonDocument {
            path: destination,
            sha256: hash,
            size_bytes: size,
        };
        self.read_json(&accepted)?;
        Ok(accepted)
    }

    /// Every graph/undo reuse reads and validates one sealed regular-file handle.
    /// Preserve raw UTF-8: reserialization would change producer input hashes.
    pub(super) fn read_json(&self, accepted: &AcceptedJsonDocument) -> SupervisorResult<String> {
        if !valid_hash(&accepted.sha256)
            || !(1..=MAX_DOCUMENT_BYTES).contains(&accepted.size_bytes)
            || accepted.path != self.root.join(format!("{}.json", &accepted.sha256[7..]))
        {
            return Err(invalid(
                "accepted JSON path or pin differs from native identity",
            ));
        }
        let mut file = regular(&accepted.path)?;
        if !file.metadata()?.permissions().readonly() {
            return Err(invalid(
                "accepted JSON is no longer sealed; explicit recovery required",
            ));
        }
        read_json_file(&mut file, &accepted.sha256, accepted.size_bytes)
    }

    /// Copy one bounded cue from the supervisor's private candidate root.
    /// Only the copied bytes are admitted. No candidate pathname is returned.
    pub(super) fn accept(
        &self,
        candidate_root: &Path,
        candidate: &Path,
        pin: &PcmPin,
    ) -> SupervisorResult<AcceptedCueAudio> {
        pin.validate()?;
        plain(&self.root)?;
        plain(candidate_root)?;
        if !candidate_root.is_dir()
            || !candidate.starts_with(candidate_root)
            || candidate.starts_with(&self.root)
        {
            return Err(invalid(
                "cue candidate is outside its private producer root",
            ));
        }
        let mut source = regular(candidate)?;
        if source.metadata()?.len() != pin.size_bytes {
            return Err(invalid("cue candidate size changed before copy"));
        }
        let (mut copy, private) = self.private_copy()?;
        stream_pin(&mut source, Some(&mut copy), pin)?;
        copy.sync_all()?;
        stream_pin(&mut copy, None, pin)?;
        pcm(&mut copy, pin)?;
        drop(copy);

        let destination = self.root.join(format!("{}.wav", &pin.sha256[7..]));
        self.publish(&private, &destination)?;
        let accepted = AcceptedCueAudio {
            path: destination,
            pin: pin.clone(),
        };
        self.verify(&accepted)?;
        Ok(accepted)
    }

    /// Revalidate a stored pin before graph reuse/undo; never silently repair.
    pub(super) fn verify(&self, accepted: &AcceptedCueAudio) -> SupervisorResult<()> {
        accepted.pin.validate()?;
        let path = self.root.join(format!("{}.wav", &accepted.pin.sha256[7..]));
        if accepted.path != path {
            return Err(invalid("accepted cue path differs from its native pin"));
        }
        let mut file = regular(&path)?;
        if !file.metadata()?.permissions().readonly() {
            return Err(invalid(
                "accepted cue is no longer sealed; explicit recovery required",
            ));
        }
        stream_pin(&mut file, None, &accepted.pin)?;
        pcm(&mut file, &accepted.pin)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scratch_claim_refuses_ambient_compact_and_preserves_original_bytes() {
        let (fixture, _, _) = fixture();
        let output = fixture.0.join("scratch-output");
        fs::create_dir(&output).unwrap();
        fs::write(output.join("original"), b"immutable old export").unwrap();
        fs::create_dir(output.join(".dfr")).unwrap();
        assert!(claim_scratch(&output, &output.join(".dfr/temp"), &"a".repeat(64), true).is_err());
        assert!(!output.join(".dfr/temp").exists());
        assert_eq!(
            fs::read(output.join("original")).unwrap(),
            b"immutable old export"
        );
    }

    #[test]
    fn scratch_credit_drop_charges_union_and_does_not_recount_chunk_workspace() {
        let (fixture, _, _) = fixture();
        let output = fixture.0.join("scratch-credit-output");
        fs::create_dir(&output).unwrap();
        let legacy = output.join(".dubflow-review-candidates/other-job/chunk-capacity-v1-owned");
        fs::create_dir_all(&legacy).unwrap();
        let candidate = legacy.join("completed-B2.pcm");
        File::create(&candidate)
            .unwrap()
            .set_len(104525926)
            .unwrap();
        let store =
            RetainedMixStore::new(&output, SCRATCH_CREDITS, budget(1024 * 1024 * 1024)).unwrap();
        let scratch = output.join(".dfr/temp");
        let credit = store
            .reserve_scratch(
                &scratch,
                &"a".repeat(64),
                SCRATCH_ALLOWANCE,
                |_| Ok(u64::MAX),
                deadline(),
            )
            .unwrap();
        let path = credit.path.clone();
        #[cfg(windows)]
        {
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(false); // Defeat attributes to test the held sharing mode itself.
            fs::set_permissions(&path, permissions).unwrap();
            let error = OpenOptions::new().write(true).open(&path).unwrap_err();
            assert_eq!(error.raw_os_error(), Some(32)); // ERROR_SHARING_VIOLATION.
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(true);
            fs::set_permissions(&path, permissions).unwrap();
            assert!(regular(&path).is_ok()); // Existing Rust quota-reader access remains allowed.
        }
        let before = store.used_bytes(deadline()).unwrap();
        fs::write(scratch.join("old-orphan.pcm"), [9u8; 17]).unwrap();
        assert_eq!(store.used_bytes(deadline()).unwrap(), before); // Covered, not added twice.
                                                                   // A73d writer only sums file lengths and still sees the full reservation.
        assert!(fs::metadata(&path).unwrap().len() >= SCRATCH_ALLOWANCE);
        drop(credit); // No stopped-tree proof: no refund or cleanup.
        assert!(path.exists());
        assert_eq!(store.used_bytes(deadline()).unwrap(), before);
        assert!(before < SCRATCH_ALLOWANCE + 2 * SCRATCH_HEADER);
        assert_eq!(fs::metadata(candidate).unwrap().len(), 104525926);
        assert!(scratch_preflight(100, 0, 512, 100, budget(100000)).is_err());
        assert!(scratch_preflight(100, 0, 512, u64::MAX, budget(500)).is_err());
        assert!(scratch_preflight(100, 1024, 512, 1024, budget(100000)).is_err());
        for path in [path, output.join(".dfr/.owner-v1")] {
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(false); // Exact test-created files only.
            fs::set_permissions(path, permissions).unwrap();
        }
    }

    #[test]
    fn scratch_reopen_checks_ownership_directory_identity_and_retirement_reserve() {
        let (fixture, _, _) = fixture();
        let output = fixture.0.join("scratch-retire-output");
        fs::create_dir(&output).unwrap();
        let store =
            RetainedMixStore::new(&output, SCRATCH_CREDITS, budget(1024 * 1024 * 1024)).unwrap();
        let scratch = output.join(".dfr/temp");
        let owner = "a".repeat(64);
        let credit = store
            .reserve_scratch(
                &scratch,
                &owner,
                SCRATCH_ALLOWANCE,
                |_| Ok(u64::MAX),
                deadline(),
            )
            .unwrap();
        assert!(claim_scratch(&output, &scratch, &"b".repeat(64), false).is_err());
        let path = credit.path.clone();
        assert!(store.retire_scratch(credit, |_| Ok(0), deadline()).is_err());
        assert!(path.exists());
        let credit = store
            .reserve_scratch(
                &scratch,
                &owner,
                SCRATCH_ALLOWANCE,
                |_| Ok(u64::MAX),
                deadline(),
            )
            .unwrap();
        let second = credit.path.clone();
        fs::write(scratch.join("retained-orphan.pcm"), [1u8; 9]).unwrap();
        store
            .retire_scratch(credit, |_| Ok(u64::MAX), deadline())
            .unwrap();
        assert!(second.exists());
        assert_eq!(read_scratch_credit(&second, &output).unwrap().0, 0);
        assert!(path.exists());
        assert_eq!(
            fs::metadata(scratch.join("retained-orphan.pcm"))
                .unwrap()
                .len(),
            9
        );
        assert!(store
            .reserve_scratch(
                &scratch,
                &owner,
                SCRATCH_ALLOWANCE,
                |_| Ok(u64::MAX),
                Instant::now()
            )
            .is_err());
        for path in [path, second, output.join(".dfr/.owner-v1")] {
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(false); // Exact test-created files only.
            fs::set_permissions(path, permissions).unwrap();
        }
    }

    #[test]
    fn scratch_inventory_refuses_entry_fanout_and_deadline_while_enumerating() {
        let (fixture, _, _) = fixture();
        let root = fixture.0.join("fanout");
        fs::create_dir(&root).unwrap();
        for n in 0..8 {
            fs::write(root.join(n.to_string()), [0]).unwrap();
        }
        let mut count = 0;
        assert!(scratch_inventory(&root, deadline(), &mut count, 4).is_err());
        assert_eq!(count, 4);
        assert!(scratch_inventory(&root, Instant::now(), &mut 0, 100).is_err());
    }

    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Windows needs readonly accepted files unsealed before fixture
            // deletion; production has no deletion/unseal operation.
            for assets in [
                self.0.join("control/review-assets"),
                self.0.join(".dubflow-review-media-v1"),
            ] {
                if let Ok(jobs) = fs::read_dir(assets) {
                    for job in jobs.flatten() {
                        if let Ok(files) = fs::read_dir(job.path()) {
                            for entry in files.flatten() {
                                if let Ok(metadata) = entry.metadata() {
                                    if metadata.is_file() && metadata.permissions().readonly() {
                                        let mut permissions = metadata.permissions();
                                        #[cfg(windows)]
                                        permissions.set_readonly(false);
                                        #[cfg(unix)]
                                        {
                                            use std::os::unix::fs::PermissionsExt;
                                            permissions.set_mode(0o600);
                                        }
                                        let _ = fs::set_permissions(entry.path(), permissions);
                                    }
                                }
                            }
                        }
                    }
                }
            }
            assert!(self.0.starts_with(std::env::temp_dir()));
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn fixture() -> (Fixture, AcceptedAudioStore, PathBuf) {
        let root = std::env::temp_dir().join(format!(
            "dubflow review assets {} {}",
            std::process::id(),
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(root.join("control")).unwrap();
        let candidate = root.join("producer candidate");
        fs::create_dir(&candidate).unwrap();
        let store = AcceptedAudioStore::new(&root, "job:review-a").unwrap();
        (Fixture(root), store, candidate)
    }

    fn wave(frames: u64, channels: u16, ancillary: bool) -> (Vec<u8>, PcmPin) {
        let payload = frames as usize * channels as usize * 2;
        let mut bytes = b"RIFF\0\0\0\0WAVE".to_vec();
        if ancillary {
            bytes.extend_from_slice(b"JUNK\x03\0\0\0abc\0");
        }
        bytes.extend_from_slice(b"fmt \x10\0\0\0\x01\0");
        bytes.extend_from_slice(&channels.to_le_bytes());
        bytes.extend_from_slice(&48000u32.to_le_bytes());
        bytes.extend_from_slice(&(48000u32 * u32::from(channels) * 2).to_le_bytes());
        bytes.extend_from_slice(&(channels * 2).to_le_bytes());
        bytes.extend_from_slice(&16u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&(payload as u32).to_le_bytes());
        bytes.extend((0..payload).map(|i| (i % 251) as u8));
        let riff = bytes.len() as u32 - 8;
        bytes[4..8].copy_from_slice(&riff.to_le_bytes());
        let pin = pin(&bytes, frames, channels);
        (bytes, pin)
    }

    #[test]
    fn private_full_mix_verifies_stereo_pcm_without_accepting_or_publishing_it() {
        let (_fixture, store, root) = fixture();
        let (bytes, pin) = wave(4800, 2, true);
        let path = root.join("final.wav");
        fs::write(&path, &bytes).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        verify_private_mix_audio(&root, &path, &pin, deadline).unwrap();
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
        assert!(!fs::metadata(&path).unwrap().permissions().readonly());
        assert!(verify_private_mix_audio(&root, &path, &pin, std::time::Instant::now()).is_err());
        for changed in [
            PcmPin {
                channels: 1,
                ..pin.clone()
            },
            PcmPin {
                frame_count: 48000 * 6 * 3600 + 1,
                ..pin.clone()
            },
            PcmPin {
                frame_count: pin.frame_count - 1,
                ..pin.clone()
            },
        ] {
            assert!(verify_private_mix_audio(&root, &path, &changed, deadline).is_err());
        }
        let foreign = root.parent().unwrap().join("outside.wav");
        fs::write(&foreign, &bytes).unwrap();
        assert!(verify_private_mix_audio(&root, &foreign, &pin, deadline).is_err());
        let mut bad = bytes;
        *bad.last_mut().unwrap() ^= 1;
        fs::write(&path, bad).unwrap();
        assert!(verify_private_mix_audio(&root, &path, &pin, deadline).is_err());
    }

    #[test]
    fn original_cue_reopen_checks_full_hash_pcm_root_deadline_without_copy_or_mutation() {
        let (_fixture, store, root) = fixture();
        let (bytes, pin) = wave(480, 1, false);
        let path = root.join("original-cue.wav");
        fs::write(&path, &bytes).unwrap();
        let modified = fs::metadata(&path).unwrap().modified().unwrap();
        verify_original_cue_audio(&root, &path, &pin, deadline()).unwrap();
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), modified);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
        assert!(verify_original_cue_audio(&root, &path, &pin, Instant::now()).is_err());
        let changed = PcmPin {
            frame_count: pin.frame_count - 1,
            ..pin.clone()
        };
        assert!(verify_original_cue_audio(&root, &path, &changed, deadline()).is_err());
        let foreign = root.parent().unwrap().join("foreign-cue.wav");
        fs::write(&foreign, &bytes).unwrap();
        assert!(verify_original_cue_audio(&root, &foreign, &pin, deadline()).is_err());
        let mut corrupted = bytes;
        *corrupted.last_mut().unwrap() ^= 1;
        fs::write(&path, corrupted).unwrap();
        assert!(verify_original_cue_audio(&root, &path, &pin, deadline()).is_err());
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
    }

    #[test]
    fn cue_output_retention_shares_quota_reuses_sealed_bytes_and_rejects_mix_or_link_substitution()
    {
        let (fixture, _, candidate) = fixture();
        let (bytes, pin) = wave(4800, 1, false);
        let path = candidate.join("cue.wav");
        fs::write(&path, &bytes).unwrap();
        let source_modified = fs::metadata(&path).unwrap().modified().unwrap();
        let store =
            RetainedMixStore::new(&fixture.0, "cue-output", budget(pin.size_bytes)).unwrap();
        let retained = store
            .accept_cue(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        assert_ne!(retained.path, path);
        assert_eq!(retained.pin, pin);
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert_eq!(
            fs::metadata(&path).unwrap().modified().unwrap(),
            source_modified
        );
        assert_eq!(store.used_bytes(deadline()).unwrap(), pin.size_bytes);
        let modified = fs::metadata(&retained.path).unwrap().modified().unwrap();
        assert!(single_cue_file(&regular(&retained.path).unwrap()).is_ok());
        let same = store
            .accept_cue(
                &candidate,
                &path,
                &pin,
                |_| panic!("valid dedup allocates no new bytes"),
                deadline(),
            )
            .unwrap();
        assert_eq!(same, retained);
        assert_eq!(
            fs::metadata(&same.path).unwrap().modified().unwrap(),
            modified
        );
        assert!(store
            .accept(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err()); // mono is not full mix.
        let video = candidate.join("new.mp4");
        fs::write(&video, b"new render candidate").unwrap();
        assert!(store
            .accept_file(
                &candidate,
                &video,
                &render_pin(b"new render candidate", RetainedFileFormat::Mp4),
                |_| Ok(u64::MAX),
                deadline()
            )
            .is_err()); // cue bytes reserve the same aggregate quota.
        let extra = retained.path.with_extension("link");
        fs::hard_link(&retained.path, &extra).unwrap();
        assert!(store.verify_cue(&retained, deadline()).is_err());
        #[cfg(windows)]
        {
            let mut permissions = fs::metadata(&extra).unwrap().permissions();
            permissions.set_readonly(false);
            fs::set_permissions(&extra, permissions).unwrap();
        }
        fs::remove_file(&extra).unwrap();
        #[cfg(windows)]
        {
            let mut permissions = fs::metadata(&retained.path).unwrap().permissions();
            permissions.set_readonly(true);
            fs::set_permissions(&retained.path, permissions).unwrap();
        }
        assert!(store.verify_cue(&retained, Instant::now()).is_err());
        store.verify_cue(&retained, deadline()).unwrap();
    }

    #[test]
    fn cue_copy_refuses_changed_caller_reserve_after_four_mib_without_retiring_prior_objects() {
        let (fixture, _, candidate) = fixture();
        let (bytes, pin) = wave(5 * 1024 * 1024 / 2, 1, false);
        let path = candidate.join("large-cue.wav");
        fs::write(&path, &bytes).unwrap();
        let store =
            RetainedMixStore::new(&fixture.0, "cue-space", budget(pin.size_bytes + 32)).unwrap();
        let old = candidate.join("old.mp4");
        fs::write(&old, b"valid prior render").unwrap();
        let prior = store
            .accept_file(
                &candidate,
                &old,
                &render_pin(b"valid prior render", RetainedFileFormat::Mp4),
                |_| Ok(u64::MAX),
                deadline(),
            )
            .unwrap();
        let mut calls = 0;
        assert!(store
            .accept_cue(
                &candidate,
                &path,
                &pin,
                |_| {
                    calls += 1;
                    Ok(if calls < 3 { u64::MAX } else { 0 })
                },
                deadline()
            )
            .is_err());
        assert_eq!(calls, 3);
        assert!(!store
            .objects
            .root
            .join(format!("{}.wav", &pin.sha256[7..]))
            .exists());
        assert_eq!(fs::read(&prior.path).unwrap(), b"valid prior render");
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert_eq!(store.used_bytes(deadline()).unwrap(), prior.pin.size_bytes);
    }

    fn pin(bytes: &[u8], frame_count: u64, channels: u16) -> PcmPin {
        PcmPin {
            sha256: format!("sha256:{:x}", Sha256::digest(bytes)),
            size_bytes: bytes.len() as u64,
            sample_rate: 48000,
            channels,
            frame_count,
        }
    }

    fn budget(bytes: u64) -> MixStorageBudget {
        MixStorageBudget {
            maximum_bytes: bytes,
            minimum_free_bytes: 100,
        }
    }

    fn deadline() -> Instant {
        Instant::now() + std::time::Duration::from_secs(60)
    }

    fn render_pin(raw: &[u8], format: RetainedFileFormat) -> RetainedFilePin {
        RetainedFilePin {
            sha256: format!("sha256:{:x}", Sha256::digest(raw)),
            size_bytes: raw.len() as u64,
            format,
        }
    }
    #[test]
    fn retained_json_record_uses_media_namespace_quota_exact_bytes_and_sealed_reuse() {
        let (fixture, _, _) = fixture();
        let raw = "{\"wide\":9007199254740993,\"version\":1}\n";
        let store =
            RetainedMixStore::new(&fixture.0, "metadata-record", budget(raw.len() as u64)).unwrap();
        let kept = store
            .accept_json_record(raw, |_| Ok(u64::MAX), deadline())
            .unwrap();
        assert_eq!(kept.pin.format, RetainedFileFormat::Json);
        assert_eq!(store.used_bytes(deadline()).unwrap(), raw.len() as u64);
        assert_eq!(store.read_json_record(&kept, deadline()).unwrap(), raw);
        assert!(fs::metadata(&kept.path).unwrap().permissions().readonly());
        let mtime = fs::metadata(&kept.path).unwrap().modified().unwrap();
        assert_eq!(
            store
                .accept_json_record(
                    raw,
                    |_| panic!("sealed metadata reuse allocates no copy/reserve"),
                    deadline()
                )
                .unwrap(),
            kept
        );
        assert_eq!(fs::metadata(&kept.path).unwrap().modified().unwrap(), mtime);
        drop(store);
        let reopened = RetainedMixStore::open_existing(
            &fixture.0,
            "metadata-record",
            budget(raw.len() as u64),
        )
        .unwrap();
        assert_eq!(reopened.read_json_record(&kept, deadline()).unwrap(), raw);
        assert_eq!(fs::metadata(&kept.path).unwrap().modified().unwrap(), mtime);
    }

    #[test]
    fn retained_json_record_counts_media_orphans_and_other_jobs_under_the_existing_writer() {
        let (fixture, _, candidate) = fixture();
        let bytes = b"physical media fixture";
        let path = candidate.join("part.mp4");
        fs::write(&path, bytes).unwrap();
        let pin = render_pin(bytes, RetainedFileFormat::Mp4);
        let raw = "{\"checkpoint\":1}";
        let store = RetainedMixStore::new(
            &fixture.0,
            "metadata-old",
            budget(pin.size_bytes + raw.len() as u64 + 1),
        )
        .unwrap();
        let video = store
            .accept_file(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let kept = store
            .accept_json_record(raw, |_| Ok(u64::MAX), deadline())
            .unwrap();
        fs::write(store.objects.root.join(".copy-interrupted-metadata"), [1u8]).unwrap();
        assert_eq!(
            store.used_bytes(deadline()).unwrap(),
            pin.size_bytes + raw.len() as u64 + 1
        );
        assert!(RetainedMixStore::new(&fixture.0, "metadata-parallel", budget(u64::MAX)).is_err());
        let old = fs::read(&kept.path).unwrap();
        drop(store);
        let another = RetainedMixStore::new(
            &fixture.0,
            "metadata-next",
            budget(pin.size_bytes + raw.len() as u64 + 1),
        )
        .unwrap();
        assert!(another
            .accept_json_record(
                "{\"new\":true}",
                |_| panic!("shared quota refuses before space probe"),
                deadline()
            )
            .is_err());
        assert_eq!(fs::read(&kept.path).unwrap(), old);
        assert_eq!(fs::read(&video.path).unwrap(), bytes);
    }

    #[test]
    fn retained_json_record_refuses_unknown_changing_space_deadline_and_invalid_or_oversized_json()
    {
        let (fixture, _, _) = fixture();
        let store = RetainedMixStore::new(&fixture.0, "metadata-space", budget(200000)).unwrap();
        let prior = store
            .accept_json_record("{\"prior\":true}", |_| Ok(u64::MAX), deadline())
            .unwrap();
        let before = fs::read(&prior.path).unwrap();
        let mtime = fs::metadata(&prior.path).unwrap().modified().unwrap();
        let raw = format!("{{\"payload\":\"{}\"}}", "x".repeat(40000));
        for fail_at in [1, 3, 4] {
            let mut calls = 0;
            assert!(store
                .accept_json_record(
                    &raw,
                    |_| {
                        calls += 1;
                        Ok(if calls == fail_at { 0 } else { u64::MAX })
                    },
                    deadline()
                )
                .is_err());
            assert_eq!(store.used_bytes(deadline()).unwrap(), prior.pin.size_bytes);
        }
        assert!(store
            .accept_json_record(
                &raw,
                |_| Err(invalid("unknown native caller space")),
                deadline()
            )
            .is_err());
        assert!(store
            .accept_json_record(
                &raw,
                |_| panic!("expired operation must not probe space"),
                Instant::now() - std::time::Duration::from_secs(1)
            )
            .is_err());
        for invalid_raw in [
            "",
            "{",
            "{\"duplicate\":1,\"duplicate\":2}",
            "{\"bad\":NaN}",
        ] {
            assert!(store
                .accept_json_record(
                    invalid_raw,
                    |_| panic!("syntax refuses before allocation"),
                    deadline()
                )
                .is_err());
        }
        let large = format!(
            "{{\"payload\":\"{}\"}}",
            "x".repeat(MAX_RETAINED_METADATA_BYTES)
        );
        assert!(store
            .accept_json_record(
                &large,
                |_| panic!("record bound refuses before allocation"),
                deadline()
            )
            .is_err());
        assert_eq!(fs::read(&prior.path).unwrap(), before);
        assert_eq!(
            fs::metadata(&prior.path).unwrap().modified().unwrap(),
            mtime
        );
    }

    #[test]
    fn retained_json_record_refuses_changed_unsealed_linked_and_foreign_format_without_repair() {
        let (fixture, _, _) = fixture();
        let store = RetainedMixStore::new(&fixture.0, "metadata-fences", budget(10000)).unwrap();
        let raw = "{\"node\":1}";
        let kept = store
            .accept_json_record(raw, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let mut foreign = kept.clone();
        foreign.pin.format = RetainedFileFormat::Srt;
        assert!(store.read_json_record(&foreign, deadline()).is_err());
        let alias = fixture.0.join("owned-hardlink-metadata.json");
        fs::hard_link(&kept.path, &alias).unwrap();
        assert!(store.read_json_record(&kept, deadline()).is_err());
        let mut permissions = fs::metadata(&kept.path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&kept.path, permissions).unwrap();
        fs::remove_file(alias).unwrap();
        assert!(store
            .accept_json_record(
                raw,
                |_| panic!("unsealed collision never allocates"),
                deadline()
            )
            .is_err());
        fs::write(&kept.path, b"{\"node\":2}").unwrap();
        let mut permissions = fs::metadata(&kept.path).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&kept.path, permissions).unwrap();
        let corrupted = fs::read(&kept.path).unwrap();
        assert!(store
            .accept_json_record(
                raw,
                |_| panic!("corrupt collision never overwrites"),
                deadline()
            )
            .is_err());
        assert_eq!(fs::read(&kept.path).unwrap(), corrupted);
    }

    #[test]
    fn retained_render_streams_beyond_cue_limit_and_reopens_without_mutable_candidate() {
        let (fixture, _, candidate) = fixture();
        let path = candidate.join("final.mp4");
        // Physical hash/copy fixture, not a valid MP4 or long-video qualification.
        let size = MAX_CUE_BYTES + BUFFER_BYTES as u64;
        let mut file = File::create(&path).unwrap();
        let mut sha = Sha256::new();
        let block = [31u8; BUFFER_BYTES];
        for _ in 0..size / BUFFER_BYTES as u64 {
            file.write_all(&block).unwrap();
            sha.update(block);
        }
        file.sync_all().unwrap();
        drop(file);
        let pin = RetainedFilePin {
            sha256: format!("sha256:{:x}", sha.finalize()),
            size_bytes: size,
            format: RetainedFileFormat::Mp4,
        };
        let store = RetainedMixStore::new(&fixture.0, "render-reopen", budget(size)).unwrap();
        let object = store
            .accept_file(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let mtime = fs::metadata(&object.path).unwrap().modified().unwrap();
        assert_eq!(store.used_bytes(deadline()).unwrap(), size);
        assert!(fs::metadata(&object.path).unwrap().permissions().readonly());
        assert_ne!(path, object.path);
        fs::write(&path, b"changed candidate").unwrap();
        assert_eq!(
            store
                .accept_file(
                    &candidate,
                    &path,
                    &pin,
                    |_| panic!("sealed reuse allocates no bytes"),
                    deadline()
                )
                .unwrap(),
            object
        );
        fs::remove_file(&path).unwrap();
        drop(store);
        let reopened =
            RetainedMixStore::open_existing(&fixture.0, "render-reopen", budget(size)).unwrap();
        reopened.verify_file(&object, deadline()).unwrap();
        assert_eq!(
            fs::metadata(&object.path).unwrap().modified().unwrap(),
            mtime
        );
    }
    #[test]
    fn render_pcm_and_orphan_bytes_share_one_locked_aggregate_quota() {
        let (fixture, _, candidate) = fixture();
        let (wav, pcm) = wave(4, 2, false);
        let path = candidate.join("original.wav");
        fs::write(&path, &wav).unwrap();
        let video = candidate.join("final.mp4");
        fs::write(&video, b"render candidate").unwrap();
        let pin = render_pin(b"render candidate", RetainedFileFormat::Mp4);
        let store = RetainedMixStore::new(
            &fixture.0,
            "render-quota",
            budget(pcm.size_bytes + pin.size_bytes),
        )
        .unwrap();
        let old = store
            .accept(&candidate, &path, &pcm, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let orphan = store.objects.root.join(".copy-interrupted");
        fs::write(&orphan, b"orphan").unwrap();
        assert!(store
            .accept_file(
                &candidate,
                &video,
                &pin,
                |_| panic!("quota refuses before OS space"),
                deadline()
            )
            .is_err());
        store.verify(&old, deadline()).unwrap();
        assert_eq!(fs::read(&orphan).unwrap(), b"orphan");
        assert!(RetainedMixStore::new(&fixture.0, "other-render", budget(u64::MAX)).is_err());
    }
    #[test]
    fn space_changes_during_render_copy_refuse_before_sealing() {
        let (fixture, _, candidate) = fixture();
        let path = candidate.join("final.mp4");
        let block = [7u8; BUFFER_BYTES];
        let mut sha = Sha256::new();
        let mut file = File::create(&path).unwrap();
        for _ in 0..256 {
            file.write_all(&block).unwrap();
            sha.update(block);
        }
        file.sync_all().unwrap();
        drop(file);
        let pin = RetainedFilePin {
            sha256: format!("sha256:{:x}", sha.finalize()),
            size_bytes: 8 * 1024 * 1024,
            format: RetainedFileFormat::Mp4,
        };
        let store =
            RetainedMixStore::new(&fixture.0, "render-space", budget(pin.size_bytes)).unwrap();
        let mut probes = 0;
        assert!(store
            .accept_file(
                &candidate,
                &path,
                &pin,
                |_| {
                    probes += 1;
                    Ok(if probes < 3 { u64::MAX } else { 0 })
                },
                deadline()
            )
            .is_err());
        assert_eq!(probes, 3);
        assert_eq!(store.used_bytes(deadline()).unwrap(), 0);
        assert_eq!(fs::metadata(&path).unwrap().len(), pin.size_bytes);
    }
    #[test]
    fn retained_render_refuses_unsealed_corrupt_substituted_or_expired_objects() {
        let (fixture, _, candidate) = fixture();
        let path = candidate.join("timeline.json");
        fs::write(&path, b"{\"timeline\":1}").unwrap();
        let pin = render_pin(b"{\"timeline\":1}", RetainedFileFormat::Json);
        let store = RetainedMixStore::new(&fixture.0, "render-corrupt", budget(1000)).unwrap();
        assert!(store
            .accept_file(
                &candidate,
                &path,
                &pin,
                |_| Err(invalid("unknown space")),
                deadline()
            )
            .is_err());
        assert!(store
            .accept_file(&candidate, &path, &pin, |_| Ok(u64::MAX), Instant::now())
            .is_err());
        let object = store
            .accept_file(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let mut substituted = object.clone();
        substituted.pin.format = RetainedFileFormat::Ass;
        assert!(store.verify_file(&substituted, deadline()).is_err());
        let mut permissions = fs::metadata(&object.path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&object.path, permissions).unwrap();
        assert!(store.verify_file(&object, deadline()).is_err());
        fs::write(&object.path, b"{\"timeline\":2}").unwrap();
        let mut permissions = fs::metadata(&object.path).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&object.path, permissions).unwrap();
        assert!(store.verify_file(&object, deadline()).is_err());
        assert!(store
            .accept_file(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert_eq!(fs::read(&object.path).unwrap(), b"{\"timeline\":2}");
    }

    #[test]
    fn retained_mix_exceeds_cue_limit_with_bounded_copy_and_separate_sealed_bytes() {
        let (fixture, cue_store, candidate) = fixture();
        let path = candidate.join("long mix.wav");
        // Physical >64MiB media exercises the old cue boundary, not just a
        // fabricated large pin. Generate/hash it in bounded chunks, not a Vec.
        let frames = MAX_CUE_BYTES / 4 + 1024;
        let payload = frames * 4;
        let (mut header, _) = wave(1, 2, false);
        header.truncate(44);
        header[4..8].copy_from_slice(&((payload + 36) as u32).to_le_bytes());
        header[40..44].copy_from_slice(&(payload as u32).to_le_bytes());
        let mut file = File::create(&path).unwrap();
        let mut hash = Sha256::new();
        file.write_all(&header).unwrap();
        hash.update(&header);
        let buffer = [17u8; BUFFER_BYTES];
        let mut left = payload;
        while left > 0 {
            let count = left.min(BUFFER_BYTES as u64) as usize;
            file.write_all(&buffer[..count]).unwrap();
            hash.update(&buffer[..count]);
            left -= count as u64;
        }
        file.sync_all().unwrap();
        drop(file);
        let pin = PcmPin {
            sha256: format!("sha256:{:x}", hash.finalize()),
            size_bytes: payload + 44,
            sample_rate: 48000,
            channels: 2,
            frame_count: frames,
        };
        assert!(cue_store.accept(&candidate, &path, &pin).is_err());
        let store = RetainedMixStore::new(&fixture.0, "job:long", budget(pin.size_bytes)).unwrap();
        let accepted = store
            .accept(&candidate, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        assert!(accepted
            .path
            .starts_with(fixture.0.join(".dubflow-review-media-v1")));
        assert!(!accepted.path.starts_with(fixture.0.join("control")));
        assert_ne!(accepted.path, path);
        assert!(fs::metadata(&accepted.path)
            .unwrap()
            .permissions()
            .readonly());
        assert!(!fs::metadata(&path).unwrap().permissions().readonly());
        assert_eq!(store.used_bytes(deadline()).unwrap(), pin.size_bytes);
        fs::write(&path, b"worker replaced candidate").unwrap();
        store.verify(&accepted, deadline()).unwrap();
    }

    #[test]
    fn retained_reuse_at_quota_needs_no_copy_or_space_probe_and_keeps_mtime() {
        let (fixture, _, root) = fixture();
        let (bytes, pin) = wave(480, 2, true);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-reuse", budget(pin.size_bytes)).unwrap();
        let first = store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let mtime = fs::metadata(&first.path).unwrap().modified().unwrap();
        assert_eq!(
            store
                .accept(
                    &root,
                    &path,
                    &pin,
                    |_| panic!("reuse must not reserve new bytes"),
                    deadline()
                )
                .unwrap(),
            first
        );
        assert_eq!(
            fs::metadata(&first.path).unwrap().modified().unwrap(),
            mtime
        );
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 1);
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }

    #[test]
    fn quota_includes_other_jobs_pending_undo_and_crash_orphans_without_eviction() {
        let (fixture, _, root) = fixture();
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store =
            RetainedMixStore::new(&fixture.0, "old-job", budget(pin.size_bytes + 10)).unwrap();
        let old = store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let orphan = store.objects.root.join(".copy-interrupted");
        fs::write(&orphan, b"ten bytes!").unwrap();
        assert_eq!(store.used_bytes(deadline()).unwrap(), pin.size_bytes + 10);
        drop(store);
        let next =
            RetainedMixStore::new(&fixture.0, "new-job", budget(pin.size_bytes + 10)).unwrap();
        assert!(next
            .accept(
                &root,
                &path,
                &pin,
                |_| panic!("quota must refuse before space probe"),
                deadline()
            )
            .is_err());
        assert_eq!(fs::read(&old.path).unwrap(), bytes);
        assert_eq!(fs::read(&orphan).unwrap(), b"ten bytes!");
        assert_eq!(fs::read_dir(&next.objects.root).unwrap().count(), 0);
    }

    #[test]
    fn low_unknown_space_overflow_and_expired_deadline_never_publish_or_fall_back() {
        let (fixture, cue_store, root) = fixture();
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-space", budget(pin.size_bytes)).unwrap();
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(pin.size_bytes + 99), deadline())
            .is_err());
        assert!(store
            .accept(
                &root,
                &path,
                &pin,
                |_| Err(invalid("space unavailable")),
                deadline()
            )
            .is_err());
        assert!(store
            .accept(&root, &path, &pin, |_| panic!("expired"), Instant::now())
            .is_err());
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 0);
        assert_eq!(fs::read_dir(&cue_store.root).unwrap().count(), 0);
        drop(store);
        let store = RetainedMixStore::new(
            &fixture.0,
            "job-space",
            MixStorageBudget {
                maximum_bytes: u64::MAX,
                minimum_free_bytes: u64::MAX,
            },
        )
        .unwrap();
        assert!(store
            .accept(&root, &path, &pin, |_| panic!("overflow"), deadline())
            .is_err());
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 0);
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }

    #[test]
    fn exclusive_output_writer_covers_distinct_jobs_and_is_released_without_unlink() {
        let (fixture, _, _) = fixture();
        let store = RetainedMixStore::new(&fixture.0, "job-a", budget(10000)).unwrap();
        assert!(RetainedMixStore::new(&fixture.0, "job-b", budget(10000)).is_err());
        let lock = store.namespace.join(".writer.lock");
        drop(store);
        assert!(lock.is_file());
        let next = RetainedMixStore::new(&fixture.0, "job-b", budget(10000)).unwrap();
        assert_eq!(next.namespace.join(".writer.lock"), lock);
        assert_eq!(fs::metadata(lock).unwrap().len(), 0);
    }

    #[test]
    fn bounded_output_writer_wait_acquires_after_other_job_releases_same_lock() {
        let (fixture, _, _) = fixture();
        let held = RetainedMixStore::new(&fixture.0, "job-held", budget(10000)).unwrap();
        let lock = held.namespace.join(".writer.lock");
        let identity = single_cue_file(&regular(&lock).unwrap()).unwrap();
        let (release, receive) = std::sync::mpsc::sync_channel(1);
        std::thread::scope(|scope| {
            scope.spawn(move || {
                receive
                    .recv_timeout(std::time::Duration::from_secs(3))
                    .unwrap();
                drop(held);
            });
            let mut polls = 0;
            let next = RetainedMixStore::new_until(
                &fixture.0,
                "job-waiting",
                budget(10000),
                Instant::now() + std::time::Duration::from_secs(2),
                &mut || {
                    polls += 1;
                    if polls == 4 {
                        release.send(()).unwrap();
                    }
                    false
                },
            )
            .unwrap();
            assert!(polls >= 4);
            assert_eq!(single_cue_file(&regular(&lock).unwrap()).unwrap(), identity);
            assert!(RetainedMixStore::new(&fixture.0, "job-third", budget(10000)).is_err());
            assert!(next.objects.root.is_dir());
        });
        assert_eq!(single_cue_file(&regular(&lock).unwrap()).unwrap(), identity);
        assert_eq!(fs::metadata(lock).unwrap().len(), 0);
    }

    #[test]
    fn bounded_output_writer_wait_refuses_cancel_expiry_and_preserves_current_owner() {
        let (fixture, _, _) = fixture();
        let namespace = fixture.0.join(".dubflow-review-media-v1");
        assert!(RetainedMixStore::new_until(
            &fixture.0,
            "job-cancelled",
            budget(10000),
            deadline(),
            &mut || true,
        )
        .is_err());
        assert!(!namespace.exists());
        assert!(RetainedMixStore::new_until(
            &fixture.0,
            "job-expired",
            budget(10000),
            Instant::now(),
            &mut || false,
        )
        .is_err());
        assert!(!namespace.exists());
        let owner = RetainedMixStore::new(&fixture.0, "job-owner", budget(10000)).unwrap();
        let lock = namespace.join(".writer.lock");
        let identity = single_cue_file(&regular(&lock).unwrap()).unwrap();
        let started = Instant::now();
        assert!(RetainedMixStore::new_until(
            &fixture.0,
            "job-deadline",
            budget(10000),
            started + std::time::Duration::from_millis(40),
            &mut || false,
        )
        .is_err());
        assert!(started.elapsed() < std::time::Duration::from_secs(2));
        let mut polls = 0;
        assert!(RetainedMixStore::new_until(
            &fixture.0,
            "job-late-cancel",
            budget(10000),
            deadline(),
            &mut || {
                polls += 1;
                polls >= 4
            },
        )
        .is_err());
        assert!(RetainedMixStore::new(&fixture.0, "job-other", budget(10000)).is_err());
        assert_eq!(single_cue_file(&regular(&lock).unwrap()).unwrap(), identity);
        assert_eq!(fs::metadata(lock).unwrap().len(), 0);
        assert_eq!(fs::read_dir(namespace).unwrap().count(), 2);
        drop(owner);
    }

    #[cfg(unix)]
    #[test]
    fn bounded_output_writer_wait_never_adopts_a_replacement_lock_path() {
        let (fixture, _, _) = fixture();
        let mut owner =
            Some(RetainedMixStore::new(&fixture.0, "job-owner", budget(10000)).unwrap());
        let namespace = owner.as_ref().unwrap().namespace.clone();
        let lock = namespace.join(".writer.lock");
        let held = namespace.join("held-test-writer-lock");
        let mut polls = 0;
        assert!(
            mix_writer_lock_until(&namespace, true, deadline(), &mut || {
                polls += 1;
                if polls == 4 {
                    fs::rename(&lock, &held).unwrap();
                    File::create(&lock).unwrap();
                    drop(owner.take());
                }
                false
            })
            .is_err()
        );
        assert_eq!(fs::metadata(lock).unwrap().len(), 0);
        assert_eq!(fs::metadata(held).unwrap().len(), 0);
    }

    #[test]
    fn reopen_revalidates_media_without_recreating_missing_job_or_lock() {
        let (fixture, _, root) = fixture();
        let namespace = fixture.0.join(".dubflow-review-media-v1");
        assert!(RetainedMixStore::open_existing(&fixture.0, "job-open", budget(10000)).is_err());
        assert!(!namespace.exists());
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-open", budget(10000)).unwrap();
        let accepted = store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        drop(store);
        let opened =
            RetainedMixStore::open_existing(&fixture.0, "job-open", budget(10000)).unwrap();
        opened.verify(&accepted, deadline()).unwrap();
        drop(opened);
        let foreign_job = namespace.join(format!("{:x}", Sha256::digest(b"missing-job")));
        assert!(RetainedMixStore::open_existing(&fixture.0, "missing-job", budget(10000)).is_err());
        assert!(!foreign_job.exists());
        fs::remove_file(namespace.join(".writer.lock")).unwrap();
        assert!(RetainedMixStore::open_existing(&fixture.0, "job-open", budget(10000)).is_err());
        assert!(!namespace.join(".writer.lock").exists());
        assert_eq!(fs::read(&accepted.path).unwrap(), bytes);
    }

    #[test]
    fn excessive_inventory_and_nested_orphan_refuse_without_discarding_files() {
        let (fixture, _, root) = fixture();
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let mut store =
            RetainedMixStore::new(&fixture.0, "job-inventory", budget(u64::MAX)).unwrap();
        // Exercise the same filesystem walk with a small per-fixture bound;
        // creating the production65536 files is not necessary for this refusal.
        let inventory_limit = 32;
        store.inventory_limit = inventory_limit;
        let nested = store.objects.root.join("interrupted-generation");
        fs::create_dir(&nested).unwrap();
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert!(nested.is_dir());
        fs::remove_dir(nested).unwrap();
        for index in 0..inventory_limit {
            File::create(store.objects.root.join(format!(".copy-{index}"))).unwrap();
        }
        assert!(store
            .accept(
                &root,
                &path,
                &pin,
                |_| panic!("bounded scan must refuse"),
                deadline()
            )
            .is_err());
        assert_eq!(
            fs::read_dir(&store.objects.root).unwrap().count(),
            inventory_limit
        );
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }

    #[test]
    fn retained_mix_refuses_corruption_unsealed_objects_bad_pcm_and_foreign_identity() {
        let (fixture, _, root) = fixture();
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-tamper", budget(10000)).unwrap();
        let accepted = store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .unwrap();
        let mut changed = accepted.clone();
        changed.path = path.clone();
        assert!(store.verify(&changed, deadline()).is_err());
        changed = accepted.clone();
        changed.pin.frame_count -= 1;
        assert!(store.verify(&changed, deadline()).is_err());
        let mut permissions = fs::metadata(&accepted.path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(&accepted.path, permissions.clone()).unwrap();
        assert!(store.verify(&accepted, deadline()).is_err());
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        let mut corrupt = bytes.clone();
        *corrupt.last_mut().unwrap() ^= 1;
        fs::write(&accepted.path, &corrupt).unwrap();
        permissions.set_readonly(true);
        fs::set_permissions(&accepted.path, permissions).unwrap();
        assert!(store.verify(&accepted, deadline()).is_err());
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert_eq!(fs::read(&accepted.path).unwrap(), corrupt);
        assert_eq!(fs::read(&path).unwrap(), bytes);
        // A checksum-valid non-PCM candidate still cannot create an object.
        let mut bad = bytes;
        bad[20] = 3;
        let bad_pin = self::pin(&bad, 480, 2);
        fs::write(&path, &bad).unwrap();
        assert!(store
            .accept(&root, &path, &bad_pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 1);
        assert_eq!(fs::read(&path).unwrap(), bad);
    }

    #[test]
    fn missing_or_traversed_output_is_not_created_and_foreign_candidates_refuse() {
        let (fixture, _, root) = fixture();
        let absent = fixture.0.join("offline output");
        assert!(RetainedMixStore::new(&absent, "job-offline", budget(10000)).is_err());
        assert!(!absent.exists());
        let traversed = root.join("../producer candidate");
        assert!(RetainedMixStore::new(&traversed, "job-path", budget(10000)).is_err());
        assert!(RetainedMixStore::new(&fixture.0, "job-zero", budget(0)).is_err());
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-path", budget(10000)).unwrap();
        assert!(store
            .accept(
                &root,
                &root.join("../producer candidate/mix.wav"),
                &pin,
                |_| Ok(u64::MAX),
                deadline()
            )
            .is_err());
        assert!(store
            .accept(
                &root,
                &fixture.0.join("outside.wav"),
                &pin,
                |_| Ok(u64::MAX),
                deadline()
            )
            .is_err());
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 0);
        let unexpected = store.namespace.join("unknown");
        fs::write(&unexpected, b"preserve").unwrap();
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert_eq!(fs::read(&unexpected).unwrap(), b"preserve");
    }

    #[cfg(unix)]
    #[test]
    fn linked_output_candidate_or_orphan_is_never_admitted_as_retained_media() {
        use std::os::unix::fs::symlink;
        let (fixture, _, root) = fixture();
        let alias = fixture.0.join("linked output");
        symlink(&root, &alias).unwrap();
        assert!(RetainedMixStore::new(&alias, "job-links", budget(10000)).is_err());
        let (bytes, pin) = wave(480, 2, false);
        let path = root.join("mix.wav");
        fs::write(&path, &bytes).unwrap();
        let store = RetainedMixStore::new(&fixture.0, "job-links", budget(10000)).unwrap();
        let linked = root.join("linked.wav");
        symlink(&path, &linked).unwrap();
        assert!(store
            .accept(&root, &linked, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        let orphan = store.objects.root.join(".copy-linked");
        symlink(&path, &orphan).unwrap();
        assert!(store
            .accept(&root, &path, &pin, |_| Ok(u64::MAX), deadline())
            .is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        fs::remove_file(orphan).unwrap();
    }

    #[test]
    fn json_documents_preserve_exact_bytes_wide_numbers_and_reuse_sealed_objects() {
        let (_fixture, store, _) = fixture();
        let raw = "{\n  \"revision\":9007199254740994,\"text\":\"Ngồi yên.\"\n}\n";
        let first = store.accept_json(raw).unwrap();
        assert_eq!(first.size_bytes, raw.len() as u64);
        assert_eq!(
            first.sha256,
            format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
        );
        assert_eq!(store.read_json(&first).unwrap(), raw);
        assert_eq!(store.accept_json(raw).unwrap(), first);
        assert!(fs::metadata(&first.path).unwrap().permissions().readonly());
        // Equivalent JSON with different bytes has a distinct producer hash.
        let compact = "{\"revision\":9007199254740994,\"text\":\"Ngồi yên.\"}";
        let second = store.accept_json(compact).unwrap();
        assert_ne!(first.path, second.path);
        assert_eq!(store.read_json(&second).unwrap(), compact);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 2);
    }

    #[test]
    fn corrupt_or_unsealed_json_is_retained_and_never_silently_repaired() {
        let (_fixture, store, _) = fixture();
        let raw = r#"{"cue":"original"}"#;
        let accepted = store.accept_json(raw).unwrap();
        let mut permissions = fs::metadata(&accepted.path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(&accepted.path, permissions.clone()).unwrap();
        assert!(store.read_json(&accepted).is_err());
        assert!(store.accept_json(raw).is_err());
        assert_eq!(fs::read_to_string(&accepted.path).unwrap(), raw);
        let corrupt = r#"{"cue":"tampered"}"#;
        assert_eq!(corrupt.len(), raw.len());
        fs::write(&accepted.path, corrupt).unwrap();
        permissions.set_readonly(true);
        fs::set_permissions(&accepted.path, permissions).unwrap();
        assert!(store.read_json(&accepted).is_err());
        assert!(store.accept_json(raw).is_err());
        assert_eq!(fs::read_to_string(&accepted.path).unwrap(), corrupt);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 1);
    }

    #[test]
    fn ambiguous_or_oversized_json_refuses_before_publication() {
        let (_fixture, store, _) = fixture();
        for raw in [
            "",
            "[]",
            "null",
            "{",
            "{} {}",
            r#"{"a":1,"a":2}"#,
            r#"{"nested":{"a":1,"a":2}}"#,
        ] {
            assert!(store.accept_json(raw).is_err());
        }
        let oversized = format!(
            "{{\"payload\":\"{}\"}}",
            "x".repeat(MAX_DOCUMENT_BYTES as usize)
        );
        assert!(store.accept_json(&oversized).is_err());
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
    }

    #[test]
    fn json_pin_substitution_and_destination_collision_refuse() {
        let (_fixture, store, _) = fixture();
        let raw = r#"{"cue":"approved"}"#;
        let accepted = store.accept_json(raw).unwrap();
        for field in 0..4 {
            let mut changed = accepted.clone();
            match field {
                0 => changed.sha256 = "sha256:invalid".into(),
                1 => changed.size_bytes += 1,
                2 => changed.path = changed.path.with_extension("wav"),
                _ => changed.size_bytes = MAX_DOCUMENT_BYTES + 1,
            }
            assert!(store.read_json(&changed).is_err());
        }
        let other = r#"{"cue":"other"}"#;
        let destination = store
            .root
            .join(format!("{:x}.json", Sha256::digest(other.as_bytes())));
        fs::create_dir(&destination).unwrap();
        assert!(store.accept_json(other).is_err());
        assert!(destination.is_dir());
        assert_eq!(store.read_json(&accepted).unwrap(), raw);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 2);
    }

    #[test]
    fn streamed_copy_is_separate_sealed_and_survives_candidate_changes() {
        let (_fixture, store, root) = fixture();
        let source = root.join("one cue.wav");
        let (bytes, pin) = wave(200000, 2, true);
        fs::write(&source, &bytes).unwrap();
        let accepted = store.accept(&root, &source, &pin).unwrap();
        assert_ne!(accepted.path, source);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert_eq!(fs::read(&accepted.path).unwrap(), bytes);
        assert!(fs::metadata(&accepted.path)
            .unwrap()
            .permissions()
            .readonly());
        fs::write(&source, vec![0; bytes.len()]).unwrap();
        store.verify(&accepted).unwrap();
        assert_eq!(fs::read(&accepted.path).unwrap(), bytes);
        assert!(store.accept(&root, &source, &pin).is_err());
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 1);
    }

    #[test]
    fn exact_existing_object_reuses_bytes_and_never_replaces_corruption() {
        let (_fixture, store, root) = fixture();
        let source = root.join("cue.wav");
        let (bytes, pin) = wave(26, 1, false);
        fs::write(&source, &bytes).unwrap();
        let first = store.accept(&root, &source, &pin).unwrap();
        assert_eq!(store.accept(&root, &source, &pin).unwrap(), first);
        let mut permissions = fs::metadata(&first.path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(&first.path, permissions).unwrap();
        fs::write(&first.path, vec![1; bytes.len()]).unwrap();
        assert!(store.accept(&root, &source, &pin).is_err());
        assert_eq!(fs::read(&first.path).unwrap(), vec![1; bytes.len()]);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 1);
    }

    #[test]
    fn matching_checksums_do_not_admit_bad_pcm_headers_or_truncation() {
        let (_fixture, store, root) = fixture();
        let source = root.join("cue.wav");
        let (original, _) = wave(26, 1, false);
        let mut bad = Vec::new();
        for (offset, value) in [
            (20, 3),
            (22, 2),
            (24, 1),
            (28, 1),
            (32, 3),
            (34, 8),
            (40, 1),
        ] {
            let mut bytes = original.clone();
            bytes[offset] = value;
            bad.push(bytes);
        }
        let mut extra = original.clone();
        extra.push(0);
        bad.push(extra);
        let mut duplicate = original.clone();
        duplicate.extend_from_slice(&original[12..36]);
        let length = duplicate.len() as u32 - 8;
        duplicate[4..8].copy_from_slice(&length.to_le_bytes());
        bad.push(duplicate);
        for bytes in bad {
            fs::write(&source, &bytes).unwrap();
            let pin = pin(&bytes, 26, 1);
            assert!(store.accept(&root, &source, &pin).is_err());
            assert_eq!(fs::read(&source).unwrap(), bytes);
            assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
        }
    }

    #[test]
    fn pin_limits_and_outside_paths_refuse_before_copy() {
        let (_fixture, store, root) = fixture();
        let source = root.join("cue.wav");
        let (bytes, original) = wave(26, 1, false);
        fs::write(&source, bytes).unwrap();
        for field in 0..6 {
            let mut pin = original.clone();
            match field {
                0 => pin.sha256 = "sha256:invalid".into(),
                1 => pin.size_bytes = MAX_CUE_BYTES + 1,
                2 => pin.sample_rate = 0,
                3 => pin.channels = 3,
                4 => pin.frame_count = 0,
                _ => pin.frame_count = u64::MAX,
            }
            assert!(store.accept(&root, &source, &pin).is_err());
        }
        assert!(store
            .accept(&root.join("other"), &source, &original)
            .is_err());
        let traversal = root.join("../producer candidate/cue.wav");
        assert!(store.accept(&root, &traversal, &original).is_err());
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 0);
    }

    #[test]
    fn conflicting_destination_is_retained_without_touching_candidate() {
        let (_fixture, store, root) = fixture();
        let source = root.join("cue.wav");
        let (bytes, pin) = wave(26, 1, false);
        fs::write(&source, &bytes).unwrap();
        let destination = store.root.join(format!("{}.wav", &pin.sha256[7..]));
        fs::create_dir(&destination).unwrap();
        assert!(store.accept(&root, &source, &pin).is_err());
        assert!(destination.is_dir());
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert_eq!(fs::read_dir(&store.root).unwrap().count(), 1);
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn export_copy_resumes_verified_prefix_after_space_refusal_and_reuses_sealed_bytes() {
        let (fixture, _, candidate) = fixture();
        let store = RetainedMixStore::new(
            &fixture.0,
            "export-resume",
            MixStorageBudget {
                maximum_bytes: 16 * 1024 * 1024,
                minimum_free_bytes: 1024,
            },
        )
        .unwrap();
        let bytes: Vec<u8> = (0..6 * 1024 * 1024).map(|n| (n % 251) as u8).collect();
        let source = candidate.join("input.mp4");
        fs::write(&source, &bytes).unwrap();
        let source_mtime = fs::metadata(&source).unwrap().modified().unwrap();
        let generation = "a".repeat(64);
        let hash = format!("sha256:{:x}", Sha256::digest(&bytes));
        let input = ExportCopyInput {
            generation: &generation,
            name: "final_vi.mp4",
            source: &source,
            sha256: &hash,
            size_bytes: bytes.len() as u64,
            remaining_after: 0,
        };
        let deadline = Instant::now() + std::time::Duration::from_secs(20);
        let mut calls = 0;
        assert!(store
            .copy_export_file(
                &input,
                |_| {
                    calls += 1;
                    Ok(if calls < 3 { u64::MAX } else { 0 })
                },
                deadline
            )
            .is_err());
        let directory = store.export_pending_directory(&generation).unwrap();
        let partial = directory.join("final_vi.mp4.partial");
        assert_eq!(fs::metadata(&partial).unwrap().len(), 4 * 1024 * 1024);
        assert_eq!(fs::read(&partial).unwrap(), bytes[..4 * 1024 * 1024]);
        assert!(!directory.join("final_vi.mp4").exists());
        let path = store
            .copy_export_file(&input, |_| Ok(u64::MAX), deadline)
            .unwrap();
        assert!(!partial.exists());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert!(fs::metadata(&path).unwrap().permissions().readonly());
        let copied_mtime = fs::metadata(&path).unwrap().modified().unwrap();
        assert_eq!(
            store
                .copy_export_file(&input, |_| panic!("sealed reuse copies no bytes"), deadline)
                .unwrap(),
            path
        );
        assert_eq!(
            fs::metadata(&path).unwrap().modified().unwrap(),
            copied_mtime
        );
        assert_eq!(
            fs::metadata(&source).unwrap().modified().unwrap(),
            source_mtime
        );
        assert_eq!(fs::read(&source).unwrap(), bytes);
        // Only this test's newly created nested output needs writable teardown
        // on Windows. Historical/other-process temporary paths are untouched.
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(&path, permissions).unwrap();
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn export_copy_refuses_corrupted_resume_prefix_without_overwriting_it() {
        let (fixture, _, candidate) = fixture();
        let store = RetainedMixStore::new(
            &fixture.0,
            "export-corrupt",
            MixStorageBudget {
                maximum_bytes: 1024 * 1024,
                minimum_free_bytes: 1024,
            },
        )
        .unwrap();
        let bytes = vec![37u8; 32768];
        let source = candidate.join("input.mp4");
        fs::write(&source, &bytes).unwrap();
        let generation = "b".repeat(64);
        let directory = store.export_pending_directory(&generation).unwrap();
        let partial = directory.join("source.mp4.partial");
        let mut bad = bytes[..8192].to_vec();
        bad[511] ^= 1;
        fs::write(&partial, &bad).unwrap();
        let mtime = fs::metadata(&partial).unwrap().modified().unwrap();
        let hash = format!("sha256:{:x}", Sha256::digest(&bytes));
        let input = ExportCopyInput {
            generation: &generation,
            name: "source.mp4",
            source: &source,
            sha256: &hash,
            size_bytes: bytes.len() as u64,
            remaining_after: 0,
        };
        assert!(store
            .copy_export_file(
                &input,
                |_| Ok(u64::MAX),
                Instant::now() + std::time::Duration::from_secs(5)
            )
            .is_err());
        assert_eq!(fs::read(&partial).unwrap(), bad);
        assert_eq!(fs::metadata(&partial).unwrap().modified().unwrap(), mtime);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert!(!directory.join("source.mp4").exists());
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn export_copy_counts_other_job_published_partial_and_orphan_bytes_before_reserving() {
        let (fixture, _, candidate) = fixture();
        let store = RetainedMixStore::new(
            &fixture.0,
            "export-quota",
            MixStorageBudget {
                maximum_bytes: 1024,
                minimum_free_bytes: 1,
            },
        )
        .unwrap();
        let other = store.namespace.join("c".repeat(64));
        fs::create_dir(&other).unwrap();
        fs::write(other.join("old.wav"), vec![1u8; 200]).unwrap();
        let published = other.join(".exports-published-v1");
        fs::create_dir(&published).unwrap();
        let prior = published.join("d".repeat(64));
        fs::create_dir(&prior).unwrap();
        fs::write(prior.join("old.mp4"), vec![2u8; 300]).unwrap();
        let generation = "e".repeat(64);
        let pending = store.export_pending_directory(&generation).unwrap();
        fs::write(pending.join("crash-orphan.partial"), vec![3u8; 200]).unwrap();
        let deadline = Instant::now() + std::time::Duration::from_secs(5);
        assert_eq!(store.used_bytes(deadline).unwrap(), 700);
        let bytes = vec![4u8; 200];
        let source = candidate.join("source.mp4");
        fs::write(&source, &bytes).unwrap();
        let hash = format!("sha256:{:x}", Sha256::digest(&bytes));
        let input = ExportCopyInput {
            generation: &generation,
            name: "source.mp4",
            source: &source,
            sha256: &hash,
            size_bytes: 200,
            remaining_after: 125,
        };
        assert!(store
            .copy_export_file(
                &input,
                |_| panic!("quota refuses before probing OS space"),
                deadline
            )
            .is_err());
        assert_eq!(
            fs::metadata(pending.join("source.mp4.partial"))
                .unwrap()
                .len(),
            0
        );
        assert_eq!(store.used_bytes(deadline).unwrap(), 700);
        assert!(!pending.join("source.mp4").exists());
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn export_atomic_rename_preserves_an_existing_destination() {
        let (_fixture, _, candidate) = fixture();
        let source = candidate.join("sealed.partial");
        let target = candidate.join("already.mp4");
        fs::write(&source, b"new bytes").unwrap();
        fs::write(&target, b"old bytes").unwrap();
        assert!(export_copy::rename_new(&source, &target).is_err());
        assert_eq!(fs::read(&source).unwrap(), b"new bytes");
        assert_eq!(fs::read(&target).unwrap(), b"old bytes");
        let fresh = candidate.join("new.mp4");
        export_copy::rename_new(&source, &fresh).unwrap();
        assert!(!source.exists());
        assert_eq!(fs::read(fresh).unwrap(), b"new bytes");
    }

    #[cfg(windows)]
    #[test]
    fn export_raw_windows_rename_encodes_wide_absolute_paths_without_locator_rewrite() {
        let encoded = |path: &str| {
            let value = export_copy::windows_rename_path(Path::new(path)).unwrap();
            assert_eq!(value.last(), Some(&0));
            String::from_utf16(&value[..value.len() - 1]).unwrap()
        };
        assert_eq!(
            encoded("C:/owned/tiếng Việt.mp4"),
            r"\\?\C:\owned\tiếng Việt.mp4"
        );
        assert_eq!(encoded(r"\\?\D:\owned\file.mp4"), r"\\?\D:\owned\file.mp4");
        assert_eq!(
            encoded(r"\\server\share\owned\file.mp4"),
            r"\\?\UNC\server\share\owned\file.mp4"
        );
        assert_eq!(
            encoded(r"\\?\UNC\server\share\owned\file.mp4"),
            r"\\?\UNC\server\share\owned\file.mp4"
        );
        for bad in [
            "relative.mp4",
            r"C:relative.mp4",
            r"C:\owned\..\foreign.mp4",
            r"C:\owned\.\file.mp4",
            "C:\\owned\\bad\0.mp4",
            r"\\.\PhysicalDrive0",
        ] {
            assert!(export_copy::windows_rename_path(Path::new(bad)).is_err());
        }
        let oversized = format!(r"C:\{}\file.mp4", "x".repeat(32767));
        assert!(export_copy::windows_rename_path(Path::new(&oversized)).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn export_atomic_rename_keeps_deep_file_and_directory_paths_and_refuses_replacement() {
        use std::os::windows::ffi::OsStrExt;
        let (fixture, _, _) = fixture();
        let root = fixture
            .0
            .join("long native export rename")
            .join("a".repeat(90))
            .join("b".repeat(90));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("sealed.partial");
        let target = root.join("final_vi.mp4");
        assert!(source.as_os_str().encode_wide().count() > 260);
        fs::write(&source, b"new sealed bytes").unwrap();
        let mut permissions = fs::metadata(&source).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&source, permissions).unwrap();
        export_copy::rename_new(&source, &target).unwrap();
        assert!(!source.exists());
        assert_eq!(fs::read(&target).unwrap(), b"new sealed bytes");
        assert!(fs::metadata(&target).unwrap().permissions().readonly());
        let modified = fs::metadata(&target).unwrap().modified().unwrap();
        let conflict = root.join("conflict.partial");
        fs::write(&conflict, b"keep new bytes").unwrap();
        assert!(export_copy::rename_new(&conflict, &target).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"new sealed bytes");
        assert_eq!(fs::metadata(&target).unwrap().modified().unwrap(), modified);
        assert_eq!(fs::read(&conflict).unwrap(), b"keep new bytes");
        let pending = root.join("pending-directory");
        let published = root.join("published-directory");
        fs::create_dir(&pending).unwrap();
        fs::write(pending.join("retained.json"), b"directory bytes").unwrap();
        export_copy::rename_new(&pending, &published).unwrap();
        assert!(!pending.exists());
        assert_eq!(
            fs::read(published.join("retained.json")).unwrap(),
            b"directory bytes"
        );
        fs::create_dir(&pending).unwrap();
        fs::write(pending.join("other.json"), b"keep prior staging").unwrap();
        assert!(export_copy::rename_new(&pending, &published).is_err());
        assert_eq!(
            fs::read(pending.join("other.json")).unwrap(),
            b"keep prior staging"
        );
        assert_eq!(
            fs::read(published.join("retained.json")).unwrap(),
            b"directory bytes"
        );
        // Only this freshly created fixture's sealed file needs unsealing.
        let mut permissions = fs::metadata(&target).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&target, permissions).unwrap();
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn export_directory_fence_refusal_preserves_staging_and_restart_recovers_published_version() {
        let (fixture, _, candidate) = fixture();
        let store = RetainedMixStore::new(
            &fixture.0,
            "export-directory",
            MixStorageBudget {
                maximum_bytes: 1024 * 1024,
                minimum_free_bytes: 1024,
            },
        )
        .unwrap();
        let generation = "f".repeat(64);
        let deadline = Instant::now() + std::time::Duration::from_secs(10);
        let mut specs = Vec::new();
        // Byte-only filesystem fixture, with no encoded-media admission claim.
        for name in [
            "final_vi.mp4",
            "captions_vi.srt",
            "captions_vi.ass",
            "timeline.json",
            "qc_report.json",
            "source.mp4",
            "review_export_manifest.json",
        ] {
            let bytes = format!("owned copy fixture {name}").into_bytes();
            let source = candidate.join(name);
            fs::write(&source, &bytes).unwrap();
            let hash = format!("sha256:{:x}", Sha256::digest(&bytes));
            store
                .copy_export_file(
                    &ExportCopyInput {
                        generation: &generation,
                        name,
                        source: &source,
                        sha256: &hash,
                        size_bytes: bytes.len() as u64,
                        remaining_after: 0,
                    },
                    |_| Ok(u64::MAX),
                    deadline,
                )
                .unwrap();
            specs.push(ExportFileSpec {
                name: name.into(),
                sha256: hash,
                size_bytes: bytes.len() as u64,
            });
        }
        let pending = store.export_pending_directory(&generation).unwrap();
        assert_eq!(
            store
                .export_remaining_bytes(&generation, &specs, deadline)
                .unwrap(),
            vec![0; specs.len()]
        );
        let orphan = pending.join("unexpected.partial");
        fs::write(&orphan, b"interrupted").unwrap();
        assert!(store
            .publish_export_directory(
                &generation,
                &specs,
                |_| Ok(u64::MAX),
                || panic!("closed inventory refuses first"),
                deadline
            )
            .is_err());
        fs::remove_file(orphan).unwrap();
        assert!(store
            .publish_export_directory(
                &generation,
                &specs,
                |_| Ok(u64::MAX),
                || Err(invalid("changed full head")),
                deadline
            )
            .is_err());
        assert!(pending.is_dir());
        assert!(store
            .export_published_directory(&generation)
            .unwrap()
            .is_none());
        let published = store
            .publish_export_directory(&generation, &specs, |_| Ok(u64::MAX), || Ok(()), deadline)
            .unwrap();
        assert!(!pending.exists());
        verify_export_files(&published, &specs, deadline).unwrap();
        let before: Vec<_> = specs
            .iter()
            .map(|s| {
                fs::metadata(published.join(&s.name))
                    .unwrap()
                    .modified()
                    .unwrap()
            })
            .collect();
        // Simulates restart after a complete rename but before a response.
        assert_eq!(
            store
                .publish_export_directory(
                    &generation,
                    &specs,
                    |_| Ok(u64::MAX),
                    || Ok(()),
                    deadline
                )
                .unwrap(),
            published
        );
        for (file, mtime) in specs.iter().zip(before) {
            let path = published.join(&file.name);
            assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), mtime);
            assert_eq!(
                fs::read(&path).unwrap(),
                fs::read(candidate.join(&file.name)).unwrap()
            );
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            #[cfg(windows)]
            permissions.set_readonly(false);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                permissions.set_mode(0o600);
            }
            fs::set_permissions(path, permissions).unwrap();
        }
    }

    #[cfg(unix)]
    #[test]
    fn linked_candidate_or_accepted_ancestor_is_refused() {
        use std::os::unix::fs::symlink;
        let (_fixture, store, root) = fixture();
        let source = root.join("cue.wav");
        let (bytes, pin) = wave(26, 1, false);
        fs::write(&source, &bytes).unwrap();
        let link = root.join("linked.wav");
        symlink(&source, &link).unwrap();
        assert!(store.accept(&root, &link, &pin).is_err());
        fs::remove_dir(&store.root).unwrap();
        symlink(&root, &store.root).unwrap();
        assert!(store.accept(&root, &source, &pin).is_err());
        fs::remove_file(&store.root).unwrap();
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn fifo_candidate_refuses_with_a_bounded_observation() {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        use std::sync::mpsc;
        use std::time::Duration;
        unsafe extern "C" {
            fn mkfifo(path: *const std::ffi::c_char, mode: std::ffi::c_uint) -> std::ffi::c_int;
        }
        let (_fixture, store, root) = fixture();
        let source = root.join("unexpected producer pipe.wav");
        let name = CString::new(source.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { mkfifo(name.as_ptr(), 0o600) }, 0);
        let (_, pin) = wave(26, 1, false);
        let (send, receive) = mpsc::channel();
        let handle = std::thread::spawn(move || {
            let _ = send.send(store.accept(&root, &source, &pin).is_err());
        });
        assert_eq!(receive.recv_timeout(Duration::from_secs(2)).unwrap(), true);
        handle.join().unwrap();
    }
}
