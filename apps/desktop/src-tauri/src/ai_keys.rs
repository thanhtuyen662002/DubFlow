//! The user's own AI keys (Claude, Gemini, ChatGPT, DeepSeek, ...), saved once
//! in Windows Credential Manager (ADR-0043): encrypted by Windows for this
//! Windows user, never in WebView storage or a plain file. The webview asks for
//! a key when it calls that provider; a video job gets it through its
//! supervisor's environment (see `job_key`), never on a command line.
//!
//! Every provider except Claude and Gemini is called from the host through
//! `engine.dubflow.ai` (`ai_ask`), so the webview's network policy stays closed.

use std::io::Write;
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use serde_json::{json, Value};

use crate::{fail, version_root};

/// Same ids as engine/dubflow/ai/client.py PRESETS and apps/desktop/src/features/ai/provider.ts.
pub(crate) const PROVIDERS: [&str; 12] = [
    "claude", "gemini", "openai", "deepseek", "qwen", "kimi", "glm", "grok", "openrouter", "groq", "mistral", "custom",
];
/// The environment variable the worker reads (and removes) for the job's AI key.
pub(crate) const JOB_KEY_ENV: &str = "DUBFLOW_AI_KEY";
const MAX_REQUEST_BYTES: usize = 32 * 1024 * 1024;
const MAX_KEY_CHARS: usize = 512;

fn target(provider: &str) -> Result<String, String> {
    if !PROVIDERS.contains(&provider) {
        return Err(fail("Nhà cung cấp AI không hợp lệ"));
    }
    Ok(format!("DubFlow/ai-key/{provider}"))
}

/// A key as the providers issue them: printable ASCII without spaces.
fn checked_key(key: &str) -> Result<String, String> {
    let key = key.trim();
    if key.len() < 8 || key.len() > MAX_KEY_CHARS || !key.bytes().all(|byte| byte.is_ascii_graphic()) {
        return Err(fail("Khóa API không đúng dạng; hãy dán lại nguyên khóa"));
    }
    Ok(key.to_owned())
}

/// The last four characters, for "Đã lưu (…abcd)".
fn masked(key: &str) -> String {
    let tail: String = key.chars().rev().take(4).collect::<Vec<_>>().into_iter().rev().collect();
    format!("…{tail}")
}

#[cfg(windows)]
mod store {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_NOT_FOUND, FILETIME};
    use windows_sys::Win32::Security::Credentials::{
        CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
    };

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    pub fn read(target: &str) -> Result<Option<String>, String> {
        let name = wide(target);
        let mut credential: *mut CREDENTIALW = std::ptr::null_mut();
        // SAFETY: `name` is NUL-terminated and outlives the call; on success the
        // credential is freed with CredFree below, after its blob is copied.
        if unsafe { CredReadW(name.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) } == 0 {
            let error = unsafe { GetLastError() };
            return if error == ERROR_NOT_FOUND { Ok(None) } else { Err(format!("Không đọc được khóa đã lưu (lỗi {error})")) };
        }
        let bytes = unsafe {
            let value = &*credential;
            let blob = if value.CredentialBlob.is_null() { &[][..] } else { std::slice::from_raw_parts(value.CredentialBlob, value.CredentialBlobSize as usize) };
            let copy = blob.to_vec();
            CredFree(credential as *const core::ffi::c_void);
            copy
        };
        String::from_utf8(bytes).map(Some).map_err(|_| "Khóa đã lưu bị hỏng; hãy lưu lại".to_owned())
    }

    pub fn write(target: &str, secret: &str) -> Result<(), String> {
        let mut name = wide(target);
        let mut user = wide("DubFlow");
        let mut blob = secret.as_bytes().to_vec();
        let credential = CREDENTIALW {
            Flags: 0,
            Type: CRED_TYPE_GENERIC,
            TargetName: name.as_mut_ptr(),
            Comment: std::ptr::null_mut(),
            LastWritten: FILETIME { dwLowDateTime: 0, dwHighDateTime: 0 },
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_mut_ptr(),
            // This machine only: the key does not roam with the Windows profile.
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount: 0,
            Attributes: std::ptr::null_mut(),
            TargetAlias: std::ptr::null_mut(),
            UserName: user.as_mut_ptr(),
        };
        // SAFETY: every pointer refers to a buffer that lives until the call returns.
        if unsafe { CredWriteW(&credential, 0) } == 0 {
            return Err(format!("Không lưu được khóa (lỗi {})", unsafe { GetLastError() }));
        }
        Ok(())
    }

    pub fn delete(target: &str) -> Result<(), String> {
        let name = wide(target);
        // SAFETY: `name` is NUL-terminated and outlives the call.
        if unsafe { CredDeleteW(name.as_ptr(), CRED_TYPE_GENERIC, 0) } == 0 {
            let error = unsafe { GetLastError() };
            if error != ERROR_NOT_FOUND {
                return Err(format!("Không xóa được khóa (lỗi {error})"));
            }
        }
        Ok(())
    }
}

#[cfg(not(windows))]
mod store {
    const UNSUPPORTED: &str = "Lưu khóa chỉ hỗ trợ trên Windows";

    pub fn read(_target: &str) -> Result<Option<String>, String> {
        Ok(None)
    }

    pub fn write(_target: &str, _secret: &str) -> Result<(), String> {
        Err(UNSUPPORTED.to_owned())
    }

    pub fn delete(_target: &str) -> Result<(), String> {
        Ok(())
    }
}

/// Which providers have a saved key, masked to the last four characters.
#[tauri::command]
pub fn ai_key_status() -> Result<Value, String> {
    let mut status = serde_json::Map::new();
    for provider in PROVIDERS {
        let saved = store::read(&target(provider)?)?;
        status.insert(provider.to_owned(), json!(saved.as_deref().map(masked)));
    }
    Ok(Value::Object(status))
}

