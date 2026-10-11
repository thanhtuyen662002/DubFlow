//! Native closed-schema Review snapshot admission and one-cue proposals.
//! Proposals authorize no file reuse or SQLite mutation. The graph caller must
//! bind canonical export cues, producers and physical assets before publication.
use super::{SupervisorError, SupervisorResult};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Map, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fmt;

const MAX_BYTES: usize = 64 * 1024 * 1024;
const MAX_REVISION: u64 = i64::MAX as u64;
const REASONS: [&str; 6] = [
    "ASR_OCR_CONFLICT",
    "SPEAKER_UNCERTAINTY",
    "TRANSLATION_ANOMALY",
    "TTS_FAILURE",
    "INPAINT_RISK",
    "SYNC_QC_WARNING",
];

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

pub(super) fn decode(raw: &str, limit: usize) -> SupervisorResult<Value> {
    if raw.is_empty() || raw.len() > limit.min(MAX_BYTES) {
        return Err(invalid("Review JSON exceeds byte limit"));
    }
    let mut decoder = serde_json::Deserializer::from_str(raw);
    let Strict(value) = Strict::deserialize(&mut decoder)?;
    decoder.end()?;
    Ok(value)
}

// serde_json::Value alone silently overwrites repeated object keys. Preserve
// that ambiguity as an error at every depth, including nullable nested plans.
struct Strict(Value);
struct StrictVisitor;
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        d.deserialize_any(StrictVisitor)
    }
}
impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Strict;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("unambiguous bounded JSON")
    }
    fn visit_unit<E: de::Error>(self) -> Result<Strict, E> {
        Ok(Strict(Value::Null))
    }
    fn visit_bool<E: de::Error>(self, v: bool) -> Result<Strict, E> {
        Ok(Strict(Value::Bool(v)))
    }
    fn visit_i64<E: de::Error>(self, v: i64) -> Result<Strict, E> {
        Ok(Strict(Value::Number(v.into())))
    }
    fn visit_u64<E: de::Error>(self, v: u64) -> Result<Strict, E> {
        Ok(Strict(Value::Number(v.into())))
    }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Strict, E> {
        Number::from_f64(v)
            .map(|n| Strict(Value::Number(n)))
            .ok_or_else(|| E::custom("nonfinite JSON"))
    }
    fn visit_str<E: de::Error>(self, v: &str) -> Result<Strict, E> {
        Ok(Strict(Value::String(v.into())))
    }
    fn visit_string<E: de::Error>(self, v: String) -> Result<Strict, E> {
        Ok(Strict(Value::String(v)))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
        let mut items = Vec::new();
        while let Some(Strict(v)) = a.next_element()? {
            items.push(v);
        }
        Ok(Strict(Value::Array(items)))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
        let mut fields = Map::new();
        while let Some((key, Strict(v))) = a.next_entry::<String, Strict>()? {
            if fields.insert(key, v).is_some() {
                return Err(de::Error::custom("duplicate Review JSON key"));
            }
        }
        Ok(Strict(Value::Object(fields)))
    }
}

pub(super) fn closed<'a>(
    value: &'a Value,
    keys: &[&str],
) -> SupervisorResult<&'a Map<String, Value>> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("Review field must be an object"))?;
    if object.len() != keys.len() || keys.iter().any(|k| !object.contains_key(*k)) {
        return Err(invalid("Review object has missing or unsupported fields"));
    }
    Ok(object)
}

pub(super) fn text(value: &Value, limit: usize) -> SupervisorResult<&str> {
    let s = value
        .as_str()
        .ok_or_else(|| invalid("Review text must be a string"))?;
    if s.is_empty()
        || s.trim() != s
        || s.chars().count() > limit
        || s.chars().any(|c| (c as u32) < 32)
    {
        return Err(invalid(
            "Review text is empty, unnormalized or outside its bound",
        ));
    }
    Ok(s)
}

pub(super) fn identity(value: &Value) -> SupervisorResult<&str> {
    let s = text(value, 256)?;
    if !s
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
    {
        return Err(invalid("Review identity is invalid"));
    }
    Ok(s)
}

fn optional_text(value: &Value, limit: usize) -> SupervisorResult<Option<&str>> {
    if value.is_null() {
        Ok(None)
    } else {
        text(value, limit).map(Some)
    }
}

