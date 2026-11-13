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

/// Why a file the user chose as a source is refused (Issue #816). Bán hàng and Tổng hợp use the same rule
/// ([`check_source`]); each names the file its own way ([`SourceRefusal::message`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SourceRefusal {
    /// Not absolute, or a `.` or `..` part.
    BadPath,
    /// Not one of the formats asked for.
    Unsupported,
    /// Nothing there (or it cannot be read).
    Missing,
    /// A symlink, a junction or anything else that is not a regular file (a folder).
    NotPlain,
    /// A folder on the way to it is a symlink or junction (or cannot be read to show it is not).
    LinkedFolder,
}

impl SourceRefusal {
    /// The Vietnamese message for a source called `kind` ("Video nguồn", "Ảnh", "Nhạc nền"…) whose file is `name`.
    /// For "Video nguồn" these are Tổng hợp's own texts, unchanged.
    pub(crate) fn message(self, kind: &str, name: &str) -> String {
        let lower = kind.to_lowercase();
        fail(match self {
            Self::BadPath => format!("Đường dẫn {lower} không hợp lệ: {name}"),
            Self::Unsupported => format!("Không mở được file: {name}"),
            Self::Missing => format!("Không tìm thấy {lower}: {name}"),
            Self::NotPlain => format!("{kind} phải là tệp thật trên máy, không phải lối tắt: {name}"),
            Self::LinkedFolder => format!("{kind} nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {name}"),
        })
    }
}

/// A redirect to somewhere else: a symlink, or on Windows a junction, mount point or other name-surrogate reparse point
/// (Rust std reports all of those as symlinks). Unlike `edit::is_link` (any reparse attribute, still used for folders),
/// a reparse point that is not a name surrogate is the file itself, not a way to another one: OneDrive Files-On-Demand
/// placeholders and deduplicated files carry one, and a user's OneDrive video must keep working as a source.
pub(crate) fn is_redirect(metadata: &fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}

/// An absolute path made only of a prefix, a root and plain names: no `.` or `..` part, read on the text itself
/// (`Path::components` hides an inner `.`).
fn plain_path(path: &Path) -> bool {
    use std::path::Component;
    let text = path.as_os_str().to_string_lossy();
    path.is_absolute()
        && !text.split(std::path::is_separator).any(|part| part == "." || part == "..")
        && path.components().all(|part| matches!(part, Component::Prefix(_) | Component::RootDir | Component::Normal(_)))
}

/// The one source rule (Issue #816) for every file the user chooses as a source in Bán hàng (photos, clips, music, a
/// background to keep, a photo to preview) and Tổng hợp (source videos, music):
/// - the path is absolute, made only of a prefix, a root and plain names: no `.` or `..` part, read on the text itself
///   (`Path::components` hides an inner `.`), so the path stored in the spec is the file the user picked;
/// - it has one of `extensions`;
/// - the path itself (not what it points to) is a regular file: never a symlink or junction;
/// - no folder on the way to it is a symlink or junction, so the file is where its path says it is.
///
/// A hard link is accepted: it is the file itself, with no other place it leads to (dedup and backup tools leave them).
/// There is no containment or prefix rule: a real file anywhere on the machine stays allowed.
///
/// The disk is read on every call and nothing is kept, so a later call sees a file that was swapped since. The worker
/// checks again when it loads the spec (`product/job.py::_plain_file`); FFmpeg opens the file by path after that, and
/// that last window cannot be closed (it is the user's own folder, outside the threat model).
pub(crate) fn check_source(path: &Path, extensions: &[&str]) -> Result<(), SourceRefusal> {
    if !plain_path(path) {
        return Err(SourceRefusal::BadPath);
    }
    if !has_extension(path, extensions) {
        return Err(SourceRefusal::Unsupported);
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| SourceRefusal::Missing)?;
    if is_redirect(&metadata) || !metadata.is_file() {
        return Err(SourceRefusal::NotPlain);
    }
    // A folder that cannot be read cannot be shown to be a plain folder: refused like a link.
    if !path.ancestors().skip(1).all(|folder| fs::symlink_metadata(folder).is_ok_and(|metadata| !is_redirect(&metadata))) {
        return Err(SourceRefusal::LinkedFolder);
    }
    Ok(())
}

/// [`check_source`] on a value from the UI, with the message for a source called `kind`; the path is returned as given.
pub(crate) fn plain_source_file(value: &str, kind: &str, extensions: &[&str]) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    check_source(&path, extensions).map_err(|refusal| refusal.message(kind, &source_name(&path)))?;
    Ok(path)
}

