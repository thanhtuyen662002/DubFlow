//! The first executable DubFlow vertical slice.
//!
//! This crate deliberately stays small.  It is an integration harness, not a
//! second media or worker contract.  Media metadata is adapted into the
//! canonical types from `dubflow-media-contracts`; worker messages are sent
//! through `dubflow-worker-protocol`; and every durable mutation goes through
//! `dubflow-job-state`.

use dubflow_job_state::{ArtifactState, DurableStore, JobStatus, StageStatus};
use dubflow_media_contracts::{AspectRatio, Dimensions, Duration, TimeBase, TimePoint, TimelineError};
use dubflow_worker_protocol::{Envelope, MessageType, Payload, ProtocolError, ShutdownStatus, StreamValidator};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const FIXTURE_MAGIC: &[u8] = b"DUBFLOW_SLICE_MEDIA_V1\n";
const MAX_MEDIA_BYTES: usize = 256 * 1024 * 1024;
const MAX_SAMPLES: usize = 1_000_000;

/// Errors raised by the integration adapter or pipeline.
#[derive(Debug)]
pub enum SliceError {
    Io(io::Error),
    Probe(ProbeError),
    Timeline(TimelineError),
    State(dubflow_job_state::StateError),
    Protocol(ProtocolError),
    Invalid(String),
}

impl fmt::Display for SliceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Probe(error) => write!(f, "probe error: {error}"),
            Self::Timeline(error) => write!(f, "timeline error: {error}"),
            Self::State(error) => write!(f, "state error: {error}"),
            Self::Protocol(error) => write!(f, "worker protocol error: {error}"),
            Self::Invalid(error) => write!(f, "invalid local-file slice: {error}"),
        }
    }
}

impl std::error::Error for SliceError {}
impl From<io::Error> for SliceError { fn from(error: io::Error) -> Self { Self::Io(error) } }
impl From<ProbeError> for SliceError { fn from(error: ProbeError) -> Self { Self::Probe(error) } }
impl From<TimelineError> for SliceError { fn from(error: TimelineError) -> Self { Self::Timeline(error) } }
impl From<dubflow_job_state::StateError> for SliceError { fn from(error: dubflow_job_state::StateError) -> Self { Self::State(error) } }
impl From<ProtocolError> for SliceError { fn from(error: ProtocolError) -> Self { Self::Protocol(error) } }

pub type Result<T> = std::result::Result<T, SliceError>;

/// Errors from the deterministic media probe.
#[derive(Debug)]
pub enum ProbeError {
    Io(io::Error),
    Invalid(String),
    Unsupported(String),
    Overflow,
}

impl fmt::Display for ProbeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Invalid(reason) => write!(f, "invalid media: {reason}"),
            Self::Unsupported(reason) => write!(f, "unsupported media: {reason}"),
            Self::Overflow => write!(f, "media metadata arithmetic overflowed"),
        }
    }
}

impl std::error::Error for ProbeError {}
impl From<io::Error> for ProbeError { fn from(error: io::Error) -> Self { Self::Io(error) } }

/// Metadata returned by the source adapter.  `presentation_timestamps` are
/// integer ticks in `time_base`; they are never converted to floating seconds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MediaMetadata {
    pub dimensions: Dimensions,
    pub pixel_aspect_ratio: AspectRatio,
    pub time_base: TimeBase,
    pub duration: Duration,
    pub presentation_timestamps: Vec<TimePoint>,
}

impl MediaMetadata {
    pub fn new(
        width: u32,
        height: u32,
        time_base: TimeBase,
        duration_ticks: u64,
        presentation_ticks: Vec<i64>,
    ) -> std::result::Result<Self, TimelineError> {
        let dimensions = Dimensions::new(width, height)?;
        let pixel_aspect_ratio = AspectRatio::new(1, 1)?;
        let duration = Duration::new(duration_ticks, time_base);
        let presentation_timestamps = presentation_ticks
            .into_iter()
            .map(|ticks| TimePoint::new(ticks, time_base))
            .collect();
        Ok(Self {
            dimensions,
            pixel_aspect_ratio,
            time_base,
            duration,
            presentation_timestamps,
        })
    }

    /// Return a copy of this metadata in one explicit canonical time base.
    pub fn canonicalize(&self, target: TimeBase) -> Result<CanonicalTimeline> {
        let duration = self.duration.rescale_exact(target)?;
        let mut timestamps = Vec::with_capacity(self.presentation_timestamps.len());
        for timestamp in &self.presentation_timestamps {
            timestamps.push(timestamp.rescale_exact(target)?);
        }
        Ok(CanonicalTimeline {
            time_base: target,
            duration,
            presentation_timestamps: timestamps,
            dimensions: self.dimensions,
        })
    }
}

