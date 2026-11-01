//! Product videos from pasted text and the user's own photos and clips
//! (ADR-0041): pick files, send downscaled photos to the webview for the AI,
//! keep a library of backgrounds, and run `python -m engine.dubflow.product`,
//! which reports through a status file.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{control_root, data_root, default_download_folder, display_path, fail, load_voice_catalog, output_file, version_root, CREATE_NO_WINDOW};

const IMAGE_EXTENSIONS: [&str; 5] = ["jpg", "jpeg", "png", "webp", "bmp"];
const CLIP_EXTENSIONS: [&str; 6] = ["mp4", "mov", "mkv", "webm", "avi", "m4v"];
const MUSIC_EXTENSIONS: [&str; 6] = ["mp3", "m4a", "aac", "wav", "ogg", "flac"];
const NAME_CHARS: usize = 40;
const TEMPLATES: [&str; 3] = ["ban-tin", "toan-man-hinh", "chi-chu"];
/// "Chỉ chữ": text cards over a background or a gradient, no photos or clips.
const TEXT_ONLY: &str = "chi-chu";
const MAX_FEATURES: usize = 8;
const MAX_FEATURE_CHARS: usize = 200;

fn has_extension(path: &Path, extensions: &[&str]) -> bool {
    path.extension().and_then(|value| value.to_str())
        .is_some_and(|value| extensions.iter().any(|known| known.eq_ignore_ascii_case(value)))
}

fn local_file(value: &str, extensions: &[&str]) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if !path.is_absolute() || !path.is_file() || !has_extension(&path, extensions) {
        return Err(fail(format!("Không mở được file: {}", path.file_name().map(|name| name.to_string_lossy()).unwrap_or_default())));
    }
    Ok(path)
}

/// Photos, photos and clips, or music, from the file dialog.
#[tauri::command]
pub fn pick_product_files(kind: String) -> Result<Vec<String>, String> {
    let dialog = rfd::FileDialog::new();
    let files = match kind.as_str() {
        "images" => dialog.set_title("Chọn ảnh sản phẩm").add_filter("Ảnh", &IMAGE_EXTENSIONS).pick_files(),
        "media" => dialog.set_title("Chọn ảnh hoặc clip").add_filter("Ảnh và clip", &[&IMAGE_EXTENSIONS[..], &CLIP_EXTENSIONS[..]].concat()).pick_files(),
        "music" => dialog.set_title("Chọn nhạc nền").add_filter("Nhạc", &MUSIC_EXTENSIONS).pick_file().map(|file| vec![file]),
        "background" => dialog.set_title("Chọn ảnh nền").add_filter("Ảnh", &IMAGE_EXTENSIONS).pick_file().map(|file| vec![file]),
        _ => return Err(fail("Loại file không hợp lệ")),
    };
    Ok(files.unwrap_or_default().into_iter().map(|path| path.to_string_lossy().into_owned()).collect())
}

