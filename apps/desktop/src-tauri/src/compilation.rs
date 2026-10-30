//! Editorial compilation of completed dubbing exports (ADR-0046).
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use crate::{control_root, display_path, fail, load_voice_catalog, review_dir, version_root, VoiceCatalog, CREATE_NO_WINDOW};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CompilationItem {
    video: String,
    title: String,
    lead: String,
    score: Option<u8>,
    pros: Vec<String>,
    cons: Vec<String>,
    start_ms: Option<u64>,
    end_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CompilationRequest {
    title: String,
    layout: String,
    voice_id: String,
    speed: f64,
    music: Option<String>,
    music_volume: f64,
    intro: String,
    outro: String,
    items: Vec<CompilationItem>,
    output_root: Option<String>,
    cpu_only: bool,
}

fn text(value: &str, limit: usize, required: bool, spoken: bool) -> bool {
    value.chars().count() <= limit
        && !value.chars().any(|c| c.is_control() && !['\n', '\r', '\t'].contains(&c))
        && (!required || !value.trim().is_empty())
        && (!spoken || value.trim().is_empty() || value.chars().any(char::is_alphanumeric))
}

fn item_dir(value: &str) -> Result<PathBuf, String> {
    if Path::new(value).file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Hãy chọn final_vi.mp4 đã hoàn tất"));
    }
    review_dir(value).map_err(|_| fail("Cần video hoàn tất bên trong DubFlow Output, có dữ liệu bản dịch"))
}

fn validate(request: &CompilationRequest, catalog: &VoiceCatalog) -> Result<Vec<PathBuf>, String> {
    if !text(&request.title, 120, true, false) || !text(&request.intro, 800, false, true) || !text(&request.outro, 800, false, true) {
        return Err(fail("Tiêu đề tối đa 120 ký tự; mở đầu và kết thúc tối đa 800 ký tự, có nội dung để đọc"));
    }
    if !["bang-thong-tin", "toan-man-hinh"].contains(&request.layout.as_str())
        || !(1.0..=1.3).contains(&request.speed) || !(0.0..=1.0).contains(&request.music_volume) {
        return Err(fail("Bố cục, tốc độ đọc hoặc âm lượng không hợp lệ"));
    }
    if !catalog.voices.iter().any(|voice| voice.voice_id == request.voice_id) {
        return Err(fail("Giọng đã chọn không có trong phiên bản này"));
    }
    if !(2..=30).contains(&request.items.len()) { return Err(fail("Hãy chọn từ 2 đến 30 video đã hoàn tất")); }
    if let Some(value) = &request.music {
        let path = Path::new(value);
        if !path.is_absolute() || !path.is_file() || !path.extension().and_then(|ext| ext.to_str())
            .is_some_and(|ext| ["mp3", "m4a", "aac", "wav", "ogg", "flac"].iter().any(|known| known.eq_ignore_ascii_case(ext))) {
            return Err(fail("Không mở được nhạc nền; chọn MP3, M4A, AAC, WAV, OGG hoặc FLAC"));
        }
    }
    request.items.iter().enumerate().map(|(index, item)| {
        if !text(&item.title, 80, true, false) || !text(&item.lead, 400, false, true)
            || item.score.is_some_and(|score| score > 10)
            || [&item.pros, &item.cons].iter().any(|values| values.len() > 3 || values.iter().any(|v| !text(v, 60, true, false)))
            || [item.start_ms, item.end_ms].iter().flatten().any(|&ms| ms > 9_007_199_254_740_991)
            || item.end_ms.is_some_and(|end| end <= item.start_ms.unwrap_or(0)) {
            return Err(fail(format!("Phần {}: tiêu đề, lời dẫn, điểm, ưu/nhược điểm hoặc mốc cắt không hợp lệ", index + 1)));
        }
        item_dir(&item.video).map(|dir| dir.join("final_vi.mp4"))
            .map_err(|_| fail(format!("Phần {}: video hoàn tất không hợp lệ", index + 1)))
    }).collect()
}

fn compilation_output(base: &Path, title: &str, seed: &str) -> PathBuf {
    let digest = Sha256::digest(seed.as_bytes());
    let run: String = digest.iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    base.join("DubFlow Output").join(format!("Tong hop - {}", crate::product::folder_title(title))).join(format!("run-{run}"))
}

fn compilation_dir(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !path.is_absolute() || path.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
        return Err(fail("Thư mục tổng hợp không hợp lệ"));
    }
    let dir = path.canonicalize().map_err(|_| fail("Không tìm thấy thư mục tổng hợp"))?;
    let run = dir.file_name().and_then(|v| v.to_str()).unwrap_or_default();
    let title = dir.parent().and_then(Path::file_name).and_then(|v| v.to_str()).unwrap_or_default();
    let root = dir.parent().and_then(Path::parent).and_then(Path::file_name);
    if run.len() != 16 || !run.starts_with("run-") || !run[4..].bytes().all(|b| b.is_ascii_hexdigit())
        || !title.starts_with("Tong hop - ") || root.is_none_or(|v| v != "DubFlow Output")
        || !dir.join(".dubflow-work/spec.json").is_file() {
        return Err(fail("Thư mục tổng hợp không hợp lệ"));
    }
    Ok(dir)
}

