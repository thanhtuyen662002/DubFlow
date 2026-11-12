//! Tổng hợp từ video gốc (spec `schema_version: 2`, ADR-0053) on the desktop (Issue #728).
//!
//! The legacy flow (`compilation.rs`: finished dubbing exports, spec v1) is untouched. Here the user's own source
//! videos go to `python -m engine.dubflow.compilation` as a v2 programme. The host checks every field, writes the
//! spec in a new run folder below "DubFlow Output" and never writes next to a source. A part's review panel, the
//! re-render in place and the final export all go through the same checked run folder: every folder from
//! "DubFlow Output" down to the run and its work folder is a plain folder (never a link or junction), and the run
//! resolves inside "DubFlow Output" by path components after canonicalising, never by a string prefix.

use serde::Deserialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::compilation::text;
use crate::{control_root, display_path, fail, load_voice_catalog, version_root, AiTranslation, VoiceCatalog, CREATE_NO_WINDOW};

/// The formats the v2 engine reads (`program.VIDEO_SUFFIXES`); anything else is refused by name.
pub(crate) const SOURCE_EXTENSIONS: [&str; 6] = ["mp4", "mov", "mkv", "webm", "m4v", "avi"];
const MUSIC_EXTENSIONS: [&str; 6] = ["mp3", "m4a", "aac", "wav", "ogg", "flac"];
const LAYOUTS: [&str; 2] = ["bang-thong-tin", "toan-man-hinh"];
const KINDS: [&str; 2] = ["top_n", "list"];
const RIGHTS: [&str; 4] = ["owned", "licensed", "permitted", "unknown"];
const KEEP_AUDIO: [&str; 3] = ["auto", "on", "off"];
const MAX_ITEMS: usize = 30;
const MAX_SOURCES: usize = 8;
const MAX_NARRATION: usize = 1500;
/// The engine refuses a larger spec (`job.load_spec`).
const SPEC_LIMIT: u64 = 256 * 1024;
const STATUS_LIMIT: u64 = 256 * 1024;
const PROGRAM_LIMIT: u64 = 4 * 1024 * 1024;
const FINAL: &str = "final.mp4";
/// What the last final export was made from (P2-6): its hash, its revision and the draft it copied.
const FINAL_RECORD: &str = "final.json";
const FINAL_RECORD_LIMIT: u64 = 64 * 1024;
const FINISHED: [&str; 3] = ["COMPLETED", "FAILED", "CANCELLED"];
const MAX_TIME_MS: u64 = 24 * 3600 * 1000;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RawSource {
    video: String,
    start_ms: Option<u64>,
    end_ms: Option<u64>,
    notes: Option<String>,
    rights: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RawItem {
    /// `i<n>`, kept across edits so a part's review decisions stay with it; a new item has none.
    item_id: Option<String>,
    title: String,
    rank: Option<u32>,
    narration: String,
    notes: String,
    sources: Vec<RawSource>,
    keep_source_audio: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RawRequest {
    title: String,
    layout: String,
    voice_id: String,
    speed: f64,
    music: Option<String>,
    music_volume: f64,
    kind: String,
    /// "user" (the user's ranks, never reordered) or "suggested" (the AI suggests; applied only with `apply_suggestion`).
    ranking: String,
    apply_suggestion: bool,
    hook: String,
    intro: String,
    outro: String,
    items: Vec<RawItem>,
    /// Tắt / Nhẹ / Cân bằng / Mạnh.
    mastering: String,
    /// The provider that may look at keyframes, draft narration and suggest a ranking (never the key).
    ai: Option<AiTranslation>,
    draft_narration: bool,
    output_root: Option<String>,
    cpu_only: bool,
}

/// A source video: an absolute path to a plain local file (not a link) of a supported format.
fn source_file(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    let name = path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    if !path.is_absolute() || path.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)) {
        return Err(fail(format!("Đường dẫn video nguồn không hợp lệ: {name}")));
    }
    if !path.extension().and_then(|ext| ext.to_str())
        .is_some_and(|ext| SOURCE_EXTENSIONS.iter().any(|known| known.eq_ignore_ascii_case(ext))) {
        return Err(fail(format!("Định dạng chưa hỗ trợ: {name} (dùng MP4, MOV, MKV, WEBM, M4V hoặc AVI)")));
    }
    let metadata = fs::symlink_metadata(&path).map_err(|_| fail(format!("Không tìm thấy video nguồn: {name}")))?;
    if crate::edit::is_link(&metadata) || !metadata.is_file() {
        return Err(fail(format!("Video nguồn phải là tệp thật trên máy, không phải lối tắt: {name}")));
    }
    if is_below_output(&path) {
        return Err(fail(format!("Video nguồn nằm trong thư mục làm việc của DubFlow: {name}")));
    }
    Ok(path)
}

/// Whether a path is inside a DubFlow work folder (`.dubflow-work`): those files are the app's, not sources.
fn is_below_output(path: &Path) -> bool {
    path.components().any(|part| matches!(part, Component::Normal(name) if name == ".dubflow-work"))
}

fn music_file(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    let metadata = fs::symlink_metadata(&path).ok();
    if !path.is_absolute() || metadata.as_ref().is_none_or(|m| crate::edit::is_link(m) || !m.is_file())
        || !path.extension().and_then(|ext| ext.to_str())
            .is_some_and(|ext| MUSIC_EXTENSIONS.iter().any(|known| known.eq_ignore_ascii_case(ext))) {
        return Err(fail("Không mở được nhạc nền; chọn MP3, M4A, AAC, WAV, OGG hoặc FLAC"));
    }
    Ok(path)
}

