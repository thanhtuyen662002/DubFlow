//! Native provenance/timing binding for an owned-adapter cue request.
//! Pins must come from the native graph and verified release assets, never UI.
//! The owned contract adapter still checks NFC normalization, language tags,
//! confidence and canonical packet encoding before worker dispatch. This API
//! authorizes no media reuse, producer execution or durable graph mutation.
use super::review_document::{
    canonical, closed, decode, identity, text, tick, AdmittedSnapshot, ReviewMode,
};
use super::{SupervisorError, SupervisorResult};
use serde_json::Value;
use sha2::{Digest, Sha256};

const MAX_REQUEST: usize = 32768;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn hash(value: &Value) -> SupervisorResult<&str> {
    let h = value
        .as_str()
        .ok_or_else(|| invalid("request pin must be a hash"))?;
    if h.len() != 71
        || !h.starts_with("sha256:")
        || !h[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid("invalid request hash"));
    }
    Ok(h)
}

fn digest(raw: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(raw))
}

fn object_hash(value: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    canonical(value, &mut raw)?;
    Ok(digest(raw.as_bytes()))
}

fn integer(value: &Value, low: u64, high: u64) -> SupervisorResult<u64> {
    value
        .as_u64()
        .filter(|n| (low..=high).contains(n))
        .ok_or_else(|| invalid("request integer is outside its bound"))
}

fn resource(value: &Value) -> SupervisorResult<()> {
    closed(value, &["max_threads", "max_memory_mb", "max_batch_items"])?;
    integer(&value["max_threads"], 1, 256)?;
    integer(&value["max_memory_mb"], 32, 1048576)?;
    integer(&value["max_batch_items"], 1, 256)?;
    Ok(())
}

fn config(value: &Value) -> SupervisorResult<()> {
    closed(
        value,
        &[
            "sample_rate",
            "channels",
            "bits_per_sample",
            "requested_profile",
            "max_attempts",
            "max_segments_per_chunk",
            "max_text_chars",
            "max_wave_bytes",
            "min_rms_milli",
            "max_clip_fraction_ppm",
            "max_duration_error_ticks",
            "min_speed_ratio_milli",
            "max_speed_ratio_milli",
            "resource",
        ],
    )?;
    for (key, low, high) in [
        ("sample_rate", 8000, 96000),
        ("channels", 1, 2),
        ("bits_per_sample", 16, 16),
        ("max_attempts", 1, 3),
        ("max_segments_per_chunk", 1, 256),
        ("max_text_chars", 1, 16384),
        ("max_wave_bytes", 1024, 512 * 1024 * 1024),
        ("min_rms_milli", 0, 1000),
        ("max_clip_fraction_ppm", 0, 1000000),
        ("max_duration_error_ticks", 0, i64::MAX as u64),
        ("min_speed_ratio_milli", 100, 1000),
        ("max_speed_ratio_milli", 1000, 3000),
    ] {
        integer(&value[key], low, high)?;
    }
    if !["auto", "cpu", "gpu", "fixture"].contains(&text(&value["requested_profile"], 256)?) {
        return Err(invalid("unsupported pinned TTS profile"));
    }
    resource(&value["resource"])
}

fn voice(value: &Value) -> SupervisorResult<()> {
    closed(
        value,
        &[
            "voice_id",
            "voice_version",
            "language",
            "display_name",
            "model_id",
            "model_version",
            "model_hash",
            "manifest_hash",
            "license_id",
            "approved",
            "credential_required",
            "network_required",
            "speaking_rate_milli",
            "pitch_style",
            "emotion_mode",
            "default",
        ],
    )?;
    identity(&value["voice_id"])?;
    for key in [
        "voice_version",
        "display_name",
        "model_id",
        "model_version",
        "license_id",
        "pitch_style",
        "emotion_mode",
    ] {
        text(&value[key], 256)?;
    }
    hash(&value["model_hash"])?;
    hash(&value["manifest_hash"])?;
    integer(&value["speaking_rate_milli"], 500, 2000)?;
    if value["language"] != "vi"
        || value["approved"] != true
        || value["credential_required"] != false
        || value["network_required"] != false
        || !value["default"].is_boolean()
    {
        return Err(invalid("target voice requires explicit unsupported setup"));
    }
    Ok(())
}

