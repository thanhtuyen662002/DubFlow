//! Kiểm tra kỹ thuật (Issue #935, Part L item 2): an advisory look at each
//! editor export once it is published and its lease is released.
//!
//! One FFmpeg pass decodes the whole file (black and frozen stretches, decode
//! errors, the audio's true peak) and the ffprobe helper reads its length and
//! streams. The length, caption and peak rules and their thresholds are Final
//! QC's (`check_output` in engine/dubflow/mastering/final_qc.py; a test keeps
//! them in step). Black and frozen use FFmpeg's own filter defaults, written
//! out. Whether burned subtitles stay inside the frame is measured apart from
//! the video, because burned text cannot be told apart from the picture: the
//! export's own subtitle layers are drawn by libass, in white, on a black frame
//! twice the export's size with the export frame in its middle, and FFmpeg's
//! `bbox` reports where they landed, past the export frame too.
//!
//! The result is never a gate: it has no "passed" or READY state, nothing
//! reads it to allow or refuse anything, and the check never deletes, renames
//! or re-encodes the video. While FFmpeg reads it (the running check, not a
//! waiting one) Windows refuses to rename, move or delete the file; the UI says
//! so and Dừng kiểm tra frees it. It only reads the video and writes in its
//! own app-owned folder, `work/checks/<file_key(video)>/`, never beside the
//! user's file, and it never touches `status.json` or `exports.json`.
use super::*;
use std::sync::atomic::AtomicUsize;

/// = final_qc.SYNC_TOLERANCE_MS (a test reads final_qc.py).
pub(super) const SYNC_TOLERANCE_MS: f64 = 120.0;
/// = final_qc.CLIP_DBTP (a test reads final_qc.py).
pub(super) const CLIP_DBTP: f64 = 0.0;
/// A track whose true peak stays below this carries no audible sound (#935 "silent"). Final QC has no such rule:
/// it only reads the peak, so this warning is the check's own and advisory.
const SILENT_DBTP: f64 = -60.0;
/// FFmpeg's defaults, written out so a newer FFmpeg cannot move them. Advisory only.
const BLACK: &str = "blackdetect=d=2:pic_th=0.98:pix_th=0.10";
const FREEZE: &str = "freezedetect=n=0.001:d=2";
/// The shortest frozen stretch reported, = `d` of `FREEZE` (what is left after the intended holds are taken out).
const MIN_FROZEN: f64 = 2.0;
/// = the first `loudnorm` pass `measure.loudness` runs for Final QC; only `input_tp` is read.
const PEAK: &str = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json";
/// The filters run on a 320-pixel copy (like the documentary shot detector): every frame is still decoded.
const SMALL: &str = "scale=320:-2";
const CHECKS_DIR: &str = "checks";
const LOG_LIMIT: u64 = 4 * 1024 * 1024;
const CHECK_LIMIT: u64 = 64 * 1024;
/// `edit_status` attaches the checks of the newest exports only.
const ATTACHED: usize = 50;
/// Black and frozen stretches kept in the result, and times named in a detail.
const MAX_SPANS: usize = 50;
const NAMED: usize = 5;

/// Checks running in this window, by check folder: "Dừng kiểm tra" stops them,
/// and a RUNNING check.json that is not here and whose lease is free was left by a closed app.
static CHECKING: LazyLock<Mutex<HashMap<PathBuf, Arc<Running>>>> = LazyLock::new(|| Mutex::new(HashMap::new()));
/// Set when DubFlow closes (`stop_all`): no check starts after that.
static CLOSING: AtomicBool = AtomicBool::new(false);
/// One check at a time in this window: each is a full decode, so they never pile up beside the next export.
static TURN: Mutex<()> = Mutex::new(());
/// Checks waiting for `TURN`, at most `MAX_WAITING`.
static WAITING: AtomicUsize = AtomicUsize::new(0);
/// The tests of this crate run many checks at once on the one `TURN`; the bound itself is tested on its own
/// (`the_check_queue_is_bounded`).
const MAX_WAITING: usize = if cfg!(test) { 64 } else { 3 };

/// Burned subtitles inside the frame: `ok` None when there is nothing to estimate.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub(super) struct SubtitleFit {
    pub(super) ok: Option<bool>,
    pub(super) detail: String,
    /// Output times (ms) of the lines at risk.
    pub(super) at_ms: Vec<u64>,
}

/// What the check needs from the export, decided in `start_export` (all pure),
/// saved as `checks/<key>/plan.json`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub(super) struct Plan {
    /// The published video: set by `for_video` once its final name is known.
    video: PathBuf,
    expected_seconds: f64,
    has_audio: bool,
    /// The last end of the subtitles; None when no .srt was written.
    srt_end_ms: Option<u64>,
    /// Output-time spans of freeze segments and still-image segments: held on purpose.
    intended_holds: Vec<(f64, f64)>,
    /// The burned Phụ đề to measure; None when nothing is burned.
    subtitles: Option<SubtitleFrame>,
    /// The published file's identity (`Stamp`), taken by `run_export` at publication, before the export is listed or
    /// marked COMPLETED: a file changed or replaced at the same name before or during the check is not judged
    /// against this export's plan (recheck 8, Lead review).
    #[serde(default)]
    stamp: Option<Stamp>,
}

/// A file's size, modified time (ns) and identity on its disk: the volume serial and file index on Windows, the
/// device and inode elsewhere ((0, 0) where neither is known). A file put in place of another at the same name, even
/// with the same size and time, has another identity.
pub(super) type Stamp = [u64; 4];

/// Phụ đề to measure (#935): the burned layers as the export drew them, the export frame they were drawn on, and
/// the part of that frame the finished picture keeps.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub(super) struct SubtitleFrame {
    burns: Vec<BurnSubtitles>,
    export: (u32, u32),
    /// In export pixels: x0, y0, x1, y1. The whole frame unless "fill" crops it.
    window: (f64, f64, f64, f64),
    /// Why a line outside the window is cut.
    why: String,
    /// The export's frame rate (num, den), the clock Phụ đề is drawn on; (0, 0) when unknown.
    #[serde(default)]
    rate: (u64, u64),
    /// Why the burned lines cannot be measured for this export, said instead of a measure on the wrong frame.
    #[serde(default)]
    unmeasurable: Option<String>,
}

impl Plan {
    /// The plan for the video `run_export` published, whether its .srt was written, and the file's stamp taken at
    /// publication (never later: by then another file may already stand at the name).
    pub(super) fn for_video(mut self, video: PathBuf, srt_written: bool, stamp: Option<Stamp>) -> Self {
        self.stamp = stamp;
        self.video = video;
        if !srt_written {
            self.srt_end_ms = None;
        }
        self
    }

    /// The main video is shown turned a quarter (a phone video): FFmpeg may hand Phụ đề a frame of another shape
    /// than the export frame the plan knows, so the burned lines are not measured (recheck 10).
    pub(super) fn turned_source(mut self, turned: bool) -> Self {
        if let Some(frame) = self.subtitles.as_mut().filter(|_| turned) {
            frame.unmeasurable = Some(TURNED.into());
        }
        self
    }

    /// The export was rendered in parts cut at `cuts` (s, `parts::render`). Each part burns Phụ đề with a libass of its
    /// own, so lines that shared the screen before a cut and are still on screen across it are stacked anew there,
    /// while the measure runs one libass over the whole export: it would judge another layout than the video's, so the
    /// burned lines are not measured then (recheck 13).
    pub(super) fn parts_cut_at(mut self, cuts: &[f64]) -> Self {
        if let Some(frame) = self.subtitles.as_mut().filter(|frame| frame.unmeasurable.is_none()) {
            if frame.burns.iter().any(|burn| restacked(&burn.lines, cuts)) {
                frame.unmeasurable = Some(PARTS.into());
            }
        }
        self
    }

    /// The published file is still the one this plan is for (or no stamp was taken).
    fn same_file(&self) -> bool {
        self.stamp.is_none() || stamp_of(&self.video) == self.stamp
    }
}

/// The stamp of the file at `video` (None when it is not a readable file).
pub(super) fn stamp_of(video: &Path) -> Option<Stamp> {
    let file = fs::File::open(video).ok()?;
    let metadata = file.metadata().ok()?;
    if !metadata.is_file() {
        return None;
    }
    let modified = metadata.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    let (volume, index) = identity(&file, &metadata)?;
    Some([metadata.len(), u64::try_from(modified.as_nanos()).ok()?, volume, index])
}

/// Where the opened file lives: its device and inode.
#[cfg(unix)]
fn identity(_file: &fs::File, metadata: &fs::Metadata) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    Some((metadata.dev(), metadata.ino()))
}

/// Where the opened file lives: its volume serial and file index.
#[cfg(windows)]
fn identity(file: &fs::File, _metadata: &fs::Metadata) -> Option<(u64, u64)> {
    let info = file_information(file)?;
    Some((u64::from(info.volume), (u64::from(info.index_high) << 32) | u64::from(info.index_low)))
}

#[cfg(not(any(unix, windows)))]
fn identity(_file: &fs::File, _metadata: &fs::Metadata) -> Option<(u64, u64)> {
    Some((0, 0))
}

/// The stamp a saved report carries, if any.
fn stamp_in(report: &Value) -> Option<Stamp> {
    let stamp = report["stamp"].as_array().filter(|stamp| stamp.len() == 4)?;
    Some([stamp[0].as_u64()?, stamp[1].as_u64()?, stamp[2].as_u64()?, stamp[3].as_u64()?])
}

/// Some line of one layer is on screen across a cut and shared the screen with another line of that layer before it:
/// the part after the cut places it afresh, where one libass for the whole export keeps the place it took.
fn restacked(lines: &[Cue], cuts: &[f64]) -> bool {
    cuts.iter().map(|cut| (cut * 1000.0).round() as u64).any(|at| {
        lines.iter().enumerate().any(|(index, line)| line.start_ms < at && at < line.end_ms
            && lines.iter().enumerate().any(|(other_index, other)| other_index != index
                && other.start_ms.max(line.start_ms) < other.end_ms.min(line.end_ms).min(at)))
    })
}

/// Said when the export was rendered in parts and lines on screen together cross a cut (`Plan::parts_cut_at`).
const PARTS: &str = "video dài được xuất song song theo từng phần và có dòng phụ đề chồng nhau ngay chỗ nối hai phần, vị trí dòng ở đó có thể khác bản đo";

/// Said when the main video is shown turned (`Plan::turned_source`).
const TURNED: &str = "video gốc quay dọc bằng điện thoại (được xoay khi phát), khung vẽ phụ đề có thể khác khung xuất";

/// Said when the published file changed before its check could judge it.
const CHANGED: &str = "video đã bị thay đổi hoặc thay bằng tệp khác sau khi xuất, nên không kiểm tra theo bản xuất này";

/// How the main video's picture reaches the exported frame.
pub(super) struct Framing<'a> {
    /// The export frame before and after Khung hình (`aspect_size`). Phụ đề is burned on the first (#930).
    pub(super) export: (u32, u32),
    pub(super) out: (u32, u32),
    pub(super) fill: &'a str,
    pub(super) aspect: Option<&'a str>,
}

/// The check's plan, from the validated edit and the request (`stills[i]`: media i is a picture).
pub(super) fn plan(edit: &Edit, stills: &[bool], expected_seconds: f64, has_audio: bool, cues: &[Cue], burns: &[&BurnSubtitles],
                   framing: &Framing, fps: &str) -> Plan {
    Plan {
        video: PathBuf::new(),
        expected_seconds,
        has_audio,
        srt_end_ms: cues.iter().map(|cue| cue.end_ms).max(),
        intended_holds: intended_holds(edit, stills),
        subtitles: subtitle_frame(burns, framing).map(|frame| SubtitleFrame { rate: rate_of(fps), ..frame }),
        stamp: None,
    }
}

/// Output-time spans the edit holds on purpose: Dừng hình segments and still pictures.
fn intended_holds(edit: &Edit, stills: &[bool]) -> Vec<(f64, f64)> {
    let at = parts::positions(edit);
    edit.segments.iter().enumerate()
        .filter(|(_, segment)| segment.freeze.is_some() || stills.get(segment.media).copied().unwrap_or(false))
        .map(|(index, segment)| (at[index], at[index] + segment_length(segment)))
        .collect()
}

/// What to measure: the layers that burn lines, and the window "fill" keeps. Phụ đề is drawn on the export frame
/// after the part looks and the overlays, before the reframe (#930), so no crop, zoom, tilt or turn of a part can
/// cut it: only the frame's own edges and "fill"'s centred crop can.
fn subtitle_frame(burns: &[&BurnSubtitles], framing: &Framing) -> Option<SubtitleFrame> {
    let burns: Vec<BurnSubtitles> = burns.iter().filter(|burn| !burn.lines.is_empty()).map(|burn| (*burn).clone()).collect();
    if burns.is_empty() {
        return None;
    }
    let filled = framing.fill == "fill" && framing.out != framing.export;
    let window = if filled { fill_window(framing.export, framing.out) } else {
        (0.0, 0.0, f64::from(framing.export.0), f64::from(framing.export.1))
    };
    let why = match (filled, framing.aspect) {
        (true, Some(aspect)) => format!("khung {aspect}, cắt cho đầy"),
        (true, None) => "cắt cho đầy khung".to_owned(),
        (false, _) => EDGE.to_owned(),
    };
    Some(SubtitleFrame { burns, export: framing.export, window, why, rate: (0, 0), unmeasurable: None })
}

/// The part of the export frame `reframe_filter`'s "fill" keeps, in export pixels, as FFmpeg computes it: `scale`
/// with force_original_aspect_ratio=increase (`av_rescale` rounds to the nearest pixel), then `crop`'s centred offset,
/// which it rounds to an even pixel for yuv420p.
fn fill_window(export: (u32, u32), out: (u32, u32)) -> (f64, f64, f64, f64) {
    let (ew, eh) = (u64::from(export.0.max(1)), u64::from(export.1.max(1)));
    let (ow, oh) = (u64::from(out.0.max(1)), u64::from(out.1.max(1)));
    let rescale = |a: u64, b: u64, c: u64| (a * b + c / 2) / c;
    let (sw, sh) = (rescale(oh, ew, eh).max(ow), rescale(ow, eh, ew).max(oh));
    // `crop` evaluates (in - out) / 2, rounds it half to even (lrint), then clears the odd bit.
    let centre = |extra: u64| { let half = extra / 2; (if extra % 2 == 1 && half % 2 == 1 { half + 1 } else { half }) & !1 };
    let (x, y) = (centre(sw - ow), centre(sh - oh));
    let (fx, fy) = (ew as f64 / sw as f64, eh as f64 / sh as f64);
    (x as f64 * fx, y as f64 * fy, (x + ow) as f64 * fx, (y + oh) as f64 * fy)
}

/// A line's time as `burn_ass` writes it: `ass_time` rounds seconds to centiseconds in floating point.
fn burn_ms(ms: u64) -> u64 {
    ((ms as f64 / 1000.0) * 100.0).round() as u64 * 10
}

/// The lines (burn, line, cue) shown in each layer: each line with its first and past-the-last run.
type Shown<'a> = Vec<Vec<(usize, usize, &'a Cue)>>;

/// The burned lines on a shortened clock, from the exported video's own frame times (`frames`, in whole ms, sorted):
/// libass draws a line on the frames whose time is within its [start, end) as `burn_ass` wrote them. Each run of
/// frames showing the same lines becomes one second, in order, and every line covers the same runs as in the export;
/// a line or an overlap no frame shows is left out, as libass never draws it. libass then draws one frame per run with
/// exactly that run's lines, which appear in the export's order, so lines shown together stack as libass stacks them
/// there (a line keeps the place it took when it appeared). One script line per line: no growth with overlaps.
/// Returns when each run starts in the export (ms), and for each layer its lines with their first and past-the-last
/// run, in `burn_ass`'s order.
fn stretches<'a>(burns: &'a [BurnSubtitles], frames: &[u64]) -> (Vec<u64>, Shown<'a>) {
    let range = |line: &Cue| {
        let (start, end) = (burn_ms(line.start_ms), burn_ms(line.end_ms));
        (frames.partition_point(|&at| at < start) as u64, frames.partition_point(|&at| at < end) as u64)
    };
    let mut bounds: Vec<u64> = burns.iter().flat_map(|burn| burn.lines.iter()).map(range).filter(|(first, end)| end > first)
        .flat_map(|(first, end)| [first, end]).collect();
    bounds.sort_unstable();
    bounds.dedup();
    let index = |frame: u64| bounds.binary_search(&frame).unwrap_or(0);
    // How many lines are on screen in frames [bounds[k], bounds[k + 1]).
    let mut change = vec![0_i64; bounds.len() + 1];
    for (first, end) in burns.iter().flat_map(|burn| burn.lines.iter()).map(range).filter(|(first, end)| end > first) {
        change[index(first)] += 1;
        change[index(end)] -= 1;
    }
    let (mut shown, mut slot, mut starts) = (0_i64, vec![usize::MAX; bounds.len()], Vec::new());
    for (k, bound) in bounds.iter().enumerate() {
        shown += change[k];
        if shown > 0 {
            slot[k] = starts.len();
            starts.push(frames.get(*bound as usize).copied().unwrap_or_default());
        }
    }
    let layers = burns.iter().map(|burn| {
        let mut lines: Vec<(u64, u64, &Cue)> = burn.lines.iter().map(|line| { let (first, end) = range(line); (first, end, line) })
            .filter(|(first, end, _)| end > first).collect();
        lines.sort_by_key(|(_, _, line)| (line.start_ms, line.end_ms));
        // a line's own runs are all on screen, so they are consecutive seconds
        lines.into_iter().map(|(first, end, line)| (slot[index(first)], slot[index(end) - 1] + 1, line)).collect()
    }).collect();
    (starts, layers)
}

/// The exported video's frame times in whole ms, sorted, read by ffprobe from its packets (no decoding), tracked by
/// `running`, as FFmpeg's subtitles filter handed them to libass. Phụ đề is drawn on the export's own frames
/// (`export_script`: `fps` first), so the filter saw frame N with pts N on the 1/fps clock and computed
/// `N * (den/num) * 1000` in double, truncated: at 30 fps frame 111 is drawn at 3699 ms, not 3700 (rechecks 8 and 9).
/// `rate` is the export's frame rate (num, den); without one (a plan made before it was kept) the packet time is
/// taken on the AV_TIME_BASE clock.
fn frame_times(ffprobe: &Path, video: &Path, running: &Running, rate: (u64, u64)) -> Option<Vec<u64>> {
    use std::io::Read;
    let mut command = Command::new(ffprobe);
    command.args(["-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0"]).arg(video)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    let (stdout, id) = spawn_tracked(command, running).ok()?;
    let mut text = String::new();
    if let Some(stdout) = stdout {
        // About 12 bytes a frame: six hours at 60 fps.
        let _ = stdout.take(64 * 1024 * 1024).read_to_string(&mut text);
    }
    if !wait_tracked(running, id) {
        return None;
    }
    let seconds = text.lines().filter_map(|line| line.trim().trim_end_matches(',').parse::<f64>().ok())
        .filter(|seconds| seconds.is_finite() && *seconds >= 0.0);
    let mut times: Vec<u64> = seconds.map(|seconds| drawn_ms(seconds, rate)).collect();
    times.sort_unstable();
    times.dedup();
    (!times.is_empty()).then_some(times)
}