fn write_status(work: &Path, state: &str, message: &str, total: usize) -> Result<(), String> {
    let partial = work.join("status.json.partial");
    fs::write(&partial, serde_json::to_vec(&json!({"schema_version":1,"state":state,"done":0,"total":total,"message":message,"path":null})).unwrap())
        .and_then(|()| fs::rename(partial, work.join("status.json")))
        .map_err(|_| fail("Không ghi được trạng thái tổng hợp"))
}

#[tauri::command]
pub fn compilation_render(request: CompilationRequest) -> Result<String, String> {
    let root = version_root()?;
    let videos = validate(&request, &load_voice_catalog(&root)?)?;
    let base = match &request.output_root {
        Some(value) => {
            let path = PathBuf::from(value);
            if !path.is_absolute() || !path.is_dir() { return Err(fail("Hãy chọn lại thư mục lưu kết quả")); }
            path
        }
        None => videos[0].ancestors().find(|p| p.file_name().is_some_and(|v| v == "DubFlow Output"))
            .and_then(Path::parent).ok_or_else(|| fail("Không tìm được thư mục lưu"))?.to_path_buf(),
    };
    let models = control_root()?.join("models");
    let started = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let output = compilation_output(&base, &request.title, &format!("{started}:{}:{}", std::process::id(), request.title));
    fs::create_dir_all(output.parent().unwrap()).map_err(|_| fail("Không tạo được thư mục tổng hợp"))?;
    fs::create_dir(&output).map_err(|_| fail("Không tạo được phiên tổng hợp mới"))?;
    let work = output.join(".dubflow-work");
    fs::create_dir(&work).map_err(|_| fail("Không tạo được thư mục làm việc"))?;
    let items: Vec<Value> = request.items.iter().zip(videos).map(|(item, video)| json!({
        "video":display_path(&video),"title":item.title,"lead":item.lead,"score":item.score,"pros":item.pros,"cons":item.cons,
        "start_ms":item.start_ms,"end_ms":item.end_ms})).collect();
    let total = items.len() + request.items.iter().filter(|i| !i.lead.trim().is_empty()).count()
        + usize::from(!request.intro.trim().is_empty()) + usize::from(!request.outro.trim().is_empty());
    let spec = json!({"schema_version":1,"title":request.title,"layout":request.layout,"voice_id":request.voice_id,
        "speed":request.speed,"music":request.music,"music_volume":request.music_volume,"intro":request.intro,"outro":request.outro,"items":items});
    let spec_path = work.join("spec.json");
    fs::write(&spec_path, serde_json::to_vec(&spec).map_err(|_| fail("Yêu cầu tổng hợp không hợp lệ"))?)
        .map_err(|_| fail("Không ghi được yêu cầu tổng hợp"))?;
    write_status(&work, "RUNNING", "Đang khởi động tổng hợp…", total)?;
    let media = root.join("runtime/media");
    let mut command = Command::new(root.join("runtime/python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.compilation", "--spec"]).arg(spec_path)
        .arg("--output-dir").arg(&output).arg("--app-root").arg(root.join("app"))
        .arg("--model-root").arg(models).arg("--ffmpeg").arg(media.join("ffmpeg.exe"))
        .arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if request.cpu_only { command.arg("--cpu-only"); }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| {
        let _ = write_status(&work, "FAILED", "Không chạy được bộ tổng hợp video", total);
        fail("Không chạy được bộ tổng hợp video")
    })?;
    std::thread::spawn(move || {
        let _ = child.wait();
        let status = fs::read(work.join("status.json")).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
        if status.as_ref().is_none_or(|value| value["state"] == "RUNNING") {
            let cancelled = work.join("cancel").exists();
            let _ = write_status(&work, if cancelled { "CANCELLED" } else { "FAILED" },
                if cancelled { "Đã hủy tổng hợp." } else { "Bộ tổng hợp đã dừng. Hãy thử lại." }, total);
        }
    });
    Ok(display_path(&output))
}

#[tauri::command]
pub fn compilation_status(output_dir: String) -> Result<Value, String> {
    let bytes = fs::read(compilation_dir(&output_dir)?.join(".dubflow-work/status.json")).map_err(|_| fail("Chưa đọc được trạng thái tổng hợp"))?;
    serde_json::from_slice::<Value>(&bytes).ok().filter(Value::is_object).ok_or_else(|| fail("Trạng thái tổng hợp không hợp lệ"))
}

