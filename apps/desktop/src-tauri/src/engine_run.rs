//! What the host keeps of an engine job besides its status file (#766, P2-N3).
//!
//! rc7 D1: the engine died when `status.json` was held open by another reader, and the host had thrown its stderr
//! away (`Stdio::null`), so nothing told why. Now the engine's stdout and stderr go to two files in the run's work
//! folder; when the engine has ended with the status still RUNNING, the host takes the job's result from the
//! engine's last stdout line (its final status) and otherwise marks the run CANCELLED or FAILED, pointing at the
//! stderr file. Every status write here retries a file held open, a bounded number of times.

use serde_json::{json, Value};
use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

pub(crate) const ENGINE_STDOUT: &str = "engine-stdout.log";
/// The engine's lock on its run (Phase 8, F3; `engine/dubflow/support/engine_lock.py`): held by the engine process
/// for its whole life and released by the OS however it ends.
pub(crate) const ENGINE_LOCK: &str = "engine.lock";
/// The runs whose engine this app started and still waits for: their watcher settles them, nobody else.
static WAITING: std::sync::LazyLock<std::sync::Mutex<std::collections::HashSet<std::path::PathBuf>>> =
    std::sync::LazyLock::new(Default::default);
pub(crate) const ENGINE_STDERR: &str = "engine-stderr.log";
const FINISHED: [&str; 3] = ["COMPLETED", "FAILED", "CANCELLED"];
const TAIL_BYTES: u64 = 1024 * 1024;
const REPLACE_ATTEMPTS: u32 = 12;

/// The child's stdout and stderr: new files in `work` (never through a link), or nothing when they cannot be made.
pub(crate) fn engine_output(work: &Path) -> (Stdio, Stdio) {
    let open = |name: &str| {
        let path = work.join(name);
        if fs::symlink_metadata(&path).is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
            return Stdio::null();
        }
        fs::File::create(path).map_or_else(|_| Stdio::null(), Stdio::from)
    };
    (open(ENGINE_STDOUT), open(ENGINE_STDERR))
}

fn busy(error: &std::io::Error) -> bool {
    error.kind() == std::io::ErrorKind::PermissionDenied || matches!(error.raw_os_error(), Some(5 | 32 | 33))
}

/// `fs::rename(temp, path)`, waiting out a reader that holds `path` open (Windows refuses the rename then): a
/// growing pause, at most `REPLACE_ATTEMPTS` tries (~8 s). On failure `temp` is removed and `path` is unchanged.
pub(crate) fn replace_retrying(temp: &Path, path: &Path) -> std::io::Result<()> {
    replace_with(temp, path, REPLACE_ATTEMPTS, |pause| std::thread::sleep(pause))
}

fn replace_with(temp: &Path, path: &Path, attempts: u32, mut sleep: impl FnMut(Duration)) -> std::io::Result<()> {
    let mut pause = Duration::from_millis(50);
    for attempt in 1..=attempts.max(1) {
        match fs::rename(temp, path) {
            Ok(()) => return Ok(()),
            Err(error) if attempt < attempts && busy(&error) => {
                sleep(pause);
                pause = (pause * 2).min(Duration::from_secs(1));
            }
            Err(error) => {
                let _ = fs::remove_file(temp);
                return Err(error);
            }
        }
    }
    unreachable!("the last attempt returns")
}

/// The engine's result: its last stdout line when it is a status object in a finished state.
fn last_result(path: &Path) -> Option<Value> {
    let mut file = fs::File::open(path).ok()?;
    let length = file.metadata().ok()?.len();
    file.seek(SeekFrom::Start(length.saturating_sub(TAIL_BYTES))).ok()?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    let line = text.lines().rev().find(|line| !line.trim().is_empty())?;
    serde_json::from_str::<Value>(line).ok()
        .filter(|value| value.is_object() && value["state"].as_str().is_some_and(|state| FINISHED.contains(&state)))
}

/// Whether an engine holds `work`'s lock: `Some(true)` working, `Some(false)` gone (the lock is free), `None` no
/// lock file (an engine that never started, or one from before the lock existed).
pub(crate) fn engine_alive(work: &Path) -> Option<bool> {
    let path = work.join(ENGINE_LOCK);
    if fs::symlink_metadata(&path).is_ok_and(|metadata| crate::edit::is_link(&metadata) || !metadata.is_file()) {
        return Some(true); // never probed through a link; treated as busy, so nothing is settled because of it
    }
    let file = match fs::OpenOptions::new().read(true).write(true).open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
        // held open by something that shares no writing (an antivirus or backup scan): not proof the engine is gone
        Err(_) => return Some(true),
    };
    match file.try_lock() {
        Ok(()) => Some(false), // dropping the file releases the probe's lock
        Err(std::fs::TryLockError::WouldBlock) => Some(true),
        Err(std::fs::TryLockError::Error(_)) => Some(true),
    }
}

