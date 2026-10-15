//! Native private B1/B2 render dispatch and result admission; no graph mutation.
use super::review_assets::{
    regular, AcceptedAudioStore, AcceptedJsonDocument, MixStorageBudget, RetainedFileFormat,
    RetainedFilePin,
};
use super::review_correction::{accepted_json, candidate_scratch, cue_command, descriptor};
use super::review_document::{canonical, closed, decode, tick, AdmittedSnapshot, ReviewMode};
use super::review_runtime::{AdmittedProcessingRuntime, AdmittedRuntime};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
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

pub(super) fn verified(
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
    pub(super) mixed: Option<&'a Value>,
    pub(super) accepted_cue: Option<&'a Value>,
    pub(super) output: &'a Path,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
    pub(super) candidates: &'a Path,
    pub(super) start_request: &'a str,
}

fn exact(value: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    canonical(value, &mut raw)?;
    Ok(raw)
}

fn ms_stamp(ms: u64, ass: bool) -> String {
    let hours = ms / 3_600_000;
    let minutes = (ms / 60_000) % 60;
    let seconds = (ms / 1000) % 60;
    if ass {
        format!("{hours}:{minutes:02}:{seconds:02}.{:02}", (ms % 1000) / 10)
    } else {
        format!("{hours:02}:{minutes:02}:{seconds:02},{:03}", ms % 1000)
    }
}

