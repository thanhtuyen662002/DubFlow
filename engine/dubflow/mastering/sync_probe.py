"""A/V sync at every boundary of a rendered programme (P2-9, Gate C), measured from the decoded output.

Two methods, both read the output as a player does (decoded, timestamps applied), never the container's
start times alone (a file-level start difference says nothing about drift inside the file):

* **markers** (synthetic test patterns): each piece of a test render has its own colour and its own tone; the
  frame where the colour changes and the 2 ms window where the tone changes are each boundary's decoded video and
  audio times, compared with the plan.
* **reference** (real footage, any join of parts): the part file is its own reference. Around each join of the
  final programme, the next part's opening (3 s) is found in the final's decoded sound (onsets of the 1 ms
  loudness envelope, cross-correlation, ±300 ms, a clear peak twice anything beyond ±20 ms) and in its decoded pictures (small grey frames, nearest match, ±10 frames); the two
  lags are the join's audio and video offsets, and their difference its A/V offset. A stretch without sound or
  without picture change is reported as not measured, never as in sync.

Thresholds (the directive's, never changed here): per boundary ≤ max(40 ms, one frame), cumulative ≤ 80 ms,
and ``final_qc.SYNC_TOLERANCE_MS`` when that is stricter.
"""
from __future__ import annotations

from array import array
import json
import math
import re
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

RATE = 48000
ENVELOPE_MS = 1
SEARCH_MS = 300
REF_MS = 1500
# the sound of a join is matched on its onsets over a longer stretch (rc12 W4, P2-N19): a voice's pitch makes the 1 ms
# envelope correlate almost as well 4–9 ms off the peak (0.81–0.88 against 1.0), so no real join of narration over
# music had a clear peak. The onsets (the envelope's first difference) do not carry that ripple. A clear peak must
# still be twice as high as anything away from it; "away" is beyond SIDELOBE_MS, a fraction of the 40 ms limit.
AUDIO_REF_MS = 2 * REF_MS
SIDELOBE_MS = 20
SEARCH_FRAMES = 10
GREY_W, GREY_H = 32, 18
BOUNDARY_FLOOR_MS = 40
CUMULATIVE_MS = 80


def limit_ms(frame_ms: float) -> float:
    return max(BOUNDARY_FLOOR_MS, frame_ms)


def _run(argv: list[str]) -> bytes:
    return subprocess.run(argv, capture_output=True, check=True, timeout=600).stdout


