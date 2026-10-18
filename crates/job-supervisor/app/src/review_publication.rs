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

fn v3_shape(record: &Value) -> SupervisorResult<()> {
    closed(
        record,
        &[
            "schema_version",
            "kind",
            "before_state",
            "origin_token",
            "proposal",
            "corrected_timeline",
            "context",
            "retained_render",
            "processing_render",
            "prepared_snapshot",
            "execution_identity",
            "output_volume_id",
            "mixed",
            "mix_packet",
            "processing_mix",
            "speech_inventory",
        ],
    )?;
    if record["schema_version"].as_u64() != Some(3)
        || record["kind"] != "review_native_publication_v3"
    {
        return Err(invalid("unknown cumulative native publication version"));
    }
    Ok(())
}

/// A cumulative publication reopens its actual immutable before-state and all
/// physical dependencies. Nothing in a sealed byte reference grants admission
/// by itself, and no public token is rewritten to bypass an original reader.
pub(super) fn read_v3(
    store: &DurableStore,
    data: &std::path::Path,
    current: &ReviewGraphState,
    assets: &AcceptedAudioStore,
    object: &AcceptedJsonDocument,
    walk: &mut RecoveryWalk,
) -> SupervisorResult<super::review_active::AdmittedPublication> {
    walk.with_proof(&object.sha256, |walk| {
        let record = decode(&walk.read_metadata(assets, object)?, LIMIT)?;
        v3_shape(&record)?;
        let reference = read_reference(store, assets, &record["before_state"], current, walk)?;
        let before = super::review_effective::read_historical(store, data, &reference, walk)?;
        let state = before.state();
        if record["origin_token"] != token(before.origin())
            || state.token.revision > current.token.revision
        {
            return Err(invalid("cumulative publication substitutes its origin or future before-state"));
        }
        let proposal_object = accepted_json(&record["proposal"], assets)?;
        let revision = state.token.revision.checked_add(1)
            .ok_or_else(|| invalid("cumulative proposal revision overflow"))?;
        let proposal = AdmittedSnapshot::read(&walk.read_metadata(assets, &proposal_object)?, &state.token.job_id, revision)?;
        let seed = super::review_seed::read_original(store, data, before.origin())?;
        let seed_graph = decode(&seed.graph, LIMIT)?;
        let mode = match seed_graph["mode"].as_str() {
            Some("b1") => super::review_document::ReviewMode::Subtitles,
            Some("b2") => super::review_document::ReviewMode::Dubbed,
            _ => return Err(invalid("cumulative publication original mode missing")),
        };
        before.snapshot().validate_correction(&proposal, mode)?;
        let timeline = accepted_json(&record["corrected_timeline"], assets)?;
        let context = accepted_json(&record["context"], assets)?;
        let processing_object = accepted_json(&record["processing_render"], assets)?;
        let saved_processing = decode(&walk.read_metadata(assets, &processing_object)?, LIMIT)?;
        let release = &saved_processing["processing_timeline"]["processing_release"];
        let processing_root = std::path::PathBuf::from(
            text(&release["processing_root"], 32767)?);
        let processing = super::review_runtime::AdmittedProcessingRuntime::read(
            store, &state.token.job_id, &processing_root, &release["release_manifest"],
            text(&release["source_sha"], 64)?)?;
        if exact_value(&saved_processing, &processing.effective_render_receipt(Some(&context.sha256))?)? == false {
            return Err(invalid("cumulative processing recipe differs from its source-matched admission"));
        }
        walk.check()?;
        let pin = super::review_retention::NativeOutputPin::read(&seed.output_root)?;
        if record["output_volume_id"] != pin.volume_id() {
            return Err(invalid("cumulative publication output volume changed"));
        }
        if seed_graph["mode"] == "b2" {
            super::review_speech::verify_effective_inventory(
                super::review_speech::EffectiveInventoryInputs {
                    store, data, assets, before: &before, after: &proposal,
                    mixed: &record["mixed"], mix_packet: &record["mix_packet"],
                    processing_mix: &record["processing_mix"], processing: &processing,
                    output_pin: &pin,
                }, &record["speech_inventory"], walk)?;
        } else if ["mixed", "mix_packet", "processing_mix", "speech_inventory"]
            .into_iter().any(|key| !record[key].is_null()) {
            return Err(invalid("B1 cumulative publication cannot invent speech or mixed audio"));
        }
        let candidates = seed.output_root.join(".dubflow-review-candidates")
            .join(format!("{:x}", Sha256::digest(state.token.job_id.as_bytes())));
        let input = super::review_render::EffectiveRenderInputs {
            processing: &processing, store, data, assets, state,
            current: &before, after: &proposal, timeline: &timeline,
            mixed: if seed_graph["mode"] == "b2" { Some(&record["mixed"]) } else { None },
            output_pin: &pin, candidates: &candidates,
        };
        let recovered = super::review_render::recover_effective_retained(&input, &record, walk)?;
        let expected = recovered.prepare_snapshot(&proposal)?;
        let prepared = accepted_json(&record["prepared_snapshot"], assets)?;
        if walk.read_metadata(assets, &prepared)? != expected.json() {
            return Err(invalid("cumulative snapshot differs from retained physical playback"));
        }
        let retained = accepted_json(&record["retained_render"], assets)?;
        for metadata in [object, &proposal_object, &timeline, &context, &processing_object, &prepared, &retained] {
            walk.read_metadata(assets, metadata)?;
        }
        walk.check()?;
        Ok(super::review_active::AdmittedPublication {
            before: state.clone(), origin: before.origin().clone(), snapshot: expected,
            graph: json!({"schema_version":1,"kind":"review_bound_retained_render_graph_v3", "publication":descriptor(object)}).to_string(),
            record,
        })
    })
}

