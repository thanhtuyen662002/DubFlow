//! Read-only discovery for the installed desktop. A current graph is physically
//! readmitted; a failed graph is never hidden behind an original snapshot.
use super::review_document::{decode, text, tick, AdmittedSnapshot};
use super::review_publication::token;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::DurableStore;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}

/// Separate display projection: the durable snapshot bytes/token are never
/// rewritten. Public revision remains the actual native decimal string.
fn project(snapshot: &AdmittedSnapshot, current: &Value, mode: &str) -> SupervisorResult<Value> {
    let revision = tick(&current["revision"], false)?;
    if snapshot.job() != text(&current["job_id"], 256)?
        || snapshot.revision() != revision
        || snapshot.sha256() != text(&current["snapshot_sha256"], 71)?
        || !["b1", "b2"].contains(&mode)
    {
        return Err(invalid(
            "desktop snapshot differs from its actual native current token",
        ));
    }
    let value = decode(snapshot.json(), 64 * 1024 * 1024)?;
    Ok(
        json!({"schema_version":3,"kind":"review_current_snapshot","job_id":snapshot.job(),
        "current_token":current,"mode":mode,"time_base":value["time_base"],"findings":value["findings"],
        "last_plan":value["last_plan"],"correction_ready":false}),
    )
}

pub(super) fn read(store: &DurableStore, data: &Path, job: &str) -> SupervisorResult<Value> {
    let current = store.review_graph_state(job)?;
    let snapshot = if let Some(current) = &current {
        let admitted = super::review_effective::read_current(store, data, current)?;
        let packet = admitted.packet()?;
        let source = decode(
            packet["source_graph_json"]
                .as_str()
                .ok_or_else(|| invalid("desktop source graph missing"))?,
            64 * 1024 * 1024,
        )?;
        let mode = text(&source["mode"], 2)?;
        Some(project(admitted.snapshot(), &token(current), mode)?)
    } else {
        None
    };
    if store.review_graph_state(job)? != current {
        return Err(invalid(
            "Review state changed during desktop discovery; reload",
        ));
    }
    Ok(
        json!({"schema_version":1,"kind":"review_native_desktop_state","job_id":job,
        "status":if snapshot.is_some(){"current"}else{"original_only"},"snapshot":snapshot}),
    )
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("desktop state argument is not UTF-8"))?;
        if !["--data-root", "--job-id"].contains(&flag.as_str()) || values.contains_key(&flag) {
            return Err(invalid("unknown or duplicate desktop state argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("desktop state argument missing"))?;
        values.insert(flag, value);
    }
    let data = PathBuf::from(
        values
            .get("--data-root")
            .ok_or_else(|| invalid("desktop state control root missing"))?,
    );
    let job = values
        .get("--job-id")
        .and_then(|v| v.to_str())
        .ok_or_else(|| invalid("desktop state job missing"))?;
    super::validate_job_id(job)?;
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("desktop state requires an absolute control root"));
    }
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let store = DurableStore::open_read_only(&db)?;
    println!("{}", read(&store, &data, job)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;

    #[test]
    fn desktop_projection_preserves_revision_above_js_range_without_rewriting_snapshot_identity() {
        with_admitted_original(|_, _, state| {
            let original =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let snapshot =
                super::super::review_active::at_revision(&original, 9_007_199_254_740_993).unwrap();
            let raw = snapshot.json().to_owned();
            let mut t = token(state);
            t["revision"] = "9007199254740993".into();
            t["snapshot_sha256"] = snapshot.sha256().into();
            let view = project(&snapshot, &t, "b1").unwrap();
            assert_eq!(view["current_token"]["revision"], "9007199254740993");
            assert_eq!(view["current_token"], t);
            assert_eq!(snapshot.json(), raw);
            assert!(view.get("revision").is_none());
            assert_eq!(view["correction_ready"], false);
            t["revision"] = "0".into();
            assert!(project(&snapshot, &t, "b1").is_err());
        });
    }

    #[test]
    fn desktop_current_discovery_refuses_corrupt_graph_instead_of_falling_back_to_original() {
        with_admitted_original(|store, data, state| {
            let packet = read(store, data, &state.token.job_id).unwrap();
            assert_eq!(packet["status"], "current");
            assert_eq!(packet["snapshot"]["current_token"], token(state));
            let before =
                AdmittedSnapshot::read(&state.snapshot_json, &state.token.job_id, 0).unwrap();
            let after = before
                .correction(
                    "finding-a",
                    Some("Changed"),
                    None,
                    super::super::review_document::ReviewMode::Subtitles,
                )
                .unwrap();
            let corrupt = store
                .commit_review_graph(&state.token, after.json(), r#"{"kind":"unknown"}"#, 10)
                .unwrap();
            assert!(read(store, data, &state.token.job_id).is_err());
            assert_eq!(
                store.review_graph_state(&state.token.job_id).unwrap(),
                Some(corrupt)
            );
        });
    }
}
