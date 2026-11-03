//! Documentary project setup and script review (ADR-0049, phase 1).
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use crate::{control_root, display_path, fail, python_json, review_dir, version_root, CREATE_NO_WINDOW};

const VIDEO_EXTENSIONS: [&str; 7] = ["mp4", "mov", "mkv", "webm", "avi", "m4v", "flv"];
const PREFIX: &str = "Phim tai lieu - ";
const TRANSCRIPT_LIMIT: u64 = 32 * 1024 * 1024;
const MANIFEST_LIMIT: u64 = 8 * 1024 * 1024;
const CONTEXT_LIMIT: u64 = 16 * 1024 * 1024;
const SCRIPT_LIMIT: u64 = 4 * 1024 * 1024;
const KOKORO_PROFILE: &str = "app/models/manifests/production-kokoro-en-v1.json";
const SPEEDS: (f64, f64) = (0.8, 1.2);

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DocuRequest {
    title: String,
    topic: String,
    target_minutes: u8,
    cinematic_framing_preset: String,
    burn_end_credits: bool,
    sources: Vec<DocuSourceInput>,
    output_root: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DocuSourceInput {
    kind: String,
    output_path: Option<String>,
    video: Option<String>,
    subtitles: Option<String>,
    title: String,
}

#[derive(Debug, Clone)]
struct ResolvedSource {
    id: String,
    title: String,
    video: PathBuf,
    transcript: PathBuf,
    origin_url: Option<String>,
    credit: Option<Value>,
}

fn text(value: &str, limit: usize, required: bool) -> bool {
    value.chars().count() <= limit
        && !value.chars().any(|character| character.is_control() && !['\n', '\r', '\t'].contains(&character))
        && (!required || !value.trim().is_empty())
}

fn has_extension(path: &Path, extensions: &[&str]) -> bool {
    path.extension().and_then(|extension| extension.to_str())
        .is_some_and(|extension| extensions.iter().any(|known| known.eq_ignore_ascii_case(extension)))
}

fn absolute_file(value: &str, extensions: &[&str], max_bytes: Option<u64>) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if !path.is_absolute() || !path.is_file() || !has_extension(&path, extensions) {
        return Err(fail("Không mở được video hoặc phụ đề đã chọn"));
    }
    if max_bytes.is_some_and(|limit| fs::metadata(&path).is_ok_and(|metadata| metadata.len() > limit)) {
        return Err(fail("File phụ đề vượt quá 8 MB"));
    }
    Ok(path)
}

fn read_json(path: &Path, limit: u64, message: &str) -> Result<Value, String> {
    let metadata = fs::metadata(path).map_err(|_| fail(message))?;
    if !metadata.is_file() || metadata.len() > limit { return Err(fail(message)); }
    let bytes = fs::read(path).map_err(|_| fail(message))?;
    serde_json::from_slice(&bytes).map_err(|_| fail(message))
}

fn unprefix_windows_path(value: &str) -> String {
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else {
        value.strip_prefix(r"\\?\").unwrap_or(value).to_owned()
    }
}

fn resolve_job(output_path: &str, title: &str) -> Result<ResolvedSource, String> {
    if Path::new(output_path).file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Hãy chọn final_vi.mp4 đã hoàn tất"));
    }
    let dir = review_dir(output_path).map_err(|_| fail("Nguồn cần là final_vi.mp4 đã hoàn tất trong DubFlow Output"))?;
    let work = dir.join(".dubflow-work");
    let transcript = work.join("transcript.json");
    let transcript_meta = fs::metadata(&transcript).map_err(|_| fail("Không tìm thấy transcript.json của nguồn DubFlow"))?;
    if !transcript_meta.is_file() || transcript_meta.len() > TRANSCRIPT_LIMIT {
        return Err(fail("transcript.json vượt quá 32 MB hoặc không hợp lệ"));
    }
    let manifest = read_json(&dir.join("job_manifest.json"), MANIFEST_LIMIT, "Không đọc được job_manifest.json của nguồn")?;
    let source = manifest.get("source").and_then(|value| value.get("path")).and_then(Value::as_str)
        .ok_or_else(|| fail("job_manifest.json không có source.path"))?;
    let source_path = PathBuf::from(unprefix_windows_path(source));
    if !source_path.is_absolute() || !source_path.is_file() {
        return Err(fail("Không tìm thấy video gốc trong job_manifest.json"));
    }
    let credit_path = dir.join("source_credit.json");
    let (origin_url, credit) = if credit_path.exists() {
        let credit = read_json(&credit_path, 16 * 1024, "Không đọc được source_credit.json của nguồn")?;
        let url = credit.get("url").and_then(Value::as_str).filter(|url| !url.trim().is_empty()).map(str::to_owned);
        (url, Some(credit))
    } else { (None, None) };
    Ok(ResolvedSource { id: String::new(), title: title.to_owned(), video: source_path, transcript, origin_url, credit })
}

