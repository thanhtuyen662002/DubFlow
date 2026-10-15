//! Optional Review IPC. Read and explicit apply requests are bound to native
//! completed-export/current sessions; the webview never selects media roots.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
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
    pending_plan: Option<Value>,
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
    let bytes = run_bounded(
        command,
        JSON_LIMIT,
        Duration::from_secs(8 * 3600),
        &request.cancelled,
    )?;
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
    let bytes = run_bounded(
        command,
        JSON_LIMIT,
        Duration::from_secs(740),
        &request.cancelled,
    )?;
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
    let bytes = run_bounded(
        command,
        JSON_LIMIT,
        Duration::from_secs(8 * 3600),
        &request.cancelled,
    )?;
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
    host.save_plan(&session, &token, &request, result.clone())?;
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
pub async fn review_apply_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    confirmation: CurrentConfirmation,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        current_apply(host, session_id, request_id, confirmation)
    })
    .await
    .map_err(|_| "Tác vụ sửa câu bị gián đoạn; hãy tải lại để kiểm tra kết quả".to_owned())?
}

#[tauri::command]
pub async fn review_undo_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentUndoRequest,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        current_undo(host, session_id, request_id, request)
    })
    .await
    .map_err(|_| "Tác vụ hoàn tác bị gián đoạn; hãy tải lại để kiểm tra".to_owned())?
}

#[tauri::command]
pub async fn review_export_v1(
    host: tauri::State<'_, ReviewHost>,
    session_id: String,
    request_id: String,
    request: CurrentExportRequest,
) -> Result<Value, String> {
    let host = host.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        current_export(host, session_id, request_id, request)
    })
    .await
    .map_err(|_| "Tác vụ xuất bị gián đoạn; hãy tải lại để kiểm tra".to_owned())?
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