fn exact_value(left: &Value, right: &Value) -> SupervisorResult<bool> {
    let mut l = String::new();
    let mut r = String::new();
    super::review_document::canonical(left, &mut l)?;
    super::review_document::canonical(right, &mut r)?;
    Ok(l == r)
}

pub(super) struct EffectivePublicationInputs<'a> {
    pub(super) store: &'a DurableStore,
    pub(super) data: &'a std::path::Path,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) current: &'a super::review_effective::AdmittedEffectiveState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) timeline: &'a AcceptedJsonDocument,
    pub(super) rendered: &'a Value,
    pub(super) mixed: Option<&'a Value>,
    pub(super) mix_packet: Option<&'a AcceptedJsonDocument>,
    pub(super) processing_mix: Option<&'a Value>,
    pub(super) speech_inventory: Option<&'a AcceptedJsonDocument>,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
}

/// Process-local, single-use admission from prepare. Private fields and no
/// deserializer prevent client JSON from authorizing the current-only commit.
pub(super) struct PreparedEffectivePublication {
    before: ReviewGraphState,
    object: AcceptedJsonDocument,
    proposal: AcceptedJsonDocument,
    admitted: super::review_active::AdmittedPublication,
    prepared_until: Instant,
}

impl PreparedEffectivePublication {
    fn new(
        before: &ReviewGraphState,
        object: AcceptedJsonDocument,
        proposal: AcceptedJsonDocument,
        admitted: super::review_active::AdmittedPublication,
        prepared_until: Instant,
    ) -> SupervisorResult<Self> {
        v3_shape(&admitted.record)?;
        let graph = json!({"schema_version":1,"kind":"review_bound_retained_render_graph_v3",
            "publication":descriptor(&object)})
        .to_string();
        if admitted.before != *before
            || admitted.origin.original_artifact_id != before.original_artifact_id
            || admitted.origin.token.original_manifest_sha256
                != before.token.original_manifest_sha256
            || admitted.record["origin_token"] != token(&admitted.origin)
            || Some(admitted.snapshot.revision()) != before.token.revision.checked_add(1)
            || admitted.record["proposal"] != descriptor(&proposal)
            || admitted.record["prepared_snapshot"]["sha256"] != admitted.snapshot.sha256()
            || admitted.graph != graph
        {
            return Err(invalid(
                "prepared publication identity differs from its admission",
            ));
        }
        Ok(Self {
            before: before.clone(),
            object,
            proposal,
            admitted,
            prepared_until,
        })
    }

    pub(super) fn revalidate(
        self,
        store: &DurableStore,
        data: &std::path::Path,
        before: &ReviewGraphState,
    ) -> SupervisorResult<super::review_active::AdmittedPublication> {
        // Always use a new complete physical/history/runtime walk. The held
        // admission replaces only commit's redundant INITIAL reconstruction.
        self.revalidate_with(store, data, before, |_, object, walk| {
            super::review_active::read_publication_with_walk(
                store,
                data,
                before,
                &descriptor(object),
                walk,
            )
        })
    }

