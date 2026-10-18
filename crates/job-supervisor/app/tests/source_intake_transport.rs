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

mod persistent {
    use super::*;
    use source_transport::service::{
        AdmissionWindow, Client, Failure, LaunchPin, NativeRequest, Phase,
    };

    fn launch(mode: &str, startup: u64) -> (Client, PathBuf) {
        let (command, path) = invocation(mode);
        let mut bounds = limits();
        bounds.timeout = Duration::from_millis(80);
        (
            Client::spawn(
                command,
                AdmissionWindow::new(Duration::from_millis(startup)).unwrap(),
                bounds,
            )
            .unwrap(),
            path,
        )
    }
    fn phase(client: &Client, expected: Phase) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if client.snapshot().phase == expected {
                return;
            }
            assert!(Instant::now() < deadline, "phase: {:?}", client.snapshot());
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    fn ready(client: &Client) {
        phase(client, Phase::Ready);
        client.drain();
    }
    fn stopped(client: &Client, failure: Option<Failure>) {
        phase(client, Phase::Stopped);
        assert!(client.snapshot().retired, "retirement must be observed");
        assert_eq!(client.snapshot().failure, failure);
    }
    fn status() -> NativeRequest {
        NativeRequest::Status {
            scan_id: "scan".into(),
        }
    }
    fn send(client: &Client, request: NativeRequest) {
        let result = client
            .submit(request, Arc::new(AtomicBool::new(false)))
            .unwrap();
        assert_eq!(result.delivery, "submitted");
        assert!(!serde_json::to_string(&result).unwrap().contains("accepted"));
    }
    fn event(client: &Client, name: &str) -> Vec<source_transport::service::Observation> {
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut observed = Vec::new();
        loop {
            observed.extend(client.drain());
            if observed.iter().any(|e| e.value["event"] == name) {
                return observed;
            }
            assert!(Instant::now() < deadline, "native fixture event missing");
            std::thread::sleep(Duration::from_millis(2));
        }
    }

