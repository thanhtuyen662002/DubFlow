//! Read-only native correction preview for the actual current generation.
//! A logical proposal is never a published snapshot or permission to infer.
use super::review_document::{closed, decode, identity, text, tick, AdmittedSnapshot, ReviewMode};
use super::review_publication::token;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

const LIMIT: usize = 64 * 1024 * 1024;
fn invalid(s: &str) -> SupervisorError {
    SupervisorError::Invalid(s.into())
}

fn changes(value: &Value) -> SupervisorResult<(Option<&str>, Option<&str>)> {
    let fields = value
        .as_object()
        .ok_or_else(|| invalid("current changes must be an object"))?;
    if fields.is_empty()
        || fields.len() > 2
        || fields
            .keys()
            .any(|k| !["translation", "voice"].contains(&k.as_str()))
    {
        return Err(invalid(
            "current changes have missing or unsupported fields",
        ));
    }
    let translation = value
        .get("translation")
        .map(|v| text(v, 8192))
        .transpose()?;
    let voice = value.get("voice").map(identity).transpose()?;
    if translation.is_some_and(|v| v != v.trim()) || voice.is_some_and(|v| v != v.trim()) {
        return Err(invalid("current changes must use exact normalized text"));
    }
    Ok((translation, voice))
}

fn proposal(
    before: &AdmittedSnapshot,
    current: &Value,
    mode: &str,
    finding: &str,
    cue: &str,
    change: &Value,
) -> SupervisorResult<Value> {
    closed(
        current,
        &[
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ],
    )?;
    if current["job_id"] != before.job()
        || tick(&current["revision"], false)? != before.revision()
        || current["snapshot_sha256"] != before.sha256()
    {
        return Err(invalid(
            "current correction preview token differs from admitted content",
        ));
    }
    identity(&json!(finding))?;
    identity(&json!(cue))?;
    let selected = before.finding(finding)?;
    if selected["cue_id"] != cue || selected["range"].is_null() {
        return Err(invalid(
            "current correction finding is not this canonical cue",
        ));
    }
    let (translation, voice) = changes(change)?;
    if translation.is_some_and(|v| selected["translated_text"] == v)
        || voice.is_some_and(|v| selected["voice"] == v)
    {
        return Err(invalid("current changes must omit unchanged fields"));
    }
    let kind = match mode {
        "b1" => ReviewMode::Subtitles,
        "b2" => ReviewMode::Dubbed,
        _ => return Err(invalid("current correction mode missing")),
    };
    let after = before.correction(finding, translation, voice, kind)?;
    before.validate_correction(&after, kind)?;
    let value = decode(after.json(), LIMIT)?;
    Ok(
        json!({"schema_version":1,"kind":"review_current_correction_plan","job_id":before.job(),
        "current_token":current,"finding_id":finding,"cue_id":cue,"changes":change,"mode":mode,
        "plan":value["last_plan"],"candidate_revision":after.revision().to_string(),"candidate_snapshot_sha256":after.sha256(),
        "read_only":true,"processing_release_admitted":false,"active_graph_published":false,"execution_ready":false}),
    )
}

