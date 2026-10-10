//! Native source service. One OS owner, one active producer, one page in flight.
//! Owned SDK workers produce private page/media artifacts; the supervisor alone
//! commits producer-bound durable state.
use super::source_owner::{reject_links, SourceOwner};
use dubflow_source_queue::{
    ItemProgress, ItemStatus, PageCheckpoint, PageFailure, PageItem, QueueError, ScanRecord,
    ScanStatus, SourceQueue, SourceQueueItem,
};
use dubflow_worker_protocol::{
    Envelope, MessageType, Payload, ShutdownStatus, StreamValidator, MAX_LINE_BYTES,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread;
use std::time::{Duration, Instant};

const STAGE: &str = "source-enumeration";
const MAX_PACKET: u64 = 4 * 1024 * 1024;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn invalid() -> Box<dyn std::error::Error> {
    io::Error::new(io::ErrorKind::InvalidData, "source boundary rejected").into()
}
fn hex_digest(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}
fn digest_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn bounded_text(value: &str, limit: usize) -> bool {
    !value.is_empty() && value.len() <= limit && !value.chars().any(char::is_control)
}
fn public_url(value: &str) -> Result<()> {
    if !bounded_text(value, 4096) || value.contains('\\') || value.contains('#') {
        return Err(invalid());
    }
    let rest = value
        .strip_prefix("https://")
        .or_else(|| value.strip_prefix("http://"))
        .ok_or_else(invalid)?;
    let authority = rest.split(['/', '?']).next().ok_or_else(invalid)?;
    if authority.is_empty()
        || authority.contains('@')
        || authority.contains('%')
        || authority.chars().any(char::is_whitespace)
    {
        return Err(invalid());
    }
    if let Some((_, query)) = value.split_once('?') {
        for parameter in query.split('&') {
            let key = parameter.split('=').next().unwrap_or("");
            let mut decoded = Vec::new();
            let mut bytes = key.bytes();
            while let Some(byte) = bytes.next() {
                if byte == b'%' {
                    let a = bytes
                        .next()
                        .and_then(|b| (b as char).to_digit(16))
                        .ok_or_else(invalid)?;
                    let b = bytes
                        .next()
                        .and_then(|b| (b as char).to_digit(16))
                        .ok_or_else(invalid)?;
                    decoded.push((a * 16 + b) as u8);
                } else {
                    decoded.push(byte);
                }
            }
            let key = String::from_utf8(decoded)?
                .to_ascii_lowercase()
                .replace('-', "_");
            if [
                "token",
                "access_token",
                "auth",
                "authorization",
                "cookie",
                "password",
                "secret",
                "signature",
                "sig",
                "key",
                "api_key",
                "session",
                "sessionid",
                "credential",
                "credentials",
                "x_amz_credential",
                "x_amz_security_token",
                "x_amz_signature",
                "x_goog_credential",
                "x_goog_signature",
                "awsaccesskeyid",
                "client_secret",
                "oauth_token",
                "oauth_verifier",
            ]
            .contains(&key.as_str())
            {
                return Err(invalid());
            }
        }
    }
    Ok(())
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    reject_links(path)?;
    let file = File::open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() > maximum {
        return Err(invalid());
    }
    let mut raw = Vec::new();
    file.take(maximum + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > maximum {
        return Err(invalid());
    }
    Ok(raw)
}

struct Runtime {
    root: PathBuf,
    data: PathBuf,
    python: PathBuf,
    worker: PathBuf,
    manifest_hash: String,
    source_sha: String,
    version: String,
}

impl Runtime {
    fn admit(root: PathBuf, data: PathBuf, manifest_hash: String) -> Result<Self> {
        if !digest_valid(&manifest_hash) {
            return Err(invalid());
        }
        reject_links(&root)?;
        reject_links(&data)?;
        let root = root.canonicalize()?;
        if !root.is_dir() {
            return Err(invalid());
        }
        fs::create_dir_all(&data)?;
        reject_links(&data)?;
        let data = data.canonicalize()?;
        if data.starts_with(&root) {
            return Err(invalid());
        }
        let raw = read_bounded(&root.join("release-manifest.json"), 16 * 1024 * 1024)?;
        if hex_digest(&raw) != manifest_hash {
            return Err(invalid());
        }
        let manifest: Value = serde_json::from_slice(&raw)?;
        if manifest["schema_version"] != 1 {
            return Err(invalid());
        }
        let source_sha = manifest["source_sha"]
            .as_str()
            .filter(|v| v.len() == 40 && v.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(invalid)?
            .to_owned();
        let version = manifest["version"]
            .as_str()
            .filter(|v| bounded_text(v, 128))
            .ok_or_else(invalid)?
            .to_owned();
        let python = root.join("runtime/python.exe");
        let worker = root.join("app/engine/dubflow/download/enumeration/worker.py");
        // Verify all importable dependencies before executing Python. A trusted
        // entry point cannot verify dependencies already executed during import.
        let mut inventory = std::collections::HashMap::new();
        let mut folded = std::collections::HashSet::new();
        for entry in manifest["artifacts"].as_array().ok_or_else(invalid)?.iter() {
            let relative = entry["path"].as_str().ok_or_else(invalid)?;
            if relative.is_empty()
                || relative.contains(['\\', ':'])
                || relative
                    .split('/')
                    .any(|p| p.is_empty() || p == "." || p == "..")
                || !folded.insert(relative.to_lowercase())
            {
                return Err(invalid());
            }
            let expected_size: u64 = entry["size_bytes"].as_str().ok_or_else(invalid)?.parse()?;
            let expected = entry["sha256"]
                .as_str()
                .filter(|v| digest_valid(v))
                .ok_or_else(invalid)?;
            if inventory
                .insert(relative.to_owned(), (expected_size, expected.to_owned()))
                .is_some()
            {
                return Err(invalid());
            }
        }
        if !inventory.contains_key("runtime/python.exe")
            || !inventory.contains_key("app/engine/dubflow/download/enumeration/worker.py")
        {
            return Err(invalid());
        }
        let mut directories = vec![root.clone()];
        let mut observed = std::collections::HashSet::new();
        let mut buffer = [0u8; 64 * 1024];
        while let Some(directory) = directories.pop() {
            // Recheck the directory ancestry at entry/exit, rather than for
            // every leaf. Never follow a linked child; no imports occur until
            // the whole tree and every expected file have been verified.
            reject_links(&directory)?;
            for child in fs::read_dir(&directory)? {
                let path = child?.path();
                let metadata = fs::symlink_metadata(&path)?;
                reject_reparse(&metadata)?;
                if metadata.is_dir() {
                    directories.push(path);
                } else if metadata.is_file() {
                    let relative = path
                        .strip_prefix(&root)?
                        .to_str()
                        .ok_or_else(invalid)?
                        .replace('\\', "/");
                    if relative == "release-manifest.json" {
                        continue;
                    }
                    let (expected_size, expected_hash) =
                        inventory.get(&relative).ok_or_else(invalid)?;
                    let mut file = File::open(&path)?;
                    let opened = file.metadata()?;
                    reject_reparse(&opened)?;
                    if !opened.is_file() || opened.len() != *expected_size {
                        return Err(invalid());
                    }
                    let mut digest = Sha256::new();
                    loop {
                        let n = file.read(&mut buffer)?;
                        if n == 0 {
                            break;
                        }
                        digest.update(&buffer[..n]);
                    }
                    if format!("{:x}", digest.finalize()) != *expected_hash
                        || !observed.insert(relative)
                    {
                        return Err(invalid());
                    }
                } else {
                    return Err(invalid());
                }
            }
            reject_links(&directory)?;
        }
        if observed.len() != inventory.len() {
            return Err(invalid());
        }
        verify_final_inventory(&root, &inventory)?;
        if hex_digest(&read_bounded(
            &root.join("release-manifest.json"),
            16 * 1024 * 1024,
        )?) != manifest_hash
        {
            return Err(invalid());
        }
        Ok(Self {
            root,
            data,
            python,
            worker,
            manifest_hash,
            source_sha,
            version,
        })
    }
}

fn verify_final_inventory(
    root: &Path,
    inventory: &std::collections::HashMap<String, (u64, String)>,
) -> Result<()> {
    // Restore the final actual-tree check after hashing. Directory ancestry
    // alone cannot detect a previously hashed leaf becoming linked/missing or
    // an unexpected file arriving. Inspect each node without following links;
    // files are not rehashed and ancestors are checked per directory.
    let mut directories = vec![root.to_path_buf()];
    let mut checked_directories = Vec::new();
    let mut observed = std::collections::HashSet::new();
    while let Some(directory) = directories.pop() {
        reject_links(&directory)?;
        for child in fs::read_dir(&directory)? {
            let path = child?.path();
            let metadata = fs::symlink_metadata(&path)?;
            reject_reparse(&metadata)?;
            if metadata.is_dir() {
                directories.push(path);
            } else if metadata.is_file() {
                let relative = path
                    .strip_prefix(root)?
                    .to_str()
                    .ok_or_else(invalid)?
                    .replace('\\', "/");
                if relative == "release-manifest.json" {
                    continue;
                }
                let (expected_size, _) = inventory.get(&relative).ok_or_else(invalid)?;
                if metadata.len() != *expected_size || !observed.insert(relative) {
                    return Err(invalid());
                }
            } else {
                return Err(invalid());
            }
        }
        reject_links(&directory)?;
        checked_directories.push(directory);
    }
    if observed.len() != inventory.len() {
        return Err(invalid());
    }
    for directory in checked_directories {
        reject_links(&directory)?;
    }
    Ok(())
}

fn reject_reparse(metadata: &fs::Metadata) -> Result<()> {
    let linked = metadata.file_type().is_symlink();
    #[cfg(windows)]
    let linked = {
        use std::os::windows::fs::MetadataExt;
        linked || metadata.file_attributes() & 0x400 != 0
    };
    if linked {
        return Err(invalid());
    }
    Ok(())
}

#[derive(Debug, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Start {
        scan_id: String,
        provider_id: String,
        source_ref: String,
        page_size: usize,
        max_items: usize,
    },
    Resume {
        scan_id: String,
    },
    Pause {
        scan_id: String,
    },
    Cancel {
        scan_id: String,
    },
    Status {
        scan_id: String,
    },
    Items {
        scan_id: String,
        offset: usize,
        limit: usize,
    },
    Download {
        scan_id: String,
        identity_key: String,
        resume: bool,
    },
    PauseDownload {
        scan_id: String,
    },
    CancelDownload {
        scan_id: String,
    },
    Shutdown,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Producer {
    recipe: String,
    source_contract_version: u32,
    manifest_sha256: String,
    source_sha: String,
    release_version: String,
    provider_id: String,
    source_ref: String,
    page_size: usize,
}
impl Producer {
    fn fingerprint(&self) -> Result<String> {
        // Explicit ordered map matches Python's sorted compact UTF-8 JSON.
        let value = serde_json::to_value(self)?;
        let sorted: std::collections::BTreeMap<String, Value> = value
            .as_object()
            .ok_or_else(invalid)?
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        Ok(hex_digest(&serde_json::to_vec(&sorted)?))
    }
    fn validate(&self, runtime: &Runtime, spec: &Spec) -> Result<()> {
        if self.recipe != "owned-source-page-worker-v1"
            || self.source_contract_version != 1
            || self.manifest_sha256 != runtime.manifest_hash
            || self.source_sha != runtime.source_sha
            || self.release_version != runtime.version
            || self.provider_id != spec.provider
            || self.source_ref != spec.reference
            || self.page_size != spec.page_size
        {
            return Err(invalid());
        }
        public_url(&self.source_ref)
    }
}

fn admission_path(data: &Path, id: &str) -> Result<PathBuf> {
    if !identifier(id) {
        return Err(invalid());
    }
    let path = data
        .join("control/source-admissions")
        .join(format!("{id}.json"));
    reject_links(&path)?;
    Ok(path)
}
fn save_admission(data: &Path, id: &str, producer: &Producer) -> Result<()> {
    let path = admission_path(data, id)?;
    fs::create_dir_all(path.parent().ok_or_else(invalid)?)?;
    reject_links(&path)?;
    let raw = serde_json::to_vec(producer)?;
    match fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
    {
        Ok(mut file) => {
            file.write_all(&raw)?;
            file.sync_all()?;
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            // A crash may have flushed admission before the DB insert. Reuse
            // only identical bytes; never repair/rebind an old producer here.
            if read_bounded(&path, 16 * 1024)? != raw {
                return Err(invalid());
            }
        }
        Err(error) => return Err(error.into()),
    }
    Ok(())
}
fn load_admission(data: &Path, record: &ScanRecord) -> Result<Producer> {
    let producer: Producer = serde_json::from_slice(&read_bounded(
        &admission_path(data, &record.scan_id)?,
        16 * 1024,
    )?)?;
    if record.producer_fingerprint.as_ref() != Some(&producer.fingerprint()?)
        || record.provider_id != producer.provider_id
        || record.source_ref != producer.source_ref
    {
        return Err(invalid());
    }
    Ok(producer)
}

fn owned_supervisor(root: &Path, executable: &Path) -> Result<()> {
    reject_links(executable)?;
    if executable.canonicalize()? != root.join("app/bin/dubflow-supervisor.exe").canonicalize()? {
        return Err(invalid());
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(tag = "kind", deny_unknown_fields)]
enum Packet {
    #[serde(rename = "source-ready")]
    Ready {
        schema_version: u32,
        job_id: String,
        stage_id: String,
        producer: Producer,
        producer_fingerprint: String,
    },
    #[serde(rename = "source-page")]
    Page {
        schema_version: u32,
        job_id: String,
        stage_id: String,
        producer_fingerprint: String,
        dispatch_revision: u64,
        request_cursor: Option<String>,
        page: Page,
    },
    #[serde(rename = "source-download-progress")]
    DownloadProgress {
        schema_version: u32,
        job_id: String,
        stage_id: String,
        producer_fingerprint: String,
        dispatch_revision: u64,
        identity_key: String,
        source_id: String,
        source_url: String,
        downloaded_bytes: u64,
        total_bytes: Option<u64>,
    },
    #[serde(rename = "source-download")]
    Download {
        schema_version: u32,
        job_id: String,
        stage_id: String,
        producer_fingerprint: String,
        dispatch_revision: u64,
        identity_key: String,
        source_id: String,
        source_url: String,
        downloaded_bytes: u64,
        total_bytes: Option<u64>,
        media_file: String,
        size_bytes: u64,
        sha256: String,
        resumed: bool,
    },
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Page {
    schema_version: u32,
    items: Vec<Item>,
    failures: Vec<Failure>,
    next_cursor: Option<String>,
    completed: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    schema_version: u32,
    identity: Identity,
    title: String,
    duration_ticks: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Identity {
    provider_id: String,
    source_id: String,
    canonical_url: String,
    identity_key: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Failure {
    source_id: String,
    code: String,
    condition: String,
    retryable: bool,
}

fn packet(work: &Path, name: &str, expected: &str) -> Result<Packet> {
    if name.len() != 51
        || !name.starts_with("source-packet-")
        || !name.ends_with(".json")
        || !name[14..46]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid());
    }
    let expected = expected
        .strip_prefix("sha256:")
        .filter(|v| digest_valid(v))
        .ok_or_else(invalid)?;
    let raw = read_bounded(&work.join(name), MAX_PACKET)?;
    if hex_digest(&raw) != expected {
        return Err(invalid());
    }
    Ok(serde_json::from_slice(&raw)?)
}

fn checked_page(packet: Packet, original: &ScanRecord, page_size: usize) -> Result<PageCheckpoint> {
    let (schema, job, stage, fingerprint, revision, cursor, page) = match packet {
        Packet::Page {
            schema_version,
            job_id,
            stage_id,
            producer_fingerprint,
            dispatch_revision,
            request_cursor,
            page,
        } => (
            schema_version,
            job_id,
            stage_id,
            producer_fingerprint,
            dispatch_revision,
            request_cursor,
            page,
        ),
        _ => return Err(invalid()),
    };
    if schema != 1
        || job != original.scan_id
        || stage != STAGE
        || Some(&fingerprint) != original.producer_fingerprint.as_ref()
        || revision != original.dispatch_revision
        || cursor != original.cursor
        || page.schema_version != 1
        || page.items.len() + page.failures.len() > page_size
    {
        return Err(invalid());
    }
    let mut items = Vec::new();
    for (index, item) in page.items.into_iter().enumerate() {
        if item.schema_version != 1
            || item.identity.provider_id != original.provider_id
            || item.identity.identity_key
                != format!("{}:{}", original.provider_id, item.identity.source_id)
            || !bounded_text(&item.title, 4096)
            || item
                .duration_ticks
                .as_ref()
                .is_some_and(|v| v.parse::<u64>().is_err())
        {
            return Err(invalid());
        }
        public_url(&item.identity.canonical_url)?;
        items.push(PageItem::new(
            item.identity.identity_key,
            item.identity.source_id,
            item.identity.canonical_url,
            (original.discovered_count + index) as u64,
        ));
    }
    let mut failures = Vec::new();
    for failure in page.failures {
        if !identifier(&failure.code)
            || !bounded_text(&failure.source_id, 512)
            || !bounded_text(&failure.condition, 4096)
        {
            return Err(invalid());
        }
        // Keep error code and retry data, never persist raw upstream diagnostics.
        failures.push(PageFailure::new(
            failure.source_id,
            &failure.code,
            format!("source item unavailable ({})", failure.code),
            failure.retryable,
        ));
    }
    Ok(PageCheckpoint {
        next_cursor: page.next_cursor,
        completed: page.completed,
        items,
        failures,
    })
}

struct Spec {
    id: String,
    provider: String,
    reference: String,
    page_size: usize,
    maximum: usize,
    existing: bool,
}
enum Wire {
    Message(Envelope),
    Closed,
    Invalid,
}
struct Active {
    spec: Spec,
    work: PathBuf,
    child: Child,
    events: Receiver<Wire>,
    validator: StreamValidator,
    sequence: u64,
    original: Option<ScanRecord>,
    last_seen: Instant,
    download: Option<DownloadDispatch>,
}

struct DownloadDispatch {
    original: ScanRecord,
    item: SourceQueueItem,
    origin_revision: u64,
    resume: bool,
    worker_completed: bool,
    final_receipt: Option<MediaReceipt>,
    verifier: Option<MediaVerifier>,
}

struct MediaReceipt {
    packet_file: String,
    size_bytes: u64,
    sha256: String,
    resumed: bool,
    downloaded_bytes: u64,
    total_bytes: Option<u64>,
    verified_path: PathBuf,
    destination: PathBuf,
}

struct MediaVerifier {
    result: Receiver<std::result::Result<VerifiedMedia, ()>>,
    stopped: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}
struct VerifiedMedia {
    file: File,
    modified: std::time::SystemTime,
}

fn media_reader(path: &Path, publication: bool) -> Result<File> {
    reject_links(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Refuse concurrent writers while hashing. The final publication guard
        // also denies deletion until the supervisor's checked SQLite commit.
        options
            .share_mode(if publication { 1 } else { 1 | 4 })
            .custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    #[cfg(not(windows))]
    let _ = publication;
    Ok(options.open(path)?)
}

#[cfg(windows)]
fn media_identity(file: &File) -> Result<(u64, u64)> {
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    #[derive(Default)]
    struct Information {
        attributes: u32,
        creation: [u32; 2],
        access: [u32; 2],
        write: [u32; 2],
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(handle: *mut std::ffi::c_void, info: *mut Information)
            -> i32;
    }
    let mut info = Information::default();
    // The repr(C) buffer matches BY_HANDLE_FILE_INFORMATION; the retained File
    // owns this valid handle for the entire call. No handle ownership transfers.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Err(io::Error::last_os_error().into());
    }
    if info.attributes & 0x400 != 0 || info.links == 0 {
        return Err(invalid());
    }
    Ok((
        u64::from(info.volume),
        (u64::from(info.index_high) << 32) | u64::from(info.index_low),
    ))
}

#[cfg(unix)]
fn media_identity(file: &File) -> Result<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file.metadata()?;
    Ok((metadata.dev(), metadata.ino()))
}

fn checked_media(media: &VerifiedMedia, path: &Path, size: u64, publication: bool) -> Result<File> {
    let guard = media_reader(path, publication)?;
    let original = media.file.metadata()?;
    let observed = guard.metadata()?;
    if !observed.is_file()
        || observed.len() != size
        || original.len() != size
        || original.modified()? != media.modified
        || observed.modified()? != media.modified
        || media_identity(&media.file)? != media_identity(&guard)?
    {
        return Err(invalid());
    }
    reject_links(path)?;
    Ok(guard)
}
impl Drop for MediaVerifier {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Relaxed);
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
    }
}
fn verify_media(path: PathBuf, size: u64, expected: String) -> MediaVerifier {
    let (tx, result) = mpsc::channel();
    let stopped = Arc::new(AtomicBool::new(false));
    let stop = stopped.clone();
    let handle = thread::spawn(move || {
        let checked = (|| -> Result<VerifiedMedia> {
            let mut file = media_reader(&path, false)?;
            let metadata = file.metadata()?;
            if !metadata.is_file() || metadata.len() != size {
                return Err(invalid());
            }
            let stamp = metadata.modified()?;
            let mut digest = Sha256::new();
            let mut buffer = [0u8; 65536];
            let mut read = 0u64;
            loop {
                if stop.load(Ordering::Relaxed) {
                    return Err(invalid());
                }
                let count = file.read(&mut buffer)?;
                if count == 0 {
                    break;
                }
                read = read.checked_add(count as u64).ok_or_else(invalid)?;
                if read > size {
                    return Err(invalid());
                }
                digest.update(&buffer[..count]);
            }
            reject_links(&path)?;
            let media = VerifiedMedia {
                file,
                modified: stamp,
            };
            if read != size || format!("{:x}", digest.finalize()) != expected {
                return Err(invalid());
            }
            checked_media(&media, &path, size, false)?;
            Ok(media)
        })()
        .map_err(|_| ());
        let _ = tx.send(checked);
    });
    MediaVerifier {
        result,
        stopped,
        thread: Some(handle),
    }
}

fn observed_progress(
    item: &SourceQueueItem,
    status: ItemStatus,
    bytes: u64,
    total: Option<u64>,
    code: Option<&str>,
) -> ItemProgress {
    ItemProgress { status, retry_count: item.retry_count, downloaded_bytes: bytes,
        total_bytes: total, error_code: code.map(str::to_owned),
        error_message: code.map(|_| "Source materialization stopped; retained private data may be resumed only by its original producer.".into()),
        media_path: None, content_hash: None }
}
fn apply_progress(item: &mut SourceQueueItem, progress: &ItemProgress) {
    item.status = progress.status;
    item.retry_count = progress.retry_count;
    item.downloaded_bytes = progress.downloaded_bytes;
    item.total_bytes = progress.total_bytes;
    item.error_code = progress.error_code.clone();
    item.error_message = progress.error_message.clone();
    item.media_path = progress.media_path.clone();
    item.content_hash = progress.content_hash.clone();
}
impl Active {
    fn send(&mut self, payload: Payload) -> Result<()> {
        let kind = if matches!(&payload, Payload::Cancel { .. }) {
            MessageType::Cancel
        } else {
            MessageType::Command
        };
        let envelope = Envelope::new(
            kind,
            format!("native-source-{}", self.sequence),
            &self.spec.id,
            STAGE,
            self.sequence,
            payload,
        )?;
        self.child
            .stdin
            .as_mut()
            .ok_or_else(invalid)?
            .write_all(&envelope.to_line()?)?;
        self.child.stdin.as_mut().ok_or_else(invalid)?.flush()?;
        self.sequence += 1;
        Ok(())
    }
    fn dispatch(&mut self, record: ScanRecord) -> Result<()> {
        if record.status != ScanStatus::Running {
            return Err(invalid());
        }
        self.send(Payload::Command { command: "source_page".into(), args_json: serde_json::to_string(&json!({"producer_fingerprint":record.producer_fingerprint, "dispatch_revision":record.dispatch_revision, "cursor":record.cursor}))? })?;
        self.original = Some(record);
        Ok(())
    }
    fn stop(&mut self) -> Result<()> {
        let _ = self.send(Payload::Cancel {
            reason: "source supervisor control".into(),
        });
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if self.child.try_wait()?.is_some() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                self.child.kill()?;
                self.child.wait()?;
                return Ok(());
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}
impl Drop for Active {
    fn drop(&mut self) {
        // Closing native stdin is also the producer's parent-loss signal. Keep
        // the OS database claim until the child has been observed dead.
        self.child.stdin.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
        // Successful packets were removed after their checked commit. Remove
        // only an empty owned directory; retain any uncommitted packet evidence.
        let _ = fs::remove_dir(&self.work);
    }
}

fn bounded_line(reader: &mut impl BufRead) -> io::Result<Option<Vec<u8>>> {
    let mut line = Vec::new();
    reader
        .take(MAX_LINE_BYTES as u64 + 1)
        .read_until(b'\n', &mut line)?;
    if line.is_empty() {
        return Ok(None);
    }
    if line.len() > MAX_LINE_BYTES || !line.ends_with(b"\n") {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "source line rejected",
        ));
    }
    Ok(Some(line))
}

