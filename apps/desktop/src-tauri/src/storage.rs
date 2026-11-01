//! Disk usage and cleanup (Issue #406): what DubFlow keeps that it can rebuild
//! or no longer needs, by category, and its removal.
//!
//! Categories:
//! - `work`: heavy media in `<output>\.dubflow-work` of finished videos (raw
//!   speech clips, the analysis track, the TTS cache, package/Shorts/reframe
//!   render caches). Every later action reads only the small JSON there or the
//!   results beside it, and the caches are rebuilt on demand. A file that is
//!   hard-linked from a kept result (the stems in `editable\`) frees nothing
//!   and is left alone.
//! - `versions`: installed versions other than the current one, the newest
//!   other one (rollback) and the one this process runs from.
//! - `profiles`: WebView2 profiles of versions that are not kept.
//! - `link_cache`: link-download scratch in `<data root>\source-work`, only
//!   while no link is waiting or downloading.
//!
//! Removal only touches paths inside those roots, never follows links, and
//! removes a folder by renaming it to a sibling `.dubflow-trash-*` first, so a
//! folder with a file in use stays whole and is reported as skipped.
use serde::Serialize;
use serde_json::Value;
use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, SystemTime};

use crate::{fail, source::Sources, updater};

pub const WORK: &str = "work";
pub const VERSIONS: &str = "versions";
pub const PROFILES: &str = "profiles";
pub const LINK_CACHE: &str = "link_cache";
const CATEGORIES: [&str; 4] = [WORK, VERSIONS, PROFILES, LINK_CACHE];

const WORK_DIR: &str = ".dubflow-work";
const TRASH_PREFIX: &str = ".dubflow-trash-";
/// Heavy media inside a work folder (also as `<name>.<ext>.partial`).
const MEDIA: [&str; 15] = ["wav", "pcm", "raw", "f32", "mp4", "mkv", "mov", "m4a", "aac", "mp3", "flac", "ogg", "opus", "webm", "ts"];
/// Work caches removed whole; the worker rebuilds them on a re-run.
const CACHE_DIRS: [&str; 1] = ["tts-cache"];
/// The publication journal's folders: recovery after an interrupted export
/// reads them, so they are never touched.
const KEEP_DIRS: [&str; 2] = ["export-pending", "export-previous"];
/// Post-actions that render inside the work folder and report a status.
const ACTIONS: [&str; 4] = ["package", "shorts", "reframe", ""];
/// A checkpoint written this recently means a job may still be running there.
const ACTIVE_WINDOW: Duration = Duration::from_secs(10 * 60);
/// Output folders scanned below one results root, at most.
const MAX_SCANNED: usize = 10_000;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Category {
    pub id: &'static str,
    pub bytes: u64,
    pub items: u64,
    /// Cannot be cleaned now (a link is downloading).
    pub busy: bool,
}

#[derive(Debug, Serialize)]
pub struct Report {
    pub categories: Vec<Category>,
    /// Install root (or data root) plus the output folders.
    pub total_used: u64,
    pub install_free_bytes: Option<u64>,
    /// Least free space among the drives holding output folders.
    pub output_free_bytes: Option<u64>,
}

#[derive(Debug, Default, Serialize, PartialEq)]
pub struct Cleaned {
    pub freed_bytes: u64,
    /// What was in use and stays for the next clean, in Vietnamese.
    pub skipped: Vec<String>,
}

/// Where DubFlow keeps its own files. `install` and `data` are `None` for
/// development builds, which then only clean output work folders.
pub struct Layout {
    pub install: Option<PathBuf>,
    pub data: Option<PathBuf>,
    /// This process's executable; the version folder holding it is kept.
    pub running_exe: Option<PathBuf>,
}

/// Which output folders may be touched and how.
pub struct Scope<'a> {
    pub outputs: &'a [PathBuf],
    /// Canonical output folders of running or pending jobs.
    pub running: &'a HashSet<PathBuf>,
    /// `work` only for results whose last activity is at least this old.
    pub older_than: Option<Duration>,
    pub link_busy: bool,
    pub now: SystemTime,
}

#[derive(Debug)]
struct Candidate {
    path: PathBuf,
    /// The folder `path` must stay inside.
    root: PathBuf,
    bytes: u64,
    dir: bool,
    /// Shown when it cannot be removed now.
    label: String,
}

mod sys {
    use std::path::Path;

    #[cfg(windows)]
    mod win {
        use std::ffi::c_void;

        #[repr(C)]
        #[derive(Default)]
        pub struct FileInformation {
            pub attributes: u32,
            pub created: [u32; 2],
            pub accessed: [u32; 2],
            pub written: [u32; 2],
            pub volume: u32,
            pub size_high: u32,
            pub size_low: u32,
            pub links: u32,
            pub index_high: u32,
            pub index_low: u32,
        }

        #[link(name = "kernel32")]
        extern "system" {
            pub fn GetDiskFreeSpaceExW(directory: *const u16, available: *mut u64, total: *mut u64, free: *mut u64) -> i32;
            pub fn GetFileInformationByHandle(file: *mut c_void, information: *mut FileInformation) -> i32;
        }
    }

    /// Bytes free for this user on the drive holding `path` (an existing folder).
    #[cfg(windows)]
    pub fn free_bytes(path: &Path) -> Option<u64> {
        use std::os::windows::ffi::OsStrExt;
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut available = 0u64;
        // SAFETY: a NUL-terminated wide path and a valid out pointer; the
        // optional totals are documented as nullable.
        let ok = unsafe { win::GetDiskFreeSpaceExW(wide.as_ptr(), &mut available, std::ptr::null_mut(), std::ptr::null_mut()) };
        (ok != 0).then_some(available)
    }

    #[cfg(not(windows))]
    pub fn free_bytes(_path: &Path) -> Option<u64> {
        None
    }

