"""``python -m engine.dubflow.product``: render the product videos a spec describes.

Progress goes to ``<output>/.dubflow-work/status.json``; the last stdout line
is the final status as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
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
    # The user's AI key for this job only (as for translation); out of the environment before FFmpeg starts.
    ai_key = os.environ.pop("DUBFLOW_AI_KEY", "") or ""
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

    inspect = None
    if spec.mastering is not None:
        from engine.dubflow.mastering import analysis

        def inspect(path: Path) -> analysis.StreamInfo:
            return analysis.probe(args.ffprobe, path)
    vision = None
    if spec.mastering is not None:
        vision = vision_factory(spec.editorial, ai_key)
    result = run(spec, output, speaker=speaker, probe=lambda path: probe_duration_ms(args.ffprobe, path),
                 measure=lambda path: probe_size(args.ffprobe, path), ffmpeg=args.ffmpeg, inspect=inspect, vision=vision)
    print(json.dumps(result))
    return 0 if result["state"] == "COMPLETED" else 1


def vision_factory(editorial, key: str):
    """A maker of vision describers on the user's AI route, or None (no AI chosen, no key, or a text-only model)."""
    if editorial is None:
        return None
    from engine.dubflow.mastering import vision
    return vision.factory(editorial.ai, key, editorial.vision_budget)


if __name__ == "__main__":
    sys.exit(main())
