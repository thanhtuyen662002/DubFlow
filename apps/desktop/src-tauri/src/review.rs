//! Optional Review IPC. Read and explicit apply requests are bound to native
//! completed-export/current sessions; the webview never selects media roots.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read};
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
    activity: Option<RequestActivity>,
}

// Host observations only. Process liveness is not worker/chunk progress or a
// successful durable result; only the existing validated reply proves success.
struct RequestActivity {
    job: String,
    operation: &'static str,
    started: Instant,
    phase: &'static str,
    phase_started: Instant,
    process: &'static str,
    process_observed: Option<Instant>,
    native: Option<NativeObservation>,
    native_invalid: bool,
    ended_phase: Option<&'static str>,
    diagnostic: Option<String>,
}

const STAGE_PREFIX: &[u8] = b"DUBFLOW_REVIEW_STAGE_V1 ";
const NATIVE_STAGES: &[&str] = &[
    "admitting_current",
    "admitting_processing",
    "verifying_runtime_files",
    "preparing_cue",
    "synthesizing_cue",
    "validating_cue",
    "mixing_audio",
    "validating_mix",
    "deriving_timeline",
    "rendering_video",
    "validating_render",
    "retaining_publication",
    "committing_revision",
    "reopening_current",
    "publishing_standard",
    "publishing_editable",
];
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct NativeStage {
    schema_version: u32,
    kind: String,
    sequence: u64,
    stage_epoch: u64,
    stage: String,
    elapsed_ms: u64,
    units_completed: u64,
    bytes_verified: u64,
}
impl NativeStage {
    fn read(raw: &[u8]) -> Option<Self> {
        let value: Self = serde_json::from_slice(raw).ok()?;
        if value.schema_version != 1
            || value.kind != "review_native_stage_observation"
            || !(1..=32768).contains(&value.sequence)
            || !(1..=value.sequence).contains(&value.stage_epoch)
            || !NATIVE_STAGES.contains(&value.stage.as_str())
            || value.elapsed_ms > 86_400_000
            || value.units_completed > 9_007_199_254_740_991
            || value.bytes_verified > 9_007_199_254_740_991
            || (value.stage != "verifying_runtime_files"
                && (value.units_completed != 0 || value.bytes_verified != 0))
        {
            return None;
        }
        Some(value)
    }
}
struct NativeObservation {
    event: NativeStage,
    received: Instant,
    advanced: Instant,
}
#[derive(Clone)]
struct StageSink {
    host: ReviewHost,
    id: String,
}
impl StageSink {
    fn invalid(&self) {
        if let Ok(mut r) = self.host.registry.lock() {
            if let Some(a) = r
                .requests
                .get_mut(&self.id)
                .and_then(|s| s.activity.as_mut())
            {
                a.native = None;
                a.native_invalid = true;
            }
        }
    }
    fn observe(&self, event: NativeStage) {
        if let Ok(mut r) = self.host.registry.lock() {
            let Some(slot) = r.requests.get_mut(&self.id) else {
                return;
            };
            if !slot.active || slot.cancelled.load(Ordering::SeqCst) {
                return;
            }
            let Some(a) = slot.activity.as_mut() else {
                return;
            };
            if a.native_invalid {
                return;
            }
            let now = Instant::now();
            let mut advanced = now;
            if let Some(previous) = &a.native {
                let p = &previous.event;
                if event.sequence <= p.sequence
                    || event.elapsed_ms < p.elapsed_ms
                    || event.stage_epoch < p.stage_epoch
                    || (event.stage_epoch == p.stage_epoch
                        && (event.stage != p.stage
                            || event.units_completed < p.units_completed
                            || event.bytes_verified < p.bytes_verified))
                {
                    a.native = None;
                    a.native_invalid = true;
                    return;
                }
                if event.stage_epoch == p.stage_epoch
                    && event.units_completed == p.units_completed
                    && event.bytes_verified == p.bytes_verified
                {
                    advanced = previous.advanced;
                }
            }
            a.native = Some(NativeObservation {
                event,
                received: now,
                advanced,
            });
        }
    }
}

/// Only this explicit observed native command permits progress records. Ordinary
/// stderr remains under its original cap; stdout/final acknowledgements do not change.
fn capture_stage_stderr(stream: impl Read, sink: StageSink) -> Result<Vec<u8>, String> {
    let mut reader = BufReader::new(stream);
    let mut diagnostics = Vec::new();
    let mut line_start = true;
    let mut progress_bytes = 0usize;
    loop {
        let mut fragment = Vec::new();
        // A long diagnostic line is read in bounded fragments, never allocated whole.
        let n = reader
            .by_ref()
            .take(513)
            .read_until(b'\n', &mut fragment)
            .map_err(|_| "Không đọc được chẩn đoán review")?;
        if n == 0 {
            return Ok(diagnostics);
        }
        let ended = fragment.last() == Some(&b'\n');
        if line_start && ended && fragment.len() <= 512 && fragment.starts_with(STAGE_PREFIX) {
            if let Some(event) = NativeStage::read(&fragment[STAGE_PREFIX.len()..]) {
                progress_bytes += fragment.len();
                if progress_bytes > 16 * 1024 * 1024 {
                    sink.invalid();
                    return Err("Quan sát review vượt giới hạn byte".into());
                }
                sink.observe(event);
                line_start = true;
                continue;
            }
            sink.invalid();
        }
        if diagnostics.len() + fragment.len() > ERROR_LIMIT {
            return Err("Nội dung review vượt giới hạn byte".into());
        }
        diagnostics.extend_from_slice(&fragment);
        line_start = ended;
    }
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
    pending_plan: Option<Value>,
    original_view: Option<Value>,
}
#[derive(Default)]
struct Registry {
    requests: HashMap<String, RequestSlot>,
    sessions: HashMap<String, Session>,
    applying_jobs: HashSet<String>,
}

#[derive(Clone, Default)]
pub struct ReviewHost {
    registry: Arc<Mutex<Registry>>,
    failure_binding: Option<Arc<Mutex<Option<FailureBinding>>>>,
}
struct FailureBinding {
    id: String,
    registered: Arc<AtomicBool>,
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

struct ApplyGuard {
    host: ReviewHost,
    job: String,
}
impl Drop for ApplyGuard {
    fn drop(&mut self) {
        if let Ok(mut registry) = self.host.registry.lock() {
            registry.applying_jobs.remove(&self.job);
        }
    }
}
impl Drop for RequestGuard {
    fn drop(&mut self) {
        if let Ok(mut registry) = self.host.registry.lock() {
            if let Some(slot) = registry.requests.get_mut(&self.id) {
                slot.active = false;
                slot.changed = Instant::now();
                if let Some(activity) = slot.activity.as_mut() {
                    activity.ended_phase = Some(activity.phase);
                    activity.phase = "ended";
                    activity.phase_started = slot.changed;
                }
            }
        }
    }
}

impl RequestGuard {
    fn failing_stderr(&self, bytes: &[u8]) {
        if let Ok(mut r) = self.host.registry.lock() {
            if let Some(a) = r
                .requests
                .get_mut(&self.id)
                .and_then(|r| r.activity.as_mut())
            {
                let text = bounded_diagnostic(&String::from_utf8_lossy(bytes), ERROR_LIMIT);
                a.diagnostic = if text.is_empty() { None } else { Some(text) };
            }
        }
    }

    fn observe(&self, job: &str, operation: &'static str) -> Result<(), String> {
        let mut registry = self
            .host
            .registry
            .lock()
            .map_err(|_| "Review không sẵn sàng")?;
        let slot = registry
            .requests
            .get_mut(&self.id)
            .ok_or("Yêu cầu review đã hết hạn")?;
        if !slot.active || slot.activity.is_some() || self.cancelled.load(Ordering::SeqCst) {
            return Err("Yêu cầu review không còn hoạt động".into());
        }
        let now = Instant::now();
        slot.activity = Some(RequestActivity {
            job: job.into(),
            operation,
            started: now,
            phase: "checking",
            phase_started: now,
            process: "idle",
            process_observed: None,
            native: None,
            native_invalid: false,
            ended_phase: None,
            diagnostic: None,
        });
        Ok(())
    }

    fn phase(&self, phase: &'static str) {
        if let Ok(mut registry) = self.host.registry.lock() {
            if let Some(activity) = registry
                .requests
                .get_mut(&self.id)
                .and_then(|s| s.activity.as_mut())
            {
                activity.phase = phase;
                if phase == "processing" {
                    activity.native = None;
                    activity.native_invalid = false;
                }
                activity.phase_started = Instant::now();
                activity.process = "idle";
                activity.process_observed = None;
            }
        }
    }

    fn process(&self, state: &'static str) {
        if let Ok(mut registry) = self.host.registry.lock() {
            if let Some(activity) = registry
                .requests
                .get_mut(&self.id)
                .and_then(|s| s.activity.as_mut())
            {
                activity.process = state;
                activity.process_observed = Some(Instant::now());
            }
        }
    }
}

impl ReviewHost {
    fn capture_failures(&self) -> Self {
        Self {
            registry: self.registry.clone(),
            failure_binding: Some(Arc::new(Mutex::new(None))),
        }
    }

    fn request_failure(&self, id: &str, message: &str) -> Value {
        let message = bounded_diagnostic(message, 1024);
        let Ok(registry) = self.registry.lock() else {
            return Value::String(message);
        };
        let Some(slot) = registry.requests.get(id) else {
            return Value::String(message);
        };
        if !slot.active && slot.changed.elapsed() >= TOMBSTONE_TTL {
            return Value::String(message);
        }
        let Some(binding) = self.failure_binding.as_ref() else {
            return Value::String(message);
        };
        let Ok(binding) = binding.try_lock() else {
            return Value::String(message);
        };
        let Some(binding) = binding.as_ref() else {
            return Value::String(message);
        };
        if binding.id != id || !Arc::ptr_eq(&binding.registered, &slot.cancelled) {
            return Value::String(message);
        }
        let Some(a) = slot.activity.as_ref() else {
            return Value::String(message);
        };
        serde_json::json!({"schema_version":1,"kind":"review_desktop_request_failure",
            "request_id":id,"job_id":a.job,"operation":a.operation,"outcome":"unconfirmed",
            "phase":a.ended_phase.unwrap_or(a.phase),"native_stage":a.native.as_ref().filter(|_| !a.native_invalid).map(|n| n.event.stage.as_str()),
            "message":message,"diagnostic":a.diagnostic})
    }

    fn request_status(&self, id: &str) -> Result<Value, String> {
        request_id(id)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        let slot = registry
            .requests
            .get(id)
            .ok_or("Chưa đọc được trạng thái tác vụ review")?;
        let a = slot
            .activity
            .as_ref()
            .ok_or("Tác vụ chưa có quan sát review")?;
        let now = if slot.active {
            Instant::now()
        } else {
            slot.changed
        };
        let ms = |time: Instant| {
            now.saturating_duration_since(time)
                .as_millis()
                .min(86_400_000) as u64
        };
        Ok(
            serde_json::json!({"schema_version":1,"kind":"review_desktop_request_activity",
            "request_id":id,"job_id":a.job,"operation":a.operation,
            "state":if !slot.active {"ended"} else if slot.cancelled.load(Ordering::SeqCst) {"cancelling"} else {"running"},
            "phase":a.phase,"elapsed_ms":ms(a.started),"phase_elapsed_ms":ms(a.phase_started),
            "process_state":a.process,"process_observation_age_ms":a.process_observed.map(ms)}),
        )
    }

