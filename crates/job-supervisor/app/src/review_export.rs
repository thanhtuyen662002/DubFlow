//! Read-only selection of the actually active corrected export inputs.
//! A serialized packet is not admission or publication authority. The future
//! publisher must reopen/copy/hash every input and fence the full current head.
use super::review_assets::{AcceptedAudioStore, ExportCopyInput, ExportFileSpec, MixStorageBudget};
use super::review_correction::{accepted_json, descriptor};
use super::review_document::{canonical, closed, decode, text, tick};
use super::review_publication::{token, RecoveryWalk};
use super::review_retention::{NativeMixStore, NativeOutputPin};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
const ROLES: [&str; 5] = [
    "final_video",
    "captions_srt",
    "captions_ass",
    "corrected_timeline",
    "qc_report",
];

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn fence(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("corrected export generation changed; reload"));
    }
    Ok(())
}

/// No worker/browser constructor or decoder is provided. This value represents
/// an observation of native verified inputs, not a lease on their future bytes.
pub(super) struct AdmittedExportInputs {
    packet: Value,
    output_pin: NativeOutputPin,
    sources: Vec<ExportSource>,
}

impl AdmittedExportInputs {
    pub(super) fn packet(&self) -> &Value {
        &self.packet
    }
    pub(super) fn output_pin(&self) -> &NativeOutputPin {
        &self.output_pin
    }
    pub(super) fn sources(&self) -> &[ExportSource] {
        &self.sources
    }
}

/// Only native prepare constructs a source. Serialized file tables cannot be
/// decoded into this type or grant the publisher a source path.
pub(super) struct ExportSource {
    role: &'static str,
    name: String,
    path: PathBuf,
    sha256: String,
    size_bytes: u64,
}
impl ExportSource {
    pub(super) fn spec(&self) -> ExportFileSpec {
        ExportFileSpec {
            name: self.name.clone(),
            sha256: self.sha256.clone(),
            size_bytes: self.size_bytes,
        }
    }
    pub(super) fn portable_pin(&self) -> Value {
        json!({"role":self.role,"relative_path":self.name,"sha256":self.sha256,"size_bytes":self.size_bytes.to_string()})
    }
    pub(super) fn copy(
        &self,
        media: &NativeMixStore,
        generation: &str,
        remaining_after: u64,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        media.copy_export_file(
            &ExportCopyInput {
                generation,
                name: &self.name,
                source: &self.path,
                sha256: &self.sha256,
                size_bytes: self.size_bytes,
                remaining_after,
            },
            deadline,
        )
    }
}

