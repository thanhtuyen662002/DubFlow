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
            &before.token.job_id,
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
