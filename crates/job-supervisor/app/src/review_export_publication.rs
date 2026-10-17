//! Versioned native standard bundle publication. Complete byte copies preserve
//! the immutable original export. Optional production editable/vendor adapters
//! remain separate; an offline bundle check grants no live Review authority.
use super::review_assets::{
    verify_export_files, AcceptedAudioStore, ExportCopyInput, ExportFileSpec, MixStorageBudget,
};
use super::review_document::{canonical, closed, decode, identity, text, tick, AdmittedSnapshot};
use super::review_export::{prepare_with_walk, AdmittedExportInputs};
use super::review_publication::{token, RecoveryWalk};
use super::review_retention::NativeMixStore;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024 * 1024;
const MANIFEST: &str = "review_export_manifest.json";
const RECIPE: &str = "review-corrected-standard-bundle-v1";

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(raw: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(raw))
}
fn exact(value: &Value) -> SupervisorResult<String> {
    let mut out = String::new();
    canonical(value, &mut out)?;
    Ok(out)
}
fn hash_text(value: &Value) -> SupervisorResult<&str> {
    let value = text(value, 71)?;
    if value.len() != 71
        || !value.starts_with("sha256:")
        || !value[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid("bundle hash is not canonical SHA256"));
    }
    Ok(value)
}
fn fence(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("export publication token changed; reload"));
    }
    Ok(())
}
fn budget() -> MixStorageBudget {
    MixStorageBudget {
        maximum_bytes: 32 * 1024 * 1024 * 1024,
        minimum_free_bytes: 2 * 1024 * 1024 * 1024,
    }
}

fn generation(value: &Value) -> SupervisorResult<String> {
    let mut value = value.clone();
    value
        .as_object_mut()
        .ok_or_else(|| invalid("bundle manifest must be an object"))?
        .remove("generation");
    Ok(hash(exact(&value)?.as_bytes())[7..].into())
}

pub(super) fn manifest(inputs: &AdmittedExportInputs) -> SupervisorResult<(Value, String)> {
    let packet = inputs.packet();
    let files: Vec<_> = inputs
        .sources()
        .iter()
        .map(|source| source.portable_pin())
        .collect();
    let mut value = json!({"schema_version":1,"kind":"review_corrected_standard_bundle","recipe":RECIPE,
        "generation":"","current_token":packet["current_token"],"origin_token":packet["origin_token"],
        "mode":packet["mode"],"current_snapshot_json":packet["current_snapshot_json"],
        "publication_sha256":packet["native_publication"]["sha256"],
        "retained_render_sha256":packet["retained_render"]["sha256"],"files":files});
    value["generation"] = generation(&value)?.into();
    manifest_specs(&value)?;
    let raw = exact(&value)?;
    if raw.len() > LIMIT {
        return Err(invalid("bundle manifest exceeds its metadata limit"));
    }
    Ok((value, raw))
}

fn read_token(value: &Value) -> SupervisorResult<(&str, u64)> {
    closed(
        value,
        &[
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ],
    )?;
    let job = identity(&value["job_id"])?;
    let revision = tick(&value["revision"], false)?;
    for field in [
        "snapshot_sha256",
        "graph_sha256",
        "original_manifest_sha256",
    ] {
        hash_text(&value[field])?;
    }
    Ok((job, revision))
}