/// A source's file name for messages.
pub(crate) fn source_name(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// A request's photos and clips by the one source rule: a clip is a "Video nguồn", as in Tổng hợp.
fn product_visuals(values: &[String]) -> Result<Vec<PathBuf>, String> {
    let extensions = [&IMAGE_EXTENSIONS[..], &CLIP_EXTENSIONS[..]].concat();
    values.iter().map(|value| {
        let kind = if has_extension(Path::new(value), &CLIP_EXTENSIONS) { "Video nguồn" } else { "Ảnh" };
        plain_source_file(value, kind, &extensions)
    }).collect()
}

/// A background kept in the library (`library`, see [`add_to_library`]): `<sha256>.<extension>` directly in a library
/// folder that is a plain folder, the file itself a regular file (never a link). It is the app's own copy, made from a
/// file the user's own source rule accepted, so the folders above the library are not looked at: the data folder may
/// itself be reached through a junction (an install root on another drive), and its backgrounds must still preview and
/// render.
fn library_file(library: &Path, value: &str) -> Option<PathBuf> {
    let path = PathBuf::from(value);
    let (id, extension) = path.file_name()?.to_str()?.split_once('.')?;
    let known = background_id(id).is_ok() && IMAGE_EXTENSIONS.contains(&extension);
    let regular = fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_file() && !is_redirect(&metadata));
    (known && plain_path(&path) && path.parent() == Some(library) && plain_dir(library) && regular).then_some(path)
}

/// An image the UI names that is either a library background ([`library_file`]) or a file of the user's, by the one
/// source rule (Issue #816) with the messages for a source called `kind`. `library` is `None` when the data folder is
/// unknown: then only the source rule applies.
fn library_or_source(library: Option<&Path>, value: &str, kind: &str) -> Result<PathBuf, String> {
    match library.and_then(|dir| library_file(dir, value)) {
        Some(path) => Ok(path),
        None => plain_source_file(value, kind, &IMAGE_EXTENSIONS),
    }
}

/// A Bán hàng request's photos and clips, music and background, as checked by [`product_sources`].
type ProductSources = (Vec<PathBuf>, Option<PathBuf>, Option<PathBuf>);

/// The files a Bán hàng request reads, checked before any run folder is made (a refused file makes no run): photos and
/// clips, music and the background. The background the UI sends is a library entry; a run made again from the edit
/// page may keep one the library no longer lists, which then has to pass the user's source rule like any other file.
fn product_sources(visuals: &[String], music: Option<&str>, background: Option<&str>, library: Option<&Path>) -> Result<ProductSources, String> {
    let visuals = product_visuals(visuals)?;
    let music = music.map(|value| plain_source_file(value, "Nhạc nền", &MUSIC_EXTENSIONS)).transpose()?;
    let background = background.map(|value| library_or_source(library, value, "Ảnh nền")).transpose()?;
    Ok((visuals, music, background))
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
    // a photo of the user's by the one source rule, or a background from the library (the app's own copy)
    let image = library_or_source(library_dir().ok().as_deref(), &path, "Ảnh")?;
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
    /// Tối ưu hậu kỳ tự động: "off" (or absent) keeps the old render exactly.
    #[serde(default)]
    mastering: Option<String>,
    /// The AI route that may look at keyframes when mastering is on; its key goes in the job's environment.
    #[serde(default)]
    vision_ai: Option<crate::AiTranslation>,
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

/// A plain folder: there, a folder, and not a symlink, junction or other reparse point.
pub(crate) fn plain_dir(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir() && !crate::edit::is_link(&metadata))
}

/// The save folder the user chose: an existing absolute folder that is not a link. Like Tổng hợp, a link or junction
/// is refused, since it could lead outside (rc8 P2-N7).
fn chosen_base(value: &str) -> Result<PathBuf, String> {
    let base = PathBuf::from(value);
    if !base.is_absolute() || !base.is_dir() {
        return Err(fail("Thư mục lưu kết quả không tồn tại; hãy chọn lại"));
    }
    if !plain_dir(&base) {
        return Err(fail("Thư mục lưu kết quả là liên kết (junction/symlink); hãy chọn một thư mục thường"));
    }
    Ok(base)
}

