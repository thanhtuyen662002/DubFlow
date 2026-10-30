"""``python -m engine.dubflow.product``: render the product videos a spec describes.

Progress goes to ``<output>/.dubflow-work/status.json``; the last stdout line
is the final status as JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .job import ProductError, Status, load_spec, probe_duration_ms, probe_size, run
from .speaker import VieNeuSpeaker


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
        return VieNeuSpeaker(args.app_root, args.model_root, args.ffmpeg, spec.voice_id, cpu_only=args.cpu_only, status=status,
                             speed=spec.speed)

    result = run(spec, output, speaker=speaker, probe=lambda path: probe_duration_ms(args.ffprobe, path),
                 measure=lambda path: probe_size(args.ffprobe, path), ffmpeg=args.ffmpeg)
    print(json.dumps(result))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
