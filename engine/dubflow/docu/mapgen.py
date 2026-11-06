"""The satellite fly-in (Tier-3): the script's ``target_Coordinates_3D`` as a 4–5 s "recon" shot.

The script has carried the site's coordinates since phase 2a, unused. When they
are valid, the visual plan reserves the second slice of the hook (right after
the opening shot) for this picture, and the rough cut draws it:

* Esri World Imagery tiles (free, no key) around the site at three zoom levels,
  6×6 tiles each: zoom 12 for the region, 14 between, 16 for the site's roofs;
  fetched once and kept in the project (``.dubflow-work/map/tiles``);
* graded cold and dark (desaturated, dark teal) with a vignette;
* a smooth exponential fly-in from the region straight down onto the site, the
  camera tilted at first and levelling out (a cheap 3D feel), each zoom level
  crossfading into the sharper one as it takes over;
* a technical HUD drawn by libass: a faint reticle grid, corner brackets, a
  target ring closing on the site, the GPS readout (``30.3907° N,
  114.8946° E``), the place name, a falling altitude readout and the imagery
  credit.

Offline (no tiles at plan or render time) the same HUD sits on a dark radar /
vector chart with a turning sweep, so a render never fails for want of a
network.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.request import Request, urlopen

import numpy as np

from . import graphics
from .graphics import FPS, HEIGHT, WIDTH

# The imagery is drawn at half size and scaled up by FFmpeg (it is soft anyway); the HUD is drawn at full size.
DRAW_W, DRAW_H = WIDTH // 2, HEIGHT // 2

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
CREDIT = "Imagery © Esri, Maxar, Earthstar Geographics"
LEVELS = (12, 14, 16)
GRID = 6
TILE = 256
MIN_SECONDS, MAX_SECONDS, SECONDS = 4.0, 5.0, 4.5
# Downloads give up after this many failures in a row (no network: fall back fast).
GIVE_UP = 3
TIMEOUT = 8.0
USER_AGENT = "DubFlow documentary map (desktop app)"
RECIPE = "map-flyin-v1"

Fetch = Callable[[str, float], bytes]


def target(script: dict[str, Any]) -> dict[str, Any] | None:
    """{lat, lon, label} from the script's ``target_Coordinates_3D`` ({lat, lng, location_name} or [lat, lon]), or None."""
    value = script.get("target_Coordinates_3D")
    label = ""
    if isinstance(value, dict):
        lat, lon = value.get("lat"), value.get("lng", value.get("lon"))
        label = str(value.get("location_name") or "").strip()[:60]
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lat, lon = value[0], value[1]
    else:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not -85 <= lat <= 85 or not -180 <= lon <= 180 or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        return None
    return {"lat": round(lat, 5), "lon": round(lon, 5), "label": label}


def readout(lat: float, lon: float) -> str:
    """``30.3907° N, 114.8946° E``."""
    return f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"


