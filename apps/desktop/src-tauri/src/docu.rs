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
    // None: no subtitles; the video's speech is recognised once the run folder exists.
    transcript: Option<PathBuf>,
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
    Ok(ResolvedSource { id: String::new(), title: title.to_owned(), video: source_path, transcript: Some(transcript), origin_url, credit })
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
                let transcript = match source.subtitles.as_deref().filter(|value| !value.is_empty()) {
                    Some(value) => Some(absolute_file(value, &["srt"], Some(8 * 1024 * 1024))
                        .map_err(|_| fail(format!("Nguồn {}: chọn phụ đề SRT không quá 8 MB", index + 1)))?),
                    None => None,
                };
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

const IN_SOURCE_STORE: &str = "Không lưu phim tài liệu trong kho video nguồn của DubFlow; hãy chọn thư mục khác";

/// DubFlow's reserved `<data root>\source-media` store (#464); sources there stay read-only (ADR-0049).
fn source_store() -> Result<PathBuf, String> {
    #[cfg(test)]
    if let Some(store) = TEST_STORE.with(|store| store.borrow().clone()) { return Ok(store); }
    Ok(crate::data_root()?.join("source-media"))
}

#[cfg(test)]
thread_local! { static TEST_STORE: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) }; }

/// `path` (which need not exist yet) through the canonical form of its nearest
/// existing ancestor, the same resolution `within` uses; `None` when that fails.
fn resolved(path: &Path) -> Option<PathBuf> {
    let mut existing = path;
    let mut rest = Vec::new();
    while fs::symlink_metadata(existing).is_err() {
        rest.push(existing.file_name()?.to_os_string());
        existing = existing.parent()?;
    }
    // A link whose target is gone cannot be resolved: fail closed.
    let mut out = existing.canonicalize().ok()?;
    for name in rest.iter().rev() { out.push(name); }
    Some(out)
}

/// Windows paths compare without regard to case.
fn same_or_inside(path: &Path, root: &Path) -> bool {
    let key = |path: &Path| -> Vec<String> { path.components().map(|part| {
        let text = part.as_os_str().to_string_lossy().into_owned();
        if cfg!(windows) { text.to_lowercase() } else { text }
    }).collect() };
    key(path).starts_with(&key(root))
}

/// Refuse a documentary path that is, or resolves (through links, junctions
/// or a redirected parent) into, the reserved source store. Called right
/// before each filesystem write; a same-user process that swaps directories
/// between this check and the write is not excluded (TOCTOU).
fn outside_source_store(path: &Path, store: &Path) -> Result<(), String> {
    let (Some(candidate), Some(reserved)) = (resolved(path), resolved(store)) else { return Err(fail(IN_SOURCE_STORE)) };
    if same_or_inside(&candidate, &reserved) || crate::within(path, store) { return Err(fail(IN_SOURCE_STORE)); }
    Ok(())
}

fn guard_source_store(path: &Path) -> Result<(), String> {
    outside_source_store(path, &source_store()?)
}

/// A documentary run folder (the editor opens its `documentary.mp4`).
pub(crate) fn is_run(dir: &Path) -> bool {
    docu_dir(&display_path(dir)).is_ok()
}