fn resolve_sources(request: &DocuRequest) -> Result<Vec<ResolvedSource>, String> {
    if !text(&request.title, 120, true) || !text(&request.topic, 600, false) {
        return Err(fail("Tiêu đề tối đa 120 ký tự; ghi chú chủ đề tối đa 600 ký tự"));
    }
    if !(3..=20).contains(&request.target_minutes) { return Err(fail("Thời lượng mục tiêu phải từ 3 đến 20 phút")); }
    if !["none", "letterbox_235", "overscan_108"].contains(&request.cinematic_framing_preset.as_str()) {
        return Err(fail("Kiểu khung hình không hợp lệ"));
    }
    if !(2..=5).contains(&request.sources.len()) { return Err(fail("Hãy chọn từ 2 đến 5 nguồn")); }
    let mut resolved = Vec::with_capacity(request.sources.len());
    for (index, source) in request.sources.iter().enumerate() {
        if !text(&source.title, 120, true) { return Err(fail(format!("Nguồn {}: tiêu đề từ 1 đến 120 ký tự", index + 1))); }
        let mut result = match source.kind.as_str() {
            "job" => {
                if source.video.is_some() || source.subtitles.is_some() { return Err(fail(format!("Nguồn {}: trường file không hợp lệ", index + 1))); }
                resolve_job(source.output_path.as_deref().unwrap_or_default(), &source.title)?
            }
            "file" => {
                if source.output_path.is_some() { return Err(fail(format!("Nguồn {}: đường dẫn job không hợp lệ", index + 1))); }
                let video = absolute_file(source.video.as_deref().unwrap_or_default(), &VIDEO_EXTENSIONS, None)
                    .map_err(|_| fail(format!("Nguồn {}: chọn video MP4, MOV, MKV, WEBM, AVI, M4V hoặc FLV", index + 1)))?;
                let transcript = absolute_file(source.subtitles.as_deref().unwrap_or_default(), &["srt"], Some(8 * 1024 * 1024))
                    .map_err(|_| fail(format!("Nguồn {}: chọn phụ đề SRT không quá 8 MB", index + 1)))?;
                ResolvedSource { id: String::new(), title: source.title.clone(), video, transcript, origin_url: None, credit: None }
            }
            _ => return Err(fail(format!("Nguồn {}: loại nguồn không hợp lệ", index + 1))),
        };
        result.id = format!("src_{:02}", index + 1);
        resolved.push(result);
    }
    Ok(resolved)
}

fn output_dir(base: &Path, title: &str, seed: &str) -> PathBuf {
    let digest = Sha256::digest(seed.as_bytes());
    let run: String = digest.iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    base.join("DubFlow Output").join(format!("{PREFIX}{}", crate::product::folder_title(title))).join(format!("run-{run}"))
}

fn docu_dir(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !path.is_absolute() || path.components().any(|component| matches!(component, std::path::Component::ParentDir)) {
        return Err(fail("Thư mục phim tài liệu không hợp lệ"));
    }
    let dir = path.canonicalize().map_err(|_| fail("Không tìm thấy thư mục phim tài liệu"))?;
    let run = dir.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    let title = dir.parent().and_then(Path::file_name).and_then(|name| name.to_str()).unwrap_or_default();
    let root = dir.parent().and_then(Path::parent).and_then(Path::file_name);
    let shape = run.len() == 16 && run.starts_with("run-") && run[4..].bytes().all(|byte| byte.is_ascii_hexdigit());
    if !shape || !title.starts_with(PREFIX) || root.is_none_or(|name| name != "DubFlow Output") {
        return Err(fail("Thư mục phim tài liệu không hợp lệ"));
    }
    let spec = read_json(&dir.join(".dubflow-work/spec.json"), 256 * 1024, "Không đọc được spec.json của phim tài liệu")?;
    if spec["schema_version"] != 1 { return Err(fail("Phiên bản dự án phim tài liệu không được hỗ trợ")); }
    Ok(dir)
}

