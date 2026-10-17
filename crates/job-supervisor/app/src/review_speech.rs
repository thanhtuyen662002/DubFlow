//! Closed per-cue speech references for versioned native publications.
//! Recovery reads sealed accepted objects, never private synthesis checkpoints.
use super::review_assets::{AcceptedAudioStore, AcceptedCueAudio, AcceptedJsonDocument, PcmPin};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{canonical, closed, decode, tick, AdmittedSnapshot};
use super::review_request::{BoundCueRequest, CueRequestPins};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::review_graph::ReviewGraphState;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::time::Instant;

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(raw: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
}
fn exact_value(left: &Value, right: &Value) -> SupervisorResult<bool> {
    let mut left_raw = String::new();
    canonical(left, &mut left_raw)?;
    let mut right_raw = String::new();
    canonical(right, &mut right_raw)?;
    Ok(left_raw == right_raw)
}
fn time_left(deadline: Instant) -> SupervisorResult<()> {
    if Instant::now() >= deadline {
        return Err(invalid(
            "speech inventory exceeded its shared recovery deadline",
        ));
    }
    Ok(())
}

/// Input/audio/provenance binding, not a new semantic confidence claim. The
/// admitted producer's complete rewrite recipe/warnings remain unchanged.
fn spoken_binding(
    spoken: &Value,
    tts_raw: &str,
    input: &Value,
    artifact: &Value,
) -> SupervisorResult<()> {
    closed(
        spoken,
        &[
            "schema_version",
            "kind",
            "recipe",
            "recipe_hash",
            "checkpoint_identity",
            "tts_document_hash",
            "provenance",
            "source_segment_ids",
            "entries",
            "fidelity_evidence",
        ],
    )?;
    let tts = decode(tts_raw, LIMIT)?;
    if spoken["schema_version"].as_u64() != Some(1)
        || spoken["kind"] != "dubbing_spoken_text_document"
        || spoken["tts_document_hash"] != hash(tts_raw)
        || spoken["provenance"] != tts["provenance"]
        || spoken["source_segment_ids"] != tts["source_segment_ids"]
        || spoken["recipe"] != "vi-first-time-contraction-v1"
        || spoken["fidelity_evidence"]
            != "restricted-lexical-rule;semantic-confidence-not-calibrated"
    {
        return Err(invalid(
            "spoken document substitutes its accepted TTS/recipe/provenance",
        ));
    }
    let entries = spoken["entries"]
        .as_array()
        .filter(|v| v.len() <= 10000)
        .ok_or_else(|| invalid("spoken entries exceed their bound"))?;
    let entry = entries
        .iter()
        .find(|v| v["original_input"]["segment_id"] == input["segment_id"])
        .ok_or_else(|| invalid("cue lacks accepted spoken evidence"))?;
    if entries
        .iter()
        .filter(|v| v["original_input"]["segment_id"] == input["segment_id"])
        .count()
        != 1
    {
        return Err(invalid("ambiguous accepted spoken cue"));
    }
    closed(
        entry,
        &[
            "schema_version",
            "kind",
            "recipe",
            "recipe_hash",
            "checkpoint_identity",
            "original_input",
            "mode",
            "spoken_text",
            "spoken_text_hash",
            "rules",
            "artifact_hash",
            "audio_hash",
        ],
    )?;
    let text = entry["spoken_text"]
        .as_str()
        .filter(|s| !s.is_empty() && s.len() <= 32768)
        .ok_or_else(|| invalid("accepted spoken text exceeds its bound"))?;
    let mut original = String::new();
    canonical(input, &mut original)?;
    let mut actual = String::new();
    canonical(&entry["original_input"], &mut actual)?;
    if entry["schema_version"].as_u64() != Some(1)
        || entry["kind"] != "dubbing_spoken_text"
        || original != actual
        || entry["recipe"] != spoken["recipe"]
        || entry["recipe_hash"] != spoken["recipe_hash"]
        || entry["checkpoint_identity"] != spoken["checkpoint_identity"]
        || entry["spoken_text_hash"] != hash(text)
        || entry["artifact_hash"] != artifact["artifact_hash"]
        || entry["audio_hash"] != artifact["content_hash"]
    {
        return Err(invalid(
            "spoken cue substitutes input, confidence, accepted PCM or checkpoint",
        ));
    }
    if entry["mode"] == "original" {
        if entry["spoken_text"] != input["text"] || entry["rules"] != json!([]) {
            return Err(invalid("original spoken mode changes canonical text"));
        }
    } else if entry["mode"] == "rewritten" {
        let original = input["text"]
            .as_str()
            .ok_or_else(|| invalid("spoken input text missing"))?;
        let expected_warning = format!("TTS_DURATION_REWRITE_APPLIED: {}; vi-first-time-contraction-v1; original translation retained; review spoken text",
            input["segment_id"].as_str().ok_or_else(||invalid("spoken cue identity missing"))?);
        if original.matches("lần đầu tiên").count() != 1
            || text != original.replacen("lần đầu tiên", "lần đầu", 1)
            || entry["rules"] != json!(["first-time-adverb"])
            || !artifact["warnings"]
                .as_array()
                .is_some_and(|v| v.iter().any(|w| w == &expected_warning))
        {
            return Err(invalid(
                "spoken rewrite differs from its recorded bounded recipe/advisory",
            ));
        }
    } else {
        return Err(invalid(
            "unknown spoken mode requires explicit compatibility",
        ));
    }
    Ok(())
}

fn selected_documents(
    assets: &AcceptedAudioStore,
    before: &ReviewGraphState,
    proposal: &AdmittedSnapshot,
    graph: &Value,
    mix_context: &Value,
    cue: &Value,
    deadline: Instant,
    output_pin: &super::review_retention::NativeOutputPin,
) -> SupervisorResult<(AcceptedJsonDocument, AcceptedJsonDocument)> {
    time_left(deadline)?;
    let request_raw = mix_context["request_json"]
        .as_str()
        .ok_or_else(|| invalid("speech request missing"))?;
    let request = decode(request_raw, 32768)?;
    let id = proposal.finding(proposal.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("speech proposal has no canonical cue"))?;
    let input = graph["canonical_inputs"]
        .as_array()
        .and_then(|v| v.iter().find(|v| v["segment_id"] == id))
        .ok_or_else(|| invalid("speech request has no original canonical input"))?;
    let snapshot = AdmittedSnapshot::read(
        &before.snapshot_json,
        &before.token.job_id,
        before.token.revision,
    )?;
    let bound = BoundCueRequest::read(
        request_raw,
        &snapshot,
        proposal,
        CueRequestPins {
            manifest_sha256: &before.token.original_manifest_sha256,
            original_input: input,
            previous_provenance: &graph["tts_provenance"],
            config: &request["config"],
            target_voice: &request["voice"],
        },
    )?;
    if mix_context["request_sha256"] != bound.input_hash() {
        return Err(invalid("speech request checksum changed"));
    }
    read_accepted_cue(assets, &bound, cue, deadline, output_pin)
}

