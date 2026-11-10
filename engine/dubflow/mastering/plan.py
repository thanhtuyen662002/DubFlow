"""The mastering plan and the mastering options (``contracts/mastering`` v1, ADR-0052).

A plan is the planner's decisions for one output video: which part of which
source plays where on the output timeline, in what role, with what framing, how
sure the planner was and why. Every time is an integer tick of the plan's one
reduced ``time_base`` (1/1000 s by default, ``media.CANONICAL_TIME_BASE``),
written as a decimal string like the timeline contract (ADR-0002). Seconds and
milliseconds are derived only at a renderer's boundary (``to_milliseconds``).

The model is dependency-free and strict: unknown keys, floats or booleans where
an integer is due, overlaps and gaps on the timeline, spans outside a source,
and the effects the product never applies on its own (flip, a fractional
rotation, a stretch) are all rejected, so a renderer can trust a loaded plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
PLAN_KIND = "mastering_plan"
OPTIONS_KIND = "mastering_options"

WORKFLOWS = ("dubbing_preserve", "product_showcase", "compilation_entertainment", "documentary_narrative")
LEVELS = ("off", "light", "balanced", "dynamic")
STYLES = ("natural", "dynamic", "cinematic", "technical", "entertainment", "sports", "educational", "storytelling")
ROLES = ("primary", "demonstration", "reaction", "broll", "hook", "transition", "still")
AUDIO_MODES = ("source", "narration", "mixed", "mute")
RIGHTS = ("owned", "licensed", "permitted", "unknown")
# What a planner may ask a renderer to add. Flip, fractional rotation, stretch and an export zoom are not here on
# purpose: they would misrepresent the footage (ADR-0052 §4), and a plan naming one is rejected.
EFFECTS = ("fade_in", "fade_out", "crossfade", "fade_black", "color_match", "lut", "ken_burns", "text_overlay", "sfx_hit")
FORBIDDEN_EFFECTS = ("flip_h", "flip_v", "rotate", "stretch", "export_zoom")

I64_MAX = 2**63 - 1
U64_MAX = 2**64 - 1
MAX_SOURCES = 200
MAX_SEGMENTS = 5000
MAX_WARNINGS = 500
MAX_TEXT = 500
MAX_REFS = 64
MIN_ZOOM_MILLI, MAX_ZOOM_MILLI = 1000, 3000
BASIS = 10_000  # Fractions of the picture in basis points.
PLAN_LIMIT = 8 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PlanError(ValueError):
    """A plan or options document that does not follow ``contracts/mastering`` v1."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------- primitives ----------

def _fail(code: str, message: str) -> PlanError:
    return PlanError(code, message)


