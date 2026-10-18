//! Native-only deferred configuration keeps optional source I/O off UI startup.
use crate::source_transport::service::{
    AdmissionWindow, Client, Failure, LaunchPin, NativeRequest, Observation, Phase, Snapshot,
    Submission,
};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UnavailableReason {
    MissingTrustedRelease,
    InvalidTrustedRelease,
    MissingAdmissionWindow,
    BootstrapCancelled,
}
impl UnavailableReason {
    pub fn message(self) -> &'static str {
        match self {
            Self::MissingTrustedRelease => "SOURCE_SETUP_MISSING: Nguồn trực tuyến chưa sẵn sàng. Hãy kiểm tra bản cài đặt DubFlow; tệp cục bộ vẫn dùng được.",
            Self::InvalidTrustedRelease => "SOURCE_SETUP_INVALID: Cấu hình nguồn trực tuyến không hợp lệ. Hãy sửa bản cài đặt DubFlow; tệp cục bộ vẫn dùng được.",
            Self::MissingAdmissionWindow => "SOURCE_SETUP_POLICY_MISSING: Thiết lập nguồn trực tuyến chưa hoàn tất. Hãy kiểm tra cấu hình DubFlow; tệp cục bộ vẫn dùng được.",
            Self::BootstrapCancelled => "SOURCE_SETUP_CANCELLED: Đã hủy chuẩn bị nguồn trực tuyến. Tệp cục bộ vẫn dùng được.",
        }
    }
}
type Bootstrap =
    Box<dyn FnOnce() -> Result<(LaunchPin, AdmissionWindow), UnavailableReason> + Send>;
