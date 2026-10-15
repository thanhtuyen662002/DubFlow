//! Opt-in current cue/timeline preparation and TTS/mix through an admitted
//! processing release. Original-only guards remain intact. Current render
//! dispatch is separate for B1/B2; cumulative graph CAS is not implemented here.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{canonical, closed, decode, tick, AdmittedSnapshot, ReviewMode};
use super::review_request::{BoundCueRequest, CueRequestPins};
use super::review_runtime::AdmittedProcessingRuntime;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Component, PathBuf};
use std::process::Command;
use std::time::Duration;

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(s: &str) -> SupervisorError {
    SupervisorError::Invalid(s.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        decode(
            include_str!("../../../../tests/review/native_cue_request_v1.json"),
            LIMIT,
        )
        .unwrap()
    }
    fn case() -> (Value, AdmittedSnapshot, AdmittedSnapshot, Value, Value) {
        let f = fixture();
        let mut before = f["before"].clone();
        before["revision"] = json!(2);
        let before =
            AdmittedSnapshot::read(&before.to_string(), before["job_id"].as_str().unwrap(), 2)
                .unwrap();
        let after = before
            .correction("finding-a", Some("Đừng cử động."), None, ReviewMode::Dubbed)
            .unwrap();
        let mut request = decode(f["cases"][0]["request_utf8"].as_str().unwrap(), 32768).unwrap();
        request["revision"] = json!("3");
        request["before_snapshot_sha256"] = json!(before.sha256());
        request["after_snapshot_sha256"] = json!(after.sha256());
        let token = json!({"job_id":before.job(),"revision":"2","snapshot_sha256":before.sha256(),
            "graph_sha256":format!("sha256:{}","c".repeat(64)),"original_manifest_sha256":f["manifest_sha256"]});
        let raw = exact(&request).unwrap();
        let packet = json!({"schema_version":1,"kind":"review_prepared_effective_cue_request",
            "context_sha256":format!("sha256:{}","d".repeat(64)),"before_token":token,
            "current_composition_sha256":format!("sha256:{}","e".repeat(64)),
            "composition_snapshot_sha256":format!("sha256:{}","f".repeat(64)),
            "effective_composition_json":"{\"current\":true}","effective_composition_sha256":digest("{\"current\":true}"),
            "request_json":raw,"request_sha256":digest(&raw),"config":f["config"],"voice":f["cases"][0]["voice"],
            "model_weights_verified":false,"active_graph_published":false});
        (f, before, after, token, packet)
    }
    fn bind(
        f: &Value,
        before: &AdmittedSnapshot,
        after: &AdmittedSnapshot,
        token: &Value,
        packet: &Value,
        input: &Value,
    ) -> SupervisorResult<BoundCueRequest> {
        admit_prepared(
            &packet.to_string(),
            before,
            after,
            CurrentCuePins {
                context_hash: &format!("sha256:{}", "d".repeat(64)),
                token,
                content_hash: &format!("sha256:{}", "e".repeat(64)),
                content_snapshot_hash: &format!("sha256:{}", "f".repeat(64)),
                projection: "{\"current\":true}",
                current_input: input,
                current_provenance: &f["previous_provenance"],
                target_voice: &f["cases"][0]["voice"],
            },
        )
    }
    #[test]
    fn current_preparation_keeps_restored_token_and_refuses_content_or_permission_substitution() {
        let (f, before, after, token, packet) = case();
        let bound = bind(&f, &before, &after, &token, &packet, &f["original_input"]).unwrap();
        assert_eq!(decode(bound.raw(), 32768).unwrap()["revision"], "3");
        assert_eq!(token["revision"], "2");
        for (field, value) in [
            ("before_token", json!({})),
            ("current_composition_sha256", json!("foreign")),
            ("composition_snapshot_sha256", json!("foreign")),
            ("effective_composition_json", json!("{\"lost\":true}")),
            ("model_weights_verified", json!(true)),
            ("active_graph_published", json!(true)),
            ("unknown", json!(true)),
        ] {
            let mut changed = packet.clone();
            changed[field] = value;
            assert!(bind(&f, &before, &after, &token, &changed, &f["original_input"]).is_err());
        }
    }
    #[test]
    fn current_request_keeps_independent_immutable_confidence_bits_including_signed_zero() {
        let (f, before, after, token, mut packet) = case();
        let mut input = f["original_input"].clone();
        input["confidence"] = json!(-0.0);
        let mut request = decode(packet["request_json"].as_str().unwrap(), 32768).unwrap();
        request["tts_input"]["confidence"] = json!(-0.0);
        let raw = exact(&request).unwrap();
        packet["request_json"] = json!(raw);
        packet["request_sha256"] = json!(digest(&raw));
        bind(&f, &before, &after, &token, &packet, &input).unwrap();
        request["tts_input"]["confidence"] = json!(0.0);
        let raw = exact(&request).unwrap();
        packet["request_json"] = json!(raw);
        packet["request_sha256"] = json!(digest(&raw));
        assert!(bind(&f, &before, &after, &token, &packet, &input).is_err());
    }

    fn mix_case() -> (Value, BoundCueRequest, Value, (Value, Value, Value)) {
        // Actual tiny Python streamed DSP metadata, with fixture speech and
        // simulated native roots. This is not physical native/undo evidence.
        let f = decode(
            include_str!("../../../../tests/review/native_effective_mix_v1.json"),
            LIMIT,
        )
        .unwrap();
        let before = AdmittedSnapshot::read(
            &f["before"].to_string(),
            f["before"]["job_id"].as_str().unwrap(),
            2,
        )
        .unwrap();
        let after = AdmittedSnapshot::read(&f["after"].to_string(), before.job(), 3).unwrap();
        let request = BoundCueRequest::read(
            f["request_json"].as_str().unwrap(),
            &before,
            &after,
            CueRequestPins {
                manifest_sha256: f["token"]["original_manifest_sha256"].as_str().unwrap(),
                original_input: &f["current"]["cues"][1]["tts_input"],
                previous_provenance: &f["previous_provenance"],
                config: &f["config"],
                target_voice: &f["voice"],
            },
        )
        .unwrap();
        let composition =
            replacement_composition(&f["current"], &request, &f["accepted_tts"]).unwrap();
        let docs: HashMap<String, Value> = serde_json::from_value(f["documents"].clone()).unwrap();
        let evidence = mix_evidence(&composition, &docs).unwrap();
        (f, request, composition, evidence)
    }

    #[test]
    fn current_mix_matches_independent_python_stream_metadata_without_losing_first_voice_edit() {
        let (f, request, composition, evidence) = mix_case();
        let (accepted, _) = admit_current_mix(
            &f["packet"].to_string(),
            f["packet"]["context_sha256"].as_str().unwrap(),
            &f["token"],
            &request,
            &composition,
            &evidence,
            &f["template"],
        )
        .unwrap();
        assert_eq!(
            decode(&accepted, LIMIT).unwrap()["cues"][0],
            f["current"]["cues"][0]
        );
        assert_eq!(f["before"]["revision"], 2);
        assert_eq!(f["after"]["revision"], 3);
        assert_eq!(
            f["before"]["findings"][0]["voice"],
            "vi-hai-dang-vieneu3-v1"
        );
    }

    #[test]
    fn checksummed_current_mix_refuses_stale_token_lost_prior_cue_and_changed_windows_confidence_or_chunks(
    ) {
        let (f, request, composition, evidence) = mix_case();
        for mutation in 0..10 {
            let mut packet = f["packet"].clone();
            let mut mixed = decode(packet["mix_document_json"].as_str().unwrap(), LIMIT).unwrap();
            if mutation == 0 {
                packet["before_token"]["revision"] = json!("0");
            } else if mutation == 1 {
                let mut current =
                    decode(packet["composition_json"].as_str().unwrap(), LIMIT).unwrap();
                current["cues"][0]["tts_input"]["text"] = json!("Lost earlier translation");
                let raw = exact(&current).unwrap();
                packet["composition_json"] = json!(raw);
                packet["composition_sha256"] = json!(digest(&raw));
                mixed["provenance"]["input_hash"] = packet["composition_sha256"].clone();
            } else {
                match mutation {
                    2 => mixed["duck_windows"][0]["end_sample"] = json!(1),
                    3 => mixed["segments"][0]["confidence"] = json!(0.9000000000000001),
                    4 => mixed["failures"] = json!([{"segment_id":"cue-a","code":"Invented"}]),
                    5 => mixed["chunks"][0]["segment_ids"] = json!(["cue-b"]),
                    6 => {
                        mixed["provenance"]["config_hash"] =
                            json!(format!("sha256:{}", "b".repeat(64)))
                    }
                    7 => packet["schema_version"] = json!(1.0),
                    8 => mixed["chunks"][0]["attempt"] = json!(1.0),
                    _ => {
                        mixed["original_audio"]["content_hash"] =
                            json!(format!("sha256:{}", "b".repeat(64)));
                        mixed["original_audio"]["metrics"]["content_hash"] =
                            mixed["original_audio"]["content_hash"].clone();
                    }
                }
            }
            let raw = exact(&mixed).unwrap();
            packet["mix_document_json"] = json!(raw);
            packet["mix_document_sha256"] = json!(digest(&raw));
            assert!(
                admit_current_mix(
                    &packet.to_string(),
                    f["packet"]["context_sha256"].as_str().unwrap(),
                    &f["token"],
                    &request,
                    &composition,
                    &evidence,
                    &f["template"]
                )
                .is_err(),
                "mutation {mutation}"
            );
        }
    }
}