fn read_spec(work: &Path) -> Option<Value> {
    crate::product::read_small_json(&work.join("spec.json"), 256 * 1024)
        .filter(|spec| spec["schema_version"] == 1 && spec["sources"].is_array())
}

fn context_args(spec: &Path, ffprobe: &Path) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "context".into(), "--spec".into(), spec.into(), "--ffprobe".into(), ffprobe.into()]
}

fn check_args(dir: &Path, script: &Path) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "check".into(), "--run-dir".into(), dir.into(), "--script".into(), script.into()]
}

fn script_error(value: &Value) -> String {
    value.get("message").and_then(Value::as_str).filter(|message| !message.trim().is_empty())
        .map(str::to_owned).unwrap_or_else(|| "Không kiểm tra được kịch bản phim tài liệu".to_owned())
}

/// Create the run folder and spec, then build the source contexts (a few seconds, off the UI thread).
#[tauri::command]
pub async fn docu_create(request: DocuRequest) -> Result<Value, String> {
    let sources = resolve_sources(&request)?;
    let base = if let Some(value) = &request.output_root {
        let path = PathBuf::from(value);
        if !path.is_absolute() || !path.is_dir() { return Err(fail("Hãy chọn lại thư mục lưu kết quả")); }
        path
    } else if let Some(job) = request.sources.iter().find(|source| source.kind == "job") {
        let dir = review_dir(job.output_path.as_deref().unwrap_or_default())?;
        dir.ancestors().find(|path| path.file_name().is_some_and(|name| name == "DubFlow Output"))
            .and_then(Path::parent).map(Path::to_path_buf).ok_or_else(|| fail("Không tìm được thư mục DubFlow Output của nguồn"))?
    } else {
        PathBuf::from(std::env::var_os("USERPROFILE").ok_or_else(|| fail("Không tìm được thư mục Videos của người dùng"))?).join("Videos")
    };
    let seed = format!("{}:{}:{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos(), std::process::id(), request.title);
    let output = output_dir(&base, &request.title, &seed);
    fs::create_dir_all(output.parent().ok_or_else(|| fail("Không tạo được thư mục phim tài liệu"))?)
        .map_err(|_| fail("Không tạo được thư mục phim tài liệu"))?;
    fs::create_dir(&output).map_err(|_| fail("Không tạo được phiên phim tài liệu mới"))?;
    let work = output.join(".dubflow-work");
    fs::create_dir(&work).map_err(|_| fail("Không tạo được thư mục làm việc phim tài liệu"))?;
    let spec_sources: Vec<Value> = sources.iter().map(|source| json!({
        "id": source.id, "title": source.title, "video": display_path(&source.video), "transcript": display_path(&source.transcript),
        "origin_url": source.origin_url, "credit": source.credit,
    })).collect();
    let spec = json!({ "schema_version": 1, "title": request.title.trim(), "topic": request.topic.trim(),
        "target_minutes": request.target_minutes, "cinematic_framing_preset": request.cinematic_framing_preset,
        "burn_end_credits": request.burn_end_credits, "sources": spec_sources });
    let spec_path = work.join("spec.json");
    let bytes = serde_json::to_vec(&spec).map_err(|_| fail("Không tạo được spec phim tài liệu"))?;
    fs::write(&spec_path, bytes).map_err(|_| fail("Không ghi được spec phim tài liệu"))?;

    let ffprobe = version_root()?.join("runtime/media/ffprobe.exe");
    let summary = python_json(context_args(&spec_path, &ffprobe), "bộ xử lý phim tài liệu").await?;
    if summary["state"] != "COMPLETED" { return Err(fail(script_error(&summary))); }
    let result_sources = summary.get("sources").and_then(Value::as_array).cloned().unwrap_or_default();
    Ok(json!({ "dir": display_path(&output), "sources": result_sources }))
}

#[tauri::command]
pub fn docu_runs(roots: Vec<String>) -> Vec<Value> {
    crate::product::find_runs(&roots, PREFIX, 60).into_iter().filter_map(|path| {
        let dir = docu_dir(&display_path(&path)).ok()?;
        let work = dir.join(".dubflow-work");
        let spec = read_spec(&work)?;
        Some(json!({ "dir": display_path(&dir), "created": crate::product::created_seconds(&dir),
            "spec": spec, "script": dir.join("documentary_script.json").is_file() }))
    }).collect()
}

#[tauri::command]
pub fn docu_open(dir: String) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    let work = dir.join(".dubflow-work");
    let spec = read_spec(&work).ok_or_else(|| fail("Không đọc được spec phim tài liệu"))?;
    let mut contexts = Vec::new();
    for source in spec["sources"].as_array().into_iter().flatten() {
        let id = source["id"].as_str().ok_or_else(|| fail("Nguồn phim tài liệu thiếu id"))?;
        let path = work.join("sources").join(id).join("source_context.json");
        contexts.push(read_json(&path, CONTEXT_LIMIT, "Không đọc được source_context.json của nguồn")?);
    }
    let script_path = dir.join("documentary_script.json");
    let script = if script_path.is_file() { Some(read_json(&script_path, SCRIPT_LIMIT, "documentary_script.json vượt quá 4 MB hoặc không hợp lệ")?) } else { None };
    Ok(json!({ "dir": display_path(&dir), "spec": spec, "contexts": contexts, "script": script, "narration": narration_info(&dir), "visual": visual_info(&dir) }))
}