/// How a run is named in WAITING: its canonical path, so every form the same folder is reached by matches (rc21:
/// a run tracked as `C:\…\.dubflow-work` was read back as `\\?\C:\…\.dubflow-work`, missed, and settled as
/// interrupted while its engine was still starting).
fn waiting_key(work: &Path) -> std::path::PathBuf {
    fs::canonicalize(work).unwrap_or_else(|_| work.to_path_buf())
}

/// This app starts the engine of `work` and will settle it when it exits.
pub(crate) fn track(work: &Path) {
    let key = waiting_key(work);
    if let Ok(mut waiting) = WAITING.lock() {
        waiting.insert(key);
    }
}

fn untrack(work: &Path) {
    let key = waiting_key(work);
    if let Ok(mut waiting) = WAITING.lock() {
        waiting.remove(&key);
    }
}

fn tracked(work: &Path) -> bool {
    let key = waiting_key(work);
    WAITING.lock().is_ok_and(|waiting| waiting.contains(&key))
}

/// The engine of `work` has exited (this app waited for it): settle and stop tracking it.
pub(crate) fn settle_tracked(work: &Path, exited_ok: bool, stopped: &str) {
    settle_after_exit(work, exited_ok, stopped);
    untrack(work);
}

/// F3: a run left RUNNING by an app (or machine) that stopped is settled when it is next read. Nothing is done while
/// this app waits for its engine, nor while any engine holds the run's lock (it is still working, also when started
/// by an earlier app); otherwise the engine is gone and the run becomes FAILED (or CANCELLED, or the engine's own
/// last word), never COMPLETED without a successful exit. Its files stay: a new run reuses every finished part.
pub(crate) fn settle_if_abandoned(work: &Path, stopped: &str) {
    if tracked(work) {
        return;
    }
    if crate::product::read_small_json(&work.join("status.json"), TAIL_BYTES).is_some_and(|value| value["state"] == "RUNNING") {
        settle_after_exit(work, false, stopped);
    }
}

/// After the engine has ended: a status still RUNNING (or unreadable) is settled. The engine's own final status
/// (its last stdout line) wins, except that COMPLETED counts only with a successful exit; otherwise a cancel request
/// makes it CANCELLED, anything else FAILED with `stopped` and where the engine's error output is.
pub(crate) fn settle_after_exit(work: &Path, exited_ok: bool, stopped: &str) {
    let status_path = work.join("status.json");
    let current = crate::product::read_small_json(&status_path, TAIL_BYTES);
    if current.as_ref().is_some_and(|value| value["state"] != "RUNNING") {
        return;
    }
    if engine_alive(work) == Some(true) {
        return; // another engine is still making this run (a second start found it busy and left)
    }
    let value = settled(work, current, exited_ok, stopped);
    let temp = status_path.with_extension("json.settle");
    if fs::write(&temp, serde_json::to_vec(&value).unwrap_or_default()).is_ok() {
        let _ = replace_retrying(&temp, &status_path);
    }
}

fn settled(work: &Path, current: Option<Value>, exited_ok: bool, stopped: &str) -> Value {
    if let Some(mut result) = last_result(&work.join(ENGINE_STDOUT)) {
        if result["state"] != "COMPLETED" || exited_ok {
            result["status_source"] = json!("engine_stdout");
            return result;
        }
    }
    let cancelled = work.join("cancel").exists();
    let has_errors = fs::metadata(work.join(ENGINE_STDERR)).is_ok_and(|metadata| metadata.len() > 0);
    let message = if cancelled {
        "Đã hủy.".to_string()
    } else if has_errors {
        format!("{stopped} Chi tiết lỗi: .dubflow-work\\{ENGINE_STDERR}")
    } else {
        stopped.to_string()
    };
    let mut value = current.filter(Value::is_object).unwrap_or_else(|| json!({"schema_version": 1, "done": 0, "total": 0}));
    value["state"] = json!(if cancelled { "CANCELLED" } else { "FAILED" });
    value["message"] = json!(message);
    value["status_source"] = json!("host");
    value
}

/// A test's stand-in for a working engine: `work`'s lock is held until the returned file is dropped.
#[cfg(test)]
pub(crate) fn engine_working(work: &Path) -> fs::File {
    fs::create_dir_all(work).unwrap();
    let held = fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(work.join(ENGINE_LOCK)).unwrap();
    held.lock().unwrap();
    held
}

