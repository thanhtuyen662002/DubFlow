//! Explicit metadata-only split authority. No v1 fallback, model load or publication.
//! Both complete releases are admitted afresh under the ORIGINAL caller deadline.
use super::{closed, decode, hash, invalid, plain, regular, relative, release_inventory,
    valid_hash, ImportProgress, MANIFEST_LIMIT, MAX_FILES, IMPORT_DEADLINE};
use super::super::{SupervisorResult, review_assets::native_identity};
use dubflow_job_state::{DurableStore, review_graph::{ReviewGraphState, ReviewToken}};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize};
use std::time::Instant;

const DECLARATION: &str = "app/engine/dubflow/review/processing-original-runtime-v2.json";
const DISPATCH: &str = "app/engine/dubflow/review/original_runtime_execution_v2.py";
const DOMAIN: &[u8] = b"review-original-runtime-processing-app-v2\0";
const HELPERS: [&str; 7] = [
    "app/engine/dubflow/worker/b2_audio.py",
    "app/engine/dubflow/worker/production_job.py",
    "app/engine/dubflow/worker/tts_checkpoints.py",
    "app/engine/dubflow/worker/tts_rewrites.py",
    "app/engine/dubflow/worker/export_publication.py",
    "app/engine/__init__.py", "app/engine/dubflow/__init__.py",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum SplitAction { PrepareEffectiveCue, PrepareEffectiveTimeline }
impl SplitAction {
    pub(super) fn name(self) -> &'static str {
        match self {
            Self::PrepareEffectiveCue => "prepare_effective_cue",
            Self::PrepareEffectiveTimeline => "prepare_effective_timeline",
        }
    }
    pub(super) fn parse(name: &str) -> SupervisorResult<Self> {
        match name {
            "prepare_effective_cue" => Ok(Self::PrepareEffectiveCue),
            "prepare_effective_timeline" => Ok(Self::PrepareEffectiveTimeline),
            _ => Err(invalid("unported split action cannot be launched")),
        }
    }
}
#[derive(Clone, Copy, Debug)]
pub(super) enum SplitSourceRole { OriginalRuntime, ProcessingApp }
impl SplitSourceRole {
    fn name(self) -> &'static str {
        match self { Self::OriginalRuntime => "original_runtime", Self::ProcessingApp => "processing_app" }
    }
}
macro_rules! role {
    ($name:ident) => {
        #[derive(Clone, Debug)]
        pub(super) struct $name { path: PathBuf }
        impl $name { pub(super) fn path(&self) -> &Path { &self.path } }
    }
}
role!(OriginalRuntimeRootV2); role!(OriginalPythonV2); role!(OriginalMediaRootV2);
role!(OriginalMediaExecutableV2); role!(OriginalModelRootV2);
role!(ProcessingAppRootV2); role!(ProcessingProfileV2); role!(ProcessingEntryV2);

