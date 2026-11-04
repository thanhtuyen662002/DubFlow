//! Cắt ghép (Issue #408): the edited timeline of a finished `final_vi.mp4`
//! rendered into a new MP4 (never over an existing file), with its subtitles
//! remapped by the front end and written beside it. The edit itself is a
//! draft in `<output>\.dubflow-work\edit\project.json`; renders report in
//! `status.json` there and finished ones are listed in `exports.json`.
//! Chèn (Issue #410) adds other videos as segments, transitions at the
//! joints and overlays; every input is fitted to the main video's frame.
//! Âm thanh (Issue #420) mixes added sounds (the sound-effect pack or the
//! user's audio files) over the output's own sound, which has one volume
//! and may dip under a sound ("ducking"), then limits the sum. Chữ (Issue
//! #422) draws texts over the picture with libass as the last filter.
//! Issue #426 opens Bán hàng videos (`video-<n>.mp4` of a product run, one
//! draft per video in `.dubflow-work\edit-<n>`) and compilations
//! (`compilation.mp4`) in the same editor.
//! Issue #497 opens any video on the computer too (the Chỉnh sửa screen):
//! its draft and renders live in `<data root>\\edit-files\\<hash>`, never
//! beside the user's file, and `edit_library` lists the finished videos.
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
/// Media in one export, the main video included.
const MAX_MEDIA: usize = 20;
const MAX_OVERLAYS: usize = 20;
const MIN_SEGMENT: f64 = 0.1;
const MIN_TRANSITION: f64 = 0.2;
const MAX_TRANSITION: f64 = 2.0;
/// Added sounds in one export, and their limits (same as the editor's).
const MAX_SOUNDS: usize = 100;
const MIN_SOUND: f64 = 0.02;
const MAX_FADE: f64 = 3.0;
const MAX_DUCK_DB: f64 = 24.0;
/// Ducking eases in before a sound and out after it over this many seconds.
const DUCK_RAMP: f64 = 0.1;
/// The final limiter keeps peaks under -1 dBFS.
const LIMITER: &str = "alimiter=limit=0.891:level=0:latency=1";
/// Every part's audio before joining: 48 kHz stereo float.
const AUDIO_FORMAT: &str = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo";
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

/// One part of the output: `[start, end)` of media `media` (0 = the main video).
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Segment {
    #[serde(default)]
    media: usize,
    start: f64,
    end: f64,
    speed: f64,
    volume: f64,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum TransitionKind {
    /// Fade through black: half out, half in; the length stays.
    Fade,
    /// Cross-dissolve: the neighbours overlap by the duration.
    Dissolve,
}

/// The joint after segment `after`.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Transition {
    after: usize,
    #[serde(rename = "type")]
    kind: TransitionKind,
    duration: f64,
}

/// Media `media` from `start` to `end` (its seconds) shown from output time
/// `at`, at x/y/w fractions of the frame (height follows its aspect).
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Overlay {
    media: usize,
    at: f64,
    start: f64,
    end: f64,
    x: f64,
    y: f64,
    w: f64,
    volume: f64,
}

/// Where an added sound comes from: a sound effect of the pack, or a file.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum SoundSource {
    Sfx { id: String },
    File { path: String },
}

/// An added sound as the editor sends it: `[start, end)` of the sound played
/// from output time `at`, volume 0–2, fades in seconds, and the original
/// sound lowered by `duck_db` while it plays (0 = not).
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SoundClip {
    source: SoundSource,
    at: f64,
    start: f64,
    end: f64,
    volume: f64,
    fade_in: f64,
    fade_out: f64,
    duck_db: f64,
}

/// A validated sound: `input` indexes the audio files, which follow the video inputs.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Sound {
    input: usize,
    at: f64,
    start: f64,
    end: f64,
    volume: f64,
    fade_in: f64,
    fade_out: f64,
    duck_db: f64,
}

/// The added sounds and the volume of the output's own sound.
#[derive(Debug, Clone, PartialEq)]
struct Mix {
    sounds: Vec<Sound>,
    main_volume: f64,
}

fn full_volume() -> f64 {
    1.0
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
    /// Other video files the edit uses (schema 2); media index i + 1.
    #[serde(default)]
    media: Vec<String>,
    segments: Vec<Segment>,
    #[serde(default)]
    transitions: Vec<Transition>,
    #[serde(default)]
    overlays: Vec<Overlay>,
    /// Added sounds (schema 3).
    #[serde(default)]
    audio: Vec<SoundClip>,
    /// "Âm thanh gốc", 0–2.
    #[serde(default = "full_volume")]
    main_volume: f64,
    /// Texts over the picture (schema 4).
    #[serde(default)]
    texts: Vec<TextClip>,
    quality: String,
    folder: String,
    file_name: String,
    /// Subtitles already remapped to the edited timeline by the front end.
    cues: Vec<Cue>,
}

/// A validated edit, clamped to its media.
#[derive(Debug, Clone, PartialEq)]
struct Edit {
    segments: Vec<Segment>,
    transitions: Vec<Transition>,
    overlays: Vec<Overlay>,
}

/// What the filter graph needs to know about each input.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Input {
    has_audio: bool,
}

/// The main video's frame: every other input is fitted into it.
#[derive(Debug, Clone, PartialEq)]
struct Frame {
    width: u32,
    height: u32,
    fps: String,
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

/// What kind of finished video an edit is of.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    /// A dubbing job's `final_vi.mp4`.
    Job,
    /// Video `n` of a Bán hàng run.
    Product(usize),
    /// A Tổng hợp run's `compilation.mp4`.
    Compilation,
    /// A Phim tài liệu run's rough cut, `documentary.mp4` (ADR-0049).
    Docu,
    /// Any other video on the computer (Issue #497).
    File,
}

/// A finished video below "DubFlow Output" that the editor may open: the
/// video, its folder, where its draft and renders live and its subtitles.
#[derive(Debug, Clone, PartialEq)]
struct Target {
    kind: Kind,
    dir: PathBuf,
    main: PathBuf,
    work: PathBuf,
    captions: Option<PathBuf>,
}

impl Target {
    fn kind_name(&self) -> &'static str {
        match self.kind { Kind::Job => "job", Kind::Product(_) => "product", Kind::Compilation => "compilation", Kind::Docu => "docu", Kind::File => "file" }
    }
}

/// A finished job's `final_vi.mp4`, a product video, a compilation or a
/// documentary rough cut below "DubFlow Output"; else any video file.
fn edit_target(output_path: &str) -> Result<Target, String> {
    match output_file(output_path).ok().and_then(|video| dubflow_target(&video)) {
        Some(target) => Ok(target),
        None => file_target(output_path, &crate::data_root()?.join("edit-files")),
    }
}

/// A video DubFlow made, by its name and folder.
fn dubflow_target(video: &Path) -> Option<Target> {
    let video = video.to_path_buf();
    let dir = video.parent()?.to_path_buf();
    let name = video.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    let (kind, work, captions) = if name == "final_vi.mp4" && is_job_output(&dir) {
        (Kind::Job, work_dir(&dir), Some(dir.join("captions_vi.srt")))
    } else if let Some((_, number)) = crate::product::product_video(&video) {
        (Kind::Product(number), dir.join(".dubflow-work").join(format!("edit-{number}")), Some(dir.join(format!("captions-{number}.srt"))))
    } else if name == "compilation.mp4" && is_compilation_run(&dir) {
        (Kind::Compilation, work_dir(&dir), None)
    } else if name == "documentary.mp4" && crate::docu::is_run(&dir) {
        // Its English captions are the Phụ đề track.
        (Kind::Docu, work_dir(&dir), Some(dir.join("captions_en.srt")))
    } else {
        return None;
    };
    Some(Target { kind, main: dir.join(name), dir, work, captions })
}

/// A dubbing job's results folder: the worker published its manifest (with
/// the source) beside `final_vi.mp4`. A folder that only happens to be named
/// like one is a user's folder: its videos open as files (Issue #497).
fn is_job_output(dir: &Path) -> bool {
    crate::read_json(&dir.join("job_manifest.json"), 16 * 1024 * 1024).is_ok_and(|manifest| manifest["source"].is_object())
}

/// Any playable video: its draft is kept below `store`, one folder per file.
fn file_target(path: &str, store: &Path) -> Result<Target, String> {
    let main = crate::playable_media(path)?;
    let dir = main.parent().ok_or_else(|| fail("Không tìm thấy thư mục của video"))?.to_path_buf();
    Ok(Target { kind: Kind::File, work: store.join(file_key(&main)), main, dir, captions: None })
}

/// The same file always has the same draft (Windows paths ignore case).
fn file_key(video: &Path) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(display_path(video).to_lowercase().as_bytes());
    digest.iter().take(10).map(|byte| format!("{byte:02x}")).collect()
}

/// `<…>\DubFlow Output\Tong hop - <title>\run-<id>` that kept its request.
fn is_compilation_run(dir: &Path) -> bool {
    let named = |dir: Option<&Path>, check: &dyn Fn(&str) -> bool| dir.and_then(Path::file_name).and_then(|name| name.to_str()).is_some_and(check);
    named(Some(dir), &|name| name.starts_with("run-")) && named(dir.parent(), &|name| name.starts_with("Tong hop - "))
        && named(dir.parent().and_then(Path::parent), &|name| name == "DubFlow Output")
        && dir.join(".dubflow-work").join("spec.json").is_file()
}

fn work_dir(dir: &Path) -> PathBuf {
    dir.join(".dubflow-work").join("edit")
}