fn digest(s: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(s.as_bytes()))
}
fn exact(value: &Value) -> SupervisorResult<String> {
    let mut s = String::new();
    canonical(value, &mut s)?;
    Ok(s)
}
fn unchanged(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("current cue generation changed; reload"));
    }
    Ok(())
}

pub(super) fn run_timeline(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_timeline_inner(args, false)
}

pub(super) fn run_render_b1(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_timeline_inner(args, true)
}

fn run_timeline_inner(
    mut args: impl Iterator<Item = OsString>,
    render: bool,
) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("timeline flag is not UTF-8"))?;
        if ![
            "--data-root",
            "--job-id",
            "--revision",
            "--snapshot-sha256",
            "--graph-sha256",
            "--finding-id",
            "--translation",
            "--voice",
            "--processing-root",
            "--processing-manifest-sha256",
            "--processing-manifest-size-bytes",
            "--processing-source-sha",
        ]
        .contains(&flag.as_str())
            || values.contains_key(&flag)
        {
            return Err(invalid("unknown or duplicate current timeline argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("timeline argument value missing"))?
            .into_string()
            .map_err(|_| invalid("timeline value is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values.get(key).map(String::as_str).ok_or_else(|| {
            invalid("current timeline requires complete token and processing selection")
        })
    };
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|p| matches!(p, Component::ParentDir)) {
        return Err(invalid("current timeline data root must be absolute"));
    }
    super::review_pin::plain(&data)?;
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _guard = super::JobExecutionGuard::acquire(&db, job)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("current timeline graph missing"))?;
    if state.token.revision != tick(&Value::String(required("--revision")?.into()), false)?
        || state.token.snapshot_sha256 != required("--snapshot-sha256")?
        || state.token.graph_sha256 != required("--graph-sha256")?
    {
        return Err(invalid("current timeline token changed; reload"));
    }
    let admitted = super::review_effective::read_current(&store, &data, &state)?;
    let view = admitted.packet()?;
    let archive = super::review_seed::read_original(&store, &data, admitted.origin())?;
    let mode_value = decode(&archive.graph, LIMIT)?["mode"].clone();
    let mode = match mode_value.as_str() {
        Some("b1") => ReviewMode::Subtitles,
        Some("b2") => ReviewMode::Dubbed,
        _ => return Err(invalid("current timeline original mode invalid")),
    };
    let before = admitted.snapshot();
    let after = before.correction(
        required("--finding-id")?,
        values.get("--translation").map(String::as_str),
        values.get("--voice").map(String::as_str),
        mode,
    )?;
    let assets = AcceptedAudioStore::open_existing(&data, job)?;
    let origin = decode(&admitted.origin().graph_json, LIMIT)?;
    let original_object =
        accepted_json(&origin["accepted_documents"]["editable_timeline"], &assets)?;
    let original = assets.read_json(&original_object)?;
    let current_object = accepted_json(&view["metadata_pins"]["current_timeline"], &assets)?;
    let current = assets.read_json(&current_object)?;
    let expected = super::review_timeline::derive_effective(
        &original,
        &archive.snapshot,
        &current,
        before,
        &after,
        mode,
    )?;
    let processing = AdmittedProcessingRuntime::read(
        &store,
        job,
        &PathBuf::from(required("--processing-root")?),
        &json!({"sha256":required("--processing-manifest-sha256")?,
            "size_bytes":tick(&Value::String(required("--processing-manifest-size-bytes")?.into()),false)?}),
        required("--processing-source-sha")?,
    )?;
    let receipt = processing.effective_timeline_receipt()?;
    if render {
        if mode_value != "b1" {
            return Err(invalid("B1 current-render command cannot downgrade B2"));
        }
        processing.effective_render_receipt(None)?;
    }
    let context=assets.accept_json(&json!({"schema_version":1,"kind":"review_native_effective_timeline_context",
        "mode":mode_value,"current_token":view["current_token"],"origin_snapshot_json":archive.snapshot.json(),
        "before_snapshot_json":before.json(),"after_snapshot_json":after.json(),
        "original_timeline_json":original,"original_timeline_sha256":original_object.sha256,
        "current_timeline_json":current,"current_timeline_sha256":current_object.sha256}).to_string())?;
    unchanged(&store, &state)?;
    let mut command = Command::new(&processing.runtime().python);
    command.args([
        OsString::from("-I"),
        OsString::from("-S"),
        OsString::from("-B"),
        OsString::from(
            receipt["entry"]["path"]
                .as_str()
                .ok_or_else(|| invalid("timeline entry missing"))?,
        ),
        OsString::from("--context"),
        context.path.as_os_str().to_owned(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
    ]);
    let raw = super::review_seed::collect(command, Duration::from_secs(60), LIMIT)?;
    let packet = decode(&raw, LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "mode",
            "current_token",
            "after_snapshot_sha256",
            "current_timeline_sha256",
            "corrected_timeline_json",
            "corrected_timeline_sha256",
            "model_weights_verified",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_prepared_effective_timeline"
        || packet["context_sha256"] != context.sha256
        || packet["mode"] != mode_value
        || exact(&packet["current_token"])? != exact(&view["current_token"])?
        || packet["after_snapshot_sha256"] != after.sha256()
        || packet["current_timeline_sha256"] != current_object.sha256
        || packet["corrected_timeline_sha256"]
            != digest(packet["corrected_timeline_json"].as_str().unwrap_or(""))
        || packet["model_weights_verified"] != false
        || packet["active_graph_published"] != false
        || packet["correction_ready"] != false
    {
        return Err(invalid(
            "timeline adapter substitutes native identity or claims authority",
        ));
    }
    let corrected = packet["corrected_timeline_json"]
        .as_str()
        .ok_or_else(|| invalid("corrected timeline bytes missing"))?;
    let unchanged_text = before.finding(after.plan_finding_id()?)?["translated_text"]
        == after.finding(after.plan_finding_id()?)?["translated_text"];
    if exact(&decode(corrected, LIMIT)?)? != exact(&decode(&expected, LIMIT)?)?
        || (unchanged_text && corrected != current)
    {
        return Err(invalid(
            "timeline adapter differs from native cumulative/source derivation",
        ));
    }
    for object in [&original_object, &current_object, &context] {
        assets.read_json(object)?;
    }
    if exact(&receipt)? != exact(&processing.effective_timeline_receipt()?)? {
        return Err(invalid("timeline processing release changed"));
    }
    unchanged(&store, &state)?;
    if exact(&super::review_effective::read_current(&store, &data, &state)?.packet()?)?
        != exact(&view)?
    {
        return Err(invalid(
            "current timeline dependencies changed during preparation",
        ));
    }
    let timeline = assets.accept_json(corrected)?;
    let proposal = assets.accept_json(after.json())?;
    unchanged(&store, &state)?;
    if render {
        let output_pin = super::review_retention::NativeOutputPin::read(&archive.output_root)?;
        let candidates = super::review_correction::private_root(&archive.output_root, job)?;
        let rendered =
            super::review_render::execute_effective(super::review_render::EffectiveRenderInputs {
                processing: &processing,
                store: &store,
                data: &data,
                assets: &assets,
                state: &state,
                current: &admitted,
                after: &after,
                timeline: &timeline,
                mixed: None,
                output_pin: &output_pin,
                candidates: &candidates,
            })?;
        unchanged(&store, &state)?;
        return super::emit_value(
            &mut std::io::stdout().lock(),
            json!({"schema_version":1,
            "kind":"review_native_rendered_effective_b1","current_token":view["current_token"],
            "context":descriptor(&context),"proposal":descriptor(&proposal),"corrected_timeline":descriptor(&timeline),
            "processing_timeline":receipt,"private_render":rendered,"tts_executed":false,"mix_executed":false,
            "render_executed":true,"model_weights_verified":false,"active_graph_published":false,"correction_ready":false}),
        );
    }
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_native_prepared_effective_timeline",
        "current_token":view["current_token"],"mode":mode_value,"context":descriptor(&context),
        "proposal":descriptor(&proposal),"corrected_timeline":descriptor(&timeline),"processing_timeline":receipt,
        "model_weights_verified":false,"media_executed":false,"active_graph_published":false,"correction_ready":false}),
    )
}

