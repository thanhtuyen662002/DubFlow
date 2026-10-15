//! Output-owned version2 physical chunk receipts/indexes. No DATA fallback,
//! original-format migration, semantic admission or current-render activation.
use super::super::review_assets::{RetainedFileFormat, RetainedFilePin};
use super::super::review_document::{closed, decode, tick};
use super::super::review_retention::{LocatedRenderFile, NativeMixStore, NativeOutputPin};
use super::super::{review_render, validate_job_id, SupervisorResult};
use super::{
    bounded_number, drive, encoded_pin, exact, hash, hash_text, invalid, BoundChunk, BoundPlan,
    ScheduleBackend, MAX_INDEX_CHAIN, MAX_RECEIPT,
};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Instant;

/// Native metadata paths are reconstructed only through canonical locators.
/// Parsing the hash/size fields alone grants no IO or physical admission.
fn record_pin(v: &Value) -> SupervisorResult<(String, u64)> {
    closed(v, &["path_hint", "format", "locator"])?;
    let hint = v["path_hint"]
        .as_str()
        .filter(|s| s.len() <= 4096)
        .ok_or_else(|| invalid("output checkpoint hint exceeds its bound"))?;
    let _ = hint;
    if v["format"] != "json" {
        return Err(invalid("output checkpoint metadata requires JSON role"));
    }
    let l = &v["locator"];
    closed(
        l,
        &[
            "schema_version",
            "volume_id",
            "root_kind",
            "relative_path",
            "content_hash",
            "size_bytes",
        ],
    )?;
    let digest = hash_text(&l["content_hash"])?;
    let size = tick(&l["size_bytes"], true)?;
    if size > MAX_RECEIPT || l["schema_version"].as_u64() != Some(1) || l["root_kind"] != "output" {
        return Err(invalid(
            "output checkpoint locator changes version/root or small record bound",
        ));
    }
    let volume = l["volume_id"]
        .as_str()
        .ok_or_else(|| invalid("output checkpoint needs native volume"))?;
    let relative = l["relative_path"]
        .as_str()
        .filter(|s| s.len() <= 4096)
        .ok_or_else(|| invalid("output checkpoint locator path exceeds bound"))?;
    dubflow_artifact_storage::ArtifactLocator::new(
        volume,
        dubflow_job_storage::RootKind::Output,
        Path::new(relative),
        digest,
        size,
    )
    .map_err(|e| invalid(&format!("output checkpoint locator refused: {e}")))?;
    Ok((digest.into(), size))
}

fn common(v: &Value, job: &str, p: &BoundPlan, kind: &str) -> SupervisorResult<()> {
    validate_job_id(job)?;
    if v["schema_version"].as_u64() != Some(2)
        || v["kind"] != kind
        || v["job_id"] != job
        || v["source_sha256"] != p.value["source_sha256"]
        || v["processing_recipe_sha256"] != p.value["processing_recipe_sha256"]
        || v["time_base"] != p.value["time_base"]
        || v["semantic_qc_admitted"] != false
        || v["active_graph_published"] != false
    {
        return Err(invalid(
            "output checkpoint substitutes source/recipe/clock/job/version or claims authority",
        ));
    }
    hash_text(&v["origin_plan_sha256"])?;
    hash_text(&v["origin_context_sha256"])?;
    Ok(())
}

fn node(raw: &str, job: &str, p: &BoundPlan) -> SupervisorResult<Value> {
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
    common(&v, job, p, "review_native_output_chunk_index")?;
    if exact(&v)? != raw || v["complete"].as_bool().is_none() {
        return Err(invalid(
            "output checkpoint node is not exact canonical metadata",
        ));
    }
    let ordinal = bounded_number(&v["ordinal"], 4096)?;
    if (ordinal == 1) != v["previous"].is_null() {
        return Err(invalid("output checkpoint chain root/ordinal mismatch"));
    }
    if ordinal > 1 {
        record_pin(&v["previous"])?;
    }
    closed(&v["chunk"], &["chunk_identity", "receipt"])?;
    hash_text(&v["chunk"]["chunk_identity"])?;
    record_pin(&v["chunk"]["receipt"])?;
    Ok(v)
}

fn packet(
    job: &str,
    p: &BoundPlan,
    e: &Value,
    n: usize,
    previous: Option<&Value>,
    complete: bool,
) -> SupervisorResult<Value> {
    let v = json!({"schema_version":2,"kind":"review_native_output_chunk_index","job_id":job,
        "source_sha256":p.value["source_sha256"],"processing_recipe_sha256":p.value["processing_recipe_sha256"],
        "time_base":p.value["time_base"],"origin_plan_sha256":p.sha256,"origin_context_sha256":p.value["context_sha256"],
        "ordinal":n,"previous":previous,"chunk":e,"complete":complete,"semantic_qc_admitted":false,"active_graph_published":false});
    node(&exact(&v)?, job, p)?;
    Ok(v)
}

