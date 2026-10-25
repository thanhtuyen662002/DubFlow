"""Find burned-in source captions over time and turn them into blur regions.

A PP-OCRv4 text detector (ONNX, CPU) runs on sampled frames of the lower
part of the picture. Detected lines are tracked over time and sorted into:

* captions: near the horizontal centre of the lower frame and changing as the
  dialogue changes, so they are blurred while they are on screen;
* fixed text: a logo, watermark or channel name that stays put with the same
  pixels for most of the video, which is kept;
* everything else (signs and scene text away from the caption area), kept.

The pure functions below take plain arrays and boxes so they can be tested
without the model; :class:`CaptionScanner` wires in FFmpeg and ONNX Runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

import numpy as np

from engine.dubflow.media.adapter import MAX_BLUR_REGIONS, BlurRegion

SCAN_RECIPE = "ppocrv4-det-lowerband-v1"
SAMPLE_FPS = 2.0
ANALYSIS_WIDTH = 640
# Only the lower part of the frame is analysed: burned-in dialogue sits there,
# and skipping the rest halves the detector's work.
BAND_TOP = 0.45
PROB_THRESHOLD = 0.3
UNCLIP_RATIO = 1.6
CAPTION_MIN_CENTER_Y = 0.55
CAPTION_MAX_CENTER_OFFSET = 0.2
# Fixed text this close to the centre is a caption that never changes, not a logo.
CENTERED_OFFSET = 0.08
# A caption segment must overlap recognised speech for at least this share of
# its time, which drops HUD counters, logos and title cards shown without speech.
MIN_SPEECH_OVERLAP = 0.3
FIXED_MIN_PRESENCE = 0.5
FIXED_MAX_CHANGE = 6.0
SEGMENT_GAP_S = 0.6
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


@dataclass(frozen=True)
class TextBox:
    """One detected text line, in fractions of the full frame."""

    x: float
    y: float
    w: float
    h: float
    signature: tuple[float, ...] = ()

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True)
class ScanResult:
    regions: tuple[BlurRegion, ...]
    samples: int
    caption_samples: int
    fixed_text: tuple[TextBox, ...]

    def summary(self) -> dict:
        return {"recipe": SCAN_RECIPE, "samples": self.samples, "caption_samples": self.caption_samples,
                "regions": len(self.regions), "kept_fixed_text": [{"x": round(b.x, 4), "y": round(b.y, 4), "w": round(b.w, 4), "h": round(b.h, 4)} for b in self.fixed_text]}


def line_boxes(prob: np.ndarray, *, threshold: float = PROB_THRESHOLD) -> list[tuple[int, int, int, int]]:
    """Pixel boxes (left, top, right, bottom) of horizontal text lines in a DB probability map.

    Rows with text form line bands; inside a band, columns closer than one line
    height join into one line. Each box is then grown like DBNet's "unclip",
    because the detector predicts a shrunken text kernel.
    """
    mask = prob > threshold
    height, width = mask.shape
    rows = mask.sum(1) > 2
    edges = np.flatnonzero(np.diff(np.concatenate(([0], rows.astype(np.int8), [0]))))
    boxes = []
    for top, bottom in zip(edges[::2], edges[1::2]):
        if bottom - top < 2:
            continue
        columns = np.flatnonzero(mask[top:bottom].any(0))
        if columns.size == 0:
            continue
        splits = np.flatnonzero(np.diff(columns) > max(2, bottom - top))
        starts = np.concatenate(([columns[0]], columns[splits + 1]))
        ends = np.concatenate((columns[splits], [columns[-1]])) + 1
        for left, right in zip(starts, ends):
            w, h = right - left, bottom - top
            if w < 4:
                continue
            grow = w * h * UNCLIP_RATIO / (2 * (w + h))
            boxes.append((max(0, int(left - grow)), max(0, int(top - grow)), min(width, int(right + grow + 0.999)), min(height, int(bottom + grow + 0.999))))
    return boxes


def signature(gray: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, ...]:
    """A coarse 16x4 brightness grid of a box, to tell changing captions from fixed text."""
    left, top, right, bottom = box
    crop = gray[top:bottom, left:right]
    if crop.size == 0:
        return ()
    ys = np.linspace(0, crop.shape[0] - 1, 4).astype(int)
    xs = np.linspace(0, crop.shape[1] - 1, 16).astype(int)
    return tuple(float(v) for v in crop[np.ix_(ys, xs)].ravel())


def _iou(a: TextBox, b: TextBox) -> float:
    left, top = max(a.x, b.x), max(a.y, b.y)
    right, bottom = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def _change(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 255.0
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def fixed_text(samples: Sequence[Sequence[TextBox]]) -> list[TextBox]:
    """Boxes that stay in one place with the same pixels for most samples (logos, watermarks)."""
    clusters: list[list[TextBox]] = []
    for boxes in samples:
        for box in boxes:
            for cluster in clusters:
                if _iou(cluster[-1], box) > 0.5:
                    cluster.append(box)
                    break
            else:
                clusters.append([box])
    total = max(1, len(samples))
    fixed = []
    for cluster in clusters:
        if len(cluster) / total < FIXED_MIN_PRESENCE:
            continue
        changes = [_change(a.signature, b.signature) for a, b in zip(cluster, cluster[1:])]
        if not changes or float(np.median(changes)) <= FIXED_MAX_CHANGE:
            fixed.append(cluster[len(cluster) // 2])
    return fixed


def is_caption(box: TextBox) -> bool:
    return box.center_y >= CAPTION_MIN_CENTER_Y and abs(box.center_x - 0.5) <= CAPTION_MAX_CENTER_OFFSET and box.w >= box.h * 1.2


def _speech_share(start_s: float, end_s: float, speech_ms: Sequence[tuple[int, int]]) -> float:
    covered = sum(max(0.0, min(end_s, b / 1000) - max(start_s, a / 1000)) for a, b in speech_ms)
    return covered / max(1e-6, end_s - start_s)


def plan_regions(samples: Sequence[tuple[float, Sequence[TextBox]]], duration_s: float, *, fps: float = SAMPLE_FPS,
                 speech_ms: Sequence[tuple[int, int]] | None = None) -> ScanResult:
    """Time-bounded blur regions covering captions while they are on screen.

    ``speech_ms`` (ASR cue spans) is optional; when given, only caption
    segments that overlap speech are blurred.
    """
    fixed = [box for box in fixed_text([boxes for _, boxes in samples])
             if not (is_caption(box) and abs(box.center_x - 0.5) <= CENTERED_OFFSET)]
    half = 0.5 / fps
    # One track per text line: [start, end, left, top, right, bottom]. A line
    # continues while something stays on screen at its height, so consecutive
    # captions in one place form one segment and gaps between them stay sharp.
    open_tracks: list[list[float]] = []
    segments: list[list[float]] = []
    caption_samples = 0
    for time_s, boxes in samples:
        captions = [box for box in boxes if is_caption(box) and not any(_iou(box, known) > 0.5 for known in fixed)]
        caption_samples += bool(captions)
        segments.extend(track for track in open_tracks if time_s - half - track[1] > SEGMENT_GAP_S)
        open_tracks = [track for track in open_tracks if time_s - half - track[1] <= SEGMENT_GAP_S]
        for box in captions:
            top, bottom = box.y, box.y + box.h
            track = next((track for track in open_tracks
                          if min(bottom, track[5]) - max(top, track[3]) >= 0.5 * min(box.h, track[5] - track[3])), None)
            if track is None:
                open_tracks.append([time_s - half, time_s + half, box.x, top, box.x + box.w, bottom])
            else:
                track[1] = time_s + half
                track[2], track[3], track[4], track[5] = min(track[2], box.x), min(track[3], top), max(track[4], box.x + box.w), max(track[5], bottom)
    segments.extend(open_tracks)
    segments.sort(key=lambda segment: segment[0])
    # Too many short segments: merge the closest neighbours in time.
    while len(segments) > MAX_BLUR_REGIONS:
        index = min(range(len(segments) - 1), key=lambda i: segments[i + 1][0] - segments[i][1])
        a, b = segments[index], segments.pop(index + 1)
        segments[index] = [a[0], b[1], min(a[2], b[2]), min(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5])]
    if speech_ms is not None:
        segments = [segment for segment in segments if _speech_share(segment[0], segment[1], speech_ms) >= MIN_SPEECH_OVERLAP]
    regions = []
    end_limit = max(0.001, duration_s)
    for start, end, left, top, right, bottom in segments:
        # Pad so anti-aliased edges and outlines are covered too.
        pad_x, pad_y = 0.015, (bottom - top) * 0.25
        left, right = max(0.0, left - pad_x), min(1.0, right + pad_x)
        top, bottom = max(0.0, top - pad_y), min(1.0, bottom + pad_y)
        start_ms, end_ms = int(max(0.0, start) * 1000), int(min(end_limit, end) * 1000)
        if end_ms <= start_ms or right - left < 0.01 or bottom - top < 0.01:
            continue
        regions.append(BlurRegion(round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4), start_ms, end_ms))
    return ScanResult(tuple(regions), len(samples), caption_samples, tuple(fixed))


class CaptionScanner:
    """Runs the detector over FFmpeg-decoded frames of one video."""

    def __init__(self, model_path: Path, ffmpeg_path: Path, *, threads: int = 4):
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.ffmpeg = ffmpeg_path

    def _frames(self, source: Path) -> Iterator[tuple[float, np.ndarray]]:
        """Sampled frames as RGB arrays, already rotated for display by FFmpeg.

        PPM frames carry their own size, so no probe of rotation or sample
        aspect ratio is needed.
        """
        command = [str(self.ffmpeg), "-nostdin", "-v", "error", "-i", str(source), "-an", "-sn", "-dn",
                   "-vf", f"fps={SAMPLE_FPS},scale={ANALYSIS_WIDTH}:-2", "-c:v", "ppm", "-f", "image2pipe", "pipe:1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        index = 0
        try:
            while True:
                header = _read_ppm_header(process.stdout)
                if header is None:
                    break
                width, height = header
                data = process.stdout.read(width * height * 3)
                if len(data) < width * height * 3:
                    break
                yield index / SAMPLE_FPS, np.frombuffer(data, np.uint8).reshape(height, width, 3)
                index += 1
        finally:
            process.stdout.close()
            process.kill()
            process.wait()

    def detect(self, rgb: np.ndarray, band_top: float, band_height: float) -> list[TextBox]:
        tensor = ((rgb.astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1)[None]
        prob = self.session.run(None, {self.input_name: tensor})[0][0, 0]
        gray = rgb.mean(2)
        height, width = prob.shape
        return [TextBox(left / width, band_top + band_height * top / height, (right - left) / width,
                        band_height * (bottom - top) / height, signature(gray, (left, top, right, bottom)))
                for left, top, right, bottom in line_boxes(prob)]

    def scan(self, source: Path, *, duration_s: float, speech_ms: Sequence[tuple[int, int]] | None = None,
             progress: Callable[[float], None] | None = None) -> ScanResult:
        samples = []
        expected = max(1.0, duration_s * SAMPLE_FPS)
        for time_s, frame in self._frames(source):
            height = frame.shape[0]
            band = max(32, int(height * (1 - BAND_TOP)) // 32 * 32)
            top = max(0, height - band)
            samples.append((time_s, self.detect(frame[top:], top / height, (height - top) / height)))
            if progress and len(samples) % 10 == 0:
                progress(min(1.0, len(samples) / expected))
        return plan_regions(samples, duration_s, speech_ms=speech_ms)


def _read_ppm_header(stream) -> tuple[int, int] | None:
    """Width and height from a binary PPM (P6) header, or None at end of stream."""
    fields: list[bytes] = []
    token = b""
    while len(fields) < 4:
        char = stream.read(1)
        if not char:
            return None
        if char.isspace():
            if token:
                fields.append(token)
                token = b""
        else:
            token += char
    if fields[0] != b"P6" or fields[3] != b"255":
        raise ValueError("unexpected frame format from FFmpeg")
    return int(fields[1]), int(fields[2])
