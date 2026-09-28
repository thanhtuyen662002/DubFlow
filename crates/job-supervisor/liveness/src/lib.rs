//! Deterministic job-liveness classification and bounded recovery decisions.
//!
//! The supervisor owns durable state; this crate only classifies observations.
//! A heartbeat keeps legitimately slow inference/render work alive even when
//! progress has not advanced. No wall-clock value is used as a kill signal.

use std::fmt;

pub const MAX_RETRY_ATTEMPTS: u32 = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobState {
    Queued,
    Running,
    WaitingResource,
    WaitingExternal,
    Retrying,
    WorkerLost,
    Recovered,
    BlockedNeedsAction,
    Completed,
    Failed,
}

impl JobState {
    pub fn wire_name(self) -> &'static str {
        match self {
            Self::Queued => "QUEUED",
            Self::Running => "RUNNING",
            Self::WaitingResource => "WAITING_RESOURCE",
            Self::WaitingExternal => "WAITING_EXTERNAL",
            Self::Retrying => "RETRYING",
            Self::WorkerLost => "WORKER_LOST",
            Self::Recovered => "RECOVERED",
            Self::BlockedNeedsAction => "BLOCKED_NEEDS_ACTION",
            Self::Completed => "COMPLETED",
            Self::Failed => "FAILED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceKind {
    Gpu,
    Cpu,
    Disk,
    Network,
    WorkerSlot,
}

impl ResourceKind {
    fn message(self) -> &'static str {
        match self {
            Self::Gpu => "Waiting for GPU",
            Self::Cpu => "Waiting for CPU",
            Self::Disk => "Waiting for disk space",
            Self::Network => "Waiting for network",
            Self::WorkerSlot => "Waiting for worker slot",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExternalWait {
    LoginRequired,
    SiteChanged,
    UserAction(String),
}

impl ExternalWait {
    fn message(&self) -> &str {
        match self {
            Self::LoginRequired => "Login required",
            Self::SiteChanged => "Source changed; review the source adapter",
            Self::UserAction(message) => message.as_str(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkerSignal {
    Heartbeat { sequence: u64 },
    NoHeartbeat,
    Eof,
    Failed { reason: String },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchedulerState {
    Queued,
    Running,
    WaitingResource(ResourceKind),
    Completed,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DurableSnapshot {
    pub checkpoint_id: Option<String>,
    pub completed_units: u64,
    pub total_units: Option<u64>,
    pub attempt: u32,
    pub max_attempts: u32,
    pub condition_fingerprint: Option<String>,
    pub previous_state: JobState,
}

impl DurableSnapshot {
    pub fn validate(&self) -> Result<(), LivenessError> {
        if self.total_units.is_some_and(|total| self.completed_units > total) {
            return Err(LivenessError::ProgressExceedsTotal);
        }
        if self.max_attempts == 0 || self.max_attempts > MAX_RETRY_ATTEMPTS {
            return Err(LivenessError::InvalidRetryBudget);
        }
        if self.attempt > self.max_attempts {
            return Err(LivenessError::InvalidRetryBudget);
        }
        if self.checkpoint_id.as_deref().is_some_and(|id| id.is_empty() || id.len() > 256) {
            return Err(LivenessError::InvalidCheckpoint);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Observation {
    pub durable: DurableSnapshot,
    pub worker: WorkerSignal,
    pub scheduler: SchedulerState,
    pub external_wait: Option<ExternalWait>,
    pub materially_changed_condition: Option<String>,
    pub resource_held: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryAction {
    None,
    RestartFromCheckpoint,
    UseApprovedFallback,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LivenessDecision {
    pub state: JobState,
    pub reason: &'static str,
    pub message: String,
    pub action: RetryAction,
    pub release_resource: bool,
    pub checkpoint_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LivenessError {
    ProgressExceedsTotal,
    InvalidRetryBudget,
    InvalidCheckpoint,
}

impl fmt::Display for LivenessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ProgressExceedsTotal => write!(f, "durable progress exceeds total"),
            Self::InvalidRetryBudget => write!(f, "retry budget is outside the bounded range"),
            Self::InvalidCheckpoint => write!(f, "checkpoint id is invalid"),
        }
    }
}

impl std::error::Error for LivenessError {}

pub fn classify(observation: &Observation) -> Result<LivenessDecision, LivenessError> {
    observation.durable.validate()?;
    let checkpoint_id = observation.durable.checkpoint_id.clone();

    if let Some(wait) = &observation.external_wait {
        return Ok(LivenessDecision {
            state: JobState::WaitingExternal,
            reason: "external_action_required",
            message: wait.message().to_owned(),
            action: RetryAction::None,
            release_resource: observation.resource_held,
            checkpoint_id,
        });
    }

    match observation.scheduler {
        SchedulerState::Queued => {
            return Ok(decision(JobState::Queued, "queued", "Queued", RetryAction::None, observation.resource_held, checkpoint_id));
        }
        SchedulerState::WaitingResource(resource) => {
            return Ok(decision(JobState::WaitingResource, "resource_wait", resource.message(), RetryAction::None, observation.resource_held, checkpoint_id));
        }
        SchedulerState::Running => {}
        SchedulerState::Completed => {
            return Ok(decision(JobState::Completed, "completed", "Completed", RetryAction::None, false, checkpoint_id));
        }
        SchedulerState::Failed => {
            return Ok(decision(JobState::Failed, "stage_failed", "Failed; review the stage details", RetryAction::None, observation.resource_held, checkpoint_id));
        }
    }

    match &observation.worker {
        WorkerSignal::Heartbeat { sequence: _ } => {
            let recovered = observation.durable.previous_state == JobState::WorkerLost || observation.durable.previous_state == JobState::Retrying;
            let (state, reason, message) = if recovered {
                (JobState::Recovered, "worker_recovered", "Stalled/recovered from checkpoint")
            } else {
                (JobState::Running, "heartbeat_alive", "Processing")
            };
            Ok(decision(state, reason, message, RetryAction::None, false, checkpoint_id))
        }
        WorkerSignal::NoHeartbeat | WorkerSignal::Eof | WorkerSignal::Failed { .. } => {
            let can_retry = observation.durable.attempt < observation.durable.max_attempts
                && (observation.durable.attempt == 0
                    || observation.materially_changed_condition.is_some()
                        && observation.materially_changed_condition != observation.durable.condition_fingerprint);
            if can_retry {
                Ok(decision(
                    JobState::Retrying,
                    "worker_lost_bounded_retry",
                    "Retrying smaller chunk from checkpoint",
                    RetryAction::RestartFromCheckpoint,
                    true,
                    checkpoint_id,
                ))
            } else {
                Ok(decision(
                    JobState::BlockedNeedsAction,
                    "retry_budget_exhausted_or_unchanged",
                    "Stalled; action required after bounded retries",
                    RetryAction::UseApprovedFallback,
                    true,
                    checkpoint_id,
                ))
            }
        }
    }
}

fn decision(
    state: JobState,
    reason: &'static str,
    message: &str,
    action: RetryAction,
    release_resource: bool,
    checkpoint_id: Option<String>,
) -> LivenessDecision {
    LivenessDecision { state, reason, message: message.to_owned(), action, release_resource, checkpoint_id }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot(previous_state: JobState) -> DurableSnapshot {
        DurableSnapshot {
            checkpoint_id: Some("cp-1".to_owned()),
            completed_units: 63,
            total_units: Some(100),
            attempt: 0,
            max_attempts: 3,
            condition_fingerprint: None,
            previous_state,
        }
    }

    #[test]
    fn heartbeat_keeps_slow_inference_running_without_wall_clock_kill() {
        let observation = Observation { durable: snapshot(JobState::Running), worker: WorkerSignal::Heartbeat { sequence: 42 }, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: None, resource_held: true };
        let result = classify(&observation).unwrap();
        assert_eq!(result.state, JobState::Running);
        assert_eq!(result.action, RetryAction::None);
        assert!(!result.release_resource);
    }

    #[test]
    fn queue_and_resource_wait_are_distinct_and_release_held_resources() {
        let queued = Observation { durable: snapshot(JobState::Queued), worker: WorkerSignal::NoHeartbeat, scheduler: SchedulerState::Queued, external_wait: None, materially_changed_condition: None, resource_held: false };
        assert_eq!(classify(&queued).unwrap().state, JobState::Queued);
        let waiting = Observation { durable: snapshot(JobState::Running), worker: WorkerSignal::NoHeartbeat, scheduler: SchedulerState::WaitingResource(ResourceKind::Gpu), external_wait: None, materially_changed_condition: None, resource_held: true };
        let result = classify(&waiting).unwrap();
        assert_eq!(result.state, JobState::WaitingResource);
        assert_eq!(result.message, "Waiting for GPU");
        assert!(result.release_resource);
    }

    #[test]
    fn worker_eof_restarts_from_checkpoint_then_blocks_without_material_change() {
        let first = Observation { durable: snapshot(JobState::Running), worker: WorkerSignal::Eof, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: None, resource_held: true };
        let retry = classify(&first).unwrap();
        assert_eq!(retry.state, JobState::Retrying);
        assert_eq!(retry.action, RetryAction::RestartFromCheckpoint);
        let mut after = snapshot(JobState::Retrying);
        after.attempt = 1;
        after.condition_fingerprint = Some("same".to_owned());
        let repeated = Observation { durable: after, worker: WorkerSignal::Failed { reason: "eof".to_owned() }, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: Some("same".to_owned()), resource_held: true };
        let blocked = classify(&repeated).unwrap();
        assert_eq!(blocked.state, JobState::BlockedNeedsAction);
        assert_eq!(blocked.action, RetryAction::UseApprovedFallback);
        assert!(blocked.release_resource);
    }

    #[test]
    fn materially_changed_retry_is_bounded_and_preserves_checkpoint() {
        let mut durable = snapshot(JobState::Retrying);
        durable.attempt = 1;
        durable.condition_fingerprint = Some("old".to_owned());
        let observation = Observation { durable, worker: WorkerSignal::NoHeartbeat, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: Some("smaller-chunk".to_owned()), resource_held: true };
        let result = classify(&observation).unwrap();
        assert_eq!(result.state, JobState::Retrying);
        assert_eq!(result.checkpoint_id.as_deref(), Some("cp-1"));
    }

    #[test]
    fn external_wait_is_actionable_and_does_not_retry_or_block_batch() {
        let observation = Observation { durable: snapshot(JobState::Running), worker: WorkerSignal::Heartbeat { sequence: 1 }, scheduler: SchedulerState::Running, external_wait: Some(ExternalWait::LoginRequired), materially_changed_condition: None, resource_held: true };
        let result = classify(&observation).unwrap();
        assert_eq!(result.state, JobState::WaitingExternal);
        assert_eq!(result.message, "Login required");
        assert_eq!(result.action, RetryAction::None);
        assert!(result.release_resource);
    }

    #[test]
    fn restart_reconstructs_from_durable_checkpoint_and_marks_recovered() {
        let observation = Observation { durable: snapshot(JobState::WorkerLost), worker: WorkerSignal::Heartbeat { sequence: 8 }, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: None, resource_held: false };
        let result = classify(&observation).unwrap();
        assert_eq!(result.state, JobState::Recovered);
        assert_eq!(result.checkpoint_id.as_deref(), Some("cp-1"));
        assert_eq!(result.message, "Stalled/recovered from checkpoint");
    }

    #[test]
    fn malformed_progress_and_unbounded_retry_budget_are_rejected() {
        let mut invalid = snapshot(JobState::Running);
        invalid.completed_units = 101;
        assert_eq!(classify(&Observation { durable: invalid, worker: WorkerSignal::Heartbeat { sequence: 1 }, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: None, resource_held: false }), Err(LivenessError::ProgressExceedsTotal));
        let mut invalid_budget = snapshot(JobState::Running);
        invalid_budget.max_attempts = MAX_RETRY_ATTEMPTS + 1;
        assert_eq!(classify(&Observation { durable: invalid_budget, worker: WorkerSignal::Heartbeat { sequence: 1 }, scheduler: SchedulerState::Running, external_wait: None, materially_changed_condition: None, resource_held: false }), Err(LivenessError::InvalidRetryBudget));
    }
}