pub(super) fn prepare(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<AdmittedExportInputs> {
    let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    prepare_with_walk(store, data, state, &mut walk)
}

pub(super) fn prepare_with_walk(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
    walk: &mut RecoveryWalk,
) -> SupervisorResult<AdmittedExportInputs> {
    walk.check()?;
    fence(store, state)?;
    let graph = decode(&state.graph_json, LIMIT)?;
    if !matches!(
        graph["kind"].as_str(),
        Some(
            "review_bound_retained_render_graph"
                | "review_bound_retained_render_graph_v2"
                | "review_bound_retained_render_graph_v3"
        )
    ) {
        return Err(invalid(
            "corrected export requires an active retained corrected graph",
        ));
    }
    let current = super::review_effective::read_current_with_walk(store, data, state, walk)?;
    let view = current.packet()?;
    if view["generation_dependencies_complete"] != true {
        return Err(invalid(
            "corrected export has incomplete native generation dependencies",
        ));
    }
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let publication = accepted_json(&view["metadata_pins"]["native_publication"], &assets)?;
    if graph["publication"] != descriptor(&publication) {
        return Err(invalid(
            "corrected export substitutes its active publication",
        ));
    }
    let record = decode(&walk.read_metadata(&assets, &publication)?, LIMIT)?;
    let retained = accepted_json(&record["retained_render"], &assets)?;
    let context = accepted_json(&record["context"], &assets)?;
    let timeline = accepted_json(&record["corrected_timeline"], &assets)?;
    let render = decode(&walk.read_metadata(&assets, &retained)?, LIMIT)?;
    let context_value = decode(&walk.read_metadata(&assets, &context)?, LIMIT)?;
    let timeline_raw = walk.read_metadata(&assets, &timeline)?;
    if view["current_timeline_json"] != timeline_raw
        || view["metadata_pins"]["current_timeline"] != descriptor(&timeline)
        || view["metadata_pins"]["retained_render"] != descriptor(&retained)
    {
        return Err(invalid(
            "corrected export timeline or retained-render binding changed",
        ));
    }

    let origin = decode(&current.origin().graph_json, LIMIT)?;
    let manifest = accepted_json(&origin["accepted_documents"]["job_manifest"], &assets)?;
    let manifest_value = decode(&walk.read_metadata(&assets, &manifest)?, LIMIT)?;
    let seed = super::review_seed::read_original(store, data, current.origin())?;
    let seed_graph = decode(&seed.graph, LIMIT)?;
    let mode = match seed_graph["mode"].as_str() {
        Some("b1") => "b1",
        Some("b2") => "b2",
        _ => return Err(invalid("corrected export original mode missing")),
    };
    let source = &context_value["source"];
    let source_path = PathBuf::from(text(&manifest_value["source"]["path"], 32767)?);
    let source_size = tick(&source["size_bytes"], true)?;
    if !source_path.is_absolute()
        || source_path
            .components()
            .any(|p| matches!(p, Component::ParentDir))
        || source["path"] != manifest_value["source"]["path"]
        || source["sha256"] != manifest_value["source"]["sha256"]
    {
        return Err(invalid(
            "corrected export source differs from its immutable original",
        ));
    }
    super::review_render::verified(
        &source_path,
        text(&source["sha256"], 71)?,
        source_size,
        false,
        walk.deadline()
            .min(Instant::now() + Duration::from_secs(180)),
    )?;

    let pin = NativeOutputPin::read(&seed.output_root)?;
    if record["output_volume_id"] != pin.volume_id() {
        return Err(invalid("corrected export retained output volume changed"));
    }
    let media = NativeMixStore::open_existing(
        &pin,
        &state.token.job_id,
        MixStorageBudget {
            maximum_bytes: 32 * 1024 * 1024 * 1024,
            minimum_free_bytes: 2 * 1024 * 1024 * 1024,
        },
    )?;
    let roles = render["artifacts"]
        .as_array()
        .filter(|roles| roles.len() == ROLES.len())
        .ok_or_else(|| invalid("corrected export requires five retained render roles"))?;
    let mut artifacts = Vec::new();
    let mut sources = Vec::new();
    for ((entry, role), name) in roles.iter().zip(ROLES).zip([
        "final_vi.mp4",
        "captions_vi.srt",
        "captions_vi.ass",
        "timeline.json",
        "qc_report.json",
    ]) {
        closed(entry, &["role", "file"])?;
        if entry["role"] != role {
            return Err(invalid("corrected export retained role order changed"));
        }
        let file = media.reopen_file(&entry["file"], walk.deadline())?;
        if role == "corrected_timeline" {
            let raw = super::review_render::verified(
                file.path(),
                text(&entry["file"]["locator"]["content_hash"], 71)?,
                tick(&entry["file"]["locator"]["size_bytes"], true)?,
                true,
                walk.deadline(),
            )?;
            if raw != timeline_raw.as_bytes() {
                return Err(invalid("corrected export physical timeline bytes changed"));
            }
        }
        artifacts.push(json!({"role":role,"path":file.path(),"file":file.descriptor()}));
        sources.push(ExportSource {
            role,
            name: name.into(),
            path: file.path().to_path_buf(),
            sha256: text(&entry["file"]["locator"]["content_hash"], 71)?.into(),
            size_bytes: tick(&entry["file"]["locator"]["size_bytes"], true)?,
        });
    }
    let extension = source_path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .filter(|value| {
            (1..=8).contains(&value.len())
                && value
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        })
        .ok_or_else(|| invalid("portable source extension is invalid"))?;
    sources.push(ExportSource {
        role: "source_video",
        name: format!("source.{extension}"),
        path: source_path.clone(),
        sha256: text(&source["sha256"], 71)?.into(),
        size_bytes: source_size,
    });
    let final_mix = if mode == "b2" {
        let roles = record["mixed"]["retained_mix_audio"]
            .as_array()
            .filter(|roles| roles.len() == 3)
            .ok_or_else(|| invalid("corrected B2 export requires three retained PCM roles"))?;
        let mut final_mix = None;
        for (entry, role) in roles
            .iter()
            .zip(["original_audio", "dialogue_stem", "final_mix"])
        {
            closed(entry, &["role", "audio"])?;
            if entry["role"] != role {
                return Err(invalid(
                    "corrected B2 export retained PCM role order changed",
                ));
            }
            let audio = media.reopen_audio(&entry["audio"], walk.deadline())?;
            sources.push(ExportSource {
                role,
                name: format!("{role}.wav"),
                path: audio.path().to_path_buf(),
                sha256: text(&entry["audio"]["locator"]["content_hash"], 71)?.into(),
                size_bytes: tick(&entry["audio"]["locator"]["size_bytes"], true)?,
            });
            if role == "final_mix" {
                final_mix = Some(json!({"path":audio.path(),"audio":audio.descriptor()}));
            }
        }
        final_mix.ok_or_else(|| invalid("corrected B2 export current final mix missing"))?
    } else {
        if !record["mixed"].is_null() {
            return Err(invalid("corrected B1 export cannot invent mixed audio"));
        }
        Value::Null
    };
    drop(media);
    for object in [&publication, &retained, &context, &timeline, &manifest] {
        walk.read_metadata(&assets, object)?;
    }
    if NativeOutputPin::read(&seed.output_root)?.volume_id() != pin.volume_id() {
        return Err(invalid(
            "corrected export output volume changed after selection",
        ));
    }
    fence(store, state)?;
    walk.check()?;
    let packet = json!({"schema_version":1,"kind":"review_native_current_export_input",
        "current_token":token(state),"origin_token":token(current.origin()),"mode":mode,
        "current_snapshot_json":state.snapshot_json,"current_timeline_json":timeline_raw,
        "original_manifest":descriptor(&manifest),"native_publication":descriptor(&publication),
        "retained_render":descriptor(&retained),"artifacts":artifacts,
        "source":{"path":source_path,"sha256":source["sha256"],"size_bytes":source_size.to_string()},
        "final_mix":final_mix,"active_graph_published":true,
        "export_published":false,"correction_ready":false});
    let mut encoded = String::new();
    canonical(&packet, &mut encoded)?;
    if encoded.len() > LIMIT {
        return Err(invalid(
            "corrected export input packet exceeds its metadata bound",
        ));
    }
    fence(store, state)?;
    walk.check()?;
    Ok(AdmittedExportInputs {
        packet,
        output_pin: pin,
        sources,
    })
}

pub(super) fn run(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(args, false)
}

pub(super) fn run_publish(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    run_inner(args, true)
}

fn run_inner(mut args: impl Iterator<Item = OsString>, publish: bool) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    const KEYS: [&str; 6] = [
        "--data-root",
        "--job-id",
        "--revision",
        "--snapshot-sha256",
        "--graph-sha256",
        "--original-manifest-sha256",
    ];
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("export argument is not UTF-8"))?;
        if !KEYS.contains(&flag.as_str()) || values.contains_key(&flag) {
            return Err(invalid("unknown or duplicate corrected export argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("missing corrected export argument"))?
            .into_string()
            .map_err(|_| invalid("export argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| invalid("corrected export requires data/job and complete current token"))
    };
    for key in KEYS {
        required(key)?;
    }
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|p| matches!(p, Component::ParentDir)) {
        return Err(invalid(
            "corrected export requires an absolute control root",
        ));
    }
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("Review graph is not initialized"))?;
    if state.token.revision != tick(&json!(required("--revision")?), false)?
        || state.token.snapshot_sha256 != required("--snapshot-sha256")?
        || state.token.graph_sha256 != required("--graph-sha256")?
        || state.token.original_manifest_sha256 != required("--original-manifest-sha256")?
    {
        return Err(invalid("corrected export token changed; reload"));
    }
    if publish {
        println!(
            "{}",
            super::review_export_publication::publish(&store, &data, &state)?
        );
    } else {
        let admitted = prepare(&store, &data, &state)?;
        println!("{}", admitted.packet());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;

    #[test]
    fn corrected_export_refuses_original_and_restored_original_without_changing_state() {
        with_admitted_original(|store, data, original| {
            assert!(prepare(store, data, original).is_err());
            let before = super::super::review_document::AdmittedSnapshot::read(
                &original.snapshot_json,
                &original.token.job_id,
                0,
            )
            .unwrap();
            let after = before
                .correction(
                    "finding-a",
                    Some("Changed cue"),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let pending = store
                .commit_review_graph(&original.token, after.json(), r#"{"kind":"unknown"}"#, 10)
                .unwrap();
            assert!(prepare(store, data, &pending).is_err());
            let restored = before.undo_proposal(&after).unwrap();
            let current = store
                .undo_review_graph(&pending.token, restored.json(), 11)
                .unwrap();
            assert_eq!(current.token.revision, 2);
            assert!(prepare(store, data, &current).is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(current)
            );
            assert!(store.review_undo_target(&original.token).is_err());
        });
    }

    #[test]
    fn corrected_export_refuses_invented_retained_graph_before_any_publication() {
        with_admitted_original(|store, data, original| {
            let before = super::super::review_document::AdmittedSnapshot::read(
                &original.snapshot_json,
                &original.token.job_id,
                0,
            )
            .unwrap();
            let after = before
                .correction(
                    "finding-a",
                    Some("Changed cue"),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let pending = store
                .commit_review_graph(
                    &original.token,
                    after.json(),
                    &json!({"schema_version":1,"kind":"review_bound_retained_render_graph_v3",
                    "publication":{"path":"untrusted","sha256":"untrusted","size_bytes":1}})
                    .to_string(),
                    10,
                )
                .unwrap();
            assert!(prepare(store, data, &pending).is_err());
            assert_eq!(
                store.review_graph_state(&original.token.job_id).unwrap(),
                Some(pending.clone())
            );
            assert_eq!(
                store.review_undo_target(&pending.token).unwrap(),
                Some(original.clone())
            );
        });
    }
}
