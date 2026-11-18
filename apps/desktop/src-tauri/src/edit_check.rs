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
//! of the export's size, and FFmpeg's `bbox` reports where they landed.
//!
//! The result is never a gate: it has no "passed" or READY state, nothing
//! reads it to allow or refuse anything, and the check never deletes, renames,
//! re-encodes or blocks the video. It only reads the video and writes in its
//! own app-owned folder, `work/checks/<file_key(video)>/`, never beside the
//! user's file, and it never touches `status.json` or `exports.json`.
use super::*;

/// = final_qc.SYNC_TOLERANCE_MS (a test reads final_qc.py).
pub(super) const SYNC_TOLERANCE_MS: f64 = 120.0;
/// = final_qc.CLIP_DBTP (a test reads final_qc.py).
pub(super) const CLIP_DBTP: f64 = 0.0;
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
const BUSY_CHECK: &str = "Video này đang được kiểm tra kỹ thuật ở cửa sổ DubFlow khác";

/// Checks running in this window, by check folder: "Dừng kiểm tra" stops them,
/// and a RUNNING check.json that is not here and whose lease is free was left by a closed app.
static CHECKING: LazyLock<Mutex<HashMap<PathBuf, Arc<Running>>>> = LazyLock::new(|| Mutex::new(HashMap::new()));
/// Set when DubFlow closes (`stop_all`): no check starts after that.
static CLOSING: AtomicBool = AtomicBool::new(false);
/// One check at a time in this window: each is a full decode, so they never pile up beside the next export.
static TURN: Mutex<()> = Mutex::new(());

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
}

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
}