fn read_chain(
    latest: &Value,
    job: &str,
    p: &BoundPlan,
    mut read: impl FnMut(&Value) -> SupervisorResult<String>,
) -> SupervisorResult<BTreeMap<String, Value>> {
    let mut current = latest.clone();
    let mut seen = BTreeSet::new();
    let mut found = BTreeMap::new();
    let mut ordered = Vec::new();
    let mut bytes = 0u64;
    let mut origin = None;
    let mut expected = None;
    loop {
        let (digest, size) = record_pin(&current)?;
        if !seen.insert(digest.clone()) || seen.len() > 4096 {
            return Err(invalid(
                "output checkpoint index cycle or excessive node count",
            ));
        }
        bytes = bytes
            .checked_add(size)
            .filter(|n| *n <= MAX_INDEX_CHAIN)
            .ok_or_else(|| invalid("output checkpoint index exceeds aggregate metadata bound"))?;
        let raw = read(&current)?;
        if raw.len() as u64 != size || hash(&raw) != digest {
            return Err(invalid(
                "output checkpoint node bytes differ from exact locator",
            ));
        }
        let v = node(&raw, job, p)?;
        let n = bounded_number(&v["ordinal"], 4096)?;
        let pins = (
            v["origin_plan_sha256"].clone(),
            v["origin_context_sha256"].clone(),
        );
        if let Some((old, _, _)) = &origin {
            if old != &pins || Some(n) != expected || v["complete"] != false {
                return Err(invalid(
                    "output checkpoint chain changed origin/ordinal/completion",
                ));
            }
        } else {
            origin = Some((pins, n, v["complete"] == true));
        }
        let id = hash_text(&v["chunk"]["chunk_identity"])?;
        if found
            .insert(id.into(), v["chunk"]["receipt"].clone())
            .is_some()
        {
            return Err(invalid("output checkpoint repeats local chunk key"));
        }
        ordered.push(v["chunk"]["chunk_identity"].clone());
        if v["previous"].is_null() {
            break;
        }
        expected = Some(n - 1);
        current = v["previous"].clone();
    }
    let ((origin_plan, context), n, complete) = origin.unwrap();
    if n as usize != ordered.len() {
        return Err(invalid("output checkpoint chain omits a completed prefix"));
    }
    if origin_plan == p.sha256 {
        let chunks = p.value["chunks"].as_array().unwrap();
        if context != p.value["context_sha256"]
            || ordered.len() > chunks.len()
            || (complete && ordered.len() != chunks.len())
            || ordered
                .iter()
                .rev()
                .zip(chunks)
                .any(|(id, c)| id != &c["identity"])
        {
            return Err(invalid(
                "output checkpoint current prefix is incomplete or reordered",
            ));
        }
    }
    Ok(found)
}

pub(crate) struct OutputPhysicalChunk {
    pub(crate) receipt: LocatedRenderFile,
    pub(crate) file: LocatedRenderFile,
    reference: Value,
}

fn receipt(raw: &str, job: &str, c: &BoundChunk) -> SupervisorResult<Value> {
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
            "chunk",
            "origin_plan_sha256",
            "origin_context_sha256",
            "file",
            "semantic_qc_admitted",
            "active_graph_published",
        ],
    )?;
    if v["schema_version"].as_u64() != Some(2)
        || v["kind"] != "review_native_output_chunk_receipt"
        || v["job_id"] != job
        || v["source_sha256"] != c.source
        || v["processing_recipe_sha256"] != c.recipe
        || v["time_base"] != c.clock
        || v["chunk"] != c.chunk
        || v["file"]["format"] != "mp4"
        || v["semantic_qc_admitted"] != false
        || v["active_graph_published"] != false
        || exact(&v)? != raw
    {
        return Err(invalid(
            "output chunk receipt changed closed native bindings or authority",
        ));
    }
    hash_text(&v["origin_plan_sha256"])?;
    hash_text(&v["origin_context_sha256"])?;
    Ok(v)
}

fn reopen(
    output: &NativeOutputPin,
    job: &str,
    c: &BoundChunk,
    descriptor: &Value,
    d: Instant,
) -> SupervisorResult<OutputPhysicalChunk> {
    record_pin(descriptor)?;
    let store = NativeMixStore::open_existing(output, job, review_render::storage_budget())?;
    let object = store.reopen_file(descriptor, d)?;
    let v = receipt(&store.read_json_record(&object, d)?, job, c)?;
    let file = store.reopen_file(&v["file"], d)?;
    Ok(OutputPhysicalChunk {
        receipt: object,
        file,
        reference: descriptor.clone(),
    })
}

