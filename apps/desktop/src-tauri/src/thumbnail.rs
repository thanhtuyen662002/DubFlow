//! Thumbnail (Issue #901): the picture a video is posted with, made in
//! Chỉnh sửa from a frame of the video or a picture of the user's, with text
//! drawn on it in the window. The window draws it; this module only reads the
//! frame or picture and writes the finished PNG or JPEG. Nothing here renders
//! the video, and nothing leaves the machine.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
use crate::CREATE_NO_WINDOW;
use crate::{display_path, fail};

/// The frame or picture is read at most this wide (a 1280×720 thumbnail may zoom in on it).
pub const FRAME_MAX_WIDTH: u32 = 1920;
/// A finished thumbnail is at most this big (YouTube takes 2 MB; a PNG of a busy 4K frame stays well under this).
pub const MAX_THUMBNAIL_BYTES: usize = 40 * 1024 * 1024;

/// The frame at `time` seconds of a video, or the picture itself, as a JPEG at most
/// [`FRAME_MAX_WIDTH`] wide, for the thumbnail's background.
#[tauri::command]
pub async fn thumbnail_frame(path: String, time: f64) -> Result<tauri::ipc::Response, String> {
    let file = crate::overlay_media(&path)?;
    if !time.is_finite() || time < 0.0 {
        return Err(fail("Thời điểm không hợp lệ"));
    }
    let still = crate::is_still(&file);
    let ffmpeg = crate::edit::media_tool("ffmpeg.exe")?;
    let jpeg = tauri::async_runtime::spawn_blocking(move || -> Result<Vec<u8>, String> {
        let mut command = Command::new(ffmpeg);
        command.args(["-nostdin", "-v", "error"]);
        if !still {
            command.args(["-ss", &format!("{time:.3}")]);
        }
        command.arg("-i").arg(&file).args(frame_args())
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let output = command.output().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
        if !output.status.success() || output.stdout.len() < 128 {
            return Err(fail(if still { "Không đọc được ảnh này" } else { "Không lấy được khung hình ở thời điểm này" }));
        }
        Ok(output.stdout)
    }).await.map_err(|_| fail("Không lấy được khung hình"))??;
    Ok(tauri::ipc::Response::new(jpeg))
}

/// One frame, no wider than [`FRAME_MAX_WIDTH`], as a good JPEG on stdout.
fn frame_args() -> Vec<String> {
    ["-frames:v", "1", "-vf", &format!("scale='min({FRAME_MAX_WIDTH},iw)':-2"), "-q:v", "2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        .iter().map(|value| (*value).to_owned()).collect()
}

/// "png" or "jpeg" (also "jpg"); the file's extension.
fn extension_of(format: &str) -> Option<&'static str> {
    match format.trim().to_ascii_lowercase().as_str() {
        "png" => Some("png"),
        "jpeg" | "jpg" => Some("jpg"),
        _ => None,
    }
}

/// Whether `bytes` are a whole image of that extension (the window's canvas
/// made them; anything else is refused before it touches the disk).
fn looks_like(bytes: &[u8], extension: &str) -> bool {
    match extension {
        "png" => bytes.len() > 64 && bytes.starts_with(b"\x89PNG\r\n\x1a\n") && bytes.ends_with(&[0xAE, 0x42, 0x60, 0x82]),
        "jpg" => bytes.len() > 64 && bytes.starts_with(&[0xFF, 0xD8, 0xFF]) && bytes.ends_with(&[0xFF, 0xD9]),
        _ => false,
    }
}

/// `<name>.<ext>` in `folder`, else `<name> (2).<ext>`…: never a file that exists.
fn free_target(folder: &Path, name: &str, extension: &str) -> Result<PathBuf, String> {
    (1..=999).map(|number| folder.join(crate::source::numbered_file_name(name, extension, number)))
        .find(|file| fs::symlink_metadata(file).is_err() && fs::symlink_metadata(crate::edit::partial_of(file)).is_err())
        .ok_or_else(|| fail("Thư mục đã có quá nhiều file cùng tên"))
}

/// Writes the image beside its `.partial`, synced, then gives it its name; an
/// existing file is never replaced. Returns the new file.
fn write_new(folder: &Path, name: &str, extension: &str, bytes: &[u8]) -> Result<PathBuf, String> {
    let target = free_target(folder, name, extension)?;
    let partial = crate::edit::partial_of(&target);
    let written = (|| -> std::io::Result<()> {
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&partial)?;
        file.write_all(bytes)?;
        file.sync_all()
    })();
    if written.is_err() {
        let _ = fs::remove_file(&partial);
        return Err(fail("Không ghi được thumbnail vào thư mục này"));
    }
    // A file made under that name meanwhile is kept: try the next free name.
    if fs::symlink_metadata(&target).is_ok() {
        let _ = fs::remove_file(&partial);
        return write_new(folder, name, extension, bytes);
    }
    fs::rename(&partial, &target).map_err(|_| {
        let _ = fs::remove_file(&partial);
        fail("Không ghi được thumbnail vào thư mục này")
    })?;
    Ok(target)
}