/// A product run's folder: below "DubFlow Output", with a work folder. Every folder from "DubFlow Output" down to
/// the run, and the work folder, is a plain folder (never a link that could lead outside), and the run resolves to a
/// place inside "DubFlow Output".
fn product_dir(value: &str) -> Result<PathBuf, String> {
    use std::path::Component;
    let bad = || fail("Thư mục video bán hàng không hợp lệ");
    let dir = PathBuf::from(value);
    if !dir.is_absolute() || dir.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)) {
        return Err(bad());
    }
    let root = dir.ancestors().skip(1).find(|part| part.file_name().is_some_and(|name| name == "DubFlow Output")).ok_or_else(bad)?;
    let chain = dir.ancestors().take_while(|part| *part != root).chain([root]);
    if !chain.into_iter().all(plain_dir) || !plain_dir(&dir.join(".dubflow-work")) {
        return Err(bad());
    }
    let (real_root, real_dir) = (root.canonicalize().map_err(|_| bad())?, dir.canonicalize().map_err(|_| bad())?);
    if real_dir == real_root || !real_dir.starts_with(&real_root) {
        return Err(bad());
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
    // Issue #816: the same source rule as Tổng hợp, before any run folder is made (a refused file makes no run). The
    // spec keeps the checked paths; the worker checks them again when it reads the spec.
    let (visuals, music, background) =
        product_sources(&visuals, request.music.as_deref(), request.background.as_deref(), library_dir().ok().as_deref())?;
    let reuse = request.reuse_audio_from.as_deref().map(product_dir).transpose()?;
    let beside_reuse = reuse.as_deref().and_then(|dir| dir.ancestors().find(|part| part.file_name().is_some_and(|name| name == "DubFlow Output")))
        .and_then(Path::parent).map(Path::to_path_buf);
    let base = match request.output_root {
        Some(value) => chosen_base(&value)?,
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
    crate::edit::record_run("product", &output);
    let mastering = crate::mastering::options(request.mastering.as_deref())?;
    let mut spec = json!({
        "schema_version": 1, "name": request.name, "price": request.price, "voice_id": request.voice_id,
        "variants": request.variants, "visuals": visuals, "music": music, "music_volume": request.music_volume, "background": background,
        "template": template, "speed": speed, "features": request.features, "focus": {"x": focus.0, "y": focus.1},
        "reuse_audio_from": reuse.as_deref().map(display_path),
    });
    // Mastering off sends nothing new: the spec is the one the old render read.
    let mut key = None;
    if let Some(options) = mastering {
        spec["mastering"] = options;
        if let Some(ai) = &request.vision_ai {
            spec["editorial"] = json!({"ai": crate::mastering::vision_route(ai)?});
            key = crate::ai_keys::job_key(&ai.provider)?.filter(|key| !key.trim().is_empty());
        }
    }
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).map_err(|error| fail(error.to_string()))?)
        .map_err(|error| fail(format!("Không ghi được yêu cầu render: {error}")))?;
    fs::write(work.join("status.json"), r#"{"schema_version":1,"state":"RUNNING","done":0,"total":0,"message":"Đang khởi động…","videos":[]}"#)
        .map_err(|error| fail(format!("Không ghi được trạng thái: {error}")))?;
    spawn_product(&root, &spec_path, &output, request.cpu_only, key)?;
    Ok(display_path(&output))
}

/// `python -m engine.dubflow.product` on a written spec; the AI key (if any) only in its environment.
fn spawn_product(root: &Path, spec_path: &Path, output: &Path, cpu_only: bool, key: Option<String>) -> Result<(), String> {
    let media = root.join("runtime").join("media");
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.product", "--spec"]).arg(spec_path)
        .arg("--output-dir").arg(output)
        .arg("--app-root").arg(root.join("app"))
        .arg("--model-root").arg(control_root()?.join("models"))
        .arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null());
    let work = output.join(".dubflow-work");
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
    let mut child = command.spawn().map_err(|error| fail(format!("Không chạy được bộ tạo video: {error}")))?;
    // a job that ends without writing its last state (a status file held open, a crash) is settled, never left RUNNING
    std::thread::spawn(move || {
        let exited_ok = child.wait().is_ok_and(|status| status.success());
        crate::engine_run::settle_after_exit(&work, exited_ok, "Bộ tạo video đã dừng. Hãy thử lại.");
    });
    Ok(())
}

/// Render one video of a mastered run again, in place, with the review's pins and exclusions: the narration and
/// the other videos are kept (`only`), the analysis and the AI's look at the footage are reused.
#[tauri::command]
pub fn product_rerender(output_dir: String, video: u32, cpu_only: bool) -> Result<(), String> {
    let dir = product_dir(&output_dir)?;
    let work = dir.join(".dubflow-work");
    rerender_in(&dir, &work, video, |spec_path, key| spawn_product(&version_root()?, spec_path, &dir, cpu_only, key))
}

/// The terminal states a run can be rendered again from; anything else (running, unreadable) is refused.
const FINISHED: [&str; 3] = ["COMPLETED", "FAILED", "CANCELLED"];
const RERENDER_SPEC: &str = "spec-rerender.json";

