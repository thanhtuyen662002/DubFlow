"""``python -m engine.dubflow.product``: render the product videos a spec describes.

Progress goes to ``<output>/.dubflow-work/status.json``; the last stdout line
is the final status as JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .job import ProductError, Status, load_spec, probe_duration_ms, probe_size, run

CPU_PROFILE = "models/manifests/production-cpu-v1.json"


class VieNeuSpeaker:
    """One VieNeu voice at natural pace, on the GPU when its verified pack allows."""

    def __init__(self, app_root: Path, model_root: Path, ffmpeg: Path, voice_id: str, *, cpu_only: bool, status: Status) -> None:
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

    def speak(self, text: str):
        return self.engine.natural_speech(text).samples

    def close(self) -> None:
        self.engine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.product")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--cpu-only", action="store_true")
    args = parser.parse_args(argv)
    output = args.output_dir.absolute()
    try:
        spec = load_spec(args.spec)
    except ProductError as error:
        work = output / ".dubflow-work"
        work.mkdir(parents=True, exist_ok=True)
        status = Status(work / "status.json", 0)
        status.write(state="FAILED", message=error.message)
        print(json.dumps(status.value))
        return 1

    def speaker(status: Status) -> VieNeuSpeaker:
        return VieNeuSpeaker(args.app_root, args.model_root, args.ffmpeg, spec.voice_id, cpu_only=args.cpu_only, status=status)

    result = run(spec, output, speaker=speaker, probe=lambda path: probe_duration_ms(args.ffprobe, path),
                 measure=lambda path: probe_size(args.ffprobe, path), ffmpeg=args.ffmpeg)
    print(json.dumps(result))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