    /// (volume, file index, link count) of a file, never following a link.
    #[cfg(windows)]
    pub fn identity(path: &Path) -> Option<(u64, u64, u32)> {
        use std::os::windows::fs::OpenOptionsExt;
        use std::os::windows::io::AsRawHandle;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
        // No data access and full sharing: works on files other processes hold.
        let file = std::fs::OpenOptions::new().access_mode(0).share_mode(7)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS).open(path).ok()?;
        let mut info = win::FileInformation::default();
        // SAFETY: the handle is open for the call and `info` is a valid out pointer.
        let ok = unsafe { win::GetFileInformationByHandle(file.as_raw_handle(), &mut info) };
        (ok != 0).then(|| (u64::from(info.volume), (u64::from(info.index_high) << 32) | u64::from(info.index_low), info.links))
    }

    #[cfg(not(windows))]
    pub fn identity(path: &Path) -> Option<(u64, u64, u32)> {
        use std::os::unix::fs::MetadataExt;
        let metadata = std::fs::symlink_metadata(path).ok()?;
        Some((metadata.dev(), metadata.ino(), u32::try_from(metadata.nlink()).unwrap_or(u32::MAX)))
    }
}

/// A symlink, junction or any other reparse point: never followed or removed.
fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    metadata.file_type().is_symlink()
}

/// An existing folder that is not a link.
fn plain_dir(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir() && !is_link(&metadata))
}

/// Visit every file below `path` (or `path` itself), never entering links.
fn walk(path: &Path, visit: &mut dyn FnMut(&Path, &fs::Metadata)) {
    fn inner(path: &Path, metadata: &fs::Metadata, depth: usize, visit: &mut dyn FnMut(&Path, &fs::Metadata)) {
        if is_link(metadata) {
            return;
        }
        if !metadata.is_dir() {
            visit(path, metadata);
            return;
        }
        if depth > 64 {
            return;
        }
        for entry in fs::read_dir(path).into_iter().flatten().flatten() {
            // On Windows this is the directory listing's own data: no extra
            // call per file, and it describes a link, not its target.
            if let Ok(child) = entry.metadata() {
                inner(&entry.path(), &child, depth + 1, visit);
            }
        }
    }
    if let Ok(metadata) = fs::symlink_metadata(path) {
        inner(path, &metadata, 0, visit);
    }
}

fn tree_bytes(path: &Path) -> u64 {
    let mut total = 0;
    walk(path, &mut |_, metadata| total += metadata.len());
    total
}

/// Like `tree_bytes`, but a large file with several hard links counts once
/// across every call sharing `seen`.
fn unique_bytes(path: &Path, seen: &mut HashSet<(u64, u64)>) -> u64 {
    let mut total = 0;
    walk(path, &mut |file, metadata| {
        if metadata.len() >= 1 << 20 {
            if let Some((volume, index, links)) = sys::identity(file) {
                if links > 1 && !seen.insert((volume, index)) {
                    return;
                }
            }
        }
        total += metadata.len();
    });
    total
}

/// `path` resolves strictly inside `root` and is not itself a link.
fn inside(path: &Path, root: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else { return false };
    if is_link(&metadata) {
        return false;
    }
    match (path.canonicalize(), root.canonicalize()) {
        (Ok(path), Ok(root)) => path != root && path.starts_with(&root),
        _ => false,
    }
}

fn is_trash(name: &str) -> bool {
    name.starts_with(TRASH_PREFIX)
}

fn heavy_media(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    let name = lower.strip_suffix(".partial").unwrap_or(&lower);
    Path::new(name).extension().and_then(|ext| ext.to_str()).is_some_and(|ext| MEDIA.contains(&ext))
}

/// Release-style version order: numbers compare as numbers, a pre-release
/// ("-rc6") sorts before its release, build metadata is ignored.
pub fn compare_versions(a: &str, b: &str) -> Ordering {
    fn split(version: &str) -> (&str, Option<&str>) {
        let version = version.split('+').next().unwrap_or(version);
        match version.split_once('-') {
            Some((core, pre)) => (core, Some(pre)),
            None => (version, None),
        }
    }
    fn natural(a: &str, b: &str) -> Ordering {
        fn chunks(text: &str) -> Vec<&str> {
            let mut parts = Vec::new();
            let mut start = 0;
            let bytes = text.as_bytes();
            for index in 1..=bytes.len() {
                if index == bytes.len() || bytes[index].is_ascii_digit() != bytes[start].is_ascii_digit() {
                    parts.push(&text[start..index]);
                    start = index;
                }
            }
            parts
        }
        let (left, right) = (chunks(a), chunks(b));
        for (x, y) in left.iter().zip(&right) {
            let order = if x.as_bytes()[0].is_ascii_digit() && y.as_bytes()[0].is_ascii_digit() {
                let (x, y) = (x.trim_start_matches('0'), y.trim_start_matches('0'));
                x.len().cmp(&y.len()).then_with(|| x.cmp(y))
            } else {
                x.cmp(y)
            };
            if order != Ordering::Equal {
                return order;
            }
        }
        left.len().cmp(&right.len())
    }
    let ((core_a, pre_a), (core_b, pre_b)) = (split(a), split(b));
    natural(core_a, core_b).then_with(|| match (pre_a, pre_b) {
        (None, None) => Ordering::Equal,
        (None, Some(_)) => Ordering::Greater,
        (Some(_), None) => Ordering::Less,
        (Some(x), Some(y)) => natural(x, y),
    })
}

fn current_version(install: &Path) -> Option<String> {
    let path = install.join("current.json");
    if fs::metadata(&path).ok()?.len() > 64 * 1024 {
        return None;
    }
    let value: Value = serde_json::from_str(&fs::read_to_string(path).ok()?).ok()?;
    value["current_version"].as_str().filter(|version| updater::valid_version(version)).map(str::to_owned)
}

/// Installed version folders (`versions\<version>`), never staging folders.
fn installed_versions(install: &Path) -> Vec<(String, PathBuf)> {
    let versions = install.join("versions");
    if !plain_dir(&versions) {
        return Vec::new();
    }
    fs::read_dir(&versions).into_iter().flatten().flatten().filter_map(|entry| {
        let name = entry.file_name().into_string().ok()?;
        let metadata = entry.metadata().ok()?;
        (updater::valid_version(&name) && metadata.is_dir() && !is_link(&metadata)).then(|| (name, entry.path()))
    }).collect()
}

fn contains_exe(dir: &Path, exe: Option<&Path>) -> bool {
    exe.is_some_and(|exe| dir.canonicalize().is_ok_and(|dir| exe.starts_with(dir)))
}

