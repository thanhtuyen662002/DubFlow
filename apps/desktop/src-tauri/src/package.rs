//! Optional packaging of a finished dubbing export; original assets stay intact.
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{control_root, fail, load_voice_catalog, review_dir, version_root, VoiceCatalog, CREATE_NO_WINDOW};

static RUNNING: LazyLock<Mutex<HashSet<PathBuf>>> = LazyLock::new(|| Mutex::new(HashSet::new()));
const MUSIC: [&str; 6] = ["mp3", "m4a", "aac", "wav", "ogg", "flac"];

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PackageRequest {
    output_path: String,
    intro: String,
    outro: String,
    voice_id: String,
    speed: f64,
    music: Option<String>,
    music_volume: f64,
    background: String,
    cpu_only: bool,
}

fn package_dir(output_path: &str) -> Result<PathBuf, String> {
    let dir = review_dir(output_path)?;
    if Path::new(output_path).file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Hãy chọn video final_vi.mp4 đã hoàn tất"));
    }
    Ok(dir)
}

fn validate(request: &PackageRequest, dir: &Path, catalog: &VoiceCatalog) -> Result<(), String> {
    if !catalog.voices.iter().any(|voice| voice.voice_id == request.voice_id) {
        return Err(fail("Giọng đã chọn không có trong phiên bản này"));
    }
    for (text, label) in [(&request.intro, "Mở đầu"), (&request.outro, "Kết thúc")] {
        if text.chars().count() > 600 || text.chars().any(|c| c.is_control() && c != '\n' && c != '\r' && c != '\t') {
            return Err(fail(format!("{label} tối đa 600 ký tự và không được chứa ký tự điều khiển")));
        }
        if !text.trim().is_empty() && !text.chars().any(char::is_alphanumeric) {
            return Err(fail(format!("{label} không có câu nào để đọc")));
        }
    }
    if request.intro.trim().is_empty() && request.outro.trim().is_empty() && request.music.is_none() {
        return Err(fail("Hãy nhập mở đầu, kết thúc hoặc chọn nhạc"));
    }
    if !(1.0..=1.3).contains(&request.speed) || !(0.0..=1.0).contains(&request.music_volume) {
        return Err(fail("Tốc độ đọc hoặc âm lượng nhạc không hợp lệ"));
    }
    if let Some(value) = &request.music {
        let path = Path::new(value);
        if !path.is_absolute() || !path.is_file() || !path.extension().and_then(|ext| ext.to_str())
            .is_some_and(|ext| MUSIC.iter().any(|known| known.eq_ignore_ascii_case(ext))) {
            return Err(fail("Không mở được nhạc nền; chọn MP3, M4A, AAC, WAV, OGG hoặc FLAC"));
        }
    }
    match request.background.as_str() {
        "keep" => (),
        "replace" | "duck" => {
            if request.music.is_none() || !dir.join("editable/dialogue_stem.wav").is_file() {
                return Err(fail("Cần nhạc của bạn và bản giọng Việt riêng để thay nhạc"));
            }
            if request.background == "duck" && !dir.join("editable/background_stem.wav").is_file() {
                return Err(fail("Không có nền gốc đã tách để giữ nhỏ"));
            }
        }
        _ => return Err(fail("Cách dùng nhạc nền không hợp lệ")),
    }
    Ok(())
}

fn recorded_voice(dir: &Path, catalog: &VoiceCatalog) -> Result<String, String> {
    let path = dir.join("job_manifest.json");
    let manifest = if path.is_file() {
        if fs::metadata(&path).map_err(|_| fail("Không đọc được thông tin giọng"))?.len() > 16 * 1024 * 1024 {
            return Err(fail("Thông tin công việc quá lớn"));
        }
        serde_json::from_slice::<Value>(&fs::read(path).map_err(|_| fail("Không đọc được thông tin giọng"))?)
            .map_err(|_| fail("Thông tin công việc không hợp lệ"))?
    } else { json!({}) };
    // Actual exported voice takes precedence over the requested preset.
    let voice = manifest.pointer("/audio/voice_id").and_then(Value::as_str)
        .or_else(|| manifest.pointer("/dubbing/requested_voice_id").and_then(Value::as_str))
        .unwrap_or(&catalog.default_voice_id);
    if !catalog.voices.iter().any(|entry| entry.voice_id == voice) {
        return Err(fail("Giọng của video không có trong phiên bản này"));
    }
    Ok(voice.to_owned())
}

