//! Native source-clock preparation is independent of candidate edits, TTS/mix
//! and encoder environment admission. One invocation advances one saved window.
use super::*;
use crate::review_effective::AdmittedEffectiveState;
use crate::review_retention::NativeOutputPin;
use std::path::Component;

pub(crate) struct SourceClockInputs<'a> {
    pub(crate) processing: &'a AdmittedProcessingRuntime,
    pub(crate) store: &'a DurableStore,
    pub(crate) data: &'a Path,
    pub(crate) assets: &'a AcceptedAudioStore,
    pub(crate) state: &'a ReviewGraphState,
    pub(crate) current: &'a AdmittedEffectiveState,
    pub(crate) output_pin: &'a NativeOutputPin,
    pub(crate) candidates: &'a Path,
}

pub(crate) struct CurrentSourceClock {
    pub(super) manifest: AcceptedJsonDocument,
    pub(super) stream: SourceStream,
    pub(super) source_pin: Value,
    pub(super) source_root: PathBuf,
    pub(super) dispatch: Value,
}

fn current_fence(input: &SourceClockInputs<'_>) -> SupervisorResult<()> {
    if input.current.state() != input.state
        || input
            .store
            .review_graph_state(&input.state.token.job_id)?
            .as_ref()
            != Some(input.state)
        || exact(
            &crate::review_effective::read_current(input.store, input.data, input.state)?
                .packet()?,
        )? != exact(&input.current.packet()?)?
        || input
            .store
            .review_graph_state(&input.state.token.job_id)?
            .as_ref()
            != Some(input.state)
    {
        return Err(invalid(
            "source clock current token/content changed; reload",
        ));
    }
    Ok(())
}

fn original_manifest(input: &SourceClockInputs<'_>) -> SupervisorResult<AcceptedJsonDocument> {
    let origin = decode(&input.current.origin().graph_json, LIMIT)?;
    accepted_json(&origin["accepted_documents"]["job_manifest"], input.assets)
}

fn source_pin(manifest: &Value) -> SupervisorResult<(Value, PathBuf)> {
    let path = PathBuf::from(
        manifest["source"]["path"]
            .as_str()
            .ok_or_else(|| invalid("original clock source path is missing"))?,
    );
    if !path.is_absolute() || path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid(
            "original clock source must be an absolute admitted path",
        ));
    }
    let digest = hash_value(&manifest["source"]["sha256"])?;
    let size = regular(&path)?.metadata()?.len();
    let root = path
        .parent()
        .ok_or_else(|| invalid("original clock source root missing"))?
        .to_owned();
    Ok((
        json!({"path":path,"sha256":digest,"size_bytes":size.to_string()}),
        root,
    ))
}

impl CurrentSourceClock {
    pub(crate) fn prepare(
        input: &SourceClockInputs<'_>,
        deadline: Instant,
    ) -> SupervisorResult<Self> {
        current_fence(input)?;
        let manifest = original_manifest(input)?;
        let raw = input.assets.read_json(&manifest)?;
        let value = decode(&raw, LIMIT)?;
        let (source_pin, source_root) = source_pin(&value)?;
        let prepared = Self {
            manifest,
            stream: SourceStream::read(&value["source"]["probe"])?,
            source_pin,
            source_root,
            dispatch: input.processing.clock_execution_receipt()?,
        };
        prepared.revalidate(input, deadline)?;
        Ok(prepared)
    }

    pub(crate) fn revalidate(
        &self,
        input: &SourceClockInputs<'_>,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        if Instant::now() >= deadline {
            return Err(invalid("source clock admission deadline expired"));
        }
        current_fence(input)?;
        let fresh = original_manifest(input)?;
        if descriptor(&fresh) != descriptor(&self.manifest) {
            return Err(invalid("source clock original manifest binding changed"));
        }
        let raw = input.assets.read_json(&fresh)?;
        let value = decode(&raw, LIMIT)?;
        let (pin, root) = source_pin(&value)?;
        if pin != self.source_pin
            || root != self.source_root
            || SourceStream::read(&value["source"]["probe"])?.descriptor()
                != self.stream.descriptor()
            || exact(&input.processing.clock_execution_receipt()?)? != exact(&self.dispatch)?
        {
            return Err(invalid(
                "source clock source/decoder/release identity changed",
            ));
        }
        let path = Path::new(pin["path"].as_str().unwrap());
        verified(
            path,
            hash_value(&pin["sha256"])?,
            tick(&pin["size_bytes"], true)?,
            false,
            deadline.min(Instant::now() + Duration::from_secs(180)),
        )?;
        current_fence(input)?;
        if Instant::now() >= deadline {
            return Err(invalid(
                "source clock revalidation exhausted phase deadline",
            ));
        }
        Ok(())
    }
}

const KEYS: [&str; 10] = [
    "--data-root",
    "--job-id",
    "--revision",
    "--snapshot-sha256",
    "--graph-sha256",
    "--original-manifest-sha256",
    "--processing-root",
    "--processing-manifest-sha256",
    "--processing-manifest-size-bytes",
    "--processing-source-sha",
];