/// The request as the engine's v2 spec, every field checked; the sources as checked paths.
fn build_spec(request: &RawRequest, catalog: &VoiceCatalog) -> Result<Value, String> {
    if !text(&request.title, 120, true, false) {
        return Err(fail("Nhập chủ đề, tối đa 120 ký tự"));
    }
    if !text(&request.hook, 300, false, true) || !text(&request.intro, 800, false, true) || !text(&request.outro, 800, false, true) {
        return Err(fail("Câu mở màn tối đa 300 ký tự; mở đầu và kết thúc tối đa 800 ký tự, có nội dung để đọc"));
    }
    if !LAYOUTS.contains(&request.layout.as_str()) || !(1.0..=1.3).contains(&request.speed) || !(0.0..=1.0).contains(&request.music_volume) {
        return Err(fail("Bố cục, tốc độ đọc hoặc âm lượng không hợp lệ"));
    }
    if !KINDS.contains(&request.kind.as_str()) || !["user", "suggested"].contains(&request.ranking.as_str())
        || (request.apply_suggestion && request.ranking != "suggested") {
        return Err(fail("Kiểu chương trình hoặc cách xếp hạng không hợp lệ"));
    }
    if !crate::mastering::LEVELS.contains(&request.mastering.as_str()) {
        return Err(fail("Mức tối ưu hậu kỳ không hợp lệ"));
    }
    if !catalog.voices.iter().any(|voice| voice.voice_id == request.voice_id) {
        return Err(fail("Giọng đã chọn không có trong phiên bản này"));
    }
    if !(2..=MAX_ITEMS).contains(&request.items.len()) {
        return Err(fail(format!("Cần từ 2 đến {MAX_ITEMS} mục")));
    }
    if (request.ranking == "suggested" || request.draft_narration) && request.ai.is_none() {
        return Err(fail("Gợi ý thứ hạng và AI viết lời bình cần nhà cung cấp AI trong Cài đặt"));
    }
    let music = request.music.as_deref().map(music_file).transpose()?;
    let mut ranks = Vec::new();
    let mut items = Vec::new();
    let mut ids: Vec<String> = Vec::new();
    for item in &request.items {
        if let Some(id) = &item.item_id {
            if !id.starts_with('i') || !is_part(id) || ids.contains(id) {
                return Err(fail("Mã mục không hợp lệ hoặc bị trùng"));
            }
            ids.push(id.clone());
        }
    }
    let mut next = 1;
    for (index, item) in request.items.iter().enumerate() {
        let number = index + 1;
        let label = |what: &str| fail(format!("Mục {number}: {what}"));
        if !text(&item.title, 80, true, false) {
            return Err(label("nhập tiêu đề, tối đa 80 ký tự"));
        }
        if !text(&item.narration, MAX_NARRATION, false, true) || !text(&item.notes, 600, false, false) {
            return Err(label("lời bình tối đa 1500 ký tự, ghi chú tối đa 600 ký tự"));
        }
        if item.narration.trim().is_empty() && !(request.draft_narration && request.ai.is_some()) {
            return Err(label("chưa có lời bình (viết lời bình, hoặc bật “AI viết lời bình”)"));
        }
        if let Some(rank) = item.rank {
            if !(1..=1000).contains(&rank) || ranks.contains(&rank) {
                return Err(label("thứ hạng phải là số nguyên dương, không trùng"));
            }
            ranks.push(rank);
        }
        let keep = item.keep_source_audio.as_deref().unwrap_or("auto");
        if !KEEP_AUDIO.contains(&keep) {
            return Err(label("cách giữ âm thanh nguồn không hợp lệ"));
        }
        if !(1..=MAX_SOURCES).contains(&item.sources.len()) {
            return Err(label(&format!("cần từ 1 đến {MAX_SOURCES} video nguồn")));
        }
        let mut sources = Vec::new();
        for (k, source) in item.sources.iter().enumerate() {
            let video = source_file(&source.video).map_err(|error| label(&format!("nguồn {}: {error}", k + 1)))?;
            let notes = source.notes.as_deref().unwrap_or("");
            let rights = source.rights.as_deref().unwrap_or("unknown");
            if !text(notes, 400, false, false) || !RIGHTS.contains(&rights)
                || [source.start_ms, source.end_ms].iter().flatten().any(|&ms| ms > MAX_TIME_MS)
                || source.end_ms.is_some_and(|end| end <= source.start_ms.unwrap_or(0)) {
                return Err(label(&format!("nguồn {}: ghi chú, quyền sử dụng hoặc mốc cắt không hợp lệ", k + 1)));
            }
            sources.push(json!({"video": display_path(&video), "start_ms": source.start_ms, "end_ms": source.end_ms,
                                "notes": notes.trim(), "rights": rights}));
        }
        let item_id = match &item.item_id {
            Some(id) => id.clone(),
            None => {
                while ids.contains(&format!("i{next}")) { next += 1; }
                ids.push(format!("i{next}"));
                format!("i{next}")
            }
        };
        items.push(json!({"item_id": item_id, "rank": item.rank, "title": item.title.trim(),
                          "narration": item.narration.trim(), "notes": item.notes.trim(), "sources": sources,
                          "keep_source_audio": keep}));
    }
    let ai = request.ai.as_ref().map(crate::mastering::vision_route).transpose()?;
    let mut editorial = json!({"draft_narration": request.draft_narration && ai.is_some()});
    if let Some(route) = ai {
        editorial["ai"] = route;
    }
    let spec = json!({
        "schema_version": 2, "title": request.title.trim(), "layout": request.layout, "voice_id": request.voice_id,
        "speed": request.speed, "music": music.as_deref().map(display_path), "music_volume": request.music_volume,
        "program": {"kind": request.kind, "ranking": request.ranking, "apply_suggestion": request.apply_suggestion},
        "hook": request.hook.trim(), "intro": request.intro.trim(), "outro": request.outro.trim(), "items": items,
        // the level the user picked, kept as picked; the engine plans Tắt as Nhẹ (a raw programme is always planned)
        "mastering": {"schema_version": 1, "level": request.mastering, "style": "entertainment"},
        "editorial": editorial,
    });
    if serde_json::to_vec(&spec).map_or(true, |bytes| bytes.len() as u64 > SPEC_LIMIT) {
        return Err(fail("Yêu cầu tổng hợp quá lớn; hãy rút ngắn lời bình hoặc bớt mục"));
    }
    Ok(spec)
}

