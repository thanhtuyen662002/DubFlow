"""H.264 encoder choice: NVIDIA NVENC first when it really works, else the CPU encoders.

A GPU is an accelerator, never a correctness dependency (AGENTS.md). The shipped
LGPL FFmpeg build contains ``h264_nvenc`` (NVIDIA's headers are MIT), but the
encoder only runs with an NVIDIA driver and a GeForce/RTX card. So the app
encodes one tiny test frame once per FFmpeg executable; only when that succeeds
is NVENC put in front of Media Foundation and OpenH264. Every caller keeps its
CPU fallback, so a driver that breaks mid-render still finishes on the CPU.

``DUBFLOW_ACCELERATOR=cpu`` (or the job's ``accelerator: cpu``) skips the GPU.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Sequence

NVENC = "h264_nvenc"
MEDIA_FOUNDATION = "h264_mf"
OPENH264 = "libopenh264"
CPU_ENCODERS: tuple[str, ...] = (MEDIA_FOUNDATION, OPENH264)
GPU_ENCODERS: tuple[str, ...] = (NVENC, *CPU_ENCODERS)
ENV = "DUBFLOW_ACCELERATOR"
_PROBE_SECONDS = 30
_CACHE: dict[str, bool] = {}
_LOCK = threading.Lock()


def _cpu_only(accelerator: str | None) -> bool:
    return (accelerator or "auto") == "cpu" or os.environ.get(ENV, "").strip().lower() == "cpu"


def nvenc_available(ffmpeg: str | Path) -> bool:
    """Whether this FFmpeg can encode H.264 on an NVIDIA GPU right now (cached per executable)."""
    key = os.fspath(ffmpeg)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        try:
            # 256x256: NVENC refuses frames smaller than its minimum (~145x49).
            # "-hwaccel cuda" proves the CUDA device opens too: FFmpeg fails a
            # render outright when it cannot, rather than decoding on the CPU.
            result = subprocess.run(
                [key, "-hide_banner", "-v", "error", "-nostdin", "-hwaccel", "cuda",
                 "-f", "lavfi", "-i", "color=c=black:s=256x256:r=25:d=0.2",
                 "-c:v", NVENC, "-pix_fmt", "yuv420p", "-f", "null", "-"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=_PROBE_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            usable = result.returncode == 0
        except (OSError, subprocess.SubprocessError, ValueError):
            usable = False
        _CACHE[key] = usable
        return usable


def h264_encoders(ffmpeg: str | Path, accelerator: str | None = "auto") -> tuple[str, ...]:
    """Encoders to try in order: NVENC (when usable), Media Foundation, OpenH264."""
    if _cpu_only(accelerator):
        return CPU_ENCODERS
    return GPU_ENCODERS if nvenc_available(ffmpeg) else CPU_ENCODERS


def decode_args(encoder: str) -> list[str]:
    """Input options for a video input: decode on the GPU alongside NVENC.

    Frames come back to system memory, so every CPU filter (blur, subtitles,
    overlays) still works; a codec the GPU cannot decode falls back to the CPU
    decoder inside FFmpeg.
    """
    return ["-hwaccel", "cuda"] if encoder == NVENC else []


def rate_args(encoder: str, bitrate: int | str) -> list[str]:
    """``-c:v`` and rate control for ``encoder`` at an average ``bitrate`` (bits/s or "8M")."""
    value = _bits(bitrate)
    args = ["-c:v", encoder, "-b:v", str(bitrate)]
    if encoder == NVENC:
        # p4 is NVENC's balanced preset: several times real time on an RTX 3060
        # with quality at least that of Media Foundation at the same bitrate.
        args += ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-maxrate", str(value * 3 // 2), "-bufsize", str(value * 2),
                 "-spatial-aq", "1", "-bf", "3"]
    elif encoder == OPENH264:
        args += ["-maxrate", str(value * 3 // 2), "-bufsize", str(value * 2)]
    return args


def label(encoder: str) -> str:
    """Vietnamese name shown in progress messages."""
    return "GPU NVIDIA" if encoder == NVENC else "CPU"


def _bits(bitrate: int | str) -> int:
    if isinstance(bitrate, int):
        return bitrate
    text = str(bitrate).strip().upper()
    scale = {"K": 1_000, "M": 1_000_000}.get(text[-1:], 1)
    return int(float(text[:-1] if scale > 1 else text) * scale)


def ordered(encoders: Sequence[str], current: str) -> list[str]:
    """``current`` first, then the rest of ``encoders`` in their order."""
    return [current] + [name for name in encoders if name != current]


__all__ = ["CPU_ENCODERS", "GPU_ENCODERS", "MEDIA_FOUNDATION", "NVENC", "OPENH264", "decode_args", "h264_encoders",
           "label", "nvenc_available", "ordered", "rate_args"]
