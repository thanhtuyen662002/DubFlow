//! The user's own AI keys (Claude, Gemini), saved once in Windows Credential
//! Manager (ADR-0043): encrypted by Windows for this Windows user, never in
//! WebView storage or a plain file. The webview asks for a key when it calls
//! that provider.

use serde_json::{json, Value};

use crate::fail;

const PROVIDERS: [&str; 2] = ["claude", "gemini"];
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_providers_and_well_formed_keys_are_accepted() {
        assert_eq!(target("gemini").unwrap(), "DubFlow/ai-key/gemini");
        assert!(target("openai").is_err());
        assert_eq!(checked_key("  sk-ant-api03-abcdef \n").unwrap(), "sk-ant-api03-abcdef");
        assert!(checked_key("short").is_err());
        assert!(checked_key("has a space inside").is_err());
        assert!(checked_key("khóa-có-dấu-123").is_err());
        assert!(checked_key(&"a".repeat(MAX_KEY_CHARS + 1)).is_err());
        assert_eq!(masked("AIzaSyExample1234"), "…1234");
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
