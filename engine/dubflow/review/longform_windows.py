"""Bounded decoded clock windows. Native owns checkpoints and process containment.

Seek times locate a candidate anchor only. Exact decoded PTS/packet position
must reopen that anchor. A packet-limited run never grants EOF by itself.
Records are private observations, not media/QC/environment/publication grants.
"""
from __future__ import annotations

import time

from .longform_plan import (_clock, _closed, _decimal, _hash, _integer,
                            encode_plan, plan_chunks, plan_hash)
from .production import ReviewError
from .wire import decode_review_json

MAX_WINDOW_BYTES = 16 * 1024 * 1024
MAX_WINDOW_FRAMES = 65536
MAX_WINDOW_PACKETS = 16384
MAX_WINDOWS = 4096
RECIPE = "review-decoded-pts-keyframe-packet-window-v1"


def clock_spec(*, source_sha256, ffprobe_sha256, video_stream_index,
               time_base, start_tick, end_tick):
    return dict(schema_version=1, recipe=RECIPE,
        source_sha256=_hash(source_sha256), ffprobe_sha256=_hash(ffprobe_sha256),
        video_stream_index=_integer(video_stream_index, "video stream", 0, 1023),
        time_base=_clock(time_base, start_tick, end_tick),
        source_interval=dict(start_tick=str(start_tick), end_tick=str(end_tick)))


def _anchor(value):
    _closed(value, {"pts", "packet_pos"})
    _decimal(value["pts"], "anchor PTS")
    _decimal(value["packet_pos"], "anchor packet position", 0)
    return value


def _seek(anchor, clock):
    # FFprobe's seek parser uses signed64 microseconds. This is only an approximate
    # seek request, never canonical identity. No float conversion or PTS rounding.
    micros = _decimal(anchor["pts"], "anchor PTS") * clock[0] * 1_000_000 // clock[1]
    _integer(micros, "ffprobe seek microseconds")
    sign = "-" if micros < 0 else ""
    whole, fraction = divmod(abs(micros), 1_000_000)
    return f"{sign}{whole}.{fraction:06d}"