pub(crate) fn media_tool(name: &str) -> Result<PathBuf, String> {
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

fn segment_length(segment: &Segment) -> f64 {
    (segment.end - segment.start) / segment.speed
}

/// Seconds the edited video lasts: each dissolve overlaps its neighbours.
fn output_seconds(segments: &[Segment], transitions: &[Transition]) -> f64 {
    let overlap: f64 = transitions.iter().filter(|joint| joint.kind == TransitionKind::Dissolve).map(|joint| joint.duration).sum();
    segments.iter().map(segment_length).sum::<f64>() - overlap
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

/// The edit clamped to its media (`durations[0]` is the main video's), or why
/// the request cannot be rendered.
fn validate(request: &ExportRequest, durations: &[f64]) -> Result<Edit, String> {
    if !(1..=MAX_SEGMENTS).contains(&request.segments.len()) {
        return Err(fail("Bản chỉnh sửa cần từ 1 đến 500 đoạn"));
    }
    if request.media.len() + 1 > MAX_MEDIA || durations.len() != request.media.len() + 1 {
        return Err(fail("Một bản sửa dùng tối đa 20 video"));
    }
    let mut segments = Vec::with_capacity(request.segments.len());
    for segment in &request.segments {
        if ![segment.start, segment.end, segment.speed, segment.volume].iter().all(|value| value.is_finite()) {
            return Err(fail("Bản chỉnh sửa có số không hợp lệ"));
        }
        let Some(&duration) = durations.get(segment.media) else {
            return Err(fail("Bản chỉnh sửa dùng video không có trong danh sách"));
        };
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
    let mut previous: Option<usize> = None;
    for joint in &request.transitions {
        let fits = |index: usize| segments.get(index).is_some_and(|segment| joint.duration <= segment_length(segment) / 2.0 + 1e-3);
        if previous.is_some_and(|after| joint.after <= after) || joint.after + 1 >= segments.len() || !joint.duration.is_finite()
            || !(MIN_TRANSITION - 1e-6..=MAX_TRANSITION + 1e-6).contains(&joint.duration) || !fits(joint.after) || !fits(joint.after + 1) {
            return Err(fail("Chuyển cảnh phải dài 0,2–2 giây và không quá nửa mỗi đoạn kề bên"));
        }
        previous = Some(joint.after);
    }
    let transitions = request.transitions.clone();
    let total = output_seconds(&segments, &transitions);
    if request.overlays.len() > MAX_OVERLAYS {
        return Err(fail("Tối đa 20 video chồng"));
    }
    let mut overlays = Vec::with_capacity(request.overlays.len());
    for overlay in &request.overlays {
        let numbers = [overlay.at, overlay.start, overlay.end, overlay.x, overlay.y, overlay.w, overlay.volume];
        let Some(&duration) = durations.get(overlay.media).filter(|_| numbers.iter().all(|value| value.is_finite())) else {
            return Err(fail("Video chồng không hợp lệ"));
        };
        let end = overlay.end.min(duration);
        if overlay.at < 0.0 || overlay.at >= total || overlay.start < 0.0 || overlay.end > duration + 0.05 || end - overlay.start < MIN_SEGMENT - 1e-6
            || !(0.0..=1.0).contains(&overlay.x) || !(0.0..=1.0).contains(&overlay.y) || !(0.01..=1.0).contains(&overlay.w)
            || !(0.0..=2.0).contains(&overlay.volume) {
            return Err(fail("Video chồng phải nằm trong khung hình và trong thời lượng video"));
        }
        overlays.push(Overlay { end, ..*overlay });
    }
    if !QUALITIES.contains(&request.quality.as_str()) {
        return Err(fail("Chất lượng xuất không hợp lệ"));
    }
    if base_name(&request.file_name).is_empty() {
        return Err(fail("Hãy đặt tên cho file video"));
    }
    let limit_ms = (total * 1000.0).ceil() as u64 + 1000;
    if request.cues.len() > MAX_CUES {
        return Err(fail("Quá nhiều câu phụ đề"));
    }
    for cue in &request.cues {
        if cue.end_ms <= cue.start_ms || cue.end_ms > limit_ms || cue.text.chars().count() > MAX_CUE_TEXT
            || cue.text.chars().any(|c| c.is_control() && c != '\n' && c != '\r' && c != '\t') {
            return Err(fail("Phụ đề của bản chỉnh sửa không hợp lệ"));
        }
    }
    Ok(Edit { segments, transitions, overlays })
}

/// The added sounds, checked against their files' lengths (`files[i]` is
/// clip i's audio file, `durations` those files' seconds), clamped to them.
fn validate_mix(clips: &[SoundClip], files: &[usize], durations: &[f64], main_volume: f64, total: f64) -> Result<Mix, String> {
    if clips.len() > MAX_SOUNDS || files.len() != clips.len() {
        return Err(fail("Tối đa 100 âm thanh thêm"));
    }
    if !main_volume.is_finite() || !(0.0..=2.0).contains(&main_volume) {
        return Err(fail("Âm lượng gốc phải từ 0 đến 200%"));
    }
    let mut sounds = Vec::with_capacity(clips.len());
    for (clip, &input) in clips.iter().zip(files) {
        let numbers = [clip.at, clip.start, clip.end, clip.volume, clip.fade_in, clip.fade_out, clip.duck_db];
        let Some(&duration) = durations.get(input).filter(|_| numbers.iter().all(|value| value.is_finite())) else {
            return Err(fail("Âm thanh thêm không hợp lệ"));
        };
        let end = clip.end.min(duration);
        if clip.at < 0.0 || clip.at >= total || clip.start < 0.0 || clip.end > duration + 0.05 || end - clip.start < MIN_SOUND - 1e-6
            || !(0.0..=2.0).contains(&clip.volume) || !(0.0..=MAX_FADE).contains(&clip.fade_in) || !(0.0..=MAX_FADE).contains(&clip.fade_out)
            || !(0.0..=MAX_DUCK_DB).contains(&clip.duck_db) {
            return Err(fail("Âm thanh thêm phải nằm trong video, âm lượng 0–200%, hiện và tắt dần tối đa 3 giây"));
        }
        sounds.push(Sound { input, at: clip.at, start: clip.start, end, volume: clip.volume, fade_in: clip.fade_in, fade_out: clip.fade_out,
            duck_db: clip.duck_db });
    }
    Ok(Mix { sounds, main_volume })
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

/// The 4a graph, with the sound ending as `[a]`.
#[cfg(test)]
fn filter_script(segments: &[Segment], has_audio: bool, quality: &str) -> String {
    filter_graph(segments, has_audio, quality, "v", "a")
}

/// The filter graph: each segment trimmed and retimed (video and audio), then
/// concatenated, the picture ending as `[video_out]` and the sound as
/// `[audio_out]`; "1080p"/"720p" scale the short side, keeping aspect and
/// even sizes.
fn filter_graph(segments: &[Segment], has_audio: bool, quality: &str, video_out: &str, audio_out: &str) -> String {
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
    let short = short_side(quality);
    let joined = if short.is_some() { "vc" } else { video_out };
    lines.push(format!("{pads}concat=n={}:v=1:a={}[{joined}]{}", segments.len(), u8::from(has_audio), if has_audio { format!("[{audio_out}]") } else { String::new() }));
    if let Some(short) = short {
        lines.push(quality_scale("vc", short, video_out));
    }
    lines.join(";\n")
}

fn short_side(quality: &str) -> Option<u32> {
    match quality { "1080p" => Some(1080), "720p" => Some(720), _ => None }
}

fn quality_scale(input: &str, short: u32, out: &str) -> String {
    format!("[{input}]scale=w='if(gte(iw,ih),-2,{short})':h='if(gte(iw,ih),{short},-2)',setsar=1[{out}]")
}

fn even(value: f64) -> u32 {
    ((value / 2.0).round() as u32 * 2).max(2)
}

/// Whether the export has a sound track: some part it plays, or an audible overlay, has audio.
fn output_has_audio(edit: &Edit, inputs: &[Input]) -> bool {
    edit.segments.iter().any(|segment| inputs[segment.media].has_audio)
        || edit.overlays.iter().any(|overlay| inputs[overlay.media].has_audio && overlay.volume > 0.0)
}

/// The filter graph for an edit, and whether it has audio. A main-video-only
/// cut keeps the 4a graph; anything else fits every segment into the main
/// frame (fit + pad, main frame rate, yuv420p, 48 kHz stereo; silence for
/// media without sound), joins them with concat, fades through black with
/// `fade`/`afade` on both sides of the joint (the length stays), dissolves
/// with `xfade` + `acrossfade` (the neighbours overlap), then lays overlays
/// on top and mixes their sound in.
fn render_script(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str) -> (String, bool) {
    video_graph(edit, inputs, frame, quality, "v", "a")
}

/// `render_script` with the picture ending as `[video_out]` and the sound as `[audio_out]`.
fn video_graph(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str, video_out: &str, audio_out: &str) -> (String, bool) {
    if inputs.len() == 1 && edit.transitions.is_empty() && edit.overlays.is_empty() {
        return (filter_graph(&edit.segments, inputs[0].has_audio, quality, video_out, audio_out), inputs[0].has_audio);
    }
    let has_audio = output_has_audio(edit, inputs);
    let segments = &edit.segments;
    let count = segments.len();
    let lengths: Vec<f64> = segments.iter().map(segment_length).collect();
    let mut fade_in = vec![0.0; count];
    let mut fade_out = vec![0.0; count];
    let mut dissolve: Vec<Option<f64>> = vec![None; count];
    for joint in &edit.transitions {
        match joint.kind {
            TransitionKind::Fade => {
                fade_out[joint.after] = joint.duration / 2.0;
                fade_in[joint.after + 1] = joint.duration / 2.0;
            }
            TransitionKind::Dissolve => dissolve[joint.after] = Some(joint.duration),
        }
    }
    let (width, height, fps) = (frame.width, frame.height, &frame.fps);
    let fit = format!("scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p,settb=AVTB");
    let mut lines = Vec::new();
    for (index, segment) in segments.iter().enumerate() {
        let (start, end) = (num(segment.start), num(segment.end));
        let pts = if segment.speed == 1.0 { "PTS-STARTPTS".to_owned() } else { format!("(PTS-STARTPTS)/{}", num(segment.speed)) };
        let mut video = vec![format!("trim=start={start}:end={end}"), format!("setpts={pts}"), fit.clone()];
        let mut fades = Vec::new();
        if fade_in[index] > 0.0 {
            fades.push(format!("t=in:st=0:d={}", num(fade_in[index])));
        }
        if fade_out[index] > 0.0 {
            fades.push(format!("t=out:st={}:d={}", num(lengths[index] - fade_out[index]), num(fade_out[index])));
        }
        video.extend(fades.iter().map(|fade| format!("fade={fade}")));
        lines.push(format!("[{}:v]{}[v{index}]", segment.media, video.join(",")));
        if has_audio {
            let (source, mut audio) = if inputs[segment.media].has_audio {
                let mut audio = vec![format!("atrim=start={start}:end={end}"), "asetpts=PTS-STARTPTS".to_owned()];
                audio.extend(atempo_chain(segment.speed));
                if segment.volume != 1.0 {
                    audio.push(format!("volume={}", num(segment.volume)));
                }
                (format!("[{}:a]", segment.media), audio)
            } else {
                (String::new(), vec!["anullsrc=r=48000:cl=stereo".to_owned(), format!("atrim=duration={}", num(lengths[index]))])
            };
            audio.push(AUDIO_FORMAT.to_owned());
            audio.extend(fades.iter().map(|fade| format!("afade={fade}")));
            lines.push(format!("{source}{}[a{index}]", audio.join(",")));
        }
    }
    // Runs of segments between dissolves are concatenated; the runs then cross-fade.
    let mut runs: Vec<(usize, usize)> = Vec::new();
    let mut first = 0;
    for index in 0..count {
        if index + 1 == count || dissolve[index].is_some() {
            runs.push((first, index));
            first = index + 1;
        }
    }
    let short = short_side(quality);
    let ov_audio: Vec<usize> = (0..edit.overlays.len())
        .filter(|&index| inputs[edit.overlays[index].media].has_audio && edit.overlays[index].volume > 0.0).collect();
    let joined_video = if edit.overlays.is_empty() && short.is_none() { video_out } else { "vj" };
    let joined_audio = if ov_audio.is_empty() { audio_out } else { "aj" };
    let audio_flag = u8::from(has_audio);
    let mut labels: Vec<(String, String)> = Vec::new();
    for (run, &(from, to)) in runs.iter().enumerate() {
        if runs.len() > 1 && from == to {
            labels.push((format!("v{from}"), format!("a{from}")));
            continue;
        }
        let (video, audio) = if runs.len() == 1 { (joined_video.to_owned(), joined_audio.to_owned()) } else { (format!("vr{run}"), format!("ar{run}")) };
        let pads: String = (from..=to).map(|index| if has_audio { format!("[v{index}][a{index}]") } else { format!("[v{index}]") }).collect();
        lines.push(format!("{pads}concat=n={}:v=1:a={audio_flag}[{video}]{}", to - from + 1, if has_audio { format!("[{audio}]") } else { String::new() }));
        labels.push((video, audio));
    }
    let mut current = labels[0].clone();
    let mut length: f64 = lengths[runs[0].0..=runs[0].1].iter().sum();
    for run in 1..runs.len() {
        let duration = dissolve[runs[run - 1].1].unwrap_or(MIN_TRANSITION);
        let last = run + 1 == runs.len();
        let (video, audio) = if last { (joined_video.to_owned(), joined_audio.to_owned()) } else { (format!("vx{run}"), format!("ax{run}")) };
        lines.push(format!("[{}][{}]xfade=transition=fade:duration={}:offset={}[{video}]", current.0, labels[run].0, num(duration), num(length - duration)));
        if has_audio {
            lines.push(format!("[{}][{}]acrossfade=d={}[{audio}]", current.1, labels[run].1, num(duration)));
        }
        length += lengths[runs[run].0..=runs[run].1].iter().sum::<f64>() - duration;
        current = (video, audio);
    }
    let mut video = current.0;
    for (index, overlay) in edit.overlays.iter().enumerate() {
        let shown = (overlay.end - overlay.start).min(length - overlay.at).max(0.0);
        let (at, until) = (num(overlay.at), num(overlay.at + shown));
        let out = if index + 1 == edit.overlays.len() && short.is_none() { video_out.to_owned() } else { format!("vo{index}") };
        lines.push(format!("[{}:v]trim=start={}:end={},setpts=PTS-STARTPTS+{at}/TB,scale={}:-2,setsar=1[ov{index}]",
            overlay.media, num(overlay.start), num(overlay.start + shown), even(overlay.w * f64::from(width))));
        lines.push(format!("[{video}][ov{index}]overlay=x={}:y={}:enable='between(t,{at},{until})':eof_action=pass[{out}]",
            (overlay.x * f64::from(width)).round() as i64, (overlay.y * f64::from(height)).round() as i64));
        video = out;
    }
    if has_audio && !ov_audio.is_empty() {
        let mut pads = format!("[{joined_audio}]");
        for &index in &ov_audio {
            let overlay = &edit.overlays[index];
            let shown = (overlay.end - overlay.start).min(length - overlay.at).max(0.0);
            let volume = if overlay.volume == 1.0 { String::new() } else { format!(",volume={}", num(overlay.volume)) };
            let delay = (overlay.at * 1000.0).round() as u64;
            lines.push(format!("[{}:a]atrim=start={}:end={},asetpts=PTS-STARTPTS{volume},{AUDIO_FORMAT},adelay={delay}:all=1[oa{index}]",
                overlay.media, num(overlay.start), num(overlay.start + shown)));
            pads.push_str(&format!("[oa{index}]"));
        }
        lines.push(format!("{pads}amix=inputs={}:duration=first:normalize=0[{audio_out}]", ov_audio.len() + 1));
    }
    if let Some(short) = short {
        lines.push(quality_scale(&video, short, video_out));
    }
    (lines.join(";\n"), has_audio)
}

/// Stretches where the original sound dips: overlapping (or nearly touching)
/// ducking sounds merge, taking the deepest dip, so the eased edges never
/// stack.
fn duck_spans(sounds: &[Sound], total: f64) -> Vec<(f64, f64, f64)> {
    let mut spans: Vec<(f64, f64, f64)> = sounds.iter().filter(|sound| sound.duck_db > 0.0)
        .map(|sound| (sound.at, sound.at + (sound.end - sound.start).min(total - sound.at).max(0.0), sound.duck_db))
        .filter(|(start, end, _)| end > start).collect();
    spans.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut merged: Vec<(f64, f64, f64)> = Vec::new();
    for span in spans {
        match merged.last_mut() {
            Some(last) if span.0 <= last.1 + 2.0 * DUCK_RAMP => {
                last.1 = last.1.max(span.1);
                last.2 = last.2.max(span.2);
            }
            _ => merged.push(span),
        }
    }
    merged
}

/// The original sound at `10^(-db/20)` from `start` to `end`, easing down
/// over the 0.1 s before and back up over the 0.1 s after.
fn duck_filter(start: f64, end: f64, db: f64) -> String {
    let depth = num(1.0 - 10f64.powf(-db / 20.0));
    let ramp_in = DUCK_RAMP.min(start);
    let from = start - ramp_in;
    let until = end + DUCK_RAMP;
    let shape = if ramp_in > 0.0005 {
        format!("min((t-{})/{},({}-t)/{})", num(from), num(ramp_in), num(until), num(DUCK_RAMP))
    } else {
        format!("({}-t)/{}", num(until), num(DUCK_RAMP))
    };
    format!("volume=volume='1-{depth}*clip({shape},0,1)':eval=frame:enable='between(t,{},{})'", num(from), num(until))
}

/// The export graph: the edit's picture and sound, then (Âm thanh) the
/// original sound's volume and ducking, the added sounds (trimmed, faded,
/// set in volume and delayed to their output time) mixed in at their own
/// levels (`normalize=0`), and a limiter so the sum does not clip. Without
/// added sounds or a volume change it is `render_script` as before. Chữ
/// (Issue #422): with a text layer, `texts` (the `subtitles` filter drawing
/// it) is the last filter of the picture; without texts nothing changes.
fn export_script(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str, mix: &Mix, texts: Option<&str>) -> (String, bool) {
    let video_out = if texts.is_some() { "vt" } else { "v" };
    let (mut graph, has_audio) = mixed_script(edit, inputs, frame, quality, mix, video_out);
    if let Some(filter) = texts {
        graph.push_str(&format!(";\n[{video_out}]{filter}[v]"));
    }
    (graph, has_audio)
}

/// The picture (ending as `[video_out]`) and the mixed sound (`[a]`).
fn mixed_script(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str, mix: &Mix, video_out: &str) -> (String, bool) {
    if mix.sounds.is_empty() && mix.main_volume == 1.0 {
        return video_graph(edit, inputs, frame, quality, video_out, "a");
    }
    let (graph, has_main) = video_graph(edit, inputs, frame, quality, video_out, "am");
    if mix.sounds.is_empty() && !has_main {
        return (graph, false);
    }
    let total = output_seconds(&edit.segments, &edit.transitions);
    let mut lines = vec![graph];
    if !has_main {
        lines.push(format!("anullsrc=r=48000:cl=stereo,atrim=duration={}[am]", num(total)));
    }
    let mut main = Vec::new();
    if mix.main_volume != 1.0 {
        main.push(format!("volume={}", num(mix.main_volume)));
    }
    main.extend(duck_spans(&mix.sounds, total).into_iter().map(|(start, end, db)| duck_filter(start, end, db)));
    if mix.sounds.is_empty() {
        main.push(LIMITER.to_owned());
        lines.push(format!("[am]{}[a]", main.join(",")));
        return (lines.join(";\n"), true);
    }
    let mut pads = if main.is_empty() {
        "[am]".to_owned()
    } else {
        lines.push(format!("[am]{}[amx]", main.join(",")));
        "[amx]".to_owned()
    };
    for (index, sound) in mix.sounds.iter().enumerate() {
        let shown = (sound.end - sound.start).min(total - sound.at).max(0.0);
        let mut filters = vec![format!("atrim=start={}:end={}", num(sound.start), num(sound.start + shown)), "asetpts=PTS-STARTPTS".to_owned(),
            AUDIO_FORMAT.to_owned()];
        let fade_in = sound.fade_in.min(shown);
        let fade_out = sound.fade_out.min(shown - fade_in);
        if fade_in > 0.0 {
            filters.push(format!("afade=t=in:st=0:d={}", num(fade_in)));
        }
        if fade_out > 0.0 {
            filters.push(format!("afade=t=out:st={}:d={}", num(shown - fade_out), num(fade_out)));
        }
        if sound.volume != 1.0 {
            filters.push(format!("volume={}", num(sound.volume)));
        }
        let delay = (sound.at * 1000.0).round() as u64;
        if delay > 0 {
            filters.push(format!("adelay={delay}:all=1"));
        }
        lines.push(format!("[{}:a]{}[sx{index}]", inputs.len() + sound.input, filters.join(",")));
        pads.push_str(&format!("[sx{index}]"));
    }
    lines.push(format!("{pads}amix=inputs={}:duration=first:normalize=0,{LIMITER}[a]", mix.sounds.len() + 1));
    (lines.join(";\n"), true)
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

fn ffmpeg_args(inputs: &[PathBuf], script: &Path, has_audio: bool, encoder: &str, bitrate: u64, fps: &str, output: &Path) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-v", "error", "-y"].map(OsString::from).to_vec();
    for input in inputs {
        args.push("-i".into());
        args.push(input.into());
    }
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

// ---------- Chữ (Issue #422) ----------

/// Texts in one export, and their limits (same as the editor's).
const MAX_TEXTS: usize = 50;
const MAX_TEXT_CHARS: usize = 300;
const MIN_TEXT_SHOWN: f64 = 0.05;
const TEXT_SIZES: (f64, f64) = (16.0, 240.0);
const MAX_TEXT_OUTLINE: f64 = 12.0;
const MIN_TEXT_WIDTH: f64 = 0.1;
const MAX_ANIM: f64 = 2.0;
const TEXT_ALIGNS: [&str; 3] = ["left", "center", "right"];
const TEXT_ANIMATIONS: [&str; 5] = ["none", "fade", "pop", "slide-up", "typewriter"];
/// Slide up starts this far below (a fraction of the frame height), as in the preview.
const TEXT_SLIDE: f64 = 0.05;
/// The text layer beside the filter script.
const TEXTS_FILE: &str = "texts.ass";

/// A text over the picture as the editor sends it: shown from output time
/// `at` for `duration` seconds, its box centred at `x`, `y` and `w` wide
/// (fractions of the frame; lines wrap inside it), `size` and
/// `outline_width` in pixels on a frame whose short side is 1080.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TextClip {
    text: String,
    at: f64,
    duration: f64,
    x: f64,
    y: f64,
    w: f64,
    font: String,
    size: f64,
    color: String,
    outline: String,
    outline_width: f64,
    #[serde(rename = "box")]
    box_color: Option<String>,
    box_opacity: f64,
    bold: bool,
    italic: bool,
    align: String,
    animation: String,
    anim_duration: f64,
}

/// The texts, checked and fitted to the output: each ends by the end of the
/// video, and its animation fits it (a fade in and out takes half of it).
fn validate_texts(texts: &[TextClip], total: f64) -> Result<Vec<TextClip>, String> {
    if texts.len() > MAX_TEXTS {
        return Err(fail("Tối đa 50 chữ trên video"));
    }
    let within = |value: f64, low: f64, high: f64| value.is_finite() && (low..=high).contains(&value);
    texts.iter().map(|text| {
        let words = text.text.trim();
        let valid = !words.is_empty() && text.text.chars().count() <= MAX_TEXT_CHARS
            && !text.text.chars().any(|c| c.is_control() && c != '\n')
            && SUBTITLE_FONTS.contains(&text.font.as_str()) && hex_color(&text.color) && hex_color(&text.outline)
            && text.box_color.as_deref().is_none_or(hex_color) && TEXT_ALIGNS.contains(&text.align.as_str())
            && TEXT_ANIMATIONS.contains(&text.animation.as_str())
            && within(text.at, 0.0, f64::MAX) && text.at < total && within(text.duration, MIN_TEXT_SHOWN - 1e-6, f64::MAX)
            && within(text.w, MIN_TEXT_WIDTH - 1e-6, 1.0) && within(text.x, 0.0, 1.0) && within(text.y, 0.0, 1.0)
            && within(text.size, TEXT_SIZES.0, TEXT_SIZES.1) && within(text.outline_width, 0.0, MAX_TEXT_OUTLINE)
            && within(text.box_opacity, 0.0, 1.0) && within(text.anim_duration, 0.0, MAX_ANIM);
        if !valid {
            return Err(fail("Chữ trên video không hợp lệ: cần nội dung tối đa 300 ký tự, phông có sẵn, nằm trong khung hình và thời lượng video"));
        }
        let duration = text.duration.min(total - text.at);
        let room = if text.animation == "fade" { duration / 2.0 } else { duration };
        Ok(TextClip { duration, anim_duration: text.anim_duration.min(room), ..text.clone() })
    }).collect()
}

/// "#RRGGBB" with an opacity as ASS "&HAABBGGRR" (alpha 00 is opaque).
fn ass_color(hex: &str, alpha: u8) -> String {
    format!("&H{alpha:02X}{}{}{}", &hex[5..7], &hex[3..5], &hex[1..3]).to_ascii_uppercase()
}

/// Seconds as an ASS time, to the centisecond.
fn ass_time(seconds: f64) -> String {
    let cs = (seconds.max(0.0) * 100.0).round() as u64;
    format!("{}:{:02}:{:02}.{:02}", cs / 360_000, cs / 6000 % 60, cs / 100 % 60, cs % 100)
}

/// Typed words are literal: braces never open override blocks and a
/// backslash never pairs with the next character (a word joiner, which
/// renders as nothing, follows it); a line break is `\N`.
fn ass_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\u{2060}"),
            '{' => out.push_str("\\{"),
            '}' => out.push_str("\\}"),
            '\n' => out.push_str("\\N"),
            '\r' => {}
            _ => out.push(c),
        }
    }
    out
}

/// A typewriter's body: every character (with its combining marks) gets a
/// `\ko` karaoke tag, so it shows (fill from SecondaryColour, which is fully
/// transparent, to PrimaryColour, outline included) at
/// `round(index × span / count)` centiseconds, as the preview reveals it.
fn typewriter_text(text: &str, span: f64) -> String {
    let mut units: Vec<String> = Vec::new();
    let mut breaks: Vec<usize> = Vec::new();
    for c in text.chars() {
        let after_break = breaks.last() == Some(&units.len());
        if c == '\n' {
            breaks.push(units.len());
        } else if let Some(unit) = units.last_mut().filter(|_| (0x300..=0x36F).contains(&(c as u32)) && !after_break) {
            unit.push(c);
        } else {
            units.push(c.to_string());
        }
    }
    let count = units.len().max(1) as f64;
    let total = (span * 100.0).round();
    let at = |index: usize| (index as f64 * total / count).round() as u64;
    let mut out = String::new();
    let mut next_break = 0;
    for (index, unit) in units.iter().enumerate() {
        while breaks.get(next_break) == Some(&index) {
            out.push_str("\\N");
            next_break += 1;
        }
        out.push_str(&format!("{{\\ko{}}}{}", at(index + 1) - at(index), ass_text(unit)));
    }
    for _ in next_break..breaks.len() {
        out.push_str("\\N");
    }
    out
}

/// The text layer as an ASS script on the output frame (PlayRes = the
/// exported size): one style per distinct look, one event per text at its
/// position (`\an4/5/6` + `\pos` for left, centred and right lines), the
/// side margins setting where lines wrap, later texts on higher layers.
/// Animations: fade `\fad` in and out; pop `\fscx\fscy` from 40 % through
/// 112 % to 100 % with `\t`, fading in; slide up `\move` from 5 % of the
/// height below, fading in; typewriter `\ko` per character.
fn texts_ass(texts: &[TextClip], width: u32, height: u32) -> String {
    let (w, h) = (f64::from(width), f64::from(height));
    let scale = w.min(h) / 1080.0;
    let mut styles: Vec<String> = Vec::new();
    let mut events = Vec::new();
    for (layer, text) in texts.iter().enumerate() {
        let size = text.size * scale;
        let (border, back, padding) = match &text.box_color {
            Some(color) => (4, ass_color(color, ((1.0 - text.box_opacity) * 255.0).round() as u8),
                (text.size * 0.2).round().max(2.0) * scale),
            None => (1, "&H64000000".to_owned(), 0.0),
        };
        let look = format!("{},{},{},&HFF000000,{},{back},{},{},0,0,100,100,0,0,{border},{},{},5,0,0,0,1",
            text.font, num(size), ass_color(&text.color, 0), ass_color(&text.outline, 0), if text.bold { -1 } else { 0 },
            if text.italic { -1 } else { 0 }, num(text.outline_width * scale), num(padding));
        let index = styles.iter().position(|known| *known == look).unwrap_or_else(|| {
            styles.push(look);
            styles.len() - 1
        });
        let (align, px) = match text.align.as_str() {
            "left" => (4, (text.x - text.w / 2.0) * w),
            "right" => (6, (text.x + text.w / 2.0) * w),
            _ => (5, text.x * w),
        };
        let py = text.y * h;
        let margin = ((1.0 - text.w) / 2.0 * w).round().max(0.0) as i64;
        let span = if text.animation == "none" { 0 } else { (text.anim_duration * 1000.0).round() as u64 };
        let place = if text.animation == "slide-up" && span > 0 {
            format!("\\move({},{},{},{},0,{span})", num(px), num(py + TEXT_SLIDE * h), num(px), num(py))
        } else {
            format!("\\pos({},{})", num(px), num(py))
        };
        let effect = match text.animation.as_str() {
            _ if span == 0 => String::new(),
            "fade" => format!("\\fad({span},{span})"),
            "pop" => {
                let grow = (span as f64 * 0.6).round() as u64;
                format!("\\fad({},0)\\fscx40\\fscy40\\t(0,{grow},\\fscx112\\fscy112)\\t({grow},{span},\\fscx100\\fscy100)", (span as f64 * 0.4).round() as u64)
            }
            "slide-up" => format!("\\fad({span},0)"),
            _ => String::new(),
        };
        let body = if text.animation == "typewriter" && span > 0 { typewriter_text(&text.text, text.anim_duration) } else { ass_text(&text.text) };
        events.push(format!("Dialogue: {layer},{},{},T{},,{margin},{margin},0,,{{\\an{align}{place}{effect}}}{body}",
            ass_time(text.at), ass_time(text.at + text.duration), index + 1));
    }
    let mut script = format!("[Script Info]\n; DubFlow: Chữ\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 1\n\
ScaledBorderAndShadow: yes\nYCbCr Matrix: None\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n");
    for (index, look) in styles.iter().enumerate() {
        script.push_str(&format!("Style: T{},{look}\n", index + 1));
    }
    script.push_str("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n");
    for event in events {
        script.push_str(&event);
        script.push('\n');
    }
    script
}

/// A file as the `filename` of a filter in a filter-graph script: forward
/// slashes, then FFmpeg's two escaping levels (the option value: `\ ' :`;
/// the graph: `\ ' [ ] , ;`), so drive colons, spaces, quotes, brackets
/// and commas in Windows paths survive.
fn filter_path(path: &str) -> String {
    let escape = |text: &str, special: &str| text.chars().fold(String::new(), |mut out, c| {
        if special.contains(c) {
            out.push('\\');
        }
        out.push(c);
        out
    });
    escape(&escape(&path.replace('\\', "/"), "\\':"), "\\'[],;")
}

/// The filter drawing the text layer at `file`.
fn subtitles_filter(file: &str) -> String {
    format!("subtitles=filename={}", filter_path(file))
}

// ---------- Phụ đề (Issue #414) ----------

/// The burned-subtitle look a job renders with, beside its outputs; the
/// worker validates it again (engine/dubflow/subtitle/style.py).
pub(crate) const SUBTITLE_STYLE_FILE: &str = "subtitle_style.json";
/// Stock Windows families covering every Vietnamese letter (same list as the worker).
pub(crate) const SUBTITLE_FONTS: [&str; 12] = ["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman", "Calibri", "Cambria",
    "Palatino Linotype", "Constantia", "Corbel", "Candara", "Segoe UI Black"];
const MAX_REVOICE: usize = 10_000;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SubtitleStyle {
    font: String,
    size: f64,
    primary: String,
    outline: String,
    #[serde(rename = "box")]
    box_color: Option<String>,
    box_opacity: f64,
    outline_width: f64,
    bold: bool,
    align: String,
    /// Normalized centre of a dragged subtitle block; both or neither.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    x: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    y: Option<f64>,
}

fn hex_color(value: &str) -> bool {
    value.len() == 7 && value.starts_with('#') && value[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
}

impl SubtitleStyle {
    pub(crate) fn validate(&self) -> Result<(), String> {
        let within = |value: f64, low: f64, high: f64| value.is_finite() && (low..=high).contains(&value);
        let valid = SUBTITLE_FONTS.contains(&self.font.as_str())
            && within(self.size, 0.6, 2.0) && within(self.box_opacity, 0.0, 1.0) && within(self.outline_width, 0.0, 8.0)
            && hex_color(&self.primary) && hex_color(&self.outline) && self.box_color.as_deref().is_none_or(hex_color)
            && ["bottom", "middle", "top"].contains(&self.align.as_str())
            && self.x.is_some() == self.y.is_some()
            && [self.x, self.y].iter().flatten().all(|value| within(*value, 0.0, 1.0));
        if valid { Ok(()) } else { Err(fail("Kiểu phụ đề không hợp lệ")) }
    }
}

/// Store a job's subtitle look beside its outputs, where the worker reads it.
pub(crate) fn write_subtitle_style(dir: &Path, style: &SubtitleStyle) -> Result<(), String> {
    style.validate()?;
    let body = serde_json::to_vec_pretty(&json!({"schema_version": 1, "style": style})).map_err(|error| fail(error.to_string()))?;
    write_atomic(&dir.join(SUBTITLE_STYLE_FILE), &body).map_err(|error| fail(format!("Không lưu được kiểu phụ đề: {error}")))
}

fn read_subtitle_style(dir: &Path) -> Option<SubtitleStyle> {
    let path = dir.join(SUBTITLE_STYLE_FILE);
    fs::metadata(&path).ok().filter(|metadata| metadata.len() <= 64 * 1024)?;
    let value: Value = serde_json::from_slice(&fs::read(&path).ok()?).ok()?;
    (value["schema_version"] == 1).then_some(())?;
    serde_json::from_value::<SubtitleStyle>(value["style"].clone()).ok().filter(|style| style.validate().is_ok())
}

/// Caption rectangles the worker detected (`caption_scan.json`), each with
/// its `index` there, which `disabled_auto` in cover.json refers to.
fn detected_blur_regions(work: &Path) -> Vec<Value> {
    let path = work.join("caption_scan.json");
    let Some(scan) = fs::metadata(&path).ok().filter(|metadata| metadata.len() <= 4 * 1024 * 1024)
        .and_then(|_| fs::read(&path).ok()).and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()) else { return Vec::new() };
    scan["regions"].as_array().into_iter().flatten().take(256).enumerate().filter(|(_, region)| {
        ["x", "y", "w", "h"].iter().all(|key| region[*key].as_f64().is_some_and(|value| (0.0..=1.0).contains(&value)))
    }).map(|(index, region)| {
        let mut region = region.clone();
        region["index"] = json!(index);
        region
    }).collect()
}

// ---------- Che (Issue #418) ----------

/// How and where source captions are covered, beside a job's outputs; the
/// worker validates it again (engine/dubflow/worker/production_job.py).
pub(crate) const COVER_FILE: &str = "cover.json";
const MAX_COVER_REGIONS: usize = 64;
const MAX_DISABLED: usize = 1024;
const DAY_MS: u64 = 86_400_000;

fn default_direction() -> String { "top".into() }
fn default_strip() -> u32 { 4 }
fn default_cover_color() -> String { "#000000".into() }
fn default_block() -> u32 { 24 }

/// Edge stretch (`stretch`, the default for new videos), `blur`, `color` or `mosaic`.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CoverStyle {
    mode: String,
    #[serde(default = "default_direction")]
    direction: String,
    #[serde(default = "default_strip")]
    strip_px: u32,
    #[serde(default = "default_cover_color")]
    color: String,
    #[serde(default = "default_block")]
    block: u32,
    #[serde(default)]
    feather: u32,
}

