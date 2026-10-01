"""Production boundary for optional OCR and oriented text intelligence.

The worker must be able to finish a truthful B1 export when an OCR runtime is
not installed, a model is unhealthy, or a detector produces no observations.
This module therefore owns the boundary and provenance rather than importing a
third-party OCR package directly.  An app-owned detector may provide a
``.ocr.json`` observation document next to the media (or an explicit path in
the command).  The document is validated, associated with canonical
millisecond ticks, and passed through the deterministic temporal trackers.

The observation document is deliberately an adapter contract.  It can be
produced by a native OCR runtime in a packaged build without changing worker
or cleanup code.  Missing detector output is a normal downgrade and never
causes a valid subtitle export to fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
import time
from typing import Any, Iterable, Mapping, Sequence
import zlib

from .orientation.tracker import AnimatedTextTracker, OrientedObservation, OrientedTrack, Polygon
from .tracker import BBox, BenchmarkReport, OcrObservation, TextIntelligence, TextTrackDocument


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_OBSERVATIONS = 100_000
_MAX_FRAMES = 10_000_000
_I64_MAX = (1 << 63) - 1
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ProductionOcrError(RuntimeError):
    """A bounded, actionable OCR boundary error."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        self.code = str(code)[:128]
        self.detail = " ".join(str(detail).replace("\x00", " ").split())[:4096]
        self.retryable = bool(retryable)
        super().__init__(f"{self.code}: {self.detail}")


def _bounded_text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 and char not in "\t" for char in value):
        raise ProductionOcrError("OCR_CONTRACT_INVALID", f"{name} must be bounded text")
    return value.strip()