/// `product_rerender` without the process: the run's own request (`spec.json`) is never changed, the attempt's
/// request goes to `spec-rerender.json`, and the status is put back as it was when the job cannot start.
fn rerender_in(dir: &Path, work: &Path, video: u32, start: impl FnOnce(&Path, Option<String>) -> Result<(), String>) -> Result<(), String> {
    let status_path = work.join("status.json");
    let before = fs::read(&status_path).ok().filter(|bytes| bytes.len() as u64 <= SPEC_LIMIT)
        .ok_or_else(|| fail("Không đọc được trạng thái của lần tạo này; không render lại được"))?;
    let status = serde_json::from_slice::<Value>(&before).ok().filter(Value::is_object)
        .ok_or_else(|| fail("Trạng thái của lần tạo này bị hỏng; không render lại được"))?;
    if status["state"] == "RUNNING" {
        return Err(fail("Video này đang được tạo; hãy chờ xong rồi render lại"));
    }
    if !status["state"].as_str().is_some_and(|state| FINISHED.contains(&state)) || !status["videos"].is_array() {
        return Err(fail("Trạng thái của lần tạo này không rõ; không render lại được"));
    }
    let mut spec = read_small_json(&work.join("spec.json"), SPEC_LIMIT).filter(|spec| spec["schema_version"] == 1)
        .ok_or_else(|| fail("Không đọc được yêu cầu render của video này"))?;
    let count = spec["variants"].as_array().map_or(0, Vec::len);
    if video == 0 || video as usize > count {
        return Err(fail("Số video không hợp lệ"));
    }
    if spec.get("mastering").map_or(true, Value::is_null) {
        return Err(fail("Video này không dùng tối ưu hậu kỳ; hãy tạo lại từ đầu"));
    }
    spec["only"] = json!([video]);
    spec["reuse_audio_from"] = json!(display_path(dir));
    let key = match spec["editorial"]["ai"]["provider"].as_str() {
        Some(provider) => crate::ai_keys::job_key(provider)?.filter(|key| !key.trim().is_empty()),
        None => None,
    };
    let attempt = work.join(RERENDER_SPEC);
    write_replace(&attempt, &serde_json::to_vec(&spec).map_err(|error| fail(error.to_string()))?)
        .map_err(|error| fail(format!("Không ghi được yêu cầu render: {error}")))?;
    let running = json!({"schema_version": 1, "state": "RUNNING", "done": 0, "total": count, "message": "Đang render lại…",
                         "videos": status["videos"]});
    write_replace(&status_path, &serde_json::to_vec(&running).map_err(|error| fail(error.to_string()))?)
        .map_err(|error| fail(format!("Không ghi được trạng thái: {error}")))?;
    let _ = fs::remove_file(work.join("cancel"));
    if let Err(error) = start(&attempt, key) {
        // nothing started: the run is as it was before the click
        let _ = write_replace(&status_path, &before);
        return Err(error);
    }
    Ok(())
}

/// Writes through a temporary file in the same folder, so a reader never sees half a file.
fn write_replace(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let temp = path.with_extension("json.tmp");
    fs::write(&temp, bytes)?;
    crate::engine_run::replace_retrying(&temp, path)
}

