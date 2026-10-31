//! Optional excerpts from a finished export; no voice/model runtime is needed.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{fail, review_dir, version_root, CREATE_NO_WINDOW};

static RUNNING: LazyLock<Mutex<HashSet<PathBuf>>> = LazyLock::new(|| Mutex::new(HashSet::new()));

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ShortsItem {
    start_ms: u64,
    end_ms: u64,
    title: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ShortsRequest {
    output_path: String,
    items: Vec<ShortsItem>,
    layout: String,
}

fn shorts_dir(output_path: &str) -> Result<PathBuf, String> {
    let dir = review_dir(output_path)?;
    if Path::new(output_path).file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Hãy chọn video final_vi.mp4 đã hoàn tất"));
    }
    Ok(dir)
}

fn validate(request: &ShortsRequest) -> Result<(), String> {
    if !(1..=3).contains(&request.items.len()) || !["auto", "ban-tin", "mo-nen"].contains(&request.layout.as_str()) {
        return Err(fail("Chọn 1–3 đoạn Shorts và bố cục hợp lệ"));
    }
    for item in &request.items {
        if item.end_ms.checked_sub(item.start_ms).is_none_or(|ms| !(5000..=60000).contains(&ms)) {
            return Err(fail("Mỗi Shorts phải dài từ 5 đến 60 giây"));
        }
        if item.title.trim().is_empty() || item.title.chars().count() > 80 || item.title.chars().any(char::is_control) {
            return Err(fail("Tiêu đề cần 1–80 ký tự và không chứa ký tự điều khiển"));
        }
    }
    Ok(())
}

fn write_status(work: &Path, state: &str, message: &str, total: usize) -> Result<(), String> {
    // Retain any published outputs when a child terminates unexpectedly.
    let previous = if state == "RUNNING" { None } else { fs::read(work.join("status.json")).ok() };
    let mut value = previous
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).filter(Value::is_object)
        .unwrap_or_else(|| json!({"schema_version": 1, "done": 0, "total": total, "outputs": []}));
    value["state"] = json!(state);
    value["message"] = json!(message);
    let partial = work.join("status.json.partial");
    fs::write(&partial, serde_json::to_vec(&value).unwrap())
        .and_then(|()| fs::rename(partial, work.join("status.json")))
        .map_err(|_| fail("Không ghi được trạng thái Shorts"))
}

#[tauri::command]
pub fn shorts_render(request: ShortsRequest) -> Result<(), String> {
    let dir = shorts_dir(&request.output_path)?;
    validate(&request)?;
    let root = version_root()?;
    let mut running = RUNNING.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    if running.contains(&dir) { return Err(fail("Video này đang được cắt Shorts")); }
    let work = dir.join(".dubflow-work/shorts");
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục Shorts"))?;
    let lease = crate::work_lease::take(&work, "Video này đang được cắt Shorts ở cửa sổ DubFlow khác")?;
    let cancel = work.join("cancel");
    if cancel.exists() { fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?; }
    let total = request.items.len();
    let spec = json!({"schema_version": 1, "video": dir.join("final_vi.mp4"), "items": request.items, "layout": request.layout});
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).unwrap()).map_err(|_| fail("Không ghi được yêu cầu Shorts"))?;
    // Start a fresh status; receipts remain available to the worker for resume.
    write_status(&work, "RUNNING", "Đang khởi động cắt Shorts…", total)?;
    let media = root.join("runtime/media");
    let mut command = Command::new(root.join("runtime/python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.shorts", "--spec"]).arg(spec_path)
        .arg("--output-dir").arg(&dir)
        .arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| {
        let _ = write_status(&work, "FAILED", "Không chạy được bộ cắt Shorts", total);
        fail("Không chạy được bộ cắt Shorts")
    })?;
    running.insert(dir.clone());
    std::thread::spawn(move || {
        let _ = child.wait();
        let status = fs::read(work.join("status.json")).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
        if status.as_ref().is_none_or(|value| !["COMPLETED", "FAILED", "CANCELLED"].iter().any(|state| value["state"] == *state)) {
            let cancelled = work.join("cancel").exists();
            let _ = write_status(&work, if cancelled { "CANCELLED" } else { "FAILED" },
                if cancelled { "Đã hủy cắt Shorts." } else { "Bộ cắt Shorts đã dừng. Hãy thử lại." }, total);
        }
        if let Ok(mut running) = RUNNING.lock() { running.remove(&dir); }
        drop(lease);
    });
    Ok(())
}

