//! Chỉnh sửa, xuất song song: a long export rendered as parts side by side.
//!
//! One FFmpeg runs the edit's filter graph on about one core, so a strong PC
//! (and its NVENC) mostly waits. A long export is therefore cut at moments
//! where nothing spans the cut (no transition, no moving or fading overlay, no
//! zoom, reverse or held frame), each part is the same edit over its window of
//! output time, video only, its inputs seeked close to what it shows (`-ss`
//! with `-itsoffset` keeps their own clock, so every trim stays valid), and
//! cut to an exact frame count so the parts add up to the timeline. The sound
//! is the whole edit's sound, rendered once beside them with a tiny picture
//! and only key frames decoded (it never depends on the picture). The parts
//! and the sound are then joined without re-encoding. Any failure falls back
//! to the single FFmpeg run. Mirrors `_render_parts` in engine/dubflow/media/adapter.py.
use super::*;
use std::sync::atomic::{AtomicU64, AtomicUsize};

/// Shorter exports gain too little from parts to be worth it.
pub(super) const PARALLEL_MIN_SECONDS: f64 = 4.0 * 60.0;
/// At least this many parts for a long export, so a part is about 10 minutes.
const PART_SECONDS: f64 = 10.0 * 60.0;
/// No part shorter than this.
const MIN_PART: f64 = 20.0;
/// Inputs are seeked this far before the first moment a part needs: a seek
/// lands on a key frame, sometimes (MPEG-TS) a little after the asked time.
const SEEK_MARGIN: f64 = 30.0;
/// A cut stays this far from a joint it does not fall on, never leaving a sliver of a segment.
const NEAR_JOINT: f64 = 0.5;
/// Seconds after a transition or before it that no cut may fall in.
const TRANSITION_MARGIN: f64 = 1.0;
/// The sound pass's picture: its short side, and frame rate. Nothing of it is kept.
const SOUND_SHORT_SIDE: f64 = 320.0;
const SOUND_FPS: &str = "5/1";

/// What a long export keeps to render as parts.
pub(super) struct Source {
    /// The main video then the other media (not the sounds or stems after them).
    pub(super) media_count: usize,
    pub(super) frame: Frame,
    /// The text layer and reframe steps of `Finish`, after the edit's picture.
    pub(super) texts: Option<String>,
    pub(super) reframe: Option<String>,
    /// Che and Phụ đề on the main video before it is cut (`with_cover`).
    pub(super) prefilter: Option<String>,
    /// The whole edit's sound only (`export_script` with `audio_only`), when there is sound.
    pub(super) sound_script: Option<PathBuf>,
}

/// The sound pass's picture: the frame shrunk to a short side of 320, at 5 frames a second.
pub(super) fn sound_frame(frame: &Frame) -> Frame {
    let scale = (SOUND_SHORT_SIDE / f64::from(frame.width.min(frame.height))).min(1.0);
    Frame { width: even(f64::from(frame.width) * scale), height: even(f64::from(frame.height) * scale), fps: SOUND_FPS.to_owned() }
}

/// Output time each segment starts at: dissolves overlap their neighbours.
fn positions(edit: &Edit) -> Vec<f64> {
    let mut at = 0.0;
    edit.segments.iter().enumerate().map(|(index, segment)| {
        let start = at;
        let overlap: f64 = edit.transitions.iter().filter(|joint| joint.after == index && joint.kind.xfade().is_some()).map(|joint| joint.duration).sum();
        at += segment_length(segment) - overlap;
        start
    }).collect()
}

fn overlay_shown(overlay: &Overlay, total: f64) -> f64 {
    (overlay.end - overlay.start).min(total - overlay.at).max(0.0)
}

/// A segment whose look changes over its length, so it cannot be split.
fn whole(segment: &Segment) -> bool {
    segment.freeze.is_some() || segment.reverse || segment.zoom.is_some()
}

/// Output times no cut may fall strictly inside.
fn blocked(edit: &Edit, total: f64) -> Vec<(f64, f64)> {
    let at = positions(edit);
    let mut spans = Vec::new();
    for (index, segment) in edit.segments.iter().enumerate() {
        if whole(segment) {
            spans.push((at[index], at[index] + segment_length(segment)));
        }
    }
    for joint in &edit.transitions {
        let end = at[joint.after] + segment_length(&edit.segments[joint.after]);
        spans.push((end - joint.duration - TRANSITION_MARGIN, end + joint.duration + TRANSITION_MARGIN));
    }
    for overlay in &edit.overlays {
        if overlay.to.is_some() || overlay.fade_in > 0.0 || overlay.fade_out > 0.0 {
            spans.push((overlay.at, overlay.at + overlay_shown(overlay, total)));
        }
    }
    spans
}