/// Closed manifest admission verifies identities and the exact relative file
/// table. Physical hashing/inventory is independent in validate_bundle below.
fn manifest_specs(value: &Value) -> SupervisorResult<Vec<ExportFileSpec>> {
    closed(
        value,
        &[
            "schema_version",
            "kind",
            "recipe",
            "generation",
            "current_token",
            "origin_token",
            "mode",
            "current_snapshot_json",
            "publication_sha256",
            "retained_render_sha256",
            "files",
        ],
    )?;
    let (job, revision) = read_token(&value["current_token"])?;
    let (origin, zero) = read_token(&value["origin_token"])?;
    if value["schema_version"].as_u64() != Some(1)
        || value["kind"] != "review_corrected_standard_bundle"
        || value["recipe"] != RECIPE
        || job != origin
        || zero != 0
        || revision == 0
        || value["current_token"]["original_manifest_sha256"]
            != value["origin_token"]["original_manifest_sha256"]
        || value["generation"] != generation(value)?
    {
        return Err(invalid(
            "bundle manifest generation or original/current identity changed",
        ));
    }
    let raw = value["current_snapshot_json"]
        .as_str()
        .ok_or_else(|| invalid("bundle current snapshot missing"))?;
    let snapshot = AdmittedSnapshot::read(raw, job, revision)?;
    if snapshot.sha256() != hash_text(&value["current_token"]["snapshot_sha256"])? {
        return Err(invalid(
            "bundle snapshot differs from its full current token",
        ));
    }
    hash_text(&value["publication_sha256"])?;
    hash_text(&value["retained_render_sha256"])?;
    let b2 = match value["mode"].as_str() {
        Some("b1") => false,
        Some("b2") => true,
        _ => return Err(invalid("bundle mode invalid")),
    };
    let files = value["files"]
        .as_array()
        .filter(|files| files.len() == if b2 { 9 } else { 6 })
        .ok_or_else(|| invalid("bundle lacks its complete mode-specific file set"))?;
    let expected = [
        ("final_video", "final_vi.mp4"),
        ("captions_srt", "captions_vi.srt"),
        ("captions_ass", "captions_vi.ass"),
        ("corrected_timeline", "timeline.json"),
        ("qc_report", "qc_report.json"),
        ("source_video", ""),
        ("original_audio", "original_audio.wav"),
        ("dialogue_stem", "dialogue_stem.wav"),
        ("final_mix", "final_mix.wav"),
    ];
    let mut specs = Vec::new();
    for (entry, (role, name)) in files.iter().zip(expected) {
        closed(entry, &["role", "relative_path", "sha256", "size_bytes"])?;
        let actual = text(&entry["relative_path"], 128)?;
        if entry["role"] != role
            || if role == "source_video" {
                !actual.strip_prefix("source.").is_some_and(|s| {
                    (1..=8).contains(&s.len())
                        && s.bytes()
                            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
                })
            } else {
                actual != name
            }
        {
            return Err(invalid(
                "bundle file order, role or portable basename changed",
            ));
        }
        let size = tick(&entry["size_bytes"], true)?;
        let limit = if role == "source_video" || role == "final_video" {
            128 * 1024 * 1024 * 1024
        } else if ["original_audio", "dialogue_stem", "final_mix"].contains(&role) {
            u64::from(u32::MAX) + 8
        } else {
            LIMIT as u64
        };
        if size > limit {
            return Err(invalid("bundle file exceeds its role bound"));
        }
        specs.push(ExportFileSpec {
            name: actual.into(),
            sha256: hash_text(&entry["sha256"])?.into(),
            size_bytes: size,
        });
    }
    Ok(specs)
}

/// The expected manifest hash must come from a trusted publication result.
/// A caller-modified manifest cannot authorize file reuse or live state.
pub(super) fn validate_bundle(
    root: &Path,
    expected_hash: &str,
    deadline: Instant,
) -> SupervisorResult<Value> {
    hash_text(&json!(expected_hash))?;
    if !root.is_absolute() || root.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid(
            "bundle validation requires an absolute plain directory",
        ));
    }
    super::review_pin::plain(root)?;
    let path = root.join(MANIFEST);
    let mut file = super::review_assets::regular(&path)?;
    let size = file.metadata()?.len();
    if size == 0 || size > LIMIT as u64 {
        return Err(invalid("bundle manifest exceeds byte bound"));
    }
    let mut bytes = Vec::new();
    file.by_ref().take(size + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size || hash(&bytes) != expected_hash {
        return Err(invalid("bundle manifest bytes changed"));
    }
    let raw = std::str::from_utf8(&bytes).map_err(|_| invalid("bundle manifest is not UTF-8"))?;
    let value = decode(raw, LIMIT)?;
    if exact(&value)? != raw {
        return Err(invalid(
            "bundle manifest is not its native canonical encoding",
        ));
    }
    let mut specs = manifest_specs(&value)?;
    specs.push(ExportFileSpec {
        name: MANIFEST.into(),
        sha256: expected_hash.into(),
        size_bytes: size,
    });
    verify_export_files(root, &specs, deadline)?;
    let timeline = specs.iter().find(|s| s.name == "timeline.json").unwrap();
    let timeline_bytes = super::review_render::verified(
        &root.join(&timeline.name),
        &timeline.sha256,
        timeline.size_bytes,
        true,
        deadline,
    )?;
    decode(
        std::str::from_utf8(&timeline_bytes)
            .map_err(|_| invalid("bundle timeline is not UTF-8"))?,
        LIMIT,
    )?;
    if Instant::now() >= deadline {
        return Err(invalid("bundle validation deadline exceeded"));
    }
    Ok(value)
}