fn provenance(value: &Value, input_hash: bool) -> SupervisorResult<()> {
    let mut keys = vec![
        "producer",
        "producer_version",
        "backend_id",
        "runtime",
        "timeline_contract",
        "config_hash",
        "model_id",
        "model_version",
        "model_hash",
        "voice_hash",
        "voice_id",
        "voice_version",
        "requested_profile",
        "hardware_profile",
        "resource",
    ];
    if input_hash {
        keys.push("input_hash");
    }
    if value.get("fallback_reason").is_some() {
        keys.push("fallback_reason");
        text(&value["fallback_reason"], 4096)?;
    }
    closed(value, &keys)?;
    for key in [
        "producer",
        "producer_version",
        "backend_id",
        "runtime",
        "timeline_contract",
        "model_id",
        "model_version",
        "voice_id",
        "voice_version",
        "requested_profile",
        "hardware_profile",
    ] {
        text(&value[key], 256)?;
    }
    for key in ["config_hash", "model_hash", "voice_hash"] {
        hash(&value[key])?;
    }
    if input_hash {
        hash(&value["input_hash"])?;
    }
    resource(&value["resource"])
}

fn gcd(mut a: u128, mut b: u128) -> u128 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
}

// Reduce before equality: signed64 ticks * signed64 numerator fits u128;
// multiplying two fractions' cross-products could require 189 bits.
fn seconds(ticks: &Value, base: &Value) -> SupervisorResult<(u128, u128)> {
    closed(base, &["numerator", "denominator"])?;
    let n = u128::from(tick(&base["numerator"], true)?);
    let d = u128::from(tick(&base["denominator"], true)?);
    if gcd(n, d) != 1 {
        return Err(invalid("cue time base must be reduced"));
    }
    let numerator = u128::from(tick(ticks, false)?) * n;
    let divisor = gcd(numerator, d);
    Ok((numerator / divisor, d / divisor))
}

fn point(value: &Value) -> SupervisorResult<(u128, u128)> {
    closed(value, &["kind", "schema_version", "ticks", "time_base"])?;
    if value["kind"] != "time_point" || value["schema_version"].as_u64() != Some(1) {
        return Err(invalid("unsupported cue point"));
    }
    seconds(&value["ticks"], &value["time_base"])
}

pub(super) struct CueRequestPins<'a> {
    pub(super) manifest_sha256: &'a str,
    pub(super) original_input: &'a Value,
    pub(super) previous_provenance: &'a Value,
    pub(super) config: &'a Value,
    pub(super) target_voice: &'a Value,
}

pub(super) struct BoundCueRequest {
    raw: String,
    input_hash: String,
}