#[tauri::command]
pub fn compilation_cancel(output_dir: String) -> Result<(), String> {
    fs::write(compilation_dir(&output_dir)?.join(".dubflow-work/cancel"), b"").map_err(|_| fail("Không hủy được tổng hợp"))
}

#[tauri::command]
pub fn compilation_description(output_dir: String) -> Result<String, String> {
    let path = compilation_dir(&output_dir)?.join("description.txt");
    if fs::metadata(&path).map_err(|_| fail("Chưa có mô tả"))?.len() > 128 * 1024 { return Err(fail("Mô tả quá lớn")); }
    fs::read_to_string(path).map_err(|_| fail("Không đọc được mô tả"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn catalog() -> VoiceCatalog {
        serde_json::from_value::<crate::VoiceManifest>(json!({"schema_version":1,"backend":"test","approved":true,
            "voice_id":"vi-test","voices":[{"voice_id":"vi-test","name":"Test","gender":"Nữ","accent":"Nam",
            "style":"tự nhiên","description":"Test","voice_version":"1","approved":true}]}))
            .map(|v| VoiceCatalog { default_voice_id:v.voice_id, voices:v.voices }).unwrap()
    }

    #[test]
    fn validates_finished_paths_and_editorial_fields() {
        let base = std::env::temp_dir().join(format!("dubflow-compilation-validation-{}", std::process::id()));
        let dir = base.join("DubFlow Output/job");
        fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        assert!(item_dir(&display_path(&video)).is_err());
        fs::write(dir.join(".dubflow-work/translation.json"), b"{}").unwrap();
        let item = json!({"video":display_path(&video),"title":"Phần thử","lead":"Xin chào.","score":8,"pros":["Hay"],"cons":[],"startMs":0,"endMs":2000});
        let value = json!({"title":"Tổng hợp","layout":"bang-thong-tin","voiceId":"vi-test","speed":1.3,"music":null,
            "musicVolume":0.2,"intro":"Xin chào.","outro":"","items":[item.clone(),item],"cpuOnly":true,"outputRoot":null});
        let parse = |v: Value| serde_json::from_value::<CompilationRequest>(v).unwrap();
        assert!(validate(&parse(value.clone()), &catalog()).is_ok());
        for (field, change) in [("title", json!("đ".repeat(121))), ("intro", json!("a".repeat(801))), ("outro", json!("!!!")),
            ("speed", json!(1.4)), ("musicVolume", json!(-1)), ("voiceId", json!("missing")), ("layout", json!("other")),
            ("items", json!([])), ("music", json!("relative.wav"))] {
            let mut bad = value.clone(); bad[field] = change;
            assert!(validate(&parse(bad), &catalog()).is_err(), "{field}");
        }
        for (field, change) in [("title", json!("x".repeat(81))), ("lead", json!("x".repeat(401))), ("score", json!(11)),
            ("pros", json!(["a", "b", "c", "d"])), ("cons", json!(["a".repeat(61)])), ("endMs", json!(0)),
            ("video", json!("relative/final_vi.mp4"))] {
            let mut bad = value.clone(); bad["items"][0][field] = change;
            assert!(validate(&parse(bad), &catalog()).is_err(), "{field}");
        }
        let mut nan = parse(value); nan.speed = f64::NAN;
        assert!(validate(&nan, &catalog()).is_err());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn names_and_command_boundaries() {
        let base = std::env::temp_dir().join(format!("dubflow-compilation-boundary-{}", std::process::id()));
        let output = compilation_output(&base, &format!("Câu: / {}...", "đ".repeat(80)), "seed");
        assert_eq!(output.file_name().unwrap().len(), 16);
        assert!(output.parent().unwrap().file_name().unwrap().to_string_lossy().starts_with("Tong hop - Câu "));
        assert!(output.parent().unwrap().file_name().unwrap().to_string_lossy().chars().count() <= 51);
        fs::create_dir_all(output.join(".dubflow-work")).unwrap();
        fs::write(output.join(".dubflow-work/spec.json"), b"{}").unwrap();
        write_status(&output.join(".dubflow-work"), "RUNNING", "Đang chạy", 2).unwrap();
        assert_eq!(compilation_status(display_path(&output)).unwrap()["total"], 2);
        compilation_cancel(display_path(&output)).unwrap();
        assert!(output.join(".dubflow-work/cancel").is_file());
        fs::write(output.join("description.txt"), "00:00 Phần 1\n").unwrap();
        assert!(compilation_description(display_path(&output)).unwrap().starts_with("00:00"));
        assert!(compilation_status(display_path(&base)).is_err());
        assert!(compilation_cancel("relative".into()).is_err());
        assert!(compilation_description(display_path(&base)).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
