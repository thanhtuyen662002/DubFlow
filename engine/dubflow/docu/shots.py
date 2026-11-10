"""Shot boundaries of a source with FFmpeg's ``scdet`` (ADR-0049, phase 2b).

``scdet`` ships with the bundled LGPL FFmpeg, so no OpenCV is needed. Frames
are scaled down first: a cut is as visible at 320 pixels wide and the scan runs
at about 30× real time.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable, Sequence

from .project import DocuError, write_json

# 8.5: a hard cut between two similar grey interiors scored 9.7 (Hubei, Tier-3) and was missed at 10.
THRESHOLD = 8.5
ANALYSIS_WIDTH = 320
# Cuts closer than this to the previous one are one transition (a dissolve flickers several frames).
# Cuts closer than this are one flicker (a flash, a double detection). A real 0.3 s shot keeps both of its cuts, so
# no slice is planned across it (Tier-3).
MIN_SHOT = 0.15
RECIPE = "scdet-320-v5"
# Gradual changes scdet misses (a whip pan, a dissolve; Tier-3): tiny grey thumbnails at SLOW_FPS. At each sample
# the picture SLOW_SPAN samples before is compared with the one SLOW_SPAN after (mean difference, % of full scale).
# It is a transition when that change is big (SLOW_MIN) and the picture was steady on both sides: SLOW_RATIO times
# the change within the span just before and the span just after. Walking along a corridor changes the picture all
# the time, so it is not cut; a whip from one view to another is.
SLOW_FPS, SLOW_SPAN, SLOW_W, SLOW_H = 5, 3, 32, 18
# A hard cut also changes the picture between two steady sides: a stretch holding one is already cut there and is left
# as it is, or every hard cut would cost two seconds of footage.
SLOW_MIN, SLOW_RATIO = 10.0, 2.5
_TIME = re.compile(r"lavfi\.scd\.time=([0-9]+(?:\.[0-9]+)?)")

Runner = Callable[[list[str], Path, Callable[[], bool]], None]


def parse(text: str, duration: float) -> list[float]:
    """Cut times from ``metadata=print`` output, inside the video, in order, without flicker."""
    cuts: list[float] = []
    for match in _TIME.finditer(text):
        time = round(float(match.group(1)), 3)
        if MIN_SHOT <= time <= duration - MIN_SHOT and (not cuts or time - cuts[-1] >= MIN_SHOT):
            cuts.append(time)
    return cuts


def changes(raw: bytes) -> list[tuple[float, float]]:
    """(time, change) for each thumbnail pair SLOW_SPAN samples apart, timed at the pair's middle."""
    size = SLOW_W * SLOW_H
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    out = []
    for i in range(len(frames) - SLOW_SPAN):
        a, b = frames[i], frames[i + SLOW_SPAN]
        out.append(((i + SLOW_SPAN / 2) / SLOW_FPS, sum(abs(x - y) for x, y in zip(a, b)) / size / 2.55))
    return out


def _frames(raw: bytes) -> list[bytes]:
    size = SLOW_W * SLOW_H
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def _difference(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) / 2.55


def transitions(raw: bytes, *, minimum: float = SLOW_MIN, ratio: float = SLOW_RATIO,
                span: int = SLOW_SPAN) -> list[tuple[float, float, float]]:
    """(time, change across, steadiest side's change) at each sample whose picture turns into another one there."""
    frames = _frames(raw)
    found = []
    for i in range(2 * span, len(frames) - 2 * span):
        across = _difference(frames[i - span], frames[i + span])
        if across <= minimum:
            continue
        sides = max(_difference(frames[i - 2 * span], frames[i - span]), _difference(frames[i + span], frames[i + 2 * span]))
        if across > ratio * sides:
            found.append((i / SLOW_FPS, across, sides))
    return found


def slow_cuts(raw: bytes, duration: float, hard: Sequence[float] = (), **tuning: float) -> list[float]:
    """Both ends of each stretch where the picture turns into another one gradually (whip pan, dissolve); stretches
    around one of the ``hard`` cuts are that cut, not a gradual change."""
    step = 1 / SLOW_FPS
    half = int(tuning.get("span", SLOW_SPAN)) / SLOW_FPS
    cuts: list[float] = []
    run: list[float] = []
    for time, _, _ in transitions(raw, **tuning) + [(float("inf"), 0.0, 0.0)]:
        if run and time - run[-1] > step * 1.5:
            low, high = max(0.0, run[0] - half), min(duration, run[-1] + half)
            if not any(low <= cut <= high for cut in hard):
                cuts += [round(low, 3), round(high, 3)]
            run = []
        run.append(time)
    return cuts


def merge(hard: list[float], slow: list[float], duration: float) -> list[float]:
    """Hard cuts plus the gradual ones, in order, without flicker (cuts under MIN_SHOT apart are one)."""
    cuts: list[float] = []
    for time in sorted({*hard, *slow}):
        if MIN_SHOT <= time <= duration - MIN_SHOT and (not cuts or time - cuts[-1] >= MIN_SHOT):
            cuts.append(time)
    return cuts


def identity(video: Path, threshold: float) -> dict[str, Any]:
    stat = video.stat()
    return {"recipe": RECIPE, "threshold": threshold, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def detect(video: Path, duration: float, cache: Path, *, ffmpeg: Path, run: Runner, cancelled: Callable[[], bool],
           threshold: float = THRESHOLD) -> list[float]:
    """The source's cuts, from ``cache`` when the file and threshold are unchanged."""
    key = identity(video, threshold)
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == key and isinstance(saved.get("cuts"), list):
            return [float(cut) for cut in saved["cuts"]]
    except (OSError, ValueError):
        pass
    work = cache.parent
    work.mkdir(parents=True, exist_ok=True)
    log = work / "scdet.txt"
    log.unlink(missing_ok=True)
    # metadata=print takes a file name; a relative one avoids escaping the drive colon in a filter.
    thumbs = work / "thumbs.gray"
    thumbs.unlink(missing_ok=True)
    run([str(ffmpeg), "-hide_banner", "-nostats", "-nostdin", "-i", str(video), "-an", "-sn", "-dn", "-filter_complex",
         f"[0:v]scale={ANALYSIS_WIDTH}:-2,split=2[cut][slow];"
         f"[cut]scdet=threshold={threshold},metadata=mode=print:key=lavfi.scd.time:file={log.name}[cuts];"
         f"[slow]fps={SLOW_FPS},scale={SLOW_W}:{SLOW_H}:flags=area,format=gray[thumbs]",
         "-map", "[cuts]", "-f", "null", "-", "-map", "[thumbs]", "-f", "rawvideo", thumbs.name], work, cancelled)
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise DocuError(f"Không tách được cảnh của {video.name}") from error
    try:
        raw = thumbs.read_bytes()
    except OSError:
        raw = b""
    hard = parse(text, duration)
    cuts = merge(hard, slow_cuts(raw, duration, hard), duration)
    thumbs.unlink(missing_ok=True)
    write_json(cache, {"schema_version": 1, "identity": key, "duration": duration, "cuts": cuts})
    log.unlink(missing_ok=True)
    return cuts


def shots(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0, *cuts, duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]
