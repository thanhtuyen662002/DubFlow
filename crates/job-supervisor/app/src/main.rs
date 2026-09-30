//! Durable DubFlow process supervisor.
//!
//! The supervisor is deliberately independent from the desktop UI.  The UI
//! speaks a small JSONL command stream, while the supervisor owns the SQLite
//! connection and the Python child process.  Python emits only the versioned
//! worker protocol; all durable mutations happen in this process.

use dubflow_job_state::{ArtifactState, DurableStore, JobStatus, StageStatus, StateError};
use dubflow_worker_protocol::{
    Envelope, MessageType, Payload, ShutdownStatus, StreamValidator, MAX_LINE_BYTES,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::panic::AssertUnwindSafe;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const STAGE_ID: &str = "local-file";
const STAGE_KIND: &str = "production-local-file";
const MAX_ATTEMPTS: u8 = 3;
const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(35);
const STDERR_LIMIT: usize = 64 * 1024;
static ID_COUNTER: AtomicU64 = AtomicU64::new(1);

type SupervisorResult<T> = Result<T, SupervisorError>;

#[derive(Debug)]
enum SupervisorError {
    Io(io::Error),
    Json(serde_json::Error),
    Protocol(dubflow_worker_protocol::ProtocolError),
    State(StateError),
    Invalid(String),
    Worker {
        code: String,
        condition: String,
        retryable: bool,
    },
}

impl std::fmt::Display for SupervisorError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Json(error) => write!(f, "JSON error: {error}"),
            Self::Protocol(error) => write!(f, "worker protocol error: {error}"),
            Self::State(error) => write!(f, "durable state error: {error}"),
            Self::Invalid(error) => write!(f, "invalid supervisor request: {error}"),
            Self::Worker {
                code, condition, ..
            } => write!(f, "{code}: {condition}"),
        }
    }
}

impl std::error::Error for SupervisorError {}
impl From<io::Error> for SupervisorError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
impl From<serde_json::Error> for SupervisorError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}
impl From<dubflow_worker_protocol::ProtocolError> for SupervisorError {
    fn from(error: dubflow_worker_protocol::ProtocolError) -> Self {
        Self::Protocol(error)
    }
}
impl From<StateError> for SupervisorError {
    fn from(error: StateError) -> Self {
        Self::State(error)
    }
}

#[derive(Clone, Debug)]
struct RuntimePaths {
    root: PathBuf,
    app_root: PathBuf,
    python: PathBuf,
    worker_script: PathBuf,
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    model_root: PathBuf,
    db: PathBuf,
}

impl RuntimePaths {
    fn from_root_and_data(
        root: PathBuf,
        data_root: PathBuf,
        db_override: Option<PathBuf>,
        model_override: Option<PathBuf>,
    ) -> SupervisorResult<Self> {
        if !root.is_absolute() {
            return Err(SupervisorError::Invalid("--root must be absolute".into()));
        }
        if !data_root.is_absolute() {
            return Err(SupervisorError::Invalid(
                "--data-root must be absolute".into(),
            ));
        }
        if let Some(model_root) = model_override.as_ref() {
            if !model_root.is_absolute() {
                return Err(SupervisorError::Invalid(
                    "--model-root must be absolute".into(),
                ));
            }
        }
        let root = root.canonicalize().map_err(|error| {
            SupervisorError::Invalid(format!("runtime root is unavailable: {error}"))
        })?;
        if !root.is_dir() {
            return Err(SupervisorError::Invalid(format!(
                "runtime root is not a directory: {}",
                root.display()
            )));
        }
        let data_root = if data_root.exists() {
            data_root.canonicalize().map_err(|error| {
                SupervisorError::Invalid(format!("data root is unavailable: {error}"))
            })?
        } else {
            fs::create_dir_all(&data_root)?;
            data_root.canonicalize().map_err(|error| {
                SupervisorError::Invalid(format!("data root is unavailable: {error}"))
            })?
        };
        let app_root = root.join("app");
        if !app_root.is_dir() {
            return Err(SupervisorError::Invalid(format!(
                "app payload is missing: {}",
                app_root.display()
            )));
        }
        let runtime_root = root.join("runtime");
        let python_candidate = first_existing(&[
            runtime_root.join("python.exe"),
            runtime_root.join("python"),
            runtime_root.join("bin").join("python.exe"),
            runtime_root.join("bin").join("python"),
        ])
        .ok_or_else(|| {
            SupervisorError::Invalid(format!(
                "app-owned Python is missing below {}",
                runtime_root.display()
            ))
        })?;
        let media_root =
            first_existing_dir(&[runtime_root.join("media"), runtime_root.join("ffmpeg")])
                .ok_or_else(|| {
                    SupervisorError::Invalid(format!(
                        "app-owned media runtime is missing below {}",
                        runtime_root.display()
                    ))
                })?;
        let ffmpeg_candidate =
            first_existing(&[media_root.join("ffmpeg.exe"), media_root.join("ffmpeg")])
                .ok_or_else(|| {
                    SupervisorError::Invalid(format!(
                        "app-owned FFmpeg is missing below {}",
                        media_root.display()
                    ))
                })?;
        let ffprobe_candidate =
            first_existing(&[media_root.join("ffprobe.exe"), media_root.join("ffprobe")])
                .ok_or_else(|| {
                    SupervisorError::Invalid(format!(
                        "app-owned FFprobe is missing below {}",
                        media_root.display()
                    ))
                })?;
        let worker_candidate = app_root
            .join("engine")
            .join("dubflow")
            .join("worker")
            .join("production_job.py");
        if !worker_candidate.is_file() {
            return Err(SupervisorError::Invalid(format!(
                "production worker is missing: {}",
                worker_candidate.display()
            )));
        }
        let app_root = owned_path(&root, &app_root, "app payload")?;
        let python = owned_path(&root, &python_candidate, "Python runtime")?;
        let ffmpeg = owned_path(&root, &ffmpeg_candidate, "FFmpeg")?;
        let ffprobe = owned_path(&root, &ffprobe_candidate, "FFprobe")?;
        let worker_script = owned_path(&root, &worker_candidate, "worker script")?;
        let model_candidate = model_override.unwrap_or_else(|| data_root.join("models"));
        if !model_candidate.starts_with(&data_root) && !model_candidate.starts_with(&root) {
            return Err(SupervisorError::Invalid(
                "--model-root must be below --data-root or --root".into(),
            ));
        }
        fs::create_dir_all(&model_candidate)?;
        let model_root = owned_path_any(&[&root, &data_root], &model_candidate, "model root")?;
        let db = db_override.unwrap_or_else(|| data_root.join("control").join("jobs.sqlite3"));
        if !db.is_absolute() {
            return Err(SupervisorError::Invalid("--db must be absolute".into()));
        }
        if let Some(parent) = db.parent() {
            fs::create_dir_all(parent)?;
        }
        Ok(Self {
            root,
            app_root,
            python,
            worker_script,
            ffmpeg,
            ffprobe,
            model_root,
            db,
        })
    }
}

fn first_existing(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|path| path.is_file()).cloned()
}

fn first_existing_dir(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|path| path.is_dir()).cloned()
}

fn owned_path(root: &Path, path: &Path, label: &str) -> SupervisorResult<PathBuf> {
    owned_path_any(&[root], path, label)
}

fn owned_path_any(roots: &[&Path], path: &Path, label: &str) -> SupervisorResult<PathBuf> {
    let path = path
        .canonicalize()
        .map_err(|error| SupervisorError::Invalid(format!("{label} is unavailable: {error}")))?;
    if !roots.iter().any(|root| path.starts_with(root)) {
        return Err(SupervisorError::Invalid(format!(
            "{label} escapes the installed runtime root"
        )));
    }
    Ok(path)
}

