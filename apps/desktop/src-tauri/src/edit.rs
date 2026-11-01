//! Cắt ghép (Issue #408): the edited timeline of a finished `final_vi.mp4`
//! rendered into a new MP4 (never over an existing file), with its subtitles
//! remapped by the front end and written beside it. The edit itself is a
//! draft in `<output>\.dubflow-work\edit\project.json`; renders report in
//! `status.json` there and finished ones are listed in `exports.json`.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{display_path, fail, output_file, source, version_root, CREATE_NO_WINDOW};

const PROJECT_LIMIT: usize = 1024 * 1024;
const MAX_SEGMENTS: usize = 500;
const MIN_SEGMENT: f64 = 0.1;
const MAX_CUES: usize = 20_000;
const MAX_CUE_TEXT: usize = 2_000;
const SRT_LIMIT: u64 = 8 * 1024 * 1024;
const EXPORTS_LIMIT: u64 = 4 * 1024 * 1024;
const EXPORTS_KEPT: usize = 200;
const QUALITIES: [&str; 3] = ["source", "1080p", "720p"];
/// The shipped FFmpeg is the LGPL build (no libx264): Media Foundation's H.264
/// encoder first, the bundled OpenH264 when Media Foundation refuses.
const ENCODERS: [&str; 2] = ["h264_mf", "libopenh264"];
const BUSY: &str = "Video này đang được xuất ở cửa sổ DubFlow khác";

struct Running {
    child: Mutex<Option<Child>>,
    cancelled: AtomicBool,
}