struct CurrentCuePins<'a> {
    context_hash: &'a str,
    token: &'a Value,
    content_hash: &'a str,
    content_snapshot_hash: &'a str,
    projection: &'a str,
    current_input: &'a Value,
    current_provenance: &'a Value,
    target_voice: &'a Value,
}

fn admit_prepared(
    raw: &str,
    before: &AdmittedSnapshot,
    after: &AdmittedSnapshot,
    pins: CurrentCuePins<'_>,
) -> SupervisorResult<BoundCueRequest> {
    let CurrentCuePins {
        context_hash,
        token,
        content_hash,
        content_snapshot_hash,
        projection,
        current_input,
        current_provenance,
        target_voice,
    } = pins;
    let packet = decode(raw, LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "before_token",
            "current_composition_sha256",
            "composition_snapshot_sha256",
            "effective_composition_json",
            "effective_composition_sha256",
            "request_json",
            "request_sha256",
            "config",
            "voice",
            "model_weights_verified",
            "active_graph_published",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_prepared_effective_cue_request"
        || packet["context_sha256"] != context_hash
        || exact(&packet["before_token"])? != exact(token)?
        || packet["current_composition_sha256"] != content_hash
        || packet["composition_snapshot_sha256"] != content_snapshot_hash
        || packet["effective_composition_json"] != projection
        || packet["effective_composition_sha256"] != digest(projection)
        || packet["voice"] != *target_voice
        || packet["model_weights_verified"] != false
        || packet["active_graph_published"] != false
    {
        return Err(invalid(
            "prepared current cue substitutes native token/content/projection/catalog",
        ));
    }
    let request_raw = packet["request_json"]
        .as_str()
        .ok_or_else(|| invalid("current cue request bytes missing"))?;
    let request = bind_current_request(
        request_raw,
        before,
        after,
        CueRequestPins {
            manifest_sha256: token["original_manifest_sha256"]
                .as_str()
                .ok_or_else(|| invalid("current manifest pin missing"))?,
            original_input: current_input,
            previous_provenance: current_provenance,
            config: &packet["config"],
            target_voice,
        },
    )?;
    if packet["request_sha256"] != request.input_hash() {
        return Err(invalid("prepared current request checksum changed"));
    }
    Ok(request)
}

/// Preparation and historical speech recovery share the same independently
/// admitted current-input fence, including numeric type and signed-zero bits.
pub(super) fn bind_current_request(
    request_raw: &str,
    before: &AdmittedSnapshot,
    after: &AdmittedSnapshot,
    pins: CueRequestPins<'_>,
) -> SupervisorResult<BoundCueRequest> {
    let request_value = decode(request_raw, 32768)?;
    // Value equality can equate signed zero or differing numeric types. Every
    // immutable input field retains its independently admitted canonical bytes.
    let mut actual = request_value["tts_input"].clone();
    let mut expected = pins.original_input.clone();
    for value in [&mut actual, &mut expected] {
        let object = value
            .as_object_mut()
            .ok_or_else(|| invalid("current cue input missing"))?;
        object.remove("text");
        object.remove("normalized_text");
    }
    if exact(&actual)? != exact(&expected)? {
        return Err(invalid(
            "current cue changed canonical input mapping/confidence bits",
        ));
    }
    BoundCueRequest::read(request_raw, before, after, pins)
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, false, false, false)
}

pub(super) fn run_synthesis(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, false, false)
}

pub(super) fn run_mix(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, false)
}

pub(super) fn run_render_b2(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true, true, true)
}