    fn request_stage(&self, id: &str) -> Result<Value, String> {
        request_id(id)?;
        let registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        let a = registry
            .requests
            .get(id)
            .and_then(|r| r.activity.as_ref())
            .ok_or("Chưa quan sát được review")?;
        let native = a.native.as_ref().filter(|_| !a.native_invalid).map(|n| serde_json::json!({
            "event":n.event,"received_age_ms":n.received.elapsed().as_millis().min(86_400_000) as u64,
            "advance_age_ms":n.advanced.elapsed().as_millis().min(86_400_000) as u64}));
        drop(registry);
        // Read host elapsed after the native event snapshot to avoid a newer
        // cross-thread event exceeding an earlier host observation cut.
        let activity = self.request_status(id)?;
        Ok(
            serde_json::json!({"schema_version":1,"kind":"review_desktop_request_stage","activity":activity,
            "native_observation":if native.is_some() {"observed"} else {"unavailable"},"native_stage":native}),
        )
    }

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
                activity: None,
            },
        );
        if let Some(binding) = &self.failure_binding {
            if let Ok(mut binding) = binding.try_lock() {
                if binding.is_none() {
                    *binding = Some(FailureBinding {
                        id: id.to_owned(),
                        registered: cancelled.clone(),
                    });
                }
            }
        }
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
                    activity: None,
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
        if registry.applying_jobs.contains(&pin.job_id) {
            return Err("Video đang áp dụng thay đổi; hãy tải lại sau khi tác vụ kết thúc".into());
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
                pending_plan: None,
                original_view: None,
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

    fn result_video_session(
        &self,
        id: &str,
        input: &ResultVideoRequest,
    ) -> Result<ExportPin, String> {
        request_id(id)?;
        validate_result_video_request(input)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        if registry.applying_jobs.contains(&input.job_id) {
            return Err(
                "Video đang có tác vụ thay đổi; hãy tải lại sau khi tác vụ kết thúc".into(),
            );
        }
        let saved = registry
            .sessions
            .get(id)
            .ok_or("Phiên xem video đã hết hạn; hãy tải lại")?;
        if saved.pin.job_id != input.job_id {
            return Err("Video không khớp phiên đang xem".into());
        }
        let bound = match (&input.expected, &saved.binding) {
            (ResultVideoExpected::Original { revision: 0 }, SessionBinding::Uninitialized) => {
                saved.original_view.as_ref().is_some_and(|v| {
                    v["schema_version"] == 2 && v["job_id"] == input.job_id && v["revision"] == 0
                })
            }
            (ResultVideoExpected::Current { current_token }, SessionBinding::Current(actual)) => {
                current_token == actual
                    && current_token.original_manifest_sha256 == saved.pin.manifest_sha256
            }
            _ => false,
        };
        if !bound {
            return Err("Phiên xem video đã đổi; hãy tải lại lời thoại".into());
        }
        Ok(saved.pin.clone())
    }

    fn save_plan(
        &self,
        session: &str,
        token: &CurrentToken,
        request: &RequestGuard,
        plan: Value,
    ) -> Result<(), String> {
        let supplied: CurrentToken = serde_json::from_value(plan["current_token"].clone())
            .map_err(|_| "Token kế hoạch không hợp lệ")?;
        if supplied != *token
            || serde_json::to_vec(&plan)
                .map_err(|_| "Kế hoạch không hợp lệ")?
                .len()
                > 64 * 1024
        {
            return Err("Kế hoạch không khớp phiên hoặc vượt giới hạn".into());
        }
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        if request.cancelled.load(Ordering::SeqCst)
            || registry.applying_jobs.contains(&token.job_id)
        {
            return Err("Kế hoạch đã bị hủy hoặc đang được áp dụng; hãy tải lại".into());
        }
        let saved = registry
            .sessions
            .get_mut(session)
            .ok_or("Phiên review đã hết hạn; hãy tải lại")?;
        if saved.binding != SessionBinding::Current(token.clone()) {
            return Err("Phiên kế hoạch đã đổi; hãy tải lại".into());
        }
        saved.pending_plan = Some(plan);
        Ok(())
    }

    /// Consume once before starting work. Other sessions for this job are
    /// invalidated even on an uncertain result; a reload observes durable CAS.
    fn consume_plan(
        &self,
        session: &str,
        plan: &Value,
    ) -> Result<(ExportPin, CurrentToken, ApplyGuard), String> {
        request_id(session)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        let saved = registry
            .sessions
            .get(session)
            .ok_or("Phiên áp dụng đã hết hạn; hãy tải lại")?;
        let token = match &saved.binding {
            SessionBinding::Current(t) => t.clone(),
            _ => return Err("Áp dụng cần phiên hiện hành; hãy tải lại".into()),
        };
        let supplied: CurrentToken = serde_json::from_value(plan["current_token"].clone())
            .map_err(|_| "Token xác nhận không hợp lệ")?;
        if supplied != token
            || saved.pending_plan.as_ref() != Some(plan)
            || registry.applying_jobs.contains(&token.job_id)
        {
            return Err(
                "Kế hoạch chưa được kiểm tra, đã thay đổi hoặc đang được áp dụng; hãy tải lại"
                    .into(),
            );
        }
        let pin = saved.pin.clone();
        let job = token.job_id.clone();
        registry.sessions.retain(|_, s| s.pin.job_id != job);
        registry.applying_jobs.insert(job.clone());
        Ok((
            pin,
            token,
            ApplyGuard {
                host: self.clone(),
                job,
            },
        ))
    }

    fn consume_undo(
        &self,
        session: &str,
        supplied: &CurrentToken,
    ) -> Result<(ExportPin, CurrentToken, ApplyGuard), String> {
        request_id(session)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        let saved = registry
            .sessions
            .get(session)
            .ok_or("Phiên hoàn tác đã hết hạn; hãy tải lại")?;
        let token = match &saved.binding {
            SessionBinding::Current(token) if token == supplied => token.clone(),
            _ => return Err("Hoàn tác cần đúng phiên hiện hành; hãy tải lại".into()),
        };
        let revision = token
            .revision
            .parse::<u64>()
            .map_err(|_| "Phiên hoàn tác không hợp lệ")?;
        if revision == 0
            || revision >= i64::MAX as u64
            || registry.applying_jobs.contains(&token.job_id)
        {
            return Err("Chưa thể hoàn tác phiên này; hãy tải lại để kiểm tra".into());
        }
        let pin = saved.pin.clone();
        let job = token.job_id.clone();
        // Share the apply guard: every older plan/session is consumed even if
        // the subsequent native undo has an uncertain outcome.
        registry.sessions.retain(|_, saved| saved.pin.job_id != job);
        registry.applying_jobs.insert(job.clone());
        Ok((
            pin,
            token,
            ApplyGuard {
                host: self.clone(),
                job,
            },
        ))
    }

    fn consume_export(
        &self,
        session: &str,
        supplied: &CurrentToken,
    ) -> Result<(ExportPin, CurrentToken, ApplyGuard), String> {
        request_id(session)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        let saved = registry
            .sessions
            .get(session)
            .ok_or("Phiên xuất đã hết hạn; hãy tải lại")?;
        let token = match &saved.binding {
            SessionBinding::Current(token) if token == supplied => token.clone(),
            _ => return Err("Xuất kết quả cần đúng phiên hiện hành; hãy tải lại".into()),
        };
        if registry.applying_jobs.contains(&token.job_id) {
            return Err("Video đang thực hiện một tác vụ; hãy đợi tác vụ kết thúc".into());
        }
        let pin = saved.pin.clone();
        let job = token.job_id.clone();
        registry.sessions.retain(|_, saved| saved.pin.job_id != job);
        registry.applying_jobs.insert(job.clone());
        Ok((
            pin,
            token,
            ApplyGuard {
                host: self.clone(),
                job,
            },
        ))
    }

    fn save_original_view(&self, request: &RequestGuard, snapshot: &Value) -> Result<(), String> {
        if snapshot["schema_version"].as_u64() != Some(2)
            || snapshot["revision"].as_u64() != Some(0)
            || !snapshot["last_plan"].is_null()
            || serde_json::to_vec(snapshot)
                .map_err(|_| "Nội dung gốc không hợp lệ")?
                .len()
                > JSON_LIMIT
        {
            return Err("Chỉ có thể bắt đầu lịch sử từ bản gốc chưa chỉnh sửa".into());
        }
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        let saved = registry
            .sessions
            .get_mut(&request.id)
            .ok_or("Phiên gốc đã hết hạn")?;
        if request.cancelled.load(Ordering::SeqCst)
            || saved.binding != SessionBinding::Uninitialized
            || snapshot["job_id"] != saved.pin.job_id
        {
            return Err("Phiên gốc đã đổi hoặc bị hủy; hãy tải lại".into());
        }
        saved.original_view = Some(snapshot.clone());
        Ok(())
    }

    fn consume_initialize(
        &self,
        session: &str,
        job: &str,
    ) -> Result<(ExportPin, Value, ApplyGuard), String> {
        request_id(session)?;
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        let saved = registry
            .sessions
            .get(session)
            .ok_or("Phiên gốc đã hết hạn; hãy tải lại")?;
        if saved.binding != SessionBinding::Uninitialized
            || saved.pin.job_id != job
            || registry.applying_jobs.contains(job)
        {
            return Err("Chỉ có thể bắt đầu từ phiên gốc chưa có lịch sử; hãy tải lại".into());
        }
        let previous = saved
            .original_view
            .clone()
            .ok_or("Phiên gốc chưa được kiểm tra; hãy tải lại")?;
        let pin = saved.pin.clone();
        registry.sessions.retain(|_, saved| saved.pin.job_id != job);
        registry.applying_jobs.insert(job.to_owned());
        Ok((
            pin,
            previous,
            ApplyGuard {
                host: self.clone(),
                job: job.to_owned(),
            },
        ))
    }

    /// Only the caller holding this host's live mutation guard may publish the
    /// freshly readmitted post-CAS session. Snapshot readers cannot recreate an
    /// old session while the apply is in flight.
    fn finish_apply_session(
        &self,
        request: &RequestGuard,
        pin: ExportPin,
        token: CurrentToken,
        applying: &ApplyGuard,
    ) -> Result<(), String> {
        if !Arc::ptr_eq(&self.registry, &applying.host.registry)
            || !Arc::ptr_eq(&self.registry, &request.host.registry)
            || applying.job != token.job_id
            || pin.job_id != token.job_id
            || pin.manifest_sha256 != token.original_manifest_sha256
        {
            return Err("Phiên áp dụng không khớp tác vụ hiện hành".into());
        }
        let mut registry = self.registry.lock().map_err(|_| "Review không sẵn sàng")?;
        registry.prune();
        if request.cancelled.load(Ordering::SeqCst)
            || !registry.applying_jobs.contains(&token.job_id)
            || registry.sessions.len() >= SESSION_LIMIT
        {
            return Err("Phiên áp dụng đã hết hạn hoặc bị hủy; hãy tải lại để kiểm tra".into());
        }
        registry
            .sessions
            .retain(|_, s| s.pin.job_id != token.job_id);
        registry.sessions.insert(
            request.id.clone(),
            Session {
                pin,
                created: Instant::now(),
                binding: SessionBinding::Current(token),
                pending_plan: None,
                original_view: None,
            },
        );
        Ok(())
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentConfirmation {
    schema_version: u32,
    kind: String,
    confirm: bool,
    plan: Value,
}

fn applied_token(
    before: &CurrentToken,
    plan: &Value,
    ack: &Value,
    fresh: &Value,
) -> Result<CurrentToken, String> {
    let error = || "Kết quả áp dụng chưa khớp bản hiện hành; hãy tải lại để kiểm tra".to_owned();
    let fields = [
        "schema_version",
        "kind",
        "job_id",
        "revision",
        "snapshot_sha256",
        "graph_sha256",
        "snapshot",
        "active_graph_published",
        "correction_ready",
    ];
    let expected = before
        .revision
        .parse::<u64>()
        .ok()
        .and_then(|n| n.checked_add(1))
        .filter(|n| *n <= i64::MAX as u64)
        .ok_or_else(error)?;
    if ack
        .as_object()
        .is_none_or(|o| o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)))
        || ack["schema_version"].as_u64() != Some(1)
        || ack["kind"] != "review_native_committed_render"
        || ack["job_id"] != before.job_id
        || ack["revision"] != expected.to_string()
        || plan["candidate_revision"] != expected.to_string()
        || ack["active_graph_published"] != true
        || ack["correction_ready"] != false
        || ack["snapshot"]["job_id"] != before.job_id
        || ack["snapshot"].as_object().is_none_or(|o| {
            o.len() != 6
                || [
                    "schema_version",
                    "job_id",
                    "revision",
                    "time_base",
                    "findings",
                    "last_plan",
                ]
                .iter()
                .any(|k| !o.contains_key(*k))
        })
        || ack["snapshot"]["schema_version"].as_u64() != Some(2)
        || ack["snapshot"]["revision"].as_u64() != Some(expected)
        || ack["snapshot"]["time_base"] != fresh["time_base"]
        || ack["snapshot"]["findings"] != fresh["findings"]
        || ack["snapshot"]["last_plan"] != fresh["last_plan"]
    {
        return Err(error());
    }
    let after: CurrentToken =
        serde_json::from_value(fresh["current_token"].clone()).map_err(|_| error())?;
    if after.job_id != before.job_id
        || after.revision != expected.to_string()
        || after.original_manifest_sha256 != before.original_manifest_sha256
        || !is_sha(&after.snapshot_sha256)
        || !is_sha(&after.graph_sha256)
        || after.snapshot_sha256 == before.snapshot_sha256
        || after.graph_sha256 == before.graph_sha256
        || ack["snapshot_sha256"] != after.snapshot_sha256
        || ack["graph_sha256"] != after.graph_sha256
        || fresh["mode"] != plan["mode"]
    {
        return Err(error());
    }
    Ok(after)
}

