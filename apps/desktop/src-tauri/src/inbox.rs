//! Requests an agent leaves for the app: dubbing jobs for the queue (ADR-0058, Issue #998) and edits for
//! Chỉnh sửa (ADR-0059, Issue #1002).
//!
//! DubFlow's MCP server writes `<data root>\control\inbox\<id>.json`; `take_inbox` hands them to the
//! frontend. Every request is untrusted input: bounded in number and size, checked again here, and only
//! ever turned into a queue job for a local video file or an editor draft of a finished video. Nothing in a
//! request is run. An accepted file moves to `inbox\accepted`, a refused one to `inbox\rejected` with a
//! `.reason.txt` the MCP server reports. `inbox_report` writes the frontend's answer to an accepted edit to
//! `inbox\results\<id>.json`.

use serde::Serialize;
use serde_json::{Map, Value};
use std::fs;
use std::path::{Path, PathBuf};

const MAX_REQUESTS: usize = 20;
const MAX_SCANNED: usize = 200;
const MAX_BYTES: u64 = 64 * 1024;
/// An edit request carries the whole draft.
const MAX_EDIT_BYTES: u64 = 1024 * 1024;
const MAX_REPORT_BYTES: usize = 16 * 1024;
const QUALITIES: [&str; 3] = ["source", "1080p", "720p"];
const REPORT_STATES: [&str; 4] = ["SAVED", "EXPORTING", "REJECTED", "FAILED"];
const OPTION_KEYS: [&str; 8] = ["dub", "voice_id", "source_language", "burn_in", "blur_auto", "separate_background", "multi_voice", "translation_engine"];

/// The options of a request; `None` keeps the user's own default for that option.
#[derive(Debug, Default, PartialEq, Serialize)]
pub struct InboxOptions {
    pub dub: Option<bool>,
    pub voice_id: Option<String>,
    /// "auto" (detect), "zh" or "en".
    pub source_language: Option<String>,
    pub burn_in: Option<bool>,
    pub blur_auto: Option<bool>,
    pub separate_background: Option<bool>,
    pub multi_voice: Option<bool>,
    /// "argos" (offline) or "hachimi".
    pub translation_engine: Option<String>,
}

#[derive(Debug, PartialEq, Serialize)]
pub struct InboxRequest {
    pub id: String,
    pub source_path: String,
    pub title: String,
    pub options: InboxOptions,
}

/// Xuất video's options an edit request may ask for; `None` is the editor's default.
#[derive(Debug, Default, PartialEq, Serialize)]
pub struct EditExport {
    pub file_name: Option<String>,
    pub quality: Option<String>,
}

/// An agent's Chỉnh sửa draft for a finished dubbed video (ADR-0059); the frontend parses `project` with the editor's own model.
#[derive(Debug, PartialEq, Serialize)]
pub struct EditRequest {
    pub id: String,
    pub output_path: String,
    pub project: Value,
    pub export: Option<EditExport>,
}

#[derive(Debug, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Taken {
    Dubbing(InboxRequest),
    Edit(EditRequest),
}

impl Taken {
    pub fn id(&self) -> &str {
        match self {
            Taken::Dubbing(request) => &request.id,
            Taken::Edit(request) => &request.id,
        }
    }
}

#[tauri::command]
pub fn take_inbox() -> Result<Vec<Taken>, String> {
    let root = crate::data_root()?;
    Ok(take_from(&root.join("control").join("inbox")))
}

/// The frontend's answer to an edit request it accepted, for the MCP server to read.
#[tauri::command]
pub fn inbox_report(id: String, report: Value) -> Result<(), String> {
    let root = crate::data_root()?;
    write_report(&root.join("control").join("inbox"), &id, &report)
}

