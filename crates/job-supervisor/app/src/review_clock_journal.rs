//! Native physical journal for private decoded-clock observations. Parsing or
//! retaining a worker observation does not admit decoded coverage or graph use.
use super::*;
use crate::review_retention::NativeMixStore;

const RECORD: usize = 4 * 1024 * 1024;
const TOTAL: usize = 16 * 1024 * 1024;
const PAGE: usize = 24 * 1024;
const RECIPE: &str = "review-decoded-pts-keyframe-packet-window-v1";

fn signed(v: &Value) -> SupervisorResult<i64> {
    let s = v
        .as_str()
        .filter(|s| s.len() <= 20)
        .ok_or_else(|| invalid("clock PTS must be exact decimal text"))?;
    let n = s
        .parse::<i64>()
        .map_err(|_| invalid("clock PTS exceeds signed64"))?;
    if n.to_string() != s {
        return Err(invalid("clock PTS is not canonical"));
    }
    Ok(n)
}
fn hash_value(v: &Value) -> SupervisorResult<&str> {
    v.as_str()
        .filter(|s| {
            s.len() == 71
                && s.starts_with("sha256:")
                && s[7..]
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
        .ok_or_else(|| invalid("clock journal requires exact full hashes"))
}
fn anchor(v: &Value) -> SupervisorResult<i64> {
    closed(v, &["pts", "packet_pos"])?;
    let p = signed(&v["pts"])?;
    if signed(&v["packet_pos"])? < 0 {
        return Err(invalid("clock anchor packet position is negative"));
    }
    Ok(p)
}
fn observe(deadline: Instant, cancelled: &mut impl FnMut() -> bool) -> SupervisorResult<()> {
    if cancelled() || Instant::now() >= deadline {
        return Err(invalid("native clock journal cancelled or expired"));
    }
    Ok(())
}
#[derive(Clone)]
struct Prefix {
    cursor: i64,
    end: i64,
    anchor: Value,
    overlap: Value,
    complete: bool,
    bytes: usize,
    frames: usize,
}
impl Prefix {
    fn new(stream: &SourceStream) -> Self {
        Self {
            cursor: stream.start,
            end: stream.end,
            anchor: Value::Null,
            overlap: json!([]),
            complete: false,
            bytes: 0,
            frames: 0,
        }
    }
    fn admit(&mut self, raw: &str, spec: &str) -> SupervisorResult<()> {
        if self.complete {
            return Err(invalid("clock journal contains a suffix after EOF"));
        }
        let v = decode(raw, RECORD)?;
        closed(
            &v,
            &[
                "schema_version",
                "kind",
                "clock_spec_sha256",
                "start_tick",
                "end_tick",
                "anchor",
                "next_anchor",
                "complete",
                "pts",
                "frame_clock_sha256",
                "observation_sha256",
                "overlap",
                "native_media_admitted",
                "semantic_qc_admitted",
            ],
        )?;
        if exact(&v)? != raw
            || v["schema_version"].as_u64() != Some(1)
            || v["kind"] != "review_decoded_clock_window"
            || v["clock_spec_sha256"] != spec
            || v["native_media_admitted"] != false
            || v["semantic_qc_admitted"] != false
            || v["anchor"] != self.anchor
            || signed(&v["start_tick"])? != self.cursor
        {
            return Err(invalid(
                "clock window changed version/source/spec/anchor or claimed authority",
            ));
        }
        let end = signed(&v["end_tick"])?;
        if end <= self.cursor || end > self.end {
            return Err(invalid("clock window lost source coverage"));
        }
        let complete = v["complete"]
            .as_bool()
            .ok_or_else(|| invalid("clock completion is not boolean"))?;
        let points = v["pts"]
            .as_array()
            .filter(|v| !v.is_empty() && v.len() <= 65536)
            .ok_or_else(|| invalid("clock window point inventory exceeds bound"))?;
        let mut last = None;
        let mut digest = Sha256::new();
        let mut all = Vec::new();
        for p in points {
            let n = signed(p)?;
            if n < self.cursor || n >= end || last.is_some_and(|old| n <= old) {
                return Err(invalid(
                    "clock window duplicates/reorders/escapes source PTS",
                ));
            }
            if last.is_none() && n != self.cursor {
                return Err(invalid("clock window lost first PTS"));
            }
            digest.update(n.to_string().as_bytes());
            digest.update(b"\n");
            last = Some(n);
            all.push(n);
        }
        if hash_value(&v["frame_clock_sha256"])? != format!("sha256:{:x}", digest.finalize()) {
            return Err(invalid("clock window point digest changed"));
        }
        hash_value(&v["observation_sha256"])?;
        let overlap = v["overlap"]
            .as_array()
            .filter(|v| v.len() <= 65536)
            .ok_or_else(|| invalid("clock lookahead exceeds bound"))?;
        let mut previous = None;
        let mut keys = 0usize;
        for frame in overlap {
            closed(frame, &["pts", "key_frame", "packet_pos", "duration"])?;
            let pts = frame["pts"]
                .as_i64()
                .ok_or_else(|| invalid("clock lookahead PTS must be signed64 integer"))?;
            let key = frame["key_frame"]
                .as_u64()
                .filter(|n| *n <= 1)
                .ok_or_else(|| invalid("clock lookahead key flag invalid"))?;
            frame["packet_pos"]
                .as_u64()
                .filter(|n| *n <= i64::MAX as u64)
                .ok_or_else(|| invalid("clock lookahead packet position invalid"))?;
            if !frame["duration"].is_null() {
                frame["duration"]
                    .as_u64()
                    .filter(|n| *n > 0 && *n <= i64::MAX as u64)
                    .ok_or_else(|| invalid("clock lookahead duration invalid"))?;
            }
            if pts < end || pts >= self.end || previous.is_some_and(|old| pts <= old) {
                return Err(invalid(
                    "clock lookahead loses exact order or source interval",
                ));
            }
            previous = Some(pts);
            keys += key as usize;
            all.push(pts);
        }
        let prior = self.overlap.as_array().unwrap();
        if all.len() < prior.len()
            || all
                .iter()
                .zip(prior)
                .any(|(n, p)| p["pts"].as_i64() != Some(*n))
        {
            return Err(invalid(
                "native clock windows disagree across exact lookahead",
            ));
        }
        if complete {
            if end != self.end || !v["next_anchor"].is_null() || !overlap.is_empty() {
                return Err(invalid("complete clock window lost source end"));
            }
        } else {
            let next = &v["next_anchor"];
            if end >= self.end
                || anchor(next)? != end
                || overlap.len() < 2
                || keys < 2
                || overlap[0]["pts"].as_i64() != Some(end)
                || overlap[0]["key_frame"].as_u64() != Some(1)
                || overlap[0]["packet_pos"].as_u64().map(|n| n.to_string())
                    != next["packet_pos"].as_str().map(str::to_owned)
            {
                return Err(invalid(
                    "clock window needs an exact packet anchor and guard GOP",
                ));
            }
        }
        if !self.anchor.is_null() && anchor(&self.anchor)? != self.cursor {
            return Err(invalid("clock journal anchor differs from cursor"));
        }
        self.bytes = self
            .bytes
            .checked_add(raw.len())
            .filter(|n| *n <= TOTAL)
            .ok_or_else(|| invalid("clock journal aggregate bytes exceeded"))?;
        self.frames = self
            .frames
            .checked_add(points.len())
            .filter(|n| *n <= 1_000_000)
            .ok_or_else(|| invalid("clock journal aggregate frames exceeded"))?;
        self.cursor = end;
        self.anchor = v["next_anchor"].clone();
        self.overlap = v["overlap"].clone();
        self.complete = complete;
        Ok(())
    }
}

fn hex(raw: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut out = String::with_capacity(raw.len() * 2);
    for b in raw {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 15) as usize] as char);
    }
    out
}
fn unhex(raw: &Value) -> SupervisorResult<Vec<u8>> {
    let s = raw
        .as_str()
        .filter(|s| !s.is_empty() && s.len() <= PAGE * 2 && s.len() % 2 == 0)
        .ok_or_else(|| invalid("clock page exceeds hex byte bound"))?;
    let digit = |b: u8| match b {
        b'0'..=b'9' => Ok(b - b'0'),
        b'a'..=b'f' => Ok(b - b'a' + 10),
        _ => Err(invalid("clock page hex is not canonical lowercase")),
    };
    s.as_bytes()
        .chunks_exact(2)
        .map(|p| Ok((digit(p[0])? << 4) | digit(p[1])?))
        .collect()
}
fn write_pages(
    store: &NativeMixStore,
    namespace: &str,
    raw: &str,
    deadline: Instant,
) -> SupervisorResult<Value> {
    let mut next = Value::Null;
    let digest = hash(raw.as_bytes());
    for (i, bytes) in raw.as_bytes().chunks(PAGE).enumerate().rev() {
        let page = json!({"schema_version":1,"kind":"review_native_clock_window_page","namespace_sha256":namespace,
            "record_sha256":digest,"offset":(i*PAGE).to_string(),"hex":hex(bytes),"next":next});
        let raw = exact(&page)?;
        let object = store.accept_json_record(&raw, deadline)?;
        if store.read_json_record(&object, deadline)? != raw {
            return Err(invalid("clock page changed at seal"));
        }
        next = object.descriptor();
    }
    Ok(next)
}
fn read_pages(
    store: &NativeMixStore,
    namespace: &str,
    marker: &Value,
    deadline: Instant,
) -> SupervisorResult<String> {
    let expected = hash_value(&marker["record_sha256"])?;
    let size = tick(&marker["size_bytes"], true)? as usize;
    if size > RECORD {
        return Err(invalid("clock record exceeds aggregate page bound"));
    }
    let mut next = marker["first_page"].clone();
    let mut bytes = Vec::new();
    let mut pages = 0usize;
    while !next.is_null() {
        pages += 1;
        if pages > RECORD.div_ceil(PAGE) {
            return Err(invalid("clock pages cycle/exceed bound"));
        }
        let object = store.reopen_file(&next, deadline)?;
        let raw = store.read_json_record(&object, deadline)?;
        let page = decode(&raw, 64 * 1024)?;
        closed(
            &page,
            &[
                "schema_version",
                "kind",
                "namespace_sha256",
                "record_sha256",
                "offset",
                "hex",
                "next",
            ],
        )?;
        if exact(&page)? != raw
            || page["schema_version"].as_u64() != Some(1)
            || page["kind"] != "review_native_clock_window_page"
            || page["namespace_sha256"] != namespace
            || page["record_sha256"] != expected
            || tick(&page["offset"], false)? != bytes.len() as u64
        {
            return Err(invalid("clock page substituted namespace/content/offset"));
        }
        let block = unhex(&page["hex"])?;
        if bytes
            .len()
            .checked_add(block.len())
            .is_none_or(|n| n > size)
        {
            return Err(invalid("clock page byte inventory exceeded"));
        }
        bytes.extend(block);
        next = page["next"].clone();
    }
    if bytes.len() != size || hash(&bytes) != expected {
        return Err(invalid("clock paged record digest/size changed"));
    }
    String::from_utf8(bytes).map_err(|_| invalid("clock paged record is not UTF8"))
}

