"""Optional NVIDIA CUDA acceleration for the CTranslate2 ASR path (ADR-0038).

A GPU is an accelerator, never a correctness dependency. On Windows with an
NVIDIA driver this module probes the device through the CUDA driver API,
provisions the pinned cuBLAS 12 / cuDNN 9 libraries from NVIDIA's own wheels
into the app-owned model root, re-verifies every library immediately before it
is loaded, and otherwise reports why the CPU is used. Nothing here raises past
``select_asr_device``: every failure becomes a CPU selection with a reason.
"""
from __future__ import annotations

import contextlib
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable, Mapping
import zipfile

from engine.dubflow.models.runtime import ModelArtifact, ModelBootstrapError, _reject_link_components, ensure_model_profile, load_profile

PROFILE_PATH = "models/manifests/accelerator-nvidia-cuda12-v1.json"
# Preferred first: int8 weights with float16 compute is Faster-Whisper's GPU default.
COMPUTE_PREFERENCE = ("int8_float16", "int8", "float16", "float32")
MAX_LIBRARY_BYTES = 2 * 1024 * 1024 * 1024
_CC_MAJOR, _CC_MINOR = 75, 76  # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR/MINOR
_DLL = re.compile(r"[A-Za-z0-9_]{1,64}\.dll")
_NOTICE = re.compile(r"[A-Za-z0-9_-]{1,64}\.txt")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOADED: dict[str, Any] = {}


class AcceleratorUnavailable(RuntimeError):
    """The CPU is used. ``silent`` marks the ordinary case of no NVIDIA driver."""

    def __init__(self, reason: str, *, silent: bool = False) -> None:
        self.reason = " ".join(str(reason).replace("\x00", " ").split())[:300]
        self.silent = silent
        super().__init__(self.reason)


@dataclass(frozen=True)
class NvidiaDevice:
    name: str
    total_memory: int
    compute_capability: tuple[int, int]
    driver_version: int

    def label(self) -> str:
        return f"{self.name} ({self.total_memory / 1024 ** 3:.0f} GB)"


@dataclass(frozen=True)
class PackFile:
    name: str
    artifact: str
    member: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Pack:
    profile_path: Path
    pack_dir: str
    driver_version: int
    min_compute_capability: tuple[int, int]
    min_total_memory: int
    artifacts: tuple[ModelArtifact, ...]
    libraries: tuple[PackFile, ...]
    notices: tuple[PackFile, ...]


@dataclass(frozen=True)
class AsrDevice:
    """How Faster-Whisper is constructed; ``reason`` explains a CPU choice."""

    device: str
    compute_type: str
    gpu: NvidiaDevice | None = None
    reason: str | None = None

    @property
    def label(self) -> str:
        if self.device != "cuda" or self.gpu is None:
            return "model CPU"
        name = self.gpu.name
        return "GPU " + (name if name.upper().startswith("NVIDIA") else "NVIDIA " + name)


CPU = AsrDevice("cpu", "int8")


def _system_library(name: str) -> Path:
    buffer = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, 260):  # type: ignore[attr-defined]
        raise AcceleratorUnavailable("unable to locate the Windows system directory")
    return Path(buffer.value) / name


def _load_driver() -> Any:
    if sys.platform != "win32":
        raise AcceleratorUnavailable("CUDA acceleration is Windows-only", silent=True)
    # The driver lives in System32; never resolve it through the DLL search path.
    path = _system_library("nvcuda.dll")
    if not path.is_file():
        raise AcceleratorUnavailable("no NVIDIA driver is installed", silent=True)
    try:
        return ctypes.WinDLL(str(path))  # type: ignore[attr-defined]
    except OSError as error:
        raise AcceleratorUnavailable(f"the NVIDIA driver could not be loaded: {error}") from error