    fn revalidate_with(
        self,
        store: &DurableStore,
        data: &std::path::Path,
        before: &ReviewGraphState,
        read_final: impl FnOnce(
            &AcceptedAudioStore,
            &AcceptedJsonDocument,
            &mut RecoveryWalk,
        ) -> SupervisorResult<super::review_active::AdmittedPublication>,
    ) -> SupervisorResult<super::review_active::AdmittedPublication> {
        if self.before != *before || Instant::now() >= self.prepared_until {
            return Err(invalid("prepared publication is stale or expired"));
        }
        require_current(store, before)?;
        let assets = AcceptedAudioStore::open_existing(data, &before.token.job_id)?;
        if decode(&assets.read_json(&self.object)?, LIMIT)? != self.admitted.record {
            return Err(invalid(
                "prepared publication bytes differ from its admission",
            ));
        }
        assets.read_json(&self.proposal)?;
        // Preserve the existing final admission budget and independent walk.
        let mut walk = RecoveryWalk::new(Instant::now() + std::time::Duration::from_secs(720));
        let final_fence = read_final(&assets, &self.object, &mut walk)?;
        walk.check()?;
        if final_fence.before != self.admitted.before
            || final_fence.origin != self.admitted.origin
            || final_fence.snapshot.json() != self.admitted.snapshot.json()
            || final_fence.graph != self.admitted.graph
            || final_fence.record != self.admitted.record
        {
            return Err(invalid("publication changed during the final native fence"));
        }
        assets.read_json(&self.object)?;
        assets.read_json(&self.proposal)?;
        require_current(store, before)?;
        walk.check()?;
        Ok(final_fence)
    }
}

/// Prepare a new explicit v3 object, then exercise the complete read path before
/// it can reach the existing CAS. Any refusal leaves the old head/undo intact.
pub(super) fn prepare_effective(
    input: EffectivePublicationInputs<'_>,
) -> SupervisorResult<PreparedEffectivePublication> {
    let deadline = Instant::now() + std::time::Duration::from_secs(720);
    let state = input.current.state();
    require_current(input.store, state)?;
    if input.rendered["retained_final_output"] != true
        || input.rendered["retained_receipt_reopened"] != true
        || input.rendered["active_graph_published"] != false
        || input.rendered["correction_ready"] != false
    {
        return Err(invalid(
            "cumulative publication requires a complete private retained render",
        ));
    }
    let before = seal_current(input.store, input.assets, input.current, deadline)?;
    let proposal = input.assets.accept_json(input.after.json())?;
    let mix_fields = [
        input.mixed.is_some(),
        input.mix_packet.is_some(),
        input.processing_mix.is_some(),
        input.speech_inventory.is_some(),
    ];
    if mix_fields.iter().any(|v| *v != mix_fields[0]) {
        return Err(invalid(
            "cumulative B2 publication requires the complete mix/speech proof set",
        ));
    }
    let processing_mix = input
        .processing_mix
        .map(|r| input.assets.accept_json(&r.to_string()))
        .transpose()?;
    let record = json!({"schema_version":3,"kind":"review_native_publication_v3",
        "before_state":descriptor(&before),"origin_token":token(input.current.origin()),
        "proposal":descriptor(&proposal),"corrected_timeline":descriptor(input.timeline),
        "context":input.rendered["context"],"retained_render":input.rendered["retained_render"],
        "processing_render":input.rendered["processing_render"],"prepared_snapshot":input.rendered["prepared_snapshot"],
        "execution_identity":Value::Null,"output_volume_id":input.output_pin.volume_id(),
        "mixed":input.mixed,"mix_packet":input.mix_packet.map(descriptor),
        "processing_mix":processing_mix.as_ref().map(descriptor),"speech_inventory":input.speech_inventory.map(descriptor)});
    let mut record = record;
    let render_recipe = accepted_json(&record["processing_render"], input.assets)?;
    record["execution_identity"] =
        decode(&input.assets.read_json(&render_recipe)?, LIMIT)?["execution_identity"].clone();
    v3_shape(&record)?;
    let object = input.assets.accept_json(&record.to_string())?;
    let admitted = read_v3(
        input.store,
        input.data,
        state,
        input.assets,
        &object,
        &mut RecoveryWalk::new(deadline),
    )?;
    if admitted.before != *state || admitted.origin != *input.current.origin() {
        return Err(invalid(
            "cumulative publication changed its native before/origin identity",
        ));
    }
    require_current(input.store, state)?;
    PreparedEffectivePublication::new(state, object, proposal, admitted, deadline)
}