pub(crate) fn write_report(inbox: &Path, id: &str, report: &Value) -> Result<(), String> {
    if !valid_id(id) || !inbox.join("accepted").join(format!("{id}.json")).is_file() {
        return Err(crate::fail("Không có yêu cầu này trong hộp thư của DubFlow"));
    }
    let mut value = report.as_object().cloned().ok_or_else(|| crate::fail("Câu trả lời phải là một đối tượng"))?;
    if !value.get("state").and_then(Value::as_str).is_some_and(|state| REPORT_STATES.contains(&state)) {
        return Err(crate::fail("Trạng thái trả lời không hợp lệ"));
    }
    value.insert("schema_version".into(), Value::from(1));
    value.insert("id".into(), Value::from(id));
    let bytes = serde_json::to_vec(&Value::Object(value)).map_err(|_| crate::fail("Câu trả lời không hợp lệ"))?;
    if bytes.len() > MAX_REPORT_BYTES {
        return Err(crate::fail("Câu trả lời quá lớn"));
    }
    let dir = inbox.join("results");
    fs::create_dir_all(&dir).map_err(|_| crate::fail("Không tạo được thư mục kết quả"))?;
    let partial = dir.join(format!("{id}.json.partial"));
    fs::write(&partial, bytes).and_then(|()| fs::rename(&partial, dir.join(format!("{id}.json"))))
        .map_err(|_| crate::fail("Không ghi được câu trả lời"))
}

/// Accepted requests in id order (ids start with their time), at most `MAX_REQUESTS` a call.
pub(crate) fn take_from(inbox: &Path) -> Vec<Taken> {
    let Ok(entries) = fs::read_dir(inbox) else { return Vec::new() };
    let mut names: Vec<String> = entries
        .filter_map(Result::ok)
        .take(MAX_SCANNED * 5)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| name.ends_with(".json"))
        .collect();
    names.sort();
    names.truncate(MAX_SCANNED);
    let mut taken = Vec::new();
    for name in names {
        if taken.len() >= MAX_REQUESTS {
            break;
        }
        let path = inbox.join(&name);
        match read_request(&path, &name) {
            Ok(request) => {
                if inbox.join("accepted").join(&name).exists() {
                    reject(inbox, &name, "a request with this id was already queued");
                } else if move_to(inbox, "accepted", &name).is_ok() {
                    taken.push(request);
                }
            }
            Err(reason) => reject(inbox, &name, &reason),
        }
    }
    taken
}

fn move_to(inbox: &Path, folder: &str, name: &str) -> std::io::Result<PathBuf> {
    let dir = inbox.join(folder);
    fs::create_dir_all(&dir)?;
    let target = dir.join(name);
    fs::rename(inbox.join(name), &target)?;
    Ok(target)
}

fn reject(inbox: &Path, name: &str, reason: &str) {
    if let Ok(target) = move_to(inbox, "rejected", name) {
        let mut note = target.into_os_string();
        note.push(".reason.txt");
        let _ = fs::write(note, reason.chars().take(400).collect::<String>());
    }
}

fn valid_id(id: &str) -> bool {
    let Some(rest) = id.strip_prefix("mcp-") else { return false };
    (8..=64).contains(&rest.len()) && rest.bytes().all(|byte| byte.is_ascii_digit() || byte.is_ascii_lowercase() || byte == b'-') && crate::validate_id(id).is_ok()
}

fn read_request(path: &Path, name: &str) -> Result<Taken, String> {
    let size = fs::symlink_metadata(path).map_err(|_| "unreadable".to_string())?.len();
    if size > MAX_EDIT_BYTES {
        return Err("request is larger than 1 MiB".into());
    }
    let bytes = fs::read(path).map_err(|_| "unreadable".to_string())?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "not JSON".to_string())?;
    let id = name.trim_end_matches(".json");
    match value.get("kind").and_then(Value::as_str) {
        Some("edit") => parse_edit(&value, id).map(Taken::Edit),
        _ if size > MAX_BYTES => Err("request is larger than 64 KiB".into()),
        _ => parse_request(&value, id).map(Taken::Dubbing),
    }
}

