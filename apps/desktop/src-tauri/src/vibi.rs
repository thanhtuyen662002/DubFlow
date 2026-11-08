//! Vibi.pro voices (ElevenLabs, MiniMax) with the user's own key (#632).
//!
//! The key is saved like the AI keys (Windows Credential Manager, `vibi`) and
//! reaches `python -m engine.dubflow.tts.vibi`, the narration and the dubbing
//! worker only through `DUBFLOW_VIBI_KEY` in their environment, never a file
//! or a command line. The webview never calls Vibi itself: every request,
//! preview and sample goes through the host, so its network policy stays closed.

use std::ffi::OsString;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use serde_json::{json, Value};

use crate::{ai_keys, control_root, display_path, fail, sfx, version_root};

/// The environment variable the engine reads (and removes) for the Vibi key.
pub(crate) const KEY_ENV: &str = "DUBFLOW_VIBI_KEY";
/// Beside a job's outputs: the Vibi voice chosen for its dubbing (no key).
pub(crate) const CLOUD_VOICE_FILE: &str = "cloud_voice.json";
const MAX_SETTINGS_BYTES: usize = 4 * 1024;
const PROVIDERS: [&str; 2] = ["elevenlabs", "minimax"];

/// The saved key, or a plain message pointing at Cài đặt.
pub(crate) fn saved_key() -> Result<String, String> {
    ai_keys::job_key("vibi")?.filter(|key| !key.trim().is_empty())
        .ok_or_else(|| fail("Chưa có khóa Vibi.pro; hãy dán khóa trong Cài đặt › Giọng đọc Vibi.pro"))
}

/// A voice's settings as the engine validates them, checked for shape and size here.
pub(crate) fn checked_settings(value: &Value) -> Result<Value, String> {
    let provider = value.get("provider").and_then(Value::as_str).unwrap_or_default();
    let voice = value.get("voice_id").and_then(Value::as_str).unwrap_or_default();
    let size = serde_json::to_vec(value).map(|bytes| bytes.len()).unwrap_or(usize::MAX);
    if !value.is_object() || !PROVIDERS.contains(&provider) || voice.is_empty() || voice.len() > 128 || size > MAX_SETTINGS_BYTES {
        return Err(fail("Giọng Vibi.pro đã chọn không hợp lệ; hãy chọn lại giọng"));
    }
    Ok(value.clone())
}

/// cloud_voice.json beside the outputs (or none), and the key for the job's environment.
pub(crate) fn write_cloud_voice(output: &Path, settings: Option<&Value>) -> Result<Option<String>, String> {
    let path = output.join(CLOUD_VOICE_FILE);
    let Some(settings) = settings.filter(|value| !value.is_null()) else {
        if path.exists() {
            fs::remove_file(&path).map_err(|error| fail(format!("unable to update {CLOUD_VOICE_FILE}: {error}")))?;
        }
        return Ok(None);
    };
    let settings = checked_settings(settings)?;
    let key = saved_key()?;
    let document = json!({"schema_version": 1, "vibi": settings});
    fs::write(&path, serde_json::to_vec_pretty(&document).map_err(|error| fail(error.to_string()))?)
        .map_err(|error| fail(format!("unable to write {CLOUD_VOICE_FILE}: {error}")))?;
    Ok(Some(key))
}

/// One request to `python -m engine.dubflow.tts.vibi`: JSON on stdin, the key in the environment.
async fn engine(request: Value, key: Option<String>) -> Result<Value, String> {
    let body = serde_json::to_vec(&request).map_err(|error| fail(error.to_string()))?;
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.tts.vibi"])
        .env("PYTHONUTF8", "1").env("PYTHONNOUSERSITE", "1");
    if let Some(key) = &key {
        command.env(KEY_ENV, key);
    }
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(crate::CREATE_NO_WINDOW);
    let output = tauri::async_runtime::spawn_blocking(move || -> std::io::Result<std::process::Output> {
        let mut child = command.spawn()?;
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(&body)?;
        }
        child.wait_with_output()
    })
    .await.map_err(|error| fail(error.to_string()))?
    .map_err(|error| fail(format!("Không chạy được bộ gọi Vibi.pro: {error}")))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let value = serde_json::from_str::<Value>(text.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ gọi Vibi.pro không trả về dữ liệu"))?;
    if value["ok"] != true {
        return Err(fail(value["message"].as_str().unwrap_or("Vibi.pro không trả lời")));
    }
    Ok(value)
}

