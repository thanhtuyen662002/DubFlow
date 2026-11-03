"""python -m engine.dubflow.docu {context,check,narrate,plan,render,capcut}; the last stdout line is ASCII JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from engine.dubflow.product.job import probe_duration_ms
from . import capcut_draft, context, narration, roughcut, script, visual
from .project import DocuError, load_spec, script_path, spec_path, write_json


def _narrate(args: argparse.Namespace) -> dict[str, object]:
    from engine.dubflow.tts import kokoro

    run_dir = args.run_dir
    load_spec(spec_path(run_dir))
    script_value = script.load_draft(script_path(run_dir))
    profile = args.app_root / kokoro.PROFILE_PATH
    producer = hashlib.sha256(profile.read_bytes()).hexdigest() if profile.is_file() else ""

    def speaker(report):
        def progress(_artifact: str, done: int, total: int) -> None:
            report(f"Đang tải giọng đọc tiếng Anh lần đầu: {done // 1_000_000}/{total // 1_000_000} MB")
        try:
            return kokoro.KokoroSpeaker(args.app_root, args.model_root, args.voice, speed=args.speed, progress=progress)
        except kokoro.KokoroError as error:
            raise DocuError(error.message) from error
    return narration.run(run_dir, script_value, speaker=speaker, voice_id=args.voice, speed=args.speed,
                         ffmpeg=args.ffmpeg, producer=producer)


def _plan(args: argparse.Namespace) -> dict[str, object]:
    spec = load_spec(spec_path(args.run_dir))
    profile = args.app_root / visual.OCR_PROFILE
    if args.no_ocr or not profile.is_file():
        scanner, identity = None, None
    else:
        from engine.dubflow.ocr import caption_scan
        from engine.dubflow.models.runtime import ensure_model_profile

        manifest = json.loads(profile.read_text(encoding="utf-8"))
        identity = {"recipe": caption_scan.SCAN_RECIPE, "profile": hashlib.sha256(profile.read_bytes()).hexdigest()}

        def scanner():
            ensure_model_profile(profile, args.model_root)
            return caption_scan.CaptionScanner(args.model_root / manifest["model_path"], args.ffmpeg)
    return visual.run(args.run_dir, spec, ffmpeg=args.ffmpeg, scanner=scanner, ocr_identity=identity)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.docu")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("context", help="write each source's source_context.json")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--ffprobe", type=Path, required=True)
    verify = commands.add_parser("check", help="validate a draft and publish documentary_script.json")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--script", type=Path, required=True)
    speak = commands.add_parser("narrate", help="speak documentary_script.json into narration.wav and its timeline")
    for name in ("run-dir", "app-root", "model-root", "ffmpeg"):
        speak.add_argument(f"--{name}", type=Path, required=True)
    speak.add_argument("--voice", required=True)
    speak.add_argument("--speed", type=float, default=1.0)
    visuals = commands.add_parser("plan", help="cut the sources into the visual plan the rough cut follows")
    for name in ("run-dir", "app-root", "model-root", "ffmpeg"):
        visuals.add_argument(f"--{name}", type=Path, required=True)
    visuals.add_argument("--no-ocr", action="store_true", help="skip the caption scan")
    cut = commands.add_parser("render", help="render the visual plan and the narration into documentary.mp4")
    for name in ("run-dir", "ffmpeg", "ffprobe"):
        cut.add_argument(f"--{name}", type=Path, required=True)
    capcut = commands.add_parser("capcut", help="write the rough cut as a CapCut draft and list it in CapCut")
    capcut.add_argument("--run-dir", type=Path, required=True)
    capcut.add_argument("--root", type=Path, help="CapCut's draft folder (default: CapCut's own)")
    args = parser.parse_args(argv)
    try:
        if args.command == "context":
            run_dir = args.spec.parent.parent
            spec = load_spec(args.spec)
            sources = context.build_all(run_dir, spec, lambda path: probe_duration_ms(args.ffprobe, path))
            result: dict[str, object] = {"state": "COMPLETED", "message": "", "sources": sources}
        elif args.command == "capcut":
            exported = capcut_draft.export(args.run_dir, load_spec(spec_path(args.run_dir)), root=args.root or capcut_draft.default_root())
            result = {"state": "COMPLETED", "message": "", **exported}
        elif args.command == "render":
            result = roughcut.run(args.run_dir, load_spec(spec_path(args.run_dir)), ffmpeg=args.ffmpeg,
                                  probe=lambda video: roughcut.probe_display(args.ffprobe, video))
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result.get("state") == "COMPLETED" else 1
        elif args.command in ("narrate", "plan"):
            result = _narrate(args) if args.command == "narrate" else _plan(args)
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result.get("state") == "COMPLETED" else 1
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