/// `narration.wav` directly inside a documentary run, for `allow_media` (#446).
pub(crate) fn narration_audio(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if path.file_name().is_none_or(|name| name != "narration.wav") {
        return Err(fail("Không phải lời đọc phim tài liệu"));
    }
    let dir = docu_dir(&display_path(path.parent().ok_or_else(|| fail("Không phải lời đọc phim tài liệu"))?))?;
    let file = dir.join("narration.wav");
    if !file.is_file() { return Err(fail("Chưa có lời đọc")); }
    Ok(file)
}

/// The finished narration (its audio and timeline) and the last run's status, when there are any.
fn narration_info(dir: &Path) -> Value {
    let audio = dir.join("narration.wav");
    let timeline = crate::product::read_small_json(&dir.join("master_audio_timeline.json"), SCRIPT_LIMIT);
    json!({ "audio": (audio.is_file() && timeline.is_some()).then(|| display_path(&audio)), "timeline": timeline,
        "status": crate::product::read_small_json(&dir.join(".dubflow-work/narration/status.json"), 64 * 1024) })
}

fn voice_profile(root: &Path) -> Result<Value, String> {
    read_json(&root.join(KOKORO_PROFILE), 1024 * 1024, "Không đọc được danh sách giọng tiếng Anh")
}

/// The English narration voices of this version, and the default one.
#[tauri::command]
pub fn docu_voices() -> Result<Value, String> {
    let profile = voice_profile(&version_root()?)?;
    let voices: Vec<Value> = profile["voices"].as_array().into_iter().flatten().map(|voice| json!({
        "voiceId": voice["voice_id"], "name": voice["name"], "gender": voice["gender"], "accent": voice["accent"], "description": voice["description"],
    })).collect();
    if voices.is_empty() { return Err(fail("Danh sách giọng tiếng Anh trống")); }
    Ok(json!({ "voices": voices, "defaultVoiceId": profile["default_voice_id"] }))
}

fn narration_args(dir: &Path, root: &Path, models: &Path, voice: &str, speed: f64) -> Vec<OsString> {
    vec!["-B".into(), "-m".into(), "engine.dubflow.docu".into(), "narrate".into(), "--run-dir".into(), dir.into(),
        "--app-root".into(), root.join("app").into(), "--model-root".into(), models.into(),
        "--ffmpeg".into(), root.join("runtime/media/ffmpeg.exe").into(), "--voice".into(), voice.into(), "--speed".into(), format!("{speed:.2}").into()]
}

fn write_job_status(work: &Path, state: &str, message: &str) -> Result<(), String> {
    fs::create_dir_all(work).map_err(|_| fail("Không tạo được thư mục làm việc"))?;
    let partial = work.join("status.json.partial");
    fs::write(&partial, serde_json::to_vec(&json!({"schema_version":1,"state":state,"done":0,"total":0,"message":message})).unwrap())
        .and_then(|()| fs::rename(partial, work.join("status.json")))
        .map_err(|_| fail("Không ghi được trạng thái công việc"))
}