/// Both original and current-generation callers independently bind the request
/// before entering this physical/semantic acceptance fence. No graph authority
/// is granted by a worker packet or by a retained PCM path.
pub(super) fn read_accepted_cue(
    assets: &AcceptedAudioStore,
    bound: &BoundCueRequest,
    cue: &Value,
    deadline: Instant,
    output_pin: &super::review_retention::NativeOutputPin,
) -> SupervisorResult<(AcceptedJsonDocument, AcceptedJsonDocument)> {
    time_left(deadline)?;
    closed(
        cue,
        &[
            "audio",
            "tts_document",
            "spoken_text",
            "recipe_identity",
            "accept_context",
            "active_graph_published",
            "retained_cue_audio",
        ],
    )?;
    if cue["active_graph_published"] != false {
        return Err(invalid("accepted cue claims graph publication"));
    }
    let request_raw = bound.raw();
    let request = decode(request_raw, 32768)?;
    let id = request["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("bound speech cue identity missing"))?;
    let tts = accepted_json(&cue["tts_document"], assets)?;
    let spoken = accepted_json(&cue["spoken_text"], assets)?;
    let context = accepted_json(&cue["accept_context"], assets)?;
    let context = decode(&assets.read_json(&context)?, LIMIT)?;
    closed(
        &context,
        &[
            "schema_version",
            "kind",
            "request_json",
            "request_sha256",
            "candidate_tts_json",
            "candidate_spoken_text_json",
            "recipe_identity",
            "accepted_wave",
        ],
    )?;
    if context["schema_version"].as_u64() != Some(1)
        || context["kind"] != "review_native_accepted_cue_context"
        || context["request_json"] != request_raw
        || context["request_sha256"] != bound.input_hash()
        || context["recipe_identity"] != cue["recipe_identity"]
        || context["accepted_wave"] != cue["audio"]
    {
        return Err(invalid(
            "accepted speech context substitutes its native request/PCM",
        ));
    }
    let audio = &cue["audio"];
    closed(
        audio,
        &[
            "path",
            "root",
            "sha256",
            "size_bytes",
            "sample_rate",
            "channels",
            "frame_count",
        ],
    )?;
    let path = PathBuf::from(
        audio["path"]
            .as_str()
            .ok_or_else(|| invalid("accepted speech path missing"))?,
    );
    if path.parent()
        != Some(
            PathBuf::from(
                audio["root"]
                    .as_str()
                    .ok_or_else(|| invalid("accepted speech root missing"))?,
            )
            .as_path(),
        )
    {
        return Err(invalid("accepted speech root changed"));
    }
    let number = |key: &str| {
        audio[key]
            .as_u64()
            .ok_or_else(|| invalid("accepted speech PCM number missing"))
    };
    let accepted = AcceptedCueAudio {
        path: path.clone(),
        pin: PcmPin {
            sha256: audio["sha256"]
                .as_str()
                .ok_or_else(|| invalid("accepted speech hash missing"))?
                .into(),
            size_bytes: number("size_bytes")?,
            sample_rate: u32::try_from(number("sample_rate")?)
                .map_err(|_| invalid("accepted speech rate overflows"))?,
            channels: u16::try_from(number("channels")?)
                .map_err(|_| invalid("accepted speech channels overflow"))?,
            frame_count: number("frame_count")?,
        },
    };
    {
        let media = super::review_retention::NativeMixStore::open_existing(
            output_pin,
            request["job_id"]
                .as_str()
                .ok_or_else(|| invalid("bound speech job identity missing"))?,
            super::review_assets::MixStorageBudget {
                maximum_bytes: 32 * 1024 * 1024 * 1024,
                minimum_free_bytes: 2 * 1024 * 1024 * 1024,
            },
        )?;
        let reopened = media.reopen_cue(&cue["retained_cue_audio"], deadline)?;
        if reopened.path() != accepted.path.as_path() || reopened.pin() != &accepted.pin {
            return Err(invalid(
                "speech acceptance substitutes its canonical retained cue",
            ));
        }
    }
    let tts_raw = assets.read_json(&tts)?;
    let tts_value = decode(&tts_raw, LIMIT)?;
    let mut expected_tts = decode(
        context["candidate_tts_json"]
            .as_str()
            .ok_or_else(|| invalid("candidate speech bytes missing"))?,
        LIMIT,
    )?;
    let artifacts = expected_tts["artifacts"]
        .as_array()
        .filter(|v| v.len() == 1)
        .ok_or_else(|| invalid("accepted speech must contain one successful cue"))?;
    let artifact = &artifacts[0];
    let selected = &request["tts_input"];
    let rate = u128::from(accepted.pin.sample_rate);
    let numerator = u128::from(tick(&selected["start"]["time_base"]["numerator"], true)?);
    let denominator = u128::from(tick(&selected["start"]["time_base"]["denominator"], true)?);
    let divisor = rate
        .checked_mul(numerator)
        .filter(|v| *v > 0)
        .ok_or_else(|| invalid("accepted speech sample mapping overflow"))?;
    let product = u128::from(accepted.pin.frame_count)
        .checked_mul(denominator)
        .ok_or_else(|| invalid("accepted speech frame mapping overflow"))?;
    let delta = product / divisor + u128::from(product % divisor != 0);
    let actual_ticks = u128::from(tick(&selected["start"]["ticks"], false)?)
        .checked_add(delta)
        .filter(|v| *v <= i64::MAX as u128)
        .ok_or_else(|| invalid("accepted speech actual end overflow"))?;
    let actual_end = json!({"schema_version":1,"kind":"time_point","ticks":actual_ticks.to_string(),
        "time_base":selected["start"]["time_base"]});
    let mut provenance = request["producer_pin"].clone();
    provenance["input_hash"] = bound.input_hash().into();
    if !matches!(expected_tts["schema_version"].as_u64(), Some(1 | 2))
        || expected_tts["kind"] != "tts_document"
        || expected_tts["provenance"] != provenance
        || expected_tts["source_segment_ids"] != json!([id])
        || expected_tts["failures"] != json!([])
        || artifact["segment_id"] != id
        || artifact["source_utterance_id"] != selected["source_utterance_id"]
        || artifact["slot_start"] != selected["start"]
        || artifact["slot_end"] != selected["end"]
        || artifact.get("render_window_end") != selected.get("render_window_end")
        || artifact["confidence"].as_f64().map(f64::to_bits)
            != selected["confidence"].as_f64().map(f64::to_bits)
        || artifact["content_hash"] != audio["sha256"]
        || artifact["frame_count"] != audio["frame_count"]
        || artifact["sample_rate"] != audio["sample_rate"]
        || artifact["channels"] != audio["channels"]
        || artifact["sample_rate"] != request["config"]["sample_rate"]
        || artifact["channels"] != request["config"]["channels"]
        || artifact["actual_end"] != actual_end
        || accepted.pin.size_bytes
            > request["config"]["max_wave_bytes"]
                .as_u64()
                .ok_or_else(|| invalid("accepted speech waveform bound missing"))?
        || artifact["bits_per_sample"] != 16
        || artifact["format"] != "wav"
        || artifact["fallback_used"] != false
    {
        return Err(invalid(
            "accepted speech substitutes source/mapping/producer/PCM pins",
        ));
    }
    expected_tts["artifacts"][0]["path"] = path.to_string_lossy().into_owned().into();
    if !exact_value(&tts_value, &expected_tts)? {
        return Err(invalid(
            "accepted TTS is not the exact native path-only retarget",
        ));
    }
    let spoken_raw = assets.read_json(&spoken)?;
    let spoken_value = decode(&spoken_raw, LIMIT)?;
    let mut expected_spoken = decode(
        context["candidate_spoken_text_json"]
            .as_str()
            .ok_or_else(|| invalid("candidate spoken bytes missing"))?,
        LIMIT,
    )?;
    expected_spoken["tts_document_hash"] = tts.sha256.clone().into();
    if !exact_value(&spoken_value, &expected_spoken)? {
        return Err(invalid(
            "accepted spoken document is not the exact native hash-only retarget",
        ));
    }
    let recipe = cue["recipe_identity"]
        .as_str()
        .filter(|r| {
            r.len() == 64
                && r.bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        })
        .ok_or_else(|| invalid("accepted cue recipe identity invalid"))?;
    let mut identity = String::new();
    canonical(
        &json!({"request":bound.input_hash(),"recipe":recipe}),
        &mut identity,
    )?;
    if spoken_value["checkpoint_identity"] != format!("{:x}", Sha256::digest(identity.as_bytes())) {
        return Err(invalid("accepted spoken checkpoint identity changed"));
    }
    spoken_binding(
        &spoken_value,
        &tts_raw,
        selected,
        &tts_value["artifacts"][0],
    )?;
    time_left(deadline)?;
    Ok((tts, spoken))
}

fn build(
    assets: &AcceptedAudioStore,
    before: &ReviewGraphState,
    proposal: &AdmittedSnapshot,
    mixed: &Value,
    cue: &Value,
    deadline: Instant,
    output_pin: &super::review_retention::NativeOutputPin,
) -> SupervisorResult<Value> {
    let origin = decode(&before.graph_json, LIMIT)?;
    let graph = decode(
        origin["source_graph_json"]
            .as_str()
            .ok_or_else(|| invalid("original speech graph missing"))?,
        LIMIT,
    )?;
    if graph["mode"] != "b2" {
        return Err(invalid("B1 cannot invent speech inventory"));
    }
    let context = accepted_json(&mixed["context"], assets)?;
    let context = decode(&assets.read_json(&context)?, LIMIT)?;
    if context["before_snapshot_json"] != before.snapshot_json
        || context["after_snapshot_json"] != proposal.json()
        || context["source_graph_json"] != origin["source_graph_json"]
    {
        return Err(invalid(
            "speech inventory substitutes its native original/proposal",
        ));
    }
    let composition = accepted_json(&mixed["composition"], assets)?;
    let composition = decode(&assets.read_json(&composition)?, LIMIT)?;
    let id = proposal.finding(proposal.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("selected speech cue missing"))?;
    let (accepted_tts, accepted_spoken) = selected_documents(
        assets, before, proposal, &graph, &context, cue, deadline, output_pin,
    )?;
    let original_tts = accepted_json(&origin["accepted_documents"]["tts_document"], assets)?;
    let original_spoken =
        accepted_json(&origin["accepted_documents"]["dubbing_spoken_text"], assets)?;
    let original_tts_raw = assets.read_json(&original_tts)?;
    let original_tts_value = decode(&original_tts_raw, LIMIT)?;
    let original_spoken_value = decode(&assets.read_json(&original_spoken)?, LIMIT)?;
    super::review_effective::verify_original_speech(&graph, &original_tts_value, deadline)?;
    let inputs = graph["canonical_inputs"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("bounded original speech input inventory missing"))?;
    let cues = composition["cues"]
        .as_array()
        .filter(|v| v.len() == inputs.len())
        .ok_or_else(|| invalid("composition loses original speech assignments"))?;
    let mut rows = Vec::new();
    let by_id: std::collections::HashMap<_, _> = inputs
        .iter()
        .filter_map(|input| input["segment_id"].as_str().map(|id| (id, input)))
        .collect();
    for artifact in original_tts_value["artifacts"]
        .as_array()
        .ok_or_else(|| invalid("original TTS artifacts missing"))?
    {
        let input = artifact["segment_id"]
            .as_str()
            .and_then(|id| by_id.get(id))
            .ok_or_else(|| invalid("original spoken artifact invents a cue"))?;
        spoken_binding(&original_spoken_value, &original_tts_raw, input, artifact)?;
    }
    for (input, assignment) in inputs.iter().zip(cues) {
        if assignment["tts_input"]["segment_id"] != input["segment_id"] {
            return Err(invalid("speech composition cue order changed"));
        }
        let selected = input["segment_id"] == id;
        let (tts, spoken) = if selected {
            (&accepted_tts, &accepted_spoken)
        } else {
            (&original_tts, &original_spoken)
        };
        if assignment["document_sha256"] != tts.sha256 {
            return Err(invalid(
                "speech inventory substitutes current TTS assignment",
            ));
        }
        rows.push(json!({"cue_id":input["segment_id"],"tts_document":descriptor(tts),"spoken_text":descriptor(spoken),
            "accepted_cue":if selected {cue.clone()} else {Value::Null}}));
    }
    time_left(deadline)?;
    Ok(
        json!({"schema_version":1,"kind":"review_native_speech_inventory","job_id":before.token.job_id,
        "manifest_sha256":before.token.original_manifest_sha256,"snapshot_sha256":proposal.sha256(),"cues":rows}),
    )
}

pub(super) fn seal(
    assets: &AcceptedAudioStore,
    before: &ReviewGraphState,
    proposal: &AdmittedSnapshot,
    mixed: &Value,
    cue: &Value,
    deadline: Instant,
    output_pin: &super::review_retention::NativeOutputPin,
) -> SupervisorResult<AcceptedJsonDocument> {
    assets.accept_json(
        &build(assets, before, proposal, mixed, cue, deadline, output_pin)?.to_string(),
    )
}

pub(super) fn verify(
    assets: &AcceptedAudioStore,
    before: &ReviewGraphState,
    proposal: &AdmittedSnapshot,
    mixed: &Value,
    object: &Value,
    deadline: Instant,
    output_pin: &super::review_retention::NativeOutputPin,
) -> SupervisorResult<Value> {
    let object = accepted_json(object, assets)?;
    let value = decode(&assets.read_json(&object)?, LIMIT)?;
    closed(
        &value,
        &[
            "schema_version",
            "kind",
            "job_id",
            "manifest_sha256",
            "snapshot_sha256",
            "cues",
        ],
    )?;
    let id = proposal.finding(proposal.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("selected speech cue missing"))?;
    let rows = value["cues"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("bounded speech inventory missing"))?;
    let selected = rows
        .iter()
        .find(|v| v["cue_id"] == id)
        .ok_or_else(|| invalid("speech inventory lacks selected cue"))?;
    if value
        != build(
            assets,
            before,
            proposal,
            mixed,
            &selected["accepted_cue"],
            deadline,
            output_pin,
        )?
    {
        return Err(invalid(
            "sealed speech inventory differs from native current assignments",
        ));
    }
    Ok(value)
}

/// A current inventory is obtained only from the native effective-state reader.
/// An original document is never substituted for a prior changed assignment.
struct CurrentSpeechRows {
    rows: Value,
    composition: Value,
    view: Value,
}

fn current_rows(
    assets: &AcceptedAudioStore,
    current: &super::review_effective::AdmittedEffectiveState,
    deadline: Instant,
) -> SupervisorResult<CurrentSpeechRows> {
    time_left(deadline)?;
    let view = current.packet()?;
    if view["generation_dependencies_complete"] != true
        || view["content_composition_json"].is_null()
    {
        return Err(invalid(
            "cumulative speech requires complete admitted current B2 dependencies",
        ));
    }
    let composition = decode(
        view["effective_composition_json"]
            .as_str()
            .ok_or_else(|| invalid("current speech projection missing"))?,
        LIMIT,
    )?;
    let content = decode(
        view["content_composition_json"]
            .as_str()
            .ok_or_else(|| invalid("current speech content missing"))?,
        LIMIT,
    )?;
    let assignments = content["cues"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("current speech canonical assignment inventory missing"))?;
    let origin = decode(&current.origin().graph_json, LIMIT)?;
    let rows = if let Some(object) = view["metadata_pins"].get("speech_inventory") {
        let object = accepted_json(object, assets)?;
        let old = decode(&assets.read_json(&object)?, LIMIT)?;
        closed(
            &old,
            &[
                "schema_version",
                "kind",
                "job_id",
                "manifest_sha256",
                "snapshot_sha256",
                "cues",
            ],
        )?;
        if old["job_id"] != current.snapshot().job()
            || old["manifest_sha256"] != view["current_token"]["original_manifest_sha256"]
            || old["snapshot_sha256"]
                != hash(
                    view["composition_snapshot_json"]
                        .as_str()
                        .ok_or_else(|| invalid("current speech content snapshot missing"))?,
                )
        {
            return Err(invalid("current speech inventory changed its admitted content snapshot or original manifest"));
        }
        let old_rows = old["cues"]
            .as_array()
            .filter(|v| v.len() == assignments.len())
            .ok_or_else(|| invalid("current speech inventory incomplete"))?;
        if old["schema_version"].as_u64() == Some(1)
            && old["kind"] == "review_native_speech_inventory"
        {
            // The native current reader already verified this legacy v2
            // publication physically. Keep the old proof under its actual tag.
            let publication = accepted_json(&view["metadata_pins"]["native_publication"], assets)?;
            let record = decode(&assets.read_json(&publication)?, LIMIT)?;
            if record["schema_version"].as_u64() != Some(2)
                || record["kind"] != "review_native_publication_v2"
            {
                return Err(invalid(
                    "legacy speech needs its admitted v2 publication proof",
                ));
            }
            Value::Array(old_rows.iter().map(|row|json!({"cue_id":row["cue_id"],"tts_document":row["tts_document"],
                "spoken_text":row["spoken_text"],"generation_proof":if row["accepted_cue"].is_null(){Value::Null}else{
                    json!({"format":"legacy_publication_v2","proof":descriptor(&publication)})}})).collect())
        } else if old["schema_version"].as_u64() == Some(2)
            && old["kind"] == "review_native_speech_inventory_v2"
        {
            old["cues"].clone()
        } else {
            return Err(invalid(
                "unknown speech inventory needs explicit compatibility",
            ));
        }
    } else {
        let tts = accepted_json(&origin["accepted_documents"]["tts_document"], assets)?;
        let spoken = accepted_json(&origin["accepted_documents"]["dubbing_spoken_text"], assets)?;
        assets.read_json(&tts)?;
        assets.read_json(&spoken)?;
        Value::Array(assignments.iter().map(|assignment|json!({"cue_id":assignment["tts_input"]["segment_id"],
            "tts_document":descriptor(&tts),"spoken_text":descriptor(&spoken),"generation_proof":Value::Null})).collect())
    };
    validate_rows(&rows, &content)?;
    let mut seen = std::collections::HashSet::new();
    for row in rows.as_array().unwrap() {
        time_left(deadline)?;
        for key in ["tts_document", "spoken_text"] {
            let object = accepted_json(&row[key], assets)?;
            if seen.insert(object.sha256.clone()) {
                assets.read_json(&object)?;
            }
        }
        if !row["generation_proof"].is_null() {
            closed(&row["generation_proof"], &["format", "proof"])?;
            let object = accepted_json(&row["generation_proof"]["proof"], assets)?;
            assets.read_json(&object)?;
        }
    }
    Ok(CurrentSpeechRows {
        rows,
        composition,
        view,
    })
}

fn validate_rows(rows: &Value, composition: &Value) -> SupervisorResult<()> {
    let rows = rows
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("bounded cumulative speech rows missing"))?;
    let cues = composition["cues"]
        .as_array()
        .filter(|v| v.len() == rows.len())
        .ok_or_else(|| invalid("cumulative speech lost cue coverage"))?;
    let docs = composition["documents"]
        .as_array()
        .ok_or_else(|| invalid("cumulative speech document set missing"))?;
    let mut ids = std::collections::HashSet::new();
    for (row, cue) in rows.iter().zip(cues) {
        closed(
            row,
            &["cue_id", "tts_document", "spoken_text", "generation_proof"],
        )?;
        let id = super::review_document::identity(&row["cue_id"])?;
        if !ids.insert(id)
            || row["cue_id"] != cue["tts_input"]["segment_id"]
            || row["tts_document"]["sha256"] != cue["document_sha256"]
        {
            return Err(invalid(
                "cumulative speech reordered/invented a cue or substituted its TTS document",
            ));
        }
        for field in ["tts_document", "spoken_text"] {
            closed(&row[field], &["path", "sha256", "size_bytes"])?;
        }
        let matches: Vec<_> = docs
            .iter()
            .filter(|d| d["sha256"] == row["tts_document"]["sha256"])
            .collect();
        if matches.len() != 1
            || ["path", "sha256", "size_bytes"]
                .iter()
                .any(|key| matches[0][*key] != row["tts_document"][*key])
        {
            return Err(invalid(
                "cumulative speech pin differs from its assigned composition document",
            ));
        }
        if !row["generation_proof"].is_null() {
            closed(&row["generation_proof"], &["format", "proof"])?;
            if !matches!(
                row["generation_proof"]["format"].as_str(),
                Some("legacy_publication_v2" | "effective_speech_v1")
            ) {
                return Err(invalid("unknown cumulative speech proof format"));
            }
            closed(
                &row["generation_proof"]["proof"],
                &["path", "sha256", "size_bytes"],
            )?;
        }
    }
    Ok(())
}

