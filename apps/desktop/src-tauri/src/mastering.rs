//! Tối ưu hậu kỳ tự động (ADR-0052/0055, Phase 5 MVP): the level a render
//! asks for, and the review panel of a mastered product video.
//!
//! The panel never edits plan files itself: each action is one call of
//! `python -m engine.dubflow.mastering.review`, after the host has checked
//! every argument (a run folder below "DubFlow Output", ids of the shape the
//! planner writes). Keyframes are read only from the run's own mastering
//! folder, by a name of the shape the analysis writes.

use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{fail, version_root, AiTranslation};

pub(crate) const LEVELS: [&str; 4] = ["off", "light", "balanced", "dynamic"];
const ACTIONS: [&str; 5] = ["approve", "unapprove", "pin", "exclude", "reset"];
const MAX_IDS: usize = 20;
const KEYFRAME_LIMIT: u64 = 8 * 1024 * 1024;

/// The `mastering` options of a spec for a level, or None for off (nothing new in the spec).
pub(crate) fn options(level: Option<&str>) -> Result<Option<Value>, String> {
    match level.unwrap_or("off") {
        "off" => Ok(None),
        level if LEVELS.contains(&level) => Ok(Some(json!({"schema_version": 1, "level": level}))),
        _ => Err(fail("Mức tối ưu hậu kỳ không hợp lệ")),
    }
}

/// The AI route that may look at keyframes (never the key).
pub(crate) fn vision_route(ai: &AiTranslation) -> Result<Value, String> {
    if !crate::ai_keys::PROVIDERS.contains(&ai.provider.as_str()) {
        return Err(fail("Nhà cung cấp AI không hợp lệ"));
    }
    let short = |value: &Option<String>, limit: usize| value.as_deref().map(str::trim).filter(|text| !text.is_empty())
        .map(|text| text.chars().filter(|ch| !ch.is_control()).take(limit).collect::<String>());
    Ok(json!({"provider": ai.provider, "model": short(&ai.model, 200), "base_url": short(&ai.base_url, 300)}))
}

fn is_unit(value: &str) -> bool {
    let letters = value.bytes().take_while(u8::is_ascii_lowercase).count();
    (1..=3).contains(&letters) && (1..=4).contains(&(value.len() - letters)) && value.bytes().skip(letters).all(|b| b.is_ascii_digit())
}

/// A shot id as the planner writes it: `v<n>c<n>` (a clip or photo of a product run).
fn is_shot(value: &str) -> bool {
    let Some(rest) = value.strip_prefix('v') else { return false };
    let Some((source, number)) = rest.split_once('c') else { return false };
    (1..=2).contains(&source.len()) && source.bytes().all(|b| b.is_ascii_digit())
        && (1..=4).contains(&number.len()) && number.bytes().all(|b| b.is_ascii_digit())
}

