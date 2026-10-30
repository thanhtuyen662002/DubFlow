"""python -m engine.dubflow.compilation; last stdout line is ASCII JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from engine.dubflow.product.job import Status, probe_duration_ms
from engine.dubflow.product.speaker import VieNeuSpeaker
from .job import CompilationError, load_spec, probe_video, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.compilation")
    for name in ("spec", "output-dir", "app-root", "model-root", "ffmpeg", "ffprobe"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--cpu-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        producer = hashlib.sha256()
        for name in ("production-cpu-v1.json", "production-vieneu-v1.json"):
            profile = args.app_root / "models" / "manifests" / name
            if profile.is_file():
                producer.update(profile.read_bytes())
    except CompilationError as error:
        work = args.output_dir / ".dubflow-work"
        work.mkdir(parents=True, exist_ok=True)
        status = Status(work / "status.json", 0)
        status.write(state="FAILED", message=error.message, path=None)
        print(json.dumps(status.value, ensure_ascii=True))
        return 1
    result = run(spec, args.output_dir, speaker=lambda status: VieNeuSpeaker(
        args.app_root, args.model_root, args.ffmpeg, spec.voice_id, cpu_only=args.cpu_only, status=status, speed=spec.speed),
        probe=lambda path: probe_duration_ms(args.ffprobe, path), measure=lambda path: probe_video(args.ffprobe, path),
        ffmpeg=args.ffmpeg, producer_key=producer.hexdigest())
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
