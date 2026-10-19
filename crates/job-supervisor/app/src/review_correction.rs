//! Native original-cue preparation and private synthesis. No graph publication.
//! The command only accepts a native stale-client token and cue edit values.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument, PcmPin};
use super::review_document::{AdmittedSnapshot, ReviewMode, closed, decode, tick};
use super::review_request::{BoundCueRequest, CueRequestPins};
use super::review_runtime::AdmittedRuntime;
use super::review_seed::{collect, read_original};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{DurableStore, review_graph::ReviewGraphState};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
pub(super) fn descriptor(object: &AcceptedJsonDocument) -> Value {
    json!({"path":object.path,"sha256":object.sha256,"size_bytes":object.size_bytes})
}

fn admit_prepared(
    raw: &str,
    context: &AcceptedJsonDocument,
    before: &AdmittedSnapshot,
    after: &AdmittedSnapshot,
    graph: &Value,
    voice: &Value,
    manifest: &str,
) -> SupervisorResult<BoundCueRequest> {
    let packet = decode(raw, 65536)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "request_json",
            "request_sha256",
            "config",
            "voice",
            "model_weights_verified",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_prepared_cue_request"
        || packet["context_sha256"] != context.sha256
        || packet["voice"] != *voice
        || packet["model_weights_verified"] != false
    {
        return Err(invalid(
            "cue preparation differs from native original/catalog/context binding",
        ));
    }
    let id = before.finding(after.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("finding is not a canonical cue"))?;
    let original = graph["canonical_inputs"]
        .as_array()
        .ok_or_else(|| invalid("original inputs are missing"))?
        .iter()
        .find(|i| i["segment_id"] == id)
        .ok_or_else(|| invalid("selected cue has no original input"))?;
    let request = BoundCueRequest::read(
        packet["request_json"]
            .as_str()
            .ok_or_else(|| invalid("prepared request lacks exact bytes"))?,
        before,
        after,
        CueRequestPins {
            manifest_sha256: manifest,
            original_input: original,
            previous_provenance: &graph["tts_provenance"],
            config: &packet["config"],
            target_voice: voice,
        },
    )?;
    if packet["request_sha256"] != request.input_hash() {
        return Err(invalid("prepared request checksum differs"));
    }
    Ok(request)
}

fn unchanged(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid(
            "Review generation changed while preparing the cue; reload",
        ));
    }
    Ok(())
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, false, false, false, false, false)
}

pub(super) fn run_synthesis(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, false, false, false, false)
}

pub(super) fn run_mix(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, false, false, false)
}

pub(super) fn run_retained_mix(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, true, false, false)
}

pub(super) fn run_render(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, true, true, false)
}

pub(super) fn run_commit_render(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, true, true, true)
}

fn run_inner(
    mut args: impl Iterator<Item = OsString>,
    synthesize: bool,
    mix: bool,
    retain: bool,
    render: bool,
    publish: bool,
) -> SupervisorResult<()> {
    let mut values = std::collections::HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("cue argument is not UTF-8"))?;
        if ![
            "--root",
            "--data-root",
            "--job-id",
            "--finding-id",
            "--revision",
            "--snapshot-sha256",
            "--graph-sha256",
            "--translation",
            "--voice",
        ]
        .contains(&flag.as_str())
            || values.contains_key(&flag)
        {
            return Err(invalid("unknown or duplicate cue preparation argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("missing cue argument"))?
            .into_string()
            .map_err(|_| invalid("cue argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values.get(key).map(String::as_str).ok_or_else(|| {
            invalid(
                "cue preparation requires root/data/job/finding and complete native revision token",
            )
        })
    };
    let root = PathBuf::from(required("--root")?);
    let data = PathBuf::from(required("--data-root")?);
    let job = required("--job-id")?;
    let finding = required("--finding-id")?;
    let revision = tick(&Value::String(required("--revision")?.into()), false)?;
    let snapshot_hash = required("--snapshot-sha256")?;
    let graph_hash = required("--graph-sha256")?;
    let translation = values.get("--translation").map(String::as_str);
    let voice = values.get("--voice").map(String::as_str);
    if translation.is_none() && voice.is_none() {
        return Err(invalid(
            "cue preparation requires an actual text or voice edit",
        ));
    }
    super::validate_job_id(job)?;
    super::review_pin::plain(&root)?;
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _guard = super::JobExecutionGuard::acquire(&db, job)?;
    // Existing private commands remain read-only. Only the separate explicit
    // native publication command may use the supervisor-owned CAS transaction.
    let store = if publish {
        DurableStore::open(&db)?
    } else {
        DurableStore::open_read_only(&db)?
    };
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("original Review state must be explicitly initialized first"))?;
    if state.token.revision != revision
        || state.token.snapshot_sha256 != snapshot_hash
        || state.token.graph_sha256 != graph_hash
    {
        return Err(invalid(
            "Review generation changed; reload before preparing a cue",
        ));
    }
    let seed = read_original(&store, &data, &state)?;
    let retained_output = if retain {
        Some(super::review_retention::NativeOutputPin::read(
            &seed.output_root,
        )?)
    } else {
        None
    };
    let graph = decode(&seed.graph, LIMIT)?;
    if render && graph["mode"] == "b1" {
        // B1 enters rendering before any voice/catalog/model execution path.
        let after = seed
            .snapshot
            .correction(finding, translation, voice, ReviewMode::Subtitles)?;
        let runtime = AdmittedRuntime::read(&store, job, &root)?;
        runtime.render_recipe(None)?;
        let assets = AcceptedAudioStore::open_existing(&data, job)?;
        let timeline = assets.accept_json(&seed.corrected_timeline(&after)?)?;
        let candidates = private_root(&seed.output_root, job)?;
        let start_request = store
            .job_start_request(job)?
            .ok_or_else(|| invalid("missing immutable render options"))?;
        let rendered = super::review_render::execute(super::review_render::RenderInputs {
            runtime: &runtime,
            assets: &assets,
            state: &state,
            after: &after,
            timeline: &timeline,
            mixed: None,
            accepted_cue: None,
            output: &seed.output_root,
            output_pin: retained_output.as_ref().unwrap(),
            candidates: &candidates,
            start_request: &start_request,
        })?;
        assets.read_json(&timeline)?;
        unchanged(&store, &state)?;
        read_original(&store, &data, &state)?;
        if publish {
            let committed = super::review_active::commit(&store, &data, &state, &rendered)?;
            return super::emit_value(
                &mut std::io::stdout().lock(),
                super::review_active::committed_packet(&committed)?,
            );
        }
        return super::emit_value(
            &mut std::io::stdout().lock(),
            json!({
                "schema_version":1,"kind":"review_rendered_original_cue","mode":"b1","job_id":job,
                "revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,
                "graph_sha256":state.token.graph_sha256,"finding_id":finding,"proposal":decode(after.json(),LIMIT)?,
                "release_sha256":runtime.release_hash,"corrected_timeline":descriptor(&timeline),
                "private_render":rendered,"tts_executed":false,"mix_executed":false,"correction_ready":false
            }),
        );
    }
    if graph["mode"] != "b2" {
        return Err(invalid(
            "B1 requires the subtitle regeneration path; never invent a TTS voice",
        ));
    }
    let after = seed
        .snapshot
        .correction(finding, translation, voice, ReviewMode::Dubbed)?;
    let selected = after.finding(finding)?["voice"]
        .as_str()
        .ok_or_else(|| invalid("correction lacks an assigned voice"))?;
    let runtime = AdmittedRuntime::read(&store, job, &root)?;
    if render {
        // Refuse a historical producer missing the renderer before inference.
        // The current supervisor never substitutes code into its pinned root.
        runtime.render_recipe(None)?;
    }
    if mix {
        runtime.mix_execution()?;
    }
    let target_voice = runtime.voice(selected)?;
    let assets = AcceptedAudioStore::open_existing(&data, job)?;
    // The opt-in successor prepares the matching timeline before inference.
    // This is private metadata; it cannot publish a partial mix/render graph.
    let corrected_timeline = if retain {
        Some(assets.accept_json(&seed.corrected_timeline(&after)?)?)
    } else {
        None
    };
    let context = json!({"schema_version":1,"kind":"review_original_cue_context","manifest_sha256":state.token.original_manifest_sha256,
        "before_snapshot_json":seed.snapshot.json(),"after_snapshot_json":after.json(),"source_graph_json":seed.graph});
    let context = assets.accept_json(&serde_json::to_string(&context)?)?;
    let mut command = Command::new(&runtime.python);
    command.args([
        OsString::from("-I"),
        OsString::from("-S"),
        OsString::from("-B"),
        runtime
            .root
            .join("app/engine/dubflow/review/cue_plan.py")
            .into_os_string(),
        OsString::from("--context"),
        context.path.clone().into_os_string(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--app-root"),
        runtime.root.join("app").into_os_string(),
        OsString::from("--profile"),
        runtime.profile.clone().into_os_string(),
    ]);
    let raw = collect(command, Duration::from_secs(60), 65536)?;
    let request = admit_prepared(
        &raw,
        &context,
        &seed.snapshot,
        &after,
        &graph,
        &target_voice,
        &state.token.original_manifest_sha256,
    )?;
    assets.read_json(&context)?;
    let accepted = assets.accept_json(request.raw())?;
    unchanged(&store, &state)?;
    let _original = read_original(&store, &data, &state)?;
    if synthesize {
        runtime.cue_execution()?;
        let candidates = private_root(&seed.output_root, job)?;
        let result = synthesize_accepted(
            &runtime,
            &assets,
            &candidates,
            &request,
            &accepted,
            retained_output.as_ref(),
            None,
        )?;
        let mixed = if mix {
            Some(mix_private(
                &runtime,
                &assets,
                &candidates,
                &state,
                after.json(),
                &request,
                &result,
                retained_output.as_ref(),
            )?)
        } else {
            None
        };
        assets.read_json(&context)?;
        assets.read_json(&accepted)?;
        unchanged(&store, &state)?;
        let _original = read_original(&store, &data, &state)?;
        if let Some(timeline) = &corrected_timeline {
            assets.read_json(timeline)?;
        }
        let rendered = if render {
            Some(super::review_render::execute(
                super::review_render::RenderInputs {
                    runtime: &runtime,
                    assets: &assets,
                    state: &state,
                    after: &after,
                    timeline: corrected_timeline.as_ref().unwrap(),
                    mixed: mixed.as_ref(),
                    accepted_cue: Some(&result),
                    output: &seed.output_root,
                    output_pin: retained_output.as_ref().unwrap(),
                    candidates: &candidates,
                    start_request: &store
                        .job_start_request(job)?
                        .ok_or_else(|| invalid("missing immutable render options"))?,
                },
            )?)
        } else {
            None
        };
        unchanged(&store, &state)?;
        let _original = read_original(&store, &data, &state)?;
        if publish {
            let committed = super::review_active::commit(
                &store,
                &data,
                &state,
                rendered
                    .as_ref()
                    .ok_or_else(|| invalid("publication requires retained render"))?,
            )?;
            return super::emit_value(
                &mut std::io::stdout().lock(),
                super::review_active::committed_packet(&committed)?,
            );
        }
        let mut packet = json!({"schema_version":1,"kind":if render {"review_rendered_original_cue"} else if retain {"review_retained_mixed_original_cue"} else if mix {"review_mixed_original_cue"} else {"review_synthesized_original_cue"},
            "job_id":job,"revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
            "finding_id":finding,"context":descriptor(&context),"request":descriptor(&accepted),"release_sha256":runtime.release_hash,
            "proposal":decode(after.json(),LIMIT)?,"accepted_cue":result,"model_weights_verified":true,"correction_ready":false});
        if let Some(timeline) = &corrected_timeline {
            packet
                .as_object_mut()
                .unwrap()
                .insert("corrected_timeline".into(), descriptor(timeline));
        }
        if let Some(candidate) = mixed {
            packet
                .as_object_mut()
                .unwrap()
                .insert("private_mix".into(), candidate);
        }
        if let Some(candidate) = rendered {
            packet
                .as_object_mut()
                .unwrap()
                .insert("private_render".into(), candidate);
        }
        return super::emit_value(&mut std::io::stdout().lock(), packet);
    }
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_prepared_original_cue",
        "job_id":job,"revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "finding_id":finding,"context":descriptor(&context),"request":descriptor(&accepted),"release_sha256":runtime.release_hash,
        "proposal":decode(after.json(),LIMIT)?,"model_weights_verified":false,"correction_ready":false}),
    )
}

