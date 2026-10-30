"""Pure crop/ASS plans, sharing compilation's vertical panel and encoding."""
from __future__ import annotations

from pathlib import Path
import textwrap

from engine.dubflow.compilation.render import ENCODER, FPS, HEIGHT, WIDTH, available_blur, clip_argv, encoding
from engine.dubflow.compilation.layout import ass_document as panel_ass, geometry
from engine.dubflow.package.render import STEREO, Video, seconds, start
from engine.dubflow.product.layout import _text, _time

MODES = ("bang-thong-tin", "cat-ngang")


def crop_y(focus_start: float, focus_end: float, duration_ms: int) -> str:
    """Percent of free vertical travel; t starts at zero after the optional trim."""
    if duration_ms <= 0 or not all(0 <= v <= 100 for v in (focus_start, focus_end)):
        raise ValueError("invalid crop focus")
    if focus_start == focus_end:
        return f"(ih-oh)*{focus_start / 100:.6f}"
    return (f"(ih-oh)*({focus_start / 100:.6f}+({(focus_end - focus_start) / 100:.6f})"
            f"*min(t/{seconds(duration_ms)},1))")


def title_ass(title: str, duration_ms: int) -> str:
    # A small top-left box; ASS escaping prevents text becoming filter/drawing code.
    document = panel_ass("", duration_ms, card=True)
    header = document.split("Dialogue:", 1)[0]
    if not title:
        return header
    lines = textwrap.wrap(title, width=46)
    text = r"\N".join(_text(line) for line in lines)
    width, height = min(950, max(map(len, lines)) * 19 + 30), len(lines) * 40 + 20
    end = _time(duration_ms, ass=True)
    return header + (f"Dialogue: 0,0:00:00.00,{end},Text,,0,0,0,,"
                     f"{{\\an7\\pos(25,25)\\bord0\\shad0\\1c&H352A17&\\1a&H30&\\p1}}"
                     f"m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}\n"
                     f"Dialogue: 1,0:00:00.00,{end},Text,,0,0,0,,"
                     f"{{\\an7\\pos(40,35)\\fs32\\bord2\\shad0}}{text}\n")


def crop_argv(ffmpeg: Path, source: Path, start_ms: int, duration_ms: int, focus_start: float,
              focus_end: float, title: bool, output: Path, *, encoder: str = ENCODER) -> list[str]:
    graph = (f"[0:v]setpts=PTS-STARTPTS,crop=w=iw:h=iw*9/16:x=0:y='{crop_y(focus_start, focus_end, duration_ms)}':exact=1,"
             f"scale={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p"
             + (",subtitles=panel.ass" if title else "") + "[video];"
             f"[0:a:0]{STEREO},apad,atrim=duration={seconds(duration_ms)},asetpts=PTS-STARTPTS[audio]")
    return (start(ffmpeg) + ["-ss", seconds(start_ms), "-i", str(source), "-filter_complex", graph,
            "-map", "[video]", "-map", "[audio]", "-t", seconds(duration_ms)] + encoding(encoder) + [str(output)])