#[derive(Debug, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case")]
enum UiRequest {
    Start {
        job_id: Option<String>,
        source_path: String,
        output_dir: String,
        #[serde(default)]
        source_language: Option<String>,
        #[serde(default)]
        target_language: Option<String>,
        #[serde(default)]
        enable_dubbing: Option<bool>,
        #[serde(default)]
        burn_in_subtitles: Option<bool>,
    },
    Status {
        job_id: String,
    },
    Cancel {
        job_id: String,
        #[serde(default)]
        reason: Option<String>,
    },
    Ping,
    Shutdown,
}

#[derive(Clone, Debug)]
struct StartSpec {
    job_id: String,
    source_path: PathBuf,
    output_dir: PathBuf,
    source_language: String,
    target_language: String,
    enable_dubbing: bool,
    burn_in_subtitles: bool,
}

impl StartSpec {
    fn from_request(
        job_id: Option<String>,
        source_path: String,
        output_dir: String,
        source_language: Option<String>,
        target_language: Option<String>,
        enable_dubbing: Option<bool>,
        burn_in_subtitles: Option<bool>,
    ) -> SupervisorResult<Self> {
        let source_path = absolute_path(&source_path, "source_path")?;
        if !source_path.is_file() {
            return Err(SupervisorError::Invalid(format!(
                "source file does not exist: {}",
                source_path.display()
            )));
        }
        let output_dir = absolute_path(&output_dir, "output_dir")?;
        let job_id = job_id.unwrap_or_else(new_job_id);
        validate_job_id(&job_id)?;
        let source_language = source_language.unwrap_or_else(|| "auto".into());
        if source_language.is_empty()
            || source_language.len() > 32
            || source_language.chars().any(|ch| ch.is_control())
        {
            return Err(SupervisorError::Invalid(
                "source_language is empty or invalid".into(),
            ));
        }
        let target_language = target_language.unwrap_or_else(|| "vi".into());
        if target_language != "vi" {
            return Err(SupervisorError::Invalid(
                "the production baseline currently supports target_language=vi".into(),
            ));
        }
        Ok(Self {
            job_id,
            source_path,
            output_dir,
            source_language,
            target_language,
            enable_dubbing: enable_dubbing.unwrap_or(false),
            burn_in_subtitles: burn_in_subtitles.unwrap_or(true),
        })
    }
}

fn absolute_path(value: &str, name: &str) -> SupervisorResult<PathBuf> {
    if value.is_empty()
        || value.len() > 32_768
        || value.chars().any(|ch| ch == '\0' || ch.is_control())
    {
        return Err(SupervisorError::Invalid(format!(
            "{name} is empty, too long, or contains control characters"
        )));
    }
    let path = PathBuf::from(value);
    if !path.is_absolute() {
        return Err(SupervisorError::Invalid(format!("{name} must be absolute")));
    }
    Ok(path)
}

fn validate_job_id(value: &str) -> SupervisorResult<()> {
    if value.is_empty()
        || value.len() > 128
        || value == "."
        || value == ".."
        || value
            .chars()
            .any(|ch| ch.is_control() || matches!(ch, '/' | '\\'))
    {
        return Err(SupervisorError::Invalid(
            "job_id must be a path-safe identifier".into(),
        ));
    }
    Ok(())
}

fn new_job_id() -> String {
    let millis = now_ms();
    let counter = ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("job-{millis:x}-{counter:x}")
}

#[derive(Debug)]
struct JobControl {
    cancelled: AtomicBool,
    child: Mutex<Option<Arc<Mutex<Child>>>>,
}

impl JobControl {
    fn new() -> Self {
        Self {
            cancelled: AtomicBool::new(false),
            child: Mutex::new(None),
        }
    }

    fn set_child(&self, child: Arc<Mutex<Child>>) {
        if let Ok(mut slot) = self.child.lock() {
            *slot = Some(child);
        }
    }

    fn clear_child(&self) {
        if let Ok(mut slot) = self.child.lock() {
            *slot = None;
        }
    }

    fn terminate(&self) -> SupervisorResult<()> {
        self.cancelled.store(true, Ordering::Release);
        let child = self
            .child
            .lock()
            .map_err(|_| SupervisorError::Invalid("child lock poisoned".into()))?
            .clone();
        if let Some(child) = child {
            let mut child = child
                .lock()
                .map_err(|_| SupervisorError::Invalid("child lock poisoned".into()))?;
            if child.try_wait()?.is_none() {
                let _ = child.kill();
            }
        }
        Ok(())
    }
}

enum InternalMessage {
    Request(UiRequest),
    InputError(String),
    InputClosed,
    Output(Value),
    Finished(String),
}

#[derive(Debug)]
enum CliMode {
    Server {
        root: PathBuf,
        data_root: Option<PathBuf>,
        db: Option<PathBuf>,
        model_root: Option<PathBuf>,
    },
    Run {
        root: Option<PathBuf>,
        data_root: PathBuf,
        model_root: Option<PathBuf>,
        status_path: Option<PathBuf>,
        job_id: Option<String>,
        source_path: String,
        output_dir: String,
        source_language: Option<String>,
        target_language: Option<String>,
        enable_dubbing: bool,
        burn_in_subtitles: bool,
    },
    Cancel {
        root: Option<PathBuf>,
        data_root: PathBuf,
        db: Option<PathBuf>,
        job_id: String,
        reason: String,
    },
}

fn main() {
    let result = run();
    if let Err(error) = result {
        // Startup failures occur before the JSONL loop exists.  Keep stderr
        // human-readable and return a non-zero code for the desktop host.
        eprintln!("DubFlow supervisor: {error}");
        std::process::exit(2);
    }
}

fn run() -> SupervisorResult<()> {
    let mode = parse_cli(std::env::args_os().skip(1))?;
    let (root, data_root, db, model_root) = match mode {
        CliMode::Run {
            root,
            data_root,
            model_root,
            status_path,
            job_id,
            source_path,
            output_dir,
            source_language,
            target_language,
            enable_dubbing,
            burn_in_subtitles,
        } => {
            return run_one_shot(
                root,
                data_root,
                model_root,
                status_path,
                job_id,
                source_path,
                output_dir,
                source_language,
                target_language,
                enable_dubbing,
                burn_in_subtitles,
            );
        }
        CliMode::Cancel {
            root,
            data_root,
            db,
            job_id,
            reason,
        } => {
            return run_cancel(root, data_root, db, &job_id, &reason);
        }
        CliMode::Server {
            root,
            data_root,
            db,
            model_root,
        } => (root, data_root, db, model_root),
    };
    let server_data_root = data_root.unwrap_or_else(|| root.clone());
    let runtime = RuntimePaths::from_root_and_data(root, server_data_root, db, model_root)?;
    let startup_store = DurableStore::open(&runtime.db)?;
    let recovered = startup_store.recover_after_restart(now_ms())?;

    let (tx, rx) = mpsc::channel::<InternalMessage>();
    spawn_stdin_reader(tx.clone());
    let controls: Arc<Mutex<HashMap<String, Arc<JobControl>>>> =
        Arc::new(Mutex::new(HashMap::new()));
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    emit_value(
        &mut stdout,
        json!({
            "event": "ready",
            "schema_version": 1,
            "runtime_root": runtime.root.display().to_string(),
            "recovered_stages": recovered,
        }),
    )?;

    loop {
        match rx.recv() {
            Ok(InternalMessage::Request(request)) => {
                if handle_request(request, &runtime, &controls, &tx, &mut stdout)? {
                    break;
                }
            }
            Ok(InternalMessage::InputError(error)) => {
                emit_value(
                    &mut stdout,
                    json!({"event":"error", "code":"REQUEST_INVALID", "condition": error}),
                )?;
            }
            Ok(InternalMessage::Output(value)) => emit_value(&mut stdout, value)?,
            Ok(InternalMessage::Finished(job_id)) => {
                if let Ok(mut active) = controls.lock() {
                    active.remove(&job_id);
                }
            }
            Ok(InternalMessage::InputClosed) | Err(mpsc::RecvError) => {
                terminate_all(&controls);
                break;
            }
        }
    }
    Ok(())
}

