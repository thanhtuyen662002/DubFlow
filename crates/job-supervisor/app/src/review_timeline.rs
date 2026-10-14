//! Pure timeline derivation from native-admitted archived bytes and snapshots.
//! No media/model I/O, SQLite mutation or permission to publish a graph.
use super::review_document::{
    canonical, closed, decode, identity, tick, AdmittedSnapshot, ReviewMode,
};
use super::{SupervisorError, SupervisorResult};
use serde_json::Value;
use std::collections::HashMap;

const LIMIT: usize = 64 * 1024 * 1024;

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

fn number(value: &Value, positive: bool) -> SupervisorResult<u64> {
    let result = if let Some(n) = value.as_u64() {
        n
    } else {
        tick(value, positive)?
    };
    if result > i64::MAX as u64 || (positive && result == 0) {
        return Err(invalid(
            "canonical timeline number exceeds its signed64 bound",
        ));
    }
    Ok(result)
}

fn integer(value: &Value) -> SupervisorResult<u64> {
    value
        .as_u64()
        .filter(|n| *n <= i64::MAX as u64)
        .ok_or_else(|| invalid("canonical cue milliseconds require bounded integers"))
}

// Quotient/remainder avoids multiplying an already-wide rational by 1000.
fn exact_ms(ticks: u64, numerator: u64, denominator: u64) -> SupervisorResult<u64> {
    let product = u128::from(ticks) * u128::from(numerator);
    let denominator = u128::from(denominator);
    let remainder = (product % denominator) * 1000;
    let whole = (product / denominator).checked_mul(1000);
    if remainder % denominator != 0 {
        return Err(invalid(
            "Review cue is not exactly representable in milliseconds",
        ));
    }
    whole
        .and_then(|n| n.checked_add(remainder / denominator))
        .filter(|n| *n <= i64::MAX as u128)
        .map(|n| n as u64)
        .ok_or_else(|| invalid("Review cue millisecond conversion exceeds its bound"))
}

fn within_duration(ms: u64, ticks: u64, numerator: u64, denominator: u64) -> bool {
    let product = u128::from(ticks) * u128::from(numerator);
    let denominator = u128::from(denominator);
    let seconds = product / denominator;
    let whole = u128::from(ms / 1000);
    whole < seconds
        || (whole == seconds
            && u128::from(ms % 1000) * denominator <= (product % denominator) * 1000)
}

pub(super) fn derive(
    original: &str,
    before: &AdmittedSnapshot,
    after: &AdmittedSnapshot,
    mode: ReviewMode,
) -> SupervisorResult<String> {
    before.validate_correction(after, mode)?;
    let (mut timeline, by_id) = admit_timeline(original, before)?;
    let old = before.finding(after.plan_finding_id()?)?;
    let new = after.finding(after.plan_finding_id()?)?;
    if old["translated_text"] == new["translated_text"] {
        return Ok(original.into());
    }
    let index = by_id[old["cue_id"].as_str().unwrap()];
    timeline["cues"][index]["translated_text"] = new["translated_text"].clone();
    let mut encoded = String::new();
    canonical(&timeline, &mut encoded)?;
    Ok(encoded)
}

