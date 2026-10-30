"""``python -m engine.dubflow.shorts``; final stdout line is ASCII JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.dubflow.product.job import probe_duration_ms
from .job import ShortsError, Status, load_spec, probe_video, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    for name in ("spec", "output-dir", "ffmpeg", "ffprobe"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if spec.video.parent != args.output_dir.resolve():
            raise ShortsError("Thư mục Shorts không khớp video")
    except ShortsError as error:
        work = args.output_dir / ".dubflow-work" / "shorts"
        work.mkdir(parents=True, exist_ok=True)
        status = Status(work / "status.json", 0)
        status.write(state="FAILED", message=error.message)
        result = status.value
    else:
        result = run(spec, probe=lambda p: probe_duration_ms(args.ffprobe, p), measure=lambda p: probe_video(args.ffprobe, p), ffmpeg=args.ffmpeg)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