/// Seconds between frames whose time is a whole millisecond (the graph's
/// precision): every frame at 25 fps, every third at 30, every 30th at 29.97.
/// A cut there starts its part's clock exactly where the whole export's is.
fn exact_step(fps: &str) -> f64 {
    let (num, den) = fps.split_once('/').map_or((fps, "1"), |pair| pair);
    let (Ok(num), Ok(den)) = (num.trim().parse::<u64>(), den.trim().parse::<u64>()) else { return 1.0 / fps_number(fps).max(1.0) };
    if num == 0 || den == 0 {
        return 1.0 / 30.0;
    }
    let gcd = |mut a: u64, mut b: u64| { while b != 0 { (a, b) = (b, a % b); } a };
    let frames = num / gcd(num, den * 1000);
    frames as f64 * den as f64 / num as f64
}

/// Up to `count - 1` cuts, each at the allowed moment closest to an even split.
fn cut_points(edit: &Edit, total: f64, fps: &str, count: usize) -> Vec<f64> {
    let step = exact_step(fps);
    let spans = blocked(edit, total);
    let joints = positions(edit);
    let allowed = |t: f64| {
        t >= MIN_PART && t <= total - MIN_PART
            && spans.iter().all(|&(lo, hi)| !(lo < t && t < hi))
            && joints.iter().all(|&joint| (t - joint).abs() < 1e-6 || (t - joint).abs() >= NEAR_JOINT)
    };
    let mut cuts: Vec<f64> = Vec::new();
    for part in 1..count {
        let want = (total * part as f64 / count as f64 / step).round() * step;
        let mut options = vec![want];
        options.extend(spans.iter().filter(|&&(lo, hi)| lo < want && want < hi).flat_map(|&(lo, hi)| [lo, hi]));
        options.extend(joints.iter().copied().filter(|joint| (joint - want).abs() < NEAR_JOINT));
        let best = options.into_iter().filter(|&t| allowed(t)).min_by(|a, b| (a - want).abs().total_cmp(&(b - want).abs()));
        if let Some(cut) = best.filter(|&cut| cuts.last().is_none_or(|&last| cut - last >= MIN_PART)) {
            cuts.push(cut);
        }
    }
    cuts
}

/// The edit over output time `[from, to)`: segments clipped to it (only
/// splittable ones are ever clipped), transitions between two of them, and
/// overlays shown in it, all on the window's own clock.
fn window(edit: &Edit, from: f64, to: f64, stills: &[bool]) -> Edit {
    let at = positions(edit);
    let total = output_seconds(&edit.segments, &edit.transitions);
    let mut kept: Vec<Option<usize>> = vec![None; edit.segments.len()];
    let mut segments = Vec::new();
    for (index, segment) in edit.segments.iter().enumerate() {
        let (begin, length) = (at[index], segment_length(segment));
        let (a, b) = (begin.max(from), (begin + length).min(to));
        if b - a <= 1e-6 {
            continue;
        }
        let mut part = *segment;
        if !whole(segment) && (a > begin + 1e-6 || b < begin + length - 1e-6) {
            part.start = clipped(segment.start + (a - begin) * segment.speed);
            part.end = segment.start + (b - begin) * segment.speed;
        }
        // Its sound fades belong to the sound pass.
        part.fade_in = 0.0;
        part.fade_out = 0.0;
        kept[index] = Some(segments.len());
        segments.push(part);
    }
    let transitions = edit.transitions.iter().filter_map(|joint| match (kept[joint.after], kept.get(joint.after + 1).copied().flatten()) {
        (Some(left), Some(right)) if right == left + 1 => Some(Transition { after: left, ..*joint }),
        _ => None,
    }).collect();
    let overlays = edit.overlays.iter().filter_map(|overlay| {
        let shown = overlay_shown(overlay, total);
        let (a, b) = (overlay.at.max(from), (overlay.at + shown).min(to));
        if b - a <= 1e-6 {
            return None;
        }
        let mut part = overlay.clone();
        if !stills.get(overlay.media).copied().unwrap_or(false) {
            part.start = clipped(overlay.start + (a - overlay.at));
        }
        part.end = part.start + (b - a);
        part.at = a - from;
        Some(part)
    }).collect();
    Edit { segments, transitions, overlays, luts: edit.luts.clone(), stems: Vec::new() }
}

