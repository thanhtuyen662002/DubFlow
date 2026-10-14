//! Read current native content and its immutable origin as separate identities.
//! No original-only guard is relaxed, producer is executed or graph is written.
use super::review_assets::{AcceptedAudioStore, PcmPin};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{canonical, closed, decode, AdmittedSnapshot};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

/// Fields cannot be manufactured by a worker/browser. Current public counters
/// remain in `current`; the revision-zero origin is archive validation only.
pub(super) struct AdmittedEffectiveState {
    current: ReviewGraphState,
    snapshot: AdmittedSnapshot,
    origin: ReviewGraphState,
    source_graph: String,
    timeline: String,
    composition: Option<String>,
    composition_snapshot: Option<String>,
    effective_composition: Option<String>,
    spoken_assignments: Option<Value>,
    metadata_pins: Value,
}

impl AdmittedEffectiveState {
    pub(super) fn snapshot(&self) -> &AdmittedSnapshot {
        &self.snapshot
    }
    pub(super) fn origin(&self) -> &ReviewGraphState {
        &self.origin
    }

    pub(super) fn packet(&self) -> SupervisorResult<Value> {
        self.packet_with_processing(None)
    }

    pub(super) fn packet_with_processing(
        &self,
        processing: Option<&super::review_runtime::AdmittedProcessingRuntime>,
    ) -> SupervisorResult<Value> {
        let processing = processing.map(|admitted| admitted.receipt()).transpose()?;
        let value = json!({"schema_version":1,"kind":"review_native_effective_state",
            "current_token":token(&self.current),"current_snapshot_json":self.snapshot.json(),
            "origin_token":token(&self.origin),"origin_graph_json":self.origin.graph_json,
            "source_graph_json":self.source_graph,"current_timeline_json":self.timeline,
            "content_composition_json":self.composition,"composition_snapshot_json":self.composition_snapshot,
            "effective_composition_json":self.effective_composition,
            "spoken_assignments":self.spoken_assignments,
            "metadata_pins":self.metadata_pins,
            "generation_dependencies_complete":self.spoken_assignments.is_some() || self.composition.is_none(),
            "processing_release_admitted":processing.is_some(),"processing_release":processing,
            "active_graph_published":false,"correction_ready":false});
        let mut encoded = String::new();
        canonical(&value, &mut encoded)?;
        Ok(value)
    }
}

fn token(state: &ReviewGraphState) -> Value {
    json!({"job_id":state.token.job_id,"revision":state.token.revision.to_string(),
        "snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "original_manifest_sha256":state.token.original_manifest_sha256})
}

fn current_fence(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("effective Review generation changed; reload"));
    }
    Ok(())
}