fn admit_timeline(
    original: &str,
    before: &AdmittedSnapshot,
) -> SupervisorResult<(Value, HashMap<String, usize>)> {
    let timeline = decode(original, LIMIT)?;
    closed(
        &timeline,
        &[
            "schema_version",
            "time_base",
            "duration_ticks",
            "cues",
            "source_clock_mapping",
        ],
    )?;
    if timeline["schema_version"].as_u64() != Some(1) {
        return Err(invalid(
            "unknown canonical timeline needs explicit compatibility",
        ));
    }
    closed(&timeline["time_base"], &["numerator", "denominator"])?;
    let numerator = number(&timeline["time_base"]["numerator"], true)?;
    let denominator = number(&timeline["time_base"]["denominator"], true)?;
    let duration = number(&timeline["duration_ticks"], true)?;
    let cues = timeline["cues"]
        .as_array()
        .filter(|c| !c.is_empty() && c.len() <= 10000)
        .ok_or_else(|| invalid("canonical timeline cue count exceeds its bound"))?;
    let mut by_id = HashMap::new();
    for (index, cue) in cues.iter().enumerate() {
        closed(
            cue,
            &[
                "cue_id",
                "start_ms",
                "end_ms",
                "source_text",
                "translated_text",
                "confidence",
            ],
        )?;
        let id = identity(&cue["cue_id"])?;
        let start = integer(&cue["start_ms"])?;
        let end = integer(&cue["end_ms"])?;
        let source = cue["source_text"]
            .as_str()
            .filter(|s| !s.trim().is_empty() && s.chars().count() <= 16384);
        let translated = cue["translated_text"]
            .as_str()
            .filter(|s| !s.trim().is_empty());
        let confidence = cue["confidence"]
            .as_f64()
            .filter(|c| c.is_finite() && (0.0..=1.0).contains(c));
        if id.chars().count() > 128
            || by_id.insert(id.to_owned(), index).is_some()
            || start >= end
            || !within_duration(end, duration, numerator, denominator)
            || source.is_none()
            || translated.is_none()
            || confidence.is_none()
        {
            return Err(invalid("invalid or duplicate canonical timeline cue"));
        }
    }
    let snapshot = decode(before.json(), LIMIT)?;
    let review_num = tick(&before.time_base()["numerator"], true)?;
    let review_den = tick(&before.time_base()["denominator"], true)?;
    for finding in snapshot["findings"].as_array().unwrap() {
        let Some(id) = finding["cue_id"].as_str() else {
            continue;
        };
        let index = by_id
            .get(id)
            .ok_or_else(|| invalid("Review invents a canonical timeline cue"))?;
        let cue = &cues[*index];
        if cue["source_text"] != finding["source_text"]
            || cue["translated_text"] != finding["translated_text"]
            || integer(&cue["start_ms"])?
                != exact_ms(
                    tick(&finding["range"]["start_ticks"], false)?,
                    review_num,
                    review_den,
                )?
            || integer(&cue["end_ms"])?
                != exact_ms(
                    tick(&finding["range"]["end_ticks"], false)?,
                    review_num,
                    review_den,
                )?
        {
            return Err(invalid(
                "Review differs from archived canonical timeline content or range",
            ));
        }
    }
    Ok((timeline, by_id))
}

