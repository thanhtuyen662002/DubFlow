//! Native original-cue request preparation. No inference or graph publication.
//! The command only accepts a native stale-client token and cue edit values.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument};
use super::review_document::{closed, decode, tick, AdmittedSnapshot, ReviewMode};
use super::review_request::{BoundCueRequest, CueRequestPins};
use super::review_runtime::AdmittedRuntime;
use super::review_seed::{collect, read_original};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn descriptor(object: &AcceptedJsonDocument) -> Value {
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
    // SQLite enforces read-only even though private request objects are stored.
    let store = DurableStore::open_read_only(&db)?;
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
    let graph = decode(&seed.graph, LIMIT)?;
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
    let target_voice = runtime.voice(selected)?;
    let assets = AcceptedAudioStore::open_existing(&data, job)?;
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
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_prepared_original_cue",
        "job_id":job,"revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "finding_id":finding,"context":descriptor(&context),"request":descriptor(&accepted),"release_sha256":runtime.release_hash,
        "proposal":decode(after.json(),LIMIT)?,"model_weights_verified":false,"correction_ready":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
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
}