/// Canonical integer timeline used by the harness.  A caller chooses the
/// target time base explicitly; no frame index or floating seconds enters the
/// durable identity.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanonicalTimeline {
    pub time_base: TimeBase,
    pub duration: Duration,
    pub presentation_timestamps: Vec<TimePoint>,
    pub dimensions: Dimensions,
}

/// A source adapter interface.  The production source can be backed by
/// ffprobe, while this PR-fast implementation is deterministic and has no
/// runtime dependency on a system executable.
pub trait MediaProbe {
    fn probe(&self, path: &Path) -> std::result::Result<MediaMetadata, ProbeError>;
}

/// Deterministic ISO-BMFF metadata probe with a small text fixture fallback.
/// The fallback lets CI exercise VFR and non-zero-PTS recovery without a large
/// binary media file.  It is intentionally a source adapter, never a new
/// contract.
#[derive(Clone, Copy, Debug, Default)]
pub struct DeterministicMediaProbe;

impl MediaProbe for DeterministicMediaProbe {
    fn probe(&self, path: &Path) -> std::result::Result<MediaMetadata, ProbeError> {
        let metadata = fs::metadata(path)?;
        let size = usize::try_from(metadata.len()).map_err(|_| ProbeError::Overflow)?;
        if size == 0 || size > MAX_MEDIA_BYTES {
            return Err(ProbeError::Invalid("file size is empty or exceeds the probe limit".into()));
        }
        let mut bytes = Vec::with_capacity(size);
        File::open(path)?.read_to_end(&mut bytes)?;
        if bytes.starts_with(FIXTURE_MAGIC) {
            return parse_fixture(&bytes[FIXTURE_MAGIC.len()..]);
        }
        parse_mp4(&bytes)
    }
}

#[derive(Default, Debug)]
struct MovieFields {
    movie_time_scale: Option<u32>,
    movie_duration: Option<u64>,
    track: Option<TrackFields>,
}

#[derive(Default, Debug)]
struct TrackFields {
    handler: Option<[u8; 4]>,
    width: Option<u32>,
    height: Option<u32>,
    time_scale: Option<u32>,
    duration: Option<u64>,
    sample_durations: Vec<u64>,
    composition_offsets: Vec<i64>,
    edit_media_time: Option<i64>,
}

#[derive(Clone, Copy, Debug)]
struct BoxHeader {
    start: usize,
    content: usize,
    end: usize,
    kind: [u8; 4],
}

fn parse_mp4(bytes: &[u8]) -> std::result::Result<MediaMetadata, ProbeError> {
    let mut movie = MovieFields::default();
    walk_movie(bytes, 0, bytes.len(), &mut movie)?;
    let track = movie.track.ok_or_else(|| ProbeError::Invalid("no video track found".into()))?;
    if track.handler.as_ref().map(|value| value != b"vide").unwrap_or(false) {
        return Err(ProbeError::Invalid("media contains no video handler".into()));
    }
    let width = track.width.ok_or_else(|| ProbeError::Invalid("video width is missing".into()))?;
    let height = track.height.ok_or_else(|| ProbeError::Invalid("video height is missing".into()))?;
    let time_scale = track
        .time_scale
        .or(movie.movie_time_scale)
        .ok_or_else(|| ProbeError::Invalid("video time scale is missing".into()))?;
    if time_scale == 0 {
        return Err(ProbeError::Invalid("video time scale must be positive".into()));
    }
    let time_base = TimeBase::new(1, u64::from(time_scale)).map_err(|_| ProbeError::Invalid("invalid video time base".into()))?;
    let mut decode_pts = Vec::new();
    let mut tick = track.edit_media_time.unwrap_or(0);
    if !track.sample_durations.is_empty() {
        decode_pts.reserve(track.sample_durations.len());
        for duration in &track.sample_durations {
            decode_pts.push(tick);
            let delta = i64::try_from(*duration).map_err(|_| ProbeError::Overflow)?;
            tick = tick.checked_add(delta).ok_or(ProbeError::Overflow)?;
        }
    } else {
        let duration = track.duration.or(movie.movie_duration).unwrap_or(0);
        decode_pts.push(tick);
        tick = tick.checked_add(i64::try_from(duration).map_err(|_| ProbeError::Overflow)?).ok_or(ProbeError::Overflow)?;
    }
    let mut presentation_ticks = Vec::with_capacity(decode_pts.len());
    for (index, decode) in decode_pts.into_iter().enumerate() {
        let offset = track.composition_offsets.get(index).copied().unwrap_or(0);
        presentation_ticks.push(decode.checked_add(offset).ok_or(ProbeError::Overflow)?);
    }
    let duration_ticks = track.duration.or_else(|| {
        if tick >= 0 { Some(u64::try_from(tick).ok()?) } else { None }
    }).or(movie.movie_duration).unwrap_or(0);
    MediaMetadata::new(width, height, time_base, duration_ticks, presentation_ticks)
        .map_err(|error| ProbeError::Invalid(error.to_string()))
}