#[cfg(test)]
mod prepared_tests {
    use super::super::review_active::AdmittedPublication;
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    // These are orchestration fixtures, not encoded v3 render admissions. The
    // private oracle below uses the real fresh runtime and media hash readers;
    // production revalidate() always calls the complete recursive v3 reader.
    fn candidate(
        assets: &AcceptedAudioStore,
        before: &ReviewGraphState,
        origin: &ReviewGraphState,
    ) -> PreparedEffectivePublication {
        let prior = AdmittedSnapshot::read(
            &before.snapshot_json,
            &before.token.job_id,
            before.token.revision,
        )
        .unwrap();
        let after = prior
            .correction(
                "finding-a",
                Some(&format!("Edit {}", before.token.revision)),
                None,
                super::super::review_document::ReviewMode::Subtitles,
            )
            .unwrap();
        let proposal = assets.accept_json(after.json()).unwrap();
        let empty = assets.accept_json("{}").unwrap();
        let record = json!({"schema_version":3,"kind":"review_native_publication_v3",
            "before_state":descriptor(&empty),"origin_token":token(origin),"proposal":descriptor(&proposal),
            "corrected_timeline":descriptor(&empty),"context":descriptor(&empty),
            "retained_render":descriptor(&empty),"processing_render":descriptor(&empty),
            "prepared_snapshot":descriptor(&proposal),"execution_identity":null,"output_volume_id":"unit-only",
            "mixed":null,"mix_packet":null,"processing_mix":null,"speech_inventory":null});
        let object = assets.accept_json(&record.to_string()).unwrap();
        let admitted = AdmittedPublication {
            before: before.clone(),
            origin: origin.clone(),
            snapshot: after,
            graph: json!({"schema_version":1,"kind":"review_bound_retained_render_graph_v3",
                "publication":descriptor(&object)})
            .to_string(),
            record,
        };
        PreparedEffectivePublication::new(
            before,
            object,
            proposal,
            admitted,
            Instant::now() + Duration::from_secs(60),
        )
        .unwrap()
    }

    fn reopened(
        assets: &AcceptedAudioStore,
        object: &AcceptedJsonDocument,
        before: &ReviewGraphState,
        origin: &ReviewGraphState,
    ) -> SupervisorResult<AdmittedPublication> {
        let record = decode(&assets.read_json(object)?, LIMIT)?;
        let prepared = accepted_json(&record["prepared_snapshot"], assets)?;
        Ok(AdmittedPublication {
            before: before.clone(),
            origin: origin.clone(),
            snapshot: AdmittedSnapshot::read(
                &assets.read_json(&prepared)?,
                &before.token.job_id,
                before.token.revision + 1,
            )?,
            graph: json!({"schema_version":1,"kind":"review_bound_retained_render_graph_v3",
                "publication":descriptor(object)})
            .to_string(),
            record,
        })
    }

    fn runtime(store: &DurableStore, data: &Path) -> PathBuf {
        let root = data.join("test-owned-runtime");
        let files: [(&str, &[u8]); 10] = [
            ("runtime/python.exe", b"owned python"),
            ("runtime/media/ffmpeg.exe", b"owned ffmpeg"),
            ("runtime/media/ffprobe.exe", b"owned ffprobe"),
            ("runtime/Lib/late.py", b"module-a"),
            ("runtime/Lib/empty.py", b""),
            (
                "app/engine/dubflow/worker/production_job.py",
                b"owned worker",
            ),
            ("app/engine/dubflow/review/cue_plan.py", b"owned cue"),
            (
                "app/models/manifests/production-cpu-v1.json",
                br#"{"tts_neural_profile":"models/manifests/voice.json"}"#,
            ),
            ("app/models/manifests/voice.json", br#"{"voices":[]}"#),
            ("app/engine/helper.py", b"owned helper"),
        ];
        let mut pins = serde_json::Map::new();
        let mut inventory = Vec::new();
        for (name, raw) in files {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, raw).unwrap();
            let sha = format!("sha256:{:x}", Sha256::digest(raw));
            pins.insert(name.into(), json!({"sha256":sha,"size_bytes":raw.len()}));
            inventory
                .push(json!({"path":name,"sha256":&sha[7..],"size_bytes":raw.len().to_string()}));
        }
        let release = json!({"schema_version":1,"artifacts":inventory}).to_string();
        fs::write(root.join("release-manifest.json"), &release).unwrap();
        let producer = json!({"runtime_root":root,"model_root":root.join("models-not-provisioned"),
            "worker":pins["app/engine/dubflow/worker/production_job.py"],"python":pins["runtime/python.exe"],
            "ffmpeg":pins["runtime/media/ffmpeg.exe"],"ffprobe":pins["runtime/media/ffprobe.exe"],
            "release_manifest":{"sha256":hash(&release),"size_bytes":release.len()},
            "model_manifests":[["production-cpu-v1.json",pins["app/models/manifests/production-cpu-v1.json"]],
                ["voice.json",pins["app/models/manifests/voice.json"]]]});
        store
            .create_job_with_start_request(
                "runtime-fixture",
                "file:///fixture",
                &json!({"schema_version":1,"producer":producer}).to_string(),
                1,
            )
            .unwrap();
        super::super::review_runtime::AdmittedRuntime::read(store, "runtime-fixture", &root)
            .unwrap();
        root
    }