/// The ms libass was given for the frame shown at `seconds` in an export at `rate` (num, den), as vf_subtitles
/// computes it from that frame's pts on the 1/fps clock.
fn drawn_ms(seconds: f64, (num, den): (u64, u64)) -> u64 {
    if num == 0 || den == 0 {
        return libass_ms((seconds * 1e6).round());
    }
    let frame = (seconds * num as f64 / den as f64).round();
    (frame * (den as f64 / num as f64) * 1000.0) as u64
}

/// The ms libass is given for a frame at `micros` on the AV_TIME_BASE clock, computed as vf_subtitles does.
fn libass_ms(micros: f64) -> u64 {
    (micros * (1.0 / 1_000_000.0) * 1000.0) as u64
}

/// An FFmpeg frame rate ("30/1", "30000/1001", "25") as (num, den); (0, 0) when it is not one.
fn rate_of(fps: &str) -> (u64, u64) {
    let (num, den) = fps.split_once('/').unwrap_or((fps, "1"));
    match (num.trim().parse::<u64>(), den.trim().parse::<u64>()) {
        (Ok(num), Ok(den)) if num > 0 && den > 0 => (num, den),
        _ => (0, 0),
    }
}

/// `bbox`'s log: for each frame where something was drawn, its frame number and the drawn area (x0, y0, x1, y1,
/// inclusive pixels).
fn read_boxes(text: &str) -> HashMap<usize, (i64, i64, i64, i64)> {
    let mut boxes = HashMap::new();
    for line in text.lines().filter(|line| line.contains("Parsed_bbox")) {
        let value = |name: &str| line.split_whitespace().find_map(|word| word.strip_prefix(name)).and_then(|value| value.parse::<i64>().ok());
        if let (Some(frame), Some(x0), Some(x1), Some(y0), Some(y1)) = (value("n:"), value("x1:"), value("x2:"), value("y1:"), value("y2:")) {
            if let Ok(frame) = usize::try_from(frame) {
                boxes.insert(frame, (x0, y0, x1, y1));
            }
        }
    }
    boxes
}

/// The measured result: a stretch's drawing must stay off the frame's outermost pixels (ink there runs on past the
/// edge) and inside the kept window. A stretch where libass drew nothing is not measured, never called fine.
fn fit_of(frame: &SubtitleFrame, starts: &[u64], boxes: &HashMap<usize, (i64, i64, i64, i64)>) -> SubtitleFit {
    let (width, height) = (f64::from(frame.export.0), f64::from(frame.export.1));
    let (wx0, wy0, wx1, wy1) = frame.window;
    let (mut cut, mut edge, mut cropped, mut blank) = (Vec::new(), false, false, 0);
    for (index, &start) in starts.iter().enumerate() {
        let Some(&(cx0, cy0, cx1, cy1)) = boxes.get(&index) else {
            blank += 1;
            continue;
        };
        // The boxes are on the measure frame, twice the export frame with it in the middle: ink on that frame's own
        // edge may run on further, and is outside the export frame anyway.
        let at_edge = cx0 <= 0 || cy0 <= 0 || cx1 >= 2 * i64::from(frame.export.0) - 1 || cy1 >= 2 * i64::from(frame.export.1) - 1;
        let (x0, y0) = (cx0 as f64 - width / 2.0, cy0 as f64 - height / 2.0);
        let (x1, y1) = (cx1 as f64 + 1.0 - width / 2.0, cy1 as f64 + 1.0 - height / 2.0);
        // half a pixel either way: an odd-sized export frame sits half a pixel off the measure frame's grid
        let on_frame = !at_edge && x0 >= -0.5 && y0 >= -0.5 && x1 <= width + 0.5 && y1 <= height + 0.5;
        let in_window = x0 >= wx0 - 0.5 && y0 >= wy0 - 0.5 && x1 <= wx1 + 0.5 && y1 <= wy1 + 0.5;
        if !on_frame {
            edge = true;
        } else if !in_window {
            cropped = true;
        }
        if !(on_frame && in_window) {
            cut.push(start);
        }
    }
    cut.sort_unstable();
    cut.dedup();
    let unseen = if blank > 0 { format!(" {blank} đoạn chưa đo được (libass không vẽ ra chữ).") } else { String::new() };
    if cut.is_empty() {
        return if blank == 0 {
            SubtitleFit { ok: Some(true), detail: "phụ đề nằm trong khung — đo trên chữ libass vẽ".into(), at_ms: Vec::new() }
        } else {
            SubtitleFit { ok: None, detail: format!("không thấy đoạn nào bị cắt.{unseen}"), at_ms: Vec::new() }
        };
    }
    // The frame's own edge, the "fill" crop, or both.
    let why = match (edge, cropped) {
        (true, false) => EDGE.to_owned(),
        (false, _) => frame.why.clone(),
        (true, true) => format!("{EDGE}; {}", frame.why),
    };
    let named: Vec<String> = cut.iter().take(NAMED).map(|ms| clock(*ms as f64 / 1000.0)).collect();
    let more = if cut.len() > NAMED { "…" } else { "" };
    let detail = format!("phụ đề bị cắt mép ở {} đoạn ({why}), từ: {}{more} — đo trên chữ libass vẽ.{unseen}", cut.len(), named.join(", "));
    cut.truncate(MAX_SPANS);
    SubtitleFit { ok: Some(false), detail, at_ms: cut }
}

const EDGE: &str = "chữ vượt ra ngoài khung hình";

/// Where libass draws the burned lines: the export's own layers painted white (`measure_ass`) on a black frame
/// twice the export's size around it, one frame per stretch (`stretches`, on the video's own frame times), and
/// FFmpeg's `bbox` around what was drawn. Never decodes or changes the video.
fn measure_subtitles(work: &Path, ffmpeg: &Path, frame: &SubtitleFrame, video: &Path, dir: &Path, running: &Running) -> SubtitleFit {
    let unmeasured = |why: String| SubtitleFit { ok: None, detail: format!("chưa đo được phụ đề: {why}"), at_ms: Vec::new() };
    if let Some(why) = &frame.unmeasurable {
        return unmeasured(why.clone());
    }
    let Some(frames) = frame_times(&ffprobe_beside(ffmpeg), video, running, frame.rate) else {
        return unmeasured("không đọc được thời điểm các khung hình".into());
    };
    let (starts, layers) = stretches(&frame.burns, &frames);
    if starts.is_empty() {
        return SubtitleFit { ok: None, detail: "không câu phụ đề nào hiện đủ một khung hình".into(), at_ms: Vec::new() };
    }
    let mut filters = Vec::new();
    for (index, (burn, shown)) in frame.burns.iter().zip(&layers).enumerate() {
        let file = dir.join(format!("measure-{}.ass", index + 1));
        if !owned(work, dir) || write_durable(&file, measure_ass(&burn.style, shown).as_bytes()).is_err() {
            return unmeasured("không ghi được tệp đo".into());
        }
        filters.push(subtitles_filter(&display_path(&file)));
    }
    let (width, height) = frame.export;
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "level+info", "-f", "lavfi", "-i"]
        .into_iter().map(OsString::from).collect();
    // Twice the export frame's size, the export frame in the middle (`measure_ass`): what runs past it is drawn too.
    args.push(format!("color=c=black:s={}x{}:r=1:d={}", 2 * width, 2 * height, starts.len()).into());
    args.extend(crate::gpu::thread_args(false));
    args.extend(["-vf".into(), format!("format=rgb24,{},format=gray,bbox", filters.join(",")).into()]);
    args.extend(["-f", "null", "-"].map(OsString::from));
    // About 150 bytes of log per stretch.
    let (result, text) = run_logged(ffmpeg, args, work, dir, "measure.log", LOG_LIMIT + 512 * starts.len() as u64, running, |_| {});
    if let Err(error) = result {
        return unmeasured(if error.is_empty() { "FFmpeg dừng giữa chừng".into() } else { error });
    }
    fit_of(frame, &starts, &read_boxes(&text))
}

// ---------- The check's own files ----------

/// A new, empty file at `name` in the check folder `dir`, for reading and writing. Whatever is at that name is
/// removed first (a link goes, never what it points to; another name of a file only loses this name), and the file
/// is made with `create_new`, which refuses anything put there in between, a link included: nothing on disk is ever
/// opened through this name. None when the folders are not plain any more or the name holds a folder.
fn fresh_file(work: &Path, dir: &Path, name: &str) -> Option<fs::File> {
    if !owned(work, dir) {
        return None;
    }
    let path = dir.join(name);
    if let Ok(metadata) = fs::symlink_metadata(&path) {
        if metadata.is_dir() && !is_link(&metadata) {
            return None;
        }
        // A link to a folder is removed as a folder on Windows.
        let _ = fs::remove_file(&path).or_else(|_| fs::remove_dir(&path));
    }
    fs::OpenOptions::new().read(true).write(true).create_new(true).open(&path).ok()
}

/// Run FFmpeg tracked by `running` with its log in a `fresh_file`, read back through the same handle (at most
/// `limit` bytes): whether it exited 0 (else its last log line) and the log.
#[allow(clippy::too_many_arguments)]
fn run_logged(ffmpeg: &Path, args: Vec<OsString>, work: &Path, dir: &Path, name: &str, limit: u64, running: &Running,
              mut line: impl FnMut(&str)) -> (Result<(), String>, String) {
    use std::io::{Read, Seek};
    // Without its log FFmpeg's findings would be lost and the pass would look clean: then it does not run.
    let Some(mut log) = fresh_file(work, dir, name) else {
        return (Err(fail("Không tạo được nhật ký kiểm tra")), String::new());
    };
    let Ok(stderr) = log.try_clone() else {
        return (Err(fail("Không tạo được nhật ký kiểm tra")), String::new());
    };
    let mut command = Command::new(ffmpeg);
    command.args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::from(stderr));
    let Ok((stdout, id)) = spawn_tracked(command, running) else {
        return (Err(fail("Không chạy được FFmpeg")), String::new());
    };
    for text in stdout.map(|stdout| BufReader::new(stdout).lines()).into_iter().flatten().map_while(Result::ok) {
        line(&text);
    }
    let ok = wait_tracked(running, id);
    let mut bytes = Vec::new();
    if log.rewind().is_ok() {
        let _ = (&mut log).take(limit).read_to_end(&mut bytes);
    }
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let last = || text.lines().rev().find(|line| !line.trim().is_empty()).map(|line| line.trim().chars().take(240).collect::<String>());
    let result = if ok { Ok(()) } else { Err(last().unwrap_or_default()) };
    (result, text)
}

/// ffprobe on `video`, tracked by `running` like FFmpeg, so Dừng kiểm tra and closing DubFlow end it too.
fn probe_tracked(ffprobe: &Path, video: &Path, running: &Running) -> Option<Probe> {
    use std::io::Read;
    let mut command = Command::new(ffprobe);
    command.args(PROBE_ARGS).arg(video).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    let (stdout, id) = spawn_tracked(command, running).ok()?;
    let mut text = String::new();
    if let Some(stdout) = stdout {
        let _ = stdout.take(CHECK_LIMIT * 4).read_to_string(&mut text);
    }
    if !wait_tracked(running, id) {
        return None;
    }
    parse_probe(&text)
}

// ---------- The FFmpeg pass ----------

/// One decode of the whole file: black and frozen stretches on a small copy, and
/// the true peak when there is sound. `threads` is `gpu::thread_args(true)`.
fn check_args(video: &Path, has_audio: bool, threads: Vec<OsString>) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-loglevel", "level+info", "-nostats", "-progress", "pipe:1"]
        .into_iter().map(OsString::from).collect();
    args.extend(threads);
    args.push("-i".into());
    args.push(video.into());
    let mut graph = format!("[0:v:0]{SMALL},{BLACK},{FREEZE}[v]");
    if has_audio {
        graph.push_str(&format!(";[0:a:0]{PEAK}[a]"));
    }
    args.extend(["-filter_complex".into(), graph.into(), "-map".into(), "[v]".into()]);
    if has_audio {
        args.extend(["-map".into(), "[a]".into()]);
    }
    args.extend(["-f", "null", "-"].map(OsString::from));
    args
}

/// What FFmpeg's log (`level+info`) says.
#[derive(Debug, Default, PartialEq)]
struct LogFacts {
    black: Vec<(f64, f64)>,
    freeze: Vec<(f64, f64)>,
    /// `loudnorm`'s `input_tp`; None when not printed. "-inf" (digital silence) is kept as minus infinity.
    peak: Option<f64>,
    errors: usize,
    first_error: Option<String>,
    fatal: bool,
}

fn field(line: &str, name: &str) -> Option<f64> {
    let rest = &line[line.find(name)? + name.len()..];
    rest.split_whitespace().next()?.parse::<f64>().ok().filter(|value| value.is_finite())
}

/// The log's facts; a freeze still open at the end runs to `decoded_end`
/// (the same rule as an open silence in analysis.py).
fn read_log(text: &str, decoded_end: f64) -> LogFacts {
    let mut facts = LogFacts::default();
    let mut open: Option<f64> = None;
    for line in text.lines() {
        // By the filters' own keys, not by the "[blackdetect @" prefix: FFmpeg names a filter
        // instance as it likes ("[Parsed_blackdetect_1 @"), and that name may change between builds.
        if let (Some(start), Some(end)) = (field(line, "black_start:"), field(line, "black_end:")) {
            facts.black.push((start, end));
        } else if let Some(start) = field(line, "freezedetect.freeze_start:") {
            open = Some(start);
        } else if let Some(end) = field(line, "freezedetect.freeze_end:") {
            if let Some(start) = open.take() {
                facts.freeze.push((start, end));
            }
        }
        for level in ["[error]", "[fatal]"] {
            if let Some(at) = line.find(level) {
                facts.errors += 1;
                facts.fatal |= level == "[fatal]";
                if facts.first_error.is_none() {
                    facts.first_error = Some(line[at + level.len()..].trim().chars().take(200).collect());
                }
                break;
            }
        }
    }
    if let Some(start) = open {
        facts.freeze.push((start, decoded_end.max(start)));
    }
    // The last `loudnorm` JSON block (measure.py reads it the same way).
    if let Some(key) = text.rfind("\"input_i\"") {
        if let (Some(open), Some(close)) = (text[..key].rfind('{'), text[key..].find('}')) {
            facts.peak = serde_json::from_str::<Value>(&text[open..=key + close]).ok()
                .and_then(|block| block["input_tp"].as_str().and_then(|value| value.trim().parse::<f64>().ok()))
                .filter(|value| !value.is_nan() && *value != f64::INFINITY);
        }
    }
    facts
}

/// `spans` without the parts inside any of `cut`.
fn subtract(spans: &[(f64, f64)], cut: &[(f64, f64)]) -> Vec<(f64, f64)> {
    let mut pieces = spans.to_vec();
    for &(from, to) in cut {
        pieces = pieces.into_iter().flat_map(|(start, end)| {
            if to <= start || from >= end {
                return vec![(start, end)];
            }
            [(start, from), (to, end)].into_iter().filter(|(start, end)| end > start).collect()
        }).collect();
    }
    pieces
}

/// Frozen stretches that are neither black nor held on purpose, at least `MIN_FROZEN` long.
fn frozen_spans(freeze: &[(f64, f64)], black: &[(f64, f64)], holds: &[(f64, f64)]) -> Vec<(f64, f64)> {
    subtract(&subtract(freeze, black), holds).into_iter().filter(|(start, end)| end - start >= MIN_FROZEN - 1e-9).collect()
}

// ---------- The result ----------

#[derive(Debug, Clone, PartialEq, Serialize)]
pub(super) struct Check {
    id: &'static str,
    label: &'static str,
    ok: Option<bool>,
    detail: String,
}

fn check(id: &'static str, label: &'static str, ok: Option<bool>, detail: impl Into<String>) -> Check {
    Check { id, label, ok, detail: detail.into() }
}

#[derive(Debug, Serialize)]
pub(super) struct ExportCheck {
    schema_version: u32,
    /// Never "final_qc": the UI never maps this to Final QC's states.
    kind: &'static str,
    advisory: bool,
    /// RUNNING | DONE | STOPPED | INTERRUPTED | UNAVAILABLE.
    state: &'static str,
    percent: f64,
    video: String,
    checks: Vec<Check>,
    warnings: Vec<&'static str>,
    unmeasured: Vec<&'static str>,
    black: Vec<[f64; 2]>,
    frozen: Vec<[f64; 2]>,
    /// Why the check could not run (UNAVAILABLE).
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    /// RUNNING but still waiting for the check before it to end.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    waiting: bool,
    checked_at: u64,
    /// The checked file's size and modified time (ns) when it was published: `attach` shows the report only on the
    /// export whose file this still is, never on a later export written to the same name (recheck 9).
    #[serde(skip_serializing_if = "Option::is_none")]
    stamp: Option<Stamp>,
}

impl ExportCheck {
    fn new(video: &Path, state: &'static str) -> Self {
        Self { schema_version: 1, kind: "export_check", advisory: true, state, percent: 0.0, video: display_path(video), checks: Vec::new(),
            warnings: Vec::new(), unmeasured: Vec::new(), black: Vec::new(), frozen: Vec::new(), error: None, waiting: false,
            checked_at: unix_now(), stamp: None }
    }

    /// A report on `plan`'s published file, carrying its stamp.
    fn of(plan: &Plan, state: &'static str) -> Self {
        Self { stamp: plan.stamp, ..Self::new(&plan.video, state) }
    }

