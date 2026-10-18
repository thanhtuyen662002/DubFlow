//! Bounded one-shot source service I/O. Call on a blocking pool, never the UI thread.
//! No pipe read, write, or join runs in the deadline observer.
pub mod service;
mod tree;

/// Private candidate-184 JSONL bridge. Process cleanup precedes JSON parsing,
/// so malformed, error, and extra responses have the same ownership guarantee.
pub fn json_request(
    command: &mut Command,
    value: &serde_json::Value,
    cancel: &AtomicBool,
) -> Result<serde_json::Value, String> {
    let payload = serde_json::to_vec(value).map_err(|_| "SOURCE_REQUEST_INVALID".to_owned())?;
    let bytes =
        request(command, payload, Limits::default(), cancel).map_err(|error| error.to_string())?;
    let text = std::str::from_utf8(&bytes).map_err(|_| "SOURCE_RESPONSE_INVALID".to_owned())?;
    let mut response = None;
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        let value: serde_json::Value =
            serde_json::from_str(line).map_err(|_| "SOURCE_RESPONSE_INVALID".to_owned())?;
        match value.get("event").and_then(serde_json::Value::as_str) {
            Some("ready") if response.is_none() => (),
            Some("error") => return Err("SOURCE_REQUEST_REJECTED".into()),
            Some(_) if response.is_none() => response = Some(value),
            _ => return Err("SOURCE_RESPONSE_INVALID".into()),
        }
    }
    response.ok_or_else(|| "SOURCE_RESPONSE_MISSING".into())
}

use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[derive(Clone, Copy)]
pub struct Limits {
    pub timeout: Duration,
    pub retire_timeout: Duration,
    pub request_bytes: usize,
    pub stdout_bytes: usize,
    pub stderr_bytes: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(15),
            retire_timeout: Duration::from_secs(3),
            request_bytes: 16 * 1024 * 1024,
            stdout_bytes: 16 * 1024 * 1024,
            stderr_bytes: 1024 * 1024,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    Cancelled,
    Timeout,
    RequestLimit,
    StdoutLimit,
    StderrLimit,
    Spawn,
    Io,
    Exit,
    Retire,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Do not echo child diagnostics, argv, request or credential bytes.
        write!(f, "SOURCE_SERVICE_{self:?}")
    }
}
impl std::error::Error for Error {}

enum Event {
    Stdout(Vec<u8>),
    Stderr,
    Written,
    Failure(Error),
}

fn reader(mut pipe: impl Read, cap: usize, stdout: bool, tx: SyncSender<Event>) {
    let mut kept = Vec::new();
    let mut total = 0usize;
    let mut buffer = [0u8; 8192];
    loop {
        match pipe.read(&mut buffer) {
            Ok(0) => {
                let _ = tx.send(if stdout {
                    Event::Stdout(kept)
                } else {
                    Event::Stderr
                });
                return;
            }
            Ok(count) => {
                total = total.saturating_add(count);
                if total > cap {
                    let _ = tx.send(Event::Failure(if stdout {
                        Error::StdoutLimit
                    } else {
                        Error::StderrLimit
                    }));
                    return;
                }
                if stdout {
                    kept.extend_from_slice(&buffer[..count]);
                }
                // stderr is drained and counted, never retained or logged.
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => {
                let _ = tx.send(Event::Failure(Error::Io));
                return;
            }
        }
    }
}

fn observe(
    child: &mut tree::OwnedChild,
    rx: &Receiver<Event>,
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<Vec<u8>, Error> {
    let mut output = None;
    let mut stderr_done = false;
    let mut written = false;
    loop {
        if cancel.load(Ordering::Acquire) {
            return Err(Error::Cancelled);
        }
        let now = Instant::now();
        if now >= deadline {
            return Err(Error::Timeout);
        }
        match rx.recv_timeout((deadline - now).min(Duration::from_millis(10))) {
            Ok(Event::Stdout(bytes)) => output = Some(bytes),
            Ok(Event::Stderr) => stderr_done = true,
            Ok(Event::Written) => written = true,
            Ok(Event::Failure(error)) => return Err(error),
            Err(mpsc::RecvTimeoutError::Timeout | mpsc::RecvTimeoutError::Disconnected) => (),
        }
        if let Some(status) = child.child.try_wait().map_err(|_| Error::Io)? {
            if !status.success() {
                // A capped reader publishes failure before closing the pipe.
                // Preserve that cause if the broken pipe also exits the child.
                while let Ok(event) = rx.try_recv() {
                    if let Event::Failure(error) = event {
                        return Err(error);
                    }
                }
                return Err(Error::Exit);
            }
            if output.is_some() && stderr_done && written {
                return Ok(output.take().unwrap());
            }
        }
    }
}

/// Owns the child subtree before it can execute. Deadline includes pipe writes,
/// EOF and successful process exit; a partial reply or no newline cannot hold it.
/// On every outcome, close/kill the tree, reap the direct child, then join only
/// already-finished I/O threads within a separate bounded retirement window.
pub fn request(
    command: &mut Command,
    payload: Vec<u8>,
    limits: Limits,
    cancel: &AtomicBool,
) -> Result<Vec<u8>, Error> {
    if cancel.load(Ordering::Acquire) {
        return Err(Error::Cancelled);
    }
    if payload.len() > limits.request_bytes {
        return Err(Error::RequestLimit);
    }
    let deadline = Instant::now() + limits.timeout;
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut owned = tree::OwnedChild::spawn(command).map_err(|_| Error::Spawn)?;
    let stdout = owned.child.stdout.take().ok_or(Error::Io)?;
    let stderr = owned.child.stderr.take().ok_or(Error::Io)?;
    let mut stdin = owned.child.stdin.take().ok_or(Error::Io)?;
    // Each producer sends at most one event; the observer owns all receives.
    let (tx, rx) = mpsc::sync_channel(4);
    let out_tx = tx.clone();
    let err_tx = tx.clone();
    let handles: Vec<JoinHandle<()>> = vec![
        thread::spawn(move || reader(stdout, limits.stdout_bytes, true, out_tx)),
        thread::spawn(move || reader(stderr, limits.stderr_bytes, false, err_tx)),
        thread::spawn(move || {
            let result = stdin
                .write_all(&payload)
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush());
            drop(stdin);
            let _ = tx.send(if result.is_ok() {
                Event::Written
            } else {
                Event::Failure(Error::Io)
            });
        }),
    ];
    let result = observe(&mut owned, &rx, cancel, deadline);
    // Terminate descendants even if the parent exited after a valid response.
    owned.terminate();
    let retire_deadline = Instant::now() + limits.retire_timeout;
    loop {
        let exited = owned.child.try_wait().map_err(|_| Error::Retire)?.is_some();
        if exited && handles.iter().all(JoinHandle::is_finished) {
            break;
        }
        if Instant::now() >= retire_deadline {
            return Err(Error::Retire);
        }
        thread::sleep(Duration::from_millis(2));
    }
    for handle in handles {
        handle.join().map_err(|_| Error::Io)?;
    }
    result
}