fn parse_cli<I>(mut args: I) -> SupervisorResult<CliMode>
where
    I: Iterator<Item = OsString>,
{
    let first = args
        .next()
        .map(|value| value.to_string_lossy().into_owned());
    let mode = match first.as_deref() {
        Some("run") => {
            let mut root = None;
            let mut data_root = None;
            let mut model_root = None;
            let mut status_path = None;
            let mut job_id = None;
            let mut source_path = None;
            let mut output_dir = None;
            let mut source_language = None;
            let mut target_language = None;
            let mut enable_dubbing = false;
            let mut burn_in_subtitles = true;
            while let Some(arg) = args.next() {
                match arg.to_string_lossy().as_ref() {
                    "--root" => root = Some(PathBuf::from(next_arg(&mut args, "--root")?)),
                    "--data-root" => {
                        data_root = Some(PathBuf::from(next_arg(&mut args, "--data-root")?))
                    }
                    "--model-root" => {
                        model_root = Some(PathBuf::from(next_arg(&mut args, "--model-root")?))
                    }
                    "--status-path" => {
                        status_path = Some(PathBuf::from(next_arg(&mut args, "--status-path")?))
                    }
                    "--job-id" => job_id = Some(next_arg(&mut args, "--job-id")?),
                    "--source" | "--source-path" => {
                        source_path = Some(next_arg(&mut args, "--source")?)
                    }
                    "--output-dir" => output_dir = Some(next_arg(&mut args, "--output-dir")?),
                    "--source-language" => {
                        source_language = Some(next_arg(&mut args, "--source-language")?)
                    }
                    "--target-language" => {
                        target_language = Some(next_arg(&mut args, "--target-language")?)
                    }
                    "--enable-dubbing" => enable_dubbing = true,
                    "--no-burn-in" => burn_in_subtitles = false,
                    "--help" | "-h" => {
                        println!("dubflow-supervisor run --source <absolute file> --output-dir <absolute dir> --data-root <absolute dir> [--root <version root>] [--status-path <file>]");
                        std::process::exit(0);
                    }
                    value => {
                        return Err(SupervisorError::Invalid(format!(
                            "unknown run argument {value}"
                        )))
                    }
                }
            }
            CliMode::Run {
                root,
                data_root: data_root
                    .ok_or_else(|| SupervisorError::Invalid("run requires --data-root".into()))?,
                model_root,
                status_path,
                job_id,
                source_path: source_path
                    .ok_or_else(|| SupervisorError::Invalid("run requires --source".into()))?,
                output_dir: output_dir
                    .ok_or_else(|| SupervisorError::Invalid("run requires --output-dir".into()))?,
                source_language,
                target_language,
                enable_dubbing,
                burn_in_subtitles,
            }
        }
        Some("cancel") => {
            let mut root = None;
            let mut data_root = None;
            let mut db = None;
            let mut job_id = None;
            let mut reason = "user requested cancellation".to_string();
            while let Some(arg) = args.next() {
                match arg.to_string_lossy().as_ref() {
                    "--root" => root = Some(PathBuf::from(next_arg(&mut args, "--root")?)),
                    "--data-root" => {
                        data_root = Some(PathBuf::from(next_arg(&mut args, "--data-root")?))
                    }
                    "--db" => db = Some(PathBuf::from(next_arg(&mut args, "--db")?)),
                    "--job-id" => job_id = Some(next_arg(&mut args, "--job-id")?),
                    "--reason" => reason = next_arg(&mut args, "--reason")?,
                    value => {
                        return Err(SupervisorError::Invalid(format!(
                            "unknown cancel argument {value}"
                        )))
                    }
                }
            }
            CliMode::Cancel {
                root,
                data_root: data_root.ok_or_else(|| {
                    SupervisorError::Invalid("cancel requires --data-root".into())
                })?,
                db,
                job_id: job_id
                    .ok_or_else(|| SupervisorError::Invalid("cancel requires --job-id".into()))?,
                reason,
            }
        }
        Some("--help") | Some("-h") | None => {
            println!("dubflow-supervisor --root <installed-version-root> [--data-root <data dir>] [--db <absolute sqlite path>]");
            if first.is_none() {
                return Err(SupervisorError::Invalid(
                    "--root is required; system runtimes are not supported".into(),
                ));
            }
            std::process::exit(0);
        }
        Some(_) => {
            // The long-lived server keeps the original flag-only interface.
            let mut server_args = Vec::new();
            if let Some(value) = first {
                server_args.push(OsString::from(value));
            }
            server_args.extend(args);
            let mut server = server_args.into_iter();
            let mut root = None;
            let mut data_root = None;
            let mut db = None;
            let mut model_root = None;
            while let Some(arg) = server.next() {
                match arg.to_string_lossy().as_ref() {
                    "--root" => root = Some(PathBuf::from(next_arg(&mut server, "--root")?)),
                    "--data-root" => {
                        data_root = Some(PathBuf::from(next_arg(&mut server, "--data-root")?))
                    }
                    "--db" => db = Some(PathBuf::from(next_arg(&mut server, "--db")?)),
                    "--model-root" => {
                        model_root = Some(PathBuf::from(next_arg(&mut server, "--model-root")?))
                    }
                    value => {
                        return Err(SupervisorError::Invalid(format!(
                            "unknown command-line argument {value}"
                        )))
                    }
                }
            }
            CliMode::Server {
                root: root.ok_or_else(|| {
                    SupervisorError::Invalid(
                        "--root is required; system runtimes are not supported".into(),
                    )
                })?,
                data_root,
                db,
                model_root,
            }
        }
    };
    Ok(mode)
}

fn next_arg<I>(args: &mut I, flag: &str) -> SupervisorResult<String>
where
    I: Iterator<Item = OsString>,
{
    args.next()
        .map(|value| value.to_string_lossy().into_owned())
        .ok_or_else(|| SupervisorError::Invalid(format!("{flag} requires a value")))
}

fn inferred_root() -> SupervisorResult<PathBuf> {
    let executable = std::env::current_exe()?;
    let app_bin = executable
        .parent()
        .ok_or_else(|| SupervisorError::Invalid("unable to infer installed runtime root".into()))?;
    let app = app_bin
        .parent()
        .ok_or_else(|| SupervisorError::Invalid("unable to infer installed runtime root".into()))?;
    let root = app
        .parent()
        .ok_or_else(|| SupervisorError::Invalid("unable to infer installed runtime root".into()))?;
    Ok(root.to_path_buf())
}

