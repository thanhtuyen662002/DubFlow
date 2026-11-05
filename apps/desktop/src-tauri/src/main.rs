// A release build is a windowed program: without this, Windows opens a
// terminal next to the app on every launch. Every console tool the host starts
// (Python, FFmpeg, the supervisor) uses CREATE_NO_WINDOW.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod ai_keys;
mod compilation;
mod cover_graph;
mod discovery;
mod docu;
mod edit;
mod package;
mod shorts;
mod reframe;
mod product;
mod sfx;
mod source;
mod source_credit;
mod storage;
mod updater;
mod work_lease;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Debug, Serialize)]
struct ReleaseInfo {
    version: String,
    channel: String,
    backend: String,
}

#[derive(Debug, Serialize)]
struct StartJobResponse {
    job_id: String,
    status_path: String,
    output_dir: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct VoiceChoice {
    voice_id: String,
    name: String,
    gender: String,
    accent: String,
    style: String,
    description: String,
    voice_version: String,
    approved: bool,
}

#[derive(Debug, Serialize)]
struct VoiceCatalog {
    default_voice_id: String,
    voices: Vec<VoiceChoice>,
}

#[derive(Deserialize)]
struct VoiceManifest {
    schema_version: u32,
    backend: String,
    voice_id: String,
    approved: bool,
    voices: Vec<VoiceChoice>,
}

fn valid_voice_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 96 &&
        value.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn load_voice_catalog(root: &Path) -> Result<VoiceCatalog, String> {
    let path = root.join("app/models/manifests/production-vieneu-v1.json");
    if fs::metadata(&path).map_err(|_| fail("Danh mục giọng chưa được cài đặt"))?.len() > 128 * 1024 {
        return Err(fail("Danh mục giọng quá lớn"));
    }
    let text = fs::read_to_string(path).map_err(|error| fail(format!("Không đọc được danh mục giọng: {error}")))?;
    let manifest: VoiceManifest = serde_json::from_str(&text).map_err(|_| fail("Danh mục giọng không hợp lệ"))?;
    let mut ids = std::collections::HashSet::new();
    if manifest.schema_version != 1 || manifest.backend != "vieneu-v3-turbo-onnx-v1" || !manifest.approved
        || manifest.voices.is_empty() || manifest.voices.len() > 64
        || manifest.voices.iter().any(|voice| {
            !valid_voice_id(&voice.voice_id) || !ids.insert(voice.voice_id.clone()) || !voice.approved ||
            [&voice.name, &voice.gender, &voice.accent, &voice.style, &voice.description, &voice.voice_version]
                .iter().any(|value| value.is_empty() || value.chars().count() > 160 || value.chars().any(char::is_control))
        }) || !ids.contains(&manifest.voice_id)
    {
        return Err(fail("Danh mục giọng không hợp lệ"));
    }
    Ok(VoiceCatalog { default_voice_id: manifest.voice_id, voices: manifest.voices })
}

#[tauri::command]
fn voice_catalog() -> Result<VoiceCatalog, String> {
    load_voice_catalog(&version_root()?)
}

#[derive(Debug, Deserialize)]
struct StatusEnvelope {
    status: Value,
    output_path: Option<String>,
}

fn fail(message: impl Into<String>) -> String {
    message.into().chars().take(4096).collect()
}

fn validate_id(value: &str) -> Result<(), String> {
    if value.is_empty() || value.len() > 128 || value == "." || value == ".." || value.chars().any(|character| character.is_control()) || value.bytes().any(|byte| byte == b'/' || byte == b'\\') {
        return Err(fail("job_id is invalid"));
    }
    Ok(())
}

fn absolute_file(value: &str, label: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if !path.is_absolute() {
        return Err(fail(format!("{label} must be an absolute path")));
    }
    let resolved = path.canonicalize().map_err(|_| fail(format!("{label} is unavailable")))?;
    if !resolved.is_file() {
        return Err(fail(format!("{label} must be a regular file")));
    }
    Ok(resolved)
}

fn version_root() -> Result<PathBuf, String> {
    let executable = std::env::current_exe().map_err(|error| fail(format!("unable to resolve desktop host: {error}")))?;
    let bin = executable.parent().ok_or_else(|| fail("desktop host has no parent directory"))?;
    let app = bin.parent().ok_or_else(|| fail("desktop host has no app root"))?;
    Ok(app.parent().unwrap_or(app).to_path_buf())
}

/// Where models, job state and the browser profile live: the install folder
/// the user chose in setup (`<root>\versions\<version>\app\bin\DubFlow.exe`),
/// so another drive really holds the large data. Development builds outside
/// that layout use `%LOCALAPPDATA%\DubFlow`, the default install folder.
fn data_root() -> Result<PathBuf, String> {
    if let Some(root) = version_root().ok().as_deref().and_then(updater::install_root) {
        return Ok(root);
    }
    let local_app_data = std::env::var_os("LOCALAPPDATA").ok_or_else(|| fail("LOCALAPPDATA is unavailable"))?;
    Ok(PathBuf::from(local_app_data).join("DubFlow"))
}

fn control_root() -> Result<PathBuf, String> {
    let root = data_root()?;
    fs::create_dir_all(root.join("control").join("jobs")).map_err(|error| fail(format!("unable to create DubFlow control root: {error}")))?;
    Ok(root)
}

fn webview_data_root(data_root: &Path, version: &str) -> Result<PathBuf, String> {
    if !data_root.is_absolute() || version.is_empty() || version.len() > 64
        || !version.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(fail("desktop profile root or release version is invalid"));
    }
    // Preserve version identity even on case-insensitive Windows filesystems.
    let encoded: String = version.bytes().map(|byte| format!("{byte:02x}")).collect();
    Ok(data_root.join("control").join("webview2").join(encoded))
}

const LOCAL_STORAGE: [&str; 3] = ["EBWebView", "Default", "Local Storage"];
/// Files a version's first launch carries over besides localStorage: the
/// cookie store (sign-ins such as Douyin) and `Local State`, which holds the
/// key the cookie values are encrypted with. Without that key the copied
/// cookies cannot be read, so both travel together.
const INHERITED_FILES: [&[&str]; 4] = [
    &["EBWebView", "Local State"],
    &["EBWebView", "Default", "Network", "Cookies"],
    &["EBWebView", "Default", "Network", "Cookies-journal"],
    &["EBWebView", "Default", "Cookies"],
];

fn profile_path(dir: &Path, parts: &[&str]) -> PathBuf {
    parts.iter().fold(dir.to_path_buf(), |path, part| path.join(part))
}

/// A version's first launch starts from the newest earlier version's
/// localStorage (queue, results folder, saved choices) and cookies (account
/// sign-ins) instead of an empty profile. Best effort; the earlier profile is
/// only read, so rolling back still finds its own data.
fn inherit_previous_profile(profile: &Path) {
    if profile.exists() {
        return;
    }
    let Some(root) = profile.parent() else { return };
    // Dot-prefixed folders are cleanup leftovers being deleted, never a profile.
    let newest = fs::read_dir(root).into_iter().flatten().flatten()
        .filter(|entry| !entry.file_name().to_string_lossy().starts_with('.'))
        .map(|entry| entry.path())
        .filter_map(|dir| fs::metadata(profile_path(&dir, &LOCAL_STORAGE).join("leveldb"))
            .and_then(|metadata| metadata.modified()).ok().map(|modified| (modified, dir)))
        .max_by_key(|(modified, _)| *modified);
    let Some((_, previous)) = newest else { return };
    let _ = copy_work_cache(&profile_path(&previous, &LOCAL_STORAGE), &profile_path(profile, &LOCAL_STORAGE), &[]);
    for parts in INHERITED_FILES {
        let (from, to) = (profile_path(&previous, parts), profile_path(profile, parts));
        if from.is_file() && !to.exists() {
            if let Some(parent) = to.parent() {
                let _ = fs::create_dir_all(parent).and_then(|_| fs::copy(&from, &to));
            }
        }
    }
}

#[cfg(windows)]
fn configure_webview_data() -> Result<(), String> {
    let profile = webview_data_root(&data_root()?, &release_info().version)?;
    let runtime = version_root()?.canonicalize().map_err(|error| fail(format!("unable to resolve desktop runtime: {error}")))?;
    let ancestor = profile.ancestors().find(|path| path.exists())
        .ok_or_else(|| fail("desktop profile has no available parent"))?;
    if ancestor.canonicalize().map_err(|error| fail(format!("unable to resolve desktop profile parent: {error}")))?.starts_with(&runtime) {
        return Err(fail("desktop profile cannot be inside the immutable runtime"));
    }
    inherit_previous_profile(&profile);
    fs::create_dir_all(&profile).map_err(|error| fail(format!("unable to create desktop profile: {error}")))?;
    let profile = profile.canonicalize().map_err(|error| fail(format!("unable to resolve desktop profile: {error}")))?;
    if profile.starts_with(runtime) {
        return Err(fail("desktop profile cannot be inside the immutable runtime"));
    }
    // WebView2 reads this documented override during environment creation.
    // Set it before Tauri starts threads or creates any configured windows.
    std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", profile);
    Ok(())
}

fn command_with_no_window(program: &Path) -> Command {
    let mut command = Command::new(program);
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

fn supervisor_binary(root: &Path) -> Result<PathBuf, String> {
    let candidate = root.join("app").join("bin").join("dubflow-supervisor.exe");
    if candidate.is_file() {
        return Ok(candidate);
    }
    if let Some(value) = std::env::var_os("DUBFLOW_SUPERVISOR") {
        let fallback = PathBuf::from(value);
        if fallback.is_file() {
            return Ok(fallback);
        }
    }
    Err(fail(format!("supervisor binary is missing: {}", candidate.display())))
}

fn status_path(root: &Path, job_id: &str) -> Result<PathBuf, String> {
    validate_id(job_id)?;
    Ok(root.join("control").join("jobs").join(format!("{job_id}.json")))
}

fn default_job_output(source: &Path, job_id: &str) -> Result<PathBuf, String> {
    let media_store = data_root().ok().map(|root| root.join("source-media"));
    choose_output(None, None, source, job_id, media_store.as_deref(), std::env::var_os("USERPROFILE").map(PathBuf::from).as_deref())
}

/// The results folder for a job, from every route `start_job` offers: the
/// folder the job asked for, a results root the user chose, or next to the
/// source. Whatever the route, a folder inside the link service's
/// `source-media` store (deep, and reserved for sources) is replaced by
/// `<profile>\Videos\DubFlow Output\…`; without a profile that is an error,
/// never a silent fallback into the store.
fn choose_output(requested: Option<&str>, results_root: Option<&Path>, source: &Path, job_id: &str,
                 media_store: Option<&Path>, profile: Option<&Path>) -> Result<PathBuf, String> {
    let candidate = match (requested, results_root) {
        (Some(value), _) => PathBuf::from(value),
        (None, Some(base)) => job_output_in(base, source, job_id)?,
        (None, None) => job_output_in(source.parent().unwrap_or(Path::new(".")), source, job_id)?,
    };
    match media_store {
        Some(store) if within(&candidate, store) => {
            let profile = profile.ok_or_else(|| fail("Không xác định được thư mục Videos để lưu kết quả"))?;
            job_output_in(&profile.join("Videos"), source, job_id)
        }
        _ => Ok(candidate),
    }
}

/// Whether `path` (which need not exist yet) lies inside `root`, comparing
/// the canonical form of its nearest existing ancestor.
fn within(path: &Path, root: &Path) -> bool {
    let Ok(root) = root.canonicalize() else { return false };
    let mut existing = path;
    let mut rest = Vec::new();
    while !existing.exists() {
        let (Some(parent), Some(name)) = (existing.parent(), existing.file_name()) else { return false };
        rest.push(name.to_os_string());
        existing = parent;
    }
    let Ok(mut resolved) = existing.canonicalize() else { return false };
    for name in rest.iter().rev() {
        resolved.push(name);
    }
    resolved.starts_with(root)
}

/// Longest video title kept in the output folder name, in characters.
const OUTPUT_TITLE_CHARS: usize = 40;

/// `<base>/DubFlow Output/<title> - vi/run-<12 hex>`; base is the source folder
/// or a user-chosen results folder.
///
/// Windows paths stop at 260 characters and the work folders nest beneath
/// this one, so the title is shortened and the run folder is 48 bits of the
/// job ID's SHA-256: lowercase, never a reserved name, and different for IDs
/// that differ only in case.
fn job_output_in(base: &Path, source: &Path, job_id: &str) -> Result<PathBuf, String> {
    use sha2::{Digest, Sha256};
    validate_id(job_id)?;
    let stem = source.file_stem().and_then(|name| name.to_str()).unwrap_or("video");
    let title: String = stem.chars().take(OUTPUT_TITLE_CHARS).collect();
    let title = title.trim_end_matches([' ', '.']);
    let digest = Sha256::digest(job_id.as_bytes());
    let run: String = digest.iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    Ok(base.join("DubFlow Output")
        .join(format!("{} - vi", if title.is_empty() { "video" } else { title }))
        .join(format!("run-{run}")))
}

#[tauri::command]
fn release_info() -> ReleaseInfo {
    ReleaseInfo {
        version: option_env!("DUBFLOW_RELEASE_VERSION")
            .unwrap_or(env!("CARGO_PKG_VERSION"))
            .to_owned(),
        channel: option_env!("DUBFLOW_RELEASE_CHANNEL")
            .unwrap_or("candidate")
            .to_owned(),
        backend: "rust-supervisor".to_owned(),
    }
}

/// Run the bundled Python with `args` from the app folder and parse the single
/// JSON object it prints. Off the UI thread: network and hashing can be slow.
pub(crate) async fn python_json(args: Vec<std::ffi::OsString>, what: &'static str) -> Result<Value, String> {
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).arg("-B").args(args);
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output())
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("Không chạy được {what}: {error}")))?;
    let line = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str::<Value>(line.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail(format!("{what} không trả về dữ liệu")))
}

/// Write a small diagnostic zip for one job to the Desktop and show it in
/// Explorer: the job's reports and metadata, never its video or audio, other
/// jobs (the job database) or local paths (Issue #351).
#[tauri::command]
async fn create_report(job_id: String, output_dir: Option<String>) -> Result<String, String> {
    validate_id(&job_id)?;
    let root = data_root()?;
    let mut args: Vec<std::ffi::OsString> = vec!["-m".into(), "engine.dubflow.support.report".into(),
        "--job-status".into(), status_path(&root, &job_id)?.into_os_string()];
    if let Some(dir) = output_dir.map(PathBuf::from) {
        // Only a DubFlow results folder: the report reads files inside it.
        if !dir.is_absolute() || !dir.is_dir() || !dir.components().any(|part| part.as_os_str() == "DubFlow Output") {
            return Err(fail("Thư mục kết quả không hợp lệ"));
        }
        args.extend(["--output-dir".into(), dir.into_os_string()]);
    }
    // The source worker's traceback log (SOURCE_WORKER_FAILED): link jobs have no results folder.
    let source_log = root.join("source-work").join("worker-errors.log");
    if source_log.is_file() {
        args.extend(["--source-log".into(), source_log.into_os_string()]);
    }
    let result = python_json(args, "bộ tạo báo cáo").await?;
    let path = result.get("path").and_then(Value::as_str)
        .ok_or_else(|| fail(result.get("message").and_then(Value::as_str).unwrap_or("Không tạo được báo cáo")))?
        .to_owned();
    let _ = Command::new("explorer.exe").arg(format!("/select,{}", display_path(Path::new(&path)))).spawn();
    Ok(path)
}

fn install_root_or_unsupported() -> Result<PathBuf, String> {
    updater::install_root(&version_root()?).ok_or_else(|| fail("Bản phát triển không hỗ trợ cập nhật tự động"))
}

