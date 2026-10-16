"""One isolated owned decoded-clock window; native owns retention and admission."""
from pathlib import Path
import sys

MAX_CONTEXT = 4 * 1024 * 1024
MAX_RESULT = 8 * 1024 * 1024 + 16384


def admit_context(raw, expected_hash):
    from .longform_plan import _closed, _decimal, _hash, _integer, encode_plan, plan_hash
    from .longform_windows import _anchor, _spec
    from .production import ReviewError
    from .render_execution import _path, _size
    from .wire import decode_review_json
    _hash(expected_hash)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CONTEXT or plan_hash(raw) != expected_hash:
        raise ReviewError("clock context differs from its native hash or byte bound")
    value = decode_review_json(raw.decode("utf-8"))
    _closed(value, {"schema_version","kind","clock_spec","ordinal","cursor","anchor","overlap",
        "source","source_root","ffprobe","media_root","processing_clock_recipe_sha256",
        "processing_release_sha256","packet_limit","timeout_seconds"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or
            value["kind"] != "review_native_clock_execution_context" or encode_plan(value) != raw):
        raise ReviewError("unknown or noncanonical clock execution context requires compatibility")
    start, end, clock = _spec(value["clock_spec"])
    cursor = _decimal(value["cursor"], "native source cursor")
    if not start <= cursor < end:
        raise ReviewError("clock execution cursor escapes its source interval")
    _integer(value["ordinal"], "native window ordinal", 1, 4096)
    _integer(value["packet_limit"], "native packet bound", 2, 16384)
    _integer(value["timeout_seconds"], "native clock duration", 1, 180)
    _hash(value["processing_clock_recipe_sha256"]); _hash(value["processing_release_sha256"])
    for name in ("source", "ffprobe"):
        _closed(value[name], {"path","sha256","size_bytes"})
        _hash(value[name]["sha256"]); _size(value[name]["size_bytes"])
        if not isinstance(value[name]["path"], str) or not 1 <= len(value[name]["path"]) <= 4096:
            raise ReviewError("clock execution needs a bounded native file locator")
        root_name = "source_root" if name == "source" else "media_root"
        root = value[root_name]
        if not isinstance(root, str) or not 1 <= len(root) <= 4096 or not _path(root).is_dir():
            raise ReviewError("clock execution requires existing native root directories")
        if name == "source":
            _path(value[name]["path"], root)
        else:
            # Inventoried owned executables may be deduplicated by packaging.
            # Source/context media retain their strict single-link checks.
            from engine.dubflow.media.adapter import _validate_executable
            _validate_executable(value[name]["path"], "ffprobe_path", trusted_root=_path(root))
    if (value["source"]["sha256"] != value["clock_spec"]["source_sha256"] or
            value["ffprobe"]["sha256"] != value["clock_spec"]["ffprobe_sha256"]):
        raise ReviewError("clock execution substitutes the native source or probe")
    overlap = value["overlap"]
    if not isinstance(overlap, list) or len(overlap) > 65536:
        raise ReviewError("clock execution lookahead exceeds its inventory")
    if value["anchor"] is None:
        if cursor != start or overlap:
            raise ReviewError("first decoded window cannot inherit a foreign prefix")
    else:
        if _anchor(value["anchor"])["pts"] != value["cursor"] or len(overlap) < 2:
            raise ReviewError("clock execution anchor differs from native prefix")
        previous, keys = None, 0
        for frame in overlap:
            _closed(frame, {"pts","key_frame","packet_pos","duration"})
            pts = _integer(frame["pts"], "native overlap PTS")
            key = _integer(frame["key_frame"], "native overlap key", 0, 1)
            _integer(frame["packet_pos"], "native overlap packet position", 0)
            if frame["duration"] is not None:
                _integer(frame["duration"], "native overlap duration", 1)
            if not cursor <= pts < end or (previous is not None and pts <= previous):
                raise ReviewError("native clock overlap loses exact source order")
            previous, keys = pts, keys + key
        if (overlap[0]["pts"] != cursor or overlap[0]["key_frame"] != 1 or keys < 2 or
                str(overlap[0]["packet_pos"]) != value["anchor"]["packet_pos"]):
            raise ReviewError("native clock overlap does not reopen the packet anchor")
    return value


def execute_context(raw, *, expected_hash, context_fence):
    from .longform_plan import _closed, encode_plan, plan_hash
    from .longform_windows import probe_window, read_window
    from .production import ReviewError
    from .wire import decode_review_json
    context = admit_context(raw, expected_hash)
    if not callable(context_fence):
        raise ReviewError("clock execution requires a native-context readback fence")
    context_fence()
    window = probe_window(spec=context["clock_spec"],
        ffprobe_path=Path(context["ffprobe"]["path"]), media_root=Path(context["media_root"]),
        executable_pin={k:context["ffprobe"][k] for k in ("sha256","size_bytes")},
        source_pin=context["source"], source_root=Path(context["source_root"]),
        cursor=int(context["cursor"]), anchor=context["anchor"], expected_overlap=context["overlap"],
        packet_limit=context["packet_limit"], timeout_seconds=context["timeout_seconds"])
    read_window(window, spec=context["clock_spec"], cursor=int(context["cursor"]), anchor=context["anchor"])
    context_fence()
    result = dict(schema_version=1, kind="review_private_clock_window_result",
        context_sha256=expected_hash, clock_spec_sha256=plan_hash(encode_plan(context["clock_spec"])),
        processing_clock_recipe_sha256=context["processing_clock_recipe_sha256"],
        ordinal=context["ordinal"], window_json=window.decode("utf-8"), window_sha256=plan_hash(window),
        native_decoded_clock_admitted=False, native_environment_admitted=False,
        semantic_qc_admitted=False, active_graph_published=False)
    # A nested canonical window string can double in byte size; result has a
    # distinct bounded envelope rather than widening the existing plan limit.
    import json
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False).encode()
    if len(encoded) > MAX_RESULT:
        raise ReviewError("clock execution result exceeds its bounded envelope")
    return encoded


def main(argv=None):
    import argparse
    import json
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("clock execution requires owned Python -I -S -B and native byte gate")
        if __package__ in (None, ""):
            sys.path.append(str(Path(sys.executable).parent / "Lib/site-packages"))
            sys.path.append(str(Path(__file__).resolve().parents[3]))
        from engine.dubflow.review import longform_clock_execution as implementation
        from engine.dubflow.review.longform_execution import _metadata
        from engine.dubflow.review.production import ReviewError
        pin = dict(path=str(args.context), sha256=args.context_sha256, size_bytes=str(args.context.stat().st_size))
        observed = {}
        raw = _metadata(pin, args.context.parent, MAX_CONTEXT, time.monotonic()+30, observed=observed)
        context = implementation.admit_context(raw, args.context_sha256)
        deadline = time.monotonic()+context["timeout_seconds"]
        def fence():
            if _metadata(pin,args.context.parent,MAX_CONTEXT,deadline,observed=observed) != raw:
                raise ReviewError("clock context changed before final result")
        result = implementation.execute_context(raw, expected_hash=args.context_sha256, context_fence=fence)
        fence()
        sys.stdout.buffer.write(result)
        return 0
    except Exception as error:
        message=str(error).encode("utf-8")[:1024].decode("utf-8",errors="ignore")
        sys.stderr.write(json.dumps({"code":"REVIEW_CLOCK_EXECUTION_INVALID","message":message},ensure_ascii=True)+"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