fn docu_dir(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !path.is_absolute() || path.components().any(|component| matches!(component, std::path::Component::ParentDir)) {
        return Err(fail("Thư mục phim tài liệu không hợp lệ"));
    }
    let dir = path.canonicalize().map_err(|_| fail("Không tìm thấy thư mục phim tài liệu"))?;
    // Every later step (narration, plan, render, script check, cancel) passes
    // through here, so a run already inside the source store is refused too.
    guard_source_store(path)?;
    guard_source_store(&dir)?;
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

fn transcribe_args(video: &Path, out: &Path, root: &Path, models: &Path) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "transcribe".into(), "--video".into(), video.into(), "--out-dir".into(), out.into(),
        "--app-root".into(), root.join("app").into(), "--model-root".into(), models.into(),
        "--ffmpeg".into(), root.join("runtime/media/ffmpeg.exe").into()]
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
        guard_source_store(&path)?;
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
    // The final run path too: an outside root whose `DubFlow Output` is a
    // link or junction into the store must not slip through.
    let store = source_store()?;
    outside_source_store(&output, &store)?;
    fs::create_dir_all(output.parent().ok_or_else(|| fail("Không tạo được thư mục phim tài liệu"))?)
        .map_err(|_| fail("Không tạo được thư mục phim tài liệu"))?;
    outside_source_store(&output, &store)?;
    fs::create_dir(&output).map_err(|_| fail("Không tạo được phiên phim tài liệu mới"))?;
    crate::edit::record_run("docu", &output);
    let work = output.join(".dubflow-work");
    fs::create_dir(&work).map_err(|_| fail("Không tạo được thư mục làm việc phim tài liệu"))?;
    let mut sources = sources;
    for source in sources.iter_mut().filter(|source| source.transcript.is_none()) {
        let out = work.join("sources").join(&source.id);
        let root = version_root()?;
        let result = python_json(transcribe_args(&source.video, &out, &root, &control_root()?.join("models")), "bộ nhận dạng lời").await?;
        if result["state"] != "COMPLETED" { return Err(fail(script_error(&result))); }
        source.transcript = Some(out.join("transcript.json"));
    }
    let spec_sources: Vec<Value> = sources.iter().map(|source| json!({
        "id": source.id, "title": source.title, "video": display_path(&source.video),
        "transcript": source.transcript.as_deref().map(display_path),
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
    Ok(json!({ "dir": display_path(&dir), "spec": spec, "contexts": contexts, "script": script, "narration": narration_info(&dir), "visual": visual_info(&dir), "render": render_info(&dir),
        "capcut": crate::product::read_small_json(&dir.join(".dubflow-work/capcut.json"), 64 * 1024),
        "labels": crate::product::read_small_json(&dir.join(".dubflow-work/labels.json"), 256 * 1024) }))
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
const RENDER_JOB: Job = Job { work: ".dubflow-work/render", busy: "Bản nháp của dự án này đang được dựng",
    starting: "Đang khởi động dựng bản nháp…", cannot: "Không chạy được bộ dựng bản nháp",
    cancelled: "Đã hủy dựng bản nháp.", stopped: "Bộ dựng bản nháp đã dừng. Hãy thử lại." };
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

fn render_args(dir: &Path, root: &Path) -> Vec<OsString> {
    vec!["-B".into(), "-m".into(), "engine.dubflow.docu".into(), "render".into(), "--run-dir".into(), dir.into(),
        "--ffmpeg".into(), root.join("runtime/media/ffmpeg.exe").into(), "--ffprobe".into(), root.join("runtime/media/ffprobe.exe").into()]
}

/// The rough cut (phase 2b-2) and the last render's status.
fn render_info(dir: &Path) -> Value {
    let video = dir.join("documentary.mp4");
    json!({ "video": video.is_file().then(|| display_path(&video)),
        "status": crate::product::read_small_json(&dir.join(".dubflow-work/render/status.json"), 64 * 1024),
        "options": crate::product::read_small_json(&dir.join(".dubflow-work/render_options.json"), 16 * 1024) })
}

/// Render the visual plan and the narration into `documentary.mp4` in the background.
#[tauri::command]
pub fn docu_render(dir: String) -> Result<(), String> {
    let dir = docu_dir(&dir)?;
    if !dir.join("visual_plan.json").is_file() { return Err(fail("Hãy lập kế hoạch hình ảnh trước khi dựng bản nháp")); }
    let root = version_root()?;
    spawn_job(&dir, &RENDER_JOB, render_args(&dir, &root))
}

#[tauri::command]
pub fn docu_render_status(dir: String) -> Result<Value, String> {
    Ok(render_info(&docu_dir(&dir)?))
}

#[tauri::command]
pub fn docu_render_cancel(dir: String) -> Result<(), String> {
    cancel_job(&dir, &RENDER_JOB)
}

fn reframe_args(dir: &Path, preset: &str) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "reframe".into(), "--run-dir".into(), dir.into(), "--preset".into(), preset.into()]
}

/// Change the framing look after creation: the plan keeps its slices, hand edits and scans; only framing is redone (#477).
#[tauri::command]
pub async fn docu_reframe(dir: String, preset: String) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    if !["none", "letterbox_235", "overscan_108"].contains(&preset.as_str()) { return Err(fail("Kiểu khung hình không hợp lệ")); }
    for work in [".dubflow-work/visual", ".dubflow-work/render"] {
        if crate::product::read_small_json(&dir.join(work).join("status.json"), 64 * 1024).is_some_and(|status| status["state"] == "RUNNING") {
            return Err(fail("Đợi lập kế hoạch hoặc dựng bản nháp xong rồi hãy đổi khung hình"));
        }
    }
    let result = python_json(reframe_args(&dir, &preset), "bộ xử lý phim tài liệu").await?;
    if result["state"] != "COMPLETED" { return Err(fail(script_error(&result))); }
    Ok(result)
}

