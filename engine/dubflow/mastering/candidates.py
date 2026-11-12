"""Candidate shots of a source, with a keyframe, picture quality and a duplicate hash (ADR-0054 §1).

A candidate is a stretch of one shot that a planner may cut a piece from. Short
and medium shots are one candidate each; a long take is split into windows so a
planner can tell its beginning from its end. For every candidate one FFmpeg call
(three when the middle frame is unusable: a quarter and three quarters in are
tried, and the first usable frame becomes the keyframe) grabs its middle frame twice: a JPEG (for the optional vision adapter and the
desktop) and a 160×90 grey frame from which pure Python measures brightness,
contrast and sharpness and computes a 64-bit difference hash. A frame that is
too dark, blank or blurred makes the candidate unusable, with the reason; two
candidates whose hashes differ in at most ``DUPLICATE_BITS`` bits show the same
picture.

Everything is in integer milliseconds and cached by the source's content.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from . import analysis as scene
from .plan import file_identity

SCHEMA_VERSION = 1
GREY_W, GREY_H = 160, 90
KEYFRAME_WIDTH = 512
# A shot longer than this is split into windows of WINDOW_MS (the last takes the rest).
SPLIT_MS = 12_000
WINDOW_MS = 6000
# Margin kept from a cut so no frame of the neighbouring shot is grabbed or shown.
CUT_MARGIN_MS = 120
MIN_CANDIDATE_MS = 400
MAX_CANDIDATES = 400
DARK, BRIGHT = 28, 235          # mean grey level
FLAT = 10                       # grey standard deviation of a blank or black frame
BLUR = 18                       # Laplacian variance below this: no detail at 160×90
DUPLICATE_BITS = 6
# When the middle frame is unusable (a fade, motion blur), these frames (‰ of the shot) are tried before the shot is
# called unusable: the first usable one becomes the keyframe (Gate S, multi-frame evidence step 1).
FALLBACK_POSITIONS = (250, 750)
RECIPE = (f"mastering-candidates-v2+grey{GREY_W}x{GREY_H}+split{SPLIT_MS}+win{WINDOW_MS}+spread{MAX_CANDIDATES}"
          f"+fallback{'-'.join(map(str, FALLBACK_POSITIONS))}")
Runner = Callable[[list[str], Path, Callable[[], bool]], None]


@dataclass(frozen=True)
class Quality:
    brightness: int  # 0–255 mean
    contrast: int    # grey standard deviation
    sharpness: int   # Laplacian variance
    problems: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.problems

    @property
    def score(self) -> int:
        """0–1000: detail and a usable exposure; 0 when unusable."""
        if self.problems:
            return 0
        detail = min(1000, self.sharpness * 1000 // 400)
        spread = min(1000, self.contrast * 1000 // 60)
        exposure = 1000 - min(1000, abs(self.brightness - 128) * 1000 // 128)
        return (detail * 5 + spread * 3 + exposure * 2) // 10


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    source_id: str
    start_ms: int
    end_ms: int
    shot_index: int
    motion: int
    quality: Quality
    dhash: int
    keyframe: str | None = None  # file name in the candidates folder

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def middle_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2

    def to_dict(self) -> dict[str, Any]:
        q = self.quality
        return {"id": self.candidate_id, "source_id": self.source_id, "start_ms": self.start_ms, "end_ms": self.end_ms,
                "shot": self.shot_index, "motion": self.motion, "dhash": f"{self.dhash:016x}", "keyframe": self.keyframe,
                "quality": {"brightness": q.brightness, "contrast": q.contrast, "sharpness": q.sharpness, "problems": list(q.problems)}}

    @classmethod
    def from_dict(cls, value: Any) -> "Candidate":
        q = value["quality"]
        quality = Quality(int(q["brightness"]), int(q["contrast"]), int(q["sharpness"]), tuple(str(p) for p in q["problems"]))
        made = cls(str(value["id"]), str(value["source_id"]), int(value["start_ms"]), int(value["end_ms"]), int(value["shot"]),
                   int(value["motion"]), quality, int(value["dhash"], 16), value.get("keyframe"))
        if made.end_ms <= made.start_ms:
            raise ValueError("empty candidate")
        return made


def windows(found: scene.Analysis, within: tuple[int, int] | None = None) -> list[tuple[int, int, int, int]]:
    """(start, end, shot index, motion) of each candidate window, kept CUT_MARGIN_MS inside its shot.

    ``within`` (a source trim, ms): only the windows that overlap it, and the cap spreads over those (P2-N11: a
    105 s window of a 36-minute film kept 12 of its windows, too few for its narration, and held frames)."""
    return [w for _, w in numbered(found, within)]


def numbered(found: scene.Analysis, within: tuple[int, int] | None = None) -> list[tuple[int, tuple[int, int, int, int]]]:
    """The windows of :func:`windows`, each with its number (stable for a source and a trim)."""
    out = []
    for index, shot in enumerate(found.shots):
        start = shot.start_ms + (CUT_MARGIN_MS if shot.start_ms > 0 else 0)
        end = shot.end_ms - (CUT_MARGIN_MS if shot.end_ms < found.duration_ms else 0)
        if end - start < MIN_CANDIDATE_MS:
            continue
        if end - start <= SPLIT_MS:
            out.append((start, end, index, shot.motion))
            continue
        at = start
        while end - at > 0:
            stop = end if end - at < WINDOW_MS + WINDOW_MS // 2 else at + WINDOW_MS
            out.append((at, stop, index, shot.motion))
            at = stop
    if within is not None:
        out = [w for w in out if w[1] > within[0] and w[0] < within[1]]
    if len(out) <= MAX_CANDIDATES:
        return list(enumerate(out, start=1))
    # a long source: windows spread over the whole of it, not the first MAX_CANDIDATES in source order (a
    # 36-minute set lost everything after 20 minutes, Why We Fight)
    step = len(out) / MAX_CANDIDATES
    return [(index + 1, out[int(index * step)]) for index in range(MAX_CANDIDATES)]


def measure(grey: bytes, width: int = GREY_W, height: int = GREY_H) -> tuple[Quality, int]:
    """Quality and difference hash of one grey frame (row-major bytes)."""
    if len(grey) < width * height:
        raise ValueError("short grey frame")
    pixels = grey[:width * height]
    count = width * height
    mean = sum(pixels) / count
    variance = sum((p - mean) ** 2 for p in pixels) / count
    laplacian = []
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            i = row + x
            laplacian.append(4 * pixels[i] - pixels[i - 1] - pixels[i + 1] - pixels[i - width] - pixels[i + width])
    lap_mean = sum(laplacian) / len(laplacian)
    sharp = sum((v - lap_mean) ** 2 for v in laplacian) / len(laplacian)
    brightness, contrast, sharpness = round(mean), round(variance ** 0.5), round(sharp)
    problems = []
    if contrast < FLAT:
        problems.append("khung hình gần như một màu (đen/trắng/trống)")
    elif brightness < DARK:
        problems.append("quá tối")
    elif brightness > BRIGHT:
        problems.append("cháy sáng")
    if not problems and sharpness < BLUR:
        problems.append("mờ, thiếu chi tiết")
    return Quality(brightness, contrast, sharpness, tuple(problems)), dhash(pixels, width, height)


def dhash(pixels: bytes, width: int, height: int) -> int:
    """64-bit difference hash: a 9×8 average grid, each cell compared with its right neighbour."""
    grid = []
    for gy in range(8):
        y0, y1 = gy * height // 8, (gy + 1) * height // 8
        row = []
        for gx in range(9):
            x0, x1 = gx * width // 9, (gx + 1) * width // 9
            total = sum(pixels[y * width + x] for y in range(y0, y1) for x in range(x0, x1))
            row.append(total / max(1, (y1 - y0) * (x1 - x0)))
        grid.append(row)
    value = 0
    for row in grid:
        for a, b in zip(row, row[1:]):
            value = (value << 1) | (1 if a > b else 0)
    return value


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def duplicate_groups(found: Sequence[Candidate]) -> dict[str, str]:
    """Candidate id → the id of the first candidate that shows the same picture (itself when none)."""
    groups: dict[str, str] = {}
    leaders: list[Candidate] = []
    for candidate in found:
        match = next((leader for leader in leaders if distance(leader.dhash, candidate.dhash) <= DUPLICATE_BITS), None)
        if match is None:
            leaders.append(candidate)
            groups[candidate.candidate_id] = candidate.candidate_id
        else:
            groups[candidate.candidate_id] = groups[match.candidate_id]
    return groups


def grab_argv(ffmpeg: Path, video: Path, at_ms: int, jpeg: str, grey: str) -> list[str]:
    """One frame at ``at_ms``: a JPEG KEYFRAME_WIDTH wide and a GREY_W×GREY_H grey raw frame."""
    return [str(ffmpeg), "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-y", "-ss", f"{at_ms / 1000:.3f}", "-i", str(video),
            "-filter_complex", f"[0:v]split=2[a][b];[a]scale={KEYFRAME_WIDTH}:-2,format=yuvj420p[jpg];"
                               f"[b]scale={GREY_W}:{GREY_H}:flags=area,format=gray[grey]",
            "-map", "[jpg]", "-frames:v", "1", "-q:v", "4", jpeg, "-map", "[grey]", "-frames:v", "1", "-f", "rawvideo", grey]


def fallback_times(start_ms: int, end_ms: int) -> list[int]:
    """Where to look when the middle frame of ``[start_ms, end_ms)`` is unusable (source ms)."""
    return [start_ms + (end_ms - start_ms) * p // 1000 for p in FALLBACK_POSITIONS]


def extract(video: Path, source_id: str, found: scene.Analysis, folder: Path, *, ffmpeg: Path, run: Runner,
            cancelled: Callable[[], bool] = lambda: False, within: tuple[int, int] | None = None) -> list[Candidate]:
    """The candidates of ``video`` (analysed as ``found``), from the cache in ``folder`` when the file is unchanged.

    ``within``: only the part of the source a trim uses (and the cap spreads over it) when the whole source has more
    windows than the cap; otherwise the windows are the same as without it."""
    if within is not None and len(windows(found)) < MAX_CANDIDATES:
        within = None  # every window of the source fits the cap: the same candidates (and cache) as without a trim
    identity = file_identity(video, RECIPE + scene.RECIPE) + ("" if within is None else f"@{within[0]}-{within[1]}")
    tag = identity[:16] if within is None else hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    cache = folder / f"candidates-{tag}.json"
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("schema_version") == SCHEMA_VERSION and saved.get("identity") == identity:
            return [_rename(Candidate.from_dict(item), source_id) for item in saved["candidates"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for number, (start, end, shot, motion) in (enumerate(windows(found), start=1) if within is None else numbered(found, within)):
        if cancelled():
            raise scene.AnalysisError("cancelled")
        jpeg, grey = f"{tag}-c{number}.jpg", f"{tag}-c{number}.grey"
        middle = (start + end) // 2
        quality, hashed, picked = Quality(0, 0, 0, ("không đọc được khung hình",)), 0, None
        for at in [middle, *fallback_times(start, end)]:
            try:
                run(grab_argv(ffmpeg, video, at, jpeg, grey), folder, cancelled)
                found_quality, found_hash = measure((folder / grey).read_bytes())
            except Exception:  # noqa: BLE001 - a frame we cannot read is not usable
                if cancelled():
                    raise  # the workflow's own cancel, not a bad frame
                continue
            finally:
                (folder / grey).unlink(missing_ok=True)
            if picked is None or found_quality.usable:
                # the middle frame's measurements stand unless a later frame is usable (its jpeg is the keyframe)
                quality, hashed, picked = found_quality, found_hash, at
            if found_quality.usable:
                break
        if picked is None:
            jpeg = None
        made.append(Candidate(f"{source_id}c{number}", source_id, start, end, shot, motion, quality, hashed,
                              jpeg if jpeg and (folder / jpeg).is_file() else None))
    partial = cache.with_name(cache.name + ".partial")
    partial.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "identity": identity, "recipe": RECIPE,
                                   "candidates": [c.to_dict() for c in made]}, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, cache)
    return made


def _rename(candidate: Candidate, source_id: str) -> Candidate:
    """A cached candidate under the source id it has in this job (the cache is shared across positions)."""
    number = candidate.candidate_id[len(candidate.source_id) + 1:]
    return Candidate(f"{source_id}c{number}", source_id, candidate.start_ms, candidate.end_ms, candidate.shot_index,
                     candidate.motion, candidate.quality, candidate.dhash, candidate.keyframe)


def still(source_id: str, path: Path, folder: Path, *, ffmpeg: Path, run: Runner,
          cancelled: Callable[[], bool] = lambda: False) -> Candidate:
    """A photo as one candidate (its own picture, measured like a frame)."""
    identity = file_identity(path, RECIPE)
    tag = identity[:16]
    folder.mkdir(parents=True, exist_ok=True)
    jpeg, grey = f"{tag}-still.jpg", f"{tag}-still.grey"
    try:
        argv = grab_argv(ffmpeg, path, 0, jpeg, grey)
        del argv[argv.index("-ss"):argv.index("-ss") + 2]
        run(argv, folder, cancelled)
        quality, hashed = measure((folder / grey).read_bytes())
    except Exception:  # noqa: BLE001 - an unreadable photo is still the seller's photo; it just has no measurements
        quality, hashed, jpeg = Quality(128, 40, 100, ()), 0, None
    finally:
        (folder / grey).unlink(missing_ok=True)
    return Candidate(f"{source_id}c1", source_id, 0, 1, 0, 0, quality, hashed, jpeg if jpeg and (folder / jpeg).is_file() else None)


__all__ = ["Candidate", "Quality", "RECIPE", "distance", "duplicate_groups", "extract", "measure", "still", "windows"]