#[tauri::command]
pub fn package_info(output_path: String) -> Result<Value, String> {
    let dir = package_dir(&output_path)?;
    let catalog = load_voice_catalog(&version_root()?)?;
    Ok(json!({"has_background_stem": dir.join("editable/background_stem.wav").is_file(),
              "voice_id": recorded_voice(&dir, &catalog)?}))
}

fn write_status(work: &Path, state: &str, message: &str) -> Result<(), String> {
    let partial = work.join("status.json.partial");
    fs::write(&partial, serde_json::to_vec(&json!({"schema_version": 1, "state": state, "message": message})).unwrap())
        .and_then(|()| fs::rename(partial, work.join("status.json")))
        .map_err(|_| fail("Không ghi được trạng thái đóng gói"))
}

#[tauri::command]
pub fn package_video(request: PackageRequest) -> Result<(), String> {
    let dir = package_dir(&request.output_path)?;
    let root = version_root()?;
    validate(&request, &dir, &load_voice_catalog(&root)?)?;
    let models = control_root()?.join("models");
    let mut running = RUNNING.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    if running.contains(&dir) {
        return Err(fail("Video này đang được đóng gói"));
    }
    let work = dir.join(".dubflow-work/package");
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục đóng gói"))?;
    let cancel = work.join("cancel");
    if cancel.exists() { fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?; }
    let spec = json!({"schema_version": 1, "video": dir.join("final_vi.mp4"), "intro": request.intro,
        "outro": request.outro, "voice_id": request.voice_id, "speed": request.speed, "music": request.music,
        "music_volume": request.music_volume, "background": request.background});
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).map_err(|_| fail("Yêu cầu đóng gói không hợp lệ"))?)
        .map_err(|_| fail("Không ghi được yêu cầu đóng gói"))?;
    write_status(&work, "RUNNING", "Đang khởi động đóng gói…")?;
    let media = root.join("runtime/media");
    let mut command = Command::new(root.join("runtime/python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.package", "--spec"]).arg(spec_path)
        .arg("--output-dir").arg(&dir)
        .arg("--app-root").arg(root.join("app"))
        .arg("--model-root").arg(models)
        .arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if request.cpu_only { command.arg("--cpu-only"); }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| {
        let _ = write_status(&work, "FAILED", "Không chạy được bộ đóng gói video");
        fail("Không chạy được bộ đóng gói video")
    })?;
    running.insert(dir.clone());
    std::thread::spawn(move || {
        let _ = child.wait();
        // A crash must not leave the inspector polling RUNNING forever.
        let status = fs::read(work.join("status.json")).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
        if status.as_ref().is_none_or(|value| value["state"] == "RUNNING") {
            let cancelled = work.join("cancel").exists();
            let _ = write_status(&work, if cancelled { "CANCELLED" } else { "FAILED" },
                if cancelled { "Đã hủy đóng gói." } else { "Bộ đóng gói đã dừng. Hãy thử lại." });
        }
        if let Ok(mut running) = RUNNING.lock() { running.remove(&dir); }
    });
    Ok(())
}

#[tauri::command]
pub fn package_status(output_path: String) -> Result<Option<Value>, String> {
    let path = package_dir(&output_path)?.join(".dubflow-work/package/status.json");
    if !path.exists() { return Ok(None); }
    let bytes = fs::read(path).map_err(|_| fail("Chưa đọc được trạng thái đóng gói"))?;
    serde_json::from_slice::<Value>(&bytes).ok().filter(Value::is_object).map(Some)
        .ok_or_else(|| fail("Trạng thái đóng gói không hợp lệ"))
}