fn launch(runtime: &Runtime, spec: Spec) -> Result<Active> {
    launch_in_work(runtime, spec, None)
}
fn launch_in_work(runtime: &Runtime, spec: Spec, selected_work: Option<PathBuf>) -> Result<Active> {
    if !identifier(&spec.id)
        || !["generic", "bilibili", "douyin"].contains(&spec.provider.as_str())
        || !(1..=100).contains(&spec.page_size)
        || !(1..=10000).contains(&spec.maximum)
    {
        return Err(invalid());
    }
    public_url(&spec.reference)?;
    let work = selected_work.unwrap_or_else(|| {
        runtime.data.join("source-work").join(format!(
            "{}-{}-{}-{}",
            spec.id,
            std::process::id(),
            super::now_ms(),
            super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ))
    });
    reject_links(&work)?;
    fs::create_dir_all(&work)?;
    reject_links(&work)?;
    let work = work.canonicalize()?;
    if !work.starts_with(&runtime.data) {
        return Err(invalid());
    }
    let mut command = owned_worker_command(runtime, &work);
    let mut child = command.spawn()?;
    let stdout = child.stdout.take().ok_or_else(invalid)?;
    let stderr = child.stderr.take().ok_or_else(invalid)?;
    let (tx, events) = mpsc::sync_channel(16);
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let wire = match bounded_line(&mut reader) {
                Ok(Some(line)) => Envelope::from_line(&line)
                    .map(Wire::Message)
                    .unwrap_or(Wire::Invalid),
                Ok(None) => Wire::Closed,
                Err(_) => Wire::Invalid,
            };
            let terminal = !matches!(&wire, Wire::Message(_));
            if tx.send(wire).is_err() || terminal {
                break;
            }
        }
    });
    thread::spawn(move || {
        let _ = io::copy(&mut BufReader::new(stderr), &mut io::sink());
    });
    let mut active = Active {
        spec,
        work,
        child,
        events,
        validator: StreamValidator::new(35000, 0)?,
        sequence: 1,
        original: None,
        last_seen: Instant::now(),
        download: None,
    };
    active.send(Payload::Command { command: "source_prepare".into(), args_json: serde_json::to_string(&json!({"bundle_root":super::external_runtime_path(runtime.root.clone()), "work_root":super::external_runtime_path(active.work.clone()), "manifest_sha256":runtime.manifest_hash, "provider_id":active.spec.provider, "source_ref":active.spec.reference, "page_size":active.spec.page_size}))? })?;
    Ok(active)
}