fn walk_movie(bytes: &[u8], start: usize, end: usize, movie: &mut MovieFields) -> std::result::Result<(), ProbeError> {
    let mut cursor = start;
    while cursor < end {
        let header = next_box(bytes, cursor, end)?;
        match &header.kind {
            b"moov" => walk_movie(bytes, header.content, header.end, movie)?,
            b"mvhd" => parse_mvhd(bytes, header, movie)?,
            b"trak" => {
                let candidate = parse_trak(bytes, header.content, header.end)?;
                let use_candidate = candidate.handler.as_ref().map(|value| value == b"vide").unwrap_or(false)
                    || (candidate.handler.is_none() && candidate.width.is_some());
                if use_candidate && movie.track.is_none() {
                    movie.track = Some(candidate);
                }
            }
            _ => {}
        }
        cursor = header.end;
    }
    if cursor != end {
        return Err(ProbeError::Invalid("box extends outside its parent".into()));
    }
    Ok(())
}

fn parse_trak(bytes: &[u8], start: usize, end: usize) -> std::result::Result<TrackFields, ProbeError> {
    let mut track = TrackFields::default();
    let mut cursor = start;
    while cursor < end {
        let header = next_box(bytes, cursor, end)?;
        match &header.kind {
            b"tkhd" => parse_tkhd(bytes, header, &mut track)?,
            b"mdia" | b"minf" | b"stbl" => parse_track_container(bytes, header.content, header.end, &mut track)?,
            b"edts" => parse_edit_container(bytes, header.content, header.end, &mut track)?,
            _ => {}
        }
        cursor = header.end;
    }
    Ok(track)
}

fn parse_track_container(bytes: &[u8], start: usize, end: usize, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let mut cursor = start;
    while cursor < end {
        let header = next_box(bytes, cursor, end)?;
        match &header.kind {
            b"mdia" | b"minf" | b"stbl" => parse_track_container(bytes, header.content, header.end, track)?,
            b"mdhd" => parse_mdhd(bytes, header, track)?,
            b"hdlr" => parse_hdlr(bytes, header, track)?,
            b"stts" => parse_stts(bytes, header, track)?,
            b"ctts" => parse_ctts(bytes, header, track)?,
            _ => {}
        }
        cursor = header.end;
    }
    Ok(())
}

fn parse_edit_container(bytes: &[u8], start: usize, end: usize, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let mut cursor = start;
    while cursor < end {
        let header = next_box(bytes, cursor, end)?;
        if &header.kind == b"elst" {
            parse_elst(bytes, header, track)?;
        }
        cursor = header.end;
    }
    Ok(())
}

fn parse_mvhd(bytes: &[u8], header: BoxHeader, movie: &mut MovieFields) -> std::result::Result<(), ProbeError> {
    let version = byte(bytes, header.content)?;
    let (scale_offset, duration_offset) = if version == 0 { (12, 16) } else if version == 1 { (20, 24) } else { return Err(ProbeError::Unsupported("mvhd version".into())); };
    movie.movie_time_scale = Some(read_u32_at(bytes, header.content.checked_add(scale_offset).ok_or(ProbeError::Overflow)?, header.end)?);
    movie.movie_duration = Some(if version == 0 {
        u64::from(read_u32_at(bytes, header.content.checked_add(duration_offset).ok_or(ProbeError::Overflow)?, header.end)?)
    } else {
        read_u64_at(bytes, header.content.checked_add(duration_offset).ok_or(ProbeError::Overflow)?, header.end)?
    });
    Ok(())
}

fn parse_tkhd(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let version = byte(bytes, header.content)?;
    let offset = if version == 0 { 76 } else if version == 1 { 88 } else { return Err(ProbeError::Unsupported("tkhd version".into())); };
    let width = read_u32_at(bytes, header.content.checked_add(offset).ok_or(ProbeError::Overflow)?, header.end)? >> 16;
    let height = read_u32_at(bytes, header.content.checked_add(offset + 4).ok_or(ProbeError::Overflow)?, header.end)? >> 16;
    if width != 0 && height != 0 {
        track.width = Some(width);
        track.height = Some(height);
    }
    Ok(())
}

