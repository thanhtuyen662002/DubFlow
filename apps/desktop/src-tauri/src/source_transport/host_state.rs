//! Managed source state can always be installed while local-file mode starts.
//! Configuration comes only from the trusted entrypoint, never IPC arguments.
use crate::source_transport::service::{
    AdmissionWindow, Client, Failure, LaunchPin, NativeRequest, Observation, Snapshot, Submission,
};
use std::sync::{atomic::AtomicBool, Arc, Mutex};

/// Closed reasons prevent bootstrap errors containing private paths/pins from
/// being copied into the webview. Every unavailable operation uses one static,
/// bounded actionable message and performs no launch, owner acquisition or retry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UnavailableReason {
    MissingTrustedRelease,
    InvalidTrustedRelease,
    MissingAdmissionWindow,
}
impl UnavailableReason {
    pub fn message(self) -> &'static str {
        match self {
            Self::MissingTrustedRelease => "SOURCE_SETUP_MISSING: Nguồn trực tuyến chưa sẵn sàng. Hãy kiểm tra bản cài đặt DubFlow; tệp cục bộ vẫn dùng được.",
            Self::InvalidTrustedRelease => "SOURCE_SETUP_INVALID: Cấu hình nguồn trực tuyến không hợp lệ. Hãy sửa bản cài đặt DubFlow; tệp cục bộ vẫn dùng được.",
            Self::MissingAdmissionWindow => "SOURCE_SETUP_POLICY_MISSING: Thiết lập nguồn trực tuyến chưa hoàn tất. Hãy kiểm tra cấu hình DubFlow; tệp cục bộ vẫn dùng được.",
        }
    }
}
enum Configuration {
    Available {
        pin: LaunchPin,
        admission: AdmissionWindow,
    },
    Unavailable(UnavailableReason),
}
pub struct SourceServiceHost {
    configuration: Configuration,
    client: Mutex<Option<Client>>,
}
impl Default for SourceServiceHost {
    fn default() -> Self {
        Self::unavailable(UnavailableReason::MissingTrustedRelease)
    }
}
impl SourceServiceHost {
    /// Retains the exact trusted pin/window; syntactically malformed pins become
    /// unavailable without filesystem probing or an attempted native launch.
    /// Complete manifest/containment/ownership validation remains native.
    pub fn new(pin: LaunchPin, admission: AdmissionWindow) -> Self {
        if pin.command().is_err() {
            return Self::unavailable(UnavailableReason::InvalidTrustedRelease);
        }
        Self {
            configuration: Configuration::Available { pin, admission },
            client: Mutex::new(None),
        }
    }
    pub fn unavailable(reason: UnavailableReason) -> Self {
        Self {
            configuration: Configuration::Unavailable(reason),
            client: Mutex::new(None),
        }
    }
    /// Optional bootstrap parts are host-only. No pin/budget is inferred and no
    /// native process starts merely because managed state is constructed.
    pub fn from_trusted_parts(pin: Option<LaunchPin>, admission: Option<AdmissionWindow>) -> Self {
        match (pin, admission) {
            (None, _) => Self::unavailable(UnavailableReason::MissingTrustedRelease),
            (Some(_), None) => Self::unavailable(UnavailableReason::MissingAdmissionWindow),
            (Some(pin), Some(admission)) => Self::new(pin, admission),
        }
    }
    fn configured(&self) -> Result<(&LaunchPin, AdmissionWindow), String> {
        match &self.configuration {
            Configuration::Available { pin, admission } => Ok((pin, *admission)),
            Configuration::Unavailable(reason) => Err(reason.message().to_owned()),
        }
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
    // Private injection point tests the real gate without permitting production
    // callers to substitute workers, pins, budgets or source adapters.
    fn start_with(
        &self,
        launch: impl FnOnce(LaunchPin, AdmissionWindow) -> Result<Client, Failure>,
    ) -> Result<Snapshot, String> {
        let (pin, admission) = self.configured()?;
        let mut client = self
            .client
            .lock()
            .map_err(|_| "SOURCE_HOST_LOCK".to_owned())?;
        if client.is_none() {
            *client = Some(launch(pin.clone(), admission).map_err(service_error)?);
        }
        client
            .as_ref()
            .map(Client::snapshot)
            .ok_or_else(|| "SOURCE_NOT_STARTED".into())
    }
    pub fn state(&self) -> Result<Snapshot, String> {
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
    fn configured_route_keeps_pins_one_live_client_and_existing_event_send_stop_behavior() {
        let value = pin();
        let root = value.root.clone();
        let data = value.data_root.clone();
        let hash = value.manifest_sha256.clone();
        let host = SourceServiceHost::new(value, window());
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