def audio(ffmpeg: str, path: Path, start_ms: int = 0, length_ms: int | None = None) -> array:
    """Mono 16-bit samples at 48 kHz of ``path`` from ``start_ms`` (decoded, priming removed by the demuxer)."""
    argv = [ffmpeg, "-v", "error", "-nostdin"]
    if start_ms:
        argv += ["-ss", f"{start_ms / 1000:.3f}"]
    argv += ["-i", str(path)]
    if length_ms is not None:
        argv += ["-t", f"{length_ms / 1000:.3f}"]
    out = array("h")
    out.frombytes(_run(argv + ["-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]))
    if sys.byteorder == "big":
        out.byteswap()
    return out


def frames(ffmpeg: str, path: Path, start_ms: int = 0, count: int | None = None) -> list[bytes]:
    """Small grey frames of ``path`` from ``start_ms``, one per decoded frame (no frame added or dropped)."""
    argv = [ffmpeg, "-v", "error", "-nostdin"]
    if start_ms:
        argv += ["-ss", f"{start_ms / 1000:.3f}"]
    argv += ["-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough"]
    if count is not None:
        argv += ["-frames:v", str(count)]
    raw = _run(argv + ["-vf", f"scale={GREY_W}:{GREY_H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"])
    size = GREY_W * GREY_H
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def envelope(samples: Sequence[int], bin_ms: int = ENVELOPE_MS) -> list[float]:
    """Mean absolute amplitude in ``bin_ms`` bins."""
    step = RATE * bin_ms // 1000
    return [sum(abs(x) for x in samples[i:i + step]) / step for i in range(0, len(samples) - step + 1, step)]


def onsets(values: Sequence[float]) -> list[float]:
    """The first difference of an envelope: where the sound starts and stops, without its slow level."""
    return [b - a for a, b in zip(values, values[1:])]


def best_lag(reference: Sequence[float], target: Sequence[float], centre: int, search: int,
             apart: int = 2) -> tuple[int | None, float]:
    """The lag (bins, relative to ``centre``) where ``reference`` best matches ``target`` (normalised correlation),
    and that correlation; None when the reference is flat (silence) or nothing matches. The peak must be clear:
    at least 0.3, and twice as high as any lag more than ``apart`` bins from it."""
    n = len(reference)
    mean = sum(reference) / max(1, n)
    ref = [x - mean for x in reference]
    norm = math.sqrt(sum(x * x for x in ref))
    if n == 0 or norm < 1e-6:
        return None, 0.0
    scores: dict[int, float] = {}
    for lag in range(-search, search + 1):
        at = centre + lag
        if at < 0 or at + n > len(target):
            continue
        window = target[at:at + n]
        wmean = sum(window) / n
        num = sum(r * (w - wmean) for r, w in zip(ref, window))
        den = norm * math.sqrt(sum((w - wmean) ** 2 for w in window))
        scores[lag] = num / den if den > 1e-6 else -1.0
    if not scores:
        return None, 0.0
    best = max(scores, key=lambda lag: scores[lag])
    # a clear peak: well correlated, and twice as high as anything more than 2 bins away (an offset of a fraction of
    # a bin splits the peak between two neighbours; re-encoding adds noise)
    others = max((v for lag, v in scores.items() if abs(lag - best) > apart), default=0.0)
    clear = scores[best] >= 0.3 and scores[best] >= 2 * max(0.0, others)
    return (best, scores[best]) if clear else (None, scores[best])


def _sad(a: bytes, b: bytes) -> int:
    return sum(abs(x - y) for x, y in zip(a, b))


def frame_lag(reference: Sequence[bytes], target: Sequence[bytes], centre: int, search: int) -> int | None:
    """The frame lag where the opening ``reference`` frames best match ``target`` (sum of absolute differences);
    None when the opening frames do not change (a still: no position to find) or the match is not clear."""
    if len(reference) < 2 or all(_sad(reference[0], f) == 0 for f in reference[1:]):
        return None
    costs = {}
    for lag in range(-search, search + 1):
        at = centre + lag
        if at < 0 or at + len(reference) > len(target):
            continue
        costs[lag] = sum(_sad(r, t) for r, t in zip(reference, target[at:]))
    if not costs:
        return None
    ordered = sorted(costs.items(), key=lambda kv: kv[1])
    if len(ordered) > 1 and ordered[1][1] - ordered[0][1] < 0.02 * max(1, ordered[1][1]):
        return None  # two lags fit as well: not measured
    return ordered[0][0]


# --- markers: synthetic test patterns (each piece its own colour and tone) ---

def goertzel(samples: Sequence[int], freq: float) -> float:
    k = 2 * math.cos(2 * math.pi * freq / RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def colour_edges(ffmpeg: str, path: Path, colours: Sequence[tuple[int, int, int]]) -> list[float]:
    """Decoded times (ms) where the centre colour of the picture changes from one marker colour to another."""
    # showinfo reports each decoded frame's time at the info level
    argv = [ffmpeg, "-hide_banner", "-v", "info", "-nostdin", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
            "-vf", "scale=16:9,showinfo", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    done = subprocess.run(argv, capture_output=True, check=True, timeout=600)
    times = [float(t) * 1000 for t in re.findall(r"pts_time:([0-9.]+)", done.stderr.decode("utf-8", "replace"))]
    size = 16 * 9 * 3
    raw = done.stdout
    labels = []
    for i in range(0, len(raw) - size + 1, size):
        c = (8 + 4 * 16) * 3
        rgb = raw[i + c], raw[i + c + 1], raw[i + c + 2]
        labels.append(min(range(len(colours)), key=lambda j: sum((a - b) ** 2 for a, b in zip(rgb, colours[j]))))
    return [times[i] for i in range(1, min(len(labels), len(times))) if labels[i] != labels[i - 1]]


def tone_edges(ffmpeg: str, path: Path, freqs: Sequence[float], *, window_ms: int = 2, floor: float = 1e5) -> list[float]:
    """Decoded times (ms) where the strongest marker tone changes (``window_ms`` resolution)."""
    samples = audio(ffmpeg, path)
    step = RATE * window_ms // 1000
    labels = []
    for i in range(0, len(samples) - step + 1, step):
        chunk = samples[i:i + step]
        power = [goertzel(chunk, f) for f in freqs]
        best = max(range(len(freqs)), key=lambda j: power[j])
        labels.append(best if power[best] > floor else -1)
    return [i * window_ms for i in range(1, len(labels)) if labels[i] != labels[i - 1] and labels[i] >= 0 and labels[i - 1] >= 0]


def measure_markers(ffmpeg: str, path: Path, plan_edges_ms: Sequence[float], colours: Sequence[tuple[int, int, int]],
                    freqs: Sequence[float], *, fps: float = 30.0) -> dict[str, Any]:
    """Each planned boundary's decoded video and audio time, their offsets to the plan and to each other."""
    video = colour_edges(ffmpeg, path, colours)
    sound = tone_edges(ffmpeg, path, freqs)
    def near(xs: Sequence[float], e: float) -> float | None:  # the nearest edge, within the search (else not measured)
        best = min(xs, key=lambda x: abs(x - e)) if xs else None
        return best if best is not None and abs(best - e) <= SEARCH_MS else None
    rows = []
    for k, edge in enumerate(plan_edges_ms, start=1):
        v, a = near(video, edge), near(sound, edge)
        rows.append({"boundary": k, "plan_ms": edge, "video_ms": None if v is None else round(v - edge, 1),
                     "audio_ms": None if a is None else round(a - edge, 1),
                     "av_ms": None if v is None or a is None else round(v - a, 1)})
    frame_ms = 1000 / fps
    measured = [r for r in rows if r["av_ms"] is not None]
    return {"frame_ms": round(frame_ms, 2), "limit_ms": limit_ms(frame_ms), "rows": rows,
            "worst_av_ms": max((abs(r["av_ms"]) for r in measured), default=None),
            "worst_plan_ms": max((abs(r["video_ms"]) for r in rows if r["video_ms"] is not None), default=None),
            "measured": len(measured), "unmeasured": len(rows) - len(measured),
            "passed": bool(measured) and len(measured) == len(rows) and all(abs(r["av_ms"]) <= limit_ms(frame_ms) for r in measured)
            and max(abs(r["av_ms"]) for r in measured) <= CUMULATIVE_MS}


def measure_joins(ffmpeg: str, final: Path, parts: Sequence[tuple[Path, float]], *, fps: float = 30.0) -> dict[str, Any]:
    """Every join of ``final`` (made of ``parts``, each (file, duration ms) in order): audio, video and A/V
    offsets in ms, with the thresholds' verdict. A join without sound or picture change is not measured."""
    frame_ms = 1000 / fps
    rows = []
    at = 0
    total = sum(length for _, length in parts)
    for index, ((part, length), (following, _)) in enumerate(zip(parts, parts[1:]), start=1):
        at += length
        # the reference fits every lag searched: near the end (a short last part) it is shorter than AUDIO_REF_MS,
        # never shorter than REF_MS when the programme has that much left
        reference_ms = max(min(REF_MS, round(total - at)), min(AUDIO_REF_MS, round(total - at) - SEARCH_MS))
        ref = onsets(envelope(audio(ffmpeg, following, 0, reference_ms)))
        begin = max(0, round(at) - SEARCH_MS)
        around = onsets(envelope(audio(ffmpeg, final, begin, reference_ms + 2 * SEARCH_MS)))
        centre = round(at - begin) // ENVELOPE_MS
        lag, _ = best_lag(ref, around, centre, SEARCH_MS // ENVELOPE_MS, apart=SIDELOBE_MS // ENVELOPE_MS)
        count = max(8, int(REF_MS / frame_ms) // 2)
        # seek to just before a frame's time, so the first decoded frame is that frame (an accurate seek returns the
        # first frame at or after the time asked)
        index_at = round(at / frame_ms)
        first = max(0, index_at - SEARCH_FRAMES)
        vref = frames(ffmpeg, following, 0, count)
        varound = frames(ffmpeg, final, max(0, int(first * frame_ms) - 1) if first else 0, count + 2 * SEARCH_FRAMES + 2)
        vlag = frame_lag(vref, varound, index_at - first, SEARCH_FRAMES)
        audio_ms = None if lag is None else lag * ENVELOPE_MS
        video_ms = None if vlag is None else round(vlag * frame_ms, 1)
        av = None if audio_ms is None or video_ms is None else round(video_ms - audio_ms, 1)
        rows.append({"join": index, "expected_ms": round(at, 1), "audio_ms": audio_ms, "video_ms": video_ms, "av_ms": av,
                     "ok": None if av is None else abs(av) <= limit_ms(frame_ms)})
    measured = [r for r in rows if r["av_ms"] is not None]
    worst = max((abs(r["av_ms"]) for r in measured), default=None)
    return {"frame_ms": round(frame_ms, 2), "limit_ms": limit_ms(frame_ms), "cumulative_limit_ms": CUMULATIVE_MS,
            "joins": rows, "measured": len(measured), "unmeasured": len(rows) - len(measured), "worst_av_ms": worst,
            "passed": verdict(rows, frame_ms)}


def verdict(rows: Sequence[dict[str, Any]], frame_ms: float) -> bool | None:
    """The result of the joins of :func:`measure_joins`: False when a measured join is out of sync (per join over
    max(40 ms, a frame), or over CUMULATIVE_MS); None, never a pass, when any join could not be measured (silence or
    a still picture there) or there is none; True only when every join is measured and in sync."""
    measured = [abs(r["av_ms"]) for r in rows if r.get("av_ms") is not None]
    if any(av > limit_ms(frame_ms) or av > CUMULATIVE_MS for av in measured):
        return False
    if not rows or len(measured) < len(rows):
        return None
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="A/V offsets at every join of a programme (P2-9)")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--final", required=True)
    parser.add_argument("--parts", nargs="+", required=True, help="the part files, in programme order")
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args(argv)
    parts = []
    for name in args.parts:
        out = subprocess.run([args.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                              "stream=nb_read_packets", "-of", "csv=p=0", name], capture_output=True, text=True, check=True)
        parts.append((Path(name), int(out.stdout.strip().strip(",")) * 1000 / args.fps))
    found = measure_joins(args.ffmpeg, Path(args.final), parts, fps=args.fps)
    print(json.dumps(found, ensure_ascii=False, indent=1))
    # 0 in sync at every join; 1 out of sync somewhere; 2 not every join measured (never a pass)
    return {True: 0, False: 1}.get(found["passed"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