#[tauri::command]
pub fn package_cancel(output_path: String) -> Result<(), String> {
    let work = package_dir(&output_path)?.join(".dubflow-work/package");
    fs::write(work.join("cancel"), b"").map_err(|_| fail("Không hủy được đóng gói"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::display_path;

    fn catalog() -> VoiceCatalog {
        serde_json::from_value::<crate::VoiceManifest>(json!({"schema_version":1,"backend":"test","approved":true,
            "voice_id":"vi-test","voices":[{"voice_id":"vi-test","name":"Test","gender":"Nữ","accent":"Nam",
            "style":"tự nhiên","description":"Test","voice_version":"1","approved":true}]}))
            .map(|value| VoiceCatalog { default_voice_id: value.voice_id, voices: value.voices }).unwrap()
    }

    fn request() -> PackageRequest {
        serde_json::from_value(json!({"outputPath":"unused", "intro":"Xin chào.", "outro":"", "voiceId":"vi-test",
            "speed":1.0, "music":null, "musicVolume":0.2, "background":"keep", "cpuOnly":true})).unwrap()
    }

    #[test]
    fn validates_text_voice_speed_volume_and_music() {
        let dir = std::env::temp_dir().join(format!("dubflow-package-validation-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let catalog = catalog();
        assert!(validate(&request(), &dir, &catalog).is_ok());
        let mut value = request();
        value.intro = "🌱".repeat(600);
        assert!(validate(&value, &dir, &catalog).is_err()); // symbols cannot be narrated
        value.intro = "đ".repeat(600);
        assert!(validate(&value, &dir, &catalog).is_ok());
        value.intro.push('a');
        assert!(validate(&value, &dir, &catalog).is_err());
        for change in ["empty", "voice", "speed", "volume", "background", "control", "music"] {
            let mut value = request();
            match change {
                "empty" => value.intro.clear(), "voice" => value.voice_id = "unknown".into(),
                "speed" => value.speed = f64::NAN, "volume" => value.music_volume = 1.1,
                "background" => value.background = "other".into(), "control" => value.intro.push('\0'),
                _ => value.music = Some("relative.mp3".into()),
            }
            assert!(validate(&value, &dir, &catalog).is_err(), "{change}");
        }
        let music = dir.join("music.FLAC");
        fs::write(&music, b"music").unwrap();
        let mut value = request();
        value.music = Some(display_path(&music));
        value.background = "replace".into();
        assert!(validate(&value, &dir, &catalog).is_err());
        fs::create_dir_all(dir.join("editable")).unwrap();
        fs::write(dir.join("editable/dialogue_stem.wav"), b"wav").unwrap();
        assert!(validate(&value, &dir, &catalog).is_ok());
        value.background = "duck".into();
        assert!(validate(&value, &dir, &catalog).is_err());
        fs::write(dir.join("editable/background_stem.wav"), b"wav").unwrap();
        assert!(validate(&value, &dir, &catalog).is_ok());
        assert_eq!(recorded_voice(&dir, &catalog).unwrap(), "vi-test");
        fs::write(dir.join("job_manifest.json"), br#"{"audio":{"voice_id":"vi-test"},"dubbing":{"requested_voice_id":"unknown"}}"#).unwrap();
        assert_eq!(recorded_voice(&dir, &catalog).unwrap(), "vi-test");
        fs::write(dir.join("job_manifest.json"), br#"{"audio":{"voice_id":"unknown"}}"#).unwrap();
        assert!(recorded_voice(&dir, &catalog).is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn commands_only_accept_finished_review_outputs() {
        let base = std::env::temp_dir().join(format!("dubflow-package-boundary-{}", std::process::id()));
        let dir = base.join("DubFlow Output/run");
        fs::create_dir_all(dir.join(".dubflow-work/package")).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        assert!(package_dir(&display_path(&video)).is_err());
        fs::write(dir.join(".dubflow-work/translation.json"), b"{}").unwrap();
        assert!(package_status(display_path(&video)).unwrap().is_none());
        write_status(&dir.join(".dubflow-work/package"), "RUNNING", "Test").unwrap();
        assert_eq!(package_status(display_path(&video)).unwrap().unwrap()["state"], "RUNNING");
        package_cancel(display_path(&video)).unwrap();
        assert!(dir.join(".dubflow-work/package/cancel").is_file());
        assert!(package_dir("relative").is_err());
        fs::write(dir.join("final_vi_packaged.mp4"), b"video").unwrap();
        assert!(package_dir(&display_path(&dir.join("final_vi_packaged.mp4"))).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
