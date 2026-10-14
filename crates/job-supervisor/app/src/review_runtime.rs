//! Read-only admission of the job's original owned producer before Python imports.
//! This validates executable/import bytes and catalog metadata, not model weights.
use super::review_assets::regular;
use super::review_document::{closed, decode};
use super::review_pin::plain;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::DurableStore;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const MAX_FILES: usize = 50000;
const IMPORT_WORKERS: usize = 4;
const IMPORT_DEADLINE: Duration = Duration::from_secs(180);

/// One cooperative deadline covers enumeration, queued files and all workers.
/// Counts describe fully verified files, not merely enumerated paths.
struct ImportProgress<'a> {
    phase: &'a str,
    started: Instant,
    deadline: Instant,
    files: AtomicUsize,
    bytes: AtomicU64,
}

impl ImportProgress<'_> {
    fn check(&self) -> SupervisorResult<()> {
        if Instant::now() >= self.deadline {
            return Err(invalid(&format!(
                "{} exceeded its bounded deadline (files={}, bytes={}, elapsed_ms={})",
                self.phase,
                self.files.load(Ordering::Relaxed),
                self.bytes.load(Ordering::Relaxed),
                self.started.elapsed().as_millis()
            )));
        }
        Ok(())
    }

    fn completed(&self, size: u64) -> SupervisorResult<()> {
        self.check()?;
        self.bytes
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
                n.checked_add(size)
            })
            .map_err(|_| invalid("producer byte inventory overflow"))?;
        self.files.fetch_add(1, Ordering::Relaxed);
        Ok(())
    }
}

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(raw: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(raw))
}
fn valid_hash(raw: &str) -> bool {
    raw.len() == 71
        && raw.starts_with("sha256:")
        && raw[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn fingerprint(path: &Path, pin: &Value, capture: bool, limit: u64) -> SupervisorResult<Vec<u8>> {
    fingerprint_before(path, pin, capture, limit, None)
}

fn fingerprint_before(
    path: &Path,
    pin: &Value,
    capture: bool,
    limit: u64,
    progress: Option<&ImportProgress<'_>>,
) -> SupervisorResult<Vec<u8>> {
    closed(pin, &["sha256", "size_bytes"])?;
    let size = pin["size_bytes"]
        .as_u64()
        .filter(|n| *n > 0 && *n <= limit)
        .ok_or_else(|| invalid("producer file size exceeds its bound"))?;
    let expected = pin["sha256"]
        .as_str()
        .filter(|h| valid_hash(h))
        .ok_or_else(|| invalid("producer file hash is invalid"))?;
    let file = regular(path)?;
    if file.metadata()?.len() != size {
        return Err(invalid(
            "job producer file size changed; compatibility required",
        ));
    }
    let mut stream = file.take(size + 1);
    let mut digest = Sha256::new();
    let mut observed = 0u64;
    let mut bytes = Vec::new();
    let mut buffer = [0u8; 32768];
    let started = Instant::now();
    loop {
        if let Some(shared) = progress {
            shared.check()?;
        }
        if started.elapsed() > Duration::from_secs(180) {
            return Err(invalid("producer hash exceeded its bounded deadline"));
        }
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        observed += count as u64;
        if observed > size {
            return Err(invalid("producer file grew beyond its original pin"));
        }
        digest.update(&buffer[..count]);
        if capture {
            bytes.extend_from_slice(&buffer[..count]);
        }
    }
    if observed != size || format!("sha256:{:x}", digest.finalize()) != expected {
        return Err(invalid(
            "job producer bytes changed; explicit compatibility required",
        ));
    }
    Ok(bytes)
}

fn relative(raw: &str) -> SupervisorResult<()> {
    if raw.is_empty()
        || raw.contains(['\\', ':'])
        || raw
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
        || Path::new(raw).is_absolute()
        || raw.chars().any(char::is_control)
    {
        return Err(invalid("producer inventory path is not portable"));
    }
    Ok(())
}

fn walk_import_files(
    root: &Path,
    phase: &str,
    visit: impl Fn(&Path, &str, &ImportProgress<'_>) -> SupervisorResult<u64> + Sync,
) -> SupervisorResult<HashSet<String>> {
    let started = Instant::now();
    walk_import_files_before(root, phase, started, started + IMPORT_DEADLINE, visit)
}

fn walk_import_files_before(
    root: &Path,
    phase: &str,
    started: Instant,
    deadline: Instant,
    visit: impl Fn(&Path, &str, &ImportProgress<'_>) -> SupervisorResult<u64> + Sync,
) -> SupervisorResult<HashSet<String>> {
    let mut observed = HashSet::new();
    let mut files = Vec::new();
    let progress = ImportProgress {
        phase,
        started,
        deadline,
        files: AtomicUsize::new(0),
        bytes: AtomicU64::new(0),
    };
    for subtree in ["app", "runtime"] {
        let mut directories = vec![root.join(subtree)];
        while let Some(directory) = directories.pop() {
            progress.check()?;
            plain(&directory)?;
            for entry in std::fs::read_dir(&directory)? {
                progress.check()?;
                let entry = entry?;
                let path = entry.path();
                // Enumeration metadata only selects the branch. Each directory
                // is checked when entered; every file callback must use regular,
                // whose fresh full ancestor checks surround the no-follow open.
                // Do not repeat that entire ancestor walk before every callback.
                let kind = entry.file_type()?;
                if kind.is_dir() {
                    directories.push(path);
                    continue;
                }
                if !kind.is_file() {
                    return Err(invalid("nonregular file in producer import tree"));
                }
                let name = path
                    .strip_prefix(root)
                    .map_err(|_| invalid("producer inventory escaped its root"))?
                    .to_str()
                    .ok_or_else(|| invalid("producer path is not UTF-8"))?
                    .replace('\\', "/");
                if observed.contains(&name) || observed.len() >= MAX_FILES {
                    return Err(invalid(
                        "duplicate or oversized observed producer inventory",
                    ));
                }
                observed.insert(name.clone());
                files.push((path, name));
            }
        }
    }
    // A bounded list (MAX_FILES) and four scoped workers avoid one thread per
    // import. Enumeration is never admission: callbacks independently re-open
    // each path through regular and verify the original complete pin. This
    // same worker limit also applies to the final metadata/inventory fence.
    let next = AtomicUsize::new(0);
    let stopped = AtomicBool::new(false);
    std::thread::scope(|scope| -> SupervisorResult<()> {
        let mut handles = Vec::new();
        for _ in 0..IMPORT_WORKERS.min(files.len()) {
            let files = &files;
            let progress = &progress;
            let next = &next;
            let stopped = &stopped;
            let visit = &visit;
            // try-spawn refuses resource exhaustion without panicking.
            let handle = std::thread::Builder::new().spawn_scoped(scope, move || {
                let result = (|| -> SupervisorResult<()> {
                    loop {
                        if stopped.load(Ordering::Acquire) {
                            return Ok(());
                        }
                        progress.check()?;
                        let index = next.fetch_add(1, Ordering::Relaxed);
                        let Some((path, name)) = files.get(index) else {
                            return Ok(());
                        };
                        let size = visit(path, name, progress)?;
                        progress.completed(size)?;
                    }
                })();
                if result.is_err() {
                    stopped.store(true, Ordering::Release);
                }
                result
            });
            match handle {
                Ok(handle) => handles.push(handle),
                Err(error) => {
                    stopped.store(true, Ordering::Release);
                    // Existing workers are joined explicitly before returning.
                    for handle in handles {
                        let _ = handle.join();
                    }
                    return Err(error.into());
                }
            }
        }
        let mut failure = None;
        for handle in handles {
            let result = handle
                .join()
                .unwrap_or_else(|_| Err(invalid("producer inventory verifier panicked")));
            if let Err(error) = result {
                stopped.store(true, Ordering::Release);
                if failure.is_none() {
                    failure = Some(error);
                }
            }
        }
        if let Some(error) = failure {
            return Err(error);
        }
        Ok(())
    })?;
    progress.check()?;
    if progress.files.load(Ordering::Relaxed) != observed.len() {
        return Err(invalid("producer inventory was not completely verified"));
    }
    Ok(observed)
}

fn final_inventory(root: &Path, inventory: &HashMap<String, Value>) -> SupervisorResult<()> {
    let observed = walk_import_files(root, "final producer inventory", |path, name, _| {
        let pin = inventory
            .get(name)
            .ok_or_else(|| invalid("unlisted final producer file"))?;
        let size = regular(path)?.metadata()?.len();
        if size != pin["size_bytes"].as_u64().unwrap() {
            return Err(invalid("final producer inventory changed after hashing"));
        }
        Ok(size)
    })?;
    if inventory
        .keys()
        .filter(|p| p.starts_with("app/") || p.starts_with("runtime/"))
        .any(|p| !observed.contains(p))
    {
        return Err(invalid(
            "final producer inventory is missing a previously hashed file",
        ));
    }
    Ok(())
}

pub(super) struct AdmittedRuntime {
    pub(super) root: PathBuf,
    pub(super) python: PathBuf,
    pub(super) profile: PathBuf,
    pub(super) catalog: Value,
    pub(super) catalog_hash: String,
    pub(super) release_hash: String,
    model_root: PathBuf,
    pub(super) ffmpeg: PathBuf,
    inventory: HashMap<String, Value>,
}

const PROCESSING_CAPABILITY: &str = "review_effective_cue_preparation_v1";
const PROCESSING_DECLARATION: &str = "app/engine/dubflow/review/processing-capabilities-v1.json";
const EFFECTIVE_ENTRY: &str = "app/engine/dubflow/review/effective_cue_plan.py";
const PROCESSING_DISPATCH_ENTRY: &str = "app/engine/dubflow/review/effective_cue_execution.py";

/// A processing release is an additional immutable dependency, never a new
/// producer binding written into the original job or copied into its bundle.
pub(super) struct AdmittedProcessingRuntime {
    runtime: AdmittedRuntime,
    origin: AdmittedRuntime,
    origin_producer: Value,
    source_sha: String,
    manifest_pin: Value,
    capability_pin: Value,
    protected_inventory_sha256: String,
}

fn processing_declaration() -> Value {
    json!({"schema_version":1,"kind":"review_processing_capabilities",
        "capabilities":[PROCESSING_CAPABILITY],
        "entrypoint_kind":"python_function","entrypoint_symbol":"prepare_effective_cue",
        "entrypoints":{"effective_cue_preparation":"engine/dubflow/review/effective_cue_plan.py",
            "native_dispatch":"engine/dubflow/review/effective_cue_execution.py"},
        "dispatch_contract":"review_native_effective_cue_bindings_v1",
        "formats":{"snapshot":2,"audio_composition":[1,2],"effective_cue_context":1,
            "prepared_effective_cue_request":1,"tts_document":[1,2],"spoken_text":1},
        "compatibility":"exact_original_runtime_tts_mix_models_media",
        "cumulative_execution":false,"active_graph_publication":false})
}

fn protected_processing_path(path: &str) -> bool {
    [
        "runtime/",
        "app/engine/dubflow/tts/",
        "app/engine/dubflow/mix/",
        "app/engine/dubflow/models/",
        "app/engine/dubflow/media/",
        "app/models/",
        "app/minimal-pipeline-wiring/",
    ]
    .iter()
    .any(|prefix| path.starts_with(prefix))
}

impl AdmittedProcessingRuntime {
    pub(super) fn read(
        store: &DurableStore,
        job: &str,
        root: &Path,
        manifest_pin: &Value,
        source_sha: &str,
    ) -> SupervisorResult<Self> {
        let raw = store.job_start_request(job)?.ok_or_else(|| {
            invalid("processing admission requires the original immutable job binding")
        })?;
        let request = decode(&raw, 64 * 1024 * 1024)?;
        if request["schema_version"].as_u64() != Some(1) {
            return Err(invalid("unsupported original processing binding"));
        }
        Self::from_origin(&request["producer"], root, manifest_pin, source_sha)
    }

    fn from_origin(
        producer: &Value,
        root: &Path,
        manifest_pin: &Value,
        source_sha: &str,
    ) -> SupervisorResult<Self> {
        if !matches!(source_sha.len(), 40 | 64)
            || !source_sha
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(invalid(
                "processing source identity must be an exact commit SHA",
            ));
        }
        let origin_root = super::absolute_path(
            producer["runtime_root"]
                .as_str()
                .ok_or_else(|| invalid("original runtime root missing"))?,
            "original runtime root",
        )?;
        let origin = AdmittedRuntime::from_producer(producer, &origin_root)?;
        plain(root)?;
        if !root.is_absolute() || root.components().any(|c| matches!(c, Component::ParentDir)) {
            return Err(invalid(
                "processing root must be explicitly admitted and absolute",
            ));
        }
        let raw = fingerprint(
            &root.join("release-manifest.json"),
            manifest_pin,
            true,
            MANIFEST_LIMIT,
        )?;
        let release = decode(
            std::str::from_utf8(&raw).map_err(|_| invalid("processing release is not UTF-8"))?,
            MANIFEST_LIMIT as usize,
        )?;
        if release["schema_version"].as_u64() != Some(1) || release["source_sha"] != source_sha {
            return Err(invalid(
                "processing release differs from its exact source identity",
            ));
        }
        // Only the processing worker descriptor is taken from the new release.
        // Runtime/tools/model manifests and model-root authority remain original.
        let worker = release["artifacts"]
            .as_array()
            .ok_or_else(|| invalid("processing release inventory missing"))?
            .iter()
            .find(|v| v["path"] == "app/engine/dubflow/worker/production_job.py")
            .ok_or_else(|| invalid("processing worker absent from the release inventory"))?;
        let worker_size = worker["size_bytes"]
            .as_str()
            .and_then(|s| s.parse::<u64>().ok())
            .filter(|n| *n > 0 && n.to_string() == worker["size_bytes"].as_str().unwrap_or(""))
            .ok_or_else(|| invalid("processing worker byte count invalid"))?;
        let checksum = worker["sha256"]
            .as_str()
            .ok_or_else(|| invalid("processing worker checksum missing"))?;
        let mut processing = producer.clone();
        processing["runtime_root"] = json!(root);
        processing["release_manifest"] = manifest_pin.clone();
        processing["worker"] =
            json!({"sha256":format!("sha256:{checksum}"),"size_bytes":worker_size});
        // Same complete bounded parallel import verification and missing/extra/
        // no-follow/full-hash/final-inventory fences as original admission.
        let runtime = AdmittedRuntime::from_producer(&processing, root)?;
        let original_protected: std::collections::BTreeMap<_, _> = origin
            .inventory
            .iter()
            .filter(|(name, _)| protected_processing_path(name))
            .map(|(name, pin)| (name.clone(), pin.clone()))
            .collect();
        let processing_protected: std::collections::BTreeMap<_, _> = runtime
            .inventory
            .iter()
            .filter(|(name, _)| protected_processing_path(name))
            .map(|(name, pin)| (name.clone(), pin.clone()))
            .collect();
        if original_protected != processing_protected || runtime.catalog_hash != origin.catalog_hash
        {
            return Err(invalid(
                "processing release changed original runtime/TTS/mix/model/media pins",
            ));
        }
        let capability_pin = runtime
            .inventory
            .get(PROCESSING_DECLARATION)
            .ok_or_else(|| invalid("processing release lacks explicit effective cue capability"))?
            .clone();
        let declaration = fingerprint(
            &root.join(PROCESSING_DECLARATION),
            &capability_pin,
            true,
            64 * 1024,
        )?;
        let declaration = decode(
            std::str::from_utf8(&declaration)
                .map_err(|_| invalid("processing capability is not UTF-8"))?,
            64 * 1024,
        )?;
        let mut admitted = String::new();
        let mut expected = String::new();
        super::review_document::canonical(&declaration, &mut admitted)?;
        super::review_document::canonical(&processing_declaration(), &mut expected)?;
        if admitted != expected {
            return Err(invalid(
                "unknown processing capabilities require explicit compatibility",
            ));
        }
        let pin = runtime.inventory.get(EFFECTIVE_ENTRY).ok_or_else(|| {
            invalid("effective cue entry is not part of the admitted processing release")
        })?;
        fingerprint(&root.join(EFFECTIVE_ENTRY), pin, false, 64 * 1024 * 1024)?;
        let mut protected = String::new();
        super::review_document::canonical(
            &serde_json::to_value(&original_protected)?,
            &mut protected,
        )?;
        let result = Self {
            runtime,
            origin,
            origin_producer: producer.clone(),
            source_sha: source_sha.into(),
            manifest_pin: manifest_pin.clone(),
            capability_pin,
            protected_inventory_sha256: hash(protected.as_bytes()),
        };
        result.preparation_dispatch()?;
        Ok(result)
    }

    pub(super) fn effective_entry(&self) -> SupervisorResult<PathBuf> {
        fingerprint(
            &self.runtime.root.join("release-manifest.json"),
            &self.manifest_pin,
            false,
            MANIFEST_LIMIT,
        )?;
        fingerprint(
            &self.origin.root.join("release-manifest.json"),
            &self.origin_producer["release_manifest"],
            false,
            MANIFEST_LIMIT,
        )?;
        fingerprint(
            &self.runtime.root.join(PROCESSING_DECLARATION),
            &self.capability_pin,
            false,
            64 * 1024,
        )?;
        let pin = self
            .runtime
            .inventory
            .get(EFFECTIVE_ENTRY)
            .ok_or_else(|| invalid("processing entry pin unavailable"))?;
        let path = self.runtime.root.join(EFFECTIVE_ENTRY);
        fingerprint(&path, pin, false, 64 * 1024 * 1024)?;
        final_inventory(&self.origin.root, &self.origin.inventory)?;
        final_inventory(&self.runtime.root, &self.runtime.inventory)?;
        Ok(path)
    }

    pub(super) fn receipt(&self) -> SupervisorResult<Value> {
        let entry = self.effective_entry()?;
        let dispatch = self.preparation_dispatch()?;
        let mut origin = String::new();
        super::review_document::canonical(&self.origin_producer, &mut origin)?;
        Ok(
            json!({"schema_version":1,"kind":"review_admitted_processing_release",
            "capability":PROCESSING_CAPABILITY,"source_sha":self.source_sha,
            "processing_root":self.runtime.root,"release_manifest":self.manifest_pin,
            "capability_document":self.capability_pin,"effective_cue_entry":{
                "path":entry,"pin":self.runtime.inventory[EFFECTIVE_ENTRY]},
            "native_dispatch_entry":{"path":dispatch,"pin":self.runtime.inventory[PROCESSING_DISPATCH_ENTRY]},
            "original_producer_sha256":hash(origin.as_bytes()),
            "original_release_sha256":self.origin.release_hash,
            "protected_inventory_sha256":self.protected_inventory_sha256,
            "original_model_root":self.origin.model_root,
            "original_job_binding_changed":false,"model_weights_verified":false,
            "cumulative_execution_admitted":false,"active_graph_published":false}),
        )
    }

    pub(super) fn runtime(&self) -> &AdmittedRuntime {
        &self.runtime
    }

    pub(super) fn preparation_dispatch(&self) -> SupervisorResult<PathBuf> {
        self.effective_entry()?;
        let pin = self
            .runtime
            .inventory
            .get(PROCESSING_DISPATCH_ENTRY)
            .ok_or_else(|| {
                invalid("native current cue dispatch is absent from its processing release")
            })?;
        let path = self.runtime.root.join(PROCESSING_DISPATCH_ENTRY);
        fingerprint(&path, pin, false, 64 * 1024 * 1024)?;
        Ok(path)
    }
}

impl AdmittedRuntime {
    pub(super) fn read(store: &DurableStore, job: &str, root: &Path) -> SupervisorResult<Self> {
        let raw = store
            .job_start_request(job)?
            .ok_or_else(|| invalid("job lacks immutable producer binding"))?;
        let request = decode(&raw, 64 * 1024 * 1024)?;
        if request["schema_version"].as_u64() != Some(1) {
            return Err(invalid("unsupported original producer binding"));
        }
        Self::from_producer(&request["producer"], root)
    }

    fn from_producer(producer: &Value, root: &Path) -> SupervisorResult<Self> {
        closed(
            producer,
            &[
                "runtime_root",
                "model_root",
                "worker",
                "python",
                "ffmpeg",
                "ffprobe",
                "release_manifest",
                "model_manifests",
            ],
        )?;
        plain(root)?;
        let recorded_root = super::absolute_path(
            producer["runtime_root"]
                .as_str()
                .ok_or_else(|| invalid("missing original runtime root"))?,
            "original runtime root",
        )?;
        plain(&recorded_root)?;
        if !root.is_dir()
            || root.components().any(|p| matches!(p, Component::ParentDir))
            || root.canonicalize()? != recorded_root.canonicalize()?
        {
            return Err(invalid(
                "original job runtime must remain installed at its pinned root",
            ));
        }
        let release_path = root.join("release-manifest.json");
        let raw = fingerprint(
            &release_path,
            &producer["release_manifest"],
            true,
            MANIFEST_LIMIT,
        )?;
        let release = decode(
            std::str::from_utf8(&raw).map_err(|_| invalid("release inventory is not UTF-8"))?,
            MANIFEST_LIMIT as usize,
        )?;
        if release["schema_version"].as_u64() != Some(1) {
            return Err(invalid("unsupported pinned release inventory"));
        }
        let records = release["artifacts"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= MAX_FILES)
            .ok_or_else(|| invalid("release inventory count exceeds its bound"))?;
        let mut inventory = HashMap::new();
        let mut folded = HashSet::new();
        for item in records {
            let path = item["path"]
                .as_str()
                .ok_or_else(|| invalid("missing release inventory path"))?;
            relative(path)?;
            let size_text = item["size_bytes"]
                .as_str()
                .ok_or_else(|| invalid("release size must be a decimal string"))?;
            let size = size_text
                .parse::<u64>()
                .map_err(|_| invalid("invalid release byte count"))?;
            let checksum = item["sha256"]
                .as_str()
                .ok_or_else(|| invalid("missing release checksum"))?;
            if size.to_string() != size_text
                || size > 8 * 1024 * 1024 * 1024
                || checksum.len() != 64
                || !valid_hash(&format!("sha256:{checksum}"))
                || !folded.insert(path.to_lowercase())
                || inventory
                    .insert(
                        path.to_string(),
                        json!({"sha256":format!("sha256:{checksum}"),"size_bytes":size}),
                    )
                    .is_some()
            {
                return Err(invalid("release inventory is ambiguous"));
            }
        }
        // Imports are admitted as complete trees; an extra module/library can
        // execute before an otherwise trusted script can check itself.
        let observed = walk_import_files(
            root,
            "original runtime validation",
            |path, name, progress| {
                let pin = inventory
                    .get(name)
                    .ok_or_else(|| invalid("unlisted file in original producer import tree"))?;
                // Empty tracked files are allowed in release trees. Both paths keep
                // the same fresh no-follow handle/ancestor checks and full hashes.
                if pin["size_bytes"] == 0 {
                    let file = regular(path)?;
                    if file.metadata()?.len() != 0 || pin["sha256"] != hash(&[]) {
                        return Err(invalid("empty producer file changed"));
                    }
                } else {
                    fingerprint_before(path, pin, false, 8 * 1024 * 1024 * 1024, Some(progress))?;
                }
                Ok(pin["size_bytes"].as_u64().unwrap())
            },
        )?;
        if inventory
            .keys()
            .filter(|p| p.starts_with("app/") || p.starts_with("runtime/"))
            .any(|p| !observed.contains(p))
        {
            return Err(invalid("original producer import file is missing"));
        }
        let python = root.join("runtime/python.exe");
        let worker = root.join("app/engine/dubflow/worker/production_job.py");
        fingerprint(&python, &producer["python"], false, 256 * 1024 * 1024)?;
        fingerprint(&worker, &producer["worker"], false, 64 * 1024 * 1024)?;
        for (role, name) in [("ffmpeg", "ffmpeg.exe"), ("ffprobe", "ffprobe.exe")] {
            let path = super::first_existing(&[
                root.join("runtime/media").join(name),
                root.join("runtime/ffmpeg").join(name),
            ])
            .ok_or_else(|| invalid("original owned media tool is missing"))?;
            fingerprint(&path, &producer[role], false, 512 * 1024 * 1024)?;
        }
        let manifests = producer["model_manifests"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= 64)
            .ok_or_else(|| invalid("original model manifest inventory is missing"))?;
        let manifest_root = root.join("app/models/manifests");
        let mut names = HashSet::new();
        for entry in manifests {
            let pair = entry
                .as_array()
                .filter(|v| v.len() == 2)
                .ok_or_else(|| invalid("invalid original model manifest tuple"))?;
            let name = pair[0]
                .as_str()
                .filter(|s| !s.contains('/') && s.ends_with(".json"))
                .ok_or_else(|| invalid("invalid original model manifest name"))?;
            relative(name)?;
            if !names.insert(name.to_string()) {
                return Err(invalid("duplicate original model manifest"));
            }
            fingerprint(&manifest_root.join(name), &pair[1], false, 512 * 1024)?;
        }
        let observed_manifests = std::fs::read_dir(&manifest_root)?
            .map(|e| e.map(|p| p.path()))
            .collect::<Result<Vec<_>, _>>()?;
        if observed_manifests
            .iter()
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("json"))
            .any(|p| !names.contains(p.file_name().unwrap().to_str().unwrap_or("")))
        {
            return Err(invalid("job model manifest roster changed"));
        }
        let profile = manifest_root.join("production-cpu-v1.json");
        let profile_name = "app/models/manifests/production-cpu-v1.json";
        let selected_raw = fingerprint(
            &profile,
            inventory
                .get(profile_name)
                .ok_or_else(|| invalid("pinned CPU profile unavailable"))?,
            true,
            512 * 1024,
        )?;
        let selected = decode(
            std::str::from_utf8(&selected_raw).map_err(|_| invalid("profile is not UTF-8"))?,
            512 * 1024,
        )?;
        let recipe = selected["tts_neural_profile"]
            .as_str()
            .ok_or_else(|| invalid("pinned CPU profile has no neural recipe"))?;
        relative(recipe)?;
        let recipe_name = format!("app/{recipe}");
        let catalog_raw = fingerprint(
            &root.join(&recipe_name),
            inventory
                .get(&recipe_name)
                .ok_or_else(|| invalid("catalog is absent from pinned release"))?,
            true,
            512 * 1024,
        )?;
        let catalog = decode(
            std::str::from_utf8(&catalog_raw).map_err(|_| invalid("catalog is not UTF-8"))?,
            512 * 1024,
        )?;
        let plan = "app/engine/dubflow/review/cue_plan.py";
        if !inventory.contains_key(plan) {
            return Err(invalid(
                "original producer lacks the cue adapter; explicit compatibility required",
            ));
        }
        // Fence the inventory and entry point again after the complete tree read.
        fingerprint(&root.join(plan), &inventory[plan], false, 64 * 1024 * 1024)?;
        final_inventory(root, &inventory)?;
        fingerprint(
            &release_path,
            &producer["release_manifest"],
            false,
            MANIFEST_LIMIT,
        )?;
        Ok(Self {
            root: root.to_path_buf(),
            python,
            profile,
            catalog,
            catalog_hash: hash(&catalog_raw),
            release_hash: hash(&raw),
            model_root: super::absolute_path(
                producer["model_root"]
                    .as_str()
                    .ok_or_else(|| invalid("missing original model root"))?,
                "original model root",
            )?,
            ffmpeg: super::first_existing(&[
                root.join("runtime/media/ffmpeg.exe"),
                root.join("runtime/ffmpeg/ffmpeg.exe"),
            ])
            .ok_or_else(|| invalid("original media executable unavailable"))?,
            inventory,
        })
    }

    pub(super) fn cue_execution(&self) -> SupervisorResult<(PathBuf, PathBuf)> {
        let name = "app/engine/dubflow/review/cue_execution.py";
        let entry = self.root.join(name);
        let pin = self.inventory.get(name).ok_or_else(|| {
            invalid(
                "original producer lacks physical cue execution; explicit compatibility required",
            )
        })?;
        fingerprint(&entry, pin, false, 64 * 1024 * 1024)?;
        plain(&self.model_root)?;
        if !self.model_root.is_dir()
            || self
                .model_root
                .components()
                .any(|c| matches!(c, Component::ParentDir))
        {
            return Err(invalid(
                "original model root unavailable; restore it explicitly before Review",
            ));
        }
        Ok((entry, self.model_root.clone()))
    }

    pub(super) fn mix_execution(&self) -> SupervisorResult<PathBuf> {
        let name = "app/engine/dubflow/review/mix_execution.py";
        let pin = self.inventory.get(name).ok_or_else(|| {
            invalid("original producer lacks private mix adapter; explicit compatibility required")
        })?;
        let entry = self.root.join(name);
        fingerprint(&entry, pin, false, 64 * 1024 * 1024)?;
        Ok(entry)
    }

    /// The recipe must belong to the original job's admitted inventory. New
    /// supervisor code does not authorize a newer adapter for a historical job.
    pub(super) fn render_recipe(
        &self,
        context_hash: Option<&str>,
    ) -> SupervisorResult<(PathBuf, PathBuf, String)> {
        let entry = self
            .root
            .join("app/engine/dubflow/review/render_execution.py");
        let probe = super::first_existing(&[
            self.root.join("runtime/media/ffprobe.exe"),
            self.root.join("runtime/ffmpeg/ffprobe.exe"),
        ])
        .ok_or_else(|| invalid("original render probe unavailable"))?;
        let mut pins = Vec::new();
        for path in [
            self.ffmpeg.clone(),
            probe.clone(),
            entry.clone(),
            self.root
                .join("app/engine/dubflow/worker/production_job.py"),
            self.root.join("app/engine/dubflow/media/adapter.py"),
            self.root
                .join("app/engine/dubflow/review/corrected_timeline.py"),
            self.root.join("app/engine/dubflow/review/wire.py"),
            self.root.join("app/engine/dubflow/review/production.py"),
            self.root
                .join("app/engine/dubflow/review/synthesis_input.py"),
            self.root
                .join("app/engine/dubflow/review/synthesis_worker.py"),
        ] {
            let name = path
                .strip_prefix(&self.root)
                .map_err(|_| invalid("render recipe escaped original root"))?
                .to_str()
                .ok_or_else(|| invalid("render recipe path is not UTF-8"))?
                .replace('\\', "/");
            let pin = self.inventory.get(&name).ok_or_else(|| {
                invalid(
                    "original producer lacks the render recipe; explicit compatibility required",
                )
            })?;
            fingerprint(&path, pin, false, 512 * 1024 * 1024)?;
            pins.push(pin["sha256"].clone());
        }
        let identity = if let Some(context) = context_hash {
            let mut raw = String::new();
            super::review_document::canonical(
                &json!({
                    "context":context,"recipe":"review-private-render-v1","executables_and_source":pins
                }),
                &mut raw,
            )?;
            hash(raw.as_bytes())
        } else {
            String::new()
        };
        Ok((entry, probe, identity))
    }

    /// Bind the owned adapter's target profile to this original catalog. Model
    /// weights remain unverified until the later physical producer loader.
    pub(super) fn voice(&self, id: &str) -> SupervisorResult<Value> {
        let roster = self.catalog["voices"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= 64)
            .ok_or_else(|| invalid("pinned voice roster is missing"))?;
        let choices = roster
            .iter()
            .filter(|v| v["voice_id"] == id)
            .collect::<Vec<_>>();
        if choices.len() != 1 || choices[0]["approved"] != true {
            return Err(invalid(
                "voice is not uniquely approved in the pinned job catalog",
            ));
        }
        let choice = choices[0];
        Ok(
            json!({"voice_id":id,"voice_version":choice["voice_version"],"language":"vi","display_name":choice["name"],
            "model_id":self.catalog["model_id"],"model_version":self.catalog["model_version"],
            "model_hash":format!("sha256:{}",self.catalog["model_tree_sha256"].as_str().ok_or_else(||invalid("missing catalog model hash"))?),
            "manifest_hash":self.catalog_hash,"license_id":self.catalog["license_id"],"approved":true,"network_required":false,
            "credential_required":false,"speaking_rate_milli":1000,"pitch_style":"auto","emotion_mode":"neutral",
            "default":self.catalog["voice_id"]==id}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::Ordering;

    struct Fixture {
        root: PathBuf,
        producer: Value,
        inventory: HashMap<String, Value>,
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
    fn pin(raw: &[u8]) -> Value {
        json!({"sha256":hash(raw),"size_bytes":raw.len()})
    }
    fn fixture() -> Fixture {
        let root = std::env::temp_dir().join(format!(
            "dubflow-review-runtime-{}-{}",
            std::process::id(),
            super::super::ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let files=[
            ("runtime/python.exe",b"owned-python".to_vec()),
            ("runtime/media/ffmpeg.exe",b"owned-ffmpeg".to_vec()),
            ("runtime/media/ffprobe.exe",b"owned-ffprobe".to_vec()),
            ("runtime/Lib/site.py",b"# owned imports".to_vec()),
            ("runtime/Lib/empty.py",vec![]),
            ("app/engine/dubflow/worker/production_job.py",b"# owned producer".to_vec()),
            ("app/engine/dubflow/review/cue_plan.py",b"# owned cue adapter".to_vec()),
            ("app/engine/dubflow/review/cue_execution.py",b"# owned physical cue adapter".to_vec()),
            ("app/engine/dubflow/review/mix_execution.py",b"# owned private mix adapter".to_vec()),
            ("app/models/manifests/production-cpu-v1.json",br#"{"tts_neural_profile":"models/manifests/production-vieneu-v1.json"}"#.to_vec()),
            ("app/models/manifests/production-vieneu-v1.json",json!({"schema_version":1,"model_id":"test-model",
                "model_version":"1","model_tree_sha256":"a".repeat(64),"license_id":"Apache-2.0","voice_id":"voice-a",
                "voices":[{"voice_id":"voice-a","voice_version":"1","name":"Giọng thử","approved":true}]}).to_string().into_bytes()),
        ];
        let mut inventory = HashMap::new();
        let mut artifacts = Vec::new();
        for (name, bytes) in files {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, &bytes).unwrap();
            inventory.insert(name.into(), pin(&bytes));
            artifacts.push(json!({"path":name,"sha256":&hash(&bytes)[7..],"size_bytes":bytes.len().to_string()}));
        }
        let raw = json!({"schema_version":1,"artifacts":artifacts}).to_string();
        fs::write(root.join("release-manifest.json"), &raw).unwrap();
        let mut manifests = Vec::new();
        for name in ["production-cpu-v1.json", "production-vieneu-v1.json"] {
            manifests.push(json!([
                name,
                inventory[&format!("app/models/manifests/{name}")]
            ]));
        }
        let producer = json!({"runtime_root":root,"model_root":root.join("unprovisioned-models"),"release_manifest":pin(raw.as_bytes()),
            "python":inventory["runtime/python.exe"],"worker":inventory["app/engine/dubflow/worker/production_job.py"],
            "ffmpeg":inventory["runtime/media/ffmpeg.exe"],"ffprobe":inventory["runtime/media/ffprobe.exe"],"model_manifests":manifests});
        Fixture {
            root,
            producer,
            inventory,
        }
    }

    fn put_processing_file(f: &mut Fixture, name: &str, raw: &[u8]) {
        let path = f.root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, raw).unwrap();
        f.inventory.insert(name.into(), pin(raw));
    }

    fn seal_processing_fixture(f: &mut Fixture) -> Value {
        let mut entries: Vec<_> = f.inventory.iter().collect();
        entries.sort_by_key(|(name, _)| *name);
        let artifacts: Vec<_> = entries
            .iter()
            .map(|(name, p)| {
                json!({"path":name,
            "sha256":p["sha256"].as_str().unwrap().trim_start_matches("sha256:"),
            "size_bytes":p["size_bytes"].as_u64().unwrap().to_string()})
            })
            .collect();
        let raw = json!({"schema_version":1,"source_sha":"b".repeat(40),"artifacts":artifacts})
            .to_string();
        fs::write(f.root.join("release-manifest.json"), raw.as_bytes()).unwrap();
        pin(raw.as_bytes())
    }

    fn processing_fixture() -> (Fixture, Value) {
        let mut f = fixture();
        put_processing_file(
            &mut f,
            PROCESSING_DECLARATION,
            processing_declaration().to_string().as_bytes(),
        );
        put_processing_file(
            &mut f,
            EFFECTIVE_ENTRY,
            b"# new effective preparation adapter",
        );
        put_processing_file(
            &mut f,
            PROCESSING_DISPATCH_ENTRY,
            b"# owned isolated native preparation dispatch",
        );
        put_processing_file(
            &mut f,
            "app/engine/dubflow/worker/production_job.py",
            b"# explicitly separate processing worker",
        );
        let manifest = seal_processing_fixture(&mut f);
        (f, manifest)
    }

    fn fixture_contents(
        root: &Path,
    ) -> std::collections::BTreeMap<PathBuf, (Vec<u8>, std::time::SystemTime)> {
        let mut result = std::collections::BTreeMap::new();
        let mut pending = vec![root.to_path_buf()];
        while let Some(directory) = pending.pop() {
            for entry in fs::read_dir(directory).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    pending.push(path);
                } else {
                    result.insert(
                        path.clone(),
                        (
                            fs::read(&path).unwrap(),
                            fs::metadata(path).unwrap().modified().unwrap(),
                        ),
                    );
                }
            }
        }
        result
    }

    #[test]
    fn separate_processing_admission_keeps_original_binding_root_models_and_bytes() {
        let original = fixture();
        let (processing, manifest) = processing_fixture();
        let saved = fixture_contents(&original.root);
        let producer = original.producer.clone();
        let admitted = AdmittedProcessingRuntime::from_origin(
            &producer,
            &processing.root,
            &manifest,
            &"b".repeat(40),
        )
        .unwrap();
        let receipt = admitted.receipt().unwrap();
        assert_eq!(receipt["source_sha"], "b".repeat(40));
        assert_eq!(receipt["processing_root"], json!(processing.root));
        assert_eq!(receipt["original_model_root"], producer["model_root"]);
        assert_eq!(receipt["original_job_binding_changed"], false);
        assert_eq!(receipt["model_weights_verified"], false);
        assert_eq!(receipt["cumulative_execution_admitted"], false);
        assert_eq!(receipt["active_graph_published"], false);
        assert_eq!(original.producer, producer);
        assert_eq!(fixture_contents(&original.root), saved);
        assert!(!original.root.join("unprovisioned-models").exists());
        assert_eq!(
            admitted.effective_entry().unwrap(),
            processing.root.join(EFFECTIVE_ENTRY)
        );
    }

    #[test]
    fn processing_admission_refuses_source_manifest_unknown_capability_or_missing_entry() {
        let original = fixture();
        for mutation in 0..7 {
            let (mut f, mut manifest) = processing_fixture();
            let mut source = "b".repeat(40);
            match mutation {
                0 => source = "c".repeat(40),
                1 => manifest["sha256"] = json!(hash(b"foreign manifest")),
                2 => {
                    let mut declaration = processing_declaration();
                    declaration["schema_version"] = json!(2);
                    put_processing_file(
                        &mut f,
                        PROCESSING_DECLARATION,
                        declaration.to_string().as_bytes(),
                    );
                    manifest = seal_processing_fixture(&mut f);
                }
                3 => {
                    let mut declaration = processing_declaration();
                    declaration["cumulative_execution"] = json!(true);
                    put_processing_file(
                        &mut f,
                        PROCESSING_DECLARATION,
                        declaration.to_string().as_bytes(),
                    );
                    manifest = seal_processing_fixture(&mut f);
                }
                4 => {
                    fs::remove_file(f.root.join(EFFECTIVE_ENTRY)).unwrap();
                    f.inventory.remove(EFFECTIVE_ENTRY);
                    manifest = seal_processing_fixture(&mut f);
                }
                5 => {
                    fs::remove_file(f.root.join(PROCESSING_DECLARATION)).unwrap();
                    f.inventory.remove(PROCESSING_DECLARATION);
                    manifest = seal_processing_fixture(&mut f);
                }
                _ => source = "B".repeat(40),
            }
            assert!(
                AdmittedProcessingRuntime::from_origin(
                    &original.producer,
                    &f.root,
                    &manifest,
                    &source
                )
                .is_err(),
                "mutation {mutation}"
            );
        }
    }

    #[test]
    fn processing_admission_compares_complete_protected_inventory_not_only_entrypoint_pins() {
        let original = fixture();
        for mutation in 0..7 {
            let (mut f, _) = processing_fixture();
            match mutation {
                0 => {
                    put_processing_file(&mut f, "runtime/Lib/site.py", b"# changed runtime import")
                }
                1 => put_processing_file(&mut f, "runtime/Lib/extra.py", b"# extra runtime import"),
                2 => {
                    fs::remove_file(f.root.join("runtime/Lib/empty.py")).unwrap();
                    f.inventory.remove("runtime/Lib/empty.py");
                }
                3 => put_processing_file(
                    &mut f,
                    "app/engine/dubflow/tts/backend.py",
                    b"# changed TTS backend",
                ),
                4 => put_processing_file(
                    &mut f,
                    "app/engine/dubflow/mix/backend.py",
                    b"# changed mix backend",
                ),
                5 => put_processing_file(
                    &mut f,
                    "app/engine/dubflow/models/loader.py",
                    b"# changed model loader",
                ),
                _ => put_processing_file(
                    &mut f,
                    "app/engine/dubflow/media/adapter.py",
                    b"# changed media adapter",
                ),
            }
            let manifest = seal_processing_fixture(&mut f);
            assert!(
                AdmittedProcessingRuntime::from_origin(
                    &original.producer,
                    &f.root,
                    &manifest,
                    &"b".repeat(40)
                )
                .is_err(),
                "mutation {mutation}"
            );
        }
    }

    #[test]
    fn processing_receipt_rechecks_capability_entry_original_manifest_and_extra_imports() {
        let original = fixture();
        for target in [
            PROCESSING_DECLARATION,
            EFFECTIVE_ENTRY,
            "app/engine/dubflow/review/unlisted.py",
        ] {
            let (f, manifest) = processing_fixture();
            let admitted = AdmittedProcessingRuntime::from_origin(
                &original.producer,
                &f.root,
                &manifest,
                &"b".repeat(40),
            )
            .unwrap();
            let path = f.root.join(target);
            if path.exists() {
                let mut raw = fs::read(&path).unwrap();
                raw[0] ^= 1;
                fs::write(path, raw).unwrap();
            } else {
                fs::write(path, b"# unlisted import").unwrap();
            }
            assert!(admitted.receipt().is_err());
        }
        let (f, manifest) = processing_fixture();
        let admitted = AdmittedProcessingRuntime::from_origin(
            &original.producer,
            &f.root,
            &manifest,
            &"b".repeat(40),
        )
        .unwrap();
        fs::write(original.root.join("release-manifest.json"), b"changed").unwrap();
        assert!(admitted.receipt().is_err());
    }

    #[test]
    fn render_dispatch_requires_every_original_recipe_file_and_fresh_bytes() {
        let f = fixture();
        let mut runtime = AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        assert!(runtime.render_recipe(None).is_err());
        for name in [
            "render_execution.py",
            "corrected_timeline.py",
            "wire.py",
            "production.py",
            "synthesis_input.py",
            "synthesis_worker.py",
        ] {
            let name = format!("app/engine/dubflow/review/{name}");
            let raw = format!("# pinned {name}").into_bytes();
            fs::write(f.root.join(&name), &raw).unwrap();
            runtime.inventory.insert(name, pin(&raw));
        }
        let media = "app/engine/dubflow/media/adapter.py";
        fs::create_dir_all(f.root.join(media).parent().unwrap()).unwrap();
        fs::write(f.root.join(media), b"# pinned media adapter").unwrap();
        runtime
            .inventory
            .insert(media.into(), pin(b"# pinned media adapter"));
        let context_a = hash(b"native-context-a");
        let context_b = hash(b"native-context-b");
        let a = runtime.render_recipe(Some(&context_a)).unwrap();
        assert!(valid_hash(&a.2));
        // App-owned Python _encode/_digest oracle for these exact ten pins.
        assert_eq!(
            a.2,
            "sha256:6fa411184df113f99211859e9acbf95372dee88a32c58dfb18170aaa04e61e37"
        );
        assert_ne!(a.2, runtime.render_recipe(Some(&context_b)).unwrap().2);
        fs::write(&a.0, b"# changed pinned recipe").unwrap();
        assert!(runtime.render_recipe(Some(&context_a)).is_err());
    }

    #[test]
    fn original_owned_runtime_admits_all_imports_and_catalog_without_provisioning() {
        let f = fixture();
        let admitted = AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        assert_eq!(admitted.python, f.root.join("runtime/python.exe"));
        let voice = admitted.voice("voice-a").unwrap();
        assert_eq!(voice["display_name"], "Giọng thử");
        assert_eq!(voice["manifest_hash"], admitted.catalog_hash);
        assert_eq!(voice["model_hash"], format!("sha256:{}", "a".repeat(64)));
        assert!(admitted.voice("foreign-voice").is_err());
        assert!(!f.root.join("unprovisioned-models").exists());
    }

    #[test]
    fn physical_cue_entry_and_model_root_remain_original_and_never_provisioned() {
        let f = fixture();
        let runtime = AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        assert!(runtime.cue_execution().is_err());
        let model_root = f.root.join("unprovisioned-models");
        assert!(!model_root.exists());
        fs::create_dir(&model_root).unwrap();
        let (entry, observed) = runtime.cue_execution().unwrap();
        assert_eq!(observed, model_root);
        assert_eq!(
            entry,
            f.root.join("app/engine/dubflow/review/cue_execution.py")
        );
        assert_eq!(runtime.ffmpeg, f.root.join("runtime/media/ffmpeg.exe"));
        fs::write(entry, b"changed physical adapter").unwrap();
        assert!(runtime.cue_execution().is_err());
    }

    #[test]
    fn releases_without_physical_cue_adapter_require_explicit_compatibility() {
        let f = fixture();
        let mut runtime = AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        runtime
            .inventory
            .remove("app/engine/dubflow/review/cue_execution.py");
        assert!(runtime.cue_execution().is_err());
        assert!(!f.root.join("unprovisioned-models").exists());
    }

    #[test]
    fn private_mix_entry_requires_original_inventory_and_no_model_provisioning() {
        let f = fixture();
        let mut runtime = AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        let entry = runtime.mix_execution().unwrap();
        assert_eq!(
            entry,
            f.root.join("app/engine/dubflow/review/mix_execution.py")
        );
        assert!(!f.root.join("unprovisioned-models").exists());
        fs::write(entry, b"changed private mix adapter").unwrap();
        assert!(runtime.mix_execution().is_err());
        runtime
            .inventory
            .remove("app/engine/dubflow/review/mix_execution.py");
        assert!(runtime.mix_execution().is_err());
    }

    #[cfg(windows)]
    #[test]
    fn canonical_original_windows_root_matches_its_plain_configured_spelling() {
        let f = fixture();
        let mut producer = f.producer.clone();
        producer["runtime_root"] = f
            .root
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .into_owned()
            .into();
        assert!(AdmittedRuntime::from_producer(&producer, &f.root).is_ok());
    }

    #[test]
    fn changed_runtime_root_release_fingerprint_and_legacy_missing_inventory_refuse() {
        let f = fixture();
        for case in 0..4 {
            let mut producer = f.producer.clone();
            match case {
                0 => {
                    producer["runtime_root"] =
                        f.root.join("other").to_string_lossy().into_owned().into()
                }
                1 => {
                    producer["release_manifest"]["sha256"] =
                        format!("sha256:{}", "b".repeat(64)).into()
                }
                2 => producer["model_manifests"] = json!([]),
                _ => producer["release_manifest"] = Value::Null,
            }
            assert!(
                AdmittedRuntime::from_producer(&producer, &f.root).is_err(),
                "case{case}"
            );
        }
    }

    #[test]
    fn replaced_same_size_import_and_extra_or_missing_modules_refuse_before_python() {
        let f = fixture();
        let path = f.root.join("runtime/Lib/site.py");
        let original = fs::read(&path).unwrap();
        let mut corrupt = original.clone();
        corrupt[0] = b'!';
        fs::write(&path, &corrupt).unwrap();
        assert!(AdmittedRuntime::from_producer(&f.producer, &f.root).is_err());
        fs::write(&path, &original).unwrap();
        let foreign = f.root.join("runtime/Lib/foreign.py");
        fs::write(&foreign, "# unexpected executable module").unwrap();
        assert!(AdmittedRuntime::from_producer(&f.producer, &f.root).is_err());
        fs::remove_file(foreign).unwrap();
        fs::remove_file(&path).unwrap();
        assert!(AdmittedRuntime::from_producer(&f.producer, &f.root).is_err());
    }

    #[test]
    fn final_inventory_rejects_post_hash_missing_extra_and_resized_files() {
        let f = fixture();
        final_inventory(&f.root, &f.inventory).unwrap();
        let path = f.root.join("runtime/Lib/site.py");
        let raw = fs::read(&path).unwrap();
        fs::write(&path, "resized").unwrap();
        assert!(final_inventory(&f.root, &f.inventory).is_err());
        fs::write(&path, &raw).unwrap();
        fs::remove_file(&path).unwrap();
        assert!(final_inventory(&f.root, &f.inventory).is_err());
        fs::write(&path, &raw).unwrap();
        fs::write(f.root.join("app/foreign.py"), "new").unwrap();
        assert!(final_inventory(&f.root, &f.inventory).is_err());
    }

    #[test]
    fn nested_import_walk_hashes_every_file_and_keeps_the_final_fence() {
        let mut f = fixture();
        for index in 0..192 {
            let tree = if index % 2 == 0 { "app" } else { "runtime" };
            let name = format!("{tree}/nested-{}/deep/module-{index}.py", index % 7);
            let raw = format!("# original import {index:04}\n").into_bytes();
            let path = f.root.join(&name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, &raw).unwrap();
            f.inventory.insert(name, pin(&raw));
        }
        let records: Vec<_> = f
            .inventory
            .iter()
            .map(|(name, pin)| {
                json!({"path":name,"sha256":&pin["sha256"].as_str().unwrap()[7..],
                "size_bytes":pin["size_bytes"].as_u64().unwrap().to_string()})
            })
            .collect();
        let raw = json!({"schema_version":1,"artifacts":records}).to_string();
        fs::write(f.root.join("release-manifest.json"), &raw).unwrap();
        f.producer["release_manifest"] = pin(raw.as_bytes());
        AdmittedRuntime::from_producer(&f.producer, &f.root).unwrap();
        let observed = walk_import_files(&f.root, "test import fence", |path, name, progress| {
            let pin = f.inventory.get(name).unwrap();
            if pin["size_bytes"] == 0 {
                assert_eq!(regular(path)?.metadata()?.len(), 0);
            } else {
                fingerprint_before(path, pin, false, 1024 * 1024, Some(progress))?;
            }
            Ok(pin["size_bytes"].as_u64().unwrap())
        })
        .unwrap();
        assert_eq!(observed.len(), f.inventory.len());
        let path = f.root.join("runtime/nested-2/deep/module-191.py");
        let original = fs::read(&path).unwrap();
        let mut changed = original.clone();
        changed[0] ^= 1;
        fs::write(&path, changed).unwrap();
        assert!(AdmittedRuntime::from_producer(&f.producer, &f.root).is_err());
        fs::write(&path, original).unwrap();
        final_inventory(&f.root, &f.inventory).unwrap();
        fs::remove_file(path).unwrap();
        assert!(final_inventory(&f.root, &f.inventory).is_err());
    }

    #[test]
    fn parallel_import_admission_is_bounded_and_verifies_every_original_file_once() {
        use std::sync::{Condvar, Mutex};
        let f = fixture();
        let seen = Mutex::new(HashSet::new());
        let gate = (Mutex::new(0usize), Condvar::new());
        let active = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let observed = walk_import_files(&f.root, "parallel test", |path, name, progress| {
            let running = active.fetch_add(1, Ordering::SeqCst) + 1;
            peak.fetch_max(running, Ordering::SeqCst);
            // A bounded rendezvous proves multiple files really overlap. It
            // cannot hang CI if an accidental serial regression is introduced.
            let mut arrived = gate.0.lock().unwrap();
            if *arrived < IMPORT_WORKERS {
                *arrived += 1;
                gate.1.notify_all();
                while *arrived < IMPORT_WORKERS {
                    let (next, timeout) = gate
                        .1
                        .wait_timeout(arrived, Duration::from_secs(10))
                        .unwrap();
                    arrived = next;
                    if timeout.timed_out() && *arrived < IMPORT_WORKERS {
                        return Err(invalid("parallel verification did not overlap"));
                    }
                }
            }
            drop(arrived);
            let pin = &f.inventory[name];
            if pin["size_bytes"] == 0 {
                assert_eq!(regular(path)?.metadata()?.len(), 0);
                assert_eq!(pin["sha256"], hash(&[]));
            } else {
                fingerprint_before(path, pin, false, 1024 * 1024, Some(progress))?;
            }
            assert!(seen.lock().unwrap().insert(name.to_string()));
            active.fetch_sub(1, Ordering::SeqCst);
            Ok(pin["size_bytes"].as_u64().unwrap())
        })
        .unwrap();
        assert_eq!(peak.load(Ordering::SeqCst), IMPORT_WORKERS);
        assert_eq!(active.load(Ordering::SeqCst), 0);
        assert_eq!(observed, *seen.lock().unwrap());
        assert_eq!(observed, f.inventory.keys().cloned().collect());
        final_inventory(&f.root, &f.inventory).unwrap();
    }

    #[test]
    fn import_deadline_covers_enumeration_and_inflight_full_hash_reads() {
        let f = fixture();
        let now = Instant::now();
        let calls = AtomicUsize::new(0);
        let error = walk_import_files_before(&f.root, "expired import", now, now, |_, _, _| {
            calls.fetch_add(1, Ordering::Relaxed);
            Ok(0)
        })
        .unwrap_err();
        assert!(error.to_string().contains("expired import exceeded"));
        assert!(error.to_string().contains("files=0, bytes=0"));
        assert_eq!(calls.load(Ordering::Relaxed), 0);

        let progress = ImportProgress {
            phase: "expired shared hash",
            started: now,
            deadline: now,
            files: AtomicUsize::new(7),
            bytes: AtomicU64::new(31),
        };
        let error = fingerprint_before(
            &f.root.join("runtime/python.exe"),
            &f.inventory["runtime/python.exe"],
            false,
            1024,
            Some(&progress),
        )
        .unwrap_err();
        assert!(error.to_string().contains("expired shared hash exceeded"));
        assert!(error.to_string().contains("files=7, bytes=31"));

        // Completing a callback after its common deadline never yields an
        // admitted partial inventory, even if the callback returns success.
        let started = Instant::now();
        let deadline = started + Duration::from_secs(1);
        let calls = AtomicUsize::new(0);
        let error = walk_import_files_before(
            &f.root,
            "late completion",
            started,
            deadline,
            |path, _, _| {
                calls.fetch_add(1, Ordering::Relaxed);
                std::thread::sleep(
                    deadline.saturating_duration_since(Instant::now()) + Duration::from_millis(1),
                );
                Ok(regular(path)?.metadata()?.len())
            },
        )
        .unwrap_err();
        assert!(calls.load(Ordering::Relaxed) > 0);
        assert!(error.to_string().contains("late completion exceeded"));
    }

    #[test]
    fn failed_or_panicked_parallel_verifier_never_admits_partial_inventory() {
        let f = fixture();
        for panic in [false, true] {
            let error = walk_import_files(&f.root, "failed verifier", |path, name, _| {
                if name == "runtime/python.exe" {
                    if panic {
                        panic!("injected verification panic");
                    }
                    return Err(invalid("injected verification refusal"));
                }
                Ok(regular(path)?.metadata()?.len())
            })
            .unwrap_err();
            assert!(error.to_string().contains(if panic {
                "producer inventory verifier panicked"
            } else {
                "injected verification refusal"
            }));
        }
        final_inventory(&f.root, &f.inventory).unwrap();
    }

    #[cfg(any(unix, windows))]
    #[test]
    fn linked_import_directory_refuses_even_with_identical_pinned_files() {
        let f = fixture();
        // cmd's mklink treats a forward slash as an option even inside a path.
        let original = f.root.join("runtime").join("Lib");
        let saved = f.root.join("saved-import-directory");
        fs::rename(&original, &saved).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&saved, &original).unwrap();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let output = std::process::Command::new("cmd.exe")
                .args(["/d", "/c", "mklink", "/J"])
                .arg(&original)
                .arg(&saved)
                .creation_flags(0x08000000)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "fixture junction creation failed: status={:?}, stdout={}, stderr={}",
                output.status.code(),
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
        }
        let admission_refused = AdmittedRuntime::from_producer(&f.producer, &f.root).is_err();
        let final_refused = final_inventory(&f.root, &f.inventory).is_err();
        #[cfg(unix)]
        fs::remove_file(&original).unwrap();
        #[cfg(windows)]
        fs::remove_dir(&original).unwrap();
        fs::rename(saved, original).unwrap();
        assert!(admission_refused);
        assert!(final_refused);
    }

    #[test]
    fn ambiguous_release_paths_and_changed_job_manifest_roster_require_compatibility() {
        let f = fixture();
        let mut release: Value =
            serde_json::from_slice(&fs::read(f.root.join("release-manifest.json")).unwrap())
                .unwrap();
        let duplicate = release["artifacts"][0].clone();
        release["artifacts"].as_array_mut().unwrap().push(duplicate);
        let raw = release.to_string();
        fs::write(f.root.join("release-manifest.json"), &raw).unwrap();
        let mut producer = f.producer.clone();
        producer["release_manifest"] = pin(raw.as_bytes());
        assert!(AdmittedRuntime::from_producer(&producer, &f.root).is_err());
        let g = fixture();
        let mut producer = g.producer.clone();
        producer["model_manifests"].as_array_mut().unwrap().pop();
        assert!(AdmittedRuntime::from_producer(&producer, &g.root).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn linked_import_is_refused_even_when_bytes_equal_the_original_pin() {
        use std::os::unix::fs::symlink;
        let f = fixture();
        let path = f.root.join("runtime/Lib/site.py");
        let saved = f.root.join("saved.py");
        fs::write(&saved, fs::read(&path).unwrap()).unwrap();
        fs::remove_file(&path).unwrap();
        symlink(&saved, &path).unwrap();
        assert!(AdmittedRuntime::from_producer(&f.producer, &f.root).is_err());
        assert!(final_inventory(&f.root, &f.inventory).is_err());
    }
}