fn retain(
    output: &NativeOutputPin,
    job: &str,
    c: &BoundChunk,
    root: &Path,
    path: &Path,
    pin: &RetainedFilePin,
    d: Instant,
) -> SupervisorResult<OutputPhysicalChunk> {
    if pin.format != RetainedFileFormat::Mp4 {
        return Err(invalid("output video chunk requires MP4 physical role"));
    }
    let descriptor = {
        let store = NativeMixStore::new(output, job, review_render::storage_budget())?;
        let file = store.accept_file(root, path, pin, d)?;
        let v = json!({"schema_version":2,"kind":"review_native_output_chunk_receipt","job_id":job,"source_sha256":c.source,
            "processing_recipe_sha256":c.recipe,"time_base":c.clock,"chunk":c.chunk,"origin_plan_sha256":c.plan_sha256,
            "origin_context_sha256":c.context_sha256,"file":file.descriptor(),"semantic_qc_admitted":false,"active_graph_published":false});
        let raw = exact(&v)?;
        receipt(&raw, job, c)?;
        let object = store.accept_json_record(&raw, d)?;
        store.verify_file(&file, d)?;
        object.descriptor()
    }; // The only output-wide writer is dropped before independent reopen.
    reopen(output, job, c, &descriptor, d)
}

/// Only the supervisor's admitted processing executor implements this interface.
/// It must provide contained bounded encoding and current/source/runtime fences.
/// The native output journal persists progress before the optional observation.
pub(crate) trait OutputChunkExecutor {
    fn fence(&mut self, deadline: Instant) -> SupervisorResult<()>;
    fn encode(&mut self, request: &Value, deadline: Instant) -> SupervisorResult<String>;
    /// Optional observation after native output journal publication. Persistence
    /// does not depend on this observer or an original job SQLite stage.
    fn checkpoint(&mut self, _index: &Value, _deadline: Instant) -> SupervisorResult<()> {
        Ok(())
    }
}
pub(crate) struct OutputScheduleInputs<'a> {
    pub(crate) output: &'a NativeOutputPin,
    pub(crate) job: &'a str,
    pub(crate) plan: &'a BoundPlan,
    pub(crate) cache: Option<&'a Value>,
    pub(crate) candidates: &'a Path,
    pub(crate) runtime_environment_sha256: &'a str,
    pub(crate) deadline: Instant,
}
pub(crate) struct OutputScheduledChunks {
    pub(crate) index: Value,
    pub(crate) files: Vec<OutputPhysicalChunk>,
    pub(crate) encoded: usize,
    pub(crate) reused: usize,
}
struct OutputBackend<'a, 'b, E> {
    input: OutputScheduleInputs<'a>,
    executor: &'b mut E,
    progress: BTreeMap<usize, Value>,
}
impl<E: OutputChunkExecutor> ScheduleBackend for OutputBackend<'_, '_, E> {
    type Kept = OutputPhysicalChunk;
    type Index = Value;
    fn fence(&mut self, d: Instant) -> SupervisorResult<()> {
        self.executor.fence(d)
    }
    fn reopen(&mut self, c: &BoundChunk, v: &Value, d: Instant) -> SupervisorResult<Self::Kept> {
        reopen(self.input.output, self.input.job, c, v, d)
    }
    fn encode(
        &mut self,
        p: &BoundPlan,
        c: &BoundChunk,
        d: Instant,
    ) -> SupervisorResult<Self::Kept> {
        let request = json!({"schema_version":1,"kind":"review_native_chunk_work","plan_json":exact(&p.value)?,"plan_sha256":p.sha256,
            "context_sha256":p.value["context_sha256"],"chunk_identity":c.chunk["identity"],"runtime_environment_sha256":self.input.runtime_environment_sha256});
        let raw = self.executor.encode(&request, d)?;
        self.executor.fence(d)?;
        let (path, pin) = encoded_pin(&raw, c, self.input.runtime_environment_sha256)?;
        retain(
            self.input.output,
            self.input.job,
            c,
            self.input.candidates,
            &path,
            &pin,
            d,
        )
    }
    fn receipt(k: &Self::Kept) -> Value {
        // Preserve archived non-authoritative hints as exact metadata bytes.
        // Native reopen still derives actual IO solely from the locator.
        k.reference.clone()
    }
    fn index_packet(
        job: &str,
        p: &BoundPlan,
        e: &Value,
        n: usize,
        previous: Option<&Value>,
        complete: bool,
    ) -> SupervisorResult<Value> {
        packet(job, p, e, n, previous, complete)
    }
    fn checkpoint(&mut self, p: &Value, d: Instant) -> SupervisorResult<Value> {
        let ordinal = bounded_number(&p["ordinal"], 4096)? as usize;
        let raw = exact(p)?;
        self.executor.fence(d)?;
        let descriptor = if let Some(saved) = self.progress.get(&ordinal) {
            if record_pin(saved)?.0 != hash(&raw) {
                return Err(invalid(
                    "recovered checkpoint node changed exact current prefix; never overwrite",
                ));
            }
            let store = NativeMixStore::open_existing(
                self.input.output,
                self.input.job,
                review_render::storage_budget(),
            )?;
            let object = store.reopen_file(saved, d)?;
            if store.read_json_record(&object, d)? != raw {
                return Err(invalid("recovered checkpoint node bytes changed"));
            }
            saved.clone()
        } else {
            let store = NativeMixStore::new(
                self.input.output,
                self.input.job,
                review_render::storage_budget(),
            )?;
            let object = store.accept_json_record(&raw, d)?;
            node(
                &store.read_json_record(&object, d)?,
                self.input.job,
                self.input.plan,
            )?;
            let descriptor = object.descriptor();
            let marker = checkpoint_packet(self.input.job, self.input.plan, ordinal, &descriptor)?;
            store.publish_chunk_checkpoint(
                &self.input.plan.sha256,
                ordinal as u16,
                &exact(&marker)?,
                d,
            )?;
            self.progress.insert(ordinal, descriptor.clone());
            descriptor
        }; // No writer lease crosses native pointer/state callback boundaries.
        self.executor.fence(d)?;
        self.executor.checkpoint(&descriptor, d)?;
        let store = NativeMixStore::open_existing(
            self.input.output,
            self.input.job,
            review_render::storage_budget(),
        )?;
        let object = store.reopen_file(&descriptor, d)?;
        node(
            &store.read_json_record(&object, d)?,
            self.input.job,
            self.input.plan,
        )?;
        Ok(descriptor)
    }
}

