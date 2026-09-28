use dubflow_job_state::{DurableStore, JobStatus, StageStatus};
use dubflow_local_file_slice::{partial_output_path, DeterministicMediaProbe, LocalFileJob, MediaProbe, ANALYSIS_STAGE, PROBE_STAGE, RENDER_STAGE, VALIDATE_STAGE};
use dubflow_media_contracts::TimeBase;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus};
use std::time::{SystemTime, UNIX_EPOCH};

const ROOT: &str = env!("CARGO_MANIFEST_DIR");
const FIXTURES: &str = "../tests/integration/local_file_slice/fixtures";

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(ROOT).join(FIXTURES).join(name)
}

fn temp_root(test_name: &str) -> PathBuf {
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
    let root = env::temp_dir().join(format!("dubflow-local-slice-{test_name}-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    root
}

fn child_or_parent(mode: &str, db: &Path, source: &Path, output: &Path) -> Option<ExitStatus> {
    if env::var("DUBFLOW_SLICE_CHILD").ok().as_deref() == Some(mode) {
        let db = PathBuf::from(env::var_os("DUBFLOW_SLICE_DB").expect("child database path"));
        let source = PathBuf::from(env::var_os("DUBFLOW_SLICE_SOURCE").expect("child source path"));
        let output = PathBuf::from(env::var_os("DUBFLOW_SLICE_OUTPUT").expect("child output path"));
        let job = LocalFileJob::create(&db, "kill-job", &source, &output).unwrap();
        if mode == "analysis" {
            job.prepare_analysis_kill(10).unwrap();
        } else {
            job.prepare_render_kill(10).unwrap();
        }
        std::process::exit(137);
    }
    let status = Command::new(env::current_exe().unwrap())
        .args(["--exact", if mode == "analysis" { "kill_during_analysis_resumes_without_redoing_probe" } else { "kill_during_render_re_renders_render_only" }])
        .env("DUBFLOW_SLICE_CHILD", mode)
        .env("RUST_BACKTRACE", "1")
        .env("DUBFLOW_SLICE_DB", db)
        .env("DUBFLOW_SLICE_SOURCE", source)
        .env("DUBFLOW_SLICE_OUTPUT", output)
        .status()
        .unwrap();
    Some(status)
}

#[test]
fn valid_landscape_and_portrait_fixtures_complete() {
    let root = temp_root("orientation");
    for (name, expected_width, expected_height) in [("landscape.mp4", 640, 360), ("portrait.mp4", 360, 640)] {
        let db = root.join(format!("{name}.sqlite"));
        let output = root.join(format!("{name}.output.mp4"));
        let source = fixture(name);
        let mut job = LocalFileJob::create(&db, format!("job-{name}"), &source, &output).unwrap();
        let metadata = job.run_to_completion(1).unwrap();
        assert_eq!(metadata.dimensions.width(), expected_width);
        assert_eq!(metadata.dimensions.height(), expected_height);
        assert_eq!(job.status().unwrap(), JobStatus::Succeeded);
        assert_eq!(job.stage_status(PROBE_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(job.stage_status(ANALYSIS_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(job.stage_status(RENDER_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(job.stage_status(VALIDATE_STAGE).unwrap(), StageStatus::Succeeded);
        assert!(output.is_file());
        assert!(!partial_output_path(&output).exists());
        assert_eq!(fs::read(&source).unwrap(), fs::read(&output).unwrap());
    }
    let _ = fs::remove_dir_all(root);
}

#[test]
fn vfr_non_zero_pts_preserve_canonical_integer_timing() {
    let metadata = DeterministicMediaProbe.probe(&fixture("vfr_nonzero_pts.dfslice")).unwrap();
    assert_eq!(metadata.dimensions.width(), 640);
    assert_eq!(metadata.dimensions.height(), 360);
    assert_eq!(metadata.time_base, TimeBase::new(1, 1000).unwrap());
    assert_eq!(metadata.duration.ticks, 130);
    assert_eq!(metadata.presentation_timestamps.iter().map(|point| point.ticks).collect::<Vec<_>>(), vec![900, 940, 1010]);
    let canonical = metadata.canonicalize(TimeBase::new(1, 1000).unwrap()).unwrap();
    assert_eq!(canonical.presentation_timestamps.iter().map(|point| point.ticks).collect::<Vec<_>>(), vec![900, 940, 1010]);
}

#[test]
fn corrupt_input_fails_only_its_job() {
    let root = temp_root("isolation");
    let good_source = fixture("landscape.mp4");
    let good_output = root.join("good.output.mp4");
    let bad_source = root.join("corrupt.mp4");
    let bad_output = root.join("bad.output.mp4");
    fs::write(&bad_source, b"this is not an ISO-BMFF file").unwrap();
    let mut good = LocalFileJob::create(root.join("jobs.sqlite"), "good-job", &good_source, &good_output).unwrap();
    let mut bad = LocalFileJob::create(root.join("jobs.sqlite"), "bad-job", &bad_source, &bad_output).unwrap();
    assert!(good.run_to_completion(1).is_ok());
    assert!(bad.run_to_completion(2).is_err());
    assert_eq!(good.status().unwrap(), JobStatus::Succeeded);
    assert_eq!(bad.status().unwrap(), JobStatus::Failed);
    assert!(good_output.is_file());
    assert!(!bad_output.exists());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn partial_output_is_never_published_as_final() {
    let root = temp_root("partial");
    let source = fixture("landscape.mp4");
    let output = root.join("output.mp4");
    let db = root.join("jobs.sqlite");
    let job = LocalFileJob::create(&db, "partial-job", &source, &output).unwrap();
    job.prepare_render_kill(1).unwrap();
    assert!(!output.exists());
    assert!(partial_output_path(&output).is_file());
    let store = DurableStore::open(&db).unwrap();
    assert!(store.artifact("partial-job-render").is_err());
    let quarantined = store.scan_orphan_artifacts(&root).unwrap();
    assert_eq!(quarantined.len(), 1);
    assert!(!partial_output_path(&output).exists());
    assert!(root.join("output.mp4.partial.orphan.quarantine").exists());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn kill_during_analysis_resumes_without_redoing_probe() {
    let root = temp_root("kill-analysis");
    let db = root.join("jobs.sqlite");
    let source = fixture("landscape.mp4");
    let output = root.join("output.mp4");
    if let Some(status) = child_or_parent("analysis", &db, &source, &output) {
        assert!(!status.success());
        let store = DurableStore::open(&db).unwrap();
        assert_eq!(store.recover_after_restart(100).unwrap(), 1);
        assert_eq!(store.stage_status("kill-job", PROBE_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(store.stage_status("kill-job", ANALYSIS_STAGE).unwrap(), StageStatus::Recovering);
        drop(store);
        let mut resumed = LocalFileJob::open_existing(&db, "kill-job", &source, &output).unwrap();
        resumed.resume_to_completion(101).unwrap();
        assert_eq!(resumed.status().unwrap(), JobStatus::Succeeded);
        assert_eq!(resumed.stage_status(PROBE_STAGE).unwrap(), StageStatus::Succeeded);
        assert!(output.is_file());
        assert!(!partial_output_path(&output).exists());
    }
    let _ = fs::remove_dir_all(root);
}

#[test]
fn kill_during_render_re_renders_render_only() {
    let root = temp_root("kill-render");
    let db = root.join("jobs.sqlite");
    let source = fixture("portrait.mp4");
    let output = root.join("output.mp4");
    if let Some(status) = child_or_parent("render", &db, &source, &output) {
        assert!(!status.success());
        let store = DurableStore::open(&db).unwrap();
        assert_eq!(store.recover_after_restart(100).unwrap(), 1);
        assert_eq!(store.stage_status("kill-job", PROBE_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(store.stage_status("kill-job", ANALYSIS_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(store.stage_status("kill-job", RENDER_STAGE).unwrap(), StageStatus::Recovering);
        drop(store);
        let mut resumed = LocalFileJob::open_existing(&db, "kill-job", &source, &output).unwrap();
        resumed.resume_to_completion(101).unwrap();
        assert_eq!(resumed.status().unwrap(), JobStatus::Succeeded);
        assert_eq!(resumed.stage_status(PROBE_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(resumed.stage_status(ANALYSIS_STAGE).unwrap(), StageStatus::Succeeded);
        assert_eq!(resumed.stage_status(RENDER_STAGE).unwrap(), StageStatus::Succeeded);
        assert!(output.is_file());
        assert!(!partial_output_path(&output).exists());
    }
    let _ = fs::remove_dir_all(root);
}
