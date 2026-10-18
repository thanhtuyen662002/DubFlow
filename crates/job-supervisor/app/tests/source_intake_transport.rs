#[path = "../../../../apps/desktop/src-tauri/src/source_transport/mod.rs"]
mod source_transport;
use source_transport::{request, Error, Limits};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc, OnceLock,
};
use std::time::{Duration, Instant};

fn fixture() -> &'static Path {
    static BIN: OnceLock<PathBuf> = OnceLock::new();
    BIN.get_or_init(|| {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "dubflow-source-fixture-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let source = dir.join("fixture.rs");
        std::fs::write(&source, include_str!("source_intake/fixture.rs")).unwrap();
        let bin = dir.join(if cfg!(windows) {
            "fixture.exe"
        } else {
            "fixture"
        });
        let flags = std::env::var("CARGO_ENCODED_RUSTFLAGS").unwrap_or_default();
        let output = Command::new("rustc")
            .args(["--edition=2021", "-o"])
            .arg(&bin)
            .arg(&source)
            .args(flags.split('\u{1f}').filter(|flag| !flag.is_empty()))
            .output()
            .expect("compile controlled fixture with the existing Rust toolchain");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        bin
    })
}
fn invocation(mode: &str) -> (Command, PathBuf) {
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    let path = fixture()
        .parent()
        .unwrap()
        .join(format!("pids-{}", NEXT.fetch_add(1, Ordering::Relaxed)));
    let mut command = Command::new(fixture());
    command.arg(mode).arg(&path);
    (command, path)
}
fn limits() -> Limits {
    Limits {
        timeout: Duration::from_millis(800),
        retire_timeout: Duration::from_secs(2),
        request_bytes: 4 * 1024 * 1024,
        stdout_bytes: 1024 * 1024,
        stderr_bytes: 1024 * 1024,
    }
}
fn pids(path: &Path, count: usize) -> Vec<u32> {
    let start = Instant::now();
    loop {
        let ids: Option<Vec<u32>> = std::fs::read_to_string(path)
            .ok()
            .and_then(|s| s.split(',').map(|id| id.parse().ok()).collect());
        if let Some(ids) = ids {
            if ids.len() >= count {
                return ids;
            }
        }
        assert!(
            start.elapsed() < Duration::from_secs(5),
            "fixture did not publish live pids"
        );
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[cfg(windows)]
mod observation {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn WaitForSingleObject(handle: *mut c_void, timeout: u32) -> u32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    pub struct Live(*mut c_void);
    impl Live {
        pub fn open(pid: u32) -> Self {
            let handle = unsafe { OpenProcess(0x0010_0000, 0, pid) };
            assert!(
                !handle.is_null(),
                "observe the real live process before cleanup"
            );
            assert_eq!(unsafe { WaitForSingleObject(handle, 0) }, 258);
            Self(handle)
        }
        pub fn stopped(&self) {
            assert_eq!(unsafe { WaitForSingleObject(self.0, 2000) }, 0);
        }
    }
    impl Drop for Live {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
#[cfg(unix)]
mod observation {
    pub struct Live(u32);
    impl Live {
        pub fn open(pid: u32) -> Self {
            assert!(std::path::Path::new(&format!("/proc/{pid}")).exists());
            Self(pid)
        }
        pub fn stopped(&self) {
            // Same bounded kernel observation as WaitForSingleObject(2000)
            // on Windows. SIGKILL delivery and /proc retirement are async.
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
            loop {
                let stat = std::fs::read_to_string(format!("/proc/{}/stat", self.0));
                if stat.is_err() || stat.unwrap().split_once(") ").unwrap().1.starts_with('Z') {
                    return;
                }
                assert!(
                    std::time::Instant::now() < deadline,
                    "owned process did not retire"
                );
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
        }
    }
}

#[test]
fn partial_stdout_and_silence_really_end_at_deadline_and_reap_child() {
    for mode in ["partial", "silence", "ignore_stdin"] {
        let (mut command, path) = invocation(mode);
        let (go, receive) = std::sync::mpsc::channel();
        let (done, finished) = std::sync::mpsc::channel();
        let watcher = std::thread::spawn(move || {
            let observed = observation::Live::open(pids(&path, 1)[0]);
            go.send(()).unwrap();
            finished.recv_timeout(Duration::from_secs(5)).unwrap();
            observed.stopped();
        });
        // A separate marker file is unnecessary: hold the observation until
        // transport's deadline + bounded cleanup has completed.
        let start = Instant::now();
        let payload = if mode == "ignore_stdin" {
            vec![b'x'; 2 * 1024 * 1024]
        } else {
            b"{}".to_vec()
        };
        assert_eq!(
            request(&mut command, payload, limits(), &AtomicBool::new(false)),
            Err(Error::Timeout)
        );
        assert!(
            start.elapsed() < Duration::from_secs(4),
            "deadline must end the operation"
        );
        receive.recv_timeout(Duration::from_secs(1)).unwrap();
        done.send(()).unwrap();
        watcher.join().unwrap();
    }
}

#[test]
fn chunked_and_newline_free_stdout_and_full_stderr_complete() {
    for mode in ["chunks", "no_newline", "stderr"] {
        let (mut command, _) = invocation(mode);
        let response = source_transport::json_request(
            &mut command,
            &serde_json::json!({"command":"test"}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(response["event"], "source_scan_accepted");
    }
}

#[test]
fn output_caps_nonzero_exit_and_parse_failures_are_truthful() {
    for (mode, error) in [
        ("stdout_flood", Error::StdoutLimit),
        ("stderr_flood", Error::StderrLimit),
        ("exit_error", Error::Exit),
    ] {
        let (mut command, _) = invocation(mode);
        assert_eq!(
            request(
                &mut command,
                b"{}".to_vec(),
                limits(),
                &AtomicBool::new(false)
            ),
            Err(error)
        );
    }
    for mode in ["malformed", "service_error"] {
        let (mut command, _) = invocation(mode);
        let result = source_transport::json_request(
            &mut command,
            &serde_json::json!({}),
            &AtomicBool::new(false),
        );
        assert!(result.is_err());
        assert!(!result.unwrap_err().contains("private-credential"));
    }
}

#[test]
fn cancellation_retires_live_child_and_descendant_preserves_neighbor() {
    let (mut command, path) = invocation("tree");
    let (mut neighbor, neighbor_path) = invocation("ignore_stdin");
    let mut neighbor = Neighbor(
        neighbor
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let neighbor_pid = pids(&neighbor_path, 1)[0];
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    let (done, finished) = std::sync::mpsc::channel();
    let watcher = std::thread::spawn(move || {
        let ids = pids(&path, 2);
        let parent = observation::Live::open(ids[0]);
        let child = observation::Live::open(ids[1]);
        flag.store(true, Ordering::Release);
        finished.recv_timeout(Duration::from_secs(5)).unwrap();
        parent.stopped();
        child.stopped();
    });
    let mut bounds = limits();
    bounds.timeout = Duration::from_secs(5);
    let start = Instant::now();
    assert_eq!(
        request(&mut command, b"{}".to_vec(), bounds, &cancelled),
        Err(Error::Cancelled)
    );
    assert!(start.elapsed() < Duration::from_secs(3));
    done.send(()).unwrap();
    watcher.join().unwrap();
    assert_eq!(neighbor.0.id(), neighbor_pid);
    assert!(
        neighbor.0.try_wait().unwrap().is_none(),
        "unrelated local work survives"
    );
    neighbor.0.kill().unwrap();
    neighbor.0.wait().unwrap();
}
struct Neighbor(Child);
impl Drop for Neighbor {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn pre_cancel_and_request_cap_do_not_spawn() {
    for (cancel, cap, expected) in [
        (true, 10, Error::Cancelled),
        (false, 1, Error::RequestLimit),
    ] {
        let (mut command, path) = invocation("silence");
        let mut bounds = limits();
        bounds.request_bytes = cap;
        assert_eq!(
            request(
                &mut command,
                b"{}".to_vec(),
                bounds,
                &AtomicBool::new(cancel)
            ),
            Err(expected)
        );
        assert!(!path.exists());
    }
}

#[test]
fn deadline_retires_descendant_holding_pipe_after_parent_exit() {
    let (mut command, path) = invocation("orphaned_pipe");
    let (done, finished) = std::sync::mpsc::channel();
    let watcher = std::thread::spawn(move || {
        let ids = pids(&path, 2);
        let descendant = observation::Live::open(ids[1]);
        finished.recv_timeout(Duration::from_secs(5)).unwrap();
        descendant.stopped();
    });
    let start = Instant::now();
    assert_eq!(
        request(
            &mut command,
            b"{}".to_vec(),
            limits(),
            &AtomicBool::new(false)
        ),
        Err(Error::Timeout)
    );
    assert!(start.elapsed() < Duration::from_secs(4));
    done.send(()).unwrap();
    watcher.join().unwrap();
}
