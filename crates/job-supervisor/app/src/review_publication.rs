//! Native historical references for cumulative publication recovery.
//! Reading a reference verifies its bytes and state identity. It does not admit
//! its graph/media, rewrite a public revision or authorize a SQLite mutation.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{closed, decode, identity, text, AdmittedSnapshot};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, review_graph::ReviewToken, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::time::Instant;

const LIMIT: usize = 64 * 1024 * 1024;
const MAX_UNDO: u16 = 256;
const MAX_PROOF_DEPTH: usize = 256;
const MAX_PROOF_READS: usize = 4096;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn hash(raw: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
}

fn hash_value(value: &Value) -> SupervisorResult<&str> {
    let value = text(value, 71)?;
    if value.len() != 71
        || !value.starts_with("sha256:")
        || !value[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid("historical reference hash is not canonical SHA256"));
    }
    Ok(value)
}

fn decimal(value: &Value) -> SupervisorResult<u64> {
    let value = text(value, 20)?;
    let number = value
        .parse::<u64>()
        .map_err(|_| invalid("historical public revision is invalid"))?;
    if number.to_string() != value {
        return Err(invalid(
            "historical public revision is not canonical decimal",
        ));
    }
    Ok(number)
}

pub(super) fn token(state: &ReviewGraphState) -> Value {
    json!({"job_id":state.token.job_id,"revision":state.token.revision.to_string(),
        "snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "original_manifest_sha256":state.token.original_manifest_sha256})
}

/// One caller-owned deadline is shared by the whole physical proof walk. There
/// is no admission cache: repeated references are counted and read again.
pub(super) struct RecoveryWalk {
    deadline: Instant,
    proof_reads: usize,
    active: HashSet<String>,
}

impl RecoveryWalk {
    pub(super) fn new(deadline: Instant) -> Self {
        Self {
            deadline,
            proof_reads: 0,
            active: HashSet::new(),
        }
    }

    pub(super) fn deadline(&self) -> Instant {
        self.deadline
    }

    pub(super) fn check(&self) -> SupervisorResult<()> {
        if Instant::now() >= self.deadline {
            return Err(invalid("cumulative publication recovery deadline exceeded"));
        }
        Ok(())
    }

    pub(super) fn read_metadata(
        &self,
        assets: &AcceptedAudioStore,
        object: &AcceptedJsonDocument,
    ) -> SupervisorResult<String> {
        self.check()?;
        let raw = assets.read_json(object)?;
        self.check()?;
        Ok(raw)
    }

    /// The caller must perform full semantic/physical admission inside this
    /// scope. A scope always retires its cycle marker, including on refusal.
    pub(super) fn with_proof<T>(
        &mut self,
        proof_sha256: &str,
        admit: impl FnOnce(&mut Self) -> SupervisorResult<T>,
    ) -> SupervisorResult<T> {
        self.check()?;
        hash_value(&json!(proof_sha256))?;
        if self.proof_reads >= MAX_PROOF_READS || self.active.len() >= MAX_PROOF_DEPTH {
            return Err(invalid(
                "cumulative publication proof walk exceeds its bound",
            ));
        }
        if !self.active.insert(proof_sha256.into()) {
            return Err(invalid(
                "cumulative publication proof references form a cycle",
            ));
        }
        self.proof_reads += 1;
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| admit(self)))
            .unwrap_or_else(|_| Err(invalid("cumulative publication proof admission panicked")));
        self.active.remove(proof_sha256);
        self.check()?;
        result
    }
}

