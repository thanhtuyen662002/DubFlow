//! Versioned artifact provenance and deterministic downstream invalidation.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ArtifactKind { Input, Timeline, Asr, Ocr, Diarization, Translation, Tts, Mix, Render, Qc }

impl ArtifactKind {
    pub fn wire_name(self) -> &'static str {
        match self { Self::Input => "INPUT", Self::Timeline => "TIMELINE", Self::Asr => "ASR", Self::Ocr => "OCR", Self::Diarization => "DIARIZATION", Self::Translation => "TRANSLATION", Self::Tts => "TTS", Self::Mix => "MIX", Self::Render => "RENDER", Self::Qc => "QC" }
    }
    fn time_dependent(self) -> bool { matches!(self, Self::Timeline | Self::Translation | Self::Tts | Self::Mix | Self::Render | Self::Qc) }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArtifactStatus { Valid, Stale, Missing, Corrupt }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Provenance {
    pub producer_version: String,
    pub input_hashes: Vec<String>,
    pub config_hash: String,
    pub model_hash: String,
    pub contract_version: String,
}

impl Provenance {
    fn validate(&self) -> Result<(), GraphError> {
        if self.producer_version.is_empty() || self.contract_version.is_empty() || !valid_hash(&self.config_hash) || !valid_hash(&self.model_hash) || self.input_hashes.iter().any(|hash| !valid_hash(hash)) { return Err(GraphError::InvalidProvenance); }
        Ok(())
    }
}

