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

/// After the engine has ended: a status still RUNNING (or unreadable) is settled. The engine's own final status
/// (its last stdout line) wins, except that COMPLETED counts only with a successful exit; otherwise a cancel request
/// makes it CANCELLED, anything else FAILED with `stopped` and where the engine's error output is.
pub(crate) fn settle_after_exit(work: &Path, exited_ok: bool, stopped: &str) {
    let status_path = work.join("status.json");
    let current = crate::product::read_small_json(&status_path, TAIL_BYTES);
    if current.as_ref().is_some_and(|value| value["state"] != "RUNNING") {
        return;
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