fn parse_mdhd(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let version = byte(bytes, header.content)?;
    let (scale_offset, duration_offset) = if version == 0 { (12, 16) } else if version == 1 { (20, 24) } else { return Err(ProbeError::Unsupported("mdhd version".into())); };
    track.time_scale = Some(read_u32_at(bytes, header.content.checked_add(scale_offset).ok_or(ProbeError::Overflow)?, header.end)?);
    track.duration = Some(if version == 0 {
        u64::from(read_u32_at(bytes, header.content.checked_add(duration_offset).ok_or(ProbeError::Overflow)?, header.end)?)
    } else {
        read_u64_at(bytes, header.content.checked_add(duration_offset).ok_or(ProbeError::Overflow)?, header.end)?
    });
    Ok(())
}

fn parse_hdlr(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let offset = header.content.checked_add(8).ok_or(ProbeError::Overflow)?;
    if offset.checked_add(4).ok_or(ProbeError::Overflow)? > header.end {
        return Err(ProbeError::Invalid("hdlr box is truncated".into()));
    }
    let mut kind = [0u8; 4];
    kind.copy_from_slice(&bytes[offset..offset + 4]);
    track.handler = Some(kind);
    Ok(())
}

fn parse_stts(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let count = usize::try_from(read_u32_at(bytes, header.content + 4, header.end)?).map_err(|_| ProbeError::Overflow)?;
    let mut offset = header.content.checked_add(8).ok_or(ProbeError::Overflow)?;
    for _ in 0..count {
        let sample_count = usize::try_from(read_u32_at(bytes, offset, header.end)?).map_err(|_| ProbeError::Overflow)?;
        let sample_delta = u64::from(read_u32_at(bytes, offset + 4, header.end)?);
        offset = offset.checked_add(8).ok_or(ProbeError::Overflow)?;
        if sample_count > MAX_SAMPLES.saturating_sub(track.sample_durations.len()) {
            return Err(ProbeError::Invalid("sample table exceeds probe limit".into()));
        }
        track.sample_durations.extend(std::iter::repeat(sample_delta).take(sample_count));
    }
    Ok(())
}

fn parse_ctts(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let version = byte(bytes, header.content)?;
    let count = usize::try_from(read_u32_at(bytes, header.content + 4, header.end)?).map_err(|_| ProbeError::Overflow)?;
    let mut offset = header.content.checked_add(8).ok_or(ProbeError::Overflow)?;
    for _ in 0..count {
        let sample_count = usize::try_from(read_u32_at(bytes, offset, header.end)?).map_err(|_| ProbeError::Overflow)?;
        let raw = read_u32_at(bytes, offset + 4, header.end)?;
        let sample_offset = if version == 1 { i64::from(i32::from_be_bytes(raw.to_be_bytes())) } else { i64::from(raw) };
        offset = offset.checked_add(8).ok_or(ProbeError::Overflow)?;
        if sample_count > MAX_SAMPLES.saturating_sub(track.composition_offsets.len()) {
            return Err(ProbeError::Invalid("composition table exceeds probe limit".into()));
        }
        track.composition_offsets.extend(std::iter::repeat(sample_offset).take(sample_count));
    }
    Ok(())
}

fn parse_elst(bytes: &[u8], header: BoxHeader, track: &mut TrackFields) -> std::result::Result<(), ProbeError> {
    let version = byte(bytes, header.content)?;
    let count = usize::try_from(read_u32_at(bytes, header.content + 4, header.end)?).map_err(|_| ProbeError::Overflow)?;
    if count == 0 { return Ok(()); }
    let entry_size: usize = if version == 1 { 20 } else if version == 0 { 12 } else { return Err(ProbeError::Unsupported("elst version".into())); };
    let offset = header.content.checked_add(8).ok_or(ProbeError::Overflow)?;
    let media_offset = offset.checked_add(if version == 1 { 8 } else { 4 }).ok_or(ProbeError::Overflow)?;
    let media_time = if version == 1 {
        i64::try_from(read_u64_at(bytes, media_offset, header.end)?).map_err(|_| ProbeError::Overflow)?
    } else {
        i64::from(i32::from_be_bytes(read_u32_at(bytes, media_offset, header.end)?.to_be_bytes()))
    };
    track.edit_media_time = Some(media_time);
    let _ = entry_size.checked_mul(count).ok_or(ProbeError::Overflow)?;
    Ok(())
}

