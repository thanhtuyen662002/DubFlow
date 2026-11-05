//! One DubFlow per Windows session. A second launch brings the running
//! window to the front and exits: two hosts would fight over the link
//! service's lock and the job queue.
//!
//! A restart or an update starts the new host before the old one has quit;
//! that host is told so through `DUBFLOW_RELAUNCH_AFTER` and waits for the
//! old one to let go instead of exiting.

/// Set (to the old host's pid) on the launcher a restart or an update starts.
pub const RELAUNCH_AFTER: &str = "DUBFLOW_RELAUNCH_AFTER";

/// The per-session claim's name.
#[cfg(windows)]
const CLAIM_NAME: &str = "Local\\DubFlowDesktop-com.dubflow.local";

/// The main window's title (tauri.conf.json); how a second launch finds it.
#[cfg(windows)]
const MAIN_TITLE: &str = "DubFlow Local";

/// How long a restarted host waits for the old one to quit.
#[cfg(windows)]
const RELAUNCH_WAIT_MS: u32 = 90_000;

/// What asking Windows for the one-per-session claim gave.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Claim {
    /// This host holds it.
    Owned,
    /// Another host holds it.
    Taken,
    /// Windows could not create or open it.
    Failed,
}

/// What waiting on an existing claim gave.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Wait {
    /// This host took it (free, or left behind by a host that quit).
    Acquired,
    /// Another host still holds it.
    Busy,
    /// Windows could not wait on it; nothing is known about other hosts.
    Failed,
}

/// What a launch should do.
#[derive(Debug, PartialEq, Eq)]
pub enum Launch {
    /// This host is the one; carry on.
    Run,
    /// Another host runs; show its window and quit.
    FocusOther,
    /// Whether another host runs is unknown; say so and quit rather than
    /// risk two hosts on one queue.
    Refuse,
}

/// The decision for a launch. An existing claim may have no holder any more
/// (its host has just quit), so every launch tries to take it: at once for
/// a plain launch, for up to `relaunch_wait_ms` for a restart. `wait` gets
/// the milliseconds to wait and tells what came of it.
pub fn decide(claim: Claim, relaunch: bool, relaunch_wait_ms: u32, wait: impl FnOnce(u32) -> Wait) -> Launch {
    match claim {
        Claim::Owned => Launch::Run,
        Claim::Failed => Launch::Refuse,
        Claim::Taken => match wait(if relaunch { relaunch_wait_ms } else { 0 }) {
            Wait::Acquired => Launch::Run,
            Wait::Busy => Launch::FocusOther,
            Wait::Failed => Launch::Refuse,
        },
    }
}

/// Whether a relaunch note names a process: only a restart or an update sets it.
pub fn relaunch_requested(value: Option<std::ffi::OsString>) -> bool {
    value.and_then(|value| value.into_string().ok()).is_some_and(|value| value.trim().parse::<u32>().is_ok_and(|pid| pid > 0))
}

/// A held or awaited claim. The handle stays open for the life of the
/// process; Windows releases the claim when the process ends, however it ends.
#[cfg(windows)]
struct Mutex(windows_sys::Win32::Foundation::HANDLE);

// A kernel handle belongs to the process, not to a thread.
#[cfg(windows)]
unsafe impl Send for Mutex {}

#[cfg(windows)]
impl Mutex {
    fn open(name: &str) -> (Option<Self>, Claim) {
        use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
        use windows_sys::Win32::System::Threading::CreateMutexW;
        let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let handle = unsafe { CreateMutexW(std::ptr::null(), 1, wide.as_ptr()) };
        if handle.is_null() {
            return (None, Claim::Failed);
        }
        let claim = if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS { Claim::Taken } else { Claim::Owned };
        (Some(Self(handle)), claim)
    }

