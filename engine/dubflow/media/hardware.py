"""Bounded hardware rendering with the shipped software encoder fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from engine.dubflow.models.hardware import ExecutionProfile, HardwareResolver
from .adapter import FfmpegMediaAdapter, MediaAdapterError


class HardwareRenderAdapter:
    """Resolve lazily, and retire a failed GPU encoder for this job."""

    def __init__(self, media: FfmpegMediaAdapter, *, resolver: HardwareResolver, requested: str = "cpu",
                 retired_reason: str | None = None, on_gpu_retired: Callable[[str], None] | None = None) -> None:
        if not isinstance(requested, str) or requested not in {"cpu", "auto", "gpu"}:
            raise ValueError("render profile must be cpu, auto or gpu")
        self.media = media
        self.resolver = resolver
        self.requested = requested
        self._profile: ExecutionProfile | None = (ExecutionProfile(requested, "cpu", "software", False, 0, retired_reason, True)
                                                   if retired_reason is not None else None)
        self._on_gpu_retired = on_gpu_retired
        self._attempts = 0
        self._succeeded = False
        self.warnings: list[str] = []

    def evidence(self) -> dict[str, Any]:
        if self._profile is None:
            return {"requested": self.requested, "selected": None, "encoder": None,
                    "status": "not_rendered", "render_attempts": 0}
        status = "not_rendered" if self._attempts == 0 else ("command_succeeded" if self._succeeded else "command_failed")
        return {**self._profile.to_dict(), "status": status,
                "render_attempts": self._attempts}

    def render(self, source_path: str | Path, output_path: str | Path, *,
               subtitle_path: str | Path | None = None, audio_path: str | Path | None = None,
               preserve_original_audio: bool = True, burn_in_subtitles: bool = False,
               overwrite: bool = False) -> Path:
        self._succeeded = False
        if self._profile is None:
            self._profile = self.resolver.resolve(self.requested, encoder="h264_nvenc")
            if self.requested == "gpu" and self._profile.fallback:
                self.warnings.append("GPU_PROFILE_FALLBACK: " + self._profile.reason)
        options = {"subtitle_path": subtitle_path, "audio_path": audio_path,
                   "preserve_original_audio": preserve_original_audio,
                   "burn_in_subtitles": burn_in_subtitles, "overwrite": overwrite}
        if self._profile.gpu:
            self._attempts += 1
            try:
                result = self.media.render(source_path, output_path, video_encoder="h264_nvenc", **options)
            except MediaAdapterError as error:
                encoder_failure = error.code == "MEDIA_OUTPUT_INVALID" or (
                    error.code in {"MEDIA_COMMAND_FAILED", "MEDIA_COMMAND_TIMEOUT"} and error.retryable)
                if not encoder_failure:
                    raise
                self.warnings.append("GPU_RENDER_FALLBACK: " + error.code + "; software encoder selected")
                self._profile = ExecutionProfile(self.requested, "cpu", "software", False, 0,
                                                 "GPU render failed; software encoder selected", True)
                if self._on_gpu_retired is not None:
                    self._on_gpu_retired(error.code)
            else:
                self._succeeded = True
                return result
        self._attempts += 1
        result = self.media.render(source_path, output_path, **options)
        self._succeeded = True
        return result


__all__ = ["HardwareRenderAdapter"]
