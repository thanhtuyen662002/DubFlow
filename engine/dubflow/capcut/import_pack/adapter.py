"""Portable CapCut import-pack adapter with no CapCut runtime dependency."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from engine.dubflow.media.adapter import MediaAdapterError, Rational, rescale_ticks


PACK_VERSION = 2
LEGACY_PACK_VERSION = 1
_I64_MAX = (1 << 63) - 1
_U64_MAX = (1 << 64) - 1
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_FILENAME = re.compile(r"^[^\x00-\x1f\\/:*?\"<>|]{1,128}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_ASSET_KINDS = {"video", "original_audio", "dub_audio", "mixed_audio", "subtitle", "timeline", "metadata"}
_COPY_BYTES = 4 * 1024 * 1024


class ImportPackError(ValueError):
    pass


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ImportPackError(f"{name} has an invalid identifier")
    return value


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_filename(value: str) -> str:
    name = Path(value).name
    stem = name.split(".", 1)[0].upper()
    if name in {"", ".", ".."} or not _FILENAME.fullmatch(name) or name.endswith((" ", ".")) or stem in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        raise ImportPackError("asset filename must be a portable non-reserved basename")
    return name


@dataclass(frozen=True)
class ImportPackAsset:
    asset_id: str
    kind: str
    source_path: Path
    format: str
    content_hash: str | None = None

    def __post_init__(self) -> None:
        _id(self.asset_id, "asset_id")
        if not isinstance(self.kind, str) or self.kind not in _ASSET_KINDS or not isinstance(self.format, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", self.format):
            raise ImportPackError("additional asset kind/format is invalid")
        _safe_filename(self.asset_id)
        if self.kind not in {"timeline", "metadata"} and len(self.asset_id) > 122:
            raise ImportPackError("asset id exceeds the derived track identifier bound")
        if not isinstance(self.source_path, Path) or self.source_path.is_symlink() or not self.source_path.is_file():
            raise ImportPackError("additional asset must identify an existing file")
        if self.content_hash is not None and (not isinstance(self.content_hash, str) or not _HASH.fullmatch(self.content_hash)):
            raise ImportPackError("additional asset content hash is invalid")
        if self.kind == "subtitle" and self.format not in {"srt", "ass", "vtt"}:
            raise ImportPackError("additional subtitle format is invalid")


@dataclass(frozen=True)
class ImportPackRequest:
    pack_id: str
    source_path: Path
    subtitle_path: Path | None = None
    dub_audio_path: Path | None = None
    original_audio_path: Path | None = None
    subtitle_format: str | None = None
    # A supplied canonical mapping produces v2 with decimal-string ticks.
    # Legacy callers without a mapping still produce v1; no timing is invented.
    timeline: Mapping[str, Any] | None = None
    additional_assets: tuple[ImportPackAsset, ...] = ()
    source_content_hash: str | None = None

    def __post_init__(self) -> None:
        _id(self.pack_id, "pack_id")
        for name, path in (("source_path", self.source_path), ("subtitle_path", self.subtitle_path), ("dub_audio_path", self.dub_audio_path), ("original_audio_path", self.original_audio_path)):
            if path is not None and (not isinstance(path, Path) or path.is_symlink() or not path.is_file()):
                raise ImportPackError(f"{name} must identify an existing file")
        if self.subtitle_path is not None:
            suffix = self.subtitle_format or self.subtitle_path.suffix.removeprefix(".").lower()
            if suffix not in {"srt", "ass", "vtt"}:
                raise ImportPackError("subtitle format must be srt, ass or vtt")
        if self.timeline is not None and not isinstance(self.timeline, Mapping):
            raise ImportPackError("timeline must be an object")
        if not isinstance(self.additional_assets, tuple) or len(self.additional_assets) > 28 or any(not isinstance(asset, ImportPackAsset) for asset in self.additional_assets):
            raise ImportPackError("additional assets must be a bounded tuple")
        if self.additional_assets and self.timeline is None:
            raise ImportPackError("additional assets require the v2 timeline contract")
        if self.source_content_hash is not None and (not isinstance(self.source_content_hash, str) or not _HASH.fullmatch(self.source_content_hash)):
            raise ImportPackError("source content hash is invalid")


@dataclass(frozen=True)
class ImportPackResult:
    root: Path
    manifest: dict[str, object]


def _plain_under(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ImportPackError("pack storage escapes the controlled root") from error
    for node in (path, *path.parents):
        if node.exists() or node.is_symlink():
            metadata = node.lstat()
            if node.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ImportPackError("pack storage contains a link/reparse point")
        if node == root:
            break


def _copy_asset(source: Path, target: Path, *, size: int, expected_hash: str, root: Path) -> None:
    """Resume a private copy only after comparing its complete retained prefix."""
    _plain_under(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _plain_under(root, target)
    retained = target.stat().st_size if target.exists() else 0
    if retained > size:
        raise ImportPackError("retained asset exceeds its pinned size")
    digest = sha256()
    with source.open("rb") as original:
        if retained:
            with target.open("rb") as prefix:
                remaining = retained
                verified = 0
                while remaining:
                    chunk = prefix.read(min(remaining, _COPY_BYTES))
                    if not chunk or original.read(len(chunk)) != chunk:
                        # A corrupt private tail is invalid checkpoint data.
                        # Keep the proven prefix and recompute the remaining
                        # bytes once; the final pinned hash still must match.
                        with target.open("r+b") as damaged:
                            damaged.truncate(verified)
                        original.seek(verified)
                        retained = verified
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                    verified += len(chunk)
        with target.open("ab" if target.exists() else "xb") as output:
            total = retained
            while chunk := original.read(_COPY_BYTES):
                total += len(chunk)
                if total > size:
                    raise ImportPackError("asset source grew during copying")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    if total != size or "sha256:" + digest.hexdigest() != expected_hash:
        raise ImportPackError("copied asset differs from its pinned content hash/size")


def _integer(value: Any, name: str, *, maximum: int = _I64_MAX, minimum: int = 0) -> int:
    if isinstance(value, str) and len(value) <= 20 and _DECIMAL.fullmatch(value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _cue_tick(cue: Mapping[str, Any], edge: str, base: Rational) -> int:
    ticks_key, ms_key = f"{edge}_ticks", f"{edge}_ms"
    ticks = _integer(cue[ticks_key], ticks_key) if ticks_key in cue else None
    if ms_key in cue:
        milliseconds = _integer(cue[ms_key], ms_key)
        converted = rescale_ticks(milliseconds, Rational(1, 1000), base,
                                  rounding="floor" if edge == "start" else "ceil", name=ms_key)
        if ticks is not None and ticks != converted:
            raise ValueError(f"conflicting {ticks_key} and {ms_key}")
        ticks = converted
    if ticks is None:
        raise ValueError(f"{ticks_key} is missing")
    return ticks


def _timeline(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize exact rational timing, covering millisecond aliases outward."""

    try:
        if not isinstance(value, Mapping):
            raise ValueError("timeline is missing")
        if set(value) - {"time_base", "duration_ticks", "cues"}:
            raise ValueError("timeline contains unknown fields")
        time_base = value.get("time_base")
        if not isinstance(time_base, Mapping) or set(time_base) != {"numerator", "denominator"}:
            raise ValueError("time_base is missing")
        base = Rational(_integer(time_base["numerator"], "numerator", maximum=_U64_MAX, minimum=1),
                        _integer(time_base["denominator"], "denominator", maximum=_U64_MAX, minimum=1))
        duration = value.get("duration_ticks")
        if duration is not None:
            duration = _integer(duration, "duration_ticks")
        cues = value.get("cues", [])
        if not isinstance(cues, list) or len(cues) > 100_000:
            raise ValueError("cues must be a bounded array")
        normalized: list[dict[str, Any]] = []
        cue_ids: set[str] = set()
        for cue in cues:
            if not isinstance(cue, Mapping):
                raise ValueError("timeline cue must be an object")
            if set(cue) - {"cue_id", "start_ticks", "end_ticks", "start_ms", "end_ms", "asset_ids", "track_id", "speaker_id", "voice_id"}:
                raise ValueError("timeline cue contains unknown fields")
            cue_id = cue.get("cue_id")
            if not isinstance(cue_id, str) or not cue_id or len(cue_id) > 256:
                raise ValueError("timeline cue id is invalid")
            if cue_id in cue_ids:
                raise ValueError("timeline contains duplicate cue id")
            cue_ids.add(cue_id)
            start, end = _cue_tick(cue, "start", base), _cue_tick(cue, "end", base)
            if end <= start or (duration is not None and end > duration):
                raise ValueError("timeline cue range is invalid")
            item: dict[str, Any] = {"cue_id": cue_id, "start_ticks": str(start), "end_ticks": str(end)}
            if "asset_ids" in cue:
                ids = cue["asset_ids"]
                if not isinstance(ids, list) or len(ids) > 32:
                    raise ValueError("cue asset_ids must be a bounded list")
                item["asset_ids"] = [_id(asset_id, "cue asset_id") for asset_id in ids]
            for key in ("track_id", "speaker_id", "voice_id"):
                if key in cue:
                    if not isinstance(cue[key], str) or not cue[key] or len(cue[key]) > 128:
                        raise ValueError(f"cue {key} is invalid")
                    item[key] = cue[key]
            normalized.append(item)
        return {
            "time_base": {"numerator": str(base.numerator), "denominator": str(base.denominator)},
            "duration_ticks": str(duration) if duration is not None else None,
            "cues": normalized,
        }
    except (AttributeError, TypeError, ValueError, MediaAdapterError) as error:
        raise ImportPackError(f"timeline is invalid: {error}") from error