/// Ask GitHub Releases for a newer signed version on this build's channel.
#[tauri::command]
async fn check_update() -> Result<Value, String> {
    if updater::install_root(&version_root()?).is_none() {
        return Ok(json!({"ok": true, "available": false, "unsupported": true}));
    }
    let info = release_info();
    python_json(["-m", "engine.dubflow.update", "check", "--current", &info.version, "--channel", &info.channel]
        .into_iter().map(Into::into).collect(), "bộ cập nhật").await
}

/// Download the newer version's setup into `<install root>\updates`, verified
/// against the signed manifest's SHA-256 and size.
#[tauri::command]
async fn download_update() -> Result<Value, String> {
    let updates = updater::updates_dir(&install_root_or_unsupported()?);
    fs::create_dir_all(&updates).map_err(|error| fail(format!("Không tạo được thư mục cập nhật: {error}")))?;
    let info = release_info();
    let mut args: Vec<std::ffi::OsString> = ["-m", "engine.dubflow.update", "download", "--current", &info.version, "--channel", &info.channel, "--dest"]
        .into_iter().map(Into::into).collect();
    args.push(updates.into_os_string());
    python_json(args, "bộ cập nhật").await
}

/// Run a downloaded setup and close DubFlow so it can start the new version.
/// The setup installs side by side; the running version stays on disk.
#[tauri::command]
fn install_update(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let root = install_root_or_unsupported()?;
    let setup = PathBuf::from(&path);
    if !updater::is_update_setup(&root, &setup) {
        return Err(fail("Tệp cài đặt cập nhật không hợp lệ"));
    }
    Command::new(&setup).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn()
        .map_err(|error| fail(format!("Không chạy được bộ cài: {error}")))?;
    app.exit(0);
    Ok(())
}

fn bootstrap_args(mode: &[&str]) -> Result<Vec<std::ffi::OsString>, String> {
    let root = version_root()?;
    let install = install_root_or_unsupported()?;
    let mut args: Vec<std::ffi::OsString> = vec![root.join("app").join("packaging").join("release").join("bootstrap.py").into_os_string()];
    args.extend(mode.iter().map(Into::into));
    args.push("--install-root".into());
    args.push(install.into_os_string());
    Ok(args)
}

/// Installed versions, newest first, and which one is current.
#[tauri::command]
async fn list_versions() -> Result<Value, String> {
    python_json(bootstrap_args(&["--list"])?, "trình quản lý phiên bản").await
}

/// Make an installed version current (rollback or roll forward). It is
/// verified file by file before the switch; it takes effect on next start.
#[tauri::command]
async fn activate_version(version: String) -> Result<Value, String> {
    if !updater::valid_version(&version) {
        return Err(fail("Phiên bản không hợp lệ"));
    }
    python_json(bootstrap_args(&["--activate", &version])?, "trình quản lý phiên bản").await
}

/// Start the current version through the user launcher and close this one.
#[tauri::command]
fn restart_app(app: tauri::AppHandle) -> Result<(), String> {
    let launcher = install_root_or_unsupported()?.join("DubFlow.cmd");
    if !launcher.is_file() {
        return Err(fail("Không tìm thấy trình khởi động DubFlow"));
    }
    let mut command = Command::new(std::env::var_os("ComSpec").unwrap_or_else(|| "cmd.exe".into()));
    command.args(["/d", "/c"]).arg(&launcher).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command.spawn().map_err(|error| fail(format!("Không khởi động lại được: {error}")))?;
    app.exit(0);
    Ok(())
}

const VIDEO_EXTENSIONS: [&str; 14] = ["mp4", "mkv", "mov", "avi", "webm", "m4v", "flv", "ts", "m2ts", "mts", "3gp", "wmv", "mpg", "mpeg"];
const MAX_EXPANDED_VIDEOS: usize = 500;

/// Dropped paths -> video files: files pass through, folders are searched up
/// to three levels deep (skipping DubFlow's own outputs), capped at 500.
fn expand_videos(paths: &[PathBuf]) -> Vec<PathBuf> {
    fn is_video(path: &Path) -> bool {
        path.extension().and_then(|ext| ext.to_str())
            .is_some_and(|ext| VIDEO_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()))
    }
    fn walk(dir: &Path, depth: usize, found: &mut Vec<PathBuf>) {
        let Ok(entries) = fs::read_dir(dir) else { return };
        let mut entries: Vec<PathBuf> = entries.filter_map(|entry| entry.ok().map(|entry| entry.path())).collect();
        entries.sort();
        for path in entries {
            if found.len() >= MAX_EXPANDED_VIDEOS {
                return;
            }
            let Ok(metadata) = fs::symlink_metadata(&path) else { continue };
            if metadata.is_file() && is_video(&path) {
                found.push(path);
            } else if metadata.is_dir() && depth < 3
                && path.file_name().is_some_and(|name| name != "DubFlow Output" && name != ".dubflow-work") {
                walk(&path, depth + 1, found);
            }
        }
    }
    let mut found = Vec::new();
    for path in paths {
        if found.len() >= MAX_EXPANDED_VIDEOS {
            break;
        }
        if path.is_dir() {
            walk(path, 1, &mut found);
        } else if is_video(path) {
            found.push(path.clone());
        }
    }
    found
}

#[tauri::command]
fn expand_video_paths(paths: Vec<String>) -> Vec<String> {
    let paths: Vec<PathBuf> = paths.into_iter().take(1000).map(PathBuf::from).filter(|path| path.is_absolute()).collect();
    expand_videos(&paths).into_iter().map(|path| path.to_string_lossy().into_owned()).collect()
}

#[tauri::command]
fn pick_folder() -> Option<String> {
    rfd::FileDialog::new().set_title("Chọn thư mục lưu kết quả").pick_folder()
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
fn pick_files() -> Result<Vec<String>, String> {
    let files = rfd::FileDialog::new()
        .add_filter("Video", &VIDEO_EXTENSIONS)
        .pick_files()
        .unwrap_or_default();
    Ok(files.into_iter().map(|path| path.to_string_lossy().into_owned()).collect())
}

/// One source-caption rectangle to blur, as fractions of the frame.
#[derive(Debug, Deserialize, Serialize)]
struct BlurRegion {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[tauri::command]
fn start_job(job_id: String, source_path: String, output_dir: Option<String>, enable_dubbing: Option<bool>, tts_voice_id: Option<String>, burn_in: Option<bool>, source_language: Option<String>, output_root: Option<String>, subtitle_box: Option<bool>, translation_engine: Option<String>, blur_regions: Option<Vec<BlurRegion>>, blur_auto: Option<bool>, multi_voice: Option<bool>, speaker_voices: Option<std::collections::BTreeMap<String, String>>, separate_background: Option<bool>, cpu_only: Option<bool>, subtitle_style: Option<edit::SubtitleStyle>, cover_style: Option<edit::CoverStyle>) -> Result<StartJobResponse, String> {
    validate_id(&job_id)?;
    if let Some(style) = &subtitle_style {
        style.validate()?;
    }
    if let Some(style) = &cover_style {
        style.validate()?;
    }
    let source = absolute_file(&source_path, "source_path")?;
    let root = version_root()?;
    let selected_voice = if enable_dubbing.unwrap_or(false) {
        let catalog = load_voice_catalog(&root)?;
        let selected = tts_voice_id.ok_or_else(|| fail("Hãy chọn giọng trước khi lồng tiếng"))?;
        if !catalog.voices.iter().any(|voice| voice.voice_id == selected) {
            return Err(fail("Giọng đã chọn không có trong phiên bản này"));
        }
        Some(selected)
    } else {
        None
    };
    let control = control_root()?;
    let status = status_path(&control, &job_id)?;
    let results_root = match (&output_dir, output_root) {
        (None, Some(base)) => {
            let base = PathBuf::from(base);
            if !base.is_absolute() || !base.is_dir() {
                return Err(fail("Thư mục lưu kết quả không tồn tại; hãy chọn lại"));
            }
            Some(base)
        }
        _ => None,
    };
    let media_store = data_root().ok().map(|root| root.join("source-media"));
    let profile = std::env::var_os("USERPROFILE").map(PathBuf::from);
    let output = choose_output(output_dir.as_deref(), results_root.as_deref(), &source, &job_id, media_store.as_deref(), profile.as_deref())?;
    if !output.is_absolute() {
        return Err(fail("output_dir must be an absolute path"));
    }
    fs::create_dir_all(&output).map_err(|error| fail(format!("unable to create output directory: {error}")))?;
    // The last applied look is the default for new videos. A folder that
    // already has one (a new version from the editor, a resumed job) keeps it.
    if let Some(style) = subtitle_style.filter(|_| burn_in != Some(false)) {
        if !output.join(edit::SUBTITLE_STYLE_FILE).exists() {
            edit::write_subtitle_style(&output, &style)?;
        }
    }
    // Likewise the last applied cover style (edge stretch unless changed); the
    // worker only uses it when something is covered.
    if let Some(style) = cover_style {
        if !output.join(edit::COVER_FILE).exists() {
            edit::write_cover(&output, &edit::CoverChoice::with_style(style))?;
        }
    }
    let model_root = control.join("models");
    let supervisor = supervisor_binary(&root)?;
    let mut command = command_with_no_window(&supervisor);
    let mut args = vec![
        "run".to_owned(), "--root".to_owned(), root.to_string_lossy().into_owned(), "--job-id".to_owned(), job_id.clone(),
        "--source".to_owned(), source.to_string_lossy().into_owned(), "--output-dir".to_owned(), output.to_string_lossy().into_owned(),
        "--data-root".to_owned(), control.to_string_lossy().into_owned(), "--model-root".to_owned(), model_root.to_string_lossy().into_owned(),
        "--status-path".to_owned(), status.to_string_lossy().into_owned(),
    ];
    if let Some(voice_id) = selected_voice {
        args.extend(["--enable-dubbing".into(), "--tts-voice-id".into(), voice_id]);
    }
    if burn_in == Some(false) {
        args.push("--no-burn-in".into());
    } else if subtitle_box == Some(true) {
        args.push("--subtitle-box".into());
    }
    match source_language.as_deref() {
        None | Some("auto") => {}
        Some(language @ ("zh" | "en")) => args.extend(["--source-language".into(), language.to_owned()]),
        Some(_) => return Err(fail("Ngôn ngữ gốc không được hỗ trợ")),
    }
    match translation_engine.as_deref() {
        None | Some("argos") => {}
        // Opt-in HachimiMT-60 (Chinese only; see docs/licenses/hachimi-mt-60.md).
        Some("hachimi") => args.extend(["--translation-engine".into(), "hachimi".into()]),
        Some(_) => return Err(fail("Bộ dịch không được hỗ trợ")),
    }
    if let Some(regions) = blur_regions.filter(|regions| !regions.is_empty()) {
        let inside = |r: &BlurRegion| [r.x, r.y, r.w, r.h].iter().all(|v| v.is_finite())
            && r.x >= 0.0 && r.y >= 0.0 && r.w >= 0.01 && r.h >= 0.01 && r.x + r.w <= 1.000001 && r.y + r.h <= 1.000001;
        if regions.len() > 16 || !regions.iter().all(inside) {
            return Err(fail("Vùng làm mờ không hợp lệ; hãy chọn lại"));
        }
        args.extend(["--blur-regions".into(), serde_json::to_string(&regions).map_err(|error| fail(error.to_string()))?]);
    }
    if blur_auto == Some(true) {
        args.push("--blur-auto".into());
    }
    if separate_background == Some(true) && enable_dubbing == Some(true) {
        args.push("--separate-background".into());
    }
    if cpu_only == Some(true) {
        args.push("--cpu-only".into());
    }
    // Experimental multi-voice only matters when dubbing.
    if multi_voice == Some(true) && enable_dubbing == Some(true) {
        args.push("--multi-voice".into());
        if let Some(voices) = speaker_voices.filter(|voices| !voices.is_empty()) {
            let known = load_voice_catalog(&root)?;
            let valid = voices.len() <= 8 && voices.iter().all(|(speaker, voice)| {
                speaker.len() == 2 && speaker.starts_with('S') && matches!(speaker.as_bytes()[1], b'1'..=b'8')
                    && known.voices.iter().any(|entry| &entry.voice_id == voice)
            });
            if !valid {
                return Err(fail("Giọng theo người nói không hợp lệ"));
            }
            args.extend(["--speaker-voices".into(), serde_json::to_string(&voices).map_err(|error| fail(error.to_string()))?]);
        }
    }
    command.args(args);
    // The supervisor reports through its status file; when it exits before it
    // can (a refused job, a crash), its stderr is the only explanation.
    let log_path = status.with_extension("supervisor.log");
    let log = fs::File::create(&log_path).map_err(|error| fail(format!("unable to create supervisor log: {error}")))?;
    command.stderr(log);
    let child = command.spawn().map_err(|error| fail(format!("unable to start supervisor: {error}")))?;
    if let Ok(mut launched) = launched_jobs().lock() {
        launched.insert(job_id.clone(), std::time::Instant::now());
    }
    watch_supervisor(child, status.clone(), log_path, job_id.clone());
    Ok(StartJobResponse { job_id, status_path: status.to_string_lossy().into_owned(), output_dir: output.to_string_lossy().into_owned() })
}

/// Wait for a launched supervisor off the UI thread. If it fails without having
/// reported a terminal state, record FAILED with its own error so the queue
/// moves on instead of showing (and later re-running) a job nobody processes.
fn watch_supervisor(mut child: std::process::Child, status: PathBuf, log: PathBuf, job_id: String) {
    std::thread::spawn(move || {
        let Ok(exit) = child.wait() else { return };
        if exit.success() {
            return;
        }
        let detail = fs::read_to_string(&log).unwrap_or_default();
        let message = supervisor_failure_message(&detail, exit.code());
        // A duplicate start was refused because another supervisor owns the
        // job; that one reports the job's outcome.
        if message.starts_with("JOB_ALREADY_RUNNING") {
            return;
        }
        let _ = mark_supervisor_failed(&status, &job_id, &message);
    });
}

/// The supervisor's last stderr line without its program prefix, so codes such
/// as JOB_ID_CONFLICT reach the desktop's failure hints unchanged.
fn supervisor_failure_message(stderr: &str, code: Option<i32>) -> String {
    stderr.lines().rev().map(str::trim).find(|line| !line.is_empty())
        .map(|line| line.strip_prefix("DubFlow supervisor:").unwrap_or(line).trim().to_owned())
        .unwrap_or_else(|| format!("SUPERVISOR_EXITED: supervisor stopped with exit code {}", code.map_or("unknown".into(), |code| code.to_string())))
        .chars().take(400).collect()
}

/// Record FAILED unless the supervisor already reported a terminal state.
fn mark_supervisor_failed(status: &Path, job_id: &str, message: &str) -> std::io::Result<()> {
    let mut envelope: Value = fs::read(status).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .filter(|value: &Value| value["status"]["progress"].is_object() && value["status"]["retry"].is_object())
        .unwrap_or_else(|| json!({"job_id": job_id, "output_path": null, "schema_version": 1, "status": {
            "schema_version": 1, "state": "QUEUED", "reason": "queued", "message": "", "checkpoint_id": null,
            "progress": {"completed_units": "0", "total_units": null, "heartbeat_sequence": "0"},
            "retry": {"attempt": "0", "max_attempts": "3", "condition_fingerprint": null},
            "resource": {"kind": null, "held": false, "release_requested": false}}}));
    if matches!(envelope["status"]["state"].as_str(), Some("COMPLETED" | "FAILED" | "BLOCKED_NEEDS_ACTION")) {
        return Ok(());
    }
    let state = &mut envelope["status"];
    state["state"] = json!("FAILED");
    state["reason"] = json!("supervisor_exited");
    state["message"] = json!(message);
    state["resource"]["held"] = json!(false);
    let partial = status.with_extension("json.partial");
    fs::write(&partial, serde_json::to_vec_pretty(&envelope).map_err(std::io::Error::other)?)?;
    fs::rename(&partial, status)
}

/// Jobs this host started in this session. The watcher above reports their
/// fate, so only jobs from an earlier session (power loss, killed app) are
/// probed for a missing supervisor.
fn launched_jobs() -> &'static std::sync::Mutex<std::collections::HashMap<String, std::time::Instant>> {
    static LAUNCHED: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, std::time::Instant>>> = std::sync::OnceLock::new();
    LAUNCHED.get_or_init(Default::default)
}

const STALE_STATUS: std::time::Duration = std::time::Duration::from_secs(20);

/// Whether no supervisor owns a job that its status file still calls active,
/// e.g. after a power loss or a killed app. The supervisor holds an exclusive
/// lock on its execution-lock file for the whole run, so if that lock can be
/// taken here (and immediately released) nothing is processing the job. Only
/// probed when the status has been unchanged for a while and the job was not
/// started by this host session, so a supervisor starting up is never raced.
fn supervisor_gone(data_root: &Path, job_id: &str, status: &Path) -> bool {
    use sha2::{Digest, Sha256};
    let quiet = fs::metadata(status).and_then(|metadata| metadata.modified()).ok()
        .and_then(|modified| modified.elapsed().ok())
        .is_some_and(|age| age >= STALE_STATUS);
    let launched_here = launched_jobs().lock().map(|launched| launched.contains_key(job_id)).unwrap_or(true);
    if !quiet || launched_here {
        return false;
    }
    // Same identity as the supervisor's JobExecutionGuard: canonical database
    // path, a NUL separator and the job id, under ".execution-locks".
    let Ok(database) = fs::canonicalize(data_root.join("control").join("jobs.sqlite3")) else { return false };
    let Some(parent) = database.parent() else { return false };
    let mut key = Sha256::new();
    key.update(database.as_os_str().as_encoded_bytes());
    key.update([0]);
    key.update(job_id.as_bytes());
    let lock = parent.join(".execution-locks").join(format!("{:x}.lock", key.finalize()));
    let Ok(file) = fs::OpenOptions::new().read(true).write(true).open(&lock) else {
        // No lock file: no supervisor ever ran this job in this data root.
        return !lock.exists();
    };
    matches!(file.try_lock(), Ok(()))
}

#[tauri::command]
fn job_status(job_id: String) -> Result<Option<Value>, String> {
    let control = control_root()?;
    let status = status_path(&control, &job_id)?;
    if !status.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&status).map_err(|error| fail(format!("unable to read job status: {error}")))?;
    let mut envelope: StatusEnvelope = serde_json::from_str(&text).map_err(|error| fail(format!("job status is invalid: {error}")))?;
    let active = matches!(envelope.status["state"].as_str(), Some("RUNNING" | "RETRYING" | "RECOVERED" | "WORKER_LOST"));
    if active && supervisor_gone(&control, &job_id, &status) {
        // Interrupted, not failed: starting it again resumes from its checkpoints.
        envelope.status["state"] = json!("QUEUED");
        envelope.status["reason"] = json!("interrupted");
        envelope.status["message"] = json!("Bị gián đoạn khi đang xử lý (máy tắt hoặc app bị đóng); bấm Bắt đầu để tiếp tục từ chỗ dừng");
        envelope.status["resource"]["held"] = json!(false);
    }
    Ok(Some(json!({"status": envelope.status, "output_path": envelope.output_path})))
}

