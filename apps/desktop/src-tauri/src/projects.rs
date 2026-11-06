//! Deleting a project the user made on one of the project pages: a
//! documentary (`Phim tai lieu - `), a compilation (`Tong hop - `) or a
//! product video run (`Ban hang - `). Each is one run folder
//! `<…>\DubFlow Output\<prefix><title>\run-<12 hex>` holding everything the
//! project made, and nothing else.
//!
//! Only such a folder is touched: never a link, never one whose job is
//! running, never one with a file in use. On Windows it goes to the Recycle
//! Bin, so a mistake can be undone; the title folder above it is removed once
//! it is empty.
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use crate::{display_path, fail};

/// A status written this recently and still RUNNING means the job is alive;
/// an older one was left by a closed or crashed app.
const ALIVE: Duration = Duration::from_secs(10 * 60);

fn prefix(kind: &str) -> Result<&'static str, String> {
    match kind {
        "docu" => Ok("Phim tai lieu - "),
        "compilation" => Ok("Tong hop - "),
        "product" => Ok("Ban hang - "),
        _ => Err(fail("Loại dự án không hợp lệ")),
    }
}

/// Status files of the jobs that may run inside a run folder.
const STATUS_FILES: [&str; 4] = [".dubflow-work/status.json", ".dubflow-work/narration/status.json",
    ".dubflow-work/visual/status.json", ".dubflow-work/render/status.json"];

fn is_link(path: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else { return true };
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 { return true; }
    }
    metadata.file_type().is_symlink()
}

/// The run folder `value` names, when it is a plain project folder of `kind`.
fn run_dir(kind: &str, value: &str) -> Result<PathBuf, String> {
    const INVALID: &str = "Thư mục dự án không hợp lệ";
    let prefix = prefix(kind)?;
    let path = Path::new(value);
    if !path.is_absolute() || path.components().any(|part| matches!(part, std::path::Component::ParentDir)) {
        return Err(fail(INVALID));
    }
    if !path.exists() { return Err(fail("Không tìm thấy dự án; có thể nó đã được xóa")); }
    // The folder itself and its title folder must be real folders, not links.
    if is_link(path) || path.parent().is_none_or(is_link) { return Err(fail(INVALID)); }
    let dir = path.canonicalize().map_err(|_| fail(INVALID))?;
    let run = dir.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    let title = dir.parent().and_then(Path::file_name).and_then(|name| name.to_str()).unwrap_or_default();
    let output = dir.parent().and_then(Path::parent).and_then(Path::file_name);
    let shape = run.len() == 16 && run.starts_with("run-") && run[4..].bytes().all(|byte| byte.is_ascii_hexdigit());
    if !shape || !title.starts_with(prefix) || output.is_none_or(|name| name != "DubFlow Output")
        || !dir.join(".dubflow-work").join("spec.json").is_file() {
        return Err(fail(INVALID));
    }
    Ok(dir)
}

/// What is still running inside the run folder, in Vietnamese.
fn running(dir: &Path, now: SystemTime) -> Option<&'static str> {
    STATUS_FILES.iter().find_map(|name| {
        let path = dir.join(name);
        let status = crate::product::read_small_json(&path, 64 * 1024)?;
        let fresh = fs::metadata(&path).and_then(|metadata| metadata.modified()).ok()
            .is_some_and(|modified| now.duration_since(modified).unwrap_or_default() < ALIVE);
        (status["state"] == "RUNNING" && fresh).then_some(match *name {
            ".dubflow-work/narration/status.json" => "Dự án đang tạo lời đọc",
            ".dubflow-work/visual/status.json" => "Dự án đang lập kế hoạch hình ảnh",
            ".dubflow-work/render/status.json" => "Dự án đang dựng bản nháp",
            _ => "Dự án đang tạo video",
        })
    })
}

/// Fails when a file inside is open (Windows refuses to rename the folder then).
fn not_in_use(dir: &Path) -> Result<(), String> {
    let name = dir.file_name().and_then(|name| name.to_str()).unwrap_or("run");
    let probe = dir.with_file_name(format!(".dubflow-deleting-{name}"));
    fs::rename(dir, &probe).map_err(|_| fail("Có tệp trong dự án đang được mở (ví dụ video đang phát hoặc thư mục đang mở). Hãy đóng rồi thử lại."))?;
    fs::rename(&probe, dir).map_err(|_| fail(format!("Không trả lại được tên thư mục dự án; nó đang tên {}", display_path(&probe))))
}

#[cfg(windows)]
fn remove(dir: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::UI::Shell::{SHFileOperationW, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_NOERRORUI, FOF_SILENT, FO_DELETE, SHFILEOPSTRUCTW};
    // The shell wants a plain path (no `\\?\`), ended by two NULs.
    let mut from: Vec<u16> = std::ffi::OsStr::new(&display_path(dir)).encode_wide().collect();
    from.extend([0, 0]);
    let mut operation: SHFILEOPSTRUCTW = unsafe { std::mem::zeroed() };
    operation.wFunc = FO_DELETE as _;
    operation.pFrom = from.as_ptr();
    operation.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT) as _;
    // SAFETY: `operation` is fully initialised and `from` outlives the call.
    let result = unsafe { SHFileOperationW(&mut operation) };
    if result != 0 || operation.fAnyOperationsAborted != 0 || dir.exists() {
        return Err(fail("Không chuyển được dự án vào Thùng rác. Hãy đóng các tệp của dự án rồi thử lại."));
    }
    Ok(())
}