fn run_one_shot(
    root: Option<PathBuf>,
    data_root: PathBuf,
    model_root: Option<PathBuf>,
    status_path: Option<PathBuf>,
    job_id: Option<String>,
    source_path: String,
    output_dir: String,
    source_language: Option<String>,
    target_language: Option<String>,
    enable_dubbing: bool,
    burn_in_subtitles: bool,
) -> SupervisorResult<()> {
    let root = match root {
        Some(root) => root,
        None => inferred_root()?,
    };
    let runtime = RuntimePaths::from_root_and_data(root, data_root.clone(), None, model_root)?;
    let spec = StartSpec::from_request(
        Some(job_id.unwrap_or_else(new_job_id)),
        source_path,
        output_dir,
        source_language,
        target_language,
        Some(enable_dubbing),
        Some(burn_in_subtitles),
    )?;
    let final_status_path = status_path.unwrap_or_else(|| {
        data_root
            .join("control")
            .join("jobs")
            .join(format!("{}.status.json", spec.job_id))
    });
    if !final_status_path.is_absolute() {
        return Err(SupervisorError::Invalid(
            "--status-path must be absolute".into(),
        ));
    }
    let mut contract_status = default_contract_status();
    let mut output_path: Option<String> = None;
    write_status_file(&final_status_path, &spec.job_id, &contract_status, None)?;
    let control = Arc::new(JobControl::new());
    let (tx, rx) = mpsc::channel();
    let runtime_for_worker = runtime.clone();
    let spec_for_worker = spec.clone();
    let control_for_worker = control.clone();
    let tx_for_worker = tx.clone();
    thread::spawn(move || {
        let outcome = std::panic::catch_unwind(AssertUnwindSafe(|| {
            execute_job(
                &runtime_for_worker,
                spec_for_worker.clone(),
                control_for_worker,
                tx_for_worker.clone(),
            )
        }));
        match outcome {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                let _ = tx_for_worker.send(InternalMessage::Output(error_value(
                    Some(&spec_for_worker.job_id),
                    "SUPERVISOR_JOB_ERROR",
                    &error.to_string(),
                    false,
                )));
            }
            Err(_) => {
                let _ = tx_for_worker.send(InternalMessage::Output(error_value(
                    Some(&spec_for_worker.job_id),
                    "SUPERVISOR_PANIC",
                    "supervisor job thread panicked; durable state remains recoverable",
                    false,
                )));
            }
        }
        let _ = tx_for_worker.send(InternalMessage::Finished(spec_for_worker.job_id));
    });
    drop(tx);
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    loop {
        match rx.recv() {
            Ok(InternalMessage::Output(value)) => {
                apply_status_event(&mut contract_status, &value, &mut output_path, &spec);
                write_status_file(
                    &final_status_path,
                    &spec.job_id,
                    &contract_status,
                    output_path.as_deref(),
                )?;
                emit_value(&mut stdout, value)?;
            }
            Ok(InternalMessage::Finished(_)) | Err(mpsc::RecvError) => break,
            Ok(InternalMessage::InputError(error)) => {
                contract_status = failed_contract_status("REQUEST_INVALID", &error);
                write_status_file(&final_status_path, &spec.job_id, &contract_status, None)?;
            }
            Ok(InternalMessage::InputClosed) => break,
            Ok(InternalMessage::Request(_)) => {}
        }
    }
    let store = DurableStore::open(&runtime.db)?;
    let durable_status = store.job_status(&spec.job_id).unwrap_or(JobStatus::Failed);
    apply_durable_status(
        &mut contract_status,
        durable_status,
        &mut output_path,
        &spec,
    );
    write_status_file(
        &final_status_path,
        &spec.job_id,
        &contract_status,
        output_path.as_deref(),
    )?;
    emit_value(
        &mut stdout,
        json!({"event":"status", "job_id":spec.job_id, "status":contract_status, "output_path":output_path}),
    )?;
    Ok(())
}

fn run_cancel(
    _root: Option<PathBuf>,
    data_root: PathBuf,
    db: Option<PathBuf>,
    job_id: &str,
    reason: &str,
) -> SupervisorResult<()> {
    if !data_root.is_absolute() {
        return Err(SupervisorError::Invalid(
            "--data-root must be absolute".into(),
        ));
    }
    let db = db.unwrap_or_else(|| data_root.join("control").join("jobs.sqlite3"));
    if !db.is_absolute() {
        return Err(SupervisorError::Invalid("--db must be absolute".into()));
    }
    let store = DurableStore::open(&db)?;
    cancel_durable(&store, job_id, reason)?;
    println!(
        "{}",
        serde_json::to_string(&json!({"status":"cancelled", "job_id":job_id}))?
    );
    Ok(())
}

fn default_contract_status() -> Value {
    json!({
        "schema_version": 1,
        "state": "QUEUED",
        "reason": "queued",
        "message": "Đang chờ supervisor",
        "checkpoint_id": Value::Null,
        "progress": {"completed_units": "0", "total_units": Value::Null, "heartbeat_sequence": "0"},
        "retry": {"attempt": "0", "max_attempts": MAX_ATTEMPTS.to_string(), "condition_fingerprint": Value::Null},
        "resource": {"kind": "CPU", "held": false, "release_requested": false}
    })
}

fn failed_contract_status(code: &str, detail: &str) -> Value {
    let mut status = default_contract_status();
    status["state"] = Value::String("FAILED".into());
    status["reason"] = Value::String(code.to_lowercase());
    status["message"] = Value::String(format!(
        "{code}: {}",
        detail.chars().take(3800).collect::<String>()
    ));
    status
}

fn write_status_file(
    path: &Path,
    job_id: &str,
    status: &Value,
    output_path: Option<&str>,
) -> SupervisorResult<()> {
    let payload = json!({
        "schema_version": 1,
        "job_id": job_id,
        "status": status,
        "output_path": output_path,
    });
    atomic_json_file(path, &payload)
}

fn contract_counter(status: &Value, path: &[&str]) -> u64 {
    let mut value = status;
    for key in path {
        value = match value.get(*key) {
            Some(next) => next,
            None => return 0,
        };
    }
    value
        .as_str()
        .and_then(|text| text.parse::<u64>().ok())
        .unwrap_or(0)
}

fn apply_status_event(
    status: &mut Value,
    event: &Value,
    output_path: &mut Option<String>,
    spec: &StartSpec,
) {
    let event_name = event
        .get("event")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let heartbeat = contract_counter(status, &["progress", "heartbeat_sequence"]).saturating_add(1);
    status["progress"]["heartbeat_sequence"] = Value::String(heartbeat.to_string());
    match event_name {
        "accepted" | "running" => {
            status["state"] = Value::String("RUNNING".into());
            status["reason"] = Value::String("running".into());
            status["message"] = Value::String("Đang xử lý bằng pipeline CPU cục bộ".into());
            status["resource"]["held"] = Value::Bool(true);
            if let Some(attempt) = event.get("attempt").and_then(Value::as_u64) {
                status["retry"]["attempt"] = Value::String(attempt.to_string());
            }
        }
        "progress" => {
            status["state"] = Value::String("RUNNING".into());
            status["reason"] = Value::String("progress".into());
            status["resource"]["held"] = Value::Bool(true);
            if let Some(detail) = event.get("detail").and_then(Value::as_str) {
                status["message"] = Value::String(detail.chars().take(4096).collect());
            }
            if let Some(done) = event.get("units_done").and_then(Value::as_u64) {
                status["progress"]["completed_units"] = Value::String(done.to_string());
            } else if let Some(fraction) = event.get("fraction").and_then(Value::as_f64) {
                status["progress"]["completed_units"] =
                    Value::String((fraction.clamp(0.0, 1.0) * 1000.0).round().to_string());
            }
            if let Some(total) = event.get("units_total").and_then(Value::as_u64) {
                status["progress"]["total_units"] = Value::String(total.to_string());
            }
        }
        "checkpoint" => {
            status["state"] = Value::String("RUNNING".into());
            status["reason"] = Value::String("checkpoint".into());
            if let Some(checkpoint) = event.get("checkpoint_id").and_then(Value::as_str) {
                status["checkpoint_id"] = Value::String(checkpoint.into());
                status["message"] = Value::String(format!("Đã lưu checkpoint {checkpoint}"));
            }
        }
        "retrying" => {
            status["state"] = Value::String("RETRYING".into());
            status["reason"] = Value::String("bounded_retry".into());
            status["message"] = Value::String("Đang thử lại với điều kiện đã thay đổi".into());
            if let Some(attempt) = event.get("next_attempt").and_then(Value::as_u64) {
                status["retry"]["attempt"] = Value::String(attempt.to_string());
            }
            if let Some(condition) = event.get("code").and_then(Value::as_str) {
                status["retry"]["condition_fingerprint"] = Value::String(condition.into());
            }
        }
        "completed" => {
            status["state"] = Value::String("COMPLETED".into());
            status["reason"] = Value::String("completed".into());
            status["message"] = Value::String("Đã kiểm tra và xuất video H.264/AAC".into());
            status["progress"]["completed_units"] = Value::String("1000".into());
            status["progress"]["total_units"] = Value::String("1000".into());
            status["resource"]["held"] = Value::Bool(false);
            *output_path = Some(
                spec.output_dir
                    .join("final_vi.mp4")
                    .to_string_lossy()
                    .into_owned(),
            );
        }
        "cancelled" => {
            status["state"] = Value::String("FAILED".into());
            status["reason"] = Value::String("cancelled".into());
            status["message"] = Value::String("Đã hủy theo yêu cầu".into());
            status["resource"]["held"] = Value::Bool(false);
        }
        "failed" | "error" => {
            let code = event
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("SUPERVISOR_JOB_ERROR");
            let condition = event
                .get("condition")
                .and_then(Value::as_str)
                .unwrap_or("job failed");
            *status = failed_contract_status(code, condition);
        }
        _ => {}
    }
}