fn cover_args(dir: &Path, source: &str, band: &Value, logos: &Value) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "cover".into(), "--run-dir".into(), dir.into(), "--source".into(), source.into(),
        "--band".into(), band.to_string().into(), "--logos".into(), logos.to_string().into()]
}

/// One source's caption band (null: no cover) and logo boxes, set by hand in Chỉnh sửa (#520); the next render uses them.
#[tauri::command]
pub async fn docu_cover(dir: String, source: String, band: Option<[f64; 2]>, logos: Vec<[f64; 4]>) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    if source.is_empty() || source.len() > 32 || !source.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(fail("Nguồn không hợp lệ"));
    }
    if logos.len() > 16 { return Err(fail("Tối đa 16 vùng logo")); }
    for work in [".dubflow-work/visual", ".dubflow-work/render"] {
        if crate::product::read_small_json(&dir.join(work).join("status.json"), 64 * 1024).is_some_and(|status| status["state"] == "RUNNING") {
            return Err(fail("Đợi lập kế hoạch hoặc dựng phim xong rồi hãy đổi vùng che"));
        }
    }
    let band = band.map_or(Value::Null, |[top, bottom]| json!({"top": top, "bottom": bottom}));
    let result = python_json(cover_args(&dir, &source, &band, &json!(logos)), "bộ xử lý phim tài liệu").await?;
    if result["state"] != "COMPLETED" { return Err(fail(script_error(&result))); }
    Ok(result)
}

fn options_args(dir: &Path, graphics: bool, captions: bool, music: &str, music_path: Option<&Path>) -> Vec<OsString> {
    let mut args: Vec<OsString> = vec!["-m".into(), "engine.dubflow.docu".into(), "options".into(), "--run-dir".into(), dir.into(),
        "--graphics".into(), graphics.to_string().into(), "--captions".into(), captions.to_string().into(), "--music".into(), music.into()];
    if let Some(path) = music_path { args.extend(["--music-path".into(), path.into()]); }
    args
}

/// What the next rough cut draws (number cards, captions) and plays (music bed); saved in the project.
#[tauri::command]
pub async fn docu_render_options(dir: String, graphics: bool, captions: bool, music: String, music_path: Option<String>) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    if !["generated", "file", "none"].contains(&music.as_str()) { return Err(fail("Lựa chọn nhạc nền không hợp lệ")); }
    let path = match music_path.as_deref().filter(|value| !value.is_empty()) {
        Some(value) => Some(absolute_file(value, &["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"], Some(512 * 1024 * 1024))
            .map_err(|_| fail("Chọn file nhạc MP3, WAV, M4A, AAC, FLAC, OGG hoặc OPUS"))?),
        None => None,
    };
    let result = python_json(options_args(&dir, graphics, captions, &music, path.as_deref()), "bộ xử lý phim tài liệu").await?;
    if result["state"] != "COMPLETED" { return Err(fail(script_error(&result))); }
    Ok(result["options"].clone())
}

fn capcut_args(dir: &Path) -> Vec<OsString> {
    vec!["-m".into(), "engine.dubflow.docu".into(), "capcut".into(), "--run-dir".into(), dir.into()]
}

/// The rough cut as a CapCut draft, listed in CapCut's own projects (phase 4a); refused while CapCut runs.
#[tauri::command]
pub async fn docu_capcut(dir: String) -> Result<Value, String> {
    let dir = docu_dir(&dir)?;
    if !dir.join("documentary.mp4").is_file() { return Err(fail("Hãy dựng bản nháp trước khi xuất sang CapCut")); }
    let result = python_json(capcut_args(&dir), "bộ xuất CapCut").await?;
    if result["state"] != "COMPLETED" { return Err(fail(script_error(&result))); }
    Ok(result)
}

fn capcut_exe() -> Option<PathBuf> {
    let exe = PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join("CapCut/Apps/CapCut.exe");
    exe.is_file().then_some(exe)
}