#[cfg(not(windows))]
fn remove(dir: &Path) -> Result<(), String> {
    fs::remove_dir_all(dir).map_err(|_| fail("Không xóa được dự án"))
}

fn delete_run(kind: &str, value: &str, now: SystemTime, forget: &dyn Fn(&Path)) -> Result<(), String> {
    let dir = run_dir(kind, value)?;
    if let Some(busy) = running(&dir, now) {
        return Err(fail(format!("{busy}. Hãy hủy hoặc đợi xong rồi xóa.")));
    }
    not_in_use(&dir)?;
    forget(&dir);
    remove(&dir)?;
    // The title folder goes too once nothing else is in it (fails otherwise).
    if let Some(title) = dir.parent() { let _ = fs::remove_dir(title); }
    Ok(())
}

/// Delete one project's run folder (to the Recycle Bin on Windows).
#[tauri::command]
pub async fn project_delete(kind: String, dir: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || delete_run(&kind, &dir, SystemTime::now(), &crate::edit::forget_run))
        .await.map_err(|_| fail("Không xóa được dự án"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("dubflow-project-delete-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        base
    }

    fn make_run(base: &Path, title: &str) -> PathBuf {
        let run = base.join("DubFlow Output").join(title).join("run-0123456789ab");
        fs::create_dir_all(run.join(".dubflow-work")).unwrap();
        fs::write(run.join(".dubflow-work/spec.json"), b"{}").unwrap();
        fs::write(run.join("documentary.mp4"), b"x").unwrap();
        run
    }

    #[test]
    fn deletes_a_run_and_its_empty_title_folder() {
        let base = base("ok");
        let run = make_run(&base, "Phim tai lieu - Test");
        let forgotten = std::cell::RefCell::new(None);
        delete_run("docu", &run.to_string_lossy(), SystemTime::now(), &|dir| { forgotten.replace(Some(dir.to_path_buf())); }).unwrap();
        assert!(!run.exists());
        assert!(!run.parent().unwrap().exists());
        assert!(base.join("DubFlow Output").is_dir());
        assert!(forgotten.borrow().is_some());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn keeps_the_title_folder_with_other_runs() {
        let base = base("sibling");
        let run = make_run(&base, "Tong hop - A");
        let other = run.with_file_name("run-ba9876543210");
        fs::create_dir_all(&other).unwrap();
        delete_run("compilation", &run.to_string_lossy(), SystemTime::now(), &|_| {}).unwrap();
        assert!(!run.exists());
        assert!(other.is_dir());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn refuses_other_folders() {
        let base = base("refuse");
        let run = make_run(&base, "Phim tai lieu - Test");
        // Wrong kind, the title folder, the output folder, a relative path, `..`.
        for (kind, path) in [("product", run.clone()), ("docu", run.parent().unwrap().to_path_buf()), ("docu", base.join("DubFlow Output")),
            ("docu", PathBuf::from("run-0123456789ab")), ("docu", run.join("..").join("run-0123456789ab")), ("other", run.clone())] {
            assert!(delete_run(kind, &path.to_string_lossy(), SystemTime::now(), &|_| {}).is_err(), "{kind} {}", path.display());
        }
        fs::remove_file(run.join(".dubflow-work/spec.json")).unwrap();
        assert!(delete_run("docu", &run.to_string_lossy(), SystemTime::now(), &|_| {}).is_err());
        assert!(run.is_dir());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn refuses_a_running_project_until_its_status_is_stale() {
        let base = base("running");
        let run = make_run(&base, "Phim tai lieu - Test");
        fs::create_dir_all(run.join(".dubflow-work/render")).unwrap();
        fs::write(run.join(".dubflow-work/render/status.json"), br#"{"state":"RUNNING"}"#).unwrap();
        let error = delete_run("docu", &run.to_string_lossy(), SystemTime::now(), &|_| {}).unwrap_err();
        assert!(error.contains("dựng bản nháp"), "{error}");
        assert!(run.is_dir());
        // Left RUNNING by an app that closed an hour ago: deletable.
        delete_run("docu", &run.to_string_lossy(), SystemTime::now() + Duration::from_secs(3600), &|_| {}).unwrap();
        assert!(!run.exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[cfg(unix)]
    #[test]
    fn refuses_a_linked_run() {
        let base = base("link");
        let run = make_run(&base, "Ban hang - Real");
        let title = base.join("DubFlow Output").join("Ban hang - Link");
        fs::create_dir_all(&title).unwrap();
        std::os::unix::fs::symlink(&run, title.join("run-aaaaaaaaaaaa")).unwrap();
        assert!(delete_run("product", &title.join("run-aaaaaaaaaaaa").to_string_lossy(), SystemTime::now(), &|_| {}).is_err());
        assert!(run.is_dir());
        let _ = fs::remove_dir_all(&base);
    }
}