fn next_box(bytes: &[u8], start: usize, parent_end: usize) -> std::result::Result<BoxHeader, ProbeError> {
    if start.checked_add(8).ok_or(ProbeError::Overflow)? > parent_end || parent_end > bytes.len() {
        return Err(ProbeError::Invalid("truncated MP4 box header".into()));
    }
    let size32 = read_u32_at(bytes, start, parent_end)?;
    let mut content = start.checked_add(8).ok_or(ProbeError::Overflow)?;
    let size = if size32 == 1 {
        let extended = read_u64_at(bytes, content, parent_end)?;
        content = content.checked_add(8).ok_or(ProbeError::Overflow)?;
        usize::try_from(extended).map_err(|_| ProbeError::Overflow)?
    } else if size32 == 0 {
        parent_end.checked_sub(start).ok_or(ProbeError::Overflow)?
    } else {
        usize::try_from(size32).map_err(|_| ProbeError::Overflow)?
    };
    if size < content.saturating_sub(start) {
        return Err(ProbeError::Invalid("MP4 box size is smaller than its header".into()));
    }
    let end = start.checked_add(size).ok_or(ProbeError::Overflow)?;
    if end > parent_end || end > bytes.len() {
        return Err(ProbeError::Invalid("MP4 box extends outside its parent".into()));
    }
    let mut kind = [0u8; 4];
    kind.copy_from_slice(&bytes[start + 4..start + 8]);
    Ok(BoxHeader { start, content, end, kind })
}

fn byte(bytes: &[u8], offset: usize) -> std::result::Result<u8, ProbeError> {
    bytes.get(offset).copied().ok_or_else(|| ProbeError::Invalid("box is truncated".into()))
}

fn read_u32_at(bytes: &[u8], offset: usize, end: usize) -> std::result::Result<u32, ProbeError> {
    if offset.checked_add(4).ok_or(ProbeError::Overflow)? > end || offset + 4 > bytes.len() {
        return Err(ProbeError::Invalid("box field is truncated".into()));
    }
    Ok(u32::from_be_bytes(bytes[offset..offset + 4].try_into().map_err(|_| ProbeError::Invalid("invalid u32".into()))?))
}

fn read_u64_at(bytes: &[u8], offset: usize, end: usize) -> std::result::Result<u64, ProbeError> {
    if offset.checked_add(8).ok_or(ProbeError::Overflow)? > end || offset + 8 > bytes.len() {
        return Err(ProbeError::Invalid("box field is truncated".into()));
    }
    Ok(u64::from_be_bytes(bytes[offset..offset + 8].try_into().map_err(|_| ProbeError::Invalid("invalid u64".into()))?))
}

fn parse_fixture(bytes: &[u8]) -> std::result::Result<MediaMetadata, ProbeError> {
    let text = std::str::from_utf8(bytes).map_err(|_| ProbeError::Invalid("fixture metadata is not UTF-8".into()))?;
    let mut width = None;
    let mut height = None;
    let mut numerator = None;
    let mut denominator = None;
    let mut duration = None;
    let mut pts = None;
    for line in text.lines() {
        let Some((key, value)) = line.split_once('=') else { continue; };
        let value = value.trim();
        match key.trim() {
            "width" => width = Some(parse_u32(value, "width")?),
            "height" => height = Some(parse_u32(value, "height")?),
            "time_base_numerator" => numerator = Some(parse_u64(value, "time_base_numerator")?),
            "time_base_denominator" => denominator = Some(parse_u64(value, "time_base_denominator")?),
            "duration_ticks" => duration = Some(parse_u64(value, "duration_ticks")?),
            "pts_ticks" => {
                let mut parsed = Vec::new();
                for item in value.split(',').filter(|item| !item.trim().is_empty()) {
                    parsed.push(item.trim().parse::<i64>().map_err(|_| ProbeError::Invalid("pts_ticks contains a non-integer".into()))?);
                    if parsed.len() > MAX_SAMPLES { return Err(ProbeError::Invalid("fixture has too many timestamps".into())); }
                }
                pts = Some(parsed);
            }
            _ => return Err(ProbeError::Invalid(format!("unknown fixture metadata key {key:?}"))),
        }
    }
    let time_base = TimeBase::new(numerator.ok_or_else(|| ProbeError::Invalid("time_base_numerator is missing".into()))?, denominator.ok_or_else(|| ProbeError::Invalid("time_base_denominator is missing".into()))?).map_err(|_| ProbeError::Invalid("fixture time base is invalid".into()))?;
    MediaMetadata::new(
        width.ok_or_else(|| ProbeError::Invalid("width is missing".into()))?,
        height.ok_or_else(|| ProbeError::Invalid("height is missing".into()))?,
        time_base,
        duration.ok_or_else(|| ProbeError::Invalid("duration_ticks is missing".into()))?,
        pts.ok_or_else(|| ProbeError::Invalid("pts_ticks is missing".into()))?,
    ).map_err(|error| ProbeError::Invalid(error.to_string()))
}