impl CoverStyle {
    pub(crate) fn validate(&self) -> Result<(), String> {
        let valid = ["stretch", "blur", "color", "mosaic"].contains(&self.mode.as_str())
            && ["top", "bottom", "both"].contains(&self.direction.as_str())
            && (2..=12).contains(&self.strip_px) && (8..=64).contains(&self.block) && self.feather <= 8 && hex_color(&self.color);
        if valid { Ok(()) } else { Err(fail("Kiểu che không hợp lệ")) }
    }
}

fn inside(x: f64, y: f64, w: f64, h: f64) -> bool {
    [x, y, w, h].iter().all(|value| value.is_finite()) && x >= 0.0 && y >= 0.0 && w >= 0.01 && h >= 0.01
        && x + w <= 1.000001 && y + h <= 1.000001
}

/// A rectangle drawn in Che, for the whole video or one stretch of it.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CoverRegion {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    start_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    end_ms: Option<u64>,
}

/// "Che cố định cả video": one rectangle with no times.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FixedRegion {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

/// What Che applies: the style, regions drawn by hand, a fixed region and
/// detected regions switched off (their index in caption_scan.json).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CoverChoice {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    style: Option<CoverStyle>,
    #[serde(default)]
    regions: Vec<CoverRegion>,
    #[serde(default)]
    disabled_auto: Vec<u32>,
    #[serde(default)]
    fixed: Option<FixedRegion>,
}

