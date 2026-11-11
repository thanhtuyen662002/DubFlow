"""Scene analysis for the mastering planner: shots, motion and silences in one FFmpeg pass (ADR-0052 §6).

Each source is decoded once. The pass reuses the documentary's shot detection
(``docu/shots.py``: ``scdet`` on a 320-pixel copy, plus the gradual changes a
whip pan or a dissolve make on 32×18 grey thumbnails at 5 fps), keeps those
thumbnails long enough to measure how much each shot moves, and runs
``silencedetect`` on the first audio stream. All three filters ship in the
bundled LGPL FFmpeg; nothing needs a GPU or a model.

The result is in integer milliseconds and cached beside the run, keyed by the
file's size, mtime and this recipe, so planning the same footage again (or
loading a saved plan) does not decode it again.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Sequence

from engine.dubflow.docu import shots as docu_shots

from .plan import file_identity

SCHEMA_VERSION = 1
SILENCE_DB = -40
SILENCE_MIN_MS = 350
# Thumbnail differences this close to a cut belong to the cut, not to the shot's motion.
CUT_GUARD_MS = 300
RECIPE = f"mastering-analysis-v2+{docu_shots.RECIPE}+silence{SILENCE_DB}dB{SILENCE_MIN_MS}ms"
Runner = Callable[[list[str], Path, Callable[[], bool]], None]

_SILENCE_START = re.compile(r"lavfi\.silence_start=(-?[0-9]+(?:\.[0-9]+)?)")
_SILENCE_END = re.compile(r"lavfi\.silence_end=(-?[0-9]+(?:\.[0-9]+)?)")


class AnalysisError(Exception):
    """The source could not be analysed; the workflow keeps its own plan."""


@dataclass(frozen=True)
class Shot:
    start_ms: int
    end_ms: int
    # Mean picture change between thumbnails 0.2 s apart, in tenths of a percent of full scale (0–1000).
    motion: int

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class Analysis:
    duration_ms: int
    cuts_ms: tuple[int, ...]
    shots: tuple[Shot, ...]
    silences_ms: tuple[tuple[int, int], ...]
    has_audio: bool
    identity: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "recipe": RECIPE, "identity": self.identity, "duration_ms": self.duration_ms,
                "cuts_ms": list(self.cuts_ms), "shots": [[s.start_ms, s.end_ms, s.motion] for s in self.shots],
                "silences_ms": [list(s) for s in self.silences_ms], "has_audio": self.has_audio}

    @classmethod
    def from_dict(cls, value: Any) -> "Analysis":
        if type(value) is not dict or value.get("schema_version") != SCHEMA_VERSION or value.get("recipe") != RECIPE:
            raise ValueError("another analysis version")

        def whole(item: Any) -> int:
            if type(item) is not int or item < 0:
                raise ValueError("not a non-negative integer")
            return item
        duration = whole(value["duration_ms"])
        cuts = tuple(whole(item) for item in value["cuts_ms"])
        found = tuple(Shot(whole(a), whole(b), whole(c)) for a, b, c in value["shots"])
        silences = tuple((whole(a), whole(b)) for a, b in value["silences_ms"])
        if type(value["has_audio"]) is not bool or type(value["identity"]) is not str:
            raise ValueError("bad fields")
        if list(cuts) != sorted(set(cuts)) or any(s.end_ms <= s.start_ms for s in found):
            raise ValueError("cuts or shots out of order")
        return cls(duration, cuts, found, silences, value["has_audio"], value["identity"])

    def shot_at(self, ms: int) -> Shot:
        for shot in self.shots:
            if shot.start_ms <= ms < shot.end_ms:
                return shot
        return self.shots[-1]


@dataclass(frozen=True)
class StreamInfo:
    duration_ms: int
    width: int
    height: int
    fps: Fraction
    has_audio: bool
    picture_ms: int | None = None  # how long the video stream itself lasts, when ffprobe says (may end before the audio)


def probe(ffprobe: Path, video: Path) -> StreamInfo:
    """Duration, display size, frame rate and whether there is an audio stream."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([str(ffprobe), "-v", "error", "-show_entries",
                                 "format=duration:stream=codec_type,width,height,avg_frame_rate,duration:stream_tags=DURATION:stream_side_data=rotation",
                                 "-of", "json", str(video)], capture_output=True, timeout=60, creationflags=flags)
        value = json.loads(result.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise AnalysisError(f"Không đọc được thông tin {video.name}") from error
    streams = value.get("streams") or []
    picture = next((s for s in streams if s.get("codec_type") == "video"), None)
    try:
        duration_ms = round(float(value["format"]["duration"]) * 1000)
        width, height = int(picture["width"]), int(picture["height"])
        # a variable-rate source reports an unreduced average (rc7 real set: 10294000/343133 ≈ 30): keep it a frame rate
        rate = Fraction(picture.get("avg_frame_rate") or "0/1").limit_denominator(1001)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise AnalysisError(f"{video.name} không có hình để phân tích") from error
    rotation = next((int(side.get("rotation", 0)) for side in picture.get("side_data_list", []) if "rotation" in side), 0)
    if rotation % 180:
        width, height = height, width
    if duration_ms <= 0 or width <= 0 or height <= 0:
        raise AnalysisError(f"{video.name} không có hình để phân tích")
    return StreamInfo(duration_ms, width, height, rate, any(s.get("codec_type") == "audio" for s in streams),
                      _stream_ms(picture))


def _stream_ms(stream: dict[str, Any]) -> int | None:
    """A stream's own length: ``duration`` (MP4, MOV) or the ``DURATION`` tag (Matroska, WebM, ``HH:MM:SS.fff``)."""
    try:
        if stream.get("duration") not in (None, "N/A"):
            value = round(float(stream["duration"]) * 1000)
        else:
            tag = str((stream.get("tags") or {}).get("DURATION") or "")
            hours, minutes, rest = tag.split(":")
            value = round((int(hours) * 3600 + int(minutes) * 60 + float(rest)) * 1000)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def silences(text: str, duration_ms: int) -> list[tuple[int, int]]:
    """``silencedetect`` metadata → ordered [start, end) in ms, clipped to the source; an open silence runs to the end."""
    found: list[tuple[int, int]] = []
    start: int | None = None
    for line in text.splitlines():
        if (match := _SILENCE_START.search(line)) is not None:
            start = max(0, round(float(match.group(1)) * 1000))
        elif (match := _SILENCE_END.search(line)) is not None and start is not None:
            end = min(duration_ms, round(float(match.group(1)) * 1000))
            if end > start:
                found.append((start, end))
            start = None
    if start is not None and start < duration_ms:
        found.append((start, duration_ms))
    return found


def shot_motion(raw: bytes, shot_bounds: Sequence[tuple[int, int]]) -> list[int]:
    """Each shot's mean change between consecutive 5 fps thumbnails, away from its cuts."""
    frames = docu_shots._frames(raw)
    step_ms = 1000 // docu_shots.SLOW_FPS
    changes = [((i + 1) * step_ms, docu_shots._difference(frames[i], frames[i + 1])) for i in range(len(frames) - 1)]
    out = []
    for start, end in shot_bounds:
        inside = [value for at, value in changes if start + CUT_GUARD_MS <= at <= end - CUT_GUARD_MS]
        if not inside:  # a very short shot: whatever is in it
            inside = [value for at, value in changes if start <= at <= end]
        out.append(min(1000, round(10 * sum(inside) / len(inside))) if inside else 0)
    return out


def analyse_text(cut_log: str, thumbs: bytes, silence_log: str | None, duration_ms: int, identity: str) -> Analysis:
    """The pure part of ``analyse``: FFmpeg's three outputs → an Analysis."""
    seconds = duration_ms / 1000
    hard = docu_shots.parse(cut_log, seconds)
    cuts_s = docu_shots.merge(hard, docu_shots.slow_cuts(thumbs, seconds, hard), seconds)
    cuts = sorted({round(cut * 1000) for cut in cuts_s if 0 < round(cut * 1000) < duration_ms})
    bounds = [(a, b) for a, b in zip([0, *cuts], [*cuts, duration_ms]) if b > a]
    motion = shot_motion(thumbs, bounds)
    found = tuple(Shot(a, b, level) for (a, b), level in zip(bounds, motion))
    quiet = tuple(silences(silence_log, duration_ms)) if silence_log is not None else ()
    return Analysis(duration_ms, tuple(cuts), found, quiet, silence_log is not None, identity)


def argv(ffmpeg: Path, video: Path, *, has_audio: bool, cut_log: str, thumbs: str, silence_log: str) -> list[str]:
    command = [str(ffmpeg), "-hide_banner", "-nostats", "-nostdin", "-i", str(video), "-filter_complex",
               f"[0:v]scale={docu_shots.ANALYSIS_WIDTH}:-2,split=2[cut][slow];"
               f"[cut]scdet=threshold={docu_shots.THRESHOLD},metadata=mode=print:key=lavfi.scd.time:file={cut_log}[cuts];"
               f"[slow]fps={docu_shots.SLOW_FPS},scale={docu_shots.SLOW_W}:{docu_shots.SLOW_H}:flags=area,format=gray[thumbs]",
               "-map", "[cuts]", "-f", "null", "-", "-map", "[thumbs]", "-f", "rawvideo", thumbs]
    if has_audio:
        command += ["-map", "0:a:0", "-vn", "-af",
                    f"silencedetect=n={SILENCE_DB}dB:d={SILENCE_MIN_MS / 1000:g},ametadata=mode=print:file={silence_log}",
                    "-f", "null", "-"]
    return command


def cache_path(folder: Path, video: Path) -> Path:
    """Where ``video``'s analysis is cached in ``folder``: named by content, so reordering or renaming clips reuses it."""
    return folder / f"analysis-{file_identity(video, RECIPE)[:24]}.json"


def analyse(video: Path, info: StreamInfo, cache: Path, *, ffmpeg: Path, run: Runner,
            cancelled: Callable[[], bool] = lambda: False) -> Analysis:
    """``video``'s shots, motion and silences, from ``cache`` when the file is unchanged.

    ``cache`` is a JSON file; its folder holds the pass's temporary files (relative names avoid escaping a Windows
    drive colon inside the filter graph). ``run`` is the workflow's cancellable FFmpeg runner.
    """
    identity = file_identity(video, RECIPE)
    try:
        saved = Analysis.from_dict(json.loads(cache.read_text(encoding="utf-8")))
        if saved.identity == identity and saved.duration_ms == info.duration_ms:
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    work = cache.parent
    work.mkdir(parents=True, exist_ok=True)
    stem = cache.stem
    names = {"cut_log": f"{stem}.scdet.txt", "thumbs": f"{stem}.thumbs.gray", "silence_log": f"{stem}.silence.txt"}
    for name in names.values():
        (work / name).unlink(missing_ok=True)
    try:
        run(argv(ffmpeg, video, has_audio=info.has_audio, **names), work, cancelled)
        cut_log = (work / names["cut_log"]).read_text(encoding="utf-8", errors="replace")
        thumbs = (work / names["thumbs"]).read_bytes()
        silence_log = (work / names["silence_log"]).read_text(encoding="utf-8", errors="replace") if info.has_audio else None
    except OSError as error:
        raise AnalysisError(f"Không phân tích được cảnh của {video.name}") from error
    finally:
        for name in names.values():
            (work / name).unlink(missing_ok=True)
    result = analyse_text(cut_log, thumbs, silence_log, info.duration_ms, identity)
    partial = cache.with_name(cache.name + ".partial")
    partial.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    os.replace(partial, cache)
    return result


__all__ = ["Analysis", "AnalysisError", "RECIPE", "Shot", "StreamInfo", "analyse", "cache_path", "analyse_text", "probe", "silences"]