#[cfg(test)]
mod tests {
    use super::*;

    fn work() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("dubflow-engine-run-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn status(work: &Path) -> Value {
        serde_json::from_slice(&fs::read(work.join("status.json")).unwrap()).unwrap()
    }

    // F3 (Phase 8): a run left RUNNING by a stopped app is settled on the next read, never while an engine works on it
    #[test]
    fn an_abandoned_run_is_settled_when_read_and_a_working_one_is_not() {
        let stopped = "Lần dựng trước bị gián đoạn.";
        let running = br#"{"schema_version":1,"state":"RUNNING","done":1,"total":3}"#;
        // no engine lock at all (the app and the engine stopped before it existed): FAILED, its counts kept
        let work = work();
        fs::write(work.join("status.json"), running).unwrap();
        settle_if_abandoned(&work, stopped);
        let value = status(&work);
        assert_eq!((value["state"].as_str(), value["message"].as_str(), value["done"].as_u64()), (Some("FAILED"), Some(stopped), Some(1)));
        // an engine holds the lock (it outlived the app that started it): left RUNNING; once it is gone: settled
        fs::write(work.join("status.json"), running).unwrap();
        let held = fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(work.join(ENGINE_LOCK)).unwrap();
        held.lock().unwrap();
        assert_eq!(engine_alive(&work), Some(true));
        settle_if_abandoned(&work, stopped);
        settle_after_exit(&work, false, stopped); // a second start that found the run busy does not settle it either
        assert_eq!(status(&work)["state"], "RUNNING");
        drop(held); // the engine died: the OS released its lock
        assert_eq!(engine_alive(&work), Some(false));
        settle_if_abandoned(&work, stopped);
        assert_eq!(status(&work)["state"], "FAILED");
        // a cancel asked before the stop is kept as CANCELLED; a finished run is never touched
        fs::write(work.join("status.json"), running).unwrap();
        fs::write(work.join("cancel"), b"").unwrap();
        settle_if_abandoned(&work, stopped);
        assert_eq!(status(&work)["state"], "CANCELLED");
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"COMPLETED","path":"x"}"#).unwrap();
        settle_if_abandoned(&work, stopped);
        assert_eq!(status(&work)["state"], "COMPLETED");
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn a_tracked_run_is_left_to_its_watcher_whatever_form_its_path_is_read_in() {
        // rc21: tracked in the form the run was made with, read back canonical (`\\?\C:\…` on Windows) while the
        // engine was still starting and had no lock yet: it was settled as interrupted under a working engine
        let work = work().join(".dubflow-work");
        fs::create_dir_all(&work).unwrap();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        track(&work);
        let canonical = fs::canonicalize(&work).unwrap();
        settle_if_abandoned(&canonical, "stopped");
        assert_eq!(status(&work)["state"], "RUNNING", "no engine.lock yet, and still not settled");
        // a stale, free lock left by the previous engine of a re-render is no proof either while this app waits
        fs::write(work.join(ENGINE_LOCK), b"").unwrap();
        settle_if_abandoned(&canonical, "stopped");
        assert_eq!(status(&work)["state"], "RUNNING");
        // its watcher settles it in either form, and then it is no longer tracked
        settle_tracked(&canonical, false, "Bộ tổng hợp đã dừng.");
        assert_eq!(status(&work)["state"], "FAILED");
        assert!(!tracked(&work));
        let _ = fs::remove_dir_all(work.parent().unwrap());
    }