fn verify_native_view(view: &Value, state: &ReviewGraphState) -> SupervisorResult<()> {
    closed(
        view,
        &[
            "schema_version",
            "kind",
            "current_token",
            "current_snapshot_json",
            "origin_token",
            "origin_graph_json",
            "source_graph_json",
            "current_timeline_json",
            "content_composition_json",
            "composition_snapshot_json",
            "effective_composition_json",
            "spoken_assignments",
            "metadata_pins",
            "generation_dependencies_complete",
            "processing_release_admitted",
            "processing_release",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    closed(
        &view["origin_token"],
        &[
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ],
    )?;
    let origin_graph = view["origin_graph_json"]
        .as_str()
        .ok_or_else(|| invalid("historical origin graph missing"))?;
    if view["schema_version"].as_u64() != Some(1)
        || view["kind"] != "review_native_effective_state"
        || view["current_token"] != token(state)
        || view["current_snapshot_json"] != state.snapshot_json
        || view["origin_token"]["job_id"] != state.token.job_id
        || view["origin_token"]["revision"] != "0"
        || view["origin_token"]["original_manifest_sha256"] != state.token.original_manifest_sha256
        || hash_value(&view["origin_token"]["graph_sha256"])? != hash(origin_graph)
        || view["generation_dependencies_complete"] != true
        || view["processing_release_admitted"] != false
        || !view["processing_release"].is_null()
        || view["active_graph_published"] != false
        || view["correction_ready"] != false
    {
        return Err(invalid(
            "historical reference substitutes its native effective view",
        ));
    }
    Ok(())
}

fn state_from_parts(
    record: &Value,
    snapshot_raw: &str,
    graph_raw: &str,
    expected: &ReviewGraphState,
) -> SupervisorResult<ReviewGraphState> {
    closed(
        record,
        &[
            "schema_version",
            "kind",
            "token",
            "original_artifact_id",
            "snapshot",
            "graph",
            "undo_depth",
            "undo_target_revision",
            "effective_state",
        ],
    )?;
    closed(
        &record["token"],
        &[
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ],
    )?;
    if record["schema_version"].as_u64() != Some(1)
        || record["kind"] != "review_native_generation_state_reference"
        || record["token"]["job_id"] != expected.token.job_id
        || record["token"]["original_manifest_sha256"] != expected.token.original_manifest_sha256
        || record["original_artifact_id"] != expected.original_artifact_id
        || snapshot_raw
            .len()
            .checked_add(graph_raw.len())
            .filter(|n| *n <= LIMIT)
            .is_none()
    {
        return Err(invalid(
            "historical reference changed its original job or payload bound",
        ));
    }
    identity(&record["token"]["job_id"])?;
    identity(&record["original_artifact_id"])?;
    let revision = decimal(&record["token"]["revision"])?;
    let snapshot = AdmittedSnapshot::read(snapshot_raw, &expected.token.job_id, revision)?;
    decode(graph_raw, LIMIT)?;
    if hash_value(&record["token"]["snapshot_sha256"])? != snapshot.sha256()
        || hash_value(&record["token"]["graph_sha256"])? != hash(graph_raw)
        || hash_value(&record["token"]["original_manifest_sha256"])?
            != expected.token.original_manifest_sha256
    {
        return Err(invalid(
            "historical state does not match its exact public token hashes",
        ));
    }
    let depth = record["undo_depth"]
        .as_u64()
        .filter(|n| *n <= u64::from(MAX_UNDO))
        .ok_or_else(|| invalid("historical undo depth exceeds the native store bound"))?
        as u16;
    let target = if record["undo_target_revision"].is_null() {
        None
    } else {
        Some(decimal(&record["undo_target_revision"])?)
    };
    if (depth == 0) != target.is_none()
        || target.is_some_and(|target| target >= revision)
        || (revision == 0 && depth != 0)
    {
        return Err(invalid("historical undo ancestry is inconsistent"));
    }
    Ok(ReviewGraphState {
        token: ReviewToken {
            job_id: expected.token.job_id.clone(),
            revision,
            snapshot_sha256: snapshot.sha256(),
            graph_sha256: hash(graph_raw),
            original_manifest_sha256: expected.token.original_manifest_sha256.clone(),
        },
        original_artifact_id: expected.original_artifact_id.clone(),
        snapshot_json: snapshot_raw.into(),
        graph_json: graph_raw.into(),
        undo_target_revision: target,
        undo_depth: depth,
    })
}

/// This is an identity/byte reference, deliberately not AdmittedEffectiveState.
/// A graph reader must validate its physical content before using it as a
/// generation baseline; parsing this object never makes it the live head.
pub(super) struct ReferencedState {
    state: ReviewGraphState,
    effective_view: Value,
}

impl ReferencedState {
    pub(super) fn state(&self) -> &ReviewGraphState {
        &self.state
    }
    pub(super) fn effective_view(&self) -> &Value {
        &self.effective_view
    }
}

pub(super) fn read_reference(
    store: &DurableStore,
    assets: &AcceptedAudioStore,
    reference: &Value,
    original_binding: &ReviewGraphState,
    walk: &mut RecoveryWalk,
) -> SupervisorResult<ReferencedState> {
    let object = accepted_json(reference, assets)?;
    walk.with_proof(&object.sha256, |walk| {
        let record = decode(&walk.read_metadata(assets, &object)?, LIMIT)?;
        let snapshot = accepted_json(&record["snapshot"], assets)?;
        let graph = accepted_json(&record["graph"], assets)?;
        let effective = accepted_json(&record["effective_state"], assets)?;
        let snapshot_raw = walk.read_metadata(assets, &snapshot)?;
        let graph_raw = walk.read_metadata(assets, &graph)?;
        let state = state_from_parts(&record, &snapshot_raw, &graph_raw, original_binding)?;
        if store.review_graph_revision(&state.token)?.as_ref() != Some(&state) {
            return Err(invalid(
                "historical state is not its exact immutable supervisor-recorded version",
            ));
        }
        if snapshot.sha256 != state.token.snapshot_sha256
            || graph.sha256 != state.token.graph_sha256
        {
            return Err(invalid(
                "historical reference descriptors changed the native state bytes",
            ));
        }
        let view = decode(&walk.read_metadata(assets, &effective)?, LIMIT)?;
        verify_native_view(&view, &state)?;
        // Re-read accepted immutable references under the same deadline. This
        // verifies bytes only; physical graph/cue/render admission still follows.
        for object in [&snapshot, &graph, &effective, &object] {
            walk.read_metadata(assets, object)?;
        }
        Ok(ReferencedState {
            state,
            effective_view: view,
        })
    })
}

fn require_current(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid(
            "current generation changed before historical proof sealing",
        ));
    }
    Ok(())
}

