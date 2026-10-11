//! Native original-cue preparation and private synthesis. No graph publication.
//! The command only accepts a native stale-client token and cue edit values.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument, PcmPin};
use super::review_document::{closed, decode, tick, AdmittedSnapshot, ReviewMode};
use super::review_request::{BoundCueRequest, CueRequestPins};
use super::review_runtime::AdmittedRuntime;
use super::review_seed::{collect, read_original};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
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
    run_inner(&mut args, false)
}

pub(super) fn run_synthesis(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(&mut args, true)
}

fn run_inner(mut args: impl Iterator<Item = OsString>, synthesize: bool) -> SupervisorResult<()> {
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
    if synthesize {
        let result = synthesize_accepted(&runtime, &assets, &data, job, &request, &accepted)?;
        assets.read_json(&context)?;
        assets.read_json(&accepted)?;
        unchanged(&store, &state)?;
        let _original = read_original(&store, &data, &state)?;
        return super::emit_value(
            &mut std::io::stdout().lock(),
            json!({"schema_version":1,"kind":"review_synthesized_original_cue",
            "job_id":job,"revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
            "finding_id":finding,"context":descriptor(&context),"request":descriptor(&accepted),"release_sha256":runtime.release_hash,
            "proposal":decode(after.json(),LIMIT)?,"accepted_cue":result,"model_weights_verified":true,"correction_ready":false}),
        );
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
        || artifact["confidence"] != segment["confidence"]
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

fn private_root(data: &Path, job: &str) -> SupervisorResult<PathBuf> {
    let root = data.join("control/review-candidates");
    let job_root = root.join(format!("{:x}", Sha256::digest(job.as_bytes())));
    for path in [&root, &job_root, &job_root.join("temp")] {
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

fn cue_command(runtime: &AdmittedRuntime, entry: &Path, scratch: &Path) -> Command {
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
    command
}

fn synthesize_accepted(
    runtime: &AdmittedRuntime,
    assets: &AcceptedAudioStore,
    data: &Path,
    job: &str,
    request: &BoundCueRequest,
    object: &AcceptedJsonDocument,
) -> SupervisorResult<Value> {
    let (entry, model_root) = runtime.cue_execution()?;
    let output = private_root(data, job)?;
    let scratch = output.join("temp");
    assets.read_json(object)?;
    let mut command = cue_command(runtime, &entry, &scratch);
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
    let audio = assets.accept(&generation, &candidate.path, &candidate.pin)?;
    let wave = json!({"path":audio.path,"root":audio.path.parent().unwrap(),"sha256":audio.pin.sha256,"size_bytes":audio.pin.size_bytes,
        "sample_rate":audio.pin.sample_rate,"channels":audio.pin.channels,"frame_count":audio.pin.frame_count});
    let context=assets.accept_json(&serde_json::to_string(&json!({"schema_version":1,"kind":"review_native_accepted_cue_context",
        "request_json":request.raw(),"request_sha256":request.input_hash(),"candidate_tts_json":candidate.tts,
        "candidate_spoken_text_json":candidate.spoken,"recipe_identity":candidate.recipe,"accepted_wave":wave}))?)?;
    let mut command = cue_command(runtime, &entry, &scratch);
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
    if decode(&tts, CUE_DOCUMENT_LIMIT)? != expected_tts
        || decode(&spoken, CUE_DOCUMENT_LIMIT)? != expected_spoken
    {
        return Err(invalid(
            "accepted adapter changed fields beyond WAV path/document hash",
        ));
    }
    assets.verify(&audio)?;
    assets.read_json(object)?;
    assets.read_json(&context)?;
    let tts = assets.accept_json(&tts)?;
    let spoken = assets.accept_json(&spoken)?;
    assets.read_json(&tts)?;
    assets.read_json(&spoken)?;
    assets.verify(&audio)?;
    Ok(
        json!({"audio":wave,"tts_document":descriptor(&tts),"spoken_text":descriptor(&spoken),"recipe_identity":candidate.recipe,
        "accept_context":descriptor(&context),"active_graph_published":false}),
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
            assert!(run_synthesis(args.iter().map(|arg| OsString::from(*arg))).is_err());
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
        let f = fixture();
        let case = &f["cases"][0];
        let request = admit(&packet(&f, case), &f, case).unwrap();
        let input = decode(request.raw(), 32768).unwrap();
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-cue-candidate-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir_all(root.join("control")).unwrap();
        let generation = root.join("private generation");
        std::fs::create_dir_all(generation.join("tts")).unwrap();
        let path = generation.join(format!("tts-{}.wav", "a".repeat(32)));
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