/// A JPEG copy of a photo, at most 1568 px on its long side (what Claude reads
/// at full detail), or a smaller thumbnail. Raw bytes avoid a JSON number array.
#[tauri::command]
pub async fn product_image(path: String, max_side: Option<u32>) -> Result<tauri::ipc::Response, String> {
    let image = local_file(&path, &IMAGE_EXTENSIONS)?;
    let side = max_side.unwrap_or(1568).clamp(64, 1568);
    let ffmpeg = version_root()?.join("runtime").join("media").join("ffmpeg.exe");
    let mut command = Command::new(&ffmpeg);
    command.args(["-nostdin", "-v", "error", "-i"]).arg(&image)
        .args(["-frames:v", "1", "-vf", &format!("scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease"),
            "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output())
        .await.map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
    if !output.status.success() || output.stdout.len() < 64 {
        return Err(fail("Không đọc được ảnh này"));
    }
    Ok(tauri::ipc::Response::new(output.stdout))
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProductRequest {
    name: String,
    price: String,
    voice_id: String,
    variants: Vec<String>,
    visuals: Vec<String>,
    music: Option<String>,
    music_volume: f64,
    /// The user's background: 1080×720 for the panel, 1080×1920 for the whole frame.
    #[serde(default)]
    background: Option<String>,
    /// "ban-tin", "toan-man-hinh" or "chi-chu"; Bản tin when absent.
    #[serde(default)]
    template: Option<String>,
    /// The highlights "Chỉ chữ" shows as cards.
    #[serde(default)]
    features: Vec<String>,
    /// The part of the background kept when it is cropped (0–1 across and down); the centre when absent.
    #[serde(default)]
    focus: Option<Focus>,
    /// Narration speed, 1.0–1.3; 1.0 when absent.
    #[serde(default)]
    speed: Option<f64>,
    /// Mẫu (#426): an earlier run whose narration is reused for a script spoken
    /// the same way; the new run sits beside it unless a folder is chosen.
    #[serde(default)]
    reuse_audio_from: Option<String>,
    cpu_only: bool,
    output_root: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct Focus {
    x: f64,
    y: f64,
}

/// A folder name Windows accepts, from the product name.
pub(super) fn folder_title(name: &str) -> String {
    let cleaned: String = name.chars()
        .map(|character| if character.is_control() || "<>:\"/\\|?*".contains(character) { ' ' } else { character })
        .collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ");
    let title: String = cleaned.chars().take(NAME_CHARS).collect();
    let title = title.trim_end_matches([' ', '.']).to_owned();
    if title.is_empty() { "san pham".to_owned() } else { title }
}

fn product_output(base: &Path, name: &str, seed: &str) -> PathBuf {
    let digest = Sha256::digest(seed.as_bytes());
    let run: String = digest.iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    base.join("DubFlow Output").join(format!("Ban hang - {}", folder_title(name))).join(format!("run-{run}"))
}

/// A product run's folder: below "DubFlow Output", with a status file.
fn product_dir(value: &str) -> Result<PathBuf, String> {
    let dir = PathBuf::from(value);
    let inside = dir.is_absolute() && dir.ancestors().skip(1).any(|part| part.file_name().is_some_and(|name| name == "DubFlow Output"));
    if !inside || value.contains("..") || !dir.join(".dubflow-work").is_dir() {
        return Err(fail("Thư mục video bán hàng không hợp lệ"));
    }
    Ok(dir)
}

/// Start rendering every script variant; returns the run's output folder.
#[tauri::command]
pub fn product_render(request: ProductRequest) -> Result<String, String> {
    let root = version_root()?;
    if !load_voice_catalog(&root)?.voices.iter().any(|voice| voice.voice_id == request.voice_id) {
        return Err(fail("Giọng đã chọn không có trong phiên bản này"));
    }
    let template = request.template.unwrap_or_else(|| "ban-tin".to_owned());
    if !TEMPLATES.contains(&template.as_str()) {
        return Err(fail("Mẫu video không hợp lệ"));
    }
    // "Chỉ chữ" uses no photos or clips.
    let visuals = if template == TEXT_ONLY { Vec::new() } else { request.visuals };
    if (template != TEXT_ONLY && visuals.is_empty()) || visuals.len() > 30 || request.variants.is_empty() || request.variants.len() > 5 {
        return Err(fail("Cần 1–30 ảnh hoặc clip và 1–5 kịch bản"));
    }
    if request.features.len() > MAX_FEATURES || request.features.iter().any(|item| item.chars().count() > MAX_FEATURE_CHARS) {
        return Err(fail("Điểm nổi bật không hợp lệ"));
    }
    let focus = request.focus.map_or((0.5, 0.5), |focus| (focus.x, focus.y));
    if ![focus.0, focus.1].iter().all(|value| (0.0..=1.0).contains(value)) {
        return Err(fail("Vị trí ảnh nền không hợp lệ"));
    }
    let speed = request.speed.unwrap_or(1.0);
    if !(1.0..=1.3).contains(&speed) {
        return Err(fail("Tốc độ đọc không hợp lệ"));
    }
    if !(0.0..=1.0).contains(&request.music_volume) {
        return Err(fail("Âm lượng nhạc nền không hợp lệ"));
    }
    let visuals = visuals.iter()
        .map(|value| local_file(value, &[&IMAGE_EXTENSIONS[..], &CLIP_EXTENSIONS[..]].concat()))
        .collect::<Result<Vec<_>, _>>()?;
    let music = request.music.as_deref().map(|value| local_file(value, &MUSIC_EXTENSIONS)).transpose()?;
    let background = request.background.as_deref().map(|value| local_file(value, &IMAGE_EXTENSIONS)).transpose()?;
    let reuse = request.reuse_audio_from.as_deref().map(product_dir).transpose()?;
    let beside_reuse = reuse.as_deref().and_then(|dir| dir.ancestors().find(|part| part.file_name().is_some_and(|name| name == "DubFlow Output")))
        .and_then(Path::parent).map(Path::to_path_buf);
    let base = match request.output_root {
        Some(value) => {
            let base = PathBuf::from(value);
            if !base.is_absolute() || !base.is_dir() {
                return Err(fail("Thư mục lưu kết quả không tồn tại; hãy chọn lại"));
            }
            base
        }
        // Beside the run it is made again from; else next to the first photo or
        // clip; "Chỉ chữ" has none, so the Videos folder.
        None if beside_reuse.is_some() => beside_reuse.unwrap_or_default(),
        None => match visuals.first() {
            Some(first) => first.parent().ok_or_else(|| fail("Không xác định được thư mục lưu"))?.to_path_buf(),
            None => default_download_folder()?.parent().ok_or_else(|| fail("Không xác định được thư mục lưu"))?.to_path_buf(),
        },
    };
    let started = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let output = product_output(&base, &request.name, &format!("{started}:{}", request.name));
    let work = output.join(".dubflow-work");
    fs::create_dir_all(&work).map_err(|error| fail(format!("Không tạo được thư mục kết quả: {error}")))?;
    let spec = json!({
        "schema_version": 1, "name": request.name, "price": request.price, "voice_id": request.voice_id,
        "variants": request.variants, "visuals": visuals, "music": music, "music_volume": request.music_volume, "background": background,
        "template": template, "speed": speed, "features": request.features, "focus": {"x": focus.0, "y": focus.1},
        "reuse_audio_from": reuse.as_deref().map(display_path),
    });
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).map_err(|error| fail(error.to_string()))?)
        .map_err(|error| fail(format!("Không ghi được yêu cầu render: {error}")))?;
    fs::write(work.join("status.json"), r#"{"schema_version":1,"state":"RUNNING","done":0,"total":0,"message":"Đang khởi động…","videos":[]}"#)
        .map_err(|error| fail(format!("Không ghi được trạng thái: {error}")))?;
    let media = root.join("runtime").join("media");
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.product", "--spec"]).arg(&spec_path)
        .arg("--output-dir").arg(&output)
        .arg("--app-root").arg(root.join("app"))
        .arg("--model-root").arg(control_root()?.join("models"))
        .arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if request.cpu_only {
        command.arg("--cpu-only");
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command.spawn().map_err(|error| fail(format!("Không chạy được bộ tạo video: {error}")))?;
    Ok(display_path(&output))
}

/// The run's progress, as `engine.dubflow.product` last wrote it.
#[tauri::command]
pub fn product_status(output_dir: String) -> Result<Value, String> {
    let dir = product_dir(&output_dir)?;
    let bytes = fs::read(dir.join(".dubflow-work").join("status.json")).map_err(|_| fail("Chưa có trạng thái"))?;
    serde_json::from_slice::<Value>(&bytes).ok().filter(Value::is_object).ok_or_else(|| fail("Trạng thái không hợp lệ"))
}

/// Ask a running batch to stop; it ends at the next sentence or video and
/// keeps the videos already finished.
#[tauri::command]
pub fn product_cancel(output_dir: String) -> Result<(), String> {
    let dir = product_dir(&output_dir)?;
    fs::write(dir.join(".dubflow-work").join("cancel"), b"").map_err(|error| fail(format!("Không hủy được: {error}")))
}

/// A product video `<…>\DubFlow Output\Ban hang - <name>\run-<id>\video-<n>.mp4`
/// of a run that kept its request: the run's folder and `n`.
pub(crate) fn product_video(video: &Path) -> Option<(PathBuf, usize)> {
    let number = video.file_name()?.to_str()?.strip_prefix("video-")?.strip_suffix(".mp4")?;
    let number = (!number.is_empty() && number.len() <= 2 && number.bytes().all(|byte| byte.is_ascii_digit()))
        .then(|| number.parse::<usize>().ok()).flatten().filter(|value| *value >= 1)?;
    let run = video.parent()?;
    let named = |dir: Option<&Path>, check: &dyn Fn(&str) -> bool| dir.and_then(Path::file_name).and_then(|name| name.to_str()).is_some_and(check);
    (named(Some(run), &|name| name.starts_with("run-")) && named(run.parent(), &|name| name.starts_with("Ban hang - "))
        && named(run.parent().and_then(Path::parent), &|name| name == "DubFlow Output")
        && run.join(".dubflow-work").join("spec.json").is_file()).then(|| (run.to_path_buf(), number))
}

const SPEC_LIMIT: u64 = 256 * 1024;

pub(crate) fn read_small_json(path: &Path, limit: u64) -> Option<Value> {
    fs::metadata(path).ok().filter(|metadata| metadata.is_file() && metadata.len() <= limit)?;
    serde_json::from_slice::<Value>(&fs::read(path).ok()?).ok().filter(Value::is_object)
}

/// Mẫu (#426): what a product video was made from, its number in the run and
/// whether its narration is still there to reuse.
#[tauri::command]
pub fn product_spec(output_path: String) -> Result<Value, String> {
    let video = output_file(&output_path)?;
    let (run, number) = product_video(&video).ok_or_else(|| fail("Đây không phải video bán hàng của DubFlow"))?;
    let work = run.join(".dubflow-work");
    let spec = read_small_json(&work.join("spec.json"), SPEC_LIMIT).filter(|spec| spec["schema_version"] == 1)
        .ok_or_else(|| fail("Không đọc được thông tin mẫu của video này"))?;
    let status = read_small_json(&work.join("status.json"), SPEC_LIMIT);
    let duration = status.as_ref().and_then(|status| status["videos"].as_array().cloned()).unwrap_or_default().into_iter()
        .find(|video| video["index"] == number).map_or(Value::Null, |video| video["duration_ms"].clone());
    Ok(json!({"spec": spec, "variant": number, "run": display_path(&run), "duration_ms": duration,
        "narration": work.join(format!("narration-{number}.wav")).is_file()}))
}

/// Run folders `<root>\DubFlow Output\<prefix><title>\run-*` that kept their
/// request, newest first. Each root is a folder the user saves to (or any
/// folder inside "DubFlow Output"); the Videos folder is always looked at.
pub(crate) fn find_runs(roots: &[String], prefix: &str, limit: usize) -> Vec<PathBuf> {
    let mut outputs: Vec<PathBuf> = Vec::new();
    let videos = default_download_folder().ok().and_then(|folder| folder.parent().map(Path::to_path_buf));
    for root in roots.iter().take(64).map(PathBuf::from).chain(videos) {
        if !root.is_absolute() || root.components().any(|part| matches!(part, std::path::Component::ParentDir)) {
            continue;
        }
        let output = root.ancestors().find(|dir| dir.file_name().is_some_and(|name| name == "DubFlow Output"))
            .map_or_else(|| root.join("DubFlow Output"), Path::to_path_buf);
        let Ok(output) = output.canonicalize() else { continue };
        if output.is_dir() && !outputs.contains(&output) {
            outputs.push(output);
        }
    }
    let mut runs: Vec<(std::time::SystemTime, PathBuf)> = Vec::new();
    for output in outputs {
        let Ok(titles) = fs::read_dir(&output) else { continue };
        for title in titles.flatten().take(2000) {
            if !title.file_name().to_str().is_some_and(|name| name.starts_with(prefix)) || !title.path().is_dir() {
                continue;
            }
            let Ok(entries) = fs::read_dir(title.path()) else { continue };
            for entry in entries.flatten().take(500) {
                let dir = entry.path();
                if !entry.file_name().to_str().is_some_and(|name| name.starts_with("run-")) || !dir.is_dir() {
                    continue;
                }
                if let Ok(modified) = fs::metadata(dir.join(".dubflow-work").join("spec.json")).and_then(|metadata| metadata.modified()) {
                    runs.push((modified, dir));
                }
            }
        }
    }
    runs.sort_by(|a, b| b.0.cmp(&a.0));
    runs.into_iter().take(limit).map(|(_, dir)| dir).collect()
}

/// When a run was started: its request's time, in Unix seconds.
pub(crate) fn created_seconds(dir: &Path) -> u64 {
    fs::metadata(dir.join(".dubflow-work").join("spec.json")).and_then(|metadata| metadata.modified()).ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok()).map_or(0, |time| time.as_secs())
}

/// Product runs found below the given folders, newest first, with their status.
#[tauri::command]
pub fn product_runs(roots: Vec<String>) -> Vec<Value> {
    find_runs(&roots, "Ban hang - ", 40).into_iter().filter_map(|dir| {
        let work = dir.join(".dubflow-work");
        let spec = read_small_json(&work.join("spec.json"), SPEC_LIMIT)?;
        Some(json!({"dir": display_path(&dir), "name": spec["name"], "template": spec["template"], "created": created_seconds(&dir),
            "status": read_small_json(&work.join("status.json"), SPEC_LIMIT)}))
    }).collect()
}

/// One image in the background library, `<data root>\backgrounds\<sha256>.<ext>`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LibraryBackground {
    /// The file's SHA-256: the same image is kept once.
    id: String,
    extension: String,
    /// The file name it was added from.
    name: String,
    width: u32,
    height: u32,
    /// Filled in when listed.
    #[serde(default, skip_deserializing)]
    path: String,
}

const LIBRARY_INDEX: &str = "index.json";
const MAX_BACKGROUND_BYTES: u64 = 40 * 1024 * 1024;
const MAX_BACKGROUNDS: usize = 200;
static LIBRARY: Mutex<()> = Mutex::new(());

fn library_dir() -> Result<PathBuf, String> {
    Ok(data_root()?.join("backgrounds"))
}

fn read_library(dir: &Path) -> Vec<LibraryBackground> {
    let entries = fs::read(dir.join(LIBRARY_INDEX)).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| serde_json::from_value::<Vec<LibraryBackground>>(value["items"].clone()).ok())
        .unwrap_or_default();
    entries.into_iter()
        .filter(|entry| background_id(&entry.id).is_ok() && IMAGE_EXTENSIONS.contains(&entry.extension.as_str()))
        .map(|mut entry| {
            entry.path = dir.join(format!("{}.{}", entry.id, entry.extension)).to_string_lossy().into_owned();
            entry
        })
        .filter(|entry| Path::new(&entry.path).is_file())
        .collect()
}

fn write_library(dir: &Path, entries: &[LibraryBackground]) -> Result<(), String> {
    let items: Vec<Value> = entries.iter().map(|entry| json!({
        "id": entry.id, "extension": entry.extension, "name": entry.name, "width": entry.width, "height": entry.height,
    })).collect();
    let partial = dir.join(format!("{LIBRARY_INDEX}.partial"));
    fs::write(&partial, serde_json::to_vec(&json!({"schema_version": 1, "items": items})).map_err(|error| fail(error.to_string()))?)
        .and_then(|_| fs::rename(&partial, dir.join(LIBRARY_INDEX)))
        .map_err(|error| fail(format!("Không lưu được kho ảnh nền: {error}")))
}

fn background_id(value: &str) -> Result<&str, String> {
    if value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
        Ok(value)
    } else {
        Err(fail("Ảnh nền không hợp lệ"))
    }
}