pub(super) fn derive_effective(
    original: &str,
    origin: &AdmittedSnapshot,
    current: &str,
    before: &AdmittedSnapshot,
    after: &AdmittedSnapshot,
    mode: ReviewMode,
) -> SupervisorResult<String> {
    if origin.revision() != 0
        || origin.job() != before.job()
        || origin.time_base() != before.time_base()
    {
        return Err(invalid(
            "effective timeline requires a separately admitted original archive",
        ));
    }
    let (mut expected, index) = admit_timeline(original, origin)?;
    let (actual, _) = admit_timeline(current, before)?;
    let snapshot = decode(before.json(), LIMIT)?;
    for finding in snapshot["findings"].as_array().unwrap() {
        if let Some(id) = finding["cue_id"].as_str() {
            let i = index
                .get(id)
                .ok_or_else(|| invalid("current timeline invents an original cue"))?;
            expected["cues"][*i]["translated_text"] = finding["translated_text"].clone();
        }
    }
    let mut expected_raw = String::new();
    let mut actual_raw = String::new();
    canonical(&expected, &mut expected_raw)?;
    canonical(&actual, &mut actual_raw)?;
    if expected_raw != actual_raw {
        return Err(invalid(
            "current timeline changes source/clock/confidence or loses earlier edits",
        ));
    }
    derive(current, before, after, mode)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/review/native_corrected_timeline_v1.json"
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

    #[test]
    fn native_translation_matches_python_oracle_and_voice_keeps_exact_bytes() {
        let f = fixture();
        let before = snapshot(&f["before"]);
        let raw = f["original_json"].as_str().unwrap();
        let translated = derive(
            raw,
            &before,
            &snapshot(&f["translated"]),
            ReviewMode::Dubbed,
        )
        .unwrap();
        assert_eq!(translated, f["expected_translated_json"].as_str().unwrap());
        assert_eq!(
            derive(raw, &before, &snapshot(&f["voice"]), ReviewMode::Dubbed).unwrap(),
            raw
        );
        assert!(before.revision() > (1_u64 << 53));
    }

    #[test]
    fn b1_has_no_tts_or_mix_authority_and_preserves_unwarned_cues_and_confidence() {
        let f = fixture();
        let mut value = f["before"].clone();
        for finding in value["findings"].as_array_mut().unwrap() {
            finding["voice"] = Value::Null;
        }
        let before = snapshot(&value);
        let after = before
            .correction("risk-a", Some("Chào bạn"), None, ReviewMode::Subtitles)
            .unwrap();
        let raw = derive(
            f["original_json"].as_str().unwrap(),
            &before,
            &after,
            ReviewMode::Subtitles,
        )
        .unwrap();
        assert_eq!(raw, f["expected_translated_json"].as_str().unwrap());
        let proposal: Value = serde_json::from_str(after.json()).unwrap();
        assert_eq!(
            proposal["last_plan"]["stages_to_rerun"],
            json!(["translation", "subtitles", "render", "qc", "export"])
        );
        assert!(before
            .correction("risk-a", None, Some("voice-b"), ReviewMode::Subtitles)
            .is_err());
    }

    #[test]
    fn timeline_version_fields_cues_source_intervals_and_proposal_substitutions_refuse() {
        let f = fixture();
        let before = snapshot(&f["before"]);
        let after = snapshot(&f["translated"]);
        for case in 0..10 {
            let mut raw: Value =
                serde_json::from_str(f["original_json"].as_str().unwrap()).unwrap();
            match case {
                0 => raw["schema_version"] = json!(2),
                1 => {
                    raw.as_object_mut().unwrap().remove("source_clock_mapping");
                }
                2 => raw["cues"][0]["source_text"] = json!("Foreign"),
                3 => raw["cues"][0]["translated_text"] = json!("Foreign"),
                4 => raw["cues"][0]["start_ms"] = json!(1001),
                5 => raw["cues"][0]["start_ms"] = json!(true),
                6 => raw["cues"][2]["cue_id"] = json!("cue-a"),
                7 => raw["cues"][2]["confidence"] = json!(-0.1),
                8 => raw["duration_ticks"] = json!(1),
                _ => raw["time_base"]["denominator"] = json!(0),
            }
            assert!(
                derive(&raw.to_string(), &before, &after, ReviewMode::Dubbed).is_err(),
                "case{case}"
            );
        }
        let mut bad = f["translated"].clone();
        bad["findings"][2]["translated_text"] = json!("Other changed cue");
        assert!(derive(
            f["original_json"].as_str().unwrap(),
            &before,
            &snapshot(&bad),
            ReviewMode::Dubbed
        )
        .is_err());
        let duplicate = f["original_json"].as_str().unwrap().replacen(
            "\"schema_version\": 1",
            "\"schema_version\":1,\"schema_version\":1",
            1,
        );
        assert!(derive(&duplicate, &before, &after, ReviewMode::Dubbed).is_err());
    }

    #[test]
    fn rational_conversion_is_exact_and_wide_products_do_not_overflow() {
        assert_eq!(exact_ms(48000, 1, 48000).unwrap(), 1000);
        assert!(exact_ms(48001, 1, 48000).is_err());
        assert!(exact_ms(i64::MAX as u64, i64::MAX as u64, 1).is_err());
        assert!(within_duration(9000, 14400000, 1, 1000));
        assert!(!within_duration(14400001, 14400000, 1, 1000));
        assert!(!within_duration(1, 1, 1, i64::MAX as u64));
    }

    fn effective_fixture() -> Value {
        decode(
            include_str!("../../../../tests/review/native_effective_timeline_v1.json"),
            LIMIT,
        )
        .unwrap()
    }

    #[test]
    fn current_b1_b2_timeline_matches_python_oracle_and_voice_keeps_current_bytes_and_counter() {
        for case in effective_fixture()["cases"].as_array().unwrap() {
            let mode = if case["mode"] == "b1" {
                ReviewMode::Subtitles
            } else {
                ReviewMode::Dubbed
            };
            let before = snapshot(&case["before"]);
            let after = snapshot(&case["after"]);
            let current = case["current_json"].as_str().unwrap();
            let result = derive_effective(
                case["original_json"].as_str().unwrap(),
                &snapshot(&case["origin"]),
                current,
                &before,
                &after,
                mode,
            )
            .unwrap();
            let mut expected = String::new();
            canonical(
                &decode(case["expected_json"].as_str().unwrap(), LIMIT).unwrap(),
                &mut expected,
            )
            .unwrap();
            let mut actual = String::new();
            canonical(&decode(&result, LIMIT).unwrap(), &mut actual).unwrap();
            assert_eq!(actual, expected);
            assert_eq!(after.revision(), before.revision() + 1);
            assert_eq!(case["token"]["revision"], before.revision().to_string());
            let original = decode(case["original_json"].as_str().unwrap(), LIMIT).unwrap();
            let final_value = decode(&result, LIMIT).unwrap();
            for (old, new) in original["cues"]
                .as_array()
                .unwrap()
                .iter()
                .zip(final_value["cues"].as_array().unwrap())
            {
                assert_eq!(
                    old["confidence"].as_f64().unwrap().to_bits(),
                    new["confidence"].as_f64().unwrap().to_bits()
                );
            }
            if before.revision() > (1_u64 << 53) {
                assert_eq!(result, current);
                assert_ne!(current, case["original_json"].as_str().unwrap());
            } else {
                assert_eq!(final_value["cues"][0]["translated_text"], "Chào bạn");
                assert_eq!(
                    final_value["cues"][1]["translated_text"],
                    "Chúc bạn bình an"
                );
                assert_eq!(final_value["cues"][2], original["cues"][2]);
            }
        }
    }

    #[test]
    fn current_timeline_rejects_lost_caption_signed_zero_source_clock_and_foreign_original() {
        let f = effective_fixture();
        let case = &f["cases"][1];
        let before = snapshot(&case["before"]);
        let after = snapshot(&case["after"]);
        let original = case["original_json"].as_str().unwrap();
        let origin = snapshot(&case["origin"]);
        for mutation in 0..7 {
            let mut current = decode(case["current_json"].as_str().unwrap(), LIMIT).unwrap();
            match mutation {
                0 => current["cues"][0]["translated_text"] = json!("Xin chào"),
                1 => current["cues"][2]["confidence"] = json!(0.0),
                2 => current["cues"][0]["confidence"] = json!(0.983856439590454),
                3 => current["source_clock_mapping"]["start_pts"] = json!("9001"),
                4 => current["cues"][2]["translated_text"] = json!("Lost unwarned caption"),
                5 => {
                    current["cues"].as_array_mut().unwrap().pop();
                }
                _ => current["duration_ticks"] = json!(14400001),
            }
            assert!(
                derive_effective(
                    original,
                    &origin,
                    &current.to_string(),
                    &before,
                    &after,
                    ReviewMode::Dubbed
                )
                .is_err(),
                "mutation {mutation}"
            );
        }
        let mut foreign = case["origin"].clone();
        foreign["revision"] = json!(2);
        assert!(derive_effective(
            original,
            &snapshot(&foreign),
            case["current_json"].as_str().unwrap(),
            &before,
            &after,
            ReviewMode::Dubbed
        )
        .is_err());
    }
}