fn checkpoint_packet(
    job: &str,
    p: &BoundPlan,
    ordinal: usize,
    index: &Value,
) -> SupervisorResult<Value> {
    let v = json!({"schema_version":2,"kind":"review_native_output_chunk_checkpoint","job_id":job,
        "plan_sha256":p.sha256,"context_sha256":p.value["context_sha256"],"ordinal":ordinal,"index":index});
    admit_checkpoint(&exact(&v)?, job, p, ordinal)?;
    Ok(v)
}

fn admit_checkpoint(
    raw: &str,
    job: &str,
    p: &BoundPlan,
    ordinal: usize,
) -> SupervisorResult<Value> {
    validate_job_id(job)?;
    let v = decode(raw, MAX_RECEIPT as usize)?;
    closed(
        &v,
        &[
            "schema_version",
            "kind",
            "job_id",
            "plan_sha256",
            "context_sha256",
            "ordinal",
            "index",
        ],
    )?;
    if v["schema_version"].as_u64() != Some(2)
        || v["kind"] != "review_native_output_chunk_checkpoint"
        || v["job_id"] != job
        || v["plan_sha256"] != p.sha256
        || v["context_sha256"] != p.value["context_sha256"]
        || bounded_number(&v["ordinal"], 4096)? as usize != ordinal
        || exact(&v)? != raw
    {
        return Err(invalid("durable output checkpoint marker changed native current job/plan/context/ordinal or canonical bytes"));
    }
    record_pin(&v["index"])?;
    Ok(v)
}