    /// Wait for the holder to let go; an abandoned claim is a claim.
    fn wait(&self, milliseconds: u32) -> Wait {
        use windows_sys::Win32::Foundation::{WAIT_ABANDONED, WAIT_OBJECT_0, WAIT_TIMEOUT};
        use windows_sys::Win32::System::Threading::WaitForSingleObject;
        match unsafe { WaitForSingleObject(self.0, milliseconds) } {
            WAIT_OBJECT_0 | WAIT_ABANDONED => Wait::Acquired,
            WAIT_TIMEOUT => Wait::Busy,
            _ => Wait::Failed,
        }
    }
}

/// Claim the session for this host, or hand the user to the running one.
/// Returns false when this host must exit at once.
#[cfg(windows)]
pub fn claim() -> bool {
    let relaunch = relaunch_requested(std::env::var_os(RELAUNCH_AFTER));
    // The helpers this host starts must never inherit the note.
    std::env::remove_var(RELAUNCH_AFTER);
    let (mutex, claim) = Mutex::open(CLAIM_NAME);
    let launch = decide(claim, relaunch, RELAUNCH_WAIT_MS, |ms| mutex.as_ref().map_or(Wait::Failed, |mutex| mutex.wait(ms)));
    // Held until the process ends.
    std::mem::forget(mutex);
    match launch {
        Launch::Run => true,
        Launch::FocusOther => {
            focus_other();
            false
        }
        Launch::Refuse => {
            refuse();
            false
        }
    }
}

#[cfg(not(windows))]
pub fn claim() -> bool {
    true
}

#[cfg(windows)]
fn refuse() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    let text: Vec<u16> = "DubFlow không kiểm tra được có bản DubFlow nào khác đang chạy hay không, nên chưa mở để tránh hai bản dùng chung hàng đợi.\r\n\r\nHãy đóng mọi cửa sổ DubFlow rồi mở lại. Nếu vẫn lỗi, khởi động lại máy."
        .encode_utf16().chain(Some(0)).collect();
    let caption: Vec<u16> = "DubFlow".encode_utf16().chain(Some(0)).collect();
    unsafe { MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), MB_OK | MB_ICONERROR) };
}

/// How long a second launch looks for the first one's window: a cold first
/// start (WebView2, the link service) can take this long to show it.
#[cfg(windows)]
const FOCUS_WAIT_MS: u64 = 30_000;

/// Bring the running host's main window to the front. The window may not
/// exist yet when both launches are moments apart, so look for a while.
/// When Windows refuses to hand over the foreground, the taskbar button
/// flashes instead, so the user still sees where DubFlow is.
#[cfg(windows)]
fn focus_other() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        FlashWindowEx, GetForegroundWindow, IsIconic, SetForegroundWindow, ShowWindow, FLASHWINFO, FLASHW_ALL, FLASHW_TIMERNOFG, SW_RESTORE, SW_SHOW,
    };
    let step = std::time::Duration::from_millis(100);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(FOCUS_WAIT_MS);
    while std::time::Instant::now() < deadline {
        if let Some(window) = other_window(MAIN_TITLE) {
            unsafe {
                ShowWindow(window, if IsIconic(window) != 0 { SW_RESTORE } else { SW_SHOW });
                if SetForegroundWindow(window) == 0 || GetForegroundWindow() != window {
                    let flash = FLASHWINFO {
                        cbSize: std::mem::size_of::<FLASHWINFO>() as u32,
                        hwnd: window,
                        dwFlags: FLASHW_ALL | FLASHW_TIMERNOFG,
                        uCount: 0,
                        dwTimeout: 0,
                    };
                    FlashWindowEx(&flash);
                }
            }
            return;
        }
        std::thread::sleep(step);
    }
}

/// Which of the windows titled like DubFlow's to bring forward: one of a
/// process started from this same program first (an older DubFlow without
/// the claim may still run alongside), else the topmost one.
pub fn pick_window<T: Copy>(candidates: &[(T, Option<std::path::PathBuf>)], own: Option<&std::path::Path>) -> Option<T> {
    let same = |path: &Option<std::path::PathBuf>| match (path, own) {
        (Some(path), Some(own)) => path.as_os_str().eq_ignore_ascii_case(own.as_os_str()),
        _ => false,
    };
    candidates.iter().find(|(_, path)| same(path)).or_else(|| candidates.first()).map(|(window, _)| *window)
}