/// How to connect Claude to this install's MCP server (phase 4b): the bundled
/// Python runs `engine.dubflow.mcp` with the app folder on its path.
fn mcp_connection(root: &Path) -> Value {
    let python = display_path(&root.join("runtime/python.exe"));
    let app = display_path(&root.join("app"));
    let desktop = json!({"mcpServers": {"dubflow": {"command": python, "args": ["-B", "-m", "engine.dubflow.mcp"], "env": {"PYTHONPATH": app}}}});
    json!({
        "python": python, "app": app,
        "claudeCode": format!("claude mcp add dubflow --scope user -e PYTHONPATH=\"{app}\" -- \"{python}\" -B -m engine.dubflow.mcp"),
        "claudeDesktop": serde_json::to_string_pretty(&desktop).unwrap_or_default(),
    })
}

#[tauri::command]
pub fn docu_mcp_connection() -> Result<Value, String> {
    Ok(mcp_connection(&version_root()?))
}

/// What an MCP client (Claude) is doing now and did last, written by the MCP server (engine/dubflow/mcp/activity.py).
#[tauri::command]
pub fn docu_mcp_activity() -> Result<Option<Value>, String> {
    Ok(crate::product::read_small_json(&control_root()?.join("control").join("mcp-activity.json"), 256 * 1024))
}

