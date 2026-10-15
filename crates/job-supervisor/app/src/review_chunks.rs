//! Physical-only immutable longform chunk checkpoints under existing native
//! output-volume/quota/locator rules. Neither a plan nor retained bytes admits
//! decoded source coverage, processing provenance, codec/AV QC or graph use.
use super::review_assets::{
    AcceptedAudioStore, AcceptedJsonDocument, RetainedFileFormat, RetainedFilePin,
};
use super::review_document::{canonical, closed, decode, tick};
use super::review_retention::{LocatedRenderFile, NativeMixStore, NativeOutputPin};
use super::{SupervisorError, SupervisorResult};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::time::Instant;

const MAX_PLAN: usize = 4 * 1024 * 1024;
const CHUNK_FIELDS: &[&str] = &[
    "start_tick",
    "end_tick",
    "frame_count",
    "frame_clock_sha256",
    "visual_dependencies_sha256",
    "identity",
];

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(raw: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
}
fn hash_text(value: &Value) -> SupervisorResult<&str> {
    value
        .as_str()
        .filter(|s| {
            s.len() == 71
                && s.starts_with("sha256:")
                && s[7..]
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
        .ok_or_else(|| invalid("longform needs an exact full lowercase SHA256 pin"))
}
fn exact(value: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    canonical(value, &mut raw)?;
    Ok(raw)
}
fn signed(value: &Value) -> SupervisorResult<i64> {
    let text = value
        .as_str()
        .filter(|s| s.len() <= 20)
        .ok_or_else(|| invalid("longform PTS must be a signed64 decimal string"))?;
    let number = text
        .parse::<i64>()
        .map_err(|_| invalid("longform PTS exceeds signed64"))?;
    if number.to_string() != text {
        return Err(invalid("longform PTS is not canonical"));
    }
    Ok(number)
}
fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
}
fn bounded_number(value: &Value, maximum: u64) -> SupervisorResult<u64> {
    value
        .as_u64()
        .filter(|n| *n > 0 && *n <= maximum)
        .ok_or_else(|| invalid("longform numeric bound is invalid"))
}
fn chunk_identity(
    source: &Value,
    recipe: &Value,
    clock: &Value,
    chunk: &Value,
) -> SupervisorResult<String> {
    let mut identity = chunk
        .as_object()
        .ok_or_else(|| invalid("longform chunk must be an object"))?
        .clone();
    identity.remove("identity");
    identity.insert("schema_version".into(), json!(1));
    identity.insert("kind".into(), json!("review_longform_video_chunk_identity"));
    identity.insert("source_sha256".into(), source.clone());
    identity.insert("processing_recipe_sha256".into(), recipe.clone());
    identity.insert("time_base".into(), clock.clone());
    Ok(hash(&exact(&Value::Object(identity))?))
}

/// Metadata bound to separately trusted native pins. This type grants only
/// physical checkpoint selection; the native render caller still admits the
/// processing release and independently decoded clock, local dependencies/QC.
pub(super) struct BoundPlan {
    value: Value,
    sha256: String,
}
#[derive(Clone)]
pub(super) struct BoundChunk {
    chunk: Value,
    clock: Value,
    source: Value,
    recipe: Value,
    plan_sha256: String,
    context_sha256: Value,
}
impl BoundPlan {
    pub(super) fn read(
        raw: &str,
        expected_plan: &str,
        context: &str,
        source: &str,
        recipe: &str,
    ) -> SupervisorResult<Self> {
        for pin in [expected_plan, context, source, recipe] {
            hash_text(&json!(pin))?;
        }
        if hash(raw) != expected_plan {
            return Err(invalid("longform plan bytes differ from native pin"));
        }
        let plan = decode(raw, MAX_PLAN)?;
        closed(
            &plan,
            &[
                "schema_version",
                "kind",
                "recipe",
                "context_sha256",
                "source_sha256",
                "processing_recipe_sha256",
                "time_base",
                "source_interval",
                "policy",
                "frame_count",
                "frame_clock_sha256",
                "chunks",
            ],
        )?;
        if plan["schema_version"].as_u64() != Some(1)
            || plan["kind"] != "review_longform_render_plan"
            || plan["recipe"] != "review-source-pts-chunks-v1"
            || plan["context_sha256"] != context
            || plan["source_sha256"] != source
            || plan["processing_recipe_sha256"] != recipe
            || exact(&plan)? != raw
        {
            return Err(invalid(
                "longform plan changed format, context, source, recipe or canonical bytes",
            ));
        }
        let clock = &plan["time_base"];
        closed(clock, &["numerator", "denominator"])?;
        let n = tick(&clock["numerator"], true)?;
        let d = tick(&clock["denominator"], true)?;
        if gcd(n, d) != 1 {
            return Err(invalid("longform time base must be reduced"));
        }
        closed(&plan["source_interval"], &["start_tick", "end_tick"])?;
        let start = signed(&plan["source_interval"]["start_tick"])?;
        let end = signed(&plan["source_interval"]["end_tick"])?;
        let duration = end
            .checked_sub(start)
            .filter(|v| *v > 0)
            .ok_or_else(|| invalid("longform source duration exceeds positive signed64"))?;
        if i128::from(duration) * i128::from(n) > 21600 * i128::from(d) {
            return Err(invalid("longform source exceeds six-hour policy"));
        }
        let policy = &plan["policy"];
        closed(policy, &["target_ticks", "max_frames", "max_chunks"])?;
        tick(&policy["target_ticks"], true)?;
        let max_frames = bounded_number(&policy["max_frames"], 1_000_000)?;
        let max_chunks = bounded_number(&policy["max_chunks"], 4096)?;
        let expected_frames = tick(&plan["frame_count"], true)?;
        hash_text(&plan["frame_clock_sha256"])?;
        let chunks = plan["chunks"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= max_chunks as usize)
            .ok_or_else(|| invalid("longform chunk inventory exceeds its policy"))?;
        let (mut previous, mut frames) = (start, 0u64);
        for chunk in chunks {
            closed(chunk, CHUNK_FIELDS)?;
            let left = signed(&chunk["start_tick"])?;
            let right = signed(&chunk["end_tick"])?;
            let count = tick(&chunk["frame_count"], true)?;
            if left != previous
                || right <= left
                || right > end
                || count > max_frames
                || i128::from(count) > i128::from(right) - i128::from(left)
            {
                return Err(invalid(
                    "longform chunk loses ordered source coverage or frame bounds",
                ));
            }
            hash_text(&chunk["frame_clock_sha256"])?;
            hash_text(&chunk["visual_dependencies_sha256"])?;
            if hash_text(&chunk["identity"])?
                != chunk_identity(
                    &plan["source_sha256"],
                    &plan["processing_recipe_sha256"],
                    clock,
                    chunk,
                )?
            {
                return Err(invalid(
                    "longform chunk identity differs from its exact local dependencies",
                ));
            }
            previous = right;
            frames = frames
                .checked_add(count)
                .filter(|v| *v <= i64::MAX as u64)
                .ok_or_else(|| invalid("longform frame total exceeds signed64"))?;
        }
        if previous != end || frames != expected_frames {
            return Err(invalid(
                "longform plan loses final source coverage or frame totals",
            ));
        }
        Ok(Self {
            value: plan,
            sha256: expected_plan.into(),
        })
    }
    pub(super) fn chunk(&self, identity: &str) -> SupervisorResult<BoundChunk> {
        hash_text(&json!(identity))?;
        let chunk = self.value["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["identity"] == identity)
            .ok_or_else(|| invalid("longform chunk is absent from its current admitted plan"))?;
        Ok(BoundChunk {
            chunk: chunk.clone(),
            clock: self.value["time_base"].clone(),
            source: self.value["source_sha256"].clone(),
            recipe: self.value["processing_recipe_sha256"].clone(),
            plan_sha256: self.sha256.clone(),
            context_sha256: self.value["context_sha256"].clone(),
        })
    }
}

pub(super) struct PhysicalChunk {
    pub(super) receipt: AcceptedJsonDocument,
    pub(super) file: LocatedRenderFile,
}

fn admit_receipt(raw: &str, job: &str, binding: &BoundChunk) -> SupervisorResult<Value> {
    super::validate_job_id(job)?;
    let v = decode(raw, 65536)?;
    closed(
        &v,
        &[
            "schema_version",
            "kind",
            "job_id",
            "source_sha256",
            "processing_recipe_sha256",
            "time_base",
            "chunk",
            "origin_plan_sha256",
            "origin_context_sha256",
            "file",
            "semantic_qc_admitted",
            "active_graph_published",
        ],
    )?;
    if v["schema_version"].as_u64() != Some(1)
        || v["kind"] != "review_native_retained_chunk"
        || v["job_id"] != job
        || v["source_sha256"] != binding.source
        || v["processing_recipe_sha256"] != binding.recipe
        || v["time_base"] != binding.clock
        || v["chunk"] != binding.chunk
        || v["file"]["format"] != "mp4"
        || v["semantic_qc_admitted"] != false
        || v["active_graph_published"] != false
    {
        return Err(invalid(
            "retained longform checkpoint changed job/clock/dependencies or claimed semantic authority",
        ));
    }
    // These original request pins preserve history but do not prevent current
    // reuse of a physically unchanged chunk after an unrelated edit or undo.
    hash_text(&v["origin_plan_sha256"])?;
    hash_text(&v["origin_context_sha256"])?;
    Ok(v)
}

pub(super) fn reopen(
    assets: &AcceptedAudioStore,
    output: &NativeOutputPin,
    job: &str,
    binding: &BoundChunk,
    receipt: &AcceptedJsonDocument,
    deadline: Instant,
) -> SupervisorResult<PhysicalChunk> {
    let v = admit_receipt(&assets.read_json(receipt)?, job, binding)?;
    let store = NativeMixStore::open_existing(output, job, super::review_render::storage_budget())?;
    let file = store.reopen_file(&v["file"], deadline)?;
    Ok(PhysicalChunk {
        receipt: receipt.clone(),
        file,
    })
}

/// This retains bytes only. The future render caller must run separate codec,
/// complete source-clock/frame/geometry/AV/QC gates before any graph publication.
pub(super) fn retain(
    assets: &AcceptedAudioStore,
    output: &NativeOutputPin,
    job: &str,
    binding: &BoundChunk,
    candidate_root: &Path,
    candidate: &Path,
    pin: &RetainedFilePin,
    deadline: Instant,
) -> SupervisorResult<PhysicalChunk> {
    if pin.format != RetainedFileFormat::Mp4 {
        return Err(invalid(
            "longform video checkpoint requires the MP4 physical role",
        ));
    }
    let store = NativeMixStore::new(output, job, super::review_render::storage_budget())?;
    let file = store.accept_file(candidate_root, candidate, pin, deadline)?;
    let record = json!({"schema_version":1,"kind":"review_native_retained_chunk","job_id":job,
        "source_sha256":binding.source,"processing_recipe_sha256":binding.recipe,"time_base":binding.clock,"chunk":binding.chunk,
        "origin_plan_sha256":binding.plan_sha256,"origin_context_sha256":binding.context_sha256,"file":file.descriptor(),
        "semantic_qc_admitted":false,"active_graph_published":false});
    let raw = exact(&record)?;
    admit_receipt(&raw, job, binding)?;
    let receipt = assets.accept_json(&raw)?;
    store.verify_file(&file, deadline)?;
    drop(store); // Independent reopen must reacquire the existing writer fence.
    reopen(assets, output, job, binding, &receipt, deadline)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/review/native_longform_plan_v1.json"
        ))
        .unwrap()
    }
    fn read(v: &Value) -> SupervisorResult<BoundPlan> {
        let raw = exact(v)?;
        BoundPlan::read(
            &raw,
            &hash(&raw),
            v["context_sha256"].as_str().unwrap(),
            v["source_sha256"].as_str().unwrap(),
            v["processing_recipe_sha256"].as_str().unwrap(),
        )
    }
    #[test]
    fn python_frame_clock_plan_has_exact_native_chunk_keys_and_wide_integer_coverage() {
        for case in fixture()["cases"].as_array().unwrap() {
            let raw = case["plan_json"].as_str().unwrap();
            let plan = decode(raw, MAX_PLAN).unwrap();
            let p = BoundPlan::read(
                raw,
                case["plan_sha256"].as_str().unwrap(),
                plan["context_sha256"].as_str().unwrap(),
                plan["source_sha256"].as_str().unwrap(),
                plan["processing_recipe_sha256"].as_str().unwrap(),
            )
            .unwrap();
            for c in plan["chunks"].as_array().unwrap() {
                assert_eq!(p.chunk(c["identity"].as_str().unwrap()).unwrap().chunk, *c);
            }
        }
    }
    #[test]
    fn plan_refuses_foreign_fields_clock_keys_gaps_totals_and_native_pin_changes() {
        let original = decode(
            fixture()["cases"][0]["plan_json"].as_str().unwrap(),
            MAX_PLAN,
        )
        .unwrap();
        for n in 0..8 {
            let mut v = original.clone();
            match n {
                0 => v["schema_version"] = json!(true),
                1 => v["extra"] = json!(1),
                2 => v["time_base"]["numerator"] = json!("2"),
                3 => v["chunks"][0]["identity"] = json!(format!("sha256:{}", "a".repeat(64))),
                4 => v["chunks"][1]["start_tick"] = json!("1"),
                5 => v["frame_count"] = json!("9000"),
                6 => v["policy"]["max_frames"] = json!(true),
                _ => v["source_interval"]["start_tick"] = json!("-0"),
            }
            assert!(read(&v).is_err(), "case{n}");
        }
        let raw = exact(&original).unwrap();
        let source = original["source_sha256"].as_str().unwrap();
        let recipe = original["processing_recipe_sha256"].as_str().unwrap();
        let context = original["context_sha256"].as_str().unwrap();
        assert!(BoundPlan::read(
            &format!("{raw} "),
            &hash(&format!("{raw} ")),
            context,
            source,
            recipe
        )
        .is_err());
        assert!(BoundPlan::read(
            &raw,
            &hash(&raw),
            &format!("sha256:{}", "f".repeat(64)),
            source,
            recipe
        )
        .is_err());
        assert!(BoundPlan::read(
            &raw,
            &format!("sha256:{}", "f".repeat(64)),
            context,
            source,
            recipe
        )
        .is_err());
    }
    fn receipt(binding: &BoundChunk) -> Value {
        json!({"schema_version":1,"kind":"review_native_retained_chunk","job_id":"chunk-job",
        "source_sha256":binding.source,"processing_recipe_sha256":binding.recipe,"time_base":binding.clock,"chunk":binding.chunk,
        "origin_plan_sha256":binding.plan_sha256,"origin_context_sha256":binding.context_sha256,
        "file":{"format":"mp4"},"semantic_qc_admitted":false,"active_graph_published":false})
    }
    #[test]
    fn metadata_receipt_reuses_only_same_local_chunk_after_unrelated_current_context_change() {
        let mut v = decode(
            fixture()["cases"][0]["plan_json"].as_str().unwrap(),
            MAX_PLAN,
        )
        .unwrap();
        let id = v["chunks"][0]["identity"].as_str().unwrap().to_owned();
        let first = read(&v).unwrap().chunk(&id).unwrap();
        let saved = exact(&receipt(&first)).unwrap();
        v["context_sha256"] = json!(format!("sha256:{}", "e".repeat(64)));
        let later = read(&v).unwrap().chunk(&id).unwrap();
        assert!(admit_receipt(&saved, "chunk-job", &later).is_ok());
        for n in 0..6 {
            let mut wrong = receipt(&first);
            match n {
                0 => wrong["semantic_qc_admitted"] = json!(true),
                1 => wrong["active_graph_published"] = json!(true),
                2 => wrong["job_id"] = json!("foreign-job"),
                3 => wrong["chunk"]["frame_count"] = json!("1"),
                4 => {
                    wrong["processing_recipe_sha256"] = json!(format!("sha256:{}", "c".repeat(64)))
                }
                _ => wrong["file"]["format"] = json!("json"),
            }
            assert!(
                admit_receipt(&exact(&wrong).unwrap(), "chunk-job", &later).is_err(),
                "case{n}"
            );
        }
    }
    #[cfg(windows)]
    #[test]
    fn actual_windows_chunk_checkpoint_reopens_same_locator_and_refuses_corruption_and_foreign_job()
    {
        use std::fs;
        use std::sync::atomic::Ordering;
        use std::time::Duration;
        let temp = std::env::temp_dir();
        let root = temp.join(format!(
            "dubflow-chunk-checkpoint-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let data = root.join("data");
        fs::create_dir(&data).unwrap();
        fs::create_dir(data.join("control")).unwrap();
        let output = root.join("output");
        fs::create_dir(&output).unwrap();
        let candidate = root.join("candidate");
        fs::create_dir(&candidate).unwrap();
        let path = candidate.join("chunk.mp4");
        let bytes = b"storage fixture, no codec or semantic QC admission";
        fs::write(&path, bytes).unwrap();
        let pin = RetainedFilePin {
            sha256: format!("sha256:{:x}", Sha256::digest(bytes)),
            size_bytes: bytes.len() as u64,
            format: RetainedFileFormat::Mp4,
        };
        let plan = decode(
            fixture()["cases"][0]["plan_json"].as_str().unwrap(),
            MAX_PLAN,
        )
        .unwrap();
        let id = plan["chunks"][0]["identity"].as_str().unwrap();
        let binding = read(&plan).unwrap().chunk(id).unwrap();
        let assets = AcceptedAudioStore::new(&data, "chunk-job").unwrap();
        let volume = NativeOutputPin::read(&output).unwrap();
        let deadline = || Instant::now() + Duration::from_secs(30);
        let kept = retain(
            &assets,
            &volume,
            "chunk-job",
            &binding,
            &candidate,
            &path,
            &pin,
            deadline(),
        )
        .unwrap();
        assert_eq!(
            kept.file.descriptor()["locator"]["volume_id"],
            volume.volume_id()
        );
        let before = fs::metadata(kept.file.path()).unwrap().modified().unwrap();
        fs::remove_file(path).unwrap();
        let opened = reopen(
            &assets,
            &volume,
            "chunk-job",
            &binding,
            &kept.receipt,
            deadline(),
        )
        .unwrap();
        assert_eq!(opened.file.path(), kept.file.path());
        assert_eq!(
            fs::metadata(opened.file.path())
                .unwrap()
                .modified()
                .unwrap(),
            before
        );
        assert!(reopen(
            &assets,
            &volume,
            "other-job",
            &binding,
            &kept.receipt,
            deadline()
        )
        .is_err());
        assert!(reopen(
            &assets,
            &volume,
            "chunk-job",
            &binding,
            &kept.receipt,
            Instant::now()
        )
        .is_err());
        let mut perms = fs::metadata(kept.file.path()).unwrap().permissions();
        perms.set_readonly(false);
        fs::set_permissions(kept.file.path(), perms).unwrap();
        assert!(reopen(
            &assets,
            &volume,
            "chunk-job",
            &binding,
            &kept.receipt,
            deadline()
        )
        .is_err());
        fs::write(kept.file.path(), vec![b'x'; bytes.len()]).unwrap();
        let mut perms = fs::metadata(kept.file.path()).unwrap().permissions();
        perms.set_readonly(true);
        fs::set_permissions(kept.file.path(), perms).unwrap();
        assert!(reopen(
            &assets,
            &volume,
            "chunk-job",
            &binding,
            &kept.receipt,
            deadline()
        )
        .is_err());
        // Teardown only this newly allocated direct child of canonical TEMP.
        assert_eq!(
            fs::canonicalize(&root).unwrap().parent(),
            Some(fs::canonicalize(&temp).unwrap().as_path())
        );
        assert!(root
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("dubflow-chunk-checkpoint-"));
        fn unseal(p: &Path) {
            for e in fs::read_dir(p).unwrap() {
                let p = e.unwrap().path();
                let m = fs::symlink_metadata(&p).unwrap();
                assert!(!m.file_type().is_symlink());
                if m.is_dir() {
                    unseal(&p)
                } else {
                    let mut x = m.permissions();
                    x.set_readonly(false);
                    fs::set_permissions(&p, x).unwrap();
                }
            }
        }
        unseal(&root);
        fs::remove_dir_all(root).unwrap();
    }
}