/// Constructed only by the native installed source selector, never deserialized
/// from a browser request. These values are inputs, not an admission token.
pub(super) struct SplitSelectionV2 {
    root: PathBuf, manifest_pin: Value, source_sha: String, native_actions: Vec<SplitAction>,
}
impl SplitSelectionV2 {
    pub(super) fn new(root: PathBuf, manifest_pin: Value, source_sha: String,
        native_actions: Vec<SplitAction>) -> SupervisorResult<Self> {
        if !root.is_absolute() || root.components().any(|p| matches!(p, std::path::Component::ParentDir))
            || !matches!(source_sha.len(), 40 | 64)
            || !source_sha.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || native_actions.is_empty() || native_actions.len() > 2
            || native_actions.iter().map(|a| a.name()).collect::<HashSet<_>>().len() != native_actions.len() {
            return Err(invalid("invalid native split source selection"));
        }
        Ok(Self { root, manifest_pin, source_sha, native_actions })
    }
}
pub(super) fn split_capability_declaration_v2() -> Value {
    json!({"schema_version":2,"kind":"review_processing_original_runtime_code_capability",
        "route":"original_python_processing_app_v2",
        "capabilities":["prepare_effective_cue","prepare_effective_timeline"],
        "dispatcher":"engine/dubflow/review/original_runtime_execution_v2.py",
        "launch_binding_format":"review_native_split_launch_binding_v2",
        "result_format":"review_split_processing_result_v2",
        "protected_policy":"complete_resolved_seven_prefix_equality_v2",
        "original_helper_policy":"exact_original_tts_recipe_helpers_v2",
        "model_provisioning":false,"original_job_binding_changed":false,"active_graph_publication":false})
}
fn encoded(value: &Value) -> SupervisorResult<Vec<u8>> {
    let mut raw = String::new();
    super::super::review_document::canonical(value, &mut raw)?;
    Ok(raw.into_bytes())
}
fn same(a: &Value, b: &Value) -> SupervisorResult<bool> { Ok(encoded(a)? == encoded(b)?) }
pub(super) fn split_execution_recipe_v2(binding: &Value) -> SupervisorResult<String> {
    let mut value = binding.clone();
    value.as_object_mut().ok_or_else(|| invalid("split recipe must be an object"))?
        .remove("execution_recipe_sha256");
    let mut bytes = DOMAIN.to_vec();
    bytes.extend_from_slice(&encoded(&value)?);
    Ok(hash(&bytes))
}
fn token_value(token: &ReviewToken) -> Value {
    json!({"job_id":token.job_id,"revision":token.revision.to_string(),
        "snapshot_sha256":token.snapshot_sha256,"graph_sha256":token.graph_sha256,
        "original_manifest_sha256":token.original_manifest_sha256})
}
fn check(deadline: Instant) -> SupervisorResult<()> {
    if Instant::now() >= deadline { return Err(invalid("original split operation deadline expired")); }
    Ok(())
}
fn inventory_root_file(root: &Path) -> SupervisorResult<std::fs::File> {
    plain(root)?;
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.access_mode(0x80).custom_flags(0x02000000 | 0x00200000);
    }
    let file = options.open(root)?;
    plain(root)?;
    if !file.metadata()?.is_dir() { return Err(invalid("split root is not a directory")); }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if file.metadata()?.file_attributes() & 0x400 != 0 {
            return Err(invalid("split directory handle is a reparse point"));
        }
    }
    Ok(file)
}
fn protected(inventory: &HashMap<String, Value>) -> BTreeMap<String, Value> {
    inventory.iter().filter(|(name, _)| super::protected_processing_path(name))
        .map(|(name, pin)| (name.clone(), pin.clone())).collect()
}
fn compatible_maps(original: &HashMap<String, Value>, processing: &HashMap<String, Value>)
    -> SupervisorResult<(Value, Value)> {
    let expected = protected(original);
    let mut resolved = protected(processing);
    // Fixed policy, no caller prefix selector: original runtime is the only runtime authority.
    resolved.retain(|name, _| !name.starts_with("runtime/"));
    for (name, pin) in &expected {
        if name.starts_with("runtime/") { resolved.insert(name.clone(), pin.clone()); }
    }
    if resolved != expected { return Err(invalid("split protected app map differs from original")); }
    let mut helpers = BTreeMap::new();
    for name in HELPERS {
        let original_pin = original.get(name).ok_or_else(|| invalid("original split helper missing"))?;
        if processing.get(name) != Some(original_pin) { return Err(invalid("split original helper changed")); }
        helpers.insert(name, original_pin.clone());
    }
    Ok((json!(resolved), json!(helpers)))
}

