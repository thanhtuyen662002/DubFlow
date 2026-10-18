//! Persistent source-serve stream. A submission proves only a completed pipe
//! write; asynchronous native events alone describe admission/durable progress.
//! No request IDs, native protocol changes, retries, repinning or early EOF.
use super::{tree::OwnedChild, Limits};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, VecDeque};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc::{self, Receiver, SyncSender},
    Arc, Mutex,
};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

/// Construct only from trusted host release configuration, never webview args.
/// Native Runtime::admit still verifies the complete inventory before ready.
#[derive(Clone)]
pub struct LaunchPin {
    pub root: PathBuf,
    pub data_root: PathBuf,
    pub manifest_sha256: String,
}
impl LaunchPin {
    pub fn command(&self) -> Result<Command, Failure> {
        if !self.root.is_absolute()
            || !self.data_root.is_absolute()
            || self.manifest_sha256.len() != 64
            || !self
                .manifest_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Failure::Configuration);
        }
        let mut command = Command::new(self.root.join("app/bin/dubflow-supervisor.exe"));
        command
            .arg("source-serve")
            .arg("--root")
            .arg(&self.root)
            .arg("--data-root")
            .arg(&self.data_root)
            .arg("--manifest-sha256")
            .arg(&self.manifest_sha256);
        Ok(command)
    }
}

/// Required finite startup policy, separate from the unchanged 15s write bound.
/// No default: the trusted entrypoint must budget full manifest admission.
#[derive(Clone, Copy)]
pub struct AdmissionWindow(Duration);
impl AdmissionWindow {
    pub fn new(timeout: Duration) -> Result<Self, Failure> {
        if timeout.is_zero() || Instant::now().checked_add(timeout).is_none() {
            return Err(Failure::Configuration);
        }
        Ok(Self(timeout))
    }
}

