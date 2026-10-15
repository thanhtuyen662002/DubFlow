//! Explicit native confirmation bridge. Only the executable's own source-bound
//! installed processing release is selected; browser/root hints grant nothing.
use super::review_document::{canonical, closed, decode, text};
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::DurableStore;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

fn invalid(s: &str) -> SupervisorError {
    SupervisorError::Invalid(s.into())
}
fn hash(raw: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(raw))
}
fn exact(v: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    canonical(v, &mut raw)?;
    Ok(raw)
}

fn confirmed(confirmation: &Value, actual: &Value) -> SupervisorResult<()> {
    closed(confirmation, &["schema_version", "kind", "confirm", "plan"])?;
    if confirmation["schema_version"].as_u64() != Some(1)
        || confirmation["kind"] != "review_current_confirmation"
        || confirmation["confirm"] != true
        || exact(&confirmation["plan"])? != exact(actual)?
    {
        return Err(invalid(
            "confirmation differs from the current native plan; reload before applying",
        ));
    }
    Ok(())
}

fn source_id(value: &str) -> SupervisorResult<()> {
    if !matches!(value.len(), 40 | 64)
        || !value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(invalid(
            "current execution requires a source-bound release build",
        ));
    }
    Ok(())
}

/// Select exactly one inventoried native executable from the same source as
/// this binary. This is selection only; full original/processing admission is
/// still performed by review_generation before model/worker execution.
fn binary_pin(release: &Value, source: &str) -> SupervisorResult<(String, u64)> {
    source_id(source)?;
    if release["schema_version"].as_u64() != Some(1) || release["source_sha"] != source {
        return Err(invalid(
            "installed processing manifest differs from this native build source",
        ));
    }
    let artifacts = release["artifacts"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 100000)
        .ok_or_else(|| invalid("installed processing inventory missing"))?;
    let mut found = artifacts
        .iter()
        .filter(|v| v["path"] == "app/bin/dubflow-supervisor.exe");
    let pin = found
        .next()
        .ok_or_else(|| invalid("processing manifest does not pin this native executable"))?;
    if found.next().is_some() {
        return Err(invalid(
            "processing native executable identity is ambiguous",
        ));
    }
    let digest = text(&pin["sha256"], 64)?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(invalid("processing executable hash invalid"));
    }
    let size = text(&pin["size_bytes"], 20)?;
    let n = size
        .parse::<u64>()
        .map_err(|_| invalid("processing executable size invalid"))?;
    if n == 0 || n > 512 * 1024 * 1024 || n.to_string() != size {
        return Err(invalid(
            "processing executable size is not canonical/bounded",
        ));
    }
    Ok((format!("sha256:{digest}"), n))
}

fn capture_file(
    path: &Path,
    limit: u64,
    capture: bool,
    deadline: Instant,
) -> SupervisorResult<(Vec<u8>, String, u64)> {
    let mut file = super::review_assets::regular(path)?;
    let before = file.metadata()?;
    if before.len() == 0 || before.len() > limit {
        return Err(invalid(
            "installed processing file exceeds its admission bound",
        ));
    }
    let mut digest = Sha256::new();
    let mut raw = Vec::new();
    let mut count = 0u64;
    let mut block = [0u8; 32768];
    loop {
        if Instant::now() >= deadline {
            return Err(invalid("installed processing selection deadline exceeded"));
        }
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        count += n as u64;
        if count > before.len() {
            return Err(invalid("installed processing file grew"));
        }
        digest.update(&block[..n]);
        if capture {
            raw.extend_from_slice(&block[..n]);
        }
    }
    let fresh = super::review_assets::regular(path)?;
    let after = file.metadata()?;
    let reopened = fresh.metadata()?;
    if count != before.len()
        || after.len() != count
        || reopened.len() != count
        || before.modified()? != after.modified()?
        || before.modified()? != reopened.modified()?
        || before.permissions().readonly() != reopened.permissions().readonly()
        || Instant::now() >= deadline
    {
        return Err(invalid(
            "installed processing file changed during source selection",
        ));
    }
    Ok((raw, format!("sha256:{:x}", digest.finalize()), count))
}

