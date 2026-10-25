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

fn control_root() -> Result<PathBuf, String> {
    let local_app_data = std::env::var_os("LOCALAPPDATA").ok_or_else(|| fail("LOCALAPPDATA is unavailable"))?;
    let root = PathBuf::from(local_app_data).join("DubFlow");
    fs::create_dir_all(root.join("control").join("jobs")).map_err(|error| fail(format!("unable to create DubFlow control root: {error}")))?;
    Ok(root)
}

fn webview_data_root(local_app_data: &Path, version: &str) -> Result<PathBuf, String> {
    if !local_app_data.is_absolute() || version.is_empty() || version.len() > 64
        || !version.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(fail("desktop profile root or release version is invalid"));
    }
    // Preserve version identity even on case-insensitive Windows filesystems.
    let encoded: String = version.bytes().map(|byte| format!("{byte:02x}")).collect();
    Ok(local_app_data.join("DubFlow/control/webview2").join(encoded))
}

#[cfg(windows)]
fn configure_webview_data() -> Result<(), String> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| fail("LOCALAPPDATA is unavailable"))?;
    let profile = webview_data_root(Path::new(&local_app_data), &release_info().version)?;
    let runtime = version_root()?.canonicalize().map_err(|error| fail(format!("unable to resolve desktop runtime: {error}")))?;
    let ancestor = profile.ancestors().find(|path| path.exists())
        .ok_or_else(|| fail("desktop profile has no available parent"))?;
    if ancestor.canonicalize().map_err(|error| fail(format!("unable to resolve desktop profile parent: {error}")))?.starts_with(&runtime) {
        return Err(fail("desktop profile cannot be inside the immutable runtime"));
    }
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

const VIDEO_EXTENSIONS: [&str; 6] = ["mp4", "mkv", "mov", "avi", "webm", "m4v"];
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
        .add_filter("Video", &["mp4", "mkv", "mov", "avi", "webm", "m4v"])
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
fn start_job(job_id: String, source_path: String, output_dir: Option<String>, enable_dubbing: Option<bool>, tts_voice_id: Option<String>, burn_in: Option<bool>, source_language: Option<String>, output_root: Option<String>, subtitle_box: Option<bool>, translation_engine: Option<String>, blur_regions: Option<Vec<BlurRegion>>, blur_auto: Option<bool>) -> Result<StartJobResponse, String> {
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
    command.args(args);
    command.spawn().map_err(|error| fail(format!("unable to start supervisor: {error}")))?;
    Ok(StartJobResponse { job_id, status_path: status.to_string_lossy().into_owned(), output_dir: output.to_string_lossy().into_owned() })
}

#[tauri::command]
fn job_status(job_id: String) -> Result<Option<Value>, String> {
    let control = control_root()?;
    let status = status_path(&control, &job_id)?;
    if !status.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&status).map_err(|error| fail(format!("unable to read job status: {error}")))?;
    let envelope: StatusEnvelope = serde_json::from_str(&text).map_err(|error| fail(format!("job status is invalid: {error}")))?;
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
    let document = read_json(&work.join("checkpoint.json"), 4 * 1024 * 1024).ok()
        .and_then(|checkpoint| checkpoint["stages"]["b2-audio"]["tts_document"].as_str().map(PathBuf::from))
        .filter(|path| path.starts_with(work))
        .and_then(|path| read_json(&path, 64 * 1024 * 1024).ok());
    document.as_ref().and_then(|value| value["failures"].as_array())
        .map(|failures| failures.iter().filter_map(|failure| failure["segment_id"].as_str().map(str::to_owned)).collect())
        .unwrap_or_default()
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
}

#[tauri::command]
fn review_cues(output_path: String) -> Result<Vec<ReviewCue>, String> {
    let dir = review_dir(&output_path)?;
    let work = dir.join(".dubflow-work");
    let translation = read_json(&work.join("translation.json"), 64 * 1024 * 1024)?;
    let overrides = if dir.join(OVERRIDES_FILE).is_file() { read_json(&dir.join(OVERRIDES_FILE), 4 * 1024 * 1024)? } else { json!({}) };
    let failed = failed_dub_cues(&work);
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
            edited: edited.is_some(),
            text: edited.unwrap_or_else(|| machine_text.clone()),
            machine_text,
            cue_id,
        })
    }).collect()
}

/// Persist reviewed texts beside the outputs; an empty map removes the file.
/// Returns the output directory so the caller can re-run the job in place.
#[tauri::command]
fn save_review(output_path: String, overrides: std::collections::BTreeMap<String, String>) -> Result<String, String> {
    let dir = review_dir(&output_path)?;
    let path = dir.join(OVERRIDES_FILE);
    if overrides.len() > 10_000 || overrides.iter().any(|(id, text)| id.is_empty() || id.len() > 128
        || text.trim().is_empty() || text.chars().count() > 2000) {
        return Err(fail("Câu sửa không hợp lệ: không được để trống hoặc dài quá 2000 ký tự"));
    }
    if overrides.is_empty() {
        if path.is_file() {
            fs::remove_file(&path).map_err(|error| fail(format!("Không xóa được bản sửa: {error}")))?;
        }
    } else {
        let temporary = dir.join(format!("{OVERRIDES_FILE}.partial"));
        let body = serde_json::to_vec_pretty(&json!({"schema_version": 1, "cues": overrides})).map_err(|error| fail(error.to_string()))?;
        fs::write(&temporary, body).and_then(|_| fs::rename(&temporary, &path))
            .map_err(|error| fail(format!("Không lưu được bản sửa: {error}")))?;
    }
    Ok(display_path(&dir))
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
    Ok(FetchStarted { output_dir: output_dir.to_string_lossy().into_owned(), status })
}

#[tauri::command]
fn fetch_status(scan_id: String, sources: tauri::State<'_, std::sync::Arc<source::Sources>>) -> Option<source::FetchStatus> {
    sources.status(&scan_id)
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
        .invoke_handler(tauri::generate_handler![release_info, voice_catalog, pick_files, start_job, job_status, cancel_job, pick_folder, expand_video_paths, open_path, reveal_path, review_cues, save_review, fetch_url, fetch_status, open_douyin_login, save_douyin_session, review_clip, check_update, download_update, install_update, list_versions, activate_version, restart_app, video_frame, discover, open_discovery, scroll_discovery, collect_discovery, translate_terms])
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
    fn browser_profiles_preserve_runtime_identity_and_refuse_invalid_roots() {
        let local = std::env::temp_dir().join("DubFlow profile [spaces]");
        let original = webview_data_root(&local, "0.1.0-rc.film").unwrap();
        assert!(original.starts_with(local.join("DubFlow/control/webview2")));
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