/// A product run's folder, for the review panel (`mastering.rs`).
pub(crate) fn run_dir(value: &str) -> Result<PathBuf, String> {
    product_dir(value)
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
    // its bytes are copied into the library: the user's real file, never what a link leads to (Issue #816)
    let source = plain_source_file(&path, "Ảnh nền", &IMAGE_EXTENSIONS)?;
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
pub(crate) mod tests {
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

    fn mastered_run(name: &str) -> (PathBuf, PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("dubflow-rerender-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let run = base.join("DubFlow Output").join("Ban hang - Nồi").join("run-0123456789ab");
        let work = run.join(".dubflow-work");
        fs::create_dir_all(&work).unwrap();
        fs::write(work.join("spec.json"), json!({"schema_version": 1, "name": "Nồi", "variants": ["A.", "B."],
                                                 "mastering": {"schema_version": 1, "level": "balanced"}}).to_string()).unwrap();
        fs::write(work.join("status.json"), json!({"schema_version": 1, "state": "COMPLETED",
                                                   "videos": [{"index": 1, "ok": true}, {"index": 2, "ok": true}]}).to_string()).unwrap();
        (base, run, work)
    }

    #[test]
    fn rerender_writes_its_own_request_and_puts_the_status_back_when_nothing_starts() {
        let (base, run, work) = mastered_run("spawn");
        let (spec, status) = (fs::read(work.join("spec.json")).unwrap(), fs::read(work.join("status.json")).unwrap());
        let failed = rerender_in(&run, &work, 2, |_, _| Err(fail("không chạy được")));
        assert!(failed.is_err());
        assert_eq!(fs::read(work.join("spec.json")).unwrap(), spec, "the run's own request is never narrowed");
        assert_eq!(fs::read(work.join("status.json")).unwrap(), status, "nothing started: the status is as before");
        let mut seen = None;
        rerender_in(&run, &work, 2, |path, _| { seen = Some(path.to_path_buf()); Ok(()) }).unwrap();
        let attempt: Value = serde_json::from_slice(&fs::read(seen.unwrap()).unwrap()).unwrap();
        assert_eq!(attempt["only"], json!([2]));
        assert_eq!(fs::read(work.join("spec.json")).unwrap(), spec);
        let running: Value = serde_json::from_slice(&fs::read(work.join("status.json")).unwrap()).unwrap();
        assert_eq!(running["state"], "RUNNING");
        assert_eq!(running["videos"].as_array().unwrap().len(), 2, "the other videos stay listed");
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn rerender_refuses_an_unknown_running_or_unreadable_status() {
        let (base, run, work) = mastered_run("status");
        for bad in [None, Some("not json"), Some(r#"{"state":"RUNNING","videos":[]}"#), Some(r#"{"state":"COMPLETED"}"#),
                    Some(r#"{"state":"PAUSED","videos":[]}"#)] {
            match bad {
                None => { let _ = fs::remove_file(work.join("status.json")); }
                Some(text) => fs::write(work.join("status.json"), text).unwrap(),
            }
            let before = fs::read(work.join("status.json")).ok();
            assert!(rerender_in(&run, &work, 1, |_, _| panic!("must not start")).is_err(), "{bad:?}");
            assert_eq!(fs::read(work.join("status.json")).ok(), before);
            assert!(!work.join(RERENDER_SPEC).exists());
        }
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_run_reached_through_a_link_is_refused() {
        let (base, run, _) = mastered_run("link");
        assert_eq!(product_dir(&run.to_string_lossy()).unwrap(), run);
        assert!(product_dir(&format!("{}\\..\\run-0123456789ab", run.display())).is_err());
        let outside = base.join("outside");
        fs::create_dir_all(outside.join(".dubflow-work")).unwrap();
        fs::write(outside.join(".dubflow-work/status.json"), "marker").unwrap();
        let alias = run.parent().unwrap().join("run-alias");
        let linked = link_dir(&outside, &alias);
        let work_alias = run.parent().unwrap().join("run-work-alias");
        fs::create_dir_all(&work_alias).unwrap();
        let work_linked = link_dir(&outside.join(".dubflow-work"), &work_alias.join(".dubflow-work"));
        #[cfg(windows)]
        assert!(linked && work_linked, "junctions need no privilege on Windows");
        if linked {
            assert!(product_dir(&alias.to_string_lossy()).is_err(), "a run that is a link");
        }
        if work_linked {
            assert!(product_dir(&work_alias.to_string_lossy()).is_err(), "a work folder that is a link");
        }
        assert_eq!(fs::read(outside.join(".dubflow-work/status.json")).unwrap(), b"marker");
        // rc8 P2-N7: a save folder that is a junction is refused before anything is written behind it
        assert!(chosen_base(&outside.to_string_lossy()).is_ok());
        let save_alias = base.join("save-alias");
        if link_dir(&outside, &save_alias) {
            assert!(chosen_base(&save_alias.to_string_lossy()).is_err(), "a save folder that is a link");
        }
        assert!(chosen_base("relative\\folder").is_err());
        let _ = fs::remove_dir_all(&base);
    }

    fn source_base(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("dubflow-source-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    /// A file symlink: a plain symlink on unix; on Windows it needs Developer Mode or an elevated process, so false
    /// (and the caller skips, saying so) when the system refuses.
    pub(crate) fn link_file(target: &Path, link: &Path) -> bool {
        #[cfg(windows)]
        let made = std::os::windows::fs::symlink_file(target, link);
        #[cfg(unix)]
        let made = std::os::unix::fs::symlink(target, link);
        if let Err(error) = &made {
            eprintln!("skipped: this system does not let the test make a file symlink ({error})");
        }
        made.is_ok()
    }

    fn refusal(path: &Path, kind: &str) -> String {
        plain_source_file(&path.to_string_lossy(), kind, &CLIP_EXTENSIONS).unwrap_err()
    }

    #[test]
    fn a_real_file_anywhere_is_a_source_and_a_hard_link_too() {
        // Issue #816: no containment rule, a real file in the temp folder (outside any DubFlow Output) is the user's file
        let base = source_base("real");
        let clip = base.join("a.mp4");
        fs::write(&clip, b"clip").unwrap();
        assert_eq!(plain_source_file(&clip.to_string_lossy(), "Video nguồn", &CLIP_EXTENSIONS).unwrap(), clip);
        // a hard link is the file itself (no privilege needed on NTFS or unix)
        let hard = base.join("hard.mp4");
        fs::hard_link(&clip, &hard).unwrap();
        assert!(plain_source_file(&hard.to_string_lossy(), "Video nguồn", &CLIP_EXTENSIONS).is_ok());
        assert_eq!(product_visuals(&[clip.to_string_lossy().into_owned(), hard.to_string_lossy().into_owned()]).unwrap(), [clip.clone(), hard]);
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn traversal_relative_missing_folders_and_other_formats_are_refused() {
        let base = source_base("bad");
        let clip = base.join("a.mp4");
        fs::write(&clip, b"clip").unwrap();
        fs::create_dir_all(base.join("sub")).unwrap();
        // `sub/../a.mp4` names a file that exists: only the `..` refuses it
        let traversal = base.join("sub").join("..").join("a.mp4");
        assert!(traversal.is_file());
        assert_eq!(refusal(&traversal, "Video nguồn"), "Đường dẫn video nguồn không hợp lệ: a.mp4");
        assert_eq!(refusal(Path::new("a.mp4"), "Video nguồn"), "Đường dẫn video nguồn không hợp lệ: a.mp4");
        assert_eq!(check_source(&PathBuf::from(format!("{}{}.{}a.mp4", base.display(), std::path::MAIN_SEPARATOR, std::path::MAIN_SEPARATOR)), &CLIP_EXTENSIONS),
                   Err(SourceRefusal::BadPath), "a `.` part, read on the text");
        assert_eq!(refusal(&base.join("missing.mp4"), "Video nguồn"), "Không tìm thấy video nguồn: missing.mp4");
        fs::create_dir_all(base.join("folder.mp4")).unwrap();
        assert_eq!(refusal(&base.join("folder.mp4"), "Video nguồn"), "Video nguồn phải là tệp thật trên máy, không phải lối tắt: folder.mp4");
        fs::write(base.join("a.flv"), b"clip").unwrap();
        assert_eq!(refusal(&base.join("a.flv"), "Video nguồn"), "Không mở được file: a.flv");
        assert_eq!(product_visuals(&[traversal.to_string_lossy().into_owned()]).unwrap_err(), "Đường dẫn video nguồn không hợp lệ: a.mp4");
        let photo = base.join("sub").join("..").join("b.jpg");
        fs::write(base.join("b.jpg"), b"jpg").unwrap();
        assert_eq!(product_visuals(&[photo.to_string_lossy().into_owned()]).unwrap_err(), "Đường dẫn ảnh không hợp lệ: b.jpg");
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_linked_photo_clip_or_music_is_refused() {
        let base = source_base("link");
        let (clip, photo, music) = (base.join("a.mp4"), base.join("a.jpg"), base.join("a.mp3"));
        for file in [&clip, &photo, &music] {
            fs::write(file, b"real").unwrap();
        }
        if !link_file(&clip, &base.join("clip-link.mp4")) {
            let _ = fs::remove_dir_all(&base);
            return;
        }
        assert!(link_file(&photo, &base.join("photo-link.jpg")) && link_file(&music, &base.join("music-link.mp3")));
        assert_eq!(product_visuals(&[clip.to_string_lossy().into_owned(), base.join("clip-link.mp4").to_string_lossy().into_owned()]).unwrap_err(),
                   "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mp4", "Tổng hợp's text for a clip");
        assert_eq!(product_visuals(&[base.join("photo-link.jpg").to_string_lossy().into_owned()]).unwrap_err(),
                   "Ảnh phải là tệp thật trên máy, không phải lối tắt: photo-link.jpg");
        assert_eq!(plain_source_file(&base.join("music-link.mp3").to_string_lossy(), "Nhạc nền", &MUSIC_EXTENSIONS).unwrap_err(),
                   "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: music-link.mp3");
        assert_eq!(fs::read(&clip).unwrap(), b"real", "the source is only read");
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_file_below_a_linked_folder_is_refused() {
        let base = source_base("folder");
        let real = base.join("real");
        fs::create_dir_all(real.join("deeper")).unwrap();
        fs::write(real.join("a.mp4"), b"clip").unwrap();
        fs::write(real.join("deeper").join("b.jpg"), b"jpg").unwrap();
        let alias = base.join("alias");
        let linked = link_dir(&real, &alias);
        #[cfg(windows)]
        assert!(linked, "junctions need no privilege on Windows");
        if linked {
            assert!(alias.join("a.mp4").is_file(), "the file is reachable through the link");
            assert_eq!(refusal(&alias.join("a.mp4"), "Video nguồn"),
                       "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: a.mp4");
            assert_eq!(product_visuals(&[alias.join("deeper").join("b.jpg").to_string_lossy().into_owned()]).unwrap_err(),
                       "Ảnh nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.jpg", "any folder on the way");
        }
        // the same file at its real place is the user's file
        assert!(plain_source_file(&real.join("a.mp4").to_string_lossy(), "Video nguồn", &CLIP_EXTENSIONS).is_ok());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn the_rule_reads_the_disk_on_every_call() {
        // S6 (TOCTOU): nothing is cached, so a file swapped after a check is seen by the next one
        let base = source_base("swap");
        let clip = base.join("a.mp4");
        fs::write(&clip, b"clip").unwrap();
        let value = clip.to_string_lossy().into_owned();
        assert!(plain_source_file(&value, "Video nguồn", &CLIP_EXTENSIONS).is_ok());
        fs::remove_file(&clip).unwrap();
        fs::create_dir(&clip).unwrap();
        assert_eq!(check_source(&clip, &CLIP_EXTENSIONS), Err(SourceRefusal::NotPlain), "now a folder");
        fs::remove_dir(&clip).unwrap();
        fs::write(base.join("other.mp4"), b"other").unwrap();
        if link_file(&base.join("other.mp4"), &clip) {
            assert_eq!(check_source(&clip, &CLIP_EXTENSIONS), Err(SourceRefusal::NotPlain), "now a link");
        }
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_photo_preview_or_a_background_to_keep_follows_the_rule() {
        // R2: product_image accepted `..`, links and junctions; product_background_add copied a link's target
        let base = source_base("preview");
        fs::create_dir_all(base.join("sub")).unwrap();
        fs::write(base.join("a.jpg"), b"jpg").unwrap();
        let traversal = base.join("sub").join("..").join("a.jpg").to_string_lossy().into_owned();
        let preview = tauri::async_runtime::block_on(product_image(traversal.clone(), None)).err();
        assert_eq!(preview.as_deref(), Some("Đường dẫn ảnh không hợp lệ: a.jpg"));
        let keep = tauri::async_runtime::block_on(product_background_add(traversal)).err();
        assert_eq!(keep.as_deref(), Some("Đường dẫn ảnh nền không hợp lệ: a.jpg"));
        let alias = base.join("alias");
        if link_dir(&base.join("sub"), &alias) {
            fs::write(base.join("sub").join("b.jpg"), b"jpg").unwrap();
            let preview = tauri::async_runtime::block_on(product_image(alias.join("b.jpg").to_string_lossy().into_owned(), None)).err();
            assert_eq!(preview.as_deref(), Some("Ảnh nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.jpg"));
        }
        let _ = fs::remove_dir_all(&base);
    }

    fn text(path: &Path) -> String {
        path.to_string_lossy().into_owned()
    }

    #[test]
    fn a_render_checks_its_photos_clips_and_music_before_any_run() {
        // what product_render reads, through the helper it calls before a run folder is made
        let base = source_base("render");
        let (clip, photo, music) = (base.join("a.mp4"), base.join("a.jpg"), base.join("a.mp3"));
        for file in [&clip, &photo, &music] {
            fs::write(file, b"real").unwrap();
        }
        fs::create_dir_all(base.join("sub")).unwrap();
        assert_eq!(product_sources(&[text(&clip), text(&photo)], Some(&text(&music)), None, None).unwrap(),
                   (vec![clip.clone(), photo.clone()], Some(music.clone()), None), "real files anywhere are the user's files");
        let up = |name: &str| text(&base.join("sub").join("..").join(name));
        assert_eq!(product_sources(&[up("a.mp4")], None, None, None).unwrap_err(), "Đường dẫn video nguồn không hợp lệ: a.mp4");
        assert_eq!(product_sources(&[up("a.jpg")], None, None, None).unwrap_err(), "Đường dẫn ảnh không hợp lệ: a.jpg");
        assert_eq!(product_sources(&[text(&clip)], Some(&up("a.mp3")), None, None).unwrap_err(), "Đường dẫn nhạc nền không hợp lệ: a.mp3");
        if link_file(&clip, &base.join("clip-link.mp4")) {
            assert!(link_file(&photo, &base.join("photo-link.jpg")) && link_file(&music, &base.join("music-link.mp3")));
            assert_eq!(product_sources(&[text(&base.join("clip-link.mp4"))], None, None, None).unwrap_err(),
                       "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mp4");
            assert_eq!(product_sources(&[text(&base.join("photo-link.jpg"))], None, None, None).unwrap_err(),
                       "Ảnh phải là tệp thật trên máy, không phải lối tắt: photo-link.jpg");
            assert_eq!(product_sources(&[text(&clip)], Some(&text(&base.join("music-link.mp3"))), None, None).unwrap_err(),
                       "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: music-link.mp3");
        }
        let alias = base.join("alias");
        let linked = link_dir(&base.join("sub"), &alias);
        #[cfg(windows)]
        assert!(linked, "junctions need no privilege on Windows");
        if linked {
            for name in ["b.mp4", "b.jpg", "b.mp3"] {
                fs::write(base.join("sub").join(name), b"real").unwrap();
            }
            let below = |name: &str| text(&alias.join(name));
            assert_eq!(product_sources(&[below("b.mp4")], None, None, None).unwrap_err(),
                       "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.mp4");
            assert_eq!(product_sources(&[below("b.jpg")], None, None, None).unwrap_err(),
                       "Ảnh nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.jpg");
            assert_eq!(product_sources(&[text(&clip)], Some(&below("b.mp3")), None, None).unwrap_err(),
                       "Nhạc nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.mp3");
        }
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn a_background_is_a_library_copy_or_a_real_file_of_the_users() {
        // Issue #816 review: the render's background followed links and `..`; a library background must still preview
        // and render when the data folder is reached through a junction
        let base = source_base("background");
        let data = base.join("data");
        let library = data.join("backgrounds");
        fs::create_dir_all(&library).unwrap();
        fs::create_dir_all(base.join("sub")).unwrap();
        let id = "a".repeat(64);
        let kept = library.join(format!("{id}.png"));
        fs::write(&kept, b"png").unwrap();
        let clip = base.join("a.mp4");
        fs::write(&clip, b"clip").unwrap();
        let render = |background: &Path| product_sources(&[text(&clip)], None, Some(&text(background)), Some(&library)).map(|sources| sources.2);
        assert_eq!(render(&kept).unwrap(), Some(kept.clone()));
        assert_eq!(library_or_source(Some(&library), &text(&kept), "Ảnh").unwrap(), kept, "its preview");
        // a run made again from the edit page may keep a background the library no longer lists: the user's rule
        let own = base.join("own.png");
        fs::write(&own, b"png").unwrap();
        assert_eq!(render(&own).unwrap(), Some(own.clone()));
        assert_eq!(render(&base.join("sub").join("..").join("own.png")).unwrap_err(), "Đường dẫn ảnh nền không hợp lệ: own.png");
        assert_eq!(render(&library.join("..").join("backgrounds").join(format!("{id}.png"))).unwrap_err(),
                   format!("Đường dẫn ảnh nền không hợp lệ: {id}.png"), "`..` is never a library entry");
        let dotted = PathBuf::from(format!("{}{}.{}{id}.png", library.display(), std::path::MAIN_SEPARATOR, std::path::MAIN_SEPARATOR));
        assert!(library_file(&library, &text(&dotted)).is_none(), "nor a `.` part, read on the text");
        assert_eq!(render(&base.join("missing.png")).unwrap_err(), "Không tìm thấy ảnh nền: missing.png");
        assert!(library_file(&library, &text(&library.join(format!("{}.png", "b".repeat(64))))).is_none(), "nothing there");
        assert!(library_file(&library, &text(&library.join("own.png"))).is_none(), "not a library name");
        assert!(library_file(&library, &text(&data.join(format!("{id}.png")))).is_none(), "not in the library folder");
        if link_file(&own, &library.join(format!("{}.png", "c".repeat(64)))) {
            let entry = library.join(format!("{}.png", "c".repeat(64)));
            assert!(library_file(&library, &text(&entry)).is_none(), "a link in the library is not the app's copy");
            assert_eq!(render(&entry).unwrap_err(), format!("Ảnh nền phải là tệp thật trên máy, không phải lối tắt: {}.png", "c".repeat(64)));
            assert!(link_file(&own, &base.join("own-link.png")));
            assert_eq!(render(&base.join("own-link.png")).unwrap_err(), "Ảnh nền phải là tệp thật trên máy, không phải lối tắt: own-link.png");
            fs::remove_file(&entry).unwrap();
        }
        let alias = base.join("alias");
        let linked = link_dir(&data, &alias);
        #[cfg(windows)]
        assert!(linked, "junctions need no privilege on Windows");
        if linked {
            // the data folder reached through a junction: its library still previews and renders
            let (library, kept) = (alias.join("backgrounds"), alias.join("backgrounds").join(format!("{id}.png")));
            assert_eq!(library_or_source(Some(&library), &text(&kept), "Ảnh").unwrap(), kept);
            assert_eq!(product_sources(&[text(&clip)], None, Some(&text(&kept)), Some(&library)).unwrap().2, Some(kept.clone()));
            // ...but not when the library is not given, and a user's photo there is below a link
            assert_eq!(library_or_source(None, &text(&kept), "Ảnh").unwrap_err(),
                       format!("Ảnh nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {id}.png"));
            fs::write(data.join("photo.png"), b"png").unwrap();
            assert_eq!(library_or_source(Some(&library), &text(&alias.join("photo.png")), "Ảnh").unwrap_err(),
                       "Ảnh nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: photo.png");
            // a library folder that is itself a link is not the app's own folder
            let library_link = base.join("library-link");
            assert!(link_dir(&library, &library_link));
            assert!(library_file(&library_link, &text(&library_link.join(format!("{id}.png")))).is_none());
        }
        let _ = fs::remove_dir_all(&base);
    }

    /// A directory link: a junction on Windows (no privilege needed), a symlink elsewhere. False when the system refuses.
    pub(crate) fn link_dir(target: &Path, link: &Path) -> bool {
        #[cfg(windows)]
        {
            Command::new("cmd").args(["/C", "mklink", "/J"]).arg(link).arg(target).stdout(Stdio::null()).stderr(Stdio::null())
                .status().is_ok_and(|status| status.success())
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
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