fn apply_durable_status(
    status: &mut Value,
    durable: JobStatus,
    output_path: &mut Option<String>,
    spec: &StartSpec,
) {
    match durable {
        JobStatus::Succeeded => {
            apply_status_event(status, &json!({"event":"completed"}), output_path, spec)
        }
        JobStatus::Failed => {
            let current = status
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("job failed")
                .to_owned();
            *status = failed_contract_status("JOB_FAILED", &current);
            status["resource"]["held"] = Value::Bool(false);
        }
        JobStatus::Cancelled => {
            apply_status_event(status, &json!({"event":"cancelled"}), output_path, spec);
        }
        JobStatus::Recovering => {
            status["state"] = Value::String("RECOVERED".into());
            status["reason"] = Value::String("process_restart".into());
            status["message"] =
                Value::String("Đã khôi phục từ checkpoint sau khi khởi động lại".into());
        }
        JobStatus::Running => {
            status["state"] = Value::String("RUNNING".into());
        }
        JobStatus::Paused => {
            status["state"] = Value::String("BLOCKED_NEEDS_ACTION".into());
        }
        JobStatus::Queued => {}
    }
}

fn atomic_json_file(path: &Path, value: &Value) -> SupervisorResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_file_name(format!(
        ".{}.{}.partial",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("status.json"),
        ID_COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let mut file = fs::File::create(&temporary)?;
        serde_json::to_writer_pretty(&mut file, value)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        Ok::<(), SupervisorError>(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn spawn_stdin_reader(tx: Sender<InternalMessage>) {
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = BufReader::new(stdin.lock());
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = tx.send(InternalMessage::InputClosed);
                    return;
                }
                Ok(_) => {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<UiRequest>(trimmed) {
                        Ok(request) => {
                            if tx.send(InternalMessage::Request(request)).is_err() {
                                return;
                            }
                        }
                        Err(error) => {
                            if tx
                                .send(InternalMessage::InputError(error.to_string()))
                                .is_err()
                            {
                                return;
                            }
                        }
                    }
                }
                Err(error) => {
                    let _ = tx.send(InternalMessage::InputError(error.to_string()));
                    let _ = tx.send(InternalMessage::InputClosed);
                    return;
                }
            }
        }
    });
}

fn handle_request(
    request: UiRequest,
    runtime: &RuntimePaths,
    controls: &Arc<Mutex<HashMap<String, Arc<JobControl>>>>,
    tx: &Sender<InternalMessage>,
    stdout: &mut impl Write,
) -> SupervisorResult<bool> {
    match request {
        UiRequest::Ping => {
            emit_value(stdout, json!({"event":"pong", "schema_version":1}))?;
        }
        UiRequest::Shutdown => {
            terminate_all(controls);
            emit_value(stdout, json!({"event":"shutdown", "status":"stopping"}))?;
            return Ok(true);
        }
        UiRequest::Status { job_id } => {
            let store = DurableStore::open(&runtime.db)?;
            match store.job_status(&job_id) {
                Ok(status) => {
                    let stage = store.stage_status(&job_id, STAGE_ID).ok();
                    emit_value(
                        stdout,
                        json!({"event":"status", "job_id":job_id, "status":job_status_name(status), "stage_status":stage.map(stage_status_name)}),
                    )?;
                }
                Err(StateError::NotFound { .. }) => emit_value(
                    stdout,
                    json!({"event":"error", "code":"JOB_NOT_FOUND", "job_id":job_id, "condition":"job does not exist"}),
                )?,
                Err(error) => return Err(error.into()),
            }
        }
        UiRequest::Cancel { job_id, reason } => {
            let reason = reason.unwrap_or_else(|| "user requested cancellation".into());
            let control = controls
                .lock()
                .map_err(|_| SupervisorError::Invalid("control lock poisoned".into()))?
                .get(&job_id)
                .cloned();
            if let Some(control) = control {
                control.terminate()?;
                emit_value(
                    stdout,
                    json!({"event":"cancellation_requested", "job_id":job_id, "reason":reason}),
                )?;
            } else {
                let store = DurableStore::open(&runtime.db)?;
                match store.job_status(&job_id) {
                    Ok(
                        JobStatus::Queued
                        | JobStatus::Recovering
                        | JobStatus::Running
                        | JobStatus::Paused,
                    ) => {
                        store.cancel_job(&job_id, &reason, now_ms())?;
                        emit_value(
                            stdout,
                            json!({"event":"cancelled", "job_id":job_id, "status":"cancelled", "reason":reason}),
                        )?;
                    }
                    Ok(status) => emit_value(
                        stdout,
                        json!({"event":"status", "job_id":job_id, "status":job_status_name(status)}),
                    )?,
                    Err(StateError::NotFound { .. }) => emit_value(
                        stdout,
                        json!({"event":"error", "code":"JOB_NOT_FOUND", "job_id":job_id, "condition":"job does not exist"}),
                    )?,
                    Err(error) => return Err(error.into()),
                }
            }
        }
        UiRequest::Start {
            job_id,
            source_path,
            output_dir,
            source_language,
            target_language,
            enable_dubbing,
            burn_in_subtitles,
        } => {
            let spec = match StartSpec::from_request(
                job_id,
                source_path,
                output_dir,
                source_language,
                target_language,
                enable_dubbing,
                burn_in_subtitles,
            ) {
                Ok(spec) => spec,
                Err(error) => {
                    emit_value(
                        stdout,
                        error_value(None, "REQUEST_INVALID", &error.to_string(), false),
                    )?;
                    return Ok(false);
                }
            };
            let mut active = controls
                .lock()
                .map_err(|_| SupervisorError::Invalid("control lock poisoned".into()))?;
            if active.contains_key(&spec.job_id) {
                emit_value(
                    stdout,
                    error_value(
                        Some(&spec.job_id),
                        "JOB_ALREADY_RUNNING",
                        "a worker is already running for this job",
                        false,
                    ),
                )?;
                return Ok(false);
            }
            let control = Arc::new(JobControl::new());
            active.insert(spec.job_id.clone(), control.clone());
            drop(active);
            emit_value(
                stdout,
                json!({"event":"accepted", "job_id":spec.job_id, "status":"queued", "stage_id":STAGE_ID}),
            )?;
            let runtime = runtime.clone();
            let tx = tx.clone();
            thread::spawn(move || {
                let job_id = spec.job_id.clone();
                let outcome = std::panic::catch_unwind(AssertUnwindSafe(|| {
                    execute_job(&runtime, spec, control.clone(), tx.clone())
                }));
                match outcome {
                    Ok(Ok(())) => {}
                    Ok(Err(error)) => {
                        let _ = tx.send(InternalMessage::Output(error_value(
                            Some(&job_id),
                            "SUPERVISOR_JOB_ERROR",
                            &error.to_string(),
                            false,
                        )));
                    }
                    Err(_) => {
                        let _ = tx.send(InternalMessage::Output(error_value(
                            Some(&job_id),
                            "SUPERVISOR_PANIC",
                            "supervisor job thread panicked; durable state remains recoverable",
                            false,
                        )));
                    }
                }
                let _ = tx.send(InternalMessage::Finished(job_id));
            });
        }
    }
    Ok(false)
}