/// A clipped start, down to the millisecond the graph is written in: cuts sit on
/// the frame grid, and a trim rounded past a frame's time would skip that frame.
fn clipped(start: f64) -> f64 {
    (start * 1000.0 + 1e-6).floor() / 1000.0
}

/// Where each input of a part starts reading: just before the first moment it
/// needs, 0 for the start of the file, None when the part does not use it.
fn seeks(part: &Edit, count: usize) -> Vec<Option<f64>> {
    let mut first: Vec<Option<f64>> = vec![None; count];
    let starts = part.segments.iter().map(|segment| (segment.media, segment.start))
        .chain(part.overlays.iter().map(|overlay| (overlay.media, overlay.start)));
    for (media, start) in starts {
        if let Some(slot) = first.get_mut(media) {
            *slot = Some(slot.map_or(start, |known: f64| known.min(start)));
        }
    }
    first.into_iter().map(|start| start.map(|start| {
        let seek = ((start - SEEK_MARGIN) * 1000.0).floor() / 1000.0;
        if seek < 1.0 { 0.0 } else { seek }
    })).collect()
}

/// A part's filter graph: the window's edit as video only, the finish steps on
/// the export's clock, then exactly `frames` frames as `[v]`.
fn part_script(part: &Edit, source: &Source, quality: &str, from: f64, frames: u64) -> String {
    let silent = vec![Input { has_audio: false }; source.media_count];
    let (mut graph, _) = video_graph(part, &silent, &source.frame, quality, "vp", "ap");
    if let Some(filter) = &source.prefilter {
        graph = with_cover(&graph, filter);
    }
    let mut steps: Vec<String> = Vec::new();
    if let Some(texts) = &source.texts {
        // The text layer is timed on the whole export.
        let shift = num(from);
        steps.push(format!("setpts=PTS+{shift}/TB,{texts},setpts=PTS-{shift}/TB"));
    }
    steps.extend(source.reframe.iter().cloned());
    let mut label = "vp".to_owned();
    for (index, filter) in steps.iter().enumerate() {
        let out = format!("vs{index}");
        graph.push_str(&format!(";\n[{label}]{filter}[{out}]"));
        label = out;
    }
    graph.push_str(&format!(";\n[{label}]fps={},tpad=stop_mode=clone:stop_duration=5,trim=end_frame={frames}[v]", source.frame.fps));
    graph
}

struct Part {
    script: PathBuf,
    output: PathBuf,
    seeks: Vec<Option<f64>>,
    frames: u64,
}

fn part_args(job: &ExportJob, part: &Part, stills: &[bool], encoder: &str, threads: usize) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-v", "error", "-y"].map(OsString::from).to_vec();
    for (index, seek) in part.seeks.iter().enumerate() {
        match seek {
            // Unused here: a stand-in keeps the input numbers without reading the file.
            None => args.extend(["-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1"].map(OsString::from)),
            Some(_) if stills[index] => {
                args.extend(["-loop", "1", "-framerate", "30", "-t"].map(OsString::from));
                args.push(num(STILL_SECONDS).into());
                args.push("-i".into());
                args.push(job.inputs[index].as_os_str().to_owned());
            }
            Some(seek) => {
                args.extend(crate::gpu::decode_args(encoder));
                if *seek > 0.0 {
                    let at = num(*seek);
                    args.extend(["-ss".into(), OsString::from(&at), "-itsoffset".into(), OsString::from(&at)]);
                }
                args.push("-i".into());
                args.push(job.inputs[index].as_os_str().to_owned());
            }
        }
    }
    args.push("-/filter_complex".into());
    args.push(part.script.as_os_str().to_owned());
    args.extend(["-map", "[v]"].map(OsString::from));
    args.extend(crate::gpu::rate_args(encoder, job.bitrate));
    let threads = threads.to_string();
    args.extend(["-threads", &threads, "-filter_complex_threads", &threads].map(OsString::from));
    args.extend(["-pix_fmt", "yuv420p", "-r", &job.fps].map(OsString::from));
    args.extend(["-an", "-sn", "-dn", "-map_chapters", "-1", "-progress", "pipe:1", "-nostats", "-f", "mp4"].map(OsString::from));
    args.push(part.output.as_os_str().to_owned());
    args
}

