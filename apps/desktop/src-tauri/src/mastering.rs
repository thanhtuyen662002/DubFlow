//! Tối ưu hậu kỳ tự động (Phase 5 MVP): the level a render asks for (ADR-0052;
//! Lồng tiếng's mastering.json is ADR-0055), and the review panel of a mastered
//! Bán hàng video (ADR-0052 + ADR-0054, the editorial layer).
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
/// The panel's decisions; "later" (REVIEW_LATER), "missing" (no suitable footage), "focus" (remember the place) and
/// "include" (let excluded footage back in, #805).
const ACTIONS: [&str; 9] = ["approve", "unapprove", "pin", "exclude", "include", "reset", "later", "missing", "focus"];
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

pub(crate) fn digits(value: &str, most: usize) -> bool {
    (1..=most).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_digit())
}

/// `<n>` or `<n>f<n>`: a shot number, or a part of a long shot split by its extra frames. Up to six digits: the
/// shots inside a trim of a long source are numbered from 100001 by their place in the whole source (#805).
pub(crate) fn shot_number(value: &str) -> bool {
    match value.split_once('f') {
        Some((number, part)) => digits(number, 6) && digits(part, 1),
        None => digits(value, 6),
    }
}

/// A shot id as the planner writes it: `v<n>c<n>`, or `v<n>c<n>f<n>` for a part of a long shot (a clip or photo
/// of a product run).
fn is_shot(value: &str) -> bool {
    let Some(rest) = value.strip_prefix('v') else { return false };
    let Some((source, number)) = rest.split_once('c') else { return false };
    digits(source, 2) && shot_number(number)
}