const CUE_PACKET_LIMIT: usize = 256 * 1024;
const CUE_DOCUMENT_LIMIT: usize = 64 * 1024;

pub(super) fn accepted_json(
    value: &Value,
    assets: &AcceptedAudioStore,
) -> SupervisorResult<AcceptedJsonDocument> {
    closed(value, &["path", "sha256", "size_bytes"])?;
    let object = AcceptedJsonDocument {
        path: PathBuf::from(
            value["path"]
                .as_str()
                .ok_or_else(|| invalid("missing accepted metadata path"))?,
        ),
        sha256: value["sha256"]
            .as_str()
            .ok_or_else(|| invalid("missing accepted metadata hash"))?
            .into(),
        size_bytes: value["size_bytes"]
            .as_u64()
            .ok_or_else(|| invalid("bad accepted metadata size"))?,
    };
    assets.read_json(&object)?;
    Ok(object)
}

fn mix_descriptor(object: &AcceptedJsonDocument, waves: &Path) -> Value {
    json!({"path":object.path,"sha256":object.sha256,"size_bytes":object.size_bytes,
        "artifact_root":waves,"metadata_root":object.path.parent().unwrap()})
}

pub(super) fn admit_mix_metadata(
    raw: &str,
    context: &Value,
    context_hash: &str,
    template: &Value,
) -> SupervisorResult<(String, String)> {
    let packet = decode(raw, LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "composition_json",
            "composition_sha256",
            "mix_document_json",
            "mix_document_sha256",
            "active_graph_published",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_private_mix_candidate"
        || packet["context_sha256"] != context_hash
        || packet["active_graph_published"] != false
    {
        return Err(invalid(
            "mix candidate differs from native context or claims publication",
        ));
    }
    let exact = |key: &str, hash: &str| -> SupervisorResult<String> {
        let text = packet[key]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= LIMIT)
            .ok_or_else(|| invalid("private mix metadata exceeds its bound"))?;
        if packet[hash] != digest(text) {
            return Err(invalid("private mix metadata checksum changed"));
        }
        Ok(text.into())
    };
    let composition_raw = exact("composition_json", "composition_sha256")?;
    let mix_raw = exact("mix_document_json", "mix_document_sha256")?;
    let composition = decode(&composition_raw, LIMIT)?;
    closed(
        &composition,
        &[
            "schema_version",
            "kind",
            "job_id",
            "manifest_sha256",
            "review_snapshot_sha256",
            "source",
            "documents",
            "cues",
            "mix_producer_pin",
        ],
    )?;
    let graph = decode(
        context["source_graph_json"]
            .as_str()
            .ok_or_else(|| invalid("missing original graph"))?,
        LIMIT,
    )?;
    let request = decode(
        context["request_json"]
            .as_str()
            .ok_or_else(|| invalid("missing cue request"))?,
        CUE_DOCUMENT_LIMIT,
    )?;
    let mut documents = vec![
        context["original_tts_document"].clone(),
        context["accepted_tts_document"].clone(),
    ];
    documents.sort_by(|a, b| a["sha256"].as_str().cmp(&b["sha256"].as_str()));
    documents.dedup();
    let inputs = graph["canonical_inputs"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("missing bounded canonical mix inputs"))?;
    let cues:Vec<Value>=inputs.iter().map(|item| {
        let selected=item["segment_id"]==request["cue_id"];
        json!({"tts_input":if selected {&request["tts_input"]} else {item},
            "document_sha256":if selected {&context["accepted_tts_document"]["sha256"]} else {&context["original_tts_document"]["sha256"]}})
    }).collect();
    let source = json!({"path":context["source_audio"]["path"],"sha256":context["source_audio"]["sha256"],
        "start":template["source_start"],"end":template["source_end"],"source_id":template["source_id"],"layout":template["source_layout"]});
    let mut producer = template["provenance"]
        .as_object()
        .ok_or_else(|| invalid("missing original mix producer"))?
        .clone();
    producer.remove("input_hash");
    producer.remove("source_hash");
    if composition["schema_version"].as_u64() != Some(2)
        || composition["kind"] != "review_audio_composition"
        || composition["job_id"] != graph["job_id"]
        || composition["manifest_sha256"] != context["manifest_sha256"]
        || composition["review_snapshot_sha256"] != request["after_snapshot_sha256"]
        || composition["source"] != source
        || composition["documents"] != json!(documents)
        || composition["cues"] != json!(cues)
        || composition["mix_producer_pin"] != json!(producer)
    {
        return Err(invalid(
            "mix composition substitutes source, snapshot, assignments or producer",
        ));
    }
    let mix = decode(&mix_raw, LIMIT)?;
    let mut expected_provenance = template["provenance"].clone();
    expected_provenance["input_hash"] = packet["composition_sha256"].clone();
    expected_provenance["source_hash"] = context["source_audio"]["sha256"].clone();
    let mut segments = template["segments"]
        .as_array()
        .ok_or_else(|| invalid("missing original mix segments"))?
        .clone();
    for segment in &mut segments {
        if segment["segment_id"] == request["cue_id"] {
            segment["status"] = "completed".into();
            segment["fallback_used"] = false.into();
        }
    }
    let failed: std::collections::BTreeSet<&str> = segments
        .iter()
        .filter(|s| s["status"] != "completed")
        .filter_map(|s| s["segment_id"].as_str())
        .collect();
    let failures = mix["failures"]
        .as_array()
        .ok_or_else(|| invalid("missing private mix failure evidence"))?;
    let failed_output: std::collections::BTreeSet<&str> = failures
        .iter()
        .filter_map(|f| f["segment_id"].as_str())
        .collect();
    if failed != failed_output || failures.len() != failed_output.len() {
        return Err(invalid("private mix loses or invents per-cue failures"));
    }
    if mix["schema_version"] != template["schema_version"]
        || mix["kind"] != "audio_mix_document"
        || mix["source_kind"] != "source_audio"
        || mix["target_language"] != "vi"
        || mix["provenance"] != expected_provenance
        || mix["segment_ids"] != template["segment_ids"]
        || mix["segments"] != json!(segments)
        || [
            "source_id",
            "source_layout",
            "source_start",
            "source_end",
            "sample_rate",
            "channels",
        ]
        .iter()
        .any(|key| mix[*key] != template[*key])
        || mix["failures"].as_array().map_or(true, |failures| {
            failures
                .iter()
                .any(|f| f["segment_id"] == request["cue_id"])
        })
    {
        return Err(invalid(
            "mix candidate substitutes original source, producer or unrelated cue evidence",
        ));
    }
    Ok((composition_raw, mix_raw))
}