fn run_inner(
    mut args: impl Iterator<Item = OsString>,
    synthesize: bool,
    mix: bool,
    render: bool,
) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("current cue flag is not UTF-8"))?;
        if ![
            "--data-root",
            "--job-id",
            "--revision",
            "--snapshot-sha256",
            "--graph-sha256",
            "--finding-id",
            "--translation",
            "--voice",
            "--processing-root",
            "--processing-manifest-sha256",
            "--processing-manifest-size-bytes",
            "--processing-source-sha",
        ]
        .contains(&flag.as_str())
            || values.contains_key(&flag)
        {
            return Err(invalid("unknown or duplicate current cue argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("missing current cue argument"))?
            .into_string()
            .map_err(|_| invalid("current cue value is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values.get(key).map(String::as_str).ok_or_else(|| {
            invalid("current cue requires a complete token and explicit processing release")
        })
    };
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("current cue control root must be absolute"));
    }
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _guard = super::JobExecutionGuard::acquire(&db, job)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("current Review graph missing"))?;
    let revision = tick(&Value::String(required("--revision")?.into()), false)?;
    if state.token.revision != revision
        || state.token.snapshot_sha256 != required("--snapshot-sha256")?
        || state.token.graph_sha256 != required("--graph-sha256")?
    {
        return Err(invalid("current cue token changed; reload"));
    }
    let admitted = super::review_effective::read_current(&store, &data, &state)?;
    let view = admitted.packet()?;
    if view["generation_dependencies_complete"] != true
        || view["content_composition_json"].is_null()
    {
        return Err(invalid(
            "current B2 cue requires complete admitted speech dependencies; no legacy inference",
        ));
    }
    let before = admitted.snapshot();
    let after = before.correction(
        required("--finding-id")?,
        values.get("--translation").map(String::as_str),
        values.get("--voice").map(String::as_str),
        ReviewMode::Dubbed,
    )?;
    let seed = super::review_seed::read_original(&store, &data, admitted.origin())?;
    let source_graph = decode(&seed.graph, LIMIT)?;
    if source_graph["mode"] != "b2" {
        return Err(invalid(
            "effective speech preparation cannot change dubbing mode",
        ));
    }
    let token = view["current_token"].clone();
    let content = view["content_composition_json"]
        .as_str()
        .ok_or_else(|| invalid("content composition missing"))?;
    let content_snapshot = view["composition_snapshot_json"]
        .as_str()
        .ok_or_else(|| invalid("content snapshot missing"))?;
    let projection = view["effective_composition_json"]
        .as_str()
        .ok_or_else(|| invalid("current projection missing"))?;
    let composition = decode(content, LIMIT)?;
    let current_projection = decode(projection, LIMIT)?;
    let cue = before.finding(after.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("current cue binding missing"))?;
    let rows = current_projection["cues"]
        .as_array()
        .ok_or_else(|| invalid("current cue inventory missing"))?;
    let selected = rows
        .iter()
        .find(|row| row["tts_input"]["segment_id"] == cue)
        .ok_or_else(|| invalid("selected cue is not admitted current content"))?;
    let documents = composition["documents"]
        .as_array()
        .ok_or_else(|| invalid("current document inventory missing"))?;
    let selected_doc = documents
        .iter()
        .find(|doc| doc["sha256"] == selected["document_sha256"])
        .ok_or_else(|| invalid("current cue document missing"))?;
    let assets = AcceptedAudioStore::open_existing(&data, job)?;
    let selected_doc = accepted_json(
        &json!({"path":selected_doc["path"],"sha256":selected_doc["sha256"],
        "size_bytes":selected_doc["size_bytes"]}),
        &assets,
    )?;
    let speech = decode(&assets.read_json(&selected_doc)?, LIMIT)?;
    let origin = decode(&admitted.origin().graph_json, LIMIT)?;
    let manifest = accepted_json(&origin["accepted_documents"]["job_manifest"], &assets)?;
    let manifest = decode(&assets.read_json(&manifest)?, LIMIT)?;
    let source = &manifest["artifacts"]["source_audio"];
    closed(source, &["path", "sha256", "size_bytes"])?;
    if composition["source"]["path"] != source["path"]
        || composition["source"]["sha256"] != source["sha256"]
    {
        return Err(invalid(
            "current cue source PCM differs from immutable original",
        ));
    }
    let template = accepted_json(&origin["accepted_documents"]["mix_document"], &assets)?;
    let size = tick(
        &Value::String(required("--processing-manifest-size-bytes")?.into()),
        false,
    )?;
    let processing = AdmittedProcessingRuntime::read(
        &store,
        job,
        &PathBuf::from(required("--processing-root")?),
        &json!({"sha256":required("--processing-manifest-sha256")?,"size_bytes":size}),
        required("--processing-source-sha")?,
    )?;
    let voice_id = after.finding(after.plan_finding_id()?)?["voice"]
        .as_str()
        .ok_or_else(|| invalid("current target voice missing"))?;
    let voice = processing.runtime().voice(voice_id)?;
    let initial_mix_receipt = if mix {
        Some(processing.effective_mix_receipt()?)
    } else {
        None
    };
    if render {
        processing.effective_render_receipt(None)?;
    }
    let context = json!({"schema_version":1,"kind":"review_effective_cue_context","capability":"review_effective_audio_v1",
        "before_token":token,"before_snapshot_json":before.json(),"after_snapshot_json":after.json(),
        "current_composition_sha256":digest(content),"composition_snapshot_sha256":digest(content_snapshot)});
    let context = assets.accept_json(&context.to_string())?;
    let bindings = json!({"schema_version":1,"kind":"review_native_effective_cue_bindings","current_token":token,
        "content_composition_json":content,"content_composition_sha256":digest(content),
        "composition_snapshot_json":content_snapshot,"composition_snapshot_sha256":digest(content_snapshot),
        "source_audio":{"path":source["path"],"sha256":source["sha256"],"size_bytes":source["size_bytes"],"artifact_root":seed.output_root},
        "canonical_inputs":source_graph["canonical_inputs"],"documents":composition["documents"],
        "mix_template":{"path":template.path,"sha256":template.sha256,"size_bytes":template.size_bytes,
            "artifact_root":seed.output_root,"metadata_root":template.path.parent()}});
    let bindings = assets.accept_json(&bindings.to_string())?;
    unchanged(&store, &state)?;
    let mut command = Command::new(&processing.runtime().python);
    command.args([
        OsString::from("-I"),
        OsString::from("-S"),
        OsString::from("-B"),
        processing.preparation_dispatch()?.into_os_string(),
        OsString::from("--context"),
        context.path.clone().into_os_string(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--bindings"),
        bindings.path.clone().into_os_string(),
        OsString::from("--bindings-sha256"),
        OsString::from(&bindings.sha256),
        OsString::from("--app-root"),
        processing.runtime().root.join("app").into_os_string(),
        OsString::from("--profile"),
        processing.runtime().profile.clone().into_os_string(),
    ]);
    let raw = super::review_seed::collect(command, Duration::from_secs(60), LIMIT)?;
    let request = admit_prepared(
        &raw,
        before,
        &after,
        CurrentCuePins {
            context_hash: &context.sha256,
            token: &token,
            content_hash: &digest(content),
            content_snapshot_hash: &digest(content_snapshot),
            projection,
            current_input: &selected["tts_input"],
            current_provenance: &speech["provenance"],
            target_voice: &voice,
        },
    )?;
    processing.preparation_dispatch()?;
    assets.read_json(&context)?;
    assets.read_json(&bindings)?;
    unchanged(&store, &state)?;
    let final_state = super::review_effective::read_current(&store, &data, &state)?.packet()?;
    if final_state != view {
        return Err(invalid(
            "admitted effective dependencies changed during cue preparation",
        ));
    }
    let object = assets.accept_json(request.raw())?;
    if object.sha256 != request.input_hash() {
        return Err(invalid("sealed current request changed canonical bytes"));
    }
    let proposal = assets.accept_json(after.json())?;
    let projected = assets.accept_json(projection)?;
    let processing_receipt = processing.receipt()?;
    if synthesize {
        // Admit storage and the explicit TTS capability before inference. A
        // preparation receipt, legacy speech inventory or mutable UI path
        // cannot authorize model loading or output-volume fallback.
        let output_pin = super::review_retention::NativeOutputPin::read(&seed.output_root)?;
        let synthesis_receipt = processing.cue_synthesis_receipt()?;
        let recipe = synthesis_receipt["recipe_identity"]
            .as_str()
            .ok_or_else(|| invalid("admitted current synthesis recipe missing"))?;
        processing.runtime().cue_execution()?;
        let candidates = super::review_correction::private_root(&seed.output_root, job)?;
        unchanged(&store, &state)?;
        let result = super::review_correction::synthesize_accepted(
            processing.runtime(),
            &assets,
            &candidates,
            &request,
            &object,
            Some(&output_pin),
            Some(recipe),
        )?;
        // Independently re-open native PCM and accepted documents, rational
        // actual-end mapping, exact confidence bits and spoken rewrite evidence.
        super::review_speech::read_accepted_cue(
            &assets,
            &request,
            &result,
            std::time::Instant::now() + Duration::from_secs(180),
            &output_pin,
        )?;
        let mixed = if let Some(initial_receipt) = initial_mix_receipt.as_ref() {
            unchanged(&store, &state)?;
            let before_mix =
                super::review_effective::read_current(&store, &data, &state)?.packet()?;
            if exact(&before_mix)? != exact(&view)? {
                return Err(invalid(
                    "current mix input generation changed during synthesis",
                ));
            }
            Some(mix_current(CurrentMixInputs {
                processing: &processing,
                assets: &assets,
                request: &request,
                cue: &result,
                view: &view,
                preparation: &context,
                bindings: &bindings,
                template: &template,
                output_pin: &output_pin,
                candidates: &candidates,
                initial_receipt,
            })?)
        } else {
            None
        };
        let final_receipt = processing.cue_synthesis_receipt()?;
        if exact(&synthesis_receipt)? != exact(&final_receipt)? {
            return Err(invalid("processing synthesis dependencies changed"));
        }
        for document in [&context, &bindings, &object, &proposal, &projected] {
            assets.read_json(document)?;
        }
        unchanged(&store, &state)?;
        let reopened = super::review_effective::read_current(&store, &data, &state)?.packet()?;
        if exact(&reopened)? != exact(&view)? {
            return Err(invalid("current generation changed during cue synthesis"));
        }
        unchanged(&store, &state)?;
        let rendered = if render {
            let original =
                accepted_json(&origin["accepted_documents"]["editable_timeline"], &assets)?;
            let current = accepted_json(&view["metadata_pins"]["current_timeline"], &assets)?;
            let corrected = super::review_timeline::derive_effective(
                &assets.read_json(&original)?,
                &seed.snapshot,
                &assets.read_json(&current)?,
                before,
                &after,
                ReviewMode::Dubbed,
            )?;
            let timeline = assets.accept_json(&corrected)?;
            let private_mix = mixed
                .as_ref()
                .ok_or_else(|| invalid("current B2 render requires its admitted mix"))?;
            let rendered = super::review_render::execute_effective(
                super::review_render::EffectiveRenderInputs {
                    processing: &processing,
                    store: &store,
                    data: &data,
                    assets: &assets,
                    state: &state,
                    current: &admitted,
                    after: &after,
                    timeline: &timeline,
                    mixed: Some(&private_mix.retained_mix),
                    output_pin: &output_pin,
                    candidates: &candidates,
                },
            )?;
            Some(rendered)
        } else {
            None
        };
        unchanged(&store, &state)?;
        let mut packet = json!({
            "schema_version":1,"kind":if render {"review_native_rendered_effective_b2"} else if mix {"review_native_mixed_effective_cue"} else {"review_native_synthesized_effective_cue"},
            "current_token":token,"context":descriptor(&context),"bindings":descriptor(&bindings),
            "request":descriptor(&object),"proposal":descriptor(&proposal),
            "effective_composition":descriptor(&projected),"accepted_cue":result,
            "processing_synthesis":final_receipt,"tts_executed":true,"mix_executed":mix,
            "render_executed":render,"model_weights_verified":true,
            "active_graph_published":false,"correction_ready":false,
        });
        if let Some(mixed) = mixed {
            packet["private_mix"] = mixed.packet();
        }
        if let Some(rendered) = rendered {
            packet["private_render"] = rendered;
        }
        return super::emit_value(&mut std::io::stdout().lock(), packet);
    }
    unchanged(&store, &state)?;
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_native_prepared_effective_cue",
        "current_token":token,"context":descriptor(&context),"bindings":descriptor(&bindings),
        "request":descriptor(&object),"proposal":descriptor(&proposal),"effective_composition":descriptor(&projected),
        "processing_release":processing_receipt,"tts_executed":false,"mix_executed":false,
        "model_weights_verified":false,"active_graph_published":false,"correction_ready":false}),
    )
}