impl CoverChoice {
    pub(crate) fn with_style(style: CoverStyle) -> Self {
        Self { style: Some(style), ..Self::default() }
    }

    pub(crate) fn validate(&self) -> Result<(), String> {
        if let Some(style) = &self.style {
            style.validate()?;
        }
        let timed = |region: &CoverRegion| match (region.start_ms, region.end_ms) {
            (None, None) => true,
            (Some(start), Some(end)) => start < end && end <= DAY_MS,
            _ => false,
        };
        let valid = self.regions.len() <= MAX_COVER_REGIONS
            && self.regions.iter().all(|region| inside(region.x, region.y, region.w, region.h) && timed(region))
            && self.fixed.as_ref().is_none_or(|fixed| inside(fixed.x, fixed.y, fixed.w, fixed.h))
            && self.disabled_auto.len() <= MAX_DISABLED && self.disabled_auto.iter().all(|index| *index < 1_000_000);
        if valid { Ok(()) } else { Err(fail("Vùng che không hợp lệ")) }
    }
}

/// Store a job's cover beside its outputs, where the worker reads it.
pub(crate) fn write_cover(dir: &Path, cover: &CoverChoice) -> Result<(), String> {
    cover.validate()?;
    let mut document = serde_json::to_value(cover).map_err(|error| fail(error.to_string()))?;
    document["schema_version"] = json!(1);
    let body = serde_json::to_vec_pretty(&document).map_err(|error| fail(error.to_string()))?;
    write_atomic(&dir.join(COVER_FILE), &body).map_err(|error| fail(format!("Không lưu được cách che chữ: {error}")))
}

fn read_cover(dir: &Path) -> Option<CoverChoice> {
    let path = dir.join(COVER_FILE);
    fs::metadata(&path).ok().filter(|metadata| metadata.len() <= 256 * 1024)?;
    let mut value: Value = serde_json::from_slice(&fs::read(&path).ok()?).ok()?;
    (value["schema_version"] == 1).then_some(())?;
    value.as_object_mut()?.remove("schema_version");
    serde_json::from_value::<CoverChoice>(value).ok().filter(|cover| cover.validate().is_ok())
}

/// Everything the Phụ đề mode needs: each line (subtitle text, spoken text,
/// original), the current look, and what the live preview plays: the clean
/// source video with the dubbed mix (`editable\final_mix.wav`) and the
/// rectangles the render covered.
#[tauri::command]
pub fn edit_subtitles_load(output_path: String) -> Result<Value, String> {
    let dir = crate::review_dir(&output_path)?;
    let cues = crate::review_cues(output_path)?;
    let manifest = crate::read_json(&dir.join("job_manifest.json"), 16 * 1024 * 1024).unwrap_or_else(|_| json!({}));
    let source = manifest["source"]["path"].as_str().map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_file()).map(|path| display_path(&path));
    let mix = dir.join("editable").join("final_mix.wav");
    Ok(json!({
        "cues": cues,
        "style": read_subtitle_style(&dir),
        "source_path": source,
        "mix_path": mix.is_file().then(|| display_path(&mix)),
        "dubbed": manifest["audio"]["mode"] == "dubbed",
        "blur_regions": detected_blur_regions(&dir.join(".dubflow-work")),
        "cover": read_cover(&dir),
        "fonts": SUBTITLE_FONTS,
    }))
}

/// "Áp dụng" in Phụ đề and Che: a new version of the job in a sibling
/// "(sửa N)" folder, as "Xem & sửa bản dịch" makes, carrying the edited
/// lines, what each line's voice speaks, the lines to voice again, the look
/// and the cover (None keeps the original's). The caller re-runs the job
/// there; cached stages, the caption scan among them, are reused.
#[tauri::command]
pub fn edit_subtitles_apply(output_path: String, overrides: std::collections::BTreeMap<String, String>,
    voice: std::collections::BTreeMap<String, String>, revoice: Vec<String>, subtitle_style: SubtitleStyle,
    cover: Option<CoverChoice>) -> Result<String, String> {
    let dir = crate::review_dir(&output_path)?;
    subtitle_style.validate()?;
    if let Some(cover) = &cover {
        cover.validate()?;
    }
    crate::check_review_texts(&overrides)?;
    crate::check_review_texts(&voice)?;
    let known: std::collections::HashSet<String> = crate::review_cues(output_path)?.into_iter().map(|cue| cue.cue_id).collect();
    let mut marked = revoice.clone();
    marked.sort();
    marked.dedup();
    if marked.len() > MAX_REVOICE || marked.len() != revoice.len()
        || !overrides.keys().chain(voice.keys()).chain(marked.iter()).all(|id| known.contains(id)) {
        return Err(fail("Danh sách câu đọc lại không hợp lệ"));
    }
    let revision = crate::Revision { overrides: &overrides, voice: &voice, revoice: &marked, style: Some(&subtitle_style), cover: cover.as_ref() };
    Ok(display_path(&crate::create_revision_with(&dir, &revision)?))
}

/// The editor draft of `original` for its new version: the cuts, inserts and
/// overlays carry over, with the main video now the new version's final.
pub(crate) fn carry_draft(original: &Path, target: &Path) {
    let from = work_dir(original).join("project.json");
    let Some(mut project) = fs::metadata(&from).ok().filter(|metadata| metadata.len() as usize <= PROJECT_LIMIT)
        .and_then(|_| fs::read(&from).ok()).and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .filter(|project| check_project(project).is_ok()) else { return };
    let final_path = display_path(&target.join("final_vi.mp4"));
    for media in project["media"].as_array_mut().into_iter().flatten() {
        if media["id"] == "main" {
            media["path"] = json!(final_path);
        }
    }
    let work = work_dir(target);
    if let Ok(bytes) = check_project(&project) {
        let _ = fs::create_dir_all(&work).and_then(|()| write_atomic(&work.join("project.json"), &bytes));
    }
}

/// The draft for this video, its facts and its subtitles.
#[tauri::command]
pub async fn edit_project_load(output_path: String) -> Result<Value, String> {
    let target = edit_target(&output_path)?;
    let facts = probe(&target.main)?;
    let project_path = target.work.join("project.json");
    let project = fs::metadata(&project_path).ok().filter(|metadata| metadata.len() as usize <= PROJECT_LIMIT)
        .and_then(|_| fs::read(&project_path).ok())
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .filter(|value| value["schema_version"].as_u64().is_some_and(|schema| (1..=4).contains(&schema)));
    // Sounds the draft uses from the user's files may play in the preview again.
    if let Some(project) = &project {
        crate::sfx::allow_files(&draft_sounds(project));
    }
    let cues = target.captions.as_ref().and_then(|captions| fs::metadata(captions).ok().filter(|metadata| metadata.len() <= SRT_LIMIT)
        .and_then(|_| fs::read(captions).ok()))
        .map(|bytes| parse_srt(&String::from_utf8_lossy(&bytes))).unwrap_or_default();
    // Phụ đề and Che re-run a dubbing job; other videos have no such job.
    let reviewable = target.kind == Kind::Job && target.dir.join(".dubflow-work").join("translation.json").is_file();
    Ok(json!({"duration": facts.duration, "width": facts.width, "height": facts.height, "fps": fps_value(&facts.fps), "size_bytes": facts.size,
        "has_audio": facts.has_audio, "project": project, "cues": cues, "kind": target.kind_name(), "reviewable": reviewable,
        "variant": match target.kind { Kind::Product(number) => json!(number), _ => Value::Null }}))
}

/// "30000/1001" as frames per second, to the millisecond.
fn fps_value(fps: &str) -> f64 {
    fps.split_once('/').and_then(|(n, d)| Some(n.parse::<f64>().ok()? / d.parse::<f64>().ok()?.max(1.0)))
        .map_or(30.0, |rate| (rate * 1000.0).round() / 1000.0)
}

