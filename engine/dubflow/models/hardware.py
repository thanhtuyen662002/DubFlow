"""Hardware-aware profile resolution with a truthful CPU fallback."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import io
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping


@dataclass(frozen=True)
class HardwareSnapshot:
    cpu_threads: int
    gpu_available: bool
    gpu_name: str | None
    vram_mb: int
    encoder: str | None
    source: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.cpu_threads) is not int or self.cpu_threads < 1:
            raise ValueError("cpu_threads must be positive")
        if type(self.gpu_available) is not bool or type(self.vram_mb) is not int or self.vram_mb < 0:
            raise ValueError("hardware GPU fields are invalid")
        if self.gpu_available and (not self.gpu_name or self.vram_mb <= 0):
            raise ValueError("a reported GPU requires name and positive VRAM")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("hardware source is required")

    def to_dict(self) -> dict[str, object]:
        return {"cpu_threads": self.cpu_threads, "gpu_available": self.gpu_available, "gpu_name": self.gpu_name, "vram_mb": self.vram_mb, "encoder": self.encoder, "source": self.source, "warnings": list(self.warnings)}


@dataclass(frozen=True)
class ExecutionProfile:
    requested: str
    selected: str
    encoder: str
    gpu: bool
    vram_mb: int
    reason: str
    fallback: bool

    def to_dict(self) -> dict[str, object]:
        return {"requested": self.requested, "selected": self.selected, "encoder": self.encoder, "gpu": self.gpu, "vram_mb": self.vram_mb, "reason": self.reason, "fallback": self.fallback}


class NvidiaHardwareProbe:
    """Check device 0 using an explicit driver tool and app-owned FFmpeg.

    An encoder smoke test establishes encoder availability only. It is not
    evidence that a model can run on CUDA or that a long job fits in VRAM.
    """

    def __init__(self, ffmpeg_path: Path, nvidia_smi_path: Path, *, timeout_seconds: float = 5.0, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.ffmpeg_path = Path(ffmpeg_path)
        self.nvidia_smi_path = Path(nvidia_smi_path)
        if not self.ffmpeg_path.is_absolute() or not self.nvidia_smi_path.is_absolute():
            raise ValueError("hardware probe executable paths must be absolute")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("hardware probe timeout must be finite and within 30 seconds")
        self.timeout_seconds = timeout_seconds
        self.runner = subprocess.run if runner is None else runner

    @classmethod
    def for_system(cls, ffmpeg_path: Path) -> NvidiaHardwareProbe:
        """Use an OS driver location, never a job-supplied executable or PATH."""
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_directory = kernel.GetSystemDirectoryW
            get_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
            get_directory.restype = ctypes.c_uint
            directory = ctypes.create_unicode_buffer(32768)
            length = get_directory(directory, len(directory))
            if length == 0 or length >= len(directory):
                raise OSError("Windows system directory is unavailable")
            driver = Path(directory.value) / "nvidia-smi.exe"
        else:
            driver = Path("/usr/bin/nvidia-smi")
        return cls(ffmpeg_path, driver)

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(command, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", shell=False,
                           timeout=self.timeout_seconds, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)

    def detect(self) -> HardwareSnapshot:
        threads = max(1, int(os.cpu_count() or 1))
        def fallback(reason: str) -> HardwareSnapshot:
            return HardwareSnapshot(threads, False, None, 0, None, "hardware-probe", (reason,))
        try:
            inventory = self._run([str(self.nvidia_smi_path), "--id=0", "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"])
            if inventory.returncode != 0:
                return fallback("GPU inventory probe failed; CPU fallback selected")
            if not isinstance(inventory.stdout, str) or len(inventory.stdout) > 65536:
                return fallback("GPU inventory response is invalid; CPU fallback selected")
            rows = list(csv.reader(io.StringIO(inventory.stdout.strip())))
            if len(rows) != 1 or len(rows[0]) != 2:
                return fallback("GPU inventory response is invalid; CPU fallback selected")
            name = rows[0][0].strip()
            free_vram = int(rows[0][1].strip())
            if not name or len(name) > 256 or not name.isprintable() or free_vram <= 0:
                return fallback("GPU has no usable free VRAM; CPU fallback selected")
            encoder = self._run([str(self.ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
                                 "-f", "lavfi", "-i", "color=size=1280x720:rate=1:duration=1",
                                 "-frames:v", "1", "-an", "-c:v", "h264_nvenc", "-gpu", "0", "-f", "null", "-"])
            if encoder.returncode != 0:
                return fallback("NVENC initialization/encode probe failed; CPU fallback selected")
            return HardwareSnapshot(threads, True, name, free_vram, "h264_nvenc", "nvidia-smi+ffmpeg-smoke")
        except subprocess.TimeoutExpired:
            return fallback("Hardware probe timed out; CPU fallback selected")
        except (OSError, ValueError, csv.Error):
            return fallback("Hardware probe unavailable or malformed; CPU fallback selected")


class HardwareResolver:
    """Resolve render profiles from an actual probe; hints never qualify GPU.

    Explicit CPU/software rendering does not start a GPU process. Accelerator
    model execution needs its own model/runtime health check.
    """

    def __init__(self, environment: Mapping[str, str] | None = None, *, probe: NvidiaHardwareProbe | None = None) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        self.probe = probe

    def detect(self) -> HardwareSnapshot:
        if self.probe is not None:
            return self.probe.detect()
        hints = any(self.environment.get(key, "").strip() for key in ("DUBFLOW_GPU_NAME", "DUBFLOW_GPU_VRAM_MB", "DUBFLOW_GPU_ENCODER"))
        warnings = ("GPU environment hints are unverified; CPU fallback selected",) if hints else ()
        return HardwareSnapshot(max(1, int(os.cpu_count() or 1)), False, None, 0, None, "cpu-safe-default", warnings)

    def resolve(self, requested: str = "auto", *, minimum_vram_mb: int = 4096, encoder: str = "software") -> ExecutionProfile:
        if requested not in {"auto", "cpu", "gpu"}:
            raise ValueError("requested profile must be auto, cpu or gpu")
        if type(minimum_vram_mb) is not int or minimum_vram_mb < 0:
            raise ValueError("minimum_vram_mb must be non-negative")
        if encoder not in {"software", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_amf", "hevc_amf"}:
            raise ValueError("unknown render encoder")
        if requested == "cpu":
            return ExecutionProfile(requested, "cpu", "software", False, 0, "CPU explicitly requested", False)
        if encoder == "software":
            return ExecutionProfile(requested, "cpu", "software", False, 0, "Software rendering requested; model GPU health is unverified", requested == "gpu")
        snapshot = self.detect()
        if snapshot.gpu_available and snapshot.vram_mb >= minimum_vram_mb and snapshot.encoder == encoder:
            return ExecutionProfile(requested, "gpu", encoder, True, snapshot.vram_mb, "Encoder smoke test and free VRAM policy passed", False)
        reason = "GPU encoder unavailable or free VRAM below the requested policy"
        return ExecutionProfile(requested, "cpu", "software", False, 0, reason, True)


__all__ = ["ExecutionProfile", "HardwareResolver", "HardwareSnapshot", "NvidiaHardwareProbe"]