/// Versions to keep: current, the newest other one, and the running one.
/// `None` when the current version is unknown: then nothing is removed.
fn kept_versions(layout: &Layout) -> Option<BTreeSet<String>> {
    let install = layout.install.as_deref()?;
    let current = current_version(install)?;
    let installed = installed_versions(install);
    let mut kept = BTreeSet::from([current.clone()]);
    if let Some((newest, _)) = installed.iter().filter(|(version, _)| *version != current)
        .max_by(|(a, _), (b, _)| compare_versions(a, b)) {
        kept.insert(newest.clone());
    }
    for (version, path) in &installed {
        if contains_exe(path, layout.running_exe.as_deref()) {
            kept.insert(version.clone());
        }
    }
    Some(kept)
}

/// Leftover `.dubflow-trash-*` folders directly inside `parent`.
fn trash_in(parent: &Path) -> Vec<Candidate> {
    if !plain_dir(parent) {
        return Vec::new();
    }
    fs::read_dir(parent).into_iter().flatten().flatten().filter_map(|entry| {
        let name = entry.file_name().into_string().ok()?;
        let metadata = entry.metadata().ok()?;
        (is_trash(&name) && metadata.is_dir() && !is_link(&metadata)).then(|| Candidate {
            bytes: tree_bytes(&entry.path()), path: entry.path(), root: parent.to_path_buf(), dir: true,
            label: "Thư mục đang xóa dở".to_owned(),
        })
    }).collect()
}

fn version_candidates(layout: &Layout) -> Vec<Candidate> {
    let (Some(install), Some(kept)) = (layout.install.as_deref(), kept_versions(layout)) else { return Vec::new() };
    let root = install.join("versions");
    let mut found: Vec<Candidate> = installed_versions(install).into_iter()
        .filter(|(version, path)| !kept.contains(version) && !contains_exe(path, layout.running_exe.as_deref()))
        .map(|(version, path)| Candidate { bytes: tree_bytes(&path), path, root: root.clone(), dir: true, label: format!("Bản cài {version}") })
        .collect();
    found.extend(trash_in(&root));
    found
}

/// `control\webview2\<hex of version>` -> version.
fn profile_version(name: &str) -> Option<String> {
    if name.is_empty() || name.len() % 2 != 0 || name.len() > 128 {
        return None;
    }
    let bytes: Option<Vec<u8>> = (0..name.len()).step_by(2).map(|index| u8::from_str_radix(name.get(index..index + 2)?, 16).ok()).collect();
    String::from_utf8(bytes?).ok().filter(|version| updater::valid_version(version))
}

fn profile_candidates(layout: &Layout) -> Vec<Candidate> {
    let (Some(data), Some(kept)) = (layout.data.as_deref(), kept_versions(layout)) else { return Vec::new() };
    let control = data.join("control");
    let root = control.join("webview2");
    if !plain_dir(&control) || !plain_dir(&root) {
        return Vec::new();
    }
    let mut found: Vec<Candidate> = fs::read_dir(&root).into_iter().flatten().flatten().filter_map(|entry| {
        let version = profile_version(entry.file_name().to_str()?)?;
        let metadata = entry.metadata().ok()?;
        (metadata.is_dir() && !is_link(&metadata) && !kept.contains(&version)).then(|| Candidate {
            bytes: tree_bytes(&entry.path()), path: entry.path(), root: root.clone(), dir: true,
            label: format!("Dữ liệu trình duyệt của bản {version}"),
        })
    }).collect();
    found.extend(trash_in(&root));
    found
}

fn link_candidates(layout: &Layout) -> Vec<Candidate> {
    let Some(data) = layout.data.as_deref() else { return Vec::new() };
    let root = data.join("source-work");
    if !plain_dir(&root) {
        return Vec::new();
    }
    fs::read_dir(&root).into_iter().flatten().flatten().filter_map(|entry| {
        let metadata = entry.metadata().ok()?;
        (!is_link(&metadata)).then(|| Candidate {
            bytes: tree_bytes(&entry.path()), path: entry.path(), root: root.clone(), dir: metadata.is_dir(),
            label: "Bộ đệm tải link".to_owned(),
        })
    }).collect()
}

/// `<title> - vi\run-…`, enough to recognise a video.
fn short_label(output: &Path) -> String {
    let parts: Vec<String> = output.components().rev().take(2).map(|part| part.as_os_str().to_string_lossy().into_owned()).collect();
    parts.into_iter().rev().collect::<Vec<_>>().join("\\")
}

fn finished(output: &Path) -> bool {
    output.join("final_vi.mp4").is_file() || output.join("job_manifest.json").is_file()
}

fn modified(path: &Path) -> Option<SystemTime> {
    fs::metadata(path).and_then(|metadata| metadata.modified()).ok()
}

/// When the result or a post-action on it last changed.
fn last_activity(output: &Path) -> Option<SystemTime> {
    let work = output.join(WORK_DIR);
    [output.join("job_manifest.json"), output.join("final_vi.mp4")].into_iter()
        .chain(ACTIONS.iter().filter(|action| !action.is_empty()).map(|action| work.join(action).join("status.json")))
        .filter_map(|path| modified(&path)).max()
}

/// Another holder (in any process) has the action's lease file open.
fn lease_held(dir: &Path) -> bool {
    let lease = dir.join("lease");
    if !lease.is_file() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const ERROR_SHARING_VIOLATION: i32 = 32;
        fs::OpenOptions::new().read(true).share_mode(0).open(&lease)
            .is_err_and(|error| error.raw_os_error() == Some(ERROR_SHARING_VIOLATION))
    }
    #[cfg(not(windows))]
    false
}

/// A job, package, Shorts, reframe or compilation may be working here.
fn output_busy(output: &Path, scope: &Scope) -> bool {
    if scope.running.contains(output) || output.canonicalize().is_ok_and(|path| scope.running.contains(&path)) {
        return true;
    }
    let work = output.join(WORK_DIR);
    let recent = |path: &Path| modified(path).is_some_and(|time| scope.now.duration_since(time).map_or(true, |age| age < ACTIVE_WINDOW));
    if recent(&work.join("checkpoint.json")) {
        return true;
    }
    if fs::read_to_string(work.join("publication.json")).is_ok_and(|text| text.contains("\"publishing\"")) {
        return true;
    }
    ACTIONS.iter().any(|action| {
        let dir = work.join(action);
        let running = fs::read(dir.join("status.json")).ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .is_some_and(|status| status["state"] == "RUNNING");
        // A crash can leave RUNNING behind; a released lease proves it stale.
        running && (lease_held(&dir) || !dir.join("lease").is_file()) || lease_held(&dir)
    })
}