/// Length, frame and sound of a local video the user wants to insert (Chèn).
#[tauri::command]
pub async fn edit_media_info(path: String) -> Result<Value, String> {
    let file = crate::playable_media(&path)?;
    let facts = probe(&file)?;
    let title = file.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    Ok(json!({"path": path, "title": title, "duration": facts.duration, "width": facts.width, "height": facts.height,
        "fps": fps_value(&facts.fps), "has_audio": facts.has_audio, "size_bytes": facts.size}))
}

/// The user's audio files a draft's sounds play (schema 3 `audio`).
fn draft_sounds(project: &Value) -> Vec<PathBuf> {
    project["audio"].as_array().into_iter().flatten().take(MAX_SOUNDS)
        .filter(|clip| clip["source"]["kind"] == "file")
        .filter_map(|clip| clip["source"]["path"].as_str().and_then(|path| crate::sfx::audio_file(path).ok()))
        .collect()
}

fn check_project(project: &Value) -> Result<Vec<u8>, String> {
    let bytes = serde_json::to_vec(project).map_err(|_| fail("Bản nháp không hợp lệ"))?;
    let segments = project["segments"].as_array().map_or(0, Vec::len);
    let schema = project["schema_version"].as_u64().unwrap_or(0);
    if bytes.len() > PROJECT_LIMIT || !(1..=4).contains(&schema) || !(1..=MAX_SEGMENTS).contains(&segments) {
        return Err(fail("Bản nháp chỉnh sửa không hợp lệ"));
    }
    Ok(bytes)
}

/// The finished Bán hàng videos, compilations and documentary rough cuts below
/// the given folders, newest first, for the Chỉnh sửa screen (Issue #497).
/// Queue videos are known to the page already.
#[tauri::command]
pub fn edit_library(roots: Vec<String>) -> Vec<Value> {
    let mut items: Vec<(u64, Value)> = Vec::new();
    let mut add = |video: PathBuf, kind: &str, title: String| {
        let Ok(metadata) = fs::metadata(&video) else { return };
        if !metadata.is_file() {
            return;
        }
        let modified = metadata.modified().ok().and_then(|time| time.duration_since(UNIX_EPOCH).ok()).map_or(0, |time| time.as_secs());
        items.push((modified, json!({"path": display_path(&video), "kind": kind, "title": title, "modified": modified, "size_bytes": metadata.len()})));
    };
    let text = |value: &Value| value.as_str().map(str::trim).filter(|text| !text.is_empty()).map(str::to_owned);
    for run in crate::product::product_runs(roots.clone()) {
        let Some(dir) = run["dir"].as_str().map(PathBuf::from) else { continue };
        let name = text(&run["name"]).unwrap_or_else(|| "Video bán hàng".into());
        let videos = run["status"]["videos"].as_array().cloned().unwrap_or_default();
        for video in videos.iter().filter(|video| video["ok"] == true) {
            if let Some(index) = video["index"].as_u64().filter(|index| (1..=99).contains(index)) {
                add(dir.join(format!("video-{index}.mp4")), "product", format!("{name} · video {index}"));
            }
        }
    }
    for run in crate::compilation::compilation_runs(roots.clone()) {
        if let Some(video) = run["video"].as_str() {
            add(PathBuf::from(video), "compilation", text(&run["spec"]["title"]).unwrap_or_else(|| "Video tổng hợp".into()));
        }
    }
    for run in crate::docu::docu_runs(roots) {
        if let Some(dir) = run["dir"].as_str() {
            add(Path::new(dir).join("documentary.mp4"), "docu", text(&run["spec"]["title"]).unwrap_or_else(|| "Phim tài liệu".into()));
        }
    }
    items.sort_by(|a, b| b.0.cmp(&a.0));
    items.into_iter().map(|(_, item)| item).collect()
}

#[tauri::command]
pub fn edit_project_save(output_path: String, project: Value) -> Result<(), String> {
    let bytes = check_project(&project)?;
    let work = edit_target(&output_path)?.work;
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))?;
    write_atomic(&work.join("project.json"), &bytes).map_err(|_| fail("Không lưu được bản nháp chỉnh sửa"))
}

struct ExportJob {
    work: PathBuf,
    /// The main video, then every other media the edit uses, once each.
    inputs: Vec<PathBuf>,
    /// Their paths as the request named them, for exports.json.
    media: Vec<String>,
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
    edit: Edit,
    /// The added sounds as requested, and the original sound's volume, for exports.json.
    audio: Vec<SoundClip>,
    main_volume: f64,
    /// The texts as requested, for exports.json.
    texts: Vec<TextClip>,
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
    command.args(ffmpeg_args(&job.inputs, &job.script, job.has_audio, encoder, job.bitrate, &job.fps, &partial))
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
        "duration": (job.seconds * 1000.0).round() / 1000.0, "quality": job.quality, "segments": job.edit.segments,
        "media": job.media, "transitions": job.edit.transitions, "overlays": job.edit.overlays, "audio": job.audio, "main_volume": job.main_volume,
        "texts": job.texts}));
    write_status(&job.work, "COMPLETED", 100.0, "Đã xuất video.", Some(&target));
}