fn next_rows(
    prior: &Value,
    current: &Value,
    replacement: &Value,
    selected: &str,
    tts: &Value,
    spoken: &Value,
    proof: &Value,
) -> SupervisorResult<Value> {
    validate_rows(prior, current)?;
    let mut rows = prior.clone();
    let before_cues = current["cues"].as_array().unwrap();
    let after_cues = replacement["cues"]
        .as_array()
        .filter(|v| v.len() == before_cues.len())
        .ok_or_else(|| invalid("cumulative speech replacement lost cue coverage"))?;
    for (old, new) in before_cues.iter().zip(after_cues) {
        if old["tts_input"]["segment_id"] != selected && !exact_value(old, new)? {
            return Err(invalid(
                "cumulative speech replacement changed an unrelated current cue",
            ));
        }
    }
    let mut changed = 0;
    for row in rows.as_array_mut().unwrap() {
        if row["cue_id"] == selected {
            row["tts_document"] = tts.clone();
            row["spoken_text"] = spoken.clone();
            row["generation_proof"] = json!({"format":"effective_speech_v1","proof":proof});
            changed += 1;
        }
    }
    if changed != 1 {
        return Err(invalid(
            "cumulative speech selected cue is absent or ambiguous",
        ));
    }
    validate_rows(&rows, replacement)?;
    Ok(rows)
}

