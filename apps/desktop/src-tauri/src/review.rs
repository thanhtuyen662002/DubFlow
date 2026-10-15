//! Optional read-only review IPC. Every audio buffer is bound to a native
//! completed-export session; the webview supplies identities, never paths.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc, Mutex,
};
use std::time::{Duration, Instant};

const JSON_LIMIT: usize = 64 * 1024 * 1024;
const AUDIO_LIMIT: usize = 64 * 1024 * 1024;
const PIN_LIMIT: usize = 64 * 1024;
const ERROR_LIMIT: usize = 4096;
const REQUEST_LIMIT: usize = 64;
const ACTIVE_LIMIT: usize = 4;
const SESSION_LIMIT: usize = 8;
const TOMBSTONE_TTL: Duration = Duration::from_secs(120);
const SESSION_TTL: Duration = Duration::from_secs(20 * 60);

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportPin {
    schema_version: u32,
    kind: String,
    job_id: String,
    manifest_path: PathBuf,
    manifest_sha256: String,
    manifest_size_bytes: u64,
    attempt: u32,
}

struct RequestSlot {
    cancelled: Arc<AtomicBool>,
    active: bool,
    changed: Instant,
}
#[derive(Clone, PartialEq)]
enum SessionBinding {
    LegacyOriginal,
    Uninitialized,
    Current(CurrentToken),
}
struct Session {
    pin: ExportPin,
    created: Instant,
    binding: SessionBinding,
}
#[derive(Default)]
struct Registry {
    requests: HashMap<String, RequestSlot>,
    sessions: HashMap<String, Session>,
}

#[derive(Clone, Default)]
pub struct ReviewHost {
    registry: Arc<Mutex<Registry>>,
}

fn request_id(value: &str) -> Result<(), String> {
    if !(16..=64).contains(&value.len())
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        return Err("Mã yêu cầu review không hợp lệ".into());
    }
    Ok(())
}

fn identity(value: &str, limit: usize) -> bool {
    !value.is_empty() && value.len() <= limit && !value.chars().any(char::is_control)
}

fn job_id(value: &str) -> Result<(), String> {
    if !identity(value, 128)
        || !value
            .bytes()
            .next()
            .is_some_and(|b| b.is_ascii_alphanumeric())
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
    {
        return Err("Mã video review không hợp lệ".into());
    }
    Ok(())
}

impl Registry {
    fn prune(&mut self) {
        self.requests
            .retain(|_, request| request.active || request.changed.elapsed() < TOMBSTONE_TTL);
        self.sessions
            .retain(|_, session| session.created.elapsed() < SESSION_TTL);
    }
}

struct RequestGuard {
    host: ReviewHost,
    id: String,
    cancelled: Arc<AtomicBool>,
}
impl Drop for RequestGuard {
    fn drop(&mut self) {
        if let Ok(mut registry) = self.host.registry.lock() {
            if let Some(slot) = registry.requests.get_mut(&self.id) {
                slot.active = false;
                slot.changed = Instant::now();
            }
        }
    }
}

impl ReviewHost {
    fn begin(&self, id: &str) -> Result<RequestGuard, String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        if registry.requests.contains_key(id) {
            return Err("Yêu cầu review đã kết thúc hoặc bị hủy; hãy tải lại".into());
        }
        if registry.requests.len() >= REQUEST_LIMIT
            || registry
                .requests
                .values()
                .filter(|slot| slot.active)
                .count()
                >= ACTIVE_LIMIT
        {
            return Err("Review đang bận; hãy thử lại sau khi tác vụ hiện tại kết thúc".into());
        }
        let cancelled = Arc::new(AtomicBool::new(false));
        registry.requests.insert(
            id.to_owned(),
            RequestSlot {
                cancelled: cancelled.clone(),
                active: true,
                changed: Instant::now(),
            },
        );
        Ok(RequestGuard {
            host: self.clone(),
            id: id.to_owned(),
            cancelled,
        })
    }

    fn cancel(&self, id: &str) -> Result<(), String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        registry.sessions.remove(id);
        if let Some(slot) = registry.requests.get_mut(id) {
            slot.cancelled.store(true, Ordering::SeqCst);
            slot.changed = Instant::now();
        } else {
            if registry.requests.len() >= REQUEST_LIMIT {
                return Err("Hàng đợi hủy review đã đầy".into());
            }
            // Preserve an abort that reaches the host before its async request.
            registry.requests.insert(
                id.to_owned(),
                RequestSlot {
                    cancelled: Arc::new(AtomicBool::new(true)),
                    active: false,
                    changed: Instant::now(),
                },
            );
        }
        Ok(())
    }

    fn save_session(&self, request: &RequestGuard, pin: ExportPin) -> Result<(), String> {
        self.save_bound_session(request, pin, SessionBinding::LegacyOriginal)
    }

    fn save_bound_session(
        &self,
        request: &RequestGuard,
        pin: ExportPin,
        binding: SessionBinding,
    ) -> Result<(), String> {
        if let SessionBinding::Current(token) = &binding {
            if token.job_id != pin.job_id || token.original_manifest_sha256 != pin.manifest_sha256 {
                return Err("Token review không khớp bản xuất đã xác thực".into());
            }
        }
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        if request.cancelled.load(Ordering::SeqCst) {
            return Err("Review đã bị hủy".into());
        }
        if registry.sessions.len() >= SESSION_LIMIT {
            return Err("Có quá nhiều video review đang mở; hãy đóng video cũ".into());
        }
        registry.sessions.insert(
            request.id.clone(),
            Session {
                pin,
                created: Instant::now(),
                binding,
            },
        );
        Ok(())
    }

    fn session(&self, id: &str, job: &str) -> Result<ExportPin, String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        match registry.sessions.get(id) {
            Some(session)
                if session.pin.job_id == job
                    && !matches!(&session.binding, SessionBinding::Current(_)) =>
            {
                Ok(session.pin.clone())
            }
            _ => Err("Review đã hết hạn hoặc đổi video; hãy tải lại lời thoại".into()),
        }
    }

    fn requires_uninitialized(&self, id: &str, job: &str) -> Result<bool, String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        match registry.sessions.get(id) {
            Some(session) if session.pin.job_id == job => {
                Ok(session.binding == SessionBinding::Uninitialized)
            }
            _ => Err("Review đã hết hạn; hãy tải lại lời thoại".into()),
        }
    }

    fn current_session(&self, id: &str, job: &str) -> Result<(ExportPin, CurrentToken), String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        match registry.sessions.get(id) {
            Some(session) if session.pin.job_id == job => match &session.binding {
                SessionBinding::Current(token) => Ok((session.pin.clone(), token.clone())),
                _ => Err("Phiên nghe hiện hành không còn hợp lệ; hãy tải lại".into()),
            },
            _ => Err("Review đã hết hạn hoặc đổi video; hãy tải lại".into()),
        }
    }
}

