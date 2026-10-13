//! Native private B2 render dispatch and result admission; no graph mutation.
use super::review_assets::{regular, AcceptedAudioStore, AcceptedJsonDocument};
use super::review_correction::{accepted_json, candidate_scratch, cue_command, descriptor};
use super::review_document::{closed, decode, tick, AdmittedSnapshot};
use super::review_runtime::AdmittedRuntime;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::review_graph::ReviewGraphState;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
const MAX_VIDEO: u64 = 128 * 1024 * 1024 * 1024;
const ROLES: [(&str, &str); 5] = [
    ("final_video", "final_vi.mp4"),
    ("captions_srt", "captions_vi.srt"),
    ("captions_ass", "captions_vi.ass"),
    ("corrected_timeline", "timeline.json"),
    ("qc_report", "qc_report.json"),
];

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

fn verified(
    path: &Path,
    expected: &str,
    size: u64,
    capture: bool,
    deadline: Instant,
) -> SupervisorResult<Vec<u8>> {
    if size == 0 || size > MAX_VIDEO || (capture && size > LIMIT as u64) {
        return Err(invalid("private render asset exceeds its byte bound"));
    }
    let mut file = regular(path)?;
    if file.metadata()?.len() != size {
        return Err(invalid("render asset size changed"));
    }
    let mut sha = Sha256::new();
    let mut count = 0u64;
    let mut bytes = Vec::new();
    let mut block = [0u8; 32768];
    loop {
        if Instant::now() >= deadline {
            return Err(invalid("native render verification exceeded its deadline"));
        }
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        count = count
            .checked_add(n as u64)
            .ok_or_else(|| invalid("render byte count overflow"))?;
        if count > size {
            return Err(invalid("render asset grew during verification"));
        }
        sha.update(&block[..n]);
        if capture {
            bytes.extend_from_slice(&block[..n]);
        }
    }
    if count != size
        || format!("sha256:{:x}", sha.finalize()) != expected
        || regular(path)?.metadata()?.len() != size
    {
        return Err(invalid("render asset content changed"));
    }
    Ok(bytes)
}

pub(super) struct RenderInputs<'a> {
    pub(super) runtime: &'a AdmittedRuntime,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) state: &'a ReviewGraphState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) timeline: &'a AcceptedJsonDocument,
    pub(super) mixed: &'a Value,
    pub(super) output: &'a Path,
    pub(super) candidates: &'a Path,
    pub(super) start_request: &'a str,
}