def probe(load_driver: Callable[[], Any] = _load_driver) -> NvidiaDevice:
    """Read device 0 through the CUDA driver API, without any CUDA toolkit."""
    cuda = load_driver()

    def call(name: str, *args: Any) -> None:
        result = getattr(cuda, name)(*args)
        if result != 0:
            raise AcceleratorUnavailable(f"{name} failed with CUDA error {result}")

    call("cuInit", 0)
    version, count, device = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    call("cuDriverGetVersion", ctypes.byref(version))
    call("cuDeviceGetCount", ctypes.byref(count))
    if count.value < 1:
        raise AcceleratorUnavailable("the NVIDIA driver reports no CUDA device")
    call("cuDeviceGet", ctypes.byref(device), 0)
    name = ctypes.create_string_buffer(256)
    memory = ctypes.c_size_t()
    major, minor = ctypes.c_int(), ctypes.c_int()
    call("cuDeviceGetName", name, 256, device)
    call("cuDeviceTotalMem_v2", ctypes.byref(memory), device)
    call("cuDeviceGetAttribute", ctypes.byref(major), _CC_MAJOR, device)
    call("cuDeviceGetAttribute", ctypes.byref(minor), _CC_MINOR, device)
    label = " ".join(name.value.decode("utf-8", "replace").split())[:96] or "unknown NVIDIA GPU"
    return NvidiaDevice(label, int(memory.value), (int(major.value), int(minor.value)), int(version.value))


def _file(value: Any, pattern: re.Pattern[str], artifacts: Mapping[str, ModelArtifact]) -> PackFile:
    if not isinstance(value, Mapping) or set(value) != {"name", "artifact", "member", "sha256", "size_bytes"}:
        raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", "pack file entries must name an artifact member, size and hash")
    name, artifact, member, digest, size = (value[key] for key in ("name", "artifact", "member", "sha256", "size_bytes"))
    if (not isinstance(name, str) or not pattern.fullmatch(name) or artifact not in artifacts
            or not isinstance(member, str) or member.startswith("/") or "\\" in member or ".." in member.split("/")
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or type(size) is not int or not 0 < size <= MAX_LIBRARY_BYTES):
        raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", f"invalid pack file entry: {name!r}")
    return PackFile(name, artifact, member, digest, size)


def load_pack(app_root: Path) -> Pack:
    path = Path(app_root) / PROFILE_PATH
    _profile_id, artifacts = load_profile(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    pack_dir, requires = document.get("pack_dir"), document.get("requires")
    if (not isinstance(pack_dir, str) or not re.fullmatch(r"accelerators/[a-z0-9-]{1,64}", pack_dir)
            or any(not artifact.relative_path.startswith(pack_dir + "/wheels/") for artifact in artifacts)):
        raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", "pack files must stay inside their accelerator folder")
    if (not isinstance(requires, Mapping) or type(requires.get("cuda_driver_version")) is not int
            or not isinstance(requires.get("min_compute_capability"), list) or len(requires["min_compute_capability"]) != 2
            or any(type(item) is not int for item in requires["min_compute_capability"])
            or type(requires.get("min_total_memory_bytes")) is not int):
        raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", "pack requirements are missing")
    libraries = tuple(_file(item, _DLL, by_id) for item in document.get("libraries") or ())
    notices = tuple(_file(item, _NOTICE, by_id) for item in document.get("notices") or ())
    names = [item.name.casefold() for item in (*libraries, *notices)]
    if not libraries or len(names) != len(set(names)):
        raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", "pack libraries must be listed once each")
    return Pack(path, pack_dir, requires["cuda_driver_version"], tuple(requires["min_compute_capability"]),
                requires["min_total_memory_bytes"], artifacts, libraries, notices)


class PackMismatch(AcceleratorUnavailable):
    """A library's bytes differ from its pin; ``ensure_pack(verify=True)`` repairs it."""


def _open_locked(path: Path) -> Any:
    """Open for reading so nobody can write, rename or delete the file while held.

    On Windows the handle shares read access only and opens a reparse point
    itself rather than its target, so a link can never be hashed as the DLL.
    """
    if sys.platform != "win32":
        return open(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN.
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x00200000 | 0x08000000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), f"unable to open {path.name} exclusively for verification")
    return open(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")


def _hash_matches(stream: Any, item: PackFile) -> bool:
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        if size > item.size_bytes:
            return False
        digest.update(block)
    return size == item.size_bytes and digest.hexdigest() == item.sha256


def _matches(path: Path, item: PackFile, *, verify: bool = True) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item.size_bytes:
            return False
        if not verify:
            return True
        with _open_locked(path) as stream:
            return _hash_matches(stream, item)
    except OSError:
        return False


def _extract(wheel: Path, item: PackFile, target: Path) -> None:
    # A unique, create-only temporary file: an existing file or link at a
    # predictable name can never redirect the write outside this folder.
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent)
    partial = Path(temporary)
    try:
        with open(descriptor, "wb") as stream, zipfile.ZipFile(wheel) as archive:
            try:
                info = archive.getinfo(item.member)
            except KeyError as error:
                raise ModelBootstrapError("ACCELERATOR_PACK_INVALID", f"{item.member} is missing from its pinned wheel") from error
            if info.file_size != item.size_bytes:
                raise ModelBootstrapError("MODEL_SIZE_INVALID", f"{item.name} differs from its pinned size")
            digest, size = hashlib.sha256(), 0
            with archive.open(info) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(block)
                    if size > item.size_bytes:
                        raise ModelBootstrapError("MODEL_SIZE_INVALID", f"{item.name} exceeds its pinned size")
                    digest.update(block)
                    stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        if size != item.size_bytes or digest.hexdigest() != item.sha256:
            raise ModelBootstrapError("MODEL_HASH_MISMATCH", f"{item.name} failed verification")
        # Renaming replaces a link at the target itself, never what it points to.
        os.replace(partial, target)
    except zipfile.BadZipFile as error:
        raise ModelBootstrapError("MODEL_HASH_MISMATCH", f"{wheel.name} is not a valid archive") from error
    finally:
        partial.unlink(missing_ok=True)


