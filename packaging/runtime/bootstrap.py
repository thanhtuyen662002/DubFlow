"""Resumable, idempotent runtime/model bootstrap controller.

The controller owns files below an install directory and never requires
administrator privileges. Network, signature and GPU detection are injected
boundaries so clean-machine/release smoke can exercise the same state machine
without downloading model weights in required CI.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
from typing import Callable, Iterable, Mapping, Sequence

from models.bootstrap.manifest import BootstrapArtifact


STATE_VERSION = 1
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BootstrapError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message[:4096]
        super().__init__(f"{code}: {self.message}")


@dataclass(frozen=True)
class HardwareProfile:
    architecture: str
    cpu_count: int
    ram_bytes: int
    accelerator: str

    @classmethod
    def detect(cls, *, accelerator: str | None = None) -> "HardwareProfile":
        architecture = platform.machine().lower().replace("-", "_") or "unknown"
        if architecture in {"amd64", "x86_64"}:
            architecture = "x86_64"
        elif architecture in {"aarch64", "arm64"}:
            architecture = "arm64"
        cpu_count = max(1, int(os.cpu_count() or 1))
        ram_bytes = 0
        try:
            ram_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (AttributeError, OSError, ValueError):
            pass
        selected = accelerator or os.environ.get("DUBFLOW_ACCELERATOR", "cpu")
        if not isinstance(selected, str) or not _SAFE.fullmatch(selected):
            selected = "cpu"
        return cls(architecture, cpu_count, max(0, ram_bytes), selected.lower())


@dataclass(frozen=True)
class BootstrapReport:
    ready: bool
    installed: tuple[str, ...]
    fallbacks: tuple[str, ...]
    failures: tuple[dict[str, str], ...]
    hardware: HardwareProfile
    state_path: str

    def to_dict(self) -> dict[str, object]:
        return {"ready": self.ready, "installed": list(self.installed), "fallbacks": list(self.fallbacks), "failures": list(self.failures), "hardware": {"architecture": self.hardware.architecture, "cpu_count": self.hardware.cpu_count, "ram_bytes": self.hardware.ram_bytes, "accelerator": self.hardware.accelerator}, "state_path": self.state_path}


Fetcher = Callable[[BootstrapArtifact, int], bytes]
SignatureVerifier = Callable[[BootstrapArtifact, str], bool]


class BootstrapController:
    def __init__(self, install_root: str | Path, *, hardware: HardwareProfile | None = None, free_bytes: int | None = None, signature_verifier: SignatureVerifier | None = None, disk_reserve_bytes: int = 0) -> None:
        self.install_root = Path(install_root).expanduser().resolve()
        self.package_root = self.install_root / "packages"
        self.state_path = self.install_root / "bootstrap-state.json"
        self.hardware = hardware or HardwareProfile.detect()
        self.free_bytes = free_bytes
        self.signature_verifier = signature_verifier
        if type(disk_reserve_bytes) is not int or disk_reserve_bytes < 0:
            raise ValueError("disk_reserve_bytes must be non-negative")
        self.disk_reserve_bytes = disk_reserve_bytes

    def preflight(self, artifacts: Sequence[BootstrapArtifact]) -> None:
        if self.free_bytes is None:
            self.install_root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.install_root).free
        else:
            if type(self.free_bytes) is not int or self.free_bytes < 0:
                raise BootstrapError("INVALID_DISK_STAT", "free_bytes must be non-negative")
            free = self.free_bytes
        required = 0
        for artifact in artifacts:
            target, partial = self._paths(artifact)
            if target.is_file() and self._verify_file(target, artifact):
                continue
            partial_size = partial.stat().st_size if partial.is_file() else 0
            required += max(0, artifact.size_bytes - partial_size)
        if free < required + self.disk_reserve_bytes:
            raise BootstrapError("LOW_DISK", f"bootstrap requires {required + self.disk_reserve_bytes} bytes but only {free} are available")

    def install(self, artifacts: Iterable[BootstrapArtifact], fetcher: Fetcher) -> BootstrapReport:
        artifacts = tuple(artifacts)
        if not artifacts:
            raise BootstrapError("EMPTY_MANIFEST", "at least one bootstrap artifact is required")
        by_id: dict[str, BootstrapArtifact] = {}
        for artifact in artifacts:
            if artifact.artifact_id in by_id:
                raise BootstrapError("DUPLICATE_ARTIFACT", f"duplicate artifact {artifact.artifact_id}")
            by_id[artifact.artifact_id] = artifact
        self.preflight(artifacts)
        self.package_root.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        installed: list[str] = []
        fallbacks: list[str] = []
        failures: list[dict[str, str]] = []
        ordered = tuple(sorted(artifacts, key=lambda item: (not item.required, item.artifact_id)))
        for artifact in ordered:
            target, _ = self._paths(artifact)
            if target.is_file() and self._verify_file(target, artifact):
                installed.append(artifact.artifact_id)
                state[artifact.artifact_id] = {"status": "installed", "path": str(target), "sha256": artifact.sha256, "version": artifact.version}
                self._save_state(state)
                continue
            if self.hardware.architecture not in artifact.architectures:
                self._record_failure(artifact, "UNSUPPORTED_ARCH", "artifact is not compatible with this CPU architecture", failures, state)
                if self._fallback(artifact, by_id, installed, fallbacks, failures, state):
                    failures[:] = [failure for failure in failures if failure.get("artifact_id") != artifact.artifact_id]
                    continue
                continue
            try:
                self._download_and_publish(artifact, fetcher, target, state)
                installed.append(artifact.artifact_id)
            except BootstrapError as error:
                self._record_failure(artifact, error.code, error.message, failures, state)
                if self._fallback(artifact, by_id, installed, fallbacks, failures, state):
                    failures[:] = [failure for failure in failures if failure.get("artifact_id") != artifact.artifact_id]
                    continue
        required_missing = [artifact.artifact_id for artifact in artifacts if artifact.required and artifact.artifact_id not in installed]
        return BootstrapReport(not required_missing and not failures, tuple(installed), tuple(fallbacks), tuple(failures), self.hardware, str(self.state_path))

    def _download_and_publish(self, artifact: BootstrapArtifact, fetcher: Fetcher, target: Path, state: dict[str, object]) -> None:
        _, partial = self._paths(artifact)
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > artifact.size_bytes:
            partial.unlink(missing_ok=True)
            offset = 0
        with partial.open("ab") as stream:
            while offset < artifact.size_bytes:
                try:
                    chunk = fetcher(artifact, offset)
                except Exception as exc:
                    raise BootstrapError("DOWNLOAD_FAILED", "download failed; rerun to resume the partial file") from exc
                if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                    raise BootstrapError("DOWNLOAD_EMPTY", "downloader returned no bytes; rerun after fixing the source")
                remaining = artifact.size_bytes - offset
                if len(chunk) > remaining:
                    raise BootstrapError("DOWNLOAD_OVERRUN", "download exceeded the manifest size")
                stream.write(bytes(chunk))
                stream.flush()
                os.fsync(stream.fileno())
                offset += len(chunk)
                state[artifact.artifact_id] = {"status": "downloading", "offset": offset, "path": str(partial), "version": artifact.version}
                self._save_state(state)
        if not self._verify_file(partial, artifact):
            raise BootstrapError("CHECKSUM_MISMATCH", "downloaded bytes do not match the manifest SHA-256")
        digest = artifact.sha256
        if self.signature_verifier is None or not self.signature_verifier(artifact, digest):
            raise BootstrapError("SIGNATURE_INVALID", "package signature verification failed")
        os.replace(partial, target)
        state[artifact.artifact_id] = {"status": "installed", "path": str(target), "sha256": digest, "version": artifact.version}
        self._save_state(state)

    def _fallback(self, artifact: BootstrapArtifact, by_id: Mapping[str, BootstrapArtifact], installed: list[str], fallbacks: list[str], failures: list[dict[str, str]], state: dict[str, object]) -> bool:
        if artifact.fallback_id is None:
            return False
        fallback = by_id.get(artifact.fallback_id)
        if fallback is None:
            failures.append({"artifact_id": artifact.artifact_id, "code": "FALLBACK_MISSING", "message": f"fallback {artifact.fallback_id} is absent from the manifest"})
            return False
        target, _ = self._paths(fallback)
        if target.is_file() and self._verify_file(target, fallback):
            if fallback.artifact_id not in installed:
                installed.append(fallback.artifact_id)
            fallbacks.append(artifact.artifact_id + "->" + fallback.artifact_id)
            state[artifact.artifact_id] = {"status": "fallback", "fallback_id": fallback.artifact_id}
            self._save_state(state)
            return True
        # A required fallback is normally ordered before an optional artifact;
        # if it is not present, report one actionable error rather than copying
        # an unverified file.
        failures.append({"artifact_id": artifact.artifact_id, "code": "FALLBACK_UNAVAILABLE", "message": f"verified fallback {fallback.artifact_id} is not installed"})
        return False

    def _record_failure(self, artifact: BootstrapArtifact, code: str, message: str, failures: list[dict[str, str]], state: dict[str, object]) -> None:
        failures.append({"artifact_id": artifact.artifact_id, "code": code, "message": message})
        state[artifact.artifact_id] = {"status": "failed", "code": code, "message": message}
        self._save_state(state)

    def _paths(self, artifact: BootstrapArtifact) -> tuple[Path, Path]:
        if not _SAFE.fullmatch(artifact.artifact_id) or not _SAFE.fullmatch(artifact.version):
            raise BootstrapError("UNSAFE_ARTIFACT_PATH", "manifest artifact path component is unsafe")
        name = artifact.artifact_id + "-" + artifact.version + ".pkg"
        target = (self.package_root / name).resolve()
        partial = (self.package_root / (name + ".partial")).resolve()
        if self.package_root.resolve() not in target.parents or self.package_root.resolve() not in partial.parents:
            raise BootstrapError("UNSAFE_ARTIFACT_PATH", "artifact path escapes the install root")
        return target, partial

    @staticmethod
    def _verify_file(path: Path, artifact: BootstrapArtifact) -> bool:
        try:
            if path.stat().st_size != artifact.size_bytes:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == artifact.sha256
        except OSError:
            return False

    def _load_state(self) -> dict[str, object]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"schema_version": STATE_VERSION, "artifacts": {}}
        if not isinstance(value, dict) or value.get("schema_version") != STATE_VERSION or not isinstance(value.get("artifacts"), dict):
            return {"schema_version": STATE_VERSION, "artifacts": {}}
        return value["artifacts"]

    def _save_state(self, artifacts: dict[str, object]) -> None:
        self.install_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema_version": STATE_VERSION, "artifacts": artifacts}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.install_root, prefix=".bootstrap-state-", suffix=".tmp", delete=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, self.state_path)


__all__ = ["BootstrapController", "BootstrapError", "BootstrapReport", "HardwareProfile"]
