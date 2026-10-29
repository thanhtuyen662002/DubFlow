"""Spleeter separation on an NVIDIA GPU, in its own isolated process (ADR-0039).

The worker may already hold the CUDA 12 ASR pack, whose cuDNN shares DLL names
with the CUDA 13 build of ONNX Runtime, so only this process loads the latter.
It verifies and loads the pack before anything imports onnxruntime, then runs
the same separation pipeline as the CPU path. One JSON request arrives on stdin;
progress and the result leave as JSON lines on stdout.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


# Replies own stdout. Libraries print there too (ONNX Runtime's provider notices),
# so everything else goes to stderr.
_REPLIES = sys.stdout
sys.stdout = sys.stderr


def _reply(value: dict) -> None:
    _REPLIES.write(json.dumps(value, ensure_ascii=True) + "\n")
    _REPLIES.flush()


def main() -> int:
    try:
        payload = sys.stdin.buffer.readline(65537)
        if len(payload) > 65536:
            raise ValueError("separation request exceeds its bound")
        request = json.loads(payload)
        app_root = Path(request["app_root"])
        location = app_root / "engine" / "dubflow" / "accelerator" / "onnx_cuda.py"
        module_spec = importlib.util.spec_from_file_location("dubflow_onnx_cuda", location)
        loader = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(loader)
        loader.activate(request["accelerator"])
        sys.path.insert(0, str(app_root))  # Isolated mode: the app package is added explicitly.
        from engine.dubflow.separation import spleeter
        from engine.dubflow.separation.production import SeparationError
    except Exception as error:  # noqa: BLE001 - every setup failure is a CPU fallback in the worker.
        _reply({"ok": False, "code": "SEPARATION_GPU_UNAVAILABLE", "condition": f"{type(error).__name__}: {error}"[:1024]})
        return 2
    try:
        metrics = spleeter.separate_background(Path(request["source"]), Path(request["background"]),
            model_dir=Path(request["model_dir"]), ffmpeg_path=Path(request["ffmpeg"]), device="cuda",
            progress=lambda fraction: _reply({"progress": float(fraction)}))
        _reply({"ok": True, "metrics": metrics})
        return 0
    except SeparationError as error:
        _reply({"ok": False, "code": error.code, "condition": error.condition[:1024]})
    except Exception as error:  # noqa: BLE001
        _reply({"ok": False, "code": "SEPARATION_GPU_FAILED", "condition": f"{type(error).__name__}: {error}"[:1024]})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
