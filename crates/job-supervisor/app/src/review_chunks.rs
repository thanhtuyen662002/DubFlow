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
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[path = "review_chunk_output.rs"]
mod output;
pub(super) use output::{
    schedule_output, OutputChunkExecutor, OutputScheduleInputs, OutputScheduledChunks,
};

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
    /// Match an independently derived native source interval, never FPS or a
    /// container's rounded canonical millisecond duration. Metadata binding only.
    pub(super) fn require_source_bounds(
        &self,
        clock: [u64; 2],
        start: i64,
        end: i64,
    ) -> SupervisorResult<()> {
        if start >= end
            || self.value["time_base"]
                != json!({"numerator":clock[0].to_string(),"denominator":clock[1].to_string()})
            || self.value["source_interval"]
                != json!({"start_tick":start.to_string(),"end_tick":end.to_string()})
        {
            return Err(invalid(
                "chunk plan differs from the independently pinned source clock/interval",
            ));
        }
        Ok(())
    }

    /// Compare every point from a separately reopened complete observation.
    /// This closes the plan's counts/hashes/policy over that stream, without
    /// granting decoder, environment, encoded-media or publication authority.
    pub(super) fn require_frame_clock(
        &self,
        points: impl IntoIterator<Item = SupervisorResult<i64>>,
        mut fence: impl FnMut() -> SupervisorResult<()>,
    ) -> SupervisorResult<()> {
        fence()?;
        let chunks = self.value["chunks"].as_array().unwrap();
        let max_frames = self.value["policy"]["max_frames"].as_u64().unwrap();
        let target = tick(&self.value["policy"]["target_ticks"], true)?;
        let end = signed(&self.value["source_interval"]["end_tick"])?;
        let (mut ordinal, mut total, mut count) = (0usize, 0u64, 0u64);
        let mut first = signed(&chunks[0]["start_tick"])?;
        let mut previous = None;
        let mut whole_hash = Sha256::new();
        let mut chunk_hash = Sha256::new();
        for point in points {
            if total % 1024 == 0 {
                fence()?;
            }
            let point = point?;
            if point >= end || previous.is_some_and(|p| point <= p) {
                return Err(invalid(
                    "observed chunk clock is reordered or outside source",
                ));
            }
            if count != 0
                && (count >= max_frames
                    || i128::from(point) - i128::from(first) >= i128::from(target))
            {
                let old = &chunks[ordinal];
                if signed(&old["end_tick"])? != point
                    || tick(&old["frame_count"], true)? != count
                    || old["frame_clock_sha256"]
                        != format!("sha256:{:x}", chunk_hash.finalize_reset())
                {
                    return Err(invalid(
                        "chunk boundary/count/hash differs from observed policy",
                    ));
                }
                ordinal += 1;
                count = 0;
                first = point;
            }
            let chunk = chunks
                .get(ordinal)
                .ok_or_else(|| invalid("observed clock has a suffix after planned chunks"))?;
            let left = signed(&chunk["start_tick"])?;
            if point < left || point >= signed(&chunk["end_tick"])? || (count == 0 && point != left)
            {
                return Err(invalid(
                    "chunk cut does not follow exact observed source PTS",
                ));
            }
            let encoded = format!("{point}\n");
            whole_hash.update(encoded.as_bytes());
            chunk_hash.update(encoded.as_bytes());
            count += 1;
            total = total
                .checked_add(1)
                .filter(|n| *n <= 1_000_000)
                .ok_or_else(|| invalid("observed clock exceeds native journal frame bound"))?;
            previous = Some(point);
        }
        let last = &chunks[ordinal];
        if count == 0
            || ordinal + 1 != chunks.len()
            || tick(&last["frame_count"], true)? != count
            || last["frame_clock_sha256"] != format!("sha256:{:x}", chunk_hash.finalize())
            || tick(&self.value["frame_count"], true)? != total
            || self.value["frame_clock_sha256"] != format!("sha256:{:x}", whole_hash.finalize())
        {
            return Err(invalid(
                "observed clock loses planned final coverage or full digest",
            ));
        }
        fence()
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

const MAX_RECEIPT: u64 = 65536;
const MAX_INDEX_CHAIN: u64 = 16 * 1024 * 1024;

fn receipt_pin(v: &Value) -> SupervisorResult<AcceptedJsonDocument> {
    closed(v, &["path", "sha256", "size_bytes"])?;
    let path = v["path"]
        .as_str()
        .filter(|p| !p.is_empty() && p.len() <= 4096)
        .ok_or_else(|| invalid("chunk receipt needs a bounded native metadata path"))?;
    let size = bounded_number(&v["size_bytes"], MAX_RECEIPT)?;
    Ok(AcceptedJsonDocument {
        path: PathBuf::from(path),
        sha256: hash_text(&v["sha256"])?.into(),
        size_bytes: size,
    })
}

/// The index is an immutable private lookup, never a media/semantic grant.
/// Every selected receipt still passes the existing native independent reopen.
fn read_index_node(raw: &str, job: &str, plan: &BoundPlan) -> SupervisorResult<Value> {
    let v = decode(raw, MAX_RECEIPT as usize)?;
    closed(
        &v,
        &[
            "schema_version",
            "kind",
            "job_id",
            "source_sha256",
            "processing_recipe_sha256",
            "time_base",
            "origin_plan_sha256",
            "origin_context_sha256",
            "ordinal",
            "previous",
            "chunk",
            "complete",
            "semantic_qc_admitted",
            "active_graph_published",
        ],
    )?;
    if v["schema_version"].as_u64() != Some(1)
        || v["kind"] != "review_native_chunk_checkpoint_index"
        || v["job_id"] != job
        || v["source_sha256"] != plan.value["source_sha256"]
        || v["processing_recipe_sha256"] != plan.value["processing_recipe_sha256"]
        || v["time_base"] != plan.value["time_base"]
        || v["complete"].as_bool().is_none()
        || v["semantic_qc_admitted"] != false
        || v["active_graph_published"] != false
        || exact(&v)? != raw
    {
        return Err(invalid(
            "chunk checkpoint index substitutes native job/source/recipe/clock or authority",
        ));
    }
    hash_text(&v["origin_plan_sha256"])?;
    hash_text(&v["origin_context_sha256"])?;
    let ordinal = bounded_number(&v["ordinal"], 4096)?;
    closed(&v["chunk"], &["chunk_identity", "receipt"])?;
    hash_text(&v["chunk"]["chunk_identity"])?;
    receipt_pin(&v["chunk"]["receipt"])?;
    if (ordinal == 1) != v["previous"].is_null() {
        return Err(invalid("chunk index loses its bounded root/ordinal"));
    }
    if !v["previous"].is_null() {
        receipt_pin(&v["previous"])?;
    }
    Ok(v)
}

fn read_index_chain(
    latest: &AcceptedJsonDocument,
    job: &str,
    plan: &BoundPlan,
    mut read: impl FnMut(&AcceptedJsonDocument) -> SupervisorResult<String>,
) -> SupervisorResult<BTreeMap<String, Value>> {
    let mut current = latest.clone();
    let mut bytes = 0u64;
    let mut found = BTreeMap::new();
    let mut ordered = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    let mut origin = None;
    let mut expected = None;
    loop {
        if current.size_bytes > MAX_RECEIPT || !seen.insert(current.sha256.clone()) {
            return Err(invalid("chunk index repeats or exceeds a metadata node"));
        }
        bytes = bytes
            .checked_add(current.size_bytes)
            .filter(|n| *n <= MAX_INDEX_CHAIN)
            .ok_or_else(|| invalid("chunk index chain exceeds its aggregate metadata bound"))?;
        let raw = read(&current)?;
        if raw.len() as u64 != current.size_bytes || hash(&raw) != current.sha256 {
            return Err(invalid(
                "chunk index node differs from its exact physical metadata pin",
            ));
        }
        let node = read_index_node(&raw, job, plan)?;
        let ordinal = bounded_number(&node["ordinal"], 4096)?;
        let pins = (
            node["origin_plan_sha256"].clone(),
            node["origin_context_sha256"].clone(),
        );
        if let Some((old, number, _)) = &origin {
            if old != &pins
                || Some(ordinal) != expected
                || node["complete"] != false
                || *number > 4096
            {
                return Err(invalid(
                    "chunk index chain substitutes plan/context/ordinal or completion",
                ));
            }
        } else {
            origin = Some((pins, ordinal, node["complete"] == true));
        }
        let entry = &node["chunk"];
        let id = hash_text(&entry["chunk_identity"])?;
        if found.insert(id.into(), entry["receipt"].clone()).is_some() {
            return Err(invalid("chunk index chain repeats a local chunk"));
        }
        ordered.push(entry["chunk_identity"].clone());
        if node["previous"].is_null() {
            break;
        }
        expected = Some(ordinal - 1);
        current = receipt_pin(&node["previous"])?;
    }
    let ((origin_plan, context), count, complete) = origin.unwrap();
    if count as usize != ordered.len() {
        return Err(invalid("chunk index chain omits a completed prefix node"));
    }
    if origin_plan == plan.sha256 {
        let chunks = plan.value["chunks"].as_array().unwrap();
        if context != plan.value["context_sha256"]
            || ordered.len() > chunks.len()
            || (complete && ordered.len() != chunks.len())
            || ordered
                .iter()
                .rev()
                .zip(chunks)
                .any(|(id, c)| id != &c["identity"])
        {
            return Err(invalid(
                "current chunk index loses its ordered completion prefix",
            ));
        }
    }
    Ok(found)
}

fn index_packet(
    job: &str,
    plan: &BoundPlan,
    entry: &Value,
    ordinal: usize,
    previous: Option<&AcceptedJsonDocument>,
    complete: bool,
) -> SupervisorResult<Value> {
    let v = json!({"schema_version":1,"kind":"review_native_chunk_checkpoint_index","job_id":job,
        "source_sha256":plan.value["source_sha256"],"processing_recipe_sha256":plan.value["processing_recipe_sha256"],
        "time_base":plan.value["time_base"],"origin_plan_sha256":plan.sha256,
        "origin_context_sha256":plan.value["context_sha256"],"chunk":entry,"ordinal":ordinal,
        "previous":previous.map(super::review_correction::descriptor),"complete":complete,
        "semantic_qc_admitted":false,"active_graph_published":false});
    read_index_node(&exact(&v)?, job, plan)?;
    Ok(v)
}

fn encoded_pin(
    raw: &str,
    binding: &BoundChunk,
    environment: &str,
) -> SupervisorResult<(PathBuf, RetainedFilePin)> {
    let v = decode(raw, MAX_RECEIPT as usize)?;
    closed(
        &v,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "plan_sha256",
            "source_sha256",
            "processing_recipe_sha256",
            "runtime_environment_sha256",
            "chunk_identity",
            "caption_window_sha256",
            "source_frame_clock_sha256",
            "frame_count",
            "file",
            "full_video_decode",
            "semantic_qc_admitted",
            "active_graph_published",
        ],
    )?;
    hash_text(&json!(environment))?;
    if v["schema_version"].as_u64() != Some(1)
        || v["kind"] != "review_private_encoded_chunk_candidate"
        || v["context_sha256"] != binding.context_sha256
        || v["plan_sha256"] != binding.plan_sha256
        || v["source_sha256"] != binding.source
        || v["processing_recipe_sha256"] != binding.recipe
        || v["runtime_environment_sha256"] != environment
        || v["chunk_identity"] != binding.chunk["identity"]
        || v["caption_window_sha256"] != binding.chunk["visual_dependencies_sha256"]
        || v["source_frame_clock_sha256"] != binding.chunk["frame_clock_sha256"]
        || v["frame_count"] != binding.chunk["frame_count"]
        || v["full_video_decode"] != true
        || v["semantic_qc_admitted"] != false
        || v["active_graph_published"] != false
    {
        return Err(invalid(
            "encoded chunk substitutes native plan/local dependencies or claims graph/QC authority",
        ));
    }
    closed(&v["file"], &["path", "sha256", "size_bytes"])?;
    let path = v["file"]["path"]
        .as_str()
        .filter(|p| !p.is_empty() && p.len() <= 4096)
        .ok_or_else(|| invalid("encoded chunk needs a bounded private path hint"))?;
    let size = tick(&v["file"]["size_bytes"], true)?;
    if size > 256 * 1024 * 1024 {
        return Err(invalid("encoded chunk exceeds its private candidate bound"));
    }
    Ok((
        PathBuf::from(path),
        RetainedFilePin {
            sha256: hash_text(&v["file"]["sha256"])?.into(),
            size_bytes: size,
            format: RetainedFileFormat::Mp4,
        },
    ))
}