fn current_apply(
    host: ReviewHost,
    session: String,
    id: String,
    confirmation: CurrentConfirmation,
) -> Result<Value, String> {
    if confirmation.schema_version != 1
        || confirmation.kind != "review_current_confirmation"
        || !confirmation.confirm
    {
        return Err("Cần xác nhận kế hoạch đã kiểm tra trước khi áp dụng".into());
    }
    let request = host.begin(&id)?;
    let (pin, before, applying) = host.consume_plan(&session, &confirmation.plan)?;
    request.observe(&pin.job_id, "apply")?;
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất gốc đã thay đổi; hãy tải lại review".into());
    }
    let body = serde_json::json!({"schema_version":1,"kind":"review_current_confirmation","confirm":true,"plan":confirmation.plan});
    let encoded = serde_json::to_string(&body).map_err(|_| "Không đọc được xác nhận sửa câu")?;
    // Windows CreateProcess has a UTF-16 command-line bound. Never truncate an
    // admitted change; refuse before spawning if the complete request is large.
    if encoded.encode_utf16().count()
        + data.as_os_str().to_string_lossy().encode_utf16().count()
        + root.as_os_str().to_string_lossy().encode_utf16().count()
        > 28000
    {
        return Err("Yêu cầu sửa vượt giới hạn; hãy tải lại trước khi tiếp tục".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-confirm-current")
        .arg("--data-root")
        .arg(&data)
        .arg("--confirmation-json")
        .arg(encoded);
    // Native operations retain their own phase/chunk deadlines. This overall
    // host bound does not impose the short read-preview timeout on rendering.
    // Cancellation kills the owned native process; its existing Windows
    // non-inherited kill-on-close WorkerTree owns each worker before byte-gate.
    request.phase("processing");
    let bytes = run_observed(command, JSON_LIMIT, Duration::from_secs(8 * 3600), &request)?;
    request.phase("verifying");
    let ack: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "Kết quả áp dụng chưa rõ; hãy tải lại để kiểm tra")?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Tác vụ đã bị hủy hoặc dữ liệu thay đổi; hãy tải lại để kiểm tra".into());
    }
    let fresh = current_state(&root, &data, &before.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được bản đã áp dụng; hãy tải lại")?;
    let after = applied_token(&before, &body["plan"], &ack, &fresh)?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Kết quả đến sau khi tác vụ bị hủy; hãy tải lại để kiểm tra".into());
    }
    host.finish_apply_session(&request, pin, after, &applying)?;
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_applied_current","before_token":body["plan"]["current_token"],"session_id":id,"snapshot":fresh}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentUndoRequest {
    schema_version: u32,
    kind: String,
    confirm: bool,
    current_token: CurrentToken,
}

fn undone_token(
    before: &CurrentToken,
    previous: &Value,
    ack: &Value,
    fresh: &Value,
) -> Result<CurrentToken, String> {
    let error = || "Kết quả hoàn tác chưa khớp bản hiện hành; hãy tải lại để kiểm tra".to_owned();
    let expected = before
        .revision
        .parse::<u64>()
        .ok()
        .and_then(|n| n.checked_add(1))
        .filter(|n| *n <= i64::MAX as u64)
        .ok_or_else(error)?;
    let fields = [
        "schema_version",
        "kind",
        "job_id",
        "revision",
        "snapshot_sha256",
        "graph_sha256",
        "snapshot",
        "active_graph_published",
        "correction_ready",
    ];
    let snapshot_fields = [
        "schema_version",
        "job_id",
        "revision",
        "time_base",
        "findings",
        "last_plan",
    ];
    if ack
        .as_object()
        .is_none_or(|o| o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)))
        || ack["schema_version"].as_u64() != Some(1)
        || ack["kind"] != "review_active_native_state"
        || ack["job_id"] != before.job_id
        || ack["revision"] != expected.to_string()
        || ack["active_graph_published"].as_bool().is_none()
        || ack["correction_ready"] != false
        || ack["snapshot"].as_object().is_none_or(|o| {
            o.len() != snapshot_fields.len() || snapshot_fields.iter().any(|k| !o.contains_key(*k))
        })
        || ack["snapshot"]["schema_version"].as_u64() != Some(2)
        || ack["snapshot"]["job_id"] != before.job_id
        || ack["snapshot"]["revision"].as_u64() != Some(expected)
        || ack["snapshot"]["time_base"] != fresh["time_base"]
        || ack["snapshot"]["findings"] != fresh["findings"]
        || ack["snapshot"]["last_plan"] != fresh["last_plan"]
        || previous["mode"] != fresh["mode"]
        || previous["time_base"] != fresh["time_base"]
    {
        return Err(error());
    }
    let after: CurrentToken =
        serde_json::from_value(fresh["current_token"].clone()).map_err(|_| error())?;
    if after.job_id != before.job_id
        || after.revision != expected.to_string()
        || after.original_manifest_sha256 != before.original_manifest_sha256
        || !is_sha(&after.snapshot_sha256)
        || !is_sha(&after.graph_sha256)
        || after.snapshot_sha256 == before.snapshot_sha256
        || after.graph_sha256 == before.graph_sha256
        || ack["snapshot_sha256"] != after.snapshot_sha256
        || ack["graph_sha256"] != after.graph_sha256
    {
        return Err(error());
    }
    let old = previous["findings"].as_array().ok_or_else(error)?;
    let next = fresh["findings"].as_array().ok_or_else(error)?;
    if old.len() != next.len() {
        return Err(error());
    }
    for (source, restored) in old.iter().zip(next) {
        if !source.is_object() || !restored.is_object() {
            return Err(error());
        }
        let mut preserved = source.clone();
        for key in ["translated_text", "voice", "output_preview"] {
            if !source.as_object().unwrap().contains_key(key)
                || !restored.as_object().unwrap().contains_key(key)
            {
                return Err(error());
            }
            preserved[key] = restored[key].clone();
        }
        if preserved != *restored {
            return Err(error());
        }
    }
    Ok(after)
}

fn current_undo(
    host: ReviewHost,
    session: String,
    id: String,
    input: CurrentUndoRequest,
) -> Result<Value, String> {
    if input.schema_version != 1 || input.kind != "review_current_undo" || !input.confirm {
        return Err("Cần xác nhận hoàn tác bản hiện hành".into());
    }
    job_id(&input.current_token.job_id)?;
    let request = host.begin(&id)?;
    let (pin, before, applying) = host.consume_undo(&session, &input.current_token)?;
    request.observe(&pin.job_id, "undo")?;
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất gốc đã thay đổi; hãy tải lại review".into());
    }
    let previous = current_state(&root, &data, &before.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được bản trước hoàn tác; hãy tải lại")?;
    let actual: CurrentToken = serde_json::from_value(previous["current_token"].clone())
        .map_err(|_| "Token hiện hành không hợp lệ")?;
    if actual != before {
        return Err("Review đã thay đổi trước hoàn tác; hãy tải lại".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-undo-active")
        .arg("--data-root")
        .arg(&data)
        .arg("--job-id")
        .arg(&before.job_id)
        .arg("--revision")
        .arg(&before.revision)
        .arg("--snapshot-sha256")
        .arg(&before.snapshot_sha256)
        .arg("--graph-sha256")
        .arg(&before.graph_sha256);
    // This existing native command physically admits current+prior graphs and
    // performs monotonic CAS under JobExecutionGuard. No producer is executed.
    request.phase("processing");
    let bytes = run_observed(command, JSON_LIMIT, Duration::from_secs(740), &request)?;
    request.phase("verifying");
    let ack: Value =
        serde_json::from_slice(&bytes).map_err(|_| "Kết quả hoàn tác chưa rõ; hãy tải lại")?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Hoàn tác đã bị hủy hoặc dữ liệu đổi; hãy tải lại để kiểm tra".into());
    }
    let fresh = current_state(&root, &data, &before.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được bản sau hoàn tác; hãy tải lại")?;
    let after = undone_token(&before, &previous, &ack, &fresh)?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Kết quả hoàn tác đến sau khi hủy; hãy tải lại để kiểm tra".into());
    }
    host.finish_apply_session(&request, pin, after, &applying)?;
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_undone_current","before_token":previous["current_token"],"session_id":id,"snapshot":fresh}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentExportRequest {
    schema_version: u32,
    kind: String,
    confirm: bool,
    format: String,
    current_token: CurrentToken,
}

fn exported_receipt(
    before: &CurrentToken,
    format: &str,
    mode: &str,
    ack: &Value,
) -> Result<Value, String> {
    let error = || "Bản xuất chưa khớp phiên hiện hành; hãy tải lại để kiểm tra".to_owned();
    let mut fields = vec![
        "schema_version",
        "kind",
        "current_token",
        "generation",
        "path",
        "manifest_sha256",
        "files_verified",
        "standard_bundle_published",
        "production_editable_pack_published",
        "current_generation_checked",
        "correction_ready",
    ];
    if format == "editable" {
        fields.extend([
            "bundle_manifest_sha256",
            "processing_receipt",
            "context_sha256",
            "worker",
            "vendor_execution",
            "active_graph_changed",
        ]);
    }
    let token: CurrentToken =
        serde_json::from_value(ack["current_token"].clone()).map_err(|_| error())?;
    let expected = match (format, mode) {
        ("standard", "b1") => 7,
        ("standard", "b2") => 10,
        ("editable", "b1") => 10,
        ("editable", "b2") => 13,
        _ => return Err(error()),
    };
    if ack
        .as_object()
        .is_none_or(|o| o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)))
        || ack["schema_version"].as_u64() != Some(1)
        || token != *before
        || ack["kind"]
            != if format == "standard" {
                "review_native_current_export_result"
            } else {
                "review_native_current_editable_result"
            }
        || ack["files_verified"].as_u64() != Some(expected)
        || ack["standard_bundle_published"] != true
        || ack["production_editable_pack_published"] != (format == "editable")
        || ack["current_generation_checked"] != true
        || ack["correction_ready"] != false
    {
        return Err(error());
    }
    let generation = ack["generation"].as_str().ok_or_else(error)?;
    let manifest = ack["manifest_sha256"].as_str().ok_or_else(error)?;
    let path = ack["path"].as_str().ok_or_else(error)?;
    if generation.len() != 64
        || !generation
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        || !is_sha(manifest)
        || path.is_empty()
        || path.encode_utf16().count() > 32767
        || path.chars().any(char::is_control)
        || !Path::new(path).is_absolute()
        || Path::new(path)
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err(error());
    }
    let standard_manifest = if format == "editable" {
        let recipe = &ack["processing_receipt"];
        let digest = ack["bundle_manifest_sha256"].as_str().ok_or_else(error)?;
        if !is_sha(digest)
            || !ack["context_sha256"].as_str().is_some_and(is_sha)
            || recipe["schema_version"].as_u64() != Some(1)
            || recipe["kind"] != "review_admitted_editable_pack"
            || recipe["capability"] != "review_editable_pack_v1"
            || !recipe["recipe_identity"].as_str().is_some_and(is_sha)
            || !recipe["execution_identity"].as_str().is_some_and(is_sha)
            || recipe["current_generation_admitted"] != false
            || recipe["model_provisioning"] != false
            || recipe["vendor_execution"] != false
            || recipe["active_graph_published"] != false
            || recipe["correction_ready"] != false
            || ack["vendor_execution"] != false
            || ack["active_graph_changed"] != false
            || !(ack["worker"].is_null() || ack["worker"].is_object())
        {
            return Err(error());
        }
        digest
    } else {
        manifest
    };
    // Only display data crosses this boundary. There is no open-file, media URL
    // or vendor-launch authority attached to the returned path.
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_export_receipt","format":format,"generation":generation,"path":path,
        "manifest_sha256":manifest,"standard_manifest_sha256":standard_manifest,"files_verified":expected,
        "standard_bundle_published":true,"production_editable_pack_published":format=="editable","vendor_execution":false}),
    )
}