#[tauri::command]
fn cancel_job(job_id: String) -> Result<(), String> {
    validate_id(&job_id)?;
    let root = version_root()?;
    let control = control_root()?;
    let supervisor = supervisor_binary(&root)?;
    let mut command = command_with_no_window(&supervisor);
    let args = ["cancel".to_owned(), "--job-id".to_owned(), job_id, "--data-root".to_owned(), control.to_string_lossy().into_owned()];
    command.args(args);
    let status = command.status().map_err(|error| fail(format!("unable to ask supervisor to cancel: {error}")))?;
    if !status.success() {
        return Err(fail("supervisor rejected cancellation"));
    }
    Ok(())
}

/// Only finished outputs are opened: an existing file below a "DubFlow Output"
/// directory, so the webview cannot ask the host to launch arbitrary files.
fn output_file(path: &str) -> Result<PathBuf, String> {
    let file = absolute_file(path, "output_path")?;
    let inside_output = file.ancestors().any(|dir| dir.file_name().is_some_and(|name| name == "DubFlow Output"));
    let media = file.extension().and_then(|ext| ext.to_str())
        .is_some_and(|ext| ["mp4", "mkv", "mov", "srt", "ass"].contains(&ext.to_ascii_lowercase().as_str()));
    if !inside_output || !media {
        return Err(fail("Chỉ mở được file kết quả của DubFlow"));
    }
    Ok(file)
}

fn display_path(path: &Path) -> String {
    // Explorer does not understand the `\\?\` prefix returned by canonicalize.
    let text = path.to_string_lossy();
    text.strip_prefix(r"\\?\").unwrap_or(&text).to_owned()
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let file = output_file(&path)?;
    Command::new("explorer.exe").arg(display_path(&file)).spawn()
        .map_err(|error| fail(format!("Không mở được file: {error}")))?;
    Ok(())
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let file = output_file(&path)?;
    Command::new("explorer.exe").arg(format!("/select,{}", display_path(&file))).spawn()
        .map_err(|error| fail(format!("Không mở được thư mục: {error}")))?;
    Ok(())
}

const OVERRIDES_FILE: &str = "translation_overrides.json";

/// A finished job's output directory: the parent of its final video, below
/// "DubFlow Output", holding the worker's translation checkpoint.
fn review_dir(output_path: &str) -> Result<PathBuf, String> {
    let video = output_file(output_path)?;
    let dir = video.parent().ok_or_else(|| fail("output has no directory"))?.to_path_buf();
    if !dir.join(".dubflow-work").join("translation.json").is_file() {
        return Err(fail("Không tìm thấy dữ liệu bản dịch của video này"));
    }
    Ok(dir)
}

/// `review_dir` of a dubbing job the supervisor finished: its status in
/// `<data root>\control\jobs` names this video. Only such a video is revised
/// into a sibling "(sửa N)" folder or opened in Phụ đề / Che; a user's folder
/// merely shaped like a job's (Issue #497) is refused.
fn job_review_dir(output_path: &str) -> Result<PathBuf, String> {
    let dir = review_dir(output_path)?;
    if !edit::recorded_job_output(&output_file(output_path)?) {
        return Err(fail("Chỉ sửa được video của một việc lồng tiếng DubFlow đã hoàn tất"));
    }
    Ok(dir)
}

/// Tests: the supervisor's completed record of `video`, as a finished job leaves it.
#[cfg(test)]
pub(crate) fn record_test_job(video: &Path) -> PathBuf {
    use sha2::{Digest, Sha256};
    let key: String = Sha256::digest(display_path(video).as_bytes()).iter().take(8).map(|byte| format!("{byte:02x}")).collect();
    let record = control_root().unwrap().join("control").join("jobs").join(format!("test-{key}.json"));
    fs::write(&record, json!({"schema_version": 1, "job_id": format!("test-{key}"), "status": {"state": "COMPLETED"},
        "output_path": display_path(video)}).to_string()).unwrap();
    record
}

fn read_json(path: &Path, limit: u64) -> Result<Value, String> {
    if fs::metadata(path).map_err(|error| fail(format!("{}: {error}", path.display())))?.len() > limit {
        return Err(fail(format!("{} is too large", path.display())));
    }
    let text = fs::read_to_string(path).map_err(|error| fail(format!("{}: {error}", path.display())))?;
    serde_json::from_str(&text).map_err(|error| fail(format!("{}: {error}", path.display())))
}

/// Cue ids whose dubbing failed in the current audio generation.
fn failed_dub_cues(work: &Path) -> std::collections::HashSet<String> {
    // Windows review paths can carry a verbatim prefix while worker paths do
    // not. Compare resolved paths so both spellings identify the same cache.
    let work = work.canonicalize().unwrap_or_else(|_| work.to_path_buf());
    let checkpoint = read_json(&work.join("checkpoint.json"), 4 * 1024 * 1024).unwrap_or_else(|_| json!({}));
    let stage = &checkpoint["stages"]["b2-audio"];
    let mut paths: Vec<PathBuf> = stage["tts_documents"].as_array().into_iter().flatten()
        .filter_map(|value| value.as_str().map(PathBuf::from)).collect();
    if let Some(path) = stage["tts_document"].as_str() {
        paths.push(PathBuf::from(path));
    }
    paths.sort();
    paths.dedup();
    let documents: Vec<Value> = paths.into_iter().filter_map(|path| path.canonicalize().ok())
        .filter(|path| path.starts_with(&work))
        .filter_map(|path| read_json(&path, 64 * 1024 * 1024).ok()).collect();
    let mut members = std::collections::HashMap::new();
    let mut failures = Vec::new();
    for document in &documents {
        if let Some(mapping) = document["member_cue_ids"].as_object() {
            for (segment, cues) in mapping {
                if let Some(cues) = cues.as_array() {
                    let cues: Vec<String> = cues.iter().filter_map(|cue| cue.as_str().map(str::to_owned)).collect();
                    if !cues.is_empty() {
                        members.insert(segment.clone(), cues);
                    }
                }
            }
        }
        let speech = if document["kind"] == "dubbing_tts_document" { &document["tts_document"] } else { document };
        failures.extend(speech["failures"].as_array().into_iter().flatten().cloned());
    }
    // Mixing can reject an otherwise accepted waveform; flag its members too.
    if let Some(document) = stage["mix_document"].as_str().map(PathBuf::from)
        .and_then(|path| path.canonicalize().ok()).filter(|path| path.starts_with(&work))
        .and_then(|path| read_json(&path, 64 * 1024 * 1024).ok()) {
        failures.extend(document["failures"].as_array().into_iter().flatten().cloned());
    }
    let mut failed = std::collections::HashSet::new();
    for failure in failures {
        if let Some(segment) = failure["segment_id"].as_str() {
            if let Some(cues) = members.get(segment) {
                failed.extend(cues.iter().cloned());
            } else {
                // Old per-cue documents have no member map.
                failed.insert(segment.to_owned());
            }
        }
    }
    failed
}

#[derive(Debug, Serialize)]
struct ReviewCue {
    cue_id: String,
    start_ms: u64,
    end_ms: u64,
    source_text: String,
    machine_text: String,
    text: String,
    /// What the line's voice speaks: the subtitle text unless a subtitle edit
    /// kept the earlier speech (the overrides file's "voice").
    voice_text: String,
    edited: bool,
    dub_failed: bool,
    /// Detected source speaker ("S1"…) when experimental multi-voice ran.
    speaker: Option<String>,
}

/// Detected speakers and their voices for a finished multi-voice video
/// (`job_manifest.json`'s "speakers"); empty when multi-voice did not run.
#[tauri::command]
fn review_speakers(output_path: String) -> Result<Value, String> {
    let dir = review_dir(&output_path)?;
    let manifest = read_json(&dir.join("job_manifest.json"), 16 * 1024 * 1024)?;
    Ok(manifest.get("speakers").filter(|value| value.is_array()).cloned().unwrap_or_else(|| json!([])))
}

/// Offline Vietnamese titles, description and hashtags for posting a
/// finished video (`python -m engine.dubflow.seo`). Off the UI thread.
#[tauri::command]
async fn post_suggestions(output_path: String, source_title: Option<String>) -> Result<Value, String> {
    let dir = review_dir(&output_path)?;
    let timeline = dir.join("editable").join("timeline.json");
    if !timeline.is_file() {
        return Err(fail("Không tìm thấy phụ đề của video này"));
    }
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.seo"]).arg(&timeline);
    if let Some(title) = source_title.filter(|title| !title.trim().is_empty() && title.chars().count() <= 200 && !title.chars().any(char::is_control)) {
        command.args(["--source-title", title.trim()]);
    }
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output())
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("Không chạy được bộ gợi ý: {error}")))?;
    let line = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str::<Value>(line.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ gợi ý không trả về dữ liệu"))
}

#[tauri::command]
fn review_cues(output_path: String) -> Result<Vec<ReviewCue>, String> {
    let dir = review_dir(&output_path)?;
    let work = dir.join(".dubflow-work");
    let translation = read_json(&work.join("translation.json"), 64 * 1024 * 1024)?;
    let overrides = if dir.join(OVERRIDES_FILE).is_file() { read_json(&dir.join(OVERRIDES_FILE), 4 * 1024 * 1024)? } else { json!({}) };
    let failed = failed_dub_cues(&work);
    let speakers = if work.join("speakers.json").is_file() { read_json(&work.join("speakers.json"), 16 * 1024 * 1024)? } else { json!({}) };
    let cues = translation["cues"].as_array().ok_or_else(|| fail("translation.json has no cues"))?;
    cues.iter().map(|cue| {
        let text = |key: &str| cue[key].as_str().unwrap_or_default().to_owned();
        let cue_id = text("cue_id");
        let machine_text = text("translated_text");
        let edited = overrides["cues"][&cue_id].as_str().map(str::to_owned);
        let subtitle = edited.clone().unwrap_or_else(|| machine_text.clone());
        Ok(ReviewCue {
            start_ms: cue["start_ms"].as_u64().ok_or_else(|| fail("cue has no start"))?,
            end_ms: cue["end_ms"].as_u64().ok_or_else(|| fail("cue has no end"))?,
            source_text: text("source_text"),
            dub_failed: failed.contains(&cue_id),
            speaker: speakers["labels"][&cue_id].as_str().map(str::to_owned),
            edited: edited.is_some(),
            voice_text: overrides["voice"][&cue_id].as_str().map_or_else(|| subtitle.clone(), str::to_owned),
            text: subtitle,
            machine_text,
            cue_id,
        })
    }).collect()
}

const REVISION_OF_FILE: &str = "revision_of.json";
/// Work-cache entries every revision rebuilds; copying them would only cost disk.
/// The editor folder belongs to the old version (its exports and their
/// status); only its draft carries over, by `edit::carry_draft`.
const REVISION_SKIP: [&str; 3] = ["b2-audio", "export-pending", "edit"];
const REVISION_SUFFIX: &str = " (sửa ";

/// "Clip - vi (sửa 2)" -> "Clip - vi", so revisions of revisions stay siblings.
fn revision_base(name: &str) -> &str {
    name.strip_suffix(')')
        .and_then(|rest| rest.rfind(REVISION_SUFFIX).map(|index| (index, &rest[index + REVISION_SUFFIX.len()..])))
        .filter(|(_, number)| !number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit()))
        .map_or(name, |(index, _)| &name[..index])
}

/// The first unused sibling folder "<name> (sửa N)" for a review revision.
fn revision_dir(original: &Path) -> Result<PathBuf, String> {
    let parent = original.parent().ok_or_else(|| fail("output has no parent directory"))?;
    let name = original.file_name().and_then(|name| name.to_str()).ok_or_else(|| fail("output folder name is not valid text"))?;
    let base = revision_base(name);
    (1..=999).map(|number| parent.join(format!("{base}{REVISION_SUFFIX}{number})")))
        .find(|candidate| !candidate.exists())
        .ok_or_else(|| fail("Video này đã có quá nhiều bản sửa"))
}

/// Copy the worker cache so a revision reuses transcription and translation;
/// links are skipped so the copy never reaches outside the cache.
fn copy_work_cache(from: &Path, to: &Path, skip: &[&str]) -> std::io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let name = entry.file_name();
        let kind = entry.file_type()?;
        if kind.is_symlink() || skip.iter().any(|skipped| name == *skipped) {
            continue;
        }
        if kind.is_dir() {
            copy_work_cache(&entry.path(), &to.join(&name), &[])?;
        } else {
            fs::copy(entry.path(), to.join(&name))?;
        }
    }
    Ok(())
}