/// The sound pass: `audio_args`, with only key frames decoded from the videos.
fn sound_args(job: &ExportJob, source: &Source, script: &Path, output: &Path) -> Vec<OsString> {
    let args = audio_args(&job.inputs, script, output);
    let mut out = Vec::with_capacity(args.len() + 2 * source.media_count);
    let mut input = 0;
    for arg in args {
        if arg == "-i" {
            if input < source.media_count && !crate::is_still(&job.inputs[input]) {
                out.extend(["-skip_frame", "nokey"].map(OsString::from));
            }
            input += 1;
        }
        out.push(arg);
    }
    out
}

fn join_args(listing: &Path, sound: Option<&Path>, output: &Path) -> Vec<OsString> {
    let mut args: Vec<OsString> = ["-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i"].map(OsString::from).to_vec();
    args.push(listing.into());
    if let Some(sound) = sound {
        args.push("-i".into());
        args.push(sound.into());
    }
    args.extend(["-map", "0:v:0"].map(OsString::from));
    if sound.is_some() {
        args.extend(["-map", "1:a:0"].map(OsString::from));
    }
    args.extend(["-c", "copy", "-sn", "-dn", "-map_chapters", "-1", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4"]
        .map(OsString::from));
    args.push(output.into());
    args
}

/// Progress of every part and the sound, written to status.json at most every 400 ms.
struct Progress<'a> {
    job: &'a ExportJob,
    frames: Vec<AtomicU64>,
    total_frames: u64,
    sound_us: AtomicU64,
    workers: usize,
    label: Mutex<&'static str>,
    written: Mutex<Instant>,
}

impl Progress<'_> {
    fn percent(&self) -> f64 {
        let frames: u64 = self.frames.iter().map(|done| done.load(Ordering::Relaxed)).sum();
        let video = frames as f64 / self.total_frames.max(1) as f64;
        let sound = if self.job.has_audio { (self.sound_us.load(Ordering::Relaxed) as f64 / 1e6 / self.job.seconds.max(0.001)).min(1.0) } else { 1.0 };
        ((video * 0.8 + sound * 0.15) * 100.0).clamp(0.0, 99.0)
    }

    fn report(&self, running: &Running) {
        if self.job.work.join("cancel").exists() {
            stop(running);
        }
        let Ok(mut written) = self.written.try_lock() else { return };
        if written.elapsed() < Duration::from_millis(400) {
            return;
        }
        let percent = self.percent();
        let label = self.label.lock().map_or("", |label| *label);
        write_status(&self.job.work, "RUNNING", percent,
            &format!("Đang xuất video{label}, {} đoạn song song… {}%", self.workers, percent.floor()), Some(&self.job.target));
        *written = Instant::now();
    }
}

fn frame_value(line: &str) -> Option<u64> {
    line.strip_prefix("frame=").and_then(|value| value.trim().parse().ok())
}

#[allow(clippy::too_many_arguments)]
fn run_part(job: &ExportJob, part: &Part, index: usize, stills: &[bool], encoder: &str, threads: usize, running: &Running, progress: &Progress) -> Result<(), String> {
    let _ = fs::remove_file(&part.output);
    let log = part.script.with_extension("log");
    let mut frames = 0;
    progress.frames[index].store(0, Ordering::Relaxed);
    run_tracked(&job.ffmpeg, part_args(job, part, stills, encoder, threads), &job.work, &log, running, |line| {
        if let Some(value) = frame_value(line) {
            frames = value;
            progress.frames[index].store(value.min(part.frames), Ordering::Relaxed);
        } else if line.starts_with("progress=") {
            progress.report(running);
        }
    })?;
    if frames != part.frames || !fs::metadata(&part.output).is_ok_and(|metadata| metadata.len() > 0) {
        return Err(format!("đoạn {} có {frames}/{} khung hình", index + 1, part.frames));
    }
    Ok(())
}