fn mix_private(
    runtime: &AdmittedRuntime,
    assets: &AcceptedAudioStore,
    output: &Path,
    state: &ReviewGraphState,
    after: &str,
    request: &BoundCueRequest,
    cue: &Value,
    retained_output: Option<&super::review_retention::NativeOutputPin>,
) -> SupervisorResult<Value> {
    let bound = decode(&state.graph_json, LIMIT)?;
    let graph_raw = bound["source_graph_json"]
        .as_str()
        .ok_or_else(|| invalid("missing native original graph"))?;
    let graph = decode(graph_raw, LIMIT)?;
    let original = &bound["accepted_documents"];
    let manifest_object = accepted_json(&original["job_manifest"], assets)?;
    let manifest = decode(&assets.read_json(&manifest_object)?, LIMIT)?;
    let speech = accepted_json(&original["tts_document"], assets)?;
    let template_object = accepted_json(&original["mix_document"], assets)?;
    let template = decode(&assets.read_json(&template_object)?, LIMIT)?;
    let accepted = accepted_json(&cue["tts_document"], assets)?;
    let source_root = original_audio_root(&graph)?;
    let wave_path = PathBuf::from(
        cue["audio"]["path"]
            .as_str()
            .ok_or_else(|| invalid("missing accepted cue audio"))?,
    );
    let mut source = manifest["artifacts"]["source_audio"].clone();
    closed(&source, &["path", "sha256", "size_bytes"])?;
    source.as_object_mut().unwrap().insert(
        "artifact_root".into(),
        source_root.to_string_lossy().into_owned().into(),
    );
    let context_value = json!({"schema_version":1,"kind":"review_native_mix_context","manifest_sha256":state.token.original_manifest_sha256,
        "before_snapshot_json":state.snapshot_json,"after_snapshot_json":after,"source_graph_json":graph_raw,
        "request_json":request.raw(),"request_sha256":request.input_hash(),"source_audio":source,
        "original_tts_document":mix_descriptor(&speech,&source_root),"mix_template":mix_descriptor(&template_object,&source_root),
        "accepted_tts_document":mix_descriptor(&accepted,wave_path.parent().unwrap())});
    let context = assets.accept_json(&serde_json::to_string(&context_value)?)?;
    let mut command = cue_command(
        runtime,
        &runtime.mix_execution()?,
        &candidate_scratch(output)?,
    )?;
    command.args([
        OsString::from("--context"),
        context.path.as_os_str().to_owned(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--output-root"),
        output.as_os_str().to_owned(),
    ]);
    let raw = collect(command, Duration::from_secs(45 * 60), LIMIT)?;
    let (composition_raw, mix_raw) =
        admit_mix_metadata(&raw, &context_value, &context.sha256, &template)?;
    let dependencies = [
        &context,
        &speech,
        &template_object,
        &accepted,
        &manifest_object,
    ];
    for object in dependencies {
        assets.read_json(object)?;
    }
    let result = accept_private_mix(PrivateMixInputs {
        assets,
        output,
        job: &state.token.job_id,
        context: &context,
        composition: &composition_raw,
        mix: &mix_raw,
        frame_count: template["original_audio"]["frame_count"]
            .as_u64()
            .ok_or_else(|| invalid("original mix frame count missing"))?,
        retained_output,
    })?;
    for object in dependencies {
        assets.read_json(object)?;
    }
    Ok(result)
}

/// Only callers that independently admit the whole composition and mix
/// metadata may reach this shared physical verification/retention boundary.
pub(super) struct PrivateMixInputs<'a> {
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) output: &'a Path,
    pub(super) job: &'a str,
    pub(super) context: &'a AcceptedJsonDocument,
    pub(super) composition: &'a str,
    pub(super) mix: &'a str,
    pub(super) frame_count: u64,
    pub(super) retained_output: Option<&'a super::review_retention::NativeOutputPin>,
}

