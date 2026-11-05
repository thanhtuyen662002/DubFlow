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
/// Lớp chồng (Issue #520): the faintest overlay, and the widest frame around one.
const MIN_OPACITY: f64 = 0.1;
const MAX_BORDER: f64 = 20.0;
/// A picture lasts this long as an input; its overlays pick their part.
pub(crate) const STILL_SECONDS: f64 = 3600.0;
/// A looping sound repeats at most this long a part (it is held in memory).
const MAX_LOOP_PART: f64 = 600.0;
/// Chuẩn hóa độ to: the loudness online platforms play at.
const LOUDNESS: &str = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000";
const MAX_TEXT_SHADOW: f64 = 12.0;
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
/// Xuất video options (Issue #526): frame rates, frame shapes and how the bars are filled.
const EXPORT_FPS: [u32; 5] = [24, 25, 30, 50, 60];
const ASPECTS: [&str; 4] = ["16:9", "9:16", "1:1", "4:5"];
/// The shipped FFmpeg is the LGPL build (no libx264): Media Foundation's H.264
/// encoder first, the bundled OpenH264 when Media Foundation refuses.
const ENCODERS: [&str; 2] = ["h264_mf", "libopenh264"];
const BUSY: &str = "Video này đang được xuất ở cửa sổ DubFlow khác";

struct Running {
    child: Mutex<Option<Child>>,
    cancelled: AtomicBool,
}

static RUNNING: LazyLock<Mutex<HashMap<PathBuf, Arc<Running>>>> = LazyLock::new(|| Mutex::new(HashMap::new()));

/// One part of the output: `[start, end)` of media `media` (0 = the main video),
/// with its look (Issue #518): crop, rotation, flips, a slow zoom, colour,
/// reverse, a held frame and sound fades. Absent look fields change nothing.
#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Segment {
    #[serde(default)]
    media: usize,
    start: f64,
    end: f64,
    speed: f64,
    volume: f64,
    /// Fractions of the source picture kept, then fitted to the frame.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    crop: Option<Crop>,
    /// 0, 90, 180 or 270 degrees clockwise.
    #[serde(default, skip_serializing_if = "is_zero")]
    rotate: u16,
    #[serde(default, skip_serializing_if = "is_false")]
    flip_h: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    flip_v: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    zoom: Option<Zoom>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    color: Option<Color>,
    #[serde(default, skip_serializing_if = "is_false")]
    reverse: bool,
    /// Hold the frame at `start` for this many seconds, without sound.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    freeze: Option<f64>,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    fade_in: f64,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    fade_out: f64,
    /// LUT màu (Issue #531): index into the request's `luts` (.cube files).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    lut: Option<usize>,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Crop {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

/// Scale `from` → `to` over the segment, centred on x, y (fractions of the picture).
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Zoom {
    from: f64,
    to: f64,
    x: f64,
    y: f64,
}

/// brightness −0.5…0.5, contrast 0.5…1.5, saturation 0…2, temperature −1…1.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Color {
    brightness: f64,
    contrast: f64,
    saturation: f64,
    temperature: f64,
}

fn is_zero(value: &u16) -> bool {
    *value == 0
}

fn is_zero_f64(value: &f64) -> bool {
    *value == 0.0
}

#[allow(clippy::trivially_copy_pass_by_ref)]
fn is_one(value: &f64) -> bool {
    *value == 1.0
}

fn is_false(value: &bool) -> bool {
    !*value
}

/// Playing backwards buffers the whole part: at most two minutes of media.
const MAX_REVERSE: f64 = 120.0;
const MAX_FREEZE: f64 = 10.0;
const MIN_FREEZE: f64 = 0.2;
const MAX_SEGMENT_FADE: f64 = 3.0;

impl Segment {
    /// Plays its part as filmed: the 4a graph can render it.
    fn is_plain(&self) -> bool {
        self.crop.is_none() && self.rotate == 0 && !self.flip_h && !self.flip_v && self.zoom.is_none() && self.color.is_none()
            && !self.reverse && self.freeze.is_none() && self.fade_in == 0.0 && self.fade_out == 0.0 && self.lut.is_none()
    }

    /// Why its look cannot be rendered, if so.
    fn look_problem(&self) -> Option<&'static str> {
        let unit = |value: f64| (0.0..=1.0).contains(&value);
        if let Some(crop) = self.crop {
            if ![crop.x, crop.y, crop.w, crop.h].iter().all(|value| value.is_finite()) || !unit(crop.x) || !unit(crop.y)
                || crop.w < 0.1 - 1e-6 || crop.h < 0.1 - 1e-6 || crop.x + crop.w > 1.0 + 1e-4 || crop.y + crop.h > 1.0 + 1e-4 {
                return Some("Vùng cắt khung phải nằm trong hình");
            }
        }
        if ![0, 90, 180, 270].contains(&self.rotate) {
            return Some("Chỉ xoay được 90, 180 hoặc 270 độ");
        }
        if let Some(zoom) = self.zoom {
            if ![zoom.from, zoom.to, zoom.x, zoom.y].iter().all(|value| value.is_finite()) || !(1.0..=3.0).contains(&zoom.from)
                || !(1.0..=3.0).contains(&zoom.to) || !unit(zoom.x) || !unit(zoom.y) {
                return Some("Zoom phải từ 1× đến 3×");
            }
        }
        if let Some(color) = self.color {
            if ![color.brightness, color.contrast, color.saturation, color.temperature].iter().all(|value| value.is_finite())
                || !(-0.5..=0.5).contains(&color.brightness) || !(0.5..=1.5).contains(&color.contrast)
                || !(0.0..=2.0).contains(&color.saturation) || !(-1.0..=1.0).contains(&color.temperature) {
                return Some("Thông số màu không hợp lệ");
            }
        }
        if let Some(freeze) = self.freeze {
            if !freeze.is_finite() || !(MIN_FREEZE - 1e-6..=MAX_FREEZE + 1e-6).contains(&freeze) || self.reverse {
                return Some("Dừng hình phải từ 0,2 đến 10 giây");
            }
        }
        if self.reverse && self.end - self.start > MAX_REVERSE + 1e-3 {
            return Some("Tua ngược chỉ dùng cho đoạn dài tối đa 2 phút");
        }
        if ![self.fade_in, self.fade_out].iter().all(|value| value.is_finite() && (0.0..=MAX_SEGMENT_FADE).contains(value)) {
            return Some("Âm thanh mờ dần tối đa 3 giây");
        }
        None
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum TransitionKind {
    /// Fade through black: half out, half in; the length stays.
    Fade,
    /// Cross-dissolve: the neighbours overlap by the duration.
    Dissolve,
    /// The rest overlap like a dissolve, drawn by `xfade` (Issue #520).
    FadeWhite,
    WipeLeft,
    WipeRight,
    SlideLeft,
    SlideRight,
    ZoomIn,
    CircleOpen,
}

impl TransitionKind {
    /// The `xfade` transition of an overlapping joint; None for fade through black.
    fn xfade(self) -> Option<&'static str> {
        Some(match self {
            Self::Fade => return None,
            Self::Dissolve => "fade",
            Self::FadeWhite => "fadewhite",
            Self::WipeLeft => "wipeleft",
            Self::WipeRight => "wiperight",
            Self::SlideLeft => "slideleft",
            Self::SlideRight => "slideright",
            Self::ZoomIn => "zoomin",
            Self::CircleOpen => "circleopen",
        })
    }
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
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
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
    /// 0.1–1 (Issue #520); 1 is opaque.
    #[serde(default = "full_volume", skip_serializing_if = "is_one")]
    opacity: f64,
    /// Corner radius, a fraction of the overlay's short side (0–0.5; 0.5 is round).
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    radius: f64,
    /// "ellipse" (Hình tròn, Issue #531): an ellipse filling its box; the radius is unused.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    shape: Option<String>,
    /// A frame around it, pixels on a frame whose short side is 1080 (0–20), in `border_color`.
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    border: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    border_color: Option<String>,
    /// Hiện dần / tắt dần (Issue #526), seconds of its shown time.
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    fade_in: f64,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    fade_out: f64,
    /// Chuyển động: where it ends; it moves and resizes evenly from x, y, w.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    to: Option<Placement>,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Placement {
    x: f64,
    y: f64,
    w: f64,
}

impl Default for Overlay {
    fn default() -> Self {
        Self { media: 0, at: 0.0, start: 0.0, end: 0.0, x: 0.0, y: 0.0, w: 0.0, volume: 1.0, opacity: 1.0, radius: 0.0, shape: None, border: 0.0, border_color: None,
            fade_in: 0.0, fade_out: 0.0, to: None }
    }
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
    /// Lặp lại (Issue #520): the part repeats until the end of the video.
    #[serde(rename = "loop", default, skip_serializing_if = "is_false")]
    repeat: bool,
    /// Lọc ồn (Issue #533): FFT noise reduction.
    #[serde(default, skip_serializing_if = "is_false")]
    denoise: bool,
    /// Chỉnh âm (EQ) preset.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    eq: Option<EqPreset>,
}

/// Chỉnh âm presets (Issue #533): Giọng rõ, Ấm, Sáng, Trầm.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum EqPreset {
    Voice,
    Warm,
    Bright,
    Bass,
}

/// Lọc ồn and chỉnh âm of one sound (or of the output's own sound).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
struct Clean {
    denoise: bool,
    eq: Option<EqPreset>,
}

impl Clean {
    fn is_plain(self) -> bool {
        !self.denoise && self.eq.is_none()
    }

    /// Native FFmpeg filters (in the LGPL build): rumble cut and FFT noise reduction, then the EQ.
    fn filters(self) -> Vec<String> {
        let mut filters = Vec::new();
        if self.denoise {
            filters.push("highpass=f=70".to_owned());
            filters.push("afftdn=nr=12:nf=-40".to_owned());
        }
        match self.eq {
            Some(EqPreset::Voice) => filters.extend(["highpass=f=90", "equalizer=f=250:t=q:w=1:g=-3", "equalizer=f=3500:t=q:w=1.2:g=4"].map(str::to_owned)),
            Some(EqPreset::Warm) => filters.extend(["equalizer=f=150:t=q:w=1:g=3", "equalizer=f=7000:t=q:w=1:g=-2"].map(str::to_owned)),
            Some(EqPreset::Bright) => filters.push("equalizer=f=6000:t=q:w=1:g=4".to_owned()),
            Some(EqPreset::Bass) => filters.push("bass=g=6:f=100".to_owned()),
            None => {}
        }
        filters
    }
}

/// A validated sound: `input` indexes the audio files, which follow the video inputs.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
struct Sound {
    input: usize,
    at: f64,
    start: f64,
    end: f64,
    volume: f64,
    fade_in: f64,
    fade_out: f64,
    duck_db: f64,
    repeat: bool,
    clean: Clean,
}

impl Sound {
    /// Seconds it plays in an output `total` seconds long.
    fn played(&self, total: f64) -> f64 {
        if self.repeat { (total - self.at).max(0.0) } else { (self.end - self.start).min(total - self.at).max(0.0) }
    }
}

/// The added sounds and the volume of the output's own sound.
#[derive(Debug, Clone, Default, PartialEq)]
struct Mix {
    sounds: Vec<Sound>,
    main_volume: f64,
    /// Lọc ồn / chỉnh âm of the output's own sound (Issue #533).
    main_clean: Clean,
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
    /// What the page opened the video as (see `edit_target`).
    kind: String,
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
    /// Che drawn in the editor (Issue #510): applied to the main video, in its own time.
    #[serde(default)]
    cover: Option<CoverChoice>,
    /// Chuẩn hóa độ to (Issue #520): the sound brought to -14 LUFS.
    #[serde(default)]
    normalize: bool,
    /// Phụ đề lines burned in (Issue #523): drawn on the main video, in its own time.
    #[serde(default)]
    burn: Option<BurnSubtitles>,
    /// Số khung/giây (Issue #526); None keeps the main video's.
    #[serde(default)]
    fps: Option<u32>,
    /// Dung lượng: "low" or "high"; None is the usual bitrate.
    #[serde(default)]
    bitrate: Option<String>,
    /// Khung hình: the picture fitted into this shape, with black ("black") or blurred ("blur") bars.
    #[serde(default)]
    aspect: Option<String>,
    #[serde(default)]
    fill: Option<String>,
    /// Chỉ âm thanh: an .m4a of the edit's sound, no picture.
    #[serde(default)]
    audio_only: bool,
    /// LUT màu (Issue #531): .cube files the segments' `lut` points at.
    #[serde(default)]
    luts: Vec<String>,
    /// Lọc ồn / chỉnh âm of the output's own sound (Issue #533).
    #[serde(default)]
    main_denoise: bool,
    #[serde(default)]
    main_eq: Option<EqPreset>,
}

/// What follows the edit's picture and sound in the export graph.
#[derive(Debug, Default)]
struct Finish {
    /// The text layer (`subtitles` filter), drawn on the exported frame.
    texts: Option<String>,
    /// The picture fitted into another frame shape (`reframe_filter`).
    reframe: Option<String>,
    normalize: bool,
    /// The picture is dropped (`nullsink`).
    audio_only: bool,
}

/// A frame of `aspect` ("9:16"…) that keeps the export's short side.
fn aspect_size(width: u32, height: u32, aspect: &str) -> (u32, u32) {
    let Some((w, h)) = aspect.split_once(':').and_then(|(w, h)| Some((w.parse::<f64>().ok()?, h.parse::<f64>().ok()?))) else {
        return (width, height);
    };
    let short = f64::from(width.min(height));
    let even = |value: f64| ((value / 2.0).round() as u32 * 2).max(2);
    if w >= h { (even(short * w / h), even(short)) } else { (even(short), even(short * h / w)) }
}

/// The picture fitted whole into `width`×`height`, over black or over a blurred, darkened copy of itself.
fn reframe_filter(width: u32, height: u32, blur: bool) -> String {
    let fit = format!("scale={width}:{height}:force_original_aspect_ratio=decrease");
    if !blur {
        return format!("{fit},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1");
    }
    format!("split[rf0][rf1];[rf0]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma=40,\
lutyuv=y='val*0.6'[rfb];[rf1]{fit}[rff];[rfb][rff]overlay=(W-w)/2:(H-h)/2,setsar=1")
}

/// A validated edit, clamped to its media.
#[derive(Debug, Clone, Default, PartialEq)]
struct Edit {
    segments: Vec<Segment>,
    transitions: Vec<Transition>,
    overlays: Vec<Overlay>,
    /// LUT files (paths) the segments' `lut` index into.
    luts: Vec<String>,
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
    /// A file's: the app-owned `edit-files` store its `work` must stay in.
    store: Option<PathBuf>,
}

impl Target {
    /// Checked again right before each durable write: a file's draft folder
    /// is still a plain folder directly inside the app's plain store.
    fn confined(&self) -> Result<(), String> {
        match &self.store {
            Some(store) => confined_work(store, &self.work).map(|_| ()),
            None => Ok(()),
        }
    }

    /// The draft folder, ready to write in: a file's is checked (and made
    /// without following any link); a DubFlow run's is made as before.
    fn prepare(&self) -> Result<(), String> {
        if self.store.is_some() {
            return self.confined();
        }
        fs::create_dir_all(&self.work).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))
    }

    fn kind_name(&self) -> &'static str {
        match self.kind { Kind::Job => "job", Kind::Product(_) => "product", Kind::Compilation => "compilation", Kind::Docu => "docu", Kind::File => "file" }
    }
}