/// Only a native processing caller implements this interface. fence must hold
/// current token/source/processing/runtime closure authority; encode must use
/// native process-tree containment and its supplied absolute deadline. checkpoint
/// must durably record this immutable index before execution advances. No worker
/// packet, hash or callback alone supplies those admissions.
pub(super) trait NativeChunkExecutor {
    fn fence(&mut self, deadline: Instant) -> SupervisorResult<()>;
    fn encode(&mut self, request: &Value, deadline: Instant) -> SupervisorResult<String>;
    fn metadata_capacity(
        &mut self,
        additional_bytes: u64,
        deadline: Instant,
    ) -> SupervisorResult<()>;
    fn checkpoint(
        &mut self,
        index: &AcceptedJsonDocument,
        deadline: Instant,
    ) -> SupervisorResult<()>;
}

pub(super) struct ScheduleInputs<'a> {
    pub(super) assets: &'a AcceptedAudioStore,
    pub(super) output: &'a NativeOutputPin,
    pub(super) job: &'a str,
    pub(super) plan: &'a BoundPlan,
    pub(super) cache: Option<&'a AcceptedJsonDocument>,
    pub(super) candidates: &'a Path,
    pub(super) runtime_environment_sha256: &'a str,
    pub(super) deadline: Instant,
}

