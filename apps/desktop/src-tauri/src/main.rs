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
    validate_id(job_id)?;
    // Encode the complete ID: Windows folds path case and reserves some names.
    // Chunk at 64 bytes so even a maximum-length ID fits each path component.
    let mut output = source.parent().unwrap_or(Path::new(".")).join("DubFlow Output")
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

#[tauri::command]
fn pick_files() -> Result<Vec<String>, String> {
    let files = rfd::FileDialog::new()
        .add_filter("Video", &["mp4", "mkv", "mov", "avi", "webm", "m4v"])
        .pick_files()
        .unwrap_or_default();
    Ok(files.into_iter().map(|path| path.to_string_lossy().into_owned()).collect())
}

#[tauri::command]
fn start_job(job_id: String, source_path: String, output_dir: Option<String>, enable_dubbing: Option<bool>, tts_voice_id: Option<String>) -> Result<StartJobResponse, String> {
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
    let output = match output_dir {
        Some(value) => PathBuf::from(value),
        None => default_job_output(&source, &job_id)?,
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

fn main() {
    #[cfg(windows)]
    configure_webview_data().expect("unable to configure DubFlow desktop profile");
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![release_info, voice_catalog, pick_files, start_job, job_status, cancel_job, open_path, reveal_path, review_cues, save_review])
        .run(tauri::generate_context!())
        .expect("error while running DubFlow desktop host");
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
