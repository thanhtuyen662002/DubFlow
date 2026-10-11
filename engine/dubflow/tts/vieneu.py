"""Pinned app-owned VieNeu Turbo model provisioning and TTS adapter."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import math
import os
import re
from pathlib import Path

from engine.dubflow.models.runtime import ModelArtifact, ensure_model_profile
from .adapter import EngineCapabilities, EngineHealth, TtsError, VoiceProfile
from .neural_vits import (
    NeuralVoicePack, NeuralVietnameseTtsEngine, _child, _digest, _install_lock,
    _json_bytes, _plain_path, _read_json,
)
from .vieneu_native import FRONTEND_ID, VERSIONS, INFERENCE_RECIPE

ENGINE_ID = "vieneu-v3-turbo-onnx-v1"
PRODUCER_VERSION = "3.4.0"
RUNTIME_ID = "vieneu-3.8.3+sea-g2p-0.9.1+onnxruntime-1.30.0+tokenizers-0.23.2+numpy-2.2.6"
PROFILE_PATH = "models/manifests/production-vieneu-v1.json"
FILES = frozenset({
    "tts/config.json", "tts/tokenizer.json", "tts/vieneu_acoustic_cached.onnx",
    "tts/vieneu_backbone_shared.data", "tts/vieneu_decode_step.onnx",
    "tts/vieneu_prefill.onnx", "tts/vieneu_v3_heads.npz", "tts/README.md",
    "codec/moss_audio_tokenizer_decode_full.onnx", "codec/moss_audio_tokenizer_decode_shared.data",
    "codec/README.md", "voices.json", "LICENSE",
})


@dataclass(frozen=True)
class VieNeuVoicePack(NeuralVoicePack):
    voice_name: str = ""


def voice_choices(profile):
    choices = profile.get("voices")
    if type(choices) is not list or not 1 <= len(choices) <= 64:
        raise TtsError("VOICE_MANIFEST_INVALID", "bounded preset catalog is missing")
    seen = set()
    for item in choices:
        if type(item) is not dict or type(item.get("voice_id")) is not str or not re.fullmatch(r"[a-z0-9-]{1,96}", item["voice_id"]) or item["voice_id"] in seen or item.get("approved") is not True:
            raise TtsError("VOICE_MANIFEST_INVALID", "duplicate, invalid or unapproved preset voice")
        if any(type(item.get(key)) is not str or not 0 < len(item[key]) <= 160 for key in ("name", "gender", "accent", "style", "description", "voice_version")):
            raise TtsError("VOICE_MANIFEST_INVALID", "invalid preset display metadata")
        seen.add(item["voice_id"])
    if profile.get("voice_id") not in seen:
        raise TtsError("VOICE_MANIFEST_INVALID", "default preset is absent from the catalog")
    return tuple(choices)


def load_vieneu_voice(app_root, model_root, profile_path, *, voice_id=None):
    app = Path(app_root).expanduser().absolute()
    selected = _read_json(Path(profile_path))
    metadata = _child(app, selected.get("tts_neural_profile", ""))
    profile = _read_json(metadata)
    if profile.get("schema_version") != 1 or profile.get("backend") != ENGINE_ID or profile.get("frontend") != FRONTEND_ID or profile.get("runtime_versions") != VERSIONS or profile.get("sample_rate") != 48000 or profile.get("inference") != INFERENCE_RECIPE:
        raise TtsError("VOICE_MANIFEST_INVALID", "VieNeu recipe/runtime differs from its pinned version")
    if profile.get("license_id") != "Apache-2.0" or profile.get("approved") is not True:
        raise TtsError("VOICE_LICENSE_UNAPPROVED", "VieNeu voice license is not approved")
    choices = voice_choices(profile)
    selected_id = profile["voice_id"] if voice_id is None else voice_id
    choice = next((item for item in choices if item["voice_id"] == selected_id), None)
    if choice is None:
        raise TtsError("VOICE_ID_UNKNOWN", "requested voice is absent from the approved pinned catalog")
    tree = profile.get("model_tree_sha256")
    if type(tree) is not str or len(tree) != 64 or any(c not in "0123456789abcdef" for c in tree):
        raise TtsError("VOICE_MANIFEST_INVALID", "VieNeu requires a model inventory SHA-256")
    pack_relative = "tts/packs/vieneu-" + tree
    records = {}
    try:
        artifacts = profile["artifacts"]
        if type(artifacts) is not list or len(artifacts) != len(FILES):
            raise ValueError("unexpected model inventory size")
        for value in artifacts:
            artifact = ModelArtifact.from_mapping(value)
            prefix = pack_relative + "/"
            if not artifact.relative_path.startswith(prefix):
                raise ValueError("artifact escapes the immutable voice pack")
            relative = artifact.relative_path[len(prefix):]
            if relative in records or relative not in FILES or artifact.size_bytes <= 0:
                raise ValueError("unexpected or duplicate voice data")
            records[relative] = {"sha256": artifact.sha256, "size_bytes": artifact.size_bytes}
        if set(records) != FILES or hashlib.sha256(_json_bytes(records)).hexdigest() != tree:
            raise ValueError("model inventory does not match its pinned hash")
    except Exception as error:
        raise TtsError("VOICE_MANIFEST_INVALID", "VieNeu model inventory is invalid") from error
    cache = Path(model_root).expanduser().absolute()
    installed = _child(cache, pack_relative)
    with _install_lock(cache):
        # Reject foreign/linked data before invoking the resumable downloader.
        if installed.exists():
            for parent, dirs, names in os.walk(installed, followlinks=False):
                for name in (*dirs, *names):
                    _plain_path(Path(parent) / name)
                for name in names:
                    path = Path(parent) / name
                    relative = path.relative_to(installed).as_posix()
                    if relative not in FILES and not any(relative == str(Path(f).with_name("." + Path(f).name + ".partial")).replace("\\", "/") for f in FILES):
                        raise TtsError("VOICE_PATH_UNSAFE", "foreign data in the immutable voice pack")
        ensure_model_profile(metadata, cache)
        for relative, record in records.items():
            path = _child(installed, relative)
            if not path.is_file() or path.stat().st_size != record["size_bytes"] or _digest(path) != record["sha256"]:
                raise TtsError("VOICE_PACK_CHECKSUM_MISMATCH", "VieNeu data differs from its pinned inventory")
    voice_name = choice["name"]
    voices = _read_json(installed / "voices.json", 256 * 1024)
    if type(voice_name) is not str or voice_name not in voices.get("presets", {}):
        raise TtsError("VOICE_MANIFEST_INVALID", "selected preset is absent from the pinned voice roster")
    pack = VieNeuVoicePack(installed, 48000, 1, "sha256:" + tree, "sha256:" + _digest(metadata), "Apache-2.0", choice["voice_id"], choice["voice_version"], profile["model_id"], profile["model_version"], voice_name=voice_name)
    voice = VoiceProfile(voice_id=pack.voice_id, voice_version=pack.version, language="vi", display_name=voice_name,
        model_id=pack.model_id, model_version=pack.model_version, model_hash=pack.model_hash, manifest_hash=pack.manifest_hash,
        license_id=pack.license_id, approved=True, network_required=False, credential_required=False, default=pack.voice_id == profile["voice_id"])
    return pack, voice


class VieNeuVietnameseTtsEngine(NeuralVietnameseTtsEngine):
    def __init__(self, pack: VieNeuVoicePack, *, ffmpeg_path=None):
        super().__init__(pack)
        self.ffmpeg_path = ffmpeg_path

    def capabilities(self):
        return EngineCapabilities(ENGINE_ID, sample_rates=(48000,), deterministic=False)

    def _target_frames(self, request, target, max_frames):
        window = request.segment.render_window_end
        if window is None:
            return target
        base = request.segment.start.time_base
        # Floor limits the measured duration; ceil could cross the next cue.
        limit = (window.ticks - request.segment.start.ticks) * base.numerator * self.pack.sample_rate // base.denominator
        if not target <= limit <= max_frames:
            raise TtsError("TTS_AUDIO_TOO_LARGE", "render window exceeds the waveform/timeline bound")
        natural = self._tts.generate(request.segment.text, sid=0, speed=1.0)
        if natural.sample_rate != self.pack.sample_rate or not 0 < len(natural.samples) <= max_frames:
            raise TtsError("TTS_AUDIO_INVALID", "natural speech has invalid waveform metadata")
        # The native child caches this exact natural speech for later tempo
        # passes. Consume only the needed interval, never pad the entire gap.
        return max(target, min(limit, len(natural.samples)))

    def _duration_fit(self, request, target):
        audio, speed_milli = super()._duration_fit(request, target, max_speed_milli=1300)
        # FFmpeg atempo is not an exact frame-count division. Measure residual
        # overshoot and change the rate, using the child's cached natural speech.
        # Natural speech that already exceeds the safe rate remains a failure.
        margin = max(1, self.pack.sample_rate * 5 // 1000)
        for _ in range(2):
            if speed_milli == 1000 or len(audio.samples) <= target:
                break
            next_speed = math.ceil(speed_milli * (len(audio.samples) + margin) / target)
            if not speed_milli < next_speed <= min(1300, request.config.max_speed_ratio_milli):
                break
            speed_milli = next_speed
            audio = self._tts.generate(request.segment.text, sid=0, speed=speed_milli / 1000)
        return audio, speed_milli

    def healthcheck(self, voice):
        if voice.model_hash != self.pack.model_hash or voice.voice_id != self.pack.voice_id:
            return EngineHealth(False, "VOICE_PACK_ID_MISMATCH", "requested voice differs from the verified VieNeu pack")
        try:
            if any(importlib.metadata.version(name) != version for name, version in VERSIONS.items()):
                return EngineHealth(False, "TTS_RUNTIME_VERSION_MISMATCH", "VieNeu runtime differs from its pinned version")
            if self._tts is None:
                from .native_process import NativeProcess
                self._tts = NativeProcess(self.pack, entrypoint=Path(__file__).with_name("vieneu_native.py"), frontend=FRONTEND_ID, sample_rate=48000,
                    initialization={"voice_name": self.pack.voice_name, "ffmpeg_path": str(self.ffmpeg_path) if self.ffmpeg_path else None})
            process = getattr(self._tts, "process", None)
            if process is not None and process.poll() is not None:
                return EngineHealth(False, "TTS_NATIVE_EXITED", "native speech process is no longer running")
            return EngineHealth(True)
        except (ImportError, importlib.metadata.PackageNotFoundError):
            return EngineHealth(False, "TTS_RUNTIME_MISSING", "app-owned VieNeu runtime is missing")
        except Exception as error:
            return EngineHealth(False, getattr(error, "code", "TTS_MODEL_LOAD_FAILED"), "VieNeu initialization failed: " + str(error)[:1000])