fn parse_u32(value: &str, name: &str) -> std::result::Result<u32, ProbeError> {
    value.parse::<u32>().map_err(|_| ProbeError::Invalid(format!("{name} is not a u32")))
}
fn parse_u64(value: &str, name: &str) -> std::result::Result<u64, ProbeError> {
    value.parse::<u64>().map_err(|_| ProbeError::Invalid(format!("{name} is not a u64")))
}

/// The three durable stages in the first slice.  Validation is kept separate
/// from rendering so a failed input cannot poison a batch and a render retry
/// does not redo source probing or analysis.
pub const PROBE_STAGE: &str = "probe";
pub const ANALYSIS_STAGE: &str = "analysis";
pub const RENDER_STAGE: &str = "render";
pub const VALIDATE_STAGE: &str = "validate";

/// A supervisor-side job facade.  Workers receive no SQLite handle and only
/// return protocol envelopes to this owner.
pub struct LocalFileJob {
    store: DurableStore,
    job_id: String,
    source: PathBuf,
    output: PathBuf,
    artifact_root: PathBuf,
    probe: DeterministicMediaProbe,
}

impl LocalFileJob {
    pub fn create(db_path: impl AsRef<Path>, job_id: impl Into<String>, source: impl AsRef<Path>, output: impl AsRef<Path>) -> Result<Self> {
        let job_id = job_id.into();
        let source = source.as_ref().to_path_buf();
        let output = output.as_ref().to_path_buf();
        let artifact_root = output.parent().unwrap_or_else(|| Path::new(".")).to_path_buf();
        let store = DurableStore::open(db_path)?;
        store.create_job(&job_id, &format!("file://{}", source.to_string_lossy()), now_ms())?;
        store.create_stage(&job_id, PROBE_STAGE, "media-probe", 3)?;
        store.create_stage(&job_id, ANALYSIS_STAGE, "fake-analysis", 3)?;
        store.create_stage(&job_id, RENDER_STAGE, "passthrough-render", 3)?;
        store.create_stage(&job_id, VALIDATE_STAGE, "output-validation", 3)?;
        Ok(Self { store, job_id, source, output, artifact_root, probe: DeterministicMediaProbe })
    }

    pub fn open_existing(db_path: impl AsRef<Path>, job_id: impl Into<String>, source: impl AsRef<Path>, output: impl AsRef<Path>) -> Result<Self> {
        let output = output.as_ref().to_path_buf();
        let artifact_root = output.parent().unwrap_or_else(|| Path::new(".")).to_path_buf();
        Ok(Self {
            store: DurableStore::open(db_path)?,
            job_id: job_id.into(),
            source: source.as_ref().to_path_buf(),
            output,
            artifact_root,
            probe: DeterministicMediaProbe,
        })
    }

    pub fn status(&self) -> Result<JobStatus> { Ok(self.store.job_status(&self.job_id)?) }
    pub fn stage_status(&self, stage: &str) -> Result<StageStatus> { Ok(self.store.stage_status(&self.job_id, stage)?) }
    pub fn source(&self) -> &Path { &self.source }
    pub fn output(&self) -> &Path { &self.output }

    /// Resume every incomplete stage after a process restart.  The state
    /// owner first marks interrupted rows recovering and then reconciles DB and
    /// filesystem state before any stage is run again.
    pub fn resume_to_completion(&mut self, now: u64) -> Result<MediaMetadata> {
        self.store.recover_after_restart(now)?;
        // A kill can land after a partial is registered in SQLite but before
        // the atomic rename. Preserve the existing writing row and rerun only
        // render; never promote a partial file as if it were final output.
        let artifact_id = format!("{}-render", self.job_id);
        if let Ok(record) = self.store.artifact(&artifact_id) {
            if matches!(record.state, ArtifactState::Writing | ArtifactState::Validated) {
                if self.output.exists() { fs::remove_file(&self.output)?; }
                let partial = partial_path(&self.output);
                if partial.is_file() { fs::remove_file(partial)?; }
            }
        }
        let handoff_pending = matches!(
            self.store.artifact(&artifact_id).ok().map(|record| record.state),
            Some(ArtifactState::Writing | ArtifactState::Validated)
        );
        if !handoff_pending {
            let _ = self.store.scan_orphan_artifacts(&self.artifact_root)?;
            let _ = self.store.reconcile_job(&self.job_id, now)?;
        }
        self.run_to_completion(now)
    }

