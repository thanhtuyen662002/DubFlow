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
use std::time::{Duration, Instant};

const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const MAX_FILES: usize = 50000;

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

fn final_inventory(root: &Path, inventory: &HashMap<String, Value>) -> SupervisorResult<()> {
    let mut observed = HashSet::new();
    let started = Instant::now();
    for subtree in ["app", "runtime"] {
        let mut directories = vec![root.join(subtree)];
        while let Some(directory) = directories.pop() {
            plain(&directory)?;
            for entry in std::fs::read_dir(&directory)? {
                if started.elapsed() > Duration::from_secs(180) {
                    return Err(invalid(
                        "final producer inventory exceeded its bounded deadline",
                    ));
                }
                let path = entry?.path();
                plain(&path)?;
                let metadata = std::fs::symlink_metadata(&path)?;
                if metadata.is_dir() {
                    directories.push(path);
                    continue;
                }
                let name = path
                    .strip_prefix(root)
                    .map_err(|_| invalid("producer inventory escaped its root"))?
                    .to_str()
                    .ok_or_else(|| invalid("producer path is not UTF-8"))?
                    .replace('\\', "/");
                let pin = inventory
                    .get(&name)
                    .ok_or_else(|| invalid("unlisted final producer file"))?;
                if !metadata.is_file()
                    || regular(&path)?.metadata()?.len() != pin["size_bytes"].as_u64().unwrap()
                    || !observed.insert(name)
                {
                    return Err(invalid("final producer inventory changed after hashing"));
                }
            }
        }
    }
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
        let started = Instant::now();
        let mut observed = HashSet::new();
        for subtree in ["app", "runtime"] {
            let mut directories = vec![root.join(subtree)];
            while let Some(directory) = directories.pop() {
                plain(&directory)?;
                for entry in std::fs::read_dir(&directory)? {
                    if started.elapsed() > Duration::from_secs(180) {
                        return Err(invalid(
                            "original runtime validation exceeded its bounded deadline",
                        ));
                    }
                    let path = entry?.path();
                    plain(&path)?;
                    let metadata = std::fs::symlink_metadata(&path)?;
                    if metadata.is_dir() {
                        directories.push(path);
                        continue;
                    }
                    if !metadata.is_file() {
                        return Err(invalid("nonregular file in producer import tree"));
                    }
                    let name = path
                        .strip_prefix(root)
                        .map_err(|_| invalid("producer import escaped its root"))?
                        .to_str()
                        .ok_or_else(|| invalid("producer path is not UTF-8"))?
                        .replace('\\', "/");
                    let pin = inventory
                        .get(&name)
                        .ok_or_else(|| invalid("unlisted file in original producer import tree"))?;
                    // Empty tracked files are allowed in release trees.
                    if pin["size_bytes"] == 0 {
                        let file = regular(&path)?;
                        if file.metadata()?.len() != 0 || pin["sha256"] != hash(&[]) {
                            return Err(invalid("empty producer file changed"));
                        }
                    } else {
                        fingerprint(&path, pin, false, 8 * 1024 * 1024 * 1024)?;
                    }
                    if !observed.insert(name) {
                        return Err(invalid("duplicate observed producer file"));
                    }
                }
            }
        }
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