def _object(value: Any, label: str, required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail("INVALID_DOCUMENT", f"{label} must be an object")
    required, optional = set(required), set(optional)
    missing = required - set(value)
    if missing:
        raise _fail("INVALID_DOCUMENT", f"{label} misses {sorted(missing)}")
    unknown = set(value) - required - optional
    if unknown:
        raise _fail("INVALID_DOCUMENT", f"{label} has unknown {sorted(unknown)}")
    return value


def _int(value: Any, label: str, *, minimum: int = 0, maximum: int = I64_MAX) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail("INVALID_INTEGER", f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _ticks(value: Any, label: str) -> int:
    """A canonical non-negative decimal string (no sign, no leading zero, no exponent)."""
    if type(value) is not str or not _DECIMAL.match(value) or int(value) > I64_MAX:
        raise _fail("INVALID_TICKS", f"{label} must be a canonical decimal string of ticks")
    return int(value)


def _text(value: Any, label: str, *, limit: int = MAX_TEXT, empty: bool = False) -> str:
    if type(value) is not str or len(value) > limit or (not empty and not value.strip()) or "\x00" in value:
        raise _fail("INVALID_TEXT", f"{label} must be text of at most {limit} characters")
    return value


def _ident(value: Any, label: str) -> str:
    if type(value) is not str or not _ID.match(value):
        raise _fail("INVALID_ID", f"{label} must be an id of letters, digits and _.:-")
    return value


def _choice(value: Any, label: str, choices: Sequence[str]) -> str:
    if type(value) is not str or value not in choices:
        raise _fail("INVALID_CHOICE", f"{label} must be one of {list(choices)}")
    return value


@dataclass(frozen=True)
class TimeBase:
    """Reduced positive rational seconds per tick."""

    numerator: int = 1
    denominator: int = 1000

    def __post_init__(self) -> None:
        _int(self.numerator, "time_base.numerator", minimum=1, maximum=U64_MAX)
        _int(self.denominator, "time_base.denominator", minimum=1, maximum=U64_MAX)
        if math.gcd(self.numerator, self.denominator) != 1:
            raise _fail("NON_REDUCED_TIME_BASE", "time base factors must be coprime")

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_dict(self) -> dict[str, str]:
        return {"numerator": str(self.numerator), "denominator": str(self.denominator)}

    @classmethod
    def from_dict(cls, value: Any) -> "TimeBase":
        item = _object(value, "time_base", ("numerator", "denominator"))
        return cls(_ticks(item["numerator"], "time_base.numerator"), _ticks(item["denominator"], "time_base.denominator"))


MILLISECONDS = TimeBase(1, 1000)


def rescale(ticks: int, source: TimeBase, target: TimeBase, rounding: str = "exact") -> int:
    """``ticks`` of ``source`` in ``target`` ticks: ``exact`` (an error when not integral), ``floor``, ``ceil`` or
    ``nearest`` (ties to even)."""
    value = Fraction(ticks) * source.seconds / target.seconds
    if value.denominator == 1:
        return value.numerator
    if rounding == "exact":
        raise _fail("INEXACT_RESCALE", f"{ticks} ticks of {source.numerator}/{source.denominator} s are not whole ticks")
    if rounding == "floor":
        return math.floor(value)
    if rounding == "ceil":
        return math.ceil(value)
    if rounding == "nearest":
        return round(value)
    raise _fail("INVALID_ROUNDING", f"unknown rounding {rounding!r}")


def to_milliseconds(ticks: int, time_base: TimeBase, rounding: str = "nearest") -> int:
    return rescale(ticks, time_base, MILLISECONDS, rounding)


def from_milliseconds(milliseconds: int, time_base: TimeBase, rounding: str = "nearest") -> int:
    return rescale(milliseconds, MILLISECONDS, time_base, rounding)


@dataclass(frozen=True)
class Span:
    """Half-open ``[start, end)`` in plan ticks; ``start == end`` only for a still's source point."""

    start: int
    end: int

    def __post_init__(self) -> None:
        _int(self.start, "span.start")
        _int(self.end, "span.end")
        if self.end < self.start:
            raise _fail("INVALID_SPAN", "a span ends before it starts")

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict[str, str]:
        return {"start": str(self.start), "end": str(self.end)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "Span":
        item = _object(value, label, ("start", "end"))
        return cls(_ticks(item["start"], f"{label}.start"), _ticks(item["end"], f"{label}.end"))


# ---------- options ----------

@dataclass(frozen=True)
class Options:
    """What the user picked: ``off`` (the default) keeps every workflow's behaviour as it was."""

    level: str = "off"
    style: str = "natural"
    # Lồng tiếng only (ADR-0055): a climax/teaser may be *suggested* for review; never on unless the user turns it on.
    enable_climax_hook: bool = False

    def __post_init__(self) -> None:
        _choice(self.level, "options.level", LEVELS)
        _choice(self.style, "options.style", STYLES)
        if type(self.enable_climax_hook) is not bool:
            raise _fail("INVALID_DOCUMENT", "options.enable_climax_hook must be a boolean")

    @property
    def enabled(self) -> bool:
        return self.level != "off"

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": OPTIONS_KIND, "level": self.level, "style": self.style,
                **({"enable_climax_hook": True} if self.enable_climax_hook else {})}

    @classmethod
    def from_dict(cls, value: Any) -> "Options":
        """A request's ``mastering`` value; absent (``None``) is ``off``."""
        if value is None:
            return cls()
        item = _object(value, "mastering_options", ("schema_version", "level"), ("kind", "style", "enable_climax_hook"))
        if type(item["schema_version"]) is not int or item["schema_version"] != SCHEMA_VERSION:
            raise _fail("UNSUPPORTED_VERSION", "mastering options must be schema_version 1")
        if "kind" in item and item["kind"] != OPTIONS_KIND:
            raise _fail("INVALID_DOCUMENT", "kind must be mastering_options")
        return cls(item["level"], item.get("style", "natural"), item.get("enable_climax_hook", False))


OFF = Options()


# ---------- plan ----------

@dataclass(frozen=True)
class Rights:
    status: str = "unknown"
    note: str = ""

    def __post_init__(self) -> None:
        _choice(self.status, "rights.status", RIGHTS)
        _text(self.note, "rights.note", empty=True)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "note": self.note}


@dataclass(frozen=True)
class Source:
    """One input the plan cuts from. A photo has ``duration == 0`` and only ``still`` segments."""

    source_id: str
    duration: int
    width: int
    height: int
    fps_numerator: int = 0
    fps_denominator: int = 1
    has_audio: bool = False
    rights: Rights = field(default_factory=Rights)
    origin_url: str | None = None
    credit: str | None = None
    identity: str | None = None

    def __post_init__(self) -> None:
        _ident(self.source_id, "source.source_id")
        _int(self.duration, "source.duration")
        _int(self.width, "source.width", minimum=1, maximum=65535)
        _int(self.height, "source.height", minimum=1, maximum=65535)
        _int(self.fps_numerator, "source.fps.numerator", maximum=1_000_000)
        _int(self.fps_denominator, "source.fps.denominator", minimum=1, maximum=1_000_000)
        if type(self.has_audio) is not bool:
            raise _fail("INVALID_DOCUMENT", "source.has_audio must be a boolean")
        if not isinstance(self.rights, Rights):
            raise _fail("INVALID_DOCUMENT", "source.rights must be Rights")
        for name in ("origin_url", "credit"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), f"source.{name}", limit=2048)
        if self.identity is not None and (type(self.identity) is not str or not _SHA256.match(self.identity)):
            raise _fail("INVALID_DIGEST", "source.identity must be a lowercase sha256")

    @property
    def is_still(self) -> bool:
        return self.duration == 0

    def to_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "duration": str(self.duration), "width": self.width, "height": self.height,
                "fps": {"numerator": self.fps_numerator, "denominator": self.fps_denominator}, "has_audio": self.has_audio,
                "rights": self.rights.to_dict(), "origin_url": self.origin_url, "credit": self.credit, "identity": self.identity}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "Source":
        item = _object(value, label, ("source_id", "duration", "width", "height", "fps", "has_audio", "rights"),
                       ("origin_url", "credit", "identity"))
        fps = _object(item["fps"], f"{label}.fps", ("numerator", "denominator"))
        rights = _object(item["rights"], f"{label}.rights", ("status",), ("note",))
        return cls(item["source_id"], _ticks(item["duration"], f"{label}.duration"), item["width"], item["height"],
                   fps["numerator"], fps["denominator"], item["has_audio"], Rights(rights["status"], rights.get("note", "")),
                   item.get("origin_url"), item.get("credit"), item.get("identity"))


