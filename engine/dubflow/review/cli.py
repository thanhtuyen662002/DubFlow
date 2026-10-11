"""Bounded read-only review commands for the owned isolated runtime.

The native supervisor must supply a trusted completed manifest pin. This
command never writes review history, artifacts or durable job state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engine.dubflow.review.export import (MAX_JSON_BYTES, build_review_from_export,
                                        validate_review_preview, read_review_preview_bytes)
from engine.dubflow.review.production import ReviewError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "preview", "audio-v1"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--cue-id")
    parser.add_argument("--finding-id")
    parser.add_argument("--revision", type=int)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
            raise ReviewError("review command requires owned Python -I -S -B")
        if (args.action != "snapshot") != (args.cue_id is not None):
            raise ReviewError("only preview/audio requires a cue ID")
        if (args.action == "audio-v1") != (args.finding_id is not None and args.revision is not None):
            raise ReviewError("only audio-v1 requires finding/revision binding")
        if args.action != "audio-v1" and (args.finding_id is not None or args.revision is not None):
            raise ReviewError("unexpected audio binding")
        bound = build_review_from_export(args.manifest, expected_job_id=args.job_id,
                                         expected_manifest_sha256=args.manifest_sha256)
        if args.action == "audio-v1":
            finding = next((item for item in bound.snapshot.findings if item.finding_id == args.finding_id), None)
            if args.revision != bound.snapshot.revision or finding is None or finding.cue_id != args.cue_id or finding.start_ticks is None or finding.output_preview is None:
                raise ReviewError("audio request does not match current revision/finding/cue")
            raw = read_review_preview_bytes(bound, args.cue_id)
            sys.stdout.buffer.write(raw)
            return 0
        if args.action == "snapshot":
            result = bound.snapshot.to_dict()
        else:
            path = validate_review_preview(bound, args.cue_id)
            artifact = json.loads(bound.previews_json)[args.cue_id]
            result = {"schema_version": 1, "kind": "review_tts_preview", "job_id": bound.snapshot.job_id,
                      "cue_id": args.cue_id, "path": str(path), "content_hash": artifact["content_hash"],
                      "size_bytes": path.stat().st_size, "sample_rate": artifact["sample_rate"],
                      "channels": artifact["channels"], "frame_count": artifact["frame_count"]}
        encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > MAX_JSON_BYTES:
            raise ReviewError("review response exceeds JSON byte limit")
        sys.stdout.write(encoded + "\n")
        return 0
    except (ReviewError, OSError, UnicodeError, ValueError) as error:
        # Error data has a fixed prefix and byte bound; it cannot become a
        # snapshot or advertise a playable file after failed validation.
        message = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code": "REVIEW_INPUT_INVALID", "message": message}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