pub(super) fn tick(value: &Value, positive: bool) -> SupervisorResult<u64> {
    let s = value
        .as_str()
        .ok_or_else(|| invalid("Review ticks must be decimal strings"))?;
    let n = s
        .parse::<u64>()
        .map_err(|_| invalid("invalid Review tick"))?;
    if s.len() > 19 || n > MAX_REVISION || n.to_string() != s || (positive && n == 0) {
        return Err(invalid("Review tick is outside canonical signed64 range"));
    }
    Ok(n)
}

fn stages(value: &Value) -> SupervisorResult<()> {
    let list = value
        .as_array()
        .ok_or_else(|| invalid("Review stages must be an array"))?;
    if !(1..=64).contains(&list.len()) {
        return Err(invalid("Review stages exceed their bound"));
    }
    let mut seen = HashSet::new();
    for v in list {
        if !seen.insert(text(v, 256)?) {
            return Err(invalid("duplicate Review stage"));
        }
    }
    Ok(())
}

fn confidence(value: &Value) -> SupervisorResult<()> {
    if value.is_null() {
        return Ok(());
    }
    let s = text(value, 128)?;
    if s == "0" || s == "1" {
        return Ok(());
    }
    if !s.starts_with("0.")
        || s.len() < 3
        || s.ends_with('0')
        || !s[2..].bytes().all(|b| b.is_ascii_digit())
    {
        return Err(invalid(
            "Review confidence must be a canonical decimal in[0,1]",
        ));
    }
    Ok(())
}

fn validate(value: &Value, job: &str, revision: u64) -> SupervisorResult<()> {
    closed(
        value,
        &[
            "schema_version",
            "job_id",
            "revision",
            "time_base",
            "findings",
            "last_plan",
        ],
    )?;
    if value["schema_version"].as_u64() != Some(2)
        || identity(&value["job_id"])? != job
        || value["revision"].as_u64() != Some(revision)
        || revision > MAX_REVISION
    {
        return Err(invalid(
            "Review schema/job/revision differs from native binding",
        ));
    }
    closed(&value["time_base"], &["numerator", "denominator"])?;
    let mut a = tick(&value["time_base"]["numerator"], true)?;
    let mut b = tick(&value["time_base"]["denominator"], true)?;
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    if a != 1 {
        return Err(invalid("Review time base must be reduced"));
    }
    let findings = value["findings"]
        .as_array()
        .ok_or_else(|| invalid("Review findings must be an array"))?;
    if findings.len() > 100000 {
        return Err(invalid("too many Review findings"));
    }
    let mut ids = HashSet::new();
    let mut cues: HashMap<&str, [&Value; 6]> = HashMap::new();
    for f in findings {
        closed(
            f,
            &[
                "finding_id",
                "reason",
                "range",
                "source_text",
                "translated_text",
                "speaker",
                "voice",
                "output_preview",
                "confidence",
                "affected_stages",
                "cue_id",
                "warning",
                "confidence_source",
            ],
        )?;
        if !ids.insert(identity(&f["finding_id"])?) {
            return Err(invalid("duplicate Review finding identity"));
        }
        if !REASONS.contains(&text(&f["reason"], 256)?) {
            return Err(invalid("unknown Review reason"));
        }
        if !f["range"].is_null() {
            closed(&f["range"], &["start_ticks", "end_ticks"])?;
            if tick(&f["range"]["end_ticks"], false)? <= tick(&f["range"]["start_ticks"], false)? {
                return Err(invalid("Review range must be positive"));
            }
        }
        text(&f["source_text"], 8192)?;
        text(&f["translated_text"], 8192)?;
        for k in ["speaker", "voice", "confidence_source"] {
            optional_text(&f[k], 256)?;
        }
        for k in ["output_preview", "warning"] {
            optional_text(&f[k], 4096)?;
        }
        confidence(&f["confidence"])?;
        if f["confidence"].is_null() && !f["confidence_source"].is_null() {
            return Err(invalid("unknown confidence cannot claim a measured source"));
        }
        stages(&f["affected_stages"])?;
        if !f["cue_id"].is_null() {
            let id = identity(&f["cue_id"])?;
            if f["range"].is_null() {
                return Err(invalid("a bound Review cue needs an exact range"));
            }
            let scope = [
                &f["range"],
                &f["source_text"],
                &f["translated_text"],
                &f["speaker"],
                &f["voice"],
                &f["cue_id"],
            ];
            if cues.insert(id, scope).is_some_and(|old| old != scope) {
                return Err(invalid("Review findings disagree on canonical cue content"));
            }
        }
    }
    let plan = &value["last_plan"];
    if !plan.is_null() {
        closed(
            plan,
            &[
                "finding_id",
                "stages_to_rerun",
                "preserved_artifacts",
                "replacement_artifacts",
                "retain_previous_until_validated",
                "requires_full_manual_review",
            ],
        )?;
        if !ids.contains(identity(&plan["finding_id"])?)
            || plan["retain_previous_until_validated"] != true
            || plan["requires_full_manual_review"] != false
        {
            return Err(invalid("invalid Review plan binding/retention"));
        }
        for k in [
            "stages_to_rerun",
            "preserved_artifacts",
            "replacement_artifacts",
        ] {
            stages(&plan[k])?;
        }
    }
    Ok(())
}

