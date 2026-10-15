//! Best-effort observations; never input to admission, CAS, retry or success.
use serde_json::json;
use std::io::Write;
use std::sync::{mpsc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub(super) const PREFIX: &str = "DUBFLOW_REVIEW_STAGE_V1 ";
const MAX_EVENTS: u64 = 32768;
const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Clone, Copy, PartialEq)]
pub(super) enum Stage {
    Current,
    Processing,
    RuntimeFiles,
    PrepareCue,
    Synthesis,
    ValidateCue,
    Mix,
    ValidateMix,
    Timeline,
    Render,
    ValidateRender,
    Retain,
    Commit,
    Reopen,
    StandardExport,
    EditableExport,
}
impl Stage {
    fn name(self) -> &'static str {
        match self {
            Self::Current => "admitting_current",
            Self::Processing => "admitting_processing",
            Self::RuntimeFiles => "verifying_runtime_files",
            Self::PrepareCue => "preparing_cue",
            Self::Synthesis => "synthesizing_cue",
            Self::ValidateCue => "validating_cue",
            Self::Mix => "mixing_audio",
            Self::ValidateMix => "validating_mix",
            Self::Timeline => "deriving_timeline",
            Self::Render => "rendering_video",
            Self::ValidateRender => "validating_render",
            Self::Retain => "retaining_publication",
            Self::Commit => "committing_revision",
            Self::Reopen => "reopening_current",
            Self::StandardExport => "publishing_standard",
            Self::EditableExport => "publishing_editable",
        }
    }
}

struct State {
    started: Instant,
    sent: Instant,
    sequence: u64,
    epoch: u64,
    stage: Stage,
    files: u64,
    bytes: u64,
}
impl State {
    fn new(now: Instant) -> Self {
        Self {
            started: now,
            sent: now,
            sequence: 0,
            epoch: 0,
            stage: Stage::Current,
            files: 0,
            bytes: 0,
        }
    }
    fn packet(&mut self, now: Instant, force: bool) -> Option<String> {
        if self.sequence >= MAX_EVENTS
            || (!force && now.saturating_duration_since(self.sent) < Duration::from_millis(250))
        {
            return None;
        }
        self.sequence += 1;
        self.sent = now;
        let packet = json!({"schema_version":1,"kind":"review_native_stage_observation",
            "sequence":self.sequence,"stage_epoch":self.epoch,"stage":self.stage.name(),
            "elapsed_ms":now.saturating_duration_since(self.started).as_millis().min(86_400_000) as u64,
            "units_completed":self.files,"bytes_verified":self.bytes});
        let line = format!("{}{}\n", PREFIX, packet);
        // No source, job, artifact, token, free text or producer bytes enter here.
        if line.len() > 512 {
            return None;
        }
        Some(line)
    }
    fn stage(&mut self, stage: Stage, now: Instant) -> Option<String> {
        if self.epoch >= MAX_EVENTS {
            return None;
        }
        self.epoch += 1;
        self.stage = stage;
        self.files = 0;
        self.bytes = 0;
        self.packet(now, true)
    }
    fn files(&mut self, files: u64, bytes: u64, now: Instant, force: bool) -> Option<String> {
        if self.stage != Stage::RuntimeFiles
            || files > MAX_SAFE_INTEGER
            || bytes > MAX_SAFE_INTEGER
            || files < self.files
            || bytes < self.bytes
        {
            return None;
        }
        self.files = files;
        self.bytes = bytes;
        self.packet(now, force)
    }
}

struct Progress {
    state: Mutex<State>,
    send: mpsc::SyncSender<String>,
}
impl Progress {
    fn update(&self, make: impl FnOnce(&mut State, Instant) -> Option<String>) {
        // Neither pipe back-pressure nor a busy observation lock blocks work.
        // Dropped observations are allowed; counters are never admission data.
        if let Ok(mut state) = self.state.try_lock() {
            if let Some(line) = make(&mut state, Instant::now()) {
                let _ = self.send.try_send(line);
            }
        }
    }
}
fn progress() -> Option<&'static Progress> {
    static PROGRESS: OnceLock<Option<Progress>> = OnceLock::new();
    PROGRESS
        .get_or_init(|| {
            if std::env::var_os("DUBFLOW_REVIEW_PROGRESS_V1").as_deref()
                != Some(std::ffi::OsStr::new("1"))
            {
                return None;
            }
            let (send, receive) = mpsc::sync_channel::<String>(16);
            std::thread::Builder::new()
                .name("review-observation".into())
                .spawn(move || {
                    for line in receive {
                        if std::io::stderr().lock().write_all(line.as_bytes()).is_err() {
                            break;
                        }
                    }
                })
                .ok()?;
            Some(Progress {
                state: Mutex::new(State::new(Instant::now())),
                send,
            })
        })
        .as_ref()
}

pub(super) fn stage(stage: Stage) {
    if let Some(p) = progress() {
        p.update(|s, now| s.stage(stage, now));
    }
}
/// Counts come only after the existing full per-file verifier returned success.
/// Concurrent/contended observations may be dropped, never normalized as health.
pub(super) fn runtime_files(files: u64, bytes: u64, force: bool) {
    if let Some(p) = progress() {
        p.update(|s, now| s.files(files, bytes, now, force));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn packets_use_real_counts_monotonic_epochs_bounded_bytes_and_no_success() {
        let now = Instant::now();
        let mut state = State::new(now);
        let first = state.stage(Stage::RuntimeFiles, now).unwrap();
        let read = |line: &str| -> serde_json::Value {
            serde_json::from_str(line.strip_prefix(PREFIX).unwrap().trim()).unwrap()
        };
        assert_eq!(read(&first)["units_completed"], 0);
        assert!(state.files(1, 10, now, false).is_none());
        let next = state
            .files(2, 20, now + Duration::from_secs(1), false)
            .unwrap();
        let value = read(&next);
        assert_eq!(value["units_completed"], 2);
        assert_eq!(value["bytes_verified"], 20);
        assert_eq!(value["sequence"], 2);
        assert_eq!(value["stage_epoch"], 1);
        assert_eq!(value.as_object().unwrap().len(), 8);
        assert!(value.get("success").is_none());
        assert!(next.len() <= 512);
        assert!(state
            .files(1, 20, now + Duration::from_secs(2), true)
            .is_none());
        assert!(state
            .files(3, MAX_SAFE_INTEGER + 1, now + Duration::from_secs(2), true)
            .is_none());
        let next = read(
            &state
                .stage(Stage::Synthesis, now + Duration::from_secs(2))
                .unwrap(),
        );
        assert_eq!(next["stage_epoch"], 2);
        assert_eq!(next["units_completed"], 0);
        assert!(state
            .files(4, 40, now + Duration::from_secs(3), true)
            .is_none());
        state.sequence = MAX_EVENTS;
        assert!(state.stage(Stage::Commit, now).is_none());
    }
    #[test]
    fn full_observation_queue_or_busy_lock_never_waits_for_a_reader() {
        let now = Instant::now();
        let (send, _unread) = mpsc::sync_channel(1);
        let p = Progress {
            state: Mutex::new(State::new(now)),
            send,
        };
        p.update(|s, n| s.stage(Stage::Current, n));
        p.update(|s, n| s.stage(Stage::Synthesis, n));
        let held = p.state.lock().unwrap();
        p.update(|s, n| s.stage(Stage::Commit, n));
        drop(held);
        assert_eq!(p.state.lock().unwrap().epoch, 2);
    }
}