/// A plain file name for Xuất video: no folder, no character Windows refuses in a name.
fn valid_file_name(name: &str) -> bool {
    let trimmed = name.trim();
    !trimmed.is_empty() && trimmed.chars().count() <= 120 && trimmed != "." && trimmed != ".."
        && !trimmed.chars().any(|ch| ch.is_control() || "\\/:*?\"<>|".contains(ch))
}

pub(crate) fn parse_edit(value: &Value, file_id: &str) -> Result<EditRequest, String> {
    let object = value.as_object().ok_or("request must be an object")?;
    if object.get("schema_version") != Some(&Value::from(1)) {
        return Err("unsupported request (schema_version 1, kind edit)".into());
    }
    let id = object.get("id").and_then(Value::as_str).unwrap_or_default();
    if !valid_id(id) || id != file_id {
        return Err("id must be mcp-… and match the file name".into());
    }
    let output = object.get("output_path").and_then(Value::as_str).unwrap_or_default();
    if output.len() > 4096 || Path::new(output).file_name().is_none_or(|name| name != "final_vi.mp4") || crate::review_dir(output).is_err() {
        return Err("output_path must be a finished dubbed video (final_vi.mp4 in DubFlow Output)".into());
    }
    let project = object.get("project").filter(|project| project.is_object()).ok_or("project must be the editor draft object")?;
    let export = match object.get("export") {
        None | Some(Value::Null) => None,
        Some(Value::Object(options)) => {
            if let Some(key) = options.keys().find(|key| !["file_name", "quality"].contains(&key.as_str())) {
                return Err(format!("unknown export option {}", key.chars().take(40).collect::<String>()));
            }
            let file_name = match options.get("file_name") {
                None | Some(Value::Null) => None,
                Some(Value::String(name)) if valid_file_name(name) => Some(name.trim().to_string()),
                Some(_) => return Err("export.file_name must be a plain file name".into()),
            };
            let quality = optional_choice(options, "quality", &QUALITIES).map_err(|_| "export.quality must be source, 1080p or 720p".to_string())?;
            Some(EditExport { file_name, quality })
        }
        Some(_) => return Err("export must be an object or null".into()),
    };
    Ok(EditRequest { id: id.to_string(), output_path: output.to_string(), project: project.clone(), export })
}

fn optional_bool(options: &Map<String, Value>, key: &str) -> Result<Option<bool>, String> {
    match options.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Bool(value)) => Ok(Some(*value)),
        Some(_) => Err(format!("options.{key} must be true, false or null")),
    }
}

fn optional_choice(options: &Map<String, Value>, key: &str, allowed: &[&str]) -> Result<Option<String>, String> {
    match options.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) if allowed.contains(&value.as_str()) => Ok(Some(value.clone())),
        Some(_) => Err(format!("options.{key} must be one of {}", allowed.join(", "))),
    }
}

