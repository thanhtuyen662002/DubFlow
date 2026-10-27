// A release build is a windowed program: without this, Windows opens a
// terminal next to the app on every launch. Every console tool the host starts
// (Python, FFmpeg, the supervisor) uses CREATE_NO_WINDOW.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod discovery;
mod source;
mod updater;

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

/// A version's first launch starts from the newest earlier version's
/// localStorage (queue, results folder, saved choices) instead of an empty
/// profile. Best effort; the earlier profile is only read, so rolling back
/// still finds its own data.
fn inherit_previous_profile(profile: &Path) {
    if profile.exists() {
        return;
    }
    let Some(root) = profile.parent() else { return };
    let storage = |dir: &Path| LOCAL_STORAGE.iter().fold(dir.to_path_buf(), |path, part| path.join(part));
    let newest = fs::read_dir(root).into_iter().flatten().flatten()
        .map(|entry| storage(&entry.path()))
        .filter_map(|path| fs::metadata(path.join("leveldb")).and_then(|metadata| metadata.modified()).ok().map(|modified| (modified, path)))
        .max_by_key(|(modified, _)| *modified);
    if let Some((_, previous)) = newest {
        let _ = copy_work_cache(&previous, &storage(profile), &[]);
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
    job_output_in(source.parent().unwrap_or(Path::new(".")), source, job_id)
}

/// `<base>/DubFlow Output/<stem> - vi/run-<id>`; base is the source folder
/// or a user-chosen results folder.
fn job_output_in(base: &Path, source: &Path, job_id: &str) -> Result<PathBuf, String> {
    validate_id(job_id)?;
    // Encode the complete ID: Windows folds path case and reserves some names.
    // Chunk at 64 bytes so even a maximum-length ID fits each path component.
    let mut output = base.join("DubFlow Output")
        .join(format!("{} - vi", source.file_stem().and_then(|name| name.to_str()).unwrap_or("video")));
    for (index, chunk) in job_id.as_bytes().chunks(64).enumerate() {
        let encoded: String = chunk.iter().map(|byte| format!("{byte:02x}")).collect();
        output = output.join(if index == 0 { format!("run-{encoded}") } else { encoded });
    }
    Ok(output)
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
async fn python_json(args: Vec<std::ffi::OsString>, what: &'static str) -> Result<Value, String> {
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
fn start_job(job_id: String, source_path: String, output_dir: Option<String>, enable_dubbing: Option<bool>, tts_voice_id: Option<String>, burn_in: Option<bool>, source_language: Option<String>, output_root: Option<String>, subtitle_box: Option<bool>, translation_engine: Option<String>, blur_regions: Option<Vec<BlurRegion>>, blur_auto: Option<bool>, multi_voice: Option<bool>, speaker_voices: Option<std::collections::BTreeMap<String, String>>, separate_background: Option<bool>) -> Result<StartJobResponse, String> {
    validate_id(&job_id)?;
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
    let output = match (output_dir, output_root) {
        (Some(value), _) => PathBuf::from(value),
        (None, Some(base)) => {
            let base = PathBuf::from(base);
            if !base.is_absolute() || !base.is_dir() {
                return Err(fail("Thư mục lưu kết quả không tồn tại; hãy chọn lại"));
            }
            job_output_in(&base, &source, &job_id)?
        }
        (None, None) => default_job_output(&source, &job_id)?,
    };
    if !output.is_absolute() {
        return Err(fail("output_dir must be an absolute path"));
    }
    fs::create_dir_all(&output).map_err(|error| fail(format!("unable to create output directory: {error}")))?;
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
        Ok(ReviewCue {
            start_ms: cue["start_ms"].as_u64().ok_or_else(|| fail("cue has no start"))?,
            end_ms: cue["end_ms"].as_u64().ok_or_else(|| fail("cue has no end"))?,
            source_text: text("source_text"),
            dub_failed: failed.contains(&cue_id),
            speaker: speakers["labels"][&cue_id].as_str().map(str::to_owned),
            edited: edited.is_some(),
            text: edited.unwrap_or_else(|| machine_text.clone()),
            machine_text,
            cue_id,
        })
    }).collect()
}

const REVISION_OF_FILE: &str = "revision_of.json";
/// Work-cache entries every revision rebuilds; copying them would only cost disk.
const REVISION_SKIP: [&str; 2] = ["b2-audio", "export-pending"];
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

/// Prepare a separate folder for a review revision and return it. The original
/// output folder is only read, so its validated outputs stay as they were.
fn create_revision(original: &Path, overrides: &std::collections::BTreeMap<String, String>) -> Result<PathBuf, String> {
    use sha2::{Digest, Sha256};
    let partial = create_revision_temp(original).map_err(|error| fail(format!("Không tạo được thư mục bản sửa: {error}")))?;
    let build = || -> Result<(), String> {
        copy_work_cache(&original.join(".dubflow-work"), &partial.join(".dubflow-work"), &REVISION_SKIP)
            .map_err(|error| fail(format!("Không chép được dữ liệu bản dịch: {error}")))?;
        if !overrides.is_empty() {
            let body = serde_json::to_vec_pretty(&json!({"schema_version": 1, "cues": overrides})).map_err(|error| fail(error.to_string()))?;
            fs::write(partial.join(OVERRIDES_FILE), body).map_err(|error| fail(format!("Không lưu được bản sửa: {error}")))?;
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
    if result.is_err() {
        // Only ever the folder this call created; nothing else is removed.
        let _ = fs::remove_dir_all(&partial);
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
    let dir = review_dir(&output_path)?;
    if overrides.len() > 10_000 || overrides.iter().any(|(id, text)| id.is_empty() || id.len() > 128
        || text.trim().is_empty() || text.chars().count() > 2000) {
        return Err(fail("Câu sửa không hợp lệ: không được để trống hoặc dài quá 2000 ký tự"));
    }
    Ok(display_path(&create_revision(&dir, &overrides)?))
}

#[derive(Debug, Serialize)]
struct FetchStarted {
    status: source::FetchStatus,
    output_dir: String,
}

#[tauri::command]
fn fetch_url(url: String, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Result<FetchStarted, String> {
    let url = url.trim().to_owned();
    let root = version_root()?;
    let supervisor = supervisor_binary(&root)?;
    let status = sources.fetch(url.clone(), &supervisor, &root, &control_root()?)?;
    let output_dir = source::link_output_dir(&url, &status.scan_id)?;
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

/// Show a downloaded source video in Explorer; only files the link service
/// saved below `<data root>\source-media`.
#[tauri::command]
fn reveal_download(path: String) -> Result<(), String> {
    let file = PathBuf::from(&path).canonicalize().map_err(|_| fail("Không tìm thấy video đã tải"))?;
    let media = data_root()?.join("source-media").canonicalize().map_err(|_| fail("Không tìm thấy video đã tải"))?;
    if !file.starts_with(&media) || !file.is_file() {
        return Err(fail("Chỉ mở được video DubFlow đã tải về"));
    }
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

/// Open (or navigate) the discovery browser at a search or creator page.
/// It shares the app's browser profile, so a saved Douyin visit helps here.
#[tauri::command]
async fn open_discovery(app: tauri::AppHandle, platform: String, kind: String, query: String) -> Result<String, String> {
    use tauri::Manager;
    let platform = discovery::Platform::parse(&platform)?;
    let url = discovery::page_url(platform, &kind, &query)?;
    if let Some(window) = app.get_webview_window(discovery::WINDOW) {
        window.navigate(url.clone()).map_err(|error| fail(error.to_string()))?;
        let _ = window.set_focus();
        return Ok(url.to_string());
    }
    tauri::WebviewWindowBuilder::new(&app, discovery::WINDOW, tauri::WebviewUrl::External(url.clone()))
        .initialization_script(discovery::CAPTURE_SCRIPT)
        .title("Khám phá video — cuộn trang để tải thêm, rồi quay lại DubFlow bấm “Lấy video từ trang”")
        .inner_size(1200.0, 860.0)
        .build()
        .map_err(|error| fail(format!("Không mở được cửa sổ khám phá: {error}")))?;
    Ok(url.to_string())
}

/// Scroll the discovery page so it loads more results.
#[tauri::command]
fn scroll_discovery(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window(discovery::WINDOW).ok_or_else(|| fail("Hãy mở trang khám phá trước"))?;
    window.eval(discovery::SCROLL_SCRIPT).map_err(|error| fail(error.to_string()))
}

/// Read the video cards the discovery page currently shows.
#[tauri::command]
async fn collect_discovery(app: tauri::AppHandle, platform: String) -> Result<Vec<discovery::DiscoveredVideo>, String> {
    use tauri::Manager;
    let platform = discovery::Platform::parse(&platform)?;
    let window = app.get_webview_window(discovery::WINDOW).ok_or_else(|| fail("Hãy mở trang khám phá trước"))?;
    let (sender, receiver) = std::sync::mpsc::channel::<String>();
    window.eval_with_callback(discovery::COLLECT_SCRIPT, move |result| { let _ = sender.send(result); })
        .map_err(|error| fail(error.to_string()))?;
    let result = tauri::async_runtime::spawn_blocking(move || receiver.recv_timeout(std::time::Duration::from_secs(10)))
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|_| fail("Trang không phản hồi; hãy đợi trang tải xong rồi thử lại"))?;
    discovery::parse_collection(platform, &result).map_err(fail)
}

/// One JPEG frame of a local video at `position` (0–1 of its duration), for
/// drawing blur regions on. Async: FFprobe/FFmpeg run off the UI thread.
/// Scaled to at most 960 px wide.
#[tauri::command]
async fn video_frame(source_path: String, position: f64) -> Result<tauri::ipc::Response, String> {
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
    let mut command = Command::new(media.join("ffmpeg.exe"));
    command.args(["-nostdin", "-v", "error", "-ss", &format!("{seconds:.3}"), "-i"])
        .arg(&source)
        .args(["-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
    if !output.status.success() || output.stdout.len() < 128 {
        return Err(fail("Không lấy được khung hình ở thời điểm này"));
    }
    Ok(tauri::ipc::Response::new(output.stdout))
}

const DOUYIN_LOGIN: &str = "douyin-login";
const DOUYIN_HOME: &str = "https://www.douyin.com/";

/// Open douyin.com in its own window so it can set fresh cookies (and the
/// user may log in). Async: WebView2 cookie access deadlocks in sync commands.
#[tauri::command]
async fn open_douyin_login(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(window) = app.get_webview_window(DOUYIN_LOGIN) {
        let _ = window.set_focus();
        return Ok(());
    }
    let url = DOUYIN_HOME.parse::<tauri::Url>().map_err(|error| fail(error.to_string()))?;
    tauri::WebviewWindowBuilder::new(&app, DOUYIN_LOGIN, tauri::WebviewUrl::External(url))
        .title("Douyin — mở trang, đăng nhập nếu muốn, rồi quay lại DubFlow bấm “Lưu phiên”")
        .inner_size(1100.0, 800.0)
        .build()
        .map_err(|error| fail(format!("Không mở được cửa sổ Douyin: {error}")))?;
    Ok(())
}

/// Read the Douyin window's cookies and store them as the protected Douyin
/// session used by link downloads. Returns how many cookies were kept.
#[tauri::command]
async fn save_douyin_session(app: tauri::AppHandle, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Result<usize, String> {
    use tauri::Manager;
    let window = app.get_webview_window(DOUYIN_LOGIN).ok_or_else(|| fail("Hãy mở cửa sổ Douyin trước"))?;
    let url = DOUYIN_HOME.parse::<tauri::Url>().map_err(|error| fail(error.to_string()))?;
    let cookies: Vec<(String, String)> = window.cookies_for_url(url)
        .map_err(|error| fail(format!("Không đọc được cookie Douyin: {error}")))?
        .into_iter().map(|cookie| (cookie.name().to_owned(), cookie.value().to_owned())).collect();
    if !cookies.iter().any(|(name, _)| name == "s_v_web_id" || name == "ttwid") {
        return Err(fail("Trang Douyin chưa tải xong; hãy đợi trang hiện video rồi bấm lại"));
    }
    let header = source::cookie_header(&cookies, &source::DOUYIN_PRIORITY).ok_or_else(|| fail("Không có cookie Douyin hợp lệ"))?;
    let count = header.split("; ").count();
    let expires_at = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|error| fail(error.to_string()))?.as_secs() + source::SESSION_SECONDS;
    let root = version_root()?;
    let supervisor = supervisor_binary(&root)?;
    let data = control_root()?;
    let sources = std::sync::Arc::clone(&sources);
    tauri::async_runtime::spawn_blocking(move || sources.save_session("douyin", header, expires_at, &supervisor, &root, &data))
        .await.map_err(|error| fail(error.to_string()))??;
    let _ = window.close();
    Ok(count)
}

fn main() {
    #[cfg(windows)]
    configure_webview_data().expect("unable to configure DubFlow desktop profile");
    let sources = std::sync::Arc::new(source::Sources::default());
    let on_exit = std::sync::Arc::clone(&sources);
    tauri::Builder::default()
        .manage(sources)
        .invoke_handler(tauri::generate_handler![release_info, voice_catalog, pick_files, start_job, job_status, cancel_job, pick_folder, expand_video_paths, open_path, reveal_path, review_cues, save_review, fetch_url, fetch_status, withdraw_fetch, open_folder, reveal_download, open_douyin_login, save_douyin_session, review_clip, video_frame, post_suggestions, discover, open_discovery, scroll_discovery, collect_discovery, translate_terms, review_speakers, check_update, download_update, install_update, list_versions, activate_version, restart_app])
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
            (".dubflow-work/export-pending/final_vi.mp4", "pending")];
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
        assert!(!first.join("final_vi.mp4").exists(), "the revision renders its own output");
        let stored: Value = serde_json::from_slice(&fs::read(first.join(OVERRIDES_FILE)).unwrap()).unwrap();
        assert_eq!(stored, json!({"schema_version": 1, "cues": {"c1": "Xin chào"}}));
        let link: Value = serde_json::from_slice(&fs::read(first.join(REVISION_OF_FILE)).unwrap()).unwrap();
        assert_eq!(link["original_output_dir"], display_path(&original));
        assert_eq!(link["original_manifest_sha256"], "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");

        assert_eq!(PathBuf::from(save_review(output.clone(), std::collections::BTreeMap::new()).unwrap()), root.join("clip - vi (sửa 2)"));
        assert!(!root.join("clip - vi (sửa 2)").join(OVERRIDES_FILE).exists());
        fs::write(first.join("final_vi.mp4"), "revised").unwrap();
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
        fs::remove_dir_all(root.parent().unwrap()).unwrap();

        assert_eq!(revision_base("clip - vi (sửa 12)"), "clip - vi");
        for name in ["clip - vi", "clip (sửa )", "clip (sửa x)", "clip (sửa 1) extra", "(sửa 1)x"] {
            assert_eq!(revision_base(name), name);
        }
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
    fn a_new_version_inherits_the_newest_earlier_local_storage() {
        let root = std::env::temp_dir().join(format!("dubflow-profile-inherit-{}", std::process::id())).join("webview2");
        let _ = fs::remove_dir_all(root.parent().unwrap());
        let storage = |version: &str| LOCAL_STORAGE.iter().fold(root.join(version), |path, part| path.join(part));
        for (version, body) in [("old", "older queue"), ("new", "newest queue")] {
            fs::create_dir_all(storage(version).join("leveldb")).unwrap();
            fs::write(storage(version).join("leveldb/000003.log"), body).unwrap();
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        inherit_previous_profile(&root.join("next"));
        assert_eq!(fs::read_to_string(storage("next").join("leveldb/000003.log")).unwrap(), "newest queue");
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