struct ProcessingSelection {
    root: PathBuf,
    source: String,
    manifest: Value,
}
fn installed_processing() -> SupervisorResult<ProcessingSelection> {
    // Set by the existing exact-source Windows release workflow at compile time.
    // Runtime environment variables, labels and manifest self-assertions cannot
    // substitute for a missing compiled source identity.
    let source = option_env!("SOURCE_SHA")
        .ok_or_else(|| invalid("current execution needs the source-bound installed release"))?;
    source_id(source)?;
    let exe = std::env::current_exe()?;
    let bin = exe
        .parent()
        .ok_or_else(|| invalid("native executable parent missing"))?;
    let app = bin
        .parent()
        .ok_or_else(|| invalid("native application root missing"))?;
    let root = app
        .parent()
        .ok_or_else(|| invalid("installed processing root missing"))?;
    if exe.file_name() != Some(std::ffi::OsStr::new("dubflow-supervisor.exe"))
        || bin.file_name() != Some(std::ffi::OsStr::new("bin"))
        || app.file_name() != Some(std::ffi::OsStr::new("app"))
        || root.join("app/bin/dubflow-supervisor.exe") != exe
        || !root.is_absolute()
    {
        return Err(invalid(
            "current execution requires the owned installed native layout",
        ));
    }
    super::review_pin::plain(root)?;
    let deadline = Instant::now() + Duration::from_secs(180);
    let path = root.join("release-manifest.json");
    let (raw, digest, size) = capture_file(&path, 64 * 1024 * 1024, true, deadline)?;
    let manifest = decode(
        std::str::from_utf8(&raw).map_err(|_| invalid("installed manifest is not UTF-8"))?,
        64 * 1024 * 1024,
    )?;
    let expected = binary_pin(&manifest, source)?;
    let (_, actual, n) = capture_file(&exe, 512 * 1024 * 1024, false, deadline)?;
    if actual != expected.0 || n != expected.1 {
        return Err(invalid(
            "installed native executable is not the source-matched manifest component",
        ));
    }
    let (fresh, again, again_size) = capture_file(&path, 64 * 1024 * 1024, true, deadline)?;
    if fresh != raw || again != digest || again_size != size {
        return Err(invalid(
            "installed processing manifest changed during selection",
        ));
    }
    Ok(ProcessingSelection {
        root: root.to_owned(),
        source: source.into(),
        manifest: json!({"sha256":digest,"size_bytes":size}),
    })
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    let mut data = None;
    let mut confirmation = None;
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("confirmation flag is not UTF-8"))?;
        let value = args
            .next()
            .ok_or_else(|| invalid("confirmation value missing"))?;
        match flag.as_str() {
            "--data-root" if data.is_none() => data = Some(PathBuf::from(value)),
            "--confirmation-json" if confirmation.is_none() => {
                confirmation = Some(
                    value
                        .into_string()
                        .map_err(|_| invalid("confirmation is not UTF-8"))?,
                )
            }
            _ => {
                return Err(invalid(
                    "unknown or duplicate confirmation argument; roots are native-owned",
                ))
            }
        }
    }
    let data = data.ok_or_else(|| invalid("confirmation control root missing"))?;
    if !data.is_absolute() || data.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(invalid("confirmation control root must be absolute"));
    }
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let confirmation = decode(
        &confirmation.ok_or_else(|| invalid("explicit confirmed plan missing"))?,
        128 * 1024,
    )?;
    let input = &confirmation["plan"];
    let store = DurableStore::open_read_only(&db)?;
    let actual = super::review_plan::read(
        &store,
        &data,
        &input["current_token"],
        text(&input["finding_id"], 256)?,
        text(&input["cue_id"], 256)?,
        &input["changes"],
    )?;
    confirmed(&confirmation, &actual)?;
    let processing = installed_processing()?;
    let token = &actual["current_token"];
    let mut dispatch = Vec::<OsString>::new();
    for (key, value) in [
        ("--job-id", text(&actual["job_id"], 256)?),
        ("--revision", text(&token["revision"], 20)?),
        ("--snapshot-sha256", text(&token["snapshot_sha256"], 71)?),
        ("--graph-sha256", text(&token["graph_sha256"], 71)?),
        (
            "--original-manifest-sha256",
            text(&token["original_manifest_sha256"], 71)?,
        ),
        (
            "--candidate-snapshot-sha256",
            text(&actual["candidate_snapshot_sha256"], 71)?,
        ),
        ("--finding-id", text(&actual["finding_id"], 256)?),
        ("--cue-id", text(&actual["cue_id"], 256)?),
    ] {
        dispatch.extend([OsString::from(key), OsString::from(value)]);
    }
    dispatch.extend([
        OsString::from("--data-root"),
        data.into_os_string(),
        OsString::from("--processing-root"),
        processing.root.into_os_string(),
        OsString::from("--processing-source-sha"),
        OsString::from(processing.source),
        OsString::from("--processing-manifest-sha256"),
        OsString::from(text(&processing.manifest["sha256"], 71)?),
        OsString::from("--processing-manifest-size-bytes"),
        OsString::from(
            processing.manifest["size_bytes"]
                .as_u64()
                .ok_or_else(|| invalid("processing manifest size missing"))?
                .to_string(),
        ),
    ]);
    for (field, flag) in [("translation", "--translation"), ("voice", "--voice")] {
        if let Some(value) = actual["changes"].get(field) {
            dispatch.extend([OsString::from(flag), OsString::from(text(value, 8192)?)]);
        }
    }
    // The commit path reacquires JobExecutionGuard, rereads all actual token
    // fields, re-derives the candidate, fully admits processing/original pins,
    // validates retained physical/semantic results and performs the existing CAS.
    drop(store);
    match actual["mode"].as_str() {
        Some("b1") => super::review_generation::run_commit_b1(dispatch.into_iter()),
        Some("b2") => super::review_generation::run_commit_b2(dispatch.into_iter()),
        _ => Err(invalid("confirmed native mode changed")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_confirmation_is_exact_and_cannot_upgrade_a_plan_into_published_authority() {
        let plan = json!({"current_token":{"revision":"9007199254740993"},"candidate_snapshot_sha256":hash(b"candidate"),"cue_id":"cue-a","changes":{"translation":"New"},"read_only":true,"execution_ready":false});
        let expected = json!({"schema_version":1,"kind":"review_current_confirmation","confirm":true,"plan":plan});
        confirmed(&expected, &plan).unwrap();
        for mutation in 0..5 {
            let mut forged = expected.clone();
            match mutation {
                0 => forged["confirm"] = json!(false),
                1 => forged["plan"]["cue_id"] = json!("cue-b"),
                2 => forged["plan"]["candidate_snapshot_sha256"] = json!(hash(b"foreign")),
                3 => forged["plan"]["execution_ready"] = json!(true),
                _ => forged["processing_root"] = json!("foreign"),
            }
            assert!(confirmed(&forged, &plan).is_err());
        }
    }
    #[test]
    fn processing_selection_requires_compiled_source_exact_unique_binary_hash_and_canonical_size() {
        let source = "a".repeat(40);
        let release = json!({"schema_version":1,"source_sha":source,"artifacts":[{"path":"app/bin/dubflow-supervisor.exe","sha256":"b".repeat(64),"size_bytes":"4829696"}]});
        assert_eq!(
            binary_pin(&release, &source).unwrap(),
            (format!("sha256:{}", "b".repeat(64)), 4829696)
        );
        for mutation in 0..5 {
            let mut bad = release.clone();
            match mutation {
                0 => bad["source_sha"] = json!("c".repeat(40)),
                1 => bad["artifacts"]
                    .as_array_mut()
                    .unwrap()
                    .push(release["artifacts"][0].clone()),
                2 => bad["artifacts"][0]["size_bytes"] = json!("04829696"),
                3 => bad["artifacts"][0]["sha256"] = json!("sha256:invented"),
                _ => bad["artifacts"][0]["path"] = json!("app/bin/foreign.exe"),
            }
            assert!(binary_pin(&bad, &source).is_err());
        }
        assert!(source_id("").is_err());
        assert!(source_id("release-latest").is_err());
    }
}