#[tauri::command]
pub fn shorts_status(output_path: String) -> Result<Option<Value>, String> {
    let path = shorts_dir(&output_path)?.join(".dubflow-work/shorts/status.json");
    if !path.exists() { return Ok(None); }
    let bytes = fs::read(path).map_err(|_| fail("Chưa đọc được trạng thái Shorts"))?;
    serde_json::from_slice::<Value>(&bytes).ok().filter(Value::is_object).map(Some)
        .ok_or_else(|| fail("Trạng thái Shorts không hợp lệ"))
}

#[tauri::command]
pub fn shorts_cancel(output_path: String) -> Result<(), String> {
    fs::write(shorts_dir(&output_path)?.join(".dubflow-work/shorts/cancel"), b"")
        .map_err(|_| fail("Không hủy được cắt Shorts"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::display_path;

    fn request() -> ShortsRequest {
        serde_json::from_value(json!({"outputPath":"unused","items":[{"start_ms":1000,"end_ms":6000,"title":"Đoạn hay"}],"layout":"auto"})).unwrap()
    }

    #[test]
    fn validates_items_titles_and_layout() {
        for layout in ["auto", "ban-tin", "mo-nen"] {
            let mut value = request();
            value.layout = layout.into();
            value.items[0].title = "đ".repeat(80);
            assert!(validate(&value).is_ok());
        }
        for change in ["empty", "many", "layout", "backward", "short", "long", "title", "control", "blank"] {
            let mut value = request();
            match change {
                "empty" => value.items.clear(),
                "many" => for _ in 0..3 { value.items.push(ShortsItem { start_ms: 0, end_ms: 5000, title: "Test".into() }); },
                "layout" => value.layout = "other".into(),
                "backward" => value.items[0].end_ms = 0,
                "short" => value.items[0].end_ms = 5999,
                "long" => value.items[0].end_ms = 61001,
                "title" => value.items[0].title = "🌱".repeat(81),
                "control" => value.items[0].title.push('\0'),
                _ => value.items[0].title = " ".into(),
            }
            assert!(validate(&value).is_err(), "{change}");
        }
        for item in [json!({"start_ms":-1,"end_ms":6000,"title":"X"}), json!({"start_ms":true,"end_ms":6000,"title":"X"}),
            json!({"start_ms":0.5,"end_ms":6000,"title":"X"})] {
            assert!(serde_json::from_value::<ShortsItem>(item).is_err());
        }
    }

    #[test]
    fn requires_finished_output_and_preserves_results_on_crash() {
        let base = std::env::temp_dir().join(format!("dubflow-shorts-host-{}", std::process::id()));
        let dir = base.join("DubFlow Output/run");
        let work = dir.join(".dubflow-work/shorts");
        fs::create_dir_all(&work).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        assert!(shorts_dir(&display_path(&video)).is_err());
        fs::write(dir.join(".dubflow-work/translation.json"), b"{}").unwrap();
        assert!(shorts_status(display_path(&video)).unwrap().is_none());
        fs::write(work.join("status.json"), br#"{"done":1,"total":2,"outputs":[{"title":"X"}]}"#).unwrap();
        write_status(&work, "FAILED", "Test", 2).unwrap();
        let status = shorts_status(display_path(&video)).unwrap().unwrap();
        assert_eq!(status["done"], 1);
        assert_eq!(status["outputs"].as_array().unwrap().len(), 1);
        shorts_cancel(display_path(&video)).unwrap();
        assert!(work.join("cancel").is_file());
        fs::write(dir.join("final_vi_packaged.mp4"), b"video").unwrap();
        assert!(shorts_dir(&display_path(&dir.join("final_vi_packaged.mp4"))).is_err());
        assert!(shorts_dir("relative/final_vi.mp4").is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
