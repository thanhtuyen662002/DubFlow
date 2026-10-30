"""``python -B -m engine.dubflow.package``; last stdout line is ASCII JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from engine.dubflow.product.job import probe_duration_ms
from engine.dubflow.product.speaker import VieNeuSpeaker
from .job import PackageError, Status, load_spec, probe_video, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.package")
    for name in ("spec", "output-dir", "app-root", "model-root", "ffmpeg", "ffprobe"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--cpu-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if spec.video.parent.resolve() != args.output_dir.resolve():
            raise PackageError("Thư mục đóng gói không khớp video")
        producer = hashlib.sha256()
        for name in ("production-cpu-v1.json", "production-vieneu-v1.json"):
            profile = args.app_root / "models" / "manifests" / name
            if profile.is_file():
                producer.update(profile.read_bytes())
    except PackageError as error:
        work = args.output_dir / ".dubflow-work" / "package"
        work.mkdir(parents=True, exist_ok=True)
        status = Status(work / "status.json")
        status.write(state="FAILED", message=error.message)
        print(json.dumps(status.value, ensure_ascii=True))
        return 1
    result = run(spec, speaker=lambda status: VieNeuSpeaker(args.app_root, args.model_root, args.ffmpeg, spec.voice_id,
                 cpu_only=args.cpu_only, status=status, speed=spec.speed), probe=lambda path: probe_duration_ms(args.ffprobe, path),
                 measure=lambda path: probe_video(args.ffprobe, path), ffmpeg=args.ffmpeg,
                 producer_key=producer.hexdigest())
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
