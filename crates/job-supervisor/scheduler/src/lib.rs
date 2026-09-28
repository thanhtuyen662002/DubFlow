//! Deterministic, resource-bounded scheduler primitives.

use std::collections::BTreeMap;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ResourceClass {
    Cpu,
    Gpu,
    Vram,
    Render,
    Download,
    Disk,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResourceLimits {
    pub cpu: u32,
    pub gpu: u32,
    pub vram: u32,
    pub render: u32,
    pub download: u32,
    pub disk: u32,
}

impl ResourceLimits {
    pub fn capacity(self, class: ResourceClass) -> u32 {
        match class {
            ResourceClass::Cpu => self.cpu,
            ResourceClass::Gpu => self.gpu,
            ResourceClass::Vram => self.vram,
            ResourceClass::Render => self.render,
            ResourceClass::Download => self.download,
            ResourceClass::Disk => self.disk,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobSpec {
    pub id: String,
    pub priority: i32,
    pub enqueued_tick: u64,
    pub class: ResourceClass,
    pub resource_units: u32,
    pub quantum_units: u32,
    pub work_units: u64,
    pub checkpoint_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct JobState {
    spec: JobSpec,
    remaining_units: u64,
    running: bool,
    paused: bool,
    poisoned: bool,
    consecutive_slices: u32,
    fairness_debt: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dispatch {
    pub job_id: String,
    pub class: ResourceClass,
    pub resource_units: u32,
    pub quantum_units: u32,
    pub checkpoint_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SchedulerError {
    InvalidJobId,
    InvalidResourceRequest,
    DuplicateJob,
    UnknownJob,
    JobNotRunning,
    CheckpointMismatch,
    InvalidCompletion,
    InvalidDiskObservation,
}

impl fmt::Display for SchedulerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for SchedulerError {}

pub struct Scheduler {
    limits: ResourceLimits,
    jobs: BTreeMap<String, JobState>,
    usage: BTreeMap<ResourceClass, u32>,
}

impl Scheduler {
    pub fn new(limits: ResourceLimits) -> Self {
        Self { limits, jobs: BTreeMap::new(), usage: BTreeMap::new() }
    }

    pub fn enqueue(&mut self, spec: JobSpec) -> Result<(), SchedulerError> {
        if spec.id.is_empty() || spec.id.len() > 128 || spec.id.chars().any(|character| matches!(character, '/' | '\\' | '\0')) {
            return Err(SchedulerError::InvalidJobId);
        }
        if spec.resource_units == 0
            || spec.quantum_units == 0
            || spec.work_units == 0
            || spec.resource_units > self.limits.capacity(spec.class)
        {
            return Err(SchedulerError::InvalidResourceRequest);
        }
        if self.jobs.contains_key(&spec.id) {
            return Err(SchedulerError::DuplicateJob);
        }
        let id = spec.id.clone();
        self.jobs.insert(id, JobState { remaining_units: spec.work_units, spec, running: false, paused: false, poisoned: false, consecutive_slices: 0, fairness_debt: 0 });
        Ok(())
    }

    /// Select one checkpoint-safe quantum per available resource class. Aging
    /// is capped so a very old poisoned/invalid item cannot overflow scoring.
    pub fn dispatch(&mut self, now_tick: u64) -> Vec<Dispatch> {
        let mut candidates = self.jobs.values().filter(|job| !job.paused && !job.poisoned && !job.running).map(|job| {
            let age = now_tick.saturating_sub(job.spec.enqueued_tick).min(1_000_000);
            let score = i64::from(job.spec.priority).saturating_mul(1_000_001)
                .saturating_add(age as i64)
                .saturating_sub((job.fairness_debt.min(i64::MAX as u64) as i64).saturating_mul(1_000_001));
            (score, job.spec.enqueued_tick, job.spec.id.clone())
        }).collect::<Vec<_>>();
        candidates.sort_by(|left, right| right.cmp(left));
        let mut dispatches = Vec::new();
        for (_, _, id) in candidates {
            let Some(job) = self.jobs.get_mut(&id) else { continue };
            let used = *self.usage.get(&job.spec.class).unwrap_or(&0);
            let capacity = self.limits.capacity(job.spec.class);
            if used.saturating_add(job.spec.resource_units) > capacity { continue; }
            let quantum = job.spec.quantum_units.min(job.remaining_units as u32);
            job.running = true;
            job.consecutive_slices = job.consecutive_slices.saturating_add(1);
            *self.usage.entry(job.spec.class).or_insert(0) += job.spec.resource_units;
            dispatches.push(Dispatch { job_id: id, class: job.spec.class, resource_units: job.spec.resource_units, quantum_units: quantum, checkpoint_id: job.spec.checkpoint_id.clone() });
        }
        dispatches
    }

    /// Complete or yield a quantum. Callers must persist the checkpoint before
    /// calling this method; releasing usage is the scheduler's only mutation.
    pub fn complete_slice(&mut self, job_id: &str, completed_units: u64, checkpoint_id: &str) -> Result<bool, SchedulerError> {
        let (class, resource_units, completed) = {
            let job = self.jobs.get_mut(job_id).ok_or(SchedulerError::UnknownJob)?;
            if !job.running { return Err(SchedulerError::JobNotRunning); }
            if job.spec.checkpoint_id != checkpoint_id { return Err(SchedulerError::CheckpointMismatch); }
            if completed_units == 0 || completed_units > job.remaining_units { return Err(SchedulerError::InvalidCompletion); }
            job.remaining_units -= completed_units;
            job.running = false;
            let completed = job.remaining_units == 0;
            job.consecutive_slices = 0;
            if !completed { job.fairness_debt = job.fairness_debt.saturating_add(1); }
            (job.spec.class, job.spec.resource_units, completed)
        };
        self.release_usage(class, resource_units);
        if completed { self.jobs.remove(job_id); }
        Ok(completed)
    }

    pub fn pause(&mut self, job_id: &str, checkpoint_id: &str) -> Result<(), SchedulerError> {
        let (class, resource_units, was_running) = {
            let job = self.jobs.get_mut(job_id).ok_or(SchedulerError::UnknownJob)?;
            if job.spec.checkpoint_id != checkpoint_id { return Err(SchedulerError::CheckpointMismatch); }
            let was_running = job.running;
            job.running = false;
            job.paused = true;
            (job.spec.class, job.spec.resource_units, was_running)
        };
        if was_running { self.release_usage(class, resource_units); }
        Ok(())
    }

    pub fn resume(&mut self, job_id: &str, checkpoint_id: &str) -> Result<(), SchedulerError> {
        let job = self.jobs.get_mut(job_id).ok_or(SchedulerError::UnknownJob)?;
        if job.spec.checkpoint_id != checkpoint_id { return Err(SchedulerError::CheckpointMismatch); }
        job.paused = false;
        Ok(())
    }

    pub fn poison(&mut self, job_id: &str) -> Result<(), SchedulerError> {
        let (class, resource_units, was_running) = {
            let job = self.jobs.get_mut(job_id).ok_or(SchedulerError::UnknownJob)?;
            let was_running = job.running;
            job.running = false;
            job.poisoned = true;
            (job.spec.class, job.spec.resource_units, was_running)
        };
        if was_running { self.release_usage(class, resource_units); }
        Ok(())
    }

    pub fn usage(&self, class: ResourceClass) -> u32 { *self.usage.get(&class).unwrap_or(&0) }
    pub fn queued_jobs(&self) -> usize { self.jobs.values().filter(|job| !job.poisoned).count() }

    fn release_usage(&mut self, class: ResourceClass, units: u32) {
        let entry = self.usage.entry(class).or_insert(0);
        *entry = entry.saturating_sub(units);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DiskGuard {
    pub minimum_free_bytes: u64,
    pub minimum_free_ratio_ppm: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiskDecision { Continue, PauseBeforeWrite, Unknown }

impl DiskGuard {
    pub fn observe(self, total_bytes: u64, available_bytes: u64, projected_write_bytes: u64) -> Result<DiskDecision, SchedulerError> {
        if total_bytes == 0 || available_bytes > total_bytes { return Err(SchedulerError::InvalidDiskObservation); }
        let Some(after_write) = available_bytes.checked_sub(projected_write_bytes) else { return Ok(DiskDecision::PauseBeforeWrite); };
        let ratio_ppm = (u128::from(after_write) * 1_000_000u128 / u128::from(total_bytes)) as u64;
        if after_write < self.minimum_free_bytes || ratio_ppm < u64::from(self.minimum_free_ratio_ppm) { Ok(DiskDecision::PauseBeforeWrite) } else { Ok(DiskDecision::Continue) }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetentionBudget { pub debug_bytes: u64, pub cache_bytes: u64 }
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetentionUsage { pub debug_bytes: u64, pub cache_bytes: u64 }
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetentionDecision { Keep, EvictDebug, EvictCache, RejectNewDebug }

pub fn retention_decision(budget: RetentionBudget, usage: RetentionUsage, incoming_debug_bytes: u64) -> RetentionDecision {
    if usage.debug_bytes.saturating_add(incoming_debug_bytes) > budget.debug_bytes { return RetentionDecision::EvictDebug; }
    if usage.cache_bytes > budget.cache_bytes { return RetentionDecision::EvictCache; }
    if incoming_debug_bytes > budget.debug_bytes { RetentionDecision::RejectNewDebug } else { RetentionDecision::Keep }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(id: &str, priority: i32, tick: u64, work: u64) -> JobSpec {
        JobSpec { id: id.to_owned(), priority, enqueued_tick: tick, class: ResourceClass::Cpu, resource_units: 1, quantum_units: 1, work_units: work, checkpoint_id: format!("{id}-cp") }
    }

    #[test]
    fn capacities_and_quantum_yield_prevent_giant_job_monopoly() {
        let mut scheduler = Scheduler::new(ResourceLimits { cpu: 1, gpu: 1, vram: 1, render: 1, download: 2, disk: 1 });
        scheduler.enqueue(job("giant", 1, 0, 100)).unwrap();
        scheduler.enqueue(job("short", 0, 0, 1)).unwrap();
        let first = scheduler.dispatch(0); assert_eq!(first.len(), 1);
        scheduler.complete_slice(&first[0].job_id, 1, &first[0].checkpoint_id).unwrap();
        let second = scheduler.dispatch(1); assert_eq!(second[0].job_id, "short");
        assert_eq!(scheduler.usage(ResourceClass::Cpu), 1);
    }

    #[test]
    fn priority_and_aging_are_deterministic() {
        let mut scheduler = Scheduler::new(ResourceLimits { cpu: 1, gpu: 0, vram: 0, render: 0, download: 0, disk: 0 });
        scheduler.enqueue(job("new-high", 2, 100, 1)).unwrap();
        scheduler.enqueue(job("old-low", 0, 0, 1)).unwrap();
        let dispatch = scheduler.dispatch(100); assert_eq!(dispatch[0].job_id, "new-high");
        scheduler.complete_slice("new-high", 1, "new-high-cp").unwrap();
        let old = scheduler.dispatch(1_000_000); assert_eq!(old[0].job_id, "old-low");
    }

    #[test]
    fn pause_resume_requires_the_same_checkpoint_and_releases_usage() {
        let mut scheduler = Scheduler::new(ResourceLimits { cpu: 1, gpu: 0, vram: 0, render: 0, download: 0, disk: 0 });
        scheduler.enqueue(job("pause", 0, 0, 5)).unwrap();
        let dispatch = scheduler.dispatch(0); assert!(scheduler.pause("pause", &dispatch[0].checkpoint_id).is_ok());
        assert_eq!(scheduler.usage(ResourceClass::Cpu), 0);
        assert_eq!(scheduler.resume("pause", "wrong"), Err(SchedulerError::CheckpointMismatch));
        scheduler.resume("pause", "pause-cp").unwrap();
        assert_eq!(scheduler.dispatch(1)[0].job_id, "pause");
    }

    #[test]
    fn poisoned_job_releases_resources_without_blocking_others() {
        let mut scheduler = Scheduler::new(ResourceLimits { cpu: 1, gpu: 0, vram: 0, render: 0, download: 0, disk: 0 });
        scheduler.enqueue(job("poison", 2, 0, 5)).unwrap(); scheduler.enqueue(job("healthy", 0, 0, 1)).unwrap();
        scheduler.dispatch(0); scheduler.poison("poison").unwrap();
        assert_eq!(scheduler.dispatch(1)[0].job_id, "healthy");
    }

    #[test]
    fn rolling_disk_guard_uses_checked_ratio_and_retention_budgets() {
        let guard = DiskGuard { minimum_free_bytes: 100, minimum_free_ratio_ppm: 100_000 };
        assert_eq!(guard.observe(1_000, 300, 100).unwrap(), DiskDecision::Continue);
        assert_eq!(guard.observe(1_000, 300, 250).unwrap(), DiskDecision::PauseBeforeWrite);
        assert_eq!(guard.observe(0, 0, 0), Err(SchedulerError::InvalidDiskObservation));
        assert_eq!(retention_decision(RetentionBudget { debug_bytes: 10, cache_bytes: 20 }, RetentionUsage { debug_bytes: 9, cache_bytes: 21 }, 0), RetentionDecision::EvictCache);
        assert_eq!(retention_decision(RetentionBudget { debug_bytes: 10, cache_bytes: 20 }, RetentionUsage { debug_bytes: 9, cache_bytes: 20 }, 2), RetentionDecision::EvictDebug);
    }

    #[test]
    fn synthetic_500_job_queue_is_bounded_and_completes_short_jobs() {
        let mut scheduler = Scheduler::new(ResourceLimits { cpu: 8, gpu: 2, vram: 2, render: 2, download: 4, disk: 2 });
        for index in 0..500 { scheduler.enqueue(job(&format!("job-{index}"), (index % 5) as i32, index as u64, 1)).unwrap(); }
        let mut completed = 0usize;
        for tick in 0..100 {
            let dispatches = scheduler.dispatch(tick);
            for dispatch in dispatches { scheduler.complete_slice(&dispatch.job_id, 1, &dispatch.checkpoint_id).unwrap(); completed += 1; }
            if completed == 500 { break; }
        }
        assert_eq!(completed, 500);
        assert_eq!(scheduler.queued_jobs(), 0);
    }
}
