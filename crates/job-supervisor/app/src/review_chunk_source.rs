//! Bind current native Review content, immutable origin/source bytes and the
//! independently derived processing recipe before any future chunk dispatch.
//! This does not admit decoded frame coverage or the codec/font/OS environment.
use super::super::review_chunks::BoundPlan;
use super::super::review_runtime::ChunkRecipeSelection;
use super::*;
use std::collections::HashSet;

#[path = "review_clock_journal.rs"]
mod clock_journal;
pub(crate) use clock_journal::{run_clock, CurrentClockJournal};

struct SourceStream {
    geometry: [u32; 2],
    clock: [u64; 2],
    stream: u16,
    start: i64,
    end: i64,
}
impl SourceStream {
    fn read(probe: &Value) -> SupervisorResult<Self> {
        let streams = probe["streams"]
            .as_array()
            .filter(|v| !v.is_empty() && v.len() <= 1024)
            .ok_or_else(|| invalid("chunk source requires a bounded archived stream inventory"))?;
        let mut indices = HashSet::new();
        for stream in streams {
            let index = stream["index"]
                .as_u64()
                .filter(|v| *v <= 1023)
                .ok_or_else(|| invalid("chunk source stream index is missing or unsupported"))?;
            if !indices.insert(index) {
                return Err(invalid(
                    "chunk source stream inventory contains duplicate indices",
                ));
            }
        }
        // The pinned production adapter selects the first video stream. No
        // caller/plan-selected alternate stream may replace that original choice.
        let video = streams
            .iter()
            .find(|v| v["codec_type"] == "video")
            .ok_or_else(|| invalid("archived source has no primary video stream"))?;
        let dimension = |name: &str| {
            video[name]
                .as_u64()
                .filter(|v| (1..=32768).contains(v))
                .map(|v| v as u32)
                .ok_or_else(|| invalid("chunk source geometry exceeds native context policy"))
        };
        closed(&video["time_base"], &["numerator", "denominator"])?;
        let positive = |name: &str| {
            video["time_base"][name]
                .as_u64()
                .filter(|v| *v > 0 && *v <= i64::MAX as u64)
                .ok_or_else(|| invalid("chunk source requires an exact positive stream rational"))
        };
        let clock = [positive("numerator")?, positive("denominator")?];
        let [mut a, mut b] = clock;
        while b != 0 {
            (a, b) = (b, a % b);
        }
        if a != 1 {
            return Err(invalid("archived chunk source time base is not reduced"));
        }
        let start = video["start_pts"]
            .as_i64()
            .ok_or_else(|| invalid("chunk source requires exact signed64 start PTS"))?;
        let duration = video["duration_ts"]
            .as_u64()
            .filter(|v| *v > 0 && *v <= i64::MAX as u64)
            .ok_or_else(|| {
                invalid(
                    "chunk source requires exact positive stream duration_ts; compatibility needed",
                )
            })?;
        if video["duration_ticks"].as_u64() != Some(duration) {
            return Err(invalid(
                "chunk source duration changed from the pinned exact stream duration",
            ));
        }
        let end = start
            .checked_add(duration as i64)
            .ok_or_else(|| invalid("chunk source end PTS exceeds signed64"))?;
        if i128::from(duration) * i128::from(clock[0]) > 21600 * i128::from(clock[1]) {
            return Err(invalid("chunk source exceeds the six-hour policy"));
        }
        Ok(Self {
            geometry: [dimension("width")?, dimension("height")?],
            clock,
            stream: video["index"].as_u64().unwrap() as u16,
            start,
            end,
        })
    }
    fn selection(&self, burn: bool, environment: &str) -> SupervisorResult<ChunkRecipeSelection> {
        ChunkRecipeSelection::new(self.geometry, self.clock, self.stream, burn, environment)
    }
    fn descriptor(&self) -> Value {
        json!({"geometry":self.geometry,"video_stream_index":self.stream,
            "time_base":{"numerator":self.clock[0].to_string(),"denominator":self.clock[1].to_string()},
            "source_interval":{"start_tick":self.start.to_string(),"end_tick":self.end.to_string()}})
    }
}