    pub fn run_to_completion(&mut self, now: u64) -> Result<MediaMetadata> {
        if self.status()? == JobStatus::Queued {
            self.store.start_job(&self.job_id, now)?;
        } else if !matches!(self.status()?, JobStatus::Running | JobStatus::Recovering) {
            return Err(SliceError::Invalid(format!("job cannot run from {:?}", self.status()?)));
        }
        let metadata = if self.stage_status(PROBE_STAGE)? == StageStatus::Succeeded {
            self.probe.probe(&self.source)?
        } else {
            self.run_probe(now)?
        };
        if self.stage_status(ANALYSIS_STAGE)? != StageStatus::Succeeded {
            self.run_analysis(&metadata, now)?;
        }
        if self.stage_status(RENDER_STAGE)? != StageStatus::Succeeded {
            self.run_render(now)?;
        }
        if self.stage_status(VALIDATE_STAGE)? != StageStatus::Succeeded {
            self.run_validate(&metadata, now)?;
        }
        if self.status()? != JobStatus::Succeeded {
            self.store.complete_job(&self.job_id, now)?;
        }
        Ok(metadata)
    }

    pub fn run_probe(&self, now: u64) -> Result<MediaMetadata> {
        let _ = self.store.start_stage(&self.job_id, PROBE_STAGE, now)?;
        match self.probe.probe(&self.source) {
            Ok(metadata) => {
                self.store.record_checkpoint(&self.job_id, PROBE_STAGE, "probe-complete", None, true, now.saturating_add(1))?;
                self.store.complete_stage(&self.job_id, PROBE_STAGE, now.saturating_add(2))?;
                Ok(metadata)
            }
            Err(error) => {
                self.store.fail_stage(&self.job_id, PROBE_STAGE, &error.to_string(), false, now.saturating_add(1))?;
                self.store.fail_job(&self.job_id, "media probe failed", now.saturating_add(2))?;
                Err(error.into())
            }
        }
    }

    /// Run the fake worker and persist its reusable checkpoint.  This method
    /// is intentionally deterministic and exercises the real JSONL validator.
    pub fn run_analysis(&self, metadata: &MediaMetadata, now: u64) -> Result<()> {
        let attempt = self.store.start_stage(&self.job_id, ANALYSIS_STAGE, now)?;
        let mut stream = StreamValidator::new(5_000, now)?;
        let command = Envelope::new(
            MessageType::Command,
            format!("{}-command", self.job_id),
            self.job_id.clone(),
            ANALYSIS_STAGE,
            1,
            Payload::Command {
                command: "analyze-deterministic".into(),
                args_json: format!("{{\"duration_ticks\":\"{}\"}}", metadata.duration.ticks),
            },
        )?;
        let _ = Envelope::from_line(&command.to_line()?)?;
        stream.accept(&command)?;
        let progress = Envelope::new(
            MessageType::Progress,
            format!("{}-progress", self.job_id),
            self.job_id.clone(),
            ANALYSIS_STAGE,
            2,
            Payload::Progress { fraction: 1.0, detail: Some("deterministic analysis".into()), units_done: Some(1), units_total: Some(1) },
        )?;
        stream.accept(&progress)?;
        let checkpoint_id = format!("analysis-attempt-{attempt}");
        let checkpoint = Envelope::new(
            MessageType::Checkpoint,
            format!("{}-checkpoint", self.job_id),
            self.job_id.clone(),
            ANALYSIS_STAGE,
            3,
            Payload::Checkpoint { checkpoint_id: checkpoint_id.clone(), reusable: true, artifact_hash: None },
        )?;
        stream.accept(&checkpoint)?;
        self.store.record_checkpoint(&self.job_id, ANALYSIS_STAGE, &checkpoint_id, None, true, now.saturating_add(1))?;
        let shutdown = Envelope::new(
            MessageType::Shutdown,
            format!("{}-shutdown", self.job_id),
            self.job_id.clone(),
            ANALYSIS_STAGE,
            4,
            Payload::Shutdown { status: ShutdownStatus::Completed },
        )?;
        stream.accept(&shutdown)?;
        stream.check_heartbeat(now.saturating_add(2))?;
        self.store.complete_stage(&self.job_id, ANALYSIS_STAGE, now.saturating_add(2))?;
        Ok(())
    }

    /// Atomically publish a passthrough output. The temporary file is flushed
    /// before the durable row is inserted and the final rename occurs; a
    /// partial path is never considered a publishable artifact.
    pub fn run_render(&self, now: u64) -> Result<()> {
        let _ = self.store.start_stage(&self.job_id, RENDER_STAGE, now)?;
        let artifact_id = format!("{}-render", self.job_id);
        let partial = partial_path(&self.output);
        // Keep the output outside the publishable namespace until every input
        // byte is flushed.  Register the final path before the atomic rename;
        // this gives restart recovery a durable row for the short rename
        // window without ever treating a partial as final.
        let result = copy_to_partial(&self.source, &partial);
        if let Err(error) = result {
            let _ = self.store.fail_stage(&self.job_id, RENDER_STAGE, &error.to_string(), true, now.saturating_add(2));
            return Err(error.into());
        }
        match self.store.artifact(&artifact_id) {
            Ok(record) if matches!(record.state, ArtifactState::Writing | ArtifactState::Validated) => {}
            Ok(record) => {
                return Err(SliceError::Invalid(format!(
                    "render artifact cannot be retried from {:?}",
                    record.state
                )));
            }
            Err(dubflow_job_state::StateError::NotFound { .. }) => {
                self.store.record_artifact_written(
                    &artifact_id,
                    &self.job_id,
                    RENDER_STAGE,
                    &self.output,
                    None,
                    true,
                    now.saturating_add(1),
                )?;
            }
            Err(error) => return Err(error.into()),
        }
        if self.output.exists() { fs::remove_file(&self.output)?; }
        fs::rename(&partial, &self.output)?;
        self.store.commit_artifact(&artifact_id, now.saturating_add(3))?;
        Ok(())
    }

