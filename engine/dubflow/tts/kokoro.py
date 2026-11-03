"""English narration voices: Kokoro v1.0 through the bundled sherpa-onnx (ADR-0049).

The pack is downloaded on first use from the pinned, hash-verified
``production-kokoro-en-v1.json``; only its US/UK English voices are offered.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Sequence

PROFILE_PATH = "models/manifests/production-kokoro-en-v1.json"
MIN_SPEED, MAX_SPEED = 0.8, 1.2

Progress = Callable[[str, int, int], None]


class KokoroError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Voice:
    voice_id: str
    speaker_id: int
    name: str
    lexicon: str
    lang: str


@dataclass(frozen=True)
class Profile:
    pack: str
    sample_rate: int
    threads: int
    voices: tuple[Voice, ...]
    default_voice_id: str

    def voice(self, voice_id: str) -> Voice:
        for voice in self.voices:
            if voice.voice_id == voice_id:
                return voice
        raise KokoroError("Giọng đọc tiếng Anh đã chọn không có trong phiên bản này")


def load_profile(app_root: Path) -> Profile:
    try:
        value = json.loads((app_root / PROFILE_PATH).read_text(encoding="utf-8"))
        voices = tuple(Voice(item["voice_id"], int(item["speaker_id"]), item["name"], item["lexicon"], item["lang"])
                       for item in value["voices"])
        profile = Profile(value["pack"], int(value["sample_rate"]), int(value["inference"]["threads"]), voices,
                          value["default_voice_id"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise KokoroError("Không đọc được danh sách giọng tiếng Anh") from error
    if not voices or len({voice.voice_id for voice in voices}) != len(voices):
        raise KokoroError("Danh sách giọng tiếng Anh không hợp lệ")
    profile.voice(profile.default_voice_id)
    return profile


class KokoroSpeaker:
    """One English voice; ``speak`` returns float samples at ``sample_rate``."""

    def __init__(self, app_root: Path, model_root: Path, voice_id: str, *, speed: float = 1.0,
                 progress: Progress | None = None, engine_factory: Callable[[Any], Any] | None = None) -> None:
        if not MIN_SPEED <= speed <= MAX_SPEED:
            raise KokoroError("Tốc độ đọc phải từ 0,8 đến 1,2")
        profile = load_profile(app_root)
        self.voice = profile.voice(voice_id)
        self.speed = speed
        self.sample_rate = profile.sample_rate
        if engine_factory is None:
            from engine.dubflow.models.runtime import ModelBootstrapError, ensure_model_profile
            try:
                ensure_model_profile(app_root / PROFILE_PATH, model_root, progress=progress)
            except ModelBootstrapError as error:
                raise KokoroError("Không tải được giọng đọc tiếng Anh; hãy kiểm tra mạng rồi thử lại") from error
            engine_factory = _sherpa
        pack = Path(model_root) / profile.pack
        self._tts = engine_factory({"model": pack / "model.onnx", "voices": pack / "voices.bin", "tokens": pack / "tokens.txt",
                                    "data_dir": pack / "espeak-ng-data", "lexicon": pack / self.voice.lexicon,
                                    "lang": self.voice.lang, "threads": profile.threads})

    def speak(self, text: str) -> Sequence[float]:
        audio = self._tts.generate(text, sid=self.voice.speaker_id, speed=self.speed)
        if audio.sample_rate != self.sample_rate:
            raise KokoroError("Giọng đọc tiếng Anh trả về sai tần số mẫu")
        return audio.samples

    def close(self) -> None:
        self._tts = None


def _sherpa(paths: dict[str, Any]) -> Any:
    import sherpa_onnx

    kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(paths["model"]), voices=str(paths["voices"]), tokens=str(paths["tokens"]),
        data_dir=str(paths["data_dir"]), lexicon=str(paths["lexicon"]), lang=paths["lang"])
    config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
        kokoro=kokoro, num_threads=paths["threads"], provider="cpu"))
    if not config.validate():
        raise KokoroError("Gói giọng đọc tiếng Anh chưa đầy đủ; hãy thử lại để tải lại")
    return sherpa_onnx.OfflineTts(config)