static RUNNING: LazyLock<Mutex<HashMap<PathBuf, Arc<Running>>>> = LazyLock::new(|| Mutex::new(HashMap::new()));

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Segment {
    start: f64,
    end: f64,
    speed: f64,
    volume: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Cue {
    start_ms: u64,
    end_ms: u64,
    text: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExportRequest {
    output_path: String,
    segments: Vec<Segment>,
    quality: String,
    folder: String,
    file_name: String,
    /// Subtitles already remapped to the edited timeline by the front end.
    cues: Vec<Cue>,
}

#[derive(Debug, Clone, PartialEq)]
struct Probe {
    duration: f64,
    width: u32,
    height: u32,
    /// "num/den", validated.
    fps: String,
    has_audio: bool,
    size: u64,
}

/// A finished job's output folder: the parent of its `final_vi.mp4` below "DubFlow Output".
fn edit_dir(output_path: &str) -> Result<PathBuf, String> {
    let video = output_file(output_path)?;
    if video.file_name().is_none_or(|name| name != "final_vi.mp4") {
        return Err(fail("Chỉ chỉnh sửa được video final_vi.mp4 đã hoàn tất"));
    }
    Ok(video.parent().ok_or_else(|| fail("output has no directory"))?.to_path_buf())
}

fn work_dir(dir: &Path) -> PathBuf {
    dir.join(".dubflow-work").join("edit")
}

fn media_tool(name: &str) -> Result<PathBuf, String> {
    let path = version_root()?.join("runtime").join("media").join(name);
    if !path.is_file() {
        return Err(fail("Không tìm thấy FFmpeg trong bản cài DubFlow"));
    }
    Ok(path)
}

fn frame_rate(value: &Value) -> Option<String> {
    let (numerator, denominator) = value.as_str()?.split_once('/')?;
    let (numerator, denominator) = (numerator.parse::<u32>().ok()?, denominator.parse::<u32>().ok()?);
    let fps = f64::from(numerator) / f64::from(denominator.max(1));
    (denominator > 0 && (1.0..=120.0).contains(&fps)).then(|| format!("{numerator}/{denominator}"))
}

/// Duration, frame size, frame rate and audio presence from FFprobe's JSON.
fn parse_probe(text: &str) -> Option<Probe> {
    let value: Value = serde_json::from_str(text).ok()?;
    let streams = value["streams"].as_array()?;
    let video = streams.iter().find(|stream| stream["codec_type"] == "video")?;
    let number = |value: &Value| value.as_str().and_then(|text| text.parse::<f64>().ok()).or_else(|| value.as_f64());
    let duration = number(&value["format"]["duration"]).filter(|seconds| seconds.is_finite() && *seconds > 0.0)?;
    Some(Probe {
        duration,
        width: u32::try_from(video["width"].as_u64()?).ok()?,
        height: u32::try_from(video["height"].as_u64()?).ok()?,
        fps: frame_rate(&video["avg_frame_rate"]).or_else(|| frame_rate(&video["r_frame_rate"])).unwrap_or_else(|| "30/1".into()),
        has_audio: streams.iter().any(|stream| stream["codec_type"] == "audio"),
        size: number(&value["format"]["size"]).map_or(0, |bytes| bytes.max(0.0) as u64),
    })
}

fn probe(video: &Path) -> Result<Probe, String> {
    let mut command = Command::new(media_tool("ffprobe.exe")?);
    command.args(["-v", "error", "-show_entries", "format=duration,size:stream=codec_type,width,height,avg_frame_rate,r_frame_rate", "-of", "json"])
        .arg(video)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFprobe: {error}")))?;
    String::from_utf8(output.stdout).ok().as_deref().and_then(parse_probe)
        .ok_or_else(|| fail("Không đọc được thời lượng video"))
}

/// Seconds the edited video lasts.
fn output_seconds(segments: &[Segment]) -> f64 {
    segments.iter().map(|segment| (segment.end - segment.start) / segment.speed).sum()
}

/// The user's name without ".mp4", made safe for Windows; empty when nothing usable was typed.
fn base_name(file_name: &str) -> String {
    let trimmed = file_name.trim();
    let stem = if trimmed.len() >= 4 && trimmed.is_char_boundary(trimmed.len() - 4) && trimmed[trimmed.len() - 4..].eq_ignore_ascii_case(".mp4") {
        &trimmed[..trimmed.len() - 4]
    } else {
        trimmed
    };
    if stem.trim().is_empty() { String::new() } else { source::safe_file_name(stem) }
}

/// Segments clamped to the source, or why the request cannot be rendered.
fn validate(request: &ExportRequest, duration: f64) -> Result<Vec<Segment>, String> {
    if !(1..=MAX_SEGMENTS).contains(&request.segments.len()) {
        return Err(fail("Bản chỉnh sửa cần từ 1 đến 500 đoạn"));
    }
    let mut segments = Vec::with_capacity(request.segments.len());
    for segment in &request.segments {
        if ![segment.start, segment.end, segment.speed, segment.volume].iter().all(|value| value.is_finite()) {
            return Err(fail("Bản chỉnh sửa có số không hợp lệ"));
        }
        let end = segment.end.min(duration);
        if segment.start < 0.0 || segment.end > duration + 0.05 || end - segment.start < MIN_SEGMENT - 1e-6 {
            return Err(fail("Mỗi đoạn phải nằm trong video và dài ít nhất 0,1 giây"));
        }
        if !(0.5..=2.0).contains(&segment.speed) {
            return Err(fail("Tốc độ mỗi đoạn phải từ 0,5× đến 2×"));
        }
        if !(0.0..=2.0).contains(&segment.volume) {
            return Err(fail("Âm lượng mỗi đoạn phải từ 0 đến 200%"));
        }
        segments.push(Segment { end, ..*segment });
    }
    if !QUALITIES.contains(&request.quality.as_str()) {
        return Err(fail("Chất lượng xuất không hợp lệ"));
    }
    if base_name(&request.file_name).is_empty() {
        return Err(fail("Hãy đặt tên cho file video"));
    }
    let limit_ms = (output_seconds(&segments) * 1000.0).ceil() as u64 + 1000;
    if request.cues.len() > MAX_CUES {
        return Err(fail("Quá nhiều câu phụ đề"));
    }
    for cue in &request.cues {
        if cue.end_ms <= cue.start_ms || cue.end_ms > limit_ms || cue.text.chars().count() > MAX_CUE_TEXT
            || cue.text.chars().any(|c| c.is_control() && c != '\n' && c != '\r' && c != '\t') {
            return Err(fail("Phụ đề của bản chỉnh sửa không hợp lệ"));
        }
    }
    Ok(segments)
}

/// An existing folder chosen for the export.
fn target_folder(folder: &str) -> Result<PathBuf, String> {
    let path = Path::new(folder);
    if !path.is_absolute() {
        return Err(fail("Hãy chọn thư mục lưu trên máy"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy thư mục lưu"))?;
    if !resolved.is_dir() {
        return Err(fail("Không tìm thấy thư mục lưu"));
    }
    Ok(resolved)
}

fn partial_of(file: &Path) -> PathBuf {
    let mut name = file.file_name().map(OsString::from).unwrap_or_default();
    name.push(".partial");
    file.with_file_name(name)
}

/// `<name>.mp4`, else `<name> (2).mp4`…: never a file (or its subtitles) that exists.
fn unique_target(folder: &Path, name: &str) -> Result<PathBuf, String> {
    (1..=999).map(|number| folder.join(source::numbered_file_name(name, "mp4", number)))
        .find(|file| !file.exists() && !file.with_extension("srt").exists() && !partial_of(file).exists())
        .ok_or_else(|| fail("Thư mục đã có quá nhiều file cùng tên"))
}

/// Seconds as FFmpeg reads them, to the millisecond, without trailing zeros.
fn num(value: f64) -> String {
    let text = format!("{value:.3}");
    let trimmed = text.trim_end_matches('0').trim_end_matches('.');
    if trimmed == "-0" { "0".into() } else { trimmed.into() }
}

/// `atempo` filters for a speed; each one stays within 0.5–2.
fn atempo_chain(speed: f64) -> Vec<String> {
    let mut rest = speed;
    let mut chain = Vec::new();
    while rest > 2.0 + 1e-9 {
        chain.push("atempo=2".to_owned());
        rest /= 2.0;
    }
    while rest < 0.5 - 1e-9 {
        chain.push("atempo=0.5".to_owned());
        rest /= 0.5;
    }
    if (rest - 1.0).abs() > 1e-9 {
        chain.push(format!("atempo={}", num(rest)));
    }
    chain
}

/// The filter graph: each segment trimmed and retimed (video and audio), then
/// concatenated; "1080p"/"720p" scale the short side, keeping aspect and even sizes.
fn filter_script(segments: &[Segment], has_audio: bool, quality: &str) -> String {
    let mut lines = Vec::new();
    let mut pads = String::new();
    for (index, segment) in segments.iter().enumerate() {
        let (start, end) = (num(segment.start), num(segment.end));
        let pts = if segment.speed == 1.0 { "PTS-STARTPTS".to_owned() } else { format!("(PTS-STARTPTS)/{}", num(segment.speed)) };
        lines.push(format!("[0:v]trim=start={start}:end={end},setpts={pts}[v{index}]"));
        pads.push_str(&format!("[v{index}]"));
        if has_audio {
            let mut audio = vec![format!("atrim=start={start}:end={end}"), "asetpts=PTS-STARTPTS".to_owned()];
            audio.extend(atempo_chain(segment.speed));
            if segment.volume != 1.0 {
                audio.push(format!("volume={}", num(segment.volume)));
            }
            lines.push(format!("[0:a]{}[a{index}]", audio.join(",")));
            pads.push_str(&format!("[a{index}]"));
        }
    }
    let short = match quality { "1080p" => Some(1080), "720p" => Some(720), _ => None };
    let video_out = if short.is_some() { "vc" } else { "v" };
    lines.push(format!("{pads}concat=n={}:v=1:a={}[{video_out}]{}", segments.len(), u8::from(has_audio), if has_audio { "[a]" } else { "" }));
    if let Some(short) = short {
        lines.push(format!("[vc]scale=w='if(gte(iw,ih),-2,{short})':h='if(gte(iw,ih),{short},-2)',setsar=1[v]"));
    }
    lines.join(";\n")
}

/// Frame size after scaling, as the filter computes it.
fn scaled_size(width: u32, height: u32, quality: &str) -> (u32, u32) {
    let short = match quality { "1080p" => 1080.0, "720p" => 720.0, _ => return (width, height) };
    let even = |value: f64| ((value / 2.0).round() as u32 * 2).max(2);
    if width >= height {
        (even(f64::from(width) * short / f64::from(height)), short as u32)
    } else {
        (short as u32, even(f64::from(height) * short / f64::from(width)))
    }
}

/// Same rule as the engine's renders: 0.12 bit per pixel per frame, 1.5–16 Mbit/s.
fn video_bitrate(width: u32, height: u32, fps: &str) -> u64 {
    let rate = fps.split_once('/').and_then(|(n, d)| Some(n.parse::<f64>().ok()? / d.parse::<f64>().ok()?.max(1.0))).unwrap_or(30.0);
    (0.12 * f64::from(width) * f64::from(height) * rate.clamp(1.0, 120.0)).clamp(1_500_000.0, 16_000_000.0).round() as u64
}

fn ffmpeg_args(input: &Path, script: &Path, has_audio: bool, encoder: &str, bitrate: u64, fps: &str, output: &Path) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-v", "error", "-y", "-i"].map(OsString::from).to_vec();
    args.push(input.into());
    args.push("-/filter_complex".into());
    args.push(script.into());
    args.extend(["-map", "[v]"].map(OsString::from));
    if has_audio {
        args.extend(["-map", "[a]"].map(OsString::from));
    }
    args.extend(["-c:v", encoder, "-b:v"].map(OsString::from));
    args.push(bitrate.to_string().into());
    if encoder == "libopenh264" {
        args.extend(["-maxrate".into(), (bitrate * 3 / 2).to_string().into(), "-bufsize".into(), (bitrate * 2).to_string().into()]);
    }
    args.extend(["-pix_fmt", "yuv420p", "-r", fps].map(OsString::from));
    if has_audio {
        args.extend(["-c:a", "aac", "-b:a", "192k"].map(OsString::from));
    }
    args.extend(["-sn", "-dn", "-map_chapters", "-1", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4"].map(OsString::from));
    args.push(output.into());
    args
}

fn srt_time(ms: u64) -> String {
    format!("{:02}:{:02}:{:02},{:03}", ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60, ms % 1000)
}

fn format_srt(cues: &[Cue]) -> String {
    cues.iter().enumerate().map(|(index, cue)| {
        let text = cue.text.replace("\r\n", "\n").replace('\r', "\n");
        format!("{}\n{} --> {}\n{}\n\n", index + 1, srt_time(cue.start_ms), srt_time(cue.end_ms), text.trim())
    }).collect()
}

fn parse_srt_time(text: &str) -> Option<u64> {
    let (clock, millis) = text.trim().split_once([',', '.'])?;
    let parts: Vec<u64> = clock.split(':').map(|part| part.trim().parse().ok()).collect::<Option<_>>()?;
    let [hours, minutes, seconds] = parts[..] else { return None };
    // "1.5" is 1 500 ms: the fraction is read as at most three digits, scaled.
    let digits = millis.trim();
    let digits = digits.get(..digits.len().min(3))?;
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let millis = digits.parse::<u64>().ok()? * 10u64.pow(3 - digits.len() as u32);
    (minutes < 60 && seconds < 60).then_some(((hours * 60 + minutes) * 60 + seconds) * 1000 + millis)
}

/// The job's subtitles; blocks that do not parse are skipped.
fn parse_srt(text: &str) -> Vec<Cue> {
    let text = text.trim_start_matches('\u{feff}').replace("\r\n", "\n").replace('\r', "\n");
    let mut cues = Vec::new();
    for block in text.split("\n\n") {
        let mut lines = block.lines().skip_while(|line| !line.contains("-->"));
        let Some(times) = lines.next() else { continue };
        let Some((start, end)) = times.split_once("-->") else { continue };
        let (Some(start_ms), Some(end_ms)) = (parse_srt_time(start), parse_srt_time(end.split_whitespace().next().unwrap_or(""))) else { continue };
        let body = lines.collect::<Vec<_>>().join("\n");
        if end_ms > start_ms && !body.trim().is_empty() {
            cues.push(Cue { start_ms, end_ms, text: body.trim().to_owned() });
        }
    }
    cues
}

fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let partial = partial_of(path);
    fs::write(&partial, bytes).and_then(|()| fs::rename(&partial, path))
}

fn write_status(work: &Path, state: &str, percent: f64, message: &str, output: Option<&Path>) {
    let value = json!({"schema_version": 1, "state": state, "percent": percent.clamp(0.0, 100.0).floor(), "message": message,
        "output_path": output.map(display_path)});
    let _ = write_atomic(&work.join("status.json"), &serde_json::to_vec(&value).unwrap_or_default());
}

fn read_exports(work: &Path) -> Vec<Value> {
    let path = work.join("exports.json");
    if fs::metadata(&path).map_or(true, |metadata| metadata.len() > EXPORTS_LIMIT) {
        return Vec::new();
    }
    fs::read(path).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| value.as_array().cloned()).unwrap_or_default()
}

fn append_export(work: &Path, entry: Value) {
    let mut exports = read_exports(work);
    exports.push(entry);
    let start = exports.len().saturating_sub(EXPORTS_KEPT);
    let _ = write_atomic(&work.join("exports.json"), &serde_json::to_vec(&exports[start..]).unwrap_or_default());
}

fn unix_now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_secs())
}

/// The draft for this video, its facts and its subtitles.
#[tauri::command]
pub async fn edit_project_load(output_path: String) -> Result<Value, String> {
    let dir = edit_dir(&output_path)?;
    let facts = probe(&dir.join("final_vi.mp4"))?;
    let project_path = work_dir(&dir).join("project.json");
    let project = fs::metadata(&project_path).ok().filter(|metadata| metadata.len() as usize <= PROJECT_LIMIT)
        .and_then(|_| fs::read(&project_path).ok())
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .filter(|value| value["schema_version"] == 1);
    let captions = dir.join("captions_vi.srt");
    let cues = fs::metadata(&captions).ok().filter(|metadata| metadata.len() <= SRT_LIMIT)
        .and_then(|_| fs::read(&captions).ok())
        .map(|bytes| parse_srt(&String::from_utf8_lossy(&bytes))).unwrap_or_default();
    Ok(json!({"duration": facts.duration, "width": facts.width, "height": facts.height, "size_bytes": facts.size,
        "has_audio": facts.has_audio, "project": project, "cues": cues}))
}

fn check_project(project: &Value) -> Result<Vec<u8>, String> {
    let bytes = serde_json::to_vec(project).map_err(|_| fail("Bản nháp không hợp lệ"))?;
    let segments = project["segments"].as_array().map_or(0, Vec::len);
    if bytes.len() > PROJECT_LIMIT || project["schema_version"] != 1 || !(1..=MAX_SEGMENTS).contains(&segments) {
        return Err(fail("Bản nháp chỉnh sửa không hợp lệ"));
    }
    Ok(bytes)
}

#[tauri::command]
pub fn edit_project_save(output_path: String, project: Value) -> Result<(), String> {
    let bytes = check_project(&project)?;
    let work = work_dir(&edit_dir(&output_path)?);
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))?;
    write_atomic(&work.join("project.json"), &bytes).map_err(|_| fail("Không lưu được bản nháp chỉnh sửa"))
}

