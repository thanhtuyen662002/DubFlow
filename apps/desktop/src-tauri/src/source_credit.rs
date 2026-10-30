//! Optional user attribution beside a finished export; never part of AI input.
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;

const FILE: &str = "source_credit.json";
const PARTIAL: &str = "source_credit.json.partial";
const MAX_BYTES: u64 = 16 * 1024;
// Serialize saves so two invocations cannot share the fixed partial filename.
static SAVE: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Rights { Own, Permission, Cc, Unknown }

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct SourceCredit {
    schema_version: u32,
    creator: String,
    platform: String,
    url: String,
    rights: Rights,
    note: String,
}

impl SourceCredit {
    fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err("Phiên bản thông tin nguồn không được hỗ trợ".into());
        }
        for (value, limit, label) in [
            (&self.creator, 120, "Tác giả"), (&self.platform, 40, "Nền tảng"),
            (&self.url, 500, "Link"), (&self.note, 500, "Ghi chú"),
        ] {
            if value.chars().any(char::is_control) {
                return Err(format!("{label} không được chứa ký tự điều khiển"));
            }
            if value.chars().count() > limit {
                return Err(format!("{label} tối đa {limit} ký tự"));
            }
        }
        if !self.url.is_empty() && (!(self.url.starts_with("http://") || self.url.starts_with("https://"))
            || !tauri::Url::parse(&self.url).is_ok_and(|url| url.host_str().is_some())) {
            return Err("Link phải bắt đầu bằng http:// hoặc https:// và có tên miền hợp lệ".into());
        }
        Ok(())
    }
}

/// Null distinguishes a missing file from an explicitly saved empty credit.
#[tauri::command]
pub fn source_credit_get(output_path: String) -> Result<Option<SourceCredit>, String> {
    let path = super::review_dir(&output_path)?.join(FILE);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("Không đọc được thông tin nguồn".into()),
    };
    if !metadata.is_file() || metadata.len() > MAX_BYTES {
        return Err("File thông tin nguồn không hợp lệ hoặc quá lớn".into());
    }
    let text = fs::read_to_string(path).map_err(|_| "Không đọc được thông tin nguồn")?;
    let credit: SourceCredit = serde_json::from_str(&text).map_err(|_| "Thông tin nguồn đã lưu không hợp lệ")?;
    credit.validate()?;
    Ok(Some(credit))
}