/// Removable heavy media of one finished output folder.
fn work_candidates(output: &Path) -> Vec<Candidate> {
    let work = output.join(WORK_DIR);
    if !plain_dir(&work) {
        return Vec::new();
    }
    let label = short_label(output);
    let mut found = Vec::new();
    let mut media: Vec<(PathBuf, u64)> = Vec::new();
    for entry in fs::read_dir(&work).into_iter().flatten().flatten() {
        let (Some(name), Ok(metadata)) = (entry.file_name().to_str().map(str::to_owned), entry.metadata()) else { continue };
        if is_link(&metadata) || KEEP_DIRS.contains(&name.as_str()) {
            continue;
        }
        let path = entry.path();
        if metadata.is_dir() && (is_trash(&name) || CACHE_DIRS.contains(&name.as_str())) {
            found.push(Candidate { bytes: tree_bytes(&path), path, root: work.clone(), dir: true, label: label.clone() });
        } else {
            walk(&path, &mut |file, metadata| {
                if file.file_name().and_then(|name| name.to_str()).is_some_and(heavy_media) {
                    media.push((file.to_path_buf(), metadata.len()));
                }
            });
        }
    }
    // A file is removed only when every hard link to it is removable here;
    // one linked from `editable\` (or anywhere else) frees nothing.
    let mut groups: HashMap<(u64, u64), (u32, Vec<(PathBuf, u64)>)> = HashMap::new();
    for (index, (path, bytes)) in media.into_iter().enumerate() {
        let (volume, file, links) = sys::identity(&path).unwrap_or((u64::MAX, index as u64, 1));
        let group = groups.entry((volume, file)).or_insert((links, Vec::new()));
        group.1.push((path, bytes));
    }
    for (links, paths) in groups.into_values() {
        if paths.len() as u64 >= u64::from(links) {
            for (position, (path, bytes)) in paths.into_iter().enumerate() {
                found.push(Candidate { path, root: work.clone(), bytes: if position == 0 { bytes } else { 0 }, dir: false, label: label.clone() });
            }
        }
    }
    found
}

fn work_plan(scope: &Scope) -> Vec<Candidate> {
    scope.outputs.iter().filter(|output| finished(output) && !output_busy(output, scope))
        .filter(|output| scope.older_than.is_none_or(|age| last_activity(output)
            .is_some_and(|time| scope.now.duration_since(time).is_ok_and(|elapsed| elapsed >= age))))
        .flat_map(|output| work_candidates(output)).collect()
}

fn plan(layout: &Layout, category: &str, scope: &Scope) -> Vec<Candidate> {
    match category {
        WORK => work_plan(scope),
        VERSIONS => version_candidates(layout),
        PROFILES => profile_candidates(layout),
        LINK_CACHE if !scope.link_busy => link_candidates(layout),
        _ => Vec::new(),
    }
}

/// Space DubFlow takes and what each category could free.
pub fn report(layout: &Layout, scope: &Scope) -> Report {
    let categories = CATEGORIES.iter().map(|&id| {
        let found = plan(layout, id, scope);
        let items = if id == WORK { found.iter().map(|candidate| &candidate.label).collect::<HashSet<_>>().len() } else { found.len() };
        Category { id, bytes: found.iter().map(|candidate| candidate.bytes).sum(), items: items as u64, busy: id == LINK_CACHE && scope.link_busy }
    }).collect();
    let mut seen = HashSet::new();
    let app = layout.install.as_deref().or(layout.data.as_deref());
    let total_used = app.map_or(0, |root| unique_bytes(root, &mut seen))
        + scope.outputs.iter().map(|output| unique_bytes(output, &mut seen)).sum::<u64>();
    Report { categories, total_used, install_free_bytes: app.and_then(sys::free_bytes), output_free_bytes: output_free_bytes(scope.outputs) }
}

/// Least free space among the drives holding `outputs` and the default
/// results folder, each drive asked once.
pub fn output_free_bytes(outputs: &[PathBuf]) -> Option<u64> {
    let mut drives = HashSet::new();
    default_output_root().into_iter().chain(outputs.iter().cloned())
        .filter_map(|path| path.ancestors().find(|dir| dir.is_dir()).map(Path::to_path_buf))
        .filter(|dir| drives.insert(dir.components().next().map(|part| part.as_os_str().to_ascii_lowercase())))
        .filter_map(|dir| sys::free_bytes(&dir)).min()
}

fn trash_name(parent: &Path) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default().as_nanos();
    parent.join(format!("{TRASH_PREFIX}{}-{nanos:x}-{}", std::process::id(), COUNTER.fetch_add(1, Ordering::Relaxed)))
}

/// No program inside `dir` is running: every `.exe` opens without sharing.
/// Windows may let a folder be renamed while a program in it runs, so this is
/// checked before anything inside is touched.
fn executables_free(dir: &Path) -> bool {
    let Ok(entries) = fs::read_dir(dir) else { return true };
    entries.flatten().all(|entry| {
        let Ok(metadata) = fs::symlink_metadata(entry.path()) else { return true };
        if is_link(&metadata) {
            true
        } else if metadata.is_dir() {
            executables_free(&entry.path())
        } else if entry.path().extension().is_some_and(|ext| ext.eq_ignore_ascii_case("exe")) {
            exclusive_open(&entry.path())
        } else {
            true
        }
    })
}

#[cfg(windows)]
fn exclusive_open(path: &Path) -> bool {
    use std::os::windows::fs::OpenOptionsExt;
    fs::OpenOptions::new().read(true).share_mode(0).open(path).is_ok()
}

#[cfg(not(windows))]
fn exclusive_open(_path: &Path) -> bool {
    true
}