def _manifest(pack_id: str, assets: list[dict[str, object]], timeline: Mapping[str, Any] | None) -> dict[str, object]:
    tracks: list[dict[str, object]] = []
    video = [asset["asset_id"] for asset in assets if asset["kind"] == "video"]
    audio = [asset["asset_id"] for asset in assets if asset["kind"] in {"original_audio", "dub_audio"}]
    subtitle = [asset["asset_id"] for asset in assets if asset["kind"] == "subtitle"]
    tracks.append({"track_id": "video-0", "kind": "video", "order": 0, "asset_ids": video, "language": None})
    if audio:
        tracks.append({"track_id": "audio-0", "kind": "audio", "order": 1, "asset_ids": audio, "language": None})
    if subtitle:
        tracks.append({"track_id": "subtitle-0", "kind": "subtitle", "order": 2, "asset_ids": subtitle, "language": "vi"})
    if timeline is not None:
        tracks = []
        has_mix = any(asset["kind"] == "mixed_audio" for asset in assets)
        has_dub = any(asset["kind"] == "dub_audio" for asset in assets)
        subtitle_enabled = False
        for category in ("video", "audio", "subtitle"):
            for asset in assets:
                kind = asset["kind"]
                group = "audio" if kind in {"original_audio", "dub_audio", "mixed_audio"} else kind
                if group != category:
                    continue
                if category == "video":
                    active = asset["asset_id"] == "source-video"
                elif category == "audio":
                    active = kind == ("mixed_audio" if has_mix else "dub_audio" if has_dub else None)
                else:
                    active = not subtitle_enabled
                    subtitle_enabled = True
                track = {"track_id": f"track-{asset['asset_id']}", "kind": category,
                         "order": len(tracks), "asset_ids": [asset["asset_id"]], "language": "vi" if category == "subtitle" else None,
                         "role": kind, "enabled_by_default": active}
                if category == "video":
                    track["mute_embedded_audio"] = has_mix or has_dub or asset["asset_id"] != "source-video"
                tracks.append(track)
    manifest = {"schema_version": PACK_VERSION if timeline is not None else LEGACY_PACK_VERSION,
                "kind": "capcut_import_pack", "pack_id": pack_id, "assets": assets, "tracks": tracks}
    if timeline is not None:
        manifest["timeline"] = _timeline(timeline)
    return manifest


