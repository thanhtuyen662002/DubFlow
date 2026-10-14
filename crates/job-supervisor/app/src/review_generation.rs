//! Opt-in current-generation cue preparation through an admitted processing release.
//! No original-only guard is relaxed and no TTS/mix/render/graph CAS is executed.
use super::review_assets::AcceptedAudioStore;
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
    let request_value = decode(request_raw, 32768)?;
    // Value equality can equate signed zero or differing numeric types. Every
    // immutable input field retains its independently admitted canonical bytes.
    let mut actual = request_value["tts_input"].clone();
    let mut expected = current_input.clone();
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
    let request = BoundCueRequest::read(
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

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
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