impl BoundCueRequest {
    pub(super) fn read(
        raw: &str,
        before: &AdmittedSnapshot,
        after: &AdmittedSnapshot,
        pins: CueRequestPins<'_>,
    ) -> SupervisorResult<Self> {
        before.validate_correction(after, ReviewMode::Dubbed)?;
        let request = decode(raw, MAX_REQUEST)?;
        closed(
            &request,
            &[
                "schema_version",
                "kind",
                "job_id",
                "revision",
                "finding_id",
                "cue_id",
                "manifest_sha256",
                "before_snapshot_sha256",
                "after_snapshot_sha256",
                "tts_input",
                "producer_pin",
                "config",
                "voice",
            ],
        )?;
        if request["schema_version"].as_u64() != Some(1)
            || request["kind"] != "review_cue_synthesis_input"
            || request["job_id"] != before.job()
            || tick(&request["revision"], true)? != after.revision()
            || hash(&request["manifest_sha256"])? != pins.manifest_sha256
            || hash(&request["before_snapshot_sha256"])? != before.sha256()
            || hash(&request["after_snapshot_sha256"])? != after.sha256()
        {
            return Err(invalid(
                "request differs from native job/revision/manifest/snapshot pins",
            ));
        }
        let finding = identity(&request["finding_id"])?;
        if finding != after.plan_finding_id()? {
            return Err(invalid(
                "request differs from native correction plan finding",
            ));
        }
        let old = before.finding(finding)?;
        let new = after.finding(finding)?;
        if request["cue_id"] != old["cue_id"] {
            return Err(invalid("request refers to another canonical cue"));
        }
        let original = pins.original_input;
        let input = &request["tts_input"];
        let mut keys = vec![
            "segment_id",
            "source_utterance_id",
            "text",
            "normalized_text",
            "start",
            "end",
            "source_language",
            "target_language",
            "confidence",
        ];
        if original.get("render_window_end").is_some() {
            keys.push("render_window_end");
        }
        closed(original, &keys)?;
        closed(input, &keys)?;
        identity(&original["segment_id"])?;
        text(&original["source_utterance_id"], 256)?;
        if original["segment_id"] != old["cue_id"]
            || original["text"] != old["translated_text"]
            || point(&original["start"])?
                != seconds(&old["range"]["start_ticks"], before.time_base())?
            || point(&original["end"])? != seconds(&old["range"]["end_ticks"], before.time_base())?
            || original["start"]["time_base"] != original["end"]["time_base"]
        {
            return Err(invalid(
                "original TTS input differs from native canonical cue",
            ));
        }
        for key in keys
            .iter()
            .filter(|k| !["text", "normalized_text"].contains(k))
        {
            if input[*key] != original[*key] {
                return Err(invalid("request substituted an immutable TTS input field"));
            }
        }
        config(pins.config)?;
        voice(pins.target_voice)?;
        provenance(pins.previous_provenance, true)?;
        provenance(&request["producer_pin"], false)?;
        if request["config"] != *pins.config
            || request["voice"] != *pins.target_voice
            || input["text"] != new["translated_text"]
            || text(&input["text"], 8192)?.chars().count() as u64
                > integer(&pins.config["max_text_chars"], 1, 16384)?
            || new["voice"] != pins.target_voice["voice_id"]
            || old["voice"] != pins.previous_provenance["voice_id"]
        {
            return Err(invalid(
                "request text/config/voice differs from pinned graph",
            ));
        }
        text(&input["normalized_text"], 16384)?;
        if let Some(window) = original.get("render_window_end") {
            point(window)?;
            if window["time_base"] != original["end"]["time_base"]
                || tick(&window["ticks"], false)? < tick(&original["end"]["ticks"], false)?
            {
                return Err(invalid("invalid canonical render window"));
            }
        }
        let previous = pins.previous_provenance;
        if previous["config_hash"] != object_hash(pins.config)?
            || previous["resource"] != pins.config["resource"]
            || previous["requested_profile"] != pins.config["requested_profile"]
            || previous["model_id"] != pins.target_voice["model_id"]
            || previous["model_version"] != pins.target_voice["model_version"]
            || previous["model_hash"] != pins.target_voice["model_hash"]
        {
            return Err(invalid(
                "model/config migration needs explicit native admission",
            ));
        }
        let mut expected = previous.clone();
        expected.as_object_mut().unwrap().remove("input_hash");
        expected["voice_id"] = pins.target_voice["voice_id"].clone();
        expected["voice_version"] = pins.target_voice["voice_version"].clone();
        expected["voice_hash"] = object_hash(pins.target_voice)?.into();
        if old["voice"] == new["voice"]
            && (previous["voice_version"] != expected["voice_version"]
                || previous["voice_hash"] != expected["voice_hash"])
        {
            return Err(invalid("unchanged voice cannot change its version/profile"));
        }
        if request["producer_pin"] != expected {
            return Err(invalid(
                "request changed pinned producer/runtime/model/resource",
            ));
        }
        Ok(Self {
            raw: raw.into(),
            input_hash: digest(raw.as_bytes()),
        })
    }