    #[cfg(windows)]
    #[test]
    fn a_lock_file_held_without_write_sharing_is_not_an_engine_gone() {
        use std::os::windows::fs::OpenOptionsExt;
        let work = work();
        fs::write(work.join(ENGINE_LOCK), b"").unwrap();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        // a scanner's handle that shares reading only (FILE_SHARE_READ = 1): the host cannot open the file to probe
        let scanner = fs::OpenOptions::new().read(true).share_mode(1).open(work.join(ENGINE_LOCK)).unwrap();
        assert_eq!(engine_alive(&work), Some(true));
        settle_if_abandoned(&work, "stopped");
        assert_eq!(status(&work)["state"], "RUNNING");
        drop(scanner);
        assert_eq!(engine_alive(&work), Some(false));
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn a_run_this_app_still_waits_for_is_left_to_its_watcher() {
        let work = work();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        track(&work); // started, the engine has not taken its lock yet
        settle_if_abandoned(&work, "stopped");
        assert_eq!(status(&work)["state"], "RUNNING");
        settle_tracked(&work, false, "Bộ tổng hợp đã dừng.");
        assert_eq!(status(&work)["state"], "FAILED");
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        settle_if_abandoned(&work, "stopped"); // no longer tracked: settled
        assert_eq!(status(&work)["state"], "FAILED");
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn the_engines_last_line_settles_a_status_it_could_not_write() {
        let work = work();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING","done":2,"total":3}"#).unwrap();
        fs::write(work.join(ENGINE_STDOUT), "noise\n{\"schema_version\":1,\"state\":\"COMPLETED\",\"done\":3,\"total\":3,\"path\":\"x\"}\n").unwrap();
        settle_after_exit(&work, true, "Bộ tổng hợp đã dừng.");
        let value = status(&work);
        assert_eq!(value["state"], "COMPLETED");
        assert_eq!(value["status_source"], "engine_stdout");
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn completed_needs_a_successful_exit() {
        let work = work();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        fs::write(work.join(ENGINE_STDOUT), "{\"state\":\"COMPLETED\"}\n").unwrap();
        fs::write(work.join(ENGINE_STDERR), "Traceback ...\nPermissionError: [WinError 5]\n").unwrap();
        settle_after_exit(&work, false, "Bộ tổng hợp đã dừng. Hãy thử lại.");
        let value = status(&work);
        assert_eq!(value["state"], "FAILED");
        assert!(value["message"].as_str().unwrap().contains(ENGINE_STDERR));
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn a_cancel_is_never_lost() {
        let work = work();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        fs::write(work.join("cancel"), b"").unwrap();
        settle_after_exit(&work, false, "Bộ tạo video đã dừng.");
        assert_eq!(status(&work)["state"], "CANCELLED");
        // the engine's own CANCELLED is kept as it said it
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"RUNNING"}"#).unwrap();
        fs::write(work.join(ENGINE_STDOUT), "{\"state\":\"CANCELLED\",\"message\":\"Đã hủy. Giữ lại 1 video đã xong.\"}\n").unwrap();
        settle_after_exit(&work, false, "Bộ tạo video đã dừng.");
        assert_eq!(status(&work)["message"], "Đã hủy. Giữ lại 1 video đã xong.");
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn a_finished_status_is_left_alone_and_garbage_is_not_a_result() {
        let work = work();
        fs::write(work.join("status.json"), br#"{"schema_version":1,"state":"FAILED","message":"x"}"#).unwrap();
        fs::write(work.join(ENGINE_STDOUT), "{\"state\":\"COMPLETED\"}\n").unwrap();
        settle_after_exit(&work, true, "stopped");
        assert_eq!(status(&work)["state"], "FAILED");
        fs::write(work.join("status.json"), b"{broken").unwrap();
        fs::write(work.join(ENGINE_STDOUT), "{\"state\":\"WHATEVER\"}\n").unwrap();
        settle_after_exit(&work, true, "stopped");
        assert_eq!(status(&work)["state"], "FAILED");
        let _ = fs::remove_dir_all(work);
    }

    #[test]
    fn a_rename_refused_by_a_reader_is_retried_then_given_up() {
        let work = work();
        let temp = work.join("status.json.tmp");
        fs::write(&temp, b"{}").unwrap();
        // a destination that is a folder refuses the rename every time: bounded, then the temp file is removed
        fs::create_dir(work.join("status.json")).unwrap();
        let mut pauses = 0;
        assert!(replace_with(&temp, &work.join("status.json"), 5, |_| pauses += 1).is_err());
        assert!(pauses < 5);
        assert!(!temp.exists());
        let _ = fs::remove_dir_all(work);
    }

    #[cfg(windows)]
    #[test]
    fn a_status_held_open_without_delete_sharing_is_waited_out() {
        use std::os::windows::fs::OpenOptionsExt;
        let work = work();
        let path = work.join("status.json");
        fs::write(&path, br#"{"state":"RUNNING"}"#).unwrap();
        let temp = work.join("status.json.tmp");
        fs::write(&temp, br#"{"state":"FAILED"}"#).unwrap();
        // read sharing only (no FILE_SHARE_DELETE): what a reader such as an antivirus does; rename is refused
        let held = fs::OpenOptions::new().read(true).share_mode(1).open(&path).unwrap();
        let mut pauses = 0;
        let mut holder = Some(held);
        let result = replace_with(&temp, &path, 6, |_| { pauses += 1; if pauses == 2 { holder.take(); } });
        assert!(result.is_ok(), "{result:?}");
        assert!(pauses >= 1);
        assert_eq!(status(&work)["state"], "FAILED");
        let _ = fs::remove_dir_all(work);
    }
}
