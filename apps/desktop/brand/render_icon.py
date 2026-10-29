"""Render the DubFlow logo (dubflow-logo.svg) into the Windows app icon.

The geometry below mirrors dubflow-logo.svg on its 256-unit grid. Each size is
drawn 8x larger and downsampled. Sizes up to 24 px keep only the middle wave
bar, so the mark stays legible in a taskbar or title bar.

    python apps/desktop/brand/render_icon.py   (needs Pillow; dev tool only)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
ICON = ROOT / "apps/desktop/src-tauri/icons/icon.ico"
PREVIEW = Path(__file__).with_name("dubflow-logo-256.png")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
INDIGO, WHITE, CORAL = (79, 70, 229, 255), (255, 255, 255, 255), (249, 115, 102, 255)
SCALE = 8


def _cubic(p0, p1, p2, p3, steps=48):
    for index in range(1, steps + 1):
        t = index / steps
        u = 1 - t
        yield tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                    for a, b, c, d in zip(p0, p1, p2, p3))


def _quadratic(p0, p1, p2, steps=24):
    for index in range(1, steps + 1):
        t = index / steps
        u = 1 - t
        yield tuple(u * u * a + 2 * u * t * b + t * t * c for a, b, c in zip(p0, p1, p2))


def _bubble():
    """M84 52 H128 C176 52 206 86 206 128 S176 204 128 204 H100 L62 232 V74 Q62 52 84 52 Z"""
    points = [(84, 52), (128, 52)]
    points += _cubic((128, 52), (176, 52), (206, 86), (206, 128))
    points += _cubic((206, 128), (206, 170), (176, 204), (128, 204))  # S reflects (206, 86).
    points += [(100, 204), (62, 232), (62, 74)]
    points += _quadratic((62, 74), (62, 52), (84, 52))
    return points


def render(size: int) -> Image.Image:
    canvas = size * SCALE
    unit = canvas / 256
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, canvas - 1, canvas - 1), radius=56 * unit, fill=INDIGO)
    draw.polygon([(x * unit, y * unit) for x, y in _bubble()], fill=WHITE)
    bars = [(125, 86, 18, 84, INDIGO)]
    if size > 24:
        bars = [(98, 104, 18, 48, INDIGO), *bars, (152, 108, 18, 40, CORAL)]
    for x, y, width, height, color in bars:
        draw.rounded_rectangle((x * unit, y * unit, (x + width) * unit, (y + height) * unit), radius=9 * unit, fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    images = [render(size) for size in SIZES]
    images[-1].save(ICON, format="ICO", sizes=[(size, size) for size in SIZES], append_images=images[:-1])
    images[-1].save(PREVIEW, format="PNG", optimize=True)
    print(f"wrote {ICON.relative_to(ROOT)} ({ICON.stat().st_size} bytes) and {PREVIEW.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