pub(super) fn read(
    store: &DurableStore,
    data: &Path,
    expected: &Value,
    finding: &str,
    cue: &str,
    change: &Value,
) -> SupervisorResult<Value> {
    closed(
        expected,
        &[
            "job_id",
            "revision",
            "snapshot_sha256",
            "graph_sha256",
            "original_manifest_sha256",
        ],
    )?;
    let job = identity(&expected["job_id"])?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("current Review graph missing"))?;
    if token(&state) != *expected {
        return Err(invalid("current correction preview token changed; reload"));
    }
    let current = super::review_effective::read_current(store, data, &state)?;
    let packet = current.packet()?;
    if packet["generation_dependencies_complete"] != true {
        return Err(invalid("current correction dependencies are incomplete"));
    }
    let source = decode(
        packet["source_graph_json"]
            .as_str()
            .ok_or_else(|| invalid("current correction source missing"))?,
        LIMIT,
    )?;
    let result = proposal(
        current.snapshot(),
        expected,
        text(&source["mode"], 2)?,
        finding,
        cue,
        change,
    )?;
    unchanged(store, &state)?;
    Ok(result)
}
fn unchanged(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("current correction changed during preview; reload"));
    }
    Ok(())
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    const KEYS: [&str; 9] = [
        "--data-root",
        "--job-id",
        "--revision",
        "--snapshot-sha256",
        "--graph-sha256",
        "--original-manifest-sha256",
        "--finding-id",
        "--cue-id",
        "--changes-json",
    ];
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("current plan flag is not UTF-8"))?;
        if !KEYS.contains(&flag.as_str()) || values.contains_key(&flag) {
            return Err(invalid("unknown or duplicate current plan argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("current plan value missing"))?
            .into_string()
            .map_err(|_| invalid("current plan value is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| invalid("current plan requires the complete token and exact changes"))
    };
    for key in KEYS {
        required(key)?;
    }
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("current plan control root must be absolute"));
    }
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    let expected = json!({"job_id":job,"revision":required("--revision")?,"snapshot_sha256":required("--snapshot-sha256")?,
        "graph_sha256":required("--graph-sha256")?,"original_manifest_sha256":required("--original-manifest-sha256")?});
    let store = DurableStore::open_read_only(&db)?;
    let change = decode(required("--changes-json")?, 32768)?;
    let result = read(
        &store,
        &data,
        &expected,
        required("--finding-id")?,
        required("--cue-id")?,
        &change,
    )?;
    super::emit_value(&mut std::io::stdout().lock(), result)
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;

    #[test]
    fn current_b1_preview_preserves_actual_wide_revision_and_durable_state_without_tts_or_voice() {
        with_admitted_original(|store, data, state| {
            let snapshot =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let wide =
                super::super::review_active::at_revision(&snapshot, 9_007_199_254_740_993).unwrap();
            let mut current = token(state);
            current["revision"] = json!(wide.revision().to_string());
            current["snapshot_sha256"] = json!(wide.sha256());
            let cue = wide.finding("finding-a").unwrap()["cue_id"]
                .as_str()
                .unwrap();
            let raw = wide.json().to_owned();
            let p = proposal(
                &wide,
                &current,
                "b1",
                "finding-a",
                cue,
                &json!({"translation":"Changed current caption"}),
            )
            .unwrap();
            assert_eq!(p["candidate_revision"], "9007199254740994");
            assert_eq!(
                p["plan"]["stages_to_rerun"],
                json!(["translation", "subtitles", "render", "qc", "export"])
            );
            assert_eq!(p["current_token"], current);
            assert_eq!(p["execution_ready"], false);
            assert_eq!(wide.json(), raw);
            assert!(proposal(
                &wide,
                &current,
                "b1",
                "finding-a",
                cue,
                &json!({"voice":"invented-voice"})
            )
            .is_err());
            let mut stale = token(state);
            stale["graph_sha256"] = json!(format!("sha256:{}", "a".repeat(64)));
            assert!(read(
                store,
                data,
                &stale,
                "finding-a",
                cue,
                &json!({"translation":"Changed"})
            )
            .is_err());
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
    fn b2_preview_uses_native_translation_and_voice_only_scope_and_refuses_substitution() {
        let f = decode(
            include_str!("../../../../tests/review/native_cue_request_v1.json"),
            LIMIT,
        )
        .unwrap();
        let before = AdmittedSnapshot::read(
            &f["before"].to_string(),
            f["before"]["job_id"].as_str().unwrap(),
            0,
        )
        .unwrap();
        let current = json!({"job_id":before.job(),"revision":"0","snapshot_sha256":before.sha256(),"graph_sha256":format!("sha256:{}","b".repeat(64)),"original_manifest_sha256":f["manifest_sha256"]});
        let translated = proposal(
            &before,
            &current,
            "b2",
            "finding-a",
            "cue-a",
            &json!({"translation":"New words"}),
        )
        .unwrap();
        assert_eq!(
            translated["plan"]["stages_to_rerun"],
            json!([
                "translation",
                "subtitles",
                "tts",
                "mix",
                "render",
                "qc",
                "export"
            ])
        );
        let voiced = proposal(
            &before,
            &current,
            "b2",
            "finding-a",
            "cue-a",
            &json!({"voice":"vi-hai-dang-vieneu3-v1"}),
        )
        .unwrap();
        assert_eq!(
            voiced["plan"]["stages_to_rerun"],
            json!(["tts", "mix", "render", "qc", "export"])
        );
        for change in [
            json!({}),
            json!({"translation":null}),
            json!({"translation":" New words"}),
            json!({"path":"foreign"}),
        ] {
            assert!(proposal(&before, &current, "b2", "finding-a", "cue-a", &change).is_err());
        }
        assert!(proposal(
            &before,
            &current,
            "b2",
            "finding-a",
            "other-cue",
            &json!({"translation":"New words"})
        )
        .is_err());
    }
}