/// Copy `source` into the library unless the same image is already there; newest first.
fn add_to_library(dir: &Path, source: &Path, measure: impl Fn(&Path) -> Result<(u32, u32), String>) -> Result<LibraryBackground, String> {
    let size = fs::metadata(source).map_err(|_| fail("Không mở được ảnh nền"))?.len();
    if size > MAX_BACKGROUND_BYTES {
        return Err(fail("Ảnh nền quá lớn (tối đa 40 MB)"));
    }
    let extension = source.extension().and_then(|value| value.to_str()).unwrap_or_default().to_ascii_lowercase();
    if !IMAGE_EXTENSIONS.contains(&extension.as_str()) {
        return Err(fail("Ảnh nền phải là JPG, PNG, WEBP hoặc BMP"));
    }
    let bytes = fs::read(source).map_err(|error| fail(format!("Không đọc được ảnh nền: {error}")))?;
    let id = format!("{:x}", Sha256::digest(&bytes));
    fs::create_dir_all(dir).map_err(|error| fail(format!("Không tạo được kho ảnh nền: {error}")))?;
    let mut entries = read_library(dir);
    if let Some(known) = entries.iter().find(|entry| entry.id == id) {
        return Ok(known.clone());
    }
    let target = dir.join(format!("{id}.{extension}"));
    let partial = dir.join(format!("{id}.partial"));
    fs::write(&partial, &bytes).and_then(|_| fs::rename(&partial, &target))
        .map_err(|error| fail(format!("Không chép được ảnh nền: {error}")))?;
    let (width, height) = match measure(&target) {
        Ok(size) => size,
        Err(error) => {
            let _ = fs::remove_file(&target);
            return Err(error);
        }
    };
    let name: String = source.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default().chars().take(120).collect();
    let entry = LibraryBackground { id, extension, name, width, height, path: target.to_string_lossy().into_owned() };
    entries.insert(0, entry.clone());
    for dropped in entries.drain(MAX_BACKGROUNDS.min(entries.len())..) {
        let _ = fs::remove_file(&dropped.path);
    }
    write_library(dir, &entries)?;
    Ok(entry)
}

