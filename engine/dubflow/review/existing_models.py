"""Read-only admission of an already installed, job-pinned VieNeu pack.

Review regeneration cannot provision or repair models. Native code supplies the
original job's model root after admitting the complete owned import inventory.
This adapter verifies the physical pack before constructing an inference engine.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import time

from engine.dubflow.models.runtime import ModelArtifact
from engine.dubflow.tts import vieneu
from engine.dubflow.tts.neural_vits import _child
from engine.dubflow.worker.export_publication import plain
from .cue_plan import _read_metadata, catalog_voice
from .production import ReviewError
from .synthesis_worker import admit_production_cue


def _inventory(root: Path, expected: set[str], deadline: float) -> None:
    directories = {str(Path(name).parent).replace("\\", "/") for name in expected} - {"."}
    observed = set()
    pending = [root]
    count = 0
    while pending:
        parent = pending.pop()
        plain(parent)
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > 64 or time.monotonic() > deadline:
                    raise ReviewError("existing model inventory exceeded its bound")
                path = Path(entry.path)
                plain(path)
                relative = path.relative_to(root).as_posix()
                if entry.is_dir(follow_symlinks=False) and relative in directories:
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and relative in expected:
                    observed.add(relative)
                else:
                    raise ReviewError("foreign or nonregular data in the existing model pack")
    if observed != expected:
        raise ReviewError("original model pack is incomplete; restore it explicitly before Review")


def _verify(path: Path, artifact: ModelArtifact, deadline: float) -> tuple[int, int, int]:
    plain(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size != artifact.size_bytes or
            getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ReviewError("original model size or regular-file identity changed")
        digest = hashlib.sha256()
        remaining = artifact.size_bytes
        while remaining:
            if time.monotonic() > deadline:
                raise ReviewError("existing model hashing exceeded its bounded deadline")
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ReviewError("original model became shorter while hashing")
            remaining -= len(chunk)
            digest.update(chunk)
        if stream.read(1) or digest.hexdigest() != artifact.sha256:
            raise ReviewError("original model checksum changed; explicit restoration required")
        plain(path)
        after = path.stat()
        identity = (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if identity != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ReviewError("original model changed during admission")
        return identity


def admit_existing_cue_pack(request, *, app_root: Path, model_root: Path, profile_path: Path):
    admit_production_cue(request.provenance, request.config)
    app_root, model_root, profile_path = map(Path, (app_root, model_root, profile_path))
    if not model_root.is_absolute() or ".." in model_root.parts:
        raise ReviewError("original model root is not an absolute native path")
    plain(model_root)
    if not model_root.is_dir():
        raise ReviewError("original model root is unavailable; Review never provisions it")
    voice = catalog_voice(app_root, profile_path, request.voice.voice_id)
    if voice != request.voice:
        raise ReviewError("installed voice/model profile differs from the pinned request")
    _, selection = _read_metadata(profile_path)
    metadata = _child(app_root, selection["tts_neural_profile"])
    _, profile = _read_metadata(metadata)
    tree = profile["model_tree_sha256"]
    prefix = "tts/packs/vieneu-" + tree + "/"
    installed = _child(model_root, prefix[:-1])
    if not installed.is_dir():
        raise ReviewError("original model pack is unavailable; Review never provisions it")
    artifacts = tuple(ModelArtifact.from_mapping(item) for item in profile["artifacts"])
    expected = {item.relative_path[len(prefix):] for item in artifacts}
    deadline = time.monotonic() + 180
    _inventory(installed, expected, deadline)
    identities = {}
    for item in artifacts:
        path = _child(model_root, item.relative_path)
        identities[path] = _verify(path, item, deadline)
    _inventory(installed, expected, deadline)
    for path, identity in identities.items():
        plain(path)
        observed = path.stat()
        if identity != (observed.st_ino, observed.st_size, observed.st_mtime_ns):
            raise ReviewError("original model pack changed after hashing")
    _, voices = _read_metadata(installed / "voices.json")
    if voice.display_name not in voices.get("presets", {}):
        raise ReviewError("selected voice is absent from the original physical preset roster")
    pack = vieneu.VieNeuVoicePack(installed, 48000, 1, voice.model_hash, voice.manifest_hash,
        voice.license_id, voice.voice_id, voice.voice_version, voice.model_id, voice.model_version,
        voice_name=voice.display_name)
    return pack