fn is_warning(value: &str) -> bool {
    value.len() == 16 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// A keyframe file name as the analysis writes it: `<16 hex>-c<n>.jpg` or `<16 hex>-still.jpg`.
fn is_keyframe(value: &str) -> bool {
    let Some(stem) = value.strip_suffix(".jpg") else { return false };
    let Some((tag, part)) = stem.split_once('-') else { return false };
    is_warning(tag) && (part == "still" || part.strip_prefix('c').is_some_and(|n| (1..=4).contains(&n.len()) && n.bytes().all(|b| b.is_ascii_digit())))
}

fn video_number(video: u32) -> Result<String, String> {
    if (1..=99).contains(&video) { Ok(video.to_string()) } else { Err(fail("Số video không hợp lệ")) }
}

/// The arguments after `--work … --video N` for one panel action, every value checked.
fn action_args(action: &str, unit: Option<&str>, what: Option<&str>, ids: &[String], warning: Option<&str>, value: Option<bool>) -> Result<Vec<String>, String> {
    match action {
        "view" | "undo" => Ok(vec![action.to_owned()]),
        "decide" => {
            let unit = unit.filter(|unit| is_unit(unit)).ok_or_else(|| fail("Câu không hợp lệ"))?;
            let what = what.filter(|what| ACTIONS.contains(what)).ok_or_else(|| fail("Thao tác không hợp lệ"))?;
            if ids.len() > MAX_IDS || ids.iter().any(|id| !is_shot(id)) {
                return Err(fail("Cảnh không hợp lệ"));
            }
            let mut args = vec!["decide".to_owned(), unit.to_owned(), what.to_owned()];
            args.extend(ids.iter().cloned());
            Ok(args)
        }
        "ack" => {
            let warning = warning.filter(|warning| is_warning(warning)).ok_or_else(|| fail("Cảnh báo không hợp lệ"))?;
            Ok(vec!["ack".to_owned(), warning.to_owned()])
        }
        "accept" => Ok(vec!["accept".to_owned(), value.ok_or_else(|| fail("Thiếu lựa chọn"))?.to_string()]),
        _ => Err(fail("Thao tác không hợp lệ")),
    }
}

fn run_review(run: &Path, video: &str, args: &[String]) -> Result<Value, String> {
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.mastering.review", "--work"]).arg(run.join(".dubflow-work"))
        .args(["--video", video]).args(args)
        .env("PYTHONUTF8", "1").env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(crate::CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được bộ duyệt cảnh: {error}")))?;
    let value = serde_json::from_slice::<Value>(&output.stdout).ok().filter(Value::is_object)
        .ok_or_else(|| fail("Bộ duyệt cảnh không trả về dữ liệu"))?;
    match value.get("error").and_then(Value::as_str) {
        Some(message) => Err(fail(message.chars().take(300).collect::<String>())),
        None => Ok(value),
    }
}

/// One action of the review panel of video `video` of a product run; returns the panel's view after it.
#[tauri::command]
pub async fn mastering_review(output_dir: String, video: u32, action: String, unit: Option<String>, what: Option<String>,
                              ids: Option<Vec<String>>, warning: Option<String>, value: Option<bool>) -> Result<Value, String> {
    let run = crate::product::run_dir(&output_dir)?;
    let video = video_number(video)?;
    let args = action_args(&action, unit.as_deref(), what.as_deref(), ids.as_deref().unwrap_or_default(), warning.as_deref(), value)?;
    tauri::async_runtime::spawn_blocking(move || run_review(&run, &video, &args))
        .await.map_err(|error| fail(error.to_string()))?
}

fn keyframe_path(run: &Path, name: &str) -> Result<PathBuf, String> {
    if !is_keyframe(name) {
        return Err(fail("Ảnh cảnh không hợp lệ"));
    }
    Ok(run.join(".dubflow-work").join("mastering").join(name))
}

/// A shot's keyframe (JPEG) for the review panel.
#[tauri::command]
pub fn mastering_keyframe(output_dir: String, name: String) -> Result<tauri::ipc::Response, String> {
    let run = crate::product::run_dir(&output_dir)?;
    let path = keyframe_path(&run, &name)?;
    let metadata = fs::metadata(&path).map_err(|_| fail("Không có ảnh của cảnh này"))?;
    if !metadata.is_file() || metadata.len() > KEYFRAME_LIMIT {
        return Err(fail("Không có ảnh của cảnh này"));
    }
    Ok(tauri::ipc::Response::new(fs::read(&path).map_err(|_| fail("Không đọc được ảnh của cảnh"))?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn levels_off_adds_nothing_and_unknown_levels_are_refused() {
        assert_eq!(options(None).unwrap(), None);
        assert_eq!(options(Some("off")).unwrap(), None);
        assert_eq!(options(Some("balanced")).unwrap(), Some(json!({"schema_version": 1, "level": "balanced"})));
        assert!(options(Some("max")).is_err());
    }

    #[test]
    fn ids_and_names_have_the_planners_shapes_only() {
        assert!(is_unit("s1") && is_unit("u12") && !is_unit("s") && !is_unit("../s1") && !is_unit("S1"));
        assert!(is_shot("v1c3") && is_shot("v12c1234") && !is_shot("v1c") && !is_shot("v1c3;rm") && !is_shot("../v1c3"));
        assert!(is_keyframe("89fe52a42e57f33f-c12.jpg") && is_keyframe("89fe52a42e57f33f-still.jpg"));
        assert!(!is_keyframe("../89fe52a42e57f33f-c1.jpg") && !is_keyframe("89fe52a42e57f33f-c1.png") && !is_keyframe("x-c1.jpg"));
        assert!(is_warning("0123456789abcdef") && !is_warning("0123456789ABCDEF") && !is_warning("0123"));
    }

    #[test]
    fn panel_actions_are_checked_before_python_runs() {
        assert_eq!(action_args("view", None, None, &[], None, None).unwrap(), vec!["view"]);
        assert_eq!(action_args("decide", Some("s2"), Some("pin"), &["v1c3".into()], None, None).unwrap(), vec!["decide", "s2", "pin", "v1c3"]);
        assert!(action_args("decide", Some("s2"), Some("filter"), &[], None, None).is_err());
        assert!(action_args("decide", Some("s2"), Some("pin"), &["--work".into()], None, None).is_err());
        assert!(action_args("decide", None, Some("pin"), &[], None, None).is_err());
        assert_eq!(action_args("accept", None, None, &[], None, Some(true)).unwrap(), vec!["accept", "true"]);
        assert!(action_args("ack", None, None, &[], Some("nope"), None).is_err());
        assert!(action_args("shell", None, None, &[], None, None).is_err());
    }

    #[test]
    fn keyframes_come_only_from_the_runs_mastering_folder() {
        let run = Path::new("C:/Videos/DubFlow Output/Ban hang - A/run-1");
        assert_eq!(keyframe_path(run, "89fe52a42e57f33f-c1.jpg").unwrap(),
                   run.join(".dubflow-work").join("mastering").join("89fe52a42e57f33f-c1.jpg"));
        assert!(keyframe_path(run, "..\\..\\secret.jpg").is_err());
    }
}