    fn with_checks(mut self, checks: Vec<Check>, black: &[(f64, f64)], frozen: &[(f64, f64)]) -> Self {
        self.warnings = checks.iter().filter(|check| check.ok == Some(false)).map(|check| check.id).collect();
        self.unmeasured = checks.iter().filter(|check| check.ok.is_none()).map(|check| check.id).collect();
        let pairs = |spans: &[(f64, f64)]| spans.iter().take(MAX_SPANS).map(|&(start, end)| [round3(start), round3(end)]).collect();
        (self.black, self.frozen) = (pairs(black), pairs(frozen));
        self.checks = checks;
        self.percent = 100.0;
        self
    }
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

/// Seconds as the UI writes them: "3,0".
fn seconds(value: f64) -> String {
    format!("{value:.1}").replace('.', ",")
}

/// "0:04", "12:30", "1:02:03".
fn clock(value: f64) -> String {
    let total = value.max(0.0).floor() as u64;
    let (hours, minutes, secs) = (total / 3600, total / 60 % 60, total % 60);
    if hours > 0 { format!("{hours}:{minutes:02}:{secs:02}") } else { format!("{minutes}:{secs:02}") }
}

fn span_detail(spans: &[(f64, f64)], what: &str) -> String {
    let named: Vec<String> = spans.iter().take(NAMED)
        .map(|&(start, end)| format!("{what} {} giây ở {}–{}", seconds(end - start), clock(start), clock(end))).collect();
    let more = if spans.len() > NAMED { format!(" và {} đoạn khác", spans.len() - NAMED) } else { String::new() };
    format!("{}{more}", named.join("; "))
}

/// What one decode pass found, as it ended.
struct Pass {
    /// FFmpeg exited 0.
    finished: bool,
    /// The last `out_time_us` plus one frame: how far the file was decoded.
    decoded_end: f64,
    /// FFmpeg's last log line when it failed.
    last_line: String,
}

/// Stretches of output time, in seconds.
type Spans = Vec<(f64, f64)>;

/// The checks from the probe (None: not readable), the pass, the log and the subtitle measure, with the black and
/// frozen stretches.
fn checks_of(plan: &Plan, probe: Option<&Probe>, pass: &Pass, log: &LogFacts, subtitles: &SubtitleFit) -> (Vec<Check>, Spans, Spans) {
    let mut checks = Vec::new();
    let duration = probe.map(|probe| probe.duration);
    let reached = duration.is_none_or(|duration| pass.decoded_end >= duration - SYNC_TOLERANCE_MS / 1000.0);
    checks.push(if pass.finished && log.errors == 0 && reached {
        check("decode", "Giải mã", Some(true), format!("đọc hết {} giây, không có lỗi", seconds(pass.decoded_end)))
    } else if log.errors > 0 {
        let first = log.first_error.as_deref().unwrap_or_default();
        check("decode", "Giải mã", Some(false), format!("{} lỗi khi giải mã, lỗi đầu: {first}", log.errors))
    } else if !pass.finished {
        check("decode", "Giải mã", Some(false), format!("FFmpeg dừng giữa chừng: {}", pass.last_line))
    } else {
        check("decode", "Giải mã", Some(false), format!("chỉ đọc được {} trên {} giây", seconds(pass.decoded_end), seconds(duration.unwrap_or_default())))
    });
    checks.extend(output_checks(plan, duration.map(|seconds| (seconds * 1000.0).round() as i64), probe.map(|probe| probe.has_audio),
        if pass.finished { log.peak } else { None }));
    // Without a whole pass the filters saw only part of the picture.
    let (black, frozen) = if pass.finished {
        (log.black.clone(), frozen_spans(&log.freeze, &log.black, &plan.intended_holds))
    } else {
        (Vec::new(), Vec::new())
    };
    checks.push(match (pass.finished, black.is_empty()) {
        (false, _) => check("black", "Hình đen", None, "chưa đo: video không đọc hết"),
        (true, true) => check("black", "Hình đen", Some(true), "không có đoạn đen từ 2 giây"),
        (true, false) => check("black", "Hình đen", Some(false), span_detail(&black, "đen")),
    });
    checks.push(match (pass.finished, frozen.is_empty()) {
        (false, _) => check("frozen", "Hình đứng", None, "chưa đo: video không đọc hết"),
        (true, true) => check("frozen", "Hình đứng", Some(true), "không có đoạn đứng hình từ 2 giây ngoài các đoạn dừng hình cố ý"),
        (true, false) => check("frozen", "Hình đứng", Some(false), span_detail(&frozen, "đứng hình")),
    });
    checks.push(check("subtitle_frame", "Phụ đề trong khung", subtitles.ok, subtitles.detail.clone()));
    (checks, black, frozen)
}

/// Final QC's `check_output` rules for an editor export: its length against the
/// edit, its audio stream, its true peak and its subtitles. `actual_ms` None: not read.
fn output_checks(plan: &Plan, actual_ms: Option<i64>, has_audio: Option<bool>, peak: Option<f64>) -> Vec<Check> {
    let expected_ms = (plan.expected_seconds * 1000.0).round() as i64;
    let mut checks = vec![match actual_ms {
        None => check("duration", "Thời lượng", None, "chưa đo"),
        Some(actual) => check("duration", "Thời lượng", Some((actual - expected_ms).abs() as f64 <= SYNC_TOLERANCE_MS),
            format!("video {actual} ms, bản sửa {expected_ms} ms")),
    }];
    if plan.has_audio {
        checks.push(match has_audio {
            None => check("audio_stream", "Có tiếng", None, "chưa đo"),
            Some(true) => check("audio_stream", "Có tiếng", Some(true), "video xuất ra có âm thanh"),
            Some(false) => check("audio_stream", "Có tiếng", Some(false), "bản sửa có tiếng nhưng video xuất ra không có âm thanh"),
        });
    } else {
        checks.push(check("audio_stream", "Có tiếng", Some(true), "bản sửa không có tiếng"));
    }
    // A silent edit exported silent has no peak to measure: that is not "chưa đo".
    if plan.has_audio || has_audio == Some(true) {
        checks.push(match peak {
            None => check("audio", "Âm thanh", None, "chưa đo đỉnh âm"),
            // A sound track that is there but silent: a warning when the edit has sound, as intended when it has none.
            Some(top) if top < SILENT_DBTP => {
                let level = if top.is_finite() { format!("đỉnh {top:.1} dBTP") } else { "không có tiếng nào".to_owned() };
                if plan.has_audio {
                    check("audio", "Âm thanh", Some(false), format!("bản sửa có tiếng nhưng âm thanh xuất ra im lặng ({level})"))
                } else {
                    check("audio", "Âm thanh", Some(true), format!("âm thanh im lặng, đúng như bản sửa ({level})"))
                }
            }
            Some(top) => check("audio", "Âm thanh", Some(top <= CLIP_DBTP), format!("đỉnh {top:.1} dBTP")),
        });
    }
    checks.push(match plan.srt_end_ms {
        None => check("captions", "Phụ đề", None, "không có tệp phụ đề"),
        Some(end) => {
            let limit = actual_ms.unwrap_or(expected_ms) as f64 + SYNC_TOLERANCE_MS;
            check("captions", "Phụ đề", Some(end as f64 <= limit), format!("phụ đề cuối kết thúc ở {end} ms"))
        }
    });
    checks
}

// ---------- Running it ----------

/// FFprobe beside FFmpeg (the app's runtime folder, or the test's FFmpeg).
fn ffprobe_beside(ffmpeg: &Path) -> PathBuf {
    let name = ffmpeg.file_name().map(|name| name.to_string_lossy().replace("ffmpeg", "ffprobe")).unwrap_or_else(|| "ffprobe".into());
    ffmpeg.with_file_name(name)
}

/// A plain folder (no link) at `dir`.
fn plain(dir: &Path) -> bool {
    fs::symlink_metadata(dir).is_ok_and(|metadata| metadata.is_dir() && !is_link(&metadata))
}

/// The check folder `dir` (`work/checks/<key>`) may be written now: `work`, `checks` and `dir` are each a plain
/// folder, looked at again right before each write (a `checks` swapped for a link during a long decode would
/// otherwise lead writes elsewhere; recheck 8).
fn owned(work: &Path, dir: &Path) -> bool {
    unlinked(work) && dir.parent().is_some_and(|checks| checks == work.join(CHECKS_DIR) && plain(checks)) && plain(dir)
}

/// The check folder of `video` below `work`.
fn check_dir(work: &Path, video: &Path) -> PathBuf {
    work.join(CHECKS_DIR).join(file_key(video))
}

/// Written only while `work`, `checks` and the folder are still plain folders.
fn save(work: &Path, dir: &Path, report: &ExportCheck) {
    if owned(work, dir) {
        let _ = write_durable(&dir.join("check.json"), &serde_json::to_vec(report).unwrap_or_default());
    }
}

/// Check the published `plan.video`. Every outcome leaves the video exactly as exported.
pub(super) fn run(work: &Path, ffmpeg: &Path, plan: Plan) {
    let dir = check_dir(work, &plan.video);
    if !unlinked(work) || plain_folder(&work.join(CHECKS_DIR)).is_err() || plain_folder(&dir).is_err() {
        return;
    }
    // The folder held for the whole check, then its own lease: a new export of the same video is never refused
    // because of a check.
    let Some(pinned) = pin(work, &dir) else { return };
    let Some(lease) = own_lease(work, &dir) else { return };
    let running = Arc::new(Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) });
    if !register(&CHECKING, &CLOSING, &dir, &running) {
        return; // DubFlow is closing
    }
    if owned(work, &dir) {
        let _ = write_durable(&dir.join("plan.json"), &serde_json::to_vec(&plan).unwrap_or_default());
    }
    let report = match wait_turn(work, &dir, &plan, &running) {
        Turn::Taken(turn) => {
            let report = check_once(work, ffmpeg, &plan, &dir, &running);
            drop(turn);
            report
        }
        Turn::Stopped => ExportCheck::of(&plan, "STOPPED"),
        Turn::Full => ExportCheck { error: Some(format!("bỏ qua vì đã có {MAX_WAITING} video khác đang chờ kiểm tra")),
            ..ExportCheck::of(&plan, "UNAVAILABLE") },
    };
    // Stopped because DubFlow is closing, not by Dừng kiểm tra: said as such.
    let report = if report.state == "STOPPED" && CLOSING.load(Ordering::SeqCst) { ExportCheck::of(&plan, "INTERRUPTED") } else { report };
    save(work, &dir, &report);
    if let Ok(mut checking) = CHECKING.lock() {
        checking.remove(&dir);
    }
    drop(lease);
    drop(pinned);
    prune(work);
}

/// The check folder's lease, held (on Windows with no sharing) while the check runs. Whatever is at that name is
/// removed first (a link, or another name of a file elsewhere, which only loses this name) and the lease is the new
/// empty file `create_new` makes, so it is always the check's own file and no file elsewhere is ever held. A lease
/// another window holds cannot be removed on Windows, so `create_new` fails and None says the check is busy.
fn own_lease(work: &Path, dir: &Path) -> Option<fs::File> {
    if !owned(work, dir) {
        return None;
    }
    let path = dir.join("lease");
    if let Ok(metadata) = fs::symlink_metadata(&path) {
        if metadata.is_dir() && !is_link(&metadata) {
            return None;
        }
        let _ = fs::remove_file(&path).or_else(|_| fs::remove_dir(&path));
    }
    // The new file is the lease itself (on Windows opened without sharing, as `work_lease::take` does), so no
    // second open of the name ever follows: when it cannot be made new, the check does not run.
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(0);
    }
    let lease = options.open(&path).ok()?;
    lease.metadata().is_ok_and(|metadata| metadata.is_file() && !is_link(&metadata)).then_some(lease)
}

/// Put `running` in `checking` under `dir`, unless DubFlow is `closing` (then false).
/// `stop_every` sets `closing` before it takes the lock, so a check either is in the
/// registry it stops, or sees `closing` and never starts.
fn register(checking: &Mutex<HashMap<PathBuf, Arc<Running>>>, closing: &AtomicBool, dir: &Path, running: &Arc<Running>) -> bool {
    match checking.lock() {
        Ok(mut checking) if !closing.load(Ordering::SeqCst) => {
            checking.insert(dir.to_path_buf(), running.clone());
            true
        }
        Ok(_) => false,
        Err(_) => !closing.load(Ordering::SeqCst),
    }
}

/// Stop every check in `checking` (their FFmpeg is killed) and let no new one start.
fn stop_every(checking: &Mutex<HashMap<PathBuf, Arc<Running>>>, closing: &AtomicBool) {
    closing.store(true, Ordering::SeqCst);
    let entries: Vec<Arc<Running>> = checking.lock().map(|checking| checking.values().cloned().collect()).unwrap_or_default();
    for entry in entries {
        stop(&entry);
    }
}

/// DubFlow is closing: no check's FFmpeg may outlive it and keep the user's video open.
pub(super) fn stop_all() {
    stop_every(&CHECKING, &CLOSING);
}

/// This window's turn to check (`TURN`), or None when "Dừng kiểm tra" or closing stopped it while it waited.
/// While it waits its check.json says RUNNING and `waiting`, so the UI keeps polling and can stop it.
fn wait_turn(work: &Path, dir: &Path, plan: &Plan, running: &Running) -> Turn<'static> {
    wait_turn_in(&TURN, &WAITING, MAX_WAITING, running, || save(work, dir, &ExportCheck { waiting: true, ..ExportCheck::of(plan, "RUNNING") }))
}

/// Taking the one check turn.
enum Turn<'a> {
    Taken(std::sync::MutexGuard<'a, ()>),
    /// Dừng kiểm tra, or DubFlow closing, while waiting.
    Stopped,
    /// `max` checks were already waiting: this one is not queued (each waits on its own export's thread).
    Full,
}

/// Wait for `turn` unless `max` others already wait in `waiting` (then `Full` at once); `announce` once on waiting.
fn wait_turn_in<'a>(turn: &'a Mutex<()>, waiting: &AtomicUsize, max: usize, running: &Running, announce: impl FnOnce()) -> Turn<'a> {
    let take = || match turn.try_lock() {
        Ok(guard) => Some(guard),
        Err(std::sync::TryLockError::Poisoned(poisoned)) => Some(poisoned.into_inner()),
        Err(std::sync::TryLockError::WouldBlock) => None,
    };
    if let Some(guard) = take() {
        return Turn::Taken(guard);
    }
    if waiting.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |count| (count < max).then_some(count + 1)).is_err() {
        return Turn::Full;
    }
    announce();
    let taken = loop {
        if running.cancelled.load(Ordering::SeqCst) {
            break Turn::Stopped;
        }
        if let Some(guard) = take() {
            break Turn::Taken(guard);
        }
        std::thread::sleep(Duration::from_millis(200));
    };
    waiting.fetch_sub(1, Ordering::SeqCst);
    taken
}

fn check_once(work: &Path, ffmpeg: &Path, plan: &Plan, dir: &Path, running: &Running) -> ExportCheck {
    let unavailable = |error: String| ExportCheck { error: Some(error), ..ExportCheck::of(plan, "UNAVAILABLE") };
    if !ffmpeg.is_file() {
        return unavailable("không tìm thấy FFmpeg".into());
    }
    if !plan.same_file() {
        return unavailable(CHANGED.into());
    }
    save(work, dir, &ExportCheck::of(plan, "RUNNING"));
    let probe = probe_tracked(&ffprobe_beside(ffmpeg), &plan.video, running);
    if running.cancelled.load(Ordering::SeqCst) {
        return ExportCheck::of(plan, "STOPPED");
    }
    let fps = probe.as_ref().map_or(30.0, |probe| fps_number(&probe.fps));
    let total = probe.as_ref().map_or(plan.expected_seconds, |probe| probe.duration);
    let has_audio = probe.as_ref().is_some_and(|probe| probe.has_audio);
    let mut last_us: Option<i64> = None;
    let mut written = Instant::now();
    let args = check_args(&plan.video, has_audio, crate::gpu::thread_args(true));
    let (result, text) = run_logged(ffmpeg, args, work, dir, "ffmpeg.log", LOG_LIMIT, running, |line| {
        if let Some(value) = line.strip_prefix("out_time_us=") {
            if let Ok(us) = value.trim().parse::<i64>() {
                last_us = Some(us.max(0));
            }
        } else if line.starts_with("progress=") && written.elapsed() >= Duration::from_secs(1) {
            let percent = last_us.map_or(0.0, |us| progress_percent(us, total)).floor();
            save(work, dir, &ExportCheck { percent, ..ExportCheck::of(plan, "RUNNING") });
            written = Instant::now();
        }
    });
    if running.cancelled.load(Ordering::SeqCst) {
        return ExportCheck::of(plan, "STOPPED");
    }
    if let Err(error) = &result {
        if last_us.is_none() && text.trim().is_empty() {
            // Not a word logged and no progress: FFmpeg never started.
            return unavailable(if error.is_empty() { "không chạy được FFmpeg".into() } else { error.clone() });
        }
    }
    let decoded_end = last_us.map_or(0.0, |us| us as f64 / 1e6 + 1.0 / fps.max(1.0));
    let log = read_log(&text, decoded_end);
    let pass = Pass { finished: result.is_ok(), decoded_end, last_line: result.err().unwrap_or_default() };
    let subtitles = match &plan.subtitles {
        Some(frame) => measure_subtitles(work, ffmpeg, frame, &plan.video, dir, running),
        None => SubtitleFit { ok: None, detail: "không có phụ đề in vào hình".into(), at_ms: Vec::new() },
    };
    if running.cancelled.load(Ordering::SeqCst) {
        return ExportCheck::of(plan, "STOPPED");
    }
    if !plan.same_file() {
        return unavailable(CHANGED.into());
    }
    let (checks, black, frozen) = checks_of(plan, probe.as_ref(), &pass, &log, &subtitles);
    ExportCheck::of(plan, "DONE").with_checks(checks, &black, &frozen)
}

/// A check folder's name: `file_key`'s 20 hex digits.
fn is_key(name: &std::ffi::OsStr) -> bool {
    name.to_str().is_some_and(|name| name.len() == 20 && name.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
}

/// Keep the newest `EXPORTS_KEPT` check folders of `work` (exports.json keeps as many entries).
/// The links are looked at again here, after the decode, and before each folder is removed:
/// only `work/checks/<key>` folders, all plain, are ever deleted. `work/checks` is held open for the whole prune
/// (`hold_checks`): on Windows it, `work` and the folders above cannot be renamed or swapped for a junction meanwhile,
/// and on Linux each folder is removed through the held handle (/proc/self/fd), so a `checks` swapped after the look
/// never leads a removal outside (Lead review 4610771246).
fn prune(work: &Path) {
    prune_with(work, &|| {});
}

/// `prune`, with `between` run after each folder is looked at and before it is removed (tests race it there).
fn prune_with(work: &Path, between: &dyn Fn()) {
    let checks = work.join(CHECKS_DIR);
    let safe = || unlinked(work) && plain(&checks);
    if !safe() {
        return;
    }
    let Some(held) = hold_checks(work) else { return };
    let base = held_base(&held, &checks);
    let Ok(entries) = fs::read_dir(&checks) else { return };
    let busy: Vec<PathBuf> = CHECKING.lock().map(|checking| checking.keys().cloned().collect()).unwrap_or_default();
    let mut folders: Vec<(SystemTime, PathBuf)> = entries.flatten().filter(|entry| is_key(&entry.file_name())).filter_map(|entry| {
        let metadata = fs::symlink_metadata(entry.path()).ok().filter(|metadata| metadata.is_dir() && !is_link(metadata))?;
        Some((metadata.modified().unwrap_or(UNIX_EPOCH), entry.path()))
    }).collect();
    if folders.len() <= EXPORTS_KEPT {
        return;
    }
    folders.sort_by_key(|(modified, _)| std::cmp::Reverse(*modified));
    for (_, folder) in folders.into_iter().skip(EXPORTS_KEPT) {
        if !busy.contains(&folder) && safe() && plain(&folder) {
            between();
            if let Some(name) = folder.file_name() {
                let _ = fs::remove_dir_all(base.join(name));
            }
        }
    }
    drop(held);
}

/// `work/checks`, held open as `pin` holds a check folder, when it is `work`'s own plain folder.
fn hold_checks(work: &Path) -> Option<fs::File> {
    let checks = work.join(CHECKS_DIR);
    let place = fs::canonicalize(work).ok().map(|work| work.join(CHECKS_DIR));
    let held = hold_folder(&checks, place)?;
    (unlinked(work) && plain(&checks)).then_some(held)
}

/// Where the folders inside the held `checks` are reached: through the handle itself on Linux, by name elsewhere
/// (on Windows the handle keeps that name from being swapped).
#[cfg(target_os = "linux")]
fn held_base(held: &fs::File, _checks: &Path) -> PathBuf {
    use std::os::unix::io::AsRawFd;
    PathBuf::from(format!("/proc/self/fd/{}", held.as_raw_fd()))
}

#[cfg(not(target_os = "linux"))]
fn held_base(_held: &fs::File, checks: &Path) -> PathBuf {
    checks.to_path_buf()
}

/// "Dừng kiểm tra": stop this window's check of `video`, if one runs.
pub(super) fn cancel(work: &Path, video: &Path) {
    if let Some(entry) = CHECKING.lock().ok().and_then(|checking| checking.get(&check_dir(work, video)).cloned()) {
        stop(&entry);
    }
}

/// The check.json of the check folder `dir` (`work/checks/<key>`). Read only while `work`, `checks` and `dir` are
/// plain folders, looked at before and after the read, through a handle `open_own` judged, and only when that handle
/// is the file at exactly `work`'s own `checks/<key>/check.json` (`at_place`): a linked `checks` swapped in between
/// the look and the open never leads the read elsewhere (Lead review 4609721830). A running check replaces its
/// check.json about once a second: a handle opened just before the new one took the name has no name any more, so it
/// is refused; the read is then tried again (`LOAD_TRIES` in all), on the file now at the name (recheck 13).
fn load_check(work: &Path, dir: &Path) -> Option<Value> {
    for try_ in 0..LOAD_TRIES {
        if try_ > 0 {
            // nothing at the name (no check made yet): nothing to try again
            fs::symlink_metadata(dir.join("check.json")).ok()?;
            // a moment for the rename that replaced the file to finish (Windows may refuse an open meanwhile)
            std::thread::sleep(Duration::from_millis(5));
        }
        if let Some(value) = load_check_once(work, dir) {
            return Some(value);
        }
    }
    None
}

/// Reads of check.json one `load_check` tries.
const LOAD_TRIES: usize = 3;

fn load_check_once(work: &Path, dir: &Path) -> Option<Value> {
    if !owned(work, dir) {
        return None;
    }
    let file = open_own(&dir.join("check.json"))?;
    if !at_place(&file, work, dir) {
        return None;
    }
    let value = read_check_json(file)?;
    owned(work, dir).then_some(value)
}

/// Where `work`'s own check.json of `dir` is, with every link above `work` resolved as the handle's own path is.
fn own_place(work: &Path, dir: &Path) -> Option<PathBuf> {
    Some(fs::canonicalize(work).ok()?.join(CHECKS_DIR).join(dir.file_name()?).join("check.json"))
}

/// The opened file is `work`'s own check.json of `dir`, by the handle's own path.
#[cfg(any(windows, target_os = "linux"))]
fn at_place(file: &fs::File, work: &Path, dir: &Path) -> bool {
    same_place(real_path(file), own_place(work, dir))
}

/// Elsewhere the folders are looked at again after the read (`load_check`).
#[cfg(all(unix, not(target_os = "linux")))]
fn at_place(_file: &fs::File, _work: &Path, _dir: &Path) -> bool {
    true
}

/// Two real paths are the same place (Windows compares them as its file system does, ignoring case).
fn same_place(real: Option<PathBuf>, place: Option<PathBuf>) -> bool {
    match (real, place) {
        (Some(real), Some(place)) if cfg!(windows) => real.to_string_lossy().eq_ignore_ascii_case(&place.to_string_lossy()),
        (Some(real), Some(place)) => real == place,
        _ => false,
    }
}

/// Where an opened file really is, from its handle (Linux: /proc/self/fd).
#[cfg(target_os = "linux")]
fn real_path(file: &fs::File) -> Option<PathBuf> {
    use std::os::unix::io::AsRawFd;
    fs::read_link(format!("/proc/self/fd/{}", file.as_raw_fd())).ok()
}

/// Where an opened file or folder really is, from its handle (GetFinalPathNameByHandleW, the form
/// `fs::canonicalize` gives).
#[cfg(windows)]
fn real_path(file: &fs::File) -> Option<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use std::os::windows::io::AsRawHandle;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetFinalPathNameByHandleW(file: *mut std::ffi::c_void, path: *mut u16, length: u32, flags: u32) -> u32;
    }
    let mut buffer = vec![0u16; 32_768];
    // SAFETY: the handle is open for the call and the buffer holds `buffer.len()` wide characters.
    let length = unsafe { GetFinalPathNameByHandleW(file.as_raw_handle(), buffer.as_mut_ptr(), buffer.len() as u32, 0) } as usize;
    (length > 0 && length < buffer.len()).then(|| PathBuf::from(std::ffi::OsString::from_wide(&buffer[..length])))
}