pub(crate) fn is_warning(value: &str) -> bool {
    value.len() == 16 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// A keyframe file name as the analysis writes it: `<16 hex>-c<n>.jpg` or `<16 hex>-still.jpg`.
fn is_keyframe(value: &str) -> bool {
    let Some(stem) = value.strip_suffix(".jpg") else { return false };
    let Some((tag, part)) = stem.split_once('-') else { return false };
    is_warning(tag) && (part == "still" || part.strip_prefix('c').is_some_and(shot_number))
}

fn video_number(video: u32) -> Result<String, String> {
    if (1..=99).contains(&video) { Ok(video.to_string()) } else { Err(fail("Số video không hợp lệ")) }
}

/// The arguments after `--work … --video N` for one panel action, every value checked.
fn action_args(action: &str, unit: Option<&str>, what: Option<&str>, ids: &[String], warning: Option<&str>, value: Option<bool>) -> Result<Vec<String>, String> {
    checked_args(action, unit, what, ids, warning, value, is_unit, is_shot)
}

/// One panel action's arguments, with the unit and shot id shapes of the workflow (Bán hàng or Tổng hợp).
#[allow(clippy::too_many_arguments)]
pub(crate) fn checked_args(action: &str, unit: Option<&str>, what: Option<&str>, ids: &[String], warning: Option<&str>,
                           value: Option<bool>, is_unit: fn(&str) -> bool, is_shot: fn(&str) -> bool) -> Result<Vec<String>, String> {
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

/// `python -m engine.dubflow.mastering.review --work <run>\.dubflow-work <scope> <args>`; `scope` is
/// `["--video", n]` (Bán hàng) or `["--part", id]` (Tổng hợp), already checked.
pub(crate) fn run_review(run: &Path, scope: [&str; 2], args: &[String]) -> Result<Value, String> {
    let root = version_root()?;
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.mastering.review", "--work"]).arg(run.join(".dubflow-work"))
        .args(scope).args(args)
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
    // the engine reads and replaces review-<n>.json in the work folder: never through a link
    if fs::symlink_metadata(run.join(".dubflow-work").join(format!("review-{video}.json")))
        .is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
        return Err(fail("Tệp duyệt cảnh của video này không an toàn (là liên kết)"));
    }
    let args = action_args(&action, unit.as_deref(), what.as_deref(), ids.as_deref().unwrap_or_default(), warning.as_deref(), value)?;
    tauri::async_runtime::spawn_blocking(move || run_review(&run, ["--video", &video], &args))
        .await.map_err(|error| fail(error.to_string()))?
}

/// The Final QC of a run (`python -m engine.dubflow.mastering.final_qc`): `scope` is `["--video", n]` (Bán hàng) or
/// `["--program"]` (Tổng hợp). It measures the output with the packaged FFmpeg; a render alone never passes it.
pub(crate) fn run_final_qc(run: &Path, scope: &[&str]) -> Result<Value, String> {
    let root = version_root()?;
    let media = root.join("runtime").join("media");
    let mut command = Command::new(root.join("runtime").join("python.exe"));
    command.current_dir(root.join("app"))
        .args(["-B", "-m", "engine.dubflow.mastering.final_qc", "--work"]).arg(run.join(".dubflow-work"))
        .args(scope).arg("--ffmpeg").arg(media.join("ffmpeg.exe")).arg("--ffprobe").arg(media.join("ffprobe.exe"))
        .env("PYTHONUTF8", "1").env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(crate::CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được kiểm tra cuối: {error}")))?;
    serde_json::from_slice::<Value>(&output.stdout).ok().filter(|value| value["kind"] == "final_qc")
        .ok_or_else(|| fail("Kiểm tra cuối không trả về kết quả"))
}

/// The Final QC of video `video` of a Bán hàng run, before it is opened for export.
#[tauri::command]
pub async fn mastering_final_qc(output_dir: String, video: u32) -> Result<Value, String> {
    let run = crate::product::run_dir(&output_dir)?;
    let video = video_number(video)?;
    tauri::async_runtime::spawn_blocking(move || run_final_qc(&run, &["--video", &video]))
        .await.map_err(|error| fail(error.to_string()))?
}

fn keyframe_path(run: &Path, name: &str) -> Result<PathBuf, String> {
    if !is_keyframe(name) {
        return Err(fail("Ảnh cảnh không hợp lệ"));
    }
    Ok(run.join(".dubflow-work").join("mastering").join(name))
}

/// Opens a file without following a link at its last step (the folders above it are checked plain by the caller).
pub(crate) fn open_no_follow(path: &Path) -> std::io::Result<fs::File> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0o400_000); // O_NOFOLLOW
    }
    #[cfg(target_os = "macos")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x0100); // O_NOFOLLOW
    }
    options.open(path)
}

/// The keyframe's bytes: only a plain file in the run's own plain mastering folder, checked on the opened handle
/// (a link put there between the check and the read is not followed).
pub(crate) fn read_keyframe(run: &Path, name: &str) -> Result<Vec<u8>, String> {
    use std::io::Read;
    let path = keyframe_path(run, name)?;
    let missing = || fail("Không có ảnh của cảnh này");
    if !crate::product::plain_dir(path.parent().expect("mastering folder")) {
        return Err(missing());
    }
    let before = fs::symlink_metadata(&path).map_err(|_| missing())?;
    if crate::edit::is_link(&before) || !before.is_file() || before.len() > KEYFRAME_LIMIT {
        return Err(missing());
    }
    let mut file = open_no_follow(&path).map_err(|_| missing())?;
    let opened = file.metadata().map_err(|_| missing())?;
    if crate::edit::is_link(&opened) || !opened.is_file() || opened.len() > KEYFRAME_LIMIT {
        return Err(missing());
    }
    let mut bytes = Vec::with_capacity(opened.len() as usize);
    file.by_ref().take(KEYFRAME_LIMIT + 1).read_to_end(&mut bytes).map_err(|_| fail("Không đọc được ảnh của cảnh"))?;
    if bytes.len() as u64 > KEYFRAME_LIMIT {
        return Err(missing());
    }
    Ok(bytes)
}

/// A shot's keyframe (JPEG) for the review panel.
#[tauri::command]
pub fn mastering_keyframe(output_dir: String, name: String) -> Result<tauri::ipc::Response, String> {
    let run = crate::product::run_dir(&output_dir)?;
    Ok(tauri::ipc::Response::new(read_keyframe(&run, &name)?))
}

