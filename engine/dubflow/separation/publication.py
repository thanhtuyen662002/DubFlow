"""Version-1 immutable audio generations; current.json is the only commit point.

The supervisor serializes publishers for an output directory. Readers resolve
the pointer once, then retain generation paths. This module never deletes data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from .production import SeparationResult
NAMES = ("source_audio.wav", "dialogue_stem.wav", "background_stem.wav")
STORE = ".audio-generations"
CURRENT = "current-audio.json"
MAX_DOCUMENT_BYTES = 64 * 1024


def _plain(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400 or (not directory and info.st_nlink != 1):
        raise ValueError("audio publication requires regular unlinked paths")


def _sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through Python. Process-death
    # safety uses same-volume replace; power-loss durability is a separate gate.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_document(path: Path, document: dict) -> None:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("audio publication document exceeds bound")
    with path.open("xb") as stream:
        if stream.write(payload) != len(payload):
            raise OSError("short audio publication document write")
        stream.flush()
        os.fsync(stream.fileno())


def _read_document(path: Path) -> dict:
    _plain(path)
    with path.open("rb") as stream:
        payload = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("audio publication document exceeds bound")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("audio publication document must be an object")
    return value


def prepare(destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    _plain(destination, directory=True)
    store = destination / STORE
    store.mkdir(exist_ok=True)
    _plain(store, directory=True)
    generation = uuid.uuid4().hex
    pending = store / f".{generation}.partial"
    pending.mkdir()
    return pending, store / generation


def read_result(document: dict) -> SeparationResult:
    """Verify an existing schema-v2 result, including historical flat paths.

    A bare legacy directory has no completion evidence: callers must supply its
    recorded result/hashes. Never infer success from three filenames alone.
    """
    from .production import SeparationConfig, SeparationMetrics, SeparationResult, _hash, _read_pcm_metadata
    import wave

    if type(document.get("schema_version")) is not int or document["schema_version"] != 2:
        raise ValueError("unsupported separation result version")
    metrics = SeparationMetrics(**document["metrics"])
    paths = tuple(Path(document[key]) for key in ("source_path", "dialogue_path", "background_path"))
    if len(set(paths)) != 3 or len({path.parent for path in paths}) != 1:
        raise ValueError("audio result paths must identify one complete set")
    for path, name, digest in zip(paths, NAMES, (metrics.source_hash, metrics.dialogue_hash, metrics.background_hash)):
        if not path.is_absolute() or path.name != name:
            raise ValueError("audio result path is invalid")
        _plain(path.parent, directory=True)
        _plain(path)
        if _read_pcm_metadata(path, SeparationConfig(max_duration_seconds=7 * 24 * 3600)) != (metrics.sample_rate, metrics.channels, metrics.frame_count):
            raise ValueError("audio result PCM metadata mismatch")
        # Header-only validation cannot detect truncated payloads.
        with wave.open(str(path), "rb") as reader:
            count = 0
            while raw := reader.readframes(4096):
                if len(raw) % (metrics.channels * 2):
                    raise ValueError("audio result contains a partial PCM frame")
                count += len(raw) // (metrics.channels * 2)
            if count != metrics.frame_count:
                raise ValueError("audio result PCM frame count mismatch")
        if _hash(path) != digest:
            raise ValueError("audio result hash mismatch")
    warnings = document["warnings"]
    if not isinstance(warnings, list) or len(warnings) > 64 or any(not isinstance(item, str) or len(item) > 4096 for item in warnings):
        raise ValueError("invalid audio result warnings")
    if type(document.get("fallback")) is not bool or document["fallback"] != (metrics.decision != "AUD-2"):
        raise ValueError("audio result fallback mismatch")
    return SeparationResult(*paths, metrics, tuple(warnings))


def publish(pending: Path, final: Path, result: SeparationResult) -> None:
    """Seal and verify all members, then atomically select that generation."""
    from .production import _hash

    candidate = result.to_dict()
    for key, name in zip(("source_path", "dialogue_path", "background_path"), NAMES):
        candidate[key] = str(pending / name)
    read_result(candidate)
    manifest = pending / "result.json"
    document = {"publication_version": 1, "generation": final.name, "result": result.to_dict()}
    _write_document(manifest, document)
    if _read_document(manifest) != document:
        raise ValueError("audio publication manifest readback mismatch")
    manifest_hash = _hash(manifest)
    _sync_directory(pending)
    os.replace(pending, final)
    _sync_directory(final.parent)
    destination = final.parent.parent
    pointer = destination / f".current-audio.{final.name}.partial"
    selection = {"publication_version": 1, "generation": final.name, "manifest_hash": manifest_hash}
    _write_document(pointer, selection)
    if _read_document(pointer) != selection:
        raise ValueError("audio publication pointer readback mismatch")
    # No member or old path is mutated here. A reader sees all A or all B.
    os.replace(pointer, destination / CURRENT)
    _sync_directory(destination)


def load_current(output_dir: str | Path) -> SeparationResult | None:
    """Resolve once and verify; orphans/partials are never promoted on restart.

    None means no generation was published. Corruption is an error, not a reason
    to select an arbitrary orphan or trust an unrecorded legacy directory.
    """
    from .production import _hash

    destination = Path(output_dir).expanduser().resolve()
    pointer_path = destination / CURRENT
    if not pointer_path.exists() and not pointer_path.is_symlink():
        return None
    pointer = _read_document(pointer_path)
    generation = pointer.get("generation")
    if type(pointer.get("publication_version")) is not int or pointer["publication_version"] != 1 or not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ValueError("invalid audio publication pointer")
    _plain(destination / STORE, directory=True)
    final = destination / STORE / generation
    _plain(final, directory=True)
    manifest = final / "result.json"
    document = _read_document(manifest)
    if _hash(manifest) != pointer.get("manifest_hash") or type(document.get("publication_version")) is not int or document["publication_version"] != 1 or document.get("generation") != generation:
        raise ValueError("audio publication manifest mismatch")
    for key, name in zip(("source_path", "dialogue_path", "background_path"), NAMES):
        if document["result"].get(key) != str(final / name):
            raise ValueError("audio publication escaped its generation")
    return read_result(document["result"])
