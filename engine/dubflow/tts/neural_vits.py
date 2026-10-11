"""Pinned, app-owned Vietnamese neural speech behind the TTS contract.

Inference is offline. First-use provisioning reuses the resumable model
downloader and verifies an archive and its complete extracted data tree.
Conversion scripts shipped by the upstream archive are never installed.
"""

from __future__ import annotations

from array import array
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
from typing import Any, Iterator, Mapping
import wave

from engine.dubflow.models import ensure_model_profile
from engine.dubflow.tts.adapter import (
    EngineCapabilities, EngineHealth, EngineSynthesis, TtsBackendError,
    TtsError, TtsRequest, VoiceProfile,
)

ENGINE_ID = "mimic3-vits-onnx-v1"
RUNTIME_VERSION = "1.13.8"
ONNX_RUNTIME_VERSION = "1.30.0"
FRONTEND_ID = "mimic3-word-blanks-v1"
PROFILE_PATH = "models/manifests/production-tts-v1.json"
MAX_FILES = 2048
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 512 * 1024
IGNORED_CONVERTERS = {"vits-mimic3.py", "vits-mimic3.sh"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_json(path: Path, limit: int = MAX_METADATA_BYTES) -> dict[str, Any]:
    try:
        _plain_path(path)
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as error:
        raise TtsError("VOICE_MANIFEST_UNAVAILABLE", "voice metadata cannot be read") from error
    if len(payload) > limit:
        raise TtsError("VOICE_MANIFEST_INVALID", "voice metadata exceeds its size bound")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise TtsError("VOICE_MANIFEST_INVALID", "voice metadata is not valid JSON") from error
    if type(value) is not dict:
        raise TtsError("VOICE_MANIFEST_INVALID", "voice metadata must be an object")
    return value


def _plain_path(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise TtsError("VOICE_PATH_UNSAFE", "voice paths cannot contain links or junctions")


def _child(root: Path, relative: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative or ":" in relative:
        raise TtsError("VOICE_PATH_UNSAFE", "voice data requires a portable relative path")
    parts = relative.split("/")
    if PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise TtsError("VOICE_PATH_UNSAFE", "voice data path escapes its root")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in reserved or any(ord(c) < 32 for c in part):
            raise TtsError("VOICE_PATH_UNSAFE", "voice data path is unsafe on Windows")
    target = root.joinpath(*parts)
    _plain_path(target)
    target.resolve().relative_to(root.resolve())
    return target


@contextmanager
def _install_lock(root: Path) -> Iterator[None]:
    """Serialize downloads/installations across worker processes."""
    _plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".neural-vits.lock"
    _plain_path(lock_path)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise TtsError("VOICE_INSTALL_BUSY", "another worker is provisioning this voice", retryable=True) from error
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _tree_valid(root: Path, expected: str) -> bool:
    if not root.exists():
        return False
    _plain_path(root)
    try:
        metadata = _read_json(root / ".installed.json")
        version = metadata.get("schema_version", 0)
        if type(version) is not int or version not in {0, 1}:
            return False
        files = metadata.get("files")
        if type(files) is not dict or not 1 <= len(files) <= MAX_FILES:
            return False
        if hashlib.sha256(_json_bytes(files)).hexdigest() != expected:
            return False
        seen: set[str] = set()
        for parent, directories, names in os.walk(root, followlinks=False):
            for name in directories:
                _plain_path(Path(parent) / name)
            for name in names:
                path = Path(parent) / name
                _plain_path(path)
                relative = path.relative_to(root).as_posix()
                if relative != ".installed.json":
                    seen.add(relative)
                if len(seen) > MAX_FILES:
                    return False
        if seen != set(files):
            return False
        for relative, record in files.items():
            target = _child(root, relative)
            if target.stat().st_size != record["size_bytes"] or _digest(target) != record["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _unpack_archive(archive: Path, destination: Path, archive_root: str, expected_tree: str) -> None:
    """Extract data only, with fixed roots, file/count limits and atomic commit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    _plain_path(destination.parent)
    temporary = Path(tempfile.mkdtemp(prefix=".vits-install-", dir=destination.parent))
    records: dict[str, dict[str, Any]] = {}
    total = 0
    try:
        with tarfile.open(archive, "r:bz2") as reader:
            for index, member in enumerate(reader):
                if index >= MAX_FILES:
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive has too many entries")
                parts = member.name.split("/")
                if not parts or parts[0] != archive_root:
                    raise TtsError("VOICE_PATH_UNSAFE", "voice archive has an unexpected root")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise TtsError("VOICE_PATH_UNSAFE", "voice archive contains links or special files")
                if len(parts) == 1:
                    if not member.isdir():
                        raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive root must be a directory")
                    continue
                relative = "/".join(parts[1:])
                target = _child(temporary, relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if member.size < 0 or total > MAX_EXPANDED_BYTES:
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive exceeds the expanded size bound")
                if relative in IGNORED_CONVERTERS:
                    continue
                if relative in records or relative == ".installed.json":
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive contains duplicate or reserved data")
                if not (relative.startswith("espeak-ng-data/") or relative in {"tokens.txt", "vi_VN-vais1000_low.onnx", "vi_VN-vais1000_low.onnx.json", "README.md"}):
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive includes unexpected data")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = reader.extractfile(member)
                if source is None:
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive member is unreadable")
                digest = hashlib.sha256()
                written = 0
                with source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > member.size:
                            raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive member exceeds its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if written != member.size:
                    raise TtsError("VOICE_ARCHIVE_INVALID", "voice archive member is truncated")
                records[relative] = {"sha256": digest.hexdigest(), "size_bytes": written}
        if hashlib.sha256(_json_bytes(records)).hexdigest() != expected_tree:
            raise TtsError("VOICE_PACK_CHECKSUM_MISMATCH", "extracted voice data differs from the pinned tree")
        with (temporary / ".installed.json").open("xb") as marker:
            marker.write(_json_bytes({"schema_version": 1, "files": records}))
            marker.flush()
            os.fsync(marker.fileno())
        if destination.exists():
            # Retain damaged data for diagnosis; never delete an in-use pack.
            _plain_path(destination)
            quarantine = destination.with_name(destination.name + ".corrupt-" + str(time.time_ns()))
            os.replace(destination, quarantine)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            if temporary.resolve().parent != destination.parent.resolve() or temporary.is_symlink():
                raise TtsError("VOICE_PATH_UNSAFE", "refusing cleanup outside the voice staging root")
            shutil.rmtree(temporary)


@dataclass(frozen=True)
class NeuralVoicePack:
    path: Path
    sample_rate: int
    channels: int
    model_hash: str
    manifest_hash: str
    license_id: str
    voice_id: str
    version: str
    model_id: str
    model_version: str
    noise_scale: float = 0.0
    noise_scale_w: float = 0.0


def load_neural_voice(app_root: str | Path, model_root: str | Path, profile_path: str | Path) -> tuple[NeuralVoicePack, VoiceProfile]:
    app = Path(app_root).expanduser().absolute()
    selected = _read_json(Path(profile_path))
    neural_path = _child(app, selected.get("tts_neural_profile", ""))
    profile = _read_json(neural_path)
    if profile.get("schema_version") != 1 or profile.get("backend") != ENGINE_ID:
        raise TtsError("VOICE_MANIFEST_INVALID", "production profile does not select the neural backend")
    if profile.get("license_id") != "CC-BY-4.0" or profile.get("approved") is not True:
        raise TtsError("VOICE_LICENSE_UNAPPROVED", "neural voice license is not approved")
    if profile.get("runtime_version") != RUNTIME_VERSION or profile.get("sample_rate") != 22050:
        raise TtsError("VOICE_MANIFEST_INVALID", "neural voice runtime/audio profile differs from its pinned version")
    from .mimic3_native import INVENTORY_SHA256
    if profile.get("frontend") != FRONTEND_ID or profile.get("onnxruntime_version") != ONNX_RUNTIME_VERSION or profile.get("phoneme_inventory_sha256") != INVENTORY_SHA256:
        raise TtsError("VOICE_MANIFEST_INVALID", "neural frontend/runtime inventory differs from its pinned version")
    inference = profile.get("inference", {"noise_scale": 0.0, "noise_scale_w": 0.0})
    if type(inference) is not dict:
        raise TtsError("VOICE_MANIFEST_INVALID", "neural inference recipe must be an object")
    noise = []
    for key in ("noise_scale", "noise_scale_w"):
        value = inference.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise TtsError("VOICE_MANIFEST_INVALID", "neural inference noise must be finite and bounded")
        noise.append(float(value))
    tree_hash = profile.get("extracted_tree_sha256")
    if type(tree_hash) is not str or len(tree_hash) != 64 or any(c not in "0123456789abcdef" for c in tree_hash):
        raise TtsError("VOICE_MANIFEST_INVALID", "neural voice tree requires a SHA-256 digest")
    artifacts = profile.get("artifacts")
    if type(artifacts) is not list or len(artifacts) != 1:
        raise TtsError("VOICE_MANIFEST_INVALID", "neural voice requires one pinned data archive")
    cache = Path(model_root).expanduser().absolute()
    _plain_path(cache)
    with _install_lock(cache):
        ensure_model_profile(neural_path, cache)
        archive = _child(cache, artifacts[0]["path"])
        installed = cache / "tts" / "packs" / tree_hash
        if not _tree_valid(installed, tree_hash):
            _unpack_archive(archive, installed, profile["archive_root"], tree_hash)
        if not _tree_valid(installed, tree_hash):
            raise TtsError("VOICE_PACK_CHECKSUM_MISMATCH", "installed neural voice failed integrity validation")
    model_hash = "sha256:" + tree_hash
    manifest_hash = "sha256:" + _digest(neural_path)
    pack = NeuralVoicePack(installed, 22050, 1, model_hash, manifest_hash, "CC-BY-4.0", profile["voice_id"], profile["voice_version"], profile["model_id"], profile["model_version"], *noise)
    voice = VoiceProfile(
        voice_id=pack.voice_id, voice_version=pack.version, language="vi",
        display_name="Vietnamese VAIS1000", model_id=pack.model_id,
        model_version=pack.model_version, model_hash=model_hash,
        manifest_hash=manifest_hash, license_id=pack.license_id, approved=True,
        network_required=False, credential_required=False, default=True,
    )
    return pack, voice


class NeuralVietnameseTtsEngine:
    def __init__(self, pack: NeuralVoicePack) -> None:
        self.pack = pack
        self._tts: Any = None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(ENGINE_ID, sample_rates=(self.pack.sample_rate,), deterministic=False)

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        if voice.model_hash != self.pack.model_hash or voice.voice_id != self.pack.voice_id:
            return EngineHealth(False, "VOICE_PACK_ID_MISMATCH", "requested voice differs from the verified neural pack")
        try:
            versions = (("sherpa-onnx", RUNTIME_VERSION), ("sherpa-onnx-core", RUNTIME_VERSION), ("onnxruntime", ONNX_RUNTIME_VERSION))
            if any(importlib.metadata.version(name) != expected for name, expected in versions):
                return EngineHealth(False, "TTS_RUNTIME_VERSION_MISMATCH", "native runtime differs from its pinned version")
            if self._tts is None:
                from .native_process import NativeProcess
                self._tts = NativeProcess(self.pack)
            process = getattr(self._tts, "process", None)
            if process is not None and process.poll() is not None:
                return EngineHealth(False, "TTS_NATIVE_EXITED", "native speech process is no longer running")
            return EngineHealth(True)
        except (ImportError, importlib.metadata.PackageNotFoundError):
            return EngineHealth(False, "TTS_RUNTIME_MISSING", "app-owned sherpa-onnx runtime is missing")
        except Exception as error:
            return EngineHealth(False, getattr(error, "code", "TTS_MODEL_LOAD_FAILED"), "neural model initialization failed: " + str(error)[:1000])

    def close(self) -> None:
        close = getattr(self._tts, "close", None)
        if close is not None:
            close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def synthesize(self, request: TtsRequest) -> EngineSynthesis:
        health = self.healthcheck(request.voice)
        if not health.ready:
            raise TtsBackendError(health.code, health.condition, retryable=False)
        if request.config.sample_rate != self.pack.sample_rate or request.config.channels != 1:
            raise TtsBackendError("TTS_AUDIO_FORMAT_UNSUPPORTED", "neural voice requires its native mono sample rate")
        if len(request.segment.text) > 512:
            raise TtsBackendError("TTS_TEXT_TOO_LONG", "neural cue exceeds the bounded inference text size")
        target = request.sample_end - request.sample_start
        max_frames = (request.config.max_wave_bytes - 44) // 2
        if target < 1 or target > max_frames:
            raise TtsBackendError("TTS_AUDIO_TOO_LARGE", "requested cue exceeds the waveform memory bound")
        try:
            target = self._target_frames(request, target, max_frames)
            audio, speed_milli = self._duration_fit(request, target)
            samples = audio.samples
            if audio.sample_rate != self.pack.sample_rate or not 0 < len(samples) <= max_frames:
                raise TtsBackendError("TTS_AUDIO_INVALID", "neural inference produced invalid waveform metadata")
            if len(samples) > target:
                if any(abs(float(value)) > 0.0001 for value in samples[target:]):
                    raise TtsBackendError("DURATION_FIT_REQUIRED", "fitted speech still exceeds the cue; spoken samples will not be cut")
                samples = samples[:target]
            peak = 0.0
            for value in samples:
                number = float(value)
                if not math.isfinite(number):
                    raise TtsBackendError("TTS_AUDIO_INVALID", "neural waveform contains non-finite samples")
                peak = max(peak, abs(number))
            if peak <= 0.0001:
                raise TtsBackendError("TTS_AUDIO_INVALID", "neural waveform contains no audible speech signal")
            gain = min(1.0, 0.94 / peak)
            pcm = array("h", (round(max(-1.0, min(1.0, float(value) * gain)) * 32767) for value in samples))
            pcm.extend(array("h", [0]) * (target - len(pcm)))
            if os.sys.byteorder != "little":
                pcm.byteswap()
            output = io.BytesIO()
            with wave.open(output, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(self.pack.sample_rate)
                writer.writeframes(pcm.tobytes())
            mode = "speed_adjusted" if speed_milli != 1000 else "padded" if len(samples) < target else "native"
            return EngineSynthesis(output.getvalue(), sample_rate=self.pack.sample_rate, fit_mode=mode, speed_ratio_milli=speed_milli, warnings=tuple(getattr(audio, "warnings", ())))
        except TtsError:
            raise
        except Exception as error:
            raise TtsBackendError("TTS_INFERENCE_FAILED", str(error)[:1000], retryable=False) from error

    def _target_frames(self, request: TtsRequest, target: int, max_frames: int) -> int:
        return target

    def _duration_fit(self, request: TtsRequest, target: int, *, max_speed_milli: int | None = None):
        audio = self._tts.generate(request.segment.text, sid=0, speed=1.0)
        speed_milli = 1000
        if len(audio.samples) > target:
            speed_milli = math.ceil(len(audio.samples) * 1000 / target)
            maximum = request.config.max_speed_ratio_milli if max_speed_milli is None else min(max_speed_milli, request.config.max_speed_ratio_milli)
            if speed_milli > maximum:
                raise TtsBackendError("DURATION_FIT_REQUIRED", "natural speech cannot fit this cue within the safe speaking rate")
            audio = self._tts.generate(request.segment.text, sid=0, speed=speed_milli / 1000)
        return audio, speed_milli