/// Lồng tiếng reads its level from this file beside the outputs (ADR-0055); absent is off.
pub(crate) const DUBBING_FILE: &str = "mastering.json";
const QC_LIMIT: u64 = 4 * 1024 * 1024;

/// A level writes `mastering.json` in a Lồng tiếng output folder; off (or no level) removes it, so off renders as before.
/// Mạnh (`dynamic`) also asks for the climax suggestion its description promises: only a suggestion for the Hậu kỳ
/// card (Giữ / Bỏ), never put into the video (ADR-0055).
pub(crate) fn write_dubbing_options(output: &Path, level: Option<&str>) -> Result<(), String> {
    let path = output.join(DUBBING_FILE);
    match options(level)? {
        Some(mut value) => {
            if value["level"] == "dynamic" {
                value["enable_climax_hook"] = Value::Bool(true);
            }
            let temp = output.join(".mastering.json.tmp");
            fs::write(&temp, serde_json::to_vec(&value).map_err(|error| fail(error.to_string()))?)
                .and_then(|()| fs::rename(&temp, &path))
                .map_err(|error| fail(format!("Không ghi được mức tối ưu hậu kỳ: {error}")))
        }
        None => match fs::remove_file(&path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(fail(format!("Không bỏ được mức tối ưu hậu kỳ cũ: {error}"))),
        },
    }
}

fn climax_file(dir: &Path) -> PathBuf {
    dir.join(".dubflow-work").join("mastering").join("climax-decision.json")
}

/// A small JSON file read only when it is a plain file (never a link), checked again on the opened handle.
/// Absent is `None`; a link, a folder or an oversized file is an error.
pub(crate) fn read_plain_json(path: &Path, limit: u64) -> Result<Option<Value>, String> {
    use std::io::Read;
    let bad = || fail(format!("{} không hợp lệ", path.display()));
    let before = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(fail(format!("{}: {error}", path.display()))),
    };
    if crate::edit::is_link(&before) || !before.is_file() || before.len() > limit {
        return Err(bad());
    }
    let mut file = open_no_follow(path).map_err(|_| bad())?;
    let opened = file.metadata().map_err(|_| bad())?;
    if crate::edit::is_link(&opened) || !opened.is_file() || opened.len() > limit {
        return Err(bad());
    }
    let mut text = String::new();
    file.by_ref().take(limit + 1).read_to_string(&mut text).map_err(|_| bad())?;
    if text.len() as u64 > limit {
        return Err(bad());
    }
    serde_json::from_str(&text).map(Some).map_err(|error| fail(format!("{}: {error}", path.display())))
}

/// What the Hậu kỳ card shows of a finished Lồng tiếng video: the export's own QC, nothing recomputed. Null when
/// the video was made with mastering off (or before it existed). Only plain folders and files are read.
fn qc_view(dir: &Path) -> Result<Value, String> {
    if !crate::product::plain_dir(dir) {
        return Err(fail("Thư mục video không hợp lệ"));
    }
    let Some(qc) = read_plain_json(&dir.join("qc_report.json"), QC_LIMIT)? else { return Ok(Value::Null) };
    let Some(mastering) = qc.get("mastering").filter(|value| value.is_object()) else { return Ok(Value::Null) };
    let fallback = qc["warnings"].as_array().is_some_and(|warnings| warnings.iter()
        .any(|warning| warning.as_str().is_some_and(|text| text.starts_with("B2_AUDIO_FALLBACK_TO_B1"))));
    let decision_file = climax_file(dir);
    let plain = |path: &Path| crate::product::plain_dir(path);
    let decision = (plain(&dir.join(".dubflow-work")) && plain(decision_file.parent().expect("mastering folder")))
        .then(|| read_plain_json(&decision_file, 64 * 1024).ok().flatten()).flatten()
        .and_then(|value| value["climax"].as_str().filter(|choice| ["keep", "drop"].contains(choice)).map(str::to_owned));
    Ok(json!({
        "level": mastering["level"], "notes": mastering["notes"], "loudness": mastering["loudness"],
        "video_filters": mastering["video_filters"], "measured": mastering["measured"],
        "output_loudness": mastering["output_loudness"], "climax": mastering["climax"],
        "climax_decision": decision, "fallback": fallback,
    }))
}