/// Full independently self-pinned release, with no fabricated original producer.
struct FullyAdmittedRelease {
    root: PathBuf, manifest_pin: Value, manifest_raw: Vec<u8>, source: String,
    inventory: HashMap<String, Value>, identities: BTreeMap<String, (u64, u64)>,
    root_identity: (u64, u64),
    executable_flags: BTreeMap<String, bool>,
    ancestor_identities: BTreeMap<PathBuf, (u64, u64)>,
}
impl FullyAdmittedRelease {
    fn read(root: &Path, manifest_pin: &Value, expected_source: Option<&str>,
        deadline: Instant) -> SupervisorResult<Self> {
        check(deadline)?; plain(root)?;
        let root_file = inventory_root_file(root)?;
        let root_identity = native_identity(&root_file, true)?;
        let mut ancestor_identities = BTreeMap::new();
        for ancestor in root.ancestors() {
            check(deadline)?;
            let directory = inventory_root_file(ancestor)?;
            ancestor_identities.insert(ancestor.to_owned(), native_identity(&directory, true)?);
        }
        let progress = ImportProgress { phase:"split complete release", started:Instant::now(),
            deadline, files:AtomicUsize::new(0), bytes:AtomicU64::new(0) };
        let manifest_raw = super::fingerprint_before(&root.join("release-manifest.json"),
            manifest_pin, true, MANIFEST_LIMIT, Some(&progress))?;
        let text = std::str::from_utf8(&manifest_raw).map_err(|_| invalid("split release UTF8"))?;
        let release = decode(text, MANIFEST_LIMIT as usize)?;
        let source = release["source_sha"].as_str().filter(|s| matches!(s.len(),40|64)
            && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
            .ok_or_else(|| invalid("split release source invalid"))?.to_owned();
        if release["schema_version"].as_u64() != Some(1)
            || expected_source.is_some_and(|s| s != source) {
            return Err(invalid("split release source/format differs"));
        }
        let inventory = release_inventory(&release)?;
        let mut executable_flags = BTreeMap::new();
        for row in release["artifacts"].as_array().unwrap() {
            let name = row["path"].as_str().unwrap();
            let executable = row["executable"].as_bool()
                .ok_or_else(|| invalid("split release executable flag missing"))?;
            executable_flags.insert(name.to_owned(), executable);
        }
        if inventory.contains_key("release-manifest.json") {
            return Err(invalid("self-referential split release manifest"));
        }
        let mut value = Self { root:root.to_owned(), manifest_pin:manifest_pin.clone(),
            manifest_raw, source, inventory, identities:BTreeMap::new(), root_identity, executable_flags,
            ancestor_identities };
        value.identities = value.verify_tree(deadline)?;
        check(deadline)?;
        Ok(value)
    }
    fn verify_tree(&self, deadline: Instant) -> SupervisorResult<BTreeMap<String,(u64,u64)>> {
        check(deadline)?; plain(&self.root)?;
        let root_file = inventory_root_file(&self.root)?;
        if native_identity(&root_file, true)? != self.root_identity {
            return Err(invalid("split release root identity changed"));
        }
        for (ancestor, expected) in &self.ancestor_identities {
            check(deadline)?;
            let directory = inventory_root_file(ancestor)?;
            if native_identity(&directory, true)? != *expected {
                return Err(invalid("split release ancestor identity changed"));
            }
        }
        let progress = ImportProgress { phase:"split complete physical inventory",
            started:Instant::now(), deadline, files:AtomicUsize::new(0), bytes:AtomicU64::new(0) };
        let mut directories = vec![(self.root.clone(),0usize)];
        let mut dirs = 0usize; let mut entries = 0usize; let mut seen = HashSet::new();
        let mut identities = BTreeMap::new();
        while let Some((directory, depth)) = directories.pop() {
            check(deadline)?; plain(&directory)?;
            dirs += 1;
            if dirs > 20000 || depth > 128 { return Err(invalid("split directory/depth bound")); }
            for entry in std::fs::read_dir(&directory)? {
                check(deadline)?; entries += 1;
                if entries > MAX_FILES + 20000 { return Err(invalid("split enumeration bound")); }
                let entry = entry?; let path = entry.path(); let kind = entry.file_type()?;
                if kind.is_dir() { directories.push((path, depth+1)); continue; }
                if !kind.is_file() { return Err(invalid("split release has nonregular entry")); }
                let name = path.strip_prefix(&self.root).map_err(|_| invalid("split root escaped"))?
                    .to_str().ok_or_else(|| invalid("split path UTF8"))?.replace('\\',"/");
                relative(&name)?;
                if !seen.insert(name.clone()) || seen.len() > MAX_FILES {
                    return Err(invalid("duplicate split release path"));
                }
                let expected = if name == "release-manifest.json" { &self.manifest_pin }
                    else { self.inventory.get(&name).ok_or_else(|| invalid("extra split release file"))? };
                let file = regular(&path)?; let identity = native_identity(&file, false)?;
                if expected["size_bytes"].as_u64() == Some(0) {
                    if file.metadata()?.len() != 0 || expected["sha256"] != hash(&[]) {
                        return Err(invalid("empty split release file changed"));
                    }
                } else {
                    super::fingerprint_before(&path, expected, false,
                        8*1024*1024*1024, Some(&progress))?;
                }
                let fresh = regular(&path)?;
                if native_identity(&fresh, false)? != identity {
                    return Err(invalid("split release file identity changed during hash"));
                }
                identities.insert(name, identity);
            }
        }
        if seen.len() != self.inventory.len()+1 || self.inventory.keys().any(|n| !seen.contains(n)) {
            return Err(invalid("split complete release file missing"));
        }
        let final_raw = super::fingerprint_before(&self.root.join("release-manifest.json"),
            &self.manifest_pin,true,MANIFEST_LIMIT,Some(&progress))?;
        if final_raw != self.manifest_raw { return Err(invalid("split manifest changed")); }
        for (ancestor, expected) in &self.ancestor_identities {
            check(deadline)?;
            if native_identity(&inventory_root_file(ancestor)?, true)? != *expected {
                return Err(invalid("split release ancestor changed during full hash"));
            }
        }
        check(deadline)?;
        Ok(identities)
    }
    fn fresh(&self, deadline: Instant) -> SupervisorResult<()> {
        if self.verify_tree(deadline)? != self.identities {
            return Err(invalid("split full release identities changed"));
        }
        Ok(())
    }
    fn pin(&self, name: &str) -> SupervisorResult<&Value> {
        self.inventory.get(name).ok_or_else(|| invalid("split dependency missing"))
    }
    fn read_document(&self, name: &str, limit: u64, deadline: Instant) -> SupervisorResult<Vec<u8>> {
        let progress = ImportProgress { phase:"split metadata",started:Instant::now(),deadline,
            files:AtomicUsize::new(0),bytes:AtomicU64::new(0) };
        super::fingerprint_before(&self.root.join(name),self.pin(name)?,true,limit,Some(&progress))
    }
    fn descriptor(&self,name:&str)->SupervisorResult<Value> {
        Ok(json!({"path":self.root.join(name),"pin":self.pin(name)?}))
    }
    fn described_map(&self, names: impl Iterator<Item=String>) -> SupervisorResult<Value> {
        let mut map = BTreeMap::new();
        for name in names {
            map.insert(name.clone(), json!({"pin":self.pin(&name)?,
                "executable":self.executable_flags.get(&name)
                    .ok_or_else(|| invalid("split release executable metadata missing"))?}));
        }
        Ok(json!(map))
    }
}

/// No generic runtime()/root swapping. This object cannot be constructed from
/// a saved receipt or browser roots. Fresh native state + both whole trees own authority.
pub(super) struct AdmittedSplitProcessingV2 {
    original: FullyAdmittedRelease, processing: FullyAdmittedRelease,
    request_raw: String, producer: Value, expected: ReviewToken, deadline: Instant,
    model_root: OriginalModelRootV2, ffmpeg_name: String, ffprobe_name: String,
    protected_map: Value, helper_map: Value, catalog_hash: String, native_actions: Vec<SplitAction>,
}
impl AdmittedSplitProcessingV2 {
    pub(super) fn read(store: &DurableStore, state: &ReviewGraphState,
        selection: SplitSelectionV2, operation_deadline: Instant) -> SupervisorResult<Self> {
        let start=Instant::now();
        // Every subsequent method keeps this SAME shorter deadline; no per-action refresh.
        let deadline = operation_deadline.min(start + IMPORT_DEADLINE);
        check(deadline)?;
        if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
            return Err(invalid("split admission current graph changed"));
        }
        let request_raw=store.job_start_request(&state.token.job_id)?
            .ok_or_else(|| invalid("split original immutable request missing"))?;
        let request=decode(&request_raw,64*1024*1024)?;
        if request["schema_version"].as_u64()!=Some(1) { return Err(invalid("split job request format")); }
        let producer=request["producer"].clone();
        closed(&producer,&["runtime_root","model_root","worker","python","ffmpeg","ffprobe",
            "release_manifest","model_manifests"])?;
        let root=super::super::absolute_path(producer["runtime_root"].as_str()
            .ok_or_else(|| invalid("original split runtime root missing"))?,"split original runtime root")?;
        let original=FullyAdmittedRelease::read(&root,&producer["release_manifest"],None,deadline)?;
        let processing=FullyAdmittedRelease::read(&selection.root,&selection.manifest_pin,
            Some(&selection.source_sha),deadline)?;
        if root.canonicalize()?==selection.root.canonicalize()? {
            return Err(invalid("split code release must be independently admitted"));
        }
        for (field,name) in [("python","runtime/python.exe"),("worker","app/engine/dubflow/worker/production_job.py")] {
            if !same(original.pin(name)?,&producer[field])? {
                return Err(invalid("original split executable differs from saved producer"));
            }
        }
        let media_name=|role:&str,exe:&str|->SupervisorResult<String>{
            for name in [format!("runtime/media/{exe}"),format!("runtime/ffmpeg/{exe}")] {
                if original.root.join(&name).exists() {
                    if !same(original.pin(&name)?,&producer[role])? {
                        return Err(invalid("original split media pin changed"));
                    }
                    return Ok(name);
                }
            }
            Err(invalid("original split media unavailable"))
        };
        let ffmpeg_name=media_name("ffmpeg","ffmpeg.exe")?;
        let ffprobe_name=media_name("ffprobe","ffprobe.exe")?;
        let model_root=OriginalModelRootV2 { path:super::super::absolute_path(
            producer["model_root"].as_str().ok_or_else(|| invalid("original split model root missing"))?,
            "split original model root")? };
        let manifests=producer["model_manifests"].as_array()
            .filter(|v| !v.is_empty() && v.len()<=64).ok_or_else(|| invalid("split model roster missing"))?;
        let mut roster=HashSet::new();
        for row in manifests {
            let pair=row.as_array().filter(|r|r.len()==2).ok_or_else(|| invalid("split model tuple"))?;
            let name=pair[0].as_str().ok_or_else(||invalid("split model name"))?;
            relative(name)?;
            if name.contains('/') || !name.ends_with(".json") || !roster.insert(name.to_owned())
                || !same(original.pin(&format!("app/models/manifests/{name}"))?,&pair[1])? {
                return Err(invalid("split original model roster changed"));
            }
        }
        for name in original.inventory.keys().filter_map(|n|n.strip_prefix("app/models/manifests/")) {
            if name.ends_with(".json") && !roster.contains(name) {
                return Err(invalid("split original model roster extra"));
            }
        }
        compatible_maps(&original.inventory,&processing.inventory)?;
        let protected_names = protected(&original.inventory).into_keys().collect::<Vec<_>>();
        for name in protected_names.iter().filter(|n| !n.starts_with("runtime/"))
            .map(String::as_str).chain(HELPERS) {
            if original.executable_flags.get(name) != processing.executable_flags.get(name) {
                return Err(invalid("split protected/helper executable metadata changed"));
            }
        }
        let protected_map = original.described_map(protected_names.into_iter())?;
        let helper_map = original.described_map(HELPERS.into_iter().map(str::to_owned))?;
        let profile_raw=original.read_document("app/models/manifests/production-cpu-v1.json",512*1024,deadline)?;
        let profile=decode(std::str::from_utf8(&profile_raw).map_err(|_|invalid("split profile UTF8"))?,512*1024)?;
        let recipe=profile["tts_neural_profile"].as_str().ok_or_else(||invalid("split catalog selection"))?;
        relative(recipe)?;
        let catalog_raw=original.read_document(&format!("app/{recipe}"),512*1024,deadline)?;
        let declaration=processing.read_document(DECLARATION,65536,deadline)?;
        if !same(&decode(std::str::from_utf8(&declaration).map_err(|_|invalid("split declaration UTF8"))?,65536)?,
            &split_capability_declaration_v2())? {
            return Err(invalid("split capability declaration requires explicit compatibility"));
        }
        processing.pin(DISPATCH)?;
        let value=Self { original,processing,request_raw,producer,expected:state.token.clone(),deadline,
            model_root,ffmpeg_name,ffprobe_name,protected_map,helper_map,catalog_hash:hash(&catalog_raw),
            native_actions:selection.native_actions };
        value.fresh(store)?;
        Ok(value)
    }
    fn state_fence(&self, store:&DurableStore)->SupervisorResult<()> {
        check(self.deadline)?;
        let state=store.review_graph_state(&self.expected.job_id)?
            .ok_or_else(|| invalid("split current graph missing"))?;
        if state.token!=self.expected
            || store.job_start_request(&self.expected.job_id)?.as_deref()!=Some(self.request_raw.as_str()) {
            return Err(invalid("split original request/current token changed"));
        }
        Ok(())
    }
    pub(super) fn fresh(&self,store:&DurableStore)->SupervisorResult<()> {
        self.state_fence(store)?;
        self.original.fresh(self.deadline)?;
        self.processing.fresh(self.deadline)?;
        self.state_fence(store)
    }
    pub(super) fn original_runtime_root(&self)->OriginalRuntimeRootV2 {
        OriginalRuntimeRootV2 {path:self.original.root.clone()}
    }
    pub(super) fn original_python(&self)->OriginalPythonV2 {
        OriginalPythonV2 {path:self.original.root.join("runtime/python.exe")}
    }
    pub(super) fn original_media_root(&self)->OriginalMediaRootV2 {
        OriginalMediaRootV2 {path:self.original.root.join("runtime")}
    }
    pub(super) fn original_ffmpeg(&self)->OriginalMediaExecutableV2 {
        OriginalMediaExecutableV2 {path:self.original.root.join(&self.ffmpeg_name)}
    }
    pub(super) fn original_ffprobe(&self)->OriginalMediaExecutableV2 {
        OriginalMediaExecutableV2 {path:self.original.root.join(&self.ffprobe_name)}
    }
    pub(super) fn original_model_root(&self)->&OriginalModelRootV2 { &self.model_root }
    pub(super) fn processing_app_root(&self)->ProcessingAppRootV2 {
        ProcessingAppRootV2 {path:self.processing.root.join("app")}
    }
    pub(super) fn original_compatible_profile(&self)->ProcessingProfileV2 {
        ProcessingProfileV2 {path:self.processing.root.join("app/models/manifests/production-cpu-v1.json")}
    }
    pub(super) fn entry_for(&self,action:SplitAction)->SupervisorResult<ProcessingEntryV2> {
        if !self.native_actions.contains(&action) { return Err(invalid("native split action not ported")); }
        Ok(ProcessingEntryV2 {path:self.processing.root.join(DISPATCH)})
    }
    fn roles(&self)->SupervisorResult<Value> {
        Ok(json!({"original_runtime_root":self.original.root,
            "original_python":self.original.descriptor("runtime/python.exe")?,
            "processing_app_root":self.processing.root.join("app"),
            "original_model_root":self.model_root.path,
            "original_media_root":self.original.root.join("runtime"),
            "ffmpeg":self.original.descriptor(&self.ffmpeg_name)?,
            "ffprobe":self.original.descriptor(&self.ffprobe_name)?,
            "profile":self.processing.descriptor("app/models/manifests/production-cpu-v1.json")?}))
    }
    pub(super) fn fresh_authority_receipt(&self,store:&DurableStore)->SupervisorResult<Value> {
        self.fresh(store)?;
        Ok(json!({"schema_version":2,"kind":"review_admitted_processing_release_v2",
            "route":"original_python_processing_app_v2","capability_document":self.processing.pin(DECLARATION)?,
            "source_sha":self.processing.source,"processing_root":self.processing.root,
            "release_manifest":self.processing.manifest_pin,
            "original_producer_sha256":hash(&encoded(&self.producer)?),
            "original_release_sha256":hash(&self.original.manifest_raw),
            "original_runtime_root":self.original.root,"original_python_pin":self.original.pin("runtime/python.exe")?,
            "processing_app_root":self.processing.root.join("app"),
            "processing_app_inventory_sha256":hash(&encoded(&self.processing.described_map(
                self.processing.inventory.keys().filter(|n|n.starts_with("app/")).cloned())?)?),
            "original_model_root":self.model_root.path,
            "original_profile_pin":self.original.pin("app/models/manifests/production-cpu-v1.json")?,
            "original_catalog_sha256":self.catalog_hash,"roles":self.roles()?,
            "original_protected_inventory_sha256":hash(&encoded(&self.protected_map)?),
            "resolved_protected_inventory_sha256":hash(&encoded(&self.protected_map)?),
            "original_helper_inventory_sha256":hash(&encoded(&self.helper_map)?),
            "launch_policy_sha256":hash(&encoded(&split_capability_declaration_v2())?),
            "original_job_binding_changed":false,"model_weights_verified":false,"active_graph_published":false}))
    }
    fn dependencies(&self,action:SplitAction)->SupervisorResult<Value> {
        self.entry_for(action)?;
        let mut names=vec![(SplitSourceRole::OriginalRuntime,"runtime/python.exe"),
            (SplitSourceRole::ProcessingApp,"engine/dubflow/review/original_runtime_execution_v2.py")];
        names.extend(match action {
            SplitAction::PrepareEffectiveCue=>vec![
                (SplitSourceRole::ProcessingApp,"engine/dubflow/review/effective_cue_execution.py"),
                (SplitSourceRole::ProcessingApp,"engine/dubflow/review/effective_cue_plan.py")],
            SplitAction::PrepareEffectiveTimeline=>vec![
                (SplitSourceRole::ProcessingApp,"engine/dubflow/review/effective_timeline_execution.py"),
                (SplitSourceRole::ProcessingApp,"engine/dubflow/review/corrected_timeline.py")],
        });
        let mut rows=vec![];
        for (role,name) in names {
            let pin=match role { SplitSourceRole::OriginalRuntime=>self.original.pin(name)?,
                SplitSourceRole::ProcessingApp=>self.processing.pin(&format!("app/{name}"))? };
            rows.push(json!({"role":role.name(),"relative_path":name,"pin":pin}));
        }
        Ok(json!(rows))
    }
    /// Build ONLY after the caller has persisted/adopted the complete actual
    /// wrapper context. The caller must retain its descriptor/owned bytes through
    /// spawn and revalidate returned semantics; this method grants no byte gate.
    pub(super) fn launch_binding(&self,store:&DurableStore,action:SplitAction,
        context_path:&Path,context_raw:&[u8],authority_raw:&[u8])->SupervisorResult<Value> {
        check(self.deadline)?;
        if context_raw.is_empty() || context_raw.len()>64*1024*1024
            || authority_raw.is_empty() || authority_raw.len()>MANIFEST_LIMIT as usize {
            return Err(invalid("split launch complete packet byte bound"));
        }
        let authority=decode(std::str::from_utf8(authority_raw).map_err(|_|invalid("split authority UTF8"))?,
            MANIFEST_LIMIT as usize)?;
        if !same(&authority,&self.fresh_authority_receipt(store)?)? {
            return Err(invalid("split authority differs from actual fresh admission"));
        }
        let context=decode(std::str::from_utf8(context_raw).map_err(|_|invalid("split context UTF8"))?,64*1024*1024)?;
        closed(&context,&["schema_version","kind","action","current_token","context_json","bindings_json"])?;
        if context["schema_version"].as_u64()!=Some(2) || context["kind"]!="review_split_action_context_v2"
            || context["action"]!=action.name() || !same(&context["current_token"],&token_value(&self.expected))? {
            return Err(invalid("split launch substituted action/current token"));
        }
        let inner_text=context["context_json"].as_str().filter(|s|!s.is_empty() && s.len()<=64*1024*1024)
            .ok_or_else(||invalid("split inner context must retain exact bytes"))?;
        let inner=decode(inner_text,64*1024*1024)?;
        let token_key=match action {SplitAction::PrepareEffectiveCue=>"before_token",
            SplitAction::PrepareEffectiveTimeline=>"current_token"};
        if !same(&inner[token_key],&context["current_token"])? {
            return Err(invalid("split inner metadata substituted current token"));
        }
        match action {
            SplitAction::PrepareEffectiveCue=>{
                let raw=context["bindings_json"].as_str().filter(|s|!s.is_empty()&&s.len()<=64*1024*1024)
                    .ok_or_else(||invalid("split cue audio bindings missing"))?;
                let bindings=decode(raw,64*1024*1024)?;
                if !same(&bindings["current_token"],&context["current_token"])? {
                    return Err(invalid("split audio token substituted"));
                }
            },
            SplitAction::PrepareEffectiveTimeline=>if !context["bindings_json"].is_null() {
                return Err(invalid("split timeline audio binding unexpected"));
            },
        }
        let context_pin=json!({"sha256":hash(context_raw),"size_bytes":context_raw.len()});
        let progress=ImportProgress {phase:"actual split context",started:Instant::now(),deadline:self.deadline,
            files:AtomicUsize::new(0),bytes:AtomicU64::new(0)};
        let actual=super::fingerprint_before(context_path,&context_pin,true,64*1024*1024,Some(&progress))?;
        if actual!=context_raw {return Err(invalid("split context physical bytes differ"));}
        let mut binding=json!({"schema_version":2,"kind":"review_native_split_launch_binding_v2",
            "action":action.name(),"current_token":token_value(&self.expected),
            "context_pin":{"path":context_path,"pin":context_pin},
            "processing_authority_receipt_pin":{"sha256":hash(authority_raw),"size_bytes":authority_raw.len()},
            "original_producer_sha256":hash(&encoded(&self.producer)?),"roles":self.roles()?,
            "recipe_dependencies":self.dependencies(action)?});
        binding["execution_recipe_sha256"]=json!(split_execution_recipe_v2(&binding)?);
        self.state_fence(store)?;
        Ok(binding)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn maps()->(HashMap<String,Value>,HashMap<String,Value>){
        let mut a=HashMap::new();
        for p in HELPERS {a.insert(p.to_owned(),json!({"sha256":hash(p.as_bytes()),"size_bytes":p.len()}));}
        for p in ["runtime/python.exe","app/engine/dubflow/tts/native.py","app/models/a.json"] {
            a.insert(p.to_owned(),json!({"sha256":hash(p.as_bytes()),"size_bytes":p.len()}));
        }
        (a.clone(),a)
    }
    #[test]
    fn only_original_runtime_resolves_and_all_helpers_and_protected_apps_remain_exact(){
        let(a,mut b)=maps();
        b.insert("runtime/python.exe".into(),json!({"sha256":hash(b"new runtime"),"size_bytes":11}));
        b.insert("runtime/new.pyd".into(),json!({"sha256":hash(b"new"),"size_bytes":3}));
        let(resolved,_)=compatible_maps(&a,&b).unwrap();
        assert_eq!(resolved,json!(protected(&a)));
        for p in ["app/engine/dubflow/tts/native.py",HELPERS[0]] {
            let mut changed=b.clone();changed.get_mut(p).unwrap()["sha256"]=json!(hash(b"drift"));
            assert!(compatible_maps(&a,&changed).is_err());
            changed.remove(p);assert!(compatible_maps(&a,&changed).is_err());
        }
        b.insert("app/engine/dubflow/tts/extra.py".into(),json!({"sha256":hash(b""),"size_bytes":0}));
        assert!(compatible_maps(&a,&b).is_err());
    }
    #[test]
    fn unported_actions_and_unknown_capabilities_cannot_enable_execution(){
        for name in ["accept_cue_documents","synthesize_cue","effective_mix","effective_render",
            "editable_pack","clock","chunk","longform","prepare_effective_cue_v1"] {
            assert!(SplitAction::parse(name).is_err());
        }
        let mut foreign=split_capability_declaration_v2();foreign["ignore_runtime"]=json!(true);
        assert!(!same(&foreign,&split_capability_declaration_v2()).unwrap());
        foreign=split_capability_declaration_v2();foreign["capabilities"][0]=json!("synthesize_cue");
        assert!(!same(&foreign,&split_capability_declaration_v2()).unwrap());
    }
    #[test]
    fn native_python_utf8_actual_nul_recipe_vector_agrees_without_path_normalization() {
        let vector=json!({"schema_version":2,"kind":"recipe_vector","path":"D:/café/Trúc Ly",
            "rows":[{"role":"original_runtime","relative_path":"runtime/python.exe",
                "pin":{"sha256":format!("sha256:{}","a".repeat(64)),"size_bytes":104952}},
                {"role":"processing_app","relative_path":"engine/dubflow/review/original_runtime_execution_v2.py",
                "pin":{"sha256":format!("sha256:{}","b".repeat(64)),"size_bytes":18823}}],
            "enabled":false,"context":null});
        assert_eq!(split_execution_recipe_v2(&vector).unwrap(),
            "sha256:4071634ab6f7389c48ea06a12037b2897f090479d38bb344d06a29ddcd731abf");
        let mut changed=vector.clone();
        changed["rows"].as_array_mut().unwrap().reverse();
        assert_ne!(split_execution_recipe_v2(&vector).unwrap(),split_execution_recipe_v2(&changed).unwrap());
    }
}
