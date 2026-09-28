"""Strict, dependency-free validation for the canonical timeline fixtures."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "fixtures" / "timeline"
SIGNED_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)$")
UNSIGNED_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decimal(value: Any, *, signed: bool) -> int:
    if not isinstance(value, str):
        raise ValueError("wide integer fields must be decimal strings")
    pattern = SIGNED_DECIMAL if signed else UNSIGNED_DECIMAL
    if not pattern.fullmatch(value):
        raise ValueError(f"non-canonical decimal string: {value!r}")
    parsed = int(value)
    if not signed and parsed <= 0:
        raise ValueError("time-base factors and dimensions must be positive")
    return parsed


def time_point(value: Any) -> tuple[Fraction, int]:
    if not isinstance(value, dict):
        raise ValueError("time point must be an object")
    if value.get("kind") != "time_point" or value.get("schema_version") != 1:
        raise ValueError("time point discriminator/version mismatch")
    ticks = decimal(value.get("ticks"), signed=True)
    base = value.get("time_base")
    if not isinstance(base, dict):
        raise ValueError("time point is missing time_base")
    numerator = decimal(base.get("numerator"), signed=False)
    denominator = decimal(base.get("denominator"), signed=False)
    rational = Fraction(ticks * numerator, denominator)
    return rational, ticks


def validate_mapping(mapping: Any) -> None:
    if not isinstance(mapping, dict) or mapping.get("kind") != "proxy_source_mapping":
        raise ValueError("proxy mapping discriminator mismatch")
    segments = mapping.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("proxy mapping needs at least one segment")
    previous: tuple[Fraction, Fraction] | None = None
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("mapping segment must be an object")
        source_start, _ = time_point(segment.get("source_start"))
        source_end, _ = time_point(segment.get("source_end"))
        proxy_start, _ = time_point(segment.get("proxy_start"))
        proxy_end, _ = time_point(segment.get("proxy_end"))
        if not source_start < source_end or not proxy_start < proxy_end:
            raise ValueError("mapping segments are half-open and must have positive duration")
        if previous is not None and (previous[0] > proxy_start or previous[1] > source_start):
            raise ValueError("mapping segments overlap or are out of order")
        previous = (proxy_end, source_end)


def validate_geometry(geometry: Any) -> None:
    if not isinstance(geometry, dict) or geometry.get("kind") != "coded_to_display_geometry":
        raise ValueError("geometry discriminator mismatch")
    dimensions = geometry.get("coded_dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("coded dimensions are required")
    decimal(dimensions.get("width"), signed=False)
    decimal(dimensions.get("height"), signed=False)
    if geometry.get("rotation_degrees") not in {0, 90, 180, 270}:
        raise ValueError("rotation must be a clockwise quarter turn")
    aspect = geometry.get("pixel_aspect_ratio")
    if not isinstance(aspect, dict):
        raise ValueError("pixel aspect ratio metadata is required")
    decimal(aspect.get("numerator"), signed=False)
    decimal(aspect.get("denominator"), signed=False)
    if geometry.get("coordinate_convention") != "pixel_bounds":
        raise ValueError("coordinate convention must be pixel_bounds")


def validate_fixture(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("kind") != "timeline_fixture":
        raise ValueError("fixture discriminator/version mismatch")
    points = value.get("source_pts")
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("fixture must contain at least three source PTS values")
    point_values = [time_point(point)[0] for point in points]
    if point_values != sorted(point_values) or len(set(point_values)) != len(point_values):
        raise ValueError("source PTS values must be strictly increasing")
    if not any(time_point(point)[1] != 0 for point in points):
        raise ValueError("fixture must preserve a non-zero PTS")
    if all((right - left) == (point_values[1] - point_values[0]) for left, right in zip(point_values, point_values[1:])):
        raise ValueError("fixture must exercise irregular VFR spacing")
    validate_mapping(value.get("proxy_mapping"))
    validate_geometry(value.get("geometry"))


def main() -> None:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    if not paths:
        raise SystemExit("no timeline fixtures found")
    for path in paths:
        validate_fixture(path)
        print(f"validated {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
