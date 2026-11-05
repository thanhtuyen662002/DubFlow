//! One DubFlow per Windows session. A second launch brings the running
//! window to the front and exits: two hosts would fight over the link
//! service's lock and the job queue.
//!
//! A restart or an update starts the new host before the old one has quit;
//! that host is told so through `DUBFLOW_RELAUNCH_AFTER` and waits for the
//! old one to let go instead of exiting.

/// Set (to the old host's pid) on the launcher a restart or an update starts.
pub const RELAUNCH_AFTER: &str = "DUBFLOW_RELAUNCH_AFTER";

/// The main window's title (tauri.conf.json); how a second launch finds it.
#[cfg(windows)]
const MAIN_TITLE: &str = "DubFlow Local";

/// What a launch should do once it knows whether another host runs.
#[derive(Debug, PartialEq, Eq)]
pub enum Launch {
    /// This host is the one; carry on.
    Run,
    /// Another host runs; show its window and quit.
    FocusOther,
}

/// The decision for a launch: `owned` tells whether this host got the
/// one-per-session claim, `relaunch` whether it may wait for the claim and
/// `waited` whether that wait ended with the claim.
pub fn decide(owned: bool, relaunch: bool, waited: impl FnOnce() -> bool) -> Launch {
    if owned || (relaunch && waited()) {
        Launch::Run
    } else {
        Launch::FocusOther
    }
}

/// Whether a relaunch note names a process: only a restart or an update sets it.
pub fn relaunch_requested(value: Option<std::ffi::OsString>) -> bool {
    value.and_then(|value| value.into_string().ok()).is_some_and(|value| value.trim().parse::<u32>().is_ok_and(|pid| pid > 0))
}

/// Claim the session for this host, or hand the user to the running one.
/// Returns false when this host must exit at once.
#[cfg(windows)]
pub fn claim() -> bool {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, WAIT_ABANDONED, WAIT_OBJECT_0};
    use windows_sys::Win32::System::Threading::{CreateMutexW, WaitForSingleObject};
    let relaunch = relaunch_requested(std::env::var_os(RELAUNCH_AFTER));
    // The helpers this host starts must never inherit the note.
    std::env::remove_var(RELAUNCH_AFTER);
    let name: Vec<u16> = "Local\\DubFlowDesktop-com.dubflow.local".encode_utf16().chain(Some(0)).collect();
    // The handle stays open for the life of the process; Windows releases
    // the claim when it exits, however it exits.
    let mutex = unsafe { CreateMutexW(std::ptr::null(), 1, name.as_ptr()) };
    if mutex.is_null() {
        // Without the claim the old behaviour (no guard) is the safe one.
        return true;
    }
    let owned = unsafe { GetLastError() } != ERROR_ALREADY_EXISTS;
    let waited = || {
        // An abandoned claim is a claim: the old host has gone.
        let result = unsafe { WaitForSingleObject(mutex, 90_000) };
        result == WAIT_OBJECT_0 || result == WAIT_ABANDONED
    };
    match decide(owned, relaunch, waited) {
        Launch::Run => true,
        Launch::FocusOther => {
            focus_other();
            false
        }
    }
}

#[cfg(not(windows))]
pub fn claim() -> bool {
    true
}

/// Bring the running host's main window to the front. The window may not
/// exist yet when both launches are moments apart, so look for a while.
#[cfg(windows)]
fn focus_other() {
    use windows_sys::Win32::Foundation::{BOOL, HWND, LPARAM};
    use windows_sys::Win32::System::Threading::GetCurrentProcessId;
    use windows_sys::Win32::UI::WindowsAndMessaging::{EnumWindows, GetWindowTextW, GetWindowThreadProcessId, IsIconic, SetForegroundWindow, ShowWindow, SW_RESTORE, SW_SHOW};

    unsafe extern "system" fn visit(window: HWND, found: LPARAM) -> BOOL {
        let mut text = [0u16; 64];
        let length = GetWindowTextW(window, text.as_mut_ptr(), text.len() as i32);
        let mut pid = 0u32;
        GetWindowThreadProcessId(window, &mut pid);
        if length > 0 && pid != GetCurrentProcessId() && String::from_utf16_lossy(&text[..length as usize]) == MAIN_TITLE {
            *(found as *mut HWND) = window;
            return 0;
        }
        1
    }

    for _ in 0..30 {
        let mut found: HWND = std::ptr::null_mut();
        unsafe { EnumWindows(Some(visit), &mut found as *mut HWND as LPARAM) };
        if !found.is_null() {
            unsafe {
                ShowWindow(found, if IsIconic(found) != 0 { SW_RESTORE } else { SW_SHOW });
                SetForegroundWindow(found);
            }
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_second_launch_focuses_the_first_unless_it_is_a_relaunch() {
        assert_eq!(decide(true, false, || unreachable!()), Launch::Run);
        assert_eq!(decide(true, true, || unreachable!()), Launch::Run, "the first host never waits");
        assert_eq!(decide(false, false, || unreachable!()), Launch::FocusOther, "a plain second launch never waits");
        assert_eq!(decide(false, true, || true), Launch::Run, "a restart takes over once the old host has gone");
        assert_eq!(decide(false, true, || false), Launch::FocusOther, "an old host that never quits keeps its place");
    }

    #[test]
    fn only_a_process_id_marks_a_relaunch() {
        assert!(relaunch_requested(Some("4242".into())));
        assert!(relaunch_requested(Some(" 17 ".into())));
        for value in ["", "0", "yes", "-3", "12a"] {
            assert!(!relaunch_requested(Some(value.into())), "{value:?}");
        }
        assert!(!relaunch_requested(None));
    }
}