/// Mirrors f68 source_server.rs's closed private request schema, byte for byte.
/// No Debug implementation: session headers must never enter diagnostics.
#[derive(Deserialize, Serialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
pub enum NativeRequest {
    SessionSave {
        provider_id: String,
        headers: BTreeMap<String, String>,
        expires_at: u64,
    },
    SessionStatus {
        provider_id: String,
    },
    SessionClear {
        provider_id: String,
    },
    StartVideo {
        scan_id: String,
        provider_id: String,
        source_ref: String,
    },
    Start {
        scan_id: String,
        provider_id: String,
        source_ref: String,
        page_size: usize,
        max_items: usize,
    },
    Resume {
        scan_id: String,
    },
    Pause {
        scan_id: String,
    },
    Cancel {
        scan_id: String,
    },
    Status {
        scan_id: String,
    },
    Items {
        scan_id: String,
        offset: usize,
        limit: usize,
    },
    Download {
        scan_id: String,
        identity_key: String,
        resume: bool,
    },
    RetryDownload {
        scan_id: String,
        identity_key: String,
    },
    PauseDownload {
        scan_id: String,
    },
    CancelDownload {
        scan_id: String,
    },
    Shutdown,
}
impl NativeRequest {
    // Preflight without cloning large untrusted strings into a JSON buffer.
    fn raw_bytes(&self) -> usize {
        match self {
            Self::SessionSave {
                provider_id,
                headers,
                ..
            } => headers
                .iter()
                .fold(provider_id.len(), |size, (key, value)| {
                    size.saturating_add(key.len())
                        .saturating_add(value.len())
                        .saturating_add(4)
                }),
            Self::SessionStatus { provider_id } | Self::SessionClear { provider_id } => {
                provider_id.len()
            }
            Self::StartVideo {
                scan_id,
                provider_id,
                source_ref,
            }
            | Self::Start {
                scan_id,
                provider_id,
                source_ref,
                ..
            } => scan_id
                .len()
                .saturating_add(provider_id.len())
                .saturating_add(source_ref.len()),
            Self::Download {
                scan_id,
                identity_key,
                ..
            }
            | Self::RetryDownload {
                scan_id,
                identity_key,
            } => scan_id.len().saturating_add(identity_key.len()),
            Self::Resume { scan_id }
            | Self::Pause { scan_id }
            | Self::Cancel { scan_id }
            | Self::Status { scan_id }
            | Self::Items { scan_id, .. }
            | Self::PauseDownload { scan_id }
            | Self::CancelDownload { scan_id } => scan_id.len(),
            Self::Shutdown => 0,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure {
    Configuration,
    Spawn,
    AdmissionTimeout,
    RequestTimeout,
    ResponseTimeout,
    Cancelled,
    NotReady,
    Busy,
    Closed,
    RequestLimit,
    StdoutLimit,
    StderrLimit,
    EventOverflow,
    Protocol,
    Io,
    Exit,
    Retire,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Admitting,
    Ready,
    Stopping,
    Stopped,
}
#[derive(Clone, Debug, Serialize)]
pub struct Snapshot {
    pub phase: Phase,
    pub failure: Option<Failure>,
    pub retired: bool,
}
#[derive(Clone, Serialize)]
pub struct Observation {
    pub sequence: u64,
    pub value: Value,
}
#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct Submission {
    pub delivery: &'static str,
}

struct Shared {
    state: Mutex<State>,
    stop: AtomicBool,
    fault: Mutex<Option<Failure>>,
    partial_started: Mutex<Option<Instant>>,
}
struct State {
    snapshot: Snapshot,
    events: VecDeque<Observation>,
}
struct Owner {
    shared: Arc<Shared>,
    sender: SyncSender<Outbound>,
    limits: Limits,
}
impl Drop for Owner {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
    }
}
#[derive(Clone)]
pub struct Client {
    owner: Arc<Owner>,
}
struct Outbound {
    bytes: Vec<u8>,
    deadline: Instant,
    cancelled: Arc<AtomicBool>,
    reply: SyncSender<Result<Submission, Failure>>,
}
enum Wire {
    Line(Vec<u8>),
    Written,
    Eof,
}
fn fault(shared: &Shared, failure: Failure) {
    let mut first = shared.fault.lock().unwrap();
    if first.is_none() {
        *first = Some(failure);
    }
}

impl Client {
    pub fn launch(pin: LaunchPin, admission: AdmissionWindow) -> Result<Self, Failure> {
        Self::spawn(pin.command()?, admission, Limits::default())
    }
    // Module fixtures supply only the executable; production uses immutable pin.command().
    pub(crate) fn spawn(
        command: Command,
        admission: AdmissionWindow,
        limits: Limits,
    ) -> Result<Self, Failure> {
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                snapshot: Snapshot {
                    phase: Phase::Admitting,
                    failure: None,
                    retired: false,
                },
                events: VecDeque::new(),
            }),
            stop: AtomicBool::new(false),
            fault: Mutex::new(None),
            partial_started: Mutex::new(None),
        });
        let (sender, requests) = mpsc::sync_channel(1);
        let owner = Arc::new(Owner {
            shared: shared.clone(),
            sender,
            limits,
        });
        thread::Builder::new()
            .name("source-service".into())
            .spawn(move || actor(command, admission, limits, requests, shared))
            .map_err(|_| Failure::Spawn)?;
        Ok(Self { owner })
    }
    pub fn snapshot(&self) -> Snapshot {
        self.owner.shared.state.lock().unwrap().snapshot.clone()
    }
    /// Drain in stream order. Sequence numbers identify host observations, not
    /// native request IDs. No inferred RPC success or automatic operation retry.
    pub fn drain(&self) -> Vec<Observation> {
        self.owner
            .shared
            .state
            .lock()
            .unwrap()
            .events
            .drain(..)
            .collect()
    }
    pub fn stop(&self) {
        let mut state = self.owner.shared.state.lock().unwrap();
        if state.snapshot.phase != Phase::Stopped {
            state.snapshot.phase = Phase::Stopping;
        }
        self.owner.shared.stop.store(true, Ordering::Release);
    }
    pub fn submit(
        &self,
        request: NativeRequest,
        cancelled: Arc<AtomicBool>,
    ) -> Result<Submission, Failure> {
        if cancelled.load(Ordering::Acquire) {
            return Err(Failure::Cancelled);
        }
        if self.snapshot().phase != Phase::Ready {
            return Err(Failure::NotReady);
        }
        let deadline = Instant::now() + self.owner.limits.timeout;
        let cap = self.owner.limits.request_bytes.min(64 * 1024);
        if request.raw_bytes() >= cap {
            return Err(Failure::RequestLimit);
        }
        let bytes = serde_json::to_vec(&request).map_err(|_| Failure::Protocol)?;
        // source_server::bounded_line uses worker MAX_LINE_BYTES=64KiB,
        // including the terminator; this is stricter than the generic pipe cap.
        if bytes.len() + 1 > cap {
            return Err(Failure::RequestLimit);
        }
        let (reply, receive) = mpsc::sync_channel(1);
        self.owner
            .sender
            .try_send(Outbound {
                bytes,
                deadline,
                cancelled: cancelled.clone(),
                reply,
            })
            .map_err(|error| match error {
                mpsc::TrySendError::Full(_) => Failure::Busy,
                _ => Failure::Closed,
            })?;
        loop {
            if cancelled.load(Ordering::Acquire) {
                return Err(Failure::Cancelled);
            }
            let now = Instant::now();
            if now >= deadline {
                return Err(Failure::RequestTimeout);
            }
            match receive.recv_timeout((deadline - now).min(Duration::from_millis(10))) {
                Ok(result) => return result,
                Err(mpsc::RecvTimeoutError::Timeout) => (),
                Err(_) => return Err(Failure::Closed),
            }
        }
    }
}

