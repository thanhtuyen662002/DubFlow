//! Private native object storage for a future semantic Review graph caller.
//!
//! This API takes supervisor-derived roots and already admitted cue pins, never
//! browser paths. It neither admits producer/model provenance nor writes SQLite.
//! Audio requires hash, size and PCM verification. JSON preserves exact bytes
//! and requires unambiguous syntax; document semantics need prior admission. A
//! later graph transaction must retain these objects and all pending undo assets.
use super::{review_pin::plain, SupervisorError, SupervisorResult, ID_COUNTER};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::Ordering;

const MAX_CUE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DOCUMENT_BYTES: u64 = 64 * 1024 * 1024;
const BUFFER_BYTES: usize = 32 * 1024;
const MAX_RIFF_CHUNKS: usize = 1024;

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
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct AcceptedCueAudio {
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

fn stream_pin(
    source: &mut File,
    mut target: Option<&mut File>,
    pin: &PcmPin,
) -> SupervisorResult<()> {
    source.seek(SeekFrom::Start(0))?;
    let mut digest = Sha256::new();
    let mut bytes = 0u64;
    let mut buffer = [0u8; BUFFER_BYTES];
    loop {
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
    if !path.is_absolute()
        || !root.is_absolute()
        || !path.starts_with(root)
        || !valid_hash(&pin.sha256)
        || pin.sample_rate != 48000
        || pin.channels != 2
        || !(1..=48000 * 6 * 3600).contains(&pin.frame_count)
        || !(44..=u64::from(u32::MAX) + 8).contains(&pin.size_bytes)
        || pin
            .frame_count
            .checked_mul(4)
            .filter(|n| *n <= pin.size_bytes - 44)
            .is_none()
    {
        return Err(invalid("invalid private six-hour stereo mix pin"));
    }
    let mut file = regular(path)?;
    let before = file.metadata()?;
    if before.len() != pin.size_bytes {
        return Err(invalid("private mix size changed"));
    }
    let mut digest = Sha256::new();
    let mut bytes = 0u64;
    let mut buffer = [0u8; BUFFER_BYTES];
    loop {
        if std::time::Instant::now() >= deadline {
            return Err(invalid(
                "private mix verification exceeded its shared deadline",
            ));
        }
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        bytes += count as u64;
        if bytes > pin.size_bytes {
            return Err(invalid("private mix grew beyond its pin"));
        }
        digest.update(&buffer[..count]);
    }
    if bytes != pin.size_bytes || format!("sha256:{:x}", digest.finalize()) != pin.sha256 {
        return Err(invalid("private mix checksum or size changed"));
    }
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

    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Windows needs readonly accepted files unsealed before fixture
            // deletion; production has no deletion/unseal operation.
            let assets = self.0.join("control/review-assets");
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

    fn pin(bytes: &[u8], frame_count: u64, channels: u16) -> PcmPin {
        PcmPin {
            sha256: format!("sha256:{:x}", Sha256::digest(bytes)),
            size_bytes: bytes.len() as u64,
            sample_rate: 48000,
            channels,
            frame_count,
        }
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