pub(crate) struct CurrentClockJournal {
    spec: Value,
    namespace: String,
    records: Vec<String>,
    prefix: Prefix,
}
impl CurrentClockJournal {
    pub(crate) fn packet(&self) -> Value {
        json!({"schema_version":1,"kind":"review_native_current_clock_journal","clock_spec":self.spec,
            "namespace_sha256":self.namespace,"window_count":self.records.len(),"next_tick":self.prefix.cursor.to_string(),
            "next_anchor":self.prefix.anchor,"complete_observation":self.prefix.complete,
            "native_decoded_clock_admitted":false,"native_environment_admitted":false,
            "semantic_qc_admitted":false,"chunked_longform":false,"active_graph_published":false})
    }
    pub(crate) fn read_window(&self, ordinal: usize) -> SupervisorResult<Option<&str>> {
        if !(1..=4097).contains(&ordinal) {
            return Err(invalid("clock ordinal exceeds bound"));
        }
        Ok(self.records.get(ordinal - 1).map(String::as_str))
    }
}
impl CurrentChunkSource {
    fn clock_spec(&self) -> SupervisorResult<Value> {
        let probe = &self.recipe["executables"]["ffprobe"]["pin"]["sha256"];
        hash_value(probe)?;
        hash_value(&self.source_pin["sha256"])?;
        Ok(
            json!({"schema_version":1,"recipe":RECIPE,"source_sha256":self.source_pin["sha256"],
            "ffprobe_sha256":probe,"video_stream_index":self.stream.stream,
            "time_base":{"numerator":self.stream.clock[0].to_string(),"denominator":self.stream.clock[1].to_string()},
            "source_interval":{"start_tick":self.stream.start.to_string(),"end_tick":self.stream.end.to_string()}}),
        )
    }
    fn journal_identity(&self) -> SupervisorResult<(Value, String)> {
        let spec = self.clock_spec()?;
        let native_recipe = &self.recipe["processing_recipe_sha256"];
        hash_value(native_recipe)?;
        // Domain separated from video plan journals despite sharing proven small-marker IO.
        let namespace = hash(
            exact(
                &json!({"kind":"review_native_decoded_clock_journal_namespace",
            "clock_spec":spec,"native_processing_recipe_sha256":native_recipe}),
            )?
            .as_bytes(),
        );
        Ok((spec, namespace))
    }
    fn scan_clock(
        &self,
        store: &NativeMixStore,
        job: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<CurrentClockJournal> {
        let (spec, namespace) = self.journal_identity()?;
        let spec_sha = hash(exact(&spec)?.as_bytes());
        let mut journal = CurrentClockJournal {
            spec,
            namespace,
            records: Vec::new(),
            prefix: Prefix::new(&self.stream),
        };
        for (ordinal, raw) in
            store.read_chunk_checkpoints(&journal.namespace, deadline, cancelled)?
        {
            observe(deadline, cancelled)?;
            let marker = decode(&raw, 64 * 1024)?;
            closed(
                &marker,
                &[
                    "schema_version",
                    "kind",
                    "job_id",
                    "namespace_sha256",
                    "ordinal",
                    "record_sha256",
                    "size_bytes",
                    "first_page",
                ],
            )?;
            if exact(&marker)? != raw
                || marker["schema_version"].as_u64() != Some(1)
                || marker["kind"] != "review_native_clock_window_checkpoint"
                || marker["job_id"] != job
                || marker["namespace_sha256"] != journal.namespace
                || marker["ordinal"].as_u64() != Some(u64::from(ordinal))
            {
                return Err(invalid(
                    "clock marker substituted native job/namespace/ordinal",
                ));
            }
            let raw = read_pages(store, &journal.namespace, &marker, deadline)?;
            journal.prefix.admit(&raw, &spec_sha)?;
            journal.records.push(raw);
        }
        observe(deadline, cancelled)?;
        Ok(journal)
    }
    pub(crate) fn read_clock_journal(
        &self,
        input: &EffectiveRenderInputs<'_>,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<CurrentClockJournal> {
        observe(deadline, cancelled)?;
        self.revalidate(input, deadline)?;
        let job = &input.state.token.job_id;
        let result = {
            let store = NativeMixStore::new(input.output_pin, job, storage_budget())?;
            self.scan_clock(&store, job, deadline, cancelled)?
        };
        self.revalidate(input, deadline)?;
        observe(deadline, cancelled)?;
        Ok(result)
    }
    pub(crate) fn append_clock_window(
        &self,
        input: &EffectiveRenderInputs<'_>,
        ordinal: u16,
        raw: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<CurrentClockJournal> {
        observe(deadline, cancelled)?;
        self.revalidate(input, deadline)?;
        if !(1..=4096).contains(&ordinal) || raw.is_empty() || raw.len() > RECORD {
            return Err(invalid("clock append ordinal/record exceeds bound"));
        }
        let job = &input.state.token.job_id;
        {
            let store = NativeMixStore::new(input.output_pin, job, storage_budget())?;
            let mut saved = self.scan_clock(&store, job, deadline, cancelled)?;
            if let Some(old) = saved.records.get(usize::from(ordinal) - 1) {
                if old != raw {
                    return Err(invalid(
                        "clock checkpoint collision changed bytes; never overwrite",
                    ));
                }
            } else {
                if usize::from(ordinal) != saved.records.len() + 1 {
                    return Err(invalid("clock append cannot skip prefix"));
                }
                saved
                    .prefix
                    .admit(raw, &hash(exact(&saved.spec)?.as_bytes()))?;
                let head = write_pages(&store, &saved.namespace, raw, deadline)?;
                observe(deadline, cancelled)?;
                let marker = json!({"schema_version":1,"kind":"review_native_clock_window_checkpoint","job_id":job,
                    "namespace_sha256":saved.namespace,"ordinal":ordinal,"record_sha256":hash(raw.as_bytes()),
                    "size_bytes":raw.len().to_string(),"first_page":head});
                store.publish_chunk_checkpoint(
                    &saved.namespace,
                    ordinal,
                    &exact(&marker)?,
                    deadline,
                )?;
            }
        } // Never hold the output-wide writer across a current/runtime fence.
        self.read_clock_journal(input, deadline, cancelled)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn oracle() -> Value {
        decode(
            include_str!("../../../../tests/review/native_decoded_clock_windows_v1.json"),
            64 * 1024,
        )
        .unwrap()
    }
    fn source(case: &Value) -> SourceStream {
        SourceStream {
            geometry: [96, 64],
            clock: [1, 1000],
            stream: 2,
            start: signed(&case["start"]).unwrap(),
            end: signed(&case["end"]).unwrap(),
        }
    }
    #[test]
    fn native_clock_prefix_matches_independent_python_negative_and_wide_pts() {
        for case in oracle()["cases"].as_array().unwrap() {
            let spec = exact(&case["spec"]).unwrap();
            assert_eq!(hash(spec.as_bytes()), case["spec_sha256"]);
            let mut state = Prefix::new(&source(case));
            for (i, window) in case["windows"].as_array().unwrap().iter().enumerate() {
                let raw = window.as_str().unwrap();
                assert_eq!(hash(raw.as_bytes()), case["window_sha256"][i]);
                state
                    .admit(raw, case["spec_sha256"].as_str().unwrap())
                    .unwrap();
            }
            assert!(state.complete);
            assert_eq!(state.cursor, signed(&case["end"]).unwrap());
            assert_eq!(state.frames, 8);
            assert!(state
                .admit(
                    case["windows"][1].as_str().unwrap(),
                    case["spec_sha256"].as_str().unwrap()
                )
                .is_err());
        }
    }
    #[test]
    fn native_clock_prefix_refuses_foreign_clock_anchor_authority_and_missing_overlap() {
        let case = &oracle()["cases"][0];
        let raw = case["windows"][0].as_str().unwrap();
        for (field, value) in [
            ("clock_spec_sha256", json!(hash(b"foreign spec"))),
            ("anchor", json!({"pts":"-50","packet_pos":"0"})),
            ("native_media_admitted", json!(true)),
            ("semantic_qc_admitted", json!(true)),
            ("pts", json!(["-50", "-50"])),
            ("end_tick", json!("-50")),
            ("overlap", json!([])),
            ("complete", json!(true)),
        ] {
            let mut v = decode(raw, RECORD).unwrap();
            v[field] = value;
            let mut state = Prefix::new(&source(case));
            assert!(
                state
                    .admit(&exact(&v).unwrap(), case["spec_sha256"].as_str().unwrap())
                    .is_err(),
                "{field}"
            );
        }
        let mut state = Prefix::new(&source(case));
        state
            .admit(raw, case["spec_sha256"].as_str().unwrap())
            .unwrap();
        let mut altered = decode(case["windows"][1].as_str().unwrap(), RECORD).unwrap();
        altered["pts"] = json!(["-20", "-9", "0", "10", "20"]);
        altered["frame_clock_sha256"] = json!(hash(b"-20\n-9\n0\n10\n20\n"));
        assert!(state
            .admit(
                &exact(&altered).unwrap(),
                case["spec_sha256"].as_str().unwrap()
            )
            .is_err());
    }
    #[test]
    fn native_clock_page_codec_preserves_full_utf8_and_refuses_foreign_hex() {
        let raw = "Xin chào — clock 9007199254740995\n".repeat(3000);
        let mut bytes = Vec::new();
        for block in raw.as_bytes().chunks(PAGE) {
            let encoded = hex(block);
            assert!(encoded.len() <= PAGE * 2);
            bytes.extend(unhex(&json!(encoded)).unwrap());
        }
        assert_eq!(bytes, raw.as_bytes());
        for v in [json!("AA"), json!("a"), json!("zz"), json!(""), json!(null)] {
            assert!(unhex(&v).is_err());
        }
    }
    #[cfg(windows)]
    #[test]
    fn native_clock_large_record_pages_reopen_on_output_without_widening_small_json_limit() {
        use crate::review_assets::MixStorageBudget;
        use crate::review_retention::NativeOutputPin;
        use std::fs;
        let root = std::env::temp_dir().join(format!(
            "dubflow-native-clock-pages-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        struct Fixture(PathBuf);
        impl Drop for Fixture {
            fn drop(&mut self) {
                fn unseal(p: &Path) {
                    if let Ok(m) = std::fs::symlink_metadata(p) {
                        if m.is_dir() {
                            if let Ok(entries) = std::fs::read_dir(p) {
                                for e in entries.flatten() {
                                    unseal(&e.path());
                                }
                            }
                        } else {
                            let mut permissions = m.permissions();
                            permissions.set_readonly(false);
                            let _ = std::fs::set_permissions(p, permissions);
                        }
                    }
                }
                unseal(&self.0);
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _fixture = Fixture(root.clone());
        let output = NativeOutputPin::read(&root).unwrap();
        let budget = MixStorageBudget {
            maximum_bytes: 8 * 1024 * 1024,
            minimum_free_bytes: 1024,
        };
        let until = Instant::now() + Duration::from_secs(30);
        let namespace = hash(b"clock page physical fixture");
        let raw = exact(&json!({"payload":"clock-π".repeat(16000)})).unwrap();
        assert!(raw.len() > 65536);
        let marker;
        {
            let store = NativeMixStore::new(&output, "clock-pages", budget).unwrap();
            assert!(store.accept_json_record(&raw, until).is_err());
            let first = write_pages(&store, &namespace, &raw, until).unwrap();
            marker = json!({"record_sha256":hash(raw.as_bytes()),"size_bytes":raw.len().to_string(),"first_page":first});
            assert_eq!(read_pages(&store, &namespace, &marker, until).unwrap(), raw);
        }
        {
            let store = NativeMixStore::open_existing(&output, "clock-pages", budget).unwrap();
            assert_eq!(read_pages(&store, &namespace, &marker, until).unwrap(), raw);
            assert!(read_pages(&store, &hash(b"foreign namespace"), &marker, until).is_err());
            let mut wrong = marker.clone();
            wrong["size_bytes"] = json!((raw.len() + 1).to_string());
            assert!(read_pages(&store, &namespace, &wrong, until).is_err());
        }
    }
}