/// Remove one candidate; the bytes freed and whether it is fully gone.
fn remove(candidate: &Candidate, running_exe: Option<&Path>, rename: &dyn Fn(&Path, &Path) -> io::Result<()>) -> (u64, bool) {
    if !inside(&candidate.path, &candidate.root) || contains_exe(&candidate.path, running_exe) {
        return (0, false);
    }
    if candidate.dir && !executables_free(&candidate.path) {
        return (0, false);
    }
    if !candidate.dir {
        return match fs::remove_file(&candidate.path) {
            Ok(()) => (candidate.bytes, true),
            Err(_) => (0, false),
        };
    }
    let before = tree_bytes(&candidate.path);
    let trash = if candidate.path.file_name().and_then(|name| name.to_str()).is_some_and(is_trash) {
        candidate.path.clone()
    } else {
        // One rename either takes the whole folder or nothing: a file in use
        // anywhere inside makes it fail, and the folder stays as it was.
        let Some(parent) = candidate.path.parent() else { return (0, false) };
        let trash = trash_name(parent);
        if rename(&candidate.path, &trash).is_err() {
            return (0, false);
        }
        trash
    };
    let _ = fs::remove_dir_all(&trash);
    let left = if trash.exists() { tree_bytes(&trash) } else { 0 };
    (before.saturating_sub(left), !trash.exists())
}

/// Remove the chosen categories; what is in use is skipped and reported.
pub fn clean_with(layout: &Layout, categories: &[String], scope: &Scope, rename: &dyn Fn(&Path, &Path) -> io::Result<()>) -> Cleaned {
    let mut cleaned = Cleaned::default();
    for category in CATEGORIES.iter().filter(|id| categories.iter().any(|chosen| chosen == *id)) {
        if *category == LINK_CACHE && scope.link_busy {
            cleaned.skipped.push("Bộ đệm tải link (đang tải link)".to_owned());
            continue;
        }
        for candidate in plan(layout, category, scope) {
            let (freed, done) = remove(&candidate, layout.running_exe.as_deref(), rename);
            cleaned.freed_bytes += freed;
            if !done && !cleaned.skipped.contains(&candidate.label) {
                cleaned.skipped.push(candidate.label);
            }
        }
    }
    cleaned
}

fn default_output_root() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE").map(|profile| PathBuf::from(profile).join("Videos").join("DubFlow Output"))
}

/// The front end's output folders (only below a "DubFlow Output" folder) plus
/// every `<title>\<job>` folder of the results roots: the default one and the
/// "DubFlow Output" folders those outputs live in, so videos removed from the
/// queue are found too. Canonical and without duplicates.
pub fn collect_outputs(given: &[String], default_root: Option<PathBuf>) -> Vec<PathBuf> {
    let in_results = |path: &Path| path.ancestors().skip(1).any(|dir| dir.file_name().is_some_and(|name| name == "DubFlow Output"));
    let given: Vec<PathBuf> = given.iter().map(PathBuf::from)
        .filter(|path| path.is_absolute() && in_results(path) && plain_dir(path)).collect();
    let mut roots: Vec<PathBuf> = default_root.into_iter().collect();
    roots.extend(given.iter().filter_map(|path| path.ancestors().skip(1).find(|dir| dir.file_name().is_some_and(|name| name == "DubFlow Output")).map(Path::to_path_buf)));
    let mut outputs: BTreeSet<PathBuf> = given.iter().filter_map(|path| path.canonicalize().ok()).collect();
    let mut scanned = HashSet::new();
    for root in roots {
        let Ok(canonical) = root.canonicalize() else { continue };
        if !scanned.insert(canonical) || !plain_dir(&root) {
            continue;
        }
        let mut count = 0;
        let dirs = |dir: &Path| -> Vec<PathBuf> {
            fs::read_dir(dir).into_iter().flatten().flatten()
                .filter(|entry| entry.metadata().is_ok_and(|metadata| metadata.is_dir() && !is_link(&metadata)))
                .map(|entry| entry.path()).collect()
        };
        'titles: for title in dirs(&root) {
            for job in dirs(&title) {
                if count >= MAX_SCANNED {
                    break 'titles;
                }
                count += 1;
                if let Ok(path) = job.canonicalize() {
                    outputs.insert(path);
                }
            }
        }
    }
    outputs.into_iter().collect()
}

fn running_set(running: &[String]) -> HashSet<PathBuf> {
    running.iter().flat_map(|path| {
        let path = PathBuf::from(path);
        let canonical = path.canonicalize().ok();
        std::iter::once(path).chain(canonical)
    }).collect()
}

/// Links started in this session; one not finished keeps `source-work` busy.
static FETCHES: LazyLock<Mutex<Vec<String>>> = LazyLock::new(|| Mutex::new(Vec::new()));
/// One clean at a time (automatic and manual).
static CLEANING: Mutex<()> = Mutex::new(());

pub fn note_fetch(scan_id: &str) {
    if let Ok(mut fetches) = FETCHES.lock() {
        fetches.push(scan_id.to_owned());
    }
}

fn link_busy(sources: &Sources) -> bool {
    let Ok(mut fetches) = FETCHES.lock() else { return true };
    fetches.retain(|scan| sources.status(scan).is_some_and(|status| !matches!(status.state.as_str(), "done" | "failed" | "cancelled")));
    !fetches.is_empty()
}

fn layout() -> Result<Layout, String> {
    let version = crate::version_root()?;
    let install = updater::install_root(&version);
    // Development builds share no install with a release; they only clean
    // output work folders.
    let data = if install.is_some() { Some(crate::data_root()?) } else { None };
    Ok(Layout { install, data, running_exe: std::env::current_exe().ok().and_then(|exe| exe.canonicalize().ok()) })
}

/// Sizes per category, total use and free space. Off the UI thread.
#[tauri::command]
pub async fn storage_report(output_dirs: Vec<String>, running: Vec<String>, sources: tauri::State<'_, Arc<Sources>>) -> Result<Report, String> {
    let busy = link_busy(&sources);
    let layout = layout()?;
    tauri::async_runtime::spawn_blocking(move || {
        let outputs = collect_outputs(&output_dirs, default_output_root());
        let running = running_set(&running);
        report(&layout, &Scope { outputs: &outputs, running: &running, older_than: None, link_busy: busy, now: SystemTime::now() })
    }).await.map_err(|error| fail(error.to_string()))
}

/// Remove the chosen categories. Off the UI thread, one clean at a time.
#[tauri::command]
pub async fn storage_clean(categories: Vec<String>, output_dirs: Vec<String>, running: Vec<String>, older_than_days: Option<u32>,
                           sources: tauri::State<'_, Arc<Sources>>) -> Result<Cleaned, String> {
    if categories.iter().any(|category| !CATEGORIES.contains(&category.as_str())) {
        return Err(fail("Loại dọn dẹp không hợp lệ"));
    }
    let busy = link_busy(&sources);
    let layout = layout()?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = CLEANING.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let outputs = collect_outputs(&output_dirs, default_output_root());
        let running = running_set(&running);
        let older_than = older_than_days.map(|days| Duration::from_secs(u64::from(days) * 86_400));
        clean_with(&layout, &categories, &Scope { outputs: &outputs, running: &running, older_than, link_busy: busy, now: SystemTime::now() },
            &|from, to| fs::rename(from, to))
    }).await.map_err(|error| fail(error.to_string()))
}