/// What a new version changes: the subtitle texts ("cues"), the text each
/// voice speaks where it differs from its subtitle, the lines to voice again,
/// the look and the cover (None keeps the original's).
pub(crate) struct Revision<'a> {
    pub overrides: &'a std::collections::BTreeMap<String, String>,
    pub voice: &'a std::collections::BTreeMap<String, String>,
    pub revoice: &'a [String],
    pub style: Option<&'a edit::SubtitleStyle>,
    pub cover: Option<&'a edit::CoverChoice>,
}

/// Prepare a separate folder for a review revision and return it. The original
/// output folder is only read, so its validated outputs stay as they were.
fn create_revision(original: &Path, overrides: &std::collections::BTreeMap<String, String>) -> Result<PathBuf, String> {
    let none = std::collections::BTreeMap::new();
    create_revision_with(original, &Revision { overrides, voice: &none, revoice: &[], style: None, cover: None })
}

fn create_revision_with(original: &Path, revision: &Revision) -> Result<PathBuf, String> {
    use sha2::{Digest, Sha256};
    let overrides = revision.overrides;
    let partial = create_revision_temp(original).map_err(|error| fail(format!("Không tạo được thư mục bản sửa: {error}")))?;
    let build = || -> Result<(), String> {
        copy_work_cache(&original.join(".dubflow-work"), &partial.join(".dubflow-work"), &REVISION_SKIP)
            .map_err(|error| fail(format!("Không chép được dữ liệu bản dịch: {error}")))?;
        if !overrides.is_empty() || !revision.voice.is_empty() || !revision.revoice.is_empty() {
            let mut document = json!({"schema_version": 1, "cues": overrides});
            if !revision.voice.is_empty() || !revision.revoice.is_empty() {
                document["voice"] = json!(revision.voice);
                document["revoice"] = json!(revision.revoice);
            }
            let body = serde_json::to_vec_pretty(&document).map_err(|error| fail(error.to_string()))?;
            fs::write(partial.join(OVERRIDES_FILE), body).map_err(|error| fail(format!("Không lưu được bản sửa: {error}")))?;
        }
        match revision.style {
            Some(style) => edit::write_subtitle_style(&partial, style)?,
            None if original.join(edit::SUBTITLE_STYLE_FILE).is_file() => {
                fs::copy(original.join(edit::SUBTITLE_STYLE_FILE), partial.join(edit::SUBTITLE_STYLE_FILE))
                    .map_err(|error| fail(format!("Không chép được kiểu phụ đề: {error}")))?;
            }
            None => {}
        }
        match revision.cover {
            Some(cover) => edit::write_cover(&partial, cover)?,
            None if original.join(edit::COVER_FILE).is_file() => {
                fs::copy(original.join(edit::COVER_FILE), partial.join(edit::COVER_FILE))
                    .map_err(|error| fail(format!("Không chép được cách che chữ: {error}")))?;
            }
            None => {}
        }
        let manifest = fs::read(original.join("job_manifest.json")).ok()
            .map(|bytes| format!("sha256:{:x}", Sha256::digest(bytes)));
        let created = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|error| fail(error.to_string()))?.as_secs();
        let link = json!({"schema_version": 1, "original_output_dir": display_path(original),
            "original_manifest_sha256": manifest, "created_unix": created});
        fs::write(partial.join(REVISION_OF_FILE), serde_json::to_vec_pretty(&link).map_err(|error| fail(error.to_string()))?)
            .map_err(|error| fail(format!("Không lưu được bản sửa: {error}")))
    };
    let result = build().and_then(|()| publish_revision(&partial, original));
    match &result {
        Ok(target) => edit::carry_draft(original, target),
        // Only ever the folder this call created; nothing else is removed.
        Err(_) => {
            let _ = fs::remove_dir_all(&partial);
        }
    }
    result
}

/// A new, uniquely named temporary folder beside `original`. `create_dir`
/// fails on an existing path, so a folder that is already there (a user's,
/// or another save in progress) is never reused or cleaned up.
fn create_revision_temp(original: &Path) -> std::io::Result<PathBuf> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let parent = original.parent().ok_or_else(|| std::io::Error::other("output has no parent directory"))?;
    let name = original.file_name().unwrap_or_default().to_string_lossy();
    for _ in 0..64 {
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().subsec_nanos();
        let candidate = parent.join(format!("{}.revision-{}-{nanos:08x}-{}.partial", revision_base(&name),
            std::process::id(), COUNTER.fetch_add(1, Ordering::Relaxed)));
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "no unique temporary folder name"))
}

/// Publish a finished temporary folder under the first free "(sửa N)" name.
/// The name is reserved with `create_dir`, which fails on any existing path
/// (an empty folder included), so nothing is ever replaced; a name taken by a
/// concurrent save moves on to the next number.
fn publish_revision(partial: &Path, original: &Path) -> Result<PathBuf, String> {
    for _ in 0..64 {
        let target = revision_dir(original)?;
        match fs::create_dir(&target) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(fail(format!("Không tạo được thư mục bản sửa: {error}"))),
        }
        return move_into_reserved(partial, &target)
            .map(|()| target)
            .map_err(|error| fail(format!("Không tạo được thư mục bản sửa: {error}")));
    }
    Err(fail("Không tạo được thư mục bản sửa: tên thư mục liên tục bị trùng"))
}

/// Move every entry of `partial` into the freshly reserved, empty `target`.
/// On failure only what this call moved is removed, then the reservation
/// itself, which `remove_dir` drops only while it is empty.
fn move_into_reserved(partial: &Path, target: &Path) -> std::io::Result<()> {
    let mut moved = Vec::new();
    let result = (|| {
        for entry in fs::read_dir(partial)? {
            let name = entry?.file_name();
            fs::rename(partial.join(&name), target.join(&name))?;
            moved.push(name);
        }
        fs::remove_dir(partial)
    })();
    if result.is_err() {
        for name in &moved {
            let path = target.join(name);
            let _ = if path.is_dir() { fs::remove_dir_all(&path) } else { fs::remove_file(&path) };
        }
        let _ = fs::remove_dir(target);
    }
    result
}

/// Save reviewed texts into a new sibling folder "<name> (sửa N)" and return
/// it; the caller re-runs the job there. An empty map re-runs the machine text.
#[tauri::command]
fn save_review(output_path: String, overrides: std::collections::BTreeMap<String, String>) -> Result<String, String> {
    let dir = job_review_dir(&output_path)?;
    check_review_texts(&overrides)?;
    Ok(display_path(&create_revision(&dir, &overrides)?))
}

fn check_review_texts(texts: &std::collections::BTreeMap<String, String>) -> Result<(), String> {
    if texts.len() > 10_000 || texts.iter().any(|(id, text)| id.is_empty() || id.len() > 128
        || text.trim().is_empty() || text.chars().count() > 2000) {
        return Err(fail("Câu sửa không hợp lệ: không được để trống hoặc dài quá 2000 ký tự"));
    }
    Ok(())
}

#[derive(Debug, Serialize)]
struct FetchStarted {
    status: source::FetchStatus,
    output_dir: String,
}

#[tauri::command]
fn fetch_url(url: String, title: Option<String>, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Result<FetchStarted, String> {
    let url = url.trim().to_owned();
    let root = version_root()?;
    let supervisor = supervisor_binary(&root)?;
    let status = sources.fetch(url.clone(), title, &supervisor, &root, &control_root()?)?;
    storage::note_fetch(&status.scan_id);
    let output_dir = source::link_output_dir(&url, status.title.as_deref(), &status.scan_id)?;
    // Exists from the start, so "Mở thư mục" works while the link downloads.
    fs::create_dir_all(&output_dir).map_err(|error| fail(format!("Không tạo được thư mục kết quả: {error}")))?;
    Ok(FetchStarted { output_dir: output_dir.to_string_lossy().into_owned(), status })
}

#[tauri::command]
fn fetch_status(scan_id: String, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Option<source::FetchStatus> {
    sources.status(&scan_id)
}

/// Drop a link that is still waiting for its turn.
#[tauri::command]
fn withdraw_fetch(scan_id: String, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> bool {
    sources.withdraw(&scan_id)
}

/// Cancel a link that is waiting, being prepared or downloading.
#[tauri::command]
fn cancel_fetch(scan_id: String, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> bool {
    sources.cancel(&scan_id)
}

/// A file the link service saved below `<data root>\source-media`.
fn downloaded_media(path: &str) -> Result<PathBuf, String> {
    let file = PathBuf::from(path).canonicalize().map_err(|_| fail("Không tìm thấy video đã tải"))?;
    let media = data_root()?.join("source-media").canonicalize().map_err(|_| fail("Không tìm thấy video đã tải"))?;
    if !file.starts_with(&media) || !file.is_file() {
        return Err(fail("Chỉ dùng được video DubFlow đã tải về"));
    }
    Ok(file)
}

fn default_download_folder() -> Result<PathBuf, String> {
    let profile = std::env::var_os("USERPROFILE").ok_or_else(|| fail("USERPROFILE is unavailable"))?;
    Ok(PathBuf::from(profile).join("Videos").join("DubFlow Downloads"))
}

/// Where downloaded videos are kept when the user has not chosen a folder.
#[tauri::command]
fn default_download_dir() -> Result<String, String> {
    Ok(default_download_folder()?.to_string_lossy().into_owned())
}

/// Copy `file` into `folder` as `<name>.<ext>`, or `<name> (2).<ext>`… when
/// taken. `create_new` reserves the name, so nothing is ever overwritten.
fn copy_to_unique(file: &Path, folder: &Path, name: &str) -> Result<PathBuf, String> {
    fs::create_dir_all(folder).map_err(|error| fail(format!("Không tạo được thư mục lưu video: {error}")))?;
    let extension = file.extension().and_then(|ext| ext.to_str()).unwrap_or_default();
    for number in 1..=999 {
        let target = folder.join(source::numbered_file_name(name, extension, number));
        match fs::OpenOptions::new().write(true).create_new(true).open(&target) {
            Ok(mut out) => {
                let copied = fs::File::open(file).and_then(|mut input| std::io::copy(&mut input, &mut out)).and_then(|_| out.sync_all());
                if let Err(error) = copied {
                    drop(out);
                    let _ = fs::remove_file(&target);
                    return Err(fail(format!("Không chép được video vào thư mục lưu: {error}")));
                }
                return Ok(target);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(fail(format!("Không chép được video vào thư mục lưu: {error}"))),
        }
    }
    Err(fail("Thư mục lưu đã có quá nhiều video trùng tên"))
}

/// Copy a finished download into the user's download folder (the default
/// when `folder` is empty) under its title; the source store keeps its own
/// file. Returns the new path.
#[tauri::command]
async fn keep_download(media_path: String, title: String, folder: Option<String>) -> Result<String, String> {
    let file = downloaded_media(&media_path)?;
    let folder = match folder.map(|folder| folder.trim().to_owned()).filter(|folder| !folder.is_empty()) {
        Some(folder) => PathBuf::from(folder),
        None => default_download_folder()?,
    };
    if !folder.is_absolute() {
        return Err(fail("Thư mục lưu video tải về không hợp lệ; hãy chọn lại"));
    }
    let name = source::safe_file_name(&title);
    let target = tauri::async_runtime::spawn_blocking(move || copy_to_unique(&file, &folder, &name))
        .await.map_err(|error| fail(error.to_string()))??;
    Ok(display_path(&target))
}

/// Open a results folder; only folders below "DubFlow Output".
#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    let dir = PathBuf::from(&path);
    let inside_output = dir.is_absolute() && dir.ancestors().skip(1).any(|d| d.file_name().is_some_and(|name| name == "DubFlow Output"));
    if !inside_output || !dir.is_dir() || path.contains("..") {
        return Err(fail("Chỉ mở được thư mục kết quả của DubFlow"));
    }
    Command::new("explorer.exe").arg(display_path(&dir)).spawn()
        .map_err(|error| fail(format!("Không mở được thư mục: {error}")))?;
    Ok(())
}

/// Show a downloaded source video in Explorer: a file the link service saved
/// below `<data root>\source-media`, or its copy in the download folder (any
/// local video; Explorer only selects it).
#[tauri::command]
fn reveal_download(path: String) -> Result<(), String> {
    let file = downloaded_media(&path).or_else(|_| playable_media(&path))
        .map_err(|_| fail("Chỉ mở được video DubFlow đã tải về"))?;
    Command::new("explorer.exe").arg(format!("/select,{}", display_path(&file))).spawn()
        .map_err(|error| fail(format!("Không mở được thư mục: {error}")))?;
    Ok(())
}

/// A short WAV clip of the source or the finished video for one review cue.
/// Async: FFmpeg runs off the UI thread. Raw bytes avoid a JSON number array.
#[tauri::command]
async fn review_clip(output_path: String, start_ms: u64, end_ms: u64, source: bool) -> Result<tauri::ipc::Response, String> {
    if end_ms <= start_ms || end_ms - start_ms > 30_000 {
        return Err(fail("Đoạn nghe thử phải dài 0–30 giây"));
    }
    let dir = review_dir(&output_path)?;
    let media = if source {
        let manifest = read_json(&dir.join("job_manifest.json"), 16 * 1024 * 1024)?;
        let path = manifest["source"]["path"].as_str().ok_or_else(|| fail("Không tìm thấy video gốc"))?;
        absolute_file(path, "source")?
    } else {
        output_file(&output_path)?
    };
    let ffmpeg = version_root()?.join("runtime").join("media").join("ffmpeg.exe");
    let mut command = Command::new(&ffmpeg);
    command.args(["-nostdin", "-v", "error", "-ss", &format!("{:.3}", start_ms as f64 / 1000.0), "-t",
            &format!("{:.3}", (end_ms - start_ms) as f64 / 1000.0 + 0.3), "-i"])
        .arg(&media)
        .args(["-vn", "-ac", "1", "-ar", "24000", "-f", "wav", "pipe:1"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
    if !output.status.success() || output.stdout.len() < 64 {
        return Err(fail("Không cắt được đoạn audio"));
    }
    Ok(tauri::ipc::Response::new(output.stdout))
}

/// Trends, keyword suggestions and Bilibili lists from the bundled Python
/// discovery adapters (`python -m engine.dubflow.discovery`). Async: network
/// calls run off the UI thread. Always returns the adapter's JSON object.
#[tauri::command]
async fn discover(platform: String, action: String, query: Option<String>, page: Option<u32>, mid: Option<String>) -> Result<Value, String> {
    discovery::Platform::parse(&platform)?;
    if !["trends", "suggest", "popular", "search", "creator"].contains(&action.as_str()) {
        return Err(fail("Thao tác khám phá không hợp lệ"));
    }
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.discovery", &platform, &action]);
    if let Some(query) = query.filter(|value| !value.trim().is_empty()) {
        if query.chars().count() > 100 || query.chars().any(char::is_control) {
            return Err(fail("Từ khóa quá dài"));
        }
        command.args(["--query", query.trim()]);
    }
    if let Some(page) = page {
        command.args(["--page", &page.clamp(1, 50).to_string()]);
    }
    if let Some(mid) = mid {
        if mid.is_empty() || mid.len() > 20 || !mid.bytes().all(|b| b.is_ascii_digit()) {
            return Err(fail("Mã kênh Bilibili không hợp lệ"));
        }
        command.args(["--mid", &mid]);
    }
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output())
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("Không chạy được bộ khám phá: {error}")))?;
    let line = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str::<Value>(line.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ khám phá không trả về dữ liệu"))
}

/// Vietnamese glosses for short Chinese terms (trend keywords), in order.
/// `engine` "hachimi" only after the user accepted its licence warning.
#[tauri::command]
async fn translate_terms(terms: Vec<String>, engine: Option<String>) -> Result<Value, String> {
    if terms.is_empty() || terms.len() > 60 || terms.iter().any(|term| term.trim().is_empty() || term.chars().count() > 80 || term.chars().any(char::is_control)) {
        return Err(fail("Danh sách từ khóa không hợp lệ"));
    }
    let engine = match engine.as_deref() {
        None | Some("argos") => "argos",
        Some("hachimi") => "hachimi",
        Some(_) => return Err(fail("Bộ dịch không được hỗ trợ")),
    };
    let root = version_root()?;
    let models = control_root()?.join("models");
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.translation.terms", "--engine", engine, "--model-root"])
        .arg(&models)
        .arg("--")
        .args(terms.iter().map(|term| term.trim()));
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output())
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("Không chạy được bộ dịch: {error}")))?;
    let line = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str::<Value>(line.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ dịch không trả về dữ liệu"))
}

/// Open (or navigate) the discovery page at a search or creator page, shown
/// over Khám phá's browser panel at `x, y, width, height` (CSS pixels of the
/// main window). There is one discovery page at a time. It shares the app's
/// browser profile, so a saved Douyin visit helps here.
#[tauri::command]
async fn open_discovery(app: tauri::AppHandle, platform: String, kind: String, query: String, x: f64, y: f64, width: f64, height: f64) -> Result<String, String> {
    let platform = discovery::Platform::parse(&platform)?;
    let url = discovery::page_url(platform, &kind, &query)?;
    let bounds = ViewBounds::new(x, y, width, height)?;
    open_embedded(&app, DISCOVERY_EMBED, url.clone(), true, bounds)?;
    Ok(url.to_string())
}

fn discovery_page(app: &tauri::AppHandle) -> Result<EmbeddedView, String> {
    EmbeddedView::find(app, DISCOVERY_EMBED).ok_or_else(|| fail("Hãy mở trang khám phá trước"))
}

/// Show the open discovery page again over the panel (back on Khám phá, or
/// after a dialog that it would have covered closed).
#[tauri::command]
async fn discovery_view_show(app: tauri::AppHandle, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    let bounds = ViewBounds::new(x, y, width, height)?;
    let view = discovery_page(&app)?;
    view.place(&app, bounds)?;
    view.show(false).map_err(|error| fail(error.to_string()))
}

/// Keep the discovery page over the panel when the window changes.
#[tauri::command]
async fn discovery_view_bounds(app: tauri::AppHandle, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    let bounds = ViewBounds::new(x, y, width, height)?;
    match EmbeddedView::find(&app, DISCOVERY_EMBED) {
        Some(view) => view.place(&app, bounds),
        None => Ok(()),
    }
}

/// Hide the discovery page but keep it loaded (leaving Khám phá, showing the results).
#[tauri::command]
async fn discovery_view_hide(app: tauri::AppHandle) -> Result<(), String> {
    match EmbeddedView::find(&app, DISCOVERY_EMBED) {
        Some(view) => view.hide().map_err(|error| fail(error.to_string())),
        None => Ok(()),
    }
}

#[tauri::command]
async fn discovery_view_close(app: tauri::AppHandle) -> Result<(), String> {
    match EmbeddedView::find(&app, DISCOVERY_EMBED) {
        Some(view) => view.close().map_err(|error| fail(error.to_string())),
        None => Ok(()),
    }
}

#[tauri::command]
async fn discovery_view_back(app: tauri::AppHandle) -> Result<(), String> {
    discovery_page(&app)?.webview().eval(discovery::BACK_SCRIPT).map_err(|error| fail(error.to_string()))
}

#[tauri::command]
async fn discovery_view_reload(app: tauri::AppHandle) -> Result<(), String> {
    discovery_page(&app)?.webview().reload().map_err(|error| fail(error.to_string()))
}

/// The address the discovery page shows now, or None when no page is open.
#[tauri::command]
async fn discovery_view_url(app: tauri::AppHandle) -> Result<Option<String>, String> {
    match EmbeddedView::find(&app, DISCOVERY_EMBED) {
        Some(view) => view.webview().url().map(|url| Some(url.to_string())).map_err(|error| fail(error.to_string())),
        None => Ok(None),
    }
}

/// Scroll the discovery page so it loads more results.
#[tauri::command]
async fn scroll_discovery(app: tauri::AppHandle) -> Result<(), String> {
    discovery_page(&app)?.webview().eval(discovery::SCROLL_SCRIPT).map_err(|error| fail(error.to_string()))
}

/// Read the video cards the discovery page currently shows.
#[tauri::command]
async fn collect_discovery(app: tauri::AppHandle, platform: String) -> Result<Vec<discovery::DiscoveredVideo>, String> {
    let platform = discovery::Platform::parse(&platform)?;
    let view = discovery_page(&app)?;
    let (sender, receiver) = std::sync::mpsc::channel::<String>();
    view.webview().eval_with_callback(discovery::COLLECT_SCRIPT, move |result| { let _ = sender.send(result); })
        .map_err(|error| fail(error.to_string()))?;
    let result = tauri::async_runtime::spawn_blocking(move || receiver.recv_timeout(std::time::Duration::from_secs(10)))
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|_| fail("Trang không phản hồi; hãy đợi trang tải xong rồi thử lại"))?;
    discovery::parse_collection(platform, &result).map_err(fail)
}

/// One JPEG frame of a local video at `position` (0–1 of its duration), for
/// drawing blur regions on and for the editor's timeline thumbnails. Async:
/// FFprobe/FFmpeg run off the UI thread. Scaled to at most `max_width` px
/// wide (64–960, default 960).
#[tauri::command]
async fn video_frame(source_path: String, position: f64, max_width: Option<u32>) -> Result<tauri::ipc::Response, String> {
    if !(0.0..=1.0).contains(&position) {
        return Err(fail("Vị trí khung hình phải trong khoảng 0–1"));
    }
    let source = absolute_file(&source_path, "source_path")?;
    let media = version_root()?.join("runtime").join("media");
    let mut probe = Command::new(media.join("ffprobe.exe"));
    probe.args(["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1"])
        .arg(&source)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    probe.creation_flags(CREATE_NO_WINDOW);
    let duration = probe.output().ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|text| text.trim().parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(0.0);
    // Stay a little before the end, where a seek can land past the last frame.
    let seconds = (duration * position).min((duration - 0.5).max(0.0));
    let scale = format!("scale='min({},iw)':-2", max_width.unwrap_or(960).clamp(64, 960));
    let mut command = Command::new(media.join("ffmpeg.exe"));
    command.args(["-nostdin", "-v", "error", "-ss", &format!("{seconds:.3}"), "-i"])
        .arg(&source)
        .args(["-frames:v", "1", "-vf", &scale, "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
    if !output.status.success() || output.stdout.len() < 128 {
        return Err(fail("Không lấy được khung hình ở thời điểm này"));
    }
    Ok(tauri::ipc::Response::new(output.stdout))
}

/// Video files the in-app player may stream through the asset protocol.
const PLAYABLE_EXTENSIONS: [&str; 8] = ["mp4", "mkv", "mov", "webm", "avi", "m4v", "ts", "flv"];

fn has_playable_extension(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str())
        .is_some_and(|extension| PLAYABLE_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str()))
}

/// Pictures the editor lays over the video as a logo or still (Issue #520).
pub(crate) const STILL_EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "webp", "bmp"];