fn caption_semantics(timeline: &str, srt: &[u8], ass: &[u8]) -> SupervisorResult<()> {
    let value = decode(timeline, LIMIT)?;
    let rows = value["cues"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("current captions need complete canonical cues"))?;
    let mut expected_srt = String::new();
    let mut expected_ass = Vec::new();
    for (i, cue) in rows.iter().enumerate() {
        let start = cue["start_ms"]
            .as_u64()
            .ok_or_else(|| invalid("caption start must be integer milliseconds"))?;
        let end = cue["end_ms"]
            .as_u64()
            .ok_or_else(|| invalid("caption end must be integer milliseconds"))?;
        let text = cue["translated_text"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
            .ok_or_else(|| invalid("current caption translation missing"))?;
        expected_srt.push_str(&format!(
            "{}\n{} --> {}\n{text}\n\n",
            i + 1,
            ms_stamp(start, false),
            ms_stamp(end, false)
        ));
        expected_ass.push(format!(
            "Dialogue: 0,{},{},Default,,0,0,0,,{}",
            ms_stamp(start, true),
            ms_stamp(end, true),
            text.replace('\n', r"\\N")
        ));
    }
    if srt != expected_srt.as_bytes() && srt != expected_srt.replace('\n', "\r\n").as_bytes() {
        return Err(invalid(
            "current SRT changed canonical events or lost an earlier edit",
        ));
    }
    let ass = std::str::from_utf8(ass).map_err(|_| invalid("current ASS is not UTF-8"))?;
    let lines: Vec<_> = ass.lines().collect();
    let sections: Vec<_> = lines
        .iter()
        .enumerate()
        .filter(|(_, line)| **line == "[Events]")
        .map(|(i, _)| i)
        .collect();
    if sections.len() != 1 {
        return Err(invalid("current ASS needs one admitted event section"));
    }
    let events = &lines[sections[0] + 1..];
    if events.len() != expected_ass.len() + 1
        || events.first()
            != Some(
                &"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            )
        || events[1..]
            .iter()
            .zip(&expected_ass)
            .any(|(actual, expected)| *actual != expected)
    {
        return Err(invalid(
            "current ASS changed canonical events or lost an earlier edit",
        ));
    }
    Ok(())
}

fn effective_qc(qc: &Value) -> SupervisorResult<()> {
    let video = |probe: &Value| -> SupervisorResult<Value> {
        let videos: Vec<_> = probe["streams"]
            .as_array()
            .ok_or_else(|| invalid("current QC streams missing"))?
            .iter()
            .filter(|s| s["codec_type"] == "video")
            .collect();
        if videos.is_empty() {
            return Err(invalid("current QC video missing"));
        }
        Ok(videos[0].clone())
    };
    let source = video(&qc["source_probe"])?;
    let output = video(&qc["output_probe"])?;
    if qc["output_probe"]["streams"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| s["codec_type"] == "video")
        .count()
        != 1
        || output["codec_name"] != "h264"
    {
        return Err(invalid(
            "current QC requires exactly one admitted H.264 video",
        ));
    }
    for field in ["width", "height"] {
        if source[field].as_u64().filter(|v| *v > 0).is_none() || output[field] != source[field] {
            return Err(invalid("current render changed source geometry"));
        }
    }
    if !source["frame_rate"].is_null() {
        let rate = |v: &Value| -> SupervisorResult<(u128, u128)> {
            closed(v, &["numerator", "denominator"])?;
            let number = |name: &str| {
                v[name]
                    .as_u64()
                    .filter(|n| *n > 0 && *n <= i64::MAX as u64)
                    .map(u128::from)
                    .ok_or_else(|| invalid("current FPS needs positive source rationals"))
            };
            Ok((number("numerator")?, number("denominator")?))
        };
        let (sn, sd) = rate(&source["frame_rate"])?;
        let (on, od) = rate(&output["frame_rate"])?;
        if sn * od != on * sd {
            return Err(invalid("current render changed exact rational source FPS"));
        }
        let duration = |p: &Value| {
            p["canonical_duration_ticks"]
                .as_u64()
                .ok_or_else(|| invalid("current QC duration missing"))
        };
        let difference =
            u128::from(duration(&qc["source_probe"])?.abs_diff(duration(&qc["output_probe"])?));
        if difference * sn > 1000 * sd {
            return Err(invalid("current render duration exceeds one source frame"));
        }
    }
    if qc["output_probe"]["streams"]
        .as_array()
        .unwrap()
        .iter()
        .any(|s| s["codec_type"] == "audio" && s["codec_name"] != "aac")
    {
        return Err(invalid("current render audio is not admitted AAC"));
    }
    Ok(())
}

fn admit_effective_result_at(
    raw: &str,
    context: &Value,
    context_hash: &str,
    identity: &str,
    candidates: &Path,
    paths: Option<&HashMap<&str, PathBuf>>,
    deadline: Instant,
) -> SupervisorResult<Value> {
    if context["kind"] != "review_native_effective_render_context" {
        return Err(invalid(
            "current render requires its distinct native context tag",
        ));
    }
    let candidate = admit_result_at(
        raw,
        context,
        context_hash,
        identity,
        candidates,
        paths,
        deadline,
    )?;
    let read = |role: &str| -> SupervisorResult<Vec<u8>> {
        let pin = &candidate["artifacts"][role];
        let original = PathBuf::from(
            pin["path"]
                .as_str()
                .ok_or_else(|| invalid("current render role path missing"))?,
        );
        let path = paths
            .and_then(|p| p.get(role))
            .map(PathBuf::as_path)
            .unwrap_or(&original);
        verified(
            path,
            pin["sha256"]
                .as_str()
                .ok_or_else(|| invalid("current render role hash missing"))?,
            tick(&pin["size_bytes"], true)?,
            true,
            deadline,
        )
    };
    caption_semantics(
        context["corrected_timeline_json"]
            .as_str()
            .ok_or_else(|| invalid("current timeline missing"))?,
        &read("captions_srt")?,
        &read("captions_ass")?,
    )?;
    let before = decode(context["before_snapshot_json"].as_str().unwrap(), LIMIT)?;
    let after = decode(context["after_snapshot_json"].as_str().unwrap(), LIMIT)?;
    let id = after["last_plan"]["finding_id"]
        .as_str()
        .ok_or_else(|| invalid("current render plan missing"))?;
    let finding = |value: &Value| -> SupervisorResult<Value> {
        value["findings"]
            .as_array()
            .ok_or_else(|| invalid("current render findings missing"))?
            .iter()
            .find(|v| v["finding_id"] == id)
            .cloned()
            .ok_or_else(|| invalid("current render selected finding missing"))
    };
    if finding(&before)?["translated_text"] == finding(&after)?["translated_text"] {
        for role in ["captions_srt", "captions_ass"] {
            if candidate["artifacts"][role]["sha256"] != context["current_captions"][role]["sha256"]
                || candidate["artifacts"][role]["size_bytes"]
                    != context["current_captions"][role]["size_bytes"]
            {
                return Err(invalid(
                    "voice-only render changed exact current caption bytes",
                ));
            }
        }
    }
    effective_qc(&decode(
        std::str::from_utf8(&read("qc_report")?).map_err(|_| invalid("current QC is not UTF-8"))?,
        LIMIT,
    )?)?;
    Ok(candidate)
}

fn admit_result(
    raw: &str,
    context: &Value,
    context_hash: &str,
    identity: &str,
    candidates: &Path,
) -> SupervisorResult<Value> {
    admit_result_at(
        raw,
        context,
        context_hash,
        identity,
        candidates,
        None,
        Instant::now() + Duration::from_secs(720),
    )
}

fn admit_result_at(
    raw: &str,
    context: &Value,
    context_hash: &str,
    identity: &str,
    candidates: &Path,
    retained_paths: Option<&HashMap<&str, PathBuf>>,
    deadline: Instant,
) -> SupervisorResult<Value> {
    let mode = context["mode"]
        .as_str()
        .filter(|mode| matches!(*mode, "b1" | "b2"))
        .ok_or_else(|| invalid("native render context mode is invalid"))?;
    if (mode == "b1") != context["retained_mix"].is_null() {
        return Err(invalid(
            "native render cannot invent a B1 mix or downgrade B2",
        ));
    }
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
        || packet["mode"] != mode
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
    if retained_paths.is_some_and(|paths| paths.len() != ROLES.len()) {
        return Err(invalid("retained render recovery requires all five roles"));
    }
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
        let physical = if let Some(paths) = retained_paths {
            paths
                .get(role)
                .ok_or_else(|| invalid("retained render role is missing"))?
                .as_path()
        } else {
            path.as_path()
        };
        let bytes = verified(
            physical,
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
    let source_audio = manifest["source"]["probe"]["streams"]
        .as_array()
        .ok_or_else(|| invalid("archived source streams missing"))?
        .iter()
        .any(|stream| stream["codec_type"] == "audio");
    if qc["schema_version"].as_u64() != Some(1)
        || qc["status"] != "passed"
        || qc["mode"] != mode
        || qc["full_av_decode"] != true
        || qc["context_sha256"] != context_hash
        || qc["execution_identity"] != identity
        || qc["active_graph_published"] != false
        || qc["source_probe"] != manifest["source"]["probe"]
        || !h264
        || (mode == "b2" && !audio)
        || (mode == "b1" && audio != source_audio)
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

/// A native recovery admission, never a worker/browser playback claim. Fields
/// remain private so a graph caller cannot manufacture a preview from a hint.
pub(super) struct VerifiedRetainedRender {
    candidate: Value,
    proposal_json: String,
    final_video: PathBuf,
}

impl VerifiedRetainedRender {
    /// Prepare the snapshot a later graph transaction must publish alongside
    /// its retained outputs. The logical proposal deliberately has old preview
    /// paths; replacing every preview is necessary because rendering produces a
    /// complete video, including unchanged cues and global findings.
    pub(super) fn prepare_snapshot(
        &self,
        proposal: &AdmittedSnapshot,
    ) -> SupervisorResult<AdmittedSnapshot> {
        if proposal.json() != self.proposal_json
            || self.candidate["after_snapshot_sha256"] != proposal.sha256()
            || !self.final_video.is_absolute()
        {
            return Err(invalid(
                "retained preview differs from its admitted proposal",
            ));
        }
        let preview = self
            .final_video
            .to_str()
            .ok_or_else(|| invalid("retained preview path is not UTF-8"))?;
        super::review_document::text(&Value::String(preview.into()), 4096)?;
        let mut snapshot = decode(proposal.json(), LIMIT)?;
        for finding in snapshot["findings"].as_array_mut().unwrap() {
            finding["output_preview"] = preview.into();
        }
        // Text/voice edits, exact source ticks, confidence, warnings and the
        // invalidation plan remain those of the native logical proposal. Fresh
        // render QC is a separate sealed artifact, not invented confidence.
        AdmittedSnapshot::read(&snapshot.to_string(), proposal.job(), proposal.revision())
    }
}

/// Re-admit sealed execution/QC metadata using canonical retained files only.
/// Candidate paths remain provenance, never recovery IO or graph authority.
pub(super) struct RecoveryInputs<'a> {
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) state: &'a ReviewGraphState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) timeline: &'a AcceptedJsonDocument,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
    pub(super) candidates: &'a Path,
}

pub(super) fn reopen_retained_result(
    input: &RecoveryInputs<'_>,
    receipt: &AcceptedJsonDocument,
    context: &AcceptedJsonDocument,
    identity: &str,
    deadline: Instant,
) -> SupervisorResult<VerifiedRetainedRender> {
    let saved = decode(&input.assets.read_json(receipt)?, LIMIT)?;
    closed(
        &saved,
        &[
            "schema_version",
            "kind",
            "job_id",
            "context",
            "candidate_result",
            "execution_identity",
            "artifacts",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    if saved["schema_version"].as_u64() != Some(1)
        || saved["kind"] != "review_native_retained_render"
        || saved["job_id"] != input.state.token.job_id
        || saved["context"] != descriptor(context)
        || saved["execution_identity"] != identity
        || saved["active_graph_published"] != false
        || saved["correction_ready"] != false
    {
        return Err(invalid(
            "retained receipt substitutes native job/context/recipe",
        ));
    }
    let context_value = decode(&input.assets.read_json(context)?, LIMIT)?;
    if context_value["job_id"] != input.state.token.job_id
        || context_value["before_snapshot_json"] != input.state.snapshot_json
        || context_value["after_snapshot_json"] != input.after.json()
        || context_value["corrected_timeline_json"] != input.assets.read_json(input.timeline)?
    {
        return Err(invalid(
            "retained recovery changed native snapshot/timeline binding",
        ));
    }
    let result = accepted_json(&saved["candidate_result"], input.assets)?;
    let result_raw = input.assets.read_json(&result)?;
    let candidate = decode(&result_raw, LIMIT)?;
    let roles = saved["artifacts"]
        .as_array()
        .filter(|roles| roles.len() == ROLES.len())
        .ok_or_else(|| invalid("retained receipt requires five ordered artifacts"))?;
    let store = super::review_retention::NativeMixStore::open_existing(
        input.output_pin,
        &input.state.token.job_id,
        MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    let mut paths = HashMap::new();
    for (entry, (role, _)) in roles.iter().zip(ROLES) {
        closed(entry, &["role", "file"])?;
        let expected_format = match role {
            "final_video" => "mp4",
            "captions_srt" => "srt",
            "captions_ass" => "ass",
            _ => "json",
        };
        if entry["role"] != role
            || entry["file"]["format"] != expected_format
            || entry["file"]["locator"]["content_hash"] != candidate["artifacts"][role]["sha256"]
            || entry["file"]["locator"]["size_bytes"] != candidate["artifacts"][role]["size_bytes"]
        {
            return Err(invalid(
                "retained receipt substitutes render role/content/format",
            ));
        }
        let file = store.reopen_file(&entry["file"], deadline)?;
        paths.insert(role, file.path().to_path_buf());
    }
    let candidate = admit_result_at(
        &result_raw,
        &context_value,
        &context.sha256,
        identity,
        input.candidates,
        Some(&paths),
        deadline,
    )?;
    Ok(VerifiedRetainedRender {
        candidate,
        proposal_json: input.after.json().into(),
        final_video: paths
            .get("final_video")
            .ok_or_else(|| invalid("retained preview role is missing"))?
            .clone(),
    })
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
    let graph = decode(
        bound["source_graph_json"]
            .as_str()
            .ok_or_else(|| invalid("original mode binding missing"))?,
        LIMIT,
    )?;
    let mode = graph["mode"]
        .as_str()
        .filter(|mode| matches!(*mode, "b1" | "b2"))
        .ok_or_else(|| invalid("original mode is invalid"))?;
    if (mode == "b2") != input.mixed.is_some() {
        return Err(invalid(
            "native render cannot invent a B1 mix or downgrade B2",
        ));
    }
    let mixed = if let Some(mixed) = input.mixed {
        if mixed["retained_audio"] != true
            || mixed["mix_metadata_retargeted"] != true
            || mixed["active_graph_published"] != false
        {
            return Err(invalid(
                "native render requires a retained admitted correction mix",
            ));
        }
        let mix_object = accepted_json(&mixed["mix_document"], input.assets)?;
        let composition_object = accepted_json(&mixed["composition"], input.assets)?;
        let mix_raw = input.assets.read_json(&mix_object)?;
        let composition_raw = input.assets.read_json(&composition_object)?;
        Some((mix_object, composition_object, mix_raw, composition_raw))
    } else {
        None
    };
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
    let retained_mix = mixed.as_ref().map(|(mix_object, composition_object, mix_raw, composition_raw)| json!({
        "mix_document_json":mix_raw,"mix_document_sha256":mix_object.sha256,
        "composition_json":composition_raw,"composition_sha256":composition_object.sha256,"artifact_root":input.output
    })).unwrap_or(Value::Null);
    let context_value = json!({"schema_version":1,"kind":"review_native_render_context","job_id":input.state.token.job_id,"mode":mode,
        "before_snapshot_json":input.state.snapshot_json,"after_snapshot_json":input.after.json(),
        "original_manifest_json":manifest_raw,"original_manifest_sha256":input.state.token.original_manifest_sha256,
        "original_timeline_json":original_timeline,"corrected_timeline_json":corrected_timeline,
        "corrected_timeline_sha256":input.timeline.sha256,"original_output_root":input.output,
        "source":{"path":source_path,"sha256":source_hash,"size_bytes":source_size.to_string(),"artifact_root":source_path.parent()},
        "retained_mix":retained_mix,
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
    for document in [&manifest_object, &timeline_object, input.timeline, &context] {
        input.assets.read_json(document)?;
    }
    if let Some((mix_object, composition_object, mix_raw, _)) = &mixed {
        input.assets.read_json(&mix_object)?;
        input.assets.read_json(&composition_object)?;
        let mix_deadline = Instant::now() + Duration::from_secs(720);
        let retained_mix = input
            .mixed
            .ok_or_else(|| invalid("retained B2 mix missing"))?;
        let candidate_mix = accepted_json(&retained_mix["candidate_mix_document"], input.assets)?;
        let candidate_raw = input.assets.read_json(&candidate_mix)?;
        let media = super::review_retention::NativeMixStore::open_existing(
            input.output_pin,
            &input.state.token.job_id,
            MixStorageBudget {
                maximum_bytes: 32 * 1024 * 1024 * 1024,
                minimum_free_bytes: 2 * 1024 * 1024 * 1024,
            },
        )?;
        media.revalidate_admitted_document(
            &candidate_raw,
            &composition_object.sha256,
            &retained_mix["retained_mix_audio"],
            mix_raw,
            mix_deadline,
        )?;
    }
    // Preserve the candidate/QC provenance separately from publication paths.
    let result = input
        .assets
        .accept_json(&serde_json::to_string(&candidate)?)?;
    let retained = super::review_retention::NativeMixStore::new(
        input.output_pin,
        &input.state.token.job_id,
        MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    let generation = input.candidates.join(format!("render-{}", &identity[7..]));
    let deadline = Instant::now() + Duration::from_secs(720);
    let formats = [
        RetainedFileFormat::Mp4,
        RetainedFileFormat::Srt,
        RetainedFileFormat::Ass,
        RetainedFileFormat::Json,
        RetainedFileFormat::Json,
    ];
    let mut located = Vec::new();
    for ((role, _), format) in ROLES.into_iter().zip(formats) {
        let descriptor = &candidate["artifacts"][role];
        let pin = RetainedFilePin {
            sha256: descriptor["sha256"]
                .as_str()
                .ok_or_else(|| invalid("admitted render hash missing"))?
                .into(),
            size_bytes: tick(&descriptor["size_bytes"], true)?,
            format,
        };
        let path = PathBuf::from(
            descriptor["path"]
                .as_str()
                .ok_or_else(|| invalid("admitted render path missing"))?,
        );
        located.push((
            role,
            retained.accept_file(&generation, &path, &pin, deadline)?,
        ));
    }
    let receipt = input.assets.accept_json(&serde_json::to_string(&json!({
        "schema_version":1,"kind":"review_native_retained_render","job_id":input.state.token.job_id,
        "context":descriptor(&context),"candidate_result":descriptor(&result),"execution_identity":identity,
        "artifacts":located.iter().map(|(role,file)|json!({"role":role,"file":file.descriptor()})).collect::<Vec<_>>(),
        "active_graph_published":false,"correction_ready":false
    }))?)?;
    input.assets.read_json(&receipt)?;
    // Retention can be expensive. Re-fence the original source/recipe/control
    // inputs after it as well, rather than relying on the pre-copy observation.
    if input.runtime.render_recipe(Some(&context.sha256))?.2 != identity {
        return Err(invalid("render recipe changed during retention"));
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
        &context,
        &result,
        &receipt,
    ] {
        input.assets.read_json(document)?;
    }
    if let Some((mix_object, composition_object, mix_raw, _)) = &mixed {
        input.assets.read_json(mix_object)?;
        input.assets.read_json(composition_object)?;
        let mix = decode(mix_raw, LIMIT)?;
        let mix_deadline = Instant::now() + Duration::from_secs(720);
        for role in ["original_audio", "dialogue_stem", "final_mix"] {
            let path = PathBuf::from(
                mix[role]["path"]
                    .as_str()
                    .ok_or_else(|| invalid("retained mix path missing"))?,
            );
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
    }
    for (_, file) in &located {
        retained.verify_file(file, deadline)?;
    }
    drop(retained);
    let reopened = reopen_retained_result(
        &RecoveryInputs {
            assets: input.assets,
            state: input.state,
            after: input.after,
            timeline: input.timeline,
            output_pin: input.output_pin,
            candidates: input.candidates,
        },
        &receipt,
        &context,
        &identity,
        deadline,
    )?;
    if reopened.candidate != candidate {
        return Err(invalid(
            "reopened retained render differs from admitted candidate",
        ));
    }
    let prepared = reopened.prepare_snapshot(input.after)?;
    let prepared_snapshot = input.assets.accept_json(prepared.json())?;
    if input.assets.read_json(&prepared_snapshot)? != prepared.json() {
        return Err(invalid(
            "sealed playback snapshot differs from native preparation",
        ));
    }
    let publication = super::review_active::prepare(
        input.assets,
        input.state,
        input.after,
        input.timeline,
        &context,
        &receipt,
        &prepared_snapshot,
        &identity,
        input.output_pin.volume_id(),
        input.mixed,
        input.accepted_cue,
        Some(input.output_pin),
    )?;
    // All five roles are retained together before returning. Partial failures
    // leave only unreferenced sealed objects; no old graph/output is retired.
    Ok(
        json!({"context":descriptor(&context),"result":descriptor(&result),"candidate":candidate,
        "retained_render":descriptor(&receipt),"prepared_snapshot":descriptor(&prepared_snapshot),
        "native_publication":descriptor(&publication),
        "retained_artifacts":located.iter().map(|(role,file)|json!({"role":role,"file":file.descriptor()})).collect::<Vec<_>>(),
        "native_output_bytes_verified":true,"retained_final_output":true,"retained_receipt_reopened":true,
        "active_graph_published":false,"correction_ready":false}),
    )
}

pub(super) struct EffectiveRenderInputs<'a> {
    pub(super) processing: &'a AdmittedProcessingRuntime,
    pub(super) store: &'a DurableStore,
    pub(super) data: &'a Path,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) state: &'a ReviewGraphState,
    pub(super) current: &'a super::review_effective::AdmittedEffectiveState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) timeline: &'a AcceptedJsonDocument,
    pub(super) mixed: Option<&'a Value>,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
    pub(super) candidates: &'a Path,
}

fn storage_budget() -> MixStorageBudget {
    MixStorageBudget {
        maximum_bytes: 32 * 1024 * 1024 * 1024,
        minimum_free_bytes: 2 * 1024 * 1024 * 1024,
    }
}

fn current_caption_pins(
    input: &EffectiveRenderInputs<'_>,
    view: &Value,
    manifest: &Value,
    output: &Path,
    deadline: Instant,
) -> SupervisorResult<Value> {
    let mut pins = serde_json::Map::new();
    if let Some(descriptor_value) = view["metadata_pins"].get("retained_render") {
        let object = accepted_json(descriptor_value, input.assets)?;
        let receipt = decode(&input.assets.read_json(&object)?, LIMIT)?;
        if receipt["job_id"] != input.state.token.job_id
            || !matches!(
                receipt["kind"].as_str(),
                Some("review_native_retained_render" | "review_native_retained_effective_render")
            )
        {
            return Err(invalid(
                "current captions require native retained render provenance",
            ));
        }
        let roles = receipt["artifacts"]
            .as_array()
            .filter(|v| v.len() == ROLES.len())
            .ok_or_else(|| invalid("current retained render lost a physical role"))?;
        let media = super::review_retention::NativeMixStore::open_existing(
            input.output_pin,
            &input.state.token.job_id,
            storage_budget(),
        )?;
        for (entry, (role, _)) in roles.iter().zip(ROLES) {
            if entry["role"] != role {
                return Err(invalid("current retained render role order changed"));
            }
            let file = media.reopen_file(&entry["file"], deadline)?;
            if role == "captions_srt" || role == "captions_ass" {
                pins.insert(
                    role.into(),
                    json!({"path":file.path(),"sha256":entry["file"]["locator"]["content_hash"],
                    "size_bytes":entry["file"]["locator"]["size_bytes"]}),
                );
            }
        }
    } else {
        for role in ["captions_srt", "captions_ass"] {
            let pin = &manifest["artifacts"][role];
            let path = PathBuf::from(
                pin["path"]
                    .as_str()
                    .ok_or_else(|| invalid("original caption path missing"))?,
            );
            if !path.is_absolute() || !path.starts_with(output) {
                return Err(invalid("original caption escaped its admitted output root"));
            }
            let size = pin["size_bytes"]
                .as_u64()
                .filter(|n| *n > 0)
                .ok_or_else(|| invalid("original caption size invalid"))?;
            verified(
                &path,
                pin["sha256"]
                    .as_str()
                    .ok_or_else(|| invalid("original caption hash missing"))?,
                size,
                true,
                deadline,
            )?;
            pins.insert(
                role.into(),
                json!({"path":path,"sha256":pin["sha256"],"size_bytes":size.to_string()}),
            );
        }
    }
    pins.insert("artifact_root".into(), json!(output));
    let pins = Value::Object(pins);
    verify_caption_pins(
        &pins,
        view["current_timeline_json"]
            .as_str()
            .ok_or_else(|| invalid("current caption timeline missing"))?,
        deadline,
    )?;
    Ok(pins)
}

fn verify_caption_pins(pins: &Value, timeline: &str, deadline: Instant) -> SupervisorResult<()> {
    let mut bytes = Vec::new();
    for role in ["captions_srt", "captions_ass"] {
        let pin = &pins[role];
        let path = PathBuf::from(
            pin["path"]
                .as_str()
                .ok_or_else(|| invalid("native current caption path missing"))?,
        );
        bytes.push(verified(
            &path,
            pin["sha256"]
                .as_str()
                .ok_or_else(|| invalid("native caption hash missing"))?,
            tick(&pin["size_bytes"], true)?,
            true,
            deadline,
        )?);
    }
    caption_semantics(timeline, &bytes[0], &bytes[1])
}

fn effective_fence(input: &EffectiveRenderInputs<'_>, view: &Value) -> SupervisorResult<()> {
    if input
        .store
        .review_graph_state(&input.state.token.job_id)?
        .as_ref()
        != Some(input.state)
    {
        return Err(invalid("current render generation changed; reload"));
    }
    let reopened =
        super::review_effective::read_current(input.store, input.data, input.state)?.packet()?;
    if exact(&reopened)? != exact(view)? {
        return Err(invalid("current render physical dependencies changed"));
    }
    Ok(())
}

fn verify_effective_mix(
    input: &EffectiveRenderInputs<'_>,
    mixed: Option<&(AcceptedJsonDocument, AcceptedJsonDocument, String, String)>,
    deadline: Instant,
) -> SupervisorResult<()> {
    if let Some((mix, composition, raw, _)) = mixed {
        input.assets.read_json(mix)?;
        input.assets.read_json(composition)?;
        let retained = input
            .mixed
            .ok_or_else(|| invalid("current B2 retained mix missing"))?;
        let candidate = accepted_json(&retained["candidate_mix_document"], input.assets)?;
        let media = super::review_retention::NativeMixStore::open_existing(
            input.output_pin,
            &input.state.token.job_id,
            storage_budget(),
        )?;
        media.revalidate_admitted_document(
            &input.assets.read_json(&candidate)?,
            &composition.sha256,
            &retained["retained_mix_audio"],
            raw,
            deadline,
        )?;
    }
    Ok(())
}

fn reopen_effective_retained(
    input: &EffectiveRenderInputs<'_>,
    receipt: &AcceptedJsonDocument,
    context: &AcceptedJsonDocument,
    processing: &AcceptedJsonDocument,
    identity: &str,
    deadline: Instant,
) -> SupervisorResult<VerifiedRetainedRender> {
    let saved = decode(&input.assets.read_json(receipt)?, LIMIT)?;
    closed(
        &saved,
        &[
            "schema_version",
            "kind",
            "job_id",
            "current_token",
            "context",
            "processing_render",
            "candidate_result",
            "execution_identity",
            "artifacts",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    let view = input.current.packet()?;
    if saved["schema_version"].as_u64() != Some(1)
        || saved["kind"] != "review_native_retained_effective_render"
        || saved["job_id"] != input.state.token.job_id
        || exact(&saved["current_token"])? != exact(&view["current_token"])?
        || saved["context"] != descriptor(context)
        || saved["processing_render"] != descriptor(processing)
        || saved["execution_identity"] != identity
        || saved["active_graph_published"] != false
        || saved["correction_ready"] != false
    {
        return Err(invalid(
            "current retained receipt changed its native token/context/processing recipe",
        ));
    }
    let ctx = decode(&input.assets.read_json(context)?, LIMIT)?;
    if ctx["kind"] != "review_native_effective_render_context"
        || exact(&ctx["current_token"])? != exact(&view["current_token"])?
        || ctx["before_snapshot_json"] != input.current.snapshot().json()
        || ctx["after_snapshot_json"] != input.after.json()
        || ctx["corrected_timeline_json"] != input.assets.read_json(input.timeline)?
    {
        return Err(invalid(
            "current retained render changed its proposal or cumulative timeline",
        ));
    }
    let runtime = decode(&input.assets.read_json(processing)?, LIMIT)?;
    if exact(&runtime)?
        != exact(
            &input
                .processing
                .effective_render_receipt(Some(&context.sha256))?,
        )?
    {
        return Err(invalid(
            "current retained render processing release changed",
        ));
    }
    let result = accepted_json(&saved["candidate_result"], input.assets)?;
    let raw = input.assets.read_json(&result)?;
    let candidate = decode(&raw, LIMIT)?;
    let entries = saved["artifacts"]
        .as_array()
        .filter(|v| v.len() == ROLES.len())
        .ok_or_else(|| invalid("current retained receipt needs five ordered roles"))?;
    let media = super::review_retention::NativeMixStore::open_existing(
        input.output_pin,
        &input.state.token.job_id,
        storage_budget(),
    )?;
    let mut paths = HashMap::new();
    for (entry, (role, _)) in entries.iter().zip(ROLES) {
        closed(entry, &["role", "file"])?;
        let format = match role {
            "final_video" => "mp4",
            "captions_srt" => "srt",
            "captions_ass" => "ass",
            _ => "json",
        };
        if entry["role"] != role
            || entry["file"]["format"] != format
            || entry["file"]["locator"]["content_hash"] != candidate["artifacts"][role]["sha256"]
            || entry["file"]["locator"]["size_bytes"] != candidate["artifacts"][role]["size_bytes"]
        {
            return Err(invalid(
                "current retained render changed role/format/full byte pins",
            ));
        }
        paths.insert(
            role,
            media
                .reopen_file(&entry["file"], deadline)?
                .path()
                .to_path_buf(),
        );
    }
    let candidate = admit_effective_result_at(
        &raw,
        &ctx,
        &context.sha256,
        identity,
        input.candidates,
        Some(&paths),
        deadline,
    )?;
    Ok(VerifiedRetainedRender {
        candidate,
        proposal_json: input.after.json().into(),
        final_video: paths
            .get("final_video")
            .ok_or_else(|| invalid("current final video missing"))?
            .clone(),
    })
}

/// Build the complete context from typed current content, immutable origin and
/// freshly reopened physical dependencies. Historical recovery uses this same
/// derivation without pretending its before-state is the current SQLite head.
fn effective_context(
    input: &EffectiveRenderInputs<'_>,
    deadline: Instant,
) -> SupervisorResult<Value> {
    let phase = super::review_publication::RecoveryWalk::new(deadline);
    phase.check()?;
    let view = input.current.packet()?;
    if input.state != input.current.state() {
        return Err(invalid(
            "current render state differs from its typed content",
        ));
    }
    let seed = super::review_seed::read_original(input.store, input.data, input.current.origin())?;
    let graph = decode(&seed.graph, LIMIT)?;
    let mode = match graph["mode"].as_str() {
        Some("b1") => ReviewMode::Subtitles,
        Some("b2") => ReviewMode::Dubbed,
        _ => return Err(invalid("current native render mode invalid")),
    };
    input
        .current
        .snapshot()
        .validate_correction(input.after, mode)?;
    if (graph["mode"] == "b2") != input.mixed.is_some() {
        return Err(invalid(
            "current render cannot downgrade B2 or invent a B1 mix",
        ));
    }
    let origin = decode(&input.current.origin().graph_json, LIMIT)?;
    let manifest_object =
        accepted_json(&origin["accepted_documents"]["job_manifest"], input.assets)?;
    let original_object = accepted_json(
        &origin["accepted_documents"]["editable_timeline"],
        input.assets,
    )?;
    let current_object = accepted_json(&view["metadata_pins"]["current_timeline"], input.assets)?;
    let manifest_raw = input.assets.read_json(&manifest_object)?;
    let manifest = decode(&manifest_raw, LIMIT)?;
    let original = input.assets.read_json(&original_object)?;
    let current = input.assets.read_json(&current_object)?;
    let corrected = input.assets.read_json(input.timeline)?;
    let expected = super::review_timeline::derive_effective(
        &original,
        &seed.snapshot,
        &current,
        input.current.snapshot(),
        input.after,
        mode,
    )?;
    if exact(&decode(&corrected, LIMIT)?)? != exact(&decode(&expected, LIMIT)?)? {
        return Err(invalid(
            "current render substituted native cumulative timeline",
        ));
    }
    let old = input
        .current
        .snapshot()
        .finding(input.after.plan_finding_id()?)?;
    let new = input.after.finding(input.after.plan_finding_id()?)?;
    if old["translated_text"] == new["translated_text"] && corrected != current {
        return Err(invalid("voice-only current timeline bytes changed"));
    }
    let captions = current_caption_pins(&input, &view, &manifest, &seed.output_root, deadline)?;
    let mixed = if let Some(m) = input.mixed {
        if m["retained_audio"] != true
            || m["mix_metadata_retargeted"] != true
            || m["active_graph_published"] != false
        {
            return Err(invalid(
                "current native render requires admitted retained mix",
            ));
        }
        let doc = accepted_json(&m["mix_document"], input.assets)?;
        let comp = accepted_json(&m["composition"], input.assets)?;
        let raw = input.assets.read_json(&doc)?;
        let composition = input.assets.read_json(&comp)?;
        Some((doc, comp, raw, composition))
    } else {
        None
    };
    verify_effective_mix(input, mixed.as_ref(), deadline)?;
    let source_path = PathBuf::from(
        manifest["source"]["path"]
            .as_str()
            .ok_or_else(|| invalid("current original source path missing"))?,
    );
    let source_size = regular(&source_path)?.metadata()?.len();
    let source_hash = manifest["source"]["sha256"]
        .as_str()
        .ok_or_else(|| invalid("source hash missing"))?;
    verified(
        &source_path,
        source_hash,
        source_size,
        false,
        deadline.min(Instant::now() + Duration::from_secs(180)),
    )?;
    let start = input
        .store
        .job_start_request(&input.state.token.job_id)?
        .ok_or_else(|| invalid("current original start binding missing"))?;
    let burn = decode(&start, LIMIT)?["burn_in_subtitles"]
        .as_bool()
        .ok_or_else(|| invalid("original burn option missing"))?;
    let retained_mix=mixed.as_ref().map(|(doc,comp,raw,composition)|json!({"mix_document_json":raw,"mix_document_sha256":doc.sha256,
        "composition_json":composition,"composition_sha256":comp.sha256,"artifact_root":seed.output_root})).unwrap_or(Value::Null);
    let ctx = json!({"schema_version":1,"kind":"review_native_effective_render_context","job_id":input.state.token.job_id,"mode":graph["mode"],
        "current_token":view["current_token"],"origin_snapshot_json":seed.snapshot.json(),"before_snapshot_json":input.current.snapshot().json(),
        "after_snapshot_json":input.after.json(),"original_manifest_json":manifest_raw,"original_manifest_sha256":manifest_object.sha256,
        "original_timeline_json":original,"current_timeline_json":current,"current_timeline_sha256":current_object.sha256,
        "corrected_timeline_json":corrected,"corrected_timeline_sha256":input.timeline.sha256,"current_captions":captions,
        "original_output_root":seed.output_root,"source":{"path":source_path,"sha256":source_hash,"size_bytes":source_size.to_string(),"artifact_root":source_path.parent()},
        "retained_mix":retained_mix,"burn_in_subtitles":burn});
    for object in [
        &manifest_object,
        &original_object,
        &current_object,
        input.timeline,
    ] {
        phase.read_metadata(input.assets, object)?;
    }
    phase.check()?;
    Ok(ctx)
}

/// Physical recovery accepts no worker/client context as authority. Compare the
/// entire saved context with the same fresh native derivation used at execution
/// and reopen the five retained roles under the caller's single phase deadline.
pub(super) fn recover_effective_retained(
    input: &EffectiveRenderInputs<'_>,
    record: &Value,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<VerifiedRetainedRender> {
    walk.check()?;
    let context = accepted_json(&record["context"], input.assets)?;
    let receipt = accepted_json(&record["retained_render"], input.assets)?;
    let processing = accepted_json(&record["processing_render"], input.assets)?;
    let saved = decode(&walk.read_metadata(input.assets, &context)?, LIMIT)?;
    let expected = effective_context(input, walk.deadline())?;
    if exact(&saved)? != exact(&expected)? {
        return Err(invalid(
            "recovered render context differs from native current/original dependencies",
        ));
    }
    let recipe = input
        .processing
        .effective_render_receipt(Some(&context.sha256))?;
    let identity = super::review_document::text(&record["execution_identity"], 128)?;
    if recipe["execution_identity"] != identity
        || exact(&decode(
            &walk.read_metadata(input.assets, &processing)?,
            LIMIT,
        )?)? != exact(&recipe)?
    {
        return Err(invalid(
            "recovered render substitutes its processing execution identity",
        ));
    }
    let reopened = reopen_effective_retained(
        input,
        &receipt,
        &context,
        &processing,
        identity,
        walk.deadline(),
    )?;
    if exact(&effective_context(input, walk.deadline())?)? != exact(&expected)? {
        return Err(invalid(
            "native render dependencies changed during physical recovery",
        ));
    }
    // The sealed receipt is a byte pin. Recheck the admitted live processing
    // dependencies after physical media recovery before it can reach CAS.
    if exact(
        &input
            .processing
            .effective_render_receipt(Some(&context.sha256))?,
    )? != exact(&recipe)?
    {
        return Err(invalid(
            "processing render recipe changed during physical recovery",
        ));
    }
    for object in [&context, &receipt, &processing, input.timeline] {
        walk.read_metadata(input.assets, object)?;
    }
    walk.check()?;
    Ok(reopened)
}

pub(super) fn execute_effective(input: EffectiveRenderInputs<'_>) -> SupervisorResult<Value> {
    let view = input.current.packet()?;
    effective_fence(&input, &view)?;
    let ctx = effective_context(&input, Instant::now() + Duration::from_secs(720))?;
    let context = input.assets.accept_json(&ctx.to_string())?;
    let recipe = input
        .processing
        .effective_render_receipt(Some(&context.sha256))?;
    let identity = recipe["execution_identity"]
        .as_str()
        .ok_or_else(|| invalid("current render recipe identity missing"))?;
    let mut command = cue_command(
        input.processing.runtime(),
        &PathBuf::from(
            recipe["entry"]["path"]
                .as_str()
                .ok_or_else(|| invalid("current entry missing"))?,
        ),
        &candidate_scratch(input.candidates)?,
    )?;
    command.args([
        OsString::from("--context"),
        context.path.clone().into_os_string(),
        OsString::from("--context-sha256"),
        OsString::from(&context.sha256),
        OsString::from("--output-root"),
        input.candidates.as_os_str().to_owned(),
        OsString::from("--ffmpeg"),
        input.processing.runtime().ffmpeg.clone().into_os_string(),
        OsString::from("--ffprobe"),
        OsString::from(
            recipe["ffprobe"]["path"]
                .as_str()
                .ok_or_else(|| invalid("current probe missing"))?,
        ),
        OsString::from("--media-root"),
        input
            .processing
            .runtime()
            .root
            .join("runtime")
            .into_os_string(),
    ]);
    effective_fence(&input, &view)?;
    let raw = super::review_seed::collect(command, Duration::from_secs(900), LIMIT)?;
    let candidate = admit_effective_result_at(
        &raw,
        &ctx,
        &context.sha256,
        identity,
        input.candidates,
        None,
        Instant::now() + Duration::from_secs(720),
    )?;
    if exact(&recipe)?
        != exact(
            &input
                .processing
                .effective_render_receipt(Some(&context.sha256))?,
        )?
    {
        return Err(invalid("current render processing recipe changed"));
    }
    let result = input.assets.accept_json(&candidate.to_string())?;
    let processing = input.assets.accept_json(&recipe.to_string())?;
    let retained = super::review_retention::NativeMixStore::new(
        input.output_pin,
        &input.state.token.job_id,
        storage_budget(),
    )?;
    let generation = input.candidates.join(format!("render-{}", &identity[7..]));
    let deadline = Instant::now() + Duration::from_secs(720);
    let formats = [
        RetainedFileFormat::Mp4,
        RetainedFileFormat::Srt,
        RetainedFileFormat::Ass,
        RetainedFileFormat::Json,
        RetainedFileFormat::Json,
    ];
    let mut located = Vec::new();
    for ((role, _), format) in ROLES.into_iter().zip(formats) {
        let value = &candidate["artifacts"][role];
        let path = PathBuf::from(
            value["path"]
                .as_str()
                .ok_or_else(|| invalid("current role path missing"))?,
        );
        let pin = RetainedFilePin {
            sha256: value["sha256"]
                .as_str()
                .ok_or_else(|| invalid("current role hash missing"))?
                .into(),
            size_bytes: tick(&value["size_bytes"], true)?,
            format,
        };
        located.push((
            role,
            retained.accept_file(&generation, &path, &pin, deadline)?,
        ));
    }
    let receipt=input.assets.accept_json(&json!({"schema_version":1,"kind":"review_native_retained_effective_render","job_id":input.state.token.job_id,
        "current_token":view["current_token"],"context":descriptor(&context),"processing_render":descriptor(&processing),"candidate_result":descriptor(&result),
        "execution_identity":identity,"artifacts":located.iter().map(|(role,file)|json!({"role":role,"file":file.descriptor()})).collect::<Vec<_>>(),
        "active_graph_published":false,"correction_ready":false}).to_string())?;
    for (_, file) in &located {
        retained.verify_file(file, deadline)?;
    }
    drop(retained); // Release output writer before independent canonical reopen.
    if exact(&effective_context(
        &input,
        Instant::now() + Duration::from_secs(720),
    )?)? != exact(&ctx)?
    {
        return Err(invalid(
            "current render context changed during execution/retention",
        ));
    }
    for object in [input.timeline, &context, &result, &processing, &receipt] {
        input.assets.read_json(object)?;
    }
    effective_fence(&input, &view)?;
    let reopened = reopen_effective_retained(
        &input,
        &receipt,
        &context,
        &processing,
        identity,
        Instant::now() + Duration::from_secs(720),
    )?;
    if reopened.candidate != candidate {
        return Err(invalid("current retained candidate changed on recovery"));
    }
    let prepared = reopened.prepare_snapshot(input.after)?;
    let snapshot = input.assets.accept_json(prepared.json())?;
    effective_fence(&input, &view)?;
    Ok(
        json!({"current_token":view["current_token"],"context":descriptor(&context),"result":descriptor(&result),"candidate":candidate,
        "processing_render":descriptor(&processing),"retained_render":descriptor(&receipt),"prepared_snapshot":descriptor(&snapshot),
        "retained_artifacts":located.iter().map(|(role,file)|json!({"role":role,"file":file.descriptor()})).collect::<Vec<_>>(),
        "native_output_bytes_verified":true,"retained_final_output":true,"retained_receipt_reopened":true,
        "cumulative_publication_prepared":false,"active_graph_published":false,"correction_ready":false}),
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

    fn preview_fixture() -> (AdmittedSnapshot, VerifiedRetainedRender) {
        let source = decode(
            include_str!("../../../../tests/review/native_snapshot_v2.json"),
            LIMIT,
        )
        .unwrap();
        let before = AdmittedSnapshot::read(
            &source["snapshot"].to_string(),
            "job-native-doc",
            9007199254740994,
        )
        .unwrap();
        let proposal = before
            .correction(
                "finding-a",
                Some("Đừng cử động."),
                None,
                super::super::review_document::ReviewMode::Dubbed,
            )
            .unwrap();
        let proof = VerifiedRetainedRender {
            candidate: json!({"after_snapshot_sha256":proposal.sha256(),
                "artifacts":{"final_video":{"path":"untrusted-candidate-hint.mp4"}}}),
            proposal_json: proposal.json().into(),
            final_video: std::env::temp_dir().join("retained preview with spaces.mp4"),
        };
        (proposal, proof)
    }

    #[test]
    fn prepared_playback_uses_retained_video_for_all_findings_preserving_exact_evidence() {
        let (proposal, proof) = preview_fixture();
        let original_bytes = proposal.json().to_owned();
        let prepared = proof.prepare_snapshot(&proposal).unwrap();
        let mut expected = decode(&original_bytes, LIMIT).unwrap();
        for finding in expected["findings"].as_array_mut().unwrap() {
            finding["output_preview"] = json!(proof.final_video);
        }
        assert_eq!(decode(prepared.json(), LIMIT).unwrap(), expected);
        assert_eq!(prepared.revision(), 9007199254740995);
        assert_ne!(prepared.sha256(), proposal.sha256());
        assert_eq!(proposal.json(), original_bytes);
        // Confidence, global warnings, unrelated cue content and source ticks
        // above 2^53 are preserved by the complete-document comparison.
        let fixture = decode(
            include_str!("../../../../tests/review/native_snapshot_v2.json"),
            LIMIT,
        )
        .unwrap();
        let prior = AdmittedSnapshot::read(
            &fixture["snapshot"].to_string(),
            proposal.job(),
            9007199254740994,
        )
        .unwrap();
        let restored = prior.undo_proposal(&prepared).unwrap();
        let mut expected_prior = decode(prior.json(), LIMIT).unwrap();
        expected_prior["revision"] = 9007199254740996u64.into();
        assert_eq!(decode(restored.json(), LIMIT).unwrap(), expected_prior);
        // Pure snapshot undo only; this is not a durable graph/media undo test.
    }

    #[test]
    fn prepared_playback_refuses_foreign_proposal_hash_or_non_native_path() {
        let (proposal, _) = preview_fixture();
        let other = proposal
            .correction(
                "finding-c",
                Some("Một câu khác."),
                None,
                super::super::review_document::ReviewMode::Dubbed,
            )
            .unwrap();
        for substitution in 0..5 {
            let (_, mut proof) = preview_fixture();
            match substitution {
                0 => proof.proposal_json = other.json().into(),
                1 => proof.candidate["after_snapshot_sha256"] = hash(b"foreign").into(),
                2 => proof.final_video = PathBuf::from("relative-candidate.mp4"),
                3 => proof.final_video = std::env::temp_dir().join("bad\npreview.mp4"),
                _ => proof.candidate["after_snapshot_sha256"] = Value::Null,
            }
            assert!(
                proof.prepare_snapshot(&proposal).is_err(),
                "substitution {substitution}"
            );
        }
    }

    fn fixture() -> Fixture {
        fixture_mode("b2", true)
    }

    fn effective_oracle() -> Value {
        decode(
            include_str!("../../../../tests/review/native_effective_render_v1.json"),
            LIMIT,
        )
        .unwrap()
    }

    fn effective_fixture(index: usize) -> Fixture {
        // Physical byte/hash admission with Python subtitle metadata only.
        // No actual renderer/current CLI/retention/4h product run is simulated.
        let oracle = effective_oracle();
        let case = &oracle["cases"][index];
        let mut f = fixture_mode(case["mode"].as_str().unwrap(), true);
        f.context["kind"] = json!("review_native_effective_render_context");
        f.context["job_id"] = case["before"]["job_id"].clone();
        f.context["before_snapshot_json"] = json!(case["before"].to_string());
        f.context["after_snapshot_json"] = json!(case["after"].to_string());
        f.context["corrected_timeline_json"] = case["expected_json"].clone();
        f.context["current_token"] = case["token"].clone();
        f.context["current_timeline_json"] = case["current_json"].clone();
        f.context["current_captions"] = json!({"captions_srt":{"sha256":hash(case["current_srt"].as_str().unwrap().as_bytes()),
            "size_bytes":case["current_srt"].as_str().unwrap().len().to_string()},
            "captions_ass":{"sha256":hash(case["current_ass"].as_str().unwrap().as_bytes()),
            "size_bytes":case["current_ass"].as_str().unwrap().len().to_string()}});
        let mut manifest =
            decode(f.context["original_manifest_json"].as_str().unwrap(), LIMIT).unwrap();
        manifest["source"]["probe"]["streams"][0]["width"] = json!(320);
        manifest["source"]["probe"]["streams"][0]["height"] = json!(180);
        manifest["source"]["probe"]["streams"][0]["frame_rate"] =
            json!({"numerator":25,"denominator":1});
        f.context["original_manifest_json"] = json!(manifest.to_string());
        f.context_hash = hash(f.context.to_string().as_bytes());
        let mut packet = decode(&f.raw, LIMIT).unwrap();
        packet["job_id"] = f.context["job_id"].clone();
        packet["context_sha256"] = json!(f.context_hash);
        packet["after_snapshot_sha256"] = json!(hash(
            f.context["after_snapshot_json"]
                .as_str()
                .unwrap()
                .as_bytes()
        ));
        for (role, key) in [
            ("captions_srt", "corrected_srt"),
            ("captions_ass", "corrected_ass"),
            ("corrected_timeline", "expected_json"),
        ] {
            let raw = case[key].as_str().unwrap().as_bytes();
            let path = PathBuf::from(packet["artifacts"][role]["path"].as_str().unwrap());
            fs::write(path, raw).unwrap();
            packet["artifacts"][role]["sha256"] = json!(hash(raw));
            packet["artifacts"][role]["size_bytes"] = json!(raw.len().to_string());
        }
        let path = PathBuf::from(packet["artifacts"]["qc_report"]["path"].as_str().unwrap());
        let mut qc = decode(
            std::str::from_utf8(&fs::read(&path).unwrap()).unwrap(),
            LIMIT,
        )
        .unwrap();
        qc["context_sha256"] = json!(f.context_hash);
        qc["source_probe"] = manifest["source"]["probe"].clone();
        qc["output_probe"]["streams"] = manifest["source"]["probe"]["streams"].clone();
        let raw = qc.to_string();
        fs::write(path, raw.as_bytes()).unwrap();
        packet["artifacts"]["qc_report"]["sha256"] = json!(hash(raw.as_bytes()));
        packet["artifacts"]["qc_report"]["size_bytes"] = json!(raw.len().to_string());
        f.raw = packet.to_string();
        f
    }

    #[test]
    fn current_caption_events_match_python_oracle_and_refuse_lost_prior_text_or_changed_source_interval(
    ) {
        let f = effective_oracle();
        for case in f["cases"].as_array().unwrap() {
            let timeline = case["expected_json"].as_str().unwrap();
            let srt = case["corrected_srt"].as_str().unwrap();
            let ass = case["corrected_ass"].as_str().unwrap();
            caption_semantics(timeline, srt.as_bytes(), ass.as_bytes()).unwrap();
            caption_semantics(
                timeline,
                srt.replace('\n', "\r\n").as_bytes(),
                ass.replace('\n', "\r\n").as_bytes(),
            )
            .unwrap();
            assert!(caption_semantics(
                timeline,
                srt.replace("Chào bạn", "Xin chào").as_bytes(),
                ass.as_bytes()
            )
            .is_err());
            assert!(caption_semantics(
                timeline,
                srt.as_bytes(),
                ass.replace("Chào bạn", "Xin chào").as_bytes()
            )
            .is_err());
            assert!(caption_semantics(
                timeline,
                srt.replace("00:00:01,000", "00:00:01,001").as_bytes(),
                ass.as_bytes()
            )
            .is_err());
        }
    }

    #[test]
    fn current_qc_requires_source_geometry_exact_rational_fps_aac_and_one_source_frame() {
        let f = effective_fixture(1);
        let packet = decode(&f.raw, LIMIT).unwrap();
        let qc = decode(
            std::str::from_utf8(
                &fs::read(packet["artifacts"]["qc_report"]["path"].as_str().unwrap()).unwrap(),
            )
            .unwrap(),
            LIMIT,
        )
        .unwrap();
        effective_qc(&qc).unwrap();
        let mut equivalent = qc.clone();
        equivalent["output_probe"]["streams"][0]["frame_rate"] =
            json!({"numerator":50,"denominator":2});
        effective_qc(&equivalent).unwrap(); // Exact rational equality, no epsilon.
        let mut one_frame = qc.clone();
        one_frame["output_probe"]["canonical_duration_ticks"] = json!(10040);
        effective_qc(&one_frame).unwrap();
        for mutation in 0..7 {
            let mut bad = qc.clone();
            match mutation {
                0 => bad["output_probe"]["streams"][0]["width"] = json!(1280),
                1 => {
                    bad["output_probe"]["streams"][0]["frame_rate"] =
                        json!({"numerator":30,"denominator":1})
                }
                2 => bad["output_probe"]["canonical_duration_ticks"] = json!(10041),
                3 => bad["output_probe"]["streams"][1]["codec_name"] = json!("mp3"),
                4 => bad["output_probe"]["streams"][0]["codec_name"] = json!("hevc"),
                5 => {
                    let extra = bad["output_probe"]["streams"][0].clone();
                    bad["output_probe"]["streams"]
                        .as_array_mut()
                        .unwrap()
                        .push(extra);
                }
                _ => {
                    bad["output_probe"]["streams"][0]["frame_rate"] =
                        json!({"numerator":25,"denominator":0})
                }
            }
            assert!(effective_qc(&bad).is_err(), "QC mutation {mutation}");
        }
    }

    #[test]
    fn current_physical_packet_admission_keeps_b1_b2_and_voice_caption_bytes_without_accepting_original_tag(
    ) {
        for index in 0..3 {
            let f = effective_fixture(index);
            admit_effective_result_at(
                &f.raw,
                &f.context,
                &f.context_hash,
                &f.identity,
                &f.root,
                None,
                Instant::now() + Duration::from_secs(30),
            )
            .unwrap();
            let mut legacy = f.context.clone();
            legacy["kind"] = json!("review_native_render_context");
            assert!(admit_effective_result_at(
                &f.raw,
                &legacy,
                &f.context_hash,
                &f.identity,
                &f.root,
                None,
                Instant::now() + Duration::from_secs(30)
            )
            .is_err());
        }
    }

    #[test]
    fn current_recovery_uses_retained_paths_after_private_files_are_gone_and_refuses_checksummed_caption_loss(
    ) {
        let f = effective_fixture(2);
        let mut packet = decode(&f.raw, LIMIT).unwrap();
        let retained = f.root.join("retained");
        fs::create_dir(&retained).unwrap();
        let mut paths = HashMap::new();
        for (role, name) in ROLES {
            let original = PathBuf::from(packet["artifacts"][role]["path"].as_str().unwrap());
            assert!(original.starts_with(&f.root));
            let path = retained.join(name);
            fs::rename(&original, &path).unwrap();
            paths.insert(role, path);
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        admit_effective_result_at(
            &packet.to_string(),
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            Some(&paths),
            deadline,
        )
        .unwrap();
        assert!(admit_effective_result_at(
            &packet.to_string(),
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            None,
            deadline
        )
        .is_err());
        let path = &paths["captions_srt"];
        let lost = String::from_utf8(fs::read(path).unwrap())
            .unwrap()
            .replace("Chào bạn", "Xin chào");
        fs::write(path, lost.as_bytes()).unwrap();
        packet["artifacts"]["captions_srt"]["sha256"] = json!(hash(lost.as_bytes()));
        packet["artifacts"]["captions_srt"]["size_bytes"] = json!(lost.len().to_string());
        assert!(admit_effective_result_at(
            &packet.to_string(),
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            Some(&paths),
            deadline
        )
        .is_err());
    }
    fn fixture_mode(mode: &str, source_audio: bool) -> Fixture {
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-render-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let identity = hash(b"native admitted recipe");
        let context_hash = hash(b"native admitted context");
        let generation = root.join(format!("render-{}", &identity[7..]));
        let mut streams = vec![json!({"codec_type":"video","codec_name":"h264"})];
        if source_audio {
            streams.push(json!({"codec_type":"audio","codec_name":"aac"}));
        }
        let probe = json!({"canonical_duration_ticks":10000,"source_path":"original-source.mp4","streams":streams});
        let mut output_probe = probe.clone();
        output_probe["source_path"] = json!(generation.join("final_vi.mp4"));
        let manifest = json!({"source":{"probe":probe},"warnings":["retained warning"]});
        let context = json!({"job_id":"native-render","mode":mode,"retained_mix":if mode == "b1" {Value::Null} else {json!({"native":"retained"})},"after_snapshot_json":"{\"revision\":\"1\"}",
            "corrected_timeline_json":"{\"source_clock\":7}","original_manifest_json":manifest.to_string()});
        let qc = json!({"schema_version":1,"status":"passed","mode":mode,"source_probe":probe,"output_probe":output_probe,
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
        let raw = json!({"schema_version":1,"kind":"review_private_render_candidate","job_id":"native-render","mode":mode,
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
    fn b1_admission_preserves_audio_or_silence_without_requiring_a_mix() {
        for audio in [false, true] {
            let f = fixture_mode("b1", audio);
            let packet =
                admit_result(&f.raw, &f.context, &f.context_hash, &f.identity, &f.root).unwrap();
            assert_eq!(packet["mode"], "b1");
            let mut invented = f.context.clone();
            invented["retained_mix"] = json!({"foreign":"mix"});
            assert!(
                admit_result(&f.raw, &invented, &f.context_hash, &f.identity, &f.root).is_err()
            );
        }
        let f = fixture();
        let mut downgraded = f.context.clone();
        downgraded["retained_mix"] = Value::Null;
        assert!(admit_result(&f.raw, &downgraded, &f.context_hash, &f.identity, &f.root).is_err());
    }
    #[test]
    fn b1_qc_cannot_lose_source_audio_or_invent_audio_for_a_silent_source() {
        for source_audio in [false, true] {
            let f = fixture_mode("b1", source_audio);
            let mut packet: Value = serde_json::from_str(&f.raw).unwrap();
            let path = PathBuf::from(packet["artifacts"]["qc_report"]["path"].as_str().unwrap());
            let mut qc: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            let streams = qc["output_probe"]["streams"].as_array_mut().unwrap();
            if source_audio {
                streams.retain(|stream| stream["codec_type"] != "audio");
            } else {
                streams.push(json!({"codec_type":"audio","codec_name":"aac"}));
            }
            let raw = qc.to_string().into_bytes();
            fs::write(&path, &raw).unwrap();
            packet["artifacts"]["qc_report"]["sha256"] = hash(&raw).into();
            packet["artifacts"]["qc_report"]["size_bytes"] = raw.len().to_string().into();
            assert!(admit_result(
                &packet.to_string(),
                &f.context,
                &f.context_hash,
                &f.identity,
                &f.root
            )
            .is_err());
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
    fn recovery_admits_all_retained_roles_after_mutable_generation_is_removed() {
        let f = fixture();
        let expected =
            admit_result(&f.raw, &f.context, &f.context_hash, &f.identity, &f.root).unwrap();
        let retained = f.root.join("retained-recovery");
        fs::create_dir(&retained).unwrap();
        let mut paths = HashMap::new();
        for (role, name) in ROLES {
            let old = PathBuf::from(expected["artifacts"][role]["path"].as_str().unwrap());
            let path = retained.join(name);
            fs::copy(old, &path).unwrap();
            paths.insert(role, path);
        }
        fs::remove_dir_all(f.root.join(format!("render-{}", &f.identity[7..]))).unwrap();
        let deadline = Instant::now() + Duration::from_secs(30);
        assert_eq!(
            admit_result_at(
                &f.raw,
                &f.context,
                &f.context_hash,
                &f.identity,
                &f.root,
                Some(&paths),
                deadline
            )
            .unwrap(),
            expected
        );
        assert!(admit_result(&f.raw, &f.context, &f.context_hash, &f.identity, &f.root).is_err());
        let path = &paths["captions_ass"];
        let mut bytes = fs::read(path).unwrap();
        bytes[0] ^= 1;
        fs::write(path, bytes).unwrap();
        assert!(admit_result_at(
            &f.raw,
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            Some(&paths),
            deadline
        )
        .is_err());
        // These physical placeholders test recovery/hash authority, not codecs.
    }

    #[test]
    fn retained_recovery_rejects_partial_roles_and_semantically_foreign_qc() {
        let f = fixture_mode("b1", false);
        let mut packet = decode(&f.raw, LIMIT).unwrap();
        let mut paths = HashMap::new();
        for (role, _) in ROLES {
            paths.insert(
                role,
                PathBuf::from(packet["artifacts"][role]["path"].as_str().unwrap()),
            );
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        let mut partial = paths.clone();
        partial.remove("qc_report");
        assert!(admit_result_at(
            &f.raw,
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            Some(&partial),
            deadline
        )
        .is_err());
        let mut qc = decode(
            std::str::from_utf8(&fs::read(&paths["qc_report"]).unwrap()).unwrap(),
            LIMIT,
        )
        .unwrap();
        qc["output_probe"]["streams"]
            .as_array_mut()
            .unwrap()
            .push(json!({"codec_type":"audio","codec_name":"aac"}));
        let bytes = qc.to_string().into_bytes();
        fs::write(&paths["qc_report"], &bytes).unwrap();
        packet["artifacts"]["qc_report"]["sha256"] = hash(&bytes).into();
        packet["artifacts"]["qc_report"]["size_bytes"] = bytes.len().to_string().into();
        assert!(admit_result_at(
            &packet.to_string(),
            &f.context,
            &f.context_hash,
            &f.identity,
            &f.root,
            Some(&paths),
            deadline
        )
        .is_err());
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