/// What a background engine job is called in its messages.
struct Job { work: &'static str, busy: &'static str, starting: &'static str, cannot: &'static str, cancelled: &'static str, stopped: &'static str }
const NARRATION_JOB: Job = Job { work: ".dubflow-work/narration", busy: "Lời đọc của dự án này đang được tạo",
    starting: "Đang khởi động giọng đọc tiếng Anh…", cannot: "Không chạy được bộ tạo lời đọc",
    cancelled: "Đã hủy tạo lời đọc.", stopped: "Bộ tạo lời đọc đã dừng. Hãy thử lại." };
const VISUAL_JOB: Job = Job { work: ".dubflow-work/visual", busy: "Kế hoạch hình ảnh của dự án này đang được lập",
    starting: "Đang khởi động lập kế hoạch hình ảnh…", cannot: "Không chạy được bộ lập kế hoạch hình ảnh",
    cancelled: "Đã hủy lập kế hoạch hình ảnh.", stopped: "Bộ lập kế hoạch hình ảnh đã dừng. Hãy thử lại." };

/// Run `python -B -m engine.dubflow.docu <args>` in the background with a status file the page polls; one at a time per kind.
fn spawn_job(dir: &Path, job: &Job, args: Vec<OsString>) -> Result<(), String> {
    let root = version_root()?;
    let work = dir.join(job.work);
    if crate::product::read_small_json(&work.join("status.json"), 64 * 1024).is_some_and(|status| status["state"] == "RUNNING") {
        return Err(fail(job.busy));
    }
    let _ = fs::remove_file(work.join("cancel"));
    write_job_status(&work, "RUNNING", job.starting)?;
    // The engine's own errors (a traceback) land here for diagnosis (#444).
    let log = fs::File::create(work.join("engine.log")).map_err(|_| fail("Không ghi được nhật ký"))?;
    let mut command = Command::new(root.join("runtime/python.exe"));
    command.current_dir(root.join("app")).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::from(log));
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| {
        let _ = write_job_status(&work, "FAILED", job.cannot);
        fail(job.cannot)
    })?;
    let (cancelled, stopped) = (job.cancelled, job.stopped);
    std::thread::spawn(move || {
        let _ = child.wait();
        let status = crate::product::read_small_json(&work.join("status.json"), 64 * 1024);
        if status.is_none_or(|value| value["state"] == "RUNNING") {
            let was_cancelled = work.join("cancel").exists();
            let _ = write_job_status(&work, if was_cancelled { "CANCELLED" } else { "FAILED" }, if was_cancelled { cancelled } else { stopped });
        }
    });
    Ok(())
}

fn cancel_job(dir: &str, job: &Job) -> Result<(), String> {
    let work = docu_dir(dir)?.join(job.work);
    fs::create_dir_all(&work).and_then(|()| fs::write(work.join("cancel"), b"")).map_err(|_| fail("Không hủy được"))
}

/// Speak the saved script in the background; `docu_narration_status` follows it.
#[tauri::command]
pub fn docu_narrate(dir: String, voice: String, speed: f64) -> Result<(), String> {
    let dir = docu_dir(&dir)?;
    let root = version_root()?;
    let known = voice_profile(&root)?["voices"].as_array().into_iter().flatten().any(|item| item["voice_id"] == voice.as_str());
    if !known { return Err(fail("Giọng đọc tiếng Anh đã chọn không có trong phiên bản này")); }
    if !speed.is_finite() || !(SPEEDS.0..=SPEEDS.1).contains(&speed) { return Err(fail("Tốc độ đọc phải từ 0,8 đến 1,2")); }
    if !dir.join("documentary_script.json").is_file() { return Err(fail("Hãy viết và lưu kịch bản trước khi tạo lời đọc")); }
    spawn_job(&dir, &NARRATION_JOB, narration_args(&dir, &root, &control_root()?.join("models"), &voice, speed))
}

#[tauri::command]
pub fn docu_narration_status(dir: String) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    Ok(narration_info(&dir))
}

#[tauri::command]
pub fn docu_narration_cancel(dir: String) -> Result<(), String> {
    cancel_job(&dir, &NARRATION_JOB)
}

fn plan_args(dir: &Path, root: &Path, models: &Path) -> Vec<OsString> {
    vec!["-B".into(), "-m".into(), "engine.dubflow.docu".into(), "plan".into(), "--run-dir".into(), dir.into(),
        "--app-root".into(), root.join("app").into(), "--model-root".into(), models.into(),
        "--ffmpeg".into(), root.join("runtime/media/ffmpeg.exe").into()]
}