struct ExportJob {
    work: PathBuf,
    input: PathBuf,
    script: PathBuf,
    folder: PathBuf,
    name: String,
    target: PathBuf,
    srt: Option<String>,
    seconds: f64,
    has_audio: bool,
    bitrate: u64,
    fps: String,
    ffmpeg: PathBuf,
    quality: String,
    segments: Vec<Segment>,
}

fn stop(running: &Running) {
    running.cancelled.store(true, Ordering::SeqCst);
    if let Ok(mut child) = running.child.lock() {
        if let Some(child) = child.as_mut() {
            let _ = child.kill();
        }
    }
}

fn progress_percent(out_time_us: i64, seconds: f64) -> f64 {
    if seconds <= 0.0 { 0.0 } else { (out_time_us as f64 / 1e6 / seconds * 100.0).clamp(0.0, 99.0) }
}

/// One FFmpeg run with `encoder`; Err holds the last line FFmpeg logged.
fn encode(job: &ExportJob, encoder: &str, running: &Running) -> Result<(), String> {
    let partial = partial_of(&job.target);
    let log_path = job.work.join("ffmpeg.log");
    let log = fs::File::create(&log_path).map(Stdio::from).unwrap_or_else(|_| Stdio::null());
    let mut command = Command::new(&job.ffmpeg);
    command.args(ffmpeg_args(&job.input, &job.script, job.has_audio, encoder, job.bitrate, &job.fps, &partial))
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(log);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|_| fail("Không chạy được FFmpeg"))?;
    let stdout = child.stdout.take();
    if let Ok(mut slot) = running.child.lock() {
        *slot = Some(child);
    }
    if running.cancelled.load(Ordering::SeqCst) {
        stop(running);
    }
    let mut percent = 0.0;
    let mut written = Instant::now() - Duration::from_secs(1);
    let lines = stdout.map(|stdout| BufReader::new(stdout).lines());
    for line in lines.into_iter().flatten().map_while(Result::ok) {
        if let Some(value) = line.strip_prefix("out_time_us=") {
            if let Ok(us) = value.trim().parse::<i64>() {
                percent = progress_percent(us, job.seconds);
            }
        } else if line.starts_with("progress=") {
            // "Hủy" from another DubFlow window arrives as a file.
            if job.work.join("cancel").exists() {
                stop(running);
            }
            if written.elapsed() >= Duration::from_millis(400) {
                write_status(&job.work, "RUNNING", percent, &format!("Đang xuất video… {}%", percent.floor()), Some(&job.target));
                written = Instant::now();
            }
        }
    }
    let status = running.child.lock().ok().and_then(|mut slot| slot.take()).map(|mut child| child.wait());
    if matches!(status, Some(Ok(code)) if code.success()) && fs::metadata(&partial).is_ok_and(|metadata| metadata.len() > 0) {
        return Ok(());
    }
    let last = fs::read_to_string(&log_path).ok()
        .and_then(|text| text.lines().rev().find(|line| !line.trim().is_empty()).map(|line| line.trim().chars().take(240).collect::<String>()));
    Err(last.unwrap_or_default())
}