fn remove_from_library(dir: &Path, id: &str) -> Result<(), String> {
    let id = background_id(id)?;
    let mut entries = read_library(dir);
    let Some(index) = entries.iter().position(|entry| entry.id == id) else { return Ok(()) };
    let removed = entries.remove(index);
    write_library(dir, &entries)?;
    fs::remove_file(&removed.path).or_else(|error| if error.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(error) })
        .map_err(|error| fail(format!("Không xóa được ảnh nền: {error}")))
}

/// An image's width and height, from FFprobe.
fn measure_image(path: &Path) -> Result<(u32, u32), String> {
    let mut command = Command::new(version_root()?.join("runtime").join("media").join("ffprobe.exe"));
    command.args(["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x"]).arg(path)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFprobe: {error}")))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let mut parts = text.trim().split('x').map(|value| value.trim().parse::<u32>().ok());
    match (output.status.success(), parts.next().flatten(), parts.next().flatten()) {
        (true, Some(width), Some(height)) if width > 0 && height > 0 => Ok((width, height)),
        _ => Err(fail("Không đọc được ảnh này")),
    }
}

/// The saved backgrounds, newest first.
#[tauri::command]
pub fn product_backgrounds() -> Result<Vec<LibraryBackground>, String> {
    let _guard = LIBRARY.lock().map_err(|_| fail("Kho ảnh nền đang bận"))?;
    Ok(read_library(&library_dir()?))
}

