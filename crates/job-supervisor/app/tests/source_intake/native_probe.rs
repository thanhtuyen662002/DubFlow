//! Explicit optional QA binary; never selected as a live-site CI test.
//! Build outside the repo with existing serde/serde_json dependencies.
#[path = "../../../../../apps/desktop/src-tauri/src/source_transport/mod.rs"]
mod source_transport;
use serde_json::{json, Value};
use source_transport::service::{AdmissionWindow, Client, NativeRequest, Observation, Phase};
use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};

fn record(log: &mut File, value: &Value) -> Result<(), String> {
    serde_json::to_writer(&mut *log, value).map_err(|_| "proof write failed")?;
    log.write_all(b"\n")
        .and_then(|_| log.flush())
        .map_err(|_| "proof write failed".into())
}
fn events(
    client: &Client,
    log: &mut File,
    pending: &mut VecDeque<Observation>,
    wanted: impl Fn(&Value) -> bool,
    timeout: Duration,
) -> Result<Observation, String> {
    let deadline = Instant::now() + timeout;
    loop {
        pending.extend(client.drain());
        while let Some(observed) = pending.pop_front() {
            record(
                log,
                &json!({"kind":"native_observation","sequence":observed.sequence,"value":observed.value}),
            )?;
            if observed.value["event"] == "source_error" {
                return Err(format!("native source_error: {}", observed.value["code"]));
            }
            if wanted(&observed.value) {
                return Ok(observed);
            }
        }
        let snapshot = client.snapshot();
        if snapshot.phase == Phase::Stopped {
            return Err(format!("native stopped: {:?}", snapshot.failure));
        }
        if Instant::now() >= deadline {
            return Err("bounded observation deadline".into());
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}
fn send(client: &Client, request: NativeRequest) -> Result<(), String> {
    client
        .submit(request, Arc::new(AtomicBool::new(false)))
        .map_err(|e| format!("delivery: {e:?}"))?;
    Ok(())
}
fn probe(client: &Client, log: &mut File, url: String) -> Result<Value, String> {
    let mut pending = VecDeque::new();
    eprintln!("probe: full native manifest admission (180s maximum, unchanged verification)");
    events(
        client,
        log,
        &mut pending,
        |v| v["event"] == "source_ready",
        Duration::from_secs(180),
    )?;
    let scan = "codex2-public-fixture".to_owned();
    eprintln!("probe: explicit start_video; no download inferred from enumeration");
    send(
        client,
        NativeRequest::StartVideo {
            scan_id: scan.clone(),
            provider_id: "generic".into(),
            source_ref: url,
        },
    )?;
    // Exact f68 video recipe emits Completed for its checked page commit, then
    // again after successful enumeration-worker exit. Neither implies download.
    for _ in 0..2 {
        events(
            client,
            log,
            &mut pending,
            |v| v["event"] == "source_status" && v["scan_id"] == scan && v["status"] == "completed",
            Duration::from_secs(120),
        )?;
    }
    send(
        client,
        NativeRequest::Items {
            scan_id: scan.clone(),
            offset: 0,
            limit: 100,
        },
    )?;
    let items = events(
        client,
        log,
        &mut pending,
        |v| v["event"] == "source_items" && v["scan_id"] == scan,
        Duration::from_secs(15),
    )?;
    let rows = items.value["items"].as_array().ok_or("items missing")?;
    if rows.len() != 1 || rows[0]["status"] != "discovered" {
        return Err("enumeration did not yield one discovered item".into());
    }
    let identity = rows[0]["identity_key"]
        .as_str()
        .ok_or("identity missing")?
        .to_owned();
    eprintln!("probe: explicit download through the original native producer");
    send(
        client,
        NativeRequest::Download {
            scan_id: scan.clone(),
            identity_key: identity.clone(),
            resume: false,
        },
    )?;
    let downloaded = events(
        client,
        log,
        &mut pending,
        |v| {
            v["event"] == "source_downloaded"
                && v["scan_id"] == scan
                && v["identity_key"] == identity
        },
        Duration::from_secs(120),
    )?;
    send(client, NativeRequest::Shutdown)?;
    let deadline = Instant::now() + Duration::from_secs(4);
    while client.snapshot().phase != Phase::Stopped {
        for observed in client.drain() {
            record(
                log,
                &json!({"kind":"native_observation","sequence":observed.sequence,"value":observed.value}),
            )?;
        }
        if Instant::now() >= deadline {
            return Err("native shutdown deadline".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let stopped = client.snapshot();
    if !stopped.retired || stopped.failure.is_some() {
        return Err("native successful exit/retirement not observed".into());
    }
    Ok(
        json!({"kind":"native_materialization_receipt","sequence":downloaded.sequence,"value":downloaded.value,"retired":true}),
    )
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(
        args.len(),
        5,
        "probe ROOT NEW_DATA EXPECTED_MANIFEST_SHA256 PUBLIC_URL"
    );
    let data = PathBuf::from(&args[2]);
    std::fs::create_dir(&data).expect("probe DATA must be new; never reuse another run");
    let mut log = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(data.join("probe.ndjson"))
        .unwrap();
    let pin = source_transport::service::LaunchPin {
        root: PathBuf::from(&args[1]),
        data_root: data.clone(),
        manifest_sha256: args[3].clone(),
    };
    let client =
        Client::launch(pin, AdmissionWindow::new(Duration::from_secs(180)).unwrap()).unwrap();
    let result = probe(&client, &mut log, args[4].clone());
    if result.is_err() {
        client.stop();
        let deadline = Instant::now() + Duration::from_secs(4);
        while client.snapshot().phase != Phase::Stopped && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    let outcome = match result {
        Ok(value) => value,
        Err(error) => json!({"kind":"probe_failure","error":error,"retirement":client.snapshot()}),
    };
    record(&mut log, &outcome).unwrap();
    serde_json::to_writer(File::create(data.join("result.json")).unwrap(), &outcome).unwrap();
    println!("{outcome}");
    if outcome["kind"] != "native_materialization_receipt" {
        std::process::exit(1);
    }
}