pub(super) fn replacement_composition(
    current: &Value,
    request: &BoundCueRequest,
    accepted: &Value,
) -> SupervisorResult<Value> {
    let request = decode(request.raw(), 32768)?;
    let mut expected = current.clone();
    let rows = expected["cues"]
        .as_array_mut()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("current mix lacks complete cue assignments"))?;
    let mut selected = 0;
    for row in rows.iter_mut() {
        if row["tts_input"]["segment_id"] == request["cue_id"] {
            row["tts_input"] = request["tts_input"].clone();
            row["document_sha256"] = accepted["sha256"].clone();
            selected += 1;
        }
    }
    if selected != 1 {
        return Err(invalid("current mix selected cue is ambiguous"));
    }
    let hashes: std::collections::BTreeSet<String> = rows
        .iter()
        .map(|v| {
            v["document_sha256"]
                .as_str()
                .map(String::from)
                .ok_or_else(|| invalid("current mix document hash missing"))
        })
        .collect::<SupervisorResult<_>>()?;
    let mut documents = std::collections::BTreeMap::new();
    for doc in current["documents"]
        .as_array()
        .ok_or_else(|| invalid("current mix documents missing"))?
    {
        let key = doc["sha256"]
            .as_str()
            .ok_or_else(|| invalid("current mix document pin missing"))?;
        if hashes.contains(key) {
            documents.insert(key.to_string(), doc.clone());
        }
    }
    let key = accepted["sha256"]
        .as_str()
        .ok_or_else(|| invalid("accepted current TTS hash missing"))?;
    if let Some(previous) = documents.get(key) {
        if exact(previous)? != exact(accepted)? {
            return Err(invalid(
                "current mix assigns conflicting descriptors to one hash",
            ));
        }
    }
    documents.insert(key.to_string(), accepted.clone());
    if documents.len() != hashes.len() {
        return Err(invalid("current mix loses an unrelated document"));
    }
    expected["schema_version"] = json!(if documents
        .values()
        .any(|d| d.get("metadata_root").is_some())
    {
        2
    } else {
        1
    });
    expected["documents"] = json!(documents.into_values().collect::<Vec<_>>());
    expected["review_snapshot_sha256"] = request["after_snapshot_sha256"].clone();
    Ok(expected)
}

