"""Hardware-aware profile resolution with a truthful CPU fallback."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


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


class HardwareResolver:
    """Resolve CPU/GPU/render choices from a launcher-provided snapshot.

    No system executable or CUDA library is required. A trusted launcher may
    provide DUBFLOW_GPU_NAME, DUBFLOW_GPU_VRAM_MB and
    DUBFLOW_GPU_ENCODER; malformed values are ignored and produce CPU.
    """

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = dict(environment or os.environ)

    def detect(self) -> HardwareSnapshot:
        threads = max(1, int(os.cpu_count() or 1))
        name = self.environment.get("DUBFLOW_GPU_NAME", "").strip() or None
        raw_vram = self.environment.get("DUBFLOW_GPU_VRAM_MB", "").strip()
        encoder = self.environment.get("DUBFLOW_GPU_ENCODER", "").strip() or None
        warnings: list[str] = []
        try:
            vram = int(raw_vram) if raw_vram else 0
        except ValueError:
            vram = 0
            warnings.append("GPU VRAM report was malformed; CPU fallback selected")
        gpu = bool(name and vram > 0)
        if not gpu and (name or raw_vram or encoder):
            warnings.append("GPU report was incomplete; CPU fallback selected")
        if gpu and encoder not in {None, "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_amf", "hevc_amf"}:
            warnings.append("GPU encoder is unknown; software encoder selected")
            encoder = None
        return HardwareSnapshot(threads, gpu, name if gpu else None, vram if gpu else 0, encoder if gpu else None, "launcher-environment" if (name or raw_vram or encoder) else "cpu-safe-default", tuple(warnings))

    def resolve(self, requested: str = "auto", *, minimum_vram_mb: int = 4096, encoder: str = "software") -> ExecutionProfile:
        if requested not in {"auto", "cpu", "gpu"}:
            raise ValueError("requested profile must be auto, cpu or gpu")
        if type(minimum_vram_mb) is not int or minimum_vram_mb < 0:
            raise ValueError("minimum_vram_mb must be non-negative")
        snapshot = self.detect()
        wants_gpu = requested == "gpu" or requested == "auto"
        usable_gpu = snapshot.gpu_available and snapshot.vram_mb >= minimum_vram_mb and (encoder == "software" or snapshot.encoder == encoder)
        if wants_gpu and usable_gpu:
            selected_encoder = snapshot.encoder or encoder
            return ExecutionProfile(requested, "gpu", selected_encoder, True, snapshot.vram_mb, "reported GPU passed VRAM/encoder policy", False)
        reason = "GPU unavailable or below the requested resource policy"
        if requested == "cpu":
            reason = "CPU explicitly requested"
        return ExecutionProfile(requested, "cpu", "software", False, 0, reason, requested == "gpu" or bool(snapshot.warnings))


__all__ = ["ExecutionProfile", "HardwareResolver", "HardwareSnapshot"]