pub(super) fn accept_private_mix(input: PrivateMixInputs<'_>) -> SupervisorResult<Value> {
    let PrivateMixInputs {
        assets,
        output,
        job,
        context,
        composition: composition_raw,
        mix: mix_raw,
        frame_count,
        retained_output,
    } = input;
    let composition_hash = digest(&composition_raw);
    let mix = decode(&mix_raw, LIMIT)?;
    let generation = output.join(format!("mix-{}", &composition_hash[7..]));
    let deadline = std::time::Instant::now() + Duration::from_secs(180);
    let mut admitted_audio = Vec::new();
    for (role, name) in [
        ("original_audio", "original.wav"),
        ("dialogue_stem", "dialogue.wav"),
        ("final_mix", "final.wav"),
    ] {
        let artifact = &mix[role];
        let path = PathBuf::from(
            artifact["path"]
                .as_str()
                .ok_or_else(|| invalid("private mix lacks a waveform path"))?,
        );
        let parent = path
            .parent()
            .ok_or_else(|| invalid("private mix lacks a parent"))?;
        let stream = parent.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if parent.parent() != Some(generation.as_path())
            || path.file_name().and_then(|n| n.to_str()) != Some(name)
            || stream.len() != 71
            || !stream.starts_with("stream-")
            || !stream[7..]
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || artifact["kind"] != role
            || artifact["format"] != "wav"
            || artifact["bits_per_sample"].as_u64() != Some(16)
            || artifact["sample_rate"].as_u64() != Some(48000)
            || artifact["channels"].as_u64() != Some(2)
            || artifact["frame_count"].as_u64() != Some(frame_count)
            || artifact["metrics"]["content_hash"] != artifact["content_hash"]
            || artifact["metrics"]["frame_count"] != artifact["frame_count"]
        {
            return Err(invalid(
                "private mix escapes its generation or changes PCM/metrics",
            ));
        }
        let pin = PcmPin {
            sha256: artifact["content_hash"]
                .as_str()
                .ok_or_else(|| invalid("missing private mix hash"))?
                .into(),
            size_bytes: super::review_assets::regular(&path)?.metadata()?.len(),
            sample_rate: 48000,
            channels: 2,
            frame_count: artifact["frame_count"]
                .as_u64()
                .ok_or_else(|| invalid("bad private mix frame count"))?,
        };
        super::review_assets::verify_private_mix_audio(&generation, &path, &pin, deadline)?;
        admitted_audio.push((role, path, pin));
    }
    assets.read_json(context)?;
    let composition = assets.accept_json(&composition_raw)?;
    let document = assets.accept_json(&mix_raw)?;
    if let Some(output_pin) = retained_output {
        // Output comes from the immutable native job seed, never an extra
        // browser/worker flag. The output-wide quota includes old undo objects
        // and interrupted copies. Refusal preserves every original artifact.
        let retained = super::review_retention::NativeMixStore::new(
            output_pin,
            job,
            super::review_assets::MixStorageBudget {
                maximum_bytes: 32 * 1024 * 1024 * 1024,
                minimum_free_bytes: 2 * 1024 * 1024 * 1024,
            },
        )?;
        let retention_deadline = std::time::Instant::now() + Duration::from_secs(720);
        let mut located = Vec::new();
        for (role, path, pin) in admitted_audio {
            let object = retained.accept(&generation, &path, &pin, retention_deadline)?;
            located.push((role, object));
        }
        // Native path-only retargeting preserves the admitted producer/source
        // semantics and independently revalidates every canonical physical pin.
        let retained_raw = retained.retarget_admitted_document(
            &mix_raw,
            &composition_hash,
            &located,
            retention_deadline,
        )?;
        let retained_document = assets.accept_json(&retained_raw)?;
        assets.read_json(&retained_document)?;
        // Recheck all roles after sealing metadata. These are private retained
        // objects, not an active playback graph or a SQLite transaction.
        for (_, object) in &located {
            retained.verify(object, retention_deadline)?;
        }
        return Ok(
            json!({"context":descriptor(&context),"composition":descriptor(&composition),
            "candidate_mix_document":descriptor(&document),"mix_document":descriptor(&retained_document),
            "private_audio_verified":true,"retained_audio":true,"active_graph_published":false,
            "mix_metadata_retargeted":true,
            "retained_mix_audio":located.iter().map(|(role,object)|json!({"role":role,"audio":object.descriptor()})).collect::<Vec<_>>()}),
        );
    }
    Ok(
        json!({"context":descriptor(&context),"composition":descriptor(&composition),"mix_document":descriptor(&document),
        "private_audio_verified":true,"retained_audio":false,"active_graph_published":false}),
    )
}

fn digest(raw: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
}

fn document(packet: &Value, json_key: &str, hash_key: &str) -> SupervisorResult<String> {
    let raw = packet[json_key]
        .as_str()
        .filter(|r| !r.is_empty() && r.len() <= CUE_DOCUMENT_LIMIT)
        .ok_or_else(|| invalid("single-cue producer document exceeds its bound"))?;
    if !decode(raw, CUE_DOCUMENT_LIMIT)?.is_object() || packet[hash_key] != digest(raw) {
        return Err(invalid("single-cue document hash or syntax differs"));
    }
    Ok(raw.to_string())
}

struct CandidateDocuments {
    tts: String,
    spoken: String,
    recipe: String,
    path: PathBuf,
    pin: PcmPin,
}

fn admit_candidate(
    raw: &str,
    request: &BoundCueRequest,
    generation: &Path,
) -> SupervisorResult<CandidateDocuments> {
    let packet = decode(raw, CUE_PACKET_LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "request_sha256",
            "recipe_identity",
            "tts_document_json",
            "tts_document_sha256",
            "spoken_text_json",
            "spoken_text_sha256",
            "model_weights_verified",
        ],
    )?;
    let recipe = packet["recipe_identity"]
        .as_str()
        .filter(|r| {
            r.len() == 64
                && r.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
        .ok_or_else(|| invalid("owned cue recipe identity is invalid"))?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_cue_candidate"
        || packet["request_sha256"] != request.input_hash()
        || packet["model_weights_verified"] != true
    {
        return Err(invalid(
            "candidate does not bind the native request and admitted weights",
        ));
    }
    let tts = document(&packet, "tts_document_json", "tts_document_sha256")?;
    let spoken = document(&packet, "spoken_text_json", "spoken_text_sha256")?;
    let value = decode(&tts, CUE_DOCUMENT_LIMIT)?;
    let input = decode(request.raw(), 32768)?;
    let mut provenance = input["producer_pin"].clone();
    provenance
        .as_object_mut()
        .ok_or_else(|| invalid("missing native producer pin"))?
        .insert("input_hash".into(), request.input_hash().into());
    let artifacts = value["artifacts"]
        .as_array()
        .filter(|a| a.len() == 1)
        .ok_or_else(|| invalid("candidate must contain exactly one artifact"))?;
    let artifact = &artifacts[0];
    let segment = &input["tts_input"];
    if !matches!(value["schema_version"].as_u64(), Some(1 | 2))
        || value["provenance"] != provenance
        || value["source_segment_ids"] != json!([input["cue_id"]])
        || value["failures"] != json!([])
        || artifact["segment_id"] != input["cue_id"]
        || artifact["source_utterance_id"] != segment["source_utterance_id"]
        || artifact["slot_start"] != segment["start"]
        || artifact["slot_end"] != segment["end"]
        || artifact["render_window_end"] != segment["render_window_end"]
        || artifact["confidence"].as_f64().map(f64::to_bits)
            != segment["confidence"].as_f64().map(f64::to_bits)
        || artifact["fallback_used"] != false
        || artifact["sample_rate"] != input["config"]["sample_rate"]
        || artifact["channels"] != input["config"]["channels"]
        || artifact["bits_per_sample"] != 16
        || artifact["format"] != "wav"
    {
        return Err(invalid(
            "candidate substitutes native producer, cue, timing, voice or PCM pins",
        ));
    }
    let path = PathBuf::from(
        artifact["path"]
            .as_str()
            .ok_or_else(|| invalid("candidate lacks a waveform path"))?,
    );
    let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
    if path.parent() != Some(generation.join("tts").as_path())
        || name.len() != 40
        || !name.starts_with("tts-")
        || !name.ends_with(".wav")
        || !name[4..36]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid(
            "candidate waveform escapes its native private generation",
        ));
    }
    let file = super::review_assets::regular(&path)?;
    let pin = PcmPin {
        sha256: artifact["content_hash"]
            .as_str()
            .ok_or_else(|| invalid("candidate waveform lacks a hash"))?
            .into(),
        size_bytes: file.metadata()?.len(),
        sample_rate: artifact["sample_rate"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| invalid("bad cue sample rate"))?,
        channels: artifact["channels"]
            .as_u64()
            .and_then(|n| u16::try_from(n).ok())
            .ok_or_else(|| invalid("bad cue channels"))?,
        frame_count: artifact["frame_count"]
            .as_u64()
            .ok_or_else(|| invalid("bad cue frame count"))?,
    };
    if pin.size_bytes
        > input["config"]["max_wave_bytes"]
            .as_u64()
            .ok_or_else(|| invalid("missing waveform bound"))?
    {
        return Err(invalid("candidate waveform exceeds the pinned config"));
    }
    Ok(CandidateDocuments {
        tts,
        spoken,
        recipe: recipe.into(),
        path,
        pin,
    })
}