fn capture(mut reader: impl Read, limit: usize) -> Result<Vec<u8>, String> {
    let mut result = Vec::new();
    reader
        .by_ref()
        .take(limit as u64 + 1)
        .read_to_end(&mut result)
        .map_err(|_| "Không đọc được phản hồi review")?;
    if result.len() > limit {
        return Err("Phản hồi review vượt giới hạn".into());
    }
    Ok(result)
}

/// Readers drain both pipes concurrently. A finite deadline covers process
/// completion AND pipe EOF; no unbounded wait/join runs on the UI thread.
fn run_bounded(
    mut command: Command,
    limit: usize,
    timeout: Duration,
    cancelled: &AtomicBool,
) -> Result<Vec<u8>, String> {
    if cancelled.load(Ordering::SeqCst) {
        return Err("Review đã bị hủy".into());
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(super::CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|_| "Không khởi động được bộ đọc review của DubFlow")?;
    let stdout = child.stdout.take().ok_or("Bộ đọc review thiếu stdout")?;
    let stderr = child.stderr.take().ok_or("Bộ đọc review thiếu stderr")?;
    let (sender, receiver) = mpsc::channel();
    let error_sender = sender.clone();
    std::thread::spawn(move || {
        let _ = sender.send((true, capture(stdout, limit)));
    });
    std::thread::spawn(move || {
        let _ = error_sender.send((false, capture(stderr, ERROR_LIMIT)));
    });
    let deadline = Instant::now() + timeout;
    let mut output = None;
    let mut errors = None;
    let mut status = None;
    let mut reader_error = None;
    let outcome = loop {
        if cancelled.load(Ordering::SeqCst) {
            break Err("Review đã bị hủy".into());
        }
        if Instant::now() >= deadline {
            break Err("Review đọc quá lâu; hãy tải lại hoặc kiểm tra ổ đĩa".into());
        }
        while let Ok((is_output, bytes)) = receiver.try_recv() {
            match bytes {
                Ok(bytes) => {
                    if is_output {
                        output = Some(bytes);
                    } else {
                        errors = Some(bytes);
                    }
                }
                Err(error) => {
                    reader_error = Some(error);
                    break;
                }
            }
        }
        // A reader overflow is always an error, even if the process exits zero.
        if let Some(error) = reader_error.take() {
            break Err(error);
        }
        if status.is_none() {
            match child.try_wait() {
                Ok(value) => status = value,
                Err(_) => break Err("Không đọc được trạng thái bộ đọc review".into()),
            }
        }
        if let (Some(status), Some(_), Some(_)) = (&status, &output, &errors) {
            if !status.success() {
                break Err("Không xác thực được lời thoại hoặc âm thanh; hãy tải lại và kiểm tra đầu ra của video".into());
            }
            break Ok(output.take().unwrap_or_default());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    if status.is_none() {
        let _ = child.kill();
        // Reap a killed owned reader without extending the deadline indefinitely.
        let reap_deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < reap_deadline {
            match child.try_wait() {
                Ok(Some(_)) | Err(_) => break,
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            }
        }
    }
    outcome
}

fn owned_file(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let path = root.join(relative);
    if !path.is_file() {
        return Err("Phiên bản này thiếu bộ đọc review; bản xuất video vẫn dùng được".into());
    }
    Ok(path)
}

fn data_root() -> Result<PathBuf, String> {
    let root =
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("Không tìm thấy dữ liệu DubFlow")?)
            .join("DubFlow");
    if !root.is_absolute() || !root.is_dir() {
        return Err("Chưa có dữ liệu video hoàn tất để review".into());
    }
    // Unlike control_root(), this read-only route never creates a directory.
    Ok(root)
}

fn trusted_pin(
    root: &Path,
    data: &Path,
    job: &str,
    cancelled: &AtomicBool,
) -> Result<ExportPin, String> {
    let mut command = Command::new(owned_file(root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-pin")
        .arg("--data-root")
        .arg(data)
        .arg("--job-id")
        .arg(job);
    let bytes = run_bounded(command, PIN_LIMIT, Duration::from_secs(20), cancelled)?;
    let pin: ExportPin =
        serde_json::from_slice(&bytes).map_err(|_| "Bản xuất review chưa được hỗ trợ")?;
    if pin.schema_version != 1
        || pin.kind != "review_export_pin"
        || pin.job_id != job
        || !pin.manifest_path.is_absolute()
        || pin.manifest_sha256.len() != 71
        || !pin.manifest_sha256.starts_with("sha256:")
        || !pin.manifest_sha256[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || pin.manifest_size_bytes == 0
        || pin.manifest_size_bytes > JSON_LIMIT as u64
        || !(1..=3).contains(&pin.attempt)
    {
        return Err("Bản xuất review không khớp video hoàn tất".into());
    }
    Ok(pin)
}

fn python_command(root: &Path, pin: &ExportPin, action: &str) -> Result<Command, String> {
    let mut command = Command::new(owned_file(root, "runtime/python.exe")?);
    command
        .args(["-I", "-S", "-B"])
        .arg(owned_file(root, "app/engine/dubflow/review/cli.py")?)
        .arg(action)
        .arg("--manifest")
        .arg(&pin.manifest_path)
        .arg("--job-id")
        .arg(&pin.job_id)
        .arg("--manifest-sha256")
        .arg(&pin.manifest_sha256);
    Ok(command)
}

fn snapshot(host: ReviewHost, job: String, id: String) -> Result<Value, String> {
    job_id(&job)?;
    let request = host.begin(&id)?;
    let root = super::version_root()?;
    let data = data_root()?;
    let pin = trusted_pin(&root, &data, &job, &request.cancelled)?;
    let bytes = run_bounded(
        python_command(&root, &pin, "snapshot")?,
        JSON_LIMIT,
        Duration::from_secs(30),
        &request.cancelled,
    )?;
    let snapshot: Value =
        serde_json::from_slice(&bytes).map_err(|_| "Nội dung review không hợp lệ")?;
    if snapshot.get("schema_version").and_then(Value::as_u64) != Some(2)
        || snapshot.get("job_id").and_then(Value::as_str) != Some(job.as_str())
        || snapshot.get("revision").and_then(Value::as_u64) != Some(0)
    {
        return Err("Nội dung review không khớp video hoặc phiên bản".into());
    }
    host.save_session(&request, pin)?;
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_session","session_id":id,"snapshot":snapshot}),
    )
}

#[derive(Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CurrentToken {
    job_id: String,
    revision: String,
    snapshot_sha256: String,
    graph_sha256: String,
    original_manifest_sha256: String,
}

fn is_sha(value: &str) -> bool {
    value.len() == 71
        && value.starts_with("sha256:")
        && value[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn validate_current_state(
    packet: Value,
    job: &str,
    pin: &ExportPin,
) -> Result<Option<Value>, String> {
    let error = || "Phiên bản review hiện hành không hợp lệ; hãy tải lại".to_owned();
    let object = packet.as_object().ok_or_else(error)?;
    let fields = ["schema_version", "kind", "job_id", "status", "snapshot"];
    if object.len() != fields.len()
        || fields.iter().any(|k| !object.contains_key(*k))
        || packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_native_desktop_state"
        || packet["job_id"] != job
    {
        return Err(error());
    }
    if packet["status"] == "original_only" && packet["snapshot"].is_null() {
        return Ok(None);
    }
    let view = &packet["snapshot"];
    let fields = [
        "schema_version",
        "kind",
        "job_id",
        "current_token",
        "mode",
        "time_base",
        "findings",
        "last_plan",
        "correction_ready",
    ];
    if packet["status"] != "current"
        || view
            .as_object()
            .is_none_or(|v| v.len() != fields.len() || fields.iter().any(|k| !v.contains_key(*k)))
        || view["schema_version"].as_u64() != Some(3)
        || view["kind"] != "review_current_snapshot"
        || view["job_id"] != job
        || !matches!(view["mode"].as_str(), Some("b1" | "b2"))
        || view["correction_ready"] != false
    {
        return Err(error());
    }
    let token: CurrentToken =
        serde_json::from_value(view["current_token"].clone()).map_err(|_| error())?;
    let revision = token.revision.parse::<u64>().map_err(|_| error())?;
    if token.job_id != job
        || revision > i64::MAX as u64
        || revision.to_string() != token.revision
        || !is_sha(&token.snapshot_sha256)
        || !is_sha(&token.graph_sha256)
        || !is_sha(&token.original_manifest_sha256)
        || token.original_manifest_sha256 != pin.manifest_sha256
    {
        return Err(error());
    }
    Ok(Some(view.clone()))
}

fn current_state(
    root: &Path,
    data: &Path,
    job: &str,
    pin: &ExportPin,
    cancelled: &AtomicBool,
) -> Result<Option<Value>, String> {
    let mut command = Command::new(owned_file(root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-desktop-state")
        .arg("--data-root")
        .arg(data)
        .arg("--job-id")
        .arg(job);
    let bytes = run_bounded(command, JSON_LIMIT, Duration::from_secs(740), cancelled)?;
    let packet = serde_json::from_slice(&bytes)
        .map_err(|_| "Không đọc được phiên bản review hiện hành".to_owned())?;
    validate_current_state(packet, job, pin)
}

fn current_snapshot(host: ReviewHost, job: String, id: String) -> Result<Value, String> {
    job_id(&job)?;
    let request = host.begin(&id)?;
    let root = super::version_root()?;
    let data = data_root()?;
    let pin = trusted_pin(&root, &data, &job, &request.cancelled)?;
    let (snapshot, binding) =
        if let Some(snapshot) = current_state(&root, &data, &job, &pin, &request.cancelled)? {
            let token: CurrentToken = serde_json::from_value(snapshot["current_token"].clone())
                .map_err(|_| "Không đọc được token review hiện hành")?;
            (snapshot, SessionBinding::Current(token))
        } else {
            // Only an absent native graph permits the original metadata reader.
            // Any native admission failure propagates instead of hiding a head.
            let bytes = run_bounded(
                python_command(&root, &pin, "snapshot")?,
                JSON_LIMIT,
                Duration::from_secs(30),
                &request.cancelled,
            )?;
            let snapshot: Value =
                serde_json::from_slice(&bytes).map_err(|_| "Nội dung review không hợp lệ")?;
            if snapshot["schema_version"].as_u64() != Some(2)
                || snapshot["job_id"] != job
                || snapshot["revision"].as_u64() != Some(0)
                || current_state(&root, &data, &job, &pin, &request.cancelled)?.is_some()
            {
                return Err("Review đã đổi phiên bản; hãy tải lại lời thoại".into());
            }
            (snapshot, SessionBinding::Uninitialized)
        };
    if trusted_pin(&root, &data, &job, &request.cancelled)? != pin {
        return Err("Bản xuất đã thay đổi; hãy tải lại review".into());
    }
    host.save_bound_session(&request, pin, binding)?;
    Ok(
        serde_json::json!({"schema_version":2,"kind":"review_desktop_current_session","session_id":id,"snapshot":snapshot}),
    )
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioRequest {
    job_id: String,
    revision: u64,
    finding_id: String,
    cue_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentAudioRequest {
    job_id: String,
    current_token: CurrentToken,
    finding_id: String,
    cue_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentPlanRequest {
    job_id: String,
    current_token: CurrentToken,
    finding_id: String,
    cue_id: String,
    changes: Value,
}

fn current_plan(
    host: ReviewHost,
    session: String,
    id: String,
    input: CurrentPlanRequest,
) -> Result<Value, String> {
    job_id(&input.job_id)?;
    if !identity(&input.finding_id, 256) || !identity(&input.cue_id, 256) {
        return Err("Câu review không hợp lệ".into());
    }
    let fields = input
        .changes
        .as_object()
        .ok_or("Thay đổi câu không hợp lệ")?;
    if fields.is_empty()
        || fields.len() > 2
        || fields
            .keys()
            .any(|k| !["translation", "voice"].contains(&k.as_str()))
        || fields.iter().any(|(k, v)| {
            v.as_str().is_none_or(|s| {
                s.is_empty()
                    || s != s.trim()
                    || s.chars().count() > if k == "translation" { 8192 } else { 256 }
            })
        })
    {
        return Err("Thay đổi câu không hợp lệ".into());
    }
    let request = host.begin(&id)?;
    let (pin, token) = host.current_session(&session, &input.job_id)?;
    if input.current_token != token || input.job_id != token.job_id {
        return Err("Token kế hoạch không khớp phiên bản đang xem; hãy tải lại".into());
    }
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất đã thay đổi; hãy tải lại review".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-current-plan")
        .arg("--data-root")
        .arg(&data)
        .arg("--job-id")
        .arg(&input.job_id)
        .arg("--revision")
        .arg(&token.revision)
        .arg("--snapshot-sha256")
        .arg(&token.snapshot_sha256)
        .arg("--graph-sha256")
        .arg(&token.graph_sha256)
        .arg("--original-manifest-sha256")
        .arg(&token.original_manifest_sha256)
        .arg("--finding-id")
        .arg(&input.finding_id)
        .arg("--cue-id")
        .arg(&input.cue_id)
        .arg("--changes-json")
        .arg(serde_json::to_string(&input.changes).map_err(|_| "Thay đổi câu không hợp lệ")?);
    let bytes = run_bounded(
        command,
        JSON_LIMIT,
        Duration::from_secs(740),
        &request.cancelled,
    )?;
    let fresh = host.current_session(&session, &input.job_id)?;
    if request.cancelled.load(Ordering::SeqCst)
        || fresh.0 != pin
        || fresh.1 != token
        || trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
    {
        return Err("Review đã đổi phiên bản hoặc bị hủy".into());
    }
    let result: Value =
        serde_json::from_slice(&bytes).map_err(|_| "Kế hoạch sửa câu không hợp lệ")?;
    let response_token: CurrentToken = serde_json::from_value(result["current_token"].clone())
        .map_err(|_| "Token kế hoạch không hợp lệ")?;
    let fields = [
        "schema_version",
        "kind",
        "job_id",
        "current_token",
        "finding_id",
        "cue_id",
        "changes",
        "mode",
        "plan",
        "candidate_revision",
        "candidate_snapshot_sha256",
        "read_only",
        "processing_release_admitted",
        "active_graph_published",
        "execution_ready",
    ];
    if result
        .as_object()
        .is_none_or(|o| o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)))
        || result["schema_version"].as_u64() != Some(1)
        || result["kind"] != "review_current_correction_plan"
        || response_token != token
        || result["job_id"] != input.job_id
        || result["finding_id"] != input.finding_id
        || result["cue_id"] != input.cue_id
        || result["changes"] != input.changes
        || result["read_only"] != true
        || result["processing_release_admitted"] != false
        || result["active_graph_published"] != false
        || result["execution_ready"] != false
    {
        return Err("Kế hoạch không còn gắn với thay đổi đang xem; hãy tải lại".into());
    }
    Ok(result)
}

fn current_audio(
    host: ReviewHost,
    session: String,
    id: String,
    input: CurrentAudioRequest,
) -> Result<Vec<u8>, String> {
    job_id(&input.job_id)?;
    if !identity(&input.finding_id, 256) || !identity(&input.cue_id, 256) {
        return Err("Câu review không hợp lệ".into());
    }
    let request = host.begin(&id)?;
    let (pin, token) = host.current_session(&session, &input.job_id)?;
    if input.current_token != token || input.job_id != token.job_id {
        return Err("Token nghe không khớp phiên bản đang xem; hãy tải lại".into());
    }
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất đã thay đổi; hãy tải lại review".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-current-audio")
        .arg("--data-root")
        .arg(&data)
        .arg("--job-id")
        .arg(&input.job_id)
        .arg("--revision")
        .arg(&token.revision)
        .arg("--snapshot-sha256")
        .arg(&token.snapshot_sha256)
        .arg("--graph-sha256")
        .arg(&token.graph_sha256)
        .arg("--original-manifest-sha256")
        .arg(&token.original_manifest_sha256)
        .arg("--finding-id")
        .arg(&input.finding_id)
        .arg("--cue-id")
        .arg(&input.cue_id);
    let bytes = run_bounded(
        command,
        AUDIO_LIMIT,
        Duration::from_secs(740),
        &request.cancelled,
    )?;
    let fresh = host.current_session(&session, &input.job_id)?;
    if request.cancelled.load(Ordering::SeqCst)
        || fresh.0 != pin
        || fresh.1 != token
        || trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
    {
        return Err("Review đã đổi phiên bản hoặc bị hủy".into());
    }
    validate_current_wav(&bytes)?;
    Ok(bytes)
}

fn validate_current_wav(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() < 44
        || bytes.len() > AUDIO_LIMIT
        || bytes.get(..4) != Some(b"RIFF")
        || bytes.get(8..12) != Some(b"WAVE")
        || u64::from(u32::from_le_bytes(
            bytes[4..8].try_into().map_err(|_| "WAV không hợp lệ")?,
        )) + 8
            != bytes.len() as u64
    {
        return Err("Bộ đọc hiện hành không trả về WAV đã xác thực".into());
    }
    Ok(())
}

fn audio(
    host: ReviewHost,
    session: String,
    id: String,
    input: AudioRequest,
) -> Result<Vec<u8>, String> {
    job_id(&input.job_id)?;
    if input.revision != 0 || !identity(&input.finding_id, 256) || !identity(&input.cue_id, 256) {
        return Err("Câu review hoặc phiên bản không hợp lệ; hãy tải lại".into());
    }
    let request = host.begin(&id)?;
    let original = host.session(&session, &input.job_id)?;
    let root = super::version_root()?;
    let data = data_root()?;
    if original != trusted_pin(&root, &data, &input.job_id, &request.cancelled)? {
        return Err("Bản xuất video đã thay đổi; hãy tải lại lời thoại".into());
    }
    let guarded = host.requires_uninitialized(&session, &input.job_id)?;
    if guarded
        && current_state(&root, &data, &input.job_id, &original, &request.cancelled)?.is_some()
    {
        return Err("Review đã có phiên bản mới; hãy tải lại trước khi nghe".into());
    }
    let mut command = python_command(&root, &original, "audio-v1")?;
    command
        .arg("--cue-id")
        .arg(&input.cue_id)
        .arg("--finding-id")
        .arg(&input.finding_id)
        .arg("--revision")
        .arg(input.revision.to_string());
    let bytes = run_bounded(
        command,
        AUDIO_LIMIT,
        Duration::from_secs(30),
        &request.cancelled,
    )?;
    // Session cancellation after subprocess validation still refuses delivery.
    if request.cancelled.load(Ordering::SeqCst)
        || host.session(&session, &input.job_id)? != original
    {
        return Err("Review đã đổi video hoặc bị hủy".into());
    }
    if guarded
        && current_state(&root, &data, &input.job_id, &original, &request.cancelled)?.is_some()
    {
        return Err("Review đã đổi phiên bản trong khi đọc âm thanh; hãy tải lại".into());
    }
    if bytes.len() < 44 || bytes.get(..4) != Some(b"RIFF") || bytes.get(8..12) != Some(b"WAVE") {
        return Err("Bộ đọc review không trả về WAV đã xác thực".into());
    }
    Ok(bytes)
}

#[tauri::command]
pub async fn review_snapshot_v2(
    host: tauri::State<'_, ReviewHost>,
    job_id: String,
    request_id: String,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || current_snapshot(host, job_id, request_id))
        .await
        .map_err(|_| "Bộ đọc review hiện hành bị gián đoạn".to_owned())?
}

#[tauri::command]
pub async fn review_snapshot_v1(
    host: tauri::State<'_, ReviewHost>,
    job_id: String,
    request_id: String,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || snapshot(host, job_id, request_id))
        .await
        .map_err(|_| "Bộ đọc review bị gián đoạn".to_owned())?
}

#[tauri::command]
pub async fn review_audio_v2(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentAudioRequest,
) -> Result<tauri::ipc::Response, String> {
    let host = host.inner().clone();
    let bytes = tauri::async_runtime::spawn_blocking(move || {
        current_audio(host, session_id, request_id, request)
    })
    .await
    .map_err(|_| "Bộ đọc âm thanh hiện hành bị gián đoạn".to_owned())??;
    Ok(tauri::ipc::Response::new(bytes))
}

#[tauri::command]
pub async fn review_plan_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentPlanRequest,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        current_plan(host, session_id, request_id, request)
    })
    .await
    .map_err(|_| "Bộ lập kế hoạch sửa câu bị gián đoạn".to_owned())?
}

#[tauri::command]
pub async fn review_audio_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: AudioRequest,
) -> Result<tauri::ipc::Response, String> {
    let host = host.inner().clone();
    let bytes =
        tauri::async_runtime::spawn_blocking(move || audio(host, session_id, request_id, request))
            .await
            .map_err(|_| "Bộ đọc âm thanh bị gián đoạn".to_owned())??;
    Ok(tauri::ipc::Response::new(bytes))
}