def ensure_pack(pack: Pack, model_root: Path, *, progress: Callable[[str, int, int], None] | None = None,
                verify: bool = False) -> Path:
    """Return the library folder, downloading and extracting it once.

    A present pack is accepted by size; ``activate`` hashes each library at the
    load boundary. ``verify=True`` hashes everything and repairs mismatches.
    """
    _reject_link_components(Path(model_root), "model root")
    root = Path(os.path.abspath(model_root)) / pack.pack_dir
    bin_dir = root / "bin"
    files = [(bin_dir, item) for item in pack.libraries] + [(root, item) for item in pack.notices]
    if all(_matches(folder / item.name, item, verify=verify) for folder, item in files):
        return bin_dir
    ensure_model_profile(pack.profile_path, model_root, progress=progress)
    bin_dir.mkdir(parents=True, exist_ok=True)
    _reject_link_components(bin_dir, "accelerator folder")
    wheels = {artifact.artifact_id: Path(os.path.abspath(model_root)) / artifact.relative_path for artifact in pack.artifacts}
    for folder, item in files:
        if not _matches(folder / item.name, item):
            _extract(wheels[item.artifact], item, folder / item.name)
    # Every needed byte now lives in the verified libraries; the archives are
    # 1.2 GB of duplicates. A later tamper re-downloads them.
    for wheel in wheels.values():
        wheel.unlink(missing_ok=True)
    return bin_dir


def activate(pack: Pack, bin_dir: Path, *, load: Callable[[str], Any] | None = None) -> None:
    """Hash each library at the load boundary, then preload it by full path.

    Every library stays open with read-only sharing from before its hash until
    after every load, so the verified bytes are the bytes Windows maps; nothing
    can write, rename or replace them in between. Loading by absolute path in
    manifest order makes CTranslate2's by-name loads resolve to these modules.
    """
    with contextlib.ExitStack() as held:
        for item in pack.libraries:
            path = bin_dir / item.name
            try:
                if path.is_symlink():
                    raise OSError("library is a link")
                stream = held.enter_context(_open_locked(path))
                verified = _hash_matches(stream, item)
            except OSError as error:
                raise PackMismatch(f"{item.name} could not be verified: {error}") from error
            if not verified:
                raise PackMismatch(f"{item.name} no longer matches its pinned hash")
        if load is None:
            if sys.platform != "win32":
                raise AcceleratorUnavailable("CUDA acceleration is Windows-only", silent=True)
            _LOADED.setdefault("directory", os.add_dll_directory(str(bin_dir)))  # type: ignore[attr-defined]
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            load = ctypes.WinDLL  # type: ignore[attr-defined]
        for item in pack.libraries:
            if item.name not in _LOADED:
                try:
                    _LOADED[item.name] = load(str(bin_dir / item.name))
                except OSError as error:
                    raise AcceleratorUnavailable(f"{item.name} could not be loaded: {error}") from error


