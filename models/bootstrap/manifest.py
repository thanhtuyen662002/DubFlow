"""Small manifest boundary consumed by the runtime bootstrap controller."""

from __future__ import annotations

from dataclasses import dataclass
import re


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BootstrapArtifact:
    artifact_id: str
    version: str
    kind: str
    size_bytes: int
    sha256: str
    signature: str
    required: bool = True
    fallback_id: str | None = None
    architectures: tuple[str, ...] = ("x86_64", "amd64", "arm64")

    def __post_init__(self) -> None:
        for name, value in (("artifact_id", self.artifact_id), ("version", self.version), ("signature", self.signature)):
            if not isinstance(value, str) or not value or len(value) > 512 or (name != "signature" and not _ID.fullmatch(value)):
                raise ValueError(f"{name} is invalid")
        if self.kind not in {"runtime", "model", "ffmpeg"}:
            raise ValueError("kind must be runtime, model or ffmpeg")
        if type(self.size_bytes) is not int or self.size_bytes < 0 or self.size_bytes > (1 << 64) - 1:
            raise ValueError("size_bytes is outside u64")
        if not isinstance(self.sha256, str) or not _SHA.fullmatch(self.sha256):
            raise ValueError("sha256 must be lowercase hexadecimal")
        if type(self.required) is not bool:
            raise ValueError("required must be boolean")
        if self.fallback_id is not None and (not isinstance(self.fallback_id, str) or not _ID.fullmatch(self.fallback_id)):
            raise ValueError("fallback_id is invalid")
        if not self.architectures or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in self.architectures):
            raise ValueError("architectures must be non-empty safe identifiers")


__all__ = ["BootstrapArtifact"]
