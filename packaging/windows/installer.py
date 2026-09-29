"""Thin Windows installer facade over the portable bootstrap state machine."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from models.bootstrap.manifest import BootstrapArtifact
from packaging.runtime.bootstrap import BootstrapController, BootstrapReport, Fetcher, HardwareProfile, SignatureVerifier


class WindowsInstaller:
    """Install into a user-owned path; no elevation is requested by design."""

    def __init__(self, install_path: str | Path, *, hardware: HardwareProfile | None = None, free_bytes: int | None = None, signature_verifier: SignatureVerifier | None = None) -> None:
        self.controller = BootstrapController(install_path, hardware=hardware, free_bytes=free_bytes, signature_verifier=signature_verifier)

    def bootstrap(self, artifacts: Iterable[BootstrapArtifact], fetcher: Fetcher) -> BootstrapReport:
        return self.controller.install(artifacts, fetcher)


__all__ = ["WindowsInstaller"]