fn current_export(
    host: ReviewHost,
    session: String,
    id: String,
    input: CurrentExportRequest,
) -> Result<Value, String> {
    if input.schema_version != 1
        || input.kind != "review_current_export_confirmation"
        || !input.confirm
        || !matches!(input.format.as_str(), "standard" | "editable")
    {
        return Err("Cần xác nhận định dạng xuất cho bản hiện hành".into());
    }
    job_id(&input.current_token.job_id)?;
    let request = host.begin(&id)?;
    let (pin, before, applying) = host.consume_export(&session, &input.current_token)?;
    request.observe(
        &pin.job_id,
        if input.format == "standard" {
            "standard_export"
        } else {
            "editable_export"
        },
    )?;
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất gốc đã đổi; hãy tải lại".into());
    }
    let previous = current_state(&root, &data, &before.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được bản hiện hành; hãy tải lại")?;
    let actual: CurrentToken = serde_json::from_value(previous["current_token"].clone())
        .map_err(|_| "Token xuất không hợp lệ")?;
    if actual != before {
        return Err("Phiên xuất đã đổi; hãy tải lại".into());
    }
    let body = serde_json::json!({"schema_version":1,"kind":"review_current_export_confirmation","confirm":true,"format":input.format,"current_token":previous["current_token"]});
    let encoded = serde_json::to_string(&body).map_err(|_| "Không đọc được xác nhận xuất")?;
    if encoded.encode_utf16().count()
        + root.as_os_str().to_string_lossy().encode_utf16().count()
        + data.as_os_str().to_string_lossy().encode_utf16().count()
        > 28000
    {
        return Err("Yêu cầu xuất vượt giới hạn đường dẫn; hãy tải lại để kiểm tra".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-confirm-current-export")
        .arg("--data-root")
        .arg(&data)
        .arg("--request-json")
        .arg(encoded);
    request.phase("processing");
    let bytes = run_observed(command, JSON_LIMIT, Duration::from_secs(8 * 3600), &request)?;
    request.phase("verifying");
    let ack: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "Kết quả xuất chưa rõ; hãy tải lại để kiểm tra")?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Xuất đã bị hủy hoặc dữ liệu đổi; hãy tải lại".into());
    }
    let receipt = exported_receipt(
        &before,
        &input.format,
        previous["mode"]
            .as_str()
            .ok_or("Chế độ xuất không hợp lệ")?,
        &ack,
    )?;
    let fresh = current_state(&root, &data, &before.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được bản sau xuất; hãy tải lại")?;
    if fresh != previous
        || request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &before.job_id, &request.cancelled)? != pin
    {
        return Err("Review đã đổi trong khi xuất; hãy tải lại để kiểm tra".into());
    }
    host.finish_apply_session(&request, pin, before, &applying)?;
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_exported_current","format":input.format,"current_token":fresh["current_token"],"session_id":id,"snapshot":fresh,"receipt":receipt}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurrentInitializeRequest {
    schema_version: u32,
    kind: String,
    confirm: bool,
    job_id: String,
}

fn initialized_token(
    pin: &ExportPin,
    previous: &Value,
    ack: &Value,
    fresh: &Value,
) -> Result<CurrentToken, String> {
    let error = || "Khởi tạo chưa khớp bản xuất gốc; hãy tải lại để kiểm tra".to_owned();
    let fields = [
        "schema_version",
        "kind",
        "job_id",
        "revision",
        "snapshot_sha256",
        "graph_sha256",
        "manifest_sha256",
        "snapshot",
        "correction_ready",
    ];
    if ack
        .as_object()
        .is_none_or(|o| o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)))
        || ack["schema_version"].as_u64() != Some(1)
        || ack["kind"] != "review_original_state"
        || ack["job_id"] != pin.job_id
        || ack["revision"] != "0"
        || ack["manifest_sha256"] != pin.manifest_sha256
        || ack["correction_ready"] != false
        || previous["schema_version"].as_u64() != Some(2)
        || previous["job_id"] != pin.job_id
        || previous["revision"].as_u64() != Some(0)
        || !previous["last_plan"].is_null()
        || ack["snapshot"] != *previous
        || fresh["time_base"] != previous["time_base"]
        || fresh["findings"] != previous["findings"]
        || fresh["last_plan"] != previous["last_plan"]
    {
        return Err(error());
    }
    let token: CurrentToken =
        serde_json::from_value(fresh["current_token"].clone()).map_err(|_| error())?;
    if token.job_id != pin.job_id
        || token.revision != "0"
        || token.original_manifest_sha256 != pin.manifest_sha256
        || !is_sha(&token.snapshot_sha256)
        || !is_sha(&token.graph_sha256)
        || ack["snapshot_sha256"] != token.snapshot_sha256
        || ack["graph_sha256"] != token.graph_sha256
    {
        return Err(error());
    }
    Ok(token)
}

fn current_initialize(
    host: ReviewHost,
    session: String,
    id: String,
    input: CurrentInitializeRequest,
) -> Result<Value, String> {
    if input.schema_version != 1 || input.kind != "review_initialize_current" || !input.confirm {
        return Err("Cần xác nhận bắt đầu chỉnh sửa bản gốc".into());
    }
    job_id(&input.job_id)?;
    let request = host.begin(&id)?;
    let (pin, previous, applying) = host.consume_initialize(&session, &input.job_id)?;
    request.observe(&pin.job_id, "initialize")?;
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
        || current_state(&root, &data, &input.job_id, &pin, &request.cancelled)?.is_some()
    {
        return Err("Bản gốc hoặc lịch sử đã thay đổi; hãy tải lại".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-original")
        .arg("--root")
        .arg(&root)
        .arg("--data-root")
        .arg(&data)
        .arg("--job-id")
        .arg(&input.job_id)
        .arg("--initialize-original");
    // Existing native initialization owns metadata validation, original pin and
    // the durable job guard. No inference, render or current-generation repair.
    request.phase("processing");
    let bytes = run_observed(command, JSON_LIMIT, Duration::from_secs(740), &request)?;
    request.phase("verifying");
    let ack: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "Kết quả khởi tạo chưa rõ; hãy tải lại để kiểm tra")?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
    {
        return Err("Khởi tạo bị hủy hoặc bản gốc đổi; hãy tải lại để kiểm tra".into());
    }
    let fresh = current_state(&root, &data, &input.job_id, &pin, &request.cancelled)?
        .ok_or("Không đọc được lịch sử vừa tạo; hãy tải lại")?;
    let token = initialized_token(&pin, &previous, &ack, &fresh)?;
    if request.cancelled.load(Ordering::SeqCst)
        || trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
    {
        return Err("Kết quả khởi tạo đến sau khi hủy; hãy tải lại để kiểm tra".into());
    }
    host.finish_apply_session(&request, pin, token, &applying)?;
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_desktop_initialized_current","job_id":input.job_id,"session_id":id,"snapshot":fresh}),
    )
}

fn bounded_diagnostic(text: &str, limit: usize) -> String {
    let mut out = String::new();
    for ch in text
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
    {
        if out.len() + ch.len_utf8() > limit {
            break;
        }
        out.push(ch);
    }
    out
}

async fn review_task_reply(
    host: ReviewHost,
    id: String,
    work: impl FnOnce() -> Result<Value, String> + Send + 'static,
) -> Result<Value, Value> {
    match tauri::async_runtime::spawn_blocking(work).await {
        Ok(result) => result.map_err(|message| host.request_failure(&id, &message)),
        Err(_) => Err(host.request_failure(
            &id,
            "Tác vụ review bị gián đoạn; hãy tải lại để kiểm tra bản hiện hành",
        )),
    }
}

/// Readers drain both pipes concurrently. A finite deadline covers process
/// completion AND pipe EOF; no unbounded wait/join runs on the UI thread.
fn run_bounded(
    command: Command,
    limit: usize,
    timeout: Duration,
    cancelled: &AtomicBool,
) -> Result<Vec<u8>, String> {
    run_bounded_inner(command, limit, timeout, cancelled, None)
}

fn run_observed(
    mut command: Command,
    limit: usize,
    timeout: Duration,
    request: &RequestGuard,
) -> Result<Vec<u8>, String> {
    command.env("DUBFLOW_REVIEW_PROGRESS_V1", "1");
    run_bounded_inner(command, limit, timeout, &request.cancelled, Some(request))
}