/// Render, then publish the video and subtitles under names that are free now.
fn run_export(job: &ExportJob, running: &Running) {
    let partial = partial_of(&job.target);
    let mut failure = String::new();
    let mut done = false;
    for (attempt, encoder) in ENCODERS.iter().enumerate() {
        if running.cancelled.load(Ordering::SeqCst) {
            break;
        }
        if attempt > 0 {
            write_status(&job.work, "RUNNING", 0.0, "Đang thử lại bằng bộ mã hóa khác…", Some(&job.target));
        }
        let _ = fs::remove_file(&partial);
        match encode(job, encoder, running) {
            Ok(()) => { done = true; break; }
            Err(error) => failure = error,
        }
    }
    if running.cancelled.load(Ordering::SeqCst) || job.work.join("cancel").exists() {
        let _ = fs::remove_file(&partial);
        write_status(&job.work, "CANCELLED", 0.0, "Đã hủy xuất video.", None);
        return;
    }
    if !done {
        let _ = fs::remove_file(&partial);
        let detail = if failure.is_empty() { String::new() } else { format!(" ({failure})") };
        write_status(&job.work, "FAILED", 0.0, &format!("Không xuất được video. Hãy thử lại hoặc chọn chất lượng khác.{detail}"), None);
        return;
    }
    // Someone may have created the name meanwhile: take the next free one.
    let target = if job.target.exists() || job.target.with_extension("srt").exists() {
        match unique_target(&job.folder, &job.name) { Ok(target) => target, Err(error) => {
            let _ = fs::remove_file(&partial);
            write_status(&job.work, "FAILED", 0.0, &error, None);
            return;
        } }
    } else {
        job.target.clone()
    };
    if fs::rename(&partial, &target).is_err() {
        let _ = fs::remove_file(&partial);
        write_status(&job.work, "FAILED", 0.0, "Không lưu được video đã xuất vào thư mục đã chọn.", None);
        return;
    }
    let srt = job.srt.as_ref().map(|text| (target.with_extension("srt"), text))
        .filter(|(path, text)| !path.exists() && write_atomic(path, text.as_bytes()).is_ok())
        .map(|(path, _)| display_path(&path));
    append_export(&job.work, json!({"path": display_path(&target), "srt_path": srt, "created": unix_now(),
        "duration": (job.seconds * 1000.0).round() / 1000.0, "quality": job.quality, "segments": job.segments}));
    write_status(&job.work, "COMPLETED", 100.0, "Đã xuất video.", Some(&target));
}