/// Every part with `encoder`, `workers` at a time; a part that fails is tried once more.
fn run_parts(job: &ExportJob, parts: &[Part], stills: &[bool], encoder: &str, workers: usize, running: &Running, progress: &Progress) -> Result<(), String> {
    let next = AtomicUsize::new(0);
    let broken = AtomicBool::new(false);
    let failure = Mutex::new(String::new());
    let threads = crate::gpu::part_threads(workers);
    std::thread::scope(|scope| {
        for _ in 0..workers.min(parts.len()) {
            scope.spawn(|| loop {
                if running.cancelled.load(Ordering::SeqCst) || broken.load(Ordering::SeqCst) {
                    break;
                }
                let index = next.fetch_add(1, Ordering::SeqCst);
                let Some(part) = parts.get(index) else { break };
                let result = run_part(job, part, index, stills, encoder, threads, running, progress)
                    .or_else(|_| if running.cancelled.load(Ordering::SeqCst) { Err(String::new()) } else { run_part(job, part, index, stills, encoder, threads, running, progress) });
                if let Err(error) = result {
                    broken.store(true, Ordering::SeqCst);
                    if let Ok(mut failure) = failure.lock() {
                        *failure = error;
                    }
                }
            });
        }
    });
    if running.cancelled.load(Ordering::SeqCst) {
        return Err(String::new());
    }
    if broken.load(Ordering::SeqCst) { Err(failure.into_inner().unwrap_or_default()) } else { Ok(()) }
}

fn write_guarded(work: &Path, path: &Path, text: &str) -> Result<(), String> {
    guarded(work)?;
    fs::write(path, text).map_err(|_| fail("Không ghi được bộ lọc xuất video"))
}

/// Render `job` as parts side by side into its partial file. Err means the
/// single FFmpeg run should render it instead (or the user cancelled).
pub(super) fn render(job: &ExportJob, source: &Source, running: &Running) -> Result<(), String> {
    let encoders = crate::gpu::encoders(&job.ffmpeg);
    let workers = crate::gpu::parallel_renders(encoders.first() == Some(&crate::gpu::NVENC));
    let fps = fps_number(&job.fps);
    if workers < 2 || job.seconds < PARALLEL_MIN_SECONDS || fps <= 0.0 {
        return Err(String::new());
    }
    let count = workers.max((job.seconds / PART_SECONDS).ceil() as usize);
    let cuts = cut_points(&job.edit, job.seconds, &job.fps, count);
    if cuts.is_empty() {
        return Err(String::new());
    }
    let dir = job.work.join("parts");
    guarded(&job.work)?;
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).map_err(|_| fail("Không tạo được thư mục tạm"))?;
    let result = render_in(job, source, running, &dir, &encoders, workers, fps, &cuts);
    if unlinked(&job.work) {
        let _ = fs::remove_dir_all(&dir);
    }
    if result.is_err() && !running.cancelled.load(Ordering::SeqCst) {
        write_status(&job.work, "RUNNING", 0.0, "Đang xuất lại trong một lượt…", Some(&job.target));
    }
    result
}