def _observation(raw, stream, packet_limit=None):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_WINDOW_BYTES:
        raise ReviewError("decoded clock window exceeds its byte inventory")
    value = decode_review_json(raw.decode("utf-8"))
    if not isinstance(value, dict) or set(value) - {"frames", "streams", "programs", "stream_groups"}:
        raise ReviewError("unknown decoded-window sections require compatibility")
    if not {"frames", "streams"} <= set(value):
        raise ReviewError("decoded window is missing frames or its selected stream")
    for name in ("programs", "stream_groups"):
        if name in value and value[name] != []:
            raise ReviewError("decoded window has unsupported program or stream groups")
    streams = value["streams"]
    if not isinstance(streams, list) or len(streams) != 1:
        raise ReviewError("decoded clock needs exactly its selected stream")
    _closed(streams[0], {"index", "nb_read_packets"})
    if type(streams[0]["index"]) is not int or streams[0]["index"] != stream:
        raise ReviewError("decoded window substituted the selected video stream")
    packets = _decimal(streams[0]["nb_read_packets"], "read packet count", 1)
    if packet_limit is not None and packets > packet_limit:
        raise ReviewError("decoded clock exceeded its selected packet inventory")
    frames = value["frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_WINDOW_FRAMES:
        raise ReviewError("decoded clock frame inventory is empty or oversized")
    result, previous = [], None
    for frame in frames:
        if not isinstance(frame, dict) or not {"pts", "key_frame", "pkt_pos"} <= set(frame):
            raise ReviewError("decoded window needs exact PTS/keyframe/packet positions")
        if set(frame) - {"pts", "key_frame", "pkt_pos", "duration", "side_data_list"}:
            raise ReviewError("unknown decoded frame fields require compatibility")
        pts = _integer(frame["pts"], "decoded PTS")
        key = _integer(frame["key_frame"], "decoded keyframe", 0, 1)
        position = _decimal(frame["pkt_pos"], "decoded packet position", 0)
        duration = frame.get("duration")
        if duration is not None:
            _integer(duration, "decoded frame duration", 1)
        side = frame.get("side_data_list", [])
        if not isinstance(side, list) or len(side) > 32:
            raise ReviewError("decoded side data exceeds its inventory")
        for item in side:
            _closed(item, {"side_data_type"})
            if not isinstance(item["side_data_type"], str) or len(item["side_data_type"].encode()) > 2048:
                raise ReviewError("decoded side-data label exceeds its bound")
        if previous is not None and pts <= previous:
            raise ReviewError("decoded window PTS are not strictly ordered")
        result.append(dict(pts=pts, key_frame=key, packet_pos=position, duration=duration))
        previous = pts
    return dict(packets=packets, frames=result)


def _spec(value):
    _closed(value, {"schema_version", "recipe", "source_sha256", "ffprobe_sha256",
                    "video_stream_index", "time_base", "source_interval"})
    _closed(value["time_base"], {"numerator", "denominator"})
    _closed(value["source_interval"], {"start_tick", "end_tick"})
    start, end = (_decimal(value["source_interval"][k], k) for k in ("start_tick", "end_tick"))
    clock = tuple(_decimal(value["time_base"][k], k, 1) for k in ("numerator", "denominator"))
    if type(value["schema_version"]) is not int or clock_spec(
            source_sha256=value["source_sha256"], ffprobe_sha256=value["ffprobe_sha256"],
            video_stream_index=value["video_stream_index"], time_base=clock,
            start_tick=start, end_tick=end) != value:
        raise ReviewError("unknown decoded-clock specification requires compatibility")
    return start, end, clock


def read_window(raw, *, spec, cursor, anchor):
    """Validate immutable record bytes; caller must separately admit their producer."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_WINDOW_BYTES:
        raise ReviewError("clock checkpoint exceeds its byte inventory")
    _spec(spec)
    value = decode_review_json(raw.decode("utf-8"))
    _closed(value, {"schema_version", "kind", "clock_spec_sha256", "start_tick", "end_tick",
                    "anchor", "next_anchor", "complete", "pts", "frame_clock_sha256",
                    "observation_sha256", "overlap", "native_media_admitted", "semantic_qc_admitted"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or
            value["kind"] != "review_decoded_clock_window" or
            value["clock_spec_sha256"] != plan_hash(encode_plan(spec)) or
            value["native_media_admitted"] is not False or value["semantic_qc_admitted"] is not False or
            value["anchor"] != anchor or type(value["complete"]) is not bool or encode_plan(value) != raw):
        raise ReviewError("clock checkpoint changed format, exact bytes, source or anchor")
    source_start = _decimal(spec["source_interval"]["start_tick"], "source start")
    source_end = _decimal(spec["source_interval"]["end_tick"], "source end")
    if not source_start <= cursor < source_end or _decimal(value["start_tick"], "window start") != cursor:
        raise ReviewError("clock checkpoint forks or loses its source prefix")
    end = _decimal(value["end_tick"], "window end")
    if not cursor < end <= source_end:
        raise ReviewError("clock checkpoint has an invalid source interval")
    if anchor is not None and _decimal(_anchor(anchor)["pts"], "anchor PTS") != cursor:
        raise ReviewError("clock checkpoint anchor differs from its source cursor")
    if value["complete"]:
        if end != source_end or value["next_anchor"] is not None or value["overlap"]:
            raise ReviewError("complete clock checkpoint loses exact source end")
    else:
        next_anchor = _anchor(value["next_anchor"])
        if end >= source_end or _decimal(next_anchor["pts"], "next anchor PTS") != end:
            raise ReviewError("clock checkpoint has no exact advancing next anchor")
        overlap = value["overlap"]
        if not isinstance(overlap, list) or not 2 <= len(overlap) <= MAX_WINDOW_FRAMES:
            raise ReviewError("clock checkpoint needs bounded decoded lookahead")
        prior = end - 1
        for frame in overlap:
            _closed(frame, {"pts", "key_frame", "packet_pos", "duration"})
            pts = _integer(frame["pts"], "lookahead PTS")
            _integer(frame["key_frame"], "lookahead keyframe", 0, 1)
            _integer(frame["packet_pos"], "lookahead packet position", 0)
            if frame["duration"] is not None:
                _integer(frame["duration"], "lookahead duration", 1)
            if not prior < pts < source_end:
                raise ReviewError("clock checkpoint lookahead loses PTS order")
            prior = pts
        if (overlap[0]["pts"] != end or overlap[0]["key_frame"] != 1 or
                str(overlap[0]["packet_pos"]) != next_anchor["packet_pos"] or
                sum(f["key_frame"] for f in overlap) < 2):
            raise ReviewError("clock checkpoint lacks a full keyframe lookahead fence")
    points = value["pts"]
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_WINDOW_FRAMES:
        raise ReviewError("clock checkpoint point inventory is invalid")
    parsed = [_decimal(p, "clock point") for p in points]
    if parsed[0] != cursor or any(not cursor <= p < end for p in parsed) or any(a >= b for a, b in zip(parsed, parsed[1:])):
        raise ReviewError("clock checkpoint has missing, duplicate or foreign PTS")
    digest = plan_hash("".join(p + "\n" for p in points).encode("ascii"))
    if _hash(value["frame_clock_sha256"]) != digest:
        raise ReviewError("clock checkpoint frame-clock hash changed")
    _hash(value["observation_sha256"])
    return value


def probe_window(*, spec, ffprobe_path, media_root, executable_pin, source_pin,
                 source_root, cursor, anchor, packet_limit=4096,
                 timeout_seconds=180, cancelled=lambda: False, expected_overlap=None):
    """Decode one bounded candidate and prove only its exact progressing window."""
    from engine.dubflow.media.adapter import _validate_executable
    from .longform_probe import _consume_process
    from .render_execution import _closed as file_closed, _file_hash, _path, _size, _verify

    _integer(packet_limit, "window packet count", 2, MAX_WINDOW_PACKETS)
    _integer(timeout_seconds, "window deadline", 1, 180)
    if not callable(cancelled):
        raise ReviewError("decoded window needs native cancellation observation")
    start, end, clock = _spec(spec)
    if clock_spec(source_sha256=source_pin["sha256"], ffprobe_sha256=executable_pin["sha256"],
            video_stream_index=spec["video_stream_index"], time_base=clock,
            start_tick=start, end_tick=end) != spec:
        raise ReviewError("decoded window substitutes its source or executable specification")
    if anchor is None:
        if cursor != start:
            raise ReviewError("a resumed clock window requires its observed packet anchor")
        seek = ""
    else:
        if _decimal(_anchor(anchor)["pts"], "anchor PTS") != cursor:
            raise ReviewError("decoded window anchor does not match its source cursor")
        seek = _seek(anchor, clock)
    deadline = time.monotonic() + timeout_seconds
    root = _path(str(media_root))
    executable = _validate_executable(ffprobe_path, "ffprobe_path", trusted_root=root)
    file_closed(executable_pin, ("sha256", "size_bytes"))
    expected_executable = (_hash(executable_pin["sha256"]), _size(executable_pin["size_bytes"]))

    def check():
        if cancelled() or time.monotonic() >= deadline:
            raise ReviewError("decoded window cancelled or exhausted its deadline")
        if _file_hash(executable, deadline, inventory_file=True) != expected_executable:
            raise ReviewError("decoded window executable changed its admitted bytes")
        return _verify(source_pin, source_root, deadline)

    source = check()

    def execute(limit):
        interval = seek + "%" + (f"+#{limit}" if limit is not None else "")
        argv = (str(executable), "-v", "error", "-threads", "2", "-select_streams",
                str(spec["video_stream_index"]), "-read_intervals", interval,
                "-show_frames", "-count_packets", "-show_streams", "-show_entries",
                "frame=pts,key_frame,pkt_pos,duration:frame_side_data=side_data_type:"
                "stream=index,nb_read_packets:stream_disposition=:stream_tags=",
                "-of", "json=string_validation=fail", "-i", str(source))
        observed = _consume_process(argv,
            lambda blocks: _observation(b"".join(blocks), spec["video_stream_index"], limit),
            deadline=deadline, max_stdout_bytes=MAX_WINDOW_BYTES, cancelled=cancelled)
        if check() != source:
            raise ReviewError("decoded window source locator changed")
        return observed

    observed = execute(packet_limit)
    # A short packet-limited run only suggests EOF. Independently run without an
    # interval end, under the SAME byte/frame/absolute-time bounds and exact pins.
    complete = observed["packets"] < packet_limit
    if complete and execute(None) != observed:
        raise ReviewError("decoded window EOF observation differs from its bounded candidate")
    frames = observed["frames"]
    if anchor is not None:
        selected = [f for f in frames if f["pts"] >= cursor]
        if (not selected or selected[0]["pts"] != cursor or selected[0]["key_frame"] != 1 or
                selected[0]["packet_pos"] != _decimal(anchor["packet_pos"], "anchor packet position", 0)):
            raise ReviewError("seek did not reopen the exact decoded packet anchor")
        frames = selected
    if expected_overlap is not None and frames[:len(expected_overlap)] != expected_overlap:
        raise ReviewError("decoded window does not reproduce its saved exact overlap")
    if frames[0]["pts"] != cursor or any(not start <= f["pts"] < end for f in frames):
        raise ReviewError("decoded window does not start at its admitted source PTS")
    if complete:
        last = frames[-1]
        if last["duration"] is None or last["pts"] + last["duration"] != end:
            raise ReviewError("decoded EOF duration does not prove the archived exact source end")
        boundary, next_anchor, kept, overlap = end, None, frames, []
    else:
        boundaries = [f for f in frames if f["key_frame"] == 1 and f["pts"] > cursor]
        if len(boundaries) < 3:
            raise ReviewError("bounded decoded window needs three advancing keyframes; explicit compatibility needed")
        # The last keyframe may have been flushed before its preceding B frames
        # were even read. Keep a complete guard GOP BEFORE that truncated tail.
        last, guard = boundaries[-3], boundaries[-2]
        boundary = last["pts"]
        next_anchor = dict(pts=str(boundary), packet_pos=str(last["packet_pos"]))
        kept = [f for f in frames if f["pts"] < boundary]
        overlap = [f for f in frames if boundary <= f["pts"] <= guard["pts"]]
    value = dict(schema_version=1, kind="review_decoded_clock_window",
        clock_spec_sha256=plan_hash(encode_plan(spec)), start_tick=str(cursor), end_tick=str(boundary),
        anchor=anchor, next_anchor=next_anchor, complete=complete,
        pts=[str(f["pts"]) for f in kept],
        frame_clock_sha256=plan_hash("".join(str(f["pts"])+"\n" for f in kept).encode("ascii")),
        observation_sha256=plan_hash(encode_plan(observed)),
        overlap=overlap,
        native_media_admitted=False, semantic_qc_admitted=False)
    raw = encode_plan(value)
    read_window(raw, spec=spec, cursor=cursor, anchor=anchor)
    check()
    return raw


def resume_plan(*, spec, read_checkpoint, checkpoint, fence, max_new_windows=1,
                context_sha256, processing_recipe_sha256, target_ticks, visual_dependencies,
                probe_args, max_frames=9000, max_chunks=MAX_WINDOWS,
                timeout_seconds=180, cancelled=lambda: False):
    """Reopen native-owned prefix, persist bounded work, then plan from its PTS.

    Callbacks perform native physical/capacity/process/current-source admission.
    This worker does not write files, durable SQLite or publication pointers.
    """
    _integer(max_new_windows, "new window bound", 1, MAX_WINDOWS)
    _integer(timeout_seconds, "planning deadline", 1, 180)
    for callback in (read_checkpoint, checkpoint, fence, cancelled):
        if not callable(callback):
            raise ReviewError("resumable clock needs native read/checkpoint/fence/cancel callbacks")
    cursor, source_end, unused_clock = _spec(spec)
    anchor, overlap, records, new = None, None, [], 0
    total_bytes, total_frames = 0, 0
    deadline = time.monotonic() + timeout_seconds
    terminal = False

    def guard():
        if cancelled() or time.monotonic() >= deadline:
            raise ReviewError("resumable decoded clock was cancelled or exceeded its deadline")
        fence()
        if cancelled() or time.monotonic() >= deadline:
            raise ReviewError("resumable decoded clock fence exhausted its deadline")

    for ordinal in range(1, MAX_WINDOWS+1):
        guard()
        raw = read_checkpoint(ordinal)
        if raw is None:
            if new >= max_new_windows:
                break
            raw = probe_window(spec=spec, cursor=cursor, anchor=anchor,
                               expected_overlap=overlap, cancelled=cancelled,
                               timeout_seconds=max(1, int(deadline-time.monotonic())), **probe_args)
            guard()
            # No progress is exposed until the native callback returns the exact
            # independently reopened immutable bytes. Refusal preserves prefix.
            if checkpoint(ordinal, raw) != raw:
                raise ReviewError("native decoded-clock checkpoint readback changed its bytes")
            new += 1
        value = read_window(raw, spec=spec, cursor=cursor, anchor=anchor)
        if overlap:
            observed_pts = [int(p) for p in value["pts"]] + [f["pts"] for f in value["overlap"]]
            if observed_pts[:len(overlap)] != [f["pts"] for f in overlap]:
                raise ReviewError("saved decoded-clock windows disagree across their overlap")
        total_bytes += len(raw)
        total_frames += len(value["pts"])
        if total_bytes > MAX_WINDOW_BYTES or total_frames > 1_000_000:
            raise ReviewError("decoded-clock journal exceeds its aggregate metadata/frame bound")
        records.append(plan_hash(raw))
        cursor = int(value["end_tick"])
        anchor = value["next_anchor"]
        overlap = value["overlap"]
        guard()
        if value["complete"]:
            if read_checkpoint(ordinal+1) is not None:
                raise ReviewError("decoded-clock checkpoint journal contains a foreign suffix")
            terminal = True
            break
    else:
        raise ReviewError("decoded-clock window journal exceeds its finite bound")
    result = dict(complete=terminal, clock_spec_sha256=plan_hash(encode_plan(spec)),
                  window_count=len(records), new_windows=new, next_tick=str(cursor),
                  next_anchor=anchor, plan=None)
    if terminal:
        # Planning reads already admitted immutable records, not the decoder.
        # The native callback reopens each record again, refusing changed bytes.
        def points():
            point_cursor, point_anchor = int(spec["source_interval"]["start_tick"]), None
            for ordinal, expected in enumerate(records, 1):
                guard()
                raw = read_checkpoint(ordinal)
                if not isinstance(raw, bytes) or plan_hash(raw) != expected:
                    raise ReviewError("decoded-clock checkpoint changed before planning")
                value = read_window(raw, spec=spec, cursor=point_cursor, anchor=point_anchor)
                yield from (int(v) for v in value["pts"])
                point_cursor, point_anchor = int(value["end_tick"]), value["next_anchor"]
            guard()
        result["plan"] = plan_chunks(points(), context_sha256=context_sha256,
            source_sha256=spec["source_sha256"], processing_recipe_sha256=processing_recipe_sha256,
            time_base=tuple(int(spec["time_base"][k]) for k in ("numerator", "denominator")),
            start_tick=int(spec["source_interval"]["start_tick"]),
            end_tick=int(spec["source_interval"]["end_tick"]), target_ticks=target_ticks,
            visual_dependencies=visual_dependencies, max_frames=max_frames,
            max_chunks=max_chunks, timeout_seconds=timeout_seconds)
    guard()
    return result