/// A raw programme's run folder: `…\DubFlow Output\Tong hop - <title>\run-<12 hex>`, every folder from
/// "DubFlow Output" down to the run and its work folder plain, resolving inside "DubFlow Output", with a v2 spec.
pub(crate) fn raw_dir(value: &str) -> Result<PathBuf, String> {
    let bad = || fail("Thư mục tổng hợp không hợp lệ");
    let dir = PathBuf::from(value);
    if !dir.is_absolute() || dir.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)) {
        return Err(bad());
    }
    let run = dir.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    let title = dir.parent().and_then(Path::file_name).and_then(|name| name.to_str()).unwrap_or_default();
    let root = dir.parent().and_then(Path::parent).ok_or_else(bad)?;
    if run.len() != 16 || !run.starts_with("run-") || !run[4..].bytes().all(|b| b.is_ascii_hexdigit())
        || !title.starts_with("Tong hop - ") || root.file_name().is_none_or(|name| name != "DubFlow Output") {
        return Err(bad());
    }
    let work = dir.join(".dubflow-work");
    if ![root, dir.parent().unwrap_or(root), dir.as_path(), work.as_path()].into_iter().all(crate::product::plain_dir) {
        return Err(bad());
    }
    let (real_root, real_dir) = (root.canonicalize().map_err(|_| bad())?, dir.canonicalize().map_err(|_| bad())?);
    // component containment: the run is two levels below the real "DubFlow Output"
    if real_dir.parent().and_then(Path::parent) != Some(real_root.as_path()) {
        return Err(bad());
    }
    Ok(dir)
}

/// The run's request; a run whose spec is not v2 is refused with a reason a person can act on.
fn read_spec(dir: &Path) -> Result<Value, String> {
    let spec = crate::mastering::read_plain_json(&dir.join(".dubflow-work").join("spec.json"), SPEC_LIMIT)?
        .filter(Value::is_object).ok_or_else(|| fail("Không đọc được yêu cầu của dự án tổng hợp này"))?;
    match spec["schema_version"].as_u64() {
        Some(2) => Ok(spec),
        Some(1) => Err(fail("Dự án này là Tổng hợp từ video đã lồng tiếng (phiên bản 1); mở ở chế độ đó")),
        Some(other) => Err(fail(format!("Dự án được tạo bằng phiên bản DubFlow mới hơn (định dạng {other}); hãy cập nhật DubFlow để mở"))),
        None => Err(fail("Yêu cầu của dự án tổng hợp này không có phiên bản; không mở được")),
    }
}

fn write_replace(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let temp = path.with_extension("json.tmp");
    if fs::symlink_metadata(&temp).is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
        return Err(std::io::Error::other("temp file is a link"));
    }
    fs::write(&temp, bytes)?;
    crate::engine_run::replace_retrying(&temp, path)
}

fn write_status(work: &Path, state: &str, message: &str) -> Result<(), String> {
    let value = json!({"schema_version": 1, "state": state, "done": 0, "total": 0, "message": message, "path": null});
    write_replace(&work.join("status.json"), &serde_json::to_vec(&value).unwrap_or_default())
        .map_err(|_| fail("Không ghi được trạng thái tổng hợp"))
}

/// The AI key of the spec's provider, for the child's environment only (never written, logged or returned).
fn job_key(spec: &Value) -> Result<Option<String>, String> {
    match spec["editorial"]["ai"]["provider"].as_str() {
        Some(provider) => Ok(crate::ai_keys::job_key(provider)?.filter(|key| !key.trim().is_empty())),
        None => Ok(None),
    }
}

/// `python -m engine.dubflow.compilation` on the run's spec; a watcher marks the run failed if it dies silently.
fn spawn(dir: &Path, cpu_only: bool, key: Option<String>) -> Result<(), String> {
    let root = version_root()?;
    let work = dir.join(".dubflow-work");
    let media = root.join("runtime").join("media");
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.compilation", "--spec"]).arg(work.join("spec.json"))
        .arg("--output-dir").arg(dir).arg("--app-root").arg(root.join("app"))
        .arg("--model-root").arg(control_root()?.join("models")).arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .env("PYTHONUTF8", "1").env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::null());
    let (stdout, stderr) = crate::engine_run::engine_output(&work);
    command.stdout(stdout).stderr(stderr);
    if cpu_only {
        command.arg("--cpu-only");
    }
    if let Some(key) = key {
        command.env(crate::ai_keys::JOB_KEY_ENV, key);
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| fail("Không chạy được bộ tổng hợp video"))?;
    std::thread::spawn(move || {
        let exited_ok = child.wait().is_ok_and(|status| status.success());
        crate::engine_run::settle_after_exit(&work, exited_ok, "Bộ tổng hợp đã dừng. Hãy thử lại.");
    });
    Ok(())
}

/// Start a raw programme in a new run folder; returns the folder.
#[tauri::command]
pub fn compilation_raw_render(request: RawRequest) -> Result<String, String> {
    let root = version_root()?;
    let spec = build_spec(&request, &load_voice_catalog(&root)?)?;
    let first = spec["items"][0]["sources"][0]["video"].as_str().map(PathBuf::from).ok_or_else(|| fail("Thiếu video nguồn"))?;
    let base = match &request.output_root {
        Some(value) => {
            let path = PathBuf::from(value);
            if !path.is_absolute() || !crate::product::plain_dir(&path) {
                return Err(fail("Hãy chọn lại thư mục lưu kết quả"));
            }
            path
        }
        None => first.parent().map(Path::to_path_buf).ok_or_else(|| fail("Không tìm được thư mục lưu"))?,
    };
    let started = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let output = crate::compilation::compilation_output(&base, &request.title, &format!("{started}:{}:raw:{}", std::process::id(), request.title));
    fs::create_dir_all(output.parent().expect("title folder")).map_err(|_| fail("Không tạo được thư mục tổng hợp"))?;
    fs::create_dir(&output).map_err(|_| fail("Không tạo được phiên tổng hợp mới"))?;
    let work = output.join(".dubflow-work");
    fs::create_dir(&work).map_err(|_| fail("Không tạo được thư mục làm việc"))?;
    let dir = raw_dir(&display_path(&output))
        .map_err(|_| fail("Thư mục lưu kết quả không an toàn (có lối tắt); hãy chọn thư mục khác"))?;
    crate::edit::record_run("compilation", &dir);
    fs::write(work.join("spec.json"), serde_json::to_vec(&spec).map_err(|_| fail("Yêu cầu tổng hợp không hợp lệ"))?)
        .map_err(|_| fail("Không ghi được yêu cầu tổng hợp"))?;
    write_status(&work, "RUNNING", "Đang khởi động tổng hợp…")?;
    let key = job_key(&spec)?;
    if let Err(error) = spawn(&dir, request.cpu_only, key) {
        let _ = write_status(&work, "FAILED", "Không chạy được bộ tổng hợp video");
        return Err(error);
    }
    Ok(display_path(&dir))
}

