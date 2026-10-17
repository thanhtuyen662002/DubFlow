//! Additive private chunk dispatch admission from the exact processing release.
//! This receipt does not admit the current/source/environment/media graph.
use super::*;

const CAPABILITY: &str = "app/engine/dubflow/review/processing-chunk-dispatch-v1.json";
const ENTRY: &str = "app/engine/dubflow/review/longform_execution.py";
const SOURCES: [&str; 15] = [
    ENTRY,
    "app/engine/dubflow/review/longform_bridge.py",
    "app/engine/dubflow/review/longform_encode.py",
    "app/engine/dubflow/review/longform_captions.py",
    "app/engine/dubflow/review/longform_fragments.py",
    "app/engine/dubflow/review/longform_plan.py",
    "app/engine/dubflow/review/longform_clock.py",
    "app/engine/dubflow/review/longform_probe.py",
    "app/engine/dubflow/review/render_execution.py",
    "app/engine/dubflow/review/synthesis_worker.py",
    "app/engine/dubflow/review/synthesis_input.py",
    "app/engine/dubflow/review/production.py",
    "app/engine/dubflow/review/export.py",
    "app/engine/dubflow/review/wire.py",
    "app/engine/dubflow/media/adapter.py",
];

fn declaration() -> Value {
    json!({"schema_version":1,"kind":"review_processing_chunk_dispatch_capability",
        "capability":"review_chunk_dispatch_v1","entrypoint":"engine/dubflow/review/longform_execution.py",
        "context_format":"review_native_chunk_execution_context_v1",
        "transport":"review_chunk_native_request_ack_result_v1",
        "encoder":"review-h264-mf-software-global-ass-chunk-v1",
        "maximum_context_bytes":131072,"maximum_plan_bytes":4194304,
        "maximum_caption_bytes":67108864,"maximum_chunk_bytes":268435456,"maximum_seconds":900,
        "result":"review_private_encoded_chunk_candidate",
        "native_current_source_admitted":false,"native_environment_admitted":false,
        "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_publication":false})
}

fn source_pins(root: &Path, inventory: &HashMap<String, Value>) -> SupervisorResult<Vec<Value>> {
    let mut result = Vec::new();
    for name in SOURCES {
        let pin = inventory.get(name).ok_or_else(|| {
            invalid("chunk dispatch dependency is not in the processing inventory")
        })?;
        fingerprint(&root.join(name), pin, false, 64 * 1024 * 1024)?;
        result.push(json!({"path":name,"pin":pin}));
    }
    Ok(result)
}

impl AdmittedProcessingRuntime {
    pub(super) fn chunk_execution_receipt(&self) -> SupervisorResult<Value> {
        // Includes unchanged original/protected-runtime compatibility plus both
        // complete current import inventories; no partial directory admission.
        let processing = self.receipt()?;
        let capability =
            self.runtime.inventory.get(CAPABILITY).ok_or_else(|| {
                invalid("processing release lacks private chunk dispatch capability")
            })?;
        let raw = fingerprint(
            &self.runtime.root.join(CAPABILITY),
            capability,
            true,
            64 * 1024,
        )?;
        let value = decode(
            std::str::from_utf8(&raw)
                .map_err(|_| invalid("chunk dispatch capability is not UTF8"))?,
            64 * 1024,
        )?;
        let mut actual = String::new();
        let mut expected = String::new();
        super::super::review_document::canonical(&value, &mut actual)?;
        super::super::review_document::canonical(&declaration(), &mut expected)?;
        if actual != expected {
            return Err(invalid(
                "unknown chunk dispatch capability requires compatibility",
            ));
        }
        let sources = source_pins(&self.runtime.root, &self.runtime.inventory)?;
        final_inventory(&self.runtime.root, &self.runtime.inventory)?;
        Ok(
            json!({"schema_version":1,"kind":"review_admitted_chunk_processing_dispatch",
            "capability":"review_chunk_dispatch_v1","processing_release":processing,
            "capability_document":capability,
            "entry":{"path":self.runtime.root.join(ENTRY),"pin":self.runtime.inventory[ENTRY]},
            "recipe_dependencies":sources,"context_format":"review_native_chunk_execution_context_v1",
            "native_current_source_admitted":false,"native_environment_admitted":false,
            "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pinned_chunk_processing_declaration_matches_python_without_enabling_public_longform() {
        let raw =
            include_str!("../../../../engine/dubflow/review/processing-chunk-dispatch-v1.json");
        let actual = decode(raw, 64 * 1024).unwrap();
        assert_eq!(actual, declaration());
        assert_eq!(actual["native_current_source_admitted"], false);
        assert_eq!(actual["native_environment_admitted"], false);
        assert_eq!(effective_render_declaration()["chunked_longform"], false);
        assert!(source_pins(Path::new("/never-open-unpinned-chunk"), &HashMap::new()).is_err());
    }
}