fn mix_evidence(
    composition: &Value,
    docs: &std::collections::HashMap<String, Value>,
) -> SupervisorResult<(Value, Value, Value)> {
    let mut rows = Vec::new();
    for cue in composition["cues"]
        .as_array()
        .ok_or_else(|| invalid("current mix cues missing"))?
    {
        let input = &cue["tts_input"];
        let id = input["segment_id"]
            .as_str()
            .ok_or_else(|| invalid("current mix cue id missing"))?;
        let doc = docs
            .get(cue["document_sha256"].as_str().unwrap_or(""))
            .ok_or_else(|| invalid("current mix assigned TTS missing"))?;
        let artifact = doc["artifacts"]
            .as_array()
            .and_then(|v| v.iter().find(|a| a["segment_id"] == id));
        let end = if input.get("render_window_end").is_some() {
            artifact.map(|a| &a["actual_end"]).unwrap_or(&input["end"])
        } else {
            &input["end"]
        };
        let start_ticks = tick(&input["start"]["ticks"], false)?;
        let end_ticks = tick(&end["ticks"], false)?;
        let fallback = artifact
            .map(|a| a["fallback_used"].clone())
            .unwrap_or(json!(false));
        let row = json!({"segment_id":id,"source_utterance_id":input["source_utterance_id"],
            "status":if artifact.is_some(){"completed"}else{"failed"},"confidence":input["confidence"],"fallback_used":fallback});
        let mut failure = Value::Null;
        let mut window = Value::Null;
        if artifact.is_none() {
            let recorded = doc["failures"]
                .as_array()
                .and_then(|v| v.iter().find(|f| f["segment_id"] == id))
                .ok_or_else(|| invalid("current mix failure has no source TTS evidence"))?;
            failure = json!({"code":"TTS_FAILED","segment_id":id,
                "condition":format!("{}: {}",recorded["code"].as_str().ok_or_else(||invalid("failure code missing"))?,
                    recorded["condition"].as_str().ok_or_else(||invalid("failure condition missing"))?),
                "recoverable":true,"fallback_used":false});
        } else {
            let numerator = u128::from(tick(&input["start"]["time_base"]["numerator"], true)?);
            let denominator = u128::from(tick(&input["start"]["time_base"]["denominator"], true)?);
            let samples = |ticks: u64, ceil: bool| -> SupervisorResult<u64> {
                let product = u128::from(ticks)
                    .checked_mul(numerator)
                    .and_then(|v| v.checked_mul(48000))
                    .ok_or_else(|| invalid("current mix sample mapping overflow"))?;
                let value = product / denominator + u128::from(ceil && product % denominator != 0);
                u64::try_from(value).map_err(|_| invalid("current mix sample index overflow"))
            };
            window = json!({"segment_id":id,"start_sample":samples(start_ticks,false)?,
                "end_sample":samples(end_ticks,true)?,"attack_samples":samples(40,true)?,
                "release_samples":samples(120,true)?,"duck_gain_milli":350});
        }
        rows.push((
            (start_ticks, end_ticks, id.to_string()),
            row,
            failure,
            window,
        ));
    }
    // This is the admitted stream mixer's explicit source-interval ordering;
    // it never changes Review stage plans or logical cue assignment order.
    rows.sort_by(|a, b| a.0.cmp(&b.0));
    Ok((
        json!(rows.iter().map(|r| r.1.clone()).collect::<Vec<_>>()),
        json!(rows
            .iter()
            .filter(|r| !r.2.is_null())
            .map(|r| r.2.clone())
            .collect::<Vec<_>>()),
        json!(rows
            .iter()
            .filter(|r| !r.3.is_null())
            .map(|r| r.3.clone())
            .collect::<Vec<_>>()),
    ))
}

fn admit_current_mix(
    raw: &str,
    context_hash: &str,
    token: &Value,
    request: &BoundCueRequest,
    composition: &Value,
    evidence: &(Value, Value, Value),
    template: &Value,
) -> SupervisorResult<(String, String)> {
    let packet = decode(raw, LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "before_token",
            "request_sha256",
            "composition_json",
            "composition_sha256",
            "mix_document_json",
            "mix_document_sha256",
            "active_graph_published",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_effective_mix_candidate"
        || packet["context_sha256"] != context_hash
        || exact(&packet["before_token"])? != exact(token)?
        || packet["request_sha256"] != request.input_hash()
        || packet["active_graph_published"] != false
    {
        return Err(invalid(
            "current mix packet substitutes its native token/request/context",
        ));
    }
    let bytes = |name: &str, pin: &str| -> SupervisorResult<String> {
        let s = packet[name]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= LIMIT)
            .ok_or_else(|| invalid("current mix metadata exceeds its bound"))?;
        if packet[pin] != digest(s) {
            return Err(invalid("current mix metadata checksum changed"));
        }
        Ok(s.into())
    };
    let composition_raw = bytes("composition_json", "composition_sha256")?;
    let mix_raw = bytes("mix_document_json", "mix_document_sha256")?;
    if exact(&decode(&composition_raw, LIMIT)?)? != exact(composition)? {
        return Err(invalid(
            "current mix loses prior edits or substitutes cue/source/producer assignments",
        ));
    }
    let mix = decode(&mix_raw, LIMIT)?;
    closed(
        &mix,
        &[
            "schema_version",
            "kind",
            "source_kind",
            "target_language",
            "source_id",
            "segment_ids",
            "segments",
            "source_layout",
            "source_start",
            "source_end",
            "sample_rate",
            "channels",
            "original_audio",
            "dialogue_stem",
            "final_mix",
            "duck_windows",
            "chunks",
            "provenance",
            "failures",
            "warnings",
        ],
    )?;
    let mut provenance = template["provenance"].clone();
    provenance["input_hash"] = packet["composition_sha256"].clone();
    provenance["source_hash"] = composition["source"]["sha256"].clone();
    let ids = json!(evidence
        .0
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["segment_id"].clone())
        .collect::<Vec<_>>());
    if mix["schema_version"].as_u64() != template["schema_version"].as_u64()
        || mix["kind"] != "audio_mix_document"
        || mix["source_kind"] != "source_audio"
        || mix["target_language"] != "vi"
        || mix["original_audio"]["content_hash"] != composition["source"]["sha256"]
        || exact(&mix["provenance"])? != exact(&provenance)?
        || mix["segment_ids"] != ids
        || exact(&mix["segments"])? != exact(&evidence.0)?
        || exact(&mix["failures"])? != exact(&evidence.1)?
        || exact(&mix["duck_windows"])? != exact(&evidence.2)?
        || [
            "source_id",
            "source_layout",
            "source_start",
            "source_end",
            "sample_rate",
            "channels",
        ]
        .iter()
        .any(|k| mix[*k] != template[*k])
    {
        return Err(invalid(
            "current mix changes source mapping, cue/failure/window evidence or protected producer",
        ));
    }
    let chunks = mix["chunks"]
        .as_array()
        .ok_or_else(|| invalid("current mix chunk evidence missing"))?;
    let rows = evidence.0.as_array().unwrap();
    if chunks.len() != rows.len().div_ceil(8) {
        return Err(invalid("current mix chunk coverage incomplete"));
    }
    for (index, (chunk, group)) in chunks.iter().zip(rows.chunks(8)).enumerate() {
        let completed = group.iter().all(|r| r["status"] == "completed");
        let failed = group.iter().all(|r| r["status"] == "failed");
        let mut keys = vec!["chunk_id", "index", "segment_ids", "status", "attempt"];
        if !completed {
            keys.push("error_code");
        }
        closed(chunk, &keys)?;
        let chunk_id = chunk["chunk_id"].as_str().unwrap_or("");
        if chunk["index"].as_u64() != Some(index as u64)
            || chunk["attempt"].as_u64() != Some(1)
            || chunk["status"]
                != if completed {
                    "completed"
                } else if failed {
                    "failed"
                } else {
                    "degraded"
                }
            || chunk["segment_ids"]
                != json!(group
                    .iter()
                    .map(|r| r["segment_id"].clone())
                    .collect::<Vec<_>>())
            || chunk_id.len() != 37
            || !chunk_id.starts_with("stream-chunk-")
            || !chunk_id[13..]
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || (!completed && chunk["error_code"] != "TTS_FAILED")
        {
            return Err(invalid(
                "current mix substitutes chunk ownership or execution evidence",
            ));
        }
    }
    Ok((composition_raw, mix_raw))
}