/// Two phases: derive immutable native source/context/recipe first, then bind
/// the plan produced for those exact pins. Neither phase grants decode or OS
/// environment authority, and no worker/client can manufacture either type.
pub(crate) struct CurrentChunkSource {
    context: AcceptedJsonDocument,
    stream: SourceStream,
    source_pin: Value,
    source_root: PathBuf,
    recipe: Value,
    environment: String,
    burn: bool,
}
impl CurrentChunkSource {
    pub(crate) fn packet(&self) -> Value {
        json!({"schema_version":1,"kind":"review_native_current_chunk_source",
            "current_render_context":descriptor(&self.context),
            "source":{"pin":self.source_pin,"root":self.source_root},
            "archived_source_stream":self.stream.descriptor(),"processing_recipe":self.recipe,
            "original_source_bytes_verified_during_preparation":true,
            "current_state_must_be_revalidated":true,"native_decoded_clock_admitted":false,
            "native_environment_admitted":false,"semantic_qc_admitted":false,
            "chunked_longform":false,"active_graph_published":false})
    }
    pub(crate) fn revalidate(
        &self,
        input: &EffectiveRenderInputs<'_>,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        if Instant::now() >= deadline {
            return Err(invalid("current chunk source admission deadline expired"));
        }
        let view = input.current.packet()?;
        effective_fence(input, &view)?;
        let fresh = effective_context(input, deadline)?;
        if exact(&fresh)? != input.assets.read_json(&self.context)? {
            return Err(invalid(
                "current chunk source/context/token changed; reload instead of repinning",
            ));
        }
        let selection = self.stream.selection(self.burn, &self.environment)?;
        if exact(&input.processing.chunk_encoder_recipe(&selection)?)? != exact(&self.recipe)? {
            return Err(invalid("current chunk processing recipe changed"));
        }
        effective_fence(input, &view)?;
        if Instant::now() >= deadline {
            return Err(invalid(
                "current chunk source revalidation exhausted deadline",
            ));
        }
        Ok(())
    }
    pub(crate) fn bind_plan(
        self,
        input: &EffectiveRenderInputs<'_>,
        raw_plan: &str,
        expected_plan: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<CurrentChunkPlan> {
        if cancelled() {
            return Err(invalid("current chunk plan binding cancelled"));
        }
        self.revalidate(input, deadline)?;
        let plan = BoundPlan::read(
            raw_plan,
            expected_plan,
            &self.context.sha256,
            self.source_pin["sha256"]
                .as_str()
                .ok_or_else(|| invalid("native chunk source SHA missing"))?,
            self.recipe["processing_recipe_sha256"]
                .as_str()
                .ok_or_else(|| invalid("native chunk recipe SHA missing"))?,
        )?;
        plan.require_source_bounds(self.stream.clock, self.stream.start, self.stream.end)?;
        let plan_object = input.assets.accept_json(raw_plan)?;
        if plan_object.sha256 != expected_plan {
            return Err(invalid(
                "native chunk plan changed its canonical sealed bytes",
            ));
        }
        let prepared = CurrentChunkPlan {
            source: self,
            plan_object,
            plan,
        };
        prepared.revalidate(input, deadline, cancelled)?;
        Ok(prepared)
    }
}

