//! DubFlow canonical media timeline contract, version 1.
//!
//! The crate deliberately has no dependencies.  It is a small executable
//! reference for the cross-language contract, rather than a media parser.  A
//! media adapter may retain a source's original time-base metadata, but all
//! durable identities use the normalized rational time-base and integer ticks
//! defined here.

use std::cmp::Ordering;
use std::fmt;

pub const SCHEMA_VERSION: u32 = 1;

/// Errors are explicit so an overflow, malformed wire value, or an invalid
/// media mapping can never silently become a plausible timestamp.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TimelineError {
    InvalidTimeBase,
    Overflow,
    NonIntegralRescale { numerator: u128, denominator: u128 },
    InvalidDuration,
    InvalidInterval,
    InvalidMapping(String),
    NoMappingSegment,
    InvalidDimensions,
    InvalidPoint,
    InvalidRotation(u16),
    InvalidAspectRatio,
    InvalidWire(String),
    UnsupportedSchema(u32),
}

impl fmt::Display for TimelineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidTimeBase => write!(f, "time base must have positive numerator and denominator"),
            Self::Overflow => write!(f, "checked timeline arithmetic overflowed"),
            Self::NonIntegralRescale { numerator, denominator } => {
                write!(f, "rescale is not integral: {numerator}/{denominator}")
            }
            Self::InvalidDuration => write!(f, "duration must be non-negative"),
            Self::InvalidInterval => write!(f, "interval must satisfy start <= end"),
            Self::InvalidMapping(reason) => write!(f, "invalid proxy/source mapping: {reason}"),
            Self::NoMappingSegment => write!(f, "timestamp falls outside every mapping segment"),
            Self::InvalidDimensions => write!(f, "dimensions must be non-zero"),
            Self::InvalidPoint => write!(f, "point is outside the half-open pixel-bound space"),
            Self::InvalidRotation(degrees) => write!(f, "unsupported clockwise rotation: {degrees} degrees"),
            Self::InvalidAspectRatio => write!(f, "pixel aspect ratio must be positive"),
            Self::InvalidWire(reason) => write!(f, "invalid canonical timeline wire value: {reason}"),
            Self::UnsupportedSchema(version) => write!(f, "unsupported timeline schema version: {version}"),
        }
    }
}

impl std::error::Error for TimelineError {}

pub type Result<T> = std::result::Result<T, TimelineError>;

/// The number of seconds represented by one tick is numerator/denominator.
/// Values are reduced so semantically identical bases have one representation.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TimeBase {
    pub numerator: u64,
    pub denominator: u64,
}

impl TimeBase {
    pub fn new(numerator: u64, denominator: u64) -> Result<Self> {
        if numerator == 0 || denominator == 0 {
            return Err(TimelineError::InvalidTimeBase);
        }
        let divisor = gcd_u64(numerator, denominator);
        Ok(Self {
            numerator: numerator / divisor,
            denominator: denominator / divisor,
        })
    }
}

/// Rounding is always named at a call site.  Exact is the default used by
/// contract-preserving operations and fails instead of inventing a tick.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RoundingMode {
    Exact,
    TowardZero,
    AwayFromZero,
    Floor,
    Ceil,
    NearestTiesToEven,
}

/// An absolute source presentation timestamp.  Negative PTS values are valid;
/// their sign is not used to infer a duration.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TimePoint {
    pub ticks: i64,
    pub time_base: TimeBase,
}

impl TimePoint {
    pub fn new(ticks: i64, time_base: TimeBase) -> Self {
        Self { ticks, time_base }
    }

    pub fn rescale(self, target: TimeBase, rounding: RoundingMode) -> Result<Self> {
        Ok(Self::new(
            scale_signed(self.ticks, self.time_base, target, rounding)?,
            target,
        ))
    }

    pub fn rescale_exact(self, target: TimeBase) -> Result<Self> {
        self.rescale(target, RoundingMode::Exact)
    }

    pub fn checked_add_offset(self, offset: TimeOffset, rounding: RoundingMode) -> Result<Self> {
        let offset = offset.rescale(self.time_base, rounding)?;
        let ticks = self.ticks.checked_add(offset.ticks).ok_or(TimelineError::Overflow)?;
        Ok(Self::new(ticks, self.time_base))
    }