fn start_export(request: ExportRequest) -> Result<Value, String> {
    let target = edit_target(&request.output_path)?;
    let main = target.main.clone();
    let facts = probe(&main)?;
    if request.media.len() + 1 > MAX_MEDIA {
        return Err(fail("Một bản sửa dùng tối đa 20 video"));
    }
    let mut inputs = vec![main];
    let mut probes = vec![facts.clone()];
    for path in &request.media {
        let file = crate::playable_media(path)?;
        probes.push(probe(&file)?);
        inputs.push(file);
    }
    let durations: Vec<f64> = probes.iter().map(|probe| probe.duration).collect();
    let edit = validate(&request, &durations)?;
    // Âm thanh: each sound's file once, after the videos.
    if request.audio.len() > MAX_SOUNDS {
        return Err(fail("Tối đa 100 âm thanh thêm"));
    }
    let mut sound_inputs: Vec<PathBuf> = Vec::new();
    let mut sound_seconds: Vec<f64> = Vec::new();
    let mut files = Vec::with_capacity(request.audio.len());
    for clip in &request.audio {
        let file = match &clip.source {
            SoundSource::Sfx { id } => crate::sfx::sound_file(id)?,
            SoundSource::File { path } => crate::sfx::audio_file(path)?,
        };
        let index = match sound_inputs.iter().position(|known| *known == file) {
            Some(index) => index,
            None => {
                sound_seconds.push(crate::sfx::audio_seconds(&file)?);
                sound_inputs.push(file);
                sound_inputs.len() - 1
            }
        };
        files.push(index);
    }
    let total = output_seconds(&edit.segments, &edit.transitions);
    let mix = validate_mix(&request.audio, &files, &sound_seconds, request.main_volume, total)?;
    let texts = validate_texts(&request.texts, total)?;
    let folder = target_folder(&request.folder)?;
    let ffmpeg = media_tool("ffmpeg.exe")?;
    let name = base_name(&request.file_name);
    let mut running = RUNNING.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    // One render per video: a product run's videos each have their own work folder.
    let key = target.work.clone();
    if running.contains_key(&key) {
        return Err(fail("Video này đang được xuất"));
    }
    let work = target.work.clone();
    fs::create_dir_all(&work).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))?;
    let lease = crate::work_lease::take(&work, BUSY)?;
    let cancel = work.join("cancel");
    if cancel.exists() {
        fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?;
    }
    let target = unique_target(&folder, &name)?;
    let script = work.join("filter.txt");
    let streams: Vec<Input> = probes.iter().map(|probe| Input { has_audio: probe.has_audio }).collect();
    let frame = Frame { width: facts.width, height: facts.height, fps: facts.fps.clone() };
    let (width, height) = scaled_size(facts.width, facts.height, &request.quality);
    // Chữ: the text layer is drawn on the exported frame, after any scaling.
    let layer = if texts.is_empty() {
        None
    } else {
        let file = work.join(TEXTS_FILE);
        fs::write(&file, texts_ass(&texts, width, height)).map_err(|_| fail("Không ghi được lớp chữ"))?;
        Some(subtitles_filter(&display_path(&file)))
    };
    let (text, has_audio) = export_script(&edit, &streams, &frame, &request.quality, &mix, layer.as_deref());
    inputs.extend(sound_inputs);
    fs::write(&script, text).map_err(|_| fail("Không ghi được bộ lọc xuất video"))?;
    let job = ExportJob {
        work: work.clone(), inputs, media: request.media, script, folder, name, target: target.clone(),
        srt: (!request.cues.is_empty()).then(|| format_srt(&request.cues)),
        seconds: output_seconds(&edit.segments, &edit.transitions), has_audio, bitrate: video_bitrate(width, height, &facts.fps),
        fps: facts.fps, ffmpeg, quality: request.quality, edit, audio: request.audio, main_volume: request.main_volume, texts: request.texts,
    };
    write_status(&work, "RUNNING", 0.0, "Đang bắt đầu xuất video…", Some(&target));
    let entry = Arc::new(Running { child: Mutex::new(None), cancelled: AtomicBool::new(false) });
    running.insert(key.clone(), entry.clone());
    drop(running);
    std::thread::spawn(move || {
        run_export(&job, &entry);
        if let Ok(mut running) = RUNNING.lock() {
            running.remove(&key);
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
    let work = edit_target(&output_path)?.work;
    let mut status = fs::read(work.join("status.json")).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).filter(Value::is_object);
    let ours = RUNNING.lock().is_ok_and(|running| running.contains_key(&work));
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
    let work = edit_target(&output_path)?.work;
    if work.is_dir() {
        fs::write(work.join("cancel"), b"").map_err(|_| fail("Không hủy được xuất video"))?;
    }
    if let Some(entry) = RUNNING.lock().ok().and_then(|running| running.get(&work).cloned()) {
        stop(&entry);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment(start: f64, end: f64, speed: f64, volume: f64) -> Segment {
        Segment { media: 0, start, end, speed, volume }
    }

    fn clip(media: usize, start: f64, end: f64) -> Segment {
        Segment { media, start, end, speed: 1.0, volume: 1.0 }
    }

    fn joint(after: usize, kind: TransitionKind, duration: f64) -> Transition {
        Transition { after, kind, duration }
    }

    fn request(segments: Vec<Segment>) -> ExportRequest {
        ExportRequest { output_path: "unused".into(), media: vec![], segments, transitions: vec![], overlays: vec![], audio: vec![], main_volume: 1.0,
            texts: vec![], quality: "source".into(),
            folder: "unused".into(), file_name: "Phim (sửa 1).mp4".into(), cues: vec![Cue { start_ms: 0, end_ms: 1000, text: "Xin chào".into() }] }
    }

    fn frame() -> Frame {
        Frame { width: 1280, height: 720, fps: "25/1".into() }
    }

    /// Every segment is fitted into the 1280×720, 25 fps main frame.
    const FIT: &str = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25/1,format=yuv420p,settb=AVTB";

    fn expected(text: &str) -> String {
        text.replace("{fit}", FIT).replace("{af}", AUDIO_FORMAT)
    }

    #[test]
    fn writes_the_graph_for_an_inserted_clip_with_a_dissolve() {
        let inputs = [Input { has_audio: true }, Input { has_audio: true }];
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 2.0, 7.0), segment(10.0, 20.0, 1.5, 0.5)],
            transitions: vec![joint(0, TransitionKind::Dissolve, 1.0)], overlays: vec![] };
        let (script, has_audio) = render_script(&edit, &inputs, &frame(), "source");
        assert!(has_audio);
        assert_eq!(script, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS,{fit}[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS,{af}[a0];
[1:v]trim=start=2:end=7,setpts=PTS-STARTPTS,{fit}[v1];
[1:a]atrim=start=2:end=7,asetpts=PTS-STARTPTS,{af}[a1];
[0:v]trim=start=10:end=20,setpts=(PTS-STARTPTS)/1.5,{fit}[v2];
[0:a]atrim=start=10:end=20,asetpts=PTS-STARTPTS,atempo=1.5,volume=0.5,{af}[a2];
[v1][a1][v2][a2]concat=n=2:v=1:a=1[vr1][ar1];
[v0][vr1]xfade=transition=fade:duration=1:offset=9[v];
[a0][ar1]acrossfade=d=1[a]"));
        assert!((output_seconds(&edit.segments, &edit.transitions) - (10.0 + 5.0 + 10.0 / 1.5 - 1.0)).abs() < 1e-9);
    }

    #[test]
    fn fades_through_black_without_changing_the_length() {
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.0, 1.0, 1.0)],
            transitions: vec![joint(0, TransitionKind::Fade, 0.8)], overlays: vec![] };
        assert_eq!(render_script(&edit, &[Input { has_audio: true }], &frame(), "720p").0, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS,{fit},fade=t=out:st=9.6:d=0.4[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS,{af},afade=t=out:st=9.6:d=0.4[a0];
[0:v]trim=start=20:end=30,setpts=PTS-STARTPTS,{fit},fade=t=in:st=0:d=0.4[v1];
[0:a]atrim=start=20:end=30,asetpts=PTS-STARTPTS,{af},afade=t=in:st=0:d=0.4[a1];
[v0][a0][v1][a1]concat=n=2:v=1:a=1[vj][a];
[vj]scale=w='if(gte(iw,ih),-2,720)':h='if(gte(iw,ih),720,-2)',setsar=1[v]"));
        assert_eq!(output_seconds(&edit.segments, &edit.transitions), 20.0);
    }

    #[test]
    fn fills_silence_for_media_without_audio_and_lays_overlays_on_top() {
        let inputs = [Input { has_audio: true }, Input { has_audio: true }, Input { has_audio: false }];
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), clip(2, 0.0, 4.0), segment(10.0, 12.0, 1.0, 1.0)],
            transitions: vec![joint(1, TransitionKind::Dissolve, 0.5)], overlays: vec![
                Overlay { media: 1, at: 3.0, start: 1.0, end: 6.0, x: 0.62, y: 0.03, w: 0.35, volume: 0.8 },
                // Runs past the end of the output: cut there; a silent clip adds no sound.
                Overlay { media: 2, at: 12.0, start: 0.0, end: 4.0, x: 0.0, y: 0.0, w: 0.2, volume: 1.0 }] };
        assert_eq!(render_script(&edit, &inputs, &frame(), "source").0, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS,{fit}[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS,{af}[a0];
[2:v]trim=start=0:end=4,setpts=PTS-STARTPTS,{fit}[v1];
anullsrc=r=48000:cl=stereo,atrim=duration=4,{af}[a1];
[0:v]trim=start=10:end=12,setpts=PTS-STARTPTS,{fit}[v2];
[0:a]atrim=start=10:end=12,asetpts=PTS-STARTPTS,{af}[a2];
[v0][a0][v1][a1]concat=n=2:v=1:a=1[vr0][ar0];
[vr0][v2]xfade=transition=fade:duration=0.5:offset=13.5[vj];
[ar0][a2]acrossfade=d=0.5[aj];
[1:v]trim=start=1:end=6,setpts=PTS-STARTPTS+3/TB,scale=448:-2,setsar=1[ov0];
[vj][ov0]overlay=x=794:y=22:enable='between(t,3,8)':eof_action=pass[vo0];
[2:v]trim=start=0:end=3.5,setpts=PTS-STARTPTS+12/TB,scale=256:-2,setsar=1[ov1];
[vo0][ov1]overlay=x=0:y=0:enable='between(t,12,15.5)':eof_action=pass[v];
[1:a]atrim=start=1:end=6,asetpts=PTS-STARTPTS,volume=0.8,{af},adelay=3000:all=1[oa0];
[aj][oa0]amix=inputs=2:duration=first:normalize=0[a]"));
    }

    #[test]
    fn graphs_without_any_sound_and_main_only_cuts() {
        let silent = [Input { has_audio: false }, Input { has_audio: false }];
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 0.0, 4.0)], transitions: vec![], overlays: vec![] };
        let (script, has_audio) = render_script(&edit, &silent, &frame(), "source");
        assert!(!has_audio);
        assert_eq!(script, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS,{fit}[v0];
[1:v]trim=start=0:end=4,setpts=PTS-STARTPTS,{fit}[v1];
[v0][v1]concat=n=2:v=1:a=0[v]"));
        // A muted overlay of a clip with sound adds no track to a silent edit.
        let muted = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0)], transitions: vec![],
            overlays: vec![Overlay { media: 1, at: 0.0, start: 0.0, end: 1.0, x: 0.0, y: 0.0, w: 0.5, volume: 0.0 }] };
        assert!(!render_script(&muted, &[Input { has_audio: false }, Input { has_audio: true }], &frame(), "source").1);
        // Only the main video, no transitions or overlays: the 4a graph.
        let cut = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.5, 1.5, 0.5)], transitions: vec![], overlays: vec![] };
        assert_eq!(render_script(&cut, &[Input { has_audio: true }], &frame(), "720p"), (filter_script(&cut.segments, true, "720p"), true));
    }

    #[test]
    fn validates_inserted_media_transitions_and_overlays() {
        let mut ok = request(vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 2.0, 8.04), segment(10.0, 20.0, 1.0, 1.0)]);
        ok.media = vec![r"D:\c\intro.mp4".into()];
        ok.transitions = vec![joint(0, TransitionKind::Dissolve, 2.0), joint(1, TransitionKind::Fade, 0.2)];
        ok.overlays = vec![Overlay { media: 1, at: 5.0, start: 0.0, end: 8.04, x: 0.62, y: 0.03, w: 0.35, volume: 1.0 }];
        let edit = validate(&ok, &[30.0, 8.0]).unwrap();
        assert_eq!(edit.segments[1].end, 8.0, "clamped to the clip");
        assert_eq!(edit.overlays[0].end, 8.0);
        assert_eq!(output_seconds(&edit.segments, &edit.transitions), 24.0);
        let cases: Vec<(&str, Box<dyn Fn(&mut ExportRequest)>)> = vec![
            ("unknown media", Box::new(|r| r.segments[1].media = 2)),
            ("clip past its end", Box::new(|r| r.segments[1].end = 9.0)),
            ("too long for the clip", Box::new(|r| r.transitions[0].duration = 3.1)),
            ("over 2 s", Box::new(|r| r.transitions[1].duration = 2.5)),
            ("too short", Box::new(|r| r.transitions[1].duration = 0.1)),
            ("after the last segment", Box::new(|r| r.transitions[1].after = 2)),
            ("out of order", Box::new(|r| r.transitions.swap(0, 1))),
            ("twice", Box::new(|r| r.transitions[1].after = 0)),
            ("nan", Box::new(|r| r.transitions[0].duration = f64::NAN)),
            ("overlay after the end", Box::new(|r| r.overlays[0].at = 24.0)),
            ("overlay outside the frame", Box::new(|r| r.overlays[0].x = 1.2)),
            ("overlay without size", Box::new(|r| r.overlays[0].w = 0.0)),
            ("overlay too loud", Box::new(|r| r.overlays[0].volume = 2.5)),
            ("overlay too short", Box::new(|r| r.overlays[0].start = 7.95)),
            ("overlay media", Box::new(|r| r.overlays[0].media = 5)),
            ("too many overlays", Box::new(|r| r.overlays = vec![r.overlays[0]; 21])),
        ];
        for (label, change) in cases {
            let mut value = request(ok.segments.clone());
            value.media = ok.media.clone();
            value.transitions = ok.transitions.clone();
            value.overlays = ok.overlays.clone();
            change(&mut value);
            assert!(validate(&value, &[30.0, 8.0]).is_err(), "{label}");
        }
        let mut crowded = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        crowded.media = vec!["x.mp4".into(); 20];
        assert!(validate(&crowded, &[30.0; 21]).is_err(), "at most 20 media with the main video");
        assert!(validate(&ok, &[30.0]).is_err(), "every listed media is probed");
        let parsed: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "media": [r"D:\a.mp4"], "quality": "source", "folder": "x",
            "fileName": "x", "cues": [], "segments": [{"media": 1, "start": 0, "end": 1, "speed": 1, "volume": 1}],
            "transitions": [{"after": 0, "type": "dissolve", "duration": 0.5}],
            "overlays": [{"media": 1, "at": 0, "start": 0, "end": 1, "x": 0, "y": 0, "w": 0.5, "volume": 1}]})).unwrap();
        assert_eq!((parsed.segments[0].media, parsed.transitions[0].kind), (1, TransitionKind::Dissolve));
        assert!(serde_json::from_value::<Transition>(json!({"after": 0, "type": "wipe", "duration": 1})).is_err());
        assert_eq!(serde_json::to_value(joint(2, TransitionKind::Fade, 0.5)).unwrap(), json!({"after": 2, "type": "fade", "duration": 0.5}));
    }

    #[test]
    fn validates_segments_quality_name_and_cues() {
        let ok = request(vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.04, 1.5, 0.5)]);
        let segments = validate(&ok, &[30.0]).unwrap().segments;
        assert_eq!(segments[1].end, 30.0, "a hair past the end is clamped");
        assert!(validate(&request((0..500).map(|i| segment(f64::from(i) * 0.1, f64::from(i) * 0.1 + 0.1, 1.0, 1.0)).collect()), &[60.0]).is_ok());
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
            assert!(validate(&value, &[30.0]).is_err(), "{label}");
        }
        let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        value.quality = "4k".into();
        assert!(validate(&value, &[30.0]).is_err());
        for name in ["", "   ", ".mp4", " .MP4 "] {
            let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
            value.file_name = name.into();
            assert!(validate(&value, &[30.0]).is_err(), "{name:?}");
        }
        for cue in [Cue { start_ms: 500, end_ms: 500, text: "x".into() }, Cue { start_ms: 0, end_ms: 11_001, text: "x".into() },
            Cue { start_ms: 0, end_ms: 100, text: "x\u{0}".into() }, Cue { start_ms: 0, end_ms: 100, text: "đ".repeat(2001) }] {
            let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
            value.cues = vec![cue];
            assert!(validate(&value, &[30.0]).is_err());
        }
        let mut value = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        value.cues = vec![Cue { start_ms: 9000, end_ms: 11_000, text: "dòng 1\ndòng 2".into() }];
        assert!(validate(&value, &[30.0]).is_ok(), "a last cue may run up to a second past the end");
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
        let args: Vec<String> = ffmpeg_args(&[PathBuf::from("in.mp4")], Path::new("f.txt"), true, "h264_mf", 3_000_000, "25/1", Path::new("o.mp4.partial"))
            .into_iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args.join(" "), "-nostdin -hide_banner -v error -y -i in.mp4 -/filter_complex f.txt -map [v] -map [a] -c:v h264_mf -b:v 3000000 \
-pix_fmt yuv420p -r 25/1 -c:a aac -b:a 192k -sn -dn -map_chapters -1 -movflags +faststart -progress pipe:1 -nostats -f mp4 o.mp4.partial");
        let silent: Vec<String> = ffmpeg_args(&[PathBuf::from("in.mp4")], Path::new("f.txt"), false, "libopenh264", 2_000_000, "30/1", Path::new("o"))
            .into_iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert!(silent.join(" ").contains("-map [v] -c:v libopenh264 -b:v 2000000 -maxrate 3000000 -bufsize 4000000 -pix_fmt"));
        assert!(!silent.iter().any(|arg| arg == "[a]" || arg == "aac"));
        assert_eq!(progress_percent(5_000_000, 10.0), 50.0);
        assert_eq!(progress_percent(50_000_000, 10.0), 99.0);
        assert_eq!(progress_percent(-1, 10.0), 0.0);
        assert_eq!(output_seconds(&[segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.5, 1.5, 0.5)], &[]), 17.0);
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
    fn product_videos_and_compilations_have_their_own_drafts() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-targets-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let product = base.join("DubFlow Output/Ban hang - Nồi chiên/run-0123456789ab");
        let compilation = base.join("DubFlow Output/Tong hop - Top 3/run-0123456789ab");
        for dir in [&product, &compilation] {
            fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
            fs::write(dir.join(".dubflow-work/spec.json"), b"{}").unwrap();
        }
        for name in ["video-1.mp4", "video-2.mp4", "video-x.mp4", "final.mp4"] {
            fs::write(product.join(name), b"video").unwrap();
        }
        fs::write(compilation.join("compilation.mp4"), b"video").unwrap();
        let first = edit_target(&crate::display_path(&product.join("video-1.mp4"))).unwrap();
        let second = edit_target(&crate::display_path(&product.join("video-2.mp4"))).unwrap();
        assert_eq!((first.kind, second.kind), (Kind::Product(1), Kind::Product(2)));
        assert!(first.work.ends_with(".dubflow-work/edit-1") && second.work.ends_with(".dubflow-work/edit-2"), "one draft per video");
        assert!(first.captions.as_ref().unwrap().ends_with("captions-1.srt"));
        // A documentary run's rough cut, with its English captions; not the same name in another folder.
        let docu = base.join("DubFlow Output/Phim tai lieu - Đập/run-000000000001");
        fs::create_dir_all(docu.join(".dubflow-work")).unwrap();
        fs::write(docu.join(".dubflow-work/spec.json"), b"{\"schema_version\":1}").unwrap();
        fs::write(docu.join("documentary.mp4"), b"video").unwrap();
        let film = edit_target(&crate::display_path(&docu.join("documentary.mp4"))).unwrap();
        assert_eq!(film.kind, Kind::Docu);
        assert!(film.captions.as_ref().unwrap().ends_with("captions_en.srt") && film.work.ends_with(".dubflow-work/edit"));
        fs::write(compilation.join("documentary.mp4"), b"video").unwrap();
        let elsewhere = compilation.join("documentary.mp4").canonicalize().unwrap();
        assert!(dubflow_target(&elsewhere).is_none());
        // Any other video opens as a file, its draft kept in the app's store, not beside it.
        let store = base.join("store");
        let file = file_target(&crate::display_path(&elsewhere), &store).unwrap();
        assert_eq!((file.kind, file.captions.is_none(), file.work.parent()), (Kind::File, true, Some(store.as_path())));
        assert_eq!(file_key(Path::new(r"C:\Videos\Clip.mp4")), file_key(Path::new(r"c:\videos\clip.MP4")));
        assert_ne!(file_key(Path::new(r"C:\Videos\a.mp4")), file_key(Path::new(r"C:\Videos\b.mp4")));
        assert!(file_target(&crate::display_path(&compilation.join(".dubflow-work/spec.json")), &store).is_err(), "not a video");
        let joined = edit_target(&crate::display_path(&compilation.join("compilation.mp4"))).unwrap();
        assert_eq!((joined.kind, joined.captions.is_none()), (Kind::Compilation, true));
        assert!(joined.work.ends_with(".dubflow-work/edit"));
        for name in ["video-x.mp4", "final.mp4"] {
            assert!(dubflow_target(&product.join(name).canonicalize().unwrap()).is_none(), "{name}");
        }
        // Without its request a run is not a product run.
        fs::remove_file(product.join(".dubflow-work/spec.json")).unwrap();
        assert!(dubflow_target(&product.join("video-1.mp4").canonicalize().unwrap()).is_none());
        fs::write(product.join(".dubflow-work/spec.json"), b"{}").unwrap();
        let project = json!({"schema_version": 4, "duration": 10.0, "segments": [{"id": "s1", "start": 0, "end": 10, "speed": 1, "volume": 1}], "texts": []});
        edit_project_save(crate::display_path(&product.join("video-2.mp4")), project.clone()).unwrap();
        assert!(product.join(".dubflow-work/edit-2/project.json").is_file());
        assert!(!product.join(".dubflow-work/edit-1").exists());
        edit_project_save(crate::display_path(&compilation.join("compilation.mp4")), project).unwrap();
        assert!(compilation.join(".dubflow-work/edit/project.json").is_file());
        assert_eq!(edit_status(crate::display_path(&product.join("video-1.mp4"))).unwrap()["exports"], json!([]));
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn saves_drafts_and_lists_exports_for_finished_videos_only() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-host-{}", std::process::id()));
        let dir = base.join("DubFlow Output/clip - vi/run-1");
        fs::create_dir_all(&dir).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        fs::write(dir.join("job_manifest.json"), json!({"source": {"path": "C:/clip.mp4"}}).to_string()).unwrap();
        let path = crate::display_path(&video);
        let project = json!({"schema_version": 1, "duration": 10.0, "segments": [{"id": "s1", "start": 0, "end": 10, "speed": 1, "volume": 1}]});
        edit_project_save(path.clone(), project.clone()).unwrap();
        let saved: Value = serde_json::from_slice(&fs::read(dir.join(".dubflow-work/edit/project.json")).unwrap()).unwrap();
        assert_eq!(saved, project);
        assert!(edit_project_save(path.clone(), json!({"schema_version": 5, "segments": [{}]})).is_err());
        assert!(edit_project_save(path.clone(), json!({"schema_version": 3, "segments": [{}], "audio": [], "main_volume": 1})).is_ok(), "4e drafts");
        assert!(edit_project_save(path.clone(), json!({"schema_version": 4, "segments": [{}], "texts": []})).is_ok(), "4f drafts");
        assert!(edit_project_save(path.clone(), json!({"schema_version": 1, "segments": []})).is_err());
        assert!(edit_project_save(path.clone(), json!({"schema_version": 1, "segments": [{}], "pad": "x".repeat(PROJECT_LIMIT)})).is_err());
        fs::write(dir.join("other.mp4"), b"video").unwrap();
        // Any other video is a file (Issue #497): its draft never goes beside it or into the job's folder.
        let as_file = |path: &Path| edit_target(&crate::display_path(path)).map_or(true, |target| target.kind == Kind::File && !target.work.starts_with(&base));
        assert!(as_file(&dir.join("other.mp4")), "only final_vi.mp4 is the job's");
        assert!(edit_project_save("relative/final_vi.mp4".into(), project.clone()).is_err());
        let outside = base.join("elsewhere/final_vi.mp4");
        fs::create_dir_all(outside.parent().unwrap()).unwrap();
        fs::write(&outside, b"video").unwrap();
        assert!(as_file(&outside), "only DubFlow results are jobs");
        // A user's own folder named like DubFlow's: final_vi.mp4 there is a file, its draft never beside it.
        let personal = base.join("Videos/DubFlow Output/Personal");
        fs::create_dir_all(&personal).unwrap();
        fs::write(personal.join("final_vi.mp4"), b"video").unwrap();
        assert!(dubflow_target(&personal.join("final_vi.mp4").canonicalize().unwrap()).is_none());
        let store = base.join("store");
        let target = file_target(&crate::display_path(&personal.join("final_vi.mp4")), &store).unwrap();
        assert!(target.kind == Kind::File && target.work.starts_with(&store));
        fs::write(personal.join("job_manifest.json"), b"not json").unwrap();
        assert!(dubflow_target(&personal.join("final_vi.mp4").canonicalize().unwrap()).is_none(), "a manifest must be the worker's");
        assert!(!personal.join(".dubflow-work").exists(), "nothing written beside the user's video");
        assert_eq!(dubflow_target(&video.canonicalize().unwrap()).map(|target| target.kind), Some(Kind::Job), "a recorded job stays a job");

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

    fn style(changes: Value) -> Value {
        let mut value = json!({"font": "Candara", "size": 1.2, "primary": "#FDE047", "outline": "#000000", "box": null,
            "box_opacity": 0.6, "outline_width": 3, "bold": true, "align": "bottom"});
        value.as_object_mut().unwrap().extend(changes.as_object().unwrap().clone());
        value
    }

    #[test]
    fn subtitle_styles_are_validated_like_the_worker_does() {
        let parse = |value: Value| serde_json::from_value::<SubtitleStyle>(value).map_err(|error| error.to_string()).and_then(|style| style.validate());
        assert!(parse(style(json!({}))).is_ok());
        assert!(parse(style(json!({"box": "#000000", "x": 0.5, "y": 0.2, "align": "top"}))).is_ok());
        for changes in [json!({"font": "Impact"}), json!({"font": "Georgia"}), json!({"size": 2.5}), json!({"size": 0.5}),
                        json!({"primary": "yellow"}), json!({"outline": "#12345"}), json!({"box": "#0000000"}), json!({"box_opacity": -0.1}),
                        json!({"outline_width": 9}), json!({"align": "left"}), json!({"x": 0.5}), json!({"x": 1.5, "y": 0.5}),
                        json!({"bold": "yes"}), json!({"extra": 1})] {
            assert!(parse(style(changes.clone())).is_err(), "{changes}");
        }
        assert_eq!(SUBTITLE_FONTS.len(), 12);
    }

    #[test]
    fn cover_choices_are_validated_like_the_worker_does() {
        let parse = |value: Value| serde_json::from_value::<CoverChoice>(value).map_err(|error| error.to_string()).and_then(|cover| cover.validate());
        assert!(parse(json!({})).is_ok());
        assert!(parse(json!({"style": {"mode": "stretch", "direction": "both", "strip_px": 12, "feather": 8}})).is_ok());
        assert!(parse(json!({"style": {"mode": "color", "color": "#1A2B3C"}, "fixed": {"x": 0, "y": 0.9, "w": 1, "h": 0.1},
            "regions": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "start_ms": 0, "end_ms": 900}], "disabled_auto": [0, 3]})).is_ok());
        for value in [json!({"style": {"mode": "smear"}}), json!({"style": {"mode": "stretch", "direction": "left"}}),
                      json!({"style": {"mode": "stretch", "strip_px": 1}}), json!({"style": {"mode": "mosaic", "block": 65}}),
                      json!({"style": {"mode": "color", "color": "red"}}), json!({"style": {"mode": "stretch", "feather": 9}}),
                      json!({"style": {"mode": "stretch", "sigma": 4}}), json!({"style": {"direction": "top"}}),
                      json!({"regions": [{"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.1}]}),
                      json!({"regions": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "start_ms": 900}]}),
                      json!({"regions": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "start_ms": 900, "end_ms": 900}]}),
                      json!({"fixed": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "start_ms": 0, "end_ms": 10}}),
                      json!({"disabled_auto": [-1]}), json!({"disabled_auto": [{"start_ms": 0, "end_ms": 10}]}), json!({"colour": 1})] {
            assert!(parse(value.clone()).is_err(), "{value}");
        }
        let many = json!({"regions": vec![json!({"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}); MAX_COVER_REGIONS + 1]});
        assert!(parse(many).is_err());
    }

    #[test]
    fn applying_subtitles_makes_a_new_version_with_texts_voices_style_and_the_draft() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-subs-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let dir = base.join("DubFlow Output/clip - vi");
        let work = dir.join(".dubflow-work");
        fs::create_dir_all(work.join("edit")).unwrap();
        fs::create_dir_all(dir.join("editable")).unwrap();
        let source = base.join("clip.mp4");
        for (path, body) in [(dir.join("final_vi.mp4"), "video".to_owned()), (source.clone(), "source".to_owned()),
                             (dir.join("editable/final_mix.wav"), "mix".to_owned()),
                             (work.join("edit/exports.json"), "[]".to_owned()), (work.join("edit/status.json"), "{}".to_owned()),
                             (work.join("caption_scan.json"), json!({"regions": [{"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1}, {"x": 2}]}).to_string()),
                             (dir.join("job_manifest.json"), json!({"source": {"path": crate::display_path(&source)}, "audio": {"mode": "dubbed"}}).to_string())] {
            fs::write(path, body).unwrap();
        }
        let cues = ["a", "b"].map(|id| json!({"cue_id": id, "start_ms": 0, "end_ms": 1000, "source_text": "源", "translated_text": "Chào"}));
        fs::write(work.join("translation.json"), json!({"cues": cues}).to_string()).unwrap();
        fs::write(work.join("edit/project.json"), json!({"schema_version": 2, "duration": 10.0, "media": [
            {"id": "main", "path": "old", "title": "", "duration": 10.0, "width": 0, "height": 0, "fps": 30, "hasAudio": true, "kind": "main"}],
            "segments": [{"id": "s1", "mediaId": "main", "start": 0, "end": 4, "speed": 1, "volume": 1}]}).to_string()).unwrap();
        let dir = PathBuf::from(crate::display_path(&fs::canonicalize(&dir).unwrap()));
        let output = crate::display_path(&dir.join("final_vi.mp4"));

        let loaded = edit_subtitles_load(output.clone()).unwrap();
        assert_eq!(loaded["cues"].as_array().unwrap().len(), 2);
        assert_eq!(loaded["cues"][0]["voice_text"], "Chào");
        assert_eq!(loaded["style"], Value::Null);
        assert_eq!(loaded["source_path"], crate::display_path(&source));
        assert!(loaded["mix_path"].as_str().unwrap().ends_with("final_mix.wav"));
        assert_eq!(loaded["dubbed"], true);
        assert_eq!(loaded["blur_regions"].as_array().unwrap().len(), 1, "an invalid region is dropped");
        assert_eq!(loaded["blur_regions"][0]["index"], 0, "each keeps its index in the scan");
        assert_eq!(loaded["cover"], Value::Null);

        let chosen: SubtitleStyle = serde_json::from_value(style(json!({"x": 0.5, "y": 0.3}))).unwrap();
        let texts = std::collections::BTreeMap::from([("a".to_owned(), "Xin chào".to_owned())]);
        let voice = std::collections::BTreeMap::from([("a".to_owned(), "Chào".to_owned())]);
        assert!(edit_subtitles_apply(output.clone(), texts.clone(), voice.clone(), vec!["nope".into()], chosen.clone(), None).is_err());
        assert!(edit_subtitles_apply(output.clone(), texts.clone(), voice.clone(), vec!["b".into(), "b".into()], chosen.clone(), None).is_err());
        let bad: SubtitleStyle = serde_json::from_value(style(json!({"font": "Impact"}))).unwrap();
        assert!(edit_subtitles_apply(output.clone(), texts.clone(), voice.clone(), vec![], bad, None).is_err());
        let wide: CoverChoice = serde_json::from_value(json!({"regions": [{"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.1}]})).unwrap();
        assert!(edit_subtitles_apply(output.clone(), texts.clone(), voice.clone(), vec![], chosen.clone(), Some(wide)).is_err());
        assert!(!dir.parent().unwrap().join("clip - vi (sửa 1)").exists(), "a refused apply creates nothing");

        let cover: CoverChoice = serde_json::from_value(json!({"style": {"mode": "mosaic", "block": 32},
            "regions": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1, "start_ms": 1000, "end_ms": 2500}],
            "disabled_auto": [0], "fixed": {"x": 0.7, "y": 0.02, "w": 0.25, "h": 0.06}})).unwrap();
        let revision = PathBuf::from(edit_subtitles_apply(output.clone(), texts, voice, vec!["b".into()], chosen.clone(), Some(cover.clone())).unwrap());
        assert_eq!(revision, dir.parent().unwrap().join("clip - vi (sửa 1)"));
        let overrides: Value = serde_json::from_slice(&fs::read(revision.join("translation_overrides.json")).unwrap()).unwrap();
        assert_eq!(overrides, json!({"schema_version": 1, "cues": {"a": "Xin chào"}, "voice": {"a": "Chào"}, "revoice": ["b"]}));
        assert_eq!(read_subtitle_style(&revision), Some(chosen));
        let draft: Value = serde_json::from_slice(&fs::read(revision.join(".dubflow-work/edit/project.json")).unwrap()).unwrap();
        assert_eq!(draft["media"][0]["path"], crate::display_path(&revision.join("final_vi.mp4")));
        assert_eq!(draft["segments"][0]["end"], 4);
        assert!(!revision.join(".dubflow-work/edit/exports.json").exists() && !revision.join(".dubflow-work/edit/status.json").exists(),
            "the old version's exports stay with it");
        assert!(read_subtitle_style(&dir).is_none(), "the original is untouched");
        // The new version's lines report what their voices speak.
        fs::write(revision.join("final_vi.mp4"), "video").unwrap();
        let again = edit_subtitles_load(crate::display_path(&revision.join("final_vi.mp4"))).unwrap();
        assert_eq!((again["cues"][0]["text"].as_str(), again["cues"][0]["voice_text"].as_str()), (Some("Xin chào"), Some("Chào")));
        assert_eq!(again["style"]["font"], "Candara");
        assert_eq!(read_cover(&revision), Some(cover.clone()));
        assert_eq!(again["cover"]["style"], json!({"mode": "mosaic", "direction": "top", "strip_px": 4, "color": "#000000", "block": 32, "feather": 0}));
        assert_eq!(again["cover"]["disabled_auto"], json!([0]));
        let written: Value = serde_json::from_slice(&fs::read(revision.join(COVER_FILE)).unwrap()).unwrap();
        assert_eq!(written["schema_version"], 1);
        assert!(read_cover(&dir).is_none(), "the original keeps its cover");
        fs::remove_dir_all(base).unwrap();
    }

    fn sound(input: usize, at: f64, start: f64, end: f64) -> Sound {
        Sound { input, at, start, end, volume: 1.0, fade_in: 0.0, fade_out: 0.0, duck_db: 0.0 }
    }

    fn whole(seconds: f64) -> Edit {
        Edit { segments: vec![segment(0.0, seconds, 1.0, 1.0)], transitions: vec![], overlays: vec![] }
    }

    const MAIN_ONLY: &str = "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[v0][a0]concat=n=1:v=1:a=1[v][am]";

    #[test]
    fn mixes_a_sound_effect_with_fades_and_limits_the_sum() {
        let mix = Mix { sounds: vec![Sound { volume: 0.8, fade_in: 0.2, fade_out: 0.5, ..sound(0, 2.5, 0.0, 1.2) }], main_volume: 1.0 };
        let (script, has_audio) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, None);
        assert!(has_audio);
        assert_eq!(script, expected(&format!("{MAIN_ONLY};
[1:a]atrim=start=0:end=1.2,asetpts=PTS-STARTPTS,{{af}},afade=t=in:st=0:d=0.2,afade=t=out:st=0.7:d=0.5,volume=0.8,adelay=2500:all=1[sx0];
[am][sx0]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.891:level=0:latency=1[a]")));
    }

    #[test]
    fn ducks_the_original_sound_under_a_clip() {
        // A file that ducks 12 dB, and a louder sound effect cut at the end of the output that ducks 6 dB.
        let mix = Mix { sounds: vec![Sound { duck_db: 12.0, ..sound(0, 4.0, 0.0, 2.0) }, Sound { volume: 1.5, duck_db: 6.0, ..sound(1, 9.5, 0.5, 3.0) }],
            main_volume: 1.0 };
        let (script, _) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, None);
        assert_eq!(script, expected(&format!("{MAIN_ONLY};
[am]volume=volume='1-0.749*clip(min((t-3.9)/0.1,(6.1-t)/0.1),0,1)':eval=frame:enable='between(t,3.9,6.1)',\
volume=volume='1-0.499*clip(min((t-9.4)/0.1,(10.1-t)/0.1),0,1)':eval=frame:enable='between(t,9.4,10.1)'[amx];
[1:a]atrim=start=0:end=2,asetpts=PTS-STARTPTS,{{af}},adelay=4000:all=1[sx0];
[2:a]atrim=start=0.5:end=1,asetpts=PTS-STARTPTS,{{af}},volume=1.5,adelay=9500:all=1[sx1];
[amx][sx0][sx1]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.891:level=0:latency=1[a]")));
        // Overlapping ducks merge and keep the deepest; one at the very start has no ease-in.
        let close = [Sound { duck_db: 6.0, ..sound(0, 1.0, 0.0, 1.0) }, Sound { duck_db: 12.0, ..sound(0, 2.1, 0.0, 0.9) }];
        assert_eq!(duck_spans(&close, 10.0), vec![(1.0, 3.0, 12.0)]);
        assert_eq!(duck_filter(0.0, 1.0, 10.0), "volume=volume='1-0.684*clip((1.1-t)/0.1,0,1)':eval=frame:enable='between(t,0,1.1)'");
    }

    #[test]
    fn changes_the_original_volume_and_keeps_plain_edits_as_before() {
        let edit = whole(10.0);
        let quiet = Mix { sounds: vec![], main_volume: 0.5 };
        assert_eq!(export_script(&edit, &[Input { has_audio: true }], &frame(), "source", &quiet, None).0, expected(&format!("{MAIN_ONLY};
[am]volume=0.5,alimiter=limit=0.891:level=0:latency=1[a]")));
        let plain = Mix { sounds: vec![], main_volume: 1.0 };
        assert_eq!(export_script(&edit, &[Input { has_audio: true }], &frame(), "source", &plain, None), render_script(&edit, &[Input { has_audio: true }], &frame(), "source"));
        // A silent video gets a sound track only for added sounds.
        assert!(!export_script(&edit, &[Input { has_audio: false }], &frame(), "source", &quiet, None).1);
        let (script, has_audio) = export_script(&edit, &[Input { has_audio: false }], &frame(), "source", &Mix { sounds: vec![sound(0, 0.0, 0.0, 1.0)], main_volume: 0.5 }, None);
        assert!(has_audio);
        assert_eq!(script, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[v0]concat=n=1:v=1:a=0[v];
anullsrc=r=48000:cl=stereo,atrim=duration=10[am];
[am]volume=0.5[amx];
[1:a]atrim=start=0:end=1,asetpts=PTS-STARTPTS,{af}[sx0];
[amx][sx0]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.891:level=0:latency=1[a]"));
    }

    #[test]
    fn validates_added_sounds() {
        let clip = |at: f64, end: f64| SoundClip { source: SoundSource::Sfx { id: "whoosh-01".into() }, at, start: 0.0, end, volume: 1.0, fade_in: 0.0,
            fade_out: 0.0, duck_db: 0.0 };
        let mix = validate_mix(&[clip(2.0, 0.64)], &[0], &[0.6], 1.0, 10.0).unwrap();
        assert_eq!(mix.sounds[0].end, 0.6, "clamped to the file");
        assert!(validate_mix(&[clip(10.0, 0.5)], &[0], &[0.6], 1.0, 10.0).is_err(), "starts after the output");
        assert!(validate_mix(&[clip(1.0, 0.7)], &[0], &[0.6], 1.0, 10.0).is_err(), "past the file");
        assert!(validate_mix(&[SoundClip { fade_in: 3.5, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[SoundClip { duck_db: 30.0, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[SoundClip { volume: f64::NAN, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[], &[], &[], 2.5, 10.0).is_err());
        let request: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "segments": [{"start": 0, "end": 1, "speed": 1, "volume": 1}],
            "quality": "source", "folder": "x", "fileName": "a", "cues": [], "mainVolume": 0.8,
            "audio": [{"source": {"kind": "file", "path": "D:\\a.mp3"}, "at": 0, "start": 0, "end": 1, "volume": 1, "fade_in": 0, "fade_out": 0, "duck_db": 6}]})).unwrap();
        assert_eq!(request.main_volume, 0.8);
        assert_eq!(request.audio[0].source, SoundSource::File { path: "D:\\a.mp3".into() });
        let old: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "segments": [], "quality": "source", "folder": "x", "fileName": "a", "cues": []})).unwrap();
        assert!(old.audio.is_empty() && old.main_volume == 1.0, "4a–4d requests");
        assert_eq!(draft_sounds(&json!({"audio": [{"source": {"kind": "sfx", "id": "pop-01"}}, {"source": {"kind": "file", "path": "relative.mp3"}}]})).len(), 0);
    }

    fn text(words: &str, changes: Value) -> TextClip {
        let mut value = json!({"text": words, "at": 1.0, "duration": 3.0, "x": 0.5, "y": 0.2, "w": 0.8, "font": "Segoe UI", "size": 90,
            "color": "#FFFFFF", "outline": "#000000", "outline_width": 3, "box": "#000000", "box_opacity": 0.6, "bold": true, "italic": false,
            "align": "center", "animation": "fade", "anim_duration": 0.5});
        value.as_object_mut().unwrap().extend(changes.as_object().unwrap().clone());
        serde_json::from_value(value).unwrap()
    }

    #[test]
    fn writes_the_text_layer_as_ass() {
        let texts = [
            // A boxed two-line callout fading in and out.
            text("Xin chào\nPhần 2", json!({})),
            // A yellow title popping in.
            text("Quá đỉnh!", json!({"at": 2.5, "duration": 2, "y": 0.6, "w": 0.9, "font": "Arial", "size": 108, "color": "#FDE047",
                "outline_width": 4.5, "box": null, "animation": "pop"})),
            // A left-aligned italic channel name sliding up, braces kept literal.
            text("@Kênh {Vui}", json!({"at": 0, "duration": 10, "x": 0.22, "y": 0.08, "w": 0.38, "font": "Arial", "size": 54, "outline_width": 0,
                "box": null, "bold": false, "italic": true, "align": "left", "animation": "slide-up", "anim_duration": 0.4})),
            // Typed out, right-aligned, in the first look (its style is reused).
            text("Gõ\nchữ", json!({"at": 5, "duration": 2, "w": 0.6, "y": 0.5, "align": "right", "animation": "typewriter"})),
        ];
        assert_eq!(texts_ass(&texts, 1280, 720), "\
[Script Info]
; DubFlow: Chữ
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 1
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, \
Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T1,Segoe UI,60,&H00FFFFFF,&HFF000000,&H00000000,&H66000000,-1,0,0,0,100,100,0,0,4,2,12,5,0,0,0,1
Style: T2,Arial,72,&H0047E0FD,&HFF000000,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: T3,Arial,36,&H00FFFFFF,&HFF000000,&H00000000,&H64000000,0,-1,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,T1,,128,128,0,,{\\an5\\pos(640,144)\\fad(500,500)}Xin chào\\NPhần 2
Dialogue: 1,0:00:02.50,0:00:04.50,T2,,64,64,0,,{\\an5\\pos(640,432)\\fad(200,0)\\fscx40\\fscy40\\t(0,300,\\fscx112\\fscy112)\\t(300,500,\\fscx100\\fscy100)}Quá đỉnh!
Dialogue: 2,0:00:00.00,0:00:10.00,T3,,397,397,0,,{\\an4\\move(38.4,93.6,38.4,57.6,0,400)\\fad(400,0)}@Kênh \\{Vui\\}
Dialogue: 3,0:00:05.00,0:00:07.00,T1,,256,256,0,,{\\an6\\pos(1024,360)}{\\ko10}G{\\ko10}õ\\N{\\ko10}c{\\ko10}h{\\ko10}ữ
");
        // Sizes follow the short side: a portrait 1080×1920 frame draws at 1080p sizes.
        let portrait = texts_ass(&texts[..1], 1080, 1920);
        assert!(portrait.contains("Style: T1,Segoe UI,90,&H00FFFFFF,&HFF000000,&H00000000,&H66000000,-1,0,0,0,100,100,0,0,4,3,18,5,0,0,0,1"));
        assert!(portrait.contains("Dialogue: 0,0:00:01.00,0:00:04.00,T1,,108,108,0,,{\\an5\\pos(540,384)\\fad(500,500)}"));
        assert_eq!(ass_text("a\\N{b}\r\nc"), "a\\\u{2060}N\\{b\\}\\Nc", "typed backslashes never form tags");
        assert_eq!(ass_time(3725.256), "1:02:05.26");
        assert_eq!(ass_color("#1A2B3C", 0x66), "&H663C2B1A");
        // Over the whole span, rounded per character; a combining mark stays with its letter.
        assert_eq!(typewriter_text("a\u{301}b c", 0.25), "{\\ko6}a\u{301}{\\ko7}b{\\ko6} {\\ko6}c");
        assert_eq!(typewriter_text("\nab\n", 0.1), "\\N{\\ko5}a{\\ko5}b\\N");
    }

    #[test]
    fn draws_texts_as_the_last_filter_and_keeps_graphs_without_texts() {
        let filter = subtitles_filter(r"D:\DubFlow Output\it's [1], a;b\.dubflow-work\edit\texts.ass");
        assert_eq!(filter, r"subtitles=filename=D\\:/DubFlow Output/it\\\'s \[1\]\, a\;b/.dubflow-work/edit/texts.ass");
        let plain = Mix { sounds: vec![], main_volume: 1.0 };
        let input = [Input { has_audio: true }];
        assert_eq!(export_script(&whole(10.0), &input, &frame(), "source", &plain, Some("subtitles=filename=t.ass")).0, "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[v0][a0]concat=n=1:v=1:a=1[vt][a];
[vt]subtitles=filename=t.ass[v]");
        // After the quality scale and the mix, with overlays and transitions: still the last filter.
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.0, 1.0, 1.0)],
            transitions: vec![joint(0, TransitionKind::Fade, 0.8)],
            overlays: vec![Overlay { media: 0, at: 1.0, start: 0.0, end: 2.0, x: 0.0, y: 0.0, w: 0.5, volume: 0.0 }] };
        let mix = Mix { sounds: vec![], main_volume: 0.5 };
        let (with, has_audio) = export_script(&edit, &input, &frame(), "720p", &mix, Some("subtitles=filename=t.ass"));
        let (without, _) = export_script(&edit, &input, &frame(), "720p", &mix, None);
        assert!(has_audio);
        assert_eq!(with, format!("{};\n[vt]subtitles=filename=t.ass[v]", without.replace("setsar=1[v]", "setsar=1[vt]")));
        assert!(without.ends_with("[am]volume=0.5,alimiter=limit=0.891:level=0:latency=1[a]") && without.contains("setsar=1[v]"));
        assert_eq!(with.matches("[v]").count(), 1, "one picture output");
    }

    #[test]
    fn validates_texts() {
        let ok = text("Xin chào", json!({}));
        let fitted = validate_texts(&[text("x", json!({"at": 9.0, "duration": 3.0, "anim_duration": 1.5}))], 10.0).unwrap();
        assert_eq!((fitted[0].duration, fitted[0].anim_duration), (1.0, 0.5), "cut at the end, a fade takes half");
        assert!(validate_texts(&vec![ok.clone(); 50], 10.0).is_ok());
        assert!(validate_texts(&vec![ok.clone(); 51], 10.0).is_err());
        let bad = [json!({"text": "  "}), json!({"text": "đ".repeat(301)}), json!({"text": "a\u{0}b"}), json!({"text": "a\r\nb"}),
                   json!({"font": "Impact"}), json!({"color": "white"}), json!({"box": "#00000"}), json!({"align": "justify"}),
                   json!({"animation": "spin"}), json!({"at": 10.0}), json!({"at": -1.0}), json!({"duration": 0.01}), json!({"w": 0.05}),
                   json!({"x": 1.2}), json!({"size": 300}), json!({"size": 10}), json!({"outline_width": 13}), json!({"box_opacity": 1.5}),
                   json!({"anim_duration": 2.5}), json!({"duration": f64::NAN})];
        for changes in bad {
            let mut value = serde_json::to_value(&ok).unwrap();
            value.as_object_mut().unwrap().extend(changes.as_object().unwrap().clone());
            let parsed = serde_json::from_value::<TextClip>(value);
            assert!(parsed.map_or(true, |clip| validate_texts(&[clip], 10.0).is_err()), "{changes}");
        }
        assert!(validate_texts(&[text(&"đ".repeat(300), json!({}))], 10.0).is_ok());
        assert!(serde_json::from_value::<TextClip>(json!({"text": "x"})).is_err());
        let request: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "segments": [], "quality": "source", "folder": "x",
            "fileName": "a", "cues": [], "texts": [serde_json::to_value(&ok).unwrap()]})).unwrap();
        assert_eq!(request.texts, vec![ok]);
    }
}