pub(super) struct EffectiveSpeechInputs<'a> {
    pub(super) store: &'a dubflow_job_state::DurableStore,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) current: &'a super::review_effective::AdmittedEffectiveState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) request: &'a BoundCueRequest,
    pub(super) cue: &'a Value,
    pub(super) mixed: &'a Value,
    pub(super) processing: &'a super::review_runtime::AdmittedProcessingRuntime,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
    pub(super) deadline: Instant,
}

/// Seals complete cumulative speech references after native request/PCM/mix
/// admission. This private object is not a publication or CAS authorization.
pub(super) fn seal_effective(
    input: EffectiveSpeechInputs<'_>,
) -> SupervisorResult<AcceptedJsonDocument> {
    let EffectiveSpeechInputs {
        store,
        assets,
        current,
        after,
        request,
        cue,
        mixed,
        processing,
        output_pin,
        deadline,
    } = input;
    current
        .snapshot()
        .validate_correction(after, super::review_document::ReviewMode::Dubbed)?;
    let prior = current_rows(assets, current, deadline)?;
    let request_value = decode(request.raw(), 32768)?;
    if request_value["before_snapshot_sha256"] != current.snapshot().sha256()
        || request_value["after_snapshot_sha256"] != after.sha256()
        || request_value["manifest_sha256"]
            != prior.view["current_token"]["original_manifest_sha256"]
    {
        return Err(invalid(
            "cumulative speech request substitutes its current native token/proposal",
        ));
    }
    let (tts, spoken) = read_accepted_cue(assets, request, cue, deadline, output_pin)?;
    let audio = PathBuf::from(
        cue["audio"]["path"]
            .as_str()
            .ok_or_else(|| invalid("current cue PCM path missing"))?,
    );
    let assigned = json!({"path":tts.path,"sha256":tts.sha256,"size_bytes":tts.size_bytes,
        "artifact_root":audio.parent().ok_or_else(||invalid("current PCM root missing"))?,"metadata_root":tts.path.parent()});
    let expected =
        super::review_generation::replacement_composition(&prior.composition, request, &assigned)?;
    let composition = accepted_json(&mixed["composition"], assets)?;
    let raw = assets.read_json(&composition)?;
    if !exact_value(&expected, &decode(&raw, LIMIT)?)?
        || mixed["retained_audio"] != true
        || mixed["mix_metadata_retargeted"] != true
        || mixed["active_graph_published"] != false
    {
        return Err(invalid(
            "cumulative speech composition loses prior assignments or lacks native retention",
        ));
    }
    let candidate = accepted_json(&mixed["candidate_mix_document"], assets)?;
    let document = accepted_json(&mixed["mix_document"], assets)?;
    let media = super::review_retention::NativeMixStore::open_existing(
        output_pin,
        current.snapshot().job(),
        super::review_assets::MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    media.revalidate_admitted_document(
        &assets.read_json(&candidate)?,
        &composition.sha256,
        &mixed["retained_mix_audio"],
        &assets.read_json(&document)?,
        deadline,
    )?;
    drop(media);
    let processing_receipt = processing.cue_synthesis_receipt()?;
    let processing_object = assets.accept_json(&processing_receipt.to_string())?;
    let before = assets.accept_json(current.snapshot().json())?;
    let proposal = assets.accept_json(after.json())?;
    let bound_request = assets.accept_json(request.raw())?;
    if bound_request.sha256 != request.input_hash() {
        return Err(invalid(
            "cumulative speech request changed while sealing its proof",
        ));
    }
    let current_view = assets.accept_json(&prior.view.to_string())?;
    let before_state = super::review_publication::seal_current(store, assets, current, deadline)?;
    let baseline = assets.accept_json(&prior.composition.to_string())?;
    let proof=assets.accept_json(&json!({"schema_version":1,"kind":"review_native_effective_speech_generation",
        "current_token":prior.view["current_token"],"before_state":descriptor(&before_state),"before_effective_state":descriptor(&current_view),
        "before_snapshot":descriptor(&before),"after_snapshot":descriptor(&proposal),"bound_request":descriptor(&bound_request),
        "baseline_composition":descriptor(&baseline),"processing_synthesis":descriptor(&processing_object),"accepted_cue":cue}).to_string())?;
    let rows = next_rows(
        &prior.rows,
        &prior.composition,
        &expected,
        request_value["cue_id"]
            .as_str()
            .ok_or_else(|| invalid("selected current cue missing"))?,
        &descriptor(&tts),
        &descriptor(&spoken),
        &descriptor(&proof),
    )?;
    if !exact_value(&processing_receipt, &processing.cue_synthesis_receipt()?)? {
        return Err(invalid("cumulative speech processing release changed"));
    }
    for object in [
        &tts,
        &spoken,
        &before,
        &proposal,
        &bound_request,
        &current_view,
        &before_state,
        &baseline,
        &proof,
        &processing_object,
        &composition,
        &candidate,
        &document,
    ] {
        assets.read_json(object)?;
    }
    time_left(deadline)?;
    assets.accept_json(&json!({"schema_version":2,"kind":"review_native_speech_inventory_v2","job_id":current.snapshot().job(),
        "manifest_sha256":prior.view["current_token"]["original_manifest_sha256"],"snapshot_sha256":after.sha256(),"cues":rows}).to_string())
}

pub(super) struct EffectiveInventoryInputs<'a> {
    pub(super) store: &'a dubflow_job_state::DurableStore,
    pub(super) data: &'a std::path::Path,
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) before: &'a super::review_effective::AdmittedEffectiveState,
    pub(super) after: &'a AdmittedSnapshot,
    pub(super) mixed: &'a Value,
    pub(super) mix_packet: &'a Value,
    pub(super) processing_mix: &'a Value,
    pub(super) processing: &'a super::review_runtime::AdmittedProcessingRuntime,
    pub(super) output_pin: &'a super::review_retention::NativeOutputPin,
}