pub(super) fn canonical(value: &Value, out: &mut String) -> SupervisorResult<()> {
    match value {
        Value::Object(object) => {
            out.push('{');
            let mut keys: Vec<_> = object.keys().collect();
            keys.sort();
            for (i, key) in keys.into_iter().enumerate() {
                if i != 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(key)?);
                out.push(':');
                canonical(&object[key], out)?;
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (i, v) in items.iter().enumerate() {
                if i != 0 {
                    out.push(',');
                }
                canonical(v, out)?;
            }
            out.push(']');
        }
        _ => out.push_str(&serde_json::to_string(value)?),
    }
    if out.len() > MAX_BYTES {
        return Err(invalid("canonical Review document exceeds byte limit"));
    }
    Ok(())
}

#[derive(Clone, Copy, Debug)]
pub(super) enum ReviewMode {
    Subtitles,
    Dubbed,
}

#[derive(Clone, Debug)]
pub(super) struct AdmittedSnapshot {
    value: Value,
    encoded: String,
}

impl AdmittedSnapshot {
    pub(super) fn read(raw: &str, job: &str, revision: u64) -> SupervisorResult<Self> {
        if raw.is_empty() || raw.len() > MAX_BYTES {
            return Err(invalid("Review JSON exceeds byte limit"));
        }
        let value = decode(raw, MAX_BYTES)?;
        validate(&value, job, revision)?;
        let mut encoded = String::new();
        canonical(&value, &mut encoded)?;
        Ok(Self { value, encoded })
    }
    pub(super) fn json(&self) -> &str {
        &self.encoded
    }
    pub(super) fn sha256(&self) -> String {
        format!("sha256:{:x}", Sha256::digest(self.encoded.as_bytes()))
    }
    pub(super) fn revision(&self) -> u64 {
        self.value["revision"].as_u64().unwrap()
    }
    pub(super) fn job(&self) -> &str {
        self.value["job_id"].as_str().unwrap()
    }

    pub(super) fn finding(&self, id: &str) -> SupervisorResult<&Value> {
        self.value["findings"]
            .as_array()
            .unwrap()
            .iter()
            .find(|f| f["finding_id"] == id)
            .ok_or_else(|| invalid("unknown Review finding"))
    }

    pub(super) fn time_base(&self) -> &Value {
        &self.value["time_base"]
    }

    pub(super) fn plan_finding_id(&self) -> SupervisorResult<&str> {
        self.value["last_plan"]["finding_id"]
            .as_str()
            .ok_or_else(|| invalid("correction plan is missing"))
    }

    fn next_revision(&self) -> SupervisorResult<u64> {
        self.revision()
            .checked_add(1)
            .filter(|v| *v <= MAX_REVISION)
            .ok_or_else(|| invalid("Review v2 revision exhausted"))
    }

