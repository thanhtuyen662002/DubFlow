"""Generated pictures of the rough cut (Tier-3): frames drawn with numpy, graphics and text drawn by libass.

The satellite map (``mapgen``) and the data slides (``infographic``) are not cut
from a source: numpy draws each frame's picture (satellite imagery, a dark
blueprint background) and FFmpeg's ``subtitles`` filter draws the HUD, the
bars and every word on top of it from an ASS file, so text is as sharp as the
captions and nothing needs Pillow or a font file of its own. The frames go to
FFmpeg through a pipe with the same encoder and settings as every other slice,
so the slices still join without re-encoding.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
from typing import Callable, Iterable, Iterator, Sequence

import numpy as np

from engine.dubflow import resources
from engine.dubflow.media import encoders as h264
from engine.dubflow.product.job import Cancelled, ProductError

WIDTH, HEIGHT, FPS = 1920, 1080, 30
BITRATE = "8M"

FrameSource = Callable[[], Iterator[np.ndarray]]


def ass_head(styles: str = "") -> str:
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Draw,Segoe UI,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            "Style: Mono,Consolas,30,&H00E6F2F2,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1\n"
            "Style: Head,Segoe UI,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1\n"
            "Style: Body,Segoe UI,36,&H00D8DEE6,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            "Style: Big,Segoe UI,120,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            + styles + "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 3600)}:{int(seconds // 60) % 60:02d}:{seconds % 60:05.2f}"


def event(start: float, end: float, text: str, style: str = "Draw", layer: int = 2) -> str:
    return f"Dialogue: {layer},{clock(start)},{clock(end)},{style},,0,0,0,,{text}\n"


def escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def colour(hex_rgb: str, alpha: int = 0) -> str:
    """``#RRGGBB`` as ASS ``&HAABBGGRR&``-style colour (alpha 0 is opaque)."""
    value = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{value[4:6]}{value[2:4]}{value[0:2]}&"


def rect(x: float, y: float, w: float, h: float) -> str:
    """A drawing command for a filled rectangle with its top-left at (x, y) of the drawing's own origin."""
    x2, y2 = x + w, y + h
    return f"m {x:.0f} {y:.0f} l {x2:.0f} {y:.0f} {x2:.0f} {y2:.0f} {x:.0f} {y2:.0f}"


def circle(radius: float, cx: float = 0.0, cy: float = 0.0) -> str:
    """A circle as four cubic Béziers."""
    k = 0.5523 * radius
    r = radius
    return (f"m {cx + r:.1f} {cy:.1f} b {cx + r:.1f} {cy + k:.1f} {cx + k:.1f} {cy + r:.1f} {cx:.1f} {cy + r:.1f} "
            f"b {cx - k:.1f} {cy + r:.1f} {cx - r:.1f} {cy + k:.1f} {cx - r:.1f} {cy:.1f} "
            f"b {cx - r:.1f} {cy - k:.1f} {cx - k:.1f} {cy - r:.1f} {cx:.1f} {cy - r:.1f} "
            f"b {cx + k:.1f} {cy - r:.1f} {cx + r:.1f} {cy - k:.1f} {cx + r:.1f} {cy:.1f}")


def drawing(commands: str, tags: str = "") -> str:
    return f"{{\\p1{tags}}}{commands}{{\\p0}}"


def ease(value: float) -> float:
    """Smooth start and stop (smootherstep) of a 0..1 progress."""
    value = min(1.0, max(0.0, value))
    return value * value * value * (value * (value * 6 - 15) + 10)


def count_up(start: float, end: float, final: str, x: int, y: int, style: str, tags: str = "", seconds: float = 0.9) -> list[str]:
    """``final`` (a figure such as "$238" or "1,323 tons") counting up from zero, then held to ``end``."""
    import re
    match = re.search(r"\d[\d,]*(?:\.\d+)?", final)
    rows = []
    if match:
        number = float(match.group(0).replace(",", ""))
        decimals = len(match.group(0).split(".")[1]) if "." in match.group(0) else 0
        commas = "," in match.group(0)
        steps = max(1, int(seconds * FPS / 2))
        for step in range(steps):
            value = number * ease((step + 1) / steps)
            shown = f"{value:,.{decimals}f}" if commas else f"{value:.{decimals}f}"
            text = final[:match.start()] + shown + final[match.end():]
            a, b = start + step * 2 / FPS, start + (step + 1) * 2 / FPS
            rows.append(event(a, b, f"{{\\an7\\pos({x},{y}){tags}}}{escape(text)}", style, 4))
        start += steps * 2 / FPS
    rows.append(event(start, end, f"{{\\an7\\pos({x},{y}){tags}}}{escape(final)}", style, 4))
    return rows


def letterbox(frame_bar: int) -> str:
    return (f",drawbox=x=0:y=0:w=iw:h={frame_bar}:color=black:t=fill,drawbox=x=0:y=ih-{frame_bar}:w=iw:h={frame_bar}:color=black:t=fill"
            if frame_bar else "")