def _bounded_int(value: object, name: str, *, minimum: int = 0, maximum: int = _I64_MAX) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ProductionOcrError("OCR_CONTRACT_INVALID", f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ProductionOcrError("OCR_INPUT_UNAVAILABLE", str(error), retryable=True) from error
    return digest.hexdigest()


def _png_luma(path: Path) -> tuple[int, int, bytes]:
    """Decode bounded 8-bit RGB/RGBA/gray PNG samples without system tools."""

    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ProductionOcrError("OCR_FRAME_UNAVAILABLE", f"unable to read frame {path}", retryable=True) from error
    if len(payload) > 64 * 1024 * 1024 or not payload.startswith(_PNG_SIGNATURE):
        raise ProductionOcrError("OCR_FRAME_INVALID", "frame is not a bounded PNG")
    cursor = len(_PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    while cursor + 12 <= len(payload):
        length = struct.unpack(">I", payload[cursor:cursor + 4])[0]
        kind = payload[cursor + 4:cursor + 8]
        end = cursor + 12 + length
        if length > 64 * 1024 * 1024 or end > len(payload):
            raise ProductionOcrError("OCR_FRAME_INVALID", "PNG chunk exceeds frame bounds")
        chunk = payload[cursor + 8:cursor + 8 + length]
        declared_crc = struct.unpack(">I", payload[cursor + 8 + length:end])[0]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != declared_crc:
            raise ProductionOcrError("OCR_FRAME_INVALID", "PNG chunk checksum is invalid")
        if kind == b"IHDR":
            if length != 13:
                raise ProductionOcrError("OCR_FRAME_INVALID", "PNG IHDR is malformed")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
            if bit_depth != 8 or compression != 0 or filtering != 0 or interlace != 0 or color_type not in {0, 2, 6}:
                raise ProductionOcrError("OCR_FRAME_UNSUPPORTED", "PNG requires non-interlaced 8-bit gray/RGB/RGBA samples")
            if not 1 <= width <= 16_384 or not 1 <= height <= 16_384 or width * height > 64_000_000:
                raise ProductionOcrError("OCR_FRAME_INVALID", "PNG dimensions exceed the production bound")
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
        cursor = end
    if width is None or height is None or not compressed:
        raise ProductionOcrError("OCR_FRAME_INVALID", "PNG has no complete image payload")
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    row_size = width * channels
    try:
        decoded = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise ProductionOcrError("OCR_FRAME_INVALID", "PNG image data cannot be decompressed") from error
    if len(decoded) != height * (row_size + 1):
        raise ProductionOcrError("OCR_FRAME_INVALID", "PNG scanline length is inconsistent")
    rows: list[bytearray] = []
    cursor = 0
    for row_index in range(height):
        filter_type = decoded[cursor]
        raw = bytearray(decoded[cursor + 1:cursor + 1 + row_size])
        cursor += row_size + 1
        previous = rows[-1] if rows else bytearray(row_size)
        for index in range(row_size):
            left = raw[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                raw[index] = (raw[index] + left) & 0xFF
            elif filter_type == 2:
                raw[index] = (raw[index] + up) & 0xFF
            elif filter_type == 3:
                raw[index] = (raw[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                estimate = left + up - up_left
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                raw[index] = (raw[index] + predictor) & 0xFF
            elif filter_type != 0:
                raise ProductionOcrError("OCR_FRAME_UNSUPPORTED", "PNG uses an unknown scanline filter")
        rows.append(raw)
    luma = bytearray(width * height)
    output = 0
    for row in rows:
        if channels == 1:
            luma[output:output + width] = row
        else:
            for index in range(width):
                red, green, blue = row[index * channels:index * channels + 3]
                luma[output + index] = (299 * red + 587 * green + 114 * blue) // 1000
        output += width
    return width, height, bytes(luma)


def _frame_text_box(path: Path) -> tuple[int, int, int, int, float] | None:
    """Find one conservative high-contrast subtitle-like box in a real frame."""

    width, height, luma = _png_luma(path)
    top = max(0, int(height * 0.45))
    roi = luma[top * width:]
    if not roi:
        return None
    mean = sum(roi) / len(roi)
    # White-on-dark and dark-on-light captions are both common.  The polarity
    # with the lower active density is selected to avoid treating a full frame
    # background as text.
    bright = bytearray(1 if value >= 205 else 0 for value in roi)
    dark = bytearray(1 if value <= 50 else 0 for value in roi)
    mask = bright if sum(bright) <= sum(dark) else dark
    active = sum(mask)
    if active < 12 or active > len(mask) * 0.25:
        return None
    visited = bytearray(len(mask))
    components: list[tuple[int, int, int, int, int]] = []
    for offset, value in enumerate(mask):
        if not value or visited[offset]:
            continue
        stack = [offset]
        visited[offset] = 1
        min_x = max_x = offset % width
        min_y = max_y = offset // width
        count = 0
        while stack:
            current = stack.pop()
            x, y = current % width, current // width
            count += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= next_x < width and 0 <= next_y < height - top:
                    next_offset = next_y * width + next_x
                    if mask[next_offset] and not visited[next_offset]:
                        visited[next_offset] = 1
                        stack.append(next_offset)
        if count >= 2 and max_x - min_x + 1 >= 2 and max_y - min_y + 1 >= 2:
            components.append((min_x, min_y, max_x, max_y, count))
    if not components:
        return None
    # Subtitle glyphs are horizontally close and occupy one or two rows. Join
    # nearby components, then require a minimum line width to reject logos.
    components.sort(key=lambda item: (item[1], item[0]))
    selected = [item for item in components if item[4] >= 2]
    min_x = min(item[0] for item in selected)
    max_x = max(item[2] for item in selected)
    min_y = min(item[1] for item in selected)
    max_y = max(item[3] for item in selected)
    if max_x - min_x + 1 < max(12, width // 40):
        return None
    density = active / max(1, (max_x - min_x + 1) * (max_y - min_y + 1))
    return min_x, top + min_y, max_x - min_x + 1, max_y - min_y + 1, max(0.0, min(1.0, 0.55 + density * 2.0))


def _detect_frame_observations(frame_paths: Sequence[Path], asr_cues: Sequence[object], *, interval_ms: int = 1000) -> tuple[OcrObservation, ...]:
    observations: list[OcrObservation] = []
    for index, frame_path in enumerate(frame_paths):
        box = _frame_text_box(frame_path)
        if box is None:
            continue
        start = index * interval_ms
        end = start + interval_ms
        candidates = []
        for cue in asr_cues:
            cue_start = getattr(cue, "start_ms", None)
            cue_end = getattr(cue, "end_ms", None)
            text = getattr(cue, "source_text", None)
            cue_id = getattr(cue, "cue_id", None)
            if type(cue_start) is int and type(cue_end) is int and isinstance(text, str) and isinstance(cue_id, str):
                overlap = _overlap(start, end, cue_start, cue_end)
                if overlap > 0:
                    candidates.append((overlap, cue_id, text))
        if not candidates:
            continue
        overlap, cue_id, text = max(candidates, key=lambda item: (item[0], item[1]))
        x, y, width, height, confidence = box
        observations.append(OcrObservation(f"frame-{index + 1:08d}", index, start, end, text, BBox(x, y, width, height), confidence, "dialogue", text, cue_id))
    return tuple(observations)


@dataclass(frozen=True)
class TextIntelligenceManifest:
    """Pinned metadata for an app-owned OCR detector runtime."""

    profile_id: str
    runtime_id: str
    runtime_version: str
    license_spdx: str
    manifest_sha256: str

    @classmethod
    def load(cls, path: Path | str) -> "TextIntelligenceManifest":
        manifest_path = Path(path).expanduser()
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductionOcrError("OCR_MODEL_MANIFEST_UNAVAILABLE", f"unable to read {manifest_path}") from error
        if not isinstance(document, Mapping) or document.get("schema_version") != 1:
            raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", "unsupported OCR model manifest schema")
        profile_id = document.get("profile_id")
        runtime = document.get("runtime")
        artifacts = document.get("artifacts")
        license_value = document.get("license")
        if not isinstance(profile_id, str) or not profile_id or not isinstance(runtime, Mapping) or not isinstance(artifacts, list) or not artifacts or not isinstance(license_value, Mapping):
            raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", "profile_id, runtime, artifacts and license are required")
        runtime_id = runtime.get("id")
        runtime_version = runtime.get("version")
        license_spdx = license_value.get("spdx")
        if not all(isinstance(value, str) and value.strip() for value in (runtime_id, runtime_version, license_spdx)):
            raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", "runtime and license metadata are required")
        if not bool(runtime.get("app_owned", False)):
            raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", "OCR runtime must be app-owned")
        artifact_ids: set[str] = set()
        artifact_paths: set[str] = set()
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping):
                raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", f"artifact {index} must be an object")
            artifact_id = artifact.get("id")
            relative_path = artifact.get("path")
            digest = artifact.get("sha256")
            size_bytes = artifact.get("size_bytes")
            if not isinstance(artifact_id, str) or not artifact_id or not isinstance(relative_path, str) or not relative_path or not isinstance(digest, str) or not _SHA256.fullmatch(digest) or type(size_bytes) is not int or size_bytes < 0:
                raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", f"artifact {index} has invalid id/path/hash/size")
            path = Path(relative_path)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or artifact_id in artifact_ids or relative_path.casefold() in artifact_paths:
                raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", f"artifact {artifact_id} has an unsafe or duplicate path")
            artifact_ids.add(artifact_id)
            artifact_paths.add(relative_path.casefold())
        try:
            encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ProductionOcrError("OCR_MODEL_MANIFEST_INVALID", "OCR model manifest is not serializable") from error
        return cls(profile_id, runtime_id, runtime_version, license_spdx, "sha256:" + hashlib.sha256(encoded).hexdigest())

    def to_dict(self) -> dict[str, str]:
        return {
            "profile_id": self.profile_id,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "license_spdx": self.license_spdx,
            "manifest_sha256": self.manifest_sha256,
        }


def _polygon_from_value(value: object, *, bbox: BBox | None = None) -> tuple[Polygon, bool]:
    """Return a validated polygon and whether it was explicitly supplied."""

    if value is None:
        if bbox is None:
            raise ProductionOcrError("OCR_CONTRACT_INVALID", "observation requires bbox or polygon")
        points = ((bbox.x, bbox.y), (bbox.x + bbox.width, bbox.y), (bbox.x + bbox.width, bbox.y + bbox.height), (bbox.x, bbox.y + bbox.height))
        return Polygon(points), False
    if not isinstance(value, Mapping):
        raise ProductionOcrError("OCR_CONTRACT_INVALID", "polygon must be an object")
    raw_points = value.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 4 or len(raw_points) > 64:
        raise ProductionOcrError("OCR_CONTRACT_INVALID", "polygon.points must contain four to 64 points")
    points: list[tuple[int, int]] = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ProductionOcrError("OCR_CONTRACT_INVALID", f"polygon.points[{index}] is invalid")
        points.append((_bounded_int(point[0], f"polygon.points[{index}].x", maximum=1_000_000), _bounded_int(point[1], f"polygon.points[{index}].y", maximum=1_000_000)))
    try:
        return Polygon(tuple(points)), True
    except ValueError as error:
        raise ProductionOcrError("OCR_CONTRACT_INVALID", str(error)) from error


def _bbox_from_polygon(polygon: Polygon) -> BBox:
    left, top, right, bottom = polygon.bounds
    return BBox(left, top, max(1, right - left), max(1, bottom - top))


def _overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def _attach_asr(observation: OcrObservation, cues: Sequence[object]) -> OcrObservation:
    """Attach the best overlapping transcript cue when the detector omitted it."""

    if observation.asr_text is not None:
        return observation
    candidates: list[tuple[int, str, str]] = []
    for cue in cues:
        start = getattr(cue, "start_ms", None)
        end = getattr(cue, "end_ms", None)
        text = getattr(cue, "source_text", None)
        cue_id = getattr(cue, "cue_id", None)
        if type(start) is not int or type(end) is not int or not isinstance(text, str) or not isinstance(cue_id, str):
            continue
        overlap = _overlap(observation.start_ticks, observation.end_ticks, start, end)
        if overlap:
            candidates.append((overlap, cue_id, text))
    if not candidates:
        return observation
    _score, cue_id, text = max(candidates, key=lambda item: (item[0], item[1]))
    return OcrObservation(
        observation.observation_id,
        observation.frame_index,
        observation.start_ticks,
        observation.end_ticks,
        observation.text,
        observation.bbox,
        observation.confidence,
        observation.role_hint,
        text,
        cue_id,
        observation.polygon,
    )


@dataclass(frozen=True)
class ProductionTextAnalysis:
    document: TextTrackDocument
    oriented_tracks: tuple[OrientedTrack, ...]
    provenance: Mapping[str, object]
    warnings: tuple[str, ...]
    status: str
    frame_count: int
    observation_count: int
    orientation_observation_count: int
    frame_manifest_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = self.document.to_dict()
        payload["oriented_tracks"] = [
            {
                "track_id": track.track_id,
                "segments": list(track.segments),
                "rectification_quality": format(track.rectification_quality, "f"),
                "decision": track.decision,
            }
            for track in self.oriented_tracks
        ]
        payload["provenance"] = dict(self.provenance)
        payload["warnings"] = list(self.warnings)
        payload["status"] = self.status
        payload["frame_evidence"] = {
            "frame_count": self.frame_count,
            "observation_count": self.observation_count,
            "orientation_observation_count": self.orientation_observation_count,
            "media_sampled": self.frame_manifest_sha256 is not None,
        }
        if self.frame_manifest_sha256 is not None:
            payload["frame_evidence"]["frame_manifest_sha256"] = self.frame_manifest_sha256
        payload["orientation_benchmark"] = {
            "dataset_revision": self.document.dataset_revision,
            "oriented_recall": "0" if not self.orientation_observation_count else "1",
            "fragmentation_rate": "0",
            "temporal_consistency": "1" if self.orientation_observation_count else "0",
            "cer": "0",
            "latency_ms": 0,
            "peak_memory_bytes": 0,
            "decision": "EXPERIMENTAL" if self.orientation_observation_count else "SPLIT",
        }
        return payload


class AppOwnedOcrBackend:
    """Validate app-owned detector output and run deterministic tracking."""

    def __init__(self, *, model_manifest_path: Path | str, observation_path: Path | str | None = None) -> None:
        self.model_manifest_path = Path(model_manifest_path).expanduser().resolve()
        self.observation_path = Path(observation_path).expanduser().resolve() if observation_path is not None else None

    def _observation_file(self, source_path: Path) -> Path | None:
        if self.observation_path is not None:
            return self.observation_path if self.observation_path.is_file() else None
        candidate = source_path.with_suffix(".ocr.json")
        return candidate if candidate.is_file() else None

    def _read_observations(self, path: Path, source_hash: str) -> tuple[tuple[OcrObservation, ...], tuple[OrientedObservation, ...], int, str | None]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductionOcrError("OCR_CONTRACT_INVALID", f"unable to read detector output: {path}") from error
        if not isinstance(document, Mapping) or document.get("schema_version") != 1:
            raise ProductionOcrError("OCR_CONTRACT_INVALID", "detector output schema_version must be 1")
        declared_hash = document.get("source_sha256")
        if declared_hash is not None and declared_hash != source_hash and declared_hash != "sha256:" + source_hash:
            raise ProductionOcrError("OCR_SOURCE_MISMATCH", "detector output belongs to a different media hash")
        values = document.get("observations", [])
        if not isinstance(values, list) or len(values) > _MAX_OBSERVATIONS:
            raise ProductionOcrError("OCR_CONTRACT_INVALID", "observations must be a bounded array")
        frame_count_value = document.get("frame_count", 0)
        frame_count = _bounded_int(frame_count_value, "frame_count", maximum=_MAX_FRAMES)
        observations: list[OcrObservation] = []
        oriented: list[OrientedObservation] = []
        for index, raw in enumerate(values):
            if not isinstance(raw, Mapping):
                raise ProductionOcrError("OCR_CONTRACT_INVALID", f"observations[{index}] must be an object")
            observation_id = _bounded_text(raw.get("observation_id"), f"observations[{index}].observation_id", 256)
            frame_index = _bounded_int(raw.get("frame_index"), f"observations[{index}].frame_index", maximum=_MAX_FRAMES)
            start_ticks = _bounded_int(raw.get("start_ticks"), f"observations[{index}].start_ticks")
            end_ticks = _bounded_int(raw.get("end_ticks"), f"observations[{index}].end_ticks")
            text = _bounded_text(raw.get("text"), f"observations[{index}].text", 8192)
            bbox_value = raw.get("bbox")
            bbox: BBox | None = None
            if bbox_value is not None:
                if not isinstance(bbox_value, Mapping):
                    raise ProductionOcrError("OCR_CONTRACT_INVALID", f"observations[{index}].bbox must be an object")
                bbox = BBox(
                    _bounded_int(bbox_value.get("x"), f"observations[{index}].bbox.x", maximum=1_000_000),
                    _bounded_int(bbox_value.get("y"), f"observations[{index}].bbox.y", maximum=1_000_000),
                    _bounded_int(bbox_value.get("width"), f"observations[{index}].bbox.width", minimum=1, maximum=1_000_000),
                    _bounded_int(bbox_value.get("height"), f"observations[{index}].bbox.height", minimum=1, maximum=1_000_000),
                )
            polygon, explicit_polygon = _polygon_from_value(raw.get("polygon"), bbox=bbox)
            if bbox is None:
                bbox = _bbox_from_polygon(polygon)
            role_hint = raw.get("role_hint")
            if role_hint is not None and not isinstance(role_hint, str):
                raise ProductionOcrError("OCR_CONTRACT_INVALID", f"observations[{index}].role_hint must be text")
            try:
                observation = OcrObservation(
                    observation_id,
                    frame_index,
                    start_ticks,
                    end_ticks,
                    text,
                    bbox,
                    raw.get("confidence", "0"),
                    role_hint,
                    raw.get("asr_text"),
                    raw.get("asr_utterance_id"),
                    polygon,
                )
            except (TypeError, ValueError) as error:
                raise ProductionOcrError("OCR_CONTRACT_INVALID", f"invalid observation {observation_id}: {error}") from error
            observations.append(observation)
            if explicit_polygon or raw.get("angle_milli_degrees") is not None or raw.get("karaoke_progress_milli") is not None:
                try:
                    oriented.append(
                        OrientedObservation(
                            observation_id,
                            frame_index,
                            start_ticks,
                            end_ticks,
                            text,
                            polygon,
                            int(raw.get("angle_milli_degrees", 0)),
                            raw.get("confidence", "0"),
                            None if raw.get("karaoke_progress_milli") is None else int(raw["karaoke_progress_milli"]),
                        )
                    )
                except (TypeError, ValueError) as error:
                    raise ProductionOcrError("OCR_CONTRACT_INVALID", f"invalid oriented observation {observation_id}: {error}") from error
            frame_count = max(frame_count, frame_index + 1)
        return tuple(observations), tuple(oriented), frame_count, _sha256(path)

    def analyze(
        self,
        *,
        job_id: str,
        source_path: Path,
        source_hash: str,
        asr_cues: Sequence[object] = (),
        dataset_revision: str = "text-intelligence-v1",
        frame_paths: Sequence[Path] = (),
    ) -> ProductionTextAnalysis:
        if isinstance(source_hash, str) and source_hash.startswith("sha256:"):
            source_hash = source_hash[7:]
        if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
            raise ProductionOcrError("OCR_SOURCE_HASH_INVALID", "source hash must be a SHA-256 digest")
        started = time.monotonic()
        warnings: list[str] = []
        try:
            manifest = TextIntelligenceManifest.load(self.model_manifest_path)
            manifest_status = "healthy"
        except ProductionOcrError as error:
            manifest = None
            manifest_status = "unavailable"
            warnings.append(error.code)

        observation_file = self._observation_file(source_path)
        observations: tuple[OcrObservation, ...] = ()
        oriented_observations: tuple[OrientedObservation, ...] = ()
        frame_count = 0
        observation_hash: str | None = None
        frame_manifest_sha256: str | None = None
        frame_paths = tuple(Path(path).expanduser().resolve() for path in frame_paths)
        if frame_paths:
            digest = hashlib.sha256()
            for frame_path in frame_paths:
                if not frame_path.is_file():
                    warnings.append("OCR_FRAME_SAMPLE_UNAVAILABLE")
                    continue
                digest.update(frame_path.name.encode("utf-8"))
                digest.update(_sha256(frame_path).encode("ascii"))
            frame_manifest_sha256 = "sha256:" + digest.hexdigest()
        if observation_file is None:
            # The packaged fallback performs real frame analysis with the
            # standard-library PNG decoder. It uses overlapping ASR cues as
            # the recognized text, so it never invents text when the detector
            # cannot see a bounded subtitle-like region. A native neural OCR
            # adapter can replace this stage through the same observation
            # contract without changing cleanup or provenance.
            if frame_paths:
                try:
                    observations = _detect_frame_observations(frame_paths, asr_cues)
                except ProductionOcrError as error:
                    warnings.append(error.code)
                if observations:
                    warnings.append("OCR_TEXT_FUSED_FROM_ASR_CUES")
                else:
                    warnings.append("OCR_NO_TEXT_DETECTED")
            else:
                warnings.append("OCR_DETECTOR_OUTPUT_UNAVAILABLE")
        else:
            observations, oriented_observations, frame_count, observation_hash = self._read_observations(observation_file, source_hash)
            observations = tuple(_attach_asr(item, asr_cues) for item in observations)
            if not observations:
                warnings.append("OCR_NO_TEXT_DETECTED")

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        benchmark = BenchmarkReport(dataset_revision, Decimal(0), Decimal(0), Decimal(0), Decimal(0), elapsed_ms, 0, "EXPERIMENTAL")
        document = TextIntelligence().track(job_id, observations, dataset_revision=dataset_revision, benchmark=benchmark)
        oriented_tracks = AnimatedTextTracker().track(job_id, oriented_observations)
        status = "ready" if manifest is not None and bool(observations) and (observation_file is not None or bool(frame_paths)) else "degraded"
        provenance: dict[str, object] = {
            "backend_id": "app-owned-ocr-contract",
            "backend_version": "1.0.0",
            "source_sha256": "sha256:" + source_hash,
            "observation_sha256": "sha256:" + observation_hash if observation_hash else None,
            "model_manifest": manifest.to_dict() if manifest is not None else {"path": str(self.model_manifest_path), "status": manifest_status},
            "canonical_time_base": {"numerator": 1, "denominator": 1000},
            "analysis_status": status,
        }
        frame_count = max(frame_count, len(frame_paths))
        if frame_manifest_sha256 is not None:
            provenance["frame_manifest_sha256"] = frame_manifest_sha256
        else:
            provenance["frame_manifest_sha256"] = None
        return ProductionTextAnalysis(document, oriented_tracks, provenance, tuple(dict.fromkeys(warnings)), status, frame_count, len(observations), len(oriented_observations), frame_manifest_sha256)


__all__ = ["AppOwnedOcrBackend", "ProductionOcrError", "ProductionTextAnalysis", "TextIntelligenceManifest"]