fn start_export(request: ExportRequest) -> Result<Value, String> {
    let dir = edit_dir(&request.output_path)?;
    let input = dir.join("final_vi.mp4");
    let facts = probe(&input)?;
    let segments = validate(&request, facts.duration)?;
    let folder = target_folder(&request.folder)?;
    let ffmpeg = media_tool("ffmpeg.exe")?;
    let name = base_name(&request.file_name);
    let mut running = RUNNING.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    if running.contains_key(&dir) {
        return Err(fail("Video này đang được xuất"));
    }
    let work = work_dir(&dir);
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))?;
    let lease = crate::work_lease::take(&work, BUSY)?;
    let cancel = work.join("cancel");
    if cancel.exists() {
        fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?;
    }
    let target = unique_target(&folder, &name)?;
    let script = work.join("filter.txt");
    fs::write(&script, filter_script(&segments, facts.has_audio, &request.quality)).map_err(|_| fail("Không ghi được bộ lọc xuất video"))?;
    let (width, height) = scaled_size(facts.width, facts.height, &request.quality);
    let job = ExportJob {
        work: work.clone(), input, script, folder, name, target: target.clone(),
        srt: (!request.cues.is_empty()).then(|| format_srt(&request.cues)),
        seconds: output_seconds(&segments), has_audio: facts.has_audio, bitrate: video_bitrate(width, height, &facts.fps),
        fps: facts.fps, ffmpeg, quality: request.quality, segments,
    };
    write_status(&work, "RUNNING", 0.0, "Đang bắt đầu xuất video…", Some(&target));
    let entry = Arc::new(Running { child: Mutex::new(None), cancelled: AtomicBool::new(false) });
    running.insert(dir.clone(), entry.clone());
    drop(running);
    std::thread::spawn(move || {
        run_export(&job, &entry);
        if let Ok(mut running) = RUNNING.lock() {
            running.remove(&dir);
        }
        drop(lease);
    });
    Ok(json!({"path": display_path(&target)}))
}

#[tauri::command]
pub async fn edit_export(request: ExportRequest) -> Result<Value, String> {
    start_export(request)
}