fn run_bounded_inner(
    mut command: Command,
    limit: usize,
    timeout: Duration,
    cancelled: &AtomicBool,
    observer: Option<&RequestGuard>,
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
    if let Some(request) = observer {
        request.process("running");
    }
    let mut last_observed = Instant::now();
    let stdout = child.stdout.take().ok_or("Bộ đọc review thiếu stdout")?;
    let stderr = child.stderr.take().ok_or("Bộ đọc review thiếu stderr")?;
    let (sender, receiver) = mpsc::channel();
    let error_sender = sender.clone();
    std::thread::spawn(move || {
        let _ = sender.send((true, capture(stdout, limit)));
    });
    let stage_sink = observer.map(|r| StageSink {
        host: r.host.clone(),
        id: r.id.clone(),
    });
    std::thread::spawn(move || {
        let bytes = match stage_sink {
            Some(sink) => capture_stage_stderr(stderr, sink),
            None => capture(stderr, ERROR_LIMIT),
        };
        let _ = error_sender.send((false, bytes));
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
                Ok(value) => {
                    status = value;
                    if status.is_some() || last_observed.elapsed() >= Duration::from_millis(250) {
                        if let Some(request) = observer {
                            request.process(if status.is_some() {
                                "exited"
                            } else {
                                "running"
                            });
                        }
                        last_observed = Instant::now();
                    }
                }
                Err(_) => break Err("Không đọc được trạng thái bộ đọc review".into()),
            }
        }
        if let (Some(status), Some(_), Some(stderr)) = (&status, &output, &errors) {
            if !status.success() {
                if let Some(request) = observer {
                    request.failing_stderr(stderr);
                }
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
        let mut reaped = false;
        while Instant::now() < reap_deadline {
            match child.try_wait() {
                Ok(Some(_)) => {
                    reaped = true;
                    break;
                }
                Err(_) => break,
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            }
        }
        if let Some(request) = observer {
            request.process(if reaped { "exited" } else { "unobserved" });
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
    let uninitialized = matches!(&binding, SessionBinding::Uninitialized);
    host.save_bound_session(&request, pin, binding)?;
    if uninitialized {
        host.save_original_view(&request, &snapshot)?;
    }
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
    host.save_plan(&session, &token, &request, result.clone())?;
    Ok(result)
}

#[derive(Clone, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum ResultVideoExpected {
    Original { revision: u64 },
    Current { current_token: CurrentToken },
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResultVideoRequest {
    schema_version: u32,
    kind: String,
    job_id: String,
    expected: ResultVideoExpected,
}

fn validate_result_video_request(input: &ResultVideoRequest) -> Result<(), String> {
    job_id(&input.job_id)?;
    if input.schema_version != 1 || input.kind != "review_open_result_request" {
        return Err("Yêu cầu xem video không được hỗ trợ".into());
    }
    match &input.expected {
        ResultVideoExpected::Original { revision: 0 } => Ok(()),
        ResultVideoExpected::Current {
            current_token: token,
        } => {
            let revision = token
                .revision
                .parse::<u64>()
                .map_err(|_| "Token video không hợp lệ")?;
            if token.job_id != input.job_id
                || revision > i64::MAX as u64
                || revision.to_string() != token.revision
                || !is_sha(&token.snapshot_sha256)
                || !is_sha(&token.graph_sha256)
                || !is_sha(&token.original_manifest_sha256)
            {
                return Err("Token video không hợp lệ".into());
            }
            Ok(())
        }
        _ => Err("Video gốc chỉ được mở từ phiên bản 0 chưa khởi tạo".into()),
    }
}

fn result_video_expected_json(expected: &ResultVideoExpected) -> Value {
    match expected {
        ResultVideoExpected::Original { revision } => {
            serde_json::json!({"kind":"original","revision":revision})
        }
        ResultVideoExpected::Current {
            current_token: token,
        } => serde_json::json!({"kind":"current","current_token":{
            "job_id":token.job_id,"revision":token.revision,"snapshot_sha256":token.snapshot_sha256,
            "graph_sha256":token.graph_sha256,"original_manifest_sha256":token.original_manifest_sha256}}),
    }
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum NativeResultVideoExpected {
    Original {
        revision: u64,
        original_manifest_sha256: String,
    },
    Current {
        current_token: CurrentToken,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeResultVideoLaunch {
    schema_version: u32,
    kind: String,
    job_id: String,
    expected: NativeResultVideoExpected,
    role: String,
    content_sha256: String,
    size_bytes: String,
    launch_requested: bool,
    managed_playback: bool,
    playback_verified: bool,
}

fn validate_result_video_launch(
    bytes: &[u8],
    input: &ResultVideoRequest,
    pin: &ExportPin,
) -> Result<NativeResultVideoLaunch, String> {
    let error = || {
        "Chưa xác nhận được yêu cầu mở video; hãy kiểm tra trình phát, không tự gửi lại".to_owned()
    };
    validate_result_video_request(input)?;
    if input.job_id != pin.job_id {
        return Err(error());
    }
    if bytes.len() > 4096 {
        return Err(error());
    }
    let ack: NativeResultVideoLaunch = serde_json::from_slice(bytes).map_err(|_| error())?;
    let bound = match (&input.expected, &ack.expected) {
        (
            ResultVideoExpected::Original { revision: 0 },
            NativeResultVideoExpected::Original {
                revision: 0,
                original_manifest_sha256,
            },
        ) => original_manifest_sha256 == &pin.manifest_sha256,
        (
            ResultVideoExpected::Current { current_token },
            NativeResultVideoExpected::Current {
                current_token: actual,
            },
        ) => {
            current_token == actual && current_token.original_manifest_sha256 == pin.manifest_sha256
        }
        _ => false,
    };
    let size = ack.size_bytes.parse::<u64>().map_err(|_| error())?;
    if ack.schema_version != 1
        || ack.kind != "review_native_result_video_launch_requested"
        || ack.job_id != input.job_id
        || !bound
        || ack.role != "final_video"
        || !is_sha(&ack.content_sha256)
        || size == 0
        || size > 128 * 1024 * 1024 * 1024
        || size.to_string() != ack.size_bytes
        || !ack.launch_requested
        || ack.managed_playback
        || ack.playback_verified
    {
        return Err(error());
    }
    Ok(ack)
}

fn open_result_video(
    host: ReviewHost,
    session: String,
    id: String,
    input: ResultVideoRequest,
) -> Result<Value, String> {
    validate_result_video_request(&input)?;
    let request = host.begin(&id)?;
    let pin = host.result_video_session(&session, &input)?;
    let root = super::version_root()?;
    let data = data_root()?;
    if trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin {
        return Err("Bản xuất đã thay đổi; hãy tải lại lời thoại".into());
    }
    let expected = result_video_expected_json(&input.expected);
    let mut native_expected = expected.clone();
    if matches!(&input.expected, ResultVideoExpected::Original { .. }) {
        native_expected
            .as_object_mut()
            .ok_or("Yêu cầu video không hợp lệ")?
            .insert(
                "original_manifest_sha256".into(),
                Value::String(pin.manifest_sha256.clone()),
            );
    }
    let native_request = serde_json::to_string(&serde_json::json!({"schema_version":1,
        "kind":"review_native_result_video_request","job_id":input.job_id,"expected":native_expected}))
        .map_err(|_| "Không đóng gói được yêu cầu video")?;
    if native_request.len() > 4096
        || request.cancelled.load(Ordering::SeqCst)
        || host.result_video_session(&session, &input)? != pin
    {
        return Err("Phiên xem video đã đổi hoặc bị hủy".into());
    }
    let mut command = Command::new(owned_file(&root, "app/bin/dubflow-supervisor.exe")?);
    command
        .arg("review-open-result")
        .arg("--data-root")
        .arg(&data)
        .arg("--request-json")
        .arg(native_request);
    let bytes = run_bounded(command, 4096, Duration::from_secs(740), &request.cancelled)?;
    let ack = validate_result_video_launch(&bytes, &input, &pin)?;
    // OS activation may already have happened. Refusal/cancellation here is an
    // uncertain launch, never permission to retry or proof no player opened.
    if request.cancelled.load(Ordering::SeqCst)
        || host.result_video_session(&session, &input)? != pin
        || trusted_pin(&root, &data, &input.job_id, &request.cancelled)? != pin
    {
        return Err(
            "Phiên xem video đã đổi; chưa xác nhận được yêu cầu mở, không tự gửi lại".into(),
        );
    }
    Ok(
        serde_json::json!({"schema_version":1,"kind":"review_result_video_launch_requested","job_id":input.job_id,
        "session_id":session,"request_id":id,"expected":expected,"role":"final_video",
        "content_sha256":ack.content_sha256,"size_bytes":ack.size_bytes,"launch_requested":true,
        "managed_playback":false,"playback_verified":false}),
    )
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
pub async fn review_open_result_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: ResultVideoRequest,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        open_result_video(host, session_id, request_id, request)
    })
    .await
    .map_err(|_| {
        "Yêu cầu mở video bị gián đoạn; hãy kiểm tra trình phát trước khi tiếp tục".to_owned()
    })?
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
pub async fn review_apply_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    confirmation: CurrentConfirmation,
) -> Result<Value, Value> {
    let host = host.inner().capture_failures();
    let errors = host.clone();
    let id = request_id.clone();
    review_task_reply(errors, id, move || {
        current_apply(host, session_id, request_id, confirmation)
    })
    .await
}

#[tauri::command]
pub async fn review_undo_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentUndoRequest,
) -> Result<Value, Value> {
    let host = host.inner().capture_failures();
    let errors = host.clone();
    let id = request_id.clone();
    review_task_reply(errors, id, move || {
        current_undo(host, session_id, request_id, request)
    })
    .await
}

#[tauri::command]
pub async fn review_export_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentExportRequest,
) -> Result<Value, Value> {
    let host = host.inner().capture_failures();
    let errors = host.clone();
    let id = request_id.clone();
    review_task_reply(errors, id, move || {
        current_export(host, session_id, request_id, request)
    })
    .await
}

#[tauri::command]
pub async fn review_initialize_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentInitializeRequest,
) -> Result<Value, Value> {
    let host = host.inner().capture_failures();
    let errors = host.clone();
    let id = request_id.clone();
    review_task_reply(errors, id, move || {
        current_initialize(host, session_id, request_id, request)
    })
    .await
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
pub fn review_request_stage_v1(
    host: tauri::State<'_, ReviewHost>,
    request_id: String,
) -> Result<Value, String> {
    host.request_stage(&request_id)
}

#[tauri::command]
pub fn review_request_status_v1(
    host: tauri::State<'_, ReviewHost>,
    request_id: String,
) -> Result<Value, String> {
    host.request_status(&request_id)
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
    fn failure_details_are_bounded_utf8_inert_text_and_keep_unconfirmed_outcome() {
        let cleaned =
            bounded_diagnostic(&format!("a\0\u{1b}b\n\t{}", "😀".repeat(3000)), ERROR_LIMIT);
        assert!(cleaned.len() <= ERROR_LIMIT);
        assert!(cleaned.starts_with("ab\n\t"));
        assert!(!cleaned
            .chars()
            .any(|c| c.is_control() && c != '\n' && c != '\t'));
        let host = ReviewHost::default().capture_failures();
        assert!(host
            .request_failure("request-unknown-error-0001", "unknown")
            .is_string());
        let request = host.begin("request-error-bounded-0001").unwrap();
        request.observe("job-error", "apply").unwrap();
        request.phase("processing");
        request.failing_stderr(b"QC refused: <tag> is diagnostic text, never a command");
        let id = request.id.clone();
        let duplicate = host.capture_failures();
        assert!(duplicate.begin(&id).is_err());
        assert!(duplicate
            .request_failure(&id, "duplicate refused before registration")
            .is_string());
        drop(request);
        let error = host.request_failure(&id, "Reload to inspect the current result");
        assert_eq!(error.as_object().unwrap().len(), 10);
        assert_eq!(error["outcome"], "unconfirmed");
        assert_eq!(error["phase"], "processing");
        assert_eq!(error["request_id"], id);
        assert_eq!(error["job_id"], "job-error");
        assert!(error["diagnostic"].as_str().unwrap().contains("<tag>"));
        for key in [
            "retryable",
            "committed",
            "success",
            "token",
            "snapshot",
            "root",
        ] {
            assert!(error.get(key).is_none());
        }
        host.registry
            .lock()
            .unwrap()
            .requests
            .get_mut(&id)
            .unwrap()
            .changed = Instant::now() - TOMBSTONE_TTL;
        assert!(host.request_failure(&id, "expired observation").is_string());
    }

    #[test]
    fn failing_owned_process_preserves_its_capped_diagnostic_in_the_bound_error() {
        let host = ReviewHost::default().capture_failures();
        let errors = host.clone();
        let id = "request-failure-process-0001".to_string();
        let result_id = id.clone();
        let result =
            tauri::async_runtime::block_on(review_task_reply(errors, id.clone(), move || {
                let request = host.begin(&id)?;
                request.observe("job-error-process", "apply")?;
                request.phase("processing");
                run_observed(
                    fixture("failed-diagnostic"),
                    4096,
                    Duration::from_secs(5),
                    &request,
                )?;
                Ok(serde_json::json!({"must_not_succeed":true}))
            }));
        let error = result.unwrap_err();
        assert_eq!(error["request_id"], result_id);
        assert_eq!(error["phase"], "processing");
        assert_eq!(error["outcome"], "unconfirmed");
        assert!(error["diagnostic"]
            .as_str()
            .unwrap()
            .contains("REVIEW_QC_DIAGNOSTIC_FIXTURE"));
    }

    #[test]
    fn no_diagnostic_is_invented_for_unobserved_or_successful_processes() {
        let host = ReviewHost::default().capture_failures();
        let request = host.begin("request-no-failed-stderr-0001").unwrap();
        request.observe("job-no-failed-stderr", "undo").unwrap();
        request.phase("processing");
        run_observed(fixture("small"), 4096, Duration::from_secs(5), &request).unwrap();
        request.phase("verifying");
        let id = request.id.clone();
        drop(request);
        let failure = host.request_failure(&id, "A later current-state check failed");
        assert_eq!(failure["phase"], "verifying");
        assert!(failure["diagnostic"].is_null());
        assert_eq!(failure["outcome"], "unconfirmed");
    }

    fn stage_packet(sequence: u64, units: u64) -> Vec<u8> {
        serde_json::to_vec(
            &serde_json::json!({"schema_version":1,"kind":"review_native_stage_observation",
            "sequence":sequence,"stage_epoch":1,"stage":"verifying_runtime_files","elapsed_ms":0,
            "units_completed":units,"bytes_verified":units * 10}),
        )
        .unwrap()
    }

    #[test]
    fn native_stage_reader_preserves_diagnostics_caps_and_closed_observation() {
        let host = ReviewHost::default();
        let request = host.begin("request-stage-reader-0001").unwrap();
        request.observe("job-stage-reader", "apply").unwrap();
        request.phase("processing");
        let sink = StageSink {
            host: host.clone(),
            id: request.id.clone(),
        };
        let packet = stage_packet(1, 3);
        let mut raw = b"warning before\n".to_vec();
        raw.extend_from_slice(STAGE_PREFIX);
        raw.extend_from_slice(&packet);
        raw.extend_from_slice(b"\nwarning after\n");
        assert_eq!(
            capture_stage_stderr(std::io::Cursor::new(raw), sink.clone()).unwrap(),
            b"warning before\nwarning after\n"
        );
        let status = host.request_stage(&request.id).unwrap();
        assert_eq!(status["native_stage"]["event"]["units_completed"], 3);
        assert_eq!(status["activity"]["state"], "running");
        assert!(status.get("success").is_none());
        // The old activity shape remains closed and unchanged.
        assert_eq!(
            host.request_status(&request.id)
                .unwrap()
                .as_object()
                .unwrap()
                .len(),
            11
        );
        let mut malformed = STAGE_PREFIX.to_vec();
        malformed.extend_from_slice(b"{\"success\":true}\n");
        assert_eq!(
            capture_stage_stderr(std::io::Cursor::new(&malformed), sink.clone()).unwrap(),
            malformed
        );
        assert_eq!(
            host.request_stage(&request.id).unwrap()["native_observation"],
            "unavailable"
        );
        let ordinary = vec![b'x'; ERROR_LIMIT];
        assert_eq!(
            capture_stage_stderr(std::io::Cursor::new(&ordinary), sink.clone()).unwrap(),
            ordinary
        );
        assert!(
            capture_stage_stderr(std::io::Cursor::new(vec![b'x'; ERROR_LIMIT + 1]), sink).is_err()
        );
    }

    #[test]
    fn repeated_counts_do_not_reset_advancement_and_reordered_events_are_unknown() {
        let host = ReviewHost::default();
        let request = host.begin("request-stage-order-0001").unwrap();
        request.observe("job-stage-order", "apply").unwrap();
        request.phase("processing");
        let sink = StageSink {
            host: host.clone(),
            id: request.id.clone(),
        };
        sink.observe(NativeStage::read(&stage_packet(1, 2)).unwrap());
        let advanced = Instant::now() - Duration::from_secs(3);
        host.registry
            .lock()
            .unwrap()
            .requests
            .get_mut(&request.id)
            .unwrap()
            .activity
            .as_mut()
            .unwrap()
            .native
            .as_mut()
            .unwrap()
            .advanced = advanced;
        sink.observe(NativeStage::read(&stage_packet(2, 2)).unwrap());
        assert!(
            host.request_stage(&request.id).unwrap()["native_stage"]["advance_age_ms"]
                .as_u64()
                .unwrap()
                >= 3000
        );
        sink.observe(NativeStage::read(&stage_packet(1, 9)).unwrap());
        assert_eq!(
            host.request_stage(&request.id).unwrap()["native_observation"],
            "unavailable"
        );
        sink.observe(NativeStage::read(&stage_packet(3, 9)).unwrap());
        assert_eq!(
            host.request_stage(&request.id).unwrap()["native_observation"],
            "unavailable"
        );
        request.phase("processing");
        host.cancel(&request.id).unwrap();
        sink.observe(NativeStage::read(&stage_packet(4, 10)).unwrap());
        assert_eq!(
            host.request_stage(&request.id).unwrap()["native_observation"],
            "unavailable"
        );
    }

    #[test]
    fn activity_binds_only_admitted_requests_and_never_promises_success() {
        let host = ReviewHost::default();
        assert!(host.request_status("request-unknown-0001").is_err());
        host.cancel("request-before-activity-0001").unwrap();
        assert!(host.request_status("request-before-activity-0001").is_err());
        let request = host.begin("request-observed-0001").unwrap();
        assert!(host.request_status(&request.id).is_err());
        request.observe("job-observed", "apply").unwrap();
        assert!(request.observe("different-job", "undo").is_err());
        let initial = host.request_status(&request.id).unwrap();
        assert_eq!(initial["job_id"], "job-observed");
        assert_eq!(initial["phase"], "checking");
        assert!(initial["process_observation_age_ms"].is_null());
        request.phase("processing");
        request.process("running");
        let running = host.request_status(&request.id).unwrap();
        assert_eq!(running["process_state"], "running");
        assert_eq!(running.as_object().unwrap().len(), 11);
        assert!(running.get("success").is_none());
        host.cancel(&request.id).unwrap();
        assert_eq!(
            host.request_status(&request.id).unwrap()["state"],
            "cancelling"
        );
        request.process("exited");
        let id = request.id.clone();
        drop(request);
        let ended = host.request_status(&id).unwrap();
        assert_eq!(ended["state"], "ended");
        assert_eq!(ended["phase"], "ended");
        assert_eq!(ended["process_state"], "exited");
        assert!(ended.get("success").is_none());
        let mut registry = host.registry.lock().unwrap();
        registry.requests.get_mut(&id).unwrap().changed = Instant::now() - TOMBSTONE_TTL;
        drop(registry);
        assert!(host.request_status(&id).is_err());
    }

    #[test]
    fn observed_process_failure_and_timeout_stay_errors() {
        let host = ReviewHost::default();
        for mode in ["small", "failed", "sleep"] {
            let request = host.begin(&format!("request-process-{mode}-0001")).unwrap();
            request.observe("job-process", "apply").unwrap();
            request.phase("processing");
            let result = run_observed(
                fixture(mode),
                4096,
                if mode == "sleep" {
                    Duration::from_millis(100)
                } else {
                    Duration::from_secs(5)
                },
                &request,
            );
            assert_eq!(result.is_ok(), mode == "small");
            let activity = host.request_status(&request.id).unwrap();
            assert_eq!(activity["process_state"], "exited");
            // Even a zero exit has not passed native reply/current validation.
            assert_eq!(activity["state"], "running");
            request.phase("verifying");
            assert!(
                host.request_status(&request.id).unwrap()["process_observation_age_ms"].is_null()
            );
        }
    }

    #[test]
    fn observed_running_child_can_be_cancelled_without_a_success_receipt() {
        let host = ReviewHost::default();
        let request = host.begin("request-observed-cancel-0001").unwrap();
        request.observe("job-observed-cancel", "apply").unwrap();
        request.phase("processing");
        let worker = std::thread::spawn(move || {
            run_observed(fixture("sleep"), 4096, Duration::from_secs(5), &request)
        });
        let deadline = Instant::now() + Duration::from_secs(4);
        loop {
            let status = host.request_status("request-observed-cancel-0001").unwrap();
            if status["process_state"] == "running" {
                assert_eq!(status["state"], "running");
                break;
            }
            assert!(Instant::now() < deadline, "child did not become observable");
            std::thread::sleep(Duration::from_millis(10));
        }
        host.cancel("request-observed-cancel-0001").unwrap();
        assert!(worker.join().unwrap().is_err());
        let ended = host.request_status("request-observed-cancel-0001").unwrap();
        assert_eq!(ended["state"], "ended");
        assert_eq!(ended["process_state"], "exited");
        assert!(ended.get("success").is_none());
    }

    fn apply_fixture() -> (ReviewHost, CurrentToken, Value) {
        let host = ReviewHost::default();
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-apply".into(),
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
        let plan = serde_json::json!({"current_token":{"job_id":token.job_id,"revision":token.revision,"snapshot_sha256":token.snapshot_sha256,"graph_sha256":token.graph_sha256,"original_manifest_sha256":token.original_manifest_sha256},
            "candidate_revision":"9007199254740994","mode":"b2","cue_id":"cue-a","changes":{"translation":"Changed"}});
        let request = host.begin("request-apply-session-0001").unwrap();
        host.save_bound_session(&request, pin, SessionBinding::Current(token.clone()))
            .unwrap();
        drop(request);
        (host, token, plan)
    }

    #[test]
    fn initialization_consumes_only_an_absent_current_session_with_saved_original_view() {
        let (host, token, _) = apply_fixture();
        assert!(host
            .consume_initialize("request-apply-session-0001", &token.job_id)
            .is_err());
        let pin = host
            .current_session("request-apply-session-0001", &token.job_id)
            .unwrap()
            .0;
        let original = serde_json::json!({"schema_version":2,"job_id":token.job_id,"revision":0,"time_base":{"numerator":"1","denominator":"1000"},"findings":[],"last_plan":null});
        let legacy = host.begin("request-initial-legacy-0001").unwrap();
        host.save_session(&legacy, pin.clone()).unwrap();
        assert!(host.save_original_view(&legacy, &original).is_err());
        assert!(host.consume_initialize(&legacy.id, &token.job_id).is_err());
        let uninitialized = host.begin("request-initial-original-0002").unwrap();
        host.save_bound_session(&uninitialized, pin.clone(), SessionBinding::Uninitialized)
            .unwrap();
        assert!(host
            .consume_initialize(&uninitialized.id, &token.job_id)
            .is_err());
        host.save_original_view(&uninitialized, &original).unwrap();
        let (bound, view, guard) = host
            .consume_initialize(&uninitialized.id, &token.job_id)
            .unwrap();
        assert_eq!(bound, pin);
        assert_eq!(view, original);
        assert!(host
            .current_session("request-apply-session-0001", &token.job_id)
            .is_err());
        assert!(host
            .consume_initialize(&uninitialized.id, &token.job_id)
            .is_err());
        let racing = host.begin("request-initial-racing-0003").unwrap();
        assert!(host
            .save_bound_session(&racing, pin.clone(), SessionBinding::Uninitialized)
            .is_err());
        host.cancel(&racing.id).unwrap();
        assert!(host
            .finish_apply_session(&racing, pin, token.clone(), &guard)
            .is_err());
        drop(guard);
        assert!(!host
            .registry
            .lock()
            .unwrap()
            .applying_jobs
            .contains(&token.job_id));
    }

    #[test]
    fn initialize_request_is_explicit_closed_and_does_not_accept_current_or_browser_roots() {
        let (host, token, _) = apply_fixture();
        let request = CurrentInitializeRequest {
            schema_version: 1,
            kind: "review_initialize_current".into(),
            confirm: false,
            job_id: token.job_id.clone(),
        };
        assert!(current_initialize(
            host.clone(),
            "request-apply-session-0001".into(),
            "request-initial-false-0001".into(),
            request
        )
        .is_err());
        assert!(host
            .current_session("request-apply-session-0001", &token.job_id)
            .is_ok());
        let raw = serde_json::json!({"schema_version":1,"kind":"review_initialize_current","confirm":true,"job_id":token.job_id,"root":"foreign"});
        assert!(serde_json::from_value::<CurrentInitializeRequest>(raw).is_err());
    }

    #[test]
    fn original_initialization_ack_matches_full_unchanged_revision_zero_evidence() {
        let (host, before, _) = apply_fixture();
        let pin = host
            .current_session("request-apply-session-0001", &before.job_id)
            .unwrap()
            .0;
        let previous = serde_json::json!({"schema_version":2,"job_id":before.job_id,"revision":0,"time_base":{"numerator":"1","denominator":"1000"},"findings":[
            {"source_text":"Source","translated_text":"Original","confidence":"0.9838564395904541","cue_id":"cue-a","range":{"start_ticks":"10","end_ticks":"20"},"output_preview":"original-preview"}],"last_plan":null});
        let ack = serde_json::json!({"schema_version":1,"kind":"review_original_state","job_id":before.job_id,"revision":"0","snapshot_sha256":before.snapshot_sha256,
            "graph_sha256":before.graph_sha256,"manifest_sha256":before.original_manifest_sha256,"snapshot":previous,"correction_ready":false});
        let fresh = serde_json::json!({"current_token":{"job_id":before.job_id,"revision":"0","snapshot_sha256":before.snapshot_sha256,"graph_sha256":before.graph_sha256,"original_manifest_sha256":before.original_manifest_sha256},
            "time_base":previous["time_base"],"findings":previous["findings"],"last_plan":null});
        assert_eq!(
            initialized_token(&pin, &previous, &ack, &fresh)
                .unwrap()
                .revision,
            "0"
        );
        for change in 0..10 {
            let mut a = ack.clone();
            let mut f = fresh.clone();
            match change {
                0 => a["manifest_sha256"] = serde_json::json!("foreign"),
                1 => a["revision"] = serde_json::json!("1"),
                2 => f["current_token"]["revision"] = serde_json::json!("1"),
                3 => a["snapshot"]["findings"][0]["source_text"] = serde_json::json!("foreign"),
                4 => f["findings"][0]["translated_text"] = serde_json::json!("foreign"),
                5 => f["findings"][0]["confidence"] = serde_json::json!("0.98"),
                6 => f["findings"][0]["range"]["start_ticks"] = serde_json::json!("0"),
                7 => f["findings"][0]["output_preview"] = Value::Null,
                8 => f["current_token"]["graph_sha256"] = serde_json::json!("foreign"),
                _ => f["last_plan"] = serde_json::json!({"unexpected":true}),
            }
            assert!(initialized_token(&pin, &previous, &a, &f).is_err());
        }
    }

    #[test]
    fn export_shares_the_job_guard_and_rotates_the_session_without_advancing_the_token() {
        let (host, token, plan) = apply_fixture();
        let mut foreign = token.clone();
        foreign.original_manifest_sha256 = format!("sha256:{}", "f".repeat(64));
        assert!(host
            .consume_export("request-apply-session-0001", &foreign)
            .is_err());
        let (pin, before, guard) = host
            .consume_export("request-apply-session-0001", &token)
            .unwrap();
        assert!(host
            .consume_plan("request-apply-session-0001", &plan)
            .is_err());
        assert!(host
            .consume_undo("request-apply-session-0001", &token)
            .is_err());
        let request = host.begin("request-export-complete-0001").unwrap();
        assert!(host
            .save_bound_session(
                &request,
                pin.clone(),
                SessionBinding::Current(token.clone())
            )
            .is_err());
        host.finish_apply_session(&request, pin, before, &guard)
            .unwrap();
        assert_eq!(
            host.current_session(&request.id, &token.job_id)
                .unwrap()
                .1
                .revision,
            token.revision
        );
        assert!(host
            .registry
            .lock()
            .unwrap()
            .sessions
            .get(&request.id)
            .unwrap()
            .pending_plan
            .is_none());
    }

    #[test]
    fn standard_and_editable_receipts_require_native_current_identity_and_correct_publication_flags(
    ) {
        let (_, before, plan) = apply_fixture();
        let path = std::env::temp_dir().join("export-receipt-no-io");
        let standard = serde_json::json!({"schema_version":1,"kind":"review_native_current_export_result","current_token":plan["current_token"],"generation":"d".repeat(64),"path":path,
            "manifest_sha256":format!("sha256:{}","e".repeat(64)),"files_verified":10,"standard_bundle_published":true,"production_editable_pack_published":false,"current_generation_checked":true,"correction_ready":false});
        let receipt = exported_receipt(&before, "standard", "b2", &standard).unwrap();
        assert_eq!(
            receipt["standard_manifest_sha256"],
            standard["manifest_sha256"]
        );
        assert_eq!(receipt["vendor_execution"], false);
        assert!(exported_receipt(&before, "standard", "b1", &standard).is_err());
        let mut editable = standard.clone();
        editable["kind"] = serde_json::json!("review_native_current_editable_result");
        editable["files_verified"] = serde_json::json!(13);
        editable["production_editable_pack_published"] = serde_json::json!(true);
        editable["bundle_manifest_sha256"] = standard["manifest_sha256"].clone();
        editable["manifest_sha256"] = serde_json::json!(format!("sha256:{}", "f".repeat(64)));
        editable["context_sha256"] = serde_json::json!(format!("sha256:{}", "1".repeat(64)));
        editable["processing_receipt"] = serde_json::json!({"schema_version":1,"kind":"review_admitted_editable_pack","capability":"review_editable_pack_v1","recipe_identity":format!("sha256:{}","2".repeat(64)),"execution_identity":format!("sha256:{}","3".repeat(64)),"current_generation_admitted":false,"model_provisioning":false,"vendor_execution":false,"active_graph_published":false,"correction_ready":false});
        editable["worker"] = Value::Null;
        editable["vendor_execution"] = serde_json::json!(false);
        editable["active_graph_changed"] = serde_json::json!(false);
        assert_eq!(
            exported_receipt(&before, "editable", "b2", &editable).unwrap()
                ["standard_manifest_sha256"],
            standard["manifest_sha256"]
        );
        for change in 0..10 {
            let mut bad = editable.clone();
            match change {
                0 => bad["current_token"]["revision"] = serde_json::json!("0"),
                1 => {
                    bad["current_token"]["original_manifest_sha256"] = serde_json::json!("foreign")
                }
                2 => bad["standard_bundle_published"] = serde_json::json!(false),
                3 => bad["current_generation_checked"] = serde_json::json!(false),
                4 => bad["files_verified"] = serde_json::json!(10),
                5 => bad["processing_receipt"]["model_provisioning"] = serde_json::json!(true),
                6 => bad["vendor_execution"] = serde_json::json!(true),
                7 => bad["active_graph_changed"] = serde_json::json!(true),
                8 => bad["path"] = serde_json::json!("../foreign"),
                _ => bad["manifest_sha256"] = serde_json::json!("foreign"),
            }
            assert!(exported_receipt(&before, "editable", "b2", &bad).is_err());
        }
    }

    #[test]
    fn undo_consumes_full_token_and_shares_the_apply_job_guard() {
        let (host, token, plan) = apply_fixture();
        let mut foreign = token.clone();
        foreign.graph_sha256 = format!("sha256:{}", "f".repeat(64));
        assert!(host
            .consume_undo("request-apply-session-0001", &foreign)
            .is_err());
        assert!(host
            .current_session("request-apply-session-0001", &token.job_id)
            .is_ok());
        let planning = host.begin("request-undo-plan-0002").unwrap();
        host.save_plan(
            "request-apply-session-0001",
            &token,
            &planning,
            plan.clone(),
        )
        .unwrap();
        let (pin, _, guard) = host
            .consume_undo("request-apply-session-0001", &token)
            .unwrap();
        assert!(host
            .consume_plan("request-apply-session-0001", &plan)
            .is_err());
        assert!(host
            .consume_undo("request-apply-session-0001", &token)
            .is_err());
        let racing = host.begin("request-undo-racing-0003").unwrap();
        assert!(host
            .save_bound_session(&racing, pin.clone(), SessionBinding::Current(token.clone()))
            .is_err());
        host.cancel(&racing.id).unwrap();
        assert!(host
            .finish_apply_session(&racing, pin.clone(), token.clone(), &guard)
            .is_err());
        drop(guard);
        assert!(host
            .current_session("request-apply-session-0001", &token.job_id)
            .is_err());
        let reload = host.begin("request-undo-reload-0004").unwrap();
        host.save_bound_session(&reload, pin, SessionBinding::Current(token.clone()))
            .unwrap();
        assert!(host.consume_undo(&reload.id, &token).is_ok());
    }

    #[test]
    fn undo_requires_explicit_closed_request_before_runtime_and_refuses_initial_revision() {
        let (host, token, _) = apply_fixture();
        let request = CurrentUndoRequest {
            schema_version: 1,
            kind: "review_current_undo".into(),
            confirm: false,
            current_token: token.clone(),
        };
        assert!(current_undo(
            host.clone(),
            "request-apply-session-0001".into(),
            "request-undo-false-0001".into(),
            request
        )
        .is_err());
        assert!(host
            .current_session("request-apply-session-0001", &token.job_id)
            .is_ok());
        let raw = serde_json::json!({"schema_version":1,"kind":"review_current_undo","confirm":true,"current_token":{
            "job_id":token.job_id,"revision":token.revision,"snapshot_sha256":token.snapshot_sha256,"graph_sha256":token.graph_sha256,"original_manifest_sha256":token.original_manifest_sha256},"target_path":"foreign"});
        assert!(serde_json::from_value::<CurrentUndoRequest>(raw).is_err());
        let pin = host
            .current_session("request-apply-session-0001", &token.job_id)
            .unwrap()
            .0;
        for revision in ["0", "9223372036854775807"] {
            let mut t = token.clone();
            t.revision = revision.into();
            let id = format!("request-undo-bound-{}", revision);
            let request = host.begin(&id).unwrap();
            host.save_bound_session(&request, pin.clone(), SessionBinding::Current(t.clone()))
                .unwrap();
            assert!(host.consume_undo(&id, &t).is_err());
        }
    }

    #[test]
    fn undo_ack_reopens_same_native_metadata_and_preserves_immutable_evidence_at_wide_revision() {
        let (_, before, _) = apply_fixture();
        let previous = serde_json::json!({"mode":"b2","time_base":{"numerator":"1","denominator":"90000"},"findings":[
            {"finding_id":"f1","cue_id":"c1","source_text":"Source","confidence":0.9838564395904541f64,"range":{"start_ticks":"10","end_ticks":"20"},"translated_text":"Changed","voice":"voice-new","output_preview":"retained-new"},
            {"finding_id":"f2","cue_id":"c2","source_text":"Other","translated_text":"Changed too","voice":"voice-b","output_preview":"retained-new"}]});
        let mut findings = previous["findings"].clone();
        findings[0]["translated_text"] = serde_json::json!("Prior");
        findings[0]["voice"] = Value::Null;
        findings[0]["output_preview"] = Value::Null;
        findings[1]["translated_text"] = serde_json::json!("Prior too");
        let ack = serde_json::json!({"schema_version":1,"kind":"review_active_native_state","job_id":before.job_id,"revision":"9007199254740994", "snapshot_sha256":format!("sha256:{}","d".repeat(64)),"graph_sha256":format!("sha256:{}","e".repeat(64)),
            "snapshot":{"schema_version":2,"job_id":before.job_id,"revision":9007199254740994u64,"time_base":previous["time_base"],"findings":findings,"last_plan":null},"active_graph_published":false,"correction_ready":false});
        let fresh = serde_json::json!({"mode":"b2","current_token":{"job_id":before.job_id,"revision":"9007199254740994","snapshot_sha256":ack["snapshot_sha256"],"graph_sha256":ack["graph_sha256"],"original_manifest_sha256":before.original_manifest_sha256},"time_base":previous["time_base"],"findings":ack["snapshot"]["findings"],"last_plan":null});
        assert_eq!(
            undone_token(&before, &previous, &ack, &fresh)
                .unwrap()
                .revision,
            "9007199254740994"
        );
        let mut retained = ack.clone();
        retained["active_graph_published"] = Value::Bool(true);
        assert!(undone_token(&before, &previous, &retained, &fresh).is_ok());
        for change in 0..12 {
            let mut a = ack.clone();
            let mut f = fresh.clone();
            match change {
                0 => a["kind"] = serde_json::json!("review_native_committed_render"),
                1 => a["revision"] = serde_json::json!("0"),
                2 => a["snapshot"]["revision"] = serde_json::json!(9007199254740993u64),
                3 => f["current_token"]["original_manifest_sha256"] = serde_json::json!("foreign"),
                4 => a["graph_sha256"] = serde_json::json!("foreign"),
                5 => f["current_token"]["graph_sha256"] = serde_json::json!(before.graph_sha256),
                6 => f["mode"] = serde_json::json!("b1"),
                7 => {
                    f["time_base"]["numerator"] = serde_json::json!("2");
                    a["snapshot"]["time_base"] = f["time_base"].clone();
                }
                8 => f["last_plan"] = serde_json::json!({"invented":true}),
                9 => {
                    f["findings"][0]["source_text"] = serde_json::json!("Lost source");
                    a["snapshot"]["findings"] = f["findings"].clone();
                }
                10 => {
                    f["findings"][0]["confidence"] = serde_json::json!(0.98);
                    a["snapshot"]["findings"] = f["findings"].clone();
                }
                _ => {
                    f["findings"].as_array_mut().unwrap().pop();
                    a["snapshot"]["findings"] = f["findings"].clone();
                }
            }
            assert!(undone_token(&before, &previous, &a, &f).is_err());
        }
    }

    #[test]
    fn native_plan_is_consumed_once_and_uncertain_apply_requires_a_new_current_session() {
        let (host, token, plan) = apply_fixture();
        assert!(host
            .consume_plan("request-apply-session-0001", &plan)
            .is_err());
        let request = host.begin("request-apply-plan-0001").unwrap();
        host.save_plan("request-apply-session-0001", &token, &request, plan.clone())
            .unwrap();
        let mut changed = plan.clone();
        changed["changes"]["translation"] = serde_json::json!("Substituted");
        assert!(host
            .consume_plan("request-apply-session-0001", &changed)
            .is_err());
        let (pin, saved, guard) = host
            .consume_plan("request-apply-session-0001", &plan)
            .unwrap();
        assert_eq!(saved.revision, token.revision);
        assert!(host
            .consume_plan("request-apply-session-0001", &plan)
            .is_err());
        assert!(host
            .current_session("request-apply-session-0001", "job-apply")
            .is_err());
        assert!(host
            .registry
            .lock()
            .unwrap()
            .applying_jobs
            .contains("job-apply"));
        let competing = host.begin("request-apply-reload-0001").unwrap();
        assert!(host
            .save_bound_session(
                &competing,
                pin.clone(),
                SessionBinding::Current(token.clone())
            )
            .is_err());
        let finished = host.begin("request-apply-finish-0001").unwrap();
        let mut next = token.clone();
        next.revision = "9007199254740994".into();
        host.finish_apply_session(&finished, pin, next.clone(), &guard)
            .unwrap();
        assert_eq!(
            host.current_session("request-apply-finish-0001", "job-apply")
                .unwrap()
                .1
                .revision,
            next.revision
        );
        assert!(host
            .save_bound_session(
                &competing,
                host.current_session("request-apply-finish-0001", "job-apply")
                    .unwrap()
                    .0,
                SessionBinding::Current(token.clone())
            )
            .is_err());
        drop(guard);
        assert!(!host
            .registry
            .lock()
            .unwrap()
            .applying_jobs
            .contains("job-apply"));
        assert!(host
            .current_session("request-apply-session-0001", "job-apply")
            .is_err());
    }

    #[test]
    fn cancelled_or_foreign_plan_is_not_saved_and_does_not_authorize_apply() {
        let (host, token, plan) = apply_fixture();
        let request = host.begin("request-apply-plan-0002").unwrap();
        let mut foreign = plan.clone();
        foreign["current_token"]["graph_sha256"] = serde_json::json!("foreign");
        assert!(host
            .save_plan("request-apply-session-0001", &token, &request, foreign)
            .is_err());
        host.cancel(&request.id).unwrap();
        assert!(host
            .save_plan("request-apply-session-0001", &token, &request, plan.clone())
            .is_err());
        assert!(host
            .consume_plan("request-apply-session-0001", &plan)
            .is_err());
        assert_eq!(
            host.current_session("request-apply-session-0001", "job-apply")
                .unwrap()
                .1
                .revision,
            token.revision
        );
    }

    #[test]
    fn committed_ack_and_fresh_current_must_agree_on_actual_wide_revision_and_graph() {
        let (_, before, plan) = apply_fixture();
        let ack = serde_json::json!({"schema_version":1,"kind":"review_native_committed_render","job_id":"job-apply","revision":"9007199254740994",
            "snapshot_sha256":format!("sha256:{}","d".repeat(64)),"graph_sha256":format!("sha256:{}","e".repeat(64)),"snapshot":{"schema_version":2,"job_id":"job-apply","revision":9007199254740994u64,"time_base":{"numerator":"1","denominator":"90000"},"findings":[{"source_text":"Original evidence"}],"last_plan":null},"active_graph_published":true,"correction_ready":false});
        let fresh = serde_json::json!({"mode":"b2","current_token":{"job_id":"job-apply","revision":"9007199254740994","snapshot_sha256":ack["snapshot_sha256"],"graph_sha256":ack["graph_sha256"],"original_manifest_sha256":before.original_manifest_sha256},"time_base":ack["snapshot"]["time_base"],"findings":ack["snapshot"]["findings"],"last_plan":null});
        assert_eq!(
            applied_token(&before, &plan, &ack, &fresh)
                .unwrap()
                .revision,
            "9007199254740994"
        );
        for change in 0..9 {
            let mut bad_ack = ack.clone();
            let mut bad_fresh = fresh.clone();
            match change {
                0 => bad_ack["active_graph_published"] = serde_json::json!(false),
                1 => bad_ack["revision"] = serde_json::json!("0"),
                2 => {
                    bad_fresh["current_token"]["graph_sha256"] =
                        serde_json::json!(before.graph_sha256)
                }
                3 => {
                    bad_fresh["current_token"]["original_manifest_sha256"] =
                        serde_json::json!("foreign")
                }
                4 => bad_fresh["current_token"]["revision"] = serde_json::json!("9007199254740995"),
                5 => bad_ack["snapshot"]["revision"] = serde_json::json!(9007199254740993u64),
                6 => {
                    bad_fresh["findings"][0]["source_text"] = serde_json::json!("Changed evidence")
                }
                7 => bad_ack["snapshot"]["time_base"]["numerator"] = serde_json::json!("2"),
                _ => bad_fresh["last_plan"] = serde_json::json!({"invented":true}),
            }
            assert!(applied_token(&before, &plan, &bad_ack, &bad_fresh).is_err());
        }
    }

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
    fn result_video_sessions_require_uninitialized_original_or_exact_current_identity() {
        let job = "job-result-video";
        let session = "result-video-session-0001";
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: job.into(),
            manifest_path: PathBuf::from("unused-original-manifest"),
            manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            manifest_size_bytes: 123,
            attempt: 1,
        };
        let original: ResultVideoRequest = serde_json::from_value(serde_json::json!({"schema_version":1,
            "kind":"review_open_result_request","job_id":job,"expected":{"kind":"original","revision":0}})).unwrap();
        let host = ReviewHost::default();
        host.registry.lock().unwrap().sessions.insert(
            session.into(),
            Session {
                pin: pin.clone(),
                created: Instant::now(),
                binding: SessionBinding::Uninitialized,
                pending_plan: None,
                original_view: Some(
                    serde_json::json!({"schema_version":2,"job_id":job,"revision":0}),
                ),
            },
        );
        assert_eq!(host.result_video_session(session, &original).unwrap(), pin);
        let token: CurrentToken = serde_json::from_value(serde_json::json!({"job_id":job,"revision":"9223372036854775806",
            "snapshot_sha256":format!("sha256:{}", "b".repeat(64)),"graph_sha256":format!("sha256:{}", "c".repeat(64)),
            "original_manifest_sha256":pin.manifest_sha256})).unwrap();
        let current = ResultVideoRequest {
            schema_version: 1,
            kind: "review_open_result_request".into(),
            job_id: job.into(),
            expected: ResultVideoExpected::Current {
                current_token: token.clone(),
            },
        };
        host.registry
            .lock()
            .unwrap()
            .sessions
            .get_mut(session)
            .unwrap()
            .binding = SessionBinding::Current(token.clone());
        assert_eq!(host.result_video_session(session, &current).unwrap(), pin);
        assert!(host.result_video_session(session, &original).is_err());
        for key in [
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ] {
            let mut value = result_video_expected_json(&current.expected);
            value["current_token"][key] = Value::String(if key == "revision" {
                "02".into()
            } else if key == "job_id" {
                "foreign-job".into()
            } else {
                format!("sha256:{}", "f".repeat(64))
            });
            let forged: ResultVideoRequest =
                serde_json::from_value(serde_json::json!({"schema_version":1,
                "kind":"review_open_result_request","job_id":job,"expected":value}))
                .unwrap();
            assert!(host.result_video_session(session, &forged).is_err());
        }
        host.registry
            .lock()
            .unwrap()
            .applying_jobs
            .insert(job.into());
        assert!(host.result_video_session(session, &current).is_err());
        host.registry.lock().unwrap().applying_jobs.clear();
        // Initialized original/restored-original is still a current-token route.
        let zero = CurrentToken {
            revision: "0".into(),
            ..token
        };
        host.registry
            .lock()
            .unwrap()
            .sessions
            .get_mut(session)
            .unwrap()
            .binding = SessionBinding::Current(zero.clone());
        let current_zero = ResultVideoRequest {
            expected: ResultVideoExpected::Current {
                current_token: zero,
            },
            ..current.clone()
        };
        assert_eq!(
            host.result_video_session(session, &current_zero).unwrap(),
            pin
        );
        assert!(host.result_video_session(session, &original).is_err());
        host.registry
            .lock()
            .unwrap()
            .sessions
            .get_mut(session)
            .unwrap()
            .binding = SessionBinding::LegacyOriginal;
        assert!(host.result_video_session(session, &original).is_err());
        host.registry
            .lock()
            .unwrap()
            .sessions
            .get_mut(session)
            .unwrap()
            .created = Instant::now() - SESSION_TTL - Duration::from_secs(1);
        assert!(host.result_video_session(session, &current_zero).is_err());
    }

    #[test]
    fn result_video_launch_ack_is_closed_bound_and_never_claims_playback() {
        let pin = ExportPin {
            schema_version: 1,
            kind: "review_export_pin".into(),
            job_id: "job-result-video".into(),
            manifest_path: PathBuf::from("unused-original-manifest"),
            manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            manifest_size_bytes: 123,
            attempt: 1,
        };
        let input: ResultVideoRequest = serde_json::from_value(serde_json::json!({"schema_version":1,
            "kind":"review_open_result_request","job_id":pin.job_id,"expected":{"kind":"original","revision":0}})).unwrap();
        let ack = serde_json::json!({"schema_version":1,"kind":"review_native_result_video_launch_requested","job_id":pin.job_id,
            "expected":{"kind":"original","revision":0,"original_manifest_sha256":pin.manifest_sha256},
            "role":"final_video","content_sha256":format!("sha256:{}", "d".repeat(64)),"size_bytes":"9326167",
            "launch_requested":true,"managed_playback":false,"playback_verified":false});
        assert!(
            validate_result_video_launch(&serde_json::to_vec(&ack).unwrap(), &input, &pin).is_ok()
        );
        for (key, value) in [
            ("job_id", Value::String("foreign-job".into())),
            ("role", Value::String("source".into())),
            ("content_sha256", Value::String("unverified".into())),
            ("size_bytes", Value::String("09326167".into())),
            ("launch_requested", Value::Bool(false)),
            ("managed_playback", Value::Bool(true)),
            ("playback_verified", Value::Bool(true)),
            ("path", Value::String("C:/untrusted/video.mp4".into())),
        ] {
            let mut bad = ack.clone();
            bad[key] = value;
            assert!(
                validate_result_video_launch(&serde_json::to_vec(&bad).unwrap(), &input, &pin)
                    .is_err()
            );
        }
        let mut bad = ack.clone();
        bad["expected"]["original_manifest_sha256"] =
            Value::String(format!("sha256:{}", "f".repeat(64)));
        assert!(
            validate_result_video_launch(&serde_json::to_vec(&bad).unwrap(), &input, &pin).is_err()
        );
        let duplicated = serde_json::to_string(&ack).unwrap().replacen(
            "\"schema_version\":1",
            "\"schema_version\":1,\"schema_version\":1",
            1,
        );
        assert!(validate_result_video_launch(duplicated.as_bytes(), &input, &pin).is_err());
        let mut request = serde_json::json!({"schema_version":1,"kind":"review_open_result_request","job_id":pin.job_id,
            "expected":{"kind":"original","revision":0}});
        request["path"] = Value::String("C:/untrusted/video.mp4".into());
        assert!(serde_json::from_value::<ResultVideoRequest>(request).is_err());
        let mut original_after = input.clone();
        original_after.expected = ResultVideoExpected::Original { revision: 1 };
        assert!(validate_result_video_request(&original_after).is_err());
        assert!(open_result_video(
            ReviewHost::default(),
            "missing-session-0001".into(),
            "result-request-0001".into(),
            input
        )
        .is_err());
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
            Ok("failed-diagnostic") => {
                eprintln!(
                    "REVIEW_QC_DIAGNOSTIC_FIXTURE: inspect input and reload; no retry authority"
                );
                std::process::exit(2);
            }
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