fn read_effective_generation(
    input: &EffectiveInventoryInputs<'_>,
    prior: &CurrentSpeechRows,
    proof_value: &Value,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<(
    BoundCueRequest,
    AcceptedJsonDocument,
    AcceptedJsonDocument,
    Value,
)> {
    let proof = accepted_json(proof_value, input.assets)?;
    walk.with_proof(&proof.sha256, |walk| {
        let record = decode(&walk.read_metadata(input.assets, &proof)?, LIMIT)?;
        closed(
            &record,
            &[
                "schema_version",
                "kind",
                "current_token",
                "before_state",
                "before_effective_state",
                "before_snapshot",
                "after_snapshot",
                "bound_request",
                "baseline_composition",
                "processing_synthesis",
                "accepted_cue",
            ],
        )?;
        if record["schema_version"].as_u64() != Some(1)
            || record["kind"] != "review_native_effective_speech_generation"
            || !exact_value(&record["current_token"], &prior.view["current_token"])?
        {
            return Err(invalid(
                "current speech generation proof substitutes its native before token",
            ));
        }
        let state = super::review_publication::read_reference(
            input.store,
            input.assets,
            &record["before_state"],
            input.before.origin(),
            walk,
        )?;
        // The surrounding graph reader has already physically admitted this
        // exact before-state in this phase. Its typed admission cannot come
        // from a client metadata record; no cross-request cache is consulted.
        if state.state() != input.before.state()
            || !exact_value(state.effective_view(), &prior.view)?
        {
            return Err(invalid(
                "speech generation proof replaces its admitted historical graph",
            ));
        }
        let before = accepted_json(&record["before_snapshot"], input.assets)?;
        let after = accepted_json(&record["after_snapshot"], input.assets)?;
        let view = accepted_json(&record["before_effective_state"], input.assets)?;
        let baseline = accepted_json(&record["baseline_composition"], input.assets)?;
        if walk.read_metadata(input.assets, &before)? != input.before.snapshot().json()
            || walk.read_metadata(input.assets, &after)? != input.after.json()
            || !exact_value(
                &decode(&walk.read_metadata(input.assets, &view)?, LIMIT)?,
                &prior.view,
            )?
            || !exact_value(
                &decode(&walk.read_metadata(input.assets, &baseline)?, LIMIT)?,
                &prior.composition,
            )?
        {
            return Err(invalid(
                "speech generation proof changed its native snapshots/effective baseline",
            ));
        }
        let synthesis = accepted_json(&record["processing_synthesis"], input.assets)?;
        let saved_synthesis = decode(&walk.read_metadata(input.assets, &synthesis)?, LIMIT)?;
        let actual_synthesis = input.processing.cue_synthesis_receipt()?;
        if !exact_value(&saved_synthesis, &actual_synthesis)?
            || record["accepted_cue"]["recipe_identity"] != actual_synthesis["recipe_identity"]
        {
            return Err(invalid(
                "speech generation proof substitutes source-bound processing synthesis",
            ));
        }
        let request_object = accepted_json(&record["bound_request"], input.assets)?;
        let request_raw = walk.read_metadata(input.assets, &request_object)?;
        let request_value = decode(&request_raw, 32768)?;
        let id = input.after.finding(input.after.plan_finding_id()?)?["cue_id"]
            .as_str()
            .ok_or_else(|| invalid("cumulative speech proposal lacks selected cue"))?;
        let rows = prior.composition["cues"]
            .as_array()
            .ok_or_else(|| invalid("cumulative speech baseline cue set missing"))?;
        let selected = rows
            .iter()
            .find(|row| row["tts_input"]["segment_id"] == id)
            .ok_or_else(|| invalid("current speech cue is missing from admitted baseline"))?;
        if request_value["cue_id"] != id {
            return Err(invalid(
                "speech generation proof selects another current cue",
            ));
        }
        let previous_tts = prior
            .rows
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["cue_id"] == id)
            .ok_or_else(|| invalid("current cue lacks its admitted TTS/spoken row"))?;
        let previous_tts = accepted_json(&previous_tts["tts_document"], input.assets)?;
        let previous = decode(&walk.read_metadata(input.assets, &previous_tts)?, LIMIT)?;
        let voice_id = input.after.finding(input.after.plan_finding_id()?)?["voice"]
            .as_str()
            .ok_or_else(|| invalid("current corrected voice missing"))?;
        let voice = input.processing.runtime().voice(voice_id)?;
        let request = super::review_generation::bind_current_request(
            &request_raw,
            input.before.snapshot(),
            input.after,
            CueRequestPins {
                manifest_sha256: &input.before.state().token.original_manifest_sha256,
                original_input: &selected["tts_input"],
                previous_provenance: &previous["provenance"],
                config: &request_value["config"],
                target_voice: &voice,
            },
        )?;
        if request_object.sha256 != request.input_hash() {
            return Err(invalid("speech generation request bytes changed"));
        }
        let cue = record["accepted_cue"].clone();
        let (tts, spoken) = read_accepted_cue(
            input.assets,
            &request,
            &cue,
            walk.deadline(),
            input.output_pin,
        )?;
        if !exact_value(
            &actual_synthesis,
            &input.processing.cue_synthesis_receipt()?,
        )? {
            return Err(invalid(
                "speech synthesis release changed during proof recovery",
            ));
        }
        for object in [
            &proof,
            &before,
            &after,
            &view,
            &baseline,
            &synthesis,
            &request_object,
            &previous_tts,
            &tts,
            &spoken,
        ] {
            walk.read_metadata(input.assets, object)?;
        }
        walk.check()?;
        Ok((request, tts, spoken, cue))
    })
}