/// The saved key for one provider, for the webview's call to that provider.
#[tauri::command]
pub fn ai_key_get(provider: String) -> Result<Option<String>, String> {
    store::read(&target(&provider)?)
}

#[tauri::command]
pub fn ai_key_set(provider: String, key: String) -> Result<String, String> {
    let key = checked_key(&key)?;
    store::write(&target(&provider)?, &key)?;
    Ok(masked(&key))
}

#[tauri::command]
pub fn ai_key_delete(provider: String) -> Result<(), String> {
    store::delete(&target(&provider)?)
}

/// Each provider's own page for creating an API key. Fixed here, so the webview
/// can only open these pages, never an address it was handed.
pub(crate) fn key_page(provider: &str) -> Option<&'static str> {
    Some(match provider {
        "claude" => "https://console.anthropic.com/settings/keys",
        "gemini" => "https://aistudio.google.com/apikey",
        "openai" => "https://platform.openai.com/api-keys",
        "deepseek" => "https://platform.deepseek.com/api_keys",
        "qwen" => "https://modelstudio.console.alibabacloud.com/",
        "kimi" => "https://platform.kimi.ai/",
        "glm" => "https://z.ai/manage-apikey/apikey-list",
        "grok" => "https://console.x.ai/",
        "openrouter" => "https://openrouter.ai/keys",
        "groq" => "https://console.groq.com/keys",
        "mistral" => "https://console.mistral.ai/api-keys",
        _ => return None,
    })
}

/// Opens the provider's key page in the user's default browser.
#[tauri::command]
pub fn ai_key_page(provider: String) -> Result<(), String> {
    let url = key_page(&provider).ok_or_else(|| fail("Nhà cung cấp này không có trang lấy khóa"))?;
    Command::new("explorer.exe").arg(url).spawn()
        .map_err(|error| fail(format!("Không mở được trình duyệt: {error}")))?;
    Ok(())
}

/// The saved key for a video job's AI translation, if any.
pub(crate) fn job_key(provider: &str) -> Result<Option<String>, String> {
    store::read(&target(provider)?)
}

/// One request to an OpenAI-compatible provider (or a model list), answered by
/// `python -m engine.dubflow.ai`: `{ok, value, model}`, `{ok, models, suggested}`
/// or `{ok: false, code, message}`. The key goes through the environment.
#[tauri::command]
pub async fn ai_ask(request: Value, key: Option<String>) -> Result<Value, String> {
    let provider = request.get("provider").and_then(Value::as_str).unwrap_or_default().to_owned();
    target(&provider)?;
    if !matches!(request.get("action").and_then(Value::as_str), Some("ask" | "models")) {
        return Err(fail("Yêu cầu AI không hợp lệ"));
    }
    let key = match key.map(|key| key.trim().to_owned()).filter(|key| !key.is_empty()) {
        Some(key) => Some(checked_key(&key)?),
        None => None,
    };
    let body = serde_json::to_vec(&request).map_err(|error| fail(error.to_string()))?;
    if body.len() > MAX_REQUEST_BYTES {
        return Err(fail("Yêu cầu AI quá lớn"));
    }
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app")).args(["-B", "-m", "engine.dubflow.ai"])
        .env("PYTHONUTF8", "1").env("PYTHONNOUSERSITE", "1");
    if let Some(key) = &key {
        command.env(JOB_KEY_ENV, key);
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
    .map_err(|error| fail(format!("Không chạy được bộ gọi AI: {error}")))?;
    let text = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str::<Value>(text.lines().last().unwrap_or_default())
        .ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ gọi AI không trả về dữ liệu"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_providers_and_well_formed_keys_are_accepted() {
        assert_eq!(target("gemini").unwrap(), "DubFlow/ai-key/gemini");
        assert_eq!(target("deepseek").unwrap(), "DubFlow/ai-key/deepseek");
        assert!(target("unknown").is_err());
        assert!(target("../claude").is_err());
        assert_eq!(checked_key("  sk-ant-api03-abcdef \n").unwrap(), "sk-ant-api03-abcdef");
        assert!(checked_key("short").is_err());
        assert!(checked_key("has a space inside").is_err());
        assert!(checked_key("khóa-có-dấu-123").is_err());
        assert!(checked_key(&"a".repeat(MAX_KEY_CHARS + 1)).is_err());
        assert_eq!(masked("AIzaSyExample1234"), "…1234");
    }

    #[test]
    fn key_pages_are_fixed_https_pages_of_known_providers() {
        assert_eq!(key_page("gemini"), Some("https://aistudio.google.com/apikey"));
        assert_eq!(key_page("custom"), None);
        assert_eq!(key_page("https://example.com"), None);
        for provider in PROVIDERS.iter().filter(|id| **id != "custom") {
            assert!(key_page(provider).is_some_and(|url| url.starts_with("https://")), "{provider}");
        }
    }

    /// Writes to this user's Credential Manager under a test-only name and removes it.
    #[cfg(windows)]
    #[test]
    fn a_key_round_trips_through_credential_manager() {
        let name = format!("DubFlow/ai-key-test/{}", std::process::id());
        store::delete(&name).unwrap();
        assert_eq!(store::read(&name).unwrap(), None);
        store::write(&name, "test-key-not-real-1234").unwrap();
        assert_eq!(store::read(&name).unwrap().as_deref(), Some("test-key-not-real-1234"));
        store::write(&name, "test-key-not-real-5678").unwrap();
        assert_eq!(store::read(&name).unwrap().as_deref(), Some("test-key-not-real-5678"));
        store::delete(&name).unwrap();
        assert_eq!(store::read(&name).unwrap(), None);
        store::delete(&name).unwrap();
    }
}