fn admit_result(
    raw: &str,
    context: &Value,
    context_hash: &str,
    identity: &str,
    candidates: &Path,
) -> SupervisorResult<Value> {
    let packet = decode(raw, LIMIT)?;
    closed(
        &packet,
        &[
            "schema_version",
            "kind",
            "job_id",
            "mode",
            "context_sha256",
            "execution_identity",
            "after_snapshot_sha256",
            "artifacts",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    if packet["schema_version"].as_u64() != Some(1)
        || packet["kind"] != "review_private_render_candidate"
        || packet["job_id"] != context["job_id"]
        || packet["mode"] != "b2"
        || packet["context_sha256"] != context_hash
        || packet["execution_identity"] != identity
        || packet["after_snapshot_sha256"]
            != hash(context["after_snapshot_json"].as_str().unwrap().as_bytes())
        || packet["active_graph_published"] != false
        || packet["correction_ready"] != false
    {
        return Err(invalid(
            "render result substitutes native context, recipe or revision",
        ));
    }
    closed(&packet["artifacts"], &ROLES.map(|(role, _)| role))?;
    let generation = candidates.join(format!("render-{}", &identity[7..]));
    let deadline = Instant::now() + Duration::from_secs(720);
    let mut qc = None;
    for (role, name) in ROLES {
        let pin = &packet["artifacts"][role];
        closed(pin, &["path", "sha256", "size_bytes"])?;
        let path = PathBuf::from(
            pin["path"]
                .as_str()
                .ok_or_else(|| invalid("missing render role path"))?,
        );
        if path != generation.join(name) {
            return Err(invalid("render output escaped its exact native generation"));
        }
        let size = tick(&pin["size_bytes"], true)?;
        let bytes = verified(
            &path,
            pin["sha256"]
                .as_str()
                .ok_or_else(|| invalid("missing render hash"))?,
            size,
            role == "corrected_timeline" || role == "qc_report",
            deadline,
        )?;
        if role == "corrected_timeline"
            && bytes
                != context["corrected_timeline_json"]
                    .as_str()
                    .unwrap()
                    .as_bytes()
        {
            return Err(invalid(
                "render output substitutes the exact corrected timeline",
            ));
        }
        if role == "qc_report" {
            qc = Some(decode(
                std::str::from_utf8(&bytes).map_err(|_| invalid("render QC is not UTF-8"))?,
                LIMIT,
            )?);
        }
    }
    let qc = qc.ok_or_else(|| invalid("render QC missing"))?;
    closed(
        &qc,
        &[
            "schema_version",
            "status",
            "mode",
            "source_probe",
            "output_probe",
            "warnings",
            "full_av_decode",
            "context_sha256",
            "execution_identity",
            "active_graph_published",
        ],
    )?;
    let manifest = decode(context["original_manifest_json"].as_str().unwrap(), LIMIT)?;
    let source_duration = qc["source_probe"]["canonical_duration_ticks"]
        .as_u64()
        .ok_or_else(|| invalid("missing QC source clock"))?;
    let output_duration = qc["output_probe"]["canonical_duration_ticks"]
        .as_u64()
        .ok_or_else(|| invalid("missing QC output clock"))?;
    let h264 = qc["output_probe"]["streams"]
        .as_array()
        .is_some_and(|streams| {
            streams
                .iter()
                .any(|s| s["codec_type"] == "video" && s["codec_name"] == "h264")
        });
    let audio = qc["output_probe"]["streams"]
        .as_array()
        .is_some_and(|streams| streams.iter().any(|s| s["codec_type"] == "audio"));
    if qc["schema_version"].as_u64() != Some(1)
        || qc["status"] != "passed"
        || qc["mode"] != "b2"
        || qc["full_av_decode"] != true
        || qc["context_sha256"] != context_hash
        || qc["execution_identity"] != identity
        || qc["active_graph_published"] != false
        || qc["source_probe"] != manifest["source"]["probe"]
        || !h264
        || !audio
        || PathBuf::from(qc["output_probe"]["source_path"].as_str().unwrap_or(""))
            != generation.join("final_vi.mp4")
        || source_duration.abs_diff(output_duration) > 2000
        || !qc["warnings"].is_array()
        || manifest["warnings"].as_array().is_none_or(|warnings| {
            warnings.iter().any(|w| {
                !qc["warnings"]
                    .as_array()
                    .is_some_and(|items| items.contains(w))
            })
        })
    {
        return Err(invalid(
            "render QC does not bind the original source and completed corrected video",
        ));
    }
    Ok(packet)
}

pub(super) fn execute(input: RenderInputs<'_>) -> SupervisorResult<Value> {
    let bound = decode(&input.state.graph_json, LIMIT)?;
    let documents = &bound["accepted_documents"];
    let manifest_object = accepted_json(&documents["job_manifest"], input.assets)?;
    let timeline_object = accepted_json(&documents["editable_timeline"], input.assets)?;
    let manifest_raw = input.assets.read_json(&manifest_object)?;
    let manifest = decode(&manifest_raw, LIMIT)?;
    let original_timeline = input.assets.read_json(&timeline_object)?;
    let corrected_timeline = input.assets.read_json(input.timeline)?;
    if input.mixed["retained_audio"] != true
        || input.mixed["mix_metadata_retargeted"] != true
        || input.mixed["active_graph_published"] != false
    {
        return Err(invalid(
            "native render requires a retained admitted correction mix",
        ));
    }
    let mix_object = accepted_json(&input.mixed["mix_document"], input.assets)?;
    let composition_object = accepted_json(&input.mixed["composition"], input.assets)?;
    let mix_raw = input.assets.read_json(&mix_object)?;
    let composition_raw = input.assets.read_json(&composition_object)?;
    let source_path = PathBuf::from(
        manifest["source"]["path"]
            .as_str()
            .ok_or_else(|| invalid("original source path missing"))?,
    );
    let source_size = regular(&source_path)?.metadata()?.len();
    let source_hash = manifest["source"]["sha256"]
        .as_str()
        .ok_or_else(|| invalid("original source hash missing"))?;
    verified(
        &source_path,
        source_hash,
        source_size,
        false,
        Instant::now() + Duration::from_secs(180),
    )?;
    let burn = decode(input.start_request, LIMIT)?["burn_in_subtitles"]
        .as_bool()
        .ok_or_else(|| invalid("immutable render option missing"))?;
    let context_value = json!({"schema_version":1,"kind":"review_native_render_context","job_id":input.state.token.job_id,"mode":"b2",
        "before_snapshot_json":input.state.snapshot_json,"after_snapshot_json":input.after.json(),
        "original_manifest_json":manifest_raw,"original_manifest_sha256":input.state.token.original_manifest_sha256,
        "original_timeline_json":original_timeline,"corrected_timeline_json":corrected_timeline,
        "corrected_timeline_sha256":input.timeline.sha256,"original_output_root":input.output,
        "source":{"path":source_path,"sha256":source_hash,"size_bytes":source_size.to_string(),"artifact_root":source_path.parent()},
        "retained_mix":{"mix_document_json":mix_raw,"mix_document_sha256":mix_object.sha256,
            "composition_json":composition_raw,"composition_sha256":composition_object.sha256,"artifact_root":input.output},
        "burn_in_subtitles":burn});
    let context = input
        .assets
        .accept_json(&serde_json::to_string(&context_value)?)?;
    let (entry, probe, identity) = input.runtime.render_recipe(Some(&context.sha256))?;
    let mut command = cue_command(input.runtime, &entry, &candidate_scratch(input.candidates)?)?;
    command.args([
        OsString::from("--context"),
        context.path.clone().into_os_string(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--output-root"),
        input.candidates.as_os_str().to_owned(),
        OsString::from("--ffmpeg"),
        input.runtime.ffmpeg.clone().into_os_string(),
        OsString::from("--ffprobe"),
        probe.into_os_string(),
        OsString::from("--media-root"),
        input.runtime.root.join("runtime").into_os_string(),
    ]);
    let raw = super::review_seed::collect(command, Duration::from_secs(900), LIMIT)?;
    let candidate = admit_result(
        &raw,
        &context_value,
        &context.sha256,
        &identity,
        input.candidates,
    )?;
    if input.runtime.render_recipe(Some(&context.sha256))?.2 != identity {
        return Err(invalid("render recipe changed during execution"));
    }
    verified(
        &source_path,
        source_hash,
        source_size,
        false,
        Instant::now() + Duration::from_secs(180),
    )?;
    for document in [
        &manifest_object,
        &timeline_object,
        input.timeline,
        &mix_object,
        &composition_object,
        &context,
    ] {
        input.assets.read_json(document)?;
    }
    let mix = decode(&mix_raw, LIMIT)?;
    let mix_deadline = Instant::now() + Duration::from_secs(720);
    for role in ["original_audio", "dialogue_stem", "final_mix"] {
        let path = PathBuf::from(
            mix[role]["path"]
                .as_str()
                .ok_or_else(|| invalid("retained mix path missing"))?,
        );
        if !path.starts_with(input.output.join(".dubflow-review-media-v1")) {
            return Err(invalid(
                "render input is not retained on its admitted output volume",
            ));
        }
        verified(
            &path,
            mix[role]["content_hash"]
                .as_str()
                .ok_or_else(|| invalid("retained mix hash missing"))?,
            regular(&path)?.metadata()?.len(),
            false,
            mix_deadline,
        )?;
    }
    // These files are still private candidates. Their hashes do not authorize
    // playback publication, final-volume retention, undo or a SQLite CAS.
    let result = input
        .assets
        .accept_json(&serde_json::to_string(&candidate)?)?;
    Ok(
        json!({"context":descriptor(&context),"result":descriptor(&result),"candidate":candidate,
        "native_output_bytes_verified":true,"retained_final_output":false,"active_graph_published":false,"correction_ready":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::Ordering;

    struct Fixture {
        root: PathBuf,
        context: Value,
        raw: String,
        identity: String,
        context_hash: String,
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
    fn fixture() -> Fixture {
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-render-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let identity = hash(b"native admitted recipe");
        let context_hash = hash(b"native admitted context");
        let generation = root.join(format!("render-{}", &identity[7..]));
        let probe = json!({"canonical_duration_ticks":10000,"source_path":"original-source.mp4","streams":[{"codec_type":"video","codec_name":"h264"},{"codec_type":"audio","codec_name":"aac"}]});
        let mut output_probe = probe.clone();
        output_probe["source_path"] = json!(generation.join("final_vi.mp4"));
        let manifest = json!({"source":{"probe":probe},"warnings":["retained warning"]});
        let context = json!({"job_id":"native-render","after_snapshot_json":"{\"revision\":\"1\"}",
            "corrected_timeline_json":"{\"source_clock\":7}","original_manifest_json":manifest.to_string()});
        let qc = json!({"schema_version":1,"status":"passed","mode":"b2","source_probe":probe,"output_probe":output_probe,
            "warnings":["retained warning"],"full_av_decode":true,"context_sha256":context_hash,"execution_identity":identity,"active_graph_published":false});
        fs::create_dir(&generation).unwrap();
        let mut artifacts = serde_json::Map::new();
        for (role, name) in ROLES {
            let raw = match role {
                "corrected_timeline" => context["corrected_timeline_json"]
                    .as_str()
                    .unwrap()
                    .as_bytes()
                    .to_vec(),
                "qc_report" => qc.to_string().into_bytes(),
                _ => format!("physical {role} fixture").into_bytes(),
            };
            let path = generation.join(name);
            fs::write(&path, &raw).unwrap();
            artifacts.insert(
                role.into(),
                json!({"path":path,"sha256":hash(&raw),"size_bytes":raw.len().to_string()}),
            );
        }
        let raw = json!({"schema_version":1,"kind":"review_private_render_candidate","job_id":"native-render","mode":"b2",
            "context_sha256":context_hash,"execution_identity":identity,
            "after_snapshot_sha256":hash(context["after_snapshot_json"].as_str().unwrap().as_bytes()),
            "artifacts":artifacts,"active_graph_published":false,"correction_ready":false}).to_string();
        Fixture {
            root,
            context,
            raw,
            identity,
            context_hash,
        }
    }
    #[test]
    fn native_admission_checks_all_physical_roles_and_exact_timeline() {
        let f = fixture();
        let good = admit_result(&f.raw, &f.context, &f.context_hash, &f.identity, &f.root).unwrap();
        let path = PathBuf::from(good["artifacts"]["final_video"]["path"].as_str().unwrap());
        let mut changed = fs::read(&path).unwrap();
        changed[0] ^= 1;
        fs::write(&path, &changed).unwrap();
        assert!(admit_result(&f.raw, &f.context, &f.context_hash, &f.identity, &f.root).is_err());
        // Physical placeholders qualify hash admission only, never AV decoding.
    }
    #[test]
    fn render_result_cannot_substitute_revision_recipe_root_roles_or_qc() {
        let f = fixture();
        let original: Value = serde_json::from_str(&f.raw).unwrap();
        for index in 0..7 {
            let mut changed = original.clone();
            match index {
                0 => changed["context_sha256"] = hash(b"foreign").into(),
                1 => changed["execution_identity"] = hash(b"foreign").into(),
                2 => changed["after_snapshot_sha256"] = hash(b"foreign").into(),
                3 => {
                    changed["artifacts"]["final_video"]["path"] = json!(f.root.join("foreign.mp4"))
                }
                4 => changed["artifacts"]
                    .as_object_mut()
                    .unwrap()
                    .remove("qc_report")
                    .map(|_| ())
                    .unwrap(),
                5 => changed["correction_ready"] = true.into(),
                _ => changed["artifacts"]["captions_srt"]["size_bytes"] = "01".into(),
            }
            assert!(
                admit_result(
                    &changed.to_string(),
                    &f.context,
                    &f.context_hash,
                    &f.identity,
                    &f.root
                )
                .is_err(),
                "substitution {index}"
            );
        }
        let mut context = f.context.clone();
        context["corrected_timeline_json"] = "{\"source_clock\":8}".into();
        assert!(admit_result(&f.raw, &context, &f.context_hash, &f.identity, &f.root).is_err());
    }
    #[test]
    fn matching_qc_byte_hash_does_not_authorize_foreign_or_incomplete_evidence() {
        let f = fixture();
        let original: Value = serde_json::from_str(&f.raw).unwrap();
        let path = PathBuf::from(original["artifacts"]["qc_report"]["path"].as_str().unwrap());
        let good: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        for index in 0..6 {
            let mut qc = good.clone();
            match index {
                0 => qc["full_av_decode"] = false.into(),
                1 => qc["source_probe"]["canonical_duration_ticks"] = 20000.into(),
                2 => qc["output_probe"]["canonical_duration_ticks"] = 20000.into(),
                3 => qc["output_probe"]["streams"][0]["codec_name"] = "unknown".into(),
                4 => qc["output_probe"]["source_path"] = "foreign.mp4".into(),
                _ => qc["warnings"] = json!([]),
            }
            let bytes = qc.to_string().into_bytes();
            fs::write(&path, &bytes).unwrap();
            let mut packet = original.clone();
            packet["artifacts"]["qc_report"]["sha256"] = hash(&bytes).into();
            packet["artifacts"]["qc_report"]["size_bytes"] = bytes.len().to_string().into();
            assert!(
                admit_result(
                    &packet.to_string(),
                    &f.context,
                    &f.context_hash,
                    &f.identity,
                    &f.root
                )
                .is_err(),
                "QC substitution {index}"
            );
        }
    }
}
