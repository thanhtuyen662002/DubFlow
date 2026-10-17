"""Private source-clock render planning, without media or publication authority.

The admitted caller supplies decoded presentation timestamps and interval-local
visual dependencies. No frames are retained in memory. Whole request identity
binds a plan, while each chunk key excludes unrelated requests/cues and audio.
Actual checkpoint reuse still requires native physical, producer and QC fences.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time

from .production import ReviewError
from .wire import decode_review_json

MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_FRAMES_PER_CHUNK = 1_000_000
MAX_TICKS = (1 << 63) - 1
MIN_TICKS = -(1 << 63)
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|-?[1-9][0-9]{0,18})\Z")
_PLAN_FIELDS = {"schema_version", "kind", "recipe", "context_sha256", "source_sha256",
                "processing_recipe_sha256", "time_base", "source_interval", "policy",
                "frame_count", "frame_clock_sha256", "chunks"}
_CHUNK_FIELDS = {"start_tick", "end_tick", "frame_count", "frame_clock_sha256",
                 "visual_dependencies_sha256", "identity"}


def _integer(value, name, low=MIN_TICKS, high=MAX_TICKS):
    if type(value) is not int or not low <= value <= high:
        raise ReviewError(f"longform {name} must be a bounded native integer")
    return value


def _decimal(value, name, low=MIN_TICKS, high=MAX_TICKS):
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ReviewError(f"longform {name} must be a canonical decimal string")
    return _integer(int(value), name, low, high)


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ReviewError("longform identity requires a full SHA256 pin")
    return value


def _closed(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ReviewError("unknown longform fields require explicit compatibility")


def encode_plan(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 1 <= len(raw) <= MAX_PLAN_BYTES:
        raise ReviewError("longform plan exceeds its metadata byte bound")
    return raw


def plan_hash(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _clock(time_base, start_tick, end_tick):
    if not isinstance(time_base, tuple) or len(time_base) != 2:
        raise ReviewError("longform clock needs a reduced rational pair")
    numerator, denominator = (_integer(x, "time base", 1) for x in time_base)
    if math.gcd(numerator, denominator) != 1:
        raise ReviewError("longform time base must already be reduced")
    _integer(start_tick, "source start")
    _integer(end_tick, "source end")
    duration = _integer(end_tick - start_tick, "source duration", 1)
    if duration * numerator > 21600 * denominator:
        raise ReviewError("longform source interval must be positive and at most six hours")
    return {"numerator": str(numerator), "denominator": str(denominator)}


def _identity(chunk, source_sha256, processing_recipe_sha256, time_base):
    # A new current token or a changed cue elsewhere must not invalidate this
    # video chunk. Every local visual dependency (including styles/geometry/
    # cleanup/burn-in where applicable) belongs in the supplied dependency pin.
    return plan_hash(encode_plan({
        "schema_version": 1, "kind": "review_longform_video_chunk_identity",
        "source_sha256": source_sha256, "processing_recipe_sha256": processing_recipe_sha256,
        "time_base": time_base, **{k: v for k, v in chunk.items() if k != "identity"}}))


def plan_chunks(frame_pts, *, context_sha256, source_sha256, processing_recipe_sha256,
                time_base, start_tick, end_tick, target_ticks,
                visual_dependencies, max_frames=9000, max_chunks=MAX_CHUNKS,
                timeout_seconds=30):
    """Consume one ordered decoded PTS stream with O(chunks) bounded metadata.

    Intervals are half-open in the exact source time base. A split always starts
    at an actual next frame. target_ticks is a target, not a reason to split a
    sparse held frame or fabricate FPS. max_frames bounds frames per chunk.
    visual_dependencies(start, end) must be pure and return a native-admitted
    interval dependency hash; this planner does not admit captions or media.
    The caller must separately contain/timeout the process feeding frame_pts.
    """
    for value in (context_sha256, source_sha256, processing_recipe_sha256):
        _hash(value)
    clock = _clock(time_base, start_tick, end_tick)
    _integer(target_ticks, "target span", 1)
    _integer(max_frames, "frame limit", 1, MAX_FRAMES_PER_CHUNK)
    _integer(max_chunks, "chunk limit", 1, MAX_CHUNKS)
    _integer(timeout_seconds, "planning deadline", 1, 180)
    if not callable(visual_dependencies):
        raise ReviewError("longform planning requires admitted local visual dependencies")
    deadline = time.monotonic() + timeout_seconds
    chunks, total, count = [], 0, 0
    previous, first = None, start_tick
    clock_hash, chunk_hash = hashlib.sha256(), hashlib.sha256()

    def check_deadline():
        if time.monotonic() >= deadline:
            raise ReviewError("longform frame-clock planning exceeded its deadline")

    def finish(boundary):
        check_deadline()
        if len(chunks) >= max_chunks:
            raise ReviewError("longform frame-clock plan exceeds its chunk bound")
        chunk = {"start_tick": str(first), "end_tick": str(boundary), "frame_count": str(count),
                 "frame_clock_sha256": "sha256:" + chunk_hash.hexdigest(),
                 "visual_dependencies_sha256": _hash(visual_dependencies(first, boundary))}
        chunk["identity"] = _identity(chunk, source_sha256, processing_recipe_sha256, clock)
        chunks.append(chunk)
        check_deadline()

    for pts in frame_pts:
        check_deadline()
        _integer(pts, "decoded presentation timestamp")
        if not start_tick <= pts < end_tick or (previous is not None and pts <= previous):
            raise ReviewError("longform decoded PTS must be strictly ordered inside its source interval")
        if previous is None and pts != start_tick:
            raise ReviewError("longform first decoded PTS differs from admitted source start")
        if count and (count >= max_frames or pts - first >= target_ticks):
            finish(pts)
            first, count, chunk_hash = pts, 0, hashlib.sha256()
        encoded = (str(pts) + "\n").encode("ascii")
        clock_hash.update(encoded)
        chunk_hash.update(encoded)
        total, count, previous = total + 1, count + 1, pts
        _integer(total, "total frames", 1)
    if previous is None:
        raise ReviewError("longform decoded frame clock is empty")
    finish(end_tick)
    result = {"schema_version": 1, "kind": "review_longform_render_plan",
              "recipe": "review-source-pts-chunks-v1", "context_sha256": context_sha256,
              "source_sha256": source_sha256, "processing_recipe_sha256": processing_recipe_sha256,
              "time_base": clock, "source_interval": {"start_tick": str(start_tick), "end_tick": str(end_tick)},
              "policy": {"target_ticks": str(target_ticks), "max_frames": max_frames, "max_chunks": max_chunks},
              "frame_count": str(total), "frame_clock_sha256": "sha256:" + clock_hash.hexdigest(), "chunks": chunks}
    encode_plan(result)
    check_deadline()
    return result


def read_plan(raw, *, expected_hash, context_sha256, source_sha256, processing_recipe_sha256):
    """Validate trusted pinned plan bytes; never claim a file/media/QC admission."""
    for value in (expected_hash, context_sha256, source_sha256, processing_recipe_sha256):
        _hash(value)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PLAN_BYTES or plan_hash(raw) != expected_hash:
        raise ReviewError("longform plan bytes differ from their admitted pin or bound")
    try:
        plan = decode_review_json(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ReviewError("longform plan requires UTF8 JSON bytes") from error
    _closed(plan, _PLAN_FIELDS)
    if (type(plan["schema_version"]) is not int or plan["schema_version"] != 1 or
            plan["kind"] != "review_longform_render_plan" or plan["recipe"] != "review-source-pts-chunks-v1"):
        raise ReviewError("unknown longform format or recipe requires compatibility")
    for name, expected in (("context_sha256", context_sha256), ("source_sha256", source_sha256),
                           ("processing_recipe_sha256", processing_recipe_sha256)):
        if plan[name] != expected:
            raise ReviewError("longform plan substitutes its request, source or processing recipe")
    _closed(plan["time_base"], {"numerator", "denominator"})
    _closed(plan["source_interval"], {"start_tick", "end_tick"})
    base = tuple(_decimal(plan["time_base"][k], "time base", 1) for k in ("numerator", "denominator"))
    start = _decimal(plan["source_interval"]["start_tick"], "source start")
    end = _decimal(plan["source_interval"]["end_tick"], "source end")
    _clock(base, start, end)
    _closed(plan["policy"], {"target_ticks", "max_frames", "max_chunks"})
    policy = plan["policy"]
    _decimal(policy["target_ticks"], "target span", 1)
    _integer(policy["max_frames"], "frame limit", 1, MAX_FRAMES_PER_CHUNK)
    _integer(policy["max_chunks"], "chunk limit", 1, MAX_CHUNKS)
    _hash(plan["frame_clock_sha256"])
    frames = _decimal(plan["frame_count"], "total frames", 1)
    chunks = plan["chunks"]
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= policy["max_chunks"]:
        raise ReviewError("longform chunk inventory exceeds its bound")
    previous, total = start, 0
    for chunk in chunks:
        _closed(chunk, _CHUNK_FIELDS)
        left = _decimal(chunk["start_tick"], "chunk start")
        right = _decimal(chunk["end_tick"], "chunk end")
        count = _decimal(chunk["frame_count"], "chunk frames", 1, policy["max_frames"])
        if left != previous or not left < right <= end or count > right - left:
            raise ReviewError("longform chunk intervals or frame counts do not cover the source clock")
        for key in ("frame_clock_sha256", "visual_dependencies_sha256", "identity"):
            _hash(chunk[key])
        if chunk["identity"] != _identity(chunk, source_sha256, processing_recipe_sha256, plan["time_base"]):
            raise ReviewError("longform chunk key differs from its exact dependencies")
        previous, total = right, total + count
    if previous != end or total != frames or encode_plan(plan) != raw:
        raise ReviewError("longform plan loses source coverage, frame totals or canonical bytes")
    return plan