/// The visual plan (phase 2b) and the last planning run's status.
fn visual_info(dir: &Path) -> Value {
    json!({ "plan": crate::product::read_small_json(&dir.join("visual_plan.json"), SCRIPT_LIMIT),
        "status": crate::product::read_small_json(&dir.join(".dubflow-work/visual/status.json"), 64 * 1024) })
}

/// Cut the sources into the visual plan in the background (shots, caption band, slices).
#[tauri::command]
pub fn docu_plan(dir: String) -> Result<(), String> {
    let dir = docu_dir(&dir)?;
    if !dir.join("master_audio_timeline.json").is_file() { return Err(fail("Hãy tạo lời đọc trước khi lập kế hoạch hình ảnh")); }
    let root = version_root()?;
    spawn_job(&dir, &VISUAL_JOB, plan_args(&dir, &root, &control_root()?.join("models")))
}

#[tauri::command]
pub fn docu_plan_status(dir: String) -> Result<Value, String> {
    Ok(visual_info(&docu_dir(&dir)?))
}

#[tauri::command]
pub fn docu_plan_cancel(dir: String) -> Result<(), String> {
    cancel_job(&dir, &VISUAL_JOB)
}

/// Validate a draft with the engine, which publishes `documentary_script.json` and returns it normalised.
#[tauri::command]
pub async fn docu_check_script(dir: String, script: Value) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    let bytes = serde_json::to_vec(&script).map_err(|_| fail("Kịch bản không hợp lệ"))?;
    if bytes.len() as u64 > SCRIPT_LIMIT { return Err(fail("Kịch bản vượt quá 4 MB")); }
    let draft = dir.join(".dubflow-work/script-draft.json");
    let mut file = fs::File::create(&draft).map_err(|_| fail("Không ghi được bản nháp kịch bản"))?;
    file.write_all(&bytes).map_err(|_| fail("Không ghi được bản nháp kịch bản"))?;
    let result = python_json(check_args(&dir, &draft), "bộ kiểm tra kịch bản").await?;
    if result["state"] == "FAILED" { return Err(fail(script_error(&result))); }
    Ok(result)
}