pub(crate) struct CurrentChunkPlan {
    source: CurrentChunkSource,
    plan_object: AcceptedJsonDocument,
    plan: BoundPlan,
}
impl CurrentChunkPlan {
    pub(crate) fn plan(&self) -> &BoundPlan {
        &self.plan
    }
    pub(crate) fn packet(&self) -> Value {
        json!({"schema_version":1,"kind":"review_native_bound_current_chunk_plan",
            "current_source":self.source.packet(),"plan":descriptor(&self.plan_object),
            "native_decoded_clock_admitted":false,"native_environment_admitted":false,
            "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false})
    }
    pub(crate) fn revalidate(
        &self,
        input: &EffectiveRenderInputs<'_>,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<()> {
        self.source.revalidate(input, deadline)?;
        input.assets.read_json(&self.plan_object)?;
        self.source
            .require_plan_clock(input, &self.plan, deadline, cancelled)?;
        if Instant::now() >= deadline {
            return Err(invalid("chunk plan readback exhausted native deadline"));
        }
        Ok(())
    }
}

/// Derive the cumulative timeline, retained mix/captions, immutable original
/// source and current token before a clock producer can create a matching plan.
/// Caller packets supply no geometry, stream, source root or clock authority.
pub(crate) fn prepare_current_chunk_source(
    input: &EffectiveRenderInputs<'_>,
    environment: &str,
    deadline: Instant,
) -> SupervisorResult<CurrentChunkSource> {
    let view = input.current.packet()?;
    effective_fence(input, &view)?;
    let context = effective_context(input, deadline)?;
    let manifest = decode(
        context["original_manifest_json"]
            .as_str()
            .ok_or_else(|| invalid("chunk source lacks its native original manifest"))?,
        LIMIT,
    )?;
    let stream = SourceStream::read(&manifest["source"]["probe"])?;
    let burn = context["burn_in_subtitles"]
        .as_bool()
        .ok_or_else(|| invalid("chunk source lost the immutable burn-in option"))?;
    let recipe = input
        .processing
        .chunk_encoder_recipe(&stream.selection(burn, environment)?)?;
    let source = &context["source"];
    let source_pin =
        json!({"path":source["path"],"sha256":source["sha256"],"size_bytes":source["size_bytes"]});
    let source_root = PathBuf::from(
        source["artifact_root"]
            .as_str()
            .ok_or_else(|| invalid("native source root is missing"))?,
    );
    let context_object = input.assets.accept_json(&exact(&context)?)?;
    let prepared = CurrentChunkSource {
        context: context_object,
        stream,
        source_pin,
        source_root,
        recipe,
        environment: environment.into(),
        burn,
    };
    prepared.revalidate(input, deadline)?;
    Ok(prepared)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        decode(
            include_str!("../../../../tests/review/native_chunk_source_v1.json"),
            64 * 1024,
        )
        .unwrap()
    }
    #[test]
    fn native_chunk_source_clock_matches_python_stream_ticks_without_ms_or_fps_substitution() {
        for case in fixture()["cases"].as_array().unwrap() {
            let source = SourceStream::read(&case["probe"]).unwrap();
            assert_eq!(source.descriptor(), case["expected"]);
            assert!(source
                .selection(false, &hash(b"environment identity, not admission"))
                .is_ok());
        }
    }
    #[test]
    fn native_chunk_source_refuses_inferred_duration_duplicate_indices_bad_clock_and_overflow() {
        let original = fixture()["cases"][0]["probe"].clone();
        let index = original["streams"]
            .as_array()
            .unwrap()
            .iter()
            .position(|v| v["codec_type"] == "video")
            .unwrap();
        for (field, value) in [
            ("start_pts", json!(1.25)),
            ("start_pts", json!(i64::MAX)),
            ("duration_ts", Value::Null),
            ("duration_ticks", json!(1)),
            ("width", json!(32769)),
            ("duration_ts", json!(0)),
            ("index", json!(true)),
        ] {
            let mut changed = original.clone();
            changed["streams"][index][field] = value;
            assert!(SourceStream::read(&changed).is_err(), "{field}");
        }
        let mut duplicate = original.clone();
        duplicate["streams"]
            .as_array_mut()
            .unwrap()
            .push(original["streams"][index].clone());
        assert!(SourceStream::read(&duplicate).is_err());
        let mut unreduced = original.clone();
        unreduced["streams"][index]["time_base"] = json!({"numerator":2,"denominator":2000});
        assert!(SourceStream::read(&unreduced).is_err());
        let mut long = original.clone();
        long["streams"][index]["duration_ts"] = json!(21600u64 * 90000 + 1);
        long["streams"][index]["duration_ticks"] = long["streams"][index]["duration_ts"].clone();
        assert!(SourceStream::read(&long).is_err());
    }
    #[test]
    fn native_chunk_plan_refuses_archived_source_interval_or_time_base_substitution() {
        let fixture = decode(
            include_str!("../../../../tests/review/native_longform_plan_v1.json"),
            4 * 1024 * 1024,
        )
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let raw = case["plan_json"].as_str().unwrap();
            let document = decode(raw, 4 * 1024 * 1024).unwrap();
            let plan = BoundPlan::read(
                raw,
                &hash(raw.as_bytes()),
                document["context_sha256"].as_str().unwrap(),
                document["source_sha256"].as_str().unwrap(),
                document["processing_recipe_sha256"].as_str().unwrap(),
            )
            .unwrap();
            let clock = [
                tick(&document["time_base"]["numerator"], true).unwrap(),
                tick(&document["time_base"]["denominator"], true).unwrap(),
            ];
            let start = document["source_interval"]["start_tick"]
                .as_str()
                .unwrap()
                .parse()
                .unwrap();
            let end = document["source_interval"]["end_tick"]
                .as_str()
                .unwrap()
                .parse()
                .unwrap();
            plan.require_source_bounds(clock, start, end).unwrap();
            assert!(plan
                .require_source_bounds([clock[0], clock[1] + 1], start, end)
                .is_err());
            assert!(plan.require_source_bounds(clock, start + 1, end).is_err());
            assert!(plan.require_source_bounds(clock, start, end + 1).is_err());
        }
    }
}
