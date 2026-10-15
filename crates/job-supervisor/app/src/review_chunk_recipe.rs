//! Native derivation of the existing Python chunk encoder recipe. This is a
//! dependency identity, never source-clock, OS/font/provider or graph admission.
use super::*;

const ENCODER_SOURCES: [&str; 8] = [
    "longform_encode.py",
    "longform_captions.py",
    "longform_fragments.py",
    "longform_plan.py",
    "longform_clock.py",
    "longform_probe.py",
    "render_execution.py",
    "synthesis_worker.py",
];

/// The native current/source/environment readers must derive these values.
/// Constructing a recipe selection does not substitute for those readers.
pub(crate) struct ChunkRecipeSelection {
    geometry: [u32; 2],
    clock: [u64; 2],
    stream: u16,
    burn: bool,
    environment: String,
}
impl ChunkRecipeSelection {
    pub(crate) fn new(
        geometry: [u32; 2],
        clock: [u64; 2],
        stream: u16,
        burn: bool,
        environment: &str,
    ) -> SupervisorResult<Self> {
        let [mut a, mut b] = clock;
        if geometry.iter().any(|n| !(1..=32768).contains(n))
            || clock.iter().any(|n| *n == 0 || *n > i64::MAX as u64)
            || stream > 1023
            || !valid_hash(environment)
        {
            return Err(invalid(
                "chunk recipe selection exceeds native context policy",
            ));
        }
        while b != 0 {
            (a, b) = (b, a % b);
        }
        if a != 1 {
            return Err(invalid("chunk recipe source clock must be reduced"));
        }
        Ok(Self {
            geometry,
            clock,
            stream,
            burn,
            environment: environment.into(),
        })
    }
}

fn wire_pin(pin: &Value) -> SupervisorResult<Value> {
    closed(pin, &["sha256", "size_bytes"])?;
    let digest = pin["sha256"]
        .as_str()
        .filter(|v| valid_hash(v))
        .ok_or_else(|| invalid("chunk executable needs its full inventoried SHA"))?;
    let size = pin["size_bytes"]
        .as_u64()
        .filter(|v| *v > 0 && *v <= 512 * 1024 * 1024)
        .ok_or_else(|| invalid("chunk executable exceeds its inventoried bound"))?;
    // Python's recipe uses the context file-pin decimal-string size, whereas
    // the release inventory carries an integer. Do not hash the wrong wire type.
    Ok(json!({"sha256":digest,"size_bytes":size.to_string()}))
}

fn recipe_value(
    selection: &ChunkRecipeSelection,
    ffmpeg: &Value,
    ffprobe: &Value,
    sources: &Value,
) -> SupervisorResult<Value> {
    closed(sources, &ENCODER_SOURCES)?;
    for name in ENCODER_SOURCES {
        if !sources[name].as_str().is_some_and(valid_hash) {
            return Err(invalid(
                "chunk recipe has a missing or malformed helper hash",
            ));
        }
    }
    Ok(
        json!({"schema_version":1,"kind":"review_private_chunk_encoder_recipe",
        "recipe":"review-h264-mf-software-global-ass-chunk-v1",
        "ffmpeg":wire_pin(ffmpeg)?,"ffprobe":wire_pin(ffprobe)?,
        "geometry":selection.geometry,"time_base":selection.clock,
        "runtime_environment_sha256":selection.environment,
        "video_stream_index":selection.stream,"burn_in_subtitles":selection.burn,
        "encoder":"h264_mf","hw_encoding":0,"quality":90,"b_frames":0,
        "pixel_format":"yuv420p","timestamps":"source_pts_trim_global_ass_then_local_origin",
        "caption_window":"review_ass_global_window_v1",
        "fragment_layout":"empty_moov_frag_keyframe_default_base_moof","sources":sources}),
    )
}

fn encoder_sources(root: &Path, inventory: &HashMap<String, Value>) -> SupervisorResult<Value> {
    let mut sources = serde_json::Map::new();
    for name in ENCODER_SOURCES {
        let relative = format!("app/engine/dubflow/review/{name}");
        let pin = inventory
            .get(&relative)
            .ok_or_else(|| invalid("chunk encoder helper is absent from the processing release"))?;
        fingerprint(&root.join(&relative), pin, false, 64 * 1024 * 1024)?;
        sources.insert(name.into(), pin["sha256"].clone());
    }
    Ok(Value::Object(sources))
}