def encode_argv(ffmpeg: Path, frames: int, encoder: str, output: Path, *, ass: Sequence[str] = (), bar: int = 0,
                size: tuple[int, int] = (WIDTH, HEIGHT)) -> list[str]:
    """Raw RGB frames of ``size`` on stdin to one 1080p slice: scaled up, the ASS files drawn in order, the letterbox bars."""
    scale = "null" if size == (WIDTH, HEIGHT) else f"scale={WIDTH}:{HEIGHT}:flags=lanczos"
    chain = "".join(f",subtitles={name}" for name in ass) + letterbox(bar)
    return [str(ffmpeg), "-hide_banner", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-", "-vf", f"{scale},format=rgb24{chain},fps={FPS},format=yuv420p,setsar=1",
            "-frames:v", str(frames), "-an", *h264.rate_args(encoder, BITRATE), *resources.ffmpeg_thread_args(),
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "15360", "-movflags", "+faststart", "-f", "mp4", str(output)]


def pipe_runner(frames: FrameSource) -> Callable[[list[str], Path, Callable[[], bool]], None]:
    """A runner (``run_ffmpeg``'s shape) that feeds ``frames()`` to FFmpeg's stdin."""
    def run(argv: list[str], cwd: Path, cancelled: Callable[[], bool]) -> None:
        log = cwd / "ffmpeg-error.log"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log.open("wb") as errors:
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags)
            try:
                assert process.stdin is not None
                for frame in frames():
                    if cancelled():
                        process.kill()
                        process.wait()
                        raise Cancelled()
                    process.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            deadline = time.monotonic() + 600
            while process.poll() is None:
                if cancelled() or time.monotonic() > deadline:
                    process.kill()
                    process.wait()
                    if cancelled():
                        raise Cancelled()
                    raise ProductError("Dựng cảnh đồ họa quá lâu nên đã dừng")
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        if process.returncode != 0:
            detail = " ".join(log.read_bytes().decode("utf-8", "replace").split())[-300:]
            raise ProductError(f"Dựng cảnh đồ họa thất bại: {detail or 'FFmpeg lỗi'}")
    return run


def solid(hex_rgb: str) -> np.ndarray:
    value = hex_rgb.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def vignette(strength: float = 0.45, width: int = WIDTH, height: int = HEIGHT) -> np.ndarray:
    """A float32 (H, W, 1) multiplier, 1 in the middle, ``1 - strength`` in the corners."""
    ys = (np.arange(height, dtype=np.float32) - height / 2) / (height / 2)
    xs = (np.arange(width, dtype=np.float32) - width / 2) / (width / 2)
    radius = np.sqrt(xs[None, :] ** 2 * 0.8 + ys[:, None] ** 2 * 0.9)
    return (1 - strength * np.clip(radius - 0.35, 0, 1) ** 1.5)[:, :, None].astype(np.float32)


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    """A fast separable box blur (three passes look Gaussian) of a float32 (H, W, C) image."""
    out = image.astype(np.float32)
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0)] * out.ndim
            pad[axis] = (radius + 1, radius)
            padded = np.pad(out, pad, mode="edge")
            summed = np.cumsum(padded, axis=axis, dtype=np.float32)
            upper = np.take(summed, np.arange(2 * radius + 1, summed.shape[axis]), axis=axis)
            lower = np.take(summed, np.arange(0, summed.shape[axis] - 2 * radius - 1), axis=axis)
            out = (upper - lower) / (2 * radius + 1)
    return out


def resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinear resize of an (H, W, C) image."""
    h, w = image.shape[:2]
    ys = np.clip((np.arange(height, dtype=np.float32) + 0.5) * h / height - 0.5, 0, h - 1)
    xs = np.clip((np.arange(width, dtype=np.float32) + 0.5) * w / width - 0.5, 0, w - 1)
    return sample(image.astype(np.float32), xs, ys)


def sample(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Bilinear samples of a float32 (H, W, C) image on the grid ``ys`` × ``xs`` (pixel coordinates, clamped)."""
    h, w = image.shape[:2]
    ys = np.clip(ys, 0, h - 1)
    xs = np.clip(xs, 0, w - 1)
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy = (ys - y0)[:, None, None]
    rows = image[y0] * (1 - fy) + image[y1] * fy
    fx = (xs - x0)[None, :, None]
    return rows[:, x0] * (1 - fx) + rows[:, x1] * fx


def read_frame(ffmpeg: Path, video: Path, at: float, run: Callable[[list[str], Path, Callable[[], bool]], None], work: Path,
               cancelled: Callable[[], bool]) -> np.ndarray | None:
    """One frame of a source at ``at`` seconds as a (1080, 1920, 3) float32 picture, or None when it cannot be read."""
    raw = work / "frame.rgb"
    raw.unlink(missing_ok=True)
    try:
        run([str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", raw.name], work, cancelled)
        data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
    except (ProductError, OSError):
        return None
    finally:
        raw.unlink(missing_ok=True)
    if data.size != WIDTH * HEIGHT * 3:
        return None
    return data.reshape(HEIGHT, WIDTH, 3).astype(np.float32)


def frames_of(make: Callable[[int], np.ndarray], count: int) -> FrameSource:
    def frames() -> Iterator[np.ndarray]:
        for index in range(count):
            yield make(index)
    return frames


def join(rows: Iterable[str]) -> str:
    return "".join(rows)
