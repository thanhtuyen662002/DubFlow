//! H.264 encoder choice for exports and light copies: NVIDIA NVENC first when it
//! really works on this PC, then Media Foundation, then the bundled OpenH264.
//!
//! A GPU is an accelerator, never a requirement (AGENTS.md). The shipped LGPL
//! FFmpeg contains `h264_nvenc`, but it only runs with an NVIDIA driver, so one
//! tiny test encode (with CUDA decoding) decides, once per FFmpeg executable.
//! Every caller keeps its CPU fallback. `DUBFLOW_ACCELERATOR=cpu` skips the GPU.
//! Mirrors engine/dubflow/media/encoders.py.

use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

pub const NVENC: &str = "h264_nvenc";
pub const CPU_ENCODERS: [&str; 2] = ["h264_mf", "libopenh264"];
const PROBE_LIMIT: Duration = Duration::from_secs(30);

static PROBED: LazyLock<Mutex<HashMap<PathBuf, bool>>> = LazyLock::new(|| Mutex::new(HashMap::new()));

fn cpu_only() -> bool {
    std::env::var("DUBFLOW_ACCELERATOR").is_ok_and(|value| value.trim().eq_ignore_ascii_case("cpu"))
}

/// Encoders to try, in order.
pub fn encoders(ffmpeg: &Path) -> Vec<&'static str> {
    let mut chain = Vec::with_capacity(3);
    if !cpu_only() && nvenc_available(ffmpeg) {
        chain.push(NVENC);
    }
    chain.extend(CPU_ENCODERS);
    chain
}

/// Whether this FFmpeg encodes H.264 on an NVIDIA GPU right now (cached).
pub fn nvenc_available(ffmpeg: &Path) -> bool {
    let Ok(mut probed) = PROBED.lock() else { return false };
    if let Some(&usable) = probed.get(ffmpeg) {
        return usable;
    }
    let usable = test_encode(ffmpeg);
    probed.insert(ffmpeg.to_path_buf(), usable);
    usable
}

fn test_encode(ffmpeg: &Path) -> bool {
    let mut command = Command::new(ffmpeg);
    // 256x256: NVENC refuses frames below its minimum size. "-hwaccel cuda"
    // proves the CUDA device opens, since a render fails outright if it cannot.
    command.args(["-hide_banner", "-v", "error", "-nostdin", "-hwaccel", "cuda", "-f", "lavfi",
            "-i", "color=c=black:s=256x256:r=25:d=0.2", "-c:v", NVENC, "-pix_fmt", "yuv420p", "-f", "null", "-"])
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(crate::CREATE_NO_WINDOW);
    let Ok(mut child) = command.spawn() else { return false };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if started.elapsed() < PROBE_LIMIT => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// Input options for a video input: decode on the GPU alongside NVENC. Frames
/// return to system memory, so every CPU filter still applies.
pub fn decode_args(encoder: &str) -> Vec<OsString> {
    if encoder == NVENC { vec!["-hwaccel".into(), "cuda".into()] } else { Vec::new() }
}

/// `-c:v` and rate control for `encoder` at an average `bitrate` in bits/s.
pub fn rate_args(encoder: &str, bitrate: u64) -> Vec<OsString> {
    let mut args: Vec<OsString> = vec!["-c:v".into(), encoder.into(), "-b:v".into(), bitrate.to_string().into()];
    let peak = [OsString::from("-maxrate"), (bitrate * 3 / 2).to_string().into(), "-bufsize".into(), (bitrate * 2).to_string().into()];
    if encoder == NVENC {
        // p4: NVENC's balanced preset, several times real time on an RTX 3060.
        args.extend(["-preset", "p4", "-tune", "hq", "-rc", "vbr"].map(OsString::from));
        args.extend(peak);
        args.extend(["-spatial-aq", "1", "-bf", "3"].map(OsString::from));
    } else if encoder == "libopenh264" {
        args.extend(peak);
    }
    args
}

/// FFmpeg thread options from Cài đặt → Tài nguyên máy (`DUBFLOW_THREADS`):
/// nothing when unset, so FFmpeg keeps its own automatic choice.
pub fn thread_args(complex_graph: bool) -> Vec<OsString> {
    let Some(threads) = configured_threads() else { return Vec::new() };
    let filter = if complex_graph { "-filter_complex_threads" } else { "-filter_threads" };
    vec!["-threads".into(), threads.to_string().into(), filter.into(), threads.to_string().into()]
}

/// Exports rendered side by side (Chỉnh sửa, xuất song song): half the thread
/// budget, at most 4 on the GPU (NVENC sessions) or 6 on the CPU. Mirrors
/// engine/dubflow/resources.py `parallel_renders`.
pub fn parallel_renders(gpu: bool) -> usize {
    (thread_budget() / 2).clamp(1, if gpu { 4 } else { 6 })
}

/// Threads one of `workers` side-by-side renders may use.
pub fn part_threads(workers: usize) -> usize {
    (thread_budget() / workers.max(1)).max(1)
}

fn thread_budget() -> usize {
    configured_threads().unwrap_or_else(machine_threads)
}

fn machine_threads() -> usize {
    std::thread::available_parallelism().map_or(1, |count| count.get())
}

fn configured_threads() -> Option<usize> {
    let value = std::env::var("DUBFLOW_THREADS").ok()?;
    let value = value.trim();
    if value.eq_ignore_ascii_case("all") {
        return Some(machine_threads());
    }
    value.parse::<usize>().ok().filter(|&count| count > 0).map(|count| count.min(machine_threads()))
}

/// Threads DubFlow suggests on this PC: keep one or two for Windows and the app itself.
fn recommended_threads(machine: usize) -> usize {
    match machine {
        0..=3 => machine.max(1),
        4..=7 => machine - 1,
        _ => machine - 2,
    }
}

#[cfg(windows)]
fn memory_bytes() -> Option<u64> {
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
    let mut status: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
    status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
    (unsafe { GlobalMemoryStatusEx(&mut status) } != 0).then_some(status.ullTotalPhys)
}

#[cfg(not(windows))]
fn memory_bytes() -> Option<u64> {
    None
}

/// "NVIDIA GeForce RTX 3060 (12 GB)" from the driver's own tool, if there is one.
fn nvidia_name() -> Option<String> {
    let mut command = Command::new("nvidia-smi");
    command.args(["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        .stdin(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(crate::CREATE_NO_WINDOW);
    let output = command.output().ok().filter(|output| output.status.success())?;
    let line = String::from_utf8_lossy(&output.stdout).lines().next()?.trim().to_owned();
    let (name, memory) = line.rsplit_once(',').unwrap_or((line.as_str(), ""));
    let name = name.trim().chars().take(80).collect::<String>();
    if name.is_empty() {
        return None;
    }
    Some(match memory.trim().parse::<f64>() {
        Ok(mib) if mib > 0.0 => format!("{name} ({:.0} GB)", mib / 1024.0),
        _ => name,
    })
}

/// What Cài đặt shows under Tài nguyên máy, and the default it saves on first run.
#[tauri::command]
pub async fn machine_profile() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let threads = machine_threads();
        serde_json::json!({
            "threads": threads,
            "recommended": recommended_threads(threads),
            "memory_gb": memory_bytes().map(|bytes| (bytes as f64 / 1024f64.powi(3)).round() as u64),
            "gpu": nvidia_name(),
        })
    }).await.map_err(|_| crate::fail("Không đọc được cấu hình máy"))
}