/// The current or last render, and the finished exports that still exist.
#[tauri::command]
pub fn edit_status(output_path: String) -> Result<Value, String> {
    let dir = edit_dir(&output_path)?;
    let work = work_dir(&dir);
    let mut status = fs::read(work.join("status.json")).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).filter(Value::is_object);
    let ours = RUNNING.lock().is_ok_and(|running| running.contains_key(&dir));
    if let Some(value) = status.as_mut().filter(|value| value["state"] == "RUNNING" && !ours) {
        // A free lease means no window is rendering: the app closed mid-export.
        if let Ok(lease) = crate::work_lease::take(&work, BUSY) {
            if let Some(target) = value["output_path"].as_str() {
                let _ = fs::remove_file(partial_of(Path::new(target)));
            }
            *value = json!({"schema_version": 1, "state": "FAILED", "percent": 0, "message": "Lần xuất trước bị gián đoạn. Hãy xuất lại.", "output_path": null});
            write_status(&work, "FAILED", 0.0, "Lần xuất trước bị gián đoạn. Hãy xuất lại.", None);
            drop(lease);
        }
    }
    let exports: Vec<Value> = read_exports(&work).into_iter()
        .filter(|entry| entry["path"].as_str().is_some_and(|path| Path::new(path).is_file())).collect();
    Ok(json!({"status": status, "exports": exports}))
}

