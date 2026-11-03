"""Shot boundaries of a source with FFmpeg's ``scdet`` (ADR-0049, phase 2b).

``scdet`` ships with the bundled LGPL FFmpeg, so no OpenCV is needed. Frames
are scaled down first: a cut is as visible at 320 pixels wide and the scan runs
at about 30× real time.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable

from .project import DocuError, write_json

THRESHOLD = 10.0
ANALYSIS_WIDTH = 320
# Cuts closer than this to the previous one are one transition (a dissolve flickers several frames).
MIN_SHOT = 0.4
RECIPE = "scdet-320-v1"
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
    run([str(ffmpeg), "-hide_banner", "-nostats", "-nostdin", "-i", str(video), "-an", "-sn", "-dn", "-vf",
         f"scale={ANALYSIS_WIDTH}:-2,scdet=threshold={threshold}:sc_pass=1,metadata=mode=print:file={log.name}",
         "-f", "null", "-"], work, cancelled)
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise DocuError(f"Không tách được cảnh của {video.name}") from error
    cuts = parse(text, duration)
    write_json(cache, {"schema_version": 1, "identity": key, "duration": duration, "cuts": cuts})
    log.unlink(missing_ok=True)
    return cuts


def shots(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0, *cuts, duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]