/// Pick a local documentary video or subtitle through the host file dialog.
#[tauri::command]
pub fn docu_pick_file(kind: String) -> Result<Option<String>, String> {
    let dialog = rfd::FileDialog::new();
    let file = match kind.as_str() {
        "video" => dialog.set_title("Chọn video nguồn").add_filter("Video", &VIDEO_EXTENSIONS).pick_file(),
        "subtitles" => dialog.set_title("Chọn phụ đề SRT").add_filter("Phụ đề SRT", &["srt"]).pick_file(),
        _ => return Err(fail("Loại file phim tài liệu không hợp lệ")),
    };
    Ok(file.map(|path| display_path(&path)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::Duration;

    fn request(sources: Vec<Value>) -> DocuRequest {
        serde_json::from_value(json!({"title":"Công trình lớn","topic":"Ghi chú", "targetMinutes":11,
            "cinematicFramingPreset":"none", "burnEndCredits":false, "sources":sources, "outputRoot":null})).unwrap()
    }

    fn local_source(video: &Path, subtitles: &Path, title: &str) -> Value {
        json!({"kind":"file","video":display_path(video),"subtitles":display_path(subtitles),"title":title})
    }

    fn file_fixture(base: &Path) -> (PathBuf, PathBuf) {
        let video = base.join("source.mp4");
        let subtitles = base.join("source.srt");
        fs::create_dir_all(base).unwrap();
        fs::write(&video, b"video").unwrap();
        fs::write(&subtitles, b"1\n00:00:00,000 --> 00:00:01,000\nHello\n").unwrap();
        (video, subtitles)
    }

    #[test]
    fn validates_request_fields_and_unknown_keys() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-validation-{}", std::process::id()));
        let (video, subtitles) = file_fixture(&base);
        let good = vec![local_source(&video, &subtitles, "Nguồn một"), local_source(&video, &subtitles, "Nguồn hai")];
        assert!(resolve_sources(&request(good.clone())).is_ok());
        let parsed = |value: Value| serde_json::from_value::<DocuRequest>(value).unwrap();
        let valid = json!({"title":"Công trình lớn","topic":"Ghi chú", "targetMinutes":11,
            "cinematicFramingPreset":"none", "burnEndCredits":false, "sources":good, "outputRoot":null});
        for (field, value) in [("title", json!("")), ("title", json!("x".repeat(121))), ("topic", json!("x".repeat(601))),
            ("topic", json!("x\u{0001}")), ("targetMinutes", json!(2)), ("targetMinutes", json!(21)),
            ("cinematicFramingPreset", json!("other")), ("sources", json!([])),
            ("sources", json!([local_source(&video, &subtitles, "A")]))] {
            let mut bad = valid.clone(); bad[field] = value;
            assert!(resolve_sources(&parsed(bad)).is_err(), "{field}");
        }
        let mut fractional = valid.clone();
        fractional["targetMinutes"] = json!(3.5);
        assert!(serde_json::from_value::<DocuRequest>(fractional).is_err());
        let mut unknown = valid;
        unknown["extra"] = json!(true);
        assert!(serde_json::from_value::<DocuRequest>(unknown).is_err());
        fs::write(&subtitles, vec![b' '; 8 * 1024 * 1024 + 1]).unwrap();
        assert!(resolve_sources(&request(vec![local_source(&video, &subtitles, "A"), local_source(&video, &subtitles, "B")])).is_err());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn resolves_job_transcript_manifest_verbatim_path_and_credit() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-job-{}", std::process::id()));
        let job = base.join("DubFlow Output/job - vi");
        let original = base.join("original.mkv");
        fs::create_dir_all(job.join(".dubflow-work")).unwrap();
        fs::write(job.join("final_vi.mp4"), b"dubbed").unwrap();
        fs::write(job.join(".dubflow-work/translation.json"), b"{}").unwrap();
        fs::write(job.join(".dubflow-work/transcript.json"), b"{}").unwrap();
        fs::write(&original, b"original").unwrap();
        fs::write(job.join("job_manifest.json"), json!({"source":{"path":format!(r"\\?\{}", original.display())}}).to_string()).unwrap();
        fs::write(job.join("source_credit.json"), json!({"schema_version":1,"url":"https://example.org/a"}).to_string()).unwrap();
        let source = resolve_job(&display_path(&job.join("final_vi.mp4")), "Job title").unwrap();
        assert_eq!(source.video, original);
        assert_eq!(display_path(&source.transcript), display_path(&job.canonicalize().unwrap().join(".dubflow-work").join("transcript.json")));
        assert_eq!(source.origin_url.as_deref(), Some("https://example.org/a"));
        assert!(source.credit.is_some());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn names_and_checks_run_folder_boundary() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-boundary-{}", std::process::id()));
        let output = output_dir(&base, &format!("Large: project / {}", "文".repeat(80)), "seed");
        assert_eq!(output.file_name().unwrap().to_string_lossy().len(), 16);
        assert!(output.parent().unwrap().file_name().unwrap().to_string_lossy().starts_with(PREFIX));
        fs::create_dir_all(output.join(".dubflow-work")).unwrap();
        fs::write(output.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        assert_eq!(docu_dir(&display_path(&output)).unwrap(), output.canonicalize().unwrap());
        assert!(docu_dir("relative").is_err());
        assert!(docu_dir(&display_path(&base)).is_err());
        let args = context_args(Path::new("spec.json"), Path::new("ffprobe.exe"));
        assert_eq!(args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect::<Vec<_>>(),
            ["-m", "engine.dubflow.docu", "context", "--spec", "spec.json", "--ffprobe", "ffprobe.exe"]);
        let args = check_args(Path::new("run"), Path::new("draft.json"));
        assert_eq!(args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect::<Vec<_>>(),
            ["-m", "engine.dubflow.docu", "check", "--run-dir", "run", "--script", "draft.json"]);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn narration_commands_and_info() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-narration-{}", std::process::id()));
        let output = output_dir(&base, "Lời đọc", "seed-narration");
        fs::create_dir_all(output.join(".dubflow-work")).unwrap();
        fs::write(output.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        let dir = output.canonicalize().unwrap();
        assert!(narration_info(&dir)["audio"].is_null());
        write_job_status(&dir.join(".dubflow-work/narration"), "RUNNING", "Đang đọc").unwrap();
        assert_eq!(docu_narration_status(display_path(&dir)).unwrap()["status"]["state"], "RUNNING");
        docu_narration_cancel(display_path(&dir)).unwrap();
        assert!(dir.join(".dubflow-work/narration/cancel").is_file());
        // The audio counts only with its timeline.
        fs::write(dir.join("narration.wav"), b"RIFF").unwrap();
        assert!(narration_info(&dir)["audio"].is_null());
        fs::write(dir.join("master_audio_timeline.json"), json!({"schema_version":1,"duration":12.5}).to_string()).unwrap();
        assert!(narration_info(&dir)["audio"].as_str().unwrap().ends_with("narration.wav"));
        assert_eq!(narration_info(&dir)["timeline"]["duration"], 12.5);
        // The player may stream this file, and only this one (#446).
        assert_eq!(narration_audio(&display_path(&dir.join("narration.wav"))).unwrap(), dir.join("narration.wav"));
        fs::write(dir.join("other.wav"), b"RIFF").unwrap();
        assert!(narration_audio(&display_path(&dir.join("other.wav"))).is_err());
        fs::create_dir_all(dir.join("sub")).unwrap();
        fs::write(dir.join("sub/narration.wav"), b"RIFF").unwrap();
        assert!(narration_audio(&display_path(&dir.join("sub/narration.wav"))).is_err());
        assert!(narration_audio("relative/narration.wav").is_err());
        let args: Vec<String> = narration_args(Path::new("run"), Path::new("v"), Path::new("models"), "am_michael", 1.0)
            .iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args[..4], ["-B", "-m", "engine.dubflow.docu", "narrate"]);
        assert_eq!(args[args.len() - 4..], ["--voice", "am_michael", "--speed", "1.00"]);
        assert!(docu_narration_cancel("relative".into()).is_err());
        let profile: Value = serde_json::from_str(include_str!("../../../../models/manifests/production-kokoro-en-v1.json")).unwrap();
        assert_eq!(profile["default_voice_id"], "am_michael");
        assert!(profile["voices"].as_array().unwrap().iter().any(|voice| voice["voice_id"] == "am_michael"));
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn plan_commands_and_info() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-plan-{}", std::process::id()));
        let output = output_dir(&base, "Kế hoạch", "seed-plan");
        fs::create_dir_all(output.join(".dubflow-work")).unwrap();
        fs::write(output.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        let dir = output.canonicalize().unwrap();
        assert!(docu_plan(display_path(&dir)).unwrap_err().contains("lời đọc"));
        assert!(visual_info(&dir)["plan"].is_null());
        fs::write(dir.join("visual_plan.json"), json!({"schema_version":1,"scenes":[]}).to_string()).unwrap();
        write_job_status(&dir.join(".dubflow-work/visual"), "COMPLETED", "Xong").unwrap();
        let info = docu_plan_status(display_path(&dir)).unwrap();
        assert_eq!(info["plan"]["schema_version"], 1);
        assert_eq!(info["status"]["state"], "COMPLETED");
        docu_plan_cancel(display_path(&dir)).unwrap();
        assert!(dir.join(".dubflow-work/visual/cancel").is_file());
        let args: Vec<String> = plan_args(Path::new("run"), Path::new("v"), Path::new("models")).iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args[..4], ["-B", "-m", "engine.dubflow.docu", "plan"]);
        assert!(docu_plan_cancel("relative".into()).is_err());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn lists_only_documentary_runs_newest_first() {
        let base = std::env::temp_dir().join(format!("dubflow-docu-runs-{}", std::process::id()));
        let older = base.join("DubFlow Output").join(format!("{PREFIX}Cũ/run-000000000001"));
        let newer = base.join("DubFlow Output").join(format!("{PREFIX}Mới/run-000000000002"));
        for dir in [&older, &newer] {
            fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
            fs::write(dir.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[],"title":"x"}).to_string()).unwrap();
            std::thread::sleep(Duration::from_millis(30));
        }
        fs::create_dir_all(base.join("DubFlow Output/Other/run-000000000003/.dubflow-work")).unwrap();
        fs::write(base.join("DubFlow Output/Other/run-000000000003/.dubflow-work/spec.json"), b"{}").unwrap();
        let runs = docu_runs(vec![display_path(&base)]);
        let found: Vec<_> = runs.iter().filter(|run| run["dir"].as_str().is_some_and(|dir| dir.starts_with(&display_path(&base.canonicalize().unwrap())))).collect();
        assert_eq!(found.iter().map(|run| run["spec"]["title"].as_str()).collect::<Vec<_>>(), [Some("x"), Some("x")]);
        assert_eq!(found[0]["dir"], display_path(&newer.canonicalize().unwrap()));
        assert!(!found[0]["script"].as_bool().unwrap());
        fs::remove_dir_all(base).unwrap();
    }
}
