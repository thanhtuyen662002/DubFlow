//! A lease on one video's work folder, held for a media child's whole life, so a
//! second DubFlow window cannot start the same operation on the same video. The
//! lease is a file opened without sharing; Windows releases it when this process
//! exits, also after a crash, so a stale lease never blocks the next run.
use std::fs::{File, OpenOptions};
use std::path::Path;

#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;

use crate::fail;

const ERROR_SHARING_VIOLATION: i32 = 32;

/// Take the lease on `work`, or fail with `busy` when another holder has it.
pub fn take(work: &Path, busy: &str) -> Result<File, String> {
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(false);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(work.join("lease")).map_err(|error| match error.raw_os_error() {
        Some(ERROR_SHARING_VIOLATION) => fail(busy),
        _ => fail("Không giữ được thư mục làm việc"),
    })
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn one_holder_at_a_time_and_released_on_drop() {
        let work = std::env::temp_dir().join(format!("dubflow-work-lease-{}", std::process::id()));
        std::fs::create_dir_all(&work).unwrap();
        let first = take(&work, "đang chạy").unwrap();
        assert!(take(&work, "đang chạy").unwrap_err().contains("đang chạy"));
        drop(first);
        drop(take(&work, "đang chạy").unwrap());
        std::fs::remove_dir_all(work).unwrap();
    }

    #[test]
    fn another_process_holds_it_until_killed() {
        let work = std::env::temp_dir().join(format!("dubflow-work-lease-other-{}", std::process::id()));
        std::fs::create_dir_all(&work).unwrap();
        let ready = work.join("ready");
        let script = format!("$f=[IO.File]::Open('{}','OpenOrCreate','ReadWrite','None'); New-Item '{}' | Out-Null; Start-Sleep 60",
            work.join("lease").display(), ready.display());
        let mut other = std::process::Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", &script]).spawn().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while !ready.exists() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        assert!(ready.exists(), "the other process never took the lease");
        assert!(take(&work, "đang chạy").unwrap_err().contains("đang chạy"));
        other.kill().unwrap();
        other.wait().unwrap();
        drop(take(&work, "đang chạy").unwrap());
        std::fs::remove_dir_all(work).unwrap();
    }
}