#[tauri::command]
pub fn source_credit_set(output_path: String, credit: SourceCredit) -> Result<(), String> {
    credit.validate()?;
    let dir = super::review_dir(&output_path)?;
    let _guard = SAVE.lock().map_err(|_| "Không lưu được thông tin nguồn; hãy mở lại ứng dụng")?;
    let partial = dir.join(PARTIAL);
    // A crash can leave a regular partial file. Never follow a linked entry.
    match fs::symlink_metadata(&partial) {
        Ok(metadata) if metadata.is_file() => fs::remove_file(&partial).map_err(|_| "Không dọn được bản lưu tạm")?,
        Ok(_) => return Err("File lưu tạm thông tin nguồn không hợp lệ".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
        Err(_) => return Err("Không kiểm tra được bản lưu tạm".into()),
    }
    let bytes = serde_json::to_vec_pretty(&credit).map_err(|_| "Không tạo được thông tin nguồn")?;
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&partial)
        .map_err(|_| "Không tạo được bản lưu tạm thông tin nguồn")?;
    file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|_| "Không ghi được thông tin nguồn")?;
    drop(file);
    fs::rename(partial, dir.join(FILE)).map_err(|_| "Không lưu được thông tin nguồn".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn empty() -> SourceCredit {
        SourceCredit { schema_version: 1, creator: "".into(), platform: "".into(), url: "".into(), rights: Rights::Unknown, note: "".into() }
    }

    #[test]
    fn validates_schema_rights_and_all_text_fields() {
        assert!(empty().validate().is_ok());
        for rights in [Rights::Own, Rights::Permission, Rights::Cc, Rights::Unknown] {
            assert!(SourceCredit { rights, ..empty() }.validate().is_ok());
        }
        for (field, limit) in [("creator", 120), ("platform", 40), ("url", 500), ("note", 500)] {
            let mut value = serde_json::to_value(empty()).unwrap();
            let prefix = if field == "url" { "https://example.com/" } else { "" };
            value[field] = json!(format!("{prefix}{}", "🌱".repeat(limit - prefix.len())));
            assert!(serde_json::from_value::<SourceCredit>(value.clone()).unwrap().validate().is_ok(), "{field} at limit");
            value[field] = json!(format!("{}x", value[field].as_str().unwrap()));
            assert!(serde_json::from_value::<SourceCredit>(value.clone()).unwrap().validate().is_err(), "{field} too long");
            for control in ['\0', '\n', '\r', '\t', '\u{7f}', '\u{85}', '\u{9f}'] {
                value[field] = json!(format!("{prefix}{control}"));
                assert!(serde_json::from_value::<SourceCredit>(value.clone()).unwrap().validate().is_err(), "{field} control");
            }
            value[field] = Value::Null;
            assert!(serde_json::from_value::<SourceCredit>(value.clone()).is_err());
            value.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<SourceCredit>(value).is_err());
        }
        assert!(SourceCredit { schema_version: 2, ..empty() }.validate().is_err());
        let mut value = serde_json::to_value(empty()).unwrap();
        value["rights"] = json!("licensed");
        assert!(serde_json::from_value::<SourceCredit>(value).is_err());
        for url in ["ftp://example.com", "javascript:alert(1)", "https://", "example.com", " https://example.com"] {
            assert!(SourceCredit { url: url.into(), ..empty() }.validate().is_err());
        }
        for url in ["", "http://example.com", "https://www.youtube.com/watch?v=123"] {
            assert!(SourceCredit { url: url.into(), ..empty() }.validate().is_ok());
        }
    }

    #[test]
    fn reads_missing_and_saved_credit_and_atomically_replaces_it() {
        let root = std::env::temp_dir().join(format!("dubflow-source-credit-{}", std::process::id()));
        let dir = root.join("DubFlow Output/clip - vi");
        fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
        fs::write(dir.join(".dubflow-work/translation.json"), "{}").unwrap();
        fs::write(dir.join("final_vi.mp4"), "finished").unwrap();
        let path = dir.join("final_vi.mp4").to_string_lossy().into_owned();
        assert_eq!(source_credit_get(path.clone()).unwrap(), None);
        source_credit_set(path.clone(), empty()).unwrap();
        assert_eq!(source_credit_get(path.clone()).unwrap(), Some(empty()));
        let credit = SourceCredit { creator: "Tác giả 🌱".into(), rights: Rights::Permission, ..empty() };
        fs::write(dir.join(PARTIAL), "interrupted save").unwrap();
        source_credit_set(path.clone(), credit.clone()).unwrap();
        assert_eq!(source_credit_get(path.clone()).unwrap(), Some(credit.clone()));
        assert!(!dir.join(PARTIAL).exists());
        assert!(source_credit_set(path.clone(), SourceCredit { note: "x\n".into(), ..empty() }).is_err());
        assert_eq!(source_credit_get(path.clone()).unwrap(), Some(credit));
        fs::create_dir(dir.join(PARTIAL)).unwrap();
        assert!(source_credit_set(path.clone(), empty()).is_err());
        assert!(source_credit_get(path.clone()).unwrap().is_some());
        let invalid = SourceCredit { note: "bad\n".into(), ..empty() };
        fs::write(dir.join(FILE), serde_json::to_vec(&invalid).unwrap()).unwrap();
        assert!(source_credit_get(path.clone()).is_err());
        fs::write(dir.join(FILE), "{broken").unwrap();
        assert!(source_credit_get(path.clone()).is_err());
        fs::write(dir.join(FILE), vec![b' '; MAX_BYTES as usize + 1]).unwrap();
        assert!(source_credit_get(path.clone()).is_err());
        assert!(source_credit_get(root.join("missing.mp4").to_string_lossy().into_owned()).is_err());
        fs::write(root.join("outside.mp4"), "finished").unwrap();
        let outside = root.join("outside.mp4").to_string_lossy().into_owned();
        assert!(source_credit_get(outside.clone()).is_err());
        assert!(source_credit_set(outside, empty()).is_err());
        assert!(!root.join(FILE).exists());
        fs::remove_file(dir.join(".dubflow-work/translation.json")).unwrap();
        assert!(source_credit_get(path.clone()).is_err());
        assert!(source_credit_set(path, empty()).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