pub(super) fn publish(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<Value> {
    super::review_pin::plain(data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _guard = super::JobExecutionGuard::acquire(&db, &state.token.job_id)?;
    fence(store, state)?;
    let mut admission = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    let inputs = prepare_with_walk(store, data, state, &mut admission)?;
    let (expected, raw) = manifest(&inputs)?;
    let generation = text(&expected["generation"], 64)?.to_owned();
    let manifest_hash = hash(raw.as_bytes());
    let mut specs = manifest_specs(&expected)?;
    specs.push(ExportFileSpec {
        name: MANIFEST.into(),
        sha256: manifest_hash.clone(),
        size_bytes: raw.len() as u64,
    });
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let manifest_object = assets.accept_json(&raw)?;
    let deadline = Instant::now() + Duration::from_secs(720);
    let media = NativeMixStore::open_existing(inputs.output_pin(), &state.token.job_id, budget())?;
    let existing = media.export_published_directory(&generation)?;
    let candidate = if let Some(path) = existing {
        path
    } else {
        let missing = media.export_remaining_bytes(&generation, &specs, deadline)?;
        let mut remaining = missing.iter().try_fold(0u64, |n, bytes| {
            n.checked_add(*bytes)
                .ok_or_else(|| invalid("bundle reservation overflow"))
        })?;
        for (index, source) in inputs.sources().iter().enumerate() {
            let spec = source.spec();
            if specs[index] != spec {
                return Err(invalid("native copy source differs from complete manifest"));
            }
            remaining = remaining
                .checked_sub(missing[index])
                .ok_or_else(|| invalid("bundle copy reservation changed"))?;
            source.copy(&media, &generation, remaining, deadline)?;
            fence(store, state)?;
        }
        media.copy_export_file(
            &ExportCopyInput {
                generation: &generation,
                name: MANIFEST,
                source: &manifest_object.path,
                sha256: &manifest_object.sha256,
                size_bytes: manifest_object.size_bytes,
                remaining_after: 0,
            },
            deadline,
        )?;
        media.export_pending_directory(&generation)?
    };
    if validate_bundle(&candidate, &manifest_hash, deadline)? != expected {
        return Err(invalid(
            "copied bundle manifest differs from native current inputs",
        ));
    }
    drop(media);

    // Release the output writer lease before the physical current reader opens
    // its own. Re-admit all dependencies, rather than trusting the input packet.
    let mut final_admission = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    let fresh = prepare_with_walk(store, data, state, &mut final_admission)?;
    let (recovered, recovered_raw) = manifest(&fresh)?;
    if recovered != expected || recovered_raw != raw {
        return Err(invalid("native export dependencies changed during copy"));
    }
    let media = NativeMixStore::open_existing(fresh.output_pin(), &state.token.job_id, budget())?;
    let path = media.publish_export_directory(
        &generation,
        &specs,
        || fence(store, state),
        final_admission.deadline(),
    )?;
    if validate_bundle(&path, &manifest_hash, final_admission.deadline())? != expected {
        return Err(invalid("published bundle changed during recovery"));
    }
    fence(store, state)?;
    final_admission.check()?;
    Ok(
        json!({"schema_version":1,"kind":"review_native_current_export_result","current_token":token(state),
        "generation":generation,"path":path,"manifest_sha256":manifest_hash,"files_verified":specs.len(),
        "standard_bundle_published":true,"production_editable_pack_published":false,"current_generation_checked":true,
        "correction_ready":false}),
    )
}

pub(super) fn run_validate(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("bundle argument is not UTF-8"))?;
        if !["--bundle-root", "--manifest-sha256"].contains(&flag.as_str())
            || values.contains_key(&flag)
        {
            return Err(invalid("unknown or duplicate bundle validation argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("bundle argument missing"))?
            .into_string()
            .map_err(|_| invalid("bundle argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let root = PathBuf::from(
        values
            .get("--bundle-root")
            .ok_or_else(|| invalid("bundle root missing"))?,
    );
    let expected = values
        .get("--manifest-sha256")
        .ok_or_else(|| invalid("trusted bundle manifest hash missing"))?;
    let manifest = validate_bundle(&root, expected, Instant::now() + Duration::from_secs(720))?;
    println!(
        "{}",
        json!({"schema_version":1,"kind":"review_native_export_bundle_validation","path":root,
        "manifest_sha256":expected,"current_token":manifest["current_token"],"generation":manifest["generation"],
        "bundle_bytes_verified":true,"current_generation_checked":false,"media_redecoded":false,"correction_ready":false})
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;
    use std::fs;

    fn writable(path: &Path) {
        let mut permissions = fs::metadata(path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(path, permissions).unwrap();
    }

    /// Encoded media semantics are deliberately not asserted by this byte-only
    /// fixture. Live publication requires the separate physical native reader.
    fn fixture(root: &Path, state: &ReviewGraphState) -> (Value, String) {
        fs::create_dir(root).unwrap();
        let revision = 9_007_199_254_740_993;
        let snapshot =
            AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
        let snapshot = super::super::review_active::at_revision(&snapshot, revision).unwrap();
        let mut current = token(state);
        current["revision"] = revision.to_string().into();
        current["snapshot_sha256"] = snapshot.sha256().into();
        let mut files = Vec::new();
        for (role, name, bytes) in [
            ("final_video", "final_vi.mp4", &b"byte-only fake video"[..]),
            (
                "captions_srt",
                "captions_vi.srt",
                &b"byte-only fake SRT"[..],
            ),
            (
                "captions_ass",
                "captions_vi.ass",
                &b"byte-only fake ASS"[..],
            ),
            (
                "corrected_timeline",
                "timeline.json",
                &br#"{"cues":[],"schema_version":1}"#[..],
            ),
            (
                "qc_report",
                "qc_report.json",
                &br#"{"byte_fixture":true}"#[..],
            ),
            ("source_video", "source.mp4", &b"byte-only fake source"[..]),
        ] {
            let path = root.join(name);
            fs::write(&path, bytes).unwrap();
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(true);
            fs::set_permissions(path, permissions).unwrap();
            files.push(json!({"role":role,"relative_path":name,"sha256":hash(bytes),"size_bytes":bytes.len().to_string()}));
        }
        let mut value = json!({"schema_version":1,"kind":"review_corrected_standard_bundle","recipe":RECIPE,"generation":"",
            "current_token":current,"origin_token":token(state),"mode":"b1","current_snapshot_json":snapshot.json(),
            "publication_sha256":hash(b"fixture publication"),"retained_render_sha256":hash(b"fixture render"),"files":files});
        value["generation"] = generation(&value).unwrap().into();
        let raw = exact(&value).unwrap();
        let path = root.join(MANIFEST);
        fs::write(&path, raw.as_bytes()).unwrap();
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(path, permissions).unwrap();
        (value, hash(raw.as_bytes()))
    }

    fn release_fixture(root: &Path) {
        // Only files created by this fixture are unsealed for Windows teardown.
        for file in fs::read_dir(root).unwrap() {
            writable(&file.unwrap().path());
        }
    }

    #[test]
    fn moved_bundle_preserves_wide_token_and_detects_equal_size_corruption() {
        with_admitted_original(|store, data, state| {
            let root = data.join("export byte fixture");
            let (value, pin) = fixture(&root, state);
            let deadline = Instant::now() + Duration::from_secs(10);
            assert_eq!(validate_bundle(&root, &pin, deadline).unwrap(), value);
            assert_eq!(value["current_token"]["revision"], "9007199254740993");
            let moved = data.join("moved bundle with spaces");
            fs::rename(&root, &moved).unwrap();
            assert_eq!(validate_bundle(&moved, &pin, deadline).unwrap(), value);
            let source = moved.join("source.mp4");
            let mut bytes = fs::read(&source).unwrap();
            bytes[3] ^= 1;
            writable(&source);
            fs::write(&source, bytes).unwrap();
            let mut permissions = fs::metadata(&source).unwrap().permissions();
            permissions.set_readonly(true);
            fs::set_permissions(&source, permissions).unwrap();
            assert!(validate_bundle(&moved, &pin, deadline).is_err());
            assert_eq!(
                store.review_graph_state(&state.token.job_id).unwrap(),
                Some(state.clone())
            );
            release_fixture(&moved);
        });
    }

    #[test]
    fn bundle_refuses_extra_partial_wrong_trusted_hash_and_forged_role_or_mode() {
        with_admitted_original(|_, data, state| {
            let root = data.join("closed export byte fixture");
            let (value, pin) = fixture(&root, state);
            let deadline = Instant::now() + Duration::from_secs(10);
            fs::write(root.join("crash.partial"), b"unregistered").unwrap();
            assert!(validate_bundle(&root, &pin, deadline).is_err());
            fs::remove_file(root.join("crash.partial")).unwrap();
            assert!(validate_bundle(&root, &hash(b"wrong manifest"), deadline).is_err());
            for change in 0..4 {
                let mut changed = value.clone();
                match change {
                    0 => changed["files"][0]["relative_path"] = "../outside.mp4".into(),
                    1 => changed["files"][0]["role"] = "source_video".into(),
                    2 => changed["mode"] = "b2".into(),
                    _ => {
                        changed["current_token"]["snapshot_sha256"] =
                            hash(b"foreign snapshot").into()
                    }
                }
                changed["generation"] = generation(&changed).unwrap().into();
                assert!(manifest_specs(&changed).is_err());
            }
            assert_eq!(validate_bundle(&root, &pin, deadline).unwrap(), value);
            release_fixture(&root);
        });
    }
}