    fn media(data: &Path, generation: u64) -> (PathBuf, Vec<u8>) {
        // Small valid mono 16-bit PCM WAV; never executes an external tool.
        let mut raw = b"RIFF".to_vec();
        raw.extend_from_slice(&40u32.to_le_bytes());
        raw.extend_from_slice(b"WAVEfmt ");
        raw.extend_from_slice(&16u32.to_le_bytes());
        raw.extend_from_slice(&1u16.to_le_bytes());
        raw.extend_from_slice(&1u16.to_le_bytes());
        raw.extend_from_slice(&16000u32.to_le_bytes());
        raw.extend_from_slice(&32000u32.to_le_bytes());
        raw.extend_from_slice(&2u16.to_le_bytes());
        raw.extend_from_slice(&16u16.to_le_bytes());
        raw.extend_from_slice(b"data");
        raw.extend_from_slice(&4u32.to_le_bytes());
        raw.extend_from_slice(&(generation as u32).to_le_bytes());
        let path = data.join(format!("retained-generation-{generation}.wav"));
        fs::write(&path, &raw).unwrap();
        (path, raw)
    }

    fn verify_media(path: &Path, raw: &[u8], walk: &RecoveryWalk) -> SupervisorResult<()> {
        let sha = format!("sha256:{:x}", Sha256::digest(raw));
        super::super::review_render::verified(
            path,
            &sha,
            raw.len() as u64,
            false,
            walk.deadline(),
        )?;
        Ok(())
    }