/// The check folder `dir` held open for as long as the check writes in it (recheck 10). On Windows it is opened
/// without delete sharing, so neither it nor any folder above it (`checks`, `work`) can be renamed, removed or
/// swapped for a link while it is held: every path the check then writes, removes or renames by stays in
/// `work`'s own `checks/<key>`. It is taken only when the handle itself is that plain folder at that place.
/// (Unix, a development platform only, cannot hold a folder this way; there the folders are looked at again
/// before each write.)
fn pin(work: &Path, dir: &Path) -> Option<fs::File> {
    if !owned(work, dir) {
        return None;
    }
    let place = fs::canonicalize(work).ok().zip(dir.file_name()).map(|(work, key)| work.join(CHECKS_DIR).join(key));
    let pinned = hold_folder(dir, place)?;
    owned(work, dir).then_some(pinned)
}

/// The folder at `folder`, opened without letting it be renamed or removed on Windows (no FILE_SHARE_DELETE), never
/// through a link at its own name, and only when its handle's real path is `place` (where that can be read).
fn hold_folder(folder: &Path, place: Option<PathBuf>) -> Option<fs::File> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ_WRITE: u32 = 0x1 | 0x2;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.share_mode(FILE_SHARE_READ_WRITE).custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let held = options.open(folder).ok()?;
    let metadata = held.metadata().ok()?;
    if !metadata.is_dir() || is_link(&metadata) {
        return None;
    }
    #[cfg(any(windows, target_os = "linux"))]
    if !same_place(real_path(&held), place) {
        return None;
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    let _ = place;
    Some(held)
}

/// A check.json file at `path` read as `load_check` reads one, without the folder checks (tests).
#[cfg(test)]
fn read_check_file(path: &Path) -> Option<Value> {
    read_check_json(open_own(path)?)
}

/// At most `CHECK_LIMIT` bytes of an opened check.json that is an export check.
fn read_check_json(file: fs::File) -> Option<Value> {
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut std::io::Read::take(file, CHECK_LIMIT + 1), &mut bytes).ok()?;
    if bytes.len() as u64 > CHECK_LIMIT {
        return None;
    }
    serde_json::from_slice::<Value>(&bytes).ok().filter(|value| value["kind"] == "export_check")
}

/// `path` opened for reading without following a link at its last step, and kept only when the opened handle
/// itself is a plain file with no other name (a hard link would be a file elsewhere) of at most `CHECK_LIMIT`
/// bytes. The handle is judged, not the path, so a name swapped after a look is never read through.
fn open_own(path: &Path) -> Option<fs::File> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // O_NONBLOCK: a FIFO planted here opens at once (and is then refused) instead of hanging the reader.
        options.custom_flags(NO_FOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = options.open(path).ok()?;
    let metadata = file.metadata().ok()?;
    (metadata.is_file() && !is_link(&metadata) && metadata.len() <= CHECK_LIMIT && links(&file, &metadata) == Some(1)).then_some(file)
}

/// How many names the opened file has.
#[cfg(unix)]
fn links(_file: &fs::File, metadata: &fs::Metadata) -> Option<u64> {
    use std::os::unix::fs::MetadataExt;
    Some(metadata.nlink())
}

/// O_NOFOLLOW | O_NONBLOCK (the values of libc, which this crate does not take in: no manifest change for two flags).
#[cfg(all(any(target_os = "linux", target_os = "android"),
          any(target_arch = "x86", target_arch = "x86_64", target_arch = "riscv64", target_arch = "loongarch64", target_arch = "s390x")))]
const NO_FOLLOW: i32 = 0x20000 | 0x800;
#[cfg(all(any(target_os = "linux", target_os = "android"),
          any(target_arch = "arm", target_arch = "aarch64", target_arch = "powerpc", target_arch = "powerpc64")))]
const NO_FOLLOW: i32 = 0x8000 | 0x800;
#[cfg(any(target_os = "macos", target_os = "ios", target_os = "freebsd", target_os = "openbsd", target_os = "netbsd", target_os = "dragonfly"))]
const NO_FOLLOW: i32 = 0x100 | 0x4;

/// How many names the opened file has.
#[cfg(windows)]
fn links(file: &fs::File, _metadata: &fs::Metadata) -> Option<u64> {
    Some(u64::from(file_information(file)?.links))
}

/// BY_HANDLE_FILE_INFORMATION of an open file.
#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
#[allow(dead_code)] // filled by Windows; only some fields are read
struct FileInformation {
    attributes: u32,
    created: [u32; 2],
    accessed: [u32; 2],
    written: [u32; 2],
    volume: u32,
    size_high: u32,
    size_low: u32,
    links: u32,
    index_high: u32,
    index_low: u32,
}

#[cfg(windows)]
fn file_information(file: &fs::File) -> Option<FileInformation> {
    use std::os::windows::io::AsRawHandle;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(file: *mut std::ffi::c_void, information: *mut FileInformation) -> i32;
    }
    let mut info = FileInformation::default();
    // SAFETY: the handle is open for the call and `info` is a valid out pointer (as in storage.rs).
    (unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } != 0).then_some(info)
}

/// A check of this window runs in `dir`.
fn ours(dir: &Path) -> bool {
    CHECKING.lock().is_ok_and(|checking| checking.contains_key(dir))
}

/// With the check's lease taken: check.json read again, because a check that ended since
/// `attach` first read it has saved its result, which is kept and never overwritten. Still
/// RUNNING with no window checking: saved and returned as INTERRUPTED. None: left as it is.
fn settle(work: &Path, dir: &Path, video: &Path) -> Option<Value> {
    if let Some(now) = load_check(work, dir).filter(|now| now["state"] != "RUNNING") {
        return Some(now);
    }
    if ours(dir) {
        return None;
    }
    let stale = ExportCheck { stamp: stamp_in(&load_check(work, dir).unwrap_or_default()), ..ExportCheck::new(video, "INTERRUPTED") };
    save(work, dir, &stale);
    serde_json::to_value(&stale).ok()
}