fn original_audio_root(graph: &Value) -> SupervisorResult<PathBuf> {
    // Only a native-admitted original graph reaches this helper. Its descriptor
    // matches the completed export and immutable start's output binding.
    let document = PathBuf::from(
        graph["documents"]["tts_document"]["path"]
            .as_str()
            .ok_or_else(|| invalid("missing original export root"))?,
    );
    let root = document
        .parent()
        .ok_or_else(|| invalid("missing export parent"))?;
    if !root.is_absolute()
        || root
            .components()
            .any(|p| matches!(p, std::path::Component::ParentDir))
    {
        return Err(invalid(
            "original output root is not an admitted absolute path",
        ));
    }
    super::review_pin::plain(root)?;
    if !root.is_dir() {
        return Err(invalid(
            "original output root unavailable; restore it before Review",
        ));
    }
    Ok(root.to_path_buf())
}

pub(super) fn private_root(output: &Path, job: &str) -> SupervisorResult<PathBuf> {
    if !output.is_absolute()
        || output
            .components()
            .any(|p| matches!(p, std::path::Component::ParentDir))
    {
        return Err(invalid(
            "Review candidates require the admitted original output root",
        ));
    }
    super::review_pin::plain(output)?;
    if !output.is_dir() {
        return Err(invalid(
            "Review output storage unavailable; never fall back to control storage",
        ));
    }
    // Full-length WAVs stay on the output volume; control keeps bounded sealed
    // metadata/cue objects. Missing output storage remains an explicit refusal.
    let root = output.join(".dubflow-review-candidates");
    let job_root = root.join(format!("{:x}", Sha256::digest(job.as_bytes())));
    // Selection is pure and precedes mkdir. All NEW scratch/credit IO is
    // deferred to the collector, which owns the caller's actual deadline.
    let scratch = select_scratch(output, cfg!(windows))?;
    let mut paths = vec![root.clone(), job_root.clone()];
    if scratch == root.join("temp") {
        paths.push(scratch);
    }
    for path in &paths {
        super::review_pin::plain(path.parent().unwrap())?;
        match std::fs::create_dir(path) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(e.into()),
        }
        super::review_pin::plain(path)?;
        if !path.is_dir() {
            return Err(invalid("private cue root is not a plain directory"));
        }
    }
    Ok(job_root)
}

// SCRATCH_LAYOUT_BEGIN: exact production placement used by the standalone probe.
fn select_scratch(output: &Path, windows: bool) -> SupervisorResult<PathBuf> {
    let legacy = output.join(".dubflow-review-candidates").join("temp");
    if !windows || legacy.as_os_str().to_string_lossy().encode_utf16().count() <= 200 {
        return Ok(legacy);
    }
    let compact = output.join(".dfr").join("temp");
    validate_scratch_length(&compact, windows)?;
    Ok(compact)
}

fn validate_scratch_length(scratch: &Path, windows: bool) -> SupervisorResult<()> {
    // Leave room for TemporaryDirectory's prefix/random suffix and PCM names.
    // A deep TEMP reproduced a hang inside Windows CreateProcess, before the
    // child could enter our protocol or observe its inference timeout.
    if windows && scratch.as_os_str().to_string_lossy().encode_utf16().count() > 200 {
        return Err(invalid(
            "Review temporary path is too long for Windows process startup; create a new processing version in a shorter output folder; prior export remains unchanged",
        ));
    }
    Ok(())
}
// SCRATCH_LAYOUT_END

pub(super) fn candidate_scratch(job_root: &Path) -> SupervisorResult<PathBuf> {
    // NativeProcess creates unique private TemporaryDirectories here. Sharing
    // the short parent across jobs does not share media/checkpoint identity.
    let parent = job_root
        .parent()
        .ok_or_else(|| invalid("missing native candidate parent"))?;
    if parent.file_name().and_then(|s| s.to_str()) != Some(".dubflow-review-candidates") {
        return Err(invalid(
            "candidate root is not the legacy admitted namespace",
        ));
    }
    let output = parent
        .parent()
        .ok_or_else(|| invalid("missing admitted output"))?;
    let scratch = select_scratch(output, cfg!(windows))?;
    #[cfg(not(windows))]
    {
        // Windows defers a fresh native scratch admission to its collector.
        // Other platforms keep the original per-call plain/existence fence.
        super::review_pin::plain(&scratch)?;
        if !scratch.is_dir() {
            return Err(invalid("Review temporary storage is unavailable"));
        }
    }
    Ok(scratch)
}

pub(super) fn cue_command(
    runtime: &AdmittedRuntime,
    entry: &Path,
    scratch: &Path,
) -> SupervisorResult<super::review_retention::OwnedReviewCommand> {
    validate_scratch_length(scratch, cfg!(windows))?;
    let mut command = Command::new(&runtime.python);
    command.args([
        OsString::from("-I"),
        OsString::from("-S"),
        OsString::from("-B"),
        entry.as_os_str().to_owned(),
    ]);
    command
        .env("TEMP", scratch)
        .env("TMP", scratch)
        .env("TMPDIR", scratch);
    #[cfg(windows)]
    {
        super::review_retention::own_scratch_command(command, scratch)
    }
    #[cfg(not(windows))]
    {
        Ok(command.into())
    } // No process-group/credit retirement claim on this platform.
}

