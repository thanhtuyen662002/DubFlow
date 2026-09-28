//! Exact model/runtime pins and bounded cleanup planning.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct VersionRef { pub id: String, pub version: String, pub sha256: String }

impl VersionRef {
    pub fn new(id: impl Into<String>, version: impl Into<String>, sha256: impl Into<String>) -> Self { Self { id: id.into(), version: version.into(), sha256: sha256.into() } }
    fn valid(&self) -> bool { !self.id.is_empty() && !self.version.is_empty() && self.sha256.len() == 64 && self.sha256.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobPin { pub job_id: String, pub terminal: bool, pub required_versions: Vec<VersionRef> }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PackageKind { Cache, Model, Runtime }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstalledPackage { pub reference: VersionRef, pub kind: PackageKind, pub bytes: u64, pub reacquirable: bool }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResumeAction { Resume, ReacquireExact(VersionRef), Invalidate(String) }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CleanupAction { EvictCache(VersionRef), EvictPackage(VersionRef), DeferActive(VersionRef) }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CleanupPlan { pub actions: Vec<CleanupAction>, pub bytes_scheduled: u64, pub more_pending: bool }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RetentionError { InvalidReference, DuplicateJob, InvalidJobId }
impl fmt::Display for RetentionError { fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{self:?}") } }
impl std::error::Error for RetentionError {}

#[derive(Debug, Default)]
pub struct RetentionGraph { pins: BTreeMap<String, JobPin>, active: BTreeSet<VersionRef> }

impl RetentionGraph {
    pub fn pin_job(&mut self, pin: JobPin) -> Result<(), RetentionError> {
        if pin.job_id.is_empty() || pin.job_id.len() > 128 { return Err(RetentionError::InvalidJobId); }
        if pin.required_versions.iter().any(|reference| !reference.valid()) { return Err(RetentionError::InvalidReference); }
        if self.pins.insert(pin.job_id.clone(), pin).is_some() { return Err(RetentionError::DuplicateJob); }
        Ok(())
    }
    pub fn set_active(&mut self, reference: VersionRef, active: bool) -> Result<(), RetentionError> {
        if !reference.valid() { return Err(RetentionError::InvalidReference); }
        if active { self.active.insert(reference); } else { self.active.remove(&reference); }
        Ok(())
    }
    pub fn resume_action(&self, job_id: &str, reference: &VersionRef, installed: &[InstalledPackage]) -> ResumeAction {
        let Some(pin) = self.pins.get(job_id) else { return ResumeAction::Invalidate("job pin is missing".to_owned()); };
        if !pin.required_versions.contains(reference) { return ResumeAction::Invalidate("requested reference is not pinned by the job".to_owned()); }
        if installed.iter().any(|package| package.reference == *reference) { ResumeAction::Resume }
        else { ResumeAction::ReacquireExact(reference.clone()) }
    }
    pub fn plan_cleanup(&self, installed: &[InstalledPackage], max_bytes: u64, max_actions: usize) -> CleanupPlan {
        let protected: BTreeSet<VersionRef> = self.pins.values().filter(|pin| !pin.terminal).flat_map(|pin| pin.required_versions.iter().cloned()).chain(self.active.iter().cloned()).collect();
        let mut candidates = installed.iter().filter(|package| !protected.contains(&package.reference));
        let mut ordered = Vec::new();
        ordered.extend(candidates.by_ref().filter(|package| package.kind == PackageKind::Cache));
        ordered.extend(candidates);
        let mut bytes_scheduled = 0u64; let mut actions = Vec::new();
        for package in ordered {
            if actions.len() >= max_actions || bytes_scheduled.saturating_add(package.bytes) > max_bytes { break; }
            bytes_scheduled = bytes_scheduled.saturating_add(package.bytes);
            actions.push(if package.kind == PackageKind::Cache { CleanupAction::EvictCache(package.reference.clone()) } else { CleanupAction::EvictPackage(package.reference.clone()) });
        }
        let scheduled: BTreeSet<VersionRef> = actions.iter().map(|action| match action { CleanupAction::EvictCache(reference) | CleanupAction::EvictPackage(reference) | CleanupAction::DeferActive(reference) => reference.clone() }).collect();
        let more_pending = installed.iter().any(|package| !protected.contains(&package.reference) && !scheduled.contains(&package.reference));
        CleanupPlan { actions, bytes_scheduled, more_pending }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    fn reference(id: &str, hash: &str) -> VersionRef { VersionRef::new(id, "1.0.0", hash) }
    fn package(id: &str, kind: PackageKind, bytes: u64) -> InstalledPackage { InstalledPackage { reference: reference(id, if id == "a" { A } else { B }), kind, bytes, reacquirable: true } }

    #[test] fn non_terminal_jobs_pin_exact_versions() { let mut graph = RetentionGraph::default(); graph.pin_job(JobPin { job_id: "job-1".to_owned(), terminal: false, required_versions: vec![reference("model", A)] }).unwrap(); let installed = vec![InstalledPackage { reference: reference("model", A), kind: PackageKind::Model, bytes: 10, reacquirable: true }]; assert_eq!(graph.resume_action("job-1", &reference("model", A), &installed), ResumeAction::Resume); }
    #[test] fn active_package_is_never_evicted() { let mut graph = RetentionGraph::default(); let active = reference("runtime", A); graph.set_active(active.clone(), true).unwrap(); let plan = graph.plan_cleanup(&[InstalledPackage { reference: active.clone(), kind: PackageKind::Runtime, bytes: 100, reacquirable: true }, package("cache", PackageKind::Cache, 5)], 1_000, 10); assert!(!plan.actions.iter().any(|action| matches!(action, CleanupAction::EvictPackage(reference) if reference == &active))); }
    #[test] fn caches_are_evicted_before_unreferenced_models_and_plan_is_bounded() { let graph = RetentionGraph::default(); let installed = vec![package("model", PackageKind::Model, 20), package("cache", PackageKind::Cache, 5), package("runtime", PackageKind::Runtime, 30)]; let plan = graph.plan_cleanup(&installed, 5, 10); assert_eq!(plan.actions, vec![CleanupAction::EvictCache(reference("cache", B))]); assert!(plan.more_pending); }
    #[test] fn missing_exact_version_reacquires_or_invalidates_without_substitution() { let mut graph = RetentionGraph::default(); let required = reference("model", A); graph.pin_job(JobPin { job_id: "job".to_owned(), terminal: false, required_versions: vec![required.clone()] }).unwrap(); assert_eq!(graph.resume_action("job", &required, &[]), ResumeAction::ReacquireExact(required.clone())); assert_eq!(graph.resume_action("other", &required, &[]), ResumeAction::Invalidate("job pin is missing".to_owned())); }
    #[test] fn cleanup_is_resumable_across_bounded_plans() { let graph = RetentionGraph::default(); let installed = vec![package("a", PackageKind::Cache, 1), package("b", PackageKind::Cache, 1), package("c", PackageKind::Cache, 1)]; let first = graph.plan_cleanup(&installed, 1, 1); assert_eq!(first.actions.len(), 1); assert!(first.more_pending); let second = graph.plan_cleanup(&installed[1..], 1, 1); assert_eq!(second.actions.len(), 1); }
}