#[tauri::command]
pub fn review_cancel_v1(
    host: tauri::State<'_, ReviewHost>,
    request_id: String,
) -> Result<(), String> {
    host.cancel(&request_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_plan_refuses_forged_full_token_and_unknown_changes_before_runtime_launch() {
        let host = ReviewHost::default();
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-plan".into(),
            manifest_path: std::env::temp_dir().join("manifest.json"),
            manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            manifest_size_bytes: 10,
            attempt: 1,
        };
        let token = CurrentToken {
            job_id: pin.job_id.clone(),
            revision: "9007199254740993".into(),
            snapshot_sha256: format!("sha256:{}", "b".repeat(64)),
            graph_sha256: format!("sha256:{}", "c".repeat(64)),
            original_manifest_sha256: pin.manifest_sha256.clone(),
        };
        let session = host.begin("request-current-plan-session").unwrap();
        host.save_bound_session(&session, pin, SessionBinding::Current(token.clone()))
            .unwrap();
        drop(session);
        for change in 0..6 {
            let mut forged = token.clone();
            let mut changes = serde_json::json!({"translation":"Changed"});
            match change {
                0 => forged.revision = "0".into(),
                1 => forged.graph_sha256 = "foreign".into(),
                2 => forged.snapshot_sha256 = "foreign".into(),
                3 => forged.original_manifest_sha256 = "foreign".into(),
                4 => forged.job_id = "other-job".into(),
                _ => changes = serde_json::json!({"path":"foreign"}),
            }
            let error = current_plan(
                host.clone(),
                "request-current-plan-session".into(),
                format!("request-plan-{change:04}"),
                CurrentPlanRequest {
                    job_id: "job-plan".into(),
                    current_token: forged,
                    finding_id: "finding-a".into(),
                    cue_id: "cue-a".into(),
                    changes,
                },
            )
            .unwrap_err();
            assert!(
                error.contains("Token kế hoạch") || change == 5 && error.contains("Thay đổi câu")
            );
        }
        assert_eq!(
            host.current_session("request-current-plan-session", "job-plan")
                .unwrap()
                .1
                .revision,
            token.revision
        );
    }

    #[test]
    fn current_audio_refuses_every_forged_token_before_opening_an_installed_runtime() {
        let host = ReviewHost::default();
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-current".into(),
            manifest_path: std::env::temp_dir().join("manifest.json"),
            manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            manifest_size_bytes: 10,
            attempt: 1,
        };
        let token = CurrentToken {
            job_id: pin.job_id.clone(),
            revision: "9007199254740993".into(),
            snapshot_sha256: format!("sha256:{}", "b".repeat(64)),
            graph_sha256: format!("sha256:{}", "c".repeat(64)),
            original_manifest_sha256: pin.manifest_sha256.clone(),
        };
        let request = host.begin("request-current-audio-session").unwrap();
        host.save_bound_session(&request, pin, SessionBinding::Current(token.clone()))
            .unwrap();
        drop(request);
        for change in 0..5 {
            let mut bad = token.clone();
            match change {
                0 => bad.revision = "0".into(),
                1 => bad.snapshot_sha256 = format!("sha256:{}", "d".repeat(64)),
                2 => bad.graph_sha256 = format!("sha256:{}", "e".repeat(64)),
                3 => bad.original_manifest_sha256 = format!("sha256:{}", "f".repeat(64)),
                _ => bad.job_id = "other-job".into(),
            }
            let input = CurrentAudioRequest {
                job_id: "job-current".into(),
                current_token: bad,
                finding_id: "finding-a".into(),
                cue_id: "cue-a".into(),
            };
            let error = current_audio(
                host.clone(),
                "request-current-audio-session".into(),
                format!("request-current-audio-{change:04}"),
                input,
            )
            .unwrap_err();
            assert!(error.contains("Token nghe"));
        }
        let (pin, saved) = host
            .current_session("request-current-audio-session", "job-current")
            .unwrap();
        assert_eq!(saved.revision, token.revision);
        assert_eq!(pin.manifest_sha256, token.original_manifest_sha256);
    }

    #[test]
    fn current_wav_envelope_refuses_truncated_extra_and_non_wav_pipe_data() {
        let mut wave = vec![0u8; 48];
        wave[..4].copy_from_slice(b"RIFF");
        wave[4..8].copy_from_slice(&40u32.to_le_bytes());
        wave[8..12].copy_from_slice(b"WAVE");
        wave[12..16].copy_from_slice(b"fmt ");
        wave[16..20].copy_from_slice(&16u32.to_le_bytes());
        wave[20..22].copy_from_slice(&1u16.to_le_bytes());
        wave[22..24].copy_from_slice(&1u16.to_le_bytes());
        wave[24..28].copy_from_slice(&48000u32.to_le_bytes());
        wave[28..32].copy_from_slice(&96000u32.to_le_bytes());
        wave[32..34].copy_from_slice(&2u16.to_le_bytes());
        wave[34..36].copy_from_slice(&16u16.to_le_bytes());
        wave[36..40].copy_from_slice(b"data");
        wave[40..44].copy_from_slice(&4u32.to_le_bytes());
        validate_current_wav(&wave).unwrap();
        assert!(validate_current_wav(&wave[..47]).is_err());
        wave.push(b'\n');
        assert!(validate_current_wav(&wave).is_err());
        assert!(validate_current_wav(br#"{"kind":"wrong pipe format"}"#).is_err());
    }

    #[test]
    fn current_projection_requires_exact_origin_pin_and_decimal_token_and_never_falls_back_on_failure(
    ) {
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-current".into(),
            manifest_path: std::env::temp_dir().join("manifest.json"),
            manifest_sha256: format!("sha256:{}", "c".repeat(64)),
            manifest_size_bytes: 10,
            attempt: 1,
        };
        let view = serde_json::json!({"schema_version":3,"kind":"review_current_snapshot","job_id":"job-current",
            "current_token":{"job_id":"job-current","revision":"9007199254740993","snapshot_sha256":format!("sha256:{}","a".repeat(64)),
                "graph_sha256":format!("sha256:{}","b".repeat(64)),"original_manifest_sha256":pin.manifest_sha256},
            "mode":"b2","time_base":{"numerator":"1","denominator":"90000"},"findings":[],"last_plan":null,"correction_ready":false});
        let packet = serde_json::json!({"schema_version":1,"kind":"review_native_desktop_state","job_id":"job-current","status":"current","snapshot":view});
        let accepted = validate_current_state(packet.clone(), "job-current", &pin)
            .unwrap()
            .unwrap();
        assert_eq!(accepted["current_token"]["revision"], "9007199254740993");
        for change in 0..5 {
            let mut bad = packet.clone();
            match change {
                0 => {
                    bad["snapshot"]["current_token"]["revision"] =
                        serde_json::json!(9007199254740992u64)
                }
                1 => bad["snapshot"]["current_token"]["revision"] = "01".into(),
                2 => {
                    bad["snapshot"]["current_token"]["original_manifest_sha256"] =
                        format!("sha256:{}", "d".repeat(64)).into()
                }
                3 => bad["snapshot"]["correction_ready"] = true.into(),
                _ => bad["snapshot"] = Value::Null,
            }
            assert!(validate_current_state(bad, "job-current", &pin).is_err());
        }
        let empty = serde_json::json!({"schema_version":1,"kind":"review_native_desktop_state","job_id":"job-current","status":"original_only","snapshot":null});
        assert!(validate_current_state(empty, "job-current", &pin)
            .unwrap()
            .is_none());
    }

    #[test]
    fn current_session_cannot_be_replayed_through_original_audio_and_uninitialized_reads_are_guarded(
    ) {
        let host = ReviewHost::default();
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-current".into(),
            manifest_path: std::env::temp_dir().join("manifest.json"),
            manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            manifest_size_bytes: 10,
            attempt: 1,
        };
        let first = host.begin("request-current-0001").unwrap();
        let token = CurrentToken {
            job_id: pin.job_id.clone(),
            revision: "9007199254740993".into(),
            snapshot_sha256: format!("sha256:{}", "b".repeat(64)),
            graph_sha256: format!("sha256:{}", "c".repeat(64)),
            original_manifest_sha256: pin.manifest_sha256.clone(),
        };
        host.save_bound_session(&first, pin.clone(), SessionBinding::Current(token))
            .unwrap();
        assert!(host.session(&first.id, "job-current").is_err());
        {
            let registry = host.registry.lock().unwrap();
            match &registry.sessions[&first.id].binding {
                SessionBinding::Current(token) => assert_eq!(token.revision, "9007199254740993"),
                _ => panic!("native current token binding was lost"),
            }
        }
        let second = host.begin("request-original-0001").unwrap();
        host.save_bound_session(&second, pin.clone(), SessionBinding::Uninitialized)
            .unwrap();
        assert_eq!(host.session(&second.id, "job-current").unwrap(), pin);
        assert!(host
            .requires_uninitialized(&second.id, "job-current")
            .unwrap());
        host.cancel(&second.id).unwrap();
        assert!(host
            .requires_uninitialized(&second.id, "job-current")
            .is_err());
    }

    #[test]
    fn cancellation_before_registration_and_duplicate_admission_are_bounded() {
        let host = ReviewHost::default();
        let first = "request-before-start";
        host.cancel(first).unwrap();
        assert!(host.begin(first).is_err());
        let request = host.begin("request-active-1").unwrap();
        assert!(host.begin("request-active-1").is_err());
        host.cancel("request-active-1").unwrap();
        assert!(request.cancelled.load(Ordering::SeqCst));
        drop(request);
        assert!(host.begin("request-active-1").is_err());
        for index in 0..REQUEST_LIMIT - 2 {
            host.cancel(&format!("request-tombstone-{index:04}"))
                .unwrap();
        }
        assert!(host.cancel("request-overflow-1").is_err());
        assert!(host.begin("request-overflow-1").is_err());
        assert!(host.begin("../outside").is_err());
    }

    #[test]
    fn sessions_bind_job_pin_and_cancel_releases_capacity() {
        let host = ReviewHost::default();
        let make_pin = || ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-review".into(),
            manifest_path: std::env::temp_dir().join("job_manifest.json"),
            manifest_sha256: format!("sha256:{}", "0".repeat(64)),
            manifest_size_bytes: 10,
            attempt: 1,
        };
        let mut live = Vec::new();
        for index in 0..ACTIVE_LIMIT {
            live.push(host.begin(&format!("request-active-{index:04}")).unwrap());
        }
        assert!(host.begin("request-active-extra").is_err());
        for request in &live {
            host.save_session(request, make_pin()).unwrap();
        }
        drop(live);
        let id = "request-active-0000";
        assert_eq!(host.session(id, "job-review").unwrap(), make_pin());
        assert!(host.session(id, "other-job").is_err());
        host.cancel(id).unwrap();
        assert!(host.session(id, "job-review").is_err());
        let request = host.begin("request-cancelled-save").unwrap();
        host.cancel(&request.id).unwrap();
        assert!(host.save_session(&request, make_pin()).is_err());
        let mut registry = host.registry.lock().unwrap();
        for slot in registry.requests.values_mut() {
            if !slot.active {
                slot.changed = Instant::now() - TOMBSTONE_TTL;
            }
        }
        for session in registry.sessions.values_mut() {
            session.created = Instant::now() - SESSION_TTL;
        }
        registry.prune();
        assert!(registry.sessions.is_empty());
        assert_eq!(registry.requests.len(), 1);
    }

    #[test]
    fn process_fixture() {
        match std::env::var("DUBFLOW_REVIEW_TEST_FIXTURE").as_deref() {
            Ok("small") => {
                println!("REVIEW_FIXTURE_OK");
                eprintln!("REVIEW_FIXTURE_STDERR");
            }
            Ok("overflow") => {
                println!("{}", "x".repeat(65536));
            }
            Ok("sleep") => std::thread::sleep(Duration::from_secs(30)),
            Ok("failed") => std::process::exit(2),
            _ => {}
        }
    }

    fn fixture(mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", "review::tests::process_fixture", "--nocapture"])
            .env("DUBFLOW_REVIEW_TEST_FIXTURE", mode);
        command
    }

    #[test]
    fn readers_drain_both_pipes_refuse_overflow_failure_timeout_and_cancel() {
        let active = AtomicBool::new(false);
        let bytes = run_bounded(fixture("small"), 4096, Duration::from_secs(5), &active).unwrap();
        assert!(String::from_utf8(bytes)
            .unwrap()
            .contains("REVIEW_FIXTURE_OK"));
        assert!(run_bounded(fixture("overflow"), 64, Duration::from_secs(5), &active).is_err());
        assert!(run_bounded(fixture("failed"), 4096, Duration::from_secs(5), &active).is_err());
        let started = Instant::now();
        assert!(run_bounded(fixture("sleep"), 4096, Duration::from_millis(100), &active).is_err());
        assert!(started.elapsed() < Duration::from_secs(4));
        let cancelled = Arc::new(AtomicBool::new(false));
        let trigger = cancelled.clone();
        let cancel = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(100));
            trigger.store(true, Ordering::SeqCst);
        });
        assert!(run_bounded(fixture("sleep"), 4096, Duration::from_secs(5), &cancelled).is_err());
        cancel.join().unwrap();
        assert!(run_bounded(
            fixture("small"),
            4096,
            Duration::from_secs(5),
            &AtomicBool::new(true)
        )
        .is_err());
    }
}
