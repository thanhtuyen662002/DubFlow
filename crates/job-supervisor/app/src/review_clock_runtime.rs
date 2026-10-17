//! Explicit source-bound private decoded-clock entry admission.
use super::*;

const CAPABILITY: &str = "app/engine/dubflow/review/processing-clock-dispatch-v1.json";
const ENTRY: &str = "app/engine/dubflow/review/longform_clock_execution.py";
const SOURCES: [&str; 20] = [
    ENTRY,
    "app/engine/dubflow/review/longform_execution.py",
    "app/engine/dubflow/review/longform_windows.py",
    "app/engine/dubflow/review/longform_probe.py",
    "app/engine/dubflow/review/longform_clock.py",
    "app/engine/dubflow/review/longform_plan.py",
    "app/engine/dubflow/review/production.py",
    "app/engine/dubflow/review/wire.py",
    "app/engine/dubflow/review/render_execution.py",
    "app/engine/dubflow/review/export.py",
    "app/engine/dubflow/mix/__init__.py",
    "app/engine/dubflow/mix/streaming.py",
    "app/engine/dubflow/mix/adapter.py",
    "app/engine/dubflow/media/__init__.py",
    "app/engine/dubflow/media/adapter.py",
    "app/engine/dubflow/security/__init__.py",
    "app/engine/dubflow/security/boundary.py",
    "app/engine/dubflow/review/__init__.py",
    "app/engine/dubflow/__init__.py",
    "app/engine/__init__.py",
];
fn declaration() -> Value {
    json!({"schema_version":1,"kind":"review_processing_clock_dispatch_capability",
        "capability":"review_clock_window_dispatch_v1",
        "entrypoint":"engine/dubflow/review/longform_clock_execution.py",
        "context_format":"review_native_clock_execution_context_v1",
        "decoder":"review-decoded-pts-keyframe-packet-window-v1",
        "maximum_context_bytes":4194304,"maximum_result_bytes":8404992,
        "maximum_window_bytes":4194304,"maximum_seconds":180,"maximum_packets":16384,
        "result":"review_private_clock_window_result","native_decoded_clock_admitted":false,
        "native_environment_admitted":false,"semantic_qc_admitted":false,
        "chunked_longform":false,"active_graph_publication":false})
}
fn dependencies(root: &Path, inventory: &HashMap<String, Value>) -> SupervisorResult<Vec<Value>> {
    let mut pins = Vec::new();
    for name in SOURCES {
        let pin = inventory.get(name).ok_or_else(|| {
            invalid("clock dispatch dependency is missing from full release inventory")
        })?;
        fingerprint(&root.join(name), pin, false, 64 * 1024 * 1024)?;
        pins.push(json!({"path":name,"pin":pin}));
    }
    Ok(pins)
}
impl AdmittedProcessingRuntime {
    pub(crate) fn clock_execution_receipt(&self) -> SupervisorResult<Value> {
        let processing = self.receipt()?;
        let cap = self
            .runtime
            .inventory
            .get(CAPABILITY)
            .ok_or_else(|| invalid("processing release lacks bounded clock capability"))?;
        let raw = fingerprint(&self.runtime.root.join(CAPABILITY), cap, true, 64 * 1024)?;
        let actual = decode(
            std::str::from_utf8(&raw).map_err(|_| invalid("clock capability is not UTF8"))?,
            64 * 1024,
        )?;
        let mut a = String::new();
        let mut b = String::new();
        super::super::review_document::canonical(&actual, &mut a)?;
        super::super::review_document::canonical(&declaration(), &mut b)?;
        if a != b {
            return Err(invalid(
                "unknown clock execution capability requires compatibility",
            ));
        }
        let sources = dependencies(&self.runtime.root, &self.runtime.inventory)?;
        let probe = super::super::first_existing(&[
            self.runtime.root.join("runtime/media/ffprobe.exe"),
            self.runtime.root.join("runtime/ffmpeg/ffprobe.exe"),
        ])
        .ok_or_else(|| invalid("owned clock probe is absent"))?;
        let relative = probe
            .strip_prefix(&self.runtime.root)
            .map_err(|_| invalid("clock probe escapes owned release"))?
            .to_str()
            .ok_or_else(|| invalid("clock probe relative path is not UTF8"))?
            .replace('\\', "/");
        let pin = self
            .runtime
            .inventory
            .get(&relative)
            .ok_or_else(|| invalid("clock probe is not inventoried"))?;
        fingerprint(&probe, pin, false, 512 * 1024 * 1024)?;
        let size = pin["size_bytes"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or_else(|| invalid("clock probe size is invalid"))?;
        let wire = json!({"sha256":pin["sha256"],"size_bytes":size.to_string()});
        let mut release_raw = String::new();
        super::super::review_document::canonical(&processing, &mut release_raw)?;
        let recipe = json!({"schema_version":1,"kind":"review_owned_clock_decoder_recipe",
            "capability":actual,"capability_pin":cap,"processing_release_sha256":hash(release_raw.as_bytes()),
            "ffprobe":wire,"sources":sources});
        let mut recipe_raw = String::new();
        super::super::review_document::canonical(&recipe, &mut recipe_raw)?;
        final_inventory(&self.runtime.root, &self.runtime.inventory)?;
        Ok(
            json!({"schema_version":1,"kind":"review_admitted_clock_processing_dispatch",
            "processing_release":processing,"processing_release_sha256":hash(release_raw.as_bytes()),
            "entry":{"path":self.runtime.root.join(ENTRY),"pin":self.runtime.inventory[ENTRY]},
            "ffprobe":{"path":probe,"pin":wire},"recipe":recipe,
            "processing_clock_recipe_sha256":hash(recipe_raw.as_bytes()),
            "native_decoded_clock_admitted":false,"native_environment_admitted":false,
            "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false}),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_clock_dependencies_reopen_every_full_helper_and_refuse_corruption() {
        let root = std::env::temp_dir().join(format!(
            "dubflow-clock-import-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        struct Fixture(PathBuf);
        impl Drop for Fixture {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _fixture = Fixture(root.clone());
        let mut pins = HashMap::new();
        for name in SOURCES {
            let path = root.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            let raw = format!("exact clock dependency {name}\n");
            std::fs::write(path, &raw).unwrap();
            pins.insert(
                name.into(),
                json!({"sha256":hash(raw.as_bytes()),"size_bytes":raw.len()}),
            );
        }
        assert_eq!(dependencies(&root, &pins).unwrap().len(), 20);
        let changed = root.join(SOURCES[2]);
        let mut raw = std::fs::read(&changed).unwrap();
        raw[0] ^= 1;
        std::fs::write(&changed, raw).unwrap();
        assert!(dependencies(&root, &pins).is_err());
        std::fs::write(&changed, format!("exact clock dependency {}\n", SOURCES[2])).unwrap();
        std::fs::remove_file(root.join(SOURCES[19])).unwrap();
        assert!(dependencies(&root, &pins).is_err());
    }
    #[test]
    fn native_clock_dispatch_declaration_matches_owned_entry_without_longform_grant() {
        let actual = decode(
            include_str!("../../../../engine/dubflow/review/processing-clock-dispatch-v1.json"),
            64 * 1024,
        )
        .unwrap();
        assert_eq!(actual, declaration());
        assert_eq!(actual["chunked_longform"], false);
        assert_eq!(actual["native_decoded_clock_admitted"], false);
        assert!(dependencies(
            Path::new("/never-import-uninventoried-clock"),
            &HashMap::new()
        )
        .is_err());
    }
}
