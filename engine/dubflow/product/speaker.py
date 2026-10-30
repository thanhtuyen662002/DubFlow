"""Shared natural VieNeu narration for product and packaged videos."""
from __future__ import annotations

from pathlib import Path
import time

from .job import ProductError, Status

CPU_PROFILE = "models/manifests/production-cpu-v1.json"


class VieNeuSpeaker:
    """One VieNeu voice at natural pace, on the GPU when its verified pack allows."""

    def __init__(self, app_root: Path, model_root: Path, ffmpeg: Path, voice_id: str, *, cpu_only: bool, status: Status,
                 speed: float = 1.0) -> None:
        from engine.dubflow.accelerator import nvidia
        from engine.dubflow.tts import vieneu

        pack, voice = vieneu.load_vieneu_voice(app_root, model_root, app_root / CPU_PROFILE, voice_id=voice_id)
        last = [0.0]

        def download(_artifact: str, done: int, total: int) -> None:
            if done < total and time.monotonic() - last[0] < 1.0:
                return
            last[0] = time.monotonic()
            status.write(message=f"Đang tải thư viện GPU NVIDIA lần đầu: {done // 1_000_000}/{total // 1_000_000} MB")
        device = nvidia.select_tts_device(app_root, model_root, "cpu" if cpu_only else "auto", progress=download)
        accelerator = device.runtime if device.device == "cuda" else None
        self.engine = vieneu.VieNeuVietnameseTtsEngine(pack, ffmpeg_path=ffmpeg, accelerator=accelerator)
        health = self.engine.healthcheck(voice)
        if not health.ready:
            self.engine.close()
            raise ProductError(f"Không khởi động được giọng đọc: {health.code}")
        self.sample_rate = pack.sample_rate
        self.speed = speed

    def speak(self, text: str):
        return self.engine.natural_speech(text, speed=self.speed).samples

    def close(self) -> None:
        self.engine.close()