#[allow(clippy::too_many_arguments)]
fn render_in(job: &ExportJob, source: &Source, running: &Running, dir: &Path, encoders: &[&'static str], workers: usize, fps: f64, cuts: &[f64]) -> Result<(), String> {
    let stills: Vec<bool> = job.inputs.iter().map(|input| crate::is_still(input)).collect();
    let mut edges = vec![0.0];
    edges.extend_from_slice(cuts);
    edges.push(job.seconds);
    let total_frames = (job.seconds * fps).round() as u64;
    let mut parts = Vec::with_capacity(edges.len() - 1);
    let mut listing = String::from("ffconcat version 1.0\n");
    for (index, pair) in edges.windows(2).enumerate() {
        let (from, to) = (pair[0], pair[1]);
        let frames = if index + 2 == edges.len() { total_frames } else { (to * fps).round() as u64 } - (from * fps).round() as u64;
        let edit = window(&job.edit, from, to, &stills);
        let script = dir.join(format!("part-{index:04}.txt"));
        write_guarded(&job.work, &script, &part_script(&edit, source, &job.quality, from, frames))?;
        listing.push_str(&format!("file part-{index:04}.mp4\n"));
        parts.push(Part { output: dir.join(format!("part-{index:04}.mp4")), seeks: seeks(&edit, source.media_count), script, frames });
    }
    let listing_path = dir.join("parts.txt");
    write_guarded(&job.work, &listing_path, &listing)?;
    let progress = Progress { job, frames: parts.iter().map(|_| AtomicU64::new(0)).collect(), total_frames, sound_us: AtomicU64::new(0), workers,
        label: Mutex::new(""), written: Mutex::new(Instant::now() - Duration::from_secs(1)) };
    let sound = dir.join("sound.m4a");
    let (video, audio) = std::thread::scope(|scope| {
        let audio = source.sound_script.as_ref().filter(|_| job.has_audio).map(|script| {
            let (progress, sound) = (&progress, &sound);
            scope.spawn(move || run_tracked(&job.ffmpeg, sound_args(job, source, script, sound), &job.work, &dir.join("sound.log"), running, |line| {
                if let Some(us) = line.strip_prefix("out_time_us=").and_then(|value| value.trim().parse::<u64>().ok()) {
                    progress.sound_us.store(us, Ordering::Relaxed);
                }
            }))
        });
        let mut video = Err(String::new());
        for encoder in encoders {
            if running.cancelled.load(Ordering::SeqCst) {
                break;
            }
            if let Ok(mut label) = progress.label.lock() {
                *label = crate::gpu::label(encoder);
            }
            video = run_parts(job, &parts, &stills, encoder, workers, running, &progress);
            if video.is_ok() {
                break;
            }
        }
        if video.is_err() {
            // Nothing to join: the single run renders the sound again, so this one stops now.
            if let Ok(mut children) = running.children.lock() {
                for child in children.iter_mut() {
                    let _ = child.kill();
                }
            }
        }
        (video, audio.map(|handle| handle.join().unwrap_or_else(|_| Err(String::new()))))
    });
    video?;
    if let Some(audio) = audio {
        audio?;
        if !fs::metadata(&sound).is_ok_and(|metadata| metadata.len() > 0) {
            return Err(String::new());
        }
    }
    if running.cancelled.load(Ordering::SeqCst) {
        return Err(String::new());
    }
    write_status(&job.work, "RUNNING", 99.0, "Đang ghép các đoạn video…", Some(&job.target));
    let partial = partial_of(&job.target);
    let _ = fs::remove_file(&partial);
    let with_sound = job.has_audio && source.sound_script.is_some();
    run_tracked(&job.ffmpeg, join_args(&listing_path, with_sound.then_some(sound.as_path()), &partial), &job.work, &dir.join("join.log"), running, |_| {})?;
    if fs::metadata(&partial).is_ok_and(|metadata| metadata.len() > 0) { Ok(()) } else { Err(String::new()) }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment(media: usize, start: f64, end: f64) -> Segment {
        Segment { media, start, end, speed: 1.0, volume: 1.0, ..Segment::default() }
    }

    fn edit(segments: Vec<Segment>) -> Edit {
        Edit { segments, ..Edit::default() }
    }

    #[test]
    fn a_plain_long_cut_splits_evenly_on_the_frame_grid() {
        let long = edit(vec![segment(0, 100.0, 3700.0)]);
        let cuts = cut_points(&long, 3600.0, "30/1", 4);
        assert_eq!(cuts, vec![900.0, 1800.0, 2700.0]);
        let part = window(&long, 900.0, 1800.0, &[false]);
        assert_eq!((part.segments[0].start, part.segments[0].end), (1000.0, 1900.0));
        assert_eq!(seeks(&part, 1), vec![Some(970.0)]);
    }

    #[test]
    fn cuts_avoid_transitions_moving_overlays_and_whole_segments() {
        let mut timeline = edit(vec![segment(0, 0.0, 600.0), segment(0, 600.0, 1200.0)]);
        timeline.transitions.push(Transition { after: 0, kind: TransitionKind::Dissolve, duration: 1.0 });
        let total = output_seconds(&timeline.segments, &timeline.transitions);
        // The even split (599.5) falls on the dissolve: the cut moves out of it.
        let cuts = cut_points(&timeline, total, "30/1", 2);
        assert_eq!(cuts.len(), 1);
        assert!(cuts[0] <= 598.0 || cuts[0] >= 601.0, "{cuts:?}");
        timeline.transitions.clear();
        timeline.segments[1].zoom = Some(Zoom { from: 1.0, to: 1.2, x: 0.5, y: 0.5 });
        // A zoom changes over its segment: only its joint may be cut there.
        assert_eq!(cut_points(&timeline, 1200.0, "30/1", 3), vec![400.0, 600.0]);
        timeline.segments[1].zoom = None;
        timeline.overlays.push(Overlay { media: 1, at: 300.0, start: 0.0, end: 600.0, w: 0.3, to: Some(Placement { x: 0.5, y: 0.5, w: 0.3 }), ..Overlay::default() });
        // The overlay moves from 300 to 900 s: the nearest moment outside it.
        assert_eq!(cut_points(&timeline, 1200.0, "30/1", 2), vec![300.0]);
    }

    #[test]
    fn cuts_fall_where_frame_times_are_whole_milliseconds() {
        assert!((exact_step("25/1") - 0.04).abs() < 1e-9);
        assert!((exact_step("30/1") - 0.1).abs() < 1e-9);
        assert!((exact_step("30000/1001") - 1.001).abs() < 1e-9);
        assert!((exact_step("60/1") - 0.05).abs() < 1e-9);
    }

    #[test]
    fn a_window_keeps_still_overlays_and_shifts_video_ones() {
        let mut timeline = edit(vec![segment(0, 0.0, 1200.0)]);
        timeline.overlays.push(Overlay { media: 1, at: 100.0, start: 0.0, end: 1000.0, w: 0.2, ..Overlay::default() });
        timeline.overlays.push(Overlay { media: 2, at: 100.0, start: 5.0, end: 1005.0, w: 0.2, ..Overlay::default() });
        let part = window(&timeline, 600.0, 1200.0, &[false, true, false]);
        let still = &part.overlays[0];
        assert_eq!((still.at, still.start, still.end), (0.0, 0.0, 500.0));
        let video = &part.overlays[1];
        assert_eq!((video.at, video.start, video.end), (0.0, 505.0, 1005.0));
        assert_eq!(seeks(&part, 3), vec![Some(570.0), Some(0.0), Some(475.0)]);
    }

    #[test]
    fn a_part_graph_is_video_only_and_exactly_its_frames() {
        let frame = Frame { width: 1920, height: 1080, fps: "30/1".into() };
        let source = Source { media_count: 1, frame, texts: Some("subtitles=filename=t.ass".into()), reframe: None, prefilter: Some("boxblur=10".into()), sound_script: None };
        let part = window(&edit(vec![segment(0, 0.0, 1200.0)]), 600.0, 1200.0, &[false]);
        let graph = part_script(&part, &source, "source", 600.0, 18000);
        assert!(!graph.contains(":a]") && !graph.contains("atrim"), "{graph}");
        assert!(graph.starts_with("[0:v]boxblur=10[mcv]"), "{graph}");
        assert!(graph.contains("[vp]setpts=PTS+600/TB,subtitles=filename=t.ass,setpts=PTS-600/TB[vs0]"), "{graph}");
        assert!(graph.ends_with("[vs0]fps=30/1,tpad=stop_mode=clone:stop_duration=5,trim=end_frame=18000[v]"), "{graph}");
    }

    #[test]
    fn the_sound_pass_picture_is_small() {
        assert_eq!(sound_frame(&Frame { width: 1920, height: 1080, fps: "30/1".into() }), Frame { width: 568, height: 320, fps: "5/1".into() });
        assert_eq!(sound_frame(&Frame { width: 240, height: 426, fps: "30/1".into() }).width, 240);
    }

    #[test]
    fn the_join_copies_the_parts_and_the_sound() {
        let args: Vec<String> = join_args(Path::new("parts.txt"), Some(Path::new("sound.m4a")), Path::new("out.mp4"))
            .iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args.join(" "), "-nostdin -hide_banner -v error -y -f concat -safe 0 -i parts.txt -i sound.m4a -map 0:v:0 -map 1:a:0 -c copy -sn -dn \
-map_chapters -1 -movflags +faststart -progress pipe:1 -nostats -f mp4 out.mp4");
    }

    /// With DUBFLOW_TEST_FFMPEG (and an encoder named by DUBFLOW_TEST_ENCODER,
    /// libx264 by default): the parts joined are the single run, frame for frame.
    #[test]
    fn real_parts_match_the_single_run() {
        let Ok(ffmpeg) = std::env::var("DUBFLOW_TEST_FFMPEG") else { return };
        let encoder = std::env::var("DUBFLOW_TEST_ENCODER").unwrap_or_else(|_| "libx264".into());
        let ffmpeg = PathBuf::from(ffmpeg);
        let dir = std::env::temp_dir().join(format!("dubflow-parts-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("work/parts")).unwrap();
        let source_file = dir.join("source.mp4");
        let made = Command::new(&ffmpeg).args(["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=330", "-f", "lavfi", "-i",
            "sine=f=330:d=330", "-c:v", "mpeg4", "-q:v", "3", "-g", "90", "-c:a", "aac", "-shortest"]).arg(&source_file).status().unwrap();
        assert!(made.success());
        let mut timeline = edit(vec![segment(0, 10.0, 190.0), Segment { speed: 2.0, ..segment(0, 200.0, 260.0) }, segment(0, 260.0, 320.0)]);
        timeline.transitions.push(Transition { after: 0, kind: TransitionKind::Dissolve, duration: 1.0 });
        timeline.overlays.push(Overlay { media: 0, at: 20.0, start: 0.0, end: 200.0, x: 0.6, y: 0.6, w: 0.3, ..Overlay::default() });
        timeline.overlays.push(Overlay { media: 0, at: 150.0, start: 0.0, end: 10.0, w: 0.2, to: Some(Placement { x: 0.5, y: 0.5, w: 0.3 }), ..Overlay::default() });
        let total = output_seconds(&timeline.segments, &timeline.transitions);
        let frame = Frame { width: 320, height: 240, fps: "30/1".into() };
        let streams = vec![Input { has_audio: true }];
        let mix = Mix { main_volume: 1.0, ..Mix::default() };
        let work = dir.join("work");
        let (graph, has_audio) = export_script(&timeline, &streams, &frame, "source", &mix, &Finish::default());
        let script = work.join("filter.txt");
        fs::write(&script, graph).unwrap();
        let (sound, _) = export_script(&timeline, &streams, &sound_frame(&frame), "source", &mix, &Finish { audio_only: true, ..Finish::default() });
        let sound_script = work.join("filter-sound.txt");
        fs::write(&sound_script, sound).unwrap();
        let job = ExportJob { work: work.clone(), inputs: vec![source_file], media: vec![], script, folder: dir.clone(), name: "out".into(),
            target: dir.join("parts.mp4"), srt: None, seconds: total, has_audio, bitrate: 1_500_000, fps: "30/1".into(), ffmpeg: ffmpeg.clone(),
            quality: "source".into(), edit: timeline.clone(), audio: vec![], main_volume: 1.0, main_denoise: false, main_eq: None, stems: None,
            texts: vec![], audio_only: false, parts: None };
        let source = Source { media_count: 1, frame, texts: None, reframe: None, prefilter: None, sound_script: Some(sound_script) };
        let running = Running { children: Mutex::new(Vec::new()), cancelled: AtomicBool::new(false) };
        let single = dir.join("single.mp4");
        run_tracked(&ffmpeg, ffmpeg_args(&job.inputs, &job.script, true, &encoder, job.bitrate, &job.fps, &single), &work, &dir.join("single.log"), &running, |_| {})
            .unwrap();
        let cuts = cut_points(&timeline, total, "30/1", 3);
        assert_eq!(cuts.len(), 2, "{cuts:?}");
        render_in(&job, &source, &running, &work.join("parts"), &[Box::leak(encoder.clone().into_boxed_str())], 3, 30.0, &cuts).unwrap();
        let parts = partial_of(&job.target);
        let probe = |file: &Path, stream: &str| {
            let output = Command::new(ffmpeg.with_file_name(ffmpeg.file_name().unwrap().to_string_lossy().replace("ffmpeg", "ffprobe")))
                .args(["-v", "error", "-select_streams", stream, "-count_frames", "-show_entries", "stream=nb_read_frames,duration", "-of", "csv=p=0"])
                .arg(file).output().unwrap();
            String::from_utf8_lossy(&output.stdout).trim().to_owned()
        };
        assert_eq!(probe(&parts, "v:0"), probe(&single, "v:0"));
        let audio = |file: &Path| probe(file, "a:0").split(',').next().unwrap_or("").parse::<f64>().unwrap_or(0.0);
        assert!((audio(&parts) - audio(&single)).abs() < 0.05, "{} vs {}", audio(&parts), audio(&single));
        let compared = Command::new(&ffmpeg).args(["-v", "info", "-i"]).arg(&parts).arg("-i").arg(&single)
            .args(["-lavfi", "[0:v][1:v]psnr=stats_file=-", "-f", "null", "-"]).output().unwrap();
        let stats = String::from_utf8_lossy(&compared.stdout);
        let scores: Vec<f64> = stats.lines().filter_map(|line| line.split_whitespace().find_map(|field| field.strip_prefix("psnr_avg:")))
            .map(|value| if value == "inf" { 99.0 } else { value.parse::<f64>().unwrap_or(0.0) }).collect();
        // (The source is MPEG-4 Part 2: every FFmpeg has it, the shipped LGPL one has no libx264.)
        // A frame off by one is about 18 dB here: only a joint of a part may
        // repeat or skip one frame (concat rounds each segment to whole frames).
        let off: Vec<usize> = scores.iter().enumerate().filter(|(_, &score)| score < 30.0).map(|(index, _)| index).collect();
        assert!(scores.len() > 8000 && off.len() <= 4, "frames under 30 dB: {off:?}");
        let _ = fs::remove_dir_all(&dir);
    }
}