/// The video `output_path` as the editor treats it. `kind` is what the page
/// opened it as: "file" (picked or dropped from the computer) always keeps
/// its draft in the app's own store. Otherwise the video is DubFlow's only
/// when an app-owned record proves it (a completed queue job's status, or a
/// run DubFlow started); anything else, a folder merely shaped like
/// DubFlow's included, is a file too (Issue #497).
fn edit_target(output_path: &str, kind: &str) -> Result<Target, String> {
    resolve_target(output_path, kind, &crate::control_root()?)
}

/// Where files' drafts live, below the data root.
const FILE_STORE: &str = "edit-files";

fn resolve_target(output_path: &str, kind: &str, data: &Path) -> Result<Target, String> {
    let store = data.join(FILE_STORE);
    match kind {
        "file" => return file_target(output_path, &store),
        "job" | "product" | "compilation" | "docu" => {}
        _ => return Err(fail("Loại video không hợp lệ")),
    }
    match output_file(output_path).ok().and_then(|video| shaped_target(&video)).filter(|target| recorded(data, target)) {
        Some(target) if target.kind_name() == kind => Ok(target),
        Some(_) => Err(fail("Video này không khớp với loại đã chọn")),
        None => file_target(output_path, &store),
    }
}

/// Where a video DubFlow made would keep its draft, by its name and folder
/// alone. Not proof: `recorded` decides whether it is DubFlow's.
fn shaped_target(video: &Path) -> Option<Target> {
    let video = video.to_path_buf();
    let dir = video.parent()?.to_path_buf();
    let name = video.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    let (kind, work, captions) = if name == "final_vi.mp4" {
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
    Some(Target { kind, main: dir.join(name), dir, work, captions, store: None })
}

/// Whether DubFlow's own records, below the data root, say it made `target`.
fn recorded(data: &Path, target: &Target) -> bool {
    match target.kind {
        Kind::Job => recorded_job(data, &target.main),
        Kind::Product(_) => recorded_run(data, "product", &target.dir),
        Kind::Compilation => recorded_run(data, "compilation", &target.dir),
        Kind::Docu => recorded_run(data, "docu", &target.dir),
        Kind::File => false,
    }
}

const MAX_JOB_RECORDS: usize = 5000;

/// `recorded_job` in DubFlow's own data root.
pub(crate) fn recorded_job_output(video: &Path) -> bool {
    crate::control_root().is_ok_and(|data| recorded_job(&data, video))
}

/// A queue job the supervisor finished with `video` as its output: its status
/// file `<data root>\control\jobs\<id>.json` names it.
fn recorded_job(data: &Path, video: &Path) -> bool {
    let Ok(video) = video.canonicalize() else { return false };
    let Ok(entries) = fs::read_dir(data.join("control").join("jobs")) else { return false };
    entries.flatten().take(MAX_JOB_RECORDS)
        .filter(|entry| entry.path().extension().is_some_and(|extension| extension == "json"))
        .filter_map(|entry| crate::read_json(&entry.path(), 1024 * 1024).ok())
        .any(|record| record["status"]["state"] == "COMPLETED"
            && record["output_path"].as_str().and_then(|path| Path::new(path).canonicalize().ok()).is_some_and(|path| path == video))
}

/// The app-owned record of a run DubFlow started: one small file per run,
/// `<data root>\control\runs\<key>.json`, keyed by the run's folder.
fn run_record(data: &Path, dir: &Path) -> Option<PathBuf> {
    let dir = dir.canonicalize().ok()?;
    Some(data.join("control").join("runs").join(format!("{}.json", file_key(&dir))))
}

/// Remember that DubFlow started the `kind` run in `dir` ("product",
/// "compilation" or "docu"), so the editor may keep drafts beside it.
pub(crate) fn record_run(kind: &str, dir: &Path) {
    if let Ok(data) = crate::control_root() {
        let _ = record_run_in(&data, kind, dir);
    }
}

fn record_run_in(data: &Path, kind: &str, dir: &Path) -> Result<(), String> {
    let path = run_record(data, dir).ok_or_else(|| fail("Không tìm thấy thư mục kết quả"))?;
    let canonical = dir.canonicalize().map_err(|_| fail("Không tìm thấy thư mục kết quả"))?;
    let record = json!({"schema_version": 1, "kind": kind, "dir": display_path(&canonical)});
    fs::create_dir_all(path.parent().unwrap_or(data)).and_then(|()| write_atomic(&path, record.to_string().as_bytes()))
        .map_err(|_| fail("Không ghi được hồ sơ phiên"))
}

fn recorded_run(data: &Path, kind: &str, dir: &Path) -> bool {
    let (Some(path), Ok(dir)) = (run_record(data, dir), dir.canonicalize()) else { return false };
    crate::read_json(&path, 64 * 1024).is_ok_and(|record| record["schema_version"] == 1 && record["kind"] == kind
        && record["dir"].as_str().and_then(|recorded| Path::new(recorded).canonicalize().ok()).is_some_and(|recorded| recorded == dir))
}

/// Any playable video: its draft is kept below `store`, one folder per file.
fn file_target(path: &str, store: &Path) -> Result<Target, String> {
    let main = crate::playable_media(path)?;
    let dir = main.parent().ok_or_else(|| fail("Không tìm thấy thư mục của video"))?.to_path_buf();
    let work = confined_work(store, &store.join(file_key(&main)))?;
    Ok(Target { kind: Kind::File, work, main, dir, captions: None, store: Some(store.to_path_buf()) })
}

/// A symlink, junction or any other reparse point.
fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    metadata.file_type().is_symlink()
}

/// `dir` as a plain folder: made if missing (never through a link), refused
/// when it is a link, a junction or not a folder.
fn plain_folder(dir: &Path) -> Result<(), String> {
    const ESCAPE: &str = "Thư mục bản nháp của DubFlow không an toàn (là liên kết); hãy xóa nó rồi thử lại";
    match fs::symlink_metadata(dir) {
        Ok(metadata) if is_link(&metadata) || !metadata.is_dir() => Err(fail(ESCAPE)),
        Ok(_) => Ok(()),
        Err(_) => {
            match fs::create_dir(dir) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(_) => return Err(fail("Không tạo được thư mục chỉnh sửa")),
            }
            let metadata = fs::symlink_metadata(dir).map_err(|_| fail("Không tạo được thư mục chỉnh sửa"))?;
            if is_link(&metadata) || !metadata.is_dir() { Err(fail(ESCAPE)) } else { Ok(()) }
        }
    }
}