/// Make the programme again in the same run: the engine reuses every part whose inputs (narration, footage,
/// review decisions) did not change. With `request`, the edited programme replaces the run's spec first (the
/// previous one is kept as `spec-previous.json`).
#[tauri::command]
pub fn compilation_raw_rerender(output_dir: String, request: Option<RawRequest>, cpu_only: bool) -> Result<(), String> {
    let dir = raw_dir(&output_dir)?;
    let work = dir.join(".dubflow-work");
    let before = read_spec(&dir)?;
    let status = crate::mastering::read_plain_json(&work.join("status.json"), STATUS_LIMIT)?.unwrap_or(Value::Null);
    if status["state"] == "RUNNING" {
        return Err(fail("Chương trình đang được dựng; hãy chờ xong rồi dựng lại"));
    }
    if !status["state"].as_str().is_some_and(|state| FINISHED.contains(&state)) {
        return Err(fail("Trạng thái của dự án không rõ; không dựng lại được"));
    }
    let spec = match &request {
        Some(request) => build_spec(request, &load_voice_catalog(&version_root()?)?)?,
        None => before.clone(),
    };
    let key = job_key(&spec)?;
    if spec != before {
        write_replace(&work.join("spec-previous.json"), &serde_json::to_vec(&before).unwrap_or_default())
            .map_err(|_| fail("Không lưu được yêu cầu cũ"))?;
        write_replace(&work.join("spec.json"), &serde_json::to_vec(&spec).unwrap_or_default())
            .map_err(|_| fail("Không ghi được yêu cầu tổng hợp"))?;
    }
    let status_bytes = serde_json::to_vec(&status).unwrap_or_default();
    write_status(&work, "RUNNING", "Đang dựng lại các phần đã đổi…")?;
    match fs::remove_file(work.join("cancel")) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(fail("Không bỏ được yêu cầu hủy cũ")),
    }
    if let Err(error) = spawn(&dir, cpu_only, key) {
        let _ = write_replace(&work.join("status.json"), &status_bytes);
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
pub fn compilation_raw_cancel(output_dir: String) -> Result<(), String> {
    let dir = raw_dir(&output_dir)?;
    let cancel = dir.join(".dubflow-work").join("cancel");
    // never written through a link planted in the work folder
    if fs::symlink_metadata(&cancel).is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
        return Err(fail("Không hủy được tổng hợp (tệp hủy không an toàn)"));
    }
    fs::write(cancel, b"").map_err(|_| fail("Không hủy được tổng hợp"))
}

/// Everything the page shows of a raw run: its request, status, the programme the engine made (parts, ranking
/// and its suggestion, review per part, rendered and reused parts), the draft video and the final export.
#[tauri::command]
pub fn compilation_raw_open(output_dir: String) -> Result<Value, String> {
    let dir = raw_dir(&output_dir)?;
    let work = dir.join(".dubflow-work");
    let spec = read_spec(&dir)?;
    let status = crate::mastering::read_plain_json(&work.join("status.json"), STATUS_LIMIT).ok().flatten();
    let program = crate::mastering::read_plain_json(&work.join("program.json"), PROGRAM_LIMIT).ok().flatten()
        .filter(|value| value["kind"] == "compilation_program");
    let file = |name: &str| {
        let path = dir.join(name);
        fs::symlink_metadata(&path).is_ok_and(|m| m.is_file() && !crate::edit::is_link(&m)).then(|| display_path(&path))
    };
    let final_path = file(FINAL);
    let final_state = final_path.as_ref().map(|path| final_state(&dir, path, status.as_ref()));
    Ok(json!({"dir": display_path(&dir), "spec": spec, "status": status, "program": program,
              "video": file("compilation.mp4"), "final": final_path, "final_state": final_state}))
}

/// The draft's identity without reading it: length and modification time (ns). A re-render replaces the draft, so
/// either changes; the final export records it and compares it on every open.
fn draft_identity(dir: &Path) -> Option<(u64, u128)> {
    let metadata = fs::symlink_metadata(dir.join("compilation.mp4")).ok().filter(|m| m.is_file() && !crate::edit::is_link(m))?;
    let modified = metadata.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos();
    Some((metadata.len(), modified))
}

fn file_sha256(path: &Path) -> std::io::Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 { break; }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// Whether `final.mp4` is the current programme (P2-6): stale when the draft was rendered again after the export,
/// when a render is running, or when no record says what it was made from (an export before the record).
fn final_state(dir: &Path, path: &str, status: Option<&Value>) -> Value {
    let record = crate::mastering::read_plain_json(&dir.join(".dubflow-work").join(FINAL_RECORD), FINAL_RECORD_LIMIT)
        .ok().flatten().filter(|value| value["kind"] == "compilation_final");
    let running = status.is_some_and(|value| value["state"] == "RUNNING");
    let Some(record) = record else {
        return json!({"path": path, "stale": true, "reason": "unknown", "revision": null, "sha256": null, "exported_at_ms": null});
    };
    let same = draft_identity(dir).is_some_and(|(len, modified)| {
        record["draft"]["bytes"].as_u64() == Some(len) && record["draft"]["modified_ns"].as_str() == Some(modified.to_string().as_str())
    });
    let reason = if running { Some("rendering") } else if !same { Some("rerendered") } else { None };
    json!({"path": path, "stale": reason.is_some(), "reason": reason, "revision": record["revision"], "sha256": record["sha256"],
           "exported_at_ms": record["exported_at_ms"]})
}

/// The parts of the run's spec, in programme order: hook, intro, items, outro (the ids the engine uses).
fn parts_of(spec: &Value) -> Vec<String> {
    let has = |key: &str| spec[key].as_str().is_some_and(|text| !text.trim().is_empty());
    let mut parts = Vec::new();
    if has("hook") { parts.push("hook".to_owned()); }
    if has("intro") { parts.push("intro".to_owned()); }
    parts.extend(spec["items"].as_array().into_iter().flatten().filter_map(|item| item["item_id"].as_str().map(str::to_owned)));
    if has("outro") { parts.push("outro".to_owned()); }
    parts
}

/// A part id of a desktop programme: hook, intro, outro or `i<n>` (the host writes item ids `i1`…`i30`).
fn is_part(value: &str) -> bool {
    matches!(value, "hook" | "intro" | "outro")
        || value.strip_prefix('i').is_some_and(|n| crate::mastering::digits(n, 2) && !n.starts_with('0'))
}