fn stdout_reader(mut pipe: impl Read, limit: usize, tx: SyncSender<Wire>, shared: Arc<Shared>) {
    let mut total = 0usize;
    let mut line = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        match pipe.read(&mut buffer) {
            Ok(0) => {
                if !line.is_empty() {
                    fault(&shared, Failure::Protocol);
                }
                let _ = tx.try_send(Wire::Eof);
                return;
            }
            Ok(n) => {
                total = total.saturating_add(n);
                if total > limit {
                    fault(&shared, Failure::StdoutLimit);
                    return;
                }
                for byte in &buffer[..n] {
                    if *byte == b'\n' {
                        *shared.partial_started.lock().unwrap() = None;
                        if tx.try_send(Wire::Line(std::mem::take(&mut line))).is_err() {
                            fault(&shared, Failure::EventOverflow);
                            return;
                        }
                    } else {
                        if line.is_empty() {
                            *shared.partial_started.lock().unwrap() = Some(Instant::now());
                        }
                        line.push(*byte);
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => (),
            Err(_) => {
                fault(&shared, Failure::Io);
                return;
            }
        }
    }
}
fn stderr_reader(mut pipe: impl Read, limit: usize, shared: Arc<Shared>) {
    let mut total = 0usize;
    let mut buffer = [0u8; 8192];
    loop {
        match pipe.read(&mut buffer) {
            Ok(0) => return,
            Ok(n) => {
                total = total.saturating_add(n);
                if total > limit {
                    fault(&shared, Failure::StderrLimit);
                    return;
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => (),
            Err(_) => {
                fault(&shared, Failure::Io);
                return;
            }
        }
    }
}

fn actor(
    mut command: Command,
    admission: AdmissionWindow,
    limits: Limits,
    requests: Receiver<Outbound>,
    shared: Arc<Shared>,
) {
    let Some(admission_deadline) = Instant::now().checked_add(admission.0) else {
        shared.state.lock().unwrap().snapshot = Snapshot {
            phase: Phase::Stopped,
            failure: Some(Failure::Configuration),
            retired: true,
        };
        return;
    };
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match OwnedChild::spawn(&mut command) {
        Ok(child) => child,
        Err(_) => {
            let mut state = shared.state.lock().unwrap();
            state.snapshot = Snapshot {
                phase: Phase::Stopped,
                failure: Some(Failure::Spawn),
                retired: true,
            };
            return;
        }
    };
    let mut stdin = child.child.stdin.take().unwrap();
    let stdout = child.child.stdout.take().unwrap();
    let stderr = child.child.stderr.take().unwrap();
    let (tx, rx) = mpsc::sync_channel(64);
    let (writer, writes) = mpsc::sync_channel::<Vec<u8>>(1);
    let read_tx = tx.clone();
    let read_shared = shared.clone();
    let error_shared = shared.clone();
    let write_shared = shared.clone();
    let handles = vec![
        thread::spawn(move || stdout_reader(stdout, limits.stdout_bytes, read_tx, read_shared)),
        thread::spawn(move || stderr_reader(stderr, limits.stderr_bytes, error_shared)),
        thread::spawn(move || {
            while let Ok(bytes) = writes.recv() {
                if stdin
                    .write_all(&bytes)
                    .and_then(|_| stdin.write_all(b"\n"))
                    .and_then(|_| stdin.flush())
                    .is_err()
                {
                    fault(&write_shared, Failure::Io);
                    return;
                }
                if tx.try_send(Wire::Written).is_err() {
                    fault(&write_shared, Failure::EventOverflow);
                    return;
                }
            }
            // EOF only on explicit service retirement, never per request.
        }),
    ];
    let mut pending: Option<Outbound> = None;
    let mut sequence = 0u64;
    let mut ready = false;
    let mut shutdown_seen = false;
    let mut exited_at = None;
    let mut eof = false;
    let result = loop {
        if shared.stop.load(Ordering::Acquire) {
            break None;
        }
        if let Some(failure) = *shared.fault.lock().unwrap() {
            break Some(failure);
        }
        if !ready && Instant::now() >= admission_deadline {
            break Some(Failure::AdmissionTimeout);
        }
        if shared
            .partial_started
            .lock()
            .unwrap()
            .is_some_and(|when| when.elapsed() >= limits.timeout)
        {
            break Some(Failure::ResponseTimeout);
        }
        if exited_at.is_some_and(|when: Instant| when.elapsed() >= limits.retire_timeout) {
            break Some(Failure::Exit);
        }
        if let Some(request) = &pending {
            if request.cancelled.load(Ordering::Acquire) {
                break Some(Failure::Cancelled);
            }
            if Instant::now() >= request.deadline {
                break Some(Failure::RequestTimeout);
            }
        }
        if let Ok(request) = requests.try_recv() {
            let failure = if !ready {
                Some(Failure::NotReady)
            } else if pending.is_some() {
                Some(Failure::Busy)
            } else if request.cancelled.load(Ordering::Acquire) {
                Some(Failure::Cancelled)
            } else if Instant::now() >= request.deadline {
                Some(Failure::RequestTimeout)
            } else {
                None
            };
            if let Some(failure) = failure {
                let _ = request.reply.try_send(Err(failure));
            } else {
                if writer.try_send(request.bytes.clone()).is_err() {
                    break Some(Failure::Io);
                }
                pending = Some(request);
            }
        }
        match rx.recv_timeout(Duration::from_millis(5)) {
            Ok(Wire::Written) => {
                if let Some(request) = pending.take() {
                    let _ = request.reply.try_send(Ok(Submission {
                        delivery: "submitted",
                    }));
                } else {
                    break Some(Failure::Protocol);
                }
            }
            Ok(Wire::Line(bytes)) => {
                let value: Value = match serde_json::from_slice(&bytes) {
                    Ok(value) => value,
                    Err(_) => break Some(Failure::Protocol),
                };
                let event = value.get("event").and_then(Value::as_str).unwrap_or("");
                if !ready {
                    if event != "source_ready"
                        || value["schema_version"] != 1
                        || value["single_writer"] != true
                    {
                        break Some(Failure::Protocol);
                    }
                    ready = true;
                    shared.state.lock().unwrap().snapshot.phase = Phase::Ready;
                } else if !event.starts_with("source_") || event == "source_ready" {
                    break Some(Failure::Protocol);
                }
                shutdown_seen |= event == "source_shutdown";
                let mut state = shared.state.lock().unwrap();
                if state.events.len() >= 64 {
                    break Some(Failure::EventOverflow);
                }
                sequence += 1;
                state.events.push_back(Observation { sequence, value });
            }
            Ok(Wire::Eof) => {
                eof = true;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => (),
            Err(_) => break Some(Failure::Exit),
        }
        match child.child.try_wait() {
            Ok(Some(status)) if !status.success() => break Some(Failure::Exit),
            Ok(Some(_)) if eof && (!shutdown_seen || pending.is_none()) => {
                break if shutdown_seen {
                    None
                } else {
                    Some(Failure::Exit)
                }
            }
            Ok(Some(_)) => {
                exited_at.get_or_insert(Instant::now());
            }
            Err(_) => break Some(Failure::Io),
            _ => (),
        }
    };
    shared.state.lock().unwrap().snapshot.phase = Phase::Stopping;
    drop(writer);
    child.terminate();
    let deadline = Instant::now() + limits.retire_timeout;
    let retired = loop {
        if child.child.try_wait().ok().flatten().is_some()
            && handles.iter().all(JoinHandle::is_finished)
        {
            break true;
        }
        if Instant::now() >= deadline {
            break false;
        }
        thread::sleep(Duration::from_millis(2));
    };
    if retired {
        for handle in handles {
            let _ = handle.join();
        }
    }
    let failure = if retired {
        result
    } else {
        Some(Failure::Retire)
    };
    shared.state.lock().unwrap().snapshot = Snapshot {
        phase: Phase::Stopped,
        failure,
        retired,
    };
    if let Some(request) = pending {
        let _ = request
            .reply
            .try_send(Err(failure.unwrap_or(Failure::Closed)));
    }
}