/// Free space of the output drives, for the automatic clean's urgency.
#[tauri::command]
pub async fn storage_free(output_dirs: Vec<String>) -> Result<Option<u64>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let outputs: Vec<PathBuf> = output_dirs.iter().map(PathBuf::from).filter(|path| path.is_absolute()).collect();
        output_free_bytes(&outputs)
    }).await.map_err(|error| fail(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Temp(PathBuf);
    impl Temp {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!("dubflow-storage-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Temp(path.canonicalize().unwrap())
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn write(path: &Path, bytes: usize) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, vec![7u8; bytes]).unwrap();
    }

    fn hex(version: &str) -> String {
        version.bytes().map(|byte| format!("{byte:02x}")).collect()
    }

    /// An install with versions 1.1.4, 1.1.9 (running), 1.1.10 (current)
    /// and 1.1.24, their profiles plus one of an uninstalled version.
    fn install(root: &Path) -> Layout {
        for version in ["1.1.4", "1.1.9", "1.1.10", "1.1.24"] {
            write(&root.join("versions").join(version).join("app/bin/DubFlow.exe"), 1000);
            write(&root.join("control/webview2").join(hex(version)).join("EBWebView/Default/x"), 100);
        }
        write(&root.join("control/webview2").join(hex("0.9.0")).join("x"), 50);
        write(&root.join("control/webview2/not-hex/x"), 50);
        write(&root.join("versions/.1.2.0.staging/x"), 10);
        write(&root.join("versions/.dubflow-trash-old/x"), 30);
        fs::write(root.join("current.json"), r#"{"current_version": "1.1.10"}"#).unwrap();
        Layout { install: Some(root.to_path_buf()), data: Some(root.to_path_buf()),
                 running_exe: Some(root.join("versions/1.1.9/app/bin/DubFlow.exe").canonicalize().unwrap()) }
    }

    fn scope<'a>(outputs: &'a [PathBuf], running: &'a HashSet<PathBuf>, older_than: Option<Duration>, link_busy: bool) -> Scope<'a> {
        // A month from now: checkpoints written by the test are not "active".
        Scope { outputs, running, older_than, link_busy, now: SystemTime::now() + Duration::from_secs(30 * 86_400) }
    }

    fn category<'a>(report: &'a Report, id: &str) -> &'a Category {
        report.categories.iter().find(|category| category.id == id).unwrap()
    }

    /// A finished 3-minute-style output folder; returns (output, removable bytes).
    fn finished_output(base: &Path, name: &str) -> (PathBuf, u64) {
        let output = base.join("DubFlow Output").join(format!("{name} - vi")).join("run-1");
        let work = output.join(WORK_DIR);
        write(&output.join("final_vi.mp4"), 5000);
        fs::write(output.join("job_manifest.json"), "{}").unwrap();
        write(&output.join("captions_vi.srt"), 10);
        for small in ["translation.json", "transcript.json", "checkpoint.json", "speakers.json", "asr-chunks/0.json", "package/status.json", "package/main.receipt.json"] {
            write(&work.join(small), 20);
        }
        write(&work.join("analysis.wav"), 300);
        write(&work.join("tts-cache/a.wav"), 200);
        write(&work.join("tts-cache/a.json"), 5);
        write(&work.join("b2-audio/id/gen/tts/tts-1.wav"), 100);
        write(&work.join("b2-audio/id/gen/tts_document.json"), 20);
        write(&work.join("b2-audio/id/gen/mix/final.wav"), 700);
        write(&work.join("package/main.mp4"), 1400);
        write(&work.join("package/final_vi_packaged.mp4.partial"), 40);
        write(&work.join("export-pending/final_vi.mp4"), 900);
        // The editable stem is the same file as the generation's final mix.
        fs::create_dir_all(output.join("editable")).unwrap();
        fs::hard_link(work.join("b2-audio/id/gen/mix/final.wav"), output.join("editable/final_mix.wav")).unwrap();
        (output, 300 + 205 + 100 + 1400 + 40)
    }

    #[test]
    fn versions_compare_as_releases() {
        assert_eq!(compare_versions("1.1.10", "1.1.9"), Ordering::Greater);
        assert_eq!(compare_versions("0.2.0-rc6", "0.2.0"), Ordering::Less);
        assert_eq!(compare_versions("0.2.0-rc10", "0.2.0-rc6"), Ordering::Greater);
        assert_eq!(compare_versions("1.2.0+build", "1.2.0"), Ordering::Equal);
        assert_eq!(profile_version(&hex("1.1.24")).as_deref(), Some("1.1.24"));
        assert_eq!(profile_version("2e2e5c78"), None);
        assert_eq!(profile_version("zz"), None);
        assert!(heavy_media("main.mp4") && heavy_media("x.MP4.partial") && heavy_media("a.wav"));
        assert!(!heavy_media("status.json.partial") && !heavy_media("intro.png") && !heavy_media("lease"));
    }

    #[test]
    fn report_sizes_each_category_and_keeps_current_previous_and_running_versions() {
        let temp = Temp::new("report");
        let layout = install(&temp.0);
        write(&temp.0.join("source-work/materialization/a/part"), 4000);
        write(&temp.0.join("source-work/scan-1/x"), 1000);
        let (output, removable) = finished_output(&temp.0, "Clip");
        let outputs = vec![output.clone()];
        let running = HashSet::new();
        let report = report(&layout, &scope(&outputs, &running, None, false));
        // 1.1.10 current, 1.1.24 newest other, 1.1.9 running: only 1.1.4 and the trash go.
        assert_eq!(category(&report, VERSIONS), &Category { id: VERSIONS, bytes: 1000 + 30, items: 2, busy: false });
        // Profiles of 1.1.4 and the uninstalled 0.9.0; unknown names stay.
        assert_eq!(category(&report, PROFILES), &Category { id: PROFILES, bytes: 150, items: 2, busy: false });
        assert_eq!(category(&report, LINK_CACHE), &Category { id: LINK_CACHE, bytes: 5000, items: 2, busy: false });
        assert_eq!(category(&report, WORK), &Category { id: WORK, bytes: removable, items: 1, busy: false });
        assert!(report.total_used > 4 * 1000 + 5000 + 5000);
        assert!(report.install_free_bytes.is_some_and(|free| free > 0));

        let busy = report_with_busy_link(&layout, &outputs);
        assert_eq!(category(&busy, LINK_CACHE), &Category { id: LINK_CACHE, bytes: 0, items: 0, busy: true });
    }

    fn report_with_busy_link(layout: &Layout, outputs: &[PathBuf]) -> Report {
        let running = HashSet::new();
        report(layout, &scope(outputs, &running, None, true))
    }

    #[test]
    fn clean_removes_only_rebuildable_files_and_never_results() {
        let temp = Temp::new("clean");
        let layout = install(&temp.0);
        write(&temp.0.join("source-work/scan-1/x"), 1000);
        let (output, removable) = finished_output(&temp.0, "Clip");
        let (busy_output, _) = finished_output(&temp.0, "Busy");
        let unfinished = temp.0.join("DubFlow Output/Half - vi/run-1");
        write(&unfinished.join(".dubflow-work/analysis.wav"), 300);
        let outputs = vec![output.clone(), busy_output.clone(), unfinished.clone()];
        let running = HashSet::from([busy_output.clone()]);
        let all: Vec<String> = CATEGORIES.iter().map(|id| id.to_string()).collect();
        let cleaned = clean_with(&layout, &all, &scope(&outputs, &running, None, false), &|from, to| fs::rename(from, to));
        assert_eq!(cleaned, Cleaned { freed_bytes: removable + 1030 + 150 + 1000, skipped: vec![] });

        let work = output.join(WORK_DIR);
        for gone in ["analysis.wav", "tts-cache", "b2-audio/id/gen/tts/tts-1.wav", "package/main.mp4", "package/final_vi_packaged.mp4.partial"] {
            assert!(!work.join(gone).exists(), "{gone} should be removed");
        }
        // Results, the small JSON every later action reads, the hard-linked
        // stem and the publication journal stay.
        for kept in ["final_vi.mp4", "job_manifest.json", "captions_vi.srt", "editable/final_mix.wav",
                     ".dubflow-work/translation.json", ".dubflow-work/transcript.json", ".dubflow-work/checkpoint.json",
                     ".dubflow-work/speakers.json", ".dubflow-work/asr-chunks/0.json", ".dubflow-work/package/main.receipt.json",
                     ".dubflow-work/b2-audio/id/gen/tts_document.json", ".dubflow-work/b2-audio/id/gen/mix/final.wav",
                     ".dubflow-work/export-pending/final_vi.mp4"] {
            assert!(output.join(kept).exists(), "{kept} must stay");
        }
        // Running and unfinished folders are untouched.
        assert!(busy_output.join(".dubflow-work/package/main.mp4").exists());
        assert!(unfinished.join(".dubflow-work/analysis.wav").exists());
        // Kept versions and their profiles; staging folders are not versions.
        for version in ["1.1.9", "1.1.10", "1.1.24"] {
            assert!(temp.0.join("versions").join(version).is_dir());
            assert!(temp.0.join("control/webview2").join(hex(version)).is_dir());
        }
        assert!(!temp.0.join("versions/1.1.4").exists() && !temp.0.join("versions/.dubflow-trash-old").exists());
        assert!(!temp.0.join("control/webview2").join(hex("1.1.4")).exists());
        assert!(!temp.0.join("control/webview2").join(hex("0.9.0")).exists());
        assert!(temp.0.join("control/webview2/not-hex").is_dir() && temp.0.join("versions/.1.2.0.staging").is_dir());
        assert!(temp.0.join("source-work").is_dir() && !temp.0.join("source-work/scan-1").exists());
        assert!(fs::read_dir(temp.0.join("versions")).unwrap().flatten().all(|entry| !entry.file_name().to_string_lossy().starts_with(TRASH_PREFIX)));
    }

    #[test]
    fn recent_and_active_work_waits_and_a_busy_link_cache_stays() {
        let temp = Temp::new("recent");
        let layout = Layout { install: None, data: Some(temp.0.clone()), running_exe: None };
        write(&temp.0.join("source-work/scan-1/x"), 1000);
        let (output, _) = finished_output(&temp.0, "Clip");
        let outputs = vec![output.clone()];
        let running = HashSet::new();
        let now = Scope { outputs: &outputs, running: &running, older_than: Some(Duration::from_secs(3 * 86_400)), link_busy: true, now: SystemTime::now() };
        let cleaned = clean_with(&layout, &[WORK.into(), LINK_CACHE.into()], &now, &|from, to| fs::rename(from, to));
        assert_eq!(cleaned.freed_bytes, 0);
        assert_eq!(cleaned.skipped, vec!["Bộ đệm tải link (đang tải link)".to_owned()]);
        assert!(output.join(".dubflow-work/package/main.mp4").exists() && temp.0.join("source-work/scan-1/x").exists());
        // Older than three days, but a package render is running there.
        fs::write(output.join(".dubflow-work/package/status.json"), r#"{"state":"RUNNING"}"#).unwrap();
        let later = scope(&outputs, &running, Some(Duration::from_secs(3 * 86_400)), false);
        assert_eq!(clean_with(&layout, &[WORK.into()], &later, &|from, to| fs::rename(from, to)).freed_bytes, 0);
        // Finished render: now it goes.
        fs::write(output.join(".dubflow-work/package/status.json"), r#"{"state":"DONE"}"#).unwrap();
        assert!(clean_with(&layout, &[WORK.into()], &later, &|from, to| fs::rename(from, to)).freed_bytes > 0);
        assert!(!output.join(".dubflow-work/package/main.mp4").exists());
    }

    #[test]
    fn a_folder_in_use_is_skipped_whole_and_reported() {
        let temp = Temp::new("in-use");
        let layout = install(&temp.0);
        let outputs = vec![];
        let running = HashSet::new();
        let refuse = |_: &Path, _: &Path| -> io::Result<()> { Err(io::Error::from(io::ErrorKind::PermissionDenied)) };
        let cleaned = clean_with(&layout, &[VERSIONS.into()], &scope(&outputs, &running, None, false), &refuse);
        // The old trash folder needs no rename and goes; 1.1.4 stays complete.
        assert_eq!(cleaned, Cleaned { freed_bytes: 30, skipped: vec!["Bản cài 1.1.4".to_owned()] });
        assert!(temp.0.join("versions/1.1.4/app/bin/DubFlow.exe").is_file());
    }

    #[cfg(windows)]
    #[test]
    fn a_locked_file_keeps_its_whole_version_folder() {
        use std::os::windows::fs::OpenOptionsExt;
        let temp = Temp::new("locked");
        let layout = install(&temp.0);
        let held = fs::OpenOptions::new().read(true).share_mode(0).open(temp.0.join("versions/1.1.4/app/bin/DubFlow.exe")).unwrap();
        let outputs = vec![];
        let running = HashSet::new();
        let cleaned = clean_with(&layout, &[VERSIONS.into()], &scope(&outputs, &running, None, false), &|from, to| fs::rename(from, to));
        assert_eq!(cleaned.skipped, vec!["Bản cài 1.1.4".to_owned()]);
        assert!(temp.0.join("versions/1.1.4/app/bin/DubFlow.exe").is_file());
        drop(held);
        let cleaned = clean_with(&layout, &[VERSIONS.into()], &scope(&outputs, &running, None, false), &|from, to| fs::rename(from, to));
        assert_eq!(cleaned, Cleaned { freed_bytes: 1000, skipped: vec![] });
    }

    #[cfg(windows)]
    #[test]
    fn a_running_program_keeps_its_version_even_when_the_folder_could_be_renamed() {
        use std::os::windows::fs::OpenOptionsExt;
        let temp = Temp::new("running");
        let layout = install(&temp.0);
        // Like a running image: shared for reading and deleting, so a rename alone would not notice.
        let held = fs::OpenOptions::new().read(true).share_mode(1 | 4).open(temp.0.join("versions/1.1.4/app/bin/DubFlow.exe")).unwrap();
        let outputs = vec![];
        let running = HashSet::new();
        let cleaned = clean_with(&layout, &[VERSIONS.into()], &scope(&outputs, &running, None, false), &|from, to| fs::rename(from, to));
        assert_eq!(cleaned.skipped, vec!["Bản cài 1.1.4".to_owned()]);
        assert!(temp.0.join("versions/1.1.4/app/bin/DubFlow.exe").is_file());
        drop(held);
    }

    #[test]
    fn paths_outside_the_roots_and_links_are_never_removed() {
        let temp = Temp::new("outside");
        let outside = temp.0.join("user/keep.wav");
        write(&outside, 100);
        let root = temp.0.join("out/.dubflow-work");
        fs::create_dir_all(&root).unwrap();
        let candidate = Candidate { path: outside.clone(), root: root.clone(), bytes: 100, dir: false, label: "x".into() };
        assert_eq!(remove(&candidate, None, &|from, to| fs::rename(from, to)), (0, false));
        let escape = Candidate { path: root.join("../../user/keep.wav"), root: root.clone(), bytes: 100, dir: false, label: "x".into() };
        assert_eq!(remove(&escape, None, &|from, to| fs::rename(from, to)), (0, false));
        assert!(outside.is_file());
        // The running executable's folder is refused even inside a root.
        let exe = temp.0.join("versions/9.9.9/DubFlow.exe");
        write(&exe, 10);
        let running = Candidate { path: temp.0.join("versions/9.9.9"), root: temp.0.join("versions"), bytes: 10, dir: true, label: "x".into() };
        assert_eq!(remove(&running, Some(&exe.canonicalize().unwrap()), &|from, to| fs::rename(from, to)), (0, false));
        assert!(exe.is_file());
    }

    #[cfg(windows)]
    #[test]
    fn a_junction_inside_a_work_folder_is_not_followed() {
        let temp = Temp::new("junction");
        let (output, _) = finished_output(&temp.0, "Clip");
        let victim = temp.0.join("user-videos");
        write(&victim.join("holiday.mp4"), 500);
        let link = output.join(".dubflow-work/linked");
        // mklink does not take the `\\?\` form canonicalize returns.
        let junction = |link: &Path, target: &Path| {
            let plain = |path: &Path| PathBuf::from(path.to_string_lossy().trim_start_matches(r"\\?\"));
            std::process::Command::new("cmd").args(["/d", "/c", "mklink", "/J"]).arg(plain(link)).arg(plain(target))
                .stdout(std::process::Stdio::null()).status().unwrap().success()
        };
        assert!(junction(&link, &victim));
        // A junction named like a cache folder is skipped as well.
        let cache_link = output.join(".dubflow-work/tts-cache");
        fs::remove_dir_all(&cache_link).unwrap();
        assert!(junction(&cache_link, &victim));
        let layout = Layout { install: None, data: None, running_exe: None };
        let outputs = vec![output.clone()];
        let running = HashSet::new();
        clean_with(&layout, &[WORK.into()], &scope(&outputs, &running, None, false), &|from, to| fs::rename(from, to));
        assert!(victim.join("holiday.mp4").is_file());
        assert!(!output.join(".dubflow-work/analysis.wav").exists());
        fs::remove_dir(&link).unwrap();
        fs::remove_dir(&cache_link).unwrap();
    }

    #[test]
    fn outputs_come_from_the_queue_and_the_results_roots() {
        let temp = Temp::new("outputs");
        let default_root = temp.0.join("Videos/DubFlow Output");
        let removed = default_root.join("Old - vi/run-a");
        fs::create_dir_all(&removed).unwrap();
        let chosen = temp.0.join("D/DubFlow Output/Clip - vi/run-b");
        let sibling = temp.0.join("D/DubFlow Output/Other - vi/run-c");
        fs::create_dir_all(&chosen).unwrap();
        fs::create_dir_all(&sibling).unwrap();
        let stray = temp.0.join("Documents/project");
        fs::create_dir_all(&stray).unwrap();
        let given = vec![chosen.to_string_lossy().into_owned(), stray.to_string_lossy().into_owned(), "relative\\DubFlow Output\\x".into()];
        let outputs = collect_outputs(&given, Some(default_root));
        let expected: Vec<PathBuf> = [removed, chosen, sibling].iter().map(|path| path.canonicalize().unwrap()).collect::<BTreeSet<_>>().into_iter().collect();
        assert_eq!(outputs, expected);
    }
}