    /// Pure logical plan/content proposal. Existing preview paths and risk
    /// evidence stay unchanged here because replacement media does not exist
    /// yet. The graph caller must derive the published preview/QC snapshot from
    /// validated producer results; it must not publish this proposal directly.
    /// Mode comes from the graph, never UI; model/catalog admission is required.
    pub(super) fn correction(
        &self,
        finding_id: &str,
        translation: Option<&str>,
        voice: Option<&str>,
        mode: ReviewMode,
    ) -> SupervisorResult<Self> {
        let selected = self.value["findings"]
            .as_array()
            .unwrap()
            .iter()
            .find(|f| f["finding_id"] == finding_id)
            .ok_or_else(|| invalid("unknown Review finding"))?;
        let cue = selected["cue_id"]
            .as_str()
            .ok_or_else(|| invalid("global finding cannot authorize cue regeneration"))?;
        let old_text = selected["translated_text"].as_str().unwrap();
        let new_text = translation.unwrap_or(old_text);
        text(&Value::String(new_text.into()), 8192)?;
        let old_voice = selected["voice"].as_str();
        let new_voice = voice.or(old_voice);
        if let Some(v) = new_voice {
            identity(&Value::String(v.into()))?;
        }
        let translated = old_text != new_text;
        let voiced = old_voice != new_voice;
        if !translated && !voiced {
            return Err(invalid("Review correction changes no cue content"));
        }
        let stages = match mode {
            ReviewMode::Subtitles => {
                if old_voice.is_some() || new_voice.is_some() {
                    return Err(invalid("B1 correction cannot assign an unpinned TTS voice"));
                }
                vec!["translation", "subtitles", "render", "qc", "export"]
            }
            ReviewMode::Dubbed => {
                if old_voice.is_none() || new_voice.is_none() {
                    return Err(invalid("B2 correction requires admitted voice provenance"));
                }
                if translated {
                    vec![
                        "translation",
                        "subtitles",
                        "tts",
                        "mix",
                        "render",
                        "qc",
                        "export",
                    ]
                } else {
                    vec!["tts", "mix", "render", "qc", "export"]
                }
            }
        };
        let mut next = self.value.clone();
        next["revision"] = self.next_revision()?.into();
        for f in next["findings"].as_array_mut().unwrap() {
            if f["cue_id"] == cue {
                f["translated_text"] = new_text.into();
                f["voice"] = new_voice.map(Value::from).unwrap_or(Value::Null);
            }
        }
        next["last_plan"] = json!({"finding_id":finding_id,"stages_to_rerun":stages,
            "preserved_artifacts":["asr","ocr"],"replacement_artifacts":stages.iter().map(|s| format!("replacement:{s}")).collect::<Vec<_>>(),
            "retain_previous_until_validated":true,"requires_full_manual_review":false});
        Self::read(
            &serde_json::to_string(&next)?,
            self.job(),
            self.next_revision()?,
        )
    }

    /// Re-derive the whole proposal so source/provenance/ranges, unrelated cues,
    /// finding order and exact descendants cannot be substituted by a caller.
    pub(super) fn validate_correction(
        &self,
        candidate: &Self,
        mode: ReviewMode,
    ) -> SupervisorResult<()> {
        let id = candidate.value["last_plan"]["finding_id"]
            .as_str()
            .ok_or_else(|| invalid("correction plan is missing"))?;
        let selected = candidate.value["findings"]
            .as_array()
            .unwrap()
            .iter()
            .find(|f| f["finding_id"] == id)
            .ok_or_else(|| invalid("correction finding is missing"))?;
        let expected = self.correction(
            id,
            selected["translated_text"].as_str(),
            selected["voice"].as_str(),
            mode,
        )?;
        if expected.value != candidate.value {
            return Err(invalid(
                "correction differs from the exact native cue proposal",
            ));
        }
        Ok(())
    }