/// A file's draft folder `work`, directly inside the app-owned `store`
/// (`<data root>\edit-files`): both plain folders, neither a link, and `work`
/// resolving to a child of the store as it resolves (Issue #497).
fn confined_work(store: &Path, work: &Path) -> Result<PathBuf, String> {
    let data = store.parent().ok_or_else(|| fail("Không tìm thấy thư mục dữ liệu"))?;
    if work.parent() != Some(store) {
        return Err(fail("Thư mục bản nháp không hợp lệ"));
    }
    plain_folder(store)?;
    plain_folder(work)?;
    let (Ok(data), Ok(real_store), Ok(real_work)) = (data.canonicalize(), store.canonicalize(), work.canonicalize()) else {
        return Err(fail("Không tìm thấy thư mục chỉnh sửa"));
    };
    if real_store != data.join(store.file_name().unwrap_or_default()) || real_work.parent() != Some(real_store.as_path()) {
        return Err(fail("Thư mục bản nháp của DubFlow không an toàn (là liên kết); hãy xóa nó rồi thử lại"));
    }
    Ok(work.to_path_buf())
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

/// A picture's size as a probe: it lasts `STILL_SECONDS`, has no sound, plays at 30 frames a second.
fn parse_still(text: &str) -> Option<Probe> {
    let value: Value = serde_json::from_str(text).ok()?;
    let video = value["streams"].as_array()?.iter().find(|stream| stream["codec_type"] == "video")?;
    let size = value["format"]["size"].as_str().and_then(|text| text.parse::<u64>().ok()).unwrap_or(0);
    Some(Probe {
        duration: STILL_SECONDS,
        width: u32::try_from(video["width"].as_u64()?).ok().filter(|width| *width > 0)?,
        height: u32::try_from(video["height"].as_u64()?).ok().filter(|height| *height > 0)?,
        fps: "30/1".into(),
        has_audio: false,
        size,
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
    if crate::is_still(video) {
        return String::from_utf8(output.stdout).ok().as_deref().and_then(parse_still).ok_or_else(|| fail("Không đọc được ảnh"));
    }
    String::from_utf8(output.stdout).ok().as_deref().and_then(parse_probe)
        .ok_or_else(|| fail("Không đọc được thời lượng video"))
}

fn segment_length(segment: &Segment) -> f64 {
    segment.freeze.unwrap_or((segment.end - segment.start) / segment.speed)
}

/// Seconds the edited video lasts: each dissolve overlaps its neighbours.
fn output_seconds(segments: &[Segment], transitions: &[Transition]) -> f64 {
    let overlap: f64 = transitions.iter().filter(|joint| joint.kind.xfade().is_some()).map(|joint| joint.duration).sum();
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
        if !(0.25..=4.0).contains(&segment.speed) {
            return Err(fail("Tốc độ mỗi đoạn phải từ 0,25× đến 4×"));
        }
        if let Some(problem) = segment.look_problem() {
            return Err(fail(problem));
        }
        if segment.lut.is_some_and(|lut| lut >= request.luts.len()) {
            return Err(fail("Đoạn dùng LUT không có trong danh sách"));
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
        if !(MIN_OPACITY - 1e-6..=1.0).contains(&overlay.opacity) || !(0.0..=0.5).contains(&overlay.radius)
            || !(0.0..=MAX_BORDER).contains(&overlay.border) || overlay.border_color.as_deref().is_some_and(|color| !hex_color(color))
            || overlay.shape.as_deref().is_some_and(|shape| shape != "ellipse")
            || (overlay.border > 0.0 && overlay.border_color.is_none()) {
            return Err(fail("Độ trong, bo góc hoặc viền của lớp chồng không hợp lệ"));
        }
        let fades = [overlay.fade_in, overlay.fade_out].iter().all(|fade| fade.is_finite() && (0.0..=MAX_FADE).contains(fade));
        let placed = overlay.to.is_none_or(|to| [to.x, to.y, to.w].iter().all(|value| value.is_finite())
            && (0.0..=1.0).contains(&to.x) && (0.0..=1.0).contains(&to.y) && (0.01..=1.0).contains(&to.w));
        if !fades || !placed {
            return Err(fail("Hiện dần, tắt dần hoặc chuyển động của lớp chồng không hợp lệ"));
        }
        overlays.push(Overlay { end, ..overlay.clone() });
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
    if request.luts.len() > MAX_LUTS || !request.luts.iter().all(|lut| is_lut_path(lut)) {
        return Err(fail("Chỉ dùng được tối đa 8 file LUT .cube"));
    }
    Ok(Edit { segments, transitions, overlays, luts: request.luts.clone() })
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
        if clip.repeat && end - clip.start > MAX_LOOP_PART + 1e-6 {
            return Err(fail("Âm thanh lặp lại dài tối đa 10 phút một lượt"));
        }
        sounds.push(Sound { input, at: clip.at, start: clip.start, end, volume: clip.volume, fade_in: clip.fade_in, fade_out: clip.fade_out,
            duck_db: clip.duck_db, repeat: clip.repeat, clean: Clean { denoise: clip.denoise, eq: clip.eq } });
    }
    Ok(Mix { sounds, main_volume, main_clean: Clean::default() })
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
fn unique_target(folder: &Path, name: &str, extension: &str) -> Result<PathBuf, String> {
    (1..=999).map(|number| folder.join(source::numbered_file_name(name, extension, number)))
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

/// A frame rate such as "30" or "30000/1001" as a number.
fn fps_number(fps: &str) -> f64 {
    let value = match fps.split_once('/') {
        Some((top, bottom)) => top.trim().parse::<f64>().unwrap_or(30.0) / bottom.trim().parse::<f64>().unwrap_or(1.0),
        None => fps.trim().parse::<f64>().unwrap_or(30.0),
    };
    if value.is_finite() && value > 0.0 { value } else { 30.0 }
}

/// One segment's picture: its part (held, reversed or retimed), then crop,
/// rotation, flips and colour on the source picture, the fit into the frame,
/// and the slow zoom on the fitted picture.
fn segment_video(segment: &Segment, fit: &str, frame: &Frame, luts: &[String]) -> Vec<String> {
    let (start, end) = (num(segment.start), num(segment.end));
    let mut video = vec![format!("trim=start={start}:end={end}")];
    if let Some(hold) = segment.freeze {
        video.push("setpts=PTS-STARTPTS".to_owned());
        video.push("trim=end_frame=1".to_owned());
        video.push(format!("tpad=stop_mode=clone:stop_duration={}", num(hold)));
    } else if segment.reverse {
        video.push("setpts=PTS-STARTPTS".to_owned());
        video.push("reverse".to_owned());
        if segment.speed != 1.0 {
            video.push(format!("setpts=PTS/{}", num(segment.speed)));
        }
    } else {
        video.push(if segment.speed == 1.0 { "setpts=PTS-STARTPTS".to_owned() } else { format!("setpts=(PTS-STARTPTS)/{}", num(segment.speed)) });
    }
    video.extend(look_filters(segment));
    if let Some(lut) = segment.lut.and_then(|lut| luts.get(lut)) {
        video.push(lut_filter(lut));
    }
    video.push(fit.to_owned());
    if let Some(hold) = segment.freeze {
        video.push(format!("trim=duration={}", num(hold)));
    }
    if let Some(zoom) = segment.zoom {
        let frames = (segment_length(segment) * fps_number(&frame.fps)).round().max(2.0);
        let (from, change) = (num(zoom.from), num(zoom.to - zoom.from));
        video.push(format!(
            "zoompan=z='{from}+{change}*min(on/{},1)':x='(iw-iw/zoom)*{}':y='(ih-ih/zoom)*{}':d=1:s={}x{}:fps={}",
            num(frames - 1.0), num(zoom.x), num(zoom.y), frame.width, frame.height, frame.fps));
        video.push("setsar=1,format=yuv420p,settb=AVTB".to_owned());
    }
    video
}

/// Crop, rotation, flips and colour of a segment, in that order, on the source picture.
fn look_filters(segment: &Segment) -> Vec<String> {
    let mut filters = Vec::new();
    if let Some(crop) = segment.crop {
        filters.push(format!("crop=w='trunc(iw*{}/2)*2':h='trunc(ih*{}/2)*2':x='iw*{}':y='ih*{}'", num(crop.w), num(crop.h), num(crop.x), num(crop.y)));
    }
    match segment.rotate {
        90 => filters.push("transpose=1".to_owned()),
        180 => filters.push("hflip,vflip".to_owned()),
        270 => filters.push("transpose=2".to_owned()),
        _ => {}
    }
    if segment.flip_h {
        filters.push("hflip".to_owned());
    }
    if segment.flip_v {
        filters.push("vflip".to_owned());
    }
    if let Some(color) = segment.color {
        // The shipped LGPL FFmpeg has no `eq`: luma through a lookup table, saturation with `hue`.
        if color.brightness != 0.0 || color.contrast != 1.0 {
            filters.push(format!("lutyuv=y='clip((val-128)*{}+128+{},0,255)'", num(color.contrast), num((color.brightness * 0.6 * 255.0).round())));
        }
        if color.saturation != 1.0 {
            filters.push(format!("hue=s={}", num(color.saturation)));
        }
        if color.temperature != 0.0 {
            let (mid, high) = (color.temperature * 0.22, color.temperature * 0.1);
            filters.push(format!("colorbalance=rs={}:bs={}:rm={}:bm={}:rh={}:bh={}", num(high), num(-high), num(mid), num(-mid), num(high), num(-high)));
        }
    }
    filters
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
    if inputs.len() == 1 && edit.transitions.is_empty() && edit.overlays.is_empty() && edit.segments.iter().all(Segment::is_plain) {
        return (filter_graph(&edit.segments, inputs[0].has_audio, quality, video_out, audio_out), inputs[0].has_audio);
    }
    let has_audio = output_has_audio(edit, inputs);
    let segments = &edit.segments;
    let count = segments.len();
    let lengths: Vec<f64> = segments.iter().map(segment_length).collect();
    let mut fade_in = vec![0.0; count];
    let mut fade_out = vec![0.0; count];
    let mut dissolve: Vec<Option<(f64, &str)>> = vec![None; count];
    for joint in &edit.transitions {
        match joint.kind.xfade() {
            None => {
                fade_out[joint.after] = joint.duration / 2.0;
                fade_in[joint.after + 1] = joint.duration / 2.0;
            }
            Some(xfade) => dissolve[joint.after] = Some((joint.duration, xfade)),
        }
    }
    let (width, height, fps) = (frame.width, frame.height, &frame.fps);
    let fit = format!("scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p,settb=AVTB");
    let mut lines = Vec::new();
    for (index, segment) in segments.iter().enumerate() {
        let (start, end) = (num(segment.start), num(segment.end));
        let mut video = segment_video(segment, &fit, frame, &edit.luts);
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
            let (source, mut audio) = if inputs[segment.media].has_audio && segment.freeze.is_none() {
                let mut audio = vec![format!("atrim=start={start}:end={end}"), "asetpts=PTS-STARTPTS".to_owned()];
                if segment.reverse {
                    audio.push("areverse".to_owned());
                }
                audio.extend(atempo_chain(segment.speed));
                if segment.volume != 1.0 {
                    audio.push(format!("volume={}", num(segment.volume)));
                }
                if segment.fade_in > 0.0 {
                    audio.push(format!("afade=t=in:st=0:d={}", num(segment.fade_in.min(lengths[index]))));
                }
                if segment.fade_out > 0.0 {
                    let fade = segment.fade_out.min(lengths[index]);
                    audio.push(format!("afade=t=out:st={}:d={}", num(lengths[index] - fade), num(fade)));
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
        let (duration, xfade) = dissolve[runs[run - 1].1].unwrap_or((MIN_TRANSITION, "fade"));
        let last = run + 1 == runs.len();
        let (video, audio) = if last { (joined_video.to_owned(), joined_audio.to_owned()) } else { (format!("vx{run}"), format!("ax{run}")) };
        lines.push(format!("[{}][{}]xfade=transition={xfade}:duration={}:offset={}[{video}]", current.0, labels[run].0, num(duration), num(length - duration)));
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
        let mut look = vec![overlay_look(overlay, width, height)];
        look.extend(overlay_motion(overlay, width, shown));
        lines.push(format!("[{}:v]trim=start={}:end={},setpts=PTS-STARTPTS+{at}/TB,{}[ov{index}]",
            overlay.media, num(overlay.start), num(overlay.start + shown), look.join(",")));
        let (x, y, moving) = overlay_place(overlay, width, height, shown);
        let eval = if moving { ":eval=frame" } else { "" };
        lines.push(format!("[{video}][ov{index}]overlay=x={x}:y={y}{eval}:enable='between(t,{at},{until})':eof_action=pass[{out}]"));
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

/// An overlay's picture: scaled to its width, inside its frame, with
/// rounded corners and its opacity (Issue #520); a plain one is only scaled.
fn overlay_look(overlay: &Overlay, width: u32, height: u32) -> String {
    let outer = even(overlay.w.max(overlay.to.map_or(0.0, |to| to.w)) * f64::from(width));
    let border = match &overlay.border_color {
        Some(_) if overlay.border > 0.0 => ((overlay.border * f64::from(width.min(height)) / 1080.0).round() as u32).clamp(1, outer / 4),
        _ => 0,
    };
    let ellipse = overlay.shape.as_deref() == Some("ellipse");
    let mut filters = vec![format!("scale={}:-2", outer - 2 * border)];
    if border == 0 && overlay.radius == 0.0 && overlay.opacity == 1.0 && !ellipse {
        filters.push("setsar=1".to_owned());
        return filters.join(",");
    }
    filters.push("format=yuva420p".to_owned());
    if let (Some(color), true) = (&overlay.border_color, border > 0) {
        filters.push(format!("pad=iw+{0}:ih+{0}:{border}:{border}:color=0x{1}FF", 2 * border, &color[1..]));
    }
    filters.push("setsar=1".to_owned());
    let opacity = if overlay.opacity == 1.0 { String::new() } else { format!("{}*", num(overlay.opacity)) };
    if ellipse {
        // Inside the box's ellipse; with a frame, the ring between it and the
        // ellipse `border` px inside is drawn in the frame colour (sizes per plane).
        let inside = |inset: &str| format!("lte(hypot((X-W/2+0.5)/(W/2{inset}),(Y-H/2+0.5)/(H/2{inset})),1)");
        let (lum, cb, cr) = match (&overlay.border_color, border > 0) {
            (Some(color), true) => {
                let (y, u, v) = yuv(color);
                let ring = inside(&format!("-{border}*W/{outer}"));
                (format!("if({ring},lum(X,Y),{y})"), format!("if({ring},cb(X,Y),{u})"), format!("if({ring},cr(X,Y),{v})"))
            }
            _ => ("lum(X,Y)".to_owned(), "cb(X,Y)".to_owned(), "cr(X,Y)".to_owned()),
        };
        filters.push(format!("geq=lum='{lum}':cb='{cb}':cr='{cr}':a='{opacity}alpha(X,Y)*{}'", inside("")));
    } else if overlay.radius > 0.0 {
        let r = format!("({}*min(W,H))", num(overlay.radius));
        filters.push(format!("geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='{opacity}alpha(X,Y)*lte(hypot(max(max({r}-X,X-W+1+{r}),0),max(max({r}-Y,Y-H+1+{r}),0)),{r})'"));
    } else if overlay.opacity != 1.0 {
        filters.push(format!("lut=a='val*{}'", num(overlay.opacity)));
    }
    filters.join(",")
}

/// A #RRGGBB colour as limited-range BT.601 Y, Cb, Cr (what yuva420p holds).
fn yuv(color: &str) -> (u32, u32, u32) {
    let part = |at: usize| f64::from(u8::from_str_radix(&color[at..at + 2], 16).unwrap_or(0)) / 255.0;
    let (r, g, b) = (part(1), part(3), part(5));
    let y = 16.0 + 65.481 * r + 128.553 * g + 24.966 * b;
    let u = 128.0 - 37.797 * r - 74.203 * g + 112.0 * b;
    let v = 128.0 + 112.0 * r - 93.786 * g - 18.214 * b;
    (y.round() as u32, u.round() as u32, v.round() as u32)
}

/// `from` to `to` evenly over [at, at + span) of output time, as an FFmpeg expression.
fn glide(from: f64, to: f64, at: f64, span: f64) -> String {
    format!("{}+{}*clip((t-{})/{},0,1)", num(from), num(to - from), num(at), num(span.max(0.001)))
}

/// Fades, then (Chuyển động) the size changing over its shown time; the
/// overlay filter moves it (`overlay_place`).
fn overlay_motion(overlay: &Overlay, width: u32, shown: f64) -> Vec<String> {
    let mut filters = Vec::new();
    let fade_in = overlay.fade_in.min(shown / 2.0);
    let fade_out = overlay.fade_out.min(shown / 2.0);
    if fade_in > 0.0 || fade_out > 0.0 {
        filters.push("format=yuva420p".to_owned());
    }
    if fade_in > 0.0 {
        filters.push(format!("fade=t=in:st={}:d={}:alpha=1", num(overlay.at), num(fade_in)));
    }
    if fade_out > 0.0 {
        filters.push(format!("fade=t=out:st={}:d={}:alpha=1", num(overlay.at + shown - fade_out), num(fade_out)));
    }
    if let Some(to) = overlay.to.filter(|to| to.w != overlay.w) {
        let size = glide(overlay.w * f64::from(width), to.w * f64::from(width), overlay.at, shown);
        filters.push(format!("scale=w='trunc(({size})/2)*2':h=-2:eval=frame"));
    }
    filters
}

/// x and y of the overlay filter: fixed, or gliding to `to`.
fn overlay_place(overlay: &Overlay, width: u32, height: u32, shown: f64) -> (String, String, bool) {
    let (w, h) = (f64::from(width), f64::from(height));
    match overlay.to.filter(|to| (to.x, to.y, to.w) != (overlay.x, overlay.y, overlay.w)) {
        Some(to) => (format!("'{}'", glide(overlay.x * w, to.x * w, overlay.at, shown)), format!("'{}'", glide(overlay.y * h, to.y * h, overlay.at, shown)), true),
        None => (((overlay.x * w).round() as i64).to_string(), ((overlay.y * h).round() as i64).to_string(), false),
    }
}

/// Stretches where the original sound dips: overlapping (or nearly touching)
/// ducking sounds merge, taking the deepest dip, so the eased edges never
/// stack.
fn duck_spans(sounds: &[Sound], total: f64) -> Vec<(f64, f64, f64)> {
    let mut spans: Vec<(f64, f64, f64)> = sounds.iter().filter(|sound| sound.duck_db > 0.0)
        .map(|sound| (sound.at, sound.at + sound.played(total), sound.duck_db))
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
fn export_script(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str, mix: &Mix, finish: &Finish) -> (String, bool) {
    let steps: Vec<&String> = finish.texts.iter().chain(finish.reframe.iter()).collect();
    let video_out = if steps.is_empty() { "v" } else { "vt" };
    let audio_out = if finish.normalize { "an" } else { "a" };
    let (mut graph, has_audio) = mixed_script(edit, inputs, frame, quality, mix, video_out, audio_out);
    let mut label = video_out.to_owned();
    for (index, filter) in steps.iter().enumerate() {
        let out = if index + 1 == steps.len() { "v".to_owned() } else { format!("vf{index}") };
        graph.push_str(&format!(";\n[{label}]{filter}[{out}]"));
        label = out;
    }
    if finish.normalize && has_audio {
        graph.push_str(&format!(";\n[an]{LOUDNESS}[a]"));
    }
    if finish.audio_only {
        graph.push_str(";\n[v]nullsink");
    }
    (graph, has_audio)
}

/// The picture (ending as `[video_out]`) and the mixed sound (`[audio_out]`).
fn mixed_script(edit: &Edit, inputs: &[Input], frame: &Frame, quality: &str, mix: &Mix, video_out: &str, audio_out: &str) -> (String, bool) {
    if mix.sounds.is_empty() && mix.main_volume == 1.0 && mix.main_clean.is_plain() {
        return video_graph(edit, inputs, frame, quality, video_out, audio_out);
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
    let mut main = if has_main { mix.main_clean.filters() } else { Vec::new() };
    if mix.main_volume != 1.0 {
        main.push(format!("volume={}", num(mix.main_volume)));
    }
    main.extend(duck_spans(&mix.sounds, total).into_iter().map(|(start, end, db)| duck_filter(start, end, db)));
    if mix.sounds.is_empty() {
        main.push(LIMITER.to_owned());
        lines.push(format!("[am]{}[{audio_out}]", main.join(",")));
        return (lines.join(";\n"), true);
    }
    let mut pads = if main.is_empty() {
        "[am]".to_owned()
    } else {
        lines.push(format!("[am]{}[amx]", main.join(",")));
        "[amx]".to_owned()
    };
    for (index, sound) in mix.sounds.iter().enumerate() {
        let shown = sound.played(total);
        let part = if sound.repeat { sound.end - sound.start } else { shown };
        let mut filters = vec![format!("atrim=start={}:end={}", num(sound.start), num(sound.start + part)), "asetpts=PTS-STARTPTS".to_owned(),
            AUDIO_FORMAT.to_owned()];
        filters.extend(sound.clean.filters());
        if sound.repeat && shown > part {
            filters.push(format!("aloop=loop=-1:size={}", (part * 48000.0).round() as u64));
            filters.push(format!("atrim=duration={}", num(shown)));
        }
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
    lines.push(format!("{pads}amix=inputs={}:duration=first:normalize=0,{LIMITER}[{audio_out}]", mix.sounds.len() + 1));
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

/// Chỉ âm thanh: the graph's sound as AAC in an .m4a.
fn audio_args(inputs: &[PathBuf], script: &Path, output: &Path) -> Vec<OsString> {
    let mut args = ffmpeg_args(inputs, script, true, "", 0, "", output);
    let at = args.iter().position(|arg| arg == "-map").unwrap_or(args.len());
    args.truncate(at);
    args.extend(["-map", "[a]", "-vn", "-c:a", "aac", "-b:a", "192k", "-sn", "-dn", "-map_chapters", "-1", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", "-f", "ipod"].map(OsString::from));
    args.push(output.into());
    args
}

fn ffmpeg_args(inputs: &[PathBuf], script: &Path, has_audio: bool, encoder: &str, bitrate: u64, fps: &str, output: &Path) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-v", "error", "-y"].map(OsString::from).to_vec();
    for input in inputs {
        if crate::is_still(input) {
            // A picture repeats as a video; each overlay trims the part it shows.
            args.extend(["-loop", "1", "-framerate", "30", "-t"].map(OsString::from));
            args.push(num(STILL_SECONDS).into());
        }
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

/// A work folder still in place, checked before every write made while a
/// render runs: a file's draft folder still confined to the app's store
/// (`confined_work`); any other neither a link nor inside one.
fn unlinked(work: &Path) -> bool {
    if let Some(store) = work.parent().filter(|store| store.file_name().is_some_and(|name| name == FILE_STORE)) {
        return confined_work(store, work).is_ok();
    }
    [Some(work), work.parent()].into_iter().flatten()
        .all(|dir| fs::symlink_metadata(dir).is_ok_and(|metadata| metadata.is_dir() && !is_link(&metadata)))
}

/// `unlinked`, as an error to stop on.
fn guarded(work: &Path) -> Result<(), String> {
    if unlinked(work) { Ok(()) } else { Err(fail("Thư mục bản nháp của DubFlow không an toàn (là liên kết); hãy xóa nó rồi thử lại")) }
}

fn write_status(work: &Path, state: &str, percent: f64, message: &str, output: Option<&Path>) {
    if !unlinked(work) {
        return;
    }
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
    if !unlinked(work) {
        return;
    }
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
    /// Bóng đổ (Issue #520): offset in pixels on a 1080 frame; drawn when there is no box.
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    shadow: f64,
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
            && within(text.box_opacity, 0.0, 1.0) && within(text.anim_duration, 0.0, MAX_ANIM) && within(text.shadow, 0.0, MAX_TEXT_SHADOW);
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
            None => (1, "&H64000000".to_owned(), text.shadow * scale),
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

/// At most this many LUT files in one edit.
const MAX_LUTS: usize = 8;
/// A .cube file is text; bigger ones are not LUTs a user picked on purpose.
const MAX_LUT_BYTES: u64 = 64 * 1024 * 1024;

fn is_lut_path(path: &str) -> bool {
    let file = Path::new(path);
    file.is_absolute() && file.extension().and_then(|extension| extension.to_str()).is_some_and(|extension| extension.eq_ignore_ascii_case("cube"))
}

/// An existing .cube file the user picked, or why it cannot be used.
pub(crate) fn lut_file(path: &str) -> Result<PathBuf, String> {
    if !is_lut_path(path) {
        return Err(fail("LUT phải là file .cube trên máy"));
    }
    let file = Path::new(path).canonicalize().map_err(|_| fail("Không tìm thấy file LUT"))?;
    let size = std::fs::metadata(&file).map(|meta| meta.len()).unwrap_or(0);
    if !file.is_file() || !is_lut_path(&file.to_string_lossy()) || size == 0 || size > MAX_LUT_BYTES {
        return Err(fail("File LUT không hợp lệ"));
    }
    Ok(file)
}

/// The colour lookup of a .cube file, after the segment's colour and before the fit into the frame.
fn lut_filter(path: &str) -> String {
    format!("lut3d=file={}", filter_path(path))
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

/// Lines a video without a re-runnable dubbing job burns in at export (Issue #523).
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BurnSubtitles {
    style: SubtitleStyle,
    lines: Vec<Cue>,
}

const BURN_FILE: &str = "subtitles.ass";

/// The lines as the worker burns subtitles (engine/dubflow/subtitle/style.py and
/// production_job._write_subtitles): PlayRes 1920x1080, one Default style for
/// the look (a box is BorderStyle 4 padded by the Shadow), a dragged block
/// placed by its centre, text literal. Times are the main video's own.
fn burn_ass(burn: &BurnSubtitles) -> Result<String, String> {
    let style = &burn.style;
    style.validate()?;
    if burn.lines.len() > MAX_CUES || burn.lines.iter()
        .any(|line| line.end_ms <= line.start_ms || line.text.trim().is_empty() || line.text.chars().count() > MAX_CUE_TEXT) {
        return Err(fail("Phụ đề không hợp lệ"));
    }
    let font_size = (48.0 * style.size).round().max(1.0);
    let (back, border, shadow) = match &style.box_color {
        Some(color) => (ass_color(color, ((1.0 - style.box_opacity) * 255.0).round() as u8), 4, (font_size * 0.2).round().max(2.0)),
        None => ("&H64000000".to_owned(), 1, 0.0),
    };
    let align = match style.align.as_str() { "top" => 8, "middle" => 5, _ => 2 };
    let prefix = match (style.x, style.y) {
        (Some(x), Some(y)) => format!("{{\\an5\\pos({},{})}}", (x * 1920.0).round() as i64, (y * 1080.0).round() as i64),
        _ => String::new(),
    };
    let mut script = format!("[Script Info]\n; DubFlow: Phụ đề\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\
ScaledBorderAndShadow: yes\nYCbCr Matrix: None\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\
Style: Default,{},{},{},&H000000FF,{},{back},{},0,0,0,100,100,0,0,{border},{},{},{align},40,40,48,1\n\n[Events]\n\
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
        style.font, num(font_size), ass_color(&style.primary, 0), ass_color(&style.outline, 0), if style.bold { -1 } else { 0 },
        num(style.outline_width), num(shadow));
    let mut lines: Vec<&Cue> = burn.lines.iter().collect();
    lines.sort_by_key(|line| (line.start_ms, line.end_ms));
    for line in lines {
        script.push_str(&format!("Dialogue: 0,{},{},Default,,0,0,0,,{prefix}{}\n", ass_time(line.start_ms as f64 / 1000.0),
            ass_time(line.end_ms as f64 / 1000.0), ass_text(line.text.trim())));
    }
    Ok(script)
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

/// The Che filter of an edited video (Issue #510): the drawn regions and the
/// fixed one, in the chosen style (stretch when none was chosen).
fn cover_filter(cover: &CoverChoice) -> Result<Option<String>, String> {
    cover.validate()?;
    let stretch = CoverStyle { mode: "stretch".into(), direction: default_direction(), strip_px: default_strip(), color: default_cover_color(),
        block: default_block(), feather: 0 };
    let style = cover.style.as_ref().unwrap_or(&stretch);
    let mut areas: Vec<crate::cover_graph::Area> = cover.regions.iter().map(|region| crate::cover_graph::Area {
        x: region.x, y: region.y, w: region.w, h: region.h, window: region.start_ms.zip(region.end_ms),
    }).collect();
    if let Some(fixed) = &cover.fixed {
        areas.push(crate::cover_graph::Area { x: fixed.x, y: fixed.y, w: fixed.w, h: fixed.h, window: None });
    }
    let look = crate::cover_graph::Look { mode: &style.mode, direction: &style.direction, strip_px: style.strip_px, color: &style.color,
        block: style.block, feather: style.feather };
    Ok(crate::cover_graph::cover_filter(&areas, &look))
}

/// The export graph reading the main video through the cover: it runs once,
/// on the main video's own clock, and every use of `[0:v]` takes a copy.
fn with_cover(graph: &str, filter: &str) -> String {
    let uses = graph.matches("[0:v]").count();
    if uses == 0 {
        return graph.to_owned();
    }
    let mut lines = vec![format!("[0:v]{filter}[mcv]")];
    let mut rest = String::with_capacity(graph.len() + uses * 4);
    if uses == 1 {
        rest = graph.replacen("[0:v]", "[mcv]", 1);
    } else {
        lines.push(format!("[mcv]split={uses}{}", (0..uses).map(|index| format!("[mc{index}]")).collect::<String>()));
        let mut parts = graph.split("[0:v]");
        rest.push_str(parts.next().unwrap_or_default());
        for (index, part) in parts.enumerate() {
            rest.push_str(&format!("[mc{index}]"));
            rest.push_str(part);
        }
    }
    lines.push(rest);
    lines.join(";\n")
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
    let dir = crate::job_review_dir(&output_path)?;
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
    let dir = crate::job_review_dir(&output_path)?;
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
pub async fn edit_project_load(output_path: String, kind: String) -> Result<Value, String> {
    let target = edit_target(&output_path, &kind)?;
    let facts = probe(&target.main)?;
    let mut project_path = target.work.join("project.json");
    if target.kind == Kind::File && kind != "file" && !project_path.is_file() {
        // A run made before DubFlow kept its records: its earlier draft is
        // read from beside it once; saving puts it in the app's store.
        if let Some(earlier) = output_file(&output_path).ok().and_then(|video| shaped_target(&video)) {
            project_path = earlier.work.join("project.json");
        }
    }
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
    let file = crate::overlay_media(&path)?;
    let facts = probe(&file)?;
    let title = file.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    Ok(json!({"path": path, "title": title, "duration": facts.duration, "width": facts.width, "height": facts.height,
        "fps": fps_value(&facts.fps), "has_audio": facts.has_audio, "size_bytes": facts.size}))
}

/// Xem thử LUT (Issue #531): the frame of `path` at `time` seconds through
/// the LUT, as a PNG at most 640 px wide, for the panel's before/after.
#[tauri::command]
pub async fn edit_lut_frame(path: String, time: f64, lut: String) -> Result<tauri::ipc::Response, String> {
    let file = crate::overlay_media(&path)?;
    let lut = crate::display_path(&lut_file(&lut)?);
    if !time.is_finite() || time < 0.0 {
        return Err(fail("Thời điểm không hợp lệ"));
    }
    let ffmpeg = media_tool("ffmpeg.exe")?;
    let png = tauri::async_runtime::spawn_blocking(move || -> Result<Vec<u8>, String> {
        let mut command = Command::new(ffmpeg);
        command.args(["-nostdin", "-v", "error", "-ss", &num(time), "-i"]).arg(&file)
            .args(["-frames:v", "1", "-vf", &format!("scale='min(640,iw)':-2,{}", lut_filter(&lut)), "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
            .stdin(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let output = command.output().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
        if !output.status.success() || output.stdout.is_empty() {
            return Err(fail("Không áp được LUT này (file .cube không đọc được)"));
        }
        Ok(output.stdout)
    }).await.map_err(|_| fail("Không xem thử được LUT"))??;
    Ok(tauri::ipc::Response::new(png))
}

/// A recording is at most this big (about an hour of Opus).
const MAX_RECORDING_BYTES: usize = 200 * 1024 * 1024;
/// Recordings of an edit live in its draft folder.
const RECORDINGS_DIR: &str = "thu-am";

/// `%XX` escapes (encodeURIComponent) back to text.
fn percent_decoded(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] == b'%' {
            let hex = std::str::from_utf8(bytes.get(at + 1..at + 3)?).ok()?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            at += 3;
        } else {
            out.push(bytes[at]);
            at += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// Thu giọng (Issue #533): the voice recorded in the editor (WebM/Opus or
/// MP4/AAC bytes in the body; the edit's `x-output-path` and `x-kind`
/// headers, URI-encoded) saved as an .m4a in the edit's draft folder.
/// Returns its path; the preview may play it.
#[tauri::command]
pub async fn edit_save_recording(request: tauri::ipc::Request<'_>) -> Result<String, String> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else {
        return Err(fail("Bản thu không hợp lệ"));
    };
    if bytes.len() < 64 || bytes.len() > MAX_RECORDING_BYTES {
        return Err(fail("Bản thu trống hoặc quá dài"));
    }
    let header = |name: &str| request.headers().get(name).and_then(|value| value.to_str().ok()).and_then(percent_decoded);
    let (Some(output_path), Some(kind)) = (header("x-output-path"), header("x-kind")) else {
        return Err(fail("Không biết bản thu thuộc video nào"));
    };
    let target = edit_target(&output_path, &kind)?;
    let ffmpeg = media_tool("ffmpeg.exe")?;
    let bytes = bytes.clone();
    tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        guarded(&target.work)?;
        let dir = target.work.join(RECORDINGS_DIR);
        fs::create_dir_all(&dir).map_err(|_| fail("Không tạo được thư mục bản thu"))?;
        guarded(&dir)?;
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_millis());
        let raw = dir.join(format!("giong-{stamp}.rec"));
        let voice = dir.join(format!("giong-{stamp}.m4a"));
        fs::write(&raw, &bytes).map_err(|_| fail("Không lưu được bản thu"))?;
        let mut command = Command::new(ffmpeg);
        command.args(["-nostdin", "-v", "error", "-y", "-i"]).arg(&raw)
            .args(["-vn", "-ac", "1", "-ar", "48000", "-c:a", "aac", "-b:a", "160k", "-f", "ipod"]).arg(&voice)
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let status = command.status();
        let _ = fs::remove_file(&raw);
        if !status.is_ok_and(|status| status.success()) || !voice.is_file() {
            let _ = fs::remove_file(&voice);
            return Err(fail("Không đọc được bản thu từ micro"));
        }
        crate::sfx::allow_files(&[voice.canonicalize().unwrap_or_else(|_| voice.clone())]);
        Ok(display_path(&voice))
    }).await.map_err(|_| fail("Không lưu được bản thu"))?
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

/// Waveform buckets per second of `edit_audio_peaks`.
pub const PEAKS_PER_SECOND: u32 = 50;
/// The waveform is drawn from this sample rate (mono).
const PEAK_RATE: u32 = 8000;
/// Waveforms longer than this are cut (about 3 hours).
const MAX_PEAKS: usize = 540_000;

/// Loudness of each bucket of 16-bit mono samples: its peak on a -48..0 dBFS
/// scale as 0–255, so speech and quiet parts both show.
pub fn bucket_level(peak: i32) -> u8 {
    if peak <= 0 {
        return 0;
    }
    let db = 20.0 * (f64::from(peak) / 32768.0).log10();
    (((db + 48.0) / 48.0).clamp(0.0, 1.0) * 255.0).round() as u8
}

/// Bucket levels of raw s16le mono `pcm` at `per_bucket` samples a bucket;
/// `carry` holds the running peak and sample count across calls.
pub fn push_peaks(pcm: &[u8], per_bucket: usize, carry: &mut (i32, usize), out: &mut Vec<u8>) {
    for pair in pcm.chunks_exact(2) {
        let sample = i32::from(i16::from_le_bytes([pair[0], pair[1]])).abs();
        carry.0 = carry.0.max(sample);
        carry.1 += 1;
        if carry.1 == per_bucket {
            if out.len() < MAX_PEAKS {
                out.push(bucket_level(carry.0));
            }
            *carry = (0, 0);
        }
    }
}

/// The waveform of a video's sound for the timeline: one byte per 1/50 s
/// (see `bucket_level`), empty when it has no sound.
#[tauri::command]
pub async fn edit_audio_peaks(path: String) -> Result<tauri::ipc::Response, String> {
    let file = crate::playable_media(&path)?;
    let ffmpeg = media_tool("ffmpeg.exe")?;
    let peaks = tauri::async_runtime::spawn_blocking(move || -> Result<Vec<u8>, String> {
        use std::io::Read;
        let mut command = Command::new(ffmpeg);
        command.args(["-nostdin", "-v", "error", "-i"]).arg(&file)
            .args(["-vn", "-ac", "1", "-ar", &PEAK_RATE.to_string(), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let mut child = command.spawn().map_err(|error| fail(format!("Không chạy được FFmpeg: {error}")))?;
        let mut stdout = child.stdout.take().ok_or_else(|| fail("Không đọc được âm thanh"))?;
        let per_bucket = (PEAK_RATE / PEAKS_PER_SECOND) as usize;
        let mut carry = (0, 0);
        let mut out = Vec::new();
        let mut buffer = vec![0u8; 64 * 1024];
        let mut odd: Option<u8> = None;
        loop {
            let read = stdout.read(&mut buffer).map_err(|_| fail("Không đọc được âm thanh"))?;
            if read == 0 {
                break;
            }
            let mut chunk = Vec::with_capacity(read + 1);
            if let Some(byte) = odd.take() {
                chunk.push(byte);
            }
            chunk.extend_from_slice(&buffer[..read]);
            if chunk.len() % 2 == 1 {
                odd = chunk.pop();
            }
            push_peaks(&chunk, per_bucket, &mut carry, &mut out);
            if out.len() >= MAX_PEAKS {
                let _ = child.kill();
                break;
            }
        }
        let _ = child.wait();
        if carry.1 > 0 && out.len() < MAX_PEAKS {
            out.push(bucket_level(carry.0));
        }
        Ok(out)
    }).await.map_err(|error| fail(error.to_string()))??;
    Ok(tauri::ipc::Response::new(peaks))
}

/// Sizes of the queue's finished videos for the Chỉnh sửa cards (0 when a file is gone).
#[tauri::command]
pub fn edit_file_sizes(paths: Vec<String>) -> Vec<u64> {
    paths.iter().take(2000)
        .map(|path| fs::metadata(path).ok().filter(fs::Metadata::is_file).map_or(0, |metadata| metadata.len()))
        .collect()
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
pub fn edit_project_save(output_path: String, kind: String, project: Value) -> Result<(), String> {
    let bytes = check_project(&project)?;
    save_draft(&edit_target(&output_path, &kind)?, &bytes)
}

fn save_draft(target: &Target, bytes: &[u8]) -> Result<(), String> {
    target.prepare()?;
    write_atomic(&target.work.join("project.json"), bytes).map_err(|_| fail("Không lưu được bản nháp chỉnh sửa"))
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
    main_denoise: bool,
    main_eq: Option<EqPreset>,
    /// The texts as requested, for exports.json.
    texts: Vec<TextClip>,
    /// Chỉ âm thanh: an .m4a, kept out of exports.json (it is no edited video).
    audio_only: bool,
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
    let log = unlinked(&job.work).then(|| fs::File::create(&log_path).ok()).flatten().map_or_else(Stdio::null, Stdio::from);
    let mut command = Command::new(&job.ffmpeg);
    let args = if job.audio_only { audio_args(&job.inputs, &job.script, &partial) }
        else { ffmpeg_args(&job.inputs, &job.script, job.has_audio, encoder, job.bitrate, &job.fps, &partial) };
    command.args(args)
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
        if job.audio_only {
            break; // No video encoder to fall back from.
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
        match unique_target(&job.folder, &job.name, if job.audio_only { "m4a" } else { "mp4" }) { Ok(target) => target, Err(error) => {
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
    if job.audio_only {
        write_status(&job.work, "COMPLETED", 100.0, "Đã xuất âm thanh.", Some(&target));
        return;
    }
    append_export(&job.work, json!({"path": display_path(&target), "srt_path": srt, "created": unix_now(),
        "duration": (job.seconds * 1000.0).round() / 1000.0, "quality": job.quality, "segments": job.edit.segments,
        "media": job.media, "luts": job.edit.luts, "transitions": job.edit.transitions, "overlays": job.edit.overlays, "audio": job.audio, "main_volume": job.main_volume, "main_denoise": job.main_denoise, "main_eq": job.main_eq,
        "texts": job.texts}));
    write_status(&job.work, "COMPLETED", 100.0, "Đã xuất video.", Some(&target));
}

fn start_export(request: ExportRequest) -> Result<Value, String> {
    let target = edit_target(&request.output_path, &request.kind)?;
    let main = target.main.clone();
    let facts = probe(&main)?;
    if request.media.len() + 1 > MAX_MEDIA {
        return Err(fail("Một bản sửa dùng tối đa 20 video"));
    }
    let mut inputs = vec![main];
    let mut probes = vec![facts.clone()];
    for path in &request.media {
        let file = crate::overlay_media(path)?;
        probes.push(probe(&file)?);
        inputs.push(file);
    }
    let durations: Vec<f64> = probes.iter().map(|probe| probe.duration).collect();
    let mut edit = validate(&request, &durations)?;
    // LUT màu: the files as they are on disk (a link may point elsewhere).
    for lut in &mut edit.luts {
        *lut = crate::display_path(&lut_file(lut)?);
    }
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
    let mut mix = validate_mix(&request.audio, &files, &sound_seconds, request.main_volume, total)?;
    mix.main_clean = Clean { denoise: request.main_denoise, eq: request.main_eq };
    let texts = validate_texts(&request.texts, total)?;
    if request.fps.is_some_and(|fps| !EXPORT_FPS.contains(&fps)) || request.bitrate.as_deref().is_some_and(|rate| rate != "low" && rate != "high")
        || request.aspect.as_deref().is_some_and(|aspect| !ASPECTS.contains(&aspect))
        || request.fill.as_deref().is_some_and(|fill| fill != "black" && fill != "blur") {
        return Err(fail("Tùy chọn xuất không hợp lệ"));
    }
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
    target.prepare()?;
    let lease = crate::work_lease::take(&work, BUSY)?;
    let cancel = work.join("cancel");
    if cancel.exists() {
        guarded(&work)?;
        fs::remove_file(cancel).map_err(|_| fail("Không xóa được yêu cầu hủy cũ"))?;
    }
    let target = unique_target(&folder, &name, if request.audio_only { "m4a" } else { "mp4" })?;
    let script = work.join("filter.txt");
    let streams: Vec<Input> = probes.iter().map(|probe| Input { has_audio: probe.has_audio }).collect();
    let fps = request.fps.map_or_else(|| facts.fps.clone(), |fps| format!("{fps}/1"));
    let frame = Frame { width: facts.width, height: facts.height, fps: fps.clone() };
    let (width, height) = scaled_size(facts.width, facts.height, &request.quality);
    let (out_width, out_height) = request.aspect.as_deref().map_or((width, height), |aspect| aspect_size(width, height, aspect));
    // Chữ: the text layer is drawn on the exported frame, after any scaling.
    let layer = if texts.is_empty() {
        None
    } else {
        let file = work.join(TEXTS_FILE);
        guarded(&work)?;
        fs::write(&file, texts_ass(&texts, width, height)).map_err(|_| fail("Không ghi được lớp chữ"))?;
        Some(subtitles_filter(&display_path(&file)))
    };
    let reframe = ((out_width, out_height) != (width, height))
        .then(|| reframe_filter(out_width, out_height, request.fill.as_deref() == Some("blur")));
    let finish = Finish { texts: layer, reframe, normalize: request.normalize, audio_only: request.audio_only };
    let (text, has_audio) = export_script(&edit, &streams, &frame, &request.quality, &mix, &finish);
    if request.audio_only && !has_audio {
        return Err(fail("Bản sửa này không có âm thanh để xuất"));
    }
    let rate = match request.bitrate.as_deref() { Some("low") => 0.6, Some("high") => 1.6, _ => 1.0 };
    let bitrate = (video_bitrate(out_width, out_height, &fps) as f64 * rate).round() as u64;
    // Che, then Phụ đề, on the main video before it is cut: both follow its own time.
    let mut prefilter: Vec<String> = request.cover.as_ref().map(cover_filter).transpose()?.flatten().into_iter().collect();
    if let Some(burn) = request.burn.as_ref().filter(|burn| !burn.lines.is_empty()) {
        let file = work.join(BURN_FILE);
        let body = burn_ass(burn)?;
        guarded(&work)?;
        fs::write(&file, body).map_err(|_| fail("Không ghi được phụ đề"))?;
        prefilter.push(subtitles_filter(&display_path(&file)));
    }
    let text = if prefilter.is_empty() { text } else { with_cover(&text, &prefilter.join(",")) };
    inputs.extend(sound_inputs);
    guarded(&work)?;
    fs::write(&script, text).map_err(|_| fail("Không ghi được bộ lọc xuất video"))?;
    let job = ExportJob {
        work: work.clone(), inputs, media: request.media, script, folder, name, target: target.clone(),
        srt: (!request.cues.is_empty() && !request.audio_only).then(|| format_srt(&request.cues)),
        seconds: output_seconds(&edit.segments, &edit.transitions), has_audio, bitrate, fps, ffmpeg, audio_only: request.audio_only,
        quality: request.quality, edit, audio: request.audio, main_volume: request.main_volume, texts: request.texts,
        main_denoise: request.main_denoise, main_eq: request.main_eq,
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
pub fn edit_status(output_path: String, kind: String) -> Result<Value, String> {
    let target = edit_target(&output_path, &kind)?;
    target.confined()?;
    let work = target.work.clone();
    let mut status = fs::read(work.join("status.json")).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).filter(Value::is_object);
    let ours = RUNNING.lock().is_ok_and(|running| running.contains_key(&work));
    if let Some(value) = status.as_mut().filter(|value| value["state"] == "RUNNING" && !ours) {
        // A free lease means no window is rendering: the app closed mid-export.
        if let Some(lease) = target.confined().ok().and_then(|()| crate::work_lease::take(&work, BUSY).ok()) {
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
pub fn edit_cancel(output_path: String, kind: String) -> Result<(), String> {
    let target = edit_target(&output_path, &kind)?;
    let work = target.work.clone();
    if work.is_dir() && target.confined().is_ok() {
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
        Segment { media: 0, start, end, speed, volume, ..Default::default() }
    }

    fn clip(media: usize, start: f64, end: f64) -> Segment {
        Segment { media, start, end, speed: 1.0, volume: 1.0, ..Default::default() }
    }

    fn joint(after: usize, kind: TransitionKind, duration: f64) -> Transition {
        Transition { after, kind, duration }
    }

    fn request(segments: Vec<Segment>) -> ExportRequest {
        ExportRequest { output_path: "unused".into(), kind: "file".into(), media: vec![], segments, transitions: vec![], overlays: vec![], audio: vec![], main_volume: 1.0,
            texts: vec![], quality: "source".into(),
            folder: "unused".into(), file_name: "Phim (sửa 1).mp4".into(), cues: vec![Cue { start_ms: 0, end_ms: 1000, text: "Xin chào".into() }], cover: None, normalize: false, burn: None, fps: None, bitrate: None, aspect: None, fill: None, audio_only: false, luts: vec![], main_denoise: false, main_eq: None }
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
    fn reads_the_sizes_of_queue_videos() {
        let folder = std::env::temp_dir().join(format!("dubflow-edit-sizes-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        let video = folder.join("a.mp4");
        fs::write(&video, b"12345").unwrap();
        let paths = vec![video.display().to_string(), folder.join("gone.mp4").display().to_string(), folder.display().to_string()];
        assert_eq!(edit_file_sizes(paths), vec![5, 0, 0]);
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn writes_the_graph_for_an_inserted_clip_with_a_dissolve() {
        let inputs = [Input { has_audio: true }, Input { has_audio: true }];
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 2.0, 7.0), segment(10.0, 20.0, 1.5, 0.5)],
            transitions: vec![joint(0, TransitionKind::Dissolve, 1.0)], overlays: vec![], ..Default::default() };
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
            transitions: vec![joint(0, TransitionKind::Fade, 0.8)], overlays: vec![], ..Default::default() };
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
                Overlay { media: 1, at: 3.0, start: 1.0, end: 6.0, x: 0.62, y: 0.03, w: 0.35, volume: 0.8, ..Default::default() },
                // Runs past the end of the output: cut there; a silent clip adds no sound.
                Overlay { media: 2, at: 12.0, start: 0.0, end: 4.0, x: 0.0, y: 0.0, w: 0.2, volume: 1.0, ..Default::default() }], ..Default::default() };
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
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 0.0, 4.0)], transitions: vec![], overlays: vec![], ..Default::default() };
        let (script, has_audio) = render_script(&edit, &silent, &frame(), "source");
        assert!(!has_audio);
        assert_eq!(script, expected("\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS,{fit}[v0];
[1:v]trim=start=0:end=4,setpts=PTS-STARTPTS,{fit}[v1];
[v0][v1]concat=n=2:v=1:a=0[v]"));
        // A muted overlay of a clip with sound adds no track to a silent edit.
        let muted = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0)], transitions: vec![],
            overlays: vec![Overlay { media: 1, at: 0.0, start: 0.0, end: 1.0, x: 0.0, y: 0.0, w: 0.5, volume: 0.0, ..Default::default() }], ..Default::default() };
        assert!(!render_script(&muted, &[Input { has_audio: false }, Input { has_audio: true }], &frame(), "source").1);
        // Only the main video, no transitions or overlays: the 4a graph.
        let cut = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.5, 1.5, 0.5)], transitions: vec![], overlays: vec![], ..Default::default() };
        assert_eq!(render_script(&cut, &[Input { has_audio: true }], &frame(), "720p"), (filter_script(&cut.segments, true, "720p"), true));
    }

    #[test]
    fn validates_inserted_media_transitions_and_overlays() {
        let mut ok = request(vec![segment(0.0, 10.0, 1.0, 1.0), clip(1, 2.0, 8.04), segment(10.0, 20.0, 1.0, 1.0)]);
        ok.media = vec![r"D:\c\intro.mp4".into()];
        ok.transitions = vec![joint(0, TransitionKind::Dissolve, 2.0), joint(1, TransitionKind::Fade, 0.2)];
        ok.overlays = vec![Overlay { media: 1, at: 5.0, start: 0.0, end: 8.04, x: 0.62, y: 0.03, w: 0.35, volume: 1.0, ..Default::default() }];
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
            ("too many overlays", Box::new(|r| r.overlays = vec![r.overlays[0].clone(); 21])),
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
        let parsed: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "kind": "file", "media": [r"D:\a.mp4"], "quality": "source", "folder": "x",
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
            ("slow", request(vec![segment(0.0, 10.0, 0.2, 1.0)])),
            ("fast", request(vec![segment(0.0, 10.0, 4.1, 1.0)])),
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
        assert!(serde_json::from_value::<ExportRequest>(json!({"outputPath": "x", "kind": "file", "segments": [], "quality": "source",
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
        assert_eq!(unique_target(&folder, "Phim", "mp4").unwrap(), folder.join("Phim.mp4"));
        fs::write(folder.join("Phim.mp4"), b"x").unwrap();
        assert_eq!(unique_target(&folder, "Phim", "mp4").unwrap(), folder.join("Phim (2).mp4"));
        fs::write(folder.join("Phim (2).srt"), b"x").unwrap();
        fs::write(folder.join("Phim (3).mp4.partial"), b"x").unwrap();
        assert_eq!(unique_target(&folder, "Phim", "mp4").unwrap(), folder.join("Phim (4).mp4"));
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
        let data = base.join("data");
        fs::create_dir_all(&data).unwrap();
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
        let docu = base.join("DubFlow Output/Phim tai lieu - Đập/run-000000000001");
        fs::create_dir_all(docu.join(".dubflow-work")).unwrap();
        fs::write(docu.join(".dubflow-work/spec.json"), b"{\"schema_version\":1}").unwrap();
        fs::write(docu.join("documentary.mp4"), b"video").unwrap();
        let open = |path: &Path, kind: &str| resolve_target(&crate::display_path(path), kind, &data);
        let store = data.join("edit-files");
        // Shaped like DubFlow's runs but not recorded by the app: files, drafts in the app's store.
        for (video, kind) in [(product.join("video-1.mp4"), "product"), (compilation.join("compilation.mp4"), "compilation"), (docu.join("documentary.mp4"), "docu")] {
            let target = open(&video, kind).unwrap();
            assert!(target.kind == Kind::File && target.work.starts_with(&store), "{kind} without a record");
        }
        record_run_in(&data, "product", &product).unwrap();
        record_run_in(&data, "compilation", &compilation).unwrap();
        record_run_in(&data, "docu", &docu).unwrap();
        let first = open(&product.join("video-1.mp4"), "product").unwrap();
        let second = open(&product.join("video-2.mp4"), "product").unwrap();
        assert_eq!((first.kind, second.kind), (Kind::Product(1), Kind::Product(2)));
        assert!(first.work.ends_with(".dubflow-work/edit-1") && second.work.ends_with(".dubflow-work/edit-2"), "one draft per video");
        assert!(first.captions.as_ref().unwrap().ends_with("captions-1.srt"));
        // Picked or dropped from the computer: always a file, even a recorded run's video.
        let picked = open(&product.join("video-1.mp4"), "file").unwrap();
        assert!(picked.kind == Kind::File && picked.work.starts_with(&store));
        assert!(open(&product.join("video-1.mp4"), "compilation").is_err(), "the page's kind must match the record");
        assert!(open(&product.join("video-1.mp4"), "other").is_err());
        // A record of another kind proves nothing.
        assert!(!recorded_run(&data, "docu", &product));
        // A documentary run's rough cut, with its English captions; not the same name in another folder.
        let film = open(&docu.join("documentary.mp4"), "docu").unwrap();
        assert_eq!(film.kind, Kind::Docu);
        assert!(film.captions.as_ref().unwrap().ends_with("captions_en.srt") && film.work.ends_with(".dubflow-work/edit"));
        fs::write(compilation.join("documentary.mp4"), b"video").unwrap();
        let elsewhere = compilation.join("documentary.mp4").canonicalize().unwrap();
        assert!(shaped_target(&elsewhere).is_none());
        // Any other video opens as a file, its draft kept in the app's store, not beside it.
        let file = file_target(&crate::display_path(&elsewhere), &store).unwrap();
        assert_eq!((file.kind, file.captions.is_none(), file.work.parent()), (Kind::File, true, Some(store.as_path())));
        assert_eq!(file_key(Path::new(r"C:\Videos\Clip.mp4")), file_key(Path::new(r"c:\videos\clip.MP4")));
        assert_ne!(file_key(Path::new(r"C:\Videos\a.mp4")), file_key(Path::new(r"C:\Videos\b.mp4")));
        assert!(file_target(&crate::display_path(&compilation.join(".dubflow-work/spec.json")), &store).is_err(), "not a video");
        let joined = open(&compilation.join("compilation.mp4"), "compilation").unwrap();
        assert_eq!((joined.kind, joined.captions.is_none()), (Kind::Compilation, true));
        assert!(joined.work.ends_with(".dubflow-work/edit"));
        for name in ["video-x.mp4", "final.mp4"] {
            assert!(shaped_target(&product.join(name).canonicalize().unwrap()).is_none(), "{name}");
        }
        // Without its request a run is not a product run.
        fs::remove_file(product.join(".dubflow-work/spec.json")).unwrap();
        assert!(shaped_target(&product.join("video-1.mp4").canonicalize().unwrap()).is_none());
        fs::write(product.join(".dubflow-work/spec.json"), b"{}").unwrap();
        fs::remove_dir_all(base).unwrap();
    }

    /// A directory link: an NTFS junction (reparse point) on Windows, a symlink elsewhere.
    fn link_dir(target: &Path, link: &Path) {
        #[cfg(windows)]
        {
            let status = Command::new("cmd").args(["/C", "mklink", "/J"]).arg(link).arg(target)
                .stdout(Stdio::null()).status().unwrap();
            assert!(status.success(), "mklink /J failed");
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, link).unwrap();
    }

    fn unlink_dir(link: &Path) {
        // A junction is removed as a folder, a symlink as a file; neither touches its target.
        if fs::remove_dir(link).is_err() {
            fs::remove_file(link).unwrap();
        }
    }

    /// The app's draft store and a file's own draft folder never lead out
    /// through a link or junction: saving fails before anything outside changes.
    #[test]
    fn file_drafts_never_follow_a_link_out_of_the_app_store() {
        use sha2::{Digest, Sha256};
        let base = std::env::temp_dir().join(format!("dubflow-edit-links-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let data = base.join("data");
        let source = base.join("Videos");
        let outside = base.join("outside");
        for dir in [&data, &source, &outside] {
            fs::create_dir_all(dir).unwrap();
        }
        let video = source.join("clip.mp4");
        fs::write(&video, b"video").unwrap();
        fs::write(outside.join("marker.txt"), b"keep").unwrap();
        fs::write(outside.join("project.json"), b"{\"theirs\":true}").unwrap();
        let state = |dir: &Path| {
            let mut entries: Vec<_> = fs::read_dir(dir).unwrap().map(|entry| {
                let path = entry.unwrap().path();
                let bytes = fs::read(&path).unwrap_or_default();
                (path.file_name().unwrap().to_owned(), format!("{:x}", Sha256::digest(&bytes)), fs::metadata(&path).unwrap().modified().unwrap())
            }).collect();
            entries.sort();
            entries
        };
        let (outside_before, source_before) = (state(&outside), state(&source));
        let path = crate::display_path(&video);
        let store = data.join("edit-files");
        let project = br#"{"schema_version":1}"#;

        // The store itself is a link to the user's folder.
        link_dir(&outside, &store);
        assert!(resolve_target(&path, "file", &data).is_err());
        unlink_dir(&store);
        // The store is a link to the video's own folder.
        link_dir(&source, &store);
        assert!(resolve_target(&path, "file", &data).is_err());
        unlink_dir(&store);
        // The file's own draft folder is a link (to elsewhere, or beside the video).
        fs::create_dir(&store).unwrap();
        let child = store.join(file_key(&video.canonicalize().unwrap()));
        for target in [&outside, &source] {
            link_dir(target, &child);
            assert!(resolve_target(&path, "file", &data).is_err());
            unlink_dir(&child);
        }
        // Swapped for a link after it was checked: the save right after fails closed.
        let opened = resolve_target(&path, "file", &data).unwrap();
        assert_eq!(opened.work, child);
        fs::remove_dir(&child).unwrap();
        link_dir(&outside, &child);
        assert!(save_draft(&opened, project).is_err());
        assert!(opened.confined().is_err(), "status and cancel writes are refused too");
        write_status(&opened.work, "FAILED", 0.0, "x", None);
        append_export(&opened.work, json!({"path": "x"}));
        assert!(!outside.join("status.json").exists() && !outside.join("exports.json").exists(), "a render's own writes are refused");
        assert!(guarded(&opened.work).is_err() && opened.prepare().is_err(), "an export does not start");
        unlink_dir(&child);
        // Swapped for a link to a folder that does not exist yet: it is never created.
        let missing = outside.join("new");
        link_dir(&missing, &child);
        assert!(save_draft(&opened, project).is_err() && opened.prepare().is_err() && opened.confined().is_err());
        write_status(&opened.work, "FAILED", 0.0, "x", None);
        append_export(&opened.work, json!({"path": "x"}));
        assert!(!missing.exists(), "the link's missing target was not created");
        assert!(resolve_target(&path, "file", &data).is_err());
        unlink_dir(&child);
        assert_eq!(state(&outside), outside_before, "nothing outside the store changed");
        assert_eq!(state(&source), source_before, "nothing beside the video changed");
        // The ordinary store still works.
        let opened = resolve_target(&path, "file", &data).unwrap();
        save_draft(&opened, project).unwrap();
        assert_eq!(fs::read(child.join("project.json")).unwrap(), project);
        assert_eq!(state(&outside), outside_before);
        fs::remove_dir_all(base).unwrap();
    }

    /// A user's own `DubFlow Output` tree holding metadata of every valid
    /// shape (a worker manifest, a completed-looking status, run requests)
    /// is still the user's: nothing is written beside those videos.
    #[test]
    fn valid_looking_metadata_in_a_users_folder_is_not_dubflows() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-fake-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let data = base.join("data");
        fs::create_dir_all(data.join("control/jobs")).unwrap();
        let output = base.join("Videos/DubFlow Output");
        let job = output.join("clip - vi/run-1");
        let product = output.join("Ban hang - x/run-0123456789ab");
        let compilation = output.join("Tong hop - x/run-0123456789ab");
        let docu = output.join("Phim tai lieu - x/run-0123456789ab");
        for dir in [&job, &product, &compilation, &docu] {
            fs::create_dir_all(dir.join(".dubflow-work")).unwrap();
            fs::write(dir.join(".dubflow-work/spec.json"), json!({"schema_version": 1, "sources": []}).to_string()).unwrap();
            fs::write(dir.join(".dubflow-work/status.json"), json!({"schema_version": 1, "state": "COMPLETED", "videos": [{"index": 1, "ok": true}]}).to_string()).unwrap();
        }
        fs::write(job.join("job_manifest.json"), json!({"source": {"path": "C:/clip.mp4"}, "audio": {"mode": "dubbed"}}).to_string()).unwrap();
        fs::write(job.join(".dubflow-work/translation.json"), b"{}").unwrap();
        // A status file beside the video naming it is not the app's record.
        fs::write(job.join("job.json"), json!({"status": {"state": "COMPLETED"}, "output_path": crate::display_path(&job.join("final_vi.mp4"))}).to_string()).unwrap();
        let videos = [(job.join("final_vi.mp4"), "job"), (product.join("video-1.mp4"), "product"),
            (compilation.join("compilation.mp4"), "compilation"), (docu.join("documentary.mp4"), "docu")];
        for (video, _) in &videos {
            fs::write(video, b"video").unwrap();
        }
        let store = data.join("edit-files");
        for (video, kind) in &videos {
            assert!(shaped_target(&video.canonicalize().unwrap()).is_some(), "{kind} is shaped like DubFlow's");
            for asked in [*kind, "file"] {
                let target = resolve_target(&crate::display_path(video), asked, &data).unwrap();
                assert!(target.kind == Kind::File && target.work.starts_with(&store), "{kind} opened as {asked}");
            }
        }
        // A job record that names another video proves nothing for this one.
        fs::write(data.join("control/jobs/job_1.json"), json!({"status": {"state": "COMPLETED"},
            "output_path": crate::display_path(&base.join("elsewhere/final_vi.mp4"))}).to_string()).unwrap();
        assert!(!recorded_job(&data, &job.join("final_vi.mp4")));
        // Nor does a record of a job that has not finished.
        fs::write(data.join("control/jobs/job_2.json"), json!({"status": {"state": "RUNNING"},
            "output_path": crate::display_path(&job.join("final_vi.mp4"))}).to_string()).unwrap();
        assert!(!recorded_job(&data, &job.join("final_vi.mp4")));
        for (video, _) in &videos {
            assert!(!video.parent().unwrap().join(".dubflow-work/edit").exists() && !video.parent().unwrap().join(".dubflow-work/edit-1").exists());
        }
        // The supervisor's completed record makes it the job's.
        fs::write(data.join("control/jobs/job_3.json"), json!({"schema_version": 1, "job_id": "job_3", "status": {"state": "COMPLETED"},
            "output_path": crate::display_path(&job.join("final_vi.mp4"))}).to_string()).unwrap();
        let recorded = resolve_target(&crate::display_path(&job.join("final_vi.mp4")), "job", &data).unwrap();
        assert!(recorded.kind == Kind::Job && recorded.work.ends_with(".dubflow-work/edit"));
        assert_eq!(resolve_target(&crate::display_path(&job.join("final_vi.mp4")), "file", &data).unwrap().kind, Kind::File);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn saves_drafts_and_lists_exports_for_finished_videos_only() {
        let base = std::env::temp_dir().join(format!("dubflow-edit-host-{}", std::process::id()));
        let dir = base.join("DubFlow Output/clip - vi/run-1");
        fs::create_dir_all(&dir).unwrap();
        let video = dir.join("final_vi.mp4");
        fs::write(&video, b"video").unwrap();
        let path = crate::display_path(&video);
        // The supervisor's record of the finished job, in the app's data root.
        let record = crate::control_root().unwrap().join(format!("control/jobs/edit-test-{}.json", std::process::id()));
        fs::write(&record, json!({"schema_version": 1, "job_id": "edit-test", "status": {"state": "COMPLETED"}, "output_path": path}).to_string()).unwrap();
        let project = json!({"schema_version": 1, "duration": 10.0, "segments": [{"id": "s1", "start": 0, "end": 10, "speed": 1, "volume": 1}]});
        edit_project_save(path.clone(), "job".into(), project.clone()).unwrap();
        let saved: Value = serde_json::from_slice(&fs::read(dir.join(".dubflow-work/edit/project.json")).unwrap()).unwrap();
        assert_eq!(saved, project);
        assert!(edit_project_save(path.clone(), "job".into(), json!({"schema_version": 5, "segments": [{}]})).is_err());
        assert!(edit_project_save(path.clone(), "job".into(), json!({"schema_version": 3, "segments": [{}], "audio": [], "main_volume": 1})).is_ok(), "4e drafts");
        assert!(edit_project_save(path.clone(), "job".into(), json!({"schema_version": 4, "segments": [{}], "texts": []})).is_ok(), "4f drafts");
        assert!(edit_project_save(path.clone(), "job".into(), json!({"schema_version": 1, "segments": []})).is_err());
        assert!(edit_project_save(path.clone(), "job".into(), json!({"schema_version": 1, "segments": [{}], "pad": "x".repeat(PROJECT_LIMIT)})).is_err());
        fs::write(dir.join("other.mp4"), b"video").unwrap();
        // Any other video is a file (Issue #497): its draft never goes beside it or into the job's folder.
        let as_file = |path: &Path| edit_target(&crate::display_path(path), "job").map_or(true, |target| target.kind == Kind::File && !target.work.starts_with(&base));
        assert!(as_file(&dir.join("other.mp4")), "only final_vi.mp4 is the job's");
        assert!(edit_project_save("relative/final_vi.mp4".into(), "job".into(), project.clone()).is_err());
        let outside = base.join("elsewhere/final_vi.mp4");
        fs::create_dir_all(outside.parent().unwrap()).unwrap();
        fs::write(&outside, b"video").unwrap();
        assert!(as_file(&outside), "only DubFlow results are jobs");
        // A user's own folder named like DubFlow's: final_vi.mp4 there is a file, its draft never beside it.
        let personal = base.join("Videos/DubFlow Output/Personal");
        fs::create_dir_all(&personal).unwrap();
        fs::write(personal.join("final_vi.mp4"), b"video").unwrap();
        assert!(edit_target(&crate::display_path(&personal.join("final_vi.mp4")), "job").unwrap().kind == Kind::File);
        let store = base.join("store");
        let target = file_target(&crate::display_path(&personal.join("final_vi.mp4")), &store).unwrap();
        assert!(target.kind == Kind::File && target.work.starts_with(&store));
        fs::write(personal.join("job_manifest.json"), json!({"source": {"path": "C:/clip.mp4"}}).to_string()).unwrap();
        edit_project_save(crate::display_path(&personal.join("final_vi.mp4")), "job".into(), project.clone()).unwrap();
        assert!(edit_target(&crate::display_path(&personal.join("final_vi.mp4")), "job").unwrap().kind == Kind::File, "a manifest is not the app's record");
        assert!(!personal.join(".dubflow-work").exists(), "nothing written beside the user's video");
        assert_eq!(edit_target(&path, "job").unwrap().kind, Kind::Job, "a recorded job stays a job");
        assert_eq!(edit_target(&path, "file").unwrap().kind, Kind::File, "picked from the computer it is a file");

        let work = dir.join(".dubflow-work/edit");
        let exported = base.join("Phim (sửa 1).mp4");
        fs::write(&exported, b"video").unwrap();
        append_export(&work, json!({"path": crate::display_path(&exported), "duration": 5.0}));
        append_export(&work, json!({"path": crate::display_path(&base.join("deleted.mp4")), "duration": 5.0}));
        // A render left RUNNING by a closed app is reported as interrupted.
        write_status(&work, "RUNNING", 40.0, "Đang xuất video… 40%", Some(&base.join("Phim (sửa 2).mp4")));
        fs::write(base.join("Phim (sửa 2).mp4.partial"), b"half").unwrap();
        let status = edit_status(path.clone(), "job".into()).unwrap();
        assert_eq!(status["exports"].as_array().unwrap().len(), 1);
        assert_eq!(status["status"]["state"], "FAILED");
        assert!(!base.join("Phim (sửa 2).mp4.partial").exists());
        // While another holder has the lease (Windows file sharing), RUNNING stays.
        if cfg!(windows) {
            write_status(&work, "RUNNING", 10.0, "x", None);
            let lease = crate::work_lease::take(&work, BUSY).unwrap();
            assert_eq!(edit_status(path.clone(), "job".into()).unwrap()["status"]["state"], "RUNNING");
            drop(lease);
        }
        edit_cancel(path, "job".into()).unwrap();
        assert!(work.join("cancel").is_file());
        let _ = fs::remove_file(record);
        let _ = fs::remove_dir_all(edit_target(&crate::display_path(&personal.join("final_vi.mp4")), "file").unwrap().work);
        fs::remove_dir_all(base).unwrap();
    }

    fn style(changes: Value) -> Value {
        let mut value = json!({"font": "Candara", "size": 1.2, "primary": "#FDE047", "outline": "#000000", "box": null,
            "box_opacity": 0.6, "outline_width": 3, "bold": true, "align": "bottom"});
        value.as_object_mut().unwrap().extend(changes.as_object().unwrap().clone());
        value
    }

    #[test]
    fn burned_lines_follow_the_worker_look_and_stay_literal() {
        let style: SubtitleStyle = serde_json::from_value(json!({"font": "Arial", "size": 1.25, "primary": "#FFFF00", "outline": "#000000",
            "box": "#101010", "box_opacity": 0.5, "outline_width": 2.5, "bold": true, "align": "top", "x": 0.5, "y": 0.25})).unwrap();
        let burn = BurnSubtitles { style: style.clone(), lines: vec![
            Cue { start_ms: 2500, end_ms: 4000, text: "Hai {\\b1}\nba".into() },
            Cue { start_ms: 0, end_ms: 1200, text: " Một ".into() },
        ] };
        let script = burn_ass(&burn).unwrap();
        assert!(script.contains("PlayResX: 1920\nPlayResY: 1080\n"));
        assert!(script.contains("Style: Default,Arial,60,&H0000FFFF,&H000000FF,&H00000000,&H80101010,-1,0,0,0,100,100,0,0,4,2.5,12,8,40,40,48,1\n"), "{script}");
        let events: Vec<&str> = script.lines().filter(|line| line.starts_with("Dialogue:")).collect();
        assert_eq!(events, [
            "Dialogue: 0,0:00:00.00,0:00:01.20,Default,,0,0,0,,{\\an5\\pos(960,270)}Một",
            "Dialogue: 0,0:00:02.50,0:00:04.00,Default,,0,0,0,,{\\an5\\pos(960,270)}Hai \\{\\\u{2060}b1\\}\\Nba",
        ]);
        let plain = BurnSubtitles { style: SubtitleStyle { box_color: None, x: None, y: None, ..style.clone() }, lines: burn.lines.clone() };
        assert!(burn_ass(&plain).unwrap().contains(",&H64000000,-1,0,0,0,100,100,0,0,1,2.5,0,8,"));
        let empty = BurnSubtitles { style: style.clone(), lines: vec![Cue { start_ms: 0, end_ms: 900, text: "  ".into() }] };
        assert!(burn_ass(&empty).is_err());
        let backwards = BurnSubtitles { style, lines: vec![Cue { start_ms: 900, end_ms: 900, text: "x".into() }] };
        assert!(burn_ass(&backwards).is_err());
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
    fn a_drawn_cover_reads_the_main_video_once() {
        let cover: CoverChoice = serde_json::from_value(json!({"style": {"mode": "color", "color": "#FF0000"},
            "regions": [{"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1, "start_ms": 0, "end_ms": 2000}]})).unwrap();
        let filter = cover_filter(&cover).unwrap().unwrap();
        assert!(filter.starts_with("drawbox=") && filter.contains("color=0xFF0000"));
        let graph = with_cover("[0:v]trim=start=0:end=1[v0];\n[0:v]trim=start=2:end=3[v1];\n[1:v]null[v2]", &filter);
        assert_eq!(graph.matches("[0:v]").count(), 1);
        assert!(graph.starts_with(&format!("[0:v]{filter}[mcv];\n[mcv]split=2[mc0][mc1];\n[mc0]trim=start=0")), "{graph}");
        assert!(graph.contains("[mc1]trim=start=2:end=3[v1]") && graph.contains("[1:v]null[v2]"));
        assert_eq!(with_cover("[0:v]null[v]", &filter), format!("[0:v]{filter}[mcv];\n[mcv]null[v]"));
        assert_eq!(cover_filter(&CoverChoice::default()).unwrap(), None);
        let stretch: CoverChoice = serde_json::from_value(json!({"fixed": {"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1}})).unwrap();
        assert!(cover_filter(&stretch).unwrap().unwrap().starts_with("split=3[cv0m]"));
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
        let mut records = vec![crate::record_test_job(&dir.join("final_vi.mp4"))];

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
        records.push(crate::record_test_job(&revision.join("final_vi.mp4")));
        let again = edit_subtitles_load(crate::display_path(&revision.join("final_vi.mp4"))).unwrap();
        assert_eq!((again["cues"][0]["text"].as_str(), again["cues"][0]["voice_text"].as_str()), (Some("Xin chào"), Some("Chào")));
        assert_eq!(again["style"]["font"], "Candara");
        assert_eq!(read_cover(&revision), Some(cover.clone()));
        assert_eq!(again["cover"]["style"], json!({"mode": "mosaic", "direction": "top", "strip_px": 4, "color": "#000000", "block": 32, "feather": 0}));
        assert_eq!(again["cover"]["disabled_auto"], json!([0]));
        let written: Value = serde_json::from_slice(&fs::read(revision.join(COVER_FILE)).unwrap()).unwrap();
        assert_eq!(written["schema_version"], 1);
        assert!(read_cover(&dir).is_none(), "the original keeps its cover");
        for record in records {
            let _ = fs::remove_file(record);
        }
        fs::remove_dir_all(base).unwrap();
    }

    fn sound(input: usize, at: f64, start: f64, end: f64) -> Sound {
        Sound { input, at, start, end, volume: 1.0, fade_in: 0.0, fade_out: 0.0, duck_db: 0.0, repeat: false, clean: Clean::default() }
    }

    fn whole(seconds: f64) -> Edit {
        Edit { segments: vec![segment(0.0, seconds, 1.0, 1.0)], transitions: vec![], overlays: vec![], ..Default::default() }
    }

    const MAIN_ONLY: &str = "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[v0][a0]concat=n=1:v=1:a=1[v][am]";

    #[test]
    fn mixes_a_sound_effect_with_fades_and_limits_the_sum() {
        let mix = Mix { sounds: vec![Sound { volume: 0.8, fade_in: 0.2, fade_out: 0.5, ..sound(0, 2.5, 0.0, 1.2) }], main_volume: 1.0, ..Default::default() };
        let (script, has_audio) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, &Finish::default());
        assert!(has_audio);
        assert_eq!(script, expected(&format!("{MAIN_ONLY};
[1:a]atrim=start=0:end=1.2,asetpts=PTS-STARTPTS,{{af}},afade=t=in:st=0:d=0.2,afade=t=out:st=0.7:d=0.5,volume=0.8,adelay=2500:all=1[sx0];
[am][sx0]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.891:level=0:latency=1[a]")));
    }

    #[test]
    fn ducks_the_original_sound_under_a_clip() {
        // A file that ducks 12 dB, and a louder sound effect cut at the end of the output that ducks 6 dB.
        let mix = Mix { sounds: vec![Sound { duck_db: 12.0, ..sound(0, 4.0, 0.0, 2.0) }, Sound { volume: 1.5, duck_db: 6.0, ..sound(1, 9.5, 0.5, 3.0) }],
            main_volume: 1.0, ..Default::default() };
        let (script, _) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, &Finish::default());
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
        let quiet = Mix { sounds: vec![], main_volume: 0.5, ..Default::default() };
        assert_eq!(export_script(&edit, &[Input { has_audio: true }], &frame(), "source", &quiet, &Finish::default()).0, expected(&format!("{MAIN_ONLY};
[am]volume=0.5,alimiter=limit=0.891:level=0:latency=1[a]")));
        let plain = Mix { sounds: vec![], main_volume: 1.0, ..Default::default() };
        assert_eq!(export_script(&edit, &[Input { has_audio: true }], &frame(), "source", &plain, &Finish::default()), render_script(&edit, &[Input { has_audio: true }], &frame(), "source"));
        // A silent video gets a sound track only for added sounds.
        assert!(!export_script(&edit, &[Input { has_audio: false }], &frame(), "source", &quiet, &Finish::default()).1);
        let (script, has_audio) = export_script(&edit, &[Input { has_audio: false }], &frame(), "source", &Mix { sounds: vec![sound(0, 0.0, 0.0, 1.0)], main_volume: 0.5, ..Default::default() }, &Finish::default());
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
            fade_out: 0.0, duck_db: 0.0, repeat: false, denoise: false, eq: None };
        let mix = validate_mix(&[clip(2.0, 0.64)], &[0], &[0.6], 1.0, 10.0).unwrap();
        assert_eq!(mix.sounds[0].end, 0.6, "clamped to the file");
        assert!(validate_mix(&[clip(10.0, 0.5)], &[0], &[0.6], 1.0, 10.0).is_err(), "starts after the output");
        assert!(validate_mix(&[clip(1.0, 0.7)], &[0], &[0.6], 1.0, 10.0).is_err(), "past the file");
        assert!(validate_mix(&[SoundClip { fade_in: 3.5, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[SoundClip { duck_db: 30.0, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[SoundClip { volume: f64::NAN, ..clip(1.0, 0.5) }], &[0], &[0.6], 1.0, 10.0).is_err());
        assert!(validate_mix(&[], &[], &[], 2.5, 10.0).is_err());
        let request: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "kind": "file", "segments": [{"start": 0, "end": 1, "speed": 1, "volume": 1}],
            "quality": "source", "folder": "x", "fileName": "a", "cues": [], "mainVolume": 0.8,
            "audio": [{"source": {"kind": "file", "path": "D:\\a.mp3"}, "at": 0, "start": 0, "end": 1, "volume": 1, "fade_in": 0, "fade_out": 0, "duck_db": 6}]})).unwrap();
        assert_eq!(request.main_volume, 0.8);
        assert_eq!(request.audio[0].source, SoundSource::File { path: "D:\\a.mp3".into() });
        let old: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "kind": "file", "segments": [], "quality": "source", "folder": "x", "fileName": "a", "cues": []})).unwrap();
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
        let plain = Mix { sounds: vec![], main_volume: 1.0, ..Default::default() };
        let input = [Input { has_audio: true }];
        assert_eq!(export_script(&whole(10.0), &input, &frame(), "source", &plain, &Finish { texts: Some("subtitles=filename=t.ass".into()), ..Finish::default() }).0, "\
[0:v]trim=start=0:end=10,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];
[v0][a0]concat=n=1:v=1:a=1[vt][a];
[vt]subtitles=filename=t.ass[v]");
        // After the quality scale and the mix, with overlays and transitions: still the last filter.
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0), segment(20.0, 30.0, 1.0, 1.0)],
            transitions: vec![joint(0, TransitionKind::Fade, 0.8)],
            overlays: vec![Overlay { media: 0, at: 1.0, start: 0.0, end: 2.0, x: 0.0, y: 0.0, w: 0.5, volume: 0.0, ..Default::default() }], ..Default::default() };
        let mix = Mix { sounds: vec![], main_volume: 0.5, ..Default::default() };
        let (with, has_audio) = export_script(&edit, &input, &frame(), "720p", &mix, &Finish { texts: Some("subtitles=filename=t.ass".into()), ..Finish::default() });
        let (without, _) = export_script(&edit, &input, &frame(), "720p", &mix, &Finish::default());
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
        let request: ExportRequest = serde_json::from_value(json!({"outputPath": "x", "kind": "file", "segments": [], "quality": "source", "folder": "x",
            "fileName": "a", "cues": [], "texts": [serde_json::to_value(&ok).unwrap()]})).unwrap();
        assert_eq!(request.texts, vec![ok]);
    }

    #[test]
    fn waveform_levels_use_a_decibel_scale() {
        assert_eq!(bucket_level(0), 0);
        assert_eq!(bucket_level(32767), 255);
        // -24 dBFS is half way up.
        let half = bucket_level((32768.0 * 10f64.powf(-24.0 / 20.0)) as i32);
        assert!((126..=129).contains(&half), "{half}");
        assert_eq!(bucket_level(10), 0, "below -48 dBFS is silence");
        let mut out = Vec::new();
        let mut carry = (0, 0);
        let samples: Vec<u8> = [100i16, -32767, 5, 7, 0].iter().flat_map(|value| value.to_le_bytes()).collect();
        push_peaks(&samples, 2, &mut carry, &mut out);
        assert_eq!(out, vec![bucket_level(32767), bucket_level(7)]);
        assert_eq!(carry, (0, 1));
    }

    #[test]
    fn segment_looks_render_and_validate() {
        let frame = Frame { width: 1920, height: 1080, fps: "30".into() };
        let fit = "FIT";
        let plain = segment(0.0, 10.0, 2.0, 1.0);
        assert_eq!(segment_video(&plain, fit, &frame, &[]), vec!["trim=start=0:end=10", "setpts=(PTS-STARTPTS)/2", "FIT"]);
        let styled = Segment {
            crop: Some(Crop { x: 0.25, y: 0.0, w: 0.5, h: 1.0 }), rotate: 90, flip_h: true,
            color: Some(Color { brightness: 0.1, contrast: 1.2, saturation: 0.0, temperature: 0.5 }), ..segment(0.0, 10.0, 1.0, 1.0)
        };
        let video = segment_video(&styled, fit, &frame, &[]).join(",");
        assert!(video.starts_with("trim=start=0:end=10,setpts=PTS-STARTPTS,crop=w='trunc(iw*0.5/2)*2':h='trunc(ih*1/2)*2':x='iw*0.25':y='ih*0',transpose=1,hflip,lutyuv=y='clip((val-128)*1.2+128+15,0,255)',hue=s=0,colorbalance="), "{video}");
        assert!(video.ends_with(",FIT"));
        let reversed = Segment { reverse: true, ..segment(0.0, 10.0, 2.0, 1.0) };
        assert_eq!(segment_video(&reversed, fit, &frame, &[]), vec!["trim=start=0:end=10", "setpts=PTS-STARTPTS", "reverse", "setpts=PTS/2", "FIT"]);
        let frozen = Segment { freeze: Some(3.0), ..segment(4.0, 4.1, 1.0, 0.0) };
        assert_eq!(segment_video(&frozen, fit, &frame, &[]),
            vec!["trim=start=4:end=4.1", "setpts=PTS-STARTPTS", "trim=end_frame=1", "tpad=stop_mode=clone:stop_duration=3", "FIT", "trim=duration=3"]);
        assert_eq!(segment_length(&frozen), 3.0);
        let zoomed = Segment { zoom: Some(Zoom { from: 1.0, to: 1.5, x: 0.5, y: 0.25 }), ..segment(0.0, 2.0, 1.0, 1.0) };
        let video = segment_video(&zoomed, fit, &frame, &[]);
        assert_eq!(video[3], "zoompan=z='1+0.5*min(on/59,1)':x='(iw-iw/zoom)*0.5':y='(ih-ih/zoom)*0.25':d=1:s=1920x1080:fps=30");
        assert_eq!(fps_number("30000/1001"), 30000.0 / 1001.0);

        // A styled main-only edit leaves the 4a graph; its sound reverses and fades.
        let edit = Edit { segments: vec![Segment { reverse: true, fade_in: 0.5, fade_out: 1.0, ..segment(0.0, 10.0, 1.0, 1.0) }], transitions: vec![], overlays: vec![], ..Default::default() };
        let inputs = vec![Input { has_audio: true }];
        let (graph, audio) = render_script(&edit, &inputs, &frame, "source");
        assert!(audio);
        assert!(graph.contains("atrim=start=0:end=10,asetpts=PTS-STARTPTS,areverse,afade=t=in:st=0:d=0.5,afade=t=out:st=9:d=1"), "{graph}");
        let edit = Edit { segments: vec![frozen], transitions: vec![], overlays: vec![], ..Default::default() };
        let (graph, _) = render_script(&edit, &inputs, &frame, "source");
        assert!(graph.contains("anullsrc=r=48000:cl=stereo,atrim=duration=3"), "a held frame is silent: {graph}");

        let bad = [
            Segment { rotate: 45, ..segment(0.0, 10.0, 1.0, 1.0) },
            Segment { crop: Some(Crop { x: 0.6, y: 0.0, w: 0.5, h: 1.0 }), ..segment(0.0, 10.0, 1.0, 1.0) },
            Segment { zoom: Some(Zoom { from: 1.0, to: 4.0, x: 0.5, y: 0.5 }), ..segment(0.0, 10.0, 1.0, 1.0) },
            Segment { color: Some(Color { brightness: 0.0, contrast: 3.0, saturation: 1.0, temperature: 0.0 }), ..segment(0.0, 10.0, 1.0, 1.0) },
            Segment { freeze: Some(20.0), ..segment(0.0, 0.1, 1.0, 0.0) },
            Segment { reverse: true, ..segment(0.0, 200.0, 1.0, 1.0) },
            Segment { fade_in: 5.0, ..segment(0.0, 10.0, 1.0, 1.0) },
        ];
        for (index, value) in bad.into_iter().enumerate() {
            assert!(validate(&request(vec![value]), &[300.0]).is_err(), "bad look {index}");
        }
        let good: Segment = serde_json::from_value(json!({"media": 0, "start": 0, "end": 10, "speed": 1, "volume": 1, "rotate": 270, "flip_v": true,
            "crop": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, "zoom": {"from": 1, "to": 1.3, "x": 0.5, "y": 0.5}, "fade_out": 0.5})).unwrap();
        assert!(validate(&request(vec![good]), &[30.0]).is_ok());
        assert!(serde_json::from_value::<Segment>(json!({"start": 0, "end": 1, "speed": 1, "volume": 1, "tint": 3})).is_err(), "unknown fields");
        assert_eq!(serde_json::to_value(segment(0.0, 1.0, 1.0, 1.0)).unwrap(), json!({"media": 0, "start": 0.0, "end": 1.0, "speed": 1.0, "volume": 1.0}));
    }

    #[test]
    fn wave_three_transitions_overlays_loops_loudness_and_shadows() {
        // Every overlapping transition is an xfade of its own kind and overlaps like a dissolve.
        let edit = Edit { segments: vec![segment(0.0, 5.0, 1.0, 1.0), segment(5.0, 10.0, 1.0, 1.0)],
            transitions: vec![joint(0, TransitionKind::WipeLeft, 1.0)], overlays: vec![], ..Default::default() };
        let (graph, _) = render_script(&edit, &[Input { has_audio: true }], &frame(), "source");
        assert!(graph.contains("xfade=transition=wipeleft:duration=1:offset=4"), "{graph}");
        assert!((output_seconds(&edit.segments, &edit.transitions) - 9.0).abs() < 1e-9);
        let kinds: Vec<TransitionKind> = serde_json::from_value(json!(["fadewhite", "wiperight", "slideleft", "slideright", "zoomin", "circleopen"])).unwrap();
        assert!(kinds.iter().all(|kind| kind.xfade().is_some()) && TransitionKind::Fade.xfade().is_none());

        // Overlays: plain ones are only scaled; framed, rounded and faded ones get alpha.
        let plain = Overlay { media: 1, end: 2.0, w: 0.25, ..Default::default() };
        assert_eq!(overlay_look(&plain, 1280, 720), "scale=320:-2,setsar=1");
        let styled = Overlay { opacity: 0.5, radius: 0.5, border: 6.0, border_color: Some("#FFCC00".into()), ..plain.clone() };
        assert_eq!(overlay_look(&styled, 1280, 720), "scale=312:-2,format=yuva420p,pad=iw+8:ih+8:4:4:color=0xFFCC00FF,setsar=1,\
geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='0.5*alpha(X,Y)*lte(hypot(max(max((0.5*min(W,H))-X,X-W+1+(0.5*min(W,H))),0),\
max(max((0.5*min(W,H))-Y,Y-H+1+(0.5*min(W,H))),0)),(0.5*min(W,H)))'");
        assert_eq!(overlay_look(&Overlay { opacity: 0.4, ..plain.clone() }, 1280, 720), "scale=320:-2,format=yuva420p,setsar=1,lut=a='val*0.4'");
        let bad = [Overlay { opacity: 0.05, ..plain.clone() }, Overlay { radius: 0.6, ..plain.clone() }, Overlay { border: 4.0, ..plain.clone() },
            Overlay { border: 4.0, border_color: Some("red".into()), ..plain.clone() }];
        for overlay in bad {
            let mut request = request(vec![segment(0.0, 5.0, 1.0, 1.0)]);
            request.media = vec!["b.png".into()];
            request.overlays = vec![overlay];
            assert!(validate(&request, &[5.0, STILL_SECONDS]).is_err());
        }
        assert_eq!(serde_json::to_value(&plain).unwrap(), json!({"media": 1, "at": 0.0, "start": 0.0, "end": 2.0, "x": 0.0, "y": 0.0, "w": 0.25, "volume": 1.0}));

        // A picture: its size, an hour long, no sound; looped as an input.
        let still = parse_still(r#"{"streams":[{"codec_type":"video","width":512,"height":256}],"format":{"size":"2048"}}"#).unwrap();
        assert_eq!((still.width, still.height, still.duration, still.has_audio, still.size), (512, 256, STILL_SECONDS, false, 2048));
        let args: Vec<String> = ffmpeg_args(&[PathBuf::from("in.mp4"), PathBuf::from("logo.PNG")], Path::new("f.txt"), true, "h264_mf", 3_000_000,
            "25/1", Path::new("o")).iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args[5..15].join(" "), "-i in.mp4 -loop 1 -framerate 30 -t 3600 -i logo.PNG");

        // A looping sound repeats its part to the end; loudness comes last.
        let mix = Mix { sounds: vec![Sound { repeat: true, ..sound(0, 2.0, 1.0, 4.0) }], main_volume: 1.0, ..Default::default() };
        let (script, _) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, &Finish { normalize: true, ..Finish::default() });
        assert!(script.contains("atrim=start=1:end=4,asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,\
aloop=loop=-1:size=144000,atrim=duration=8,adelay=2000:all=1"), "{script}");
        assert!(script.ends_with(&format!("[an]{LOUDNESS}[a]")), "{script}");
        let (plain_script, _) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, &Finish::default());
        assert!(!plain_script.contains("loudnorm"));
        let long = SoundClip { source: SoundSource::Sfx { id: "x".into() }, at: 0.0, start: 0.0, end: 700.0, volume: 1.0, fade_in: 0.0, fade_out: 0.0,
            duck_db: 0.0, repeat: true, denoise: false, eq: None };
        assert!(validate_mix(&[long.clone()], &[0], &[800.0], 1.0, 900.0).is_err());
        assert!(validate_mix(&[SoundClip { repeat: false, ..long }], &[0], &[800.0], 1.0, 900.0).is_ok());

        // A text shadow is the style's Shadow when there is no box.
        let shadowed: TextClip = serde_json::from_value(json!({"text": "Hi", "at": 0, "duration": 2, "x": 0.5, "y": 0.5, "w": 0.8, "font": "Arial",
            "size": 72, "color": "#FFFFFF", "outline": "#000000", "outline_width": 4, "box": null, "box_opacity": 0.6, "bold": true, "italic": false,
            "align": "center", "animation": "none", "anim_duration": 0.5, "shadow": 6})).unwrap();
        assert!(texts_ass(&[shadowed.clone()], 1920, 1080).contains(",1,4,6,5,0,0,0,1"), "{}", texts_ass(&[shadowed.clone()], 1920, 1080));
        assert!(validate_texts(&[TextClip { shadow: 13.0, ..shadowed }], 10.0).is_err());
    }

    #[test]
    fn export_options_reframe_change_rate_and_drop_the_picture() {
        assert_eq!(aspect_size(1920, 1080, "9:16"), (1080, 1920));
        assert_eq!(aspect_size(1080, 1920, "16:9"), (1920, 1080));
        assert_eq!(aspect_size(1280, 720, "1:1"), (720, 720));
        assert_eq!(aspect_size(1280, 720, "4:5"), (720, 900));
        assert_eq!(reframe_filter(720, 900, false), "scale=720:900:force_original_aspect_ratio=decrease,pad=720:900:(ow-iw)/2:(oh-ih)/2,setsar=1");
        assert!(reframe_filter(720, 900, true).contains("gblur=sigma=40"));
        let finish = Finish { texts: Some("subtitles=filename=t.ass".into()), reframe: Some(reframe_filter(720, 720, false)), audio_only: true, ..Finish::default() };
        let mix = Mix { sounds: vec![], main_volume: 1.0, ..Default::default() };
        let (graph, audio) = export_script(&whole(10.0), &[Input { has_audio: true }], &frame(), "source", &mix, &finish);
        assert!(audio);
        assert!(graph.contains("[vt]subtitles=filename=t.ass[vf0];\n[vf0]scale=720:720:force_original_aspect_ratio=decrease"), "{graph}");
        assert!(graph.ends_with("setsar=1[v];\n[v]nullsink"), "{graph}");
        let args: Vec<String> = audio_args(&[PathBuf::from("in.mp4")], Path::new("f.txt"), Path::new("o.m4a.partial"))
            .iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args[7..].join(" "), "-/filter_complex f.txt -map [a] -vn -c:a aac -b:a 192k -sn -dn -map_chapters -1 -movflags +faststart \
-progress pipe:1 -nostats -f ipod o.m4a.partial");
    }

    #[test]
    fn overlays_fade_and_glide() {
        let base = Overlay { media: 1, at: 2.0, end: 4.0, x: 0.1, y: 0.1, w: 0.25, ..Default::default() };
        assert!(overlay_motion(&base, 1280, 4.0).is_empty());
        assert_eq!(overlay_place(&base, 1280, 720, 4.0), ("128".into(), "72".into(), false));
        let moving = Overlay { fade_in: 0.5, fade_out: 3.0, to: Some(Placement { x: 0.5, y: 0.1, w: 0.5 }), ..base.clone() };
        assert_eq!(overlay_motion(&moving, 1280, 4.0), vec!["format=yuva420p".to_owned(), "fade=t=in:st=2:d=0.5:alpha=1".into(),
            "fade=t=out:st=4:d=2:alpha=1".into(), "scale=w='trunc((320+320*clip((t-2)/4,0,1))/2)*2':h=-2:eval=frame".into()]);
        assert_eq!(overlay_place(&moving, 1280, 720, 4.0), ("'128+512*clip((t-2)/4,0,1)'".into(), "'72+0*clip((t-2)/4,0,1)'".into(), true));
        assert!(overlay_look(&moving, 1280, 720).starts_with("scale=640:-2"), "drawn at its largest, then scaled");
        let edit = Edit { segments: vec![segment(0.0, 10.0, 1.0, 1.0)], transitions: vec![], overlays: vec![moving.clone()], ..Default::default() };
        let (graph, _) = render_script(&edit, &[Input { has_audio: true }, Input { has_audio: false }], &frame(), "source");
        assert!(graph.contains("overlay=x='128+512*clip((t-2)/4,0,1)':y='72+0*clip((t-2)/4,0,1)':eval=frame:enable='between(t,2,6)'"), "{graph}");
        let mut request = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        request.media = vec!["b.png".into()];
        request.overlays = vec![Overlay { fade_in: 4.0, ..base.clone() }];
        assert!(validate(&request, &[10.0, STILL_SECONDS]).is_err());
        request.overlays = vec![Overlay { to: Some(Placement { x: 0.5, y: 2.0, w: 0.5 }), ..base }];
        assert!(validate(&request, &[10.0, STILL_SECONDS]).is_err());
    }
    #[test]
    fn ellipse_overlays_cut_round_with_a_ring_frame() {
        assert_eq!(yuv("#FFFFFF"), (235, 128, 128));
        assert_eq!(yuv("#000000"), (16, 128, 128));
        let round = Overlay { media: 1, end: 4.0, w: 0.25, shape: Some("ellipse".into()), ..Default::default() };
        assert_eq!(overlay_look(&round, 1280, 720),
            "scale=320:-2,format=yuva420p,setsar=1,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='alpha(X,Y)*lte(hypot((X-W/2+0.5)/(W/2),(Y-H/2+0.5)/(H/2)),1)'");
        let framed = Overlay { border: 10.0, border_color: Some("#FFFFFF".into()), ..round.clone() };
        let look = overlay_look(&framed, 1280, 720);
        assert!(look.contains("lum='if(lte(hypot((X-W/2+0.5)/(W/2-7*W/320),(Y-H/2+0.5)/(H/2-7*W/320)),1),lum(X,Y),235)'"), "{look}");
        let mut request = request(vec![segment(0.0, 10.0, 1.0, 1.0)]);
        request.media = vec!["b.png".into()];
        request.overlays = vec![Overlay { shape: Some("star".into()), ..round }];
        assert!(validate(&request, &[10.0, STILL_SECONDS]).is_err());
    }

    #[test]
    fn segments_take_a_lut_file_once_validated() {
        let frame = frame();
        let graded = Segment { lut: Some(0), ..segment(0.0, 10.0, 1.0, 1.0) };
        assert!(!graded.is_plain());
        let luts = vec![r"D:\LUT\it's [teal].cube".to_owned()];
        assert_eq!(segment_video(&graded, "FIT", &frame, &luts),
            vec!["trim=start=0:end=10".to_owned(), "setpts=PTS-STARTPTS".into(), r"lut3d=file=D\\:/LUT/it\\\'s \[teal\].cube".into(), "FIT".into()]);
        let mut request = request(vec![graded]);
        assert!(validate(&request, &[10.0]).is_err(), "points at no file");
        request.luts = vec!["relative.cube".into()];
        assert!(validate(&request, &[10.0]).is_err(), "only absolute .cube paths");
        request.luts = vec![if cfg!(windows) { r"D:\LUT\teal.cube".into() } else { "/luts/teal.CUBE".into() }];
        assert_eq!(validate(&request, &[10.0]).map(|edit| edit.luts.len()), Ok(1));
        request.luts = vec![if cfg!(windows) { r"D:\LUT\teal.png".into() } else { "/luts/teal.png".into() }];
        assert!(validate(&request, &[10.0]).is_err());
        assert!(lut_file("/no/such/file.cube").is_err());
    }

    #[test]
    fn sounds_and_the_main_sound_can_be_cleaned() {
        assert!(Clean::default().filters().is_empty());
        assert_eq!(Clean { denoise: true, eq: Some(EqPreset::Bass) }.filters(), vec!["highpass=f=70", "afftdn=nr=12:nf=-40", "bass=g=6:f=100"]);
        let voice: SoundClip = serde_json::from_value(json!({"source": {"kind": "sfx", "id": "x"}, "at": 0, "start": 0, "end": 1, "volume": 1,
            "fade_in": 0, "fade_out": 0, "duck_db": 0, "denoise": true, "eq": "voice"})).unwrap();
        assert_eq!((voice.denoise, voice.eq), (true, Some(EqPreset::Voice)));
        assert!(serde_json::from_value::<SoundClip>(json!({"source": {"kind": "sfx", "id": "x"}, "at": 0, "start": 0, "end": 1, "volume": 1,
            "fade_in": 0, "fade_out": 0, "duck_db": 0, "eq": "loud"})).is_err(), "unknown presets are refused");
        let mix = validate_mix(&[voice], &[0], &[1.0], 1.0, 10.0).unwrap();
        assert_eq!(mix.sounds[0].clean, Clean { denoise: true, eq: Some(EqPreset::Voice) });
        let edit = whole(10.0);
        let (graph, _) = export_script(&edit, &[Input { has_audio: true }, Input { has_audio: true }], &frame(), "source", &mix, &Finish::default());
        assert!(graph.contains("[2:a]atrim=start=0:end=1,asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,highpass=f=70,afftdn=nr=12:nf=-40,highpass=f=90,"), "{graph}");
        let main = Mix { main_volume: 1.0, main_clean: Clean { denoise: true, eq: None }, ..Default::default() };
        let (graph, _) = export_script(&edit, &[Input { has_audio: true }], &frame(), "source", &main, &Finish::default());
        assert!(graph.contains("[am]highpass=f=70,afftdn=nr=12:nf=-40,"), "{graph}");
        assert_eq!(percent_decoded("D%3A%5CVideos%5CPhim%20hay%20(s%E1%BB%ADa).mp4").as_deref(), Some(r"D:\Videos\Phim hay (sửa).mp4"));
        assert_eq!(percent_decoded("%E1%BB"), None);
        assert_eq!(percent_decoded("%Z1"), None);
    }
}