pub(crate) fn parse_request(value: &Value, file_id: &str) -> Result<InboxRequest, String> {
    let object = value.as_object().ok_or("request must be an object")?;
    if object.get("schema_version") != Some(&Value::from(1)) || object.get("kind").and_then(Value::as_str) != Some("dubbing") {
        return Err("unsupported request (schema_version 1, kind dubbing)".into());
    }
    let id = object.get("id").and_then(Value::as_str).unwrap_or_default();
    if !valid_id(id) || id != file_id {
        return Err("id must be mcp-… and match the file name".into());
    }
    let source = object.get("source_path").and_then(Value::as_str).unwrap_or_default();
    let path = Path::new(source);
    let video = path
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| crate::VIDEO_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()));
    if source.len() > 4096 || source.chars().any(char::is_control) || !path.is_absolute() || !video {
        return Err("source_path must be the absolute path of a video file".into());
    }
    if !path.canonicalize().is_ok_and(|resolved| resolved.is_file()) {
        return Err("source video is missing".into());
    }
    let title = match object.get("title") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) => text.chars().map(|ch| if ch.is_control() { ' ' } else { ch }).take(200).collect::<String>().trim().to_string(),
        Some(_) => return Err("title must be text".into()),
    };
    let empty = Map::new();
    let options = match object.get("options") {
        None | Some(Value::Null) => &empty,
        Some(Value::Object(options)) => options,
        Some(_) => return Err("options must be an object".into()),
    };
    if let Some(key) = options.keys().find(|key| !OPTION_KEYS.contains(&key.as_str())) {
        return Err(format!("unknown option {}", key.chars().take(40).collect::<String>()));
    }
    let voice_id = match options.get("voice_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(voice)) if (1..=96).contains(&voice.len()) && voice.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-') => Some(voice.clone()),
        Some(_) => return Err("options.voice_id is invalid".into()),
    };
    Ok(InboxRequest {
        id: id.to_string(),
        source_path: source.to_string(),
        title,
        options: InboxOptions {
            dub: optional_bool(options, "dub")?,
            voice_id,
            source_language: optional_choice(options, "source_language", &["auto", "zh", "en"])?,
            burn_in: optional_bool(options, "burn_in")?,
            blur_auto: optional_bool(options, "blur_auto")?,
            separate_background: optional_bool(options, "separate_background")?,
            multi_voice: optional_bool(options, "multi_voice")?,
            translation_engine: optional_choice(options, "translation_engine", &["argos", "hachimi"])?,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("dubflow-inbox-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(base.join("inbox")).unwrap();
        base
    }

    fn request(id: &str, source: &Path) -> Value {
        json!({"schema_version": 1, "kind": "dubbing", "id": id, "created_at": "2026-11-28T10:00:00Z",
               "source_path": source.to_string_lossy(), "title": "Đập\nTam Hiệp",
               "options": {"dub": true, "voice_id": "vi-a", "source_language": "zh", "burn_in": null, "blur_auto": true,
                           "separate_background": null, "multi_voice": null, "translation_engine": "argos"}})
    }

    #[test]
    fn accepted_requests_come_back_in_order_and_move_aside() {
        let base = temp("accept");
        let video = base.join("clip.mp4");
        fs::write(&video, b"x").unwrap();
        let inbox = base.join("inbox");
        for id in ["mcp-20261128-100001-bbbbbbbb", "mcp-20261128-100000-aaaaaaaa"] {
            fs::write(inbox.join(format!("{id}.json")), request(id, &video).to_string()).unwrap();
        }
        fs::write(inbox.join("mcp-20261128-100002-cccccccc.json.partial"), b"{").unwrap();
        let taken = take_from(&inbox);
        assert_eq!(taken.iter().map(Taken::id).collect::<Vec<_>>(), ["mcp-20261128-100000-aaaaaaaa", "mcp-20261128-100001-bbbbbbbb"]);
        let Taken::Dubbing(first) = &taken[0] else { panic!("a dubbing request") };
        assert_eq!(serde_json::to_value(&taken[0]).unwrap()["kind"], "dubbing", "the frontend tells the kinds apart");
        assert_eq!(first.title, "Đập Tam Hiệp");
        assert_eq!(first.options, InboxOptions { dub: Some(true), voice_id: Some("vi-a".into()), source_language: Some("zh".into()), blur_auto: Some(true), translation_engine: Some("argos".into()), ..InboxOptions::default() });
        assert!(inbox.join("accepted").join("mcp-20261128-100000-aaaaaaaa.json").is_file());
        assert!(inbox.join("mcp-20261128-100002-cccccccc.json.partial").is_file(), "a file still being written is left alone");
        assert!(take_from(&inbox).is_empty(), "each request is taken once");
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn refused_requests_move_to_rejected_with_a_reason() {
        let base = temp("reject");
        let video = base.join("clip.mp4");
        fs::write(&video, b"x").unwrap();
        let inbox = base.join("inbox");
        let cases: Vec<(&str, Value, &str)> = vec![
            ("mcp-20261128-100000-00000001", { let mut v = request("mcp-20261128-100000-00000001", &video); v["source_path"] = json!(base.join("gone.mp4").to_string_lossy()); v }, "missing"),
            ("mcp-20261128-100000-00000002", { let mut v = request("mcp-20261128-100000-00000002", &video); v["options"]["run"] = json!("calc.exe"); v }, "unknown option"),
            ("mcp-20261128-100000-00000003", request("mcp-20261128-100000-99999999", &video), "file name"),
            ("mcp-20261128-100000-00000004", { let mut v = request("mcp-20261128-100000-00000004", &video); v["options"]["dub"] = json!("yes"); v }, "options.dub"),
            ("mcp-20261128-100000-00000005", { let mut v = request("mcp-20261128-100000-00000005", &video); v["source_path"] = json!("clip.mp4"); v }, "absolute"),
            ("mcp-20261128-100000-00000006", { let mut v = request("mcp-20261128-100000-00000006", &video); v["schema_version"] = json!(2); v }, "unsupported"),
        ];
        for (id, value, _) in &cases {
            fs::write(inbox.join(format!("{id}.json")), value.to_string()).unwrap();
        }
        fs::write(inbox.join("job-123.json"), request("job-123", &video).to_string()).unwrap();
        let mut padded = request("mcp-20261128-100000-00000007", &video);
        padded["title"] = json!("x".repeat(70 * 1024));
        fs::write(inbox.join("mcp-20261128-100000-00000007.json"), padded.to_string()).unwrap();
        fs::write(inbox.join("mcp-20261128-100000-00000008.json"), vec![b' '; 1100 * 1024]).unwrap();
        assert!(take_from(&inbox).is_empty());
        for (id, _, words) in &cases {
            let reason = fs::read_to_string(inbox.join("rejected").join(format!("{id}.json.reason.txt"))).unwrap();
            assert!(reason.contains(words), "{id}: {reason}");
        }
        assert!(inbox.join("rejected").join("job-123.json").is_file());
        assert!(fs::read_to_string(inbox.join("rejected").join("mcp-20261128-100000-00000007.json.reason.txt")).unwrap().contains("64 KiB"));
        assert!(fs::read_to_string(inbox.join("rejected").join("mcp-20261128-100000-00000008.json.reason.txt")).unwrap().contains("1 MiB"));
        let _ = fs::remove_dir_all(base);
    }

    /// A finished dubbed video as review_dir accepts it: final_vi.mp4 in DubFlow Output, with its translation.
    fn finished(base: &Path) -> PathBuf {
        let dir = base.join("DubFlow Output").join("clip - vi").join("run-0123456789ab");
        fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
        fs::write(dir.join(".dubflow-work").join("translation.json"), b"{\"cues\": []}").unwrap();
        fs::write(dir.join("final_vi.mp4"), b"x").unwrap();
        dir.join("final_vi.mp4")
    }

    fn edit(id: &str, output: &Path) -> Value {
        json!({"schema_version": 1, "kind": "edit", "id": id, "created_at": "2026-11-28T10:00:00Z", "output_path": output.to_string_lossy(),
               "project": {"schema_version": 4, "segments": [{"id": "s1", "mediaId": "main", "start": 0, "end": 5}]},
               "export": {"file_name": "Đập (agent)", "quality": "1080p"}})
    }

    #[test]
    fn edit_requests_carry_the_draft_and_their_answers_go_to_results() {
        let base = temp("edit");
        let output = finished(&base);
        let inbox = base.join("inbox");
        let id = "mcp-20261128-100000-eeeeeeee";
        let mut big = edit(id, &output);
        big["project"]["texts"] = json!(vec!["chữ"; 20_000]);
        assert!(big.to_string().len() > 64 * 1024);
        fs::write(inbox.join(format!("{id}.json")), big.to_string()).unwrap();
        let taken = take_from(&inbox);
        let [Taken::Edit(request)] = taken.as_slice() else { panic!("one edit request: {taken:?}") };
        assert_eq!(request.export, Some(EditExport { file_name: Some("Đập (agent)".into()), quality: Some("1080p".into()) }));
        assert_eq!(request.project["segments"][0]["id"], "s1");
        assert_eq!(serde_json::to_value(&taken[0]).unwrap()["kind"], "edit");

        write_report(&inbox, id, &json!({"state": "EXPORTING", "message": "Đang xuất", "path": "D:\\x.mp4"})).unwrap();
        let answer: Value = serde_json::from_slice(&fs::read(inbox.join("results").join(format!("{id}.json"))).unwrap()).unwrap();
        assert_eq!((answer["state"].as_str(), answer["id"].as_str(), answer["schema_version"].as_u64()), (Some("EXPORTING"), Some(id), Some(1)));
        write_report(&inbox, id, &json!({"state": "SAVED"})).unwrap();
        assert!(write_report(&inbox, id, &json!({"state": "COMPLETED"})).is_err(), "only the frontend's own states");
        assert!(write_report(&inbox, id, &json!({"state": "SAVED", "message": "x".repeat(20_000)})).is_err());
        assert!(write_report(&inbox, "mcp-20261128-100000-ffffffff", &json!({"state": "SAVED"})).is_err(), "only an accepted request");
        assert!(write_report(&inbox, "../../x", &json!({"state": "SAVED"})).is_err());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn edit_requests_are_checked() {
        let base = temp("edit-refused");
        let output = finished(&base);
        let id = "mcp-20261128-100000-eeeeeeee";
        assert!(parse_edit(&edit(id, &output), id).is_ok());
        let bare = base.join("elsewhere").join("final_vi.mp4");
        fs::create_dir_all(bare.parent().unwrap()).unwrap();
        fs::write(&bare, b"x").unwrap();
        let cases: Vec<(Value, &str)> = vec![
            ({ let mut v = edit(id, &output); v["output_path"] = json!(bare.to_string_lossy()); v }, "finished dubbed video"),
            ({ let mut v = edit(id, &output); v["output_path"] = json!(output.with_file_name("captions_vi.srt").to_string_lossy()); v }, "finished dubbed video"),
            ({ let mut v = edit(id, &output); v["project"] = json!([1]); v }, "draft object"),
            ({ let mut v = edit(id, &output); v["export"]["file_name"] = json!("..\\..\\x"); v }, "plain file name"),
            ({ let mut v = edit(id, &output); v["export"]["quality"] = json!("4k"); v }, "export.quality"),
            ({ let mut v = edit(id, &output); v["export"]["folder"] = json!("C:\\"); v }, "unknown export option"),
            ({ let mut v = edit(id, &output); v["schema_version"] = json!(2); v }, "unsupported"),
        ];
        for (value, words) in cases {
            let error = parse_edit(&value, id).unwrap_err();
            assert!(error.contains(words), "{words}: {error}");
        }
        assert!(parse_edit(&edit(id, &output), "mcp-20261128-100000-00000000").unwrap_err().contains("file name"));
        let mut no_export = edit(id, &output);
        no_export["export"] = Value::Null;
        assert_eq!(parse_edit(&no_export, id).unwrap().export, None);
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn a_text_file_is_not_a_video_and_a_missing_inbox_is_empty() {
        let base = temp("kinds");
        let text = base.join("notes.txt");
        fs::write(&text, b"x").unwrap();
        let mut value = request("mcp-20261128-100000-aaaaaaaa", &text);
        assert!(parse_request(&value, "mcp-20261128-100000-aaaaaaaa").unwrap_err().contains("video"));
        value["source_path"] = json!(base.join("x.mp4").to_string_lossy());
        value["options"] = Value::Null;
        assert!(parse_request(&value, "mcp-20261128-100000-aaaaaaaa").unwrap_err().contains("missing"));
        assert!(!valid_id("mcp-short") && !valid_id("mcp-20261128-100000-AAAA") && valid_id("mcp-20261128-100000-aaaaaaaa"));
        assert!(take_from(&base.join("nowhere")).is_empty());
        let _ = fs::remove_dir_all(base);
    }
}
