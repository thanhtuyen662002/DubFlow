//! Optional 16:9 derivative of a completed vertical export.
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
const MAX_MS: u64 = 9_007_199_254_740_991;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReframeRequest {
    output_path: String,
    mode: String,
    #[serde(default)]
    title: String,
    score: Option<u8>,
    #[serde(default)]
    pros: Vec<String>,
    #[serde(default)]
    cons: Vec<String>,
    #[serde(default)]
    summary: String,
    #[serde(default = "centre")]
    focus_start: f64,
    #[serde(default = "centre")]
    focus_end: f64,
    #[serde(default)]
    start_ms: u64,
    end_ms: Option<u64>,
}

fn centre() -> f64 { 50.0 }

fn reframe_dir(output_path: &str) -> Result<PathBuf, String> {
    let dir = review_dir(output_path)?;
    if Path::new(output_path).file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Hãy chọn video final_vi.mp4 đã hoàn tất"));
    }
    Ok(dir)
}

fn validate(request: &ReframeRequest) -> Result<(), String> {
    let text = |value: &str, limit| value.chars().count() <= limit && !value.chars().any(char::is_control);
    if !["bang-thong-tin", "cat-ngang"].contains(&request.mode.as_str())
        || !text(&request.title, 80) || !text(&request.summary, 120)
        || request.score.is_some_and(|score| score > 10)
        || [&request.pros, &request.cons].iter().any(|points| points.len() > 3
            || points.iter().any(|point| point.trim().is_empty() || !text(point, 60)))
        || [request.focus_start, request.focus_end].iter().any(|focus| !(0.0..=100.0).contains(focus))
        || request.start_ms > MAX_MS
        || request.end_ms.is_some_and(|end| end <= request.start_ms || end > MAX_MS)
    {
        return Err(fail("Yêu cầu đổi khung không hợp lệ: tiêu đề ≤80, tóm tắt ≤120, tối đa 3 ý mỗi loại (≤60), điểm 0–10, vị trí 0–100 và mốc cắt tăng dần"));
    }
    Ok(())
}

fn write_status(work: &Path, state: &str, message: &str) -> Result<(), String> {
    let value = json!({"schema_version": 1, "state": state, "message": message});
    let partial = work.join("status.json.partial");
    fs::write(&partial, serde_json::to_vec(&value).unwrap())
        .and_then(|()| fs::rename(partial, work.join("status.json")))
        .map_err(|_| fail("Không ghi được trạng thái đổi khung"))
}

fn finish_status(work: &Path) {
    let status = fs::read(work.join("status.json")).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    if status.as_ref().is_none_or(|value| !["COMPLETED", "FAILED", "CANCELLED"].iter().any(|state| value["state"] == *state)) {
        let cancelled = work.join("cancel").exists();
        let _ = write_status(work, if cancelled { "CANCELLED" } else { "FAILED" },
            if cancelled { "Đã hủy đổi khung 16:9." } else { "Bộ đổi khung đã dừng. Hãy thử lại." });
    }
}

#[tauri::command]
pub fn reframe_render(request: ReframeRequest) -> Result<(), String> {
    let dir = reframe_dir(&request.output_path)?;
    validate(&request)?;
    let root = version_root()?;
    let mut running = RUNNING.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    if running.contains(&dir) { return Err(fail("Video này đang được đổi khung")); }
    let work = dir.join(".dubflow-work/reframe");
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục đổi khung"))?;
    let lease = crate::work_lease::take(&work, "Video này đang được đổi khung ở cửa sổ DubFlow khác")?;
    let cancel = work.join("cancel");
    if cancel.exists() { fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?; }
    let spec = json!({"schema_version": 1, "video": dir.join("final_vi.mp4"), "mode": request.mode,
        "title": request.title, "score": request.score, "pros": request.pros, "cons": request.cons,
        "summary": request.summary, "focus_start": request.focus_start, "focus_end": request.focus_end,
        "start_ms": request.start_ms, "end_ms": request.end_ms});
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).unwrap()).map_err(|_| fail("Không ghi được yêu cầu đổi khung"))?;
    write_status(&work, "RUNNING", "Đang khởi động đổi khung 16:9…")?;
    let media = root.join("runtime/media");
    let mut command = Command::new(root.join("runtime/python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.reframe", "--spec"]).arg(spec_path)
        .arg("--output-dir").arg(&dir)
        .arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| {
        let _ = write_status(&work, "FAILED", "Không chạy được bộ đổi khung");
        fail("Không chạy được bộ đổi khung")
    })?;
    running.insert(dir.clone());
    std::thread::spawn(move || {
        let _ = child.wait();
        finish_status(&work);
        if let Ok(mut running) = RUNNING.lock() { running.remove(&dir); }
        drop(lease);
    });
    Ok(())
}

#[tauri::command]
pub fn reframe_status(output_path: String) -> Result<Option<Value>, String> {
    let path = reframe_dir(&output_path)?.join(".dubflow-work/reframe/status.json");
    if !path.exists() { return Ok(None); }
    fs::read(path).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .filter(Value::is_object).map(Some).ok_or_else(|| fail("Chưa đọc được trạng thái đổi khung"))
}