fn arguments(
    mut args: impl Iterator<Item = OsString>,
) -> SupervisorResult<HashMap<String, String>> {
    let mut values = HashMap::new();
    while let Some(key) = args.next() {
        let key = key
            .into_string()
            .map_err(|_| invalid("source clock flag is not UTF8"))?;
        if !KEYS.contains(&key.as_str()) || values.contains_key(&key) {
            return Err(invalid("unknown or duplicate source clock flag"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("source clock flag value missing"))?
            .into_string()
            .map_err(|_| invalid("source clock argument is not UTF8"))?;
        if value.is_empty() || value.len() > 32768 {
            return Err(invalid("source clock argument exceeds bound"));
        }
        values.insert(key, value);
    }
    if KEYS.iter().any(|key| !values.contains_key(*key)) {
        return Err(invalid(
            "source clock requires full current token and processing selection",
        ));
    }
    Ok(values)
}

fn token_fence(state: &ReviewGraphState, values: &HashMap<String, String>) -> SupervisorResult<()> {
    let get = |key: &str| {
        values
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| invalid("source clock token field missing"))
    };
    if state.token.job_id != get("--job-id")?
        || state.token.revision != tick(&json!(get("--revision")?), false)?
        || state.token.snapshot_sha256 != get("--snapshot-sha256")?
        || state.token.graph_sha256 != get("--graph-sha256")?
        || state.token.original_manifest_sha256 != get("--original-manifest-sha256")?
    {
        return Err(invalid("source clock current token changed; reload"));
    }
    Ok(())
}

pub(crate) fn run_clock(args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let values = arguments(args)?;
    let data = PathBuf::from(&values["--data-root"]);
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("source clock requires absolute control root"));
    }
    crate::review_pin::plain(&data)?;
    let job = &values["--job-id"];
    crate::validate_job_id(job)?;
    let db = data.join("control/jobs.sqlite3");
    crate::review_pin::plain(&db)?;
    let _guard = crate::JobExecutionGuard::acquire(&db, job)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("source clock current graph missing"))?;
    token_fence(&state, &values)?;
    let deadline = Instant::now() + Duration::from_secs(720);
    stage(Stage::Current);
    let current = crate::review_effective::read_current(&store, &data, &state)?;
    let original = crate::review_seed::read_original(&store, &data, current.origin())?;
    stage(Stage::Processing);
    let processing = AdmittedProcessingRuntime::read(
        &store,
        job,
        &PathBuf::from(&values["--processing-root"]),
        &json!({"sha256":values["--processing-manifest-sha256"],
            "size_bytes":tick(&json!(values["--processing-manifest-size-bytes"]), true)?}),
        &values["--processing-source-sha"],
    )?;
    let assets = AcceptedAudioStore::open_existing(&data, job)?;
    let output_pin = NativeOutputPin::read(&original.output_root)?;
    let candidates = crate::review_correction::private_root(&original.output_root, job)?;
    let input = SourceClockInputs {
        processing: &processing,
        store: &store,
        data: &data,
        assets: &assets,
        state: &state,
        current: &current,
        output_pin: &output_pin,
        candidates: &candidates,
    };
    let source = CurrentSourceClock::prepare(&input, deadline)?;
    let journal = source.advance_clock_window(&input, deadline, &mut || false)?;
    source.revalidate(&input, deadline)?;
    println!(
        "{}",
        exact(
            &json!({"schema_version":1,"kind":"review_native_source_clock_progress",
        "current_token":crate::review_publication::token(&state),"journal":journal.packet(),
        "native_decoded_clock_admitted":false,"native_environment_admitted":false,
        "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false})
        )?
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn values(state: &ReviewGraphState) -> HashMap<String, String> {
        [
            ("--job-id", state.token.job_id.clone()),
            ("--revision", state.token.revision.to_string()),
            ("--snapshot-sha256", state.token.snapshot_sha256.clone()),
            ("--graph-sha256", state.token.graph_sha256.clone()),
            (
                "--original-manifest-sha256",
                state.token.original_manifest_sha256.clone(),
            ),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), v))
        .collect()
    }
    #[test]
    fn native_source_clock_refuses_each_foreign_current_field_without_mutation() {
        crate::review_seed::tests::with_admitted_original(|store, _, state| {
            let mut wide = state.clone();
            wide.token.revision = 9007199254740995;
            let expected = values(&wide);
            token_fence(&wide, &expected).unwrap();
            for key in [
                "--job-id",
                "--revision",
                "--snapshot-sha256",
                "--graph-sha256",
                "--original-manifest-sha256",
            ] {
                let mut foreign = expected.clone();
                foreign.insert(
                    key.into(),
                    if key == "--revision" {
                        "9007199254740994".into()
                    } else {
                        "foreign".into()
                    },
                );
                assert!(token_fence(&wide, &foreign).is_err(), "{key}");
            }
            for revision in [
                "09007199254740995",
                "9007199254740996.0",
                "9223372036854775808",
            ] {
                let mut foreign = expected.clone();
                foreign.insert("--revision".into(), revision.into());
                assert!(token_fence(&wide, &foreign).is_err());
            }
            assert_eq!(
                store
                    .review_graph_state(&state.token.job_id)
                    .unwrap()
                    .as_ref(),
                Some(state)
            );
        });
    }
    #[test]
    fn native_source_clock_command_rejects_caller_decoder_clock_environment_and_incomplete_flags() {
        let valid: Vec<OsString> = KEYS
            .iter()
            .flat_map(|key| [(*key).into(), "value".into()])
            .collect();
        assert_eq!(arguments(valid.clone().into_iter()).unwrap().len(), 10);
        for flag in [
            "--source",
            "--ffprobe",
            "--time-base",
            "--environment",
            "--ordinal",
            "--translation",
            "--voice",
            "--revision",
        ] {
            let mut extra = valid.clone();
            extra.extend([flag.into(), "injected".into()]);
            assert!(arguments(extra.into_iter()).is_err(), "{flag}");
        }
        assert!(arguments(valid[..valid.len() - 1].iter().cloned()).is_err());
        assert!(arguments(valid[..valid.len() - 2].iter().cloned()).is_err());
    }
}
