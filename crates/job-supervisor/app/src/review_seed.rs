//! Explicit native initialization from a completed original export.
//! The owned adapter reads metadata only. Media is revalidated before reuse;
//! initialization does not authorize correction/undo or claim model admission.
use super::review_assets::{AcceptedAudioStore, AcceptedJsonDocument};
use super::review_document::{AdmittedSnapshot, closed, decode, identity};
use super::review_pin::{CommittedExport, archived_export, committed_export, plain};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{DurableStore, review_graph::ReviewGraphState};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

#[path = "review_chunk_bridge.rs"]
mod chunk_bridge;
pub(super) use chunk_bridge::{ChunkWorkerBinding, NativeChunkRequest, collect_chunk_worker};
#[path = "review_clock_collect.rs"]
mod clock_collect;
pub(crate) use clock_collect::collect_clock_worker;

const LIMIT: usize = 64 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(60);

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn number(value: &Value) -> SupervisorResult<u64> {
    if let Some(n) = value.as_u64().filter(|n| *n <= i64::MAX as u64) {
        return Ok(n);
    }
    super::review_document::tick(value, false)
}

fn metadata_raw(export: &CommittedExport, role: &str) -> SupervisorResult<String> {
    let descriptor = &export.manifest["artifacts"][role];
    closed(descriptor, &["path", "sha256", "size_bytes"])?;
    let path = PathBuf::from(
        descriptor["path"]
            .as_str()
            .ok_or_else(|| invalid("missing metadata path"))?,
    );
    let manifest = PathBuf::from(export.pin["manifest_path"].as_str().unwrap());
    let size = descriptor["size_bytes"]
        .as_u64()
        .filter(|n| (1..=LIMIT as u64).contains(n))
        .ok_or_else(|| invalid("original metadata size is outside its bound"))?;
    if !path.starts_with(manifest.parent().unwrap())
        || path.components().any(|c| matches!(c, Component::ParentDir))
    {
        return Err(invalid("original metadata escapes its committed output"));
    }
    let file = super::review_assets::regular(&path)?;
    if file.metadata()?.len() != size {
        return Err(invalid("original metadata size changed"));
    }
    let mut bytes = Vec::new();
    file.take(size + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size
        || descriptor["sha256"] != format!("sha256:{:x}", Sha256::digest(&bytes))
    {
        return Err(invalid("original metadata hash changed"));
    }
    let raw = String::from_utf8(bytes).map_err(|_| invalid("original metadata is not UTF-8"))?;
    decode(&raw, LIMIT)?;
    Ok(raw)
}

pub(super) struct AdmittedSeed {
    pub(super) snapshot: AdmittedSnapshot,
    pub(super) graph: String,
    pub(super) output_root: PathBuf,
    documents: HashMap<String, String>,
}

fn export_output_root(export: &CommittedExport) -> SupervisorResult<PathBuf> {
    // The pin came from the immutable start/committed artifact binding, never
    // from a document path inside a producer's nested private checkpoints.
    let manifest = PathBuf::from(
        export.pin["manifest_path"]
            .as_str()
            .ok_or_else(|| invalid("missing original manifest output binding"))?,
    );
    if !manifest.is_absolute()
        || manifest
            .components()
            .any(|c| matches!(c, Component::ParentDir))
        || manifest.file_name() != Some(std::ffi::OsStr::new("job_manifest.json"))
    {
        return Err(invalid("original manifest output binding is invalid"));
    }
    // Archived Review metadata must remain readable while the media volume is
    // offline. Candidate creation separately checks availability/plain storage.
    Ok(manifest.parent().unwrap().to_path_buf())
}

impl AdmittedSeed {
    pub(super) fn corrected_timeline(&self, after: &AdmittedSnapshot) -> SupervisorResult<String> {
        let mode = if decode(&self.graph, LIMIT)?["mode"] == "b2" {
            super::review_document::ReviewMode::Dubbed
        } else {
            super::review_document::ReviewMode::Subtitles
        };
        let timeline = self
            .documents
            .get("editable_timeline")
            .ok_or_else(|| invalid("archived original timeline is missing"))?;
        super::review_timeline::derive(timeline, &self.snapshot, after, mode)
    }

    fn read(raw: &str, export: &CommittedExport) -> SupervisorResult<Self> {
        let packet = decode(raw, LIMIT)?;
        let graph = decode(
            packet["graph_json"]
                .as_str()
                .ok_or_else(|| invalid("missing original graph"))?,
            LIMIT,
        )?;
        let roles = graph["documents"]
            .as_object()
            .ok_or_else(|| invalid("missing original documents"))?;
        if roles.len() > 6 {
            return Err(invalid("original metadata role count exceeds its bound"));
        }
        let mut documents = HashMap::new();
        let mut total = export.manifest_raw.len();
        for role in roles.keys() {
            let document = metadata_raw(export, role)?;
            total = total.saturating_add(document.len());
            if total > LIMIT {
                return Err(invalid(
                    "original archived metadata exceeds its combined byte budget",
                ));
            }
            documents.insert(role.clone(), document);
        }
        Self::read_documents(raw, export, documents)
    }

    fn read_documents(
        raw: &str,
        export: &CommittedExport,
        originals: HashMap<String, String>,
    ) -> SupervisorResult<Self> {
        let packet = decode(raw, LIMIT)?;
        closed(
            &packet,
            &[
                "schema_version",
                "kind",
                "job_id",
                "manifest_sha256",
                "snapshot_json",
                "graph_json",
            ],
        )?;
        if packet["schema_version"].as_u64() != Some(1)
            || packet["kind"] != "review_original_seed"
            || packet["job_id"] != export.pin["job_id"]
            || packet["manifest_sha256"] != export.pin["manifest_sha256"]
        {
            return Err(invalid(
                "original seed differs from native completed identity",
            ));
        }
        let snapshot_raw = packet["snapshot_json"]
            .as_str()
            .ok_or_else(|| invalid("missing original snapshot"))?;
        let graph_raw = packet["graph_json"]
            .as_str()
            .ok_or_else(|| invalid("missing original graph"))?;
        if snapshot_raw.len().saturating_add(graph_raw.len()) > LIMIT {
            return Err(invalid("original state byte budget exceeded"));
        }
        let snapshot =
            AdmittedSnapshot::read(snapshot_raw, export.pin["job_id"].as_str().unwrap(), 0)?;
        let graph = decode(graph_raw, LIMIT)?;
        closed(
            &graph,
            &[
                "schema_version",
                "kind",
                "job_id",
                "revision",
                "mode",
                "manifest_sha256",
                "documents",
                "time_base",
                "source_clock_mapping",
                "canonical_inputs",
                "tts_provenance",
                "media_policy",
                "producer_policy",
            ],
        )?;
        if graph["schema_version"].as_u64() != Some(1)
            || graph["kind"] != "review_original_graph"
            || graph["job_id"] != export.pin["job_id"]
            || graph["revision"] != "0"
            || graph["manifest_sha256"] != export.pin["manifest_sha256"]
            || graph["time_base"] != *snapshot.time_base()
            || graph["media_policy"] != "revalidate-before-reuse"
            || graph["producer_policy"] != "explicit-release-admission"
        {
            return Err(invalid("original graph version/identity/policy differs"));
        }
        let b2 = export.manifest["artifacts"].get("tts_document").is_some();
        if export.manifest["dubbing"]["enabled"].as_bool() != Some(b2)
            || graph["mode"] != if b2 { "b2" } else { "b1" }
        {
            return Err(invalid("original graph invents a dubbing mode"));
        }
        let roles: &[&str] = if b2 {
            &[
                "editable_timeline",
                "qc_report",
                "tts_document",
                "dubbing_placement",
                "dubbing_spoken_text",
                "mix_document",
            ]
        } else {
            &["editable_timeline", "qc_report"]
        };
        let documents = closed(&graph["documents"], roles)?;
        if originals.len() != roles.len() {
            return Err(invalid("original metadata role set differs"));
        }
        let mut total = export.manifest_raw.len();
        let mut loaded = HashMap::new();
        for role in roles {
            if documents[*role] != export.manifest["artifacts"][*role] {
                return Err(invalid("graph substitutes an original metadata descriptor"));
            }
            let raw = originals
                .get(*role)
                .ok_or_else(|| invalid("missing archived original metadata"))?;
            total = total.saturating_add(raw.len());
            if total > LIMIT
                || documents[*role]["size_bytes"].as_u64() != Some(raw.len() as u64)
                || documents[*role]["sha256"]
                    != format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
            {
                return Err(invalid(
                    "original archived metadata bytes differ or exceed budget",
                ));
            }
            loaded.insert(*role, decode(raw, LIMIT)?);
        }
        let timeline = &loaded["editable_timeline"];
        let qc = &loaded["qc_report"];
        if timeline["schema_version"].as_u64() != Some(1)
            || qc["schema_version"].as_u64() != Some(1)
            || qc["status"] != "passed"
            || graph["source_clock_mapping"]
                != *timeline.get("source_clock_mapping").unwrap_or(&Value::Null)
        {
            return Err(invalid("original timeline/QC/source clock differs"));
        }
        let base = snapshot.time_base();
        let num = number(&base["numerator"])?;
        let den = number(&base["denominator"])?;
        if number(&timeline["time_base"]["numerator"])? != num
            || number(&timeline["time_base"]["denominator"])? != den
        {
            return Err(invalid("original canonical time base differs"));
        }
        let cues = timeline["cues"]
            .as_array()
            .filter(|c| c.len() <= 10000)
            .ok_or_else(|| invalid("original cue count is outside its bound"))?;
        let mut by_id = HashMap::new();
        for cue in cues {
            let id = identity(&cue["cue_id"])?;
            if by_id.insert(id, cue).is_some() {
                return Err(invalid("duplicate original canonical cue"));
            }
        }
        let inputs = graph["canonical_inputs"]
            .as_array()
            .ok_or_else(|| invalid("canonical inputs require an array"))?;
        if !b2 && (!inputs.is_empty() || !graph["tts_provenance"].is_null()) {
            return Err(invalid("B1 cannot invent TTS authority"));
        }
        if b2 {
            let tts = &loaded["tts_document"];
            if graph["tts_provenance"] != tts["provenance"] || inputs.len() != cues.len() {
                return Err(invalid("original TTS provenance/input count differs"));
            }
            let sources = tts["source_segment_ids"]
                .as_array()
                .ok_or_else(|| invalid("original TTS source IDs are missing"))?;
            if sources.len() != inputs.len() {
                return Err(invalid("original source/input count differs"));
            }
            let mut ids = HashSet::new();
            for (index, input) in inputs.iter().enumerate() {
                let id = identity(&input["segment_id"])?;
                let cue = by_id
                    .get(id)
                    .ok_or_else(|| invalid("TTS input invents a source cue"))?;
                if !ids.insert(id)
                    || sources[index] != input["segment_id"]
                    || input["source_utterance_id"] != input["segment_id"]
                    || input["text"] != cue["translated_text"]
                    || input["confidence"] != cue["confidence"]
                    || number(&input["start"]["ticks"])? != number(&cue["start_ms"])?
                    || number(&input["end"]["ticks"])? != number(&cue["end_ms"])?
                    || input["start"]["time_base"] != json!({"numerator":"1","denominator":"1000"})
                    || input["end"]["time_base"] != input["start"]["time_base"]
                {
                    return Err(invalid(
                        "original TTS input substitutes source/content/timing",
                    ));
                }
            }
        }
        let snapshot_value = decode(snapshot.json(), LIMIT)?;
        if !snapshot_value["last_plan"].is_null() {
            return Err(invalid(
                "original snapshot cannot invent an earlier correction",
            ));
        }
        let mut previews = HashMap::new();
        if b2 {
            for artifact in loaded["tts_document"]["artifacts"]
                .as_array()
                .ok_or_else(|| invalid("missing original TTS artifacts"))?
            {
                let id = identity(&artifact["segment_id"])?;
                let path = artifact["path"]
                    .as_str()
                    .ok_or_else(|| invalid("missing original TTS path"))?;
                if previews.insert(id, PathBuf::from(path)).is_some() {
                    return Err(invalid("duplicate original TTS artifact"));
                }
            }
        }
        for finding in snapshot_value["findings"].as_array().unwrap() {
            let Some(id) = finding["cue_id"].as_str() else {
                continue;
            };
            let cue = by_id
                .get(id)
                .ok_or_else(|| invalid("snapshot invents a canonical cue"))?;
            let measured = !cue["confidence"].is_null();
            let confidence = finding["confidence"]
                .as_str()
                .map(|s| decode(s, LIMIT))
                .transpose()?;
            if measured != confidence.is_some()
                || (measured
                    && confidence.as_ref().and_then(Value::as_f64) != cue["confidence"].as_f64())
                || (measured && finding["confidence_source"] != "cue.confidence")
                || (!measured && !finding["confidence_source"].is_null())
                || finding["speaker"] != *cue.get("speaker_id").unwrap_or(&Value::Null)
            {
                return Err(invalid(
                    "original finding substitutes confidence or speaker evidence",
                ));
            }
            if let Some(path) = finding["output_preview"].as_str() {
                if previews.get(id) != Some(&PathBuf::from(path)) {
                    return Err(invalid("original finding substitutes a preview path"));
                }
            }
            let convert = |ms: &Value| -> SupervisorResult<String> {
                let product = u128::from(number(ms)?) * u128::from(den);
                let divisor = 1000 * u128::from(num);
                if product % divisor != 0 || product / divisor > i64::MAX as u128 {
                    return Err(invalid("original cue is not exactly representable"));
                }
                Ok((product / divisor).to_string())
            };
            if finding["range"]["start_ticks"] != convert(&cue["start_ms"])?
                || finding["range"]["end_ticks"] != convert(&cue["end_ms"])?
                || finding["source_text"].as_str() != cue["source_text"].as_str().map(str::trim)
                || finding["translated_text"].as_str()
                    != cue["translated_text"].as_str().map(str::trim)
                || (!b2 && !finding["voice"].is_null())
                || (b2 && finding["voice"] != graph["tts_provenance"]["voice_id"])
            {
                return Err(invalid(
                    "original finding substitutes cue mapping/content/voice",
                ));
            }
        }
        Ok(Self {
            snapshot,
            graph: graph_raw.into(),
            output_root: export_output_root(export)?,
            documents: originals,
        })
    }
}

fn object_descriptor(object: &AcceptedJsonDocument) -> Value {
    json!({"path":object.path,"sha256":object.sha256,"size_bytes":object.size_bytes})
}

fn bound_graph(
    data: &Path,
    export: &CommittedExport,
    seed: &AdmittedSeed,
) -> SupervisorResult<String> {
    let assets = AcceptedAudioStore::new(data, seed.snapshot.job())?;
    let mut documents = serde_json::Map::new();
    documents.insert(
        "job_manifest".into(),
        object_descriptor(&assets.accept_json(&export.manifest_raw)?),
    );
    for (role, raw) in &seed.documents {
        documents.insert(role.clone(), object_descriptor(&assets.accept_json(raw)?));
    }
    let graph = serde_json::to_string(
        &json!({"schema_version":1,"kind":"review_bound_original_graph",
        "source_graph_json":seed.graph,"accepted_documents":documents}),
    )?;
    if seed.snapshot.json().len().saturating_add(graph.len()) > LIMIT {
        return Err(invalid(
            "bound original state exceeds its combined byte budget",
        ));
    }
    Ok(graph)
}

fn accepted_descriptor(value: &Value) -> SupervisorResult<AcceptedJsonDocument> {
    closed(value, &["path", "sha256", "size_bytes"])?;
    Ok(AcceptedJsonDocument {
        path: PathBuf::from(
            value["path"]
                .as_str()
                .ok_or_else(|| invalid("missing accepted metadata path"))?,
        ),
        sha256: value["sha256"]
            .as_str()
            .ok_or_else(|| invalid("missing accepted metadata hash"))?
            .into(),
        size_bytes: value["size_bytes"]
            .as_u64()
            .ok_or_else(|| invalid("invalid accepted metadata size"))?,
    })
}

/// Read-only recovery uses sealed exact bytes and the durable original binding,
/// never mutable export paths or an adapter that could regenerate anything.
pub(super) fn read_original(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
) -> SupervisorResult<AdmittedSeed> {
    if state.token.revision != 0 || state.undo_depth != 0 || state.undo_target_revision.is_some() {
        return Err(invalid(
            "existing generation requires its active graph reader; never overwrite",
        ));
    }
    let bound = decode(&state.graph_json, LIMIT)?;
    closed(
        &bound,
        &[
            "schema_version",
            "kind",
            "source_graph_json",
            "accepted_documents",
        ],
    )?;
    if bound["schema_version"].as_u64() != Some(1) || bound["kind"] != "review_bound_original_graph"
    {
        return Err(invalid(
            "unknown original graph format requires explicit recovery",
        ));
    }
    let raw_graph = bound["source_graph_json"]
        .as_str()
        .ok_or_else(|| invalid("missing source graph bytes"))?;
    let graph = decode(raw_graph, LIMIT)?;
    let source_documents = graph["documents"]
        .as_object()
        .ok_or_else(|| invalid("missing source graph documents"))?;
    let documents = bound["accepted_documents"]
        .as_object()
        .ok_or_else(|| invalid("missing accepted original metadata"))?;
    if source_documents.len() > 6
        || documents.len() != source_documents.len() + 1
        || !documents.contains_key("job_manifest")
    {
        return Err(invalid("accepted original metadata role set differs"));
    }
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let manifest = assets.read_json(&accepted_descriptor(&documents["job_manifest"])?)?;
    let export = archived_export(store, &state.token.job_id, manifest)?;
    if export.artifact_id != state.original_artifact_id
        || export.pin["manifest_sha256"] != state.token.original_manifest_sha256
    {
        return Err(invalid(
            "archived manifest differs from durable Review binding",
        ));
    }
    let mut originals = HashMap::new();
    let mut total = export.manifest_raw.len();
    for role in source_documents.keys() {
        let descriptor = documents
            .get(role)
            .ok_or_else(|| invalid("accepted original metadata is missing"))?;
        let raw = assets.read_json(&accepted_descriptor(descriptor)?)?;
        total = total.saturating_add(raw.len());
        if total > LIMIT {
            return Err(invalid(
                "accepted original metadata exceeds its combined budget",
            ));
        }
        originals.insert(role.clone(), raw);
    }
    let packet = serde_json::to_string(&json!({"schema_version":1,"kind":"review_original_seed",
        "job_id":state.token.job_id,"manifest_sha256":state.token.original_manifest_sha256,
        "snapshot_json":state.snapshot_json,"graph_json":raw_graph}))?;
    AdmittedSeed::read_documents(&packet, &export, originals)
}

fn initialize(
    store: &DurableStore,
    data: &Path,
    export: &CommittedExport,
    seed: &AdmittedSeed,
) -> SupervisorResult<ReviewGraphState> {
    let job = seed.snapshot.job();
    // Never repair/reinitialize any existing generation, including a corrupt
    // optional schema or object. Read it before creating any private copies.
    if let Some(state) = store.review_graph_state(job)? {
        let old = read_original(store, data, &state)?;
        if state.original_artifact_id != export.artifact_id
            || state.token.original_manifest_sha256
                != export.pin["manifest_sha256"].as_str().unwrap()
            || old.snapshot.json() != seed.snapshot.json()
            || old.graph != seed.graph
        {
            return Err(invalid(
                "existing original generation differs; explicit recovery required",
            ));
        }
        return Ok(state);
    }
    let graph = bound_graph(data, export, seed)?;
    let current = committed_export(store, job)?;
    if current.pin != export.pin || current.artifact_id != export.artifact_id {
        return Err(invalid(
            "completed export changed before Review initialization",
        ));
    }
    store.initialize_review_schema()?;
    let state = store.create_review_graph(
        job,
        &export.artifact_id,
        export.pin["manifest_sha256"].as_str().unwrap(),
        seed.snapshot.json(),
        &graph,
        super::now_ms(),
    )?;
    read_original(store, data, &state)?;
    Ok(state)
}

struct AdapterProcess {
    child: Child,
    tree: Option<super::WorkerTree>,
}
impl Drop for AdapterProcess {
    fn drop(&mut self) {
        drop(self.tree.take());
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn retire_scratch_process(
    mut process: AdapterProcess,
    until: Instant,
) -> SupervisorResult<super::worker_tree::RetiredTree> {
    let mut tree = process
        .tree
        .take()
        .ok_or_else(|| invalid("scratch process has no owned Job"))?;
    tree.terminate_owned(until)?;
    loop {
        if Instant::now() >= until {
            return Err(invalid(
                "scratch parent retirement exhausted original deadline",
            ));
        }
        if process.child.try_wait()?.is_some() {
            break;
        }
        std::thread::sleep(
            Duration::from_millis(5).min(until.saturating_duration_since(Instant::now())),
        );
    }
    // Keep the Job alive, but release the collector's process reference before
    // querying ActiveProcesses. Parent status alone is never retirement proof.
    drop(process);
    Ok(tree.retire_until(until)?)
}

// The original metadata adapter never spawns descendants. Windows containment
// still precedes the byte gate; error/timeout/cap paths terminate and reap it.
pub(super) fn collect(
    command: impl Into<super::review_retention::OwnedReviewCommand>,
    timeout: Duration,
    limit: usize,
) -> SupervisorResult<String> {
    let (mut command, scratch, prepared_at) = command.into().into_parts();
    let deadline = prepared_at.unwrap_or_else(Instant::now) + timeout;
    if Instant::now() >= deadline {
        return Err(invalid(
            "scratch admission exhausted the original worker deadline",
        ));
    }
    let scratch = scratch.map(|intent| intent.admit(deadline)).transpose()?;
    if Instant::now() >= deadline {
        return Err(invalid("scratch admission exhausted worker deadline"));
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            if let Some(scratch) = scratch {
                let _ = scratch.retire_unstarted(deadline);
            }
            return Err(error.into());
        }
    };
    let mut process = AdapterProcess { child, tree: None };
    process.tree = Some(super::WorkerTree::attach(&process.child)?);
    let result = (|| {
        let stdout = process.child.stdout.take().unwrap();
        let stderr = process.child.stderr.take().unwrap();
        let (send, receive) = mpsc::channel();
        for (error, cap, stream) in [
            (false, limit, Box::new(stdout) as Box<dyn Read + Send>),
            (true, 4096, Box::new(stderr) as Box<dyn Read + Send>),
        ] {
            let send = send.clone();
            std::thread::spawn(move || {
                let mut bytes = Vec::new();
                let result = stream
                    .take(cap as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map(|_| bytes);
                let _ = send.send((error, cap, result));
            });
        }
        drop(send);
        let mut stdin = process.child.stdin.take().unwrap();
        stdin.write_all(&[1])?;
        drop(stdin);
        let mut out = None;
        let mut errors = None;
        while Instant::now() < deadline {
            match receive.recv_timeout(Duration::from_millis(20)) {
                Ok((error, cap, result)) => {
                    let bytes = result?;
                    if bytes.len() > cap {
                        return Err(invalid("original adapter output exceeded byte cap"));
                    }
                    if error {
                        errors = Some(bytes);
                    } else {
                        out = Some(bytes);
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout)
                | Err(mpsc::RecvTimeoutError::Disconnected) => {
                    std::thread::sleep(Duration::from_millis(10))
                }
            }
            if let Some(status) = process.child.try_wait()? {
                if let (Some(out), Some(errors)) = (&out, &errors) {
                    if !status.success() || !errors.is_empty() {
                        return Err(invalid(
                            "original adapter refused metadata; completed export remains unchanged",
                        ));
                    }
                    return String::from_utf8(out.clone())
                        .map_err(|_| invalid("original adapter response is not UTF-8"));
                }
            }
        }
        Err(invalid(
            "original adapter exceeded its bounded observation deadline",
        ))
    })();
    if let Some(scratch) = scratch {
        // Never close the Job and infer retirement from EOF/parent status.
        let until = deadline.min(Instant::now() + Duration::from_secs(2));
        let proof = retire_scratch_process(process, until)?;
        scratch.retire_stopped(proof, deadline)?;
    }
    result
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let mut root = None;
    let mut data = None;
    let mut job = None;
    let mut initialize_original = false;
    while let Some(flag) = args.next() {
        match flag.to_str() {
            Some("--root") if root.is_none() => {
                root = Some(PathBuf::from(
                    args.next().ok_or_else(|| invalid("missing root"))?,
                ))
            }
            Some("--data-root") if data.is_none() => {
                data = Some(PathBuf::from(
                    args.next().ok_or_else(|| invalid("missing data root"))?,
                ))
            }
            Some("--job-id") if job.is_none() => {
                job = Some(
                    args.next()
                        .ok_or_else(|| invalid("missing job"))?
                        .into_string()
                        .map_err(|_| invalid("job ID is not UTF-8"))?,
                )
            }
            Some("--initialize-original") if !initialize_original => initialize_original = true,
            _ => return Err(invalid("unknown or duplicate original Review argument")),
        }
    }
    let root = root.ok_or_else(|| invalid("review-original requires --root"))?;
    let data = data.ok_or_else(|| invalid("review-original requires --data-root"))?;
    let job = job.ok_or_else(|| invalid("review-original requires --job-id"))?;
    super::validate_job_id(&job)?;
    plain(&root)?;
    plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    plain(&db)?;
    let _guard = if initialize_original {
        Some(super::JobExecutionGuard::acquire(&db, &job)?)
    } else {
        None
    };
    let store = if initialize_original {
        DurableStore::open(&db)?
    } else {
        DurableStore::open_read_only(&db)?
    };
    if let Some(state) = store.review_graph_state(&job)? {
        read_original(&store, &data, &state)?;
        return emit_original(&state);
    }
    if !initialize_original {
        return Err(invalid(
            "Review is not initialized; explicit --initialize-original is required",
        ));
    }
    let python = super::first_existing(&[
        root.join("runtime/python.exe"),
        root.join("runtime/python"),
        root.join("runtime/bin/python"),
    ])
    .ok_or_else(|| invalid("owned original Review Python is unavailable"))?;
    let script = root.join("app/engine/dubflow/review/cli.py");
    plain(&python)?;
    plain(&script)?;
    let export = committed_export(&store, &job)?;
    let mut command = Command::new(python);
    command.args([
        OsString::from("-I"),
        OsString::from("-S"),
        OsString::from("-B"),
        script.into_os_string(),
        OsString::from("original-v1"),
        OsString::from("--native-gate"),
        OsString::from("--manifest"),
        OsString::from(export.pin["manifest_path"].as_str().unwrap()),
        OsString::from("--job-id"),
        OsString::from(&job),
        OsString::from("--manifest-sha256"),
        OsString::from(export.pin["manifest_sha256"].as_str().unwrap()),
    ]);
    let raw = collect(command, TIMEOUT, LIMIT)?;
    let seed = AdmittedSeed::read(&raw, &export)?;
    let state = initialize(&store, &data, &export, &seed)?;
    emit_original(&state)
}

fn emit_original(state: &ReviewGraphState) -> SupervisorResult<()> {
    super::emit_value(
        &mut std::io::stdout().lock(),
        json!({"schema_version":1,"kind":"review_original_state","job_id":state.token.job_id,
        "revision":state.token.revision.to_string(),"snapshot_sha256":state.token.snapshot_sha256,"graph_sha256":state.token.graph_sha256,
        "manifest_sha256":state.token.original_manifest_sha256,"snapshot":decode(&state.snapshot_json,LIMIT)?,"correction_ready":false}),
    )
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    use dubflow_job_state::JobStatus;
    use std::fs;
    use std::sync::atomic::Ordering;

    struct Fixture {
        root: PathBuf,
        db: PathBuf,
        manifest: PathBuf,
        store: DurableStore,
        export: CommittedExport,
        packet: Value,
        _cleanup: Cleanup,
    }
    struct Cleanup(PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            // These paths exist only under this test's newly created root.
            let objects = self
                .0
                .join("control/review-assets")
                .join(format!("{:x}", Sha256::digest(b"job-seed")));
            if let Ok(files) = fs::read_dir(objects) {
                for file in files.flatten() {
                    if let Ok(meta) = file.metadata() {
                        #[cfg(windows)]
                        {
                            let mut permissions = meta.permissions();
                            permissions.set_readonly(false);
                            let _ = fs::set_permissions(file.path(), permissions);
                        }
                        #[cfg(not(windows))]
                        {
                            let _ = meta;
                        }
                    }
                }
            }
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture() -> Fixture {
        fixture_with_metadata_subdir("")
    }

    /// Native active-reader tests share the original admission boundary. Test
    /// metadata/SQLite only; this fixture does not contain encoded video.
    pub(crate) fn with_admitted_original(f: impl FnOnce(&DurableStore, &Path, &ReviewGraphState)) {
        let fixture = fixture();
        let seed = AdmittedSeed::read(&fixture.packet.to_string(), &fixture.export).unwrap();
        let state = initialize(&fixture.store, &fixture.root, &fixture.export, &seed).unwrap();
        f(&fixture.store, &fixture.root, &state);
    }

    fn fixture_with_metadata_subdir(metadata_subdir: &str) -> Fixture {
        let root = std::env::temp_dir().join(format!(
            "dubflow-review-seed-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("control")).unwrap();
        let output = root.join("output");
        fs::create_dir(&output).unwrap();
        let metadata_root = output.join(metadata_subdir);
        fs::create_dir_all(&metadata_root).unwrap();
        let timeline = json!({"schema_version":1,"duration_ticks":10000,"time_base":{"numerator":1,"denominator":1000},
            "cues":[{"cue_id":"cue-a","start_ms":100,"end_ms":900,"source_text":"Hello","translated_text":"Xin chào","confidence":0.1}]});
        let mut descriptors = serde_json::Map::new();
        for (role, value) in [
            ("editable_timeline", timeline),
            (
                "qc_report",
                json!({"schema_version":1,"status":"passed","warnings":[]}),
            ),
        ] {
            // Producer formatting is deliberately retained exactly.
            let raw = serde_json::to_string_pretty(&value).unwrap() + "\n";
            let path = metadata_root.join(format!("{role}.json"));
            fs::write(&path, &raw).unwrap();
            descriptors.insert(role.into(),json!({"path":path,"sha256":format!("sha256:{:x}",Sha256::digest(raw.as_bytes())),"size_bytes":raw.len()}));
        }
        let manifest = output.join("job_manifest.json");
        fs::write(
            &manifest,
            serde_json::to_string_pretty(
                &json!({"schema_version":1,"kind":"dubflow_localization_job",
            "job_id":"job-seed","dubbing":{"enabled":false},"artifacts":descriptors}),
            )
            .unwrap(),
        )
        .unwrap();
        let db = root.join("control/jobs.sqlite3");
        let store = DurableStore::open(&db).unwrap();
        store
            .create_job_with_start_request(
                "job-seed",
                "file:///source.mp4",
                &json!({"schema_version":1,"output_dir":output}).to_string(),
                1,
            )
            .unwrap();
        store
            .create_stage(
                "job-seed",
                super::super::STAGE_ID,
                "production-local-file",
                3,
            )
            .unwrap();
        store.start_job("job-seed", 2).unwrap();
        store
            .start_stage("job-seed", super::super::STAGE_ID, 3)
            .unwrap();
        store
            .record_artifact_written(
                "job-seed-local-file-job-manifest-a1",
                "job-seed",
                super::super::STAGE_ID,
                &manifest,
                None,
                true,
                4,
            )
            .unwrap();
        store
            .commit_artifact("job-seed-local-file-job-manifest-a1", 5)
            .unwrap();
        store.complete_job("job-seed", 6).unwrap();
        store
            .create_job("other-job", "file:///other.mp4", 7)
            .unwrap();
        let export = committed_export(&store, "job-seed").unwrap();
        let snapshot = json!({"schema_version":2,"job_id":"job-seed","revision":0,"time_base":{"numerator":"1","denominator":"1000"},
            "last_plan":null,"findings":[{"finding_id":"finding-a","cue_id":"cue-a","reason":"SYNC_QC_WARNING",
            "range":{"start_ticks":"100","end_ticks":"900"},"source_text":"Hello","translated_text":"Xin chào",
            "speaker":null,"voice":null,"output_preview":null,"confidence":"0.1","confidence_source":"cue.confidence",
            "affected_stages":["render","qc"],"warning":"LOW_CONFIDENCE_CUE"}]});
        let graph = json!({"schema_version":1,"kind":"review_original_graph","job_id":"job-seed","revision":"0","mode":"b1",
            "manifest_sha256":export.pin["manifest_sha256"],"documents":descriptors,"time_base":snapshot["time_base"],
            "source_clock_mapping":null,"canonical_inputs":[],"tts_provenance":null,
            "media_policy":"revalidate-before-reuse","producer_policy":"explicit-release-admission"});
        let packet = json!({"schema_version":1,"kind":"review_original_seed","job_id":"job-seed",
            "manifest_sha256":export.pin["manifest_sha256"],"snapshot_json":snapshot.to_string(),"graph_json":graph.to_string()});
        Fixture {
            _cleanup: Cleanup(root.clone()),
            root,
            db,
            manifest,
            store,
            export,
            packet,
        }
    }

    #[test]
    fn nested_checkpoint_metadata_does_not_select_candidate_output_storage() {
        let f = fixture_with_metadata_subdir(".dubflow-work/b2-audio/producer/config");
        let original_manifest = fs::read(&f.manifest).unwrap();
        let seed = AdmittedSeed::read(&f.packet.to_string(), &f.export).unwrap();
        assert_eq!(seed.output_root, f.root.join("output"));
        let metadata = Path::new(
            f.export.manifest["artifacts"]["editable_timeline"]["path"]
                .as_str()
                .unwrap(),
        );
        assert_ne!(seed.output_root, metadata.parent().unwrap());
        assert_eq!(fs::read(&f.manifest).unwrap(), original_manifest);
        assert!(!seed.output_root.join(".dubflow-review-candidates").exists());
        assert!(f.store.review_graph_state("job-seed").unwrap().is_none());
    }

    #[test]
    fn output_placement_refuses_invalid_bindings_without_creating_storage() {
        let mut f = fixture();
        for path in [
            PathBuf::from("relative/job_manifest.json"),
            f.root.join("output/../control/job_manifest.json"),
            f.root.join("output/tts_document.json"),
        ] {
            f.export.pin["manifest_path"] = path.to_string_lossy().into_owned().into();
            assert!(export_output_root(&f.export).is_err(), "{}", path.display());
        }
        f.export.pin["manifest_path"] = f
            .root
            .join("unavailable/job_manifest.json")
            .to_string_lossy()
            .into_owned()
            .into();
        assert_eq!(
            export_output_root(&f.export).unwrap(),
            f.root.join("unavailable")
        );
        assert!(!f.root.join("unavailable").exists());
        assert!(!f.root.join("control/.dubflow-review-candidates").exists());
        assert!(f.store.review_graph_state("job-seed").unwrap().is_none());
    }

    #[test]
    fn original_state_retains_exact_metadata_and_reopens_without_exports_or_runtime() {
        let f = fixture();
        let seed = AdmittedSeed::read(&f.packet.to_string(), &f.export).unwrap();
        assert_eq!(f.store.schema_version().unwrap(), 2);
        assert!(f.store.review_graph_state("job-seed").unwrap().is_none());
        let state = initialize(&f.store, &f.root, &f.export, &seed).unwrap();
        let original = read_original(&f.store, &f.root, &state).unwrap();
        assert_eq!(original.graph, seed.graph);
        assert_eq!(original.documents, seed.documents);
        assert_eq!(state.snapshot_json, seed.snapshot.json());
        assert_eq!(
            initialize(&f.store, &f.root, &f.export, &seed).unwrap(),
            state
        );
        // Removing only this fixture's producer documents demonstrates that
        // recovery does not run Python or read mutable output paths.
        for descriptor in f.export.manifest["artifacts"].as_object().unwrap().values() {
            fs::remove_file(descriptor["path"].as_str().unwrap()).unwrap();
        }
        fs::remove_file(&f.manifest).unwrap();
        fs::remove_dir(f.root.join("output")).unwrap();
        let reader = DurableStore::open_read_only(&f.db).unwrap();
        let stored = reader.review_graph_state("job-seed").unwrap().unwrap();
        let recovered = read_original(&reader, &f.root, &stored).unwrap();
        assert_eq!(recovered.documents, seed.documents);
        assert_eq!(reader.schema_version().unwrap(), 2);
        assert_eq!(reader.job_status("other-job").unwrap(), JobStatus::Queued);
        assert!(reader.review_graph_state("other-job").unwrap().is_none());
        assert!(committed_export(&reader, "job-seed").is_err());
        assert!(
            archived_export(
                &reader,
                "job-seed",
                f.export.manifest_raw.replace("job-seed", "other-id")
            )
            .is_err()
        );
        // The read-only command succeeds with an empty runtime root.
        run([
            "--root",
            f.root.to_str().unwrap(),
            "--data-root",
            f.root.to_str().unwrap(),
            "--job-id",
            "job-seed",
        ]
        .into_iter()
        .map(OsString::from))
        .unwrap();
        assert_eq!(
            reader.review_graph_state("job-seed").unwrap().unwrap(),
            stored
        );
    }

    #[test]
    fn substituted_seed_identity_source_timing_confidence_voice_preview_and_version_refuse() {
        let f = fixture();
        for case in 0..11 {
            let mut packet = f.packet.clone();
            let mut snapshot = decode(packet["snapshot_json"].as_str().unwrap(), LIMIT).unwrap();
            let mut graph = decode(packet["graph_json"].as_str().unwrap(), LIMIT).unwrap();
            match case {
                0 => packet["job_id"] = "other-job".into(),
                1 => packet["manifest_sha256"] = format!("sha256:{}", "a".repeat(64)).into(),
                2 => graph["schema_version"] = 2.into(),
                3 => {
                    graph["documents"]["qc_report"]["path"] =
                        f.manifest.to_string_lossy().into_owned().into()
                }
                4 => snapshot["findings"][0]["translated_text"] = "Câu khác".into(),
                5 => snapshot["findings"][0]["range"]["end_ticks"] = "901".into(),
                6 => snapshot["findings"][0]["confidence"] = "0.2".into(),
                7 => snapshot["findings"][0]["speaker"] = "invented-speaker".into(),
                8 => snapshot["findings"][0]["voice"] = "vi-truc-ly-vieneu3-v1".into(),
                9 => {
                    snapshot["findings"][0]["output_preview"] =
                        f.manifest.to_string_lossy().into_owned().into()
                }
                _ => graph["producer_policy"] = "caller-authorized".into(),
            }
            packet["snapshot_json"] = snapshot.to_string().into();
            packet["graph_json"] = graph.to_string().into();
            assert!(
                AdmittedSeed::read(&packet.to_string(), &f.export).is_err(),
                "case{case}"
            );
        }
        assert!(f.store.review_graph_state("job-seed").unwrap().is_none());
        assert!(!f.root.join("control/review-assets").exists());
        assert_eq!(f.store.schema_version().unwrap(), 2);
    }

    #[test]
    fn archive_corruption_refuses_without_repair_or_replacing_the_original_head() {
        let f = fixture();
        let seed = AdmittedSeed::read(&f.packet.to_string(), &f.export).unwrap();
        let state = initialize(&f.store, &f.root, &f.export, &seed).unwrap();
        let wrapper = decode(&state.graph_json, LIMIT).unwrap();
        let descriptor =
            accepted_descriptor(&wrapper["accepted_documents"]["editable_timeline"]).unwrap();
        let exact = fs::read(&descriptor.path).unwrap();
        let mut permissions = fs::metadata(&descriptor.path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&descriptor.path, permissions).unwrap();
        let mut corrupt = exact.clone();
        corrupt[0] = b'!';
        fs::write(&descriptor.path, &corrupt).unwrap();
        assert!(read_original(&f.store, &f.root, &state).is_err());
        assert!(initialize(&f.store, &f.root, &f.export, &seed).is_err());
        assert_eq!(fs::read(&descriptor.path).unwrap(), corrupt);
        assert_eq!(
            f.store.review_graph_state("job-seed").unwrap().unwrap(),
            state
        );
        assert_eq!(
            f.store.job_status("job-seed").unwrap(),
            JobStatus::Succeeded
        );
    }

    #[test]
    fn changed_original_and_unknown_bound_format_cannot_initialize_or_replace_a_head() {
        let f = fixture();
        let seed = AdmittedSeed::read(&f.packet.to_string(), &f.export).unwrap();
        fs::write(&f.manifest, "changed").unwrap();
        assert!(initialize(&f.store, &f.root, &f.export, &seed).is_err());
        assert!(f.store.review_graph_state("job-seed").unwrap().is_none());
        fs::write(&f.manifest, &f.export.manifest_raw).unwrap();
        let state = initialize(&f.store, &f.root, &f.export, &seed).unwrap();
        for case in 0..3 {
            let mut changed = state.clone();
            let mut wrapper = decode(&changed.graph_json, LIMIT).unwrap();
            match case {
                0 => wrapper["schema_version"] = 2.into(),
                1 => {
                    wrapper["accepted_documents"]
                        .as_object_mut()
                        .unwrap()
                        .remove("qc_report");
                }
                _ => changed.token.revision = 1,
            }
            changed.graph_json = wrapper.to_string();
            assert!(read_original(&f.store, &f.root, &changed).is_err());
        }
        assert_eq!(
            f.store.review_graph_state("job-seed").unwrap().unwrap(),
            state
        );
    }

    #[test]
    fn original_command_rejects_missing_duplicate_and_caller_metadata_arguments() {
        for args in [
            vec![],
            vec!["--manifest", "arbitrary"],
            vec!["--job-id", "a", "--job-id", "b"],
            vec!["--root"],
            vec!["--initialize-original", "--initialize-original"],
        ] {
            assert!(run(args.into_iter().map(OsString::from)).is_err());
        }
    }

    // A test-only leaf process runs in this same binary. It observes the byte
    // gate and never spawns descendants or accesses production state.
    #[test]
    #[ignore]
    fn adapter_process_fixture() {
        let mut gate = [0u8];
        std::io::stdin().read_exact(&mut gate).unwrap();
        assert_eq!(gate, [1]);
        match std::env::var("DUBFLOW_SEED_ADAPTER_FIXTURE")
            .unwrap()
            .as_str()
        {
            "success" => println!("fixture-adapter-success"),
            "stdout-cap" => {
                std::io::stdout().write_all(&vec![b'x'; 8192]).unwrap();
                std::thread::sleep(Duration::from_secs(30));
            }
            "stderr-cap" => {
                std::io::stderr().write_all(&vec![b'x'; 8192]).unwrap();
                std::thread::sleep(Duration::from_secs(30));
            }
            "timeout" => std::thread::sleep(Duration::from_secs(30)),
            "error" => {
                eprintln!("fixture-refusal");
                std::process::exit(3);
            }
            _ => panic!("unknown test-only adapter case"),
        }
    }

    #[test]
    fn original_adapter_success_caps_refusal_and_deadline_are_bounded() {
        for case in ["success", "stdout-cap", "stderr-cap", "timeout", "error"] {
            let mut command = Command::new(std::env::current_exe().unwrap());
            command
                .args([
                    "--exact",
                    "review_seed::tests::adapter_process_fixture",
                    "--ignored",
                    "--nocapture",
                ])
                .env("DUBFLOW_SEED_ADAPTER_FIXTURE", case);
            let started = Instant::now();
            let result = collect(command, Duration::from_secs(3), 1024);
            assert!(
                started.elapsed() < Duration::from_secs(8),
                "case{case} was not bounded"
            );
            if case == "success" {
                assert!(result.unwrap().contains("fixture-adapter-success"));
            } else {
                assert!(result.is_err(), "case{case}");
            }
        }
    }
}
