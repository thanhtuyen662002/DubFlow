"""Private bounded ffprobe clock execution; no render/checkpoint publication.

The native caller must admit the processing release and contain this Python
worker and every descendant. This module rechecks concrete source/executable
pins, streams bounded pipes and rejects partial or failed probe observations.
It grants no compatibility, original producer repin or durable writer authority.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .longform_clock import MAX_BLOCK_BYTES, plan_decoded_clock
from .longform_plan import _hash, _integer
from .production import ReviewError

DIAGNOSTIC_BYTES = 4096
PIPE_QUEUE_BLOCKS = 16


def _consume_process(argv, consume, *, deadline, max_stdout_bytes, cancelled):
    """Bounded pipe kernel. Tests use an app-owned Python fixture, never a shell."""
    from engine.dubflow.security import build_subprocess_plan

    def check():
        if cancelled():
            raise ReviewError("decoded clock probe was cancelled; partial observations are not reusable")
        if time.monotonic() >= deadline:
            raise ReviewError("decoded clock probe exceeded its bounded deadline")

    check()
    command = build_subprocess_plan(argv[0], argv[1:])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        child = subprocess.Popen(command.argv, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    except OSError as error:
        raise ReviewError("owned decoded-clock probe could not start") from error
    events, stop, workers = queue.Queue(maxsize=PIPE_QUEUE_BLOCKS), threading.Event(), []
    diagnostic = bytearray()

    def send(event):
        while not stop.is_set():
            try:
                events.put(event, timeout=0.05)
                return
            except queue.Full:
                pass

    def read_pipe(stream, label):
        count = 0
        try:
            while not stop.is_set():
                data = stream.read1(MAX_BLOCK_BYTES)
                if not data:
                    break
                count += len(data)
                limit = max_stdout_bytes if label == "stdout" else DIAGNOSTIC_BYTES
                if count > limit:
                    send(("error", label + " exceeds its bounded byte inventory"))
                    return
                if label == "stdout":
                    send(("data", data))
                else:
                    diagnostic.extend(data)
            send(("end", label))
        except Exception:
            send(("error", label + " pipe could not be read completely"))

    exhausted = False

    def blocks():
        nonlocal exhausted
        ended = set()
        while len(ended) < 2:
            check()
            try:
                kind, value = events.get(timeout=min(0.05, max(0.001, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if kind == "error":
                raise ReviewError("decoded clock probe " + value)
            if kind == "end":
                ended.add(value)
            else:
                yield value
        check()
        exhausted = True

    try:
        for label, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            thread = threading.Thread(target=read_pipe, args=(stream, label),
                                      name="review-clock-" + label, daemon=True)
            thread.start()
            workers.append(thread)
        result = consume(blocks())
        if not exhausted:
            raise ReviewError("decoded clock consumer did not validate the complete pipe inventory")
        check()
        try:
            code = child.wait(timeout=max(0.001, deadline-time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise ReviewError("decoded clock probe exceeded its bounded deadline after pipe EOF") from error
        check()
        # With -v error, any stderr means decoding was not a clean observation,
        # even when ffprobe returns zero. No warning normalization or partial use.
        if code != 0 or diagnostic:
            raise ReviewError("decoded clock probe failed or emitted decode diagnostics")
        return result
    finally:
        stop.set()
        cleanup_deadline = time.monotonic() + 2.0
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=max(0.001, cleanup_deadline-time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise ReviewError("decoded clock child did not reap; native containment must recover") from error
        for worker in workers:
            worker.join(timeout=max(0.0, cleanup_deadline-time.monotonic()))
        if any(w.is_alive() for w in workers):
            # Never block closing a pipe still owned by a reader. Native worker
            # containment must kill inherited handles; this path cannot succeed.
            raise ReviewError("decoded clock readers did not join; native containment must recover")
        child.stdout.close()
        child.stderr.close()


def probe_plan(*, ffprobe_path, media_root, executable_pin, source_pin, source_root,
               video_stream_index, context_sha256, processing_recipe_sha256,
               time_base, start_tick, end_tick, target_ticks, visual_dependencies,
               max_frames=9000, max_chunks=4096, max_clock_bytes=16*1024*1024,
               max_clock_frames=1_000_000, timeout_seconds=30, cancelled=lambda: False):
    """Plan one admitted bounded clock window, with before/after physical pins.

    This first process adapter performs no approximate seek. Native window/index
    admission must be connected before longform activation; running one full
    six-hour decode under this existing bounded deadline is not that solution.
    """
    from engine.dubflow.media.adapter import _validate_executable
    from .render_execution import _closed, _file_hash, _path, _size, _verify
    from .longform_clock import MAX_CLOCK_BYTES, MAX_CLOCK_FRAMES
    _integer(timeout_seconds, "probe deadline", 1, 180)
    _integer(video_stream_index, "video stream index", 0, 1023)
    _integer(max_clock_bytes, "probe stdout bound", 1, MAX_CLOCK_BYTES)
    _integer(max_clock_frames, "probe frame bound", 1, MAX_CLOCK_FRAMES)
    if not callable(cancelled):
        raise ReviewError("decoded clock cancellation needs a native observation callback")
    deadline = time.monotonic() + timeout_seconds
    root = _path(str(media_root))
    executable = _validate_executable(ffprobe_path, "ffprobe_path", trusted_root=root)
    _closed(executable_pin, ("sha256", "size_bytes"))
    _hash(executable_pin["sha256"])
    expected_executable = (executable_pin["sha256"], _size(executable_pin["size_bytes"]))

    def check_pins():
        if cancelled() or time.monotonic() >= deadline:
            raise ReviewError("decoded clock probe cancelled or exceeded admission deadline")
        if _file_hash(executable, deadline, inventory_file=True) != expected_executable:
            raise ReviewError("decoded clock executable differs from its admitted inventory")
        return _verify(source_pin, source_root, deadline)

    source = check_pins()
    argv = (str(executable), "-v", "error", "-threads", "2", "-select_streams", str(video_stream_index),
            "-show_frames", "-show_entries", "frame=pts:frame_side_data=side_data_type",
            "-of", "xml=fully_qualified=0:string_validation=fail", "-i", str(source))

    def consume(blocks):
        return plan_decoded_clock(blocks, max_clock_bytes=max_clock_bytes,
            max_clock_frames=max_clock_frames, context_sha256=context_sha256,
            source_sha256=source_pin["sha256"], processing_recipe_sha256=processing_recipe_sha256,
            time_base=time_base, start_tick=start_tick, end_tick=end_tick, target_ticks=target_ticks,
            visual_dependencies=visual_dependencies, max_frames=max_frames, max_chunks=max_chunks,
            timeout_seconds=timeout_seconds)

    result = _consume_process(argv, consume, deadline=deadline,
                              max_stdout_bytes=max_clock_bytes, cancelled=cancelled)
    if check_pins() != source:
        raise ReviewError("decoded clock source locator changed during observation")
    return result