    #[test]
    fn prepared_one_two_three_node_orchestration_reopens_once_and_keeps_prior_rows_and_pcm() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let root = runtime(store, data);
            let mut state = original.clone();
            let mut rows = vec![original.clone()];
            let mut audio = vec![media(data, 0)];
            for revision in 1..=3 {
                let prepared = candidate(&assets, &state, original);
                let mut calls = 0;
                let final_fence = prepared
                    .revalidate_with(store, data, &state, |a, o, walk| {
                        calls += 1;
                        super::super::review_runtime::AdmittedRuntime::read(
                            store,
                            "runtime-fixture",
                            &root,
                        )?;
                        for (path, raw) in &audio {
                            verify_media(path, raw, walk)?;
                        }
                        reopened(a, o, &state, original)
                    })
                    .unwrap();
                assert_eq!(
                    calls, 1,
                    "one final oracle, no duplicate initial reconstruction"
                );
                state = store
                    .commit_review_graph(
                        &state.token,
                        final_fence.snapshot.json(),
                        &final_fence.graph,
                        10 + revision,
                    )
                    .unwrap();
                assert_eq!(state.token.revision, revision);
                assert_eq!(state.undo_depth as u64, revision);
                for row in &rows {
                    assert_eq!(
                        store.review_graph_revision(&row.token).unwrap().as_ref(),
                        Some(row)
                    );
                }
                for (path, raw) in &audio {
                    assert_eq!(fs::read(path).unwrap(), *raw);
                }
                rows.push(state.clone());
                audio.push(media(data, revision));
            }
        });
    }

    #[test]
    fn prepared_final_fence_refuses_late_same_size_restored_mtime_extra_missing_link_and_media() {
        for fault in 0..5 {
            with_admitted_original(|store, data, original| {
                let assets =
                    AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
                let root = runtime(store, data);
                let (path, raw) = media(data, 0);
                let prepared = candidate(&assets, original, original);
                let import = root.join("runtime/Lib/late.py");
                let mut held_directory = None;
                match fault {
                    0 => {
                        let meta = fs::metadata(&import).unwrap();
                        fs::write(&import, b"module-b").unwrap();
                        fs::File::options()
                            .write(true)
                            .open(&import)
                            .unwrap()
                            .set_times(fs::FileTimes::new().set_modified(meta.modified().unwrap()))
                            .unwrap();
                        assert_eq!(
                            fs::metadata(&import).unwrap().modified().unwrap(),
                            meta.modified().unwrap()
                        );
                        assert_eq!(fs::metadata(&import).unwrap().len(), meta.len());
                    }
                    1 => fs::write(root.join("runtime/Lib/unlisted.py"), b"extra").unwrap(),
                    2 => fs::remove_file(&import).unwrap(),
                    3 => {
                        // cmd treats an unquoted forward slash as an option.
                        let dir = root.join("runtime").join("Lib");
                        let held = data.join("held-test-lib");
                        fs::rename(&dir, &held).unwrap();
                        #[cfg(windows)]
                        {
                            use std::os::windows::process::CommandExt;
                            let output = std::process::Command::new("cmd.exe")
                                .args(["/d", "/c", "mklink", "/J"])
                                .arg(&dir)
                                .arg(&held)
                                .creation_flags(0x08000000)
                                .output()
                                .unwrap();
                            assert!(
                                output.status.success(),
                                "fixture junction creation failed: status={:?}, stdout={}, stderr={}",
                                output.status.code(),
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                        }
                        #[cfg(unix)]
                        std::os::unix::fs::symlink(&held, &dir).unwrap();
                        held_directory = Some((dir, held));
                    }
                    _ => {
                        let mut changed = raw.clone();
                        *changed.last_mut().unwrap() ^= 1;
                        fs::write(&path, changed).unwrap();
                    }
                }
                let result = prepared.revalidate_with(store, data, original, |a, o, walk| {
                    super::super::review_runtime::AdmittedRuntime::read(
                        store,
                        "runtime-fixture",
                        &root,
                    )?;
                    verify_media(&path, &raw, walk)?;
                    reopened(a, o, original, original)
                });
                if let Some((dir, held)) = held_directory {
                    #[cfg(windows)]
                    fs::remove_dir(&dir).unwrap();
                    #[cfg(unix)]
                    fs::remove_file(&dir).unwrap();
                    fs::rename(held, dir).unwrap();
                }
                assert!(
                    result.is_err(),
                    "late fault {fault} must reach a fresh reader"
                );
                assert_eq!(
                    store
                        .review_graph_state(&original.token.job_id)
                        .unwrap()
                        .as_ref(),
                    Some(original)
                );
                assert!(store.review_undo_target(&original.token).unwrap().is_none());
                if fault != 4 {
                    assert_eq!(fs::read(path).unwrap(), raw);
                }
            });
        }
    }

    #[test]
    fn prepared_binding_rejects_forged_identity_expiry_changed_seal_and_stale_token() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            for field in 0..7 {
                let mut prepared = candidate(&assets, original, original);
                match field {
                    0 => prepared.admitted.before.token.graph_sha256 = hash("wrong token"),
                    1 => prepared.admitted.origin.original_artifact_id = "foreign-original".into(),
                    2 => {
                        prepared.admitted.origin.token.original_manifest_sha256 =
                            hash("foreign manifest")
                    }
                    3 => prepared.admitted.record["proposal"] = json!(null),
                    4 => prepared.admitted.graph.push(' '),
                    5 => {
                        prepared.admitted.record["prepared_snapshot"]["sha256"] =
                            hash("foreign snapshot").into()
                    }
                    _ => {
                        prepared.admitted.snapshot = AdmittedSnapshot::read(
                            &original.snapshot_json,
                            &original.token.job_id,
                            original.token.revision,
                        )
                        .unwrap()
                    }
                }
                assert!(
                    PreparedEffectivePublication::new(
                        original,
                        prepared.object,
                        prepared.proposal,
                        prepared.admitted,
                        prepared.prepared_until
                    )
                    .is_err(),
                    "forged identity {field}"
                );
            }
            let mut expired = candidate(&assets, original, original);
            expired.prepared_until = Instant::now() - Duration::from_secs(1);
            assert!(expired
                .revalidate_with(store, data, original, |_, _, _| panic!(
                    "expired entered final reader"
                ))
                .is_err());
            let mut changed_seal = candidate(&assets, original, original);
            changed_seal.object = assets.accept_json("{}").unwrap();
            assert!(changed_seal
                .revalidate_with(store, data, original, |_, _, _| panic!(
                    "changed seal entered final reader"
                ))
                .is_err());
            let stale = candidate(&assets, original, original);
            let next = candidate(&assets, original, original);
            let current = store
                .commit_review_graph(
                    &original.token,
                    next.admitted.snapshot.json(),
                    &next.admitted.graph,
                    20,
                )
                .unwrap();
            assert!(stale
                .revalidate_with(store, data, original, |_, _, _| panic!(
                    "stale token entered final reader"
                ))
                .is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(current)
            );
        });
    }

    #[test]
    fn prepared_final_comparison_cancellation_and_cas_race_leave_old_rows_and_audio() {
        for fault in 0..9 {
            with_admitted_original(|store, data, original| {
                let assets =
                    AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
                let (path, raw) = media(data, 0);
                let prepared = candidate(&assets, original, original);
                let result = prepared.revalidate_with(store, data, original, |a, o, walk| {
                    verify_media(&path, &raw, walk)?;
                    if fault == 0 {
                        return Err(invalid("simulated cancellation during final admission"));
                    }
                    let mut final_fence = reopened(a, o, original, original)?;
                    match fault {
                        1 => final_fence.before.token.graph_sha256 = hash("changed before"),
                        2 => final_fence.origin.token.graph_sha256 = hash("changed origin"),
                        3 => final_fence.graph.push(' '),
                        4 => final_fence.record["output_volume_id"] = "changed volume".into(),
                        5 => {
                            final_fence.snapshot = AdmittedSnapshot::read(
                                &original.snapshot_json,
                                &original.token.job_id,
                                original.token.revision,
                            )
                            .unwrap()
                        }
                        6 => {
                            store.commit_review_graph(
                                &original.token,
                                final_fence.snapshot.json(),
                                &final_fence.graph,
                                30,
                            )?;
                        }
                        7 => {
                            let mut permissions = fs::metadata(&o.path)?.permissions();
                            permissions.set_readonly(false);
                            fs::set_permissions(&o.path, permissions)?;
                            fs::remove_file(&o.path)?;
                        }
                        _ => walk.deadline = Instant::now() - Duration::from_secs(1),
                    }
                    Ok(final_fence)
                });
                assert!(result.is_err(), "final fault {fault}");
                assert_eq!(
                    store
                        .review_graph_revision(&original.token)
                        .unwrap()
                        .as_ref(),
                    Some(original)
                );
                assert_eq!(fs::read(path).unwrap(), raw);
                let current = store
                    .review_graph_state(&original.token.job_id)
                    .unwrap()
                    .unwrap();
                assert_eq!(current.token.revision, if fault == 6 { 1 } else { 0 });
                if fault == 6 {
                    let next = candidate(&assets, &current, original);
                    assert!(
                        store
                            .commit_review_graph(
                                &original.token,
                                next.admitted.snapshot.json(),
                                &next.admitted.graph,
                                31
                            )
                            .is_err(),
                        "SQLite CAS still rejects a stale token"
                    );
                }
            });
        }
    }

    #[test]
    fn prepared_head_race_after_final_fence_still_requires_sqlite_cas() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let (path, raw) = media(data, 0);
            let ours = candidate(&assets, original, original)
                .revalidate_with(store, data, original, |a, o, walk| {
                    verify_media(&path, &raw, walk)?;
                    reopened(a, o, original, original)
                })
                .unwrap();
            let competing = candidate(&assets, original, original);
            let winner = store
                .commit_review_graph(
                    &original.token,
                    competing.admitted.snapshot.json(),
                    &competing.admitted.graph,
                    40,
                )
                .unwrap();
            assert!(store
                .commit_review_graph(&original.token, ours.snapshot.json(), &ours.graph, 41)
                .is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(winner)
            );
            assert_eq!(
                store
                    .review_graph_revision(&original.token)
                    .unwrap()
                    .as_ref(),
                Some(original)
            );
            assert_eq!(fs::read(path).unwrap(), raw);
        });
    }

    #[test]
    fn prepared_production_commit_cannot_turn_metadata_only_into_physical_v3_admission() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let prepared = candidate(&assets, original, original);
            assert!(super::super::review_active::commit_prepared_effective(
                store, data, original, prepared
            )
            .is_err());
            assert_eq!(
                store
                    .review_graph_state(&original.token.job_id)
                    .unwrap()
                    .as_ref(),
                Some(original)
            );
            assert!(store.review_undo_target(&original.token).unwrap().is_none());
        });
    }
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;
    use std::time::Duration;

    fn restore_original_at_two(store: &DurableStore, state: &ReviewGraphState) -> ReviewGraphState {
        let before = AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
        let changed = before
            .correction(
                "finding-a",
                Some("Chào bạn."),
                None,
                super::super::review_document::ReviewMode::Subtitles,
            )
            .unwrap();
        // Test-only low-level row; no encoded render or native publication is
        // claimed. The real monotonic state transition restores the original.
        let one = store
            .commit_review_graph(
                &state.token,
                changed.json(),
                r#"{"kind":"test-only-unknown"}"#,
                10,
            )
            .unwrap();
        let restored = before.undo_proposal(&changed).unwrap();
        store
            .undo_review_graph(&one.token, restored.json(), 11)
            .unwrap()
    }

    #[test]
    fn recorded_reference_recovery_preserves_after_undo_token_and_refuses_forged_effective_view() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let zero = super::super::review_effective::read_current(store, data, original).unwrap();
            let zero_reference = seal_current(
                store,
                &assets,
                &zero,
                Instant::now() + Duration::from_secs(10),
            )
            .unwrap();
            let restored = restore_original_at_two(store, original);
            let admitted =
                super::super::review_effective::read_current(store, data, &restored).unwrap();
            let reference = seal_current(
                store,
                &assets,
                &admitted,
                Instant::now() + Duration::from_secs(10),
            )
            .unwrap();
            for (object, expected) in [(&zero_reference, original), (&reference, &restored)] {
                let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(10));
                let recorded =
                    read_reference(store, &assets, &descriptor(object), original, &mut walk)
                        .unwrap();
                assert_eq!(recorded.state(), expected);
                let physical = super::super::review_effective::read_historical(
                    store, data, &recorded, &mut walk,
                )
                .unwrap();
                assert_eq!(physical.state(), expected);
                assert_eq!(physical.origin().token.revision, 0);
                assert_eq!(
                    store.review_graph_state(&original.token.job_id).unwrap(),
                    Some(restored.clone())
                );
                assert!(store.review_undo_target(&restored.token).unwrap().is_none());
            }
            assert_eq!(admitted.state().token.revision, 2);
            assert!(super::super::review_seed::read_original(store, data, &restored).is_err());
            // Valid accepted bytes and a real recorded token do not prove that
            // an asserted effective view describes the physical graph.
            let mut forged_view = admitted.packet().unwrap();
            forged_view["current_timeline_json"] = r#"{"changed":"unadmitted timeline"}"#.into();
            let view_object = assets.accept_json(&forged_view.to_string()).unwrap();
            let mut record = decode(&assets.read_json(&reference).unwrap(), LIMIT).unwrap();
            record["effective_state"] = descriptor(&view_object);
            let forged = assets.accept_json(&record.to_string()).unwrap();
            let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(10));
            let reference =
                read_reference(store, &assets, &descriptor(&forged), original, &mut walk).unwrap();
            assert!(super::super::review_effective::read_historical(
                store, data, &reference, &mut walk
            )
            .is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(restored)
            );
            assert_eq!(
                store
                    .review_graph_revision(&original.token)
                    .unwrap()
                    .as_ref(),
                Some(original)
            );
        });
    }

    #[test]
    fn recorded_reference_cannot_hide_changed_original_artifact_bytes() {
        with_admitted_original(|store, data, original| {
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let native =
                super::super::review_effective::read_current(store, data, original).unwrap();
            let object = seal_current(
                store,
                &assets,
                &native,
                Instant::now() + Duration::from_secs(10),
            )
            .unwrap();
            let path = data.join("output/editable_timeline.json");
            let before = std::fs::read(&path).unwrap();
            let mut changed = before.clone();
            let index = changed.iter().position(|b| *b == b'{').unwrap();
            changed[index] = b'[';
            assert_eq!(changed.len(), before.len());
            std::fs::write(&path, changed).unwrap();
            let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(10));
            let reference =
                read_reference(store, &assets, &descriptor(&object), original, &mut walk).unwrap();
            assert!(super::super::review_effective::read_historical(
                store, data, &reference, &mut walk
            )
            .is_err());
            assert_eq!(
                store
                    .review_graph_state(&original.token.job_id)
                    .unwrap()
                    .as_ref(),
                Some(original)
            );
            assert!(store.review_undo_target(&original.token).unwrap().is_none());
        });
    }

    #[test]
    fn cumulative_publication_without_processing_and_physical_render_never_changes_after_undo_head()
    {
        with_admitted_original(|store, data, original| {
            let restored = restore_original_at_two(store, original);
            let native =
                super::super::review_effective::read_current(store, data, &restored).unwrap();
            let assets = AcceptedAudioStore::open_existing(data, &original.token.job_id).unwrap();
            let before = seal_current(
                store,
                &assets,
                &native,
                Instant::now() + Duration::from_secs(10),
            )
            .unwrap();
            let after = native
                .snapshot()
                .correction(
                    "finding-a",
                    Some("Một lần sửa sau undo."),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let proposal = assets.accept_json(after.json()).unwrap();
            let empty = assets.accept_json("{}").unwrap();
            let record = json!({"schema_version":3,"kind":"review_native_publication_v3", "before_state":descriptor(&before),
                "origin_token":token(native.origin()),"proposal":descriptor(&proposal),"corrected_timeline":descriptor(&empty),
                "context":descriptor(&empty),"retained_render":descriptor(&empty),"processing_render":descriptor(&empty),
                "prepared_snapshot":descriptor(&proposal),"execution_identity":hash("missing render"),"output_volume_id":"fixture-only",
                "mixed":null,"mix_packet":null,"processing_mix":null,"speech_inventory":null});
            let proof = assets.accept_json(&record.to_string()).unwrap();
            let private = json!({"retained_final_output":true,"retained_receipt_reopened":true,"active_graph_published":false,
                "native_publication":descriptor(&proof)});
            // Real commit performs the recursive v3 admission before CAS. A
            // claimed ready receipt plus valid metadata cannot manufacture a
            // processing release or any of the five physical render roles.
            assert!(super::super::review_active::commit(store, data, &restored, &private).is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(restored.clone())
            );
            assert!(store.review_undo_target(&restored.token).unwrap().is_none());
            assert_eq!(
                store.review_graph_revision(&restored.token).unwrap(),
                Some(restored)
            );
            assert!(store
                .review_graph_revision(&ReviewToken {
                    revision: 3,
                    snapshot_sha256: after.sha256(),
                    graph_sha256: hash("not committed"),
                    ..original.token.clone()
                })
                .is_err());
        });
    }

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