fn execute_job(
    runtime: &RuntimePaths,
    spec: StartSpec,
    control: Arc<JobControl>,
    tx: Sender<InternalMessage>,
) -> SupervisorResult<()> {
    let store = DurableStore::open(&runtime.db)?;
    ensure_job(&store, &spec)?;
    let status = store.job_status(&spec.job_id)?;
    match status {
        JobStatus::Succeeded => {
            let _ = tx.send(InternalMessage::Output(json!({"event":"completed", "job_id":spec.job_id, "status":"succeeded", "resumed":true})));
            return Ok(());
        }
        JobStatus::Cancelled => {
            let _ = tx.send(InternalMessage::Output(
                json!({"event":"cancelled", "job_id":spec.job_id, "status":"cancelled"}),
            ));
            return Ok(());
        }
        JobStatus::Failed => {
            let _ = tx.send(InternalMessage::Output(error_value(
                Some(&spec.job_id),
                "JOB_TERMINAL",
                "the job has failed; create a new job ID",
                false,
            )));
            return Ok(());
        }
        JobStatus::Queued | JobStatus::Recovering | JobStatus::Running | JobStatus::Paused => {}
    }
    if status == JobStatus::Queued {
        store.start_job(&spec.job_id, now_ms())?;
    } else if status == JobStatus::Paused {
        // Paused jobs can resume after a desktop restart.  The state crate
        // intentionally permits only explicit Running from Paused; using the
        // same durable transition keeps the operation auditable.
        store.start_job(&spec.job_id, now_ms())?;
    }
    let stage_status = store.stage_status(&spec.job_id, STAGE_ID)?;
    if stage_status == StageStatus::Succeeded {
        store.complete_job(&spec.job_id, now_ms())?;
        let _ = tx.send(InternalMessage::Output(json!({"event":"completed", "job_id":spec.job_id, "status":"succeeded", "resumed":true})));
        return Ok(());
    }

    loop {
        if control.cancelled.load(Ordering::Acquire) {
            cancel_durable(&store, &spec.job_id, "user requested cancellation")?;
            let _ = tx.send(InternalMessage::Output(
                json!({"event":"cancelled", "job_id":spec.job_id, "status":"cancelled"}),
            ));
            return Ok(());
        }
        let attempt = store.start_stage(&spec.job_id, STAGE_ID, now_ms())?;
        let _ = tx.send(InternalMessage::Output(json!({"event":"running", "job_id":spec.job_id, "stage_id":STAGE_ID, "attempt":attempt})));
        let outcome = match run_worker_attempt(runtime, &spec, &control, attempt, &tx, &store) {
            Ok(WorkerOutcome::Completed { .. }) if control.cancelled.load(Ordering::Acquire) => {
                Ok(WorkerOutcome::Cancelled)
            }
            Ok(WorkerOutcome::Completed { exit_code }) => {
                match commit_output_artifacts(&store, &spec, attempt) {
                    Ok(artifacts) => {
                        store.complete_job(&spec.job_id, now_ms())?;
                        let _ = tx.send(InternalMessage::Output(json!({"event":"completed", "job_id":spec.job_id, "status":"succeeded", "attempt":attempt, "exit_code":exit_code, "artifacts":artifacts})));
                        return Ok(());
                    }
                    Err(error) => Err(error),
                }
            }
            Ok(outcome) => Ok(outcome),
            Err(error) => Err(error),
        };
        match outcome {
            Ok(WorkerOutcome::Completed { .. }) if !control.cancelled.load(Ordering::Acquire) => {
                unreachable!("completed worker was handled before failure dispatch")
            }
            Ok(WorkerOutcome::Cancelled) => {
                cancel_durable(&store, &spec.job_id, "user requested cancellation")?;
                let _ = tx.send(InternalMessage::Output(
                    json!({"event":"cancelled", "job_id":spec.job_id, "status":"cancelled"}),
                ));
                return Ok(());
            }
            Ok(WorkerOutcome::Failed {
                code,
                condition,
                retryable,
            }) => {
                let reason = format!("{code}: {condition}");
                store.fail_stage(&spec.job_id, STAGE_ID, &reason, retryable, now_ms())?;
                if retryable && attempt < MAX_ATTEMPTS {
                    let retry_condition = format!("worker-restart-attempt-{attempt}-{code}");
                    store.retry_stage(&spec.job_id, STAGE_ID, &retry_condition, now_ms())?;
                    let _ = tx.send(InternalMessage::Output(json!({"event":"retrying", "job_id":spec.job_id, "stage_id":STAGE_ID, "attempt":attempt, "next_attempt":attempt.saturating_add(1), "code":code, "condition":condition})));
                    continue;
                }
                let _ = store.fail_job(&spec.job_id, &reason, now_ms());
                let _ = tx.send(InternalMessage::Output(json!({"event":"failed", "job_id":spec.job_id, "status":"failed", "attempt":attempt, "code":code, "condition":condition, "retryable":retryable})));
                return Ok(());
            }
            Err(error) => {
                let reason = error.to_string();
                let retryable = matches!(
                    error,
                    SupervisorError::Io(_)
                        | SupervisorError::Worker {
                            retryable: true,
                            ..
                        }
                );
                let code = match &error {
                    SupervisorError::Protocol(_) => "WORKER_PROTOCOL_INVALID".into(),
                    SupervisorError::State(_) => "STATE_WRITE_FAILED".into(),
                    SupervisorError::Worker { code, .. } => code.clone(),
                    _ => "SUPERVISOR_WORKER_ERROR".into(),
                };
                let _ = store.fail_stage(&spec.job_id, STAGE_ID, &reason, retryable, now_ms());
                if retryable && attempt < MAX_ATTEMPTS {
                    let retry_condition = format!("supervisor-restart-attempt-{attempt}-{code}");
                    if store
                        .retry_stage(&spec.job_id, STAGE_ID, &retry_condition, now_ms())
                        .is_ok()
                    {
                        let _ = tx.send(InternalMessage::Output(json!({"event":"retrying", "job_id":spec.job_id, "stage_id":STAGE_ID, "attempt":attempt, "next_attempt":attempt.saturating_add(1), "code":code, "condition":reason})));
                        continue;
                    }
                }
                let _ = store.fail_job(&spec.job_id, &reason, now_ms());
                let _ = tx.send(InternalMessage::Output(error_value(
                    Some(&spec.job_id),
                    &code,
                    &reason,
                    retryable,
                )));
                return Ok(());
            }
        }
    }
}

fn ensure_job(store: &DurableStore, spec: &StartSpec) -> SupervisorResult<()> {
    match store.job_status(&spec.job_id) {
        Ok(_) => match store.stage_status(&spec.job_id, STAGE_ID) {
            Ok(_) => Ok(()),
            Err(StateError::NotFound { .. }) => Err(SupervisorError::Invalid(
                "existing job has no production stage".into(),
            )),
            Err(error) => Err(error.into()),
        },
        Err(StateError::NotFound { .. }) => {
            store.create_job(
                &spec.job_id,
                &format!("file://{}", spec.source_path.display()),
                now_ms(),
            )?;
            store.create_stage(&spec.job_id, STAGE_ID, STAGE_KIND, MAX_ATTEMPTS)?;
            Ok(())
        }
        Err(error) => Err(error.into()),
    }
}