fn owned_worker_command(runtime: &Runtime, work: &Path) -> Command {
    // Canonical Windows paths retain a verbatim prefix. Python preserves it
    // in executable/prefix/__file__, while the command's roots are normalized.
    // Use one external spelling; the worker's strict origin check stays intact.
    let mut command = Command::new(super::external_runtime_path(runtime.python.clone()));
    command
        .args(["-I", "-S", "-B"])
        .arg(super::external_runtime_path(runtime.worker.clone()))
        .current_dir(work)
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn scan_value(record: &ScanRecord) -> Value {
    let reference = if public_url(&record.source_ref).is_ok() {
        record.source_ref.as_str()
    } else {
        "[private reference redacted]"
    };
    json!({"event":"source_status", "scan_id":record.scan_id, "provider_id":record.provider_id, "source_ref":reference,
        "status":format!("{:?}", record.status).to_ascii_lowercase(), "cursor":record.cursor,
        "dispatch_revision":record.dispatch_revision, "producer_fingerprint":record.producer_fingerprint,
        "discovered_count":record.discovered_count, "completed_count":record.completed_count, "failed_count":record.failed_count})
}

struct Server {
    runtime: Runtime,
    queue: SourceQueue,
    active: Option<Active>,
    _owner: SourceOwner,
}
impl Server {
    fn open(runtime: Runtime) -> Result<Self> {
        let control = runtime.data.join("control");
        reject_links(&control)?;
        fs::create_dir_all(&control)?;
        let owner = SourceOwner::acquire(&control.join("source-owner.lock"), super::now_ms())?;
        let db = control.join("sources.sqlite3");
        for path in [
            db.clone(),
            control.join("sources.sqlite3-wal"),
            control.join("sources.sqlite3-shm"),
        ] {
            reject_links(&path)?;
        }
        let queue = SourceQueue::open(db)?;
        // The OS claim proves no compatible writable service survived. Workers
        // have no DB handle, and terminate on native stdin EOF after owner loss.
        queue.recover_running(super::now_ms())?;
        Ok(Self {
            runtime,
            queue,
            active: None,
            _owner: owner,
        })
    }
    fn start_download(&mut self, id: String, identity: String, resume: bool) -> Result<Value> {
        if self.active.is_some() || !identifier(&id) || !bounded_text(&identity, 1024) {
            return Err(invalid());
        }
        let record = self.queue.scan(&id)?;
        if record.status != ScanStatus::Completed || record.producer_fingerprint.is_none() {
            return Err(invalid());
        }
        let producer = load_admission(&self.runtime.data, &record)?;
        let spec = Spec {
            id: id.clone(),
            provider: record.provider_id.clone(),
            reference: record.source_ref.clone(),
            page_size: producer.page_size,
            maximum: record.max_items,
            existing: true,
        };
        producer.validate(&self.runtime, &spec)?;
        let mut item = self.queue.item(&id, &identity)?;
        if item.status != ItemStatus::Discovered
            && !(resume && item.status == ItemStatus::Downloading)
        {
            // Failed/cancelled items are not automatic retries. A changed
            // protected/network condition needs a separate explicit decision.
            return Err(invalid());
        }
        public_url(&item.source_url)?;
        let work = self
            .runtime
            .data
            .join("source-work/materialization")
            .join(&self.runtime.manifest_hash)
            .join(&id)
            .join(hex_digest(identity.as_bytes()));
        let mut active = launch_in_work(&self.runtime, spec, Some(work))?;
        let progress = observed_progress(
            &item,
            ItemStatus::Downloading,
            item.downloaded_bytes,
            item.total_bytes,
            None,
        );
        let original =
            self.queue
                .update_item_progress_checked(&record, &item, &progress, super::now_ms())?;
        apply_progress(&mut item, &progress);
        active.download = Some(DownloadDispatch {
            origin_revision: original.dispatch_revision,
            original,
            item,
            resume,
            worker_completed: false,
            final_receipt: None,
            verifier: None,
        });
        self.active = Some(active);
        Ok(json!({"event":"source_download_preparing", "scan_id":id, "identity_key":identity}))
    }
    fn halt_download(&mut self, id: &str, cancel: bool) -> Result<Value> {
        let active = self.active.as_mut().ok_or_else(invalid)?;
        if active.spec.id != id {
            return Err(invalid());
        }
        let download = active.download.as_mut().ok_or_else(invalid)?;
        let progress = observed_progress(
            &download.item,
            if cancel {
                ItemStatus::Cancelled
            } else {
                ItemStatus::Downloading
            },
            download.item.downloaded_bytes,
            download.item.total_bytes,
            Some(if cancel {
                "SOURCE_DOWNLOAD_CANCELLED"
            } else {
                "SOURCE_DOWNLOAD_PAUSED"
            }),
        );
        let record = self.queue.update_item_progress_checked(
            &download.original,
            &download.item,
            &progress,
            super::now_ms(),
        )?;
        apply_progress(&mut download.item, &progress);
        download.original = record;
        let identity = download.item.identity_key.clone();
        let mut active = self.active.take().ok_or_else(invalid)?;
        active.stop()?;
        Ok(
            json!({"event":if cancel {"source_download_cancelled"} else {"source_download_paused"},
            "scan_id":id, "identity_key":identity, "retained_private_media":true}),
        )
    }
    fn halt(&mut self, id: &str, cancel: bool) -> Result<Value> {
        if self
            .active
            .as_ref()
            .is_some_and(|a| a.spec.id == id && a.download.is_some())
        {
            return self.halt_download(id, cancel);
        }
        if self
            .active
            .as_ref()
            .is_some_and(|a| a.spec.id == id && a.original.is_none())
        {
            // Admission is not a durable scan until verified ready. Cancelling
            // preparation must still stop the owned child, including resume.
            let mut active = self.active.take().ok_or_else(invalid)?;
            let record = match self.queue.scan(id) {
                Ok(record) if cancel => {
                    Some(self.queue.cancel_scan(&record.scan_id, super::now_ms())?)
                }
                Ok(record) => Some(record),
                Err(QueueError::NotFound { .. }) => None,
                Err(error) => return Err(error.into()),
            };
            active.stop()?;
            return Ok(record.as_ref().map(scan_value).unwrap_or_else(
                || json!({"event":"source_preparation_stopped", "scan_id":id, "cancelled":cancel}),
            ));
        }
        let record = if cancel {
            self.queue.cancel_scan(id, super::now_ms())?
        } else {
            self.queue.pause_scan(id, super::now_ms())?
        };
        if self.active.as_ref().is_some_and(|a| a.spec.id == id) {
            let mut active = self.active.take().ok_or_else(invalid)?;
            active.stop()?;
        }
        Ok(scan_value(&record))
    }
    fn request(&mut self, request: Request) -> Result<(Value, bool)> {
        let value = match request {
            Request::Start {
                scan_id,
                provider_id,
                source_ref,
                page_size,
                max_items,
            } => {
                if self.active.is_some() {
                    return Err(invalid());
                }
                match self.queue.scan(&scan_id) {
                    Err(QueueError::NotFound { .. }) => (),
                    _ => return Err(invalid()),
                }
                self.active = Some(launch(
                    &self.runtime,
                    Spec {
                        id: scan_id.clone(),
                        provider: provider_id,
                        reference: source_ref,
                        page_size,
                        maximum: max_items,
                        existing: false,
                    },
                )?);
                json!({"event":"source_preparing", "scan_id":scan_id})
            }
            Request::Resume { scan_id } => {
                if self.active.is_some() {
                    return Err(invalid());
                }
                let record = self.queue.scan(&scan_id)?;
                if ![ScanStatus::Paused, ScanStatus::Queued].contains(&record.status)
                    || record.producer_fingerprint.is_none()
                {
                    return Err(invalid());
                }
                let producer = load_admission(&self.runtime.data, &record)?;
                let spec = Spec {
                    id: scan_id.clone(),
                    provider: record.provider_id,
                    reference: record.source_ref,
                    page_size: producer.page_size,
                    maximum: record.max_items,
                    existing: true,
                };
                // Reject a changed runtime before any extraction or resume write.
                producer.validate(&self.runtime, &spec)?;
                self.active = Some(launch(&self.runtime, spec)?);
                json!({"event":"source_preparing", "scan_id":scan_id})
            }
            Request::Pause { scan_id } => self.halt(&scan_id, false)?,
            Request::Cancel { scan_id } => self.halt(&scan_id, true)?,
            Request::Status { scan_id } => scan_value(&self.queue.scan(&scan_id)?),
            Request::Items {
                scan_id,
                offset,
                limit,
            } => {
                if limit == 0 || limit > 100 || offset > 10000 {
                    return Err(invalid());
                }
                let items = self.queue.items(&scan_id)?;
                let total = items.len();
                let items: Vec<Value> = items.into_iter().skip(offset).take(limit).map(|i| {
                    let public = public_url(&i.source_url).is_ok();
                    json!({"identity_key":i.identity_key, "source_id":i.source_id, "source_url":if public {Some(i.source_url)} else {None}, "status":format!("{:?}",i.status).to_ascii_lowercase(), "retry_count":i.retry_count, "downloaded_bytes":i.downloaded_bytes, "total_bytes":i.total_bytes, "media_path":i.media_path, "content_hash":i.content_hash, "error_code":i.error_code})
                }).collect();
                json!({"event":"source_items", "scan_id":scan_id, "offset":offset, "total":total, "items":items})
            }
            Request::Download {
                scan_id,
                identity_key,
                resume,
            } => self.start_download(scan_id, identity_key, resume)?,
            Request::PauseDownload { scan_id } => self.halt_download(&scan_id, false)?,
            Request::CancelDownload { scan_id } => self.halt_download(&scan_id, true)?,
            Request::Shutdown => {
                self.shutdown()?;
                return Ok((json!({"event":"source_shutdown"}), true));
            }
        };
        Ok((value, false))
    }
    fn shutdown(&mut self) -> Result<()> {
        if self.active.as_ref().is_some_and(|a| a.download.is_some()) {
            let id = self.active.as_ref().ok_or_else(invalid)?.spec.id.clone();
            self.halt_download(&id, false)?;
            return Ok(());
        }
        if let Some(mut active) = self.active.take() {
            if let Ok(record) = self.queue.scan(&active.spec.id) {
                if record.status == ScanStatus::Running {
                    self.queue.pause_scan(&record.scan_id, super::now_ms())?;
                }
            }
            active.stop()?;
        }
        Ok(())
    }
    fn download_packet(&mut self, document: Packet, packet_file: String) -> Result<Option<Value>> {
        let active = self.active.as_mut().ok_or_else(invalid)?;
        if active.original.is_none() {
            return Err(invalid());
        }
        let download = active.download.as_mut().ok_or_else(invalid)?;
        if download.final_receipt.is_some() {
            return Err(invalid());
        }
        let (schema, job, stage, fingerprint, revision, identity, source, url, bytes, total, media) =
            match document {
                Packet::DownloadProgress {
                    schema_version,
                    job_id,
                    stage_id,
                    producer_fingerprint,
                    dispatch_revision,
                    identity_key,
                    source_id,
                    source_url,
                    downloaded_bytes,
                    total_bytes,
                } => (
                    schema_version,
                    job_id,
                    stage_id,
                    producer_fingerprint,
                    dispatch_revision,
                    identity_key,
                    source_id,
                    source_url,
                    downloaded_bytes,
                    total_bytes,
                    None,
                ),
                Packet::Download {
                    schema_version,
                    job_id,
                    stage_id,
                    producer_fingerprint,
                    dispatch_revision,
                    identity_key,
                    source_id,
                    source_url,
                    downloaded_bytes,
                    total_bytes,
                    media_file,
                    size_bytes,
                    sha256,
                    resumed,
                } => (
                    schema_version,
                    job_id,
                    stage_id,
                    producer_fingerprint,
                    dispatch_revision,
                    identity_key,
                    source_id,
                    source_url,
                    downloaded_bytes,
                    total_bytes,
                    Some((media_file, size_bytes, sha256, resumed)),
                ),
                _ => return Err(invalid()),
            };
        const MAX_TRANSFER: u64 = 64 * 1024 * 1024 * 1024;
        if schema != 1
            || job != active.spec.id
            || stage != STAGE
            || Some(&fingerprint) != download.original.producer_fingerprint.as_ref()
            || revision != download.origin_revision
            || identity != download.item.identity_key
            || source != download.item.source_id
            || url != download.item.source_url
            || bytes > MAX_TRANSFER
            || total.is_some_and(|value| value < bytes || value > MAX_TRANSFER)
        {
            return Err(invalid());
        }
        public_url(&url)?;
        if let Some((name, size, hash, resumed)) = media {
            if name != "source-media.mp4"
                || size == 0
                || size > MAX_TRANSFER / 2
                || !digest_valid(&hash)
            {
                return Err(invalid());
            }
            let path = active.work.join(name);
            reject_links(&path)?;
            let parent = self
                .runtime
                .data
                .join("source-media")
                .join(&self.runtime.manifest_hash)
                .join(&active.spec.id)
                .join(hex_digest(download.item.identity_key.as_bytes()));
            reject_links(&parent)?;
            fs::create_dir_all(&parent)?;
            reject_links(&parent)?;
            let destination = parent.join(format!("{hash}.mp4"));
            reject_links(&destination)?;
            // An interrupted publication may already exist. Rehash it, never
            // trust its content-addressed name or replace an unverified orphan.
            let verified_path = if destination.exists() {
                destination.clone()
            } else {
                path
            };
            download.verifier = Some(verify_media(verified_path.clone(), size, hash.clone()));
            download.final_receipt = Some(MediaReceipt {
                packet_file,
                size_bytes: size,
                sha256: hash,
                resumed,
                downloaded_bytes: bytes,
                total_bytes: total,
                verified_path,
                destination,
            });
            active.last_seen = Instant::now();
            return Ok(None);
        }
        let progress =
            observed_progress(&download.item, ItemStatus::Downloading, bytes, total, None);
        let record = self.queue.update_item_progress_checked(
            &download.original,
            &download.item,
            &progress,
            super::now_ms(),
        )?;
        apply_progress(&mut download.item, &progress);
        download.original = record;
        fs::remove_file(active.work.join(packet_file))?;
        active.last_seen = Instant::now();
        Ok(Some(
            json!({"event":"source_download_progress", "scan_id":active.spec.id,
            "identity_key":identity, "downloaded_bytes":bytes, "total_bytes":total}),
        ))
    }
    fn poll_download(&mut self) -> Result<Option<Value>> {
        let Some(active) = self.active.as_mut() else {
            return Ok(None);
        };
        let Some(download) = active.download.as_mut() else {
            return Ok(None);
        };
        if !download.worker_completed {
            return Ok(None);
        }
        let verifier = download.verifier.as_mut().ok_or_else(invalid)?;
        let verified = match verifier.result.try_recv() {
            Err(TryRecvError::Empty) => {
                // Actual verified-file worker is still live; source controls
                // remain available while bounded streaming SHA verification runs.
                if verifier
                    .thread
                    .as_ref()
                    .is_some_and(|handle| !handle.is_finished())
                {
                    active.last_seen = Instant::now();
                    return Ok(None);
                }
                return Err(invalid());
            }
            Ok(Ok(media)) => media,
            _ => return Err(invalid()),
        };
        let receipt = download.final_receipt.as_ref().ok_or_else(invalid)?;
        let source = active.work.join("source-media.mp4");
        // Only a successful producer and verified media can be promoted. Keep
        // a content-addressed orphan if the subsequent checked DB commit fails.
        let destination = &receipt.destination;
        checked_media(&verified, &receipt.verified_path, receipt.size_bytes, false)?;
        if &receipt.verified_path != destination {
            // Same owned data volume. Hard-link publication refuses an existing
            // name atomically and avoids copying multi-gigabyte verified media.
            fs::hard_link(&receipt.verified_path, destination)?;
        }
        let publication_guard = checked_media(&verified, destination, receipt.size_bytes, true)?;
        let mut progress = observed_progress(
            &download.item,
            ItemStatus::Downloaded,
            receipt.downloaded_bytes,
            receipt.total_bytes,
            None,
        );
        progress.media_path = Some(
            super::external_runtime_path(destination.clone())
                .to_string_lossy()
                .into_owned(),
        );
        progress.content_hash = Some(receipt.sha256.clone());
        let record = self.queue.update_item_progress_checked(
            &download.original,
            &download.item,
            &progress,
            super::now_ms(),
        )?;
        let value = json!({"event":"source_downloaded", "scan_id":active.spec.id,
            "identity_key":download.item.identity_key, "media_path":progress.media_path,
            "sha256":receipt.sha256, "size_bytes":receipt.size_bytes, "resumed":receipt.resumed,
            "downloaded_bytes":receipt.downloaded_bytes, "total_bytes":receipt.total_bytes,
            "completed_count":record.completed_count, "failed_count":record.failed_count});
        let _ = fs::remove_file(active.work.join(&receipt.packet_file));
        drop(publication_guard);
        drop(verified);
        // A duplicate private final is no longer needed after durable admission.
        // Partial streams/receipts remain scoped to their original producer.
        let _ = fs::remove_file(source);
        self.active.take();
        Ok(Some(value))
    }
    fn fail_download(&mut self, code: &str, retryable: bool) -> Result<Value> {
        let active = self.active.as_mut().ok_or_else(invalid)?;
        let download = active.download.as_mut().ok_or_else(invalid)?;
        let progress = observed_progress(
            &download.item,
            ItemStatus::Failed,
            download.item.downloaded_bytes,
            download.item.total_bytes,
            Some(code),
        );
        self.queue.update_item_progress_checked(
            &download.original,
            &download.item,
            &progress,
            super::now_ms(),
        )?;
        let value = json!({"event":"source_download_failed", "scan_id":active.spec.id,
            "identity_key":download.item.identity_key, "code":code, "retryable":retryable, "automatic_retry":false,
            "retained_private_media":true});
        let mut active = self.active.take().ok_or_else(invalid)?;
        active.stop()?;
        Ok(value)
    }
    fn event(&mut self, wire: Wire) -> Result<Option<Value>> {
        if matches!(&wire, Wire::Closed)
            && self
                .active
                .as_ref()
                .is_some_and(|a| a.download.as_ref().is_some_and(|d| d.worker_completed))
        {
            return Ok(None);
        }
        let active = self.active.as_mut().ok_or_else(invalid)?;
        let envelope = match wire {
            Wire::Message(value) => value,
            _ => return Err(invalid()),
        };
        active.validator.accept(&envelope)?;
        if envelope.job_id != active.spec.id || envelope.stage_id != STAGE {
            return Err(invalid());
        }
        match envelope.payload {
            Payload::Heartbeat { .. } => {
                active.last_seen = Instant::now();
                Ok(None)
            }
            Payload::Checkpoint {
                checkpoint_id,
                reusable: true,
                artifact_hash: Some(hash),
            } => {
                let document = packet(&active.work, &checkpoint_id, &hash)?;
                if matches!(
                    &document,
                    Packet::DownloadProgress { .. } | Packet::Download { .. }
                ) {
                    return self.download_packet(document, checkpoint_id);
                }
                let record = if active.original.is_none() {
                    let (schema, job, stage, producer, fingerprint) = match document {
                        Packet::Ready {
                            schema_version,
                            job_id,
                            stage_id,
                            producer,
                            producer_fingerprint,
                        } => (
                            schema_version,
                            job_id,
                            stage_id,
                            producer,
                            producer_fingerprint,
                        ),
                        _ => return Err(invalid()),
                    };
                    if schema != 1 || job != active.spec.id || stage != STAGE {
                        return Err(invalid());
                    }
                    producer.validate(&self.runtime, &active.spec)?;
                    if producer.fingerprint()? != fingerprint {
                        return Err(invalid());
                    }
                    if active.spec.existing {
                        let original = self.queue.scan(&active.spec.id)?;
                        if original.producer_fingerprint.as_ref() != Some(&fingerprint) {
                            return Err(invalid());
                        }
                    } else {
                        save_admission(&self.runtime.data, &active.spec.id, &producer)?;
                        self.queue.create_bound_scan(
                            &active.spec.id,
                            &producer.provider_id,
                            &producer.source_ref,
                            active.spec.maximum,
                            super::now_ms(),
                            &fingerprint,
                        )?;
                    }
                    if let Some(download) = active.download.as_ref() {
                        let record = self.queue.scan(&active.spec.id)?;
                        if record != download.original {
                            return Err(invalid());
                        }
                        record
                    } else {
                        self.queue.resume_scan(&active.spec.id, super::now_ms())?
                    }
                } else {
                    let original = active.original.as_ref().ok_or_else(invalid)?;
                    let page = checked_page(document, original, active.spec.page_size)?;
                    self.queue
                        .checkpoint_page_checked(original, &page, super::now_ms())?
                };
                // Only a checked durable commit authorizes deletion and UI update.
                fs::remove_file(active.work.join(&checkpoint_id))?;
                if let Some(download) = active.download.as_ref() {
                    active.send(Payload::Command { command: "source_download".into(), args_json: serde_json::to_string(&json!({
                        "producer_fingerprint":record.producer_fingerprint, "dispatch_revision":download.origin_revision,
                        "identity_key":download.item.identity_key, "source_id":download.item.source_id,
                        "source_url":download.item.source_url, "resume":download.resume}))? })?;
                    active.original = Some(record.clone());
                } else if record.status == ScanStatus::Running {
                    active.dispatch(record.clone())?;
                }
                Ok(Some(scan_value(&record)))
            }
            Payload::Shutdown {
                status: ShutdownStatus::Completed,
            } => {
                if let Some(download) = active.download.as_mut() {
                    if download.final_receipt.is_none() {
                        return Err(invalid());
                    }
                    let deadline = Instant::now() + Duration::from_secs(3);
                    loop {
                        if let Some(status) = active.child.try_wait()? {
                            if !status.success() {
                                return Err(invalid());
                            }
                            break;
                        }
                        if Instant::now() >= deadline {
                            return Err(invalid());
                        }
                        thread::sleep(Duration::from_millis(20));
                    }
                    download.worker_completed = true;
                    return Ok(None);
                }
                let record = self.queue.scan(&active.spec.id)?;
                if record.status != ScanStatus::Completed {
                    return Err(invalid());
                }
                let mut active = self.active.take().ok_or_else(invalid)?;
                let deadline = Instant::now() + Duration::from_secs(3);
                loop {
                    if let Some(status) = active.child.try_wait()? {
                        if !status.success() {
                            return Err(invalid());
                        }
                        break;
                    }
                    if Instant::now() >= deadline {
                        return Err(invalid());
                    }
                    thread::sleep(Duration::from_millis(20));
                }
                Ok(Some(scan_value(&record)))
            }
            Payload::Failure {
                code,
                retryable,
                attempt: 1,
                ..
            } => {
                if ![
                    "AUTH_REQUIRED",
                    "RATE_LIMITED",
                    "SOURCE_CHANGED",
                    "NOT_FOUND",
                    "PRIVATE",
                    "NETWORK",
                    "UNSUPPORTED",
                    "INVALID_INPUT",
                    "CHECKPOINT_INVALID",
                    "SOURCE_WORKER_FAILED",
                    "SOURCE_WORKER_REQUEST_INVALID",
                ]
                .contains(&code.as_str())
                {
                    return Err(invalid());
                }
                if active.download.is_some() {
                    return self.fail_download(&code, retryable).map(Some);
                }
                let id = active.spec.id.clone();
                self.shutdown()?;
                Ok(Some(
                    json!({"event":"source_error", "scan_id":id, "code":code, "condition":format!("Source scan stopped ({code}); last committed page retained."), "retryable":retryable, "automatic_retry":false}),
                ))
            }
            _ => Err(invalid()),
        }
    }
    fn fail_active(&mut self) -> Value {
        if self.active.as_ref().is_some_and(|a| a.download.is_some()) {
            if let Ok(value) = self.fail_download("SOURCE_CHANGED", false) {
                return value;
            }
            // A stale/failed durable transaction must never be overwritten by
            // an unchecked fallback. Retire the producer and retain evidence.
            if let Some(mut active) = self.active.take() {
                let _ = active.stop();
            }
            return json!({"event":"source_error", "code":"SOURCE_DOWNLOAD_DISPATCH_REJECTED", "retryable":false});
        }
        let id = self.active.as_ref().map(|a| a.spec.id.clone());
        let _ = self.shutdown();
        json!({"event":"source_error", "scan_id":id, "code":"SOURCE_SCAN_STOPPED", "condition":"Source scan stopped; the last committed page is retained. Repair the reported source/runtime condition before explicit resume.", "retryable":false})
    }
}

pub(super) fn run(args: impl Iterator<Item = OsString>) -> Result<()> {
    let mut args = args;
    let mut root = None;
    let mut data = None;
    let mut hash = None;
    while let Some(flag) = args.next() {
        let value = args.next().ok_or_else(invalid)?;
        match flag.to_str() {
            Some("--root") if root.is_none() => root = Some(PathBuf::from(value)),
            Some("--data-root") if data.is_none() => data = Some(PathBuf::from(value)),
            Some("--manifest-sha256") if hash.is_none() => {
                hash = Some(value.into_string().map_err(|_| invalid())?)
            }
            _ => return Err(invalid()),
        }
    }
    let runtime = Runtime::admit(
        root.ok_or_else(invalid)?,
        data.ok_or_else(invalid)?,
        hash.ok_or_else(invalid)?,
    )?;
    owned_supervisor(&runtime.root, &std::env::current_exe()?)?;
    let mut server = Server::open(runtime)?;
    let (tx, input) = mpsc::sync_channel(16);
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = BufReader::new(stdin.lock());
        loop {
            let line = bounded_line(&mut reader);
            let terminal = !matches!(&line, Ok(Some(_)));
            if tx.send(line).is_err() || terminal {
                break;
            }
        }
    });
    let stdout = io::stdout();
    let mut output = io::BufWriter::new(stdout.lock());
    super::emit_value(
        &mut output,
        json!({"event":"source_ready", "schema_version":1, "single_writer":true}),
    )?;
    loop {
        match input.try_recv() {
            Ok(Ok(Some(line))) => {
                let result = serde_json::from_slice::<Request>(&line)
                    .map_err(|_| invalid())
                    .and_then(|request| server.request(request));
                match result {
                    Ok((value, terminal)) => {
                        super::emit_value(&mut output, value)?;
                        if terminal {
                            break;
                        }
                    }
                    Err(_) => super::emit_value(
                        &mut output,
                        json!({"event":"source_error", "code":"SOURCE_REQUEST_REJECTED", "retryable":false}),
                    )?,
                }
            }
            Ok(_) | Err(TryRecvError::Disconnected) => {
                server.shutdown()?;
                break;
            }
            Err(TryRecvError::Empty) => (),
        }
        let wire = server
            .active
            .as_ref()
            .and_then(|a| a.events.try_recv().ok());
        if let Some(wire) = wire {
            match server.event(wire) {
                Ok(Some(value)) => super::emit_value(&mut output, value)?,
                Ok(None) => (),
                Err(_) => {
                    let value = server.fail_active();
                    super::emit_value(&mut output, value)?;
                }
            }
        }
        match server.poll_download() {
            Ok(Some(value)) => super::emit_value(&mut output, value)?,
            Ok(None) => (),
            Err(_) => {
                let value = server.fail_active();
                super::emit_value(&mut output, value)?;
            }
        }
        if server
            .active
            .as_ref()
            .is_some_and(|a| a.last_seen.elapsed() > Duration::from_secs(35))
        {
            let value = server.fail_active();
            super::emit_value(&mut output, value)?;
        }
        thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;

    struct Temporary(PathBuf);
    impl Temporary {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "dubflow-source-native-{}-{}-{}",
                std::process::id(),
                super::super::now_ms(),
                super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
        fn runtime(&self) -> Runtime {
            let root = self.0.join("bundle");
            let data = self.0.join("data");
            fs::create_dir_all(&root).unwrap();
            fs::create_dir_all(&data).unwrap();
            Runtime {
                root,
                data,
                python: self.0.join("unused-python"),
                worker: self.0.join("unused-worker"),
                manifest_hash: "a".repeat(64),
                source_sha: "b".repeat(40),
                version: "0.1.0-test".into(),
            }
        }
        fn recorded_runtime(&self) -> Runtime {
            let mut runtime = self.runtime();
            // CI test interpreter and scripted SDK substitute only. Production
            // Runtime::admit accepts exclusively inventory-verified entry points.
            let output = Command::new("python")
                .args(["-I", "-S", "-B", "-c", "import sys;print(sys.executable)"])
                .output()
                .unwrap();
            assert!(output.status.success());
            runtime.python = PathBuf::from(String::from_utf8(output.stdout).unwrap().trim());
            runtime.worker = runtime.root.join("recorded-worker.py");
            fs::write(&runtime.worker, r#"import hashlib,json,pathlib,sys,time,uuid
first=json.loads(sys.stdin.buffer.readline())
args=first['payload']['args']; work=pathlib.Path(args['work_root'])
producer={'recipe':'owned-source-page-worker-v1','source_contract_version':1,'manifest_sha256':args['manifest_sha256'],'source_sha':'b'*40,'release_version':'0.1.0-test','provider_id':args['provider_id'],'source_ref':args['source_ref'],'page_size':args['page_size']}
fingerprint=hashlib.sha256(json.dumps(producer,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
sequence=0
def emit(kind,payload):
 global sequence
 sequence+=1
 sys.stdout.buffer.write((json.dumps({'schema_version':1,'message_type':kind,'message_id':'recorded-'+str(sequence),'job_id':first['job_id'],'stage_id':'source-enumeration','sequence':sequence,'payload':payload})+'\n').encode());sys.stdout.buffer.flush()
def packet(value):
 value.update(job_id=first['job_id'],stage_id='source-enumeration')
 raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();name='source-packet-'+uuid.uuid4().hex+'.json'
 (work/name).write_bytes(raw)
 emit('checkpoint',{'checkpoint_id':name,'artifact_hash':'sha256:'+hashlib.sha256(raw).hexdigest(),'reusable':True})
packet({'schema_version':1,'kind':'source-ready','producer':producer,'producer_fingerprint':fingerprint})
while True:
 line=sys.stdin.buffer.readline()
 if not line: sys.exit(2)
 request=json.loads(line)
 if request['message_type']=='cancel':sys.exit(0)
 args=request['payload']['args']
 if request['payload']['command']=='source_download':
  mode_path=work/'download-mode.json';mode=json.loads(mode_path.read_text()) if mode_path.exists() else 'complete'
  partial=work/'recorded-media.part';resumed=args['resume'] and partial.exists()
  binding={key:args[key] for key in ('producer_fingerprint','dispatch_revision','identity_key','source_id','source_url')}
  partial.write_bytes(b'abcd')
  packet({'schema_version':1,'kind':'source-download-progress',**binding,'downloaded_bytes':4,'total_bytes':8})
  if mode=='pause':
   sys.stdin.buffer.readline();sys.exit(0)
  if mode=='fail':
   emit('failure',{'code':'NETWORK','retryable':True,'attempt':1,'condition':'recorded network failure'})
   emit('shutdown',{'status':'failed'});sys.exit(2)
  raw=partial.read_bytes()+b'efgh';(work/'source-media.mp4').write_bytes(raw)
  packet({'schema_version':1,'kind':'source-download',**binding,'media_file':'source-media.mp4','size_bytes':len(raw),'sha256':'0'*64 if mode=='bad-hash' else hashlib.sha256(raw).hexdigest(),'resumed':resumed,'downloaded_bytes':8,'total_bytes':8})
  emit('shutdown',{'status':'completed'});sys.exit(2 if mode=='bad-exit' else 0)
 cursor=args['cursor']
 if cursor=='page-2' and request['sequence']>2:
  time.sleep(30)  # Interrupt a real page-2 process after the first DB commit.
 item={'schema_version':1,'identity':{'provider_id':producer['provider_id'],'source_id':'one' if cursor is None else 'two','canonical_url':'https://example.test/one' if cursor is None else 'https://example.test/two','identity_key':producer['provider_id']+(':one' if cursor is None else ':two')},'title':'Recorded item','duration_ticks':'1000'}
 done=cursor is not None
 packet({'schema_version':1,'kind':'source-page','producer_fingerprint':fingerprint,'dispatch_revision':args['dispatch_revision'],'request_cursor':cursor,'page':{'schema_version':1,'items':[item],'failures':[],'next_cursor':None if done else 'page-2','completed':done}})
 if done:emit('shutdown',{'status':'completed'});sys.exit(0)
"#).unwrap();
            runtime
        }
    }
    impl Drop for Temporary {
        fn drop(&mut self) {
            let parent = self.0.parent().unwrap().canonicalize().unwrap();
            assert_eq!(parent, std::env::temp_dir().canonicalize().unwrap());
            assert!(self
                .0
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("dubflow-source-native-"));
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn producer() -> Producer {
        Producer {
            recipe: "owned-source-page-worker-v1".into(),
            source_contract_version: 1,
            manifest_sha256: "a".repeat(64),
            source_sha: "b".repeat(40),
            release_version: "0.1.0-test".into(),
            provider_id: "generic".into(),
            source_ref: "https://example.test/điện-ảnh".into(),
            page_size: 2,
        }
    }
    fn original(queue: &SourceQueue) -> ScanRecord {
        let producer = producer();
        queue
            .create_bound_scan(
                "scan",
                "generic",
                &producer.source_ref,
                100,
                1,
                &producer.fingerprint().unwrap(),
            )
            .unwrap();
        queue.resume_scan("scan", 2).unwrap()
    }
    fn page_value(record: &ScanRecord) -> Value {
        json!({"schema_version":1,"kind":"source-page","job_id":record.scan_id,"stage_id":STAGE,"producer_fingerprint":record.producer_fingerprint,"dispatch_revision":record.dispatch_revision,"request_cursor":record.cursor,"page":{"schema_version":1,"items":[{"schema_version":1,"identity":{"provider_id":"generic","source_id":"one","canonical_url":"https://example.test/one","identity_key":"generic:one"},"title":"One","duration_ticks":"1000"}],"failures":[],"next_cursor":"page-2","completed":false}})
    }
    fn decode(value: Value) -> Packet {
        serde_json::from_value(value).unwrap()
    }
    fn pump(server: &mut Server) {
        let wire = server
            .active
            .as_ref()
            .unwrap()
            .events
            .recv_timeout(Duration::from_secs(10))
            .unwrap();
        server.event(wire).unwrap();
    }

    fn download_server(temp: &Temporary) -> Server {
        let mut server = Server::open(temp.recorded_runtime()).unwrap();
        let record = original(&server.queue);
        let mut value = page_value(&record);
        value["page"]["completed"] = json!(true);
        value["page"]["next_cursor"] = Value::Null;
        let mut sibling = value["page"]["items"][0].clone();
        sibling["identity"]["source_id"] = json!("two");
        sibling["identity"]["identity_key"] = json!("generic:two");
        sibling["identity"]["canonical_url"] = json!("https://example.test/two");
        value["page"]["items"].as_array_mut().unwrap().push(sibling);
        server
            .queue
            .checkpoint_page_checked(
                &record,
                &checked_page(decode(value), &record, 2).unwrap(),
                3,
            )
            .unwrap();
        save_admission(&server.runtime.data, "scan", &producer()).unwrap();
        server
    }
    fn begin_download(server: &mut Server, identity: &str, mode: &str, resume: bool) -> PathBuf {
        server
            .request(Request::Download {
                scan_id: "scan".into(),
                identity_key: identity.into(),
                resume,
            })
            .unwrap();
        let work = server.active.as_ref().unwrap().work.clone();
        fs::write(
            work.join("download-mode.json"),
            serde_json::to_vec(mode).unwrap(),
        )
        .unwrap();
        pump(server); // Actual private ready/producer admission and command.
        work
    }
    fn finish_verification(server: &mut Server) -> Result<Value> {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(value) = server.poll_download()? {
                return Ok(value);
            }
            if Instant::now() >= deadline {
                return Err(invalid());
            }
            thread::sleep(Duration::from_millis(5));
        }
    }
    fn finish_download(server: &mut Server) -> Value {
        pump(server); // Actual progress -> checked item transaction.
        pump(server); // Actual final packet -> native streaming verification.
        pump(server); // Actual successful child exit.
        finish_verification(server).unwrap()
    }

    #[test]
    fn native_recorded_download_commits_verified_owned_media_and_reopens() {
        let temp = Temporary::new();
        let mut server = download_server(&temp);
        let before = server.queue.scan("scan").unwrap();
        let work = begin_download(&mut server, "generic:one", "complete", false);
        let value = finish_download(&mut server);
        assert_eq!(value["event"], "source_downloaded");
        assert_eq!(value["downloaded_bytes"], 8);
        assert_eq!(value["completed_count"], 1);
        assert!(server.active.is_none());
        let item = server.queue.item("scan", "generic:one").unwrap();
        assert_eq!(item.status, ItemStatus::Downloaded);
        assert_eq!(
            item.content_hash.as_deref(),
            Some(hex_digest(b"abcdefgh").as_str())
        );
        let media = PathBuf::from(item.media_path.as_ref().unwrap())
            .canonicalize()
            .unwrap();
        assert!(media.starts_with(&server.runtime.data.join("source-media")));
        assert_eq!(fs::read(&media).unwrap(), b"abcdefgh");
        assert!(!work.join("source-media.mp4").exists());
        let record = server.queue.scan("scan").unwrap();
        assert_eq!(record.status, ScanStatus::Completed);
        assert_eq!(record.cursor, before.cursor);
        assert_eq!(record.producer_fingerprint, before.producer_fingerprint);
        assert_eq!(
            server.queue.item("scan", "generic:two").unwrap().status,
            ItemStatus::Discovered
        );
        assert!(server
            .start_download("scan".into(), "generic:one".into(), true)
            .is_err());
        drop(server);
        let reopened = Server::open(temp.recorded_runtime()).unwrap();
        assert_eq!(reopened.queue.item("scan", "generic:one").unwrap(), item);
        assert_eq!(reopened.queue.scan("scan").unwrap(), record);
    }

    #[test]
    fn native_download_pause_preserves_parts_and_refuses_old_dispatch_on_resume() {
        let temp = Temporary::new();
        let mut server = download_server(&temp);
        let work = begin_download(&mut server, "generic:one", "pause", false);
        pump(&mut server);
        let old = server.active.as_ref().unwrap().download.as_ref().unwrap();
        let (original, item) = (old.original.clone(), old.item.clone());
        assert_eq!(item.downloaded_bytes, 4);
        assert_eq!(
            server.halt_download("scan", false).unwrap()["event"],
            "source_download_paused"
        );
        assert_eq!(fs::read(work.join("recorded-media.part")).unwrap(), b"abcd");
        assert!(server
            .queue
            .update_item_progress_checked(
                &original,
                &item,
                &observed_progress(&item, ItemStatus::Downloaded, 8, Some(8), None),
                super::super::now_ms()
            )
            .is_err());
        assert!(server
            .start_download("scan".into(), "generic:one".into(), false)
            .is_err());
        assert_eq!(
            begin_download(&mut server, "generic:one", "complete", true),
            work
        );
        let value = finish_download(&mut server);
        assert_eq!(value["resumed"], true);
        assert_eq!(
            server.queue.item("scan", "generic:one").unwrap().status,
            ItemStatus::Downloaded
        );
    }

    #[test]
    fn native_download_failure_and_cancel_leave_siblings_executable() {
        for cancel in [false, true] {
            let temp = Temporary::new();
            let mut server = download_server(&temp);
            begin_download(
                &mut server,
                "generic:one",
                if cancel { "pause" } else { "fail" },
                false,
            );
            pump(&mut server);
            if cancel {
                assert_eq!(
                    server.halt_download("scan", true).unwrap()["event"],
                    "source_download_cancelled"
                );
            } else {
                let wire = server
                    .active
                    .as_ref()
                    .unwrap()
                    .events
                    .recv_timeout(Duration::from_secs(10))
                    .unwrap();
                let value = server.event(wire).unwrap().unwrap();
                assert_eq!(value["code"], "NETWORK");
                assert_eq!(value["retryable"], true);
                assert_eq!(value["automatic_retry"], false);
            }
            let item = server.queue.item("scan", "generic:one").unwrap();
            assert_eq!(
                item.status,
                if cancel {
                    ItemStatus::Cancelled
                } else {
                    ItemStatus::Failed
                }
            );
            assert_eq!(item.downloaded_bytes, 4);
            assert!(item.media_path.is_none());
            assert!(server
                .start_download("scan".into(), "generic:one".into(), true)
                .is_err());
            begin_download(&mut server, "generic:two", "complete", false);
            assert_eq!(finish_download(&mut server)["event"], "source_downloaded");
            assert_eq!(server.queue.item("scan", "generic:one").unwrap(), item);
            assert_eq!(
                server.queue.scan("scan").unwrap().failed_count,
                if cancel { 0 } else { 1 }
            );
        }
    }

    #[test]
    fn native_interrupted_download_reopens_with_new_epoch_and_original_producer() {
        let temp = Temporary::new();
        let mut server = download_server(&temp);
        let work = begin_download(&mut server, "generic:one", "pause", false);
        pump(&mut server);
        let old = server.active.as_ref().unwrap().download.as_ref().unwrap();
        let (original, item) = (old.original.clone(), old.item.clone());
        server.active.as_mut().unwrap().child.kill().unwrap();
        server.active.as_mut().unwrap().child.wait().unwrap();
        drop(server); // Actual OS owner/store reopen; interrupted item stays resumable.
        let mut reopened = Server::open(temp.recorded_runtime()).unwrap();
        assert!(
            reopened.queue.scan("scan").unwrap().dispatch_revision > original.dispatch_revision
        );
        assert!(reopened
            .queue
            .update_item_progress_checked(
                &original,
                &item,
                &observed_progress(&item, ItemStatus::Downloaded, 8, Some(8), None),
                super::super::now_ms()
            )
            .is_err());
        assert_eq!(fs::read(work.join("recorded-media.part")).unwrap(), b"abcd");
        reopened.runtime.source_sha = "c".repeat(40);
        assert!(reopened
            .start_download("scan".into(), "generic:one".into(), true)
            .is_err());
        assert!(reopened.active.is_none());
        assert_eq!(reopened.queue.item("scan", "generic:one").unwrap(), item);
        reopened.runtime.source_sha = "b".repeat(40);
        assert_eq!(
            begin_download(&mut reopened, "generic:one", "complete", true),
            work
        );
        assert_eq!(finish_download(&mut reopened)["resumed"], true);
    }

    #[test]
    fn native_download_refuses_false_hash_and_unsuccessful_worker_exit() {
        for mode in ["bad-hash", "bad-exit"] {
            let temp = Temporary::new();
            let mut server = download_server(&temp);
            begin_download(&mut server, "generic:one", mode, false);
            pump(&mut server);
            pump(&mut server);
            let wire = server
                .active
                .as_ref()
                .unwrap()
                .events
                .recv_timeout(Duration::from_secs(10))
                .unwrap();
            if mode == "bad-exit" {
                assert!(server.event(wire).is_err());
            } else {
                server.event(wire).unwrap();
                assert!(finish_verification(&mut server).is_err());
            }
            assert_eq!(server.fail_active()["event"], "source_download_failed");
            let item = server.queue.item("scan", "generic:one").unwrap();
            assert_eq!(item.status, ItemStatus::Failed);
            assert!(item.media_path.is_none() && item.content_hash.is_none());
            assert_eq!(server.queue.scan("scan").unwrap().completed_count, 0);
        }
    }

    #[test]
    fn native_download_recovers_verified_publication_after_rejected_commit() {
        let temp = Temporary::new();
        let mut server = download_server(&temp);
        begin_download(&mut server, "generic:one", "complete", false);
        pump(&mut server);
        pump(&mut server);
        pump(&mut server);
        let download = server.active.as_ref().unwrap().download.as_ref().unwrap();
        let destination = download.final_receipt.as_ref().unwrap().destination.clone();
        // Advance the durable row behind this callback before publication. The
        // file may publish, but the old snapshot cannot bless it in SQLite.
        server
            .queue
            .update_item_progress_checked(
                &download.original,
                &download.item,
                &observed_progress(&download.item, ItemStatus::Downloading, 4, Some(8), None),
                super::super::now_ms(),
            )
            .unwrap();
        assert!(finish_verification(&mut server).is_err());
        assert_eq!(fs::read(&destination).unwrap(), b"abcdefgh");
        assert_eq!(
            server.fail_active()["code"],
            "SOURCE_DOWNLOAD_DISPATCH_REJECTED"
        );
        assert_eq!(
            server.queue.item("scan", "generic:one").unwrap().status,
            ItemStatus::Downloading
        );
        drop(server);
        let mut reopened = Server::open(temp.recorded_runtime()).unwrap();
        begin_download(&mut reopened, "generic:one", "complete", true);
        assert_eq!(finish_download(&mut reopened)["event"], "source_downloaded");
        assert_eq!(
            PathBuf::from(
                reopened
                    .queue
                    .item("scan", "generic:one")
                    .unwrap()
                    .media_path
                    .unwrap()
            )
            .canonicalize()
            .unwrap(),
            destination.canonicalize().unwrap()
        );
    }

    #[test]
    fn native_download_refuses_foreign_packet_without_mutating_progress() {
        let temp = Temporary::new();
        let mut server = download_server(&temp);
        begin_download(&mut server, "generic:one", "pause", false);
        let download = server.active.as_ref().unwrap().download.as_ref().unwrap();
        let item = download.item.clone();
        let record = download.original.clone();
        let base = json!({"kind":"source-download-progress","schema_version":1,"job_id":"scan","stage_id":STAGE,
            "producer_fingerprint":record.producer_fingerprint,"dispatch_revision":download.origin_revision,
            "identity_key":item.identity_key,"source_id":item.source_id,"source_url":item.source_url,"downloaded_bytes":4,"total_bytes":8});
        for (key, value) in [
            ("job_id", json!("foreign")),
            ("producer_fingerprint", json!("0".repeat(64))),
            ("dispatch_revision", json!(record.dispatch_revision + 1)),
            ("identity_key", json!("generic:two")),
            ("source_id", json!("two")),
            ("source_url", json!("https://example.test/two")),
            ("downloaded_bytes", json!(9)),
        ] {
            let mut packet = base.clone();
            packet[key] = value;
            assert!(server
                .download_packet(decode(packet), "source-packet-invalid.json".into())
                .is_err());
            assert_eq!(server.queue.scan("scan").unwrap(), record);
            assert_eq!(server.queue.item("scan", "generic:one").unwrap(), item);
        }
        server.halt_download("scan", true).unwrap();
        assert!(server
            .download_packet(decode(base), "source-packet-late.json".into())
            .is_err());
        assert_eq!(
            server.queue.item("scan", "generic:one").unwrap().status,
            ItemStatus::Cancelled
        );
    }

    #[test]
    fn native_verified_media_refuses_replaced_path_after_hash() {
        let temp = Temporary::new();
        let path = temp.0.join("media.mp4");
        fs::write(&path, b"abcdefgh").unwrap();
        let verifier = verify_media(path.clone(), 8, hex_digest(b"abcdefgh"));
        let media = verifier
            .result
            .recv_timeout(Duration::from_secs(10))
            .unwrap()
            .unwrap();
        #[cfg(windows)]
        assert!(fs::write(&path, b"badbytes").is_err());
        fs::rename(&path, temp.0.join("original.mp4")).unwrap();
        fs::write(&path, b"abcdefgh").unwrap(); // Same bytes/size, a different file.
        assert!(checked_media(&media, &path, 8, true).is_err());
        drop(media);
        drop(verifier);
    }

    #[test]
    fn native_preparation_can_be_cancelled_without_a_false_durable_scan() {
        let temp = Temporary::new();
        let mut server = Server::open(temp.recorded_runtime()).unwrap();
        server
            .request(Request::Start {
                scan_id: "scan".into(),
                provider_id: "generic".into(),
                source_ref: producer().source_ref,
                page_size: 2,
                max_items: 100,
            })
            .unwrap();
        let value = server.halt("scan", true).unwrap();
        assert_eq!(value["event"], "source_preparation_stopped");
        assert!(server.active.is_none());
        assert!(server.queue.scan("scan").is_err());
        drop(server);
    }

    #[test]
    #[cfg(windows)]
    fn native_worker_observes_normal_paths_after_canonical_windows_launch() {
        let temp = Temporary::new();
        let mut runtime = temp.recorded_runtime();
        fs::write(&runtime.worker, r#"import json,sys
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'worker':__file__,
    'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,'no_bytecode':sys.dont_write_bytecode}))
"#).unwrap();
        runtime.python = runtime.python.canonicalize().unwrap();
        runtime.worker = runtime.worker.canonicalize().unwrap();
        let output = owned_worker_command(&runtime, &runtime.data)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let value: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            PathBuf::from(value["executable"].as_str().unwrap()),
            super::super::external_runtime_path(runtime.python.clone())
        );
        assert_eq!(
            PathBuf::from(value["worker"].as_str().unwrap()),
            super::super::external_runtime_path(runtime.worker.clone())
        );
        assert!(!value["prefix"].as_str().unwrap().starts_with(r"\\?\"));
        assert_eq!(value["isolated"], 1);
        assert_eq!(value["no_site"], 1);
        assert_eq!(value["no_bytecode"], true);
    }

    fn admission_inventory(temp: &Temporary) -> (Runtime, Vec<Value>) {
        let runtime = temp.runtime();
        let mut entries = Vec::new();
        for (relative, raw) in [
            ("runtime/python.exe", b"recorded-python".as_slice()),
            (
                "app/engine/dubflow/download/enumeration/worker.py",
                b"recorded-worker".as_slice(),
            ),
            (
                "app/engine/dubflow/download/source_adapter.py",
                b"recorded-import".as_slice(),
            ),
        ] {
            let path = runtime.root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, raw).unwrap();
            entries.push(json!({"path":relative,"size_bytes":raw.len().to_string(),"sha256":hex_digest(raw)}));
        }
        (runtime, entries)
    }

    fn admission_manifest(runtime: &Runtime, entries: &[Value]) -> String {
        let raw = serde_json::to_vec(&json!({"schema_version":1,"source_sha":"b".repeat(40),"version":"0.1.0-test","artifacts":entries})).unwrap();
        fs::write(runtime.root.join("release-manifest.json"), &raw).unwrap();
        hex_digest(&raw)
    }

    fn fully_admitted_inventory(
        temp: &Temporary,
    ) -> (Runtime, std::collections::HashMap<String, (u64, String)>) {
        let (runtime, entries) = admission_inventory(temp);
        let expected = admission_manifest(&runtime, &entries);
        let admitted = Runtime::admit(runtime.root, runtime.data, expected).unwrap();
        let inventory = entries
            .iter()
            .map(|entry| {
                (
                    entry["path"].as_str().unwrap().to_owned(),
                    (
                        entry["size_bytes"].as_str().unwrap().parse().unwrap(),
                        entry["sha256"].as_str().unwrap().to_owned(),
                    ),
                )
            })
            .collect();
        (admitted, inventory)
    }

    #[test]
    fn native_final_inventory_refuses_post_hash_missing_extra_or_resized_files() {
        for mutation in ["missing", "extra", "resized"] {
            let temp = Temporary::new();
            // The complete real hash walk succeeds before the mutation. Then
            // exercise the exact final verifier used at the admission boundary.
            let (runtime, inventory) = fully_admitted_inventory(&temp);
            let path = runtime
                .root
                .join("app/engine/dubflow/download/source_adapter.py");
            assert!(path.starts_with(&temp.0));
            match mutation {
                "missing" => fs::remove_file(path).unwrap(),
                "extra" => fs::write(runtime.root.join("late-import.py"), b"unverified").unwrap(),
                "resized" => fs::write(path, b"recorded-import-extended").unwrap(),
                _ => unreachable!(),
            }
            assert!(
                verify_final_inventory(&runtime.root, &inventory).is_err(),
                "{mutation}"
            );
        }
    }

    #[test]
    #[cfg(unix)]
    fn native_final_inventory_refuses_post_hash_link_changes() {
        for directory in [false, true] {
            let temp = Temporary::new();
            let (runtime, inventory) = fully_admitted_inventory(&temp);
            let link = runtime.root.join(if directory {
                "app/engine"
            } else {
                "app/engine/dubflow/download/source_adapter.py"
            });
            let target = temp.0.join("late-identical-external");
            assert!(link.starts_with(&temp.0) && target.starts_with(&temp.0));
            fs::rename(&link, &target).unwrap();
            std::os::unix::fs::symlink(&target, &link).unwrap();
            assert!(verify_final_inventory(&runtime.root, &inventory).is_err());
        }
    }

    #[test]
    #[cfg(windows)]
    fn native_final_inventory_refuses_post_hash_directory_junction() {
        let temp = Temporary::new();
        let (runtime, inventory) = fully_admitted_inventory(&temp);
        let link = runtime.root.join("app/engine");
        let target = temp.0.join("late-identical-external");
        assert!(link.starts_with(&temp.0) && target.starts_with(&temp.0));
        fs::rename(&link, &target).unwrap();
        let output = Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg(&target)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let refused = verify_final_inventory(&runtime.root, &inventory).is_err();
        fs::remove_dir(&link).unwrap(); // Remove only our junction before fixture cleanup.
        assert!(refused);
    }

    #[test]
    fn native_inventory_rejects_incomplete_tampered_or_ambiguous_entries() {
        for defect in [
            "missing",
            "same_size_tamper",
            "wrong_size",
            "wrong_hash",
            "duplicate",
            "case_duplicate",
            "traversal",
            "unexpected",
            "missing_worker",
        ] {
            let temp = Temporary::new();
            let (runtime, mut entries) = admission_inventory(&temp);
            let import = runtime
                .root
                .join("app/engine/dubflow/download/source_adapter.py");
            match defect {
                "missing" => fs::remove_file(&import).unwrap(),
                "same_size_tamper" => fs::write(&import, b"tampered-import").unwrap(),
                "wrong_size" => entries[2]["size_bytes"] = json!("900"),
                "wrong_hash" => entries[2]["sha256"] = json!("0".repeat(64)),
                "duplicate" => entries.push(entries[2].clone()),
                "case_duplicate" => {
                    let mut entry = entries[2].clone();
                    entry["path"] = json!(entry["path"].as_str().unwrap().to_uppercase());
                    entries.push(entry);
                }
                "traversal" => entries[2]["path"] = json!("../outside.py"),
                "unexpected" => {
                    fs::write(runtime.root.join("unexpected.py"), b"shadow-import").unwrap()
                }
                "missing_worker" => {
                    entries.remove(1);
                }
                _ => unreachable!(),
            }
            let expected = admission_manifest(&runtime, &entries);
            assert!(
                Runtime::admit(runtime.root, runtime.data, expected).is_err(),
                "{defect}"
            );
        }
    }

    #[test]
    fn native_full_nested_inventory_is_verified_without_skipping_files() {
        let temp = Temporary::new();
        let (runtime, mut entries) = admission_inventory(&temp);
        for index in 0u32..2048 {
            let relative = format!("app/nested/level-{}/imports/module-{index}.py", index % 64);
            let path = runtime.root.join(&relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            let raw = index.to_le_bytes();
            fs::write(path, raw).unwrap();
            entries.push(json!({"path":relative,"size_bytes":raw.len().to_string(),"sha256":hex_digest(&raw)}));
        }
        let expected = admission_manifest(&runtime, &entries);
        assert!(
            Runtime::admit(runtime.root.clone(), runtime.data.clone(), expected.clone()).is_ok()
        );
        fs::write(
            runtime
                .root
                .join("app/nested/level-63/imports/module-2047.py"),
            [0, 0, 0, 0],
        )
        .unwrap();
        assert!(Runtime::admit(runtime.root, runtime.data, expected).is_err());
    }

    #[test]
    #[cfg(unix)]
    fn native_inventory_refuses_identical_linked_files_and_directories() {
        for directory in [false, true] {
            let temp = Temporary::new();
            let (runtime, entries) = admission_inventory(&temp);
            let expected = admission_manifest(&runtime, &entries);
            let link = runtime.root.join(if directory {
                "app/engine"
            } else {
                "app/engine/dubflow/download/source_adapter.py"
            });
            let target = temp.0.join("external-identical");
            assert!(link.starts_with(&temp.0) && target.starts_with(&temp.0));
            fs::rename(&link, &target).unwrap();
            std::os::unix::fs::symlink(&target, &link).unwrap();
            assert!(Runtime::admit(runtime.root, runtime.data, expected).is_err());
        }
    }

    #[test]
    #[cfg(windows)]
    fn native_inventory_refuses_identical_directory_junction() {
        let temp = Temporary::new();
        let (runtime, entries) = admission_inventory(&temp);
        let expected = admission_manifest(&runtime, &entries);
        let link = runtime.root.join("app/engine");
        let target = temp.0.join("external-identical");
        assert!(link.starts_with(&temp.0) && target.starts_with(&temp.0));
        fs::rename(&link, &target).unwrap();
        let output = Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg(&target)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let refused = Runtime::admit(runtime.root, runtime.data, expected).is_err();
        fs::remove_dir(&link).unwrap(); // Remove junction itself before fixture cleanup.
        assert!(refused);
    }

    #[test]
    fn native_runtime_checks_import_inventory_before_child_execution() {
        let temp = Temporary::new();
        let runtime = temp.runtime();
        let files = [
            ("runtime/python.exe", b"recorded-python".as_slice()),
            (
                "app/engine/dubflow/download/enumeration/worker.py",
                b"recorded-worker".as_slice(),
            ),
            (
                "app/engine/dubflow/download/source_adapter.py",
                b"recorded-import".as_slice(),
            ),
        ];
        let mut entries = Vec::new();
        for (relative, raw) in files {
            let path = runtime.root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, raw).unwrap();
            entries.push(json!({"path":relative,"size_bytes":raw.len().to_string(),"sha256":hex_digest(raw)}));
        }
        let manifest=serde_json::to_vec(&json!({"schema_version":1,"source_sha":"b".repeat(40),"version":"0.1.0-test","artifacts":entries})).unwrap();
        fs::write(runtime.root.join("release-manifest.json"), &manifest).unwrap();
        let expected = hex_digest(&manifest);
        assert!(
            Runtime::admit(runtime.root.clone(), runtime.data.clone(), expected.clone()).is_ok()
        );
        fs::write(
            runtime
                .root
                .join("app/engine/dubflow/download/source_adapter.py"),
            b"changed-import",
        )
        .unwrap();
        assert!(
            Runtime::admit(runtime.root.clone(), runtime.data.clone(), expected.clone()).is_err()
        );
        fs::write(
            runtime
                .root
                .join("app/engine/dubflow/download/source_adapter.py"),
            b"recorded-import",
        )
        .unwrap();
        fs::write(runtime.root.join("unexpected.py"), b"shadow-import").unwrap();
        assert!(Runtime::admit(runtime.root.clone(), runtime.data.clone(), expected).is_err());
    }

    #[test]
    fn native_parent_cannot_claim_a_different_installed_runtime() {
        let temp = Temporary::new();
        let runtime = temp.runtime();
        let executable = runtime.root.join("app/bin/dubflow-supervisor.exe");
        fs::create_dir_all(executable.parent().unwrap()).unwrap();
        fs::write(&executable, b"owned-parent").unwrap();
        assert!(owned_supervisor(&runtime.root, &executable).is_ok());
        let foreign = temp.0.join("foreign-supervisor.exe");
        fs::write(&foreign, b"owned-parent").unwrap();
        assert!(owned_supervisor(&runtime.root, &foreign).is_err());
    }

    #[test]
    fn native_recorded_stdio_commits_pauses_and_restarts_from_page_n() {
        let temp = Temporary::new();
        let runtime = temp.recorded_runtime();
        let mut server = Server::open(runtime).unwrap();
        server
            .request(Request::Start {
                scan_id: "scan".into(),
                provider_id: "generic".into(),
                source_ref: producer().source_ref,
                page_size: 2,
                max_items: 100,
            })
            .unwrap();
        pump(&mut server); // Python UTF-8 sorted producer -> Rust fingerprint.
        pump(&mut server); // Real packet/hash -> checked SQLite first page.
        let dispatched = server.active.as_ref().unwrap().original.clone().unwrap();
        assert_eq!(dispatched.cursor.as_deref(), Some("page-2"));
        let pid = server.active.as_ref().unwrap().child.id();
        let stopped = server.halt("scan", false).unwrap();
        assert_eq!(stopped["status"], "paused");
        assert!(server.active.is_none());
        assert!(server
            .queue
            .checkpoint_page_checked(&dispatched, &PageCheckpoint::default(), 3)
            .is_err());
        assert_eq!(server.queue.items("scan").unwrap().len(), 1);
        drop(server); // Close store and OS owner before actual reopen.
        let runtime = temp.recorded_runtime();
        let mut restarted = Server::open(runtime).unwrap();
        assert_eq!(
            restarted.queue.scan("scan").unwrap().cursor.as_deref(),
            Some("page-2")
        );
        restarted
            .request(Request::Resume {
                scan_id: "scan".into(),
            })
            .unwrap();
        assert_ne!(restarted.active.as_ref().unwrap().child.id(), pid);
        pump(&mut restarted);
        pump(&mut restarted);
        pump(&mut restarted);
        assert!(restarted.active.is_none());
        let record = restarted.queue.scan("scan").unwrap();
        assert_eq!(record.status, ScanStatus::Completed);
        assert_eq!(record.discovered_count, 2);
        assert_eq!(
            restarted
                .queue
                .items("scan")
                .unwrap()
                .iter()
                .map(|i| i.source_id.as_str())
                .collect::<Vec<_>>(),
            vec!["one", "two"]
        );
        drop(restarted);
    }

    #[test]
    fn native_database_owner_prevents_recovery_by_second_service() {
        let temp = Temporary::new();
        let server = Server::open(temp.runtime()).unwrap();
        original(&server.queue);
        assert!(Server::open(temp.runtime()).is_err());
        assert_eq!(
            server.queue.scan("scan").unwrap().status,
            ScanStatus::Running
        );
        drop(server);
        // The retained metadata file is not ownership. A fresh OS handle proves
        // the prior owner released its claim, then recovery invalidates dispatch.
        let reopened = Server::open(temp.runtime()).unwrap();
        assert_eq!(
            reopened.queue.scan("scan").unwrap().status,
            ScanStatus::Paused
        );
        assert_eq!(reopened.queue.scan("scan").unwrap().dispatch_revision, 2);
        drop(reopened);
    }

    #[test]
    fn native_page_rejects_cross_scope_identity_and_dispatch() {
        let queue = SourceQueue::open_in_memory().unwrap();
        let record = original(&queue);
        for (field, replacement) in [
            ("job_id", json!("other")),
            ("stage_id", json!("local-file")),
            ("producer_fingerprint", json!("c".repeat(64))),
            ("dispatch_revision", json!(record.dispatch_revision + 1)),
            ("request_cursor", json!("foreign")),
        ] {
            let mut value = page_value(&record);
            value[field] = replacement;
            assert!(checked_page(decode(value), &record, 2).is_err());
        }
        let mut value = page_value(&record);
        value["page"]["items"][0]["identity"]["identity_key"] = json!("generic:other");
        assert!(checked_page(decode(value), &record, 2).is_err());
        assert_eq!(queue.scan("scan").unwrap().discovered_count, 0);
    }

    #[test]
    fn native_packet_rejects_hash_path_unknown_members_and_budget() {
        let temp = Temporary::new();
        let queue = SourceQueue::open_in_memory().unwrap();
        let record = original(&queue);
        let raw = serde_json::to_vec(&page_value(&record)).unwrap();
        let name = "source-packet-0123456789abcdef0123456789abcdef.json";
        fs::write(temp.0.join(name), &raw).unwrap();
        assert!(packet(&temp.0, name, &format!("sha256:{}", hex_digest(&raw))).is_ok());
        assert!(packet(&temp.0, name, &format!("sha256:{}", "0".repeat(64))).is_err());
        assert!(packet(
            &temp.0,
            "../source-packet-0123456789abcdef0123456789abcdef.json",
            &format!("sha256:{}", hex_digest(&raw))
        )
        .is_err());
        let mut value = page_value(&record);
        value["media_url"] = json!("private");
        assert!(serde_json::from_value::<Packet>(value).is_err());
        let file = File::create(temp.0.join(name)).unwrap();
        file.set_len(MAX_PACKET + 1).unwrap();
        drop(file);
        assert!(packet(&temp.0, name, &format!("sha256:{}", hex_digest(&raw))).is_err());
    }

    #[test]
    fn native_admission_refuses_changed_pins_and_missing_legacy_record() {
        let temp = Temporary::new();
        let runtime = temp.runtime();
        let queue = SourceQueue::open_in_memory().unwrap();
        let record = original(&queue);
        assert!(load_admission(&runtime.data, &record).is_err());
        let original = producer();
        save_admission(&runtime.data, "scan", &original).unwrap();
        assert_eq!(load_admission(&runtime.data, &record).unwrap().page_size, 2);
        let mut changed = original.clone();
        changed.page_size = 3;
        assert!(save_admission(&runtime.data, "scan", &changed).is_err());
        let spec = Spec {
            id: "scan".into(),
            provider: record.provider_id.clone(),
            reference: record.source_ref.clone(),
            page_size: 2,
            maximum: 100,
            existing: true,
        };
        let mut changed_runtime = temp.runtime();
        changed_runtime.manifest_hash = "c".repeat(64);
        assert!(original.validate(&changed_runtime, &spec).is_err());
        fs::write(
            admission_path(&runtime.data, "scan").unwrap(),
            serde_json::to_vec(&changed).unwrap(),
        )
        .unwrap();
        assert!(load_admission(&runtime.data, &record).is_err());
        assert_eq!(
            queue.scan("scan").unwrap().producer_fingerprint,
            record.producer_fingerprint
        );
    }

    #[test]
    fn native_refuses_private_item_urls_and_sanitizes_failures() {
        let queue = SourceQueue::open_in_memory().unwrap();
        let record = original(&queue);
        for url in [
            "https://user:secret@example.test/video",
            "https://example.test/video?x-%61mz-signature=private",
            "https://example.test/video?secret=private",
        ] {
            let mut value = page_value(&record);
            value["page"]["items"][0]["identity"]["canonical_url"] = json!(url);
            assert!(checked_page(decode(value), &record, 2).is_err());
        }
        let mut value = page_value(&record);
        value["page"]["failures"] = json!([{"source_id":"missing","code":"PRIVATE","condition":"upstream secret diagnostics","retryable":false}]);
        let page = checked_page(decode(value), &record, 2).unwrap();
        assert_eq!(
            page.failures[0].error_message,
            "source item unavailable (PRIVATE)"
        );
        let result = queue.checkpoint_page_checked(&record, &page, 3).unwrap();
        assert_eq!(result.failed_count, 1);
        assert_eq!(result.discovered_count, 1);
    }

    #[test]
    fn native_stdio_and_requests_are_bounded_and_closed() {
        assert!(bounded_line(&mut io::Cursor::new(vec![b'x'; MAX_LINE_BYTES + 1])).is_err());
        assert!(bounded_line(&mut io::Cursor::new(b"unterminated")).is_err());
        assert!(bounded_line(&mut io::Cursor::new(b"{}\n"))
            .unwrap()
            .is_some());
        assert!(serde_json::from_str::<Request>(
            r#"{"command":"resume","scan_id":"scan","producer_fingerprint":"replace"}"#
        )
        .is_err());
    }
}
