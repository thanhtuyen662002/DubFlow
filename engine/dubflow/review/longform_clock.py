"""Bounded consumer of an owned ffprobe decoded-frame XML stream.

Use frame=pts, XML fully_qualified=0, one explicitly selected video stream.
No process, file, network, media admission or checkpoint authority is granted.
Native execution must still contain the producer, verify its exit/diagnostics,
admit source/recipe bytes and checkpoint bounded clock windows. Seeking alone
cannot prove exact source coverage. No best-effort/FPS timestamp substitution.
"""
from __future__ import annotations

import hashlib
import time
from xml.parsers import expat

from .longform_plan import _decimal, _integer, plan_chunks
from .production import ReviewError

MAX_BLOCK_BYTES = 4096
MAX_TOKEN_BYTES = 8192
MAX_CLOCK_BYTES = 512 * 1024 * 1024
MAX_CLOCK_FRAMES = 1_000_000


class DecodedFrameClock:
    """SAX reader keeps only one bounded input block and a small element stack."""

    def __init__(self, *, max_bytes=16 * 1024 * 1024, max_frames=MAX_CLOCK_FRAMES,
                 timeout_seconds=30):
        self.max_bytes = _integer(max_bytes, "decoded-clock byte bound", 1, MAX_CLOCK_BYTES)
        self.max_frames = _integer(max_frames, "decoded-clock frame bound", 1, MAX_CLOCK_FRAMES)
        _integer(timeout_seconds, "decoded-clock deadline", 1, 180)
        self.deadline = time.monotonic() + timeout_seconds
        self._bytes = self._last_event = self._count = 0
        self._first = self._last = self._pending = None
        self._digest = hashlib.sha256()
        self._stack, self._points = [], []
        self._root = self._frames = self._closed = self._finished = self._failed = False
        self._parser = expat.ParserCreate()
        self._parser.StartElementHandler = self._start
        self._parser.EndElementHandler = self._end
        self._parser.CharacterDataHandler = self._text
        self._parser.XmlDeclHandler = self._declaration
        self._parser.StartDoctypeDeclHandler = self._forbidden
        self._parser.EntityDeclHandler = self._forbidden
        self._parser.ExternalEntityRefHandler = self._forbidden
        self._parser.ProcessingInstructionHandler = self._forbidden
        self._parser.StartCdataSectionHandler = self._forbidden
        self._parser.CommentHandler = self._forbidden
        self._parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def _check(self):
        if self._finished or self._failed:
            raise ReviewError("decoded clock is finished or refused; never reuse a partial reader")
        if time.monotonic() >= self.deadline:
            raise ReviewError("decoded frame-clock read exceeded its deadline")

    def _forbidden(self, *unused):
        raise ReviewError("decoded frame clock refuses declarations, entities and non-writer XML")

    def _declaration(self, version, encoding, standalone):
        if version != "1.0" or (encoding is not None and encoding.lower() != "utf-8"):
            raise ReviewError("decoded frame clock requires XML1.0 UTF8")

    def _start(self, name, attrs):
        self._last_event = self._parser.CurrentByteIndex
        if len(attrs) > 32 or any(len(k) > 128 or len(v.encode("utf-8")) > 2048 for k, v in attrs.items()):
            raise ReviewError("decoded frame-clock attributes exceed their bound")
        parent = self._stack[-1] if self._stack else None
        if parent is None:
            if self._root or name != "ffprobe":
                raise ReviewError("decoded clock needs one ffprobe root")
            self._root = True
        elif parent == "ffprobe":
            if name != "frames" or attrs or self._frames:
                raise ReviewError("decoded clock needs one frame section only")
            self._frames = True
        elif parent == "frames":
            if name != "frame" or set(attrs) != {"pts"}:
                raise ReviewError("decoded frame needs its exact source PTS; missing or inferred timestamps refuse")
            point = _decimal(attrs["pts"], "decoded source PTS")
            if self._count >= self.max_frames or (self._last is not None and point <= self._last):
                raise ReviewError("decoded frame clock exceeds its frame bound or presentation order")
            self._pending = point
        elif parent == "frame":
            if name != "side_data_list" or attrs:
                raise ReviewError("unexpected decoded frame metadata needs explicit compatibility")
        elif parent == "side_data_list":
            if name != "side_data":
                raise ReviewError("unexpected decoded side data needs explicit compatibility")
        else:
            raise ReviewError("decoded frame clock has unsupported nested data")
        self._stack.append(name)
        if len(self._stack) > 5:
            raise ReviewError("decoded frame-clock nesting exceeds its bound")

    def _end(self, name):
        self._last_event = self._parser.CurrentByteIndex
        if not self._stack or self._stack.pop() != name:
            raise ReviewError("decoded frame-clock sections do not match")
        if name == "frame":
            point = self._pending
            if point is None:
                raise ReviewError("decoded frame is missing its native integer PTS")
            if self._first is None:
                self._first = point
            self._count += 1
            self._last, self._pending = point, None
            self._digest.update((str(point) + "\n").encode("ascii"))
            self._points.append(point)
        elif name == "ffprobe":
            self._closed = True

    def _text(self, text):
        if text.strip():
            raise ReviewError("decoded frame clock contains unexpected text")

    def _parse(self, block, final):
        try:
            self._check()
            if type(block) is not bytes or len(block) > MAX_BLOCK_BYTES:
                raise ReviewError("decoded frame-clock input block exceeds its byte bound")
            self._bytes += len(block)
            if self._bytes > self.max_bytes or self._bytes - self._last_event > MAX_TOKEN_BYTES + MAX_BLOCK_BYTES:
                raise ReviewError("decoded frame-clock stream or unfinished XML token exceeds its byte bound")
            self._points = []
            self._parser.Parse(block, final)
            if self._bytes - self._last_event > MAX_TOKEN_BYTES:
                raise ReviewError("decoded frame-clock unfinished XML token exceeds its bound")
            self._check()
            return tuple(self._points)
        except (ReviewError, expat.ExpatError) as error:
            self._failed = True
            if isinstance(error, ReviewError):
                raise
            raise ReviewError("decoded frame clock is not a complete supported XML stream") from error

    def feed(self, block):
        return self._parse(block, False)

    def finish(self):
        points = self._parse(b"", True)
        if not self._root or not self._frames or not self._closed or self._stack or not self._count:
            self._failed = True
            raise ReviewError("decoded frame clock is incomplete or empty")
        self._finished = True
        return points

    @property
    def summary(self):
        if not self._finished or self._failed:
            raise ReviewError("only a complete decoded clock has a summary")
        return {"frame_count": str(self._count), "first_pts": str(self._first), "last_pts": str(self._last),
                "frame_clock_sha256": "sha256:" + self._digest.hexdigest()}


def iter_decoded_pts(blocks, *, max_bytes=16 * 1024 * 1024,
                     max_frames=MAX_CLOCK_FRAMES, timeout_seconds=30):
    """Yield source PTS incrementally; a truncated tail still refuses completion."""
    reader = DecodedFrameClock(max_bytes=max_bytes, max_frames=max_frames, timeout_seconds=timeout_seconds)
    for block in blocks:
        yield from reader.feed(block)
    yield from reader.finish()


def plan_decoded_clock(blocks, *, max_clock_bytes=16 * 1024 * 1024,
                       max_clock_frames=MAX_CLOCK_FRAMES, **plan_args):
    """Connect the bounded decoded stream to private planning, never admit media.

    A native caller still must verify process exit/diagnostics and exact source
    window coverage. ffprobe seek positions alone are not those boundaries.
    This call must not become one uncheckpointed full-longform decode phase.
    """
    points = iter_decoded_pts(blocks, max_bytes=max_clock_bytes, max_frames=max_clock_frames,
                              timeout_seconds=plan_args.get("timeout_seconds", 30))
    return plan_chunks(points, **plan_args)