    #[test]
    fn cli_pin_and_closed_schema_match_current_native_service() {
        let root = std::env::temp_dir().join("immutable release");
        let data = std::env::temp_dir().join("source data");
        let hash = "a".repeat(64);
        let pin = LaunchPin {
            root: root.clone(),
            data_root: data.clone(),
            manifest_sha256: hash.clone(),
        };
        let command = pin.command().unwrap();
        assert_eq!(
            command.get_program(),
            root.join("app/bin/dubflow-supervisor.exe")
        );
        assert_eq!(
            command
                .get_args()
                .map(|s| s.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
            [
                "source-serve".to_owned(),
                "--root".into(),
                root.to_string_lossy().into_owned(),
                "--data-root".into(),
                data.to_string_lossy().into_owned(),
                "--manifest-sha256".into(),
                hash
            ]
        );
        let request = serde_json::json!({"command":"start_video","scan_id":"scan","provider_id":"generic","source_ref":"https://example.test/x"});
        let native: NativeRequest = serde_json::from_value(request.clone()).unwrap();
        assert_eq!(serde_json::to_value(native).unwrap(), request);
        assert!(serde_json::from_value::<NativeRequest>(
            serde_json::json!({"command":"enqueue_sources","items":[]})
        )
        .is_err());
        assert!(serde_json::from_value::<NativeRequest>(
            serde_json::json!({"command":"status","scan_id":"scan","request_id":1})
        )
        .is_err());
        assert!(AdmissionWindow::new(Duration::ZERO).is_err());
    }

    #[test]
    fn full_startup_admission_is_distinct_from_short_request_bound() {
        let (client, path) = launch("service_slow", 1500);
        assert_eq!(
            client.submit(status(), Arc::new(AtomicBool::new(false))),
            Err(Failure::NotReady)
        );
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(client.snapshot().phase, Phase::Admitting); // exceeds 80ms request bound
        ready(&client);
        send(&client, status());
        let values = event(&client, "source_status");
        assert_eq!(
            values
                .iter()
                .map(|e| e.value["event"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["source_download_progress", "source_status"]
        );
        assert!(values[0].sequence < values[1].sequence);
        assert!(!PathBuf::from(format!("{}.eof", path.display())).exists());
        client.stop();
        stopped(&client, None);
    }

    #[test]
    fn admission_timeout_wrong_ready_and_startup_cancellation_retire_child() {
        let (client, path) = launch("service_admission_stall", 1500);
        let live = observation::Live::open(pids(&path, 1)[0]);
        stopped(&client, Some(Failure::AdmissionTimeout));
        live.stopped();
        let (client, _) = launch("service_wrong_ready", 1000);
        stopped(&client, Some(Failure::Protocol));
        let (client, path) = launch("service_admission_stall", 5000);
        let live = observation::Live::open(pids(&path, 1)[0]);
        client.stop();
        stopped(&client, None);
        live.stopped();
    }

    #[test]
    fn native_scan_cancel_keeps_service_owner_and_stdin_alive_until_shutdown() {
        let (client, path) = launch("service_live", 1500);
        ready(&client);
        let owner = observation::Live::open(pids(&path, 1)[0]);
        send(
            &client,
            NativeRequest::StartVideo {
                scan_id: "scan".into(),
                provider_id: "generic".into(),
                source_ref: "https://example.test/clip,1".into(),
            },
        );
        event(&client, "source_preparing");
        let worker = observation::Live::open(pids(&path, 2)[1]);
        send(
            &client,
            NativeRequest::Cancel {
                scan_id: "scan".into(),
            },
        );
        event(&client, "source_preparation_stopped");
        worker.stopped();
        assert_eq!(client.snapshot().phase, Phase::Ready);
        send(&client, status());
        event(&client, "source_status");
        assert!(!PathBuf::from(format!("{}.eof", path.display())).exists());
        send(&client, NativeRequest::Shutdown);
        stopped(&client, None);
        owner.stopped();
        assert_eq!(
            std::fs::read_to_string(format!("{}.commands", path.display())).unwrap(),
            "start_video\ncancel\nstatus\nshutdown\n"
        );
    }

    #[test]
    fn full_stderr_partial_line_and_overflow_have_distinct_bounded_outcomes() {
        let (client, _) = launch("service_stderr", 1500);
        ready(&client);
        send(&client, status());
        event(&client, "source_status");
        client.stop();
        stopped(&client, None);
        for (mode, failure) in [
            ("service_partial", Failure::ResponseTimeout),
            ("service_stdout_flood", Failure::StdoutLimit),
            ("service_stderr_flood", Failure::StderrLimit),
            ("service_events_flood", Failure::EventOverflow),
        ] {
            let (client, _) = launch(mode, 1500);
            stopped(&client, Some(failure));
        }
    }

    fn fill_pipe(client: &Client, flag: Arc<AtomicBool>) -> Failure {
        for _ in 0..512 {
            // finite legal line sizes eventually fill an unread OS pipe
            let request = NativeRequest::Start {
                scan_id: "scan".into(),
                provider_id: "generic".into(),
                source_ref: "https://example.test/".to_owned() + &"a".repeat(4000),
                page_size: 1,
                max_items: 1,
            };
            match client.submit(request, flag.clone()) {
                Ok(submitted) => assert_eq!(submitted.delivery, "submitted"),
                Err(error) => return error,
            }
        }
        panic!("fixture pipe did not become bounded/backpressured");
    }
    #[test]
    fn blocked_request_write_times_out_and_cancelled_write_retires_service() {
        let (client, path) = launch("service_ignore_stdin", 1500);
        ready(&client);
        let live = observation::Live::open(pids(&path, 1)[0]);
        assert_eq!(
            fill_pipe(&client, Arc::new(AtomicBool::new(false))),
            Failure::RequestTimeout
        );
        stopped(&client, Some(Failure::RequestTimeout));
        live.stopped();
        let (client, path) = launch("service_ignore_stdin", 1500);
        ready(&client);
        let live = observation::Live::open(pids(&path, 1)[0]);
        let flag = Arc::new(AtomicBool::new(false));
        let signal = flag.clone();
        let cancel = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(30));
            signal.store(true, Ordering::Release);
        });
        assert_eq!(fill_pipe(&client, flag), Failure::Cancelled);
        cancel.join().unwrap();
        // Cancellation before a queued write starts can be refused without
        // touching the service; explicit retirement remains available either way.
        client.stop();
        phase(&client, Phase::Stopped);
        assert!(client.snapshot().retired);
        live.stopped();
    }

    #[test]
    fn oversized_request_is_local_rejection_and_last_drop_retires_active_tree() {
        let (client, path) = launch("service_live", 1500);
        ready(&client);
        assert_eq!(
            client.submit(
                NativeRequest::StartVideo {
                    scan_id: "scan".into(),
                    provider_id: "generic".into(),
                    source_ref: "x".repeat(70_000)
                },
                Arc::new(AtomicBool::new(false))
            ),
            Err(Failure::RequestLimit)
        );
        send(&client, status());
        event(&client, "source_status");
        let owner = observation::Live::open(pids(&path, 1)[0]);
        send(
            &client,
            NativeRequest::StartVideo {
                scan_id: "scan".into(),
                provider_id: "generic".into(),
                source_ref: "https://example.test/x".into(),
            },
        );
        event(&client, "source_preparing");
        let worker = observation::Live::open(pids(&path, 2)[1]);
        drop(client);
        owner.stopped();
        worker.stopped();
    }
}
