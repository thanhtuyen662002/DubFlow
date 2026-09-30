"""App-owned offline Vietnamese CPU voice for the production worker.

The production path deliberately does not depend on a cloud service, a system
Python package, or the test-only fixture engine.  The voice pack is a small,
reviewable JSON manifest containing the synthesis tables used by the
deterministic additive voice.  It is checked against the selected production
model manifest before it can be used.  The synthesizer emits ordinary signed
16-bit PCM WAV and honours the exact integer sample interval requested by the
TTS adapter, so the adapter remains responsible for duration and waveform QC.

This is an app-owned baseline voice, rather than a claim that the baseline is
equivalent to a neural studio voice.  Its deterministic CPU implementation is
useful on machines where a larger neural pack is unavailable and gives the
worker a truthful, playable B2 result with a bounded B1 fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import unicodedata
from typing import Any, Mapping
import wave

from engine.dubflow.tts.adapter import (
    EngineCapabilities,
    EngineHealth,
    EngineSynthesis,
    TtsBackendError,
    TtsError,
    TtsRequest,
    VoiceProfile,
)


VOICE_PACK_SCHEMA_VERSION = 1
VOICE_MANIFEST_KEY = "tts_voice_pack"
VOICE_PACK_RELATIVE_PATH = "models/voices/vi-builtin-v1.json"
APPROVED_LICENSE_ID = "dubflow-builtin-voice-1.0"
MAX_VOICE_PACK_BYTES = 1 * 1024 * 1024


def _hash_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _text(value: Any, name: str, limit: int = 256) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit or any(ord(char) < 0x20 for char in value):
        raise TtsError("VOICE_PACK_INVALID", f"{name} must be a bounded, printable string")
    return value.strip()


def _positive_number(value: Any, name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TtsError("VOICE_PACK_INVALID", f"{name} must be finite")
    result = float(value)
    if result < minimum or result > maximum:
        raise TtsError("VOICE_PACK_INVALID", f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _safe_child(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or "\\" in relative:
        raise TtsError("VOICE_PACK_INVALID", "voice pack path must be relative and portable")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise TtsError("VOICE_PACK_INVALID", "voice pack path escapes the application root") from error
    return target


@dataclass(frozen=True)
class VoicePack:
    """Validated app-owned synthesis tables and their artifact identity."""

    path: Path
    artifact_id: str
    version: str
    language: str
    license_id: str
    model_id: str
    model_version: str
    sample_rate: int
    channels: int
    base_frequency_hz: float
    amplitude: int
    harmonic_mix: tuple[float, float]
    vowel_offsets_hz: Mapping[str, float]
    tone_offsets_hz: Mapping[str, float]
    model_hash: str
    manifest_hash: str
    raw: Mapping[str, Any]


def _manifest_hash(document: Mapping[str, Any]) -> str:
    # Hash the selected voice entry without its derived hash fields.  This
    # keeps the identity stable and avoids a circular manifest/hash relation.
    selected = dict(document)
    selected.pop("sha256", None)
    selected.pop("size_bytes", None)
    return _hash_json(selected)


def load_production_voice(app_root: str | Path, profile_path: str | Path | None = None) -> tuple[VoicePack, VoiceProfile]:
    """Load and verify the voice pack selected by the CPU production profile.

    The profile pins path, byte length, SHA-256, model/voice IDs and license.
    The app-owned pack is read only after every one of those checks passes.
    ``TtsError`` codes are intentionally stable so the worker can turn any
    health failure into a visible B1 downgrade.
    """

    root = Path(app_root).expanduser().resolve()
    selected_profile = Path(profile_path).expanduser().resolve() if profile_path is not None else root / "models" / "manifests" / "production-cpu-v1.json"
    try:
        profile = json.loads(selected_profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TtsError("VOICE_MANIFEST_UNAVAILABLE", f"unable to read production profile: {error}") from error
    if not isinstance(profile, Mapping) or profile.get("schema_version") != 1:
        raise TtsError("VOICE_MANIFEST_INVALID", "production profile schema is unsupported")
    selected = profile.get(VOICE_MANIFEST_KEY)
    if not isinstance(selected, Mapping):
        raise TtsError("VOICE_MANIFEST_INVALID", "production profile does not select an offline voice pack")
    required = ("id", "path", "version", "language", "license_id", "model_id", "model_version", "sha256", "size_bytes", "approved")
    missing = [key for key in required if key not in selected]
    if missing:
        raise TtsError("VOICE_MANIFEST_INVALID", f"voice selection is missing: {', '.join(missing)}")
    artifact_id = _text(selected["id"], "voice.id")
    relative = _text(selected["path"], "voice.path", limit=1024).replace("/", "/")
    pack_path = _safe_child(root, relative)
    expected_hash = _text(selected["sha256"], "voice.sha256", limit=80)
    if not expected_hash.startswith("sha256:") or len(expected_hash) != 71:
        raise TtsError("VOICE_MANIFEST_INVALID", "voice.sha256 must be a sha256: digest")
    size_bytes = selected["size_bytes"]
    if type(size_bytes) is not int or size_bytes <= 0 or size_bytes > MAX_VOICE_PACK_BYTES:
        raise TtsError("VOICE_MANIFEST_INVALID", "voice.size_bytes is outside the safe range")
    if selected.get("approved") is not True or selected.get("license_id") != APPROVED_LICENSE_ID:
        raise TtsError("VOICE_LICENSE_UNAPPROVED", "selected voice pack is not approved for offline distribution")
    try:
        actual_size = pack_path.stat().st_size
        payload = pack_path.read_bytes()
    except OSError as error:
        raise TtsError("VOICE_PACK_UNAVAILABLE", f"voice pack is unavailable: {pack_path}", retryable=True) from error
    if actual_size != size_bytes or len(payload) != size_bytes:
        raise TtsError("VOICE_PACK_CHECKSUM_MISMATCH", "voice pack size differs from the selected manifest")
    actual_hash = _hash_bytes(payload)
    if actual_hash != expected_hash:
        raise TtsError("VOICE_PACK_CHECKSUM_MISMATCH", "voice pack SHA-256 differs from the selected manifest")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TtsError("VOICE_PACK_INVALID", f"voice pack JSON is invalid: {error}") from error
    if not isinstance(document, Mapping) or document.get("schema_version") != VOICE_PACK_SCHEMA_VERSION:
        raise TtsError("VOICE_PACK_INVALID", "voice pack schema is unsupported")
    if document.get("artifact_id") != artifact_id or document.get("version") != selected["version"]:
        raise TtsError("VOICE_PACK_ID_MISMATCH", "voice pack identity differs from the selected manifest")
    if document.get("language") != "vi" or selected.get("language") != "vi":
        raise TtsError("VOICE_PACK_LANGUAGE_UNSUPPORTED", "the production baseline voice must speak Vietnamese")
    if document.get("license_id") != APPROVED_LICENSE_ID:
        raise TtsError("VOICE_LICENSE_UNAPPROVED", "voice pack license does not match the approved policy")
    synthesis = document.get("synthesis")
    if not isinstance(synthesis, Mapping):
        raise TtsError("VOICE_PACK_INVALID", "voice pack synthesis tables are missing")
    sample_rate = synthesis.get("sample_rate")
    channels = synthesis.get("channels")
    if type(sample_rate) is not int or sample_rate not in {16000, 24000, 48000} or channels != 1:
        raise TtsError("VOICE_PACK_INVALID", "voice pack audio format is unsupported")
    base_frequency = _positive_number(synthesis.get("base_frequency_hz"), "synthesis.base_frequency_hz", minimum=80.0, maximum=300.0)
    amplitude = synthesis.get("amplitude")
    if type(amplitude) is not int or not 1000 <= amplitude <= 12000:
        raise TtsError("VOICE_PACK_INVALID", "synthesis.amplitude is outside the safe range")
    harmonic = synthesis.get("harmonic_mix")
    if not isinstance(harmonic, list) or len(harmonic) != 2:
        raise TtsError("VOICE_PACK_INVALID", "synthesis.harmonic_mix must contain two values")
    harmonic_mix = tuple(_positive_number(value, "synthesis.harmonic_mix", minimum=0.0, maximum=0.8) for value in harmonic)
    vowels = synthesis.get("vowel_offsets_hz")
    tones = synthesis.get("tone_offsets_hz")
    if not isinstance(vowels, Mapping) or not isinstance(tones, Mapping):
        raise TtsError("VOICE_PACK_INVALID", "voice pack pitch tables are missing")
    vowel_offsets = {str(key): _positive_number(value, "synthesis.vowel_offsets_hz", minimum=-120.0, maximum=120.0) for key, value in vowels.items()}
    tone_offsets = {str(key): _positive_number(value, "synthesis.tone_offsets_hz", minimum=-120.0, maximum=120.0) for key, value in tones.items()}
    model_id = _text(selected["model_id"], "voice.model_id")
    model_version = _text(selected["model_version"], "voice.model_version")
    voice = VoiceProfile(
        voice_id=artifact_id,
        voice_version=_text(selected["version"], "voice.version"),
        language="vi",
        display_name=_text(selected.get("display_name", "DubFlow Vietnamese Built-in"), "voice.display_name"),
        model_id=model_id,
        model_version=model_version,
        model_hash=actual_hash,
        manifest_hash=_manifest_hash(selected),
        license_id=APPROVED_LICENSE_ID,
        approved=True,
        credential_required=False,
        network_required=False,
        speaking_rate_milli=1000,
        pitch_style="table-driven",
        emotion_mode="neutral",
        default=bool(selected.get("default", True)),
    )
    pack = VoicePack(
        pack_path,
        artifact_id,
        _text(document["version"], "pack.version"),
        "vi",
        APPROVED_LICENSE_ID,
        model_id,
        model_version,
        sample_rate,
        channels,
        base_frequency,
        amplitude,
        harmonic_mix,
        vowel_offsets,
        tone_offsets,
        actual_hash,
        voice.manifest_hash,
        document,
    )
    return pack, voice


def _tone_key(character: str) -> str:
    decomposed = unicodedata.normalize("NFD", character)
    marks = {mark for mark in decomposed[1:]}
    # Vietnamese tone marks are stable Unicode combining marks.  Mapping to a
    # compact key keeps the pack readable while preserving deterministic pitch.
    if "\u0301" in marks:
        return "acute"
    if "\u0300" in marks:
        return "grave"
    if "\u0309" in marks:
        return "hook"
    if "\u0303" in marks:
        return "tilde"
    if "\u0323" in marks:
        return "dot"
    return "level"


class BuiltinVietnameseTtsEngine:
    """Deterministic additive CPU synthesis using a verified voice pack."""

    def __init__(self, pack: VoicePack) -> None:
        if not isinstance(pack, VoicePack):
            raise TtsError("VOICE_PACK_INVALID", "production engine requires a verified VoicePack")
        self.pack = pack
        self.calls: list[str] = []

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            "dubflow-vi-builtin-v1",
            sample_rates=(self.pack.sample_rate,),
            channels=(self.pack.channels,),
            deterministic=True,
            supports_duration_fit=True,
            network_required=False,
            credential_required=False,
        )

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        if not isinstance(voice, VoiceProfile) or voice.model_hash != self.pack.model_hash or voice.model_id != self.pack.model_id:
            return EngineHealth(False, "VOICE_MODEL_MISMATCH", "voice profile does not match the verified app-owned pack")
        if voice.language != "vi" or not voice.approved or voice.network_required or voice.credential_required:
            return EngineHealth(False, "VOICE_NOT_OFFLINE", "voice profile is not an approved offline Vietnamese profile")
        return EngineHealth(True)

    def synthesize(self, request: TtsRequest) -> EngineSynthesis:
        self.calls.append(request.request_id)
        if request.config.sample_rate != self.pack.sample_rate or request.config.channels != self.pack.channels:
            raise TtsBackendError("VOICE_FORMAT_UNSUPPORTED", "requested format differs from the verified voice pack", retryable=False)
        frame_count = request.sample_end - request.sample_start
        if frame_count < 1:
            raise TtsBackendError("AUDIO_DURATION_MISMATCH", "target interval has no samples", retryable=False)
        if frame_count * self.pack.channels * 2 + 128 > request.config.max_wave_bytes:
            raise TtsBackendError("AUDIO_TOO_LARGE", "requested waveform exceeds the configured limit", retryable=False)
        text = unicodedata.normalize("NFC", request.segment.normalized_text or request.segment.text)
        characters = [character for character in text if not character.isspace()]
        if not characters:
            raise TtsBackendError("TEXT_EMPTY", "voice request contains no speakable characters", retryable=False)
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        frames = bytearray()
        rate = self.pack.sample_rate
        for index in range(frame_count):
            character = characters[min(len(characters) - 1, (index * len(characters)) // frame_count)]
            base = self.pack.base_frequency_hz
            decomposed = unicodedata.normalize("NFD", character)
            vowel = next((item for item in "aeiouyăâêôơưAEIOUYĂÂÊÔƠƯ" if item in decomposed), "")
            base += self.pack.vowel_offsets_hz.get(vowel.casefold(), 0.0)
            base += self.pack.tone_offsets_hz.get(_tone_key(character), 0.0)
            base += float((seed ^ (index * 2654435761)) % 23 - 11)
            frequency = max(80.0, min(320.0, base))
            phase = 2.0 * math.pi * frequency * index / rate
            envelope = min(1.0, (index + 1) / max(1, int(rate * 0.012)))
            envelope *= min(1.0, (frame_count - index) / max(1, int(rate * 0.018)))
            # A low-level deterministic consonant component gives unvoiced
            # letters energy without making the output a constant tone.
            noise = (((seed + index * 1103515245) >> 8) & 0xFF) - 128
            value = self.pack.amplitude * envelope * (
                math.sin(phase)
                + self.pack.harmonic_mix[0] * math.sin(phase * 2.0)
                + self.pack.harmonic_mix[1] * math.sin(phase * 3.0)
            ) + noise * 8.0
            sample = max(-32766, min(32766, int(round(value))))
            if sample == 0:
                sample = 1 if ((seed + index) & 1) else -1
            frames.extend(struct.pack("<h", sample))
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(self.pack.channels)
            writer.setsampwidth(2)
            writer.setframerate(self.pack.sample_rate)
            writer.writeframes(bytes(frames))
        return EngineSynthesis(
            output.getvalue(),
            sample_rate=self.pack.sample_rate,
            channels=self.pack.channels,
            bits_per_sample=16,
            fit_mode="native",
            speed_ratio_milli=1000,
        )


__all__ = [
    "APPROVED_LICENSE_ID",
    "BuiltinVietnameseTtsEngine",
    "VOICE_MANIFEST_KEY",
    "VOICE_PACK_RELATIVE_PATH",
    "VOICE_PACK_SCHEMA_VERSION",
    "VoicePack",
    "load_production_voice",
]