pub(super) fn seal_current(
    store: &DurableStore,
    assets: &AcceptedAudioStore,
    current: &super::review_effective::AdmittedEffectiveState,
    deadline: Instant,
) -> SupervisorResult<AcceptedJsonDocument> {
    let mut walk = RecoveryWalk::new(deadline);
    let state = current.state();
    walk.check()?;
    require_current(store, state)?;
    let view = current.packet()?;
    verify_native_view(&view, state)?;
    let snapshot = assets.accept_json(&state.snapshot_json)?;
    let graph = assets.accept_json(&state.graph_json)?;
    let effective = assets.accept_json(&view.to_string())?;
    let record = json!({"schema_version":1,"kind":"review_native_generation_state_reference",
        "token":token(state),"original_artifact_id":state.original_artifact_id,"snapshot":descriptor(&snapshot),
        "graph":descriptor(&graph),"undo_depth":state.undo_depth,
        "undo_target_revision":state.undo_target_revision.map(|n|n.to_string()),"effective_state":descriptor(&effective)});
    let object = assets.accept_json(&record.to_string())?;
    let reopened = read_reference(store, assets, &descriptor(&object), state, &mut walk)?;
    if reopened.state() != state || reopened.effective_view() != &view {
        return Err(invalid(
            "historical reference changed during native sealing",
        ));
    }
    require_current(store, state)?;
    walk.check()?;
    Ok(object)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn fixture() -> (ReviewGraphState, Value) {
        let fixture = decode(
            include_str!("../../../../tests/review/native_snapshot_v2.json"),
            LIMIT,
        )
        .unwrap();
        let snapshot = &fixture["snapshot"];
        let raw = snapshot.to_string();
        let admitted = AdmittedSnapshot::read(
            &raw,
            snapshot["job_id"].as_str().unwrap(),
            snapshot["revision"].as_u64().unwrap(),
        )
        .unwrap();
        let graph = r#"{"kind":"reference-metadata-fixture-only"}"#;
        let state = ReviewGraphState {
            token: ReviewToken {
                job_id: admitted.job().into(),
                revision: admitted.revision(),
                snapshot_sha256: admitted.sha256(),
                graph_sha256: hash(graph),
                original_manifest_sha256: format!("sha256:{}", "a".repeat(64)),
            },
            original_artifact_id: "original-export".into(),
            snapshot_json: raw,
            graph_json: graph.into(),
            undo_depth: 0,
            undo_target_revision: None,
        };
        let doc = |sha: &str| json!({"path":"/metadata-fixture-only","sha256":sha,"size_bytes":1});
        let record = json!({"schema_version":1,"kind":"review_native_generation_state_reference","token":token(&state),
            "original_artifact_id":state.original_artifact_id,"snapshot":doc(&state.token.snapshot_sha256),
            "graph":doc(&state.token.graph_sha256),"undo_depth":0,"undo_target_revision":null,
            "effective_state":doc(&format!("sha256:{}","b".repeat(64)))});
        (state, record)
    }

    #[test]
    fn historical_state_reference_preserves_wide_restored_public_counter_without_normalization() {
        let (state, mut record) = fixture();
        assert!(state.token.revision > (1 << 53));
        assert_eq!(
            state_from_parts(&record, &state.snapshot_json, &state.graph_json, &state).unwrap(),
            state
        );
        record["undo_depth"] = 1.into();
        record["undo_target_revision"] = (state.token.revision - 2).to_string().into();
        let read =
            state_from_parts(&record, &state.snapshot_json, &state.graph_json, &state).unwrap();
        assert_eq!(read.token, state.token);
        assert_eq!(read.undo_depth, 1);
        assert_eq!(read.undo_target_revision, Some(state.token.revision - 2));
    }

    #[test]
    fn historical_state_reference_refuses_aliases_wrong_hash_binding_and_false_undo_ancestry() {
        let (state, record) = fixture();
        for mutation in 0..10 {
            let mut bad = record.clone();
            match mutation {
                0 => bad["token"]["revision"] = format!("0{}", state.token.revision).into(),
                1 => bad["token"]["revision"] = state.token.revision.into(),
                2 => bad["token"]["snapshot_sha256"] = format!("sha256:{}", "c".repeat(64)).into(),
                3 => bad["token"]["graph_sha256"] = format!("sha256:{}", "c".repeat(64)).into(),
                4 => {
                    bad["token"]["original_manifest_sha256"] =
                        format!("sha256:{}", "c".repeat(64)).into()
                }
                5 => bad["original_artifact_id"] = "foreign-export".into(),
                6 => bad["undo_depth"] = 1.into(),
                7 => {
                    bad["undo_depth"] = 1.into();
                    bad["undo_target_revision"] = state.token.revision.to_string().into();
                }
                8 => {
                    bad["undo_depth"] = 257.into();
                    bad["undo_target_revision"] = "0".into();
                }
                _ => bad["client_authority"] = true.into(),
            }
            assert!(
                state_from_parts(&bad, &state.snapshot_json, &state.graph_json, &state).is_err(),
                "mutation {mutation}"
            );
        }
        assert!(state_from_parts(&record, &state.snapshot_json, "{}", &state).is_err());
    }

    #[test]
    fn recovery_walk_refuses_cycles_deadline_and_repeated_proof_read_exhaustion() {
        let pin = format!("sha256:{}", "a".repeat(64));
        let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(30));
        assert!(walk
            .with_proof(&pin, |walk| walk.with_proof(&pin, |_| Ok(())))
            .is_err());
        assert!(walk.active.is_empty());
        assert!(walk
            .with_proof(&pin, |_| -> SupervisorResult<()> { panic!("proof fault") })
            .is_err());
        assert!(walk.active.is_empty());
        assert!(walk.with_proof(&pin, |_| Ok(())).is_ok());
        for _ in walk.proof_reads..MAX_PROOF_READS {
            walk.with_proof(&pin, |_| Ok(())).unwrap();
        }
        assert!(walk.with_proof(&pin, |_| Ok(())).is_err());
        let mut expired = RecoveryWalk::new(Instant::now() - Duration::from_secs(1));
        let mut executed = false;
        assert!(expired
            .with_proof(&pin, |_| {
                executed = true;
                Ok(())
            })
            .is_err());
        assert!(!executed);
    }

    #[test]
    fn historical_view_refuses_client_authority_or_missing_physical_dependency_evidence() {
        let (state, _) = fixture();
        let origin_graph = r#"{"kind":"origin-reference-metadata-fixture-only"}"#;
        let mut origin = token(&state);
        origin["revision"] = "0".into();
        origin["graph_sha256"] = hash(origin_graph).into();
        let view = json!({"schema_version":1,"kind":"review_native_effective_state","current_token":token(&state),
            "current_snapshot_json":state.snapshot_json,"origin_token":origin,"origin_graph_json":origin_graph,
            "source_graph_json":"{}","current_timeline_json":"{}","content_composition_json":null,
            "composition_snapshot_json":null,"effective_composition_json":null,"spoken_assignments":null,
            "metadata_pins":{},"generation_dependencies_complete":true,"processing_release_admitted":false,
            "processing_release":null,"active_graph_published":false,"correction_ready":false});
        verify_native_view(&view, &state).unwrap();
        for mutation in 0..6 {
            let mut bad = view.clone();
            match mutation {
                0 => bad["current_token"]["revision"] = "0".into(),
                1 => bad["origin_graph_json"] = "{}".into(),
                2 => bad["generation_dependencies_complete"] = false.into(),
                3 => bad["active_graph_published"] = true.into(),
                4 => bad["processing_release_admitted"] = true.into(),
                _ => bad["client_authority"] = true.into(),
            }
            assert!(
                verify_native_view(&bad, &state).is_err(),
                "mutation {mutation}"
            );
        }
    }
}