/// The Hậu kỳ card of a finished Lồng tiếng video (`output_path` is its final video).
#[tauri::command]
pub fn dubbing_qc(output_path: String) -> Result<Value, String> {
    qc_view(&crate::review_dir(&output_path)?)
}

/// Records Giữ / Bỏ in the video's own plain `.dubflow-work\mastering` folder: nothing is created or written
/// through a link, and an existing link at the record or its temp file is refused or replaced, never followed.
fn write_climax_decision(dir: &Path, record: &Value) -> Result<(), String> {
    use std::io::Write;
    let bad = || fail("Thư mục làm việc của video không hợp lệ");
    let work = dir.join(".dubflow-work");
    if !crate::product::plain_dir(dir) || !crate::product::plain_dir(&work) {
        return Err(bad());
    }
    let folder = work.join("mastering");
    match fs::create_dir(&folder) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(fail(error.to_string())),
    }
    if !crate::product::plain_dir(&folder) {
        return Err(bad());
    }
    let path = climax_file(dir);
    if let Ok(metadata) = fs::symlink_metadata(&path) {
        if crate::edit::is_link(&metadata) || !metadata.is_file() {
            return Err(bad());
        }
    }
    let temp = path.with_extension("json.tmp");
    match fs::remove_file(&temp) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(fail(error.to_string())),
    }
    let bytes = serde_json::to_vec(record).map_err(|error| fail(error.to_string()))?;
    fs::OpenOptions::new().write(true).create_new(true).open(&temp)
        .and_then(|mut file| file.write_all(&bytes).and_then(|()| file.sync_all()))
        .and_then(|()| fs::rename(&temp, &path))
        .map_err(|error| {
            let _ = fs::remove_file(&temp);
            fail(format!("Không lưu được lựa chọn: {error}"))
        })
}