/// A sentence of a part: `<part>.u<n>`.
fn is_unit(value: &str) -> bool {
    value.split_once(".u").is_some_and(|(part, n)| is_part(part) && crate::mastering::digits(n, 3))
}

/// A shot of a programme source: `i<n>-s<k>c<n>`, or `…f<n>` for a part of a long shot.
fn is_shot(value: &str) -> bool {
    let Some((source, number)) = value.split_once('c') else { return false };
    let Some((item, k)) = source.split_once("-s") else { return false };
    item.starts_with('i') && is_part(item) && crate::mastering::digits(k, 1) && crate::mastering::shot_number(number)
}

fn checked_part(dir: &Path, part: &str) -> Result<String, String> {
    if !is_part(part) || !parts_of(&read_spec(dir)?).iter().any(|known| known == part) {
        return Err(fail("Phần không hợp lệ"));
    }
    Ok(part.to_owned())
}

/// One action of the review panel of a part of a raw run (the Phase 5 panel); returns the part's view after it.
#[allow(clippy::too_many_arguments)]
#[tauri::command]
pub async fn compilation_review(output_dir: String, part: String, action: String, unit: Option<String>, what: Option<String>,
                                ids: Option<Vec<String>>, warning: Option<String>, value: Option<bool>) -> Result<Value, String> {
    let dir = raw_dir(&output_dir)?;
    let part = checked_part(&dir, &part)?;
    if let Some(unit) = &unit {
        if unit.split_once(".u").is_none_or(|(of, _)| of != part) {
            return Err(fail("Câu không thuộc phần này"));
        }
    }
    if fs::symlink_metadata(dir.join(".dubflow-work").join(format!("review-{part}.json")))
        .is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
        return Err(fail("Tệp duyệt cảnh của phần này không an toàn (là liên kết)"));
    }
    let args = crate::mastering::checked_args(&action, unit.as_deref(), what.as_deref(), ids.as_deref().unwrap_or_default(),
                                              warning.as_deref(), value, is_unit, is_shot)?;
    tauri::async_runtime::spawn_blocking(move || crate::mastering::run_review(&dir, ["--part", &part], &args))
        .await.map_err(|error| fail(error.to_string()))?
}

/// A shot's keyframe (JPEG) of a raw run, for the review panel.
#[tauri::command]
pub fn compilation_keyframe(output_dir: String, name: String) -> Result<tauri::ipc::Response, String> {
    let dir = raw_dir(&output_dir)?;
    Ok(tauri::ipc::Response::new(crate::mastering::read_keyframe(&dir, &name)?))
}

/// What is still open in each part, read fresh from the review files through the review CLI (fails closed).
fn open_parts(dir: &Path, review: impl Fn(&Path, &str) -> Result<Value, String>) -> Result<Vec<(String, usize)>, String> {
    let mut open = Vec::new();
    for part in parts_of(&read_spec(dir)?) {
        let view = review(dir, &part)?;
        let unreviewed = view["unreviewed"].as_array().map_or(0, Vec::len);
        if view["kind"] != "editorial_review_view" {
            return Err(fail(format!("Không đọc được bản duyệt của phần {part}")));
        }
        if unreviewed > 0 && view["accept_unreviewed"] != true {
            open.push((part, unreviewed));
        }
    }
    Ok(open)
}

/// The final export: only from a completed run, and only when every part is reviewed or its unreviewed sentences
/// were accepted (`accept`, recorded per part, after the user confirmed). Copies the draft to `final.mp4`.
fn finalize_in(dir: &Path, accept: bool, review: impl Fn(&Path, &str, &[String]) -> Result<Value, String>,
               qc: impl Fn(&Path) -> Result<Value, String>) -> Result<Value, String> {
    let work = dir.join(".dubflow-work");
    let status = crate::mastering::read_plain_json(&work.join("status.json"), STATUS_LIMIT)?.unwrap_or(Value::Null);
    if status["state"] != "COMPLETED" {
        return Err(fail("Chưa có bản dựng hoàn tất để xuất"));
    }
    let view = |dir: &Path, part: &str| review(dir, part, &["view".to_owned()]);
    let open = open_parts(dir, &view)?;
    if !open.is_empty() {
        if !accept {
            let count: usize = open.iter().map(|(_, n)| n).sum();
            return Ok(json!({"exported": false, "unreviewed": count,
                             "parts": open.iter().map(|(part, n)| json!({"part": part, "unreviewed": n})).collect::<Vec<_>>()}));
        }
        for (part, _) in &open {
            review(dir, part, &["accept".to_owned(), "true".to_owned()])?;
        }
        if !open_parts(dir, &view)?.is_empty() {
            return Err(fail("Không lưu được xác nhận xuất các câu chưa duyệt"));
        }
    }
    // Final QC: a completed render is not a passed check (timeline, length, captions, peak, decisions not rendered,
    // critical sentences left unsettled); nothing is exported until it passes
    let checked = qc(dir)?;
    if checked["passed"] != true {
        return Ok(json!({"exported": false, "qc": checked}));
    }
    let draft = dir.join("compilation.mp4");
    let metadata = fs::symlink_metadata(&draft).map_err(|_| fail("Không tìm thấy bản dựng"))?;
    if crate::edit::is_link(&metadata) || !metadata.is_file() {
        return Err(fail("Bản dựng không an toàn (là liên kết)"));
    }
    let target = dir.join(FINAL);
    if fs::symlink_metadata(&target).is_ok_and(|m| crate::edit::is_link(&m) || !m.is_file()) {
        return Err(fail("Không ghi được bản xuất (đường dẫn là liên kết)"));
    }
    let partial = dir.join("final.partial.mp4");
    let _ = fs::remove_file(&partial);
    let identity = draft_identity(dir).ok_or_else(|| fail("Không tìm thấy bản dựng"))?;
    fs::copy(&draft, &partial).and_then(|_| fs::rename(&partial, &target)).map_err(|error| {
        let _ = fs::remove_file(&partial);
        fail(format!("Không xuất được video: {error}"))
    })?;
    // what this final is: its hash, a revision that grows with each export, the draft it copied (P2-6)
    let sha256 = file_sha256(&target).map_err(|_| fail("Không đọc lại được bản xuất"))?;
    let record_path = work.join(FINAL_RECORD);
    let previous = crate::mastering::read_plain_json(&record_path, FINAL_RECORD_LIMIT).ok().flatten();
    let revision = previous.as_ref().and_then(|value| value["revision"].as_u64()).unwrap_or(0) + 1;
    let exported_at_ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
    let record = json!({"schema_version": 1, "kind": "compilation_final", "revision": revision, "sha256": sha256,
                        "exported_at_ms": exported_at_ms, "draft": {"bytes": identity.0, "modified_ns": identity.1.to_string()}});
    write_replace(&record_path, &serde_json::to_vec(&record).unwrap_or_default()).map_err(|_| fail("Không ghi được thông tin bản xuất"))?;
    Ok(json!({"exported": true, "path": display_path(&target), "sha256": sha256, "revision": revision}))
}