/// Only already admitted content may be projected onto the current public
/// snapshot. Playback paths and monotonic revisions are separate from content;
/// no cue/diagnostic/plan field or physical dependency may be changed here.
fn project_composition(
    raw: &str,
    content: &AdmittedSnapshot,
    current: &AdmittedSnapshot,
    manifest: &str,
) -> SupervisorResult<String> {
    if current.revision() < content.revision() {
        return Err(invalid(
            "composition projection cannot move the public revision backwards",
        ));
    }
    let semantic = |snapshot: &AdmittedSnapshot| -> SupervisorResult<String> {
        let mut value = decode(snapshot.json(), LIMIT)?;
        value.as_object_mut().unwrap().remove("revision");
        for finding in value["findings"].as_array_mut().unwrap() {
            finding.as_object_mut().unwrap().remove("output_preview");
        }
        let mut result = String::new();
        canonical(&value, &mut result)?;
        Ok(result)
    };
    if semantic(content)? != semantic(current)? {
        return Err(invalid(
            "composition projection changed admitted content or diagnostics",
        ));
    }
    let mut value = decode(raw, LIMIT)?;
    closed(
        &value,
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
    if !matches!(value["schema_version"].as_u64(), Some(1 | 2))
        || value["kind"] != "review_audio_composition"
        || value["job_id"] != content.job()
        || value["manifest_sha256"] != manifest
        || value["review_snapshot_sha256"] != content.sha256()
    {
        return Err(invalid(
            "composition projection substitutes its admitted immutable identity",
        ));
    }
    let mut existing = String::new();
    canonical(&value, &mut existing)?;
    if existing != raw {
        return Err(invalid(
            "composition projection requires canonical admitted bytes",
        ));
    }
    value["review_snapshot_sha256"] = current.sha256().into();
    let mut result = String::new();
    canonical(&value, &mut result)?;
    Ok(result)
}

fn same_confidence_bits(left: &Value, right: &Value) -> bool {
    match (left.as_f64(), right.as_f64()) {
        (Some(left), Some(right)) => left.to_bits() == right.to_bits(),
        _ => false,
    }
}

/// Reopen every original per-cue PCM pin. Paths come from the exact archived
/// original document, and its root comes from the native original export role.
pub(super) fn verify_original_speech(
    graph: &Value,
    speech: &Value,
    deadline: Instant,
) -> SupervisorResult<()> {
    let document = PathBuf::from(
        graph["documents"]["tts_document"]["path"]
            .as_str()
            .ok_or_else(|| invalid("original speech root missing"))?,
    );
    let root = document
        .parent()
        .ok_or_else(|| invalid("original speech parent missing"))?;
    let inputs = graph["canonical_inputs"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("bounded original cue inventory missing"))?;
    let artifacts = speech["artifacts"]
        .as_array()
        .filter(|v| v.len() <= inputs.len())
        .ok_or_else(|| invalid("original speech artifact inventory missing"))?;
    let mut by_id = std::collections::HashMap::new();
    for input in inputs {
        let id = input["segment_id"]
            .as_str()
            .ok_or_else(|| invalid("original canonical cue identity missing"))?;
        if by_id.insert(id, input).is_some() {
            return Err(invalid("duplicate original canonical cue"));
        }
    }
    let mut seen = std::collections::HashSet::new();
    for artifact in artifacts {
        let id = artifact["segment_id"]
            .as_str()
            .ok_or_else(|| invalid("original speech cue missing"))?;
        let input = by_id
            .get(id)
            .ok_or_else(|| invalid("original speech invents a canonical cue"))?;
        if !seen.insert(id)
            || artifact["source_utterance_id"] != input["source_utterance_id"]
            || artifact["slot_start"] != input["start"]
            || artifact["slot_end"] != input["end"]
            || artifact.get("render_window_end") != input.get("render_window_end")
            || !same_confidence_bits(&artifact["confidence"], &input["confidence"])
            || artifact["format"] != "wav"
            || artifact["bits_per_sample"] != 16
        {
            return Err(invalid(
                "original speech changes canonical mapping/header/confidence",
            ));
        }
        let path = PathBuf::from(
            artifact["path"]
                .as_str()
                .ok_or_else(|| invalid("original speech path missing"))?,
        );
        let pin = PcmPin {
            sha256: artifact["content_hash"]
                .as_str()
                .ok_or_else(|| invalid("original speech hash missing"))?
                .into(),
            size_bytes: super::review_assets::regular(&path)?.metadata()?.len(),
            sample_rate: artifact["sample_rate"]
                .as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .ok_or_else(|| invalid("original speech rate missing"))?,
            channels: artifact["channels"]
                .as_u64()
                .and_then(|n| u16::try_from(n).ok())
                .ok_or_else(|| invalid("original speech channels missing"))?,
            frame_count: artifact["frame_count"]
                .as_u64()
                .ok_or_else(|| invalid("original speech frames missing"))?,
        };
        super::review_assets::verify_original_cue_audio(root, &path, &pin, deadline)?;
    }
    // Failed original cues stay explicit data; no silent inference/fallback.
    let failures = speech["failures"]
        .as_array()
        .ok_or_else(|| invalid("original speech failures missing"))?;
    for failure in failures {
        let id = failure["segment_id"]
            .as_str()
            .ok_or_else(|| invalid("original failed cue missing"))?;
        if !seen.insert(id) || !by_id.contains_key(id) {
            return Err(invalid("original speech failure inventory changed"));
        }
    }
    if seen.len() != inputs.len() {
        return Err(invalid("original speech loses a canonical cue"));
    }
    Ok(())
}

pub(super) fn read_current(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<AdmittedEffectiveState> {
    current_fence(store, state)?;
    let (snapshot, publication) = super::review_active::read_active_inputs(store, data, state)?;
    let mut origin = if let Some(publication) = &publication {
        publication.before.clone()
    } else {
        // A distinct archived-origin identity; never sent as the current token.
        let zero = super::review_active::at_revision(&snapshot, 0)?;
        let mut archive = state.clone();
        archive.token.revision = 0;
        archive.snapshot_json = zero.json().into();
        archive.token.snapshot_sha256 = zero.sha256();
        archive
    };
    if origin.token.revision != 0 || origin.undo_depth != 0 || origin.undo_target_revision.is_some()
    {
        return Err(invalid(
            "effective origin is not the admitted original archive",
        ));
    }
    let seed = super::review_seed::read_original(store, data, &origin)?;
    origin.snapshot_json = seed.snapshot.json().into();
    origin.token.snapshot_sha256 = seed.snapshot.sha256();
    let graph = decode(&seed.graph, LIMIT)?;
    let bound = decode(&origin.graph_json, LIMIT)?;
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let original_timeline =
        accepted_json(&bound["accepted_documents"]["editable_timeline"], &assets)?;
    let timeline_object = if let Some(publication) = &publication {
        accepted_json(&publication.record["corrected_timeline"], &assets)?
    } else {
        original_timeline.clone()
    };
    let timeline = assets.read_json(&timeline_object)?;
    let (composition, composition_snapshot, spoken_assignments, mut metadata_pins) = if graph
        ["mode"]
        == "b2"
    {
        if let Some(publication) = &publication {
            let object = accepted_json(&publication.record["mixed"]["composition"], &assets)?;
            let proposal = accepted_json(&publication.record["proposal"], &assets)?;
            let (spoken, pins) = if publication.record["schema_version"] == 2 {
                let inventory = accepted_json(&publication.record["speech_inventory"], &assets)?;
                let value = decode(&assets.read_json(&inventory)?, LIMIT)?;
                let assignments:Vec<Value>=value["cues"].as_array()
                    .ok_or_else(||invalid("accepted current speech assignments missing"))?.iter()
                    .map(|row|json!({"cue_id":row["cue_id"],"tts_document_sha256":row["tts_document"]["sha256"],
                        "spoken_text":row["spoken_text"]})).collect();
                (Some(json!(assignments)), Some(descriptor(&inventory)))
            } else {
                (None, None)
            };
            let mut metadata = json!({"composition":descriptor(&object),"composition_snapshot":descriptor(&proposal)});
            if let Some(pins) = pins {
                metadata
                    .as_object_mut()
                    .unwrap()
                    .insert("speech_inventory".into(), pins);
            }
            (
                Some(assets.read_json(&object)?),
                Some(assets.read_json(&proposal)?),
                spoken,
                metadata,
            )
        } else {
            let speech = accepted_json(&bound["accepted_documents"]["tts_document"], &assets)?;
            let spoken =
                accepted_json(&bound["accepted_documents"]["dubbing_spoken_text"], &assets)?;
            let template = accepted_json(&bound["accepted_documents"]["mix_document"], &assets)?;
            let manifest = accepted_json(&bound["accepted_documents"]["job_manifest"], &assets)?;
            let speech_raw = assets.read_json(&speech)?;
            let deadline = Instant::now() + Duration::from_secs(720);
            verify_original_speech(&graph, &decode(&speech_raw, LIMIT)?, deadline)?;
            let mix = decode(&assets.read_json(&template)?, LIMIT)?;
            let original_manifest = decode(&assets.read_json(&manifest)?, LIMIT)?;
            let source = &original_manifest["artifacts"]["source_audio"];
            closed(source, &["path", "sha256", "size_bytes"])?;
            let root = PathBuf::from(
                graph["documents"]["tts_document"]["path"]
                    .as_str()
                    .ok_or_else(|| invalid("original speech root missing"))?,
            )
            .parent()
            .ok_or_else(|| invalid("original speech parent missing"))?
            .to_path_buf();
            let source_path = PathBuf::from(
                source["path"]
                    .as_str()
                    .ok_or_else(|| invalid("original source PCM path missing"))?,
            );
            if !source_path.is_absolute()
                || !source_path.starts_with(&root)
                || mix["provenance"]["source_hash"] != source["sha256"]
            {
                return Err(invalid(
                    "current composition substitutes original source PCM binding",
                ));
            }
            super::review_render::verified(
                &source_path,
                source["sha256"]
                    .as_str()
                    .ok_or_else(|| invalid("original source PCM hash missing"))?,
                source["size_bytes"]
                    .as_u64()
                    .filter(|n| *n > 0)
                    .ok_or_else(|| invalid("original source PCM size missing"))?,
                false,
                deadline,
            )?;
            let speech_pin = json!({"path":speech.path,"sha256":speech.sha256,"size_bytes":speech.size_bytes,
                "artifact_root":root,"metadata_root":speech.path.parent()});
            let mut producer = mix["provenance"]
                .as_object()
                .ok_or_else(|| invalid("original mix producer missing"))?
                .clone();
            producer.remove("input_hash");
            producer.remove("source_hash");
            let inputs = graph["canonical_inputs"].as_array().unwrap();
            let value = json!({"schema_version":2,"kind":"review_audio_composition","job_id":state.token.job_id,
                "manifest_sha256":state.token.original_manifest_sha256,"review_snapshot_sha256":snapshot.sha256(),
                "source":{"path":source["path"],"sha256":source["sha256"],"start":mix["source_start"],
                    "end":mix["source_end"],"source_id":mix["source_id"],"layout":mix["source_layout"]},
                "documents":[speech_pin],"cues":inputs.iter().map(|v|json!({"tts_input":v,
                    "document_sha256":speech.sha256})).collect::<Vec<_>>(),"mix_producer_pin":producer});
            let mut composition = String::new();
            canonical(&value, &mut composition)?;
            let assignments: Vec<Value> = inputs
                .iter()
                .map(|v| {
                    json!({"cue_id":v["segment_id"],
                "tts_document_sha256":speech.sha256,"spoken_text":descriptor(&spoken)})
                })
                .collect();
            (
                Some(composition),
                Some(snapshot.json().into()),
                Some(json!(assignments)),
                json!({"tts_document":descriptor(&speech),
                "spoken_text":descriptor(&spoken),"mix_template":descriptor(&template),"job_manifest":descriptor(&manifest)}),
            )
        }
    } else {
        (
            None,
            None,
            None,
            json!({"original_timeline":descriptor(&original_timeline)}),
        )
    };
    metadata_pins
        .as_object_mut()
        .unwrap()
        .insert("current_timeline".into(), descriptor(&timeline_object));
    if let Some(publication) = &publication {
        metadata_pins.as_object_mut().unwrap().insert(
            "native_publication".into(),
            decode(&state.graph_json, LIMIT)?["publication"].clone(),
        );
        metadata_pins.as_object_mut().unwrap().insert(
            "retained_render".into(),
            publication.record["retained_render"].clone(),
        );
    }
    let effective_composition = match (&composition, &composition_snapshot) {
        (Some(raw), Some(content)) => {
            let value = decode(content, LIMIT)?;
            let revision = value["revision"]
                .as_u64()
                .ok_or_else(|| invalid("composition snapshot revision missing"))?;
            let admitted = AdmittedSnapshot::read(content, &state.token.job_id, revision)?;
            Some(project_composition(
                raw,
                &admitted,
                &snapshot,
                &state.token.original_manifest_sha256,
            )?)
        }
        (None, None) => None,
        _ => {
            return Err(invalid(
                "composition and its admitted snapshot must be paired",
            ))
        }
    };
    current_fence(store, state)?;
    Ok(AdmittedEffectiveState {
        current: state.clone(),
        snapshot,
        origin,
        source_graph: seed.graph,
        timeline,
        composition,
        composition_snapshot,
        effective_composition,
        spoken_assignments,
        metadata_pins,
    })
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;

    fn composition_fixture(snapshot: &AdmittedSnapshot, manifest: &str) -> String {
        let value = json!({"schema_version":2,"kind":"review_audio_composition",
            "job_id":snapshot.job(),"manifest_sha256":manifest,
            "review_snapshot_sha256":snapshot.sha256(),"source":{"sha256":"sha256:source"},
            "documents":[],"cues":[],"mix_producer_pin":{}});
        let mut raw = String::new();
        canonical(&value, &mut raw).unwrap();
        raw
    }

    #[test]
    fn content_projection_only_changes_snapshot_pin_and_keeps_public_counter() {
        with_admitted_original(|_, _, state| {
            let original =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let content = original
                .correction(
                    "finding-a",
                    Some("Chào bạn."),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let raw = composition_fixture(&content, &state.token.original_manifest_sha256);
            let mut value = decode(content.json(), LIMIT).unwrap();
            value["revision"] = json!(4);
            for finding in value["findings"].as_array_mut().unwrap() {
                finding["output_preview"] = json!("D:/native/retained-video.mp4");
            }
            let current =
                AdmittedSnapshot::read(&value.to_string(), &state.token.job_id, 4).unwrap();
            let projected = project_composition(
                &raw,
                &content,
                &current,
                &state.token.original_manifest_sha256,
            )
            .unwrap();
            let mut expected = decode(&raw, LIMIT).unwrap();
            expected["review_snapshot_sha256"] = json!(current.sha256());
            let mut expected_raw = String::new();
            canonical(&expected, &mut expected_raw).unwrap();
            assert_eq!(projected, expected_raw);
            assert_eq!(
                raw,
                composition_fixture(&content, &state.token.original_manifest_sha256)
            );
            assert_eq!(content.revision(), 1);
            assert_eq!(current.revision(), 4);
        });
    }

    #[test]
    fn content_projection_refuses_prior_edit_diagnostic_plan_or_backwards_revision() {
        with_admitted_original(|_, _, state| {
            let original =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let content = original
                .correction(
                    "finding-a",
                    Some("Chào bạn."),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let raw = composition_fixture(&content, &state.token.original_manifest_sha256);
            for field in [
                "translated_text",
                "source_text",
                "voice",
                "warning",
                "confidence",
            ] {
                let mut value = decode(content.json(), LIMIT).unwrap();
                value["revision"] = json!(3);
                value["findings"][0][field] = if field == "confidence" {
                    json!("0.123")
                } else {
                    json!("foreign")
                };
                let current =
                    AdmittedSnapshot::read(&value.to_string(), &state.token.job_id, 3).unwrap();
                assert!(project_composition(
                    &raw,
                    &content,
                    &current,
                    &state.token.original_manifest_sha256
                )
                .is_err());
            }
            for revision in [0, 3] {
                let mut value = decode(content.json(), LIMIT).unwrap();
                value["revision"] = json!(revision);
                if revision == 3 {
                    value["last_plan"] = Value::Null;
                }
                let current =
                    AdmittedSnapshot::read(&value.to_string(), &state.token.job_id, revision)
                        .unwrap();
                assert!(project_composition(
                    &raw,
                    &content,
                    &current,
                    &state.token.original_manifest_sha256
                )
                .is_err());
            }
        });
    }

    #[test]
    fn content_projection_requires_exact_closed_immutable_composition_identity() {
        with_admitted_original(|_, _, state| {
            let snapshot =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let raw = composition_fixture(&snapshot, &state.token.original_manifest_sha256);
            for (field, value) in [
                ("kind", json!("foreign")),
                ("schema_version", json!(true)),
                ("job_id", json!("foreign")),
                ("manifest_sha256", json!("foreign")),
                ("review_snapshot_sha256", json!("foreign")),
                ("unknown", json!(true)),
            ] {
                let mut changed = decode(&raw, LIMIT).unwrap();
                changed[field] = value;
                assert!(project_composition(
                    &changed.to_string(),
                    &snapshot,
                    &snapshot,
                    &state.token.original_manifest_sha256
                )
                .is_err());
            }
            assert!(project_composition(
                &(raw + "\n"),
                &snapshot,
                &snapshot,
                &state.token.original_manifest_sha256
            )
            .is_err());
        });
    }

    #[test]
    fn speech_confidence_preserves_actual_source_binary64_and_signed_zero() {
        for (original, rounded) in [
            ("0.9838564395904541", "0.983856439590454"),
            ("0.9168733358383179", "0.916873335838318"),
            ("-0.0", "0.0"),
        ] {
            let original = decode(original, LIMIT).unwrap();
            let changed = decode(rounded, LIMIT).unwrap();
            assert!(same_confidence_bits(&original, &original));
            assert!(!same_confidence_bits(&original, &changed));
        }
        assert!(!same_confidence_bits(&json!(true), &json!(1.0)));
        assert!(!same_confidence_bits(&Value::Null, &Value::Null));
    }

    #[test]
    fn current_origin_reader_preserves_original_bytes_and_distinct_public_revision() {
        with_admitted_original(|store, data, state| {
            let original = read_current(store, data, state).unwrap().packet().unwrap();
            assert_eq!(original["current_token"]["revision"], "0");
            assert_eq!(original["origin_token"]["revision"], "0");
            let before =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let changed = before
                .correction(
                    "finding-a",
                    Some("Chào bạn."),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let pending = store
                .commit_review_graph(&state.token, changed.json(), r#"{"kind":"unknown"}"#, 10)
                .unwrap();
            assert!(read_current(store, data, &pending).is_err());
            let target = store.review_undo_target(&pending.token).unwrap().unwrap();
            let restored = before.undo_proposal(&changed).unwrap();
            let two = store
                .undo_review_graph(&pending.token, restored.json(), 11)
                .unwrap();
            let result = read_current(store, data, &two).unwrap().packet().unwrap();
            assert_eq!(result["current_token"]["revision"], "2");
            assert_eq!(result["origin_token"]["revision"], "0");
            assert_eq!(
                result["current_timeline_json"],
                original["current_timeline_json"]
            );
            assert_eq!(result["origin_graph_json"], original["origin_graph_json"]);
            assert!(super::super::review_seed::read_original(store, data, &two).is_err());
            assert!(read_current(store, data, &target).is_err()); // historical token is not current.
            assert_eq!(
                store.review_graph_state(&state.token.job_id).unwrap(),
                Some(two)
            );
        });
    }
}