impl Plan {
    /// The plan for the video `run_export` published, and whether its .srt was written.
    pub(super) fn for_video(mut self, video: PathBuf, srt_written: bool) -> Self {
        self.video = video;
        if !srt_written {
            self.srt_end_ms = None;
        }
        self
    }
}

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
                   framing: &Framing) -> Plan {
    Plan {
        video: PathBuf::new(),
        expected_seconds,
        has_audio,
        srt_end_ms: cues.iter().map(|cue| cue.end_ms).max(),
        intended_holds: intended_holds(edit, stills),
        subtitles: subtitle_frame(burns, framing),
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
    let (ew, eh) = (f64::from(framing.export.0.max(1)), f64::from(framing.export.1.max(1)));
    let (ow, oh) = (f64::from(framing.out.0.max(1)), f64::from(framing.out.1.max(1)));
    // `reframe_filter` "fill": the frame scaled up and its middle kept along the long axis.
    let filled = framing.fill == "fill" && framing.out != framing.export;
    let (kx, ky) = if !filled { (1.0, 1.0) } else if ow / oh < ew / eh { ((ow / oh) / (ew / eh), 1.0) } else { (1.0, (ew / eh) / (ow / oh)) };
    let window = ((1.0 - kx) / 2.0 * ew, (1.0 - ky) / 2.0 * eh, (1.0 + kx) / 2.0 * ew, (1.0 + ky) / 2.0 * eh);
    let why = match (filled, framing.aspect) {
        (true, Some(aspect)) => format!("khung {aspect}, cắt cho đầy"),
        (true, None) => "cắt cho đầy khung".to_owned(),
        (false, _) => "chữ vượt ra ngoài khung hình".to_owned(),
    };
    Some(SubtitleFrame { burns, export: framing.export, window, why })
}

/// One moment per line (its middle), each with the lines of every layer shown at that moment, so lines shown
/// together stack as in the export. Returns the start of the line each moment is for, and for each layer the lines
/// it shows at each moment, in `burn_ass`'s order.
fn moments(burns: &[BurnSubtitles]) -> (Vec<u64>, Vec<Vec<Vec<&Cue>>>) {
    let mut at: Vec<(u64, u64)> = burns.iter().flat_map(|burn| burn.lines.iter())
        .map(|cue| (cue.start_ms + cue.end_ms.saturating_sub(cue.start_ms) / 2, cue.start_ms)).collect();
    at.sort_unstable();
    at.dedup_by_key(|(middle, _)| *middle);
    let layers = burns.iter().map(|burn| {
        let mut lines: Vec<&Cue> = burn.lines.iter().collect();
        lines.sort_by_key(|line| (line.start_ms, line.end_ms));
        let (mut next, mut shown) = (0, Vec::<&Cue>::new());
        at.iter().map(|&(middle, _)| {
            while next < lines.len() && lines[next].start_ms <= middle {
                shown.push(lines[next]);
                next += 1;
            }
            shown.retain(|line| line.end_ms > middle);
            shown.clone()
        }).collect()
    }).collect();
    (at.into_iter().map(|(_, start)| start).collect(), layers)
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

/// The measured result: a moment's drawing must stay off the frame's outermost pixels (ink there runs on past the
/// edge) and inside the kept window. A moment where libass drew nothing is not measured, never called fine.
fn fit_of(frame: &SubtitleFrame, starts: &[u64], boxes: &HashMap<usize, (i64, i64, i64, i64)>) -> SubtitleFit {
    let (width, height) = (i64::from(frame.export.0), i64::from(frame.export.1));
    let (wx0, wy0, wx1, wy1) = frame.window;
    let (mut cut, mut blank) = (Vec::new(), 0);
    for (index, &start) in starts.iter().enumerate() {
        let Some(&(x0, y0, x1, y1)) = boxes.get(&index) else {
            blank += 1;
            continue;
        };
        let on_frame = x0 > 0 && y0 > 0 && x1 < width - 1 && y1 < height - 1;
        let in_window = x0 as f64 >= wx0 - 0.5 && y0 as f64 >= wy0 - 0.5 && (x1 + 1) as f64 <= wx1 + 0.5 && (y1 + 1) as f64 <= wy1 + 0.5;
        if !(on_frame && in_window) {
            cut.push(start);
        }
    }
    cut.sort_unstable();
    cut.dedup();
    let unseen = if blank > 0 { format!(" {blank} câu chưa đo được (libass không vẽ ra chữ).") } else { String::new() };
    if cut.is_empty() {
        return if blank == 0 {
            SubtitleFit { ok: Some(true), detail: "phụ đề nằm trong khung — đo trên chữ libass vẽ".into(), at_ms: Vec::new() }
        } else {
            SubtitleFit { ok: None, detail: format!("không thấy câu nào bị cắt.{unseen}"), at_ms: Vec::new() }
        };
    }
    let named: Vec<String> = cut.iter().take(NAMED).map(|ms| clock(*ms as f64 / 1000.0)).collect();
    let more = if cut.len() > NAMED { "…" } else { "" };
    let detail = format!("{} câu bị cắt mép ({}): {}{more} — đo trên chữ libass vẽ.{unseen}", cut.len(), frame.why, named.join(", "));
    cut.truncate(MAX_SPANS);
    SubtitleFit { ok: Some(false), detail, at_ms: cut }
}

/// Where libass draws the burned lines: the export's own layers painted white (`measure_ass`) on a black frame of
/// the export's size, one frame per moment, and FFmpeg's `bbox` around what was drawn. Reads no part of the video.
fn measure_subtitles(work: &Path, ffmpeg: &Path, frame: &SubtitleFrame, dir: &Path, running: &Running) -> SubtitleFit {
    let unmeasured = |why: String| SubtitleFit { ok: None, detail: format!("chưa đo được phụ đề: {why}"), at_ms: Vec::new() };
    let (starts, layers) = moments(&frame.burns);
    let mut filters = Vec::new();
    for (index, (burn, shown)) in frame.burns.iter().zip(&layers).enumerate() {
        let file = dir.join(format!("measure-{}.ass", index + 1));
        if !(unlinked(work) && plain(dir)) || write_atomic(&file, measure_ass(&burn.style, shown).as_bytes()).is_err() {
            return unmeasured("không ghi được tệp đo".into());
        }
        filters.push(subtitles_filter(&display_path(&file)));
    }
    let (width, height) = frame.export;
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "level+info", "-f", "lavfi", "-i"]
        .into_iter().map(OsString::from).collect();
    args.push(format!("color=c=black:s={width}x{height}:r=1:d={}", starts.len()).into());
    args.extend(crate::gpu::thread_args(false));
    args.extend(["-vf".into(), format!("{},bbox", filters.join(",")).into()]);
    args.extend(["-f", "null", "-"].map(OsString::from));
    let log_path = dir.join("measure.log");
    if let Err(error) = run_tracked(ffmpeg, args, work, &log_path, running, |_| {}) {
        return unmeasured(if error.is_empty() { "FFmpeg dừng giữa chừng".into() } else { error });
    }
    // About 150 bytes of log per moment.
    let limit = LOG_LIMIT + 512 * starts.len() as u64;
    let text = fs::File::open(&log_path).ok().map(|file| {
        let mut bytes = Vec::new();
        let _ = std::io::Read::read_to_end(&mut std::io::Read::take(file, limit), &mut bytes);
        String::from_utf8_lossy(&bytes).into_owned()
    }).unwrap_or_default();
    fit_of(frame, &starts, &read_boxes(&text))
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
    /// `loudnorm`'s `input_tp`; None when not printed or "-inf" (silence).
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
                .filter(|value| value.is_finite());
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
}

impl ExportCheck {
    fn new(video: &Path, state: &'static str) -> Self {
        Self { schema_version: 1, kind: "export_check", advisory: true, state, percent: 0.0, video: display_path(video), checks: Vec::new(),
            warnings: Vec::new(), unmeasured: Vec::new(), black: Vec::new(), frozen: Vec::new(), error: None, waiting: false,
            checked_at: unix_now() }
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

/// The check folder of `video` below `work`.
fn check_dir(work: &Path, video: &Path) -> PathBuf {
    work.join(CHECKS_DIR).join(file_key(video))
}

/// Written only while `work`, `checks` and the folder are still plain folders.
fn save(work: &Path, dir: &Path, report: &ExportCheck) {
    if unlinked(work) && dir.parent().is_some_and(plain) && plain(dir) {
        let _ = write_atomic(&dir.join("check.json"), &serde_json::to_vec(report).unwrap_or_default());
    }
}

/// Check the published `plan.video`. Every outcome leaves the video exactly as exported.
pub(super) fn run(work: &Path, ffmpeg: &Path, plan: Plan) {
    let dir = check_dir(work, &plan.video);
    if !unlinked(work) || plain_folder(&work.join(CHECKS_DIR)).is_err() || plain_folder(&dir).is_err() {
        return;
    }
    // Its own lease: a new export of the same video is never refused because of a check.
    let Ok(lease) = crate::work_lease::take(&dir, BUSY_CHECK) else { return };
    let running = Arc::new(Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) });
    if !register(&CHECKING, &CLOSING, &dir, &running) {
        return; // DubFlow is closing
    }
    if unlinked(work) && plain(&dir) {
        let _ = write_atomic(&dir.join("plan.json"), &serde_json::to_vec(&plan).unwrap_or_default());
    }
    let report = match wait_turn(work, &dir, &plan.video, &running) {
        Some(turn) => {
            let report = check_once(work, ffmpeg, &plan, &dir, &running);
            drop(turn);
            report
        }
        None => ExportCheck::new(&plan.video, "STOPPED"),
    };
    save(work, &dir, &report);
    if let Ok(mut checking) = CHECKING.lock() {
        checking.remove(&dir);
    }
    drop(lease);
    prune(work);
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
fn wait_turn(work: &Path, dir: &Path, video: &Path, running: &Running) -> Option<std::sync::MutexGuard<'static, ()>> {
    let mut announced = false;
    loop {
        if running.cancelled.load(Ordering::SeqCst) {
            return None;
        }
        match TURN.try_lock() {
            Ok(turn) => return Some(turn),
            Err(std::sync::TryLockError::Poisoned(poisoned)) => return Some(poisoned.into_inner()),
            Err(std::sync::TryLockError::WouldBlock) => {}
        }
        if !announced {
            save(work, dir, &ExportCheck { waiting: true, ..ExportCheck::new(video, "RUNNING") });
            announced = true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn check_once(work: &Path, ffmpeg: &Path, plan: &Plan, dir: &Path, running: &Running) -> ExportCheck {
    let unavailable = |error: String| ExportCheck { error: Some(error), ..ExportCheck::new(&plan.video, "UNAVAILABLE") };
    if !ffmpeg.is_file() {
        return unavailable("không tìm thấy FFmpeg".into());
    }
    save(work, dir, &ExportCheck::new(&plan.video, "RUNNING"));
    let probe = probe_with(&ffprobe_beside(ffmpeg), &plan.video).ok();
    let fps = probe.as_ref().map_or(30.0, |probe| fps_number(&probe.fps));
    let total = probe.as_ref().map_or(plan.expected_seconds, |probe| probe.duration);
    let has_audio = probe.as_ref().is_some_and(|probe| probe.has_audio);
    let log_path = dir.join("ffmpeg.log");
    let mut last_us: Option<i64> = None;
    let mut written = Instant::now();
    let result = run_tracked(ffmpeg, check_args(&plan.video, has_audio, crate::gpu::thread_args(true)), work, &log_path, running, |line| {
        if let Some(value) = line.strip_prefix("out_time_us=") {
            if let Ok(us) = value.trim().parse::<i64>() {
                last_us = Some(us.max(0));
            }
        } else if line.starts_with("progress=") && written.elapsed() >= Duration::from_secs(1) {
            let percent = last_us.map_or(0.0, |us| progress_percent(us, total)).floor();
            save(work, dir, &ExportCheck { percent, ..ExportCheck::new(&plan.video, "RUNNING") });
            written = Instant::now();
        }
    });
    if running.cancelled.load(Ordering::SeqCst) {
        return ExportCheck::new(&plan.video, "STOPPED");
    }
    let text = fs::File::open(&log_path).ok().map(|file| {
        let mut bytes = Vec::new();
        let _ = std::io::Read::read_to_end(&mut std::io::Read::take(file, LOG_LIMIT), &mut bytes);
        String::from_utf8_lossy(&bytes).into_owned()
    }).unwrap_or_default();
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
        Some(frame) => measure_subtitles(work, ffmpeg, frame, dir, running),
        None => SubtitleFit { ok: None, detail: "không có phụ đề in vào hình".into(), at_ms: Vec::new() },
    };
    if running.cancelled.load(Ordering::SeqCst) {
        return ExportCheck::new(&plan.video, "STOPPED");
    }
    let (checks, black, frozen) = checks_of(plan, probe.as_ref(), &pass, &log, &subtitles);
    ExportCheck::new(&plan.video, "DONE").with_checks(checks, &black, &frozen)
}

/// A check folder's name: `file_key`'s 20 hex digits.
fn is_key(name: &std::ffi::OsStr) -> bool {
    name.to_str().is_some_and(|name| name.len() == 20 && name.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
}

/// Keep the newest `EXPORTS_KEPT` check folders of `work` (exports.json keeps as many entries).
/// The links are looked at again here, after the decode, and before each folder is removed:
/// only `work/checks/<key>` folders, all plain, are ever deleted.
fn prune(work: &Path) {
    let checks = work.join(CHECKS_DIR);
    let safe = || unlinked(work) && plain(&checks);
    if !safe() {
        return;
    }
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
            let _ = fs::remove_dir_all(folder);
        }
    }
}

/// "Dừng kiểm tra": stop this window's check of `video`, if one runs.
pub(super) fn cancel(work: &Path, video: &Path) {
    if let Some(entry) = CHECKING.lock().ok().and_then(|checking| checking.get(&check_dir(work, video)).cloned()) {
        stop(&entry);
    }
}

/// A check.json of at most `CHECK_LIMIT` bytes that is an export check.
fn read_check_file(path: &Path) -> Option<Value> {
    if fs::metadata(path).map_or(true, |metadata| metadata.len() > CHECK_LIMIT) {
        return None;
    }
    fs::read(path).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).filter(|value| value["kind"] == "export_check")
}

/// A check of this window runs in `dir`.
fn ours(dir: &Path) -> bool {
    CHECKING.lock().is_ok_and(|checking| checking.contains_key(dir))
}

/// With the check's lease taken: check.json read again, because a check that ended since
/// `attach` first read it has saved its result, which is kept and never overwritten. Still
/// RUNNING with no window checking: saved and returned as INTERRUPTED. None: left as it is.
fn settle(work: &Path, dir: &Path, video: &Path) -> Option<Value> {
    if let Some(now) = read_check_file(&dir.join("check.json")).filter(|now| now["state"] != "RUNNING") {
        return Some(now);
    }
    if ours(dir) {
        return None;
    }
    let stale = ExportCheck::new(video, "INTERRUPTED");
    save(work, dir, &stale);
    serde_json::to_value(&stale).ok()
}

/// Each of the newest exports gets its check (`entry["check"]`) when one was made;
/// a RUNNING check no window runs any more is reported, and saved, as INTERRUPTED.
pub(super) fn attach(work: &Path, exports: &mut [Value]) {
    for entry in exports.iter_mut().rev().take(ATTACHED) {
        let Some(video) = entry["path"].as_str().map(PathBuf::from) else { continue };
        let dir = check_dir(work, &video);
        let path = dir.join("check.json");
        if !plain(&dir) {
            continue;
        }
        let Some(mut value) = read_check_file(&path) else { continue };
        // The lease file is only made in `work/checks/<key>` while all three are plain folders.
        if value["state"] == "RUNNING" && !ours(&dir) && unlinked(work) && plain(&work.join(CHECKS_DIR)) {
            // A free lease means no window is checking: the app closed mid-check.
            if let Ok(lease) = crate::work_lease::take(&dir, BUSY_CHECK) {
                value = settle(work, &dir, &video).unwrap_or(value);
                drop(lease);
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
        assert_eq!(read_log(&silent, 33.0).peak, None, "silence has no peak");
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
    /// 41 Chinese characters with no space: libass (no Unicode line breaking for a native .ass) keeps it one row.
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
    fn moments_show_what_is_on_screen_together() {
        let first = BurnSubtitles { style: style(json!({})), lines: vec![cue(4_000, 6_000, "B"), cue(0, 10_000, "A"), cue(12_000, 13_000, "C")] };
        let second = BurnSubtitles { style: style(json!({})), lines: vec![cue(5_000, 7_000, "x")] };
        let burns = [first, second];
        let (starts, layers) = moments(&burns);
        // middles 5 000 (A), 5 000 (B, the same moment), 6 000 (x; B has just ended), 12 500 (C)
        assert_eq!(starts, [0, 5_000, 12_000]);
        let texts = |shown: &Vec<&Cue>| shown.iter().map(|line| line.text.clone()).collect::<Vec<_>>();
        assert_eq!(layers[0].iter().map(texts).collect::<Vec<_>>(), [vec!["A", "B"], vec!["A"], vec!["C"]]);
        assert_eq!(layers[1].iter().map(texts).collect::<Vec<_>>(), [vec!["x"], vec!["x"], vec![]]);
        // the measure script draws moment i from second i, painted white, at the layer's dragged spot
        let mut dragged = style(json!({"x": 0.25, "y": 0.5, "box": "#101010", "box_opacity": 0.5}));
        dragged.primary = "#FFFF00".into();
        let script = measure_ass(&dragged, &layers[0]);
        assert!(script.contains("Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an5\\pos(480,540)}A\n"), "{script}");
        assert_eq!(script.matches("Dialogue:").count(), 4);
        assert!(script.contains(",&H00FFFFFF,&H000000FF,&H00FFFFFF,&H80FFFFFF,-1,"), "{script}");
        assert!(!script.contains("&H0000FFFF") && !script.contains("101010"), "no real colour left");
        // a box nobody can see stays unseen
        let clear = measure_ass(&style(json!({"box": "#000000", "box_opacity": 0.0})), &layers[1]);
        assert!(clear.contains(",&HFFFFFFFF,-1,"), "{clear}");
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
        let boxes = |list: &[(usize, (i64, i64, i64, i64))]| list.iter().copied().collect::<HashMap<_, _>>();
        let short = (894, 979, 1025, 1024);
        let long = (358, 976, 1560, 1034);
        let fine = fit_of(&wide, &[2_000, 12_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!((fine.ok, fine.detail.as_str()), (Some(true), "phụ đề nằm trong khung — đo trên chữ libass vẽ"));
        let cut = fit_of(&tall, &[2_000, 12_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!((cut.ok, cut.at_ms.clone()), (Some(false), vec![12_000]));
        assert_eq!(cut.detail, "1 câu bị cắt mép (khung 9:16, cắt cho đầy): 0:12 — đo trên chữ libass vẽ.");
        // ink on the outermost column or row runs on past the frame
        let edge = fit_of(&wide, &[3_000, 4_000, 5_000], &boxes(&[(0, (0, 975, 1919, 1028)), (1, (10, 1000, 300, 1079)), (2, (1, 1, 1918, 1078))]));
        assert_eq!((edge.ok, edge.at_ms), (Some(false), vec![3_000, 4_000]));
        assert!(edge.detail.starts_with("2 câu bị cắt mép (chữ vượt ra ngoài khung hình): 0:03, 0:04"), "{}", edge.detail);
        // nothing drawn for a moment: not measured, never called fine
        let blank = fit_of(&wide, &[2_000, 12_000], &boxes(&[(0, short)]));
        assert_eq!(blank.ok, None);
        assert!(blank.detail.contains("1 câu chưa đo được"), "{}", blank.detail);
        let both = fit_of(&tall, &[2_000, 12_000, 20_000], &boxes(&[(0, short), (1, long)]));
        assert_eq!(both.ok, Some(false));
        assert!(both.detail.ends_with("1 câu chưa đo được (libass không vẽ ra chữ)."), "{}", both.detail);
        // at most NAMED times in words, MAX_SPANS kept
        let many: Vec<u64> = (0..60).map(|index| index * 1_000).collect();
        let all = fit_of(&wide, &many, &HashMap::from_iter((0..60).map(|index| (index, (0, 0, 10, 10)))));
        assert_eq!(all.at_ms.len(), MAX_SPANS);
        assert!(all.detail.starts_with("60 câu bị cắt mép (chữ vượt ra ngoài khung hình): 0:00, 0:01, 0:02, 0:03, 0:04… "), "{}", all.detail);
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
        let planned = plan(&edit, &[false], 20.0, true, &[], &[&line], &wide);
        assert_eq!(planned.subtitles, subtitle_frame(&[&line], &wide));
        assert_eq!(planned.subtitles.unwrap().window, (0.0, 0.0, 1920.0, 1080.0));
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
        let measure = |lines: Vec<Cue>, style: SubtitleStyle, framing: &Framing| {
            let burn = BurnSubtitles { style, lines };
            measure_subtitles(&work, &ffmpeg, &subtitle_frame(&[&burn], framing).unwrap(), &folder, &running)
        };
        let wide = Framing { export: (1920, 1080), out: (1920, 1080), fill: "black", aspect: None };
        let four_three = Framing { export: (1920, 1080), out: (1440, 1080), fill: "fill", aspect: Some("4:3") };
        // short lines at the bottom, one with a box: inside
        let fine = measure(vec![cue(1_000, 3_000, "Chào"), cue(4_000, 6_000, LONG)], style(json!({})), &wide);
        assert_eq!(fine.ok, Some(true), "{}", fine.detail);
        assert_eq!(measure(vec![cue(1_000, 3_000, "Chào")], style(json!({"box": "#000000", "box_opacity": 0.6})), &four_three).ok, Some(true));
        // a Chinese sentence with no space stays one row: wider than the picture, and cut by a 4:3 fill
        let chinese = measure(vec![cue(1_000, 3_000, "Chào"), cue(5_000, 7_000, CHINESE)], style(json!({})), &wide);
        assert_eq!((chinese.ok, chinese.at_ms.clone()), (Some(false), vec![5_000]), "{}", chinese.detail);
        assert_eq!(measure(vec![cue(5_000, 7_000, CHINESE)], style(json!({})), &four_three).at_ms, [5_000]);
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
        // two lines at once stack: both are measured together
        let stacked = measure(vec![cue(1_000, 5_000, "Dòng một"), cue(2_000, 4_000, "Dòng hai")], style(json!({})), &wide);
        assert_eq!(stacked.ok, Some(true), "{}", stacked.detail);
        let _ = fs::remove_dir_all(dir);
        println!("subtitle-measure: ran against real FFmpeg");
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
        let plan = plain_plan(1.0, true, None).for_video(video.clone(), false);
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
            let (work, missing, plan) = (work.clone(), missing.clone(), plain_plan(1.0, false, None).for_video(video.clone(), false));
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
        run(&work, &missing, plain_plan(1.0, false, None).for_video(video.clone(), false));
        assert_eq!(fs::read_dir(&elsewhere).unwrap().count(), 0, "nothing written through the link");
        unlink_dir(&work.join(CHECKS_DIR));
        // a check folder that is a link is refused too, and never read
        fs::create_dir(work.join(CHECKS_DIR)).unwrap();
        link_dir(&elsewhere, &check_dir(&work, &video));
        fs::write(elsewhere.join("check.json"), serde_json::to_vec(&ExportCheck::new(&video, "DONE")).unwrap()).unwrap();
        run(&work, &missing, plain_plan(1.0, false, None).for_video(video.clone(), false));
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
        run(&work, &ffmpeg, plan.clone().for_video(video.clone(), true));
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
        run(&work, &ffmpeg, held.clone().for_video(video.clone(), true));
        assert_eq!(read_check(&work, &video)["frozen"], json!([]));
        assert_eq!(fs::read(&video).unwrap(), bytes, "the video is never changed");
        // zeroed bytes: FFmpeg exits 0 but logs decode errors
        let zeroed = dir.join("zeroed.mp4");
        let mut damaged = bytes.clone();
        let third = damaged.len() / 3;
        damaged[third..third + 20_000].fill(0);
        fs::write(&zeroed, &damaged).unwrap();
        run(&work, &ffmpeg, held.clone().for_video(zeroed.clone(), true));
        let report = read_check(&work, &zeroed);
        assert_eq!(report["checks"][0]["id"], "decode");
        assert_eq!(report["checks"][0]["ok"], false, "{report}");
        // no moov: FFmpeg fails; the file is left as it was
        let truncated = dir.join("truncated.mp4");
        fs::write(&truncated, &bytes[..bytes.len() / 2]).unwrap();
        run(&work, &ffmpeg, held.clone().for_video(truncated.clone(), true));
        let report = read_check(&work, &truncated);
        assert_eq!((report["state"].as_str(), report["checks"][0]["ok"].as_bool()), (Some("DONE"), Some(false)), "{report}");
        assert_eq!(fs::read(&truncated).unwrap(), &bytes[..bytes.len() / 2]);
        // Dừng kiểm tra mid-run: STOPPED, the video untouched
        let long = dir.join("long.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=240", "-f", "lavfi", "-i",
            "sine=f=330:d=240", "-c:v", "mpeg4", "-q:v", "20", "-c:a", "aac", "-shortest"]).arg(&long).status().unwrap();
        assert!(made.success());
        let long_bytes = fs::read(&long).unwrap();
        let worker = {
            let (work, ffmpeg, plan) = (work.clone(), ffmpeg.clone(), plain_plan(240.0, true, None).for_video(long.clone(), false));
            std::thread::spawn(move || run(&work, &ffmpeg, plan))
        };
        let folder = check_dir(&work, &long);
        let deadline = Instant::now() + Duration::from_secs(30);
        while !CHECKING.lock().unwrap().contains_key(&folder) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        cancel(&work, &long);
        worker.join().unwrap();
        assert_eq!(read_check(&work, &long)["state"], "STOPPED");
        assert_eq!(fs::read(&long).unwrap(), long_bytes);
        let _ = fs::remove_dir_all(dir);
        println!("export-check: ran against real FFmpeg");
    }
}