/// Apply Cài đặt to every process DubFlow starts from now on: `threads` 0 is
/// "Dùng toàn bộ tài nguyên máy", otherwise the most threads one task may use;
/// `cpu_only` keeps video encoding (and every model) off the GPU.
#[tauri::command]
pub fn set_performance(threads: u32, cpu_only: bool) {
    // Children inherit the host's environment; nothing reads these concurrently in a way a torn value could hurt.
    if threads == 0 {
        std::env::set_var("DUBFLOW_THREADS", "all");
    } else {
        std::env::set_var("DUBFLOW_THREADS", threads.to_string());
    }
    if cpu_only {
        std::env::set_var("DUBFLOW_ACCELERATOR", "cpu");
    } else {
        std::env::remove_var("DUBFLOW_ACCELERATOR");
    }
}

/// How the progress text names the encoder.
pub fn label(encoder: &str) -> &'static str {
    if encoder == NVENC { " bằng GPU NVIDIA" } else { "" }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(args: Vec<OsString>) -> String {
        args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect::<Vec<_>>().join(" ")
    }

    #[test]
    fn nvenc_gets_gpu_decode_and_peak_limited_vbr() {
        assert_eq!(text(decode_args(NVENC)), "-hwaccel cuda");
        assert_eq!(text(rate_args(NVENC, 3_000_000)),
            "-c:v h264_nvenc -b:v 3000000 -preset p4 -tune hq -rc vbr -maxrate 4500000 -bufsize 6000000 -spatial-aq 1 -bf 3");
    }

    #[test]
    fn cpu_encoders_keep_their_arguments() {
        assert!(decode_args("h264_mf").is_empty());
        assert_eq!(text(rate_args("h264_mf", 3_000_000)), "-c:v h264_mf -b:v 3000000");
        assert_eq!(text(rate_args("libopenh264", 2_000_000)), "-c:v libopenh264 -b:v 2000000 -maxrate 3000000 -bufsize 4000000");
    }

    #[test]
    fn the_suggested_budget_leaves_room_for_windows() {
        assert_eq!(recommended_threads(1), 1);
        assert_eq!(recommended_threads(4), 3);
        assert_eq!(recommended_threads(12), 10);
        assert_eq!(recommended_threads(24), 22);
    }

    #[test]
    fn a_missing_ffmpeg_keeps_the_cpu_encoders() {
        assert_eq!(encoders(Path::new("/definitely/not/ffmpeg.exe")), CPU_ENCODERS.to_vec());
    }
}
