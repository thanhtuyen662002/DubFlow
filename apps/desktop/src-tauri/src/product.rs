//! Product videos from the user's own photos and clips (ADR-0041): pick
//! files, send downscaled photos to the webview for Claude, and run
//! `python -m engine.dubflow.product`, which reports through a status file.

use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{control_root, display_path, fail, load_voice_catalog, version_root, CREATE_NO_WINDOW};

const IMAGE_EXTENSIONS: [&str; 5] = ["jpg", "jpeg", "png", "webp", "bmp"];
const CLIP_EXTENSIONS: [&str; 6] = ["mp4", "mov", "mkv", "webm", "avi", "m4v"];
const MUSIC_EXTENSIONS: [&str; 6] = ["mp3", "m4a", "aac", "wav", "ogg", "flac"];
const NAME_CHARS: usize = 40;

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
    cpu_only: bool,
    output_root: Option<String>,
}

/// A folder name Windows accepts, from the product name.
fn folder_title(name: &str) -> String {
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
    if request.visuals.is_empty() || request.visuals.len() > 30 || request.variants.is_empty() || request.variants.len() > 5 {
        return Err(fail("Cần 1–30 ảnh hoặc clip và 1–5 kịch bản"));
    }
    if !(0.0..=1.0).contains(&request.music_volume) {
        return Err(fail("Âm lượng nhạc nền không hợp lệ"));
    }
    let visuals = request.visuals.iter()
        .map(|value| local_file(value, &[&IMAGE_EXTENSIONS[..], &CLIP_EXTENSIONS[..]].concat()))
        .collect::<Result<Vec<_>, _>>()?;
    let music = request.music.as_deref().map(|value| local_file(value, &MUSIC_EXTENSIONS)).transpose()?;
    let base = match request.output_root {
        Some(value) => {
            let base = PathBuf::from(value);
            if !base.is_absolute() || !base.is_dir() {
                return Err(fail("Thư mục lưu kết quả không tồn tại; hãy chọn lại"));
            }
            base
        }
        None => visuals[0].parent().ok_or_else(|| fail("Không xác định được thư mục lưu"))?.to_path_buf(),
    };
    let started = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let output = product_output(&base, &request.name, &format!("{started}:{}", request.name));
    let work = output.join(".dubflow-work");
    fs::create_dir_all(&work).map_err(|error| fail(format!("Không tạo được thư mục kết quả: {error}")))?;
    let spec = json!({
        "schema_version": 1, "name": request.name, "price": request.price, "voice_id": request.voice_id,
        "variants": request.variants, "visuals": visuals, "music": music, "music_volume": request.music_volume,
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

#[cfg(test)]
mod tests {
    use super::*;

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
        fs::remove_dir_all(&base).unwrap();
    }
}