/// Giữ / Bỏ on the climax suggestion: only recorded (the suggestion is never put into the video by itself).
#[tauri::command]
pub fn dubbing_qc_climax(output_path: String, keep: bool) -> Result<Value, String> {
    let dir = crate::review_dir(&output_path)?;
    let view = qc_view(&dir)?;
    let climax = &view["climax"];
    if !climax.is_object() {
        return Err(fail("Video này không có gợi ý cao trào"));
    }
    let record = json!({"schema_version": 1, "climax": if keep { "keep" } else { "drop" },
                        "start_ms": climax["start_ms"], "end_ms": climax["end_ms"]});
    write_climax_decision(&dir, &record)?;
    qc_view(&dir)
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
        assert!(is_shot("v1c3f2") && !is_shot("v1c3f") && !is_shot("v1c3f12") && !is_shot("v1cf2") && !is_shot("v1c3f2f1"));
        assert!(is_shot("v1c100509") && is_shot("v1c100509f2") && !is_shot("v1c1005091"));
        assert!(is_keyframe("89fe52a42e57f33f-c100509.jpg") && is_keyframe("89fe52a42e57f33f-c100509f1.jpg"));
        assert!(is_keyframe("89fe52a42e57f33f-c12.jpg") && is_keyframe("89fe52a42e57f33f-still.jpg"));
        assert!(is_keyframe("89fe52a42e57f33f-c12f3.jpg") && !is_keyframe("89fe52a42e57f33f-c12f.jpg"));
        assert!(!is_keyframe("../89fe52a42e57f33f-c1.jpg") && !is_keyframe("89fe52a42e57f33f-c1.png") && !is_keyframe("x-c1.jpg"));
        assert!(is_warning("0123456789abcdef") && !is_warning("0123456789ABCDEF") && !is_warning("0123"));
    }

    #[test]
    fn panel_actions_are_checked_before_python_runs() {
        assert_eq!(action_args("view", None, None, &[], None, None).unwrap(), vec!["view"]);
        assert_eq!(action_args("decide", Some("s2"), Some("pin"), &["v1c3".into()], None, None).unwrap(), vec!["decide", "s2", "pin", "v1c3"]);
        assert!(action_args("decide", Some("s2"), Some("filter"), &[], None, None).is_err());
        assert_eq!(action_args("decide", Some("s2"), Some("include"), &["v1c3".into()], None, None).unwrap(),
                   vec!["decide", "s2", "include", "v1c3"]);
        for what in ["later", "missing", "focus"] {
            assert_eq!(action_args("decide", Some("s2"), Some(what), &[], None, None).unwrap(), vec!["decide", "s2", what]);
        }
        assert!(action_args("decide", Some("s2"), Some("pin"), &["--work".into()], None, None).is_err());
        assert!(action_args("decide", None, Some("pin"), &[], None, None).is_err());
        assert_eq!(action_args("accept", None, None, &[], None, Some(true)).unwrap(), vec!["accept", "true"]);
        assert!(action_args("ack", None, None, &[], Some("nope"), None).is_err());
        assert!(action_args("shell", None, None, &[], None, None).is_err());
    }

    #[test]
    fn a_keyframe_that_is_a_link_or_sits_in_a_linked_folder_is_not_read() {
        let base = std::env::temp_dir().join(format!("dubflow-keyframe-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let run = base.join("DubFlow Output").join("Ban hang - A").join("run-1");
        let mastering = run.join(".dubflow-work").join("mastering");
        fs::create_dir_all(&mastering).unwrap();
        let name = "89fe52a42e57f33f-c1.jpg";
        fs::write(mastering.join(name), b"jpeg").unwrap();
        assert_eq!(read_keyframe(&run, name).unwrap(), b"jpeg");
        let outside = base.join("secret");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join(name), b"outside marker").unwrap();
        // a linked mastering folder
        let other = base.join("DubFlow Output").join("Ban hang - A").join("run-2");
        fs::create_dir_all(other.join(".dubflow-work")).unwrap();
        if crate::product::tests::link_dir(&outside, &other.join(".dubflow-work").join("mastering")) {
            assert!(read_keyframe(&other, name).is_err());
        } else {
            assert!(cfg!(not(windows)), "junctions need no privilege on Windows");
        }
        // a keyframe that is itself a link (a symlink where allowed; a hard link is a plain file and stays readable)
        #[cfg(unix)]
        {
            fs::remove_file(mastering.join(name)).unwrap();
            std::os::unix::fs::symlink(outside.join(name), mastering.join(name)).unwrap();
            assert!(read_keyframe(&run, name).is_err());
        }
        assert_eq!(fs::read(outside.join(name)).unwrap(), b"outside marker");
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn keyframes_come_only_from_the_runs_mastering_folder() {
        let run = Path::new("C:/Videos/DubFlow Output/Ban hang - A/run-1");
        assert_eq!(keyframe_path(run, "89fe52a42e57f33f-c1.jpg").unwrap(),
                   run.join(".dubflow-work").join("mastering").join("89fe52a42e57f33f-c1.jpg"));
        assert!(keyframe_path(run, "..\\..\\secret.jpg").is_err());
    }

    #[test]
    fn dubbing_level_file_is_written_for_a_level_and_removed_for_off() {
        let dir = std::env::temp_dir().join(format!("dubflow-mastering-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        write_dubbing_options(&dir, Some("light")).unwrap();
        let written: Value = serde_json::from_slice(&fs::read(dir.join(DUBBING_FILE)).unwrap()).unwrap();
        assert_eq!(written, json!({"schema_version": 1, "level": "light"}));
        write_dubbing_options(&dir, Some("dynamic")).unwrap();
        let written: Value = serde_json::from_slice(&fs::read(dir.join(DUBBING_FILE)).unwrap()).unwrap();
        assert_eq!(written, json!({"schema_version": 1, "level": "dynamic", "enable_climax_hook": true}), "Mạnh suggests a climax");
        write_dubbing_options(&dir, Some("off")).unwrap();
        assert!(!dir.join(DUBBING_FILE).exists());
        write_dubbing_options(&dir, None).unwrap();
        assert!(write_dubbing_options(&dir, Some("../x")).is_err());
        assert!(!dir.join(DUBBING_FILE).exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn qc_card_reads_the_exports_mastering_block_and_records_the_climax_choice() {
        let dir = std::env::temp_dir().join(format!("dubflow-qc-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(qc_view(&dir).unwrap(), Value::Null);
        fs::write(dir.join("qc_report.json"), r#"{"warnings":[]}"#).unwrap();
        assert_eq!(qc_view(&dir).unwrap(), Value::Null, "off: no card");
        fs::write(dir.join("qc_report.json"), serde_json::to_vec(&json!({
            "warnings": ["B2_AUDIO_FALLBACK_TO_B1: x"],
            "mastering": {"level": "dynamic", "notes": ["Âm lượng: …"], "loudness": false, "video_filters": [],
                          "measured": {}, "climax": {"start_ms": 1000, "end_ms": 6000}}})).unwrap()).unwrap();
        let view = qc_view(&dir).unwrap();
        assert_eq!(view["level"], "dynamic");
        assert_eq!(view["fallback"], true);
        assert_eq!(view["climax_decision"], Value::Null);
        fs::create_dir_all(climax_file(&dir).parent().unwrap()).unwrap();
        fs::write(climax_file(&dir), r#"{"climax":"keep"}"#).unwrap();
        assert_eq!(qc_view(&dir).unwrap()["climax_decision"], "keep");
        fs::write(climax_file(&dir), r#"{"climax":"apply-now"}"#).unwrap();
        assert_eq!(qc_view(&dir).unwrap()["climax_decision"], Value::Null);
        write_climax_decision(&dir, &json!({"climax": "drop"})).unwrap();
        assert_eq!(qc_view(&dir).unwrap()["climax_decision"], "drop");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_qc_card_neither_reads_nor_writes_through_a_link() {
        let base = std::env::temp_dir().join(format!("dubflow-qc-link-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let outside = base.join("outside");
        fs::create_dir_all(&outside).unwrap();
        let qc = serde_json::to_vec(&json!({"mastering": {"level": "light", "climax": {"start_ms": 0, "end_ms": 5000}}})).unwrap();
        fs::write(outside.join("qc_report.json"), &qc).unwrap();
        // a linked mastering folder: the record is refused and nothing lands outside
        let dir = base.join("video");
        fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
        fs::write(dir.join("qc_report.json"), &qc).unwrap();
        if crate::product::tests::link_dir(&outside, &dir.join(".dubflow-work").join("mastering")) {
            assert!(write_climax_decision(&dir, &json!({"climax": "keep"})).is_err());
            assert!(!outside.join("climax-decision.json").exists());
            fs::write(outside.join("climax-decision.json"), r#"{"climax":"keep"}"#).unwrap();
            assert_eq!(qc_view(&dir).unwrap()["climax_decision"], Value::Null, "a decision behind a link is not read");
        } else {
            assert!(cfg!(not(windows)), "junctions need no privilege on Windows");
        }
        // a linked output folder is not read at all
        let linked = base.join("linked");
        if crate::product::tests::link_dir(&outside, &linked) {
            assert!(qc_view(&linked).is_err());
        }
        // a qc report that is itself a link is not read
        #[cfg(unix)]
        {
            let plain = base.join("plain");
            fs::create_dir_all(&plain).unwrap();
            std::os::unix::fs::symlink(outside.join("qc_report.json"), plain.join("qc_report.json")).unwrap();
            assert!(qc_view(&plain).is_err());
        }
        let _ = fs::remove_dir_all(&base);
    }
}