    pub fn checked_add_duration(self, duration: Duration, rounding: RoundingMode) -> Result<Self> {
        let duration = duration.rescale(self.time_base, rounding)?;
        let sum = (self.ticks as i128)
            .checked_add(duration.ticks as i128)
            .ok_or(TimelineError::Overflow)?;
        if sum < i64::MIN as i128 || sum > i64::MAX as i128 {
            return Err(TimelineError::Overflow);
        }
        let ticks = sum as i64;
        Ok(Self::new(ticks, self.time_base))
    }

    /// Returns a signed relative offset (`self - other`) in self's time base.
    pub fn checked_sub(self, other: TimePoint, rounding: RoundingMode) -> Result<TimeOffset> {
        let other = other.rescale(self.time_base, rounding)?;
        let ticks = self.ticks.checked_sub(other.ticks).ok_or(TimelineError::Overflow)?;
        Ok(TimeOffset::new(ticks, self.time_base))
    }

    pub fn cmp_exact(self, other: Self) -> Result<Ordering> {
        compare_time_points(self, other)
    }

    pub fn to_wire(self) -> WireTimePoint {
        WireTimePoint::from(self)
    }
}

/// A signed relative offset.  It is distinct from an absolute PTS and from a
/// non-negative duration to prevent accidental semantic substitution.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TimeOffset {
    pub ticks: i64,
    pub time_base: TimeBase,
}

impl TimeOffset {
    pub fn new(ticks: i64, time_base: TimeBase) -> Self {
        Self { ticks, time_base }
    }

    pub fn rescale(self, target: TimeBase, rounding: RoundingMode) -> Result<Self> {
        Ok(Self::new(
            scale_signed(self.ticks, self.time_base, target, rounding)?,
            target,
        ))
    }

    pub fn rescale_exact(self, target: TimeBase) -> Result<Self> {
        self.rescale(target, RoundingMode::Exact)
    }
}

/// A non-negative elapsed duration.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Duration {
    pub ticks: u64,
    pub time_base: TimeBase,
}

impl Duration {
    pub fn new(ticks: u64, time_base: TimeBase) -> Self {
        Self { ticks, time_base }
    }

    pub fn rescale(self, target: TimeBase, rounding: RoundingMode) -> Result<Self> {
        Ok(Self::new(
            scale_unsigned(self.ticks, self.time_base, target, rounding)?,
            target,
        ))
    }

    pub fn rescale_exact(self, target: TimeBase) -> Result<Self> {
        self.rescale(target, RoundingMode::Exact)
    }
}

/// Half-open interval [start, end).  Endpoints are stored in one explicit
/// time base, avoiding a hidden float or a separately rounded duration.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Interval {
    pub start: TimePoint,
    pub end: TimePoint,
}

impl Interval {
    pub fn new(start: TimePoint, end: TimePoint) -> Result<Self> {
        let end = end.rescale_exact(start.time_base)?;
        if start.ticks > end.ticks {
            return Err(TimelineError::InvalidInterval);
        }
        Ok(Self { start, end })
    }

    pub fn from_duration(start: TimePoint, duration: Duration, rounding: RoundingMode) -> Result<Self> {
        let end = start.checked_add_duration(duration, rounding)?;
        Self::new(start, end)
    }

    pub fn contains(&self, point: TimePoint, rounding: RoundingMode) -> Result<bool> {
        let point = point.rescale(self.start.time_base, rounding)?;
        Ok(point.ticks >= self.start.ticks && point.ticks < self.end.ticks)
    }