enum Configuration {
    Available {
        pin: LaunchPin,
        admission: AdmissionWindow,
    },
    Unavailable(UnavailableReason),
    Deferred(Option<Bootstrap>),
    Initializing,
}
#[derive(Clone)]
pub struct SourceServiceHost {
    configuration: Arc<Mutex<Configuration>>,
    client: Arc<Mutex<Option<Client>>>,
    bootstrap_cancelled: Arc<AtomicBool>,
}
impl Default for SourceServiceHost {
    fn default() -> Self {
        Self::unavailable(UnavailableReason::MissingTrustedRelease)
    }
}
impl SourceServiceHost {
    fn configuration(value: Configuration) -> Self {
        Self {
            configuration: Arc::new(Mutex::new(value)),
            client: Arc::new(Mutex::new(None)),
            bootstrap_cancelled: Arc::new(AtomicBool::new(false)),
        }
    }
    pub fn new(pin: LaunchPin, admission: AdmissionWindow) -> Self {
        if pin.command().is_err() {
            return Self::unavailable(UnavailableReason::InvalidTrustedRelease);
        }
        Self::configuration(Configuration::Available { pin, admission })
    }
    pub fn unavailable(reason: UnavailableReason) -> Self {
        Self::configuration(Configuration::Unavailable(reason))
    }
    pub fn from_trusted_parts(pin: Option<LaunchPin>, admission: Option<AdmissionWindow>) -> Self {
        match (pin, admission) {
            (None, _) => Self::unavailable(UnavailableReason::MissingTrustedRelease),
            (Some(_), None) => Self::unavailable(UnavailableReason::MissingAdmissionWindow),
            (Some(pin), Some(admission)) => Self::new(pin, admission),
        }
    }
    /// Saves a native closure without calling it. No filesystem access, process
    /// launch or thread is required merely to construct/manage this state.
    pub fn deferred(
        bootstrap: impl FnOnce() -> Result<(LaunchPin, AdmissionWindow), UnavailableReason>
            + Send
            + 'static,
    ) -> Self {
        Self::configuration(Configuration::Deferred(Some(Box::new(bootstrap))))
    }
    fn configured(&self) -> Result<(LaunchPin, AdmissionWindow), String> {
        match &*self.configuration.lock().map_err(|_| "SOURCE_HOST_LOCK")? {
            Configuration::Available { pin, admission } => Ok((pin.clone(), *admission)),
            Configuration::Unavailable(reason) => Err(reason.message().to_owned()),
            _ => Err("SOURCE_NOT_STARTED".to_owned()),
        }
    }
    /// The async Tauri entry executes this on a blocking worker. Do not hold
    /// either mutex while doing filesystem I/O: state/stop remain responsive.
    fn prepare_configuration(&self) -> Result<bool, String> {
        let setup = {
            let mut config = self.configuration.lock().map_err(|_| "SOURCE_HOST_LOCK")?;
            match &mut *config {
                Configuration::Deferred(slot) => {
                    let value = slot.take().ok_or("SOURCE_HOST_SETUP")?;
                    *config = Configuration::Initializing;
                    value
                }
                Configuration::Initializing => return Ok(false),
                _ => return Ok(true),
            }
        };
        let result = setup();
        let mut config = self.configuration.lock().map_err(|_| "SOURCE_HOST_LOCK")?;
        *config = if self.bootstrap_cancelled.load(Ordering::Acquire) {
            Configuration::Unavailable(UnavailableReason::BootstrapCancelled)
        } else {
            match result {
                Ok((pin, admission)) if pin.command().is_ok() => {
                    Configuration::Available { pin, admission }
                }
                Ok(_) => Configuration::Unavailable(UnavailableReason::InvalidTrustedRelease),
                Err(reason) => Configuration::Unavailable(reason),
            }
        };
        Ok(true)
    }
    pub(super) fn client(&self) -> Result<Client, String> {
        self.configured()?;
        self.client
            .lock()
            .map_err(|_| "SOURCE_HOST_LOCK".to_owned())?
            .clone()
            .ok_or_else(|| "SOURCE_NOT_STARTED".into())
    }
    pub fn start(&self) -> Result<Snapshot, String> {
        self.start_with(Client::launch)
    }
    fn start_with(
        &self,
        launch: impl FnOnce(LaunchPin, AdmissionWindow) -> Result<Client, Failure>,
    ) -> Result<Snapshot, String> {
        if !self.prepare_configuration()? {
            return Ok(Snapshot {
                phase: Phase::Admitting,
                failure: None,
                retired: false,
            });
        }
        let (pin, admission) = self.configured()?;
        let mut client = self
            .client
            .lock()
            .map_err(|_| "SOURCE_HOST_LOCK".to_owned())?;
        if client.is_none() {
            if self.bootstrap_cancelled.load(Ordering::Acquire) {
                return Err(UnavailableReason::BootstrapCancelled.message().into());
            }
            *client = Some(launch(pin, admission).map_err(service_error)?);
        }
        client
            .as_ref()
            .map(Client::snapshot)
            .ok_or_else(|| "SOURCE_NOT_STARTED".into())
    }
    pub fn state(&self) -> Result<Snapshot, String> {
        if matches!(
            &*self.configuration.lock().map_err(|_| "SOURCE_HOST_LOCK")?,
            Configuration::Initializing
        ) {
            return Ok(Snapshot {
                phase: Phase::Admitting,
                failure: None,
                retired: false,
            });
        }
        Ok(self.client()?.snapshot())
    }
    pub fn events(&self) -> Result<Vec<Observation>, String> {
        Ok(self.client()?.drain())
    }
    pub fn send(
        &self,
        request: NativeRequest,
        cancelled: Arc<AtomicBool>,
    ) -> Result<Submission, String> {
        self.client()?
            .submit(request, cancelled)
            .map_err(service_error)
    }
    pub fn stop(&self) -> Result<Snapshot, String> {
        {
            let mut config = self.configuration.lock().map_err(|_| "SOURCE_HOST_LOCK")?;
            match &*config {
                Configuration::Deferred(_) | Configuration::Initializing => {
                    self.bootstrap_cancelled.store(true, Ordering::Release);
                    *config = Configuration::Unavailable(UnavailableReason::BootstrapCancelled);
                    return Ok(Snapshot {
                        phase: Phase::Stopped,
                        failure: Some(Failure::Cancelled),
                        retired: true,
                    });
                }
                Configuration::Unavailable(reason) => return Err(reason.message().into()),
                _ => self.bootstrap_cancelled.store(true, Ordering::Release),
            }
        }
        let client = self.client()?;
        client.stop();
        Ok(client.snapshot())
    }
}
pub(super) fn service_error(value: Failure) -> String {
    format!("SOURCE_SERVICE_{value:?}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_transport::{service::Phase, Limits};
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    fn pin() -> LaunchPin {
        let unique = format!(
            "source-host-state-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        LaunchPin {
            root: std::env::temp_dir().join(&unique).join("private-release"),
            data_root: std::env::temp_dir().join(unique).join("private-data"),
            manifest_sha256: "b".repeat(64),
        }
    }
    fn window() -> AdmissionWindow {
        AdmissionWindow::new(Duration::from_millis(1500)).unwrap()
    }
    #[test]
    fn deferred_failure_or_malformed_pin_is_lazy_and_never_retried_or_launched() {
        for malformed in [false, true] {
            let calls = Arc::new(AtomicUsize::new(0));
            let seen = calls.clone();
            let host = SourceServiceHost::deferred(move || {
                seen.fetch_add(1, Ordering::SeqCst);
                if malformed {
                    let mut value = pin();
                    value.manifest_sha256 = "invalid".into();
                    Ok((value, window()))
                } else {
                    Err(UnavailableReason::MissingTrustedRelease)
                }
            });
            assert_eq!(calls.load(Ordering::SeqCst), 0);
            assert_eq!(host.state().unwrap_err(), "SOURCE_NOT_STARTED");
            assert_eq!(calls.load(Ordering::SeqCst), 0);
            for _ in 0..3 {
                assert!(host
                    .start_with(|_, _| panic!("invalid setup launched"))
                    .is_err());
            }
            assert_eq!(calls.load(Ordering::SeqCst), 1);
            assert!(host.client.lock().unwrap().is_none());
        }
    }
    #[test]
    fn blocked_setup_does_not_hold_state_or_stop_and_cancel_prevents_late_launch() {
        let (entered_send, entered_recv) = std::sync::mpsc::channel();
        let (release_send, release_recv) = std::sync::mpsc::channel();
        let host = SourceServiceHost::deferred(move || {
            entered_send.send(()).unwrap();
            release_recv.recv_timeout(Duration::from_secs(5)).unwrap();
            Ok((pin(), window()))
        });
        let background = host.clone();
        let setup = std::thread::spawn(move || {
            background.start_with(|_, _| panic!("cancelled setup launched"))
        });
        entered_recv.recv_timeout(Duration::from_secs(2)).unwrap();
        let start = Instant::now();
        assert_eq!(host.state().unwrap().phase, Phase::Admitting);
        assert_eq!(
            host.start_with(|_, _| panic!("duplicate setup launched"))
                .unwrap()
                .phase,
            Phase::Admitting
        );
        let stopped = host.stop().unwrap();
        assert_eq!(stopped.phase, Phase::Stopped);
        assert!(stopped.retired);
        assert!(start.elapsed() < Duration::from_secs(1));
        release_send.send(()).unwrap();
        assert_eq!(
            setup.join().unwrap().unwrap_err(),
            UnavailableReason::BootstrapCancelled.message()
        );
        assert!(host.client.lock().unwrap().is_none());
        assert!(host
            .start_with(|_, _| panic!("cancelled bootstrap retried"))
            .is_err());
    }
    fn request() -> NativeRequest {
        NativeRequest::Status {
            scan_id: "scan".into(),
        }
    }
    fn assert_unavailable(host: SourceServiceHost, expected: UnavailableReason) {
        let attempts = AtomicUsize::new(0);
        for _ in 0..3 {
            let start = Instant::now();
            assert_eq!(host.start().unwrap_err(), expected.message());
            assert_eq!(
                host.start_with(|_, _| {
                    attempts.fetch_add(1, Ordering::SeqCst);
                    panic!("unavailable host must never launch")
                })
                .unwrap_err(),
                expected.message()
            );
            assert_eq!(host.state().unwrap_err(), expected.message());
            assert_eq!(host.events().err().unwrap(), expected.message());
            assert_eq!(
                host.send(request(), Arc::new(AtomicBool::new(false)))
                    .unwrap_err(),
                expected.message()
            );
            assert_eq!(host.stop().unwrap_err(), expected.message());
            assert!(start.elapsed() < Duration::from_secs(1));
        }
        assert_eq!(attempts.load(Ordering::SeqCst), 0);
        assert!(host.client.lock().unwrap().is_none());
        assert!(expected.message().len() < 512);
        assert!(expected.message().contains("tệp cục bộ vẫn dùng được"));
        assert!(!expected.message().contains("private-release"));
        assert!(!expected.message().contains(&"b".repeat(64)));
    }
    #[test]
    fn unavailable_and_missing_parts_gate_every_operation_without_launch_or_retry() {
        for reason in [
            UnavailableReason::MissingTrustedRelease,
            UnavailableReason::InvalidTrustedRelease,
            UnavailableReason::MissingAdmissionWindow,
        ] {
            assert_unavailable(SourceServiceHost::unavailable(reason), reason);
        }
        assert_unavailable(
            SourceServiceHost::default(),
            UnavailableReason::MissingTrustedRelease,
        );
        assert_unavailable(
            SourceServiceHost::from_trusted_parts(None, Some(window())),
            UnavailableReason::MissingTrustedRelease,
        );
        assert_unavailable(
            SourceServiceHost::from_trusted_parts(None, None),
            UnavailableReason::MissingTrustedRelease,
        );
        assert_unavailable(
            SourceServiceHost::from_trusted_parts(Some(pin()), None),
            UnavailableReason::MissingAdmissionWindow,
        );
    }
    #[test]
    fn malformed_trusted_parts_fail_closed_without_creating_a_data_root() {
        for mode in ["root", "data", "hash"] {
            let mut value = pin();
            let data = value.data_root.clone();
            match mode {
                "root" => value.root = PathBuf::from("private-relative-root"),
                "data" => value.data_root = PathBuf::from("private-relative-data"),
                _ => value.manifest_sha256 = "private-invalid-hash".into(),
            }
            assert!(!data.exists());
            assert_unavailable(
                SourceServiceHost::new(value, window()),
                UnavailableReason::InvalidTrustedRelease,
            );
            assert!(!data.exists());
        }
    }
    fn fixture() -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "source-host-fixture-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&dir).unwrap();
        let source = dir.join("fixture.rs");
        std::fs::write(
            &source,
            include_str!("../../../../../crates/job-supervisor/app/tests/source_intake/fixture.rs"),
        )
        .unwrap();
        let binary = dir.join(if cfg!(windows) {
            "fixture.exe"
        } else {
            "fixture"
        });
        let flags = std::env::var("CARGO_ENCODED_RUSTFLAGS").unwrap_or_default();
        let result = Command::new("rustc")
            .args(["--edition=2021", "-o"])
            .arg(&binary)
            .arg(source)
            .args(flags.split('\u{1f}').filter(|arg| !arg.is_empty()))
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        binary
    }
    fn await_phase(host: &SourceServiceHost, phase: Phase) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while host.state().unwrap().phase != phase {
            assert!(Instant::now() < deadline, "host phase did not advance");
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    #[test]
    fn deferred_route_keeps_pins_one_live_client_and_existing_event_send_stop_behavior() {
        let value = pin();
        let root = value.root.clone();
        let data = value.data_root.clone();
        let hash = value.manifest_sha256.clone();
        let bootstrap_calls = Arc::new(AtomicUsize::new(0));
        let observed = bootstrap_calls.clone();
        let host = SourceServiceHost::deferred(move || {
            observed.fetch_add(1, Ordering::SeqCst);
            Ok((value, window()))
        });
        assert_eq!(bootstrap_calls.load(Ordering::SeqCst), 0);
        assert_eq!(host.state().unwrap_err(), "SOURCE_NOT_STARTED");
        let binary = fixture();
        let pid = binary.with_file_name("pids");
        let attempts = AtomicUsize::new(0);
        host.start_with(|pin, admission| {
            assert_eq!(pin.root, root);
            assert_eq!(pin.data_root, data);
            assert_eq!(pin.manifest_sha256, hash);
            attempts.fetch_add(1, Ordering::SeqCst);
            let mut command = Command::new(binary);
            command.arg("service_live").arg(pid);
            Client::spawn(command, admission, Limits::default())
        })
        .unwrap();
        await_phase(&host, Phase::Ready);
        host.start_with(|_, _| panic!("managed client must be reused"))
            .unwrap();
        assert_eq!(attempts.load(Ordering::SeqCst), 1);
        assert_eq!(bootstrap_calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            host.send(request(), Arc::new(AtomicBool::new(false)))
                .unwrap()
                .delivery,
            "submitted"
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut status = false;
        while !status {
            status = host
                .events()
                .unwrap()
                .iter()
                .any(|event| event.value["event"] == "source_status");
            assert!(Instant::now() < deadline, "native fixture status missing");
            std::thread::sleep(Duration::from_millis(2));
        }
        host.stop().unwrap();
        await_phase(&host, Phase::Stopped);
        assert!(host.state().unwrap().retired);
        host.start_with(|_, _| panic!("terminal client must not auto-restart"))
            .unwrap();
        assert_eq!(host.state().unwrap().phase, Phase::Stopped);
    }
    #[test]
    fn configured_route_uses_the_supplied_admission_window() {
        let binary = fixture();
        let pid = binary.with_file_name("pids");
        let host = SourceServiceHost::new(
            pin(),
            AdmissionWindow::new(Duration::from_millis(40)).unwrap(),
        );
        host.start_with(|pin, admission| {
            assert_eq!(pin.manifest_sha256, "b".repeat(64));
            let mut command = Command::new(binary);
            command.arg("service_slow").arg(pid);
            Client::spawn(command, admission, Limits::default())
        })
        .unwrap();
        await_phase(&host, Phase::Stopped);
        let snapshot = host.state().unwrap();
        assert!(snapshot.retired);
        assert_eq!(snapshot.failure, Some(Failure::AdmissionTimeout));
    }
}