/// Check the key (balance), list models or voices, or the built-in presets.
/// `key` checks a key typed in Cài đặt before it is saved.
#[tauri::command]
pub async fn vibi_call(request: Value, key: Option<String>) -> Result<Value, String> {
    let action = request.get("action").and_then(Value::as_str).unwrap_or_default();
    if !matches!(action, "me" | "models" | "voices" | "presets") {
        return Err(fail("Yêu cầu Vibi.pro không hợp lệ"));
    }
    if serde_json::to_vec(&request).map(|bytes| bytes.len()).unwrap_or(usize::MAX) > 16 * 1024 {
        return Err(fail("Yêu cầu Vibi.pro quá lớn"));
    }
    let key = match key.map(|key| key.trim().to_owned()).filter(|key| !key.is_empty()) {
        Some(key) => Some(key),
        None if action == "presets" => None,
        None => Some(saved_key()?),
    };
    engine(request, key).await
}

fn short_hash(text: &str) -> String {
    // FNV-1a twice with different seeds: a stable cache file name, not a security boundary.
    let mut a: u64 = 0xcbf29ce484222325;
    let mut b: u64 = 0x84222325cbf29ce4;
    for byte in text.bytes() {
        a = (a ^ u64::from(byte)).wrapping_mul(0x100000001b3);
        b = (b ^ u64::from(byte)).wrapping_mul(0x100000001b3).rotate_left(5);
    }
    format!("{a:016x}{b:016x}")
}

fn cache_file(folder: &str, key: &str) -> Result<PathBuf, String> {
    let dir = control_root()?.join(folder);
    fs::create_dir_all(&dir).map_err(|_| fail("Không tạo được thư mục giọng mẫu"))?;
    Ok(dir.join(format!("{}.mp3", short_hash(key))))
}

fn playable(file: PathBuf) -> Result<String, String> {
    let file = file.canonicalize().map_err(|_| fail("Không tìm thấy giọng mẫu"))?;
    sfx::allow_files(std::slice::from_ref(&file));
    Ok(display_path(&file))
}

/// A voice's own preview (free), fetched once by the host; the file's path for the player.
#[tauri::command]
pub async fn vibi_preview(url: String) -> Result<String, String> {
    if !url.starts_with("https://") || url.len() > 2000 {
        return Err(fail("Giọng này không có bản nghe thử"));
    }
    let file = cache_file("vibi-previews", &url)?;
    if !file.is_file() {
        engine(json!({"action": "preview", "url": url, "out": file.to_string_lossy()}), Some(saved_key()?)).await?;
    }
    playable(file)
}

/// A sample line read with every chosen setting (this spends a little credit, once per setting).
#[tauri::command]
pub async fn vibi_sample(settings: Value, text: Option<String>) -> Result<String, String> {
    let settings = checked_settings(&settings)?;
    let text: String = text.unwrap_or_default().chars().filter(|ch| !ch.is_control()).take(400).collect();
    let file = cache_file("vibi-samples", &format!("{settings}|{text}"))?;
    if !file.is_file() {
        engine(json!({"action": "sample", "settings": settings, "text": text, "out": file.to_string_lossy()}), Some(saved_key()?)).await?;
    }
    playable(file)
}

/// `--vibi <file>` for the narration, with the settings written into the project's work folder.
pub(crate) fn narration_settings(work: &Path, settings: &Value) -> Result<(Vec<OsString>, String), String> {
    let settings = checked_settings(settings)?;
    let key = saved_key()?;
    fs::create_dir_all(work).map_err(|_| fail("Không tạo được thư mục làm việc"))?;
    let path = work.join("vibi_voice.json");
    fs::write(&path, serde_json::to_vec_pretty(&settings).map_err(|error| fail(error.to_string()))?)
        .map_err(|_| fail("Không ghi được cài đặt giọng Vibi.pro"))?;
    Ok((vec!["--vibi".into(), path.into_os_string()], key))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_need_a_known_provider_and_a_voice() {
        assert!(checked_settings(&json!({"provider": "elevenlabs", "voice_id": "abc"})).is_ok());
        assert!(checked_settings(&json!({"provider": "minimax", "voice_id": "English_ManWithDeepVoice"})).is_ok());
        assert!(checked_settings(&json!({"provider": "openai", "voice_id": "abc"})).is_err());
        assert!(checked_settings(&json!({"provider": "elevenlabs"})).is_err());
        assert!(checked_settings(&json!({"provider": "elevenlabs", "voice_id": "a", "pad": "x".repeat(5000)})).is_err());
        assert!(checked_settings(&json!("elevenlabs")).is_err());
    }

    #[test]
    fn no_choice_removes_the_file() {
        let dir = std::env::temp_dir().join(format!("dubflow-vibi-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(CLOUD_VOICE_FILE), b"{}").unwrap();
        assert_eq!(write_cloud_voice(&dir, None).unwrap(), None);
        assert!(!dir.join(CLOUD_VOICE_FILE).exists());
        assert_eq!(write_cloud_voice(&dir, Some(&Value::Null)).unwrap(), None);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn cache_names_are_stable_and_differ_by_content() {
        assert_eq!(short_hash("a"), short_hash("a"));
        assert_ne!(short_hash("a"), short_hash("b"));
        assert_eq!(short_hash("https://x/y.mp3").len(), 32);
    }
}
