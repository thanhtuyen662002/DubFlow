//! Opt-in native Review graph publication/recovery/undo. Standard exports and
//! the desktop activation gate stay independent of these private operations.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument, MixStorageBudget};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{closed, decode, tick, AdmittedSnapshot, ReviewMode};
use super::review_retention::{NativeMixStore, NativeOutputPin};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{
    review_graph::{ReviewGraphState, ReviewToken},
    DurableStore,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

/// Only called from the completed native render path after physical recovery.
/// Separate documents avoid copying large prior graph bytes into each row.
pub(super) fn prepare(
    assets: &AcceptedAudioStore,
    before: &ReviewGraphState,
    proposal: &AdmittedSnapshot,
    timeline: &AcceptedJsonDocument,
    context: &AcceptedJsonDocument,
    render: &AcceptedJsonDocument,
    prepared: &AcceptedJsonDocument,
    identity: &str,
    volume: &str,
    mixed: Option<&Value>,
    accepted_cue: Option<&Value>,
    output_pin: Option<&NativeOutputPin>,
) -> SupervisorResult<AcceptedJsonDocument> {
    if before.token.revision != 0 || before.undo_depth != 0 || before.undo_target_revision.is_some()
    {
        return Err(invalid(
            "this render recipe cannot publish a cumulative generation",
        ));
    }
    let snapshot = assets.accept_json(&before.snapshot_json)?;
    let graph = assets.accept_json(&before.graph_json)?;
    let proposal = assets.accept_json(proposal.json())?;
    let speech = if let Some(cue) = accepted_cue {
        Some(super::review_speech::seal(
            assets,
            before,
            &AdmittedSnapshot::read(&assets.read_json(&proposal)?, &before.token.job_id, 1)?,
            mixed.ok_or_else(|| invalid("speech publication requires its retained B2 mix"))?,
            cue,
            Instant::now() + Duration::from_secs(720),
            output_pin
                .filter(|pin| pin.volume_id() == volume)
                .ok_or_else(|| {
                    invalid("speech publication requires its admitted native output volume")
                })?,
        )?)
    } else {
        None
    };
    let mut record = json!({"schema_version":if speech.is_some() {2} else {1},
        "kind":if speech.is_some() {"review_native_publication_v2"} else {"review_native_publication"},
        "before":{"job_id":before.token.job_id,"revision":"0",
            "original_artifact_id":before.original_artifact_id,
            "original_manifest_sha256":before.token.original_manifest_sha256,
            "snapshot":descriptor(&snapshot),"graph":descriptor(&graph)},
        "proposal":descriptor(&proposal),"corrected_timeline":descriptor(timeline),
        "context":descriptor(context),"retained_render":descriptor(render),
        "prepared_snapshot":descriptor(prepared),"execution_identity":identity,
        "output_volume_id":volume,"mixed":mixed});
    if let Some(speech) = speech {
        record
            .as_object_mut()
            .unwrap()
            .insert("speech_inventory".into(), descriptor(&speech));
    }
    assets.accept_json(&record.to_string())
}

fn before_state(
    record: &Value,
    assets: &AcceptedAudioStore,
    job: &str,
) -> SupervisorResult<ReviewGraphState> {
    let before = &record["before"];
    closed(
        before,
        &[
            "job_id",
            "revision",
            "original_artifact_id",
            "original_manifest_sha256",
            "snapshot",
            "graph",
        ],
    )?;
    if before["job_id"] != job || before["revision"] != "0" {
        return Err(invalid(
            "publication substitutes its original native generation",
        ));
    }
    let snapshot = accepted_json(&before["snapshot"], assets)?;
    let graph = accepted_json(&before["graph"], assets)?;
    Ok(ReviewGraphState {
        token: ReviewToken {
            job_id: job.into(),
            revision: 0,
            snapshot_sha256: snapshot.sha256.clone(),
            graph_sha256: graph.sha256.clone(),
            original_manifest_sha256: super::review_document::text(
                &before["original_manifest_sha256"],
                128,
            )?
            .into(),
        },
        original_artifact_id: super::review_document::identity(&before["original_artifact_id"])?
            .into(),
        snapshot_json: assets.read_json(&snapshot)?,
        graph_json: assets.read_json(&graph)?,
        undo_target_revision: None,
        undo_depth: 0,
    })
}

/// Bind retained mix metadata and all three PCM roles again on recovery. No
/// mutable candidate path/hint is used, and no model/worker is executed here.
fn verify_mix(
    record: &Value,
    context: &Value,
    assets: &AcceptedAudioStore,
    original: &ReviewGraphState,
    seed_graph: &str,
    pin: &NativeOutputPin,
    proposal: &AdmittedSnapshot,
    deadline: Instant,
) -> SupervisorResult<()> {
    let mixed = &record["mixed"];
    if context["mode"] == "b1" {
        if !mixed.is_null() || !context["retained_mix"].is_null() {
            return Err(invalid("B1 recovery cannot invent a mix"));
        }
        return Ok(());
    }
    closed(
        mixed,
        &[
            "context",
            "composition",
            "candidate_mix_document",
            "mix_document",
            "private_audio_verified",
            "retained_audio",
            "active_graph_published",
            "mix_metadata_retargeted",
            "retained_mix_audio",
        ],
    )?;
    if mixed["private_audio_verified"] != true
        || mixed["retained_audio"] != true
        || mixed["mix_metadata_retargeted"] != true
        || mixed["active_graph_published"] != false
    {
        return Err(invalid("B2 graph requires native retained mix admission"));
    }
    let mix_context = accepted_json(&mixed["context"], assets)?;
    let composition = accepted_json(&mixed["composition"], assets)?;
    let candidate = accepted_json(&mixed["candidate_mix_document"], assets)?;
    let retained = accepted_json(&mixed["mix_document"], assets)?;
    let mix_context_raw = assets.read_json(&mix_context)?;
    let mix_context_value = decode(&mix_context_raw, LIMIT)?;
    if mix_context_value["before_snapshot_json"] != original.snapshot_json
        || mix_context_value["after_snapshot_json"] != proposal.json()
        || mix_context_value["source_graph_json"] != seed_graph
        || mix_context_value["manifest_sha256"] != original.token.original_manifest_sha256
    {
        return Err(invalid(
            "retained mix substitutes native source or logical proposal",
        ));
    }
    let composition_raw = assets.read_json(&composition)?;
    let candidate_raw = assets.read_json(&candidate)?;
    let retained_raw = assets.read_json(&retained)?;
    let graph = decode(&original.graph_json, LIMIT)?;
    let template = accepted_json(&graph["accepted_documents"]["mix_document"], assets)?;
    super::review_correction::admit_mix_metadata(
        &json!({"schema_version":1,
        "kind":"review_private_mix_candidate","context_sha256":mix_context.sha256,
        "composition_json":composition_raw,"composition_sha256":composition.sha256,
        "mix_document_json":candidate_raw,"mix_document_sha256":candidate.sha256,
        "active_graph_published":false})
        .to_string(),
        &mix_context_value,
        &mix_context.sha256,
        &decode(&assets.read_json(&template)?, LIMIT)?,
    )?;
    let media = NativeMixStore::open_existing(
        pin,
        &original.token.job_id,
        MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    let entries = mixed["retained_mix_audio"]
        .as_array()
        .filter(|v| v.len() == 3)
        .ok_or_else(|| invalid("retained mix requires three ordered PCM roles"))?;
    let mut located = Vec::new();
    for (entry, role) in entries
        .iter()
        .zip(["original_audio", "dialogue_stem", "final_mix"])
    {
        closed(entry, &["role", "audio"])?;
        if entry["role"] != role {
            return Err(invalid("retained PCM role substituted"));
        }
        located.push((role, media.reopen_audio(&entry["audio"], deadline)?));
    }
    if media.retarget_admitted_document(&candidate_raw, &composition.sha256, &located, deadline)?
        != retained_raw
    {
        return Err(invalid(
            "retained PCM metadata is not the exact native path-only retarget",
        ));
    }
    let render_mix = &context["retained_mix"];
    closed(
        render_mix,
        &[
            "mix_document_json",
            "mix_document_sha256",
            "composition_json",
            "composition_sha256",
            "artifact_root",
        ],
    )?;
    if render_mix["mix_document_json"] != retained_raw
        || render_mix["mix_document_sha256"] != retained.sha256
        || render_mix["composition_json"] != composition_raw
        || render_mix["composition_sha256"] != composition.sha256
        || render_mix["artifact_root"] != context["original_output_root"]
    {
        return Err(invalid(
            "render graph substitutes its admitted retained mix",
        ));
    }
    Ok(())
}

pub(super) struct AdmittedPublication {
    pub(super) before: ReviewGraphState,
    pub(super) origin: ReviewGraphState,
    pub(super) snapshot: AdmittedSnapshot,
    pub(super) graph: String,
    pub(super) record: Value,
}

fn publication_version(record: &Value) -> SupervisorResult<u64> {
    let version = record["schema_version"].as_u64();
    let mut fields = vec![
        "schema_version",
        "kind",
        "before",
        "proposal",
        "corrected_timeline",
        "context",
        "retained_render",
        "prepared_snapshot",
        "execution_identity",
        "output_volume_id",
        "mixed",
    ];
    if version == Some(2) {
        fields.push("speech_inventory");
    }
    closed(record, &fields)?;
    match (version, record["kind"].as_str()) {
        (Some(1), Some("review_native_publication")) => Ok(1),
        (Some(2), Some("review_native_publication_v2")) => Ok(2),
        _ => Err(invalid("unknown native graph publication version")),
    }
}

fn read_publication(
    store: &DurableStore,
    data: &Path,
    current: &ReviewGraphState,
    publication: &Value,
) -> SupervisorResult<AdmittedPublication> {
    read_publication_before(
        store,
        data,
        current,
        publication,
        Instant::now() + Duration::from_secs(720),
    )
}

fn read_publication_before(
    store: &DurableStore,
    data: &Path,
    current: &ReviewGraphState,
    publication: &Value,
    deadline: Instant,
) -> SupervisorResult<AdmittedPublication> {
    read_publication_with_walk(
        store,
        data,
        current,
        publication,
        &mut super::review_publication::RecoveryWalk::new(deadline),
    )
}

pub(super) fn read_publication_with_walk(
    store: &DurableStore,
    data: &Path,
    current: &ReviewGraphState,
    publication: &Value,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<AdmittedPublication> {
    walk.check()?;
    let deadline = walk.deadline();
    let assets = AcceptedAudioStore::open_existing(data, &current.token.job_id)?;
    let object = accepted_json(publication, &assets)?;
    let record = decode(&walk.read_metadata(&assets, &object)?, LIMIT)?;
    if record["schema_version"].as_u64() == Some(3) {
        return super::review_publication::read_v3(store, data, current, &assets, &object, walk);
    }
    let version = publication_version(&record)?;
    let before = before_state(&record, &assets, &current.token.job_id)?;
    if before.original_artifact_id != current.original_artifact_id
        || before.token.original_manifest_sha256 != current.token.original_manifest_sha256
    {
        return Err(invalid(
            "corrected graph substitutes its durable original export",
        ));
    }
    let seed = super::review_seed::read_original(store, data, &before)?;
    let seed_graph = decode(&seed.graph, LIMIT)?;
    let mode = if seed_graph["mode"] == "b2" {
        ReviewMode::Dubbed
    } else {
        ReviewMode::Subtitles
    };
    let proposal_object = accepted_json(&record["proposal"], &assets)?;
    let proposal = AdmittedSnapshot::read(
        &assets.read_json(&proposal_object)?,
        &before.token.job_id,
        1,
    )?;
    seed.snapshot.validate_correction(&proposal, mode)?;
    let timeline = accepted_json(&record["corrected_timeline"], &assets)?;
    if assets.read_json(&timeline)? != seed.corrected_timeline(&proposal)? {
        return Err(invalid(
            "graph substitutes its exact corrected source timeline",
        ));
    }
    let context = accepted_json(&record["context"], &assets)?;
    let context_value = decode(&assets.read_json(&context)?, LIMIT)?;
    let original = decode(&before.graph_json, LIMIT)?;
    for (key, role) in [
        ("original_manifest_json", "job_manifest"),
        ("original_timeline_json", "editable_timeline"),
    ] {
        let document = accepted_json(&original["accepted_documents"][role], &assets)?;
        if context_value[key] != assets.read_json(&document)? {
            return Err(invalid(
                "graph context substitutes archived canonical metadata",
            ));
        }
    }
    let start = decode(
        &store
            .job_start_request(&current.token.job_id)?
            .ok_or_else(|| invalid("missing original start options"))?,
        LIMIT,
    )?;
    if context_value["original_manifest_sha256"] != before.token.original_manifest_sha256
        || context_value["mode"] != seed_graph["mode"]
        || context_value["original_output_root"] != json!(seed.output_root)
        || context_value["corrected_timeline_sha256"] != timeline.sha256
        || context_value["burn_in_subtitles"] != start["burn_in_subtitles"]
    {
        return Err(invalid(
            "native publication changed mode, source, render options or timeline pins",
        ));
    }
    let pin = NativeOutputPin::read(&seed.output_root)?;
    if record["output_volume_id"] != pin.volume_id() {
        return Err(invalid("native publication output volume changed"));
    }
    verify_mix(
        &record,
        &context_value,
        &assets,
        &before,
        &seed.graph,
        &pin,
        &proposal,
        deadline,
    )?;
    if version == 2 {
        if seed_graph["mode"] == "b1" {
            return Err(invalid("B1 publication cannot invent current speech"));
        }
        super::review_speech::verify(
            &assets,
            &before,
            &proposal,
            &record["mixed"],
            &record["speech_inventory"],
            deadline,
            &pin,
        )?;
    }
    let receipt = accepted_json(&record["retained_render"], &assets)?;
    let identity = super::review_document::text(&record["execution_identity"], 128)?;
    use sha2::{Digest, Sha256};
    let candidates = seed
        .output_root
        .join(".dubflow-review-candidates")
        .join(format!(
            "{:x}",
            Sha256::digest(current.token.job_id.as_bytes())
        ));
    let recovered = super::review_render::reopen_retained_result(
        &super::review_render::RecoveryInputs {
            assets: &assets,
            state: &before,
            after: &proposal,
            timeline: &timeline,
            output_pin: &pin,
            candidates: &candidates,
        },
        &receipt,
        &context,
        identity,
        deadline,
    )?;
    let expected = recovered.prepare_snapshot(&proposal)?;
    let prepared = accepted_json(&record["prepared_snapshot"], &assets)?;
    if assets.read_json(&prepared)? != expected.json() {
        return Err(invalid(
            "published snapshot differs from retained playback admission",
        ));
    }
    walk.check()?;
    Ok(AdmittedPublication {
        origin: before.clone(),
        before,
        snapshot: expected,
        graph: json!({"schema_version":1,
        "kind":if version==2 {"review_bound_retained_render_graph_v2"} else {"review_bound_retained_render_graph"},
        "publication":descriptor(&object)})
        .to_string(),
        record,
    })
}

pub(super) fn at_revision(
    snapshot: &AdmittedSnapshot,
    revision: u64,
) -> SupervisorResult<AdmittedSnapshot> {
    let mut value = decode(snapshot.json(), LIMIT)?;
    value["revision"] = revision.into();
    AdmittedSnapshot::read(&value.to_string(), snapshot.job(), revision)
}

/// Revalidate current playback media and exact snapshot, without executing a
/// producer, repairing namespaces, importing a sidecar or inventing fallback.
fn read_active(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<AdmittedSnapshot> {
    Ok(read_active_inputs(store, data, state)?.0)
}

/// Return the exact admitted source record alongside playback. The effective
/// reader consumes these same verified inputs without a second full media walk.
pub(super) fn read_active_inputs(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<(AdmittedSnapshot, Option<AdmittedPublication>)> {
    read_active_inputs_before(
        store,
        data,
        state,
        Instant::now() + Duration::from_secs(720),
    )
}

pub(super) fn read_active_inputs_before(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
    deadline: Instant,
) -> SupervisorResult<(AdmittedSnapshot, Option<AdmittedPublication>)> {
    read_active_inputs_with_walk(
        store,
        data,
        state,
        &mut super::review_publication::RecoveryWalk::new(deadline),
    )
}

pub(super) fn read_active_inputs_with_walk(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<(AdmittedSnapshot, Option<AdmittedPublication>)> {
    walk.check()?;
    let deadline = walk.deadline();
    let graph = decode(&state.graph_json, LIMIT)?;
    if graph["kind"] == "review_bound_original_graph" {
        if state.undo_depth != 0 || state.undo_target_revision.is_some() {
            return Err(invalid(
                "restored original graph has inconsistent undo ancestry",
            ));
        }
        let current = AdmittedSnapshot::read(
            &state.snapshot_json,
            &state.token.job_id,
            state.token.revision,
        )?;
        let zero = at_revision(&current, 0)?;
        let mut original = state.clone();
        original.token.revision = 0;
        original.snapshot_json = zero.json().into();
        original.token.snapshot_sha256 = zero.sha256();
        let seed = super::review_seed::read_original(store, data, &original)?;
        let restored = at_revision(&seed.snapshot, state.token.revision)?;
        if restored.json() != state.snapshot_json {
            return Err(invalid("restored original snapshot is not canonical"));
        }
        // Original exports may be on an offline volume. Explicit active/undo
        // refuses unavailable or changed media; standard export is untouched.
        let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
        let bound = decode(&state.graph_json, LIMIT)?;
        let manifest = accepted_json(&bound["accepted_documents"]["job_manifest"], &assets)?;
        let manifest = decode(&assets.read_json(&manifest)?, LIMIT)?;
        for entry in manifest["artifacts"]
            .as_object()
            .ok_or_else(|| invalid("original artifact set missing"))?
            .values()
        {
            let path = PathBuf::from(
                entry["path"]
                    .as_str()
                    .ok_or_else(|| invalid("original media path missing"))?,
            );
            let size = entry["size_bytes"]
                .as_u64()
                .ok_or_else(|| invalid("original media size missing"))?;
            super::review_render::verified(
                &path,
                entry["sha256"]
                    .as_str()
                    .ok_or_else(|| invalid("original media hash missing"))?,
                size,
                false,
                deadline,
            )?;
        }
        walk.check()?;
        return Ok((restored, None));
    }
    closed(&graph, &["schema_version", "kind", "publication"])?;
    if graph["schema_version"].as_u64() != Some(1)
        || !matches!(
            graph["kind"].as_str(),
            Some(
                "review_bound_retained_render_graph"
                    | "review_bound_retained_render_graph_v2"
                    | "review_bound_retained_render_graph_v3"
            )
        )
    {
        return Err(invalid(
            "unknown corrected graph needs explicit compatibility",
        ));
    }
    let admitted = read_publication_with_walk(store, data, state, &graph["publication"], walk)?;
    if admitted.graph != state.graph_json {
        return Err(invalid(
            "corrected graph is not its native canonical encoding",
        ));
    }
    let expected = at_revision(&admitted.snapshot, state.token.revision)?;
    if expected.json() != state.snapshot_json {
        return Err(invalid(
            "active snapshot substitutes its admitted playback or evidence",
        ));
    }
    walk.check()?;
    Ok((expected, Some(admitted)))
}

pub(super) fn commit(
    store: &DurableStore,
    data: &Path,
    before: &ReviewGraphState,
    rendered: &Value,
) -> SupervisorResult<ReviewGraphState> {
    if rendered["retained_final_output"] != true
        || rendered["retained_receipt_reopened"] != true
        || rendered["active_graph_published"] != false
    {
        return Err(invalid(
            "render is not a complete private native publication",
        ));
    }
    if store.review_graph_state(&before.token.job_id)?.as_ref() != Some(before) {
        return Err(invalid("Review changed before publication; reload"));
    }
    let admitted = read_publication(store, data, before, &rendered["native_publication"])?;
    if admitted.before != *before {
        return Err(invalid("publication binds a different prior graph"));
    }
    // A second physical/semantic admission immediately before CAS keeps failed
    // validation outside SQLite and leaves the old graph and undo row intact.
    let final_fence = read_publication(store, data, before, &rendered["native_publication"])?;
    if final_fence.snapshot.json() != admitted.snapshot.json()
        || final_fence.graph != admitted.graph
    {
        return Err(invalid("publication changed during the final native fence"));
    }
    store
        .commit_review_graph(
            &before.token,
            admitted.snapshot.json(),
            &admitted.graph,
            super::now_ms(),
        )
        .map_err(Into::into)
}

pub(super) fn committed_packet(state: &ReviewGraphState) -> SupervisorResult<Value> {
    Ok(
        json!({"schema_version":1,"kind":"review_native_committed_render",
        "job_id":state.token.job_id,"revision":state.token.revision.to_string(),
        "snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "snapshot":decode(&state.snapshot_json,LIMIT)?,"active_graph_published":true,
        "correction_ready":false}),
    )
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>, undo: bool) -> SupervisorResult<()> {
    run_inner(&mut args, undo, false)
}

pub(super) fn run_effective(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, false, true)
}

fn run_inner(
    mut args: impl Iterator<Item = OsString>,
    undo: bool,
    effective: bool,
) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("active argument is not UTF-8"))?;
        if ![
            "--data-root",
            "--job-id",
            "--revision",
            "--snapshot-sha256",
            "--graph-sha256",
        ]
        .contains(&flag.as_str())
            && !(effective
                && [
                    "--processing-root",
                    "--processing-manifest-sha256",
                    "--processing-manifest-size-bytes",
                    "--processing-source-sha",
                ]
                .contains(&flag.as_str()))
            || values.contains_key(&flag)
        {
            return Err(invalid("unknown or duplicate active argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("missing active argument"))?
            .into_string()
            .map_err(|_| invalid("active argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| invalid("active/undo requires data/job and complete current token"))
    };
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute()
        || data
            .components()
            .any(|p| matches!(p, std::path::Component::ParentDir))
    {
        return Err(invalid(
            "native active graph requires an absolute control root",
        ));
    }
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _guard = if undo {
        Some(super::JobExecutionGuard::acquire(&db, job)?)
    } else {
        None
    };
    let store = if undo {
        DurableStore::open(&db)?
    } else {
        DurableStore::open_read_only(&db)?
    };
    let current = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("Review graph is not initialized"))?;
    let revision = tick(&Value::String(required("--revision")?.into()), false)?;
    if current.token.revision != revision
        || current.token.snapshot_sha256 != required("--snapshot-sha256")?
        || current.token.graph_sha256 != required("--graph-sha256")?
    {
        return Err(invalid("active Review token changed; reload"));
    }
    if effective {
        let admitted = super::review_effective::read_current(&store, &data, &current)?;
        let keys = [
            "--processing-root",
            "--processing-manifest-sha256",
            "--processing-manifest-size-bytes",
            "--processing-source-sha",
        ];
        let count = keys.iter().filter(|key| values.contains_key(**key)).count();
        let processing = if count == 0 {
            None
        } else {
            if count != keys.len() {
                return Err(invalid(
                    "processing selection requires root, exact manifest hash/size and source SHA",
                ));
            }
            let size = tick(
                &Value::String(required("--processing-manifest-size-bytes")?.into()),
                false,
            )?;
            Some(super::review_runtime::AdmittedProcessingRuntime::read(
                &store,
                job,
                &PathBuf::from(required("--processing-root")?),
                &json!({"sha256":required("--processing-manifest-sha256")?,"size_bytes":size}),
                required("--processing-source-sha")?,
            )?)
        };
        let packet = admitted.packet_with_processing(processing.as_ref())?;
        if store.review_graph_state(job)?.as_ref() != Some(&current) {
            return Err(invalid(
                "effective state changed during processing release admission; reload",
            ));
        }
        return super::emit_value(&mut std::io::stdout().lock(), packet);
    }
    let current_snapshot = read_active(&store, &data, &current)?;
    let (state, snapshot) = if undo {
        let target = store
            .review_undo_target(&current.token)?
            .ok_or_else(|| invalid("no Review correction to undo"))?;
        let admitted_target = read_active(&store, &data, &target)?;
        let restored = admitted_target.undo_proposal(&current_snapshot)?;
        read_active(&store, &data, &target)?;
        let state = store.undo_review_graph(&current.token, restored.json(), super::now_ms())?;
        (state, restored)
    } else {
        if store.review_graph_state(job)?.as_ref() != Some(&current) {
            return Err(invalid("Review changed during playback admission"));
        }
        (current, current_snapshot)
    };
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_active_native_state",
        "job_id":job,"revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,
        "graph_sha256":state.token.graph_sha256,"snapshot":decode(snapshot.json(),LIMIT)?,
        "active_graph_published":matches!(decode(&state.graph_json,LIMIT)?["kind"].as_str(),
            Some("review_bound_retained_render_graph"|"review_bound_retained_render_graph_v2"|"review_bound_retained_render_graph_v3")),
        "correction_ready":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;

    #[test]
    fn sealed_publication_before_state_roundtrips_exact_native_original_and_refuses_substitution() {
        with_admitted_original(|_store, data, state| {
            let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id).unwrap();
            let empty = assets.accept_json("{}").unwrap();
            let snapshot =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let object = prepare(
                &assets,
                state,
                &snapshot,
                &empty,
                &empty,
                &empty,
                &empty,
                "sha256:unexecuted-test",
                "volume-test",
                None,
                None,
                None,
            )
            .unwrap();
            let original = decode(&assets.read_json(&object).unwrap(), LIMIT).unwrap();
            assert_eq!(publication_version(&original).unwrap(), 1);
            assert!(original.get("speech_inventory").is_none());
            assert_eq!(
                before_state(&original, &assets, &state.token.job_id).unwrap(),
                *state
            );
            for index in 0..5 {
                let mut changed = original.clone();
                match index {
                    0 => changed["before"]["job_id"] = "other-job".into(),
                    1 => changed["before"]["revision"] = "1".into(),
                    2 => changed["before"]["revision"] = 0.into(),
                    3 => changed["before"]["snapshot"]["path"] = "outside-native-store.json".into(),
                    _ => changed["before"]
                        .as_object_mut()
                        .unwrap()
                        .insert("caller_authorized".into(), true.into())
                        .map(|_| ())
                        .unwrap_or(()),
                }
                assert!(
                    before_state(&changed, &assets, &state.token.job_id).is_err(),
                    "substitution {index}"
                );
            }
        });
    }

    #[test]
    fn publication_versions_require_explicit_tags_and_inventory_without_migrating_v1() {
        let one = json!({"schema_version":1,"kind":"review_native_publication","before":{},"proposal":{},
            "corrected_timeline":{},"context":{},"retained_render":{},"prepared_snapshot":{},
            "execution_identity":"unused-shape-fixture","output_volume_id":"unused-shape-fixture","mixed":null});
        let original = one.to_string();
        assert_eq!(publication_version(&one).unwrap(), 1);
        let mut two = one.clone();
        two["schema_version"] = 2.into();
        two["kind"] = "review_native_publication_v2".into();
        assert!(publication_version(&two).is_err());
        two.as_object_mut()
            .unwrap()
            .insert("speech_inventory".into(), json!({}));
        assert_eq!(publication_version(&two).unwrap(), 2); // shape dispatch only, no physical admission.
        for case in 0..4 {
            let mut bad = two.clone();
            match case {
                0 => bad["kind"] = "review_native_publication".into(),
                1 => bad["schema_version"] = true.into(),
                2 => bad["schema_version"] = 3.into(),
                _ => {
                    bad.as_object_mut()
                        .unwrap()
                        .insert("caller_authorized".into(), true.into());
                }
            }
            assert!(publication_version(&bad).is_err());
        }
        let mut bad = one.clone();
        bad.as_object_mut()
            .unwrap()
            .insert("speech_inventory".into(), json!({}));
        assert!(publication_version(&bad).is_err());
        assert_eq!(one.to_string(), original);
    }

    #[test]
    fn invalid_publication_preserves_exact_sqlite_head_and_undo() {
        with_admitted_original(|store, data, state| {
            let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id).unwrap();
            let foreign = assets
                .accept_json(r#"{"schema_version":999,"kind":"unknown"}"#)
                .unwrap();
            let rendered = json!({"retained_final_output":true,"retained_receipt_reopened":true,
                "active_graph_published":false,"native_publication":descriptor(&foreign)});
            assert!(commit(store, data, state, &rendered).is_err());
            assert_eq!(
                store
                    .review_graph_state(&state.token.job_id)
                    .unwrap()
                    .as_ref(),
                Some(state)
            );
            assert!(store.review_undo_target(&state.token).unwrap().is_none());
            let mut stale = state.clone();
            stale.token.graph_sha256 = format!("sha256:{}", "0".repeat(64));
            assert!(commit(store, data, &stale, &rendered).is_err());
            assert_eq!(
                store
                    .review_graph_state(&state.token.job_id)
                    .unwrap()
                    .as_ref(),
                Some(state)
            );
        });
    }

    #[test]
    fn restored_original_reader_keeps_monotonic_revision_and_refuses_unknown_graph_or_changed_media(
    ) {
        with_admitted_original(|store, data, state| {
            let before = read_active(store, data, state).unwrap();
            let proposed = before
                .correction("finding-a", Some("Chào bạn."), None, ReviewMode::Subtitles)
                .unwrap();
            // Low-level test-only row, not a native-admitted video publication.
            let unknown = store
                .commit_review_graph(&state.token, proposed.json(), r#"{"kind":"unknown"}"#, 10)
                .unwrap();
            assert!(read_active(store, data, &unknown).is_err());
            let target = store.review_undo_target(&unknown.token).unwrap().unwrap();
            let prior = read_active(store, data, &target).unwrap();
            let restored = prior.undo_proposal(&proposed).unwrap();
            let two = store
                .undo_review_graph(&unknown.token, restored.json(), 11)
                .unwrap();
            assert_eq!(
                read_active(store, data, &two).unwrap().json(),
                restored.json()
            );
            assert_eq!(two.token.revision, 2);
            assert_eq!(two.graph_json, state.graph_json);
            assert_eq!(two.undo_depth, 0);
            assert!(store
                .commit_review_graph(&state.token, proposed.json(), "{}", 12)
                .is_err());
            let path = data.join("output/editable_timeline.json");
            let old = std::fs::read(&path).unwrap();
            std::fs::write(&path, b"{}").unwrap();
            assert!(read_active(store, data, &two).is_err());
            assert_eq!(
                store.review_graph_state(&state.token.job_id).unwrap(),
                Some(two)
            );
            std::fs::write(path, old).unwrap();
        });
    }

    #[test]
    fn active_command_refuses_duplicate_unknown_or_incomplete_tokens_before_mutation() {
        for args in [
            vec!["--job-id", "job-seed", "--job-id", "other"],
            vec!["--caller-path", "arbitrary.mp4"],
            vec!["--data-root", "relative"],
            vec!["--job-id", "job-seed"],
        ] {
            assert!(run(args.into_iter().map(OsString::from), true).is_err());
        }
    }
}