/// A top-level window of another process with exactly this title.
#[cfg(windows)]
fn other_window(title: &str) -> Option<windows_sys::Win32::Foundation::HWND> {
    use windows_sys::Win32::Foundation::{CloseHandle, BOOL, HWND, LPARAM};
    use windows_sys::Win32::System::Threading::{GetCurrentProcessId, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION};
    use windows_sys::Win32::UI::WindowsAndMessaging::{EnumWindows, GetWindowTextW, GetWindowThreadProcessId};

    struct Search<'a> {
        title: &'a str,
        found: Vec<(HWND, u32)>,
    }
    unsafe extern "system" fn visit(window: HWND, search: LPARAM) -> BOOL {
        let search = &mut *(search as *mut Search);
        let mut text = [0u16; 64];
        let length = GetWindowTextW(window, text.as_mut_ptr(), text.len() as i32);
        let mut pid = 0u32;
        GetWindowThreadProcessId(window, &mut pid);
        if length > 0 && pid != GetCurrentProcessId() && String::from_utf16_lossy(&text[..length as usize]) == search.title {
            search.found.push((window, pid));
        }
        1
    }
    fn image(pid: u32) -> Option<std::path::PathBuf> {
        use std::os::windows::ffi::OsStringExt;
        let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if process.is_null() {
            return None;
        }
        let mut buffer = [0u16; 1024];
        let mut length = buffer.len() as u32;
        let ok = unsafe { QueryFullProcessImageNameW(process, PROCESS_NAME_WIN32, buffer.as_mut_ptr(), &mut length) };
        unsafe { CloseHandle(process) };
        (ok != 0).then(|| std::ffi::OsString::from_wide(&buffer[..length as usize]).into())
    }
    let mut search = Search { title, found: Vec::new() };
    unsafe { EnumWindows(Some(visit), &mut search as *mut Search as LPARAM) };
    let candidates: Vec<(HWND, Option<std::path::PathBuf>)> = search.found.iter().map(|&(window, pid)| (window, image(pid))).collect();
    let own = std::env::current_exe().ok();
    pick_window(&candidates, own.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_second_launch_focuses_the_first_unless_it_is_a_relaunch() {
        assert_eq!(decide(Claim::Owned, false, 90, |_| unreachable!()), Launch::Run);
        assert_eq!(decide(Claim::Owned, true, 90, |_| unreachable!()), Launch::Run, "the first host never waits");
        assert_eq!(decide(Claim::Taken, false, 90, |ms| { assert_eq!(ms, 0, "a plain launch only looks"); Wait::Busy }), Launch::FocusOther);
        assert_eq!(decide(Claim::Taken, true, 90, |ms| { assert_eq!(ms, 90); Wait::Acquired }), Launch::Run, "a restart takes over once the old host has gone");
        assert_eq!(decide(Claim::Taken, true, 90, |_| Wait::Busy), Launch::FocusOther, "an old host that never quits keeps its place");
    }

    #[test]
    fn a_claim_windows_cannot_make_never_starts_a_second_host() {
        assert_eq!(decide(Claim::Failed, false, 90, |_| unreachable!()), Launch::Refuse);
        assert_eq!(decide(Claim::Failed, true, 90, |_| unreachable!()), Launch::Refuse, "not even a restart guesses");
        for relaunch in [false, true] {
            assert_eq!(decide(Claim::Taken, relaunch, 90, |_| Wait::Failed), Launch::Refuse, "a failed wait proves nothing about another host");
        }
    }

    #[test]
    fn a_plain_launch_takes_a_claim_whose_host_has_just_quit() {
        // The old host quit between this launch opening the claim and looking at it.
        assert_eq!(decide(Claim::Taken, false, 90, |ms| if ms == 0 { Wait::Acquired } else { Wait::Busy }), Launch::Run);
    }

    #[test]
    fn the_window_of_this_same_program_wins_over_an_older_dubflow() {
        use std::path::{Path, PathBuf};
        let own = Path::new(r"C:\DubFlow\versions\1.1.71\app\bin\DubFlow.exe");
        let old = Some(PathBuf::from(r"C:\DubFlow\versions\1.1.70\app\bin\DubFlow.exe"));
        let same = Some(PathBuf::from(r"c:\dubflow\VERSIONS\1.1.71\app\bin\dubflow.exe"));
        assert_eq!(pick_window(&[(1, old.clone()), (2, same), (3, None)], Some(own)), Some(2), "same program, any letter case");
        assert_eq!(pick_window(&[(1, old.clone()), (3, None)], Some(own)), Some(1), "else the topmost");
        assert_eq!(pick_window(&[(1, old)], None), Some(1));
        assert_eq!(pick_window::<u8>(&[], Some(own)), None);
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

    /// The real Windows claim: a holder keeps it from a second opener, and
    /// a waiting opener gets it the moment the holder goes away.
    #[cfg(windows)]
    #[test]
    fn the_windows_claim_is_exclusive_and_handed_over_when_its_holder_ends() {
        use std::sync::mpsc;
        let name = format!("Local\\DubFlowDesktop-test-{}-{:?}", std::process::id(), std::time::SystemTime::now());
        // Windows refuses a name with a backslash past its namespace: the claim fails closed.
        assert_eq!(Mutex::open("Local\\bad\\name\\with\\too\\many").1, Claim::Failed);
        let (held, release) = (mpsc::channel(), mpsc::channel::<()>());
        let holder = {
            let name = name.clone();
            let (held, release) = (held.0, release.1);
            std::thread::spawn(move || {
                let (mutex, claim) = Mutex::open(&name);
                held.send(claim).unwrap();
                release.recv().unwrap();
                // Ends without releasing, like a host that quits: the claim is abandoned.
                std::mem::forget(mutex);
            })
        };
        assert_eq!(held.1.recv().unwrap(), Claim::Owned);
        let (second, claim) = Mutex::open(&name);
        let second = second.unwrap();
        assert_eq!(claim, Claim::Taken);
        assert_eq!(second.wait(200), Wait::Busy, "the holder still runs");
        release.0.send(()).unwrap();
        holder.join().unwrap();
        assert_eq!(second.wait(5_000), Wait::Acquired, "the holder has gone, so the claim is handed over");
        assert_eq!(decide(claim, true, 5_000, |ms| second.wait(ms)), Launch::Run);
    }

    /// A plain launch that opens a claim whose holder has already gone (the
    /// holder ended without letting go) takes it at once instead of exiting.
    #[cfg(windows)]
    #[test]
    fn a_plain_launch_takes_an_abandoned_windows_claim() {
        let name = format!("Local\\DubFlowDesktop-abandoned-{}-{:?}", std::process::id(), std::time::SystemTime::now());
        let (opened, keep) = std::sync::mpsc::channel();
        let holder = {
            let name = name.clone();
            std::thread::spawn(move || {
                let (mutex, claim) = Mutex::open(&name);
                assert_eq!(claim, Claim::Owned);
                // Keep the object alive after this thread (its owner) ends.
                opened.send(mutex).unwrap();
            })
        };
        let first = keep.recv().unwrap();
        holder.join().unwrap();
        let (second, claim) = Mutex::open(&name);
        assert_eq!(claim, Claim::Taken, "the object still exists");
        let second = second.unwrap();
        assert_eq!(decide(claim, false, 90_000, |ms| second.wait(ms)), Launch::Run);
        // Now this launch holds it: a third launch must not take it.
        let third = std::thread::spawn(move || {
            let (mutex, claim) = Mutex::open(&name);
            let mutex = mutex.unwrap();
            (claim, decide(claim, false, 90_000, |ms| mutex.wait(ms)))
        }).join().unwrap();
        assert_eq!(third, (Claim::Taken, Launch::FocusOther));
        drop(first);
    }
}
