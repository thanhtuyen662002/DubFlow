"""Deterministic SRT/ASS Vietnamese subtitle compositor."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence
import re
import unicodedata

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint


SUBTITLE_CONTRACT_VERSION = 1
TARGET_LANGUAGE = "vi"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class SubtitleError(ValueError):
    """A stable composition or wire-validation failure."""

    def __init__(self, code: str, condition: str) -> None:
        if not code or not condition:
            raise ValueError("subtitle failures require code and condition")
        self.code = code
        self.condition = _safe_condition(condition)
        super().__init__(f"{code}: {self.condition}")


def _safe_condition(value: Any) -> str:
    text = _CONTROL.sub(" ", str(value) or "subtitle compositor returned an empty condition").strip()
    return text[:4096] or "subtitle compositor returned an empty condition"


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise SubtitleError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-free")
    return value


def _blob(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit or any(ord(char) < 0x20 and char not in "\r\n\t" for char in value) or "\x7f" in value:
        raise SubtitleError("INVALID_TEXT", f"{name} must be non-empty, bounded and control-safe")
    return value


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _integer(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise SubtitleError("INVALID_INTEGER", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise SubtitleError("INVALID_INTEGER", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SubtitleError("INVALID_INTEGER", f"{name} must be <= {maximum}")
    return value


def _confidence(value: Any, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise SubtitleError("INVALID_CONFIDENCE", f"{name} must be finite")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise SubtitleError("INVALID_CONFIDENCE", f"{name} must be between 0 and 1")
    return result


def _hash_json(value: Any) -> str:
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _ensure_hash(value: Any, name: str) -> str:
    value = _text(value, name, limit=80)
    if _SHA256.fullmatch(value) is None:
        raise SubtitleError("INVALID_HASH", f"{name} must be a sha256 digest")
    return value


@dataclass(frozen=True)
class SubtitleInput:
    source_utterance_id: str
    text: str
    start: TimePoint
    end: TimePoint
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _text(self.source_utterance_id, "subtitle.source_utterance_id", limit=256)
        _text(self.text, "subtitle.text", limit=8192)
        _confidence(self.confidence, "subtitle.confidence")
        if not isinstance(self.start, TimePoint) or not isinstance(self.end, TimePoint):
            raise SubtitleError("INVALID_TIMELINE", "subtitle boundaries must be canonical time points")
        if self.start.time_base != self.end.time_base:
            raise SubtitleError("TIMELINE_TIME_BASE_MISMATCH", "subtitle boundaries use different time bases")
        try:
            TimeInterval(self.start, self.end)
        except Exception as error:
            raise SubtitleError("INVALID_TIMELINE", str(error)) from error

    @classmethod
    def from_translation(cls, segment: Any) -> "SubtitleInput":
        try:
            return cls(segment.source_utterance_id, segment.translated_text, segment.start, segment.end, segment.confidence)
        except AttributeError as error:
            raise SubtitleError("INVALID_SOURCE", "translation segment lacks required identity/timing fields") from error


@dataclass(frozen=True)
class SubtitleConfig:
    video_width: int
    video_height: int
    max_line_chars: int = 42
    max_lines: int = 2
    max_chars_per_second: int = 17
    minimum_duration_ms: int = 500
    minimum_gap_ms: int = 40
    position: str = "bottom-center"
    font_name: str = "Noto Sans"
    font_fallbacks: tuple[str, ...] = ("Arial", "sans-serif")
    font_available: bool = True

    def __post_init__(self) -> None:
        _integer(self.video_width, "config.video_width", minimum=1, maximum=65535)
        _integer(self.video_height, "config.video_height", minimum=1, maximum=65535)
        _integer(self.max_line_chars, "config.max_line_chars", minimum=4, maximum=256)
        _integer(self.max_lines, "config.max_lines", minimum=1, maximum=4)
        _integer(self.max_chars_per_second, "config.max_chars_per_second", minimum=1, maximum=1000)
        _integer(self.minimum_duration_ms, "config.minimum_duration_ms", minimum=0, maximum=60000)
        _integer(self.minimum_gap_ms, "config.minimum_gap_ms", minimum=0, maximum=10000)
        if self.position not in {"top-center", "middle-center", "bottom-center"}:
            raise SubtitleError("INVALID_LAYOUT", "subtitle position is unsupported")
        _text(self.font_name, "config.font_name", limit=128)
        if not self.font_fallbacks or len(set(self.font_fallbacks)) != len(self.font_fallbacks):
            raise SubtitleError("INVALID_FONT_POLICY", "font fallbacks must be non-empty and unique")
        for font in self.font_fallbacks:
            _text(font, "config.font_fallback", limit=128)
        if type(self.font_available) is not bool:
            raise SubtitleError("INVALID_FONT_POLICY", "font_available must be boolean")

    @property
    def aspect(self) -> str:
        if self.video_width == self.video_height:
            return "square"
        return "landscape" if self.video_width > self.video_height else "portrait"

    def to_hash(self) -> str:
        return _hash_json({
            "video_width": self.video_width,
            "video_height": self.video_height,
            "max_line_chars": self.max_line_chars,
            "max_lines": self.max_lines,
            "max_chars_per_second": self.max_chars_per_second,
            "minimum_duration_ms": self.minimum_duration_ms,
            "minimum_gap_ms": self.minimum_gap_ms,
            "position": self.position,
            "font_name": self.font_name,
            "font_fallbacks": list(self.font_fallbacks),
            "font_available": self.font_available,
        })


@dataclass(frozen=True)
class SubtitleProvenance:
    producer: str
    producer_version: str
    config_hash: str
    input_hash: str
    timeline_contract: str

    def __post_init__(self) -> None:
        _text(self.producer, "provenance.producer", limit=128)
        _text(self.producer_version, "provenance.producer_version", limit=128)
        if self.timeline_contract != "timeline-v1":
            raise SubtitleError("UNSUPPORTED_TIMELINE_CONTRACT", "subtitle v1 requires timeline-v1")
        _ensure_hash(self.config_hash, "provenance.config_hash")
        _ensure_hash(self.input_hash, "provenance.input_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "config_hash": self.config_hash,
            "input_hash": self.input_hash,
            "timeline_contract": self.timeline_contract,
        }


@dataclass(frozen=True)
class SubtitleLayout:
    width: int
    height: int
    aspect: str
    position: str
    margin_left: int
    margin_right: int
    margin_top: int
    margin_bottom: int
    font_name: str
    font_fallbacks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "aspect": self.aspect,
            "position": self.position,
            "margin_left": self.margin_left,
            "margin_right": self.margin_right,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "font_name": self.font_name,
            "font_fallbacks": list(self.font_fallbacks),
        }


@dataclass(frozen=True)
class SubtitleCue:
    cue_id: str
    source_utterance_id: str
    start: TimePoint
    end: TimePoint
    text: str
    lines: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "source_utterance_id": self.source_utterance_id,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "text": self.text,
            "lines": list(self.lines),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class SubtitleDocument:
    timeline_offset: TimePoint
    cues: tuple[SubtitleCue, ...]
    layout: SubtitleLayout
    srt: str
    ass: str
    provenance: SubtitleProvenance
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SUBTITLE_CONTRACT_VERSION,
            "kind": "subtitle_document",
            "source_kind": "translation",
            "target_language": TARGET_LANGUAGE,
            "timeline_offset": self.timeline_offset.to_dict(),
            "cues": [item.to_dict() for item in self.cues],
            "layout": self.layout.to_dict(),
            "srt": self.srt,
            "ass": self.ass,
            "provenance": self.provenance.to_dict(),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} must be an object")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} has unknown fields: {sorted(unknown)}")
    if missing:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} is missing fields: {sorted(missing)}")
    return value


def _decimal(value: Any, name: str, *, signed: bool) -> int:
    if type(value) is not str:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} must be a decimal string")
    pattern = r"^(0|-?[1-9][0-9]*)$" if signed else r"^[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} is not canonical decimal")
    result = int(value)
    _integer(result, name, minimum=I64_MIN if signed else 1, maximum=I64_MAX if signed else U64_MAX)
    return result


def _parse_point(value: Any, name: str) -> TimePoint:
    value = _object(value, name, {"kind", "schema_version", "ticks", "time_base"})
    if value["kind"] != "time_point" or value["schema_version"] != SUBTITLE_CONTRACT_VERSION:
        raise SubtitleError("INVALID_DOCUMENT", f"{name} discriminator/version is unsupported")
    base = _object(value["time_base"], f"{name}.time_base", {"numerator", "denominator"})
    try:
        return TimePoint(
            _decimal(value["ticks"], f"{name}.ticks", signed=True),
            TimeBase(
                _decimal(base["numerator"], f"{name}.time_base.numerator", signed=False),
                _decimal(base["denominator"], f"{name}.time_base.denominator", signed=False),
            ),
        )
    except SubtitleError:
        raise
    except Exception as error:
        raise SubtitleError("INVALID_TIMELINE", str(error)) from error


def _parse_interval(start: Any, end: Any, name: str) -> TimeInterval:
    try:
        return TimeInterval(start, end)
    except Exception as error:
        raise SubtitleError("INVALID_TIMELINE", f"{name}: {error}") from error


def validate_subtitle_document(value: Mapping[str, Any]) -> None:
    value = _object(value, "document", {"schema_version", "kind", "source_kind", "target_language", "timeline_offset", "cues", "layout", "srt", "ass", "provenance", "warnings"})
    if value["schema_version"] != SUBTITLE_CONTRACT_VERSION or value["kind"] != "subtitle_document" or value["source_kind"] != "translation" or value["target_language"] != TARGET_LANGUAGE:
        raise SubtitleError("INVALID_DOCUMENT", "subtitle document discriminator/version is unsupported")
    offset = _parse_point(value["timeline_offset"], "timeline_offset")
    if offset.ticks < 0:
        raise SubtitleError("INVALID_DOCUMENT", "timeline offset must be non-negative")
    layout = _object(value["layout"], "layout", {"width", "height", "aspect", "position", "margin_left", "margin_right", "margin_top", "margin_bottom", "font_name", "font_fallbacks"})
    _integer(layout["width"], "layout.width", minimum=1, maximum=65535)
    _integer(layout["height"], "layout.height", minimum=1, maximum=65535)
    if layout["aspect"] not in {"portrait", "landscape", "square"} or layout["position"] not in {"top-center", "middle-center", "bottom-center"}:
        raise SubtitleError("INVALID_DOCUMENT", "layout aspect or position is invalid")
    for name in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        _integer(layout[name], f"layout.{name}", minimum=0)
    _text(layout["font_name"], "layout.font_name", limit=128)
    if type(layout["font_fallbacks"]) is not list or not layout["font_fallbacks"] or len(set(layout["font_fallbacks"])) != len(layout["font_fallbacks"]):
        raise SubtitleError("INVALID_DOCUMENT", "layout font fallbacks are invalid")
    for font in layout["font_fallbacks"]:
        _text(font, "layout.font_fallback", limit=128)
    _blob(value["srt"], "srt", limit=2_000_000)
    _blob(value["ass"], "ass", limit=2_000_000)
    provenance = _object(value["provenance"], "provenance", {"producer", "producer_version", "config_hash", "input_hash", "timeline_contract"})
    SubtitleProvenance(**provenance)
    cues = value["cues"]
    if type(cues) is not list:
        raise SubtitleError("INVALID_DOCUMENT", "cues must be an array")
    ids: set[str] = set()
    previous_end: TimePoint | None = None
    for index, raw in enumerate(cues):
        item = _object(raw, f"cues[{index}]", {"cue_id", "source_utterance_id", "start", "end", "text", "lines", "confidence"})
        cue_id = _text(item["cue_id"], "cue.cue_id", limit=256)
        if cue_id in ids:
            raise SubtitleError("INVALID_DOCUMENT", "cue IDs must be unique")
        ids.add(cue_id)
        _text(item["source_utterance_id"], "cue.source_utterance_id", limit=256)
        start = _parse_point(item["start"], f"cues[{index}].start")
        end = _parse_point(item["end"], f"cues[{index}].end")
        if start.ticks < 0 or end.ticks < 0:
            raise SubtitleError("INVALID_DOCUMENT", "subtitle cues cannot have negative times")
        _parse_interval(start, end, f"cue {cue_id}")
        if previous_end is not None and previous_end.compare(start) > 0:
            raise SubtitleError("INVALID_DOCUMENT", "subtitle cues overlap")
        previous_end = end
        _text(item["text"], "cue.text", limit=8192)
        lines = item["lines"]
        if type(lines) is not list or not lines or len(lines) > 4:
            raise SubtitleError("INVALID_DOCUMENT", "cue lines are invalid")
        for line in lines:
            _text(line, "cue.line", limit=256)
        _confidence(item["confidence"], "cue.confidence")
    if type(value["warnings"]) is not list or any(type(item) is not str or not item.strip() for item in value["warnings"]):
        raise SubtitleError("INVALID_DOCUMENT", "warnings must be non-empty strings")


def parse_subtitle_json(text: str | bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise SubtitleError("DUPLICATE_FIELD", f"duplicate JSON member {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise SubtitleError("NON_FINITE_NUMBER", f"JSON constant {value!r} is not allowed")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except SubtitleError:
        raise
    except (TypeError, ValueError) as error:
        raise SubtitleError("MALFORMED_JSON", str(error)) from error
    validate_subtitle_document(value)
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _duration_ticks_for_ms(milliseconds: int, base: TimeBase) -> int:
    return _ceil_div(milliseconds * base.denominator, 1000 * base.numerator)


def _wrap_lines(text: str, max_chars: int) -> tuple[str, ...]:
    normalized = _normalize(text)
    words = normalized.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        pieces = [word[index : index + max_chars] for index in range(0, len(word), max_chars)] or [""]
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= max_chars:
                current += " " + piece
            else:
                lines.append(current)
                current = piece
    if current:
        lines.append(current)
    return tuple(lines)


def _line_groups(lines: Sequence[str], max_lines: int) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(lines[index : index + max_lines]) for index in range(0, len(lines), max_lines))


def _format_srt_time(point: TimePoint, *, end: bool) -> str:
    numerator = point.ticks * point.time_base.numerator * 1000
    denominator = point.time_base.denominator
    milliseconds = _ceil_div(numerator, denominator) if end else numerator // denominator
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _escape_ass(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


class LocalSubtitleComposer:
    """Compose safe subtitle cues from translated canonical segments."""

    def __init__(self, *, config: SubtitleConfig, provenance: SubtitleProvenance) -> None:
        self.config = config
        self.provenance = provenance
        if provenance.config_hash != config.to_hash():
            raise SubtitleError("PROVENANCE_CONFIG_MISMATCH", "provenance config hash differs from subtitle config")

    def compose(self, segments: Sequence[SubtitleInput] | Sequence[Any], *, input_hash: str) -> SubtitleDocument:
        if input_hash != self.provenance.input_hash:
            raise SubtitleError("PROVENANCE_INPUT_MISMATCH", "subtitle input hash differs from provenance")
        items = tuple(item if isinstance(item, SubtitleInput) else SubtitleInput.from_translation(item) for item in segments)
        if not items:
            raise SubtitleError("EMPTY_SUBTITLES", "subtitle input has no translated segments")
        seen: set[str] = set()
        base = items[0].start.time_base
        for item in items:
            if item.source_utterance_id in seen:
                raise SubtitleError("DUPLICATE_SOURCE_ID", f"duplicate subtitle source ID {item.source_utterance_id}")
            seen.add(item.source_utterance_id)
            if item.start.time_base != base:
                raise SubtitleError("TIMELINE_TIME_BASE_MISMATCH", "subtitle segments must share one time base")
        ordered = tuple(sorted(items, key=lambda item: (item.start.ticks, item.end.ticks, item.source_utterance_id)))
        shift = max(0, -min(item.start.ticks for item in ordered))
        offset = TimePoint(shift, base)
        warnings: list[str] = []
        if shift:
            warnings.append("timeline shifted to zero for subtitle format; canonical offset is recorded")
        if not self.config.font_available:
            warnings.append(f"font fallback selected: {', '.join(self.config.font_fallbacks)}")
        layout = self._layout()
        cues: list[SubtitleCue] = []
        previous_end: TimePoint | None = None
        gap_ticks = _duration_ticks_for_ms(self.config.minimum_gap_ms, base)
        minimum_ticks = _duration_ticks_for_ms(self.config.minimum_duration_ms, base)
        for index, item in enumerate(ordered):
            start = TimePoint(item.start.ticks + shift, base)
            natural_end = TimePoint(item.end.ticks + shift, base)
            if previous_end is not None and previous_end.compare(start) > 0:
                raise SubtitleError("OVERLAPPING_CUES", f"subtitle source intervals overlap at {item.source_utterance_id}")
            next_start = None
            if index + 1 < len(ordered):
                next_start = TimePoint(ordered[index + 1].start.ticks + shift, base)
            required_ticks = max(minimum_ticks, _ceil_div(len(_normalize(item.text)) * base.denominator, self.config.max_chars_per_second * base.numerator))
            desired_end = TimePoint(max(natural_end.ticks, start.ticks + required_ticks), base)
            end = natural_end
            if desired_end.compare(natural_end) > 0:
                cap = TimePoint(next_start.ticks - gap_ticks, base) if next_start is not None else None
                if cap is not None and desired_end.compare(cap) <= 0:
                    end = desired_end
                    warnings.append(f"cue {item.source_utterance_id} duration extended for reading speed")
                else:
                    if next_start is not None and natural_end.compare(next_start) > 0:
                        raise SubtitleError("OVERLAPPING_CUES", f"source cue has no non-overlapping presentation interval at {item.source_utterance_id}")
                    warnings.append(f"cue {item.source_utterance_id} exceeds reading-speed budget")
            groups = _line_groups(_wrap_lines(item.text, self.config.max_line_chars), self.config.max_lines)
            if len(groups) > 1:
                warnings.append(f"cue {item.source_utterance_id} split into {len(groups)} readable subtitle cues")
            duration_ticks = end.ticks - start.ticks
            if duration_ticks < len(groups):
                raise SubtitleError("TEXT_TOO_LONG_FOR_INTERVAL", f"cue {item.source_utterance_id} cannot be split into positive intervals")
            weights = [max(1, sum(len(line) for line in group)) for group in groups]
            total_weight = sum(weights)
            cursor = start.ticks
            for group_index, (group, weight) in enumerate(zip(groups, weights)):
                if group_index == len(groups) - 1:
                    group_end = end.ticks
                else:
                    remaining_groups = len(groups) - group_index - 1
                    allocation = max(1, (duration_ticks * weight) // total_weight)
                    allocation = min(allocation, end.ticks - cursor - remaining_groups)
                    group_end = cursor + allocation
                cue_id = "cue-" + sha256(f"{item.source_utterance_id}|{cursor}|{group_end}|{group_index}".encode("utf-8")).hexdigest()[:24]
                group_text = " ".join(group)
                cues.append(SubtitleCue(cue_id, item.source_utterance_id, TimePoint(cursor, base), TimePoint(group_end, base), group_text, group, item.confidence))
                cursor = group_end
            previous_end = end
        srt = self._srt(cues)
        ass = self._ass(cues, layout)
        document = SubtitleDocument(offset, tuple(cues), layout, srt, ass, self.provenance, tuple(dict.fromkeys(warnings)))
        validate_subtitle_document(document.to_dict())
        return document

    def _layout(self) -> SubtitleLayout:
        margin_x = max(1, self.config.video_width * 8 // 100)
        margin_y = max(1, self.config.video_height * 8 // 100)
        return SubtitleLayout(
            self.config.video_width,
            self.config.video_height,
            self.config.aspect,
            self.config.position,
            margin_x,
            margin_x,
            margin_y,
            margin_y,
            self.config.font_name if self.config.font_available else self.config.font_fallbacks[0],
            self.config.font_fallbacks,
        )

    @staticmethod
    def _srt(cues: Sequence[SubtitleCue]) -> str:
        blocks = []
        for index, cue in enumerate(cues, start=1):
            blocks.append(
                f"{index}\n{_format_srt_time(cue.start, end=False)} --> {_format_srt_time(cue.end, end=True)}\n{chr(10).join(cue.lines)}"
            )
        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _ass(cues: Sequence[SubtitleCue], layout: SubtitleLayout) -> str:
        alignment = {"top-center": 8, "middle-center": 5, "bottom-center": 2}[layout.position]
        header = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {layout.width}",
            f"PlayResY: {layout.height}",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{layout.font_name},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,{alignment},{layout.margin_left},{layout.margin_right},{layout.margin_bottom},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for cue in cues:
            start = _format_ass_time(cue.start, end=False)
            end = _format_ass_time(cue.end, end=True)
            text = _escape_ass("\n".join(cue.lines))
            header.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        return "\n".join(header) + "\n"


def _format_ass_time(point: TimePoint, *, end: bool) -> str:
    numerator = point.ticks * point.time_base.numerator * 100
    denominator = point.time_base.denominator
    centiseconds = (-(-numerator // denominator) if end else numerator // denominator)
    centiseconds = max(0, centiseconds)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"
