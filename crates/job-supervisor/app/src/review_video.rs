//! Native result-version launch, never browser path opening or playback proof.
//! The module/dispatch and existing identity-helper visibility are C1 glue.
//! Selection is read-only; the only external side effect is the final fixed
//! Windows association request. Parent cancellation/timeout owns this reader;
//! once activation starts an external player cannot be revoked or auto-retried.
//! Tauri directly spawns/kills this reader; it does not assign a per-reader Job.
//! Any enclosing app/QA Job must be identified separately. An acknowledgement
//! does not prove player survival after retirement; never request breakaway.
use super::review_assets::{MixStorageBudget, native_identity, regular};
use super::review_document::{closed, decode, text, tick};
use super::review_pin::{CommittedExport, committed_export};
use super::review_publication::RecoveryWalk;
use super::review_retention::{NativeMixStore, NativeOutputPin};
use super::{JobExecutionGuard, SupervisorError, SupervisorResult};
use dubflow_job_state::{DurableStore, review_graph::ReviewGraphState};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const REQUEST_LIMIT: usize = 64 * 1024;
const METADATA_LIMIT: usize = 64 * 1024 * 1024;
// Same absolute native read policy as review_preview/review_export. Never reset
// this clock after selection, hashing, COM work, or the launch boundary.
const READ_SECONDS: u64 = 720;
const HASH_BLOCK: usize = 32768;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn check(deadline: Instant, cancelled: &mut impl FnMut() -> bool) -> SupervisorResult<()> {
    if cancelled() {
        return Err(invalid(
            "result-video request cancelled before acknowledgement",
        ));
    }
    if Instant::now() >= deadline {
        return Err(invalid("result-video original native deadline exhausted"));
    }
    Ok(())
}
fn hash_text(value: &str) -> bool {
    value.len() == 71
        && value.starts_with("sha256:")
        && value[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct CurrentToken {
    job_id: String,
    revision: String,
    snapshot_sha256: String,
    graph_sha256: String,
    original_manifest_sha256: String,
}
impl CurrentToken {
    fn validate(&self, job: &str) -> SupervisorResult<()> {
        let revision = self
            .revision
            .parse::<u64>()
            .map_err(|_| invalid("result-video revision is not canonical"))?;
        if self.job_id != job
            || revision > i64::MAX as u64
            || revision.to_string() != self.revision
            || !hash_text(&self.snapshot_sha256)
            || !hash_text(&self.graph_sha256)
            || !hash_text(&self.original_manifest_sha256)
        {
            return Err(invalid(
                "result-video requires the exact complete current token",
            ));
        }
        Ok(())
    }
    fn matches(&self, state: &ReviewGraphState) -> bool {
        self.job_id == state.token.job_id
            && self.revision == state.token.revision.to_string()
            && self.snapshot_sha256 == state.token.snapshot_sha256
            && self.graph_sha256 == state.token.graph_sha256
            && self.original_manifest_sha256 == state.token.original_manifest_sha256
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Expected {
    Original {
        revision: u64,
        original_manifest_sha256: String,
    },
    Current {
        current_token: CurrentToken,
    },
}
impl Expected {
    fn original_hash(&self) -> &str {
        match self {
            Self::Original {
                original_manifest_sha256,
                ..
            } => original_manifest_sha256,
            Self::Current { current_token } => &current_token.original_manifest_sha256,
        }
    }
    fn validate(&self, job: &str) -> SupervisorResult<()> {
        match self {
            Self::Original {
                revision: 0,
                original_manifest_sha256,
            } if hash_text(original_manifest_sha256) => Ok(()),
            Self::Current { current_token } => current_token.validate(job),
            _ => Err(invalid(
                "original result-video requires revision zero and native original pin",
            )),
        }
    }
    fn head_matches(&self, head: Option<&ReviewGraphState>) -> bool {
        match (self, head) {
            (Self::Original { revision: 0, .. }, None) => true,
            (Self::Current { current_token }, Some(head)) => current_token.matches(head),
            _ => false,
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema_version: u32,
    kind: String,
    job_id: String,
    expected: Expected,
}
impl Request {
    fn read(raw: &str) -> SupervisorResult<Self> {
        if raw.is_empty() || raw.len() > REQUEST_LIMIT {
            return Err(invalid("result-video request exceeds its metadata bound"));
        }
        let input: Self = serde_json::from_value(decode(raw, REQUEST_LIMIT)?)?;
        input.validate()?;
        Ok(input)
    }
    fn validate(&self) -> SupervisorResult<()> {
        if self.schema_version != 1 || self.kind != "review_native_result_video_request" {
            return Err(invalid("unknown native result-video request"));
        }
        super::validate_job_id(&self.job_id)?;
        self.expected.validate(&self.job_id)
    }
}

fn arguments(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<(PathBuf, Request)> {
    let mut data = None;
    let mut request = None;
    while let Some(flag) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| invalid("missing result-video argument"))?;
        match flag.to_str() {
            Some("--data-root") if data.is_none() => data = Some(PathBuf::from(value)),
            Some("--request-json") if request.is_none() => {
                let raw = value
                    .into_string()
                    .map_err(|_| invalid("result-video JSON is not UTF8"))?;
                request = Some(Request::read(&raw)?);
            }
            _ => return Err(invalid("unknown or duplicate result-video argument")),
        }
    }
    let data = data.ok_or_else(|| invalid("result-video requires native data root"))?;
    if !data.is_absolute() || data.components().any(|p| matches!(p, Component::ParentDir)) {
        return Err(invalid(
            "result-video native data root must be absolute without traversal",
        ));
    }
    Ok((
        data,
        request.ok_or_else(|| invalid("result-video requires closed native request"))?,
    ))
}

struct PinnedNode {
    file: File,
    path: PathBuf,
    identity: (u64, u64),
    directory: bool,
}
impl PinnedNode {
    fn open(path: &Path, directory: bool, share_write: bool) -> SupervisorResult<Self> {
        super::review_pin::plain(path)?;
        let before = if directory {
            if !std::fs::symlink_metadata(path)?.is_dir() {
                return Err(invalid("result-video directory changed type"));
            }
            None
        } else {
            Some(regular(path)?)
        };
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // No delete sharing pins names. Media also denies other data
            // writers; the existing DB permits unrelated jobs to write.
            options
                .share_mode(if share_write { 3 } else { 1 })
                .custom_flags(if directory { 0x02200000 } else { 0x00200000 });
        }
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(0x20000 | 0x800 | if directory { 0x10000 } else { 0 });
        }
        #[cfg(not(windows))]
        let _ = share_write;
        let file = options.open(path)?;
        let metadata = file.metadata()?;
        if if directory {
            !metadata.is_dir()
        } else {
            !metadata.is_file()
        } {
            return Err(invalid("result-video held object changed type"));
        }
        let identity = native_identity(&file, directory)?;
        if let Some(before) = &before {
            if native_identity(before, false)? != identity {
                return Err(invalid("result-video file changed during native open"));
            }
        }
        let node = Self {
            file,
            path: path.to_path_buf(),
            identity,
            directory,
        };
        node.recheck()?;
        Ok(node)
    }
    fn recheck(&self) -> SupervisorResult<()> {
        super::review_pin::plain(&self.path)?;
        if native_identity(&self.file, self.directory)? != self.identity {
            return Err(invalid("result-video held identity changed"));
        }
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.custom_flags(if self.directory {
                0x02200000
            } else {
                0x00200000
            });
        }
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(0x20000 | 0x800 | if self.directory { 0x10000 } else { 0 });
        }
        let current = options.open(&self.path)?;
        let metadata = current.metadata()?;
        if (if self.directory {
            !metadata.is_dir()
        } else {
            !metadata.is_file()
        }) || native_identity(&current, self.directory)? != self.identity
        {
            return Err(invalid(
                "result-video pathname no longer matches its held identity",
            ));
        }
        Ok(())
    }
}

struct ExistingStore {
    // SQLite closes before execution exclusion and path pins are released.
    store: DurableStore,
    _execution: JobExecutionGuard,
    db: PinnedNode,
    control: PinnedNode,
    data: PinnedNode,
}
impl ExistingStore {
    fn open(data: &Path, job: &str) -> SupervisorResult<Self> {
        let data = PinnedNode::open(data, true, true)?;
        let control = PinnedNode::open(&data.path.join("control"), true, true)?;
        // This MUST succeed before acquire(), whose legacy API can create an
        // absent DB. Windows no-delete pins prevent that absence/recreation.
        let db = PinnedNode::open(&control.path.join("jobs.sqlite3"), false, true)?;
        if data.identity.0 != control.identity.0 || control.identity.0 != db.identity.0 {
            return Err(invalid(
                "result-video existing control/database changed device",
            ));
        }
        let execution = JobExecutionGuard::acquire(&db.path, job)?;
        db.recheck()?;
        control.recheck()?;
        data.recheck()?;
        let store = DurableStore::open_read_only(&db.path)?;
        Ok(Self {
            store,
            _execution: execution,
            db,
            control,
            data,
        })
    }
    fn recheck(&self) -> SupervisorResult<()> {
        self.data.recheck()?;
        self.control.recheck()?;
        self.db.recheck()
    }
}

struct Source {
    path: PathBuf,
    sha256: String,
    bytes: u64,
    sealed: bool,
    output: NativeOutputPin,
}
fn original_source(original: &CommittedExport) -> SupervisorResult<Source> {
    let manifest = PathBuf::from(text(&original.pin["manifest_path"], 32767)?);
    let root = manifest
        .parent()
        .ok_or_else(|| invalid("result-video original output root missing"))?;
    let role = &original.manifest["artifacts"]["final_video"];
    closed(role, &["path", "sha256", "size_bytes"])?;
    let expected = root.join("final_vi.mp4");
    if PathBuf::from(text(&role["path"], 32767)?) != expected {
        return Err(invalid(
            "result-video original role escaped its exact native output",
        ));
    }
    let sha256 = text(&role["sha256"], 71)?.to_owned();
    let bytes = role["size_bytes"]
        .as_u64()
        .filter(|n| *n != 0)
        .ok_or_else(|| invalid("result-video original role size missing"))?;
    if !hash_text(&sha256) {
        return Err(invalid("result-video original role hash invalid"));
    }
    let output = NativeOutputPin::read(root)?;
    Ok(Source {
        path: output.root().join("final_vi.mp4"),
        sha256,
        bytes,
        sealed: false,
        output,
    })
}
fn select(
    store: &DurableStore,
    data: &Path,
    head: Option<&ReviewGraphState>,
    original: &CommittedExport,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<Source> {
    check(deadline, cancelled)?;
    let Some(state) = head else {
        return original_source(original);
    };
    let graph = decode(&state.graph_json, METADATA_LIMIT)?;
    let mut walk = RecoveryWalk::new(deadline);
    if graph["kind"] == "review_bound_original_graph" {
        // Initialized/undo-restored original remains the exact CURRENT route.
        super::review_effective::read_current_with_walk(store, data, state, &mut walk)?;
        check(deadline, cancelled)?;
        return original_source(original);
    }
    // The existing retained-only export admission is unchanged. It re-admits
    // physical current roles but does NOT copy/publish an export.
    let admitted = super::review_export::prepare_with_walk(store, data, state, &mut walk)?;
    let rows = admitted.packet()["artifacts"]
        .as_array()
        .ok_or_else(|| invalid("result-video admitted render inventory missing"))?;
    let mut selected = rows.iter().filter(|r| r["role"] == "final_video");
    let role = selected
        .next()
        .ok_or_else(|| invalid("result-video current role missing"))?;
    if selected.next().is_some() {
        return Err(invalid("result-video current role is ambiguous"));
    }
    let output = NativeOutputPin::read(admitted.output_pin().root())?;
    if output.volume_id() != admitted.output_pin().volume_id() {
        return Err(invalid("result-video selected output volume changed"));
    }
    let media = NativeMixStore::open_existing(
        &output,
        &state.token.job_id,
        MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    // Only the native admitted object supplies this locator. Its absolute hint
    // and the public finding/output_preview are never file-open authority.
    let located = media.reopen_file(&role["file"], deadline)?;
    let descriptor = located.descriptor();
    if descriptor["format"] != "mp4" {
        return Err(invalid("result-video current role is not MP4"));
    }
    let sha256 = text(&descriptor["locator"]["content_hash"], 71)?.to_owned();
    let bytes = tick(&descriptor["locator"]["size_bytes"], true)?;
    check(deadline, cancelled)?;
    Ok(Source {
        path: located.path().to_path_buf(),
        sha256,
        bytes,
        sealed: true,
        output,
    })
}

fn digest(
    input: &mut impl Read,
    expected: &str,
    size: u64,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<()> {
    if size == 0 || !hash_text(expected) {
        return Err(invalid("result-video digest lacks canonical content pins"));
    }
    let mut hasher = Sha256::new();
    let mut bytes = 0u64;
    let mut block = [0u8; HASH_BLOCK];
    loop {
        check(deadline, cancelled)?;
        let n = input.read(&mut block)?;
        if n == 0 {
            break;
        }
        bytes = bytes
            .checked_add(n as u64)
            .filter(|n| *n <= size)
            .ok_or_else(|| invalid("result-video grew beyond its native size"))?;
        hasher.update(&block[..n]);
    }
    if bytes != size || format!("sha256:{:x}", hasher.finalize()) != expected {
        return Err(invalid("result-video full held-file hash/size changed"));
    }
    check(deadline, cancelled)
}
struct HeldVideo {
    node: PinnedNode,
    directories: Vec<PinnedNode>,
    sha256: String,
    bytes: u64,
    modified: std::time::SystemTime,
    sealed: bool,
}
impl HeldVideo {
    fn read(
        source: &Source,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<Self> {
        check(deadline, cancelled)?;
        if !hash_text(&source.sha256) || source.bytes == 0 {
            return Err(invalid("result-video lacks native content pins"));
        }
        let mut directories = Vec::new();
        let mut parent = source
            .path
            .parent()
            .ok_or_else(|| invalid("result-video parent missing"))?;
        loop {
            if !parent.starts_with(source.output.root()) || directories.len() >= 32 {
                return Err(invalid(
                    "result-video escaped its bounded native output ancestry",
                ));
            }
            directories.push(PinnedNode::open(parent, true, true)?);
            if parent == source.output.root() {
                break;
            }
            parent = parent
                .parent()
                .ok_or_else(|| invalid("result-video native output ancestor missing"))?;
        }
        // Reuse the existing render-size policy and its bounded full hash. A
        // second streaming pass pins/validates the SAME handle kept for launch,
        // rather than treating the first path-based observation as a lease.
        super::review_render::verified(
            &source.path,
            &source.sha256,
            source.bytes,
            false,
            deadline,
        )?;
        check(deadline, cancelled)?;
        let mut node = PinnedNode::open(&source.path, false, false)?;
        if node.identity.0 != directories.last().unwrap().identity.0 {
            return Err(invalid("result-video file changed output device"));
        }
        let metadata = node.file.metadata()?;
        if metadata.len() != source.bytes || (source.sealed && !metadata.permissions().readonly()) {
            return Err(invalid("result-video native size/seal changed"));
        }
        let modified = metadata.modified()?;
        node.file.seek(SeekFrom::Start(0))?;
        digest(
            &mut node.file,
            &source.sha256,
            source.bytes,
            deadline,
            cancelled,
        )?;
        let held = Self {
            node,
            directories,
            sha256: source.sha256.clone(),
            bytes: source.bytes,
            modified,
            sealed: source.sealed,
        };
        held.recheck()?;
        Ok(held)
    }
    fn recheck(&self) -> SupervisorResult<()> {
        for directory in &self.directories {
            directory.recheck()?;
        }
        self.node.recheck()?;
        let fresh = regular(&self.node.path)?;
        for file in [&self.node.file, &fresh] {
            let meta = file.metadata()?;
            if meta.len() != self.bytes
                || meta.modified()? != self.modified
                || (self.sealed && !meta.permissions().readonly())
            {
                return Err(invalid(
                    "result-video held file size/time/seal changed before launch",
                ));
            }
        }
        Ok(())
    }
}

fn fence(
    context: &ExistingStore,
    request: &Request,
    pin: &Value,
    head: &Option<ReviewGraphState>,
    source: &Source,
    held: &HeldVideo,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<()> {
    check(deadline, cancelled)?;
    context.recheck()?;
    if context.store.review_graph_state(&request.job_id)? != *head {
        return Err(invalid(
            "result-video current head changed; reload without original fallback",
        ));
    }
    let original = committed_export(&context.store, &request.job_id)?;
    if original.pin != *pin || original.pin["manifest_sha256"] != request.expected.original_hash() {
        return Err(invalid("result-video original committed pin changed"));
    }
    let fresh = NativeOutputPin::read(source.output.root())?;
    if fresh.volume_id() != source.output.volume_id() || fresh.root() != source.output.root() {
        return Err(invalid("result-video native output binding changed"));
    }
    held.recheck()?;
    check(deadline, cancelled)?;
    if context.store.review_graph_state(&request.job_id)? != *head {
        return Err(invalid("result-video head changed at final launch fence"));
    }
    Ok(())
}
fn open_with(
    data: &Path,
    request: &Request,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
    launch: &mut impl FnMut(&HeldVideo) -> SupervisorResult<()>,
) -> SupervisorResult<Value> {
    check(deadline, cancelled)?;
    request.validate()?;
    let context = ExistingStore::open(data, &request.job_id)?;
    check(deadline, cancelled)?;
    let head = context.store.review_graph_state(&request.job_id)?;
    if !request.expected.head_matches(head.as_ref()) {
        return Err(invalid(
            "result-video expected graph absent/current identity mismatch",
        ));
    }
    let original = committed_export(&context.store, &request.job_id)?;
    if original.pin["manifest_sha256"] != request.expected.original_hash() {
        return Err(invalid("result-video expected original manifest changed"));
    }
    let source = select(
        &context.store,
        data,
        head.as_ref(),
        &original,
        deadline,
        cancelled,
    )?;
    let held = HeldVideo::read(&source, deadline, cancelled)?;
    fence(
        &context,
        request,
        &original.pin,
        &head,
        &source,
        &held,
        deadline,
        cancelled,
    )?;
    // Irreversible activation boundary. This is not playback completion and an
    // external application is not managed/revocable by Review session clear.
    launch(&held)?;
    if let Err(error) = fence(
        &context,
        request,
        &original.pin,
        &head,
        &source,
        &held,
        deadline,
        cancelled,
    ) {
        return Err(invalid(&format!(
            "result-video launch may already have been requested; final result is unconfirmed, do not retry automatically: {error}"
        )));
    }
    Ok(
        json!({"schema_version":1,"kind":"review_native_result_video_launch_requested",
        "job_id":request.job_id,"expected":request.expected,"role":"final_video",
        "content_sha256":held.sha256,"size_bytes":held.bytes.to_string(),"launch_requested":true,
        "managed_playback":false,"playback_verified":false}),
    )
}

/// Only fixed native data/job/identity reach this module. CLI cancellation is
/// performed by the existing bounded parent terminating this reader; a late
/// cancel cannot revoke a file association request already accepted by the OS.
pub(super) fn run(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let deadline = Instant::now() + Duration::from_secs(READ_SECONDS);
    let (data, request) = arguments(args)?;
    #[cfg(not(windows))]
    {
        let _ = (data, request, deadline);
        Err(invalid(
            "native result-video opening is not qualified on this platform",
        ))
    }
    #[cfg(windows)]
    {
        let _apartment = windows::Apartment::new()?;
        let result = open_with(&data, &request, deadline, &mut || false, &mut |held| {
            windows::launch(held, deadline)
        })?;
        super::emit_value(&mut std::io::stdout().lock(), result)
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStrExt;
    #[repr(C)]
    struct ShellInfo {
        size: u32,
        mask: u32,
        window: *mut c_void,
        verb: *const u16,
        file: *const u16,
        parameters: *const u16,
        directory: *const u16,
        show: i32,
        instance: *mut c_void,
        id_list: *mut c_void,
        class: *const u16,
        class_key: *mut c_void,
        hot_key: u32,
        icon_or_monitor: *mut c_void,
        process: *mut c_void,
    }
    #[link(name = "shell32")]
    unsafe extern "system" {
        fn ShellExecuteExW(info: *mut ShellInfo) -> i32;
    }
    #[link(name = "ole32")]
    unsafe extern "system" {
        fn CoInitializeEx(reserved: *mut c_void, options: u32) -> i32;
        fn CoUninitialize();
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    pub(super) struct Apartment;
    impl Apartment {
        pub(super) fn new() -> SupervisorResult<Self> {
            // Microsoft recommends STA|DISABLE_OLE1DDE for shell extensions.
            // S_OK and S_FALSE both require one paired CoUninitialize.
            let code = unsafe { CoInitializeEx(std::ptr::null_mut(), 2 | 4) };
            if code < 0 {
                return Err(invalid("native result-video COM apartment refused"));
            }
            Ok(Self)
        }
    }
    impl Drop for Apartment {
        fn drop(&mut self) {
            unsafe {
                CoUninitialize();
            }
        }
    }
    // Official API/ABI and the synchronous association handoff requirement:
    // https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecuteexw
    // https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow
    pub(super) fn launch(held: &HeldVideo, deadline: Instant) -> SupervisorResult<()> {
        check(deadline, &mut || false)?;
        held.recheck()?;
        let mut path: Vec<u16> = held.node.path.as_os_str().encode_wide().collect();
        if path.is_empty() || path.len() > 32767 || path.contains(&0) {
            return Err(invalid(
                "native result-video selected path cannot be passed unchanged to Windows",
            ));
        }
        path.push(0);
        let verb = [b'o' as u16, b'p' as u16, b'e' as u16, b'n' as u16, 0];
        let mut info: ShellInfo = unsafe { std::mem::zeroed() };
        info.size = std::mem::size_of::<ShellInfo>() as u32;
        // NOASYNC: this short-lived reader must finish the shell handoff.
        // NO_UI: report normal association errors instead of opening error UI.
        // NOCLOSEPROCESS: close a returned handle; DDE may validly return none.
        // Never enable DOENVSUBST, NOZONECHECKS, ASYNCOK, or WAITFORINPUTIDLE.
        info.mask = 0x100 | 0x400 | 0x40;
        info.verb = verb.as_ptr();
        info.file = path.as_ptr();
        info.show = 1; // SW_SHOWNORMAL, only after explicit user action.
        let ok = unsafe { ShellExecuteExW(&mut info) };
        let error = if ok == 0 {
            Some(std::io::Error::last_os_error())
        } else {
            None
        };
        if !info.process.is_null() {
            unsafe {
                CloseHandle(info.process);
            }
        }
        if let Some(error) = error {
            return Err(invalid(&format!(
                "native result-video association/launch refused; no playback confirmation: {error}"
            )));
        }
        // The OS call is synchronous and not cancellable here. The existing
        // parent bounds/terminates the reader. A late return is UNCERTAIN.
        if Instant::now() >= deadline {
            return Err(invalid(
                "native result-video launch returned late; it may already be requested, do not retry automatically",
            ));
        }
        Ok(())
    }
    #[cfg(all(test, target_pointer_width = "64"))]
    #[test]
    fn windows_shell_info_x64_layout_only_not_a_launch_probe() {
        assert_eq!(std::mem::size_of::<ShellInfo>(), 112);
        assert_eq!(std::mem::align_of::<ShellInfo>(), 8);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    static FIXTURE_ID: AtomicU64 = AtomicU64::new(0);
    fn sha(bytes: &[u8]) -> String {
        format!("sha256:{:x}", Sha256::digest(bytes))
    }
    fn until() -> Instant {
        Instant::now() + Duration::from_secs(30)
    }
    fn original_request(job: &str, hash: &str) -> String {
        json!({"schema_version":1,"kind":"review_native_result_video_request","job_id":job,
            "expected":{"kind":"original","revision":0,"original_manifest_sha256":hash}})
        .to_string()
    }
    fn current_request(job: &str) -> Value {
        json!({"schema_version":1,"kind":"review_native_result_video_request","job_id":job,
            "expected":{"kind":"current","current_token":{"job_id":job,"revision":"9007199254740993",
                "snapshot_sha256":sha(b"snapshot"),"graph_sha256":sha(b"graph"),"original_manifest_sha256":sha(b"manifest")}}})
    }
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "dubflow-c3-28-video-{}-{}",
                std::process::id(),
                FIXTURE_ID.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Only fresh unit fixtures, never product/old-job/QA evidence.
            let root = self.0.canonicalize().unwrap();
            assert_eq!(
                root.parent(),
                Some(std::env::temp_dir().canonicalize().unwrap().as_path())
            );
            assert!(
                root.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("dubflow-c3-28-video-")
            );
            fn unseal(path: &Path) {
                for entry in fs::read_dir(path).unwrap() {
                    let path = entry.unwrap().path();
                    let meta = fs::symlink_metadata(&path).unwrap();
                    assert!(!meta.file_type().is_symlink());
                    #[cfg(windows)]
                    {
                        use std::os::windows::fs::MetadataExt;
                        assert_eq!(meta.file_attributes() & 0x400, 0);
                    }
                    if meta.is_dir() {
                        unseal(&path);
                    } else if meta.permissions().readonly() {
                        let mut permissions = meta.permissions();
                        permissions.set_readonly(false);
                        fs::set_permissions(path, permissions).unwrap();
                    }
                }
            }
            unseal(&root);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn closed_request_rejects_paths_urls_unknown_duplicate_and_incomplete_fields() {
        let original = original_request("job-video", &sha(b"manifest"));
        assert!(Request::read(&original).is_ok());
        let mut value: Value = serde_json::from_str(&original).unwrap();
        for field in [
            "path",
            "url",
            "path_hint",
            "output_root",
            "launcher",
            "producer",
        ] {
            value[field] = json!("file:///untrusted.mp4");
            assert!(Request::read(&value.to_string()).is_err());
            value.as_object_mut().unwrap().remove(field);
        }
        value["expected"]["revision"] = json!(1);
        assert!(Request::read(&value.to_string()).is_err());
        value["expected"]["revision"] = json!(0);
        value["expected"]["path"] = json!("wrong.mp4");
        assert!(Request::read(&value.to_string()).is_err());
        assert!(Request::read(&original.replacen("{", "{\"job_id\":\"other\",", 1)).is_err());
        assert!(Request::read(&(" ".repeat(REQUEST_LIMIT) + &original)).is_err());
        let current = current_request("job-video");
        for field in [
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ] {
            let mut missing = current.clone();
            missing["expected"]["current_token"]
                .as_object_mut()
                .unwrap()
                .remove(field);
            assert!(Request::read(&missing.to_string()).is_err(), "{field}");
        }
    }

    #[test]
    fn current_revision_is_exact_decimal_and_restored_original_is_not_original_fallback() {
        use dubflow_job_state::review_graph::ReviewToken;
        let input = current_request("job-video");
        let request = Request::read(&input.to_string()).unwrap();
        let Expected::Current { current_token } = &request.expected else {
            unreachable!()
        };
        let state = ReviewGraphState {
            token: ReviewToken {
                job_id: "job-video".into(),
                revision: 9007199254740993,
                snapshot_sha256: current_token.snapshot_sha256.clone(),
                graph_sha256: current_token.graph_sha256.clone(),
                original_manifest_sha256: current_token.original_manifest_sha256.clone(),
            },
            original_artifact_id: "fixture-only".into(),
            snapshot_json: "{}".into(),
            graph_json: "{\"kind\":\"review_bound_original_graph\"}".into(),
            undo_depth: 0,
            undo_target_revision: None,
        };
        // Token predicate only: this fabricated row is NOT current media admission.
        assert!(request.expected.head_matches(Some(&state)));
        assert!(!request.expected.head_matches(None));
        let original = Request::read(&original_request(
            "job-video",
            &current_token.original_manifest_sha256,
        ))
        .unwrap();
        assert!(!original.expected.head_matches(Some(&state)));
        assert!(original.expected.head_matches(None));
        for bad in ["09007199254740993", "+1", " 1", "-0", "9223372036854775808"] {
            let mut changed = input.clone();
            changed["expected"]["current_token"]["revision"] = json!(bad);
            assert!(Request::read(&changed.to_string()).is_err());
        }
        let mut changed = input;
        changed["expected"]["current_token"]["job_id"] = json!("other-job");
        assert!(Request::read(&changed.to_string()).is_err());
    }

    #[test]
    fn cli_accepts_only_two_native_flags_and_never_creates_missing_database() {
        let fixture = Fixture::new();
        let raw = original_request("job-video", &sha(b"manifest"));
        let args = vec![
            OsString::from("--data-root"),
            fixture.0.as_os_str().to_owned(),
            OsString::from("--request-json"),
            OsString::from(&raw),
        ];
        assert!(arguments(args.clone().into_iter()).is_ok());
        for tail in [
            vec!["--path", "wrong"],
            vec!["--data-root", "other"],
            vec!["--request-json", "{}"],
            vec!["--root"],
        ] {
            let mut changed = args.clone();
            changed.extend(tail.into_iter().map(OsString::from));
            assert!(arguments(changed.into_iter()).is_err());
        }
        fs::create_dir(fixture.0.join("control")).unwrap();
        assert!(ExistingStore::open(&fixture.0, "job-video").is_err());
        assert!(!fixture.0.join("control/jobs.sqlite3").exists());
        assert!(!fixture.0.join("control/.execution-locks").exists());
        #[cfg(not(windows))]
        {
            assert!(run(args.into_iter()).is_err());
            assert!(!fixture.0.join("control/jobs.sqlite3").exists());
        }
    }

    struct Filled {
        remaining: usize,
        seen: usize,
        largest: usize,
        fail_after: Option<usize>,
    }
    impl Read for Filled {
        fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
            self.largest = self.largest.max(output.len());
            if self.fail_after.is_some_and(|n| self.seen >= n) {
                return Err(std::io::Error::other("fixture read failure"));
            }
            let n = output.len().min(self.remaining);
            output[..n].fill(42);
            self.remaining -= n;
            self.seen += n;
            Ok(n)
        }
    }
    #[test]
    fn full_digest_is_bounded_and_read_truncation_growth_cancel_deadline_fail_closed() {
        let size = 4 * 1024 * 1024 + 17;
        let mut expected = Sha256::new();
        for _ in 0..4096 {
            expected.update([42u8; 1024]);
        }
        expected.update([42u8; 17]);
        let expected = format!("sha256:{:x}", expected.finalize());
        let reader = || Filled {
            remaining: size,
            seen: 0,
            largest: 0,
            fail_after: None,
        };
        let mut input = reader();
        digest(&mut input, &expected, size as u64, until(), &mut || false).unwrap();
        assert!(input.largest <= HASH_BLOCK && input.seen == size);
        assert!(
            digest(
                &mut reader(),
                &expected,
                size as u64 - 1,
                until(),
                &mut || false
            )
            .is_err()
        );
        assert!(
            digest(
                &mut reader(),
                &expected,
                size as u64 + 1,
                until(),
                &mut || false
            )
            .is_err()
        );
        let mut failed = reader();
        failed.fail_after = Some(HASH_BLOCK);
        assert!(digest(&mut failed, &expected, size as u64, until(), &mut || false).is_err());
        let mut expired = reader();
        assert!(
            digest(
                &mut expired,
                &expected,
                size as u64,
                Instant::now(),
                &mut || false
            )
            .is_err()
        );
        assert_eq!(expired.seen, 0);
        assert!(digest(&mut expired, &expected, size as u64, until(), &mut || true).is_err());
        assert_eq!(expired.seen, 0);
    }

    #[test]
    fn held_identity_rejects_same_bytes_replacement_and_multilink_without_deleting_files() {
        let f = Fixture::new();
        let path = f.0.join("result.mp4");
        fs::write(&path, b"synthetic identity fixture").unwrap();
        let node = PinnedNode::open(&path, false, false).unwrap();
        #[cfg(unix)]
        {
            let old = f.0.join("old.mp4");
            fs::rename(&path, &old).unwrap();
            fs::write(&path, b"synthetic identity fixture").unwrap();
            assert!(node.recheck().is_err());
            assert_eq!(fs::read(old).unwrap(), b"synthetic identity fixture");
        }
        #[cfg(windows)]
        {
            assert!(fs::rename(&path, f.0.join("old.mp4")).is_err());
            assert!(OpenOptions::new().write(true).open(&path).is_err());
            node.recheck().unwrap();
        }
        drop(node);
        let alias = f.0.join("alias.mp4");
        fs::hard_link(&path, &alias).unwrap();
        assert!(PinnedNode::open(&path, false, false).is_err());
        assert!(path.exists() && alias.exists());
    }

    // These are tiny synthetic committed-file fixtures, NOT playable-video,
    // producer/runtime, actual ShellExecute, or perceptual playback evidence.
    #[cfg(windows)]
    struct OriginalFixture {
        store: DurableStore,
        data: PathBuf,
        manifest: PathBuf,
        video: PathBuf,
        request: Request,
        _fixture: Fixture,
    }
    #[cfg(windows)]
    impl OriginalFixture {
        fn new() -> Self {
            let fixture = Fixture::new();
            let data = fixture.0.join("data");
            fs::create_dir_all(data.join("control")).unwrap();
            let output = fixture.0.join("output");
            fs::create_dir(&output).unwrap();
            let video = output.join("final_vi.mp4");
            let bytes = b"\0\0\0\x18ftypisom\0\0\0\0isomiso2"; // Only a file-integrity fixture.
            fs::write(&video, bytes).unwrap();
            let manifest = output.join("job_manifest.json");
            fs::write(&manifest, json!({"schema_version":1,"kind":"dubflow_localization_job","job_id":"job-video",
                "artifacts":{"final_video":{"path":video,"sha256":sha(bytes),"size_bytes":bytes.len()}}}).to_string()).unwrap();
            let store = DurableStore::open(data.join("control/jobs.sqlite3")).unwrap();
            store
                .create_job_with_start_request(
                    "job-video",
                    "file:///fixture-only.mp4",
                    &json!({"schema_version":1,"output_dir":output}).to_string(),
                    1,
                )
                .unwrap();
            store
                .create_stage("job-video", crate::STAGE_ID, crate::STAGE_KIND, 3)
                .unwrap();
            store.start_job("job-video", 2).unwrap();
            store.start_stage("job-video", crate::STAGE_ID, 3).unwrap();
            store
                .record_artifact_written(
                    "job-video-local-file-job-manifest-a1",
                    "job-video",
                    crate::STAGE_ID,
                    &manifest,
                    None,
                    true,
                    4,
                )
                .unwrap();
            store
                .commit_artifact("job-video-local-file-job-manifest-a1", 5)
                .unwrap();
            store.complete_job("job-video", 6).unwrap();
            let original = committed_export(&store, "job-video").unwrap();
            let request = Request::read(&original_request(
                "job-video",
                original.pin["manifest_sha256"].as_str().unwrap(),
            ))
            .unwrap();
            Self {
                store,
                data,
                manifest,
                video,
                request,
                _fixture: fixture,
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn injected_launcher_original_ack_is_closed_and_never_claims_actual_playback() {
        let f = OriginalFixture::new();
        let before = (
            fs::read(&f.video).unwrap(),
            committed_export(&f.store, "job-video").unwrap().pin,
        );
        let mut calls = 0;
        let ack = open_with(&f.data, &f.request, until(), &mut || false, &mut |held| {
            calls += 1;
            assert_eq!(held.sha256, sha(&before.0));
            held.recheck()?;
            Ok(())
        })
        .unwrap();
        assert_eq!(calls, 1);
        assert_eq!(ack.as_object().unwrap().len(), 10);
        assert_eq!(ack["launch_requested"], true);
        assert_eq!(ack["managed_playback"], false);
        assert_eq!(ack["playback_verified"], false);
        assert_eq!(
            ack["expected"],
            serde_json::to_value(&f.request.expected).unwrap()
        );
        assert!(ack.get("path").is_none() && ack.get("url").is_none());
        assert_eq!(fs::read(&f.video).unwrap(), before.0);
        assert_eq!(
            committed_export(&f.store, "job-video").unwrap().pin,
            before.1
        );
        assert!(f.store.review_graph_state("job-video").unwrap().is_none());
    }

    #[cfg(windows)]
    #[test]
    fn original_graph_present_and_invalid_current_never_fall_back_to_opening_original() {
        let f = OriginalFixture::new();
        f.store.initialize_review_schema().unwrap();
        let original = committed_export(&f.store, "job-video").unwrap();
        let state = f
            .store
            .create_review_graph(
                "job-video",
                &original.artifact_id,
                original.pin["manifest_sha256"].as_str().unwrap(),
                "{}",
                "{}",
                7,
            )
            .unwrap();
        let mut calls = 0;
        assert!(
            open_with(&f.data, &f.request, until(), &mut || false, &mut |_| {
                calls += 1;
                Ok(())
            })
            .is_err()
        );
        let token = CurrentToken {
            job_id: state.token.job_id.clone(),
            revision: state.token.revision.to_string(),
            snapshot_sha256: state.token.snapshot_sha256.clone(),
            graph_sha256: state.token.graph_sha256.clone(),
            original_manifest_sha256: state.token.original_manifest_sha256.clone(),
        };
        let request = Request {
            expected: Expected::Current {
                current_token: token,
            },
            ..f.request.clone()
        };
        // Deliberately invalid current physical provenance cannot be bypassed
        // merely because an intact committed original video is available.
        assert!(
            open_with(&f.data, &request, until(), &mut || false, &mut |_| {
                calls += 1;
                Ok(())
            })
            .is_err()
        );
        assert_eq!(calls, 0);
        assert_eq!(
            f.store.review_graph_state("job-video").unwrap(),
            Some(state)
        );
    }

    #[cfg(windows)]
    #[test]
    fn prelaunch_corruption_missing_cancel_expiry_and_launch_refusal_preserve_originals() {
        for case in ["hash", "missing", "cancel", "deadline", "launch-refused"] {
            let f = OriginalFixture::new();
            if case == "hash" {
                fs::write(&f.video, b"same namespace, wrong file content").unwrap();
            }
            if case == "missing" {
                fs::rename(&f.video, f.video.with_extension("saved")).unwrap();
            }
            let pin = committed_export(&f.store, "job-video").unwrap().pin;
            let mut calls = 0;
            let end = if case == "deadline" {
                Instant::now()
            } else {
                until()
            };
            let result = open_with(
                &f.data,
                &f.request,
                end,
                &mut || case == "cancel",
                &mut |_| {
                    calls += 1;
                    Err(invalid("injected association refusal; no actual OS launch"))
                },
            );
            assert!(result.is_err(), "{case}");
            assert_eq!(calls, usize::from(case == "launch-refused"), "{case}");
            assert_eq!(committed_export(&f.store, "job-video").unwrap().pin, pin);
            assert!(f.store.review_graph_state("job-video").unwrap().is_none());
        }
    }

    #[cfg(windows)]
    #[test]
    fn injected_late_cancel_manifest_or_head_change_after_activation_is_unconfirmed_not_success() {
        for change in ["cancel", "manifest", "head"] {
            let f = OriginalFixture::new();
            let cancelled = std::cell::Cell::new(false);
            let mut calls = 0;
            let before = fs::read(&f.video).unwrap();
            let error = open_with(
                &f.data,
                &f.request,
                until(),
                &mut || cancelled.get(),
                &mut |_| {
                    calls += 1;
                    if change == "manifest" {
                        fs::write(&f.manifest, b"fixture changed AFTER mock activation")?;
                    } else if change == "head" {
                        // A competing writer is simulated only in this new
                        // fixture. Production writers must honor job exclusion.
                        f.store.initialize_review_schema()?;
                        let original = committed_export(&f.store, "job-video")?;
                        f.store.create_review_graph(
                            "job-video",
                            &original.artifact_id,
                            original.pin["manifest_sha256"].as_str().unwrap(),
                            "{}",
                            "{}",
                            7,
                        )?;
                    } else {
                        cancelled.set(true);
                    }
                    Ok(())
                },
            )
            .unwrap_err();
            assert_eq!(calls, 1);
            assert!(error.to_string().contains("unconfirmed"));
            assert_eq!(fs::read(&f.video).unwrap(), before);
        }
    }

    #[cfg(windows)]
    #[test]
    fn existing_job_exclusion_refuses_launch_and_releases_without_graph_mutation() {
        let f = OriginalFixture::new();
        let before = fs::read(&f.video).unwrap();
        let guard =
            JobExecutionGuard::acquire(&f.data.join("control/jobs.sqlite3"), "job-video").unwrap();
        let mut calls = 0;
        assert!(
            open_with(&f.data, &f.request, until(), &mut || false, &mut |_| {
                calls += 1;
                Ok(())
            })
            .is_err()
        );
        assert_eq!(calls, 0);
        drop(guard);
        assert!(
            open_with(&f.data, &f.request, until(), &mut || false, &mut |_| {
                calls += 1;
                Ok(())
            })
            .is_ok()
        );
        assert_eq!(calls, 1);
        assert_eq!(fs::read(&f.video).unwrap(), before);
        assert!(f.store.review_graph_state("job-video").unwrap().is_none());
    }
}