/// Each of the newest exports gets its check (`entry["check"]`) when one was made;
/// a RUNNING check no window runs any more is reported, and saved, as INTERRUPTED.
pub(super) fn attach(work: &Path, exports: &mut [Value]) {
    for entry in exports.iter_mut().rev().take(ATTACHED) {
        let Some(video) = entry["path"].as_str().map(PathBuf::from) else { continue };
        let dir = check_dir(work, &video);
        let Some(mut value) = load_check(work, &dir) else { continue };
        // A report on an earlier file written to the same name is not this export's.
        if stamp_in(&value).is_some_and(|stamp| stamp_of(&video).is_some_and(|now| now != stamp)) {
            continue;
        }
        // The lease file is only made in `work/checks/<key>` while all three are plain folders.
        if value["state"] == "RUNNING" && !ours(&dir) && unlinked(work) && plain(&work.join(CHECKS_DIR)) {
            // A free lease means no window is checking: the app closed mid-check.
            if let Some((pinned, lease)) = pin(work, &dir).and_then(|pinned| Some((pinned, own_lease(work, &dir)?))) {
                value = settle(work, &dir, &video).unwrap_or(value);
                drop((lease, pinned));
            }
        }
        entry["check"] = value;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn number_after(text: &str, name: &str) -> f64 {
        let line = text.lines().find(|line| line.starts_with(name)).unwrap_or_else(|| panic!("{name} is gone from final_qc.py"));
        line[name.len()..].split('#').next().unwrap().trim().trim_start_matches('=').trim().parse().unwrap()
    }

    #[test]
    fn thresholds_match_final_qc() {
        // a Windows checkout has CRLF line ends
        let final_qc = &include_str!("../../../../engine/dubflow/mastering/final_qc.py").replace("\r\n", "\n");
        assert!(final_qc.contains("\nSYNC_TOLERANCE_MS = 120\n") && final_qc.contains("\nCLIP_DBTP = 0.0\n"));
        assert_eq!(number_after(final_qc, "SYNC_TOLERANCE_MS ="), SYNC_TOLERANCE_MS);
        assert_eq!(number_after(final_qc, "CLIP_DBTP ="), CLIP_DBTP);
        // and its labels and wording, so the two read alike
        for text in ["\"duration\", \"Thời lượng\"", "\"captions\", \"Phụ đề\"", "\"audio\", \"Âm thanh\"", "chưa đo đỉnh âm", "không có tệp phụ đề",
                     "phụ đề cuối kết thúc ở"] {
            assert!(final_qc.contains(text), "{text}");
        }
        let measure = &include_str!("../../../../engine/dubflow/mastering/measure.py").replace("\r\n", "\n");
        assert!(measure.contains("target: float = -16.0, peak: float = -1.5, lra: float = 11.0"), "PEAK is measure.loudness's first pass");
        assert_eq!(PEAK, "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json");
    }

    fn by_id(checks: &[Check]) -> HashMap<&'static str, Check> {
        checks.iter().map(|check| (check.id, check.clone())).collect()
    }

    fn plain_plan(expected_seconds: f64, has_audio: bool, srt_end_ms: Option<u64>) -> Plan {
        Plan { video: PathBuf::from("v.mp4"), expected_seconds, has_audio, srt_end_ms, ..Plan::default() }
    }

    #[test]
    fn output_rules_match_final_qc_vectors() {
        // tests/mastering/test_final_qc.py OutputTests: expected 6000 ms.
        let plan = plain_plan(6.0, true, Some(5900));
        let good = by_id(&output_checks(&plan, Some(6040), Some(true), Some(-1.8)));
        assert!(good.values().all(|check| check.ok == Some(true)), "{good:?}");
        assert_eq!(good["duration"].detail, "video 6040 ms, bản sửa 6000 ms");
        assert_eq!(good["audio"].detail, "đỉnh -1.8 dBTP");
        let bad = by_id(&output_checks(&plan, Some(6500), Some(true), Some(0.4)));
        assert_eq!((bad["duration"].ok, bad["audio"].ok), (Some(false), Some(false)));
        let late = by_id(&output_checks(&plain_plan(6.0, true, Some(7000)), Some(6000), Some(true), None));
        assert_eq!(late["captions"].ok, Some(false));
        assert_eq!(late["audio"].ok, None, "not measured is said, never counted as passed");
        assert_eq!(late["audio"].detail, "chưa đo đỉnh âm");
        // the edges: exactly 120 ms off is fine, as in Final QC
        assert_eq!(by_id(&output_checks(&plan, Some(6120), Some(true), Some(0.0)))["duration"].ok, Some(true));
        assert_eq!(by_id(&output_checks(&plan, Some(6121), Some(true), Some(0.0)))["duration"].ok, Some(false));
        assert_eq!(by_id(&output_checks(&plan, Some(6000), Some(true), Some(0.0)))["audio"].ok, Some(true));
        let unread = by_id(&output_checks(&plain_plan(6.0, true, None), None, None, None));
        assert_eq!((unread["duration"].ok, unread["captions"].ok, unread["audio_stream"].ok), (None, None, None));
        assert_eq!(unread["captions"].detail, "không có tệp phụ đề");
    }

    #[test]
    fn args_decode_the_whole_file_once() {
        let text = |args: Vec<OsString>| args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect::<Vec<_>>().join(" ");
        assert_eq!(text(check_args(Path::new("out.mp4"), true, Vec::new())),
            "-nostdin -hide_banner -loglevel level+info -nostats -progress pipe:1 -i out.mp4 -filter_complex \
[0:v:0]scale=320:-2,blackdetect=d=2:pic_th=0.98:pix_th=0.10,freezedetect=n=0.001:d=2[v];\
[0:a:0]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json[a] -map [v] -map [a] -f null -");
        assert_eq!(text(check_args(Path::new("out.mp4"), false, vec!["-threads".into(), "4".into()])),
            "-nostdin -hide_banner -loglevel level+info -nostats -progress pipe:1 -threads 4 -i out.mp4 -filter_complex \
[0:v:0]scale=320:-2,blackdetect=d=2:pic_th=0.98:pix_th=0.10,freezedetect=n=0.001:d=2[v] -map [v] -f null -");
    }

    const LOG: &str = "[info] Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
[info]   freezedetect:default -> Stream #0:0 (wrapped_avframe)
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_start: 4
[blackdetect @ 0x7f932c000cc0] [info] black_start:4 black_end:7 black_duration:3
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_duration: 3
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_end: 7
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_start: 7
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_duration: 3.04
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_end: 10.04
[freezedetect @ 0x561ddbd3f400] [info] lavfi.freezedetect.freeze_start: 30.5
[Parsed_loudnorm_3 @ 0x561ddbd4da40] [info]
{
\t\"input_i\" : \"-21.83\",
\t\"input_tp\" : \"-12.65\",
\t\"input_lra\" : \"0.10\",
\t\"input_thresh\" : \"-31.83\",
\t\"output_i\" : \"-16.02\",
\t\"output_tp\" : \"-6.84\",
\t\"normalization_type\" : \"dynamic\",
\t\"target_offset\" : \"0.02\"
}
";

    #[test]
    fn reads_ffmpeg_log() {
        let facts = read_log(LOG, 33.0);
        assert_eq!(facts.black, [(4.0, 7.0)]);
        assert_eq!(facts.freeze, [(4.0, 7.0), (7.0, 10.04), (30.5, 33.0)], "a freeze open at the end runs to the decoded end");
        assert_eq!(facts.peak, Some(-12.65));
        assert_eq!((facts.errors, facts.first_error.clone(), facts.fatal), (0, None, false));
        let silent = LOG.replace("\"-12.65\"", "\"-inf\"");
        assert_eq!(read_log(&silent, 33.0).peak, Some(f64::NEG_INFINITY), "digital silence is kept, not taken for unmeasured");
        assert_eq!(read_log(&LOG.replace("\"-12.65\"", "\"nan\""), 33.0).peak, None);
        assert_eq!(read_log("[info] no loudnorm here", 1.0).peak, None);
        let damaged = "[aac @ 0x55e106711380] [error] channel element 0.0 is not allocated
[mpeg4 @ 0x55e10674edc0] [error] ac-tex damaged at 13 10
[mpeg4 @ 0x55e10674edc0] [warning] something odd
[mpeg4 @ 0x55e10674edc0] [error] Error at MB: 309
";
        let facts = read_log(damaged, 12.0);
        assert_eq!((facts.errors, facts.first_error.as_deref(), facts.fatal), (3, Some("channel element 0.0 is not allocated"), false));
        let truncated = "[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5636bbf204c0] [error] moov atom not found
[in#0 @ 0x5636bbf203c0] [error] Error opening input: Invalid data found when processing input
[fatal] Error opening input file trunc.mp4.
";
        let facts = read_log(truncated, 0.0);
        assert_eq!((facts.errors, facts.first_error.as_deref(), facts.fatal), (3, Some("moov atom not found"), true));
        // read by the filters' keys, whatever FFmpeg names their instances
        let renamed = LOG.replace("[blackdetect @", "[Parsed_blackdetect_1 @").replace("[freezedetect @", "[Parsed_freezedetect_2 @");
        assert_eq!(read_log(&renamed, 33.0), read_log(LOG, 33.0));
        let bare = "black_start:4 black_end:7 black_duration:3\nlavfi.freezedetect.freeze_start: 7\nlavfi.freezedetect.freeze_end: 10.04\n";
        let facts = read_log(bare, 33.0);
        assert_eq!((facts.black, facts.freeze), (vec![(4.0, 7.0)], vec![(7.0, 10.04)]));
    }

    #[test]
    fn frozen_spans_skip_black_and_intended_holds() {
        assert!(frozen_spans(&[(4.0, 7.0)], &[(4.0, 7.0)], &[]).is_empty(), "black is reported as black only");
        assert!(frozen_spans(&[(7.0, 10.04)], &[], &[(7.0, 10.0)]).is_empty(), "0.04 s left is under 2 s");
        assert_eq!(frozen_spans(&[(12.0, 20.0)], &[], &[(12.0, 15.0)]), [(15.0, 20.0)]);
        assert_eq!(frozen_spans(&[(0.0, 10.0)], &[(2.0, 3.0)], &[(6.0, 7.0)]), [(0.0, 2.0), (3.0, 6.0), (7.0, 10.0)]);
        assert_eq!(frozen_spans(&[(1.0, 5.0)], &[(8.0, 9.0)], &[]), [(1.0, 5.0)], "a cut elsewhere keeps the span once");
    }

    fn segment(media: usize, start: f64, end: f64) -> Segment {
        Segment { media, start, end, speed: 1.0, volume: 1.0, ..Segment::default() }
    }

    #[test]
    fn intended_holds_follow_positions() {
        // a 1 s dissolve after the held frame: the picture overlaps it
        let mut edit = Edit { segments: vec![segment(0, 0.0, 10.0), Segment { freeze: Some(3.0), ..segment(0, 10.0, 10.5) }, segment(1, 0.0, 5.0),
            segment(0, 20.0, 30.0)], transitions: vec![Transition { after: 1, kind: TransitionKind::Dissolve, duration: 1.0 }], ..Edit::default() };
        assert_eq!(intended_holds(&edit, &[false, true]), [(10.0, 13.0), (12.0, 17.0)]);
        assert_eq!(intended_holds(&edit, &[false, false]), [(10.0, 13.0)]);
        // fade through black keeps the length
        edit.transitions[0].kind = TransitionKind::Fade;
        assert_eq!(intended_holds(&edit, &[false, true]), [(10.0, 13.0), (13.0, 18.0)]);
    }

    fn style(changes: Value) -> SubtitleStyle {
        let mut value = json!({"font": "Arial", "size": 1.2, "primary": "#FFFFFF", "outline": "#000000", "box": null,
            "box_opacity": 0.6, "outline_width": 3, "bold": true, "align": "bottom"});
        value.as_object_mut().unwrap().extend(changes.as_object().unwrap().clone());
        serde_json::from_value(value).unwrap()
    }

    fn burn(style: SubtitleStyle, text: &str, start_ms: u64) -> BurnSubtitles {
        BurnSubtitles { style, lines: vec![Cue { start_ms, end_ms: start_ms + 2000, text: text.into() }] }
    }

    const LONG: &str = "Đây là một câu phụ đề khá dài để thử khung dọc";
    /// 41 Chinese characters with no space.
    const CHINESE: &str = "大家好欢迎来到我们的频道今天我们来聊一聊这座城市里最有名的一家老字号餐厅和它的故事吧";

    fn cue(start_ms: u64, end_ms: u64, text: &str) -> Cue {
        Cue { start_ms, end_ms, text: text.into() }
    }

    #[test]
    fn subtitle_frame_keeps_what_fill_keeps() {
        let wide = Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None };
        assert_eq!(subtitle_frame(&[], &wide), None);
        assert_eq!(subtitle_frame(&[&BurnSubtitles { style: style(json!({})), lines: Vec::new() }], &wide), None, "nothing burned");
        let line = burn(style(json!({})), LONG, 12_000);
        let whole = subtitle_frame(&[&line], &wide).unwrap();
        assert_eq!((whole.export, whole.window, whole.why.as_str()), ((1920, 1080), (0.0, 0.0, 1920.0, 1080.0), "chữ vượt ra ngoài khung hình"));
        // 16:9 → 9:16 cropped to fill: only the middle 607.5 px of the width stays
        let tall = Framing { export: (1920, 1080), out: (608, 1080), fill: "fill", aspect: Some("9:16") };
        let kept = subtitle_frame(&[&line], &tall).unwrap();
        let (x0, y0, x1, y1) = kept.window;
        assert!((x0 - 656.25).abs() < 1.0 && (x1 - 1263.75).abs() < 1.0 && y0 == 0.0 && y1 == 1080.0, "{:?}", kept.window);
        assert_eq!(kept.why, "khung 9:16, cắt cho đầy");
        for fill in ["blur", "black"] {
            assert_eq!(subtitle_frame(&[&line], &Framing { fill, ..tall }).unwrap().window, (0.0, 0.0, 1920.0, 1080.0), "{fill} keeps it all");
        }
        // 16:9 → 21:9 filled: the top and bottom go
        let (_, top, _, bottom) = subtitle_frame(&[&line], &Framing { out: (2520, 1080), fill: "fill", aspect: Some("21:9"), ..tall }).unwrap().window;
        assert!(top > 100.0 && bottom < 980.0, "{top} {bottom}");
        // an empty layer is left out, a second layer kept
        let empty = BurnSubtitles { style: style(json!({})), lines: Vec::new() };
        assert_eq!(subtitle_frame(&[&empty, &line, &line], &wide).unwrap().burns.len(), 2);
    }

    #[test]
    fn stretches_keep_the_order_and_overlaps_of_the_export() {
        let first = BurnSubtitles { style: style(json!({})), lines: vec![cue(4_000, 6_000, "B"), cue(0, 10_000, "A"), cue(12_000, 13_000, "C")] };
        let second = BurnSubtitles { style: style(json!({})), lines: vec![cue(5_000, 7_000, "x")] };
        let burns = [first, second];
        let (starts, layers) = stretches(&burns, &grid(25.0, 15.0));
        // A | A B | A B x | A x | A | (nothing: left out) | C
        assert_eq!(starts, [0, 4_000, 5_000, 6_000, 7_000, 12_000]);
        let spans = |layer: &Vec<(usize, usize, &Cue)>| layer.iter().map(|(first, last, line)| (*first, *last, line.text.clone())).collect::<Vec<_>>();
        assert_eq!(spans(&layers[0]), [(0, 5, "A".to_owned()), (1, 3, "B".to_owned()), (5, 6, "C".to_owned())]);
        assert_eq!(spans(&layers[1]), [(2, 4, "x".to_owned())]);
        // the measure script: painted white, each line once on the short clock, at the layer's dragged spot
        let mut dragged = style(json!({"x": 0.25, "y": 0.5, "box": "#101010", "box_opacity": 0.5}));
        dragged.primary = "#FFFF00".into();
        let script = measure_ass(&dragged, &layers[0]);
        // on the measure frame: twice the script frame, the export's in the middle, margins and the spot moved with it
        assert!(script.contains("Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{\\an5\\pos(1440,1080)}A\n"), "{script}");
        assert!(script.contains("PlayResX: 3840\nPlayResY: 2160\n") && script.contains(",2,1000,1000,588,1\n"), "{script}");
        let plain_burn = burn_ass(&BurnSubtitles { style: dragged.clone(), lines: vec![cue(0, 1_000, "A")] }).unwrap();
        assert!(plain_burn.contains("PlayResX: 1920\nPlayResY: 1080\n") && plain_burn.contains(",2,40,40,48,1\n") && plain_burn.contains("\\pos(480,540)"), "{plain_burn}");
        assert_eq!(script.matches("Dialogue:").count(), 3);
        assert!(script.contains(",&H00FFFFFF,&H000000FF,&H00FFFFFF,&H80FFFFFF,-1,"), "{script}");
        assert!(!script.contains("&H0000FFFF") && !script.contains("101010"), "no real colour left");
        // a box nobody can see stays unseen
        let clear = measure_ass(&style(json!({"box": "#000000", "box_opacity": 0.0})), &layers[1]);
        assert!(clear.contains(",&HFFFFFFFF,-1,"), "{clear}");
        // 2 000 lines that all run to the end (a broken .srt): one script line each, at most one stretch per frame
        let piled = BurnSubtitles { style: style(json!({})), lines: (0..2_000).map(|index| cue(index * 10, 60_000, "x")).collect() };
        let (starts, layers) = stretches(std::slice::from_ref(&piled), &grid(25.0, 70.0));
        assert_eq!((starts.len(), layers[0].len()), (501, 2_000), "20 s of starts at 25 fps");
        assert!(layers[0].iter().all(|(_, last, _)| *last == 501));
        assert_eq!(measure_ass(&piled.style, &layers[0]).matches("Dialogue:").count(), 2_000);
    }

    /// Frame times of a constant-rate video, as FFmpeg's subtitles filter sees them (whole ms, rounded down).
    fn grid(fps: f64, seconds: f64) -> Vec<u64> {
        (0..(fps * seconds) as u64).map(|frame| drawn_ms(frame as f64 / fps, (fps as u64, 1))).collect()
    }

    /// Only what a frame of the export shows is measured together (rechecks 6 and 7): times as `burn_ass` writes
    /// them, on the video's own frame times.
    #[test]
    fn stretches_follow_the_frames_libass_draws() {
        // the times burn_ass writes, floating point included
        for ms in (0..20_000_u64).chain([1_005, 3_725_250]) {
            let written = ass_time(ms as f64 / 1000.0);
            let (clock, cs) = written.rsplit_once('.').unwrap();
            let seconds: Vec<u64> = clock.split(':').map(|part| part.parse().unwrap()).collect();
            let back = ((seconds[0] * 60 + seconds[1]) * 60 + seconds[2]) * 1000 + cs.parse::<u64>().unwrap() * 10;
            assert_eq!(burn_ms(ms), back, "{ms} ms is written {written}");
        }
        assert_eq!(burn_ms(1_005), 1_000, "1.005 s is written 0:00:01.00 (100.49999… cs)");
        let lines = |list: Vec<Cue>| BurnSubtitles { style: style(json!({})), lines: list };
        let pairs = |layer: &Vec<(usize, usize, &Cue)>| layer.iter().map(|(first, last, _)| (*first, *last)).collect::<Vec<_>>();
        let frames = grid(25.0, 10.0);
        // 3 ms of overlap, the same centisecond: never on screen together
        let burns = [lines(vec![cue(0, 1_004, "A"), cue(1_001, 3_000, "B")])];
        let (starts, layers) = stretches(&burns, &frames);
        assert_eq!((starts, pairs(&layers[0])), (vec![0, 1_000], vec![(0, 1), (1, 2)]));
        // 10 ms of overlap between two frames at 25 fps: the same
        let burns = [lines(vec![cue(0, 4_000, "A"), cue(3_990, 7_000, "B")])];
        let (starts, layers) = stretches(&burns, &frames);
        assert_eq!((starts, pairs(&layers[0])), (vec![0, 4_000], vec![(0, 1), (1, 2)]));
        // B written 0:00:00.24 starts on the frame at 240 ms, where A still shows: measured together
        let burns = [lines(vec![cue(0, 300, "A"), cue(240, 1_000, "B")])];
        let (starts, layers) = stretches(&burns, &frames);
        assert_eq!((starts, pairs(&layers[0])), (vec![0, 240, 320], vec![(0, 2), (1, 3)]));
        // Phụ đề is drawn on the 1/fps clock and vf_subtitles truncates N × (den/num) × 1000 (rechecks 8 and 9): at
        // 30 fps frame 111 is drawn at 3699 ms, so A ending at 3700 still shows with B from 3690: measured together
        assert_eq!((drawn_ms(111.0 / 30.0, (30, 1)), drawn_ms(110.0 / 30.0, (30, 1)), drawn_ms(5.0 / 25.0, (25, 1))), (3699, 3666, 200));
        assert_eq!(drawn_ms(1001.0 / 30_000.0 * 3.0, (30_000, 1001)), 100, "29.97: frame 3 at 100.1 ms");
        assert_eq!(libass_ms(200_000.0), 199, "without a rate, the AV_TIME_BASE clock");
        let thirty = grid(30.0, 10.0);
        assert_eq!(thirty[111], 3699);
        let burns = [lines(vec![cue(3_000, 3_700, "A"), cue(3_690, 5_000, "B")])];
        let (starts, layers) = stretches(&burns, &thirty);
        assert_eq!((starts, pairs(&layers[0])), (vec![3_000, 3_699, 3_733], vec![(0, 2), (1, 3)]));
        // and the frame a rounded ms would put after A shows B alone
        let burns = [lines(vec![cue(3_000, 3_700, "A"), cue(3_700, 5_000, "B")])];
        let (starts, layers) = stretches(&burns, &thirty);
        assert_eq!((starts, pairs(&layers[0])), (vec![3_000, 3_733], vec![(0, 1), (1, 2)]));
        // a line shorter than a frame is never drawn, so never measured
        let burns = [lines(vec![cue(1_010, 1_030, "flash"), cue(2_000, 3_000, "C")])];
        let (starts, layers) = stretches(&burns, &frames);
        assert_eq!((starts, layers[0].len()), (vec![2_000], 1));
        // after a cut the frames drift off any fixed rate: the video's own times decide (frame at 11 380 ms)
        let drifted = [11_344, 11_377, 11_380, 11_413, 11_447];
        let burns = [lines(vec![cue(11_000, 11_390, "A"), cue(11_380, 12_000, "B")])];
        let (starts, layers) = stretches(&burns, &drifted);
        assert_eq!((starts, pairs(&layers[0])), (vec![11_344, 11_380, 11_413], vec![(0, 2), (1, 3)]));
    }

    #[test]
    fn the_fill_window_is_ffmpegs_crop() {
        // 854×480 to 1:1: the crop offset 187 is rounded down to 186 (yuv420p)
        assert_eq!(fill_window((854, 480), (480, 480)), (186.0, 0.0, 666.0, 480.0));
        let (x0, y0, x1, y1) = fill_window((1920, 1080), (608, 1080));
        assert_eq!((x0, y0, x1, y1), (656.0, 0.0, 1264.0, 1080.0));
        let (_, top, _, bottom) = fill_window((1920, 1080), (2520, 1080));
        assert!((top - 168.0 * 1080.0 / 1418.0).abs() < 1e-9 && (bottom - 1248.0 * 1080.0 / 1418.0).abs() < 1e-9, "{top} {bottom}");
        // a cut at the frame's own edge is named as such even under a fill
        let line = burn(style(json!({})), LONG, 12_000);
        let tall = subtitle_frame(&[&line], &Framing { export: (1920, 1080), out: (608, 1080), fill: "fill", aspect: Some("9:16") }).unwrap();
        let top_edge = fit_of(&tall, &[1_000], &HashMap::from([(0, (700, 0, 1200, 60))]));
        assert!(top_edge.detail.starts_with("phụ đề bị cắt mép ở 1 đoạn (chữ vượt ra ngoài khung hình), từ: 0:01"), "{}", top_edge.detail);
        let both = fit_of(&tall, &[1_000, 2_000], &HashMap::from([(0, (700, 0, 1200, 60)), (1, (300 + 960, 900 + 540, 1500 + 960, 1000 + 540))]));
        assert!(both.detail.contains("(chữ vượt ra ngoài khung hình; khung 9:16, cắt cho đầy)"), "{}", both.detail);
    }

    #[test]
    fn boxes_are_read_from_the_bbox_log() {
        let log = "[Parsed_color_0 @ 0x1] [info] something\n\
[Parsed_bbox_1 @ 0x55f8] [info] n:0 pts:0 pts_time:0 x1:894 x2:1025 y1:979 y2:1024 w:132 h:46 crop=132:46:894:979 drawbox=894:979:132:46\n\
[Parsed_bbox_1 @ 0x55f8] [info] n:2 pts:2 pts_time:2 x1:0 x2:1919 y1:975 y2:1028 w:1920 h:54 crop=1920:54:0:975 drawbox=0:975:1920:54\n\
[Parsed_bbox_1 @ 0x55f8] [info] n:3 pts:3 broken\n";
        let boxes = read_boxes(log);
        assert_eq!(boxes.len(), 2);
        assert_eq!(boxes[&0], (894, 979, 1025, 1024));
        assert_eq!(boxes[&2], (0, 975, 1919, 1028));
    }

    #[test]
    fn fit_of_judges_the_measured_boxes() {
        let line = burn(style(json!({})), LONG, 12_000);
        let wide = subtitle_frame(&[&line], &Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None }).unwrap();
        let tall = subtitle_frame(&[&line], &Framing { export: (1920, 1080), out: (608, 1080), fill: "fill", aspect: Some("9:16") }).unwrap();
        // boxes in export pixels, put on the measure frame (twice the size, the export frame in the middle)
        let boxes = |list: &[(usize, (i64, i64, i64, i64))]| list.iter().map(|&(n, (x0, y0, x1, y1))| (n, (x0 + 960, y0 + 540, x1 + 960, y1 + 540)))
            .collect::<HashMap<_, _>>();
        let short = (894, 979, 1025, 1024);
        let long = (358, 976, 1560, 1034);
        let fine = fit_of(&wide, &[2_000, 12_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!((fine.ok, fine.detail.as_str()), (Some(true), "phụ đề nằm trong khung — đo trên chữ libass vẽ"));
        let cut = fit_of(&tall, &[2_000, 12_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!((cut.ok, cut.at_ms.clone()), (Some(false), vec![12_000]));
        assert_eq!(cut.detail, "phụ đề bị cắt mép ở 1 đoạn (khung 9:16, cắt cho đầy), từ: 0:12 — đo trên chữ libass vẽ.");
        // ink past the frame (drawn on the measure frame around it) is cut; ink up to the outermost pixel is not;
        // ink on the measure frame's own edge may run on further and is cut (recheck 11)
        let edge = fit_of(&wide, &[3_000, 4_000, 5_000, 6_000, 7_000], &boxes(&[(0, (-3, 975, 1919, 1028)), (1, (10, 1000, 300, 1180)),
            (2, (0, 0, 1919, 1079)), (3, (100, -540, 300, 10)), (4, (1, 1, 1918, 1078))]));
        assert_eq!((edge.ok, edge.at_ms), (Some(false), vec![3_000, 4_000, 6_000]));
        assert!(edge.detail.starts_with("phụ đề bị cắt mép ở 3 đoạn (chữ vượt ra ngoài khung hình), từ: 0:03, 0:04, 0:06"), "{}", edge.detail);
        // nothing drawn for a moment: not measured, never called fine
        let blank = fit_of(&wide, &[2_000, 12_000], &boxes(&[(0, short)]));
        assert_eq!(blank.ok, None);
        assert!(blank.detail.contains("1 đoạn chưa đo được"), "{}", blank.detail);
        let both = fit_of(&tall, &[2_000, 12_000, 20_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!(both.ok, Some(false));
        assert!(both.detail.ends_with("1 đoạn chưa đo được (libass không vẽ ra chữ)."), "{}", both.detail);
        // at most NAMED times in words, MAX_SPANS kept
        let many: Vec<u64> = (0..60).map(|index| index * 1_000).collect();
        let all = fit_of(&wide, &many, &HashMap::from_iter((0..60).map(|index| (index, (0, 0, 970, 550)))));
        assert_eq!(all.at_ms.len(), MAX_SPANS);
        assert!(all.detail.starts_with("phụ đề bị cắt mép ở 60 đoạn (chữ vượt ra ngoài khung hình), từ: 0:00, 0:01, 0:02, 0:03, 0:04… "), "{}", all.detail);
    }

    /// #930: Phụ đề is burned after the part looks, so a crop, zoom, tilt or turn of a part never moves what is measured.
    #[test]
    fn part_looks_do_not_move_the_burned_lines() {
        let wide = Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None };
        let mut edit = Edit { segments: vec![segment(0, 0.0, 10.0), segment(0, 20.0, 30.0)], ..Edit::default() };
        edit.segments[1].crop = Some(Crop { x: 0.0, y: 0.0, w: 1.0, h: 0.8 });
        edit.segments[0].zoom = Some(Zoom { from: 1.0, to: 2.0, x: 0.0, y: 0.0 });
        edit.segments[0].tilt = 3.0;
        edit.segments[1].rotate = 90;
        let line = burn(style(json!({})), LONG, 12_000);
        let planned = plan(&edit, &[false], 20.0, true, &[], &[&line], &wide, "30/1");
        assert_eq!(planned.subtitles, subtitle_frame(&[&line], &wide).map(|frame| SubtitleFrame { rate: (30, 1), ..frame }));
        assert_eq!(planned.subtitles.unwrap().window, (0.0, 0.0, 1920.0, 1080.0));
        assert_eq!((rate_of("30000/1001"), rate_of("25"), rate_of("x"), rate_of("30/0")), ((30_000, 1001), (25, 1), (0, 0), (0, 0)));
        // a phone video shown turned: the burned lines are said unmeasured, never measured on the wrong frame
        let turned = plan(&edit, &[false], 20.0, true, &[], &[&line], &wide, "30/1").turned_source(true);
        assert_eq!(turned.subtitles.as_ref().unwrap().unmeasurable.as_deref(), Some(TURNED));
        let running = Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) };
        let dir = temp("turned");
        let fit = measure_subtitles(&dir.join("work"), &dir.join("ffmpeg"), turned.subtitles.as_ref().unwrap(), &dir.join("v.mp4"), &dir, &running);
        assert_eq!((fit.ok, fit.detail), (None, format!("chưa đo được phụ đề: {TURNED}")));
        assert_eq!(plan(&edit, &[false], 20.0, true, &[], &[&line], &wide, "30/1").turned_source(false).subtitles.unwrap().unmeasurable, None);
        let _ = fs::remove_dir_all(dir);
    }

    /// An export rendered in parts burns each part with its own libass: lines that shared the screen before a cut
    /// and are still on screen across it are stacked anew there, so the burned lines are said unmeasured instead of
    /// judged on the one-libass layout (recheck 13); lines that never shared the screen across a cut are measured.
    #[test]
    fn lines_stacked_across_a_part_cut_are_not_measured() {
        let wide = Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None };
        let edit = Edit { segments: vec![segment(0, 0.0, 600.0)], ..Edit::default() };
        let planned = |lines: Vec<Cue>, cuts: &[f64]| {
            let layer = BurnSubtitles { style: style(json!({})), lines };
            plan(&edit, &[false], 600.0, true, &[], &[&layer], &wide, "30/1").parts_cut_at(cuts).subtitles.unwrap().unmeasurable
        };
        // A 0-4 s under B 2-11 s, cut at 5 s: B is placed afresh after the cut
        assert_eq!(planned(vec![cue(0, 4_000, "A"), cue(2_000, 11_000, "B")], &[5.0]).as_deref(), Some(PARTS));
        // the same lines in one FFmpeg run (no cut), or a cut after they are gone, or before they meet: measured
        assert_eq!(planned(vec![cue(0, 4_000, "A"), cue(2_000, 11_000, "B")], &[]), None);
        assert_eq!(planned(vec![cue(0, 4_000, "A"), cue(2_000, 11_000, "B")], &[12.0]), None);
        assert_eq!(planned(vec![cue(0, 4_000, "A"), cue(6_000, 11_000, "B")], &[5.0]), None, "A ends before the cut, B is alone across it");
        assert_eq!(planned(vec![cue(0, 9_000, "A"), cue(6_000, 11_000, "B")], &[5.0]), None, "A is alone up to the cut: placed the same");
        // a line ending exactly at the cut is not on screen across it
        assert_eq!(planned(vec![cue(0, 5_000, "A"), cue(2_000, 5_000, "B")], &[5.0]), None);
        // lines on two layers never push each other (one libass per layer)
        let first = BurnSubtitles { style: style(json!({})), lines: vec![cue(0, 4_000, "A")] };
        let second = BurnSubtitles { style: style(json!({})), lines: vec![cue(2_000, 11_000, "B")] };
        assert_eq!(plan(&edit, &[false], 600.0, true, &[], &[&first, &second], &wide, "30/1").parts_cut_at(&[5.0]).subtitles.unwrap().unmeasurable, None);
        // a turned source keeps its own reason
        let layer = BurnSubtitles { style: style(json!({})), lines: vec![cue(0, 4_000, "A"), cue(2_000, 11_000, "B")] };
        let turned = plan(&edit, &[false], 600.0, true, &[], &[&layer], &wide, "30/1").turned_source(true).parts_cut_at(&[5.0]);
        assert_eq!(turned.subtitles.unwrap().unmeasurable.as_deref(), Some(TURNED));
        let running = Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) };
        let dir = temp("parts-cut");
        let cut = plan(&edit, &[false], 600.0, true, &[], &[&layer], &wide, "30/1").parts_cut_at(&[5.0]);
        let fit = measure_subtitles(&dir.join("work"), &dir.join("ffmpeg"), cut.subtitles.as_ref().unwrap(), &dir.join("v.mp4"), &dir, &running);
        assert_eq!((fit.ok, fit.detail), (None, format!("chưa đo được phụ đề: {PARTS}")));
        let _ = fs::remove_dir_all(dir);
    }

    /// With DUBFLOW_TEST_FFMPEG: libass itself says where the lines land.
    #[test]
    fn real_subtitle_measure() {
        let Ok(ffmpeg) = std::env::var("DUBFLOW_TEST_FFMPEG") else { return };
        let ffmpeg = PathBuf::from(ffmpeg);
        let dir = temp("measure");
        let work = dir.join("work");
        let folder = work.join(CHECKS_DIR).join("00000000000000000000");
        fs::create_dir_all(&folder).unwrap();
        let running = Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) };
        // the export whose frame times the measure follows: 12 s at 25 fps
        let clip = dir.join("clip.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=12", "-c:v", "mpeg4", "-q:v", "10"])
            .arg(&clip).status().unwrap();
        assert!(made.success());
        let measure = |lines: Vec<Cue>, style: SubtitleStyle, framing: &Framing| {
            let burn = BurnSubtitles { style, lines };
            measure_subtitles(&work, &ffmpeg, &SubtitleFrame { rate: (25, 1), ..subtitle_frame(&[&burn], framing).unwrap() }, &clip, &folder, &running)
        };
        let frames = frame_times(&ffprobe_beside(&ffmpeg), &clip, &running, (25, 1)).unwrap();
        assert_eq!((frames.len(), frames[0], frames[1], frames[299]), (300, 0, 40, 11_960), "the clip's own frame times, as libass gets them on the 1/25 clock");
        // The export's own graph draws Phụ đề on its frames (#935, recheck 9): after a cut at 30 fps (concat on the
        // AV_TIME_BASE clock), a line written 0:00:03.69–0:00:03.70 shows on exactly the frames whose libass time
        // `frame_times` gives as inside it (frame 111, drawn at 3699 ms).
        let source = dir.join("source30.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30,format=yuv420p", "-t", "8",
            "-c:v", "mpeg4", "-q:v", "2", "-video_track_timescale", "15360"]).arg(&source).status().unwrap();
        assert!(made.success());
        let tick = dir.join("tick.ass");
        fs::write(&tick, "[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, \
            PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
            BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: D,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,\
            &H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, \
            Effect, Text\nDialogue: 0,0:00:03.69,0:00:03.70,D,,0,0,0,,X\n").unwrap();
        let cut = Edit { segments: vec![segment(0, 0.0, 1.04), segment(0, 1.5, 5.5)], ..Edit::default() };
        let frame30 = Frame { width: 320, height: 180, fps: "30/1".into() };
        let finish = Finish { burn: Some(subtitles_filter(&display_path(&tick))), ..Finish::default() };
        let (graph, _) = export_script(&cut, &[Input { has_audio: false }], &frame30, "source", &Mix { main_volume: 1.0, ..Mix::default() }, &finish);
        let burned = dir.join("burned30.mp4");
        let mut command = Command::new(&ffmpeg);
        command.args(["-v", "error", "-y", "-i"]).arg(&source).args(["-filter_complex", &graph, "-map", "[v]"]);
        if graph.contains("[a]") {
            command.args(["-map", "[a]"]);
        }
        let made = command.args(["-r", "30/1", "-c:v", "mpeg4", "-q:v", "2"]).arg(&burned).status().unwrap();
        assert!(made.success(), "{graph}");
        let drawn = Command::new(&ffmpeg).args(["-v", "info", "-i"]).arg(&burned).args(["-vf", "format=gray,bbox=min_val=128", "-f", "null", "-"])
            .output().unwrap();
        let shown: Vec<usize> = read_boxes(&String::from_utf8_lossy(&drawn.stderr)).into_keys().collect();
        let times = frame_times(&ffprobe_beside(&ffmpeg), &burned, &running, (30, 1)).unwrap();
        let expected: Vec<usize> = times.iter().enumerate().filter(|(_, ms)| (3_690..3_700).contains(*ms)).map(|(n, _)| n).collect();
        assert_eq!((shown, expected), (vec![111], vec![111]), "{times:?}");
        let wide = Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None };
        let four_three = Framing { export: (1920, 1080), out: (1440, 1080), fill: "fill", aspect: Some("4:3") };
        // short lines at the bottom, one with a box: inside
        let fine = measure(vec![cue(1_000, 3_000, "Chào"), cue(4_000, 6_000, LONG)], style(json!({})), &wide);
        assert_eq!(fine.ok, Some(true), "{}", fine.detail);
        assert_eq!(measure(vec![cue(1_000, 3_000, "Chào")], style(json!({"box": "#000000", "box_opacity": 0.6})), &four_three).ok, Some(true));
        // a long Chinese sentence with no space: whether libass breaks it between characters depends on its build
        // (Unicode line breaking), which is why the check measures instead of guessing; either way it is cut by a 4:3
        // fill, and only that line is named
        let chinese = measure(vec![cue(1_000, 3_000, "Chào"), cue(5_000, 7_000, CHINESE)], style(json!({})), &four_three);
        assert_eq!((chinese.ok, chinese.at_ms.clone()), (Some(false), vec![5_000]), "{}", chinese.detail);
        // the same words with spaces wrap and stay inside the 4:3 window
        let spaced = measure(vec![cue(5_000, 7_000, "大家好 欢迎来到 我们的频道 今天 我们来聊一聊")], style(json!({})), &four_three);
        assert_eq!(spaced.ok, Some(true), "{}", spaced.detail);
        // a long line dragged near the left edge sticks out; the same line in the middle does not
        assert_eq!(measure(vec![cue(7_000, 9_000, LONG)], style(json!({"x": 0.1, "y": 0.5})), &wide).at_ms, [7_000]);
        assert_eq!(measure(vec![cue(7_000, 9_000, LONG)], style(json!({"x": 0.5, "y": 0.5})), &wide).ok, Some(true));
        // libass leaves wrapped rows uneven (recheck 4), whatever an estimate says: 30 words wrap into rows wider than the
        // 4:3 window and are cut by it
        let words = vec!["nghiêng"; 30].join(" ");
        assert_eq!(measure(vec![cue(9_000, 11_000, &words)], style(json!({"size": 1.0})), &four_three).at_ms, [9_000]);
        // an ordinary short line under a 9:16 fill is no false alarm
        let tall = Framing { export: (1920, 1080), out: (608, 1080), fill: "fill", aspect: Some("9:16") };
        assert_eq!(measure(vec![cue(1_000, 3_000, "Xin chào các bạn")], style(json!({})), &tall).ok, Some(true));
        assert_eq!(measure(vec![cue(1_000, 3_000, LONG)], style(json!({})), &tall).at_ms, [1_000]);
        // a line that appears while another is shown stacks above or below it and keeps that place after the first
        // ends (libass); in the middle at size 2 the pair is taller than what a 21:9 fill keeps (recheck 5)
        let wide_fill = Framing { export: (1920, 1080), out: (2520, 1080), fill: "fill", aspect: Some("21:9") };
        let pho = "Hôm nay chúng ta sẽ cùng nhau tìm hiểu cách nấu món phở bò truyền thống của Hà Nội, từ khâu chọn xương, ninh nước dùng";
        let pair = vec![cue(0, 4_000, pho), cue(3_000, 7_000, pho)];
        let stacked = measure(pair.clone(), style(json!({"size": 2.0, "align": "middle"})), &wide_fill);
        assert_eq!(stacked.ok, Some(false), "{}", stacked.detail);
        assert_eq!(measure(vec![pair[0].clone()], style(json!({"size": 2.0, "align": "middle"})), &wide_fill).ok, Some(true), "one alone fits");
        // an odd export size is measured at that size: a long line dragged to the right edge is cut, a short one in
        // the middle is not
        let odd = Framing { export: (853, 481), out: (853, 481), fill: "black", aspect: None };
        assert_eq!(measure(vec![cue(1_000, 3_000, LONG)], style(json!({"x": 0.9, "y": 0.5})), &odd).at_ms, [1_000]);
        assert_eq!(measure(vec![cue(1_000, 3_000, "Chào")], style(json!({})), &odd).ok, Some(true));
        // two lines at once stack: both are measured together
        let stacked = measure(vec![cue(1_000, 5_000, "Dòng một"), cue(2_000, 4_000, "Dòng hai")], style(json!({})), &wide);
        assert_eq!(stacked.ok, Some(true), "{}", stacked.detail);
        // a wrapped block dragged so low that its last row is wholly below the frame, while the row above stops a
        // few pixels short of the edge: the hidden row is drawn on the measure frame around it, so it is seen cut
        // (recheck 11); higher up the same block fits
        let low = measure(vec![cue(1_000, 3_000, pho)], style(json!({"size": 2.0, "x": 0.5, "y": 0.951})), &wide);
        assert_eq!((low.ok, low.at_ms.clone()), (Some(false), vec![1_000]), "{}", low.detail);
        assert_eq!(measure(vec![cue(1_000, 3_000, pho)], style(json!({"size": 2.0, "x": 0.5, "y": 0.5})), &wide).ok, Some(true));
        // eleven lines at once pile up past the top: cut, though libass leaves the visible rows inside
        let pile: Vec<Cue> = (0..12).map(|n| cue(1_000 + n, 3_000, "anh ơi")).collect();
        assert_eq!(measure(pile, style(json!({"size": 2.0})), &wide).ok, Some(false));
        let _ = fs::remove_dir_all(dir);
        println!("subtitle-measure: ran against real FFmpeg");
    }

    /// #935 "silent": a sound track that is there but silent warns when the edit has sound, never when it has none.
    #[test]
    fn a_silent_track_warns_only_when_the_edit_has_sound() {
        let checks = by_id(&output_checks(&plain_plan(10.0, true, None), Some(10_000), Some(true), Some(f64::NEG_INFINITY)));
        assert_eq!(checks["audio_stream"].ok, Some(true));
        assert_eq!((checks["audio"].ok, checks["audio"].detail.as_str()),
            (Some(false), "bản sửa có tiếng nhưng âm thanh xuất ra im lặng (không có tiếng nào)"));
        let faint = by_id(&output_checks(&plain_plan(10.0, true, None), Some(10_000), Some(true), Some(-71.25)));
        assert_eq!((faint["audio"].ok, faint["audio"].detail.as_str()), (Some(false), "bản sửa có tiếng nhưng âm thanh xuất ra im lặng (đỉnh -71.2 dBTP)"));
        let quiet = by_id(&output_checks(&plain_plan(10.0, true, None), Some(10_000), Some(true), Some(-40.0)));
        assert_eq!(quiet["audio"].ok, Some(true), "quiet is not silent");
        let intended = by_id(&output_checks(&plain_plan(10.0, false, None), Some(10_000), Some(true), Some(f64::NEG_INFINITY)));
        assert_eq!((intended["audio"].ok, intended["audio"].detail.as_str()), (Some(true), "âm thanh im lặng, đúng như bản sửa (không có tiếng nào)"));
        let unmeasured = by_id(&output_checks(&plain_plan(10.0, true, None), Some(10_000), Some(true), None));
        assert_eq!(unmeasured["audio"].ok, None);
    }

    /// At most `max` checks wait for the turn; the next is told at once, and a stopped wait leaves its place.
    #[test]
    fn the_check_queue_is_bounded() {
        let turn = Mutex::new(());
        let waiting = AtomicUsize::new(0);
        let held = turn.lock().unwrap();
        let runners: Vec<Arc<Running>> = (0..3).map(|_| Arc::new(Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) })).collect();
        std::thread::scope(|scope| {
            let waits: Vec<_> = runners.iter().map(|running| {
                let (turn, waiting) = (&turn, &waiting);
                scope.spawn(move || matches!(wait_turn_in(turn, waiting, 2, running, || {}), Turn::Taken(_)))
            }).collect();
            let deadline = Instant::now() + Duration::from_secs(10);
            while waiting.load(Ordering::SeqCst) < 2 && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(5));
            }
            std::thread::sleep(Duration::from_millis(300));
            assert_eq!(waiting.load(Ordering::SeqCst), 2, "two wait");
            // the third found the queue full without waiting
            let finished: Vec<usize> = waits.iter().enumerate().filter(|(_, wait)| wait.is_finished()).map(|(index, _)| index).collect();
            assert_eq!(finished.len(), 1, "one was turned away at once");
            let extra = Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) };
            assert!(matches!(wait_turn_in(&turn, &waiting, 2, &extra, || panic!("never announced")), Turn::Full));
            // stopping one frees its place
            let stopped = (0..3).find(|index| !finished.contains(index)).unwrap();
            runners[stopped].cancelled.store(true, Ordering::SeqCst);
            let deadline = Instant::now() + Duration::from_secs(10);
            while waiting.load(Ordering::SeqCst) > 1 && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(5));
            }
            assert_eq!(waiting.load(Ordering::SeqCst), 1);
            drop(held);
            let taken: Vec<bool> = waits.into_iter().map(|wait| wait.join().unwrap()).collect();
            assert_eq!(taken.iter().filter(|taken| **taken).count(), 1, "only the one still waiting got the turn: {taken:?}");
        });
        assert_eq!(waiting.load(Ordering::SeqCst), 0);
    }

    /// A link or a second name planted in a check folder, even one that already exists, never lets the check write
    /// or hold a file elsewhere (ADR-0006).
    #[test]
    fn planted_links_in_a_check_folder_are_never_written_through() {
        let dir = temp("planted");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        fs::write(&video, b"not a video").unwrap();
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        let marker = dir.join("marker.bin");
        fs::write(&marker, b"marker bytes").unwrap();
        let before = || (fs::read(&marker).unwrap(), fs::metadata(&marker).unwrap().modified().unwrap());
        let start = before();
        std::thread::sleep(Duration::from_millis(20));
        let mut planted = vec!["plan.json", "check.json", "ffmpeg.log"];
        for name in &planted {
            fs::hard_link(&marker, folder.join(name)).unwrap();
        }
        #[cfg(unix)]
        for name in ["measure.log", "measure-1.ass", "lease", "check.json.partial", "plan.json.partial"] {
            std::os::unix::fs::symlink(&marker, folder.join(name)).unwrap();
            planted.push(name);
        }
        // A fake FFmpeg that fails at once: the check still writes plan.json and check.json and opens its log (on
        // Unix it also says a word, so the subtitle measure runs too: measure-1.ass and measure.log).
        let fake = dir.join(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" });
        fs::write(&fake, b"").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::write(&fake, "#!/bin/sh\necho boom >&2\nexit 1\n").unwrap();
            fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
            // and a fake FFprobe that knows only the frame times (one frame each 40 ms for 2 s)
            let probe = dir.join("ffprobe");
            let times: String = (0..50).map(|k| format!("{:.6}\\n", k as f64 * 0.04)).collect();
            fs::write(&probe, format!("#!/bin/sh\ncase \"$*\" in *packet=pts_time*) printf '{times}';; *) exit 1;; esac\n")).unwrap();
            fs::set_permissions(&probe, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let line = burn(style(json!({})), LONG, 1_000);
        let framing = Framing { export: (640, 360), out: (640, 360), fill: "black", aspect: None };
        let plan = Plan { subtitles: subtitle_frame(&[&line], &framing), ..plain_plan(1.0, true, None) }.for_video(video.clone(), false, stamp_of(&video));
        run(&work, &fake, plan);
        assert_eq!(before(), start, "the marker was never written");
        for name in ["plan.json", "check.json"] {
            let metadata = fs::symlink_metadata(folder.join(name)).unwrap();
            assert!(metadata.is_file() && !is_link(&metadata), "{name} is the check's own file now");
        }
        assert_ne!(read_check(&work, &video)["state"], Value::Null);
        #[cfg(unix)]
        {
            let report = read_check(&work, &video);
            assert_eq!(report["state"], "DONE", "{report}");
            let subtitles = report["checks"].as_array().unwrap().iter().find(|check| check["id"] == "subtitle_frame").unwrap().clone();
            assert!(subtitles["detail"].as_str().unwrap().starts_with("chưa đo được phụ đề"), "{subtitles}");
            for name in ["measure.log", "measure-1.ass", "ffmpeg.log"] {
                let metadata = fs::symlink_metadata(folder.join(name)).unwrap();
                assert!(metadata.is_file() && !is_link(&metadata), "{name} is the check's own file now");
            }
        }
        // a check.json reached through a link is never read
        #[cfg(unix)]
        {
            let elsewhere = dir.join("elsewhere.json");
            fs::write(&elsewhere, br#"{"kind":"export_check","state":"DONE"}"#).unwrap();
            fs::remove_file(folder.join("check.json")).unwrap();
            std::os::unix::fs::symlink(&elsewhere, folder.join("check.json")).unwrap();
            assert_eq!(read_check_file(&folder.join("check.json")), None);
        }
        let _ = fs::remove_dir_all(dir);
    }

    /// A read that opens check.json just before a progress save takes the name is refused on that handle (it has no
    /// name any more) and tried again on the new file, so a poll never loses a running check (recheck 13).
    #[test]
    fn a_read_racing_a_progress_save_still_reads_the_report() {
        let dir = temp("racing-save");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        save(&work, &folder, &ExportCheck::new(&video, "RUNNING"));
        #[cfg(target_os = "linux")]
        {
            let stale = open_own(&folder.join("check.json")).unwrap();
            save(&work, &folder, &ExportCheck { percent: 50.0, ..ExportCheck::new(&video, "RUNNING") });
            assert!(!at_place(&stale, &work, &folder), "the handle opened before the save is not the file at the name");
            assert_eq!(load_check(&work, &folder).unwrap()["percent"], 50.0, "the new report is read");
        }
        // a save every moment while polls read: every read gets a report
        let stop = Arc::new(AtomicBool::new(false));
        let writer = {
            let (work, folder, video, stop) = (work.clone(), folder.clone(), video.clone(), stop.clone());
            std::thread::spawn(move || {
                let mut percent = 0.0;
                while !stop.load(Ordering::SeqCst) {
                    percent = (percent + 1.0) % 100.0;
                    save(&work, &folder, &ExportCheck { percent, ..ExportCheck::new(&video, "RUNNING") });
                }
            })
        };
        for _ in 0..2_000 {
            assert_eq!(load_check(&work, &folder).map(|report| report["state"].clone()), Some(json!("RUNNING")));
        }
        stop.store(true, Ordering::SeqCst);
        writer.join().unwrap();
        let _ = fs::remove_dir_all(dir);
    }

    /// A check.json that is already a hard link (another name of a file elsewhere, here holding a valid RUNNING
    /// export check) is never read, by `attach` either, and nothing is written through it; a link or a FIFO at the
    /// name is refused on the opened handle, without hanging.
    #[test]
    fn a_planted_check_json_is_never_read() {
        let dir = temp("hardread");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        let elsewhere = dir.join("elsewhere.json");
        let running = br#"{"kind":"export_check","state":"RUNNING","checks":[]}"#;
        fs::write(&elsewhere, running).unwrap();
        // the file is valid when it has one name
        let own = dir.join("own.json");
        fs::write(&own, running).unwrap();
        assert_eq!(read_check_file(&own).unwrap()["state"], "RUNNING");
        fs::hard_link(&elsewhere, folder.join("check.json")).unwrap();
        assert_eq!(read_check_file(&folder.join("check.json")), None, "a second name is not the check's own file");
        let mut exports = vec![json!({ "path": display_path(&video) })];
        attach(&work, &mut exports);
        assert!(exports[0].get("check").is_none(), "{}", exports[0]);
        assert_eq!(fs::read(&elsewhere).unwrap(), running, "nothing was written through the link");
        assert!(!folder.join("lease").exists(), "no lease was taken for a file that was never read");
        // a file symlink to a valid check outside: never followed (O_NOFOLLOW / FILE_FLAG_OPEN_REPARSE_POINT), by
        // load_check and attach either, and the outside file is left as it was
        fs::remove_file(folder.join("check.json")).unwrap();
        let outside_stamp = fs::metadata(&own).unwrap().modified().unwrap();
        if file_symlink(&own, &folder.join("check.json")) {
            assert_eq!(read_check_file(&folder.join("check.json")), None, "a symlink is never followed");
            assert_eq!(load_check(&work, &folder), None);
            attach(&work, &mut exports);
            assert!(exports[0].get("check").is_none(), "{}", exports[0]);
            assert_eq!((fs::read(&own).unwrap(), fs::metadata(&own).unwrap().modified().unwrap()), (running.to_vec(), outside_stamp));
            assert!(!folder.join("lease").exists());
            fs::remove_file(folder.join("check.json")).unwrap();
        }
        #[cfg(unix)]
        {
            let fifo = folder.join("check.json");
            assert!(Command::new("mkfifo").arg(&fifo).status().unwrap().success());
            let started = Instant::now();
            assert_eq!(read_check_file(&fifo), None);
            assert!(started.elapsed() < Duration::from_secs(5), "a FIFO never holds the reader");
        }
        let _ = fs::remove_dir_all(dir);
    }

    /// A `checks` folder that is a link (or is swapped for one between the look and the open) never leads a read of
    /// check.json elsewhere, by `attach` either: a valid check there is not attached, and nothing there changes.
    /// On Windows `work/checks` is a junction (mklink /J), on Unix a symlink; both runners run it.
    #[test]
    fn a_linked_checks_folder_is_never_read_through() {
        let dir = temp("readlinked");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        fs::write(folder.join("check.json"), serde_json::to_vec(&ExportCheck::new(&video, "DONE")).unwrap()).unwrap();
        assert_eq!(load_check(&work, &folder).unwrap()["state"], "DONE", "the check's own file is read");
        // the same check folder elsewhere, with a valid single-name check.json
        let elsewhere = dir.join("elsewhere");
        let key = folder.file_name().unwrap();
        fs::create_dir_all(elsewhere.join(key)).unwrap();
        let outside = elsewhere.join(key).join("check.json");
        let running = serde_json::to_vec(&ExportCheck::new(&video, "RUNNING")).unwrap();
        fs::write(&outside, &running).unwrap();
        let stamp = fs::metadata(&outside).unwrap().modified().unwrap();
        fs::rename(work.join(CHECKS_DIR), dir.join("old-checks")).unwrap();
        link_dir(&elsewhere, &work.join(CHECKS_DIR));
        assert!(plain(&folder), "the last step alone looks plain");
        assert_eq!(load_check(&work, &folder), None);
        let mut exports = vec![json!({ "path": display_path(&video) })];
        attach(&work, &mut exports);
        assert!(exports[0].get("check").is_none(), "{}", exports[0]);
        assert!(own_lease(&work, &folder).is_none(), "no lease to settle under");
        assert_eq!((fs::read(&outside).unwrap(), fs::metadata(&outside).unwrap().modified().unwrap()), (running.clone(), stamp));
        assert_eq!(fs::read_dir(elsewhere.join(key)).unwrap().count(), 1, "nothing was made there");
        // the race itself: a handle opened through the swapped `checks` is caught by where it really is
        // (GetFinalPathNameByHandleW on Windows, /proc/self/fd on Linux)
        let through = open_own(&folder.join("check.json")).expect("the final step alone is a plain single-name file");
        #[cfg(any(windows, target_os = "linux"))]
        assert!(!at_place(&through, &work, &folder));
        drop(through);
        unlink_dir(&work.join(CHECKS_DIR));
        fs::rename(dir.join("old-checks"), work.join(CHECKS_DIR)).unwrap();
        assert!(at_place(&open_own(&folder.join("check.json")).unwrap(), &work, &folder), "the real file is accepted");
        assert_eq!(load_check(&work, &folder).unwrap()["state"], "DONE");
        assert_eq!(fs::read(&outside).unwrap(), running, "the outside file is unchanged");
        let _ = fs::remove_dir_all(dir);
    }

    /// A file changed or replaced at the published name before its check ran is not judged against this export's
    /// plan (recheck 8: a re-export to the same name while the first check waited).
    #[test]
    fn a_replaced_video_is_not_judged_by_the_old_plan() {
        let dir = temp("replaced");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        fs::write(&video, b"export one").unwrap();
        let fake = dir.join("ffmpeg");
        fs::write(&fake, b"").unwrap();
        let plan = plain_plan(1.0, true, None).for_video(video.clone(), false, stamp_of(&video));
        assert!(plan.same_file());
        std::thread::sleep(Duration::from_millis(20));
        fs::write(&video, b"export two, longer").unwrap();
        assert!(!plan.same_file());
        run(&work, &fake, plan);
        let report = read_check(&work, &video);
        assert_eq!((report["state"].as_str(), report["error"].as_str()), (Some("UNAVAILABLE"), Some(CHANGED)), "{report}");
        assert_eq!(fs::read(&video).unwrap(), b"export two, longer");
        // that report is about export one's file: a later export at the same name never shows it
        assert!(stamp_in(&report).is_some());
        let mut exports = vec![json!({ "path": display_path(&video) })];
        attach(&work, &mut exports);
        assert!(exports[0].get("check").is_none(), "{}", exports[0]);
        // a report on the file as it is now is shown
        let now = ExportCheck::of(&plain_plan(1.0, true, None).for_video(video.clone(), false, stamp_of(&video)), "DONE");
        save(&work, &check_dir(&work, &video), &now);
        attach(&work, &mut exports);
        assert_eq!(exports[0]["check"]["state"], "DONE");
        // a plan saved before stamps existed still reads
        let old: Plan = serde_json::from_value(json!({"video": "x.mp4", "expected_seconds": 1.0, "has_audio": false,
            "srt_end_ms": null, "intended_holds": [], "subtitles": null})).unwrap();
        assert_eq!(old.stamp, None);
        let _ = fs::remove_dir_all(dir);
    }

    /// The stamp is taken as the export is published (on its partial file, before the rename), so a file put at the
    /// name after publication but before the check starts is caught, even with the same bytes, size and modified
    /// time: its identity on the disk differs (Lead review on recheck 11).
    #[test]
    fn a_video_replaced_after_publication_is_not_judged_by_the_plan() {
        let dir = temp("replaced-after-publication");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let partial = dir.join("Phim.mp4.partial");
        fs::write(&partial, b"the export").unwrap();
        let published = stamp_of(&partial);
        assert!(published.is_some());
        fs::rename(&partial, &video).unwrap();
        assert_eq!(stamp_of(&video), published, "a rename keeps the file's stamp");
        let fake = dir.join("ffmpeg");
        fs::write(&fake, b"").unwrap();
        // another file put at the name: same bytes, same size, same modified time
        let modified = fs::metadata(&video).unwrap().modified().unwrap();
        let other = dir.join("other.mp4");
        fs::write(&other, b"the export").unwrap();
        fs::File::options().write(true).open(&other).unwrap().set_modified(modified).unwrap();
        fs::remove_file(&video).unwrap();
        fs::rename(&other, &video).unwrap();
        let now = stamp_of(&video).unwrap();
        let before = published.unwrap();
        assert_eq!((now[0], now[1]), (before[0], before[1]), "size and modified time alone cannot tell them apart");
        assert_ne!(now, before);
        let plan = plain_plan(1.0, true, None).for_video(video.clone(), false, published);
        assert!(!plan.same_file());
        run(&work, &fake, plan);
        let report = read_check(&work, &video);
        assert_eq!((report["state"].as_str(), report["error"].as_str()), (Some("UNAVAILABLE"), Some(CHANGED)), "{report}");
        assert_eq!(stamp_in(&report), published, "the report names the file it was for");
        assert_eq!(fs::read(&video).unwrap(), b"the export", "the file at the name is untouched");
        let mut exports = vec![json!({ "path": display_path(&video) })];
        attach(&work, &mut exports);
        assert!(exports[0].get("check").is_none(), "{}", exports[0]);
        // the file that was published, untouched, is judged
        let kept = plain_plan(1.0, true, None).for_video(video.clone(), false, stamp_of(&video));
        assert!(kept.same_file());
        // a report written before the identity was in the stamp is not read as one
        assert_eq!(stamp_in(&json!({ "stamp": [1, 2] })), None);
        let _ = fs::remove_dir_all(dir);
    }

    /// While the check holds its folder, the folder, `checks` and `work` cannot be renamed aside (and so not swapped
    /// for a link) on Windows; a pin is only ever taken on the own plain folder (recheck 10).
    #[test]
    fn the_check_folder_is_held_while_the_check_writes() {
        let dir = temp("pinned");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        let pinned = pin(&work, &folder).expect("the own plain folder is held");
        #[cfg(windows)]
        {
            assert!(fs::rename(&folder, dir.join("aside")).is_err(), "the check folder stays");
            assert!(fs::rename(work.join(CHECKS_DIR), dir.join("aside")).is_err(), "checks stays");
            assert!(fs::rename(&work, dir.join("aside")).is_err(), "work stays");
            // writing in it is unaffected
            save(&work, &folder, &ExportCheck::new(&video, "RUNNING"));
            assert_eq!(load_check(&work, &folder).unwrap()["state"], "RUNNING");
        }
        drop(pinned);
        // a linked `checks` is never held
        let elsewhere = dir.join("elsewhere");
        fs::create_dir_all(elsewhere.join(folder.file_name().unwrap())).unwrap();
        fs::rename(work.join(CHECKS_DIR), dir.join("old-checks")).unwrap();
        link_dir(&elsewhere, &work.join(CHECKS_DIR));
        assert!(pin(&work, &folder).is_none());
        unlink_dir(&work.join(CHECKS_DIR));
        let _ = fs::remove_dir_all(dir);
    }

    /// `checks` swapped for a link after the check started (a long decode): nothing is removed, made or written
    /// through it (recheck 8).
    #[test]
    fn a_checks_folder_swapped_for_a_link_is_never_written_through() {
        let dir = temp("swapped");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        assert!(owned(&work, &folder));
        let elsewhere = dir.join("elsewhere");
        let key = folder.file_name().unwrap();
        fs::create_dir_all(elsewhere.join(key)).unwrap();
        let marker = elsewhere.join(key).join("measure.log");
        fs::write(&marker, b"user data").unwrap();
        fs::rename(work.join(CHECKS_DIR), dir.join("old-checks")).unwrap();
        link_dir(&elsewhere, &work.join(CHECKS_DIR));
        assert!(plain(&folder), "the last step alone looks plain");
        assert!(!owned(&work, &folder));
        assert!(fresh_file(&work, &folder, "measure.log").is_none());
        assert!(own_lease(&work, &folder).is_none());
        save(&work, &folder, &ExportCheck::new(&video, "DONE"));
        assert_eq!(fs::read(&marker).unwrap(), b"user data");
        let left: Vec<_> = fs::read_dir(elsewhere.join(key)).unwrap().flatten().map(|entry| entry.file_name()).collect();
        assert_eq!(left.len(), 1, "{left:?}");
        let _ = fs::remove_dir_all(dir);
    }

    /// Dừng kiểm tra while ffprobe hangs: the probe is ended too, nothing is left running.
    #[cfg(unix)]
    #[test]
    fn stopping_ends_a_hanging_probe() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp("probe");
        let work = dir.join("work");
        let video = dir.join("Phim.mp4");
        fs::write(&video, b"not a video").unwrap();
        let pid_file = dir.join("probe.pid");
        let (ffmpeg, ffprobe) = (dir.join("ffmpeg"), dir.join("ffprobe"));
        fs::write(&ffprobe, format!("#!/bin/sh\necho $$ > '{}'\nexec sleep 30\n", pid_file.display())).unwrap();
        fs::write(&ffmpeg, "#!/bin/sh\nexit 1\n").unwrap();
        for file in [&ffmpeg, &ffprobe] {
            fs::set_permissions(file, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let worker = {
            let (work, ffmpeg, plan) = (work.clone(), ffmpeg.clone(), plain_plan(1.0, true, None).for_video(video.clone(), false, stamp_of(&video)));
            std::thread::spawn(move || run(&work, &ffmpeg, plan))
        };
        let deadline = Instant::now() + Duration::from_secs(20);
        while !pid_file.exists() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let started = Instant::now();
        cancel(&work, &video);
        worker.join().unwrap();
        assert!(started.elapsed() < Duration::from_secs(10), "the probe did not hold the check");
        assert_eq!(read_check(&work, &video)["state"], "STOPPED");
        let pid = fs::read_to_string(&pid_file).unwrap();
        let alive = Command::new("kill").args(["-0", pid.trim()]).status().is_ok_and(|status| status.success());
        assert!(!alive, "the probe is gone");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn audio_expected_but_missing_warns() {
        let checks = by_id(&output_checks(&plain_plan(10.0, true, None), Some(10_000), Some(false), None));
        assert_eq!(checks["audio_stream"].ok, Some(false));
        assert_eq!(checks["audio_stream"].detail, "bản sửa có tiếng nhưng video xuất ra không có âm thanh");
        assert_eq!(checks["audio"].ok, None);
    }

    #[test]
    fn no_audio_expected_is_fine() {
        let checks = by_id(&output_checks(&plain_plan(10.0, false, None), Some(10_000), Some(false), None));
        assert_eq!(checks["audio_stream"].ok, Some(true));
        assert!(!checks.contains_key("audio"), "a silent export has no peak to call unmeasured");
    }

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("dubflow-check-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("work")).unwrap();
        dir
    }

    fn read_check(work: &Path, video: &Path) -> Value {
        serde_json::from_slice(&fs::read(check_dir(work, video).join("check.json")).unwrap()).unwrap()
    }

    #[test]
    fn a_check_never_touches_the_video() {
        let dir = temp("untouched");
        let work = dir.join("work");
        let video = dir.join("Phim (sửa 1).mp4");
        fs::write(&video, b"video bytes").unwrap();
        fs::write(work.join("exports.json"), b"[]").unwrap();
        fs::write(work.join("status.json"), b"{\"state\":\"COMPLETED\"}").unwrap();
        let before = |path: &Path| (fs::read(path).unwrap(), fs::metadata(path).unwrap().modified().unwrap());
        let (video_before, exports_before, status_before) = (before(&video), before(&work.join("exports.json")), before(&work.join("status.json")));
        let plan = plain_plan(1.0, true, None).for_video(video.clone(), false, stamp_of(&video));
        run(&work, &dir.join("missing").join("ffmpeg"), plan);
        let report = read_check(&work, &video);
        assert_eq!((report["state"].as_str(), report["kind"].as_str(), report["advisory"].as_bool()), (Some("UNAVAILABLE"), Some("export_check"), Some(true)));
        assert!(report.get("passed").is_none(), "no passed or READY field");
        assert_eq!(before(&video), video_before);
        assert_eq!(before(&work.join("exports.json")), exports_before);
        assert_eq!(before(&work.join("status.json")), status_before);
        let beside: Vec<_> = fs::read_dir(&dir).unwrap().flatten().map(|entry| entry.file_name()).collect();
        assert_eq!(beside.len(), 2, "nothing written beside the video: {beside:?}");
        assert!(!CHECKING.lock().unwrap().contains_key(&check_dir(&work, &video)));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn checks_are_attached_and_stale_ones_reported() {
        let dir = temp("attach");
        let work = dir.join("work");
        let (done, stale, ours, unchecked) = (dir.join("a.mp4"), dir.join("b.mp4"), dir.join("c.mp4"), dir.join("d.mp4"));
        for (video, state) in [(&done, "DONE"), (&stale, "RUNNING"), (&ours, "RUNNING")] {
            let folder = check_dir(&work, video);
            fs::create_dir_all(&folder).unwrap();
            fs::write(folder.join("check.json"), serde_json::to_vec(&ExportCheck::new(video, state)).unwrap()).unwrap();
        }
        let running = Arc::new(Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) });
        CHECKING.lock().unwrap().insert(check_dir(&work, &ours), running.clone());
        let mut exports: Vec<Value> = [&done, &stale, &ours, &unchecked].iter().map(|video| json!({"path": display_path(video)})).collect();
        attach(&work, &mut exports);
        assert_eq!(exports[0]["check"]["state"], "DONE");
        assert_eq!(exports[1]["check"]["state"], "INTERRUPTED", "a RUNNING check no window runs, its lease free");
        assert_eq!(read_check(&work, &stale)["state"], "INTERRUPTED", "and saved so");
        assert_eq!(exports[2]["check"]["state"], "RUNNING", "this window's check still runs");
        assert!(exports[3].get("check").is_none(), "an export without a check has no check key");
        cancel(&work, &ours);
        assert!(running.cancelled.load(Ordering::SeqCst), "Dừng kiểm tra stops it");
        CHECKING.lock().unwrap().remove(&check_dir(&work, &ours));
        let _ = fs::remove_dir_all(dir);
    }

    /// A process that runs until it is killed.
    fn long_child() -> Child {
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("ping");
            command.args(["-n", "60", "127.0.0.1"]);
            command
        };
        #[cfg(not(windows))]
        let mut command = {
            let mut command = Command::new("sleep");
            command.arg("60");
            command
        };
        command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap()
    }

    #[test]
    fn closing_stops_every_check_and_starts_none() {
        // A registry of its own: the app's CHECKING and CLOSING stay open for the other tests.
        let (checking, closing) = (Mutex::new(HashMap::new()), AtomicBool::new(false));
        let entries: Vec<Arc<Running>> = (0..2).map(|_| Arc::new(Running { children: Mutex::new(vec![long_child()]), cancelled: AtomicBool::new(false) })).collect();
        for (index, entry) in entries.iter().enumerate() {
            assert!(register(&checking, &closing, Path::new(&format!("checks/{index}")), entry));
        }
        stop_every(&checking, &closing);
        for entry in &entries {
            assert!(entry.cancelled.load(Ordering::SeqCst));
            let mut child = entry.children.lock().unwrap().pop().unwrap();
            let deadline = Instant::now() + Duration::from_secs(10);
            while child.try_wait().unwrap().is_none() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(20));
            }
            assert!(child.try_wait().unwrap().is_some(), "the check's FFmpeg is killed, not left running");
        }
        let late = Arc::new(Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) });
        assert!(!register(&checking, &closing, Path::new("checks/late"), &late), "no check starts once DubFlow closes");
        assert_eq!(checking.lock().unwrap().len(), 2);
    }

    #[test]
    fn checks_take_turns() {
        let dir = temp("turns");
        let work = dir.join("work");
        let missing = dir.join("missing").join("ffmpeg");
        let (first, second) = (dir.join("first.mp4"), dir.join("second.mp4"));
        let turn = TURN.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        let start = |video: &PathBuf| {
            let (work, missing, plan) = (work.clone(), missing.clone(), plain_plan(1.0, false, None).for_video(video.clone(), false, stamp_of(video)));
            std::thread::spawn(move || run(&work, &missing, plan))
        };
        let (a, b) = (start(&first), start(&second));
        let waiting = |video: &Path| fs::read(check_dir(&work, video).join("check.json")).ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).is_some_and(|check| check["state"] == "RUNNING" && check["waiting"] == true);
        let deadline = Instant::now() + Duration::from_secs(30);
        while !(waiting(&first) && waiting(&second)) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(waiting(&first) && waiting(&second), "both wait while another check runs");
        let mut exports = vec![json!({"path": display_path(&first)})];
        attach(&work, &mut exports);
        assert_eq!(exports[0]["check"]["state"], "RUNNING", "a waiting check is this window's, never INTERRUPTED");
        cancel(&work, &first);
        a.join().unwrap();
        assert_eq!(read_check(&work, &first)["state"], "STOPPED", "Dừng kiểm tra reaches a waiting check");
        drop(turn);
        b.join().unwrap();
        assert_eq!(read_check(&work, &second)["state"], "UNAVAILABLE", "the next one runs once the turn is free");
        assert!(read_check(&work, &second).get("waiting").is_none());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_result_saved_meanwhile_is_never_called_interrupted() {
        let dir = temp("settle");
        let work = dir.join("work");
        let video = dir.join("v.mp4");
        let folder = check_dir(&work, &video);
        fs::create_dir_all(&folder).unwrap();
        // attach read RUNNING; the check then saved DONE and let its lease go
        let done = ExportCheck::new(&video, "DONE").with_checks(Vec::new(), &[], &[]);
        fs::write(folder.join("check.json"), serde_json::to_vec(&done).unwrap()).unwrap();
        assert_eq!(settle(&work, &folder, &video).unwrap()["state"], "DONE");
        assert_eq!(read_check(&work, &video)["state"], "DONE", "kept on disk");
        // still RUNNING and no window checks it: the app closed mid-check
        fs::write(folder.join("check.json"), serde_json::to_vec(&ExportCheck::new(&video, "RUNNING")).unwrap()).unwrap();
        assert_eq!(settle(&work, &folder, &video).unwrap()["state"], "INTERRUPTED");
        assert_eq!(read_check(&work, &video)["state"], "INTERRUPTED");
        let _ = fs::remove_dir_all(dir);
    }

    /// `checks` swapped for a link to an outside folder of check-named folders after prune looked at a folder and
    /// before it removes it: nothing outside is removed or changed. On Windows the held `checks` cannot be renamed
    /// at all; on Linux the swap happens and the removal still goes through the held handle, into the real folder
    /// (Lead review 4610771246).
    #[test]
    fn prune_never_removes_through_checks_swapped_mid_prune() {
        use sha2::{Digest, Sha256};
        let dir = temp("prune-race");
        let work = dir.join("work");
        let checks = work.join(CHECKS_DIR);
        let elsewhere = dir.join("elsewhere");
        let names: Vec<String> = (0..EXPORTS_KEPT + 2).map(|index| format!("{index:020x}")).collect();
        for root in [&checks, &elsewhere] {
            for (index, name) in names.iter().enumerate() {
                fs::create_dir_all(root.join(name)).unwrap();
                fs::write(root.join(name).join("marker.bin"), format!("user data {index}")).unwrap();
            }
        }
        // the oldest folders are the ones prune removes
        for (index, name) in names.iter().enumerate() {
            let at = UNIX_EPOCH + Duration::from_secs(1_700_000_000 + index as u64 * 60);
            #[cfg(unix)]
            fs::File::open(checks.join(name)).unwrap().set_modified(at).unwrap();
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                // FILE_WRITE_ATTRIBUTES on the folder itself (FILE_FLAG_BACKUP_SEMANTICS opens a folder)
                let folder = fs::OpenOptions::new().access_mode(0x0100).custom_flags(0x0200_0000).open(checks.join(name)).unwrap();
                folder.set_modified(at).unwrap();
            }
        }
        let marks = |root: &Path| names.iter().map(|name| {
            let file = root.join(name).join("marker.bin");
            let bytes = fs::read(&file).unwrap();
            (Sha256::digest(&bytes).to_vec(), bytes, fs::metadata(&file).unwrap().modified().unwrap())
        }).collect::<Vec<_>>();
        let outside = marks(&elsewhere);
        let moved = dir.join("checks-real");
        let swapped = std::cell::Cell::new(false);
        let tried = std::cell::Cell::new(false);
        prune_with(&work, &|| {
            if tried.replace(true) {
                return;
            }
            // the race: checks renamed aside and a link to the outside folder put at its name
            if fs::rename(&checks, &moved).is_ok() {
                link_dir(&elsewhere, &checks);
                swapped.set(true);
            }
        });
        assert!(tried.get(), "prune came to a removal");
        assert_eq!(marks(&elsewhere), outside, "every outside folder and its bytes, SHA-256 and modified time");
        #[cfg(windows)]
        {
            assert!(!swapped.get(), "the held checks folder cannot be renamed");
            assert_eq!(fs::read_dir(&checks).unwrap().count(), EXPORTS_KEPT, "the two oldest went, from the real folder");
        }
        if swapped.get() {
            unlink_dir(&checks);
            // the removal went through the held handle, into the real folder: the newer of the two oldest (removed
            // newest first), then the link is seen and nothing more goes
            assert!(!moved.join(&names[1]).exists() && moved.join(&names[0]).exists());
            assert_eq!(fs::read_dir(&moved).unwrap().count(), EXPORTS_KEPT + 1);
        }
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn prune_keeps_the_newest_and_deletes_only_plain_check_folders() {
        let dir = temp("prune");
        let work = dir.join("work");
        let checks = work.join(CHECKS_DIR);
        let elsewhere = dir.join("elsewhere");
        let names: Vec<String> = (0..EXPORTS_KEPT + 3).map(|index| format!("{index:020x}")).collect();
        for root in [&checks, &elsewhere] {
            for name in &names {
                fs::create_dir_all(root.join(name)).unwrap();
            }
            fs::create_dir_all(root.join("not-a-check")).unwrap();
        }
        // through a linked checks folder nothing is removed
        let moved = dir.join("checks-real");
        fs::rename(&checks, &moved).unwrap();
        link_dir(&elsewhere, &checks);
        prune(&work);
        assert_eq!(fs::read_dir(&elsewhere).unwrap().count(), EXPORTS_KEPT + 4, "nothing deleted through the link");
        unlink_dir(&checks);
        fs::rename(&moved, &checks).unwrap();
        // the plain folder: three check folders over the limit go, anything else stays
        prune(&work);
        let left: Vec<String> = fs::read_dir(&checks).unwrap().flatten().map(|entry| entry.file_name().to_string_lossy().into_owned()).collect();
        assert_eq!(left.len(), EXPORTS_KEPT + 1, "{}", left.len());
        assert!(left.contains(&"not-a-check".to_owned()));
        let _ = fs::remove_dir_all(dir);
    }

    /// A file symlink at `link` to `target`: true when made. Windows needs the symlink privilege (an elevated or
    /// Developer Mode account); without it the case is skipped locally and fails under CI, which has it.
    fn file_symlink(target: &Path, link: &Path) -> bool {
        #[cfg(unix)]
        let made = std::os::unix::fs::symlink(target, link);
        #[cfg(windows)]
        let made = std::os::windows::fs::symlink_file(target, link);
        match made {
            Ok(()) => true,
            Err(error) if std::env::var_os("CI").is_none() => {
                println!("file symlink skipped: {error}");
                false
            }
            Err(error) => panic!("file symlink: {error}"),
        }
    }

    /// A directory link: an NTFS junction on Windows, a symlink elsewhere (as edit.rs's link tests make them).
    fn link_dir(target: &Path, link: &Path) {
        #[cfg(windows)]
        {
            let status = Command::new("cmd").args(["/C", "mklink", "/J"]).arg(link).arg(target).stdout(Stdio::null()).status().unwrap();
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

    #[test]
    fn a_linked_checks_folder_is_refused() {
        let dir = temp("linked");
        let work = dir.join("work");
        let elsewhere = dir.join("elsewhere");
        fs::create_dir_all(&elsewhere).unwrap();
        let video = dir.join("v.mp4");
        fs::write(&video, b"v").unwrap();
        let missing = dir.join("missing").join("ffmpeg");
        link_dir(&elsewhere, &work.join(CHECKS_DIR));
        run(&work, &missing, plain_plan(1.0, false, None).for_video(video.clone(), false, stamp_of(&video)));
        assert_eq!(fs::read_dir(&elsewhere).unwrap().count(), 0, "nothing written through the link");
        unlink_dir(&work.join(CHECKS_DIR));
        // a check folder that is a link is refused too, and never read
        fs::create_dir(work.join(CHECKS_DIR)).unwrap();
        link_dir(&elsewhere, &check_dir(&work, &video));
        fs::write(elsewhere.join("check.json"), serde_json::to_vec(&ExportCheck::new(&video, "DONE")).unwrap()).unwrap();
        run(&work, &missing, plain_plan(1.0, false, None).for_video(video.clone(), false, stamp_of(&video)));
        let mut exports = vec![json!({"path": display_path(&video)})];
        attach(&work, &mut exports);
        assert_eq!(fs::read_dir(&elsewhere).unwrap().count(), 1, "only the planted file is there");
        assert_eq!(serde_json::from_slice::<Value>(&fs::read(elsewhere.join("check.json")).unwrap()).unwrap()["state"], "DONE");
        assert!(exports[0].get("check").is_none());
        unlink_dir(&check_dir(&work, &video));
        let _ = fs::remove_dir_all(dir);
    }

    /// With DUBFLOW_TEST_FFMPEG (ffprobe beside it): the check against real FFmpeg.
    #[test]
    fn real_export_check_finds_black_and_frozen_stretches() {
        let Ok(ffmpeg) = std::env::var("DUBFLOW_TEST_FFMPEG") else { return };
        let ffmpeg = PathBuf::from(ffmpeg);
        let dir = temp("real");
        let work = dir.join("work");
        let video = dir.join("clip.mp4");
        // testsrc2 4 s, black 3 s, one frame held 3 s, testsrc2 2 s; a sine under it all. MPEG-4 Part 2:
        // every FFmpeg has it, the shipped LGPL one has no libx264.
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=4", "-f", "lavfi", "-i",
            "color=black:s=320x240:r=25:d=3", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=3,trim=end_frame=1,tpad=stop_mode=clone:stop_duration=3",
            "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=2", "-f", "lavfi", "-i", "sine=f=330:d=12", "-filter_complex",
            "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a", "-c:v", "mpeg4", "-q:v", "3", "-c:a", "aac", "-shortest"])
            .arg(&video).status().unwrap();
        assert!(made.success());
        let bytes = fs::read(&video).unwrap();
        let plan = plain_plan(12.0, true, Some(11_500));
        run(&work, &ffmpeg, plan.clone().for_video(video.clone(), true, stamp_of(&video)));
        let report = read_check(&work, &video);
        assert_eq!(report["state"], "DONE", "{report}");
        let checks: HashMap<String, Value> = report["checks"].as_array().unwrap().iter().map(|check| (check["id"].as_str().unwrap().to_owned(), check.clone())).collect();
        for id in ["decode", "duration", "audio_stream", "audio", "captions"] {
            assert_eq!(checks[id]["ok"], true, "{id}: {}", checks[id]);
        }
        assert_eq!(checks["subtitle_frame"]["ok"], Value::Null);
        assert_eq!(report["black"], json!([[4.0, 7.0]]));
        let frozen = report["frozen"].as_array().unwrap();
        assert_eq!(frozen.len(), 1, "{report}");
        let (start, end) = (frozen[0][0].as_f64().unwrap(), frozen[0][1].as_f64().unwrap());
        assert!((start - 7.0).abs() < 0.01 && (10.0..10.2).contains(&end), "{start}–{end}");
        assert_eq!(report["warnings"], json!(["black", "frozen"]));
        // held on purpose: not reported
        let held = Plan { intended_holds: vec![(7.0, 10.0)], ..plan.clone() };
        run(&work, &ffmpeg, held.clone().for_video(video.clone(), true, stamp_of(&video)));
        assert_eq!(read_check(&work, &video)["frozen"], json!([]));
        assert_eq!(fs::read(&video).unwrap(), bytes, "the video is never changed");
        // a sound track that is there but silent (#935): a warning when the edit has sound, fine when it has none
        let silent = dir.join("silent.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=3", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-t", "3"]).arg(&silent).status().unwrap();
        assert!(made.success());
        run(&work, &ffmpeg, plain_plan(3.0, true, None).for_video(silent.clone(), false, stamp_of(&silent)));
        let report = read_check(&work, &silent);
        let audio = |report: &Value| report["checks"].as_array().unwrap().iter().find(|check| check["id"] == "audio").unwrap().clone();
        assert_eq!((report["state"].as_str(), audio(&report)["ok"].as_bool()), (Some("DONE"), Some(false)), "{report}");
        assert!(audio(&report)["detail"].as_str().unwrap().starts_with("bản sửa có tiếng nhưng âm thanh xuất ra im lặng"), "{report}");
        run(&work, &ffmpeg, plain_plan(3.0, false, None).for_video(silent.clone(), false, stamp_of(&silent)));
        assert_eq!(audio(&read_check(&work, &silent))["ok"], true, "an edit without sound exported silent is fine");
        // zeroed bytes: FFmpeg exits 0 but logs decode errors
        let zeroed = dir.join("zeroed.mp4");
        let mut damaged = bytes.clone();
        let third = damaged.len() / 3;
        damaged[third..third + 20_000].fill(0);
        fs::write(&zeroed, &damaged).unwrap();
        run(&work, &ffmpeg, held.clone().for_video(zeroed.clone(), true, stamp_of(&zeroed)));
        let report = read_check(&work, &zeroed);
        assert_eq!(report["checks"][0]["id"], "decode");
        assert_eq!(report["checks"][0]["ok"], false, "{report}");
        // no moov: FFmpeg fails; the file is left as it was
        let truncated = dir.join("truncated.mp4");
        fs::write(&truncated, &bytes[..bytes.len() / 2]).unwrap();
        run(&work, &ffmpeg, held.clone().for_video(truncated.clone(), true, stamp_of(&truncated)));
        let report = read_check(&work, &truncated);
        assert_eq!((report["state"].as_str(), report["checks"][0]["ok"].as_bool()), (Some("DONE"), Some(false)), "{report}");
        assert_eq!(fs::read(&truncated).unwrap(), &bytes[..bytes.len() / 2]);
        // Dừng kiểm tra mid-run, while FFmpeg decodes (recheck 8: not before it starts): the decode is ended at once,
        // STOPPED, the video untouched
        let long = dir.join("long.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=600", "-f", "lavfi", "-i",
            "sine=f=330:d=600", "-c:v", "mpeg4", "-q:v", "20", "-c:a", "aac", "-shortest"]).arg(&long).status().unwrap();
        assert!(made.success());
        let long_bytes = fs::read(&long).unwrap();
        let worker = {
            let (work, ffmpeg, plan) = (work.clone(), ffmpeg.clone(), plain_plan(600.0, true, None).for_video(long.clone(), false, stamp_of(&long)));
            std::thread::spawn(move || run(&work, &ffmpeg, plan))
        };
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut percent = 0.0;
        while Instant::now() < deadline && !worker.is_finished() {
            let report = load_check(&work, &check_dir(&work, &long)).unwrap_or_default();
            percent = report["percent"].as_f64().unwrap_or(0.0);
            if report["state"] == "RUNNING" && percent > 0.0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(percent > 0.0 && percent < 90.0, "the decode was seen running ({percent}%)");
        let stopped = Instant::now();
        cancel(&work, &long);
        worker.join().unwrap();
        assert!(stopped.elapsed() < Duration::from_secs(3), "Dừng kiểm tra ended the running FFmpeg ({:?})", stopped.elapsed());
        assert_eq!(read_check(&work, &long)["state"], "STOPPED");
        assert_eq!(fs::read(&long).unwrap(), long_bytes);
        let _ = fs::remove_dir_all(dir);
        println!("export-check: ran against real FFmpeg");
    }
}