pub(crate) fn is_still(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str())
        .is_some_and(|extension| STILL_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str()))
}

/// A local video, or a picture (`is_still`), the editor may lay over the output.
pub(crate) fn overlay_media(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !is_still(path) {
        return playable_media(value);
    }
    if !path.is_absolute() {
        return Err(fail("Chỉ dùng được ảnh PNG, JPG, WEBP hoặc BMP trên máy"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy ảnh"))?;
    if !resolved.is_file() || !is_still(&resolved) {
        return Err(fail("Không tìm thấy ảnh"));
    }
    Ok(resolved)
}

/// Videos and pictures for the editor's Chèn (pictures go over the video).
#[tauri::command]
fn pick_edit_files() -> Result<Vec<String>, String> {
    let mut all: Vec<&str> = VIDEO_EXTENSIONS.to_vec();
    all.extend(STILL_EXTENSIONS);
    let files = rfd::FileDialog::new()
        .add_filter("Video và ảnh", &all)
        .add_filter("Video", &VIDEO_EXTENSIONS)
        .add_filter("Ảnh", &STILL_EXTENSIONS)
        .pick_files()
        .unwrap_or_default();
    Ok(files.into_iter().map(|path| path.to_string_lossy().into_owned()).collect())
}

/// LUT màu (Issue #531): one .cube file, or none when the user cancels.
#[tauri::command]
fn pick_lut() -> Result<Option<String>, String> {
    Ok(rfd::FileDialog::new().add_filter("LUT màu (.cube)", &["cube", "CUBE"]).pick_file()
        .map(|path| path.to_string_lossy().into_owned()))
}

/// The canonical path of an existing local video, or why the player may not open it.
fn playable_media(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !path.is_absolute() || !has_playable_extension(path) {
        return Err(fail("Chỉ xem được file video MP4, MKV, MOV, WEBM, AVI, M4V, TS hoặc FLV trên máy"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy file video"))?;
    // A link may point elsewhere; the file actually served must be a video too.
    if !resolved.is_file() || !has_playable_extension(&resolved) {
        return Err(fail("Không tìm thấy file video"));
    }
    Ok(resolved)
}

/// The dubbed mix of a finished job (`<output>\editable\*.wav`), which the
/// editor's Phụ đề preview plays beside the muted source video. No other
/// audio file can be streamed.
fn editable_audio(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    let is_wav = |path: &Path| path.extension().and_then(|extension| extension.to_str()).is_some_and(|extension| extension.eq_ignore_ascii_case("wav"));
    if !path.is_absolute() || !is_wav(path) {
        return Err(fail("Chỉ phát được âm thanh lồng tiếng của DubFlow"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy âm thanh lồng tiếng"))?;
    let in_editable = resolved.parent().and_then(Path::file_name).is_some_and(|name| name == "editable");
    let in_output = resolved.ancestors().any(|dir| dir.file_name().is_some_and(|name| name == "DubFlow Output"));
    if !resolved.is_file() || !is_wav(&resolved) || !in_editable || !in_output {
        return Err(fail("Chỉ phát được âm thanh lồng tiếng của DubFlow"));
    }
    Ok(resolved)
}

/// Let the main window's player stream one local video (or a job's dubbed
/// mix, see `editable_audio`, or a sound the editor's Âm thanh may play, see
/// `sfx::playable_audio`); returns the path to load.
#[tauri::command]
fn allow_media(app: tauri::AppHandle, path: String) -> Result<String, String> {
    use tauri::Manager;
    let extension = Path::new(&path).extension().and_then(|extension| extension.to_str()).map(str::to_ascii_lowercase).unwrap_or_default();
    let media = if extension == "wav" {
        editable_audio(&path).or_else(|error| docu::narration_audio(&path).or_else(|_| sfx::playable_audio(&path)).map_err(|_| error))?
    } else if sfx::AUDIO_EXTENSIONS.contains(&extension.as_str()) {
        sfx::playable_audio(&path)?
    } else {
        overlay_media(&path)?
    };
    app.asset_protocol_scope().allow_file(&media).map_err(|error| fail(error.to_string()))?;
    let text = media.to_string_lossy();
    Ok(match text.strip_prefix(r"\\?\UNC\") {
        Some(share) => format!(r"\\{share}"),
        None => display_path(&media),
    })
}

const MAIN_WINDOW: &str = "main";
/// The douyin.com page shown inside the main window's "Đăng nhập Douyin" dialog.
const DOUYIN_LOGIN: &str = "douyin-login";
/// Fallback when a child webview cannot be created: a frameless window owned
/// by the main window, kept over the dialog's box.
const DOUYIN_LOGIN_WINDOW: &str = "douyin-login-window";
/// Hidden window that visits douyin.com to refresh the session.
const DOUYIN_SESSION: &str = "douyin-session";
const DOUYIN_HOME: &str = "https://www.douyin.com/";
const DOUYIN_STATE_FILE: &str = "douyin-session.json";

fn douyin_home() -> Result<tauri::Url, String> {
    DOUYIN_HOME.parse::<tauri::Url>().map_err(|error| fail(error.to_string()))
}

fn unix_now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// When this app last saved a Douyin session (no cookie values on disk here;
/// those live only in the service's protected store).
fn douyin_state_path() -> Result<PathBuf, String> {
    Ok(control_root()?.join("control").join(DOUYIN_STATE_FILE))
}

fn douyin_saved_at() -> Option<u64> {
    let text = fs::read_to_string(douyin_state_path().ok()?).ok()?;
    serde_json::from_str::<Value>(&text).ok()?["saved_at_unix"].as_u64()
}

/// Where an embedded page goes, in CSS (= logical) pixels of the main window.
#[derive(Clone, Copy)]
struct ViewBounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl ViewBounds {
    fn new(x: f64, y: f64, width: f64, height: f64) -> Result<Self, String> {
        if ![x, y, width, height].iter().all(|value| value.is_finite() && value.abs() < 100_000.0) {
            return Err(fail("Vị trí khung trang không hợp lệ"));
        }
        Ok(Self { x: x.max(0.0), y: y.max(0.0), width: width.max(1.0), height: height.max(1.0) })
    }
}

/// A web page drawn over a box of the main window: a child webview labelled
/// `child`, or, when child webviews cannot be created, a frameless window
/// labelled `window` owned by the main window and kept over the box.
#[derive(Clone, Copy)]
struct Embed {
    child: &'static str,
    window: &'static str,
    title: &'static str,
    /// Runs before any page script on every page load.
    script: Option<&'static str>,
    /// Links that would open a new window load in the page itself instead.
    keep_popups: bool,
}

/// douyin.com inside the "Đăng nhập Douyin" dialog.
const DOUYIN_EMBED: Embed = Embed { child: DOUYIN_LOGIN, window: DOUYIN_LOGIN_WINDOW, title: "Đăng nhập Douyin", script: None, keep_popups: false };
/// The search or creator page inside Khám phá's browser panel.
const DISCOVERY_EMBED: Embed = Embed {
    child: discovery::VIEW, window: discovery::FALLBACK_WINDOW, title: "Khám phá video",
    script: Some(discovery::CAPTURE_SCRIPT), keep_popups: true,
};

enum EmbeddedView {
    Child(tauri::Webview),
    Window(tauri::WebviewWindow),
}

impl EmbeddedView {
    fn find(app: &tauri::AppHandle, embed: Embed) -> Option<Self> {
        use tauri::Manager;
        app.get_webview(embed.child).map(Self::Child)
            .or_else(|| app.get_webview_window(embed.window).map(Self::Window))
    }

    /// The page itself, for navigation, scripts and cookies.
    fn webview(&self) -> &tauri::Webview {
        match self {
            Self::Child(webview) => webview,
            Self::Window(window) => window.as_ref(),
        }
    }

    fn place(&self, app: &tauri::AppHandle, bounds: ViewBounds) -> Result<(), String> {
        use tauri::Manager;
        let error = |error: tauri::Error| fail(error.to_string());
        match self {
            Self::Child(webview) => {
                webview.set_position(tauri::LogicalPosition::new(bounds.x, bounds.y)).map_err(error)?;
                webview.set_size(tauri::LogicalSize::new(bounds.width, bounds.height)).map_err(error)
            }
            Self::Window(window) => {
                let main = app.get_webview_window(MAIN_WINDOW).ok_or_else(|| fail("Không thấy cửa sổ DubFlow"))?;
                let origin = main.inner_position().map_err(error)?;
                let scale = main.scale_factor().map_err(error)?;
                window.set_position(tauri::PhysicalPosition::new(origin.x + (bounds.x * scale).round() as i32, origin.y + (bounds.y * scale).round() as i32))
                    .map_err(error)?;
                window.set_size(tauri::LogicalSize::new(bounds.width, bounds.height)).map_err(error)
            }
        }
    }

    fn show(&self, focus: bool) -> tauri::Result<()> {
        match self {
            Self::Child(webview) => webview.show().and_then(|_| if focus { webview.set_focus() } else { Ok(()) }),
            Self::Window(window) => window.show().and_then(|_| if focus { window.set_focus() } else { Ok(()) }),
        }
    }

    fn hide(&self) -> tauri::Result<()> {
        match self {
            Self::Child(webview) => webview.hide(),
            Self::Window(window) => window.hide(),
        }
    }

    fn close(&self) -> tauri::Result<()> {
        match self {
            Self::Child(webview) => webview.close(),
            Self::Window(window) => window.close(),
        }
    }
}

/// A new-window request from `embed`'s page: web links load in the page
/// itself, anything else is refused. Never a separate window.
fn keep_popup(app: &tauri::AppHandle, embed: Embed) -> impl Fn(tauri::Url, tauri::webview::NewWindowFeatures) -> tauri::webview::NewWindowResponse<tauri::Wry> + Send + 'static {
    let app = app.clone();
    move |url, _features| {
        if discovery::loads_in_place(&url) {
            let app = app.clone();
            // Not from inside the webview's own callback.
            tauri::async_runtime::spawn(async move {
                if let Some(view) = EmbeddedView::find(&app, embed) {
                    let _ = view.webview().navigate(url);
                }
            });
        }
        tauri::webview::NewWindowResponse::Deny
    }
}

/// Show `embed` at `bounds`: move and show it when it exists (navigating it to
/// `url` when `navigate`), else create it at `url`. Async callers only:
/// creating a webview deadlocks in a sync command on Windows.
fn open_embedded(app: &tauri::AppHandle, embed: Embed, url: tauri::Url, navigate: bool, bounds: ViewBounds) -> Result<(), String> {
    use tauri::Manager;
    if let Some(view) = EmbeddedView::find(app, embed) {
        if navigate {
            view.webview().navigate(url).map_err(|error| fail(error.to_string()))?;
        }
        view.place(app, bounds)?;
        let _ = view.show(true);
        return Ok(());
    }
    let main = app.get_window(MAIN_WINDOW).ok_or_else(|| fail("Không thấy cửa sổ DubFlow"))?;
    let mut builder = tauri::webview::WebviewBuilder::new(embed.child, tauri::WebviewUrl::External(url.clone()));
    if let Some(script) = embed.script {
        builder = builder.initialization_script(script);
    }
    if embed.keep_popups {
        builder = builder.on_new_window(keep_popup(app, embed));
    }
    let child = main.add_child(builder, tauri::LogicalPosition::new(bounds.x, bounds.y), tauri::LogicalSize::new(bounds.width, bounds.height));
    if let Ok(child) = child {
        let _ = child.set_focus();
        return Ok(());
    }
    // No child webviews here: a frameless window owned by DubFlow stands in.
    let owner = app.get_webview_window(MAIN_WINDOW).ok_or_else(|| fail("Không thấy cửa sổ DubFlow"))?;
    let mut builder = tauri::WebviewWindowBuilder::new(app, embed.window, tauri::WebviewUrl::External(url))
        .title(embed.title)
        .decorations(false)
        .resizable(false)
        .skip_taskbar(true)
        .inner_size(bounds.width, bounds.height)
        .parent(&owner).map_err(|error| fail(error.to_string()))?;
    if let Some(script) = embed.script {
        builder = builder.initialization_script(script);
    }
    if embed.keep_popups {
        builder = builder.on_new_window(keep_popup(app, embed));
    }
    let window = builder.build().map_err(|error| fail(format!("Không mở được trang: {error}")))?;
    EmbeddedView::Window(window).place(app, bounds)
}

/// Show douyin.com inside the "Đăng nhập Douyin" dialog at `x, y, width,
/// height` (CSS pixels of the main window).
#[tauri::command]
async fn douyin_view_open(app: tauri::AppHandle, sources: tauri::State<'_, std::sync::Arc<source::Sources>>, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    let bounds = ViewBounds::new(x, y, width, height)?;
    open_embedded(&app, DOUYIN_EMBED, douyin_home()?, false, bounds)?;
    // The link service's first start checks the whole install (a minute or
    // more on a cold disk); start it while the user logs in so "Xong" saves
    // at once. A failure here shows up again, with its message, on "Xong".
    if let (Ok(root), Ok(data)) = (version_root(), control_root()) {
        if let Ok(supervisor) = supervisor_binary(&root) {
            let sources = std::sync::Arc::clone(&sources);
            tauri::async_runtime::spawn_blocking(move || { let _ = sources.warm_up(&supervisor, &root, &data); });
        }
    }
    Ok(())
}

/// Keep the login page over the dialog's box when the window changes.
#[tauri::command]
async fn douyin_view_bounds(app: tauri::AppHandle, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    let bounds = ViewBounds::new(x, y, width, height)?;
    match EmbeddedView::find(&app, DOUYIN_EMBED) {
        Some(view) => view.place(&app, bounds),
        None => Ok(()),
    }
}

#[tauri::command]
async fn douyin_view_close(app: tauri::AppHandle) -> Result<(), String> {
    match EmbeddedView::find(&app, DOUYIN_EMBED) {
        Some(view) => view.close().map_err(|error| fail(error.to_string())),
        None => Ok(()),
    }
}

/// douyin.com cookies, read through the login page when it is open, else the
/// main window (all webviews share one browser profile).
fn douyin_cookies(app: &tauri::AppHandle) -> Result<Vec<(String, String)>, String> {
    use tauri::Manager;
    let url = douyin_home()?;
    let cookies = match EmbeddedView::find(app, DOUYIN_EMBED) {
        Some(view) => view.webview().cookies_for_url(url),
        None => app.get_webview_window(MAIN_WINDOW).ok_or_else(|| fail("Không thấy cửa sổ DubFlow"))?.cookies_for_url(url),
    };
    Ok(cookies.map_err(|error| fail(format!("Không đọc được cookie Douyin: {error}")))?
        .into_iter().map(|cookie| (cookie.name().to_owned(), cookie.value().to_owned())).collect())
}

/// Douyin has given this browser profile a visitor identity.
fn has_douyin_visit(cookies: &[(String, String)]) -> bool {
    cookies.iter().any(|(name, value)| (name == "s_v_web_id" || name == "ttwid") && !value.is_empty())
}

/// Store the cookies as the protected Douyin session used by link downloads
/// and remember when. Returns how many cookies were kept.
async fn store_douyin_session(sources: std::sync::Arc<source::Sources>, cookies: Vec<(String, String)>) -> Result<usize, String> {
    if !has_douyin_visit(&cookies) {
        return Err(fail("Trang Douyin chưa tải xong; hãy đợi trang hiện video rồi bấm lại"));
    }
    let header = source::cookie_header(&cookies, &source::DOUYIN_PRIORITY).ok_or_else(|| fail("Không có cookie Douyin hợp lệ"))?;
    let count = header.split("; ").count();
    let now = unix_now();
    let root = version_root()?;
    let supervisor = supervisor_binary(&root)?;
    let data = control_root()?;
    tauri::async_runtime::spawn_blocking(move || sources.save_session("douyin", header, now + source::SESSION_SECONDS, &supervisor, &root, &data))
        .await.map_err(|error| fail(error.to_string()))??;
    let _ = fs::write(douyin_state_path()?, json!({"schema_version": 1, "saved_at_unix": now}).to_string());
    Ok(count)
}

/// "Xong" in the login dialog: save the session from the login page and close it.
#[tauri::command]
async fn save_douyin_session(app: tauri::AppHandle, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Result<usize, String> {
    let cookies = douyin_cookies(&app)?;
    let count = store_douyin_session(std::sync::Arc::clone(&sources), cookies).await?;
    let _ = douyin_view_close(app).await;
    Ok(count)
}

fn douyin_ensure_lock() -> &'static tauri::async_runtime::Mutex<()> {
    static LOCK: std::sync::OnceLock<tauri::async_runtime::Mutex<()>> = std::sync::OnceLock::new();
    LOCK.get_or_init(|| tauri::async_runtime::Mutex::new(()))
}

/// Make sure link downloads have a fresh Douyin session: one saved by this app
/// less than 20 hours ago is kept unless `force`; otherwise a hidden window
/// visits douyin.com until it has a visitor cookie (at most 25 s) and the
/// session is saved from it.
#[tauri::command]
async fn ensure_douyin_session(app: tauri::AppHandle, sources: tauri::State<'_, std::sync::Arc<source::Sources>>, force: bool) -> Result<String, String> {
    use tauri::Manager;
    let _one_at_a_time = douyin_ensure_lock().lock().await;
    if !force && douyin_saved_at().is_some_and(|saved| source::session_fresh(saved, unix_now())) {
        return Ok("ready".into());
    }
    if let Some(stale) = app.get_webview_window(DOUYIN_SESSION) {
        let _ = stale.close();
    }
    let url = douyin_home()?;
    let window = tauri::WebviewWindowBuilder::new(&app, DOUYIN_SESSION, tauri::WebviewUrl::External(url.clone()))
        .title("Douyin")
        .visible(false)
        .skip_taskbar(true)
        .build()
        .map_err(|error| fail(format!("Không mở được Douyin: {error}")))?;
    let mut cookies = Vec::new();
    for _ in 0..50 {
        tauri::async_runtime::spawn_blocking(|| std::thread::sleep(std::time::Duration::from_millis(500)))
            .await.map_err(|error| fail(error.to_string()))?;
        cookies = window.cookies_for_url(url.clone()).unwrap_or_default().into_iter()
            .map(|cookie| (cookie.name().to_owned(), cookie.value().to_owned())).collect();
        if has_douyin_visit(&cookies) {
            break;
        }
    }
    let _ = window.close();
    if !has_douyin_visit(&cookies) {
        return Err(fail("Douyin không phản hồi sau 25 giây; chưa lấy được phiên truy cập."));
    }
    store_douyin_session(std::sync::Arc::clone(&sources), cookies).await?;
    Ok("ready".into())
}

/// Whether the shared browser profile is logged in to Douyin.
#[tauri::command]
async fn douyin_account(app: tauri::AppHandle) -> Result<Value, String> {
    let logged_in = douyin_cookies(&app)?.iter().any(|(name, value)| name == "sessionid" && !value.is_empty());
    Ok(json!({"logged_in": logged_in}))
}

/// Log out of Douyin: delete every douyin.com cookie of the browser profile.
/// The next Douyin link saves a fresh visitor session.
#[tauri::command]
async fn douyin_logout(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let main = app.get_webview_window(MAIN_WINDOW).ok_or_else(|| fail("Không thấy cửa sổ DubFlow"))?;
    let cookies = main.cookies().map_err(|error| fail(format!("Không đọc được cookie Douyin: {error}")))?;
    for cookie in cookies.into_iter().filter(|cookie| cookie.domain().is_some_and(|domain| {
        let domain = domain.trim_start_matches('.').to_ascii_lowercase();
        domain == "douyin.com" || domain.ends_with(".douyin.com")
    })) {
        main.delete_cookie(cookie).map_err(|error| fail(format!("Không xóa được cookie Douyin: {error}")))?;
    }
    if let Ok(path) = douyin_state_path() {
        let _ = fs::remove_file(path);
    }
    Ok(())
}

fn main() {
    #[cfg(windows)]
    configure_webview_data().expect("unable to configure DubFlow desktop profile");
    let sources = std::sync::Arc::new(source::Sources::default());
    let on_exit = std::sync::Arc::clone(&sources);
    tauri::Builder::default()
        .manage(sources)
        .invoke_handler(tauri::generate_handler![reframe::reframe_render, reframe::reframe_status, reframe::reframe_cancel, reframe::reframe_info, shorts::shorts_render, shorts::shorts_status, shorts::shorts_cancel, package::package_video, package::package_info, package::package_status, package::package_cancel, source_credit::source_credit_get, source_credit::source_credit_set, ai_keys::ai_key_status, ai_keys::ai_key_get, ai_keys::ai_key_set, ai_keys::ai_key_delete, create_report, product::pick_product_files, product::product_image, product::product_render, product::product_status, product::product_cancel, product::product_spec, product::product_runs, product::product_backgrounds, product::product_background_add, product::product_background_delete, release_info, voice_catalog, pick_files, pick_edit_files, pick_lut, start_job, job_status, cancel_job, pick_folder, expand_video_paths, open_path, reveal_path, review_cues, save_review, fetch_url, fetch_status, withdraw_fetch, cancel_fetch, keep_download, default_download_dir, open_folder, reveal_download, save_douyin_session, ensure_douyin_session, douyin_account, douyin_logout, douyin_view_open, douyin_view_bounds, douyin_view_close, review_clip, video_frame, allow_media, post_suggestions, discover, open_discovery, discovery_view_show, discovery_view_bounds, discovery_view_hide, discovery_view_close, discovery_view_back, discovery_view_reload, discovery_view_url, scroll_discovery, collect_discovery, translate_terms, review_speakers, check_update, download_update, install_update, list_versions, activate_version, restart_app, compilation::compilation_render, compilation::compilation_status, compilation::compilation_cancel, compilation::compilation_description, compilation::compilation_runs, docu::docu_create, docu::docu_runs, docu::docu_open, docu::docu_check_script, docu::docu_pick_file, docu::docu_voices, docu::docu_narrate, docu::docu_narration_status, docu::docu_narration_cancel, docu::docu_plan, docu::docu_plan_status, docu::docu_plan_cancel, docu::docu_cover, docu::docu_layers,
            docu::docu_reframe, docu::docu_render_options, docu::docu_render, docu::docu_render_status, docu::docu_render_cancel, docu::docu_capcut, docu::docu_launch_capcut, docu::docu_mcp_connection, docu::docu_mcp_activity, storage::storage_report, storage::storage_clean, storage::storage_free, edit::edit_project_load, edit::edit_library, edit::edit_file_sizes, edit::edit_project_save, edit::edit_export, edit::edit_status, edit::edit_cancel, edit::edit_media_info, edit::edit_audio_peaks, edit::edit_lut_frame, edit::edit_subtitles_load, edit::edit_subtitles_apply, sfx::sfx_status, sfx::sfx_install, sfx::sfx_path, sfx::user_audio_list, sfx::audio_info, sfx::pick_audio_folder, sfx::pick_audio_files, sfx::expand_audio_paths])
        .build(tauri::generate_context!())
        .expect("error while building DubFlow desktop host")
        .run(move |_, event| {
            if let tauri::RunEvent::Exit = event {
                on_exit.shutdown();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kept_downloads_get_a_free_name_and_never_overwrite() {
        let root = std::env::temp_dir().join(format!("dubflow-keep-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let media = root.join("abc123.mp4");
        fs::write(&media, "video").unwrap();
        let folder = root.join("DubFlow Downloads");
        let first = copy_to_unique(&media, &folder, "Phim hay").unwrap();
        assert_eq!(first, folder.join("Phim hay.mp4"));
        let second = copy_to_unique(&media, &folder, "Phim hay").unwrap();
        assert_eq!(second, folder.join("Phim hay (2).mp4"));
        fs::write(&first, "mine").unwrap();
        assert_eq!(copy_to_unique(&media, &folder, "Phim hay").unwrap(), folder.join("Phim hay (3).mp4"));
        assert_eq!(fs::read_to_string(&first).unwrap(), "mine", "an existing file is never replaced");
        assert_eq!(fs::read_to_string(&second).unwrap(), "video");
        assert!(media.is_file(), "the store keeps its own file");
        assert!(copy_to_unique(&root.join("missing.mp4"), &folder, "x").is_err());
        assert!(!folder.join("x.mp4").exists(), "a failed copy leaves nothing behind");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn results_of_cached_link_videos_leave_the_source_store() {
        let root = std::env::temp_dir().join(format!("dubflow-results-base-{}", std::process::id()));
        let store = root.join("data").join("source-media");
        let cached = store.join("ab12").join("link-1").join("cd34").join("ef56.mp4");
        let local = root.join("clips").join("talk.mp4");
        fs::create_dir_all(cached.parent().unwrap()).unwrap();
        fs::create_dir_all(local.parent().unwrap()).unwrap();
        fs::write(&cached, "video").unwrap();
        fs::write(&local, "video").unwrap();
        let profile = root.join("profile");
        let videos = profile.join("Videos");
        let choose = |requested: Option<&Path>, results_root: Option<&Path>, source: &Path, profile: Option<&Path>| {
            choose_output(requested.map(|path| path.to_str().unwrap()), results_root, source, "job-1", Some(&store), profile)
        };
        let outside = |path: &Path| !within(path, &store);
        // Next to the source: kept for local videos, moved to Videos for cached downloads.
        assert_eq!(choose(None, None, &local, Some(&profile)).unwrap(), job_output_in(&root.join("clips"), &local, "job-1").unwrap());
        let moved = choose(None, None, &cached, Some(&profile)).unwrap();
        assert_eq!(moved, job_output_in(&videos, &cached, "job-1").unwrap());
        assert!(outside(&moved));
        // A requested folder that does not exist yet still counts as inside the store.
        let pinned = store.join("ab12").join("link-1").join("cd34").join("DubFlow Output").join("ef56 - vi").join("run-1");
        assert!(within(&pinned, &store));
        assert!(!within(&root.join("out").join("run-1"), &store));
        let redirected = choose(Some(&pinned), None, &cached, Some(&profile)).unwrap();
        assert_eq!(redirected, job_output_in(&videos, &cached, "job-1").unwrap());
        assert!(outside(&redirected));
        let kept = root.join("Videos").join("DubFlow Output").join("x - vi").join("run-2");
        assert_eq!(choose(Some(&kept), None, &local, Some(&profile)).unwrap(), kept);
        // A chosen results root inside the store is redirected too; one outside is used as is.
        let rooted = choose(None, Some(&store.join("ab12")), &local, Some(&profile)).unwrap();
        assert_eq!(rooted, job_output_in(&videos, &local, "job-1").unwrap());
        assert!(outside(&rooted));
        assert_eq!(choose(None, Some(&root.join("clips")), &local, Some(&profile)).unwrap(), job_output_in(&root.join("clips"), &local, "job-1").unwrap());
        // Without a profile a store path is refused, never used.
        assert!(choose(Some(&pinned), None, &cached, None).is_err());
        assert!(choose(None, None, &cached, None).is_err());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn player_accepts_only_existing_local_videos() {
        let root = std::env::temp_dir().join(format!("dubflow-playable-{}", std::process::id()));
        fs::create_dir_all(root.join("folder.mp4")).unwrap();
        fs::write(root.join("clip.MP4"), "video").unwrap();
        fs::write(root.join("notes.txt"), "text").unwrap();
        let clip = playable_media(&root.join("clip.MP4").to_string_lossy()).unwrap();
        assert_eq!(clip, fs::canonicalize(root.join("clip.MP4")).unwrap());
        assert!(playable_media(&root.join("notes.txt").to_string_lossy()).is_err());
        assert!(playable_media(&root.join("missing.mkv").to_string_lossy()).is_err());
        assert!(playable_media(&root.join("folder.mp4").to_string_lossy()).is_err());
        assert!(playable_media("clip.mp4").is_err());
        assert!(playable_media(&root.join("clip").to_string_lossy()).is_err());
        for name in ["a.mkv", "a.mov", "a.webm", "a.avi", "a.m4v", "a.ts", "a.flv"] {
            assert!(has_playable_extension(Path::new(name)), "{name}");
        }
        assert!(!has_playable_extension(Path::new("a.exe")));
        // Audio: only a finished job's editable mix.
        let editable = root.join("DubFlow Output/clip - vi/editable");
        fs::create_dir_all(&editable).unwrap();
        fs::write(editable.join("final_mix.wav"), "wav").unwrap();
        fs::write(root.join("DubFlow Output/clip - vi/final_mix.wav"), "wav").unwrap();
        fs::write(root.join("song.wav"), "wav").unwrap();
        assert_eq!(editable_audio(&editable.join("final_mix.wav").to_string_lossy()).unwrap(),
            fs::canonicalize(editable.join("final_mix.wav")).unwrap());
        assert!(editable_audio(&root.join("DubFlow Output/clip - vi/final_mix.wav").to_string_lossy()).is_err(), "not in editable");
        assert!(editable_audio(&root.join("song.wav").to_string_lossy()).is_err(), "not a DubFlow output");
        assert!(editable_audio(&editable.join("missing.wav").to_string_lossy()).is_err());
        assert!(editable_audio(&root.join("clip.MP4").to_string_lossy()).is_err());
        assert!(editable_audio("editable/final_mix.wav").is_err());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn review_marks_all_failed_phrase_members_across_voices_and_legacy_documents() {
        let root = std::env::temp_dir().join(format!("dubflow-phrase-review-{}", std::process::id()));
        let output = root.join("DubFlow Output/clip - vi");
        fs::create_dir_all(output.join(".dubflow-work")).unwrap();
        fs::write(output.join("final_vi.mp4"), "video").unwrap();
        let output = PathBuf::from(display_path(&fs::canonicalize(&output).unwrap()));
        let work = output.join(".dubflow-work");
        let primary = work.join("tts_document.json");
        let second = work.join("tts_document.second.json");
        let mix = work.join("mix_document.json");
        let checkpoint = json!({"stages": {"b2-audio": {"tts_document": primary,
            "tts_documents": [primary, second, root.join("outside.json")], "mix_document": mix}}});
        fs::write(work.join("checkpoint.json"), checkpoint.to_string()).unwrap();
        fs::write(&primary, json!({"kind": "dubbing_tts_document", "member_cue_ids": {
            "phrase-a": ["a", "b"], "phrase-c": ["c", "d"]},
            "tts_document": {"failures": [{"segment_id": "phrase-a"}]}}).to_string()).unwrap();
        fs::write(&second, json!({"kind": "dubbing_tts_document", "member_cue_ids": {"phrase-e": ["e", "f"]},
            "tts_document": {"failures": [{"segment_id": "phrase-e"}]}}).to_string()).unwrap();
        fs::write(&mix, json!({"failures": [{"segment_id": "phrase-c"}, {"segment_id": "phrase-a"}]}).to_string()).unwrap();
        fs::write(root.join("outside.json"), json!({"failures": [{"segment_id": "g"}]}).to_string()).unwrap();
        let source_cues = ["a", "b", "c", "d", "e", "f", "g"]
            .map(|id| json!({"cue_id": id, "start_ms": 0, "end_ms": 1000, "source_text": "源", "translated_text": "Chào"}));
        fs::write(work.join("translation.json"), json!({"cues": source_cues}).to_string()).unwrap();
        let cues = review_cues(output.join("final_vi.mp4").to_string_lossy().into_owned()).unwrap();
        assert_eq!(cues.iter().filter(|cue| cue.dub_failed).map(|cue| cue.cue_id.as_str()).collect::<Vec<_>>(),
            ["a", "b", "c", "d", "e", "f"]);
        assert_eq!(failed_dub_cues(&work).len(), 6);
        fs::write(&primary, json!({"failures": [{"segment_id": "g"}]}).to_string()).unwrap();
        fs::write(work.join("checkpoint.json"), json!({"stages": {"b2-audio": {"tts_document": primary}}}).to_string()).unwrap();
        assert_eq!(failed_dub_cues(&work), std::collections::HashSet::from(["g".to_owned()]));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn browser_profiles_preserve_runtime_identity_and_refuse_invalid_roots() {
        let local = std::env::temp_dir().join("DubFlow profile [spaces]");
        let original = webview_data_root(&local, "0.1.0-rc.film").unwrap();
        assert!(original.starts_with(local.join("control").join("webview2")));
        assert_eq!(original, webview_data_root(&local, "0.1.0-rc.film").unwrap());
        assert_ne!(original.to_string_lossy().to_lowercase(),
            webview_data_root(&local, "0.1.0-rc.Film").unwrap().to_string_lossy().to_lowercase());
        assert_ne!(original, webview_data_root(&local, "0.1.0-rc.next").unwrap());
        assert!(webview_data_root(Path::new("relative"), "0.1.0").is_err());
        for version in ["", "../escape", "a\\escape", "a:b", "a\n"] {
            assert!(webview_data_root(&local, version).is_err());
        }
        assert!(webview_data_root(&local, &"x".repeat(65)).is_err());
        assert!(webview_data_root(&local, &"x".repeat(64)).is_ok());
    }

    #[test]
    fn review_revisions_go_to_new_sibling_folders_and_leave_the_original_untouched() {
        let root = std::env::temp_dir().join(format!("dubflow-revision-{}", std::process::id())).join("DubFlow Output");
        let _ = fs::remove_dir_all(root.parent().unwrap());
        let original = root.join("clip - vi");
        let files = [("final_vi.mp4", "video"), ("captions_vi.srt", "srt"), ("job_manifest.json", "{}"),
            (".dubflow-work/translation.json", "{\"cues\": []}"), (".dubflow-work/checkpoint.json", "{}"),
            (".dubflow-work/tts-cache/a.wav", "tts"), (".dubflow-work/b2-audio/x/y.wav", "mix"),
            (".dubflow-work/export-pending/final_vi.mp4", "pending"), (".dubflow-work/edit/exports.json", "[]"),
            ("subtitle_style.json", "{\"schema_version\": 1}"), ("cover.json", "{\"schema_version\": 1, \"regions\": []}")];
        for (path, body) in files {
            fs::create_dir_all(original.join(path).parent().unwrap()).unwrap();
            fs::write(original.join(path), body).unwrap();
        }
        // Outputs are canonicalized (long names, no `\\?\`), so compare against the same spelling.
        let root = PathBuf::from(display_path(&fs::canonicalize(&root).unwrap()));
        let original = root.join("clip - vi");
        let snapshot =|dir: &Path| files.map(|(path, _)| fs::read(dir.join(path)).ok());
        let before = snapshot(&original);
        let output = original.join("final_vi.mp4").to_string_lossy().into_owned();
        let overrides = std::collections::BTreeMap::from([("c1".to_owned(), "Xin chào".to_owned())]);
        let mut records = vec![record_test_job(&original.join("final_vi.mp4"))];
        // A user's folder that happens to look like an old temporary name must survive every save.
        for stale in ["clip - vi (sửa 1).partial", "clip - vi (sửa 2).partial"] {
            fs::create_dir_all(root.join(stale)).unwrap();
            fs::write(root.join(stale).join("sentinel.txt"), "keep").unwrap();
        }
        let sentinels_intact = || ["clip - vi (sửa 1).partial", "clip - vi (sửa 2).partial"].iter()
            .all(|stale| fs::read_to_string(root.join(stale).join("sentinel.txt")).is_ok_and(|text| text == "keep"));
        let temporaries_left = || root.read_dir().unwrap().filter(|entry| {
            entry.as_ref().unwrap().file_name().to_string_lossy().contains(".revision-")
        }).count();

        assert!(save_review(output.clone(), std::collections::BTreeMap::from([("c1".to_owned(), "  ".to_owned())])).is_err());
        assert!(!root.join("clip - vi (sửa 1)").exists(), "a rejected save creates nothing");
        assert!(sentinels_intact() && temporaries_left() == 0);

        let first = PathBuf::from(save_review(output.clone(), overrides.clone()).unwrap());
        assert_eq!(first, root.join("clip - vi (sửa 1)"));
        assert_eq!(snapshot(&original), before, "the original outputs are byte-identical");
        assert!(!original.join(OVERRIDES_FILE).exists());
        assert_eq!(fs::read_to_string(first.join(".dubflow-work/tts-cache/a.wav")).unwrap(), "tts");
        assert!(first.join(".dubflow-work/translation.json").is_file() && first.join(".dubflow-work/checkpoint.json").is_file());
        assert!(!first.join(".dubflow-work/b2-audio").exists() && !first.join(".dubflow-work/export-pending").exists());
        assert!(!first.join(".dubflow-work/edit").exists(), "no draft to carry, and the old exports stay behind");
        assert_eq!(fs::read_to_string(first.join("subtitle_style.json")).unwrap(), "{\"schema_version\": 1}", "the look carries over");
        assert_eq!(fs::read_to_string(first.join("cover.json")).unwrap(), "{\"schema_version\": 1, \"regions\": []}", "so does the cover");
        assert!(!first.join("final_vi.mp4").exists(), "the revision renders its own output");
        let stored: Value = serde_json::from_slice(&fs::read(first.join(OVERRIDES_FILE)).unwrap()).unwrap();
        assert_eq!(stored, json!({"schema_version": 1, "cues": {"c1": "Xin chào"}}));
        let link: Value = serde_json::from_slice(&fs::read(first.join(REVISION_OF_FILE)).unwrap()).unwrap();
        assert_eq!(link["original_output_dir"], display_path(&original));
        assert_eq!(link["original_manifest_sha256"], "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");

        assert_eq!(PathBuf::from(save_review(output.clone(), std::collections::BTreeMap::new()).unwrap()), root.join("clip - vi (sửa 2)"));
        assert!(!root.join("clip - vi (sửa 2)").join(OVERRIDES_FILE).exists());
        fs::write(first.join("final_vi.mp4"), "revised").unwrap();
        records.push(record_test_job(&first.join("final_vi.mp4")));
        let again = save_review(first.join("final_vi.mp4").to_string_lossy().into_owned(), overrides.clone()).unwrap();
        assert_eq!(PathBuf::from(again), root.join("clip - vi (sửa 3)"), "a revision of a revision stays a sibling");
        assert!(sentinels_intact(), "pre-existing .partial folders are never removed");
        assert_eq!(temporaries_left(), 0, "each save's own temporary folder is gone");

        // A save that fails part-way removes only its own temporary folder.
        let broken = root.join("broken - vi");
        fs::create_dir_all(&broken).unwrap();
        fs::write(broken.join("final_vi.mp4"), "video").unwrap();
        assert!(create_revision(&broken, &overrides).is_err(), "no work cache to copy");
        assert!(!root.join("broken - vi (sửa 1)").exists());
        assert!(sentinels_intact() && temporaries_left() == 0);

        // Publication never replaces an existing folder, empty or not.
        fs::create_dir(root.join("clip - vi (sửa 4)")).unwrap();
        fs::create_dir(root.join("clip - vi (sửa 5)")).unwrap();
        fs::write(root.join("clip - vi (sửa 5)").join("sentinel.txt"), "keep").unwrap();
        let next = PathBuf::from(save_review(output.clone(), overrides.clone()).unwrap());
        assert_eq!(next, root.join("clip - vi (sửa 6)"));
        assert_eq!(fs::read_dir(root.join("clip - vi (sửa 4)")).unwrap().count(), 0, "an empty folder stays empty");
        assert_eq!(fs::read_to_string(root.join("clip - vi (sửa 5)").join("sentinel.txt")).unwrap(), "keep");
        let source = create_revision_temp(&original).unwrap();
        let other = create_revision_temp(&original).unwrap();
        assert_ne!(source, other, "every save gets its own temporary folder");
        fs::write(source.join("marker.txt"), "mine").unwrap();
        // A reserved target receives exactly this call's entries.
        let reserved = root.join("reserved");
        fs::create_dir(&reserved).unwrap();
        move_into_reserved(&source, &reserved).unwrap();
        assert_eq!(fs::read_to_string(reserved.join("marker.txt")).unwrap(), "mine");
        assert!(!source.exists());
        fs::remove_dir_all(&other).unwrap();

        // Concurrent saves each publish their own folder; nothing is shared or replaced.
        let workers: Vec<_> = (0..6).map(|_| {
            let (output, overrides) = (output.clone(), overrides.clone());
            std::thread::spawn(move || save_review(output, overrides).map(PathBuf::from))
        }).collect();
        let mut published: Vec<PathBuf> = workers.into_iter().map(|worker| worker.join().unwrap().unwrap()).collect();
        published.sort();
        published.dedup();
        assert_eq!(published.len(), 6, "every concurrent save got a distinct folder");
        for folder in &published {
            assert!(folder.join(OVERRIDES_FILE).is_file() && folder.join(REVISION_OF_FILE).is_file());
        }
        assert_eq!(snapshot(&original), before, "the original outputs are still byte-identical");
        assert!(sentinels_intact() && temporaries_left() == 0);
        for record in records {
            let _ = fs::remove_file(record);
        }
        fs::remove_dir_all(root.parent().unwrap()).unwrap();

        assert_eq!(revision_base("clip - vi (sửa 12)"), "clip - vi");
        for name in ["clip - vi", "clip (sửa )", "clip (sửa x)", "clip (sửa 1) extra", "(sửa 1)x"] {
            assert_eq!(revision_base(name), name);
        }
    }

    /// A user's own `DubFlow Output` folder shaped like a job's, translation
    /// data included, is never revised: no sibling folder, nothing changed.
    #[test]
    fn review_saves_need_the_supervisors_record_of_a_finished_job() {
        let base = std::env::temp_dir().join(format!("dubflow-review-fake-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let personal = base.join("Videos/DubFlow Output/Personal");
        let files = [("final_vi.mp4", "video"), ("job_manifest.json", "{\"source\": {\"path\": \"C:/clip.mp4\"}}"),
            (".dubflow-work/translation.json", "{\"cues\": [{\"cue_id\": \"c1\", \"text\": \"a\"}]}"), (".dubflow-work/checkpoint.json", "{}"),
            ("marker.txt", "keep")];
        for (path, body) in files {
            fs::create_dir_all(personal.join(path).parent().unwrap()).unwrap();
            fs::write(personal.join(path), body).unwrap();
        }
        let personal = PathBuf::from(display_path(&personal.canonicalize().unwrap()));
        let state = |dir: &Path| files.map(|(path, _)| (fs::read(dir.join(path)).ok(), fs::metadata(dir.join(path)).and_then(|m| m.modified()).ok()));
        let listing = || { let mut names: Vec<_> = fs::read_dir(personal.parent().unwrap()).unwrap().map(|e| e.unwrap().file_name()).collect(); names.sort(); names };
        let (before, names) = (state(&personal), listing());
        let output = display_path(&personal.join("final_vi.mp4"));
        let overrides = std::collections::BTreeMap::from([("c1".to_owned(), "Xin chào".to_owned())]);
        assert!(save_review(output.clone(), overrides.clone()).is_err());
        assert!(edit::edit_subtitles_load(output.clone()).is_err());
        assert!(edit::edit_subtitles_apply(output.clone(), overrides.clone(), Default::default(), vec![], serde_json::from_value(json!({"font": "Candara", "size": 1.2,
            "primary": "#FDE047", "outline": "#000000", "box": null, "box_opacity": 0.6, "outline_width": 3, "bold": true, "align": "bottom"})).unwrap(), None).is_err());
        // An adjacent status naming the video is the user's file, not the app's record.
        fs::write(personal.join("job.json"), json!({"status": {"state": "COMPLETED"}, "output_path": output}).to_string()).unwrap();
        assert!(save_review(output.clone(), overrides.clone()).is_err());
        fs::remove_file(personal.join("job.json")).unwrap();
        assert_eq!(listing(), names, "no sibling (sửa N) folder");
        assert_eq!(state(&personal), before, "the user's files are byte- and time-identical");
        // Once the supervisor recorded it as a finished job, it is revised as usual.
        let record = record_test_job(&personal.join("final_vi.mp4"));
        let revision = PathBuf::from(save_review(output.clone(), overrides).unwrap());
        assert_eq!(revision, personal.parent().unwrap().join("Personal (sửa 1)"));
        let _ = fs::remove_file(record);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn interrupted_jobs_are_detected_only_when_no_supervisor_holds_the_lock() {
        use sha2::{Digest, Sha256};
        let root = std::env::temp_dir().join(format!("dubflow-liveness-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("control/jobs")).unwrap();
        fs::write(root.join("control/jobs.sqlite3"), b"").unwrap();
        let status = root.join("control/jobs/job-1.json");
        fs::write(&status, b"{}").unwrap();
        let database = fs::canonicalize(root.join("control/jobs.sqlite3")).unwrap();
        let mut key = Sha256::new();
        key.update(database.as_os_str().as_encoded_bytes());
        key.update([0]);
        key.update(b"job-1");
        let locks = database.parent().unwrap().join(".execution-locks");
        fs::create_dir_all(&locks).unwrap();
        let lock = locks.join(format!("{:x}.lock", key.finalize()));
        fs::write(&lock, b"").unwrap();

        assert!(!supervisor_gone(&root, "job-1", &status), "a freshly written status is never probed");
        let old = std::time::SystemTime::now() - std::time::Duration::from_secs(120);
        fs::File::options().write(true).open(&status).unwrap().set_modified(old).unwrap();
        let held = fs::OpenOptions::new().read(true).write(true).open(&lock).unwrap();
        held.try_lock().unwrap();
        assert!(!supervisor_gone(&root, "job-1", &status), "a live supervisor holds the lock");
        drop(held);
        assert!(supervisor_gone(&root, "job-1", &status), "nobody holds the lock after a power loss");
        assert!(fs::OpenOptions::new().read(true).write(true).open(&lock).unwrap().try_lock().is_ok(), "the probe released the lock");
        launched_jobs().lock().unwrap().insert("job-1".into(), std::time::Instant::now());
        assert!(!supervisor_gone(&root, "job-1", &status), "a job this host session started is watched, not probed");
        launched_jobs().lock().unwrap().remove("job-1");
        fs::remove_file(&lock).unwrap();
        assert!(supervisor_gone(&root, "job-1", &status), "no lock file: no supervisor ever ran it here");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_new_version_inherits_the_newest_earlier_local_storage_and_cookies() {
        let root = std::env::temp_dir().join(format!("dubflow-profile-inherit-{}", std::process::id())).join("webview2");
        let _ = fs::remove_dir_all(root.parent().unwrap());
        let storage = |version: &str| LOCAL_STORAGE.iter().fold(root.join(version), |path, part| path.join(part));
        for (version, body) in [("old", "older queue"), ("new", "newest queue")] {
            fs::create_dir_all(storage(version).join("leveldb")).unwrap();
            fs::write(storage(version).join("leveldb/000003.log"), body).unwrap();
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let file = |version: &str, parts: &[&str]| profile_path(&root.join(version), parts);
        for parts in INHERITED_FILES.iter().take(3) {
            fs::create_dir_all(file("new", parts).parent().unwrap()).unwrap();
            fs::write(file("new", parts), parts.last().unwrap()).unwrap();
        }
        inherit_previous_profile(&root.join("next"));
        assert_eq!(fs::read_to_string(storage("next").join("leveldb/000003.log")).unwrap(), "newest queue");
        // Sign-ins survive the update: the cookie store and its key travel too.
        for parts in INHERITED_FILES.iter().take(3) {
            assert_eq!(fs::read_to_string(file("next", parts)).unwrap(), *parts.last().unwrap());
        }
        assert!(!file("next", INHERITED_FILES[3]).exists(), "a file the earlier profile lacks is not invented");
        assert_eq!(fs::read_to_string(storage("new").join("leveldb/000003.log")).unwrap(), "newest queue", "the source is untouched");
        // An existing profile is never overwritten.
        fs::write(storage("next").join("leveldb/000003.log"), "own data").unwrap();
        inherit_previous_profile(&root.join("next"));
        assert_eq!(fs::read_to_string(storage("next").join("leveldb/000003.log")).unwrap(), "own data");
        // No earlier profile: nothing is created here (WebView2 creates its own).
        let lonely = std::env::temp_dir().join(format!("dubflow-profile-alone-{}", std::process::id()));
        let _ = fs::remove_dir_all(&lonely);
        fs::create_dir_all(&lonely).unwrap();
        inherit_previous_profile(&lonely.join("first"));
        assert!(!lonely.join("first").exists());
        fs::remove_dir_all(root.parent().unwrap()).unwrap();
        fs::remove_dir_all(&lonely).unwrap();
    }

    #[test]
    fn a_supervisor_that_exits_without_reporting_fails_the_job_with_its_error() {
        let root = std::env::temp_dir().join(format!("dubflow-watch-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        assert_eq!(supervisor_failure_message("\nDubFlow supervisor: JOB_ID_CONFLICT: job ID belongs to different input\n\n", Some(2)),
            "JOB_ID_CONFLICT: job ID belongs to different input");
        assert_eq!(supervisor_failure_message("", Some(3)), "SUPERVISOR_EXITED: supervisor stopped with exit code 3");
        // A stale RUNNING status from an earlier attempt becomes FAILED with the reason.
        let status = root.join("job-1.json");
        fs::write(&status, serde_json::to_vec(&json!({"job_id": "job-1", "output_path": null, "schema_version": 1, "status": {
            "schema_version": 1, "state": "RUNNING", "reason": "running", "message": "Đang xử lý", "checkpoint_id": "subtitles",
            "progress": {"completed_units": "700", "total_units": "1000", "heartbeat_sequence": "21"},
            "retry": {"attempt": "3", "max_attempts": "3", "condition_fingerprint": null},
            "resource": {"kind": "CPU", "held": true, "release_requested": false}}})).unwrap()).unwrap();
        mark_supervisor_failed(&status, "job-1", "JOB_ID_CONFLICT: refused").unwrap();
        let value: Value = serde_json::from_slice(&fs::read(&status).unwrap()).unwrap();
        assert_eq!((value["status"]["state"].as_str(), value["status"]["reason"].as_str(), value["status"]["message"].as_str()),
            (Some("FAILED"), Some("supervisor_exited"), Some("JOB_ID_CONFLICT: refused")));
        assert_eq!(value["status"]["resource"]["held"], false);
        assert_eq!(value["status"]["progress"]["completed_units"], "700", "the last real progress is kept");
        // A terminal state the supervisor wrote itself is never overwritten.
        let done = root.join("job-2.json");
        let mut completed = value.clone();
        completed["status"]["state"] = json!("COMPLETED");
        fs::write(&done, serde_json::to_vec(&completed).unwrap()).unwrap();
        mark_supervisor_failed(&done, "job-2", "late").unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&fs::read(&done).unwrap()).unwrap()["status"]["state"], "COMPLETED");
        // No status at all (refused before writing): a complete FAILED status is created.
        let missing = root.join("job-3.json");
        mark_supervisor_failed(&missing, "job-3", "JOB_ID_CONFLICT: refused").unwrap();
        let created: Value = serde_json::from_slice(&fs::read(&missing).unwrap()).unwrap();
        assert_eq!((created["job_id"].as_str(), created["status"]["state"].as_str()), (Some("job-3"), Some("FAILED")));
        assert!(created["status"]["progress"].is_object() && created["status"]["retry"].is_object());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn dropped_folders_expand_to_videos_without_dubflow_outputs() {
        let root = std::env::temp_dir().join(format!("dubflow-expand-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        for path in ["a.mp4", "notes.txt", "season/ep1.MKV", "season/deep/ep2.mov", "season/deep/deeper/x/too-deep.mp4",
                     "DubFlow Output/a - vi/final_vi.mp4", "season/.dubflow-work/cache.mp4"] {
            let file = root.join(path);
            fs::create_dir_all(file.parent().unwrap()).unwrap();
            fs::write(&file, b"x").unwrap();
        }
        let names: Vec<String> = expand_videos(&[root.clone()]).iter()
            .map(|path| path.strip_prefix(&root).unwrap().to_string_lossy().replace('\\', "/")).collect();
        assert_eq!(names, ["a.mp4", "season/deep/ep2.mov", "season/ep1.MKV"]);
        assert_eq!(expand_videos(&[root.join("notes.txt"), root.join("a.mp4")]), [root.join("a.mp4")]);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn chosen_results_folder_keeps_the_dubflow_output_layout() {
        let source = std::env::temp_dir().join("clips").join("talk.mp4");
        let chosen = std::env::temp_dir().join("Kết quả");
        let output = job_output_in(&chosen, &source, "job-1").unwrap();
        assert!(output.starts_with(chosen.join("DubFlow Output").join("talk - vi")));
        assert_eq!(default_job_output(&source, "job-1").unwrap(), job_output_in(source.parent().unwrap(), &source, "job-1").unwrap());
        assert!(job_output_in(&chosen, &source, "../x").is_err());
    }

    #[test]
    fn voice_versions_keep_separate_outputs_and_resume_the_same_directory() {
        let source = std::env::temp_dir().join("video [spaces].mp4");
        let original = default_job_output(&source, "job-original").unwrap();
        let variant = default_job_output(&source, "job-variant").unwrap();
        assert_ne!(original, variant);
        assert_eq!(original, default_job_output(&source, "job-original").unwrap());
        let upper = default_job_output(&source, "JOB-original").unwrap();
        assert_ne!(original.to_string_lossy().to_lowercase(), upper.to_string_lossy().to_lowercase());
        assert_ne!(original, source.parent().unwrap().join("DubFlow Output/video [spaces] - vi"));
        for id in ["", ".", "..", "../escape", "job\\escape"] {
            assert!(default_job_output(&source, id).is_err());
        }
        for id in ["CON".to_owned(), "x".repeat(128)] {
            let output = default_job_output(&source, &id).unwrap();
            assert!(output.starts_with(source.parent().unwrap().join("DubFlow Output")));
            assert!(output.components().all(|part| part.as_os_str().to_string_lossy().len() <= 132));
        }
    }

    #[test]
    fn long_titles_and_ids_keep_the_output_folder_short() {
        // The tester's 1.1.10 failure: a long Chinese title and a 128-character run folder.
        let source = PathBuf::from(r"C:\Users\Admin\Downloads\正片五条人之仁科 罗永浩近五个小时的变态超长对谈 (1).mp4");
        let output = job_output_in(Path::new(r"C:\Youtube\Video"), &source, &"x".repeat(128)).unwrap();
        let run = output.file_name().unwrap().to_string_lossy().into_owned();
        assert!(run.starts_with("run-") && run.len() == 16, "{run}");
        assert!(output.starts_with(r"C:\Youtube\Video\DubFlow Output\正片五条人之仁科 罗永浩近五个小时的变态超长对谈 (1) - vi"));
        // The worker needs 135 characters below this folder; Windows allows 259.
        assert!(output.to_string_lossy().chars().count() + 135 <= 259, "{}", output.display());
        let long = job_output_in(Path::new(r"C:\x"), &PathBuf::from(format!(r"C:\x\{}.mp4", "长".repeat(90))), "job-1").unwrap();
        let title = long.parent().unwrap().file_name().unwrap().to_string_lossy().into_owned();
        assert_eq!(title, format!("{} - vi", "长".repeat(40)));
        let padded = job_output_in(Path::new(r"C:\x"), Path::new(r"C:\x\a. . .mp4"), "job-1").unwrap();
        assert!(padded.starts_with(r"C:\x\DubFlow Output\a - vi"));
    }

    #[test]
    fn catalog_reads_the_installed_app_payload_and_rejects_unknown_or_duplicate_ids() {
        let root = std::env::temp_dir().join(format!("dubflow-voice-catalog-test-{}", std::process::id()));
        let path = root.join("app/models/manifests/production-vieneu-v1.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let source = include_str!("../../../../models/manifests/production-vieneu-v1.json");
        fs::write(&path, source).unwrap();
        let catalog = load_voice_catalog(&root).unwrap();
        assert_eq!(catalog.voices.len(), 25);
        assert!(catalog.voices.iter().any(|voice| voice.voice_id == catalog.default_voice_id));
        let mut changed: Value = serde_json::from_str(source).unwrap();
        changed["voice_id"] = json!("missing");
        fs::write(&path, changed.to_string()).unwrap();
        assert!(load_voice_catalog(&root).is_err());
        changed["voice_id"] = json!(catalog.default_voice_id);
        changed["voices"][1] = changed["voices"][0].clone();
        fs::write(&path, changed.to_string()).unwrap();
        assert!(load_voice_catalog(&root).is_err());
        fs::remove_file(&path).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
        fs::remove_dir(root.join("app/models")).unwrap();
        fs::remove_dir(root.join("app")).unwrap();
        fs::remove_dir(&root).unwrap();
    }
}
