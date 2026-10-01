use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Write};
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

#[derive(Debug, Deserialize)]
struct StatusEnvelope {
    status: Value,
    output_path: Option<String>,
}

fn fail(message: impl Into<String>) -> String {
    message.into().chars().take(4096).collect()
}

fn validate_id(value: &str) -> Result<(), String> {
    if value.is_empty() || value.len() > 128 || value.chars().any(|character| character.is_control()) || value.bytes().any(|byte| byte == b'/' || byte == b'\\') {
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
fn start_job(job_id: String, source_path: String, output_dir: Option<String>) -> Result<StartJobResponse, String> {
    validate_id(&job_id)?;
    let source = absolute_file(&source_path, "source_path")?;
    let root = version_root()?;
    let control = control_root()?;
    let status = status_path(&control, &job_id)?;
    let output = output_dir
        .map(|value| PathBuf::from(value))
        .unwrap_or_else(|| source.parent().unwrap_or(Path::new(".")).join("DubFlow Output").join(format!("{} - vi", source.file_stem().and_then(|name| name.to_str()).unwrap_or("video"))));
    if !output.is_absolute() {
        return Err(fail("output_dir must be an absolute path"));
    }
    fs::create_dir_all(&output).map_err(|error| fail(format!("unable to create output directory: {error}")))?;
    let model_root = control.join("models");
    let supervisor = supervisor_binary(&root)?;
    let mut command = command_with_no_window(&supervisor);
    let args = [
        "run".to_owned(), "--root".to_owned(), root.to_string_lossy().into_owned(), "--job-id".to_owned(), job_id.clone(),
        "--source".to_owned(), source.to_string_lossy().into_owned(), "--output-dir".to_owned(), output.to_string_lossy().into_owned(),
        "--data-root".to_owned(), control.to_string_lossy().into_owned(), "--model-root".to_owned(), model_root.to_string_lossy().into_owned(),
        "--status-path".to_owned(), status.to_string_lossy().into_owned(),
    ];
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

fn supervisor_server_request(request: Value) -> Result<Value, String> {
    let root = version_root()?;
    let control = control_root()?;
    let supervisor = supervisor_binary(&root)?;
    let mut command = Command::new(supervisor);
    command
        .args([
            "server",
            "--root",
            root.to_string_lossy().as_ref(),
            "--data-root",
            control.to_string_lossy().as_ref(),
            "--model-root",
            control.join("models").to_string_lossy().as_ref(),
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|error| fail(format!("unable to start supervisor source service: {error}")))?;
    let mut stdin = child.stdin.take().ok_or_else(|| fail("supervisor source service stdin is unavailable"))?;
    let mut stdout = BufReader::new(child.stdout.take().ok_or_else(|| fail("supervisor source service stdout is unavailable"))?);
    let payload = serde_json::to_vec(&request).map_err(|error| fail(format!("source request is invalid: {error}")))?;
    stdin.write_all(&payload).map_err(|error| fail(format!("unable to send source request: {error}")))?;
    stdin.write_all(b"\n").map_err(|error| fail(format!("unable to send source request terminator: {error}")))?;
    stdin.flush().map_err(|error| fail(format!("unable to flush source request: {error}")))?;
    drop(stdin);
    let mut line = String::new();
    let mut response: Option<Value> = None;
    for _ in 0..32 {
        line.clear();
        let read = stdout.read_line(&mut line).map_err(|error| fail(format!("unable to read source response: {error}")))?;
        if read == 0 {
            break;
        }
        let value: Value = match serde_json::from_str(line.trim()) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if value.get("event").and_then(Value::as_str) == Some("ready") {
            continue;
        }
        response = Some(value);
        break;
    }
    let _ = child.kill();
    let _ = child.wait();
    let response = response.ok_or_else(|| fail("supervisor did not return a source response"))?;
    if response.get("event").and_then(Value::as_str) == Some("error") {
        return Err(fail(response.get("condition").and_then(Value::as_str).unwrap_or("source request failed")));
    }
    Ok(response)
}

#[tauri::command]
fn enqueue_sources(items: Value) -> Result<Value, String> {
    if !items.is_array() {
        return Err(fail("source items must be an array"));
    }
    supervisor_server_request(json!({"command":"enqueue_sources", "items":items}))
}

#[tauri::command]
fn source_scan_status(scan_id: String) -> Result<Value, String> {
    validate_id(&scan_id)?;
    supervisor_server_request(json!({"command":"source_status", "scan_id":scan_id}))
}

#[tauri::command]
fn pause_source_scan(scan_id: String) -> Result<Value, String> {
    validate_id(&scan_id)?;
    supervisor_server_request(json!({"command":"pause_source_scan", "scan_id":scan_id}))
}

#[tauri::command]
fn resume_source_scan(scan_id: String) -> Result<Value, String> {
    validate_id(&scan_id)?;
    supervisor_server_request(json!({"command":"resume_source_scan", "scan_id":scan_id}))
}

#[tauri::command]
fn cancel_source_scan(scan_id: String) -> Result<Value, String> {
    validate_id(&scan_id)?;
    supervisor_server_request(json!({"command":"cancel_source_scan", "scan_id":scan_id}))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![release_info, pick_files, start_job, job_status, cancel_job, enqueue_sources, source_scan_status, pause_source_scan, resume_source_scan, cancel_source_scan])
        .run(tauri::generate_context!())
        .expect("error while running DubFlow desktop host");
}