struct CurrentMixInputs<'a> {
    processing: &'a AdmittedProcessingRuntime,
    assets: &'a AcceptedAudioStore,
    request: &'a BoundCueRequest,
    cue: &'a Value,
    view: &'a Value,
    preparation: &'a AcceptedJsonDocument,
    bindings: &'a AcceptedJsonDocument,
    template: &'a AcceptedJsonDocument,
    output_pin: &'a super::review_retention::NativeOutputPin,
    candidates: &'a std::path::Path,
    initial_receipt: &'a Value,
}

struct CurrentMixResult {
    processing_mix: Value,
    retained_mix: Value,
    candidate_packet: AcceptedJsonDocument,
}

impl CurrentMixResult {
    // Existing private command packets retain their existing closed schema.
    // Only the new v3 native caller may publish the separate evidence reference.
    fn packet(&self) -> Value {
        json!({"processing_mix":self.processing_mix,"retained_mix":self.retained_mix,"active_graph_published":false})
    }
}

fn mix_current(input: CurrentMixInputs<'_>) -> SupervisorResult<CurrentMixResult> {
    let CurrentMixInputs {
        processing,
        assets,
        request,
        cue,
        view,
        preparation,
        bindings,
        template,
        output_pin,
        candidates,
        initial_receipt,
    } = input;
    let receipt = processing.effective_mix_receipt()?;
    if exact(&receipt)? != exact(initial_receipt)? {
        return Err(invalid("current mix processing admission changed"));
    }
    let tts = accepted_json(&cue["tts_document"], assets)?;
    let audio = std::path::PathBuf::from(
        cue["audio"]["path"]
            .as_str()
            .ok_or_else(|| invalid("accepted cue PCM missing"))?,
    );
    let descriptor = json!({"path":tts.path,"sha256":tts.sha256,"size_bytes":tts.size_bytes,
        "artifact_root":audio.parent().ok_or_else(||invalid("accepted PCM root missing"))?,"metadata_root":tts.path.parent()});
    let current = decode(
        view["effective_composition_json"]
            .as_str()
            .ok_or_else(|| invalid("current composition missing"))?,
        LIMIT,
    )?;
    let expected = replacement_composition(&current, request, &descriptor)?;
    let mut docs = std::collections::HashMap::new();
    for doc in expected["documents"].as_array().unwrap() {
        let object = accepted_json(
            &json!({"path":doc["path"],"sha256":doc["sha256"],"size_bytes":doc["size_bytes"]}),
            assets,
        )?;
        docs.insert(
            object.sha256.clone(),
            decode(&assets.read_json(&object)?, LIMIT)?,
        );
    }
    let evidence = mix_evidence(&expected, &docs)?;
    let template_value = decode(&assets.read_json(template)?, LIMIT)?;
    let context=assets.accept_json(&json!({"schema_version":1,"kind":"review_native_effective_mix_context",
        "preparation_context_json":assets.read_json(preparation)?,"preparation_context_sha256":preparation.sha256,
        "bindings_json":assets.read_json(bindings)?,"bindings_sha256":bindings.sha256,
        "request_json":request.raw(),"request_sha256":request.input_hash(),"accepted_tts_document":descriptor,
        "accepted_spoken_text":cue["spoken_text"],"accept_context":cue["accept_context"]}).to_string())?;
    let entry = std::path::PathBuf::from(
        receipt["mix_entry"]["path"]
            .as_str()
            .ok_or_else(|| invalid("current mix entry missing"))?,
    );
    let mut command = super::review_correction::cue_command(
        processing.runtime(),
        &entry,
        &super::review_correction::candidate_scratch(candidates)?,
    )?;
    command.args([
        OsString::from("--context"),
        context.path.as_os_str().to_owned(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--output-root"),
        candidates.as_os_str().to_owned(),
        OsString::from("--app-root"),
        processing.runtime().root.join("app").into_os_string(),
        OsString::from("--profile"),
        processing.runtime().profile.as_os_str().to_owned(),
    ]);
    let raw = super::review_seed::collect(command, Duration::from_secs(45 * 60), LIMIT)?;
    let (composition, mix) = admit_current_mix(
        &raw,
        &context.sha256,
        &view["current_token"],
        request,
        &expected,
        &evidence,
        &template_value,
    )?;
    let candidate_packet = assets.accept_json(&raw)?;
    let request_value = decode(request.raw(), 32768)?;
    let result =
        super::review_correction::accept_private_mix(super::review_correction::PrivateMixInputs {
            assets,
            output: candidates,
            job: request_value["job_id"]
                .as_str()
                .ok_or_else(|| invalid("current mix job missing"))?,
            context: &context,
            composition: &composition,
            mix: &mix,
            frame_count: template_value["original_audio"]["frame_count"]
                .as_u64()
                .ok_or_else(|| invalid("original mix frames missing"))?,
            retained_output: Some(output_pin),
        })?;
    for doc in [preparation, bindings, template, &tts, &context] {
        assets.read_json(doc)?;
    }
    if exact(&receipt)? != exact(&processing.effective_mix_receipt()?)? {
        return Err(invalid("current mix release changed during execution"));
    }
    assets.read_json(&candidate_packet)?;
    Ok(CurrentMixResult {
        processing_mix: receipt,
        retained_mix: result,
        candidate_packet,
    })
}

pub(super) struct CurrentMixRecoveryInputs<'a> {
    pub(super) store: &'a DurableStore,
    pub(super) data: &'a std::path::Path,
    pub(super) processing: &'a AdmittedProcessingRuntime,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) current: &'a super::review_effective::AdmittedEffectiveState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) request: &'a BoundCueRequest,
    pub(super) cue: &'a Value,
    pub(super) retained_mix: &'a Value,
    pub(super) candidate_packet: &'a Value,
    pub(super) processing_mix: &'a Value,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
}