/// Start CapCut so the user can open the exported draft.
#[tauri::command]
pub fn docu_launch_capcut() -> Result<(), String> {
    let exe = capcut_exe().ok_or_else(|| fail("Không tìm thấy CapCut trên máy này"))?;
    Command::new(exe).spawn().map(|_| ()).map_err(|_| fail("Không mở được CapCut"))
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
        "music" => dialog.set_title("Chọn file nhạc nền").add_filter("Nhạc", &["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"]).pick_file(),
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
        // No subtitles: accepted; the speech is recognised when the run folder exists.
        let plain = json!({"kind":"file","video":display_path(&video),"title":"Plain"});
        let resolved = resolve_sources(&request(vec![plain.clone(), local_source(&video, &subtitles, "B")])).unwrap();
        assert!(resolved[0].transcript.is_none());
        assert!(resolved[1].transcript.is_some());
        let args: Vec<String> = transcribe_args(&video, Path::new("C:/run/src_01"), Path::new("C:/v"), Path::new("C:/m"))
            .iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(&args[..3], ["-m", "engine.dubflow.docu", "transcribe"]);
        assert!(args.contains(&"C:/m".to_owned()) && args.iter().any(|arg| arg.ends_with("app")));
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
        assert_eq!(display_path(source.transcript.as_deref().unwrap()), display_path(&job.canonicalize().unwrap().join(".dubflow-work").join("transcript.json")));
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
    fn options_command_passes_every_choice() {
        let args = options_args(Path::new(r"C:\run"), true, false, "file", Some(Path::new(r"C:\music.mp3")));
        let text: Vec<String> = args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(text, ["-m", "engine.dubflow.docu", "options", "--run-dir", r"C:\run", "--graphics", "true", "--captions", "false",
            "--music", "file", "--music-path", r"C:\music.mp3"]);
    }

    #[test]
    fn reframe_command_names_the_preset() {
        let args = reframe_args(Path::new(r"C:\run"), "letterbox_235");
        let text: Vec<String> = args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(text, ["-m", "engine.dubflow.docu", "reframe", "--run-dir", r"C:\run", "--preset", "letterbox_235"]);
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
        // The rough cut follows the plan.
        assert!(docu_render(display_path(&base)).is_err());
        fs::remove_file(dir.join("visual_plan.json")).unwrap();
        assert!(docu_render(display_path(&dir)).unwrap_err().contains("kế hoạch"));
        assert!(render_info(&dir)["video"].is_null());
        fs::write(dir.join("documentary.mp4"), b"mp4").unwrap();
        assert!(docu_render_status(display_path(&dir)).unwrap()["video"].as_str().unwrap().ends_with("documentary.mp4"));
        docu_render_cancel(display_path(&dir)).unwrap();
        assert!(dir.join(".dubflow-work/render/cancel").is_file());
        let args: Vec<String> = render_args(Path::new("run"), Path::new("v")).iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args[..4], ["-B", "-m", "engine.dubflow.docu", "render"]);
        let connection = mcp_connection(Path::new("D:/Tools/DubFlow/versions/9.9.9"));
        assert!(connection["claudeCode"].as_str().unwrap().starts_with("claude mcp add dubflow --scope user -e PYTHONPATH=\""));
        assert!(connection["claudeCode"].as_str().unwrap().ends_with("python.exe\" -B -m engine.dubflow.mcp"));
        let desktop: Value = serde_json::from_str(connection["claudeDesktop"].as_str().unwrap()).unwrap();
        assert_eq!(desktop["mcpServers"]["dubflow"]["args"], json!(["-B", "-m", "engine.dubflow.mcp"]));
        assert!(desktop["mcpServers"]["dubflow"]["env"]["PYTHONPATH"].as_str().unwrap().ends_with("app"));
        let args: Vec<String> = capcut_args(Path::new("run")).iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-m", "engine.dubflow.docu", "capcut", "--run-dir", "run"]);
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

    /// Points `source_store()` at a test store on this thread until dropped.
    struct TestStore;
    impl TestStore {
        fn set(store: &Path) -> Self { TEST_STORE.with(|cell| *cell.borrow_mut() = Some(store.to_path_buf())); TestStore }
    }
    impl Drop for TestStore {
        fn drop(&mut self) { TEST_STORE.with(|cell| *cell.borrow_mut() = None); }
    }

    /// Every entry below `root` (links not followed): kind, size, mtime and content hash.
    fn snapshot(root: &Path) -> Vec<(String, bool, u64, std::time::SystemTime, String)> {
        let mut out = Vec::new();
        let mut stack = vec![root.to_path_buf()];
        while let Some(dir) = stack.pop() {
            for entry in fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                let meta = fs::symlink_metadata(&path).unwrap();
                let hash = if meta.is_file() { format!("{:x}", Sha256::digest(fs::read(&path).unwrap())) } else { String::new() };
                if meta.is_dir() && !meta.file_type().is_symlink() { stack.push(path.clone()); }
                out.push((display_path(path.strip_prefix(root).unwrap()), meta.is_dir(), meta.len(), meta.modified().unwrap(), hash));
            }
        }
        out.sort();
        out
    }

    /// A directory link: an NTFS junction (reparse point) on Windows, a symlink elsewhere.
    fn link_dir(target: &Path, link: &Path) {
        #[cfg(windows)]
        {
            let status = Command::new("cmd").args(["/C", "mklink", "/J"]).arg(link).arg(target)
                .stdout(Stdio::null()).status().unwrap();
            assert!(status.success(), "mklink /J failed");
            assert_eq!(fs::read_link(link).ok().and_then(|target| target.canonicalize().ok()), target.canonicalize().ok());
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, link).unwrap();
    }

    /// A store holding one source file, and two local sources outside it.
    fn store_fixture(name: &str) -> (PathBuf, PathBuf, Vec<Value>) {
        let base = std::env::temp_dir().join(format!("dubflow-docu-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let store = base.join("data").join("source-media");
        fs::create_dir_all(store.join("nested")).unwrap();
        fs::write(store.join("nested/source.mp4"), b"reserved source bytes").unwrap();
        let (video, subtitles) = file_fixture(&base.join("sources"));
        (base, store, vec![local_source(&video, &subtitles, "Nguồn một"), local_source(&video, &subtitles, "Nguồn hai")])
    }

    fn create_in(root: &Path, sources: &[Value]) -> Result<Value, String> {
        let value = json!({"title":"Công trình lớn","topic":"", "targetMinutes":5, "cinematicFramingPreset":"none",
            "burnEndCredits":false, "sources":sources, "outputRoot":display_path(root)});
        tauri::async_runtime::block_on(docu_create(serde_json::from_value(value).unwrap()))
    }

    fn refused<T: std::fmt::Debug>(result: Result<T, String>) {
        assert!(result.unwrap_err().contains("kho video nguồn"));
    }

    #[test]
    fn source_store_refuses_new_documentary_output() {
        let (base, store, sources) = store_fixture("store-create");
        let _store = TestStore::set(&store);
        let before = snapshot(&store);
        let mut roots = vec![store.clone(), store.join("nested")];
        // The same folder spelled differently still resolves into the store.
        roots.push(if cfg!(windows) { base.join("data").join("SOURCE-MEDIA").join("Nested") } else { store.join(".").join("nested") });
        for root in &roots {
            let error = create_in(root, &sources).unwrap_err();
            assert!(error.contains("kho video nguồn"), "{root:?}: {error}");
        }
        assert_eq!(snapshot(&store), before);
        // An ordinary folder outside the store still gets its run and spec.
        let outside = base.join("out");
        fs::create_dir_all(&outside).unwrap();
        let _ = create_in(&outside, &sources);
        let runs: Vec<_> = fs::read_dir(outside.join("DubFlow Output")).unwrap().flatten()
            .flat_map(|title| fs::read_dir(title.path()).unwrap().flatten().map(|run| run.path())).collect();
        assert_eq!(runs.len(), 1);
        assert!(runs[0].join(".dubflow-work/spec.json").is_file());
        assert!(is_run(&runs[0]));
        assert_eq!(snapshot(&store), before);
        // A path that cannot be resolved fails closed.
        assert!(outside_source_store(Path::new("relative"), &store).is_err());
        assert!(outside_source_store(&outside, &store).is_ok());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn junction_aliases_into_source_store_are_refused() {
        let (base, store, sources) = store_fixture("store-junction");
        let _store = TestStore::set(&store);
        fs::create_dir_all(store.join("redirect")).unwrap();
        let before = snapshot(&store);
        // The chosen root is itself a junction into the store.
        let alias = base.join("alias");
        link_dir(&store, &alias);
        refused(create_in(&alias, &sources));
        refused(create_in(&alias.join("nested"), &sources));
        // An outside root whose `DubFlow Output` is redirected into the store.
        let outside = base.join("out");
        fs::create_dir_all(&outside).unwrap();
        link_dir(&store.join("redirect"), &outside.join("DubFlow Output"));
        refused(create_in(&outside, &sources));
        assert_eq!(snapshot(&store), before);
        // A run reached through the junction is still a run inside the store.
        let run = store.join("redirect").join(format!("{PREFIX}Cũ")).join("run-0000000000aa");
        fs::create_dir_all(run.join(".dubflow-work")).unwrap();
        fs::write(run.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        let through = outside.join("DubFlow Output").join(format!("{PREFIX}Cũ")).join("run-0000000000aa");
        refused(docu_dir(&display_path(&through)));
        refused(docu_render_cancel(display_path(&through)));
        assert!(!run.join(".dubflow-work/render").exists());
        // Remove the links first so cleanup never follows them into the store.
        fs::remove_dir(&alias).unwrap();
        fs::remove_dir(outside.join("DubFlow Output")).unwrap();
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn existing_run_inside_source_store_is_refused_before_any_write() {
        let (base, store, _) = store_fixture("store-existing");
        let _store = TestStore::set(&store);
        let run = store.join("DubFlow Output").join(format!("{PREFIX}Cũ")).join("run-0000000000bb");
        fs::create_dir_all(run.join(".dubflow-work")).unwrap();
        fs::write(run.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        for name in ["documentary_script.json", "master_audio_timeline.json", "visual_plan.json"] {
            fs::write(run.join(name), b"{}").unwrap();
        }
        fs::write(run.join("documentary.mp4"), b"mp4").unwrap();
        let before = snapshot(&store);
        let dir = display_path(&run);
        refused(docu_narrate(dir.clone(), "am_michael".into(), 1.0));
        refused(docu_plan(dir.clone()));
        refused(docu_render(dir.clone()));
        refused(docu_narration_cancel(dir.clone()));
        refused(docu_plan_cancel(dir.clone()));
        refused(docu_render_cancel(dir.clone()));
        refused(tauri::async_runtime::block_on(docu_check_script(dir.clone(), json!({"schema_version":1}))));
        refused(tauri::async_runtime::block_on(docu_capcut(dir.clone())));
        refused(docu_open(dir.clone()));
        refused(narration_audio(&display_path(&run.join("narration.wav"))));
        assert!(!is_run(&run));
        assert!(docu_runs(vec![display_path(&store)]).is_empty());
        assert_eq!(snapshot(&store), before);
        // The identical run outside the store still works.
        let outside = base.join("DubFlow Output").join(format!("{PREFIX}Cũ")).join("run-0000000000bb");
        fs::create_dir_all(outside.join(".dubflow-work")).unwrap();
        fs::write(outside.join(".dubflow-work/spec.json"), json!({"schema_version":1,"sources":[]}).to_string()).unwrap();
        docu_render_cancel(display_path(&outside)).unwrap();
        assert!(outside.join(".dubflow-work/render/cancel").is_file());
        assert_eq!(snapshot(&store), before);
        fs::remove_dir_all(base).unwrap();
    }
}