@dataclass(frozen=True)
class Box:
    """A protected region (face, product, text, HUD) in basis points of the source picture."""

    x: int
    y: int
    w: int
    h: int
    label: str = "subject"

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            _int(getattr(self, name), f"box.{name}", maximum=BASIS)
        if self.w == 0 or self.h == 0 or self.x + self.w > BASIS or self.y + self.h > BASIS:
            raise _fail("INVALID_BOX", "a protected box must lie inside the picture")
        _choice(self.label, "box.label", ("subject", "face", "product", "text", "hud", "logo", "caption"))

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "label": self.label}


@dataclass(frozen=True)
class Framing:
    """A zoom from ``zoom_from`` to ``zoom_to`` (milli, 1000 = 100 %) around a centre, keeping ``protect`` in view."""

    zoom_from: int = 1000
    zoom_to: int = 1000
    center_x: int = BASIS // 2
    center_y: int = BASIS // 2
    protect: tuple[Box, ...] = ()

    def __post_init__(self) -> None:
        _int(self.zoom_from, "framing.zoom_from", minimum=MIN_ZOOM_MILLI, maximum=MAX_ZOOM_MILLI)
        _int(self.zoom_to, "framing.zoom_to", minimum=MIN_ZOOM_MILLI, maximum=MAX_ZOOM_MILLI)
        _int(self.center_x, "framing.center_x", maximum=BASIS)
        _int(self.center_y, "framing.center_y", maximum=BASIS)
        if type(self.protect) is not tuple or len(self.protect) > 32 or not all(isinstance(b, Box) for b in self.protect):
            raise _fail("INVALID_DOCUMENT", "framing.protect must be at most 32 boxes")

    @property
    def zooms(self) -> bool:
        return self.zoom_from != 1000 or self.zoom_to != 1000

    def to_dict(self) -> dict[str, Any]:
        return {"zoom_from": self.zoom_from, "zoom_to": self.zoom_to, "center_x": self.center_x, "center_y": self.center_y,
                "protect": [box.to_dict() for box in self.protect]}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "Framing":
        item = _object(value, label, ("zoom_from", "zoom_to", "center_x", "center_y"), ("protect",))
        protect = item.get("protect", [])
        if type(protect) is not list:
            raise _fail("INVALID_DOCUMENT", f"{label}.protect must be an array")
        boxes = tuple(Box(**_object(box, f"{label}.protect[{i}]", ("x", "y", "w", "h"), ("label",))) for i, box in enumerate(protect))
        return cls(item["zoom_from"], item["zoom_to"], item["center_x"], item["center_y"], boxes)