def _cuda_compute_types() -> set[str]:
    import ctranslate2  # type: ignore[import-not-found]

    if ctranslate2.get_cuda_device_count() < 1:
        raise AcceleratorUnavailable("CTranslate2 found no usable CUDA device")
    return set(ctranslate2.get_supported_compute_types("cuda"))


def select_asr_device(app_root: Path, model_root: Path, preference: str = "auto", *,
                      progress: Callable[[str, int, int], None] | None = None,
                      probe: Callable[[], NvidiaDevice] = probe,
                      ensure: Callable[..., Path] = ensure_pack,
                      activate: Callable[[Pack, Path], None] = activate,
                      compute_types: Callable[[], set[str]] = _cuda_compute_types) -> AsrDevice:
    """Choose CUDA when the device, pinned pack and runtime all agree; else the CPU."""
    if preference == "cpu":
        return CPU
    try:
        gpu = probe()
    except AcceleratorUnavailable as error:
        return AsrDevice("cpu", "int8", reason=None if error.silent else error.reason)
    except Exception as error:  # noqa: BLE001 - a broken driver must not stop the job
        return AsrDevice("cpu", "int8", reason=f"NVIDIA probe failed: {type(error).__name__}: {error}"[:300])
    try:
        pack = load_pack(app_root)
        if gpu.driver_version < pack.driver_version:
            raise AcceleratorUnavailable(f"driver supports CUDA {gpu.driver_version // 1000}.{gpu.driver_version % 1000 // 10}; "
                                         f"{pack.driver_version // 1000}.{pack.driver_version % 1000 // 10} or newer is required")
        if gpu.compute_capability < pack.min_compute_capability:
            raise AcceleratorUnavailable("compute capability {}.{} is below {}.{}".format(*gpu.compute_capability, *pack.min_compute_capability))
        if gpu.total_memory < pack.min_total_memory:
            raise AcceleratorUnavailable(f"{gpu.total_memory // 1024 ** 2} MB of GPU memory is below {pack.min_total_memory // 1024 ** 2} MB")
        bin_dir = ensure(pack, model_root, progress=progress)
        try:
            activate(pack, bin_dir)
        except PackMismatch:
            # Restore changed libraries from freshly verified wheels, once.
            bin_dir = ensure(pack, model_root, progress=progress, verify=True)
            activate(pack, bin_dir)
        supported = compute_types()
        compute = next((item for item in COMPUTE_PREFERENCE if item in supported), None)
        if compute is None:
            raise AcceleratorUnavailable("CTranslate2 supports no compute type on this GPU")
        return AsrDevice("cuda", compute, gpu)
    except AcceleratorUnavailable as error:
        return AsrDevice("cpu", "int8", gpu, error.reason)
    except ModelBootstrapError as error:
        return AsrDevice("cpu", "int8", gpu, f"GPU libraries unavailable: {error.code}: {error.detail}"[:300])
    except Exception as error:  # noqa: BLE001 - the CPU path is always available
        return AsrDevice("cpu", "int8", gpu, f"GPU setup failed: {type(error).__name__}: {error}"[:300])


__all__ = ["AcceleratorUnavailable", "AsrDevice", "CPU", "NvidiaDevice", "Pack", "PackMismatch", "activate", "ensure_pack",
           "load_pack", "probe", "select_asr_device"]