/// Rebind the selected generation and every prior current cue reference. This
/// verifies inventory/media; only a separate complete graph CAS may publish.
pub(super) fn verify_effective_inventory(
    input: EffectiveInventoryInputs<'_>,
    inventory_value: &Value,
    walk: &mut super::review_publication::RecoveryWalk,
) -> SupervisorResult<Value> {
    walk.check()?;
    input
        .before
        .snapshot()
        .validate_correction(input.after, super::review_document::ReviewMode::Dubbed)?;
    let prior = current_rows(input.assets, input.before, walk.deadline())?;
    let object = accepted_json(inventory_value, input.assets)?;
    let value = decode(&walk.read_metadata(input.assets, &object)?, LIMIT)?;
    closed(
        &value,
        &[
            "schema_version",
            "kind",
            "job_id",
            "manifest_sha256",
            "snapshot_sha256",
            "cues",
        ],
    )?;
    if value["schema_version"].as_u64() != Some(2)
        || value["kind"] != "review_native_speech_inventory_v2"
        || value["job_id"] != input.after.job()
        || value["manifest_sha256"] != input.before.state().token.original_manifest_sha256
        || value["snapshot_sha256"] != input.after.sha256()
    {
        return Err(invalid(
            "current inventory substitutes its native generation/proposal",
        ));
    }
    let composition = accepted_json(&input.mixed["composition"], input.assets)?;
    let composition_raw = walk.read_metadata(input.assets, &composition)?;
    let replacement = decode(&composition_raw, LIMIT)?;
    validate_rows(&value["cues"], &replacement)?;
    let id = input.after.finding(input.after.plan_finding_id()?)?["cue_id"]
        .as_str()
        .ok_or_else(|| invalid("current inventory has no selected cue"))?;
    let selected = value["cues"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["cue_id"] == id)
        .ok_or_else(|| invalid("current inventory lost selected cue"))?;
    if selected["generation_proof"]["format"] != "effective_speech_v1" {
        return Err(invalid(
            "new current correction requires its own effective speech proof",
        ));
    }
    let proof = &selected["generation_proof"]["proof"];
    let (request, tts, spoken, cue) = read_effective_generation(&input, &prior, proof, walk)?;
    let audio = PathBuf::from(
        cue["audio"]["path"]
            .as_str()
            .ok_or_else(|| invalid("recovered cue PCM path missing"))?,
    );
    let assigned = json!({"path":tts.path,"sha256":tts.sha256,"size_bytes":tts.size_bytes,
        "artifact_root":audio.parent().ok_or_else(||invalid("recovered cue root missing"))?,"metadata_root":tts.path.parent()});
    let expected =
        super::review_generation::replacement_composition(&prior.composition, &request, &assigned)?;
    let recovered = super::review_generation::recover_current_mix(
        super::review_generation::CurrentMixRecoveryInputs {
            store: input.store,
            data: input.data,
            processing: input.processing,
            assets: input.assets,
            current: input.before,
            after: input.after,
            request: &request,
            cue: &cue,
            retained_mix: input.mixed,
            candidate_packet: input.mix_packet,
            processing_mix: input.processing_mix,
            output_pin: input.output_pin,
        },
        walk,
    )?;
    if !exact_value(&recovered, &expected)? {
        return Err(invalid(
            "current speech recovery differs from full native mix admission",
        ));
    }
    if !exact_value(&expected, &replacement)?
        || input.mixed["retained_audio"] != true
        || input.mixed["mix_metadata_retargeted"] != true
        || input.mixed["active_graph_published"] != false
    {
        return Err(invalid(
            "current inventory/mix loses prior assignments or native retention",
        ));
    }
    let candidate = accepted_json(&input.mixed["candidate_mix_document"], input.assets)?;
    let retained = accepted_json(&input.mixed["mix_document"], input.assets)?;
    let media = super::review_retention::NativeMixStore::open_existing(
        input.output_pin,
        input.after.job(),
        super::review_assets::MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    media.revalidate_admitted_document(
        &walk.read_metadata(input.assets, &candidate)?,
        &composition.sha256,
        &input.mixed["retained_mix_audio"],
        &walk.read_metadata(input.assets, &retained)?,
        walk.deadline(),
    )?;
    drop(media);
    let expected_rows = next_rows(
        &prior.rows,
        &prior.composition,
        &expected,
        id,
        &descriptor(&tts),
        &descriptor(&spoken),
        proof,
    )?;
    if !exact_value(&expected_rows, &value["cues"])? {
        return Err(invalid(
            "current inventory changed a prior TTS/spoken/proof reference",
        ));
    }
    for metadata in [&object, &composition, &candidate, &retained] {
        walk.read_metadata(input.assets, metadata)?;
    }
    walk.check()?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Value, String, Value, Value) {
        let input =
            json!({"segment_id":"cue-a","text":"Xin chào.","confidence":0.9838564395904541});
        let artifact = json!({"artifact_hash":format!("sha256:{}","a".repeat(64)),
            "content_hash":format!("sha256:{}","b".repeat(64)),"warnings":[]});
        let raw = json!({"provenance":{"voice_id":"voice-current"},"source_segment_ids":["cue-a"]})
            .to_string();
        let tts = decode(&raw, LIMIT).unwrap();
        let spoken = json!({"schema_version":1,"kind":"dubbing_spoken_text_document",
            "recipe":"vi-first-time-contraction-v1","recipe_hash":format!("sha256:{}","c".repeat(64)),
            "checkpoint_identity":"d".repeat(64),"tts_document_hash":hash(&raw),
            "provenance":tts["provenance"],"source_segment_ids":["cue-a"],
            "fidelity_evidence":"restricted-lexical-rule;semantic-confidence-not-calibrated",
            "entries":[{"schema_version":1,"kind":"dubbing_spoken_text","recipe":"vi-first-time-contraction-v1",
                "recipe_hash":format!("sha256:{}","c".repeat(64)),"checkpoint_identity":"d".repeat(64),
                "original_input":input,"mode":"original","spoken_text":"Xin chào.",
                "spoken_text_hash":hash("Xin chào."),"rules":[],"artifact_hash":artifact["artifact_hash"],
                "audio_hash":artifact["content_hash"]}]});
        (spoken, raw, input, artifact)
    }
    #[test]
    fn spoken_reference_binds_exact_input_pcm_and_source_confidence_without_rewriting() {
        let (spoken, raw, input, artifact) = fixture();
        spoken_binding(&spoken, &raw, &input, &artifact).unwrap();
        for case in 0..8 {
            let mut changed = spoken.clone();
            match case {
                0 => changed["tts_document_hash"] = hash("different").into(),
                1 => changed["provenance"]["voice_id"] = "voice-other".into(),
                2 => {
                    changed["entries"][0]["original_input"]["confidence"] = 0.983856439590454.into()
                }
                3 => changed["entries"][0]["audio_hash"] = hash("different audio").into(),
                4 => changed["entries"][0]["spoken_text"] = "Changed words".into(),
                5 => changed["entries"][0]["checkpoint_identity"] = "x".repeat(64).into(),
                6 => changed["entries"]
                    .as_array_mut()
                    .unwrap()
                    .push(spoken["entries"][0].clone()),
                _ => {
                    changed["entries"][0]
                        .as_object_mut()
                        .unwrap()
                        .insert("caller_authorized".into(), true.into());
                }
            }
            assert!(
                spoken_binding(&changed, &raw, &input, &artifact).is_err(),
                "case {case}"
            );
        }
    }

    fn cumulative_fixture() -> (Value, Value, Value, Value, Value, Value) {
        // Independent actual tiny Python DSP composition metadata; physical
        // temp media no longer exists and this is not native generation proof.
        let f = decode(
            include_str!("../../../../tests/review/native_effective_mix_v1.json"),
            LIMIT,
        )
        .unwrap();
        let current = f["current"].clone();
        let replacement = decode(f["packet"]["composition_json"].as_str().unwrap(), LIMIT).unwrap();
        let strip =
            |d: &Value| json!({"path":d["path"],"sha256":d["sha256"],"size_bytes":d["size_bytes"]});
        let dummy = |letter: &str| {
            json!({"path":format!("/native-metadata/{}.json",letter.repeat(64)),
            "sha256":format!("sha256:{}",letter.repeat(64)),"size_bytes":123})
        };
        let rows:Vec<_>=current["cues"].as_array().unwrap().iter().map(|cue| {
            let doc=current["documents"].as_array().unwrap().iter().find(|d|d["sha256"]==cue["document_sha256"]).unwrap();
            json!({"cue_id":cue["tts_input"]["segment_id"],"tts_document":strip(doc),"spoken_text":dummy("a"),
                "generation_proof":{"format":"legacy_publication_v2","proof":dummy("b")}})
        }).collect();
        (
            current,
            replacement,
            json!(rows),
            strip(&f["accepted_tts"]),
            dummy("c"),
            dummy("d"),
        )
    }

    #[test]
    fn cumulative_speech_next_cue_preserves_first_tts_spoken_and_legacy_proof_reference() {
        let (current, replacement, prior, tts, spoken, proof) = cumulative_fixture();
        let selected = replacement["cues"][1]["tts_input"]["segment_id"]
            .as_str()
            .unwrap();
        let rows = next_rows(
            &prior,
            &current,
            &replacement,
            selected,
            &tts,
            &spoken,
            &proof,
        )
        .unwrap();
        assert!(exact_value(&rows[0], &prior[0]).unwrap());
        assert_eq!(rows[1]["tts_document"], tts);
        assert_eq!(rows[1]["spoken_text"], spoken);
        assert_eq!(rows[1]["generation_proof"]["format"], "effective_speech_v1");
        assert_eq!(
            rows[0]["generation_proof"]["format"],
            "legacy_publication_v2"
        );
        assert_ne!(
            rows[0]["tts_document"]["sha256"],
            rows[1]["tts_document"]["sha256"]
        );
        assert!(exact_value(&current["cues"][0], &replacement["cues"][0]).unwrap());
    }

    #[test]
    fn same_cue_replacement_changes_only_its_generation_ref_and_keeps_other_current_rows() {
        let (current, replacement, prior, tts, spoken, proof) = cumulative_fixture();
        let rows = next_rows(
            &prior,
            &current,
            &replacement,
            "cue-b",
            &tts,
            &spoken,
            &proof,
        )
        .unwrap();
        let mut next = replacement.clone();
        let mut next_tts = tts.clone();
        next_tts["sha256"] = json!(format!("sha256:{}", "e".repeat(64)));
        next_tts["path"] = json!(format!("/native-metadata/{}.json", "e".repeat(64)));
        next["cues"][1]["document_sha256"] = next_tts["sha256"].clone();
        next["cues"][1]["tts_input"]["text"] = json!("Một sửa đổi tiếp theo.");
        next["documents"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|d| d["sha256"] == tts["sha256"])
            .unwrap()["sha256"] = next_tts["sha256"].clone();
        next["documents"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|d| d["sha256"] == next_tts["sha256"])
            .unwrap()["path"] = next_tts["path"].clone();
        let mut next_proof = proof.clone();
        next_proof["sha256"] = json!(format!("sha256:{}", "f".repeat(64)));
        let replaced = next_rows(
            &rows,
            &replacement,
            &next,
            "cue-b",
            &next_tts,
            &spoken,
            &next_proof,
        )
        .unwrap();
        assert!(exact_value(&replaced[0], &rows[0]).unwrap());
        assert_eq!(replaced[1]["generation_proof"]["proof"], next_proof);
        assert_ne!(replaced[1]["generation_proof"], rows[1]["generation_proof"]);
    }

    #[test]
    fn cumulative_speech_refuses_missing_duplicate_reordered_or_lost_prior_current_assignments() {
        let (current, replacement, prior, tts, spoken, proof) = cumulative_fixture();
        for mutation in 0..8 {
            let mut bad_prior = prior.clone();
            let mut bad_next = replacement.clone();
            match mutation {
                0 => {
                    bad_prior.as_array_mut().unwrap().pop();
                }
                1 => bad_prior[1]["cue_id"] = bad_prior[0]["cue_id"].clone(),
                2 => bad_prior.as_array_mut().unwrap().reverse(),
                3 => bad_next["cues"][0]["tts_input"]["text"] = json!("Lost previous translation"),
                4 => bad_next["cues"][0]["tts_input"]["confidence"] = json!(0.0),
                5 => bad_next["cues"][0]["document_sha256"] = tts["sha256"].clone(),
                6 => bad_prior[0]["generation_proof"]["format"] = json!("client_authorized"),
                _ => bad_prior[0]["tts_document"]["size_bytes"] = json!(1),
            }
            assert!(
                next_rows(&bad_prior, &current, &bad_next, "cue-b", &tts, &spoken, &proof).is_err(),
                "case {mutation}"
            );
        }
        assert!(next_rows(
            &prior,
            &current,
            &replacement,
            "foreign-cue",
            &tts,
            &spoken,
            &proof
        )
        .is_err());
    }
    #[test]
    fn spoken_rewrite_keeps_recorded_recipe_and_advisory_without_invented_confidence() {
        let (mut spoken, raw, mut input, mut artifact) = fixture();
        input["text"] = "Khi lần đầu tiên gặp bạn.".into();
        spoken["entries"][0]["original_input"] = input.clone();
        spoken["entries"][0]["mode"] = "rewritten".into();
        spoken["entries"][0]["spoken_text"] = "Khi lần đầu gặp bạn.".into();
        spoken["entries"][0]["spoken_text_hash"] = hash("Khi lần đầu gặp bạn.").into();
        spoken["entries"][0]["rules"] = json!(["first-time-adverb"]);
        artifact["warnings"]=json!(["TTS_DURATION_REWRITE_APPLIED: cue-a; vi-first-time-contraction-v1; original translation retained; review spoken text"]);
        spoken_binding(&spoken, &raw, &input, &artifact).unwrap();
        artifact["warnings"] = json!([]);
        assert!(spoken_binding(&spoken, &raw, &input, &artifact).is_err());
        assert_eq!(input["text"], "Khi lần đầu tiên gặp bạn.");
        assert_eq!(
            spoken["fidelity_evidence"],
            "restricted-lexical-rule;semantic-confidence-not-calibrated"
        );
    }
    #[test]
    fn exact_retarget_comparison_preserves_binary64_and_signed_zero() {
        assert!(!exact_value(
            &decode("-0.0", LIMIT).unwrap(),
            &decode("0.0", LIMIT).unwrap()
        )
        .unwrap());
        assert!(!exact_value(
            &decode("0.9168733358383179", LIMIT).unwrap(),
            &decode("0.916873335838318", LIMIT).unwrap()
        )
        .unwrap());
    }
}