fn valid_hash(value: &str) -> bool { value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactNode {
    pub id: String,
    pub kind: ArtifactKind,
    pub scope: String,
    pub inputs: Vec<String>,
    pub provenance: Provenance,
    pub status: ArtifactStatus,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Change { Voice { scope: String }, Translation { scope: String }, Timeline, MissingOrCorrupt { id: String }, Config { id: String } }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GraphError { InvalidId, InvalidScope, InvalidProvenance, DuplicateId, UnknownInput(String), Cycle, UnknownArtifact(String) }
impl fmt::Display for GraphError { fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{self:?}") } }
impl std::error::Error for GraphError {}

#[derive(Debug, Default)]
pub struct ArtifactGraph { nodes: BTreeMap<String, ArtifactNode> }

impl ArtifactGraph {
    pub fn register(&mut self, node: ArtifactNode) -> Result<(), GraphError> {
        if node.id.is_empty() || node.id.len() > 256 || !node.id.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-')) { return Err(GraphError::InvalidId); }
        if node.scope.is_empty() || node.scope.len() > 256 { return Err(GraphError::InvalidScope); }
        node.provenance.validate()?;
        if self.nodes.contains_key(&node.id) { return Err(GraphError::DuplicateId); }
        if node.inputs.iter().any(|input| !self.nodes.contains_key(input)) { return Err(GraphError::UnknownInput(node.id)); }
        self.nodes.insert(node.id.clone(), node);
        if self.has_cycle() { self.nodes.remove(&node.id); return Err(GraphError::Cycle); }
        Ok(())
    }

    pub fn node(&self, id: &str) -> Result<&ArtifactNode, GraphError> { self.nodes.get(id).ok_or_else(|| GraphError::UnknownArtifact(id.to_owned())) }
    pub fn can_reuse(&self, id: &str, provenance: &Provenance) -> Result<bool, GraphError> { let node = self.node(id)?; Ok(node.status == ArtifactStatus::Valid && node.provenance == *provenance) }

    pub fn apply_change(&mut self, change: Change) -> Result<BTreeSet<String>, GraphError> {
        let mut roots = Vec::new();
        match change {
            Change::Voice { scope } => roots.extend(self.nodes.values().filter(|node| node.kind == ArtifactKind::Tts && node.scope == scope).map(|node| node.id.clone())),
            Change::Translation { scope } => roots.extend(self.nodes.values().filter(|node| node.kind == ArtifactKind::Translation && node.scope == scope).map(|node| node.id.clone())),
            Change::Timeline => roots.extend(self.nodes.values().filter(|node| node.kind == ArtifactKind::Timeline).map(|node| node.id.clone())),
            Change::MissingOrCorrupt { id } | Change::Config { id } => { self.node(&id)?; roots.push(id); }
        }
        let affected = self.downstream_closure(&roots);
        for id in &affected { if let Some(node) = self.nodes.get_mut(id) { node.status = ArtifactStatus::Stale; } }
        Ok(affected)
    }

    pub fn mark_missing_or_corrupt(&mut self, id: &str, corrupt: bool) -> Result<BTreeSet<String>, GraphError> {
        let affected = self.apply_change(Change::MissingOrCorrupt { id: id.to_owned() })?;
        if let Some(node) = self.nodes.get_mut(id) { node.status = if corrupt { ArtifactStatus::Corrupt } else { ArtifactStatus::Missing }; }
        Ok(affected)
    }

    fn downstream_closure(&self, roots: &[String]) -> BTreeSet<String> {
        let mut affected = BTreeSet::new(); let mut queue = VecDeque::from(roots.to_vec());
        while let Some(id) = queue.pop_front() { if !affected.insert(id.clone()) { continue; } for node in self.nodes.values().filter(|node| node.inputs.iter().any(|input| input == &id)) { queue.push_back(node.id.clone()); } }
        affected
    }

    fn has_cycle(&self) -> bool {
        fn visit(id: &str, graph: &ArtifactGraph, visiting: &mut BTreeSet<String>, visited: &mut BTreeSet<String>) -> bool {
            if visiting.contains(id) { return true; } if !visited.insert(id.to_owned()) { return false; }
            visiting.insert(id.to_owned());
            if let Some(node) = graph.nodes.get(id) { for input in &node.inputs { if visit(input, graph, visiting, visited) { return true; } } }
            visiting.remove(id); false
        }
        let mut visiting = BTreeSet::new(); let mut visited = BTreeSet::new(); self.nodes.keys().any(|id| visit(id, self, &mut visiting, &mut visited))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const H: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    fn node(id: &str, kind: ArtifactKind, scope: &str, inputs: &[&str]) -> ArtifactNode { ArtifactNode { id: id.to_owned(), kind, scope: scope.to_owned(), inputs: inputs.iter().map(|input| (*input).to_owned()).collect(), provenance: Provenance { producer_version: "1".to_owned(), input_hashes: vec![H.to_owned()], config_hash: H.to_owned(), model_hash: H.to_owned(), contract_version: "v1".to_owned() }, status: ArtifactStatus::Valid } }
    fn graph() -> ArtifactGraph { let mut graph = ArtifactGraph::default(); graph.register(node("input", ArtifactKind::Input, "job", &[])).unwrap(); graph.register(node("asr", ArtifactKind::Asr, "job", &["input"])).unwrap(); graph.register(node("translation-a", ArtifactKind::Translation, "line-a", &["asr"])).unwrap(); graph.register(node("translation-b", ArtifactKind::Translation, "line-b", &["asr"])).unwrap(); graph.register(node("tts-a", ArtifactKind::Tts, "line-a", &["translation-a"])).unwrap(); graph.register(node("tts-b", ArtifactKind::Tts, "line-b", &["translation-b"])).unwrap(); graph.register(node("mix", ArtifactKind::Mix, "job", &["tts-a", "tts-b"])).unwrap(); graph.register(node("qc", ArtifactKind::Qc, "job", &["mix"])).unwrap(); graph }

    #[test] fn voice_change_invalidates_only_affected_tts_and_downstream() { let mut graph = graph(); let affected = graph.apply_change(Change::Voice { scope: "line-a".to_owned() }).unwrap(); assert!(affected.contains("tts-a") && affected.contains("mix") && affected.contains("qc")); assert!(!affected.contains("tts-b") && !affected.contains("asr")); assert_eq!(graph.node("qc").unwrap().status, ArtifactStatus::Stale); }
    #[test] fn translation_change_does_not_invalidate_asr_or_unrelated_line() { let mut graph = graph(); let affected = graph.apply_change(Change::Translation { scope: "line-b".to_owned() }).unwrap(); assert!(affected.contains("translation-b") && affected.contains("tts-b")); assert!(!affected.contains("asr") && !affected.contains("translation-a") && !affected.contains("tts-a")); }
    #[test] fn missing_corrupt_artifact_invalidates_all_descendants() { let mut graph = graph(); let affected = graph.mark_missing_or_corrupt("asr", false).unwrap(); assert!(affected.contains("asr") && affected.contains("translation-a") && affected.contains("qc")); assert_eq!(graph.node("asr").unwrap().status, ArtifactStatus::Missing); }
    #[test] fn provenance_mismatch_disables_reuse() { let graph = graph(); let mut provenance = graph.node("asr").unwrap().provenance.clone(); assert!(graph.can_reuse("asr", &provenance).unwrap()); provenance.config_hash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_owned(); assert!(!graph.can_reuse("asr", &provenance).unwrap()); }
    #[test] fn invalid_registration_is_rejected() { let mut graph = graph(); assert_eq!(graph.register(node("bad/id", ArtifactKind::Input, "job", &[])), Err(GraphError::InvalidId)); assert!(matches!(graph.register(node("unknown", ArtifactKind::Tts, "x", &["missing"])), Err(GraphError::UnknownInput(_)))); }
}