def create_import_pack(request: ImportPackRequest, output_root: Path) -> ImportPackResult:
    if output_root.exists() or output_root.is_symlink():
        raise ImportPackError("OUTPUT_EXISTS")
    # Build and bind the complete request before creating any private files.
    specifications = [(ImportPackAsset("source-video", "video", request.source_path, request.source_path.suffix.removeprefix(".").lower() or "video", request.source_content_hash),
                       "media/" + _safe_filename(request.source_path.name))]
    for asset_id, kind, source in (("source-audio", "original_audio", request.original_audio_path), ("dub-audio", "dub_audio", request.dub_audio_path)):
        if source is not None:
            specifications.append((ImportPackAsset(asset_id, kind, source, source.suffix.removeprefix(".").lower() or "audio"), f"audio/{asset_id}-{_safe_filename(source.name)}"))
    if request.subtitle_path is not None:
        source = request.subtitle_path
        specifications.append((ImportPackAsset("vietsub", "subtitle", source, request.subtitle_format or source.suffix.removeprefix(".").lower()), "subtitles/" + _safe_filename(source.name)))
    for asset in request.additional_assets:
        folder = "media" if asset.kind == "video" else "audio" if asset.kind.endswith("audio") else "subtitles" if asset.kind == "subtitle" else "metadata"
        specifications.append((asset, f"{folder}/{asset.asset_id}-{_safe_filename(asset.source_path.name)}"))
    identifiers: set[str] = set()
    assets = []
    for asset, relative in specifications:
        if asset.asset_id.casefold() in identifiers:
            raise ImportPackError("duplicate asset id")
        identifiers.add(asset.asset_id.casefold())
        assets.append({"asset_id": asset.asset_id, "kind": asset.kind, "relative_path": relative,
                       "content_hash": asset.content_hash or _hash_file(asset.source_path),
                       "size_bytes": str(asset.source_path.stat().st_size), "format": asset.format})
    manifest = _manifest(request.pack_id, assets, request.timeline)
    if request.timeline is not None:
        track_ids = {track["track_id"] for track in manifest["tracks"]}
        asset_ids = {asset["asset_id"] for asset in assets}
        for cue in manifest["timeline"]["cues"]:
            if not set(cue.get("asset_ids", [])) <= asset_ids or ("track_id" in cue and cue["track_id"] not in track_ids):
                raise ImportPackError("timeline cue references an unknown pack asset/track")
    pins = {"recipe": "portable-import-pack-copy-v2", "manifest": manifest}
    raw_pins = json.dumps(pins, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw_pins) > 16 * 1024 * 1024:
        raise ImportPackError("pack metadata exceeds bounded storage")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_root.parent / f".{output_root.name}.{sha256(raw_pins).hexdigest()[:32]}.partial"
    _plain_under(output_root.parent, temporary)
    marker = temporary / ".dubflow-input-pins.json"
    if not temporary.exists():
        temporary.mkdir()
    if marker.exists():
        _plain_under(temporary, marker)
        if not marker.is_file() or marker.stat().st_size > 16 * 1024 * 1024 or marker.read_bytes() != raw_pins:
            raise ImportPackError("unrecognized private pack checkpoint")
    else:
        partial_marker = temporary / ".dubflow-input-pins.partial"
        _plain_under(temporary, partial_marker)
        if any(path != partial_marker for path in temporary.iterdir()):
            raise ImportPackError("unrecognized private pack checkpoint")
        retained_marker = partial_marker.read_bytes() if partial_marker.is_file() and partial_marker.stat().st_size <= len(raw_pins) else b""
        if partial_marker.exists() and (not partial_marker.is_file() or partial_marker.stat().st_size > len(raw_pins) or not raw_pins.startswith(retained_marker)):
            raise ImportPackError("unrecognized private pack marker")
        with partial_marker.open("ab" if partial_marker.exists() else "xb") as stream:
            stream.write(raw_pins[len(retained_marker):])
            stream.flush()
            os.fsync(stream.fileno())
        partial_marker.rename(marker)
    # Interrupted copies remain private and resumable. No existing canonical
    # export or unrelated folder is removed on a failed copy.
    for (asset, relative), descriptor in zip(specifications, assets):
        _copy_asset(asset.source_path, temporary / relative, size=int(descriptor["size_bytes"]),
                    expected_hash=descriptor["content_hash"], root=temporary)
    manifest_path = temporary / "manifest.json"
    _plain_under(temporary, manifest_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    validate_import_pack(temporary)
    temporary.rename(output_root)
    return ImportPackResult(output_root, manifest)


def validate_import_pack(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    _plain_under(root, manifest_path)
    if not manifest_path.is_file():
        raise ImportPackError("manifest.json is missing")
    try:
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ImportPackError("manifest exceeds bounded storage")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportPackError("manifest is unreadable") from exc
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest.get("schema_version") not in {LEGACY_PACK_VERSION, PACK_VERSION} or manifest.get("kind") != "capcut_import_pack":
        raise ImportPackError("unsupported import pack manifest")
    assets = manifest.get("assets")
    tracks = manifest.get("tracks")
    timeline = manifest.get("timeline")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 256 or not isinstance(tracks, list) or not 1 <= len(tracks) <= 32:
        raise ImportPackError("manifest assets/tracks are invalid")
    if manifest["schema_version"] == PACK_VERSION:
        if _timeline(timeline) != timeline:
            raise ImportPackError("v2 timeline must use normalized decimal-string ticks")
    elif timeline is not None:
        # Read pre-v2 draft packs without rewriting their published manifest.
        _timeline(timeline)
    asset_ids: set[str] = set()
    asset_kinds: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not all(key in asset for key in ("asset_id", "kind", "relative_path", "content_hash", "size_bytes", "format")):
            raise ImportPackError("manifest asset is incomplete")
        asset_id = _id(asset["asset_id"], "asset_id")
        if asset_id in asset_ids:
            raise ImportPackError("manifest contains duplicate asset id")
        asset_ids.add(asset_id)
        if not isinstance(asset["kind"], str) or asset["kind"] not in _ASSET_KINDS or not isinstance(asset["content_hash"], str) or not _HASH.fullmatch(asset["content_hash"]):
            raise ImportPackError("manifest asset kind/hash is invalid")
        asset_kinds[asset_id] = asset["kind"]
        relative = asset["relative_path"]
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
            raise ImportPackError("manifest contains an unsafe relative path")
        path = root / Path(relative)
        _plain_under(root, path)
        if not path.is_file() or _hash_file(path) != asset["content_hash"] or str(path.stat().st_size) != asset["size_bytes"]:
            raise ImportPackError(f"asset validation failed: {asset_id}")
    orders: set[int] = set()
    track_ids: set[str] = set()
    active_tracks: dict[str, list[str]] = {"video": [], "audio": [], "subtitle": []}
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("asset_ids"), list):
            raise ImportPackError("manifest track is invalid")
        order = track.get("order")
        if type(order) is not int or not 0 <= order <= 31 or order in orders:
            raise ImportPackError("manifest track order is invalid")
        orders.add(order)
        if not track["asset_ids"] or any(not isinstance(identifier, str) for identifier in track["asset_ids"]) or not set(track["asset_ids"]) <= asset_ids:
            raise ImportPackError("manifest track references an unknown asset")
        track_id = _id(track.get("track_id"), "track_id")
        if track_id in track_ids or len(track_id) > 128:
            raise ImportPackError("manifest track id is invalid/duplicated")
        track_ids.add(track_id)
        if manifest["schema_version"] == PACK_VERSION:
            kind, role = track.get("kind"), track.get("role")
            if kind not in active_tracks or type(track.get("enabled_by_default")) is not bool or len(track["asset_ids"]) != 1:
                raise ImportPackError("v2 track selection is invalid")
            if role != asset_kinds[track["asset_ids"][0]]:
                raise ImportPackError("v2 track role differs from its asset")
            expected_kind = "audio" if role in {"original_audio", "dub_audio", "mixed_audio"} else role
            if kind != expected_kind or (kind == "video" and type(track.get("mute_embedded_audio")) is not bool):
                raise ImportPackError("v2 track kind/audio policy is invalid")
            if track["enabled_by_default"]:
                active_tracks[kind].append(role)
    if manifest["schema_version"] == PACK_VERSION:
        if any(len(roles) > 1 for roles in active_tracks.values()):
            raise ImportPackError("v2 alternatives/component stems cannot be enabled together")
        for cue in timeline["cues"]:
            if not set(cue.get("asset_ids", [])) <= asset_ids or ("track_id" in cue and cue["track_id"] not in track_ids):
                raise ImportPackError("timeline cue references an unknown pack asset/track")
    return manifest
