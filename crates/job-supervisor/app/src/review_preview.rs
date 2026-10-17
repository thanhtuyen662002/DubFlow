//! Full-token current synthesized cue playback. Paths come only from opaque
//! native current admission and accepted metadata, never a webview hint.
use super::review_assets::{AcceptedAudioStore, CueAudioKind, PcmPin};
use super::review_correction::accepted_json;
use super::review_document::{decode, identity, text, tick};
use super::review_effective::AdmittedEffectiveState;
use super::review_publication::RecoveryWalk;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn selected<'a>(values: &'a Value, key: &str, id: &str) -> SupervisorResult<&'a Value> {
    let values = values
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("current audio inventory is missing"))?;
    let mut matches = values.iter().filter(|v| v[key] == id);
    let first = matches
        .next()
        .ok_or_else(|| invalid("current cue audio is unavailable; no new synthesis is implied"))?;
    if matches.next().is_some() {
        return Err(invalid("current cue audio identity is ambiguous"));
    }
    Ok(first)
}

fn capture(
    current: &AdmittedEffectiveState,
    assets: &AcceptedAudioStore,
    finding: &str,
    cue: &str,
    walk: &mut RecoveryWalk,
) -> SupervisorResult<Vec<u8>> {
    walk.check()?;
    let view = current.packet()?;
    let source = decode(
        view["source_graph_json"]
            .as_str()
            .ok_or_else(|| invalid("current source binding missing"))?,
        LIMIT,
    )?;
    if source["mode"] != "b2" || view["generation_dependencies_complete"] != true {
        return Err(invalid(
            "current synthesized preview requires admitted B2 speech; B1 cannot invent TTS",
        ));
    }
    let finding = current.snapshot().finding(finding)?;
    if finding["cue_id"] != cue || finding["range"].is_null() {
        return Err(invalid(
            "current preview finding does not identify this canonical cue",
        ));
    }
    let composition = decode(
        view["effective_composition_json"]
            .as_str()
            .ok_or_else(|| invalid("current speech composition missing"))?,
        LIMIT,
    )?;
    let cues = composition["cues"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 10000)
        .ok_or_else(|| invalid("current speech cue inventory missing"))?;
    let matches: Vec<_> = cues
        .iter()
        .filter(|v| v["tts_input"]["segment_id"] == cue)
        .collect();
    if matches.len() != 1 {
        return Err(invalid("current preview cue assignment changed"));
    }
    let hash = text(&matches[0]["document_sha256"], 71)?;
    let doc = selected(&composition["documents"], "sha256", hash)?;
    // accepted_json validates the canonical data-store object path. Neither
    // metadata_root nor output_preview is used as file-read authority.
    let object = accepted_json(
        &json!({"path":doc["path"],"sha256":doc["sha256"],"size_bytes":doc["size_bytes"]}),
        assets,
    )?;
    let speech = decode(&walk.read_metadata(assets, &object)?, LIMIT)?;
    let origin = decode(&current.origin().graph_json, LIMIT)?;
    let original = accepted_json(&origin["accepted_documents"]["tts_document"], assets)?;
    // Preserve the original producer's source-read policy only for that exact
    // archived metadata object. Changed/current documents require retained seal.
    let kind = if object == original {
        CueAudioKind::ArchivedOriginal
    } else {
        CueAudioKind::Retained
    };
    let artifact = selected(&speech["artifacts"], "segment_id", cue)?;
    if artifact["format"] != "wav" || artifact["bits_per_sample"] != 16 {
        return Err(invalid(
            "current preview does not have its admitted PCM format",
        ));
    }
    let path = PathBuf::from(text(&artifact["path"], 32767)?);
    let root = PathBuf::from(text(&doc["artifact_root"], 32767)?);
    let number = |field: &str| {
        artifact[field]
            .as_u64()
            .ok_or_else(|| invalid("current cue PCM field missing"))
    };
    let pin = PcmPin {
        sha256: text(&artifact["content_hash"], 71)?.into(),
        size_bytes: super::review_assets::regular(&path)?.metadata()?.len(),
        sample_rate: u32::try_from(number("sample_rate")?)
            .map_err(|_| invalid("current cue sample rate overflows"))?,
        channels: u16::try_from(number("channels")?)
            .map_err(|_| invalid("current cue channels overflow"))?,
        frame_count: number("frame_count")?,
    };
    let raw =
        super::review_assets::read_current_cue_audio(&root, &path, &pin, kind, walk.deadline())?;
    walk.read_metadata(assets, &object)?;
    walk.check()?;
    Ok(raw)
}

pub(super) fn read(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
    finding: &str,
    cue: &str,
) -> SupervisorResult<Vec<u8>> {
    identity(&json!(finding))?;
    identity(&json!(cue))?;
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("current preview token changed; reload"));
    }
    let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    let current = super::review_effective::read_current_with_walk(store, data, state, &mut walk)?;
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let bytes = capture(&current, &assets, finding, cue, &mut walk)?;
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid(
            "current preview changed during waveform read; reload",
        ));
    }
    walk.check()?;
    Ok(bytes)
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    const KEYS: [&str; 8] = [
        "--data-root",
        "--job-id",
        "--revision",
        "--snapshot-sha256",
        "--graph-sha256",
        "--original-manifest-sha256",
        "--finding-id",
        "--cue-id",
    ];
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("current audio argument is not UTF-8"))?;
        if !KEYS.contains(&flag.as_str()) || values.contains_key(&flag) {
            return Err(invalid("unknown or duplicate current audio argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("current audio argument missing"))?
            .into_string()
            .map_err(|_| invalid("current audio argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values.get(key).map(String::as_str).ok_or_else(|| {
            invalid("current audio requires the full actual token and cue/finding IDs")
        })
    };
    for key in KEYS {
        required(key)?;
    }
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("current audio control root must be absolute"));
    }
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("Review graph is not initialized"))?;
    if state.token.revision != tick(&json!(required("--revision")?), false)?
        || state.token.snapshot_sha256 != required("--snapshot-sha256")?
        || state.token.graph_sha256 != required("--graph-sha256")?
        || state.token.original_manifest_sha256 != required("--original-manifest-sha256")?
    {
        return Err(invalid("current audio token changed; reload"));
    }
    let bytes = read(
        &store,
        &data,
        &state,
        required("--finding-id")?,
        required("--cue-id")?,
    )?;
    // Never emit partial/audio bytes before complete source/current admission.
    std::io::stdout().lock().write_all(&bytes)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_assignment_selects_exact_document_and_cue_and_refuses_ambiguity_or_missing_audio() {
        let docs = json!([{"sha256":"old-document","path":"original"},{"sha256":"current-document","path":"corrected"}]);
        assert_eq!(
            selected(&docs, "sha256", "current-document").unwrap()["path"],
            "corrected"
        );
        let cues = json!([{"segment_id":"cue-a","path":"current-a"},{"segment_id":"cue-b","path":"retained-b"}]);
        assert_eq!(
            selected(&cues, "segment_id", "cue-b").unwrap()["path"],
            "retained-b"
        );
        assert!(selected(&cues, "segment_id", "unmapped").is_err());
        let ambiguous = json!([{"segment_id":"cue-a"},{"segment_id":"cue-a"}]);
        assert!(selected(&ambiguous, "segment_id", "cue-a").is_err());
        assert!(selected(&json!([]), "segment_id", "cue-a").is_err());
    }
}
