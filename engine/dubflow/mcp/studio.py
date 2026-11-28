"""MCP tools for DubFlow's other makers (#996): Bán hàng (product videos) first.

A tool makes exactly the run the app's page makes: ``<base>\\DubFlow Output\\Ban hang - <name>\\run-<id>`` with
``.dubflow-work\\spec.json`` and ``status.json``, the app's run record, and ``python -m engine.dubflow.product`` started
in the background with its output in ``engine-stdout.log`` / ``engine-stderr.log``. The app lists such runs by
scanning, so a video an agent starts shows up in Bán hàng with its progress, and opens in Chỉnh sửa as DubFlow's own.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from engine.dubflow.docu.project import DocuError, write_json

PRODUCT_PREFIX = "Ban hang - "
_RUN = re.compile(r"^run-[0-9a-f]{12}$")
VOICE_MANIFEST = Path("models") / "manifests" / "production-vieneu-v1.json"
TEMPLATES = {"ban-tin": "Bản tin: video trong khung, chữ và giá ở dải bên",
             "toan-man-hinh": "Toàn màn hình: ảnh/clip phủ kín khung dọc",
             "chi-chu": "Chỉ chữ: không ảnh, chữ trên nền màu hoặc ảnh nền"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], *, read_only: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only}}


_RUN_ARG = {"type": "string", "description": "The run folder (…\\DubFlow Output\\Ban hang - <name>\\run-<id>) from create_product_video "
            "or list_product_videos"}

TOOLS = [
    _tool("list_vietnamese_voices", "DubFlow's Vietnamese voices (VieNeu, on this computer) for product videos and dubbing: voice_id, name, "
          "gender, accent, style.", {}, [], read_only=True),
    _tool("create_product_video", "Make vertical sales videos (Bán hàng) like the app's page: one video per script (1-5 scripts, you write "
          "them in Vietnamese: hook, benefits, price, call to action), spoken by a Vietnamese voice over the product's photos and clips "
          "with captions, the price and up to 8 highlights. Starts in the background (a minute or more per video); poll "
          "get_product_video. The run appears in the app's Bán hàng list and opens in Chỉnh sửa.",
          {"name": {"type": "string", "maxLength": 120, "description": "Product name, shown on screen"},
           "price": {"type": "string", "maxLength": 60, "description": "e.g. '299.000đ'"},
           "scripts": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "maxLength": 3000},
                       "description": "Narration scripts, one video each"},
           "visuals": {"type": "array", "maxItems": 30, "items": {"type": "string"},
                       "description": "Absolute paths of photos (JPG, PNG, WEBP, BMP) or clips (MP4, MOV, MKV, WEBM, AVI, M4V); "
                                      "1-30, none for chi-chu"},
           "template": {"type": "string", "enum": list(TEMPLATES), "description": "; ".join(f"{key}: {text}" for key, text in TEMPLATES.items())},
           "features": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}, "description": "Highlights on screen"},
           "voice_id": {"type": "string", "description": "From list_vietnamese_voices"},
           "speed": {"type": "number", "minimum": 1.0, "maximum": 1.3, "description": "Default 1.0"},
           "music": {"type": "string", "description": "Absolute path of a music file (MP3, M4A, AAC, WAV, OGG, FLAC)"},
           "music_volume": {"type": "number", "minimum": 0, "maximum": 1, "description": "Default 0.15"},
           "background": {"type": "string", "description": "Absolute path of a background image (chi-chu)"},
           "output_root": {"type": "string", "description": "Folder that gets 'DubFlow Output'; default next to the first visual, or Videos"}},
          ["name", "scripts", "voice_id"]),
    _tool("get_product_video", "Progress and results of a product video run: state (RUNNING, COMPLETED, FAILED, CANCELLED), how many "
          "videos are done, the message, and the finished MP4 files.", {"run": _RUN_ARG}, ["run"], read_only=True),
    _tool("list_product_videos", "Product video runs (Bán hàng), newest first, with their state.",
          {"root": {"type": "string", "description": "A 'DubFlow Output' folder; default the user's Videos\\DubFlow Output"}}, [],
          read_only=True),
]


def voices(app_root: Path) -> list[dict[str, Any]]:
    path = app_root / VOICE_MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Danh mục giọng tiếng Việt chưa được cài đặt") from error
    return [{key: voice.get(key) for key in ("voice_id", "name", "gender", "accent", "style", "description")}
            for voice in manifest.get("voices") or [] if isinstance(voice, dict) and voice.get("approved")]


def _folder_title(name: str) -> str:
    cleaned = " ".join("".join(" " if ord(c) < 32 or c in '<>:"/\\|?*' else c for c in name).split())
    return cleaned[:40].rstrip(" .") or "san pham"  # product.rs NAME_CHARS


def _absolute_file(value: Any, what: str) -> str:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_file():
        raise DocuError(f"{what}: cần đường dẫn đầy đủ tới một file có thật ({value})")
    return str(path)


def _run_dir(value: Any) -> Path:
    """A product run folder: …\\DubFlow Output\\Ban hang - <name>\\run-<12 hex> with its work folder, not a link."""
    path = Path(str(value or ""))
    if not path.is_absolute():
        raise DocuError("run phải là đường dẫn đầy đủ của thư mục run-…")
    path = path.resolve()
    if (not _RUN.match(path.name) or not path.parent.name.startswith(PRODUCT_PREFIX) or path.parent.parent.name != "DubFlow Output"
            or not (path / ".dubflow-work" / "spec.json").is_file()):
        raise DocuError("Không phải thư mục video bán hàng của DubFlow")
    return path


def _status(run: Path) -> dict[str, Any]:
    try:
        value = json.loads((run / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _python(app_root: Path) -> Path:
    bundled = app_root.parent / "runtime" / "python.exe"
    return bundled if bundled.is_file() else Path(sys.executable)


def _spawn(argv: list[str], work: Path, cwd: Path, env: dict[str, str] | None = None) -> None:
    """Start an engine in the background; it outlives this MCP process (the app does the same from its own)."""
    stdout = open(work / "engine-stdout.log", "wb")  # noqa: SIM115 - handed to the child
    stderr = open(work / "engine-stderr.log", "wb")  # noqa: SIM115
    attempts = [0]
    if sys.platform == "win32":
        base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        # Out of the MCP client's job object when it allows that, so closing the client does not stop the render.
        attempts = [base | 0x01000000, base]  # CREATE_BREAKAWAY_FROM_JOB
    try:
        for index, flags in enumerate(attempts):
            try:
                subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, creationflags=flags,
                                 close_fds=True, env=env)
                return
            except OSError:
                if index == len(attempts) - 1:
                    raise
    finally:
        stdout.close()
        stderr.close()


def create_product_video(args: dict[str, Any], install: Any, *, record_run: Any, base_default: Path,
                         spawn: Any = _spawn) -> dict[str, Any]:
    from engine.dubflow.product.job import ProductError, load_spec

    name = " ".join(str(args.get("name") or "").split())[:120]
    if not name:
        raise DocuError("Cần tên sản phẩm")
    scripts = args.get("scripts")
    if not isinstance(scripts, list) or not 1 <= len(scripts) <= 5 or not all(isinstance(text, str) and text.strip() for text in scripts):
        raise DocuError("Cần 1–5 kịch bản (mỗi kịch bản một video)")
    template = args.get("template") or "ban-tin"
    if template not in TEMPLATES:
        raise DocuError("Mẫu video phải là " + ", ".join(TEMPLATES))
    visuals = [] if template == "chi-chu" else [_absolute_file(item, "Ảnh/clip") for item in (args.get("visuals") or [])]
    if template != "chi-chu" and not 1 <= len(visuals) <= 30:
        raise DocuError("Cần 1–30 ảnh hoặc clip của sản phẩm")
    voice_id = str(args.get("voice_id") or "")
    if voice_id not in {voice["voice_id"] for voice in voices(install.app_root)}:
        raise DocuError("Giọng không có trong danh mục; xem list_vietnamese_voices")
    music = _absolute_file(args["music"], "Nhạc nền") if args.get("music") else None
    background = _absolute_file(args["background"], "Ảnh nền") if args.get("background") else None
    if args.get("output_root"):
        base = Path(str(args["output_root"]))
        if not base.is_absolute() or not base.is_dir():
            raise DocuError("output_root phải là thư mục có thật (đường dẫn đầy đủ)")
    else:
        base = Path(visuals[0]).parent if visuals else base_default
    seed = f"{time.time_ns()}:{os.getpid()}:{name}"
    run = base / "DubFlow Output" / f"{PRODUCT_PREFIX}{_folder_title(name)}" / f"run-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    work = run / ".dubflow-work"
    spec = {"schema_version": 1, "name": name, "price": " ".join(str(args.get("price") or "").split())[:60], "voice_id": voice_id,
            "variants": [text.strip() for text in scripts], "visuals": visuals, "music": music,
            "music_volume": float(args.get("music_volume", 0.15)), "background": background, "template": template,
            "speed": float(args.get("speed") or 1.0), "features": [" ".join(str(item).split()) for item in (args.get("features") or [])],
            "focus": {"x": 0.5, "y": 0.5}, "reuse_audio_from": None}
    work.mkdir(parents=True, exist_ok=False)
    spec_path = work / "spec.json"
    write_json(spec_path, spec)
    try:
        load_spec(spec_path)  # the engine's own check, before anything starts
    except ProductError as error:
        write_json(work / "status.json", {"schema_version": 1, "state": "FAILED", "done": 0, "total": 0, "message": error.message, "videos": []})
        raise DocuError(error.message) from error
    write_json(work / "status.json", {"schema_version": 1, "state": "RUNNING", "done": 0, "total": len(scripts),
                                      "message": "Đang khởi động…", "videos": []})
    record_run("product", run)
    spawn([str(_python(install.app_root)), "-B", "-m", "engine.dubflow.product", "--spec", str(spec_path), "--output-dir", str(run),
           "--app-root", str(install.app_root), "--model-root", str(install.model_root), "--ffmpeg", str(install.ffmpeg),
           "--ffprobe", str(install.ffprobe)], work, install.app_root)
    return {"run": str(run), "videos_planned": len(scripts), "template": template,
            "next": "get_product_video until state is COMPLETED (about a minute or more per video)"}


def product_video(run_value: Any) -> dict[str, Any]:
    run = _run_dir(run_value)
    status = _status(run)
    videos = sorted(str(path) for path in run.glob("*.mp4"))
    return {"run": str(run), "state": status.get("state"), "done": status.get("done"), "total": status.get("total"),
            "message": status.get("message"), "videos": videos}


def list_product_videos(roots: list[Path]) -> list[dict[str, Any]]:
    found = []
    for root in roots:
        for title in sorted(root.glob(PRODUCT_PREFIX + "*")):
            for run in title.glob("run-*"):
                if _RUN.match(run.name) and (run / ".dubflow-work" / "spec.json").is_file():
                    status = _status(run)
                    found.append({"run": str(run), "name": title.name[len(PRODUCT_PREFIX):], "state": status.get("state"),
                                  "created": run.stat().st_mtime, "videos": len(list(run.glob("*.mp4")))})
    return sorted(found, key=lambda item: -item["created"])[:60]


def record_run(data_root: Path):
    """The app's run record (edit.rs record_run_in) for a run of ``kind``."""
    def write(kind: str, run: Path) -> None:
        folder = run.resolve()
        key = hashlib.sha256(str(folder).lower().encode("utf-8")).hexdigest()[:20]
        path = data_root / "control" / "runs" / f"{key}.json"
        write_json(path, {"schema_version": 1, "kind": kind, "dir": str(folder)})
    return write


__all__ = ["TOOLS", "create_product_video", "list_product_videos", "product_video", "record_run", "voices"]