@dataclass(frozen=True)
class Segment:
    segment_id: str
    source_id: str
    source: Span
    timeline: Span
    role: str = "primary"
    audio_mode: str = "source"
    speech_refs: tuple[str, ...] = ()
    framing: Framing | None = None
    effects: tuple[str, ...] = ()
    confidence: int = 1000  # milli, 0–1000
    needs_review: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ident(self.segment_id, "segment.segment_id")
        _ident(self.source_id, "segment.source_id")
        if not isinstance(self.source, Span) or not isinstance(self.timeline, Span):
            raise _fail("INVALID_DOCUMENT", "segment spans must be Span")
        if self.timeline.length <= 0:
            raise _fail("EMPTY_SEGMENT", f"{self.segment_id} has no length on the timeline")
        _choice(self.role, "segment.role", ROLES)
        _choice(self.audio_mode, "segment.audio_mode", AUDIO_MODES)
        if self.role == "still":
            if self.source.length != 0:
                raise _fail("INVALID_STILL", f"{self.segment_id}: a still shows one source moment")
        elif self.source.length != self.timeline.length:
            # v1 has no retiming: a moving segment plays its footage at its own speed.
            raise _fail("RETIMED_SEGMENT", f"{self.segment_id}: source and timeline lengths differ")
        for name, limit in (("speech_refs", MAX_REFS), ("reasons", 16)):
            values = getattr(self, name)
            if type(values) is not tuple or len(values) > limit:
                raise _fail("INVALID_DOCUMENT", f"segment.{name} must be at most {limit} items")
        for ref in self.speech_refs:
            _ident(ref, "segment.speech_refs[]")
        for reason in self.reasons:
            _text(reason, "segment.reasons[]", limit=200)
        if self.framing is not None and not isinstance(self.framing, Framing):
            raise _fail("INVALID_DOCUMENT", "segment.framing must be Framing")
        if type(self.effects) is not tuple or len(self.effects) > 16 or len(set(self.effects)) != len(self.effects):
            raise _fail("INVALID_DOCUMENT", "segment.effects must be distinct, at most 16")
        for effect in self.effects:
            if effect in FORBIDDEN_EFFECTS:
                raise _fail("FORBIDDEN_EFFECT", f"{self.segment_id}: {effect} is never applied automatically")
            _choice(effect, "segment.effects[]", EFFECTS)
        _int(self.confidence, "segment.confidence_milli", maximum=1000)
        if type(self.needs_review) is not bool:
            raise _fail("INVALID_DOCUMENT", "segment.needs_review must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"segment_id": self.segment_id, "source_id": self.source_id, "source": self.source.to_dict(),
                "timeline": self.timeline.to_dict(), "role": self.role, "audio_mode": self.audio_mode,
                "speech_refs": list(self.speech_refs), "framing": None if self.framing is None else self.framing.to_dict(),
                "effects": list(self.effects), "confidence_milli": self.confidence, "needs_review": self.needs_review,
                "reasons": list(self.reasons)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "Segment":
        item = _object(value, label, ("segment_id", "source_id", "source", "timeline", "role", "audio_mode", "confidence_milli",
                                      "needs_review"), ("speech_refs", "framing", "effects", "reasons"))
        lists = {}
        for name in ("speech_refs", "effects", "reasons"):
            raw = item.get(name, [])
            if type(raw) is not list:
                raise _fail("INVALID_DOCUMENT", f"{label}.{name} must be an array")
            lists[name] = tuple(raw)
        framing = item.get("framing")
        return cls(item["segment_id"], item["source_id"], Span.from_dict(item["source"], f"{label}.source"),
                   Span.from_dict(item["timeline"], f"{label}.timeline"), item["role"], item["audio_mode"],
                   lists["speech_refs"], None if framing is None else Framing.from_dict(framing, f"{label}.framing"),
                   lists["effects"], item["confidence_milli"], item["needs_review"], lists["reasons"])


@dataclass(frozen=True)
class Provenance:
    planner: str
    planner_version: str
    recipe: str
    inputs: tuple[tuple[str, str], ...] = ()  # (name, sha256), sorted by name

    def __post_init__(self) -> None:
        for name in ("planner", "planner_version", "recipe"):
            _text(getattr(self, name), f"provenance.{name}", limit=200)
        if type(self.inputs) is not tuple or len(self.inputs) > 500:
            raise _fail("INVALID_DOCUMENT", "provenance.inputs must be at most 500 entries")
        names = [name for name, _ in self.inputs]
        if names != sorted(set(names)):
            raise _fail("INVALID_DOCUMENT", "provenance.inputs names must be distinct and sorted")
        for name, digest in self.inputs:
            _text(name, "provenance.inputs name", limit=200)
            if type(digest) is not str or not _SHA256.match(digest):
                raise _fail("INVALID_DIGEST", f"provenance input {name} must be a lowercase sha256")

    def to_dict(self) -> dict[str, Any]:
        return {"planner": self.planner, "planner_version": self.planner_version, "recipe": self.recipe,
                "inputs": {name: digest for name, digest in self.inputs}}

    @classmethod
    def from_dict(cls, value: Any) -> "Provenance":
        item = _object(value, "provenance", ("planner", "planner_version", "recipe", "inputs"))
        if type(item["inputs"]) is not dict:
            raise _fail("INVALID_DOCUMENT", "provenance.inputs must be an object")
        return cls(item["planner"], item["planner_version"], item["recipe"], tuple(sorted(item["inputs"].items())))


@dataclass(frozen=True)
class Plan:
    plan_id: str
    workflow: str
    options: Options
    duration: int
    sources: tuple[Source, ...]
    segments: tuple[Segment, ...]
    provenance: Provenance
    time_base: TimeBase = MILLISECONDS
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ident(self.plan_id, "plan_id")
        _choice(self.workflow, "workflow", WORKFLOWS)
        if not isinstance(self.options, Options) or not isinstance(self.time_base, TimeBase) or not isinstance(self.provenance, Provenance):
            raise _fail("INVALID_DOCUMENT", "options, time_base and provenance must be typed")
        _int(self.duration, "duration", minimum=1)
        if type(self.sources) is not tuple or not 1 <= len(self.sources) <= MAX_SOURCES:
            raise _fail("INVALID_DOCUMENT", f"a plan has 1 to {MAX_SOURCES} sources")
        if type(self.segments) is not tuple or not 1 <= len(self.segments) <= MAX_SEGMENTS:
            raise _fail("INVALID_DOCUMENT", f"a plan has 1 to {MAX_SEGMENTS} segments")
        if type(self.warnings) is not tuple or len(self.warnings) > MAX_WARNINGS:
            raise _fail("INVALID_DOCUMENT", "too many warnings")
        for warning in self.warnings:
            _text(warning, "warnings[]")
        sources: dict[str, Source] = {}
        for source in self.sources:
            if not isinstance(source, Source):
                raise _fail("INVALID_DOCUMENT", "sources must be Source")
            if source.source_id in sources:
                raise _fail("DUPLICATE_ID", f"source {source.source_id} appears twice")
            sources[source.source_id] = source
        seen: set[str] = set()
        at = 0
        for segment in self.segments:
            if not isinstance(segment, Segment):
                raise _fail("INVALID_DOCUMENT", "segments must be Segment")
            if segment.segment_id in seen:
                raise _fail("DUPLICATE_ID", f"segment {segment.segment_id} appears twice")
            seen.add(segment.segment_id)
            if segment.timeline.start != at:
                raise _fail("TIMELINE_GAP" if segment.timeline.start > at else "TIMELINE_OVERLAP",
                            f"{segment.segment_id} starts at {segment.timeline.start}, expected {at}")
            at = segment.timeline.end
            source = sources.get(segment.source_id)
            if source is None:
                raise _fail("UNKNOWN_SOURCE", f"{segment.segment_id} cuts from unknown {segment.source_id}")
            if source.is_still and segment.role != "still":
                raise _fail("INVALID_STILL", f"{segment.segment_id}: a photo can only be a still")
            if segment.source.end > source.duration:
                raise _fail("OUTSIDE_SOURCE", f"{segment.segment_id} ends after {segment.source_id} does")
        if at != self.duration:
            raise _fail("TIMELINE_GAP", f"the segments cover {at} of {self.duration} ticks")

    # ----- mapping -----

    def source(self, source_id: str) -> Source:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise _fail("UNKNOWN_SOURCE", source_id)

    def segment_at(self, tick: int) -> Segment:
        """The segment on screen at timeline ``tick`` (half-open: an end belongs to the next segment)."""
        if not 0 <= tick < self.duration:
            raise _fail("OUTSIDE_TIMELINE", f"{tick} is outside the timeline")
        low, high = 0, len(self.segments) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if self.segments[middle].timeline.start <= tick:
                low = middle
            else:
                high = middle - 1
        return self.segments[low]

    def to_source(self, tick: int) -> tuple[str, int]:
        """Timeline ``tick`` → (source id, source tick)."""
        segment = self.segment_at(tick)
        if segment.role == "still":
            return segment.source_id, segment.source.start
        return segment.source_id, segment.source.start + tick - segment.timeline.start

    def to_timeline(self, source_id: str, tick: int) -> list[int]:
        """Every timeline tick showing ``source_id`` at ``tick`` (footage may be used more than once, or not at all)."""
        return [segment.timeline.start + tick - segment.source.start for segment in self.segments
                if segment.source_id == source_id and segment.role != "still" and segment.source.start <= tick < segment.source.end]

    # ----- wire -----

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": PLAN_KIND, "plan_id": self.plan_id, "workflow": self.workflow,
                "options": {"level": self.options.level, "style": self.options.style,
                            **({"enable_climax_hook": True} if self.options.enable_climax_hook else {})}, "time_base": self.time_base.to_dict(),
                "duration": str(self.duration), "sources": [source.to_dict() for source in self.sources],
                "segments": [segment.to_dict() for segment in self.segments], "warnings": list(self.warnings),
                "provenance": self.provenance.to_dict()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1) + "\n"

    @classmethod
    def from_dict(cls, value: Any) -> "Plan":
        item = _object(value, "mastering_plan", ("schema_version", "kind", "plan_id", "workflow", "options", "time_base", "duration",
                                                 "sources", "segments", "warnings", "provenance"))
        if type(item["schema_version"]) is not int or item["schema_version"] != SCHEMA_VERSION:
            raise _fail("UNSUPPORTED_VERSION", "only mastering_plan schema_version 1 is understood")
        if item["kind"] != PLAN_KIND:
            raise _fail("INVALID_DOCUMENT", "kind must be mastering_plan")
        options = _object(item["options"], "options", ("level", "style"), ("enable_climax_hook",))
        for name in ("sources", "segments", "warnings"):
            if type(item[name]) is not list:
                raise _fail("INVALID_DOCUMENT", f"{name} must be an array")
        return cls(item["plan_id"], item["workflow"], Options(options["level"], options["style"], options.get("enable_climax_hook", False)),
                   _ticks(item["duration"], "duration"),
                   tuple(Source.from_dict(raw, f"sources[{i}]") for i, raw in enumerate(item["sources"])),
                   tuple(Segment.from_dict(raw, f"segments[{i}]") for i, raw in enumerate(item["segments"])),
                   Provenance.from_dict(item["provenance"]), TimeBase.from_dict(item["time_base"]), tuple(item["warnings"]))


