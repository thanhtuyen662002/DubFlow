"""Load the verified ONNX Runtime CUDA pack in an isolated native process (ADR-0039).

Standard library only: VieNeu's and Spleeter's native processes load this file by
path, before anything imports onnxruntime.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

GPU_RUNTIME_VERSION = "1.30.0"
_PACK_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}(?:/[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}){0,4}")
# Verified accelerator files stay open with read-only sharing for this
# process's life: nothing can change them after their hash (ADR-0039).
_HELD: list = []


def _open_verified(path: Path, size: int, digest: str):
    """Open read-shared (a reparse point as itself) and check the pinned bytes."""
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN.
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x00200000 | 0x08000000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ValueError(f"accelerator file {path.name} cannot be opened for verification")
    stream = open(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")
    hasher, total = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        total += len(block)
        if total > size:
            break
        hasher.update(block)
    if total != size or hasher.hexdigest() != digest:
        stream.close()
        raise ValueError(f"accelerator file {path.name} no longer matches its pinned hash")
    return stream


def activate(spec: dict):
    """Import ONNX Runtime GPU from the verified pack, and place every session on CUDA.

    Runs before anything imports onnxruntime, so the pinned SDK's own
    ``InferenceSession(..., providers=["CPUExecutionProvider"])`` calls get the
    CUDA build. Its sources stay byte-identical; only the provider list changes.
    """
    if os.name != "nt" or "onnxruntime" in sys.modules:
        raise ValueError("ONNX Runtime GPU must be the first ONNX Runtime this process loads")
    manifest, root = Path(spec["manifest"]), Path(spec["root"])
    payload = manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest() != spec["manifest_sha256"]:
        raise ValueError("accelerator manifest differs from the copy the worker verified")
    document = json.loads(payload)
    bin_dir, site = root / "bin", root / "site"
    entries = [(bin_dir, item) for item in document["libraries"]] + [(site, item) for item in document["site"]]
    for folder, item in entries:
        name = item["name"]
        if type(name) is not str or not _PACK_NAME.fullmatch(name) or ".." in name.split("/"):
            raise ValueError("invalid accelerator file name")
        _HELD.append(_open_verified(folder / name, item["size_bytes"], item["sha256"]))
    import ctypes
    for folder in (bin_dir, site / "onnxruntime" / "capi"):
        _HELD.append(os.add_dll_directory(str(folder)))
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    for item in document["libraries"]:
        _HELD.append(ctypes.WinDLL(str(bin_dir / item["name"])))
    sys.path.insert(0, str(site))
    import onnxruntime
    if (Path(onnxruntime.__file__).resolve() != (site / "onnxruntime" / "__init__.py").resolve()
            or onnxruntime.__version__ != GPU_RUNTIME_VERSION
            or "CUDAExecutionProvider" not in onnxruntime.get_available_providers()):
        raise ValueError("the loaded ONNX Runtime is not the pinned CUDA build")
    base = onnxruntime.InferenceSession

    class CudaSession(base):
        def __init__(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
            super().__init__(path_or_bytes, sess_options, providers=[("CUDAExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"], **kwargs)
            if self.get_providers()[0] != "CUDAExecutionProvider":
                raise RuntimeError("ONNX Runtime did not place the model on the GPU")

    onnxruntime.InferenceSession = CudaSession