/// Recover the original accepted current-mix packet, never a newly fabricated
/// success response. The same independent metadata oracle and all three native
/// retained PCM fences apply without executing a producer or recomputing DSP.
pub(super) fn recover_current_mix(
    input: CurrentMixRecoveryInputs<'_>,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<Value> {
    walk.check()?;
    let view = input.current.packet()?;
    let seed = super::review_seed::read_original(input.store, input.data, input.current.origin())?;
    let origin = decode(&input.current.origin().graph_json, LIMIT)?;
    let graph = decode(&seed.graph, LIMIT)?;
    if graph["mode"] != "b2" || input.output_pin.volume_id().is_empty() {
        return Err(invalid(
            "current mix recovery requires its native B2/output admission",
        ));
    }
    let tts = accepted_json(&input.cue["tts_document"], input.assets)?;
    let audio = std::path::PathBuf::from(
        input.cue["audio"]["path"]
            .as_str()
            .ok_or_else(|| invalid("recovered current PCM path missing"))?,
    );
    let assigned = json!({"path":tts.path,"sha256":tts.sha256,"size_bytes":tts.size_bytes,
        "artifact_root":audio.parent().ok_or_else(||invalid("recovered PCM parent missing"))?,"metadata_root":tts.path.parent()});
    let current = decode(
        view["effective_composition_json"]
            .as_str()
            .ok_or_else(|| invalid("current recovery composition missing"))?,
        LIMIT,
    )?;
    let expected = replacement_composition(&current, input.request, &assigned)?;
    let template = accepted_json(&origin["accepted_documents"]["mix_document"], input.assets)?;
    let manifest = accepted_json(&origin["accepted_documents"]["job_manifest"], input.assets)?;
    let manifest_value = decode(&walk.read_metadata(input.assets, &manifest)?, LIMIT)?;
    let source = &manifest_value["artifacts"]["source_audio"];
    closed(source, &["path", "sha256", "size_bytes"])?;
    if current["source"]["path"] != source["path"]
        || current["source"]["sha256"] != source["sha256"]
    {
        return Err(invalid(
            "current recovery source PCM differs from immutable original",
        ));
    }
    let template_value = decode(&walk.read_metadata(input.assets, &template)?, LIMIT)?;
    let content = view["content_composition_json"]
        .as_str()
        .ok_or_else(|| invalid("current recovery content missing"))?;
    let content_snapshot = view["composition_snapshot_json"]
        .as_str()
        .ok_or_else(|| invalid("current recovery content snapshot missing"))?;
    let preparation = json!({"schema_version":1,"kind":"review_effective_cue_context","capability":"review_effective_audio_v1",
        "before_token":view["current_token"],"before_snapshot_json":input.current.snapshot().json(),"after_snapshot_json":input.after.json(),
        "current_composition_sha256":digest(content),"composition_snapshot_sha256":digest(content_snapshot)});
    let bindings = json!({"schema_version":1,"kind":"review_native_effective_cue_bindings","current_token":view["current_token"],
        "content_composition_json":content,"content_composition_sha256":digest(content),"composition_snapshot_json":content_snapshot,
        "composition_snapshot_sha256":digest(content_snapshot),"source_audio":{"path":source["path"],"sha256":source["sha256"],
            "size_bytes":source["size_bytes"],"artifact_root":seed.output_root},
        "canonical_inputs":graph["canonical_inputs"],"documents":current["documents"],
        "mix_template":{"path":template.path,"sha256":template.sha256,"size_bytes":template.size_bytes,
            "artifact_root":seed.output_root,"metadata_root":template.path.parent()}});
    let mixed = input.retained_mix;
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
        return Err(invalid(
            "current mix recovery lacks complete native retention flags",
        ));
    }
    let context = accepted_json(&mixed["context"], input.assets)?;
    let context_value = decode(&walk.read_metadata(input.assets, &context)?, LIMIT)?;
    closed(
        &context_value,
        &[
            "schema_version",
            "kind",
            "preparation_context_json",
            "preparation_context_sha256",
            "bindings_json",
            "bindings_sha256",
            "request_json",
            "request_sha256",
            "accepted_tts_document",
            "accepted_spoken_text",
            "accept_context",
        ],
    )?;
    let preparation_raw = context_value["preparation_context_json"]
        .as_str()
        .ok_or_else(|| invalid("saved preparation bytes missing"))?;
    let bindings_raw = context_value["bindings_json"]
        .as_str()
        .ok_or_else(|| invalid("saved current binding bytes missing"))?;
    if context_value["schema_version"].as_u64() != Some(1)
        || context_value["kind"] != "review_native_effective_mix_context"
        || exact(&decode(preparation_raw, LIMIT)?)? != exact(&preparation)?
        || context_value["preparation_context_sha256"] != digest(preparation_raw)
        || exact(&decode(bindings_raw, LIMIT)?)? != exact(&bindings)?
        || context_value["bindings_sha256"] != digest(bindings_raw)
        || context_value["request_json"] != input.request.raw()
        || context_value["request_sha256"] != input.request.input_hash()
        || exact(&context_value["accepted_tts_document"])? != exact(&assigned)?
        || context_value["accepted_spoken_text"] != input.cue["spoken_text"]
        || context_value["accept_context"] != input.cue["accept_context"]
    {
        return Err(invalid(
            "saved current mix context replaces native token/content/source/request/cue bindings",
        ));
    }
    let processing = accepted_json(input.processing_mix, input.assets)?;
    let processing_value = decode(&walk.read_metadata(input.assets, &processing)?, LIMIT)?;
    let receipt = input.processing.effective_mix_receipt()?;
    if exact(&receipt)? != exact(&processing_value)? {
        return Err(invalid("saved current mix processing recipe changed"));
    }
    let mut documents = std::collections::HashMap::new();
    let mut objects = Vec::new();
    for doc in expected["documents"].as_array().unwrap() {
        let object = accepted_json(
            &json!({"path":doc["path"],"sha256":doc["sha256"],"size_bytes":doc["size_bytes"]}),
            input.assets,
        )?;
        documents.insert(
            object.sha256.clone(),
            decode(&walk.read_metadata(input.assets, &object)?, LIMIT)?,
        );
        objects.push(object);
    }
    let evidence = mix_evidence(&expected, &documents)?;
    let packet = accepted_json(input.candidate_packet, input.assets)?;
    let raw_packet = walk.read_metadata(input.assets, &packet)?;
    let (composition_raw, candidate_raw) = admit_current_mix(
        &raw_packet,
        &context.sha256,
        &view["current_token"],
        input.request,
        &expected,
        &evidence,
        &template_value,
    )?;
    let composition = accepted_json(&mixed["composition"], input.assets)?;
    let candidate = accepted_json(&mixed["candidate_mix_document"], input.assets)?;
    let retained = accepted_json(&mixed["mix_document"], input.assets)?;
    if walk.read_metadata(input.assets, &composition)? != composition_raw
        || walk.read_metadata(input.assets, &candidate)? != candidate_raw
    {
        return Err(invalid(
            "saved current mix packet changed its exact admitted metadata",
        ));
    }
    let media = super::review_retention::NativeMixStore::open_existing(
        input.output_pin,
        input.current.snapshot().job(),
        super::review_assets::MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    media.revalidate_admitted_document(
        &candidate_raw,
        &composition.sha256,
        &mixed["retained_mix_audio"],
        &walk.read_metadata(input.assets, &retained)?,
        walk.deadline(),
    )?;
    drop(media);
    for object in objects.iter().chain([
        &tts,
        &template,
        &manifest,
        &context,
        &processing,
        &packet,
        &composition,
        &candidate,
        &retained,
    ]) {
        walk.read_metadata(input.assets, object)?;
    }
    if exact(&receipt)? != exact(&input.processing.effective_mix_receipt()?)? {
        return Err(invalid("current mix processing changed during recovery"));
    }
    walk.check()?;
    Ok(expected)
}