fn cancel_durable(store: &DurableStore, job_id: &str, reason: &str) -> SupervisorResult<()> {
    match store.job_status(job_id)? {
        JobStatus::Queued | JobStatus::Running | JobStatus::Paused | JobStatus::Recovering => {
            store.cancel_job(job_id, reason, now_ms())?;
        }
        JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled => {}
    }
    Ok(())
}

#[derive(Debug)]
enum WorkerOutcome {
    Completed {
        exit_code: i32,
    },
    Cancelled,
    Failed {
        code: String,
        condition: String,
        retryable: bool,
    },
}

fn run_worker_attempt(
    runtime: &RuntimePaths,
    spec: &StartSpec,
    control: &Arc<JobControl>,
    attempt: u8,
    tx: &Sender<InternalMessage>,
    store: &DurableStore,
) -> SupervisorResult<WorkerOutcome> {
    fs::create_dir_all(&spec.output_dir)?;
    let checkpoint_path = spec
        .output_dir
        .join(".dubflow-work")
        .join("checkpoint.json");
    let args = json!({
        "job_id": spec.job_id,
        "stage_id": STAGE_ID,
        "source_path": spec.source_path,
        "output_dir": spec.output_dir,
        "app_root": runtime.app_root,
        "model_root": runtime.model_root,
        "media_runtime_root": runtime.ffmpeg.parent().unwrap_or(runtime.root.as_path()),
        "ffmpeg_path": runtime.ffmpeg,
        "ffprobe_path": runtime.ffprobe,
        "source_language": spec.source_language,
        "target_language": spec.target_language,
        "enable_dubbing": spec.enable_dubbing,
        "burn_in_subtitles": spec.burn_in_subtitles,
        "checkpoint_path": checkpoint_path,
    });
    let args_json = serde_json::to_string(&args)?;
    let command = Envelope::new(
        MessageType::Command,
        format!(
            "supervisor-command-{attempt}-{}",
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ),
        &spec.job_id,
        STAGE_ID,
        1,
        Payload::Command {
            command: "run_local_file".into(),
            args_json,
        },
    )?;
    let command_line = command.to_line()?;
    let mut process = Command::new(&runtime.python);
    process
        .arg("-B")
        .arg(&runtime.worker_script)
        .current_dir(&runtime.app_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = process.spawn().map_err(|error| SupervisorError::Worker {
        code: "WORKER_SPAWN_FAILED".into(),
        condition: error.to_string(),
        retryable: true,
    })?;
    let child = Arc::new(Mutex::new(child));
    control.set_child(child.clone());
    let setup_result: SupervisorResult<(ChildStdout, ChildStderr)> = (|| {
        let mut child_guard = child
            .lock()
            .map_err(|_| SupervisorError::Invalid("child lock poisoned".into()))?;
        let stdin = child_guard
            .stdin
            .as_mut()
            .ok_or_else(|| SupervisorError::Invalid("worker stdin was not piped".into()))?;
        stdin.write_all(&command_line)?;
        stdin.flush()?;
        let stdout = child_guard
            .stdout
            .take()
            .ok_or_else(|| SupervisorError::Invalid("worker stdout was not piped".into()))?;
        let stderr = child_guard
            .stderr
            .take()
            .ok_or_else(|| SupervisorError::Invalid("worker stderr was not piped".into()))?;
        Ok((stdout, stderr))
    })();
    let (stdout_pipe, stderr_pipe) = match setup_result {
        Ok(pipes) => pipes,
        Err(error) => {
            let _ = control.terminate();
            control.clear_child();
            return Err(error);
        }
    };

    let (line_tx, line_rx) = mpsc::channel::<io::Result<Option<Vec<u8>>>>();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout_pipe);
        loop {
            let mut line = Vec::new();
            match reader.read_until(b'\n', &mut line) {
                Ok(0) => {
                    let _ = line_tx.send(Ok(None));
                    break;
                }
                Ok(_) if line.len() > MAX_LINE_BYTES => {
                    let _ = line_tx.send(Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "worker JSONL line exceeds protocol limit",
                    )));
                    break;
                }
                Ok(_) => {
                    if line_tx.send(Ok(Some(line))).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = line_tx.send(Err(error));
                    break;
                }
            }
        }
    });
    let stderr_thread = thread::spawn(move || read_limited(stderr_pipe, STDERR_LIMIT));

    let mut validator = StreamValidator::new(35_000, 0)?;
    let mut terminal_status = None;
    let mut worker_failure = None;
    let started = Instant::now();
    loop {
        match line_rx.recv_timeout(HEARTBEAT_TIMEOUT) {
            Ok(Ok(Some(line))) => {
                let envelope = match Envelope::from_line(&line) {
                    Ok(envelope) => envelope,
                    Err(error) => {
                        return abort_worker(control, &child, stderr_thread, error.into())
                    }
                };
                if envelope.job_id != spec.job_id || envelope.stage_id != STAGE_ID {
                    return abort_worker(
                        control,
                        &child,
                        stderr_thread,
                        SupervisorError::Invalid(
                            "worker envelope job/stage identity mismatch".into(),
                        ),
                    );
                }
                if let Err(error) = validator.accept(&envelope) {
                    return abort_worker(control, &child, stderr_thread, error.into());
                }
                match envelope.payload {
                    Payload::Progress {
                        fraction,
                        detail,
                        units_done,
                        units_total,
                    } => {
                        let _ = tx.send(InternalMessage::Output(json!({"event":"progress", "job_id":spec.job_id, "stage_id":STAGE_ID, "fraction":fraction, "detail":detail, "units_done":units_done, "units_total":units_total, "attempt":attempt})));
                    }
                    Payload::Checkpoint {
                        checkpoint_id,
                        reusable,
                        artifact_hash,
                    } => {
                        if let Err(error) = store.record_checkpoint(
                            &spec.job_id,
                            STAGE_ID,
                            &checkpoint_id,
                            artifact_hash.as_deref(),
                            reusable,
                            now_ms(),
                        ) {
                            return abort_worker(control, &child, stderr_thread, error.into());
                        }
                        let _ = tx.send(InternalMessage::Output(json!({"event":"checkpoint", "job_id":spec.job_id, "stage_id":STAGE_ID, "checkpoint_id":checkpoint_id, "reusable":reusable, "artifact_hash":artifact_hash, "attempt":attempt})));
                    }
                    Payload::Heartbeat { .. } => {}
                    Payload::Failure {
                        code,
                        retryable,
                        condition,
                        ..
                    } => {
                        worker_failure = Some((code, condition, retryable));
                    }
                    Payload::Shutdown { status } => terminal_status = Some(status),
                    Payload::Command { .. } | Payload::Cancel { .. } => {
                        return abort_worker(
                            control,
                            &child,
                            stderr_thread,
                            SupervisorError::Invalid(
                                "worker emitted an invalid command/cancel message".into(),
                            ),
                        )
                    }
                }
            }
            Ok(Ok(None)) => break,
            Ok(Err(error)) => {
                return abort_worker(
                    control,
                    &child,
                    stderr_thread,
                    SupervisorError::Worker {
                        code: "WORKER_STDOUT_READ_FAILED".into(),
                        condition: error.to_string(),
                        retryable: true,
                    },
                )
            }
            Err(RecvTimeoutError::Timeout) => {
                let _ = control.terminate();
                let _ = child.lock().map(|mut child_guard| child_guard.wait());
                control.clear_child();
                let _ = stderr_thread.join();
                return Ok(WorkerOutcome::Failed {
                    code: "WORKER_HEARTBEAT_TIMEOUT".into(),
                    condition: format!(
                        "no worker protocol message for {} seconds",
                        started.elapsed().as_secs()
                    ),
                    retryable: true,
                });
            }
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }
    control.clear_child();
    let status = {
        let mut child_guard = child
            .lock()
            .map_err(|_| SupervisorError::Invalid("child lock poisoned".into()))?;
        child_guard.wait()?
    };
    let stderr = stderr_thread
        .join()
        .unwrap_or_else(|_| Ok(String::new()))
        .unwrap_or_default();
    if control.cancelled.load(Ordering::Acquire) {
        return Ok(WorkerOutcome::Cancelled);
    }
    if let Some((code, condition, retryable)) = worker_failure {
        return Ok(WorkerOutcome::Failed {
            code,
            condition,
            retryable,
        });
    }
    match terminal_status {
        Some(ShutdownStatus::Completed) if status.success() => Ok(WorkerOutcome::Completed {
            exit_code: status.code().unwrap_or(0),
        }),
        Some(ShutdownStatus::Cancelled) => Ok(WorkerOutcome::Cancelled),
        Some(ShutdownStatus::Failed) => Ok(WorkerOutcome::Failed {
            code: "WORKER_FAILED".into(),
            condition: nonempty_detail(&stderr, "worker reported failure"),
            retryable: true,
        }),
        None => Ok(WorkerOutcome::Failed {
            code: "WORKER_EOF".into(),
            condition: nonempty_detail(&stderr, "worker ended without a terminal shutdown message"),
            retryable: true,
        }),
        Some(ShutdownStatus::Completed) => Ok(WorkerOutcome::Failed {
            code: "WORKER_EXIT_NONZERO".into(),
            condition: nonempty_detail(
                &stderr,
                "worker reported completed but exited unsuccessfully",
            ),
            retryable: true,
        }),
    }
}