pub(super) fn synthesize_accepted(
    runtime: &AdmittedRuntime,
    assets: &AcceptedAudioStore,
    output: &Path,
    request: &BoundCueRequest,
    object: &AcceptedJsonDocument,
    retained_output: Option<&super::review_retention::NativeOutputPin>,
    expected_recipe: Option<&str>,
) -> SupervisorResult<Value> {
    let (entry, model_root) = runtime.cue_execution()?;
    let scratch = candidate_scratch(output)?;
    assets.read_json(object)?;
    let mut command = cue_command(runtime, &entry, &scratch)?;
    command.args([
        OsString::from("produce"),
        OsString::from("--request"),
        object.path.as_os_str().to_owned(),
        OsString::from("--request-sha256"),
        OsString::from(request.input_hash()),
        OsString::from("--app-root"),
        runtime.root.join("app").into_os_string(),
        OsString::from("--model-root"),
        model_root.into_os_string(),
        OsString::from("--profile"),
        runtime.profile.as_os_str().to_owned(),
        OsString::from("--ffmpeg"),
        runtime.ffmpeg.as_os_str().to_owned(),
        OsString::from("--output-root"),
        output.as_os_str().to_owned(),
    ]);
    let raw = collect(command, Duration::from_secs(300), CUE_PACKET_LIMIT)?;
    let generation = output.join(format!("cue-{}", &request.input_hash()[7..]));
    let candidate = admit_candidate(&raw, request, &generation)?;
    if expected_recipe.is_some_and(|recipe| recipe != candidate.recipe) {
        return Err(invalid(
            "candidate recipe differs from admitted processing synthesis",
        ));
    }
    let job = request.raw();
    let request_value = decode(job, CUE_DOCUMENT_LIMIT)?;
    let job = request_value["job_id"]
        .as_str()
        .ok_or_else(|| invalid("retained cue job missing"))?;
    let budget = super::review_assets::MixStorageBudget {
        maximum_bytes: 32 * 1024 * 1024 * 1024,
        minimum_free_bytes: 2 * 1024 * 1024 * 1024,
    };
    let retained = if let Some(output_pin) = retained_output {
        let media = super::review_retention::NativeMixStore::new(output_pin, job, budget)?;
        Some(media.accept_cue(
            &generation,
            &candidate.path,
            &candidate.pin,
            std::time::Instant::now() + Duration::from_secs(180),
        )?)
    } else {
        None
    };
    let audio = if let Some(retained) = &retained {
        super::review_assets::AcceptedCueAudio {
            path: retained.path().into(),
            pin: retained.pin().clone(),
        }
    } else {
        assets.accept(&generation, &candidate.path, &candidate.pin)?
    };
    let verify_audio = || -> SupervisorResult<()> {
        if let (Some(output_pin), Some(retained)) = (retained_output, &retained) {
            let media =
                super::review_retention::NativeMixStore::open_existing(output_pin, job, budget)?;
            let reopened = media.reopen_cue(
                &retained.descriptor(),
                std::time::Instant::now() + Duration::from_secs(180),
            )?;
            if reopened.path() != audio.path.as_path() || reopened.pin() != &audio.pin {
                return Err(invalid("retained cue differs from its native accepted PCM"));
            }
            Ok(())
        } else {
            assets.verify(&audio)
        }
    };
    let wave = json!({"path":audio.path,"root":audio.path.parent().unwrap(),"sha256":audio.pin.sha256,"size_bytes":audio.pin.size_bytes,
        "sample_rate":audio.pin.sample_rate,"channels":audio.pin.channels,"frame_count":audio.pin.frame_count});
    let context=assets.accept_json(&serde_json::to_string(&json!({"schema_version":1,"kind":"review_native_accepted_cue_context",
        "request_json":request.raw(),"request_sha256":request.input_hash(),"candidate_tts_json":candidate.tts,
        "candidate_spoken_text_json":candidate.spoken,"recipe_identity":candidate.recipe,"accepted_wave":wave}))?)?;
    let mut command = cue_command(runtime, &entry, &scratch)?;
    command.args([
        OsString::from("accept"),
        OsString::from("--context"),
        context.path.as_os_str().to_owned(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
    ]);
    let raw = collect(command, Duration::from_secs(60), CUE_PACKET_LIMIT)?;
    let packet = decode(&raw, CUE_PACKET_LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "request_sha256",
            "tts_document_json",
            "tts_document_sha256",
            "spoken_text_json",
            "spoken_text_sha256",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_accepted_cue_documents"
        || packet["request_sha256"] != request.input_hash()
    {
        return Err(invalid("accepted cue documents differ from native request"));
    }
    let tts = document(&packet, "tts_document_json", "tts_document_sha256")?;
    let spoken = document(&packet, "spoken_text_json", "spoken_text_sha256")?;
    let mut expected_tts = decode(&candidate.tts, CUE_DOCUMENT_LIMIT)?;
    expected_tts["artifacts"][0]["path"] = audio.path.to_string_lossy().into_owned().into();
    let mut expected_spoken = decode(&candidate.spoken, CUE_DOCUMENT_LIMIT)?;
    expected_spoken["tts_document_hash"] = digest(&tts).into();
    let exact = |value: &Value| -> SupervisorResult<String> {
        let mut raw = String::new();
        super::review_document::canonical(value, &mut raw)?;
        Ok(raw)
    };
    if exact(&decode(&tts, CUE_DOCUMENT_LIMIT)?)? != exact(&expected_tts)?
        || exact(&decode(&spoken, CUE_DOCUMENT_LIMIT)?)? != exact(&expected_spoken)?
    {
        return Err(invalid(
            "accepted adapter changed fields beyond WAV path/document hash",
        ));
    }
    verify_audio()?;
    assets.read_json(object)?;
    assets.read_json(&context)?;
    let tts = assets.accept_json(&tts)?;
    let spoken = assets.accept_json(&spoken)?;
    assets.read_json(&tts)?;
    assets.read_json(&spoken)?;
    verify_audio()?;
    let mut result = json!({"audio":wave,"tts_document":descriptor(&tts),"spoken_text":descriptor(&spoken),"recipe_identity":candidate.recipe,
        "accept_context":descriptor(&context),"active_graph_published":false});
    if let Some(retained) = retained {
        result
            .as_object_mut()
            .unwrap()
            .insert("retained_cue_audio".into(), retained.descriptor());
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidates_bind_original_output_storage_and_do_not_fall_back_to_control() {
        let base = std::env::temp_dir().join(format!(
            "dubflow review output placement {} {}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let output = base.join("original media output");
        let control = base.join("local control");
        std::fs::create_dir_all(&output).unwrap();
        std::fs::create_dir(&control).unwrap();
        let original = output.join("original.bin");
        std::fs::write(&original, b"preserve original bytes").unwrap();
        let before = std::fs::metadata(&original).unwrap().modified().unwrap();
        let audio = output.join(".dubflow-work/b2-audio/producer/config");
        std::fs::create_dir_all(&audio).unwrap();
        let graph = json!({"documents":{"tts_document":{"path":audio.join("tts_document.json")}}});
        assert_eq!(original_audio_root(&graph).unwrap(), audio);
        let candidate = private_root(&output, "job-placement").unwrap();
        assert!(candidate.starts_with(&output));
        assert!(!candidate.starts_with(&audio));
        assert_eq!(std::fs::read_dir(&audio).unwrap().count(), 0);
        assert!(candidate_scratch(&candidate).unwrap().is_dir());
        assert_eq!(
            candidate_scratch(&candidate).unwrap(),
            candidate.parent().unwrap().join("temp")
        );
        assert!(!candidate.starts_with(&control));
        assert_eq!(std::fs::read_dir(&control).unwrap().count(), 0);
        assert_eq!(private_root(&output, "job-placement").unwrap(), candidate);
        assert_ne!(private_root(&output, "other-job").unwrap(), candidate);
        assert_eq!(
            candidate_scratch(&private_root(&output, "other-job").unwrap()).unwrap(),
            candidate_scratch(&candidate).unwrap()
        );
        assert_eq!(
            std::fs::read(&original).unwrap(),
            b"preserve original bytes"
        );
        assert_eq!(
            std::fs::metadata(&original).unwrap().modified().unwrap(),
            before
        );
        let missing = base.join("unavailable output");
        assert!(private_root(&missing, "job-placement").is_err());
        assert!(!missing.exists());
        assert!(
            original_audio_root(
                &json!({"documents":{"tts_document":{"path":missing.join("tts_document.json")}}})
            )
            .is_err()
        );
        assert!(
            original_audio_root(
                &json!({"documents":{"tts_document":{"path":"relative/tts_document.json"}}})
            )
            .is_err()
        );
        let traversal = output.join("..").join("local control");
        assert!(private_root(&traversal, "job-placement").is_err());
        assert!(private_root(&original, "job-placement").is_err());
        assert_eq!(std::fs::read_dir(&control).unwrap().count(), 0);
        assert!(
            base.canonicalize()
                .unwrap()
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
        );
        std::fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn windows_scratch_guard_counts_utf16_and_refuses_before_startup() {
        assert!(validate_scratch_length(Path::new(&"a".repeat(200)), true).is_ok());
        assert!(validate_scratch_length(Path::new(&"a".repeat(201)), true).is_err());
        assert!(validate_scratch_length(Path::new(&"\u{1f3ac}".repeat(100)), true).is_ok());
        assert!(validate_scratch_length(Path::new(&"\u{1f3ac}".repeat(101)), true).is_err());
        assert!(validate_scratch_length(Path::new(&"a".repeat(300)), false).is_ok());
    }

    #[test]
    fn windows_default_output_uses_only_compact_scratch_without_changing_candidates() {
        let output = PathBuf::from(format!("C:/{}", "o".repeat(176)));
        assert_eq!(
            output.as_os_str().to_string_lossy().encode_utf16().count(),
            179
        );
        assert_eq!(
            select_scratch(&output, true).unwrap(),
            output.join(".dfr/temp")
        );
        let extended = PathBuf::from(format!("\\\\?\\{}", output.display()));
        assert_eq!(
            select_scratch(&extended, true)
                .unwrap()
                .as_os_str()
                .to_string_lossy()
                .encode_utf16()
                .count(),
            193
        );
        let short = PathBuf::from("C:/short-output");
        assert_eq!(
            select_scratch(&short, true).unwrap(),
            short.join(".dubflow-review-candidates/temp")
        );
        assert!(select_scratch(&PathBuf::from(format!("C:/{}", "o".repeat(190))), true).is_err());
        assert!(select_scratch(&PathBuf::from(format!("C:/{}", "🎬".repeat(100))), true).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn legacy_scratch_revalidates_late_missing_file_and_symlink() {
        let base = std::env::temp_dir().join(format!(
            "dubflow-late-scratch-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let output = base.join("output");
        std::fs::create_dir_all(&output).unwrap();
        let candidate = private_root(&output, "late-scratch").unwrap();
        let scratch = candidate.parent().unwrap().join("temp");
        assert_eq!(candidate_scratch(&candidate).unwrap(), scratch);
        std::fs::remove_dir(&scratch).unwrap();
        assert!(candidate_scratch(&candidate).is_err());
        std::fs::write(&scratch, b"not a directory").unwrap();
        assert!(candidate_scratch(&candidate).is_err());
        assert_eq!(std::fs::read(&scratch).unwrap(), b"not a directory");
        std::fs::remove_file(&scratch).unwrap();
        let foreign = base.join("foreign");
        std::fs::create_dir(&foreign).unwrap();
        std::fs::write(foreign.join("preserve.bin"), b"foreign unchanged").unwrap();
        std::os::unix::fs::symlink(&foreign, &scratch).unwrap();
        assert!(candidate_scratch(&candidate).is_err());
        assert_eq!(
            std::fs::read(foreign.join("preserve.bin")).unwrap(),
            b"foreign unchanged"
        );
        std::fs::remove_file(&scratch).unwrap();
        assert!(
            base.canonicalize()
                .unwrap()
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
        );
        std::fs::remove_dir_all(base).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn oversized_windows_output_refuses_before_creating_private_storage() {
        let base = std::env::temp_dir().join(format!(
            "dubflow-long-scratch-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let output = base.join("o".repeat(170));
        std::fs::create_dir_all(&output).unwrap();
        let error = private_root(&output, "long-output-job").unwrap_err();
        assert!(error.to_string().contains("shorter output folder"));
        assert_eq!(std::fs::read_dir(&output).unwrap().count(), 0);
        assert!(
            base.canonicalize()
                .unwrap()
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
        );
        std::fs::remove_dir_all(base).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn linked_output_storage_is_refused_before_candidate_creation() {
        let base = std::env::temp_dir().join(format!(
            "dubflow review linked output {} {}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let output = base.join("actual output");
        std::fs::create_dir_all(&output).unwrap();
        let link = base.join("linked output");
        std::os::unix::fs::symlink(&output, &link).unwrap();
        assert!(private_root(&link, "job-placement").is_err());
        assert!(
            original_audio_root(
                &json!({"documents":{"tts_document":{"path":link.join("tts_document.json")}}})
            )
            .is_err()
        );
        assert_eq!(std::fs::read_dir(&output).unwrap().count(), 0);
        assert!(
            base.canonicalize()
                .unwrap()
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
        );
        std::fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn private_mix_metadata_binds_actual_python_packet_and_all_unrelated_assignments() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../tests/review/native_mix_candidate_v1.json"
        ))
        .unwrap();
        let context = &fixture["context"];
        let template = &fixture["template"];
        let hash = fixture["context_sha256"].as_str().unwrap();
        let good = &fixture["packet"];
        admit_mix_metadata(&good.to_string(), context, hash, template).unwrap();
        for case in 0..8 {
            let mut bad = good.clone();
            if case == 0 {
                bad["context_sha256"] = "sha256:foreign".into();
            } else if case == 1 {
                bad["active_graph_published"] = true.into();
            } else if case == 2 {
                bad["schema_version"] = 2.into();
            } else if case < 7 {
                let mut composition =
                    decode(bad["composition_json"].as_str().unwrap(), LIMIT).unwrap();
                match case {
                    3 => composition["source"]["sha256"] = "sha256:foreign".into(),
                    4 => composition["cues"][0]["document_sha256"] = "sha256:foreign".into(),
                    5 => composition["review_snapshot_sha256"] = "sha256:foreign".into(),
                    _ => composition["mix_producer_pin"]["runtime_id"] = "foreign".into(),
                }
                let raw = composition.to_string();
                bad["composition_sha256"] = digest(&raw).into();
                bad["composition_json"] = raw.into();
            } else {
                let mut mix = decode(bad["mix_document_json"].as_str().unwrap(), LIMIT).unwrap();
                mix["segments"][0]["status"] = "failed".into();
                let raw = mix.to_string();
                bad["mix_document_sha256"] = digest(&raw).into();
                bad["mix_document_json"] = raw.into();
            }
            assert!(
                admit_mix_metadata(&bad.to_string(), context, hash, template).is_err(),
                "case{case}"
            );
        }
    }
    #[test]
    fn original_cue_arguments_require_complete_token_and_never_accept_browser_paths() {
        for args in [
            vec![],
            vec!["--manifest", "outside"],
            vec!["--request", "outside"],
            vec!["--root"],
            vec!["--job-id", "a", "--job-id", "b"],
            vec!["--revision", "00"],
        ] {
            assert!(run_synthesis(args.iter().map(|arg| OsString::from(*arg))).is_err());
            assert!(run_mix(args.iter().map(|arg| OsString::from(*arg))).is_err());
            assert!(run(args.into_iter().map(OsString::from)).is_err());
        }
    }

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/review/native_cue_request_v1.json"
        ))
        .unwrap()
    }
    fn snapshot(value: &Value) -> AdmittedSnapshot {
        AdmittedSnapshot::read(
            &value.to_string(),
            value["job_id"].as_str().unwrap(),
            value["revision"].as_u64().unwrap(),
        )
        .unwrap()
    }
    fn context() -> AcceptedJsonDocument {
        AcceptedJsonDocument {
            path: PathBuf::from("unused-test-context.json"),
            sha256: format!("sha256:{}", "a".repeat(64)),
            size_bytes: 1,
        }
    }
    fn packet(f: &Value, case: &Value) -> Value {
        json!({"schema_version":1,"kind":"review_prepared_cue_request","context_sha256":context().sha256,
            "request_json":case["request_utf8"],"request_sha256":case["input_hash"],"config":f["config"],"voice":case["voice"],"model_weights_verified":false})
    }
    fn admit(value: &Value, f: &Value, case: &Value) -> SupervisorResult<BoundCueRequest> {
        let graph = json!({"canonical_inputs":[f["original_input"]],"tts_provenance":f["previous_provenance"]});
        admit_prepared(
            &value.to_string(),
            &context(),
            &snapshot(&f["before"]),
            &snapshot(&case["after"]),
            &graph,
            &case["voice"],
            f["manifest_sha256"].as_str().unwrap(),
        )
    }

    #[test]
    fn prepared_boundary_retains_actual_python_request_bytes_and_native_original_pins() {
        let f = fixture();
        for case in f["cases"].as_array().unwrap() {
            let result = admit(&packet(&f, case), &f, case).unwrap();
            assert_eq!(result.raw(), case["request_utf8"].as_str().unwrap());
            assert_eq!(result.input_hash(), case["input_hash"]);
        }
    }

    #[test]
    fn prepared_boundary_refuses_substituted_context_catalog_config_hash_and_weight_claim() {
        let f = fixture();
        let case = &f["cases"][0];
        for modify in 0..6 {
            let mut value = packet(&f, case);
            match modify {
                0 => value["context_sha256"] = format!("sha256:{}", "b".repeat(64)).into(),
                1 => value["voice"]["display_name"] = "unbound catalog value".into(),
                2 => value["config"]["resource"]["max_threads"] = 3.into(),
                3 => value["request_sha256"] = format!("sha256:{}", "b".repeat(64)).into(),
                4 => value["model_weights_verified"] = true.into(),
                _ => value["schema_version"] = 2.into(),
            }
            assert!(admit(&value, &f, case).is_err(), "case{modify}");
        }
    }

    #[test]
    fn candidate_admission_and_native_copy_bind_exact_cue_and_private_waveform() {
        let mut f = fixture();
        let mut case = f["cases"][0].clone();
        // Independently admitted signed zero must survive the candidate fence;
        // Value equality alone treats the substituted positive zero as equal.
        f["original_input"]["confidence"] = json!(-0.0);
        let mut cue = decode(case["request_utf8"].as_str().unwrap(), 32768).unwrap();
        cue["tts_input"]["confidence"] = json!(-0.0);
        let mut raw = String::new();
        super::super::review_document::canonical(&cue, &mut raw).unwrap();
        case["request_utf8"] = json!(raw);
        case["input_hash"] = json!(digest(&raw));
        let request = admit(&packet(&f, &case), &f, &case).unwrap();
        let input = decode(request.raw(), 32768).unwrap();
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-cue-candidate-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir_all(root.join("control")).unwrap();
        let generation = root.join("private generation");
        std::fs::create_dir_all(generation.join("tts")).unwrap();
        let path = generation
            .join("tts")
            .join(format!("tts-{}.wav", "a".repeat(32)));
        let rate = input["config"]["sample_rate"].as_u64().unwrap() as u32;
        let channels = input["config"]["channels"].as_u64().unwrap() as u16;
        let frames = 16u64;
        let size = frames as usize * channels as usize * 2;
        let mut wave = b"RIFF".to_vec();
        wave.extend_from_slice(&(36 + size as u32).to_le_bytes());
        wave.extend_from_slice(b"WAVEfmt \x10\0\0\0\x01\0");
        wave.extend_from_slice(&channels.to_le_bytes());
        wave.extend_from_slice(&rate.to_le_bytes());
        wave.extend_from_slice(&(rate * u32::from(channels) * 2).to_le_bytes());
        wave.extend_from_slice(&(channels * 2).to_le_bytes());
        wave.extend_from_slice(&16u16.to_le_bytes());
        wave.extend_from_slice(b"data");
        wave.extend_from_slice(&(size as u32).to_le_bytes());
        wave.extend(std::iter::repeat(1u8).take(size));
        std::fs::write(&path, &wave).unwrap();
        let mut provenance = input["producer_pin"].clone();
        provenance["input_hash"] = request.input_hash().into();
        let segment = &input["tts_input"];
        let tts = json!({"schema_version":2,"provenance":provenance,"source_segment_ids":[input["cue_id"]],"failures":[],
            "artifacts":[{"segment_id":input["cue_id"],"source_utterance_id":segment["source_utterance_id"],"slot_start":segment["start"],"slot_end":segment["end"],
            "render_window_end":segment["render_window_end"],"confidence":segment["confidence"],"fallback_used":false,"sample_rate":rate,"channels":channels,
            "bits_per_sample":16,"format":"wav","path":path,"frame_count":frames,"content_hash":format!("sha256:{:x}",Sha256::digest(&wave))}]});
        let mut candidate = json!({"schema_version":1,"kind":"review_cue_candidate","request_sha256":request.input_hash(),"recipe_identity":"a".repeat(64),
            "tts_document_json":tts.to_string(),"tts_document_sha256":digest(&tts.to_string()),"spoken_text_json":"{}","spoken_text_sha256":digest("{}"),"model_weights_verified":true});
        let admitted = admit_candidate(&candidate.to_string(), &request, &generation).unwrap();
        let mut changed = candidate.clone();
        let mut substituted = tts.clone();
        substituted["artifacts"][0]["confidence"] = json!(0.0);
        changed["tts_document_json"] = json!(substituted.to_string());
        changed["tts_document_sha256"] = json!(digest(&substituted.to_string()));
        assert!(admit_candidate(&changed.to_string(), &request, &generation).is_err());
        let assets = AcceptedAudioStore::new(&root, "job-native-candidate").unwrap();
        let accepted = assets
            .accept(&generation, &admitted.path, &admitted.pin)
            .unwrap();
        assets.verify(&accepted).unwrap();
        for change in 0..7 {
            let mut changed = candidate.clone();
            match change {
                0 => changed["request_sha256"] = format!("sha256:{}", "c".repeat(64)).into(),
                1 => changed["model_weights_verified"] = false.into(),
                2 => changed["tts_document_sha256"] = format!("sha256:{}", "c".repeat(64)).into(),
                3 => changed["recipe_identity"] = "unbound".into(),
                _ => {
                    let mut data = tts.clone();
                    match change {
                        4 => {
                            data["provenance"]["model_hash"] =
                                format!("sha256:{}", "c".repeat(64)).into()
                        }
                        5 => data["artifacts"][0]["source_utterance_id"] = "another-source".into(),
                        _ => {
                            data["artifacts"][0]["path"] = root
                                .join("outside.wav")
                                .to_string_lossy()
                                .into_owned()
                                .into()
                        }
                    }
                    changed["tts_document_json"] = data.to_string().into();
                    changed["tts_document_sha256"] = digest(&data.to_string()).into();
                }
            }
            assert!(
                admit_candidate(&changed.to_string(), &request, &generation).is_err(),
                "case{change}"
            );
        }
        candidate["spoken_text_json"] = "{\"duplicate\":1,\"duplicate\":2}".into();
        candidate["spoken_text_sha256"] =
            digest(candidate["spoken_text_json"].as_str().unwrap()).into();
        assert!(admit_candidate(&candidate.to_string(), &request, &generation).is_err());
        std::fs::remove_file(&path).unwrap();
        assets.verify(&accepted).unwrap();
        let mut permissions = std::fs::metadata(&accepted.path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        std::fs::set_permissions(&accepted.path, permissions).unwrap();
        assert!(root.starts_with(std::env::temp_dir()));
        std::fs::remove_dir_all(root).unwrap();
    }
}