fn executable_pin(runtime: &AdmittedRuntime, path: &Path) -> SupervisorResult<Value> {
    let relative = path
        .strip_prefix(&runtime.root)
        .map_err(|_| invalid("chunk executable escaped the admitted processing root"))?
        .to_str()
        .ok_or_else(|| invalid("chunk executable path is not UTF8"))?
        .replace('\\', "/");
    let pin = runtime
        .inventory
        .get(&relative)
        .ok_or_else(|| invalid("chunk executable is not in the processing inventory"))?;
    fingerprint(path, pin, false, 512 * 1024 * 1024)?;
    wire_pin(pin)?;
    Ok(pin.clone())
}

impl AdmittedProcessingRuntime {
    pub(crate) fn chunk_encoder_recipe(
        &self,
        selection: &ChunkRecipeSelection,
    ) -> SupervisorResult<Value> {
        let dispatch = self.chunk_execution_receipt()?;
        let probe = super::super::first_existing(&[
            self.runtime.root.join("runtime/media/ffprobe.exe"),
            self.runtime.root.join("runtime/ffmpeg/ffprobe.exe"),
        ])
        .ok_or_else(|| invalid("owned chunk probe is absent from the processing release"))?;
        let ffmpeg = executable_pin(&self.runtime, &self.runtime.ffmpeg)?;
        let ffprobe = executable_pin(&self.runtime, &probe)?;
        let sources = encoder_sources(&self.runtime.root, &self.runtime.inventory)?;
        let value = recipe_value(selection, &ffmpeg, &ffprobe, &sources)?;
        let mut raw = String::new();
        super::super::review_document::canonical(&value, &mut raw)?;
        final_inventory(&self.runtime.root, &self.runtime.inventory)?;
        Ok(
            json!({"schema_version":1,"kind":"review_native_chunk_encoder_recipe",
            "processing_dispatch":dispatch,"recipe_json":raw,"processing_recipe_sha256":hash(raw.as_bytes()),
            "executables":{"ffmpeg":{"path":self.runtime.ffmpeg,"pin":wire_pin(&ffmpeg)?},
                "ffprobe":{"path":probe,"pin":wire_pin(&ffprobe)?}},
            "native_current_source_admitted":false,"native_environment_admitted":false,
            "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn oracle() -> Value {
        decode(
            include_str!("../../../../tests/review/native_chunk_recipe_v1.json"),
            64 * 1024,
        )
        .unwrap()
    }
    fn selection(value: &Value) -> SupervisorResult<ChunkRecipeSelection> {
        ChunkRecipeSelection::new(
            serde_json::from_value(value["geometry"].clone())?,
            serde_json::from_value(value["time_base"].clone())?,
            value["video_stream_index"].as_u64().unwrap() as u16,
            value["burn_in_subtitles"].as_bool().unwrap(),
            value["runtime_environment_sha256"].as_str().unwrap(),
        )
    }
    fn encoded(v: &Value) -> SupervisorResult<String> {
        let value = recipe_value(
            &selection(v)?,
            &v["ffmpeg_inventory"],
            &v["ffprobe_inventory"],
            &v["sources"],
        )?;
        let mut raw = String::new();
        super::super::super::review_document::canonical(&value, &mut raw)?;
        Ok(raw)
    }
    #[test]
    fn native_chunk_recipe_matches_independent_python_bytes_and_every_dependency_key() {
        let fixture = oracle();
        for sample in fixture["cases"].as_array().unwrap() {
            let raw = encoded(&sample["input"]).unwrap();
            assert_eq!(raw, sample["python_recipe_json"].as_str().unwrap());
            assert_eq!(hash(raw.as_bytes()), sample["python_recipe_sha256"]);
            let parsed = decode(&raw, 64 * 1024).unwrap();
            assert!(parsed["ffmpeg"]["size_bytes"].is_string());
            for name in [
                "geometry",
                "time_base",
                "video_stream_index",
                "burn_in_subtitles",
                "runtime_environment_sha256",
            ] {
                let mut changed = sample["input"].clone();
                changed[name] = match name {
                    "geometry" => json!([128, 96]),
                    "time_base" => json!([1, 90000]),
                    "video_stream_index" => json!(7),
                    "burn_in_subtitles" => json!(!changed[name].as_bool().unwrap()),
                    _ => json!(hash(b"different native environment")),
                };
                assert_ne!(encoded(&changed).unwrap(), raw, "{name}");
            }
            for name in ENCODER_SOURCES {
                let mut changed = sample["input"].clone();
                changed["sources"][name] = json!(hash(b"changed helper"));
                assert_ne!(encoded(&changed).unwrap(), raw, "{name}");
            }
            for name in ["ffmpeg_inventory", "ffprobe_inventory"] {
                let mut changed = sample["input"].clone();
                changed[name]["sha256"] = json!(hash(b"changed owned executable"));
                assert_ne!(encoded(&changed).unwrap(), raw, "{name}");
            }
        }
    }
    #[test]
    fn native_chunk_recipe_refuses_unknown_sources_wire_types_and_invalid_clock_policy() {
        let sample = &oracle()["cases"][0]["input"];
        for clock in [[0, 1], [2, 1000], [1, 0], [1, u64::MAX]] {
            assert!(
                ChunkRecipeSelection::new([96, 64], clock, 0, false, &hash(b"environment"))
                    .is_err()
            );
        }
        assert!(
            ChunkRecipeSelection::new([0, 64], [1, 1000], 0, false, &hash(b"environment")).is_err()
        );
        assert!(
            ChunkRecipeSelection::new([96, 32769], [1, 1000], 0, false, &hash(b"environment"))
                .is_err()
        );
        assert!(
            ChunkRecipeSelection::new([96, 64], [1, 1000], 1024, false, &hash(b"environment"))
                .is_err()
        );
        assert!(
            ChunkRecipeSelection::new([96, 64], [1, 1000], 0, false, "caller supplied label")
                .is_err()
        );
        for name in ENCODER_SOURCES {
            let mut missing = sample.clone();
            missing["sources"].as_object_mut().unwrap().remove(name);
            assert!(encoded(&missing).is_err());
        }
        let mut extra = sample.clone();
        extra["sources"]["unknown.py"] = json!(hash(b"unknown"));
        assert!(encoded(&extra).is_err());
        for wrong in [
            json!("123"),
            json!(0),
            json!(-1),
            json!(true),
            json!(536870913u64),
        ] {
            let mut changed = sample.clone();
            changed["ffmpeg_inventory"]["size_bytes"] = wrong;
            assert!(encoded(&changed).is_err());
        }
    }
    #[test]
    fn native_chunk_recipe_helper_inventory_checks_actual_full_bytes_and_missing_paths() {
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-chunk-recipe-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        struct OwnedFixture(PathBuf);
        impl Drop for OwnedFixture {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _fixture = OwnedFixture(root.clone());
        let dir = root.join("app/engine/dubflow/review");
        std::fs::create_dir_all(&dir).unwrap();
        let mut inventory = HashMap::new();
        for name in ENCODER_SOURCES {
            let raw = format!("physical helper fixture {name}\n");
            std::fs::write(dir.join(name), raw.as_bytes()).unwrap();
            inventory.insert(
                format!("app/engine/dubflow/review/{name}"),
                json!({"sha256":hash(raw.as_bytes()),"size_bytes":raw.len()}),
            );
        }
        assert_eq!(
            encoder_sources(&root, &inventory)
                .unwrap()
                .as_object()
                .unwrap()
                .len(),
            8
        );
        let changed = dir.join(ENCODER_SOURCES[0]);
        let mut raw = std::fs::read(&changed).unwrap();
        raw[0] ^= 1;
        std::fs::write(&changed, &raw).unwrap();
        assert!(encoder_sources(&root, &inventory).is_err());
        std::fs::write(
            &changed,
            format!("physical helper fixture {}\n", ENCODER_SOURCES[0]),
        )
        .unwrap();
        std::fs::remove_file(dir.join(ENCODER_SOURCES[7])).unwrap();
        assert!(encoder_sources(&root, &inventory).is_err());
    }
}