/// Keep a picked image in the library (once per distinct image) and return it.
#[tauri::command]
pub async fn product_background_add(path: String) -> Result<LibraryBackground, String> {
    let source = local_file(&path, &IMAGE_EXTENSIONS)?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = LIBRARY.lock().map_err(|_| fail("Kho ảnh nền đang bận"))?;
        add_to_library(&library_dir()?, &source, measure_image)
    }).await.map_err(|error| fail(error.to_string()))?
}

/// Delete a background from the library.
#[tauri::command]
pub fn product_background_delete(id: String) -> Result<(), String> {
    let _guard = LIBRARY.lock().map_err(|_| fail("Kho ảnh nền đang bận"))?;
    remove_from_library(&library_dir()?, &id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_library_keeps_each_image_once_newest_first_and_deletes() {
        let base = std::env::temp_dir().join(format!("dubflow-backgrounds-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let library = base.join("backgrounds");
        fs::create_dir_all(&base).unwrap();
        let (first, copy, second) = (base.join("Nền xanh.PNG"), base.join("copy.png"), base.join("nen.jpg"));
        fs::write(&first, b"first image").unwrap();
        fs::write(&copy, b"first image").unwrap();
        fs::write(&second, b"second image").unwrap();
        let added = add_to_library(&library, &first, |_| Ok((1080, 720))).unwrap();
        assert_eq!((added.extension.as_str(), added.name.as_str(), added.width, added.height), ("png", "Nền xanh.PNG", 1080, 720));
        assert_eq!(added.id, format!("{:x}", Sha256::digest(b"first image")));
        assert!(Path::new(&added.path).ends_with(format!("{}.png", added.id)));
        assert_eq!(add_to_library(&library, &copy, |_| panic!("a known image is not measured again")).unwrap().id, added.id);
        let newer = add_to_library(&library, &second, |_| Ok((1080, 1920))).unwrap();
        assert_eq!(read_library(&library).iter().map(|entry| entry.id.clone()).collect::<Vec<_>>(), [newer.id.clone(), added.id.clone()]);
        assert!(add_to_library(&library, &base.join("missing.png"), |_| Ok((1, 1))).is_err());
        fs::write(base.join("broken.png"), b"not an image").unwrap();
        assert!(add_to_library(&library, &base.join("broken.png"), |_| Err(fail("Không đọc được ảnh này"))).is_err());
        assert_eq!(fs::read_dir(&library).unwrap().count(), 3, "two images and the index; the broken one is not kept");
        remove_from_library(&library, &added.id).unwrap();
        assert!(!Path::new(&added.path).exists());
        assert_eq!(read_library(&library), vec![newer.clone()]);
        assert!(remove_from_library(&library, "../index").is_err());
        remove_from_library(&library, &"0".repeat(64)).unwrap();
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn product_videos_their_request_and_runs_are_found() {
        let base = std::env::temp_dir().join(format!("dubflow-product-spec-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let run = base.join("DubFlow Output").join("Ban hang - Nồi").join("run-0123456789ab");
        fs::create_dir_all(run.join(".dubflow-work")).unwrap();
        fs::write(run.join(".dubflow-work/spec.json"), json!({"schema_version": 1, "name": "Nồi", "template": "chi-chu", "variants": ["A.", "B."]}).to_string()).unwrap();
        fs::write(run.join(".dubflow-work/status.json"), json!({"state": "COMPLETED", "videos": [{"index": 2, "ok": true, "duration_ms": 31000}]}).to_string()).unwrap();
        fs::write(run.join(".dubflow-work/narration-2.wav"), b"wav").unwrap();
        for name in ["video-2.mp4", "video-0.mp4", "video-123.mp4", "video-2.mkv"] {
            fs::write(run.join(name), b"video").unwrap();
        }
        let video = run.join("video-2.mp4").canonicalize().unwrap();
        assert_eq!(product_video(&video).map(|(_, number)| number), Some(2));
        for name in ["video-0.mp4", "video-123.mp4", "video-2.mkv"] {
            assert!(product_video(&run.join(name)).is_none(), "{name}");
        }
        let spec = product_spec(display_path(&video)).unwrap();
        assert_eq!((spec["variant"].as_u64(), spec["spec"]["variants"][1].as_str(), spec["duration_ms"].as_u64(), spec["narration"].as_bool()),
            (Some(2), Some("B."), Some(31000), Some(true)));
        assert!(product_spec(display_path(&run.join("video-2.mkv"))).is_err());
        let runs = product_runs(vec![display_path(&base)]);
        let ours: Vec<_> = runs.iter().filter(|value| value["dir"].as_str().is_some_and(|dir| dir.contains("dubflow-product-spec-"))).collect();
        assert_eq!(ours.len(), 1);
        assert_eq!((ours[0]["name"].as_str(), ours[0]["status"]["state"].as_str()), (Some("Nồi"), Some("COMPLETED")));
        // Another kind of run below the same folder is not listed.
        fs::create_dir_all(base.join("DubFlow Output/Tong hop - x/run-0123456789ab/.dubflow-work")).unwrap();
        fs::write(base.join("DubFlow Output/Tong hop - x/run-0123456789ab/.dubflow-work/spec.json"), b"{}").unwrap();
        assert_eq!(find_runs(&[display_path(&base)], "Ban hang - ", 10).iter().filter(|dir| dir.to_string_lossy().contains("dubflow-product-spec-")).count(), 1);
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn folder_title_is_a_short_windows_name() {
        assert_eq!(folder_title("Nồi chiên: 5.2L / \"Lock\"..."), "Nồi chiên 5.2L Lock");
        assert_eq!(folder_title("  ?? "), "san pham");
        assert_eq!(folder_title(&"a".repeat(80)).chars().count(), NAME_CHARS);
    }

    #[test]
    fn output_sits_below_dubflow_output_with_a_short_run_id() {
        let path = product_output(Path::new(r"D:\Videos"), "Nồi chiên", "seed");
        let text = path.to_string_lossy();
        assert!(text.starts_with(r"D:\Videos\DubFlow Output\Ban hang - Nồi chiên\run-"), "{text}");
        assert_eq!(path.file_name().unwrap().len(), "run-".len() + 12);
    }

    #[test]
    fn status_reads_only_product_runs() {
        let base = std::env::temp_dir().join(format!("dubflow-product-{}", std::process::id()));
        let run = base.join("DubFlow Output").join("Ban hang - x").join("run-1");
        fs::create_dir_all(run.join(".dubflow-work")).unwrap();
        fs::write(run.join(".dubflow-work").join("status.json"), br#"{"state":"RUNNING"}"#).unwrap();
        assert_eq!(product_status(run.to_string_lossy().into_owned()).unwrap()["state"], "RUNNING");
        assert!(product_status(base.to_string_lossy().into_owned()).is_err());
        assert!(product_status("relative".to_owned()).is_err());
        product_cancel(run.to_string_lossy().into_owned()).unwrap();
        assert!(run.join(".dubflow-work").join("cancel").is_file());
        assert!(product_cancel(base.to_string_lossy().into_owned()).is_err());
        fs::remove_dir_all(&base).unwrap();
    }
}