# ---------- persistence ----------

def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("DUPLICATE_KEY", f"duplicate key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> Any:
    raise _fail("INVALID_NUMBER", f"{name} is not JSON")


def parse(text: str) -> Plan:
    if len(text.encode("utf-8")) > PLAN_LIMIT:
        raise _fail("TOO_LARGE", "the plan is too large")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except json.JSONDecodeError as error:
        raise _fail("INVALID_JSON", str(error)) from error
    return Plan.from_dict(value)


def save(path: Path, plan: Plan) -> None:
    """Write atomically: a crash leaves the old plan or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(plan.to_json())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def load(path: Path) -> Plan:
    try:
        if path.stat().st_size > PLAN_LIMIT:
            raise _fail("TOO_LARGE", "the plan is too large")
        return parse(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise _fail("UNREADABLE", f"cannot read {path.name}") from error


def load_matching(path: Path, *, workflow: str, options: Options, planner_version: str,
                  inputs: Mapping[str, str]) -> Plan | None:
    """The saved plan when it was made by this planner version from exactly these inputs and options, else None.

    A missing, unreadable, older-schema or mismatching plan is not an error: the caller plans again.
    """
    try:
        plan = load(path)
    except PlanError:
        return None
    if (plan.workflow != workflow or plan.options != options or plan.provenance.planner_version != planner_version
            or plan.provenance.inputs != tuple(sorted(inputs.items()))):
        return None
    return plan


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_json(value: Any) -> str:
    return digest_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


# Files up to this size are hashed whole; larger ones by FINGERPRINT_BLOCKS evenly spaced blocks.
FULL_HASH_LIMIT = 32 * 1024 * 1024
FINGERPRINT_BLOCKS = 16
FINGERPRINT_BLOCK = 64 * 1024
FINGERPRINT_RECIPE = "content-v1"
_FINGERPRINTS: dict[tuple[str, int, int], str] = {}


def content_fingerprint(path: Path) -> str:
    """A digest of what ``path`` holds, independent of its name and mtime.

    Small files are hashed whole. A larger file is hashed by its size and
    FINGERPRINT_BLOCKS blocks of 64 KiB spread from its first to its last byte
    (about 1 MiB read), which covers the container header, the index (moov or
    cues) and samples across the whole stream: a re-encode, trim, edit or
    replacement changes them even when the size and mtime were kept. The
    result is remembered per (path, size, mtime) for this process only.
    """
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key in _FINGERPRINTS:
        return _FINGERPRINTS[key]
    sha = hashlib.sha256(f"{FINGERPRINT_RECIPE}:{stat.st_size}:".encode())
    with path.open("rb") as source:
        if stat.st_size <= FULL_HASH_LIMIT:
            while chunk := source.read(1024 * 1024):
                sha.update(chunk)
        else:
            last = stat.st_size - FINGERPRINT_BLOCK
            for index in range(FINGERPRINT_BLOCKS):
                source.seek(last * index // (FINGERPRINT_BLOCKS - 1))
                sha.update(source.read(FINGERPRINT_BLOCK))
    _FINGERPRINTS[key] = digest = sha.hexdigest()
    return digest


def file_identity(path: Path, recipe: str = "") -> str:
    """The identity of a media file for caches and saved plans: its content fingerprint and the analysis recipe.

    Phase 1 used size + mtime, which keeps a stale cache when a file is replaced by one of the same size with
    its mtime restored (copy tools, sync clients, archives). The content fingerprint does not.
    """
    return digest_json({"content": content_fingerprint(path), "recipe": recipe})


def _frame_tick(frame: int, time_base: TimeBase, fps: Fraction) -> int:
    """The first plan tick inside frame ``frame`` (its exact start, rounded up to a whole tick)."""
    return math.ceil(Fraction(frame) / fps / time_base.seconds)


def frame_floor(ticks: int, time_base: TimeBase, fps: Fraction) -> int:
    """The first tick of the frame (at ``fps``) that ``ticks`` falls in: at or before ``ticks``, idempotent.

    A cut placed there shows that frame and none of the one before, also at 30000/1001 where frames do not
    start on whole milliseconds.
    """
    if fps <= 0:
        raise _fail("INVALID_FPS", "frame rate must be positive")
    return _frame_tick(math.floor(Fraction(ticks) * time_base.seconds * fps), time_base, fps)


def frame_ceil(ticks: int, time_base: TimeBase, fps: Fraction) -> int:
    """The first tick of the first frame starting at or after ``ticks`` (at or after ``ticks``, idempotent)."""
    if fps <= 0:
        raise _fail("INVALID_FPS", "frame rate must be positive")
    return _frame_tick(math.ceil(Fraction(ticks) * time_base.seconds * fps), time_base, fps)


def seconds_text(ticks: int, time_base: TimeBase) -> str:
    """``ticks`` as FFmpeg seconds with microsecond precision, computed exactly (no float in between)."""
    micro = rescale(ticks, time_base, TimeBase(1, 1_000_000), "nearest")
    sign = "-" if micro < 0 else ""
    whole, part = divmod(abs(micro), 1_000_000)
    return f"{sign}{whole}.{part:06d}"