pub(super) struct ScheduledChunks {
    pub(super) index: AcceptedJsonDocument,
    pub(super) files: Vec<PhysicalChunk>,
    pub(super) encoded: usize,
    pub(super) reused: usize,
}

trait ScheduleBackend {
    type Kept;
    type Index;
    fn fence(&mut self, deadline: Instant) -> SupervisorResult<()>;
    fn reopen(
        &mut self,
        chunk: &BoundChunk,
        receipt: &Value,
        deadline: Instant,
    ) -> SupervisorResult<Self::Kept>;
    fn encode(
        &mut self,
        plan: &BoundPlan,
        chunk: &BoundChunk,
        deadline: Instant,
    ) -> SupervisorResult<Self::Kept>;
    fn receipt(kept: &Self::Kept) -> Value;
    fn index_packet(
        job: &str,
        plan: &BoundPlan,
        entry: &Value,
        ordinal: usize,
        previous: Option<&Self::Index>,
        complete: bool,
    ) -> SupervisorResult<Value>;
    fn checkpoint(&mut self, packet: &Value, deadline: Instant) -> SupervisorResult<Self::Index>;
}

fn guard(
    backend: &mut impl ScheduleBackend,
    deadline: Instant,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<()> {
    if cancelled() || Instant::now() >= deadline {
        return Err(invalid(
            "native chunk schedule cancelled or exceeded deadline",
        ));
    }
    backend.fence(deadline)?;
    if cancelled() || Instant::now() >= deadline {
        return Err(invalid("native chunk fence cancelled or exceeded deadline"));
    }
    Ok(())
}

fn drive<B: ScheduleBackend>(
    job: &str,
    plan: &BoundPlan,
    cache: BTreeMap<String, Value>,
    deadline: Instant,
    backend: &mut B,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<(B::Index, Vec<B::Kept>, usize, usize)> {
    super::validate_job_id(job)?;
    guard(backend, deadline, cancelled)?;
    let mut files = Vec::new();
    let (mut encoded, mut reused) = (0, 0);
    let mut last = None;
    let mut index_bytes = 0u64;
    let chunks = plan.value["chunks"].as_array().unwrap();
    for c in chunks {
        let chunk = BoundChunk {
            chunk: c.clone(),
            clock: plan.value["time_base"].clone(),
            source: plan.value["source_sha256"].clone(),
            recipe: plan.value["processing_recipe_sha256"].clone(),
            plan_sha256: plan.sha256.clone(),
            context_sha256: plan.value["context_sha256"].clone(),
        };
        let phase = deadline.min(Instant::now() + Duration::from_secs(900));
        guard(backend, phase, cancelled)?;
        let id = hash_text(&c["identity"])?;
        let kept = if let Some(receipt) = cache.get(id) {
            reused += 1;
            backend.reopen(&chunk, receipt, phase)?
        } else {
            encoded += 1;
            backend.encode(plan, &chunk, phase)?
        };
        guard(backend, phase, cancelled)?;
        let entry = json!({"chunk_identity":id,"receipt":B::receipt(&kept)});
        let packet = B::index_packet(
            job,
            plan,
            &entry,
            files.len() + 1,
            last.as_ref(),
            files.len() + 1 == chunks.len(),
        )?;
        index_bytes = index_bytes
            .checked_add(exact(&packet)?.len() as u64)
            .filter(|n| *n <= MAX_INDEX_CHAIN)
            .ok_or_else(|| {
                invalid("new chunk checkpoint chain exceeds its aggregate metadata bound")
            })?;
        let saved = backend.checkpoint(&packet, phase)?;
        guard(backend, phase, cancelled)?;
        files.push(kept);
        last = Some(saved);
    }
    Ok((
        last.ok_or_else(|| invalid("native chunk plan is empty"))?,
        files,
        encoded,
        reused,
    ))
}

struct PhysicalSchedule<'a, 'b, E> {
    input: ScheduleInputs<'a>,
    executor: &'b mut E,
}
impl<E: NativeChunkExecutor> ScheduleBackend for PhysicalSchedule<'_, '_, E> {
    type Kept = PhysicalChunk;
    type Index = AcceptedJsonDocument;
    fn index_packet(
        job: &str,
        p: &BoundPlan,
        e: &Value,
        n: usize,
        previous: Option<&AcceptedJsonDocument>,
        complete: bool,
    ) -> SupervisorResult<Value> {
        index_packet(job, p, e, n, previous, complete)
    }
    fn fence(&mut self, d: Instant) -> SupervisorResult<()> {
        self.executor.fence(d)
    }
    fn reopen(&mut self, c: &BoundChunk, v: &Value, d: Instant) -> SupervisorResult<PhysicalChunk> {
        reopen(
            self.input.assets,
            self.input.output,
            self.input.job,
            c,
            &receipt_pin(v)?,
            d,
        )
    }
    fn encode(
        &mut self,
        p: &BoundPlan,
        c: &BoundChunk,
        d: Instant,
    ) -> SupervisorResult<PhysicalChunk> {
        let request = json!({"schema_version":1,"kind":"review_native_chunk_work","plan_json":exact(&p.value)?,
            "plan_sha256":p.sha256,"context_sha256":p.value["context_sha256"],"chunk_identity":c.chunk["identity"],
            "runtime_environment_sha256":self.input.runtime_environment_sha256});
        let raw = self.executor.encode(&request, d)?;
        self.executor.fence(d)?;
        let (path, pin) = encoded_pin(&raw, c, self.input.runtime_environment_sha256)?;
        self.executor.metadata_capacity(MAX_RECEIPT, d)?;
        let kept = retain(
            self.input.assets,
            self.input.output,
            self.input.job,
            c,
            self.input.candidates,
            &path,
            &pin,
            d,
        )?;
        self.executor.metadata_capacity(0, d)?;
        Ok(kept)
    }
    fn receipt(k: &PhysicalChunk) -> Value {
        super::review_correction::descriptor(&k.receipt)
    }
    fn checkpoint(&mut self, p: &Value, d: Instant) -> SupervisorResult<AcceptedJsonDocument> {
        let raw = exact(p)?;
        self.executor.metadata_capacity(raw.len() as u64, d)?;
        let index = self.input.assets.accept_json(&raw)?;
        self.executor.metadata_capacity(0, d)?;
        self.executor.checkpoint(&index, d)?;
        self.input.assets.read_json(&index)?;
        Ok(index)
    }
}

/// Ordered native physical-checkpoint execution, with a checkpoint after every
/// completed chunk. This returns no semantic QC, AV or graph publication grant.
/// Source-clock indexed coverage, actual processing executor/runtime closure,
/// final audio/mux/QC and current render integration must be supplied separately.
pub(super) fn schedule<E: NativeChunkExecutor>(
    input: ScheduleInputs<'_>,
    executor: &mut E,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<ScheduledChunks> {
    hash_text(&json!(input.runtime_environment_sha256))?;
    super::validate_job_id(input.job)?;
    if cancelled() || Instant::now() >= input.deadline {
        return Err(invalid(
            "native chunk schedule cancelled or expired before admission",
        ));
    }
    executor.fence(input.deadline)?;
    let cache = if let Some(saved) = input.cache {
        read_index_chain(saved, input.job, input.plan, |object| {
            if cancelled() || Instant::now() >= input.deadline {
                return Err(invalid(
                    "native chunk cache read cancelled or exceeded deadline",
                ));
            }
            executor.fence(input.deadline)?;
            input.assets.read_json(object)
        })?
    } else {
        BTreeMap::new()
    };
    let job = input.job;
    let plan = input.plan;
    let deadline = input.deadline;
    let mut backend = PhysicalSchedule { input, executor };
    let (index, files, encoded, reused) =
        drive(job, plan, cache, deadline, &mut backend, cancelled)?;
    Ok(ScheduledChunks {
        index,
        files,
        encoded,
        reused,
    })
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
    fn clock_case(case: &Value) -> (BoundPlan, Vec<i64>) {
        let value = decode(case["plan_json"].as_str().unwrap(), MAX_PLAN).unwrap();
        let points = case["source_pts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| signed(p).unwrap())
            .collect();
        (read(&value).unwrap(), points)
    }
    #[test]
    fn complete_observed_clock_matches_every_negative_and_wide_vfr_plan_point() {
        for case in fixture()["cases"].as_array().unwrap() {
            let (plan, points) = clock_case(case);
            plan.require_frame_clock(points.into_iter().map(Ok), || Ok(()))
                .unwrap();
        }
    }
    #[test]
    fn observed_clock_refuses_missing_extra_changed_reordered_points_and_stream_errors() {
        let (plan, points) = clock_case(&fixture()["cases"][0]);
        for mutation in 0..5 {
            let mut changed = points.clone();
            match mutation {
                0 => {
                    changed.remove(2);
                }
                1 => changed.insert(2, -95),
                2 => changed[1] = -98,
                3 => changed.swap(1, 2),
                _ => changed.push(-41),
            }
            assert!(
                plan.require_frame_clock(changed.into_iter().map(Ok), || Ok(()))
                    .is_err(),
                "mutation{mutation}"
            );
        }
        let broken = points
            .into_iter()
            .map(Ok)
            .chain(std::iter::once(Err(invalid("point reader failed"))));
        assert!(plan.require_frame_clock(broken, || Ok(())).is_err());
    }
    #[test]
    fn observed_clock_refuses_valid_metadata_with_foreign_policy_or_whole_clock_digest() {
        let case = &fixture()["cases"][0];
        let (_, points) = clock_case(case);
        let original = decode(case["plan_json"].as_str().unwrap(), MAX_PLAN).unwrap();
        for n in 0..2 {
            let mut value = original.clone();
            if n == 0 {
                value["policy"]["target_ticks"] = json!("1000000");
                value["policy"]["max_frames"] = json!(1000);
            } else {
                value["frame_clock_sha256"] = json!(hash("different complete clock"));
            }
            // Metadata/hash admission alone accepts this self-consistent plan.
            let plan = read(&value).unwrap();
            assert!(plan
                .require_frame_clock(points.iter().copied().map(Ok), || Ok(()))
                .is_err());
        }
    }
    #[test]
    fn observed_clock_final_fence_refuses_late_cancel_or_expiry() {
        let (plan, points) = clock_case(&fixture()["cases"][1]);
        let mut checks = 0usize;
        let result = plan.require_frame_clock(points.into_iter().map(Ok), || {
            checks += 1;
            if checks == 3 {
                Err(invalid("cancelled after complete observation"))
            } else {
                Ok(())
            }
        });
        assert!(result.is_err());
        assert_eq!(checks, 3);
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
    #[derive(Default)]
    struct MockSchedule {
        nodes: BTreeMap<String, String>,
        saved: Vec<AcceptedJsonDocument>,
        events: Vec<String>,
        generated: usize,
        fail_encode: Option<usize>,
        fail_checkpoint: bool,
        fail_reopen: bool,
        stale: bool,
    }
    impl ScheduleBackend for MockSchedule {
        type Kept = Value;
        type Index = AcceptedJsonDocument;
        fn index_packet(
            job: &str,
            p: &BoundPlan,
            e: &Value,
            n: usize,
            previous: Option<&AcceptedJsonDocument>,
            complete: bool,
        ) -> SupervisorResult<Value> {
            index_packet(job, p, e, n, previous, complete)
        }
        fn fence(&mut self, _: Instant) -> SupervisorResult<()> {
            if self.stale {
                Err(invalid("changed current native fence"))
            } else {
                Ok(())
            }
        }
        fn reopen(&mut self, c: &BoundChunk, v: &Value, _: Instant) -> SupervisorResult<Value> {
            self.events
                .push(format!("R:{}", c.chunk["identity"].as_str().unwrap()));
            if self.fail_reopen {
                return Err(invalid("corrupt physical checkpoint; no encode fallback"));
            }
            receipt_pin(v)?;
            Ok(v.clone())
        }
        fn encode(&mut self, _: &BoundPlan, c: &BoundChunk, _: Instant) -> SupervisorResult<Value> {
            self.generated += 1;
            self.events
                .push(format!("E:{}", c.chunk["identity"].as_str().unwrap()));
            if self.fail_encode == Some(self.generated) {
                return Err(invalid("bounded encoder failed"));
            }
            Ok(
                json!({"path":std::env::temp_dir().join("mock-chunk-receipt"),"sha256":hash(&exact(&c.chunk)?),"size_bytes":42}),
            )
        }
        fn receipt(v: &Value) -> Value {
            v.clone()
        }
        fn checkpoint(&mut self, p: &Value, _: Instant) -> SupervisorResult<AcceptedJsonDocument> {
            let raw = exact(p)?;
            let sha256 = hash(&raw);
            self.events.push(format!("C:{}", p["ordinal"]));
            let object = AcceptedJsonDocument {
                path: std::env::temp_dir().join(&sha256[7..]),
                sha256: sha256.clone(),
                size_bytes: raw.len() as u64,
            };
            if let Some(old) = self.nodes.insert(sha256, raw.clone()) {
                assert_eq!(old, raw);
            }
            if self.fail_checkpoint {
                return Err(invalid(
                    "native pointer checkpoint failed; metadata is an orphan",
                ));
            }
            self.saved.push(object.clone());
            Ok(object)
        }
    }
    fn schedule_plan() -> Value {
        decode(
            fixture()["cases"][0]["plan_json"].as_str().unwrap(),
            MAX_PLAN,
        )
        .unwrap()
    }
    fn cache(
        mock: &MockSchedule,
        last: &AcceptedJsonDocument,
        p: &BoundPlan,
    ) -> SupervisorResult<BTreeMap<String, Value>> {
        read_index_chain(last, "chunk-job", p, |pin| {
            mock.nodes
                .get(&pin.sha256)
                .cloned()
                .ok_or_else(|| invalid("missing index node"))
        })
    }
    #[test]
    fn native_ordered_schedule_reuses_only_unchanged_local_chunks_after_other_cue_edit() {
        let mut value = schedule_plan();
        let first = read(&value).unwrap();
        let mut original = MockSchedule::default();
        let (last, _, encoded, reused) = drive(
            "chunk-job",
            &first,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(30),
            &mut original,
            &mut || false,
        )
        .unwrap();
        assert_eq!((encoded, reused), (3, 0));
        let preserved = original.nodes.clone();
        value["context_sha256"] = json!(hash("new cumulative current context"));
        value["chunks"][1]["visual_dependencies_sha256"] = json!(hash("changed only middle cue"));
        value["chunks"][1]["identity"] = json!(chunk_identity(
            &value["source_sha256"],
            &value["processing_recipe_sha256"],
            &value["time_base"],
            &value["chunks"][1]
        )
        .unwrap());
        let current = read(&value).unwrap();
        let lookup = cache(&original, &last, &current).unwrap();
        let mut next = MockSchedule::default();
        let (_, files, encoded, reused) = drive(
            "chunk-job",
            &current,
            lookup,
            Instant::now() + Duration::from_secs(30),
            &mut next,
            &mut || false,
        )
        .unwrap();
        assert_eq!((files.len(), encoded, reused), (3, 1, 2));
        assert_eq!(
            next.events
                .iter()
                .filter(|s| s.starts_with("R:") || s.starts_with("E:"))
                .map(|s| &s[..1])
                .collect::<Vec<_>>(),
            ["R", "E", "R"]
        );
        assert_eq!(original.nodes, preserved);
        let root = decode(
            next.nodes.get(&next.saved[0].sha256).unwrap(),
            MAX_RECEIPT as usize,
        )
        .unwrap();
        assert!(root["previous"].is_null());
        assert_eq!(root["ordinal"], 1);
    }
    #[test]
    fn native_partial_checkpoint_chain_resumes_without_replaying_completed_chunk() {
        let p = read(&schedule_plan()).unwrap();
        let mut first = MockSchedule {
            fail_encode: Some(2),
            ..Default::default()
        };
        assert!(drive(
            "chunk-job",
            &p,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(30),
            &mut first,
            &mut || false
        )
        .is_err());
        assert_eq!(first.saved.len(), 1);
        let latest = first.saved[0].clone();
        assert_eq!(
            decode(first.nodes.get(&latest.sha256).unwrap(), 65536).unwrap()["complete"],
            false
        );
        let lookup = cache(&first, &latest, &p).unwrap();
        let prior = first.nodes.clone();
        let mut next = MockSchedule::default();
        let (last, _, encoded, reused) = drive(
            "chunk-job",
            &p,
            lookup,
            Instant::now() + Duration::from_secs(30),
            &mut next,
            &mut || false,
        )
        .unwrap();
        assert_eq!((encoded, reused), (2, 1));
        assert!(next.events[0].starts_with("R:"));
        assert_eq!(first.nodes, prior);
        assert_eq!(cache(&next, &last, &p).unwrap().len(), 3);
    }
    #[test]
    fn native_schedule_refuses_cancel_stale_deadline_checkpoint_and_corrupt_reuse_without_advancing(
    ) {
        let p = read(&schedule_plan()).unwrap();
        for mode in 0..4 {
            let mut mock = MockSchedule {
                stale: mode == 1,
                fail_checkpoint: mode == 3,
                ..Default::default()
            };
            let deadline = if mode == 2 {
                Instant::now() - Duration::from_secs(1)
            } else {
                Instant::now() + Duration::from_secs(30)
            };
            assert!(drive(
                "chunk-job",
                &p,
                BTreeMap::new(),
                deadline,
                &mut mock,
                &mut || mode == 0
            )
            .is_err());
            assert_eq!(mock.generated, if mode == 3 { 1 } else { 0 });
            assert!(mock.saved.is_empty());
        }
        let mut base = MockSchedule::default();
        let (last, _, _, _) = drive(
            "chunk-job",
            &p,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(30),
            &mut base,
            &mut || false,
        )
        .unwrap();
        let lookup = cache(&base, &last, &p).unwrap();
        let mut corrupt = MockSchedule {
            fail_reopen: true,
            ..Default::default()
        };
        assert!(drive(
            "chunk-job",
            &p,
            lookup,
            Instant::now() + Duration::from_secs(30),
            &mut corrupt,
            &mut || false
        )
        .is_err());
        assert_eq!(corrupt.generated, 0);
        assert!(corrupt.saved.is_empty());
    }
    fn encoded(binding: &BoundChunk, environment: &str) -> Value {
        json!({"schema_version":1,"kind":"review_private_encoded_chunk_candidate","context_sha256":binding.context_sha256,
            "plan_sha256":binding.plan_sha256,"source_sha256":binding.source,"processing_recipe_sha256":binding.recipe,
            "runtime_environment_sha256":environment,"chunk_identity":binding.chunk["identity"],
            "caption_window_sha256":binding.chunk["visual_dependencies_sha256"],"source_frame_clock_sha256":binding.chunk["frame_clock_sha256"],
            "frame_count":binding.chunk["frame_count"],"file":{"path":std::env::temp_dir().join("mock-only.mp4"),"sha256":hash("video bytes"),"size_bytes":"12"},
            "full_video_decode":true,"semantic_qc_admitted":false,"active_graph_published":false})
    }
    #[test]
    fn native_encoded_candidate_and_index_refuse_foreign_pins_counts_fields_and_authority() {
        let p = read(&schedule_plan()).unwrap();
        let binding = p
            .chunk(p.value["chunks"][0]["identity"].as_str().unwrap())
            .unwrap();
        let env = hash("native runtime closure");
        let original = encoded(&binding, &env);
        assert!(encoded_pin(&exact(&original).unwrap(), &binding, &env).is_ok());
        for key in [
            "context_sha256",
            "plan_sha256",
            "source_sha256",
            "processing_recipe_sha256",
            "runtime_environment_sha256",
            "chunk_identity",
            "caption_window_sha256",
            "source_frame_clock_sha256",
        ] {
            let mut bad = original.clone();
            bad[key] = json!(hash("foreign"));
            assert!(
                encoded_pin(&exact(&bad).unwrap(), &binding, &env).is_err(),
                "{key}"
            );
        }
        for key in [
            "schema_version",
            "frame_count",
            "full_video_decode",
            "semantic_qc_admitted",
            "active_graph_published",
            "extra",
        ] {
            let mut bad = original.clone();
            bad[key] = if key == "full_video_decode" {
                json!(false)
            } else {
                json!(true)
            };
            assert!(
                encoded_pin(&exact(&bad).unwrap(), &binding, &env).is_err(),
                "{key}"
            );
        }
        for size in [
            json!(0),
            json!(true),
            json!("0"),
            json!("268435457"),
            json!("+12"),
        ] {
            let mut bad = original.clone();
            bad["file"]["size_bytes"] = size;
            assert!(encoded_pin(&exact(&bad).unwrap(), &binding, &env).is_err());
        }
        let entry = json!({"chunk_identity":binding.chunk["identity"],"receipt":{"path":std::env::temp_dir().join("metadata"),"sha256":hash("receipt"),"size_bytes":20}});
        let node = index_packet("chunk-job", &p, &entry, 1, None, false).unwrap();
        for key in [
            "job_id",
            "source_sha256",
            "processing_recipe_sha256",
            "origin_plan_sha256",
            "time_base",
            "ordinal",
            "previous",
            "semantic_qc_admitted",
            "active_graph_published",
            "foreign",
        ] {
            let mut bad = node.clone();
            bad[key] = json!(true);
            assert!(
                read_index_node(&exact(&bad).unwrap(), "chunk-job", &p).is_err(),
                "{key}"
            );
        }
    }
    #[test]
    fn native_checkpoint_chain_is_linear_bounded_and_refuses_missing_or_changed_metadata() {
        let mut value = schedule_plan();
        value["time_base"] = json!({"numerator":"1","denominator":"1000"});
        value["source_interval"] = json!({"start_tick":"0","end_tick":"4096"});
        value["frame_count"] = json!("4096");
        value["policy"] = json!({"target_ticks":"1","max_frames":1,"max_chunks":4096});
        let chunks=(0..4096).map(|i| {
            let mut c=json!({"start_tick":i.to_string(),"end_tick":(i+1).to_string(),"frame_count":"1",
                "frame_clock_sha256":hash(&format!("mock frame {i}")),"visual_dependencies_sha256":hash("unchanged captions"),"identity":hash("temporary")});
            c["identity"]=json!(chunk_identity(&value["source_sha256"],&value["processing_recipe_sha256"],&value["time_base"],&c).unwrap());c
        }).collect::<Vec<_>>();
        value["chunks"] = json!(chunks);
        let p = read(&value).unwrap();
        let mut mock = MockSchedule::default();
        let (last, _, encoded, reused) = drive(
            "chunk-job",
            &p,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(60),
            &mut mock,
            &mut || false,
        )
        .unwrap();
        assert_eq!((encoded, reused, mock.nodes.len()), (4096, 0, 4096));
        assert!(mock.nodes.values().map(|s| s.len() as u64).sum::<u64>() <= MAX_INDEX_CHAIN);
        assert_eq!(cache(&mock, &last, &p).unwrap().len(), 4096);
        let mut missing = mock.nodes.clone();
        missing.remove(&mock.saved[0].sha256);
        assert!(read_index_chain(&last, "chunk-job", &p, |pin| missing
            .get(&pin.sha256)
            .cloned()
            .ok_or_else(|| invalid("missing")))
        .is_err());
        assert!(read_index_chain(&last, "chunk-job", &p, |_| Ok("{}".into())).is_err());
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