    pub fn is_empty(&self) -> bool {
        self.start.ticks == self.end.ticks
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WireTimeBase {
    pub numerator: String,
    pub denominator: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WireTimePoint {
    pub schema_version: u32,
    pub ticks: String,
    pub time_base: WireTimeBase,
}

impl From<TimePoint> for WireTimePoint {
    fn from(point: TimePoint) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            ticks: point.ticks.to_string(),
            time_base: WireTimeBase {
                numerator: point.time_base.numerator.to_string(),
                denominator: point.time_base.denominator.to_string(),
            },
        }
    }
}

impl WireTimePoint {
    pub fn to_time_point(&self) -> Result<TimePoint> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(TimelineError::UnsupportedSchema(self.schema_version));
        }
        let ticks = parse_i64_decimal(&self.ticks)?;
        let numerator = parse_u64_decimal(&self.time_base.numerator)?;
        let denominator = parse_u64_decimal(&self.time_base.denominator)?;
        Ok(TimePoint::new(ticks, TimeBase::new(numerator, denominator)?))
    }

    /// Canonical JSON order and decimal-string encoding are part of the v1
    /// wire contract.  No float conversion or JSON number is used for ticks or
    /// rational factors.
    pub fn to_json(&self) -> Result<String> {
        let point = self.to_time_point()?;
        let point = WireTimePoint::from(point);
        Ok(format!(
            "{{\"kind\":\"time_point\",\"schema_version\":{},\"ticks\":\"{}\",\"time_base\":{{\"numerator\":\"{}\",\"denominator\":\"{}\"}}}}",
            point.schema_version,
            point.ticks,
            point.time_base.numerator,
            point.time_base.denominator
        ))
    }

    /// Parse only the canonical v1 object.  This intentionally rejects JSON
    /// numbers for wide fields, duplicate/reordered fields, escapes in numeric
    /// strings, plus signs, leading zeros, exponents, and unknown members.
    pub fn from_json(input: &str) -> Result<Self> {
        let mut parser = JsonCursor::new(input);
        parser.expect_byte(b'{')?;
        parser.expect_key("kind")?;
        if parser.parse_string()? != "time_point" {
            return Err(TimelineError::InvalidWire("kind must be time_point".into()));
        }
        parser.expect_comma()?;
        parser.expect_key("schema_version")?;
        let version = parser.parse_u64_token()?;
        if version > u32::MAX as u64 {
            return Err(TimelineError::Overflow);
        }
        let schema_version = version as u32;
        if schema_version != SCHEMA_VERSION {
            return Err(TimelineError::UnsupportedSchema(schema_version));
        }
        parser.expect_comma()?;
        parser.expect_key("ticks")?;
        let ticks = parser.parse_string()?;
        parse_i64_decimal(&ticks)?;
        parser.expect_comma()?;
        parser.expect_key("time_base")?;
        parser.expect_byte(b'{')?;
        parser.expect_key("numerator")?;
        let numerator = parser.parse_string()?;
        parse_u64_decimal(&numerator)?;
        parser.expect_comma()?;
        parser.expect_key("denominator")?;
        let denominator = parser.parse_string()?;
        parse_u64_decimal(&denominator)?;
        parser.expect_byte(b'}')?;
        parser.expect_byte(b'}')?;
        parser.finish()?;
        let wire = Self {
            schema_version,
            ticks,
            time_base: WireTimeBase { numerator, denominator },
        };
        wire.to_time_point()?;
        Ok(wire)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Dimensions {
    pub width: u32,
    pub height: u32,
}

impl Dimensions {
    pub fn new(width: u32, height: u32) -> Result<Self> {
        if width == 0 || height == 0 {
            return Err(TimelineError::InvalidDimensions);
        }
        Ok(Self { width, height })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AspectRatio {
    pub numerator: u32,
    pub denominator: u32,
}

impl AspectRatio {
    pub fn new(numerator: u32, denominator: u32) -> Result<Self> {
        if numerator == 0 || denominator == 0 {
            return Err(TimelineError::InvalidAspectRatio);
        }
        let divisor = gcd_u64(numerator as u64, denominator as u64) as u32;
        Ok(Self {
            numerator: numerator / divisor,
            denominator: denominator / divisor,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rotation {
    Deg0,
    Deg90,
    Deg180,
    Deg270,
}

impl Rotation {
    pub fn from_degrees(degrees: u16) -> Result<Self> {
        match degrees {
            0 => Ok(Self::Deg0),
            90 => Ok(Self::Deg90),
            180 => Ok(Self::Deg180),
            270 => Ok(Self::Deg270),
            other => Err(TimelineError::InvalidRotation(other)),
        }
    }

    pub fn degrees(self) -> u16 {
        match self {
            Self::Deg0 => 0,
            Self::Deg90 => 90,
            Self::Deg180 => 180,
            Self::Deg270 => 270,
        }
    }
}

/// Integer points are pixel-bound coordinates: (0,0) is the top-left corner
/// and (width,height) is the bottom-right corner.  This single convention is
/// used for OCR polygons and masks; pixel centers are not a second contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PixelPoint {
    pub x: i64,
    pub y: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct GeometryTransform {
    pub coded_dimensions: Dimensions,
    pub rotation: Rotation,
    pub pixel_aspect_ratio: AspectRatio,
}

impl GeometryTransform {
    pub fn new(
        coded_dimensions: Dimensions,
        rotation: Rotation,
        pixel_aspect_ratio: AspectRatio,
    ) -> Self {
        Self {
            coded_dimensions,
            rotation,
            pixel_aspect_ratio,
        }
    }

    pub fn display_dimensions(&self) -> Dimensions {
        match self.rotation {
            Rotation::Deg0 | Rotation::Deg180 => self.coded_dimensions,
            Rotation::Deg90 | Rotation::Deg270 => Dimensions {
                width: self.coded_dimensions.height,
                height: self.coded_dimensions.width,
            },
        }
    }

    pub fn to_display(&self, point: PixelPoint) -> Result<PixelPoint> {
        self.validate_point(point, self.coded_dimensions)?;
        let w = self.coded_dimensions.width as i64;
        let h = self.coded_dimensions.height as i64;
        Ok(match self.rotation {
            Rotation::Deg0 => point,
            Rotation::Deg90 => PixelPoint { x: h - point.y, y: point.x },
            Rotation::Deg180 => PixelPoint { x: w - point.x, y: h - point.y },
            Rotation::Deg270 => PixelPoint { x: point.y, y: w - point.x },
        })
    }

    pub fn from_display(&self, point: PixelPoint) -> Result<PixelPoint> {
        self.validate_point(point, self.display_dimensions())?;
        let w = self.coded_dimensions.width as i64;
        let h = self.coded_dimensions.height as i64;
        Ok(match self.rotation {
            Rotation::Deg0 => point,
            Rotation::Deg90 => PixelPoint { x: point.y, y: h - point.x },
            Rotation::Deg180 => PixelPoint { x: w - point.x, y: h - point.y },
            Rotation::Deg270 => PixelPoint { x: w - point.y, y: point.x },
        })
    }

    fn validate_point(&self, point: PixelPoint, dimensions: Dimensions) -> Result<()> {
        if point.x < 0
            || point.y < 0
            || point.x > dimensions.width as i64
            || point.y > dimensions.height as i64
        {
            return Err(TimelineError::InvalidPoint);
        }
        Ok(())
    }
}

/// One half-open piece of a proxy/source map.  Endpoints use a common base on
/// each side, but the source and proxy bases may differ.  Gaps and timestamp
/// discontinuities are represented by separate, non-overlapping segments.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MappingSegment {
    pub source_start: TimePoint,
    pub source_end: TimePoint,
    pub proxy_start: TimePoint,
    pub proxy_end: TimePoint,
}

impl MappingSegment {
    pub fn new(
        source_start: TimePoint,
        source_end: TimePoint,
        proxy_start: TimePoint,
        proxy_end: TimePoint,
    ) -> Result<Self> {
        if source_start.time_base != source_end.time_base {
            return Err(TimelineError::InvalidMapping("source endpoints need one time base".into()));
        }
        if proxy_start.time_base != proxy_end.time_base {
            return Err(TimelineError::InvalidMapping("proxy endpoints need one time base".into()));
        }
        if source_start.cmp_exact(source_end)? != Ordering::Less
            || proxy_start.cmp_exact(proxy_end)? != Ordering::Less
        {
            return Err(TimelineError::InvalidMapping("segments must have positive duration".into()));
        }
        Ok(Self {
            source_start,
            source_end,
            proxy_start,
            proxy_end,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProxySourceMapping {
    pub segments: Vec<MappingSegment>,
}

impl ProxySourceMapping {
    pub fn new(segments: Vec<MappingSegment>) -> Result<Self> {
        if segments.is_empty() {
            return Err(TimelineError::InvalidMapping("at least one segment is required".into()));
        }
        for pair in segments.windows(2) {
            if pair[0].proxy_end.cmp_exact(pair[1].proxy_start)? == Ordering::Greater {
                return Err(TimelineError::InvalidMapping("proxy segments overlap or are out of order".into()));
            }
            if pair[0].source_end.cmp_exact(pair[1].source_start)? == Ordering::Greater {
                return Err(TimelineError::InvalidMapping("source segments overlap or are out of order".into()));
            }
        }
        Ok(Self { segments })
    }

    pub fn map_proxy_to_source(&self, proxy: TimePoint, rounding: RoundingMode) -> Result<TimePoint> {
        for segment in &self.segments {
            let proxy = proxy.rescale(segment.proxy_start.time_base, rounding)?;
            if proxy.ticks >= segment.proxy_start.ticks && proxy.ticks < segment.proxy_end.ticks {
                let offset = proxy.ticks.checked_sub(segment.proxy_start.ticks).ok_or(TimelineError::Overflow)?;
                let mapped = scale_signed(offset, segment.proxy_start.time_base, segment.source_start.time_base, rounding)?;
                let ticks = segment.source_start.ticks.checked_add(mapped).ok_or(TimelineError::Overflow)?;
                if ticks < segment.source_start.ticks || ticks >= segment.source_end.ticks {
                    return Err(TimelineError::InvalidMapping("rounded point escaped source segment".into()));
                }
                return Ok(TimePoint::new(ticks, segment.source_start.time_base));
            }
        }
        Err(TimelineError::NoMappingSegment)
    }

    pub fn map_source_to_proxy(&self, source: TimePoint, rounding: RoundingMode) -> Result<TimePoint> {
        for segment in &self.segments {
            let source = source.rescale(segment.source_start.time_base, rounding)?;
            if source.ticks >= segment.source_start.ticks && source.ticks < segment.source_end.ticks {
                let offset = source.ticks.checked_sub(segment.source_start.ticks).ok_or(TimelineError::Overflow)?;
                let mapped = scale_signed(offset, segment.source_start.time_base, segment.proxy_start.time_base, rounding)?;
                let ticks = segment.proxy_start.ticks.checked_add(mapped).ok_or(TimelineError::Overflow)?;
                if ticks < segment.proxy_start.ticks || ticks >= segment.proxy_end.ticks {
                    return Err(TimelineError::InvalidMapping("rounded point escaped proxy segment".into()));
                }
                return Ok(TimePoint::new(ticks, segment.proxy_start.time_base));
            }
        }
        Err(TimelineError::NoMappingSegment)
    }
}

fn gcd_u64(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let remainder = a % b;
        a = b;
        b = remainder;
    }
    a
}

fn gcd_u128(mut a: u128, mut b: u128) -> u128 {
    while b != 0 {
        let remainder = a % b;
        a = b;
        b = remainder;
    }
    a
}

fn scale_ratio(from: TimeBase, to: TimeBase) -> Result<(u128, u128)> {
    let numerator = (from.numerator as u128)
        .checked_mul(to.denominator as u128)
        .ok_or(TimelineError::Overflow)?;
    let denominator = (from.denominator as u128)
        .checked_mul(to.numerator as u128)
        .ok_or(TimelineError::Overflow)?;
    let divisor = gcd_u128(numerator, denominator);
    Ok((numerator / divisor, denominator / divisor))
}

fn magnitude_i64(value: i64) -> u128 {
    if value < 0 {
        (-(value as i128)) as u128
    } else {
        value as u128
    }
}

fn round_unsigned(
    quotient: u128,
    remainder: u128,
    denominator: u128,
    mode: RoundingMode,
    negative: bool,
) -> Result<u128> {
    if remainder == 0 {
        return Ok(quotient);
    }
    let increment = match mode {
        RoundingMode::Exact => {
            return Err(TimelineError::NonIntegralRescale {
                numerator: remainder,
                denominator,
            })
        }
        RoundingMode::TowardZero => false,
        RoundingMode::AwayFromZero => true,
        RoundingMode::Floor => negative,
        RoundingMode::Ceil => !negative,
        RoundingMode::NearestTiesToEven => {
            remainder > denominator / 2
                || (denominator % 2 == 0
                    && remainder == denominator / 2
                    && quotient % 2 == 1)
        }
    };
    if increment {
        quotient.checked_add(1).ok_or(TimelineError::Overflow)
    } else {
        Ok(quotient)
    }
}

fn signed_from_magnitude(magnitude: u128, negative: bool) -> Result<i64> {
    const MIN_MAGNITUDE: u128 = 1u128 << 63;
    if negative {
        if magnitude == MIN_MAGNITUDE {
            Ok(i64::MIN)
        } else if magnitude <= i64::MAX as u128 {
            Ok(-(magnitude as i64))
        } else {
            Err(TimelineError::Overflow)
        }
    } else if magnitude <= i64::MAX as u128 {
        Ok(magnitude as i64)
    } else {
        Err(TimelineError::Overflow)
    }
}

fn scale_signed(value: i64, from: TimeBase, to: TimeBase, mode: RoundingMode) -> Result<i64> {
    let (mut numerator, mut denominator) = scale_ratio(from, to)?;
    let negative = value < 0;
    let mut magnitude = magnitude_i64(value);
    let divisor = gcd_u128(magnitude, denominator);
    if divisor != 0 {
        magnitude /= divisor;
        denominator /= divisor;
    }
    let product = magnitude.checked_mul(numerator).ok_or(TimelineError::Overflow)?;
    let quotient = product / denominator;
    let remainder = product % denominator;
    let quotient = round_unsigned(quotient, remainder, denominator, mode, negative)?;
    signed_from_magnitude(quotient, negative)
}

fn scale_unsigned(value: u64, from: TimeBase, to: TimeBase, mode: RoundingMode) -> Result<u64> {
    let (numerator, mut denominator) = scale_ratio(from, to)?;
    let mut value = value as u128;
    let divisor = gcd_u128(value, denominator);
    if divisor != 0 {
        value /= divisor;
        denominator /= divisor;
    }
    let product = value.checked_mul(numerator).ok_or(TimelineError::Overflow)?;
    let quotient = product / denominator;
    let remainder = product % denominator;
    let quotient = round_unsigned(quotient, remainder, denominator, mode, false)?;
    if quotient > u64::MAX as u128 {
        return Err(TimelineError::Overflow);
    }
    Ok(quotient as u64)
}

fn compare_positive_fractions(mut left_num: u128, mut left_den: u128, mut right_num: u128, mut right_den: u128) -> Result<Ordering> {
    let divisor = gcd_u128(left_num, left_den);
    left_num /= divisor;
    left_den /= divisor;
    let divisor = gcd_u128(right_num, right_den);
    right_num /= divisor;
    right_den /= divisor;
    let divisor = gcd_u128(left_num, right_num);
    if divisor != 0 {
        left_num /= divisor;
        right_num /= divisor;
    }
    let divisor = gcd_u128(left_den, right_den);
    left_den /= divisor;
    right_den /= divisor;
    let left = left_num.checked_mul(right_den).ok_or(TimelineError::Overflow)?;
    let right = right_num.checked_mul(left_den).ok_or(TimelineError::Overflow)?;
    Ok(left.cmp(&right))
}

fn compare_time_points(left: TimePoint, right: TimePoint) -> Result<Ordering> {
    if left.time_base == right.time_base {
        return Ok(left.ticks.cmp(&right.ticks));
    }
    let left_negative = left.ticks < 0;
    let right_negative = right.ticks < 0;
    if left_negative != right_negative {
        return Ok(if left_negative { Ordering::Less } else { Ordering::Greater });
    }
    let left_num = magnitude_i64(left.ticks)
        .checked_mul(left.time_base.numerator as u128)
        .ok_or(TimelineError::Overflow)?;
    let right_num = magnitude_i64(right.ticks)
        .checked_mul(right.time_base.numerator as u128)
        .ok_or(TimelineError::Overflow)?;
    let ordering = compare_positive_fractions(
        left_num,
        left.time_base.denominator as u128,
        right_num,
        right.time_base.denominator as u128,
    )?;
    Ok(if left_negative { ordering.reverse() } else { ordering })
}

fn parse_u64_decimal(value: &str) -> Result<u64> {
    if value.is_empty() || (value.len() > 1 && value.starts_with('0')) || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(TimelineError::InvalidWire("expected canonical unsigned decimal string".into()));
    }
    let mut result = 0u64;
    for byte in value.bytes() {
        result = result
            .checked_mul(10)
            .and_then(|value| value.checked_add((byte - b'0') as u64))
            .ok_or(TimelineError::Overflow)?;
    }
    Ok(result)
}

fn parse_i64_decimal(value: &str) -> Result<i64> {
    if value.is_empty() {
        return Err(TimelineError::InvalidWire("expected canonical signed decimal string".into()));
    }
    let (negative, digits) = if let Some(rest) = value.strip_prefix('-') {
        (true, rest)
    } else {
        (false, value)
    };
    if digits.is_empty()
        || (digits.len() > 1 && digits.starts_with('0'))
        || (negative && digits == "0")
        || !digits.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(TimelineError::InvalidWire("expected canonical signed decimal string".into()));
    }
    let mut magnitude = 0u128;
    for byte in digits.bytes() {
        magnitude = magnitude
            .checked_mul(10)
            .and_then(|value| value.checked_add((byte - b'0') as u128))
            .ok_or(TimelineError::Overflow)?;
    }
    signed_from_magnitude(magnitude, negative)
}

struct JsonCursor<'a> {
    input: &'a [u8],
    index: usize,
}

impl<'a> JsonCursor<'a> {
    fn new(input: &'a str) -> Self {
        Self { input: input.as_bytes(), index: 0 }
    }

    fn skip_ws(&mut self) {
        while self.input.get(self.index).is_some_and(|byte| byte.is_ascii_whitespace()) {
            self.index += 1;
        }
    }

    fn expect_byte(&mut self, expected: u8) -> Result<()> {
        self.skip_ws();
        if self.input.get(self.index) == Some(&expected) {
            self.index += 1;
            Ok(())
        } else {
            Err(TimelineError::InvalidWire(format!("expected JSON byte {:?}", expected as char)))
        }
    }

    fn expect_comma(&mut self) -> Result<()> {
        self.expect_byte(b',')
    }

    fn expect_key(&mut self, expected: &str) -> Result<()> {
        let actual = self.parse_string()?;
        if actual != expected {
            return Err(TimelineError::InvalidWire(format!("expected key {expected}")));
        }
        self.expect_byte(b':')
    }

    fn parse_string(&mut self) -> Result<String> {
        self.skip_ws();
        if self.input.get(self.index) != Some(&b'"') {
            return Err(TimelineError::InvalidWire("expected JSON string".into()));
        }
        self.index += 1;
        let start = self.index;
        while let Some(&byte) = self.input.get(self.index) {
            match byte {
                b'"' => {
                    let value = std::str::from_utf8(&self.input[start..self.index])
                        .map_err(|_| TimelineError::InvalidWire("string is not UTF-8".into()))?;
                    self.index += 1;
                    if value.bytes().any(|byte| byte < 0x20 || byte == b'\\') {
                        return Err(TimelineError::InvalidWire("wire strings may not contain escapes".into()));
                    }
                    return Ok(value.to_owned());
                }
                b'\\' | 0x00..=0x1f => {
                    return Err(TimelineError::InvalidWire("invalid escape/control byte".into()))
                }
                _ => self.index += 1,
            }
        }
        Err(TimelineError::InvalidWire("unterminated JSON string".into()))
    }

    fn parse_u64_token(&mut self) -> Result<u64> {
        self.skip_ws();
        let start = self.index;
        while let Some(&byte) = self.input.get(self.index) {
            if byte.is_ascii_digit() {
                self.index += 1;
            } else {
                break;
            }
        }
        if start == self.index {
            return Err(TimelineError::InvalidWire("expected JSON unsigned integer".into()));
        }
        let value = std::str::from_utf8(&self.input[start..self.index])
            .map_err(|_| TimelineError::InvalidWire("integer is not UTF-8".into()))?;
        parse_u64_decimal(value)
    }

    fn finish(&mut self) -> Result<()> {
        self.skip_ws();
        if self.index == self.input.len() {
            Ok(())
        } else {
            Err(TimelineError::InvalidWire("trailing JSON members or bytes".into()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hz(value: u64) -> TimeBase {
        TimeBase::new(1, value).unwrap()
    }

    #[test]
    fn time_base_is_reduced_and_rejects_zero() {
        assert_eq!(TimeBase::new(1000, 2000).unwrap(), TimeBase { numerator: 1, denominator: 2 });
        assert_eq!(TimeBase::new(0, 1), Err(TimelineError::InvalidTimeBase));
        assert_eq!(TimeBase::new(1, 0), Err(TimelineError::InvalidTimeBase));
    }

    #[test]
    fn exact_wide_tick_json_round_trip_never_uses_float() {
        let point = TimePoint::new(9_007_199_254_740_993, hz(90_000));
        let json = point.to_wire().to_json().unwrap();
        assert!(json.contains("\"ticks\":\"9007199254740993\""));
        let decoded = WireTimePoint::from_json(&json).unwrap().to_time_point().unwrap();
        assert_eq!(decoded, point);
        let minimum = TimePoint::new(i64::MIN, hz(1));
        assert_eq!(WireTimePoint::from_json(&minimum.to_wire().to_json().unwrap()).unwrap().to_time_point().unwrap(), minimum);
        let maximum = TimePoint::new(i64::MAX, hz(1));
        assert_eq!(WireTimePoint::from_json(&maximum.to_wire().to_json().unwrap()).unwrap().to_time_point().unwrap(), maximum);
    }

    #[test]
    fn malformed_wire_numbers_and_versions_are_rejected() {
        let point = TimePoint::new(1, hz(90_000));
        let json = point.to_wire().to_json().unwrap();
        assert!(WireTimePoint::from_json(&json.replace("\"1\"", "1")).is_err());
        assert!(WireTimePoint::from_json(&json.replace("\"ticks\":\"1\"", "\"ticks\":\"01\"")).is_err());
        assert!(WireTimePoint::from_json(&json.replace("\"schema_version\":1", "\"schema_version\":2")).is_err());
        assert!(WireTimePoint::from_json(&json.replace("\"denominator\":\"90000\"", "\"denominator\":\"+90000\"")).is_err());
    }

    #[test]
    fn rescale_rounding_is_explicit_and_negative_safe() {
        let third = TimePoint::new(1, hz(3));
        assert_eq!(third.rescale(hz(2), RoundingMode::Exact), Err(TimelineError::NonIntegralRescale { numerator: 2, denominator: 3 }));
        assert_eq!(third.rescale(hz(2), RoundingMode::NearestTiesToEven).unwrap().ticks, 1);
        let negative = TimePoint::new(-1, hz(3));
        assert_eq!(negative.rescale(hz(2), RoundingMode::Floor).unwrap().ticks, -1);
        assert_eq!(negative.rescale(hz(2), RoundingMode::Ceil).unwrap().ticks, 0);
    }

    #[test]
    fn timestamp_offset_duration_and_half_open_interval_are_distinct() {
        let base = hz(1_000);
        let start = TimePoint::new(10, base);
        let interval = Interval::from_duration(start, Duration::new(5, base), RoundingMode::Exact).unwrap();
        assert!(interval.contains(TimePoint::new(10, base), RoundingMode::Exact).unwrap());
        assert!(interval.contains(TimePoint::new(14, base), RoundingMode::Exact).unwrap());
        assert!(!interval.contains(TimePoint::new(15, base), RoundingMode::Exact).unwrap());
        let offset = start.checked_sub(TimePoint::new(12, base), RoundingMode::Exact).unwrap();
        assert_eq!(offset.ticks, -2);
    }

    #[test]
    fn vfr_nonzero_pts_and_piecewise_proxy_mapping_preserve_gaps() {
        let source_base = hz(90_000);
        let proxy_base = hz(1_000);
        let first = MappingSegment::new(
            TimePoint::new(90_000, source_base),
            TimePoint::new(270_000, source_base),
            TimePoint::new(0, proxy_base),
            TimePoint::new(2_000, proxy_base),
        )
        .unwrap();
        let second = MappingSegment::new(
            TimePoint::new(360_000, source_base),
            TimePoint::new(540_000, source_base),
            TimePoint::new(3_000, proxy_base),
            TimePoint::new(5_000, proxy_base),
        )
        .unwrap();
        let mapping = ProxySourceMapping::new(vec![first, second]).unwrap();
        let mapped = mapping.map_proxy_to_source(TimePoint::new(1_000, proxy_base), RoundingMode::Exact).unwrap();
        assert_eq!(mapped, TimePoint::new(180_000, source_base));
        assert_eq!(mapping.map_source_to_proxy(mapped, RoundingMode::Exact).unwrap().ticks, 1_000);
        assert_eq!(mapping.map_proxy_to_source(TimePoint::new(2_500, proxy_base), RoundingMode::Exact), Err(TimelineError::NoMappingSegment));
        assert_eq!(mapping.map_proxy_to_source(TimePoint::new(5_000, proxy_base), RoundingMode::Exact), Err(TimelineError::NoMappingSegment));
    }

    #[test]
    fn rotation_transforms_all_corners_and_swap_dimensions() {
        let dimensions = Dimensions::new(1920, 1080).unwrap();
        let aspect = AspectRatio::new(4, 2).unwrap();
        assert_eq!(aspect, AspectRatio { numerator: 2, denominator: 1 });
        let corners = [
            PixelPoint { x: 0, y: 0 },
            PixelPoint { x: 1920, y: 0 },
            PixelPoint { x: 0, y: 1080 },
            PixelPoint { x: 1920, y: 1080 },
        ];
        for rotation in [Rotation::Deg0, Rotation::Deg90, Rotation::Deg180, Rotation::Deg270] {
            let transform = GeometryTransform::new(dimensions, rotation, aspect);
            if matches!(rotation, Rotation::Deg90 | Rotation::Deg270) {
                assert_eq!(transform.display_dimensions(), Dimensions { width: 1080, height: 1920 });
            }
            for corner in corners {
                let display = transform.to_display(corner).unwrap();
                assert_eq!(transform.from_display(display).unwrap(), corner);
            }
        }
        assert_eq!(Rotation::from_degrees(45), Err(TimelineError::InvalidRotation(45)));
    }

    #[test]
    fn checked_extreme_arithmetic_returns_errors_instead_of_wrapping() {
        let point = TimePoint::new(i64::MAX, hz(1));
        assert_eq!(point.checked_add_offset(TimeOffset::new(1, hz(1)), RoundingMode::Exact), Err(TimelineError::Overflow));
        let huge = TimeBase::new(u64::MAX, 1).unwrap();
        assert_eq!(TimePoint::new(i64::MAX, huge).rescale(hz(1), RoundingMode::Exact), Err(TimelineError::Overflow));
    }
}