    pub(super) fn raw(&self) -> &str {
        &self.raw
    }
    pub(super) fn input_hash(&self) -> &str {
        &self.input_hash
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/review/native_cue_request_v1.json"
        ))
        .unwrap()
    }

    fn snapshot(value: &Value) -> AdmittedSnapshot {
        AdmittedSnapshot::read(
            &value.to_string(),
            value["job_id"].as_str().unwrap(),
            value["revision"].as_u64().unwrap(),
        )
        .unwrap()
    }

    fn request(case: &Value) -> Value {
        serde_json::from_str(case["request_utf8"].as_str().unwrap()).unwrap()
    }

    fn bind(raw: &str, f: &Value, case: &Value) -> SupervisorResult<BoundCueRequest> {
        BoundCueRequest::read(
            raw,
            &snapshot(&f["before"]),
            &snapshot(&case["after"]),
            CueRequestPins {
                manifest_sha256: f["manifest_sha256"].as_str().unwrap(),
                original_input: &f["original_input"],
                previous_provenance: &f["previous_provenance"],
                config: &f["config"],
                target_voice: &case["voice"],
            },
        )
    }

    #[test]
    fn actual_python_requests_keep_exact_bytes_wide_revision_and_distinct_bases() {
        let f = fixture();
        assert_ne!(
            f["before"]["time_base"],
            f["original_input"]["start"]["time_base"]
        );
        assert!(f["before"]["revision"].as_u64().unwrap() > (1 << 53));
        for case in f["cases"].as_array().unwrap() {
            let raw = case["request_utf8"].as_str().unwrap();
            let bound = bind(raw, &f, case).unwrap();
            assert_eq!(bound.raw(), raw);
            assert_eq!(bound.input_hash(), case["input_hash"]);
        }
    }

    #[test]
    fn wrong_job_revision_manifest_and_logical_hashes_refuse() {
        let f = fixture();
        let case = &f["cases"][0];
        for field in [
            "job_id",
            "revision",
            "manifest_sha256",
            "before_snapshot_sha256",
            "after_snapshot_sha256",
        ] {
            let mut value = request(case);
            value[field] = match field {
                "job_id" => "other-job".into(),
                "revision" => "1".into(),
                _ => format!("sha256:{}", "e".repeat(64)).into(),
            };
            assert!(bind(&value.to_string(), &f, case).is_err(), "{field}");
        }
    }

    #[test]
    fn request_cannot_substitute_immutable_input_identity_timing_or_confidence() {
        let f = fixture();
        let case = &f["cases"][0];
        for field in [
            "segment_id",
            "source_utterance_id",
            "start",
            "end",
            "render_window_end",
            "source_language",
            "target_language",
            "confidence",
        ] {
            let mut value = request(case);
            value["tts_input"][field] = match field {
                "start" | "end" | "render_window_end" => {
                    let mut p = value["tts_input"][field].clone();
                    p["ticks"] = "1".into();
                    p
                }
                "confidence" => 0.5.into(),
                _ => "other".into(),
            };
            assert!(bind(&value.to_string(), &f, case).is_err(), "{field}");
        }
    }

    #[test]
    fn same_cue_other_finding_cannot_replace_the_plan_identity() {
        let f = fixture();
        let case = &f["cases"][0];
        let mut value = request(case);
        value["finding_id"] = "finding-b".into();
        assert!(bind(&value.to_string(), &f, case).is_err());
        value = request(case);
        value["cue_id"] = "cue-c".into();
        assert!(bind(&value.to_string(), &f, case).is_err());
    }

    #[test]
    fn model_runtime_producer_resource_and_unexpected_input_hash_refuse() {
        let f = fixture();
        let case = &f["cases"][1];
        for field in [
            "model_hash",
            "model_version",
            "runtime",
            "producer_version",
            "resource",
            "input_hash",
        ] {
            let mut value = request(case);
            value["producer_pin"][field] = match field {
                "model_hash" | "input_hash" => format!("sha256:{}", "e".repeat(64)).into(),
                "resource" => json!({"max_threads":256,"max_memory_mb":512,"max_batch_items":1}),
                _ => "new-producer".into(),
            };
            assert!(bind(&value.to_string(), &f, case).is_err(), "{field}");
        }
    }

    #[test]
    fn unchanged_voice_upgrade_and_unapproved_target_need_explicit_admission() {
        let f = fixture();
        let mut case = f["cases"][0].clone();
        let mut value = request(&case);
        case["voice"]["voice_version"] = "new-version".into();
        value["voice"] = case["voice"].clone();
        value["producer_pin"]["voice_version"] = "new-version".into();
        value["producer_pin"]["voice_hash"] = object_hash(&case["voice"]).unwrap().into();
        assert!(bind(&value.to_string(), &f, &case).is_err());
        let mut case = f["cases"][1].clone();
        let mut value = request(&case);
        case["voice"]["approved"] = false.into();
        value["voice"] = case["voice"].clone();
        assert!(bind(&value.to_string(), &f, &case).is_err());
        let mut f = f;
        let case = f["cases"][0].clone();
        let mut value = request(&case);
        f["config"]["max_text_chars"] = 513.into();
        value["config"] = f["config"].clone();
        assert!(bind(&value.to_string(), &f, &case).is_err());
    }

    #[test]
    fn rational_equality_avoids_cross_product_overflow_and_refuses_aliases() {
        let max = i64::MAX.to_string();
        let base = json!({"numerator":max,"denominator":(i64::MAX-1).to_string()});
        let huge = seconds(&max.clone().into(), &base).unwrap();
        assert_eq!(huge, ((i64::MAX as u128).pow(2), (i64::MAX - 1) as u128));
        assert_ne!(
            huge,
            seconds(&(i64::MAX - 1).to_string().into(), &base).unwrap()
        );
        assert_eq!(
            seconds(
                &"1000".into(),
                &json!({"numerator":"1","denominator":"1000"})
            )
            .unwrap(),
            seconds(
                &"48000".into(),
                &json!({"numerator":"1","denominator":"48000"})
            )
            .unwrap()
        );
        for base in [
            json!({"numerator":"2","denominator":"2000"}),
            json!({"numerator":"1","denominator":"0"}),
            json!({"numerator":"01","denominator":"1000"}),
        ] {
            assert!(seconds(&"1000".into(), &base).is_err());
        }
        let mut f = fixture();
        let case = f["cases"][0].clone();
        f["original_input"]["start"]["ticks"] = "1".into();
        assert!(bind(case["request_utf8"].as_str().unwrap(), &f, &case).is_err());
    }

    #[test]
    fn corrected_text_limits_and_packet_byte_bound_are_required() {
        let f = fixture();
        let original = &f["cases"][0];
        for size in [512, 513] {
            let before = snapshot(&f["before"]);
            let changed = "đ".repeat(size);
            let after = before
                .correction("finding-a", Some(&changed), None, ReviewMode::Dubbed)
                .unwrap();
            let mut case = original.clone();
            case["after"] = serde_json::from_str(after.json()).unwrap();
            let mut value = request(&case);
            value["after_snapshot_sha256"] = after.sha256().into();
            value["tts_input"]["text"] = changed.clone().into();
            value["tts_input"]["normalized_text"] = changed.into();
            assert_eq!(bind(&value.to_string(), &f, &case).is_ok(), size == 512);
        }
        let oversized =
            original["request_utf8"].as_str().unwrap().to_owned() + &" ".repeat(MAX_REQUEST);
        assert!(bind(&oversized, &f, original).is_err());
    }

    #[test]
    fn duplicate_keys_trailing_data_and_unknown_closed_fields_refuse() {
        let f = fixture();
        let case = &f["cases"][0];
        let raw = case["request_utf8"].as_str().unwrap();
        for bad in [
            raw.replacen(
                "\"kind\":\"review_cue_synthesis_input\"",
                "\"kind\":\"review_cue_synthesis_input\",\"kind\":\"review_cue_synthesis_input\"",
                1,
            ),
            raw.to_owned() + " {}",
        ] {
            assert!(bind(&bad, &f, case).is_err());
        }
        let mut value = request(case);
        value["producer_pin"]["unknown"] = true.into();
        assert!(bind(&value.to_string(), &f, case).is_err());
        let mut value = request(case);
        value["tts_input"]["unknown"] = true.into();
        assert!(bind(&value.to_string(), &f, case).is_err());
    }
}