#[tauri::command]
pub fn edit_cancel(output_path: String) -> Result<(), String> {
    let dir = edit_dir(&output_path)?;
    let work = work_dir(&dir);
    if work.is_dir() {
        fs::write(work.join("cancel"), b"").map_err(|_| fail("Không hủy được xuất video"))?;
    }
    if let Some(entry) = RUNNING.lock().ok().and_then(|running| running.get(&dir).cloned()) {
        stop(&entry);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment(start: f64, end: f64, speed: f64, volume: f64) -> Segment {
        Segment { start, end, speed, volume }
    }

    fn request(segments: Vec<Segment>) -> ExportRequest {
        ExportRequest { output_path: "unused".into(), segments, quality: "source".into(), folder: "unused".into(),
            file_name: "Phim (sửa 1).mp4".into(), cues: vec![Cue { start_ms: 0, end_ms: 1000, text: "Xin chào".into() }] }
    }

    #[test]
    fn validates_segments_quality_name_and_cues() {
        let ok = request(vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.04, 1.5, 0.5)]);
        let segments = validate(&ok, 30.0).unwrap();
        assert_eq!(segments[1].end, 30.0, "a hair past the end is clamped");
        assert!(validate(&request((0..500).map(|i| segment(f64::from(i) * 0.1, f64::from(i) * 0.1 + 0.1, 1.0, 1.0)).collect()), 60.0).is_ok());
        let bad: Vec<(&str, ExportRequest)> = vec![
            ("none", request(vec![])),
            ("too many", request((0..501).map(|_| segment(0.0, 1.0, 1.0, 1.0)).collect())),
            ("negative", request(vec![segment(-0.5, 10.0, 1.0, 1.0)])),
            ("past end", request(vec![segment(0.0, 30.2, 1.0, 1.0)])),
            ("short", request(vec![segment(5.0, 5.05, 1.0, 1.0)])),
            ("backward", request(vec![segment(8.0, 5.0, 1.0, 1.0)])),
            ("slow", request(vec![segment(0.0, 10.0, 0.4, 1.0)])),
            ("fast", request(vec![segment(0.0, 10.0, 2.1, 1.0)])),
            ("loud", request(vec![segment(0.0, 10.0, 1.0, 2.01)])),
            ("muted below zero", request(vec![segment(0.0, 10.0, 1.0, -0.1)])),
            ("nan", request(vec![segment(0.0, f64::NAN, 1.0, 1.0)])),
        ];
        for (label, value) in bad {
            assert!(validate(&value, 30.0).is_err(), "{label}");
        }
        let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        value.quality = "4k".into();
        assert!(validate(&value, 30.0).is_err());
        for name in ["", "   ", ".mp4", " .MP4 "] {
            let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
            value.file_name = name.into();
            assert!(validate(&value, 30.0).is_err(), "{name:?}");
        }
        for cue in [Cue { start_ms: 500, end_ms: 500, text: "x".into() }, Cue { start_ms: 0, end_ms: 11_001, text: "x".into() },
            Cue { start_ms: 0, end_ms: 100, text: "x\u{0}".into() }, Cue { start_ms: 0, end_ms: 100, text: "đ".repeat(2001) }] {
            let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
            value.cues = vec![cue];
            assert!(validate(&value, 30.0).is_err());
        }
        let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        value.cues = vec![Cue { start_ms: 9000, end_ms: 11_000, text: "dòng 1\ndòng 2".into() }];
        assert!(validate(&value, 30.0).is_ok(), "a last cue may run up to a second past the end");
        assert!(serde_json::from_value::<ExportRequest>(json!({"outputPath": "x", "segments": [], "quality": "source",
            "folder": "x", "fileName": "x", "cues": [], "overwrite": true})).is_err(), "no overwrite option");
        assert!(serde_json::from_value::<Segment>(json!({"start": 0, "end": 1, "speed": 1, "volume": 1, "id": "s1"})).is_err());
    }

    #[test]
    fn writes_the_filter_script_for_a_two_segment_edit() {
        let segments = [segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.5, 1.5, 0.5)];
        assert_eq!(filter_script(&segments, true, "source"), "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[0:v]trim=start=20:end=30.5,setpts=(PTS-STARTPTS)/1.5[v1];
[0:a]atrim=start=20:end=30.5,asetpts=PTS-STARTPTS,atempo=1.5,volume=0.5[a1];
[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]");
        assert_eq!(filter_script(&segments, true, "720p"), "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[0:v]trim=start=20:end=30.5,setpts=(PTS-STARTPTS)/1.5[v1];
[0:a]atrim=start=20:end=30.5,asetpts=PTS-STARTPTS,atempo=1.5,volume=0.5[a1];
[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][a];
[vc]scale=w='if(gte(iw,ih),-2,720)':h='if(gte(iw,ih),720,-2)',setsar=1[v]");
        assert_eq!(filter_script(&[segment(1.234_56, 2.0, 0.75, 0.0)], false, "1080p"), "\
[0:v]trim=start=1.235:end=2,setpts=(PTS-STARTPTS)/0.75[v0];
[v0]concat=n=1:v=1:a=0[vc];
[vc]scale=w='if(gte(iw,ih),-2,1080)':h='if(gte(iw,ih),1080,-2)',setsar=1[v]");
    }

    #[test]
    fn chains_atempo_within_its_range() {
        assert!(atempo_chain(1.0).is_empty());
        assert_eq!(atempo_chain(1.5), ["atempo=1.5"]);
        assert_eq!(atempo_chain(0.5), ["atempo=0.5"]);
        assert_eq!(atempo_chain(2.0), ["atempo=2"]);
        assert_eq!(atempo_chain(4.0), ["atempo=2", "atempo=2"]);
        assert_eq!(atempo_chain(5.0), ["atempo=2", "atempo=2", "atempo=1.25"]);
        assert_eq!(atempo_chain(0.3), ["atempo=0.5", "atempo=0.6"]);
        assert_eq!(atempo_chain(0.2), ["atempo=0.5", "atempo=0.5", "atempo=0.8"]);
        assert_eq!(num(0.0), "0");
        assert_eq!(num(12.3456), "12.346");
        assert_eq!(num(-0.0001), "0");
    }

    #[test]
    fn sizes_bitrate_arguments_and_progress() {
        assert_eq!(scaled_size(1024, 576, "source"), (1024, 576));
        assert_eq!(scaled_size(1024, 576, "1080p"), (1920, 1080));
        assert_eq!(scaled_size(1080, 1920, "720p"), (720, 1280));
        assert_eq!(scaled_size(853, 480, "720p"), (1280, 720));
        assert_eq!(video_bitrate(1920, 1080, "25/1"), 6_220_800);
        assert_eq!(video_bitrate(320, 240, "30/1"), 1_500_000);
        assert_eq!(video_bitrate(3840, 2160, "60/1"), 16_000_000);
        let args: Vec<String> = ffmpeg_args(Path::new("in.mp4"), Path::new("f.txt"), true, "h264_mf", 3_000_000, "25/1", Path::new("o.mp4.partial"))
            .into_iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args.join(" "), "-nostdin -hide_banner -v error -y -i in.mp4 -/filter_complex f.txt -map [v] -map [a] -c:v h264_mf -b:v 3000000 \
-pix_fmt yuv420p -r 25/1 -c:a aac -b:a 192k -sn -dn -map_chapters -1 -movflags +faststart -progress pipe:1 -nostats -f mp4 o.mp4.partial");
        let silent: Vec<String> = ffmpeg_args(Path::new("in.mp4"), Path::new("f.txt"), false, "libopenh264", 2_000_000, "30/1", Path::new("o"))
            .into_iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert!(silent.join(" ").contains("-map [v] -c:v libopenh264 -b:v 2000000 -maxrate 3000000 -bufsize 4000000 -pix_fmt"));
        assert!(!silent.iter().any(|arg| arg == "[a]" || arg == "aac"));
        assert_eq!(progress_percent(5_000_000, 10.0), 50.0);
        assert_eq!(progress_percent(50_000_000, 10.0), 99.0);
        assert_eq!(progress_percent(-1, 10.0), 0.0);
        assert_eq!(output_seconds(&[segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.5, 1.5, 0.5)]), 17.0);
    }

    #[test]
    fn reads_ffprobe_output() {
        let probe = parse_probe(r#"{"streams":[{"codec_type":"video","width":1024,"height":576,"avg_frame_rate":"25/1","r_frame_rate":"25/1"},
            {"codec_type":"audio","r_frame_rate":"0/0"}],"format":{"duration":"154.200000","size":"48000000"}}"#).unwrap();
        assert_eq!(probe, Probe { duration: 154.2, width: 1024, height: 576, fps: "25/1".into(), has_audio: true, size: 48_000_000 });
        let silent = parse_probe(r#"{"streams":[{"codec_type":"video","width":720,"height":1280,"avg_frame_rate":"0/0","r_frame_rate":"30000/1001"}],
            "format":{"duration":"10"}}"#).unwrap();
        assert_eq!((silent.fps.as_str(), silent.has_audio, silent.size), ("30000/1001", false, 0));
        assert_eq!(parse_probe(r#"{"streams":[{"codec_type":"video","width":2,"height":2,"r_frame_rate":"9000/1"}],"format":{"duration":"1"}}"#).unwrap().fps, "30/1");
        assert!(parse_probe(r#"{"streams":[{"codec_type":"audio"}],"format":{"duration":"10"}}"#).is_none());
        assert!(parse_probe(r#"{"streams":[{"codec_type":"video","width":2,"height":2}],"format":{"duration":"N/A"}}"#).is_none());
        assert!(parse_probe("not json").is_none());
    }

    #[test]
    fn names_are_sanitized_and_never_overwrite() {
        assert_eq!(base_name(" Phim hay (sửa 1).MP4 "), "Phim hay (sửa 1)");
        assert_eq!(base_name("a:b*c?.mp4"), "a b c");
        assert_eq!(base_name("CON"), "CON_");
        assert_eq!(base_name("đ.mp4"), "đ");
        assert_eq!(base_name("  "), "");
        let folder = std::env::temp_dir().join(format!("dubflow-edit-names-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        assert_eq!(unique_target(&folder, "Phim").unwrap(), folder.join("Phim.mp4"));
        fs::write(folder.join("Phim.mp4"), b"x").unwrap();
        assert_eq!(unique_target(&folder, "Phim").unwrap(), folder.join("Phim (2).mp4"));
        fs::write(folder.join("Phim (2).srt"), b"x").unwrap();
        fs::write(folder.join("Phim (3).mp4.partial"), b"x").unwrap();
        assert_eq!(unique_target(&folder, "Phim").unwrap(), folder.join("Phim (4).mp4"));
        assert_eq!(partial_of(&folder.join("Phim (4).mp4")), folder.join("Phim (4).mp4.partial"));
        assert!(target_folder("relative").is_err());
        assert!(target_folder(&folder.join("missing").to_string_lossy()).is_err());
        assert!(target_folder(&folder.join("Phim.mp4").to_string_lossy()).is_err());
        assert!(target_folder(&folder.to_string_lossy()).is_ok());
        fs::remove_dir_all(folder).unwrap();
    }

    #[test]
    fn formats_and_parses_subtitles() {
        let cues = vec![
            Cue { start_ms: 1_000, end_ms: 3_000, text: "Xin chào".into() },
            Cue { start_ms: 3_725_250, end_ms: 3_726_000, text: "dòng một\r\ndòng hai".into() },
        ];
        let text = format_srt(&cues);
        assert_eq!(text, "1\n00:00:01,000 --> 00:00:03,000\nXin chào\n\n2\n01:02:05,250 --> 01:02:06,000\ndòng một\ndòng hai\n\n");
        let parsed = parse_srt(&format!("\u{feff}{}", text.replace('\n', "\r\n")));
        assert_eq!(parsed[0], cues[0]);
        assert_eq!(parsed[1].text, "dòng một\ndòng hai");
        assert_eq!(parsed[1].start_ms, 3_725_250);
        let messy = "1\n00:00:01.5 --> 00:00:02,000 X1:0\nmột\n\n\n2\nbroken --> line\nhai\n\n3\n00:00:05,000 --> 00:00:04,000\nngược\n\n4\n00:01:70,000 --> 00:02:00,000\nsai\n\n5\n00:00:06,000 --> 00:00:07,000\n\n";
        assert_eq!(parse_srt(messy), vec![Cue { start_ms: 1_500, end_ms: 2_000, text: "một".into() }]);
        assert!(parse_srt("").is_empty());
    }

    #[test]
    fn saves_drafts_and_lists_exports_for_finished_videos_only() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-host-{}", std::process::id()));
        let dir = base.join("DubFlow Output/clip - vi/run-1");
        fs::create_dir_all(&dir).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        let path = crate::display_path(&video);
        let project = json!({"schema_version": 1, "duration": 10.0, "segments": [{"id": "s1", "start": 0, "end": 10, "speed": 1, "volume": 1}]});
        edit_project_save(path.clone(), project.clone()).unwrap();
        let saved: Value = serde_json::from_slice(&fs::read(dir.join(".dubflow-work/edit/project.json")).unwrap()).unwrap();
        assert_eq!(saved, project);
        assert!(edit_project_save(path.clone(), json!({"schema_version": 2, "segments": [{}]})).is_err());
        assert!(edit_project_save(path.clone(), json!({"schema_version": 1, "segments": []})).is_err());
        assert!(edit_project_save(path.clone(), json!({"schema_version": 1, "segments": [{}], "pad": "x".repeat(PROJECT_LIMIT)})).is_err());
        fs::write(dir.join("other.mp4"), b"video").unwrap();
        assert!(edit_project_save(crate::display_path(&dir.join("other.mp4")), project.clone()).is_err(), "only final_vi.mp4");
        assert!(edit_project_save("relative/final_vi.mp4".into(), project.clone()).is_err());
        let outside = base.join("elsewhere/final_vi.mp4");
        fs::create_dir_all(outside.parent().unwrap()).unwrap();
        fs::write(&outside, b"video").unwrap();
        assert!(edit_project_save(crate::display_path(&outside), project).is_err(), "only DubFlow results");

        let work = dir.join(".dubflow-work/edit");
        let exported = base.join("Phim (sửa 1).mp4");
        fs::write(&exported, b"video").unwrap();
        append_export(&work, json!({"path": crate::display_path(&exported), "duration": 5.0}));
        append_export(&work, json!({"path": crate::display_path(&base.join("deleted.mp4")), "duration": 5.0}));
        // A render left RUNNING by a closed app is reported as interrupted.
        write_status(&work, "RUNNING", 40.0, "Đang xuất video… 40%", Some(&base.join("Phim (sửa 2).mp4")));
        fs::write(base.join("Phim (sửa 2).mp4.partial"), b"half").unwrap();
        let status = edit_status(path.clone()).unwrap();
        assert_eq!(status["exports"].as_array().unwrap().len(), 1);
        assert_eq!(status["status"]["state"], "FAILED");
        assert!(!base.join("Phim (sửa 2).mp4.partial").exists());
        // While another holder has the lease (Windows file sharing), RUNNING stays.
        if cfg!(windows) {
            write_status(&work, "RUNNING", 10.0, "x", None);
            let lease = crate::work_lease::take(&work, BUSY).unwrap();
            assert_eq!(edit_status(path.clone()).unwrap()["status"]["state"], "RUNNING");
            drop(lease);
        }
        edit_cancel(path).unwrap();
        assert!(work.join("cancel").is_file());
        fs::remove_dir_all(base).unwrap();
    }
}