    /// Target must come from the supervisor's pinned immutable undo row. This
    /// proposal restores its entire content except a new monotonic revision.
    pub(super) fn undo_proposal(&self, current: &Self) -> SupervisorResult<Self> {
        if self.job() != current.job()
            || self.value["time_base"] != current.value["time_base"]
            || self.revision() >= current.revision()
        {
            return Err(invalid(
                "undo target differs from the native prior revision",
            ));
        }
        let mut restored = self.value.clone();
        restored["revision"] = current.next_revision()?.into();
        let result = Self::read(
            &serde_json::to_string(&restored)?,
            self.job(),
            current.next_revision()?,
        )?;
        if result.json().len() > self.json().len() + 32 {
            return Err(invalid("undo exceeds its reserved canonical bytes"));
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/review/native_snapshot_v2.json"
        ))
        .unwrap()
    }
    fn read(value: &Value) -> SupervisorResult<AdmittedSnapshot> {
        AdmittedSnapshot::read(&value.to_string(), "job-native-doc", 9007199254740994)
    }
    fn base() -> AdmittedSnapshot {
        read(&fixture()["snapshot"]).unwrap()
    }

    #[test]
    fn python_fixture_admits_exact_ticks_revision_and_all_six_reasons() {
        let doc = base();
        assert_eq!(doc.revision(), 9007199254740994);
        assert_eq!(
            doc.value["findings"][0]["range"]["start_ticks"],
            "9007199254740993"
        );
        assert_eq!(doc.value["findings"].as_array().unwrap().len(), 6);
        assert_eq!(doc.sha256(), fixture()["snapshot_sha256"]);
    }

    #[test]
    fn duplicate_keys_at_root_finding_and_nullable_plan_are_refused() {
        let doc = base();
        let proposal = doc
            .correction("finding-a", Some("Đừng cử động."), None, ReviewMode::Dubbed)
            .unwrap();
        for (raw, revision) in [
            (doc.json().replacen("\"schema_version\":2", "\"schema_version\":2,\"schema_version\":2", 1), doc.revision()),
            (doc.json().replacen("\"finding_id\":\"finding-a\"", "\"finding_id\":\"finding-a\",\"finding_id\":\"finding-a\"", 1), doc.revision()),
            (proposal.json().replacen("\"retain_previous_until_validated\":true", "\"retain_previous_until_validated\":true,\"retain_previous_until_validated\":true", 1), proposal.revision()),
        ] { assert!(AdmittedSnapshot::read(&raw, doc.job(), revision).is_err()); }
    }

    #[test]
    fn closed_schema_rejects_missing_nullable_fields_unknown_versions_and_numeric_substitutions() {
        for mutate in 0..10 {
            let mut doc = fixture()["snapshot"].clone();
            match mutate {
                0 => {
                    doc["findings"][0]
                        .as_object_mut()
                        .unwrap()
                        .remove("warning");
                }
                1 => doc["findings"][0]["confidence"] = 0.4.into(),
                2 => doc["schema_version"] = 1.into(),
                3 => doc["revision"] = Value::Number(Number::from_f64(9007199254740994.0).unwrap()),
                4 => doc["revision"] = true.into(),
                5 => doc["time_base"]["numerator"] = "01".into(),
                6 => doc["time_base"]["numerator"] = "2".into(),
                7 => doc["findings"][0]["range"]["start_ticks"] = "-1".into(),
                8 => doc["findings"][0]["unexpected"] = "extension".into(),
                _ => doc["job_id"] = "other-job".into(),
            }
            assert!(read(&doc).is_err(), "case{mutate}");
        }
        assert!(AdmittedSnapshot::read(
            &(base().json().to_owned() + " {}"),
            "job-native-doc",
            9007199254740994
        )
        .is_err());
    }

    #[test]
    fn cue_consistency_unknown_confidence_and_range_admission_are_required() {
        for mutate in 0..5 {
            let mut doc = fixture()["snapshot"].clone();
            match mutate {
                0 => doc["findings"][1]["translated_text"] = "Khác câu.".into(),
                1 => doc["findings"][0]["confidence"] = Value::Null,
                2 => doc["findings"][0]["range"] = Value::Null,
                3 => doc["findings"][0]["affected_stages"] = json!(["tts", "tts"]),
                _ => {
                    doc["findings"][0]["range"]["end_ticks"] =
                        doc["findings"][0]["range"]["start_ticks"].clone()
                }
            }
            assert!(read(&doc).is_err());
        }
    }

    #[test]
    fn b2_native_proposals_equal_actual_python_corrections() {
        let doc = base();
        for (expected, translation, voice) in [
            ("translation", Some("Đừng cử động."), None),
            ("voice", None, Some("vi-hai-dang-vieneu3-v1")),
        ] {
            let next = doc
                .correction("finding-a", translation, voice, ReviewMode::Dubbed)
                .unwrap();
            assert_eq!(next.value, fixture()[expected]);
            doc.validate_correction(&next, ReviewMode::Dubbed).unwrap();
            assert_eq!(next.value["findings"][2], doc.value["findings"][2]);
            assert_eq!(
                next.value["findings"][1]["translated_text"],
                next.value["findings"][0]["translated_text"]
            );
        }
    }

    #[test]
    fn b1_translation_has_no_tts_mix_or_voice_authority() {
        let mut value = fixture()["snapshot"].clone();
        for f in value["findings"].as_array_mut().unwrap() {
            f["voice"] = Value::Null;
        }
        let doc = read(&value).unwrap();
        let next = doc
            .correction(
                "finding-a",
                Some("Đừng cử động."),
                None,
                ReviewMode::Subtitles,
            )
            .unwrap();
        assert_eq!(
            next.value["last_plan"]["stages_to_rerun"],
            json!(["translation", "subtitles", "render", "qc", "export"])
        );
        assert!(doc
            .correction(
                "finding-a",
                None,
                Some("vi-hai-dang-vieneu3-v1"),
                ReviewMode::Subtitles
            )
            .is_err());
        doc.validate_correction(&next, ReviewMode::Subtitles)
            .unwrap();
    }

    #[test]
    fn unicode_text_bounds_count_characters_and_reject_controls() {
        let doc = base();
        let exact = "đ".repeat(8192);
        assert!(doc
            .correction("finding-a", Some(&exact), None, ReviewMode::Dubbed)
            .is_ok());
        for bad in ["đ".repeat(8193), "Đừng\nđi.".into(), "  ".into()] {
            assert!(doc
                .correction("finding-a", Some(&bad), None, ReviewMode::Dubbed)
                .is_err());
        }
    }

    #[test]
    fn native_transition_rejects_changed_source_unrelated_cues_or_invented_plan() {
        let doc = base();
        let valid = doc
            .correction("finding-a", Some("Đừng cử động."), None, ReviewMode::Dubbed)
            .unwrap();
        for mutate in 0..4 {
            let mut value = valid.value.clone();
            match mutate {
                0 => value["findings"][2]["translated_text"] = "Câu khác bị sửa.".into(),
                1 => {
                    for f in value["findings"].as_array_mut().unwrap() {
                        if f["cue_id"] == "cue-a" {
                            f["source_text"] = "Forged source".into();
                        }
                    }
                }
                2 => value["last_plan"]["stages_to_rerun"] = json!(["asr", "render"]),
                _ => value["findings"].as_array_mut().unwrap().swap(0, 1),
            }
            let candidate =
                AdmittedSnapshot::read(&value.to_string(), doc.job(), valid.revision()).unwrap();
            assert!(doc
                .validate_correction(&candidate, ReviewMode::Dubbed)
                .is_err());
        }
        assert!(doc
            .correction("finding-global", Some("Sai."), None, ReviewMode::Dubbed)
            .is_err());
        assert!(doc
            .correction("finding-a", None, None, ReviewMode::Dubbed)
            .is_err());
    }

    #[test]
    fn undo_restores_complete_prior_content_with_new_revision_and_reserved_bytes() {
        let first = base();
        let second = first
            .correction("finding-a", Some("Đừng cử động."), None, ReviewMode::Dubbed)
            .unwrap();
        let third = second
            .correction(
                "finding-c",
                None,
                Some("vi-hai-dang-vieneu3-v1"),
                ReviewMode::Dubbed,
            )
            .unwrap();
        let restored = second.undo_proposal(&third).unwrap();
        let mut expected = second.value.clone();
        expected["revision"] = (third.revision() + 1).into();
        assert_eq!(restored.value, expected);
        assert!(restored.json().len() <= second.json().len() + 32);
        let all = first.undo_proposal(&restored).unwrap();
        let mut initial = first.value.clone();
        initial["revision"] = (restored.revision() + 1).into();
        assert_eq!(all.value, initial);
        assert!(first.undo_proposal(&first).is_err());
        let mut max = first.value.clone();
        max["revision"] = MAX_REVISION.into();
        let exhausted =
            AdmittedSnapshot::read(&max.to_string(), first.job(), MAX_REVISION).unwrap();
        assert!(exhausted
            .correction("finding-a", Some("Sai."), None, ReviewMode::Dubbed)
            .is_err());
        assert!(first.undo_proposal(&exhausted).is_err());
    }
}