/// Lưu thumbnail: the PNG or JPEG the window drew (raw body; headers
/// `x-folder`, `x-name` and `x-format`, URI-encoded) saved as a new file in
/// that folder. Returns its path.
#[tauri::command]
pub async fn thumbnail_save(request: tauri::ipc::Request<'_>) -> Result<String, String> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else {
        return Err(fail("Ảnh thumbnail không hợp lệ"));
    };
    let header = |name: &str| request.headers().get(name).and_then(|value| value.to_str().ok()).and_then(crate::edit::percent_decoded);
    let (Some(folder), Some(name), Some(format)) = (header("x-folder"), header("x-name"), header("x-format")) else {
        return Err(fail("Chưa chọn nơi lưu thumbnail"));
    };
    let extension = extension_of(&format).ok_or_else(|| fail("Chỉ lưu được PNG hoặc JPEG"))?;
    if bytes.len() > MAX_THUMBNAIL_BYTES || !looks_like(bytes, extension) {
        return Err(fail("Ảnh thumbnail không hợp lệ"));
    }
    let name = thumbnail_name(&name);
    if name.is_empty() {
        return Err(fail("Hãy đặt tên cho thumbnail"));
    }
    let folder = crate::edit::target_folder(&folder)?;
    let bytes = bytes.clone();
    let file = tauri::async_runtime::spawn_blocking(move || write_new(&folder, &name, extension, &bytes))
        .await.map_err(|_| fail("Không lưu được thumbnail"))??;
    if let Ok(mut saved) = SAVED.lock() {
        saved.push(file.clone());
    }
    Ok(display_path(&file))
}

/// The thumbnails saved since DubFlow opened: the only files "Mở thư mục" shows.
static SAVED: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());

/// Mở thư mục: Explorer with a thumbnail saved here selected.
#[tauri::command]
pub fn thumbnail_reveal(path: String) -> Result<(), String> {
    let known = SAVED.lock().map(|saved| saved.iter().any(|file| display_path(file) == path)).unwrap_or(false);
    if !known || !Path::new(&path).is_file() {
        return Err(fail("Chỉ mở được thumbnail vừa lưu"));
    }
    Command::new("explorer.exe").arg(format!("/select,{path}")).spawn()
        .map_err(|error| fail(format!("Không mở được thư mục: {error}")))?;
    Ok(())
}

/// The typed name without an image extension, made safe for Windows; empty when nothing usable was typed.
fn thumbnail_name(typed: &str) -> String {
    let trimmed = typed.trim();
    let lower = trimmed.to_ascii_lowercase();
    let stem = [".png", ".jpg", ".jpeg"].iter()
        .find(|suffix| lower.ends_with(**suffix) && trimmed.is_char_boundary(trimmed.len() - suffix.len()))
        .map_or(trimmed, |suffix| &trimmed[..trimmed.len() - suffix.len()]);
    if stem.trim().is_empty() { String::new() } else { crate::source::safe_file_name(stem) }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png() -> Vec<u8> {
        let mut bytes = b"\x89PNG\r\n\x1a\n".to_vec();
        bytes.extend([0u8; 80]);
        bytes.extend([0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82]);
        bytes
    }

    #[test]
    fn only_a_whole_png_or_jpeg_is_saved() {
        assert_eq!(extension_of("PNG"), Some("png"));
        assert_eq!(extension_of("jpeg"), Some("jpg"));
        assert_eq!(extension_of("webp"), None);
        assert!(looks_like(&png(), "png"));
        assert!(!looks_like(&png()[..60], "png"));
        assert!(!looks_like(&png(), "jpg"));
        let mut jpeg = vec![0xFF, 0xD8, 0xFF, 0xE0];
        jpeg.extend([0u8; 80]);
        jpeg.extend([0xFF, 0xD9]);
        assert!(looks_like(&jpeg, "jpg"));
        jpeg.pop();
        assert!(!looks_like(&jpeg, "jpg"), "a JPEG cut short is refused");
    }

    #[test]
    fn a_thumbnail_never_replaces_a_file() {
        let dir = std::env::temp_dir().join(format!("dubflow-thumb-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("Phim hay.png"), b"the user's own picture").unwrap();
        let first = write_new(&dir, "Phim hay", "png", &png()).unwrap();
        assert_eq!(first, dir.join("Phim hay (2).png"));
        assert_eq!(fs::read(dir.join("Phim hay.png")).unwrap(), b"the user's own picture");
        let second = write_new(&dir, "Phim hay", "png", &png()).unwrap();
        assert_eq!(second, dir.join("Phim hay (3).png"));
        assert_eq!(fs::read(&second).unwrap(), png());
        assert!(fs::read_dir(&dir).unwrap().flatten().all(|entry| !entry.file_name().to_string_lossy().ends_with(".partial")));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_typed_name_loses_its_extension_and_unsafe_letters() {
        assert_eq!(thumbnail_name("  Bìa video.PNG "), "Bìa video");
        assert_eq!(thumbnail_name("a.jpeg"), "a");
        assert_eq!(thumbnail_name(".png"), "");
        assert_eq!(thumbnail_name("   "), "");
        assert!(!thumbnail_name("a<b>:c").contains(['<', '>', ':']));
    }

    #[test]
    fn the_frame_is_read_large_enough_for_a_1280_thumbnail() {
        let args = frame_args().join(" ");
        assert!(args.contains("min(1920,iw)"), "{args}");
        assert!(args.contains("-frames:v 1"));
    }
}