#[tauri::command]
pub fn reframe_cancel(output_path: String) -> Result<(), String> {
    fs::write(reframe_dir(&output_path)?.join(".dubflow-work/reframe/cancel"), b"")
        .map_err(|_| fail("Không hủy được đổi khung"))
}

#[derive(Debug, Serialize)]
pub struct ReframeInfo { width: u64, height: u64, vertical: bool }

fn parse_info(bytes: &[u8]) -> Result<ReframeInfo, String> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| fail("Không đọc được kích thước video"))?;
    let stream = &value["streams"][0];
    match (stream["width"].as_u64(), stream["height"].as_u64()) {
        (Some(width), Some(height)) if width > 0 && height > 0 => Ok(ReframeInfo { width, height, vertical: height > width }),
        _ => Err(fail("Không đọc được kích thước video")),
    }
}

#[tauri::command]
pub async fn reframe_info(output_path: String) -> Result<ReframeInfo, String> {
    let video = reframe_dir(&output_path)?.join("final_vi.mp4");
    let mut command = Command::new(version_root()?.join("runtime/media/ffprobe.exe"));
    command.args(["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json"])
        .arg(video).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || command.output()).await
        .map_err(|_| fail("Không kiểm tra được video"))?.map_err(|_| fail("Không chạy được bộ kiểm tra video"))?;
    if !output.status.success() { return Err(fail("Không đọc được kích thước video")); }
    parse_info(&output.stdout)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::display_path;

    fn request() -> ReframeRequest {
        serde_json::from_value(json!({"outputPath":"unused", "mode":"bang-thong-tin"})).unwrap()
    }

    #[test]
    fn strict_fields_and_ranges() {
        assert!(validate(&request()).is_ok());
        for value in [json!({"score":11}), json!({"title":"đ".repeat(81)}), json!({"title":"x\n"}),
            json!({"summary":"x".repeat(121)}), json!({"pros":["a","b","c","d"]}), json!({"cons":[""]}),
            json!({"pros":["x".repeat(61)]}), json!({"focusStart":-1}), json!({"focusEnd":101}),
            json!({"endMs":0}), json!({"startMs":2000,"endMs":1000}), json!({"mode":"other"})] {
            let mut base = json!({"outputPath":"unused", "mode":"cat-ngang"});
            base.as_object_mut().unwrap().extend(value.as_object().unwrap().clone());
            assert!(validate(&serde_json::from_value::<ReframeRequest>(base).unwrap()).is_err());
        }
        for value in [json!({"score":true}), json!({"score":2.5}), json!({"score":-1}), json!({"extra":1}),
            json!({"startMs":-1}), json!({"startMs":1.5}), json!({"focusStart":true})] {
            let mut base = json!({"outputPath":"unused", "mode":"cat-ngang"});
            base.as_object_mut().unwrap().extend(value.as_object().unwrap().clone());
            assert!(serde_json::from_value::<ReframeRequest>(base).is_err());
        }
        let mut valid = request();
        valid.title = "🌱".repeat(80); valid.score = Some(0); valid.focus_start = 0.0; valid.focus_end = 100.0;
        assert!(validate(&valid).is_ok());
        valid.focus_start = f64::NAN;
        assert!(validate(&valid).is_err());
    }

    #[test]
    fn probe_geometry_and_vertical_flag() {
        for (width, height, vertical) in [(90,160,true),(160,90,false),(90,90,false)] {
            let info = parse_info(&serde_json::to_vec(&json!({"streams":[{"width":width,"height":height}]})).unwrap()).unwrap();
            assert_eq!((info.width,info.height,info.vertical), (width,height,vertical));
        }
        for value in [json!({}),json!({"streams":[]}),json!({"streams":[{"width":0,"height":90}]})] {
            assert!(parse_info(&serde_json::to_vec(&value).unwrap()).is_err());
        }
    }

    #[test]
    fn boundary_cancel_and_crash_watcher() {
        let base = std::env::temp_dir().join(format!("dubflow-reframe-host-{}", std::process::id()));
        let dir = base.join("DubFlow Output/run");
        let work = dir.join(".dubflow-work/reframe");
        fs::create_dir_all(&work).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        assert!(reframe_dir(&display_path(&video)).is_err());
        fs::write(dir.join(".dubflow-work/translation.json"), b"{}").unwrap();
        let path = display_path(&video);
        assert!(reframe_status(path.clone()).unwrap().is_none());
        write_status(&work, "RUNNING", "Test").unwrap();
        finish_status(&work);
        assert_eq!(reframe_status(path.clone()).unwrap().unwrap()["state"], "FAILED");
        write_status(&work, "RUNNING", "Test").unwrap();
        reframe_cancel(path.clone()).unwrap();
        finish_status(&work);
        assert_eq!(reframe_status(path.clone()).unwrap().unwrap()["state"], "CANCELLED");
        write_status(&work, "COMPLETED", "Test").unwrap();
        finish_status(&work);
        assert_eq!(reframe_status(path).unwrap().unwrap()["state"], "COMPLETED");
        fs::write(dir.join("final_vi_16x9.mp4"), b"video").unwrap();
        assert!(reframe_dir(&display_path(&dir.join("final_vi_16x9.mp4"))).is_err());
        assert!(reframe_dir("relative/final_vi.mp4").is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
