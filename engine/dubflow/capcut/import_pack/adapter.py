"""Portable CapCut import-pack adapter with no CapCut runtime dependency."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import tempfile


PACK_VERSION = 1
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_FILENAME = re.compile(r"^[^\x00-\x1f\\/:*?\"<>|]{1,128}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


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
class ImportPackRequest:
    pack_id: str
    source_path: Path
    subtitle_path: Path | None = None
    dub_audio_path: Path | None = None
    original_audio_path: Path | None = None
    subtitle_format: str | None = None

    def __post_init__(self) -> None:
        _id(self.pack_id, "pack_id")
        for name, path in (("source_path", self.source_path), ("subtitle_path", self.subtitle_path), ("dub_audio_path", self.dub_audio_path), ("original_audio_path", self.original_audio_path)):
            if path is not None and (not isinstance(path, Path) or not path.is_file()):
                raise ImportPackError(f"{name} must identify an existing file")
        if self.subtitle_path is not None:
            suffix = self.subtitle_format or self.subtitle_path.suffix.removeprefix(".").lower()
            if suffix not in {"srt", "ass", "vtt"}:
                raise ImportPackError("subtitle format must be srt, ass or vtt")


@dataclass(frozen=True)
class ImportPackResult:
    root: Path
    manifest: dict[str, object]


def _asset(asset_id: str, kind: str, source: Path, destination: Path, root: Path, format_name: str) -> dict[str, object]:
    _id(asset_id, "asset_id")
    if kind not in {"video", "original_audio", "dub_audio", "subtitle"}:
        raise ImportPackError("unsupported asset kind")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    relative = destination.relative_to(root).as_posix()
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ImportPackError("generated asset path escaped pack root")
    return {
        "asset_id": asset_id,
        "kind": kind,
        "relative_path": relative,
        "content_hash": _hash_file(destination),
        "size_bytes": str(destination.stat().st_size),
        "format": format_name,
    }


def _manifest(pack_id: str, assets: list[dict[str, object]]) -> dict[str, object]:
    tracks: list[dict[str, object]] = []
    video = [asset["asset_id"] for asset in assets if asset["kind"] == "video"]
    audio = [asset["asset_id"] for asset in assets if asset["kind"] in {"original_audio", "dub_audio"}]
    subtitle = [asset["asset_id"] for asset in assets if asset["kind"] == "subtitle"]
    tracks.append({"track_id": "video-0", "kind": "video", "order": 0, "asset_ids": video, "language": None})
    if audio:
        tracks.append({"track_id": "audio-0", "kind": "audio", "order": 1, "asset_ids": audio, "language": None})
    if subtitle:
        tracks.append({"track_id": "subtitle-0", "kind": "subtitle", "order": 2, "asset_ids": subtitle, "language": "vi"})
    return {"schema_version": PACK_VERSION, "kind": "capcut_import_pack", "pack_id": pack_id, "assets": assets, "tracks": tracks}


def create_import_pack(request: ImportPackRequest, output_root: Path) -> ImportPackResult:
    if output_root.exists():
        raise ImportPackError("OUTPUT_EXISTS")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{request.pack_id}.partial-", dir=output_root.parent))
    try:
        assets: list[dict[str, object]] = []
        assets.append(_asset("source-video", "video", request.source_path, temporary / "media" / _safe_filename(request.source_path.name), temporary, "mp4"))
        if request.original_audio_path is not None:
            assets.append(_asset("source-audio", "original_audio", request.original_audio_path, temporary / "audio" / _safe_filename(request.original_audio_path.name), temporary, request.original_audio_path.suffix.removeprefix(".").lower() or "audio"))
        if request.dub_audio_path is not None:
            assets.append(_asset("dub-audio", "dub_audio", request.dub_audio_path, temporary / "audio" / _safe_filename(request.dub_audio_path.name), temporary, request.dub_audio_path.suffix.removeprefix(".").lower() or "audio"))
        if request.subtitle_path is not None:
            format_name = request.subtitle_format or request.subtitle_path.suffix.removeprefix(".").lower()
            assets.append(_asset("vietsub", "subtitle", request.subtitle_path, temporary / "subtitles" / _safe_filename(request.subtitle_path.name), temporary, format_name))
        manifest = _manifest(request.pack_id, assets)
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output_root)
        return ImportPackResult(output_root, manifest)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_import_pack(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ImportPackError("manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportPackError("manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PACK_VERSION or manifest.get("kind") != "capcut_import_pack":
        raise ImportPackError("unsupported import pack manifest")
    assets = manifest.get("assets")
    tracks = manifest.get("tracks")
    if not isinstance(assets, list) or not assets or not isinstance(tracks, list) or not tracks:
        raise ImportPackError("manifest assets/tracks are invalid")
    asset_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or not all(key in asset for key in ("asset_id", "kind", "relative_path", "content_hash", "size_bytes", "format")):
            raise ImportPackError("manifest asset is incomplete")
        asset_id = _id(asset["asset_id"], "asset_id")
        if asset_id in asset_ids:
            raise ImportPackError("manifest contains duplicate asset id")
        asset_ids.add(asset_id)
        relative = asset["relative_path"]
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
            raise ImportPackError("manifest contains an unsafe relative path")
        path = root / Path(relative)
        if not path.is_file() or _hash_file(path) != asset["content_hash"] or str(path.stat().st_size) != asset["size_bytes"]:
            raise ImportPackError(f"asset validation failed: {asset_id}")
    orders: set[int] = set()
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("asset_ids"), list):
            raise ImportPackError("manifest track is invalid")
        order = track.get("order")
        if type(order) is not int or order in orders:
            raise ImportPackError("manifest track order is invalid")
        orders.add(order)
        if not set(track["asset_ids"]) <= asset_ids:
            raise ImportPackError("manifest track references an unknown asset")
    return manifest