    pub fn run_validate(&self, metadata: &MediaMetadata, now: u64) -> Result<()> {
        let _ = self.store.start_stage(&self.job_id, VALIDATE_STAGE, now)?;
        let output_metadata = self.probe.probe(&self.output)?;
        if output_metadata.dimensions != metadata.dimensions || output_metadata.time_base != metadata.time_base || output_metadata.duration != metadata.duration {
            self.store.fail_stage(&self.job_id, VALIDATE_STAGE, "passthrough metadata changed", false, now.saturating_add(1))?;
            self.store.fail_job(&self.job_id, "output validation failed", now.saturating_add(2))?;
            return Err(SliceError::Invalid("passthrough metadata changed".into()));
        }
        self.store.complete_stage(&self.job_id, VALIDATE_STAGE, now.saturating_add(1))?;
        Ok(())
    }

    /// Prepare a hard-kill boundary after a reusable analysis checkpoint.
    pub fn prepare_analysis_kill(&self, now: u64) -> Result<()> {
        if self.status()? == JobStatus::Queued { self.store.start_job(&self.job_id, now)?; }
        if self.stage_status(PROBE_STAGE)? != StageStatus::Succeeded { let _ = self.run_probe(now)?; }
        let metadata = self.probe.probe(&self.source)?;
        let _ = self.store.start_stage(&self.job_id, ANALYSIS_STAGE, now.saturating_add(3))?;
        self.store.record_checkpoint(&self.job_id, ANALYSIS_STAGE, "analysis-safe-kill", None, true, now.saturating_add(4))?;
        let _ = metadata;
        Ok(())
    }

    /// Prepare a hard-kill boundary while a render `.partial` exists.  The
    /// caller exits immediately after this method returns.
    pub fn prepare_render_kill(&self, now: u64) -> Result<()> {
        if self.status()? == JobStatus::Queued { self.store.start_job(&self.job_id, now)?; }
        if self.stage_status(PROBE_STAGE)? != StageStatus::Succeeded { let _ = self.run_probe(now)?; }
        if self.stage_status(ANALYSIS_STAGE)? != StageStatus::Succeeded {
            let metadata = self.probe.probe(&self.source)?;
            self.run_analysis(&metadata, now.saturating_add(3))?;
        }
        let _ = self.store.start_stage(&self.job_id, RENDER_STAGE, now.saturating_add(5))?;
        let partial = partial_path(&self.output);
        let mut input = File::open(&self.source)?;
        let mut output = OpenOptions::new().create(true).write(true).truncate(true).open(&partial)?;
        let mut buf = [0u8; 4096];
        let read = input.read(&mut buf)?;
        output.write_all(&buf[..read.min(32)])?;
        output.sync_all()?;
        Ok(())
    }
}

fn copy_to_partial(source: &Path, partial: &Path) -> io::Result<()> {
    if let Some(parent) = partial.parent() { fs::create_dir_all(parent)?; }
    let mut input = File::open(source)?;
    let mut temp = OpenOptions::new().create(true).write(true).truncate(true).open(partial)?;
    io::copy(&mut input, &mut temp)?;
    temp.sync_all()?;
    Ok(())
}

fn partial_path(output: &Path) -> PathBuf {
    output.with_file_name(format!("{}.partial", output.file_name().and_then(|name| name.to_str()).unwrap_or("output")))
}

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis().try_into().unwrap_or(u64::MAX)
}

/// Exposed for integration tests so they can assert that a partial is never a
/// final output path.
pub fn partial_output_path(output: &Path) -> PathBuf { partial_path(output) }

/// Return true only for a committed artifact.  A partial file or a writing
/// row is not a publishable output.
pub fn output_is_committed(store: &DurableStore, artifact_id: &str) -> Result<bool> {
    Ok(store.artifact(artifact_id)?.state == ArtifactState::Committed)
}