def tile_position(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """The point in tile units of the Web Mercator grid at ``zoom``."""
    scale = 2 ** zoom
    x = (lon + 180.0) / 360.0 * scale
    rad = math.radians(lat)
    y = (1 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2 * scale
    return x, y


def origin(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """The top-left tile of the GRID×GRID mosaic around the point."""
    x, y = tile_position(lat, lon, zoom)
    return int(math.floor(x)) - GRID // 2 + 1, int(math.floor(y)) - GRID // 2 + 1


def _http(url: str, timeout: float) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:  # noqa: S310 (fixed https host)
        return response.read(2_000_000)


def tile_folder(work: Path) -> Path:
    return work / "map" / "tiles"


def fetch_tiles(lat: float, lon: float, folder: Path, fetch: Fetch | None = None, *, cancelled: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Every tile of the three mosaics into ``folder`` (kept: fetched once); {zoom: tiles present} and whether it went online."""
    fetch = fetch or _http
    present: dict[str, int] = {}
    misses = 0
    offline = False
    for zoom in LEVELS:
        x0, y0 = origin(lat, lon, zoom)
        count = 0
        for dy in range(GRID):
            for dx in range(GRID):
                path = folder / str(zoom) / f"{x0 + dx}_{y0 + dy}.jpg"
                if path.is_file() and path.stat().st_size > 0:
                    count += 1
                    continue
                if offline or cancelled():
                    continue
                try:
                    data = fetch(TILE_URL.format(z=zoom, y=y0 + dy, x=x0 + dx), TIMEOUT)
                except Exception:  # noqa: BLE001 (no network, a refused tile: the map falls back, never fails)
                    misses += 1
                    offline = misses >= GIVE_UP and count == 0
                    continue
                if not data.startswith(b"\xff\xd8"):
                    continue
                misses = 0
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(".partial")
                partial.write_bytes(data)
                partial.replace(path)
                count += 1
        present[str(zoom)] = count
    return {"tiles": present, "satellite": all(present.get(str(zoom), 0) >= GRID * GRID * 3 // 4 for zoom in (LEVELS[0], LEVELS[-1]))}


def mosaic_argv(ffmpeg: Path, folder: Path, lat: float, lon: float, zoom: int, output: Path) -> list[str] | None:
    """One FFmpeg call that stacks the zoom level's tiles (dark where one is missing) into raw RGB."""
    x0, y0 = origin(lat, lon, zoom)
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]
    labels, layout, found = [], [], 0
    graph = []
    for dy in range(GRID):
        for dx in range(GRID):
            index = dy * GRID + dx
            path = folder / str(zoom) / f"{x0 + dx}_{y0 + dy}.jpg"
            if path.is_file() and path.stat().st_size > 0:
                argv += ["-i", str(path)]
                found += 1
            else:
                argv += ["-f", "lavfi", "-i", f"color=c=0x0b141a:s={TILE}x{TILE}:d=1"]
            graph.append(f"[{index}:v]scale={TILE}:{TILE},format=rgb24,setsar=1[t{index}]")
            labels.append(f"[t{index}]")
            layout.append(f"{dx * TILE}_{dy * TILE}")
    if not found:
        return None
    graph.append(f"{''.join(labels)}xstack=inputs={GRID * GRID}:layout={'|'.join(layout)}[out]")
    return argv + ["-filter_complex", ";".join(graph), "-map", "[out]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(output)]


def grade(image: np.ndarray) -> np.ndarray:
    """Cold, dark and desaturated: the satellite look of an engineering documentary."""
    picture = image.astype(np.float32) / 255.0
    luma = picture @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    picture = luma[..., None] + 0.42 * (picture - luma[..., None])
    picture = picture * np.array([0.78, 0.95, 1.0], dtype=np.float32) + np.array([0.0, 0.018, 0.03], dtype=np.float32)
    picture = np.clip(picture, 0, 1) ** 1.18 * 0.9
    return (picture * 255).astype(np.float32)


def load_mosaics(ffmpeg: Path, work: Path, lat: float, lon: float, run: Callable, cancelled: Callable[[], bool]) -> dict[int, np.ndarray]:
    """{zoom: graded (H, W, 3) float32 mosaic} for each level that has tiles."""
    folder = tile_folder(work)
    found: dict[int, np.ndarray] = {}
    for zoom in LEVELS:
        raw = work / "map" / f"mosaic-{zoom}.rgb"
        argv = mosaic_argv(ffmpeg, folder, lat, lon, zoom, raw)
        if argv is None:
            continue
        try:
            run(argv, work, cancelled)
            data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
        except Exception:  # noqa: BLE001 (a broken tile: that level is left out)
            continue
        finally:
            raw.unlink(missing_ok=True)
        size = GRID * TILE
        if data.size == size * size * 3:
            found[zoom] = grade(data.reshape(size, size, 3))
    return found


def camera(index: int, count: int) -> tuple[float, float]:
    """(ground width in zoom-16 pixels, tilt) of frame ``index``: a fast fall that slows onto the site, then a slight drift."""
    far = GRID * TILE * 2 ** (LEVELS[-1] - LEVELS[0]) * 0.72
    near = GRID * TILE * 0.82
    progress = index / max(1, count - 1)
    travel = graphics.ease(min(1.0, progress / 0.82))
    width = far * (near / far) ** travel
    # The last part keeps creeping in, so the shot never freezes.
    width *= 1 - 0.06 * max(0.0, progress - 0.82) / 0.18
    tilt = 0.28 * (1 - travel)
    return width, tilt


def _level_sample(mosaic: np.ndarray, zoom: int, centre: tuple[float, float], width16: float, tilt: float) -> np.ndarray:
    """The frame (DRAW_W × DRAW_H) sampled from one mosaic: ground ``width16`` (zoom-16 pixels) across, the top rows
    further away while the camera is tilted."""
    factor = 2 ** (LEVELS[-1] - zoom)
    cx, cy = centre
    scale = np.float32(width16 / factor / DRAW_W)
    ys = np.arange(DRAW_H, dtype=np.float32) - DRAW_H / 2
    xs = np.arange(DRAW_W, dtype=np.float32) - DRAW_W / 2
    if tilt <= 1e-3:
        return graphics.sample(mosaic, cx + xs * scale, cy + ys * scale)
    # A tilted camera: rows near the top are further away, so they show more ground and are squeezed together.
    depth = (1 + tilt * (-ys / (DRAW_H / 2))).astype(np.float32)
    steps = scale * depth
    rows_y = cy + np.cumsum(steps) - steps.sum() / 2
    h, w = mosaic.shape[:2]
    rows_y = np.clip(rows_y, 0, h - 1)
    y0 = np.floor(rows_y).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)
    fy = (rows_y - y0)[:, None, None]
    rows = mosaic[y0] * (1 - fy) + mosaic[y1] * fy
    px = np.clip(cx + xs[None, :] * scale * depth[:, None], 0, w - 1)
    x0 = np.floor(px).astype(np.intp)
    x1 = np.minimum(x0 + 1, w - 1)
    fx = (px - x0)[:, :, None]
    line = np.arange(DRAW_H)[:, None]
    return rows[line, x0] * (1 - fx) + rows[line, x1] * fx


def satellite_frames(mosaics: dict[int, np.ndarray], lat: float, lon: float, count: int) -> graphics.FrameSource:
    shade = graphics.vignette(0.55, DRAW_W, DRAW_H)
    centres = {}
    for zoom in mosaics:
        x, y = tile_position(lat, lon, zoom)
        x0, y0 = origin(lat, lon, zoom)
        centres[zoom] = ((x - x0) * TILE, (y - y0) * TILE)
    size = GRID * TILE

    def frame(index: int) -> np.ndarray:
        width16, tilt = camera(index, count)
        # The sharpest level whose mosaic still covers the view, crossfaded with the coarser one as it takes over.
        covering = [zoom for zoom in sorted(mosaics) if width16 / 2 ** (LEVELS[-1] - zoom) * (1 + tilt) <= size * 0.98]
        coarse = [zoom for zoom in sorted(mosaics)]
        chosen = covering[-1] if covering else coarse[0]
        picture = _level_sample(mosaics[chosen], chosen, centres[chosen], width16, tilt)
        coarser = [zoom for zoom in coarse if zoom < chosen]
        if coarser:
            # How far past the switch point this level is: blend over the first 35 % of its range.
            over = size * 0.98 / (width16 / 2 ** (LEVELS[-1] - chosen) * (1 + tilt))
            blend = min(1.0, (over - 1) / 0.35)
            if blend < 1:
                other = _level_sample(mosaics[coarser[-1]], coarser[-1], centres[coarser[-1]], width16, tilt)
                picture = other * (1 - blend) + picture * blend
        return np.clip(picture * shade, 0, 255).astype(np.uint8)
    return graphics.frames_of(frame, count)


def radar_frames(count: int) -> graphics.FrameSource:
    """The offline chart: a dark teal radial field with faint noise; the rings and sweep come from the HUD."""
    ys = (np.arange(DRAW_H, dtype=np.float32) - DRAW_H / 2)[:, None]
    xs = (np.arange(DRAW_W, dtype=np.float32) - DRAW_W / 2)[None, :]
    radius = np.sqrt(xs ** 2 + ys ** 2) / (DRAW_W / 2)
    glow = np.clip(1 - radius, 0, 1)[:, :, None]
    base = graphics.solid("#050C10") + glow * (graphics.solid("#0E2A30") - graphics.solid("#050C10"))
    rng = np.random.default_rng(7)
    grain = rng.normal(0, 2.0, (DRAW_H, DRAW_W, 1)).astype(np.float32)
    picture = np.clip(base + grain, 0, 255).astype(np.uint8)
    return graphics.frames_of(lambda index: picture, count)


TEAL, AMBER, WHITE = "#5FE3D8", "#FAC822", "#FFFFFF"


def hud_ass(lat: float, lon: float, label: str, seconds: float, bar: int, *, satellite: bool) -> str:
    """The HUD: grid, brackets, target ring, readouts and credit, all inside the letterbox."""
    rows: list[str] = []
    top, bottom = bar + 34, HEIGHT - bar - 34
    end = seconds
    grid = []
    for x in range(0, WIDTH + 1, 160):
        grid.append(graphics.rect(x, top - 34, 1, bottom - top + 68))
    for y in range(top - 34 + 40, bottom + 34, 160):
        grid.append(graphics.rect(0, y, WIDTH, 1))
    rows.append(graphics.event(0, end, graphics.drawing(" ".join(grid), f"\\an7\\pos(0,0)\\1c{graphics.colour(TEAL)}\\1a&HD0&\\fad(300,200)"), layer=1))
    # Corner brackets.
    arm, edge = 70, 4
    left, right = 60, WIDTH - 60
    brackets = []
    for x, y, sx, sy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
        brackets.append(graphics.rect(min(x, x + sx * arm), y if sy > 0 else y - edge, arm, edge))
        brackets.append(graphics.rect(x if sx > 0 else x - edge, min(y, y + sy * arm), edge, arm))
    rows.append(graphics.event(0, end, graphics.drawing(" ".join(brackets), f"\\an7\\pos(0,0)\\1c{graphics.colour(TEAL)}\\1a&H30&\\fad(250,200)"), layer=2))
    cx, cy = WIDTH // 2, HEIGHT // 2
    ring_tags = f"\\an5\\pos({cx},{cy})\\1a&HFF&\\3c{graphics.colour(AMBER)}\\bord3\\shad0\\fscx260\\fscy260\\t(0,{int(seconds * 820)},0.6,\\fscx100\\fscy100)\\fad(200,200)"
    rows.append(graphics.event(0.15, end, graphics.drawing(graphics.circle(60, 60, 60), ring_tags), layer=3))
    outer = f"\\an5\\pos({cx},{cy})\\1a&HFF&\\3c{graphics.colour(TEAL)}\\3a&H60&\\bord2\\shad0\\fscx420\\fscy420\\t(0,{int(seconds * 900)},0.7,\\fscx170\\fscy170)\\fad(200,200)"
    rows.append(graphics.event(0.0, end, graphics.drawing(graphics.circle(60, 60, 60), outer), layer=3))
    ticks = " ".join([graphics.rect(cx - 140, cy - 1, 60, 3), graphics.rect(cx + 80, cy - 1, 60, 3),
                      graphics.rect(cx - 1, cy - 140, 3, 60), graphics.rect(cx - 1, cy + 80, 3, 60)])
    rows.append(graphics.event(0.4, end, graphics.drawing(ticks, f"\\an7\\pos(0,0)\\1c{graphics.colour(AMBER)}\\fad(300,200)"), layer=3))
    rows.append(graphics.event(0.0, end, graphics.drawing(graphics.circle(5, cx, cy), f"\\an7\\pos(0,0)\\1c{graphics.colour(AMBER)}"), layer=3))
    source = "SATELLITE RECON  //  ESRI WORLD IMAGERY" if satellite else "VECTOR CHART  //  OFFLINE"
    rows.append(graphics.event(0.1, end, f"{{\\an7\\pos(96,{top + 12})\\fs26\\1c{graphics.colour(TEAL)}\\fad(250,200)}}{graphics.escape(source)}", "Mono", 4))
    rows.append(graphics.event(0.5, end, f"{{\\an1\\pos(96,{bottom - 64})\\fs34\\1c{graphics.colour(AMBER)}\\fad(300,200)}}{graphics.escape(readout(lat, lon))}", "Mono", 4))
    if label:
        rows.append(graphics.event(0.8, end, f"{{\\an1\\pos(96,{bottom - 108})\\fs62\\b1\\1c{graphics.colour(WHITE)}\\fad(350,200)}}{graphics.escape(label.upper())}", "Head", 4))
    rows.append(graphics.event(0.5, end, f"{{\\an3\\pos({WIDTH - 96},{bottom - 18})\\fs22\\1c&HC0C8C8&\\fad(300,200)}}{graphics.escape(CREDIT if satellite else 'Coordinates from the sources')}", "Mono", 4))
    # The altitude readout falls with the camera.
    steps = max(1, int(seconds / 0.2))
    for step in range(steps):
        width16, _ = camera(int(step / steps * seconds * FPS), int(seconds * FPS))
        metres = width16 * 156543.03 * math.cos(math.radians(lat)) / 2 ** LEVELS[-1] * 0.9
        text = f"ALT {metres / 1000:5.1f} KM" if metres >= 1000 else f"ALT {metres:5.0f} M"
        rows.append(graphics.event(step * 0.2, min(end, (step + 1) * 0.2), f"{{\\an9\\pos({WIDTH - 96},{top + 12})\\fs28\\1c{graphics.colour(TEAL)}}}{text}", "Mono", 4))
    rows.append(graphics.event(0.2, end, f"{{\\an9\\pos({WIDTH - 96},{top + 52})\\fs22\\1c&HA0B0B0&\\fad(250,200)}}TARGET LOCK", "Mono", 4))
    if not satellite:
        rings = " ".join(graphics.circle(r, cx, cy) for r in (120, 240, 360))
        rows.append(graphics.event(0, end, graphics.drawing(rings, f"\\an7\\pos(0,0)\\1a&HFF&\\3c{graphics.colour(TEAL)}\\3a&H90&\\bord2\\shad0"), layer=1))
        sweep = f"m 0 0 l 0 -380 b 120 -380 230 -330 300 -240 l 0 0"
        rows.append(graphics.event(0, end, graphics.drawing(sweep, f"\\an7\\pos({cx},{cy})\\org({cx},{cy})\\1c{graphics.colour(TEAL)}\\1a&HB0&\\t(0,{int(seconds * 1000)},\\frz-{int(seconds * 140)})"), layer=1))
    return graphics.ass_head() + "".join(rows)


def build(ffmpeg: Path, work: Path, place: dict[str, Any], frames: int, bar: int, run: Callable,
          cancelled: Callable[[], bool], *, fetch: Fetch | None = None) -> tuple[graphics.FrameSource, str, dict[str, Any]]:
    """The frames, the HUD file name (in ``work``) and a description of what was drawn, for one fly-in slice."""
    lat, lon = float(place["lat"]), float(place["lon"])
    state = fetch_tiles(lat, lon, tile_folder(work), fetch, cancelled=cancelled)
    mosaics = load_mosaics(ffmpeg, work, lat, lon, run, cancelled) if state["satellite"] else {}
    satellite = LEVELS[0] in mosaics and LEVELS[-1] in mosaics
    seconds = frames / FPS
    name = "map-hud.ass"
    (work / name).write_text(hud_ass(lat, lon, str(place.get("label") or ""), seconds, bar, satellite=satellite), encoding="utf-8")
    source = satellite_frames(mosaics, lat, lon, frames) if satellite else radar_frames(frames)
    return source, name, {"satellite": satellite, "tiles": state["tiles"]}


def placement(slot: float, minimum: float) -> tuple[float, float] | None:
    """(offset, length) of the fly-in in the first scene's slot: right after an opening shot of 1.8–6.5 s."""
    for length in (SECONDS, MAX_SECONDS, MIN_SECONDS):
        for before in (5.0, 4.5, 5.5, 4.0, 6.0, 3.5, 3.0, 6.5, 2.5, 2.0, minimum):
            after = slot - before - length
            if before >= minimum and (abs(after) < 1e-6 or after >= minimum):
                return before, length
    return None


def scene_for(scenes: Sequence[dict[str, Any]]) -> int | None:
    """Index of the scene (in narration order) that should show the map: the first hook scene with room, else the
    first that talks about the site's place or ground ("acres", "located", the province)."""
    words = ("acre", "located", "province", "footprint", "site", "square feet", "city", "where")
    for index, scene in enumerate(scenes[:3]):
        if scene.get("chapter_type") == "HOOK" and scene.get("slot", 0) >= MIN_SECONDS + 2 * 1.8:
            return index
    for index, scene in enumerate(scenes):
        text = str(scene.get("narration_text") or "").casefold()
        if any(word in text for word in words) and scene.get("slot", 0) >= MIN_SECONDS + 1.8:
            return index
    return None


__all__ = ["build", "fetch_tiles", "placement", "readout", "scene_for", "target", "tile_position"]