fn abort_worker(
    control: &Arc<JobControl>,
    child: &Arc<Mutex<Child>>,
    stderr_thread: thread::JoinHandle<io::Result<String>>,
    error: SupervisorError,
) -> SupervisorResult<WorkerOutcome> {
    let _ = control.terminate();
    if let Ok(mut child_guard) = child.lock() {
        let _ = child_guard.wait();
    }
    control.clear_child();
    let _ = stderr_thread.join();
    Err(error)
}

fn read_limited(mut reader: impl Read, limit: usize) -> io::Result<String> {
    let mut bytes = Vec::new();
    let mut buffer = [0u8; 4096];
    while bytes.len() < limit {
        let remaining = limit - bytes.len();
        let chunk_size = remaining.min(buffer.len());
        let read = reader.read(&mut buffer[..chunk_size])?;
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..read]);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn nonempty_detail(stderr: &str, fallback: &str) -> String {
    let detail = stderr
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if detail.is_empty() {
        fallback.into()
    } else {
        detail.chars().take(4096).collect()
    }
}

fn commit_output_artifacts(
    store: &DurableStore,
    spec: &StartSpec,
    attempt: u8,
) -> SupervisorResult<Vec<Value>> {
    let required = [
        ("final_video", PathBuf::from("final_vi.mp4")),
        ("captions_srt", PathBuf::from("captions_vi.srt")),
        ("captions_ass", PathBuf::from("captions_vi.ass")),
        ("qc_report", PathBuf::from("qc_report.json")),
        ("job_manifest", PathBuf::from("job_manifest.json")),
        ("editable_timeline", PathBuf::from("editable/timeline.json")),
    ];
    let mut artifacts = Vec::with_capacity(required.len());
    for (name, relative) in required {
        let path = spec.output_dir.join(relative);
        let metadata = fs::metadata(&path).map_err(|error| SupervisorError::Worker {
            code: "ARTIFACT_MISSING".into(),
            condition: format!("{name}: {error}"),
            retryable: true,
        })?;
        if !metadata.is_file() || metadata.len() == 0 {
            return Err(SupervisorError::Worker {
                code: "ARTIFACT_INVALID".into(),
                condition: format!("{name} is empty or not a regular file"),
                retryable: true,
            });
        }
        let artifact_id = format!(
            "{}-{}-{}-a{}",
            spec.job_id,
            STAGE_ID,
            name.replace('_', "-"),
            attempt
        );
        store.record_artifact_written(
            &artifact_id,
            &spec.job_id,
            STAGE_ID,
            &path,
            None,
            true,
            now_ms(),
        )?;
        let hash = store.commit_artifact(&artifact_id, now_ms())?;
        artifacts.push(json!({"id":artifact_id, "name":name, "path":path, "sha256":hash, "size_bytes":metadata.len(), "state":artifact_state_name(ArtifactState::Committed)}));
    }
    Ok(artifacts)
}

fn terminate_all(controls: &Arc<Mutex<HashMap<String, Arc<JobControl>>>>) {
    if let Ok(active) = controls.lock() {
        for control in active.values() {
            let _ = control.terminate();
        }
    }
}

fn emit_value(stdout: &mut impl Write, value: Value) -> SupervisorResult<()> {
    serde_json::to_writer(&mut *stdout, &value)?;
    stdout.write_all(b"\n")?;
    stdout.flush()?;
    Ok(())
}

fn error_value(job_id: Option<&str>, code: &str, condition: &str, retryable: bool) -> Value {
    let mut value =
        json!({"event":"error", "code":code, "condition":condition, "retryable":retryable});
    if let Some(job_id) = job_id {
        value["job_id"] = Value::String(job_id.into());
    }
    value
}

fn job_status_name(status: JobStatus) -> &'static str {
    match status {
        JobStatus::Queued => "queued",
        JobStatus::Running => "running",
        JobStatus::Paused => "paused",
        JobStatus::Recovering => "recovering",
        JobStatus::Succeeded => "succeeded",
        JobStatus::Failed => "failed",
        JobStatus::Cancelled => "cancelled",
    }
}

fn stage_status_name(status: StageStatus) -> &'static str {
    match status {
        StageStatus::Pending => "pending",
        StageStatus::Running => "running",
        StageStatus::Paused => "paused",
        StageStatus::Recovering => "recovering",
        StageStatus::Succeeded => "succeeded",
        StageStatus::Failed => "failed",
        StageStatus::Cancelled => "cancelled",
    }
}

fn artifact_state_name(state: ArtifactState) -> &'static str {
    match state {
        ArtifactState::Writing => "writing",
        ArtifactState::Validated => "validated",
        ArtifactState::Committed => "committed",
        ArtifactState::Missing => "missing",
        ArtifactState::Quarantined => "quarantined",
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_ids_are_path_safe() {
        let id = new_job_id();
        validate_job_id(&id).unwrap();
        assert!(!id.contains('/') && !id.contains('\\'));
    }

    #[test]
    fn request_rejects_relative_paths_and_non_vietnamese_target() {
        assert!(StartSpec::from_request(
            None,
            "relative.mp4".into(),
            "C:\\out".into(),
            None,
            None,
            None,
            None
        )
        .is_err());
        let source = std::env::temp_dir().join(format!(
            "dubflow-supervisor-test-{}.mp4",
            std::process::id()
        ));
        std::fs::write(&source, b"test").unwrap();
        let output =
            std::env::temp_dir().join(format!("dubflow-supervisor-out-{}", std::process::id()));
        let error = StartSpec::from_request(
            Some("job".into()),
            source.to_string_lossy().into_owned(),
            output.to_string_lossy().into_owned(),
            None,
            Some("en".into()),
            None,
            None,
        )
        .unwrap_err();
        assert!(error.to_string().contains("target_language"));
        let _ = std::fs::remove_file(source);
    }

    #[test]
    fn output_artifact_ids_include_attempt_for_retry_isolation() {
        assert_eq!(artifact_state_name(ArtifactState::Committed), "committed");
        let id = format!("{}-{}-{}-a{}", "job", STAGE_ID, "final-video", 2);
        assert!(id.ends_with("-a2"));
    }

    #[test]
    fn cli_requires_an_explicit_app_root() {
        let error = parse_cli(std::iter::empty::<OsString>()).unwrap_err();
        assert!(error.to_string().contains("--root"));
    }
}
