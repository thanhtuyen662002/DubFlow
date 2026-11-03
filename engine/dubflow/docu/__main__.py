"""python -m engine.dubflow.docu {context,check}; the last stdout line is ASCII JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from engine.dubflow.product.job import probe_duration_ms
from . import context, script
from .project import DocuError, load_spec, script_path, spec_path, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.docu")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("context", help="write each source's source_context.json")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--ffprobe", type=Path, required=True)
    verify = commands.add_parser("check", help="validate a draft and publish documentary_script.json")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--script", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "context":
            run_dir = args.spec.parent.parent
            spec = load_spec(args.spec)
            sources = context.build_all(run_dir, spec, lambda path: probe_duration_ms(args.ffprobe, path))
            result: dict[str, object] = {"state": "COMPLETED", "message": "", "sources": sources}
        else:
            spec = load_spec(spec_path(args.run_dir))
            result = script.check(script.load_draft(args.script), spec, script.load_contexts(args.run_dir, spec))
            write_json(script_path(args.run_dir), result)
    except DocuError as error:
        print(json.dumps({"state": "FAILED", "message": error.message}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