fn recover_progress<E: OutputChunkExecutor>(
    input: &OutputScheduleInputs<'_>,
    executor: &mut E,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<BTreeMap<usize, Value>> {
    let records = {
        let store = NativeMixStore::new(input.output, input.job, review_render::storage_budget())?;
        store.read_chunk_checkpoints(&input.plan.sha256, input.deadline, cancelled)?
    };
    let mut found = BTreeMap::new();
    let mut previous = None;
    for (ordinal, raw) in records {
        if cancelled() || Instant::now() >= input.deadline {
            return Err(invalid("durable checkpoint recovery cancelled or expired"));
        }
        executor.fence(input.deadline)?;
        let marker = admit_checkpoint(&raw, input.job, input.plan, usize::from(ordinal))?;
        let index = &marker["index"];
        let value = {
            let store = NativeMixStore::open_existing(
                input.output,
                input.job,
                review_render::storage_budget(),
            )?;
            let object = store.reopen_file(index, input.deadline)?;
            node(
                &store.read_json_record(&object, input.deadline)?,
                input.job,
                input.plan,
            )?
        };
        admit_progress_node(&value, input.plan, usize::from(ordinal), previous.as_ref())?;
        found.insert(usize::from(ordinal), index.clone());
        previous = Some(index.clone());
    }
    executor.fence(input.deadline)?;
    Ok(found)
}

fn admit_progress_node(
    value: &Value,
    plan: &BoundPlan,
    ordinal: usize,
    previous: Option<&Value>,
) -> SupervisorResult<()> {
    let chunks = plan.value["chunks"].as_array().unwrap();
    if !(1..=chunks.len()).contains(&ordinal)
        || value["ordinal"].as_u64() != Some(ordinal as u64)
        || value["origin_plan_sha256"] != plan.sha256
        || value["origin_context_sha256"] != plan.value["context_sha256"]
        || value["chunk"]["chunk_identity"] != chunks[ordinal - 1]["identity"]
        || &value["previous"] != previous.unwrap_or(&Value::Null)
        || value["complete"] != (ordinal == chunks.len())
    {
        return Err(invalid("durable output checkpoint marker and node do not describe the exact completed current prefix"));
    }
    Ok(())
}

pub(crate) fn schedule_output<E: OutputChunkExecutor>(
    input: OutputScheduleInputs<'_>,
    executor: &mut E,
    cancelled: &mut impl FnMut() -> bool,
) -> SupervisorResult<OutputScheduledChunks> {
    validate_job_id(input.job)?;
    hash_text(&json!(input.runtime_environment_sha256))?;
    if cancelled() || Instant::now() >= input.deadline {
        return Err(invalid(
            "output chunk schedule cancelled or expired before admission",
        ));
    }
    executor.fence(input.deadline)?;
    let progress = recover_progress(&input, executor, cancelled)?;
    let mut cache = if let Some(latest) = input.cache {
        read_chain(latest, input.job, input.plan, |descriptor| {
            if cancelled() || Instant::now() >= input.deadline {
                return Err(invalid("output chunk index read cancelled or expired"));
            }
            executor.fence(input.deadline)?;
            let store = NativeMixStore::open_existing(
                input.output,
                input.job,
                review_render::storage_budget(),
            )?;
            let object = store.reopen_file(descriptor, input.deadline)?;
            store.read_json_record(&object, input.deadline)
        })?
    } else {
        BTreeMap::new()
    };
    if let Some((_, latest)) = progress.last_key_value() {
        let current = read_chain(latest, input.job, input.plan, |descriptor| {
            if cancelled() || Instant::now() >= input.deadline {
                return Err(invalid("recovered output chain cancelled or expired"));
            }
            executor.fence(input.deadline)?;
            let store = NativeMixStore::open_existing(
                input.output,
                input.job,
                review_render::storage_budget(),
            )?;
            let object = store.reopen_file(descriptor, input.deadline)?;
            store.read_json_record(&object, input.deadline)
        })?;
        cache.extend(current); // Current durable prefix wins; archived local cache fills the rest.
    }
    let job = input.job;
    let plan = input.plan;
    let deadline = input.deadline;
    let mut backend = OutputBackend {
        input,
        executor,
        progress,
    };
    let (index, files, encoded, reused) =
        drive(job, plan, cache, deadline, &mut backend, cancelled)?;
    Ok(OutputScheduledChunks {
        index,
        files,
        encoded,
        reused,
    })
}

#[cfg(test)]
mod tests {
    use super::super::super::review_assets::AcceptedJsonDocument;
    use super::*;
    use std::time::Duration;
    fn plan_value() -> Value {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../tests/review/native_longform_plan_v1.json"
        ))
        .unwrap();
        decode(
            fixture["cases"][0]["plan_json"].as_str().unwrap(),
            4 * 1024 * 1024,
        )
        .unwrap()
    }
    fn read(v: &Value) -> BoundPlan {
        let raw = exact(v).unwrap();
        BoundPlan::read(
            &raw,
            &hash(&raw),
            v["context_sha256"].as_str().unwrap(),
            v["source_sha256"].as_str().unwrap(),
            v["processing_recipe_sha256"].as_str().unwrap(),
        )
        .unwrap()
    }
    fn locator(raw: &str) -> Value {
        let digest = hash(raw);
        json!({"path_hint":"untrusted preview only","format":"json","locator":{"schema_version":1,
            "volume_id":"windows-volume-guid:01234567-89ab-cdef-0123-456789abcdef","root_kind":"output",
            "relative_path":format!(".dubflow-review-media-v1/{}/{}.json","b".repeat(64),&digest[7..]),
            "content_hash":digest,"size_bytes":raw.len().to_string()}})
    }
    #[derive(Default)]
    struct MockOutput {
        nodes: BTreeMap<String, String>,
        saved: Vec<Value>,
        encoded: usize,
        reused: usize,
        fail_encode: Option<usize>,
    }
    impl ScheduleBackend for MockOutput {
        type Index = Value;
        type Kept = Value;
        fn fence(&mut self, _: Instant) -> SupervisorResult<()> {
            Ok(())
        }
        fn reopen(&mut self, _: &BoundChunk, v: &Value, _: Instant) -> SupervisorResult<Value> {
            record_pin(v)?;
            self.reused += 1;
            Ok(v.clone())
        }
        fn encode(&mut self, _: &BoundPlan, c: &BoundChunk, _: Instant) -> SupervisorResult<Value> {
            self.encoded += 1;
            if self.fail_encode == Some(self.encoded) {
                return Err(invalid("mock encode fails after prior completed node"));
            }
            Ok(locator(&format!(
                "mock physical receipt {}",
                c.chunk["identity"]
            )))
        }
        fn receipt(k: &Value) -> Value {
            k.clone()
        }
        fn index_packet(
            j: &str,
            p: &BoundPlan,
            e: &Value,
            n: usize,
            previous: Option<&Value>,
            complete: bool,
        ) -> SupervisorResult<Value> {
            packet(j, p, e, n, previous, complete)
        }
        fn checkpoint(&mut self, v: &Value, _: Instant) -> SupervisorResult<Value> {
            let raw = exact(v)?;
            let l = locator(&raw);
            self.nodes.insert(hash(&raw), raw);
            self.saved.push(l.clone());
            Ok(l)
        }
    }
    fn cache(
        mock: &MockOutput,
        index: &Value,
        p: &BoundPlan,
    ) -> SupervisorResult<BTreeMap<String, Value>> {
        read_chain(index, "output-job", p, |l| {
            mock.nodes
                .get(&record_pin(l)?.0)
                .cloned()
                .ok_or_else(|| invalid("missing output metadata"))
        })
    }
    #[test]
    fn durable_marker_refuses_foreign_current_plan_context_ordinal_and_noncanonical_envelopes() {
        let p = read(&plan_value());
        let index = locator("immutable index fixture");
        let marker = checkpoint_packet("output-job", &p, 1, &index).unwrap();
        let raw = exact(&marker).unwrap();
        assert_eq!(admit_checkpoint(&raw, "output-job", &p, 1).unwrap(), marker);
        for key in [
            "schema_version",
            "kind",
            "job_id",
            "plan_sha256",
            "context_sha256",
            "ordinal",
            "foreign",
        ] {
            let mut bad = marker.clone();
            bad[key] = json!("foreign");
            assert!(
                admit_checkpoint(&exact(&bad).unwrap(), "output-job", &p, 1).is_err(),
                "{key}"
            );
        }
        for ordinal in [0, 2, 4097] {
            let mut bad = marker.clone();
            bad["ordinal"] = json!(ordinal);
            assert!(admit_checkpoint(&exact(&bad).unwrap(), "output-job", &p, 1).is_err());
        }
        for mode in 0..3 {
            let mut bad = marker.clone();
            match mode {
                0 => bad["index"]["locator"]["root_kind"] = json!("control"),
                1 => bad["index"]["format"] = json!("mp4"),
                _ => bad["index"]["locator"]["size_bytes"] = json!("65537"),
            }
            assert!(admit_checkpoint(&exact(&bad).unwrap(), "output-job", &p, 1).is_err());
        }
        assert!(admit_checkpoint(&(raw.clone() + "\n"), "output-job", &p, 1).is_err());
        assert!(admit_checkpoint(&raw, "other-job", &p, 1).is_err());
        let mut next = plan_value();
        next["context_sha256"] = json!(hash("new cumulative current generation"));
        assert!(admit_checkpoint(&raw, "output-job", &read(&next), 1).is_err());
    }
    #[test]
    fn recovered_current_prefix_refuses_marker_forks_reordered_local_keys_and_early_completion() {
        let p = read(&plan_value());
        let first = locator("first exact marker index");
        let entries = p.value["chunks"].as_array().unwrap();
        assert_eq!(entries.len(), 3);
        let entry =
            json!({"chunk_identity":entries[1]["identity"],"receipt":locator("second receipt")});
        let value = packet("output-job", &p, &entry, 2, Some(&first), false).unwrap();
        admit_progress_node(&value, &p, 2, Some(&first)).unwrap();
        for mode in 0..6 {
            let mut bad = value.clone();
            match mode {
                0 => bad["previous"] = locator("forked previous index"),
                1 => bad["origin_plan_sha256"] = json!(hash("other origin plan")),
                2 => bad["origin_context_sha256"] = json!(hash("other origin context")),
                3 => bad["chunk"]["chunk_identity"] = entries[0]["identity"].clone(),
                4 => bad["ordinal"] = json!(1),
                _ => bad["complete"] = json!(true),
            }
            assert!(
                admit_progress_node(&bad, &p, 2, Some(&first)).is_err(),
                "{mode}"
            );
        }
        assert!(admit_progress_node(&value, &p, 0, None).is_err());
        assert!(admit_progress_node(&value, &p, 4, Some(&first)).is_err());
        let mut final_entry = entry.clone();
        final_entry["chunk_identity"] = entries[2]["identity"].clone();
        let unfinished = packet("output-job", &p, &final_entry, 3, Some(&first), false).unwrap();
        assert!(admit_progress_node(&unfinished, &p, 3, Some(&first)).is_err());
    }
    #[test]
    fn output_index_uses_same_loop_and_partial_resume_reuses_exact_local_keys_after_other_context_change(
    ) {
        let mut v = plan_value();
        let p = read(&v);
        let mut first = MockOutput {
            fail_encode: Some(2),
            ..Default::default()
        };
        assert!(drive(
            "output-job",
            &p,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(30),
            &mut first,
            &mut || false
        )
        .is_err());
        assert_eq!(first.saved.len(), 1);
        let checkpoint = first.saved[0].clone();
        let old = first.nodes.clone();
        v["context_sha256"] = json!(hash("new current cumulative token"));
        let next = read(&v);
        let lookup = cache(&first, &checkpoint, &next).unwrap();
        let mut second = MockOutput::default();
        let (last, files, encoded, reused) = drive(
            "output-job",
            &next,
            lookup,
            Instant::now() + Duration::from_secs(30),
            &mut second,
            &mut || false,
        )
        .unwrap();
        assert_eq!((files.len(), encoded, reused), (3, 2, 1));
        assert_eq!(first.nodes, old);
        assert_eq!(cache(&second, &last, &next).unwrap().len(), 3);
        let root = node(
            second
                .nodes
                .get(&record_pin(&second.saved[0]).unwrap().0)
                .unwrap(),
            "output-job",
            &next,
        )
        .unwrap();
        assert!(root["previous"].is_null());
        assert_eq!(root["schema_version"], 2);
        assert!(second
            .saved
            .iter()
            .all(|l| l["locator"]["root_kind"] == "output" && l["format"] == "json"));
    }
    #[test]
    fn output_index_refuses_DATA_v1_wrong_origin_clock_order_foreign_role_and_missing_or_changed_bytes(
    ) {
        let p = read(&plan_value());
        let mut mock = MockOutput::default();
        let (last, _, _, _) = drive(
            "output-job",
            &p,
            BTreeMap::new(),
            Instant::now() + Duration::from_secs(30),
            &mut mock,
            &mut || false,
        )
        .unwrap();
        let raw = mock.nodes.get(&record_pin(&last).unwrap().0).unwrap();
        let original = node(raw, "output-job", &p).unwrap();
        for key in [
            "schema_version",
            "job_id",
            "source_sha256",
            "processing_recipe_sha256",
            "time_base",
            "origin_plan_sha256",
            "complete",
            "foreign",
        ] {
            let mut bad = original.clone();
            bad[key] = json!("foreign");
            assert!(
                node(&exact(&bad).unwrap(), "output-job", &p).is_err(),
                "{key}"
            );
        }
        for mode in 0..3 {
            let mut bad = original.clone();
            match mode {
                0 => bad["ordinal"] = json!(5),
                1 => bad["chunk"]["chunk_identity"] = p.value["chunks"][0]["identity"].clone(),
                _ => bad["origin_context_sha256"] = json!(hash("foreign origin")),
            }
            let changed = exact(&bad).unwrap();
            let descriptor = locator(&changed);
            let mut nodes = mock.nodes.clone();
            nodes.insert(hash(&changed), changed);
            assert!(read_chain(&descriptor, "output-job", &p, |l| nodes
                .get(&record_pin(l)?.0)
                .cloned()
                .ok_or_else(|| invalid("missing")))
            .is_err());
        }
        let legacy = AcceptedJsonDocument {
            path: std::env::temp_dir().join("legacy.json"),
            sha256: hash("legacy"),
            size_bytes: 6,
        };
        assert!(record_pin(&super::super::super::review_correction::descriptor(&legacy)).is_err());
        for mode in 0..3 {
            let mut bad = last.clone();
            match mode {
                0 => bad["format"] = json!("mp4"),
                1 => bad["locator"]["root_kind"] = json!("control"),
                _ => bad["locator"]["size_bytes"] = json!("65537"),
            };
            assert!(record_pin(&bad).is_err());
        }
        assert!(read_chain(&last, "output-job", &p, |_| Ok("{}".into())).is_err());
        let mut nodes = mock.nodes.clone();
        nodes.remove(&record_pin(&mock.saved[0]).unwrap().0);
        assert!(read_chain(&last, "output-job", &p, |l| nodes
            .get(&record_pin(l)?.0)
            .cloned()
            .ok_or_else(|| invalid("missing")))
        .is_err());
    }
    #[cfg(windows)]
    #[test]
    fn actual_output_schedule_drops_writer_before_pointer_callback_and_reopens_all_metadata_without_DATA(
    ) {
        use std::fs;
        let parent = std::env::temp_dir();
        let root = parent.join(format!(
            "dubflow-output-schedule-{}-{}",
            std::process::id(),
            super::super::super::ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        assert!(!root.exists());
        fs::create_dir(&root).unwrap();
        let candidates = root.join("owned-candidates");
        fs::create_dir(&candidates).unwrap();
        let output = NativeOutputPin::read(&root).unwrap();
        let p = read(&plan_value());
        let environment = hash("mock-only runtime closure");
        struct Executor<'a> {
            output: &'a NativeOutputPin,
            candidates: &'a Path,
            plan: &'a BoundPlan,
            environment: &'a str,
            encoded: usize,
            checkpoints: usize,
        }
        impl OutputChunkExecutor for Executor<'_> {
            fn fence(&mut self, _: Instant) -> SupervisorResult<()> {
                Ok(())
            }
            fn encode(&mut self, request: &Value, _: Instant) -> SupervisorResult<String> {
                self.encoded += 1;
                let c = self
                    .plan
                    .chunk(request["chunk_identity"].as_str().unwrap())?;
                let bytes = format!(
                    "physical storage fixture {}, not valid encoded MP4",
                    c.chunk["identity"]
                );
                let path = self.candidates.join(format!("{}.mp4", self.encoded));
                fs::write(&path, bytes.as_bytes())?;
                exact(
                    &json!({"schema_version":1,"kind":"review_private_encoded_chunk_candidate","context_sha256":c.context_sha256,
                    "plan_sha256":c.plan_sha256,"source_sha256":c.source,"processing_recipe_sha256":c.recipe,"runtime_environment_sha256":self.environment,
                    "chunk_identity":c.chunk["identity"],"caption_window_sha256":c.chunk["visual_dependencies_sha256"],"source_frame_clock_sha256":c.chunk["frame_clock_sha256"],
                    "frame_count":c.chunk["frame_count"],"file":{"path":path,"sha256":hash(&bytes),"size_bytes":bytes.len().to_string()},
                    "full_video_decode":true,"semantic_qc_admitted":false,"active_graph_published":false}),
                )
            }
            fn checkpoint(&mut self, index: &Value, d: Instant) -> SupervisorResult<()> {
                // Taking the real existing output-wide lock proves no storage
                // writer crosses this callback. This test callback only
                // observes pointers; it is not a real crash-safe state writer.
                let store = NativeMixStore::open_existing(
                    self.output,
                    "output-job",
                    review_render::storage_budget(),
                )?;
                let object = store.reopen_file(index, d)?;
                node(
                    &store.read_json_record(&object, d)?,
                    "output-job",
                    self.plan,
                )?;
                self.checkpoints += 1;
                Ok(())
            }
        }
        let mut exec = Executor {
            output: &output,
            candidates: &candidates,
            plan: &p,
            environment: &environment,
            encoded: 0,
            checkpoints: 0,
        };
        let until = Instant::now() + Duration::from_secs(60);
        let first = schedule_output(
            OutputScheduleInputs {
                output: &output,
                job: "output-job",
                plan: &p,
                cache: None,
                candidates: &candidates,
                runtime_environment_sha256: &environment,
                deadline: until,
            },
            &mut exec,
            &mut || false,
        )
        .unwrap();
        assert_eq!((first.encoded, first.reused, exec.checkpoints), (3, 0, 3));
        let pins = first
            .files
            .iter()
            .map(|k| {
                (
                    k.file.path().to_path_buf(),
                    fs::metadata(k.file.path()).unwrap().modified().unwrap(),
                    k.receipt.path().to_path_buf(),
                    fs::metadata(k.receipt.path()).unwrap().modified().unwrap(),
                )
            })
            .collect::<Vec<_>>();
        let second = schedule_output(
            OutputScheduleInputs {
                output: &output,
                job: "output-job",
                plan: &p,
                cache: None, // New native invocation discovers its durable output journal.
                candidates: &candidates,
                runtime_environment_sha256: &environment,
                deadline: until,
            },
            &mut exec,
            &mut || false,
        )
        .unwrap();
        assert_eq!((second.encoded, second.reused, exec.encoded), (0, 3, 3));
        assert_eq!(second.index, first.index);
        for (file, mtime, receipt, rmtime) in pins {
            assert_eq!(fs::metadata(file).unwrap().modified().unwrap(), mtime);
            assert_eq!(fs::metadata(receipt).unwrap().modified().unwrap(), rmtime);
        }
        assert!(!root.join("review-assets").exists());
        assert!(!root.join("control").exists());
        let mut wrong = first.index.clone();
        wrong["locator"]["volume_id"] =
            json!("windows-volume-guid:00000000-0000-0000-0000-000000000000");
        assert!(schedule_output(
            OutputScheduleInputs {
                output: &output,
                job: "output-job",
                plan: &p,
                cache: Some(&wrong),
                candidates: &candidates,
                runtime_environment_sha256: &environment,
                deadline: until
            },
            &mut exec,
            &mut || false
        )
        .is_err());
        assert_eq!(exec.encoded, 3);
        assert_eq!(root.parent(), Some(parent.as_path()));
        assert!(root
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("dubflow-output-schedule-"));
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
                    fs::set_permissions(p, x).unwrap();
                }
            }
        }
        unseal(&root);
        fs::remove_dir_all(root).unwrap();
    }
}