#[tauri::command]
pub async fn compilation_raw_finalize(output_dir: String, accept: bool) -> Result<Value, String> {
    let dir = raw_dir(&output_dir)?;
    tauri::async_runtime::spawn_blocking(move || {
        finalize_in(&dir, accept, |dir, part, args| crate::mastering::run_review(dir, ["--part", part], args),
                    |dir| crate::mastering::run_final_qc(dir, &["--program"]))
    }).await.map_err(|error| fail(error.to_string()))?
}

/// The Final QC of a raw programme (what "Xuất bản cuối" checks first), for the result page.
#[tauri::command]
pub async fn compilation_raw_final_qc(output_dir: String) -> Result<Value, String> {
    let dir = raw_dir(&output_dir)?;
    tauri::async_runtime::spawn_blocking(move || crate::mastering::run_final_qc(&dir, &["--program"]))
        .await.map_err(|error| fail(error.to_string()))?
}

/// "Thêm cả thư mục": a folder of source videos.
#[tauri::command]
pub fn pick_source_folder() -> Option<String> {
    rfd::FileDialog::new().set_title("Chọn thư mục chứa video nguồn").pick_folder().map(|path| display_path(&path))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn catalog() -> VoiceCatalog {
        serde_json::from_value::<crate::VoiceManifest>(json!({"schema_version":1,"backend":"test","approved":true,
            "voice_id":"vi-test","voices":[{"voice_id":"vi-test","name":"Test","gender":"Nữ","accent":"Nam",
            "style":"tự nhiên","description":"Test","voice_version":"1","approved":true}]}))
            .map(|v| VoiceCatalog { default_voice_id: v.voice_id, voices: v.voices }).unwrap()
    }

    fn base(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("dubflow-raw-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn request(videos: &[&Path]) -> Value {
        let items: Vec<Value> = videos.iter().enumerate().map(|(i, video)| json!({"title": format!("Mục {}", i + 1),
            "itemId": null, "rank": videos.len() - i, "narration": "Đây là lời bình.", "notes": "", "keepSourceAudio": "auto",
            "sources": [{"video": display_path(video), "startMs": null, "endMs": null, "notes": null, "rights": "owned"}]})).collect();
        json!({"title": "Top 5", "layout": "toan-man-hinh", "voiceId": "vi-test", "speed": 1.0, "music": null, "musicVolume": 0.12,
               "kind": "top_n", "ranking": "user", "applySuggestion": false, "hook": "", "intro": "Xin chào.", "outro": "",
               "items": items, "mastering": "balanced", "ai": null, "draftNarration": false, "outputRoot": null, "cpuOnly": true})
    }

    fn parse(value: Value) -> RawRequest {
        serde_json::from_value(value).unwrap()
    }

    #[test]
    fn builds_a_v2_spec_and_keeps_the_users_ranking() {
        let base = base("spec");
        let a = base.join("a.mp4");
        let b = base.join("b.MOV");
        fs::write(&a, b"x").unwrap();
        fs::write(&b, b"x").unwrap();
        let spec = build_spec(&parse(request(&[&a, &b])), &catalog()).unwrap();
        assert_eq!(spec["schema_version"], 2);
        assert_eq!(spec["items"][0]["item_id"], "i1");
        assert_eq!(spec["items"][0]["rank"], 2, "the user's rank, never reordered");
        assert_eq!(spec["items"][1]["sources"][0]["video"], display_path(&b));
        assert_eq!(spec["program"], json!({"kind": "top_n", "ranking": "user", "apply_suggestion": false}));
        assert_eq!(spec["mastering"]["level"], "balanced");
        assert!(spec["editorial"].get("ai").is_none(), "no AI route without a provider");
        assert!(!spec.to_string().contains("key"), "no key in the spec");
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn refuses_bad_fields_unsupported_formats_and_links() {
        let base = base("bad");
        let a = base.join("a.mp4");
        fs::write(&a, b"x").unwrap();
        let flv = base.join("c.flv");
        fs::write(&flv, b"x").unwrap();
        let good = request(&[&a, &a]);
        assert!(build_spec(&parse(good.clone()), &catalog()).is_ok());
        for (field, change) in [("title", json!("")), ("hook", json!("x".repeat(301))), ("layout", json!("other")),
            ("speed", json!(2)), ("kind", json!("best")), ("ranking", json!("ai")), ("mastering", json!("max")),
            ("voiceId", json!("missing")), ("items", json!([])), ("music", json!("relative.mp3")), ("draftNarration", json!(true))] {
            let mut bad = good.clone();
            bad[field] = change;
            assert!(build_spec(&parse(bad), &catalog()).is_err(), "{field}");
        }
        let mut suggested = good.clone();
        suggested["applySuggestion"] = json!(true);
        assert!(build_spec(&parse(suggested), &catalog()).is_err(), "applying a suggestion needs one");
        for (field, change) in [("title", json!("x".repeat(81))), ("narration", json!("")), ("rank", json!(0)),
            ("keepSourceAudio", json!("loud")), ("sources", json!([]))] {
            let mut bad = good.clone();
            bad["items"][0][field] = change;
            assert!(build_spec(&parse(bad), &catalog()).is_err(), "{field}");
        }
        let mut kept = good.clone();
        kept["items"][0]["itemId"] = json!("i2");
        let spec = build_spec(&parse(kept.clone()), &catalog()).unwrap();
        assert_eq!((spec["items"][0]["item_id"].as_str(), spec["items"][1]["item_id"].as_str()), (Some("i2"), Some("i1")),
                   "an item keeps its id when the list is reordered; a new one gets a free id");
        for id in [json!("hook"), json!("i0"), json!("../i1"), json!("i31x")] {
            let mut bad = kept.clone();
            bad["items"][0]["itemId"] = id.clone();
            assert!(build_spec(&parse(bad), &catalog()).is_err(), "{id}");
        }
        let mut twice = kept;
        twice["items"][1]["itemId"] = json!("i2");
        assert!(build_spec(&parse(twice), &catalog()).is_err());
        let mut same_rank = good.clone();
        same_rank["items"][1]["rank"] = same_rank["items"][0]["rank"].clone();
        assert!(build_spec(&parse(same_rank), &catalog()).is_err());
        for video in [display_path(&flv), "relative.mp4".into(), display_path(&base.join("..").join("a.mp4")),
                      display_path(&base.join("missing.mp4")), display_path(&base)] {
            let mut bad = good.clone();
            bad["items"][0]["sources"][0]["video"] = json!(video);
            assert!(build_spec(&parse(bad), &catalog()).is_err(), "{video}");
        }
        let error = source_file(&display_path(&flv)).unwrap_err();
        assert!(error.contains("c.flv") && error.contains("chưa hỗ trợ"), "{error}");
        for (field, change) in [("startMs", json!(5000)), ("rights", json!("stolen")), ("notes", json!("x".repeat(401)))] {
            let mut bad = good.clone();
            bad["items"][0]["sources"][0][field] = change;
            if field == "startMs" { bad["items"][0]["sources"][0]["endMs"] = json!(4000); }
            assert!(build_spec(&parse(bad), &catalog()).is_err(), "{field}");
        }
        let work = base.join("x").join(".dubflow-work");
        fs::create_dir_all(&work).unwrap();
        fs::write(work.join("source.mp4"), b"x").unwrap();
        assert!(source_file(&display_path(&work.join("source.mp4"))).is_err(), "the app's own files are not sources");
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&a, base.join("link.mp4")).unwrap();
            assert!(source_file(&display_path(&base.join("link.mp4"))).is_err(), "a link is not a source");
        }
        let mut unknown = good.clone();
        unknown["shell"] = json!("calc.exe");
        assert!(serde_json::from_value::<RawRequest>(unknown).is_err(), "unknown fields are refused");
        let mut filter = good;
        filter["items"][0]["sources"][0]["filter"] = json!("[0:v]null");
        assert!(serde_json::from_value::<RawRequest>(filter).is_err(), "no raw FFmpeg filters from the UI");
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn ids_have_the_engines_shapes_only() {
        for good in ["hook", "intro", "outro", "i1", "i30"] { assert!(is_part(good), "{good}"); }
        for bad in ["i0", "i01", "i123", "../i4", "i4/..", "", "item", "I1", "i1 "] { assert!(!is_part(bad), "{bad}"); }
        assert!(is_unit("i1.u2") && is_unit("hook.u1") && is_unit("outro.u123"));
        assert!(!is_unit("i1.u") && !is_unit("u2") && !is_unit("i1.u2;x") && !is_unit("../i1.u2"));
        assert!(is_shot("i1-s1c3") && is_shot("i12-s8c1234") && is_shot("i2-s1c12f3"));
        for bad in ["v1c3", "hook-s1c3", "i1-s12c3", "i1-s1c", "i1-s1c3f12", "i1-s1c3;rm", "../i1-s1c3"] {
            assert!(!is_shot(bad), "{bad}");
        }
        assert!(crate::mastering::checked_args("decide", Some("i1.u2"), Some("pin"), &["i1-s1c3".into()], None, None, is_unit, is_shot).is_ok());
        assert!(crate::mastering::checked_args("decide", Some("i1.u2"), Some("pin"), &["v1c3".into()], None, None, is_unit, is_shot).is_err());
        assert!(crate::mastering::checked_args("decide", Some("s2"), Some("pin"), &[], None, None, is_unit, is_shot).is_err());
    }

    fn make_run(base: &Path, spec: Value) -> PathBuf {
        let dir = base.join("DubFlow Output").join("Tong hop - Top 5").join("run-0123456789ab");
        fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
        fs::write(dir.join(".dubflow-work").join("spec.json"), spec.to_string()).unwrap();
        dir
    }

    #[test]
    fn run_folders_are_plain_and_inside_dubflow_output() {
        let base = base("dir");
        let dir = make_run(&base, json!({"schema_version": 2, "items": [{"item_id": "i1"}, {"item_id": "i2"}], "intro": "x", "hook": ""}));
        assert!(raw_dir(&display_path(&dir)).is_ok());
        assert_eq!(parts_of(&read_spec(&dir).unwrap()), ["intro", "i1", "i2"]);
        for bad in [display_path(&base), "relative/run-0123456789ab".into(), format!("{}/../run-0123456789ab", display_path(&dir)),
                    display_path(&base.join("DubFlow Output").join("Ban hang - x").join("run-0123456789ab")),
                    display_path(&base.join("Other").join("Tong hop - x").join("run-0123456789ab"))] {
            assert!(raw_dir(&bad).is_err(), "{bad}");
        }
        // a run folder reached through a link or junction is refused, and nothing is written behind it
        let outside = base.join("outside");
        fs::create_dir_all(outside.join("run-0123456789ab").join(".dubflow-work")).unwrap();
        fs::write(outside.join("run-0123456789ab").join(".dubflow-work").join("spec.json"), r#"{"schema_version":2,"items":[]}"#).unwrap();
        let linked = base.join("DubFlow Output").join("Tong hop - Linked");
        if crate::product::tests::link_dir(&outside, &linked) {
            assert!(raw_dir(&display_path(&linked.join("run-0123456789ab"))).is_err());
            assert!(compilation_raw_cancel(display_path(&linked.join("run-0123456789ab"))).is_err());
            assert!(!outside.join("run-0123456789ab").join(".dubflow-work").join("cancel").exists());
        } else {
            assert!(cfg!(not(windows)), "junctions need no privilege on Windows");
        }
        let work_link = base.join("DubFlow Output").join("Tong hop - W").join("run-00000000000a");
        fs::create_dir_all(&work_link).unwrap();
        if crate::product::tests::link_dir(&outside.join("run-0123456789ab").join(".dubflow-work"), &work_link.join(".dubflow-work")) {
            assert!(raw_dir(&display_path(&work_link)).is_err(), "a linked work folder is refused");
        }
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn versions_are_told_apart_with_a_clear_reason() {
        let base = base("versions");
        let v1 = make_run(&base, json!({"schema_version": 1, "items": []}));
        assert!(read_spec(&v1).unwrap_err().contains("phiên bản 1"));
        fs::write(v1.join(".dubflow-work").join("spec.json"), r#"{"schema_version":7}"#).unwrap();
        let error = read_spec(&v1).unwrap_err();
        assert!(error.contains("mới hơn") && error.contains('7'), "{error}");
        fs::write(v1.join(".dubflow-work").join("spec.json"), b"not json").unwrap();
        assert!(read_spec(&v1).is_err());
        assert!(compilation_raw_open(display_path(&v1)).is_err());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn review_and_rerender_refuse_parts_and_states_they_should() {
        let base = base("review");
        let dir = make_run(&base, json!({"schema_version": 2, "items": [{"item_id": "i1"}, {"item_id": "i2"}], "intro": "x"}));
        assert!(checked_part(&dir, "i1").is_ok() && checked_part(&dir, "intro").is_ok());
        assert!(checked_part(&dir, "i3").is_err(), "not in this programme");
        assert!(checked_part(&dir, "outro").is_err(), "no outro written");
        write_status(&dir.join(".dubflow-work"), "RUNNING", "x").unwrap();
        assert!(compilation_raw_rerender(display_path(&dir), None, true).unwrap_err().contains("đang được dựng"));
        let opened = compilation_raw_open(display_path(&dir)).unwrap();
        assert_eq!(opened["status"]["state"], "RUNNING");
        assert!(opened["video"].is_null() && opened["final"].is_null());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn final_export_needs_every_part_reviewed_or_accepted() {
        let base = base("final");
        let dir = make_run(&base, json!({"schema_version": 2, "items": [{"item_id": "i1"}, {"item_id": "i2"}], "intro": ""}));
        let work = dir.join(".dubflow-work");
        let accepted = std::cell::RefCell::new(Vec::<String>::new());
        let review = |_: &Path, part: &str, args: &[String]| -> Result<Value, String> {
            if args[0] == "accept" { accepted.borrow_mut().push(part.to_owned()); }
            let open = part == "i2" && !accepted.borrow().iter().any(|p| p == "i2");
            Ok(json!({"kind": "editorial_review_view", "unreviewed": if open { json!(["i2.u1"]) } else { json!([]) },
                      "accept_unreviewed": !open && part == "i2"}))
        };
        let passed = |_: &Path| -> Result<Value, String> { Ok(json!({"kind": "final_qc", "passed": true})) };
        write_status(&work, "RUNNING", "x").unwrap();
        assert!(finalize_in(&dir, true, review, passed).is_err(), "nothing to export before the render completes");
        write_status(&work, "COMPLETED", "x").unwrap();
        fs::write(dir.join("compilation.mp4"), b"draft").unwrap();
        let asked = finalize_in(&dir, false, review, passed).unwrap();
        assert_eq!(asked["exported"], false);
        assert_eq!(asked["unreviewed"], 1);
        assert_eq!(asked["parts"][0]["part"], "i2");
        assert!(!dir.join(FINAL).exists(), "no export without the user's confirmation");
        // a completed render whose final QC fails is not exported, and the failed checks come back
        let failing = |_: &Path| -> Result<Value, String> {
            Ok(json!({"kind": "final_qc", "passed": false, "state": "FINAL_QC_FAILED",
                      "failed": [{"id": "duration", "ok": false, "detail": "x"}]}))
        };
        let blocked = finalize_in(&dir, true, review, failing).unwrap();
        assert_eq!(blocked["exported"], false);
        assert_eq!(blocked["qc"]["state"], "FINAL_QC_FAILED");
        assert!(!dir.join(FINAL).exists(), "no export when the final QC fails");
        let unreadable = |_: &Path| -> Result<Value, String> { Err("qc crashed".to_owned()) };
        assert!(finalize_in(&dir, true, review, unreadable).is_err(), "a QC that cannot run fails closed");
        assert!(!dir.join(FINAL).exists());
        let done = finalize_in(&dir, true, review, passed).unwrap();
        assert_eq!(done["exported"], true);
        assert_eq!(done["revision"], 1);
        assert_eq!(done["sha256"].as_str().map(str::len), Some(64));
        assert_eq!(accepted.borrow().as_slice(), ["i2"], "only the open part is accepted");
        assert_eq!(fs::read(dir.join(FINAL)).unwrap(), b"draft");
        // P2-6: the final is current until the draft is rendered again; then it is stale until exported again
        let opened = compilation_raw_open(display_path(&dir)).unwrap();
        assert_eq!(opened["final_state"]["stale"], false, "{opened}");
        assert_eq!(opened["final_state"]["revision"], 1);
        assert_eq!(opened["final_state"]["sha256"], done["sha256"]);
        std::thread::sleep(std::time::Duration::from_millis(20));
        fs::write(dir.join("compilation.mp4"), b"draft after a re-render").unwrap();
        let stale = compilation_raw_open(display_path(&dir)).unwrap();
        assert_eq!(stale["final_state"]["stale"], true);
        assert_eq!(stale["final_state"]["reason"], "rerendered");
        assert_eq!(stale["final"], opened["final"], "the old file is still there, only marked");
        write_status(&work, "RUNNING", "x").unwrap();
        assert_eq!(compilation_raw_open(display_path(&dir)).unwrap()["final_state"]["reason"], "rendering");
        write_status(&work, "COMPLETED", "x").unwrap();
        let again = finalize_in(&dir, true, review, passed).unwrap();
        assert_eq!(again["revision"], 2);
        assert_ne!(again["sha256"], done["sha256"]);
        assert_eq!(fs::read(dir.join(FINAL)).unwrap(), b"draft after a re-render");
        let fresh = compilation_raw_open(display_path(&dir)).unwrap();
        assert_eq!(fresh["final_state"]["stale"], false);
        assert_eq!(fresh["final_state"]["sha256"], again["sha256"]);
        // an export made before the record existed is never shown as current
        fs::remove_file(work.join(FINAL_RECORD)).unwrap();
        assert_eq!(compilation_raw_open(display_path(&dir)).unwrap()["final_state"]["reason"], "unknown");
        let broken = |_: &Path, _: &str, _: &[String]| -> Result<Value, String> { Ok(json!({"kind": "other"})) };
        assert!(finalize_in(&dir, true, broken, passed).is_err(), "an unreadable review fails closed");
        fs::remove_dir_all(base).unwrap();
    }
}
